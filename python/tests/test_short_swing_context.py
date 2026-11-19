"""Synthetic, receipt-timed tests for the two chart conditions."""

from dataclasses import replace

import pytest

from research.intraday_event_scenarios import MinuteBar
from research.short_swing_context import afternoon_breakout, opening_rise, prior_late_strength


def minute(i, close=100, width=2):
    return MinuteBar(i, 100, max(close, 100 + width), 100 - width, close, i + 3)


def tape():
    return {i: minute(i, width=1 if 180 <= i < 240 else 2) for i in range(380)}


def test_final_break_uses_only_the_trigger_close_and_registered_information_clock():
    bars = tape()
    bars[244] = minute(244, close=101)  # Equal to the prior high.
    bars[299] = minute(299, close=103)
    signal = afternoon_breakout(bars, 380)
    assert signal.condition and signal.status == "order"
    assert (signal.request.decision_offset, signal.request.reference_close, signal.request.scale,
            signal.request.structure_stop, signal.level) == (302, 103, 4, 99, 101)
    assert afternoon_breakout({i: b for i, b in bars.items() if i <= 299}, 380) == signal
    assert afternoon_breakout(bars, 330).status == "ineligible"


def test_missing_condition_and_missing_firstness_are_distinct():
    bars = tape()
    bars[254] = minute(254, close=104)
    del bars[160]
    assert afternoon_breakout(bars, 380).status == "order"
    assert afternoon_breakout(bars, 380).condition is None
    del bars[242]
    assert afternoon_breakout(bars, 380).reason == "unknown_before_first_break"


def test_first_break_is_final_even_when_a_later_candle_is_missing():
    bars = tape()
    bars[244] = minute(244, close=102)
    del bars[245]
    assert afternoon_breakout(bars, 380).request.decision_offset == 247
    bars[244] = replace(bars[244], available_offset=248)
    assert afternoon_breakout(bars, 380).status == "unknown"


def test_flat_midday_box_does_not_qualify_as_compression():
    bars = tape()
    bars.update({i: minute(i, width=0) for i in range(180, 240)})
    bars[254] = minute(254, close=103)
    assert afternoon_breakout(bars, 380).condition is False


def test_prior_state_requires_only_declared_windows_and_uses_last_received_block():
    bars = tape()
    bars[369] = minute(369, close=105)
    bars[374] = minute(374, close=98)
    del bars[255]
    assert prior_late_strength(bars, 380) is True
    bars[369] = minute(369, close=102)
    assert prior_late_strength(bars, 380) is False
    del bars[120]
    assert prior_late_strength(bars, 380) is None
    assert prior_late_strength({}, 250) is None


def test_opening_rise_is_same_day_only_and_requires_positive_observable_scale():
    bars = {i: minute(i) for i in range(15)}
    assert opening_rise(bars, 380).status == "no_signal"
    bars[14] = minute(14, close=101)
    order = opening_rise(bars, 380)
    assert (order.request.decision_offset, order.request.structure_stop, order.level) == (17, 98, 100)
    flat = {i: minute(i, width=0) for i in range(14)} | {14: bars[14]}
    assert opening_rise(flat, 380).status == "ineligible"
    del bars[2]
    assert opening_rise(bars, 380).status == "unknown"


def test_source_identity_cannot_silently_change():
    bars = tape()
    bars[50] = bars[51]
    with pytest.raises(ValueError, match="identity"):
        prior_late_strength(bars, 380)


def test_impossible_expiry_precedes_missing_candle_at_session_boundary():
    assert opening_rise({}, 48).status == "ineligible"
    assert opening_rise({}, 49).status == "unknown"
    assert afternoon_breakout({}, 278).status == "ineligible"
    bars = tape()
    del bars[285]
    assert afternoon_breakout(bars, 323).status == "ineligible"
    assert afternoon_breakout(bars, 324).status == "unknown"
