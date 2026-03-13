from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from benefitradar.models import Article
from benefitradar.raw_logger import RawLogger


def _make_article(*, title: str = "Sample title", summary: str = "요약") -> Article:
    return Article(
        title=title,
        link="https://example.com/article",
        summary=summary,
        published=datetime(2026, 3, 4, 10, 0, tzinfo=UTC),
        source="RSS Source",
        category="coffee",
        matched_entities={"brands": ["Blue Bottle"]},
    )


def test_log_creates_yyyy_mm_dd_directory_and_jsonl_file(tmp_path: Path) -> None:
    logger = RawLogger(tmp_path)
    article = _make_article()

    output_path = logger.log([article], source_name="MySource")

    assert output_path.parent.name == datetime.now(UTC).date().isoformat()
    assert output_path.name == "MySource.jsonl"
    assert output_path.exists()


def test_log_writes_valid_json_per_line(tmp_path: Path) -> None:
    logger = RawLogger(tmp_path)
    articles = [
        _make_article(title="A", summary="첫 번째"),
        _make_article(title="B", summary="두 번째"),
    ]

    output_path = logger.log(articles, source_name="source")

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    parsed_0 = cast(dict[str, object], json.loads(lines[0]))
    parsed_1 = cast(dict[str, object], json.loads(lines[1]))

    assert parsed_0["title"] == "A"
    assert parsed_1["title"] == "B"
    assert parsed_0["published"] == "2026-03-04T10:00:00+00:00"

    logged_at = parsed_0.get("logged_at")
    assert isinstance(logged_at, str)
    assert logged_at.endswith("+00:00")


def test_log_appends_when_called_multiple_times(tmp_path: Path) -> None:
    logger = RawLogger(tmp_path)

    output_path = logger.log([_make_article(title="first")], source_name="source")
    _ = logger.log([_make_article(title="second")], source_name="source")

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["title"] == "first"
    assert json.loads(lines[1])["title"] == "second"


def test_log_preserves_korean_text_without_ascii_escaping(tmp_path: Path) -> None:
    logger = RawLogger(tmp_path)

    output_path = logger.log([_make_article(summary="한글 테스트")], source_name="source")
    content = output_path.read_text(encoding="utf-8")

    assert "한글 테스트" in content
    assert "\\u" not in content


def test_log_replaces_slashes_in_source_name_for_safe_filename(tmp_path: Path) -> None:
    logger = RawLogger(tmp_path)

    output_path = logger.log([_make_article()], source_name="news/rss\\feed")

    assert output_path.name == "news_rss_feed.jsonl"
    assert output_path.exists()


def test_log_with_empty_articles_creates_empty_file(tmp_path: Path) -> None:
    logger = RawLogger(tmp_path)

    output_path = logger.log([], source_name="empty")

    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == ""
