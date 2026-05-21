"""
Data validation utilities for Radar articles.

Provides functions for:
- Title normalization (whitespace, special characters)
- URL similarity detection
- Article validation
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from benefitradar.models import Article

_TITLE_CLEANUP_THRESHOLD = 300
_TITLE_MAX_CHARS = 220
_SUMMARY_MAX_CHARS = 8000
_TITLE_ACTION_TERMS = ("지원", "신청", "모집", "공고", "사업", "급여", "수당", "바우처", "장려금")
_TITLE_AUDIENCE_TERMS = ("청년", "노인", "장애인", "자영업자", "아동", "가족", "소상공인")
_TITLE_ORGANIZATION_SUFFIXES = (
    "센터",
    "복지관",
    "재단",
    "공단",
    "협회",
    "위원회",
    "부",
    "청",
)
_TITLE_NOISE_TERMS = {
    "모집일정",
    "모집일정 :",
    "지원대상",
    "지원대상 :",
    "좋아요",
    "알림",
    "전체",
}


def normalize_title(title: str) -> str:
    """
    Normalize article title by removing extra whitespace and special characters.

    Args:
        title: Raw article title

    Returns:
        Normalized title (lowercase, no extra spaces, minimal special chars)

    Examples:
        >>> normalize_title("  Breaking News  ")
        "breaking news"
        >>> normalize_title("Title (Updated)")
        "title updated"
    """
    if not title:
        return ""

    normalized = title.lower()

    normalized = re.sub(r"\s+", " ", normalized).strip()

    normalized = re.sub(r"[^\w\s\-]", "", normalized)

    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def clean_article_text_fields(article: Article) -> Article:
    """Normalize noisy browser-collected title/summary text in-place."""
    article.title = clean_display_title(article.title)
    article.summary = clean_summary_text(article.summary)
    return article


def clean_display_title(title: str) -> str:
    stripped = title.strip()
    if not stripped:
        return ""

    lines = [
        _collapse_whitespace(line) for line in stripped.splitlines() if _collapse_whitespace(line)
    ]
    has_browser_noise = len(lines) > 1 and any(_is_noisy_title_line(line) for line in lines)
    if (
        len(stripped) <= _TITLE_CLEANUP_THRESHOLD
        and stripped.count("\n") < 4
        and not has_browser_noise
    ):
        return _collapse_whitespace(stripped)

    best_line = _select_best_title_line(stripped)
    if best_line:
        return _truncate_text(best_line, _TITLE_MAX_CHARS)
    return _truncate_text(_collapse_whitespace(stripped), _TITLE_MAX_CHARS)


def clean_summary_text(summary: str) -> str:
    collapsed = _collapse_whitespace(summary)
    return _truncate_text(collapsed, _SUMMARY_MAX_CHARS)


def _select_best_title_line(text: str) -> str:
    candidates = [
        _collapse_whitespace(line) for line in text.splitlines() if _collapse_whitespace(line)
    ]
    scored: list[tuple[int, int, str]] = []
    for index, line in enumerate(candidates):
        if _is_noisy_title_line(line):
            continue
        score = 0
        lowered = line.lower()
        has_action = any(term in lowered for term in _TITLE_ACTION_TERMS)
        if has_action:
            score += 8
        if any(term in lowered for term in _TITLE_AUDIENCE_TERMS):
            score += 5
        if "복지" in lowered:
            score += 2
        if any(term in lowered for term in ("benefit", "grant", "subsidy")):
            score += 8
        if line.startswith("[") and "]" in line:
            score += 4
        if 8 <= len(line) <= _TITLE_MAX_CHARS:
            score += 5
        if len(line) > _TITLE_MAX_CHARS:
            score -= 4
        if re.search(r"20\d{2}", line):
            score += 1
        if not has_action and line.endswith(_TITLE_ORGANIZATION_SUFFIXES):
            score -= 8
        score -= min(index, 6)
        scored.append((score, min(len(line), _TITLE_MAX_CHARS), line))
    if not scored:
        return ""
    return max(scored)[2]


def _is_noisy_title_line(line: str) -> bool:
    stripped = line.strip()
    label = stripped.removesuffix(":").strip()
    if stripped in _TITLE_NOISE_TERMS or label in _TITLE_NOISE_TERMS:
        return True
    if stripped.startswith("#"):
        return True
    if re.fullmatch(r"D\s*-\s*\d+", stripped, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"20\d{2}[.-]\d{1,2}[.-]\d{1,2}\s*~\s*20\d{2}[.-]\d{1,2}[.-]\d{1,2}", stripped):
        return True
    if len(stripped) <= 2:
        return True
    return False


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def validate_url_format(url: str) -> bool:
    """
    Validate if URL has valid format.

    Args:
        url: URL to validate

    Returns:
        True if URL is valid, False otherwise

    Examples:
        >>> validate_url_format("https://example.com/article")
        True
        >>> validate_url_format("not-a-url")
        False
        >>> validate_url_format("")
        False
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url)
        # Must have scheme and netloc
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def is_similar_url(url1: str, url2: str, threshold: float = 0.8) -> bool:
    """
    Check if two URLs are similar (same domain and similar path).

    Args:
        url1: First URL
        url2: Second URL
        threshold: Similarity threshold (0.0-1.0)

    Returns:
        True if URLs are similar, False otherwise

    Examples:
        >>> is_similar_url(
        ...     "https://example.com/article/123",
        ...     "https://example.com/article/123?ref=abc"
        ... )
        True
        >>> is_similar_url(
        ...     "https://example.com/article/123",
        ...     "https://other.com/article/123"
        ... )
        False
    """
    try:
        parsed1 = urlparse(url1)
        parsed2 = urlparse(url2)

        if parsed1.netloc != parsed2.netloc:
            return False

        path1 = parsed1.path
        path2 = parsed2.path

        if path1 == path2:
            return True

        ratio = SequenceMatcher(None, path1, path2).ratio()
        return ratio >= threshold

    except Exception:
        return False


def detect_duplicate_articles(
    title1: str,
    url1: str,
    title2: str,
    url2: str,
    title_threshold: float = 0.85,
    url_threshold: float = 0.8,
) -> bool:
    """
    Detect if two articles are duplicates based on title and URL similarity.

    Args:
        title1: First article title
        url1: First article URL
        title2: Second article title
        url2: Second article URL
        title_threshold: Title similarity threshold
        url_threshold: URL similarity threshold

    Returns:
        True if articles are likely duplicates, False otherwise

    Examples:
        >>> detect_duplicate_articles(
        ...     "Breaking News",
        ...     "https://example.com/article/123",
        ...     "Breaking News",
        ...     "https://example.com/article/123?ref=abc"
        ... )
        True
    """
    # Normalize titles
    norm_title1 = normalize_title(title1)
    norm_title2 = normalize_title(title2)

    # Check title similarity
    title_ratio = SequenceMatcher(None, norm_title1, norm_title2).ratio()
    if title_ratio < title_threshold:
        return False

    # Check URL similarity
    return is_similar_url(url1, url2, url_threshold)


def validate_article(article: Article) -> tuple[bool, list[str]]:
    """
    Validate an Article object for data quality.

    Args:
        article: Article to validate

    Returns:
        Tuple of (is_valid, error_messages)

    Examples:
        >>> from benefitradar.models import Article
        >>> from datetime import datetime
        >>> article = Article(
        ...     title="Valid Article",
        ...     link="https://example.com/article",
        ...     summary="Summary",
        ...     published=datetime.now(),
        ...     source="Example",
        ...     category="news"
        ... )
        >>> is_valid, errors = validate_article(article)
        >>> is_valid
        True
    """
    errors: list[str] = []

    # Validate title
    if not article.title or not isinstance(article.title, str):
        errors.append("title is missing or not a string")
    elif len(article.title.strip()) == 0:
        errors.append("title is empty")

    # Validate link
    if not article.link or not isinstance(article.link, str):
        errors.append("link is missing or not a string")
    elif not validate_url_format(article.link):
        errors.append(f"link has invalid URL format: {article.link}")

    # Validate summary
    if not article.summary or not isinstance(article.summary, str):
        errors.append("summary is missing or not a string")
    elif len(article.summary.strip()) == 0:
        errors.append("summary is empty")

    # Validate source
    if not article.source or not isinstance(article.source, str):
        errors.append("source is missing or not a string")

    # Validate category
    if not article.category or not isinstance(article.category, str):
        errors.append("category is missing or not a string")

    return len(errors) == 0, errors
