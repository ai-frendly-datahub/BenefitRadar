from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import requests
import structlog

from .models import Article, Source


logger = structlog.get_logger(__name__)

# Cache configuration
_CACHE_DIR = Path(os.environ.get("BOKJIRO_CACHE_DIR", "data/api_cache"))
_CACHE_TTL_SECONDS = int(os.environ.get("BOKJIRO_CACHE_TTL", "3600"))  # 1 hour default
_API_TIMEOUT = int(os.environ.get("BOKJIRO_API_TIMEOUT", "15"))


def _cache_key(endpoint: str, params: dict[str, str]) -> str:
    """Generate a stable cache key from endpoint and params."""
    # Exclude API key from cache key for security
    safe_params = {k: v for k, v in sorted(params.items()) if k != "serviceKey"}
    raw = f"{endpoint}:{json.dumps(safe_params, sort_keys=True)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_cached_response(cache_file: Path) -> bytes | None:
    """Load cached API response if it exists and is not expired."""
    if not cache_file.exists():
        return None

    try:
        meta_file = cache_file.with_suffix(".meta.json")
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            cached_at = meta.get("cached_at", 0)
            if time.time() - cached_at > _CACHE_TTL_SECONDS:
                logger.debug("cache_expired", cache_file=str(cache_file))
                return None

        return cache_file.read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cache_read_error", error=str(exc))
        return None


def _load_stale_cache(cache_file: Path) -> bytes | None:
    """Load cached response regardless of TTL (for fallback on API failure)."""
    if not cache_file.exists():
        return None

    try:
        return cache_file.read_bytes()
    except OSError as exc:
        logger.warning("stale_cache_read_error", error=str(exc))
        return None


def _save_cache(cache_file: Path, content: bytes) -> None:
    """Save API response to file cache."""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(content)

        meta_file = cache_file.with_suffix(".meta.json")
        meta_file.write_text(json.dumps({"cached_at": time.time()}))
    except OSError as exc:
        logger.warning("cache_write_error", error=str(exc))


def _validate_xml_response(content: bytes) -> bool:
    """Validate that the XML response has expected structure."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return False

    # Check for error response from API
    result_code = root.findtext(".//resultCode") or root.findtext(".//errCd")
    if result_code and result_code != "00" and result_code != "0":
        return False

    # Check for at least one known container element
    has_items = bool(
        root.findall(".//servList") or root.findall(".//item") or root.findall("./body/items/item")
    )
    # Also accept empty but valid responses (no items but valid structure)
    has_body = root.find(".//body") is not None or root.find("body") is not None

    return has_items or has_body


def collect_bokjiro(
    source: Source,
    *,
    category: str,
    limit: int = 30,
    timeout: int | None = None,
) -> list[Article]:
    """Collect government subsidy program data from 보조금24 (bokjiro.go.kr).

    Requires BOKJIRO_API_KEY environment variable.
    Returns empty list gracefully if API key is not set or API is unreachable.

    Features:
    - File-based response caching with configurable TTL
    - Graceful fallback to stale cache on API failure
    - XML response schema validation
    - Configurable timeout via env var or parameter
    """
    api_key = os.environ.get("BOKJIRO_API_KEY", "")
    if not api_key:
        # Return empty list gracefully; not an error, just unconfigured.
        return []

    effective_timeout = timeout if timeout is not None else _API_TIMEOUT

    base_url = "http://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations"
    endpoint = f"{base_url}/LcgvWelfarelist"

    params = {
        "serviceKey": api_key,
        "pageNo": "1",
        "numOfRows": str(min(limit, 100)),
    }

    key = _cache_key(endpoint, params)
    cache_file = _CACHE_DIR / f"bokjiro_{key}.xml"

    # Try to use fresh cache first
    cached = _load_cached_response(cache_file)
    if cached is not None:
        logger.debug("using_cached_response", source=source.name, cache_file=str(cache_file))
        return _parse_bokjiro_xml(cached, source_name=source.name, category=category)

    # Fetch from API with error handling
    try:
        response = requests.get(endpoint, params=params, timeout=effective_timeout)
        response.raise_for_status()
        content = response.content

        # Validate response schema before parsing
        if not _validate_xml_response(content):
            logger.warning(
                "invalid_api_response_schema",
                source=source.name,
                status_code=response.status_code,
            )
            # Fall back to stale cache
            stale = _load_stale_cache(cache_file)
            if stale is not None:
                logger.info("using_stale_cache_after_invalid_response", source=source.name)
                return _parse_bokjiro_xml(stale, source_name=source.name, category=category)
            return []

        # Cache the successful response
        _save_cache(cache_file, content)
        return _parse_bokjiro_xml(content, source_name=source.name, category=category)

    except requests.exceptions.Timeout:
        logger.warning("bokjiro_api_timeout", source=source.name, timeout=effective_timeout)
    except requests.exceptions.ConnectionError as exc:
        logger.warning("bokjiro_api_connection_error", source=source.name, error=str(exc))
    except requests.exceptions.HTTPError as exc:
        logger.warning(
            "bokjiro_api_http_error",
            source=source.name,
            status_code=getattr(exc.response, "status_code", None),
        )
    except requests.exceptions.RequestException as exc:
        logger.warning("bokjiro_api_request_error", source=source.name, error=str(exc))

    # API failed — try stale cache as fallback
    stale = _load_stale_cache(cache_file)
    if stale is not None:
        logger.info(
            "using_stale_cache_fallback",
            source=source.name,
            cache_file=str(cache_file),
        )
        return _parse_bokjiro_xml(stale, source_name=source.name, category=category)

    logger.warning("bokjiro_api_unavailable_no_cache", source=source.name)
    return []


def _parse_bokjiro_xml(
    content: bytes,
    *,
    source_name: str,
    category: str,
) -> list[Article]:
    """Parse 보조금24 XML response into Article objects."""
    articles: list[Article] = []

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return articles

    items = root.findall(".//servList") or root.findall(".//item")
    if not items:
        items = root.findall("./body/items/item")

    for item in items:
        title = _text(item, "servNm") or _text(item, "wlfareInfoNm") or _text(item, "title") or ""
        summary_parts = []

        target = _text(item, "servDgst") or _text(item, "lifeNmArray") or ""
        if target:
            summary_parts.append(target)

        amount = _text(item, "sprtCycNm") or _text(item, "sprtAmt") or ""
        if amount:
            summary_parts.append(f"지원: {amount}")

        dept = _text(item, "jurMnofNm") or _text(item, "charger") or ""
        if dept:
            summary_parts.append(f"담당: {dept}")

        link = _text(item, "servDtlLink") or _text(item, "infoUrl") or ""
        if not link:
            serv_id = _text(item, "servId") or ""
            if serv_id:
                link = f"https://www.bokjiro.go.kr/ssis-teu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={serv_id}"

        summary = " | ".join(summary_parts) if summary_parts else title

        if title.strip():
            articles.append(
                Article(
                    title=title.strip(),
                    link=link.strip() if link else "",
                    summary=summary.strip(),
                    published=datetime.now(UTC),
                    source=source_name,
                    category=category,
                )
            )

    return articles


def _text(element: ElementTree.Element, tag: str) -> str:
    """Safely extract text from an XML element's child tag."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""
