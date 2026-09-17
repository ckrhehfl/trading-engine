"""Tests for `research.krx_signal_ic`.

This is a measurement whose output is a specification, so the properties
that matter are the ones that would make a feature look predictive when
it is not:

- **Both gap rules.** rd-p fixed forward returns crossing the closing
  auction or the overnight break. A *trailing* window has the same
  problem and had never been handled — a 60-bar momentum at 09:05 reaches
  into yesterday afternoon and reports it as an hour of movement.
- **No look-ahead.** A feature at bar `i` must pair with a trade entered
  at `i + 1`, never with one entered at `i`.
- **A shared instant grid for the cross-section.** Stepping per symbol
  lands names on different minutes and silently collapses the sample.
- **An unmeasurable feature is dropped, not scored zero.** In a results
  table, `n=0` is indistinguishable from "measured it, found nothing" —
  the opposite conclusion.
"""

from __future__ import annotations

import numpy as np
import pytest

from research.krx_intraday_power import BAR_MS, continuous_blocks
from research.krx_signal_ic import (
    FEATURES,
    HORIZONS,
    USABLE_IC,
    Feature,
    IcRow,
    SymbolBars,
    available_features,
    cross_sectional_ic,
    defined_mask,
    forward_return,
)


def _bars(code: str, minutes: list[int], px: list[float] | None = None) -> SymbolBars:
    """`minutes` are minute offsets; a jump of more than 1 makes a gap."""
    t = np.array(minutes, dtype=np.int64) * BAR_MS
    p = np.array(px if px is not None else range(1, len(minutes) + 1), dtype=float)
    return SymbolBars(code, t, p, p, p, p, p, p, continuous_blocks(t))


#: Two five-bar sessions with a gap between them — the shape of a real
#: KRX tape in miniature.
_TWO_SESSIONS = list(range(5)) + list(range(1000, 1005))


# ------------------------------------------------- the trailing gap rule


def test_a_trailing_window_never_spans_a_gap():
    """**The half rd-p did not need and so did not build.** A 60-bar
    momentum at 09:05 would otherwise reach back through the 17.5-hour
    overnight break and report yesterday afternoon as this morning."""
    blocks = continuous_blocks(np.array(_TWO_SESSIONS, dtype=np.int64) * BAR_MS)
    mask = defined_mask(blocks, 2)
    # First two bars of EACH session are undefined, not just of the array.
    assert mask.tolist() == [False, False, True, True, True,
                             False, False, True, True, True]


def test_a_longer_lookback_costs_more_of_each_session():
    """The constraint's cost is real and grows with the lookback — it is
    reported rather than absorbed."""
    blocks = continuous_blocks(np.array(_TWO_SESSIONS, dtype=np.int64) * BAR_MS)
    assert defined_mask(blocks, 1).sum() == 8
    assert defined_mask(blocks, 4).sum() == 2
    assert defined_mask(blocks, 5).sum() == 0, "a lookback longer than a session"


def test_an_empty_tape_is_not_an_error():
    assert defined_mask(np.array([], dtype=np.int64), 5).tolist() == []


# -------------------------------------------------- the forward gap rule


def test_a_forward_return_never_spans_a_gap():
    bars = _bars("A", _TWO_SESSIONS)
    fwd = forward_return(bars, 2)
    # Entry is the bar AFTER i, exit two later; both must share a block.
    assert np.isnan(fwd[2]) and np.isnan(fwd[3]), "would cross the gap"
    assert np.isfinite(fwd[0]) and np.isfinite(fwd[4])


def test_the_feature_bar_is_never_the_entry_bar():
    """**The look-ahead guard.** A signal seen at bar `i` can only be
    acted on at `i + 1`; pairing it with a return starting at `i` would
    trade on a bar the signal was computed from."""
    bars = _bars("A", list(range(10)), px=[1, 2, 4, 8, 16, 32, 64, 128, 256, 512])
    fwd = forward_return(bars, 1)
    # entry = open[1] = 2, exit = open[2] = 4 -> +1.0, recorded at index 0.
    assert fwd[0] == pytest.approx(1.0)
    assert fwd[0] != pytest.approx((4 - 1) / 1), "paired with its own bar"


def test_a_horizon_longer_than_the_tape_yields_nothing():
    bars = _bars("A", list(range(4)))
    assert not np.isfinite(forward_return(bars, 10)).any()


# ------------------------------------- the cross-section's shared grid


def _constant_feature(bars: SymbolBars, value: float) -> np.ndarray:
    return np.full(bars.open.size, value)


def test_the_cross_section_uses_one_grid_for_every_name():
    """**A real sampling artefact before this was fixed.** Stepping by the
    horizon inside each symbol lands names on different minutes, so the
    intersection collapses — 3,840 instants at h=15 became **143** at
    h=60 and **20** at h=240, which is a property of the sampling and not
    of the market."""
    minutes = list(range(300))
    rng = np.random.default_rng(7)
    universe = [
        # Each name needs its OWN path: identical paths give the forward
        # returns no cross-sectional spread, and a rank correlation
        # against a constant is undefined rather than zero.
        _bars(c, minutes, px=list(100 + np.cumsum(rng.normal(0, 1, len(minutes)))))
        for c in ("A", "B", "C", "D")
    ]
    values = {b.code: _constant_feature(b, i) for i, b in enumerate(universe)}
    feature = Feature("flat", 1, lambda *a: None)
    row = cross_sectional_ic(universe, values, feature, 60)
    assert row.n > 0, "the grid produced no rankable instants"
    # ~300 bars stepped by 60 -> a handful of instants, every one usable.
    assert row.n >= 3


def test_an_instant_with_fewer_than_three_names_is_skipped():
    """A rank correlation over two points is always +/-1 and carries no
    information; including them would manufacture a signal."""
    rng = np.random.default_rng(3)
    universe = [
        _bars(c, list(range(100)), px=list(100 + np.cumsum(rng.normal(0, 1, 100))))
        for c in ("A", "B")
    ]
    values = {b.code: _constant_feature(b, i) for i, b in enumerate(universe)}
    row = cross_sectional_ic(universe, values, Feature("flat", 1, lambda *a: None), 15)
    assert row.n == 0


# --------------------------------- an unmeasurable feature is not a zero


def test_a_feature_whose_input_is_absent_is_dropped_and_named():
    """**KIS does not serve per-minute 거래대금** — `acml_tr_pbmn` is
    cumulative, so `quote_volume` is NULL on every intraday bar. That
    costs this study the literature's strongest prior (abnormal turnover,
    rd-c §2), and it must read as *not measured* rather than as *measured
    and flat*: in the results table those are the same row."""
    universe = [_bars("A", list(range(100)))]
    universe[0] = SymbolBars(
        "A", universe[0].open_time_ms, universe[0].open, universe[0].high,
        universe[0].low, universe[0].close, universe[0].volume,
        np.zeros(100), universe[0].blocks,          # no 거래대금
    )
    keep, dropped = available_features(universe)
    assert any("turnover_z" in d for d in dropped)
    assert all(f.name != "turnover_z_60" for f in keep)
    assert any("NOT measured" in d for d in dropped)


def test_the_same_feature_is_kept_when_its_input_exists():
    """The negative control: the drop must be about the data, not about
    the feature's name."""
    bars = _bars("A", list(range(100)))
    with_value = SymbolBars(
        "A", bars.open_time_ms, bars.open, bars.high, bars.low, bars.close,
        bars.volume, np.arange(1, 101, dtype=float), bars.blocks,
    )
    keep, dropped = available_features([with_value])
    assert any(f.name == "turnover_z_60" for f in keep)
    assert not dropped


# ----------------------------------------------------- the usable floor


def test_the_usable_floor_is_s8s_calibration():
    """0.02–0.05 is a genuinely useful signal, so a feature looking
    unimpressive is normal rather than a disappointment."""
    assert USABLE_IC == 0.02
    assert not IcRow("x", 15, "time-series", 100, 0.019, None, 1e-9).usable
    assert IcRow("x", 15, "time-series", 100, -0.021, None, 1e-9).usable


def test_an_unmeasurable_ic_is_not_usable():
    assert not IcRow("x", 15, "time-series", 0, None, None, None).usable


def test_the_horizons_are_the_repriced_ones():
    """rd-q moved the Korean cost floor from 30bp to ~13bp for the
    futures-liquid names, which is what puts h=15 back in range — rd-p §5
    called it implausible at 30bp."""
    assert HORIZONS == (15, 60, 240)


def test_every_feature_declares_its_lookback():
    """The lookback is what decides where the trailing window crosses a
    gap, so a wrong one is a silent look-back error rather than a
    cosmetic mismatch."""
    for f in FEATURES:
        assert f.lookback > 0, f.name
        assert f.requires in ("close", "value"), f.name
