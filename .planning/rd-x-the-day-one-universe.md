# Research Direction Task X — the +54%/yr was the selection date, and moving it removes the premium

**Measured 2026-09-20/21.** Modules:
`research/krx_dayone_universe.py`, `research/krx_dayone_drift.py`.
Reproducible with

```
python -m research.krx_dayone_universe --rank
python -m research.krx_dayone_drift --fetch
python -m research.krx_dayone_drift --measure
python -m research.krx_dayone_drift --compare
```

**Discovery mode.** KRX daily was spent by `ms-f` on 2026-09-13. Nothing
here may be promoted, quoted as evidence of an edge, or reported as a
pass; the output is a universe definition and a diagnosis.

Opened by [`rd-v`](rd-v-payoff-geometry-and-the-universe-drift.md) §1,
which measured the `rd-r` ten at **+54%/yr** against KOSPI's +19% and
named the cause: they were ranked on **2026Q1** turnover, a date inside
the window they are then measured over. This is the test of that
diagnosis, made possible by [`rd-w`](rd-w-the-delisted-universe.md)'s
delisted source.

---

## 1. The headline

> **Same window, same construction, only the selection date moved — and
> the premium goes from +27%/yr to −2%/yr.**

| selection rule | equal-weight | per yr | vs KOSPI | median name |
|---|---|---|---|---|
| **`rd-r`: 2026Q1 futures turnover** | 16.55x | **+44%/yr** | **4.92x, +27%/yr** | **+797%** |
| **day-one: 2019-01 spot turnover** | 2.86x | **+15%/yr** | **0.85x, −2%/yr** | **+8%** |
| KOSPI 2019-2026 | 3.36x | +17%/yr | — | — |

**The median name is the sharper number**: +797% against +8%, a factor of
roughly a hundred. `rd-v` §1's diagnosis is confirmed — the drift was the
selection date and nothing about Korean equities.

**The day-one basket slightly *under*performs the index**, which is what
an equal-weight basket of 2019's most-traded names should do: 2019's
turnover leaders included the speculative names that then collapsed.
Six of the thirty lost 70% or more.

## 2. Why the comparison had to be re-run rather than quoted

`rd-v` §1 reports 7.94x / +54%/yr against a KOSPI of 2.31x / +19%/yr.
Those come from the `rd-r` ten's **own inner-join panel**, 2021-11-29 to
2026-09-17, **4.80 years**. This document's panel is 2019-01-02 to
2026-09-18, **7.71 years**.

**So quoting +54% beside +15% would be changing two things at once.**
`--compare` re-measures the `rd-r` ten over *this* window, which makes
the only difference the selection rule. That is why the table above says
+44%/yr for `rd-r` rather than `rd-v`'s +54%: a longer window, the same
names, a lower annualised figure. Both are correct about their own
window; only one pair is a comparison.

A name that listed after 2019-01 starts at its own first bar — `402340`
SK스퀘어 begins 2021-11-29 — which is the same treatment a delisted
member gets at the other end.

## 3. The universe, and what selecting in 2019 actually picks

`krx_dayone_universe.py`: rank every common-stock candidate by median
거래대금 over **2019-01**, take the top 30, freeze.

| | |
|---|---|
| candidates | **4,640** (2,604 live + 2,036 delisted common stock, from `rd-w`) |
| ranked | 2,180 |
| absent from the window | 2,458 |
| too thin to rank (<15 sessions) | 2 |
| **errors** | **0** |

The members a 2026-informed rule would never have chosen are the point:
신라젠, 헬릭스미스, 아난티, 좋은사람들, 신화프리텍, 한국내화 — all
2019 turnover leaders, all down 70-98%.

**Only one member (셀트리온헬스케어, merged into 셀트리온 2024-01-11)
delisted during the window**, and that is worth stating plainly: at the
top of the 2019 turnover ranking, survivorship is a *small* correction.
The large correction is the selection date. `rd-w`'s delisted source
matters much more for a wider or longer scan, where the delisted fraction
is not 1-in-30 — 2,036 of the 4,640 candidates here are delisted names,
and 2,458 candidates were absent from the 2019 window entirely.

## 4. A wire fact this turned up, and it is a hazard for any KRX backtest

> **KIS prints a bar for every session a HALTED name is listed**, with
> `O == H == L == C` at the last traded price and `acml_tr_pbmn` **0**.

Measured on 신라젠 `215600`: **604 consecutive sessions, 2020-05-04 to
2022-10-12**, 603 of them with all four prices equal at 7,757 and zero
turnover. The bar after it opens at that same 7,757 and closes at
**10,043 — a +29% resumption gap**. 좋은사람들 `033340` carries **832**
such sessions.

This is the equity counterpart of the single-stock-futures fact CLAUDE.md
already records (*"a bar is printed for every session a contract is
LISTED, carrying `acml_vol` 0 when nobody traded it"*), and it is more
dangerous on three counts:

1. **A return series reads 603 zeros**, which deflates realised
   volatility. Whether a Sharpe computed over it comes out *higher* also
   depends on the return sample, the annualisation constant and whether
   the resumption gap is included — **none of which is measured here**, so
   the claim is that it may inflate, not that it does.
2. **A backtest holds a position it could not have exited for two and a
   half years**, and nothing in the bar stream says so.
3. **The resumption gap is taken as a tradeable one-day return.** It was
   not tradeable; it was the price of two years of being locked in.

**A bar is not evidence the name was tradeable — only that it was
listed.** It is detectable (`O==H==L==C` and zero turnover) and
`krx_dayone_drift.frozen_sessions` does so; it is **counted and reported
rather than filtered**, because what to do about it depends on the
measurement. A first-open-to-last-close ratio is unaffected — a holder
really was stuck — so §1's figures stand.

## 5. A defect the fail-closed reporting caught

The first measurement printed *"KOSPI unavailable — no selection premium
quoted"*. The index had been fetched (1,895 bars) and stored as all-NULL
prices: **the index endpoint returns `bstp_nmix_*` where equities return
`stck_*`**, on an otherwise identical row. `kis_klines._parse_row`
already documents this; this module read the equity names against the
index response.

**Nothing threw.** Every price stored as `NULL`, the `open IS NOT NULL`
filter then removed every row, and the series was silently empty. What
surfaced it was the one line that refuses to substitute a guess for a
figure it could not compute — `rd-v`'s own convention, carried over.
`store_series` now raises when every row parses to NULL prices, because
that is a wrong endpoint mapping rather than missing data.

## 6. What this does NOT establish

**No strategy, no edge, nothing run.** This is a diagnosis of a
measurement artefact and the construction of a cleaner universe.

**A cleaner universe is not an unbiased one.** Ranking on 2019-01
turnover is still a choice, made once, and the top of a turnover ranking
is a particular kind of name. What it removes is the *look-ahead* in the
ranking date; it does not make the basket representative of the market.

**One month is a short ranking window.** 2019-01 was chosen because it is
the start of the panel, not because a month is the right amount of data
to rank liquidity on. A longer pre-window would be a different
specification and is not tested here.

**The halt fact is measured on three names**, not systematically across
the 4,640. The rate at which KRX names carry frozen stretches is unknown.

**The comparison arm re-uses the `rd-r` ten**, which were selected on
information from inside the window. That is the whole point of the
comparison and also means the +44%/yr figure is not a claim about
anything except what that selection rule produces.

## 7. What follows

1. **Every future KRX measurement on a selected basket states its
   selection date**, and if that date is inside the measurement window,
   reports the premium the way §1 does. `rd-t`, `rd-u` and `tm-e` all
   sit on a 2026-selected basket and are bounded by `rd-v` §1 accordingly.
2. **Frozen-bar detection belongs in the pipeline, not in one module.**
   `frozen_sessions` is a query over two columns; any KRX study that
   computes returns should run it and report the count.
3. **The index field map is a per-endpoint property**, like the row cap
   before it. Reading one endpoint's names against another's gives NULLs,
   not errors.
4. **The full-universe scan is the next step and is now unblocked** —
   4,640 survivorship-safe common-stock candidates, ~0.7s per call
   measured, and the two traps above named before it starts.
