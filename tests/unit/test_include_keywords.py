"""Pew Research include_keywords gate.

Quoted titles come from the 2026-10-29 off-topic filter spec.
The caregiving regression loads the title and summary stored in data/raw.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
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

# Quoted titles. The three useful mid items the gate keeps come first.
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
    "Women, Work and Caregiving Through the Arc of Family Life",
}

CAREGIVING_TITLE = "Women, Work and Caregiving Through the Arc of Family Life"
ELIGIBLE_VOTERS_TITLE = "What We Know About Eligible Voters Who Are Naturalized U.S. Citizens"
DATA_RAW = Path(__file__).resolve().parents[2] / "data" / "raw"


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


def _iter_source_records(source_name: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in sorted(DATA_RAW.glob(f"*/{source_name}.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
    return records


def _record_text(record: dict[str, str]) -> str:
    return f"{record.get('title', '')} {record.get('summary', '')}".lower()


def _find_record(source_name: str, needle: str) -> dict[str, str]:
    for record in _iter_source_records(source_name):
        if needle in _record_text(record):
            return record
    raise AssertionError(f"{needle!r} was not found in collected {source_name} data")


def test_pew_gate_keeps_quoted_benefit_titles_and_drops_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    titles = _collect(_pew_source(), QUOTED_TITLES, monkeypatch)

    assert titles == [
        "Many Americans can't afford basic necessities",
        "Men Are Experiencing More Job Growth Than Women in AI-Exposed Occupations",
        CAREGIVING_TITLE,
    ]
    assert "Methodology" not in titles


def test_real_caregiving_item_is_kept_and_trailing_boundary_misses_it() -> None:
    """Collected Pew title. ``caregiv`` plus a trailing boundary does not match Caregiving."""
    record = _find_record("Pew Research", CAREGIVING_TITLE.lower())
    title = record["title"]
    summary = record.get("summary") or ""
    text = _record_text(record)

    assert title == CAREGIVING_TITLE
    assert re.search(r"(?<![a-z0-9])caregivs?(?![a-z0-9])", text) is None
    assert _keyword_in_text("caregiv", text) is True
    assert _passes_include_keywords(_pew_source(), title, summary) is True


def test_other_stems_match_real_benefit_words_and_collected_noise_stays_out() -> None:
    subsidies = _find_record("Tax Foundation", "healthcare subsidies")
    eligibility = _find_record("KFF Health News", "medicaid eligibility")
    retired = _find_record("Federal News Network", "retired federal employees")
    subsidiary = _find_record("Pew Research", "subsidiary of the pew charitable trusts")
    voters = _find_record("Pew Research", ELIGIBLE_VOTERS_TITLE.lower())

    assert _keyword_in_text("subsid", _record_text(subsidies)) is True
    assert _keyword_in_text("eligib", _record_text(eligibility)) is True
    assert _keyword_in_text("retire", _record_text(retired)) is True
    assert _keyword_in_text("subsid", _record_text(subsidiary)) is False
    assert (
        _passes_include_keywords(
            _pew_source(),
            voters["title"],
            voters.get("summary") or "",
        )
        is False
    )
    assert voters["title"] == ELIGIBLE_VOTERS_TITLE


def test_october_window_keeps_the_three_valid_mid_items() -> None:
    records: dict[str, dict[str, str]] = {}
    for path in sorted(DATA_RAW.glob("*/Pew Research.jsonl")):
        day = path.parent.name
        if not ("2026-10-22" <= day <= "2026-10-28"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            link = str(item.get("link") or item.get("title"))
            records[link] = item

    source = _pew_source()
    kept = [
        item["title"]
        for item in records.values()
        if _passes_include_keywords(source, item["title"], item.get("summary") or "")
    ]

    assert CAREGIVING_TITLE in kept
    assert ELIGIBLE_VOTERS_TITLE not in kept
    assert len(records) == 43
    assert len(kept) == 3
    assert sum(1 for title in kept if title != CAREGIVING_TITLE) == 2


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
