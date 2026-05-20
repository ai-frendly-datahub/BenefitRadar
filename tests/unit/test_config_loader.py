from __future__ import annotations

from pathlib import Path

import pytest

from benefitradar import config_loader
from benefitradar.config_loader import (
    _bool_value,
    _dict_items,
    _dict_value,
    _float_value,
    _parse_entity,
    _parse_source,
    _read_yaml_dict,
    _resolve_env_refs,
    _resolve_path,
    _string_list_value,
    _string_value,
    load_category_config,
    load_category_quality_config,
    load_notification_config,
    load_settings,
)

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


def test_loader_scalar_helpers_and_yaml_fallbacks(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    yaml_file = tmp_path / "not_dict.yaml"
    yaml_file.write_text("- item\n", encoding="utf-8")

    raw = {
        "name": " value ",
        "empty": "",
        "true": "yes",
        "false": "0",
        "bad_bool": "maybe",
        "float": "1.5",
        "bad_float": "nope",
        "list": [" a ", "", 2],
        "tuple": ("x", "y"),
        "config": {"x": "${TEST_CONFIG_VALUE}"},
    }

    assert (
        _resolve_path("data/file.db", project_root=project_root)
        == (project_root / "data/file.db").resolve()
    )
    assert _resolve_path(str(tmp_path / "absolute.db"), project_root=project_root) == (
        tmp_path / "absolute.db"
    )
    assert _read_yaml_dict(yaml_file) == {}
    assert _string_value(raw, "name", "default") == " value "
    assert _string_value(raw, "empty", "default") == "default"
    assert _bool_value(raw, "true", False) is True
    assert _bool_value(raw, "false", True) is False
    assert _bool_value(raw, "bad_bool", True) is True
    assert _float_value(raw, "float", 0.0) == 1.5
    assert _float_value(raw, "bad_float", 2.0) == 2.0
    assert _dict_items([{"a": 1}, "skip", {2: "b"}]) == [{"a": 1}, {"2": "b"}]
    assert _dict_items("not-list") == []
    assert _string_list_value(raw, "list") == ["a", "2"]
    assert _string_list_value(raw, "tuple") == ["x", "y"]
    assert _string_list_value({"single": "one"}, "single") == ["one"]


def test_env_resolution_and_parse_source_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_CONFIG_VALUE", "resolved")
    source = _parse_source(
        {
            "name": "${TEST_CONFIG_VALUE} Source",
            "type": "api",
            "url": "https://example.com",
            "id": "source-id",
            "enabled": "false",
            "language": "ko",
            "country": "KR",
            "region": "Seoul",
            "trust_tier": "T1",
            "weight": "2.5",
            "content_type": "notice",
            "collection_tier": "C2_api",
            "producer_role": "official",
            "info_purpose": {"unexpected": "mapping"},
            "notes": "note",
            "config": {"token": "${TEST_CONFIG_VALUE}"},
        }
    )
    default_source = _parse_source({"name": "Minimal"})
    entity = _parse_entity(
        {
            "name": "SubsidyProgram",
            "display_name": "지원사업",
            "keywords": (" 지원금 ", "", 123),
        }
    )

    assert _resolve_env_refs(["${TEST_CONFIG_VALUE}", {"x": "${MISSING_ENV}"}]) == [
        "resolved",
        {"x": ""},
    ]
    assert _dict_value({"config": {"token": "${TEST_CONFIG_VALUE}"}}, "config") == {
        "token": "resolved"
    }
    assert _dict_value({"config": "not-dict"}, "config") == {}
    assert source.name == "resolved Source"
    assert source.enabled is False
    assert source.weight == 2.5
    assert source.info_purpose == []
    assert source.config == {"token": "resolved"}
    assert default_source.type == "rss"
    assert default_source.enabled is True
    assert entity.keywords == ["지원금", "123"]

    with pytest.raises(ValueError, match="Empty source"):
        _parse_source({})
    with pytest.raises(ValueError, match="Empty entity"):
        _parse_entity({})


def test_load_settings_category_and_notifications_from_custom_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example")
    settings_file = tmp_path / "config.yaml"
    settings_file.write_text(
        """
database_path: data/custom.duckdb
report_dir: /tmp/benefitradar-reports
raw_data_dir: raw
search_db_path: search/custom.db
""".strip(),
        encoding="utf-8",
    )
    categories_dir = tmp_path / "categories"
    categories_dir.mkdir()
    (categories_dir / "custom.yaml").write_text(
        """
category_name: custom
sources:
  - name: Custom Source
    url: https://example.com/feed
entities:
  - name: Program
    keywords: [grant]
data_quality:
  priority: P2
integration_candidates:
  - name: api
""".strip(),
        encoding="utf-8",
    )
    notifications_file = tmp_path / "notifications.yaml"
    notifications_file.write_text(
        """
notifications:
  enabled: true
  channels: [email, webhook, telegram, 3]
  webhook_url: "${WEBHOOK_URL}"
  email:
    smtp_host: smtp.example.com
    smtp_port: "2525"
    username: "${MISSING_USER}"
    password: secret
    from_address: from@example.com
    to_addresses: [to@example.com, 1]
  telegram:
    bot_token: token
    chat_id: chat
  rules:
    deadline_days: "${MISSING_DEADLINE}"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(settings_file)
    category = load_category_config("custom", categories_dir=categories_dir)
    quality = load_category_quality_config("custom", categories_dir=categories_dir)
    notifications = load_notification_config(notifications_file)

    assert settings.database_path == (Path.cwd() / "data/custom.duckdb").resolve()
    assert settings.report_dir == Path("/tmp/benefitradar-reports")
    assert settings.raw_data_dir == (Path.cwd() / "raw").resolve()
    assert category.display_name == "custom"
    assert category.sources[0].name == "Custom Source"
    assert category.entities[0].keywords == ["grant"]
    assert quality["data_quality"]["priority"] == "P2"
    assert quality["integration_candidates"][0]["name"] == "api"
    assert notifications.enabled is True
    assert notifications.channels == ["email", "webhook", "telegram"]
    assert notifications.webhook_url == "https://hooks.example"
    assert notifications.email is not None
    assert notifications.email.smtp_port == 2525
    assert notifications.email.username == ""
    assert notifications.email.to_addresses == ["to@example.com"]
    assert notifications.telegram is not None
    assert notifications.telegram.bot_token == "token"
    assert notifications.rules == {"deadline_days": ""}


def test_loader_missing_files_and_invalid_notification_sections(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing.yaml"
    notifications_file = tmp_path / "notifications.yaml"
    invalid_email_file = tmp_path / "invalid-email.yaml"

    notifications_file.write_text("notifications: disabled", encoding="utf-8")
    invalid_email_file.write_text(
        """
notifications:
  enabled: true
  channels: [email]
  email:
    smtp_host: smtp.example.com
    smtp_port: not-a-number
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        load_settings(missing_config)
    with pytest.raises(FileNotFoundError):
        load_category_config("missing", categories_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        load_category_quality_config("missing", categories_dir=tmp_path)

    assert load_notification_config(tmp_path / "absent.yaml").enabled is False
    assert load_notification_config(notifications_file).enabled is False

    invalid_email = load_notification_config(invalid_email_file)
    assert invalid_email.email is None


def test_default_project_root_resolution_is_stable() -> None:
    # Guards the local project_root convention used when no config path is supplied.
    assert config_loader.Path(__file__).name == "test_config_loader.py"
