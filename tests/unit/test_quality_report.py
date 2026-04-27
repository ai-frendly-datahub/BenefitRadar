from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from benefitradar.models import Article, CategoryConfig, Source
from benefitradar.quality_report import build_quality_report, write_quality_report


pytestmark = pytest.mark.unit


def _source(
    name: str,
    event_model: str,
    sla_days: int | None = None,
    *,
    quiet_when_no_items: bool = False,
) -> Source:
    config: dict[str, object] = {"event_model": event_model}
    if sla_days is not None:
        config["freshness_sla_days"] = sla_days
    if quiet_when_no_items:
        config["quiet_when_no_items"] = True
    return Source(name=name, type="rss", url=f"https://example.com/{name}", config=config)


def test_build_quality_report_tracks_benefit_operational_statuses() -> None:
    now = datetime(2026, 4, 12, tzinfo=UTC)
    category = CategoryConfig(
        category_name="benefit",
        display_name="Benefit",
        sources=[
            _source("Deadline Source", "application_deadline", 1),
            _source("Eligibility Source", "eligibility_rule", 2),
            _source("No Extracted Event", "eligibility_rule", 2),
            _source("Missing Source", "application_deadline", 1),
            _source("Selection Source", "selection_result", 7),
            _source("News Source", "support_program_notice", 3),
        ],
        entities=[],
    )
    articles = [
        Article(
            title="Deadline benefit notice",
            link="https://example.com/deadline",
            summary="Apply by 2026-04-30",
            published=now - timedelta(days=1),
            collected_at=now,
            source="Deadline Source",
            category="benefit",
            matched_entities={
                "OperationalEvent": ["application_deadline"],
                "ApplicationDeadline": ["2026-04-30"],
                "BenefitProgramId": ["BENE1"],
                "BenefitProgramKey": ["id:BENE1"],
                "ApplicationChannel": ["online"],
                "BenefitAmount": ["20만원"],
            },
        ),
        Article(
            title="Eligibility benefit notice",
            link="https://example.com/eligibility",
            summary="서울 청년 대상",
            published=now - timedelta(days=5),
            collected_at=now,
            source="Eligibility Source",
            category="benefit",
            matched_entities={
                "OperationalEvent": ["eligibility_rule"],
                "BenefitProgramKey": ["title:eligibility-source:eligibility-benefit-notice"],
                "EligibilityRegion": ["서울"],
                "EligibilityTargetGroup": ["청년"],
            },
        ),
        Article(
            title="Unparsed eligibility notice",
            link="https://example.com/missing-event",
            summary="공고는 있으나 운영 이벤트가 없음",
            published=now,
            collected_at=now,
            source="No Extracted Event",
            category="benefit",
            matched_entities={},
        ),
        Article(
            title="Selection result",
            link="https://example.com/result",
            summary="선정자 발표",
            published=now - timedelta(days=2),
            collected_at=now,
            source="Selection Source",
            category="benefit",
            matched_entities={
                "OperationalEvent": ["selection_result"],
                "BenefitProgramKey": ["title:selection-source:selection-result"],
                "SelectionResultDate": ["2026-04-10"],
                "SelectionSelectedCount": ["120"],
                "SelectionExecutionAmount": ["3억원"],
                "SelectionProgramTitle": ["청년 월세 지원"],
            },
        ),
        Article(
            title="General policy notice",
            link="https://example.com/news",
            summary="복지 정책 안내",
            published=now,
            collected_at=now,
            source="News Source",
            category="benefit",
            matched_entities={},
        ),
    ]

    report = build_quality_report(
        category=category,
        articles=articles,
        errors=["Deadline Source: timeout after retry"],
        quality_config={
            "data_quality": {
                "quality_outputs": {
                    "tracked_event_models": [
                        "support_program_notice",
                        "application_deadline",
                        "eligibility_rule",
                        "selection_result",
                    ]
                }
            },
            "source_backlog": {"operational_candidates": [{"id": "bokjiro_detail"}]},
        },
        generated_at=now,
    )

    assert report["summary"]["fresh_sources"] == 3
    assert report["summary"]["stale_sources"] == 1
    assert report["summary"]["missing_event_sources"] == 1
    assert report["summary"]["missing_sources"] == 1
    assert report["summary"]["not_tracked_sources"] == 0
    assert report["summary"]["support_program_notice_events"] == 1
    assert report["summary"]["application_deadline_events"] == 1
    assert report["summary"]["eligibility_rule_events"] == 1
    assert report["summary"]["selection_result_events"] == 1
    assert report["summary"]["unique_program_key_count"] == 3
    assert report["summary"]["deadline_with_program_key_events"] == 1
    assert report["source_backlog"] == {"operational_candidates": [{"id": "bokjiro_detail"}]}

    rows = {row["source"]: row for row in report["sources"]}
    assert rows["Deadline Source"]["status"] == "fresh"
    assert rows["Deadline Source"]["latest_application_deadline"] == "2026-04-30"
    assert rows["Deadline Source"]["latest_program_id"] == "BENE1"
    assert rows["Deadline Source"]["latest_program_key"] == "id:BENE1"
    assert rows["Deadline Source"]["latest_application_channels"] == ["online"]
    assert rows["Deadline Source"]["latest_benefit_amounts"] == ["20만원"]
    assert rows["Deadline Source"]["errors"] == ["Deadline Source: timeout after retry"]
    assert rows["Eligibility Source"]["status"] == "stale"
    assert rows["Eligibility Source"]["latest_eligibility_fields"]["Region"] == ["서울"]
    assert rows["Selection Source"]["latest_selection_result_date"] == "2026-04-10"
    assert rows["Selection Source"]["latest_selected_count"] == "120"
    assert rows["Selection Source"]["latest_execution_amount"] == "3억원"


def test_disabled_benefit_source_is_skipped_not_active_tracked() -> None:
    now = datetime(2026, 4, 12, tzinfo=UTC)
    source = _source("Credential Source", "eligibility_rule", 3)
    source.enabled = False
    source.config["skip_reason"] = "Missing API key."
    source.config["reenable_gate"] = "Configure API key and contract test."
    category = CategoryConfig(
        category_name="benefit",
        display_name="Benefit",
        sources=[source],
        entities=[],
    )

    report = build_quality_report(
        category=category,
        articles=[
            Article(
                title="Cached eligibility notice",
                link="https://example.com/eligibility",
                summary="지원 대상 안내",
                published=now,
                collected_at=now,
                source="Credential Source",
                category="benefit",
                matched_entities={"OperationalEvent": ["eligibility_rule"]},
            )
        ],
        quality_config={
            "data_quality": {
                "quality_outputs": {"tracked_event_models": ["eligibility_rule"]}
            }
        },
        generated_at=now,
    )

    row = report["sources"][0]
    assert row["tracked"] is False
    assert row["status"] == "skipped_disabled"
    assert row["skip_reason"] == "Missing API key."
    assert report["summary"]["tracked_sources"] == 0
    assert report["summary"]["skipped_disabled_sources"] == 1
    assert report["summary"]["eligibility_rule_events"] == 0


def test_benefit_quality_report_marks_quiet_sources_when_configured() -> None:
    now = datetime(2026, 4, 12, tzinfo=UTC)
    category = CategoryConfig(
        category_name="benefit",
        display_name="Benefit",
        sources=[_source("Quiet Source", "support_program_notice", 3, quiet_when_no_items=True)],
        entities=[],
    )

    report = build_quality_report(
        category=category,
        articles=[],
        quality_config={
            "data_quality": {
                "quality_outputs": {"tracked_event_models": ["support_program_notice"]}
            }
        },
        generated_at=now,
    )

    row = report["sources"][0]
    assert row["status"] == "quiet"
    assert report["summary"]["quiet_sources"] == 1
    assert report["summary"]["missing_sources"] == 0


def test_write_quality_report_writes_latest_and_dated_files(tmp_path) -> None:
    report = {
        "category": "benefit",
        "generated_at": "2026-04-12T03:04:05+00:00",
        "summary": {},
        "sources": [],
        "events": [],
        "errors": [],
    }

    paths = write_quality_report(report, output_dir=tmp_path, category_name="benefit")

    assert paths["latest"] == tmp_path / "benefit_quality.json"
    assert paths["dated"] == tmp_path / "benefit_20260412_quality.json"
    assert json.loads(paths["latest"].read_text(encoding="utf-8")) == report
    assert json.loads(paths["dated"].read_text(encoding="utf-8")) == report
