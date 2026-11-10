"""Freeze a single retry after an opening-laggard order misses its limit."""

from datetime import datetime, timedelta
from fractions import Fraction

from research import intraday_candidate_laggard_execution as execution
from research.intraday_candidate_exit import _minute, _number
from research.intraday_candidate_laggard_path import ready
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs, _require


RETRY_GATE_OFFSETS = (63, 64)


def _eligible(rank_plan: dict, path_plan: dict) -> bool:
    state = path_plan["state"]
    _require(state in ("retain", "unresolved", "rebound", "weak"), "ej_laggard_retry_path_differs")
    if state != "weak":
        return False
    _require(ready(rank_plan), "ej_laggard_retry_weak_order_not_ready")
    return True


def _result(state, reason, *, original_open=None, first_close=None, second_close=None, quantity=None) -> dict:
    return {"state": state, "reason": reason,
            "original_open": None if original_open is None else str(original_open),
            "first_close": None if first_close is None else str(first_close),
            "second_close": None if second_close is None else str(second_close),
            "quantity": quantity}


def classify(symbol: str, decision_at: datetime, rank_plan: dict, path_plan: dict,
             session: SessionInputs | None) -> dict:
    """Decide at opening +67 using the original order and observed +63/+64 bars."""
    if not _eligible(rank_plan, path_plan):
        return _result("retain", "original_path_not_weak")
    _require(isinstance(session, SessionInputs), "ej_laggard_retry_session_required")
    limit, stop = _number(rank_plan["limit"]), _number(rank_plan["stop"])
    quantity = execution.requested_quantity(symbol, decision_at, limit, stop, session)
    if quantity == 0:
        return _result("retain", "original_quantity_zero", quantity=quantity)

    first_at, second_at = (session.opening + offset * MINUTE for offset in RETRY_GATE_OFFSETS)
    first = _minute(session, first_at)
    if first is None:
        return _result("unresolved", "original_open_unavailable", quantity=quantity)
    opened, _, _, first_close = map(Fraction, first)
    evidence = {"original_open": opened, "first_close": first_close, "quantity": quantity}
    if opened <= stop:
        return _result("retain", "original_open_at_or_below_stop", **evidence)
    if opened <= limit:
        return _result("retain", "original_open_within_bounds", **evidence)

    second = _minute(session, second_at)
    if second is None:
        return _result("unresolved", "confirmation_close_unavailable", **evidence)
    second_close = Fraction(second[3])
    if min(first_close, second_close) <= stop:
        return _result("retain", "confirmation_structure_lost", second_close=second_close, **evidence)
    return _result("retry", "retry_ready", second_close=second_close, **evidence)


def reference_classify(symbol: str, decision_at: datetime, rank_plan: dict, path_plan: dict,
                       session: SessionInputs | None) -> dict:
    """Reconstruct the retry decision through independent sizing and quotes."""
    if not _eligible(rank_plan, path_plan):
        return _result("retain", "original_path_not_weak")
    _require(isinstance(session, SessionInputs), "ej_laggard_retry_session_required")
    limit, stop = _number(rank_plan["limit"]), _number(rank_plan["stop"])
    quantity = execution.reference_requested_quantity(symbol, decision_at, limit, stop, session)
    if not quantity:
        return _result("retain", "original_quantity_zero", quantity=0)

    cutoff = decision_at + timedelta(minutes=5)
    quote = _reference_quote(session, cutoff - timedelta(minutes=4))
    if quote is None:
        return _result("unresolved", "original_open_unavailable", quantity=quantity)
    opened, first_close = quote[0], quote[3]
    second_close = None
    if stop < opened <= limit:
        state, reason = "retain", "original_open_within_bounds"
    elif opened <= stop:
        state, reason = "retain", "original_open_at_or_below_stop"
    else:
        next_quote = _reference_quote(session, cutoff - timedelta(minutes=3))
        if next_quote is None:
            state, reason = "unresolved", "confirmation_close_unavailable"
        else:
            second_close = next_quote[3]
            if first_close > stop and second_close > stop:
                state, reason = "retry", "retry_ready"
            else:
                state, reason = "retain", "confirmation_structure_lost"
    return _result(state, reason, original_open=opened, first_close=first_close,
                   second_close=second_close, quantity=quantity)
