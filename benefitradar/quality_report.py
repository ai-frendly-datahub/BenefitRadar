from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Article, CategoryConfig, Source

TRACKED_EVENT_MODEL_ORDER = [
    "support_program_notice",
    "application_deadline",
    "eligibility_rule",
    "selection_result",
]
TRACKED_EVENT_MODELS = set(TRACKED_EVENT_MODEL_ORDER)


def build_quality_report(
    *,
    category: CategoryConfig,
    articles: Iterable[Article],
    errors: Iterable[str] | None = None,
    quality_config: Mapping[str, object] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = _as_utc(generated_at or datetime.now(UTC))
    articles_list = list(articles)
    errors_list = [str(error) for error in (errors or [])]
    quality = _dict(quality_config or {}, "data_quality")
    freshness_sla = _dict(quality, "freshness_sla")
    tracked_event_models = _tracked_event_models(quality)

    event_rows = _build_event_rows(articles_list, category.sources, tracked_event_models)
    source_rows = [
        _build_source_row(
            source=source,
            articles=articles_list,
            event_rows=event_rows,
            errors=errors_list,
            freshness_sla=freshness_sla,
            tracked_event_models=tracked_event_models,
            generated_at=generated,
        )
        for source in category.sources
    ]

    status_counts = Counter(str(row["status"]) for row in source_rows)
    event_counts = Counter(str(row["event_model"]) for row in event_rows)
    program_keys = {
        str(row["program_key"]) for row in event_rows if str(row.get("program_key") or "")
    }
    deadline_with_program_key = sum(
        1
        for row in event_rows
        if row.get("event_model") == "application_deadline" and row.get("program_key")
    )
    summary = {
        "total_sources": len(source_rows),
        "tracked_sources": sum(1 for row in source_rows if row["tracked"]),
        "fresh_sources": status_counts.get("fresh", 0),
        "quiet_sources": status_counts.get("quiet", 0),
        "stale_sources": status_counts.get("stale", 0),
        "missing_sources": status_counts.get("missing", 0),
        "missing_event_sources": status_counts.get("missing_event", 0),
        "unknown_event_date_sources": status_counts.get("unknown_event_date", 0),
        "not_tracked_sources": status_counts.get("not_tracked", 0),
        "skipped_disabled_sources": status_counts.get("skipped_disabled", 0),
        "collection_error_count": len(errors_list),
        "unique_program_key_count": len(program_keys),
        "deadline_with_program_key_events": deadline_with_program_key,
    }
    for event_model in TRACKED_EVENT_MODEL_ORDER:
        summary[f"{event_model}_events"] = event_counts.get(event_model, 0)

    return {
        "category": category.category_name,
        "generated_at": generated.isoformat(),
        "operational_depth_note": (
            "Deadline, eligibility, and selection-result signals are tracked separately; "
            "benefit summaries must not be treated as final application eligibility."
        ),
        "summary": summary,
        "sources": source_rows,
        "events": event_rows,
        "source_backlog": (quality_config or {}).get("source_backlog", {}),
        "errors": errors_list,
    }


def write_quality_report(
    report: Mapping[str, object],
    *,
    output_dir: Path,
    category_name: str,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = _parse_datetime(str(report.get("generated_at") or "")) or datetime.now(UTC)
    date_stamp = _as_utc(generated_at).strftime("%Y%m%d")

    latest_path = output_dir / f"{category_name}_quality.json"
    dated_path = output_dir / f"{category_name}_{date_stamp}_quality.json"
    encoded = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    latest_path.write_text(encoded + "\n", encoding="utf-8")
    dated_path.write_text(encoded + "\n", encoding="utf-8")
    return {"latest": latest_path, "dated": dated_path}


def _build_event_rows(
    articles: list[Article],
    sources: list[Source],
    tracked_event_models: set[str],
) -> list[dict[str, Any]]:
    source_map = {source.name: source for source in sources}
    rows: list[dict[str, Any]] = []
    for article in articles:
        event_models = article.matched_entities.get("OperationalEvent", [])
        if isinstance(event_models, list):
            candidate_models = [str(raw_model) for raw_model in event_models]
        else:
            candidate_models = []

        source = source_map.get(article.source)
        if source is not None and not source.enabled:
            continue
        if source is not None:
            source_event_model = _source_event_model(source)
            if source_event_model == "support_program_notice":
                candidate_models.append(source_event_model)

        if not candidate_models:
            continue

        seen_models: set[str] = set()
        for event_model in candidate_models:
            if event_model in seen_models:
                continue
            seen_models.add(event_model)
            if event_model not in tracked_event_models:
                continue
            event_at = _event_datetime(article, event_model)
            rows.append(
                {
                    "source": article.source,
                    "event_model": event_model,
                    "title": article.title,
                    "url": article.link,
                    "event_at": event_at.isoformat() if event_at else None,
                    "program_id": _first_match(article, "BenefitProgramId"),
                    "program_key": _first_match(article, "BenefitProgramKey"),
                    "application_deadline": _first_match(article, "ApplicationDeadline"),
                    "application_start_date": _first_match(article, "ApplicationStartDate"),
                    "application_channels": _list(
                        article.matched_entities.get("ApplicationChannel")
                    ),
                    "benefit_amounts": _list(article.matched_entities.get("BenefitAmount")),
                    "selection_result_date": _first_match(article, "SelectionResultDate"),
                    "selected_count": _first_match(article, "SelectionSelectedCount"),
                    "execution_amount": _first_match(article, "SelectionExecutionAmount"),
                    "selection_program_title": _first_match(article, "SelectionProgramTitle"),
                    "eligibility_fields": _eligibility_fields(article),
                }
            )
    return rows


def _build_source_row(
    *,
    source: Source,
    articles: list[Article],
    event_rows: list[dict[str, Any]],
    errors: list[str],
    freshness_sla: Mapping[str, object],
    tracked_event_models: set[str],
    generated_at: datetime,
) -> dict[str, Any]:
    source_articles = [article for article in articles if article.source == source.name]
    source_errors = [error for error in errors if error.startswith(f"{source.name}:")]
    event_model = _source_event_model(source)
    source_event_rows = [
        row
        for row in event_rows
        if row["source"] == source.name and row["event_model"] == event_model
    ]
    latest_event = _latest_event(source_event_rows)
    latest_event_at = (
        _parse_datetime(str(latest_event.get("event_at") or "")) if latest_event else None
    )
    sla_days = _source_sla_days(source, event_model, freshness_sla)
    age_days = _age_days(generated_at, latest_event_at) if latest_event_at else None
    tracked = source.enabled and event_model in tracked_event_models
    status = _source_status(
        source=source,
        event_model=event_model,
        tracked_event_models=tracked_event_models,
        article_count=len(source_articles),
        event_count=len(source_event_rows),
        latest_event_at=latest_event_at,
        sla_days=sla_days,
        age_days=age_days,
    )

    return {
        "source": source.name,
        "source_type": source.type,
        "enabled": source.enabled,
        "tracked": tracked,
        "event_model": event_model,
        "freshness_sla_days": sla_days,
        "status": status,
        "article_count": len(source_articles),
        "event_count": len(source_event_rows),
        "latest_event_at": latest_event_at.isoformat() if latest_event_at else None,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "latest_title": str(latest_event.get("title", "")) if latest_event else "",
        "latest_url": str(latest_event.get("url", "")) if latest_event else "",
        "latest_program_id": str(latest_event.get("program_id", "")) if latest_event else "",
        "latest_program_key": str(latest_event.get("program_key", "")) if latest_event else "",
        "latest_application_deadline": (
            str(latest_event.get("application_deadline", "")) if latest_event else ""
        ),
        "latest_application_channels": (
            latest_event.get("application_channels", []) if latest_event else []
        ),
        "latest_benefit_amounts": (latest_event.get("benefit_amounts", []) if latest_event else []),
        "latest_selection_result_date": (
            str(latest_event.get("selection_result_date", "")) if latest_event else ""
        ),
        "latest_selected_count": (
            str(latest_event.get("selected_count", "")) if latest_event else ""
        ),
        "latest_execution_amount": (
            str(latest_event.get("execution_amount", "")) if latest_event else ""
        ),
        "latest_eligibility_fields": (
            latest_event.get("eligibility_fields", {}) if latest_event else {}
        ),
        "skip_reason": source.config.get("skip_reason"),
        "reenable_gate": source.config.get("reenable_gate"),
        "errors": source_errors,
    }


def _source_status(
    *,
    source: Source,
    event_model: str,
    tracked_event_models: set[str],
    article_count: int,
    event_count: int,
    latest_event_at: datetime | None,
    sla_days: int | None,
    age_days: float | None,
) -> str:
    if not source.enabled:
        return "skipped_disabled"
    if event_model not in tracked_event_models:
        return "not_tracked"
    if article_count == 0:
        if bool(source.config.get("quiet_when_no_items")):
            return "quiet"
        return "missing"
    if event_count == 0:
        return "missing_event"
    if latest_event_at is None or age_days is None:
        return "unknown_event_date"
    if sla_days is not None and age_days > sla_days:
        return "stale"
    return "fresh"


def _tracked_event_models(quality: Mapping[str, object]) -> set[str]:
    outputs = _dict(quality, "quality_outputs")
    raw = outputs.get("tracked_event_models")
    if isinstance(raw, list):
        values = {str(item).strip() for item in raw if str(item).strip()}
        return values & TRACKED_EVENT_MODELS or set(TRACKED_EVENT_MODELS)
    return set(TRACKED_EVENT_MODELS)


def _source_event_model(source: Source) -> str:
    raw = source.config.get("event_model")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    source_name = source.name.lower()
    source_url = source.url.lower()
    if source.type.lower() == "api" or "bokjiro" in source_url:
        return "eligibility_rule"
    if any(token in source_name for token in ("복지", "여성가족", "보건복지")):
        return "eligibility_rule"
    if any(token in source_name for token in ("일자리", "고용", "교육", "국토", "중소벤처")):
        return "application_deadline"
    return ""


def _source_sla_days(
    source: Source,
    event_model: str,
    freshness_sla: Mapping[str, object],
) -> int | None:
    raw_source_sla = source.config.get("freshness_sla_days")
    parsed_source_sla = _as_int(raw_source_sla)
    if parsed_source_sla is not None:
        return parsed_source_sla

    model_sla = freshness_sla.get(event_model)
    if isinstance(model_sla, Mapping):
        return _as_int(model_sla.get("max_age_days"))
    return None


def _latest_event(event_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated: list[tuple[datetime, dict[str, Any]]] = []
    undated: list[dict[str, Any]] = []
    for row in event_rows:
        event_at = _parse_datetime(str(row.get("event_at") or ""))
        if event_at is not None:
            dated.append((event_at, row))
        else:
            undated.append(row)
    if dated:
        return max(dated, key=lambda item: item[0])[1]
    return undated[0] if undated else None


def _event_datetime(article: Article, event_model: str) -> datetime | None:
    if event_model == "support_program_notice":
        event_time = article.published or article.collected_at
        return _as_utc(event_time) if event_time is not None else None
    if event_model == "application_deadline":
        deadline = _first_match(article, "ApplicationDeadline")
        if deadline:
            return _parse_datetime(deadline)
    if event_model == "selection_result":
        result_date = _first_match(article, "SelectionResultDate")
        if result_date:
            return _parse_datetime(result_date)
    event_time = article.published or article.collected_at
    return _as_utc(event_time) if event_time is not None else None


def _first_match(article: Article, key: str) -> str:
    values = article.matched_entities.get(key, [])
    if isinstance(values, list) and values:
        return str(values[0])
    return ""


def _list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _eligibility_fields(article: Article) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    prefix = "Eligibility"
    for key, values in article.matched_entities.items():
        if not key.startswith(prefix) or key == "Eligibility":
            continue
        if isinstance(values, list):
            fields[key.removeprefix(prefix)] = [str(value) for value in values]
    return fields


def _age_days(generated_at: datetime, event_at: datetime) -> float:
    return max(0.0, (_as_utc(generated_at) - _as_utc(event_at)).total_seconds() / 86400)


def _dict(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key)
    return value if isinstance(value, Mapping) else {}


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_datetime(value: str) -> datetime | None:
    if not value or value == "None":
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None
