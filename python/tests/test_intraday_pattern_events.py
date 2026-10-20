"""Synthetic causal event tests; no prices, experiments or external IO."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from research.intraday_pattern_events import (
    Bar, PatternGapError, PatternValidationError,
    detect_opening_range_breakout, detect_post_drop_recovery,
)


D = Decimal
OPEN = datetime(2025, 1, 2, 9, tzinfo=timezone(timedelta(hours=9)))
CUTOFF = OPEN + timedelta(hours=6)


def bar(index, *, open="100", high="101", low="99", close="100", delay=0):
    start = OPEN + timedelta(minutes=5 * index)
    end = start + timedelta(minutes=5)
    return Bar(start, end, end + timedelta(seconds=delay),
               D(open), D(high), D(low), D(close))


def breakout(bars, **kwargs):
    return detect_opening_range_breakout(
        bars, session_open=OPEN, entry_cutoff=kwargs.pop("entry_cutoff", CUTOFF),
        range_minutes=kwargs.pop("range_minutes", 10), **kwargs,
    )


def recovery(bars, **kwargs):
    return detect_post_drop_recovery(
        bars, session_open=OPEN, entry_cutoff=kwargs.pop("entry_cutoff", CUTOFF),
        lookback_minutes=kwargs.pop("lookback_minutes", 10),
        drop_bps=kwargs.pop("drop_bps", D("500")),
        recovery_bps=kwargs.pop("recovery_bps", D("200")), **kwargs,
    )


def a_bars():
    return [bar(0), bar(1, high="103"), bar(2, high="104", close="103"),
            bar(3, high="104", close="103.01", delay=7)]


def b_bars():
    return [bar(0, high="105"), bar(1),
            bar(2, open="100", high="101", low="95", close="100"),
            bar(3, open="95", high="100", low="94", close="100"),
            bar(4, open="95", high="96", low="94", close="95.88", delay=9)]


def test_opening_range_is_initial_complete_window_then_strict_close():
    bars = a_bars()
    assert breakout(bars[:2]) is None
    assert breakout(bars[:3]) is None  # Equality and intrabar high are insufficient.
    event = breakout(bars)
    assert event.family == "opening_range_breakout"
    assert event.context == tuple(bars[:2])
    assert (event.context_high, event.context_low) == (D("103"), D("99"))
    assert (event.signal_bar_start, event.signal_bar_end) == (bars[3].start_at, bars[3].end_at)
    assert event.decision_at == bars[3].available_at > event.signal_bar_end
    assert event.drop_bar is event.trough_bar is None


def test_range_window_high_is_not_updated_with_later_non_signal_bars():
    bars = [bar(0), bar(1), bar(2, high="110"), bar(3, high="102", close="102")]
    assert breakout(bars).signal_bar_start == bars[3].start_at


def test_recovery_needs_later_bar_and_new_low_cannot_confirm_itself():
    bars = b_bars()
    assert recovery(bars[:3]) is None  # Drop bar itself already closes above recovery.
    assert recovery(bars[:4]) is None  # New low bar itself also closes above recovery.
    event = recovery(bars)
    assert event.family == "post_drop_recovery"
    assert event.context == tuple(bars[:2])
    assert event.context_high == D("105")
    assert event.drop_bar == bars[2]
    assert event.trough_bar == bars[3]
    assert event.decision_at == bars[4].available_at
    assert event.signal_bar_start > event.trough_bar.start_at


def test_drop_uses_prior_window_high_not_current_high_or_incomplete_history():
    bars = [bar(0, high="105"), bar(1, low="100"), bar(2, low="100"),
            bar(3, high="200", low="98"), bar(4)]
    assert recovery(bars) is None
    assert recovery([bar(0, high="1000", low="1")]) is None


def test_first_drop_provenance_is_kept_as_rolling_history_changes():
    bars = b_bars()
    bars.insert(4, bar(4, open="94", high="95", low="94", close="95"))
    bars[5] = bar(5, open="95", high="96", low="94", close="95.88")
    event = recovery(bars)
    assert event.context == tuple(bars[:2])
    assert event.drop_bar == bars[2]
    assert event.trough_bar == bars[3]  # Equal later lows retain the first evidence.
    assert event.signal_bar_start == bars[5].start_at


@pytest.mark.parametrize("detector,make_bars", [(breakout, a_bars), (recovery, b_bars)])
def test_cutoff_uses_availability_and_is_inclusive(detector, make_bars):
    bars = make_bars()
    decision = bars[-1].available_at
    assert detector(bars, entry_cutoff=decision) is not None
    assert detector(bars, entry_cutoff=decision - timedelta(microseconds=1)) is None


@pytest.mark.parametrize("detector,make_bars", [(breakout, a_bars), (recovery, b_bars)])
def test_first_event_stops_consuming_and_invalid_future_cannot_erase_it(detector, make_bars):
    bars = make_bars()
    expected = detector(bars)
    invalid_future = replace(bar(99), high=D("NaN"), available_at=OPEN)
    assert detector([*bars, invalid_future]) == expected

    def stream():
        yield from bars
        raise AssertionError("future input must remain unread")

    assert detector(stream()) == expected


@pytest.mark.parametrize("detector", [breakout, recovery])
@pytest.mark.parametrize("indices", [[1], [0, 2], [0, 0], [0, 1, 0]])
def test_missing_duplicate_and_out_of_order_prefix_fail_before_signal(detector, indices):
    with pytest.raises(PatternGapError):
        detector([bar(index) for index in indices])


@pytest.mark.parametrize("detector", [breakout, recovery])
def test_overlap_and_wrong_duration_fail(detector):
    with pytest.raises(PatternGapError):
        detector([bar(0), replace(bar(1), start_at=OPEN + timedelta(minutes=4))])
    with pytest.raises(PatternValidationError, match="duration"):
        detector([replace(bar(0), end_at=OPEN + timedelta(minutes=4))])


@pytest.mark.parametrize("detector", [breakout, recovery])
@pytest.mark.parametrize("changes", [
    {"open": D("0")}, {"high": D("Infinity")}, {"low": D("NaN")},
    {"close": 100.0}, {"low": D("100.1")}, {"close": D("102")},
    {"start_at": OPEN.replace(tzinfo=None)},
    {"available_at": OPEN.replace(tzinfo=None)},
    {"available_at": OPEN + timedelta(minutes=4)},
])
def test_invalid_consumed_prices_and_times_fail_closed(detector, changes):
    with pytest.raises(PatternValidationError):
        detector([replace(bar(0), **changes)])


@pytest.mark.parametrize("detector", [breakout, recovery])
def test_out_of_order_availability_is_rejected(detector):
    with pytest.raises(PatternValidationError, match="availability"):
        detector([bar(0, delay=600), bar(1)])


@pytest.mark.parametrize("detector", [breakout, recovery])
def test_cutoff_does_not_read_later_prices_or_consume_next_bar(detector):
    future = replace(bar(2), high=D("NaN"))

    def stream():
        yield bar(0)
        yield future
        raise AssertionError("input after cutoff must remain unread")

    assert detector(stream(), entry_cutoff=bar(0).available_at) is None


@pytest.mark.parametrize("value", [0, -5, 7, True, 5.0])
def test_windows_must_be_explicit_positive_five_minute_multiples(value):
    with pytest.raises(PatternValidationError):
        breakout([], range_minutes=value)
    with pytest.raises(PatternValidationError):
        recovery([], lookback_minutes=value)


@pytest.mark.parametrize("name", ["drop_bps", "recovery_bps"])
@pytest.mark.parametrize("value", [D("0"), D("-1"), D("NaN"), D("Infinity"), 100])
def test_thresholds_must_be_positive_finite_decimals(name, value):
    with pytest.raises(PatternValidationError):
        recovery([], **{name: value})


def test_unreachable_nonpositive_drop_price_is_invalid():
    with pytest.raises(PatternValidationError, match="10000"):
        recovery([], drop_bps=D("10000"))


@pytest.mark.parametrize("detector", [breakout, recovery])
def test_session_metadata_is_validated_even_for_empty_input(detector):
    assert detector([]) is None
    with pytest.raises(PatternValidationError, match="entry_cutoff"):
        detector([], entry_cutoff=OPEN - timedelta(seconds=1))
    with pytest.raises(PatternValidationError, match="timezone-aware"):
        detector([], entry_cutoff=CUTOFF.replace(tzinfo=None))


def test_caller_supplies_special_session_open_without_a_hardcoded_clock():
    bars = a_bars()
    shift = timedelta(hours=1)
    shifted = [replace(item, start_at=item.start_at + shift,
                       end_at=item.end_at + shift, available_at=item.available_at + shift)
               for item in bars]
    event = detect_opening_range_breakout(shifted, session_open=OPEN + shift,
                                         entry_cutoff=CUTOFF, range_minutes=10)
    assert event.decision_at == bars[-1].available_at + shift


def test_decimal_threshold_comparisons_do_not_round_at_the_active_precision():
    bars = [bar(0, high="100"),
            bar(1, open="99", high="100", low="99", close="99"),
            bar(2, open="99", high="100", low="99", close="99.9899999999999999999999999999"),
            bar(3, open="99", high="100", low="99", close="99.99")]
    with localcontext() as context:
        context.prec = 3
        event = recovery(bars, lookback_minutes=5, drop_bps=D("100"), recovery_bps=D("100"))
    assert event.signal_bar_start == bars[3].start_at
    exact = D("101.0000000000000000000000000001")
    assert breakout([bar(0), bar(1), bar(2, high=str(exact), close=str(exact))]) is not None
