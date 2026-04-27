# Data Quality Plan

- 생성 시각: `2026-04-23T14:45:24.863320+00:00`
- 우선순위: `P1`
- 데이터 품질 점수: `93`
- 가장 약한 축: `추적성`
- Governance: `medium`
- Primary Motion: `conversion`

## 현재 이슈

- 비활성 고가치 source 1개가 있어 freshness와 traceability가 떨어짐

## 필수 신호

- 지원·복지 프로그램 원문 공고와 신청 마감일
- 대상자·소득·지역·연령 eligibility 조건
- 선정 결과와 집행 실적 또는 접수 상태

## 품질 게이트

- 신청 시작일·마감일·결과 발표일을 별도 필드로 유지
- 지원 금액과 eligibility 조건은 원문 URL로 trace 가능해야 함
- 혜택 요약과 실제 신청 가능 상태를 분리

## 다음 구현 순서

- deadline과 eligibility extractor를 운영 레이어로 추가
- 보조금24·복지로·지자체 공고의 program id 정규화
- 선정/집행 실적 source를 후행 검증 신호로 연결

## 운영 규칙

- 원문 URL, 수집일, 이벤트 발생일은 별도 필드로 유지한다.
- 공식 source와 커뮤니티/시장 source를 같은 신뢰 등급으로 병합하지 않는다.
- collector가 인증키나 네트워크 제한으로 skip되면 실패를 숨기지 말고 skip 사유를 기록한다.
- 이 문서는 `scripts/build_data_quality_review.py --write-repo-plans`로 재생성한다.
