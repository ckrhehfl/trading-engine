"""Pure EJ laggard entry and exit with an independent exact payoff calculation."""

from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from research import intraday_chart_cash as cash
from research import intraday_scenario_policy as policy
from research.intraday_candidate_exit import _minute
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _clock, _require, _session


COST_BPS = 33
RISK_BUDGET = Fraction(100_000)
DECISION_OFFSET = 62 * MINUTE
LATE_DECISION_LEAD = 76 * MINUTE
OPEN_LATE_DECISION_LEAD = 46 * MINUTE
SHOCK_DECISION_OFFSETS = (DECISION_OFFSET, 122 * MINUTE, 182 * MINUTE)
HOLD = 30 * MINUTE
BAR_LAG = 3 * MINUTE
DELAY_MINUTES = 5
RETRY_EXPIRY_OFFSET = 123 * MINUTE

_Schedule = Literal["opening", "late", "shock", "open_late", "retry"]


def _validate(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
              session: SessionInputs, delay_minutes: int, *, schedule: _Schedule) -> tuple[datetime, datetime, datetime]:
    _session(session)
    _clock(decision_at)
    if schedule == "shock":
        decision_matches = decision_at - session.opening in SHOCK_DECISION_OFFSETS
    elif schedule == "open_late":
        decision_matches = decision_at == session.closing - OPEN_LATE_DECISION_LEAD
    elif schedule == "late":
        decision_matches = decision_at == session.closing - LATE_DECISION_LEAD
    else:
        decision_matches = decision_at == session.opening + DECISION_OFFSET
    request_at = decision_at + delay_minutes * MINUTE
    entry_at = request_at + MINUTE
    expiry_at = session.opening + RETRY_EXPIRY_OFFSET if schedule == "retry" else decision_at + MINUTE + HOLD
    _require(symbol == session.symbol and symbol == symbol.strip()
             and decision_matches and decision_at >= session.opening
             and decision_at.second == decision_at.microsecond == 0
             and expiry_at < session.closing, "ej_laggard_trade_clock_or_identity_differs")
    _require(type(limit) is Fraction and type(stop) is Fraction and 0 < stop < limit,
             "ej_laggard_invalid_sealed_bounds")
    return request_at, entry_at, expiry_at


def _sizing_episode(symbol: str, start_at: datetime, expiry_at: datetime) -> policy.Episode:
    return policy.start_episode(cash.new_book("EJ-laggard"), symbol, start_at, expiry_at,
                                risk_budget=RISK_BUDGET, cost_bps=COST_BPS)


def requested_quantity(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                       session: SessionInputs) -> int:
    """Reproduce the original opening order size without reading a price."""
    _, _, expiry_at = _validate(symbol, decision_at, limit, stop, session, 0, schedule="opening")
    episode = _sizing_episode(symbol, decision_at, expiry_at)
    quantity, _ = policy._quantity(episode, policy.EntryBounds(stop, limit))
    return quantity


def _empty_arm() -> dict:
    return {
        "status": "no_fill", "gross": "0", "cost": "0", "net": "0", "entry_notional": "0",
        "fill": None, "selected_trigger": "retained", "reason": "insufficient_risk_or_cash_for_one_unit",
        "requested_quantity": 0, "entry_request_at": None, "entry_execute_at": None,
        "entry_at": None, "entry_price": None, "exit_request_at": None, "exit_execute_at": None,
        "exit_at": None, "exit_price": None, "trigger_start_at": None, "trigger_close": None,
        "unknown_at": None,
    }


def _fill(outcome: str, quantity: int, cost: Fraction | None) -> dict:
    return {"outcome": outcome, "quantity": quantity, "cost": None if cost is None else str(cost)}


def _unresolved(arm: dict, reason: str, at: datetime) -> dict:
    return {**arm, "status": "unresolved", "gross": None, "cost": None, "net": None,
            "entry_notional": None, "reason": reason, "unknown_at": at.isoformat()}


def execute_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                  session: SessionInputs) -> dict:
    """Size at the sealed limit and process only required modeled prices."""
    return _execute_trade(symbol, decision_at, limit, stop, session, delay_minutes=0)


def execute_delayed_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                          session: SessionInputs) -> dict:
    """Delay the entry request five minutes from the sealed decision."""
    return _execute_trade(symbol, decision_at, limit, stop, session, delay_minutes=DELAY_MINUTES)


def execute_late_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                       session: SessionInputs) -> dict:
    """Request entry at the fixed session-close-relative decision."""
    return _execute_trade(symbol, decision_at, limit, stop, session, delay_minutes=0, schedule="late")


def execute_shock_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                        session: SessionInputs) -> dict:
    """Request entry at one of the three registered shock decisions."""
    return _execute_trade(symbol, decision_at, limit, stop, session, delay_minutes=0, schedule="shock")


def execute_open_late_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                            session: SessionInputs) -> dict:
    """Use the original execution policy for the opening breadth comparison."""
    return _execute_trade(symbol, decision_at, limit, stop, session, delay_minutes=0, schedule="open_late")


def execute_retry_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                        session: SessionInputs) -> dict:
    """Price the sealed opening retry, keeping the original 60-minute expiry."""
    return _execute_trade(symbol, decision_at, limit, stop, session, delay_minutes=DELAY_MINUTES, schedule="retry")


def _execute_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                   session: SessionInputs, *, delay_minutes: int, schedule: _Schedule = "opening") -> dict:
    request_at, entry_at, expiry_at = _validate(symbol, decision_at, limit, stop, session, delay_minutes, schedule=schedule)
    # The retry book starts at its request because the original attempt stayed flat.
    episode = _sizing_episode(symbol, request_at if schedule == "retry" else decision_at, expiry_at)
    quantity, funds = policy._quantity(episode, policy.EntryBounds(stop, limit))
    arm = _empty_arm()
    if not quantity:
        return arm

    arm.update(requested_quantity=quantity, entry_request_at=request_at.isoformat(),
               entry_execute_at=entry_at.isoformat())
    entry_bar = _minute(session, entry_at)
    if entry_bar is None:
        arm.update(fill=_fill("indeterminate", 0, None), selected_trigger="unknown")
        return _unresolved(arm, "entry_price_unknown", entry_at)
    price = Fraction(entry_bar[0])
    arm["entry_price"] = str(price)
    if not stop < price <= limit:
        arm.update(fill=_fill("declined", 0, Fraction()), reason="open_outside_frozen_entry_bounds")
        return arm

    opened = cash.enter(episode.book, symbol, entry_price=price, observed_prices={}, cost_bps=COST_BPS,
                        decision_funds=funds, requested_quantity=quantity)
    _require(opened.filled and opened.quantity == quantity, "ej_laggard_reserved_entry_refused")
    arm.update(entry_at=entry_at.isoformat(), fill=_fill(opened.outcome, quantity, opened.assumed_cost))

    sale_at = expiry_at
    arm["selected_trigger"] = "time"
    start = entry_at
    while start + BAR_LAG + MINUTE < expiry_at:
        bar = _minute(session, start)
        if bar is None:
            arm["selected_trigger"] = "unknown"
            return _unresolved(arm, "first_structural_close_unknown", start)
        close = Fraction(bar[3])
        if close <= stop:
            sale_at = start + BAR_LAG + MINUTE
            arm.update(selected_trigger="structural", trigger_start_at=start.isoformat(), trigger_close=str(close))
            break
        start += MINUTE

    arm.update(exit_request_at=(sale_at - MINUTE).isoformat(), exit_execute_at=sale_at.isoformat())
    sale_bar = _minute(session, sale_at)
    if sale_bar is None:
        return _unresolved(arm, "exit_price_unknown", sale_at)
    exit_price = Fraction(sale_bar[0])
    sold = cash.exit(opened.book, symbol, quantity=quantity, exit_price=exit_price)
    _require(sold.filled and not sold.book.lots, "ej_laggard_reserved_exit_refused")
    realized, = sold.book.realized_exits
    arm.update(status="closed", gross=str(realized.gross_pnl), cost=str(realized.allocated_entry_cost),
               net=str(realized.net_pnl), entry_notional=str(realized.entry_basis), reason="exited",
               exit_at=sale_at.isoformat(), exit_price=str(exit_price))
    return arm


def _reference_prices(session: SessionInputs, at: datetime) -> tuple[Fraction, Fraction] | None:
    row = session.minutes.get(at)
    if (not isinstance(row, Minute) or (row.symbol, row.start_at) != (session.symbol, at)
            or row.price_state != "observed" or not isinstance(row.available_at, datetime)
            or row.available_at - BAR_LAG != at
            or type(row.ohlc) is not tuple or len(row.ohlc) != 4):
        return None
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in row.ohlc):
        return None
    op, high, low, close = map(Fraction, row.ohlc)
    if 0 < low <= op <= high and low <= close <= high:
        return op, close
    return None


def reference_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                    session: SessionInputs) -> dict:
    """Reconstruct the ledger using independent bar checks and exact cash arithmetic."""
    return _reference_trade(symbol, decision_at, limit, stop, session, delay_minutes=0)


def reference_delayed_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                            session: SessionInputs) -> dict:
    """Independently reconstruct the ledger for the fixed delayed request."""
    return _reference_trade(symbol, decision_at, limit, stop, session, delay_minutes=DELAY_MINUTES)


def reference_late_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                         session: SessionInputs) -> dict:
    """Independently reconstruct the ledger for the fixed late request."""
    return _reference_trade(symbol, decision_at, limit, stop, session, delay_minutes=0, schedule="late")


def reference_shock_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                          session: SessionInputs) -> dict:
    """Independently reconstruct the ledger at a registered shock decision."""
    return _reference_trade(symbol, decision_at, limit, stop, session, delay_minutes=0, schedule="shock")


def reference_open_late_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                              session: SessionInputs) -> dict:
    """Reconstruct the opening breadth comparison payoff."""
    return _reference_trade(symbol, decision_at, limit, stop, session, delay_minutes=0, schedule="open_late")


def reference_retry_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                          session: SessionInputs) -> dict:
    """Rebuild the opening retry from independent quotes and cash arithmetic."""
    return _reference_trade(symbol, decision_at, limit, stop, session, delay_minutes=DELAY_MINUTES, schedule="retry")


def _reference_quantity(limit: Fraction, stop: Fraction, fee: Fraction) -> int:
    notional_cap = min(cash.INITIAL_CAPITAL * cash.ORDER_FRACTION, cash.INITIAL_CAPITAL / (1 + fee))
    risk_cap = RISK_BUDGET / (2 * (limit * (1 + fee) - stop))
    return min(risk_cap // 1, notional_cap // limit)


def reference_requested_quantity(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                                 session: SessionInputs) -> int:
    """Reconstruct the original opening order size with exact independent arithmetic."""
    _validate(symbol, decision_at, limit, stop, session, 0, schedule="opening")
    return _reference_quantity(limit, stop, Fraction(COST_BPS, 10_000))


def _reference_trade(symbol: str, decision_at: datetime, limit: Fraction, stop: Fraction,
                     session: SessionInputs, *, delay_minutes: int, schedule: _Schedule = "opening") -> dict:
    request_at, entry_at, expiry_at = _validate(symbol, decision_at, limit, stop, session, delay_minutes, schedule=schedule)
    fee = Fraction(COST_BPS, 10_000)
    quantity = _reference_quantity(limit, stop, fee)
    arm = _empty_arm()
    if quantity == 0:
        return arm

    arm.update(requested_quantity=quantity, entry_request_at=request_at.isoformat(),
               entry_execute_at=entry_at.isoformat())
    entry = _reference_prices(session, entry_at)
    if entry is None:
        arm.update(fill=_fill("indeterminate", 0, None), selected_trigger="unknown")
        return _unresolved(arm, "entry_price_unknown", entry_at)
    purchase = entry[0]
    arm["entry_price"] = str(purchase)
    if purchase <= stop or purchase > limit:
        arm.update(fill=_fill("declined", 0, Fraction()), reason="open_outside_frozen_entry_bounds")
        return arm

    basis = quantity * purchase
    cost = basis * fee
    arm.update(entry_at=entry_at.isoformat(), fill=_fill("modeled", quantity, cost), selected_trigger="time")
    sale_at = expiry_at
    earliest_exit = entry_at + BAR_LAG + MINUTE
    for offset in range((expiry_at - earliest_exit) // MINUTE):
        scheduled_exit = earliest_exit + offset * MINUTE
        bar_at = scheduled_exit - MINUTE - BAR_LAG
        prices = _reference_prices(session, bar_at)
        if prices is None:
            arm["selected_trigger"] = "unknown"
            return _unresolved(arm, "first_structural_close_unknown", bar_at)
        if prices[1] <= stop:
            sale_at = scheduled_exit
            arm.update(selected_trigger="structural", trigger_start_at=bar_at.isoformat(),
                       trigger_close=str(prices[1]))
            break

    arm.update(exit_request_at=(sale_at - MINUTE).isoformat(), exit_execute_at=sale_at.isoformat())
    sale = _reference_prices(session, sale_at)
    if sale is None:
        return _unresolved(arm, "exit_price_unknown", sale_at)
    gross = quantity * (sale[0] - purchase)
    arm.update(status="closed", gross=str(gross), cost=str(cost), net=str(gross - cost),
               entry_notional=str(basis), reason="exited", exit_at=sale_at.isoformat(), exit_price=str(sale[0]))
    return arm
