"""Tests for `research.stage3_separator`.

The properties that carry this module are the ones the specification
(`.planning/rd-m-stage3-separator-specification.md`) makes load-bearing:

- **The null permutes labels, not the series.** That is the whole reason
  stage 3 is a better instrument than stage 2's three nulls, so a test
  pins that a confound shared by both branches cancels.
- **The split threshold is in-sample and the events are held fixed**, so a
  dropped event must not move the threshold for the survivors.
- **The composition guard's threshold comes from the same permutation**,
  never a constant -- the one thing rd-j's `null_suspect` got wrong.
- **`m` is 18** and comes from the constant, so a run that produced fewer
  tests cannot shrink its own correction.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from research.situation_catalogue import DEFAULT_ROUND_TRIP
from research.stage2_event_study import dependence_penalty
from research.stage3_separator import (
    EFFECT_FLOOR,
    FAMILY_SIZE,
    FDR_Q,
    HORIZONS,
    MIN_PERMUTATIONS_FOR_VERDICT,
    SEPARATORS,
    SeparatorTest,
    benjamini_yekutieli,
    extremity,
    median_split,
    prior_trend,
    report,
    run,
    separator_test,
    taker_buy_share,
    total_variation,
    verdict,
)


def _test(**kw):
    base = dict(
        situation="S1 support penetration",
        horizon=15,
        separator=SEPARATORS[0],
        n_high=500,
        n_low=500,
        n_dropped=0,
        mean_high=0.0020,
        mean_low=-0.0020,
        null_sd=0.0005,
        p_value=1e-4,
        permutations=2000,
        event_sd=0.0100,
        tvd_decile=0.05,
        tvd_decile_null=0.10,
        tvd_hour=0.05,
        tvd_hour_null=0.10,
        significant=True,
    )
    return SeparatorTest(**{**base, **kw})


# ------------------------------------------------------ the specification


def test_the_family_is_eighteen_and_fixed():
    """3 situations x 2 horizons x 3 separators. rd-m §3 -- extending
    either dimension makes the reported FDR meaningless."""
    assert FAMILY_SIZE == 18
    assert HORIZONS == (15, 60)
    assert len(SEPARATORS) == 3


def test_the_horizons_stop_at_60_because_rd_l_said_so():
    """240 and 1440 are excluded by rd-l's power rule, not by a result:
    their detectable difference is 34-59bp, the regime rd-l was written to
    name."""
    assert 240 not in HORIZONS and 1440 not in HORIZONS


def test_the_permutation_floor_makes_the_rank_one_threshold_reachable():
    """Below this, `1/(B+1)` cannot reach the m=18 rank-1 threshold, so no
    test could clear the decision rule however strong it was and the
    verdict would be an artefact of the permutation count."""
    thresh = FDR_Q / (FAMILY_SIZE * dependence_penalty(FAMILY_SIZE))
    assert 1.0 / (MIN_PERMUTATIONS_FOR_VERDICT + 1) <= thresh
    assert 1.0 / MIN_PERMUTATIONS_FOR_VERDICT > thresh  # and it is the smallest such B


def test_a_run_below_the_permutation_floor_is_refused():
    with pytest.raises(ValueError, match="cannot reach the rank-1 BY threshold"):
        run(permutations=MIN_PERMUTATIONS_FOR_VERDICT - 1)


def test_the_branch_floor_is_the_measured_round_trip():
    assert EFFECT_FLOOR == DEFAULT_ROUND_TRIP


# -------------------------------------------------------- the separators


def test_taker_buy_share_is_nan_on_a_zero_volume_bar():
    """526 bars of the real series have no trade at all, so the share is
    genuinely undefined. Imputing 0.5 would manufacture a 'balanced'
    observation in whichever branch the median put it."""
    v = np.array([10.0, 0.0, 4.0])
    tb = np.array([6.0, 0.0, 1.0])
    s = taker_buy_share(v, tb)
    assert s[0] == pytest.approx(0.6)
    assert np.isnan(s[1])
    assert s[2] == pytest.approx(0.25)


def test_taker_buy_share_lies_in_the_unit_interval_on_real_shaped_input():
    rng = np.random.default_rng(0)
    v = rng.uniform(1, 100, 1000)
    tb = v * rng.uniform(0, 1, 1000)
    s = taker_buy_share(v, tb)
    assert np.all((s >= 0) & (s <= 1))


def test_prior_trend_reads_only_backwards():
    """It is evaluated at the event bar, so a single value from the future
    must not move any earlier one."""
    c = np.arange(1.0, 501.0)
    a = prior_trend(c, lookback=240)
    c2 = c.copy()
    c2[-1] = 10_000.0
    b = prior_trend(c2, lookback=240)
    assert np.allclose(a[:-1], b[:-1], equal_nan=True)


def test_prior_trend_is_undefined_before_a_full_lookback():
    out = prior_trend(np.arange(1.0, 300.0), lookback=240)
    assert np.all(np.isnan(out[:240])) and np.all(np.isfinite(out[240:]))


def test_prior_trend_on_a_series_shorter_than_its_lookback_is_all_nan():
    assert np.all(np.isnan(prior_trend(np.arange(1.0, 10.0), lookback=240)))


def test_extremity_is_defined_per_situation_and_s3_uses_volume():
    """S3 has no level for a depth to be measured against -- it fires on
    volume -- so a shared definition would be wrong rather than merely
    inelegant."""
    rng = np.random.default_rng(1)
    # Long enough for S3's `trailing_quantile`, whose window is 43,200 bars
    # (30 days) -- below that its extremity is undefined everywhere, which
    # is the look-ahead guard working, not a gap in the definition.
    n = 50_000
    c = 100 + np.cumsum(rng.normal(0, 0.1, n))
    h, l = c + 0.5, c - 0.5
    v = rng.uniform(1, 10, n)
    for name in ("S1 support penetration", "S2 resistance break", "S3 abnormal activity"):
        x = extremity(name, h, l, c, v)
        assert x.size == n
        assert np.isfinite(x).any(), name


def test_an_unregistered_situation_has_no_extremity_and_says_so():
    """Silently returning NaN would produce a test with every event
    dropped, which reads as a data problem rather than a missing
    definition."""
    with pytest.raises(ValueError, match="no registered extremity"):
        extremity("S9 invented", np.ones(10), np.ones(10), np.ones(10), np.ones(10))


# ------------------------------------------------------------ the split


def test_the_median_split_is_balanced_on_distinct_values():
    x = np.arange(100.0)
    kept, is_high, dropped = median_split(x, np.arange(100))
    assert dropped == 0
    assert int(is_high.sum()) == 50 and int((~is_high).sum()) == 50


def test_an_undefined_separator_drops_the_event_and_is_counted():
    x = np.arange(20.0)
    x[[3, 7]] = np.nan
    kept, is_high, dropped = median_split(x, np.arange(20))
    assert dropped == 2 and kept.size == 18


def test_a_dropped_event_does_not_move_the_threshold_for_the_survivors():
    """The median is taken over the survivors. Taking it over all events
    including the dropped ones would let an undefined value decide which
    branch a defined one lands in."""
    x = np.arange(20.0)
    kept_a, high_a, _ = median_split(x[np.arange(20) != 19], np.arange(19))
    x2 = x.copy()
    x2[19] = np.nan
    kept_b, high_b, dropped = median_split(x2, np.arange(20))
    assert dropped == 1
    assert np.array_equal(kept_a, kept_b) and np.array_equal(high_a, high_b)


def test_a_separator_with_too_few_defined_events_is_refused():
    x = np.full(10, np.nan)
    x[0] = 1.0
    with pytest.raises(ValueError, match="have a defined separator"):
        median_split(x, np.arange(10))


# --------------------------------------------------- total variation


def test_identical_composition_has_zero_distance():
    a = np.array([0, 1, 2, 0, 1, 2])
    assert total_variation(a, a.copy(), 3) == pytest.approx(0.0)


def test_disjoint_composition_has_distance_one():
    assert total_variation(np.zeros(5, int), np.ones(5, int), 2) == pytest.approx(1.0)


def test_distance_is_a_proportion_not_a_count():
    """Two branches of very different sizes but identical composition are
    at distance zero -- otherwise the guard would fire on the split's
    imbalance rather than on its confounding."""
    a = np.array([0] * 90 + [1] * 10)
    b = np.array([0] * 9 + [1] * 1)
    assert total_variation(a, b, 2) == pytest.approx(0.0)


def test_an_empty_branch_is_maximally_distant_rather_than_a_crash():
    assert total_variation(np.array([], int), np.array([0, 1]), 2) == 1.0


# ------------------------------------------------------ the null itself


def _series(n=6000, seed=3):
    rng = np.random.default_rng(seed)
    o = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
    return o, rng


def test_an_uninformative_separator_is_not_significant():
    """The separator is pure noise, so the split carries nothing and the
    permutation p should be unremarkable."""
    o, rng = _series()
    ev = np.arange(500, 5000, 20)
    x = np.full(o.size, np.nan)
    x[ev] = rng.normal(size=ev.size)
    t = separator_test(
        o, ev, 15, x, np.zeros(o.size, np.int8), np.zeros(o.size, np.int8),
        "S1 support penetration", SEPARATORS[0], rng, permutations=700,
    )
    assert t.p_value > 0.05


def test_a_separator_that_really_splits_the_outcomes_is_found():
    """The guard against a null that cannot detect anything: a separator
    constructed to predict the forward return must come back small."""
    n = 6000
    rng = np.random.default_rng(5)
    ev = np.arange(500, 5000, 20)
    o = np.full(n, 100.0)
    x = np.full(n, np.nan)
    for j, i in enumerate(ev):
        up = j % 2 == 0
        x[i] = 1.0 if up else 0.0
        # The EXIT bar only. Setting the whole window to one constant makes
        # entry and exit equal and every forward return exactly zero, which
        # is a fixture that cannot detect anything -- the first version of
        # this test did that and passed the module a series with no signal
        # in it at all.
        o[i + 1 + 15] = 100.0 * (1.02 if up else 0.98)
    t = separator_test(
        o, ev, 15, x, np.zeros(n, np.int8), np.zeros(n, np.int8),
        "S1 support penetration", SEPARATORS[0], rng, permutations=700,
    )
    assert t.p_value <= 2.0 / 701
    assert t.difference > 0


def test_a_confound_shared_by_both_branches_cancels():
    """**The reason stage 3 is a better instrument than stage 2's nulls.**
    A large drift affects every event equally, so it lands in both branch
    means and vanishes from the difference -- where a shift or matched
    null had to model it."""
    n = 6000
    rng = np.random.default_rng(7)
    ev = np.arange(500, 5000, 20)
    # A strong common uptrend, plus a separator that carries nothing.
    o = 100 * np.exp(np.arange(n) * 0.0004)
    x = np.full(n, np.nan)
    x[ev] = rng.normal(size=ev.size)
    t = separator_test(
        o, ev, 15, x, np.zeros(n, np.int8), np.zeros(n, np.int8),
        "S1 support penetration", SEPARATORS[0], rng, permutations=700,
    )
    assert t.mean_high > 0 and t.mean_low > 0  # the drift is in both branches
    assert abs(t.difference) < 1e-4  # and not in their difference
    assert t.p_value > 0.05


def test_an_event_without_its_forward_window_is_dropped_and_counted():
    o, rng = _series(n=1200)
    ev = np.array([100, 300, 500, 700, 1190])  # the last cannot reach h=15
    x = np.full(o.size, np.nan)
    x[ev] = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    t = separator_test(
        o, ev, 15, x, np.zeros(o.size, np.int8), np.zeros(o.size, np.int8),
        "S1 support penetration", SEPARATORS[0], rng, permutations=700,
    )
    assert t.n_dropped == 1 and t.n_high + t.n_low == 4


def test_a_degenerate_split_is_refused_rather_than_reported():
    """A near-constant separator puts everything on one side; a 'result'
    from a 1-event branch is noise wearing a table row."""
    o, rng = _series()
    ev = np.arange(500, 5000, 20)
    x = np.full(o.size, np.nan)
    x[ev] = 1.0  # every value identical -> `>= median` is all-True
    with pytest.raises(ValueError, match="split is"):
        separator_test(
            o, ev, 15, x, np.zeros(o.size, np.int8), np.zeros(o.size, np.int8),
            "S1 support penetration", SEPARATORS[0], rng, permutations=700,
        )


def test_an_undefined_volatility_decile_does_not_index_from_the_end():
    """`-1` means 'decile undefined'. Passed to `bincount` unshifted it
    would silently land in the last bin and corrupt the composition
    guard's own input."""
    o, rng = _series()
    ev = np.arange(500, 5000, 20)
    x = np.full(o.size, np.nan)
    x[ev] = rng.normal(size=ev.size)
    t = separator_test(
        o, ev, 15, x, np.full(o.size, -1, np.int8), np.zeros(o.size, np.int8),
        "S1 support penetration", SEPARATORS[0], rng, permutations=700,
    )
    # Every event shares one (undefined) stratum, so composition is identical.
    assert t.tvd_decile == pytest.approx(0.0)


# --------------------------------------------------------- the verdicts


def test_the_composition_guard_fires_when_either_axis_exceeds_its_null():
    assert not _test().split_confounded
    assert _test(tvd_decile=0.2).split_confounded
    assert _test(tvd_hour=0.2).split_confounded


def test_the_guard_threshold_is_the_null_not_a_constant():
    """The same TVD is fine against a loose null and confounded against a
    tight one, which is the point: 'more different than random labelling
    of these same events would produce'."""
    assert not _test(tvd_decile=0.15, tvd_decile_null=0.20).split_confounded
    assert _test(tvd_decile=0.15, tvd_decile_null=0.10).split_confounded


def test_the_floor_is_on_the_branch_not_the_difference():
    """A strategy trades one branch and pays one round trip to be in it,
    so two sub-cost branches that differ by a lot are a fact about market
    structure, not a candidate."""
    r = _test(mean_high=0.0005, mean_low=-0.0005)  # difference 10bp, branches 5bp
    assert abs(r.difference) * 1e4 == pytest.approx(10.0)
    assert not r.clears_effect_floor


def test_the_best_branch_keeps_its_sign():
    """A -20bp branch is as tradeable as a +20bp one; the family is
    two-sided by construction and `max(abs(...))` would discard that."""
    assert _test(mean_high=0.0005, mean_low=-0.0030).best_branch == pytest.approx(-0.0030)
    assert _test(mean_high=0.0030, mean_low=-0.0005).best_branch == pytest.approx(0.0030)


def test_advancing_needs_all_three_clauses():
    assert _test().advances
    assert not _test(significant=False).advances
    assert not _test(mean_high=0.0001, mean_low=-0.0001).advances
    assert not _test(tvd_hour=0.99).advances


def test_a_significant_but_confounded_test_is_named_as_such():
    assert verdict(_test(tvd_decile=0.9)) == "SPLIT-CONFOUNDED"
    assert verdict(_test(mean_high=0.0001, mean_low=-0.0001)) == "sig, small"
    assert verdict(_test(significant=False, p_value=0.4)) == "-"
    assert verdict(_test()) == "ADVANCE"


# --------------------------------------------------------------- FDR


def test_the_correction_uses_the_declared_family_size_not_the_row_count():
    """A run that silently produced fewer tests must not shrink its own
    correction and inflate its own significance."""
    few = benjamini_yekutieli([_test(p_value=p) for p in (1e-5, 0.5)])
    pen = dependence_penalty(FAMILY_SIZE)
    assert few[0].bh_threshold == pytest.approx(FDR_Q / (FAMILY_SIZE * pen))


def test_the_rank_one_threshold_is_the_registered_one():
    """rd-m §3 pins 0.001590 at m=18."""
    pen = dependence_penalty(FAMILY_SIZE)
    assert FDR_Q / (FAMILY_SIZE * pen) == pytest.approx(0.001590, abs=1e-6)


def test_nothing_clears_a_family_of_null_results():
    out = benjamini_yekutieli([_test(p_value=0.4, significant=False) for _ in range(18)])
    assert not any(r.significant for r in out)


# ------------------------------------------------------------- report


def test_the_report_names_the_threshold_the_floor_and_discovery_mode(capsys):
    report([_test()])
    out = capsys.readouterr().out
    assert "0.001590" in out and "12bp" in out
    assert "Discovery mode" in out and "advance to stage 4" in out


# ------------------------------------------------- null calibration


def test_a_label_permutation_null_is_calibrated_by_construction():
    """**rd-m §2's central claim, measured rather than argued.** Both
    branches come from the same events, so the permutation distribution
    *is* the sampling distribution of the difference -- unlike stage 2's
    matched null, which rd-l §4.1 found at 0.41 because it matched on
    PRIOR volatility while the events were FORWARD volatility bursts."""
    o, rng = _series(n=20_000)
    ev = np.arange(500, 19_000, 20)
    x = np.full(o.size, np.nan)
    x[ev] = rng.normal(size=ev.size)
    t = separator_test(
        o, ev, 15, x, np.zeros(o.size, np.int8), np.zeros(o.size, np.int8),
        "S1 support penetration", SEPARATORS[0], rng, permutations=2000,
    )
    assert t.null_calibration == pytest.approx(1.0, abs=0.06)


def test_the_analytic_se_is_the_two_sample_form():
    r = _test(n_high=400, n_low=600, event_sd=0.01)
    assert r.analytic_se == pytest.approx(0.01 * math.sqrt(1 / 400 + 1 / 600))


def test_a_narrower_null_shows_up_as_a_calibration_below_one():
    """The number that would have caught rd-l §4.1 three nulls earlier."""
    r = _test(n_high=500, n_low=500, event_sd=0.01, null_sd=0.0002)
    assert r.null_calibration < 0.5


def test_an_empty_branch_gives_no_calibration_rather_than_a_division():
    assert math.isnan(_test(n_high=0).analytic_se)
    assert math.isnan(_test(n_high=0).null_calibration)


def test_the_report_names_the_calibration_range(capsys):
    report([_test()])
    assert "null calibration" in capsys.readouterr().out
