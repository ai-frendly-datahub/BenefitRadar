from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from benefitradar.common import quality_checks

pytestmark = pytest.mark.unit


def _records_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    _ = con.execute("""
        CREATE TABLE records (
            url TEXT,
            title TEXT,
            language TEXT,
            published_at TIMESTAMP
        )
        """)
    _ = con.executemany(
        "INSERT INTO records VALUES (?, ?, ?, ?)",
        [
            ("https://example.com/a", "청년 지원금", "ko", datetime.now(UTC) - timedelta(days=1)),
            ("https://example.com/a", None, "jp", datetime.now(UTC) + timedelta(days=1)),
            ("https://example.com/b", "의료비 지원", None, datetime.now(UTC)),
        ],
    )
    return con


def test_quality_check_conversion_helpers_and_identifier_quoting() -> None:
    assert quality_checks._quote_identifier('a"b') == '"a""b"'
    assert quality_checks._to_int(True) == 1
    assert quality_checks._to_int(3) == 3
    assert quality_checks._to_int(3.0) == 3
    assert quality_checks._to_int(b"4") == 4
    assert quality_checks._to_optional_int(None) is None
    assert quality_checks._to_optional_int("5") == 5
    assert quality_checks._to_optional_float(None) is None
    assert quality_checks._to_optional_float(True) == 1.0
    assert quality_checks._to_optional_float("2.5") == 2.5

    with pytest.raises(TypeError, match="int-compatible"):
        quality_checks._to_int(object())
    with pytest.raises(TypeError, match="float-compatible"):
        quality_checks._to_optional_float(object())


def test_fetchone_required_and_column_exists() -> None:
    con = _records_connection()
    try:
        assert quality_checks._fetchone_required(con, "SELECT COUNT(*) FROM records")[0] == 3
        assert quality_checks._column_exists(con, table_name="records", column_name="language")
        assert not quality_checks._column_exists(con, table_name="records", column_name="missing")
        with pytest.raises(RuntimeError, match="returned no rows"):
            quality_checks._fetchone_required(con, "SELECT 1 WHERE FALSE")
    finally:
        con.close()


def test_run_all_checks_reports_findings_for_present_columns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    con = _records_connection()
    try:
        quality_checks.run_all_checks(
            con,
            table_name="records",
            null_conditions={"missing_title": "title IS NULL"},
            text_columns=["title"],
            allowed_languages={"ko"},
            url_column="url",
            date_column="published_at",
        )
        output = capsys.readouterr().out
    finally:
        con.close()

    assert "Total records: 3" in output
    assert "missing_title: 1 / 3 (33.3%)" in output
    assert "2x: https://example.com/a" in output
    assert "title: avg/min/max" in output
    assert "Invalid language values:" in output
    assert "jp: 1" in output
    assert "future dates: 1" in output


def test_checks_handle_empty_tables_and_missing_optional_columns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    con = duckdb.connect(":memory:")
    try:
        _ = con.execute("CREATE TABLE minimal (url TEXT, title TEXT)")
        quality_checks.run_all_checks(
            con,
            table_name="minimal",
            null_conditions={"missing_title": "title IS NULL"},
            text_columns=["title"],
            language_column="language",
            date_column="published_at",
        )
        output = capsys.readouterr().out
    finally:
        con.close()

    assert "Total records: 0" in output
    assert "No records found." in output
    assert "No duplicate URLs found." in output
    assert "title: avg/min/max = N/A / None / None" in output
    assert "Skipping language check: missing column 'language'" in output
    assert "Skipping date check: missing column 'published_at'" in output


def test_language_check_reports_empty_allowed_and_invalid_branches(
    capsys: pytest.CaptureFixture[str],
) -> None:
    con = duckdb.connect(":memory:")
    try:
        _ = con.execute("CREATE TABLE empty_language (language TEXT)")
        quality_checks.check_language_values(con, table_name="empty_language")
        empty_output = capsys.readouterr().out

        _ = con.execute("CREATE TABLE languages (language TEXT)")
        _ = con.executemany("INSERT INTO languages VALUES (?)", [("ko",), ("en",)])
        quality_checks.check_language_values(
            con,
            table_name="languages",
            allowed_languages={"ko", "en"},
        )
        allowed_output = capsys.readouterr().out

        quality_checks.check_language_values(con, table_name="languages")
        distribution_output = capsys.readouterr().out
    finally:
        con.close()

    assert "No language values found." in empty_output
    assert "All language values are allowed." in allowed_output
    assert "Distribution:" in distribution_output
