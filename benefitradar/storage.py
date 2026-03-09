from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Iterable, List

import duckdb

from .models import Article


def _utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert tz-aware datetime to UTC naive for DuckDB."""
    if dt is None:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class RadarStorage:
    """DuckDB 기반 경량 스토리지."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._ensure_tables()

    def close(self) -> None:
        self.conn.close()

    def _ensure_tables(self) -> None:
        self.conn.execute(
            """
            CREATE SEQUENCE IF NOT EXISTS articles_id_seq START 1;
            CREATE TABLE IF NOT EXISTS articles (
                id BIGINT PRIMARY KEY DEFAULT nextval('articles_id_seq'),
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL UNIQUE,
                summary TEXT,
                published TIMESTAMP,
                collected_at TIMESTAMP NOT NULL,
                entities_json TEXT
            );
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_articles_category_time ON articles (category, published, collected_at);"
        )

    def upsert_articles(self, articles: Iterable[Article]) -> None:
        """중복 링크는 덮어쓰고 최신 수집 시각을 기록."""
        now = _utc_naive(datetime.now(timezone.utc))
        for article in articles:
            entities_json = json.dumps(article.matched_entities, ensure_ascii=False)
            published = _utc_naive(article.published)

            # 단순화: 같은 링크는 삭제 후 삽입 (DuckDB MERGE 대신 안전한 방식으로)
            self.conn.execute("DELETE FROM articles WHERE link = ?", [article.link])
            self.conn.execute(
                """
                INSERT INTO articles (category, source, title, link, summary, published, collected_at, entities_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    article.category,
                    article.source,
                    article.title,
                    article.link,
                    article.summary,
                    published,
                    now,
                    entities_json,
                ],
            )

    def recent_articles(self, category: str, *, days: int = 7, limit: int = 200) -> List[Article]:
        """최근 N일 내 기사 반환."""
        since = _utc_naive(datetime.now(timezone.utc) - timedelta(days=days))
        cur = self.conn.execute(
            """
            SELECT category, source, title, link, summary, published, collected_at, entities_json
            FROM articles
            WHERE category = ? AND COALESCE(published, collected_at) >= ?
            ORDER BY COALESCE(published, collected_at) DESC
            LIMIT ?
            """,
            [category, since, limit],
        )
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]

        results: List[Article] = []
        for row in rows:
            row_map = dict(zip(columns, row))
            published = row_map.get("published")

            entities = {}
            raw_entities = row_map.get("entities_json")
            if raw_entities:
                try:
                    entities = json.loads(raw_entities)
                except json.JSONDecodeError:
                    entities = {}

            results.append(
                Article(
                    title=row_map.get("title", ""),
                    link=row_map.get("link", ""),
                    summary=row_map.get("summary") or "",
                    published=published,
                    source=row_map.get("source", ""),
                    category=row_map.get("category", ""),
                    matched_entities=entities,
                )
            )
        return results

    def delete_older_than(self, days: int) -> int:
        """보존 기간 밖 데이터 삭제."""
        cutoff = _utc_naive(datetime.now(timezone.utc) - timedelta(days=days))
        count_row = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE COALESCE(published, collected_at) < ?", [cutoff]
        ).fetchone()
        to_delete = count_row[0] if count_row else 0
        self.conn.execute("DELETE FROM articles WHERE COALESCE(published, collected_at) < ?", [cutoff])
        return to_delete
