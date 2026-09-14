"""Tests for `research.situation_catalogue` — rd-b stage 1.

Two things are worth pinning. The **prior-only** extremes, because a
rolling window that includes the current bar makes penetration impossible
by construction — a bug this project has already written once and fixed.
And the **feasibility ceiling**, because it is the rule that actually
discriminates: rd-b gave stage 1 a floor (≥20 events/year) and no ceiling,
and on the real data every situation clears the floor while most fail the
ceiling.
"""

from __future__ import annotations

import numpy as np
import pytest

from research.situation_catalogue import (
    DEFAULT_ROUND_TRIP,
    episodes,
    feasibility,
    pct_rank_threshold,
    roll_extreme,
    roll_sum_prior,
)


# --------------------------------------------------------- prior-only windows


def test_the_rolling_minimum_excludes_the_current_bar():
    """The bug that makes a penetration study measure nothing: with the
    current bar included, `low < prior_low` can never be true."""
    lows = np.array([10.0, 9.0, 8.0, 7.0, 6.0])
    out = roll_extreme(lows, 2, want_min=True)
    # at index 2 the prior two bars are 10 and 9
    assert out[2] == 9.0
    assert out[3] == 8.0
    assert np.isnan(out[0]) and np.isnan(out[1])


def test_a_new_low_is_below_its_own_prior_minimum():
    lows = np.array([10.0, 10.0, 10.0, 9.0])
    out = roll_extreme(lows, 3, want_min=True)
    assert out[3] == 10.0 and lows[3] < out[3]


def test_the_rolling_maximum_mirrors_the_minimum():
    highs = np.array([1.0, 5.0, 3.0, 2.0, 9.0])
    out = roll_extreme(highs, 2, want_min=False)
    assert out[2] == 5.0
    assert out[4] == 3.0


def test_the_prior_sum_ends_at_the_previous_bar():
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = roll_sum_prior(v, 2)
    # at index 3 the prior two values are v[1]=2 and v[2]=3
    assert out[3] == 5.0
    assert out[4] == 7.0


def test_the_first_valid_prior_sum_is_at_index_w():
    """**The boundary the off-by-one actually moved**, asserted directly.

    The original bug used `idx >= w + 1`, which leaves index `w` NaN and
    delays every volume feature by a bar. Checking only indices 3 and 4
    would pass with the bug still in place -- the guard has to name the
    first valid index or it is inert against the thing it exists for.
    """
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = roll_sum_prior(v, 2)
    assert np.isnan(out[0]) and np.isnan(out[1]), "no full prior window yet"
    assert out[2] == 3.0, "index w must carry the first full window, a[0] + a[1]"


# ------------------------------------------------------------ episodes


def test_consecutive_hits_inside_the_cooldown_are_one_episode():
    assert episodes(np.array([0, 1, 2, 3]), cooldown=10) == 1


def test_hits_separated_by_the_cooldown_are_separate():
    assert episodes(np.array([0, 10, 20]), cooldown=10) == 3


def test_a_hit_one_bar_short_of_the_cooldown_does_not_count():
    assert episodes(np.array([0, 9]), cooldown=10) == 1


def test_no_hits_is_no_episodes():
    assert episodes(np.array([], dtype=int)) == 0


# --------------------------------------------------- the feasibility ceiling


def test_a_rare_situation_is_feasible():
    """172/year -- support penetration on the real data."""
    drag, verdict = feasibility(172)
    assert verdict == "feasible"
    assert 0.20 < drag < 0.22


def test_a_near_continuous_situation_is_cost_infeasible():
    """2,130/year -- the fair value gap on the real data. At a 12bp round
    trip that is 256% a year in costs before any edge exists."""
    drag, verdict = feasibility(2130)
    assert verdict == "COST-INFEASIBLE"
    assert drag > 2.5


def test_the_verdict_is_monotonic_in_frequency():
    order = ["feasible", "cost-hostile", "COST-INFEASIBLE"]
    seen = [feasibility(f)[1] for f in (50, 100, 200, 400, 600, 900, 2000)]
    ranks = [order.index(v) for v in seen]
    assert ranks == sorted(ranks), seen


def test_too_rare_beats_the_cost_verdict():
    """Below the measurability floor the cost question is moot."""
    assert feasibility(1)[1] == "too rare"


def test_a_cheaper_venue_moves_the_ceiling():
    """The ceiling is a statement about costs, not about the situation.
    Halve the round trip and a cost-hostile situation becomes feasible --
    which is why the assumed figure has to be cited, never guessed."""
    assert feasibility(400)[1] == "cost-hostile"
    assert feasibility(400, round_trip=DEFAULT_ROUND_TRIP / 4)[1] == "feasible"


def test_the_default_round_trip_is_the_measured_one():
    """`scalp-s9` measured it. A silently retuned constant here would move
    every verdict in the table."""
    assert DEFAULT_ROUND_TRIP == pytest.approx(0.0012)


# ---------------------------------------------------------------- misc


def test_a_quantile_threshold_ignores_nans():
    a = np.array([np.nan, 1.0, 2.0, 3.0, 4.0])
    assert pct_rank_threshold(a, 0.5) == 2.5


def test_an_all_nan_series_yields_nan_rather_than_raising():
    assert np.isnan(pct_rank_threshold(np.array([np.nan, np.nan]), 0.5))


# --------------------------------------------------- no future data leaks


def test_the_trailing_quantile_uses_only_prior_bars():
    """The defect this replaced: a whole-array quantile classified a bar
    using data from after it, which moved the episode counts and therefore
    every `feasibility()` verdict."""
    from research.situation_catalogue import trailing_quantile

    a = np.arange(100.0)
    out = trailing_quantile(a, 0.5, window=10, step=10)
    # the first full window is a[0:10]; its median is assigned from index 10
    assert np.isnan(out[:10]).all(), "bars before the first full window must be NaN"
    assert out[10] == np.quantile(a[0:10], 0.5)
    assert out[20] == np.quantile(a[10:20], 0.5)


def test_appending_future_bars_does_not_change_an_earlier_threshold():
    """The property, asserted directly rather than inferred. This is what
    a whole-array quantile violates."""
    from research.situation_catalogue import trailing_quantile

    short = np.arange(60.0)
    long = np.arange(600.0)
    a = trailing_quantile(short, 0.9, window=10, step=10)
    b = trailing_quantile(long, 0.9, window=10, step=10)
    np.testing.assert_array_equal(a[:60], b[:60])


def test_a_whole_array_quantile_would_fail_that_property():
    """Proves the test above can fail — the guard is only worth having if
    the thing it forbids actually trips it."""
    short, long = np.arange(60.0), np.arange(600.0)
    assert pct_rank_threshold(short, 0.9) != pct_rank_threshold(long, 0.9)


def test_the_threshold_is_constant_between_recalibrations():
    """A trader recalibrates periodically, not every bar; holding the
    threshold is what makes this cheap on 3.66M bars."""
    from research.situation_catalogue import trailing_quantile

    out = trailing_quantile(np.arange(100.0), 0.5, window=10, step=10)
    assert len(set(out[10:20])) == 1


def test_a_window_of_all_nans_yields_nan_rather_than_guessing():
    from research.situation_catalogue import trailing_quantile

    a = np.full(40, np.nan)
    assert np.isnan(trailing_quantile(a, 0.5, window=10, step=10)).all()
