"""Facade for date-based storage maintenance tasks.

Composes the low-level helpers from ``benefitradar.date_storage`` into a
single ``apply_date_storage_policy()`` call used by ``main.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from benefitradar.date_storage import (
    cleanup_date_directories,
    cleanup_dated_reports,
    snapshot_database,
)


def apply_date_storage_policy(
    *,
    database_path: Path,
    raw_data_dir: Path,
    report_dir: Path,
    keep_raw_days: int = 180,
    keep_report_days: int = 90,
    snapshot_db: bool = False,
) -> dict[str, Any]:
    """Run all date-based storage maintenance in one call.

    Parameters
    ----------
    database_path:
        Path to the DuckDB database file.
    raw_data_dir:
        Root directory containing date-stamped raw-data folders.
    report_dir:
        Directory containing dated HTML report files.
    keep_raw_days:
        Number of days of raw-data directories to retain.
    keep_report_days:
        Number of days of report files to retain.
    snapshot_db:
        If *True*, create a daily snapshot copy of the database.

    Returns
    -------
    dict
        ``{"snapshot_path": str | None}`` — path to the snapshot file
        created (if any).
    """
    snapshot_path: str | None = None

    if snapshot_db:
        result = snapshot_database(database_path)
        if result is not None:
            snapshot_path = str(result)

    cleanup_date_directories(raw_data_dir, keep_days=keep_raw_days)
    cleanup_dated_reports(report_dir, keep_days=keep_report_days)

    return {"snapshot_path": snapshot_path}
