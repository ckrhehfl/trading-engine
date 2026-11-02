"""Lazy, source-only EE session composition over caller-supplied minute rows.

No IO or source authentication. Opaque caller metadata is retained as evidence
identity only. Each policy consumes its own causal prefix; shared projections
do not admit prices or previous attempts on another policy's behalf.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from re import fullmatch

from research.intraday_ed_inputs import Minute, SessionInputs, STATES
from research import intraday_pattern_events as core
from research import intraday_trader_paths as paths
from research import intraday_trader_scenarios as scenarios_core


MINUTE = timedelta(minutes=1)
FIVE = timedelta(minutes=5)
LAG = timedelta(minutes=2)
SCENARIOS = scenarios_core.SCENARIOS


@dataclass(frozen=True)
class ProjectionIssue:
    minute: datetime
    reason: str


@dataclass(frozen=True)
class PolicyResult:
    scenario_id: str
    detection: scenarios_core.Detection
    path: paths.PathOutcome | None
    path_refusal: str | None
    issues: tuple[ProjectionIssue, ...]


@dataclass(frozen=True)
class SessionResult:
    symbol: str
    opening: datetime
    closing: datetime
    source_metadata: bytes
    results: tuple[PolicyResult, ...]


@dataclass(frozen=True)
class _Projection:
    bar: core.Bar | None
    issue: ProjectionIssue | None = None
    invalid: bool = False


def _minute_clock(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise core.PatternValidationError("aware_clock_required")


def _project_block(session: SessionInputs, start: datetime) -> _Projection:
    end = start + FIVE
    deadline = end + LAG
    prices: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    for index in range(5):
        at = start + index * MINUTE
        item = session.minutes.get(at)
        if item is None:
            return _Projection(None, ProjectionIssue(at, "missing_minute"))
        try:
            if not isinstance(item, Minute):
                raise core.PatternValidationError("typed_minute_required")
            _minute_clock(item.start_at)
            if item.symbol != session.symbol or item.start_at != at:
                raise core.PatternValidationError("minute_identity_changed")
            _minute_clock(item.available_at)
            if item.available_at < at + MINUTE:
                raise core.PatternValidationError("minute_availability_precedes_completion")
            # A later vintage cannot reveal even an invalid OHLC payload here.
            if item.available_at > deadline:
                return _Projection(None, ProjectionIssue(at, "price_unavailable_by_bar_deadline"))
            if type(item.price_state) is not str or item.price_state not in STATES:
                raise core.PatternValidationError("invalid_price_state")
            if item.price_state != "observed":
                if item.ohlc is not None:
                    raise core.PatternValidationError("unknown_price_has_payload")
                return _Projection(None, ProjectionIssue(at, f"price_{item.price_state}"))
            ohlc = item.ohlc
            if type(ohlc) is not tuple or len(ohlc) != 4:
                raise core.PatternValidationError("observed_price_requires_ohlc")
            for value in ohlc:
                if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                    raise core.PatternValidationError("positive_finite_decimal_price_required")
            op, high, low, close = ohlc
            if not low <= min(op, close) <= max(op, close) <= high:
                raise core.PatternValidationError("invalid_minute_ohlc_order")
            prices.append(ohlc)
        except core.PatternValidationError as error:
            return _Projection(None, ProjectionIssue(at, str(error)), invalid=True)
    return _Projection(core.Bar(start, end, deadline, prices[0][0],
                                max(value[1] for value in prices),
                                min(value[2] for value in prices), prices[-1][3]))


def _bars(
    session: SessionInputs, cache: dict[datetime, _Projection],
    issues: list[ProjectionIssue],
) -> Iterator[core.Bar]:
    start = session.opening
    while start + FIVE <= session.closing:
        if start not in cache:
            cache[start] = _project_block(session, start)
        projected = cache[start]
        if projected.issue is not None:
            issues.append(projected.issue)
            if projected.invalid:
                raise core.PatternValidationError(projected.issue.reason)
            return
        yield projected.bar
        start += FIVE


def _validate_session(session: SessionInputs) -> None:
    if not isinstance(session, SessionInputs):
        raise core.PatternValidationError("typed_session_required")
    core._aware(session.opening, "opening")
    core._aware(session.closing, "closing")
    if (type(session.symbol) is not str
            or fullmatch(r"KRX:[0-9A-Z]{6}", session.symbol) is None
            or not isinstance(session.minutes, Mapping)):
        raise core.PatternValidationError("invalid_session_identity")
    if (session.opening.second or session.opening.microsecond
            or session.closing.second or session.closing.microsecond
            or session.closing <= session.opening
            or (session.closing - session.opening) % FIVE
            or session.opening.utcoffset() != session.closing.utcoffset()
            or session.opening.date() != session.closing.date()):
        raise core.PatternValidationError("invalid_session_clock")


def evaluate_session(
    session: SessionInputs, *, scenarios: tuple[str, ...] = SCENARIOS,
    profit_facts: tuple[scenarios_core.ProfitFact | None, scenarios_core.ProfitFact | None] | None = None,
    event_session_admitted: bool | None = None,
    previous: Mapping[str, scenarios_core.PreviousTerminal | None] | None = None,
    source_metadata: bytes = b"{}",
) -> SessionResult:
    """Compose unchanged registered detectors and paths without scanning rows.

    Malformed invocation/session parameters raise PatternValidationError. A
    malformed demanded projection becomes that policy's invalid detection;
    missing/not-yet-available prices terminate only its observable prefix. Path
    integrity refusals remain separate from ordinary unknown path outcomes.
    """
    _validate_session(session)
    if (type(scenarios) is not tuple or not scenarios
            or any(type(value) is not str or value not in SCENARIOS for value in scenarios)
            or len(set(scenarios)) != len(scenarios)):
        raise core.PatternValidationError("invalid_scenario_selection")
    if previous is not None and not isinstance(previous, Mapping):
        raise core.PatternValidationError("previous_mapping_required")
    if previous is not None and any(type(key) is not str or key not in SCENARIOS for key in previous):
        raise core.PatternValidationError("invalid_previous_scenario")
    if type(source_metadata) is not bytes:
        raise core.PatternValidationError("opaque_source_metadata_bytes_required")
    cache: dict[datetime, _Projection] = {}
    results: list[PolicyResult] = []
    for scenario_id in scenarios:
        issues: list[ProjectionIssue] = []
        detection = scenarios_core.detect_bars(
            scenario_id, _bars(session, cache, issues), symbol=session.symbol,
            opening=session.opening, closing=session.closing,
            profit_facts=profit_facts, event_session_admitted=event_session_admitted,
            previous=previous.get(scenario_id) if previous is not None else None,
        )
        outcome = None
        refusal = None
        if detection.candidate is not None:
            candidate = detection.candidate
            admission = ("pass" if candidate.eligible is True else
                         "decline" if candidate.eligible is False else "unknown")
            plan = paths.PathPlan(scenario_id, session.symbol, candidate.available_at,
                                  candidate.stop, candidate.target, session.opening,
                                  session.closing, admission)
            try:
                outcome = paths.evaluate_path(plan, session.minutes)
            except paths.PathRefusal as error:
                refusal = str(error)
        results.append(PolicyResult(scenario_id, detection, outcome, refusal, tuple(issues)))
    return SessionResult(session.symbol, session.opening, session.closing,
                         source_metadata, tuple(results))
