"""Compare a sealed weak opening path with the original same-day peers."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from fractions import Fraction
from statistics import median

from research.intraday_candidate_exit import _minute, _number
from research.intraday_candidate_laggard_selection import _reference_quote
from research.intraday_ed_inputs import MINUTE, SessionInputs, _clock, _require, _session


PEER_OFFSETS = (29, 59)
PEER_QUORUM = 8


def _targets(paths: Mapping[str, dict], decision: datetime) -> set[str]:
    _clock(decision)
    _require(decision.second == decision.microsecond == 0, "ej_laggard_peers_decision_differs")
    _require(isinstance(paths, Mapping) and len(paths) == 20
             and all(type(symbol) is str and bool(symbol) for symbol in paths),
             "ej_laggard_peers_original_roster_differs")
    _require(all(path["state"] in ("retain", "unresolved", "rebound", "weak") for path in paths.values()),
             "ej_laggard_peers_original_path_differs")
    return {symbol for symbol, path in paths.items() if path["state"] == "weak"}


def _check_sessions(paths: Mapping[str, dict], decision: datetime, sessions: Mapping[str, SessionInputs]) -> None:
    _require(isinstance(sessions, Mapping) and sessions.keys() == paths.keys(),
             "ej_laggard_peers_session_roster_differs")
    for symbol, session in sessions.items():
        _session(session)
        _require(session.symbol == symbol and session.opening + 62 * MINUTE == decision < session.closing,
                 "ej_laggard_peers_decision_or_session_differs")


def _sealed_prices(path: dict) -> tuple[Fraction, Fraction]:
    middle, last = _number(path["mid_close"]), _number(path["last_close"])
    _require(0 < last <= middle, "ej_laggard_peers_invalid_frozen_path")
    return middle, last


def _result(state, reason, value=None, count=None, center=None) -> dict:
    return {"state": state, "reason": reason, "target_return": None if value is None else str(value),
            "peer_count": count, "peer_median": None if center is None else str(center)}


def classify_day(paths: Mapping[str, dict], decision: datetime, sessions: Mapping[str, SessionInputs]) -> dict:
    """Read two fixed closes and compare each weak target with other members."""
    targets = _targets(paths, decision)
    result = {symbol: _result("retain", "original_path_not_weak") for symbol in paths if symbol not in targets}
    if not targets:
        return result
    _check_sessions(paths, decision, sessions)
    returns = {}
    for symbol, session in sessions.items():
        bars = [_minute(session, session.opening + offset * MINUTE) for offset in PEER_OFFSETS]
        middle, last = (None if bar is None else Fraction(bar[3]) for bar in bars)
        if symbol in targets:
            sealed = _sealed_prices(paths[symbol])
            _require(all(price is None or price == saved for price, saved in zip((middle, last), sealed, strict=True)),
                     "ej_laggard_peers_target_close_differs")
        if middle is not None and last is not None:
            returns[symbol] = last / middle - 1

    for symbol in paths:
        if symbol not in targets:
            continue
        peers = [value for member, value in returns.items() if member != symbol]
        count = len(peers)
        center = median(peers) if count >= PEER_QUORUM else None
        value = returns.get(symbol)
        if value is None:
            state, reason = "unresolved", "target_prices_unavailable"
        elif center is None:
            state, reason = "unresolved", "peer_quorum_unmet"
        elif value < center:
            state, reason = "weaker", "target_below_peer_median"
        else:
            state, reason = "other", "target_at_or_above_peer_median"
        result[symbol] = _result(state, reason, value, count, center)
    return result


def reference_classify_day(paths: Mapping[str, dict], decision: datetime, sessions: Mapping[str, SessionInputs]) -> dict:
    """Reconstruct each comparison from independently validated quotes."""
    targets = _targets(paths, decision)
    result = {symbol: _result("retain", "original_path_not_weak") for symbol in paths if symbol not in targets}
    if not targets:
        return result
    _check_sessions(paths, decision, sessions)
    middle_at, last_at = decision - timedelta(minutes=33), decision - timedelta(minutes=3)
    values = {}
    for symbol, session in sessions.items():
        mid_quote = _reference_quote(session, middle_at)
        end_quote = _reference_quote(session, last_at)
        if symbol in targets:
            sealed_mid, sealed_last = _sealed_prices(paths[symbol])
            _require((mid_quote is None or mid_quote[3] == sealed_mid)
                     and (end_quote is None or end_quote[3] == sealed_last), "ej_laggard_peers_target_close_differs")
        if mid_quote is not None and end_quote is not None:
            values[symbol] = (end_quote[3] - mid_quote[3]) / mid_quote[3]

    for symbol in paths:
        if symbol not in targets:
            continue
        others = sorted(values[member] for member in values if member != symbol)
        count = len(others)
        center = (others[(count - 1) // 2] + others[count // 2]) / 2 if count >= PEER_QUORUM else None
        if symbol not in values:
            result[symbol] = _result("unresolved", "target_prices_unavailable", count=count, center=center)
        elif center is None:
            result[symbol] = _result("unresolved", "peer_quorum_unmet", values[symbol], count)
        elif center <= values[symbol]:
            result[symbol] = _result("other", "target_at_or_above_peer_median", values[symbol], count, center)
        else:
            result[symbol] = _result("weaker", "target_below_peer_median", values[symbol], count, center)
    return result
