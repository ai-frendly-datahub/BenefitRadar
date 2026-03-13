from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from benefitradar.models import Article, CategoryConfig, EntityDefinition, Source
from benefitradar.storage import RadarStorage


@pytest.fixture
def tmp_storage(tmp_path: Path) -> RadarStorage:
    """Create a temporary RadarStorage instance for testing."""
    db_path = tmp_path / "test.duckdb"
    storage = RadarStorage(db_path)
    yield storage
    storage.close()


@pytest.fixture
def sample_articles() -> list[Article]:
    """Create sample articles with realistic benefit domain data."""
    now = datetime.now(UTC)
    return [
        Article(
            title="2024년 기초생활보장 수급자 선정기준 변경",
            link="https://bokjiro.go.kr/benefit/1001",
            summary="기초생활보장 수급자 선정기준이 변경되었습니다. 중위소득 기준이 상향 조정되었습니다.",
            published=now,
            source="bokjiro",
            category="benefit",
            matched_entities={},
        ),
        Article(
            title="청년 주거 지원금 신청 시작",
            link="https://bokjiro.go.kr/benefit/1002",
            summary="청년층을 위한 주거 지원금 신청이 시작되었습니다. 월 30만원 지원.",
            published=now,
            source="bokjiro",
            category="benefit",
            matched_entities={},
        ),
        Article(
            title="아동수당 인상 안내",
            link="https://bokjiro.go.kr/benefit/1003",
            summary="아동수당이 월 10만원에서 15만원으로 인상되었습니다.",
            published=now,
            source="bokjiro",
            category="benefit",
            matched_entities={},
        ),
        Article(
            title="장애인 활동보조 서비스 확대",
            link="https://bokjiro.go.kr/benefit/1004",
            summary="장애인 활동보조 서비스가 확대되어 더 많은 대상자가 혜택을 받을 수 있습니다.",
            published=now,
            source="bokjiro",
            category="benefit",
            matched_entities={},
        ),
        Article(
            title="노인 일자리 사업 모집",
            link="https://bokjiro.go.kr/benefit/1005",
            summary="65세 이상 노인을 위한 일자리 사업 모집이 시작되었습니다.",
            published=now,
            source="bokjiro",
            category="benefit",
            matched_entities={},
        ),
    ]


@pytest.fixture
def sample_entities() -> list[EntityDefinition]:
    """Create sample entities with benefit domain keywords."""
    return [
        EntityDefinition(
            name="income_support",
            display_name="소득 지원",
            keywords=["기초생활보장", "생계급여", "의료급여", "주거급여", "교육급여"],
        ),
        EntityDefinition(
            name="housing",
            display_name="주거 지원",
            keywords=["주거", "주택", "전세", "월세", "주거비"],
        ),
        EntityDefinition(
            name="child_benefits",
            display_name="아동 지원",
            keywords=["아동수당", "아이", "자녀", "아동", "양육"],
        ),
        EntityDefinition(
            name="disability",
            display_name="장애인 지원",
            keywords=["장애인", "활동보조", "장애", "보조금"],
        ),
        EntityDefinition(
            name="elderly",
            display_name="노인 지원",
            keywords=["노인", "65세", "고령", "일자리", "연금"],
        ),
    ]


@pytest.fixture
def sample_config(tmp_path: Path, sample_entities: list[EntityDefinition]) -> CategoryConfig:
    """Create a sample CategoryConfig for testing."""
    sources = [
        Source(
            name="bokjiro",
            type="api",
            url="https://api.bokjiro.go.kr/openapi/v1/benefits",
        ),
    ]
    return CategoryConfig(
        category_name="benefit",
        display_name="복지 혜택",
        sources=sources,
        entities=sample_entities,
    )
