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

import pytest

from research.eligibility import (
    DEFAULT_NULL_PROBABILITY,
    DEFAULT_SIGNIFICANCE_ALPHA,
    binomial_sign_test_p_value,
    evaluate_eligibility,
    evaluate_fold_consistency,
    evaluate_mean_sharpe_significance,
    evaluate_sign_test,
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
