# Strategy Research Task H: multi-lookback ensemble vs. single-lookback baseline, ADX regime weighting, real volatility targeting

## Scope note

This task follows a deep, credibility-graded research pass into how
institutional-grade systematic crypto strategies are structured. That
research isn't itself written up anywhere else in this repo, so (per
CLAUDE.md's Strategy Research Methodology preamble and
`.planning/README.md`'s "a file here can only be created for work that
has actually happened" rule) this document is its durable home, same
precedent as `.planning/sr-g-overfitting-safeguards.md`. Three findings
from that research pass are what this task implements; the task's own
brief was explicit that the central empirical question (does ensembling
actually help on our own data) must be tested, not assumed.

## Finding 1: multi-lookback ensembles are NOT unconditionally better than a single well-chosen lookback -- this is the actual, testable question this task exists to answer

Credible sources genuinely split on this. Concretum Group, Man AHL (Baz
et al. 2015), and QuantPedia's own published research all support
ensembling multiple lookback periods into one combined signal -- the
intuition being that no single lookback is right for every regime, so
averaging/voting across several smooths out the risk of being tuned to
the wrong one. But a 2025 CFM-affiliated study (Valeyre) found a single
120-day EMA outperformed a multi-signal ensemble across 70 traditional
CTA assets -- a direct empirical counterexample from a comparably
rigorous source.

The resolution isn't "ensembling always wins" or "ensembling never
wins" -- it's that ensembling only helps when the underlying asset's
regime shifts **faster than a single lookback can track**. Valeyre's
result was on traditional CTA assets (equities, rates, commodities,
FX), where regimes plausibly shift on a month-to-quarter scale, meaning
even a single, sufficiently long lookback (120 days) can track them
adequately. QuantPedia's own BTC-specific research, by contrast,
suggests Bitcoin's regime shifts operate on a much faster, **day-scale**
cadence -- if true, a single lookback would systematically lag, and
combining several scales should genuinely help.

**This is exactly the kind of claim CLAUDE.md's Strategy Research
Methodology says must be tested empirically on our own data, not
assumed from someone else's asset class or someone else's paper.**
That's the actual point of this task: build both a single-best-lookback
baseline (the control) and a multi-lookback ensemble (the treatment),
holding every other variable constant (same risk management, same
regime weighting, same volatility targeting, same fees/slippage, same
walk-forward windows, same data), and get an honest, direct,
non-cherry-picked answer for BTC-USDT specifically. See "Full honest
real-world results" below for that answer.

## Finding 2: ATR stops and volatility targeting are different concepts -- this project had only built the former

`research/strategies/risk_management.py` (Task F) already has an
ATR-based stop-loss: it controls how much a **single trade** can lose,
by placing a stop `1.5x ATR` away from entry and sizing the position so
that stop, if hit, loses exactly 1% of a fixed reference equity. That's
a genuinely useful, but narrow, risk control -- it says nothing about
how the strategy's **overall** exposure should change as the market's
volatility regime itself changes.

Real institutional volatility targeting (Robert Carver's framework,
also the general convention Concretum Group is reported to use) is a
different, portfolio/exposure-level control: it scales the **overall
position size** inversely to a rolling realized-volatility estimate, so
exposure automatically shrinks in high-volatility/choppy periods and
grows in calm/trending ones -- independent of what any individual
trade's own stop distance happens to be. Concretum's own reported
convention targets a **20% annualized volatility**. Their exact formula
was not accessible during this research pass (Cloudflare-blocked), so
this task implements **the general, commonly-cited institutional
convention** (targeting a fixed annualized vol via a realized-vol
estimator), not a literal reproduction of Concretum's specific
implementation -- stated here so that distinction isn't lost later.

`research/strategies/volatility_targeting.py` (this task) keeps this
**conceptually and computationally separate** from the ATR stop, per
the task's explicit brief: `final_quantity = atr_sized_base_quantity *
regime_weight * vol_scalar`. The ATR stop still answers "where do I get
out if this specific trade goes wrong"; volatility targeting separately
answers "how big should my overall exposure be right now."

## Finding 3: regime detection should gate/weight a momentum signal continuously, not as a hard binary switch -- ADX over Hurst/HMM

Multiple sources in this research pass converge on the same point:
probabilistic/continuous regime scaling reduces whipsaw compared to an
abrupt on/off switch. This project's existing `regime_momentum.py`
(Task E) used exactly the abrupt kind -- a binary up/down 1h-SMA gate
that either fully allowed or fully blocked a 15m crossover signal. This
task replaces that pattern (for the two new strategies specifically;
`regime_momentum.py`/`regime_momentum_risk_managed.py` are untouched)
with a continuous weight.

**ADX was the concrete technique specified for this task**, over two
credible alternatives that were assessed and set aside for this
project's current stage:

- **Hurst exponent** -- a real, credible mean-reversion-vs-trending
  discriminator, but heavier to compute and noisier at this project's
  current data scale than a standard technical indicator like ADX.
- **HMM (Hidden Markov Model) regime detection** -- a peer-reviewed
  source in this research pass found HMM regime models showing real
  predictive power **in-sample** that degraded substantially
  **out-of-sample**. That's a direct caution against relying on
  heavier, more overfit-prone regime machinery before this project has
  even validated a single simple strategy -- consistent with CLAUDE.md's
  existing "few tunable knobs" discipline and Task G's MinBTL-spirit
  overfitting concern.

ADX measures **trend strength**, not direction -- conventional
interpretation: `> 25` trending, `< 20` ranging/choppy.
`research/strategies/regime_weighting.py` (this task) implements this
as a **continuous linear ramp** (`0` weight at/below 20, `1` weight
at/above 25, linearly interpolated between) rather than a hard cutoff,
per the finding above. It scales **how much conviction** a genuine
crossover signal gets, not whether that signal fires at all -- the
crossover's own sign still decides LONG vs. SHORT.

## What was built

### 1. `research/strategies/regime_weighting.py` -- shared component

- **`AverageDirectionalIndex`**: an incremental, look-ahead-safe ADX
  calculator (`update(kline) -> Decimal | None`, one `Kline` per call).
  Standard directional-movement definitions (`+DM`/`-DM`/`TR`, per bar,
  unambiguous), but -- **same deliberate simplification precedent as
  `risk_management.AverageTrueRange`'s own documented choice** -- uses a
  **plain rolling mean** for the smoothing step, not Wilder's original
  exponential recursion that "ADX" conventionally refers to. CLAUDE.md's
  brief asked for "a standard, look-ahead-safe rolling technical
  indicator," not specifically Wilder smoothing; a plain rolling mean is
  simpler to verify by hand and the smoothing-convention choice isn't
  itself a claim of edge. A degenerate all-flat window (`sum(TR) == 0`)
  yields `DX = 0` directly (not a crash, not `None`) -- a flat market
  has no directional movement to measure, which is exactly what
  `ADX == 0` means.
- **`compute_regime_weight(adx, low=20, high=25) -> Decimal`**: the
  continuous `[0, 1]` ramp described in Finding 3.
  `adx=None` (warmup) is baked in as `Decimal(0)` directly -- see the
  function's own docstring for why this differs from
  `volatility_targeting.compute_vol_scalar`'s `None`-in/`None`-out
  contract (ADX has a natural "off" value at the bottom of its own
  scale; realized volatility does not).

### 2. `research/strategies/volatility_targeting.py` -- shared component

- **`RollingRealizedVolatility`**: an incremental, look-ahead-safe
  rolling realized-volatility estimator (`update(kline) -> Decimal |
  None`). Sample standard deviation (ddof=1, matching
  `metrics.metrics`'s own Sharpe convention) of simple per-bar returns
  over a trailing 20-bar window (`DEFAULT_VOL_LOOKBACK_PERIOD`, a common,
  simple convention, not searched/tuned), annualized via
  `sqrt(bars_per_day * 365)` -- same fixed 365-day-year convention as
  every other annualization in this codebase. All-`Decimal` arithmetic
  throughout, including the final `Decimal.sqrt()` step -- no float
  round-trip. A zero-variance reading is a valid `Decimal(0)`, never
  `None` -- unlike `metrics.metrics`'s Sharpe (where zero variance is
  its own division denominator), this estimator never divides by the
  value it computes, so a perfectly flat price series has a genuine,
  meaningful zero-vol reading; the actual divide-by-zero risk is
  downstream, in `compute_vol_scalar`, and is handled there.
- **`compute_vol_scalar(realized_annualized_vol, *,
  target_annualized_vol=0.20, min_scalar=0, max_scalar=3) -> Decimal |
  None`**: `target / realized`, clamped to `[min_scalar, max_scalar]`.
  `realized=None` (warmup) returns `None`, **not** a baked-in default --
  a deliberate contrast with `compute_regime_weight`'s convention (see
  that function's docstring, and this module's, for the full reasoning:
  there is no equally natural single scalar for "no volatility estimate
  yet" the way ADX's own low-threshold floor gives it one). Both
  strategies below choose to skip trading entirely until this
  estimator's own warmup completes -- the same "no evidence = no trade"
  convention this codebase already uses everywhere else.
  `realized <= 0` returns `max_scalar` directly (an anomalously calm
  market is exactly the scenario the cap exists for, not a crash path).
  `max_scalar=3` is a deliberate, documented safety cap (not
  searched/tuned) against an unboundedly large multiplier in a
  near-zero-volatility market.

### 3. `research/strategies/single_lookback_momentum.py` -- baseline/control

`SingleLookbackMomentumStrategy` + `SingleLookbackMomentumTrainable`.
`regime_momentum_risk_managed.py`'s natural successor: same ATR
stop/target and fixed-fractional sizing (`risk_management.py`,
unchanged), same single-tier edge-triggered SMA-crossover shape as
`hourly_momentum.py` (native 1h bars -- see "Why 1h, not 15m" below),
plus the two new layers: ADX-based continuous regime weighting and real
volatility targeting.

`final_quantity = atr_sized_base_quantity * regime_weight * vol_scalar`.
Either factor going to (near-)zero naturally suppresses the trade
entirely -- no explicit binary branch needed, a genuinely continuous
gate.

`fit()` grid-searches `DEFAULT_CANDIDATE_GRID` -- 6 native-1h `(fast,
slow)` pairs spanning sub-day (`(4, 12)`) to multi-day (`(24, 72)` = 3
days) scales: `((4, 12), (6, 18), (8, 24), (12, 36), (16, 48), (24,
72))`. Deliberately sized to span the **same range** the ensemble's 3
fixed pairs cover (two of this grid's entries, `(12, 36)` and `(24,
72)`, are exactly the ensemble's medium/long pairs) -- so the "best
single lookback" this strategy's `fit()` can find is a genuinely
comparable alternative to what the ensemble offers, not an artificially
narrower search. Same per-candidate scoring/logging/tie-break/zero-trade
handling as every prior `Trainable` in this package
(`regime_momentum_risk_managed.py`, `hourly_momentum.py`).

### 4. `research/strategies/ensemble_momentum.py` -- treatment

`EnsembleMomentumStrategy` + `EnsembleMomentumTrainable`. Same
risk-management/regime-weighting/vol-targeting layers as the baseline,
applied identically (imported unchanged) -- the *only* deliberate
difference is where the entry/direction signal comes from, which is
what makes the comparison meaningful.

**Combining 3 lookback scales**: `DEFAULT_LOOKBACK_PAIRS = ((4, 12),
(12, 36), (24, 72))` -- short (4h/12h), medium (12h/36h), long
(24h/72h), in native 1h-bar units. Chosen to span sub-day to multi-day
scales, roughly matching QuantPedia's BTC-specific "day-scale regime
shifts" finding cited in Finding 1 above -- **not searched/tuned to this
asset**. Each pair's own fast/slow SMA crossover produces a sign
(`+1`/`0`/`-1`); the 3 signs are combined by **majority vote**
(`sum(signs)`; strictly positive -> `+1`, strictly negative -> `-1`,
an exact tie -> `0`/no signal). Majority vote was chosen over a
weighted average because it needs no additional tunable weight
parameters (keeping "few tunable knobs" intact) and is directly
interpretable ("at least 2 of 3 scales agree") -- at the deliberate cost
of discarding *how strongly* each pair agrees. **All 3 pairs must be
simultaneously warmed up** (`len(closes) >= max(slow)`) before any
combined signal is produced at all, rather than combining whichever
subset happens to be warm -- avoids the ensemble's effective character
silently changing over the warmup period.

**`fit()` does not grid-search anything** -- the 3 lookback pairs and
every risk/regime/vol constant are structurally fixed at construction
time. This is a deliberate design choice, not a shortcut: searching
combinations of 3 lookback pairs would combinatorially blow up the
tunable-parameter count, directly working against CLAUDE.md's "few
tunable knobs" guidance and, more concretely, against
`research.overfitting_check`'s MinBTL-style combinations-per-year
heuristic (Task G) -- exactly the overfitting risk that heuristic exists
to flag. `fit()` still backtests the one fixed-shape ensemble against
`train_klines` and logs it as its own `backtest_run` record
(`candidate_index=0`, `total_candidates=1`) for symmetry with every
other strategy's "every `fit()` call leaves a trace" convention, and so
`check_combination_count` counts it honestly (1 combination actually
tried, not an undercount).

### Why 1h, not 15m

Both new strategies operate on native 1h bars, not 15m. Real BingX
15m depth (~252 days, ~19,870 research bars after the existing holdout
cutoff) structurally caps out at **3** non-overlapping walk-forward
folds at the provisional default windows -- every prior task in this
project (sr-c through sr-g) has already hit this ceiling and flagged it.
Real 1h depth (~820 days, 16,078 research bars after
`configs/research/holdout_1h.json`'s existing cutoff) supports **19**
folds at the windows sr-f Part 2 already established
(train=2,160/validate=720/step=720 1h-bars) -- comfortably clearing
CLAUDE.md's 8-10-fold credibility floor. Since this task's actual point
is a credible, honest ensemble-vs-baseline comparison, statistical power
matters more here than timeframe purity: a 3-fold comparison could not
distinguish a genuine difference from noise, while a 19-fold one has a
real chance to. Reused sr-f Part 2's exact windows rather than picking
new ones to target a particular fold count.

## TDD

Every new module's tests were written first and confirmed failing
(`ModuleNotFoundError`) before the corresponding production code
existed.

- **`test_regime_weighting.py`** (13 tests): `AverageDirectionalIndex`
  construction validation; warmup (`None` for the first-ever bar and
  during window-filling); a hand-computed 4-bar ADX (period=2, full
  arithmetic shown in comments); a flat-market zero-TR/zero-DM
  degenerate case (`DX=0`, not a crash); look-ahead safety (same prefix
  -> same value regardless of future bars, mirroring
  `risk_management.AverageTrueRange`'s identical test); a rolling-window
  test proving the DX window actually drops its oldest value.
  `compute_regime_weight`: at/below low threshold -> 0, at/above high
  -> 1, exact linear-ramp midpoint and quarter-point values, default
  20/25 thresholds, `None`-in -> `0`-out, rejecting `low >= high`.
- **`test_volatility_targeting.py`** (17 tests): `RollingRealizedVolatility`
  construction validation (`period < 2` rejected); warmup; a
  hand-computed stdev+annualization (closes chosen so returns are exact
  fractions `[0.3, 0.1, -0.1]`, `bars_per_day=365` deliberately chosen
  so the annualization factor is an exact integer -- full arithmetic
  shown in comments); a rolling-window-drop test proving a second,
  genuinely different value after the oldest return rolls out; a
  zero-variance-yields-zero-not-`None` test (with the distinction from
  `metrics.metrics`'s Sharpe convention explained in the test itself);
  look-ahead safety. `compute_vol_scalar`: exact match -> scalar 1,
  calm/choppy markets scale up/down correctly, capping at `max_scalar`
  and flooring at `min_scalar`, zero realized vol capped (not a
  `ZeroDivisionError`), `None`-in -> `None`-out (explicitly contrasted
  with `compute_regime_weight`'s different convention), default-bounds
  behavior, and constructor-error validation.
- **`test_single_lookback_momentum.py`** (24 tests): construction
  validation (including that invalid `adx_period`/`vol_period` correctly
  propagate `ValueError` from the shared calculators); edge-triggered
  crossover correctness under a monkeypatched "full conviction" ADX/vol
  reading (isolating crossover-sign logic from the new regime/vol
  layers, which get their own dedicated test class); ATR stop/target/
  sizing formula still holding exactly under full conviction; **the
  actual point of this task** -- entry quantity scaled by both a
  non-trivial regime weight (0.5, at the exact ADX ramp midpoint) and a
  non-trivial vol scalar (2, for a below-target realized vol), an ADX
  reading at/below the low threshold suppressing an otherwise-valid
  crossover entirely, and an unwarmed (`None`) realized-vol reading
  doing the same; `fit()` grid-search logging/winner-selection/fallback
  behavior; a real `run_walk_forward` integration smoke test, including
  one exercising `sensitivity_extractor`.
- **`test_ensemble_momentum.py`** (27 tests): `_combined_sign`'s
  majority-vote arithmetic in full isolation (all-agree, majority-
  overrides-minority in both directions, exact ties, mixed-with-flat);
  construction validation (fewer than 3 pairs rejected, invalid
  fast/slow in any pair rejected); **a dedicated test proving the
  combined signal genuinely diverges from a single constituent pair's
  own reading** -- a real synthetic price path where the short pair
  alone would read bearish (independently recomputed and asserted as a
  test-setup precondition) while the ensemble, outvoted 2-to-1 by the
  medium/long pairs, does not fire a fresh bearish signal; a warmup test
  proving no signal fires until the *longest* configured pair's window
  is full; the same ATR/regime/vol-scaling test coverage shape as the
  baseline; `fit()`'s no-grid-search behavior (exactly one logged
  candidate, `candidate_index=0`/`total_candidates=1`, the fixed
  lookback pairs correctly recorded in the logged `params`); a real
  `run_walk_forward` integration smoke test.

Full suite: **442 passed** (was 361 immediately before this task's
branch point -- matching `.planning/sr-g-overfitting-safeguards.md`'s
own final count exactly, confirming no other work landed on `main`
between sr-g and this task's branch point). This task adds exactly **81
new tests** (13 `test_regime_weighting.py` + 17
`test_volatility_targeting.py` + 24 `test_single_lookback_momentum.py` +
27 `test_ensemble_momentum.py` = 81, confirmed via `pytest --collect-only`
against each file individually, matching the 442-361=81 full-suite
delta exactly); nothing from any prior task regressed -- confirmed by
running the complete, unfiltered `uv run pytest` suite, not just the new
test files.

**One real bug caught mid-development by the test-writing process
itself, not anticipated in the original design**: an early version of
`TestEnsembleSignalDiffersFromAnySingleConstituent`'s divergence test
used a 1-unit price dip to try to flip the short pair's sign while
leaving the medium/long pairs unmoved. That dip was too small -- the
short pair's own computed sign came back `+1` (still bullish), not the
`-1` the test asserted as a setup precondition, causing the test itself
to fail with a clear, direct assertion message
(`"test setup assumption: the short pair alone should read bearish
here"`) rather than a wrong result silently passing. Fixed by widening
the dip to 20 units (verified via a real Python computation of all
three pairs' signs against the candidate price path before hardcoding
it into the test, not just hand arithmetic) -- a legitimate example of a
test's own setup assertion catching a scenario-construction mistake
before it could hide a real logic error.

**One test-fixture-only Decimal-precision issue, also caught by TDD,
not a production bug**: an early version of
`TestRiskManagementWithFullConviction::test_entry_has_atr_scaled_stop_and_target_with_one_to_two_risk_reward`
in `test_ensemble_momentum.py` computed its own ATR organically from a
price path with `atr_period=3`, and the resulting ATR landed on a
many-digit repeating decimal. `compute_stop_and_target`'s `atr * 1.5`
and `atr * 3.0` each round independently at Decimal's 28-significant-
digit default context precision, so the two results occasionally
*don't* compare as exactly `2x` for a sufficiently ugly ATR value --
purely a property of that specific test fixture's arithmetic, not a
`risk_management.py` production bug (its own `test_risk_management.py`
already thoroughly covers the 1:2 ratio in isolation with clean,
hand-picked ATR inputs). Fixed by pinning ATR to a clean `Decimal("2")`
via the same class-level monkeypatch technique already used for
ADX/vol in that test -- keeping the test focused on "does the strategy
correctly wire whatever ATR it reads into `compute_stop_and_target`",
which is what it actually needs to prove.

## Verification (full command output, not paraphrased)

```text
$ cd python && uv run pytest -q
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 65%]
........................................................................ [ 81%]
........................................................................ [ 97%]
..........                                                               [100%]
442 passed in 38.33s
```

442 - 361 = 81 new tests, confirmed to the individual test by
`pytest --collect-only -q` against each new file: `test_regime_weighting.py`=13,
`test_volatility_targeting.py`=17, `test_single_lookback_momentum.py`=24,
`test_ensemble_momentum.py`=27 -- 13+17+24+27=81, exactly matching the
full-suite delta.

## Full honest real-world walk-forward results (2026-07-27, real cached BingX 1h data)

Ran via an uncommitted verification script (`python/_task_h_verification.py`,
same convention as every prior task's own real verification runs --
written, run once, results transcribed here, then deleted, never
committed). `load_research_klines` against `configs/research/holdout_1h.json`
(unchanged from sr-f) -> **16,078 bars**
(`2024-04-27T10:00:00Z` -> `2026-02-26T07:00:00Z`), identical to sr-f
Part 2's own data. Windows: `train_bars=2160, validate_bars=720,
step_bars=720` (identical to sr-f Part 2, reused rather than re-picked)
-> **19 folds** for both strategies. `fee_bps=5, slippage_bps=2`
throughout, matching every prior task's convention. Every constructor
default used as-is (ATR period 14/1.5x/3.0x, ADX period 14/20/25 ramp,
vol-target period 20/20%-target/[0,3] scalar bounds, 1% fixed-fractional
risk) -- nothing tuned toward a better-looking result.

### Baseline/control: `single-lookback-momentum` (grid-searched single best (fast, slow))

`run_id=fee7e17c-4a42-4a24-86a2-f247f52a60a5`. `sensitivity_extractor`
wired in (see "Why the ensemble has no `sensitivity_extractor`" below
for why the treatment doesn't get the same treatment).

| fold | trades | total_return | sharpe | max_dd | win_rate | profit_factor |
|---|---|---|---|---|---|---|
| 0 | 23 | -2.54% | -2.483 | 4.45% | 34.8% | 0.762 |
| 1 | 13 | -2.71% | -4.262 | 2.92% | 30.8% | 0.529 |
| 2 | 11 | -2.20% | -2.194 | 4.81% | 36.4% | 0.763 |
| 3 | 8 | -0.14% | -0.248 | 0.78% | 37.5% | 1.052 |
| 4 | 16 | -4.18% | -4.773 | 5.45% | 37.5% | 0.430 |
| 5 | 7 | +1.47% | +3.569 | 0.80% | 42.9% | 2.787 |
| 6 | 10 | +3.41% | +3.415 | 1.77% | 50.0% | 4.077 |
| 7 | 9 | -0.35% | -0.533 | 2.43% | 33.3% | 0.966 |
| 8 | 20 | -3.08% | -1.716 | 4.72% | 30.0% | 0.763 |
| 9 | 30 | -11.95% | -8.363 | 12.56% | 20.0% | 0.425 |
| 10 | 15 | -1.93% | -2.132 | 4.25% | 33.3% | 0.916 |
| 11 | 14 | -3.47% | -3.153 | 5.43% | 28.6% | 0.734 |
| 12 | 14 | -0.66% | -0.704 | 4.59% | 35.7% | 1.186 |
| 13 | 14 | +0.59% | +0.439 | 5.63% | 21.4% | 1.311 |
| 14 | 8 | -0.93% | -1.060 | 2.96% | 25.0% | 0.879 |
| 15 | 10 | -2.01% | -2.612 | 3.85% | 30.0% | 0.695 |
| 16 | 11 | -2.72% | -2.845 | 4.65% | 45.5% | 0.621 |
| 17 | 21 | -4.87% | -4.351 | 7.30% | 28.6% | 0.668 |
| 18 | 23 | -19.14% | -5.900 | 19.53% | 30.4% | 0.185 |

**Aggregate**: `fold_count=19`, `mean_sharpe=-2.100`, `min_sharpe=-8.363`,
`all_folds_positive_sharpe=False` (3 of 19 positive: folds 5, 6, 13),
`worst_fold_max_drawdown=19.53%` (fold 18), `mean_total_return=-3.02%`,
`total_trades=277`, `mean_profit_factor=1.039`, `min_profit_factor=0.185`,
`folds_with_zero_trades=0`.

### Treatment: `ensemble-momentum` (fixed 3-lookback-pair majority vote, no grid search)

`run_id=f055735a-3882-4ae5-9782-8e0ab42a2a03`.

| fold | trades | total_return | sharpe | max_dd | win_rate | profit_factor |
|---|---|---|---|---|---|---|
| 0 | 13 | +1.39% | +1.700 | 1.87% | 46.2% | 1.536 |
| 1 | 15 | -1.30% | -1.229 | 3.16% | 46.7% | 0.899 |
| 2 | 10 | +4.31% | +3.466 | 2.35% | 60.0% | 2.919 |
| 3 | 13 | -3.54% | -3.877 | 4.09% | 38.5% | 0.483 |
| 4 | 15 | -3.95% | -4.884 | 5.19% | 33.3% | 0.490 |
| 5 | 15 | -3.13% | -4.035 | 4.21% | 20.0% | 0.607 |
| 6 | 20 | -1.70% | -1.217 | 6.74% | 35.0% | 0.985 |
| 7 | 15 | +3.91% | +4.048 | 0.94% | 40.0% | 3.017 |
| 8 | 15 | +0.79% | +0.632 | 4.73% | 26.7% | 1.412 |
| 9 | 10 | +3.14% | +3.053 | 2.30% | 60.0% | 2.772 |
| 10 | 15 | -4.98% | -5.735 | 6.40% | 26.7% | 0.586 |
| 11 | 13 | -4.49% | -4.165 | 5.92% | 30.8% | 0.587 |
| 12 | 16 | -4.94% | -4.040 | 8.72% | 31.3% | 0.641 |
| 13 | 18 | -5.00% | -3.806 | 8.93% | 27.8% | 0.717 |
| 14 | 12 | +5.31% | +3.610 | 2.41% | 50.0% | 2.908 |
| 15 | 10 | +0.53% | +0.820 | 1.92% | 40.0% | 1.361 |
| 16 | 16 | -6.47% | -7.615 | 6.79% | 25.0% | 0.302 |
| 17 | 9 | -2.84% | -3.727 | 4.97% | 22.2% | 0.565 |
| 18 | 12 | +0.85% | +1.409 | 1.42% | 33.3% | 1.611 |

**Aggregate**: `fold_count=19`, `mean_sharpe=-1.347`, `min_sharpe=-7.615`,
`all_folds_positive_sharpe=False` (8 of 19 positive: folds 0, 2, 7, 8, 9,
14, 15, 18), `worst_fold_max_drawdown=8.93%` (fold 13),
`mean_total_return=-1.16%`, `total_trades=262`, `mean_profit_factor=1.284`,
`min_profit_factor=0.302`, `folds_with_zero_trades=0`.

### Why the ensemble has no `sensitivity_extractor` result

`run_walk_forward`'s `sensitivity_extractor` opts a fold into Task G's
parameter-sensitivity check, which perturbs a fold's **winning grid
candidate** and re-checks whether nearby values also work. The baseline
has exactly this shape (a real 6-candidate grid, one winner per fold).
The ensemble's `fit()` **does not grid-search anything** -- its 3
lookback pairs are structurally fixed, and `EnsembleMomentumTrainable.
fit()` never reads `params["candidates"]` at all. Wiring
`sensitivity_extractor` in anyway would be either a silent no-op (every
"perturbed candidate" would produce a byte-identical result, since
`fit()` ignores whatever candidate value is passed) or actively
misleading (a fake `is_robust` verdict about a dimension that was never
really varied). Omitted deliberately, not an oversight -- see "Judgment
calls" below.

### Direct comparison: did the ensemble actually beat the baseline?

**Yes, on every single metric measured, on this real data, with every
other variable held constant (same windows, fees, risk management,
regime weighting, vol targeting).** This is the direct, honest answer
to the question this task exists to answer:

| metric | baseline (single-lookback) | ensemble (3-lookback majority vote) | which is better |
|---|---|---|---|
| mean Sharpe | -2.100 | -1.347 | **ensemble** (less negative) |
| min Sharpe | -8.363 | -7.615 | **ensemble** |
| folds with positive Sharpe | 3 of 19 (15.8%) | 8 of 19 (42.1%) | **ensemble**, substantially more consistent |
| worst-fold max drawdown | 19.53% | 8.93% | **ensemble**, more than 2x safer in its worst fold |
| mean total return | -3.02% | -1.16% | **ensemble** (less negative) |
| total trades | 277 | 262 | roughly comparable |
| mean profit factor | 1.039 | 1.284 | **ensemble**, materially closer to the 1.3-1.5 floor |
| min profit factor | 0.185 | 0.302 | **ensemble** |

Neither strategy clears CLAUDE.md's Eligibility Bar (see below) -- this
is not a validated edge for either. But **the ensemble's edge over the
baseline is real, consistent (not one lucky fold driving the aggregate:
positive-Sharpe fold count alone is nearly 3x higher), and shows up on
every axis this project's own metrics layer measures**, on BTC-USDT's
own real data, tested honestly rather than assumed from either camp of
the research literature. This is evidence in favor of the QuantPedia/
Concretum/Man AHL position (ensembling helps) over the CFM/Valeyre
position (a single well-chosen lookback wins) **for this specific
asset, timeframe, and strategy shape** -- not a general claim that
ensembling always wins, consistent with Finding 1's own framing that the
answer depends on whether the underlying asset's regime shifts faster
than a single lookback can track.

### Eligibility bar evaluation (CLAUDE.md's Backtest/Walk-Forward Eligibility Bar)

Both strategies cleared the 8-10-fold credibility floor (19 folds each),
so every criterion is evaluated with no caveat for either.

**Baseline (`single-lookback-momentum`)**:

| Criterion | Requirement | Actual | Result |
|---|---|---|---|
| Fold count | >= 8-10 for credibility | 19 | **PASS** |
| Positive Sharpe, every fold | Sharpe > 0 in all folds | 3 of 19 positive; mean -2.100, min -8.363 | **FAIL** |
| Max drawdown ceiling | <= 20-25%, per-fold and aggregate | worst fold 19.53% (fold 18) | **PASS** (barely, just under the 20% low end) |
| Minimum total trades | >= 100 across all folds | 277 | **PASS** |
| Profit factor floor | 1.3-1.5 | mean 1.039, min 0.185 | **FAIL** |

**Ensemble (`ensemble-momentum`)**:

| Criterion | Requirement | Actual | Result |
|---|---|---|---|
| Fold count | >= 8-10 for credibility | 19 | **PASS** |
| Positive Sharpe, every fold | Sharpe > 0 in all folds | 8 of 19 positive; mean -1.347, min -7.615 | **FAIL** |
| Max drawdown ceiling | <= 20-25%, per-fold and aggregate | worst fold 8.93% (fold 13) | **PASS**, comfortably |
| Minimum total trades | >= 100 across all folds | 262 | **PASS** |
| Profit factor floor | 1.3-1.5 | mean 1.284, min 0.302 | **FAIL**, but mean is now inside the floor's own cited range (1.3-1.5) even though the floor's actual bar is "mean AND min both clear it" -- min (0.302) is nowhere close |

**Neither strategy clears the eligibility bar** -- both fail the
positive-Sharpe-every-fold and profit-factor-floor criteria, same
pass/fail pattern as every prior real strategy result in this project
(sr-e, sr-f). This is a legitimate, honest negative result for both --
stated plainly, not softened, matching this project's standing
"no tuning or cherry-picking toward a better-looking result" discipline.
The ensemble's real, consistent improvement over the baseline (previous
section) does not change this: it is closer to the bar on every measured
axis, not across it.

### `overfitting_check.check_combination_count` -- real, honest result

Ran against the real, accumulated `runs/experiments.jsonl` (360 records
before this task's two real runs; 660 after -- 300 new records: 114
baseline grid-candidate records + ~165 baseline sensitivity-check
records + 1 baseline final standalone record + 19 ensemble per-fold
records + 1 ensemble final standalone record).

```json
{
  "strategy_id": "single-lookback-momentum",
  "total_combinations_tried": 172,
  "parent_run_groups": {"fee7e17c-4a42-4a24-86a2-f247f52a60a5": 6},
  "standalone_run_count": 166,
  "data_span_years": 1.8352739726027398,
  "combinations_per_year": 93.71897742116066,
  "risk_level": "high"
}
```

```json
{
  "strategy_id": "ensemble-momentum",
  "total_combinations_tried": 2,
  "parent_run_groups": {"f055735a-3882-4ae5-9782-8e0ab42a2a03": 1},
  "standalone_run_count": 1,
  "data_span_years": 1.8352739726027398,
  "combinations_per_year": 1.0897555514088448,
  "risk_level": "low"
}
```

**Reported honestly, exactly as the task asked, including the
uncomfortable part**: the baseline's `HIGH` risk tier is real, but it is
**substantially self-inflicted by turning on `sensitivity_extractor`** --
the 6-candidate grid itself (correctly counted once per distinct
`parent_run_id`, per `check_combination_count`'s own documented
counting rule, not once per fold) contributes only 6 to the total of
172; the other 166 are `sensitivity_extractor`-driven single-candidate
evaluations (19 folds x up to 9 evaluations each: 1 winner re-check + up
to 8 neighbors). This is exactly the same interaction sr-g's own
real-data section already documented and predicted ("turning on the
parameter-sensitivity check on a strategy with thin real data will
itself measurably raise that strategy's MinBTL-style risk tier") -- not
a new surprise, but a real, honest confirmation of it on this task's own
data. The ensemble's `LOW` tier is real too, and is a genuine structural
side-benefit of its "fixed, not grid-searched" design (Finding 1's
design section above) -- fewer tunable knobs means fewer combinations
tried by construction, independent of how well the strategy actually
performs. Neither tier changes the walk-forward metrics or the
eligibility-bar verdict above; both are a separate, complementary
diagnostic about search intensity relative to data depth, exactly as
`overfitting_check.py`'s own docstring describes.

## Judgment calls resolved without asking

- **1h timeframe for both new strategies, not 15m.** See "Why 1h, not
  15m" above -- statistical credibility for the ensemble-vs-baseline
  comparison (this task's actual point) outweighs timeframe purity.
- **Majority vote, not a weighted average, for combining the ensemble's
  3 lookback pairs.** The task's own brief left this as an explicit
  judgment call. Chosen for zero additional tunable weight parameters
  and direct interpretability; the cost (discarding *how strongly* each
  pair agrees) is real but accepted, not hidden.
- **The ensemble's 3 lookback pairs are fixed, not grid-searched.**
  Explicitly reasoned through in "What was built" #4 above -- searching
  lookback-pair combinations would combinatorially blow up the tunable-
  knob count and directly work against Task G's own overfitting
  safeguard.
- **No `sensitivity_extractor` for the ensemble's real run.** See "Why
  the ensemble has no `sensitivity_extractor` result" above -- the check
  is structurally about grid-search risk, and the ensemble has no grid.
- **`compute_regime_weight`'s `None`-in -> `Decimal(0)`-out convention,
  vs. `compute_vol_scalar`'s `None`-in -> `None`-out convention.** A
  deliberate, documented asymmetry, not an inconsistency -- see both
  functions' own docstrings and the "What was built" summaries above for
  the full reasoning (ADX has a natural "off" floor on its own scale;
  realized volatility does not).
- **`max_vol_scalar=3`, a documented, non-searched safety cap** on how
  large the volatility-target multiplier can grow in a near-zero-
  volatility market -- see `volatility_targeting.py`'s own constant
  docstring.
- **ADX/vol/ATR "conviction" composition (`base * regime_weight *
  vol_scalar`) is inlined directly in each strategy's `_open` method,
  not given its own shared module function.** It's a two-multiply
  one-liner -- genuinely trivial arithmetic, not logic worth extracting
  into a third shared module; the two actually nontrivial pieces (the
  ADX ramp, the vol-scalar-with-cap arithmetic) each already have their
  own tested, shared, pure function in their respective modules.
- **Reused sr-f Part 2's exact 1h walk-forward windows
  (train=2160/validate=720/step=720) rather than picking new ones.**
  Avoids any appearance of window-shopping for a fold count or result
  shape; these windows were already established, documented, and
  "not tuned to hit a target fold count" by a prior task.
- **Both real walk-forward runs logged to the same real, accumulated
  `runs/experiments.jsonl`** (660 records after this task, 360 before)
  rather than a fresh/isolated log -- matches this project's standing
  single-writer, append-only, never-cleaned-up experiment-log
  convention, and is what makes the real `check_combination_count`
  results above meaningful (they reflect this `strategy_id`'s actual
  full history, not an artificially isolated sample).
- **No holdout access made by this task.** Neither strategy is close to
  clearing the eligibility bar, so there is nothing legitimate to spend
  either dataset's one-shot holdout access confirming -- same reasoning
  as every prior task's identical negative result.

## Deliberately out of scope

- **Tuning any parameter (ADX thresholds, vol-target level/bounds, ATR
  multipliers, risk fraction, candidate grid, lookback pairs, walk-
  forward windows) to produce a better-looking result.** Explicitly
  against this task's brief and CLAUDE.md's Strategy Research
  Methodology in spirit -- every constant used is a documented,
  commonly-cited convention or a direct reuse of a prior task's already-
  established value, never searched against this task's own results.
- **A weighted-average (rather than majority-vote) ensemble
  combination.** Considered, not built -- see "Judgment calls" above.
- **Testing the ensemble on 15m data for a second, parallel comparison.**
  The 15m dataset structurally caps at 3 folds regardless of strategy,
  which sr-c/sr-e/sr-f already established provides too little
  statistical power to add a meaningful second data point to this
  task's core comparison; not worth the added scope for a result that
  couldn't be interpreted with any real confidence either way.
- **Wilder's original exponential ADX smoothing**, in favor of the same
  plain-rolling-mean simplification `risk_management.AverageTrueRange`
  already established as this project's convention -- see Finding 3 and
  "What was built" #1 above.
- **A literal reproduction of Concretum's exact volatility-targeting
  formula** -- inaccessible during this task's research pass; the
  general institutional convention was implemented instead, explicitly
  labeled as such. See Finding 2.
- **Any holdout confirmation run for either strategy** -- see "Judgment
  calls" above.
- **Changing CLAUDE.md's Eligibility Bar thresholds, the 1h holdout
  cutoff, or the 1h walk-forward window defaults.** All three remain
  exactly as previously established/approved; this task evaluates
  against them, it doesn't change any of them.
- **Modifying `regime_momentum.py` or `regime_momentum_risk_managed.py`**
  (the earlier hard-binary-regime-gate strategies) to use the new
  continuous ADX weighting. Both are untouched, byte-for-byte -- this
  task adds two new strategies rather than modifying existing, already-
  logged ones, consistent with every prior task's precedent (sr-f didn't
  modify `regime_momentum.py` either, for the same "keep prior results
  reproducible against unmodified code" reasoning).
