"""Classify the first-hour path from the original opening-laggard decision."""

from datetime import datetime, timedelta
from fractions import Fraction

from research.intraday_candidate_exit import _minute, _number
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs, _clock, _require, _session


READY_OFFSETS = (0, 29, 59)


def ready(plan: dict) -> bool:
    return plan["rank_state"] == "select" and plan["order_state"] == "ready"


def _frozen_order(symbol: str, decision: datetime, plan: dict, session: SessionInputs):
    _session(session)
    _clock(decision)
    _require(session.symbol == symbol and session.opening + 62 * MINUTE == decision
             and decision.second == decision.microsecond == 0 and decision < session.closing,
             "ej_laggard_path_decision_differs")
    limit, original_return = _number(plan["limit"]), _number(plan["return"])
    _require(limit > 0 and -1 < original_return < 0, "ej_laggard_path_invalid_frozen_order")
    return limit, original_return


def _result(state, reason, first=None, mid=None, last=None) -> dict:
    return {"state": state, "reason": reason, "first_open": None if first is None else str(first),
            "mid_close": None if mid is None else str(mid),
            "last_close": None if last is None else str(last)}


def classify(symbol: str, decision: datetime, plan: dict, session: SessionInputs | None) -> dict:
    """Use only bars known by the original decision, keeping partial evidence."""
    if not ready(plan):
        return _result("retain", "original_order_not_ready")
    limit, original_return = _frozen_order(symbol, decision, plan, session)
    bars = [_minute(session, session.opening + offset * MINUTE) for offset in READY_OFFSETS]
    first, mid, last = (None if bar is None else Fraction(bar[field])
                        for bar, field in zip(bars, (0, 3, 3), strict=True))
    if last is not None:
        _require(last == limit, "ej_laggard_path_last_close_differs")
        if first is not None:
            _require(last / first - 1 == original_return, "ej_laggard_path_original_return_differs")
    if None in (first, mid, last):
        return _result("unresolved", "path_prices_unavailable", first, mid, last)
    if last > mid:
        return _result("rebound", "late_close_above_mid", first, mid, last)
    return _result("weak", "late_close_at_or_below_mid", first, mid, last)


def reference_classify(symbol: str, decision: datetime, plan: dict, session: SessionInputs | None) -> dict:
    """Reconstruct the path independently from reference price quotes."""
    if not ready(plan):
        return _result("retain", "original_order_not_ready")
    limit, original_return = _frozen_order(symbol, decision, plan, session)
    quotes = [_reference_quote(session, decision - timedelta(minutes=lag)) for lag in (62, 33, 3)]
    first, mid, last = (None if quote is None else quote[field]
                        for quote, field in zip(quotes, (0, 3, 3), strict=True))
    _require(last is None or last == limit, "ej_laggard_path_last_close_differs")
    _require(first is None or last is None or last - first == original_return * first,
             "ej_laggard_path_original_return_differs")
    if None in (first, mid, last):
        return _result("unresolved", "path_prices_unavailable", first, mid, last)
    if last - mid <= 0:
        return _result("weak", "late_close_at_or_below_mid", first, mid, last)
    return _result("rebound", "late_close_above_mid", first, mid, last)
