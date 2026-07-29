# Strategy Research Task Q: Probabilistic and Deflated Sharpe Ratio

## Scope note

`sr-j` (the task that derived the Eligibility Bar's current, revised
fold-consistency + aggregate-significance clauses) named the **Probabilistic
Sharpe Ratio** as "the more statistically correct upgrade" over the plain
one-sample t-test it settled for, and deliberately deferred it -- "assessed
and named, not built speculatively", the same treatment `sr-g` gave
CSCV/PBO. It is listed under `sr-j`'s own "Deliberately out of scope" as
"A full Probabilistic Sharpe Ratio / Deflated Sharpe Ratio implementation".

This task builds it. The motivation is concrete rather than academic: this
project has now logged **1,839 backtest records across 8 strategy
families**, and its current aggregate-significance test (a one-sample
t-test on the per-fold Sharpe values) has no notion of how much searching
produced the result it is judging. The Deflated Sharpe Ratio is the
standard correction for exactly that.

**The Eligibility Bar itself is NOT amended by this task.** CLAUDE.md's
Bar is human-approval-gated (same status as Risk Parameters). DSR is
implemented and *reported*; `evaluate_eligibility`'s `passed` is computed
from exactly the same three checks as before. A later task collects the
proposal for the human.

## Formulas and sources

Both from the same Bailey/López de Prado lineage this project already
trusts for MinBTL (`research/overfitting_check.py`, `sr-g`) and catalogued
as source #5 in `sr-j`'s reference table:

- **PSR** -- Bailey & López de Prado, "The Sharpe Ratio Efficient
  Frontier", *Journal of Risk* 15(2), 2012.
- **DSR** -- Bailey & López de Prado, "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality",
  *Journal of Portfolio Management* 40(5), 2014.

```text
PSR(SR*) = Φ[ ((SR̂ − SR*) · sqrt(T − 1))
              / sqrt(1 − γ₃·SR̂ + ((γ₄ − 1)/4)·SR̂²) ]

SR₀ = sqrt(V̂) · [ (1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ]

DSR = PSR(SR₀)
```

- `T` -- number of return observations.
- `γ₃` -- skewness of the return series.
- `γ₄` -- **raw** kurtosis (3 for a normal), not excess. Getting this
  wrong shifts the estimator's variance term by exactly `(2/4)·SR̂²`.
- `γ` -- Euler–Mascheroni, 0.5772156649015329. Not in stdlib `math`
  (which carries pi/e/tau/inf/nan only), so spelled out as a module
  constant.
- `V̂` -- variance **across the N trials** of the estimated
  per-observation Sharpe.
- `Φ` / `Φ⁻¹` -- `statistics.NormalDist().cdf` / `.inv_cdf`.

Both `Φ` and `Φ⁻¹` were confirmed present in the stdlib before any code
was written, so **this is stdlib-only, zero new dependencies** -- the same
hard project rule the existing exact-t-distribution p-value
(`_regularized_incomplete_beta`, `sr-k`) already had to satisfy. No
numpy, no scipy.

**All Sharpe quantities are per-observation, never annualized.**
De-annualize by dividing by `sqrt(bars_per_day · 365)` for a per-bar
series or `sqrt(365)` for a daily one (this project's fixed 365-day
convention, not 365.25). At 1h bars the per-bar factor is
`sqrt(24·365) ≈ 93.6`, so feeding an annualized Sharpe straight in would
overstate PSR enormously.

## Verification anchor: reproduced exactly

The brief supplied an independent anchor: PSR at N=1 for "Configuration C"
should land near **0.5194**, against the existing t-test's 0.484.

Configuration C is the real `ensemble-momentum-configuration-c` /
`task-n-with-funding` run -- the 19-fold walk-forward on real BingX 1h
data behind CLAUDE.md's "Current best result". Its inputs, read from the
real experiment log (read-only; the log is gitignored and is never touched
by tests):

| Input | Value | Where from |
|---|---|---|
| mean annualized fold Sharpe | +0.03906545672922773 | `aggregate_metrics.mean_sharpe` |
| `bars_per_day` | 24 | 1h bars |
| `SR̂` (per-bar) | 4.173888594e-4 | `deannualize_sharpe` |
| `T` | 13,680 | `validate_bars` (720) × `fold_count` (19) |
| `γ₃`, `γ₄` | 0, 3 | normal-assumption fallback |
| `SR*` | 0 | N=1 ⇒ SR₀ = 0 |

**Result, computed by the shipped code:**

```text
z   = 0.04881665
PSR = 0.5194673      (anchor: ≈ 0.5194 — reproduced)
DSR at N=1 = 0.5194673   (identical, as it must be: SR₀ = 0 at N=1)
```

Cross-check against the module's *pre-existing and entirely separate*
one-sample t-test, which shares no code path with PSR (exact
t-distribution via a regularized incomplete beta vs. a normal CDF):

| Method | "better than chance" |
|---|---|
| PSR(0) | 0.519467 |
| 1 − one-sided t-test p (p = 0.484458) | 0.515542 |
| difference | **0.003926** |

Two independent methods landing within 0.004 of each other is strong
evidence the implementation is right.

**One honest discrepancy, investigated rather than papered over.** The
brief quotes the t-test as `t = 0.040455, p = 0.484088`, but the shipped
code reports `t = 0.039512, p = 0.484458` on the same run. Both are
correct — they are computed from different roundings of the same data.
The brief's fold list is CLAUDE.md's published 2-decimal rounding, whose
mean is exactly 0.040; the real logged values have mean 0.0390654. Running
the anchor both ways:

| Fold values used | mean | PSR | t | 1 − p |
|---|---|---|---|---|
| exact, from the log | 0.03906546 | **0.5194673** | 0.039512 | 0.515542 |
| published 2-dp rounding | 0.04000000 | 0.5199326 | 0.040455 | 0.515912 |

So the brief's PSR anchor (0.5194) was computed from the exact mean and
its t-statistic (0.040455) from the rounded list. The tests assert the
exact-value anchor (0.5194, `abs=1e-4`); the 2-dp figure differs by
0.00047, which is rounding noise, not a defect.

`T`-convention sensitivity, since "13,680 bars" and "13,661 returns" are
both defensible readings of `T` (19 folds of 720 bars produce 719 returns
each, the fold boundaries not being real returns):

| `T` convention | PSR |
|---|---|
| `validate_bars × fold_count` = 13,680 | 0.5194673 |
| actual return count 19 × 719 = 13,661 | 0.5194538 |
| `data_range.num_bars` = 16,078 | 0.5211033 |

The first two differ in the 5th decimal. The module takes `T` as an
explicit parameter rather than choosing for the caller.

## Honest input audit

What a logged `runs/experiments.jsonl` record could supply **before** this
task:

| Quantity | Available? | Source |
|---|---|---|
| `SR̂` per fold | yes | `fold_results[i].metrics.sharpe_ratio` (annualized) |
| `SR̂` aggregate | yes | `aggregate_metrics.mean_sharpe` (annualized) |
| `T` | yes | `walk_forward_config.validate_bars × fold_count`, or `data_range.num_bars` |
| `V̂` | yes | assembled across a family's records |
| `N` | caller-supplied | `sr-p`'s `FamilyOverfittingCheckResult.selection_trials` — see the aside below |
| `γ₃`, `γ₄` | **NO** | `walkforward._metrics_summary` deliberately drops `equity_curve`; `Metrics.equity_curve` exists only in memory |
| `bars_per_day` | **NO** | only inferable by reverse-engineering `train_bars` (2160 ⇒ 1h, 8640 ⇒ 15m) |

The two gaps are handled differently, on purpose:

**Gap 1, the moments — handled twice.** `evaluate_psr` /
`evaluate_deflated_sharpe` take `skewness` and `kurtosis` as explicit
optional parameters defaulting to `None`. When `None`, they fall back to
`γ₃=0, γ₄=3` **and record `moments_source="normal_assumption"` on the
result dataclass** — never silently normal. A caller supplying `(0.0,
3.0)` explicitly gets numerically identical output but
`moments_source="observed"`, because "we measured 0 and 3" is a different
claim from "we assumed them". A half-specified pair (one given, one
`None`) raises: they describe the same series, so supplying one alone is a
caller mistake, not a partial measurement worth silently completing.

And the gap is closed permanently: `Metrics` now carries
`return_skewness` / `return_kurtosis` / `num_returns`, computed by a new
`metrics.metrics.compute_return_moments` from the very same equity curve
the Sharpe comes from, and forwarded by `walkforward._metrics_summary`.

**Gap 2, `bars_per_day`** — now written into `walk_forward_config`
directly.

### Aside: `N` is genuinely non-trivial, which is why it is a parameter

While auditing the real log (read-only) for this doc, the record shapes
came out as:

| Shape | Count |
|---|---|
| single-candidate records (no `fold_count`, no aggregate Sharpe), with a `parent_run_id` | 1,424 |
| single-candidate records, standalone | 383 |
| real walk-forward runs with `fold_count` and an aggregate Sharpe | 33 |

So "how many trials produced the best result" has at least two very
different defensible answers (1,839 vs 33) before anyone even argues about
re-runs of identical configurations. `evaluate_deflated_sharpe` therefore
takes `num_trials` as a plain `int` parameter and derives nothing.

`sr-p` landed while this task was in flight and now owns that derivation.
The composition point is concrete:
`research.overfitting_check.FamilyOverfittingCheckResult.selection_trials`
— documented there as "the honest `N` for this family" — feeds straight
into `evaluate_deflated_sharpe(num_trials=..., ...)`. Deliberately still
not wired together in code: `sr-p` deliberately reports *two* defensible
counts (`selection_trials`, which includes reproduction runs, and
`deduplicated_selection_trials`, which merges them), and its own docstring
says choosing between them "is a judgment call the heuristic shouldn't
make unilaterally". This module takes the number; it does not pick which
one.

## Kurtosis sensitivity, reproduced

The brief asserts the normal-moment fallback is defensible at this
project's magnitudes and asks for the sensitivity table to be reproduced
rather than taken on trust.

**First, the moments themselves — measured, not assumed.** Computed
directly from the real cached BingX `BTC-USDT` 1h closes (19,678 bars,
read-only):

| Series | n returns | skew | raw kurtosis |
|---|---|---|---|
| hourly, full 1h history | 19,677 | **−0.1029** | **12.8852** |
| hourly, Configuration C's research window | 16,077 | −0.1112 | 12.8599 |
| daily (24-bar resample), full history | 818 | −0.4105 | 5.7295 |

The brief's cited "skew ≈ −0.103, raw kurtosis ≈ 12.89" reproduces
exactly. Note the daily row: resampling *raises* |skew| but roughly halves
kurtosis, a partial central-limit effect — which matters because the daily
path must be evaluated with daily moments, not hourly ones.

**Detection threshold** = the annualized Sharpe at which PSR reaches 0.95,
solved by fixed-point iteration (the threshold appears on both sides via
the `SR̂²` term):

| Window | Moments | α=0.05 annualized-Sharpe threshold | vs normal |
|---|---|---|---|
| per-bar 1h, T=3,600 | normal (0, 3) | 2.5667 | — |
| per-bar 1h, T=3,600 | kurtosis only (0, 12.885) | 2.5691 | +0.093% |
| per-bar 1h, T=3,600 | real 1h (−0.1029, 12.885) | 2.5727 | +0.235% |
| daily, T=150 | normal (0, 3) | 2.5862 | — |
| daily, T=150 | real daily (−0.4105, 5.730) | 2.6763 | +3.484% |
| per-bar 1h, T=13,680 (Config C) | normal (0, 3) | 1.3164 | — |
| per-bar 1h, T=13,680 (Config C) | real 1h (−0.1029, 12.885) | 1.3176 | +0.097% |
| daily, T=570 (Config C) | normal (0, 3) | 1.3190 | — |
| daily, T=570 (Config C) | real daily (−0.4105, 5.730) | 1.3400 | +1.595% |

And at Configuration C's *own* `SR̂` (4.17e-4 per bar), the moments are
effectively irrelevant:

| Moments | PSR |
|---|---|
| normal (0, 3) | 0.519467295 |
| real 1h (−0.1029, 12.885) | 0.519466873 |

A difference of 4e-7 — because `SR̂² ≈ 1.7e-7` makes the `((γ₄−1)/4)·SR̂²`
term vanish, exactly as the brief argued.

**Two honest deviations from the brief's stated numbers:**

1. The brief states the T=3,600 threshold moves "from 2.577 to 2.580"
   under real kurtosis. The *effect size* reproduces (+0.003 absolute /
   ~0.12% there vs +0.0024 / +0.093% here — same order, same
   "<1%" conclusion), but the *absolute level* does not: the stated
   formula at T=3,600 gives 2.5667, not 2.577. Every convention tried
   (ignoring the `SR̂²` term: 2.5662; T as returns rather than points:
   2.5670) lands at 2.566–2.567. The 2.577 figure is most likely a
   slightly different `T` (2.577 implies T ≈ 3,570). Reported rather than
   forced into agreement; the conclusion the table supports is unchanged.
2. The brief's "resampling 3,600 hourly points to 150 daily points moves
   the threshold only 2.566 → 2.574" reproduces **exactly** when the
   `SR̂²` term is ignored (2.5662 → 2.5744, the bracketed column above).
   With the full formula the daily figure is 2.5862, because at daily
   sampling `SR̂ ≈ 0.135` is large enough that `0.5·SR̂²` is no longer
   negligible. Both readings support the same point: resampling costs
   well under 1% of detection power.

## The iid problem and why daily resampling is the default

PSR's derivation assumes iid returns. This project's strategies hold
positions ~19h (`sr-i`: mean winner hold time) across 1h bars, so per-bar
returns inside a trade are strongly autocorrelated. Autocorrelation
inflates the *effective* `T`, and since `T` enters PSR through
`sqrt(T−1)`, an inflated `T` makes PSR **anti-conservative** — it reports
more confidence than the evidence supports. That is the wrong direction of
error for a bar meant to stop a project from promoting noise.

The standard remedy is to evaluate on daily-resampled equity returns, and
it is nearly free here because the detection threshold is governed by
calendar span, not sampling frequency (≈ `1.645/sqrt(years)`): the table
above shows the cost as 2.566 → 2.574 (0.3%) or 2.5667 → 2.5862 (0.8%)
depending on whether the `SR̂²` term is included. Compare that against
Configuration C's own numbers, where per-bar and daily agree to four
decimals anyway:

| Path | `SR̂` | `T` | PSR |
|---|---|---|---|
| per-bar | 4.1739e-4 | 13,680 | 0.5194673 |
| daily | 2.0448e-3 | 570 | 0.5194509 |

So: `resample_equity_to_daily(equity_curve, bars_per_day=...)` takes the
last bar of each **completed** day (a trailing partial day is dropped
entirely — its final observation is not a day's close, and including it
would make the last "daily" return cover a different span from every other
one), and `psr_from_equity_curve` defaults to `sampling=SAMPLING_DAILY`.
The per-bar path stays available as `sampling=SAMPLING_PER_BAR` and is
documented in the module docstring, not hidden.

## Judgment calls

**1. `Metrics`' three new fields are unconditional.** This departs from
the recent "field only appears when the feature is actually used"
convention (`funding_pnl_included`, `parameter_sensitivity`). Justified
because: (a) they are always computable from an equity curve
`compute_metrics` already builds; (b) DSR is about to be proposed as an
eligibility criterion, so every run wants them; and (c)
`_metrics_summary` deliberately drops `equity_curve` before logging, so
without these fields a logged run could *never* be re-evaluated under
PSR/DSR without re-running the whole backtest. A conditional flag would be
pure ceremony. Same reasoning for `bars_per_day` in
`walk_forward_config`. Flagged here because it is a real, deliberate
deviation from an established convention, not an oversight.

Checked before adding, as the brief required: **nothing in the codebase
constructs `Metrics` positionally.** The only non-`compute_metrics`
constructions are three keyword-only canned fixtures in
`tests/test_ensemble_momentum.py`. The new fields nonetheless carry
dataclass defaults (`None`/`None`/`0`) so a hand-built `Metrics` with no
equity curve to derive moments from does not have to invent them;
`compute_metrics` always populates all three explicitly.

**2. Population (biased) moments, not sample-adjusted.** `m3/m2^1.5` and
`m4/m2²` with divide-by-`n`, matching the form the PSR derivation assumes.
The difference from a Fisher-adjusted estimator is O(1/n) and negligible
at the observation counts here (hundreds to tens of thousands).

**3. `V̂ == 0` with N ≥ 2 returns `None`, not `SR₀ = 0`.** The
mathematical limit is genuinely 0 (no dispersion ⇒ no selection bias to
correct), but zero variance across 2+ *real* trials essentially always
means a caller passed one value repeated, and silently returning the most
permissive possible answer for what is probably a bug is the wrong
default. Documented, with the escape hatch named: call `evaluate_psr`
directly if the limit is genuinely wanted.

**4. `N == 1` is special-cased to `SR₀ = 0.0`.** The Gumbel-based
expected-maximum approximation is undefined there (`Φ⁻¹(1 − 1/1) =
Φ⁻¹(0) = −∞`), while the quantity it approximates — the expected maximum
of a single draw from a zero-mean distribution — is exactly 0. This is
also what makes "DSR at N=1 == PSR(0)" true, which is the verification
anchor above.

The approximation is asymptotic in `N` and is rough at small counts: at
N=2 it gives `0.5198·sqrt(V̂)` against a true expected maximum of
`1/sqrt(π) ≈ 0.5642·sqrt(V̂)`. It errs toward *under*-deflating there,
which is the permissive direction — noted in the function's docstring so a
small-N DSR is not mistaken for a conservative number. Growth for
reference (`V̂ = 1`):

| N | 1 | 2 | 5 | 10 | 50 | 100 | 1,000 | 1,839 |
|---|---|---|---|---|---|---|---|---|
| `SR₀/sqrt(V̂)` | 0.000 | 0.520 | 1.193 | 1.575 | 2.276 | 2.531 | 3.255 | 3.425 |

**5. No `passed` field on `DeflatedSharpeResult`.** Deliberate: a `passed`
would read as a gate, and DSR is not one yet.

**6. `statistics.NormalDist.cdf` underflows to exactly 0.0 below z ≈
−8.3** (it is `0.5·(1 + erf(z/sqrt(2)))`, and `erf` saturates at −1.0).
Confirmed empirically, not assumed. So a `psr`/`dsr` of `0.0` means "below
double-precision resolution", not "impossible" — and is explicitly *not*
this module's `None` ("no evidence") convention. Not worked around: a
strategy at PSR 1e-17 and one at PSR 0.0 get the same verdict. Documented
in the module docstring so a future reader does not mistake it for a bug.

## What was built

`python/research/eligibility.py` (additions only — every pre-existing
function is byte-for-byte unchanged in behaviour):

| Name | Purpose |
|---|---|
| `resample_equity_to_daily` | last bar of each completed day |
| `deannualize_sharpe` | annualized ⇒ per-observation, daily or per-bar |
| `evaluate_psr` / `PsrResult` | PSR, with `moments_source` provenance |
| `psr_from_equity_curve` | PSR straight off an equity curve, real measured moments, daily by default |
| `sharpe_variance_across_trials` | `V̂` from a family's trial Sharpes |
| `deflated_sharpe_benchmark` | `SR₀` |
| `evaluate_deflated_sharpe` / `DeflatedSharpeResult` | `DSR = PSR(SR₀)` |
| `evaluate_eligibility(..., deflated_sharpe=...)` | reports DSR; `passed` unchanged |

Constants: `EULER_MASCHERONI`, `NORMAL_SKEWNESS`, `NORMAL_KURTOSIS`,
`MOMENTS_SOURCE_OBSERVED`, `MOMENTS_SOURCE_NORMAL`, `SAMPLING_DAILY`,
`SAMPLING_PER_BAR`.

`python/metrics/metrics.py`: `ReturnMoments`, `compute_return_moments`,
`per_bar_returns` (extracted from `_sharpe_ratio` so the Sharpe and the
moments can never drift onto different definitions of "the return
series"), and the three new `Metrics` fields.

`python/research/walkforward.py`: `_metrics_summary` forwards the three
new fields; `walk_forward_config` gains `bars_per_day`.

Every dataclass is frozen with a `to_dict()`, every consciously-chosen
argument is keyword-only, and every degenerate input returns `None` rather
than raising or fabricating — matching the module's existing conventions.

## Testing

TDD throughout: the tests were written first and confirmed failing
(`ImportError` on the new names) before any implementation existed.

**61 new tests**, all in existing files (`tests/test_eligibility.py`,
`tests/test_metrics.py`, `tests/test_walkforward.py`). Full suite on the
rebased tree: **849 passed** (788 on `main` at the rebase point, which
already includes `sr-t`'s and `sr-p`'s own new tests; nothing regressed).

This branch was rebased onto `main` after `sr-t` (1d data path) and `sr-p`
(family-level trial accounting) landed. The only conflict was in
`tests/test_walkforward.py`, where `sr-p` and this task had each appended
a new block to the end of the same file; resolved by keeping both blocks
verbatim, derived mechanically from the index stages (with an assertion
that neither side had modified any pre-existing line) rather than
hand-transcribed. `research/walkforward.py` merged cleanly and carries
both `sr-p`'s `strategy_family` / `sensitivity:`-prefixed `parent_run_id`
and this task's `bars_per_day` + return-moment logging.

Coverage, per the brief's list:

- **PSR known values** — three independent kinds: `SR̂ == SR*` ⇒ exactly
  0.5 (analytic, no reference implementation needed); `γ₃=0, γ₄=1` makes
  the denominator exactly 1 so `z = SR̂·sqrt(T−1)` ⇒ `Φ(2)` at
  `SR̂=0.5, T=17`; and Configuration C's real anchor at 0.5194, plus a
  cross-check that it agrees with the pre-existing t-test to within 0.01.
- **Normal-assumption fallback records its source** — and an explicit
  `(0.0, 3.0)` call is numerically identical but records `"observed"`.
- **Directional sanity** — negative skew and fatter tails both *lower*
  PSR for a positive Sharpe; PSR rises with `SR̂` and falls with the
  benchmark.
- **Degenerate inputs return `None`** — `T ∈ {0, 1}`, `sharpe_ratio=None`,
  a non-positive estimator variance, `N < 1`, `V̂ = 0`, `V̂ = None` with
  `N ≥ 2`, `< 2` trials for `V̂`. Invalid *choices* still raise: negative
  `T`, negative `V̂`, kurtosis `< 1`, a half-specified moment pair, an
  unknown `sampling`.
- **Daily-resampling boundaries** — 3,600 bars ⇒ 150 points; 3,610 bars ⇒
  still 150, last value `equity_curve[3599]`; each point is that day's
  final bar; under one full day ⇒ `[]`; `bars_per_day ≤ 0` raises.
- **`evaluate_eligibility`'s `passed` is unchanged** when `deflated_sharpe`
  is supplied — tested both directions (a failing result stays failing
  with a good DSR; a passing result stays passing with a terrible one),
  plus that the `to_dict()` key is absent unless supplied.

The Configuration C fixture is embedded as literals in the test file, with
a comment recording why: `runs/experiments.jsonl` is gitignored, absent
from a fresh clone and from CI, and is the real research audit trail —
tests must not read it. This matches the existing
`TestMeanSharpeSignificanceRealFixture` fixture, which transcribes the
original ensemble's real fold Sharpes the same way.

## Deliberately out of scope

- **Amending CLAUDE.md's Eligibility Bar.** DSR is proposed, not adopted;
  the Bar is human-approval-gated and a later task collects the proposal.
  Nothing in this task's code can cause a strategy to pass or fail
  differently than before it.
- **Deriving `N` from the experiment log.** `sr-p` owns the trial count;
  `num_trials` is a plain parameter here so the two compose (see the aside
  above for the exact seam), and choosing between `sr-p`'s two defensible
  counts stays a caller/human decision.
- **Re-running any strategy under PSR/DSR to produce a new verdict.** This
  task builds and verifies the machinery. The illustrative Configuration C
  numbers above are exactly that — illustrative — and in particular no
  DSR-at-a-real-`N` figure is published here, because the real `N` is
  `sr-p`'s output and which of its two counts to use has not been decided.
- **The Probabilistic Sharpe Ratio's confidence-interval / minimum-track-
  record-length companions** from the same 2012 paper. Not needed for the
  significance question; not built speculatively, same discipline as
  `sr-g`'s CSCV/PBO deferral.
- **Backfilling the new logged fields onto the 1,839 existing records.**
  They were logged without moments and cannot gain them without re-running
  the backtests; the normal-assumption fallback (with its explicit
  provenance flag) is precisely the mechanism for evaluating those older
  records honestly.
