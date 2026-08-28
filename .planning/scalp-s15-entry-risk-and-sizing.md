# Scalping Strategy Research Task S15 — the three remedies S14 pointed at, all three measured

Executed 2026-08-28, immediately after S14's REJECTED verdict. S14's
diagnosis named three candidate fixes; the operator asked for all three.
None was assumed — each was measured before anything was built on it, and
two of the three turned out to be wrong in ways worth recording.

**Outcome: still REJECTED, but for a different reason than S14.** Removing
the stop improves mean fold Sharpe **4.4x** (−1.471 → −0.333) and takes
profit factor from 1.248 (FAIL) to 2.513 (PASS). It is still negative. Three
independent structural remedies each moved the result in the right
direction and none crossed zero, which is what "the signal is not there"
looks like as opposed to "the risk management was wrong".

---

## (a) Enter later — NO, and the reason retires the idea rather than tuning it

S12 measured that winners dig 1.86 ATR against themselves before working,
which reads as "the entry is early". If so, waiting should shrink the
adverse excursion and make a stop affordable.

It shrinks the excursion exactly as predicted, and takes the outcome with
it. `|z|≥6`, top 1%, non-overlapping positions:

| Entry | gross | net @12bps | t | mean MAE |
|---|---|---|---|---|
| immediate (S14) | **+30.87bps** | +18.87 | 2.56 | 4.65 ATR |
| +5 bars | +18.92 | +6.92 | 2.05 | 3.49 |
| +10 bars | +7.55 | −4.45 | 0.86 | 3.18 |
| +30 bars | −2.18 | −14.18 | −0.32 | 2.54 |
| +60 bars | +3.93 | −8.07 | 0.71 | 2.23 |
| turn, 25% retrace | +23.58 | +11.58 | 1.90 | 4.25 |
| turn, 50% retrace | +24.44 | +12.44 | 2.10 | 3.91 |

Mean MAE falls monotonically with delay and so does the outcome. **The
adverse excursion is not a cost paid before the edge arrives; it is the
edge.** A confirmation entry (wait for the move to give back 25–50% of its
adverse excursion, rather than for a fixed number of bars) is the best of
the delayed variants — it keeps ~76–79% of the gross for a slightly lower
MAE — and is still strictly worse than immediate entry on net outcome and
on t.

This is not a "tune the delay" result. Every delay tested is worse, and
monotonically so.

## (b) A better stop — NO, and the first hypothesis was wrong

**The obvious hypothesis, tested and rejected.** S14 used 2.65 ATR, from
S12's winners'-MAE p80 measured at `|z|≥2` in the top 10% of activity (57
trades/day), while S14 traded `|z|≥5..6` in the top 1% (0.1–0.2/day) — a
population ~29x more selective. "The stop came from the wrong population"
is the natural diagnosis and it is **false**: re-running S12's own
`recommend_stop` on the correct population gives **2.71 ATR** (`|z|≥5`)
and **3.36 ATR** (`|z|≥6`). The winners' MAE p80 barely moves.

What does move is the *mean* MAE over all positions (4.65 ATR), which is
dominated by losers — median loser MAE is 5.0–5.4 ATR against a median
winner MAE of 1.0–1.2. Conflating the mean with the p80 is what made the
hypothesis look right. Recorded rather than quietly deleted, because the
wrong version of this claim was stated out loud before it was measured.

**The real finding needed a different question**: not "how many winners
does the stop destroy" but **"what does the stop realise on the positions
it catches, versus what those positions actually did without it?"**

`|z|≥5`, top 1%, 526 independent positions:

| Stop | win cut | lose cut | stop takes | they did | verdict | all-in mean |
|---|---|---|---|---|---|---|
| 1.50 ATR | 42.5% | 94.6% | −43.5bps | −34.8bps | **HURTS** | +8.04 |
| 2.65 (S14) | 21.3% | 82.9% | −76.2 | −58.2 | **HURTS** | +4.72 |
| 4.00 | 8.2% | 62.0% | −115.0 | −96.5 | **HURTS** | +7.59 |
| 6.00 | 4.1% | 41.9% | −184.0 | −135.3 | **HURTS** | +2.95 |
| 8.00 | 1.1% | 27.9% | −235.1 | −192.2 | **HURTS** | +7.85 |
| 12.00 | 0.0% | 14.7% | −368.4 | −280.0 | **HURTS** | +7.59 |
| **none** | — | — | — | — | — | **+13.97** |

Identical pattern at `|z|≥6` (none = +30.87 beats every stop). **At every
width tested, in both cells, the stop realises a larger loss than the
position it catches would have taken on its own.** It manufactures losses
rather than avoiding them. The "lose cut 82.9%" figure that makes a stop
look good is measuring the wrong thing: those positions were mostly
heading for a *small* loss.

Stated with its limits: this assumes a stopped position does not re-enter
and cannot know intra-bar ordering, so it is not a backtest. The sign is
not close enough for either to change it, and the walk-forward below
confirms it directly.

## (c) Equity-aware sizing — built, and it is real infrastructure

`compute_position_size` sizes against a **fixed** `reference_equity`, so a
strategy that has lost 90% of its account still risks 1% of the
*original* — 10% of what remains. That is the mechanism behind S6's
−239,161% run. S7 added the zero-equity circuit breaker and explicitly
left this half open; `compute_position_size`'s own docstring names the
blocker, that a strategy "has no legitimate way to observe" its equity.

**`backtest.engine.EquityObserver`** is that legitimate way. `run_backtest`
*already* reconstructs mark-to-market equity every bar when
`starting_equity` is supplied — that is how the S7 floor works — so this
hands over a value it already has. Look-ahead safety holds by
construction: equity for bar `i` is built only from fills with
`fill_time <= klines[i].open_time`, and is delivered *before* the strategy
is asked for an intent. Duck-typed and optional; a plain callable strategy
and a run without `starting_equity` both behave exactly as before.

`SelectiveReversionStrategy` gains `sizing_mode`. In `compounding` it
sizes against current equity and **fails closed** — if no equity was ever
delivered it refuses to trade and counts the refusals on
`declined_no_equity`, rather than silently falling back to the constant
while reporting itself as compounding.

## The combined walk-forward

Two cells, declared before running so the second isolates sizing rather
than being reached for after seeing the first. 83 folds, same geometry and
costs as S14.

| | S14 (2.65 ATR stop) | no stop + compounding | no stop + fixed |
|---|---|---|---|
| mean fold Sharpe | −1.4709 | **−0.3326** | −0.2954 |
| folds Sharpe > 0 | 36.1% | **45.8%** | 45.8% |
| mean profit factor | 1.248 ✗ | **2.513** ✓ | 2.553 ✓ |
| median fold PF | 0.908 | 1.175 | 1.082 |
| worst drawdown | 12.44% | 22.55% | 23.09% |
| trades | 721 | 481 | 481 |
| compounded | −73.3% | −50.6% | −51.0% |
| **verdict** | REJECTED | **REJECTED** | REJECTED |

Removing the stop is worth **4.4x on mean Sharpe** and flips profit factor
from fail to pass — the largest single improvement any change has produced
in this arc. It is still not an edge: mean Sharpe is negative and fewer
than half the folds are positive.

**Compounding sizing is ~neutral here** (−0.3326 vs −0.2954 Sharpe; 22.55%
vs 23.09% drawdown). That is the expected result for a strategy that never
compounds far in either direction, and is not evidence against the
mechanism — it is evidence that sizing was not what was wrong.

## A weakness in the scoring, found by this run

The no-stop cell passes the profit-factor floor on a **mean of 2.55 while
its median fold is 1.18** — and S14 passes nothing but its median is 0.91
against a mean of 1.25. A profit factor is a ratio of two non-negative
magnitudes, so a fold with almost no losing trades produces an enormous
value and can drag the mean across the floor by itself.

CLAUDE.md sets the floor without naming which statistic it applies to, and
`walkforward` aggregates the mean, so the mean is still what is scored —
changing that is a gate change and needs its own approval. But
`s14_eligibility.py` now prints the median beside it and flags
**FRAGILE** whenever the mean passes and the median does not. Reporting it
is not optional.

## What this closes and what it does not

**Closes**: the `selective-reversion` signal (`z(htf_ret_4h) +
z(taker_buy_share)` faded at high selectivity) as a tradeable edge at 1m.
Three structural remedies, each measured rather than assumed, each moving
the result the right way, none reaching zero. Re-running any of these
specs or searching around them is foreclosed.

**Does not close, and should be reused**:

- **`EquityObserver` and `compounding` sizing** — infrastructure, closing
  a gap S6 exposed and S7 half-fixed. Neutral here only because this
  strategy never ran long enough to compound.
- **The CSTI structure and the R:R gate** — untouched by any of this.
- **"The excursion is the edge" for mean reversion** — measured twice now
  (S14's stop diagnosis, S15's delay sweep). A mean-reversion entry cannot
  be made safer by entering later or by stopping earlier; both remove the
  same thing they are meant to protect.
- **Order flow's orthogonality to price** (S11, |r| ≤ 0.006) — nothing
  here disconfirms it.

**Explicitly not a live option**: another threshold, lookback or stop
width on this window. `N` is now **125**.

## Artefacts

| File | What it is |
|---|---|
| `python/backtest/engine.py` | `EquityObserver` protocol + the one-line delivery in the bar loop |
| `python/research/strategies/selective_reversion.py` | `sizing_mode`, `use_stop`, `on_equity` |
| `python/research/analysis/s15_entry_timing.py` | (a) the delay and confirmation sweep |
| `python/research/analysis/s15_risk_control.py` | (b) the stop measurement, including the disproved hypothesis |
| `python/research/analysis/s15_walkforward_run.py` | the two logged cells |
| `python/research/analysis/s14_eligibility.py` | `--strategy-id`, median-PF fragility flag |

Logged runs: `bd58a14e-be8a-4b34-9e08-b9308b6ac142` (compounding),
`28d77f0d-6734-4125-8d09-d4cf0b74c68f` (fixed). Both
`is_holdout_run=false`, both counting toward `N`.
