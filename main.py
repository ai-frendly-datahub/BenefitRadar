from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from benefitradar.analyzer import apply_entity_rules
from benefitradar.collector import collect_sources
from benefitradar.common.validators import validate_article
from benefitradar.config_loader import load_category_config, load_settings
from benefitradar.raw_logger import RawLogger
from benefitradar.reporter import generate_report
from benefitradar.search_index import SearchIndex
from benefitradar.storage import RadarStorage


def run(
    *,
    category: str,
    config_path: Path | None = None,
    categories_dir: Path | None = None,
    per_source_limit: int = 30,
    recent_days: int = 7,
    timeout: int = 15,
    keep_days: int = 90,
) -> Path:
    """Execute the lightweight collect -> analyze -> report pipeline."""
    settings = load_settings(config_path)
    category_cfg = load_category_config(category, categories_dir=categories_dir)

    print(f"[Radar] Collecting '{category_cfg.display_name}' from {len(category_cfg.sources)} sources...")
    collected, errors = collect_sources(
        category_cfg.sources,
        category=category_cfg.category_name,
        limit_per_source=per_source_limit,
        timeout=timeout,
    )

    raw_logger = RawLogger(settings.raw_data_dir)
    for source in category_cfg.sources:
        source_articles = [article for article in collected if article.source == source.name]
        if source_articles:
            _ = raw_logger.log(source_articles, source_name=source.name)

    analyzed = apply_entity_rules(collected, category_cfg.entities)

    # Validate articles for data quality
    validated_articles = []
    validation_errors = []
    for article in analyzed:
        is_valid, validation_msgs = validate_article(article)
        if is_valid:
            validated_articles.append(article)
        else:
            validation_errors.append(f"{article.link}: {', '.join(validation_msgs)}")

    storage = RadarStorage(settings.database_path)
    storage.upsert_articles(validated_articles)
    _ = storage.delete_older_than(keep_days)

    with SearchIndex(settings.search_db_path) as search_idx:
        for article in validated_articles:
            search_idx.upsert(article.link, article.title, article.summary)

    recent_articles = storage.recent_articles(category_cfg.category_name, days=recent_days)
    storage.close()

    stats = {
        "sources": len(category_cfg.sources),
        "collected": len(collected),
        "matched": sum(1 for a in collected if a.matched_entities),
        "window_days": recent_days,
    }

    output_path = settings.report_dir / f"{category_cfg.category_name}_report.html"
    _ = generate_report(
        category=category_cfg,
        articles=recent_articles,
        output_path=output_path,
        stats=stats,
        errors=errors + validation_errors,
    )
    print(f"[Radar] Report generated at {output_path}")
    if errors or validation_errors:
        print(f"[Radar] {len(errors) + len(validation_errors)} issue(s) found. See report for details.")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lightweight Radar template runner")
    _ = parser.add_argument("--category", required=True, help="Category name matching a YAML in config/categories/")
    _ = parser.add_argument("--config", type=Path, default=None, help="Path to config/config.yaml (optional)")
    _ = parser.add_argument("--categories-dir", type=Path, default=None, help="Custom directory for category YAML files")
    _ = parser.add_argument("--per-source-limit", type=int, default=30, help="Max items to pull from each source")
    _ = parser.add_argument("--recent-days", type=int, default=7, help="Window (days) to show in the report")
    _ = parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout per request (seconds)")
    _ = parser.add_argument("--keep-days", type=int, default=90, help="Retention window for stored items")
    return parser.parse_args()


def _to_path(value: object) -> Path | None:
    if isinstance(value, Path):
        return value
    return None


def _to_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


if __name__ == "__main__":
    args = cast(dict[str, object], vars(parse_args()))
    _ = run(
        category=str(args.get("category", "")),
        config_path=_to_path(args.get("config")),
        categories_dir=_to_path(args.get("categories_dir")),
        per_source_limit=_to_int(args.get("per_source_limit"), 30),
        recent_days=_to_int(args.get("recent_days"), 7),
        timeout=_to_int(args.get("timeout"), 15),
        keep_days=_to_int(args.get("keep_days"), 90),
    )
