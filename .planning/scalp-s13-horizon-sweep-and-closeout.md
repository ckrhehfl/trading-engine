# Scalping Strategy Research Task S13 — the holding-horizon sweep, and the selectivity reversal

Executed 2026-08-28. Two findings, and the second overturns the first's
conclusion. Read both halves: the horizon sweep is correct and still
useful, but the closeout recommendation it originally carried was drawn
from one arbitrary operating point and has been retracted.

Measurement only: no strategy registered, no holdout accessed, nothing
logged to `runs/experiments.jsonl`. Already-spent window.

## The question

S12 measured a gross mean outcome of **+0.95 bps** per position against a
**10 bps** taker fee. Three remedies were on the table. Two were already
foreclosed by measurement before this task:

- **Maker execution.** At ~2 bps round trip, +0.95 bps still loses
  (−1.05 bps). Ruled out in S12 — and ruled out *without* first having to
  harden `fill.py`'s optimistic limit-fill model, which is the real
  saving.
- **Signal combination.** S11 established roughly 3 independent signals.
  Grinold's `sqrt(3)/sqrt(2)` over the two already used gives ~1.16 bps
  against 10 bps. Insufficient by an order of magnitude, and the law is
  known to *overstate*.

That left one: **does the gross outcome grow with holding period?** It
needed roughly a 10x improvement.

## The answer: it peaks at one hour, then inverts

Entry rule identical to S12 (fade `htf_ret_4h` + taker-buy share,
equal-weighted, |z| ≥ 2.0, trailing top-10% activity). Only the holding
period varies. 145,568 positions at every horizon.

| Holding | Win rate | Gross mean | Net @12bps | vs fee alone |
|---|---|---|---|---|
| 15 min | 37.3% | +0.43 bps | −11.57 | 0.04x |
| 30 min | 40.7% | +0.67 bps | −11.33 | 0.07x |
| **1 hour** | 43.2% | **+0.95 bps** | −11.05 | **0.10x** |
| 2 hour | 44.7% | **−0.05 bps** | −12.05 | −0.01x |
| 4 hour | 46.8% | −1.65 bps | −13.65 | −0.17x |
| 8 hour | 47.6% | −5.67 bps | −17.67 | −0.57x |

**The gross outcome does not grow with holding period. It peaks at 1 hour
and turns negative at 2.**

That is exactly what S11's ICs predicted and is the strongest internal
consistency check in this arc: the surviving signals are **mean-reverting
at the hour scale**. Hold past the reversion window and the edge is spent
— what remains is noise, and then the position is carried into moves the
signal said nothing about.

Note the win rate rises monotonically (37.3% → 47.6%) while the gross
outcome falls and inverts. A rising win rate with a falling expectancy is
the classic shape of giving back more on the losers than the extra wins
are worth, and it is a good illustration of why S8 put expectancy on the
metric list rather than trusting win rate.

## What this closes, quantified

To clear a 10 bps taker round trip, a candidate needs a gross mean
outcome of roughly **10-15 bps** — 10.5x to 15.8x the best figure this
signal family produced at its own optimal horizon.

Translating that back through the observed relationship (IC ~0.05 →
0.95 bps) implies a required IC on the order of **0.5-0.8**. For
calibration, S8's own cited practitioner range is **0.02-0.05** for a
"genuinely useful" signal, and no sustained IC near 0.5 appears anywhere
in the public literature.

**This is not "we did not find anything." It is a measured account of why
nothing is there at this timescale and this cost level**, with each
remedy closed by its own number rather than by fatigue.

## What is NOT concluded

- **Not** that no scalping edge exists anywhere. This tested roughly 20
  feature formulations across 7 categories, on one asset, one venue, one
  timescale band.
- **Not** that the statistical machinery was wrong or too strict. It was
  never the binding constraint here — no DSR or PSR gate ever rejected
  anything in S8-S13. What rejected these candidates was **arithmetic**:
  a 10 bps fee against a sub-1 bps gross outcome.
- **Not** that the daily line is affected. `daily-tsmom-ensemble` trades
  at a completely different frequency, where a 12 bps round trip is
  negligible against multi-day moves, and it remains in paper trading on
  its own human-approved exception.

## What this arc leaves behind

Reusable, tested infrastructure that outlives the negative result:

| Module | What it does |
|---|---|
| `research/ic.py` | Time-series rank IC, non-overlapping sampling, Benjamini-Hochberg across a sweep |
| `research/excursion.py` | MAE/MFE to S8 §3.7's contract, stop recommendation, `trailing_percentile_rank` |
| `research/strategies/regime_classifier.py` | Two-axis regime labelling with hysteresis, dwell, and gap-aware fail-closed |
| `backtest/engine.py`'s insolvency floor | Task S7, applies to every strategy in the codebase |
| `research/analysis/s12_excursion_run.py` | A committed, checksummed reproduction |

And measured facts that constrain any future work here:

- Real BTCUSDT perpetual spread is **one tick** (~0.015 bps); the taker
  fee is ~330x it and dominates the cost structure entirely (S9).
- Volatility conditioning must use an **absolute** measure; a ratio to a
  trailing mean destroys the signal (5.21x vs 1.22x separation, S10).
- Order flow is **orthogonal to price structure** (|r| ≤ 0.006) — the one
  genuinely independent pair this project has found (S11).
- Price/momentum signals at this scale are **mean-reverting**, and the
  reversion is spent by roughly one hour (S11, S13).

**The reserved Binance spot 1m holdout was never touched.** The entire
arc ran on already-spent windows. That is the discipline working: a
negative result cost no irreplaceable data.

## SUPERSEDED: the selectivity sweep reverses this conclusion

Everything above is correct **for the entry rule it tested** — and that
turned out to be the whole problem. The human operator pushed back that
this was still "trading dozens of times a day", not the "once or twice a
day, only on a genuinely big signal" they had been describing. They were
right, and it is measurable:

**The tested entry fired 57.2 times per day.** One or two a day is ~29x
more selective, and that operating point was never tested.

Sweeping selectivity on the identical signals (|z| threshold × activity
percentile), 60-minute hold, gross mean outcome:

| \|z\| ≥ | Activity | Trades/day | Gross mean | vs 12bps cost |
|---|---|---|---|---|
| 2.0 | top 10% | 57.24 | +0.95 bps | ✗ |
| 3.0 | top 0.1% | 2.81 | +10.78 bps | ✗ |
| 4.0 | top 0.1% | **1.46** | **+19.31 bps** | ✓ |
| 5.0 | top 1% | **1.18** | **+25.10 bps** | ✓ |
| 6.0 | top 1% | 0.48 | **+41.30 bps** | ✓ |

**Gross outcome rises monotonically with selectivity and crosses the cost
line.** The "scalping does not clear costs" conclusion was drawn from a
single arbitrary operating point and generalised to the whole space. It
does not hold.

Direction is confirmed unchanged: trend-**following** returns exactly the
negative of fading at every cell, so mean reversion remains the correct
sign.

### But this is not an edge — and the significance figures above are retracted

The table above counts **every qualifying bar** as a separate position.
Extreme readings cluster, so consecutive qualifying bars produce many
60-minute windows covering nearly the same price path. Treating those as
independent observations inflates any standard error computed from them.
An earlier version of this document reported t = 7.0–8.0 from exactly
that calculation and called it strong. **That claim is withdrawn.**
CodeRabbit flagged the same defect on the PR independently, and Task S14
measured its size.

Restricting to **non-overlapping** positions — the discipline
`research/ic.py` already enforces for IC sampling, which the excursion
sweep never inherited:

| \|z\| ≥ | Activity | All obs | mean | t (uncorrected) | Independent | mean | **t** |
|---|---|---|---|---|---|---|---|
| 2.0 | top 10% | 145,568 | +0.95 | 3.39 | 11,554 | **−0.84** | −0.98 |
| 4.0 | top 0.1% | 3,717 | +19.31 | 7.04 | 775 | +7.81 | 1.50 |
| 5.0 | top 1% | 2,994 | +25.10 | 7.96 | 526 | +13.97 | 1.95 |
| 6.0 | top 1% | 1,233 | +41.30 | 7.31 | 236 | +30.87 | 2.56 |
| 6.0 | top 0.1% | 818 | +48.84 | 6.24 | 192 | +45.83 | **2.90** |

**t collapses roughly threefold, and the mean falls 25–60%** — so the
duplicated observations were also the better ones. Against this 15-cell
search a Bonferroni-corrected two-sided threshold is |t| ≈ 2.94, which
**nothing reaches**. `|z|≥4` no longer clears the 12bps cost at all, and
the `|z|≥2` cell flips negative.

What survives is weaker and still real: gross outcome does rise
monotonically with selectivity, and the sign (mean reversion, not
trend-following) is confirmed by the inverse returning exactly the
negative in every cell.

### Concentration, which was found before the sampling defect and still holds

2021 supplies roughly **60% of the edge from ~10% of the trades** in
every promising cell, and **2026 is negative in all of them**. Excluding
2021, only `|z|≥6, top 1%` clears cost (+18.24bps against +7.63 and
+11.43). 2021 was the leverage-driven bull-and-crash year, exactly where
an extreme-selectivity mean-reversion signal would thrive.

### The error pattern this exposed, named so it stops recurring

Three of this session's largest errors share one shape: **a result from
one arbitrary configuration generalised to a whole domain.**

1. Costs compared to the *unconditional* move distribution → "minutes-scale is impossible" (false)
2. A *directional* hypothesis tested by measuring *magnitude* → "levels carry nothing" (false)
3. One operating point at 57 trades/day → "scalping cannot clear costs" (false)

Each was caught by the operator's pushback or by a measurement, never by
my own reasoning. The rule this adds to the methodology: **never conclude
about a domain from a single parameter setting — sweep it first.**

## Status

Not a closeout, and not a candidate either. The selectivity direction was
real enough to be worth building and testing properly, which **Task S14
did** — `.planning/scalp-s14-selective-reversion.md`. That candidate went
through 83 folds and 721 trades of real walk-forward validation and was
**REJECTED**, and the diagnosis is what produced the sampling correction
recorded above.
