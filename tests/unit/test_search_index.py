from __future__ import annotations

import sqlite3
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import duckdb
import yaml

from benefitradar.config_loader import load_settings


class _SearchResult(Protocol):
    link: str
    title: str
    snippet: str
    rank: float


class _SearchIndex(Protocol):
    def upsert(self, link: str, title: str, body: str) -> None: ...

    def upsert_many(self, documents: list[tuple[str, str, str]]) -> int: ...

    def replace_all(self, documents: list[tuple[str, str, str]]) -> int: ...

    def count_documents(self) -> int: ...

    def search(self, query: str, *, limit: int = 20) -> list[_SearchResult]: ...

    def close(self) -> None: ...

    def __enter__(self) -> _SearchIndex: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...


class _SearchIndexCtor(Protocol):
    def __call__(self, db_path: Path) -> _SearchIndex: ...


SearchIndex = cast(_SearchIndexCtor, import_module("radar.search_index").SearchIndex)


def test_index_creation_creates_tables_fts_and_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "search_index.db"

    with SearchIndex(db_path):
        pass

    conn = sqlite3.connect(db_path)
    try:
        rows = cast(
            list[tuple[str, str]],
            conn.execute("SELECT name, type FROM sqlite_master").fetchall(),
        )
    finally:
        conn.close()

    objects = {(name, object_type) for name, object_type in rows}
    assert ("documents", "table") in objects
    assert ("documents_fts", "table") in objects
    assert ("documents_ai", "trigger") in objects
    assert ("documents_ad", "trigger") in objects
    assert ("documents_au", "trigger") in objects


def test_upsert_and_search_returns_matching_results(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    index.upsert(
        link="https://example.com/a",
        title="Bordeaux market update",
        body="The bordeaux wine market has shown strong demand.",
    )

    results = index.search("bordeaux")
    index.close()

    assert len(results) == 1
    assert results[0].link == "https://example.com/a"
    assert results[0].title == "Bordeaux market update"
    assert "<b>bordeaux</b>" in results[0].snippet.lower()
    assert isinstance(results[0].rank, float)


def test_search_returns_empty_list_when_no_match(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    index.upsert(
        link="https://example.com/a",
        title="Coffee market update",
        body="No wine content here.",
    )

    results = index.search("bordeaux")
    index.close()

    assert results == []


def test_search_supports_korean_text(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    index.upsert(
        link="https://example.com/ko",
        title="보르도 와인 뉴스",
        body="보르도 와인 생산량이 증가했습니다.",
    )

    results = index.search("보르도 와인")
    index.close()

    assert len(results) == 1
    assert results[0].link == "https://example.com/ko"


def test_upsert_same_link_twice_updates_document(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    link = "https://example.com/article"

    index.upsert(link=link, title="Old title", body="first version body")
    index.upsert(link=link, title="New title", body="second version body")

    new_results = index.search("second")
    old_results = index.search("first")
    index.close()

    assert len(new_results) == 1
    assert new_results[0].title == "New title"
    assert old_results == []


def test_search_respects_limit_parameter(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    for idx in range(5):
        index.upsert(
            link=f"https://example.com/{idx}",
            title=f"Document {idx}",
            body="bordeaux wine term",
        )

    results = index.search("bordeaux", limit=2)
    index.close()

    assert len(results) == 2


def test_search_returns_empty_for_non_positive_limit(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    index.upsert("https://example.com/a", "Benefit article", "benefit body")

    assert index.search("benefit", limit=0) == []

    index.close()


def test_upsert_many_skips_blank_documents_and_count_is_idempotent(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")

    count = index.upsert_many(
        [
            ("https://example.com/a", "Valid title", "body"),
            ("", "Missing link", "body"),
            ("https://example.com/blank-title", "   ", "body"),
            (" https://example.com/trimmed ", " Trimmed title ", "body"),
        ]
    )

    assert count == 2
    assert index.count_documents() == 2
    assert len(index.search("trimmed")) == 1

    index.close()
    index.close()


def test_replace_all_clears_existing_documents(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    index.upsert("https://example.com/old", "Old benefit", "old body")

    count = index.replace_all([("https://example.com/new", "New benefit", "new body")])

    assert count == 1
    assert index.search("old") == []
    assert len(index.search("new")) == 1
    index.close()


def test_context_manager_supports_open_and_close(tmp_path: Path) -> None:
    db_path = tmp_path / "search_index.db"

    with SearchIndex(db_path) as index:
        index.upsert(
            link="https://example.com/a",
            title="Inside context",
            body="context manager test",
        )
        assert len(index.search("context")) == 1

    try:
        _ = index.search("context")
        raise AssertionError("Expected sqlite3.ProgrammingError after connection close")
    except sqlite3.ProgrammingError:
        pass


def test_search_ranking_places_more_relevant_document_first(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    index.upsert(
        link="https://example.com/high",
        title="Merlot from France",
        body="Merlot from France is a celebrated wine style. Merlot France pairing tips.",
    )
    index.upsert(
        link="https://example.com/low",
        title="Merlot overview",
        body="This article mentions merlot once.",
    )

    results = index.search("merlot OR france", limit=2)
    index.close()

    assert len(results) == 2
    assert results[0].link == "https://example.com/high"
    assert results[0].rank <= results[1].rank


def test_search_invalid_fts_query_falls_back_without_raising(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search_index.db")
    index.upsert(
        link="https://example.com/benefit",
        title="Benefit deadline",
        body="The benefit application deadline is close.",
    )

    fallback_results = index.search("benefit OR")
    empty_results = index.search('"')
    index.close()

    assert len(fallback_results) == 1
    assert fallback_results[0].link == "https://example.com/benefit"
    assert empty_results == []


def test_search_returns_empty_when_primary_and_fallback_queries_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    index = SearchIndex(tmp_path / "search_index.db")

    def always_fail(*args: object, **kwargs: object) -> list[_SearchResult]:
        _ = args, kwargs
        raise sqlite3.OperationalError("forced failure")

    monkeypatch.setattr(index, "_execute_search", always_fail)

    assert index.search("benefit OR") == []

    index.close()


def test_sync_search_index_from_duckdb_rebuilds_missing_index(tmp_path: Path) -> None:
    from benefitradar.search_index import sync_search_index_from_duckdb

    db_path = tmp_path / "radar.duckdb"
    search_db_path = tmp_path / "search_index.db"
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute("""
            CREATE TABLE articles (
                category TEXT,
                source TEXT,
                title TEXT,
                link TEXT,
                summary TEXT,
                published TIMESTAMP,
                collected_at TIMESTAMP
            )
            """)
        _ = conn.execute("""
            INSERT INTO articles
            VALUES ('benefit', 'source', '청년 주거 지원금', 'https://example.com/a',
                    '신청 마감 안내', NULL, CURRENT_TIMESTAMP)
            """)
    finally:
        conn.close()

    indexed = sync_search_index_from_duckdb(search_db_path, db_path, category="benefit")

    with SearchIndex(search_db_path) as index:
        results = index.search("주거")
    assert indexed == 1
    assert len(results) == 1
    assert results[0].link == "https://example.com/a"


def test_sync_search_index_from_missing_duckdb_clears_existing_index(tmp_path: Path) -> None:
    from benefitradar.search_index import sync_search_index_from_duckdb

    missing_db_path = tmp_path / "missing.duckdb"
    search_db_path = tmp_path / "search_index.db"

    with SearchIndex(search_db_path) as index:
        index.upsert("https://example.com/stale", "Stale article", "stale body")

    indexed = sync_search_index_from_duckdb(search_db_path, missing_db_path)

    with SearchIndex(search_db_path) as index:
        assert indexed == 0
        assert index.count_documents() == 0


def test_load_search_documents_handles_missing_table_and_limit(tmp_path: Path) -> None:
    from benefitradar.search_index import load_search_documents

    db_path = tmp_path / "radar.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        assert load_search_documents(conn, limit=0) == []
        assert load_search_documents(conn) == []
    finally:
        conn.close()


def test_load_search_documents_filters_category_and_limit(tmp_path: Path) -> None:
    from benefitradar.search_index import load_search_documents

    db_path = tmp_path / "radar.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute("""
            CREATE TABLE articles (
                category TEXT,
                source TEXT,
                title TEXT,
                link TEXT,
                summary TEXT,
                published TIMESTAMP,
                collected_at TIMESTAMP
            )
            """)
        _ = conn.execute("""
            INSERT INTO articles VALUES
            ('benefit', 'source', '첫 번째 지원금', 'https://example.com/1', NULL, NULL, CURRENT_TIMESTAMP),
            ('benefit', 'source', '두 번째 지원금', 'https://example.com/2', 'summary', NULL, CURRENT_TIMESTAMP),
            ('policy', 'source', '정책 기사', 'https://example.com/policy', 'summary', NULL, CURRENT_TIMESTAMP),
            ('benefit', 'source', '', 'https://example.com/blank-title', 'summary', NULL, CURRENT_TIMESTAMP),
            ('benefit', 'source', '링크 없음', '', 'summary', NULL, CURRENT_TIMESTAMP)
            """)

        documents = load_search_documents(conn, category="benefit", limit=1)
    finally:
        conn.close()

    assert len(documents) == 1
    assert documents[0][0].startswith("https://example.com/")
    assert documents[0][2] in {"", "summary"}


def test_load_settings_reads_search_db_path_and_default() -> None:
    settings = load_settings()
    project_root = Path(__file__).resolve().parents[2]

    assert settings.search_db_path == (project_root / "data" / "search_index.db").resolve()


def test_load_settings_reads_custom_search_db_path(tmp_path: Path) -> None:
    custom_path = (tmp_path / "custom_search.db").resolve()
    config_path = tmp_path / "config.yaml"
    _ = config_path.write_text(
        yaml.safe_dump(
            {
                "database_path": "data/radar_data.duckdb",
                "report_dir": "reports",
                "raw_data_dir": "data/raw",
                "search_db_path": str(custom_path),
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.search_db_path == custom_path
