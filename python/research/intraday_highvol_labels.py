"""Retrospective EG path and fixed-horizon labels over typed archived minutes."""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_chart_labels import _read_minute
from research.intraday_ed_inputs import MINUTE, Issue, Minute, SessionInputs, _clock, _require, _session


Touch = Literal["target_first", "stop_first", "same_minute_unknown", "time", "unavailable"]


@dataclass(frozen=True)
class FirstTouch:
    status: Touch
    minute: datetime | None
    mature_at: datetime | None
    issue: Issue | None = None


@dataclass(frozen=True)
class HighVolPath:
    entry_at: datetime
    exit_at: datetime
    status: Literal["admitted", "entry_refused", "entry_unavailable", "outside_clock"]
    entry_open: Fraction | None = None
    fixed_exit_open: Fraction | None = None
    fixed_gross: Fraction | None = None
    fixed_mature_at: datetime | None = None
    first_touch: FirstTouch | None = None
    entry_issue: Issue | None = None
    fixed_exit_issue: Issue | None = None


def _first_touch(session: SessionInputs, entry: Minute, exit_at: datetime, stop: Fraction,
                 target: Fraction, cutoff: datetime) -> FirstTouch:
    mature_at = entry.available_at
    at = entry.start_at
    while at < exit_at:
        row, issue = (entry, None) if at == entry.start_at else _read_minute(session, at, cutoff)
        if issue is not None:
            return FirstTouch("unavailable", at, None, issue)
        mature_at = max(mature_at, row.available_at)
        op, high, low, _ = row.ohlc
        # The OPEN precedes the rest of the minute. When it is between the
        # barriers, OHLC cannot order a minute that touches both.
        if op <= stop:
            return FirstTouch("stop_first", at, mature_at)
        if op >= target:
            return FirstTouch("target_first", at, mature_at)
        if low <= stop and high >= target:
            return FirstTouch("same_minute_unknown", at, mature_at)
        if low <= stop:
            return FirstTouch("stop_first", at, mature_at)
        if high >= target:
            return FirstTouch("target_first", at, mature_at)
        at += MINUTE
    return FirstTouch("time", None, mature_at)


def label_path(session: SessionInputs, decision_at: datetime, stop: Fraction,
               target: Fraction, *, horizon_minutes: int, label_cutoff: datetime) -> HighVolPath:
    """Compare a fixed exit OPEN and path order without treating touches as fills.

    A missing prefix minute prevents any subsequent touch from proving first
    order. The fixed OPEN return can still be observed. Outcome maturity is
    separate for these two targets so fold admission can use the chosen label.
    """
    _session(session)
    for clock in (decision_at, label_cutoff):
        _clock(clock)
    _require(type(horizon_minutes) is int and horizon_minutes in (30, 60), "invalid_eg_horizon")
    _require(type(stop) is Fraction and type(target) is Fraction and 0 < stop < target,
             "invalid_eg_barriers")
    offset = decision_at - session.opening - 2 * MINUTE
    _require(offset >= 15 * MINUTE and not offset % (5 * MINUTE),
             "invalid_eg_decision_clock")
    entry_at = decision_at + MINUTE
    exit_at = entry_at + horizon_minutes * MINUTE
    if exit_at >= session.closing - 15 * MINUTE:
        return HighVolPath(entry_at, exit_at, "outside_clock")
    entry, issue = _read_minute(session, entry_at, label_cutoff)
    if issue is not None:
        return HighVolPath(entry_at, exit_at, "entry_unavailable", entry_issue=issue)
    price = Fraction(entry.ohlc[0])
    if not stop < price < target:
        return HighVolPath(entry_at, exit_at, "entry_refused", entry_open=price)

    touch = _first_touch(session, entry, exit_at, stop, target, label_cutoff)
    exit_row, exit_issue = _read_minute(session, exit_at, label_cutoff)
    if exit_issue is not None:
        return HighVolPath(entry_at, exit_at, "admitted", entry_open=price,
                           first_touch=touch, fixed_exit_issue=exit_issue)
    exit_open = Fraction(exit_row.ohlc[0])
    return HighVolPath(entry_at, exit_at, "admitted", price, exit_open, exit_open / price - 1,
                       max(entry.available_at, exit_row.available_at), touch)
