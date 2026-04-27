from __future__ import annotations

from pathlib import Path

import pytest

from benefitradar.models import Article, EntityDefinition
from benefitradar.reporter import generate_report


def _apply_entity_rules_py39(
    articles: list[Article], entities: list[EntityDefinition]
) -> list[Article]:
    """Apply entity rules (Python 3.9 compatible version)."""
    analyzed: list[Article] = []
    lowered_entities = [
        EntityDefinition(
            name=e.name,
            display_name=e.display_name,
            keywords=[kw.lower() for kw in e.keywords],
        )
        for e in entities
    ]

    for article in articles:
        haystack = f"{article.title}\n{article.summary}".lower()
        matches: dict[str, list[str]] = {}
        for entity, lowered_entity in zip(entities, lowered_entities, strict=False):
            hit_keywords = [kw for kw in lowered_entity.keywords if kw and kw in haystack]
            if hit_keywords:
                matches[entity.name] = hit_keywords
        article.matched_entities = matches
        analyzed.append(article)

    return analyzed


@pytest.mark.integration
def test_report_generation(
    tmp_path: Path,
    sample_articles: list[Article],
    sample_entities: list[EntityDefinition],
    sample_config,
) -> None:
    """Test report generation: generate HTML → verify file exists + contains expected content."""
    analyzed = _apply_entity_rules_py39(sample_articles, sample_entities)

    output_path = tmp_path / "report.html"
    stats = {"total_articles": len(analyzed), "sources": 1}

    result = generate_report(
        category=sample_config,
        articles=analyzed,
        output_path=output_path,
        stats=stats,
    )

    assert result.exists()
    assert result.suffix == ".html"

    content = result.read_text(encoding="utf-8")
    assert "복지 혜택" in content
    assert "2024년 기초생활보장" in content
    assert "청년 주거 지원금" in content
