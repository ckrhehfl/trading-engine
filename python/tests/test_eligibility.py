"""Tests for `python/research/eligibility.py` -- Strategy Research Task K's
reusable Backtest/Walk-Forward Eligibility Bar evaluation utility,
implementing CLAUDE.md's Eligibility Bar's revised (2026-07-27) "fold
consistency" + "aggregate significance" clauses. See
`.planning/sr-j-fold-diagnosis-and-eligibility-review.md` (the revision's
own derivation) and `.planning/sr-k-mean-reversion-and-blend.md`.

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/eligibility.py` did.
"""

import math
from decimal import Decimal
from statistics import NormalDist, fmean, stdev

import pytest

from research.eligibility import (
    DEFAULT_NULL_PROBABILITY,
    DEFAULT_SIGNIFICANCE_ALPHA,
    EULER_MASCHERONI,
    MOMENTS_SOURCE_NORMAL,
    MOMENTS_SOURCE_OBSERVED,
    SAMPLING_DAILY,
    SAMPLING_PER_BAR,
    binomial_sign_test_p_value,
    deannualize_sharpe,
    deflated_sharpe_benchmark,
    evaluate_deflated_sharpe,
    evaluate_eligibility,
    evaluate_fold_consistency,
    evaluate_mean_sharpe_significance,
    evaluate_psr,
    evaluate_sign_test,
    psr_from_equity_curve,
    resample_equity_to_daily,
    sharpe_variance_across_trials,
)

# ---------------------------------------------------------------------------
# Fold consistency
# ---------------------------------------------------------------------------


class TestEvaluateFoldConsistency:
    def test_rejects_non_positive_or_over_one_min_fraction(self):
        with pytest.raises(ValueError, match="min_fraction"):
            evaluate_fold_consistency([1.0], min_fraction=Decimal("0"))
        with pytest.raises(ValueError, match="min_fraction"):
            evaluate_fold_consistency([1.0], min_fraction=Decimal("1.5"))

    def test_exact_11_of_19_matches_configuration_c(self):
        # sr-i's Configuration C: 11/19 folds positive == 57.9%.
        values = [1.0] * 11 + [-1.0] * 8
        result = evaluate_fold_consistency(values, min_fraction=Decimal("0.85"))
        assert result.num_positive == 11
        assert result.num_folds == 19
        assert result.fraction_positive == pytest.approx(11 / 19)
        assert result.passed is False  # 11/19 = 57.9% < 85%

    def test_16_of_19_passes_an_80_percent_floor(self):
        values = [1.0] * 16 + [-1.0] * 3
        result = evaluate_fold_consistency(values, min_fraction=Decimal("0.80"))
        assert result.passed is True

    def test_16_of_19_fails_a_90_percent_floor(self):
        values = [1.0] * 16 + [-1.0] * 3
        result = evaluate_fold_consistency(values, min_fraction=Decimal("0.90"))
        assert result.passed is False

    def test_none_folds_count_as_not_positive(self):
        # Same convention as research.walkforward._aggregate_metrics'
        # all_folds_positive_sharpe: a None (zero-trade/zero-variance) fold
        # is "no evidence", which fails the positive check, not skipped.
        values = [1.0, 1.0, None]
        result = evaluate_fold_consistency(values, min_fraction=Decimal("0.5"))
        assert result.num_positive == 2
        assert result.num_folds == 3
        assert result.passed is True  # 2/3 = 66.7% >= 50%

    def test_zero_sharpe_is_not_positive(self):
        values = [0.0, 1.0]
        result = evaluate_fold_consistency(values, min_fraction=Decimal("0.9"))
        assert result.num_positive == 1

    def test_empty_folds_never_passes(self):
        result = evaluate_fold_consistency([], min_fraction=Decimal("0.5"))
        assert result.num_folds == 0
        assert result.fraction_positive is None
        assert result.passed is False


# ---------------------------------------------------------------------------
# Binomial sign test -- reproduces sr-j's own worked examples exactly
# ---------------------------------------------------------------------------


class TestBinomialSignTestPValue:
    """Every expected value below is transcribed directly from
    `.planning/sr-j-fold-diagnosis-and-eligibility-review.md`'s own real,
    already-published `math.comb`-computed reference tables -- not
    fabricated for this test.
    """

    def test_p_at_least_11_of_19_at_null_p_half_matches_configuration_c(self):
        # sr-j: "P(X >= 11 | n=19, p=0.5) = 32.38%" -- Configuration C's own
        # real sign-test result.
        p = binomial_sign_test_p_value(11, 19, null_probability=0.5)
        assert p == pytest.approx(0.32380, abs=1e-4)

    def test_p_at_least_8_of_19_at_null_p_half_matches_original_ensemble(self):
        # sr-j: "sign-test P(X >= 8 | n=19, p=0.5) = 82.04%" (original,
        # pre-refinement ensemble).
        p = binomial_sign_test_p_value(8, 19, null_probability=0.5)
        assert p == pytest.approx(0.8204, abs=1e-4)

    def test_p_all_19_of_19_at_true_p_080(self):
        # sr-j's reference table: "0.80 | 1.4412%".
        p = binomial_sign_test_p_value(19, 19, null_probability=0.80)
        assert p == pytest.approx(0.014412, abs=1e-6)

    def test_power_at_16_of_19_floor_true_p_080(self):
        # sr-j's proposed-revision table: ">=80% | 16/19 | 45.51%".
        p = binomial_sign_test_p_value(16, 19, null_probability=0.80)
        assert p == pytest.approx(0.4551, abs=1e-4)

    def test_power_at_17_of_19_floor_true_p_080(self):
        # sr-j: ">=85% | 17/19 | 23.69%".
        p = binomial_sign_test_p_value(17, 19, null_probability=0.80)
        assert p == pytest.approx(0.2369, abs=1e-4)

    def test_power_at_18_of_19_floor_true_p_080(self):
        # sr-j: ">=90% | 18/19 | 8.29%".
        p = binomial_sign_test_p_value(18, 19, null_probability=0.80)
        assert p == pytest.approx(0.0829, abs=1e-4)

    def test_power_at_16_of_19_floor_true_p_090(self):
        # sr-j: ">=80% | 16/19 | ... | 88.50%".
        p = binomial_sign_test_p_value(16, 19, null_probability=0.90)
        assert p == pytest.approx(0.8850, abs=1e-4)

    def test_iid_sign_null_reference_rate_at_16_of_19(self):
        # sr-j: ">=80% | 16/19 | ... | 0.221%" (the p=0.50 reference column).
        p = binomial_sign_test_p_value(16, 19, null_probability=0.50)
        assert p == pytest.approx(0.00221, abs=1e-5)

    def test_rejects_out_of_range_num_positive(self):
        with pytest.raises(ValueError):
            binomial_sign_test_p_value(-1, 19)
        with pytest.raises(ValueError):
            binomial_sign_test_p_value(20, 19)

    def test_zero_folds_edge_case_via_evaluate_sign_test(self):
        result = evaluate_sign_test([])
        assert result.num_folds == 0
        assert result.passed is False


class TestEvaluateSignTest:
    def test_configuration_c_does_not_reject_no_edge_null(self):
        values = [1.0] * 11 + [-1.0] * 8
        result = evaluate_sign_test(values, alpha=0.05)
        assert result.p_value == pytest.approx(0.32380, abs=1e-4)
        assert result.passed is False

    def test_strong_result_rejects_no_edge_null(self):
        values = [1.0] * 18 + [-1.0] * 1
        result = evaluate_sign_test(values, alpha=0.05)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Mean-Sharpe significance -- exact incomplete-beta-based t-test
# ---------------------------------------------------------------------------


class TestMeanSharpeSignificanceDegenerateCases:
    def test_fewer_than_two_values_yields_none_not_a_crash(self):
        result = evaluate_mean_sharpe_significance([1.0])
        assert result.p_value is None
        assert result.passed is False

    def test_zero_variance_yields_none_not_a_crash(self):
        result = evaluate_mean_sharpe_significance([2.0, 2.0, 2.0])
        assert result.p_value is None
        assert result.passed is False

    def test_none_folds_are_excluded_from_the_sample(self):
        # Matches research.walkforward._aggregate_metrics' mean_sharpe
        # convention: None folds excluded (no evidence to average in), not
        # treated as zero.
        result = evaluate_mean_sharpe_significance([1.0, 2.0, None, 3.0])
        assert result.n == 3


class TestIncompleteBetaTDistributionExactness:
    """Independent, non-fabricated verification of the regularized-
    incomplete-beta-based t-distribution p-value, via three separate exact/
    reference methods -- see `research/eligibility.py`'s
    `_regularized_incomplete_beta` docstring.
    """

    def test_cauchy_exact_value_at_df_1(self):
        # At df=1 the t-distribution IS the standard Cauchy distribution:
        # P(|T| >= 1) = 1 - 2*atan(1)/pi = 0.5 exactly (closed form,
        # independently computable via math.atan, not via the incomplete
        # beta machinery under test).
        from research.eligibility import _t_distribution_two_sided_p_value

        p = _t_distribution_two_sided_p_value(1.0, 1)
        expected = 1.0 - 2.0 * math.atan(1.0) / math.pi
        assert p == pytest.approx(expected, abs=1e-9)
        assert p == pytest.approx(0.5, abs=1e-9)

    def test_cauchy_exact_value_at_t_equals_sqrt3_df_1(self):
        # P(|T| >= sqrt(3)) at df=1 = 1 - 2*atan(sqrt(3))/pi = 1/3 exactly
        # (atan(sqrt(3)) = pi/3).
        from research.eligibility import _t_distribution_two_sided_p_value

        p = _t_distribution_two_sided_p_value(math.sqrt(3), 1)
        assert p == pytest.approx(1.0 / 3.0, abs=1e-9)

    def test_large_df_converges_to_standard_normal_via_erf(self):
        # As df -> infinity, Student's t -> standard normal. Independent
        # cross-check via math.erf (not the incomplete-beta code path).
        from research.eligibility import _t_distribution_two_sided_p_value

        t = 1.96
        p = _t_distribution_two_sided_p_value(t, 100_000)
        normal_p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(t / math.sqrt(2))))
        assert p == pytest.approx(normal_p, abs=1e-4)
        assert p == pytest.approx(0.05, abs=1e-3)

    def test_textbook_critical_value_df_18_two_sided_5_percent(self):
        # Well-known t-table entry, also independently cited in this
        # project's own sr-j: "~2.10 critical value a two-sided 5% test at
        # 18 degrees of freedom would need".
        from research.eligibility import _t_distribution_two_sided_p_value

        p = _t_distribution_two_sided_p_value(2.101, 18)
        assert p == pytest.approx(0.05, abs=1e-3)

    def test_textbook_critical_value_df_18_two_sided_1_percent(self):
        from research.eligibility import _t_distribution_two_sided_p_value

        p = _t_distribution_two_sided_p_value(2.878, 18)
        assert p == pytest.approx(0.01, abs=1e-3)


class TestMeanSharpeSignificanceRealFixture:
    """The original (pre-refinement) ensemble's real 19 per-fold Sharpe
    values, transcribed exactly from `.planning/sr-i-ensemble-refinement.md`
    ("Re-sorting the original ensemble's 19 real per-fold Sharpe values"):
    a real, already-published, non-fabricated fixture. sr-j independently
    reports this sample's mean=-1.347, sample stdev=3.600, t=-1.631 -- this
    test reproduces the mean/stdev/t-statistic from the raw values.
    """

    _ORIGINAL_ENSEMBLE_SHARPE_VALUES = [
        -7.61,
        -5.73,
        -4.88,
        -4.17,
        -4.04,
        -4.03,
        -3.88,
        -3.81,
        -3.73,
        -1.23,
        -1.22,
        0.63,
        0.82,
        1.41,
        1.7,
        3.05,
        3.47,
        3.61,
        4.05,
    ]

    def test_reproduces_sr_j_mean_stdev_and_t_statistic(self):
        result = evaluate_mean_sharpe_significance(self._ORIGINAL_ENSEMBLE_SHARPE_VALUES)
        assert result.n == 19
        assert result.mean == pytest.approx(-1.347, abs=5e-3)
        assert result.stdev == pytest.approx(3.600, abs=5e-3)
        assert result.t_statistic == pytest.approx(-1.631, abs=5e-3)
        assert result.degrees_of_freedom == 18
        # A negative t-statistic (one-sided "mean > 0" test) can never
        # reject the null -- p-value must be large (not significant).
        assert result.p_value > 0.5
        assert result.passed is False

    def test_configuration_c_like_positive_but_insignificant_mean(self):
        # sr-j: Configuration C mean=+0.027, sample stdev=4.318, t=0.0274 --
        # engineered synthetic 19-value sample matching that exact
        # mean/stdev (not the raw per-fold values, which sr-j's own
        # document does not list individually for Configuration C).
        # Construct 19 values with an exact chosen mean and sample stdev:
        # 18 values at -x and 1 value at +18x + mean_offset is fiddly;
        # simpler: use a symmetric +-spread around the target mean.
        base_mean = 0.027
        spread = 4.318
        values = [base_mean + spread, base_mean - spread] * 9 + [base_mean]
        result = evaluate_mean_sharpe_significance(values)
        assert result.mean == pytest.approx(base_mean, abs=1e-6)
        assert result.t_statistic == pytest.approx(0.0274, abs=0.05)
        assert result.passed is False  # nowhere near p<0.05


# ---------------------------------------------------------------------------
# Combined evaluate_eligibility
# ---------------------------------------------------------------------------


class TestEvaluateEligibility:
    def test_requires_min_fold_consistency_keyword(self):
        with pytest.raises(TypeError):
            evaluate_eligibility([1.0, 2.0])  # type: ignore[call-arg]

    def test_configuration_c_fails_the_revised_bar_on_every_sub_check(self):
        # sr-j's own conclusion: "Neither this project's original ensemble
        # nor its most-refined Configuration C would pass a revised bar
        # that required genuine statistical significance."
        values = [1.0] * 11 + [-1.0] * 8
        result = evaluate_eligibility(values, min_fold_consistency=Decimal("0.85"))
        assert result.fold_consistency.passed is False
        assert result.sign_test.passed is False
        assert result.passed is False

    def test_a_strong_clean_result_passes_all_three_checks(self):
        values = [2.0] * 18 + [1.5]
        result = evaluate_eligibility(values, min_fold_consistency=Decimal("0.80"))
        assert result.fold_consistency.passed is True
        assert result.sign_test.passed is True
        assert result.sharpe_significance.passed is True
        assert result.passed is True

    def test_to_dict_round_trips_basic_shape(self):
        values = [1.0] * 11 + [-1.0] * 8
        result = evaluate_eligibility(values, min_fold_consistency=Decimal("0.85"))
        d = result.to_dict()
        assert d["passed"] is False
        assert "fold_consistency" in d
        assert "sign_test" in d
        assert "sharpe_significance" in d

    def test_default_constants_are_sane(self):
        assert DEFAULT_NULL_PROBABILITY == 0.5
        assert DEFAULT_SIGNIFICANCE_ALPHA == 0.05


# ---------------------------------------------------------------------------
# Probabilistic / Deflated Sharpe Ratio -- Strategy Research Task Q.
#
# Written first (TDD): every test below failed on `ImportError` before
# `research/eligibility.py` grew the PSR/DSR machinery.
# ---------------------------------------------------------------------------


# Configuration C's ("ensemble-momentum-configuration-c", strategy_version
# "task-n-with-funding") real 19 per-fold annualized Sharpe values -- the
# real BingX 1h walk-forward run behind CLAUDE.md's "Current best result"
# figures. Transcribed as literals, deliberately NOT read back from
# `runs/experiments.jsonl` at test time: that log is gitignored, is absent
# from a fresh clone and from CI, and a test reaching into the real research
# audit trail would be a far worse idea than a static fixture. Rounded to
# the 2 decimals CLAUDE.md itself publishes, these are
# [4.32, 2.55, 4.76, 2.18, -2.18, -2.06, -6.71, 5.06, 1.34, 0.52, -7.03,
# -0.75, -3.97, 3.1, 4.55, 5.33, -2.29, -8.38, 0.42].
_CONFIGURATION_C_FOLD_SHARPES = [
    4.318955921518776,
    2.5466239211716615,
    4.757810238881071,
    2.1794433506823765,
    -2.1769986084503503,
    -2.057064070912302,
    -6.710983378150571,
    5.057374582568075,
    1.3370653264845198,
    0.5176700308573401,
    -7.031912720217879,
    -0.7506955630826128,
    -3.9688402254868325,
    3.1034485418250273,
    4.546943155561598,
    5.330381533417626,
    -2.291398547072899,
    -8.382325941886227,
    0.41674613014693085,
]
# `validate_bars` (720) x `fold_count` (19) for that same real run -- the
# bar count behind its aggregate Sharpe. 1h bars, so 24 bars/day.
_CONFIGURATION_C_NUM_BARS = 720 * 19
_CONFIGURATION_C_BARS_PER_DAY = 24


def _synthetic_equity_curve(num_bars: int, *, start: str = "10000") -> list[Decimal]:
    """A deterministic (no RNG, no real market data) but non-degenerate
    equity curve: per-bar returns oscillate via `sin` around a small
    positive drift, so the return series has real non-zero variance,
    skewness and kurtosis without depending on a random seed.
    """
    equity = Decimal(start)
    curve = [equity]
    for i in range(1, num_bars):
        step = Decimal(str(1 + 0.004 * math.sin(i) + 0.0001))
        equity = equity * step
        curve.append(equity)
    return curve


class TestResampleEquityToDaily:
    def test_3600_hourly_bars_become_150_daily_points(self):
        curve = [Decimal(i) for i in range(3600)]
        assert len(resample_equity_to_daily(curve, bars_per_day=24)) == 150

    def test_only_completed_days_are_kept(self):
        # 3610 bars = 150 complete days + a 10-bar partial day. The partial
        # day is dropped entirely (its last observed equity is not a day's
        # close), so the result is identical to the 3600-bar case.
        curve = [Decimal(i) for i in range(3610)]
        daily = resample_equity_to_daily(curve, bars_per_day=24)
        assert len(daily) == 150
        assert daily[-1] == Decimal(3599)

    def test_each_point_is_that_days_final_bar(self):
        curve = [Decimal(i) for i in range(96)]
        daily = resample_equity_to_daily(curve, bars_per_day=24)
        assert daily == [Decimal(23), Decimal(47), Decimal(71), Decimal(95)]

    def test_fewer_bars_than_one_full_day_yields_nothing(self):
        assert resample_equity_to_daily([Decimal(i) for i in range(23)], bars_per_day=24) == []

    def test_empty_curve_yields_empty(self):
        assert resample_equity_to_daily([], bars_per_day=24) == []

    def test_rejects_non_positive_bars_per_day(self):
        with pytest.raises(ValueError, match="bars_per_day"):
            resample_equity_to_daily([Decimal(1)], bars_per_day=0)


class TestDeannualizeSharpe:
    def test_daily_sampling_divides_by_sqrt_365(self):
        assert deannualize_sharpe(2.0, bars_per_day=24, sampling=SAMPLING_DAILY) == pytest.approx(
            2.0 / math.sqrt(365)
        )

    def test_per_bar_sampling_divides_by_sqrt_bars_per_day_times_365(self):
        assert deannualize_sharpe(2.0, bars_per_day=24, sampling=SAMPLING_PER_BAR) == pytest.approx(
            2.0 / math.sqrt(24 * 365)
        )

    def test_daily_is_the_default_sampling(self):
        assert deannualize_sharpe(2.0, bars_per_day=24) == deannualize_sharpe(
            2.0, bars_per_day=24, sampling=SAMPLING_DAILY
        )

    def test_rejects_unknown_sampling(self):
        with pytest.raises(ValueError, match="sampling"):
            deannualize_sharpe(1.0, bars_per_day=24, sampling="weekly")

    def test_rejects_non_positive_bars_per_day(self):
        with pytest.raises(ValueError, match="bars_per_day"):
            deannualize_sharpe(1.0, bars_per_day=0, sampling=SAMPLING_PER_BAR)


class TestEvaluatePsr:
    def test_observed_sharpe_equal_to_benchmark_is_exactly_one_half(self):
        # SR_hat == SR* => numerator 0 => Phi(0) = 0.5 exactly, whatever T
        # and the moments are. An analytic check needing no reference
        # implementation at all.
        result = evaluate_psr(sharpe_ratio=0.3, num_observations=500, benchmark_sharpe=0.3)
        assert result.psr == pytest.approx(0.5, abs=1e-12)
        assert result.z_score == pytest.approx(0.0, abs=1e-12)

    def test_known_value_with_a_unit_denominator(self):
        # skewness=0 and kurtosis=1 (the mathematical minimum for a raw
        # fourth standardized moment) make the estimator's variance term
        # exactly 1 - 0*SR + ((1-1)/4)*SR^2 = 1, so z = SR_hat*sqrt(T-1).
        # SR_hat=0.5, T=17 => z = 0.5*4 = 2.0 => PSR = Phi(2), a standard
        # normal value independently available from stdlib.
        result = evaluate_psr(sharpe_ratio=0.5, num_observations=17, skewness=0.0, kurtosis=1.0)
        assert result.z_score == pytest.approx(2.0, abs=1e-12)
        assert result.psr == pytest.approx(NormalDist().cdf(2.0), abs=1e-12)

    def test_configuration_c_verification_anchor(self):
        # THE verification anchor for this module (see
        # `.planning/sr-q-deflated-sharpe.md`): Configuration C's real
        # aggregate result, de-annualized to a per-bar Sharpe, at
        # T = validate_bars * fold_count = 13,680, under the normal-moment
        # fallback. Independently cross-checked against this module's
        # pre-existing and entirely separate one-sample t-test:
        # evaluate_mean_sharpe_significance reports a one-sided p of ~0.484
        # on the same folds, i.e. ~0.516 "better than chance" -- within
        # 0.004 of the PSR below, despite sharing no code path.
        per_bar = deannualize_sharpe(
            fmean(_CONFIGURATION_C_FOLD_SHARPES),
            bars_per_day=_CONFIGURATION_C_BARS_PER_DAY,
            sampling=SAMPLING_PER_BAR,
        )
        result = evaluate_psr(sharpe_ratio=per_bar, num_observations=_CONFIGURATION_C_NUM_BARS)
        assert result.psr == pytest.approx(0.5194, abs=1e-4)
        assert result.moments_source == MOMENTS_SOURCE_NORMAL

    def test_configuration_c_psr_agrees_with_the_independent_t_test(self):
        per_bar = deannualize_sharpe(
            fmean(_CONFIGURATION_C_FOLD_SHARPES),
            bars_per_day=_CONFIGURATION_C_BARS_PER_DAY,
            sampling=SAMPLING_PER_BAR,
        )
        psr = evaluate_psr(sharpe_ratio=per_bar, num_observations=_CONFIGURATION_C_NUM_BARS)
        t_test = evaluate_mean_sharpe_significance(_CONFIGURATION_C_FOLD_SHARPES)
        # The t-test reports P(no edge); 1 - p is its "better than chance"
        # counterpart, the quantity PSR reports directly.
        assert psr.psr == pytest.approx(1 - t_test.p_value, abs=0.01)

    def test_normal_assumption_is_recorded_not_silent(self):
        fallback = evaluate_psr(sharpe_ratio=0.2, num_observations=500)
        assert fallback.moments_source == MOMENTS_SOURCE_NORMAL
        assert fallback.skewness == 0.0
        assert fallback.kurtosis == 3.0

    def test_explicitly_normal_moments_are_recorded_as_observed(self):
        # Numerically identical to the fallback, but provenance differs --
        # "we measured 0/3" is a different claim from "we assumed 0/3".
        observed = evaluate_psr(sharpe_ratio=0.2, num_observations=500, skewness=0.0, kurtosis=3.0)
        fallback = evaluate_psr(sharpe_ratio=0.2, num_observations=500)
        assert observed.psr == pytest.approx(fallback.psr)
        assert observed.moments_source == MOMENTS_SOURCE_OBSERVED
        assert fallback.moments_source == MOMENTS_SOURCE_NORMAL

    def test_negative_skew_lowers_psr_for_a_positive_sharpe(self):
        # -gamma3*SR_hat enlarges the estimator's standard error when skew
        # is negative and SR_hat > 0: fat left tails mean less confidence.
        neutral = evaluate_psr(sharpe_ratio=0.2, num_observations=500, skewness=0.0, kurtosis=3.0)
        left_tailed = evaluate_psr(sharpe_ratio=0.2, num_observations=500, skewness=-1.5, kurtosis=3.0)
        assert left_tailed.psr < neutral.psr

    def test_fat_tails_lower_psr_for_a_positive_sharpe(self):
        thin = evaluate_psr(sharpe_ratio=0.2, num_observations=500, skewness=0.0, kurtosis=3.0)
        fat = evaluate_psr(sharpe_ratio=0.2, num_observations=500, skewness=0.0, kurtosis=13.0)
        assert fat.psr < thin.psr

    def test_psr_increases_with_observed_sharpe_and_decreases_with_benchmark(self):
        low = evaluate_psr(sharpe_ratio=0.1, num_observations=500)
        high = evaluate_psr(sharpe_ratio=0.3, num_observations=500)
        deflated = evaluate_psr(sharpe_ratio=0.3, num_observations=500, benchmark_sharpe=0.2)
        assert low.psr < high.psr
        assert deflated.psr < high.psr

    def test_degenerate_observation_counts_yield_none_not_a_crash(self):
        for num_observations in (0, 1):
            result = evaluate_psr(sharpe_ratio=0.5, num_observations=num_observations)
            assert result.psr is None
            assert result.z_score is None

    def test_rejects_a_negative_observation_count(self):
        with pytest.raises(ValueError, match="num_observations"):
            evaluate_psr(sharpe_ratio=0.5, num_observations=-1)

    def test_rejects_impossible_kurtosis(self):
        # A raw (non-excess) fourth standardized moment is >= 1 for every
        # real distribution; below that is a caller error, not degenerate
        # data.
        with pytest.raises(ValueError, match="kurtosis"):
            evaluate_psr(sharpe_ratio=0.5, num_observations=500, skewness=0.0, kurtosis=0.5)

    def test_rejects_a_half_specified_moment_pair(self):
        with pytest.raises(ValueError, match="skewness"):
            evaluate_psr(sharpe_ratio=0.5, num_observations=500, skewness=-0.1)
        with pytest.raises(ValueError, match="kurtosis"):
            evaluate_psr(sharpe_ratio=0.5, num_observations=500, kurtosis=12.0)

    def test_non_positive_estimator_variance_yields_none(self):
        # Extreme, mutually inconsistent moments can drive
        # 1 - g3*SR + ((g4-1)/4)*SR^2 to <= 0. "No evidence", not a
        # math-domain crash.
        result = evaluate_psr(sharpe_ratio=10.0, num_observations=500, skewness=50.0, kurtosis=1.0)
        assert result.psr is None
        assert result.z_score is None

    def test_to_dict_exposes_every_reported_field(self):
        d = evaluate_psr(sharpe_ratio=0.2, num_observations=500).to_dict()
        for key in (
            "psr",
            "sharpe_ratio",
            "benchmark_sharpe",
            "num_observations",
            "skewness",
            "kurtosis",
            "moments_source",
            "z_score",
            "sampling",
        ):
            assert key in d


class TestPsrFromEquityCurve:
    def test_daily_is_the_default_and_is_recorded(self):
        curve = _synthetic_equity_curve(480)  # 20 days of 1h bars
        result = psr_from_equity_curve(curve, bars_per_day=24)
        assert result.sampling == SAMPLING_DAILY
        assert result.num_observations == 19  # 20 daily points -> 19 returns
        assert result.moments_source == MOMENTS_SOURCE_OBSERVED
        assert result.psr is not None

    def test_per_bar_path_is_available_and_uses_every_bar(self):
        curve = _synthetic_equity_curve(480)
        result = psr_from_equity_curve(curve, bars_per_day=24, sampling=SAMPLING_PER_BAR)
        assert result.sampling == SAMPLING_PER_BAR
        assert result.num_observations == 479
        assert result.psr is not None

    def test_daily_resampling_counts_far_fewer_observations(self):
        # The whole point of defaulting to daily: per-bar returns inside a
        # multi-hour holding period are autocorrelated, which inflates the
        # effective observation count and makes PSR anti-conservative.
        curve = _synthetic_equity_curve(480)
        daily = psr_from_equity_curve(curve, bars_per_day=24)
        per_bar = psr_from_equity_curve(curve, bars_per_day=24, sampling=SAMPLING_PER_BAR)
        assert daily.num_observations < per_bar.num_observations

    def test_too_short_a_curve_yields_none_not_a_crash(self):
        result = psr_from_equity_curve(_synthetic_equity_curve(20), bars_per_day=24)
        assert result.psr is None
        assert result.num_observations == 0

    def test_flat_curve_has_no_measurable_sharpe(self):
        result = psr_from_equity_curve([Decimal("10000")] * 480, bars_per_day=24)
        assert result.psr is None
        assert result.sharpe_ratio is None

    def test_rejects_bad_arguments_on_either_sampling_path(self):
        curve = _synthetic_equity_curve(480)
        with pytest.raises(ValueError, match="sampling"):
            psr_from_equity_curve(curve, bars_per_day=24, sampling="weekly")
        # bars_per_day is unused by the per-bar path, but a non-positive
        # value is a caller bug either way -- it must not fail loudly on one
        # path and pass silently on the other.
        for sampling in (SAMPLING_DAILY, SAMPLING_PER_BAR):
            with pytest.raises(ValueError, match="bars_per_day"):
                psr_from_equity_curve(curve, bars_per_day=0, sampling=sampling)


class TestSharpeVarianceAcrossTrials:
    def test_matches_the_sample_variance_of_the_trial_sharpes(self):
        values = [0.1, 0.2, 0.3, 0.4]
        assert sharpe_variance_across_trials(values) == pytest.approx(stdev(values) ** 2)

    def test_fewer_than_two_trials_is_none_not_zero(self):
        assert sharpe_variance_across_trials([0.2]) is None
        assert sharpe_variance_across_trials([]) is None

    def test_none_trials_are_excluded(self):
        assert sharpe_variance_across_trials([0.1, None, 0.3]) == pytest.approx(stdev([0.1, 0.3]) ** 2)


class TestDeflatedSharpeBenchmark:
    def test_single_trial_benchmark_is_exactly_zero(self):
        # E[max of ONE draw from a zero-mean distribution] = 0. The
        # Gumbel-based approximation is itself undefined at N=1
        # (Phi^-1(1 - 1/1) = Phi^-1(0) = -inf), so N=1 is special-cased to
        # the value the expectation actually takes.
        assert deflated_sharpe_benchmark(trial_sharpe_variance=4.0, num_trials=1) == 0.0

    def test_two_trials_matches_the_closed_form(self):
        # At N=2 the first term vanishes exactly (Phi^-1(1 - 1/2) = 0), so
        # SR0 = sqrt(V) * gamma * Phi^-1(1 - 1/(2e)) -- computable here
        # from stdlib alone, no reference implementation.
        expected = math.sqrt(9.0) * EULER_MASCHERONI * NormalDist().inv_cdf(1 - 1 / (2 * math.e))
        assert deflated_sharpe_benchmark(trial_sharpe_variance=9.0, num_trials=2) == pytest.approx(expected)

    def test_benchmark_grows_with_the_number_of_trials(self):
        values = [deflated_sharpe_benchmark(trial_sharpe_variance=1.0, num_trials=n) for n in (2, 10, 100, 1000)]
        assert values == sorted(values)

    def test_benchmark_scales_with_the_spread_of_the_trials(self):
        small = deflated_sharpe_benchmark(trial_sharpe_variance=1.0, num_trials=50)
        large = deflated_sharpe_benchmark(trial_sharpe_variance=4.0, num_trials=50)
        assert large == pytest.approx(2 * small)

    def test_zero_trials_yields_none(self):
        assert deflated_sharpe_benchmark(trial_sharpe_variance=1.0, num_trials=0) is None

    def test_zero_variance_across_trials_yields_none(self):
        assert deflated_sharpe_benchmark(trial_sharpe_variance=0.0, num_trials=10) is None

    def test_rejects_a_negative_variance(self):
        with pytest.raises(ValueError, match="trial_sharpe_variance"):
            deflated_sharpe_benchmark(trial_sharpe_variance=-1.0, num_trials=10)


class TestEvaluateDeflatedSharpe:
    def test_single_trial_reduces_exactly_to_psr_against_zero(self):
        psr = evaluate_psr(sharpe_ratio=0.2, num_observations=500)
        dsr = evaluate_deflated_sharpe(
            sharpe_ratio=0.2, num_observations=500, num_trials=1, trial_sharpe_variance=None
        )
        assert dsr.benchmark_sharpe == 0.0
        assert dsr.dsr == pytest.approx(psr.psr)

    def test_configuration_c_at_one_trial_matches_the_verification_anchor(self):
        per_bar = deannualize_sharpe(
            fmean(_CONFIGURATION_C_FOLD_SHARPES),
            bars_per_day=_CONFIGURATION_C_BARS_PER_DAY,
            sampling=SAMPLING_PER_BAR,
        )
        dsr = evaluate_deflated_sharpe(
            sharpe_ratio=per_bar,
            num_observations=_CONFIGURATION_C_NUM_BARS,
            num_trials=1,
            trial_sharpe_variance=None,
        )
        assert dsr.dsr == pytest.approx(0.5194, abs=1e-4)

    def test_more_trials_deflate_the_result(self):
        one = evaluate_deflated_sharpe(
            sharpe_ratio=0.4, num_observations=2000, num_trials=1, trial_sharpe_variance=0.01
        )
        many = evaluate_deflated_sharpe(
            sharpe_ratio=0.4, num_observations=2000, num_trials=200, trial_sharpe_variance=0.01
        )
        assert many.dsr < one.dsr
        assert many.benchmark_sharpe > 0

    def test_missing_trial_variance_beyond_one_trial_yields_none(self):
        result = evaluate_deflated_sharpe(
            sharpe_ratio=0.4, num_observations=2000, num_trials=200, trial_sharpe_variance=None
        )
        assert result.dsr is None
        assert result.benchmark_sharpe is None

    def test_degenerate_observation_count_yields_none(self):
        result = evaluate_deflated_sharpe(
            sharpe_ratio=0.4, num_observations=1, num_trials=10, trial_sharpe_variance=0.01
        )
        assert result.dsr is None

    def test_normal_assumption_is_recorded_on_the_dsr_result_too(self):
        result = evaluate_deflated_sharpe(
            sharpe_ratio=0.4, num_observations=2000, num_trials=10, trial_sharpe_variance=0.01
        )
        assert result.psr.moments_source == MOMENTS_SOURCE_NORMAL

    def test_to_dict_exposes_every_reported_field(self):
        d = evaluate_deflated_sharpe(
            sharpe_ratio=0.4, num_observations=2000, num_trials=10, trial_sharpe_variance=0.01
        ).to_dict()
        for key in ("dsr", "benchmark_sharpe", "num_trials", "trial_sharpe_variance", "psr"):
            assert key in d


class TestEligibilityReportsButDoesNotGateOnDeflatedSharpe:
    """CLAUDE.md's Eligibility Bar is human-approval-gated (same status as
    Risk Parameters). DSR is a *proposed* replacement for its mean-Sharpe
    t-test clause -- until a human approves that, supplying a DSR must
    change what `evaluate_eligibility` REPORTS and nothing about what it
    DECIDES.
    """

    def _dsr(self, sharpe_ratio: float):
        return evaluate_deflated_sharpe(
            sharpe_ratio=sharpe_ratio, num_observations=2000, num_trials=50, trial_sharpe_variance=0.01
        )

    def test_passed_is_unchanged_for_a_failing_result(self):
        values = [1.0] * 11 + [-1.0] * 8
        without = evaluate_eligibility(values, min_fold_consistency=Decimal("0.85"))
        with_dsr = evaluate_eligibility(
            values, min_fold_consistency=Decimal("0.85"), deflated_sharpe=self._dsr(0.001)
        )
        assert without.passed is False
        assert with_dsr.passed is False

    def test_passed_is_unchanged_for_a_passing_result_even_with_a_terrible_dsr(self):
        values = [2.0] * 18 + [1.5]
        without = evaluate_eligibility(values, min_fold_consistency=Decimal("0.80"))
        with_dsr = evaluate_eligibility(
            values, min_fold_consistency=Decimal("0.80"), deflated_sharpe=self._dsr(-5.0)
        )
        assert without.passed is True
        assert with_dsr.passed is True

    def test_deflated_sharpe_is_reported_when_supplied(self):
        values = [1.0] * 11 + [-1.0] * 8
        result = evaluate_eligibility(
            values, min_fold_consistency=Decimal("0.85"), deflated_sharpe=self._dsr(0.4)
        )
        assert result.deflated_sharpe is not None
        assert "deflated_sharpe" in result.to_dict()

    def test_key_is_absent_when_not_supplied(self):
        # Same "field only appears when the feature is actually used"
        # convention research/walkforward.py already uses for
        # parameter_sensitivity/funding_pnl_included -- an existing reader
        # of an EligibilityResult sees a byte-for-byte unchanged shape.
        values = [1.0] * 11 + [-1.0] * 8
        result = evaluate_eligibility(values, min_fold_consistency=Decimal("0.85"))
        assert result.deflated_sharpe is None
        assert "deflated_sharpe" not in result.to_dict()
