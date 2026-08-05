"""Tests for `python/research/meta_analysis.py` -- Strategy Research Task
AC's Stouffer's (weighted) Z-score combination of two already-logged,
independent PSR significance tests (`sr-v`'s BingX holdout and `sr-ab`'s
Binance holdout, both confirming the same `strategy_id=daily-tsmom-
ensemble` hypothesis on disjoint samples). See
`.planning/sr-ac-combined-holdout-meta-analysis.md` for the full
derivation and the real combined-evidence report this module supports.

Written first (TDD): this file existed and failed on `ModuleNotFoundError`
before `research/meta_analysis.py` did.

Every numeric fixture here is either a hand-derived identity (so the
expected value is computed independently of the function under test, not
just re-asserting whatever the code happens to produce) or this project's
own real, already-logged `sr-v`/`sr-ab` PSR z-scores.
"""

import math
from statistics import NormalDist

import pytest

from research.meta_analysis import (
    StoufferCombinationResult,
    combine_z_scores,
    sample_size_weights,
)

# ---------------------------------------------------------------------------
# sample_size_weights
# ---------------------------------------------------------------------------


class TestSampleSizeWeights:
    def test_returns_sqrt_of_each_sample_size(self):
        weights = sample_size_weights([1078, 1365])
        assert weights == pytest.approx([math.sqrt(1078), math.sqrt(1365)])

    def test_rejects_empty_input(self):
        with pytest.raises(ValueError, match="non-empty"):
            sample_size_weights([])

    def test_rejects_non_positive_sample_size(self):
        with pytest.raises(ValueError, match="positive"):
            sample_size_weights([100, 0])
        with pytest.raises(ValueError, match="positive"):
            sample_size_weights([100, -5])


# ---------------------------------------------------------------------------
# combine_z_scores
# ---------------------------------------------------------------------------


class TestCombineZScores:
    def test_rejects_empty_z_scores(self):
        with pytest.raises(ValueError, match="non-empty"):
            combine_z_scores([])

    def test_rejects_mismatched_weight_length(self):
        with pytest.raises(ValueError, match="same length"):
            combine_z_scores([1.0, 2.0], weights=[1.0])

    def test_rejects_non_positive_weight(self):
        with pytest.raises(ValueError, match="positive"):
            combine_z_scores([1.0, 2.0], weights=[1.0, 0.0])
        with pytest.raises(ValueError, match="positive"):
            combine_z_scores([1.0, 2.0], weights=[1.0, -3.0])

    def test_rejects_non_finite_z_score(self):
        with pytest.raises(ValueError, match="finite"):
            combine_z_scores([1.0, math.nan])
        with pytest.raises(ValueError, match="finite"):
            combine_z_scores([1.0, math.inf])
        with pytest.raises(ValueError, match="finite"):
            combine_z_scores([1.0, -math.inf])

    def test_rejects_non_finite_weight(self):
        with pytest.raises(ValueError, match="finite"):
            combine_z_scores([1.0, 2.0], weights=[1.0, math.nan])
        with pytest.raises(ValueError, match="finite"):
            combine_z_scores([1.0, 2.0], weights=[1.0, math.inf])

    def test_equal_extremely_large_weights_do_not_overflow(self):
        # Squaring 1e200 alone overflows float64 (1e200**2 == 1e400 ->
        # inf) -- if combine_z_scores summed w**2 without first
        # normalizing by the max weight, this would produce a non-finite
        # or garbage combined_z. Equal weights should still reduce to the
        # same unweighted identity as elsewhere in this file.
        huge = 1e200
        result = combine_z_scores([1.0, 2.0], weights=[huge, huge])
        assert math.isfinite(result.combined_z)
        assert result.combined_z == pytest.approx((1.0 + 2.0) / math.sqrt(2))

    def test_equal_extremely_small_weights_do_not_underflow(self):
        tiny = 1e-200
        result = combine_z_scores([1.0, 2.0], weights=[tiny, tiny])
        assert math.isfinite(result.combined_z)
        assert result.combined_z == pytest.approx((1.0 + 2.0) / math.sqrt(2))

    def test_extreme_weight_ratio_does_not_silently_zero_out_via_overflow(self):
        # Confirmed bug this test guards against: WITHOUT normalizing by
        # the max weight first, sum(w**2) for w=[1e200, 1e-200] overflows
        # to float('inf') (1e200**2 == 1e400), which collapses
        # combined_z to 0.0 -- a silently WRONG answer (the dominant
        # weight's own z-score should carry the result, not vanish),
        # not a raised error. With normalization, the negligible weight
        # correctly contributes ~nothing and the dominant weight's own
        # z-score (1.0) carries the result.
        result = combine_z_scores([1.0, 2.0], weights=[1e200, 1e-200])
        assert math.isfinite(result.combined_z)
        assert result.combined_z == pytest.approx(1.0, abs=1e-9)

    def test_extreme_weights_still_report_the_original_unnormalized_values(self):
        # The internal normalization used to avoid overflow must not leak
        # into what the result reports -- a caller inspecting `.weights`
        # should see exactly what it passed in, not a rescaled version.
        result = combine_z_scores([1.0, 2.0], weights=[1e200, 1e-200])
        assert result.weights == (1e200, 1e-200)

    def test_degenerate_single_input_returns_its_own_z_score_unchanged(self):
        # n=1: Z_combined = w*z / sqrt(w^2) = z for any positive w --
        # combining "one test with itself" must be a no-op.
        result = combine_z_scores([1.9327])
        assert result.combined_z == pytest.approx(1.9327)
        assert result.combined_probability == pytest.approx(NormalDist().cdf(1.9327))

        result_weighted = combine_z_scores([1.9327], weights=[42.0])
        assert result_weighted.combined_z == pytest.approx(1.9327)

    def test_two_identical_z_scores_unweighted_gives_z_times_sqrt_2(self):
        # Hand-derived identity: Z_combined = (z + z) / sqrt(1^2 + 1^2)
        #                                    = 2z / sqrt(2) = z * sqrt(2).
        z = 1.6448536269514722  # the standard one-sided alpha=0.05 critical z
        result = combine_z_scores([z, z])
        assert result.combined_z == pytest.approx(z * math.sqrt(2))

    def test_two_identical_z_scores_equal_n_weighted_reduces_to_same_identity(self):
        # Hand-derived identity for the weighted case at EQUAL n:
        # w1 = w2 = sqrt(n), so
        #   Z_combined = (sqrt(n)*z + sqrt(n)*z) / sqrt(n + n)
        #              = sqrt(n)*2z / sqrt(2n) = 2z / sqrt(2) = z * sqrt(2)
        # i.e. identical to the unweighted case whenever n1 == n2, exactly
        # as CLAUDE.md's own task brief predicts ("reduces to a known
        # identity for the weighted case with equal n").
        z = -0.7
        n = 500
        weights = sample_size_weights([n, n])
        result = combine_z_scores([z, z], weights=weights)
        assert result.combined_z == pytest.approx(z * math.sqrt(2))

    def test_unweighted_default_matches_explicit_equal_weights(self):
        z_scores = [0.8, -1.3, 2.1]
        default_result = combine_z_scores(z_scores)
        explicit_result = combine_z_scores(z_scores, weights=[1.0, 1.0, 1.0])
        assert default_result.combined_z == pytest.approx(explicit_result.combined_z)

    def test_combined_probability_is_normal_cdf_of_combined_z(self):
        result = combine_z_scores([1.0, 2.0], weights=[3.0, 4.0])
        assert result.combined_probability == pytest.approx(NormalDist().cdf(result.combined_z))

    def test_result_carries_inputs_through_unchanged(self):
        result = combine_z_scores([1.0, 2.0], weights=[3.0, 4.0])
        assert result.z_scores == (1.0, 2.0)
        assert result.weights == (3.0, 4.0)

    def test_to_dict_round_trips_all_fields(self):
        result = combine_z_scores([1.0, 2.0], weights=[3.0, 4.0])
        as_dict = result.to_dict()
        assert as_dict["combined_z"] == result.combined_z
        assert as_dict["combined_probability"] == result.combined_probability
        assert as_dict["z_scores"] == [1.0, 2.0]
        assert as_dict["weights"] == [3.0, 4.0]

    def test_real_sr_v_and_sr_ab_psr_z_scores_sample_size_weighted(self):
        # The real, already-logged PSR z-scores this task combines:
        # sr-v (BingX holdout, run_id=8143a525-3159-447b-991d-2f11a0ef790b,
        # n=1078) and sr-ab (Binance holdout,
        # run_id=a84d52ba-5f5d-43bd-a528-3d5cd494208a, n=1365) -- both
        # already verified directly against runs/experiments.jsonl.
        z_sr_v = 1.5274374516384879
        n_sr_v = 1078
        z_sr_ab = 2.540994558102991
        n_sr_ab = 1365

        weights = sample_size_weights([n_sr_v, n_sr_ab])
        result = combine_z_scores([z_sr_v, z_sr_ab], weights=weights)

        # Hand-computed via the formula directly, independent of the
        # implementation under test.
        w1, w2 = math.sqrt(n_sr_v), math.sqrt(n_sr_ab)
        expected_z = (w1 * z_sr_v + w2 * z_sr_ab) / math.sqrt(w1**2 + w2**2)
        assert result.combined_z == pytest.approx(expected_z)
        assert result.combined_z == pytest.approx(2.9140024493420316)
        assert result.combined_probability == pytest.approx(0.9982158644653765)
        # Clears the project's standing 0.95 significance convention.
        assert result.combined_probability >= 0.95

    def test_isinstance_stouffer_result(self):
        assert isinstance(combine_z_scores([1.0]), StoufferCombinationResult)
