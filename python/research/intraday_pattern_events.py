"""Pure, causal price events over one caller-supplied session's five-minute bars.

No IO, tuning, fills or returns. Parameters and the session opening time must be
fixed by the caller. Only the prefix through the first event is consumed; an
exhausted prefix is not a certificate of complete session coverage.
"""

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal


_FIVE_MINUTES = timedelta(minutes=5)


class PatternValidationError(ValueError):
    """Invalid parameters or an invalid consumed bar."""


class PatternGapError(PatternValidationError):
    """The consumed stream is not a contiguous prefix from session open."""


@dataclass(frozen=True)
class Bar:
    start_at: datetime
    end_at: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class PatternEvent:
    family: Literal["opening_range_breakout", "post_drop_recovery"]
    decision_at: datetime
    signal_bar_start: datetime
    signal_bar_end: datetime
    context: tuple[Bar, ...]
    drop_bar: Bar | None = None
    trough_bar: Bar | None = None

    @property
    def context_high(self) -> Decimal:
        return max(bar.high for bar in self.context)

    @property
    def context_low(self) -> Decimal:
        return min(bar.low for bar in self.context)


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PatternValidationError(f"{name} must be a timezone-aware datetime")


def _minutes(value: int, name: str) -> int:
    if type(value) is not int or value <= 0 or value % 5:
        raise PatternValidationError(f"{name} must be positive and divisible by five")
    return value // 5


def _positive_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PatternValidationError(f"{name} must be a positive finite Decimal")


def _prefix(
    bars: Iterable[Bar], session_open: datetime, entry_cutoff: datetime,
) -> Iterator[Bar]:
    _aware(session_open, "session_open")
    _aware(entry_cutoff, "entry_cutoff")
    if entry_cutoff < session_open:
        raise PatternValidationError("entry_cutoff precedes session_open")
    expected_start, previous_available = session_open, session_open
    for bar in bars:
        if not isinstance(bar, Bar):
            raise PatternValidationError("stream must contain Bar instances")
        _aware(bar.available_at, "available_at")
        if bar.available_at > entry_cutoff:
            return  # Do not inspect prices or consume later bars after cutoff.
        _aware(bar.start_at, "start_at")
        _aware(bar.end_at, "end_at")
        if bar.start_at != expected_start:
            raise PatternGapError("missing, duplicate, overlapping or out-of-order bar")
        if bar.end_at - bar.start_at != _FIVE_MINUTES:
            raise PatternValidationError("bar duration must be exactly five minutes")
        if bar.start_at.astimezone(session_open.tzinfo).date() != session_open.date():
            raise PatternValidationError("bar belongs to a different session date")
        if bar.available_at < bar.end_at or bar.available_at < previous_available:
            raise PatternValidationError("bar availability precedes completion or prior availability")
        for name in ("open", "high", "low", "close"):
            _positive_decimal(getattr(bar, name), name)
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise PatternValidationError("OHLC prices violate low/open/close/high ordering")
        expected_start, previous_available = bar.end_at, bar.available_at
        yield bar


def detect_opening_range_breakout(
    bars: Iterable[Bar], *, session_open: datetime, entry_cutoff: datetime,
    range_minutes: int,
) -> PatternEvent | None:
    """First subsequent completed close strictly above the initial range high.

    ``context`` is exactly the completed opening range. The signal close is an
    observation, never an executable fill; decision_at is its availability time.
    """
    count = _minutes(range_minutes, "range_minutes")
    context: list[Bar] = []
    for bar in _prefix(bars, session_open, entry_cutoff):
        if len(context) < count:
            context.append(bar)
        elif bar.close > max(item.high for item in context):
            return PatternEvent("opening_range_breakout", bar.available_at,
                                bar.start_at, bar.end_at, tuple(context))
    return None


def detect_post_drop_recovery(
    bars: Iterable[Bar], *, session_open: datetime, entry_cutoff: datetime,
    lookback_minutes: int, drop_bps: Decimal, recovery_bps: Decimal,
) -> PatternEvent | None:
    """First confirmed recovery after the first drop against a full prior window.

    A drop is low <= prior-window high * (1 - drop_bps / 10000). A subsequent
    bar must close >= latest trough * (1 + recovery_bps / 10000); a new low
    resets the trough and cannot confirm on that same bar. There is no timeout
    or restart of the first drop before the caller's cutoff. Context retains
    the original prior window; drop_bar and trough_bar retain their evidence.
    """
    count = _minutes(lookback_minutes, "lookback_minutes")
    _positive_decimal(drop_bps, "drop_bps")
    _positive_decimal(recovery_bps, "recovery_bps")
    if drop_bps >= 10000:
        raise PatternValidationError("drop_bps must be less than 10000")
    drop_factor = Fraction(1) - Fraction(drop_bps) / 10000
    recovery_factor = Fraction(1) + Fraction(recovery_bps) / 10000
    history: deque[Bar] = deque(maxlen=count)
    context: tuple[Bar, ...] = ()
    drop_bar: Bar | None = None
    trough_bar: Bar | None = None
    for bar in _prefix(bars, session_open, entry_cutoff):
        if drop_bar is None:
            if len(history) == count and Fraction(bar.low) <= max(
                Fraction(item.high) for item in history
            ) * drop_factor:
                context, drop_bar, trough_bar = tuple(history), bar, bar
        elif bar.low < trough_bar.low:
            trough_bar = bar
        elif Fraction(bar.close) >= Fraction(trough_bar.low) * recovery_factor:
            return PatternEvent("post_drop_recovery", bar.available_at,
                                bar.start_at, bar.end_at, context, drop_bar, trough_bar)
        history.append(bar)
    return None
