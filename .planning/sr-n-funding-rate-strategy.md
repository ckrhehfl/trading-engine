# Strategy Research Task N: funding-rate-extremity contrarian strategy + Configuration C funding re-run

## Scope note

Two things, per this task's brief, neither skippable: (1) build the first
strategy in this project on a genuinely different signal class (perpetual
funding rate, a positioning/crowding proxy) on top of `sr-m`'s funding-
rate infrastructure, and (2) re-run Configuration C (`sr-i`'s best real
result so far, mean Sharpe +0.027, 11/19 folds) with real funding P&L
now threaded through, to resolve CLAUDE.md's own open question about
whether the funding-rate gap was silently costing (or helping) that
figure. Both are real, walk-forward-validated results against real
cached BingX 1h data — no numbers below are hand-derived or estimated.

## Part 1: threshold design for the funding-extremity signal

### Why a rolling z-score, not a fixed absolute threshold

A real empirical check across this project's full cached funding-rate
history (6,199 rows, 2020-11-29 through present) found the **annual
standard deviation of funding rate itself shifted by roughly 9x across
years**:

| year | n | mean | stdev | min | max |
|---|---|---|---|---|---|
| 2020 (partial) | 97 | 0.000012 | 0.000099 | -0.000384 | 0.000333 |
| 2021 | 1095 | 0.000132 | 0.000405 | -0.001186 | 0.003000 |
| 2022 | 1095 | 0.000037 | 0.000209 | -0.000400 | 0.006034 |
| 2023 | 1095 | 0.000052 | 0.000125 | -0.000205 | 0.000608 |
| 2024 | 1098 | 0.000133 | 0.000149 | -0.000123 | 0.001171 |
| 2025 | 1095 | 0.000066 | 0.000046 | -0.000121 | 0.000505 |
| 2026 (partial) | 624 | 0.000025 | 0.000070 | -0.000455 | 0.000100 |

2021's stdev (0.000405) is ~8.8x 2025's (0.000046). A fixed absolute
threshold calibrated on one era would be badly miscalibrated in another
(near-silent in the quiet regime, firing on routine noise in the volatile
one) — this alone rules out a fixed cutoff and confirms the task brief's
own framing ("funding's typical range/distribution may have shifted").

### z-score vs. rolling percentile

Both would have been defensible; z-score was chosen, for three concrete
reasons (not a claim of objective superiority):

1. **Consistency with every other indicator in this codebase.** Every
   existing indicator (`risk_management.AverageTrueRange`,
   `regime_weighting.AverageDirectionalIndex`, `mean_reversion.
   BollingerBands`, `volatility_targeting.RollingRealizedVolatility`) uses
   a plain algebraic rolling-mean/rolling-stdev formula, never order
   statistics. A z-score keeps this strategy's threshold logic legible in
   the same terms as everything around it.
2. **Direct interpretability.** "How many standard deviations from its
   own recent normal" matches this project's existing threshold
   conventions (e.g. Bollinger Bands' `k * stdev` band width) more
   directly than a percentile rank would.
3. **No empirical reason found to prefer the more complex alternative.**
   A real frequency check (below) across several candidate lookback
   windows and thresholds found no meaningful difference in signal
   frequency between them — so there was nothing concrete a percentile
   approach would have fixed that a z-score didn't already handle.

### Lookback and threshold: empirical frequency check

Computed, over the real 1h research window (2,464 real settlements,
`2024-04-27` → `2026-02-26`, i.e. the same research window every other
1h strategy in this project uses):

| lookback (settlements) | thresh=1.5 | thresh=2.0 | thresh=2.5 |
|---|---|---|---|
| 30 | 316 | 157 | 77 |
| 60 | 323 | 150 | 88 |
| 90 | 319 | 159 | 80 |
| 180 | 308 | 148 | 73 |
| 270 | 298 | 155 | 80 |

("count of settlements with `|z| >= threshold`", out of 2,464.) No
lookback/threshold combination in this reasonable range stood out —
raw extreme-reading frequency is essentially flat across all of them
(~6% of settlements at threshold=2.0, regardless of lookback). This is
itself informative: it means the choice of lookback within this range
isn't a meaningfully "tunable knob" for raw signal frequency, consistent
with this project's "few tunable knobs" discipline (no grid search was
warranted here even before deciding not to build one — see Part 1's
design doc in `funding_extremity.py`'s own docstring).

**Chosen**: `lookback_settlements=90` (~30 days at funding's ~3x/day
cadence — deliberately matching this project's `validate_bars=720`
(30-day) 1h walk-forward window in spirit, though the units are
different: 90 real settlements, not 90 hourly bars), `entry_z_
threshold=2.0` (a standard "unusual" cutoff, not searched/tuned).

### Why funding history's much deeper depth than kline history matters here

Funding-rate history goes back to 2020-11-29 (6,199 rows total, clamped
to 5,744 within the same 1h holdout cutoff used for klines) — materially
deeper than 1h kline history (2024-04-27 onward, ~820 days). This means
`RollingFundingZScore`'s own 90-settlement (~30-day) warmup is already
satisfied by real historical funding data **before the first 1h kline
bar of interest even exists** — a genuine advantage over every price-
based indicator in this codebase, each of which needs its own multi-
week/month warmup period carved out of the (thinner) kline history
itself. `research/holdout.py::load_research_funding` (new, see Part 3)
is called with `start_ms=0` in this task's real run specifically to take
advantage of this.

### Why NOT ADX regime weighting (a deliberate exclusion)

Every price-based strategy in this project (`ensemble_momentum.py`,
`single_lookback_momentum.py`, `mean_reversion.py`) uses ADX-based regime
weighting. This strategy deliberately does not, for two reasons stated
in `funding_extremity.py`'s own module docstring (reproduced briefly
here, full reasoning there):

1. ADX measures price-trend strength — a different dimension from what
   this strategy's signal measures (funding/positioning extremity).
   There's no tested basis for which direction an ADX gate should even
   point for this signal (momentum's high-ADX-full-weight convention and
   mean-reversion's low-ADX-full-weight convention both import an
   assumption with no evidence behind it for *this* signal specifically).
2. Isolating this strategy to funding-only + risk management + vol
   targeting is what makes an honest evaluation of the funding-extremity
   signal *itself* possible — mixing in ADX would make it impossible to
   tell whether any result comes from the funding signal or a riding-
   along price-trend filter.

## Part 2: real walk-forward results

Both runs below use the exact same real data/config as `sr-i`/`sr-h`:
`load_research_klines`/`load_research_funding` against
`configs/research/holdout_1h.json` (16,078 research klines,
`2024-04-27T10:00:00Z` → `2026-02-26T07:00:00Z`; 5,744 funding rows,
clamped to the same cutoff), `train_bars=2160, validate_bars=720,
step_bars=720` (19 folds), `fee_bps=5, slippage_bps=2, bars_per_day=24`.

### Configuration C: before vs. after real funding P&L

**Reproduction check first**: Configuration C (`EnsembleMomentumTrainable`
with `adx_low=RECALIBRATED_ADX_LOW_THRESHOLD, adx_high=
RECALIBRATED_ADX_HIGH_THRESHOLD`, `params={"candidates":
DEFAULT_RISK_REWARD_TENTHS_CANDIDATES}`) was re-run **without**
`funding_rates` first, in this same script execution, as a same-code
same-cached-data sanity check before introducing the funding variable —
and it reproduced `sr-i`'s originally reported figures essentially
exactly (mean Sharpe 0.027134 vs. reported 0.027; min Sharpe -8.4418 vs.
-8.442; 11/19 folds positive, both; worst drawdown 4.2329% vs. 4.23%;
total trades 199, both; mean profit factor 1.9674 vs. 1.967; min profit
factor 0.1282 vs. 0.128). Confirms no code/data drift since `sr-i` — the
comparison below is a genuine isolated funding-inclusion delta, not
confounded by anything else having changed.

| metric | without funding (reproduction) | with real funding P&L | delta |
|---|---|---|---|
| run_id | `1ec94408-7d2d-42cc-aabd-622d49910b50` | `0fbc11cc-64d2-4ff5-b1ac-68382bdecf4d` | |
| mean Sharpe | 0.027134 | **0.039065** | +0.011932 |
| min Sharpe | -8.441794 | -8.382326 | +0.059468 (less bad) |
| folds positive Sharpe | 11/19 (57.9%) | 11/19 (57.9%) | unchanged |
| worst-fold max drawdown | 4.2329% | 4.2365% | +0.0036pp (negligibly worse) |
| mean total return | 0.21322% | 0.21949% | +0.00627pp |
| total trades | 199 | 199 | unchanged (funding doesn't change entries) |
| mean profit factor | 1.96737 | 1.96839 | +0.00101 |
| min profit factor | 0.12823 | 0.13124 | +0.00302 |

**Honest verdict: a small, mostly neutral, slightly positive effect —
not a meaningful reversal in either direction.** Mean Sharpe moved
+44% in *relative* terms (0.027 → 0.039) but both are tiny absolute
numbers close to zero; every other metric moved by a fraction of a
percentage point, in either direction (worst drawdown got marginally
*worse*, not better — reported plainly, not cherry-picked). This is
consistent with `sr-i`'s own diagnosis of this strategy's short average
holding period (~19.3h for winners, ~11.5h for losers) — too brief for
most trades to accumulate many of funding's ~8h settlements, so funding's
aggregate P&L contribution across 199 trades stays small relative to the
strategy's price-driven P&L.

**Eligibility bar: unchanged verdict, both fail identically.**

| check | without funding | with funding |
|---|---|---|
| fold consistency (@80/85/90%, all three) | FAIL (57.9% < floor) | FAIL (57.9% < floor) |
| sign test (`p`, `n=19`) | FAIL (p=0.32380) | FAIL (p=0.32380, identical — same 11/19 win/loss split) |
| Sharpe significance (one-sample t-test, one-sided) | FAIL (p=0.48922) | FAIL (p=0.48446, marginally better, still nowhere near 0.05) |
| **overall** | **FAIL** | **FAIL** |

**This directly answers CLAUDE.md's open question**: the profit-factor
floor's slippage/fee cushion, not the funding-rate gap, is what's still
doing the real work for Configuration C specifically — funding P&L was
not silently costing (or hiding) a materially different result. This is
one real data point for one strategy with a specific (short) holding-
period profile, not a general claim that funding never matters for any
strategy in this project.

### Funding-extremity contrarian strategy: real result

`run_id=393c5acc-5dcc-43cb-a8f3-8a236da720d7`, `FundingExtremityTrainable`
with every default (`funding_zscore_lookback=90, entry_z_threshold=2`,
default ATR/vol-targeting constants), real funding P&L wired through
both the signal (construction-time `funding_rates`) and `compute_metrics`
(`run_walk_forward`'s `funding_rates` parameter).

| metric | value |
|---|---|
| fold count | 19 |
| mean Sharpe | -0.005415 |
| min Sharpe | -5.979144 |
| folds positive Sharpe | 3/19 (15.8%) |
| worst-fold max drawdown | 0.6268% |
| mean total return | 0.08658% |
| **total trades** | **7** |
| mean profit factor | 1.18774 |
| min profit factor | 0.0 |
| folds with zero trades | 14/19 |

Per-fold Sharpe (19 values, `None` where a fold had zero trades):
`4.581, None, None, 2.982, None, -5.097, None, 3.487, None, None, None,
None, -5.979, None, None, None, None, None, None`.

**Eligibility bar: FAILS outright, at all three candidate floors
(80/85/90%)** — fold consistency 15.8% (3/19), sign test p=0.99964
(worse than a coin flip, consistent with the negative mean Sharpe), Sharpe
significance p=0.50089 (no evidence). But the headline number that
actually matters here is **7 total trades against the bar's 100-trade
floor** — this result is inconclusive about the underlying signal, not a
clean "no edge" finding.

### Diagnosing the 7-trade result (mechanism, not noise)

A dedicated diagnostic script (not a redesign, not a second "candidate"
whose numbers get reported as an alternative — a mechanistic explanation
of the *already-reported* 7-trade result, same precedent as `sr-i`'s
Step 1 trade-level diagnosis) reused the exact same `RollingFundingZScore`/
`_funding_signal` code and the exact same `generate_folds` boundaries as
the real run above, and counted, per fold:

- **raw crossings into an extreme reading** (any transition from
  "not extreme" to "extreme", regardless of direction) — **71 total**
  across all 19 folds.
- **opposite-direction flips** (the strategy's actual edge-trigger
  condition: crossing into extreme, differing from the *last established*
  nonzero extreme direction) — **7 total** — an exact match to the real
  walk-forward's 7 trades, confirming the mechanism precisely.

**The bottleneck is the edge-trigger design, not genuine rarity of
extreme funding readings.** Extreme funding readings happen often enough
(71 times, ~3.7/fold on average) to support far more trades under a less
conservative trigger rule — but this strategy (matching every other
strategy in this codebase's "fire only on signal state change"
convention) requires the signal to flip all the way to the *opposite*
extreme, starting from a fresh (`None`) state at the beginning of every
fold's 30-day validate window, before it ever fires. Funding tends to
stay in one regime (persistently high or persistently low) for extended
stretches rather than oscillating between opposite extremes within a
single 30-day window — real, disclosed evidence, not assumed.

**Not fixed in this task.** Changing the edge-trigger rule now, having
already seen a thin result from it, would cross into tuning-after-the-
fact — exactly what this project's methodology (experiment-count
tracking, holdout discipline) exists to guard against. This is flagged as
a concrete, scoped candidate follow-up instead (see "Deliberately out of
scope" below), not silently patched under result pressure.

## Part 3: `funding_rates` pass-through design

### `research/walkforward.py::run_walk_forward`

Checked first (per this task's brief) whether any existing mechanism
threaded `funding_rates` from a walk-forward caller down to `metrics.
metrics.compute_metrics` — there was none (`sr-m` explicitly named this
as deliberately out of scope for itself). Added a new, additive/opt-in
`funding_rates: Sequence[FundingRate] | None = None` keyword parameter,
following this project's established "new opt-in parameter, `None`
default, existing callers see byte-for-byte identical behavior" pattern
(same shape as Task D's `parent_run_id`, Task G's `sensitivity_extractor`).

Passed straight through, **unfiltered**, to every fold's `compute_metrics`
call. This is deliberate and safe, not a lookahead risk: `compute_metrics`/
`metrics.position.PositionTracker.apply_funding_through` only ever
consume funding rows up to each kline's own `open_time` (cursor-based,
see that module's docstring) — a fold's own `validate_klines` upper bound
still caps what actually gets applied, regardless of how much extra
(temporally later) data the passed-in series happens to contain. Verified
directly by a dedicated test
(`test_run_walk_forward_funding_rates_only_affect_folds_whose_validate_
window_contains_a_settlement`) proving a settlement placed inside fold
1's window has zero effect on fold 0's reported metrics.

Also added a conditional `walk_forward_config["funding_pnl_included"] =
True` marker to the logged record — **only present when `funding_rates`
is actually supplied** (same "field only appears when the feature is
used" convention as `parameter_sensitivity`), so an existing reader of
`runs/experiments.jsonl` sees byte-for-byte the same shape as before this
parameter existed.

Not threaded into `sensitivity_extractor`/`check_parameter_sensitivity`'s
in-sample re-evaluations — a diagnostic-only, opt-in add-on with its own
already-documented failure containment; funding P&L for that path is
deliberately out of scope (see below). The Configuration C re-run in this
task deliberately did not use `sensitivity_extractor` either (present in
the original `sr-i` run, but a diagnostic-only field that doesn't affect
any headline metric) — kept the comparison isolated to the funding
variable specifically.

### `research/holdout.py::load_research_funding` (new)

`sr-m` explicitly deferred building a funding-data research loader. This
task needed one for both real runs, so it was added, mirroring
`load_research_klines` closely: same unconditional holdout-cutoff clamp
(`end_ms` clamped down to `holdout_cutoff_ms`, never enforced as an
error), same warning-on-actual-clamp behavior. Two differences, both
documented in the function's own docstring: `symbol` is a real explicit
parameter (funding has no `interval` concept, so no dedicated per-
timeframe holdout config the way klines have `holdout.json` vs.
`holdout_1h.json` — the same `holdout_config_path` a caller already
passes for klines is reused for funding too). No `load_holdout_funding`
counterpart was added — no task has needed one yet (neither real run in
this task touches the holdout split); adding one speculatively would go
against "touch only what the task requires."

## TDD

New/modified test files, all written first and confirmed to exercise
real (not placeholder) behavior:

- `python/tests/test_funding_extremity.py` (new, 34 tests): `Rolling
  FundingZScore` (warmup, hand-computed z-score, zero-variance handling,
  look-ahead safety via an extra-future-rows check, unsorted-input
  rejection, between-settlements caching), `_funding_signal` (the
  contrarian direction logic — explicit tests for both directions
  named in this task's brief: high-positive-z → SHORT, very-negative-z →
  LONG, plus the in-band/boundary/invalid-threshold cases), end-to-end
  edge-triggered contrarian direction through the full strategy (both
  crossing directions, first-reading-never-fires, all-`None` warmup),
  risk-management composition (ATR stop/target 1:2, fixed-fractional
  sizing, stop-hit flattening, 1h/`GUARDED_MARKET` intent shape), genuine
  (non-monkeypatched) `RollingFundingZScore` warmup wiring, and
  `FundingExtremityTrainable.fit()` (required-keyword `funding_rates`,
  candidate logging, fresh-strategy return, train-klines-only isolation,
  defaults, and — the honesty requirement from this task's brief — a spy
  test proving `fit()`'s own in-sample `compute_metrics` call receives
  `funding_rates`).
- `python/tests/test_walkforward.py` (+4 tests): omitting `funding_rates`
  matches explicit `None`; a settlement inside a fold's validate window
  changes that fold's `final_equity`/`funding_pnl`; a settlement outside
  a fold's window leaves it unaffected (the pass-through-safety proof);
  `funding_pnl_included` only appears in the logged record when
  `funding_rates` was actually supplied.
- `python/tests/test_holdout.py` (+8 tests): `load_research_funding`'s
  range/clamp/warning/symbol-default/symbol-override/typed-field
  behavior, mirroring the existing `load_research_klines` test coverage.

Full suite: **687 passed** (was 641 immediately before this task,
confirmed by running the complete, unfiltered `uv run pytest` suite both
before and after — nothing from any prior task regressed). 641 + 46
(34 + 4 + 8) = 687, exact match.

```text
$ cd python && uv run pytest -q
........................................................................ [ 10%]
........................................................................ [ 20%]
........................................................................ [ 31%]
........................................................................ [ 41%]
........................................................................ [ 52%]
........................................................................ [ 62%]
........................................................................ [ 73%]
........................................................................ [ 83%]
........................................................................ [ 94%]
.......................................                                  [100%]
687 passed in 60.25s
```

## CodeRabbit review findings (PR #48)

One review pass, 4 actionable findings. **All 4 accepted and fixed** —
none declined.

- **Major, genuine functional-correctness bug**: `FundingExtremityStrategy.
  __call__`'s edge-trigger condition originally gated `atr is not None and
  atr > 0` *inside* the same `if` that decides whether to attempt an
  entry at all — so when ATR was unavailable (warmup, or a degenerate
  `atr <= 0`) at the exact bar a real opposite-extreme crossing occurred,
  `_open()` was never called, `entry_rejected_by_filters` stayed `False`
  (it's only ever set *inside* the now-skipped branch), and `_signal_
  state` was silently updated anyway — permanently losing that crossing
  until the z-score flipped again, exactly the failure mode the `entry_
  rejected_by_filters` mechanism exists to prevent (already correctly
  handled for the vol-scalar/`final_quantity<=0` filters, just not for
  ATR). Confirmed as a real bug via a failing-first TDD test
  (`test_atr_warmup_does_not_consume_the_edge_trigger_state`, run against
  the pre-fix code and confirmed to fail: `0 == 1`) before fixing.
  **Real-run impact: none for the actual reported figures** — re-ran the
  funding-extremity strategy's full 19-fold walk-forward after the fix
  (`run_id=2177505c-b968-4bda-8aa8-9e2b686e777e`) and every figure
  (7 trades, identical per-fold Sharpe values, identical eligibility
  verdict) matched the pre-fix run byte-for-byte — because `AverageTrueRange`'s
  own warmup (14 bars) completes almost immediately relative to
  `RollingFundingZScore`'s (90 real settlements, up to ~30 days), ATR was
  already long-warm by the time this strategy's z-score ever produces its
  first real (non-`None`) reading in this specific dataset. A genuine
  correctness fix for robustness (a degenerate `atr<=0` reading could
  still occur at any point, not just during warmup), not one that changes
  any number in this document.
- **Real data-integrity risk**: `load_research_funding`'s `symbol`
  default was a hardcoded `"BTC-USDT"` literal, unlike `load_research_
  klines`'s config-derived default — a caller passing a holdout config
  for a different symbol while forgetting to also pass `symbol` explicitly
  would have silently paired that symbol's klines with BTC-USDT's funding
  data. Fixed: `symbol` now defaults to `None`, resolved from the given
  `holdout_config_path`'s own `symbol` field (matching `load_research_
  klines`'s behavior exactly) when omitted; an explicit `symbol` still
  overrides. New regression test
  (`test_load_research_funding_default_symbol_tracks_a_non_btc_holdout_
  config`) uses a real non-BTC holdout config to prove the default is
  genuinely config-derived, not a literal that happens to match every
  other fixture's BTC-USDT config.
- **`zip(seq, seq[1:])` → `itertools.pairwise`**: a real Ruff finding
  (B905/RUF007) in `RollingFundingZScore`'s ascending-order validation —
  functionally identical, clearer intent, no slice copy. Fixed.
- **Test coverage nitpick, accepted**: `test_fit_never_reads_klines_
  beyond_train_klines`'s original form only compared `id(klines_arg) ==
  id(klines)` against a `klines` array with no "future" beyond it to leak
  in the first place — proving object-identity pass-through, not genuine
  lookahead safety. Fixed to give `fit()` a real 40-bar array, pass only
  the first 20 bars as `train_klines`, and assert every `run_backtest`
  call's `klines_arg` ends exactly at `train_klines[-1].open_time` — a
  test that would actually fail if `fit()` ever leaked bars 20-39 into
  `run_backtest`.

Full suite after all fixes: **689 passed** (was 687 immediately before
this review pass — the 2 new regression tests, ATR-warmup and non-BTC-
symbol, are the only production-code-touching fixes in this pass; the
`pairwise` and lookahead-test changes don't add new test count on their
own).

## Judgment calls resolved without asking

- **z-score over rolling percentile** for the extremity threshold — see
  Part 1's three-reason justification; both would have been defensible,
  documented as a judgment call rather than an objectively-superior
  choice.
- **No ADX regime weighting** — a deliberate exclusion, not an oversight;
  full reasoning in `funding_extremity.py`'s module docstring and Part 1
  above.
- **No opt-in grid search** on `funding_zscore_lookback`/`entry_z_
  threshold` — same precedent as `mean_reversion.MeanReversionTrainable`
  (Task K): a brand-new, not-yet-evaluated signal family shouldn't gain a
  second tunable dimension before it's cleared even one real evaluation.
- **`funding_rates` passed unfiltered (full series) to every fold**,
  relying on the already-established cursor-based look-ahead-safety
  guarantee (`metrics.position.PositionTracker`) rather than pre-slicing
  per fold — simpler, and explicitly verified safe by a dedicated test
  (see "TDD" above).
- **Config C re-run without `sensitivity_extractor`** — kept the before/
  after comparison isolated to exactly one variable (funding P&L); the
  diagnostic field doesn't affect any headline metric anyway.
- **`load_holdout_funding` not built** — no real caller needs it yet;
  adding it speculatively would be scope creep. Documented explicitly in
  `load_research_funding`'s own docstring as a known, deliberate gap.
- **The 7-trade result reported as-is, not redesigned after the fact** —
  see Part 2's "Diagnosing the 7-trade result" section for the full
  reasoning; this was the single most consequential judgment call in this
  task, made explicitly to avoid tuning-after-seeing-results.
- **No holdout access** — neither real result here clears the Eligibility
  Bar (Configuration C fails on fold-consistency/sign-test/Sharpe-
  significance regardless of funding P&L; the funding-extremity strategy
  fails on trade count so severely the bar can't be meaningfully applied)
  — nothing legitimate to spend either dataset's one-shot holdout access
  confirming.

## Deliberately out of scope

- **Redesigning the funding-extremity strategy's edge-trigger rule** —
  diagnosed, not fixed; a real, scoped candidate for a future task (see
  CLAUDE.md's updated "Strategy Attempts So Far").
- **Threading `funding_rates` into `research.robustness.
  check_parameter_sensitivity`'s in-sample re-evaluations** — a
  diagnostic-only, opt-in overfitting-safeguard path, unrelated to this
  task's two actual deliverables.
- **`load_holdout_funding`** — no real caller yet.
- **Any holdout confirmation run** for either result in this task.
- **The single-symbol-scope reconsideration** flagged in CLAUDE.md as the
  next-larger option if funding also didn't clear the bar — it didn't,
  but this task's own honest finding (the funding-extremity result is
  inconclusive, not a clean failure) means jumping straight to that
  larger architecture reconsideration isn't yet justified either; both
  live options are named in CLAUDE.md's updated "Strategy Attempts So
  Far" without a default choice made between them.
