from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from benefitradar.search_index import SearchIndex


def _init_articles_table(db_path: Path) -> None:
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute("""
            CREATE TABLE articles (
                id BIGINT PRIMARY KEY,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL UNIQUE,
                summary TEXT,
                published TIMESTAMP,
                collected_at TIMESTAMP NOT NULL,
                entities_json TEXT
            )
            """)
    finally:
        conn.close()


def _seed_article(
    *,
    db_path: Path,
    article_id: int,
    title: str,
    link: str,
    collected_at: datetime,
    entities: dict[str, list[str]] | None = None,
) -> None:
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute(
            """
            INSERT INTO articles (id, category, source, title, link, summary, published, collected_at, entities_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                article_id,
                "coffee",
                "Test Source",
                title,
                link,
                "summary",
                None,
                collected_at,
                json.dumps(entities or {}, ensure_ascii=False),
            ],
        )
    finally:
        conn.close()


def _seed_quality_article(
    *,
    db_path: Path,
    article_id: int,
    title: str,
    link: str,
    source: str,
    collected_at: datetime,
    entities: dict[str, list[str]],
) -> None:
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute(
            """
            INSERT INTO articles (id, category, source, title, link, summary, published, collected_at, entities_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                article_id,
                "benefit",
                source,
                title,
                link,
                "summary",
                collected_at,
                collected_at,
                json.dumps(entities, ensure_ascii=False),
            ],
        )
    finally:
        conn.close()


def _write_quality_category_config(categories_dir: Path) -> None:
    categories_dir.mkdir(parents=True, exist_ok=True)
    (categories_dir / "benefit.yaml").write_text(
        """
category_name: benefit
display_name: Benefit Radar
data_quality:
  quality_outputs:
    tracked_event_models: [application_deadline, eligibility_rule]
  freshness_sla:
    application_deadline:
      max_age_days: 1
    eligibility_rule:
      max_age_days: 3
source_backlog:
  operational_candidates:
    - id: bokjiro_detail
      name: Bokjiro detail
sources:
  - name: Deadline Source
    type: rss
    url: https://example.com/deadline
    config:
      event_model: application_deadline
      freshness_sla_days: 1
  - name: Eligibility Source
    type: api
    url: https://example.com/eligibility
    config:
      event_model: eligibility_rule
      freshness_sla_days: 3
entities: []
""",
        encoding="utf-8",
    )


def test_handle_search(tmp_path: Path) -> None:
    from mcp_server.tools import handle_search

    db_path = tmp_path / "radar.duckdb"
    search_db_path = tmp_path / "search.db"
    _init_articles_table(db_path)

    now = datetime.now(UTC)
    recent_link = "https://example.com/recent"
    old_link = "https://example.com/old"

    _seed_article(
        db_path=db_path,
        article_id=1,
        title="Recent coffee demand",
        link=recent_link,
        collected_at=now - timedelta(days=2),
    )
    _seed_article(
        db_path=db_path,
        article_id=2,
        title="Old coffee demand",
        link=old_link,
        collected_at=now - timedelta(days=20),
    )

    with SearchIndex(search_db_path) as idx:
        idx.upsert(recent_link, "Recent coffee demand", "Demand is rising")
        idx.upsert(old_link, "Old coffee demand", "Demand was low")

    output = handle_search(
        search_db_path=search_db_path,
        db_path=db_path,
        query="last 7 days coffee",
        limit=10,
    )

    assert "Recent coffee demand" in output
    assert "Old coffee demand" not in output


def test_handle_search_empty_query_and_non_positive_limit(tmp_path: Path) -> None:
    from mcp_server.tools import handle_search

    db_path = tmp_path / "radar.duckdb"
    search_db_path = tmp_path / "search.db"
    _init_articles_table(db_path)

    assert (
        handle_search(search_db_path=search_db_path, db_path=db_path, query="   ", limit=10)
        == "No results found."
    )
    assert (
        handle_search(search_db_path=search_db_path, db_path=db_path, query="coffee", limit=0)
        == "No results found."
    )


def test_handle_search_backfills_empty_index_from_duckdb(tmp_path: Path) -> None:
    from mcp_server.tools import handle_search

    db_path = tmp_path / "radar.duckdb"
    search_db_path = tmp_path / "search.db"
    _init_articles_table(db_path)
    _seed_article(
        db_path=db_path,
        article_id=1,
        title="청년 주거 지원금",
        link="https://example.com/youth-housing",
        collected_at=datetime.now(UTC),
    )

    output = handle_search(
        search_db_path=search_db_path,
        db_path=db_path,
        query="주거",
        limit=10,
    )

    assert "청년 주거 지원금" in output


def test_handle_search_invalid_fts_query_returns_no_results(tmp_path: Path) -> None:
    from mcp_server.tools import handle_search

    db_path = tmp_path / "radar.duckdb"
    search_db_path = tmp_path / "search.db"
    _init_articles_table(db_path)

    output = handle_search(
        search_db_path=search_db_path,
        db_path=db_path,
        query='"',
        limit=10,
    )

    assert output == "No results found."


def test_handle_recent_updates(tmp_path: Path) -> None:
    from mcp_server.tools import handle_recent_updates

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)
    now = datetime.now(UTC)

    _seed_article(
        db_path=db_path,
        article_id=1,
        title="Most recent",
        link="https://example.com/1",
        collected_at=now - timedelta(hours=1),
    )
    _seed_article(
        db_path=db_path,
        article_id=2,
        title="Older",
        link="https://example.com/2",
        collected_at=now - timedelta(days=2),
    )

    output = handle_recent_updates(db_path=db_path, days=1, limit=10)

    assert "Most recent" in output
    assert "Older" not in output


def test_handle_recent_updates_empty_and_non_positive_limit(tmp_path: Path) -> None:
    from mcp_server.tools import handle_recent_updates

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)

    assert handle_recent_updates(db_path=db_path, limit=0) == "No recent updates found."
    assert handle_recent_updates(db_path=db_path, days=7, limit=10) == "No recent updates found."


def test_handle_sql_select(tmp_path: Path) -> None:
    from mcp_server.tools import handle_sql

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)

    output = handle_sql(db_path=db_path, query="SELECT COUNT(*) AS total FROM articles")

    assert "total" in output
    assert "0" in output


def test_handle_sql_empty_rows_and_database_errors(tmp_path: Path) -> None:
    from mcp_server.tools import handle_sql

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)

    assert handle_sql(db_path=db_path, query="SELECT title FROM articles WHERE 1=0") == (
        "No rows returned."
    )
    assert "Error:" in handle_sql(db_path=db_path, query="SELECT missing_column FROM articles")


def test_handle_sql_blocked(tmp_path: Path) -> None:
    from mcp_server.tools import handle_sql

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)

    output = handle_sql(db_path=db_path, query="DROP TABLE articles")

    assert "Only SELECT/WITH/EXPLAIN queries are allowed" in output


def test_handle_top_trends(tmp_path: Path) -> None:
    from mcp_server.tools import handle_top_trends

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)
    now = datetime.now(UTC)

    _seed_article(
        db_path=db_path,
        article_id=1,
        title="a",
        link="https://example.com/a",
        collected_at=now - timedelta(days=1),
        entities={"Region": ["ethiopia", "kenya"], "Roaster": ["blue bottle"]},
    )
    _seed_article(
        db_path=db_path,
        article_id=2,
        title="b",
        link="https://example.com/b",
        collected_at=now - timedelta(days=1),
        entities={"Region": ["brazil"]},
    )

    output = handle_top_trends(db_path=db_path, days=7, limit=10)

    assert "Region" in output
    assert "3" in output
    assert "Roaster" in output
    assert "1" in output


def test_handle_top_trends_empty_invalid_json_and_non_list_entities(tmp_path: Path) -> None:
    from mcp_server.tools import handle_top_trends

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)
    now = datetime.now(UTC)
    _seed_article(
        db_path=db_path,
        article_id=1,
        title="bad json",
        link="https://example.com/bad-json",
        collected_at=now,
    )
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute(
            "UPDATE articles SET entities_json = ? WHERE link = ?",
            ["{not-json", "https://example.com/bad-json"],
        )
        _ = conn.execute(
            """
            INSERT INTO articles (id, category, source, title, link, summary, published, collected_at, entities_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                2,
                "coffee",
                "Test Source",
                "non-list",
                "https://example.com/non-list",
                "summary",
                None,
                now,
                json.dumps({"Region": "not-list"}),
            ],
        )
    finally:
        conn.close()

    assert handle_top_trends(db_path=db_path, limit=0) == "No trend data available."
    assert handle_top_trends(db_path=db_path, days=7, limit=10) == "No trend data available."


def test_handle_benefit_match_query_no_query_tags_and_empty(tmp_path: Path) -> None:
    from mcp_server.tools import handle_benefit_match

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)
    now = datetime.now(UTC)
    _seed_article(
        db_path=db_path,
        article_id=1,
        title="청년 주거 지원금",
        link="https://example.com/youth",
        collected_at=now,
        entities={"SubsidyProgram": ["지원금"]},
    )
    _seed_article(
        db_path=db_path,
        article_id=2,
        title="노인 의료비 지원",
        link="https://example.com/health",
        collected_at=now,
    )

    matched = handle_benefit_match(db_path=db_path, query="청년", days=7, limit=10)
    all_recent = handle_benefit_match(db_path=db_path, query="", days=7, limit=10)
    empty = handle_benefit_match(db_path=db_path, query="없는검색어", days=7, limit=10)

    assert "청년 주거 지원금 [SubsidyProgram]" in matched
    assert "노인 의료비 지원" not in matched
    assert "청년 주거 지원금" in all_recent
    assert "노인 의료비 지원" in all_recent
    assert empty == "No matching benefits found."


def test_handle_benefit_match_ignores_invalid_entity_json(tmp_path: Path) -> None:
    from mcp_server.tools import handle_benefit_match

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)
    _seed_article(
        db_path=db_path,
        article_id=1,
        title="청년 지원 안내",
        link="https://example.com/invalid-entities",
        collected_at=datetime.now(UTC),
    )
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute(
            "UPDATE articles SET entities_json = ? WHERE link = ?",
            ["{not-json", "https://example.com/invalid-entities"],
        )
    finally:
        conn.close()

    output = handle_benefit_match(db_path=db_path, query="청년", days=7, limit=10)

    assert "청년 지원 안내" in output
    assert "[" not in output.splitlines()[1]


def test_filter_links_by_days_and_load_recent_quality_articles_edge_cases(tmp_path: Path) -> None:
    from benefitradar.mcp_server import tools

    missing_db = tmp_path / "missing.duckdb"
    db_without_articles = tmp_path / "empty.duckdb"
    conn = duckdb.connect(str(db_without_articles))
    conn.close()

    assert tools._filter_links_by_days(db_path=missing_db, links=[], days=7) == set()
    assert (
        tools._load_recent_quality_articles(
            db_path=missing_db,
            category="benefit",
            days=7,
            limit=10,
        )
        == []
    )
    assert (
        tools._load_recent_quality_articles(
            db_path=db_without_articles,
            category="benefit",
            days=7,
            limit=10,
        )
        == []
    )
    assert (
        tools._load_recent_quality_articles(
            db_path=db_without_articles,
            category="benefit",
            days=7,
            limit=0,
        )
        == []
    )


def test_load_recent_quality_articles_parses_entities_and_dates(tmp_path: Path) -> None:
    from benefitradar.mcp_server import tools

    db_path = tmp_path / "radar.duckdb"
    _init_articles_table(db_path)
    now = datetime.now(UTC)
    _seed_quality_article(
        db_path=db_path,
        article_id=1,
        title="청년 지원금 신청",
        link="https://example.com/benefit",
        source="Deadline Source",
        collected_at=now,
        entities={"SubsidyProgram": ["지원금"], "Bad": ["ok"]},
    )
    conn = duckdb.connect(str(db_path))
    try:
        _ = conn.execute(
            "UPDATE articles SET entities_json = ? WHERE link = ?",
            [
                json.dumps({"SubsidyProgram": ["지원금"], "Ignored": "not-list"}),
                "https://example.com/benefit",
            ],
        )
    finally:
        conn.close()

    articles = tools._load_recent_quality_articles(
        db_path=db_path,
        category="benefit",
        days=0,
        limit=10,
    )

    assert len(articles) == 1
    assert articles[0].matched_entities == {"SubsidyProgram": ["지원금"]}
    assert articles[0].published is not None
    assert articles[0].collected_at is not None


def test_handle_quality_report_returns_benefit_operational_json(tmp_path: Path) -> None:
    from mcp_server.tools import handle_quality_report

    db_path = tmp_path / "radar.duckdb"
    categories_dir = tmp_path / "categories"
    deadline = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    _write_quality_category_config(categories_dir)
    _init_articles_table(db_path)
    _seed_quality_article(
        db_path=db_path,
        article_id=1,
        title="청년 지원 신청 마감",
        link="https://example.com/deadline",
        source="Deadline Source",
        collected_at=datetime.now(UTC),
        entities={
            "OperationalEvent": ["application_deadline"],
            "ApplicationDeadline": [deadline],
        },
    )

    output = handle_quality_report(
        db_path=db_path,
        categories_dir=categories_dir,
        days=30,
        limit=10,
    )
    payload = json.loads(output)

    assert payload["category"] == "benefit"
    assert payload["summary"]["fresh_sources"] == 1
    assert payload["summary"]["missing_sources"] == 1
    assert payload["summary"]["application_deadline_events"] == 1
    assert payload["source_backlog"]["operational_candidates"][0]["id"] == "bokjiro_detail"


def test_handle_quality_report_returns_error_for_missing_category(tmp_path: Path) -> None:
    from mcp_server.tools import handle_quality_report

    output = handle_quality_report(
        db_path=tmp_path / "missing.duckdb",
        categories_dir=tmp_path / "categories",
        category="missing",
    )

    assert output.startswith("Error:")


def test_handle_price_watch_stub() -> None:
    from mcp_server.tools import handle_price_watch

    output = handle_price_watch(threshold=10.0)

    assert "Not available in template project" in output
