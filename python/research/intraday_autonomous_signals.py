"""Pure fixed DZ signals over caller-supplied original CC five-minute prefixes.

No input loading, outcomes, means or trial logging. The caller authenticates the
frozen registration, original target identities, provenance and modeled session
clocks before supplying bars. Actual execution also needs the registered review,
profiling and logging gates; this component does not authorize an experiment.
Only consumed bars are validated. A terminal state never inspects later bars.
Decimal inputs are compared through exact Fractions to avoid context rounding.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Literal

from research import intraday_pattern_events as core
from research.intraday_autonomous_round import HYPOTHESIS_IDS


HYPOTHESES = HYPOTHESIS_IDS
FIVE = timedelta(minutes=5)
LAG = timedelta(minutes=2)


@dataclass(frozen=True)
class Detection:
    hypothesis_id: str
    state: Literal["signal", "known_no_signal", "unknown"]
    reason: str
    decision_at: datetime
    observed_through: datetime | None
    signal_bar_start: datetime | None = None
    signal_bar_end: datetime | None = None
    setup_bar_end: datetime | None = None


class InvalidSourceClock(core.PatternValidationError):
    """A consumed supplied bar violates the registered end-plus-two clock."""


def _registered_bars(bars: Iterable[core.Bar], opening: datetime) -> Iterator[core.Bar]:
    expected_start = opening
    for bar in bars:
        if not isinstance(bar, core.Bar):
            raise core.PatternValidationError("stream must contain Bar instances")
        core._aware(bar.end_at, "end_at")
        core._aware(bar.available_at, "available_at")
        if bar.available_at != bar.end_at + LAG:
            raise InvalidSourceClock("invalid_source_clock")
        core._aware(bar.start_at, "start_at")
        if bar.start_at < expected_start:
            raise core.PatternValidationError("invalid_source_sequence")
        expected_start = bar.end_at
        yield bar


def detect_bars(
    hypothesis_id: str,
    bars: Iterable[core.Bar],
    *,
    opening: datetime,
    closing: datetime,
) -> Detection:
    """Return one causal terminal state for one original target/session.

    Pass CC Session.prefix directly. The caller supplies the original modeled
    session clocks; this function never reads Session.minutes or future prices.
    Unknown decision_at is the scheduled assessment whose needed prefix is
    missing, not evidence that a signal was known. An absent required bar is
    unknown, whereas a proven initial failure, invalidation, or timeout is known
    no signal. Invalid prices/source clocks raise; they are not data gaps.

    H1-H4 use the exact open+240 decision cutoff. H5 evaluates at close-78,
    and requires its entire scheduled prefix even when morning strength is false.
    Label/session-end feasibility for a detected event belongs to the later
    label component; this component reports no entry/exit price or outcome.
    """
    if hypothesis_id not in HYPOTHESES:
        raise core.PatternValidationError("unregistered_hypothesis")
    core._aware(opening, "opening")
    core._aware(closing, "closing")
    if (closing <= opening or (closing - opening) % FIVE
            or closing.astimezone(opening.tzinfo).date() != opening.date()):
        raise core.PatternValidationError("invalid_session_clock")

    h = HYPOTHESES.index(hypothesis_id) + 1
    initial_count = 12 if h in (1, 2) else 6
    cutoff = (closing - timedelta(minutes=78) if h == 5
              else opening + timedelta(minutes=240))
    observed: datetime | None = None
    setup_end: datetime | None = None

    def result(state, reason, decision, signal=None):
        return Detection(
            hypothesis_id, state, reason, decision, observed,
            signal.start_at if signal is not None else None,
            signal.end_at if signal is not None else None, setup_end,
        )

    if opening + initial_count * FIVE + LAG > cutoff:
        return result("unknown", "clock_infeasible", cutoff)
    required_count = (cutoff - opening - LAG) // FIVE
    prefix = iter(core._prefix(_registered_bars(bars, opening), opening, cutoff))
    history: list[core.Bar] = []
    high = low = None
    falling = 0
    trigger_count = 0
    morning_strength = False

    for index in range(required_count):
        expected_end = opening + (index + 1) * FIVE
        if expected_end > closing:
            return result("unknown", "clock_infeasible", expected_end + LAG)
        try:
            bar = next(prefix)
        except StopIteration:
            return result("unknown", "missing_prefix", expected_end + LAG)
        except core.PatternGapError:
            return result("unknown", "noncontiguous_prefix", expected_end + LAG)
        observed = bar.end_at
        if index < initial_count:
            history.append(bar)
            if index != initial_count - 1:
                continue
            high = max(Fraction(item.high) for item in history)
            low = min(Fraction(item.low) for item in history)
            close = Fraction(bar.close)
            first_open = Fraction(history[0].open)
            morning_strength = close > first_open
            if h == 1 and not morning_strength:
                return result("known_no_signal", "initial_condition_false", bar.available_at)
            if h == 2 and not (close < first_open and close * 2 < high + low):
                return result("known_no_signal", "initial_condition_false", bar.available_at)
            if h == 4 and high == low:
                return result("known_no_signal", "initial_range_zero", bar.available_at)
            continue

        previous = history[-1]
        close = Fraction(bar.close)
        if h == 1:
            if close * 2 < high + low:
                return result("known_no_signal", "midpoint_invalidated", bar.available_at)
            if setup_end is None:
                falling = falling + 1 if bar.close < previous.close else 0
                if falling == 2:
                    setup_end = bar.end_at
            else:
                trigger_count += 1
                if bar.close > previous.high:
                    return result("signal", "reclaim", bar.available_at, bar)
                if trigger_count == 3:
                    return result("known_no_signal", "first_setup_timeout", bar.available_at)
        elif h == 2:
            if close * 2 > high + low:
                return result("signal", "midpoint_recovered", bar.available_at, bar)
        elif h == 3:
            if setup_end is None:
                if close < low:
                    setup_end = bar.end_at
            else:
                trigger_count += 1
                if close > low:
                    return result("signal", "downside_reclaimed", bar.available_at, bar)
                if trigger_count == 3:
                    return result("known_no_signal", "first_setup_timeout", bar.available_at)
        elif h == 4 and bar.end_at >= opening + timedelta(minutes=90):
            previous_six = history[-6:]
            rolling_high = max(Fraction(item.high) for item in previous_six)
            rolling_low = min(Fraction(item.low) for item in previous_six)
            if (rolling_high - rolling_low) * 2 <= high - low and close > rolling_high:
                return result("signal", "compression_expanded", bar.available_at, bar)
        history.append(bar)

    if h == 5:
        latest = history[-1]
        if morning_strength and Fraction(latest.close) > high:
            return result("signal", "late_strength", cutoff, latest)
        return result("known_no_signal", "fixed_condition_false", cutoff)
    return result("known_no_signal", "decision_cutoff", cutoff)
