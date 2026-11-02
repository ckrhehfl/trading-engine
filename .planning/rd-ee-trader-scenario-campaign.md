# Research Direction Task EE — 트레이더 시나리오 원문 대조와 고정 상태 전이

## 운영자 결정과 이번 작업

2026-11-02 운영자는 웨돔·제이트레이더·부자컨닝·김민겸 조사 자료 전체를 계열별로 깊게 연구하는 새 Goal을 요청했다. 각 계열 최소10개 심화 질문과 결과가 없을 때의 구체적인 후속 경로를 요구했다. call_b9d59ca9a528434dbae780fa5776ea39에서 '새 수익 실험도 포함해 진행'을 선택해 직전의 새 성과 계산 보류를 해제했다.

Goal은 당시 국내 상위 약20개 보통주·개인 실행 가능 현물 당일 매매 범위를 유지한다. 기존 자금·위험·예약 자료·자원·결론·승격 경계는 CLAUDE.md를 따른다. 이번 등록은 위험 정책의 교체가 아니다. 연구는 로컬 WSL이며 GCP 수집기·인증정보·주문 경로에 접근하지 않는다.

이번 소스 작업은 세 기본 명세와 **순수 사건 판정기·발명 테스트**다. 실제 원문 reader·성과 실행기·계좌·학습기·관측 활성화를 포함하지 않는다. 새 시장 행 접근은0이다. 실제 비교는 별도 원천 투영·소스/실행 환경·자원·전체 발명 자격·독립 검산 및 리뷰/CI/정상 머지를 충족한 등록 단위에서만 수행한다.

## 조사 자료의 역할과 증거 수준

기존 조사에서는 네 출처에서 각각5편, 총20편의 관련 자막·설명을 검토했다. 모든 차트를 전체 시청한 것은 아니며,20개 독립 전략이나20개 수익 검증을 뜻하지 않는다. 후속 공개 교육 원문6개를 본문 대조했다.

| 계열/공통 기능 | 원자료 | 가져올 관점과 한계 |
| --- | --- | --- |
| A 첫 조정 후 재출발 | 웨돔 W2, 제이트레이더 J4·J5, 아래 SMB 글 | 큰 주기·주요 가격대·첫 조정 순번·작은 주기 재개. 해외선물 매도·틱 차트와 국내 현물 분봉은 다름 |
| B 영역 바깥 시도 실패 후 회복 | 제이트레이더 J3 및 J1·J2, 아래 MarketGauge | 이전 거래 영역과 시작 위치, 하락 진행 실패·회복/바닥 돌파. 실제 가격별 체결 없이 거래량 프로파일·흡수라고 인증하지 않음 |
| C 새 재료와 관심 집중 | 부자컨닝 B2·B5, SMB In Play 설명 | 당일 주목 이유와 사건 내용·시간·가격 반응. 편집 채널의 수치를 소개된 트레이더 원본 확정값으로 쓰지 않음 |
| 시나리오 대응·위험 | 웨돔 W1·W3·W4·W5, 부자컨닝 B1·B3·B4 | 가설 철회·예상 불일치·거래하지 않음·새 근거의 재진입. 위험 요약 자체를 별도 진입 전략으로 세지 않음 |
| 조합·검증 | 김민겸 K1~K5 | 비용·시점·중복 노출·관측 밖 평가·자동화. 대회 알파나 재무 이야기를 국내 분봉 수익 증거로 바꾸지 않음 |

직접 연결한 공개 원문:

- [제이트레이더 J4 진입 설명](https://www.youtube.com/watch?v=fFpeL71ZOJQ), [J5 다중 주기 설명](https://www.youtube.com/watch?v=sfNMSipN_SQ).
- [J3 거래 영역/초기 범위 설명](https://www.youtube.com/watch?v=Hl-ZefT_Md0), [J1 체결 반응](https://www.youtube.com/watch?v=R_LCGesep9c), [J2 CVD](https://www.youtube.com/watch?v=H9n2ISFLf7g).
- [부자컨닝 B2 우메키 해설](https://www.youtube.com/watch?v=BrC96Iv37qs), [B5 장전 고점 해설](https://www.youtube.com/watch?v=Yqxs14n6iVQ).
- [김민겸 K1 유퀴즈](https://www.youtube.com/watch?v=EbRTGBH-vJ4), [K2 조합·검증](https://www.youtube.com/watch?v=5avgkEHjBeY), [K4 평가 구간·비용](https://www.youtube.com/watch?v=_UU2ikCqyVo).
- [Jeff Davis의 Show Me Entry](https://www.smbtraining.com/blog/kiss-keep-it-simple-trading-lesson-the-%E2%80%9Cshow-me%E2%80%9D-entry-for-pullbacks), [Steven Spencer의 Higher Time Frame Primer](https://www.smbtraining.com/blog/higher-time-frame-webinar-primer).
- [Mike Bellafiore의 Getting Back In](https://www.smbtraining.com/blog/getting-back-in), [SMB Gap Give and Go](https://smbpowerpoints.s3.amazonaws.com/Gap_Give_and_Go_Cheat_Sheet.pdf), [SMB Stocks In Play 설명](https://www.smbtraining.com/about_4).
- [Geoff Bysshe의 Trading the10OClock Bulls](https://www.marketgauge.com/TradingThe10OclockBulls.pdf), 큰 주기/OR 저점 회복 부분 선별 검토. 전체72쪽 정독이나 모든 그림 독립 확인으로 세지 않음.

SMB의 개장 갭 조정 원문은3~7분 압축·1분 범위의 봉 중 돌파와 별도 재시도를 설명한다. MarketGauge의 저점 회복 본문에는 즉시 회복과 약20분 바닥 뒤 재돌파가 별개로 나온다. 따라서 최근 v1의5분 세 봉 확인은 모든 원형의 공통 규칙이 아니다. 아래 EE 숫자와 종가 기반 체결 대리는 **우리가 고정한 연구 제안**이며 원본 트레이더의 실제 알고리즘 재현이라고 부르지 않는다.

## 계열별 심화 질문 36개

| 계열 | 출처와 연결해 조사할12개 질문 |
| --- | --- |
| A | A01 큰 주기/진입 주기 정렬, A02 선을 전일 고점으로 대체해도 되는가, A03 사전 관심·참여, A04 시장·업종 상대 강도, A05 첫 조정의 순번, A06 깊이·시간, A07 거래량 감소와 진짜 흡수의 차이, A08 큰 가격대/작은 지지 정렬, A09 다음 장애물·비용 공간, A10 기대 반응 불발, A11 T 위 유지와 재회복의 재시작 차이, A12 추세 노출과 신호 추가 가치 |
| B | B01 진짜 거래량 영역/가격 범위 구분, B02 시작 위치, B03 이탈·복귀·체류, B04 체결 흡수/봉 대리치, B05 테스트 순번, B06 현물 롱 표현, B07 남은 공간, B08 사전 환경, B09 재이탈 무효화, B10 회귀 불발·종료, B11 새 정보의 재시도, B12 단순 반등 대비 영역 정보 |
| C | C01 당시 사건 내용·수정, C02 공개·수집 시각 구분, C03 동시간 참여, C04 국가·장전·KRX/NXT 구분, C05 선행/돌파/종가 확인, C06 거래 대비 진행, C07 공통 시장과 개별 내용, C08 사건 나이·시도 순번, C09 실제 VWAP/근사 구분, C10 예상 불일치 대응, C11 새 사건의 재진입, C12 같은 차트에서 정보 선정의 추가 가치 |

질문별 원문 의미·필요 입력을 검토한 것과 국내 실제 차트·독립 성과 검증을 완료한 것은 별도로 기록한다. 미지원 질문은 구체적인 원천/정보 부족으로 남기고 해당 계열 전체를 폐쇄하지 않는다.

## 기존 실험과 구분

- EA-A는 첫60분 상승·두 연속 하락 종가·다음 세 봉 회복과 거래량/동료 맥락이었다. EE-A는 동결한 개장 가격대, 돌파→첫 조정→높아진 저점→별도 확인이라는 상태 순서와15분 배경을 구분한다.
- EA-B는 개장30분 하단 이탈 후 다음 세 봉 안 빠른 회복이었다. EE-B는 하락 봉 이후 네 봉의 바닥 형성과 그 뒤 재돌파를 별도 검사한다. 빠른 회복 대조와 진입 시각이 달라 결과를 순수 선정 효과라고 주장하지 않는다.
- EC는 기존 EA 신호를 고정한 공동 계좌였다. 새 신호를 옛 신호 마스크로 제한하지 않고 순수 회계만 재사용한다.
- ED-H1의 날짜/제목 플래그와 같은 시험은 반복하지 않는다. EE-C는 원래 보고서의 비교 가능한 영업손익 내용이 있어야 하며, 목록만 있으면 미지원/미확인이다.
- 최근 SK하이닉스 세 날짜의 전일 고점 v1 최초 진입 불성립은 보존한다. EE가 그 기록을 고쳐 쓰거나 해당 세 날짜를 미사용 검증 자료로 만드는 것은 아니다.

## 고정한 첫 명세

정확한 숫자·정의·상한은 `configs/research/discovery/intraday-trader-scenarios-v1.json`을 이번 연구 계약으로 사용한다. 이는 실거래 위험 설정이 아니다.

### 공통 입력과 결과

5분 완성 봉·선택 당시 가용 정보·정확한 가격 단위만 받는다. 원래 당시 월별 종목군과 두 정보 가용 정책, 원래 발견 기간과 미확인 좌표를 보존한다. `retrieved_at`은 역사적 `available_at`을 입증하지 않는다. 공급자 수정 단위·원본 vintage·최종성은 확인된 범위 이상으로 인증하지 않는다.

판정기는 IO·주문·손익·계좌 기능이 없는 순수 함수다. 원래 부모 사건·확인 봉·동결한 손절/목표·선정/거절/미확인·시도·취소 이유를 남긴다. 결측/잘못된 단위·시점/관측된 조건 불충족을 구분한다. 충분한 연속 원인 구간이 없는 부재를 '신호 없음'으로 확정하지 않는다. 완성된 앞 사건 뒤의 공백은 그 사건을 지우지 않는다.

### A

개장30분 고가를 미리 고정한 가격 범위 대리 기준선으로 쓴다. 첫 종가 돌파에서 이미 완성된15분 두 구간의 상승 배경을 동결한다. 이후 첫 낮아진 종가가 조정이며, 그 저점을 낮추지 않는 첫 높은 저점 봉과 그 다음 별도 고가 초과 확인을 기다린다. 확인은 조정 뒤 여섯 봉 안이다. 선 이하 종가나 조정 저점 갱신은 취소한다. 조정 저점을 손절, 첫 움직임 고점을 목표로 동결한다.

15분 맥락 제거 대조는 같은 가격 사건·진입/청산·지원 범위를 사용한다. 원문의 모든 중요한 가격대를 이 개장 고가 대리치가 대신한다고 주장하지 않는다.

### B

개장30분 저가를 첫 종가로 이탈한 부모 사건을 고정한다. 넓은 이탈 봉을 제외한 다음 네 봉이 이탈 저점을 낮추지 않아야 한다. 이 네 봉의 고가를 동결한 뒤 다음 세 봉 안 그 고가와 개장 저가를 모두 종가로 회복해야 한다. 부모 저점·개장 고가는 손절/목표다. 저점 갱신을 새 바닥으로 재설정하지 않는다.

빠른 회복 대조는 같은 부모 이후 첫 개장 저가 초과 종가를 등록된 전체 기한 안에 사용한다. 두 정책의 시간·선정이 함께 다르므로 이를 분리한 후속 비교 없이 '바닥 선별만의 효과'라고 부르지 않는다.

### C

동일 분기·이전 연도·동일 통화/단위·연결 기준의 원본 보고 손익을 요구한다. 현재 보고 영업이익 양수와 전년 비교 영업손익0이하를 고정된 내용 조건으로 둔다. 이는 우리 첫 가설이며 애널리스트 예상 대비 서프라이즈나 최초 실적 공개 자체의 증거가 아니다.

이 정보가 알려진 첫 적격 세션의 첫5봉 고가 초과 부모를 사용하며 앞선 다섯 봉 저점을 손절, 신호 종가와 그 손절로부터2R 목표를 계산한다. 내용 제거 대조는 같은 부모/시각/계산을 유지한다. 원래 양쪽 보고서·시점·이전 잠정 공시의 지원이 없으면 미확인이다. 누락된 보고를 좋은 뉴스·나쁜 뉴스·현금0으로 치환하지 않는다. 현물 실험의 실제 내용 지원은 별도 감사 사항이다.

### 청산과 재진입

향후 경로 어댑터는 신호 뒤 첫 가능한 분 시가 대리 진입을 사용한다. 구조 종가 무효화·사전 목표 종가·기대 반응 불발·시간 종료의 우선순위를 동결한다. 완성 봉 가용 지연과 다음 시가를 지키며 같은 신호 종가 체결이나 고저가 터치의 확정 체결을 만들지 않는다. 동일 분 안 저점/목표 순서는 별도 미확인 진단이다.

A/B의 추가 자격은 최대 한 번이며 이전 취소/청산 시각이 있어야 한다. A는 이전 고점과 기준선보다 높은 새 완성 종가 뒤 새 조정·확인, B는 새 하단 이탈 뒤 새 바닥·확인이다. 계속 T 위라는 이유만으로 시계를 재시작하지 않는다. C는 같은 사실로 자동 재진입하지 않는다. 판정기는 자격만 내며 실제 보유·현금·위험 판단은 계좌 어댑터에 남는다. 이 정책의 재진입은 실패한 연구 실행의 자동 재시도 허용과 무관하다.

## 제한된 후속 탐색과 완료 기준

계열별 기본1개와 정보/기전이 다른 후속 최대2개, 단독 최대9개 및 지원되는 상위 두 정책의 공동운용1개를 이번 라운드 상한으로 둔다. 대조·fit·시계·비용·민감도·미실행까지 전체 시도 장부를 별도로 둔다. 새 데이터 결과를 보고 무한 임계값을 변경하지 않는다.

거래 없음은 분모·지원·탈락·원문 번역을 점검하고 다음 기전으로 진행한다. 비용 전 약함과 비용 후 약함·한때 움직였다 반납·시기 집중·빈도 부족·기술 실패를 구분한다. 결측과 작은 표본은 운영자가 승인한 제한 탐색으로 명시하며 같은 질문을 반복하지 않는다. 유료/예약/위험/거래/방향 폐쇄/정착 결론 경계는 유지한다.

마감 산출은 전 계열 근거·전 시도와 미지원 이유·비교와 불확실성·후속 검증 후보 또는 미확보 이유·주문 없는 관측 준비의 차이다. 후보 발견 자체를 보장하지 않는다. 순수 판정기·테스트가 통과해도 Goal 전체의 완료나 시장 성과를 뜻하지 않는다.

## 원천과 구현 재사용

로컬 EC/ED 캐시 metadata 대조에서 기존 원문 SQLite와 압축 스냅샷 파일이 있음을 확인했다. 실제 내용·해시·완전성·재사용 자격은 이번 소스로 인증하지 않았다. 기존 ED/EC 실제 실행기는 다시 실행하지 않는다.

`intraday_pattern_events.Bar`와 원인 구간 검사를 재사용한다. 시점/금액·순수 회계는 기존 helper로 연결하며 네트워크가 있는 `krx_instrument_cost.py`를 호출하지 않는다.33/43bp 가정은 현재 실제 계좌 비용으로 인증하지 않는다. 새로운 전송·저장·학습 프레임워크를 만들지 않는다.

## 검증과 현재 상태

발명 사례에는 A/B/C의 성립·엄격한 경계·취소·미거래·재진입·내용 미확인·미래 정보 거부·단위/기간 오류·부분 구간·미래 suffix 미소비를 포함한다. 독립 원문/연구 검토·필수CI·정상 머지는 별도 완료 증거로 기록한다.

현재는 등록과 순수 판정기를 구현 중이다. 이 문서는 실제 시장 입력이나 새 수익 결과의 완료 기록이 아니다. 최종 소스·테스트·리뷰·출시 영수증은 수행 뒤 여기에 추가한다.

## 순수 판정과 지연 경로 구현 및 지정 검증

intraday_trader_scenarios.py는 세 기본/대조 판정, 동결한 가격대·상태·취소·추가 자격과 C 내용 미확인을 구현했다. intraday_trader_paths.py는 별도 PathPlan에 대한 지연 OPEN·구조/목표/반응/시간 종료, 정확한 가정 비용 가격 라벨을 구현했다. 이는 거래 계좌·실제 체결·원본 reader가 아니다.

별도 fresh-context 구현 작업이 두 모듈과 합성 테스트를 작성했고, 새 통합 테스트는 후보→경로와 JSON/행동 일치를 손검산한다. 원문 이론이나 실제 수익을 증명하는 테스트로 세지 않는다. 주 에이전트의 WSL 지정 실행에서 새 EE 테스트193개가 통과했다. 같은 실행의 계획 인덱스 검사는 새 문서에 따른 개수228→229 갱신 누락 두 곳을 검출했다. 두 문구를 수정한 뒤 인덱스만 재실행해30passed·기존1skip을 확인했다. 실패를 전략 손실이나 실제 실행 실패로 기록하지 않는다.

명령은 python/.venv/bin/python -m pytest -q에 tests/test_intraday_trader_scenarios.py, tests/test_intraday_trader_paths.py, tests/test_intraday_trader_integration.py를 지정했고, 인덱스는 tests/test_planning_index.py다. 보호된 미추적 preflight 두 파일을 읽거나 스테이징·테스트하지 않았고 전체 스캐너/전체 로컬 테스트도 실행하지 않았다. 최종 staged 가드레일·별도 리뷰·CI·정상 머지는 후속 출시 증거다. 이 시점 실제 시장 행 접근·수익 비교는0이다.

## 첫 별도 검토와 비차단 회귀 보강

소스 검토 ee_source_review와 연구 무결성 검토 ee_integrity_review는 각각040ab4218a96cf680260f98c453bf390ea92decf 전체10파일을 독립 검토하고 지정193개 테스트를 재실행했다. 차단 지적은 없었다. 소스 검토가 제안한 '15분 두 종가가 상승해도 세션 시가 이하면 거절' 회귀 한 건을 보강했다. 운영 계산 코드는 바꾸지 않았고 세 EE 테스트의 최종 실행은194passed였다. 최종 테스트/기록 차이의 별도 재확인은 필요하다.

공개 원격 push는 자동 승인 심사가 해당 공개 저장소·새 브랜치 전송의 구체적 승인이 부족하다고 거절해 수행되지 않았다. PR·CI·머지 완료로 보고하지 않는다. 최종 검토 결과를 고정한 뒤 전송 범위만 운영자에게 확인하며, 이를 우회하는 다른 전송 경로를 사용하지 않는다.

## 순수 세션 투영·독립 검산과 전체 로컬 실행 준비

첫 출시 브랜치 codex/ee-trader-scenario-contracts는 최종3e040697e56d6480e8c9e55c6a2029a027e3c592로 보존했다. ee_source_review와 ee_integrity_review는 마지막 회귀 보강 차이까지 재확인해 차단 지적 없음을 회신했다. 공개 전송 승인 질문이 미응답인 동안 이를 push/PR/CI/머지로 간주하거나 다른 경로로 전송하지 않는다. 후속 로컬 준비는 별도 codex/ee-local-input-comparison에서 진행한다.

intraday-trader-local-input-v1.json에 후속 준비의 입력·투영·독립 검산·호스트 경계를 먼저 기록했다. 이 파일의 actual_execution_authorized_by_this_file와 actual_runner_ready는 모두 false다. 새 원본 시장 행 접근이나 실제 비교를 이 준비 등록만으로 실행하지 않는다.

intraday_trader_session.py는 기존 Minute/SessionInputs에서 필요한5분 구간만 투영한다. 가용 시각을 먼저 확인하고 미래 payload를 읽지 않으며, 결측·비관측·가용 지연과 잘못된 입력을 구별한다. 현재 신호가 사용하지 않는 거래량 누락을 가격 누락으로 바꾸지 않는다. 각 정책의 이전 종료 증거를 따로 전달하고 경로 거부를0수익으로 치환하지 않는다. 원천 메타데이터는 보존하되 인증했다고 주장하지 않는다.

intraday_trader_reference.py는 원본 Minute에서 집계·판정·배경·내용 지원·재진입·지연 OPEN과 정확한 가격수익 라벨을 별도로 재구성한다. 생산 판정기/청산기/봉 캐시를 정답으로 호출하지 않는다. 모든 결과 필드·타입·정책 수·이전 사건·가정·불투명 메타데이터를 대조하며, 이 영수증의 주장은 typed-session 수학으로 한정한다. 원본 아카이브·전체 모집단·계좌·실행 장부·출시를 인증하는 것은 아니다.

주 에이전트의 WSL 지정 실행에서 기존194개와 세션64개·독립 검산107개, 합계365개가 통과했다. 실제 가격·원본 압축 본문·SQLite BLOB·기존 성과를 읽지 않았다. 보호된 미추적 preflight 두 파일도 읽거나 테스트하지 않았다.

기존 EC와 최신 context 실행 소스를 대조하니 WSL worker 뒤의 coordinator와 canonical journal은 GCP에 있었다. 기존 local_pipe_test는 발명 전용이며 가짜 SSH sample 때문에 실제 실행 자격으로 사용할 수 없었다. 이를 전체 로컬 실행이 이미 완성된 것으로 보고하지 않는다.

기존 bridge/transport에 EE 고정 unit/spec/input/ordinary 프로필 전용 local_coordinator 분기를 추가했다. worker/controller/coordinator가 같은 로컬 worktree의 별도 실제 PID로 실행되고, journal·locks·marker·cache·output·diagnostics는 고정된 private native WSL root 안에 있어야 한다. SSH와 remote command·socket 생성을 금지한다. 실제 세 역할만 측정하고 기존 SSH 예약분은 사용하지 않으며 다른 역할로 재배분하지 않는다. 기존 EC/ED 원격 모드·자원 예외·실패 기록은 유지한다.

새 로컬 자격 검사는 source/runtime/host/root뿐 아니라 실제 child exit0·ACK·PID·동일 terminal을 결합한다. STARTED 이전 입력 거절, frame 절단·child 실패·ACK 소실·orphan·재실행 거절을 발명 사례로 검사했다. 구현 에이전트 ee_native_lifecycle의 최종 EE lifecycle+transport 지정 실행은133passed였다. 이는 현재 EE 실제 source qualification 또는 실제 시장 실행의 통과가 아니다.

기존 bridge 회귀까지 포함한 마지막 실행은360passed/1failed였다. 직전 실행도359passed/1failed였으며, 각각 기존 EDAS520 worker_network_no_receipt와 worker_network에서 worker EOF 직후 coordinator 종료로 STARTED만 남고 테스트가 두 장부 행을 기대한 경우다. 실패를 삭제하거나 반복 실행의 green으로 대체하지 않는다. 원격 정책·기존 테스트는 변경하지 않았다. ee_native_lifecycle의 읽기 전용 기준3e040697 대조에서는 기존 local_pipe_test와 기본 local_coordinator=False가 유지되고 EOF→예외→child 종료 순서도 기준과 같아 새 원격 회귀 경로를 발견하지 못했다. 기준 소스 재실행이나 발생률 영향 검증은 하지 않았으므로 이 결과는 소스 대조 판단으로 한정한다.

이번 단위의 최종 staged 검사·새 소스/무결성 검토·CI·머지는 아직 별도 출시 작업이다. 실제 A/B 원천 hook·전체 발명 자격·원본 인증·실제 결과 저장 검산도 남아 있다. EE 실제 비교는 D+1/D+2 모두0, 새 수익후보0이며, 이0은 수익 실험의 부정 결과가 아니다. C의 원래 비교 가능한 분기 내용 지원은 여전히 미확인이고 A/B 준비와 분리한다.

## b3edb07 별도 검토와 원래 모집단 연결 준비

로컬 준비11파일을 b3edb07a57a44a37191f163031565855a6c2a471로 WSL Git commit했다. staged 가드레일·공백 검사와 pre-commit gitleaks가 통과했다. 별도 fresh-context ee_local_source_review는 전체 diff와 바인딩 규칙을 확인하고 지정212개를 직접 실행해 통과했다. ee_local_integrity_review는 같은 전체 diff·명세·관련 입력/계산을 검토하고 지정171개를 직접 실행해 통과했다. 둘 다 새 차단 지적은 없었으며 앞선 넓은 회귀의 실패, 실제 원본/전체 자격/계좌/CI/머지 공백을 명시했다. 이 기록이 해당 실패를 해소하지는 않는다.

후속 intraday_trader_inputs.py는 caller가 인증한 원래 UniverseCalendar와 unchanged-byte reader를 받는 IO 없는 iterator다. 전체 metadata를 첫 raw lookup 전에 검사하고, 원래 두 가용 시계의 target·rank·상태·메타데이터를 유지하며 union(session,symbol)을 한 번씩 정렬 방문한다. stored count 미확인 좌표를 제외하거나 이전 EA/ED 마스크를 호출하지 않는다. 생산과 참조는 같은 원본 chunk에서 각각1분 decoder를 거쳐 수학을 대조한다. 현재 raw/decoded 한 세션만 보유하고 세션 전후 reader identity를 확인한다. 외부 원문 경로를 열거나 실제 캐시를 인증한 것은 아니다.

이 iterator의 지원은 A/B와 각 대조의 첫 시도다. C의 원본 내용, 자동 재진입 연결, 정수주·현금·위험 공동 계좌는 이 준비 완료에 포함하지 않는다. 새 준비 JSON에 이 차이를 명시했다. 실제 source hook·전체 원본/geometry 인증·실행 바인딩·저장 출력 검산·전체 발명 자격은 여전히 별도 작업이다. 실제 시장행을 읽기 전에 최종 source 변경의 delta 검토와 CI/정상 머지가 필요하다.

구현 에이전트 ee_original_population_adapter의 새58개 발명 검사가 통과했다. 이후 주 에이전트가 WSL에서 EE 여섯 테스트 파일·새 lifecycle·기존 transport·계획 인덱스를 함께 지정해586passed·기존1skip in16.10s를 확인했다. 이 묶음은 앞서 실패한 넓은 기존 bridge 회귀를 포함하지 않으며 그 실패를 해소하거나 전체 로컬 suite/CI 통과로 주장하지 않는다. 실제 원문·보호 원본·GCP·네트워크에는 접근하지 않았다.

## 원래 세 입력 hook·저장 결과 검산과 실행 준비 등록

다음 Goal turn은 직전 f6e958b 소스/검토를 실제 진전으로 분류하고 Git 상태와 private 진행 기록을 재확인했다. 공개 전송 범위 답변은 아직 없었으며 같은 차단을 우회하지 않았다. 원래 단위의 기술 실패/결과를 다시 실행하거나 수익 숫자를 읽지 않고, 기존 입력·저장 프레임을 연결하는 코드를 진행했다.

새 intraday_trader_unit.py는 기존 UnitHooks/UnitPlan과 ordinary all-local bridge를 쓴다. source-only factory에는 원래 세 파일·월별 모집단·decoded archive pin·과학 명세 hash·출력·자원 프로필을 고정하고, 과학/원문/gzip 모듈은 worker callback까지 지연한다. 입력은 새 단위로 인증 전송된 CB/CC/CX뿐이다. 발명 입력은 새 scratch index를 만들지만 실제 입력은 기존 봉인 raw-byte SQLite를 원래 압축/decoded pin과 완전히 다시 대조하며, 이전 ED cache를 생성·수정하거나 ED 단위를 재실행하지 않는다.

캐시의 물리 위치와 pin 선언에는 사전 등록한 metadata-only 조사만 사용했다. 기존 native received의 파일명·크기·mode를 확인했고, 처음 추정한 native output 경로가 없었다. 기존 caller 소스가 canonical output은 원격이라고 밝혀 GCP로 연결하지 않고, 로컬 completion metadata가 가리킨 native receipts/snapshot-actual-publication.json의 raw_byte_audit identity 필드만 추출했다. 그16,316byte 저장 메타데이터의 캐시 hash462a625697062da1cfdb5a5b96152cd7762b138762d77c6877b044134cf22187와 CC/CX 원래 archive hash·geometry 선언을 새 준비 등록에 연결했다. 가격/volume 본문·SQLite BLOB·snapshot·금융성과 필드는 읽지 않았다. 기존 receipt의 존재를 새 실제 원본 인증으로 대신하지 않는다.

intraday_trader_report.py는 EE 전용 얇은 canonical gzip 보고서다. 원래 calendar/targets·여섯 binding·출력 geometry와 각 union의 typed 첫 시도4정책·원천 metadata·수학 영수증, 최종 summary·prefix digest를 보존한다. 기존 closed scalar codec만 재사용하며, 실제 원천이 만드는 canonical JSON metadata bytes가 아니면 정규화해 숨기지 않고 거부한다. saved verifier는 생산 iterator/판정/청산을 정답으로 호출하지 않고, 원래 raw를 다시 읽어 typed-Minute와 별도 reference로 모든 저장 결과를 대조한다. 공유 raw decoder는 신뢰 경계이며 독립 decoder 구현 검증으로 주장하지 않는다.

헤더/행/종료 순서·정확한 원래 좌표/정책·누락/추가·원래 metadata·단위/시계·Footer·decoded/압축 pin·단일 gzip member EOF를 검사한다. verify_report의 일반 iterable 소비 자체는 압축 EOF 증거가 아니므로, 실제 hook은 read_report를 직접 넘겨 완전히 소진한 뒤에만 full_eof_verified를 기록한다. unknown·decline·refusal은 금융0으로 바꾸지 않는다. raw가격과 per-pathFraction은 정확하게 보존하며 descriptive aggregate만 사전 명시한 Decimal 통계를 쓴다.

주 에이전트 검토에서 target.metadata의1→true/1.0을 header와행 양쪽에 바꾸면 dict equality가 통과할 수 있음을 지적했다. 새 회귀가 수정 전 실패해 이를 재현했고, wire canonical bytes 비교로 수정했다. 또한 path.entry_at만 있으면 declined gate와 미확인 OPEN도 주간 진입에 세던 오류를 수정했다. 확인 진입·조회 시도·OPEN 미확인·경로 거부를 따로 집계하고, 진입 뒤 결과만 미확인인 경우는 진입에 유지한다. 이 수정은 신호·청산 명세나 연구 결과를 보고 바꾼 것이 아니다.

지정 합성 hook 테스트의 첫 실행은 fixture의 worker 디렉터리가 기본755라 기존 private-cache 검사가 거절했다(45passed/2failed). fixture를 실제 bridge처럼700으로 고친 뒤 hook+report92개가 통과했다. strict metadata/진입 집계 수정 후 EE·transport·인덱스의 지정 묶음은685passed·기존1skip in22.73s였다. 이후 아래 readiness 등록과 그 회귀를 보강한 최종 새 unit/report 묶음은100passed in6.22s다. 넓은 기존 bridge fault-case의 앞선 실패는 여전히 보존하며 이 통과로 해소됐다고 하지 않는다.

별도 개발용 전체 규모 발명 worker 진단은257일·5,158union·각clock5,140target과 압축/원문 audit·생산·저장·독립 검산을 CPU130.511486초, peakRSS90,568KiB, wall130.572초에 마쳤다. output은7,377,827byte였다. worker의 기존 hard cap과 낮은 우선순위 안에서 실행했으며 남은 역할의 자원을 빌리지 않았다. 최초 -I/-S 진단 호출은 test fixture만 필요로 하는 pytest를 찾지 못해 native data 디렉터리 생성 전 종료됐고, 개발용 venv 호출을 고쳐 위 진단을 수행했다. 이것은 최종 source/isolated runtime/전체3역할 자격이 아니다. 현재 결과는 마지막 strictness·빈도 수정 전 로드한 코드의 개발 진단으로만 보존한다. 경로와 원래 증거는 var/ee-worker-diagnostic-20261102.json에 있다. 추가 성능 변경은 이 결과로 정당화되지 않아 하지 않았다.

검토 대상 private var/ee-local-runtime.py는 같은 worktree와 새 EE native root,28개 고정 source closure, 실제3역할과 ordinary 프로필을 고정한다. 기존 reviewed-source importer 패턴을 재사용해 파일 hash를 확인한 source bytes만 compile하며 project pyc/sys.path fallback이나 보호 원본 읽기를 거부한다. import/config 작성 자체는 실행·디렉터리 생성·원문·장부 접근을 하지 않는다. 새 로컬 layout 생성은 별도 명시 호출이고 actual은actual1/전체geometry/qualification metadata를 요구한다. 실제 source/Git/정상 머지/전체 자격 검사는 기존 bridge가 다시 강제한다.

격리 cold metadata 진단은 실제 coordinator-parent 한도에서28개 source와 source plan·journal 모듈을 읽되 worker science를 지연하고, 보호 경로를 open 전에 거부했다. 최종 CPU0.113906초·VmPeak29,632KiB였고 실제원문/장부 접근0이다. 이를 현재source whole-path qualification이나 새 source-probe 영수증으로 부르지 않는다.

명세와 CLAUDE Current Scope는 구현된 EE-LOCAL-PRIMARY의 실제1회 등록으로 갱신했다. Goal에서 승인한 유한 탐색의 첫 A/B 가격경로·대조4정책이며, release·최종 별도 검토·필수CI·정상 머지·동일source/runtime의 전체 발명 자격 및 원래 영수증의 별도 감사를 통과해야만 실행된다. readiness=true 자체는 raw read 권한이나 qualification이 아니다. 이전 resource/retry 예외를 승계하지 않는다. C 내용·명시 재진입·정수주 공동계좌·후속 기전과 전체 Goal 감사는 남아 있다. 실제 EE market body와 수익 비교는 여전히0이다.

## Primary 별도 검토 지적과 실행 경계 수정

공개8파일을7cadae0ad9d68c36e62b4472e8f89a8bf84be8b0로 WSL commit했고 staged guardrail·공백·gitleaks가 통과했다. fresh ee_primary_source_review와 ee_primary_integrity_review는 전체diff·관련규칙/원문코드·고정private caller/coldtest를 각각 읽고 지정100개와cold source-plan을 직접 실행했다. 둘 다 private SHA일치를 확인했다.

별도 검토가 actual1/257 고정이 private make_config에만 있고 CLI main은 base64 config를 직접 받아 generic bridge로 넘긴다고 지적했다. 고정 cache/output 경로의 exclusive 생성은 재사용을 막지만, 첫 실행을 미등록 attempt이름이나 틀린 sessions metadata로 기록하는 것은 막지 못했다. 이를 차단 지적으로 받아 public primary_hooks.load_plan 자체에서 실제mode의 exact-str actual1과 exact-int257을 강제했다. private builder를 거치지 않는6개 잘못된 실제 config가 evidence stat/hash 이전에 거절되는 회귀를 추가했다. 기존 공용 bridge·ED/EC·과학·자원/입력 범위는 변경하지 않았다. 수정의 최종 지정 검사와 delta 재검토 결과는 별도 기록한다.

## 공개 전송 승인·첫 CI와 Linux 발명 fixture 수정

운영자는 call_e851eccdf44846f29de3b9b36ee9a05e의1번으로 이번 Goal의 codex/ee-* 브랜치에 검토된 code/docs/invented tests의 공개push·PR·필수CI·정상머지를 명시 승인했다. 이전 자동 심사 차단을 우회한 것이 아니라 그 범위 답변을 받은 후 origin을 검증하고76b37c0을 push해 PR395를 열었다. 원본 시장자료·인증정보·계좌/체결 원장·private var는 전송하지 않았다. 실제/GCP/주문 권한은 확대하지 않았다.

76b37c0의 필수 gitleaks·hostname·Java 검사는 통과했지만 Python run41452543197/job125301036936은6failed·14,589passed·기존3skip·169subtests in1656.84s로 실패했다. 실패6개는 모두 새 local lifecycle fixture였고, native Linux CI에서 production worker의 WSL runtime required 검사가 먼저 거부해 journal이 생성되지 않은 후속 실패였다. 기존 EDAS520 race는 이 실행에서 실패하지 않았지만 그 기록을 없애지는 않는다. 동일 CI를 green까지 반복하지 않고 원인을 수정한다.

production WSL·source·자원 검사는 그대로 두고 발명 driver에서만 일관된 가상 WSL kernel release를 사용하도록 했다. parent의 발명 machine pin도 같은 descriptor에서 계산한다. 실제3PID·wait4·stdio·Git·source byte·CPU/메모리·네트워크 거부는 실제 검사로 유지한다. driver는invented_only=True만 받고 실제 입력에는 사용할 수 없다. 추가 음성 회귀는 가상 native non-WSL release에서 기존 worker 거부가 입력 생성 전에 발생함을 확인한다. 이는 실제 현재 source/runtime qualification을 대체하는 가상 kernel 증거가 아니다. 별도 실제 WSL 전체 qualification은 정상머지 후 그대로 필요하다.

## PR395 정상 출시와 첫 전체 발명 자격의 CPU 실패

최종033393c9effe85c168daf93e3e33033c106415e0의 public2파일 delta와 private launch/collector/auditor 및 합성 테스트5파일을 source ee_primary_source_review와 integrity ee_primary_integrity_review가 각각 재확인했다. lifecycle42개·private60개를 두 검토자가 직접 통과했고, hash 확인 후 같은 bytes를 실행하도록 수정한 helper와 외부 cache/decoded pin 대조를 확인했다. source42passed6.52s·60passed18.85s, integrity42passed7.30s·60passed20.79s였고 새 차단 지적은 없었다.

필수 Python run41458246789/job125320364393은14596passed·기존3skip·169subtests in2755.51s였다. Java41458246736·hostname41458246768·gitleaks41458246834도 success였다. 미해결 GitHub 리뷰 대화0·리뷰 요청 변경0·최종 head 일치를 확인한 후 PR395를 정상2부모 merge4942453b2a625e84b45f0739fee579653225dc2a로 병합했다. merge의 두 번째 부모는033393c9이고 tree9db6e4a2134e2f6c4698973cb36ff79ca285bda1은 검토본과 같다. 같은 worktree를 그 merge에 고정했으며 보호된 미추적 원본은 읽거나 스테이징하지 않았다.

private 고정 실행기로 새 로컬 EE root에서 cold-v1을 한 번 수행했다. 정상 exit0,28source·WSL machine/runtime·merge/tree·한도 readback이 일치했고 시장·장부·marker 접근0이었다. 이어 같은 source/runtime의 전체257일·5158union·각clock5140target genuine3process invented profile-v1을 한 번 실행했다. worker는 저장 보고서의 독립 검산 ee_report_verify_after 중 absolute process/rusage CPU budget에서 실패했다. wait4 진단 CPU180.060138초·peakRSS86872KiB·worker exit1·coordinator 종료-15, controller exit1이었다. 이는 수익이나 신호 실패가 아니라 전체 실행 자격의 기술 실패다. 실제 EE 시장 행 접근·실제 비교는0이다.

원래 profile-v1 launch hash e689be88bf493c467fb0a1a6095b37d1edc736dff000f1cdf9c64f9313055f2b, process failure hash0e1b44ace5b5b18dbd303c6436659c175c661b12d18a308138d74fa200b5e5c6, 원래 invented journal hash46067b0f8466d5711696cc10ef0681c1cadfdd559ffa45ce1a04f2a3cd5a8be8 및 미해결 marker는 private 원래 경로에 보존한다. 실패 출력을 완료 publication·ACK나 actual qualification으로 채택하지 않았고 원래 경로를 지우거나 재사용하지 않았다. ordinary resource 한도를 늘리거나 실제 자동 재시도를 승인받은 것으로 간주하지 않는다.

후속은 codex/ee-verification-reuse에서 신규 작은 발명 자료로 반복 계산을 측정하고 의미가 같은 비용 제거만 진행한다. 새 exclusive cold-v2/profile-v2 caller/collector는 원래 v1 파일을 보존하는 별도 private 파일로 준비한다. 최종 source의 별도 검토·CI·정상 merge와 새 전체 자격·원본 receipt 별도 감사를 통과하기 전 실제 actual1을 시작하지 않는다. C 내용·재진입·공동계좌·근거 있는 후속 기전과 전체 Goal 완료 감사는 여전히 별도 남은 일이다.

## 같은 한도의 반복 검산 비용 측정과 동등 수정

새 발명8일·168union·32642행의 isolated checked-byte source 경로를 cProfile로 측정했다. reference의 OHLC 형식 검사에서 사용하지 않는 Fraction을 반복 생성하고, report 생산·write·read·saved verify에서 같은 typed record를 여러 번 decode하는 비용을 확인했다. 일반 venv cProfile15.567228초와 -I/-S14.943235초의 동일 결과 비교에서는 importer 격리 자체의 slowdown은 재현되지 않았다. 과거130.51초 prototype의 checkpoint는 상태 출력뿐이었고 genuine worker의 자원 readback·receipt를 수행하지 않았으므로 같은 성능 경로로 직접 비교할 수 없다는 제한을 추가한다. 과거 원본 증거는 그대로 보존한다.

수정은 독립 reference의 Decimal 형식·유한·양수 검사를 유지하면서 버리던 Fraction 생성만 제거하고, report_records의 이미 독립 검산한 typed 원본을 동일 record contract에 전달해 생산 단계 roundtrip1회만 생략한다. writer·read·saved verifier의 closed decoder·canonical roundtrip·원본 대조·독립 수학·EOF·자원 checkpoint는 유지한다. Decimal 정밀도·비가격 scalar 거부와 producer 변조·위조 inline receipt의 writer 이전 거부 회귀를 추가했다. shared raw parser 신뢰 경계나 producer/verifier 독립성 범위를 확대해 주장하지 않는다.

진짜 worker budget을 입력 생성 전부터 연결한 같은8일 no-profile 진단에서 수정 전 CPU6.098130초, Fraction 생성 제거 후5.721381초, 최종 typed 생산 handoff 후5.317566초였다. 최종 peakRSS31680KiB, 출력245283byte·SHA84ed1ade9ed73a6796bcaf01e634a52710a3d15146bd5ceccefaf1eee121e76a는 수정 전과 동일했다. 이것은 현재 full257 자격이나 실제 결과가 아니다. 구현 에이전트의 최종 지정 EE8파일543개 검사는16.67초에 통과했다. 최종 source 동결 후 신규 전용 /tmp의 전체257 개발 진단1회를 ordinary worker 한도로 진행하며, 그 결과와 별도 최종 검토·필수CI·정상 merge·새 full profile 자격은 별도 증거로 기록한다.

## 최종 개발 진단 콘솔 증거와 별도 검토

위7파일의 동등 수정은 d7b19ee79e880071ea890ffea9149696385b0a3c로 WSL commit했다. gitleaks·staged guardrail·공백 검사가 통과했다. 주 에이전트는9개 EE 테스트와 문서/계획 인덱스를 지정해669passed·기존1skip in22.66s를 확인했다. 전체 로컬 suite나 보호 원본은 검사하지 않았다.

새 전체257 개발 진단 session39904는 exit0과 완료 JSON을 반환했다. 콘솔 수치 기준 absolute processCPU162.554623678초·rusageCPU162.554623초·peakRSS86556KiB·wall161.799242초였고, 입력 생성 CPU5.212131초를 포함해 genuine budget finish complete·violations[]였다. 원래 worker 한도·nice10·oneCPU·wall 한도를 유지했다. 저장 결과5158session·각clock5140target, 각 CC/CX1002211행·daily2674, independent verified/fullEOF/fullgeometry/allcoordinates true를 반환했다. 출력7384959byte SHA4fd8e66197c0724f08e1cf9f3df9e4d50b77764204b91389b5b654abbd12e327는 그 완료 콘솔의 선언이다.

중요한 증거 제한: 실행 종료 뒤 새 WSL 호출에서 /tmp/ee-small-invented-profile-m_6tehum의 진단·pstats·source/hash 파일이 없음을 관측했다. 원인은 확인하지 못했으며 원래 파일이 영구 보존됐다고 주장하지 않는다. 원래 tool stdout/exit0에서 관측한 값을 private var/ee-development-diagnostic-console-20261103.json에 복원했고 SHA는fc3c1e17c8990cd966be32f0e204516f026a2641f1330cde447640cc20f5fad0이다. 이것은 원본 native 파일·그 파일의 hash/EOF 감사·독립 재현·전체3role qualification이 아닌 개발 capacity 콘솔 증거다. 별도 재진단은 하지 않았다. 최초 genuine profile-v1의 /home 실패 원본은 그대로 보존한다. 최종 release 뒤 새 profile-v2는 영구 /home 경로에서 완료·원본 receipt 별도 감사까지 통과해야 하며 이 콘솔로 대체하지 않는다.

fresh source ee_primary_source_review는 base4942453..d7b19ee 전체7파일과 private v2세 파일 전체/이전 대비 diff·SHA를 검토했고543passed12.46s·private60passed10.24s를 직접 확인했다. fresh integrity ee_primary_integrity_review는 같은 전체 소스·private 차이·과학/한도/실패/주장 경계를 검토하고 지정6파일345passed19.07s를 직접 확인했다. 둘 다 새 차단 지적은 없었다. source는 버리는 Fraction 생성만 제거했음을, 둘 다 writer/read/saved independent/EOF/checkpoint가 유지됨을 확인했다. 콘솔 복원과 원본 소실 제한을 명시하는 데 동의했다. 뒤에 추가하는 이 문서·handoff의 최종 기록 차이는 별도 재확인한다. 새 필수CI·정상 merge·fresh full profile-v2·원본 감사·실제 비교는 이 시점 아직 미완료다.

## PR396 정상 출시·영구 자격과 첫 실제 비교 완료

두 별도 검토자가 최종 e299a48d5a107ed702d2c94935ba680204eb2675의 마지막 문서2파일도 재확인했고 새 차단은 없었다. 필수 Python41470263382/job125361665099은14610passed·기존3skip·169subtests in2891.91s, Java41470263328·gitleaks41470263262·hostname41470263407도 success였다. final head·미해결 리뷰 대화0·정상 merge 가능 상태를 확인한 후 PR396을617fba451c623f11a77a4d7b5d8ac11d018f284f로 정상 병합했다. 두 번째 부모는 e299a48, tree7754fc749ea0983b3da7ba52b9795852bed9f648은 검토본과 일치했다.

같은 worktree의 정확한 머지 소스로 영구 /home EE root에서 cold-v2가 통과했고, 새 profile-v2 genuine3process 전체 발명 실행이 exit0으로 완료됐다. source manifest1b6c5356ec49354da9d2ce758a6fb273c48705f6d3c04189c7d3ab95e3a4470b·runtime/machine·전체257session/5158union/각clock5140target과 EOF/독립 수학·실제 세PID를 대조했다. workerCPU125.029054초·집계CPU126.032319초였고 unused SSH 예약은 그대로였다. 검토된 별도 collector/auditor가 원본 launch/source/budget/process/prepared/publication/journal/ACK와 두 출력 사본을 대조해 PASS했다. publication SHA2214101bae151959caeec20a7617333a3bfccf1c9642f4f2a232cb9a5a453bd8다. 별도 auditor 자체의 범위는 원본 metadata/출력 identity이며, 과학 검산과 전체 qualification은 원래 완료된 bridge/worker 경로와 구분한다.

그 뒤 등록된 primary actual1을 한 번 실행해 exit0·원본 별도 감사 PASS를 확인했다. run3c5129e9-37e6-4a02-a21e-653226551e9e, publication SHA5e093d3b70341c7d217db676ae280b31fcd44dee3d2854d943d75760cce96cee, output SHAab6b952049e2387cf6a68a0f4fe11f967b89383178c108e8312c5cf4cf62d979(10943315byte), summary SHA2a7c11c2b4bf920c94a9c841a980d82bfeece5501aa6eba85bf2d61d65f51b7b다. workerCPU101.407395초·집계102.507795초로 한도 내였다. 원래 세 입력과 기존 봉인 캐시를 인증했고 새 획득·GCP·주문·예약 접근은 없었다. 이 actual1은 소진 상태로 보존하며 재실행하지 않는다.

전체5158개 저장 행을 다시 읽어 독립적으로 entry/exit의 exact Fraction gross/net33/net43 1080등식과 원래 순서의 Decimal50 집계·footer·압축/decoded/prefix hash를 대조했다. 모두 일치했다. 그 저장 결과 전용 감사의 첫 호출은 transport summary hash의 마지막 개행 누락으로 hash assertion에서 종료했으며, 개행을 바로잡은 재독으로 통과했다. 실제 unit·과학·자격의 재실행은 아니다. 개인 사례·장부는 private에만 보존하고 공개하지 않는다.

두 시계의 확인 진입과 numeric label은 같았으며 독립 표본으로 합치지 않는다. S1의 원래5140target,56calendar week에서 아래 결과를 얻었다. 단위는 건당 평균 가격 label bp,0.01%=1bp다.

| 정책 | 가격 후보 | 확인 진입 | numeric/보유후미확인 | gross bp | net33 bp | 관측 주평균 진입 |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| A 첫 조정 | 98 | 4 | 4/0 | 24.033852 | -8.966148 | 0.071429 |
| A 15분 맥락 제거 | 98 | 5 | 5/0 | 30.871914 | -2.128086 | 0.089286 |
| B 바닥 확인 회복 | 80 | 69 | 68/1 | 21.738901 | -11.261099 | 1.232143 |
| B 빠른 회복 | 301 | 284 | 283/1 | 8.794897 | -24.205103 | 5.071429 |

S1 신호 미확인은 A2623·B2593·빠른회복2592, S2는2625·2595·2594다. 이를 no-trade/수익0으로 만들지 않았다. B 보유 후 미확인1개도 null이다. 원래 대상 약 절반의 공백·A의 작은 표본·관측된 경로 선택·소진된 발견 자료·33/43bp 가정비용·가격 단위/체결 미인증 한계 때문에 전체 상위20개·실제 계좌·우위 또는 승격을 인증하지 않는다. 어느 기본안도 관측 주15회 목표나 비용 후 양의 평균을 확보하지 못했다.

## 저장 장부의 기전 진단과 두 후속의 사전 고정

A 정보 통과81개 중 범위 거절64개는 전부 entry>=target, entry<=stop은0이었다. 그64개 중60개는 확인 신호 종가부터 이미 목표 이상,4개만 신호는 범위 안인데 지연 OPEN이 목표 이상이었다. 신호 종가에서 남은 목표 공간은 <=0bp68개·0~33bp11개·>33bp2개였다. 이는 가까운 첫 움직임 고점과 재출발 확인의 순서가 충돌하는 진단이며, 다른 목표의 수익을 계산한 것은 아니다. A 확인4개는 맥락 제거 대조와 같은 시각·label이고 대조의 추가1개뿐이라 맥락 효과 결론을 내리지 않는다.

B 완료68개는 net33 양수17·음수51, target7·structural3·response14·time44였고 별도 미확인1개를 유지했다. 기록 기여는 시기/종목에 집중됐지만 이를 보고 유리한 날짜나 이름을 고르는 필터를 만들지 않는다. B 완료68개는 빠른 회복 대조에도 같은 날짜·종목으로 있으나 같은 시각/label은6개뿐이어서 기존 평균 차이를 순수 선정 또는 바닥 정보 효과로 분리할 수 없다. 원래 순서의 최초 net-positive/negative/통과후거절·보유후미확인 사례를 private 저장 보고서에서 대조했다. 원문 매매자 실제 손익의 성공/실패와는 구분한다.

그 근거와 기존 A09·B08/B12 질문에서 새 intraday-trader-mechanism-followups-v1.json을 성과 계산 전에 고정했다. A-2R-TARGET은 동일 A 확인·stop·시계에서 signal.close+2*(signal.close-stop)을 진입 전에 동결해, 기존 C에 이미 정의했던 risk target을 재사용한다. 목표/편입이 함께 변하므로 pure exit effect라고 하지 않는다. B-POSITIVE-PEERS는 동일 B 진입/청산을 유지하고 당시 다른 대형주15분 방향을 추가하며, 공통지원 대조는 같은 quorum에서 sign만 제거한다. 이는 원문 그대로의 전략이나 이미 입증된 정보가 아닌 우리의 유한 후속 가설이다.

원래 S1/S2 매매 대상 전체는 유지하고, 설명용 peer basket은 같은 session의 원래 S2 보수적20개에서 대상 종목을 제외해 두 target clock에 공통 사용한다. 원래 membership available/selected 시각을 각각 원래 decision 전에 인증하며 S2에서 미래 S1 종목을 사용하지 않는다. 기존 DO에서 고정했던15분 firstOPEN/lastCLOSE·최소5개·모든 complete peer 동등가중·정확0거절 구성을 재사용하고, 부족/결측은 두 B arm 모두 unknown이다. 결과가 좋은 peer/시기·미래 label을 고르지 않는다. 이 basket은 공식 시장/업종 지수나 전체 시장이 아니다.

새 source-only 등록은 실제 readiness=false이며 이번 준비 중 후속 actual은0이다. 구현은 기존 detector/paths·독립 수학 원시·raw source audit와 thin report framing/gzip/EOF를 재사용하고 정확히 primary/default 또는 mechanism1 profile만 닫아 지원한다. primary는 신규 과학 모듈을 eager import하지 않는다. 새 profile은 원래 S2 전체 metadata를 첫 raw lookup 전에 검증하고 하루 private deepcopy cache만 유지하며, saved verifier는 새 cache와 fresh raw decode를 쓴다. decoded chunk/row projection hash와 원본 archive byte 인증을 명확히 구분한다. 별도 source/integrity·필수CI·정상merge·동일 source/runtime의 fresh full qualification·원본 감사를 마친 뒤에만 새 EE-MECHANISM-1 actual1을 허용할 수 있다. 첫 primary의 재시도·C/재진입/계좌 완료로 간주하지 않는다.

## 후속 연결 구현·동일 한도 개발 진단과 readiness 등록

새 followups/followup_reference/followup_inputs는 기존 구조 검출·지연 경로·독립 수학 원시를 재사용하고, report는 정확히 primary/default와 mechanism1만 선택한다. 새 typed record는 원래 target·결과·수학 영수증과 predecision peer 증거를 보존한다. source parser의 문법/단위 신뢰 경계는 그대로이며 production feature를 reference oracle로 쓰지 않는다. primary import는 신규 과학 모듈에 의존하지 않는 fresh 격리 회귀도 확인했다. root의 새 closed hook은 원래 input/scenario bytes를 hash 참조하고 신규 actual1/full257·전용 단위 경로·source/runtime·ordinary profile·공통 lock/journal을 강제한다. 기존 primary actual1 경로나 자원 예외를 빌리는 설정은 첫 증거 접근 전에 거부한다.

원래 Source.load_universe는 원래 day의 targets 외 canonical metadata를 보존한다. 새 iterator는 모든 clock/day의 available_at<=selected_at<session_open, 원래20rank와 동일 day metadata를 첫 raw lookup 전에 닫고, 각 peer가 원래 B decision까지 사용 가능한지 별도로 확인한다. raw CC의 available_at=None·수집시각은 역사 공개시각의 증거로 쓰지 않고 inherited minute-end+2 가정을 명시한다. 필수15분의 결측/conflict/invalid는 구분하며 임의 stale cutoff나 carry-forward를 만들지 않는다.

whole-path 발명 입력에는 네 session mode로 positive/negative/zero/subquorum을 넣되 원래 geometry·행수는 유지하고, 각 session의 한 유효 B anchor 구조를 보존했다. 기존 primary/default 발명 bytes와 실제 입력은 바꾸지 않는다.4일 통합 fixture가 저장·독립 검산 뒤 네 상태를 모두 보존하는 회귀가 통과했다. 새 unit만의 첫 지정검사에서36passed/1failed는 테스트가 schema 이름을 잘못 추정한 경우였고 실제 저장/독립 검산은 통과했다. 등록 schema와 구현의 일치를 검사하도록 고친 뒤 primary/new hook91개가 통과했다.

첫 개발 helper 호출은 source_hashes metadata 누락으로 입력 생성 전에 종료됐다. 그 private 실패 경로 mechanism1-od9_s_5g는 보존했다. 수정한 helper의 첫 전체 개발 실행 mechanism1-nwh2010i는 독립 검산 중 absoluteCPU180.038289에서 실패했다. 오류/한도 readback·source·진행·미검증 출력을 영구 /home 경로에 보존했고 실제 후속은 실행하지 않았다.

새8일 cProfile에서 deepcopy가7,788,815호출·누적8.516초로19.347초 compute의44%였고 codec decode1.054초·fields0.236초였다. 따라서 공유 codec을 바꾸지 않고 raw cache의 builtin JSON dict/list/immutable atoms 복사만 local clone으로 대체했다. memo로 nested mutable/alias/cycle 격리를 유지하고 비JSON 객체에는 기존 deepcopy를 쓴다. cache/producer 변조가 reference 입력이 되지 않는 회귀를 보강했다. payload·입력·검산·EOF·resource checkpoint는 줄이지 않았다.

같은8일 real-budget no-profile 실행은 CPU6.536138→5.695898초였다.168개 SESSION 원문 SHAe202d0cce715a5f2f4926e36875b5c65eea3a742f3c8f2d3f5f8e4a7cf8a4791과 footer summary/counts는 정확히 같았다. source 및 manifest binding, 그에 따른 footer prefix와 원래 whole-file SHA는 다르므로 전체 원본 파일 동일이라고 주장하지 않는다. 전후 원본 비교는 영구 development/mechanism1-small-comparison-3zeu3wok에 보존했다.

최종 clone을 복원한 후 신규 전체 개발 실행 mechanism1-pb61a4hb가 exit0·CPU167.790167초·peakRSS87192KiB·wall164.696701초로 완료됐다. genuine budget finish complete/violations[]·worker 한도·nice10·oneCPU를 유지했고 source manifest는 끝까지 같았다. 전체257session/5158union/각clock5140target·각 CC/CX1002211행·daily2674와 independent/fullEOF를 확인했다. 출력9049006byte SHA55c0a0d2b0052a8c7f54781ee54cedc266484eceebd82e94d64cbbcdd2de9275, 영구 원본 diagnostic.json SHA7325af657bf8a2e2d6d46673407cd1921a87ccb070e9f0d43939e2fcb539e69b를 다음 WSL 호출에서도 재확인했다. 이것은 readiness 갱신 전 개발 capacity 증거이며, 마지막 source의3role qualification을 대신하지 않는다.

이후 구현한 EE-MECHANISM-1 actual1의 readiness/등록을 true로 고정했다. 이는 기존 Goal 안의 새 두 가설·대조 단위이며 actual은 여전히0이다. source-only cold 검사에서34source·VmPeak29728KiB·CPU0.211초·시장/장부0·worker science 지연·보호경로 open 전 거부를 확인했다. 새 private metadata 감사56개와 helper4개도60passed10.58s였다. 최종 source/integrity 검토·필수CI·정상merge·fresh durable whole qualification·원본 영수증 감사는 후속이며, 그 전 실제 접근은 허용하지 않는다.

## 후속 최종 소스 검토와 감사 연결 오류 수정

최종 구현 fea2f3ecaa0b4949f47fd58b6a43c1049ce2cec4는 WSL gitleaks·staged guardrail·공백 검사를 통과한14파일이다. 주 에이전트의 최종 지정 EE/계획/문서 검사는733passed·기존1skip in32.28s였고, 마지막 handoff 뒤 계획/문서 검사84passed·기존1skip in4.34s를 확인했다. 보호된 미추적 preflight 두 파일은 읽거나 스테이징/테스트하지 않았고 전체 로컬 suite/scanner는 실행하지 않았다.

별도 fresh-context source 검토 ee_mechanism_source_review와 연구 무결성 검토 ee_primary_integrity_review가 전체14public diff와 private8파일을 읽었다. 두 검토자는 metadata collector가 새 unit의 registration_sha256에 이전 local-input 등록 hash를 넣어 정상 결과도 거부할 결함을 독립적으로 찾았다. private collector의 실제 main이 사용하는 receipt_expectations를 새 mechanism 등록에 연결했다. 원래 pin을 다시 넣으면 독립 발명 영수증 감사가 FAIL하고 새 pin이면 PASS하는 연결 회귀를 추가했다. 첫 red는 함수 부재, 수정 후 private61passed10.16s였다. 과학·입력·한도·공개 소스는 이 수정으로 바뀌지 않았다.

source 검토자는 지정 공개/비공개221passed28.03s와 수정 후 private61passed10.44s, cold34source·VmPeak29720KiB·CPU0.117581s·시장/장부0·qualificationfalse를 직접 확인했다. integrity 검토자는 최종 지정6파일233passed26.44s를 직접 확인했다. 두 검토 모두 위 결함 수정 후 잔여 차단 지적/권고가 없었고, closed unit 경로·일반 한도·동결2R·선행 가용 S2 peer 공통지원·독립 fresh raw 검산·원본 EOF와 주장 경계를 확인했다. 최종 문서 기록 차이는 별도 재확인한다.

private 최종 SHA256: runtime09b2618c2775773b55c0f6319adb537b35801807dc7b83d6bae4455916a57c2c, cold73821af535c0146a7ba808ad84f9f52b3e330802be750e77359c7a3b7e333311, launchfadb9e79a191433ea4d91bf9adcce0ff49866b6fc21209ac49d37551771b5e7a, collector6187cefc63eeec273eb4379647c83bbb9400b5df1870c16235aa1b8dc58bf963, receipt-auditda443ec80a2abb7d2e663369d187fa1b8de8d66c4c25bfdad500003993614d07, development080036d767929101f9cbcdacf8dc67b3f42ce328d86a2c4b138dcab29543123a, receipt-testse0ceab422e006fb68e157aa9cf5b62631730296d31a4fbc12530686f15d70ebc, helper-tests922ed72f28ed67da3960ce38617d0b9cd700ce0957cb52b7dab0c09397b91cc1。원본 가격/개인 사례/장부와 이 private 파일들은 공개 push에서 제외한다. 새 필수CI·정상merge·정확한 source/runtime의 fresh durable3role qualification·원본 receipt 별도 감사·실제 followup은 아직 미완료이며 개발 capacity로 대체하지 않는다.

## PR397 정상 출시와 두 후속 실제 결과

두 검토자가 최종009706e8affc3ecae8c040d98904094b3fbe43ac 문서 차이도 재확인했다. PR397의 필수 Python41495185823/job125448148380은14674passed·기존3skip·169subtests in2827.00s였고 Java41495185711·gitleaks41495186471·hostname41495185913도 success였다. final head·미해결 thread0·변경요청0 확인 후 정상2부모 merge e61fe665be776a8e28d8fbfb78c328766b439a39를 완료했다. 두 번째 부모는009706e, tree dd41d6002a255e5b441cd6e614df1ce94384aa10은 검토본과 같았다.

같은 소스의 cold-v1은48이 아닌 당시34source를 검사했고 시장/장부/marker0이었다. 이어 새 EE-MECHANISM-1 profile-v1 genuine3role 전체257일/5158union/각clock5140target이 exit0·별도 원본 receipt 감사 PASS였다. source manifest a2e2b1a8786bcc8114aae941a69615ac4cde4c2cc2787337250a4f5c271a37af, workerCPU167.685683·집계168.713454초로 일반 한도를 유지했다. publication SHA7917aa0c699b27708d78216bbf063ea2c84351a603fb1815f0afa691b6356c73이며 원본/독립수학/EOF/세PID와 helper metadata 감사의 범위는 별개로 확인했다.

그 뒤 새 actual1을 한 번 실행해 exit0·원본 receipt 감사 PASS를 확인했다. run e6434089-17e8-4d7f-bade-72c5623a6477, publication SHA56b6fe76631d9e66e931b5791fe49c977dbece7ca56689b3de6034a2317e16dd, output SHAe0750e5fcdc82b58e30715993ecfcb920c595c4a2306db68c188f309ae8e5b0a(11441864byte), summary SHA6fe7b886275936b2c135c6919b0a552eca4e3084405ad4beb9cf7d6921302bed다. workerCPU121.655088·집계122.874626초, raw3/원래 cache와 full decoded/EOF 검산을 유지했다. actual1은 소진됐고 재실행/덮어쓰지 않는다.

별도 ee_saved_case_audit는 허용된 새/이전 저장 보고서와 publication/audit만 읽어 전체5158좌표, 새609 exact Fraction label등식,98개2R 목표,80개peer 산술·시점·quorum과 Decimal50 footer·모든 hash/EOF를 대조했다. 새 저장 감사 JSON SHA1a1e2f2790fcc8894070cfd6d7452a0a9848098260612c2d7815e40ba4450854, source6ebaaa0a71c0dea6346d07151e00d075aa45d4d37a0c707e740e920f1c3c43d3, private MD a09e7bea01cc9b87f1fea14c821b6bff977cc9222f95a7ac31a39b0877ac5640다. 최초 JSON stdout이 잘린 뒤 minified 재독으로 원문을 보존했으며 actual/과학 재실행은 아니다.14개 최초 시간순 개인 사례/경로는 private에만 보존한다.

두 정보가용 가정의 확인 진입/numeric은 같았고 독립 표본으로 합치지 않았다.56원래 calendar weeks에서 다음을 관측했다. bp는0.01%이며 고정 비용 가정 라벨이다.

| 정책 | 확인 진입 | numeric/held unknown | gross bp | net33 bp | net43 bp | 관측 주평균 |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| A frozen2R |81|79/2|-0.156956|-33.156956|-43.156956|1.446429|
| B positive peers |58|57/1|29.705014|-3.294986|-13.294986|1.035714|
| B common peers |68|67/1|21.533676|-11.466324|-21.466324|1.214286|

B 두 arm에는 공통 admission unknown1개도 별도로 있다. S1 신호미확인 A2623/B2593, S2는2625/2595를 원래5140분모에 유지했다. A는 기존4개 유지·추가77·소실0이며 공유4개 new-old 평균은-38.367768bp(2개 동일label)였다. 목표 공간 확대가 실제 평균 진행 개선을 뜻하지 않았다. B 공유58개는 entry/exit/가격/label이 모두 같았고 common-only 완료10개 net33평균은-58.042953bp였다. 이 관측 선정 차이를 유의한 정보 효과나 계좌 우위로 부르지 않는다.

peer known79=positive68/negative11/zero0, unknown1은 complete3개였다. known은 원래 다른19개 중8~10개만 완전지원됐으며 전체1520window는745complete/775missing이었다. Bpositive numeric57개는 net33 양수16/음수41, target7/structural2/response10/time38이었다. 양의 기여는13개월 중3개·12종목 중2개에 집중됐다. A numeric79개는 양수23/음수56, target7/structural19/response19/time34였다. 가격경로합은 계좌손익이 아니며 유리한 종목/달을 보고 골라내는 새 필터를 만들지 않는다. 어느 후속도 관측 주15회나 고정비용 후 양의 평균을 확보하지 않았다. 신호/보유 결측·작은 선택표본·소진 discovery·cost/price-unit/OPEN 가정의 한계를 유지한다.

## C 입력 적합성 처분과 공식 원문 경로

현재 EE3입력은 모집단/가격/거래량이며 비교 가능한 두 원본 영업손익이 없다. 기존 ED 공시 등록은 no_viewer_body_or_financial_statement=true의 목록 조회만이다. 목록 접수번호/제출일/제목으로 ProfitFact를 채우거나 C 성과0으로 바꾸지 않는다. 이 source 계약 범위의 미지원이지 모든 저장소에 자료가 없거나 C가 실패했다는 판정이 아니다. 별도 integrity 검토는 원래 미지원/마감 원칙에 따라 이번 라운드에서 이 처분과 구체적 후속 경로를 기록할 수 있고, 추가 원문 획득이 필수라는 숨은 gate를 만들지 않아야 한다고 확인했다.

[공시 원본파일](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)과 [XBRL 원본](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019)은 접수번호별 원문 후보 경로이며 인증키를 요구한다. [주요계정](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016)은 기업/연도/보고서코드 조회이고 개별 접수번호를 요청에 고정하는 경로가 아니다. [공식 재무정보 안내](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS003)는 이후 정정으로 수치가 달라질 수 있음을 명시한다. 경로 존재는 실제 권한·원본 보유·이전 잠정 공개·당시 가용 시각의 입증이 아니다. 새 키/외부 문의/원문 획득/collector 변경은 하지 않았다.

C01~C12는 각각 원본/정정, 공개-수집, 참여, 채널, 트리거, 거래대비진행, 시장-내용, 사건나이, VWAP, 불일치청산, 새사건, 내용제거대조의 필요 입력/가능 primitive/미실행을 private capability 표에 대조했다. C05/C10의 순수 primitive가 있어도 C01의 실제 사실 지원을 대체하지 않는다. 새 원문 비교를 한다면 별도 source/pins/clock/unit/보존/독립 검산 단위를 먼저 등록해야 한다.

## 명시적 재진입과 조건부 공동 계좌의 사전 설계

codex/ee-reentry-joint-completion은 별도 reentry/joint 등록과 닫힌 completion unit을 준비한다. A/B 각각 두번째 기전 후속은 기존 A11/B11의 추가 자격 최대1회다. A의 first와 additional은 기존 signal-frozen2R, B는 기존 base first/additional을 쓰며 peer를 몰래 혼합하지 않는다. 원래 first는 새 source/reference에서 재구성하지만 옛 actual unit을 재실행하거나 옛 성공 mask를 사용하지 않는다. 각4slot의 원래target분모·first/reset/nullable additional·unknown/refusal를 보존하고 두 leg의 수익률합을 현금 계좌수익이라고 부르지 않는다.

처음 private 최소안은 만료/known no-entry를 제외했으나, 별도 integrity 검토는 원래 cancelled enum이 구조취소로만 제한되지 않았음을 확인했다. 새 caller는 관측 만료와 확정 진입 거절도 **모델 자격 취소**로 사전 정의한다. 원래 first SHA·cause·최초 확정 시각을 보존한다: expiry는 원래 expired trace availability, admission/cap은 확인 decision, 가격/비용 거절은 관측 entry OPEN이다. 실제 주문 취소·체결·flat·청산 증거로 재명명하지 않는다. unknown OPEN/held/exit, integrity refusal/invalid, 원래 부모 부재, A의 원래 impulse freeze 부재는 terminal을 만들지 않는다. A의 impulse reset은 그 terminal까지 알려져 있어야 하며2R profit target과 구분한다. 기존 actual 기록을 소급 변경하지 않는다.

Joint는 전체 상위2가 아닌 **지원되는 A/B 계열별 하나씩**을 고정된S2 observed complete-leg net33 평균, 더 많은 numeric count, canonical ID 순으로 선정한다. 재진입 bundle은 first+additional을 같은 정책으로 묶는다. diagnostic controls는 별도 장부에 남기고 순위 후보를 늘리지 않는다. 음수 평균만으로 joint를 생략하지 않는다. 선택표본 순위는 계좌 기대수익/검증 우위가 아니다. 실제 pair는 아직 null이며 raw시장/계좌 결과를 이 준비에서 읽지 않는다. 감사된 standalone 결과 뒤 pair/evidence를 source에 고정하고 새 검토·CI·정상 merge·정확한 source 자격을 충족해야 joint actual1이 가능하다.

세 arm은 기존 총 가상자금과 A+B 초기반반 book을 같이 사용한다: JOINT, A활성/Bcash, B활성/Acash. 초기배정이 같아도 이후 공동 NAV/위험/중복 제약으로 수량·노출·거절이 달라질 수 있다. 신규 EE 후보·분 OPEN·목표/구조/반응/시간 종료만 연결하고 EC의 기존5분 진입 guard는 바꾸지 않는다. 계좌 원시는 재사용하며 cost1회·정수주·현재 위험 한도를 유지한다. 신호 unknown이 있는 전체모집단 계좌는 미식별이고 관측 후보 조건부 명목 계좌와 별도다. 미래 path 완료로 후보를 제외하거나 다음 날 현금/손실 이력을 리셋하지 않는다. 기존 금액/가격 단위 가정과 실제 물적 주식·KRW·체결 인증은 구분한다.

## 완료 연결의 구현과 개발 검증 중간 증거

fresh reentry worker는 순수/독립 수학·original-population connector와 report의 추가 closed reentry profile을 구현했다. private31계약을 공개 이식하고 첫 결과를 paths 없이 구성하는 helper1개를 추가했다. 새/기존 지정4파일123passed12.01s, 원래 e61fe665 report와 같은 발명 입력에서 primary/mechanism1 gzip bytes 동일을 확인했다. report는 null additional을 가짜 Detection/0으로 만들지 않고 support원인·원래4slot분모·원본 순서와 full EOF를 보존한다.

fresh joint worker는 기존 book/risk와 독립 EC 위험/현금 수학을 재사용하고 하루12arm·각각의독립carry·lazy price map·flat idle압축과 새 typed State/Result·별도 day framing을 구현했다. 사전 별도 source 검토가 동시진입 후 참조 gates 미갱신과 signal 대신 decision cutoff 두 결함을 발명 반례로 찾아냈다. worker는 새4반례에서3failed/1pass를 확인한 뒤 최소 수정했고 private45pass1.80s, 공개 source/보고66pass8.52s를 확인했다. 공용 codec의 nullable bytes 순서는 새 닫힌 annotation에서 해결했으며 shared codec은 바꾸지 않았다. 둘 다 actual/원본/GCP/commit/push를 하지 않았다.

root는 두 spec/unit 전용 hook과 기존 raw3 인증 context를 공유하고 exclusive unit/actual1/fullgeometry/source/runtime·일반자원·계좌 선택 placeholder 거부를 검사했다. 첫 새unit 검사는37pass/1fail로 joint typed Fraction summary를 단순JSON에 넘긴 연결 오류를 찾았다. closed scalar codec encoding 뒤 summary hash를 계산하도록 고친 후38passed6.24s였다. 기존 primary/followupunit92passed8.96s도 통과했다. 이는 source/invented 검증이며 새 실제 실행이나 전체 자격이 아니다.

신규 private completion runtime은 공개47+자기파일1의 literal48source를 닫고 과학 import를 worker까지 지연한다. 전이 의존10개도 공개 minimum에 명시했다. 지원 python/.venv interpreter의 source-only cold는48source·VmPeak29768KiB·CPU0.124808s·시장/장부0·두 readyfalse·science deferred·보호경로 open 전 거부였다. 처음 잘못된 루트 .venv 명령은 helper 시작 전 경로 오류였고 새 환경 설치/복사는 없었다. 새 원본 metadata/helper 발명 테스트150passed9.99s이며 old helper·실패/실제 원본은 그대로다. collector는 exact unit/attempt leaf를 감사기 이전에 확인한다.

같은 source의 신규 영구 개발 경로 completion-ee-reentry-1-5c4dq6tg에서 REENTRY full257 진단이 exit0·CPU135.170792s·peakRSS87028KiB·wall133.529333s·worker budget complete/violations[]로 완료됐다. 원래 geometry/full EOF/독립 검산을 보존했고 output9072972byte SHA c5a82264fd5df31f0b65cc2f216bd0c2ec08ee187f666924ea5ac9258e6ed9f3, diagnostic SHA e95beb38aedf0b99ee28fe18788c2fe667b308a5b23f4cd16a9d89fd306443cf를 다음 WSL 호출에서도 확인했다. source manifest ab71c8d3646fcda41feacf63566998be54c34bfdf2f23d07619a87ccf74fa177/runtime302ed7ddd9f8970b00130dacfe6d49a9055e2b786c7df74a61acf19663b75e91다. 이는 readiness 갱신 전 개발 capacity이며 final release/3role qualification/원본감사/actual을 대신하지 않는다. Joint full 개발 진단과 마지막 source 동결·별도 검토·필수CI·정상 merge는 후속 증거로 추가한다.

## Joint 준비용 전체 진단의 한도 실패와 별도 출시 단계

새 completion-ee-joint-1-unaolgvo 전체257 개발 실행은 생산/저장 report_records의 ee_joint_day_after에서 CPU180.404459초로 worker 한도에 도달했다. saved verifier 단계에 도달하지 못했고 qualification/actual은0이다. 원본 failure/부분 출력/source/progress를 영구 경로에 보존했고 failure.json SHAe4076a5c04f6f638dcadede84d3224cfb0c50363092571b806251fb1a628d61e를 재확인했다. 이는 공동 전략 손실이 아니며 RE 개발 성공을 joint의 자격으로 채택하지 않는다. 일반 자원을 늘리거나 source/원본/시도 범위를 줄이지 않았다.

같은 소스의 별도8일 plain 진단은 CPU11.000924초였고 cProfile은31.674337초였다. 반복 typed 복원/준비/계좌 수학 비용을 확인해 producer typed handoff와 불필요한 expected codec roundtrip을 먼저 줄였으며, 작은 before/after day JSONL·state/events·summary/count는 같았다(day SHA c44411e1b76ec885a13f00797902ca986016748b395972e93a78b7dc1fa2f2a7). source/binding/footer-prefix 때문에 whole-file hash는 다르다. 이 좁은 개선의 plain CPU9.703160초는 개발 측정일 뿐 전체 통과나 실제 성과가 아니다. 각 lineage의 정확히 같은 immutable day inputs/state에 대한 메모화는 별도 작은 반례/동등성으로 측정 중이며 독립 raw source/최종 저장 검산을 공유 oracle로 만들지 않는다.

Root는 준비용 composed pair를 고정한 진단이 나중에 다른 selected actual branch를 자격화하지 못하도록 계약을 보완했다. ready joint는 selected_pair와 invented_pair의 일치를 요구하고 같은 정책 branch의 원래 fullgeometry 발명 입력으로 fresh qualification을 한다. pair/evidence가 null인 현재 joint는 계속 unready다. RE만 source readiness를 true로 등록한 뒤 새unit 지정39passed6.91s를 확인했다. 이는 release/실제 권한 gate의 면제가 아니며 원래 모든 별도 리뷰·필수CI·정상 merge·exact whole qualification·원본 receipt 감사가 선행한다. Joint의 pair/evidence 동결은 감사된 standalone 결과 뒤 별도 작은 PR/새CI·정상 merge로 진행한다.

## Joint 작은 진단 최종 동결 — 전체 통과와 구별

최종 joint 변경은 producer typed handoff와 불필요한 expected codec roundtrip 제거로 제한했다. producer와 reference의 별도 raw decode는 보존했고, saved verifier는 저장 wire와 원래 입력에서 독립 재구성을 계속한다. 정확한 day/state 메모화는 이 진단에서 hit0이고 비용만 추가되어 코드와 해당 실험 테스트2개를 제거했다. raw saved 검사를 생략하거나 실행 범위를 줄이지 않았다.

최종 지정 joint 테스트67passed8.38s, 별도8일 plain 개발 진단 CPU9.592691초·peakRSS63048KiB였다. 경로 completion-small-ee-joint-1-ke5rzru1, source manifest0a2dc24034e4f6fb7c3b67d065585ab1941368e9d56cabe40de234d705ca5841다. 초기11.000924초 대비 이 작은 fixture에서만 약12.8% 감소했다. 8개 day wire/state/events/summary/count의 SHA c44411e1b76ec885a13f00797902ca986016748b395972e93a78b7dc1fa2f2a7는 동일하고 source binding/header/footer-prefix에 따른 whole-file 차이는 유지한다. 이는 앞서 실패한 전체257 준비용 pair 진단의 성공이나 재실행을 뜻하지 않는다. 실제 선택 pair가 정해진 뒤 그 pair와 정확히 같은 소스로 별도 전체 자격을 확인한다.

## PR398 최종 검토와 첫 CI 실패 보존

공개21파일의 dfdcf8fec6c06684fb53f566ebd1481997b95c6a를 별도 소스 검토 ee_mechanism_source_review와 연구 무결성 검토 ee_primary_integrity_review가 각각 최종 검토했고 추가 지적은 없었다. Root의 지정 EE16개 테스트 파일과 계획 인덱스는828passed·기존1skip(54.77초), staged guardrail·diff check·gitleaks도 통과했다. 최종 공개 소스 리뷰는 PR398 comment6587345756에 기록했다. 이 검토가 CI나 전체 실행 자격을 대신하지 않는다.

첫 Python CI run41515755699/job125518780559는2897.69초에3failed·14820passed·3skipped·169subtests였다. 기존 generic bridge의 두 ED cold probe가32MiB 경계에 닿아 strict below32MiB 검사에 실패했고, 잘못된 budget scope 거절 한 건에서 예상 stderr가 비어 있었다. Java·gitleaks·hostname CI는 통과했다. 정확히 관련된6개 parametrized 사례는 수정 전 로컬에서6passed·222deselected(4.70초)였으나, 이를 CI 실패 해소로 취급하지 않았다. 초기 메모리 여유와 거절 경로를 같은 한도·검사로 점검하며 머지·새 실제 실행은 보류한다. 이는 전략 성과 실패가 아니다.

별도 worker ee_bridge_lean_fix는 coordinator의 불필요한 socket wrapper와 shlex 초기화를 지연했다. hostname은 CPython의 공개 socket.gethostname과 동일한 extension callable을 사용해 fingerprint payload를 보존했다. 네트워크 차단 함수는 호출 시 기존 public socket에 같은 차단을 설치한다. 실패 영수증 저장 뒤 원래 오류 type/message를 stderr에 즉시 flush하고 그대로 raise하며, 진단 쓰기 실패로 원래 예외를 바꾸지 않는다. 원본 CI artifacts가 없어 공백의 단일 원인을 확정하지 않았으며, traceback 지연 초기화·종료 경쟁은 진단 보강의 가능한 동기다.

지정 전체 bridge 테스트232passed(25.99초)와 기존 network/SSH 관련20개(5.39초)가 통과했다. 원래 budget-scope3프로필의 정상/억제 traceback6경우 모두 원래 stderr·STARTED/FAILED·unresolved·실패 영수증을 확인했다. 최종 fresh source-only15회는 프로필별5회씩 ordinary29286400B, ED60029474816B, AS52029478912B로 같은 관측치를 보였다. strict32MiB 대비 최소4075520B 여유이며 qualifies_actual=false·시장/장부/marker/출력 접근0이다. -I가 환경 hash seed를 무시하므로 seed별 재현이라고 주장하지 않는다. 한도·엄격한 검사·원래 scope·guard·종료 정책은 유지했다. 이 수치는 로컬 회귀이며 새 최종-head CI를 대신하지 않는다.
