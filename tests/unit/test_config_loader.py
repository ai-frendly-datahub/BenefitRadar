from __future__ import annotations

import pytest

from benefitradar.config_loader import load_category_config, load_category_quality_config


pytestmark = pytest.mark.unit


def test_load_category_config_preserves_source_operational_config() -> None:
    category = load_category_config("benefit")
    by_name = {source.name: source for source in category.sources}

    assert by_name["보조금24"].config["event_model"] == "eligibility_rule"
    assert by_name["국토교통부"].config["event_model"] == "support_program_notice"
    assert by_name["서울시 복지"].config["event_model"] == "eligibility_rule"
    assert by_name["고용노동부"].enabled is False
    assert "resets connections" in by_name["고용노동부"].config["skip_reason"]
    assert by_name["여성가족부"].enabled is False
    assert by_name["교육부"].enabled is False


def test_load_category_quality_config_exposes_data_quality_contract() -> None:
    quality_config = load_category_quality_config("benefit")
    data_quality = quality_config["data_quality"]
    source_backlog = quality_config["source_backlog"]

    assert data_quality["priority"] == "P1"
    assert data_quality["weakest_dimension"] == "operational_depth"
    assert "support_program_notice" in data_quality["quality_outputs"]["tracked_event_models"]
    assert "application_deadline" in data_quality["quality_outputs"]["tracked_event_models"]
    assert "eligibility_rule" in data_quality["quality_outputs"]["tracked_event_models"]
    assert source_backlog["operational_candidates"]
