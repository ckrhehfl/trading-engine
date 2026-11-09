"""Causal late-session leader and follower decisions with independent replay."""

from collections.abc import Mapping
from datetime import datetime
from fractions import Fraction
from statistics import median

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs


LEADER_RANK_MAX = 5
LEADER_QUORUM = 4
RETURN_LIMIT = Fraction(33, 10_000)


def _decision_at(sessions, ranks):
    if not isinstance(sessions, Mapping) or len(sessions) != 20:
        raise ValueError("EJ late leader requires exactly 20 roster members")
    if (not isinstance(ranks, Mapping) or ranks.keys() != sessions.keys()
            or any(type(rank) is not int for rank in ranks.values())
            or set(ranks.values()) != set(range(1, 21))):
        raise ValueError("EJ late leader requires original ranks 1 through 20")

    clocks = set()
    for symbol, session in sessions.items():
        if (type(symbol) is not str or not symbol or not isinstance(session, SessionInputs)
                or session.symbol != symbol or not isinstance(session.minutes, Mapping)):
            raise ValueError("EJ late leader session identity differs")
        opening, closing = session.opening, session.closing
        if (not all(isinstance(at, datetime) and at.utcoffset() is not None
                    and at.second == at.microsecond == 0 for at in (opening, closing))
                or opening.date() != closing.date() or closing - 93 * MINUTE < opening):
            raise ValueError("EJ late leader session geometry differs")
        clocks.add((opening.isoformat(), closing.isoformat()))
    if len(clocks) != 1:
        raise ValueError("EJ late leader sessions do not share one opening and closing")
    return closing - 76 * MINUTE


def _plan(rank, value, state, reason, *, gated=None, order="retain", limit=None, stop=None):
    return {"rank": rank, "return": None if value is None else str(value), "rank_state": state,
            "gated_state": state if gated is None else gated, "order_state": order,
            "reason": reason, "limit": None if limit is None else str(limit),
            "stop": None if stop is None else str(stop)}


def _day(decision, count, center, gate, plans):
    return {"decision_at": decision.isoformat(), "observed_leaders": count,
            "leader_median_return": None if center is None else str(center),
            "leader_state": gate, "symbols": plans}


def select_day(sessions, ranks):
    """Freeze both entry plans using the same 15 bars available at decision time."""
    decision = _decision_at(sessions, ranks)
    starts = tuple(decision - offset * MINUTE for offset in range(17, 2, -1))
    prices = {}
    for symbol, session in sessions.items():
        bars = [_minute(session, start) for start in starts]
        if all(bar is not None for bar in bars):
            limit = Fraction(bars[-1][3])
            value = limit / Fraction(bars[0][0]) - 1
            prices[symbol] = value, limit, Fraction(min(bar[2] for bar in bars))

    leaders = [value for symbol, (value, _, _) in prices.items() if ranks[symbol] <= LEADER_RANK_MAX]
    center = median(leaders) if len(leaders) >= LEADER_QUORUM else None
    gate = "unresolved" if center is None else "select" if center > RETURN_LIMIT else "decline"

    plans = {}
    for symbol, rank in sorted(ranks.items()):
        value, limit, stop = prices.get(symbol, (None, None, None))
        if rank <= LEADER_RANK_MAX:
            plan = _plan(rank, value, "decline", "leader_not_target")
        elif value is None:
            plan = _plan(rank, None, "unresolved", "window_unavailable")
        elif not 0 <= value <= RETURN_LIMIT:
            plan = _plan(rank, value, "decline", "return_outside_band")
        elif limit == stop:
            plan = _plan(rank, value, "select", "limit_at_stop", gated=gate,
                         order="decline", limit=limit, stop=stop)
        else:
            plan = _plan(rank, value, "select", "order_ready", gated=gate,
                         order="ready", limit=limit, stop=stop)
        plans[symbol] = plan
    return _day(decision, len(leaders), center, gate, plans)


def reference_day(sessions, ranks):
    """Reconstruct the same decisions with independent minute and return checks."""
    decision = _decision_at(sessions, ranks)
    observed = {}
    for symbol, session in sessions.items():
        latest = decision - 3 * MINUTE
        quotes = [_reference_quote(session, latest - offset * MINUTE) for offset in range(15)]
        if None not in quotes:
            first_open, last_close = quotes[14][0], quotes[0][3]
            observed[symbol] = (last_close - first_open) / first_open, last_close, min(quote[2] for quote in quotes)

    leader_values = sorted(observed[symbol][0] for symbol in observed if ranks[symbol] in range(1, 6))
    count = len(leader_values)
    center = None
    gate = "unresolved"
    if count in (4, 5):
        center = (leader_values[(count - 1) // 2] + leader_values[count // 2]) / 2
        gate = "select" if center * 10_000 > 33 else "decline"

    plans = {}
    for symbol in sorted(sessions):
        rank = ranks[symbol]
        value, limit, stop = observed.get(symbol, (None, None, None))
        if rank < 6:
            plans[symbol] = _plan(rank, value, "decline", "leader_not_target")
        elif value is None:
            plans[symbol] = _plan(rank, None, "unresolved", "window_unavailable")
        elif value * 10_000 < 0 or value * 10_000 > 33:
            plans[symbol] = _plan(rank, value, "decline", "return_outside_band")
        else:
            ready = limit - stop > 0
            plans[symbol] = _plan(rank, value, "select", "order_ready" if ready else "limit_at_stop",
                                  gated=gate, order="ready" if ready else "decline", limit=limit, stop=stop)
    return _day(decision, count, center, gate, plans)
