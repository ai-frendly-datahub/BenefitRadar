# Business Quality Upgrade

- Generated: `2026-04-14T04:48:11.525239+00:00`
- Portfolio verdict: `충분`
- Business value score: `89.1`
- Upgrade phase: P1 신청 가능성 extractor 강화
- Primary motion: `conversion`
- Weakest dimension: `traceability`

## Current Evidence

- Primary rows: `1957`
- Today raw rows: `69`
- Latest report items: `76`
- Match rate: `100.0%`
- Collection errors: `2`
- Freshness gap: `3`

## Upgrade Actions

- deadline과 eligibility extractor 결과를 support_program_notice와 별도 운영 이벤트로 유지한다.
- program_id 정규화와 선정 결과 source 연결을 통해 신청 가능성 판단의 후행 검증 루프를 만든다.
- 보조금24/복지로 계열 후보는 parser와 evidence URL 검증 후 운영 레이어로 확장한다.

## Quality Contracts

- `config/categories/benefit.yaml`: output `reports/benefit_quality.json`, tracked `support_program_notice, application_deadline, eligibility_rule, selection_result`, backlog items `3`

## Contract Gaps

- None.
