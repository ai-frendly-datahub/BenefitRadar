from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests
from pybreaker import CircuitBreakerError

from benefitradar import collector
from benefitradar.collector import (
    _collect_browser_pass,
    _collect_reddit_pass,
    _collect_single,
    _create_session,
    _entry_text,
    _extract_datetime,
    _fetch_url_with_retry,
    _parse_retry_after,
    _source_bool,
    collect_sources,
)
from benefitradar.exceptions import ParseError, SourceError
from benefitradar.models import Article, Source

pytestmark = pytest.mark.unit


class _FakeHealthStore:
    def __init__(self, disabled: bool = False) -> None:
        self.disabled = disabled
        self.failures: list[tuple[str, str, float]] = []
        self.successes: list[tuple[str, float]] = []
        self.closed = False

    def is_disabled(self, source_name: str) -> bool:
        _ = source_name
        return self.disabled

    def record_failure(self, source_name: str, error: str, delay: float) -> None:
        self.failures.append((source_name, error, delay))

    def record_success(self, source_name: str, delay: float) -> None:
        self.successes.append((source_name, delay))

    def close(self) -> None:
        self.closed = True


class _FakeThrottler:
    def __init__(self) -> None:
        self.acquired: list[str] = []
        self.failures: list[tuple[str, int | str | None]] = []
        self.successes: list[str] = []

    def acquire(self, source_name: str) -> None:
        self.acquired.append(source_name)

    def record_failure(self, source_name: str, *, retry_after: int | str | None = None) -> None:
        self.failures.append((source_name, retry_after))

    def record_success(self, source_name: str) -> None:
        self.successes.append(source_name)

    def get_current_delay(self, source_name: str) -> float:
        _ = source_name
        return 1.25


def test_fetch_url_records_retry_after_failure_and_success() -> None:
    error_response = Mock()
    error_response.status_code = 429
    error_response.headers = {"Retry-After": "12"}
    http_error = requests.exceptions.HTTPError("rate limited", response=error_response)
    response_429 = Mock()
    response_429.raise_for_status.side_effect = http_error
    response_ok = Mock()
    response_ok.raise_for_status = Mock()
    session = Mock()
    session.get.side_effect = [response_429, response_ok]
    throttler = _FakeThrottler()
    health_store = _FakeHealthStore()

    response = _fetch_url_with_retry(
        "https://example.com/feed",
        timeout=5,
        session=session,
        source_name="source",
        throttler=throttler,
        health_store=health_store,
        max_attempts=2,
    )

    assert response is response_ok
    assert throttler.acquired == ["source", "source"]
    assert throttler.failures == [("source", 12)]
    assert throttler.successes == ["source"]
    assert health_store.failures == [("source", "rate limited", 1.25)]
    assert health_store.successes == [("source", 1.25)]


def test_retry_after_source_bool_and_session_helpers() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("   ") is None
    assert _parse_retry_after("15") == 15
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") == ("Wed, 21 Oct 2026 07:28:00 GMT")

    assert _source_bool(Source(name="a", type="rss", url="x", config={"flag": True}), "flag")
    assert _source_bool(Source(name="a", type="rss", url="x", config={"flag": " YES "}), "flag")
    assert not _source_bool(Source(name="a", type="rss", url="x", config={"flag": "off"}), "flag")
    assert not _source_bool(Source(name="a", type="rss", url="x", config={"flag": 1}), "flag")

    session = _create_session()
    try:
        assert session.headers["User-Agent"].startswith("Mozilla/5.0")
        assert "https://" in session.adapters
    finally:
        session.close()


def test_collect_browser_and_reddit_pass_delegate_to_radar_core(monkeypatch) -> None:
    browser_calls: list[dict[str, object]] = []
    reddit_calls: list[dict[str, object]] = []
    source = Source(name="board", type="browser", url="https://example.com")

    def fake_browser_sources(**kwargs: object) -> tuple[list[Article], list[str]]:
        browser_calls.append(kwargs)
        return [], ["browser-warning"]

    def fake_reddit_sources(**kwargs: object) -> tuple[list[Article], list[str]]:
        reddit_calls.append(kwargs)
        return [], ["reddit-warning"]

    monkeypatch.setitem(
        sys.modules,
        "radar_core.browser_collector",
        SimpleNamespace(collect_browser_sources=fake_browser_sources),
    )
    monkeypatch.setitem(
        sys.modules,
        "radar_core.reddit_collector",
        SimpleNamespace(collect_reddit_sources=fake_reddit_sources),
    )

    assert _collect_browser_pass(
        [source], category="benefit", timeout=3, health_db_path="h.db"
    ) == (
        [],
        ["browser-warning"],
    )
    assert _collect_reddit_pass(
        [source],
        category="benefit",
        limit_per_source=4,
        timeout=5,
        health_db_path="h.db",
    ) == ([], ["reddit-warning"])
    assert browser_calls[0]["timeout"] == 3000
    assert reddit_calls[0]["limit"] == 4


def test_collect_sources_disabled_circuit_and_optional_collector_import_errors() -> None:
    disabled = Source(name="disabled", type="rss", url="https://disabled.example/feed")
    breaker_open = Source(name="breaker", type="rss", url="https://breaker.example/feed")
    browser = Source(name="browser", type="browser", url="https://example.com/board")
    reddit = Source(name="reddit", type="reddit", url="https://reddit.com/r/test")

    health_store = _FakeHealthStore(disabled=True)
    manager = Mock()
    manager.get_breaker.return_value.call.side_effect = CircuitBreakerError("open")

    with (
        patch("radar.collector.CrawlHealthStore", return_value=health_store),
        patch("radar.collector.get_circuit_breaker_manager", return_value=manager),
        patch("radar.collector._collect_browser_pass", side_effect=ImportError),
        patch("radar.collector._collect_reddit_pass", side_effect=ImportError),
        patch("radar.collector._create_session") as mock_create_session,
    ):
        articles, errors = collect_sources(
            [disabled, breaker_open, browser, reddit],
            category="benefit",
            min_interval_per_host=0.0,
            max_workers=1,
        )

    assert articles == []
    assert any("Source disabled" in error for error in errors)
    assert any("Browser collection unavailable" in error for error in errors)
    assert any("Reddit collection unavailable" in error for error in errors)
    assert health_store.closed is True
    mock_create_session.assert_not_called()

    health_store.disabled = False
    with (
        patch("radar.collector.CrawlHealthStore", return_value=health_store),
        patch("radar.collector.get_circuit_breaker_manager", return_value=manager),
    ):
        _, breaker_errors = collect_sources(
            [breaker_open],
            category="benefit",
            min_interval_per_host=0.0,
            max_workers=1,
        )
    assert breaker_errors == ["breaker: Circuit breaker open (source unavailable)"]


def test_collect_single_api_unsupported_and_parse_error(monkeypatch) -> None:
    api_source = Source(name="api", type="api", url="https://api.example")
    unsupported = Source(name="bad", type="csv", url="https://example.com/file.csv")
    rss = Source(name="rss", type="rss", url="https://example.com/feed")
    article = Article(
        title="api article",
        link="https://example.com/api",
        summary="summary",
        published=None,
        source="api",
        category="benefit",
    )

    monkeypatch.setattr(collector, "collect_bokjiro", lambda *args, **kwargs: [article])
    assert _collect_single(api_source, category="benefit", limit=1, timeout=3) == [article]

    with pytest.raises(SourceError, match="Unsupported source type"):
        _collect_single(unsupported, category="benefit", limit=1, timeout=3)

    response = Mock()
    response.content = b"not used"
    monkeypatch.setattr(collector, "_fetch_url_with_retry", lambda *args, **kwargs: response)
    monkeypatch.setattr(collector.feedparser, "parse", Mock(side_effect=RuntimeError("bad xml")))
    with pytest.raises(ParseError, match="Failed to parse feed"):
        _collect_single(rss, category="benefit", limit=1, timeout=3)


def test_collect_single_rss_content_summary_skip_and_date_fallbacks(monkeypatch) -> None:
    rss = Source(name="rss", type="rss", url="https://example.com/feed")
    response = Mock()
    response.content = b"not used"
    parsed_feed = SimpleNamespace(
        entries=[
            {
                "title": "Content article",
                "link": "https://example.com/content",
                "content": [{"value": "Content summary"}],
                "updated": "Thu, 21 May 2026 12:30:00",
            },
            {"title": "No link", "summary": "skip"},
            {"link": "https://example.com/no-title", "summary": "skip"},
        ]
    )
    monkeypatch.setattr(collector, "_fetch_url_with_retry", lambda *args, **kwargs: response)
    monkeypatch.setattr(collector.feedparser, "parse", Mock(return_value=parsed_feed))

    articles = _collect_single(rss, category="benefit", limit=10, timeout=3)

    assert len(articles) == 1
    assert articles[0].summary == "Content summary"
    assert articles[0].published is not None
    assert articles[0].published.tzinfo is not None

    parsed_time = time.strptime("2026-05-21 12:00:00", "%Y-%m-%d %H:%M:%S")
    assert _extract_datetime({"published_parsed": parsed_time}) == datetime(
        2026, 5, 21, 12, 0, tzinfo=UTC
    )
    assert _extract_datetime({"updated_parsed": parsed_time}) == datetime(
        2026, 5, 21, 12, 0, tzinfo=UTC
    )
    assert _extract_datetime({"date": "bad date"}) is None
    assert _entry_text({"value": 3}, "value") == ""
