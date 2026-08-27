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

### But this is not yet an edge, and the reason is in the same data

Significance is strong — t = 7.0 to 8.0 across the promising cells, far
above what even a Bonferroni correction for the 15-cell sweep would
demand. The problem is **concentration**:

| Cell | 2021's share of trades | 2021's share of the edge |
|---|---|---|
| \|z\|≥4, top 0.1% | 11.1% | **65%** |
| \|z\|≥5, top 1% | 9.6% | **59%** |
| \|z\|≥6, top 1% | 8.4% | **60%** |

Excluding 2021, only the most selective cell still clears cost:

| Cell | 7 years ex-2021 | |
|---|---|---|
| \|z\|≥4, top 0.1% | +7.63 bps | ✗ |
| \|z\|≥5, top 1% | +11.43 bps | ✗ |
| \|z\|≥6, top 1% | **+18.24 bps** | ✓ |

And **2026, the most recent year, is negative in all three** (−23.9 /
−29.3 / −26.7 bps).

2021 was the leverage-driven bull-and-crash year; an extreme-selectivity
mean-reversion signal firing on violent moves is exactly what would have
thrived there. Whether that is a regime the strategy needs, or an
artefact, is the question the validation pipeline exists to answer.

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

Not a closeout. `|z|≥5, top 1%` and `|z|≥6, top 1%` are the first real
candidates this arc has produced, and they now go into the walk-forward /
DSR machinery that has never yet had anything worth running through it.
