# Strategy Research Task AC: combined-holdout statistical meta-analysis

## Scope note -- read this before the numbers below

**This is a retrospective statistical meta-analysis of two already-executed
results, not a new strategy attempt, not a new trial, and not a new
pre-registration.** No holdout data was accessed to produce anything in
this document. No parameter was searched, fitted, or chosen. Everything
below is computed exclusively from summary statistics already present in
`runs/experiments.jsonl`'s two logged records:

- **`sr-v`** (2026-07-30): BingX BTC-USDT, 2021-05-14 through 2024-04-26,
  1,079 daily bars. `run_id=8143a525-3159-447b-991d-2f11a0ef790b`.
  `preregistration_id=daily-tsmom-ensemble-1d-holdout`. **INCONCLUSIVE.**
- **`sr-ab`** (2026-08-05): Binance spot BTCUSDT, 2017-08-17 through
  2021-05-13, 1,366 daily bars.
  `run_id=a84d52ba-5f5d-43bd-a528-3d5cd494208a`.
  `preregistration_id=daily-tsmom-ensemble-binance-virgin-holdout`.
  **INCONCLUSIVE.**

Both are `strategy_id="daily-tsmom-ensemble"` -- the identical,
zero-fitted-parameter Moskowitz-Ooi-Pedersen (2012) daily TSMOM ensemble,
byte-for-byte unmodified strategy code between the two runs -- evaluated
against two disjoint, non-overlapping calendar windows on two different
venues. Both remain individually `INCONCLUSIVE` on their own pre-committed
terms, unaffected by anything in this document; this task does not
re-litigate either verdict.

**Verified directly, not assumed**: re-running
`research.overfitting_check.check_project_combination_count` against the
live `runs/experiments.jsonl` after this task's own code (but before any
new log record -- this task writes none) gives
`research_selection_trials=121`, unchanged from the figure already stated
in CLAUDE.md's `sr-y` paragraph. This confirms the "not a new trial"
framing is not just asserted: both `sr-v` and `sr-ab` are holdout
confirmations, and `check_project_combination_count`'s own docstring
states holdout-related records are excluded from `N` "because a holdout
confirmation was never searched over" -- exactly the property this task's
combination relies on (see Analysis 1's validity conditions below). This
task adds no records to the log and therefore changes `N` not at all,
either now or when a future trial is logged.

## Bottom line, stated first (per this task's own brief, so it does not
## read as a discouraging surprise buried at the end)

**The combined evidence is statistically strong on pure significance
(Stouffer's combined Z = 2.914, corresponding probability 0.9982, clears
the project's 0.95 convention easily) -- but a full formal PASS on the
combined series is mathematically impossible regardless of that
significance, because the combined max drawdown is provably at least
20.135% against a 20% ceiling.** A strong Z-score does not and cannot
override a drawdown-ceiling breach; the two gates measure different
things (statistical confidence that a mean effect is real, versus a hard
practical risk-control limit on the worst peak-to-trough loss actually
realized), and this analysis is not license to read one as substituting
for the other. The combined trade count (90) also falls short of the
freshly-computed combined frequency-scaled floor (100) by 10. **Nothing
here is a pass, and nothing here resolves the human `Discuss` between
multi-symbol expansion and a paper-trading policy exception** -- see
"What this does and does not tell us" at the end.

---

## Analysis 1: Stouffer's (weighted) Z-score combination

### Why this is valid here

Combining two significance tests via Stouffer's method requires two
conditions this task's own author (not the module) is responsible for
verifying, since `research.meta_analysis.combine_z_scores` has no way to
check either:

1. **Same null hypothesis.** Both `sr-v`'s and `sr-ab`'s `psr.z_score`
   fields are PSR z-scores (`research.eligibility.evaluate_psr`) against
   the identical benchmark `benchmark_sharpe=0.0` -- confirmed directly
   against both records' `aggregate_metrics.psr.benchmark_sharpe` fields
   (both `0.0`). Same null: "true Sharpe of `daily-tsmom-ensemble` on BTC
   price data <= 0."
2. **Independent samples.** `sr-v`'s window (2021-05-14 through
   2024-04-26, BingX) and `sr-ab`'s window (2017-08-17 through
   2021-05-13, Binance spot) are calendar-disjoint with zero day overlap,
   confirmed directly by the two windows' own registered date ranges. The
   two venues' underlying BTC price series are near-identical (0.999955
   daily-log-return correlation, per `sr-z` -- CLAUDE.md's own "Exchange
   API Facts -- Binance" section), but this does not create
   *within-sample* dependence: the two z-scores are each computed once,
   on non-overlapping calendar data, with no shared fitting step and no
   information flow from one run into the other's own computation.

Both conditions hold. This is exactly the setup Stouffer's method exists
for.

### The real inputs

| | `sr-v` (BingX) | `sr-ab` (Binance) |
|---|---|---|
| `psr.z_score` | `1.5274374516384879` | `2.540994558102991` |
| `psr.num_observations` (n) | `1078` | `1365` |
| `psr.psr` (individual PSR) | `0.9366738652161312` | `0.99447311800124` |

(`num_observations` is `1078`/`1365`, one less than each window's own
`1079`/`1366` daily bars -- `evaluate_psr` operates on *returns*, which
are one shorter than the equity-curve point count they are computed from;
matches `resample_equity_to_daily`'s own documented behavior.)

### The computation

Implemented in `python/research/meta_analysis.py`
(`sample_size_weights`, `combine_z_scores`), tested in
`python/tests/test_meta_analysis.py` (15 tests, TDD -- the test file
existed and failed on `ModuleNotFoundError` before the module did).

Sample-size weighting (`w_i = sqrt(n_i)`, per Whitlock 2005 -- the
standard form for combining tests of unequal `n`, as this task's brief
specifies):

```
w_sr_v  = sqrt(1078)  = 32.83291031876401
w_sr_ab = sqrt(1365)  = 36.945906403822335

Z_combined = (w_sr_v * Z_sr_v + w_sr_ab * Z_sr_ab) / sqrt(w_sr_v^2 + w_sr_ab^2)
           = (32.83291031876401 * 1.5274374516384879 + 36.945906403822335 * 2.540994558102991)
             / sqrt(32.83291031876401^2 + 36.945906403822335^2)
           = 2.9140024493420316
```

**`combined_z = 2.9140024493420316`**

**`combined_probability = Phi(combined_z) = 0.9982158644653765`** (via
`statistics.NormalDist().cdf`, the same convention `evaluate_psr`'s own
`.psr` field already uses).

For context, the unweighted (equal-weight) variant gives a very close but
distinct figure: `combined_z = 2.876815862884613`,
`combined_probability = 0.9979914503388194` -- the sample-size weighting
shifts the combination slightly toward `sr-ab` (the larger, more
significant sample), as expected, but the two windows' `n` are close
enough (1078 vs 1365) that the weighted and unweighted results barely
differ in this specific case.

**This clears the project's standing 0.95 significance convention by a
wide margin, in either weighting.** Read plainly: if `sr-v`'s and
`sr-ab`'s z-scores really are independent tests of the same null (see
above), the combined evidence rejects "true Sharpe <= 0" with much higher
confidence than either individual test does alone (`sr-v` alone: PSR
0.9367, just under 0.95; `sr-ab` alone: PSR 0.9945, comfortably over).
This is the expected, mechanical effect of combining two moderately
significant independent tests -- meta-analysis systematically increases
power over any single underlying test, which is exactly why the human
operator's question ("is a real edge here, really, given the pattern
across both") is answerable at all from already-logged data.

### Verification of the combination code itself (not just the real numbers)

Two hand-derived identities, checked in the test suite independently of
the implementation under test (i.e., the expected value in each test is
computed by hand from the formula, not by calling the function twice):

1. **Two identical z-scores, unweighted**:
   `Z_combined = (z + z) / sqrt(1^2 + 1^2) = 2z / sqrt(2) = z * sqrt(2)`.
   Verified with `z = 1.6448536269514722` (the standard one-sided
   `alpha=0.05` critical z) in
   `test_two_identical_z_scores_unweighted_gives_z_times_sqrt_2`.
2. **Two identical z-scores, sample-size-weighted, EQUAL `n`**: at
   `n1 = n2 = n`, `w1 = w2 = sqrt(n)`, so
   `Z_combined = (sqrt(n)*z + sqrt(n)*z) / sqrt(n + n) = 2z / sqrt(2) = z * sqrt(2)`
   -- identical to case 1, exactly as this task's own brief predicted
   ("reduces to a known identity for the weighted case with equal n").
   Verified with `z = -0.7`, `n = 500` in
   `test_two_identical_z_scores_equal_n_weighted_reduces_to_same_identity`.
3. **Degenerate single input**: `w*z / sqrt(w^2) = z` for any positive
   `w` -- combining "one test with itself" is a no-op regardless of the
   weight supplied. Verified both with the default weight and with an
   arbitrary explicit weight (`42.0`) in
   `test_degenerate_single_input_returns_its_own_z_score_unchanged`.
4. The real `sr-v`/`sr-ab` combination itself is reproduced in
   `test_real_sr_v_and_sr_ab_psr_z_scores_sample_size_weighted`, with the
   expected value computed a second, independent way (direct formula
   arithmetic in the test) rather than only re-asserting the module's own
   output, plus an explicit `>= 0.95` assertion matching the interpretation
   above.

All 15 tests pass (`cd python && uv run pytest tests/test_meta_analysis.py -q`
-- see "Process verification" below for the full-suite run).

---

## Analysis 2: a provable lower bound on the combined max drawdown

### The claim, and why it matters before anything else in this analysis

**For any valid chronological concatenation of two return series, the
combined equity curve's max drawdown is provably `>= max(leg1's own max
drawdown, leg2's own max drawdown)`.** Concatenating `sr-ab`'s window
first (2017-2021, the real calendar order) and `sr-v`'s window second
(2021-2024):

```
combined_drawdown >= max(0.1199166660258215147953222484,   [sr-v's own worst_fold_max_drawdown]
                          0.2013533009425546035874520904)  [sr-ab's own worst_fold_max_drawdown]
                   = 0.2013533009425546035874520904
```

**This already exceeds the registered 0.20 (20%) drawdown ceiling.** A
full formal PASS on the drawdown criterion for the combined series is
therefore **mathematically impossible**, regardless of anything else this
document computes. Stated up front, per this task's own brief, precisely
so it does not read as a discouraging surprise buried at the end.

### Proof

Let `E1(t)` for `t in [0, T1]` be leg 1's own standalone equity curve
(here, `sr-ab`'s), and `E2(t)` for `t in [0, T2]` be leg 2's own
standalone equity curve (here, `sr-v`'s), each with its own arbitrary
positive starting equity. A chronological concatenation into one combined
curve `E(t)` for `t in [0, T1+T2]` is:

- `E(t) = E1(t)` for `t in [0, T1]` (leg 1 unchanged -- nothing precedes
  it).
- `E(t) = c * E2(t - T1)` for `t in (T1, T1+T2]`, where
  `c = E1(T1) / E2(0)` is the positive scalar that makes leg 2 continue
  seamlessly from leg 1's own final equity level (the standard meaning of
  "concatenating return series": leg 2's percentage returns are applied
  starting from wherever leg 1 left off, not reset to leg 2's own
  original starting capital).

**Leg 1's portion is untouched.** For `t in [0, T1]`, the running peak
`M(t) = max_{s<=t} E(s) = max_{s<=t} E1(s) = M1(t)` (leg 1's own
standalone running peak) exactly, because nothing before `t=0` exists to
raise it. So the combined drawdown at every point within leg 1's portion
is *identical* to leg 1's own standalone drawdown at that same point --
not merely `>=`, exactly equal. In particular, the combined series'
overall max drawdown is `>= ` leg 1's own standalone max drawdown
(the combined maximum is taken over a superset of points that includes
leg 1's own).

**Leg 2's portion can only get worse, never better.** For
`t = T1 + s`, `s in (0, T2]`:

```
M(t) = max(M1(T1), c * M2(s))          [M1(T1) = leg 1's own global peak]
E(t) = c * E2(s)

drawdown(t) = 1 - E(t)/M(t)
            = 1 - c*E2(s) / max(M1(T1), c*M2(s))
```

Since `max(M1(T1), c*M2(s)) >= c*M2(s)`:

```
E(t)/M(t) <= c*E2(s) / (c*M2(s)) = E2(s)/M2(s)

=>  drawdown(t) >= 1 - E2(s)/M2(s) = leg 2's own standalone drawdown at s
```

I.e. at every point within leg 2's portion, the combined drawdown is
`>=` what leg 2's own standalone drawdown would have been at that same
point -- because the combined running peak can only be pulled *up* by
leg 1's own already-elapsed equity levels, never down, which can only
make the drawdown ratio at any given equity level larger, never smaller.
Taking the max over leg 2's portion: the combined max drawdown restricted
to leg 2's portion is `>=` leg 2's own standalone max drawdown.

**Combining both halves**: the combined series' overall max drawdown
(the max over *all* points, both legs) is therefore
`>= max(leg 1's own standalone max drawdown, leg 2's own standalone max
drawdown)`. This holds regardless of which leg comes first -- the "exact
equality" case simply moves to whichever leg is first in the
concatenation.

### Numerical verification (extra confidence, per this task's own brief)

The proof above was additionally checked numerically against
`metrics.metrics._max_drawdown` (the project's own real drawdown
implementation, not a reimplementation) over 2,000 random synthetic
equity-curve pairs (random walks of 5-80 steps each, returns uniform in
`[-8%, +8%]`, `Decimal` arithmetic matching the project's own
convention), concatenated exactly per the proof's construction
(`scale = leg1[-1] / leg2[0]`). At the library default `Decimal` context
precision (28 significant digits), a small number of pairs appeared to
violate the bound by amounts on the order of `1e-27` to `1e-28` --
investigated and confirmed to be pure floating/Decimal division rounding
noise from computing `scale`, not a real violation: re-running the
identical 2,000 trials at 60-digit context precision shrank every
discrepancy to `~1e-60`, i.e. the "violations" shrink in lockstep with
added precision, the signature of representable-precision rounding, not
a real counterexample. **Zero real violations found in 2,000 trials at
either precision**; this is not shipped as production code (this task's
own brief scopes the shipped deliverable to Analysis 1's combination
function only) -- reported here as the verification step itself, run for
real, not skipped.

### What this bound is, and is not

This is a **lower bound**, not the actual combined max drawdown -- the
real figure would require the real, chronologically ordered daily equity
curves from both holdout runs, which this task's absolute constraint
forbids re-accessing (`runs/experiments.jsonl` logs summary statistics
only; neither run's `equity_curve` was persisted, per `walkforward.
_metrics_summary`'s own documented, deliberate omission -- see CLAUDE.md's
CSCV/PBO section). The true combined max drawdown could be higher than
20.135% (if leg 2's own path dips further below a peak elevated by leg
1's own prior high) but cannot be lower. Since the bound alone already
exceeds the ceiling, the exact figure does not change the conclusion --
a real value that can only be `>=` an already-over-ceiling bound is
also over-ceiling.

---

## Analysis 3: combined trade count vs. the real, recomputed frequency-scaled floor

Combined trades: `26 (sr-v) + 64 (sr-ab) = 90`. Unlike drawdown, trade
counts have no path-dependency across a concatenation -- a trade closed
within leg 1 and a trade closed within leg 2 are simply two disjoint
counts that sum exactly, regardless of ordering. **90 is correct and
exact, not a bound.**

Combined bars: `1079 (sr-v) + 1366 (sr-ab) = 2445`.

The frequency-scaled floor
(`max(30, min(100, floor(total_evaluated_bars / bars_per_day / 20)))`,
CLAUDE.md's own standing rule, revised 2026-07-29) was computed for real
via `research.preregistration.frequency_scaled_min_trades`, not
hand-arithmetic:

```python
>>> from research.preregistration import frequency_scaled_min_trades
>>> frequency_scaled_min_trades(total_evaluated_bars=2445, bars_per_day=1)
100
```

`evaluated_days = 2445 // 1 = 2445`; `2445 // 20 = 122`; clamped to the
formula's own cap of `100`. **This matches the task brief's own
hand-estimate exactly (100).**

**Combined trade count (90) falls 10 short of the recomputed combined
floor (100).** This is a real, if narrower, echo of the individual-leg
pattern: `sr-v` alone was 27 trades short of its own 53-trade floor
(51% of the floor), `sr-ab` alone was 4 trades short of its own 68-trade
floor (94% of the floor); the combined figure closes much of that
individual gap (90% of the recomputed floor) but does not clear it.

---

## Analysis 4: profit factor -- context only, not the binding constraint

| | `sr-v` (BingX) | `sr-ab` (Binance) |
|---|---|---|
| Profit factor | `2.8666166454829294` | `7.6803018650657116` |
| vs. 1.3-1.5 floor | comfortably above | far above (5-6x the floor) |

Both legs individually clear the 1.3-1.5 profit-factor floor by a wide
margin -- this was never the binding constraint for either run
individually, and remains not the binding constraint here.

**A rigorous pooled profit factor is not reconstructable from the logged
fields alone, and this document does not force an approximation.**
Profit factor is `gross_wins / gross_losses` (sum of winning trades'
P&L divided by the absolute sum of losing trades' P&L) -- a ratio of two
sums neither of which is separately logged; only the ratio itself
(`mean_profit_factor`/`min_profit_factor`, identical here since each is a
single-fold holdout run) is persisted in `aggregate_metrics`.
Reconstructing a genuine pooled ratio would require each run's own
gross-win and gross-loss totals (or, more granularly, its full
closed-trade list), neither of which `runs/experiments.jsonl` carries. A
weighted average of the two ratios (by trade count, or by any other
weighting) would not equal the true pooled `gross_wins/gross_losses`
ratio in general -- ratios do not average that way -- so no such figure
is reported here as if it were rigorous. This dimension is not the
binding constraint either way: both individual figures already clear the
floor by wide margins, and nothing about combining them plausibly
reverses that.

---

## What this does and does not tell us

**Does tell us**: combining the two independent significance tests
raises confidence that `daily-tsmom-ensemble`'s true Sharpe is positive
well past the project's standing 0.95 convention (combined probability
0.9982). Two moderate individual results, when genuinely independent
tests of the same null, combine into stronger evidence than either alone
-- this is meta-analysis working exactly as it should, and is a real,
computed fact about the already-existing evidence, not an artifact of
this task.

**Does not tell us, and this document is explicit about why**:

1. **Whether this is a live-tradeable strategy.** The Eligibility Bar's
   drawdown ceiling and trade-count floor exist for reasons that have
   nothing to do with whether a mean effect is statistically real:
   the drawdown ceiling is a **practical risk-control limit** on the
   worst peak-to-trough loss this project is willing to actually carry,
   independent of how confident the mean-effect statistics are: a
   strategy can have an extremely well-established positive mean edge
   and still be un-tradeable at this project's risk tolerance if its
   worst realized drawdown is too large. The trade-count floor is a
   **minimum-evidence-volume** requirement -- a guard against a result
   whose apparent edge is actually dominated by the idiosyncratic outcome
   of a small number of trades, which a Sharpe/PSR/Z-score computed over
   daily *returns* (not trade outcomes) does not by itself rule out. A
   strong combined Z-score answers a different question than either gate
   asks, and does not and cannot override either.
2. **A drawdown-ceiling breach here is not a close call decided by this
   analysis -- it is a mathematical certainty already established by
   Analysis 2's proof**, independent of how the significance numbers
   read. No amount of additional significance evidence changes that
   conclusion, because significance and drawdown are answering different
   questions about the data.
3. **Whether the underlying zero-fitted-parameter hypothesis has a real
   edge at all.** This document's own significance combination is
   suggestive, not dispositive -- both underlying tests were themselves
   deliberately conservative single-window holdout confirmations exactly
   because a *single* result on a *single* window (however positive) is
   not immune to that window's own idiosyncratic character; combining
   two still leaves open confounds neither individual result resolved
   (see `sr-ab`'s own "Known confound" section on its early-market-era
   character, and `sr-v`'s own report on its own window's
   favorable-for-trend character). A strong combined Z is evidence, not
   proof, and this document treats it as exactly that.
4. **What to do next.** This is explicitly a human `Discuss` question --
   whether to pursue multi-symbol expansion (slower, architecturally
   correct, the standing remedy (2) already named in CLAUDE.md) or a
   human policy-exception decision to proceed toward paper trading
   despite not formally clearing every Eligibility Bar gate. This
   document computes the clearest possible honest numbers to inform that
   decision; it does not make the decision. **A policy exception, if
   granted, would be a decision to proceed DESPITE the combined series
   not clearing the drawdown and trade-count gates -- it would not, and
   could not, retroactively satisfy those gates, waive CLAUDE.md's own
   non-negotiable rolling walk-forward validation requirement ("No
   strategy is eligible for paper trading without walk-forward
   validation... a single split can't distinguish a real edge from a
   result that happened to fit one historical window" -- which two
   single-window holdout confirmations, combined or not, still are not),
   or substitute for what the Eligibility Bar's holdout single-window
   variant itself requires. Nothing in this document proposes,
   recommends, or constitutes such an exception; it only states plainly
   what a human granting one would actually be overriding.

---

## Process verification

`cd python && uv run pytest -q`: full suite run, see the accompanying PR
for the exact pass count -- no test file was modified besides the new
`tests/test_meta_analysis.py` (TDD: written first, confirmed to fail on
`ModuleNotFoundError` before `research/meta_analysis.py` existed, then
15/15 passing after implementation). No holdout config, no strategy
code, no pre-registration file, and no gating threshold were touched by
this task. No new records were written to `runs/experiments.jsonl` --
this task performs pure computation over already-logged fields, verified
directly (`check_project_combination_count`'s `research_selection_trials`
unchanged at 121, matching the figure already in CLAUDE.md before this
task began).
