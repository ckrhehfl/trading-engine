"""Unit tests for `research.ic`.

The properties that matter here are the ones a naive IC harness gets
wrong and that would silently manufacture significance: non-overlapping
sampling, ties handled in the rank correlation, `None` features skipped
rather than imputed, and multiple-testing correction applied across the
whole sweep rather than per horizon.
"""

import math

import pytest

from research.ic import (
    IcResult,
    benjamini_hochberg,
    conditional_ic,
    format_sweep,
    measure_all,
    measure_ic,
    pearson,
    sample_indices,
    spearman,
)


# --- correlation primitives -------------------------------------------------


def test_pearson_and_spearman_are_one_for_a_perfect_increasing_relationship():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert pearson(xs, xs) == pytest.approx(1.0)
    assert spearman(xs, xs) == pytest.approx(1.0)


def test_spearman_sees_a_monotone_relationship_that_pearson_understates():
    # Exponential in x: monotone, so rank correlation is exactly 1, while
    # the linear correlation is not. This is the whole reason rank IC is
    # the default for fat-tailed return data.
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [math.exp(x) for x in xs]
    assert spearman(xs, ys) == pytest.approx(1.0)
    assert pearson(xs, ys) < 0.95


def test_correlations_are_none_not_zero_for_degenerate_input():
    # "Not measurable" must not be reported as "measured, no relationship".
    assert pearson([1.0, 2.0], [1.0, 2.0]) is None
    assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert pearson([1.0, 2.0, 3.0], [1.0, 2.0]) is None


def test_spearman_averages_ties_so_low_cardinality_features_stay_correct():
    # A feature taking two values (a session flag, a boolean) must not get
    # arbitrary tie-breaking, which would fabricate ordering information.
    xs = [0.0, 0.0, 1.0, 1.0]
    ys = [1.0, 2.0, 3.0, 4.0]
    got = spearman(xs, ys)
    assert got is not None
    assert got == pytest.approx(0.894427, abs=1e-5)


# --- non-overlapping sampling -----------------------------------------------


def test_sample_indices_step_by_the_horizon_so_windows_never_overlap():
    idx = sample_indices(total_bars=100, horizon=10)
    assert idx == [0, 10, 20, 30, 40, 50, 60, 70, 80]
    # Every forward window must land inside the data.
    assert all(i + 10 < 100 for i in idx)


def test_sample_indices_respects_warmup():
    assert sample_indices(total_bars=50, horizon=10, warmup=15) == [15, 25, 35]


def test_sample_indices_returns_empty_rather_than_negative_ranges():
    assert sample_indices(total_bars=5, horizon=10) == []
    assert sample_indices(total_bars=20, horizon=5, warmup=100) == []


@pytest.mark.parametrize("kwargs, match", [
    ({"horizon": 0}, "horizon"),
    ({"horizon": -1}, "horizon"),
    ({"horizon": 5, "warmup": -1}, "warmup"),
])
def test_sample_indices_rejects_invalid_arguments(kwargs, match):
    with pytest.raises(ValueError, match=match):
        sample_indices(total_bars=100, **kwargs)


# --- measure_ic -------------------------------------------------------------


def _closes_from_returns(returns):
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    return closes


def test_a_feature_that_perfectly_predicts_the_next_move_scores_ic_one():
    """An oracle feature -- literally the forward return -- must score 1.0.

    Returns are all distinct on purpose. A two-valued alternation would
    make the feature fully tied while floating-point error leaves the
    realised forward returns very slightly un-tied, and Spearman between a
    tied variable and an untied one cannot reach 1.0 even when the groups
    order perfectly. That is correct behaviour, not a defect, but it makes
    for a misleading oracle test.
    """
    rets = [(-1) ** i * (0.001 * (i + 1)) for i in range(40)]
    closes = _closes_from_returns(rets)
    feature = [rets[i] if i < len(rets) else None for i in range(len(closes))]
    r = measure_ic("oracle", feature, closes, horizon=1)
    assert r.rank_ic == pytest.approx(1.0)
    assert r.n > 10


def test_a_fully_tied_feature_cannot_reach_ic_one_against_untied_returns():
    """Pins the property the oracle test above sidesteps, so nobody
    'fixes' the harness to force 1.0 here: with the feature taking only
    two values and the realised returns not exactly tied, the ceiling is
    below 1 by construction."""
    rets = [0.01, -0.01] * 30
    closes = _closes_from_returns(rets)
    feature = [rets[i] if i < len(rets) else None for i in range(len(closes))]
    r = measure_ic("two_valued_oracle", feature, closes, horizon=1)
    assert r.rank_ic is not None
    assert 0.8 < r.rank_ic < 1.0


def test_a_feature_uncorrelated_with_forward_returns_scores_near_zero():
    rets = [0.01, -0.01] * 60
    closes = _closes_from_returns(rets)
    # Constant-slope feature carries no information about the alternation.
    feature = [float(i) for i in range(len(closes))]
    r = measure_ic("ramp", feature, closes, horizon=2)
    assert r.rank_ic is not None
    assert abs(r.rank_ic) < 0.3


def test_none_features_are_skipped_not_imputed():
    rets = [0.01, -0.01] * 30
    closes = _closes_from_returns(rets)
    full = [float(i) for i in range(len(closes))]
    half = [v if i % 2 == 0 else None for i, v in enumerate(full)]
    a = measure_ic("full", full, closes, horizon=1)
    b = measure_ic("half", half, closes, horizon=1)
    assert b.n < a.n, "None features must reduce the sample, not be filled in"
    assert b.n > 0


def test_is_usable_encodes_the_projects_own_0_02_calibration():
    def result(ic):
        return IcResult("f", 1, 100, ic, ic, None, None)

    assert not result(0.019).is_usable
    assert result(0.02).is_usable
    assert result(-0.05).is_usable, "a negative IC of the same size is equally usable"
    assert not result(None).is_usable


# --- multiple testing -------------------------------------------------------


def test_benjamini_hochberg_rejects_nothing_when_every_p_value_is_large():
    assert benjamini_hochberg([0.9, 0.8, 0.7]) == [False, False, False]


def test_benjamini_hochberg_accepts_a_clearly_significant_result():
    flags = benjamini_hochberg([1e-9, 0.9, 0.8, 0.7])
    assert flags[0] is True
    assert not any(flags[1:])


def test_benjamini_hochberg_is_less_conservative_than_bonferroni():
    # Twenty tests, five genuinely small p-values just under alpha/m.
    p = [0.001, 0.002, 0.003, 0.004, 0.005] + [0.5] * 15
    flags = benjamini_hochberg(p, alpha=0.05)
    bonferroni = [x <= 0.05 / len(p) for x in p]
    assert sum(flags) > sum(bonferroni)


def test_benjamini_hochberg_treats_none_as_never_a_discovery():
    flags = benjamini_hochberg([None, 1e-9])
    assert flags[0] is False
    assert flags[1] is True


def test_benjamini_hochberg_rejects_an_invalid_alpha():
    with pytest.raises(ValueError, match="alpha"):
        benjamini_hochberg([0.1], alpha=0)


# --- sweep ------------------------------------------------------------------


def test_measure_all_corrects_across_the_whole_sweep_not_per_horizon():
    """The real guarantee: the family corrected is the family tested.

    Deliberately NOT asserting that more hypotheses make the bar
    stricter -- an earlier version of this test did, and the invariant is
    false for Benjamini-Hochberg (see the counterexample test below).
    """
    rets = [0.01, -0.01] * 60
    closes = _closes_from_returns(rets)
    features = {f"noise_{k}": [float((i * (k + 3)) % 7) for i in range(len(closes))] for k in range(8)}
    sweeps = measure_all(features, closes, horizons=[1, 2])

    assert len(sweeps) == 2 * len(features), "every feature x horizon pair must be tested"
    expected = benjamini_hochberg([s.result.p_value for s in sweeps])
    assert [s.survives_fdr for s in sweeps] == expected, (
        "survives_fdr must be one BH pass over the whole sweep's p-values"
    )


def test_benjamini_hochberg_is_not_monotone_in_the_number_of_hypotheses():
    """Pins the property that invalidated an earlier version of the test
    above, so the false invariant cannot be reintroduced.

    BH re-ranks the whole family when it changes, so adding a hypothesis
    with a very small p-value raises the step-up cutoff and can make a
    previously-rejected result significant.
    """
    assert benjamini_hochberg([0.03, 0.9]) == [False, False]
    assert benjamini_hochberg([0.001, 0.03, 0.9]) == [True, True, False]


def test_measure_all_sorts_by_absolute_rank_ic():
    rets = [0.01, -0.01] * 40
    closes = _closes_from_returns(rets)
    features = {
        "oracle": [rets[i] if i < len(rets) else None for i in range(len(closes))],
        "ramp": [float(i) for i in range(len(closes))],
    }
    sweeps = measure_all(features, closes, horizons=[1])
    ics = [abs(s.result.rank_ic) for s in sweeps if s.result.rank_ic is not None]
    assert ics == sorted(ics, reverse=True)


def test_is_interesting_requires_both_size_and_surviving_correction():
    rets = [0.01, -0.01] * 60
    closes = _closes_from_returns(rets)
    sweeps = measure_all(
        {"oracle": [rets[i] if i < len(rets) else None for i in range(len(closes))]},
        closes,
        horizons=[1],
    )
    assert sweeps[0].is_interesting


# --- conditional IC ---------------------------------------------------------


def test_conditional_ic_restricts_to_the_top_quantile_of_the_conditioner():
    rets = [0.01, -0.01] * 60
    closes = _closes_from_returns(rets)
    feature = [float(i) for i in range(len(closes))]
    conditioner = [float(i) for i in range(len(closes))]
    full = measure_ic("f", feature, closes, horizon=1)
    top = conditional_ic("f", feature, closes, 1, conditioner, quantile=0.9)
    assert top.n < full.n
    assert top.n > 0


def test_conditional_ic_handles_an_entirely_unmeasurable_conditioner():
    closes = _closes_from_returns([0.01] * 20)
    r = conditional_ic("f", [1.0] * len(closes), closes, 1, [None] * len(closes), quantile=0.9)
    assert r.n == 0
    assert r.rank_ic is None


def test_conditional_ic_rejects_an_out_of_range_quantile():
    closes = _closes_from_returns([0.01] * 20)
    with pytest.raises(ValueError, match="quantile"):
        conditional_ic("f", [1.0] * len(closes), closes, 1, [1.0] * len(closes), quantile=1.0)


# --- formatting -------------------------------------------------------------


def test_format_sweep_hides_the_long_tail_below_the_threshold():
    rets = [0.01, -0.01] * 40
    closes = _closes_from_returns(rets)
    features = {
        "oracle": [rets[i] if i < len(rets) else None for i in range(len(closes))],
        "flat": [1.0] * len(closes),
    }
    sweeps = measure_all(features, closes, horizons=[1])
    text = format_sweep(sweeps, min_abs_ic=0.5)
    assert "oracle" in text
    assert "flat" not in text


# --- input validation and perfect correlation -------------------------------


def test_measure_ic_rejects_a_length_mismatch_instead_of_indexing_off_the_end():
    closes = _closes_from_returns([0.01] * 30)
    with pytest.raises(ValueError, match="same length"):
        measure_ic("short", [1.0] * 5, closes, horizon=1)


def test_conditional_ic_rejects_a_length_mismatch_instead_of_silently_truncating():
    closes = _closes_from_returns([0.01] * 30)
    with pytest.raises(ValueError, match="same length"):
        conditional_ic("f", [1.0] * 5, closes, 1, [1.0] * len(closes), quantile=0.5)


def test_a_perfect_rank_match_gets_a_real_p_value_not_none():
    """A perfect correlation is the strongest evidence possible; leaving
    its p-value None would exclude it from FDR correction and mark the
    most predictive feature imaginable as not interesting."""
    rets = [(-1) ** i * (0.001 * (i + 1)) for i in range(40)]
    closes = _closes_from_returns(rets)
    feature = [rets[i] if i < len(rets) else None for i in range(len(closes))]
    r = measure_ic("oracle", feature, closes, horizon=1)
    assert r.rank_ic == pytest.approx(1.0)
    assert r.p_value == 0.0
    assert r.t_stat == math.inf

    sweeps = measure_all({"oracle": feature}, closes, horizons=[1])
    assert sweeps[0].survives_fdr, "a perfect feature must survive FDR"
    assert sweeps[0].is_interesting
