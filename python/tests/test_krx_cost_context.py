"""Tests for `research.krx_cost_context`.

This module exists to stop one sentence being misread — *"it did not clear
the cost floor"* — so the properties that matter are the ones that would
let it answer that question dishonestly:

- **The conditioner must be strictly trailing.** Conditioning on a
  volatility that includes today is look-ahead dressed as a regime.
- **The unit of the significance test is the date**, not the name-day.
  That is CLAUDE.md's session-clustering rule, and this module is the
  first thing written after it.
- **The cost floor is `rd-q`'s measured figure**, not a number chosen to
  make the ratio look good.
"""

from __future__ import annotations

import numpy as np
import pytest

from research.conclusion_check import check_clustered_observations
from research.krx_conjunction import COST_FLOOR_BP, Panel, forward_total
from research.krx_cost_context import (
    MOVE_PERCENTILES,
    SIGNAL_WINDOW,
    VOL_TERCILES,
    VOL_WINDOW,
    close_to_close,
    move_by_vol_bucket,
    move_percentiles,
    reversal_ic_by_vol_tercile,
    trailing_return,
    trailing_vol,
)


def _panel(open_px, close_px) -> Panel:
    o = np.array(open_px, dtype=float)
    c = np.array(close_px, dtype=float)
    pc = np.vstack([np.full((1, o.shape[1]), np.nan), c[:-1]])
    return Panel(list(range(o.shape[0])), [f"S{i}" for i in range(o.shape[1])], o, c, pc)


def _random_panel(days: int = 200, names: int = 6, seed: int = 3) -> Panel:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.012, (days, names)), axis=0)
    open_ = close * (1 + rng.normal(0, 0.004, (days, names)))
    return _panel(open_.tolist(), close.tolist())


# ------------------------------------------------- no look-ahead


def test_the_volatility_conditioner_is_strictly_trailing():
    """**The guard that makes conditioning legitimate.** Row `t` must use
    rows `t-window .. t-1` only. Including day `t` would let a regime
    label know the move it is being used to predict."""
    p = _random_panel(days=60, names=4)
    vol = trailing_vol(p, window=5)
    c2c = close_to_close(p)
    t = 30
    expected = np.nanstd(c2c[t - 5 : t], axis=0)
    assert vol[t] == pytest.approx(expected, nan_ok=True)
    # And it must NOT equal the window that includes today.
    inclusive = np.nanstd(c2c[t - 4 : t + 1], axis=0)
    assert not np.allclose(vol[t], inclusive)


def test_the_signal_window_is_strictly_trailing_too():
    p = _random_panel(days=60, names=4)
    sig = trailing_return(p, window=5)
    c2c = close_to_close(p)
    t = 30
    assert sig[t] == pytest.approx(np.nansum(c2c[t - 5 : t], axis=0), nan_ok=True)


def test_the_first_rows_have_no_conditioner():
    """A window that cannot be filled must be nan, never a partial sum
    silently standing in for a full one."""
    p = _random_panel(days=30, names=3)
    vol = trailing_vol(p, window=10)
    assert np.isnan(vol[:10]).all()
    assert np.isfinite(vol[10:]).any()


# ------------------------------------------- the move-vs-cost arithmetic


def test_percentiles_are_reported_as_multiples_of_the_measured_floor():
    moves = np.array([13.0, 26.0, 130.0])
    out = dict((q, (bp, mult)) for q, bp, mult in move_percentiles(moves, (50,)))
    bp, mult = out[50]
    assert bp == pytest.approx(26.0)
    assert mult == pytest.approx(26.0 / COST_FLOOR_BP)


def test_the_floor_is_rd_qs_measured_round_trip():
    """Not a number picked to make the ratio flattering — rd-q measured it
    for the futures-liquid names, against rd-f's earlier 30bp spot
    estimate."""
    assert COST_FLOOR_BP == 13.0


def test_an_empty_sample_reports_nothing_rather_than_zero():
    assert move_percentiles(np.array([np.nan, np.nan])) == []


def test_buckets_run_calmest_first_and_cover_the_sample():
    """The question is whether ANY regime brings the move down to the
    floor, so bucket 1 must be the calmest — reversing it would answer a
    different question while looking identical."""
    p = _random_panel(days=400, names=8)
    moves = np.abs(p.intraday) * 1e4
    vol = trailing_vol(p)
    rows = move_by_vol_bucket(moves, vol, buckets=5)
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
    counts = sum(r[1] for r in rows)
    usable = int((np.isfinite(moves) & np.isfinite(vol)).sum())
    assert counts == pytest.approx(usable, rel=0.02)


def test_a_bucket_too_thin_to_describe_is_dropped_not_reported():
    """A median over a handful of points is an estimate with nothing
    behind it, and printed in a table it reads exactly like the others.

    The sample here is large enough to clear the *earlier* size guard —
    50 points into 5 buckets — so this exercises the per-bucket floor
    specifically, which an earlier version of this test did not: it used
    3 points and tripped `m.size < buckets` first, leaving the floor
    untested.
    """
    rng = np.random.default_rng(1)
    moves = rng.uniform(10, 300, 50)
    vol = rng.uniform(0.01, 0.05, 50)
    assert move_by_vol_bucket(moves, vol, buckets=5) == [], "10 per bucket is too thin"
    # And the same data in one bucket of 50 clears the floor.
    assert len(move_by_vol_bucket(moves, vol, buckets=1)) == 1


# --------------------------------- the date is the unit of the test


def test_the_ic_is_one_observation_per_date():
    """**CLAUDE.md's session-clustering rule, and this module is the first
    thing written under it.** Ten names at one instant share that
    instant's market-wide move; pooling them would be the exact defect
    `check_clustered_observations` blocks."""
    p = _random_panel(days=300, names=8)
    rows, dates = reversal_ic_by_vol_tercile(p, forward_total(p, 1))
    assert len(dates) == len(set(dates)), "a date must appear at most once"
    assert sum(r.dates for r in rows) == pytest.approx(len(dates), abs=1)
    # The sample the terciles are cut from must itself pass the check.
    assert check_clustered_observations(dates, reported_n=len(dates)) is None


def test_pooling_name_days_would_be_caught():
    """The negative control for the line above: if this module had pooled,
    the check would have blocked it. Without this, the passing assertion
    above proves only that the check tolerates whatever was passed."""
    p = _random_panel(days=100, names=8)
    _, dates = reversal_ic_by_vol_tercile(p, forward_total(p, 1))
    pooled = [d for d in dates for _ in range(8)]
    assert check_clustered_observations(pooled) is not None


def test_every_tercile_is_reported_even_when_it_carries_nothing():
    """A regime with no result must appear as a row with `None`, not
    vanish — a missing row and a null row read identically in a table and
    mean opposite things."""
    p = _random_panel(days=200, names=6)
    rows, _ = reversal_ic_by_vol_tercile(p, forward_total(p, 1))
    assert [r.label for r in rows] == list(VOL_TERCILES)


def test_a_panel_too_short_to_split_returns_no_regimes():
    p = _random_panel(days=30, names=4)
    rows, _ = reversal_ic_by_vol_tercile(p, forward_total(p, 1))
    assert rows == []


# ----------------------------------------------------------- constants


def test_the_windows_are_the_adopted_ones():
    """21 is the Korean trading month Lou-Polk-Skouras sort on; 5 is rd-t's
    surviving short-horizon reversal. Neither was searched here."""
    assert VOL_WINDOW == 21
    assert SIGNAL_WINDOW == 5


def test_the_percentiles_bracket_the_distribution():
    """Chosen to show the shape rather than to find a flattering point —
    a median alone could hide a thin left tail where cost binds."""
    assert MOVE_PERCENTILES[0] <= 25 and MOVE_PERCENTILES[-1] >= 99
    assert list(MOVE_PERCENTILES) == sorted(MOVE_PERCENTILES)
