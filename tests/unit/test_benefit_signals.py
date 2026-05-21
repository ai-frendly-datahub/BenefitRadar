from __future__ import annotations

from datetime import UTC, datetime

import pytest

from benefitradar.benefit_signals import (
    build_program_key,
    enrich_benefit_operational_fields,
    extract_application_channels,
    extract_benefit_amounts,
    extract_selection_program_title,
    extract_selection_result_fields,
)
from benefitradar.models import Article

pytestmark = pytest.mark.unit


def test_enrich_benefit_operational_fields_extracts_deadline_eligibility_and_program_id() -> None:
    article = Article(
        title="서울 청년 월세 지원 신청 마감 2026년 4월 30일",
        link="https://example.com/detail?servId=BENE12345",
        summary="청년 1인가구 대상, 만 19세 이상 34세 이하, 소득 기준중위소득 조건, 복지로 온라인 신청, 월 20만원 지원.",
        published=datetime(2026, 4, 12, tzinfo=UTC),
        source="서울시 복지",
        category="benefit",
        matched_entities={},
    )

    enriched = enrich_benefit_operational_fields([article])[0]

    assert enriched.matched_entities["BenefitProgramId"] == ["BENE12345"]
    assert enriched.matched_entities["BenefitProgramKey"] == ["id:BENE12345"]
    assert enriched.matched_entities["ApplicationDeadline"] == ["2026-04-30"]
    assert enriched.matched_entities["ApplicationChannel"] == ["online"]
    assert enriched.matched_entities["BenefitAmount"] == ["20만원"]
    assert enriched.matched_entities["EligibilityTargetGroup"] == ["청년"]
    assert enriched.matched_entities["EligibilityHousehold"] == ["1인가구"]
    assert enriched.matched_entities["EligibilityAgeCondition"] == ["19-34"]
    assert "eligibility_rule" in enriched.matched_entities["OperationalEvent"]
    assert "application_deadline" in enriched.matched_entities["OperationalEvent"]


def test_extract_selection_result_fields_requires_result_evidence() -> None:
    fields = extract_selection_result_fields(
        "청년 월세 지원 선정결과 발표 2026.04.20 120명 선정, 총 3억원 집행",
        reference_date=datetime(2026, 4, 12, tzinfo=UTC),
    )

    assert fields["result_date"] == ["2026-04-20"]
    assert fields["selected_count"] == ["120"]
    assert fields["execution_amount"] == ["3억원"]
    assert fields["program_title"] == ["청년 월세 지원"]
    assert extract_selection_result_fields("선정 결과 안내") == {}


def test_enrich_benefit_operational_fields_ignores_phone_number_fragments() -> None:
    article = Article(
        title="서울 물산업 지원사업 신청 안내",
        link="https://example.com/support",
        summary=(
            "문의 02-2133-3831 등록일 2026-04-22 공고마감일자 2026-06-30 "
            "접수기간 : '26.4.22.(수) 09:00 ~ '26.6.30.(화) 17:00 까지"
        ),
        published=datetime(2026, 4, 22, tzinfo=UTC),
        source="서울시",
        category="benefit",
        matched_entities={},
    )

    enriched = enrich_benefit_operational_fields([article])[0]

    assert enriched.matched_entities["ApplicationStartDate"] == ["2026-04-22"]
    assert enriched.matched_entities["ApplicationDeadline"] == ["2026-06-30"]


def test_enrich_benefit_operational_fields_extracts_selection_result() -> None:
    article = Article(
        title="청년 월세 지원 선정결과 발표 2026.04.20 120명 선정",
        link="https://example.com/results/notice-12345",
        summary="최종 선정자 안내",
        published=datetime(2026, 4, 12, tzinfo=UTC),
        source="지자체",
        category="benefit",
        matched_entities={},
    )

    enriched = enrich_benefit_operational_fields([article])[0]

    assert enriched.matched_entities["SelectionResultDate"] == ["2026-04-20"]
    assert enriched.matched_entities["SelectionSelectedCount"] == ["120"]
    assert enriched.matched_entities["SelectionProgramTitle"] == ["청년 월세 지원"]
    assert enriched.matched_entities["OperationalEvent"] == ["selection_result"]


def test_selection_result_fields_extracts_public_outcome_notices() -> None:
    fields = extract_selection_result_fields(
        "[해양수산부]숙박부터 해양레저까지... 우수 해양관광상품 7개 선정 "
        "제10회 우수 해양관광상품 공모전 결과 총 7개의 상품을 선정했다고 밝혔다."
    )

    assert fields["selected_count"] == ["7"]
    assert fields["program_title"] == ["우수 해양관광상품"]


def test_selection_result_fields_uses_summary_when_title_has_no_count() -> None:
    article = Article(
        title="새글 인공지능·사물인터넷 기술,돌봄 현장 실증 거쳐 빠르게 확산한다",
        link="https://example.com/mohw",
        summary=(
            "AX Sprint 사업 스마트홈, 스마트 사회복지시설 분야 협력단 선정 결과를 발표했다. "
            "이번 공모에는 스마트홈 분야 10개 컨소시엄, 스마트 사회복지시설 분야 "
            "8개 컨소시엄이 참여했으며 과업별 평가를 거쳐 각 1개 컨소시엄이 최종 선정됐다."
        ),
        published=datetime(2026, 5, 7, tzinfo=UTC),
        source="보건복지부",
        category="benefit",
        matched_entities={},
    )

    enriched = enrich_benefit_operational_fields([article])[0]

    assert enriched.matched_entities["SelectionSelectedCount"] == ["1"]
    assert enriched.matched_entities["SelectionProgramTitle"] == [article.title]
    assert "selection_result" in enriched.matched_entities["OperationalEvent"]


def test_enrich_benefit_operational_fields_ignores_future_selection_plan() -> None:
    article = Article(
        title="'서울 도심 공공주택 복합사업' 후보지 공모, 주민 제안 44곳 접수",
        link="https://example.com/future-selection",
        summary="후보지선정위원회에서 심사해 오는 7월 중에 최종 선정 결과를 발표할 계획이다.",
        published=datetime(2026, 5, 17, tzinfo=UTC),
        source="정책브리핑",
        category="benefit",
        matched_entities={},
    )

    enriched = enrich_benefit_operational_fields([article])[0]

    assert "SelectionSelectedCount" not in enriched.matched_entities
    assert "selection_result" not in enriched.matched_entities.get("OperationalEvent", [])


def test_selection_program_title_handles_selection_specific_programs() -> None:
    assert (
        extract_selection_program_title(
            "[농림축산식품부]농촌의 다양한 가능성 확인! "
            "농촌창업 경진대회(어메니티 분야) 최종 선정"
        )
        == "농촌창업 경진대회(어메니티 분야)"
    )


def test_extract_application_channels_amounts_and_program_key() -> None:
    text = "정부24 온라인 신청 또는 주민센터 방문신청, 최대 50만원 지원"

    assert extract_application_channels(text) == ["online", "visit"]
    assert extract_benefit_amounts(text) == ["50만원"]
    assert build_program_key(
        program_id="", title="서울 청년 월세 지원 신청", source="서울시 복지"
    ) == ("title:서울시-복지:서울-청년-월세-지원")


def test_enrich_benefit_operational_fields_prefers_embedded_card_text_over_page_summary() -> None:
    article = Article(
        title=(
            "한국인공지능·소프트웨어산업협회\n"
            "2026 청년미래플러스 1회차 참여자 모집(구직청년 및 재직청년)\n"
            "D - 5\n"
            "모집일정 :\n"
            "2026-03-31(화) ~ 2026-04-19(일)\n"
            "지원대상 :\n"
            "만 15세 ~ 34세 이하 대한민국 청년"
        ),
        link="https://job.gg.go.kr/jobSprt/detail.do?seq=2785",
        summary=(
            "다른 카드: AutoCAD 활용 도면작성 입문(2차) 5월 13일(수) 까지 "
            "광주시 거주 청년 만 19세-34세"
        ),
        published=datetime(2026, 4, 14, tzinfo=UTC),
        source="경기도 일자리재단",
        category="benefit",
        matched_entities={},
    )

    enriched = enrich_benefit_operational_fields([article])[0]

    assert enriched.matched_entities["ApplicationStartDate"] == ["2026-03-31"]
    assert enriched.matched_entities["ApplicationDeadline"] == ["2026-04-19"]
    assert enriched.matched_entities["EligibilityAgeCondition"] == ["15-34"]
    assert enriched.matched_entities["BenefitProgramKey"] == [
        "title:경기도-일자리재단:2026-청년미래플러스-1회차-참여자-모집-구직청년-및-재직청년"
    ]
