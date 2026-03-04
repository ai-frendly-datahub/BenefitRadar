from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Tuple

import feedparser
import requests

from .models import Article, Source


def collect_sources(
    sources: List[Source],
    *,
    category: str,
    limit_per_source: int = 30,
    timeout: int = 15,
) -> Tuple[List[Article], List[str]]:
    """Fetch items from all configured sources, returning articles and errors."""
    articles: List[Article] = []
    errors: List[str] = []

    for source in sources:
        try:
            articles.extend(_collect_single(source, category=category, limit=limit_per_source, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - surface errors to the caller
            errors.append(f"{source.name}: {exc}")

    return articles, errors


def _collect_single(
    source: Source,
    *,
    category: str,
    limit: int,
    timeout: int,
) -> List[Article]:
    if source.type.lower() != "rss":
        raise ValueError(f"Unsupported source type '{source.type}'. Only 'rss' is supported in the template.")

    response = requests.get(source.url, timeout=timeout)
    response.raise_for_status()

    feed = feedparser.parse(response.content)
    items: List[Article] = []

    for entry in feed.entries[:limit]:
        published = _extract_datetime(entry)
        summary = entry.get("summary", "") or entry.get("description", "") or ""

        items.append(
            Article(
                title=(entry.get("title") or "").strip() or "(no title)",
                link=(entry.get("link") or "").strip(),
                summary=summary.strip(),
                published=published,
                source=source.name,
                category=category,
            )
        )

    return items


def _extract_datetime(entry: dict) -> datetime | None:
    """Parse a feed entry date into a timezone-aware datetime."""
    if entry.get("published_parsed"):
        return datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
    if entry.get("updated_parsed"):
        return datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=timezone.utc)

    for key in ("published", "updated", "date"):
        raw = entry.get(key)
        if raw:
            try:
                dt = parsedate_to_datetime(str(raw))
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
    return None
