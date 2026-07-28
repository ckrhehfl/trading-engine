"""Reusable Backtest/Walk-Forward Eligibility Bar evaluation utility --
Strategy Research Task K. See CLAUDE.md's "Backtest/Walk-Forward
Eligibility Bar" (revised 2026-07-27) and `.planning/sr-j-fold-diagnosis-
and-eligibility-review.md` for the full derivation this module implements:
the original literal "positive Sharpe in every fold" clause was found to be
statistically stricter than intended (even a genuinely strong, real
80%-true-edge strategy clears a literal 19/19 sweep only ~1.4% of the time)
and was replaced with two required checks:

1. **Fold consistency**: at least a configurable floor (CLAUDE.md's
   human-approved range is 80-90%; this module does not hardcode a single
   point value inside that range -- `evaluate_fold_consistency`/
   `evaluate_eligibility`'s `min_fraction`/`min_fold_consistency` is a
   REQUIRED keyword argument with no default, so a caller must consciously
   choose a value rather than silently inherit one this module picked on
   its behalf) of folds must show positive (strictly > 0) annualized
   Sharpe.
2. **Aggregate significance**: the full set of per-fold Sharpe ratios must
   reject "no real edge" via BOTH (a) a binomial sign test on the fold
   win/loss count against `p=0.5` (`binomial_sign_test_p_value`, via stdlib
   `math.comb`, following `.planning/sr-j-fold-diagnosis-and-eligibility-
   review.md`'s own exact worked methodology -- see this module's test
   suite, which reproduces several of that document's own published
   reference numbers directly) AND (b) a significance check on the mean
   fold Sharpe against zero. **Both required, not either** -- they catch
   different failure modes: the sign test catches "wins no more often than
   a coin flip"; the Sharpe-significance test catches "wins slightly more
   often than a coin flip, but the aggregate risk-adjusted return is still
   noise" (e.g. many small wins erased by a few disproportionate losses) --
   a case a fold-count percentage alone would not by itself rule out.

Both checks are evaluated ONE-SIDED ("better than chance", not merely
"different from chance in either direction"), per CLAUDE.md's explicit
wording.

## The mean-Sharpe-significance test: implemented, not deferred

sr-j proposed "a plain one-sample t-test as the immediately implementable
stdlib-only version" but explicitly disclosed an open cost: "the t-test's
exact p-value needs either scipy or an accepted approximation... not yet
resolved". This module resolves that cost by implementing the EXACT
(to floating-point precision) t-distribution p-value via the regularized
incomplete beta function (`_regularized_incomplete_beta`), computed with the
standard continued-fraction algorithm (Numerical Recipes, Press et al., 3rd
ed., section 6.4) using only stdlib `math` (`math.lgamma`, no `scipy`, no
new dependency) -- this is the same numerical relationship
`scipy.stats.t.cdf`/`scipy.special.betainc` themselves are built on, not an
approximation or a shortcut. See `_regularized_incomplete_beta`'s own
docstring for the three independent, non-fabricated verification methods
this module's test suite checks it against (the exact closed-form Cauchy
distribution at df=1, the standard-normal limit as df -> infinity via
`math.erf`, and well-known textbook Student's-t critical-value tables).

This module deliberately does NOT evaluate the OTHER Eligibility Bar
criteria (fold-count credibility floor, drawdown ceiling, minimum trade
count, profit-factor floor) -- those are simple threshold comparisons a
caller can check directly against a `WalkForwardResult`'s `.aggregate` dict
(`research/walkforward.py` already computes and logs them); this module
exists specifically for the two statistically nontrivial checks this
project's revised bar requires.

## Probabilistic / Deflated Sharpe Ratio (Strategy Research Task Q)

sr-j named the Probabilistic Sharpe Ratio as "the more statistically
correct upgrade" over the plain t-test above, and deferred it -- "assessed
and named, not built speculatively", the same treatment sr-g gave CSCV/PBO.
This module now builds it, plus the Deflated Sharpe Ratio that extends it,
from:

- Bailey & Lopez de Prado, "The Sharpe Ratio Efficient Frontier", *Journal
  of Risk* 15(2), 2012 -- PSR.
- Bailey & Lopez de Prado, "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality", *Journal of
  Portfolio Management* 40(5), 2014 -- DSR.

    PSR(SR*) = Phi[ ((SR_hat - SR*) * sqrt(T - 1))
                    / sqrt(1 - g3*SR_hat + ((g4 - 1)/4)*SR_hat^2) ]

    SR0 = sqrt(V_hat) * [ (1 - gamma)*Phi^-1(1 - 1/N)
                          + gamma*Phi^-1(1 - 1/(N*e)) ]

    DSR = PSR(SR0)

where `T` is the number of return observations, `g3`/`g4` are the return
series' skewness and **raw** (3 for a normal, not excess) kurtosis, `gamma`
is the Euler-Mascheroni constant, `V_hat` is the variance across the `N`
trials of the estimated per-observation Sharpe, and `Phi`/`Phi^-1` are
`statistics.NormalDist().cdf`/`.inv_cdf` -- so this stays stdlib-only, zero
new dependencies, exactly like the t-test above.

Why DSR matters here specifically: this project has now logged 1,839
backtests across 8 strategy families. A significance test that ignores how
much searching produced its best result is measuring the wrong thing. DSR
is the standard correction -- it raises the bar a Sharpe must clear in
proportion to the number of trials and their dispersion.

### Sharpe units

Every Sharpe quantity in this section is **per-observation**, never
annualized. Convert with `deannualize_sharpe` (this project's fixed 365-day
convention). Feeding an annualized Sharpe straight into `evaluate_psr`
overstates PSR enormously -- at 1h bars the annualization factor is
sqrt(24*365) ~= 93.6x.

### Honest input audit (as of 2026-07-28)

Available from a logged `runs/experiments.jsonl` record today:

- `SR_hat`: per-fold (`fold_results[i].metrics.sharpe_ratio`) and aggregate
  (`aggregate_metrics.mean_sharpe`) -- annualized, so de-annualize first.
- `T`: `walk_forward_config.validate_bars * fold_count`, or
  `data_range.num_bars`.
- `V_hat`: assembled across a strategy family's records via
  `sharpe_variance_across_trials`.
- `N`: the trial count, supplied by the caller (a separate task owns
  deriving it correctly from the experiment log; this module deliberately
  takes it as a plain parameter so the two compose without either
  depending on the other's internals).

NOT available before Task Q, and the two gaps it closes:

1. **Per-return skewness/kurtosis.** `walkforward._metrics_summary`
   deliberately drops `equity_curve`, and `Metrics.equity_curve` exists
   only in memory -- so a logged run carried nothing from which g3/g4 could
   be recovered. Handled two ways: `evaluate_psr`/`evaluate_deflated_sharpe`
   take `skewness`/`kurtosis` as explicit optional parameters that fall
   back to the normal assumption (g3=0, g4=3) while **recording
   `moments_source="normal_assumption"` on the result** -- never silently
   normal; and `metrics.metrics.Metrics` now carries
   `return_skewness`/`return_kurtosis`/`num_returns` unconditionally, so
   runs logged from now on can be evaluated with real moments.
2. **`bars_per_day`.** Previously inferable from a logged record only by
   reverse-engineering `walk_forward_config.train_bars` (2160 => 1h,
   8640 => 15m). Now logged directly in `walk_forward_config`.

The normal fallback is defensible at this project's magnitudes, not just
convenient: real BTC-USDT 1h returns measure skew ~= -0.103 and raw
kurtosis ~= 12.89 (measured, not assumed -- see
`.planning/sr-q-deflated-sharpe.md` for the reproduction), yet at T=3,600
that moves the alpha=0.05 annualized-Sharpe detection threshold only from
2.567 to 2.573 (~0.2%), because the per-bar Sharpe is ~4e-4 and the
`((g4-1)/4)*SR_hat^2` term is therefore vanishingly small. The planning doc
publishes the full sensitivity table.

### One correction the papers' assumptions demand: daily resampling

PSR assumes iid returns. This project's strategies hold positions ~19h
across 1h bars, so per-bar returns are strongly autocorrelated *within* a
trade -- which inflates the effective `T` and makes PSR
**anti-conservative** (over-confident). The standard remedy is to evaluate
on daily-resampled equity returns, and it is nearly free here because the
detection threshold depends on calendar span, not sampling frequency
(~ `1.645/sqrt(years)`): resampling 3,600 hourly points to 150 daily points
moves the threshold only 2.566 -> 2.575 (ignoring the tiny SR^2 term).
`resample_equity_to_daily` does the resampling and `psr_from_equity_curve`
defaults to `SAMPLING_DAILY`; the per-bar path stays available via
`sampling=SAMPLING_PER_BAR` and is documented, not hidden.

### One numerical limit worth knowing about

CPython's `statistics.NormalDist.cdf` is `0.5 * (1 + erf(z / sqrt(2)))`, so
for `z` below roughly -8.3 the `erf` term reaches exactly -1.0 and the CDF
returns **exactly 0.0** rather than the true ~1e-17. A `psr`/`dsr` of `0.0`
therefore means "below double-precision resolution", not "impossible", and
in particular is NOT this module's `None` ("no evidence") convention -- it
is a real, decisively-negative answer. Deliberately not worked around: a
strategy whose PSR is 1e-17 and one whose PSR is 0.0 get the same verdict.

### Eligibility Bar status: PROPOSED, not adopted

CLAUDE.md's Eligibility Bar is human-approval-gated (same status as Risk
Parameters). DSR is a **proposed** replacement for its mean-Sharpe t-test
clause and has not been approved. `evaluate_eligibility` therefore accepts
an optional `deflated_sharpe` that it REPORTS and nothing more: `passed`
is computed from exactly the same three checks as before. A later task
collects the proposal for the human; this module must not front-run it.
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist, StatisticsError, fmean, stdev
from typing import Sequence

from metrics.metrics import compute_return_moments, per_bar_returns

DEFAULT_NULL_PROBABILITY = 0.5
DEFAULT_SIGNIFICANCE_ALPHA = 0.05

# 365, not 365.25 -- this project's fixed annualization convention (see
# CLAUDE.md's "Strategy Research Operational Design" and
# `metrics/metrics.py`'s `_DAYS_PER_YEAR`), not open to per-implementation
# judgment.
_DAYS_PER_YEAR = 365

# Euler-Mascheroni constant, the `gamma` in the Deflated Sharpe Ratio's
# expected-maximum (Gumbel) approximation. Not in stdlib `math` (which has
# pi/e/tau/inf/nan only), so it is spelled out here to full float precision
# rather than approximated.
EULER_MASCHERONI = 0.5772156649015329

# Moments of a standard normal distribution, used when a caller supplies no
# measured skewness/kurtosis. The kurtosis is RAW (3.0), not excess (0.0) --
# see `metrics.metrics.ReturnMoments`.
NORMAL_SKEWNESS = 0.0
NORMAL_KURTOSIS = 3.0

# `PsrResult.moments_source` values -- the difference between "we measured
# these moments" and "we assumed a normal distribution" is never left
# implicit on a result.
MOMENTS_SOURCE_OBSERVED = "observed"
MOMENTS_SOURCE_NORMAL = "normal_assumption"

# Sampling frequencies for `deannualize_sharpe`/`psr_from_equity_curve`.
# Daily is the default everywhere -- see the module docstring's
# "daily resampling" section for the iid-violation reasoning.
SAMPLING_DAILY = "daily"
SAMPLING_PER_BAR = "per_bar"
_VALID_SAMPLINGS = (SAMPLING_DAILY, SAMPLING_PER_BAR)


def _count_positive_folds(fold_sharpe_values: Sequence[float | None]) -> int:
    """Shared "positive fold" convention for both `evaluate_fold_consistency`
    and `evaluate_sign_test`: a `None` (zero-trade/zero-variance), zero, or
    negative Sharpe fold counts as NOT positive -- the same treatment
    `research.walkforward._aggregate_metrics`'s `all_folds_positive_sharpe`
    already gives it. Extracted into one helper (rather than duplicated
    inline in both call sites) so the two Eligibility Bar checks can never
    silently drift onto different definitions of "positive fold" if one is
    edited without the other.
    """
    return sum(1 for s in fold_sharpe_values if s is not None and s > 0)


# ---------------------------------------------------------------------------
# Fold consistency
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldConsistencyResult:
    num_positive: int
    num_folds: int
    fraction_positive: float | None
    min_fraction_required: Decimal
    passed: bool

    def to_dict(self) -> dict:
        return {
            "num_positive": self.num_positive,
            "num_folds": self.num_folds,
            "fraction_positive": self.fraction_positive,
            "min_fraction_required": str(self.min_fraction_required),
            "passed": self.passed,
        }


def evaluate_fold_consistency(
    fold_sharpe_values: Sequence[float | None],
    *,
    min_fraction: Decimal,
) -> FoldConsistencyResult:
    """CLAUDE.md's revised Eligibility Bar, clause 1: at least
    `min_fraction` of folds must show a positive (strictly > 0) annualized
    Sharpe. A `None` fold Sharpe (zero trades, or zero-variance returns --
    `metrics.metrics.Metrics.sharpe_ratio`'s "no evidence" convention)
    counts as NOT positive -- the same treatment `research.walkforward.
    _aggregate_metrics`'s `all_folds_positive_sharpe` already gives it.

    `min_fraction` is compared exactly via `Decimal` rational arithmetic
    (`num_positive / num_folds >= min_fraction`), never via a float
    round-trip, so a boundary case (e.g. exactly 16/20 against a 0.80
    floor) can never misclassify due to floating-point representation
    error.
    """
    if not 0 < min_fraction <= 1:
        raise ValueError(f"min_fraction must be in (0, 1], got {min_fraction}")

    num_folds = len(fold_sharpe_values)
    num_positive = _count_positive_folds(fold_sharpe_values)

    if num_folds == 0:
        return FoldConsistencyResult(
            num_positive=0,
            num_folds=0,
            fraction_positive=None,
            min_fraction_required=min_fraction,
            passed=False,
        )

    fraction_positive = num_positive / num_folds
    passed = Decimal(num_positive) / Decimal(num_folds) >= min_fraction
    return FoldConsistencyResult(
        num_positive=num_positive,
        num_folds=num_folds,
        fraction_positive=fraction_positive,
        min_fraction_required=min_fraction,
        passed=passed,
    )


# ---------------------------------------------------------------------------
# Binomial sign test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignTestResult:
    num_positive: int
    num_folds: int
    null_probability: float
    p_value: float
    alpha: float
    passed: bool

    def to_dict(self) -> dict:
        return {
            "num_positive": self.num_positive,
            "num_folds": self.num_folds,
            "null_probability": self.null_probability,
            "p_value": self.p_value,
            "alpha": self.alpha,
            "passed": self.passed,
        }


def binomial_sign_test_p_value(
    num_positive: int,
    num_folds: int,
    *,
    null_probability: float = DEFAULT_NULL_PROBABILITY,
) -> float:
    """One-sided upper-tail exact binomial p-value:
    `P(X >= num_positive | n=num_folds, p=null_probability)`, testing
    H0: true per-fold win probability <= `null_probability` against
    H1: true per-fold win probability > `null_probability` ("wins more
    often than chance") -- computed as an exact tail sum of the binomial
    PMF via stdlib `math.comb`, following `.planning/sr-j-fold-diagnosis-
    and-eligibility-review.md`'s own worked methodology precisely (this
    module's test suite reproduces several of that document's own
    published reference numbers directly, e.g.
    `P(X >= 11 | n=19, p=0.5) = 32.380%`, Configuration C's own real
    sign-test result).
    """
    if num_folds < 0:
        raise ValueError(f"num_folds must be non-negative, got {num_folds}")
    if not 0 <= num_positive <= num_folds:
        raise ValueError(f"num_positive must be in [0, num_folds] (num_folds={num_folds}), got {num_positive}")
    if not 0 < null_probability < 1:
        raise ValueError(f"null_probability must be in (0, 1), got {null_probability}")

    return sum(
        math.comb(num_folds, k) * (null_probability**k) * ((1 - null_probability) ** (num_folds - k))
        for k in range(num_positive, num_folds + 1)
    )


def evaluate_sign_test(
    fold_sharpe_values: Sequence[float | None],
    *,
    null_probability: float = DEFAULT_NULL_PROBABILITY,
    alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
) -> SignTestResult:
    """`binomial_sign_test_p_value` applied to a fold's Sharpe values
    (`None`/non-positive folds count as losses, same convention as
    `evaluate_fold_consistency`). `passed` is `True` iff `p_value < alpha`
    (the null of "no real edge" is rejected). Zero folds is a degenerate
    "no evidence" case: `p_value=1.0` (cannot reject anything), `passed`
    forced `False`.
    """
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    num_folds = len(fold_sharpe_values)
    num_positive = _count_positive_folds(fold_sharpe_values)

    if num_folds == 0:
        return SignTestResult(
            num_positive=0,
            num_folds=0,
            null_probability=null_probability,
            p_value=1.0,
            alpha=alpha,
            passed=False,
        )

    p_value = binomial_sign_test_p_value(num_positive, num_folds, null_probability=null_probability)
    return SignTestResult(
        num_positive=num_positive,
        num_folds=num_folds,
        null_probability=null_probability,
        p_value=p_value,
        alpha=alpha,
        passed=p_value < alpha,
    )


# ---------------------------------------------------------------------------
# Mean-Sharpe significance: exact t-distribution p-value via the
# regularized incomplete beta function (stdlib math only, no scipy).
# ---------------------------------------------------------------------------


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_continued_fraction(a: float, b: float, x: float, *, max_iterations: int = 200, tol: float = 1e-12) -> float:
    """Lentz's continued-fraction algorithm for the regularized incomplete
    beta function `I_x(a, b)`, restricted to the convergence region used by
    `_regularized_incomplete_beta` (`x < (a+1)/(a+b+2)`) -- the standard
    Numerical Recipes (Press et al., "Numerical Recipes in C", 3rd ed.,
    section 6.4) `betacf` algorithm, transcribed directly. Stdlib
    arithmetic only.
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < tol:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function `I_x(a, b)`, computed via
    the standard continued-fraction method (Numerical Recipes 6.4) --
    stdlib `math` only (`math.lgamma`), no `scipy`. Used below to derive an
    EXACT (to floating-point precision), not approximated, two-sided
    Student's t-distribution p-value from its well-known closed-form
    relationship to the incomplete beta function:
    `P(|T| >= |t|) = I_{df/(df+t^2)}(df/2, 1/2)`.

    This is the honest, stdlib-only implementation CLAUDE.md's revised
    Eligibility Bar section calls for ("implement it if you can do so
    honestly with stdlib only... or explicitly defer it") -- not an
    approximation or a shortcut: `scipy.stats.t.cdf`/`scipy.special.betainc`
    themselves are built on the same continued-fraction algorithm.

    Verified in `test_eligibility.py` against three independent,
    non-fabricated reference points, none of which exercise this same code
    path: (1) the exact closed-form Cauchy distribution (df=1, where this
    reduces to `1 - 2*atan(t)/pi`, independently computable via
    `math.atan`); (2) the standard normal limit as `df -> infinity`
    (independently computable via `math.erf`); (3) well-known textbook
    Student's-t critical-value tables (e.g. the two-sided 5%/1% critical
    values at df=18 are ~2.101/~2.878 -- the 5% figure is also
    independently cited in this project's own `.planning/sr-j-fold-
    diagnosis-and-eligibility-review.md`).
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def _t_distribution_two_sided_p_value(t_statistic: float, degrees_of_freedom: int) -> float:
    """Exact two-sided Student's t-distribution p-value:
    `P(|T| >= |t|) = I_{df/(df+t^2)}(df/2, 1/2)`.
    """
    if degrees_of_freedom < 1:
        raise ValueError(f"degrees_of_freedom must be >= 1, got {degrees_of_freedom}")
    x = degrees_of_freedom / (degrees_of_freedom + t_statistic * t_statistic)
    return _regularized_incomplete_beta(degrees_of_freedom / 2.0, 0.5, x)


@dataclass(frozen=True)
class MeanSharpeSignificanceResult:
    n: int
    mean: float | None
    stdev: float | None
    t_statistic: float | None
    degrees_of_freedom: int | None
    p_value: float | None
    alpha: float
    passed: bool

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "mean": self.mean,
            "stdev": self.stdev,
            "t_statistic": self.t_statistic,
            "degrees_of_freedom": self.degrees_of_freedom,
            "p_value": self.p_value,
            "alpha": self.alpha,
            "passed": self.passed,
        }


def evaluate_mean_sharpe_significance(
    fold_sharpe_values: Sequence[float | None],
    *,
    null_mean: float = 0.0,
    alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
) -> MeanSharpeSignificanceResult:
    """One-sample, ONE-SIDED (H1: true mean fold Sharpe > `null_mean`,
    "better than chance", not merely "different from chance in either
    direction" -- per CLAUDE.md's revised Eligibility Bar) t-test on the
    defined (non-`None`) per-fold Sharpe values, matching `research.
    walkforward._aggregate_metrics`'s own `mean_sharpe`/`min_sharpe`
    convention of excluding `None` folds (no evidence to average in)
    rather than treating them as zero.

    `None` fields (never a raised exception, never a fabricated `0.0`
    p-value) for fewer than 2 defined values (a sample stdev needs >= 2
    observations) or zero sample variance -- the same "no evidence, not bad
    evidence" degenerate-input convention `metrics.metrics._sharpe_ratio`
    already uses. `passed` is `False` whenever `p_value is None`.
    """
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    values = [float(s) for s in fold_sharpe_values if s is not None]
    n = len(values)
    if n < 2:
        return MeanSharpeSignificanceResult(
            n=n, mean=None, stdev=None, t_statistic=None, degrees_of_freedom=None, p_value=None, alpha=alpha,
            passed=False,
        )

    mean_value = fmean(values)
    try:
        sample_stdev = stdev(values)
    except StatisticsError:
        sample_stdev = 0.0

    if sample_stdev == 0.0:
        return MeanSharpeSignificanceResult(
            n=n, mean=mean_value, stdev=0.0, t_statistic=None, degrees_of_freedom=None, p_value=None, alpha=alpha,
            passed=False,
        )

    degrees_of_freedom = n - 1
    t_statistic = (mean_value - null_mean) / (sample_stdev / math.sqrt(n))
    two_sided_p = _t_distribution_two_sided_p_value(t_statistic, degrees_of_freedom)
    one_sided_p = (two_sided_p / 2.0) if t_statistic > 0 else (1.0 - two_sided_p / 2.0)

    return MeanSharpeSignificanceResult(
        n=n,
        mean=mean_value,
        stdev=sample_stdev,
        t_statistic=t_statistic,
        degrees_of_freedom=degrees_of_freedom,
        p_value=one_sided_p,
        alpha=alpha,
        passed=one_sided_p < alpha,
    )


# ---------------------------------------------------------------------------
# Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012)
# ---------------------------------------------------------------------------


def resample_equity_to_daily(equity_curve: Sequence[Decimal], *, bars_per_day: int) -> list[Decimal]:
    """One equity observation per **completed** day: the last bar of each
    full `bars_per_day` block.

    A trailing partial day is dropped entirely rather than contributing its
    latest mark -- a partial day's final observation is not a day's close,
    and including it would make the last "daily" return cover a different
    span from every other one. With 3,610 hourly bars and `bars_per_day=24`
    the result is the same 150 points as 3,600 bars would give.

    Exists because PSR assumes iid returns while this project's strategies
    hold positions across many bars (see the module docstring). `Decimal`
    values are passed through untouched -- this only selects, never
    arithmetic.
    """
    if bars_per_day <= 0:
        raise ValueError(f"bars_per_day must be positive, got {bars_per_day}")
    completed_days = len(equity_curve) // bars_per_day
    return [equity_curve[(day + 1) * bars_per_day - 1] for day in range(completed_days)]


def deannualize_sharpe(annualized_sharpe: float, *, bars_per_day: int, sampling: str = SAMPLING_DAILY) -> float:
    """Convert this project's annualized Sharpe convention back to the
    per-observation Sharpe PSR/DSR are defined on.

    Divides by `sqrt(365)` for `SAMPLING_DAILY` (one observation per day) or
    `sqrt(bars_per_day * 365)` for `SAMPLING_PER_BAR` -- the exact inverse
    of `metrics.metrics._sharpe_ratio`'s annualization, 365 not 365.25.

    `sampling` defaults to daily to match `psr_from_equity_curve`'s default;
    it must match whatever series `T` was counted on, or the resulting PSR
    is meaningless (at 1h bars the two differ by sqrt(24) ~= 4.9x).
    """
    if bars_per_day <= 0:
        raise ValueError(f"bars_per_day must be positive, got {bars_per_day}")
    if sampling not in _VALID_SAMPLINGS:
        raise ValueError(f"sampling must be one of {_VALID_SAMPLINGS}, got {sampling!r}")

    observations_per_year = _DAYS_PER_YEAR if sampling == SAMPLING_DAILY else bars_per_day * _DAYS_PER_YEAR
    return annualized_sharpe / math.sqrt(observations_per_year)


@dataclass(frozen=True)
class PsrResult:
    """`psr` and `z_score` are `None` -- never a fabricated `0.5`, never a
    raised exception -- for degenerate inputs (fewer than 2 observations, an
    undefined Sharpe, or moments implying a non-positive estimator
    variance), the same "no evidence" convention as the rest of this module.

    `moments_source` records whether `skewness`/`kurtosis` were measured
    (`MOMENTS_SOURCE_OBSERVED`) or assumed normal
    (`MOMENTS_SOURCE_NORMAL`); `sampling` records which return frequency the
    inputs describe, or `None` when the caller supplied `SR_hat`/`T`
    directly without saying.
    """

    psr: float | None
    sharpe_ratio: float | None
    benchmark_sharpe: float
    num_observations: int
    skewness: float
    kurtosis: float
    moments_source: str
    z_score: float | None
    sampling: str | None

    def to_dict(self) -> dict:
        return {
            "psr": self.psr,
            "sharpe_ratio": self.sharpe_ratio,
            "benchmark_sharpe": self.benchmark_sharpe,
            "num_observations": self.num_observations,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
            "moments_source": self.moments_source,
            "z_score": self.z_score,
            "sampling": self.sampling,
        }


def _resolve_moments(skewness: float | None, kurtosis: float | None) -> tuple[float, float, str]:
    """Normal-assumption fallback with explicit provenance. A half-specified
    pair raises: skewness and kurtosis come from the same return series, so
    supplying one without the other is a caller mistake, not a partial
    measurement worth silently completing.
    """
    if skewness is None and kurtosis is None:
        return NORMAL_SKEWNESS, NORMAL_KURTOSIS, MOMENTS_SOURCE_NORMAL
    if skewness is None:
        raise ValueError("skewness must be supplied whenever kurtosis is (they describe the same series)")
    if kurtosis is None:
        raise ValueError("kurtosis must be supplied whenever skewness is (they describe the same series)")
    if kurtosis < 1.0:
        raise ValueError(f"kurtosis must be raw (>= 1, 3 for a normal), not excess, got {kurtosis}")
    return skewness, kurtosis, MOMENTS_SOURCE_OBSERVED


def evaluate_psr(
    *,
    sharpe_ratio: float | None,
    num_observations: int,
    benchmark_sharpe: float = 0.0,
    skewness: float | None = None,
    kurtosis: float | None = None,
    sampling: str | None = None,
) -> PsrResult:
    """Probabilistic Sharpe Ratio: the probability that the true Sharpe
    exceeds `benchmark_sharpe`, given an observed Sharpe estimated from
    `num_observations` returns whose skewness/kurtosis are `skewness`/
    `kurtosis` (Bailey & Lopez de Prado 2012):

        PSR(SR*) = Phi[ ((SR_hat - SR*) * sqrt(T - 1))
                        / sqrt(1 - g3*SR_hat + ((g4 - 1)/4)*SR_hat^2) ]

    `sharpe_ratio` and `benchmark_sharpe` are **per-observation**, not
    annualized -- see `deannualize_sharpe` and the module docstring.

    `skewness`/`kurtosis` default to `None`, which falls back to the normal
    assumption (0, 3) and records `moments_source="normal_assumption"` on
    the result. They must be supplied together or not at all. `kurtosis` is
    raw (3 for a normal), not excess; a value below 1 is impossible for a
    real distribution and raises.

    `sampling` is provenance only -- it is recorded on the result and never
    used in the arithmetic, so a caller who de-annualized on a daily series
    can say so.

    Degenerate inputs yield `psr=None`/`z_score=None` rather than an
    exception or a fabricated 0.5: fewer than 2 observations (the estimator
    needs `T-1 >= 1`), a `None` `sharpe_ratio` (an undefined Sharpe, e.g. a
    zero-trade fold), or moments that drive the estimator's variance term to
    <= 0. A genuinely invalid *choice* -- a negative observation count, a
    half-specified moment pair, an impossible kurtosis -- still raises.
    """
    if num_observations < 0:
        raise ValueError(f"num_observations must be non-negative, got {num_observations}")

    g3, g4, moments_source = _resolve_moments(skewness, kurtosis)

    def _degenerate() -> PsrResult:
        return PsrResult(
            psr=None,
            sharpe_ratio=sharpe_ratio,
            benchmark_sharpe=benchmark_sharpe,
            num_observations=num_observations,
            skewness=g3,
            kurtosis=g4,
            moments_source=moments_source,
            z_score=None,
            sampling=sampling,
        )

    if sharpe_ratio is None or num_observations < 2:
        return _degenerate()

    variance_term = 1.0 - g3 * sharpe_ratio + ((g4 - 1.0) / 4.0) * sharpe_ratio * sharpe_ratio
    if variance_term <= 0:
        return _degenerate()

    z_score = ((sharpe_ratio - benchmark_sharpe) * math.sqrt(num_observations - 1)) / math.sqrt(variance_term)
    return PsrResult(
        psr=NormalDist().cdf(z_score),
        sharpe_ratio=sharpe_ratio,
        benchmark_sharpe=benchmark_sharpe,
        num_observations=num_observations,
        skewness=g3,
        kurtosis=g4,
        moments_source=moments_source,
        z_score=z_score,
        sampling=sampling,
    )


def psr_from_equity_curve(
    equity_curve: Sequence[Decimal],
    *,
    bars_per_day: int,
    sampling: str = SAMPLING_DAILY,
    benchmark_sharpe: float = 0.0,
) -> PsrResult:
    """`evaluate_psr` driven straight off an equity curve, with **real
    measured moments** rather than the normal fallback.

    `sampling` defaults to `SAMPLING_DAILY`: the curve is resampled to one
    point per completed day (`resample_equity_to_daily`) before returns are
    taken, because per-bar returns inside a multi-bar holding period are
    autocorrelated and PSR assumes iid returns -- evaluating per-bar
    over-counts the evidence and makes PSR anti-conservative. Pass
    `sampling=SAMPLING_PER_BAR` for the raw per-bar path when that is
    genuinely what is wanted (e.g. comparing against a per-bar figure
    computed elsewhere); it is supported, just not the default.

    The Sharpe, the moments, and `T` all come from the same resampled
    series, so they cannot drift apart. `bars_per_day` is only used for the
    resampling itself -- nothing here is annualized.
    """
    if sampling not in _VALID_SAMPLINGS:
        raise ValueError(f"sampling must be one of {_VALID_SAMPLINGS}, got {sampling!r}")

    if sampling == SAMPLING_DAILY:
        series = resample_equity_to_daily(equity_curve, bars_per_day=bars_per_day)
    else:
        series = list(equity_curve)
    moments = compute_return_moments(series)
    returns = per_bar_returns(series)

    per_observation_sharpe: float | None = None
    if len(returns) >= 2:
        mean_return = fmean(returns)
        try:
            stdev_return = stdev(returns)
        except StatisticsError:
            stdev_return = 0.0
        if stdev_return > 0:
            per_observation_sharpe = mean_return / stdev_return

    return evaluate_psr(
        sharpe_ratio=per_observation_sharpe,
        num_observations=moments.num_returns,
        benchmark_sharpe=benchmark_sharpe,
        skewness=moments.skewness,
        kurtosis=moments.kurtosis,
        sampling=sampling,
    )


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014)
# ---------------------------------------------------------------------------


def sharpe_variance_across_trials(trial_sharpe_ratios: Sequence[float | None]) -> float | None:
    """`V_hat`: the sample variance of the estimated Sharpe ratios across
    the trials a strategy family actually ran. `None` folds are excluded
    (no evidence to include), matching `evaluate_mean_sharpe_significance`.

    `None` -- never `0.0` -- for fewer than 2 defined values: a sample
    variance needs at least two observations, and a fabricated zero would
    silently collapse `deflated_sharpe_benchmark` to "no selection bias at
    all", the most permissive possible answer.

    The trial Sharpes must be on the SAME per-observation scale as the
    `sharpe_ratio` eventually passed to `evaluate_deflated_sharpe` -- mixing
    an annualized `V_hat` with a per-observation `SR_hat` would inflate the
    benchmark by the annualization factor.
    """
    values = [float(s) for s in trial_sharpe_ratios if s is not None]
    if len(values) < 2:
        return None
    return stdev(values) ** 2


def deflated_sharpe_benchmark(*, trial_sharpe_variance: float | None, num_trials: int) -> float | None:
    """`SR0`: the Sharpe an entirely edge-less researcher would expect to
    reach as the *maximum* of `num_trials` independent trials whose Sharpe
    estimates have variance `trial_sharpe_variance` (Bailey & Lopez de
    Prado 2014):

        SR0 = sqrt(V_hat) * [ (1 - gamma)*Phi^-1(1 - 1/N)
                              + gamma*Phi^-1(1 - 1/(N*e)) ]

    This is the benchmark DSR deflates against: more trials, or more
    dispersed trials, mean a higher bar.

    `num_trials == 1` returns exactly `0.0`: the expected maximum of a
    single draw from a zero-mean distribution is 0, while the Gumbel-based
    approximation above is undefined there (`Phi^-1(1 - 1/1) = Phi^-1(0) =
    -inf`). So DSR at N=1 collapses to PSR against zero, which is exactly
    what it should mean -- no search, no selection bias to correct.

    `None` (not a fabricated 0.0) when `num_trials < 1`, or when
    `trial_sharpe_variance` is `None`/`0.0` for `num_trials >= 2`. A zero
    variance across 2+ trials means every trial produced the identical
    Sharpe estimate, which in practice signals a caller passing one value
    repeatedly rather than a real, informative trial set; the mathematical
    limit (SR0 = 0, i.e. DSR = PSR(0)) is available by calling
    `evaluate_psr` directly for any caller who genuinely wants it.

    Note the approximation is asymptotic in `N` and is known to be rough at
    very small counts (at N=2 it gives ~0.520*sqrt(V) against a true
    expected maximum of 1/sqrt(pi) ~= 0.564*sqrt(V)) -- it errs toward
    under-deflating there, which is the permissive direction, so a small-N
    DSR should not be read as a conservative number.
    """
    if num_trials < 1:
        return None
    if num_trials == 1:
        return 0.0
    if trial_sharpe_variance is None:
        return None
    if trial_sharpe_variance < 0:
        raise ValueError(f"trial_sharpe_variance must be non-negative, got {trial_sharpe_variance}")
    if trial_sharpe_variance == 0:
        return None

    normal = NormalDist()
    expected_max = (1.0 - EULER_MASCHERONI) * normal.inv_cdf(1.0 - 1.0 / num_trials) + (
        EULER_MASCHERONI * normal.inv_cdf(1.0 - 1.0 / (num_trials * math.e))
    )
    return math.sqrt(trial_sharpe_variance) * expected_max


@dataclass(frozen=True)
class DeflatedSharpeResult:
    """`dsr` is `PSR(SR0)` -- the probability the true Sharpe beats what
    `num_trials` trials of pure luck would have produced. `None` whenever
    `SR0` or the underlying PSR is undefined (see
    `deflated_sharpe_benchmark` and `PsrResult`).

    Deliberately has no `passed` field: DSR is a PROPOSED Eligibility Bar
    criterion pending human approval (see the module docstring), and a
    `passed` here would read as a gate that does not exist yet.
    """

    dsr: float | None
    benchmark_sharpe: float | None
    num_trials: int
    trial_sharpe_variance: float | None
    psr: PsrResult

    def to_dict(self) -> dict:
        return {
            "dsr": self.dsr,
            "benchmark_sharpe": self.benchmark_sharpe,
            "num_trials": self.num_trials,
            "trial_sharpe_variance": self.trial_sharpe_variance,
            "psr": self.psr.to_dict(),
        }


def evaluate_deflated_sharpe(
    *,
    sharpe_ratio: float | None,
    num_observations: int,
    num_trials: int,
    trial_sharpe_variance: float | None,
    skewness: float | None = None,
    kurtosis: float | None = None,
    sampling: str | None = None,
) -> DeflatedSharpeResult:
    """Deflated Sharpe Ratio: `PSR(SR0)`, i.e. `evaluate_psr` against the
    selection-bias-corrected benchmark `deflated_sharpe_benchmark` rather
    than against zero (Bailey & Lopez de Prado 2014).

    All Sharpe quantities -- `sharpe_ratio` and the trials behind
    `trial_sharpe_variance` alike -- are **per-observation**, not
    annualized, and must be on the same sampling frequency as each other
    and as `num_observations`.

    `num_trials` is taken as a plain parameter rather than derived here:
    counting a strategy family's real trials from `runs/experiments.jsonl`
    is a separate concern with its own subtleties (grid candidates vs.
    standalone runs, re-runs of an identical configuration), owned
    elsewhere. `num_trials=1` reduces this to `PSR(0)` exactly.

    When `SR0` is undefined, `dsr` and `benchmark_sharpe` are both `None`
    but the underlying `PSR(0)` is still reported on `.psr` -- a caller
    losing the deflation should not also lose the undeflated figure.
    """
    benchmark = deflated_sharpe_benchmark(trial_sharpe_variance=trial_sharpe_variance, num_trials=num_trials)
    psr = evaluate_psr(
        sharpe_ratio=sharpe_ratio,
        num_observations=num_observations,
        benchmark_sharpe=benchmark if benchmark is not None else 0.0,
        skewness=skewness,
        kurtosis=kurtosis,
        sampling=sampling,
    )
    return DeflatedSharpeResult(
        dsr=psr.psr if benchmark is not None else None,
        benchmark_sharpe=benchmark,
        num_trials=num_trials,
        trial_sharpe_variance=trial_sharpe_variance,
        psr=psr,
    )


# ---------------------------------------------------------------------------
# Combined evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityResult:
    fold_consistency: FoldConsistencyResult
    sign_test: SignTestResult
    sharpe_significance: MeanSharpeSignificanceResult
    passed: bool
    deflated_sharpe: DeflatedSharpeResult | None = None

    def to_dict(self) -> dict:
        record = {
            "fold_consistency": self.fold_consistency.to_dict(),
            "sign_test": self.sign_test.to_dict(),
            "sharpe_significance": self.sharpe_significance.to_dict(),
            "passed": self.passed,
        }
        # Present only when a caller actually supplied a DSR -- the same
        # "field only appears when the feature is used" convention
        # `research/walkforward.py` uses for `parameter_sensitivity` and
        # `funding_pnl_included`, so an existing reader sees a byte-for-byte
        # unchanged shape.
        if self.deflated_sharpe is not None:
            record["deflated_sharpe"] = self.deflated_sharpe.to_dict()
        return record


def evaluate_eligibility(
    fold_sharpe_values: Sequence[float | None],
    *,
    min_fold_consistency: Decimal,
    null_probability: float = DEFAULT_NULL_PROBABILITY,
    significance_alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
    deflated_sharpe: DeflatedSharpeResult | None = None,
) -> EligibilityResult:
    """CLAUDE.md's revised (2026-07-27) "fold consistency" + "aggregate
    significance" Eligibility Bar clauses, combined into one evaluation.

    `min_fold_consistency` is REQUIRED (no default) -- CLAUDE.md
    deliberately left the exact value within its approved 80-90% range to a
    human decision; this function does not silently pick a point value on
    the caller's behalf (see module docstring).

    `passed` is `True` iff ALL THREE checks pass: fold-consistency AND the
    sign test AND the Sharpe-significance test ("Both required, not
    either").

    `deflated_sharpe` (Strategy Research Task Q) is **reported and nothing
    more**: it is carried onto the result and into `to_dict()`, and has
    exactly zero influence on `passed`. CLAUDE.md's Eligibility Bar is
    human-approval-gated (same status as Risk Parameters), and DSR is a
    proposed replacement for the mean-Sharpe t-test clause that has not been
    approved -- silently letting it gate anything here would be this module
    unilaterally amending the bar. See the module docstring.

    This function does NOT evaluate the OTHER Eligibility Bar criteria
    (fold-count credibility floor, drawdown ceiling, minimum trade count,
    profit-factor floor) -- see module docstring for why those stay a
    caller's direct responsibility.
    """
    fold_consistency = evaluate_fold_consistency(fold_sharpe_values, min_fraction=min_fold_consistency)
    sign_test = evaluate_sign_test(fold_sharpe_values, null_probability=null_probability, alpha=significance_alpha)
    sharpe_significance = evaluate_mean_sharpe_significance(fold_sharpe_values, alpha=significance_alpha)
    passed = fold_consistency.passed and sign_test.passed and sharpe_significance.passed
    return EligibilityResult(
        fold_consistency=fold_consistency,
        sign_test=sign_test,
        sharpe_significance=sharpe_significance,
        passed=passed,
        deflated_sharpe=deflated_sharpe,
    )
