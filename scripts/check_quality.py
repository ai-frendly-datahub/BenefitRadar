#!/usr/bin/env python3
"""Run DuckDB data quality checks."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT.parent / "radar-core"))

from benefitradar.benefit_signals import enrich_benefit_operational_fields  # noqa: E402
from benefitradar.common.quality_checks import run_all_checks  # noqa: E402
from benefitradar.config_loader import (  # noqa: E402
    load_category_config,
    load_category_quality_config,
)
from benefitradar.models import Article  # noqa: E402
from benefitradar.quality_report import build_quality_report, write_quality_report  # noqa: E402
from benefitradar.relevance import (  # noqa: E402
    apply_source_context_entities,
    filter_relevant_articles,
)
from benefitradar.storage import RadarStorage  # noqa: E402


def _project_path(project_root: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else project_root / path


def _load_runtime_config(project_root: Path) -> dict[str, Any]:
    raw = (
        yaml.safe_load((project_root / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    )
    return raw if isinstance(raw, dict) else {}


def _coerce_date(value: object) -> date | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _latest_article_date(db_path: Path, category_name: str) -> date | None:
    if not db_path.exists():
        return None
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            row = con.execute(
                """
                SELECT MAX(COALESCE(published, collected_at))
                FROM articles
                WHERE category = ?
                """,
                [category_name],
            ).fetchone()
    except duckdb.Error:
        return None
    if not row:
        return None
    return _coerce_date(row[0])


def _lookback_days(target_date: date | None, *, minimum_days: int = 7) -> int:
    if target_date is None:
        return minimum_days
    age_days = (datetime.now(UTC).date() - target_date).days + 1
    return max(minimum_days, age_days)


def _dedupe_articles(articles: list[Article]) -> list[Article]:
    deduped: dict[str, Article] = {}
    for article in articles:
        key = article.link or f"{article.source}:{article.title}"
        deduped.setdefault(str(key), article)
    return list(deduped.values())


def _load_recent_quality_errors(
    report_dir: Path,
    *,
    category_name: str,
    today: date | None = None,
) -> list[str]:
    report_path = report_dir / f"{category_name}_quality.json"
    if not report_path.exists():
        return []

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    generated_date = _coerce_date(payload.get("generated_at"))
    if today is None:
        today = datetime.now(UTC).date()
    if generated_date != today:
        return []

    raw_errors = payload.get("errors")
    if not isinstance(raw_errors, list):
        return []
    return [str(error) for error in raw_errors if str(error).strip()]


def generate_quality_artifacts(
    project_root: Path = PROJECT_ROOT,
    *,
    category_name: str = "benefit",
) -> tuple[dict[str, Path], dict[str, Any]]:
    runtime_config = _load_runtime_config(project_root)
    db_path = _project_path(
        project_root,
        str(runtime_config.get("database_path", "data/radar_data.duckdb")),
    )
    report_dir = _project_path(
        project_root,
        str(runtime_config.get("report_dir", "reports")),
    )
    categories_dir = project_root / "config" / "categories"
    category_cfg = load_category_config(category_name, categories_dir=categories_dir)
    quality_cfg = load_category_quality_config(category_name, categories_dir=categories_dir)
    lookback_days = _lookback_days(_latest_article_date(db_path, category_cfg.category_name))

    with RadarStorage(db_path) as storage:
        recent_by_collected_at = cast(
            Callable[..., list[Article]],
            storage.recent_articles_by_collected_at,  # type: ignore[attr-defined]
        )
        articles = _dedupe_articles(
            [
                *storage.recent_articles(
                    category_cfg.category_name, days=lookback_days, limit=1000
                ),
                *recent_by_collected_at(
                    category_cfg.category_name,
                    days=lookback_days,
                    limit=1000,
                ),
            ]
        )

    enriched_articles = enrich_benefit_operational_fields(articles)
    scoped_articles = filter_relevant_articles(
        apply_source_context_entities(enriched_articles, category_cfg.sources),
        category_cfg.sources,
    )
    report = build_quality_report(
        category=category_cfg,
        articles=scoped_articles or articles,
        errors=_load_recent_quality_errors(
            report_dir,
            category_name=category_cfg.category_name,
        ),
        quality_config=quality_cfg,
    )
    paths = write_quality_report(
        report,
        output_dir=report_dir,
        category_name=category_cfg.category_name,
    )
    return paths, report


def main() -> None:
    runtime_config = _load_runtime_config(PROJECT_ROOT)
    db_path = _project_path(
        PROJECT_ROOT,
        str(runtime_config.get("database_path", "data/radar_data.duckdb")),
    )
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    with duckdb.connect(str(db_path), read_only=True) as con:
        run_all_checks(
            con,
            table_name="articles",
            null_conditions={
                "title": "title IS NULL OR title = ''",
                "link": "link IS NULL OR link = ''",
                "summary": "summary IS NULL OR summary = ''",
                "published": "published IS NULL",
            },
            text_columns=["title", "summary"],
            url_column="link",
            date_column="published",
        )

    paths, report = generate_quality_artifacts(PROJECT_ROOT)
    summary = report["summary"]
    print(f"quality_report={paths['latest']}")
    print(f"tracked_sources={summary['tracked_sources']}")
    print(f"fresh_sources={summary['fresh_sources']}")
    print(f"stale_sources={summary['stale_sources']}")
    print(f"missing_sources={summary['missing_sources']}")
    print(f"not_tracked_sources={summary['not_tracked_sources']}")
    print(f"unique_program_key_count={summary['unique_program_key_count']}")


if __name__ == "__main__":
    main()
