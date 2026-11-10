"""Select bounded opening pullbacks with a separate rebound condition."""

from collections.abc import Mapping
from datetime import timedelta
from fractions import Fraction

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_laggard_selection import _decision_at, _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs


ENTRY_OFFSETS = (0, 29, *range(45, 60))
STRENGTH_BPS = 66


def _plan(prices, state, order, reason, *, limit=None, stop=None, rebound=None, rebound_state="not_applicable"):
    first, mid, last = (None if price is None else str(price) for price in prices)
    return {
        "rank_state": state,
        "gated_state": rebound_state if state == "select" else state,
        "order_state": order,
        "reason": reason,
        "limit": None if limit is None else str(limit),
        "stop": None if stop is None else str(stop),
        "first_open": first,
        "mid_close": mid,
        "last_close": last,
        "rebound_reference_close": None if rebound is None else str(rebound),
        "rebound_state": rebound_state,
    }


def select_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Freeze both opening policies before examining execution prices."""
    decision = _decision_at(sessions)
    plans = {}
    threshold = Fraction(STRENGTH_BPS, 10_000)
    for symbol, session in sorted(sessions.items()):
        opening, middle, ending = (
            _minute(session, session.opening + offset * MINUTE) for offset in (0, 29, 59)
        )
        prices = tuple(
            None if bar is None else Fraction(bar[field])
            for bar, field in ((opening, 0), (middle, 3), (ending, 3))
        )
        first, mid, last = prices
        if None in prices:
            plans[symbol] = _plan(prices, "unresolved", "retain", "own_prices_unavailable")
        elif mid / first - 1 < threshold:
            plans[symbol] = _plan(prices, "decline", "retain", "first_half_below_threshold")
        elif last >= mid:
            plans[symbol] = _plan(prices, "decline", "retain", "last_close_not_below_mid")
        elif last < (first + mid) / 2:
            plans[symbol] = _plan(prices, "decline", "retain", "pullback_below_half_gain")
        else:
            bars = [_minute(session, session.opening + offset * MINUTE) for offset in range(45, 59)]
            bars.append(ending)
            rebound_bar = bars[54 - 45]
            rebound = None if rebound_bar is None else Fraction(rebound_bar[3])
            rebound_state = "unresolved" if rebound is None else "select" if last > rebound else "decline"
            if None in bars:
                plans[symbol] = _plan(prices, "select", "unresolved", "structure_prices_unavailable",
                                      limit=last, rebound=rebound, rebound_state=rebound_state)
            else:
                stop = Fraction(min(bar[2] for bar in bars))
                ready = stop < last
                plans[symbol] = _plan(prices, "select", "ready" if ready else "decline",
                                      "order_ready" if ready else "limit_at_stop", limit=last,
                                      stop=stop, rebound=rebound, rebound_state=rebound_state)
    return {"decision_at": decision.isoformat(), "symbols": plans}


def reference_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Reconstruct eligibility and bounds using independent quotes and arithmetic."""
    decision = _decision_at(sessions)
    plans = {}
    for symbol, session in sorted(sessions.items()):
        opening = _reference_quote(session, decision - timedelta(minutes=62))
        middle = _reference_quote(session, decision - timedelta(minutes=33))
        ending = _reference_quote(session, decision - timedelta(minutes=3))
        first = None if opening is None else opening[0]
        mid = None if middle is None else middle[3]
        last = None if ending is None else ending[3]
        prices = first, mid, last
        if first is None or mid is None or last is None:
            plans[symbol] = _plan(prices, "unresolved", "retain", "own_prices_unavailable")
        elif 10_000 * (mid - first) < STRENGTH_BPS * first:
            plans[symbol] = _plan(prices, "decline", "retain", "first_half_below_threshold")
        elif mid - last <= 0:
            plans[symbol] = _plan(prices, "decline", "retain", "last_close_not_below_mid")
        elif 2 * (last - first) < mid - first:
            plans[symbol] = _plan(prices, "decline", "retain", "pullback_below_half_gain")
        else:
            quotes = {
                age: _reference_quote(session, decision - timedelta(minutes=age))
                for age in range(4, 18)
            }
            quotes[3] = ending
            rebound_quote = quotes[8]
            rebound, rebound_state = None, "unresolved"
            if rebound_quote is not None:
                rebound = rebound_quote[3]
                rebound_state = "select" if last - rebound > 0 else "decline"
            if any(quote is None for quote in quotes.values()):
                plans[symbol] = _plan(prices, "select", "unresolved", "structure_prices_unavailable",
                                      limit=last, rebound=rebound, rebound_state=rebound_state)
            else:
                stop = min(quote[2] for quote in quotes.values())
                ready = last - stop > 0
                plans[symbol] = _plan(prices, "select", "ready" if ready else "decline",
                                      "order_ready" if ready else "limit_at_stop", limit=last,
                                      stop=stop, rebound=rebound, rebound_state=rebound_state)
    return {"decision_at": decision.isoformat(), "symbols": plans}
