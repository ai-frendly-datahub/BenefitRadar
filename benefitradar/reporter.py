from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Protocol, cast

import duckdb
from jinja2 import Template

from .models import Article, CategoryConfig


_KOREA_GEOJSON_URLS = (
    "https://raw.githubusercontent.com/southkorea/southkorea-maps/master/kostat/2013/json/skorea_provinces_geo.json",
    "https://raw.githubusercontent.com/southkorea/southkorea-maps/master/kostat/2013/json/skorea_provinces_geo_simple.json",
)

_PROVINCE_GEO_NAMES: dict[str, str] = {
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전라북도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
}

_PROVINCE_ALIASES: dict[str, tuple[str, ...]] = {
    "서울": ("서울", "서울특별시"),
    "부산": ("부산", "부산광역시"),
    "대구": ("대구", "대구광역시"),
    "인천": ("인천", "인천광역시"),
    "광주": ("광주", "광주광역시"),
    "대전": ("대전", "대전광역시"),
    "울산": ("울산", "울산광역시"),
    "세종": ("세종", "세종특별자치시"),
    "경기": ("경기", "경기도"),
    "강원": ("강원", "강원도", "강원특별자치도"),
    "충북": ("충북", "충청북도"),
    "충남": ("충남", "충청남도"),
    "전북": ("전북", "전라북도", "전북특별자치도"),
    "전남": ("전남", "전라남도"),
    "경북": ("경북", "경상북도"),
    "경남": ("경남", "경상남도"),
    "제주": ("제주", "제주도", "제주특별자치도"),
}

_PROVINCE_ORDER: tuple[str, ...] = tuple(_PROVINCE_GEO_NAMES.keys())


class _TemplateRenderer(Protocol):
    def render(self, **context: object) -> str: ...


def generate_report(
    *,
    category: CategoryConfig,
    articles: Iterable[Article],
    output_path: Path,
    stats: dict[str, int],
    database_path: Path | None = None,
    errors: list[str] | None = None,
) -> Path:
    """Render a simple HTML report for the collected articles."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    articles_list = list(articles)
    entity_counts = _count_entities(articles_list)
    effective_database_path = _resolve_database_path(database_path, output_path)
    region_counts = _build_region_counts(
        category_name=category.category_name,
        articles=articles_list,
        database_path=effective_database_path,
        window_days=_resolve_window_days(stats),
    )
    region_rows = _build_region_rows(region_counts)
    korea_map_html = _render_korea_choropleth_map(region_rows)
    korea_region_table_html = _render_region_table(region_rows)

    # Convert Article objects to dicts for JSON serialization (for JavaScript charts)
    articles_json = []
    for article in articles_list:
        article_data = {
            "title": article.title,
            "link": article.link,
            "source": article.source,
            "published": article.published.isoformat() if article.published else None,
            "published_at": article.published.isoformat() if article.published else None,
            "summary": article.summary,
            "matched_entities": article.matched_entities or {},
            "collected_at": article.collected_at.isoformat()
            if hasattr(article, "collected_at") and article.collected_at
            else None,
        }
        articles_json.append(article_data)

    template = cast(_TemplateRenderer, Template(_REPORT_TEMPLATE))
    rendered = template.render(
        category=category,
        articles=articles_list,  # Keep original for template rendering
        articles_json=articles_json,  # JSON-serializable version for charts
        generated_at=datetime.now(timezone.utc),
        stats=stats,
        entity_counts=entity_counts,
        korea_map_html=korea_map_html,
        korea_region_table_html=korea_region_table_html,
        errors=errors or [],
    )
    _ = output_path.write_text(rendered, encoding="utf-8")
    return output_path


def _count_entities(articles: Iterable[Article]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for article in articles:
        for entity_name, keywords in (article.matched_entities or {}).items():
            counter[entity_name] += len(keywords)
    return counter


def _resolve_window_days(stats: dict[str, int]) -> int:
    window_days = stats.get("window_days", 7)
    if isinstance(window_days, bool):
        return 7
    if isinstance(window_days, int):
        return max(window_days, 1)
    return 7


def _resolve_database_path(database_path: Path | None, output_path: Path) -> Path | None:
    if database_path is not None:
        return database_path

    report_root = output_path.parent.parent
    candidates = [
        report_root / "data" / "radar.duckdb",
        report_root / "data" / "radar_data.duckdb",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _build_region_counts(
    *,
    category_name: str,
    articles: Iterable[Article],
    database_path: Path | None,
    window_days: int,
) -> Counter[str]:
    db_counter = _query_region_counts_from_duckdb(
        category_name=category_name,
        database_path=database_path,
        window_days=window_days,
    )
    if db_counter:
        return db_counter
    return _aggregate_region_counts_from_articles(articles)


def _query_region_counts_from_duckdb(
    *, category_name: str, database_path: Path | None, window_days: int
) -> Counter[str]:
    if database_path is None or not database_path.exists():
        return Counter()

    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).replace(tzinfo=None)
    try:
        with duckdb.connect(str(database_path), read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT entities_json
                FROM articles
                WHERE category = ? AND COALESCE(published, collected_at) >= ?
                """,
                [category_name, since],
            ).fetchall()
    except duckdb.Error:
        return Counter()

    counter: Counter[str] = Counter()
    for row in rows:
        if not row:
            continue
        raw_entities = row[0]
        if not isinstance(raw_entities, str) or not raw_entities.strip():
            continue
        try:
            entities_payload = json.loads(raw_entities)
        except json.JSONDecodeError:
            continue

        provinces = _extract_provinces_from_payload(entities_payload)
        for province in provinces:
            counter[province] += 1
    return counter


def _aggregate_region_counts_from_articles(articles: Iterable[Article]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for article in articles:
        provinces = _extract_provinces_from_payload(article.matched_entities or {})
        for province in provinces:
            counter[province] += 1
    return counter


def _extract_provinces_from_payload(payload: object) -> set[str]:
    provinces: set[str] = set()
    for token in _iter_string_values(payload):
        normalized = _normalize_token(token)
        if not normalized:
            continue
        for province, aliases in _PROVINCE_ALIASES.items():
            if any(alias in normalized for alias in aliases):
                provinces.add(province)
                break
    return provinces


def _iter_string_values(payload: object) -> list[str]:
    strings: list[str] = []
    stack: list[object] = [payload]

    while stack:
        current = stack.pop()
        if isinstance(current, str):
            strings.append(current)
            continue
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(key, str):
                    strings.append(key)
                stack.append(value)
            continue
        if isinstance(current, list):
            stack.extend(current)
    return strings


def _normalize_token(token: str) -> str:
    return re.sub(r"[\s\-_/.,()\[\]{}]+", "", token)


def _build_region_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    rows = [
        {
            "province": province,
            "geo_name": _PROVINCE_GEO_NAMES[province],
            "count": int(counter.get(province, 0)),
        }
        for province in _PROVINCE_ORDER
    ]
    rows.sort(key=lambda item: int(item["count"]), reverse=True)
    return rows[:17]


def _load_korea_geojson() -> dict[str, Any]:
    for geojson_url in _KOREA_GEOJSON_URLS:
        try:
            with urllib.request.urlopen(geojson_url, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
                return payload
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ):
            continue
    raise ValueError("Korea GeoJSON is unavailable")


def _render_korea_choropleth_map(region_rows: list[dict[str, Any]]) -> str | None:
    if not region_rows:
        return None

    try:
        import plotly.express as px
    except ImportError:
        return None

    try:
        geojson = _load_korea_geojson()
        figure = px.choropleth_mapbox(
            region_rows,
            geojson=geojson,
            locations="geo_name",
            featureidkey="properties.name",
            color="count",
            color_continuous_scale=["#0e1a30", "#19a7c3", "#33d6c5", "#f6c84c"],
            mapbox_style="carto-positron",
            center={"lat": 36.35, "lon": 127.85},
            zoom=5,
            opacity=0.78,
            hover_name="province",
            hover_data={"count": True, "geo_name": False},
        )
        figure.update_traces(marker_line_width=0.9, marker_line_color="rgba(14, 22, 42, 0.9)")
        figure.update_layout(
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            coloraxis_colorbar={"title": "articles"},
        )
        return figure.to_html(full_html=False, include_plotlyjs="cdn")
    except Exception:
        return None


def _render_region_table(region_rows: list[dict[str, Any]]) -> str:
    max_count = max((int(row["count"]) for row in region_rows), default=0)
    scale = max_count if max_count > 0 else 1

    rows_html: list[str] = [
        '<table class="region-table" aria-label="Regional benefit distribution table">',
        "<thead><tr><th>Region</th><th>Count</th><th>Share</th></tr></thead>",
        "<tbody>",
    ]
    for row in region_rows:
        province = escape(str(row["province"]))
        count = int(row["count"])
        width = int((count / scale) * 100)
        rows_html.append(
            """<tr>
                <td><span class=\"mono\">{province}</span></td>
                <td>{count}</td>
                <td>
                  <div class=\"region-meter\">
                    <span class=\"region-meter-fill\" style=\"width:{width}%\"></span>
                  </div>
                </td>
              </tr>""".format(
                province=province,
                count=count,
                width=width,
            )
        )

    rows_html.append("</tbody></table>")
    return "\n".join(rows_html)


_REPORT_TEMPLATE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="dark" />
    <title>{{ (category.display_name if category else "Radar Report")|e }} - Report</title>

    <link rel="preconnect" href="https://cdn.jsdelivr.net" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />

    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.css" />
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" />

    <style>
      :root{
        --bg0:#05070c;
        --bg1:#070b14;
        --panel0:rgba(10, 16, 30, .72);
        --panel1:rgba(14, 22, 42, .72);
        --panel2:rgba(14, 22, 42, .92);
        --line:rgba(150, 190, 255, .14);
        --line2:rgba(150, 190, 255, .22);
        --shadow:0 18px 60px rgba(0,0,0,.44);
        --text:#e9eefb;
        --muted:rgba(233, 238, 251, .72);
        --faint:rgba(233, 238, 251, .52);
        --brand:#33d6c5;
        --brand2:#19a7c3;
        --accent:#f6c84c;
        --danger:#ff5b6e;

        --radius:16px;
        --radius2:22px;
        --max:1180px;

        --mono:"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        --sans:"Pretendard Variable","Pretendard", ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;

        --focus: 0 0 0 3px rgba(51, 214, 197, .20), 0 0 0 1px rgba(51, 214, 197, .70);
      }

      *{box-sizing:border-box}
      html,body{height:100%}
      body{
        margin:0;
        font-family:var(--sans);
        color:var(--text);
        background:
          radial-gradient(1200px 700px at 18% -8%, rgba(25,167,195,.28), transparent 58%),
          radial-gradient(1100px 600px at 92% 8%, rgba(246,200,76,.18), transparent 60%),
          radial-gradient(900px 520px at 52% 110%, rgba(51,214,197,.18), transparent 62%),
          linear-gradient(180deg, var(--bg0), var(--bg1) 55%, #04060a);
        overflow-x:hidden;
      }

      a{color:inherit; text-decoration:none}
      a:hover{text-decoration:none}
      ::selection{background:rgba(51,214,197,.24)}
      .skip{
        position:absolute;
        left:-999px;
        top:12px;
        padding:10px 12px;
        border-radius:12px;
        background:rgba(14, 22, 42, .98);
        border:1px solid var(--line2);
        color:var(--text);
        z-index:9999;
      }
      .skip:focus{left:12px; box-shadow:var(--focus); outline:none}

      .wrap{max-width:var(--max); margin:0 auto; padding:20px 16px 64px}
      .topbar{
        position:sticky; top:0; z-index:50;
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        background: linear-gradient(180deg, rgba(5,7,12,.92), rgba(5,7,12,.55));
        border-bottom:1px solid rgba(150,190,255,.10);
      }
      .topbar-inner{max-width:var(--max); margin:0 auto; padding:14px 16px; display:flex; align-items:center; justify-content:space-between; gap:14px}
      .brand{
        display:flex; align-items:center; gap:10px;
        min-width:220px;
      }
      .mark{
        width:34px; height:34px; border-radius:12px;
        background:
          radial-gradient(18px 18px at 32% 28%, rgba(255,255,255,.18), transparent 55%),
          linear-gradient(145deg, rgba(51,214,197,.95), rgba(25,167,195,.78) 55%, rgba(246,200,76,.72));
        box-shadow: 0 10px 30px rgba(0,0,0,.45);
        border:1px solid rgba(255,255,255,.10);
      }
      .brand-title{
        line-height:1.05;
        font-weight:750;
        letter-spacing:-.02em;
      }
      .brand-sub{
        display:block;
        font-family:var(--mono);
        font-size:12px;
        color:var(--faint);
        margin-top:4px;
      }
      .nav{
        display:flex; flex-wrap:wrap; gap:8px;
        justify-content:flex-end;
      }
      .pill{
        display:inline-flex; align-items:center; gap:8px;
        padding:9px 12px;
        border-radius:999px;
        border:1px solid rgba(150,190,255,.14);
        background:rgba(10,16,30,.45);
        color:var(--muted);
        font-size:13px;
        transition: transform .18s ease, border-color .18s ease, background .18s ease, color .18s ease;
        user-select:none;
      }
      .pill:hover{
        transform: translateY(-1px);
        border-color: rgba(51,214,197,.34);
        background: rgba(10,16,30,.70);
        color: rgba(233,238,251,.88);
      }
      .pill:focus-visible{outline:none; box-shadow:var(--focus)}

      .hero{
        padding:24px 0 12px;
        display:grid;
        grid-template-columns: 1.25fr .75fr;
        gap:18px;
        align-items:stretch;
      }
      .hero-card{
        border-radius: var(--radius2);
        background: linear-gradient(180deg, var(--panel2), rgba(14,22,42,.58));
        border: 1px solid rgba(150,190,255,.14);
        box-shadow: var(--shadow);
        padding:18px 18px 16px;
        position:relative;
        overflow:hidden;
      }
      .hero-card:before{
        content:"";
        position:absolute;
        inset:-2px;
        background:
          radial-gradient(280px 160px at 12% 18%, rgba(51,214,197,.18), transparent 60%),
          radial-gradient(240px 140px at 82% 22%, rgba(246,200,76,.12), transparent 58%),
          radial-gradient(300px 170px at 46% 110%, rgba(25,167,195,.14), transparent 62%);
        filter: blur(10px);
        opacity:.9;
        pointer-events:none;
      }
      .hero-body{position:relative}
      .kicker{
        display:inline-flex; align-items:center; gap:10px;
        font-family:var(--mono);
        font-size:12px;
        color:rgba(233,238,251,.72);
        letter-spacing:.06em;
        text-transform:uppercase;
      }
      .dot{
        width:8px; height:8px; border-radius:999px;
        background: var(--brand);
        box-shadow:0 0 0 4px rgba(51,214,197,.12);
      }
      h1{
        margin:10px 0 0;
        font-weight:820;
        letter-spacing:-.03em;
        font-size: clamp(28px, 3.2vw, 40px);
        line-height:1.08;
      }
      .lede{
        margin:10px 0 0;
        color: var(--muted);
        font-size:15px;
        line-height:1.55;
        max-width: 72ch;
      }
      .meta{
        margin:14px 0 0;
        display:flex;
        flex-wrap:wrap;
        gap:10px 12px;
        color: rgba(233,238,251,.70);
        font-size:13px;
      }
      .meta span{
        display:inline-flex; align-items:center; gap:8px;
        padding:6px 10px;
        border-radius:999px;
        border:1px solid rgba(150,190,255,.12);
        background: rgba(10,16,30,.40);
      }
      .mono{font-family:var(--mono)}
      .accent{color: rgba(246,200,76,.96)}
      .ok{color: rgba(51,214,197,.92)}
      .bad{color: rgba(255,91,110,.92)}

      .stats{
        border-radius: var(--radius2);
        background: linear-gradient(180deg, rgba(10,16,30,.64), rgba(10,16,30,.36));
        border: 1px solid rgba(150,190,255,.12);
        box-shadow: 0 12px 40px rgba(0,0,0,.34);
        padding:16px;
        display:grid;
        grid-template-columns: 1fr 1fr;
        gap:12px;
      }
      .stat{
        border-radius: 14px;
        background: rgba(14,22,42,.45);
        border: 1px solid rgba(150,190,255,.12);
        padding:12px 12px 10px;
        min-height: 76px;
      }
      .stat .label{
        color: rgba(233,238,251,.66);
        font-size:12px;
        font-family: var(--mono);
        letter-spacing:.03em;
      }
      .stat .value{
        margin-top:8px;
        font-weight:800;
        letter-spacing:-.02em;
        font-size: 20px;
        line-height:1.1;
      }
      .stat .hint{
        margin-top:6px;
        color: rgba(233,238,251,.60);
        font-size:12px;
        line-height:1.35;
      }

      .grid{
        margin-top:14px;
        display:grid;
        grid-template-columns: 1fr 1fr;
        gap:14px;
      }
      .panel{
        border-radius: var(--radius2);
        background: linear-gradient(180deg, var(--panel0), var(--panel1));
        border: 1px solid rgba(150,190,255,.12);
        box-shadow: 0 16px 55px rgba(0,0,0,.38);
        overflow:hidden;
      }
      .panel-hd{
        padding:14px 16px;
        display:flex;
        justify-content:space-between;
        align-items:flex-start;
        gap:12px;
        border-bottom:1px solid rgba(150,190,255,.10);
        background: rgba(10,16,30,.20);
      }
      .panel-title{
        margin:0;
        font-weight:780;
        letter-spacing:-.02em;
        font-size:14px;
      }
      .panel-sub{
        margin:6px 0 0;
        color: rgba(233,238,251,.64);
        font-size:12px;
        line-height:1.35;
        font-family: var(--mono);
      }
      .panel-bd{padding:14px 16px 16px}
      .chart-wrap{position:relative; height: 290px}
      .chart-wrap.tall{height: 320px}
      .panel.span-2{grid-column: span 2}
      .korea-map-wrap{
        min-height: 360px;
        border-radius: 14px;
        border: 1px solid rgba(150,190,255,.12);
        background: rgba(10,16,30,.32);
        overflow: hidden;
      }
      .korea-map-wrap .plotly-graph-div{height:360px !important}
      .region-table-wrap{
        border-radius: 14px;
        border: 1px solid rgba(150,190,255,.12);
        background: rgba(10,16,30,.32);
        overflow-x: auto;
      }
      .region-table{
        width:100%;
        border-collapse: collapse;
        min-width: 420px;
      }
      .region-table th,
      .region-table td{
        padding:10px 12px;
        border-bottom:1px solid rgba(150,190,255,.10);
        text-align:left;
        font-size:13px;
      }
      .region-table th{
        color: rgba(233,238,251,.66);
        font-family: var(--mono);
        letter-spacing: .02em;
      }
      .region-table tr:last-child td{border-bottom:none}
      .region-meter{
        width: 100%;
        min-width: 140px;
        height: 8px;
        border-radius: 999px;
        background: rgba(150,190,255,.12);
        overflow: hidden;
      }
      .region-meter-fill{
        display:block;
        height:100%;
        border-radius: 999px;
        background: linear-gradient(90deg, rgba(25,167,195,.78), rgba(51,214,197,.92));
      }
      canvas{max-width:100%}

      .notice{
        margin-top:14px;
        border-radius: var(--radius2);
        border: 1px solid rgba(255,91,110,.28);
        background: linear-gradient(180deg, rgba(255,91,110,.14), rgba(10,16,30,.56));
        box-shadow: 0 18px 55px rgba(0,0,0,.34);
        padding:14px 16px;
      }
      .notice h2{
        margin:0;
        font-size:14px;
        font-weight:800;
        letter-spacing:-.02em;
      }
      .notice p{
        margin:8px 0 0;
        color: rgba(233,238,251,.74);
        font-size:13px;
        line-height:1.5;
      }
      .notice ul{
        margin:10px 0 0;
        padding-left: 18px;
        color: rgba(233,238,251,.72);
        font-size:13px;
        line-height:1.55;
      }

      .section{
        margin-top:16px;
      }
      .section-hd{
        display:flex; align-items:flex-end; justify-content:space-between; gap:12px;
        margin: 20px 0 10px;
      }
      .section-hd h2{
        margin:0;
        font-size: 16px;
        font-weight:820;
        letter-spacing:-.02em;
      }
      .section-hd .right{
        display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end;
      }
      .muted{color: rgba(233,238,251,.68)}
      .small{font-size:12px}
      .kbd{
        font-family: var(--mono);
        font-size: 12px;
        color: rgba(233,238,251,.72);
        background: rgba(10,16,30,.52);
        border:1px solid rgba(150,190,255,.14);
        padding:6px 10px;
        border-radius:999px;
      }

      .articles{
        display:grid;
        grid-template-columns: 1fr;
        gap:10px;
      }
      .card{
        border-radius: 18px;
        background: rgba(14,22,42,.46);
        border: 1px solid rgba(150,190,255,.12);
        padding: 12px 12px 11px;
        transition: transform .18s ease, border-color .18s ease, background .18s ease;
        display:flex;
        gap:10px;
        align-items:flex-start;
      }
      .card:hover{
        transform: translateY(-1px);
        border-color: rgba(51,214,197,.22);
        background: rgba(14,22,42,.62);
      }
      .card:focus-within{box-shadow: var(--focus)}
      .card .left{flex:1; min-width:0}
      .title{
        margin:0;
        font-weight:780;
        letter-spacing:-.02em;
        font-size: 14px;
        line-height:1.35;
      }
      .title a{
        display:inline-block;
        outline:none;
      }
      .title a:focus-visible{outline:none}
      .desc{
        margin:8px 0 0;
        color: rgba(233,238,251,.68);
        font-size: 13px;
        line-height: 1.55;
      }
      .row{
        margin-top:10px;
        display:flex;
        flex-wrap:wrap;
        gap:8px;
        align-items:center;
        color: rgba(233,238,251,.64);
        font-size: 12px;
      }
      .chip{
        display:inline-flex; align-items:center;
        gap:8px;
        padding:6px 10px;
        border-radius:999px;
        border: 1px solid rgba(150,190,255,.12);
        background: rgba(10,16,30,.38);
        font-family: var(--mono);
        font-size: 12px;
      }
      .chip strong{font-weight:800; color: rgba(233,238,251,.86)}
      .chip.brand{
        border-color: rgba(51,214,197,.18);
        background: rgba(51,214,197,.10);
      }
      .actions{
        display:flex;
        align-items:center;
        justify-content:flex-end;
      }
      .btn{
        display:inline-flex;
        align-items:center;
        justify-content:center;
        gap:10px;
        padding:10px 12px;
        border-radius: 14px;
        border: 1px solid rgba(150,190,255,.14);
        background: rgba(10,16,30,.46);
        color: rgba(233,238,251,.86);
        font-size: 13px;
        font-weight: 700;
        letter-spacing: -.01em;
        transition: transform .18s ease, border-color .18s ease, background .18s ease;
        cursor:pointer;
        user-select:none;
        min-width: 84px;
      }
      .btn:hover{
        transform: translateY(-1px);
        border-color: rgba(246,200,76,.30);
        background: rgba(10,16,30,.68);
      }
      .btn:focus-visible{outline:none; box-shadow: var(--focus)}
      .btn.primary{
        border-color: rgba(51,214,197,.22);
        background: linear-gradient(135deg, rgba(51,214,197,.22), rgba(25,167,195,.12));
      }
      .btn.primary:hover{border-color: rgba(51,214,197,.36)}
      .btn[aria-disabled="true"]{opacity:.6; cursor:not-allowed}

      .footer{
        margin-top:24px;
        padding-top:14px;
        border-top: 1px solid rgba(150,190,255,.10);
        display:flex;
        flex-wrap:wrap;
        gap:10px 14px;
        justify-content:space-between;
        align-items:center;
        color: rgba(233,238,251,.62);
        font-size: 12px;
      }
      .footer a{color: rgba(233,238,251,.74)}
      .footer a:hover{color: rgba(51,214,197,.90)}

      @media (max-width: 980px){
        .hero{grid-template-columns: 1fr; }
        .stats{grid-template-columns: 1fr 1fr}
        .grid{grid-template-columns: 1fr}
        .panel.span-2{grid-column: span 1}
      }
      @media (max-width: 560px){
        .topbar-inner{align-items:flex-start}
        .brand{min-width: 0}
        .nav{justify-content:flex-start}
        .stats{grid-template-columns: 1fr}
        .card{flex-direction:column}
        .actions{justify-content:flex-start}
        .btn{width:100%}
        .chart-wrap{height: 260px}
        .chart-wrap.tall{height: 300px}
        .korea-map-wrap{min-height: 300px}
        .korea-map-wrap .plotly-graph-div{height:300px !important}
      }
      @media (prefers-reduced-motion: reduce){
        *{scroll-behavior:auto !important}
        .pill,.card,.btn{transition:none !important}
        .pill:hover,.card:hover,.btn:hover{transform:none !important}
      }
    </style>
  </head>

  <body>
    <a class="skip" href="#content">Skip to content</a>

    <header class="topbar" role="banner">
      <div class="topbar-inner">
        <div class="brand" aria-label="Report identity">
          <div class="mark" aria-hidden="true"></div>
          <div>
            <div class="brand-title">{{ (category.display_name if category else "Radar Report")|e }}</div>
            <span class="brand-sub">editorial report - charts + feed</span>
          </div>
        </div>

        <nav class="nav" aria-label="Jump links">
          <a class="pill" href="#charts">Charts</a>
          <a class="pill" href="#region-map">Regions</a>
          <a class="pill" href="#entities">Entities</a>
          <a class="pill" href="#articles">Articles</a>
          {% if errors %}
          <a class="pill" href="#errors" aria-label="Jump to errors ({{ errors|length }})">Errors</a>
          {% endif %}
        </nav>
      </div>
    </header>

    <main id="content" class="wrap" role="main">
      <section class="hero" aria-label="Overview">
        <div class="hero-card">
          <div class="hero-body">
            <div class="kicker">
              <span class="dot" aria-hidden="true"></span>
              <span class="mono">RADAR</span>
              <span class="mono muted">/</span>
              <span class="mono accent">{{ (category.display_name if category else "category")|e }}</span>
            </div>

            <h1>{{ (category.display_name if category else "Radar Report")|e }}</h1>
            <p class="lede">
              A fast, reusable HTML report for lightweight Radar projects: entity distribution, article velocity,
              and source mix, alongside a clean reading list.
            </p>

            <div class="meta" aria-label="Key facts">
              <span><span class="mono muted">articles</span><span class="mono ok">{{ articles|length }}</span></span>
              <span><span class="mono muted">entities</span><span class="mono accent">{{ entity_counts|length if entity_counts else 0 }}</span></span>
              <span><span class="mono muted">errors</span><span class="mono {% if errors %}bad{% else %}ok{% endif %}">{{ errors|length if errors else 0 }}</span></span>
              <span title="Generated time"><span class="mono muted">generated</span><span class="mono">{{ generated_at.strftime('%Y-%m-%d %H:%M UTC') }}</span></span>
            </div>
          </div>
        </div>

        <aside class="stats" aria-label="Quick stats">
          <div class="stat">
            <div class="label">ARTICLES</div>
            <div class="value">{{ stats.collected if stats else articles|length }}</div>
            <div class="hint">Total items in this run</div>
          </div>
          <div class="stat">
            <div class="label">UNIQUE ENTITIES</div>
            <div class="value">{{ entity_counts|length if entity_counts else 0 }}</div>
            <div class="hint">Distinct extracted names</div>
          </div>
          <div class="stat">
            <div class="label">TOP ENTITY</div>
            <div class="value">
              {% if entity_counts %}
                {% set top_entity = entity_counts.most_common(1)[0] %}
                {{ top_entity[0]|e }}
              {% else %}
                -
              {% endif %}
            </div>
            <div class="hint">Most frequent across articles</div>
          </div>
          <div class="stat">
            <div class="label">SOURCES</div>
            <div class="value">{{ stats.sources if stats else "-" }}</div>
            <div class="hint">Auto-counted for charts</div>
          </div>
        </aside>
      </section>

      {% if errors %}
      <section id="errors" class="notice" role="alert" aria-label="Errors">
        <h2>Errors detected ({{ errors|length }})</h2>
        <p>Some sources or steps reported errors. The report still renders with partial data.</p>
        <ul>
          {% for err in errors %}
          <li><span class="mono">{{ err|e }}</span></li>
          {% endfor %}
        </ul>
      </section>
      {% endif %}

      <section id="charts" class="section" aria-label="Charts">
        <div class="section-hd">
          <h2>Visuals</h2>
          <div class="right">
            <span class="kbd">Chart.js</span>
            <span class="kbd">Plotly</span>
            <span class="kbd">dark editorial</span>
            <span class="kbd">responsive</span>
          </div>
        </div>

        <div class="grid">
          <article class="panel" aria-label="Entity distribution">
            <header class="panel-hd">
              <div>
                <p class="panel-title">Entity Distribution</p>
                <p class="panel-sub">Top entities by frequency</p>
              </div>
              <div class="pill" aria-hidden="true">bar</div>
            </header>
            <div class="panel-bd">
              <div class="chart-wrap" role="img" aria-label="Bar chart showing entity frequency">
                <canvas id="chartEntities"></canvas>
              </div>
              <noscript>
                <p class="muted small">Charts require JavaScript. Enable JS to see entity distribution.</p>
              </noscript>
            </div>
          </article>

          <article class="panel" aria-label="Article timeline">
            <header class="panel-hd">
              <div>
                <p class="panel-title">Article Timeline</p>
                <p class="panel-sub">Daily volume inferred from article dates</p>
              </div>
              <div class="pill" aria-hidden="true">line</div>
            </header>
            <div class="panel-bd">
              <div class="chart-wrap tall" role="img" aria-label="Line chart showing article volume over time">
                <canvas id="chartTimeline"></canvas>
              </div>
              <noscript>
                <p class="muted small">Charts require JavaScript. Enable JS to see the timeline.</p>
              </noscript>
            </div>
          </article>
        </div>

        <div class="grid" style="margin-top:14px">
          <article id="region-map" class="panel span-2" aria-label="Regional benefit distribution">
            <header class="panel-hd">
              <div>
                <p class="panel-title">Korea Regional Benefit Distribution</p>
                <p class="panel-sub">Province-level counts from DuckDB entities_json</p>
              </div>
              <div class="pill" aria-hidden="true">map</div>
            </header>
            <div class="panel-bd">
              {% if korea_map_html %}
              <div class="korea-map-wrap" role="img" aria-label="Korea choropleth map by province">
                {{ korea_map_html | safe }}
              </div>
              {% else %}
              <p class="muted small" style="margin:0 0 10px">
                GeoJSON or Plotly was unavailable. Showing fallback province table.
              </p>
              <div class="region-table-wrap">
                {{ korea_region_table_html | safe }}
              </div>
              {% endif %}
            </div>
          </article>
        </div>

        <div class="grid" style="margin-top:14px">
          <article class="panel" aria-label="Source distribution">
            <header class="panel-hd">
              <div>
                <p class="panel-title">Source Distribution</p>
                <p class="panel-sub">Share of articles by source</p>
              </div>
              <div class="pill" aria-hidden="true">pie</div>
            </header>
            <div class="panel-bd">
              <div class="chart-wrap" role="img" aria-label="Pie chart showing article share by source">
                <canvas id="chartSources"></canvas>
              </div>
              <noscript>
                <p class="muted small">Charts require JavaScript. Enable JS to see source breakdown.</p>
              </noscript>
            </div>
          </article>

           <article class="panel" aria-label="Notes">
             <header class="panel-hd">
               <div>
                 <p class="panel-title">Reading Notes</p>
                 <p class="panel-sub">Fast scan guidance</p>
               </div>
               <div class="pill" aria-hidden="true">tips</div>
             </header>
             <div class="panel-bd">
               <p class="muted" style="margin:0; line-height:1.6; font-size:13px">
                 Use the entity chart to spot concentration, the timeline to detect bursts, and the source mix to
                 gauge coverage bias. The article list keeps metadata compact for quick triage.
               </p>
               <div style="margin-top:12px; display:flex; flex-wrap:wrap; gap:8px">
                 <span class="chip brand"><strong>Teal</strong> momentum</span>
                 <span class="chip"><strong>Amber</strong> signal</span>
                 <span class="chip"><strong>Mono</strong> data</span>
               </div>
             </div>
           </article>
         </div>

         <div class="grid" style="margin-top:14px">
           <article class="panel" aria-label="Data freshness">
             <header class="panel-hd">
               <div>
                 <p class="panel-title">Data Freshness</p>
                 <p class="panel-sub">Collection lag in hours</p>
               </div>
               <div class="pill" aria-hidden="true">diagnostic</div>
             </header>
             <div class="panel-bd">
               <div class="chart-wrap" role="img" aria-label="Bar chart showing collection lag distribution">
                 <canvas id="chartFreshness"></canvas>
               </div>
               <noscript>
                 <p class="muted small">Charts require JavaScript. Enable JS to see freshness data.</p>
               </noscript>
             </div>
           </article>

           <article class="panel" aria-label="Entity extraction rate">
             <header class="panel-hd">
               <div>
                 <p class="panel-title">Entity Extraction Rate</p>
                 <p class="panel-sub">Articles with matched entities</p>
               </div>
               <div class="pill" aria-hidden="true">diagnostic</div>
             </header>
             <div class="panel-bd">
               <div class="chart-wrap" role="img" aria-label="Doughnut chart showing entity extraction rate">
                 <canvas id="chartEntityRate"></canvas>
               </div>
               <noscript>
                 <p class="muted small">Charts require JavaScript. Enable JS to see extraction rate.</p>
               </noscript>
             </div>
           </article>
         </div>

         <div class="grid" style="margin-top:14px">
           <article class="panel" aria-label="Source health">
             <header class="panel-hd">
               <div>
                 <p class="panel-title">Source Health</p>
                 <p class="panel-sub">Articles per source (sorted)</p>
               </div>
               <div class="pill" aria-hidden="true">diagnostic</div>
             </header>
             <div class="panel-bd">
               <div class="chart-wrap" role="img" aria-label="Horizontal bar chart showing source health">
                 <canvas id="chartSourceHealth"></canvas>
               </div>
               <noscript>
                 <p class="muted small">Charts require JavaScript. Enable JS to see source health.</p>
               </noscript>
             </div>
           </article>
         </div>
       </section>

      <section id="entities" class="section" aria-label="Entity table">
        <div class="section-hd">
          <h2>Entities</h2>
          <div class="right">
            <span class="kbd">top 12 shown in chart</span>
            <span class="kbd">full list in data</span>
          </div>
        </div>

        <article class="panel">
          <header class="panel-hd">
            <div>
              <p class="panel-title">Entity Counts</p>
              <p class="panel-sub">Rendered as a compact list for accessibility</p>
            </div>
            <div class="pill" aria-hidden="true">list</div>
          </header>
          <div class="panel-bd">
            {% if entity_counts %}
              <div class="articles" aria-label="Entity count list">
                {% for name, count in entity_counts.most_common(24) %}
                <div class="card" role="group" aria-label="Entity {{ name|e }} has count {{ count }}">
                  <div class="left">
                    <p class="title"><span class="mono">{{ name|e }}</span></p>
                    <div class="row">
                      <span class="chip brand"><strong>count</strong> {{ count }}</span>
                    </div>
                  </div>
                  <div class="actions">
                    <span class="btn" aria-disabled="true" title="Entity details not available in lightweight template">Details</span>
                  </div>
                </div>
                {% endfor %}
              </div>
            {% else %}
              <p class="muted" style="margin:0; font-size:13px; line-height:1.6">
                No entity counts available for this run.
              </p>
            {% endif %}
          </div>
        </article>
      </section>

      <section id="articles" class="section" aria-label="Articles">
        <div class="section-hd">
          <h2>Articles</h2>
          <div class="right">
            <span class="kbd">cards</span>
            <span class="kbd">source + date</span>
            <span class="kbd">fast scan</span>
          </div>
        </div>

        <article class="panel">
          <header class="panel-hd">
            <div>
              <p class="panel-title">Reading List</p>
              <p class="panel-sub">Click through to the original source</p>
            </div>
            <div class="pill" aria-hidden="true">{{ articles|length }}</div>
          </header>
          <div class="panel-bd">
            {% if articles %}
            <div class="articles" aria-label="Article list">
              {% for a in articles %}
              <div class="card" role="article" aria-label="Article: {{ a.title|e }}">
                <div class="left">
                  <p class="title">
                    <a href="{{ a.link|e }}" target="_blank" rel="noopener noreferrer">{{ a.title|e }}</a>
                  </p>

                  {% if a.summary %}
                  <p class="desc">{{ a.summary[:220]|e }}{% if a.summary|length > 220 %}...{% endif %}</p>
                  {% endif %}

                  <div class="row" aria-label="Article metadata">
                    <span class="chip"><strong>source</strong> {{ a.source|e }}</span>
                    {% if a.published %}
                    <span class="chip"><strong>date</strong> <span class="mono">{{ a.published.date().isoformat() }}</span></span>
                    {% endif %}
                    {% if a.matched_entities %}
                    <span class="chip brand"><strong>entities</strong> {{ a.matched_entities|length }}</span>
                    {% endif %}
                  </div>
                </div>

                <div class="actions">
                  <a class="btn primary" href="{{ a.link|e }}" target="_blank" rel="noopener noreferrer" aria-label="Open article">Open</a>
                </div>
              </div>
              {% endfor %}
            </div>
            {% else %}
            <p class="muted" style="margin:0; font-size:13px; line-height:1.6">
              No articles were collected for this run.
            </p>
            {% endif %}
          </div>
        </article>
      </section>

      <footer class="footer" role="contentinfo">
        <div>
          <span class="mono">category</span>
          <span class="muted">/</span>
          <span>{{ (category.display_name if category else "Radar Report")|e }}</span>
        </div>
        <div>
          <a href="#content" class="mono" aria-label="Back to top">back to top</a>
        </div>
      </footer>

      <script id="articles-data" type="application/json">{{ articles_json|tojson }}</script>
      <script id="entities-data" type="application/json">{{ entity_counts|tojson if entity_counts else '{}' }}</script>

      <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
      <script>
        (function () {
          const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

          function readJson(id, fallback) {
            const el = document.getElementById(id);
            if (!el) return fallback;
            const txt = (el.textContent || "").trim();
            if (!txt) return fallback;
            try { return JSON.parse(txt); } catch (e) { return fallback; }
          }

          const articles = readJson("articles-data", []);
          const entityCountsRaw = readJson("entities-data", {});

          function normalizeEntityPairs(raw) {
            if (!raw) return [];
            if (Array.isArray(raw)) {
              if (raw.length && Array.isArray(raw[0]) && raw[0].length >= 2) {
                return raw.map(p => [String(p[0]), Number(p[1]) || 0]);
              }
              if (raw.length && typeof raw[0] === "object") {
                return raw.map(o => [String(o.name || o.entity || ""), Number(o.count || o.value || 0) || 0]).filter(p => p[0]);
              }
              return [];
            }
            if (typeof raw === "object") {
              return Object.entries(raw).map(([k, v]) => [String(k), Number(v) || 0]);
            }
            return [];
          }

          function getArticleDate(a) {
            const v = a && (a.published_at || a.published || a.date || a.datetime || a.publishedAt || a.publishedAtISO);
            if (!v) return null;

            const s = String(v);
            const direct = new Date(s);
            if (!isNaN(direct.getTime())) return direct;

            const m = s.match(/^(\\d{4})-(\\d{2})-(\\d{2})/);
            if (m) {
              const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
              if (!isNaN(d.getTime())) return d;
            }
            return null;
          }

          function toDayKey(d) {
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, "0");
            const day = String(d.getDate()).padStart(2, "0");
            return y + "-" + m + "-" + day;
          }

          function buildTimeline(items) {
            const map = new Map();
            for (const a of items) {
              const d = getArticleDate(a);
              if (!d) continue;
              const k = toDayKey(d);
              map.set(k, (map.get(k) || 0) + 1);
            }
            const keys = Array.from(map.keys()).sort();
            return { labels: keys, values: keys.map(k => map.get(k) || 0) };
          }

          function buildSources(items) {
            const map = new Map();
            for (const a of items) {
              const s = (a && (a.source || a.source_name || a.publisher || a.feed || a.domain)) ? String(a.source || a.source_name || a.publisher || a.feed || a.domain) : "unknown";
              const key = s.trim() || "unknown";
              map.set(key, (map.get(key) || 0) + 1);
            }
            const pairs = Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
            const top = pairs.slice(0, 10);
            const rest = pairs.slice(10).reduce((acc, p) => acc + p[1], 0);
            const labels = top.map(p => p[0]);
            const values = top.map(p => p[1]);
            if (rest > 0) { labels.push("other"); values.push(rest); }
            return { labels, values };
          }

          function palette(n) {
            const base = [
              "rgba(51,214,197,.86)",  // teal
              "rgba(25,167,195,.78)",  // blue-teal
              "rgba(246,200,76,.86)",  // amber
              "rgba(120,162,255,.78)", // soft blue
              "rgba(255,91,110,.74)",  // red
              "rgba(160,118,255,.70)", // violet (small)
              "rgba(95,222,132,.70)",  // green
              "rgba(255,154,74,.70)",  // orange
              "rgba(226,93,255,.62)",  // magenta
              "rgba(233,238,251,.46)"  // gray
            ];
            const out = [];
            for (let i = 0; i < n; i++) out.push(base[i % base.length]);
            return out;
          }

          function chartDefaults() {
            return {
              color: "rgba(233,238,251,.78)",
              borderColor: "rgba(150,190,255,.14)",
              font: { family: '"Pretendard Variable","Pretendard",system-ui,-apple-system,Segoe UI,sans-serif', size: 12 },
              animation: reducedMotion ? false : { duration: 650, easing: "easeOutQuart" }
            };
          }

          if (!window.Chart) return;

          Chart.defaults.maintainAspectRatio = false;
          Chart.defaults.responsive = true;
          Chart.defaults.plugins.legend.labels.boxWidth = 10;
          Chart.defaults.plugins.legend.labels.boxHeight = 10;
          Chart.defaults.plugins.legend.labels.usePointStyle = true;

          const defaults = chartDefaults();
          Chart.defaults.color = defaults.color;
          Chart.defaults.borderColor = defaults.borderColor;
          Chart.defaults.font = defaults.font;
          Chart.defaults.animation = defaults.animation;

          const entityPairs = normalizeEntityPairs(entityCountsRaw)
            .filter(p => p[0] && Number.isFinite(p[1]))
            .sort((a, b) => b[1] - a[1])
            .slice(0, 12);

          const timeline = buildTimeline(articles);
          const sources = buildSources(articles);

          const entityCanvas = document.getElementById("chartEntities");
          if (entityCanvas && entityPairs.length) {
            const labels = entityPairs.map(p => p[0]);
            const values = entityPairs.map(p => p[1]);
            new Chart(entityCanvas.getContext("2d"), {
              type: "bar",
              data: {
                labels,
                datasets: [{
                  label: "count",
                  data: values,
                  backgroundColor: "rgba(51,214,197,.35)",
                  borderColor: "rgba(51,214,197,.72)",
                  borderWidth: 1.2,
                  borderRadius: 10,
                  maxBarThickness: 44
                }]
              },
              options: {
                plugins: {
                  legend: { display: false },
                  tooltip: {
                    backgroundColor: "rgba(10,16,30,.92)",
                    borderColor: "rgba(51,214,197,.2)",
                    borderWidth: 1,
                    titleColor: "rgba(233,238,251,.92)",
                    bodyColor: "rgba(233,238,251,.84)"
                  }
                },
                scales: {
                  x: {
                    grid: { display: false },
                    ticks: { color: "#eee" }
                  },
                  y: {
                    beginAtZero: true,
                    grid: { color: "rgba(255,255,255,.1)" },
                    ticks: { color: "#eee" }
                  }
                }
              }
            });
          }

          const timelineCanvas = document.getElementById("chartTimeline");
          if (timelineCanvas && timeline.labels.length) {
            new Chart(timelineCanvas.getContext("2d"), {
              type: "line",
              data: {
                labels: timeline.labels,
                datasets: [{
                  label: "articles/day",
                  data: timeline.values,
                  tension: 0.28,
                  fill: true,
                  borderColor: "rgba(246,200,76,.84)",
                  backgroundColor: "rgba(246,200,76,.12)",
                  pointRadius: 2.8,
                  pointHoverRadius: 4.2,
                  pointBackgroundColor: "rgba(246,200,76,.84)",
                  pointBorderColor: "rgba(10,16,30,.92)",
                  pointBorderWidth: 1.2
                }]
              },
              options: {
                plugins: {
                  legend: { display: false },
                  tooltip: {
                    backgroundColor: "rgba(10,16,30,.92)",
                    borderColor: "rgba(51,214,197,.2)",
                    borderWidth: 1
                  }
                },
                scales: {
                  x: {
                    grid: { display: false },
                    ticks: {
                      color: "rgba(233,238,251,.68)",
                      maxRotation: 0,
                      autoSkip: true,
                      maxTicksLimit: 9
                    }
                  },
                  y: {
                    beginAtZero: true,
                    grid: { color: "rgba(255,255,255,.1)" },
                    ticks: { color: "#eee" }
                  }
                }
              }
            });
          }

          const sourcesCanvas = document.getElementById("chartSources");
          if (sourcesCanvas && sources.labels.length) {
            const colors = palette(sources.labels.length);
            new Chart(sourcesCanvas.getContext("2d"), {
              type: "doughnut",
              data: {
                labels: sources.labels,
                datasets: [{
                  label: "articles",
                  data: sources.values,
                  backgroundColor: colors.map(c => c.replace(")", ", .24)").replace("rgba", "rgba")),
                  borderColor: colors.map(c => c.replace(")", ", .80)").replace("rgba", "rgba")),
                  borderWidth: 1.1,
                  hoverOffset: 6
                }]
              },
              options: {
                cutout: "62%",
                plugins: {
                  legend: {
                    position: "bottom",
                    {color: "#eee", padding: 14 }
                  },
                  tooltip: {
                    backgroundColor: "rgba(10,16,30,.92)",
                    borderColor: "rgba(51,214,197,.2)",
                    borderWidth: 1
                  }
                }
              }
            });
          }

           // Chart 1: Data Freshness (collection lag in hours)
           function buildFreshness(items) {
             const lagBuckets = { "0-1h": 0, "1-6h": 0, "6-24h": 0, "1-3d": 0, "3-7d": 0, "7d+": 0 };
             const now = new Date();
             for (const a of items) {
               const pubStr = a && (a.published_at || a.published);
               const collStr = a && a.collected_at;
               if (!pubStr || !collStr) continue;
               const pubDate = new Date(String(pubStr));
               const collDate = new Date(String(collStr));
               if (isNaN(pubDate.getTime()) || isNaN(collDate.getTime())) continue;
               const lagMs = collDate.getTime() - pubDate.getTime();
               const lagHours = lagMs / (1000 * 60 * 60);
               if (lagHours < 1) lagBuckets["0-1h"]++;
               else if (lagHours < 6) lagBuckets["1-6h"]++;
               else if (lagHours < 24) lagBuckets["6-24h"]++;
               else if (lagHours < 72) lagBuckets["1-3d"]++;
               else if (lagHours < 168) lagBuckets["3-7d"]++;
               else lagBuckets["7d+"]++;
             }
             return { labels: Object.keys(lagBuckets), values: Object.values(lagBuckets) };
           }

           const freshnessData = buildFreshness(articles);
           const freshnessCanvas = document.getElementById("chartFreshness");
           if (freshnessCanvas && freshnessData.labels.length) {
             new Chart(freshnessCanvas.getContext("2d"), {
               type: "bar",
               data: {
                 labels: freshnessData.labels,
                 datasets: [{
                   label: "articles",
                   data: freshnessData.values,
                   backgroundColor: "rgba(51,214,197,.2)",
                   borderColor: "#33d6c5",
                   borderWidth: 1.2,
                   borderRadius: 8
                 }]
               },
               options: {
                 plugins: {
                   legend: { display: false },
                   tooltip: {
                     backgroundColor: "rgba(10,16,30,.92)",
                     borderColor: "rgba(51,214,197,.2)",
                     borderWidth: 1
                   }
                 },
                 scales: {
                   x: {
                     grid: { display: false },
                     ticks: { color: "#eee" }
                   },
                   y: {
                     beginAtZero: true,
                     grid: { color: "rgba(255,255,255,.1)" },
                     ticks: { color: "#eee" }
                   }
                 }
               }
             });
           }

           // Chart 2: Entity Extraction Rate (doughnut with center text)
           function buildEntityRate(items) {
             let withEntities = 0, withoutEntities = 0;
             for (const a of items) {
               const ents = a && a.matched_entities;
               if (ents && Object.keys(ents).length > 0) withEntities++;
               else withoutEntities++;
             }
             return { with: withEntities, without: withoutEntities };
           }

           const entityRateData = buildEntityRate(articles);
           const entityRateCanvas = document.getElementById("chartEntityRate");
           if (entityRateCanvas) {
             const total = entityRateData.with + entityRateData.without;
             const pct = total > 0 ? Math.round((entityRateData.with / total) * 100) : 0;
             const plugin = {
               id: "textCenter",
               beforeDatasetsDraw(c) {
                 const { width, height } = c.chartArea;
                 const x = c.chartArea.left + width / 2;
                 const y = c.chartArea.top + height / 2;
                 c.ctx.save();
                 c.ctx.font = "bold 24px sans-serif";
                 c.ctx.fillStyle = "#eee";
                 c.ctx.textAlign = "center";
                 c.ctx.textBaseline = "middle";
                 c.ctx.fillText(pct + "%", x, y);
                 c.ctx.restore();
               }
             };
             new Chart(entityRateCanvas.getContext("2d"), {
               type: "doughnut",
               data: {
                 labels: ["With entities", "Without entities"],
                 datasets: [{
                   data: [entityRateData.with, entityRateData.without],
                   backgroundColor: ["rgba(95,222,132,.35)", "rgba(255,91,110,.35)"],
                   borderColor: ["rgba(95,222,132,.80)", "rgba(255,91,110,.80)"],
                   borderWidth: 1.2
                 }]
               },
               options: {
                 cutout: "62%",
                 plugins: {
                   legend: { position: "bottom", {color: "#eee", padding: 14 } },
                   tooltip: {
                     backgroundColor: "rgba(10,16,30,.92)",
                     borderColor: "rgba(51,214,197,.2)",
                     borderWidth: 1
                   }
                 }
               },
               plugins: [plugin]
             });
           }

           // Chart 3: Source Health (horizontal bar, sorted descending)
           function buildSourceHealth(items) {
             const map = new Map();
             for (const a of items) {
               const s = (a && a.source) ? String(a.source) : "unknown";
               const key = s.trim() || "unknown";
               map.set(key, (map.get(key) || 0) + 1);
             }
             const pairs = Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
             return { labels: pairs.map(p => p[0]), values: pairs.map(p => p[1]) };
           }

           const sourceHealthData = buildSourceHealth(articles);
           const sourceHealthCanvas = document.getElementById("chartSourceHealth");
           if (sourceHealthCanvas && sourceHealthData.labels.length) {
             const colors = palette(sourceHealthData.labels.length);
             new Chart(sourceHealthCanvas.getContext("2d"), {
               type: "bar",
               data: {
                 labels: sourceHealthData.labels,
                 datasets: [{
                   label: "articles",
                   data: sourceHealthData.values,
                   backgroundColor: colors.map(c => c.replace(")", ", .35)").replace("rgba", "rgba")),
                   borderColor: colors.map(c => c.replace(")", ", .80)").replace("rgba", "rgba")),
                   borderWidth: 1.2,
                   borderRadius: 8
                 }]
               },
               options: {
                 indexAxis: "y",
                 plugins: {
                   legend: { display: false },
                   tooltip: {
                     backgroundColor: "rgba(10,16,30,.92)",
                     borderColor: "rgba(51,214,197,.2)",
                     borderWidth: 1
                   }
                 },
                 scales: {
                   x: {
                     beginAtZero: true,
                     grid: { color: "rgba(255,255,255,.1)" },
                     ticks: { color: "#eee" }
                   },
                   y: {
                     grid: { display: false },
                     ticks: { color: "#eee" }
                   }
                 }
               }
             });
           }
        })();
      </script>
    </main>
  </body>
</html>
"""


def generate_index_html(report_dir: Path) -> Path:
    """Generate an index.html that lists all available report files."""
    report_dir.mkdir(parents=True, exist_ok=True)

    html_files = sorted(
        [f for f in report_dir.glob("*.html") if f.name != "index.html"],
        key=lambda p: p.name,
    )

    reports = []
    for html_file in html_files:
        name = html_file.stem
        display_name = name.replace("_report", "").replace("_", " ").title()
        reports.append({"filename": html_file.name, "display_name": display_name})

    template = cast(_TemplateRenderer, Template(_INDEX_TEMPLATE))
    rendered = template.render(
        reports=reports,
        generated_at=datetime.now(timezone.utc),
    )

    index_path = report_dir / "index.html"
    _ = index_path.write_text(rendered, encoding="utf-8")
    return index_path


_INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Radar Reports</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 24px; background: #f6f8fb; color: #0f172a; }
    h1 { margin: 0 0 8px 0; }
    .muted { color: #475569; font-size: 13px; margin-bottom: 24px; }
    .reports { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
    .card { background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); transition: box-shadow 0.2s; }
    .card:hover { box-shadow: 0 4px 6px rgba(0,0,0,0.08); }
    a { color: #0f172a; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .empty { text-align: center; color: #64748b; padding: 48px; }
  </style>
</head>
<body>
  <h1>Radar Reports</h1>
  <div class="muted">Generated at {{ generated_at.isoformat() }} (UTC)</div>

  {% if reports %}
  <div class="reports">
    {% for report in reports %}
    <div class="card">
      <a href="{{ report.filename }}"><strong>{{ report.display_name }}</strong></a>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">No reports available yet.</div>
  {% endif %}
</body>
</html>
"""
