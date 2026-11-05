"""Matched-entry nominal lot paths for EF fixed and dynamic exits."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_chart_folds import ENTRY_OFFSETS, Key
from research.intraday_chart_management import FIXED_EXIT_MINUTES, UPDATE_MINUTES, HoldingDecision, holding_decision
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_pattern_calibration import session_close, session_open


@dataclass(frozen=True)
class ExitLeg:
    offset_after_entry: int
    reason: str
    status: str
    quantity: int
    price: Fraction | None


@dataclass(frozen=True)
class PathResult:
    status: str
    original_quantity: int
    original_notional: Fraction | None
    initial_cost: Fraction | None
    legs: tuple[ExitLeg, ...]
    decisions: tuple[HoldingDecision, ...]
    gross_return: Fraction | None
    net_return: Fraction | None
    average_hold_minutes: Fraction | None
    unresolved_quantity: int | None


def _path_result(book, quantity, notional, paid, legs, decisions) -> PathResult:
    known = not book.inventory_uncertain and not book.lots
    remaining = sum(lot.quantity for lot in book.lots)
    return PathResult("closed" if known else "unavailable", quantity, notional, paid, tuple(legs), tuple(decisions),
                      book.realized_gross / notional if known else None,
                      book.realized_net / notional if known else None,
                      Fraction(sum(leg.quantity * leg.offset_after_entry for leg in legs), quantity) if known else None,
                      remaining if not known else 0)


def _quote(snapshot, key):
    row = snapshot.row(*key)
    if (row.session, row.symbol, row.offset) != key or row.execution_state == "outside_clock":
        raise InputRefusal("paired_ef_quote_coordinate_or_clock_differs")
    if (row.execution_state not in ("available", "unavailable")
            or (row.execution_state == "available") != (row.execution_open is not None)):
        raise InputRefusal("paired_ef_quote_state_differs")
    return row.execution_open


def paired_exit_paths(snapshot, key: Key, *, cost_bps: int,
                      gross15_at: Callable[[Key], float | None]) -> tuple[PathResult, PathResult]:
    """Use one identical initial lot. No released cash is redeployed here.

    The caller supplies only an originally admitted entry and the same fitted
    model's clock-keyed forecasts. A path is an observation, not an account
    return, trade count commitment or known fill.
    """
    day, symbol, offset = key
    entry_at = session_open(day) + (offset + 3) * MINUTE
    if (type(cost_bps) is not int or cost_bps not in (33, 43) or offset not in ENTRY_OFFSETS
            or entry_at + FIXED_EXIT_MINUTES * MINUTE >= session_close(day) - 15 * MINUTE):
        raise InputRefusal("registered_ef_paired_path_clock_and_cost_required")
    entry_price = _quote(snapshot, key)
    entry = cash.enter(cash.new_book("EF-paired-path"), symbol, entry_price=entry_price,
                       observed_prices={}, cost_bps=cost_bps)
    if not entry.filled:
        result = PathResult("entry_unavailable" if entry.book.inventory_uncertain else "entry_declined",
                            0, None, None, (), (), None, None, None,
                            None if entry.book.inventory_uncertain else 0)
        return result, result
    quantity = entry.quantity
    notional = quantity * entry_price

    def sell(book, offset_after_entry, reason, *, half):
        quote = _quote(snapshot, (day, symbol, offset + offset_after_entry))
        if half:
            outcome = cash.reduce_half(book, symbol, exit_price=quote)
        else:
            outcome = cash.exit(book, symbol, quantity=book.lots[0].quantity, exit_price=quote)
        return outcome.book, ExitLeg(offset_after_entry, reason, outcome.outcome, outcome.quantity, quote)

    managed, legs, history = entry.book, [], []
    for step in UPDATE_MINUTES:
        forecast = gross15_at((day, symbol, offset + step))
        decision = holding_decision(entry_at, tuple(history), entry_at + (step - 1) * MINUTE, forecast)
        history.append(decision)
        if decision.action == "hold":
            continue
        # A one-unit reduction is declined without consulting a future quote.
        if decision.action == "reduce_half" and managed.lots[0].quantity == 1:
            legs.append(ExitLeg(step, decision.reason, "declined", 0, None))
            continue
        managed, leg = sell(managed, step, decision.reason, half=decision.action == "reduce_half")
        legs.append(leg)
        if managed.inventory_uncertain or not managed.lots:
            break
    if managed.lots and not managed.inventory_uncertain:
        managed, leg = sell(managed, FIXED_EXIT_MINUTES, "fixed_cap", half=False)
        legs.append(leg)
    fixed, fixed_leg = sell(entry.book, FIXED_EXIT_MINUTES, "fixed_cap", half=False)
    return (_path_result(fixed, quantity, notional, entry.assumed_cost, (fixed_leg,), ()),
            _path_result(managed, quantity, notional, entry.assumed_cost, legs, history))
