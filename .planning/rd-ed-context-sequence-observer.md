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

### 첫 전체 CI의 격리 오류 수정과 offline IO 묶음

첫 PR386 head의 전체 CI는13040개 통과·3개 건너뜀·169 subtests 통과와
1개 실패를25분07초에 보고했다. 실패는 `run_discovery_trial` 직접 import가
기존 autouse 로그 격리 참조를 우회할 수 있다는 AST 보호 검사였다. 실행기를
`experiment_log` 모듈 참조 호출로 수정했고, 같은 기존 AST 검사 함수의 범위를
명시한 공개 source closure로 제한한 local 투영도 통과했다. 이는 전체 CI 검사를
대신하지 않는다. 보호 원본 때문에 local 전체 파일 순회는 실행하지 않았다.
실패한 CI·기존 source manifest를 보존하며 새로운 최종 source로 다시 검토한다.
이때 실제 공시/가격 접근·학습은0이고 실제 실행 재시도도 아니다.

Goal의 독립 O1 준비를 같은 최종 검증 묶음에 포함했다. 새 observer IO는 이미
받은 canonical raw receipt만 typed core로 변환하고 exact Decimal·unknown과
NO_ORDER를 보존한다. 기존 native storage 기본요소로 새 private journal을
독점 생성하고 append마다 fsync한다. 완료 prefix digest와0400 seal 없이는
완료된 replay로 취급하지 않는다. replay는 저장 상태를 oracle로 쓰지 않고
raw receipt를 다시 core에 적용한다. 쓰기 실패·불완전 tail·변조는 원문을 남기고
거절하며 자동 truncate/resume/복구 경로는 없다.

상위 directory descriptor마다 `.git` 존재를 nofollow stat으로 검사해 현재
worktree뿐 아니라 다른 native worktree와 operational checkout도 거절한다.
외부 transport·인증정보·실시간 export·모델 추론·주문·운영 signal 경로는 없다.
이는 연결을 위한 offline 지속 기록 경계이며 실제 live connector나 계정 권한,
수집 가동·체결 검증의 완료가 아니다. core와 IO 지정 합성99개는3.50초에 통과했다.
이 두 파일은 G1 실행기 dependency graph/고정14파일 밖의 독립 source이며,
G1 과학/자원/원래 대상/공시 조회 계약은 변경하지 않는다.

### PR386 완료, 첫 공시 수집의 검산 실패와 저장 원문 보완

PR386은 최종 별도 소스·연구 검토와 네 필수 검사 각각의 push/PR 실행 모두가
통과한 뒤 정상 머지했다. 격리 GCP 연구 checkout은 같은 tree로 반영했고 운영
수집기 checkout은 변경하지 않았다. PR 이벤트만 반환하는 workflow connector를
전체 check-run 목록으로 오해했던 중간 보고는 정정했다. 보호 규칙 우회는 없다.

private WSL 제어기의 앞선 두 시작 시도는 기존 메모리 한도에서 SSH 전에
종료됐다. 불필요한 worker import와 최대 크기 버퍼, 명령 parser import를 줄이고
정확한 전체 CLI의 source/Git 시작 경로를 별도로 확인했다. 이 admission 검사는
qualification=false이며 전체 실행 자격으로 쓰이지 않는다. 최종 소스 재검토와
같은 한도에서 전체 SSH 발명 profile REV3를 완료했고 별도 검토자가 저장된 원본
223개 산출물과54MiB·5400행·18코드, canonical·자원·소스 영수증을 감사했다.
원본 발명 결과는 실제 수집 완료나 과거 공시 완전성의 증거가 아니다.

그 뒤 첫 실제 공시 단위를 한 번 실행했으나 독립 검산에서 실패했다. 원본
수집 파서는 목록을 받아들였지만, 독립 검산기의 비중첩 div regex가 바깥 layout
wrapper와 안쪽 pageInfo를 하나로 소비해 페이지 정보 컨테이너를 놓쳤다.
별도 원본 실패 감사에서 이 구조 차이를 확인했고 같은 오류를 발명 중첩
wrapper로 재현했다. 원래 실패 기록·저장 응답은 보존하며 수집 재시도는 없다.
검산 전 producer의 행·지원 수는 아직 채택하지 않는다.

독립 구조 검사에서 정확한 raw container span을 추적하도록 수정해 producer
tree나 parser를 oracle로 사용하지 않고 기존 행·정정·일자·pagination 검산을
유지했다. 관련 지정 발명 검사128개가7.64초에 통과했다. 이 local 결과는 최종
소스 검토·CI·정상 머지나 보완 단위의 전체 자격을 대신하지 않는다.

Goal이 허용한 한 차례 보완은 저장된 원문의 재검산으로 등록한다. 추가 API·DB
조회나 producer 수집 재실행 없이 원래 응답·실패 장부를 SHA로 고정하고, 새
단위의 started 이후 원문을 읽어 독립 검산 결과를 새 독점 경로에 저장한다.
원래 실패를 성공으로 덮어쓰지 않는다. 기존 일반 자원 분할·자동 실제 재시도
금지·원래 월별 대상·공시의 date-only 및 역사적 보존 한계는 유지한다.
수익 비교·시장 모델 fit과 관측기 가동은 아직0이다.

보완 등록은 `intraday-ed-disclosure-saved-repair-v1.json`이며 실제 원본78개
파일·이전14 source·두 controller·실패 run identity를 외부 감사 SHA로 고정했다.
새 실행기는 기존 logger·lock·budget·source/정상 merge 증명·독점 저장을 재사용한다.
actual은 producer 수집을 호출하지 않고 독립 원문 검산만 수행한다. 발명 profile은
원본222개와 새 영수증7개, 총229개 산출물의 전체 저장·검산 경계를 확인한다.
새 지정 합성21개가2.02초에 통과했고 source-only 검증은 실제 접근0이었다.

### 반복 CI 대기 축소

PR386에서는 feature push와 PR이 같은 전체 Python/Java 검사를 각각 실행했다.
PR Python 완료 뒤 push Python을 더 기다린 사실을 근거로 두 비용 큰 workflow의
push trigger만 main으로 제한한다. 모든 PR은 경로 필터 없이 같은 전체 검사와
같은 required job 이름·권한·격리 검사를 실행한다. main merge 후 push 검사도
유지한다. 짧은 gitleaks/hostname workflow는 모든 push와 PR에 그대로 남긴다.
따라서 PR을 만들기 전 feature push에는 두 전체 테스트가 없고, 정상 PR 머지의
네 필수 검사는 계속 필요하다. 검사 내용·보호 규칙을 생략하거나 바꾸지 않는다.
공식 GitHub workflow syntax의 push branches/event 조합에 따른 변경이며,
별도 최종 검토와 새 PR의 실제 네 검사·정상 merge로 확인한다.

### PR387 완료와 단 한 번의 저장 원문 보완 채택

PR387은 source `f7cbf0c7ffaa18c84fabd460be45c8a1f3462ec5`의 별도 소스·연구
검토와 네 필수 CI를 마치고 `585475faeadf19e325f42f377b55be5dd63acb9e`로
정상 머지했다. Python CI는13117개 통과·기존 skip3·subtest169였고,
격리 GCP 연구 checkout과 WSL의 source17개 SHA/tree가 일치했다. 운영 수집기
checkout `4ce85d714890b87f1a57ae89d4942660e41c0483`은 변경하지 않았다.

같은 소스·자원 분할에서 전체 발명 profile을 완료한 뒤 원래78개 파일만
사용하는 실제 저장 보완을 한 번 실행했다. run
`d3406da1-d8cd-4584-ac54-4fb2e868ee53`은 완료됐고 별도 원본 영수증 감사도
통과했다. 현재 조회 목록 지원은14코드·61행, 미확인4코드다. 보완의 새 HTTP는0,
원래 수집18회는 그 저장 응답의 출처다. 원래 실패 run은 그대로 실패이며 원문을
고쳐 쓰거나 재수집하지 않았다. 이 수치는 당시 공시 전체의 완전성·정확한 장중
공개시각·과거 issuer identity·보고서 의미를 모두 검증한 수치가 아니다.
등록된 수집과 보완은 모두 소진했으므로 다시 실행하지 않는다.

### G2 공통 입력·계좌·모델 비교 구현

새 공통 등록은 `intraday-ed-h1-common-comparison-v1.json`이다. 기존 월별 당시
대상·257개 날짜와 공백을 보존하고 원본42파일(CB·CC·CX와 공시 원문·영수증)을
받는 입력 계약, 한 공통 snapshot을 재사용하는 후속 입력 계약을 명시한다.
과거의 EC 세 파일 계약과 기존 arm/clock/cost guard는 별도로 유지한다.

입력 경계는 원본 gzip EOF·압축/해제 SHA·행수와 원문 바이트 캐시를 검증한다.
공시 분류는 date-only 시각 가정과 annotation unknown을 남긴다. 공통30분 입력과
206개 특성, 첫 광범위 부모, 조건부 진입 거절·close 기반 손절·60분 cap,
정수 수량·연속 위험·sticky unknown 회계를 연결한다. fixed causal rolling
지표와 학습으로 맞추는 전처리를 구분한 규칙도 등록·CLAUDE에 함께 명시한다.
평가 구간의 이미 알려진 과거 raw를 고정 지표에 쓰는 것은 허용하되,
평가 정답이나 미래 raw로 지표·학습·선정 기준을 고르지 않는다.

독립 snapshot 검산은 producer 부모·특성·라벨 함수를 호출하지 않고 원자료에서
모든 좌표를 재계산한다. 독립 계좌 검산은 자체 초기자본과 연속 상태를 유지해
모든 현금·수량·이벤트·NAV를 비교한다. flat/event-free 분의 반복 계산 생략은
이벤트·보유 중 매분·개장·종가·최종 상태를 보존하는 동등성 검사 대상이다.
조건부 관측 범위를 전체 모집단 계좌로 재명명할 수 없도록 carry의 domain과
시각 가정을 고정한다. no-fill의 학습 종료시각은 진입 거절시각이며 실제 거래
최소 표본에는 포함하지 않는다. 모델의 각 fold는 학습 날짜의 라벨만 투영한다.

지정 회귀8파일495개, 새 통합7파일173개, 이후 계좌·비교 수정3파일68개가 각각
통과했다. 서로 겹치는 검사이므로 합산한 고유 검사 수로 보고하지 않는다.
이는 전체 CI·최종 소스 검토·정상 머지·전체 발명 자격이나 실제 실험의 완료가
아니다. 메서드 저장 실행기와 정확한 수치 환경·소스 바인딩은 계속 연결 중이며
현재 `actual_runner_ready=false`다. 새 시장 fit·성과 비교는0이다.

H2의 공식 과거 업종/동기화 분봉 입력은 아직 지원이 확인되지 않아 미실행이다.
TCN의 선택한 최소 backend는 해당 일반 자원 분할에서 cold import에 실패했으며
이는 시장 전략 실패나 모든 시계열 모델 실패라는 결과가 아니다. LightGBM의
작은500×206 발명 fit은 기술 검증을 통과했으나 전체 실행 규모의 자격은 아니다.
관측기는 offline 영수증·replay 준비만 완료했으며 실제 관측 가동·주문은0이다.

### G2 전 구간 합성 연결과 CPU 한도 결정

새 요약 검산기는 정책·종목/시기 집중도·일별 계좌·원래157일 분모·동일 자금
대조·매칭을 각각 재계산한다. 이전 완료 결과를 재사용할 때는 SHA뿐 아니라
원래 canonical 완료 span과 source/runtime/input binding도 확인한다. 과거 span은
고정 종료 offset까지만 읽어, 뒤에 새 장부 기록이 추가돼도 이전 완료를 검증한다.
전송·계좌 요약과 관련한 지정170개가15.62초, 이후 저장본·실행·독립 검산의
지정110개가194.97초에 통과했다. 두 숫자는 서로 겹치므로 합산하지 않는다.

로컬 발명 진단v1은 exit1·완료 영수증 없음으로 종료됐고 당시 /tmp 산출물도
확인할 수 없어 시작·원인을 확정하지 않는다. 그 원인을 CPU나 메모리 실패로
단정하지 않는다. 이후 진단은 WSL home의 새 독점 경로와 시작·단계 영수증을
사용한다. v2는 CPU180 한도에서 생산 중 중단됐고154개 session·6120개 coordinate
접두부만 남았다. 독립 과학 검산은 시작하지 못했고 결과를 채택하지 않는다.

producer에는 종목·과거 날짜·정확한30분 offset별 최대440개 참조 캐시를 추가했다.
독립 검산기는 별도 캐시를 유지하며 producer의 과학 중간값을 oracle로 받지 않는다.
생성 객체를 저장하면서 다시 타입 객체로 복원하던 중복 작업도 분리했다. 기본
reader/writer의 타입 검증은 유지하며, 고정 실행기의 빠른 경로 역시 순서·원래
분모·canonical bytes·footer·EOF·SHA와 전 행 원문 독립 재계산을 통과해야 채택된다.
빠른 경로의 저장 바이트 동일성과 재해시된 오계산 거절을 포함한 지정112개가
56.90초에 통과했다. 이것은 실제 실행 자격이 아니다.

그 경로의 v3 진단도 CPU175.004초에서 영수증을 보존하고 중단했다. 당시 AS 표본은
107110400 bytes였고198개 session·7895개 coordinate 접두부만 남았다. 메모리 한도
초과나 전략의 손익 실패를 관찰한 결과가 아니다. 전체 생산·독립 검산 완료는0이다.

운영자는 `call_999db0d0f39f4712be1e7a41a28b59a8`의1번을 선택해 이 ED 지원
분기에 공유CPU600을 승인했다. worker는480, 나머지 역할의 CPU60/40/20 및 Git
예약은 유지한다. 공유메모리512MiB·worker352MiB·벽시계1200초·oneCPU·nice10·
serial·독립 검산·자동 actual retry 금지는 그대로다. `ed-h1-cpu600-v1`은 정확한
ED 등록/단위에만 적용하며 다른 연구나 H2/TCN에 승계하지 않는다. 새 자원 프로필,
공개 등록·예산 영수증·private 실행기를 함께 변경하고 지정 검증·별도 리뷰·CI·
정상 머지·전체 발명 실행을 완료한 뒤 실제 단위를 진행한다. 증설·구매·수집기·
인증정보·위험·자금·주문 변경은 없다. 새 시장 fit·성과 비교는 여전히0이다.

### G2 CPU600 로컬 발명 진단과 실행 준비 선언

새 독점 경로의 v4는257일·원래10280좌표와 원본 규모의 발명 가격·거래량을
생성하고, 저장본의 모든 좌표를 원자료에서 독립 재계산하여 완료했다. CPU는
396.446314203초, 종료 AS 표본은109232128 bytes, 저장본은44452009 bytes였다.
이것은 로컬 단일 worker 진단이며 전체 SSH·canonical 장부·원격 저장 경로의
실행 자격이 아니다. 이전 실패 v1~v3와 원본 영수증은 그대로 보존한다.

등록의 과학 숫자와 공개 runtime 계약을 고정하고 core3 단위의 구현 준비를
선언한다. 조건부 정보 제거 단위는 아직 닫혀 있다. 구현 준비는 실행 허가나
검증 완료가 아니며 최종 소스 바인딩·별도 두 검토·필수 CI·정상 머지와 각
단위의 새 전체 발명 자격을 모두 통과하기 전에는 actual을 시작하지 않는다.
이 단계의 시장 fit과 성과 비교는0이다. 새 자료 수집과 TCN 재시도는 없다.

최종 지정19파일 검사는913 passed·1 skipped(249.09초)였다. 별도 private
실행기21검사도 통과했다(11.71초). 최초 묶음은 draft 상태를 가정하던 발명
fixture가 ready 전환과 맞지 않아 중단됐고, fixture를 명시적 draft/ready로
분리한 뒤 재검사했다. private 단독 검사의 최초16개 import 실패는 프로젝트
pytest 설정을 명시해 해결했으며 운영 경계를 완화하지 않았다.

별도 소스 검토에서 private 실행기의 일반 import가 검토된 .py 대신 동일한
mtime/size의 오래된 pyc 또는 stdlib 이름을 가린 파일을 실행할 수 있음을
지적했다. 실행 전 전체 고정 roster/SHA를 인증하는 source-only finder로
고쳤고, worker의 지연 import까지 같은 경계를 유지한다. 별도 프로세스 검사로
일치 pyc·stdlib/project shadow·미등록/변경된 lazy source 거절을 확인했다.
명시적 writer AST35파일·staged guardrail·diff check도 통과했다. 최종 두 검토와
필수 CI·정상 머지·각 전체 발명 자격은 별도 단계이며 아직 완료로 세지 않는다.
