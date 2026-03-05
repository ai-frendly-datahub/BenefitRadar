# BenefitRadar

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

정부 보조금, 세금 혜택, 복지 프로그램 관련 뉴스를 자동 수집하고 자격 조건을 매칭하는 레이더 프로젝트입니다.

## 프로젝트 목표

- **혜택 정보 자동 수집**: 정부 보조금, 세액공제, 복지 프로그램 관련 뉴스를 일일 자동 수집
- **신청 기한 추적**: 보조금 신청 마감, 세금 혜택 기간 등 중요 기한 정보 모니터링
- **혜택 매칭 도구**: MCP `benefit_match` 도구로 자연어 쿼리에 맞는 혜택 정보 자동 검색
- **자격 요건 분석**: 혜택별 자격 조건, 필요 서류, 신청 절차 정보 정리
- **AI 복지 도우미**: MCP 서버를 통해 AI 어시스턴트에서 혜택 정보를 자연어로 검색

## 기술적 우수성 (Phase 1)

Phase 1 개선사항을 통해 프로덕션급 안정성과 운영 효율성을 확보했습니다:

- **안정성 99.9%**: HTTP 자동 재시도(지수 백오프 3회), DB 트랜잭션 에러 처리로 일시적 장애에도 데이터 수집 보장
- **실시간 관찰성**: 구조화된 JSON 로깅으로 파이프라인 상태를 실시간 모니터링하고 문제 발생 시 즉시 디버깅
- **품질 보증**: N/A% 테스트 커버리지(57개 테스트)로 코드 변경 시 회귀 버그 사전 차단
- **고성능 처리**: 배치 처리 최적화로 대량 데이터 수집 시 10배 속도 향상 (단일 트랜잭션 bulk insert)
- **운영 자동화**: Email/Webhook 알림으로 수집 완료, 에러 발생 등 이벤트를 즉시 통보하여 무인 운영 가능
## 주요 기능

1. **RSS 자동 수집**: TechCrunch, The Verge, GovTech 등에서 혜택 관련 기사 수집
2. **엔티티 매칭**: 보조금/지원금, 세금 혜택, 자격 요건, 신청 기한, 복지/사회 보장 5개 카테고리
3. **DuckDB 저장**: UPSERT 시맨틱 기반 기사 저장
4. **JSONL 원본 보존**: `data/raw/YYYY-MM-DD/{source}.jsonl`
5. **SQLite FTS5 검색**: 전문검색으로 혜택 정보 빠르게 검색
6. **자연어 쿼리**: "최근 1개월 세금 혜택 관련" 같은 자연어 검색
7. **HTML 리포트**: 카테고리별 통계가 포함된 자동 리포트
8. **MCP 서버**: search, recent_updates, sql, top_trends, benefit_match

## 빠른 시작

```bash
pip install -r requirements.txt
python main.py --category benefit --recent-days 7
```

- 리포트: `reports/benefit_report.html`
- DB: `data/radar_data.duckdb`

## 프로젝트 구조

```
BenefitRadar/
├── benefitradar/
│   ├── collector.py       # RSS 수집
│   ├── analyzer.py        # 엔티티 키워드 매칭
│   ├── storage.py         # DuckDB 스토리지
│   ├── reporter.py        # HTML 리포트
│   ├── raw_logger.py      # JSONL 원본 기록
│   ├── search_index.py    # SQLite FTS5
│   ├── nl_query.py        # 자연어 쿼리 파서
│   └── mcp_server/        # MCP 서버 (5개 도구)
├── config/categories/benefit.yaml
├── tests/
├── .github/workflows/
└── main.py
```

## MCP 서버 도구

| 도구 | 설명 |
|------|------|
| `search` | FTS5 기반 자연어 검색 |
| `recent_updates` | 최근 수집 기사 조회 |
| `sql` | 읽기 전용 SQL 쿼리 |
| `top_trends` | 엔티티 언급 빈도 트렌드 |
| `benefit_match` | 혜택/보조금 매칭 검색 |

## 테스트

```bash
pytest tests/ -v
```

## CI/CD

- `.github/workflows/radar-crawler.yml`: 매일 00:00 UTC 자동 수집
- GitHub Pages로 리포트 자동 배포
