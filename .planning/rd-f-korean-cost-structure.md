# Research Direction Task F — what a Korean round trip actually costs

**Sourced and measured 2026-09-14.** This closes a blocker that has been
open since MS-F §2 (*"the cost constants, which must be sourced"*) and
that [`rd-e`](rd-e-stage1-catalogue-result.md) §4 names as a prerequisite
for any Korean stage 2.

**No strategy was run and no return was computed.** This is a cost
measurement.

It also corrects a stale assumption of this project's own: MS-F recorded
"roughly 42bp" for KRX and rd-e repeated it. That figure was for
**single-stock futures**, and decision **B3** moved the universe to
**spot common stock** — a different instrument with a different, and
larger, cost structure.

---

## 1. The number

| Component | Side | **over the KR-10 window** | **today** | Basis |
|---|---|---|---|---|
| **증권거래세 + 농어촌특별세** | **sell only** | **20.0** | **20.0** | statute, effective **2026-01-01** |
| Spread (one full crossing) | round trip | **~9.9** | ~6.5 | **measured**, §1.2 |
| 위탁수수료 + 유관기관제비용 | both | **3.54** | **3.54** | **pinned**, §1.3 |
| **Round trip** | | **~33.4 bp** | **~30.0 bp** | |

**Both columns apply today's 20bp tax throughout** (see §1.1 item 3) and
**both use a one-tick spread**, which §1.2 shows is a *lower bound*. So
both are **floor estimates**, and the true figures are higher by however
much the real quoted spread exceeds one tick.

**Against BTC's measured 12bp (`scalp-s9`), Korean spot equities cost
roughly 2.5–2.8× as much — and about two thirds of it is a tax charged
whether the trade won or lost.**

**Neither column is an actual historical cost. Both are SCENARIOS**, and
saying so is not a hedge — it is what the two open items in §1.1 and §1.2
imply. Each applies **today's 20bp tax across the whole window** (the
per-era schedule is unsourced, and §1.1 item 3 says the direction of that
simplification's bias is not established) and **a one-tick spread** (§1.2
shows that is a floor, not a measurement).

So they are the **one-tick, fixed-tax floor** at two spread regimes:

| scenario | spread used | round trip |
|---|---|---|
| **2019–2026 window** | 9.85bp, the full-window median | **~33.4bp** |
| **2026** | 6.46bp, this year's median | **~30.0bp** |

The 3.4bp between them is **arithmetic under a fixed tax and a one-tick
assumption**, not a claim that real historical trades cost 3.4bp more than
this year's. §2's two columns are therefore a **sensitivity analysis**
across cost scenarios, and neither is a final Korean figure until the
per-era tax schedule and the realised spread are measured.

### 1.1 The tax, which dominates and just went up

Effective **2026-01-01**, as the follow-through on the 2025 세제개편안,
the rates reverted to their 2023 levels:

| Market | 증권거래세 | 농어촌특별세 | **Total on every sale** |
|---|---|---|---|
| **KOSPI** | 0.05% | 0.15% | **0.20%** |
| **KOSDAQ** | 0.20% | — | **0.20%** |
| KONEX | 0.10% | — | 0.10% |

KOSPI's securities transaction tax rose from 0% to 0.05% with the 0.15%
농특세 retained; KOSDAQ's went from 0.15% to 0.20% with no 농특세. The two
markets land on the same 0.20%.

**Three properties matter more than the rate itself:**

1. **It is charged on the sale, not on the gain.** A losing trade pays it
   in full. This is the Taiwan finding — *71% of the average day trader's
   loss is transaction cost* ([`rd-c`](rd-c-mechanism-catalogue.md) §1) —
   in Korean statutory form.
2. **It is unavoidable.** Unlike a commission it cannot be negotiated to a
   broker tier, and unlike a spread it cannot be reduced by passive
   execution.
3. **The rate has changed repeatedly across the KR-10 window, and the
   direction of the resulting bias is NOT established here.** 2026 is the
   latest of several moves, and the earlier ones did not all go the same
   way — KOSPI's 증권거래세 was *higher* than 0.05% earlier in the window
   before being cut toward 0, while 농특세 stayed at 0.15%. So applying
   today's 20bp across 2019–2026 is **a stated simplification, not a
   conservative one**: it may understate the early years and overstate the
   late ones. **A per-era schedule must be sourced before any Korean
   registration**; this document does not have one and does not guess.

*Sources:* [기재부 개정 보도](https://news.nate.com/view/20251201n26338) ·
[2026 세제 변경 정리](https://www.kiwoomam.com/lounge/KI0502010102M?kijaNo=554) ·
[증권거래세 — 나무위키 (탄력세율 조항 포함)](https://namu.wiki/w/%EC%A6%9D%EA%B6%8C%EA%B1%B0%EB%9E%98%EC%84%B8) ·
[세율표 정리](https://glasswallet.com/blog/stock-transaction-tax-guide/).
Note one source found during this search
([TrendMetricLab](https://trendmetriclab.com/guides/stamp-duty-securities-tax/))
still publishes the **2023–2025** rates (KOSPI 0% + 0.18%); it is stale
and was not used.

### 1.2 The spread, measured rather than cited

The published KRX tick schedule was not obtainable from a primary source
in this search, so the tick was **measured from the store instead** — the
GCD of a name's closes divided by its median price, which gives the tick
as a fraction of price.

**Measured per year, because the answer moves:**

| Year | names | **median tick (bp)** | min | max |
|---|---|---|---|---|
| 2019 | 6 | 13.09 | 0.98 | 25.45 |
| 2020 | 6 | 9.82 | 6.35 | 11.85 |
| 2021 | 6 | 12.48 | 5.76 | 27.47 |
| 2022 | 7 | **16.21** | 8.47 | 35.34 |
| 2023 | 7 | 12.12 | 6.89 | 17.76 |
| 2024 | 8 | 12.15 | 5.65 | 15.48 |
| 2025 | 8 | 7.17 | 3.80 | 16.21 |
| 2026 | 9 | **6.46** | 4.45 | 15.58 |

**Full-window median across name-years: 9.85bp** (p25 6.89, p75 13.59).

This corrects a first version of this document, which measured only the
**last 250 days** and reported 5.45bp. That figure is right about today
and wrong about the window a backtest would run on — 2022's median is
**2.5× larger**. MS-F's own measurement (5.91bp for SK하이닉스 to 19.49bp
for 삼성전자) sits inside this range and is now explained: it was reading
a different era's price levels, not a different instrument.

The last 250 days, per name, for reference:

| Symbol | Name | Median price | Implied tick | **1 tick (bp)** |
|---|---|---|---|---|
| 009150 | 삼성전기 | 402,000 | 100 | 2.49 |
| 007390 | 네이처셀 | 22,250 | 10 | 4.49 |
| 064350 | 현대로템 | 202,000 | 100 | 4.95 |
| 005930 | 삼성전자 | 183,500 | 100 | **5.45** |
| 000660 | SK하이닉스 | 907,000 | 500 | 5.51 |
| 000720 | 현대건설 | 107,200 | 100 | 9.33 |
| 028300 | HLB | 48,750 | 50 | 10.26 |
| 051910 | LG화학 | 324,500 | 500 | 15.41 |

**Median 5.45bp today, against 9.85bp over the full window.**

**These are a LOWER BOUND on the spread, not the spread.** A tick is the
minimum price *increment*; the quoted bid-ask can be several ticks wide,
and daily closes cannot measure it — only quote or trade data can. So
"one tick = the round-trip cost of crossing" is the **best case**, and
every figure derived from it is correspondingly **biased low**. At two
ticks the window round trip is `20.0 + 2×9.85 + 3.539 = ` **43.24bp**, and
§2's single surviving candidate does not survive either — pinned by
`test_a_two_tick_spread_removes_the_last_korean_survivor`.

Measuring the realised spread needs intraday quote or trade data, which
[`rd-c`](rd-c-kis-flow-probe-result.md) shows is obtainable for ~250
sessions and is **not yet collected**. Until then this section is a
**one-tick floor scenario**, and it is labelled as such everywhere it is
used.

**Two honest limitations.** A GCD is a **lower bound** — a name that
crossed a tick band during the 250 days reports the smaller tick (삼성전기
at 402,000 reporting 100 means it traded under 200,000 at some point). And
**two names had to be excluded**: 셀트리온 and 삼성바이오로직스 returned a
GCD of 1, because the store holds **수정주가** (`FID_ORG_ADJ_PRC=0`) and
adjustment produces prices that are not multiples of any tick. That is the
adjustment this project deliberately chose, so the exclusion is a
consequence of a correct decision, not a data fault.

### 1.3 The commission, now pinned to an account and a medium

A first version of this document left this as an assumed 1–3bp, because
KIS publishes the schedule per account type, medium and volume band and
it could not be read from search results. **Raised on review of PR #169
and since read directly from KIS's own page**, then verified independently
before being adopted ([기준일 2025-10-27](https://securities.koreainvestment.com/main/customer/guide/_static/TF04ae010000.jsp)):

| | per side |
|---|---|
| 위탁수수료 — **뱅키스(BanKIS) 온라인** account, 국내주식 (KOSPI/KOSDAQ/KONEX), HTS · 홈페이지 · 모바일 | **0.0140527%** = 1.405bp |
| 유관기관제비용 — **KRX** | **0.0036396%** = 0.364bp (NXT: 0.0031833%) |
| **Total per side** | **1.769bp** |
| **Round trip** | **3.539bp** |

**The account type and the order medium are part of the number**, which is
why both are named: a 영업점 account, or the same account ordering by
phone, is a different tier entirely. Quoting "the KIS commission" without
them would be the same class of error as quoting a tick size without its
price band.

유관기관제비용 is charged to the customer **on top of** the commission and
is a floor no tier goes below, so 3.539bp is close to the best case for a
retail account. **A different account or medium moves it, and would move
§1's totals with it.**

## 2. What this does to the stage-1 table

[`rd-e`](rd-e-stage1-catalogue-result.md) ranked twelve situations by
annual cost drag `F × κ` at BTC's κ = 12bp, and three survived. Re-scored
at Korea's two figures, using the same event frequencies:

| Situation | /yr | @ 12bp (BTC) | **@ 33.4bp (KRX window)** | @ 30.0bp (KRX today) |
|---|---|---|---|---|
| **abnormal activity, top 1%** | 113 | 14% **feasible** | **38% cost-hostile** | **34% feasible** |
| resistance break, prior-1d high | 168 | 20% **feasible** | 56% cost-hostile | 50% cost-hostile |
| support penetration, prior-1d low | 172 | 21% **feasible** | 57% cost-hostile | 52% cost-hostile |
| range expansion after compression | 417 | 50% | 139% INFEASIBLE | 125% INFEASIBLE |
| absorption | 468 | 56% | 156% INFEASIBLE | 140% INFEASIBLE |
| abnormal activity, top 10% | 582 | 70% | 194% INFEASIBLE | 175% INFEASIBLE |
| everything below | ≥755 | ≥91% | ≥252% INFEASIBLE | ≥227% INFEASIBLE |

> **Nothing is "feasible" on Korean spot equities over the window a
> backtest would actually run on.** Three situations clear the bar on BTC
> perpetuals; **zero** clear it at the window's own 32bp, and exactly one
> — the rarest — clears it at today's 30.0bp, at **34% against a 35%
> line.**

**That gap between the two Korean columns is the sharpest thing in this
document.** The single candidate that survives does so only because
Korean share prices rose enough in 2025–2026 to shrink the tick in
relative terms. A study backtested over 2019–2026 and scored at the
window's own costs finds nothing; the same study scored at today's costs
finds one survivor sitting one point inside the boundary. **Which column a registration uses is
therefore not a detail — it decides the result**, and using today's number
on historical data is the flattering choice.

**The frequencies are BTC's, so this table is a projection and not a
measurement — including its ordering.** `feasibility()` scores
`F × round_trip`, and `F` is the only thing that differs between rows, so
the ranking above is *entirely* BTC's event frequencies. Korean equities
have price limits, a call-auction open and close and a 6h45m session;
any of those can reorder `F` and therefore reorder the survivors.

**So this must not be used to prioritise which situations get a Korean
stage 2** until Korean frequencies are measured. What does carry over is
the *level*: the ceiling moves against every situation together, by the
ratio of the round trips.

**This is the strongest statement of rd-c §2's thesis yet.** *The filter
is the strategy* is not a preference here; on Korean spot equities a
20bp statutory tax on every sale means **only an extremely selective rule
can pay for itself at all** — and at the window's own spread, perhaps not
even that one.

## 3. The instrument question B3 reopened, and it is the operator's

Decision B3 chose a full KOSPI + KOSDAQ scan, which means **spot common
stock**. But the KR-10 universe (MS-E) was built from **single-stock
futures underlyings**, and futures carry **no 증권거래세** — the tax that
is three quarters of the cost above.

| | Spot common stock | Single-stock futures |
|---|---|---|
| Universe | **2,718** names | ~265 underlyings |
| 증권거래세 | **20bp per sale** | **none** |
| Round trip | **~33.4bp window / ~30.0bp today** (§1, one-tick floor) | no 증권거래세, otherwise **not sourced** |
| Selection breadth | the "stocks in play" mechanism works | too narrow for it |
| History | daily to 1991, intraday ~250 sessions | **KIS serves no expired contract at all** (MS-B §2.1) |
| Leverage / margin | cash | exchange-set margin, ~₩10k–100k minimum |

**Neither dominates**, and the trade is sharp: spot gives the breadth that
makes selection-as-strategy work and pays a tax that kills all but the
rarest situations; futures remove the tax and cannot support a wide scan,
and their historical tradeability is **unverifiable** from KIS.

**Not a decision this document should make.** It is recorded here so the
choice is made with the tax on the table rather than discovered after a
registration is written.

## 4. What this does not establish

- **No Korean event frequency has been measured.** §2's table reuses BTC's
  frequencies. Korean equities have price limits, a call-auction open and
  close, and a 6h45m session; every one of those changes how often a
  situation fires.
- **The commission is now pinned** (§1.3) to 뱅키스 online, KRX, 2025-10-27.
  A different account tier or medium moves it.
- **The futures round trip is not sourced at all** (§3) — the comparison
  says "materially lower", which is an inference from the absent tax, not
  a figure.
- **No strategy is implied.** A feasible cost drag is the weakest possible
  positive statement: it means costs do not rule the situation out in
  advance.

## 5. What follows

1. **A Korean registration states which tax regime it applies** and why —
   the rate changed on 2026-01-01 and the KR-10 window opens in 2019.
2. **~~Pin the commission tier~~ — done** (§1.3). What is still open is
   the **realised spread**, which needs intraday quote data, and the
   **per-era tax schedule** (§1.1 item 3).
3. **The instrument choice (§3) is an operator decision**, and it should
   be made before intraday KRX collection is scoped — futures and spot do
   not need the same data.
4. `situation_catalogue.feasibility()` already takes `round_trip`, so
   re-scoring is a parameter, not a rewrite.
