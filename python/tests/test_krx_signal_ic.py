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

from research.ic import spearman
from research.krx_intraday_power import BAR_MS, continuous_blocks
from research.krx_signal_ic import (
    FEATURES,
    HORIZONS,
    USABLE_IC,
    Feature,
    IcRow,
    SymbolBars,
    _corr_from_sums,
    _ranks,
    available_features,
    block_bootstrap_corr_p,
    block_bootstrap_mean_p,
    cross_sectional_ic,
    defined_mask,
    forward_return,
    orthogonality,
    session_of,
    symbol_block_count,
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


# --------------------------------- the p-value the gate actually runs on


def test_the_session_key_is_the_krx_trading_date():
    """A KRX trading date maps exactly onto UTC midnight, so integer
    division by a day is the session — no calendar, no holiday table."""
    day = 86_400_000
    assert session_of(0) == 0
    assert session_of(day - 1) == 0, "23:59 belongs to the same session"
    assert session_of(day) == 1
    # 09:00 KST and 15:20 KST on the same date are the same session.
    assert session_of(5 * day) == session_of(5 * day + 6 * 3_600_000)


def test_a_bootstrap_resamples_sessions_not_observations():
    """**The dependence this exists for.** Ten names at one instant share a
    market-wide move and instants share a session, so an observation is
    not a draw. Resampling whole sessions is what makes the spread reflect
    the dependence that is really there."""
    rng = np.random.default_rng(5)
    # Twenty sessions, each with a strong shared shift: within a session
    # the observations barely vary, so the real uncertainty is the
    # session-to-session spread and NOT the pooled one.
    values, sessions = [], []
    for s in range(20):
        shift = rng.normal(0, 1.0)
        for _ in range(50):
            values.append(shift + rng.normal(0, 0.01))
            sessions.append(s)
    p, ratio = block_bootstrap_mean_p(values, sessions, draws=400)
    assert p is not None and ratio is not None
    assert ratio > 3.0, (
        f"a shared per-session shock must inflate the standard error, got {ratio:.2f}"
    )


def test_an_independent_sample_is_barely_corrected():
    """The negative control: with no session structure the block standard
    error must land near the naive one, or the correction is inventing
    dependence rather than measuring it."""
    rng = np.random.default_rng(6)
    values = rng.normal(0, 1.0, 1000).tolist()
    sessions = [i % 50 for i in range(1000)]
    _, ratio = block_bootstrap_mean_p(values, sessions, draws=400)
    assert 0.75 < ratio < 1.35, ratio


def test_a_correlation_bootstrap_tests_the_POOLED_statistic():
    """**A real defect, caught by the figures disagreeing.** The first
    version bootstrapped the mean of per-session ICs while `rank_ic`
    beside it was the pooled Spearman — two different numbers. On the real
    run it produced p = 0.0005 where the naive p was 0.41, and a
    correction that turns a null result significant is a bug, not a
    finding."""
    rng = np.random.default_rng(7)
    xs, ys, sessions = [], [], []
    for s in range(30):
        for _ in range(40):
            x = rng.normal()
            xs.append(x)
            ys.append(0.3 * x + rng.normal())
            sessions.append(s)
    p, ratio = block_bootstrap_corr_p(xs, ys, sessions, draws=400)
    assert p is not None and p < 0.05, "a real 0.3 correlation must register"
    assert ratio is not None and 0.5 < ratio < 2.5, ratio


def test_a_correlation_bootstrap_finds_nothing_in_noise():
    rng = np.random.default_rng(8)
    n = 1200
    xs = rng.normal(0, 1, n).tolist()
    ys = rng.normal(0, 1, n).tolist()
    p, _ = block_bootstrap_corr_p(xs, ys, [i % 30 for i in range(n)], draws=400)
    assert p > 0.05, p


def test_a_bootstrap_p_is_never_reported_as_exactly_zero():
    """`0` would read as certainty when it only means no draw reached the
    observed value — a resolution limit, floored at `1/draws`."""
    rng = np.random.default_rng(9)
    values, sessions = [], []
    for s in range(40):
        for _ in range(10):
            values.append(100.0 + rng.normal(0, 0.001))
            sessions.append(s)
    p, _ = block_bootstrap_mean_p(values, sessions, draws=200)
    assert p == pytest.approx(1 / 200)


def test_too_few_sessions_is_not_measured():
    """Two sessions cannot produce a resampling distribution, and
    returning a number anyway would be a figure with no support."""
    assert block_bootstrap_mean_p([1.0, 2.0, 3.0], [0, 0, 1]) == (None, None)
    assert block_bootstrap_corr_p([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], [0, 0, 1]) == (
        None,
        None,
    )


def test_ranks_average_ties_rather_than_ordering_them():
    """A rank correlation over tied values must not depend on which tied
    element happened to sort first."""
    assert _ranks(np.array([10.0, 20.0, 20.0, 40.0])).tolist() == [1.0, 2.5, 2.5, 4.0]


def test_the_rank_correlation_from_sums_is_the_spearman():
    """`_corr_from_sums` is what every bootstrap draw uses, so it has to
    agree with the module's own `spearman` on the full sample — otherwise
    the observed statistic and the resampled ones are different things."""
    rng = np.random.default_rng(11)
    xs = rng.normal(0, 1, 200)
    ys = 0.5 * xs + rng.normal(0, 1, 200)
    rx, ry = _ranks(xs), _ranks(ys)
    sums = np.array(
        [rx.size, rx.sum(), ry.sum(), (rx * rx).sum(), (ry * ry).sum(), (rx * ry).sum()]
    )
    assert _corr_from_sums(sums) == pytest.approx(
        spearman(xs.tolist(), ys.tolist()), abs=1e-9
    )


# ------------------------------------- redundancy is a CROSS-SECTIONAL question


def test_a_shared_TIME_move_is_not_redundancy():
    """**What this decides is the signal count**, which the write-up rests
    on, so it must be measured the way the ICs are.

    Here two features move together *over time* — both rise and fall with
    the same market-wide shock — while their *cross-sectional rankings*
    are independent at every instant. They are therefore two signals for
    the purpose of picking which name to hold, and a pooled correlation
    over every (name, instant) pair says the opposite, because the shared
    time component is constant across the cross-section and drops out of a
    per-instant ranking but not out of a pooled one.

    On the real panel the pooled version reported every pair as more
    redundant than the per-instant one — 0.850 against 0.765 on the
    strongest pair.
    """
    rng = np.random.default_rng(13)
    minutes = list(range(400))
    universe = [
        _bars(c, minutes, px=list(100 + np.cumsum(rng.normal(0, 1, len(minutes)))))
        for c in ("A", "B", "C", "D", "E")
    ]
    n = universe[0].open.size
    shock = rng.normal(0, 20.0, n)          # common to every name, per instant
    flat: dict[str, np.ndarray] = {}
    for b in universe:
        flat[b.code + "|ret_5"] = shock + rng.normal(0, 1.0, n)
        flat[b.code + "|ret_15"] = shock + rng.normal(0, 1.0, n)
    feats = [f for f in FEATURES if f.name in ("ret_5", "ret_15")]
    per_instant = orthogonality(universe, flat, feats)[("ret_5", "ret_15")]

    pooled_x = [v for b in universe for v in flat[b.code + "|ret_5"].tolist()]
    pooled_y = [v for b in universe for v in flat[b.code + "|ret_15"].tolist()]
    pooled = abs(spearman(pooled_x, pooled_y))

    assert pooled > 0.9, f"the shared time move makes the pooled figure high ({pooled:.3f})"
    assert per_instant < 0.2, (
        f"but the cross-sectional rankings are independent ({per_instant:.3f})"
    )


def test_two_features_that_rank_the_universe_alike_are_one_signal():
    """The positive control for the same function: genuine redundancy must
    still read as redundancy, so the per-instant construction is not
    simply reporting everything as independent."""
    rng = np.random.default_rng(17)
    minutes = list(range(300))
    universe = [
        _bars(c, minutes, px=list(100 + np.cumsum(rng.normal(0, 1, len(minutes)))))
        for c in ("A", "B", "C", "D")
    ]
    flat: dict[str, np.ndarray] = {}
    for i, b in enumerate(universe):
        base = rng.normal(0, 1, b.open.size)
        # A monotone transform plus a per-name level offset: the same
        # ranking at every instant, so one signal.
        flat[b.code + "|ret_5"] = base + 100.0 * i
        flat[b.code + "|ret_15"] = base * 3.0 + 300.0 * i
    feats = [f for f in FEATURES if f.name in ("ret_5", "ret_15")]
    out = orthogonality(universe, flat, feats)
    assert out[("ret_5", "ret_15")] == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------- the reported block count


def test_block_ids_are_counted_per_symbol_not_merged():
    """**`continuous_blocks` numbers from 0 for every symbol**, so a set
    over the concatenated IDs merges each name's block 0 into one and
    under-counts. On the real ten-name universe that is **562 against a
    real 5,381** — an order of magnitude, in a figure the run prints as a
    description of its own data."""
    a = _bars("A", list(range(5)) + list(range(1000, 1005)))
    b = _bars("B", list(range(5)) + list(range(2000, 2005)))
    assert symbol_block_count([a, b]) == 4, "two blocks each"
    merged = len(set(np.concatenate([a.blocks, b.blocks]).tolist()))
    assert merged == 2, "the wrong construction, kept here to show the gap"
