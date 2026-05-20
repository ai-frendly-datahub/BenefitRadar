from __future__ import annotations

from collections.abc import Iterable

from .models import Article, Source

TRACKED_EVENT_MODELS = {
    "application_deadline",
    "eligibility_rule",
    "selection_result",
    "support_program_notice",
}
SOURCE_CONTEXT_PURPOSES = {
    "application_deadline",
    "eligibility_rule",
    "selection_result",
    "support_program_notice",
}
OPERATIONAL_ENTITY_NAMES = {
    "ApplicationDeadline",
    "ApplicationStartDate",
    "OperationalEvent",
    "SelectionResultDate",
}
BENEFIT_DOMAIN_ENTITY_NAMES = {
    "EducationSupport",
    "Eligibility",
    "EmploymentBenefit",
    "FamilySupport",
    "HealthBenefit",
    "HousingAssistance",
    "SocialWelfare",
    "SubsidyProgram",
    "TargetDemographic",
    "TaxBenefit",
}
CORE_BENEFIT_TERMS = {
    "aca",
    "affordability",
    "assistance",
    "benefit",
    "benefits",
    "coverage",
    "earned income",
    "food assistance",
    "grant",
    "housing assistance",
    "medicaid",
    "medicare",
    "rental assistance",
    "snap",
    "social security",
    "subsidy",
    "tax credit",
    "tax refund",
    "tax relief",
    "unemployment",
    "welfare",
    "교육급여",
    "급여",
    "기초생활",
    "돌봄",
    "바우처",
    "복지",
    "보조금",
    "생계급여",
    "세액공제",
    "소득공제",
    "수당",
    "의료급여",
    "의료비 지원",
    "장려금",
    "저소득",
    "전세 지원",
    "주거급여",
    "지원금",
    "차상위",
    "취약계층",
    "환급",
}
APPLICATION_TERMS = {
    "application",
    "apply",
    "deadline",
    "마감",
    "모집",
    "선정",
    "신청",
    "접수",
}
TAX_BENEFIT_TERMS = {
    "deduction",
    "eitc",
    "tax benefit",
    "tax break",
    "tax credit",
    "tax refund",
    "tax relief",
    "근로장려금",
    "소득공제",
    "세금감면",
    "세액공제",
    "세제혜택",
    "자녀장려금",
    "환급",
}


def apply_source_context_entities(
    articles: Iterable[Article],
    sources: Iterable[Source],
) -> list[Article]:
    source_map = {source.name: source for source in sources if source.enabled}
    classified: list[Article] = []
    for article in articles:
        if article.category != "benefit":
            classified.append(article)
            continue

        source = source_map.get(article.source)
        if source is None:
            continue

        tags = _source_context_tags(source)
        if tags:
            existing = article.matched_entities.get("SourceSignal", [])
            existing_values = existing if isinstance(existing, list) else [existing]
            article.matched_entities["SourceSignal"] = sorted(
                {str(value) for value in existing_values} | set(tags)
            )
        classified.append(article)
    return classified


def filter_relevant_articles(
    articles: Iterable[Article],
    sources: Iterable[Source],
) -> list[Article]:
    source_map = {source.name: source for source in sources if source.enabled}
    filtered: list[Article] = []
    for article in articles:
        if article.category != "benefit":
            filtered.append(article)
            continue

        source = source_map.get(article.source)
        if source is None:
            continue
        if _is_invalid_page(article):
            continue
        if _has_benefit_signal(article, source):
            filtered.append(article)
    return filtered


def _has_benefit_signal(article: Article, source: Source) -> bool:
    entity_names = set(article.matched_entities)
    text = f"{article.title} {article.summary}".lower()
    domain_entities = entity_names & BENEFIT_DOMAIN_ENTITY_NAMES

    if entity_names & OPERATIONAL_ENTITY_NAMES:
        return True
    if not domain_entities:
        return False
    if _contains_any(text, CORE_BENEFIT_TERMS):
        return True
    if "TaxBenefit" in domain_entities and _contains_any(text, TAX_BENEFIT_TERMS):
        return True
    if domain_entities & {"SocialWelfare", "HealthBenefit", "HousingAssistance"}:
        return True
    if _contains_any(text, APPLICATION_TERMS) and len(domain_entities) >= 2:
        return True
    if _source_event_model(source) in TRACKED_EVENT_MODELS and len(domain_entities) >= 2:
        return True
    return False


def _source_context_tags(source: Source) -> list[str]:
    tags = {purpose for purpose in source.info_purpose if purpose in SOURCE_CONTEXT_PURPOSES}
    event_model = _source_event_model(source)
    if event_model in TRACKED_EVENT_MODELS:
        tags.add(event_model)
    return sorted(tags)


def _source_event_model(source: Source) -> str:
    raw = source.config.get("event_model")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    source_url = source.url.lower()
    source_name = source.name.lower()
    if source.type.lower() == "api" or "bokjiro" in source_url:
        return "eligibility_rule"
    if any(token in source_name for token in ("복지", "여성가족", "보건복지")):
        return "eligibility_rule"
    if any(token in source_name for token in ("일자리", "고용", "교육", "국토", "중소벤처")):
        return "application_deadline"
    return ""


def _contains_any(text: str, terms: set[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _is_invalid_page(article: Article) -> bool:
    haystack = f"{article.title} {article.summary}".lower()
    return any(
        marker in haystack
        for marker in (
            "404",
            "access denied",
            "not found",
            "page not found",
            "request blocked",
            "service unavailable",
            "페이지를 찾을 수 없습니다",
        )
    )
