# Research Direction Task ED — 새 정보·시계열 비교와 주문 없는 관측 준비

## 2026-10-31 KST — 승인된 Goal과 시작 상태

운영자는 직전 상세 설계대로 새 Goal 등록, 두 가설의 실제 입력 보유·공백 감사,
가능한 규칙·LightGBM·시계열 비교와 주문 없는 관측 준비, 별도 검토·필수 CI·
정상 머지를 요청했다. Goal 도구는 active를 반환했고 토큰 예산은 지정하지 않았다.
원래 상세 설계는 CLAUDE.md Current Scope의 `ED detailed design`에 보존했다.
이 기록은 그 규칙·위험 수치를 별도로 복제하지 않는다.

기준 소스는 PR384 정상 머지 b4d1fbc87e0784674ce6ac964817f99b484a85cc다.
현재 브랜치 `codex/ed-context-sequence-observer-plan`에는 직전 설계 문서 두 개의
미커밋 변경이 있으며 보존했다. 기존 보호 미추적 원본 둘은 읽기·import·검사·
scan·staging에서 제외한다. 기존 EC Goal의 완료와 음의 결과는 되돌리지 않는다.

### 권한과 실행 순서

새 승인은 발견용 기존 자료 감사와 지원되는 실험, 코드·관측 연결 준비를 포함한다.
새 forward/예약 자료, 관측기 실제 가동, 모의/실주문, 기존 수집기·인증정보·
위험·자금 변경이나 유료 구매는 포함하지 않는다. 기존 발견용 입력에 대한
supplement는 정확한 원천·날짜·행/요청 상한을 먼저 등록하고 성과 전에 동결한다.
근거 없는 과거 공개시각을 만들거나 운영자에게 외부기관 문의를 요구하지 않는다.

1. 가격 성과를 계산하지 않는 G1 metadata 감사를 등록·구현·합성 검산한다.
2. 관측기는 원천/종목 스트림의 순수 receipt reducer와 NO_ORDER 판단 기록을
   합성자료로 준비한다. 계좌·주문 API나 외부 transport를 활성화하지 않는다.
3. 최종 소스·연구 검토와 CI/정상 머지, 필요한 정확 소스의 합성 자격을 확인한
   후 G1 실제 감사 한 번을 실행한다. 실제 실패는 자동 재시도하지 않는다.
4. H1/H2 각각 자료 가용·범위 제한·불가를 구분하고 고정된 한 차례 보완 여부를
   결정한다. 지원되는 분기의 후보·특징·정답·비용·fold 계약을 결과 전에 고정한다.
5. 기존 공통 구성요소로 모델 비교와 별도 검산을 수행하고 탐색 결과를 낸다.
   입력이 불가능한 분기를 가상의 수익 결과나 성공한 정책으로 세지 않는다.

### G1 고정 조사 계약

등록: `configs/research/discovery/intraday-ed-input-inventory-v1.json`.
대상은 기존 GCP 원본 DB의 schema와 이미 소비한 발견 기간의 제한된 metadata다.
schema 목록은 데이터 보유를 찾기 위한 구조 확인이며 기간 밖의 행을 읽지 않는다.
`klines`의 KRX/공식 index namespace 1분행, `positioning`의 주식 호가 metric에 대해
날짜·심볼·시각·조회시각·거래량/거래대금의 NULL/빈값/값 존재만 확인한다.
가격·수익률·수량/금액 값은 SQLite 밖으로 반환하지 않는다.

공시·업종 테이블 후보는 이름/열 구조로만 식별하며 이름만으로 적합한 자료라고
판정하지 않는다. 공시 시각·정정 이력과 당시 업종 mapping은 별도 실제 내용
계약이 있어야 사용할 수 있다. 없는 테이블·없는 행·호환되지 않는 schema와
관측 불가를 구분한다. API·인증정보·forward 읽기나 새 손익 계산은 없다.

한 readonly transaction 안의 SQL 집계와 별도 timestamp/presence 스트림을
대조한다. schema 열거와 후보 keyword 분류는 공통 helper이며 독립적으로
재구현한 schema 증명이 아니다. metadata digest는 반환 projection의 정체성이며 운영 DB 파일 hash나
기업행사·실제 거래 가능성의 증명이 아니다. 호출기는 기존 발견 기록·자원 검사·
독점 출력 저장을 재사용하고 started가 DB 접근보다 앞선다.

### 현재 사실과 진행도

새 실제 감사 0회, 새 정책 성과 비교 0/6, 새 학습 0회, 관측기 실제 가동 0회.
관측기와 metadata 감사의 독립 파일 소유 구현을 진행한다. 소스 구조 조사는
실제 DB 보유 확인과 다르다. 현재 미완료인 모델·입력 상태를 성공으로 세지 않는다.

## 소스 준비와 지정 합성 검증

`intraday_ed_input_inventory`는 schema 후보와 주식/index 분봉·호가 metadata의
producer SQL 집계를 별도 timestamp/presence 스트림으로 대조한다. 임의 SQL,
가격/OHLC, 수치 거래량 및 호가 value 반환, DB 쓰기·마이그레이션을 차단한다.
schema helper 공통 사용은 독립 schema 증명으로 과장하지 않는다.

`intraday_ed_observer`는 이미 받은 typed receipt만 받는 제한된 offline reducer다.
Decimal, clock/원천/vintage, 실제0/미확인/오류를 보존하며 중복·충돌·순서 역전·
공백·재연결·stale/crossed 호가를 검출한다. 명시한 batch 상한을 넘으면 원본
상태를 변경하지 않고 거절한다. 인과 시각이 일치할 때에만 외부 판단을 NO_ORDER로
동결한다. 외부 transport·지속 저장·복구·모델 추론이나 운영 가동은 아직 아니다.

이 두 모듈의 지정 WSL 합성 검사69개는4.26초에 통과했다. 별도 소스 검토자도
같은69개를3.33초에 독립 실행했고 예비 범위에서 차단 지적을 보고하지 않았다.
색인·문서 수 지정 검사8개는2.41초에 통과했다. 예비 연구 검토는 schema 검산의
주장 경계를 보완한 뒤 추가 지적이 없었다. 아직 최종 커밋·private 호출기 검토,
필수 CI, 전체 합성 자격이나 실제 입력 감사의 통과를 뜻하지 않는다.

private 호출기는 기존 발견 기록·자원 검사·Git helper와 안전한 독점 저장을
재사용한다. 원격 metadata worker, 로컬 WSL controller/SSH 및 제한 Git 자식의
정적 상한을 기존 일반 프로필 안에 분할하며 과거 CPU600 예외를 사용하지 않는다.
운영 collector source는 변경하지 않고, 격리 research checkout만 정상 merge의
동일 source/tree로 확인한 후 사용한다. source-only validate는 실제 접근0으로
통과했다. full invented profile과 actual은 아직0회다.

private 실행기의 지정 tiny/source 검사12개가7.67초에 통과했고, 제한된 Git
자식이 실제 source 명령을 수행하는 경우와 native 출력 경계도 포함했다.
로컬 WSL에서 같은 producer/독립 검산으로 발명 분봉200만 행과 호가5만 행의
규모 진단을 한 번 완료했다(CPU11.334979초, address space316,260,352bytes).
이는 SSH 전체 경로 자격이 아니라 사전 로컬 진단이다. 원본은 worktree 밖
`/tmp/ed-g1-local-invented-20261031-v1`에 보존했다. 이때 실제 입력 접근0이다.
