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
    DESCRIBED_STOP,
    DESCRIBED_TARGET,
    INDEX_SYMBOL,
    PAYOFF_RATIOS,
    annualised,
    barrier_trades,
    breakeven_win_rate,
    clustered_p,
    index_drift,
    panel_years,
    per_name_drift,
)
from research.strategies.scenario_playbook import DailyPanel

_DAY = 86_400_000


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
