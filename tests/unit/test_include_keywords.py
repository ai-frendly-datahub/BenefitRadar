"""Pew Research include_keywords gate.

Fixtures use titles quoted in the 2026-10-29 off-topic filter spec.
Ellipses in those quotations are kept as written; nothing past them is invented.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benefitradar import collector
from benefitradar.collector import (
    _collect_single,
    _include_keywords,
    _keyword_in_text,
    _passes_include_keywords,
)
from benefitradar.config_loader import load_category_config
from benefitradar.models import Source

pytestmark = pytest.mark.unit

# Spec section 4 keyword list, Pew Research only.
PEW_INCLUDE_KEYWORDS = [
    "benefit",
    "social security",
    "medicaid",
    "medicare",
    "snap",
    "food stamp",
    "poverty",
    "afford",
    "income",
    "wage",
    "worker",
    "caregiv",
    "retire",
    "pension",
    "tax",
    "insurance",
    "welfare",
    "unemployment",
    "housing",
    "childcare",
    "child care",
    "disability",
    "paid leave",
    "minimum wage",
    "job",
    "cost of living",
    "stimulus",
    "subsid",
    "eligib",
    "veteran",
    "health care",
    "healthcare",
    "prescription",
]

# Quoted titles. The two useful items the gate keeps come first.
# "Women, Work and Caregiving…" is the one useful mid item the spec says this list drops.
QUOTED_TITLES = [
    "Many Americans can't afford basic necessities",
    "Men Are Experiencing More Job Growth Than Women in AI-Exposed Occupations",
    "Women, Work and Caregiving Through the Arc of Family Life",
    "Methodology",
    "Acknowledgments",
    "Pace of Trump's Judicial Appointments Has Slowed…",
    "Americans' Use of Social Media in 2026",
    "Quiz: What Is Your Civic Personality?",
]

KEPT_TITLES = {
    "Many Americans can't afford basic necessities",
    "Men Are Experiencing More Job Growth Than Women in AI-Exposed Occupations",
}


def _pew_source(**config_overrides: object) -> Source:
    config: dict[str, object] = {"include_keywords": list(PEW_INCLUDE_KEYWORDS)}
    config.update(config_overrides)
    return Source(
        name="Pew Research",
        type="rss",
        url="https://www.pewresearch.org/feed/",
        config=config,
    )


def _entries(titles: list[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for index, title in enumerate(titles):
        stored_title = title
        if title == "Many Americans can't afford basic necessities":
            stored_title = "Many Americans can&#39;t afford basic necessities"
        entries.append(
            {
                "title": stored_title,
                "link": f"https://example.com/pew/{index}",
                "summary": "",
            }
        )
    return entries


def _collect(source: Source, titles: list[str], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    response = Mock()
    response.content = b"<rss/>"
    monkeypatch.setattr(collector, "_fetch_url_with_retry", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        collector.feedparser,
        "parse",
        Mock(return_value=SimpleNamespace(entries=_entries(titles))),
    )
    articles = _collect_single(source, category="benefit", limit=20, timeout=3)
    return [article.title for article in articles]


def test_pew_gate_keeps_quoted_benefit_titles_and_drops_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    titles = _collect(_pew_source(), QUOTED_TITLES, monkeypatch)

    assert titles == [
        "Many Americans can't afford basic necessities",
        "Men Are Experiencing More Job Growth Than Women in AI-Exposed Occupations",
    ]
    assert "Methodology" not in titles
    assert "Women, Work and Caregiving Through the Arc of Family Life" not in titles


def test_unconfigured_source_keeps_quoted_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source(name="Federal News Network", type="rss", url="https://example.com/fnn")

    assert _collect(source, QUOTED_TITLES, monkeypatch) == QUOTED_TITLES


def test_empty_or_non_list_keyword_config_does_not_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_gate = _pew_source(include_keywords=[])
    string_gate = _pew_source(include_keywords="benefit")

    assert _collect(empty_gate, ["Methodology"], monkeypatch) == ["Methodology"]
    assert _collect(string_gate, ["Methodology"], monkeypatch) == ["Methodology"]
    assert _include_keywords(Source(name="plain", type="rss", url="https://example.com")) == []


def test_word_boundary_plural_and_korean_matching() -> None:
    snap_source = Source(
        name="Pew Research",
        type="rss",
        url="https://www.pewresearch.org/feed/",
        config={"include_keywords": ["snap", "food stamp", "worker", "복지"]},
    )

    assert _passes_include_keywords(snap_source, "SNAP benefits expanded", "")
    assert not _passes_include_keywords(snap_source, "Civic snapshot of public opinion", "")
    assert _passes_include_keywords(snap_source, "Food stamps application window", "")
    assert _passes_include_keywords(snap_source, "Hiring more workers", "")
    assert not _passes_include_keywords(snap_source, "A workerbee study", "")
    assert _passes_include_keywords(snap_source, "기초생활 복지 안내", "")
    assert not _passes_include_keywords(snap_source, "Methodology", "no benefit terms here")

    assert _keyword_in_text("snap", "a snapshot report") is False
    assert _keyword_in_text("snap", "snap benefits") is True
    assert _keyword_in_text("job", "more jobs this year") is True
    assert _keyword_in_text("job", "jobless rate") is False
    assert _keyword_in_text("cost of living", "the cost of livingston index") is False
    assert _keyword_in_text("복지", "청년복지정책") is True
    assert _keyword_in_text("복지", "health policy") is False


def test_summary_keyword_keeps_item_and_blank_keywords_are_ignored() -> None:
    assert _passes_include_keywords(
        _pew_source(),
        "Methodology",
        "Notes on medicaid enrollment",
    )

    source = _pew_source(include_keywords=["job", None, "  ", 3])
    assert _include_keywords(source) == ["job"]
    assert _passes_include_keywords(source, "More jobs opened", "")
    assert not _passes_include_keywords(source, "Methodology", "")
    assert _keyword_in_text("   ", "job") is False


def test_benefit_yaml_sets_pew_keywords_and_leaves_pew_enabled() -> None:
    category = load_category_config("benefit")
    by_name = {source.name: source for source in category.sources}
    pew = by_name["Pew Research"]

    assert pew.enabled is True
    assert pew.config["include_keywords"] == PEW_INCLUDE_KEYWORDS
    assert all(
        "include_keywords" not in source.config
        for source in category.sources
        if source.name != "Pew Research"
    )
    assert "include_keywords" not in by_name["Federal News Network"].config
    assert "include_keywords" not in by_name["Government Executive"].config
    assert "include_keywords" not in by_name["Tax Foundation"].config
