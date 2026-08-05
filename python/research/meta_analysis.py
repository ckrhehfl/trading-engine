"""Retrospective statistical meta-analysis combining independent PSR
significance tests from disjoint samples testing the same null hypothesis
-- Strategy Research Task AC.

## Why this exists

`sr-v` (BingX BTC-USDT, 2021-05-14 through 2024-04-26, 1,079 daily bars,
`run_id=8143a525-3159-447b-991d-2f11a0ef790b`) and `sr-ab` (Binance spot
BTCUSDT, 2017-08-17 through 2021-05-13, 1,366 daily bars,
`run_id=a84d52ba-5f5d-43bd-a528-3d5cd494208a`) are two independent,
already-executed, already-logged pre-registered single-window holdout
confirmations of the exact same zero-fitted-parameter hypothesis
(`strategy_id="daily-tsmom-ensemble"`, the Moskowitz-Ooi-Pedersen 2012
daily time-series-momentum ensemble), evaluated against two disjoint,
non-overlapping calendar windows. Both are individually `INCONCLUSIVE`
(each missed a full PASS by narrow margins on different criteria -- see
`.planning/sr-ac-combined-holdout-meta-analysis.md` for the full
picture, including why the drawdown and trade-count gates cannot be
overridden by a strong combined significance figure alone).

Each run's own `psr.z_score` field (`research.eligibility.evaluate_psr`'s
`PsrResult.z_score`) is already a valid standard-normal test statistic
for the null "true Sharpe <= 0" -- computed once, honestly, on the
run's own resampled daily returns. Combining two such statistics from
independent samples testing the same null is exactly the textbook setup
for Stouffer's method (Stouffer et al. 1949, *The American Soldier*),
and the sample-size-weighted variant this module implements (weight
`sqrt(n_i)`) is the standard, published form for combining tests of
unequal sample size (Whitlock, M.C. 2005, "Combining probability from
independent tests: the weighted Z-method is superior to Fisher's
approach", *Journal of Evolutionary Biology* 18(5), 1368-1373).

## Formula

    Z_combined = sum(w_i * Z_i) / sqrt(sum(w_i^2))

`sample_size_weights` computes the recommended `w_i = sqrt(n_i)`
weighting from each sample's observation count. `combine_z_scores`
accepts any positive weight vector (or none, which defaults to equal
weighting `w_i = 1`, reducing the formula to the textbook unweighted
Stouffer's method) -- the weighting scheme is a caller choice, not
hardcoded, matching this project's convention elsewhere
(`research.eligibility.evaluate_fold_consistency`'s `min_fraction`) of
never silently picking a value on the caller's behalf.

`combined_probability` (`Phi(Z_combined)`, via `statistics.NormalDist`,
the same convention `research.eligibility.evaluate_psr`'s own `.psr`
field already uses) is the probability, under the combined evidence,
that the true Sharpe of the underlying hypothesis exceeds the benchmark
the input z-scores were each computed against (here, zero, since both
`sr-v`'s and `sr-ab`'s own `z_score` fields are PSR z-scores against a
zero benchmark). It is interpretable the same way a PSR is -- but it is
NOT a new PSR or DSR computation in its own right; it is a textbook
combination of two already-computed PSR z-scores, valid specifically
because a PSR z-score is already a standard normal statistic under its
own null.

## Validity requires the caller to have verified two things this module
## cannot check

1. Every input z-score tests the SAME null hypothesis.
2. The samples behind them are INDEPENDENT (no shared observations, no
   shared fitting, no data leakage between them).

`sr-v` and `sr-ab` satisfy both: same `strategy_id`, same
zero-fitted-parameter strategy code (byte-for-byte unmodified between
the two runs), and calendar-disjoint, different-venue price windows
that never overlap. This module trusts the caller on both counts --
exactly like `research.eligibility.evaluate_deflated_sharpe` trusts its
caller's `num_trials` (a separate concern, owned elsewhere, deliberately
not re-derived internally).

## Not a new trial, not a new pre-registration

This module recombines summary statistics from two trials that are each
ALREADY individually counted in
`research.overfitting_check.check_project_combination_count`'s
project-level `N`. Calling `combine_z_scores` reads no raw price data,
accesses no holdout, computes no new backtest, and registers no new
hypothesis under `configs/research/preregistrations/` -- it is pure,
free, already-available-data computation. See
`.planning/sr-ac-combined-holdout-meta-analysis.md` for the full
accounting of what this task is and is not.

## What this module deliberately does NOT do

It does not evaluate the max-drawdown or minimum-trade-count Eligibility
Bar gates for a combined series -- those depend on the real, chronologically
concatenated equity curve / trade list, not on summary z-scores, and this
project's single-access holdout discipline forbids re-loading either
`sr-v`'s or `sr-ab`'s underlying holdout data a further time just to
assemble one. See the planning doc's drawdown-bound proof for what CAN be
said about the combined max drawdown from already-logged summary
statistics alone (a provable lower bound, not the exact combined figure).
"""

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Sequence

# Default per-input weight when a caller supplies none -- equal weighting,
# which reduces `combine_z_scores` to the textbook unweighted Stouffer's
# method.
_DEFAULT_WEIGHT = 1.0


def sample_size_weights(sample_sizes: Sequence[int]) -> list[float]:
    """`sqrt(n_i)` for each sample size in `sample_sizes` -- the standard,
    published weighting for Stouffer's method when combining tests
    computed from independent samples of unequal size (Whitlock 2005; see
    module docstring). Pass the result straight through as
    `combine_z_scores(..., weights=sample_size_weights([n1, n2, ...]))`.

    Raises for an empty `sample_sizes`, or any non-positive entry: a zero
    or negative observation count has no meaningful square-root weight and
    signals a caller mistake (e.g. passing a z-score or a Sharpe by
    accident), not a valid degenerate input to pass through silently.
    """
    if not sample_sizes:
        raise ValueError("sample_sizes must be non-empty")
    for n in sample_sizes:
        if n <= 0:
            raise ValueError(f"each sample size must be positive, got {n}")
    return [math.sqrt(n) for n in sample_sizes]


@dataclass(frozen=True)
class StoufferCombinationResult:
    """`combined_z`: Stouffer's combined Z-statistic. `combined_probability`:
    `Phi(combined_z)`, the one-sided probability the combined evidence
    assigns to "true effect exceeds the benchmark the inputs were computed
    against" -- read the same way a PSR is (see module docstring), but
    only ever as strong as the two conditions listed in the module
    docstring ("same null", "independent samples") actually hold for the
    caller's own inputs; this result carries no evidence either way about
    whether they do.

    `z_scores`/`weights` are the resolved inputs, carried through
    unchanged (including the resolved default-weight vector when the
    caller passed `weights=None`) so a reader of a logged/reported result
    never has to re-derive what was actually combined.
    """

    combined_z: float
    combined_probability: float
    z_scores: tuple[float, ...]
    weights: tuple[float, ...]

    def to_dict(self) -> dict:
        return {
            "combined_z": self.combined_z,
            "combined_probability": self.combined_probability,
            "z_scores": list(self.z_scores),
            "weights": list(self.weights),
        }


def combine_z_scores(
    z_scores: Sequence[float],
    *,
    weights: Sequence[float] | None = None,
) -> StoufferCombinationResult:
    """Stouffer's (weighted) Z-score method:

        Z_combined = sum(w_i * Z_i) / sqrt(sum(w_i^2))

    Each `z_scores[i]` must be a standard-normal test statistic for the
    SAME null hypothesis, computed on samples INDEPENDENT of each other --
    see the module docstring's "Validity requires..." section; this
    function has no way to verify either condition and trusts the caller.

    `weights` defaults to `None`, which resolves to equal weighting
    (`1.0` for every input) -- the textbook unweighted Stouffer's method.
    Pass `sample_size_weights(...)` for the sample-size-weighted variant
    recommended when combining tests computed from differently sized
    independent samples (Whitlock 2005).

    A degenerate single-input call (`len(z_scores) == 1`) is a
    mathematical no-op regardless of the weight supplied:
    `w*z / sqrt(w^2) == z` for any positive `w` -- "combining one test
    with itself" must return that test's own z-score unchanged, and this
    function's test suite verifies exactly that identity rather than just
    asserting it runs.

    Raises for an empty `z_scores`, a `weights` of different length, a
    non-finite (`NaN`/`inf`/`-inf`) z-score, or a weight that is
    non-positive or non-finite (the same validation `sample_size_weights`
    applies to its own output, extended to also reject `NaN`/`inf`): a
    non-finite input would silently propagate into a non-finite or
    spuriously "maximal-confidence" `combined_z`/`combined_probability`
    rather than fail loudly, and is virtually always a caller mistake
    (e.g. a `NaN` z-score from an upstream degenerate PSR computation)
    rather than a valid input to combine.

    Individually finite, individually valid weights can still combine
    badly at extreme magnitude: squaring a weight around `1e200` or
    larger overflows `float`'s ~1.8e308 max (`(1e200)**2 == 1e400`,
    which Python evaluates to `inf`), which would silently collapse
    `combined_z` to `0.0` -- a WRONG answer, not a raised error and not
    the mathematically correct one (the dominant weight's own z-score
    should dominate the result, not vanish from it). Guarded against by
    normalizing `resolved_weights` by their own maximum before summing:
    `Z_combined` is exactly invariant under uniform positive rescaling of
    every weight by the same positive constant (both the numerator and
    `sqrt(sum(w^2))` denominator scale by that same constant, so the
    ratio is unchanged), so this changes nothing mathematically while
    keeping every squared term within float range for any realistic
    caller input. The two weighted sums additionally use `math.fsum`
    (Neumaier/Shewchuk summation) rather than the builtin `sum`, for the
    same reason this project already prefers `Decimal`/exact arithmetic
    where practical -- lower accumulated floating-point error, at no
    real cost for the small input sizes this function is ever called
    with.
    """
    if not z_scores:
        raise ValueError("z_scores must be non-empty")

    if weights is None:
        resolved_weights = [_DEFAULT_WEIGHT] * len(z_scores)
    else:
        resolved_weights = list(weights)
        if len(resolved_weights) != len(z_scores):
            raise ValueError(
                f"weights must be the same length as z_scores "
                f"({len(resolved_weights)} != {len(z_scores)})"
            )
    for z_score in z_scores:
        if not math.isfinite(z_score):
            raise ValueError(f"each z_score must be finite, got {z_score}")
    for weight in resolved_weights:
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"each weight must be finite and positive, got {weight}")

    # Normalize by the largest weight before squaring/summing -- see the
    # overflow discussion above. `resolved_weights` (the caller-facing,
    # UN-normalized values) is still what gets reported on the result
    # below, so this scaling is purely an internal computation detail.
    max_weight = max(resolved_weights)
    scaled_weights = [w / max_weight for w in resolved_weights]

    numerator = math.fsum(w * z for w, z in zip(scaled_weights, z_scores, strict=True))
    denominator = math.sqrt(math.fsum(w * w for w in scaled_weights))
    combined_z = numerator / denominator
    combined_probability = NormalDist().cdf(combined_z)

    return StoufferCombinationResult(
        combined_z=combined_z,
        combined_probability=combined_probability,
        z_scores=tuple(z_scores),
        weights=tuple(resolved_weights),
    )
