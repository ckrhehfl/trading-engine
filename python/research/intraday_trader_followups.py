"""Registered EE mechanism proposals over supplied rows; no IO or actual runs.

The fixed S2 basket is an explanatory large-stock proxy, never an official
index. Raw identity parsing is shared with the source boundary; feature math
and saved-result reference math are separate implementations.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Literal

from research import intraday_ed_snapshot as codec
from research import intraday_ed_sources as sources
from research import intraday_trader_paths as paths
from research import intraday_trader_scenarios as scenarios
from research import intraday_trader_session as sessions
from research.intraday_ed_inputs import SessionInputs


POLICIES = ("EE-A-2R-TARGET", "EE-B-POSITIVE-PEERS", "EE-B-COMMON-PEERS")
MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class PeerInput:
    membership: sources.TargetCoordinate
    raw: dict | None


@dataclass(frozen=True)
class PeerMinute:
    start_at: datetime
    end_at: datetime
    available_at: datetime
    state: Literal["observed", "missing", "conflict", "invalid", "unavailable"]
    decoded_rows: bytes


@dataclass(frozen=True)
class PeerWindow:
    symbol: str
    membership_metadata: bytes
    membership_available_at: datetime
    membership_selected_at: datetime
    source_metadata: bytes
    decoded_chunk_sha256: str | None
    state: Literal["complete", "missing", "conflict", "invalid", "unavailable"]
    observations: tuple[PeerMinute, ...]
    return_value: Fraction | None


@dataclass(frozen=True)
class PeerContext:
    basket_clock: Literal["S2"]
    window_start: datetime
    window_end: datetime
    decision_at: datetime
    windows: tuple[PeerWindow, ...]
    complete_count: int
    state: Literal["known", "unknown"]
    mean: Fraction | None


def peer_context(symbol: str, end: datetime, decision: datetime,
                 peers: Iterable[PeerInput]) -> PeerContext:
    """Retain every original other peer and all fifteen predecision atoms.

    decoded_rows and decoded_chunk_sha256 identify canonical projections of
    decoded values, never original archive line bytes. Authentication of those
    original bytes belongs to the caller's existing audited source reader.
    """
    sources._need(end + 2 * MINUTE == decision, "peer_decision_clock_differs")
    result, values, seen = [], [], set()
    for supplied in peers:
        sources._need(type(supplied) is PeerInput and type(supplied.membership) is sources.TargetCoordinate,
                      "typed_peer_input_required")
        member, raw = supplied.membership, supplied.raw
        sources._need(member.scenario == "S2" and member.symbol != symbol
                      and member.symbol not in seen and len(seen) < 20, "peer_basket_identity_differs")
        seen.add(member.symbol)
        metadata = codec._json(member.metadata)
        available, selected = (sources._clock(metadata[name]) for name in ("available_at", "selected_at"))
        sources._need(available <= selected < sources.calendar_source.session_open(member.session)
                      and selected <= decision, "peer_membership_unavailable")
        buckets = {}
        provenance, fingerprint = b"null", None
        if raw is not None:
            got_symbol, got_day, _, _ = sources._raw_identity(raw, "CC")
            sources._need((got_symbol, got_day) == (member.symbol, member.session),
                          "peer_source_identity_differs")
            provenance = codec.canonical({key: value for key, value in raw.items() if key != "rows"})
            fingerprint = sources._sha(codec.canonical(raw) + b"\n")
            for row in raw["rows"]:
                at = datetime.fromtimestamp(row["open_time_ms"] // 60000 * 60, end.tzinfo)
                buckets.setdefault(at, []).append(row)
        observations, prices, states = [], [], []
        for offset in range(15):
            at = end - (15 - offset) * MINUTE
            rows = buckets.get(at, [])
            state = "missing" if not rows else "conflict" if len(rows) != 1 else "observed"
            parsed = None
            if state == "observed":
                try:
                    parsed = tuple(Decimal(rows[0][key]) if type(rows[0][key]) is str else None
                                   for key in ("open", "high", "low", "close"))
                    if (any(value is None or not value.is_finite() or value <= 0 for value in parsed)
                            or not parsed[2] <= min(parsed[0], parsed[3]) <= max(parsed[0], parsed[3]) <= parsed[1]):
                        state = "invalid"
                except InvalidOperation:
                    state = "invalid"
            if at + 3 * MINUTE > decision:
                state = "unavailable"
            observations.append(PeerMinute(at, at + MINUTE, at + 3 * MINUTE,
                                           state, codec.canonical(rows)))
            prices.append(parsed)
            states.append(state)
        state = "complete" if all(value == "observed" for value in states) else next(
            value for value in ("unavailable", "invalid", "conflict", "missing") if value in states)
        change = Fraction(prices[-1][3]) / Fraction(prices[0][0]) - 1 if state == "complete" else None
        if change is not None:
            values.append(change)
        result.append(PeerWindow(member.symbol, member.metadata, available, selected,
                                 provenance, fingerprint, state, tuple(observations), change))
    sources._need(len(result) <= 20, "peer_basket_bound")
    known = len(values) >= 5
    return PeerContext("S2", end - 15 * MINUTE, end, decision, tuple(result), len(values),
                       "known" if known else "unknown", sum(values, Fraction()) / len(values) if known else None)


def evaluate_session(session: SessionInputs, *, peers: Iterable[PeerInput],
                     source_metadata: bytes = b"{}") -> tuple[sessions.SessionResult, PeerContext | None]:
    """Original A/B structures, one frozen 2R target, and two common peer arms."""
    sessions._validate_session(session)
    if type(source_metadata) is not bytes:
        raise sources.SourceRefusal("opaque_source_metadata_bytes_required")
    projections = {}
    detections, issues = [], []
    for family in ("EE-A", "EE-B"):
        demanded = []
        detections.append(scenarios.detect_bars(family, sessions._bars(session, projections, demanded),
            symbol=session.symbol, opening=session.opening, closing=session.closing))
        issues.append(tuple(demanded))
    b = detections[1].candidate
    context = None if b is None else peer_context(session.symbol, b.signal.end_at, b.available_at, peers)
    results = []
    for index, policy in enumerate(POLICIES):
        base = 0 if index == 0 else 1
        detection, candidate = detections[base], detections[base].candidate
        if candidate is not None:
            if base == 0:
                target = scenarios._decimal(3 * Fraction(candidate.signal.close) - 2 * Fraction(candidate.stop))
                candidate = replace(candidate, target=target)
                detection = replace(detection, trace=(*detection.trace, scenarios.Transition(
                    "risk_target_frozen", candidate.signal.end_at, candidate.available_at, target)))
            else:
                known = context.state == "known"
                passes = known and (index == 2 or context.mean > 0)
                info = scenarios.InformationAdmission("unknown" if not known else "pass" if passes else "decline",
                    "peer_quorum_unknown" if not known else "peer_common_support" if index == 2 else
                    "positive_peer_mean" if passes else "peer_mean_not_positive")
                candidate = replace(candidate, information=info)
            candidate = replace(candidate, scenario_id=policy)
        detection = replace(detection, scenario_id=policy, candidate=candidate)
        outcome = refusal = None
        if candidate is not None:
            admission = "pass" if candidate.eligible is True else "decline" if candidate.eligible is False else "unknown"
            plan = paths.PathPlan(policy, session.symbol, candidate.available_at, candidate.stop,
                                  candidate.target, session.opening, session.closing, admission)
            try:
                outcome = paths.evaluate_path(plan, session.minutes)
            except paths.PathRefusal as error:
                refusal = str(error)
        results.append(sessions.PolicyResult(policy, detection, outcome, refusal, issues[base]))
    return sessions.SessionResult(session.symbol, session.opening, session.closing,
                                  source_metadata, tuple(results)), context
