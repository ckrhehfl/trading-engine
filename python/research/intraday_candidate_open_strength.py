"""Select sustained opening strength with a separate weak-peer gate."""

from collections.abc import Mapping
from datetime import timedelta
from fractions import Fraction
from statistics import median

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_laggard_selection import _decision_at, _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs


ENTRY_OFFSETS = (0, 29, *range(45, 60))
STRENGTH_BPS = 66
PEER_QUORUM = 8


def _plan(prices, own_return, peer_count, peer_median, peer_state, state, order, reason, *, limit=None, stop=None):
    first, mid, last = prices
    return {"rank_state": state, "gated_state": peer_state if state == "select" else state,
            "order_state": order, "reason": reason,
            "limit": None if limit is None else str(limit), "stop": None if stop is None else str(stop),
            "first_open": None if first is None else str(first), "mid_close": None if mid is None else str(mid),
            "last_close": None if last is None else str(last), "own_return": None if own_return is None else str(own_return),
            "peer_count": peer_count, "peer_median": None if peer_median is None else str(peer_median),
            "peer_state": peer_state}


def select_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Freeze both opening policies before examining any execution prices."""
    decision = _decision_at(sessions)
    prices, end_bars, peer_returns = {}, {}, {}
    for symbol, session in sessions.items():
        bars = [_minute(session, session.opening + offset * MINUTE) for offset in (0, 29, 59)]
        first, mid, last = (None if bar is None else Fraction(bar[field])
                            for bar, field in zip(bars, (0, 3, 3), strict=True))
        prices[symbol] = first, mid, last
        end_bars[symbol] = bars[-1]
        if mid is not None and last is not None:
            peer_returns[symbol] = last / mid - 1

    plans = {}
    threshold = Fraction(STRENGTH_BPS, 10_000)
    for symbol, session in sorted(sessions.items()):
        first, mid, last = prices[symbol]
        own_return = None if first is None or last is None else last / first - 1
        peers = [value for name, value in peer_returns.items() if name != symbol]
        center = median(peers) if len(peers) >= PEER_QUORUM else None
        gate = "unresolved" if center is None else "select" if center < 0 else "decline"
        context = prices[symbol], own_return, len(peers), center, gate
        if None in prices[symbol]:
            plans[symbol] = _plan(*context, "unresolved", "retain", "own_prices_unavailable")
        elif own_return < threshold:
            plans[symbol] = _plan(*context, "decline", "retain", "first_hour_below_threshold")
        elif last < mid:
            plans[symbol] = _plan(*context, "decline", "retain", "late_half_did_not_hold")
        else:
            bars = [_minute(session, session.opening + offset * MINUTE) for offset in range(45, 59)]
            bars.append(end_bars[symbol])
            if None in bars:
                plans[symbol] = _plan(*context, "select", "unresolved", "structure_prices_unavailable", limit=last)
            else:
                stop = Fraction(min(bar[2] for bar in bars))
                ready = stop < last
                plans[symbol] = _plan(*context, "select", "ready" if ready else "decline",
                                      "order_ready" if ready else "limit_at_stop", limit=last, stop=stop)
    return {"decision_at": decision.isoformat(), "symbols": plans, "observed_peer_members": len(peer_returns)}


def reference_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Reconstruct decisions with independent quote validation and exact sums."""
    decision = _decision_at(sessions)
    quotes, returns = {}, {}
    for symbol, session in sessions.items():
        first, mid, last = (_reference_quote(session, decision - timedelta(minutes=offset)) for offset in (62, 33, 3))
        quotes[symbol] = first, mid, last
        if mid is not None and last is not None:
            returns[symbol] = (last[3] - mid[3]) / mid[3]

    plans = {}
    for symbol, session in sorted(sessions.items()):
        first_quote, mid_quote, last_quote = quotes[symbol]
        first = None if first_quote is None else first_quote[0]
        mid = None if mid_quote is None else mid_quote[3]
        last = None if last_quote is None else last_quote[3]
        own_return = None if first is None or last is None else (last - first) / first
        others = sorted(value for name, value in returns.items() if name != symbol)
        count = len(others)
        center, gate = None, "unresolved"
        if count >= PEER_QUORUM:
            center = (others[(count - 1) // 2] + others[count // 2]) / 2
            gate = "select" if center < 0 else "decline"
        context = (first, mid, last), own_return, count, center, gate
        if first is None or mid is None or last is None:
            plans[symbol] = _plan(*context, "unresolved", "retain", "own_prices_unavailable")
        elif 10_000 * (last - first) < STRENGTH_BPS * first:
            plans[symbol] = _plan(*context, "decline", "retain", "first_hour_below_threshold")
        elif mid > last:
            plans[symbol] = _plan(*context, "decline", "retain", "late_half_did_not_hold")
        else:
            lows = [_reference_quote(session, decision - timedelta(minutes=offset)) for offset in range(4, 18)]
            lows.append(last_quote)
            if any(quote is None for quote in lows):
                plans[symbol] = _plan(*context, "select", "unresolved", "structure_prices_unavailable", limit=last)
            else:
                stop = min(quote[2] for quote in lows)
                ready = last - stop > 0
                plans[symbol] = _plan(*context, "select", "ready" if ready else "decline",
                                      "order_ready" if ready else "limit_at_stop", limit=last, stop=stop)
    return {"decision_at": decision.isoformat(), "symbols": plans, "observed_peer_members": len(returns)}
