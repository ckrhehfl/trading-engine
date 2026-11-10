"""Select late rising names and independently reconstruct the morning peer gate."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from fractions import Fraction
from statistics import median

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs, _session


OPENING_LAST_OFFSET = 29
DECISION_LEAD_MINUTES = 46
LATE_LOOKBACK_MINUTES = 15
PEER_QUORUM = 10
PEER_THRESHOLD_BPS = 33
_BAR_LAG_MINUTES = 3


def _decision_at(sessions: Mapping[str, SessionInputs]) -> datetime:
    if not isinstance(sessions, Mapping) or len(sessions) != 20:
        raise ValueError("EJ open-late requires exactly 20 roster members")
    clocks = set()
    for symbol, session in sessions.items():
        _session(session)
        if symbol != session.symbol:
            raise ValueError("EJ open-late session identity differs")
        opening, closing = session.opening, session.closing
        decision = closing - DECISION_LEAD_MINUTES * MINUTE
        if (opening.date() != closing.date()
                or any(at.second or at.microsecond for at in (opening, closing))
                or decision < opening + (OPENING_LAST_OFFSET + _BAR_LAG_MINUTES) * MINUTE):
            raise ValueError("EJ open-late session geometry differs")
        clocks.add((opening.isoformat(), closing.isoformat()))
    if len(clocks) != 1:
        raise ValueError("EJ open-late sessions do not share one opening and closing")
    return decision


def _plan(late, opening, endpoints, peers, center, gate, state, order, reason, *, limit=None, stop=None):
    first_open, last_close = endpoints
    return {"rank_state": state, "gated_state": gate if state == "select" else state,
            "order_state": order, "reason": reason,
            "limit": None if limit is None else str(limit), "stop": None if stop is None else str(stop),
            "late_return": None if late is None else str(late),
            "opening_first_open": None if first_open is None else str(first_open),
            "opening_last_close": None if last_close is None else str(last_close),
            "opening_return": None if opening is None else str(opening),
            "opening_peer_count": peers, "opening_peer_median": None if center is None else str(center),
            "opening_gate": gate}


def select_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Seal eligible late entries and the gate from other roster members only."""
    decision = _decision_at(sessions)
    morning, endpoints, afternoon = {}, {}, {}
    starts = tuple(decision - offset * MINUTE for offset in range(
        LATE_LOOKBACK_MINUTES + _BAR_LAG_MINUTES - 1, _BAR_LAG_MINUTES - 1, -1))
    for symbol, session in sessions.items():
        first = _minute(session, session.opening)
        last = _minute(session, session.opening + OPENING_LAST_OFFSET * MINUTE)
        first_open = None if first is None else Fraction(first[0])
        last_close = None if last is None else Fraction(last[3])
        endpoints[symbol] = first_open, last_close
        if first_open is not None and last_close is not None:
            morning[symbol] = last_close / first_open - 1
        bars = [_minute(session, at) for at in starts]
        if None not in bars:
            close = Fraction(bars[-1][3])
            afternoon[symbol] = close / Fraction(bars[0][0]) - 1, close, Fraction(min(bar[2] for bar in bars))

    plans = {}
    threshold = Fraction(PEER_THRESHOLD_BPS, 10_000)
    for symbol in sorted(sessions):
        peers = [value for name, value in morning.items() if name != symbol]
        center = median(peers) if len(peers) >= PEER_QUORUM else None
        gate = "unresolved" if center is None else "select" if center > threshold else "decline"
        late, limit, stop = afternoon.get(symbol, (None, None, None))
        context = late, morning.get(symbol), endpoints[symbol], len(peers), center, gate
        if late is None:
            plans[symbol] = _plan(*context, "unresolved", "retain", "late_window_unavailable")
        elif late <= 0:
            plans[symbol] = _plan(*context, "decline", "retain", "late_return_not_positive")
        else:
            ready = stop < limit
            plans[symbol] = _plan(*context, "select", "ready" if ready else "decline",
                                  "order_ready" if ready else "limit_at_stop", limit=limit, stop=stop)
    return {"decision_at": decision.isoformat(), "observed_opening_members": len(morning), "symbols": plans}


def reference_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Rebuild exact peer medians and bounds using independent quote checks."""
    decision = _decision_at(sessions)
    endpoints, returns = {}, {}
    for symbol, session in sessions.items():
        start_quote = _reference_quote(session, session.opening)
        end_quote = _reference_quote(session, session.opening + timedelta(minutes=OPENING_LAST_OFFSET))
        first_open = start_quote[0] if start_quote is not None else None
        last_close = end_quote[3] if end_quote is not None else None
        endpoints[symbol] = first_open, last_close
        if first_open is not None and last_close is not None:
            returns[symbol] = (last_close - first_open) / first_open

    plans = {}
    for symbol, session in sorted(sessions.items()):
        others = sorted(value for name, value in returns.items() if name != symbol)
        count = len(others)
        center, gate = None, "unresolved"
        if count >= PEER_QUORUM:
            center = (others[(count - 1) // 2] + others[count // 2]) / 2
            gate = "select" if center * 10_000 > PEER_THRESHOLD_BPS else "decline"

        latest = decision - timedelta(minutes=_BAR_LAG_MINUTES)
        reverse = [_reference_quote(session, latest - timedelta(minutes=offset))
                   for offset in range(LATE_LOOKBACK_MINUTES)]
        context = returns.get(symbol), endpoints[symbol], count, center, gate
        if None in reverse:
            plans[symbol] = _plan(None, *context, "unresolved", "retain", "late_window_unavailable")
            continue
        close, first_open = reverse[0][3], reverse[-1][0]
        late = (close - first_open) / first_open
        if close <= first_open:
            plans[symbol] = _plan(late, *context, "decline", "retain", "late_return_not_positive")
        else:
            stop = min(quote[2] for quote in reverse)
            ready = close - stop > 0
            plans[symbol] = _plan(late, *context, "select", "ready" if ready else "decline",
                                  "order_ready" if ready else "limit_at_stop", limit=close, stop=stop)
    return {"decision_at": decision.isoformat(), "observed_opening_members": len(returns), "symbols": plans}
