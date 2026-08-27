# Scalping Strategy Research Task S13 — the holding-horizon sweep, and what it closes

Executed 2026-08-28. The last open question in the S8 work order, and it
answers negatively — decisively enough that the honest next step is a
recommendation to stop this line rather than to try a fourth candidate.

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

## Recommendation

**Stop this line and write the retrospective.** Not because the research
failed to produce results — it produced several — but because the
specific question "can this signal family be traded profitably at
minutes-to-hours horizons on BTC" now has a measured answer, and it is
no.

Continuing would mean either searching for a 10x stronger signal in a
space where the strongest of ~20 candidates reached IC 0.05, or waiting
for a fee structure that does not exist at this account tier. Neither is
research.

**This is a human decision, not one this document makes.** The
alternatives, stated fairly: a different asset class or data source
(S8's own remaining structural remedy); the equity-compounding sizing
work S7 left half-done; or returning attention to the daily line already
in paper trading, whose 30-day clock is the only thing currently running.
