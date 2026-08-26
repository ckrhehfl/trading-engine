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
    DEFAULT_ATR_RATIO_WINDOW,
    AtrRatio,
    Regime,
    RegimeClassifier,
    Structure,
    Volatility,
)

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
    c = RegimeClassifier(adx_period=3, atr_period=1, atr_ratio_window=3, min_dwell_bars=0)
    # Calm stretch to warm both axes, then a genuinely wide bar so the
    # volatility axis has something outside its band to resolve on --
    # constant-width bars alone sit at ratio 1.0 forever, which is the
    # separate case asserted below.
    bars = flat_bars(12, width=1.0) + flat_bars(1, width=20.0, start=100)
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
    c = RegimeClassifier(adx_period=3, atr_period=3, atr_ratio_window=4)
    assert all(c.update(b) is None for b in flat_bars(60))


# --- hysteresis -------------------------------------------------------------


def test_a_reading_inside_the_band_holds_the_previous_label():
    """The defining property of hysteresis: once EXPANSION is established,
    a reading of exactly 1.0 -- comfortably inside the 0.8-1.5 band --
    must NOT reset the label. A plain single-threshold classifier would
    flip here; this one holds."""
    c = RegimeClassifier(adx_period=3, atr_period=1, atr_ratio_window=3, min_dwell_bars=0)

    # atr_period=1 makes ATR exactly the bar range, so the ratio is
    # hand-computable. Warm up with range 2.0 bars.
    for b in flat_bars(12, width=1.0):
        c.update(b)

    # Range 20 against a trailing mean of 2 -> ratio 10 -> EXPANSION.
    established = c.update(bar(100, 110.0, 90.0, 100.0))
    assert established is not None
    assert established.volatility is Volatility.EXPANSION

    # Trailing ATR window is now [2, 2, 20], mean 8. A bar of range
    # exactly 8 gives ratio exactly 1.0 -- inside the band.
    held = c.update(bar(101, 104.0, 96.0, 100.0))
    assert held is not None
    assert held.volatility is Volatility.EXPANSION, "hysteresis band failed to hold the label"
    assert held.volatility_bars > established.volatility_bars, "holding should extend the counter"


def test_structure_flips_to_trending_on_a_sustained_trend():
    c = RegimeClassifier(adx_period=3, atr_period=3, atr_ratio_window=3, min_dwell_bars=0)
    last = None
    for b in flat_bars(20, width=1.0):
        last = c.update(b) or last
    for b in trending_bars(40, start=100, step=5.0):
        last = c.update(b) or last
    assert last is not None
    assert last.structure is Structure.TRENDING


# --- minimum dwell ----------------------------------------------------------


def test_min_dwell_blocks_a_label_change_before_the_dwell_is_met():
    c = RegimeClassifier(adx_period=3, atr_period=3, atr_ratio_window=3, min_dwell_bars=50)
    seen = []
    for b in flat_bars(20, width=1.0):
        r = c.update(b)
        if r:
            seen.append(r.structure)
    for b in trending_bars(30, start=100, step=5.0):
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
    c = RegimeClassifier(adx_period=3, atr_period=3, atr_ratio_window=3, min_dwell_bars=0)
    counts = []
    for b in flat_bars(20, width=1.0) + trending_bars(40, start=100, step=5.0):
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
        ({"ratio_low": Decimal("2"), "ratio_high": Decimal("1")}, "ratio_low"),
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
    bars = flat_bars(30, width=1.0) + trending_bars(30, start=100, step=5.0)
    a = RegimeClassifier(adx_period=3, atr_period=3, atr_ratio_window=3, min_dwell_bars=0)
    prefix = [a.update(b) for b in bars[:40]]
    b_ = RegimeClassifier(adx_period=3, atr_period=3, atr_ratio_window=3, min_dwell_bars=0)
    full = [b_.update(x) for x in bars]
    assert prefix == full[:40]
