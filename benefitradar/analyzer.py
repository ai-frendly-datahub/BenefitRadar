from __future__ import annotations

from typing import Iterable, List

from .models import Article, EntityDefinition


def apply_entity_rules(articles: Iterable[Article], entities: List[EntityDefinition]) -> List[Article]:
    """Attach matched entity keywords to each article via simple keyword search."""
    analyzed: List[Article] = []
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
