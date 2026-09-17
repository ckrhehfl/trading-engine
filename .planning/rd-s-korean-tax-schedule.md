# Research Direction Task S — the per-era Korean tax schedule, and the settlement trap inside it

**Sourced 2026-09-17.** Module: `research/krx_tax_schedule.py`.
Reproducible with

```
python -m research.krx_tax_schedule
```

Closes [`rd-f`](rd-f-korean-cost-structure.md) §1.1 item 3, which this
project has carried as **the blocking prerequisite for any Korean
registration** since 2026-09-14:

> *"The rate has changed repeatedly across the KR-10 window, and the
> direction of the resulting bias is NOT established here … applying
> today's 20bp across 2019–2026 is a stated simplification, not a
> conservative one: it may understate the early years and overstate the
> late ones. **A per-era schedule must be sourced before any Korean
> registration**; this document does not have one and does not guess."*

**No strategy was run and no return was computed.** This is a cost
measurement, like rd-f itself.

---

## 1. The headline

> **rd-f's flat 20bp understates the real cost, and the schedule it
> replaces was wrong in both directions at once.**
>
> Session-weighted across the 1,893 sessions of the KRX window, the real
> tax averages **21.45bp** against rd-f's flat **20.0bp** — the flat
> figure is **1.45bp light**. It is too low on **986 sessions (52%)**, by
> as much as **10bp**; too high on **486 (26%)**, by as much as 5bp; and
> exactly right on **421 (22%)**.
>
> **But the more consequential finding is not the rate — it is the date.**
> Every statutory rate change is keyed to the **양도일 (settlement date)**,
> and a backtest keys on the **trade date**. The two differ by **two
> trading sessions**, which came to **4 calendar days** at the 2019 change
> and **5** at the 2024 year end. A cost model that applies the statutory
> date directly charges the wrong rate to every trade in that seam.

## 2. The schedule, by trade date

Both markets pay the **same total in every era** of this window — KOSPI's
농어촌특별세 is fixed at 0.15% and its 증권거래세 was moved to keep the
totals level. So for a cost model the market does not matter, only the
date. That is a coincidence of policy rather than a structural fact, and
the module asserts it rather than assuming it.

| trade dates | KOSPI 거래세 | 농특세 | KOSDAQ 거래세 | **total** | statutory 양도일 |
|---|---|---|---|---|---|
| 2019-01-02 … 2019-05-29 | 0.15% | 0.15% | 0.30% | **30.0bp** | (in force at the open) |
| **2019-05-30** … 2020-12-28 | 0.10% | 0.15% | 0.25% | **25.0bp** | 2019-06-03 |
| 2020-12-29 … 2022-12-27 | 0.08% | 0.15% | 0.23% | **23.0bp** | 2021-01-01 |
| 2022-12-28 … 2023-12-26 | 0.05% | 0.15% | 0.20% | **20.0bp** | 2023-01-01 |
| 2023-12-27 … 2024-12-26 | 0.03% | 0.15% | 0.18% | **18.0bp** | 2024-01-01 |
| **2024-12-27** … 2025-12-26 | 0.00% | 0.15% | 0.15% | **15.0bp** | 2025-01-01 |
| 2025-12-29 … (current) | 0.05% | 0.15% | 0.20% | **20.0bp** | 2026-01-01 |

KONEX went 0.30% → **0.10%** in 2019 and has stayed there. No universe
this project uses is KONEX-listed; it is carried so a future one cannot
silently inherit KOSPI's rate, which is twice as much.

**The arc is one policy, reversed.** The 2021–2025 cuts were the
step-down that was supposed to accompany 금융투자소득세 (금투세); 금투세
was abolished instead, and the 2026 increase is the government replacing
the forgone revenue at the transaction stage. A backtest over this window
never owes 금투세 — only this.

## 3. The settlement trap

증권거래세 is levied on **양도**, which in Korea settles on the **second
trading day after execution**. Every change is therefore announced with
*two* dates, and the Korean press reported both:

| change | statutory 양도일 | published 체결일 | gap |
|---|---|---|---|
| 2019 cut | 2019-06-03 | **2019-05-30** | 2 sessions = 4 calendar days |
| 2025 cut | 2025-01-01 | **2024-12-27** | 2 sessions = 5 calendar days |

**Two sessions, two different calendar-day gaps** — 2024-12-25 (Christmas)
and 2024-12-31 (KRX's year-end 휴장일) both fall inside the second seam.
So a rule written in calendar days is right at one boundary and wrong at
the other, which is why `trade_date_boundary` walks the **real trading
calendar** instead.

The calendar comes from the **KOSPI index series** already in the store —
an index prints exactly when the market is open, so it needs no holiday
table and covers the moving lunar holidays `KrxMarketCalendar` still
lists as unresolved. That is the same index-as-calendar trick
`store.find_missing_ranges` needs for KRX, reused for the same reason.

### 3.1 The lag is validated, not assumed

T+2 is **the only lag of T+1, T+2, T+3 that reproduces both published
trade-date boundaries** from their statutory dates. Two independent
answers, five and a half years apart, one of them across a year end with
two holidays in the seam. T+1 and T+3 each miss both.

That is the check this document rests on, and it is the kind this project
has learned to insist on: the rule was tested against two facts it did
not generate, rather than against a calendar invented to suit it.

## 4. What it does to rd-f's numbers

rd-f's two scenario columns both applied 20bp throughout, and both said
so. Correcting only the tax:

| | rd-f's round trip | tax correction | corrected |
|---|---|---|---|
| **2019–2026 window** | ~33.4bp | +1.45 | **~34.9bp** |
| **2026 today** | ~30.0bp | 0.00 | ~30.0bp |

**Today's column is unaffected**, because 2026's real rate *is* 20bp —
rd-f picked the right number for the present and the wrong one for the
past. The window column moves up by the session-weighted error.

**This does not settle rd-f's other open item.** Its spread figure is
still a one-tick floor, and [`rd-q`](rd-q-which-korean-instrument.md)
already measured the real quoted spread at **12.1bp median** against
rd-f's 6.5bp one-tick assumption — which moves the today column to
**~35.6bp**, as rd-q §3 records. The two corrections are independent and
both point the same way: **Korean spot costs more than rd-f's floor
estimate, on both of its open items.**

## 5. Why session-weighting is a stated assumption, not a neutral one

The 1.45bp figure answers *"what would a strategy trading uniformly
across the window have paid?"* That is the right question for a
schedule-level bias and the **wrong** one for any particular strategy: a
strategy that traded more in 2019–2020 is understated by more than
1.45bp, and one concentrated in 2024–2025 is overstated.

**A real registration does not use this number at all.** It applies
`total_bp(market, trade_date, calendar)` per trade. The aggregate exists
to answer rd-f's question about the direction of the bias, which was
posed at the level of the schedule.

## 6. What this does not establish

**Nothing about whether an edge exists.** A cost input, like rd-f.

**Nothing about single-stock futures**, which pay **no 증권거래세 at
all** — rd-q's whole finding and the reason that instrument matters. This
module is the spot leg only. A futures-executed strategy owes none of
this, which is worth restating because it makes the 1.45bp correction
irrelevant to the instrument rd-r's universe was selected for, and very
relevant to the spot alternative it was compared against.

**The commission is untouched.** rd-f §1.3's 3.54bp is pinned and this
does not revisit it.

**Sources are secondary, not statutory.** The rates and both published
체결일 dates come from Korean press and reference sources, cross-checked
against each other; the 시행령 부칙 themselves were not read. Every era
carries its citation in `SCHEDULE`. rd-f found one source
(TrendMetricLab) still publishing the stale 2023–2025 rates and did not
use it; that source is still stale and still unused.

**One era predates the window and is not verified against it.** The
0.30% level dates to 2017-04-01, before the KRX series opens
(2019-01-02), so it is carried as the rate in force at the open rather
than as a measured boundary. Nothing in this project trades before 2019.

## 7. What follows

1. **The Korean registration prerequisite is closed.** rd-f §1.1 item 3
   was the last one named as blocking; a Korean family can now be
   specified with a per-trade tax rather than a scenario.
2. **rd-f's spread item is still open**, and the quote sampler merged in
   #178 is accumulating what settles it. That is now the only open cost
   item for the spot leg.
3. **Futures commission remains unsourced** (rd-q §6), and for a
   futures-executed strategy it is the *only* remaining cost unknown —
   the tax is zero and the spread is being sampled.
4. **Then rd-p's h = 15 cell**, at a measured floor rather than an
   assumed one, with its own event dispersion reported.

---

*Sources.* [증권거래세 — 나무위키 (탄력세율 및 세율 변천)](https://namu.wiki/w/%EC%A6%9D%EA%B6%8C%EA%B1%B0%EB%9E%98%EC%84%B8) ·
[서울신문 — 증권거래세 30일부터 인하, 코스피·코스닥 0.05%P↓](https://www.seoul.co.kr/news/economy/securities/2019/05/22/20190522024015) ·
[한국일보 — 내달 3일 결제분부터 증권거래세 인하](https://www.hankookilbo.com/news/article/201905211157035366) ·
[금융투자협회 — 증권유관기관 공동보도자료: 오늘부터 증권거래세가 인하됩니다](https://www.kofia.or.kr/npboard/m_18/view.do?nttId=122270&bbsId=BBSMSTR_000000000203&page=29) ·
[한국세정신문 — 내년 1월부터 증권거래세율 코스피 0.05%, 코스닥 0.20%로 상향](https://taxtimes.co.kr/news/article.html?no=272624) ·
[DS투자증권 — 2025년 증권거래세율 인하 적용안내 (체결일 기준)](https://ds-sec.co.kr/bbs/board.php?bo_table=sub06_10&wr_id=751&page=1) ·
[KB — 국내 주식 세금 총정리](https://kbthink.com/main/asset-management/wealth-manage-tip/kbthink-original/202410/kr-stocktax.html)
