"""Pure EK own-chart measurements and separate original-roster peer inputs.

Only requested completed minutes are consumed. Numeric vectors contain no
identity, original rank, peer field or future label. Volume placeholders always
carry an observation mask; stored volume does not certify physical share units.
"""
from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
import math

from research.intraday_chart_inputs import build_chart_inputs, build_peer_context
from research.intraday_chart_tensors import MINUTE_CHANNELS, PEER_CHANNELS, chart_tensor, peer_tensor
from research.intraday_ed_inputs import MINUTE, InputRefusal, SessionInputs, Window, window


SCHEMA = "EK-own-chart-v1"
PEER_SCHEMA = "EK-observed-peers-v1"
STATIC_NAMES = (
    "window_return_pct", "absolute_path_pct", "net_path_efficiency",
    "negative_move_top3_fraction", "down_fraction", "longest_down_run_fraction",
    "maximum_drawdown_pct", "maximum_drawdown_duration_fraction", "current_drawdown_pct",
    "maximum_drawdown_recovery_fraction", "recent5_return_pct", "recent15_return_pct",
    "prior45_return_pct", "window_close_location", "zero_window_range",
    "recent15_mean_range_pct", "prior45_mean_range_pct", "recent_prior_mean_range_ratio",
    "zero_prior_mean_range", "volume_observed_fraction", "recent15_volume_observed_fraction",
    "recent_prior_mean_volume_ratio", "volume_ratio_supported", "elapsed_minutes_over_390",
    "remaining_minutes_over_60",
)


def _geometry(session: SessionInputs, end_offset: int, remaining_minutes: int = 60) -> None:
    if (not isinstance(session, SessionInputs) or type(end_offset) is not int
            or end_offset < 60 or end_offset % 5 or type(remaining_minutes) is not int
            or not 0 <= remaining_minutes <= 390):
        raise InputRefusal("EK registered completed-minute geometry required")
    # The shared validator checks session identity, aware clocks and cash close.
    window(session, end_offset - 1, 1, session.opening + (end_offset + 2) * MINUTE)


def _observations(session: SessionInputs, offset: int, count: int, decision_at) -> Window:
    """Shared lower validation only; producer/reference algebra remains separate."""
    observed = window(session, offset, count, decision_at, price=True, volume=True)
    if any(row.available_at != row.start_at + 3 * MINUTE for row in observed.minutes):
        raise InputRefusal("EK minute-end-plus-two observation clock required")
    return observed


def _ratio(numerator: Fraction, denominator: Fraction) -> Fraction:
    return numerator / denominator if denominator else Fraction()


def _finite(values) -> list[float]:
    try:
        result = [float(value) for value in values]
    except (OverflowError, ValueError) as error:
        raise InputRefusal("EK numeric vector outside finite boundary") from error
    if not all(math.isfinite(value) for value in result):
        raise InputRefusal("EK numeric vector outside finite boundary")
    return result


def _candidate(session: SessionInputs, observed: Window) -> dict:
    prices = {row.start_at: row.ohlc for row in observed.minutes if row.price_state == "observed"}
    first = prices.get(session.opening)
    last = prices.get(session.opening + 59 * MINUTE)
    result = {"candidate_state": "endpoints_unknown", "order_state": "unresolved",
              "own_return": None, "limit": None, "stop": None}
    if first is None or last is None:
        return result
    limit = Fraction(last[3])
    value = limit / Fraction(first[0]) - 1
    lows = [prices.get(session.opening + offset * MINUTE) for offset in range(45, 60)]
    stop = None if any(row is None for row in lows) else min(Fraction(row[2]) for row in lows)
    result.update(own_return=str(value), limit=str(limit), stop=None if stop is None else str(stop))
    if value >= 0:
        result.update(candidate_state="own_nonnegative", order_state="decline")
        return result
    order = "unresolved" if stop is None else "decline" if stop == limit else "ready"
    state = ("own_negative_chart_unsupported" if len(prices) != 60 else
             "limit_at_stop" if stop == limit else "ready")
    result.update(candidate_state=state, order_state=order, stop=None if stop is None else str(stop))
    return result


def _static(prices, volumes, end_offset: int, remaining_minutes: int) -> list[float]:
    anchor = prices[0][0]
    closes = [anchor, *(row[3] for row in prices)]
    changes = [right - left for left, right in zip(closes, closes[1:])]
    distance = sum(map(abs, changes), Fraction())
    negative = sorted((-value for value in changes if value < 0), reverse=True)
    down_run = longest_run = 0
    peak, peak_index = anchor, 0
    maximum_dd = Fraction()
    drawdown_peak = trough = anchor
    longest_duration = 0
    for index, (close, change) in enumerate(zip(closes[1:], changes), 1):
        down_run = down_run + 1 if change < 0 else 0
        longest_run = max(longest_run, down_run)
        if close >= peak:
            peak, peak_index = close, index
        decline = (peak - close) / peak
        if decline > maximum_dd:
            maximum_dd, drawdown_peak, trough = decline, peak, close
        longest_duration = max(longest_duration, index - peak_index)
    last = closes[-1]
    high, low = max(row[1] for row in prices), min(row[2] for row in prices)
    ranges = [(row[1] - row[2]) / row[0] for row in prices]
    recent_range = sum(ranges[-15:], Fraction()) / 15
    prior_range = sum(ranges[:45], Fraction()) / 45
    known = [value for value in volumes if value is not None]
    recent_known = [value for value in volumes[-15:] if value is not None]
    volume_supported = len(known) == 60 and sum(volumes[:45], Fraction()) > 0
    volume_ratio = (sum(volumes[-15:], Fraction()) * 3 / sum(volumes[:45], Fraction())
                    if volume_supported else Fraction())
    return _finite((
        100 * (last / anchor - 1), 100 * distance / anchor,
        _ratio(last - anchor, distance), _ratio(sum(negative[:3], Fraction()), sum(negative, Fraction())),
        Fraction(len(negative), 60), Fraction(longest_run, 60), 100 * maximum_dd,
        Fraction(longest_duration, 60), 100 * (peak - last) / peak,
        _ratio(last - trough, drawdown_peak - trough),
        100 * (last / prices[-5][0] - 1), 100 * (last / prices[-15][0] - 1),
        100 * (prices[44][3] / anchor - 1), _ratio(last - low, high - low), int(high == low),
        100 * recent_range, 100 * prior_range, _ratio(recent_range, prior_range), int(prior_range == 0),
        Fraction(len(known), 60), Fraction(len(recent_known), 15), volume_ratio, int(volume_supported),
        Fraction(end_offset, 390), Fraction(remaining_minutes, 60),
    ))


def build_features(session: SessionInputs, *, end_offset: int = 60, remaining_minutes: int = 60) -> dict:
    """Return first-hour admission and a current latest60 own-chart vector.

    Admission always uses opening minutes0..59, including on later management
    queries. Missing interior prices can erase the learned vector while keeping
    valid endpoint/last15 order geometry. No peer gate enters this function.
    """
    _geometry(session, end_offset, remaining_minutes)
    decision = session.opening + (end_offset + 2) * MINUTE
    original = _observations(session, 0, 60, session.opening + 62 * MINUTE)
    current = original if end_offset == 60 else _observations(session, end_offset - 60, 60, decision)
    rows = {row.start_at: row for row in current.minutes}
    projected = SessionInputs(session.symbol, session.opening, session.closing, rows)
    chart = build_chart_inputs(projected, session.opening + end_offset * MINUTE, decision)
    tensor = chart_tensor(chart)
    at = [session.opening + offset * MINUTE for offset in range(end_offset - 60, end_offset)]
    states = [rows[clock].volume_state if clock in rows else "missing" for clock in at]
    issues = [{"minute": issue.minute.isoformat(), "reason": issue.reason}
              for issue in current.issues if not issue.reason.startswith("volume_")]
    values = None
    if tensor is not None:
        prices = [tuple(map(Fraction, rows[clock].ohlc)) for clock in at]
        volumes = [Fraction(rows[clock].shares) if states[index] == "observed" else None
                   for index, clock in enumerate(at)]
        values = _static(prices, volumes, end_offset, remaining_minutes)
    return {"schema": SCHEMA, "symbol": session.symbol, "decision_at": decision.isoformat(),
            "end_offset": end_offset, **_candidate(session, original), "chart_state": chart.status,
            "minute": None if tensor is None else [list(row) for row in tensor.minute],
            "static": values, "price_issues": issues, "volume_states": states}


def build_peers(symbol: str, sessions: Mapping[str, SessionInputs], *, end_offset: int = 60) -> dict:
    """Return only other original members; never replace a missing peer."""
    if (not isinstance(sessions, Mapping) or symbol not in sessions or len(sessions) > 20
            or any(type(name) is not str or not name or not isinstance(value, SessionInputs)
                   or value.symbol != name for name, value in sessions.items())):
        raise InputRefusal("EK original peer roster required")
    session = sessions[symbol]
    _geometry(session, end_offset)
    end = session.opening + end_offset * MINUTE
    # Volume is deliberately not a peer support condition.
    peer = build_peer_context(session, end, end + 2 * MINUTE,
                              expected_peer_ids=tuple(name for name in sessions if name != symbol),
                              peer_sessions=sessions)
    tensor = peer_tensor(peer)
    count, expected = len(peer.common_peer_ids), len(peer.expected_peer_ids)
    return {"schema": PEER_SCHEMA, "status": peer.status,
            "minute": None if tensor is None else [list(row) for row in tensor.minute],
            "expected_ids": list(peer.expected_peer_ids), "observed_ids": list(peer.common_peer_ids),
            "expected_count": expected, "observed_count": count,
            "coverage": float(Fraction(count, expected)) if expected else 0.0}


def build_all_peers(sessions: Mapping[str, SessionInputs], *, end_offset: int = 60) -> dict[str, dict]:
    """Validate each original path once, then subtract each target from totals.

    The output for a target is exactly ``build_peers(target, sessions)``. This
    changes repeated work only; incomplete paths, quorum and original roster
    denominators keep their per-target meanings.
    """
    if (not isinstance(sessions, Mapping) or not 1 <= len(sessions) <= 20
            or any(type(name) is not str or not name or not isinstance(value, SessionInputs)
                   or value.symbol != name for name, value in sessions.items())):
        raise InputRefusal("EK original peer roster required")
    names = sorted(sessions)
    first = sessions[names[0]]
    _geometry(first, end_offset)
    decision = first.opening + (end_offset + 2) * MINUTE
    paths = {}
    for name in names:
        current = sessions[name]
        if name != names[0]:
            _geometry(current, end_offset)
        if (current.opening, current.closing) != (first.opening, first.closing):
            raise InputRefusal("peer_session_identity_or_calendar_differs")
        observed = window(current, end_offset - 60, 60, decision)
        if observed.issues:
            continue
        anchor = Fraction(observed.minutes[0].ohlc[0])
        paths[name] = tuple((Fraction(row.ohlc[3]) / Fraction(row.ohlc[0]) - 1,
                             Fraction(row.ohlc[3]) / anchor - 1, int(Fraction(row.ohlc[3]) > anchor))
                            for row in observed.minutes)
    totals = tuple(tuple(sum((path[index][channel] for path in paths.values()), Fraction())
                         for channel in range(3)) for index in range(60))
    result = {}
    for name in names:
        expected = [other for other in names if other != name]
        common = [other for other in paths if other != name]
        count = len(common)
        minute = None
        if count >= 5:
            own = paths.get(name)
            minute = [_finite((100 * (total[0] - (own[index][0] if own is not None else 0)) / count,
                               100 * (total[1] - (own[index][1] if own is not None else 0)) / count,
                               (total[2] - (own[index][2] if own is not None else 0)) / count))
                      for index, total in enumerate(totals)]
        result[name] = {"schema": PEER_SCHEMA, "status": "available" if minute is not None else "unavailable",
                        "minute": minute, "expected_ids": expected, "observed_ids": common,
                        "expected_count": len(expected), "observed_count": count,
                        "coverage": float(Fraction(count, len(expected))) if expected else 0.0}
    return result
