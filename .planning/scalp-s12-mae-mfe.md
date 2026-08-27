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
| Win rate | **47.4%** |
| **Mean outcome, gross** | **+4.28 bps** |
| Mean outcome, net of 12bps | **−7.72 bps** |

**The gross edge is real and it matches S11's IC.** A ~−0.05 IC at the
60-minute horizon predicts a small positive expectancy from fading, and
+4.28 bps per position is exactly that magnitude. The signal is not
imaginary.

**And it does not matter, because:**

| Component | bps |
|---|---|
| Gross edge per position | **+4.28** |
| Taker fee, round trip | **10.00** |
| Slippage, round trip (S9 measured) | 2.00 |
| **Net** | **−7.72** |

**The fee alone is 2.3x the gross edge.** Slippage is a rounding error
here — S9 already established the fee dominates, and this is the first
time that has been measured against a real signal rather than against an
unconditional price move. Even with *zero* slippage this entry loses
5.72 bps per position.

## Stop placement: S6's 1.5 ATR was far too tight

The measured excursion distribution, and what each candidate stop would
have cut:

| Stop | Winners cut | Losers cut |
|---|---|---|
| 1.00 ATR | 58.2% | 96.2% |
| **1.50 ATR** (S6's choice) | **43.4%** | 91.3% |
| 2.00 ATR | 32.3% | 85.1% |
| **2.78 ATR** (winners' p80) | **20.0%** | 73.6% |
| 3.90 ATR (winners' p90) | 10.0% | 56.4% |

**S6 chose 1.5 ATR by convention and never measured it. On this sample it
would have destroyed 43.4% of eventual winners.** That is the concrete
version of S8's complaint that the multipliers "were never measured
against anything" — now with a number attached.

Sweeney's boundary sits around **2.78 ATR** (winners' 80th percentile),
which still truncates 73.6% of losers. Whether that trade is worth making
depends on a target and a risk budget, neither of which exists yet.

## Two diagnostics that both fire

**Fragility: does NOT fire, and an earlier version of this document
wrongly said it did.** Mean winning-position MAE is **0.638 R** measured
against the recommended 2.78 ATR stop — just inside Sweeney's 0.7R
threshold. The earlier claim compared a raw **1.856 ATR** figure against
a threshold denominated in **R**, which silently assumed 1R = 1 ATR. R is
multiples of the *planned risk*, and it does not exist until a stop is
chosen, so that comparison was meaningless. `fragility_check` now
requires the planned risk to be supplied and the number is reported in R.

Worth stating precisely: 0.638R is *inside* the threshold but not
comfortably. Winners routinely give back around two thirds of the planned
risk before recovering, so the entry is not fragile by Sweeney's test —
but it is not far off it either.

**Exit quality.** Median MFE capture is **0.119** — the typical position
keeps about 12% of the best excursion it showed, well below the 35-55%
band practitioner literature treats as typical. (An earlier version
reported **−0.094** by dividing a *net* outcome by a *gross* MFE, which
folded the cost structure into a number meant to measure exit timing.
Both sides are now gross.) A pure time-based exit is leaving most of the
available move on the table, which is what a target is for.

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

## Corrections made on review

Seven findings, all valid, several changing reported numbers:

| Finding | Effect |
|---|---|
| Entry used the fill bar's **close**, not its **open** | `simulate_fill` fills at `next_bar.open`. Using the close also let pre-entry intrabar movement count as excursion. Fixed; win rate 46.9% → 47.4%, gross +3.93 → +4.28 bps |
| Excursions documented as net, implemented as gross | Contract **amended to gross**, with reasoning: a stop is placed on *price* and triggers regardless of fees, so deducting half a round trip from MAE would misplace every stop derived from it |
| `mfe_capture` mixed a net numerator with a gross denominator | Now gross over gross; −0.094 → **+0.119** |
| 0.7 threshold applied to ATR, not R | Invalidated the fragility claim entirely. `fragility_check` now requires the planned risk; the warning **no longer fires** (0.638R) |
| Percentile off by one (`int(n*p)`) | Now nearest-rank `ceil(n*p)-1`, so "80% stayed inside" is true rather than false by one observation |
| Flat outcomes counted as losers | Now strictly negative; a flat position is neither |
| No runnable reproduction | `research/analysis/s12_excursion_run.py` committed |

## Honest limits

- **One provisional entry, not a strategy.** Different thresholds,
  horizons, or signal weights would give different excursion
  distributions. The stop recommendation is conditional on this entry.
- **MAE is a lower bound.** Bar high/low cannot see the intrabar path, so
  the true worst excursion is at least this bad and possibly worse.
- **Equal weights are unfitted, not optimal — and unfitted does not mean
  it is a floor.** Fitting the weights here would be exactly the
  overfitting S8 warns about, so they were left equal. But other weights
  could produce a *lower* gross edge just as easily as a higher one:
  every number here is scoped to this specific equal-weight provisional
  entry and says nothing about the best achievable from these signals.
- **This is a selection step** and counts toward the project-level trial
  budget when a real strategy is eventually registered.
- Measured on already-spent windows; no holdout accessed, nothing logged
  to `runs/experiments.jsonl`.

## Reproduction

Committed as a runnable module, because describing the inputs is not the
same as being able to regenerate them — the trailing z-scores, the
activity filter and the entry selection all have to be reproducible, not
just the excursion functions:

```
PYTHONPATH=python python/.venv/bin/python -m research.analysis.s12_excursion_run
```

`--json` emits the full result set machine-readably and prints a sha256
of the payload to stderr, so an independent re-run can be compared byte
for byte rather than eyeballed. `--db-path` selects the kline database
(default `python/data/var/klines.sqlite3`).

Every constant lives at the top of that module and is either conventional
(ATR period 14, 2-sigma entry, 1,440-bar z-score window) or measured
elsewhere in this arc (12bps round trip from S9, the 90th-percentile
activity threshold from S9, the 60-minute horizon and the 4-hour lag from
S11). None was fitted here.

Symbol `BINANCE-FUTURES:BTCUSDT`, all 3,661,780 bars, already spent.
