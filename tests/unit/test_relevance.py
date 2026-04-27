from __future__ import annotations

import pytest

from benefitradar.models import Article, Source
from benefitradar.relevance import apply_source_context_entities, filter_relevant_articles


pytestmark = pytest.mark.unit


def _article(
    *,
    title: str,
    source: str = "정책브리핑",
    summary: str | None = None,
    matched_entities: dict[str, list[str]] | None = None,
) -> Article:
    return Article(
        title=title,
        link=f"https://example.com/{title}",
        summary=summary if summary is not None else title,
        published=None,
        source=source,
        category="benefit",
        matched_entities=matched_entities or {},
    )


def test_apply_source_context_entities_adds_event_model_signal() -> None:
    article = _article(
        title="청년 지원금 신청",
        matched_entities={"SubsidyProgram": ["지원금"]},
    )
    source = Source(
        name="정책브리핑",
        type="rss",
        url="https://www.korea.kr/rss/policy.xml",
        config={"event_model": "support_program_notice"},
    )

    classified = apply_source_context_entities([article], [source])

    assert classified[0].matched_entities["SourceSignal"] == ["support_program_notice"]


def test_filter_relevant_articles_keeps_benefit_rows_and_drops_generic_policy() -> None:
    sources = [
        Source(
            name="정책브리핑",
            type="rss",
            url="https://www.korea.kr/rss/policy.xml",
            config={"event_model": "support_program_notice"},
        ),
        Source(name="Tax Foundation", type="rss", url="https://taxfoundation.org/feed/"),
    ]
    articles = [
        _article(
            title="고유가 피해지원금 신청 마감",
            matched_entities={
                "SubsidyProgram": ["지원금"],
                "Eligibility": ["신청"],
                "OperationalEvent": ["application_deadline"],
            },
        ),
        _article(
            title="관세청 인사 발표",
            matched_entities={"GovernmentPolicy": ["policy"]},
        ),
        _article(
            title="Rethinking Your Tax Refund",
            source="Tax Foundation",
            matched_entities={"TaxBenefit": ["tax refund"]},
        ),
    ]

    filtered = filter_relevant_articles(
        apply_source_context_entities(articles, sources),
        sources,
    )

    assert [article.title for article in filtered] == [
        "고유가 피해지원금 신청 마감",
        "Rethinking Your Tax Refund",
    ]
