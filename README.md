# BenefitRadar

혜택/지원금 관련 신호를 수집하고, 자격 요건/신청 기한/지원 항목 키워드를 태깅해
DuckDB와 HTML 리포트로 정리하는 경량 Radar 파이프라인입니다.

## 빠른 시작
1. 의존성 설치
   ```bash
   pip install -r requirements.txt
   ```
2. 실행
   ```bash
   python main.py --category benefit --recent-days 7
   # 리포트: reports/benefit_report.html
   ```

## 카테고리 설정
- 기본 카테고리 파일: `config/categories/benefit.yaml`
- 추적 대상:
  - 보조금/지원금(Subsidy)
  - 세금 혜택(TaxCredit)
  - 자격 요건(Eligibility)
  - 신청 기한(Deadline)
  - 복지/사회 보장(Welfare)

## MCP 서버
- 서버 이름: `benefitradar`
- 주요 툴:
  - `search`
  - `recent_updates`
  - `sql`
  - `top_trends`
  - `benefit_match` (최근 기사에서 혜택/지원금 매칭)

## GitHub Actions
- 워크플로: `.github/workflows/radar-crawler.yml`
- 워크플로 이름: `BenefitRadar Crawler`
- 기본 카테고리 환경 변수: `RADAR_CATEGORY=benefit`

## 기본 경로
- DB: `data/radar_data.duckdb`
- 검색 인덱스: `data/search_index.db`
- 원본 JSONL: `data/raw/`
- 리포트 출력: `reports/`
