"""Can a window answer the question, computed before it is spent on one.

`CLAUDE.md` requires a family's detectable effect to be computed before the
family is specified, and this project has repeatedly specified first and
discovered the window's limits afterwards. These tests pin the arithmetic that
answers it, including the one result that is not obvious: **trade count and
turnover are separable through the universe size**, so the trade-count floor and
the cost ceiling are not the contradiction they first appear to be.

Figures are checked against hand-computed values rather than against the
implementation's own output, so a sign error or a dropped quantile fails here.
"""

from __future__ import annotations

import math

import pytest

from research.window_feasibility import (
    DEFAULT_ALPHA,
    DEFAULT_POWER,
    KRX_SESSIONS_PER_YEAR,
    Shape,
    cost_drag,
    detection_floor,
    feasible_shapes,
    max_round_trips,
    power_for,
    smallest_detectable_at_power,
    trade_count_floor,
)

#: The reserved pre-2019 panel: KIS's floor to the end of 2018.
RESERVED_YEARS = 27.35


def test_the_floor_matches_the_projects_own_formula():
    """`1.6449 / sqrt(years)`, and 0.315 on the reserved panel — the figure
    `CLAUDE.md` quotes."""
    assert detection_floor(RESERVED_YEARS) == pytest.approx(0.315, abs=0.002)
    assert detection_floor(7.71) == pytest.approx(0.592, abs=0.002)  # spent KRX daily
    assert detection_floor(1.84) == pytest.approx(1.213, abs=0.002)  # 1h research


def test_power_requires_a_larger_effect_than_the_floor():
    """**The distinction this module exists for.** The floor says what a result
    must exceed; power says what a real effect must be for the study to find it.
    They differ by the power quantile — a factor of 2.28 in required years.
    """
    floor = detection_floor(RESERVED_YEARS)
    powered = smallest_detectable_at_power(RESERVED_YEARS)
    assert floor is not None and powered is not None
    assert powered > floor
    assert powered == pytest.approx(0.475, abs=0.002)
    # The ratio is the quantile ratio and so is span-independent.
    assert powered / floor == pytest.approx((1.6449 + 0.8416) / 1.6449, abs=0.01)


@pytest.mark.parametrize(
    "years,sharpe,expected",
    [
        (RESERVED_YEARS, 0.4, 0.673),  # below the 80% line
        (RESERVED_YEARS, 0.475, 0.799),  # at it
        (RESERVED_YEARS, 0.5, 0.834),
        (11.0, 0.5, 0.505),  # a 2008-start window is a coin flip for 0.5
    ],
)
def test_power_for_a_given_effect(years, sharpe, expected):
    """The figures behind the window-truncation trade-off: cutting the panel at
    2008 for data-quality reasons takes a 0.5 edge from 83% power to 51%."""
    assert power_for(sharpe, years) == pytest.approx(expected, abs=0.003)


def test_cost_drag_is_linear_in_round_trips():
    """One full-capital round trip costs its spread once, so doubling turnover
    doubles the drag. Pinned because a non-linear cost model would be a venue
    claim this project has not measured."""
    assert cost_drag(1, 33) == pytest.approx(0.0033)
    assert cost_drag(12, 33) == pytest.approx(0.0396)
    assert cost_drag(24, 33) == pytest.approx(2 * cost_drag(12, 33))


def test_the_cost_ceiling_is_a_turnover_ceiling():
    """A 0.5 Sharpe at 15% volatility grosses 7.5%. Spending at most a quarter
    of it on costs at the Korean 33 bp round trip allows about 5.7 round trips a
    year — which is a holding-period floor of roughly two months.
    """
    limit = max_round_trips(round_trip_bps=33, gross_return=0.075, cost_budget_fraction=0.25)
    assert limit is not None
    assert limit == pytest.approx(5.68, abs=0.05)
    assert KRX_SESSIONS_PER_YEAR / limit == pytest.approx(43.1, abs=1.0)


def test_the_trade_floor_clamps_as_claude_md_specifies():
    """`max(30, min(100, sessions/20))`. The reserved panel's 6,701 sessions
    clamp at 100, so the floor there is the ceiling of the formula."""
    assert trade_count_floor(RESERVED_YEARS * KRX_SESSIONS_PER_YEAR) == 100
    assert trade_count_floor(400) == 30  # short window clamps at the bottom
    assert trade_count_floor(1200) == 60  # in between, unclamped


def test_a_single_name_satisfies_both_constraints_only_barely():
    """**The constraint that makes a portfolio the comfortable choice rather than
    the only one.** One name trading twice a year produces 55 trades across 27
    years, under the 100 floor; getting to 100 means four trades a year, which
    reaches 109 and costs 1.32% — 70% of a quarter-of-gross budget.

    So a single name is feasible and has nine trades of margin. The portfolio
    shapes reach the same floor at a third of the turnover, which is what makes
    them the shape to prefer — not that the alternative is impossible.
    """
    two = Shape(universe_size=1, trades_per_name_per_year=2.0, years=RESERVED_YEARS, round_trip_bps=33)
    assert two.total_trades == pytest.approx(54.7, abs=0.5)
    assert not two.meets_trade_floor()

    four = Shape(universe_size=1, trades_per_name_per_year=4.0, years=RESERVED_YEARS, round_trip_bps=33)
    assert four.meets_trade_floor(), "four trades a year does clear the floor"
    assert four.drag == pytest.approx(0.0132, abs=0.0005)
    assert four.drag / 0.075 > 0.17, (
        "and it spends a real share of a 0.5-Sharpe gross return to do so"
    )


def test_trade_count_and_turnover_are_separable_through_the_universe():
    """**The non-obvious result.** A round trip in one of `n` equally weighted
    names moves `1/n` of capital, so widening the universe multiplies trades and
    leaves turnover untouched. That is what makes the trade floor and the cost
    ceiling compatible rather than contradictory.
    """
    one = Shape(1, 2.0, RESERVED_YEARS, 33)
    thirty = Shape(30, 2.0, RESERVED_YEARS, 33)

    assert thirty.total_trades == pytest.approx(30 * one.total_trades)
    assert thirty.round_trips_per_year == pytest.approx(one.round_trips_per_year), (
        "turnover must not move with the universe size"
    )
    assert thirty.drag == pytest.approx(one.drag)
    assert thirty.meets_trade_floor() and not one.meets_trade_floor()


def test_round_trips_equal_trades_per_name():
    """The identity behind the separability, asserted directly so a refactor
    cannot quietly reintroduce a dependence on the universe size."""
    for n in (1, 7, 25):
        for per_name in (0.5, 2.0, 6.0):
            shape = Shape(n, per_name, RESERVED_YEARS, 33)
            assert shape.round_trips_per_year == pytest.approx(per_name)


def test_the_feasible_set_on_the_reserved_panel_is_not_empty():
    """The question this module was written to answer. A quarter of a
    0.5-Sharpe gross return as the cost budget, at the Korean round trip."""
    shapes = feasible_shapes(
        years=RESERVED_YEARS,
        round_trip_bps=33,
        gross_return=0.075,
        cost_budget_fraction=0.25,
    )
    assert shapes, "no shape clears both constraints; the window cannot be used"

    # **A single name does survive, and the margin is the point.** One name
    # trading four times a year reaches 109 trades against the 100 floor — nine
    # spare — and spends 70% of the cost budget to do it. A first version of this
    # test asserted every survivor was a portfolio and was wrong; what is true is
    # that the single-name shape has almost no room, so a slightly higher cost or
    # a slightly shorter window removes it.
    single = [s for s in shapes if s.universe_size == 1]
    assert len(single) == 1, f"expected exactly the four-trades-a-year shape, got {single}"
    assert single[0].total_trades == pytest.approx(109, abs=1)
    assert single[0].drag / (0.075 * 0.25) == pytest.approx(0.70, abs=0.02), (
        "the single-name shape should be shown spending most of its cost budget"
    )

    # Every shape holds for weeks rather than days — the cost ceiling's real
    # content, since turnover and holding period are reciprocal.
    for s in shapes:
        holding = s.mean_holding_sessions
        assert holding is not None and holding >= 40, (
            f"{s.universe_size} names x {s.trades_per_name_per_year}/yr holds only "
            f"{holding:.0f} sessions, which the cost budget should have excluded"
        )


def test_a_tighter_cost_budget_shrinks_the_feasible_set():
    """Monotonicity, as a guard against the budget being ignored."""
    loose = feasible_shapes(
        years=RESERVED_YEARS, round_trip_bps=33, gross_return=0.075, cost_budget_fraction=0.50
    )
    tight = feasible_shapes(
        years=RESERVED_YEARS, round_trip_bps=33, gross_return=0.075, cost_budget_fraction=0.05
    )
    assert len(tight) < len(loose)
    assert all(s in loose for s in tight)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"years": 0},
        {"years": -5},
    ],
)
def test_degenerate_spans_return_none(kwargs):
    """The "no evidence" convention: a span that cannot define a floor gets
    `None`, never a number a caller would print."""
    assert detection_floor(**kwargs) is None
    assert smallest_detectable_at_power(**kwargs) is None
    assert power_for(0.5, **kwargs) is None


def test_degenerate_cost_inputs_return_none():
    assert max_round_trips(round_trip_bps=0, gross_return=0.075, cost_budget_fraction=0.25) is None
    assert max_round_trips(round_trip_bps=33, gross_return=0, cost_budget_fraction=0.25) is None
    assert max_round_trips(round_trip_bps=33, gross_return=0.075, cost_budget_fraction=0) is None
    assert max_round_trips(round_trip_bps=33, gross_return=0.075, cost_budget_fraction=1.5) is None


def test_the_defaults_are_the_conventional_pair():
    """Stated so a future reader does not have to infer them, and so a silent
    change to either is visible."""
    assert DEFAULT_ALPHA == 0.05
    assert DEFAULT_POWER == 0.80
    assert KRX_SESSIONS_PER_YEAR == 245.0


# ---------------------------------------------------------------------------
# The alpha contract, unified across all three statistics
#
# Flagged on review of PR #220: `detection_floor` was a second copy of
# `retrospective.detection_floor_sharpe` and had already drifted — it raised
# `StatisticsError` from inside `inv_cdf` where the real function raises
# `ValueError`. The two companions validated nothing at all. A bad `alpha` is a
# caller mistake in every one of them, so all three now fail the same way.

import pytest as _pytest

from research.retrospective import detection_floor_sharpe


def test_detection_floor_delegates_rather_than_reimplementing():
    """Identical output to the one definition, across spans. A second
    implementation is free to drift, which is what happened."""
    for years in (0.5, 1.84, 7.71, 27.35):
        for alpha in (0.01, 0.05, 0.10):
            assert detection_floor(years, alpha=alpha) == detection_floor_sharpe(
                years, alpha=alpha
            )


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5, 5.0])
def test_an_impossible_alpha_raises_in_every_statistic(alpha):
    """One contract, three functions: `ValueError` naming the parameter — a
    caller mistake, not a `None` that would propagate into a report as "no
    evidence".

    **Matched on the message, not just the type.** `statistics.StatisticsError`
    subclasses `ValueError`, so a bare `raises(ValueError)` passes on an
    unvalidated `inv_cdf` blowing up deep inside the arithmetic — which is
    exactly the drift this check exists to catch, and a mutation removing the
    validation survived until the match was added.
    """
    for call in (
        lambda: detection_floor(27.35, alpha=alpha),
        lambda: smallest_detectable_at_power(27.35, alpha=alpha),
        lambda: power_for(0.5, 27.35, alpha=alpha),
    ):
        with _pytest.raises(ValueError, match="alpha must be in"):
            call()


@pytest.mark.parametrize("alpha", [0.0, 1.5])
def test_a_degenerate_span_still_returns_none_even_with_a_bad_alpha(alpha):
    """**The ordering that the guard placement protects.** The delegated helper
    validates `alpha` before `years`, so calling it unguarded would turn
    `detection_floor(0, alpha=1.5)` from `None` into a raise — tightening a
    contract this module's callers rely on. The `years` check stays first.
    """
    assert detection_floor(0, alpha=alpha) is None
    assert detection_floor(-5, alpha=alpha) is None
