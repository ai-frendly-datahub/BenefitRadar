# BENEFITRADAR

복지로 API를 통해 정부 복지 정보를 실시간으로 수집하고, 키워드 기반 엔티티 분석으로 복지 동향을 추적합니다.

## STRUCTURE

```
BenefitRadar/
├── benefitradar/
│   ├── collector.py              # collect_sources() — 복지로 API (bokjiro.go.kr)
│   ├── analyzer.py               # apply_entity_rules() — 복지 카테고리별 키워드 매칭 (주거, 교육, 의료, 일자리 등)
│   ├── reporter.py               # generate_report() — Jinja2 HTML
│   ├── storage.py                # RadarStorage — DuckDB upsert/query/retention
│   ├── models.py                 # Source, Article, EntityDefinition, CategoryConfig
│   ├── config_loader.py          # YAML 로딩
│   ├── logger.py                 # structlog 구조화 로깅
│   ├── notifier.py               # Email/Webhook 알림
│   ├── raw_logger.py             # JSONL 원시 로깅
│   ├── search_index.py           # SQLite FTS5 전문 검색
│   ├── nl_query.py               # 자연어 쿼리 파서
│   ├── common/                   # 공유 유틸리티
│   └── mcp_server/               # MCP 서버 (server.py + tools.py)
├── config/
│   ├── config.yaml               # database_path, report_dir, raw_data_dir, search_db_path
│   └── categories/{domain}.yaml  # 소스 + 엔티티 정의
├── data/                         # DuckDB, search_index.db, raw/ JSONL
├── reports/                      # 생성된 HTML 리포트
├── tests/unit/                   # pytest 단위 테스트
├── main.py                       # CLI 엔트리포인트
└── .github/workflows/radar-crawler.yml
```

## ENTITIES

| Entity | Examples |
|--------|----------|
| 주요 엔티티 1 | 예시 1, 예시 2, 예시 3 |
| 주요 엔티티 2 | 예시 4, 예시 5, 예시 6 |
| 주요 엔티티 3 | 예시 7, 예시 8, 예시 9 |

## DEVIATIONS FROM TEMPLATE

- 표준 템플릿 대비 특화 기능 1
- 표준 템플릿 대비 특화 기능 2

## COMMANDS

```bash
python main.py --category benefit --recent-days 7
python main.py --category benefit --per-source-limit 50 --keep-days 90
```
