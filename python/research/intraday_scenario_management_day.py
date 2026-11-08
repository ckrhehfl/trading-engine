"""Replay EH management for every original coordinate in one audited feature day."""

from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_campaign_readiness import KST
from research.intraday_ed_inputs import MINUTE, SessionInputs, _require, _session
from research.intraday_scenario_analogue_roster import ChartAnalogueRoster, HeldAnalogueRoster
from research.intraday_scenario_decision_inputs import chart_decision_inputs
from research.intraday_scenario_decision_schedule import DecisionFrame
from research.intraday_scenario_held_inputs import HeldDecisionInputs, held_decision_inputs
from research.intraday_scenario_management_case import ManagedCase, replay_matched_breakout
from research.intraday_scenario_management_compare import DecisionObservation, _RequestedOpens, _position_replay
from research.intraday_scenario_management_entry import fill_matched_entry, prepare_matched_entry
from research.intraday_scenario_management_report import Coordinate, ManagementObservation
from research.intraday_scenario_policy import Episode, Request
from research.intraday_scenario_questions import opening_range_questions
from research.intraday_scenario_rollout import _visible
from research.intraday_scenario_snapshot import ProjectedCoordinate, ProjectedDay, _validate_day
from research.intraday_scenario_state import ScenarioEvent, scan_opening_range_high

DayStatus = Literal["no_event", "unknown", "unsupported", "no_entry", "declined", "unresolved", "confirmed"]
SessionProvider = Callable[[str, str], SessionInputs | None]


@dataclass(frozen=True)
class InitialEntryTrace:
    """Frozen request and modeled units actually applied to the research book."""

    decision_at: datetime
    request: Request
    open_price: Fraction | None
    fill_status: Literal["modeled", "declined", "indeterminate"]
    fill_quantity: int


@dataclass(frozen=True)
class ManagementDayCase:
    symbol: str
    status: DayStatus
    reason: str
    managed: ManagedCase | None = None
    initial_entry: InitialEntryTrace | None = None


@dataclass(frozen=True)
class ManagementDay:
    day: str
    scenario: str
    cases: tuple[ManagementDayCase, ...]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(case.symbol for case in self.cases)

    @property
    def observations(self) -> dict[Coordinate, ManagementObservation]:
        return {
            (self.day, case.symbol): ManagementObservation(
                "unconfirmed" if case.status in ("unknown", "unresolved") else case.status,
                case.managed.comparison if case.managed is not None else None,
            )
            for case in self.cases
        }


@dataclass(frozen=True)
class _ConfirmedEntry:
    initial: Episode
    session: SessionInputs
    opens: _RequestedOpens
    event: ScenarioEvent
    reason: str
    trace: InitialEntryTrace


def _prepare_coordinate(
    day: str, coordinate: ProjectedCoordinate, frames: tuple[DecisionFrame, ...], read_session: SessionProvider, *,
    risk_budget: Fraction, cost_bps: int,
) -> ManagementDayCase | _ConfirmedEntry:
    symbol = coordinate.symbol
    if coordinate.status == "known_no_event":
        return ManagementDayCase(symbol, "no_event", coordinate.status)
    if coordinate.status in ("support_unknown", "session_missing") or coordinate.breakout_gap:
        reason = "breakout_observation_gap" if coordinate.breakout_gap else coordinate.status
        return ManagementDayCase(symbol, "unknown", reason)
    if coordinate.status == "not_yet_eligible":
        return ManagementDayCase(symbol, "unsupported", coordinate.status)
    if not frames:
        return ManagementDayCase(symbol, "no_entry", "breakout_not_observed")

    first = frames[0]
    if first.chart is None or first.chart.status == "unsupported":
        return ManagementDayCase(symbol, "unsupported", "breakout_chart_unsupported")
    if first.chart.status == "unknown":
        return ManagementDayCase(symbol, "unknown", "breakout_chart_unknown")
    if first.chart.status == "resolved":
        return ManagementDayCase(symbol, "no_entry", "breakout_question_already_resolved")
    _require(first.initial.question is not None and first.initial.question.start_at == first.observed_at
             and all(frame.initial == first.initial for frame in frames), "eh_management_breakout_question_differs")

    session = read_session(day, symbol)
    if session is None:
        return ManagementDayCase(symbol, "unknown", "evaluation_session_missing")
    _session(session)
    _require((session.symbol, session.opening, session.closing) == (symbol, coordinate.opening, coordinate.closing),
             "eh_management_evaluation_session_identity_differs")
    scan = scan_opening_range_high(session, current_evidence=coordinate.evidence, through=first.observed_at)
    event = scan.event
    _require(scan.status == "event" and event is not None and event.breakout is not None
             and (event.origin.available_at, event.breakout.available_at, event.expires_at)
             == (first.event_at, first.observed_at, first.event_expires_at), "eh_management_breakout_event_differs")
    paths = opening_range_questions(session, event)
    _require(paths[-1] == first.initial and chart_decision_inputs(
        session, first.initial.question, first.initial.start_close, decision_at=first.observed_at,
    ) == first.chart, "eh_management_breakout_prefix_differs")

    visible, _ = _visible(session, first.observed_at - 3 * MINUTE, first.observed_at)
    _require(visible is not None, "eh_management_breakout_minute_differs")
    prepared = prepare_matched_entry(first, cash.new_book("eh-matched-management"), visible_minute=visible,
                                     risk_budget=risk_budget, cost_bps=cost_bps)
    request = prepared.episode.pending
    if request is None:
        return ManagementDayCase(symbol, "no_entry", prepared.reason)
    opens = _RequestedOpens(session)
    open_price = opens[request.execute_at]
    initial, fill = fill_matched_entry(prepared, request.execute_at, open_price)
    trace = InitialEntryTrace(prepared.episode.last_at, request, open_price, fill.outcome, fill.quantity)
    if not fill.filled:
        status = "unresolved" if fill.outcome == "indeterminate" else "declined"
        return ManagementDayCase(symbol, status, fill.reason, initial_entry=trace)
    return _ConfirmedEntry(initial, session, opens, event, fill.reason, trace)


def _prepared_coordinates(
    day: ProjectedDay, *, scenario: str, read_session: SessionProvider, risk_budget: Fraction, cost_bps: int,
    checkpoint: Callable[[str], None] | None,
) -> Iterator[tuple[ManagementDayCase | _ConfirmedEntry, tuple[DecisionFrame, ...]]]:
    _require(type(day) is ProjectedDay and scenario in ("S1", "S2")
             and callable(read_session) and (checkpoint is None or callable(checkpoint))
             and type(risk_budget) is Fraction and risk_budget > 0 and type(cost_bps) is int and cost_bps in (33, 43),
             "eh_management_invalid_day_inputs")
    _validate_day(day)
    ranked = sorted((target.rank, item) for item in day.coordinates for target in item.targets
                    if target.scenario == scenario)
    _require(tuple(rank for rank, _ in ranked) == tuple(range(1, 21)), "eh_management_original_roster_differs")

    breakouts: dict[str, list[DecisionFrame]] = defaultdict(list)
    for row in day.rows:
        if scenario in row.scenarios and row.frame.initial.stage == "breakout":
            breakouts[row.frame.symbol].append(row.frame)
    for _, item in ranked:
        if checkpoint is not None:
            checkpoint("eh_management_coordinate")
        frames = tuple(breakouts[item.symbol])
        yield _prepare_coordinate(day.day, item, frames, read_session, risk_budget=risk_budget, cost_bps=cost_bps), frames


def baseline_holding_inputs(
    day: ProjectedDay, *, scenario: str, read_session: SessionProvider, risk_budget: Fraction, cost_bps: int,
    checkpoint: Callable[[str], None] | None = None,
) -> tuple[HeldDecisionInputs, ...]:
    """Collect first-position observations from the shared structural and time baseline."""
    observations = []

    def collect(entry: _ConfirmedEntry) -> None:
        def observe(view: DecisionObservation) -> None:
            observations.append(held_decision_inputs(entry.session, view.episode, level=entry.event.level.price,
                band=entry.event.band, decision_at=view.at, planning_cap=view.planning_cap))

        _position_replay(entry.initial, entry.session, entry.opens, observe)

    for entry, _ in _prepared_coordinates(day, scenario=scenario, read_session=read_session,
            risk_budget=risk_budget, cost_bps=cost_bps, checkpoint=checkpoint):
        if isinstance(entry, _ConfirmedEntry):
            collect(entry)
    return tuple(observations)


def run_management_day(
    day: ProjectedDay, *, scenario: str, roster: ChartAnalogueRoster, read_session: SessionProvider,
    risk_budget: Fraction, cost_bps: int, downside_penalty: Fraction, fresh_reentry: bool,
    checkpoint: Callable[[str], None] | None = None, held_roster: HeldAnalogueRoster | None = None,
) -> ManagementDay:
    """Read only the current coordinates and the roster's earlier fold sessions."""
    _require(type(day) is ProjectedDay and type(roster) is ChartAnalogueRoster
             and type(downside_penalty) is Fraction and downside_penalty >= 0 and type(fresh_reentry) is bool,
             "eh_management_invalid_day_inputs")
    fold = roster.fold
    _require(day.day in fold.test_sessions, "eh_management_day_outside_test_fold")
    _require(held_roster is None or (type(held_roster) is HeldAnalogueRoster and held_roster.fold == fold),
             "eh_management_held_roster_fold_differs")

    def read_prior_session(prior_day: str, symbol: str) -> SessionInputs | None:
        _require(prior_day in fold.training_sessions and prior_day < day.day,
                 "eh_management_prior_session_outside_training_fold")
        session = read_session(prior_day, symbol)
        if session is not None:
            _session(session)
            _require(session.symbol == symbol and session.opening.astimezone(KST).date().isoformat() == prior_day
                     and session.closing <= fold.first_test_opening, "eh_management_prior_session_identity_differs")
        return session

    cases = []
    for entry, frames in _prepared_coordinates(day, scenario=scenario, read_session=read_session,
            risk_budget=risk_budget, cost_bps=cost_bps, checkpoint=checkpoint):
        if isinstance(entry, ManagementDayCase):
            cases.append(entry)
            continue
        managed = replay_matched_breakout(entry.initial, entry.session, entry.opens, entry.event, frames, roster,
            read_prior_session=read_prior_session, downside_penalty=downside_penalty, fresh_reentry=fresh_reentry,
            held_roster=held_roster)
        cases.append(ManagementDayCase(entry.initial.symbol, "confirmed", entry.reason, managed,
                                      initial_entry=entry.trace))
    return ManagementDay(day.day, scenario, tuple(cases))
