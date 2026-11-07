"""Predecision EH chart and matched-peer inputs, separate from the offline census."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_chart_inputs import _aggregate, _clock, _need, _session
from research.intraday_ed_inputs import Issue, MINUTE, SessionInputs
from research.intraday_scenario_labels import ExactPrice, PathQuestion, QuestionId, label_path
from research.intraday_scenario_state import _observed


WINDOW_MINUTES = 20
PriceVector = tuple[Fraction, Fraction, Fraction, Fraction]


@dataclass(frozen=True)
class ChartDecisionInputs:
    forecast_id: tuple[str, QuestionId, datetime]
    status: Literal["ready", "resolved", "unknown", "unsupported"]
    minutes: tuple[PriceVector, ...] = ()
    five_minute_bars: tuple[PriceVector, ...] = ()
    state: tuple[Fraction, ...] = ()
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class PeerDecisionInputs:
    symbol: str
    decision_at: datetime
    expected: tuple[str, ...]
    complete: tuple[str, ...]
    sequence: tuple[tuple[Fraction, Fraction, Fraction], ...] | None
    issues: tuple[Issue, ...]


def _window_start(session: SessionInputs, decision_at: datetime) -> int:
    _session(session)
    _clock(decision_at)
    elapsed = (decision_at - session.opening) // MINUTE
    _need(22 <= elapsed and (elapsed - 22) % 5 == 0 and decision_at < session.closing,
          "opening_scenario_decision_requires_22_plus_5m_grid")
    return elapsed - 2 - WINDOW_MINUTES


def chart_decision_inputs(
    session: SessionInputs, question: PathQuestion, start_close: ExactPrice | None, *, decision_at: datetime,
) -> ChartDecisionInputs:
    """Build a fixed 20-minute price sequence at an original or same-question update."""
    start = _window_start(session, decision_at)
    _window_start(session, question.start_at)
    _need(question.end_at - question.start_at == 30 * MINUTE, "opening_scenario_requires_fixed_30m_question")
    forecast_id = (session.symbol, *question.forecast_id(decision_at))
    visible = label_path(question, start_close, session, cutoff_at=decision_at)
    if visible.status != "pending":
        issues = (Issue(session.symbol, visible.observed_through, visible.reason),) if visible.reason else ()
        return ChartDecisionInputs(forecast_id, visible.status, issues=issues)
    rows, issues = _observed(session, start, WINDOW_MINUTES, decision_at)
    if issues:
        return ChartDecisionInputs(forecast_id, "unknown", issues=issues)

    level, band = question.level, question.band

    def relative(ohlc) -> PriceVector:
        op, high, low, close = ((Fraction(price) - level) / band for price in ohlc)
        return op, high, low, close

    minutes = tuple(relative(row.ohlc) for row in rows)
    coarse = tuple(relative(_aggregate(rows[index:index + 5])) for index in range(0, WINDOW_MINUTES, 5))
    close = Fraction(rows[-1].ohlc[3])
    remaining = (question.end_at - visible.observed_through) // MINUTE
    state = ((question.target - close) / band, (close - question.stop) / band,
             Fraction(remaining, 30), Fraction(visible.departed), Fraction(visible.retested),
             Fraction(sum(item[-1] > 1 for item in minutes), WINDOW_MINUTES))
    return ChartDecisionInputs(forecast_id, "ready", minutes, coarse, state)


def peer_decision_inputs(
    session: SessionInputs, *, decision_at: datetime, members_known_at: datetime | None,
    expected_peers: tuple[str, ...], peers: Mapping[str, SessionInputs | None],
) -> PeerDecisionInputs:
    """Project the same 20 minutes from an authenticated dated peer roster."""
    _session(session)
    _need(type(expected_peers) is tuple and session.symbol not in expected_peers,
          "unique_original_peers_excluding_target_required")
    return peer_clock_inputs(session, decision_at=decision_at, members_known_at=members_known_at,
                             roster=expected_peers, target_symbols=(session.symbol,), peers=peers)[0]


def peer_clock_inputs(
    session: SessionInputs, *, decision_at: datetime, members_known_at: datetime | None,
    roster: tuple[str, ...], target_symbols: tuple[str, ...], peers: Mapping[str, SessionInputs | None],
) -> tuple[PeerDecisionInputs, ...]:
    """Read each peer once for a clock and subtract each target's own contribution."""
    start = _window_start(session, decision_at)
    _need(all(type(ids) is tuple and all(type(item) is str and item for item in ids)
              and len(ids) == len(set(ids)) for ids in (roster, target_symbols))
          and isinstance(peers, Mapping), "unique_original_peer_and_target_rosters_required")
    if not target_symbols:
        return ()
    expected = tuple(sorted(roster))
    if members_known_at is not None:
        _clock(members_known_at)
    if members_known_at is None or members_known_at > decision_at:
        return tuple(PeerDecisionInputs(symbol, decision_at, tuple(peer for peer in expected if peer != symbol),
                                       (), None, (Issue(symbol, decision_at, "peer_roster_unavailable_at_decision"),))
                     for symbol in target_symbols)

    projected: dict[str, tuple[tuple[Fraction, Fraction, int], ...]] = {}
    issues: dict[str, tuple[Issue, ...]] = {}
    for symbol in expected:
        peer = peers.get(symbol)
        if peer is None:
            issues[symbol] = (Issue(symbol, decision_at, "expected_peer_session_missing"),)
            continue
        _session(peer)
        _need((peer.symbol, peer.opening, peer.closing) == (symbol, session.opening, session.closing),
              "peer_session_identity_or_calendar_differs")
        rows, problems = _observed(peer, start, WINDOW_MINUTES, decision_at)
        if problems:
            issues[symbol] = problems
            continue
        anchor = Fraction(rows[0].ohlc[0])
        prices = ((Fraction(row.ohlc[0]), Fraction(row.ohlc[3])) for row in rows)
        projected[symbol] = tuple((close / op - 1, close / anchor - 1, int(close > anchor)) for op, close in prices)

    totals = tuple((sum(item[0] for item in steps), sum(item[1] for item in steps), sum(item[2] for item in steps))
                   for steps in zip(*projected.values(), strict=True))
    zero = ((Fraction(), Fraction(), 0),) * WINDOW_MINUTES
    output = []
    for symbol in target_symbols:
        expected_peers = tuple(peer for peer in expected if peer != symbol)
        complete = tuple(peer for peer in projected if peer != symbol)
        problems = tuple(issue for peer in expected_peers for issue in issues.get(peer, ()))
        count = len(complete)
        sequence = None
        if count >= 5:
            own = projected.get(symbol, zero)
            sequence = tuple(((aggregate[0] - single[0]) / count, (aggregate[1] - single[1]) / count,
                              Fraction(aggregate[2] - single[2], count))
                             for aggregate, single in zip(totals, own, strict=True))
        else:
            problems += (Issue(symbol, decision_at, "fewer_than_five_complete_original_peers"),)
        output.append(PeerDecisionInputs(symbol, decision_at, expected_peers, complete, sequence, problems))
    return tuple(output)
