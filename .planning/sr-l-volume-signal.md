# Strategy Research Task L: does volume discriminate Configuration C's
winners from losers, and a standalone On-Balance-Volume strategy

## Scope note

This project has now tried price-only technical signals five times across
Tasks E-K (naive momentum, risk-managed momentum, multi-lookback ensemble
+ ADX regime weighting + vol targeting, ADX-recalibration + risk:reward
refinement -- `.planning/sr-i-ensemble-refinement.md`'s "Configuration C",
this project's best real result to date -- and regime-gated mean-reversion
+ a momentum/reversion blend, `.planning/sr-k-mean-reversion-and-blend.md`,
neither of which improved on Configuration C). `.planning/sr-j-fold-
diagnosis-and-eligibility-review.md` diagnosed Configuration C's 8
remaining negative folds and found the real discriminator between winning
and losing trades was **trade-level win rate itself** (25.6% in negative
folds vs 45.4% in positive folds) -- notably **not** whole-fold
choppiness and **not** ADX (36.7 vs 37.8, essentially identical) -- but
found no cause and proposed no fix, naming that gap explicitly as
unresolved. The human explicitly asked to try a genuinely different
signal type: volume, which `backtest/kline.py`'s `Kline.volume` field has
carried since this project's very first data-pipeline task but which no
strategy has ever used. This task investigates that gap from a new angle,
per the human's explicit instruction to diagnose first and let the
diagnosis (not a preference) decide what gets built.

Every real number below comes from an actual re-run against real, cached
BingX 1h data (`python/data/var/klines.sqlite3`, `configs/research/
holdout_1h.json`, 16,078 research bars, `2024-04-27T10:00:00Z` ->
`2026-02-26T07:00:00Z`, identical to sr-h/sr-i/sr-j/sr-k) -- nothing here
is estimated or hand-derived.

---

## Step 1: does volume discriminate Configuration C's own winning trades
from its losing trades?

### Methodology

Reproduced Configuration C exactly (`EnsembleMomentumTrainable` with
`adx_low=RECALIBRATED_ADX_LOW_THRESHOLD` (25), `adx_high=
RECALIBRATED_ADX_HIGH_THRESHOLD` (50), `fee_bps=5`, `slippage_bps=2`,
`params={"candidates": DEFAULT_RISK_REWARD_TENTHS_CANDIDATES}`
(`((15,), (20,), (25,), (30,))`), `train_bars=2160, validate_bars=720,
step_bars=720, bars_per_day=24`), via a real `run_walk_forward` call
against `research.holdout.load_research_klines`'s real data. **Real
re-run result matches sr-i's/sr-j's own published Configuration C
numbers exactly** (mean Sharpe +0.0271, min Sharpe -8.4418, 11/19 folds
positive, worst drawdown 4.2329%, mean total return +0.2132%, 199 total
trades, mean profit factor 1.9674, min profit factor 0.1282), confirming
this is a valid, apples-to-apples reproduction, not a different config.

For every real closed trade (`metrics.position.reconstruct_trades`
applied to each fold's real `(filled_intents, fills)`, the same technique
sr-i/sr-j already used -- **193 real trades reconstructed**, 6 fewer than
the 199 `num_trades` the aggregate reports, exactly matching sr-i's own
already-documented explanation: `num_trades` additionally counts
still-open positions force-closed at a fold boundary, which
`reconstruct_trades` alone does not, since it only walks realized
`(intent, fill)` pairs), the **signal bar** (the bar immediately
preceding the fill -- `backtest/fill.py`'s `simulate_fill` always fills at
`next_bar.open_time`, so the bar that actually produced the entry
decision is the one right before `trade.entry_time`) was located inside
that fold's own `validate_klines`, and its volume was compared to a
trailing rolling average computed **only from bars strictly before the
signal bar** (look-ahead safe by construction: never reads the signal bar
itself or anything later) -- `volume_ratio = signal_bar.volume /
mean(volume over the N bars immediately before it)`, `N=20` as the
primary window (matching this codebase's other 20-bar rolling
conventions -- `mean_reversion.DEFAULT_BOLLINGER_PERIOD`, `volatility_
targeting.DEFAULT_VOL_LOOKBACK_PERIOD`), with `N=10`/`N=30` computed too
as a robustness check on the window choice itself. All 193 trades had
enough in-fold trailing history to compute this at every window size
tested (no trades excluded).

### Results (real, all five statistical tests independently computed)

68 winners, 125 losers (weighted win rate 35.2%, consistent with
Configuration C's own already-reported near-breakeven trade-level
economics).

| test | statistic | result |
|---|---|---|
| Welch's two-sample t-test, `volume_ratio` winners vs losers (N=20) | mean winners=1.4272, mean losers=1.2423, t=0.9010, df=116.30 | **p=0.3694** -- not significant |
| Two-proportion z-test, win rate above- vs below-rolling-average | above: n=82, win_rate=39.02%; below: n=111, win_rate=32.43% | z=0.9477, **p=0.3433** -- not significant |
| Two-proportion z-test, win rate volume-spike (>=1.5x avg) vs no-spike | spike: n=52, win_rate=44.23%; no-spike: n=141, win_rate=31.91% | z=1.5891, **p=0.1120** -- not significant (closest of the five, still short of the conventional 5% threshold) |
| Point-biserial correlation, `is_winner` vs `volume_ratio` | r=0.069, t=0.9558, df=191 | **p=0.3404** -- negligible, not significant |
| Welch's t-test, robustness across window choice | N=10: t=1.2547, p=0.2125; N=20: t=0.9010, p=0.3694; N=30: t=0.9185, p=0.3605 | **not significant at any window size** -- the "no discrimination" conclusion does not depend on the arbitrary 20-bar choice |

The exact two-sample/correlation p-values were computed via the same
regularized-incomplete-beta Student's-t implementation
`research/eligibility.py` already implements and this project already
trusts (stdlib `math` only, no `scipy`) -- reused directly, not
reimplemented, for a two-sample Welch's t-test and a correlation
significance test instead of `eligibility.py`'s own one-sample use case.
The two-proportion z-tests use the standard pooled-variance formula and
stdlib `math.erf` for the normal CDF.

**A further, honest complication**: breaking the primary comparison down
by trade side shows an **inconsistent direction**, not a uniform
mechanism -- LONG winners had a *lower* mean volume ratio than LONG
losers (1.103 vs 1.238, the *opposite* of what a "volume confirms
winners" story would predict), while SHORT winners had a *higher* mean
ratio than SHORT losers (1.751 vs 1.247, consistent with that story). A
real effect would be expected to point the same direction on both sides
of the book; this one doesn't, which further weakens the case for a real,
exploitable signal even before considering that none of the five tests
above cleared conventional significance in the first place.

### Verdict

**No real, robust discriminating power.** All five independent
statistical tests come back non-significant at the conventional 5%
threshold; this holds across three different rolling-window choices
(10/20/30 bars), so the conclusion isn't an artifact of one arbitrary
parameter choice; and the one test that came closest to significance
(the volume-spike win-rate comparison, p=0.112) is (a) still short of
0.05, (b) one of five separate tests run in this diagnosis -- a real
multiple-comparisons exposure that would require a considerably smaller
p-value to survive a Bonferroni-style correction, not just "closest of
the five" -- and (c) not corroborated by a consistent direction when
split by trade side. Per this task's own explicit branching instruction,
this result does **not** justify building a volume-confirmation filter on
top of Configuration C's existing signal -- that path would not be
honestly grounded in what this diagnosis actually found. **Path taken:
build a standalone, independently-evaluated volume-based strategy
instead.**

---

## Step 2: `ObvTrendStrategy` -- a standalone On-Balance-Volume strategy

### Why On-Balance Volume, not price/volume divergence or a
volume-confirmed breakout

All three are legitimate, long-established technical-analysis concepts;
this is a documented judgment call, not a claim of objective superiority.
On-Balance Volume (Joseph Granville, 1963) was chosen because:

1. It is a genuinely **different kind** of volume signal than what Step
   1 already tested and found non-discriminating on this data. Step 1
   tested raw entry-bar volume *level* relative to a trailing average
   (and a volume-"spike" threshold on that same level) -- both a static,
   single-bar comparison. OBV is a **cumulative, path-dependent running
   total** of signed volume (directional volume flow accumulated over
   time), structurally distinct from a single-bar level comparison. A
   volume-confirmed-breakout design would have re-tested essentially the
   same "is this bar's volume elevated" question Step 1 already answered
   negatively, just gating a different base signal (a Donchian-style
   breakout) instead of `ensemble_momentum.py`'s SMA crossover -- not a
   meaningfully different question given what was already found.
   Price/volume divergence is conceptually interesting but requires
   detecting local price extrema and comparing them against OBV's own
   local extrema -- a materially more complex detection algorithm with
   more implementation-risk surface for a first, exploratory pass at this
   signal family.
2. OBV admits the exact same "value vs. its own trailing rolling mean,
   sign-based, edge-triggered" structural shape this codebase already
   uses for price (`single_lookback_momentum.py`'s fast/slow SMA
   crossover) and for Bollinger Bands (`mean_reversion.py`'s close vs.
   its own rolling mean +/- k*stdev) -- so this strategy's composition
   with the shared regime-weighting/risk-management/volatility-targeting
   infrastructure is a direct, low-risk reuse of an already-tested
   pattern, not a new composition shape needing its own from-scratch
   design.
3. It is a genuinely standard, decades-old technique (not a novel
   invention with unknown backtesting properties), consistent with every
   other indicator choice in this package (ATR, ADX, Bollinger Bands are
   all similarly conventional, not novel).

### Design

`OnBalanceVolume` -- an incremental, look-ahead-safe calculator
(`python/research/strategies/obv_trend.py`), fed one `Kline` per call via
`update()`: the standard Granville definition, a single cumulative
running total (`obv[t] = obv[t-1] +/- volume[t]` depending on the sign of
`close[t] - close[t-1]`, unchanged if flat). Returns `None` for the very
first bar ever fed (no prior close to compare against -- same "no
evidence yet" convention as `risk_management.AverageTrueRange`/
`regime_weighting.AverageDirectionalIndex`'s own first-bar warmup), a
real cumulative `Decimal` from the second bar onward.

`_obv_trend_signal(obv, obv_sma) -> int`: `+1` if `obv` is strictly above
its own trailing `obv_ma_period`-bar rolling mean (volume flow confirms
an up-move), `-1` if strictly below, `0` if exactly equal -- the same
strict-inequality sign convention as every other signal helper in this
package. `DEFAULT_OBV_MA_PERIOD = 20`, matching this package's other
20-bar rolling conventions, not searched/tuned to this asset.

**Non-inverted regime gating -- the one deliberate contrast with
`mean_reversion.py`**: unlike Bollinger-Band mean-reversion (which
inverts `regime_weighting.compute_regime_weight` because reversion needs
a *ranging* market), OBV trend-following is directionally the same kind
of signal as the momentum strategies -- it wants a market that is
actually moving, not chopping sideways. `ObvTrendStrategy` therefore uses
`compute_regime_weight` **unmodified**, the same (non-inverted) direction
`ensemble_momentum.py`/`single_lookback_momentum.py` use: full conviction
at/above the high ADX threshold, near-zero at/below the low one.

**Reused, unmodified, from sibling modules**: `regime_weighting.
AverageDirectionalIndex`/`compute_regime_weight` (not inverted, as
above); `risk_management.AverageTrueRange`, `OpenPosition`,
`compute_stop_and_target`, `compute_position_size`, `check_exit_trigger`
-- the identical ATR-based stop/target/sizing every strategy in this
package uses; `volatility_targeting.RollingRealizedVolatility`,
`compute_vol_scalar` -- the identical real volatility-targeting scalar
every strategy in this package uses. `final_quantity =
atr_sized_base_quantity * regime_weight * vol_scalar`, the same
composition every sibling module uses.

**Edge-triggering, including the `entry_rejected_by_filters` fix from day
one**: same "fire only when the signal state changes" convention as
every strategy in this package. `mean_reversion.py`/`momentum_reversion_
blend.py` each needed a real functional-correctness fix during Strategy
Research Task K's CodeRabbit review because an attempted-but-
filter-rejected entry was silently consuming the edge-trigger state,
permanently missing a signal that never actually traded. This module
builds that fix in from the start (identical mechanism) rather than
repeating the same review round-trip on brand-new code.
`EnsembleMomentumStrategy`/`SingleLookbackMomentumStrategy` still lack
this fix (already-shipped, out of this task's scope, same disclosed
inconsistency `mean_reversion.py`'s own docstring already names).

`ObvTrendTrainable` (`python/research/strategies/obv_trend.py`):
deliberately does **not** include any grid search -- every constant
(OBV-MA period, ATR/ADX/vol periods, ADX thresholds, stop/target
multipliers, vol-target level/bounds, risk fraction) is fixed at
construction time, matching `MeanReversionTrainable`'s identical judgment
call from Task K: this is a brand-new, not-yet-evaluated signal family,
so adding a tunable dimension before it has even been evaluated once
would add search surface (and MinBTL-style overfitting risk) ahead of
any evidence it's warranted.

---

## TDD

`python/tests/test_obv_trend.py` (28 tests), written first: this file
existed and failed on `ModuleNotFoundError` before `research/strategies/
obv_trend.py` did. Coverage:

- **`OnBalanceVolume`** (6 tests): first-bar warmup returns `None`;
  close-above/-below/-equal-to-prior-close accumulation; a 5-bar
  hand-computed cumulative sequence (up/down/flat/flat/up) checked
  against the exact expected running total; **look-ahead safety**
  (the value at bar `k` is identical whether or not future bars beyond
  `k` are ever fed -- the same explicit look-ahead-safety test pattern
  `BollingerBands`'s own test suite uses).
- **`_obv_trend_signal`** (3 tests): above/below/exactly-equal-to its own
  SMA.
- **Construction** (5 tests): starts flat; stop/target multiplier and
  `obv_ma_period` exposed as properties; default ADX thresholds match
  `regime_weighting`'s own defaults (asserted via `inspect.signature`,
  not merely "construction doesn't raise" -- the same precision fix
  Task K's own CodeRabbit review already required for the equivalent
  `mean_reversion.py` test); `DEFAULT_OBV_MA_PERIOD == 20`.
- **Non-inverted regime gating** (3 tests): ADX at/below the low
  threshold fully suppresses an entry (a real OBV crossover fires
  internally but `final_quantity` is suppressed to zero); ADX at/above
  the high threshold gives full (1.0) conviction; a genuine mid-ramp ADX
  reading (21.25) gives exactly `compute_regime_weight`'s own 0.25
  reading, used directly (**not** complemented, the deliberate contrast
  with `mean_reversion.py`'s `1 - weight` inversion).
- **Risk management** (3 tests): ATR-scaled stop/target with the 1:2
  risk:reward ratio; a stop-hit emits a correctly-sized flattening
  intent; entry intents carry `signal_timeframe="1h"`/
  `OrderType.GUARDED_MARKET`/`limit_price=None`.
- **Warmup** (2 tests): no signal until the OBV-SMA window is full; no
  signal on the very first bar regardless of `obv_ma_period`.
- **Edge-triggering** (1 test): a filter-rejected entry (ADX below the
  low threshold, suppressing every entry) does **not** consume the
  signal state -- proven by then relaxing ADX on a fresh bar with the
  *same*, never-consumed bullish state and confirming the entry still
  fires.
- **`ObvTrendTrainable`/`fit()`** (5 tests): logs exactly one candidate;
  logs the fixed `obv_ma_period` in `params`; returns a fresh strategy
  bound to that fixed period; never reads klines beyond `train_klines`
  (a monkeypatched `run_backtest` spy asserting every call sees the exact
  same `train_klines` object); the default `obv_ma_period` is used when
  a `Trainable` is constructed without an override.

One real test-construction bug caught (not a production-code bug) during
this task's own TDD process: the first draft of the ADX-gating tests ran
the *entire* `_obv_flip_klines()` sequence (a monotonic decline then a
long monotonic rally, deliberately long enough to establish a clean OBV
crossover) before asserting `open_position is not None` -- but the rally
leg continues well past the flip bar, long enough that the position
opened at the flip would hit its own ATR target and close again before
the assertion ran, the same class of test-construction pitfall
`.planning/sr-k-mean-reversion-and-blend.md` documented for its own
"pure momentum regime" blend test. Fixed by breaking at the first fired
entry, matching `TestRiskManagement`'s own established convention in this
same file (and `mean_reversion.py`'s/`ensemble_momentum.py`'s sibling
test suites).

Full suite: **568 passed** (up from 540 immediately before this task --
28 new tests, zero regressions in any prior task's tests):

```text
$ cd python && uv run pytest -q
........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 38%]
........................................................................ [ 50%]
........................................................................ [ 63%]
........................................................................ [ 76%]
........................................................................ [ 88%]
................................................................ [100%]
568 passed in 41.22s
```

---

## Real walk-forward result

Identical windows/data/costs to sr-h/sr-i/sr-j/sr-k for a fair comparison:
`train_bars=2160, validate_bars=720, step_bars=720, bars_per_day=24`,
`fee_bps=5, slippage_bps=2` (BingX's real, verified VIP0 taker rate),
`research.holdout.load_research_klines` against `configs/research/
holdout_1h.json` -> 16,078 bars, 19 folds. Same recalibrated ADX
thresholds as sr-k used for mean-reversion/the blend (`adx_low=25,
adx_high=50`, `ensemble_momentum.RECALIBRATED_ADX_LOW_THRESHOLD`/
`RECALIBRATED_ADX_HIGH_THRESHOLD`) for a fair, apples-to-apples
comparison; `DEFAULT_OBV_MA_PERIOD=20`, no grid search, matching
`ObvTrendTrainable`'s single-fixed-candidate design above.

### `ObvTrendStrategy` alone (`run_id=a61cadd6-1537-466b-a74b-d0c51683afcf`)

| metric | value |
|---|---|
| mean Sharpe | **-2.8548** |
| min Sharpe | -8.7726 |
| folds positive | **4/19 (21.1%)** |
| worst-fold max drawdown | 10.80% |
| mean total return | -2.24% |
| total trades | 435 |
| mean profit factor | **0.9544** |
| min profit factor | 0.1059 |
| weighted trade-level win rate | 33.1% (right at the 1:2 risk:reward's ~33.3% breakeven line) |

Per-fold Sharpe: `4.612, -3.517, -3.274, 1.646, -6.489, -5.297, 4.936,
-2.681, -0.933, -8.177, -4.677, -5.231, -2.217, -7.849, 4.530, -1.641,
-4.195, -8.773, -5.014`.

Per-fold trade counts/win rates/profit factors were also inspected
individually (18-29 trades/fold, win rates ranging 10.5%-51.9%, profit
factors ranging 0.106-3.231) -- a healthy spread with no single fold
driving the whole result and no suspicious pattern (e.g. a permanently
inverted signal would show ~0% or ~100% win rates everywhere; it doesn't),
consistent with a real, if weak/whipsaw-prone, signal rather than an
implementation bug.

**A further honest observation, beyond what CLAUDE.md's Eligibility Bar
itself checks**: the one-sample t-test on the 19 fold Sharpe values gives
`t=-2.9497` at `df=18` -- large enough in magnitude that the **two-sided**
p-value (`2 * (1 - one_sided_p) = 2 * (1 - 0.9957) ~= 0.0086`) is itself
below the conventional 5% threshold. This means the mean fold Sharpe is
not merely "not distinguishable from zero" (the failure mode every other
strategy in this project has shown so far) -- it is **statistically
distinguishable from zero in the *negative* direction**: real evidence
that this specific configuration underperforms a no-edge baseline, not
just an absence of evidence for an edge. `research.eligibility`'s own
one-sided "better than chance" test correctly reports this as failed
(`p=0.9957`, nowhere near `<0.05`) -- the two-sided observation above is
additional, honest context beyond what the Eligibility Bar's own
mechanics report, not a different or contradictory result.

### Eligibility Bar evaluation (revised bar, via `research.eligibility`)

Evaluated at all three candidate floors in CLAUDE.md's approved 80-90%
range -- identical result across all three, since the strategy is nowhere
close to any of them.

| Criterion | Requirement | Result |
|---|---|---|
| Fold count | >=8-10 | 19 -- PASS |
| Fold consistency | >=80-90% positive | 4/19 = 21.1% -- **FAIL** |
| Sign test (one-sided, p<0.05) | reject "no edge" | p=0.9979 -- **FAIL** (far on the wrong side of chance) |
| Mean-Sharpe significance (one-sided, p<0.05) | reject "no edge" | p=0.9957 -- **FAIL** (far on the wrong side of chance; see the two-sided observation above) |
| Max drawdown ceiling | <=20-25% | 10.80% -- PASS (on this criterion alone) |
| Minimum total trades | >=100 | 435 -- PASS |
| Profit factor floor | 1.3-1.5 (mean) | 0.9544 -- **FAIL** (below 1.0 -- aggregate gross losses exceed gross wins) |
| **Overall** | all required checks pass | **FAIL** |

### Comparison against every prior real result in this project

| Strategy | mean Sharpe | folds positive | mean profit factor | worst drawdown | sign-test p | Eligibility Bar |
|---|---|---|---|---|---|---|
| Momentum alone (Configuration C, sr-i/sr-j) | **+0.027** | 11/19 (57.9%) | **1.967** | **4.23%** | 0.324 (closest to significant) | FAIL (3/3 required checks fail, but directionally plausible) |
| Momentum/reversion blend (sr-k) | -1.399 | 7/19 (36.8%) | 1.072 | 7.81% | 0.916 (wrong side of chance) | FAIL |
| Mean-reversion alone (sr-k) | -1.770 | 6/19 (31.6%) | 1.048 | 11.69% | 0.968 (wrong side of chance) | FAIL |
| **OBV-trend alone (this task)** | **-2.855** | **4/19 (21.1%)** | **0.954** | 10.80% | **0.998 (worst of any strategy tried)** | FAIL |

**OBV-trend is the weakest real result in this project's history, worse
than every prior strategy on every metric that matters** (mean Sharpe,
fold-positive fraction, profit factor, sign-test p-value) except worst
drawdown, where it sits between the blend and mean-reversion alone (still
well short of momentum alone's 4.23%). Momentum alone (Configuration C)
remains, by a wide margin, this project's strongest and only directionally
plausible real result.

---

## Overall honest conclusion

**Volume, investigated two different ways in this task, has not moved
this project closer to a validated strategy.** Step 1 found no real,
robust evidence that volume discriminates Configuration C's own winning
trades from its losing trades (five independent tests, all
non-significant, robust across three window choices, and inconsistent in
direction by trade side) -- so `sr-j`'s still-unexplained gap (why some
trend-following entries follow through and others don't) remains
unexplained; volume is not the answer, at least not in either of the two
forms tested here (raw level-vs-average, or a spike threshold on that
same level). Step 2's standalone OBV-based strategy, built and evaluated
honestly on real data with real statistical tests rather than assumed to
work, performed **worse than every prior strategy in this project**,
including the mean-reversion signal that itself already badly
underperformed momentum in sr-k. This is reported here exactly as
plainly as a positive result would have been, per this project's
standing "no tuning or cherry-picking, report the honest answer" rule.

Momentum alone (Configuration C) remains this project's best real
result and the only one with a directionally plausible (if not yet
statistically significant) case for a genuine edge. Neither this task's
volume-discrimination diagnosis nor its standalone OBV strategy changes
that. Per CLAUDE.md's Strategy Research Methodology, funding-rate signals
are the next thing queued to try if further signal-type exploration
continues.

---

## A real, disclosed process cost

Step 1's diagnostic script was developed incrementally (an initial pass,
then a second pass adding the volume-spike proportion test and the
rolling-window robustness check), and `run_walk_forward` was re-run once
more against the identical, deterministic Configuration C reproduction as
a result -- the exact same avoidable inefficiency `.planning/sr-j-fold-
diagnosis-and-eligibility-review.md`'s own "Process notes" section
already flagged and recommended against repeating ("consolidate into one
instrumented `run_walk_forward` call... rather than several incremental
re-runs of an unchanged, deterministic configuration"). Both runs were
byte-identical (confirmed by matching aggregate numbers exactly), so no
conclusion changed, but `research.overfitting_check.check_combination_count
("ensemble-momentum")`, re-run after this task, now reports
`total_combinations_tried=234` (up from sr-j's own already-elevated 224 --
this task's 2 extra real reproduction runs, each carrying the opt-in
4-candidate risk:reward grid, contribute exactly 8 of that increase),
`risk_level` remaining `"high"` (already high before this task). Disclosed
here plainly, same as sr-j's own equivalent disclosure, and named again as
a concrete lesson this project has now failed to fully apply twice.
`obv-trend` (a brand-new `strategy_id`) is unaffected by this and sits at
`total_combinations_tried=2`, `risk_level="low"` -- one real walk-forward
run plus its own single-candidate `fit()` log entry, nothing more.

Both throwaway diagnostic scripts (`python/_task_l_diagnosis.py`,
`python/_task_l_realrun.py`) were written, run, their output transcribed
into this document, then deleted -- same "written once, run once, results
transcribed, then deleted, never committed" convention every prior
real-data task in this project has used (sr-h, sr-i, sr-j, sr-k).

## Judgment calls resolved without asking

- **The rolling-window period for Step 1's volume-ratio diagnostic
  (primary N=20)** was chosen to match this codebase's other 20-bar
  rolling conventions (Bollinger period, vol-targeting lookback), then
  explicitly re-tested at N=10/N=30 as a robustness check rather than
  presenting only the primary window's result -- the "no discrimination"
  conclusion is unchanged at every window size tested.
- **The signal bar (not the fill bar) is what Step 1's volume comparison
  is computed against.** `trade.entry_time` (from `metrics.position.
  reconstruct_trades`) equals the *fill's* time, which `backtest/fill.py`
  always sets to the bar *after* the strategy's actual decision bar. The
  economically meaningful question ("was volume elevated when the
  strategy actually decided to enter") is about the decision bar, so
  Step 1 looks one bar earlier than `trade.entry_time` -- documented
  explicitly in the diagnostic script and repeated here so a future
  reader doesn't have to re-derive it.
- **`ObvTrendTrainable` has no grid search of any kind**, not even an
  OBV-MA-period search -- matching `MeanReversionTrainable`'s identical
  Task K judgment call for a brand-new, not-yet-evaluated signal family:
  adding tunable dimensions before a first honest evaluation would add
  search surface ahead of any evidence it's warranted. Real, disclosed
  cost: it is possible a different `obv_ma_period` performs better on
  this data, but searching for one now -- before any evidence the OBV
  concept itself has edge -- would be exactly the kind of blind tuning
  this project's Strategy Research Methodology exists to avoid, and this
  task's own real result (worse than every prior strategy on every
  quality metric, not a close miss) gives no indication a parameter
  search would plausibly close the gap to Configuration C.
- **The `entry_rejected_by_filters` edge-trigger fix was built into
  `obv_trend.py` from the start**, rather than shipped without it and
  fixed in a later CodeRabbit review round (as happened for
  `mean_reversion.py`/`momentum_reversion_blend.py` in Task K) -- a
  direct, deliberate application of a lesson this project already paid
  the cost of learning once.
- **No holdout access was made.** The standalone OBV strategy is the
  weakest result in this project's history, not remotely close to the
  Eligibility Bar -- no legitimate reason to spend either dataset's
  one-shot holdout confirmation, same reasoning as every prior negative
  result in this project.
- **`ensemble_momentum.py`, `mean_reversion.py`, `momentum_reversion_
  blend.py`, `regime_weighting.py`, `risk_management.py`, `volatility_
  targeting.py`, `eligibility.py` are all untouched.** Every real number
  from Configuration C, mean-reversion, and the blend cited in this
  document's comparison table is transcribed from sr-i/sr-j/sr-k's own
  already-published results (Configuration C) or this task's own fresh,
  independently-run reproduction (Step 1) -- consistent with this
  project's "don't touch already-tested strategy/shared-infrastructure
  code" precedent and "reuse an already-published result rather than
  redundantly re-running an unchanged, deterministic configuration"
  precedent (sr-k's own explicit choice for Configuration C).

## Deliberately out of scope

- **A volume-confirmation filter on top of Configuration C.** Ruled out
  by Step 1's diagnosis before any filter code was written -- not
  attempted, per this task's own explicit branching instruction.
- **Price/volume divergence or volume-confirmed breakout as alternative
  standalone designs.** Documented, defensible reasoning for choosing OBV
  instead (see "Why On-Balance Volume" above); trying either alternative
  is a legitimate future task, not this one, especially given this task's
  own real result already shows a clear, honestly-reported negative
  outcome for the volume signal family generally on this data, not one
  that obviously calls for a different specific formula.
- **Any grid search over `obv_ma_period` or any other `ObvTrendStrategy`
  constant.** See "Judgment calls" above.
- **Investigating *why* OBV-trend performs this poorly** (e.g. whether
  the OBV-vs-its-own-SMA construction is simply too noisy/whipsaw-prone
  on 1h BTC data). Named as a plausible mechanism, not investigated
  further -- a future task's concrete next step if this signal family is
  revisited, though given how far short of every prior result this task's
  real number falls, revisiting this specific construction is not
  obviously the most promising use of a future task's time compared to
  the funding-rate signal CLAUDE.md already queues as the next thing to
  try.
- **Any holdout confirmation run.** Neither Step 1's diagnosis nor Step
  2's strategy comes remotely close to justifying one -- see "Judgment
  calls" above.
- **Changing CLAUDE.md's Eligibility Bar, the 1h holdout cutoff, or the
  1h walk-forward window defaults.** All three remain exactly as
  previously established/approved; this task evaluates against them
  using the exact same windows as every prior task, for a fair
  comparison.
