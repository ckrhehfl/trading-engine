"""EG matched-entry exits over the shared exact nominal lot ledger."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import isfinite
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_chart_labels import _read_minute
from research.intraday_chart_paired_paths import ExitLeg, PathResult, _path_result
from research.intraday_ed_inputs import MINUTE, SessionInputs, _clock, _require, _session


@dataclass(frozen=True)
class HoldingUpdate:
    decision_at: datetime
    close: Fraction | None
    hold_gain: float | None
    action: Literal["hold", "reduce_half", "exit"]
    reason: str


@dataclass(frozen=True)
class ManagementPair:
    fixed: PathResult
    managed: PathResult
    updates: tuple[HoldingUpdate, ...]


def paired_management(
    session: SessionInputs, entry_at: datetime, entry_open: Fraction, stop: Fraction, *,
    cost_bps: int, label_cutoff: datetime,
    hold_gain_at: Callable[[datetime], float | None],
) -> ManagementPair:
    """Fork one paid entry lot and query each forecast only at its decision.

    The forecast estimates continuation versus exiting at the next OPEN on
    the same unit, with its entry cost already paid. A known close at or below
    the stop takes precedence. The first nonpositive forecast requests one
    reduction, and two consecutive nonpositive forecasts request exit.
    """
    _session(session)
    _clock(entry_at)
    _clock(label_cutoff)
    offset = entry_at - session.opening - 3 * MINUTE
    cap = entry_at + 30 * MINUTE
    _require(offset >= 15 * MINUTE and not offset % (5 * MINUTE)
             and cap < session.closing - 15 * MINUTE, "invalid_eg_management_clock")
    _require(type(cost_bps) is int and cost_bps in (33, 43)
             and type(stop) is Fraction and type(entry_open) is Fraction
             and 0 < stop < entry_open, "invalid_eg_management_entry")
    entry = cash.enter(cash.new_book("EG-paired-path"), session.symbol, entry_price=entry_open,
                       observed_prices={}, cost_bps=cost_bps)
    if not entry.filled:
        declined = PathResult("entry_declined", 0, None, None, (), (), None, None, None, 0)
        return ManagementPair(declined, declined, ())
    notional = entry.quantity * entry_open

    def sell(book, step, reason, half=False):
        if half and book.lots[0].quantity == 1:
            return book, ExitLeg(step, reason, "declined", 0, None)
        at = entry_at + step * MINUTE
        row, _ = _read_minute(session, at, label_cutoff)
        price = Fraction(row.ohlc[0]) if row is not None else None
        outcome = (cash.reduce_half(book, session.symbol, exit_price=price) if half else
                   cash.exit(book, session.symbol, quantity=book.lots[0].quantity, exit_price=price))
        return outcome.book, ExitLeg(step, reason, outcome.outcome, outcome.quantity, price)

    book, updates, legs = entry.book, [], []
    reduction_requested = previous_nonpositive = False
    for step in range(5, 30, 5):
        decision_at = entry_at + (step - 1) * MINUTE
        row, _ = _read_minute(session, decision_at - 3 * MINUTE, min(decision_at, label_cutoff))
        close = Fraction(row.ohlc[3]) if row is not None else None
        gain = None
        if close is not None and close <= stop:
            action, reason = "exit", "structural_close_failure"
        else:
            gain = hold_gain_at(decision_at) if close is not None else None
            _require(gain is None or (type(gain) is float and isfinite(gain)), "invalid_eg_hold_gain")
            nonpositive = gain is not None and gain <= 0
            if nonpositive and previous_nonpositive:
                action, reason = "exit", "consecutive_nonpositive_hold_gain"
            elif nonpositive and not reduction_requested:
                action, reason = "reduce_half", "first_nonpositive_hold_gain"
                reduction_requested = True
            else:
                action, reason = "hold", "hold_gain_unavailable" if gain is None else "no_exit_signal"
            previous_nonpositive = nonpositive
        updates.append(HoldingUpdate(decision_at, close, gain, action, reason))
        if action != "hold":
            book, leg = sell(book, step, reason, half=action == "reduce_half")
            legs.append(leg)
            if not book.lots or book.inventory_uncertain:
                break
    if book.lots and not book.inventory_uncertain:
        book, leg = sell(book, 30, "fixed_cap")
        legs.append(leg)
    fixed, fixed_leg = sell(entry.book, 30, "fixed_cap")
    return ManagementPair(
        _path_result(fixed, entry.quantity, notional, entry.assumed_cost, [fixed_leg], ()),
        _path_result(book, entry.quantity, notional, entry.assumed_cost, legs, ()),
        tuple(updates),
    )
