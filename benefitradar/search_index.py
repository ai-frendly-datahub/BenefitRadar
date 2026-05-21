from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import cast

import duckdb

SearchDocument = tuple[str, str, str]
_FALLBACK_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)


@dataclass
class SearchResult:
    link: str
    title: str
    snippet: str
    rank: float


class SearchIndex:
    _db_path: Path
    _conn: sqlite3.Connection | None

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._create_schema()

    def __enter__(self) -> SearchIndex:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise sqlite3.ProgrammingError("SearchIndex connection is closed")
        return self._conn

    def _create_schema(self) -> None:
        conn = self._connection()
        _ = conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                link TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title, body, content='documents', content_rowid='rowid'
            );

            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, title, body)
                VALUES (new.rowid, new.title, new.body);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, body)
                VALUES ('delete', old.rowid, old.title, old.body);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, body)
                VALUES ('delete', old.rowid, old.title, old.body);
                INSERT INTO documents_fts(rowid, title, body)
                VALUES (new.rowid, new.title, new.body);
            END;
            """)
        conn.commit()

    def upsert(self, link: str, title: str, body: str) -> None:
        conn = self._connection()
        _ = conn.execute("DELETE FROM documents WHERE link = ?", (link,))
        _ = conn.execute(
            "INSERT INTO documents(link, title, body) VALUES (?, ?, ?)",
            (link, title, body),
        )
        conn.commit()

    def upsert_many(self, documents: Iterable[SearchDocument]) -> int:
        conn = self._connection()
        count = 0
        for link, title, body in documents:
            clean_link = link.strip()
            clean_title = title.strip()
            if not clean_link or not clean_title:
                continue
            _ = conn.execute("DELETE FROM documents WHERE link = ?", (clean_link,))
            _ = conn.execute(
                "INSERT INTO documents(link, title, body) VALUES (?, ?, ?)",
                (clean_link, clean_title, body),
            )
            count += 1
        conn.commit()
        return count

    def replace_all(self, documents: Iterable[SearchDocument]) -> int:
        conn = self._connection()
        _ = conn.execute("DELETE FROM documents")
        count = self.upsert_many(documents)
        return count

    def count_documents(self) -> int:
        conn = self._connection()
        row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        if row is None:
            return 0
        return int(row[0])

    def search(self, query: str, *, limit: int = 20) -> list[SearchResult]:
        if limit <= 0:
            return []

        try:
            return self._execute_search(query, limit=limit)
        except sqlite3.OperationalError:
            fallback_query = _fallback_match_query(query)
            if not fallback_query:
                return []
            try:
                return self._execute_search(fallback_query, limit=limit)
            except sqlite3.OperationalError:
                return []

    def _execute_search(self, query: str, *, limit: int) -> list[SearchResult]:
        conn = self._connection()
        cursor = conn.execute(
            """
            SELECT
                d.link AS link,
                d.title AS title,
                snippet(documents_fts, 1, '<b>', '</b>', '...', 32) AS snippet,
                bm25(documents_fts) AS rank
            FROM documents_fts
            JOIN documents AS d ON d.rowid = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (query, limit),
        )

        rows = cast(list[tuple[str, str, str, float]], cursor.fetchall())
        results: list[SearchResult] = []
        for row in rows:
            link, title, snippet_text, rank = row
            results.append(
                SearchResult(
                    link=str(link),
                    title=str(title),
                    snippet=str(snippet_text),
                    rank=float(rank),
                )
            )
        return results

    def close(self) -> None:
        if self._conn is None:
            return
        self._conn.close()
        self._conn = None


def sync_search_index_from_connection(
    search_db_path: Path,
    conn: duckdb.DuckDBPyConnection,
    *,
    category: str | None = None,
    limit: int | None = None,
) -> int:
    documents = load_search_documents(conn, category=category, limit=limit)
    with SearchIndex(search_db_path) as index:
        return index.replace_all(documents)


def sync_search_index_from_duckdb(
    search_db_path: Path,
    db_path: Path,
    *,
    category: str | None = None,
    limit: int | None = None,
) -> int:
    if not db_path.exists():
        with SearchIndex(search_db_path) as index:
            return index.replace_all([])

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        return sync_search_index_from_connection(
            search_db_path,
            conn,
            category=category,
            limit=limit,
        )
    finally:
        conn.close()


def load_search_documents(
    conn: duckdb.DuckDBPyConnection,
    *,
    category: str | None = None,
    limit: int | None = None,
) -> list[SearchDocument]:
    if limit is not None and limit <= 0:
        return []
    if not _has_articles_table(conn):
        return []

    filters = ["link IS NOT NULL", "link <> ''", "title IS NOT NULL", "title <> ''"]
    params: list[object] = []
    if category:
        filters.append("category = ?")
        params.append(category)

    limit_clause = ""
    limit_param: list[object] = []
    if limit is not None:
        limit_clause = "LIMIT ?"
        limit_param.append(limit)

    rows = cast(
        list[tuple[object, object, object]],
        conn.execute(
            f"""
            SELECT link, title, COALESCE(summary, '') AS body
            FROM articles
            WHERE {" AND ".join(filters)}
            ORDER BY COALESCE(published, collected_at, TIMESTAMP '1970-01-01') DESC
            {limit_clause}
            """,
            [*params, *limit_param],
        ).fetchall(),
    )
    return [(str(link), str(title), str(body)) for link, title, body in rows]


def _has_articles_table(conn: duckdb.DuckDBPyConnection) -> bool:
    row = conn.execute("""
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = 'articles'
        """).fetchone()
    return bool(row and row[0])


def _fallback_match_query(query: str) -> str:
    terms = _FALLBACK_TOKEN_RE.findall(query)
    if not terms:
        return ""
    quoted_terms = [f'"{term}"' for term in dict.fromkeys(terms[:16])]
    return " OR ".join(quoted_terms)
