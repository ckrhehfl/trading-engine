# Research Direction Task CA — investigate feasible short-term scenarios before fixing the scope

## 2026-10-20 — performed intake and public-source investigation

This records design work and a literature/documentation investigation that
actually happened. It is not a registered performance specification, a run
permit, an amendment to the Eligibility Bar or a new research conclusion.
Binding rules remain in `CLAUDE.md`. No numerical candidate universe,
holding limit or first pattern family has been adopted.

### 사용자 목적과 이번 변경

- 국내 최상위 소수 종목에서 급등·급락 움직임을 이용하는, 개인이 실행 가능한
  수익 모델을 연구한다. 초고속 지연차익·코로케이션 경쟁은 제외한다.
- 수시간~수일 보유를 생각하되, 사전에 정의한 조건이 무효화되면 수분 만에도
  청산할 수 있다. 사후 가장 유리한 보유기간을 거래마다 붙이는 뜻은 아니다.
- 사용자는 희망 빈도를 **계좌 전체 주 15회 이상 새 진입**으로 올렸다.
  연구/운용 적합성 목표다. 강제 진입·주문 쪼개기·위험 한도 완화가 아니다.
  매주 하한인지 장기 주평균 목표인지도 최종 명세에서 구분해야 한다.
- 최초 추천한 상위 10개·최대 3거래일·추세 지속 우선은 채택하지 않았다.
  사용자는 근거와 시나리오를 조사해 선택지를 먼저 제시하라고 요청했다.
- 종목 수, 장중 전용/야간 보유, 첫 가설은 미확정이다. 공개 조사와 설계는
  계속할 수 있지만, 이 선택에 의존하는 성과 실험은 시작하지 않는다.

주 15회는 5거래일 주간의 일평균 3회에 해당한다. 52주 내내 충족한다는
가정에서 연 780회지만 실제 기회 수는 미측정이다. 같은 날 반도체주 여러 개의
진입이나 같은 급변의 재진입을 독립 사례로 세지 않는다. 빈도 증가 자체가
검정력 증가, 우위 또는 이익을 보장하지 않는다.

### 공개 근거 — 확인된 범위와 전이 한계

2026-10-20 웹 검색 및 열람. 아래는 논문·저자 소속기관·공식 API/거래소 문서다.
검색 결과로 노출된 학술 초록/방법/결론도 사용했으며, 일부 본문 직접 열람은
403/429/timeout으로 실패했다. 전문의 비용 가정을 모두 검산한 조사라고
표현하지 않는다. 외부 수익률을 프로젝트 실험 결과로 옮기지 않는다.

1. **Market intraday momentum**, Gao et al. (2018),
   [저자 소속기관의 연구 소개](https://profiles.wustl.edu/en/publications/market-intraday-momentum/).
   미국 S&P 500 ETF의 1993~2013년 자료에서 첫 30분 움직임과 마지막
   30분 움직임의 예측 관계를 보고한다. 국내 개별 대형주의 하루 종일
   추종 수익이나 현재 비용 후 우위를 증명한 연구는 아니다.
2. **Intraday Patterns in the Cross-section of Stock Returns**, Heston et al.
   (2010), [저자 원문/초록](https://arxiv.org/abs/1005.3535).
   동일 시간대의 반복성과 단기 반전, 일시적 수급 불균형 및 bid-ask bounce를
   구분한다. 체결가의 반등이 실제로 매수·매도 가능한 순이익과 같지 않다는
   연구 설계상의 경고로 사용한다. 한국의 반등 전략 합격 증거가 아니다.
3. **Market Intraday Momentum with New Measures for Trading Cost: Evidence
   from KOSPI Index** (2022),
   [출판사 논문](https://www.mdpi.com/1911-8074/15/11/523).
   2004~2016년 코스피 지수의 장중 모멘텀과 고가·저가 기반 스프레드 추정치를
   연구했다. 지수의 통계 관계와 추정 비용을 실제 개별주 매매·세금·체결
   비용의 검증으로 읽지 않는다. 이번에는 검색에 노출된 초록/방법을 확인했고
   출판사 본문 직접 열람은 제한되었다.
4. **Assessing the profitability of intraday opening range breakout strategies**
   (2013), [출판사 논문](https://www.sciencedirect.com/science/article/pii/S1544612312000438).
   미국 원유선물의 돌파 연구다. 공개 방법·결론에는 구간별로 성과가 안정적이지
   않았다는 내용도 있다. 시초 구간 돌파의 가설 사례이며 한국 주식 성과
   증거가 아니다. 일별 OHLC를 사용한 이 논문의 방법이 프로젝트의 봉 내부
   진입·청산 순서를 확정해 주지도 않는다.
5. **Quantifying the High-Frequency Trading Arms Race** (2021),
   [NBER 저자 연구](https://www.nber.org/papers/w29011).
   거래소 메시지 자료로 초고속 지연차익 경쟁을 연구한다. 이번 방향에서는
   그 경쟁을 제외하고 완성된 분봉 이후에도 남는 효과를 찾는다. 분봉 전략은
   속도와 비용에 무관하다는 뜻은 아니다. 지연 민감도를 따로 측정해야 한다.
6. [한국투자증권 공식 분봉 API 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py):
   현재 예제는 과거 분봉 최대 1년 보관, 호출당 최대 120건, J/NX/UN 시장
   구분을 설명한다. 프로젝트에 그만큼 저장되어 있다는 증거가 아니다.
   KRX·NXT·통합 데이터를 조용히 이어 붙이지 않는다.
7. [KRX 공식 시장 안내](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp)와
   [VI 안내](https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T7.jsp):
   정규장·별도 장 구간 및 변동성 완화 장치를 구분한다. 현재 설명으로 과거
   모든 날짜의 세션·체결 규칙을 입증하지 않는다. 첫 연구는 정규장으로
   제한하는 선택을 제안하며, 실제 날짜별 규칙과 특별 개장일은 달력으로 처리한다.
8. [OpenDART 공시검색 공식 명세](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001):
   목록의 `rcept_dt`는 YYYYMMDD 접수일자다. 이 필드만으로 장중 공시
   관측 시각을 만들 수 없다. 공시 이후 가격 반응 연구는 실제 시간 근거와
   정정 이력이 필요하며, 뉴스 원문 이력은 별도 자료 문제다.
9. [The Probability of Backtest Overfitting, 저자 논문](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf):
   반복 탐색과 최선 전략 선택이 만드는 거짓 발견 위험을 설명한다.
   이 조사는 수많은 지표·시간·종목 조합을 전수 최적화하는 근거가 아니다.

**추론:** 수십 분~수시간 후에도 남는 효과를 대상으로 하고 지연에 강한지
검증하면, 코로케이션 없이 연구할 수 있는 후보가 된다. 논문이 현재 국내
개별주에서 그런 효과의 존재나 수익을 보장하지 않는다.

**가정/미확인:** 현재 저장된 분봉의 충분성, 새 모집단의 사건 빈도,
현실적인 체결·비용 후 성과, 주 15회 달성 가능성은 아직 측정하지 않았다.
특정 서버 사양·구매·증설은 이번 조사에서 요구하지 않는다.

### 다섯 시나리오 — 선택용 설계 예시, 고정 명세 아님

| 유형 | 관찰하고 매수할 모습의 예 | 보유 범위 예시 | 핵심 실패 가능성 / 추가 자료 |
|---|---|---|---|
| A. 장 초반 범위 돌파 | 처음 일정 구간의 고가를 이후 완성 봉이 넘어선 뒤 진입 | 수십 분~당일 | 횡보장의 가짜 돌파, 급등 직후 나쁜 체결; 분봉·과거 변동성·거래 활동 |
| B. 장중 급등 후 눌림·재상승 | 이례적 상승 뒤 조정이 멈추고 사전 정의한 가격을 재돌파 | 수십 분~수시간 | 반등처럼 보이는 추세 종료, 조건을 계속 붙이는 과적합; 분봉 |
| C. 급락 후 회복 확인 | 급락 자체가 아닌, 하락 정지·회복 기준 확인 뒤 매수 | 수십 분~당일 | 새 악재로 하락 지속, 미세한 반등이 비용 이하; 분봉·가능하면 시장 맥락 |
| D. 마감 강세 후 익일 지속 | 마감 전까지 관측한 강세가 다음 날에도 이어지는지 조사 | 익일~수일 | 밤사이 갭, 장중 손절 불가, 기업행사; 야간을 포함한 장부 |
| E. 공시·실적 이후 지연 반응 | 당시 관측 가능한 공시 이후 첫 과열 구간을 기다린 뒤 규칙 진입 | 수시간~수일 | 시각·원문/정정 이력·기대 대비 서프라이즈 정의, 사건 빈도 부족 |

A/B는 같은 추세 계열이고 완전히 독립적인 아이디어가 아니다. C는 반대의
경제 가설이다. 갭 상승 뒤 지속·갭 하락 뒤 회복도 각각 A/B 또는 C의 별도
등록 가능한 사건으로 볼 수 있다. 지금 한꺼번에 모든 하위 유형을 탐색하거나
패턴에 따라 미래를 보고 유리한 매도 규칙을 고르는 제안은 아니다.

시장/업종 대비 상대 강도, 같은 시간대 대비 비정상 거래 활동, 당일 시장 상태는
**추가 설명 변수 후보**다. 최초 가격 가설을 고정하고 각 변수의 추가 가치를
같은 대상·시각·노출의 비교에서 확인한다. 재무제표는 단기 배경/대상 선정,
실적 발표는 사건, 가격은 진입·청산 계기가 될 수 있다. 전부 처음부터 섞지 않는다.

### 제시할 연구 패키지 — 아직 사용자 선택 전

1. **추천: 상위 20개 안팎의 장중 패턴 연구.** 당시 시총 기준 후보와 유동성을
   고정하고 A 또는 B 중 한 추세 가설과 C 반등 가설을 각각 작은 연구로 검토한다.
   우선 당일 청산을 제안한다. 관찰 종목과 장중 기회를 늘리는 방향이지만
   15회 달성이나 더 높은 수익률은 미확인이다. 두 유형을 즉시 합산 운용하지 않는다.
2. **상위 10개 안팎 집중 + 장중/익일 보유.** A/B와 D 중 정할 범위를 연구한다.
   대상은 더 집중되지만 야간 갭·동시 보유·빈도 제약이 커질 수 있다.
3. **공시·실적 중심 사건 연구.** E를 우선하고 가격은 진입 계기로 사용한다.
   경제적 설명은 직접적이지만 당시 공시 시각·내용 자료 부담이 더 크고,
   소수 대형주만으로 주 15회를 얻기는 특히 불확실하다.

상위 20개·10개는 수익이나 빈도를 확인해서 정한 숫자가 아닌 선택용 예시다.
최종 모집단·리밸런싱·방향·보유 범위는 사용자 선택 후 성과 조회 전에 고정한다.
현물 매수를 첫 범위로 제안한다. 하락 지속을 공매도 수익으로 계산하거나
현물·선물·레버리지를 승인 없이 추가하지 않는다.

### 현재 기반을 재사용하는 범위

기존 KRX/KIS 수집·달력·세금·기업행사·실험 로그·검산 체계를 활용할 수 있다.
BT~BZ의 반년 간격 선정/126거래일 보유 결과는 이번 시간대의 성공 근거가 아니다.
과거 KR-10 분봉 수집 기록도 오늘의 데이터 커버리지나 당시 top-N 집합이 아니다.
닫힌 과거 탐색과 예약된 구간은 기존 규칙 그대로 둔다.

소스에서 확인한 `python/data/kis_intraday.py`는 누적 `acml_tr_pbmn`을 봉별
거래대금으로 저장하지 않고 `quote_volume`을 비운다. 완전한 연속 원본이
있는지 먼저 확인하지 않고 복구 가능하다고 선언하지 않는다. 거래량으로 시작할
수 있는 가설은 거래량 신호로 명명한다. 0·종가×거래량·누적값을 봉별 실제
거래대금으로 대체하지 않는다. 실제 VWAP도 필요한 거래대금이 없으면 만들지 않는다.

Java Risk Gateway 등은 기존 안전 부품이다. 현물 주문 경로의 운용 준비가
완료되었다는 뜻은 아니며, 첫 연구를 위해 주문 플랫폼부터 다시 만들지 않는다.

### 최소 진행 절차와 종료 조건

1. **현재:** 공개 조사와 사용자 범위 선택. 어떤 가격 가설·종목군·보유 범위를
   조사할지 합의한다. 다른 조건을 조용히 최적화하지 않는다.
2. **입력 적합성:** 기존 Discovery 접근 규칙에 맞춰 먼저 목록/읽기 범위를
   등록한다. 필요한 당시 모집단·분봉·필드·시각·누락만 확인한다. 현재 종목만
   과거에 고정하지 않고, 출처·관측/가용/수집 시각과 가격 단위를 구분한다.
3. **최소 보정:** 별도로 등록한 제한적 보정에서 사건 빈도·겹침·자체 분산과
   비용 단위를 확보한다. 수익이 좋은 조건을 고르는 단계가 아니다. 자기 사건
   기준 검정력 판정 **후에** 최종 비교군/비교 실험 명세를 확정·실행한다.
   BR/BT/BZ의 탐색 예외를 계승하지 않는다. 새 선택이 필요하면 이유를 제시한다.
4. **첫 성과 탐색:** 지정된 Discovery 구간에서 고정 가설의 현실적 진입 이후
   손익·비용·낙폭·노출·빈도와 고정 비교를 계산한다. `run_discovery_trial`과
   canonical `runs/experiments.jsonl`을 재사용하고 실제 입력 읽기 전에 started를
   남긴다. 정확한 소스/명세/입력 해시, completed/failed 영수증을 보존한다.
5. **판정:** 독립 검산 후 시기·종목 집중, 비용/지연 민감도, 주별 기회 수를
   보고한다. 확인된 결과를 토대로 다음 가설·개선·독립 검증을 결정한다.
   holdout 접근·승급·실거래는 별도 기존 체크포인트다.

하네스는 위 순서를 자동 검사·기록하는 작은 연결이다. 모든 예외와 완전한
주문 엔진을 구현한 뒤 첫 실험을 시작하는 계획이 아니다. 실제 결과를 바꾸는
시간 검사·완성 봉·지연 체결·중복 사건·현금/비용/야간 회계부터 구현한다.
체결·용량처럼 원자료가 부족한 부분은 명시한 보수적 가정 및 민감도로
제한할 수 있으며, 그 가정과 양립하지 않는 전략을 검증 완료로 부르지 않는다.

최종 명세에서는 당일/전일/월말 입력 각각의 `available_at <= decision_at`,
같은 봉 체결 방지, VI·휴장·누락·야간 구분, 현금·정수 주식 수·비용·기업행사,
동시 신호 우선순위, 투자 에피소드와 주문 건수 구분을 다룬다. 다일 중첩과
시장 공통 충격을 반영한 표준오차를 사용하고 기존 요구의 보정/단순 오차
비율도 보고한다. 과거 일봉의 블록 길이·비용 수치를 복사하지 않는다.

### 누락과 반복을 줄이는 진행 하네스

`CLAUDE.md`는 기존 규칙/방향 소유자, 이 문서는 실제 조사·선택 기록,
`docs/codex-handoff.md` 앞부분은 현재 목표·최신 근거·미결정·다음 행동,
기존 실험 로그는 실제 실행 증거를 맡는다. 새 범용 플랫폼·플러그인·일괄
`GOALS.md`/`PLANS.md` 도입이 선행 조건이 아니다. 이는 프로젝트에 맞게
선택적 파일 관례를 쓰라는 [OpenAI 공식 안내](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex)와도 일치한다.

소스 검토·연구 무결성 검토·실제 산출물 독립 검산·CI는 별개 상태로 남긴다.
현재 자료 검산의 구체적 결함/새 질문이 없으면 같은 장부의 반복 감사를
새 연구 결과처럼 세지 않는다. 사용자에게 매번 성과 보고 승인을 새로 요구하지
않으며, CLAUDE.md의 방향 폐쇄·holdout 접근·결론 반영 체크포인트 및 기존
고위험/비용 승인 경계를 유지한다.

보고에는 새 사실/근거, 추론/가정, 코드로 제거한 위험(이번에는 없음), 남은 위험,
실제 실험 횟수, 현재 단계·직접 미결정·다음 산출물을 포함한다. 첫 실험의
최종 막힘 개수는 모집단·가설 선택 전에는 확정할 수 없다. 현재 직접 결정은
**연구 패키지 선택 1건**이고, 데이터·구현·검정력의 잔여 조건 수는 미측정이다.
이번 공개 조사로 실제 데이터/구현 조건이 해소된 수는 **0건**이다.

### 이번 검증의 경계

새 시장자료·DB·실험 장부·자격 증명·GCP에 접근하지 않았고 수익 계산,
주문·수집기 변경·배포도 하지 않았다. 독립 설계 검토와 문서 정합성 검사를
성과 검증·최종 PR 리뷰·CI·배포 완료로 표현하지 않는다. 기존 미추적 사용자
Python 파일 두 개는 읽기·실행·수정·스테이징 대상에서 제외한다.

## 2026-10-20 — design verification receipt

- Separate read-only reviewer `intraday_charter_integrity_review` returned
  **PASS, zero blocking findings** for the data-free design at SHA256
  `b6c0554bb694e0a5048c65d9a07ab8f94107fb78f9e392d55dc608fffdad0ebc`
  (this document before this receipt was appended). The reviewer did not
  independently fetch the external sources or certify actual strategy outputs.
- Its wording recommendation was applied to the handoff and scope paragraph:
  mainly hours/days, with minute-scale invalidation exits. Numerical universe,
  holding limit and first family remain pending.
- WSL Ubuntu-24.04, worktree-owned `python/.venv/bin/python -B -m pytest`:
  the eight explicit document/index/count test node IDs in
  `python/tests/test_planning_index.py` passed, **8 passed in 4.31s**.
  No full test collection or actual-data test was run.
- `git diff --check` passed. WSL `scripts/codex_guardrails.py staged` returned
  OK; the index was empty and this is not a full repository or document-content
  security scan. Full scans were avoided to preserve the excluded user files.
- No commit, PR, required CI, merge or GCP deployment was performed for this
  intake. These remain distinct from this design review and local tests.

## 2026-10-20 — operator selected package 1 after the investigation

The operator replied **“1. 상위 20개 안팎·당일 매매: 상승 지속과 급락 후 반등을
각각 연구 (추천)”**. This supersedes the pending-choice status earlier in this
append-only record. The original top-10/maximum-three-session proposal remains
unadopted history. No scope-choice question is pending.

Adopt the around-top-20, intraday direction and investigate continuation and
post-drop recovery separately. The first continuation design will use an
opening-range breakout because its observation/trigger boundaries can be made
explicit; pullback/reacceleration remains a later candidate, not a simultaneous
third search. Exact universe/ranking cutoff and effective time, liquidity,
event thresholds, completed-bar interval, fill delay, position/cost model,
exit policy and Discovery/calibration input scope must be registered before
their dependent reads/runs. Do not tune them after inspecting profits.

Intraday liquidation is the intended policy, not an assumption that every
exit must fill: halts, VI and other inability to sell still require preserved
positions and explicit residual-risk accounting. Fifteen new entries per week
remains an operating-fit target; actual opportunity and independence are unknown.
The two hypotheses are not automatically independent tests or jointly approved
portfolio sleeves. No new actual-data reads or performance runs occurred when
recording this decision. The next authorized work is the bounded inventory
registration and minimum calibration design under the existing guards.

The preceding design-review hash covers the proposal, not this later decision
appendix or the updated handoff/scope text. Final source review/CI remains open.

The same separate reviewer subsequently checked this decision delta and its
handoff/scope counterparts: **zero design-blocking findings**. It confirmed
that further scoping/registration can proceed without another scope question.
This additional design review is still not final commit/PR approval.

## 2026-10-20 — execution started: bounded inventory registration

The operator explicitly requested starting the selected direction. The first
actual-data task is a metadata inventory, registered in
`configs/research/discovery/intraday-scope-inventory-v1.json`. It reads only
`klines` symbol, interval, timestamp, retrieval stamp and field presence in
the already-spent **2025-09-04..2026-09-23** KST intraday Discovery window.
OHLC, numerical volume/turnover, events, forward moves, ranks, order books,
investor flow, later intraday dates and every reserved database are excluded.
No API call or local database copy/synchronization is included.

The source database must be the explicit collector cache absolute path, not
the isolated checkout's default cache. Use SQLite URI read-only/query-only,
one snapshot, a column-read authorizer, bounded aggregation and a query time
budget. `data.store.connect()` is inappropriate because it creates/migrates
tables. Missing databases and unsupported schemas fail rather than creating
an empty cache. Counts/presence do not diagnose halt, VI, no trades or supplier
failure, and a span or count alone does not certify completeness.

The minimal implementation owns a new inventory module and synthetic tests;
existing per-study wrappers, experiment logging and isolated source deployment
are reused. Source/specification/dependency/runtime pins, non-overwriting
private outputs, durable canonical started/completed-or-failed records,
separate source/integrity reviews, actual CI, normal merge and a separate
verification of the actual aggregate result remain required. Resource/retry
boundaries are in the JSON. The collector stays at its own existing checkout
and is not fast-forwarded, restarted or rescheduled for this research.

Immediate output: which symbols have which stored sessions, actual field
presence and timestamp offsets within the named spent range. This resolves
the current-data-coverage unknown, not historical membership or profitability.
The next registration will use that inventory to define a dated top-20
universe acquisition/join and the minimum event-frequency/own-dispersion
calibration. It must not silently shrink to the old KR-10 survivors or consume
newer unspent dates to make a larger sample. No prior daily-study underpower
or unmodeled-asset exception transfers.

## 2026-10-20 — inventory implementation and synthetic verification

`research.intraday_scope_inventory` now implements the fixed read-only query,
column authorizer, snapshot, operation/time/aggregate-row limits, exact spec
path checks, started-before-read logging, non-overwriting output and
file/directory durability before canonical completion. Source/specification
bytes and an optional caller's full source/runtime validator are checked
inside the logged callback before its completed record.

The final module's explicit synthetic test file passed **37 tests in 2.87s**
under WSL Ubuntu-24.04. The preceding integrated document/module invocation
passed 44 tests before the final directory-fsync addition; it is not evidence
that that addition was tested by the earlier run. A separate, producer-free
aggregate verifier passed 19 synthetic mutation tests. The private deployment
helper's syntax/JSON-line and actual-producer-to-independent-verifier tests
passed two tests. All used temporary synthetic stores and logs only.

Private helper `var/ca-inventory-publication.py` will fast-forward only the
isolated research source to the reviewed normal merge, pin the stdlib-only
runtime and source/dependency closure, execute the logged inventory once,
check its exact new canonical started/completed span, and run the independent
aggregate verifier without re-reading the database. It saves exclusive private
source/result/verification receipts. Its cooperative study lock does not prove
other programs use that lock; unexpected concurrent canonical records fail
publication. The aggregate verifier checks supplied-row arithmetic/digests,
not extraction completeness, historical eligibility or source truth.

At this entry, final source review, required CI, normal merge and actual GCP
inventory remain pending. No actual price/amount/event/return read or new
performance experiment is included in this work unit.
