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
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import StatisticsError, fmean, stdev
from typing import Sequence

DEFAULT_NULL_PROBABILITY = 0.5
DEFAULT_SIGNIFICANCE_ALPHA = 0.05


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
# Combined evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityResult:
    fold_consistency: FoldConsistencyResult
    sign_test: SignTestResult
    sharpe_significance: MeanSharpeSignificanceResult
    passed: bool

    def to_dict(self) -> dict:
        return {
            "fold_consistency": self.fold_consistency.to_dict(),
            "sign_test": self.sign_test.to_dict(),
            "sharpe_significance": self.sharpe_significance.to_dict(),
            "passed": self.passed,
        }


def evaluate_eligibility(
    fold_sharpe_values: Sequence[float | None],
    *,
    min_fold_consistency: Decimal,
    null_probability: float = DEFAULT_NULL_PROBABILITY,
    significance_alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
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
    )
