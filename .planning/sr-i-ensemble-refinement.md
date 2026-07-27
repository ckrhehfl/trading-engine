# Strategy Research Task I: diagnosing and refining the ensemble-momentum strategy

## Scope note

`.planning/sr-h-ensemble-regime-voltargeting.md`'s real 19-fold walk-forward
run showed the multi-lookback ensemble strategy beating the single-
lookback baseline on every quality metric (mean Sharpe -1.35 vs -2.10, 8/19
vs 3/19 positive-Sharpe folds, worst drawdown 8.9% vs 19.5%, mean profit
factor 1.28 vs 1.04) -- genuinely closer than anything tried before in this
project, but still short of CLAUDE.md's Eligibility Bar (positive Sharpe in
*every* fold; profit factor floor 1.3-1.5). The human explicitly asked to
keep refining the ensemble rather than move on to something else. This
document is that refinement: a real diagnosis of *why* the -1.35 mean
Sharpe happens, followed by only the refinements that diagnosis actually
supports, tested honestly (including the ones that didn't help).

Everything below is a real, evidenced result against real cached BingX 1h
data -- no numbers here are hand-derived or estimated.

## Step 1: diagnosis

### Is the negative result driven by a few catastrophic tail folds, or is it uniform?

Neither, cleanly. Re-sorting the original ensemble's 19 real per-fold
Sharpe values (from sr-h's own table):

```text
-7.61, -5.73, -4.88, -4.17, -4.04, -4.03, -3.88, -3.81, -3.73, -1.23, -1.22,
0.63, 0.82, 1.41, 1.7, 3.05, 3.47, 3.61, 4.05
```

mean=-1.347, sample stdev=3.600. There's a real gap between -3.73 and
-1.23 (a genuine two-cluster shape: 6 folds at or below -4.0 -- call these
"severely negative" -- and a separate cluster of 5 folds between -1.2 and
-3.9), plus 8 positive folds. This is closer to "a substantial minority of
folds (6/19, 31.6%) are severely negative, with the remaining negative
folds only moderately so" than either extreme framing in the task brief.
It's not one or two outliers dragging down an otherwise-fine strategy, but
it's also not uniform mediocrity across all 19 folds -- 8/19 (42%) are
already positive. This matters for what kind of fix helps: a couple of
outliers would point at a specific event/bug; a genuinely uniform result
would suggest the core signal has no edge at all. What actually showed up
(a real 2-cluster split, with a healthy positive tail too) pointed toward
*cost/margin* diagnosis rather than "find and patch one broken fold" --
which is exactly what the trade-level analysis below confirms.

### Trade-level diagnosis (real reconstruction via `metrics/position.py`, not aggregate-only)

Reproduced Task H's exact 19-fold walk-forward for the ensemble
(`EnsembleMomentumStrategy` with every original default, same
`fee_bps=5`/`slippage_bps=2`, same windows) bar-for-bar, instrumented to
capture each closed trade's exit reason (stop/target/forced-close-at-fold-
end) and each entry's constituent-pair conviction. 262 real trades
(matches sr-h's `total_trades` exactly).

**Exit-reason breakdown (all 19 folds, 262 trades)**:

| exit reason | count | win rate | total gross P&L | mean P&L |
|---|---|---|---|---|
| target hit | 90 | 100.0% (by construction) | +$9,920.83 | +$110.23 |
| stop hit | 165 | 1.2% | -$9,884.96 | -$59.91 |
| forced close (fold end) | 7 | 14.3% | -$101.87 | -$14.55 |

Excluding forced closes (a fold-boundary artifact, not a real trading
outcome), the real target-hit rate is `90 / (90 + 165) = 35.3%`. The fixed
1:2 stop:target ratio (`DEFAULT_STOP_MULTIPLIER=1.5` /
`DEFAULT_TARGET_MULTIPLIER=3.0`) implies a **33.3% breakeven win rate**
before costs (`1 / (1 + 2)`). The real trade-level win rate sits barely
above that breakeven line -- a thin margin, not a comfortable one.

**Cost breakdown, decomposed by re-running the same 19 folds with
`fee_bps`/`slippage_bps` independently zeroed out** (this project's fee
and slippage are two structurally separate costs -- `backtest/fill.py`'s
`simulate_fill` applies slippage to the *fill price itself* before
`metrics/position.py` ever computes a trade's `realized_pnl`, while the
exchange commission (`Fill.fee`) is tracked entirely separately and only
deducted at the equity-curve level in `metrics/metrics.py` -- so "gross
trade P&L" and "total fees paid" are not directly additive without care,
and an earlier draft of this diagnosis conflated the two; corrected here):

| configuration | gross trade P&L (sum of `realized_pnl`, already reflects any modeled slippage) | exchange fees paid (`Fill.fee`, `fee_bps` only) | net (P&L - fees) |
|---|---|---|---|
| zero cost (`fee_bps=0`, `slippage_bps=0`) | **+$792.52** | $0.00 | +$792.52 |
| slippage only (`fee_bps=0`, `slippage_bps=2`) | -$66.00 | $0.00 | -$66.00 |
| fees only (`fee_bps=5`, `slippage_bps=0`) | +$792.52 | $2,146.30 | -$1,353.78 |
| **real (`fee_bps=5`, `slippage_bps=2`) -- what Task H actually ran** | -$66.00 | $2,146.30 | **-$2,212.30** |

The real, corrected picture: **the raw directional signal itself has
genuine, if modest, positive edge** (+$792.52 gross across all 262 real
trades at zero cost -- consistent with the 35.3% real target-hit rate
sitting just *above*, not at, the 33.3% breakeven line). **Two separate
costs erode it, not one**: modeled slippage (2bps each way on every market
order) alone is large enough to flip that positive edge to a $66.00 loss
-- an $858.52 swing from a nominally small 2bps rate, because it's applied
against price levels the strategy's own stop/target logic was sized
around. Exchange fees (`fee_bps=5`, BingX's real, verified VIP0 taker
rate -- see below) are a further, larger, and separate -$2,146.30 on top,
landing at the real net -$2,212.30 (matching the dollar loss implied by
`mean_total_return=-1.164%` across 19 folds of $10,000 starting equity --
`-0.01164 * 19 * 10000 = -$2,212.36`, matching to rounding). **Trading
costs -- slippage and fees together, not a negative raw signal -- account
for essentially the entire aggregate negative result.** This is the single
most important finding of this diagnosis, corrected from an earlier draft
that inaccurately described the combined $2,146.30+slippage effect as one
undifferentiated "fees" line item.

**Holding periods**: winners held for a mean 19.3 hours; losers for a mean
11.5 hours -- consistent with the ATR stop/target structure (a winning
trade rides to a target 2x farther away than the stop; a losing trade gets
stopped out faster). No unexpected pattern here.

**Conviction breakdown (corrected)**: an earlier version of this
diagnostic's instrumentation had a real bug -- it tried to recover each
trade's "how many of the 3 lookback pairs agreed" reading by aligning
`_combined_sign` call indices to bar indices, but `_combined_sign` is only
called once `have_all_pairs` is `True` (after the longest pair's 72-bar
warmup), not from bar 0 -- so the alignment was off by the warmup length,
producing nonsensical (`agreeing_pairs` of 0 or 1 for trades whose
`current_sign` could only mathematically arise from at least 2 pairs
agreeing) results. Fixed by recomputing each pair's sign **directly** from
the close-price history at the exact signal bar, using
`EnsembleMomentumStrategy`'s own SMA formula -- deterministic, no
instrumentation/call-order assumption needed. The corrected result: **257
of 262 real trades (98.1%) fired on a 2-of-3-pairs-agree signal; only 5
(1.9%) fired on a genuine 3-of-3 unanimous signal.** This is a real,
structural fact about continuous BTC price data, not noise: an SMA
crossover's sign is exactly zero only at a measure-zero instant, so a
"1-bullish-2-flat" case (the only way `_combined_sign` could net a signal
from fewer than 2 agreeing pairs) essentially never happens in practice.
**This directly rules out "require 3-of-3 agreement" as a viable
refinement** -- see Step 2.

### ADX empirical distribution: is the traditional 20/25 convention right for BTC?

Computed `AverageDirectionalIndex` (this project's plain-rolling-mean,
non-Wilder-smoothed variant) over the full research dataset (16,051 usable
bars after warmup) and, separately and leakage-free, over just fold 0's
own 2,160-bar train window (a slice entirely before every fold's validate
window in the whole walk-forward, so this second computation can't leak
information from any fold's evaluation into the diagnosis):

| | full research dataset (n=16,051) | fold-0 train window only (n=2,133, leakage-free) |
|---|---|---|
| `<=20` (regime weight 0) | 14.2% | 11.3% |
| `20-25` (the actual "ramp" zone) | 11.7% | (not separately reported; see percentiles) |
| `>=25` (regime weight 1) | 74.1% | 76.1% |
| p10 / p25 / p50 / p75 / p90 | 17.99 / 24.61 / 34.90 / 47.80 / 61.21 | 19.45 / 25.72 / 35.89 / 50.20 / 63.07 |
| mean | 37.45 | 38.95 |

Both samples agree closely. **The traditional 20/25 convention (calibrated
for traditional-TA use, not this project's own asset+indicator
combination) puts almost the entire distribution above the "trending"
threshold**: on this specific ADX computation, BTC-USDT 1h sits at full
regime-weight conviction (`>=25`) roughly three-quarters of the time, and
the "continuous ramp" the design is actually meant to provide only
actually operates across ~12% of real bars. In practice this means the
ADX layer behaves close to a near-constant full-weight multiplier for the
vast majority of trades -- it isn't doing much of the *continuous*
discrimination work Finding 3 of sr-h's design intended. This is a real,
data-supported case for recalibrating the thresholds to BTC's own
empirical distribution, not a blind convention swap.

### BingX real fee schedule vs. this project's cost assumptions

Verified directly against BingX's own published fee-schedule page
(`https://bingx.com/en/support/articles/360046487573-perpetual-futures-fee-schedule`,
fetched live during this diagnosis): **VIP0 (standard, no VIP tier)
perpetual futures fees are maker 0.02% / taker 0.05%.** Every `OrderIntent`
every strategy in this project emits uses `OrderType.GUARDED_MARKET` (a
market order -- a taker fill), so the applicable real rate is the 0.05%
(5bps) taker rate.

**This project's existing `fee_bps=5` (used in every real walk-forward call
to date, including Task H's) is an exact match to BingX's real, currently-
published VIP0 taker rate.** It is not more conservative than reality --
it's already accurate. Per this task's explicit instruction ("only correct
if assumptions are more conservative than real; if you can't find the real
schedule, keep the existing assumption"), **no fee correction was made or
is warranted.** `slippage_bps=2` is a separate, non-fee cost assumption
(market-order price impact, not a published exchange rate) with no
directly comparable authoritative figure to check it against; left
unchanged for the same reason -- no evidence to revise it either way.

This is itself informative for what's driving the negative result: the
$2,146 in real fees paid across 262 trades isn't an artifact of an overly
conservative assumption this diagnosis gets to relax -- it's what those
262 trades would really cost on BingX today. The fix has to come from
trading fewer/better trades or a better cost-to-edge ratio, not from a
cost-assumption correction.

### Diagnosis summary -> what Step 2 actually tests

1. **Fee correction: not applicable** (already accurate) -- ruled out.
2. **Ensemble conviction requirement (3-of-3 vs 2-of-3): not viable** --
   only 5 of 262 real trades are ever 3-of-3; requiring it would collapse
   trade count to near-zero (failing the Eligibility Bar's 100-trade floor
   outright) on a sample far too small to draw any real conclusion from.
   Not attempted.
3. **Stop/target risk:reward ratio: worth testing.** The real target-hit
   rate (35.3%) sits just above the 1:2 ratio's 33.3% breakeven line -- a
   thin enough margin (and a raw, cost-free edge of only +$792.52 across
   262 trades) that a different ratio could plausibly move the needle, and
   the cost breakdown above means costs (not signal quality) are the
   dominant lever. Tested via an opt-in grid search (below).
4. **ADX threshold recalibration: worth testing.** Real, leakage-free
   empirical evidence that the traditional 20/25 convention doesn't fit
   BTC's actual ADX distribution on this project's own indicator
   implementation. Tested via recalibrated fixed constants (below).

## Step 2: what was tried

### A. Opt-in risk:reward grid search (code change, TDD)

Added an **opt-in** grid search to `EnsembleMomentumTrainable.fit()` in
`python/research/strategies/ensemble_momentum.py`, purely additive: when
`params` omits `"candidates"` entirely, `fit()` is byte-for-byte the
original sr-h behavior (one fixed candidate, `total_candidates=1`) --
every existing caller and every one of sr-h's own tests is unaffected.
When a caller explicitly supplies `params["candidates"]` (a non-empty
sequence of `(risk_reward_tenths,)` 1-tuples -- an integer encoding, `10x`
the target:stop ratio, e.g. `20` means `target_multiplier =
stop_multiplier * 2.0`), `fit()` grid-searches `target_multiplier` across
them per fold, on that fold's own `train_klines` only -- the same
train-only, walk-forward-safe selection
`SingleLookbackMomentumTrainable.fit()` already uses for its own grid.
`stop_multiplier` is never varied. The integer-tenths encoding (rather
than a `Decimal` `target_multiplier` directly) is what lets
`research.overfitting_check`/`research.robustness.check_parameter_sensitivity`
(Task G's overfitting safeguard, built for integer window-length-shaped
candidates) work on this new dimension completely unmodified, per this
task's own brief.

`DEFAULT_RISK_REWARD_TENTHS_CANDIDATES = ((15,), (20,), (25,), (30,))` --
4 candidates spanning 1:1.5 to 1:3, deliberately including `20` (the
original, unsearched ratio) so the search can honestly conclude "no
change" if that's what the data shows.

**10 new tests** added to `python/tests/test_ensemble_momentum.py`
(`TestFitOptionalRiskRewardGridSearch`; 8 written for this refinement
originally, 2 more added during CodeRabbit review -- see "CodeRabbit
review findings" below), written first and confirmed failing before the
production code existed: per-candidate logging shape, candidate-specific
`target_multiplier` in logged params (`stop_multiplier` never varies),
selection logic (isolated via a monkeypatched `compute_metrics` returning
canned per-candidate results, proving `fit()` picks the highest-
`total_return` candidate, not the first or last), zero-trade fallback to
the first candidate, empty-candidate-list/non-positive/non-integer/bool
`risk_reward_tenths` rejection, and confirming the default
(no-`"candidates"`-key) path is genuinely untouched. Plus 1 new test
confirming `target_multiplier`/`stop_multiplier` are now exposed as
properties (needed for a real `sensitivity_extractor`), and 1 new
`TestRealWalkForwardIntegration` case proving the grid search, the real
`run_walk_forward` fold loop, and Task G's `sensitivity_extractor` all
compose correctly end to end.

**Real result (19-fold walk-forward, default ADX thresholds, `run_id=
c1569b01-6334-40e1-97e8-7f6ebeae1ac0`)**: mean Sharpe -1.011 (vs. original
-1.347 -- a small improvement), min Sharpe -7.083 (vs. -7.615, a small
improvement), **but 7/19 folds positive (vs. 8/19 originally -- worse)**,
worst drawdown 10.52% (vs. 8.93% -- worse), mean profit factor 1.270 (vs.
1.284 -- essentially flat/slightly worse), min profit factor 0.165 (vs.
0.302 -- worse). Task G's own `sensitivity_extractor`, wired in for this
run, found only 7 of 19 folds' winning risk:reward candidate "robust" (at
least half its perturbed neighbors also profitable in-sample) -- and 9 of
19 folds' winning candidate wasn't even profitable in-sample to begin
with, a real red flag that the train-optimal choice is itself often weak.

**Honest verdict: the risk:reward grid search, on its own, does not help.**
It's a wash on some axes and a real regression on others (fewer
positive-Sharpe folds, worse drawdown, worse min profit factor). The
near-breakeven diagnosis in Step 1 correctly identified this as *worth
testing*, but testing it honestly shows it doesn't deliver -- reported
here plainly rather than dropped from the record. **Not adopted on its
own.** (Its interaction with the ADX recalibration below is a separate,
more encouraging finding -- see "Combined" below.)

### B. ADX threshold recalibration (constants only, no search)

Added two new named constants to `ensemble_momentum.py`,
`RECALIBRATED_ADX_LOW_THRESHOLD = Decimal("25")` /
`RECALIBRATED_ADX_HIGH_THRESHOLD = Decimal("50")`, derived from the
leakage-free fold-0-train-window percentiles computed in Step 1
(p25~=25.72, p75~=50.20) by **rounding down to the nearest integer**
(`Decimal.to_integral_value(rounding=ROUND_FLOOR)` -- not nearest-integer
rounding, which would give 26 for 25.72; a floor was chosen so the
recalibrated low threshold is never set *above* the real empirical p25,
keeping the "ramp" zone at least as wide as the true interquartile range
rather than narrower than it): `25.72 -> 25`, `50.20 -> 50`. **Not the
constructor default** for either
`EnsembleMomentumStrategy` or `EnsembleMomentumTrainable` --
`regime_weighting.DEFAULT_ADX_LOW_THRESHOLD`/`DEFAULT_ADX_HIGH_THRESHOLD`
(20/25) remain the default for both this strategy and
`single_lookback_momentum.py`, completely unchanged, so every existing
test and sr-h's own historical result stays reproducible against
unmodified defaults, and `single_lookback_momentum.py` (untouched by this
task) is entirely unaffected. A caller opts in explicitly by passing
`adx_low=RECALIBRATED_ADX_LOW_THRESHOLD, adx_high=
RECALIBRATED_ADX_HIGH_THRESHOLD` to `EnsembleMomentumTrainable`'s
constructor -- exactly the same "purely additive, opt-in" shape as the
risk:reward grid. **1 new test** confirms the constants differ from the
(still-unchanged) defaults and that they construct a working strategy.

**Real result (19-fold walk-forward, fixed candidate as before, no
risk:reward search, `run_id=350f00bb-e528-4f16-9a2c-14d4b8ffffab`)**: a
substantial, across-the-board improvement over the original: mean Sharpe
-0.627 (vs. -1.347), 9/19 folds positive (vs. 8/19), worst drawdown 4.23%
(vs. 8.93% -- roughly half), mean total return -0.04% (vs. -1.16% --
essentially flat instead of clearly negative), mean profit factor **1.907**
(vs. 1.284 -- now well clear of the 1.3-1.5 eligibility floor), total
trades 213 (fewer, as expected -- the wider zero-weight zone below 25
suppresses more marginal entries). The one real cost: min Sharpe got worse
(-9.992 vs. -7.615) and min profit factor got worse (0.096 vs. 0.302) --
both driven by a single fold (fold 17, `[2025-12-18 -> 2026-01-17]`), which
was already one of the worse folds under the original thresholds (-3.727)
and got meaningfully worse under recalibration (-9.992). Flagged plainly,
not hidden: **this refinement has a real, concrete tail-risk cost in
exchange for its broad average improvement**, not a free win.

### C. Combined (ADX recalibration + risk:reward grid search)

Tested for completeness, since B helped substantially and A was neutral-
to-negative alone -- worth checking whether A's mechanism behaves
differently once B has already cleaned up which trades actually fire.

**Real result (19-fold walk-forward, both refinements together, `run_id=
e75c91e2-4959-4178-9f01-cf14412c3cfc`)**: **better than B alone on 6 of the
7 quality metrics measured, tied on the 7th (worst drawdown, identical
4.23% in both) -- never worse on any of them** -- mean Sharpe **+0.027**
(positive!, vs. B alone's -0.627), 11/19 folds positive (vs. 9/19 for B
alone), mean total return **+0.21%** (positive, vs. -0.04%), mean profit
factor 1.967 (vs. 1.907), min Sharpe -8.442 (vs. B alone's -9.992, less
bad), min profit factor 0.128 (vs. B alone's 0.096, less bad), total
trades 199 (fewer than B alone's 213, still comfortably above the
100-trade floor -- trade count is a volume figure, not itself scored as
better/worse, same convention sr-h used). Fold 17 remains the worst fold
across every variant tested (original -3.727, B -9.992, combined -8.442)
-- recalibrating ADX makes this specific fold meaningfully worse under
every configuration that includes it, a consistent, disclosed weak point
of the recalibration, not something the risk:reward search fixes.

**This is a real, not-just-noise interaction**: A alone was a wash/mild
regression; combined with B it improves on every axis relative to B alone
(not a mixed result where some metrics improve and others regress, which
is what pure noise would tend to produce). A plausible mechanism: once the
ADX filter is actually discriminating real trending-vs-choppy periods for
BTC (B's fix), the *quality* of the surviving trade set changes enough
that the risk:reward choice starts to matter in a way it didn't on the
original, noisier trade set.

**Honest caveat, stated plainly**: this conclusion rests on comparing 4
full walk-forward configurations (original, A alone, B alone, combined) --
a real, if modest, multiple-comparisons exposure. `research.
overfitting_check.check_combination_count("ensemble-momentum")`, re-run
after all of this task's real runs, now reports:

```json
{
  "strategy_id": "ensemble-momentum",
  "total_combinations_tried": 109,
  "parent_run_groups": {
    "f055735a-3882-4ae5-9782-8e0ab42a2a03": 1,
    "350f00bb-e528-4f16-9a2c-14d4b8ffffab": 1,
    "c1569b01-6334-40e1-97e8-7f6ebeae1ac0": 4,
    "e75c91e2-4959-4178-9f01-cf14412c3cfc": 4
  },
  "standalone_run_count": 99,
  "data_span_years": 1.8352739726027398,
  "combinations_per_year": 59.39,
  "risk_level": "high"
}
```

`risk_level` moved from sr-h's `"low"` (2 combinations) to **`"high"`**
(109). Reported honestly, exactly as sr-h itself predicted would happen
("turning on the parameter-sensitivity check on a strategy with thin real
data will itself measurably raise that strategy's MinBTL-style risk
tier") -- confirmed here, not a new surprise. Breaking down the 109: the 4
`parent_run_groups` entries (1+1+4+4=10) are the actual *configurations*
compared in this task (original, A, B, combined) -- a real but small
number. The other **99 are `standalone_run_count`**, almost entirely
Task G's own `sensitivity_extractor` diagnostic evaluations from run C's
grid search (19 folds x up to 5 evaluations each: 1 winner re-check + up
to 4 perturbed neighbors) -- these are diagnostic-only checks that never
influenced which candidate won, not additional "configurations tried
looking for a better number." Still, the honest total is `HIGH`, and this
is disclosed here rather than only citing the friendlier 10-combination
breakdown.

## Full honest before/after comparison

All four real 19-fold walk-forward runs, identical windows
(`train_bars=2160, validate_bars=720, step_bars=720`), identical data
(`load_research_klines` against `configs/research/holdout_1h.json`,
16,078 bars, `2024-04-27T10:00:00Z` -> `2026-02-26T07:00:00Z`), identical
`fee_bps=5`/`slippage_bps=2`.

| metric | original (sr-h) | A: risk:reward grid alone | B: ADX recalib alone | C: combined (A+B) |
|---|---|---|---|---|
| run_id | `f055735a-...` | `c1569b01-...` | `350f00bb-...` | `e75c91e2-...` |
| mean Sharpe | -1.347 | -1.011 | -0.627 | **+0.027** |
| min Sharpe | -7.615 | -7.083 | -9.992 | -8.442 |
| folds positive Sharpe | 8/19 (42.1%) | 7/19 (36.8%) | 9/19 (47.4%) | **11/19 (57.9%)** |
| worst-fold max drawdown | 8.93% | 10.52% | 4.23% | **4.23%** |
| mean total return | -1.16% | -1.14% | -0.04% | **+0.21%** |
| total trades | 262 | 238 | 213 | 199 |
| mean profit factor | 1.284 | 1.270 | 1.907 | **1.967** |
| min profit factor | 0.302 | 0.165 | 0.096 | 0.128 |

Scoring each configuration against the original on the 7 rows that are
genuinely quality metrics (excluding `total trades`, a volume/activity
figure this project doesn't score as better/worse either way -- same
convention sr-h used):

**A (risk:reward grid alone) is a genuine negative/neutral result vs. the
original** -- worse on 4 of 7 (folds positive, worst drawdown, mean and
min profit factor), better on 3 (mean Sharpe, min Sharpe, mean total
return -- the latter two only marginally). Reported honestly; not adopted
on its own.

**B (ADX recalibration alone) is a genuine, substantial improvement vs.
the original** on 5 of 7, at the real, disclosed cost of a worse tail on
the remaining 2 (min Sharpe, min profit factor -- both driven by fold 17).

**C (combined) is the strongest result found**: better than B alone on 6
of 7 (tied on the 7th, worst drawdown -- see above), and better than the
original on 5 of 7, worse than the original on the same 2 tail metrics B
alone is worse on (min Sharpe, min profit factor, both still driven by
fold 17).

## Eligibility bar evaluation (all four, CLAUDE.md's Backtest/Walk-Forward Eligibility Bar)

All four cleared the 8-10-fold credibility floor (19 folds), so every
criterion is evaluated with no caveat.

| Criterion | Requirement | Original | A (rr-grid) | B (adx-recalib) | C (combined) |
|---|---|---|---|---|---|
| Fold count | >=8-10 | 19 -- PASS | 19 -- PASS | 19 -- PASS | 19 -- PASS |
| Positive Sharpe, every fold | all 19 | 8/19 -- **FAIL** | 7/19 -- **FAIL** | 9/19 -- **FAIL** | 11/19 -- **FAIL** |
| Max drawdown ceiling | <=20-25% | 8.93% -- PASS | 10.52% -- PASS | 4.23% -- PASS | 4.23% -- PASS |
| Minimum total trades | >=100 | 262 -- PASS | 238 -- PASS | 213 -- PASS | 199 -- PASS |
| Profit factor floor | 1.3-1.5 (mean) | 1.284 -- **FAIL** | 1.270 -- **FAIL** | 1.907 -- PASS | 1.967 -- PASS |

**Configuration C (combined) is the first real result in this project's
history to clear 4 of the 5 Eligibility Bar criteria** -- fold count,
drawdown, trade count, and (now comfortably) profit factor. **It still
fails "positive Sharpe in every fold"** -- 11/19 is a real, substantial
improvement over every prior real result in this project (original
ensemble 8/19, single-lookback baseline 3/19), but it is not 19/19, and
CLAUDE.md's bar is explicit that partial credit doesn't count. **This is
genuine, honest progress, not a validated edge.** No holdout access was
made -- the strategy is closer to the bar than anything tried before, but
not at it, so there is nothing legitimate yet to spend either dataset's
one-shot holdout access confirming (same reasoning as every prior task's
identical negative result).

## TDD

`python/tests/test_ensemble_momentum.py` grew from 27 to 40 tests (+13):
`TestFitOptionalRiskRewardGridSearch` (10 tests, including 2 added during
CodeRabbit review -- see below) + 1 new `TestConstruction` test
(stop/target multiplier properties) + 1 new `TestConstruction` test
(recalibrated ADX constants) + 1 new `TestRealWalkForwardIntegration` case
(grid search + `sensitivity_extractor` end to end) = 13 additions. Every
new test was written first and confirmed failing (`AttributeError`/
`Failed: DID NOT RAISE`) before the corresponding production code in
`python/research/strategies/ensemble_momentum.py` existed.

Full suite: **455 passed** (was 442 immediately before this task,
confirmed by running the complete, unfiltered `uv run pytest` suite both
before and after -- nothing from any prior task regressed).

```text
$ cd python && uv run pytest -q
........................................................................ [ 15%]
........................................................................ [ 31%]
........................................................................ [ 47%]
........................................................................ [ 63%]
........................................................................ [ 79%]
........................................................................ [ 94%]
.......................                                                  [100%]
455 passed in 41.50s
```

**One real bug caught during this task's own diagnostic tooling, not in
production code**: an early version of the diagnostic script's
"conviction" instrumentation (attempting to recover each trade's
constituent-pair agreement by aligning `_combined_sign` call indices to
bar indices) produced nonsensical results (`agreeing_pairs=0` or `1` for
trades that could only mathematically arise from >=2 pairs agreeing) --
root-caused to `_combined_sign` only being called from the bar where
`have_all_pairs` first becomes `True` (after the longest pair's 72-bar
warmup), not from bar 0, so a naive index-based alignment was off by the
warmup length. Fixed by recomputing each pair's sign directly from close
history instead of relying on call-order instrumentation -- this was a
diagnostic-script bug, not a production bug; `EnsembleMomentumStrategy`
itself was never wrong, only the ad hoc script trying to introspect it.

## CodeRabbit review findings

One review pass, 5 actionable findings. **All 5 accepted and fixed** --
none declined.

- **MD040 lint: the Sharpe-value list's fenced code block had no language
  identifier.** Real, valid lint finding. Fixed: ` ```text `.
- **Fee/slippage conflation, the most substantive finding.** The original
  draft described the combined $2,146.30 figure as "fees" while its own
  calculation implicitly folded in `slippage_bps=2`'s effect, and called
  the raw signal "near-zero" pre-cost. Investigating this properly (not
  just rewording around it) uncovered a materially more accurate and more
  interesting diagnosis: re-running the same 19 folds with `fee_bps`/
  `slippage_bps` independently zeroed out shows the **raw, zero-cost
  signal is actually genuinely profitable (+$792.52 across 262 trades)**,
  not near-breakeven -- `Fill.fee` (the $2,146.30 figure) is purely the
  exchange-commission component and never touches `ClosedTrade.realized_pnl`
  at all (`metrics/position.py` tracks it entirely separately, deducted
  only at the equity-curve level), while modeled slippage acts on the fill
  price itself and is already embedded in the -$66.00 "gross trade P&L"
  figure. The corrected finding is meaningfully sharper than the original:
  two *separate* real costs (slippage first, turning +$792.52 into -$66.00
  on its own; exchange fees second, a further -$2,146.30) both erode a
  genuinely positive raw edge, rather than one undifferentiated "fees"
  line item erasing a coin-flip signal. Fixed with a full corrected
  cost-decomposition table and rewritten prose in the Step 1 diagnosis.
- **Ambiguous ADX-threshold rounding rule.** "rounded to 25/50" didn't
  specify a reproducible rule -- ordinary nearest-integer rounding of
  `25.72` gives `26`, not `25`. Real, valid precision gap. Fixed: the
  actual rule used (round down / floor, chosen so the recalibrated low
  threshold is never set above the true empirical p25) is now stated
  explicitly.
- **"Better than B alone on every single metric" overclaimed** -- the
  table itself shows worst drawdown tied at 4.23% between B and C, not a
  win for C. A real, valid inconsistency (the same class of overclaim
  sr-h's own CodeRabbit review caught once before, on the "every single
  metric" baseline-vs-ensemble comparison). Fixed: reworded to "better on
  6 of 7 quality metrics, tied on the 7th," with the A-vs-original and
  B-vs-original comparison counts also independently recomputed and
  corrected (excluding `total trades`, a volume figure this project
  doesn't score as better/worse either way, from the quality-metric
  count) rather than trusting the original hand count.
- **`risk_reward_tenths` validation only checked `<= 0`, not that the
  value is a genuine positive int.** A real robustness gap: `Decimal(
  "15.5")` would silently corrupt the `target_multiplier` arithmetic, and
  `True`/`False` (bool is an `int` subclass in Python) would silently be
  treated as `risk_reward_tenths=1`/`0`. Fixed in
  `EnsembleMomentumTrainable.fit()` with an explicit `isinstance(...,
  int)` check that excludes `bool`, raising `ValueError` for both cases --
  plus 2 new TDD tests (`test_fit_rejects_non_integer_risk_reward_tenths`,
  `test_fit_rejects_bool_risk_reward_tenths`), written first and confirmed
  failing before the fix.

Full suite after all fixes: **455 passed** (up from 453 before the review
fixes -- the two new bool/non-integer-rejection tests are the only
production-code-touching fix in this pass; the rest are documentation
corrections).

## Judgment calls resolved without asking

- **Neither the risk:reward grid nor the ADX recalibration became this
  strategy's new *default*.** Both stay opt-in (via explicit constructor
  kwargs / `params["candidates"]`), preserving sr-h's original behavior
  byte-for-byte when not explicitly requested -- keeps every existing test
  and sr-h's own historical result reproducible against unmodified
  defaults, and keeps `single_lookback_momentum.py` (which shares
  `regime_weighting.py`'s ADX defaults) completely unaffected, consistent
  with "touch only what the task requires."
- **`risk_reward_tenths` integer-tenths encoding for the risk:reward
  grid**, specifically to let Task G's existing `check_parameter_sensitivity`/
  `perturb_candidate` machinery (built for integer window-length
  candidates) work unmodified, rather than building a parallel
  Decimal-aware sensitivity checker for one new dimension.
- **ADX recalibration thresholds computed from fold 0's own train window
  only** (2,160 bars, the earliest slice of the research dataset), not the
  full pooled research dataset, specifically so the computation is
  leakage-free against every fold's own validate window (fold 0's train
  window chronologically precedes every fold's validate window in the
  whole walk-forward) -- the full-dataset computation was also done and
  closely agrees (14.2% vs 11.3% below 20, 74.1% vs 76.1% above 25), which
  is reassuring but the leakage-free number is what's actually used.
- **The risk:reward grid search was reported and rejected as "doesn't
  help" on its own, rather than silently dropped from the writeup or
  quietly folded into the combined recipe without disclosure.** Matches
  this task's explicit brief ("if a candidate refinement doesn't help when
  actually tested, say so and don't ship it") -- it is disclosed here, and
  is not recommended as a standalone change; it is only part of
  configuration C (combined), whose own real, cross-axis improvement over
  B alone is what justifies including it there specifically.
  A alone is not the change being proposed; C (which includes it) is, an
  important distinction stated plainly.
- **Fold 17's degradation under ADX recalibration was investigated far
  enough to characterize but not far enough to fix.** It's the worst fold
  under B and C (though not under A, where it's actually the best-behaved
  fold at -0.020) -- flagged as a known, disclosed weak point rather than
  smoothed over, but a deeper root-cause investigation (e.g. what
  specifically about `2025-12-18 -> 2026-01-17`'s price action interacts
  badly with a wider ADX ramp) was judged out of scope for this task's
  time budget; a future task could pick this up specifically.
- **No holdout access.** Configuration C is the closest this project has
  come to the Eligibility Bar (4 of 5 criteria), but it still fails
  "positive Sharpe every fold" -- not close enough to justify spending
  either dataset's one-shot holdout confirmation, same reasoning as every
  prior task's identical negative result.
- **`regime_momentum.py`, `regime_momentum_risk_managed.py`,
  `hourly_momentum.py`, `single_lookback_momentum.py` are untouched** --
  this task only modifies `ensemble_momentum.py` and its tests, consistent
  with every prior task's "don't modify already-logged, already-tested
  strategies" precedent (sr-f, sr-h).

## Deliberately out of scope

- **Making the risk:reward grid or the ADX recalibration the new
  constructor default.** Both stay opt-in -- see "Judgment calls" above.
- **Requiring 3-of-3 ensemble conviction.** Ruled out by Step 1's
  diagnosis (only 5/262 real trades are ever 3-of-3) before any code was
  written -- not attempted.
- **Any fee/slippage correction.** BingX's real VIP0 taker rate (0.05%)
  exactly matches this project's existing `fee_bps=5` assumption -- no
  correction warranted or made.
- **Root-causing fold 17's specific degradation under ADX recalibration.**
  Characterized and disclosed, not fixed -- see "Judgment calls" above.
- **Any holdout confirmation run.** Configuration C doesn't clear the
  Eligibility Bar -- see "Eligibility bar evaluation" above.
- **Changing CLAUDE.md's Eligibility Bar thresholds, the 1h holdout
  cutoff, or the 1h walk-forward window defaults.** All three remain
  exactly as previously established/approved; this task evaluates against
  them using the exact same windows as sr-h, for a fair comparison.
- **Modifying `regime_weighting.py`'s shared `DEFAULT_ADX_LOW_THRESHOLD`/
  `DEFAULT_ADX_HIGH_THRESHOLD`.** Would have silently changed
  `single_lookback_momentum.py`'s behavior too, an untested, unrelated
  side effect -- the recalibrated thresholds live as new,
  `ensemble_momentum.py`-local, opt-in constants instead.
