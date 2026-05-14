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
│   └── categories/benefit.yaml  # 소스 + 엔티티 정의
├── data/                         # DuckDB, search_index.db, raw/ JSONL
├── reports/                      # 생성된 HTML 리포트
├── tests/unit/                   # pytest 단위 테스트
├── main.py                       # CLI 엔트리포인트
└── .github/workflows/radar-crawler.yml
```

## ENTITIES

| Entity | Examples |
|--------|----------|
| SubsidyProgram | 보조금, 지원금, grant, funding |
| TargetDemographic | 청년, 노인, 장애인, 자영업자 |
| Eligibility | 자격 요건, 소득 기준, 신청 조건 |
| TaxBenefit | 세액공제, 소득공제, tax credit |

## DEVIATIONS FROM TEMPLATE

- 복지로/보조금24/정부 공식 채널을 우선 evidence로 취급한다.
- `support_program_notice`, `application_deadline`, `eligibility_rule`, `selection_result` 이벤트 모델을 분리한다.
- API key 또는 브라우저 제한 source는 임의 활성화하지 않고 skip 사유와 재활성화 gate를 유지한다.

## COMMANDS

```bash
python main.py --category benefit --recent-days 7
python main.py --category benefit --per-source-limit 50 --keep-days 90
```
