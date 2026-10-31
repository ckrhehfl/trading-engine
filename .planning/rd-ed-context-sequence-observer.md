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

## G1 첫 감사 완료와 한정 공시 보완 준비

PR385는 별도 최종 소스·연구 검토와 필수CI 네 개의 통과 후 정상 머지했다.
검토된 private 실행기·검사기와 합성 검사의 격리 GCP 전송은 운영자의 구체적
추가 승인 이후 완료했다. 앞서 자동 승인 심사가 실행 전 거절한 두 시도는 실제
실행·전송이 없었고 우회하지 않았다. 이 승인 요청은 해소된 과거 기록이다.
최종 소스의 전체 SSH 발명자료 자격검증과 실제 metadata 감사 한 번이 각각
완료됐고 독립 저장 영수증·주장 감사도 통과했다. 원본·해시·private 결과는
운영자의 continuation에 보존한다. 원래 실제 단위를 재실행하지 않는다.

이번 감사는 데이터 적합성이나 수익 결과가 아니다. 공시·업종 schema의 이름
검색은 외부 archive 부재를 증명하지 않는다. 저장된 원본 metadata의 고정 SHA
투영으로 보완 종목 ID를 얻으며, 추가 DB 조회나 가격 계산을 하지 않는다.

다음 등록은 `intraday-ed-disclosure-support-v1.json`이다. 기존 AV source에서
확인한 DART 공개 HTML 정기보고서 목록만 정확히 제한해 보완한다. API 인증키를
요구하는 OpenDART JSON 명세와 다른 채널이며, 그 명세의 정정 옵션을 HTML의
완전성 증거로 사용하지 않는다. 현재 query의 반환 전수·정정 행을 보존하고
접수 날짜를 장중 공개시각으로 만들지 않는다. 미검증 empty/부분응답은 unknown이다.
원래 당시 월별20종목 표는 유지하고 관측 지원이 없는 대상은 unknown으로 남긴다.

H1의 정확 시장 기준은 원 설계에서 G2 동결 사항이다. 향후 H1용 관측 동료
대용치를 명명·동결할 수 있지만, 기존 EC 세 창/10특징 타입을 ED30분 텐서로
위장하지 않는다. H2는 공식 당시 업종 mapping·분봉을 요구하므로 이를 동료
대용치로 바꾸지 않는다. 공식 KIS 업종 분봉 source에는 고정 과거 날짜/cursor
요청이 없어 해당 발견 기간의 지원은 미확인이다. 이는 API 전체 불능이나 전략
실패라는 결론이 아니며, 가능한 H1 분기의 진행을 막는 이유로 쓰지 않는다.

새 성과·학습은 아직 실행하지 않았다. 이 보완은 성과가 아닌 입력 지원 판단이며,
정확 실행 경로·소스·자원 동결, 별도 검토·CI·머지와 자체 전체 합성 자격 후에만
실제 조회를 한 번 허용한다. 기존 CPU600 예외나 자동 재시도는 사용하지 않는다.

### 공시 보완 구현과 지정 검사

파서·수집/원본 검산·native 단위 실행기를 공개 source로 분리했다. 실행기는 기존
logger·exclusive storage·연구 lock·Git helper·ResearchBudget을 재사용한다.
EC의 원래 세 입력 schema를 바꾸거나 새 공시를 옛 A/B 자료로 위장하지 않는다.
추가 private 실행 코드를 GCP 파일로 복사하는 경로는 없다. 공개 코드의 정상
머지 소스를 격리 checkout에 반영하고, private WSL 제어기는 고정 configuration과
검증한 공개 entrypoint 호출만 전달한다. 수집기 checkout은 변경하지 않는다.

원본 검산은 producer 파서를 oracle로 호출하지 않고 saved HTML에서 별도로
페이지·접수번호·날짜·회사·정정 행을 재구성한다. 이 일치는 공식 채널의 당시
완전성 증명이 아니다. profile은 발명18코드·54요청·5400행과 총54MiB 원문을
고정해 저장·재해시·검산 경계를 확인한다. 모든 역할의 상한은 기존 일반 단위의
부분집합이며 잔여 예산을 새 모델이나 재시도에 재배정하지 않는다.

별도 예비 소스 검토에서 부팅 import 경로와 dependency 검증 순서를 보완했다.
실행 전 고정 공개14파일을 standard library만으로 검증하고, 외부 SHA로 고정한
최종 local source manifest를 project import보다 먼저 검사한다. 표준 모듈 shadow와
기존 unchecked bytecode가 검토된 source를 대신 실행하지 않는 발명 child 검사도
추가했다. 이는 실제 조회 오류나 시장 결과가 아니라 source 준비 중 수정이다.

지정 공개 파서·수집기·실행기와 private 제어기·planning 검사162개가18.11초에
통과했고1개는 건너뛰었다. 최종 변경이 끝나면 최종 diff 검토와 관련 지정 검사를
확인한다. 이 결과는 CI·정상 머지·전체 SSH 자격이나 실제 공시 조회 완료를 대신하지
않는다. 보호 미추적 원본에 대한 전체 scanner/전체 local pytest는 실행하지 않는다.
