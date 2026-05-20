from __future__ import annotations

import pytest

from benefitradar.models import Article, Source
from benefitradar.relevance import (
    _contains_any,
    _has_benefit_signal,
    _is_invalid_page,
    _source_context_tags,
    _source_event_model,
    apply_source_context_entities,
    filter_relevant_articles,
)

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


def test_source_context_and_filtering_edge_cases() -> None:
    non_benefit = Article(
        title="Coffee futures",
        link="https://example.com/coffee",
        summary="market update",
        published=None,
        source="Unknown",
        category="coffee",
    )
    missing_source = _article(title="청년 지원금", source="Missing")
    existing_signal = _article(
        title="청년 지원금",
        matched_entities={"SourceSignal": ["existing"]},
    )
    source = Source(
        name="정책브리핑",
        type="rss",
        url="https://www.korea.kr/rss/policy.xml",
        info_purpose=["application_deadline", "ignore"],
        config={"event_model": "support_program_notice"},
    )

    classified = apply_source_context_entities(
        [non_benefit, missing_source, existing_signal],
        [source],
    )

    assert classified[0] is non_benefit
    assert missing_source not in classified
    assert classified[1].matched_entities["SourceSignal"] == [
        "application_deadline",
        "existing",
        "support_program_notice",
    ]

    filtered = filter_relevant_articles(
        [
            non_benefit,
            _article(
                title="404 페이지를 찾을 수 없습니다",
                matched_entities={"SubsidyProgram": ["지원금"]},
            ),
            _article(title="청년 지원금", source="Missing"),
        ],
        [source],
    )
    assert filtered == [non_benefit]


def test_benefit_signal_rules_and_source_event_model_inference() -> None:
    support_source = Source(
        name="정책브리핑",
        type="rss",
        url="https://www.korea.kr/rss/policy.xml",
        config={"event_model": "support_program_notice"},
    )
    api_source = Source(name="복지 API", type="api", url="https://example.com")
    bokjiro_source = Source(name="Other", type="rss", url="https://bokjiro.go.kr/feed")
    welfare_name = Source(name="보건복지 소식", type="rss", url="https://example.com")
    deadline_name = Source(name="고용 공고", type="rss", url="https://example.com")
    unknown_source = Source(name="General", type="rss", url="https://example.com")

    assert _has_benefit_signal(
        _article(title="deduction update", matched_entities={"TaxBenefit": ["deduction"]}),
        support_source,
    )
    assert _has_benefit_signal(
        _article(title="건강보험 안내", matched_entities={"HealthBenefit": ["건강"]}),
        support_source,
    )
    assert _has_benefit_signal(
        _article(
            title="신청 안내",
            matched_entities={"SubsidyProgram": ["지원"], "TargetDemographic": ["청년"]},
        ),
        support_source,
    )
    assert _has_benefit_signal(
        _article(
            title="청년 대상 안내",
            matched_entities={"Eligibility": ["대상"], "TargetDemographic": ["청년"]},
        ),
        support_source,
    )
    assert not _has_benefit_signal(
        _article(title="일반 정책", matched_entities={"Eligibility": ["대상"]}),
        unknown_source,
    )

    assert _source_context_tags(api_source) == ["eligibility_rule"]
    assert _source_event_model(bokjiro_source) == "eligibility_rule"
    assert _source_event_model(welfare_name) == "eligibility_rule"
    assert _source_event_model(deadline_name) == "application_deadline"
    assert _source_event_model(unknown_source) == ""
    assert _contains_any("this has tax credit info", {"tax credit"})
    assert _is_invalid_page(_article(title="service unavailable"))
