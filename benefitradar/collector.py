from __future__ import annotations

import html
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, List, Tuple

import feedparser
import requests
from pybreaker import CircuitBreakerError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .bokjiro_collector import collect_bokjiro
from .models import Article, Source
from .resilience import get_circuit_breaker_manager


def _fetch_url_with_retry(
    url: str,
    timeout: int,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """Fetch URL with retry logic on transient errors."""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True,
    )
    def _fetch() -> requests.Response:
        response = requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response

    return _fetch()


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
    manager = get_circuit_breaker_manager()

    for source in sources:
        try:
            breaker = manager.get_breaker(source.name)
            articles.extend(
                breaker.call(
                    _collect_single,
                    source,
                    category=category,
                    limit=limit_per_source,
                    timeout=timeout,
                )
            )
        except CircuitBreakerError:
            errors.append(f"{source.name}: Circuit breaker open (source unavailable)")
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
    source_type = source.type.lower()

    if source_type == "api":
        return _collect_api(source, category=category, limit=limit, timeout=timeout)
    if source_type != "rss":
        raise ValueError(
            f"Unsupported source type '{source.type}'. Only 'rss' and 'api' are supported."
        )

    headers = {
        "User-Agent": "BenefitRadar/1.0 (Government Benefits Aggregator; +https://github.com/zzragida/ai-frendly-datahub)",
        "Accept": "application/rss+xml, application/xml, application/atom+xml, text/xml, */*",
    }
    response = _fetch_url_with_retry(source.url, timeout, headers=headers)

    feed = feedparser.parse(response.content)
    items: List[Article] = []

    for entry in feed.entries[:limit]:
        published = _extract_datetime(entry)
        summary = entry.get("summary", "") or entry.get("description", "") or ""
        if not summary:
            _content = entry.get("content", [])
            if _content:
                summary = _content[0].get("value", "")

        items.append(
            Article(
                title=html.unescape((entry.get("title") or "").strip()) or "(no title)",
                link=(entry.get("link") or "").strip(),
                summary=html.unescape(summary.strip()),
                published=published,
                source=source.name,
                category=category,
            )
        )

    return items


def _collect_api(
    source: Source,
    *,
    category: str,
    limit: int,
    timeout: int,
) -> List[Article]:
    """Route API-type sources to their specific collectors."""
    if "bokjiro" in source.url or "보조금" in source.name:
        return collect_bokjiro(source, category=category, limit=limit, timeout=timeout)
    raise ValueError(f"No API collector registered for source '{source.name}' (url: {source.url})")


def _extract_datetime(entry: dict) -> Optional[datetime]:  # type: ignore[type-arg]
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
