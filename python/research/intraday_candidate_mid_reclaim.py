"""Select a fixed late support reclaim and test whether prior closes held above it."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from fractions import Fraction

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs, _session


DECISION_OFFSET = 122
EXPIRY_OFFSET = 153
SUPPORT_OFFSETS = tuple(range(30, 60))
STRUCTURE_OFFSETS = tuple(range(114, 120))
ENTRY_OFFSETS = (*SUPPORT_OFFSETS, *STRUCTURE_OFFSETS)


def _decision_at(sessions: Mapping[str, SessionInputs]) -> datetime:
    if not isinstance(sessions, Mapping) or len(sessions) != 20:
        raise ValueError("EJ mid-reclaim requires exactly 20 roster members")
    clocks = set()
    for symbol, session in sessions.items():
        _session(session)
        if symbol != session.symbol:
            raise ValueError("EJ mid-reclaim session identity differs")
        opening, closing = session.opening, session.closing
        decision = opening + DECISION_OFFSET * MINUTE
        expiry = opening + EXPIRY_OFFSET * MINUTE
        if (not opening.date() == expiry.date() == closing.date() or expiry >= closing
                or any(at.second or at.microsecond for at in (opening, closing))):
            raise ValueError("EJ mid-reclaim session geometry differs")
        clocks.add((opening.isoformat(), closing.isoformat()))
    if len(clocks) != 1:
        raise ValueError("EJ mid-reclaim sessions do not share one opening and closing")
    return decision


def _plan(support, broken, reclaimed, rank, reason, *, order="retain", limit=None, stop=None,
          held_117=None, held_118=None, hold="not_applicable"):
    prices = {
        "limit": limit, "stop": stop, "support_low": support, "break_close": broken,
        "reclaim_close": reclaimed, "hold_close_117": held_117, "hold_close_118": held_118,
    }
    return {
        "rank_state": rank,
        "gated_state": hold if rank == "select" else rank,
        "order_state": order,
        "reason": reason,
        "hold_state": hold,
        **{name: None if price is None else str(Fraction(price)) for name, price in prices.items()},
    }


def select_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Freeze both reclaim policies from completed predecision minutes only."""
    decision = _decision_at(sessions)
    plans = {}
    for symbol, session in sorted(sessions.items()):
        anchor_bars = [_minute(session, session.opening + offset * MINUTE) for offset in SUPPORT_OFFSETS]
        support = None if None in anchor_bars else min(bar[2] for bar in anchor_bars)
        breaking = _minute(session, session.opening + 114 * MINUTE)
        reclaiming = _minute(session, session.opening + 119 * MINUTE)
        broken = None if breaking is None else breaking[3]
        reclaimed = None if reclaiming is None else reclaiming[3]
        prices = support, broken, reclaimed
        if None in prices:
            plans[symbol] = _plan(*prices, "unresolved", "own_prices_unavailable")
        elif broken >= support:
            plans[symbol] = _plan(*prices, "decline", "break_close_not_below_support")
        elif reclaimed <= support:
            plans[symbol] = _plan(*prices, "decline", "reclaim_close_not_above_support")
        else:
            middle = {offset: _minute(session, session.opening + offset * MINUTE) for offset in range(115, 119)}
            held_117, held_118 = (None if middle[offset] is None else middle[offset][3] for offset in (117, 118))
            held = held_117, held_118
            if any(close is not None and close <= support for close in held):
                hold = "decline"
            else:
                hold = "unresolved" if None in held else "select"

            if None in middle.values():
                order, reason, stop = "unresolved", "structure_prices_unavailable", None
            else:
                # The break bar low is below support, which is below the reclaim close.
                stop = min(bar[2] for bar in (breaking, *middle.values(), reclaiming))
                order, reason = "ready", "order_ready"
            plans[symbol] = _plan(*prices, "select", reason, order=order, limit=reclaimed, stop=stop,
                                  held_117=held_117, held_118=held_118, hold=hold)
    return {"decision_at": decision.isoformat(), "symbols": plans}


def reference_day(sessions: Mapping[str, SessionInputs]) -> dict:
    """Reconstruct the decisions with the independent quote validator and exact differences."""
    decision = _decision_at(sessions)
    plans = {}
    for symbol, session in sorted(sessions.items()):
        anchor = [_reference_quote(session, decision - timedelta(minutes=age)) for age in range(63, 93)]
        lows = [quote[2] for quote in anchor if quote is not None]
        support = min(lows) if len(lows) == 30 else None
        closing_quote = _reference_quote(session, decision - timedelta(minutes=3))
        break_quote = _reference_quote(session, decision - timedelta(minutes=8))
        reclaimed = None if closing_quote is None else closing_quote[3]
        broken = None if break_quote is None else break_quote[3]
        prices = support, broken, reclaimed
        if support is None or broken is None or reclaimed is None:
            plans[symbol] = _plan(*prices, "unresolved", "own_prices_unavailable")
            continue
        if support - broken <= 0:
            plans[symbol] = _plan(*prices, "decline", "break_close_not_below_support")
            continue
        if reclaimed - support <= 0:
            plans[symbol] = _plan(*prices, "decline", "reclaim_close_not_above_support")
            continue

        quotes = [_reference_quote(session, decision - timedelta(minutes=age)) for age in range(4, 8)]
        held_118 = None if quotes[0] is None else quotes[0][3]
        held_117 = None if quotes[1] is None else quotes[1][3]
        differences = [close - support for close in (held_117, held_118) if close is not None]
        hold = "unresolved"
        if differences and min(differences) <= 0:
            hold = "decline"
        elif len(differences) == 2:
            hold = "select"

        structure_lows = [quote[2] for quote in (closing_quote, *quotes, break_quote) if quote is not None]
        stop = min(structure_lows) if len(structure_lows) == 6 else None
        if stop is None:
            order, reason = "unresolved", "structure_prices_unavailable"
        else:
            order, reason = "ready", "order_ready"
        plans[symbol] = _plan(*prices, "select", reason, order=order, limit=reclaimed, stop=stop,
                              held_117=held_117, held_118=held_118, hold=hold)
    return {"decision_at": decision.isoformat(), "symbols": plans}
