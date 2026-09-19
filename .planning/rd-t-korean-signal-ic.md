# Research Direction Task T — do any Korean intraday features predict anything?

**Measured 2026-09-18.** Module: `research/krx_signal_ic.py`. Reproducible
with

```
python -m research.krx_signal_ic
```

**Discovery mode**, so the promotion `N` is not incremented and nothing
here may be promoted, quoted as evidence of an edge, or reported as a
pass. The only legitimate output is a written specification.

> **Bounded by [`rd-v`](rd-v-payoff-geometry-and-the-universe-drift.md), measured 2026-09-19.** This universe returned **+694% equal-weight over the panel, +54%/yr, against KOSPI's +19%/yr — a selection premium of 3.4x the index.** `rd-r` picked these ten on 2026Q1 futures liquidity, and a name becomes liquid enough to carry a listed future *because* it has gone up 10-25x, so the criterion and the return are the same fact. **Any long-only or drift-exposed figure below is partly that.** The cross-sectional and h=1 framings used here are the defence, and they defend against the *level*, not the dispersion.

This is `scalp-s11`'s question asked in Korea. It is also the **one piece
of the five-part plan that needed new work** — the CSTI structure (S8),
the leg vocabulary (Task A), the volatility conditioner (S10) and the
management policy (Task D) are all already found, and are **adopted
rather than re-searched**, which is what keeps their cost at zero.

---

## 1. The headline

> **Four of 42 tests clear both the usable-IC floor and the
> multiple-testing correction — and all four are CROSS-SECTIONAL. Not one
> time-series feature survives.**
>
> The axis this project could never compute before is the only one
> carrying anything.

| feature | h | kind | n | rank IC | p naive | **p block** | SEx |
|---|---|---|---|---|---|---|---|
| `ret_5` | 15 | **cross-sectional** | 5,977 | **−0.0399** | 2.2e-16 | **0.0005** | 1.06 |
| `ret_15` | 15 | **cross-sectional** | 5,803 | **−0.0288** | 1.3e-08 | **0.0005** | 0.97 |
| `ret_15` | 60 | **cross-sectional** | 1,259 | **−0.0297** | 6.0e-03 | **0.0045** | 1.01 |
| `range_pos_60` | 15 | **cross-sectional** | 5,030 | **−0.0264** | 2.4e-07 | **0.0005** | 0.94 |

**The gate runs on `p block`, not on `p naive`** — see §5.1. The naive
column is retained only so the size of the correction is visible.

Every survivor is **negative**: the name that has risen most against its
peers over the last 5–15 minutes tends to **underperform** them over the
next 15–60.

**That it is a known effect is a point in its favour, not against.**
Short-horizon cross-sectional reversal is among the most replicated
results in the equity literature. Finding it means the instrument is
working; finding something nobody has ever seen would be the worrying
outcome.

## 2. Why the cross-section is the whole story

`research/ic.py` recorded the limitation before Korea existed here:

> *"This is a TIME-SERIES IC, not the cross-sectional one. The
> conventional quant-equity IC correlates a feature across many assets at
> one instant. This project trades one symbol … breadth here comes from
> independent decisions in time, so it is bounded by how long a bet is
> held, not by how many symbols are traded."*

A universe lifts that. Under `IR ≈ IC × √breadth`, ten names decided
independently at one instant is breadth a single symbol cannot buy at any
holding period. The two ICs answer different questions — *"is now a good
time?"* versus *"which name, right now?"* — and **Korea answers the
second and not the first.**

That is the most consequential sentence here, because every one of the
1,883 logged trials asked the first question.

## 3. Eight features are about three signals, and only one carries

Measured as the **per-instant cross-sectional** rank correlation, averaged
— the same construction the ICs use. The figures in brackets are what a
correlation pooled over every (name, instant) pair reported, and every one
of them is higher; §5.2 explains why that construction overstates
redundancy and why it was replaced.

| pair | \|ρ\| | (pooled) | |
|---|---|---|---|
| `rvol_15` vs `rvol_60` | **0.765** | (0.850) | same signal |
| `ret_60` vs `range_pos_60` | **0.654** | (0.788) | same signal |
| `ret_15` vs `range_pos_60` | **0.549** | (0.654) | same signal |
| `ret_5` vs `ret_15` | **0.459** | (0.515) | borderline |
| `ret_5` vs `range_pos_60` | 0.399 | | |
| `rvol_15` vs `range_pos_60` | 0.075 | | independent |

So the eight collapse to roughly **three**: *recent relative move*
(returns and range position together), *volatility*, and *volume*. **S11
found exactly the same collapse on BTC** — ten survivors, three signals —
which is now a twice-observed property rather than one market's quirk.

The `ret_5`/`ret_15` pair sits at 0.459 rather than the pooled 0.515, so
it no longer crosses the 0.5 "same signal" line on its own. The grouping
is unchanged regardless: both are tied into the same cluster through
`range_pos_60` (0.399 and 0.549), and all four §1 survivors live in it.

**Of the three, only "recent relative move" survives.** Volatility and
volume clear neither the floor nor the correction in the cross-section.
`IR ≈ IC × √breadth` takes the count of *independent* signals, so the
honest figure to carry forward is **one**, not four and not eight.

## 4. What could not be measured, and why that is a real loss

**`turnover_z` — abnormal 거래대금 — is not computable on KRX intraday.**
KIS serves `acml_tr_pbmn` as a *cumulative* figure within the session, so
`kis_intraday.py` deliberately leaves `quote_volume` NULL on every minute
bar. Daily bars carry it; minute bars cannot.

This costs the study its strongest prior. [`rd-c`](rd-c-mechanism-catalogue.md)
§2 records that the literature's best filter is exactly this: Zarattini's
"Stocks in Play" gets Sharpe 2.81 from restricting a plain opening-range
breakout to abnormally *active* names, while the same rule fails entirely
with no filter. **The single feature most likely to work is the one the
data cannot express.**

`volume_z_60` is carried as the available proxy and is a proxy: the same
share count is a different amount of money at different prices, and the
universe spans ₩84,000 to ₩1,765,000 a share.

The module **drops it by name and says so** rather than reporting `n = 0`
— in a results table those are the same row, and they are opposite
conclusions.

## 5. The trailing gap rule, which had never been built

[`rd-p`](rd-p-what-krx-intraday-could-resolve.md) found that a **forward**
return computed positionally on KRX silently spans the 11-minute closing
auction or the 17.5-hour overnight break, and built `continuous_blocks`.

**The identical problem exists on the trailing side, and nothing handled
it.** A 60-bar momentum computed at 09:05 reaches back through the
overnight gap into yesterday afternoon and reports it as an hour of
movement. Measured:

| | |
|---|---|
| bars whose 60-bar trailing window spans a gap | **161,512** of 955,273 (**17%**) |
| `ret_60` → h=15 IC **with** the rule | **+0.0037** |
| `ret_60` → h=15 IC **without** it | **−0.0031** |

**The sign flips.** Both figures are below the usable floor so nothing in
§1 rode on it — but a sign is precisely what gets written up as a
directional finding, and rd-p's own correction moved σ by 13% on a
quantity that did matter.

A feature is therefore defined only where its entire trailing window lies
inside one contiguous run of 60-second bars. The cost is the first
`lookback` bars of **every session**, and it is reported rather than
absorbed.

## 5.1 The naive p-value is not a significance test, and correcting it strengthened the conclusion

**Raised on review of PR #181 and it was right.** Both IC constructions
produce samples that are not independent: ten names share a market-wide
move at the same instant, and instants within one session share that
session's conditions. Stepping by the horizon removes *overlap between
forward windows* and does nothing about either. `research/ic.py` already
said as much about its own p-values, and CLAUDE.md carries the standing
rule — *deduplicate to independent samples before reporting a p-value, or
state that you did not.* Feeding those p-values straight to
Benjamini-Hochberg made the `survive` column a screening result wearing a
significance test's clothes.

Every p-value is now a **session block bootstrap**: 2,000 resamples of
whole trading dates, at a fixed seed so the figure is reproducible.
`SEx` — the block standard error over the naive one, the analogue of the
`null_sd / se` ratio CLAUDE.md requires on every permutation test — is
reported on every row.

**The correction is real, and it is asymmetric in the direction that
matters:**

| | SEx median | range (h = 15, 60) | effect |
|---|---|---|---|
| **time-series** rows | **1.32** | **1.12 – 1.89** | naive p-values were materially too small |
| **cross-sectional** rows | 0.99 | 0.90 – 1.06 | essentially uncorrected |

The ranges exclude h=240, whose 79–137 observations make its own SEx an
estimate with little behind it (§7 already declines to read those rows as
measurements); the time-series minimum over all horizons is 0.94, and it
is an h=240 row.

That is not a coincidence, and it is the most interesting thing this
correction produced. The time-series construction *pools across names at
the same instant*, which is precisely the dependence being corrected for.
The cross-sectional construction already collapses each instant to one
number before averaging, so it had almost nothing left to correct.

**So §1's conclusion survives the correction, and the correction is
additional evidence for it.** The four survivors are the same four
cross-sectional rows, at essentially unchanged p-values. What changes is
the time-series column: `rvol_60` at h=15 moves 1.4e-03 → **0.0895**,
`volume_z_60` at h=15 moves 3.9e-03 → **0.0325**, `rvol_15` at h=15 moves
1.0e-02 → **0.1220**. The rows that looked closest to carrying were the
ones being flattered most.

**One defect found while building it, worth recording because a correction
that produces a false positive is worse than no correction.** The first
version bootstrapped the mean of per-session ICs while the `rank_ic`
printed beside it was the pooled Spearman — two different statistics. On
the real run it reported `p = 0.0005` where the naive p was `0.41`, which
is how it was caught: a dependence correction is not supposed to turn a
null result significant. The bootstrap now resamples the statistic that is
actually reported, via per-session sufficient statistics so 2,000 draws
over 59,698 pairs stay affordable.

## 5.2 Redundancy had to be measured cross-sectionally too

**Also raised on review of PR #181, and also right.** `orthogonality()`
pooled every (name, instant) pair into a single correlation. That absorbs
two things which have nothing to do with redundancy: the persistent level
difference between names — this universe spans ₩84,000 to ₩1,765,000 a
share — and the common time variation every name shares.

Both inflate it, so two features whose cross-sectional *rankings*
genuinely disagree every day could still be reported as "the same signal".
And this is not a side diagnostic: **it is what §3 turns into the signal
count, and the signal count is what §8 carries forward.**

Since the conclusion is a cross-sectional one — *which name, right now* —
the correlation has to be the cross-sectional one: rank the universe by
each feature at one instant, correlate the two rankings, average over
instants. Every pair came down, as §3 records. The qualitative grouping
into three signals held.

## 6. The cross-sectional sample was wrong first, and the fix changed the answer

The first implementation stepped by the horizon **inside each symbol**,
which lands different names on different minutes and collapses the
intersection: 3,840 usable instants at h=15 became **143** at h=60 and
**20** at h=240. That is a property of the sampling, not of the market.

With one shared grid built over the union timeline, the same horizons
give 5,977 / 1,302 / 137 — and three of the four survivors in §1 appear
only after the fix. The first run reported **1 of 42**; the corrected one
reports **4 of 42**, all cross-sectional.

## 7. What this does not establish

**It is not evidence of an edge, and may not be quoted as one.** Discovery
mode's first guard. An IC says a number carries information about what
follows; it says nothing about whether the information survives costs,
sizing, or execution.

**An IC of 0.03–0.04 is at the low end of usable.** S8's own calibration
puts 0.02–0.05 as genuinely useful, so this is real but modest — and
`IR ≈ IC × √breadth` is a documented *upper bound* that the literature
agrees overstates achievable IR.

**Costs are not applied anywhere in this document.** rd-q's ~13bp round
trip for the futures-liquid names is the number a specification must
clear, and a cross-sectional long-short pays it on both legs.

**The universe carries a mild look-ahead.** rd-r selected these ten on
**2026Q1** futures turnover, and the intraday window spans 2025-09 to
2026-09 — so the selection window sits inside the measurement window.
Bounded rather than removed: rd-r measured rank persistence at Spearman
**+0.954**, so the 2026Q1 ranking is close to any other quarter's.

**h = 240 is thin.** 79–137 instants. Those rows are reported for
completeness and should not be read as measurements.

**투자자별 매매동향 is absent from every figure above**, and it is the
obvious third axis — the one information source Korea has that crypto does
not. What exists, measured on the instance 2026-09-18 rather than assumed:

| | |
|---|---|
| storage | `positioning`, metrics `krx_investor_flow.{individual,institution,foreign}.{buy,sell}.{qty,value}` |
| symbols | **18** |
| trading dates | **33**, 2026-08-03 → 2026-09-17, no interior gap |
| collection began | 2026-09-14 |

**33 trading dates from four days of collection is not a contradiction,
and this is the part worth stating** — a reviewer reasonably read "one
month" as arithmetic that does not work. `inquire-investor` takes **no
date parameter and returns a rolling 30-row lookback**, so the very first
call on 2026-09-14 already delivered back to 2026-08-03. The series
therefore cannot be backfilled *earlier* than that first call, and loses
nothing from that call forward — which is exactly why the collector's
start date matters and why it is now on the always-on instance.

One month of 18 names is enough to look at and not enough to specify on.
That is why waiting here is productive rather than idle.

## 8. What follows

1. **The specification names one signal, not four features.** Recent
   relative move, cross-sectional, h = 15–60. Volatility and volume are
   not carried.
2. **It is a relative-value shape, not a timing shape** — rank the
   universe, be long the laggards and short the leaders, rather than
   deciding when to be in the market. That follows from §2 and it is a
   different kind of strategy from anything in the 1,883 logged trials.
3. **Single-stock futures make the short leg possible at all**, which is
   rd-q's finding doing real work: shorting Korean spot is restricted and
   expensive, and futures pay no 증권거래세.
4. **Add 매매동향 as the second independent signal when it has depth.**
   One signal is `√1` of breadth; the case for Korea rests on getting to
   two or three.
5. **Then a specification, committed before any confirmation run**, with
   the entry taken from outside where possible and the management policy
   adopted from Task D rather than re-searched.
6. **Every future cross-sectional measurement in this project inherits
   §5.1 and §5.2.** Both are construction rules rather than results: a
   p-value over observations that share a session is not a significance
   test, and a redundancy figure must be computed in the same
   cross-section as the conclusion drawn from it.
   [`rd-u`](rd-u-do-conjunctions-beat-their-parts.md) hit the first of
   these independently, one task later, and there it moved the verdict
   from one surviving hypothesis to none.
