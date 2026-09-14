"""Tests for `research.stage2_event_study` — rd-b stage 2.

The case that matters is **the control pool**, because on the real run a
directional exclusion rule manufactured a significant, correctly-signed,
cost-clearing effect out of nothing: `+15.55bp, t = 2.76, p = 0.0059`,
which collapsed to `+0.96bp, t = 0.17` when only the *control* arm's
eligibility changed. rd-h §3.

None of the headline statistics exposed it. What exposed it was one
control arm disagreeing with another that should have matched it — so
that asymmetry is what these tests pin.
"""

from __future__ import annotations

import numpy as np
import pytest

from research.stage2_event_study import (
    CONTROLS_PER_EVENT,
    EXCLUSION_RULES,
    FAMILY_SIZE,
    FDR_Q,
    HORIZONS,
    EventTest,
    benjamini_hochberg,
    collapse,
    control_pool,
    forward_return,
    hour_of_day,
    match_controls,
    welch,
)


def _result(p, diff=0.02, **over):
    """A test whose control arm sits ON the unconditional baseline, so the
    control-bias veto does not fire unless a case sets it deliberately."""
    base = dict(
        situation="S", horizon=60, n_events=100, n_controls=500, n_dropped=0,
        event_mean=diff, control_mean=0.0, unconditional_mean=0.0,
        pool_share=0.85, difference=diff,
        t_statistic=0.0, p_value=p,
    )
    base.update(over)
    return EventTest(**base)


# ------------------------------------------------------- the control pool


def _episodes():
    return {"up": np.array([1000]), "down": np.array([5000])}


def test_the_own_rule_leaves_other_situations_eligible():
    """This is the bias. A control pool that excludes only the tested
    situation is conditional on *that* situation's absence, which for a
    directional situation is a directional condition."""
    pool = control_pool(10_000, 10, np.zeros(10_000, np.int8), _episodes(), "up", "own")
    assert not pool[1000], "the tested situation must be excluded"
    assert pool[5000], "the other situation is still eligible under 'own'"


def test_the_all_rule_excludes_every_situation():
    pool = control_pool(10_000, 10, np.zeros(10_000, np.int8), _episodes(), "up", "all")
    assert not pool[1000] and not pool[5000]


def test_the_two_rules_differ_which_is_the_whole_point():
    z = np.zeros(10_000, np.int8)
    own = control_pool(10_000, 10, z, _episodes(), "up", "own")
    alll = control_pool(10_000, 10, z, _episodes(), "up", "all")
    assert own.sum() > alll.sum(), "'all' must be strictly more restrictive"


def test_a_directional_exclusion_shifts_the_control_mean():
    """The artifact, reproduced in miniature.

    A series that rises only in a few bursts; 'events' are those bursts.
    Excluding their neighbourhood removes the rises, so the surviving
    control pool has a materially lower mean return than the series does.
    That 16bp version of this effect was rd-h's entire 'discovery'.
    """
    n = 4000
    px = np.full(n, 100.0)
    bursts = [500, 1500, 2500, 3500]
    for b in bursts:
        px[b:] *= 1.02
    episodes_by = {"up": np.array(bursts)}
    z = np.zeros(n, np.int8)
    horizon = 50

    idx_all = np.arange(1, n - 1 - horizon)
    unconditional = forward_return(px, idx_all, horizon).mean()

    pool = control_pool(n, horizon, z, episodes_by, "up", "own", cooldown=200)
    excluded = forward_return(px, np.where(pool)[0], horizon).mean()

    assert unconditional > 0, "the series must rise for the test to mean anything"
    assert excluded < unconditional, (
        "excluding the rising bursts must drag the control mean down; "
        f"got {excluded:.6f} against {unconditional:.6f}"
    )


def test_an_unknown_exclusion_rule_is_refused():
    with pytest.raises(ValueError, match="rule must be one of"):
        control_pool(100, 1, np.zeros(100, np.int8), _episodes(), "up", "sometimes")


def test_both_named_rules_are_accepted():
    for rule in EXCLUSION_RULES:
        control_pool(10_000, 10, np.zeros(10_000, np.int8), _episodes(), "up", rule)


# ---------------------------------------------------------- measurement


def test_entry_is_the_bar_after_the_event():
    """Task C reported +45 where real fills gave -97 because the book used
    the prices the strategy saw when deciding."""
    px = np.array([10.0, 20.0, 22.0, 30.0])
    # event at index 0 -> entry at open[1] = 20, exit at open[1+1] = 22
    assert forward_return(px, np.array([0]), 1)[0] == pytest.approx(0.1)


def test_hour_of_day_is_utc():
    # 1970-01-01T05:00:00Z
    assert hour_of_day(np.array([5 * 3_600_000]))[0] == 5


def test_episodes_collapse_within_the_cooldown():
    mask = np.zeros(1000, bool)
    mask[[10, 11, 12, 400]] = True
    assert list(collapse(mask, cooldown=100)) == [10, 400]


# --------------------------------------------------------------- matching


def test_a_dropped_event_does_not_misalign_the_rest():
    """`kept` is returned explicitly rather than inferred from the control
    count. A dropped event can sit anywhere, so slicing the event array by
    `len(controls) // k` pairs the wrong events with the wrong controls."""
    n = 1000
    decile = np.zeros(n, np.int8)
    hour = np.zeros(n, np.int8)
    # Only one stratum, with far too few members for three events.
    eligible = np.zeros(n, bool)
    eligible[:CONTROLS_PER_EVENT + 2] = True
    events = np.array([100, 200, 300])
    kept, ctrl, dropped = match_controls(
        events, decile, hour, eligible, np.random.default_rng(0)
    )
    assert kept.size * CONTROLS_PER_EVENT == ctrl.size
    assert kept.size + dropped == events.size
    assert set(kept.tolist()) <= set(events.tolist())


def test_controls_are_never_reused_within_a_test():
    n = 5000
    decile = np.zeros(n, np.int8)
    hour = np.zeros(n, np.int8)
    eligible = np.zeros(n, bool)
    eligible[:1000] = True
    events = np.array([2000, 2500, 3000])
    _, ctrl, _ = match_controls(
        events, decile, hour, eligible, np.random.default_rng(1)
    )
    assert len(set(ctrl.tolist())) == ctrl.size, "a control was drawn twice"


def test_matching_is_reproducible_under_a_fixed_seed():
    n = 5000
    decile, hour = np.zeros(n, np.int8), np.zeros(n, np.int8)
    eligible = np.zeros(n, bool)
    eligible[:1000] = True
    events = np.array([2000, 2500])
    a = match_controls(events, decile, hour, eligible, np.random.default_rng(7))[1]
    b = match_controls(events, decile, hour, eligible, np.random.default_rng(7))[1]
    np.testing.assert_array_equal(a, b)


def test_controls_come_from_the_events_own_stratum():
    n = 2000
    decile = np.zeros(n, np.int8)
    decile[1000:] = 3
    hour = np.zeros(n, np.int8)
    eligible = np.ones(n, bool)
    kept, ctrl, _ = match_controls(
        np.array([1500]), decile, hour, eligible, np.random.default_rng(2)
    )
    assert (decile[ctrl] == 3).all()


# ------------------------------------------------------------------ BH


def test_the_correction_uses_the_fixed_family_size_not_the_run_size():
    """A run that silently produced fewer tests must not inflate its own
    significance by shrinking the correction."""
    from research.stage2_event_study import dependence_penalty

    one = benjamini_hochberg([_result(0.02)], yekutieli=False)
    assert one[0].bh_threshold == pytest.approx(FDR_Q / FAMILY_SIZE)
    assert not one[0].bh_significant, "0.02 must not clear 0.1/12"
    del dependence_penalty


def test_the_smallest_p_clears_at_the_rank_one_threshold():
    out = benjamini_hochberg([_result(0.005), _result(0.9)], yekutieli=False)
    sig = [r for r in out if r.bh_significant]
    assert len(sig) == 1 and sig[0].p_value == 0.005


def test_yekutieli_is_strictly_more_conservative():
    """S1, S2 and S3 use different event sets and control draws, so PRDS
    across all twelve is asserted rather than shown. BY holds under
    arbitrary dependence, at a sum(1/i) cost."""
    from research.stage2_event_study import dependence_penalty

    assert dependence_penalty(12, yekutieli=False) == 1.0
    assert dependence_penalty(12, yekutieli=True) == pytest.approx(3.1032, abs=1e-4)
    bh = benjamini_hochberg([_result(0.005)], yekutieli=False)[0]
    by = benjamini_hochberg([_result(0.005)], yekutieli=True)[0]
    assert by.bh_threshold < bh.bh_threshold
    assert bh.bh_significant and not by.bh_significant


def test_the_run_result_is_unchanged_by_the_choice_of_correction():
    """Both the as-specified p=0.00587 and the corrected p=0.0134 fail
    under either correction, which is why adopting the stricter one after
    seeing the result is safe here."""
    for p_value in (0.00587, 0.0134):
        for yek in (False, True):
            (r,) = benjamini_hochberg([_result(p_value)], yekutieli=yek)
            if not yek:
                continue
            assert not r.bh_significant


def test_a_test_below_the_effect_floor_does_not_advance():
    """Significant but smaller than the round trip is a fact about market
    structure, not a candidate."""
    (r,) = benjamini_hochberg([_result(0.001, diff=0.0005)])
    assert r.bh_significant and not r.clears_effect_floor and not r.advances


def test_a_significant_test_above_the_floor_advances():
    (r,) = benjamini_hochberg([_result(0.001, diff=0.0016)])
    assert r.advances


def test_the_family_is_three_situations_by_four_horizons():
    assert FAMILY_SIZE == 3 * len(HORIZONS) == 12


# ---------------------------------------------------------------- welch


def test_welch_reports_no_difference_for_identical_samples():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    t, p, _ = welch(a, a.copy())
    assert t == pytest.approx(0.0) and p == pytest.approx(1.0)


def test_welch_detects_a_clear_shift():
    rng = np.random.default_rng(3)
    a = rng.normal(1.0, 1.0, 500)
    b = rng.normal(0.0, 1.0, 500)
    t, p, _ = welch(a, b)
    assert t > 5 and p < 1e-6


def test_welch_survives_zero_variance():
    a = np.ones(10)
    t, p, df = welch(a, np.ones(10))
    assert (t, p, df) == (0.0, 1.0, 1)


# ------------------------------------------------- review-driven guards


def test_a_gap_that_is_not_declared_is_refused():
    """Row indices are read as minutes, so an undeclared gap silently
    stretches every forward window spanning it."""
    from research.stage2_event_study import verify_continuity

    t = np.arange(0, 10 * 60_000, 60_000)
    verify_continuity(t, declared=())  # contiguous, none declared: fine
    broken = np.delete(t, 5)
    with pytest.raises(ValueError, match="gap set does not match"):
        verify_continuity(broken, declared=())


def test_a_declared_gap_that_has_vanished_is_also_refused():
    """If the series no longer has the gap this project measured, it is
    not the series these results were computed on."""
    from research.stage2_event_study import verify_continuity

    t = np.arange(0, 10 * 60_000, 60_000)
    with pytest.raises(ValueError, match="declared-but-absent"):
        verify_continuity(t, declared=((300_000, 120_000),))


def test_a_declared_gap_is_accepted():
    from research.stage2_event_study import KNOWN_GAPS_MS, verify_continuity

    gap, step = KNOWN_GAPS_MS[0]
    t = np.array([gap - 60_000, gap, gap + step, gap + step + 60_000])
    verify_continuity(t)  # the module default declares exactly this gap


def test_a_gap_of_the_wrong_size_at_a_declared_position_is_refused():
    """Declaring only the position accepts a 3-minute gap where a 2-minute
    one was measured -- a different number of missing bars, with row
    indices still read as minutes."""
    from research.stage2_event_study import verify_continuity

    t = np.array([0, 60_000, 240_000, 300_000])  # 3-minute gap after 60_000
    verify_continuity(t, declared=((60_000, 180_000),))
    with pytest.raises(ValueError, match="unexpected"):
        verify_continuity(t, declared=((60_000, 120_000),))


def test_the_effective_cooldown_grows_with_the_horizon():
    """rd-g fixed a 240-bar cooldown and a 1,440-bar horizon, which cannot
    both hold under a t-test: two events 300 bars apart share 1,140 bars of
    a 1,440-bar window."""
    from research.stage2_event_study import COOLDOWN, effective_cooldown

    assert effective_cooldown(15) == COOLDOWN
    assert effective_cooldown(60) == COOLDOWN
    # h=240 needs 241: an event at i occupies [i+1, i+241], so the next
    # event must start at i+241 or later for the windows to be disjoint.
    assert effective_cooldown(240) == 241
    assert effective_cooldown(1440) == 1441
    assert effective_cooldown(1440, enforce=False) == COOLDOWN


def test_events_spaced_by_the_effective_cooldown_do_not_overlap():
    from research.stage2_event_study import effective_cooldown

    h = 1440
    mask = np.zeros(20_000, bool)
    mask[[1000, 1300, 2600, 5000]] = True
    ev = collapse(mask, cooldown=effective_cooldown(h))
    assert (np.diff(ev) > h).all(), "forward windows must be disjoint"


def test_drawn_controls_are_held_apart_when_spacing_is_required():
    n = 50_000
    decile, hour = np.zeros(n, np.int8), np.zeros(n, np.int8)
    eligible = np.ones(n, bool)
    events = np.array([10_000, 20_000, 30_000])
    _, ctrl, _ = match_controls(
        events, decile, hour, eligible, np.random.default_rng(5), min_spacing=1441
    )
    assert (np.diff(np.sort(ctrl)) >= 1441).all()


def test_welch_reports_a_real_difference_between_unequal_constants():
    """`a=[1,1]`, `b=[2,2]` have different means and zero variance;
    reporting (0, 1) inverts the answer."""
    t, p, _ = welch(np.array([1.0, 1.0]), np.array([2.0, 2.0]))
    assert t == -np.inf and p == 0.0


def test_welch_keeps_fractional_degrees_of_freedom():
    rng = np.random.default_rng(11)
    _, _, df = welch(rng.normal(0, 1, 30), rng.normal(0, 3, 80))
    assert df != int(df), "Welch-Satterthwaite df is fractional and was truncated"


def test_the_boundary_break_counts_as_an_event():
    """rd-g says 'broken by >=0.3%'; a strict comparison drops exactly-0.3%."""
    from research.stage2_event_study import situations

    n = 3000
    o = np.full(n, 100.0)
    hi = np.full(n, 100.0)
    lo = np.full(n, 100.0)
    c = np.full(n, 100.0)
    v = np.ones(n)
    t = np.arange(n, dtype=np.int64) * 60_000
    lo[2000] = 100.0 * 0.997  # exactly 0.3% below the prior low
    out = situations(t, o, hi, lo, c, v)
    assert out["S1 support penetration"][2000], "an exactly-0.3% break must count"


def test_an_event_is_kept_when_a_spacing_valid_subset_exists():
    """Sampling k at random and then thinning drops the event whenever the
    draw happens to collide, even though a valid k-subset exists -- making
    the event sample depend on the draw rather than on availability."""
    n = 100_000
    decile, hour = np.zeros(n, np.int8), np.zeros(n, np.int8)
    eligible = np.zeros(n, bool)
    # Exactly CONTROLS_PER_EVENT candidates that are spacing-valid, plus
    # many that collide with them. A draw-then-thin implementation almost
    # always loses the event here; thin-then-draw never does.
    valid = [i * 5000 for i in range(1, CONTROLS_PER_EVENT + 1)]
    for base in valid:
        eligible[base] = True
        eligible[base + 1 : base + 40] = True
    kept, ctrl, dropped = match_controls(
        np.array([90_000]), decile, hour, eligible,
        np.random.default_rng(4), min_spacing=1441,
    )
    assert dropped == 0 and kept.size == 1
    assert (np.diff(np.sort(ctrl)) >= 1441).all()


def test_the_control_pool_honours_a_horizon_specific_cooldown():
    """An event and a control 300 bars apart have overlapping 1,440-bar
    forward windows -- the same contract violation the event arm was
    fixed for."""
    episodes_by = {"up": np.array([5000])}
    z = np.zeros(20_000, np.int8)
    narrow = control_pool(20_000, 1440, z, episodes_by, "up", "own", cooldown=240)
    wide = control_pool(20_000, 1440, z, episodes_by, "up", "own", cooldown=1441)
    assert narrow[5300], "the base cooldown leaves an overlapping bar eligible"
    assert not wide[5300], "the horizon cooldown must exclude it"


# --------------------------------------------------- the control-bias veto


def test_a_significant_test_with_a_drifted_control_is_vetoed():
    """The whole rd-h finding, as a rule: if the control arm has moved
    further from the unconditional baseline than half the difference
    claimed, the 'effect' is a statement about the control construction.

    The real case: S2 at h=1440 posted +130.50bp, t=3.51, p=5.4e-04 on a
    control sitting 86bp below unconditional.
    """
    (r,) = benjamini_hochberg(
        [_result(0.0005, diff=0.01305, control_mean=-0.007305, unconditional_mean=0.0013)]
    )
    assert r.bh_significant and r.clears_effect_floor
    assert r.control_suspect and not r.advances


def test_a_significant_test_with_an_unbiased_control_still_advances():
    (r,) = benjamini_hochberg(
        [_result(0.0005, diff=0.0016, control_mean=0.0002, unconditional_mean=0.0002)]
    )
    assert r.advances


def test_control_bias_is_measured_against_the_unconditional_baseline():
    r = _result(0.5, diff=0.01, control_mean=-0.004, unconditional_mean=0.001)
    assert r.control_bias == pytest.approx(-0.005)
