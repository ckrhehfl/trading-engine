"""Independent modeled exit and fresh reentry checks for an EH execution row."""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from research.intraday_campaign_cash_book import INITIAL_CAPITAL, ORDER_FRACTION
from research.intraday_campaign_readiness import KST
from research.intraday_ed_inputs import FIVE, MINUTE, SessionInputs, _require, _session
from research.intraday_pattern_events import Bar
from research.intraday_scenario_open_reference import _aggregate, _prefix_rows, _price_row
from research.intraday_scenario_rollout import ReplayEvent
from research.intraday_scenario_snapshot import _time


@dataclass(frozen=True)
class _ClosedHolding:
    at: datetime
    sales: tuple[tuple[int, Fraction], ...]


def _trade(action, decision_at, execute_at, requested, status, quantity, price, reason) -> list:
    return [action, _time(decision_at), _time(execute_at), requested, status, quantity,
            None if price is None else str(price), reason]


def _orders(events: tuple[ReplayEvent, ...]) -> list:
    orders = []
    pending = None
    for event in events:
        if event.execute_at is not None:
            _require(pending is None and event.fill_status is None, "eh_execution_trace_overlapping_requests")
            pending = event
        if event.fill_status is not None:
            _require(pending is not None and (event.action, event.at) == (pending.action, pending.execute_at),
                     "eh_execution_trace_unmatched_fill")
            orders.append(_trade(event.action, pending.at, event.at, pending.quantity,
                                 event.fill_status, event.quantity, event.open_price, pending.reason))
            pending = None
    _require(pending is None, "eh_execution_trace_pending_request")
    return orders


def _modeled_open(session: SessionInputs, at: datetime) -> Fraction | None:
    row, _ = _price_row(session, at, at + 3 * MINUTE)
    return None if row is None else Fraction(row.ohlc[0])


def _verify_request(trade: list | None, action: str, at: datetime, quantity: int, reason: str) -> None:
    _require(type(trade) is list and len(trade) == 8 and type(trade[3]) is int
             and trade[:4] == [action, _time(at), _time(at + MINUTE), quantity]
             and trade[7] == reason, "eh_exit_reference_request_differs")


def _verify_fill(trade: list, price: Fraction | None, outcome: str, filled: int) -> None:
    _require(type(trade[5]) is int and trade[4:7] == [outcome, filled, None if price is None else str(price)],
             "eh_exit_reference_fill_differs")


def _verify_holding(
    status: str, trades: list, session: SessionInputs, *, entered: datetime, expiry: datetime,
    stop: Fraction, quantity: int, discretionary: bool, assessment_clocks: list[datetime] | None = None,
) -> _ClosedHolding | None:
    """Check sale precedence and collect eligible assessment clocks when requested."""
    cap = min(expiry, entered + 30 * MINUTE)
    at, unreviewed = entered + MINUTE, entered
    records = iter(trades)
    current = next(records, None)
    reduced = False
    sales = []
    while at < cap:
        missing = False
        reason = None
        while unreviewed <= at - 3 * MINUTE:
            row, _ = _price_row(session, unreviewed, at)
            unreviewed += MINUTE
            if row is None:
                missing = True
            elif Fraction(row.ohlc[3]) <= stop:
                reason = "structural_close_failure"
                break
        if reason is None and at + MINUTE == cap:
            reason = "fixed_episode_end"
        action = "exit"
        if reason is None:
            if missing:
                _require(status == "unresolved" and current is None, "eh_exit_reference_missing_close_differs")
                return None
            assessment_due = at >= entered + 3 * MINUTE and not (at - session.opening - 2 * MINUTE) % FIVE
            if assessment_due and assessment_clocks is not None:
                assessment_clocks.append(at)
            if current is None or current[1] != _time(at):
                at += MINUTE
                continue
            _require(discretionary and assessment_due and current[0] in ("reduce_half", "exit"),
                     "eh_exit_reference_discretionary_request_differs")
            action = current[0]
            reason = "higher_value_of_reduction" if action == "reduce_half" else "higher_value_of_exit"
        _require(action != "reduce_half" or (not reduced and quantity > 1),
                 "eh_exit_reference_reduction_differs")
        requested = quantity // 2 if action == "reduce_half" else quantity
        trade = current
        _verify_request(trade, action, at, requested, reason)
        executed = at + MINUTE
        price = _modeled_open(session, executed)
        current = next(records, None)
        if price is None:
            _verify_fill(trade, None, "indeterminate", 0)
            _require(status == "unresolved" and current is None, "eh_exit_reference_missing_open_differs")
            return None
        _verify_fill(trade, price, "modeled", requested)
        sales.append((requested, price))
        if action == "exit":
            _require(status == "closed" and current is None, "eh_exit_reference_closed_holding_differs")
            return _ClosedHolding(executed, tuple(sales))
        quantity -= requested
        reduced = True
        # The fill consumes this clock. Catch up all newly due closes at the next decision.
        at = executed + MINUTE
    _require(status == "unresolved" and current is None, "eh_exit_reference_terminal_holding_differs")
    return None


def _first_confirmation(
    session: SessionInputs, *, start: int, last: datetime, level: Fraction, band: Fraction, below: bool,
) -> tuple[str, Bar | None]:
    reclaimed = False
    retest_high = previous_close = None
    above_highs = []
    while (at := session.opening + (start + 7) * MINUTE) <= last:
        minutes, issues = _prefix_rows(session, range(start, start + 5), at)
        if issues:
            return "unresolved", None
        bar = _aggregate(minutes)
        close = Fraction(bar.close)
        if below:
            if not reclaimed:
                reclaimed = close > level + band
            elif close <= level - band:
                return "not_reentered", None
            elif retest_high is None and close < previous_close:
                if Fraction(bar.low) <= level - band:
                    return "not_reentered", None
                retest_high = Fraction(bar.high)
            elif retest_high is not None and close > retest_high:
                return "ready", bar
        elif Fraction(bar.low) <= level:
            return "not_reentered", None
        elif len(above_highs) < 2:
            above_highs.append(Fraction(bar.high))
        elif close > max(above_highs):
            return "ready", bar
        previous_close = close
        start += 5
    return "not_reentered", None


def _verify_fresh(
    arm: list, closed: _ClosedHolding | None, session: SessionInputs, *, entered: datetime, expiry: datetime,
    level: Fraction, band: Fraction, quantity: int, entry_price: Fraction, cost: Fraction, risk_budget: Fraction,
    assessment_clocks: list[datetime],
) -> None:
    if closed is None:
        _require(arm == ["unresolved", []], "eh_exit_reference_fresh_after_unknown_close")
        return
    elapsed = (closed.at - session.opening) // MINUTE
    start = (elapsed + 4) // 5 * 5
    last = expiry - 6 * MINUTE
    if session.opening + (start + 7) * MINUTE > last:
        _require(arm == ["not_reentered", []], "eh_exit_reference_fresh_expired_differs")
        return

    lowest = min(entry_price, closed.sales[-1][1])
    if lowest >= level:
        first_offset = (entered - session.opening) // MINUTE
        final_offset = (closed.at - session.opening) // MINUTE
        known, issues = _prefix_rows(session, range(first_offset, final_offset), closed.at + 2 * MINUTE)
        lowest = min(lowest, min((Fraction(row.ohlc[2]) for row in known), default=lowest))
        if lowest >= level and (issues or lowest == level):
            _require(arm == ["unresolved", []], "eh_exit_reference_fresh_route_differs")
            return
    status, confirmation = _first_confirmation(session, start=start, last=last, level=level, band=band,
                                               below=lowest < level)
    if confirmation is None:
        _require(arm == [status, []], "eh_exit_reference_fresh_evidence_differs")
        return
    at = confirmation.available_at
    close = Fraction(confirmation.close)
    stop = min(Fraction(confirmation.low), level - band)
    limit = close + band
    if not 0 < stop < close <= limit:
        _require(arm == ["not_reentered", []], "eh_exit_reference_fresh_bounds_differs")
        return

    paid_unit_basis = entry_price * (1 + cost)
    cash = INITIAL_CAPITAL - quantity * paid_unit_basis + sum(sold * price for sold, price in closed.sales)
    losses = sum(max(0, sold * (paid_unit_basis - price)) for sold, price in closed.sales)
    budget = max(0, risk_budget - losses)
    ceiling = min(cash * ORDER_FRACTION, cash / (1 + cost))
    fresh_quantity = min(budget // (limit * (1 + cost) - stop), ceiling // limit)
    if not fresh_quantity:
        _require(arm == ["not_reentered", []], "eh_exit_reference_fresh_budget_differs")
        return
    terminal, trades = arm
    _require(bool(trades), "eh_exit_reference_fresh_request_missing")
    entry = trades[0]
    _verify_request(entry, "reenter", at, fresh_quantity, "higher_value_of_entry")
    execute = at + MINUTE
    price = _modeled_open(session, execute)
    if price is None:
        _verify_fill(entry, None, "indeterminate", 0)
        _require(terminal == "unresolved" and len(trades) == 1, "eh_exit_reference_fresh_missing_open_differs")
    elif not stop < price <= limit:
        _verify_fill(entry, price, "declined", 0)
        _require(terminal == "not_reentered" and len(trades) == 1, "eh_exit_reference_fresh_decline_differs")
    else:
        _verify_fill(entry, price, "modeled", fresh_quantity)
        _verify_holding(terminal, trades[1:], session, entered=execute, expiry=expiry, stop=stop,
                        quantity=fresh_quantity, discretionary=False, assessment_clocks=assessment_clocks)


def verify_management_exits(
    row: dict, session: SessionInputs, *, level: Fraction, band: Fraction, risk_budget: Fraction,
    cost_bps: int, fresh_reentry: bool,
) -> None:
    """Check a confirmed row after its first entry, shape and utilities have been audited.

    Level and band come from the sealed first breakout. The caller supplies the
    matching authenticated original session and separately verifies utility scores.
    Reconstruct the full assessment schedule even when no discretionary order was saved.
    """
    _session(session)
    _require(row["status"] == "confirmed" and row["symbol"] == session.symbol
             and row["day"] == session.opening.astimezone(KST).date().isoformat()
             and all(type(value) is Fraction and value > 0 for value in (level, band, risk_budget))
             and type(cost_bps) is int and cost_bps in (33, 43) and type(fresh_reentry) is bool,
             "eh_exit_reference_scope_differs")
    initial = row["initial"]
    entered = datetime.fromisoformat(initial["trade"][2])
    expiry = datetime.fromisoformat(row["event_expiry"])
    quantity, entry_price = initial["trade"][5], Fraction(initial["trade"][6])
    stop = Fraction(initial["stop"])
    _require(session.opening < entered < expiry < session.closing
             and type(quantity) is int and quantity > 0 and 0 < stop < entry_price,
             "eh_exit_reference_initial_position_differs")
    arms = row["arms"]
    expected = {"fixed", "dynamic", "fresh_increment"} if fresh_reentry else {"fixed", "dynamic"}
    _require(arms.keys() == expected, "eh_exit_reference_arms_differ")
    fixed, dynamic = arms["fixed"], arms["dynamic"]
    _verify_holding(*fixed, session, entered=entered, expiry=expiry, stop=stop, quantity=quantity,
                    discretionary=False)
    first_clocks, fresh_clocks = [], []
    closed = _verify_holding(*dynamic, session, entered=entered, expiry=expiry, stop=stop, quantity=quantity,
                             discretionary=True, assessment_clocks=first_clocks)
    if fresh_reentry:
        _verify_fresh(arms["fresh_increment"], closed, session, entered=entered, expiry=expiry, level=level,
                      band=band, quantity=quantity, entry_price=entry_price, cost=Fraction(cost_bps, 10_000),
                      risk_budget=risk_budget, assessment_clocks=fresh_clocks)
    expected_clocks = [(_time(at), attempt) for attempt, clocks in enumerate((first_clocks, fresh_clocks), 1)
                       for at in clocks]
    decisions = row.get("decisions")
    _require(type(decisions) is list and all(type(item) is list and len(item) == 6 and type(item[2]) is int
                                             for item in decisions)
             and [(item[0], item[2]) for item in decisions] == expected_clocks,
             "eh_exit_reference_assessment_schedule_differs")
