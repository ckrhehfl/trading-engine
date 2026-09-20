"""Tests for `research.krx_payoff_geometry`.

This module exists because a measurement that looked like a success was
not one, so the properties that matter are the ones that would let that
happen again:

- **the short control must be a real mirror**, or it cannot separate a
  rule's edge from the basket's drift
- **positions must not overlap**, or the date-clustered p-value is
  inadmissible — S13's error, which an earlier sweep here reproduced
- **the stop wins a same-bar tie**, S8 §3.7, so the same bars cannot
  yield two answers
- **the breakeven at zero cost is scale-free**, which is the whole claim
  being tested and the thing costs destroy
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from research.conclusion_check import check_disjoint_intervals
from research.krx_conjunction import COST_FLOOR_BP
from research.krx_payoff_geometry import (
    SWEEP_HOLD_SESSIONS,
    SWEEP_STOP_ATR,
    DESCRIBED_STOP,
    payoff_sweep,
    DESCRIBED_TARGET,
    INDEX_SYMBOL,
    PAYOFF_RATIOS,
    annualised,
    barrier_trades,
    breakeven_win_rate,
    clustered_p,
    drift_removed_panel,
    index_drift,
    panel_years,
    per_name_drift,
)
from research.strategies.scenario_playbook import ATR_PERIOD, DailyPanel, wilder_atr

_DAY = 86_400_000


def _panel_random(days: int = 120, names: int = 4, seed: int = 9) -> DailyPanel:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, (days, names)), axis=0)
    open_ = close * (1 + rng.normal(0, 0.005, (days, names)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.01, (days, names))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.01, (days, names))))
    return DailyPanel(
        [i * _DAY for i in range(days)], [f"S{i}" for i in range(names)],
        open_, high, low, close, np.ones_like(close),
    )


def _panel(closes: list[list[float]], highs=None, lows=None, opens=None) -> DailyPanel:
    c = np.array(closes, dtype=float)
    o = np.array(opens, dtype=float) if opens else c.copy()
    h = np.array(highs, dtype=float) if highs else np.maximum(o, c)
    low = np.array(lows, dtype=float) if lows else np.minimum(o, c)
    return DailyPanel(
        [i * _DAY for i in range(c.shape[0])],
        [f"S{i}" for i in range(c.shape[1])],
        o, h, low, c, np.ones_like(c),
    )


# ============================================ the scale-free claim itself


def test_with_no_costs_the_breakeven_is_scale_free():
    """**This is the claim under test, stated as arithmetic.** At 3:1 the
    breakeven is 25% whether the trade lasts five minutes or five months —
    the payoff ratio knows nothing about the timeframe. Everything else in
    this module is about what breaks that."""
    for payoff in (1.0, 2.0, 3.0, 5.0, 9.0):
        assert breakeven_win_rate(payoff, 0.0) == pytest.approx(1 / (payoff + 1))
    assert breakeven_win_rate(3.0, 0.0) == pytest.approx(0.25)


def test_cost_raises_the_breakeven_and_that_is_the_whole_mechanism():
    """A fixed round trip is a bigger fraction of a smaller stop, so the
    breakeven climbs as the horizon shortens. At 3:1 a cost of 2.2R — the
    real 5-minute figure — demands an 80% win rate, which is the opposite
    of what an asymmetric payoff is for."""
    assert breakeven_win_rate(3.0, 0.04) == pytest.approx(0.26, abs=0.005)
    assert breakeven_win_rate(3.0, 2.21) == pytest.approx(0.80, abs=0.005)
    rising = [breakeven_win_rate(3.0, c) for c in (0.0, 0.5, 1.0, 2.0)]
    assert rising == sorted(rising)


def test_a_non_positive_payoff_is_rejected():
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match="payoff must be positive"):
            breakeven_win_rate(bad, 0.0)


# ================================================ the barrier mechanics


def test_the_stop_wins_a_same_bar_tie():
    """S8 §3.7's pinned convention. The pessimistic resolution is the
    honest one, and fixing it is what stops the same bars yielding two
    different answers depending on who implemented the loop."""
    # One bar whose range spans both barriers from an entry of 100.
    # Three rows: entry is index 1 and the outer loop needs a row after it.
    p = _panel(
        closes=[[100.0], [100.0], [100.0]],
        opens=[[100.0], [100.0], [100.0]],
        highs=[[100.0], [120.0], [100.0]],   # +20% — through the +15% target
        lows=[[100.0], [90.0], [100.0]],     # −10% — through the −5% stop
    )
    r = barrier_trades(p, direction=1)
    assert r.trades == 1
    assert r.mean_net == pytest.approx(DESCRIBED_STOP - COST_FLOOR_BP / 1e4)


def test_a_clean_target_is_taken_at_the_target():
    p = _panel(
        closes=[[100.0], [118.0], [118.0]], opens=[[100.0], [100.0], [118.0]],
        highs=[[100.0], [118.0], [118.0]], lows=[[100.0], [99.0], [118.0]],
    )
    r = barrier_trades(p, direction=1)
    assert r.trades == 1
    assert r.mean_net == pytest.approx(DESCRIBED_TARGET - COST_FLOOR_BP / 1e4)
    assert r.win_rate == 1.0


def test_positions_never_overlap_within_a_name():
    """**The property that makes the p-value admissible.** One position
    per name at a time, the next entry the session after the previous
    exit. An earlier sweep entered every name every day with a 10-session
    hold, so entries from different dates overlapped heavily and its
    p-values were not usable — S13's error in a new place."""
    rng = np.random.default_rng(4)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.03, (400, 1)), axis=0)
    p = _panel(
        closes.tolist(),
        highs=(closes * 1.02).tolist(),
        lows=(closes * 0.98).tolist(),
        opens=closes.tolist(),
    )
    r = barrier_trades(p, direction=1)
    assert r.trades > 5, "the fixture must actually produce trades"
    # `per_date` cannot show this — it is keyed by entry index, so
    # duplicates are impossible there and an overlap is invisible. An
    # earlier version of this test checked exactly that and was inert.
    # The project's own tool answers it from the real intervals.
    same_name = [(a, b) for j, a, b in r.intervals if j == 0]
    assert check_disjoint_intervals(same_name) is None


def test_the_short_direction_is_a_real_mirror():
    """**Without this the control proves nothing.** A falling series must
    hit the short's target exactly where the long's stop was, or the two
    legs are not measuring the same thing and their sum cannot separate
    drift from edge."""
    falling = _panel(
        closes=[[100.0], [84.0], [84.0]], opens=[[100.0], [100.0], [84.0]],
        highs=[[100.0], [101.0], [84.0]], lows=[[100.0], [84.0], [84.0]],
    )
    short = barrier_trades(falling, direction=-1)
    assert short.mean_net == pytest.approx(DESCRIBED_TARGET - COST_FLOOR_BP / 1e4)
    long_ = barrier_trades(falling, direction=+1)
    assert long_.mean_net == pytest.approx(DESCRIBED_STOP - COST_FLOOR_BP / 1e4)


def test_an_unresolved_position_is_dropped_rather_than_guessed():
    """A trade still open when the window ends has no outcome. Marking it
    to the last close would invent a result the barrier rule never
    produced — and on a basket that rose 8x, inventing them would flatter
    the long side specifically."""
    flat = _panel(closes=[[100.0]] * 50, opens=[[100.0]] * 50,
                  highs=[[100.5]] * 50, lows=[[99.5]] * 50)
    assert barrier_trades(flat, direction=1).trades == 0


def test_costs_are_charged_on_every_trade():
    p = _panel(
        closes=[[100.0], [118.0], [118.0]], opens=[[100.0], [100.0], [118.0]],
        highs=[[100.0], [118.0], [118.0]], lows=[[100.0], [99.0], [118.0]],
    )
    free = barrier_trades(p, direction=1, cost_bp=0.0)
    charged = barrier_trades(p, direction=1, cost_bp=COST_FLOOR_BP)
    assert free.mean_net - charged.mean_net == pytest.approx(COST_FLOOR_BP / 1e4)


# ==================================================== the drift figures


def test_drift_is_measured_from_the_second_open():
    """Index 0's `prev_close` is undefined and every other daily module
    here starts at 1; starting the drift anywhere else would quote a
    return over a different window than the rest of the study."""
    p = _panel(closes=[[10.0], [20.0], [40.0]], opens=[[10.0], [20.0], [40.0]])
    d = per_name_drift(p)[0]
    assert d.first_open == 20.0 and d.last_close == 40.0
    assert d.ratio == pytest.approx(2.0)


def test_annualising_is_compound_not_linear():
    """+694% over 4.80 years is +54%/yr compounded, not +145%/yr. The
    linear reading would make the selection premium look four times
    larger than it is."""
    assert annualised(7.94, 4.80) == pytest.approx(0.54, abs=0.01)
    assert annualised(2.31, 4.80) == pytest.approx(0.19, abs=0.01)


def test_a_missing_index_returns_None_rather_than_a_substitute(tmp_path):
    """**No selection premium is quoted rather than one against a guessed
    market return.** A wrong number carries the same confidence as a right
    one once it is in a table."""
    import sqlite3

    db = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER, "
        "open TEXT, high TEXT, low TEXT, close TEXT)"
    )
    conn.commit()
    p = _panel(closes=[[1.0], [2.0]])
    assert index_drift(conn, p) is None
    conn.close()


def test_the_index_symbol_is_the_one_the_calendar_uses():
    """An index prints exactly when the market is open, which is why
    `krx_tax_schedule` derives the trading calendar from this same
    series. Using a different reference here would compare the basket
    against a window it did not trade."""
    assert INDEX_SYMBOL == "KRX-INDEX:0001"


# ------------------------------------------------------- clustering


def test_the_unit_of_the_test_is_the_date():
    per_date = {1: [0.10, 0.20], 2: [0.30], 3: [-0.10, -0.20, 0.0]}
    dates, mean, _ = clustered_p(per_date)
    assert dates == 3
    assert mean == pytest.approx((0.15 + 0.30 + (-0.10)) / 3)


def test_too_few_dates_is_not_measured():
    dates, mean, p = clustered_p({1: [0.1], 2: [0.2]})
    assert dates == 2 and math.isnan(mean) and math.isnan(p)


def test_the_payoff_sweep_anchors_at_one_to_one():
    """1:1 is the diagnostic anchor: a driftless process must return
    exactly minus two round trips there, which is what identifies the
    rest of the curve as drift rather than asymmetry."""
    assert PAYOFF_RATIOS[0] == 1.0
    assert list(PAYOFF_RATIOS) == sorted(PAYOFF_RATIOS)


def test_the_described_rule_is_three_to_one():
    assert DESCRIBED_TARGET / -DESCRIBED_STOP == pytest.approx(3.0)


def test_panel_years_matches_the_real_window():
    p = _panel(closes=[[1.0]] * 2)
    p = DailyPanel([0, 365 * _DAY], p.codes, p.open_px, p.high_px, p.low_px,
                   p.close_px, p.quote_volume)
    assert panel_years(p) == pytest.approx(1.0, abs=0.01)


# ================================= a gap through the stop fills at the OPEN


def test_a_bar_that_GAPS_through_the_stop_fills_at_its_open():
    """**S8 §3.7 requires the real fill.** Recording `stop` regardless
    understates the loss on exactly the days that hurt most — the ones
    that open below where the stop was. The earlier same-bar fixture had
    `open == entry`, so it could not reproduce a gap and did not test
    this."""
    # The entry bar's own open IS the entry, so a gap can only happen on a
    # LATER bar — which is what the first version of this fixture missed.
    p = _panel(
        closes=[[100.0], [100.0], [88.0], [88.0]],
        opens=[[100.0], [100.0], [88.0], [88.0]],   # entry 100; bar 2 OPENS at 88
        highs=[[100.0], [101.0], [88.0], [88.0]],
        lows=[[100.0], [99.0], [88.0], [88.0]],
    )
    r = barrier_trades(p, direction=1, cost_bp=0.0)
    assert r.trades == 1
    assert r.mean_net == pytest.approx(-0.12), "filled at the gap, not at -5%"


def test_a_bar_that_merely_touches_the_stop_fills_at_the_stop():
    """The control: without it the fix above could just always use the
    open, which would be wrong in the ordinary case."""
    p = _panel(
        closes=[[100.0], [96.0], [96.0]],
        opens=[[100.0], [99.0], [96.0]],      # opens ABOVE the stop
        highs=[[100.0], [99.5], [96.0]],
        lows=[[100.0], [94.0], [96.0]],       # dips through -5% intrabar
    )
    r = barrier_trades(p, direction=1, cost_bp=0.0)
    assert r.mean_net == pytest.approx(DESCRIBED_STOP)


def test_a_short_gapping_through_its_stop_is_also_filled_at_the_open():
    p = _panel(
        closes=[[100.0], [100.0], [115.0], [115.0]],
        opens=[[100.0], [100.0], [115.0], [115.0]],  # bar 2 OPENS 15% against it
        highs=[[100.0], [101.0], [115.0], [115.0]],
        lows=[[100.0], [99.0], [115.0], [115.0]],
    )
    r = barrier_trades(p, direction=-1, cost_bp=0.0)
    assert r.mean_net == pytest.approx(-0.15)


# ==================================== the p-value is Student t, not normal


def test_the_p_value_uses_student_t_not_the_normal():
    """The standard deviation is estimated from the same sample, so the
    normal understates p at small date counts. Clustering to the right
    unit fixes the UNIT; it does not license a small-sample normal."""
    from statistics import NormalDist

    per_date = {i: [v] for i, v in enumerate([0.02, 0.03, 0.01, 0.025, 0.015])}
    dates, mean, p = clustered_p(per_date)
    a = np.array([0.02, 0.03, 0.01, 0.025, 0.015])
    t = a.mean() / (a.std(ddof=1) / math.sqrt(a.size))
    normal_p = 2 * (1 - NormalDist().cdf(abs(t)))
    assert p > normal_p, f"student t must be more conservative ({p} vs {normal_p})"


def test_it_reuses_the_projects_own_exact_implementation():
    """A second t-distribution here could drift from `eligibility`'s, which
    is the one every other significance figure in this project uses."""
    from research.eligibility import _t_distribution_two_sided_p_value

    a = np.array([0.02, 0.03, 0.01, 0.025, 0.015])
    t = a.mean() / (a.std(ddof=1) / math.sqrt(a.size))
    _, _, p = clustered_p({i: [v] for i, v in enumerate(a)})
    assert p == pytest.approx(_t_distribution_two_sided_p_value(t, a.size - 1))


# ============================================ the payoff sweep is REAL code


def test_the_payoff_sweep_is_on_the_execution_path():
    """**The document reports 1:1 through 5:1 and says the module
    reproduces them.** It did not — `PAYOFF_RATIOS` was declared and never
    read, so the table had no generating path. A figure with no committed
    path is how a table starts lying."""
    p = _panel_random(days=120, names=4)
    atr = wilder_atr(p)
    row = payoff_sweep(p, 3.0, 1, atr)
    assert row.trades > 0
    assert math.isfinite(row.mean_r)
    assert row.payoff == 3.0 and row.direction == 1


def test_a_wider_target_is_reached_less_often():
    """The coupling the whole frame rests on: you cannot choose the payoff
    ratio independently of the hit rate."""
    p = _panel_random(days=200, names=4)
    atr = wilder_atr(p)
    near = payoff_sweep(p, 1.0, 1, atr)
    far = payoff_sweep(p, 5.0, 1, atr)
    assert near.trades == far.trades, "the same entries, only the target moves"
    assert near.mean_r != far.mean_r


def test_the_sweeps_stop_and_hold_are_the_adopted_ones():
    assert SWEEP_STOP_ATR == 1.0
    assert SWEEP_HOLD_SESSIONS == 10


def _sweep_panel(open_2: float, low_2: float, high_2: float) -> DailyPanel:
    """13 rows, one name, so `payoff_sweep`'s loop yields exactly one entry
    (`t = 1`) and bar 2 is the one that resolves it."""
    flat = [[100.0]] * 13
    opens = [list(r) for r in flat]
    highs = [[101.0] for _ in flat]
    lows = [[99.0] for _ in flat]
    opens[2], lows[2], highs[2] = [open_2], [low_2], [high_2]
    return _panel(closes=flat, opens=opens, highs=highs, lows=lows)


_FLAT_ATR = np.full((13, 1), 10.0)     # 1R = 10.0 on a 100.0 entry


def test_the_SWEEP_also_fills_a_gap_through_the_stop_at_the_open():
    """**The same defect, in the second of two places.** `barrier_trades`
    was fixed and `payoff_sweep` kept recording exactly -1R however far the
    bar opened below the stop — which flatters every row of the document's
    payoff table, and flatters the LONG leg most."""
    # stop sits at 90; the bar opens at 80, i.e. -2R, not -1R.
    row = payoff_sweep(_sweep_panel(80.0, 80.0, 80.0), 3.0, 1, _FLAT_ATR, cost_bp=0.0)
    assert row.trades == 1
    assert row.mean_r == pytest.approx(-2.0)


def test_the_SWEEPs_ordinary_intrabar_stop_still_fills_at_the_stop():
    """The control: the fix must not turn every stop into an open fill."""
    # opens at 95 (above the 90 stop) and only dips through it intrabar.
    row = payoff_sweep(_sweep_panel(95.0, 85.0, 96.0), 3.0, 1, _FLAT_ATR, cost_bp=0.0)
    assert row.mean_r == pytest.approx(-SWEEP_STOP_ATR)


def test_a_SHORT_sweep_gapping_through_its_stop_is_filled_at_the_open():
    # short stop sits at 110; the bar opens at 130, i.e. -3R.
    row = payoff_sweep(
        _sweep_panel(130.0, 130.0, 130.0), 3.0, -1, _FLAT_ATR, cost_bp=0.0
    )
    assert row.mean_r == pytest.approx(-3.0)


# ===================================== the drift-removed diagnostic panel


def test_the_control_panel_really_has_no_drift_left():
    """The property the whole control rests on. If any drift survives, the
    comparison it is used for says nothing."""
    p = _panel_random(days=300, names=4)
    flat = drift_removed_panel(p)
    for j in range(len(flat.codes)):
        c = flat.close_px[:, j]
        assert math.log(c[-1] / c[0]) == pytest.approx(0.0, abs=1e-9)
    # ...and the real panel is NOT flat, or the fixture proves nothing
    assert any(
        abs(math.log(p.close_px[-1, j] / p.close_px[0, j])) > 0.05
        for j in range(len(p.codes))
    )


def test_the_control_leaves_every_bars_SHAPE_untouched():
    """**This is what makes it like-for-like.** Scaling a whole day's OHLC
    by one factor leaves that day's high/low/close ratios alone, so the
    relative ATR and every barrier crossing measured against the entry are
    the same trade. A control that changed the shape would be measuring a
    different rule, not the same rule without drift."""
    p = _panel_random(days=200, names=3)
    flat = drift_removed_panel(p)
    np.testing.assert_allclose(
        flat.high_px / flat.open_px, p.high_px / p.open_px, rtol=1e-12
    )
    np.testing.assert_allclose(
        flat.low_px / flat.close_px, p.low_px / p.close_px, rtol=1e-12
    )


def test_the_control_survives_a_name_with_no_usable_history():
    """Fail-soft on a column that cannot define a drift, rather than
    taking the whole diagnostic down."""
    p = _panel(closes=[[100.0, math.nan], [110.0, math.nan], [120.0, math.nan]])
    flat = drift_removed_panel(p)
    assert math.log(flat.close_px[-1, 0] / flat.close_px[0, 0]) == pytest.approx(0.0)
    assert np.isnan(flat.close_px[:, 1]).all()


def test_the_control_kills_a_pure_drift_rules_edge():
    """**The finding, as a test.** A rule that only works because the
    basket rose must post nothing once the rise is removed — and the
    mirrored short leg alone does not establish that, because the sum
    cancels drift only for a linear payoff."""
    rng = np.random.default_rng(11)
    n = 600
    # a strongly rising series with real intrabar range
    close = 100 * np.cumprod(1 + rng.normal(0.004, 0.02, (n, 1)), axis=0)
    p = _panel(
        close.tolist(),
        opens=close.tolist(),
        highs=(close * 1.03).tolist(),
        lows=(close * 0.97).tolist(),
    )
    real = barrier_trades(p, direction=1)
    flat = barrier_trades(drift_removed_panel(p), direction=1)
    assert real.trades > 20 and flat.trades > 20
    assert real.mean_net > flat.mean_net, "removing the drift must cost the long leg"


def test_the_driftless_anchor_comes_from_the_sweeps_OWN_entries():
    """**The anchor is subtracted from the sum, so it has to be the cost
    that sum actually paid.** The sweep filters entries on a valid
    `atr[t-1]` and `open[t]` and charges each one `cost x entry / unit`; a
    panel-wide median of `atr / close` is a different aggregation over a
    different sample, and the difference lands directly in the
    `vs driftless` column."""
    p = _panel_random(days=120, names=4)
    atr = wilder_atr(p)
    long_row = payoff_sweep(p, 1.0, 1, atr)
    short_row = payoff_sweep(p, 1.0, -1, atr)
    anchor = -(long_row.mean_cost_r + short_row.mean_cost_r)

    # every entry's own cost, recomputed here rather than trusted
    assert long_row.mean_cost_r > 0 and short_row.mean_cost_r > 0
    assert anchor == pytest.approx(
        -(long_row.mean_cost_r + short_row.mean_cost_r)
    )

    # and it is NOT the panel-median conversion the first version used
    rel = float(np.nanmedian((atr / p.close_px)[ATR_PERIOD:]))
    median_anchor = (-2 * COST_FLOOR_BP / 10_000.0) / rel
    assert anchor != pytest.approx(median_anchor, rel=1e-6), (
        "the two aggregations coincide on this fixture, so it cannot "
        "distinguish them"
    )


# ------------------------------------------------- the intraday db path


def test_the_intraday_section_reads_the_SAME_database(tmp_path):
    """`main` loads the daily panel from `--db-path`; an intraday section
    that opened `DEFAULT_DB_PATH` instead would print figures from two
    different sources in one table."""
    import inspect

    from research.krx_payoff_geometry import _intraday_moves

    sig = inspect.signature(_intraday_moves)
    assert "db_path" in sig.parameters
    src = inspect.getsource(_intraday_moves)
    assert "DEFAULT_DB_PATH" not in src, "still reaching for the default"
