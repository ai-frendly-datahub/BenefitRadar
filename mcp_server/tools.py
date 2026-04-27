from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import duckdb

from benefitradar.config_loader import load_category_config, load_category_quality_config
from benefitradar.models import Article
from benefitradar.nl_query import parse_query
from benefitradar.quality_report import build_quality_report
from benefitradar.search_index import SearchIndex


_ALLOWED_SQL = re.compile(r"^\s*(SELECT|WITH|EXPLAIN)\b", re.IGNORECASE)


def _has_table(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchone()
    return bool(row and row[0])


def _format_rows(columns: list[str], rows: list[tuple[object, ...]]) -> str:
    if not rows:
        return "No rows returned."
    text_rows = [tuple("" if value is None else str(value) for value in row) for row in rows]
    widths = [len(name) for name in columns]
    for row in text_rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    header = " | ".join(col.ljust(widths[idx]) for idx, col in enumerate(columns))
    divider = "-+-".join("-" * widths[idx] for idx in range(len(columns)))
    body = [
        " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)) for row in text_rows
    ]
    return "\n".join([header, divider, *body])


def _filter_links_by_days(*, db_path: Path, links: list[str], days: int) -> set[str]:
    if not links:
        return set()
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    placeholders = ", ".join("?" for _ in links)
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        cursor = conn.execute(
            f"""
            SELECT link
            FROM articles
            WHERE collected_at >= ? AND link IN ({placeholders})
            """,
            [cutoff, *links],
        )
        rows = cast(list[tuple[str]], cursor.fetchall())
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def handle_search(*, search_db_path: Path, db_path: Path, query: str, limit: int = 20) -> str:
    parsed = parse_query(query)
    effective_limit = parsed.limit if parsed.limit is not None else limit
    if effective_limit <= 0:
        return "No results found."
    search_text = parsed.search_text or query.strip()
    if not search_text:
        return "No results found."

    with SearchIndex(search_db_path) as idx:
        results = idx.search(search_text, limit=effective_limit)

    if parsed.days is not None:
        allowed_links = _filter_links_by_days(
            db_path=db_path,
            links=[result.link for result in results],
            days=parsed.days,
        )
        results = [result for result in results if result.link in allowed_links]

    if not results:
        return "No results found."

    lines = [f"Found {len(results)} result(s):"]
    for result in results:
        lines.append(f"- {result.title}")
        lines.append(f"  Link: {result.link}")
        lines.append(f"  Snippet: {result.snippet}")
    return "\n".join(lines)


def handle_recent_updates(*, db_path: Path, days: int = 7, limit: int = 20) -> str:
    if limit <= 0:
        return "No recent updates found."

    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        cursor = conn.execute(
            """
            SELECT title, source, link, collected_at
            FROM articles
            WHERE collected_at >= ?
            ORDER BY collected_at DESC
            LIMIT ?
            """,
            [cutoff, limit],
        )
        rows = cast(list[tuple[str, str, str, datetime]], cursor.fetchall())
    finally:
        conn.close()

    if not rows:
        return "No recent updates found."

    lines = [f"Recent updates ({len(rows)}):"]
    for row in rows:
        title, source, link, collected_at = row
        lines.append(f"- {title} | {source} | {collected_at} | {link}")
    return "\n".join(lines)


def handle_sql(*, db_path: Path, query: str) -> str:
    if not _ALLOWED_SQL.match(query):
        return "Error: Only SELECT/WITH/EXPLAIN queries are allowed."

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        cursor = conn.execute(query)
        rows = cursor.fetchall()
        description = cursor.description
        columns = [str(desc[0]) for desc in description] if description else ["result"]
        return _format_rows(columns, rows)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"
    finally:
        conn.close()


def handle_top_trends(*, db_path: Path, days: int = 7, limit: int = 10) -> str:
    if limit <= 0:
        return "No trend data available."

    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT entities_json
            FROM articles
            WHERE collected_at >= ?
            """,
            [cutoff],
        ).fetchall()
        entity_rows = cast(list[tuple[str | None]], rows)
    finally:
        conn.close()

    counts: Counter[str] = Counter()
    for row in entity_rows:
        raw_entities = row[0]
        if not raw_entities:
            continue
        try:
            data = cast(dict[str, object], json.loads(str(raw_entities)))
        except json.JSONDecodeError:
            continue
        for entity_name, matched in data.items():
            if not isinstance(matched, list):
                continue
            matched_items = cast(list[object], matched)
            counts[entity_name] += len(matched_items)

    if not counts:
        return "No trend data available."

    lines = ["Top trends:"]
    for entity_name, count in counts.most_common(limit):
        lines.append(f"- {entity_name}: {count}")
    return "\n".join(lines)


def handle_quality_report(
    *,
    db_path: Path,
    category: str = "benefit",
    categories_dir: Path | None = None,
    days: int = 30,
    limit: int = 500,
) -> str:
    try:
        category_config = load_category_config(category, categories_dir=categories_dir)
        quality_config = load_category_quality_config(category, categories_dir=categories_dir)
        report = build_quality_report(
            category=category_config,
            articles=_load_recent_quality_articles(
                db_path=db_path,
                category=category_config.category_name,
                days=days,
                limit=limit,
            ),
            quality_config=quality_config,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


def _load_recent_quality_articles(
    *,
    db_path: Path,
    category: str,
    days: int,
    limit: int,
) -> list[Article]:
    if not db_path.exists() or limit <= 0:
        return []

    cutoff = datetime.now(tz=UTC) - timedelta(days=max(days, 1))
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        if not _has_table(conn, "articles"):
            return []
        rows = conn.execute(
            """
            SELECT title, link, summary, source, category, published, collected_at, entities_json
            FROM articles
            WHERE category = ? AND COALESCE(published, collected_at) >= ?
            ORDER BY COALESCE(published, collected_at) DESC
            LIMIT ?
            """,
            [category, cutoff, limit],
        ).fetchall()
    finally:
        conn.close()

    articles: list[Article] = []
    for title, link, summary, source, category_value, published, collected_at, raw_entities in rows:
        entities: dict[str, list[str]] = {}
        if raw_entities:
            try:
                parsed = json.loads(str(raw_entities))
                if isinstance(parsed, dict):
                    entities = {
                        str(key): [str(value) for value in values]
                        for key, values in parsed.items()
                        if isinstance(values, list)
                    }
            except json.JSONDecodeError:
                entities = {}
        articles.append(
            Article(
                title=str(title),
                link=str(link),
                summary=str(summary or ""),
                published=published if isinstance(published, datetime) else None,
                source=str(source),
                category=str(category_value),
                matched_entities=entities,
                collected_at=collected_at if isinstance(collected_at, datetime) else None,
            )
        )
    return articles


def handle_price_watch(*, threshold: float = 0.0) -> str:
    _ = threshold
    return "Not available in template project"
