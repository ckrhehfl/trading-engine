"""Causal EG chart and observed-peer projections over supplied sessions."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import log1p
from statistics import median
from typing import Literal

from research.intraday_chart_inputs import ExactOHLC, _clock, _need, _session, _volume
from research.intraday_chart_tensors import MINUTE_CHANNELS, _finite
from research.intraday_ed_inputs import FIVE, MINUTE, Issue, Minute, SessionInputs, window


MINUTE_SLOTS = 60
REQUIRED_MINUTES = 15
CHART_CHANNELS = (*MINUTE_CHANNELS, "price_observed", "preopening")
PEER_CHANNELS = (
    "peer_minute_oc_pct", "peer_cumulative_pct", "peer_positive_fraction",
    "peer_coverage", "peer_supported", "preopening",
)
Availability = Literal["available", "unavailable"]


@dataclass(frozen=True)
class HighVolChart:
    symbol: str
    opening: datetime
    bar_end: datetime
    decision_at: datetime
    status: Availability
    sequence: tuple[tuple[float, ...], ...] | None
    price_states: tuple[str, ...]
    volume_states: tuple[str, ...]
    price_issues: tuple[Issue, ...]


@dataclass(frozen=True)
class HighVolPeers:
    symbol: str
    opening: datetime
    bar_end: datetime
    decision_at: datetime
    status: Availability
    expected_peer_ids: tuple[str, ...] | None
    common_peer_ids: tuple[str, ...]
    denominator: int | None
    known_counts: tuple[int, ...] | None
    sequence: tuple[tuple[float, ...], ...] | None
    issues: tuple[Issue, ...]


def _offset(session: SessionInputs, bar_end: datetime, decision_at: datetime) -> int:
    _session(session)
    _clock(bar_end)
    _clock(decision_at)
    elapsed = bar_end - session.opening
    _need(REQUIRED_MINUTES * MINUTE <= elapsed and bar_end <= session.closing
          and not elapsed % FIVE, "invalid_eg_input_bar_end")
    _need(decision_at == bar_end + 2 * MINUTE, "invalid_eg_input_decision_at")
    return elapsed // MINUTE


def _prefix(session: SessionInputs, offset: int, decision_at: datetime) -> tuple[
    tuple[Minute | None, ...], tuple[str, ...], tuple[Issue, ...],
]:
    """Look up each in-session slot once and validate only visible payloads."""
    start = max(0, offset - MINUTE_SLOTS)
    visible = {}
    late = {}
    for index in range(start, offset):
        at = session.opening + index * MINUTE
        row = session.minutes.get(at)
        if row is None:
            continue
        _need(isinstance(row, Minute) and row.symbol == session.symbol and row.start_at == at,
              "minute_identity_changed")
        _clock(row.start_at)
        _clock(row.available_at)
        _need(row.available_at >= at + 3 * MINUTE, "highvol_source_before_availability_floor")
        if row.available_at <= decision_at:
            visible[at] = row
        else:
            late[at] = Issue(session.symbol, at, "minute_available_after_cutoff")

    bounded = SessionInputs(session.symbol, session.opening, session.closing, visible)
    checked = window(bounded, start, offset - start, decision_at)
    issues = tuple(late.get(issue.minute, issue) for issue in checked.issues)
    reasons = {issue.minute: issue.reason for issue in issues}
    clocks = tuple(session.opening + index * MINUTE for index in range(offset - MINUTE_SLOTS, offset))
    states = tuple("preopening" if at < session.opening else reasons.get(at, "observed") for at in clocks)
    return tuple(visible.get(at) for at in clocks), states, issues


def _prices(row: Minute | None, state: str) -> ExactOHLC | None:
    if state != "observed":
        return None
    op, high, low, close = row.ohlc
    return Fraction(op), Fraction(high), Fraction(low), Fraction(close)


def _chart_price_channels(prices: ExactOHLC, anchor: Fraction) -> tuple[float, ...]:
    op, high, low, close = prices
    zero_range = high == low
    return (
        *(_finite(100 * (price / anchor - 1)) for price in prices),
        _finite(100 * (close - op) / op), _finite(100 * (high - low) / op),
        0.0 if zero_range else _finite((close - low) / (high - low)), float(zero_range),
    )


def build_chart_inputs(session: SessionInputs, bar_end: datetime, decision_at: datetime) -> HighVolChart:
    """Encode latest60 slots with the most recent15 observed prices required.

    Each decision uses its earliest visible OPEN in this window as anchor and
    its median strictly positive observed volume as scale (1 when none). A later
    decision can therefore renormalize shared slots as its visible window changes.
    """
    offset = _offset(session, bar_end, decision_at)
    rows, states, issues = _prefix(session, offset, decision_at)
    volumes = tuple(
        _volume(row, row.start_at) if row is not None else None for row in rows
    )
    volume_states = tuple(volume.state if volume is not None else state
                          for volume, state in zip(volumes, states, strict=True))
    sequence = None
    if all(state == "observed" for state in states[-REQUIRED_MINUTES:]):
        prices = tuple(_prices(row, state) for row, state in zip(rows, states, strict=True))
        anchor = next(value[0] for value in prices if value is not None)
        positives = [item.shares for item in volumes if item is not None
                     and item.shares is not None and item.shares > 0]
        scale = median(positives) if positives else Fraction(1)
        points = []
        for price, volume, state in zip(prices, volumes, states, strict=True):
            price_channels = (0.0,) * 8 if price is None else _chart_price_channels(price, anchor)
            shares = volume.shares if volume is not None else None
            points.append((*price_channels, 0.0 if shares is None else log1p(_finite(shares / scale)),
                           float(shares is not None), float(price is not None), float(state == "preopening")))
        sequence = tuple(points)
    return HighVolChart(session.symbol, session.opening, bar_end, decision_at,
                        "unavailable" if sequence is None else "available", sequence, states, volume_states, issues)


def build_peer_inputs(
    session: SessionInputs, bar_end: datetime, decision_at: datetime, *,
    expected_peer_ids: tuple[str, ...] | None, peer_sessions: Mapping[str, SessionInputs | None],
    minimum_known: int,
) -> HighVolPeers:
    """Use only original leave-one-out peers with complete latest15 prices.

    An older slot uses observed prices among that same cohort. Coverage always
    divides by the original roster, and moments use the known count only when
    it meets the supplied minimum. Unknown membership has no denominator.
    """
    offset = _offset(session, bar_end, decision_at)
    identity = (session.symbol, session.opening, bar_end, decision_at)
    _need(type(minimum_known) is int and minimum_known > 0, "invalid_eg_peer_minimum_known")
    _need(isinstance(peer_sessions, Mapping), "peer_session_mapping_required")
    if expected_peer_ids is None:
        return HighVolPeers(*identity, "unavailable", None, (), None, None, None,
                            (Issue(session.symbol, bar_end, "original_peer_roster_unavailable"),))
    _need(type(expected_peer_ids) is tuple
          and all(type(symbol) is str and bool(symbol) for symbol in expected_peer_ids)
          and len(set(expected_peer_ids)) == len(expected_peer_ids)
          and session.symbol not in expected_peer_ids, "unique_original_peers_excluding_target_required")
    expected = tuple(sorted(expected_peer_ids))
    common, prices, issues = [], [], []
    for symbol in expected:
        peer = peer_sessions.get(symbol)
        if peer is None:
            issues.append(Issue(symbol, bar_end, "expected_peer_session_missing"))
            continue
        _session(peer)
        _need((peer.symbol, peer.opening, peer.closing) == (symbol, session.opening, session.closing),
              "peer_session_identity_or_calendar_differs")
        rows, states, gaps = _prefix(peer, offset, decision_at)
        issues.extend(gaps)
        if all(state == "observed" for state in states[-REQUIRED_MINUTES:]):
            common.append(symbol)
            values = tuple(_prices(row, state) for row, state in zip(rows, states, strict=True))
            prices.append((next(value[0] for value in values if value is not None), values))

    counts = tuple(sum(values[index] is not None for _, values in prices) for index in range(MINUTE_SLOTS))
    if len(common) < minimum_known:
        issues.append(Issue(session.symbol, bar_end, "insufficient_complete_original_peers"))
        return HighVolPeers(*identity, "unavailable", expected, tuple(common), len(expected),
                            counts, None, tuple(issues))

    sequence = []
    for index, count in enumerate(counts):
        coverage = _finite(Fraction(count, len(expected)))
        if count < minimum_known:
            sequence.append((0.0, 0.0, 0.0, coverage, 0.0, float(offset - MINUTE_SLOTS + index < 0)))
            continue
        members = [(anchor, values[index]) for anchor, values in prices if values[index] is not None]
        oc = sum((value[3] / value[0] - 1 for _, value in members), Fraction())
        cumulative = sum((value[3] / anchor - 1 for anchor, value in members), Fraction())
        positive = sum(value[3] > anchor for anchor, value in members)
        sequence.append((_finite(100 * oc / count), _finite(100 * cumulative / count),
                         _finite(Fraction(positive, count)), coverage, 1.0, 0.0))
    return HighVolPeers(*identity, "available", expected, tuple(common), len(expected),
                        counts, tuple(sequence), tuple(issues))
