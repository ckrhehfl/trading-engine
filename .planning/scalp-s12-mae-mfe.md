# Scalping Strategy Research Task S12 — MAE/MFE, and the number that decides everything

S8 Part 4 step 5, executed 2026-08-26. Builds the excursion machinery
S8 §3.7 specified, then runs it on provisional entries built from S11's
orthogonal signals.

**The headline is not the stop placement.** It is that this entry has a
small, real, positive gross edge — and the taker fee alone is more than
twice that edge. Everything else follows from that.

## What was built

`python/research/excursion.py`, 21 tests. Implements S8 §3.7's pinned
calculation contract so the same positions cannot yield different
R-multiples depending on who measured them: measurement starts at the
**fill** bar (`signal_bar_index + 1`, matching `simulate_fill`), not the
signal bar; excursions are net of costs; the intrabar path uses bar
high/low, which makes MAE a **lower bound** on the true worst excursion
and is disclosed rather than treated as exact; excursions are reported in
**ATR units** as well as bps, because a stop in bps means different
things in a quiet hour and a violent one.

**A real error, found by running it.** The first implementation flagged
*reaching the holding limit* as censored. That made all 106,361 positions
censored and the entire analysis vacuous — the run printed `표본 0`. The
contract means the opposite: a fixed holding period is a legitimate
time-based exit, so the outcome there is real, and **censoring means the
dataset ran out first**. Inverted, with both tests rewritten to pin the
corrected direction and a docstring that says which way round it goes and
why.

## The provisional entry — disclosed as a selection step

MAE/MFE needs entries, so one had to be defined. **This is not a
strategy**: no stop, no target, no sizing, no P&L curve. It exists to
generate positions whose excursions can be measured.

Built from the two signals S11 measured as orthogonal (|r| ≤ 0.006):

- **Price**: `htf_ret_4h`, whose IC is **negative** → fade the move
- **Flow**: taker-buy share, IC also negative → fade aggressive buying

Each z-scored over a trailing 1,440-bar window (the current bar excluded
from its own reference), summed with **equal weight — no fitted
weights** — entering when |z-sum| ≥ 2.0, restricted to the top 10% of
activity per S9. Holding 60 minutes, the horizon S11's ICs were
strongest at. Costs 12bps per S9.

That is a search step and counts as one; it is recorded here rather than
absorbed silently.

## Result: a real edge, comprehensively eaten by fees

106,361 positions, zero censored.

| | |
|---|---|
| Win rate | **46.9%** |
| Mean outcome, net of 12bps | **−8.07 bps** |
| Median outcome, net | −5.79 bps |
| **Mean outcome, gross** | **+3.93 bps** |

**The gross edge is real and it matches S11's IC.** A ~−0.05 IC at the
60-minute horizon predicts a small positive expectancy from fading, and
+3.93 bps per position is exactly that magnitude. The signal is not
imaginary.

**And it does not matter, because:**

| Component | bps |
|---|---|
| Gross edge per position | **+3.93** |
| Taker fee, round trip | **10.00** |
| Slippage, round trip (S9 measured) | 2.00 |
| **Net** | **−8.07** |

**The fee alone is 2.5x the gross edge.** Slippage is a rounding error
here — S9 already established the fee dominates, and this is the first
time that has been measured against a real signal rather than against an
unconditional price move. Even with *zero* slippage this entry loses
6.07 bps per position.

## Stop placement: S6's 1.5 ATR was far too tight

The measured excursion distribution, and what each candidate stop would
have cut:

| Stop | Winners cut | Losers cut |
|---|---|---|
| 1.00 ATR | 62.7% | 96.2% |
| **1.50 ATR** (S6's choice) | **45.1%** | 91.0% |
| 2.00 ATR | 32.7% | 84.5% |
| **2.77 ATR** (winners' p80) | **20.0%** | 73.1% |
| 3.88 ATR (winners' p90) | 10.0% | 55.9% |

**S6 chose 1.5 ATR by convention and never measured it. On this sample it
would have destroyed 45% of eventual winners.** That is the concrete
version of S8's complaint that the multipliers "were never measured
against anything" — now with a number attached.

Sweeney's boundary sits around **2.77 ATR** (winners' 80th percentile),
which still truncates 73% of losers. Whether that trade is worth making
depends on a target and a risk budget, neither of which exists yet.

## Two diagnostics that both fire

**Fragility.** Mean winning-position MAE is **1.856 ATR**, far above
Sweeney's 0.7 threshold. These "winners" are rescue trades that went
deeply against the position before recovering, so a small worsening of
conditions converts a large share of them into losses. A strategy resting
on them is structurally fragile even when its win rate looks respectable.

**Exit quality.** Median MFE capture is **−0.094** — negative, meaning
the typical position ended *below its entry* despite having shown some
favourable excursion along the way. Under a pure time-based exit that is
what a losing rule looks like from the inside.

## What this changes

1. **A ~4bps gross edge cannot be traded as a taker.** The binding
   constraint is now explicit and quantified: any candidate must clear
   ~12bps round trip, and this one clears a third of it. Either the
   signal gets much stronger, or the holding period gets long enough for
   the move to grow, or execution moves to maker fees (~2bps round trip,
   which would make +3.93 gross marginally positive) — and that last
   route requires hardening `fill.py`'s optimistic limit-fill model
   first, which S9 already flagged.
2. **Stops must come from this distribution, not convention.** 2.77 ATR,
   not 1.5.
3. **The fragility signal is a warning about the entry, not the stop.**
   Winners digging 1.86 ATR on average says the entry is early — it fades
   strength before strength has finished. That is a signal-timing problem
   no stop placement fixes.

## Honest limits

- **One provisional entry, not a strategy.** Different thresholds,
  horizons, or signal weights would give different excursion
  distributions. The stop recommendation is conditional on this entry.
- **MAE is a lower bound.** Bar high/low cannot see the intrabar path, so
  the true worst excursion is at least this bad and possibly worse.
- **Equal weights are unfitted, not optimal.** Deliberate — fitting the
  weights here would be exactly the overfitting S8 warns about — but it
  means the gross edge is a floor, not a ceiling.
- **This is a selection step** and counts toward the project-level trial
  budget when a real strategy is eventually registered.
- Measured on already-spent windows; no holdout accessed, nothing logged
  to `runs/experiments.jsonl`.

## Reproduction

`research.excursion.measure_excursion` over entries as described above,
`max_hold=60`, `cost_bps=12`, ATR(14) at entry as the unit.
`recommend_stop(unit="atr")`, `fragility_check`, `mfe_capture_rate` for
the summaries. Symbol `BINANCE-FUTURES:BTCUSDT`, all 3,661,780 bars.
