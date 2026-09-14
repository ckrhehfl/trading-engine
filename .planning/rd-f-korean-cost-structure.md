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
| 위탁수수료 + 유관기관제비용 | both | ~1–3 | ~1–3 | 유관기관 floor cited; tier not pinned |
| **Round trip** | | **~32 bp** | **~28 bp** | |

**Against BTC's measured 12bp (`scalp-s9`), Korean spot equities cost
roughly 2.4–2.7× as much — and about two thirds of it is a tax charged
whether the trade won or lost.**

**The two columns are the finding, not a hedge.** The spread in *basis
points* is era-dependent: KRX's tick is a fixed number of 원 within a
price band, so as Korean share prices rose through 2025–2026 the same
nominal tick became a smaller fraction of price. A backtest over
2019–2026 must use the window's own figure (**~32bp**), and a live system
starting today faces **~28bp**. Using today's number on historical data
understates costs by about 4bp on every round trip.

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
3. **This is a 2026 change**, so any backtest spanning 2019–2025 that
   applies today's rate overstates historical costs, and one applying the
   old rate understates today's. A registration must say which it uses.

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

**Median 5.45bp today, against 9.85bp over the full window.** Buying at
the ask and selling at the bid crosses one full spread, so the tick in bp
*is* the round-trip spread cost for a marketable order.

**Two honest limitations.** A GCD is a **lower bound** — a name that
crossed a tick band during the 250 days reports the smaller tick (삼성전기
at 402,000 reporting 100 means it traded under 200,000 at some point). And
**two names had to be excluded**: 셀트리온 and 삼성바이오로직스 returned a
GCD of 1, because the store holds **수정주가** (`FID_ORG_ADJ_PRC=0`) and
adjustment produces prices that are not multiples of any tick. That is the
adjustment this project deliberately chose, so the exclusion is a
consequence of a correct decision, not a data fault.

### 1.3 The commission, the one component not pinned

KIS's schedule is published per account type, medium and volume band and
was **not obtainable as a number** from this search; the page is
[here](https://securities.koreainvestment.com/main/customer/guide/_static/TF04ae010000.jsp).
What is confirmed: **유관기관제비용** (KRX + KSD + FCM) is charged to the
customer on top, is quoted around 0.0036% per side, and is a floor no tier
can go below.

**So 1–3bp round trip is a stated assumption, not a measurement**, and it
is the smallest term. At the extremes — a zero-commission promotional
account (0.7bp round trip, 유관기관 only) versus a full-service tier — the
total moves between roughly 31bp and 34bp on the window figure, which
changes no conclusion below. **It should still be pinned before a registration**, per this
project's standing rule against inventing cost constants.

## 2. What this does to the stage-1 table

[`rd-e`](rd-e-stage1-catalogue-result.md) ranked twelve situations by
annual cost drag `F × κ` at BTC's κ = 12bp, and three survived. Re-scored
at Korea's two figures, using the same event frequencies:

| Situation | /yr | @ 12bp (BTC) | **@ 32bp (KRX window)** | @ 28bp (KRX today) |
|---|---|---|---|---|
| **abnormal activity, top 1%** | 113 | 14% **feasible** | **36% cost-hostile** | 32% **feasible** |
| resistance break, prior-1d high | 168 | 20% **feasible** | 54% cost-hostile | 47% cost-hostile |
| support penetration, prior-1d low | 172 | 21% **feasible** | 55% cost-hostile | 48% cost-hostile |
| range expansion after compression | 417 | 50% | 133% INFEASIBLE | 117% INFEASIBLE |
| absorption | 468 | 56% | 150% INFEASIBLE | 131% INFEASIBLE |
| abnormal activity, top 10% | 582 | 70% | 186% INFEASIBLE | 163% INFEASIBLE |
| everything below | ≥755 | ≥91% | ≥242% INFEASIBLE | ≥211% INFEASIBLE |

> **Nothing is "feasible" on Korean spot equities over the window a
> backtest would actually run on.** Three situations clear the bar on BTC
> perpetuals; **zero** clear it at the window's own 32bp, and exactly one
> — the rarest — clears it at today's 28bp, at 32% against a 35% line.

**That gap between the two Korean columns is the sharpest thing in this
document.** The single candidate that survives does so only because
Korean share prices rose enough in 2025–2026 to shrink the tick in
relative terms. A study backtested over 2019–2026 and scored at the
window's own costs finds nothing; the same study scored at today's costs
finds one marginal survivor. **Which column a registration uses is
therefore not a detail — it decides the result**, and using today's number
on historical data is the flattering choice.

The frequencies are BTC's and Korean equities will differ, so the table is
a projection rather than a measurement — but the **ordering** is a
property of the situations, and the ceiling moves against all of them
together.

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
| Round trip | **~27bp** | materially lower, **not yet sourced** |
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
- **The commission is an assumption** (§1.3), not a measurement.
- **The futures round trip is not sourced at all** (§3) — the comparison
  says "materially lower", which is an inference from the absent tax, not
  a figure.
- **No strategy is implied.** A feasible cost drag is the weakest possible
  positive statement: it means costs do not rule the situation out in
  advance.

## 5. What follows

1. **A Korean registration states which tax regime it applies** and why —
   the rate changed on 2026-01-01 and the KR-10 window opens in 2019.
2. **Pin the commission tier** before any Korean registration, per the
   standing rule against invented cost constants.
3. **The instrument choice (§3) is an operator decision**, and it should
   be made before intraday KRX collection is scoped — futures and spot do
   not need the same data.
4. `situation_catalogue.feasibility()` already takes `round_trip`, so
   re-scoring is a parameter, not a rewrite.
