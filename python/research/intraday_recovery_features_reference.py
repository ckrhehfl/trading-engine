"""Independent EK measurement reconstruction over validated original minutes.

Only schema constants and lower session/minute validation are shared with the
producer. No producer candidate, chart transform, statistic or peer projection
is used as the expected value.
"""
from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from math import isfinite, log1p
from statistics import median

from research.intraday_ed_inputs import MINUTE, InputRefusal, SessionInputs, window
from research.intraday_recovery_features import PEER_SCHEMA, SCHEMA, _geometry, _observations


def _divide(top, bottom):
    return top / bottom if bottom else Fraction()


def _numbers(values):
    try:
        result = list(map(float, values))
    except (OverflowError, ValueError) as error:
        raise InputRefusal("EK numeric vector outside finite boundary") from error
    if not all(map(isfinite, result)):
        raise InputRefusal("EK numeric vector outside finite boundary")
    return result


def _admission(session, original):
    available = {int((row.start_at - session.opening) // MINUTE): tuple(map(Fraction, row.ohlc))
                 for row in original.minutes if row.price_state == "observed"}
    value = limit = stop = None
    state, order = "endpoints_unknown", "unresolved"
    if 0 in available and 59 in available:
        limit = available[59][3]
        value = (limit - available[0][0]) / available[0][0]
        if all(index in available for index in range(45, 60)):
            stop = min(available[index][2] for index in range(45, 60))
        if value >= 0:
            state, order = "own_nonnegative", "decline"
        else:
            if stop is not None:
                order = "ready" if stop < limit else "decline"
            if all(index in available for index in range(60)):
                state = "ready" if order == "ready" else "limit_at_stop"
            else:
                state = "own_negative_chart_unsupported"
    return {"candidate_state": state, "order_state": order,
            "own_return": None if value is None else str(value),
            "limit": None if limit is None else str(limit), "stop": None if stop is None else str(stop)}


def _measure(prices, volumes, end_offset, remaining_minutes):
    first, last = prices[0][0], prices[-1][3]
    path = [first] + [price[3] for price in prices]
    steps = [path[index + 1] - path[index] for index in range(60)]
    length = sum((abs(change) for change in steps), Fraction())
    declines = sorted([abs(change) for change in steps if change < 0])
    runs, run = [], 0
    drawdowns, durations = [], []
    for index in range(1, 61):
        run = run + 1 if steps[index - 1] < 0 else 0
        runs.append(run)
        peak = max(path[:index + 1])
        peak_time = max(position for position in range(index + 1) if path[position] == peak)
        durations.append(index - peak_time)
        drawdowns.append(((peak - path[index]) / peak, peak, path[index]))
    worst_index = max(range(60), key=lambda index: drawdowns[index][0])
    worst, peak_at_worst, bottom = drawdowns[worst_index]
    high = max(price[1] for price in prices)
    low = min(price[2] for price in prices)
    early_range = sum(((hi - lo) / op for op, hi, lo, _ in prices[:45]), Fraction()) / 45
    late_range = sum(((hi - lo) / op for op, hi, lo, _ in prices[45:]), Fraction()) / 15
    observed = sum(value is not None for value in volumes)
    late_observed = sum(value is not None for value in volumes[45:])
    volume_support = observed == 60 and sum(volumes[:45], Fraction()) != 0
    volume_ratio = ((sum(volumes[45:], Fraction()) / 15) / (sum(volumes[:45], Fraction()) / 45)
                    if volume_support else Fraction())
    recovery = (last - bottom) / (peak_at_worst - bottom) if worst else Fraction()
    return _numbers((
        100 * (last - first) / first, 100 * length / first, _divide(last - first, length),
        _divide(sum(declines[-3:], Fraction()), sum(declines, Fraction())),
        Fraction(len(declines), 60), Fraction(max(runs), 60), 100 * worst,
        Fraction(max(durations), 60), 100 * (max(path) - last) / max(path), recovery,
        100 * (last - prices[55][0]) / prices[55][0],
        100 * (last - prices[45][0]) / prices[45][0], 100 * (prices[44][3] - first) / first,
        _divide(last - low, high - low), int(high == low),
        100 * late_range, 100 * early_range, _divide(late_range, early_range), int(early_range == 0),
        Fraction(observed, 60), Fraction(late_observed, 15), volume_ratio, int(volume_support),
        Fraction(end_offset, 390), Fraction(remaining_minutes, 60),
    ))


def build_features(session: SessionInputs, *, end_offset: int = 60, remaining_minutes: int = 60) -> dict:
    """Reconstruct the JSON feature and candidate record without the producer."""
    _geometry(session, end_offset, remaining_minutes)
    decision = session.opening + (end_offset + 2) * MINUTE
    initial = _observations(session, 0, 60, session.opening + 62 * MINUTE)
    current = initial if end_offset == 60 else _observations(session, end_offset - 60, 60, decision)
    by_minute = {row.start_at: row for row in current.minutes}
    clocks = [session.opening + index * MINUTE for index in range(end_offset - 60, end_offset)]
    states = ["missing" if clock not in by_minute else by_minute[clock].volume_state for clock in clocks]
    issues = [{"minute": issue.minute.isoformat(), "reason": issue.reason}
              for issue in current.issues if not issue.reason.startswith("volume_")]
    supported = all(clock in by_minute and by_minute[clock].price_state == "observed" for clock in clocks)
    minute = static = None
    if supported:
        prices = [tuple(map(Fraction, by_minute[clock].ohlc)) for clock in clocks]
        volumes = [Fraction(by_minute[clock].shares) if state == "observed" else None
                   for clock, state in zip(clocks, states, strict=True)]
        positives = [volume for volume in volumes if volume is not None and volume > 0]
        scale = median(positives) if positives else Fraction(1)
        anchor = prices[0][0]
        minute = []
        for (opened, high, low, close), volume in zip(prices, volumes, strict=True):
            price_channels = _numbers((100 * (opened - anchor) / anchor, 100 * (high - anchor) / anchor,
                100 * (low - anchor) / anchor, 100 * (close - anchor) / anchor,
                100 * (close - opened) / opened, 100 * (high - low) / opened,
                _divide(close - low, high - low), int(high == low)))
            ratio = _numbers((volume / scale,))[0] if volume is not None else 0.0
            minute.append([*price_channels, log1p(ratio), float(volume is not None)])
        static = _measure(prices, volumes, end_offset, remaining_minutes)
    return {"schema": SCHEMA, "symbol": session.symbol, "decision_at": decision.isoformat(),
            "end_offset": end_offset, **_admission(session, initial),
            "chart_state": "available" if supported else "unavailable", "minute": minute,
            "static": static, "price_issues": issues, "volume_states": states}


def build_peers(symbol: str, sessions: Mapping[str, SessionInputs], *, end_offset: int = 60) -> dict:
    """Rebuild the leave-one-out arithmetic on original complete peer windows."""
    if (not isinstance(sessions, Mapping) or symbol not in sessions or len(sessions) > 20
            or any(type(name) is not str or not name or not isinstance(value, SessionInputs)
                   or value.symbol != name for name, value in sessions.items())):
        raise InputRefusal("EK original peer roster required")
    current = sessions[symbol]
    _geometry(current, end_offset)
    decision = current.opening + (end_offset + 2) * MINUTE
    expected = sorted(set(sessions) - {symbol})
    complete = {}
    for name in expected:
        other = sessions[name]
        if (other.opening, other.closing) != (current.opening, current.closing):
            raise InputRefusal("peer_session_identity_or_calendar_differs")
        observed = window(other, end_offset - 60, 60, decision)
        if not observed.issues:
            complete[name] = [tuple(map(Fraction, row.ohlc)) for row in observed.minutes]
    count = len(complete)
    minute = None
    if count >= 5:
        minute = []
        for index in range(60):
            minute.append(_numbers((
                sum((100 * (rows[index][3] - rows[index][0]) / rows[index][0]
                     for rows in complete.values()), Fraction()) / count,
                sum((100 * (rows[index][3] - rows[0][0]) / rows[0][0]
                     for rows in complete.values()), Fraction()) / count,
                Fraction(sum(rows[index][3] > rows[0][0] for rows in complete.values()), count),
            )))
    return {"schema": PEER_SCHEMA, "status": "available" if minute is not None else "unavailable",
            "minute": minute, "expected_ids": expected, "observed_ids": sorted(complete),
            "expected_count": len(expected), "observed_count": count,
            "coverage": float(Fraction(count, len(expected))) if expected else 0.0}


def build_all_peers(sessions: Mapping[str, SessionInputs], *, end_offset: int = 60) -> dict[str, dict]:
    """Reconstruct all leave-one-out views with one validation per price path."""
    if (not isinstance(sessions, Mapping) or not 1 <= len(sessions) <= 20
            or any(type(name) is not str or not name or not isinstance(value, SessionInputs)
                   or value.symbol != name for name, value in sessions.items())):
        raise InputRefusal("EK original peer roster required")
    ordered = sorted(sessions)
    origin = sessions[ordered[0]]
    _geometry(origin, end_offset)
    decision = origin.opening + (end_offset + 2) * MINUTE
    complete = {}
    for name in ordered:
        value = sessions[name]
        if name != ordered[0]:
            _geometry(value, end_offset)
        if value.opening != origin.opening or value.closing != origin.closing:
            raise InputRefusal("peer_session_identity_or_calendar_differs")
        checked = window(value, end_offset - 60, 60, decision)
        if not checked.issues:
            complete[name] = [tuple(map(Fraction, item.ohlc)) for item in checked.minutes]
    observations = {}
    for name, prices in complete.items():
        anchor = prices[0][0]
        observations[name] = [((close - opened) / opened, (close - anchor) / anchor, int(close > anchor))
                              for opened, _, _, close in prices]
    sums = [[sum((rows[index][channel] for rows in observations.values()), Fraction())
             for channel in range(3)] for index in range(60)]
    result = {}
    for target in ordered:
        others = sorted(set(ordered) - {target})
        supported = sorted(set(observations) - {target})
        size = len(supported)
        values = None
        if size >= 5:
            values = []
            for index in range(60):
                correction = observations[target][index] if target in observations else (0, 0, 0)
                values.append(_numbers(((sums[index][0] - correction[0]) * Fraction(100, size),
                                        (sums[index][1] - correction[1]) * Fraction(100, size),
                                        (sums[index][2] - correction[2]) / size)))
        result[target] = {"schema": PEER_SCHEMA, "status": "available" if values is not None else "unavailable",
                          "minute": values, "expected_ids": others, "observed_ids": supported,
                          "expected_count": len(others), "observed_count": size,
                          "coverage": float(Fraction(size, len(others))) if others else 0.0}
    return result
