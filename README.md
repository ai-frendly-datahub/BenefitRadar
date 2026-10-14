# BenefitRadar - 복지 정보 레이더

**🌐 Live Report**: https://ai-frendly-datahub.github.io/BenefitRadar/


정부 공식 RSS/웹 공고와 API 후보를 통해 복지 정보를 수집하고, 키워드 기반 엔티티 분석으로 복지 동향을 추적합니다.

## 프로젝트 목표

- **데이터 수집**: 정책브리핑, 정부·지자체 공고, 보조금24/복지 API 후보
- **엔티티 분석**: 복지 카테고리별 키워드 매칭 (주거, 교육, 의료, 일자리 등)
- **트렌드 리포트**: DuckDB 저장 + HTML 리포트로 복지/지원금 동향 시각화
- **자동화**: GitHub Actions 일일 수집 + GitHub Pages 리포트 자동 배포

## 기술적 우수성

- **안정성**: HTTP 자동 재시도(지수 백오프), DB 트랜잭션 에러 처리
- **관찰성**: 구조화된 JSON 로깅으로 파이프라인 상태 실시간 모니터링
- **품질 보증**: 단위 테스트로 코드 변경 시 회귀 버그 사전 차단
- **고성능**: 배치 처리 최적화로 대량 데이터 수집 시 성능 향상
- **운영 자동화**: Email/Webhook 알림으로 무인 운영 가능

## 빠른 시작

1. 가상환경을 만들고 런타임 의존성을 설치합니다.
   ```bash
   pip install -r requirements.txt
   ```

   개발/테스트 의존성까지 설치하려면 `pip install -r requirements-dev.txt`를 사용합니다.

2. 실행:
   ```bash
   python main.py --category benefit --recent-days 7
   # 리포트: reports/benefit_report.html
   ```

   주요 옵션: `--per-source-limit 20`, `--recent-days 5`, `--keep-days 60`, `--timeout 45`.

## 운영 계약

Radar-Template의 수집 계약을 BenefitRadar에 고정한다. 시크릿 값은 코드, YAML, 로그, skip 파일에 넣지 않는다.

1. **radar-core 핀.** `requirements.txt`와 `pyproject.toml`은 `radar-core @ git+https://github.com/ai-frendly-datahub/radar-core.git@main` 이다.
2. **스냅샷 전에 압축.** `apply_date_storage_policy()`와 `RadarStorage.create_daily_snapshot()`은 복사 전에 `radar_core.db_compact.compact_duckdb_file`를 호출한다. 압축이 실패해도 기존 파일을 스냅샷한다.
3. **피드 클라이언트.** User-Agent는 `BenefitRadar/0.1`이다. RSS 타임아웃 기본값은 45초이고 TLS 검증은 항상 켠다. Cloudflare, 챌린지, 쿠키 월, 403/404 제목은 저장 전에 버린다.
4. **없는 시크릿.** 소스마다 `::warning` 한 번과 `data/raw/<날짜>/<소스>.skip.json`(환경 변수 이름만)을 남긴다. 수집 실패는 빈 리스트이며 샘플 기사를 만들지 않는다.
5. **실패해야 하는 실행.** 활성 소스 수집이 0건이거나, 출력이 0바이트이거나, DuckDB가 100MiB 이상이면 작업이 실패한다. 데이터 푸시에는 `continue-on-error`가 없다.
6. **Pages는 푸시 성공 후에만.** `gh-pages` 배포는 auto-commit `outcome == success`일 때만 실행된다.
7. **테스트.** 날짜 단정은 `tests/reference_dates.py`(2026-03-13 12:00 UTC)와 `tests/fixtures/`를 쓴다. 커버리지는 `relative_files = true`이다.

API 키가 필요한 보조금24/복지로 소스는 켜지 않는다. 환경 변수 이름은 `BOKJIRO_API_KEY`이다.

## GitHub Actions & GitHub Pages

- 워크플로: `.github/workflows/radar-crawler.yml`
  - 스케줄: 매일 00:00 UTC (KST 09:00), 수동 실행도 지원.
  - 환경 변수 `RADAR_CATEGORY` 값은 `benefit`입니다.
  - 수집 타임아웃은 45초입니다.
  - 리포트 배포 디렉터리: `reports` → `gh-pages` 브랜치. 데이터 푸시가 성공한 뒤에만 배포합니다.
  - DuckDB 경로: `data/radar_data.duckdb` (Pages에 올라가지 않음). 커밋 전 90MiB 예산으로 압축하고, 100MiB 이상이면 작업을 실패시킵니다. 아티팩트로 30일 보관.

- 설정 방법:
  1) 저장소 Settings → Pages에서 `gh-pages` 브랜치를 선택해 활성화
  2) Actions 권한을 기본값으로 두거나 외부 PR에서도 실행되도록 설정
  3) 워크플로 파일의 `RADAR_CATEGORY`가 `benefit`인지 확인

## 동작 방식

- **수집**: 카테고리 YAML에 정의된 소스를 수집합니다. 실행 시 DuckDB에 적재하고 보존 기간(`keep_days`)을 적용합니다.
- **분석**: 엔티티별 키워드 매칭. 매칭된 키워드를 리포트에 칩으로 표시합니다.
- **리포트**: `reports/<category>_report.html`을 생성하며, 최근 N일(기본 7일) 기사와 엔티티 히트 카운트, 수집 오류를 표시합니다.

## 기본 경로

- DB: `data/radar_data.duckdb`
- 리포트 출력: `reports/`

## 디렉터리 구성

```
BenefitRadar/
  main.py                 # CLI 엔트리포인트
  requirements.txt        # 의존성
  config/
    config.yaml           # DB/리포트 경로 설정
    categories/
      benefit.yaml  # 소스 + 엔티티 정의
  benefitradar/
    collector.py          # 데이터 수집
    analyzer.py           # 엔티티 태깅
    reporter.py           # HTML 렌더링
    storage.py            # DuckDB 저장/정리
    config_loader.py      # YAML 로더
    models.py             # 데이터 클래스
  .github/workflows/      # GitHub Actions (crawler + Pages 배포)
```

<!-- DATAHUB-OPS-AUDIT:START -->
## DataHub Operations

- CI/CD workflows: `pr-checks.yml`, `radar-crawler.yml`, `release.yml`.
- GitHub Pages visualization: `reports/index.html` (valid HTML); https://ai-frendly-datahub.github.io/BenefitRadar/.
- Latest remote Pages check: HTTP 200, HTML.
- Local workspace audit: 61 Python files parsed, 0 syntax errors.
- Re-run audit from the workspace root: `python scripts/audit_ci_pages_readme.py --syntax-check --write`.
- Latest audit report: `_workspace/2026-04-14_github_ci_pages_readme_audit.md`.
- Latest Pages URL report: `_workspace/2026-04-14_github_pages_url_check.md`.
<!-- DATAHUB-OPS-AUDIT:END -->
