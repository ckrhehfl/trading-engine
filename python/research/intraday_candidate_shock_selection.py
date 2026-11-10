"""Pure causal selection and independent checks for the EJ intraday shock setup."""

from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from research.intraday_candidate_exit import _minute
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _session


DECISION_OFFSETS = (62, 122, 182)
SHOCK_LIMIT = Fraction(66, 10_000)


def _check_session(session: SessionInputs) -> None:
    _session(session)
    if (session.opening.second != 0 or session.opening.microsecond != 0
            or session.closing.date() != session.opening.date()
            or session.closing < session.opening + DECISION_OFFSETS[-1] * MINUTE):
        raise ValueError("EJ shock session geometry differs")


def _selected(decision, unknown, limit, stop):
    ready = stop < limit
    return {"decision_at": decision.isoformat(), "rank_state": "select",
            "order_state": "ready" if ready else "decline", "limit": str(limit), "stop": str(stop),
            "reason": "order_ready" if ready else "limit_at_stop", "unknown_offsets": unknown}


def _unselected(unknown):
    return {"decision_at": None, "rank_state": "unresolved" if unknown else "decline",
            "order_state": "retain", "limit": None, "stop": None,
            "reason": "price_window_unavailable" if unknown else "shock_recovery_not_observed",
            "unknown_offsets": unknown}


def select_price(session: SessionInputs) -> dict:
    """Freeze the earliest qualifying price event and retain earlier gaps."""
    _check_session(session)
    unknown = []
    for offset in DECISION_OFFSETS:
        decision = session.opening + offset * MINUTE
        prices = [_minute(session, session.opening + start * MINUTE)
                  for start in range(offset - 12, offset - 2)]
        if None in prices:
            unknown.append(offset)
            continue
        initial = Fraction(prices[0][0])
        shock = Fraction(prices[4][3])
        latest = Fraction(prices[-1][3])
        if shock / initial - 1 <= -SHOCK_LIMIT and latest < initial and latest - shock >= (initial - shock) / 3:
            return _selected(decision, unknown, latest, Fraction(min(bar[2] for bar in prices)))
    return _unselected(unknown)


def reference_price(session: SessionInputs) -> dict:
    """Reconstruct prices from the minute verifier and compare cross products."""
    _check_session(session)
    unknown = []
    for offset in DECISION_OFFSETS:
        decision = session.opening + timedelta(minutes=offset)
        backward = [_reference_quote(session, decision - timedelta(minutes=distance))
                    for distance in range(3, 13)]
        if any(quote is None for quote in backward):
            unknown.append(offset)
            continue
        latest, shock, initial = backward[0][3], backward[5][3], backward[-1][0]
        drop = initial - shock
        if drop * 10_000 < 66 * initial or latest >= initial or 3 * (latest - shock) < drop:
            continue
        return _selected(decision, unknown, latest, min(quote[2] for quote in backward))
    return _unselected(unknown)


def _volume_decision(plan: dict, session: SessionInputs) -> datetime:
    _check_session(session)
    decision = datetime.fromisoformat(plan["decision_at"])
    if plan["decision_at"] not in {(session.opening + offset * MINUTE).isoformat() for offset in DECISION_OFFSETS}:
        raise ValueError("EJ shock volume decision differs from frozen price event")
    return decision


def _shares(session, at):
    item = session.minutes.get(at)
    if not isinstance(item, Minute) or item.symbol != session.symbol or item.start_at != at:
        return None
    if item.volume_state != "observed" or item.available_at != at + 3 * MINUTE:
        return None
    value = item.shares
    return Fraction(value) if isinstance(value, Decimal) and value.is_finite() and value >= 0 else None


def _reference_shares(session, at):
    item = session.minutes.get(at)
    if (not isinstance(item, Minute) or (item.symbol, item.start_at, item.volume_state) != (session.symbol, at, "observed")
            or not isinstance(item.available_at, datetime) or item.available_at.utcoffset() is None
            or item.available_at - at != timedelta(minutes=3)):
        return None
    shares = item.shares
    if not isinstance(shares, Decimal) or not shares.is_finite() or shares < 0:
        return None
    return Fraction(*shares.as_integer_ratio())


def _volume_result(plan, state, reason, ratio=None):
    return {**plan, "gated_state": state, "volume_reason": reason,
            "volume_ratio": None if ratio is None else str(ratio)}


def apply_volume(plan: dict, session: SessionInputs | None) -> dict:
    """Apply the volume gate to the frozen event without replacing that event."""
    if plan["rank_state"] != "select":
        return _volume_result(plan, plan["rank_state"], "volume_not_applicable")
    if session is None:
        return _volume_result(plan, "unresolved", "volume_window_unavailable")
    decision = _volume_decision(plan, session)
    shares = [_shares(session, decision + offset * MINUTE) for offset in range(-37, -7)]
    if None in shares:
        return _volume_result(plan, "unresolved", "volume_window_unavailable")
    baseline_average = sum(shares[:25]) / 25
    if baseline_average == 0:
        return _volume_result(plan, "unresolved", "volume_reference_zero")
    ratio = (sum(shares[25:]) / 5) / baseline_average
    return _volume_result(plan, "select" if ratio >= 2 else "decline",
                          "volume_ratio_at_least_two" if ratio >= 2 else "volume_ratio_below_two", ratio)


def reference_volume(plan: dict, session: SessionInputs | None) -> dict:
    """Independently reconstruct the same gate using exact sums and comparisons."""
    if plan["rank_state"] != "select":
        return _volume_result(plan, plan["rank_state"], "volume_not_applicable")
    if session is None:
        return _volume_result(plan, "unresolved", "volume_window_unavailable")
    decision = _volume_decision(plan, session)
    shock_total = Fraction(0)
    reference_total = Fraction(0)
    for distance in range(8, 38):
        value = _reference_shares(session, decision - timedelta(minutes=distance))
        if value is None:
            return _volume_result(plan, "unresolved", "volume_window_unavailable")
        if distance <= 12:
            shock_total += value
        else:
            reference_total += value
    if reference_total == 0:
        return _volume_result(plan, "unresolved", "volume_reference_zero")
    scaled_shock = 5 * shock_total
    if scaled_shock >= 2 * reference_total:
        return _volume_result(plan, "select", "volume_ratio_at_least_two", scaled_shock / reference_total)
    return _volume_result(plan, "decline", "volume_ratio_below_two", scaled_shock / reference_total)
