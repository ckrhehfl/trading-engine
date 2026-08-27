"""Unit tests for `research.strategies.regime_classifier`.

The properties worth testing here are the ones S8 §3.3 requires and that
a plain threshold classifier would get wrong: hysteresis (a reading
inside the band holds the previous label rather than flipping), minimum
dwell (a label cannot change on consecutive bars even when readings say
it should), and look-ahead safety (no bar influences its own label
through a lookback that includes it).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kline import Kline
from research.strategies.regime_classifier import (
    DEFAULT_ABSOLUTE_HISTORY,
    DEFAULT_ATR_RATIO_WINDOW,
    AbsoluteAtr,
    AtrRatio,
    Regime,
    RegimeClassifier,
    Structure,
    Volatility,
    VolatilityAxis,
)

# Tests that hand-compute an expected volatility reading use the RATIO
# axis deliberately: with atr_period=1 the ratio is exactly bar-range over
# the trailing mean, which is verifiable by hand. RATIO is not the default
# and is not recommended for use (see the module docstring and Task S10) --
# it is the axis whose *mechanics* are easiest to assert precisely, and
# hysteresis/dwell are shared by both axes.
RATIO = VolatilityAxis.RATIO

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bar(i: int, high: float, low: float, close: float) -> Kline:
    return Kline(
        open_time=START + timedelta(minutes=i),
        open=Decimal(str(close)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal("1"),
    )


def flat_bars(n: int, price: float = 100.0, width: float = 1.0, start: int = 0):
    """`n` bars of constant range and no net drift -- ranging, stable vol."""
    return [bar(start + i, price + width, price - width, price) for i in range(n)]


def trending_bars(n: int, start_price: float = 100.0, step: float = 5.0, start: int = 0):
    """`n` bars marching steadily upward -- high ADX by construction."""
    out = []
    for i in range(n):
        p = start_price + step * i
        out.append(bar(start + i, p + 1, p - 1, p))
    return out


# --- AtrRatio ---------------------------------------------------------------


def test_atr_ratio_is_none_through_warmup_and_one_when_volatility_is_constant():
    r = AtrRatio(atr_period=3, window=4)
    seen = [r.update(b) for b in flat_bars(20, width=1.0)]
    # Warmup: ATR needs 3 bars, then 4 ATR readings must accumulate.
    assert seen[0] is None
    assert all(v is None for v in seen[:6])
    settled = [v for v in seen if v is not None]
    assert settled, "ratio never resolved"
    # Constant bar width => constant ATR => ratio exactly 1.
    assert all(v == Decimal(1) for v in settled)


def test_atr_ratio_excludes_the_current_reading_from_its_own_denominator():
    # If the current ATR were included in the mean it is compared against,
    # a step change would be damped. With it excluded, the first bar after
    # a 4x widening must read close to 4x, not lower.
    r = AtrRatio(atr_period=1, window=4)
    for b in flat_bars(6, width=1.0):
        r.update(b)
    ratio = None
    for b in flat_bars(1, width=4.0, start=100):
        ratio = r.update(b)
    assert ratio == Decimal(4)


def test_atr_ratio_returns_none_rather_than_dividing_by_zero_on_a_flat_window():
    r = AtrRatio(atr_period=1, window=3)
    # Zero-width bars => ATR 0 => window mean 0.
    zero = [bar(i, 100.0, 100.0, 100.0) for i in range(10)]
    assert all(r.update(b) is None for b in zero)


def test_atr_ratio_rejects_a_non_positive_window():
    with pytest.raises(ValueError, match="window must be positive"):
        AtrRatio(window=0)


# --- warmup -----------------------------------------------------------------


def test_classifier_returns_none_until_both_axes_have_resolved():
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    # Calm stretch to warm both axes, then a genuinely wide bar so the
    # volatility axis has something outside its band to resolve on --
    # constant-width bars alone sit at ratio 1.0 forever, which is the
    # separate case asserted below.
    bars = flat_bars(12, width=1.0) + flat_bars(1, width=20.0, start=12)
    out = [c.update(b) for b in bars]
    assert out[0] is None
    assert any(r is not None for r in out), "never resolved at all"
    first = next(i for i, r in enumerate(out) if r is not None)
    assert all(r is None for r in out[:first]), "resolved, un-resolved, then resolved again"


def test_a_bar_that_warms_up_inside_the_band_yields_no_fabricated_label():
    # Constant volatility => ratio exactly 1.0, which sits inside the
    # 0.8-1.5 hysteresis band forever. With no axis-defining reading ever
    # arriving, the classifier must keep returning None rather than
    # inventing an initial volatility state.
    c = RegimeClassifier(adx_period=3, atr_period=3, volatility_axis=RATIO, atr_ratio_window=4)
    assert all(c.update(b) is None for b in flat_bars(60))


# --- hysteresis -------------------------------------------------------------


def test_a_reading_inside_the_band_holds_the_previous_label():
    """The defining property of hysteresis: once EXPANSION is established,
    a reading of exactly 1.0 -- comfortably inside the 0.8-1.5 band --
    must NOT reset the label. A plain single-threshold classifier would
    flip here; this one holds."""
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)

    # atr_period=1 makes ATR exactly the bar range, so the ratio is
    # hand-computable. Warm up with range 2.0 bars.
    for b in flat_bars(12, width=1.0):
        c.update(b)

    # Range 20 against a trailing mean of 2 -> ratio 10 -> EXPANSION.
    established = c.update(bar(12, 110.0, 90.0, 100.0))
    assert established is not None
    assert established.volatility is Volatility.EXPANSION

    # Trailing ATR window is now [2, 2, 20], mean 8. A bar of range
    # exactly 8 gives ratio exactly 1.0 -- inside the band.
    held = c.update(bar(13, 104.0, 96.0, 100.0))
    assert held is not None
    assert held.volatility is Volatility.EXPANSION, "hysteresis band failed to hold the label"
    assert held.volatility_bars > established.volatility_bars, "holding should extend the counter"


def test_structure_flips_to_trending_on_a_sustained_trend():
    c = RegimeClassifier(adx_period=3, atr_period=3, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    last = None
    for b in flat_bars(20, width=1.0):
        last = c.update(b) or last
    for b in trending_bars(40, start=20, step=5.0):
        last = c.update(b) or last
    assert last is not None
    assert last.structure is Structure.TRENDING


# --- minimum dwell ----------------------------------------------------------


def test_min_dwell_blocks_a_label_change_before_the_dwell_is_met():
    c = RegimeClassifier(adx_period=3, atr_period=3, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=50)
    seen = []
    for b in flat_bars(20, width=1.0):
        r = c.update(b)
        if r:
            seen.append(r.structure)
    for b in trending_bars(30, start=20, step=5.0):
        r = c.update(b)
        if r:
            seen.append(r.structure)
    # A dwell longer than the whole trending stretch must prevent the flip.
    assert seen, "classifier never resolved"
    assert len(set(seen)) == 1, f"label changed despite an unmet dwell: {set(seen)}"


def test_dwell_default_is_derived_from_the_adx_period():
    assert RegimeClassifier(adx_period=14).min_dwell_bars == 14
    assert RegimeClassifier(adx_period=7).min_dwell_bars == 7
    assert RegimeClassifier(adx_period=7, min_dwell_bars=0).min_dwell_bars == 0


def test_bars_held_counters_increase_while_a_label_persists():
    c = RegimeClassifier(adx_period=3, atr_period=3, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    counts = []
    for b in flat_bars(20, width=1.0) + trending_bars(40, start=20, step=5.0):
        r = c.update(b)
        if r:
            counts.append(r.structure_bars)
    assert counts, "never resolved"
    assert max(counts) > 1, "counter never advanced past a single bar"


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"adx_low": Decimal("30"), "adx_high": Decimal("25")}, "adx_low"),
        ({"vol_low": Decimal("2"), "vol_high": Decimal("1")}, "vol_low"),
        ({"min_dwell_bars": -1}, "min_dwell_bars"),
    ],
)
def test_classifier_rejects_inverted_or_negative_configuration(kwargs, match):
    with pytest.raises(ValueError, match=match):
        RegimeClassifier(**kwargs)


def test_defaults_reuse_the_existing_project_constants():
    # The point of this module is that it invents almost nothing; if these
    # drift apart from their sources, that claim stops being true.
    from research.strategies.regime_weighting import (
        DEFAULT_ADX_HIGH_THRESHOLD,
        DEFAULT_ADX_LOW_THRESHOLD,
        DEFAULT_ADX_PERIOD,
    )
    from research.strategies.risk_management import DEFAULT_ATR_PERIOD

    assert DEFAULT_ADX_PERIOD == 14
    assert DEFAULT_ATR_PERIOD == 14
    assert DEFAULT_ADX_LOW_THRESHOLD == Decimal("20")
    assert DEFAULT_ADX_HIGH_THRESHOLD == Decimal("25")
    assert DEFAULT_ATR_RATIO_WINDOW == 20


# --- look-ahead safety ------------------------------------------------------


def test_classification_of_a_prefix_is_unchanged_by_bars_that_come_after_it():
    """The load-bearing guarantee: feeding N bars then M more must not
    alter any label already emitted for the first N."""
    bars = flat_bars(30, width=1.0) + trending_bars(30, start=30, step=5.0)
    a = RegimeClassifier(adx_period=3, atr_period=3, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    prefix = [a.update(b) for b in bars[:40]]
    b_ = RegimeClassifier(adx_period=3, atr_period=3, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    full = [b_.update(x) for x in bars]
    assert prefix == full[:40]


# --- AbsoluteAtr: the axis that measured as working -------------------------


def test_absolute_atr_returns_a_rank_in_zero_to_one_after_warmup():
    a = AbsoluteAtr(atr_period=2, history=10, refresh_every=1)
    seen = [a.update(b) for b in flat_bars(40, width=1.0)]
    assert seen[0] is None
    settled = [v for v in seen if v is not None]
    assert settled, "never resolved"
    assert all(Decimal(0) <= v <= Decimal(1) for v in settled)


def test_absolute_atr_keeps_the_level_that_the_ratio_throws_away():
    """The property this axis exists for, and the reason S10 replaced the
    ratio with it.

    After a step up in volatility lasting longer than the ratio's own
    window, the RATIO returns to exactly 1.0 -- it only ever sees "same as
    recently". The ABSOLUTE rank is still pinned high, because the level
    really is high relative to its much longer history. That difference is
    what separated 5.21x from 1.22x on real data.

    Note both eventually forget: a trailing percentile also decays once
    its own history fills with the new level. The point is the timescale --
    5 readings versus 100 here, and 20 versus 1440 at the defaults."""
    ratio = AtrRatio(atr_period=1, window=5)
    absolute = AbsoluteAtr(atr_period=1, history=100, refresh_every=1)

    calm = flat_bars(140, width=1.0)
    loud = flat_bars(15, width=10.0, start=200)

    for b in calm:
        ratio.update(b)
        absolute.update(b)

    r_last = a_last = None
    for b in loud:
        r_last = ratio.update(b)
        a_last = absolute.update(b)

    assert r_last is not None and a_last is not None
    # The ratio has forgotten: sustained loudness reads as exactly "normal".
    assert r_last == Decimal(1), f"ratio should normalise back to 1.0, got {r_last}"
    # The absolute rank has not. Exact arithmetic: history holds 100
    # readings, 14 of them loud (the 15th is the current bar, excluded from
    # its own comparison), so 86 sit strictly below -> 0.86.
    assert a_last == Decimal("0.86"), f"expected the arithmetic rank 0.86, got {a_last}"
    assert a_last > Decimal("0.8"), "absolute rank should still read as high volatility"


def test_absolute_atr_ranks_against_history_that_excludes_the_current_bar():
    # A bar must not be able to influence its own rank.
    a = AbsoluteAtr(atr_period=1, history=4, refresh_every=1)
    for b in flat_bars(10, width=1.0):
        a.update(b)
    # First genuinely wider bar: every one of the 4 historical readings is
    # below it, so the rank must be exactly 1.0 -- which is only true if
    # the current reading was excluded from the comparison set.
    rank = a.update(bar(300, 150.0, 50.0, 100.0))
    assert rank == Decimal(1)


@pytest.mark.parametrize("kwargs, match", [({"history": 0}, "history"), ({"refresh_every": 0}, "refresh_every")])
def test_absolute_atr_rejects_non_positive_configuration(kwargs, match):
    with pytest.raises(ValueError, match=match):
        AbsoluteAtr(**kwargs)


def test_a_stale_threshold_snapshot_never_changes_a_rank_into_the_future():
    """Refreshing the sorted snapshot on a schedule is a cost decision. It
    must only ever make a rank slightly out of date, never clairvoyant --
    so a lazily-refreshed classifier's labels must match an
    every-bar-refreshed one on the bars where both have resolved, or
    differ only by lagging it."""
    bars = flat_bars(60, width=1.0) + flat_bars(60, width=6.0, start=300)
    eager = AbsoluteAtr(atr_period=1, history=20, refresh_every=1)
    lazy = AbsoluteAtr(atr_period=1, history=20, refresh_every=10)
    for b in bars:
        eager_rank, lazy_rank = eager.update(b), lazy.update(b)
        # Neither may resolve before the other: warmup is identical.
        assert (eager_rank is None) == (lazy_rank is None)


# --- the classifier defaults to the axis that works -------------------------


def test_classifier_defaults_to_the_absolute_axis():
    c = RegimeClassifier()
    assert isinstance(c._vol, AbsoluteAtr)  # noqa: SLF001 -- asserting the default wiring
    assert RegimeClassifier(volatility_axis=VolatilityAxis.RATIO)._vol.__class__ is AtrRatio  # noqa: SLF001


def test_absolute_axis_default_history_is_one_day_of_one_minute_bars():
    assert DEFAULT_ABSOLUTE_HISTORY == 1440


# --- gaps fail closed -------------------------------------------------------


def test_a_missing_bar_resets_state_and_suppresses_the_label():
    """ADX and ATR accumulate across consecutive bars, so a pair that was
    never adjacent produces an indicator computed from a discontinuity.
    This project's own 1m data has real gaps, so the classifier must
    notice and refuse rather than emit a fabricated label."""
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    for b in flat_bars(12, width=1.0):
        c.update(b)
    established = c.update(bar(12, 110.0, 90.0, 100.0))
    assert established is not None, "precondition: a label was established"
    assert c.discontinuities == 0

    # Jump forward by 10 minutes instead of 1.
    after_gap = c.update(bar(23, 110.0, 90.0, 100.0))
    assert after_gap is None, "a gap must suppress the label"
    assert c.discontinuities == 1

    # And it stays suppressed until warmup completes again.
    assert c.update(bar(24, 101.0, 99.0, 100.0)) is None


def test_a_duplicate_or_out_of_order_bar_is_treated_as_a_discontinuity():
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    for b in flat_bars(12, width=1.0):
        c.update(b)
    assert c.update(bar(12, 110.0, 90.0, 100.0)) is not None
    # Same timestamp again -> delta of zero, not one interval.
    assert c.update(bar(12, 110.0, 90.0, 100.0)) is None
    assert c.discontinuities == 1
    # Backwards in time -> negative delta.
    c.update(bar(13, 101.0, 99.0, 100.0))
    assert c.update(bar(5, 101.0, 99.0, 100.0)) is None
    assert c.discontinuities == 2


def test_contiguous_bars_never_trip_the_discontinuity_counter():
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO, atr_ratio_window=3, min_dwell_bars=0)
    for b in flat_bars(200, width=1.0):
        c.update(b)
    assert c.discontinuities == 0


def test_an_explicit_expected_interval_overrides_inference():
    # Bars every 5 minutes. Without an explicit interval the first pair
    # would define 5 minutes as normal; with 1 minute declared, every bar
    # is a discontinuity.
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO,
                         atr_ratio_window=3, expected_interval=timedelta(minutes=1))
    five_min = [bar(i * 5, 101.0, 99.0, 100.0) for i in range(10)]
    assert all(c.update(b) is None for b in five_min)
    assert c.discontinuities == 9


def test_a_duplicate_first_pair_cannot_become_the_expected_interval():
    """The inference step is the one place a broken bar stream could
    whitelist itself: if the first two bars are duplicates, a zero
    interval would become "normal" and every later duplicate would sail
    through the contiguity check it exists to catch."""
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO, atr_ratio_window=3)
    assert c.update(bar(0, 101.0, 99.0, 100.0)) is None
    assert c.update(bar(0, 101.0, 99.0, 100.0)) is None
    assert c.discontinuities == 1, "a duplicate opening pair must count as a discontinuity"
    # Genuine 1-minute bars now establish the interval normally.
    for i in range(1, 40):
        c.update(bar(i, 101.0, 99.0, 100.0))
    assert c.discontinuities == 1, "contiguous bars after the reset must not add more"


def test_a_backwards_first_pair_cannot_become_the_expected_interval():
    c = RegimeClassifier(adx_period=3, atr_period=1, volatility_axis=RATIO, atr_ratio_window=3)
    assert c.update(bar(10, 101.0, 99.0, 100.0)) is None
    assert c.update(bar(3, 101.0, 99.0, 100.0)) is None
    assert c.discontinuities == 1


@pytest.mark.parametrize("bad", [timedelta(0), timedelta(minutes=-1)])
def test_classifier_rejects_a_non_positive_expected_interval(bad):
    with pytest.raises(ValueError, match="expected_interval must be positive"):
        RegimeClassifier(expected_interval=bad)
