"""Receipt-timed chart conditions for the local short swing comparison."""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from statistics import median

from research.intraday_event_execution import EntryRequest
from research.intraday_event_scenarios import MinuteBar


@dataclass(frozen=True)
class ContextSignal:
    status: str
    reason: str
    condition: bool | None = None
    request: EntryRequest | None = None
    level: Fraction | None = None


def _span(bars: Mapping[int, MinuteBar], start: int, end: int, clock: int) -> list[MinuteBar] | None:
    window = []
    for minute in range(start, end):
        bar = bars.get(minute)
        if bar is None or bar.available_offset > clock:
            return None
        if bar.offset != minute or bar.available_offset != minute + 3:
            raise ValueError("Expected original bar identity and registered receipt lag")
        window.append(bar)
    return window


def _order(history: list[MinuteBar], length: int, stop: Fraction, level: Fraction,
           condition: bool | None) -> ContextSignal:
    final = history[-1]
    scale = median(bar.high - bar.low for bar in history)
    if scale <= 0 or final.available_offset + 31 >= length:
        return ContextSignal("ineligible", "sizing_or_session_ineligible", condition)
    request = EntryRequest(final.available_offset, final.close, scale, stop)
    return ContextSignal("order", "order", condition, request, level)


def afternoon_breakout(bars: Mapping[int, MinuteBar], length: int) -> ContextSignal:
    """Find the first five-minute closing breakout. Keep its compression label separate."""
    if 247 + 31 >= length:
        return ContextSignal("ineligible", "session_ended_before_window")
    box = _span(bars, 180, 240, 242)
    if box is None:
        return ContextSignal("unknown", "unknown_midday_range")
    high = max(bar.high for bar in box)
    low = min(bar.low for bar in box)
    early = _span(bars, 120, 180, 242)
    compressed = None if early is None else 0 < high - low < max(b.high for b in early) - min(b.low for b in early)
    for start in range(240, 300, 5):
        clock = start + 7
        if clock + 31 >= length:
            return ContextSignal("ineligible", "session_ended_before_window", compressed)
        candle = _span(bars, start, start + 5, clock)
        if candle is None:
            return ContextSignal("unknown", "unknown_before_first_break", compressed)
        if candle[-1].close > high:
            history = _span(bars, start - 10, start + 5, clock)
            if history is None:
                return ContextSignal("unknown", "unknown_sizing_span", compressed)
            return _order(history, length, low, high, compressed)
    return ContextSignal("no_signal", "complete_no_break", compressed)


def prior_late_strength(bars: Mapping[int, MinuteBar], length: int) -> bool | None:
    """A within-day comparison, available before the close. No price crosses dates."""
    clock = length - 5
    end = (clock - 3 + 1) // 5 * 5
    if end - 5 < 240:
        return None
    opening = _span(bars, 0, 240, clock)
    late = _span(bars, end - 5, end, clock)
    if opening is None or late is None:
        return None
    return late[-1].close > max(bar.high for bar in opening)


def opening_rise(bars: Mapping[int, MinuteBar], length: int) -> ContextSignal:
    """Next session's fixed entry. The caller joins the prior state by trading date and symbol."""
    if 17 + 31 >= length:
        return ContextSignal("ineligible", "session_ended_before_window")
    first = _span(bars, 0, 15, 17)
    if first is None:
        return ContextSignal("unknown", "unknown_first_15")
    if first[-1].close <= first[0].open:
        return ContextSignal("no_signal", "complete_no_rise")
    return _order(first, length, min(bar.low for bar in first), first[0].open, None)
