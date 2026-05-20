from __future__ import annotations

import sys
from pathlib import Path

import pytest

import main as radar_main
from benefitradar.models import Article, Source

pytestmark = pytest.mark.unit


def _article(
    *,
    title: str,
    link: str,
    source: str = "정책브리핑",
    matched_entities: dict[str, list[str]] | None = None,
) -> Article:
    return Article(
        title=title,
        link=link,
        summary=title,
        published=None,
        source=source,
        category="benefit",
        matched_entities=matched_entities or {},
    )


class _FakeStorage:
    def __init__(self, published: list[Article], collected: list[Article]) -> None:
        self.published = published
        self.collected = collected

    def recent_articles(self, category: str, *, days: int, limit: int) -> list[Article]:
        assert category == "benefit"
        assert days == 7
        assert limit == 1000
        return self.published

    def recent_articles_by_collected_at(
        self,
        category: str,
        *,
        days: int,
        limit: int,
    ) -> list[Article]:
        assert category == "benefit"
        assert days == 7
        assert limit == 1000
        return self.collected


def test_parse_args_accepts_pipeline_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--category",
            "benefit",
            "--config",
            "config.yaml",
            "--categories-dir",
            "categories",
            "--per-source-limit",
            "5",
            "--recent-days",
            "3",
            "--timeout",
            "9",
            "--keep-days",
            "30",
            "--keep-raw-days",
            "60",
            "--keep-report-days",
            "45",
            "--snapshot-db",
            "--notifications-config",
            "notifications.yaml",
            "--generate-report",
            "--max-sources",
            "2",
            "--exclude-source",
            "A",
            "--exclude-source",
            "B",
        ],
    )

    args = radar_main.parse_args()

    assert args.category == "benefit"
    assert args.config == Path("config.yaml")
    assert args.categories_dir == Path("categories")
    assert args.per_source_limit == 5
    assert args.recent_days == 3
    assert args.timeout == 9
    assert args.keep_days == 30
    assert args.keep_raw_days == 60
    assert args.keep_report_days == 45
    assert args.snapshot_db is True
    assert args.notifications_config == Path("notifications.yaml")
    assert args.generate_report is True
    assert args.max_sources == 2
    assert args.exclude_source == ["A", "B"]


def test_main_argument_conversion_helpers() -> None:
    path = Path("config.yaml")

    assert radar_main._to_path(path) == path
    assert radar_main._to_path("config.yaml") is None
    assert radar_main._to_int(5, 30) == 5
    assert radar_main._to_int("6", 30) == 6
    assert radar_main._to_int("bad", 30) == 30
    assert radar_main._to_int(True, 30) == 30
    assert radar_main._to_int(None, 30) == 30
    assert radar_main._to_optional_int(None) is None
    assert radar_main._to_optional_int(7) == 7
    assert radar_main._to_optional_int("8") == 8
    assert radar_main._to_optional_int("bad") is None
    assert radar_main._to_optional_int(False) is None
    assert radar_main._to_str_list(["A", 1, "B"]) == ["A", "B"]
    assert radar_main._to_str_list(("A", "B")) == []


def test_dedupe_articles_keeps_first_link_and_uses_source_title_fallback() -> None:
    first = _article(title="첫 번째", link="https://example.com/a")
    duplicate = _article(title="두 번째", link="https://example.com/a")
    fallback = _article(title="링크 없음", link="", source="공식")
    fallback_duplicate = _article(title="링크 없음", link="", source="공식")

    deduped = radar_main._dedupe_articles([first, duplicate, fallback, fallback_duplicate])

    assert deduped == [first, fallback]


def test_select_report_articles_combines_published_and_collected_without_sources() -> None:
    published = _article(title="발행 기준", link="https://example.com/a")
    collected = _article(title="수집 기준", link="https://example.com/b")
    duplicate = _article(title="중복", link="https://example.com/a")
    storage = _FakeStorage([published], [duplicate, collected])

    selected = radar_main._select_report_articles(
        storage,
        "benefit",
        recent_days=7,
        sources=None,
    )

    assert selected == [published, collected]


def test_select_report_articles_applies_source_relevance_when_available() -> None:
    relevant = _article(
        title="청년 지원금 신청",
        link="https://example.com/relevant",
        matched_entities={"SubsidyProgram": ["지원금"], "Eligibility": ["신청"]},
    )
    irrelevant = _article(
        title="기관 인사 발표",
        link="https://example.com/irrelevant",
        matched_entities={"GovernmentPolicy": ["policy"]},
    )
    storage = _FakeStorage([relevant, irrelevant], [])
    sources = [
        Source(
            name="정책브리핑",
            type="rss",
            url="https://www.korea.kr/rss/policy.xml",
            config={"event_model": "support_program_notice"},
        )
    ]

    selected = radar_main._select_report_articles(
        storage,
        "benefit",
        recent_days=7,
        sources=sources,
    )

    assert selected == [relevant]
