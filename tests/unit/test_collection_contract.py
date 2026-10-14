from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
import yaml
from radar_core.config_loader import filter_sources

from benefitradar.collection_contract import (
    BENEFITRADAR_USER_AGENT,
    RSS_TIMEOUT_SECONDS,
    EmptyCollectionError,
    apply_feed_client_contract,
    drop_challenge_pages,
    ensure_collection_not_empty,
)
from benefitradar.collector import collect_sources
from benefitradar.common.validators import is_challenge_or_error_page
from benefitradar.config_loader import load_category_config
from benefitradar.models import Source
from main import run

_FIXTURE = Path("tests/fixtures/challenge_feed.xml").read_bytes()


def test_coverage_config_uses_relative_paths() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    setup_cfg = Path("setup.cfg").read_text(encoding="utf-8")
    assert "relative_files = true" in pyproject
    assert "relative_files = True" in setup_cfg


def test_radar_core_is_pinned_to_main() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    pin = "radar-core @ git+https://github.com/ai-frendly-datahub/radar-core.git@main"
    assert pin in requirements
    assert pin in pyproject


def test_workflow_fails_closed_on_push_and_empty_outputs() -> None:
    workflow = Path(".github/workflows/radar-crawler.yml").read_text(encoding="utf-8")
    auto = workflow.split("- name: Auto-commit data updates", 1)[1].split(
        "- name: Deploy to GitHub Pages", 1
    )[0]
    deploy = workflow.split("- name: Deploy to GitHub Pages", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error" not in auto
    assert "steps.auto-commit.outcome == 'success'" in deploy
    assert "is 0 bytes" in workflow
    assert "100 MiB" in workflow
    assert "--timeout 45" in workflow


def test_session_uses_identifying_user_agent_timeout_and_tls() -> None:
    apply_feed_client_contract()
    recorded: dict[str, object] = {}

    def _get(*_args: object, **kwargs: object) -> Mock:
        recorded.update(kwargs)
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = _FIXTURE
        return response

    source = Source(name="Example News RSS", type="rss", url="https://example.com/feed.xml")
    with patch("benefitradar.collector.requests.Session.get", side_effect=_get):
        articles, errors = collect_sources(
            [source],
            category="benefit",
            timeout=RSS_TIMEOUT_SECONDS,
            max_workers=1,
        )
    articles, challenge_errors = drop_challenge_pages(articles)

    assert errors == []
    assert challenge_errors == []
    assert [article.title for article in articles] == ["BenefitRadar release notes"]
    assert recorded["timeout"] == RSS_TIMEOUT_SECONDS == 45
    assert recorded["verify"] is True
    headers = recorded["headers"]
    assert isinstance(headers, dict)
    assert headers["User-Agent"] == BENEFITRADAR_USER_AGENT
    assert str(headers["User-Agent"]).startswith("BenefitRadar/0.1")
    assert "Mozilla" not in str(headers["User-Agent"])


def test_error_page_titles_are_not_articles() -> None:
    assert is_challenge_or_error_page("404 Not Found", "")
    assert is_challenge_or_error_page("403 Forbidden", "")
    assert not is_challenge_or_error_page("청년 주거 지원금 신청", "소득 기준 안내")


def test_collector_failure_returns_empty_list_without_sample_data() -> None:
    apply_feed_client_contract()
    source = Source(name="down", type="rss", url="https://example.com/feed.xml")
    with patch(
        "benefitradar.collector.requests.Session.get",
        side_effect=requests.exceptions.ConnectionError("down"),
    ):
        articles, errors = collect_sources([source], category="benefit", max_workers=1)

    assert articles == []
    assert errors
    blob = " ".join(errors).lower()
    assert "sample" not in blob
    assert "fake" not in blob


def test_missing_secret_warns_once_and_records_names_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from benefitradar.collection_contract import partition_missing_secrets

    source = Source(
        name="Keyed API",
        type="rss",
        url="https://example.com/feed.xml",
        config={"required_env": ["BENEFITRADAR_TEST_TOKEN", "BENEFITRADAR_TEST_SECRET"]},
    )
    with patch.dict(
        os.environ,
        {
            "BENEFITRADAR_TEST_TOKEN": "",
            "BENEFITRADAR_TEST_SECRET": "",
            "DECOY_SECRET": "super-secret-value",
        },
        clear=False,
    ):
        runnable, errors = partition_missing_secrets([source], raw_dir=tmp_path)

    assert runnable == []
    assert any("skipped_missing_env BENEFITRADAR_TEST_TOKEN" in error for error in errors)
    warning = capsys.readouterr().out
    assert warning.count("::warning title=BenefitRadar missing secret::") == 1
    assert "BENEFITRADAR_TEST_TOKEN" in warning
    assert "BENEFITRADAR_TEST_SECRET" in warning
    assert "super-secret-value" not in warning
    skip_files = list(tmp_path.rglob("*.skip.json"))
    assert len(skip_files) == 1
    text = skip_files[0].read_text(encoding="utf-8")
    assert "BENEFITRADAR_TEST_TOKEN" in text
    assert "BENEFITRADAR_TEST_SECRET" in text
    assert "super-secret-value" not in text
    assert skip_files[0].name == "Keyed API.skip.json"


def test_invalid_required_env_is_not_written(tmp_path: Path) -> None:
    from benefitradar.collection_contract import partition_missing_secrets

    source = Source(
        name="bad-config",
        type="rss",
        url="https://example.com/feed.xml",
        config={"required_env": "actual-secret-value"},
    )
    _runnable, errors = partition_missing_secrets([source], raw_dir=tmp_path)
    assert errors
    assert "actual-secret-value" not in errors[0]
    assert "environment variable name" in errors[0]
    assert list(tmp_path.rglob("*.skip.json")) == []


def test_disabled_sources_are_not_fetched() -> None:
    source = Source(name="Paused", type="rss", url="https://example.com/feed.xml", enabled=False)
    with patch(
        "benefitradar.collector.requests.Session.get", side_effect=AssertionError("fetched")
    ):
        articles, errors = collect_sources([source], category="benefit", max_workers=1)
    assert articles == []
    assert errors == []


def test_filter_sources_keeps_disabled_for_quality_reporting(tmp_path: Path) -> None:
    category_file = tmp_path / "benefit.yaml"
    category_file.write_text(
        yaml.safe_dump(
            {
                "category_name": "benefit",
                "display_name": "Benefit",
                "sources": [
                    {
                        "name": "Paused",
                        "type": "rss",
                        "url": "https://example.com/feed.xml",
                        "enabled": False,
                    }
                ],
                "entities": [],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_category_config("benefit", categories_dir=tmp_path)
    assert [source.name for source in filter_sources(loaded.sources)] == ["Paused"]
    assert loaded.sources[0].enabled is False


def test_ensure_collection_not_empty_rejects_zero_articles() -> None:
    with pytest.raises(EmptyCollectionError):
        ensure_collection_not_empty(collected_count=0, enabled_source_count=2)


def test_ensure_collection_not_empty_allows_items_or_no_enabled_sources() -> None:
    ensure_collection_not_empty(collected_count=1, enabled_source_count=2)
    ensure_collection_not_empty(collected_count=0, enabled_source_count=0)


def test_run_fails_when_active_sources_collect_nothing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    categories_dir = tmp_path / "categories"
    categories_dir.mkdir()
    config_path.write_text(
        yaml.safe_dump(
            {
                "database_path": str(tmp_path / "data" / "radar_data.duckdb"),
                "report_dir": str(tmp_path / "reports"),
                "raw_data_dir": str(tmp_path / "data" / "raw"),
                "search_db_path": str(tmp_path / "data" / "search_index.db"),
            }
        ),
        encoding="utf-8",
    )
    (categories_dir / "test_cat.yaml").write_text(
        yaml.safe_dump(
            {
                "category_name": "test_cat",
                "display_name": "Test",
                "sources": [
                    {"name": "Empty", "type": "rss", "url": "https://example.com/feed.xml"}
                ],
                "entities": [],
            }
        ),
        encoding="utf-8",
    )
    empty_feed = (
        b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>"""
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.content = empty_feed
    with (
        patch("benefitradar.collector.requests.Session.get", return_value=response),
        pytest.raises(EmptyCollectionError),
    ):
        run(
            category="test_cat",
            config_path=config_path,
            categories_dir=categories_dir,
            per_source_limit=5,
            recent_days=7,
            timeout=5,
        )
