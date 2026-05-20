from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from benefitradar.models import Article
from benefitradar.storage import RadarStorage


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_quality.py"
    spec = importlib.util.spec_from_file_location("benefitradar_check_quality_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_quality_artifacts_uses_latest_stored_checkpoint(
    tmp_path: Path,
    capsys,
) -> None:
    project_root = tmp_path
    (project_root / "config" / "categories").mkdir(parents=True)

    (project_root / "config" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "database_path": "data/radar_data.duckdb",
                "report_dir": "reports",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (project_root / "config" / "categories" / "benefit.yaml").write_text(
        yaml.safe_dump(
            {
                "category_name": "benefit",
                "display_name": "Benefit Radar",
                "sources": [
                    {
                        "id": "deadline_feed",
                        "name": "Deadline Feed",
                        "type": "rss",
                        "url": "https://example.com/benefit.xml",
                        "enabled": True,
                        "config": {
                            "event_model": "application_deadline",
                            "freshness_sla_days": 7,
                        },
                    }
                ],
                "entities": [],
                "data_quality": {
                    "quality_outputs": {
                        "tracked_event_models": ["application_deadline"],
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    article_time = datetime.now(UTC) - timedelta(days=30)
    db_path = project_root / "data" / "radar_data.duckdb"
    with RadarStorage(db_path) as storage:
        storage.upsert_articles(
            [
                Article(
                    title="Youth support application deadline",
                    link="https://example.com/deadline",
                    summary="Applications close on April 30, 2026.",
                    published=article_time,
                    collected_at=article_time,
                    source="Deadline Feed",
                    category="benefit",
                    matched_entities={
                        "OperationalEvent": ["application_deadline"],
                        "ApplicationDeadline": ["2026-04-30"],
                        "BenefitProgramKey": ["id:BENE-001"],
                    },
                )
            ]
        )

    module = _load_script_module()
    paths, report = module.generate_quality_artifacts(project_root)

    assert Path(paths["latest"]).exists()
    assert Path(paths["dated"]).exists()
    assert report["summary"]["tracked_sources"] == 1
    assert report["summary"]["application_deadline_events"] == 1
    assert report["summary"]["unique_program_key_count"] == 1

    module.PROJECT_ROOT = project_root
    module.main()
    captured = capsys.readouterr()
    assert "quality_report=" in captured.out
    assert "tracked_sources=1" in captured.out
    assert "unique_program_key_count=1" in captured.out


def test_check_quality_helper_branches(tmp_path: Path) -> None:
    module = _load_script_module()
    project_root = tmp_path

    assert module._project_path(project_root, "data/radar.duckdb") == (
        project_root / "data/radar.duckdb"
    )
    assert module._project_path(project_root, Path("/tmp/radar.duckdb")) == Path(
        "/tmp/radar.duckdb"
    )

    (project_root / "config").mkdir()
    (project_root / "config" / "config.yaml").write_text("- not-a-dict\n", encoding="utf-8")
    assert module._load_runtime_config(project_root) == {}

    aware = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)
    assert module._coerce_date(naive) == date(2026, 5, 21)
    assert module._coerce_date(aware) == date(2026, 5, 21)
    assert module._coerce_date(date(2026, 5, 21)) == date(2026, 5, 21)
    assert module._coerce_date("2026-05-21T12:00:00Z") == date(2026, 5, 21)
    assert module._coerce_date("2026-05-21 extra") == date(2026, 5, 21)
    assert module._coerce_date("not-a-date") is None
    assert module._coerce_date(object()) is None

    assert module._latest_article_date(project_root / "missing.duckdb", "benefit") is None
    assert module._lookback_days(None, minimum_days=10) == 10
    assert module._lookback_days(datetime.now(UTC).date(), minimum_days=7) == 7

    first = Article(
        title="A",
        link="https://example.com/a",
        summary="summary",
        published=None,
        source="source",
        category="benefit",
    )
    duplicate = Article(
        title="Duplicate",
        link="https://example.com/a",
        summary="summary",
        published=None,
        source="source",
        category="benefit",
    )
    fallback = Article(
        title="Fallback",
        link="",
        summary="summary",
        published=None,
        source="source",
        category="benefit",
    )
    assert module._dedupe_articles([first, duplicate, fallback]) == [first, fallback]


def test_latest_article_date_handles_duckdb_errors_and_empty_rows(tmp_path: Path) -> None:
    module = _load_script_module()
    db_path = tmp_path / "radar.duckdb"
    db_path.write_text("not a duckdb", encoding="utf-8")

    assert module._latest_article_date(db_path, "benefit") is None

    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=None)
    connection.execute.return_value.fetchone.return_value = None
    with patch.object(module.duckdb, "connect", return_value=connection):
        assert module._latest_article_date(db_path, "benefit") is None


def test_main_exits_when_database_missing(tmp_path: Path, capsys) -> None:
    module = _load_script_module()
    project_root = tmp_path
    (project_root / "config").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        yaml.safe_dump({"database_path": "data/missing.duckdb"}),
        encoding="utf-8",
    )
    module.PROJECT_ROOT = project_root

    try:
        with patch.object(module.sys, "exit", side_effect=SystemExit(1)) as mock_exit:
            try:
                module.main()
            except SystemExit:
                pass
    finally:
        module.PROJECT_ROOT = Path(__file__).resolve().parents[2]

    assert "Database not found:" in capsys.readouterr().out
    mock_exit.assert_called_once_with(1)
