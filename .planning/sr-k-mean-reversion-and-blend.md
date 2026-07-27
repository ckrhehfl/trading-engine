# Strategy Research Task K: regime-gated mean-reversion, a regime-adaptive
blend with the existing momentum ensemble, and a reusable Eligibility Bar
evaluation utility

## Scope note

This project has now tried the momentum/trend-following signal class three
times (naive crossover -> risk-managed -> multi-lookback ensemble with ADX
regime weighting + real volatility targeting, `.planning/sr-h-ensemble-
regime-voltargeting.md`, refined with recalibrated ADX thresholds + an
opt-in risk:reward search, `.planning/sr-i-ensemble-refinement.md`). The
best real result to date -- sr-i's "Configuration C" -- clears 4 of 5
original Eligibility Bar criteria but not "positive Sharpe in every fold"
(11/19). `.planning/sr-j-fold-diagnosis-and-eligibility-review.md` diagnosed
those 8 negative folds (no clean, honestly-justifiable fix found) and
proposed replacing the literal 100%-of-folds clause with a statistically
sounder two-part bar; that proposal was approved and is now live in
CLAUDE.md (revised 2026-07-27).

A deep, credibility-graded research pass early in this project's momentum
work found that blending momentum with mean-reversion produces smoother,
more robust risk-adjusted returns than either alone in multiple independent
studies -- but this project has never built the mean-reversion side of that
finding, or the blend. That is this task, in three parts:

1. `python/research/strategies/mean_reversion.py` -- a regime-gated
   Bollinger-Band mean-reversion strategy ("candidate B").
2. `python/research/strategies/momentum_reversion_blend.py` -- a
   regime-adaptive blend of (1) with the existing momentum ensemble
   (`ensemble_momentum.py`, reused unmodified).
3. `python/research/eligibility.py` -- a reusable utility implementing the
   revised Eligibility Bar's fold-consistency and aggregate-significance
   checks, so future strategy evaluations don't redo this math ad hoc.

Every real number below comes from an actual re-run against real, cached
BingX 1h data (`python/data/var/klines.sqlite3`, 19,678 bars,
2024-04-27T10:00:00Z onward) or is transcribed exactly from sr-j's own
already-published, real per-fold table (Configuration C, reused rather than
re-run -- see "Real walk-forward results" below) -- nothing here is
estimated or hand-derived.

---

## Design 1: `MeanReversionStrategy` -- regime-gated Bollinger Bands

### Why Bollinger Bands, not RSI

Both are standard, defensible mean-reversion signals. Bollinger Bands was
chosen because (1) it produces a **price-level** signal (distance from a
rolling band), the same kind of quantity `risk_management.
compute_stop_and_target` already consumes, so the entry/stop/target
composition stays structurally uniform with every momentum strategy in this
package; (2) its rolling-mean/rolling-stdev shape is structurally identical
to `volatility_targeting.RollingRealizedVolatility`'s already-established
pattern in this codebase (same incremental `update()` contract, same ddof=1
sample-stdev convention, kept for cross-codebase consistency rather than the
more traditional population stdev most Bollinger references use -- a
~2.6% band-width difference at period=20, not a qualitative change). RSI
would have been equally defensible; this is a documented judgment call, not
a claim of objective superiority.

### Inverted regime gating -- the core design point

`ensemble_momentum.py`/`single_lookback_momentum.py` weight positions UP
when ADX is high (trending). Mean-reversion needs the opposite: this
project's own earlier research explicitly found pure mean-reversion fails
badly in trending markets (a real cited backtest: 66% win rate but -16.88%
net loss, because a few large trend-riding losses erased many small
reversion wins). `MeanReversionStrategy` reuses `regime_weighting.
compute_regime_weight` **unmodified** and takes its complement:
`mean_reversion_weight = Decimal(1) - compute_regime_weight(adx, low=
adx_low, high=adx_high)` -- full conviction when ADX is at/below `low`
(ranging), fully suppressed when ADX is at/above `high` (trending).
Everything else (ATR stop/target, fixed-fractional sizing, real volatility
targeting) is composed identically to the momentum strategies, reusing
`risk_management.py`/`volatility_targeting.py` unmodified.

### Edge-triggering's one disclosed consequence

Same "fire only on a signal-state flip" convention every strategy in this
package uses. Because "inside the bands" (no signal) is the *common* case
for Bollinger Bands (unlike an SMA-crossover sign, which is almost never
exactly flat), a strategy's first-ever band breach never fires (no prior
state to differ from) -- only a *later* breach in the opposite direction
does. One real, disclosed consequence: if a position is stopped out while
price is still beyond the same band, the strategy will not immediately
re-enter; it waits for the state to flip away and back. A more eager rule
was considered and rejected in favor of reusing this package's one uniform,
already-tested triggering convention rather than adding a second, bespoke
one.

---

## Design 2: `MomentumReversionBlendStrategy` -- regime-adaptive blend

### The combination formula

```
blended_strength = regime_weight * momentum_sign
                    + (1 - regime_weight) * reversion_sign
```

`momentum_sign`/`reversion_sign` are each `-1`/`0`/`+1` (the same discrete
readings `ensemble_momentum._combined_sign`/`mean_reversion._bollinger_
signal` already produce); `regime_weight` is `compute_regime_weight`'s
existing `[0, 1]` ADX ramp, computed **once** per bar and reused for both
sides (not recomputed independently). This is a convex combination of two
values in `[-1, 1]`, so it's always in `[-1, 1]`; at the extremes it
collapses exactly to one sub-signal (`regime_weight=1` -> pure momentum,
`regime_weight=0` -> pure reversion). `abs(blended_strength)` is used
directly as the position-size conviction weight (the same role
`regime_weight` alone plays in the pure strategies) -- NOT multiplied by
`compute_regime_weight` a second time, since the ADX information is already
fully incorporated into the blend arithmetic.

This directly satisfies the task's brief: use the *same* continuous ADX
weight already computed, not a naive fixed 50/50 split (which this
project's own earlier research explicitly found can underperform either
pure strategy alone) and not a new blending mechanism (the combination is
literally the existing `compute_regime_weight` output applied as a
weight, plus the two already-existing sign functions -- no new indicator or
statistic was invented).

### Rejected alternative: netting two independent sub-positions

A composite design running a full, self-contained `EnsembleMomentumStrategy`
and `MeanReversionStrategy` internally and netting their independently
-tracked positions was considered and rejected: every strategy in this
codebase (and `metrics.position.PositionTracker`, and therefore the whole
Eligibility Bar evaluation) assumes one coherent open/flatten position
lifecycle. Netting two independently-sized sub-positions would require
*partial* position adjustments (increase/decrease an existing position),
a kind of order that exists nowhere else in this codebase and a materially
larger addition than this task's scope warrants. The signal-level blend
above produces one coherent trade stream compatible with the existing
metrics pipeline completely unmodified -- see the module's own docstring
for the full reasoning.

### Reuse, warmup

`ensemble_momentum._combined_sign` and `DEFAULT_LOOKBACK_PAIRS` are imported
directly (the actual momentum-side combination logic reused, not rebuilt,
per the task's explicit instruction); the trivial per-pair SMA-sign loop is
duplicated locally, matching this codebase's own established "duplicate
small shared shapes across strategy modules" convention (every strategy
module already duplicates its own `_sign`/`_data_range`/`_metrics_summary`
rather than sharing them). `mean_reversion.BollingerBands`/`_bollinger_
signal` are reused directly for the reversion side. Both sub-signals must be
simultaneously ready (momentum's longest lookback AND the Bollinger window
full) before any blended signal is computed, extending
`EnsembleMomentumStrategy`'s own "don't let the strategy's character
silently change during warmup" reasoning from 3 pairs within one family to
both signal families.

---

## Design 3: `python/research/eligibility.py`

Implements CLAUDE.md's revised (2026-07-27) Eligibility Bar's two required
checks:

1. **Fold consistency** (`evaluate_fold_consistency`): fraction of folds
   with positive Sharpe against a **required, no-default** `min_fraction`
   keyword argument -- CLAUDE.md deliberately left the 80-90% floor a human
   decision, so this module does not pick a point value on a caller's
   behalf. `None` folds count as not-positive, matching `research.
   walkforward._aggregate_metrics`'s existing convention exactly. The
   pass/fail comparison is done via exact `Decimal` rational arithmetic
   (`num_positive/num_folds >= min_fraction`), never a float round-trip.
2. **Binomial sign test** (`binomial_sign_test_p_value`): the exact
   one-sided upper-tail binomial p-value via stdlib `math.comb`, following
   sr-j's own worked methodology precisely -- verified in this module's own
   test suite by reproducing 8 of sr-j's own published reference numbers
   exactly (e.g. `P(X>=11|n=19,p=0.5)=32.380%`, Configuration C's real
   result; `P(X>=8|n=19,p=0.5)=82.04%`, the original ensemble's; every
   power/reference-rate figure from sr-j's proposed-floor table).
3. **Mean-Sharpe significance** (`evaluate_mean_sharpe_significance`): sr-j
   proposed a plain one-sample t-test but explicitly disclosed an open cost
   -- "the t-test's exact p-value needs either scipy or an accepted
   approximation... not yet resolved". **This task resolves that cost
   rather than deferring it**: the exact (to floating-point precision)
   t-distribution p-value is computed via the regularized incomplete beta
   function (`_regularized_incomplete_beta`), implemented with the standard
   Numerical-Recipes continued-fraction algorithm using only stdlib `math`
   (`math.lgamma`) -- the same numerical relationship `scipy.stats.t.cdf`
   itself is built on, not an approximation. Verified against three
   independent, non-fabricated reference methods that don't exercise the
   same code path: the exact closed-form Cauchy distribution at df=1
   (`P(|T|>=1)=0.5` exactly, `P(|T|>=sqrt(3))=1/3` exactly, both via
   `math.atan`), the standard-normal limit as df -> infinity (via
   `math.erf`, matching to 3 decimal places at df=100,000), and two
   textbook critical-value checks at df=18 (2.101 -> ~5%, 2.878 -> ~1%,
   the first also independently cited in sr-j itself). All pass. This was
   judged the honest option per CLAUDE.md's explicit instruction
   ("implement it if you can do so honestly with stdlib only... or
   explicitly defer it... don't fake a p-value with an unjustified
   shortcut") -- a real, exact implementation was achievable with
   reasonable confidence given these independent verification methods, so
   it was built rather than deferred.

Both checks are evaluated one-sided ("better than chance"), per CLAUDE.md's
explicit wording. `evaluate_eligibility` combines all three (fold
consistency AND sign test AND Sharpe significance, all required) into one
`EligibilityResult`. The module deliberately does **not** evaluate the
other Eligibility Bar criteria (fold-count floor, drawdown ceiling, trade
count, profit-factor floor) -- those remain simple threshold checks a
caller does directly against `WalkForwardResult.aggregate`, matching
`research/walkforward.py`'s own "compute and log the figures, don't
evaluate the bar" scoping.

---

## TDD

- `python/tests/test_mean_reversion.py` (27 tests): `BollingerBands`
  construction/warmup/hand-computed values/zero-variance/look-ahead safety;
  `_bollinger_signal` boundary cases; construction; **3 tests explicitly
  verifying the inverted regime-gating direction** (ADX at the low
  threshold gives mean-reversion FULL conviction where momentum would be
  fully suppressed; ADX at the high threshold fully suppresses
  mean-reversion where momentum would be at full conviction; a mid-ramp ADX
  reading gives mean-reversion the numeric complement of what momentum's
  own formula would compute at the identical reading); ATR stop/target/
  sizing; warmup; `MeanReversionTrainable.fit()`.
- `python/tests/test_momentum_reversion_blend.py` (23 tests): `_blend_
  signals`' combination arithmetic (10 exact cases, including disagreement
  cancelling at an even split, and each extreme collapsing to exactly one
  sub-signal); construction; warmup requiring both sub-signals
  simultaneously; **2 tests directly verifying the regime-adaptive
  property** (high ADX reproduces momentum-alone's exact entry
  quantity/direction; low ADX reproduces mean-reversion-alone's exact entry
  quantity/direction); exit/order shape; `MomentumReversionBlendTrainable.
  fit()`.
- `python/tests/test_eligibility.py` (34 tests): fold consistency
  (including the exact `Decimal` boundary and `None`-handling cases); the
  binomial sign test reproducing 8 of sr-j's own published numbers exactly;
  the incomplete-beta t-distribution engine's 3 independent verification
  methods (Cauchy exact values, normal-limit cross-check, textbook critical
  values); mean-Sharpe significance on a real, transcribed 19-value fixture
  (the original ensemble's actual per-fold Sharpe values from sr-i,
  reproducing sr-j's own reported mean/stdev/t-statistic); the combined
  `evaluate_eligibility`.

One real construction bug caught by the tests during development (not a
production-code bug -- a test-fixture bug): an initial price-path fixture
used a 4-bar Bollinger window, which is algebraically self-normalizing (a
direct derivation shows a single new bar can never breach a `period=4,k=2`
band regardless of magnitude: the breach condition reduces to
`1/period + k/sqrt(period) < 1`, which is `1.25 > 1` at period=4 -- false
for any move size). Fixed by deriving the general condition and switching
the test fixtures to `period=10` (`0.7325 < 1` -- breaches regardless of
magnitude), documented in the test file. One real test-only bug also
caught: an early version of the blend's "pure momentum" regime test ran
every kline through to the end of the price path, which let the position
close via its own target hit before the assertion ran; fixed by breaking
the loop at first entry, matching every sibling test's own convention.

Full suite: **539 passed** (up from 455 before this task -- 84 new tests,
zero regressions in any prior task's tests).

```text
$ cd python && uv run pytest -q
........................................................................ [ 13%]
........................................................................ [ 26%]
........................................................................ [ 40%]
........................................................................ [ 53%]
........................................................................ [ 66%]
........................................................................ [ 80%]
........................................................................ [ 93%]
...................................                                      [100%]
539 passed in 40.66s
```

---

## Real walk-forward results

Identical windows/data/costs to sr-h/sr-i/sr-j for a fair comparison:
`train_bars=2160, validate_bars=720, step_bars=720, bars_per_day=24`,
`fee_bps=5, slippage_bps=2` (BingX's real, verified VIP0 taker rate),
`research.holdout.load_research_klines` against
`configs/research/holdout_1h.json` -> **16,078 bars**
(`2024-04-27T10:00:00Z` -> `2026-02-26T07:00:00Z`), **19 folds**. Both new
strategies use the SAME recalibrated ADX thresholds (`adx_low=25,
adx_high=50`, `ensemble_momentum.RECALIBRATED_ADX_LOW_THRESHOLD`/
`RECALIBRATED_ADX_HIGH_THRESHOLD`) sr-i validated for momentum on this
asset/indicator -- reused rather than left at the traditional 20/25
convention sr-i found doesn't discriminate well for BTC 1h data on this
project's own (non-Wilder) ADX implementation, and required for the blend
anyway since both sides share one ADX reading. **One real, disclosed
difference from momentum's Configuration C**: neither new strategy uses
Task I's opt-in risk:reward grid search (both stay at the fixed 1:2 ratio,
`stop_multiplier=1.5`/`target_multiplier=3.0`) -- a deliberate scope choice
(see "Judgment calls" below), so the comparison is apples-to-apples on ADX
threshold/lookback pairs/costs/windows, but not on that one dimension.

### Mean-reversion alone (`run_id=cdf9ff71-483b-4dbc-ac19-4f795e8b82c0`)

| metric | value |
|---|---|
| mean Sharpe | **-1.859** |
| min Sharpe | -7.178 |
| folds positive | **5/19 (26.3%)** |
| worst-fold max drawdown | 12.38% |
| mean total return | -1.99% |
| total trades | 210 |
| mean profit factor | **1.031** |
| min profit factor | 0.033 |

Per-fold Sharpe: `0.560, -2.925, 1.809, -4.643, 5.163, -2.178, -6.368,
-4.572, -0.982, -7.178, -1.551, -0.921, -4.788, 3.828, -4.973, -4.001,
-1.307, -2.520, 2.227`.

### Momentum/reversion blend (`run_id=7c6e6c22-b6ab-4f96-b6c0-e88daf4fe0ba`)

| metric | value |
|---|---|
| mean Sharpe | **-1.399** |
| min Sharpe | -4.950 |
| folds positive | **7/19 (36.8%)** |
| worst-fold max drawdown | 7.81% |
| mean total return | -1.06% |
| total trades | 450 |
| mean profit factor | 1.072 |
| min profit factor | 0.391 |

Per-fold Sharpe: `1.397, 0.602, -2.988, -2.333, 0.580, -4.950, -1.148,
-3.190, -0.360, 1.306, -3.431, 2.857, -4.883, -1.464, 1.550, -4.816, 0.819,
-4.915, -1.214`.

Trade count (450) is roughly 2x either pure strategy's (210 / 199) -- an
expected mechanism, not a bug: the blend fires whenever `sign(blended_
strength)` flips, and since momentum's own crossover sign is nearly always
nonzero (an SMA-crossover sign is exactly zero only at a measure-zero
instant, the same fact `ensemble_momentum.py`'s own docstring already
establishes), the blend inherits momentum's own flip frequency as its
baseline *plus* additional reversion-driven flips during band breaches.

### Momentum alone -- Configuration C (reused from sr-j, not re-run)

Per this task's own instruction ("reuse it, don't rerun redundantly unless
you have a specific reason to"): sr-j's diagnosis independently reproduced
Configuration C bar-for-bar (`run_id=a7f8185d-3de4-42ec-88c0-7b37ff7a542f`)
and published its full real per-fold Sharpe table. Transcribed exactly
(verified: sums to the reported mean +0.0271, 11 of 19 values are
positive, matching sr-i/sr-j's reported "11/19" exactly):
`4.283, 2.522, 4.685, 2.208, -2.207, -1.999, -6.722, 5.076, 1.308, 0.463,
-7.029, -0.804, -3.989, 3.041, 4.608, 5.374, -2.349, -8.442, 0.489`. Mean
Sharpe +0.027, 11/19 positive, mean profit factor 1.97, worst drawdown
4.2%, 199 total trades (see sr-i/sr-j for the full table).

---

## Eligibility Bar evaluation (revised bar, via `research.eligibility`)

Evaluated at all three candidate floors in CLAUDE.md's approved 80-90%
range (16/19, 17/19, 18/19) -- the result is identical across all three for
every strategy below, since none gets remotely close to any of them.

| Strategy | Fold consistency (floor 80%) | Sign test (p, one-sided) | Sharpe significance (t, p) | Overall |
|---|---|---|---|---|
| Mean-reversion alone | **FAIL** (5/19 = 26.3%) | **FAIL** (p=0.990) | **FAIL** (t=-2.384, p=0.986) | **FAIL** |
| Momentum/reversion blend | **FAIL** (7/19 = 36.8%) | **FAIL** (p=0.916) | **FAIL** (t=-2.408, p=0.987) | **FAIL** |
| Momentum alone (Config C) | **FAIL** (11/19 = 57.9%) | **FAIL** (p=0.324) | **FAIL** (t=0.027, p=0.489) | **FAIL** |

**None of the three clears the revised bar.** Momentum alone (Configuration
C) remains, by a wide margin, this project's strongest real result on every
metric that matters (mean Sharpe, fold-positive percentage, sign-test
p-value, profit factor, drawdown) -- its sign-test p-value (0.324) is at
least in a plausible range for a strategy that might clear the bar with a
few more strong folds; both new strategies' sign-test p-values (0.990 and
0.916) are on the *wrong side of the coin flip entirely* -- **worse than
random**, not merely "not yet significant". This matters for how to read
the result: Configuration C's failure is "not enough evidence yet, real
directional promise"; mean-reversion's and the blend's failures are "the
evidence points the wrong way".

## Three-way comparison and the honest verdict

**The blend helps relative to mean-reversion alone, on every metric
measured** (mean Sharpe -1.399 vs -1.859; folds positive 7/19 vs 5/19; mean
profit factor 1.072 vs 1.031; worst drawdown 7.81% vs 12.38%) -- consistent
with the design's intent that mixing in trend-following exposure should
smooth out mean-reversion's weakest periods.

**The blend does NOT help relative to momentum alone.** Every single
metric is worse: mean Sharpe -1.399 vs momentum's +0.027; folds positive
7/19 (36.8%) vs 11/19 (57.9%); mean profit factor 1.072 vs 1.967; worst
drawdown 7.81% vs 4.23%; sign-test p-value 0.916 (worse than a coin flip)
vs 0.324. **This project's earlier research finding -- that blending
momentum with mean-reversion produces smoother, more robust risk-adjusted
returns than either alone -- is not reproduced by this real implementation
on this real data.** Reported plainly, per this task's explicit
instruction, rather than reframed toward a better-looking conclusion: on
this asset, this indicator implementation, and this specific mean-reversion
signal, mean-reversion is simply a much weaker signal than momentum
(mean profit factor barely above 1, worst drawdown nearly 3x momentum's),
and blending it in -- even with regime-adaptive, not naive-fixed,
weighting -- dilutes momentum's real (if not-yet-statistically-significant)
edge rather than complementing it. The most plausible reason, consistent
with the per-fold data: momentum's positive folds and mean-reversion's
positive folds do not reliably line up with the *same* regime periods on
this specific 19-fold sample, so the blend's ADX-driven handoff between the
two signals doesn't consistently allocate weight to whichever one is
actually working at that moment -- a real, evidenced limitation of this
specific implementation, not a claim that regime-adaptive blending as a
general technique is unsound.

**Momentum alone (Configuration C) remains this project's best real
result, and the only one of the three with a directionally plausible
(if not yet significant) case for a genuine edge.** Neither new strategy
built in this task changes that. This is the honest outcome of the last
major unexplored thread from this project's original momentum/
mean-reversion research finding: it did not pan out as hoped when actually
built and tested end-to-end on real data, and that is reported here as
plainly as the alternative would have been.

---

## Judgment calls resolved without asking

- **Neither `MeanReversionTrainable` nor `MomentumReversionBlendTrainable`
  includes `ensemble_momentum.py`'s Task I opt-in risk:reward grid search.**
  Both are brand-new, not-yet-evaluated signal families; adding a second
  tunable dimension before either had been evaluated once would add search
  surface (and MinBTL-style risk) ahead of any evidence it's warranted.
  Real, disclosed cost: the comparison against Configuration C is not
  apples-to-apples on this one dimension (Config C's numbers include a
  grid-searched risk:reward ratio; both new strategies use the fixed
  original 1:2 ratio) -- flagged plainly above rather than silently
  glossed over. Given how far both new strategies are from clearing the
  bar or even approaching Configuration C's numbers, it is very unlikely
  this one dimension would have closed that gap, but it is not proven not
  to matter.
- **Both new strategies use the recalibrated ADX thresholds (25/50), not
  the traditional 20/25 default**, for the real evaluation runs (though
  20/25 remains each module's own constructor *default*, matching every
  other strategy's "recalibrated constants are opt-in, never default"
  convention). Chosen for a fair comparison against Configuration C and
  because the blend structurally needs one shared ADX reading for both
  sides regardless.
- **`min_fold_consistency` was evaluated at all three candidate floors
  (80/85/90%)** rather than picking one -- the result is identical across
  all three here (every strategy is far below even the loosest floor), so
  this didn't change any conclusion, but it's the honest way to apply a
  utility that deliberately doesn't hardcode a point value within
  CLAUDE.md's approved range.
- **Momentum alone (Configuration C) was NOT re-run** -- its real per-fold
  Sharpe values were transcribed exactly from sr-j's own already-published
  diagnosis table (itself an independently-verified, bar-for-bar
  reproduction of sr-i's original Configuration C run) and cross-checked
  against the two aggregate facts sr-i/sr-j already reported (11/19
  positive, mean Sharpe +0.027) before use -- both checks passed exactly.
  Re-running would have been a redundant `run_walk_forward` call against an
  unchanged, deterministic configuration, adding avoidable weight to
  `strategy_id="ensemble-momentum"`'s own MinBTL-style combination count
  for no new information (sr-j's own "Process notes" section explicitly
  flags this as a real, avoidable cost from its own diagnosis task).
- **No holdout access was made for either new strategy.** Neither clears
  even the loosest candidate fold-consistency floor, let alone the
  aggregate-significance checks -- not remotely close enough to justify
  spending either dataset's one-shot holdout confirmation, same reasoning
  as every prior negative result in this project.
- **The throwaway real-run script (`python/_task_k_realrun.py`) was
  written, run once, its output transcribed into this document, then
  removed** -- same "written once, run once, results transcribed, then
  deleted, never committed" convention every prior real-data task in this
  project has used (sr-h, sr-i, sr-j).

## Deliberately out of scope

- **Tuning either new strategy's parameters to try to close the gap to
  Configuration C.** This task's brief was to build and honestly evaluate
  mean-reversion and the blend, not to iterate toward a better-looking
  number -- explicitly warned against by the task itself ("No tuning or
  cherry-picking toward a better-looking result").
- **A different mean-reversion signal (e.g. RSI) or a different blend
  formula.** Documented, defensible choices were made for both (see Design
  1/2 above); trying alternatives is a legitimate future task, not this
  one, especially given the real result already shows a clear, honestly-
  reported negative outcome that doesn't obviously call for a different
  formula so much as a fundamentally stronger reversion signal.
- **Investigating *why* momentum's and mean-reversion's positive folds
  don't line up on this data** (the plausible mechanism named in the
  three-way comparison above). Named as the most likely explanation, not
  investigated further -- a future task's concrete next step if this
  thread is revisited.
- **Modifying `ensemble_momentum.py`, `regime_weighting.py`,
  `risk_management.py`, or `volatility_targeting.py`.** All reused
  unmodified, per this task's explicit brief and this project's established
  "don't touch already-tested strategy/shared-infrastructure code" 
  precedent.
- **Implementing the Probabilistic Sharpe Ratio / Deflated Sharpe Ratio
  upgrade sr-j named as the eventual, more statistically correct successor
  to a plain t-test.** The plain t-test (now with an exact, not
  approximated, p-value) is what CLAUDE.md's revised bar actually
  specifies; the PSR/DSR upgrade remains a named-but-deferred future
  improvement, same treatment sr-g gave full CSCV/PBO and sr-j gave this
  same PSR/DSR item.
