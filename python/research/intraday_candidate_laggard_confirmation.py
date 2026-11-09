"""Pure EJ9 confirmation at the fixed delayed-entry decision."""

from datetime import datetime, timedelta
from fractions import Fraction

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs, _clock, _require, _session


def _validate(session: SessionInputs, decision_at: datetime, stop: Fraction) -> None:
    _session(session)
    _clock(decision_at)
    _require(decision_at == session.opening + 62 * MINUTE
             and decision_at.second == decision_at.microsecond == 0
             and decision_at + 5 * MINUTE < session.closing,
             "ej_laggard_confirmation_clock_differs")
    _require(type(stop) is Fraction and stop > 0, "ej_laggard_confirmation_invalid_stop")


def _result(state, reason, closes):
    return {"state": state, "reason": reason,
            "closes": [None if close is None else str(close) for close in closes]}


def confirmation(session: SessionInputs, decision_at: datetime, stop: Fraction) -> dict:
    """Decide at D+5 from exactly the three bars starting D through D+2."""
    _validate(session, decision_at, stop)
    bars = [_minute(session, decision_at + offset * MINUTE) for offset in range(3)]
    closes = [None if bar is None else Fraction(bar[3]) for bar in bars]
    if any(close is None for close in closes):
        return _result("unresolved", "confirmation_close_unknown", closes)
    if any(close <= stop for close in closes):
        return _result("decline", "confirmation_at_or_below_stop", closes)
    if closes[2] < closes[0]:
        return _result("decline", "confirmation_close_weaker", closes)
    return _result("select", "confirmation_stable", closes)


def reference_confirmation(session: SessionInputs, decision_at: datetime, stop: Fraction) -> dict:
    """Verify the same cutoff with the independent reference quote reader."""
    _validate(session, decision_at, stop)
    cutoff = decision_at + timedelta(minutes=5)
    quotes = [_reference_quote(session, cutoff - timedelta(minutes=lag)) for lag in (5, 4, 3)]
    closes = [quote[3] if quote is not None else None for quote in quotes]
    if None in closes:
        state, reason = "unresolved", "confirmation_close_unknown"
    elif min(closes) - stop <= 0:
        state, reason = "decline", "confirmation_at_or_below_stop"
    elif closes[-1] - closes[0] >= 0:
        state, reason = "select", "confirmation_stable"
    else:
        state, reason = "decline", "confirmation_close_weaker"
    return _result(state, reason, closes)
