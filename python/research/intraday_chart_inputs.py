"""Pure EF chart and observed-peer inputs over caller-authenticated sessions.

These exact stored-price observations do not certify raw/adjusted price basis,
physical share units, historical availability, venue channel or executable fills.
The caller owns the original universe and expected previous-session calendar.
Only requested completed prefixes are read; this module performs no IO, model
fitting, candidate selection or outcome evaluation. ED's contracts stay intact.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from research.intraday_ed_inputs import (
    MINUTE, STATES, InputRefusal, Issue, Minute, SessionInputs, window,
)


CHANNEL_NAMES = (
    "open_over_anchor_minus_one", "high_over_anchor_minus_one",
    "low_over_anchor_minus_one", "close_over_anchor_minus_one",
    "body_over_open", "range_over_open", "close_location", "zero_range_flag",
)
FIVE = 5 * MINUTE
Availability = Literal["available", "unavailable"]
ExactOHLC = tuple[Fraction, Fraction, Fraction, Fraction]


@dataclass(frozen=True)
class VolumeObservation:
    start_at: datetime
    shares: Fraction | None
    state: str


@dataclass(frozen=True)
class ChartMinute:
    start_at: datetime
    channels: tuple[Fraction, ...]


@dataclass(frozen=True)
class CoarseBar:
    start_at: datetime
    end_at: datetime
    status: Literal["observed", "not_yet_in_session", "unavailable"]
    ohlc: ExactOHLC | None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class PreviousSessionLevels:
    expected_opening: datetime | None
    status: Availability
    high: Fraction | None = None
    low: Fraction | None = None
    close: Fraction | None = None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class ChartInputs:
    """Latest60 price support governs status; optional fields have own masks.

``issues`` covers required latest60 OHLC only. Coarse bars and the previous
session retain their own issues; optional volume keeps its exact source state.
No peer fields or market-derived copies belong in this chart-only record.
"""

    symbol: str
    opening: datetime
    bar_end: datetime
    decision_at: datetime
    status: Availability
    sequence: tuple[ChartMinute, ...] | None
    volume: tuple[VolumeObservation, ...]
    coarse: tuple[CoarseBar, ...]
    previous: PreviousSessionLevels
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class PeerContext:
    """Same complete60-minute peer set for every equal-weighted channel.

The denominator is the complete original expected peer population, including
missing peers. Cumulative returns use each peer's first OPEN in the window;
positive fractions count strictly positive cumulative returns at each minute.
This observed-peer proxy is neither an official index nor sector certification.
"""

    symbol: str
    opening: datetime
    bar_end: datetime
    decision_at: datetime
    expected_peer_ids: tuple[str, ...]
    common_peer_ids: tuple[str, ...]
    denominator: int
    status: Availability
    minute_oc_return: tuple[Fraction, ...] | None
    cumulative_anchored_return: tuple[Fraction, ...] | None
    positive_fraction: tuple[Fraction, ...] | None
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class ContextualInputs:
    chart: ChartInputs
    peer: PeerContext

    @property
    def status(self) -> Availability:
        return "available" if self.chart.status == self.peer.status == "available" else "unavailable"


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal(reason)


def _clock(value: object) -> None:
    _need(isinstance(value, datetime) and value.utcoffset() is not None
          and value.second == value.microsecond == 0, "aware_minute_clock_required")


def _session(value: SessionInputs) -> None:
    _need(isinstance(value, SessionInputs), "typed_session_required")
    _clock(value.opening)
    _clock(value.closing)
    _need(type(value.symbol) is str and bool(value.symbol)
          and isinstance(value.minutes, Mapping), "invalid_session_identity")
    _need(value.opening < value.closing and value.opening.date() == value.closing.date()
          and (value.closing - value.opening) % MINUTE == timedelta(0), "invalid_cash_session_geometry")


def _geometry(session: SessionInputs, bar_end: datetime, decision_at: datetime,
              entry_grid: bool) -> int:
    _session(session)
    _clock(bar_end)
    _clock(decision_at)
    _need(type(entry_grid) is bool, "boolean_entry_grid_required")
    offset = (bar_end - session.opening) // MINUTE
    _need(60 <= offset and bar_end <= session.closing
          and (bar_end - session.opening) % FIVE == timedelta(0), "completed_five_minute_grid_required")
    _need(not entry_grid or (offset <= 300 and (offset - 60) % 15 == 0),
          "entry_grid_requires_60_through_300_step15")
    _need(decision_at == bar_end + 2 * MINUTE, "bar_end_plus_two_decision_required")
    return offset


def _volume(item: Minute | None, at: datetime) -> VolumeObservation:
    if item is None:
        return VolumeObservation(at, None, "missing")
    _need(type(item.volume_state) is str and item.volume_state in STATES,
          "invalid_volume_observation_state")
    if item.volume_state != "observed":
        _need(item.shares is None, "unknown_volume_has_payload")
        return VolumeObservation(at, None, item.volume_state)
    _need(isinstance(item.shares, Decimal) and item.shares.is_finite() and item.shares >= 0,
          "invalid_exact_shares")
    return VolumeObservation(at, Fraction(item.shares), "observed")


def _aggregate(rows: tuple[Minute, ...]) -> ExactOHLC:
    values = tuple(tuple(map(Fraction, row.ohlc)) for row in rows)
    return values[0][0], max(row[1] for row in values), min(row[2] for row in values), values[-1][3]


def _previous(current: SessionInputs, decision_at: datetime, expected: datetime | None,
              previous: SessionInputs | None) -> PreviousSessionLevels:
    if expected is None:
        _need(previous is None, "previous_session_requires_expected_opening")
        return PreviousSessionLevels(None, "unavailable", issues=(
            Issue(current.symbol, current.opening, "previous_session_not_supplied"),))
    _clock(expected)
    _need(expected.date() < current.opening.date(), "expected_previous_day_must_be_earlier")
    if previous is None:
        return PreviousSessionLevels(expected, "unavailable", issues=(
            Issue(current.symbol, expected, "expected_previous_session_missing"),))
    _session(previous)
    _need(previous.symbol == current.symbol and previous.opening == expected,
          "previous_session_identity_or_expected_date_differs")
    _need(previous.closing < current.opening and previous.closing <= decision_at,
          "previous_session_not_completed_before_current")
    count = (previous.closing - previous.opening) // MINUTE
    observed = window(previous, 0, count, decision_at)
    if observed.issues:
        return PreviousSessionLevels(expected, "unavailable", issues=observed.issues)
    _, high, low, close = _aggregate(observed.minutes)
    return PreviousSessionLevels(expected, "available", high, low, close)


def build_chart_inputs(
    session: SessionInputs, bar_end: datetime, decision_at: datetime, *, entry_grid: bool = False,
    expected_previous_opening: datetime | None = None, previous_session: SessionInputs | None = None,
) -> ChartInputs:
    """Read only latest120 in-session prices and optional explicit prior session.

Entry ends are offsets60..300, every15 minutes. Holding updates permit every
completed5-minute end from60 through the supplied close; downstream execution
must separately require a permitted later fill clock. No previous-session slot
fills the coarse tensor's explicitly scheduled preopening slots.
"""
    offset = _geometry(session, bar_end, decision_at, entry_grid)
    start = max(0, offset - 120)
    observed = window(session, start, offset - start, decision_at)
    rows = {row.start_at: row for row in observed.minutes}
    latest_start = bar_end - 60 * MINUTE
    issues = tuple(issue for issue in observed.issues if issue.minute >= latest_start)
    volume = tuple(_volume(rows.get(latest_start + index * MINUTE), latest_start + index * MINUTE)
                   for index in range(60))
    sequence = None
    if not issues:
        latest = tuple(rows[latest_start + index * MINUTE] for index in range(60))
        anchor = Fraction(latest[0].ohlc[0])
        points = []
        for row in latest:
            op, hi, lo, cl = map(Fraction, row.ohlc)
            zero_range = hi == lo
            channels = (op / anchor - 1, hi / anchor - 1, lo / anchor - 1, cl / anchor - 1,
                        (cl - op) / op, (hi - lo) / op,
                        Fraction(0) if zero_range else (cl - lo) / (hi - lo), Fraction(int(zero_range)))
            points.append(ChartMinute(row.start_at, channels))
        sequence = tuple(points)
    coarse = []
    for index in range(24):
        at = bar_end - 120 * MINUTE + index * FIVE
        end = at + FIVE
        if end <= session.opening:
            coarse.append(CoarseBar(at, end, "not_yet_in_session", None))
            continue
        problems = tuple(issue for issue in observed.issues if at <= issue.minute < end)
        aggregate = None if problems else _aggregate(tuple(rows[at + minute * MINUTE] for minute in range(5)))
        coarse.append(CoarseBar(at, end, "unavailable" if problems else "observed", aggregate, problems))
    previous = _previous(session, decision_at, expected_previous_opening, previous_session)
    return ChartInputs(session.symbol, session.opening, bar_end, decision_at,
                       "unavailable" if issues else "available", sequence, volume, tuple(coarse), previous, issues)


def build_peer_context(
    session: SessionInputs, bar_end: datetime, decision_at: datetime, *,
    expected_peer_ids: tuple[str, ...], peer_sessions: Mapping[str, SessionInputs | None],
) -> PeerContext:
    """Use only the caller's original peers, each complete over the same60 minutes."""
    offset = _geometry(session, bar_end, decision_at, False)
    _need(type(expected_peer_ids) is tuple
          and all(type(symbol) is str and bool(symbol) for symbol in expected_peer_ids)
          and len(set(expected_peer_ids)) == len(expected_peer_ids)
          and session.symbol not in expected_peer_ids, "unique_original_peers_excluding_target_required")
    _need(isinstance(peer_sessions, Mapping), "peer_session_mapping_required")
    expected = tuple(sorted(expected_peer_ids))
    common, values, issues = [], [], []
    for symbol in expected:
        peer = peer_sessions.get(symbol)
        if peer is None:
            issues.append(Issue(symbol, bar_end - 60 * MINUTE, "expected_peer_session_missing"))
            continue
        _session(peer)
        _need((peer.symbol, peer.opening, peer.closing) == (symbol, session.opening, session.closing),
              "peer_session_identity_or_calendar_differs")
        observed = window(peer, offset - 60, 60, decision_at)
        if observed.issues:
            issues.extend(observed.issues)
            continue
        common.append(symbol)
        values.append(tuple(tuple(map(Fraction, row.ohlc)) for row in observed.minutes))
    oc = cumulative = positive = None
    if len(common) >= 5:
        count = len(common)
        oc = tuple(sum((peer[index][3] / peer[index][0] - 1 for peer in values), Fraction()) / count
                   for index in range(60))
        cumulative = tuple(sum((peer[index][3] / peer[0][0] - 1 for peer in values), Fraction()) / count
                           for index in range(60))
        positive = tuple(Fraction(sum(peer[index][3] > peer[0][0] for peer in values), count)
                         for index in range(60))
    else:
        issues.append(Issue(session.symbol, bar_end, "fewer_than_five_complete_original_peers"))
    return PeerContext(session.symbol, session.opening, bar_end, decision_at, expected, tuple(common),
                       len(expected), "available" if oc is not None else "unavailable",
                       oc, cumulative, positive, tuple(issues))


def with_peer_context(chart: ChartInputs, peer: PeerContext) -> ContextualInputs:
    """Keep exactly the supplied chart object; do not rebuild contextual features."""
    _need(type(chart) is ChartInputs and type(peer) is PeerContext, "typed_chart_and_peer_required")
    _need((chart.symbol, chart.opening, chart.bar_end, chart.decision_at)
          == (peer.symbol, peer.opening, peer.bar_end, peer.decision_at), "chart_peer_coordinates_differ")
    return ContextualInputs(chart, peer)
