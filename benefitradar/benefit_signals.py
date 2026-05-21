from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from urllib.parse import parse_qs, urlparse

from .models import Article

_APPLICATION_MARKERS = (
    "신청",
    "접수",
    "모집",
    "공모",
    "마감",
    "접수기간",
    "신청기간",
    "deadline",
    "apply",
    "application",
)
_DEADLINE_CONTEXT_MARKERS = ("마감", "까지", "deadline", "closing")
_SELECTION_RESULT_MARKERS = (
    "선정결과",
    "선정 결과",
    "선정자",
    "선정평가 결과",
    "최종선정",
    "최종 선정",
    "신규 선정",
    "예비선정",
    "예비 선정",
    "합격자",
    "선정기업",
    "선정 기업",
    "지원대상 선정",
    "selection result",
)
_APPLICATION_CHANNEL_MARKERS = {
    "online": ("온라인", "온라인신청", "인터넷", "복지로", "정부24", "bokjiro", "gov.kr"),
    "visit": ("방문", "방문신청", "주민센터", "행정복지센터", "센터 방문"),
    "mail": ("우편", "등기", "우편접수"),
    "email": ("이메일", "email", "e-mail"),
}
_AMOUNT_RE = re.compile(
    r"(?:(?:최대|월|연|총|약|지원(?:금|액)?|급여|바우처)\s*)"
    r"(?P<amount>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>억\s*원|억원|만\s*원|만원|천\s*원|천원|원)"
)
_AGE_RANGE_RE = re.compile(
    r"(?<!\d)(?:만\s*)?(?P<start>\d{1,3})\s*(?:세)?\s*(?:~|-|부터|이상)\s*"
    r"(?:만\s*)?(?P<end>\d{1,3})?\s*(?:세)?\s*(?:까지|이하)?(?!\d)"
)
_SELECTION_COUNT_UNIT_RE = (
    r"(?:명|가구|세대|팀|개사|개\s*사|개교|개소|개처|개|건|곳|기관|기업|상품|사례)"
)
_SELECTION_ACTION_RE = r"(?:선정|선발|합격|지정)"
_SELECTION_COUNT_RE = re.compile(
    rf"(?P<count>\d{{1,5}}(?:,\d{{3}})*)\s*{_SELECTION_COUNT_UNIT_RE}\s*"
    rf"(?:을|를|이|가|의)?[^\d]{{0,40}}?{_SELECTION_ACTION_RE}"
)
_SELECTION_COUNT_REVERSED_RE = re.compile(
    rf"{_SELECTION_ACTION_RE}(?:자|대상|가구|기업|팀)?\s*"
    rf"(?P<count>\d{{1,5}}(?:,\d{{3}})*)\s*{_SELECTION_COUNT_UNIT_RE}"
)
_SELECTION_RESULT_EVIDENCE_RE = re.compile(
    rf"(?:선정\s*결과|선정평가\s*결과|결과\s*발표|공모\s*결과)\s*"
    rf"[\w가-힣\s·'\"“”‘’()「」-]{{0,45}}?{_SELECTION_ACTION_RE}|"
    rf"(?:최종|신규|예비)\s*[\w가-힣\s·'\"“”‘’()「」-]{{0,30}}?{_SELECTION_ACTION_RE}|"
    rf"(?:우수|대상(?:지|자)?)\s*[\w가-힣\s·'\"“”‘’()「」-]{{0,35}}?"
    rf"\d{{1,5}}(?:,\d{{3}})*\s*{_SELECTION_COUNT_UNIT_RE}\s*{_SELECTION_ACTION_RE}|"
    rf"{_SELECTION_ACTION_RE}(?:됐|되었|했다|하였다|했다고\s*밝)"
)
_SELECTION_PROGRAM_TITLE_RE = re.compile(
    rf"(?P<title>[가-힣A-Za-z0-9][가-힣A-Za-z0-9·ㆍ\s'\"“”‘’()「」,-]{{2,90}}?)"
    rf"(?:\s+\d{{1,5}}(?:,\d{{3}})*\s*{_SELECTION_COUNT_UNIT_RE})?\s*"
    rf"(?:(?:최종|신규|우수|대상(?:지|자)?|후보(?:지)?|지원|사업|과제)\s*){{0,3}}"
    rf"{_SELECTION_ACTION_RE}"
)
_PROGRAM_TITLE_RE = re.compile(
    r"(?P<title>[가-힣A-Za-z0-9·\-\s]{2,80}?"
    r"(?:월세\s*지원|주거\s*지원|복지\s*사업|지원사업|지원금|바우처|장려금|급여|수당))"
)
_ELIGIBILITY_RULE_MARKERS = (
    "대상",
    "신청자격",
    "지원자격",
    "자격",
    "요건",
    "조건",
    "소득",
    "중위소득",
    "재산",
    "가구",
    "세대",
    "거주",
    "지역",
    "만 ",
    "세 이상",
    "세 이하",
    "eligible",
    "eligibility",
    "qualification",
    "requirement",
)

_YEAR_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*(?:년|[.\-/])\s*"
    r"(?P<month>\d{1,2})\s*(?:월|[.\-/])\s*"
    r"(?P<day>\d{1,2})\s*(?:일|\.)?"
)
_MONTH_DATE_RE = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*(?:월|[.\-/])\s*" r"(?P<day>\d{1,2})\s*(?:일|\.)?(?!\d)"
)


@dataclass(frozen=True)
class ApplicationWindow:
    start_date: str | None
    deadline: str | None


def enrich_benefit_operational_fields(articles: Iterable[Article]) -> list[Article]:
    """Add deadline, eligibility, and selection-result hints to article entities."""
    enriched: list[Article] = []
    for article in articles:
        text = _benefit_operational_text(article)
        window = extract_application_window(text, reference_date=article.published)
        eligibility = extract_eligibility_fields(text)
        program_id = extract_program_id(article.link)
        program_key = build_program_key(
            program_id=program_id,
            title=article.title,
            source=article.source,
        )
        benefit_amounts = extract_benefit_amounts(text)
        application_channels = extract_application_channels(text)
        selection_result = extract_selection_result_fields(
            article.title,
            reference_date=article.published,
        )
        if not selection_result and _has_explicit_selection_result_summary(article.summary):
            selection_result = extract_selection_result_fields(
                article.summary,
                reference_date=article.published,
            )
            if selection_result and article.title.strip():
                selection_result["program_title"] = [article.title.strip()]

        matches = dict(article.matched_entities)
        event_models: list[str] = []
        if program_id:
            matches["BenefitProgramId"] = [program_id]
        if program_key:
            matches["BenefitProgramKey"] = [program_key]
        if window.start_date:
            matches["ApplicationStartDate"] = [window.start_date]
        if window.deadline:
            matches["ApplicationDeadline"] = [window.deadline]
            event_models.append("application_deadline")
        if application_channels:
            matches["ApplicationChannel"] = application_channels
        if benefit_amounts:
            matches["BenefitAmount"] = benefit_amounts
        for field_name, values in eligibility.items():
            matches[f"Eligibility{field_name.title().replace('_', '')}"] = values
        if _has_eligibility_rule_evidence(text, eligibility):
            event_models.append("eligibility_rule")
        for field_name, values in selection_result.items():
            matches[_selection_entity_key(field_name)] = values
        if selection_result:
            event_models.append("selection_result")
        if event_models:
            matches["OperationalEvent"] = _dedupe_ordered(
                [*matches.get("OperationalEvent", []), *event_models]
                if isinstance(matches.get("OperationalEvent"), list)
                else event_models
            )

        article.matched_entities = matches
        enriched.append(article)
    return enriched


def extract_application_window(
    text: str, *, reference_date: datetime | None = None
) -> ApplicationWindow:
    haystack = text.strip()
    haystack_lower = haystack.lower()
    if not haystack or not any(marker in haystack_lower for marker in _APPLICATION_MARKERS):
        return ApplicationWindow(start_date=None, deadline=None)

    dates = _extract_dates(haystack, reference_date=reference_date)
    if not dates:
        return ApplicationWindow(start_date=None, deadline=None)

    if len(dates) >= 2:
        return ApplicationWindow(start_date=dates[0], deadline=dates[-1])
    if any(marker in haystack_lower for marker in _DEADLINE_CONTEXT_MARKERS):
        return ApplicationWindow(start_date=None, deadline=dates[0])
    return ApplicationWindow(start_date=None, deadline=None)


def _benefit_operational_text(article: Article) -> str:
    title = article.title.strip()
    summary = article.summary.strip()
    if _has_embedded_operational_sections(title):
        return title
    return "\n".join(part for part in (title, summary) if part)


def _has_embedded_operational_sections(text: str) -> bool:
    return "모집일정" in text and "지원대상" in text


def extract_selection_result_date(text: str, *, reference_date: datetime | None = None) -> str:
    haystack = _plain_operational_text(text)
    haystack_lower = haystack.lower()
    if not haystack or "발표" not in haystack_lower or not _has_selection_result_evidence(haystack):
        return ""
    dates = _extract_dates(haystack, reference_date=reference_date)
    return dates[-1] if dates else ""


def extract_selection_result_fields(
    text: str, *, reference_date: datetime | None = None
) -> dict[str, list[str]]:
    haystack = _plain_operational_text(text)
    if not haystack or not _has_selection_result_evidence(haystack):
        return {}
    evidence_text = " ".join(_selection_candidate_sentences(haystack)) or haystack

    fields: dict[str, list[str]] = {}
    result_date = extract_selection_result_date(evidence_text, reference_date=reference_date)
    if result_date:
        fields["result_date"] = [result_date]

    selected_count = _best_selection_count(evidence_text)
    if selected_count:
        fields["selected_count"] = [selected_count.replace(",", "")]

    amounts = extract_benefit_amounts(evidence_text)
    if amounts:
        fields["execution_amount"] = amounts[:1]

    program_title = extract_program_title(evidence_text) or extract_selection_program_title(
        evidence_text
    )
    if program_title:
        fields["program_title"] = [program_title]

    if not any(key in fields for key in ("selected_count", "execution_amount", "program_title")):
        return {}
    return fields


def extract_selection_program_title(text: str) -> str:
    haystack = _plain_operational_text(text)
    for sentence in _selection_candidate_sentences(haystack):
        match = _SELECTION_PROGRAM_TITLE_RE.search(sentence)
        if not match:
            continue
        title = _clean_selection_program_title(match.group("title"))
        if title:
            return title
    return ""


def extract_eligibility_fields(text: str) -> dict[str, list[str]]:
    keyword_groups = {
        "target_group": [
            "청년",
            "신혼부부",
            "한부모",
            "장애인",
            "노인",
            "어르신",
            "저소득",
            "기초생활수급",
            "차상위",
            "취약계층",
            "소상공인",
            "자영업자",
            "중소기업",
            "실업자",
            "구직자",
        ],
        "life_stage": ["임신", "출산", "영유아", "아동", "청소년", "대학생", "군인"],
        "household": ["1인가구", "다자녀", "맞벌이", "가구", "세대"],
        "income_condition": ["소득", "중위소득", "재산", "기준중위소득", "보험료"],
        "region": [
            "서울",
            "경기",
            "인천",
            "부산",
            "대구",
            "광주",
            "대전",
            "울산",
            "세종",
            "강원",
            "충북",
            "충남",
            "전북",
            "전남",
            "경북",
            "경남",
            "제주",
        ],
    }
    haystack = text.lower()
    matches: dict[str, list[str]] = {}
    for field_name, keywords in keyword_groups.items():
        hits = _keyword_hits(haystack, keywords)
        if hits:
            matches[field_name] = _dedupe_ordered(hits)
    age_conditions = extract_age_conditions(text)
    if age_conditions:
        matches["age_condition"] = age_conditions
    return matches


def extract_benefit_amounts(text: str) -> list[str]:
    amounts: list[str] = []
    for match in _AMOUNT_RE.finditer(text):
        amount = match.group("amount").replace(",", "")
        unit = re.sub(r"\s+", "", match.group("unit"))
        amounts.append(f"{amount}{unit}")
    return _dedupe_ordered(amounts)


def extract_application_channels(text: str) -> list[str]:
    haystack = text.lower()
    channels: list[str] = []
    for channel, markers in _APPLICATION_CHANNEL_MARKERS.items():
        if any(marker.lower() in haystack for marker in markers):
            channels.append(channel)
    return channels


def extract_age_conditions(text: str) -> list[str]:
    conditions: list[str] = []
    for match in _AGE_RANGE_RE.finditer(text):
        raw = match.group(0)
        if "만" not in raw and "세" not in raw:
            continue
        start = match.group("start")
        end = match.group("end")
        if not start:
            continue
        start_age = int(start)
        end_age = int(end) if end else None
        if start_age > 120 or (end_age is not None and end_age > 120):
            continue
        if end:
            conditions.append(f"{start}-{end}")
        elif any(token in match.group(0) for token in ("이상", "부터")):
            conditions.append(f"{start}+")
        elif any(token in match.group(0) for token in ("이하", "까지")):
            conditions.append(f"0-{start}")
    return _dedupe_ordered(conditions)


def extract_program_title(text: str) -> str:
    match = _PROGRAM_TITLE_RE.search(text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group("title")).strip()


def _plain_operational_text(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def _has_selection_result_evidence(text: str) -> bool:
    haystack = _plain_operational_text(text)
    haystack_lower = haystack.lower()
    if _is_future_selection_statement(haystack):
        return False
    if any(marker in haystack_lower for marker in _SELECTION_RESULT_MARKERS):
        return True
    return bool(_SELECTION_RESULT_EVIDENCE_RE.search(haystack))


def _has_explicit_selection_result_summary(text: str) -> bool:
    haystack = _plain_operational_text(text).lower()
    if _is_future_selection_statement(haystack):
        return False
    return any(
        marker in haystack
        for marker in (
            "선정 결과",
            "선정결과",
            "선정평가 결과",
            "결과를 발표",
            "결과 발표",
        )
    )


def _is_future_selection_statement(text: str) -> bool:
    haystack = _plain_operational_text(text)
    return any(
        marker in haystack
        for marker in (
            "발표할 계획",
            "발표할 예정",
            "선정할 계획",
            "선정할 예정",
        )
    )


def _selection_candidate_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\[[^\]]{2,30}\]", " ", text)
    parts = re.split(r"[!?\n]|…| - ", normalized)
    candidates = [re.sub(r"\s+", " ", part).strip() for part in parts]
    return [candidate for candidate in candidates if _has_selection_result_evidence(candidate)]


def _clean_selection_program_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip(" ,-'\"“”‘’")
    cleaned = re.sub(r"^(?:새글\s*)+", "", cleaned)
    cleaned = re.sub(r"^[가-힣A-Za-z0-9·ㆍ\s]{2,30}부는\s+", "", cleaned)
    cleaned = re.sub(r"^[가-힣A-Za-z0-9·ㆍ\s]{2,30}청은\s+", "", cleaned)
    cleaned = re.sub(r"^[가-힣A-Za-z0-9·ㆍ\s]{2,30}는\s+", "", cleaned)
    if not cleaned or cleaned in {"선정 결과", "선정결과", "결과 발표"}:
        return ""
    return cleaned[:120].strip()


def extract_program_id(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("servId", "wlfareInfoId", "svcId", "programId"):
        values = query.get(key)
        if values and values[0].strip():
            return values[0].strip()
    path_tail = parsed.path.rstrip("/").split("/")[-1]
    if path_tail and re.fullmatch(r"[A-Za-z0-9_-]{5,}", path_tail):
        return path_tail
    return ""


def build_program_key(*, program_id: str, title: str, source: str) -> str:
    if program_id:
        return f"id:{program_id}"
    title_key = _normalize_key_text(extract_program_title(title) or _compact_title_for_key(title))
    source_key = _normalize_key_text(source)
    if not title_key:
        return ""
    return f"title:{source_key}:{title_key}" if source_key else f"title:{title_key}"


def _compact_title_for_key(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    scored: list[tuple[int, int, str]] = []
    for line in lines:
        if len(line) < 4:
            continue
        score = 0
        if any(
            marker in line.lower()
            for marker in (
                "모집",
                "공고",
                "지원",
                "사업",
                "신청",
                "수당",
                "급여",
                "바우처",
                "장려금",
                "benefit",
                "grant",
                "subsidy",
            )
        ):
            score += 3
        if re.search(r"20\d{2}", line):
            score += 1
        if len(line) > 12:
            score += 1
        if line.startswith("#") or line.lower().startswith("d -"):
            score -= 3
        if line in {"모집일정 :", "모집일정", "지원대상 :", "지원대상"}:
            score -= 3
        scored.append((score, min(len(line), 120), line))
    if not scored:
        return lines[0][:120]
    return max(scored)[2][:120]


def _has_eligibility_rule_evidence(text: str, eligibility: dict[str, list[str]]) -> bool:
    if not eligibility:
        return False
    haystack = text.lower()
    return any(marker.lower() in haystack for marker in _ELIGIBILITY_RULE_MARKERS)


def _normalize_key_text(text: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", text.strip().lower())
    return normalized.strip("-")


def _extract_dates(text: str, *, reference_date: datetime | None = None) -> list[str]:
    dates: list[str] = []
    occupied_spans: list[tuple[int, int]] = []
    for match in _YEAR_DATE_RE.finditer(text):
        parsed = _iso_date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
        if parsed:
            dates.append(parsed)
            occupied_spans.append(match.span())

    base_year = _reference_year(reference_date)
    for match in _MONTH_DATE_RE.finditer(text):
        if _overlaps(match.span(), occupied_spans):
            continue
        parsed = _iso_date(base_year, int(match.group("month")), int(match.group("day")))
        if parsed:
            dates.append(parsed)
    return _dedupe_ordered(dates)


def _reference_year(reference_date: datetime | None) -> int:
    if reference_date is None:
        return datetime.now(UTC).year
    return reference_date.year


def _iso_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(
        start < occupied_end and end > occupied_start for occupied_start, occupied_end in spans
    )


def _keyword_hits(haystack: str, keywords: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for keyword in sorted(keywords, key=len, reverse=True):
        normalized = keyword.lower()
        if normalized not in haystack:
            continue
        if any(normalized in existing.lower() for existing in hits):
            continue
        hits.append(keyword)
    return hits


def _first_group_match(text: str, patterns: Iterable[re.Pattern[str]], group: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return str(match.group(group))
    return ""


def _best_selection_count(text: str) -> str:
    matches: list[tuple[int, int, str]] = []
    for pattern in (_SELECTION_COUNT_RE, _SELECTION_COUNT_REVERSED_RE):
        for match in pattern.finditer(text):
            matches.append((match.end() - match.start(), match.start(), str(match.group("count"))))
    if not matches:
        return ""
    return min(matches)[2]


def _selection_entity_key(field_name: str) -> str:
    if field_name == "result_date":
        return "SelectionResultDate"
    return f"Selection{field_name.title().replace('_', '')}"


def _dedupe_ordered(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
