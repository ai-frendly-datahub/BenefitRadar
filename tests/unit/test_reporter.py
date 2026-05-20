from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from benefitradar.models import Article, CategoryConfig
from benefitradar.reporter import generate_report

pytestmark = pytest.mark.unit


def test_generate_report_injects_benefit_quality_panel_into_latest_and_dated_report(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "benefit_report.html"
    quality_report = {
        "operational_depth_note": "Deadline and eligibility signals are separate.",
        "summary": {
            "fresh_sources": 1,
            "stale_sources": 1,
            "missing_sources": 1,
            "missing_event_sources": 1,
            "application_deadline_events": 1,
            "eligibility_rule_events": 1,
            "selection_result_events": 1,
            "unique_program_key_count": 2,
        },
        "sources": [
            {
                "source": "Eligibility Source",
                "status": "stale",
                "event_model": "eligibility_rule",
                "age_days": 5,
            }
        ],
        "events": [
            {
                "source": "Deadline Source",
                "event_model": "application_deadline",
                "title": "청년 지원 신청 마감",
                "application_deadline": "2026-04-30",
                "program_id": "BENE1",
                "program_key": "id:BENE1",
                "application_channels": ["online"],
                "benefit_amounts": ["20만원"],
            },
            {
                "source": "Selection Source",
                "event_model": "selection_result",
                "title": "청년 월세 지원 선정결과 발표",
                "selection_result_date": "2026-04-20",
                "selected_count": "120",
                "execution_amount": "3억원",
                "program_key": "title:selection-source:청년-월세-지원",
            },
        ],
    }

    result = generate_report(
        category=CategoryConfig(
            category_name="benefit",
            display_name="Benefit Radar",
            sources=[],
            entities=[],
        ),
        articles=[
            Article(
                title="청년 지원 신청 마감",
                link="https://example.com/benefit",
                summary="신청 마감 2026-04-30",
                published=datetime(2026, 4, 12, tzinfo=UTC),
                source="Deadline Source",
                category="benefit",
                matched_entities={"OperationalEvent": ["application_deadline"]},
            )
        ],
        output_path=output_path,
        stats={"article_count": 1, "source_count": 1, "matched_count": 1},
        quality_report=quality_report,
    )

    assert result == output_path
    latest_html = output_path.read_text(encoding="utf-8")
    assert 'id="benefit-quality"' in latest_html
    assert "Benefit Quality" in latest_html
    assert "benefit_quality.json" in latest_html
    assert "Deadline Source" in latest_html
    assert "2026-04-30" in latest_html
    assert "program keys" in latest_html
    assert "selected 120" in latest_html

    dated_report = next(
        path for path in tmp_path.glob("benefit_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].html")
    )
    dated_html = dated_report.read_text(encoding="utf-8")
    assert 'id="benefit-quality"' in dated_html
    assert "Eligibility Source" in dated_html

    summaries = sorted(
        tmp_path.glob("benefit_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_summary.json")
    )
    assert len(summaries) == 1
    summary = summaries[0].read_text(encoding="utf-8")
    assert '"repo": "BenefitRadar"' in summary
    assert '"ontology_version": "0.1.0"' in summary
    assert '"govsupport.application_deadline"' in summary
