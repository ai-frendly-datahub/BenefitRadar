from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
