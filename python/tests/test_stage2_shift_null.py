"""Tests for `research.stage2_shift_null` — rd-b stage 2, corrected.

Three nulls have now been tried on the same twelve tests, and **each
fixed its predecessor's flaw while carrying one of its own**. The tests
here pin the two that were found by measurement rather than by reading:

- **The seam.** The first circular shift wrapped the *exit* index, so an
  entry near the end of the series collected a return that ran back to
  the start — one **−87.4%** observation on this data. It biased every
  null draw in proportion to `horizon / n`, and it produced an
  `ADVANCE`. Caught because rd-j §6 registered "suspect the null's seam"
  *before* the run.
- **The volatility stratum.** A shift null preserves drift, era and
  autocorrelation, and still compares the market's most volatile hours
  against all hours. S3 sits in the top decile **97.3%** of the time, and
  matching on it moved `p` from 5e-04 to 0.075.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from research import stage2_shift_null
from research.stage2_event_study import KNOWN_GAPS_MS, SYMBOL
from research.stage2_shift_null import (
    BARS_PER_DAY,
    DEFAULT_PERMUTATIONS,
    EFFECT_FLOOR,
    FAMILY_SIZE,
    MIN_PERMUTATIONS_FOR_VERDICT,
    SHIFT_MODES,
    ShiftTest,
    benjamini_yekutieli,
    event_standard_error,
    matched_null,
    overlap_block,
    permutation_p,
    permutation_test,
    run,
    shift_offsets,
)


def _test(p, observed=0.005, null_mean=0.0, unconditional=0.0, **over):
    base = dict(
        situation="S", horizon=240, n_events=500, observed=observed,
        null_mean=null_mean, null_sd=0.001, unconditional=unconditional,
        p_value=p, permutations=DEFAULT_PERMUTATIONS,
    )
    base.update(over)
    return ShiftTest(**base)


# ------------------------------------------------------------- the seam


def test_the_null_does_not_wrap_the_exit_index():
    """The regression for the real bug. On a monotonically rising series
    every forward return is positive; a wrapped exit would produce a large
    negative draw and drag the null below every observation."""
    n = 5000
    px = np.linspace(100.0, 200.0, n)
    events = np.array([1000, 2000, 3000])
    offsets = np.array([500, 1500, -700])
    _, null = permutation_test(px, events, 240, offsets)
    assert (null > 0).all(), "a negative draw on a rising series means the exit wrapped"


def test_the_null_centres_on_the_unconditional_return():
    """The property the seam violated, asserted directly: a shift null
    must reproduce the series' own mean, because it only relabels which
    return each event collects."""
    rng = np.random.default_rng(0)
    n = 40_000
    px = 100 * np.exp(np.cumsum(rng.normal(2e-6, 1e-4, n)))
    events = rng.integers(1, n - 2000, size=300)
    horizon = 240
    starts = np.arange(1, n - 1 - horizon + 1)
    uncond = ((px[starts + horizon] - px[starts]) / px[starts]).mean()
    _, null = permutation_test(px, events, horizon, shift_offsets(n, 1441, 400, rng))
    assert null.mean() == pytest.approx(uncond, abs=2e-5)


def test_a_horizon_too_long_for_the_series_is_refused():
    with pytest.raises(ValueError, match="too short"):
        permutation_test(np.arange(10.0) + 100, np.array([1, 2]), 20, np.array([3]))


# ---------------------------------------------------------- shift offsets


def test_free_offsets_stay_inside_the_bounds():
    off = shift_offsets(100_000, 1441, 500, np.random.default_rng(1), "free")
    assert off.min() >= 1441 and off.max() < 100_000 - 1441


def test_day_offsets_preserve_hour_of_day():
    """rd-b measured a real intraday cycle, so a free shift smears exactly
    the structure a situation might be exploiting."""
    off = shift_offsets(1_000_000, 1441, 500, np.random.default_rng(2), "day")
    assert (off % BARS_PER_DAY == 0).all()


def test_an_unknown_shift_mode_is_refused():
    with pytest.raises(ValueError, match="mode must be one of"):
        shift_offsets(100_000, 1441, 10, np.random.default_rng(3), "sideways")


def test_both_named_modes_are_accepted():
    for mode in SHIFT_MODES:
        shift_offsets(1_000_000, 1441, 10, np.random.default_rng(4), mode)


def test_a_series_too_short_for_the_bounds_is_refused():
    with pytest.raises(ValueError, match="too short"):
        shift_offsets(2000, 1441, 10, np.random.default_rng(5), "free")


# ------------------------------------------------------ permutation p


def test_a_p_value_of_exactly_zero_is_not_attainable():
    """`(1 + k) / (B + 1)`. A finite permutation set cannot support p=0,
    and reporting one would overstate the evidence."""
    null = np.zeros(999)
    assert permutation_p(10.0, null) == pytest.approx(1.0 / 1000.0)


def test_an_observation_at_the_null_centre_is_maximally_unsurprising():
    # Not exactly 1.0: the null's own mean is not exactly 0 in floating
    # point, so the draw nearest the centre falls fractionally inside it.
    assert permutation_p(0.0, np.linspace(-1, 1, 999)) > 0.99


def test_the_test_is_two_sided():
    null = np.linspace(-1, 1, 999)
    assert permutation_p(-0.9, null) == pytest.approx(permutation_p(0.9, null), abs=2e-3)


def test_p_is_centred_on_the_nulls_own_mean_not_on_zero():
    """A null with a non-zero centre is normal — the unconditional drift
    is positive — so comparing against zero would call drift an effect."""
    null = np.linspace(9.0, 11.0, 999)
    assert permutation_p(10.0, null) == pytest.approx(1.0)


# ------------------------------------------------------------- verdicts


def test_an_effect_under_the_cost_floor_does_not_advance():
    r, = benjamini_yekutieli([_test(1e-5, observed=EFFECT_FLOOR / 2)])
    assert r.significant and not r.clears_effect_floor and not r.advances


def test_a_significant_effect_over_the_floor_advances():
    r, = benjamini_yekutieli([_test(1e-5, observed=EFFECT_FLOOR * 2)])
    assert r.advances


def test_a_null_that_misses_the_unconditional_mean_is_vetoed():
    """`control_suspect`'s replacement. The seam bug produced exactly
    this: a null centred well below the series' own mean."""
    # bias -0.0025 against an effect of 0.0040: comfortably over half,
    # rather than pinned on the boundary where float noise decides it.
    r, = benjamini_yekutieli(
        [_test(1e-5, observed=0.0030, null_mean=-0.0010, unconditional=0.0015)]
    )
    assert r.significant and r.clears_effect_floor
    assert r.null_suspect and not r.advances


def test_the_null_suspect_threshold_is_half_the_effect():
    """Stated exactly, the way rd-h's `control_suspect` had to be: half
    the claimed effect, not all of it."""
    just_over = _test(1e-5, observed=0.0030, null_mean=0.0, unconditional=-0.0016)
    just_under = _test(1e-5, observed=0.0030, null_mean=0.0, unconditional=-0.0014)
    assert abs(just_over.null_bias) > abs(just_over.effect) / 2 and just_over.null_suspect
    assert abs(just_under.null_bias) < abs(just_under.effect) / 2 and not just_under.null_suspect


def test_a_faithful_null_is_not_vetoed():
    r, = benjamini_yekutieli(
        [_test(1e-5, observed=0.0030, null_mean=0.0013, unconditional=0.0013)]
    )
    assert not r.null_suspect and r.advances


def test_the_correction_uses_the_fixed_family_size():
    r, = benjamini_yekutieli([_test(0.02)])
    assert r.bh_threshold == pytest.approx(0.10 / (FAMILY_SIZE * 3.1032), rel=1e-3)
    assert not r.significant, "0.02 must not clear the BY rank-1 threshold"


def test_the_family_is_twelve():
    assert FAMILY_SIZE == 12


# --------------------------------------------------------- matched null


def _strata(n):
    dec = np.zeros(n, np.int8)
    dec[n // 2 :] = 1
    return dec, np.zeros(n, np.int8)


def test_the_matched_null_draws_from_the_events_own_stratum():
    """The confound the shift null leaves open: events do not sit in a
    random volatility stratum, and S3 is top-decile 97.3% of the time."""
    n = 4000
    px = np.concatenate([np.full(n // 2, 100.0), np.linspace(100.0, 300.0, n // 2)])
    dec, hr = _strata(n)
    events = np.array([2200, 2600, 3000])  # all in the rising stratum
    _, null, keep = matched_null(px, events, 50, dec, hr, np.random.default_rng(6), 200)
    assert keep.size == events.size
    assert (null > 0).all(), "a draw from the flat stratum would give zero"


def test_an_event_with_an_empty_stratum_is_dropped_and_counted():
    """Drawing from a neighbouring stratum would silently undo the
    matching this test exists for."""
    n = 4000
    px = np.linspace(100.0, 200.0, n)
    dec = np.zeros(n, np.int8)
    dec[3000] = 7  # a stratum of exactly one bar, which is the event itself
    hr = np.zeros(n, np.int8)
    # Horizon short enough that every event has a forward window, so the
    # only reason to drop one is the empty stratum -- empty because the
    # event cannot be its own control.
    events = np.array([1000, 1500, 2000, 3000])
    _, _, keep = matched_null(px, events, 50, dec, hr, np.random.default_rng(7), 20)
    assert events.size - keep.size == 1
    assert 3000 not in keep, "the dropped event must be the one with no control"


def test_an_event_is_never_drawn_as_its_own_control():
    """Putting the observation inside the null shrinks the difference
    being measured. Distinct from rd-h's failure: this removes the event
    bars themselves (0.02% of the real series), not their neighbourhood
    (~15%), so there is no complement to be directionally biased."""
    n = 2000
    px = np.linspace(100.0, 200.0, n)
    dec, hr = np.zeros(n, np.int8), np.zeros(n, np.int8)
    # A stratum of three bars: two events and one non-event. Every draw
    # must land on the non-event.
    for i in (500, 600, 900):
        dec[i] = 3
    _, null, keep = matched_null(
        px, np.array([500, 600]), 50, dec, hr, np.random.default_rng(11), 30
    )
    assert keep.size == 2
    only = (px[951] - px[901]) / px[901]
    assert null.min() == pytest.approx(only) and null.max() == pytest.approx(only), (
        "bar 900 is the only admissible control; 500 and 600 are the events"
    )


def test_an_event_without_its_own_forward_window_is_dropped_too():
    """A non-empty pool is not enough: the event itself has to have
    somewhere to exit. Checking only the pool let an event near the end of
    the series index past the array."""
    n = 4000
    px = np.linspace(100.0, 200.0, n)
    dec, hr = np.zeros(n, np.int8), np.zeros(n, np.int8)
    events = np.array([100, 200, 3990])
    _, _, keep = matched_null(px, events, 50, dec, hr, np.random.default_rng(10), 20)
    assert events.size - keep.size == 1 and 3990 not in keep


def test_too_few_matchable_events_is_refused_rather_than_reported():
    n = 1000
    px = np.linspace(100.0, 200.0, n)
    dec = np.full(n, -1, np.int8)  # nothing eligible anywhere
    with pytest.raises(ValueError, match="stratum"):
        matched_null(px, np.array([100, 200]), 10, dec, np.zeros(n, np.int8),
                     np.random.default_rng(8), 10)


def test_the_matched_and_shift_nulls_disagree_when_strata_differ():
    """The whole rd-k finding in miniature: events concentrated in the
    high-return stratum beat a global shift and do not beat a
    stratum-matched draw."""
    n = 6000
    px = np.concatenate([np.full(n // 2, 100.0), np.linspace(100.0, 400.0, n // 2)])
    dec, hr = _strata(n)
    events = np.array([3200, 3600, 4000, 4400])
    rng = np.random.default_rng(9)
    _, shift = permutation_test(px, events, 50, shift_offsets(n, 100, 300, rng, "free"))
    obs, matched, _keep = matched_null(px, events, 50, dec, hr, rng, 300)
    assert obs - shift.mean() > obs - matched.mean(), (
        "the global shift must overstate the effect when events cluster in a stratum"
    )


# ------------------------------------------------- review-driven guards


def test_the_matched_null_is_reachable_from_run():
    """rd-k's deciding result was produced by a scratch script and could
    not be reproduced by any repo command until review caught it. A result
    nobody can reproduce with a command is not a reproducible result."""
    from research.stage2_shift_null import NULL_MODES

    assert "matched" in NULL_MODES


def test_a_permutation_count_that_cannot_reach_the_threshold_is_refused():
    """`1/(B+1)` is the smallest attainable p. Below B=372 no test can
    clear the rank-1 BY threshold of 0.002685 however strong it is, so the
    verdict would be an artefact of the permutation count."""
    from research.stage2_shift_null import MIN_PERMUTATIONS_FOR_VERDICT, run

    # The smallest attainable p is 1/(B+1), not 1/B.
    threshold = 0.10 / (FAMILY_SIZE * 3.1032)
    assert 1.0 / (MIN_PERMUTATIONS_FOR_VERDICT + 1) <= threshold
    assert 1.0 / MIN_PERMUTATIONS_FOR_VERDICT > threshold, "the constant is off by one"
    for bad in (0, 1, 371):
        with pytest.raises(ValueError, match="cannot reach"):
            run(permutations=bad)


def test_an_unknown_null_mode_is_refused_before_any_data_is_loaded():
    from research.stage2_shift_null import run

    with pytest.raises(ValueError, match="mode must be one of"):
        run(mode="handwave")


def test_the_null_suspect_veto_does_not_apply_to_the_matched_null():
    """A volatility+hour matched null is SUPPOSED to differ from the
    unconditional mean — that difference IS the confound being held fixed,
    and it reaches +18.57bp at h=1440 on the real data. Vetoing on it
    would reject a test precisely for controlling what it controls."""
    shift = _test(1e-5, observed=0.0030, null_mean=-0.0010, unconditional=0.0015,
                  null_mode="free")
    matched = _test(1e-5, observed=0.0030, null_mean=-0.0010, unconditional=0.0015,
                    null_mode="matched")
    assert shift.null_suspect and not matched.null_suspect
    assert matched.null_bias == shift.null_bias, "the number is still reported"


def test_the_shift_nulls_keep_the_veto():
    for mode in ("free", "day"):
        r = _test(1e-5, observed=0.0030, null_mean=-0.0010, unconditional=0.0015,
                  null_mode=mode)
        assert r.null_suspect


# ------------------------------- the event arm's own standard error


def test_the_overlap_block_comes_from_the_two_constants():
    """`ceil(horizon / cooldown)` -- 1 where forward windows are disjoint,
    6 at h=1440 against the 240-bar cooldown. Derived rather than chosen,
    so it cannot drift away from the event construction."""
    assert overlap_block(15) == 1
    assert overlap_block(60) == 1
    assert overlap_block(240) == 1
    assert overlap_block(1440) == 6
    assert overlap_block(241) == 2


def test_a_non_positive_cooldown_is_refused():
    with pytest.raises(ValueError, match="cooldown must be positive"):
        overlap_block(240, 0)


def test_with_no_overlap_the_bootstrap_is_the_closed_form_exactly():
    """Not approximately: at block 1 there is nothing to correct, and
    returning a noisy bootstrap estimate instead would make the h=15/60/240
    figures irreproducible for no gain."""
    r = np.random.default_rng(1).normal(0, 0.01, 500)
    assert event_standard_error(r, 1, np.random.default_rng(2)) == pytest.approx(
        r.std(ddof=1) / np.sqrt(r.size)
    )


def test_the_bootstrap_widens_the_error_on_positively_correlated_returns():
    """**The point of the correction.** Overlapping forward windows share
    bars, so their covariance is real and positive; the closed form ignores
    it and understates the error, which understates the cost."""
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.01, 200)
    r = np.repeat(base, 6)  # six consecutive events sharing one window
    closed = r.std(ddof=1) / np.sqrt(r.size)
    boot = event_standard_error(r, 6, np.random.default_rng(4), draws=800)
    assert boot > 1.5 * closed


def test_the_bootstrap_is_close_to_the_closed_form_on_independent_draws():
    """The correction must not inflate an error that needs no correcting,
    or every horizon would pay for the one that does."""
    r = np.random.default_rng(5).normal(0, 0.01, 1200)
    closed = r.std(ddof=1) / np.sqrt(r.size)
    boot = event_standard_error(r, 6, np.random.default_rng(6), draws=1500)
    assert boot == pytest.approx(closed, rel=0.20)


def test_a_block_longer_than_the_sample_is_clamped():
    r = np.random.default_rng(7).normal(0, 0.01, 10)
    assert event_standard_error(r, 50, np.random.default_rng(8), draws=100) > 0


def test_a_single_return_has_no_standard_error():
    with pytest.raises(ValueError, match="at least 2 returns"):
        event_standard_error(np.array([0.01]), 1, np.random.default_rng(9))


def _synthetic_series(tmp_path, sigma=0.002, n_half=30_000, seed=0):
    """A 1m series `run` will accept, written to a real SQLite file.

    It has to reproduce **the declared gap exactly** -- `verify_continuity`
    fails closed on any other gap set -- and be long enough for the
    43,200-bar trailing quantile S3's threshold needs, so this is 60,000
    bars around the real gap rather than a toy.
    """
    gap_start, gap_step = KNOWN_GAPS_MS[0]
    t = np.concatenate([
        gap_start - np.arange(n_half - 1, -1, -1) * 60_000,   # ends AT gap_start
        gap_start + gap_step + np.arange(n_half) * 60_000,
    ])
    n = t.size
    rng = np.random.default_rng(seed)
    c = 10_000 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    o = np.roll(c, 1)
    o[0] = c[0]
    wig = np.abs(rng.normal(0, sigma * 3, n))
    h, l = np.maximum(o, c) * (1 + wig), np.minimum(o, c) * (1 - wig)
    v = np.abs(rng.lognormal(0, 1.0, n))
    path = tmp_path / "klines.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER,"
        " open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT)"
    )
    conn.executemany(
        "INSERT INTO klines VALUES (?,?,?,?,?,?,?,?)",
        [
            (SYMBOL, "1m", int(t[i]), str(o[i]), str(h[i]), str(l[i]),
             str(c[i]), str(v[i]))
            for i in range(n)
        ],
    )
    conn.commit()
    conn.close()
    return str(path)


def test_the_synthetic_series_exercises_all_twelve_cells(tmp_path):
    """The guard below is only a guard if `run` really runs. A fixture
    that raised, or produced a short family, would make the next test pass
    for the wrong reason."""
    out = run(_synthetic_series(tmp_path), mode="matched",
              permutations=MIN_PERMUTATIONS_FOR_VERDICT)
    assert len(out) == FAMILY_SIZE
    assert all(r.n_events >= 2 for r in out)


@pytest.mark.parametrize("mode", ["matched", "free"])
def test_the_bootstrap_does_not_disturb_run_s_null_draws(tmp_path, monkeypatch, mode):
    """**A reported diagnostic must not move the result**, asserted at the
    level where the regression actually happened.

    The first version passed `run`'s own `rng` to `event_standard_error`,
    so every shift offset and matched control drawn after its first call
    was silently re-rolled -- S3 h=1440 went from 33.07bp / p=1.25e-02 to
    32.13bp / p=6.50e-03, turning a non-result into an ADVANCE.

    Testing `event_standard_error` in isolation cannot catch that, because
    the fault is in **which generator `run` hands it**. So this replaces it
    with a version that burns a large number of draws from whatever
    generator it receives: if that is the null stream, every p-value moves.
    """
    db = _synthetic_series(tmp_path)
    kw = dict(mode=mode, permutations=MIN_PERMUTATIONS_FOR_VERDICT)
    baseline = run(db, **kw)

    real = stage2_shift_null.event_standard_error

    def greedy(returns, block, rng, draws=2000):
        rng.integers(0, 10, 10_000)          # burn the stream it was given
        return real(returns, block, rng, draws)

    monkeypatch.setattr(stage2_shift_null, "event_standard_error", greedy)
    after = run(db, **kw)

    assert [r.p_value for r in after] == [r.p_value for r in baseline]
    assert [r.null_mean for r in after] == [r.null_mean for r in baseline]
    assert [r.observed for r in after] == [r.observed for r in baseline]
