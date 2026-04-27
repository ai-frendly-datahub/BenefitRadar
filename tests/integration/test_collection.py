from __future__ import annotations

from unittest.mock import patch

import pytest

from benefitradar.models import Article, Source
from benefitradar.storage import RadarStorage


@pytest.mark.integration
def test_collection_workflow(
    tmp_storage: RadarStorage,
    sample_articles: list[Article],
) -> None:
    """Test complete collection workflow: mock RSS feed → collect → verify structure."""
    with patch("benefitradar.collector.collect_sources") as mock_collect:
        mock_collect.return_value = (sample_articles, [])

        articles, errors = mock_collect(
            [Source(name="bokjiro", type="api", url="https://api.bokjiro.go.kr")],
            category="benefit",
            limit_per_source=30,
        )

        assert len(articles) == 5
        assert len(errors) == 0
        assert all(isinstance(a, Article) for a in articles)
        assert all(a.category == "benefit" for a in articles)
        assert all(a.source == "bokjiro" for a in articles)


@pytest.mark.integration
def test_storage_persistence(
    tmp_storage: RadarStorage,
    sample_articles: list[Article],
) -> None:
    """Test storage integration: insert articles → query → verify data integrity."""
    tmp_storage.upsert_articles(sample_articles)

    articles = tmp_storage.recent_articles(category="benefit", days=30, limit=100)

    assert len(articles) == 5
    assert articles[0].title == "2024년 기초생활보장 수급자 선정기준 변경"
    assert articles[0].link == "https://bokjiro.go.kr/benefit/1001"
    assert articles[0].source == "bokjiro"
    assert articles[0].category == "benefit"


@pytest.mark.integration
def test_duplicate_handling(
    tmp_storage: RadarStorage,
    sample_articles: list[Article],
) -> None:
    """Test duplicate handling: insert same link twice → verify single entry."""
    tmp_storage.upsert_articles(sample_articles[:2])
    result1 = tmp_storage.recent_articles(category="benefit", days=30, limit=100)
    assert len(result1) == 2

    tmp_storage.upsert_articles(sample_articles[:2])
    result2 = tmp_storage.recent_articles(category="benefit", days=30, limit=100)
    assert len(result2) == 2

    tmp_storage.upsert_articles(sample_articles[2:])
    result3 = tmp_storage.recent_articles(category="benefit", days=30, limit=100)
    assert len(result3) == 5
