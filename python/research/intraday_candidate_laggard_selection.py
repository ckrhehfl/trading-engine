"""Pure causal entry decisions for the EJ opening cross-sectional laggards."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from fractions import Fraction
from statistics import median

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_exit_reference import _quote
from research.intraday_ed_inputs import MINUTE, SessionInputs


def _decision_at(sessions):
    if not isinstance(sessions, Mapping) or len(sessions) != 20:
        raise ValueError("EJ laggards require exactly 20 roster members")
    clocks = set()
    for symbol, session in sessions.items():
        if (type(symbol) is not str or not symbol or not isinstance(session, SessionInputs)
                or session.symbol != symbol or not isinstance(session.minutes, Mapping)):
            raise ValueError("EJ laggard session identity differs")
        opening, closing = session.opening, session.closing
        if (not all(isinstance(at, datetime) and at.utcoffset() is not None
                    and at.second == at.microsecond == 0 for at in (opening, closing))
                or closing < opening + 62 * MINUTE):
            raise ValueError("EJ laggard session geometry differs")
        clocks.add((opening.isoformat(), closing.isoformat()))
    if len(clocks) != 1:
        raise ValueError("EJ laggard sessions do not share one opening and closing")
    return opening + 62 * MINUTE


def _row(value, rank, reason, *, gated=None, order="retain", limit=None, stop=None):
    return {"return": None if value is None else str(value), "rank_state": rank,
            "gated_state": rank if gated is None else gated, "order_state": order,
            "reason": reason, "limit": None if limit is None else str(limit),
            "stop": None if stop is None else str(stop)}


def select_day(sessions):
    """Rank only prices available at the fixed decision and freeze entry bounds."""
    decision = _decision_at(sessions)
    returns, limits = {}, {}
    for symbol, session in sessions.items():
        first = _minute(session, session.opening)
        last = _minute(session, session.opening + 59 * MINUTE)
        if first is not None and last is not None:
            limits[symbol] = Fraction(last[3])
            returns[symbol] = limits[symbol] / Fraction(first[0]) - 1

    observed = len(returns)
    result = {"decision_at": decision.isoformat(), "observed": observed, "median_return": None, "symbols": {}}
    if observed < 10:
        result["symbols"] = {symbol: _row(returns.get(symbol), "unresolved", "basket_below_quorum")
                             for symbol in sorted(sessions)}
        return result

    center = median(returns.values())
    result["median_return"] = str(center)
    bottom = set(sorted(returns, key=lambda symbol: (returns[symbol], symbol))[:observed // 5])
    for symbol in sorted(sessions):
        value = returns.get(symbol)
        if value is None:
            row = _row(None, "unresolved", "endpoints_unavailable")
        elif symbol not in bottom:
            row = _row(value, "decline", "outside_laggard_quintile")
        elif value >= 0:
            row = _row(value, "decline", "return_not_negative")
        else:
            session = sessions[symbol]
            limit = limits[symbol]
            gated = "select" if center >= 0 else "decline"
            lows = [_minute(session, session.opening + offset * MINUTE) for offset in range(45, 60)]
            if any(bar is None for bar in lows):
                row = _row(value, "select", "stop_window_unavailable", gated=gated, order="unresolved", limit=limit)
            else:
                stop = Fraction(min(bar[2] for bar in lows))
                if stop == limit:
                    row = _row(value, "select", "limit_at_stop", gated=gated, order="decline", limit=limit, stop=stop)
                else:
                    row = _row(value, "select", "order_ready", gated=gated, order="ready", limit=limit, stop=stop)
        result["symbols"][symbol] = row
    return result


def _reference_quote(session, at):
    try:
        return _quote(session, at)
    except TypeError:
        # Invalid timestamp types do not provide a valid price observation.
        return None


def reference_day(sessions):
    """Reconstruct eligibility and bounds with the independent minute validator."""
    decision = _decision_at(sessions)
    observations = {}
    for symbol, session in sessions.items():
        opening = _reference_quote(session, decision - timedelta(minutes=62))
        latest = _reference_quote(session, decision - timedelta(minutes=3))
        if opening is not None and latest is not None:
            observations[symbol] = ((latest[3] - opening[0]) / opening[0], latest[3])

    ordered_returns = sorted(value for value, _ in observations.values())
    count = len(ordered_returns)
    center = None if count < 10 else (ordered_returns[(count - 1) // 2] + ordered_returns[count // 2]) / 2
    rows = {}
    for symbol, session in sorted(sessions.items()):
        value, limit = observations.get(symbol, (None, None))
        if center is None:
            rows[symbol] = _row(value, "unresolved", "basket_below_quorum")
            continue
        if value is None:
            rows[symbol] = _row(None, "unresolved", "endpoints_unavailable")
            continue
        rank = sum((other_value, other_symbol) < (value, symbol)
                   for other_symbol, (other_value, _) in observations.items())
        if 5 * (rank + 1) > count:
            rows[symbol] = _row(value, "decline", "outside_laggard_quintile")
            continue
        if value >= 0:
            rows[symbol] = _row(value, "decline", "return_not_negative")
            continue
        bounds = [_reference_quote(session, decision - timedelta(minutes=offset))
                  for offset in range(3, 18)]
        gated = "decline" if center < 0 else "select"
        if None in bounds:
            rows[symbol] = _row(value, "select", "stop_window_unavailable", gated=gated, order="unresolved", limit=limit)
        else:
            stop = min(bar[2] for bar in bounds)
            valid = limit > stop
            rows[symbol] = _row(value, "select", "order_ready" if valid else "limit_at_stop",
                                gated=gated, order="ready" if valid else "decline", limit=limit, stop=stop)
    return {"decision_at": decision.isoformat(), "observed": count,
            "median_return": None if center is None else str(center), "symbols": rows}
