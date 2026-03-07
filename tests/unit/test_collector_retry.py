from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import Mock, patch

import pytest
import requests

from benefitradar import collector as collector_module
from benefitradar.models import Article, Source

RSS_CONTENT = b"""<?xml version="1.0"?>
<rss version="2.0">
    <channel>
        <item>
            <title>Test Article</title>
            <link>http://example.com/article</link>
            <description>Test summary</description>
            <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
        </item>
    </channel>
</rss>"""


def _collect_single_callable() -> Callable[..., list[Article]]:
    return cast(Callable[..., list[Article]], getattr(collector_module, "_collect_single"))


class TestCollectorRetryLogic:
    def test_retry_on_timeout(self) -> None:
        collect_single = _collect_single_callable()
        source = Source(name="test_feed", type="rss", url="http://example.com/feed")
        with patch("benefitradar.collector.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.content = RSS_CONTENT
            mock_response.raise_for_status = Mock()
            mock_get.side_effect = [
                requests.exceptions.Timeout("timeout"),
                requests.exceptions.Timeout("timeout"),
                mock_response,
            ]

            articles = collect_single(source, category="test", limit=10, timeout=15)

            assert len(articles) == 1
            assert isinstance(articles[0], Article)
            assert articles[0].title == "Test Article"
            assert mock_get.call_count == 3

    def test_retry_on_5xx_error(self) -> None:
        collect_single = _collect_single_callable()
        source = Source(name="test_feed", type="rss", url="http://example.com/feed")
        with patch("benefitradar.collector.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.content = RSS_CONTENT
            mock_response.raise_for_status = Mock()

            error_response = Mock()
            error_response.status_code = 503
            error_response.raise_for_status = Mock(
                side_effect=requests.exceptions.HTTPError("503 Service Unavailable")
            )
            mock_get.side_effect = [error_response, error_response, mock_response]

            articles = collect_single(source, category="test", limit=10, timeout=15)

            assert len(articles) == 1
            assert articles[0].title == "Test Article"
            assert mock_get.call_count == 3

    def test_4xx_error_retries_and_raises(self) -> None:
        collect_single = _collect_single_callable()
        source = Source(name="test_feed", type="rss", url="http://example.com/feed")
        with patch("benefitradar.collector.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.HTTPError("404 Not Found")

            with pytest.raises(requests.exceptions.HTTPError):
                _ = collect_single(source, category="test", limit=10, timeout=15)

            assert mock_get.call_count == 3

    def test_max_retries_exceeded(self) -> None:
        collect_single = _collect_single_callable()
        source = Source(name="test_feed", type="rss", url="http://example.com/feed")
        with patch("benefitradar.collector.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout("timeout")

            with pytest.raises(requests.exceptions.Timeout):
                _ = collect_single(source, category="test", limit=10, timeout=15)

            assert mock_get.call_count == 3

    def test_connection_error_retry(self) -> None:
        collect_single = _collect_single_callable()
        source = Source(name="test_feed", type="rss", url="http://example.com/feed")
        with patch("benefitradar.collector.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.content = RSS_CONTENT
            mock_response.raise_for_status = Mock()
            mock_get.side_effect = [
                requests.exceptions.ConnectionError("connection failed"),
                requests.exceptions.ConnectionError("connection failed"),
                mock_response,
            ]

            articles = collect_single(source, category="test", limit=10, timeout=15)

            assert len(articles) == 1
            assert mock_get.call_count == 3
