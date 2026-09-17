"""Tests for `research.krx_conjunction`.

The module asks whether conditions *combined* say more than any one of
them, so the properties that matter are the ones that would let a
combination look better than it is:

- **The decomposition must rebuild the close-to-close move**, or every
  figure derived from it is meaningless.
- **No look-ahead.** A decision from today's close is acted on at
  tomorrow's open, a price nobody had seen.
- **The cost test is on the MAGNITUDE of the excess.** An earlier version
  tested `mean - base > floor` and so could only see a long edge — while
  every conditional mean in the first real run was negative.
- **A median split has no free parameter**, which is what keeps the
  conjunction measurable where Task C's extreme thresholds left it at two
  firings in 2,544 bars.
"""

from __future__ import annotations

import numpy as np
import pytest

from research.krx_conjunction import (
    COST_FLOOR_BP,
    HOLD_DAYS,
    LOOKBACK_DAYS,
    Outcome,
    Panel,
    _above_median,
    _summarise,
    build_conditions,
    decomposition_gap,
    forward_total,
    independence,
    interleaved_sessions,
    measure,
)


def _panel(open_px: list[list[float]], close_px: list[list[float]]) -> Panel:
    o = np.array(open_px, dtype=float)
    c = np.array(close_px, dtype=float)
    pc = np.vstack([np.full((1, o.shape[1]), np.nan), c[:-1]])
    dates = list(range(o.shape[0]))
    codes = [f"S{i}" for i in range(o.shape[1])]
    return Panel(dates, codes, o, c, pc)


# ------------------------------------------------- the decomposition


def test_the_two_components_rebuild_the_close_to_close_move():
    """**If this fails nothing else in the module means anything.** The
    overnight and intraday pieces must compose multiplicatively into the
    move they were split out of."""
    p = _panel([[100.0], [103.0], [99.0]], [[102.0], [101.0], [105.0]])
    c2c = p.close_px[1:, 0] / p.close_px[:-1, 0] - 1
    rebuilt = ((1 + p.overnight) * (1 + p.intraday) - 1)[1:, 0]
    assert rebuilt == pytest.approx(c2c)


def test_the_overnight_leg_is_the_gap_and_the_intraday_leg_is_the_session():
    p = _panel([[100.0], [110.0]], [[105.0], [121.0]])
    assert p.overnight[1, 0] == pytest.approx(110 / 105 - 1)
    assert p.intraday[1, 0] == pytest.approx(121 / 110 - 1)


def test_the_first_day_has_no_overnight_leg():
    """There is no previous close to gap from, and inventing one would
    manufacture a return."""
    p = _panel([[100.0], [110.0]], [[105.0], [121.0]])
    assert np.isnan(p.overnight[0, 0])


def test_a_consistent_panel_reports_no_gap():
    p = _panel([[100.0], [103.0], [99.0]], [[102.0], [101.0], [105.0]])
    assert decomposition_gap(p) < 1e-12


def test_a_panel_whose_overnight_leg_gaps_from_the_wrong_close_is_caught():
    """**The version of this check that is not a tautology.** Comparing the
    composed legs against a close-to-close return *also* built from
    `prev_close` is an algebraic identity and can never fail — the first
    version of this test proved it, at 2.2e-16 on a deliberately broken
    panel. Comparing against the previous row's close instead asks the
    thing that can be wrong: does the gap span the night immediately
    before it?"""
    p = _panel([[100.0], [103.0]], [[102.0], [101.0]])
    broken = Panel(
        p.dates, p.codes, p.open_px, p.close_px,
        np.array([[np.nan], [77.0]]),          # not yesterday's close
    )
    assert decomposition_gap(broken) > 1e-9


def test_a_panel_with_nothing_comparable_refuses_rather_than_passing():
    """A gap of `nan` would compare false against the threshold and let the
    run proceed, which is the wrong direction for a fail-closed check."""
    p = _panel([[100.0]], [[102.0]])
    assert not np.isfinite(decomposition_gap(p))
    assert decomposition_gap(p) > 1e-9


# ------------------------------------------ the daily-axis gap rule


def test_a_session_dropped_from_inside_the_span_is_found():
    """**rd-p's gap rule, on the daily axis.** An inner join on date means
    one name missing one session drops it for everyone, and the overnight
    legs either side of the hole then span two nights while being reported
    as one."""
    assert interleaved_sessions([1, 2, 4, 5], [1, 2, 3, 4, 5]) == [3]


def test_an_end_effect_is_not_a_hole():
    """718 of the real panel's dates are dropped and **none is a hole** —
    they are all the eight newer names listing later. Refusing on an end
    effect would refuse the real run."""
    assert interleaved_sessions([3, 4, 5], [1, 2, 3, 4, 5, 6, 7]) == []


def test_no_dates_is_not_an_error():
    assert interleaved_sessions([], [1, 2, 3]) == []


# -------------------------------------------------------- no look-ahead


def test_a_decision_is_acted_on_at_a_price_it_could_not_have_seen():
    """**The look-ahead guard.** A condition computed from day `t`'s close
    must be filled at day `t+1`'s open."""
    p = _panel([[10.0], [20.0], [40.0]], [[11.0], [22.0], [44.0]])
    fwd = forward_total(p, 1)
    # From t=0: enter at open[1]=20, exit at close[1]=22 -> +10%.
    assert fwd[0, 0] == pytest.approx(0.1)
    assert fwd[0, 0] != pytest.approx(22 / 11 - 1), "used its own day's close"


def test_holding_longer_spans_the_overnight_gaps():
    """h=1 is the intraday leg alone; h=5 carries four gaps with it, which
    is why their baselines differ and why the module labels each."""
    p = _panel(
        [[10.0], [20.0], [30.0], [40.0]], [[11.0], [22.0], [33.0], [44.0]]
    )
    one, three = forward_total(p, 1), forward_total(p, 3)
    assert one[0, 0] == pytest.approx(22 / 20 - 1)
    assert np.isnan(three[0, 0]) or three[0, 0] != pytest.approx(one[0, 0])


# --------------------------------------------- the cost test's direction


def test_the_cost_test_is_on_the_MAGNITUDE_of_the_excess():
    """**A real defect in the first version.** It tested `mean - base >
    floor`, so it recognised a long edge only — and every conditional mean
    in the first real run was negative. The whole result was short, and
    the report showed nothing."""
    short = Outcome("x", 500, 250, -20.0, -3.0, 0.001)
    assert short.excess_bp(-3.0) == pytest.approx(-17.0)
    assert short.clears_cost(-3.0), "a short edge must count"
    assert short.direction(-3.0) == "SHORT"

    long_ = Outcome("y", 500, 250, +20.0, 3.0, 0.001)
    assert long_.clears_cost(3.0) and long_.direction(3.0) == "LONG"


def test_an_excess_under_the_floor_does_not_clear_in_either_direction():
    """The real h=1 result sits here: −11.4bp of excess against a 13bp
    round trip. Close is not clearing."""
    near = Outcome("x", 2360, 1139, -14.4, None, 0.008)
    assert abs(near.excess_bp(-3.0)) == pytest.approx(11.4)
    assert not near.clears_cost(-3.0)


def test_an_unmeasurable_outcome_clears_nothing():
    assert not Outcome("x", 1, 1, None, None, None).clears_cost(0.0)


# ----------------------------------- the date is the unit of the test


def test_a_days_names_count_as_ONE_observation_not_ten():
    """**The independence correction, and it is not cosmetic.** Ten names
    on one day share a market-wide move, so pooling them as ten draws
    understates the standard error the same way S13's overlapping
    excursions did. On the real panel it moved a p-value from 0.016 to
    0.182 — and another from 0.113 to 0.039, so it is not a uniform
    haircut either."""
    fwd = np.array([[0.01, 0.03], [0.02, 0.02]])
    sel = np.ones(fwd.shape, dtype=bool)
    out = _summarise("x", fwd, sel)
    assert out.firings == 4, "four name-days"
    assert out.dates == 2, "but only two independent observations"


def test_a_date_where_nothing_fired_does_not_become_an_observation():
    fwd = np.array([[0.01], [0.02], [0.03]])
    sel = np.array([[True], [False], [True]])
    out = _summarise("x", fwd, sel)
    assert out.firings == 2 and out.dates == 2


def test_each_date_weighs_the_same_regardless_of_how_many_names_fired():
    """A day on which eight names qualify must not outvote a day on which
    one does — otherwise the mean is a turnout-weighted figure rather than
    the average outcome of taking the trade."""
    fwd = np.array(
        [[0.10, 0.10, 0.10], [0.10, 0.10, 0.10], [-0.30, np.nan, np.nan]]
    )
    sel = np.array(
        [[True, True, True], [True, True, True], [True, False, False]]
    )
    out = _summarise("x", fwd, sel)
    assert out.firings == 7, "seven name-days, six of them on two dates"
    # Equal-weighted by date: (0.10 + 0.10 + -0.30) / 3 = -0.0333...
    # Pooling the name-days instead gives (6*0.10 - 0.30)/7 = +0.0429 —
    # a different figure AND the opposite sign.
    assert out.mean_bp == pytest.approx(-0.1 / 3 * 1e4)


def test_fewer_than_three_dates_is_not_measured():
    fwd = np.array([[0.01], [0.02]])
    out = _summarise("x", fwd, np.ones(fwd.shape, dtype=bool))
    assert out.dates == 2 and out.mean_bp is None and out.p_value is None


# ------------------------------------------------- the median split


def test_a_median_split_has_no_threshold_to_search():
    """Half the universe by construction, fixed by the data rather than
    chosen — so there is nothing to overfit and nothing to add to `N`."""
    row = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _above_median(row).tolist() == [False, False, False, True, True]


def test_a_cross_section_too_small_to_rank_fires_nothing():
    """Two names give a median one of them sits exactly on; calling either
    'above' would invent a ranking."""
    assert not _above_median(np.array([1.0, 2.0])).any()


def test_missing_names_do_not_shift_the_split_onto_others():
    row = np.array([1.0, np.nan, 3.0, 5.0, np.nan, 9.0])
    out = _above_median(row)
    assert not out[1] and not out[4], "a missing name cannot fire"
    assert out.sum() == 2, "half of the four present names"


# ------------------------------------------------ conjunction mechanics


def _conditions_panel(days: int = LOOKBACK_DAYS + 40, names: int = 6) -> Panel:
    rng = np.random.default_rng(11)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, (days, names)), axis=0)
    open_ = close * (1 + rng.normal(0, 0.003, (days, names)))
    return _panel(open_.tolist(), close.tolist())


def test_every_condition_is_adopted_and_names_its_source():
    """Nothing here was searched for — that is what keeps the study at one
    hypothesis rather than a new search, so each condition must be able to
    say where it came from."""
    conds = build_conditions(_conditions_panel())
    assert len(conds) == 4
    for c in conds:
        assert c.source.strip(), c.name
        assert c.mask.dtype == bool


def test_conditions_fire_often_enough_to_be_measurable():
    """**Task C's failure mode, designed out.** Extreme thresholds left a
    four-way conjunction at 2 firings in 2,544 bars; median splits put
    each condition near half the universe."""
    panel = _conditions_panel()
    conds = build_conditions(panel)
    for c in conds:
        rate = c.mask.sum() / c.mask.size
        assert 0.15 < rate < 0.65, f"{c.name} fires on {rate:.1%}"


def test_eleven_combinations_are_measured_and_all_of_them_are_reported():
    """**The selection problem, made visible rather than hidden.** Four
    singles, six pairs, the four-way conjunction and the unconditional
    baseline — quoting the best of eleven without saying eleven were tried
    is the error this project corrects everywhere else, so the count is
    pinned here and BH runs over all of them."""
    panel = _conditions_panel()
    rows = measure(panel, build_conditions(panel), 1)
    assert rows[0].label == "(unconditional)"
    assert len(rows) == 1 + 4 + 6 + 1
    labels = [r.label for r in rows[1:]]
    assert len(set(labels)) == 11, "a combination measured twice inflates BH"


def test_two_conditions_do_not_measure_their_pair_twice():
    """At four conditions the pair size and the full size differ, so the
    deduplication is inert and a test at four cannot see it — verified by
    removing it and watching nothing fail. At **two** they coincide, and a
    label counted twice would enter BH twice and lower every corrected
    threshold."""
    panel = _conditions_panel()
    pair = build_conditions(panel)[:2]
    rows = measure(panel, pair, 1)
    labels = [r.label for r in rows[1:]]
    assert len(labels) == len(set(labels)), labels
    assert len(rows) == 1 + 2 + 1, "two singles and their one conjunction"


def test_independence_compares_observed_against_the_product():
    """Task C's one positive result was this comparison coming out far
    below the independent product — conditions that always fire together
    add nothing to each other."""
    panel = _conditions_panel()
    observed, product = independence(panel, build_conditions(panel))
    assert 0.0 <= observed <= 1.0
    assert 0.0 <= product <= 1.0


# ----------------------------------------------------------- constants


def test_the_cost_floor_is_rd_qs_measured_one():
    """Not an assumption: rd-q measured ~13bp for the futures-liquid
    names, against rd-f's earlier 30bp spot estimate."""
    assert COST_FLOOR_BP == 13.0


def test_the_lookback_is_the_papers_and_the_holds_are_declared():
    """Lou-Polk-Skouras sort on a lagged one month; 21 is the Korean
    trading month. The holding periods are stated up front rather than
    emerging from an exit rule, which is Task C's other fix."""
    assert LOOKBACK_DAYS == 21
    assert HOLD_DAYS == (1, 5)
