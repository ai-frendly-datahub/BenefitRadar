from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from html import escape
from pathlib import Path
from typing import Any, cast

from radar_core.ontology import build_summary_ontology_metadata
from radar_core.report_utils import (
    generate_index_html as _core_generate_index_html,
)
from radar_core.report_utils import (
    generate_report as _core_generate_report,
)

from .models import Article, CategoryConfig


def generate_report(
    *,
    category: CategoryConfig,
    articles: Iterable[Article],
    output_path: Path,
    stats: dict[str, int],
    errors: list[str] | None = None,
    store: object | None = None,
    quality_report: Mapping[str, Any] | None = None,
) -> Path:
    """Generate HTML report (delegates to radar-core)."""
    articles_list = list(articles)
    plugin_charts: list[dict[str, Any]] = []
    ontology_metadata = (
        build_summary_ontology_metadata(
            "BenefitRadar",
            category_name=category.category_name,
            search_from=Path(__file__).resolve(),
        )
        or {}
    )

    # --- Universal plugins (entity heatmap + source reliability) ---
    try:
        from radar_core.plugins.entity_heatmap import get_chart_config as _heatmap_config

        _heatmap = _heatmap_config(articles=articles_list)
        if _heatmap is not None:
            plugin_charts.append(_heatmap)
    except Exception:
        pass
    try:
        from radar_core.plugins.source_reliability import get_chart_config as _reliability_config

        _reliability = _reliability_config(store=store)
        if _reliability is not None:
            plugin_charts.append(_reliability)
    except Exception:
        pass

    result = _core_generate_report(
        category=category,
        articles=articles_list,
        output_path=output_path,
        stats=stats,
        errors=errors,
        plugin_charts=cast(Any, plugin_charts if plugin_charts else None),
        ontology_metadata=ontology_metadata,
    )
    _patch_latest_summary_metadata(result, category.category_name, ontology_metadata)
    if quality_report:
        _inject_benefit_quality_panel(result, quality_report)
        _inject_latest_dated_report_panel(result, category.category_name, quality_report)
    return result


def generate_index_html(
    report_dir: Path,
    summaries_dir: Path | None = None,
) -> Path:
    """Generate index.html (delegates to radar-core)."""
    radar_name = "Benefit Radar"
    return _core_generate_index_html(report_dir, radar_name)


def _inject_latest_dated_report_panel(
    output_path: Path,
    category_name: str,
    quality_report: Mapping[str, Any],
) -> None:
    dated_reports = sorted(
        output_path.parent.glob(f"{category_name}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].html"),
        key=lambda path: path.stat().st_mtime,
    )
    if dated_reports:
        _inject_benefit_quality_panel(dated_reports[-1], quality_report)


def _patch_latest_summary_metadata(
    output_path: Path,
    category_name: str,
    ontology_metadata: Mapping[str, Any],
) -> None:
    """Keep dated summary JSON ontology metadata stable across radar-core versions."""
    summary_files = sorted(
        output_path.parent.glob(
            f"{category_name}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_summary.json"
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not summary_files:
        return

    summary_path = summary_files[-1]
    try:
        payload_raw = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload_raw, dict):
        return

    payload = cast(dict[str, Any], payload_raw)
    payload.setdefault("repo", str(ontology_metadata.get("repo") or "BenefitRadar"))
    payload.setdefault(
        "ontology_version",
        str(ontology_metadata.get("ontology_version") or "0.1.0"),
    )
    event_models = ontology_metadata.get("event_models")
    if isinstance(event_models, list) and event_models:
        payload.setdefault("event_models", event_models)
    else:
        payload.setdefault(
            "event_models",
            [
                "govsupport.support_program_notice",
                "govsupport.application_deadline",
                "govsupport.eligibility_rule",
                "govsupport.selection_result",
            ],
        )
    payload.setdefault("ontology_metadata", dict(ontology_metadata))

    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _inject_benefit_quality_panel(
    output_path: Path,
    quality_report: Mapping[str, Any],
) -> None:
    if not output_path.exists():
        return
    html = output_path.read_text(encoding="utf-8")
    if 'id="benefit-quality"' in html:
        return

    marker = '<section id="entities"'
    if marker not in html:
        return

    panel = _render_benefit_quality_panel(quality_report)
    rendered = html.replace(marker, panel.rstrip() + "\n      " + marker, 1)
    rendered = "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"
    output_path.write_text(rendered, encoding="utf-8")


def _render_benefit_quality_panel(quality_report: Mapping[str, Any]) -> str:
    summary = quality_report.get("summary")
    summary_map = summary if isinstance(summary, Mapping) else {}
    sources = [row for row in _list(quality_report.get("sources")) if isinstance(row, Mapping)]
    events = [row for row in _list(quality_report.get("events")) if isinstance(row, Mapping)]
    flagged_sources = [
        row
        for row in sources
        if str(row.get("status")) in {"stale", "missing", "missing_event", "unknown_event_date"}
    ][:6]
    chips = [
        ("fresh", summary_map.get("fresh_sources", 0)),
        ("stale", summary_map.get("stale_sources", 0)),
        ("missing", summary_map.get("missing_sources", 0)),
        ("missing event", summary_map.get("missing_event_sources", 0)),
        ("notice events", summary_map.get("support_program_notice_events", 0)),
        ("deadline events", summary_map.get("application_deadline_events", 0)),
        ("eligibility events", summary_map.get("eligibility_rule_events", 0)),
        ("selection events", summary_map.get("selection_result_events", 0)),
        ("program keys", summary_map.get("unique_program_key_count", 0)),
    ]
    chip_html = "\n".join(
        f'<span class="chip"><strong>{escape(label)}</strong> {escape(str(value))}</span>'
        for label, value in chips
    )
    note = escape(str(quality_report.get("operational_depth_note") or ""))
    return f"""
      <section id="benefit-quality" class="section" aria-label="Benefit quality">
        <div class="section-hd">
          <h2>Benefit Quality</h2>
          <div class="right">
            <span class="kbd">benefit_quality.json</span>
            <span class="kbd">deadline + eligibility</span>
          </div>
        </div>
        <article class="panel">
          <header class="panel-hd">
            <div>
              <p class="panel-title">Application Readiness Checks</p>
              <p class="panel-sub">deadline, eligibility, and selection-result coverage</p>
            </div>
          </header>
          <div class="panel-bd">
            <div class="row" aria-label="Benefit quality summary">
              {chip_html}
            </div>
            <p class="muted small">{note}</p>
            {_render_quality_sources(flagged_sources)}
            {_render_quality_events(events[:6])}
          </div>
        </article>
      </section>
"""


def _render_quality_sources(flagged_sources: list[Mapping[str, Any]]) -> str:
    if not flagged_sources:
        return '<p class="muted small">No stale or missing tracked sources in this run.</p>'

    items = []
    for row in flagged_sources:
        source = escape(str(row.get("source", "")))
        status = escape(str(row.get("status", "")))
        model = escape(str(row.get("event_model", "")))
        age = row.get("age_days")
        age_text = "" if age is None else f", age {escape(str(age))}d"
        items.append(f"<li><strong>{source}</strong>: {status} ({model}{age_text})</li>")
    return "<ul>" + "\n".join(items) + "</ul>"


def _render_quality_events(events: list[Mapping[str, Any]]) -> str:
    if not events:
        return '<p class="muted small">No deadline or eligibility events were extracted.</p>'

    items = []
    for event in events:
        source = escape(str(event.get("source", "")))
        model = escape(str(event.get("event_model", "")))
        title = escape(str(event.get("title", "")))
        deadline = str(event.get("application_deadline") or "")
        result_detail = _format_selection_result(event)
        program_id = str(event.get("program_id") or "")
        program_key = str(event.get("program_key") or "")
        amount_text = ", ".join(str(item) for item in _list(event.get("benefit_amounts")))
        channel_text = ", ".join(str(item) for item in _list(event.get("application_channels")))
        fields = event.get("eligibility_fields")
        field_text = _format_eligibility_fields(fields if isinstance(fields, Mapping) else {})
        detail = escape(
            deadline
            or result_detail
            or amount_text
            or channel_text
            or program_id
            or program_key
            or field_text
            or "event date unavailable"
        )
        items.append(f"<li><strong>{source}</strong>: {model} - {title} ({detail})</li>")
    return "<ul>" + "\n".join(items) + "</ul>"


def _format_selection_result(event: Mapping[str, Any]) -> str:
    parts: list[str] = []
    result_date = str(event.get("selection_result_date") or "")
    selected_count = str(event.get("selected_count") or "")
    execution_amount = str(event.get("execution_amount") or "")
    if result_date:
        parts.append(result_date)
    if selected_count:
        parts.append(f"selected {selected_count}")
    if execution_amount:
        parts.append(f"execution {execution_amount}")
    return ", ".join(parts)


def _format_eligibility_fields(fields: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, values in fields.items():
        if isinstance(values, list):
            value_text = ", ".join(str(value) for value in values[:3])
        else:
            value_text = str(values)
        if value_text:
            parts.append(f"{key}: {value_text}")
    return "; ".join(parts[:3])


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
