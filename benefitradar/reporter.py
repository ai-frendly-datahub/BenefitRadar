from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import duckdb
from jinja2 import Environment, FileSystemLoader

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


_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _get_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False,
    )


def _copy_static_assets(report_dir: Path) -> None:
    src = _TEMPLATE_DIR / "static"
    dst = report_dir / "static"
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        _ = shutil.copytree(str(src), str(dst))


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

    template = _get_jinja_env().get_template("report.html")
    rendered = template.render(
        category=category,
        articles=articles_list,  # Keep original for template rendering
        articles_json=articles_json,  # JSON-serializable version for charts
        generated_at=datetime.now(UTC),
        stats=stats,
        entity_counts=entity_counts,
        korea_map_html=korea_map_html,
        korea_region_table_html=korea_region_table_html,
        errors=errors or [],
    )
    _ = output_path.write_text(rendered, encoding="utf-8")

    now_ts = datetime.now(UTC)
    date_stamp = now_ts.strftime("%Y%m%d")
    dated_name = f"{category.category_name}_{date_stamp}.html"
    dated_path = output_path.parent / dated_name
    _ = dated_path.write_text(rendered, encoding="utf-8")

    _copy_static_assets(output_path.parent)

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

    since = (datetime.now(UTC) - timedelta(days=window_days)).replace(tzinfo=None)
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
            f"""<tr>
                <td><span class=\"mono\">{province}</span></td>
                <td>{count}</td>
                <td>
                  <div class=\"region-meter\">
                    <span class=\"region-meter-fill\" style=\"width:{width}%\"></span>
                  </div>
                </td>
              </tr>"""
        )

    rows_html.append("</tbody></table>")
    return "\n".join(rows_html)


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

    template = _get_jinja_env().get_template("index.html")
    rendered = template.render(
        reports=reports,
        generated_at=datetime.now(UTC),
    )

    index_path = report_dir / "index.html"
    _ = index_path.write_text(rendered, encoding="utf-8")
    return index_path
