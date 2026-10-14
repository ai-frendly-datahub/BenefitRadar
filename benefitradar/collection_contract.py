"""Collection contract shared with Radar-Template.

BenefitRadar fetches RSS in ``benefitradar.collector``. This module pins that
client: identifying User-Agent, TLS on, challenge pages dropped, missing
secrets recorded by name only, and a hard failure when enabled sources
collect nothing.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benefitradar.common.validators import is_challenge_or_error_page
from benefitradar.models import Article, Source

BENEFITRADAR_USER_AGENT = (
    "BenefitRadar/0.1 (+https://github.com/ai-frendly-datahub/BenefitRadar; public-rss)"
)
RSS_TIMEOUT_SECONDS = 45
_ENV_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CONTRACT_APPLIED = False


class EmptyCollectionError(RuntimeError):
    """Enabled sources produced no articles."""


def apply_feed_client_contract() -> None:
    """Force the BenefitRadar HTTP client onto the feed contract."""
    global _CONTRACT_APPLIED
    import benefitradar.collector as collector

    collector._DEFAULT_HEADERS["User-Agent"] = BENEFITRADAR_USER_AGENT
    collector._DEFAULT_HEADERS["Accept"] = (
        "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"
    )
    if _CONTRACT_APPLIED:
        return

    original_create = collector._create_session
    original_fetch = collector._fetch_url_with_retry

    def _create_session() -> Any:
        session: Any = original_create()
        session.verify = True
        session.headers["User-Agent"] = BENEFITRADAR_USER_AGENT
        return session

    def _fetch_url_with_retry(
        url: str,
        timeout: int,
        headers: dict[str, str] | None = None,
        session: Any = None,
        **kwargs: Any,
    ) -> Any:
        merged = dict(headers or {})
        merged["User-Agent"] = BENEFITRADAR_USER_AGENT
        if session is not None:
            session.verify = True
            raw_get = session.get

            def _get_with_tls(*args: Any, **get_kwargs: Any) -> Any:
                get_kwargs["verify"] = True
                get_headers = get_kwargs.get("headers")
                if isinstance(get_headers, dict):
                    get_headers["User-Agent"] = BENEFITRADAR_USER_AGENT
                    get_kwargs["headers"] = get_headers
                return raw_get(*args, **get_kwargs)

            session.get = _get_with_tls
        return original_fetch(url, timeout, headers=merged, session=session, **kwargs)

    collector_any: Any = collector
    collector_any._create_session = _create_session
    collector_any._fetch_url_with_retry = _fetch_url_with_retry
    _CONTRACT_APPLIED = True


def _env_var_names(value: object) -> list[str]:
    raw_names: list[str] = []
    if isinstance(value, str):
        raw_names = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raw_names = [str(item) for item in value]
    names: list[str] = []
    for item in raw_names:
        candidate = item.strip()
        if not candidate:
            continue
        if _ENV_VAR_NAME.fullmatch(candidate) is None:
            raise ValueError("required_env must be an environment variable name")
        names.append(candidate)
    return names


def missing_required_env(source: Source) -> list[str]:
    """Return required env var names that are unset. Values are never returned."""
    configured = source.config.get("required_env", source.config.get("required_env_vars"))
    missing: list[str] = []
    for name in _env_var_names(configured):
        if not os.environ.get(name, "").strip():
            missing.append(name)
    return missing


def emit_missing_secret_warning(source_name: str, env_names: str | Sequence[str]) -> None:
    """Warn once that a source was skipped. Prints env var names, never values."""
    names = _env_var_names(env_names if isinstance(env_names, str) else list(env_names))
    if not names:
        raise ValueError("required_env must be an environment variable name")
    message = f"{source_name} skipped; required env {', '.join(names)}"
    print(f"::warning title=BenefitRadar missing secret::{message}", flush=True)


def log_skip(*, raw_dir: Path, source_name: str, required_env: str | Sequence[str]) -> Path:
    """Write ``data/raw/<date>/<source>.skip.json`` with env names only."""
    from datetime import UTC, datetime

    if isinstance(required_env, str):
        env_names = [required_env]
    else:
        env_names = [str(name) for name in required_env]
    cleaned = _env_var_names(env_names)
    if not cleaned:
        raise ValueError("required_env must be an environment variable name")

    now = datetime.now(UTC)
    date_dir = raw_dir / now.date().isoformat()
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_source_name = source_name.replace("/", "_").replace("\\", "_")
    output_path = date_dir / f"{safe_source_name}.skip.json"
    payload = {
        "record_type": "skip",
        "source_id": source_name,
        "status": "skipped_missing_env",
        "required_env": cleaned if len(cleaned) > 1 else cleaned[0],
        "skip_reason": ", ".join(f"{name} not set" for name in cleaned),
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_path


def partition_missing_secrets(
    sources: list[Source],
    *,
    raw_dir: Path,
) -> tuple[list[Source], list[str]]:
    """Drop sources whose required env names are unset. Disabled sources stay."""
    runnable: list[Source] = []
    errors: list[str] = []
    for source in sources:
        if not source.enabled:
            runnable.append(source)
            continue
        try:
            missing = missing_required_env(source)
        except ValueError:
            errors.append(f"{source.name}: required_env must be an environment variable name")
            continue
        if not missing:
            runnable.append(source)
            continue
        emit_missing_secret_warning(source.name, missing)
        log_skip(raw_dir=raw_dir, source_name=source.name, required_env=missing)
        errors.append(f"{source.name}: skipped_missing_env {', '.join(missing)}")
    return runnable, errors


def drop_challenge_pages(articles: list[Article]) -> tuple[list[Article], list[str]]:
    """Remove Cloudflare, cookie-wall, and HTTP error documents before storage."""
    kept: list[Article] = []
    dropped_sources: dict[str, int] = {}
    kept_sources: set[str] = set()
    for article in articles:
        if is_challenge_or_error_page(article.title, article.summary):
            dropped_sources[article.source] = dropped_sources.get(article.source, 0) + 1
            continue
        kept.append(article)
        kept_sources.add(article.source)
    errors: list[str] = []
    for source_name, count in dropped_sources.items():
        if source_name not in kept_sources:
            errors.append(
                f"{source_name}: dropped {count} challenge, Cloudflare, or cookie-wall page(s)"
            )
    return kept, errors


def ensure_collection_not_empty(*, collected_count: int, enabled_source_count: int) -> None:
    """Fail the run when enabled sources produced nothing.

    A dated snapshot of an old database is not evidence that this run collected data.
    """
    if enabled_source_count > 0 and collected_count <= 0:
        print(
            "::error title=BenefitRadar empty collection::Collection produced no articles",
            flush=True,
        )
        raise EmptyCollectionError("Collection produced no articles from enabled sources")
