"""Join prior-chart action values to one matched initial EH position."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import TYPE_CHECKING, Literal

from research.intraday_ed_inputs import SessionInputs, _require
from research.intraday_scenario_action_projection import SourceCoordinate, evaluate_chart_actions, evaluate_held_actions
from research.intraday_scenario_analogue_roster import ChartAnalogueRoster
from research.intraday_scenario_decision_schedule import DecisionFrame
from research.intraday_scenario_held_inputs import held_decision_inputs
from research.intraday_scenario_management_compare import DecisionObservation, ManagementComparison, ModeledOpens, compare_management
from research.intraday_scenario_policy import EntryBounds, Episode, Values
from research.intraday_scenario_state import ScenarioEvent

if TYPE_CHECKING:
    from research.intraday_scenario_analogue_roster import HeldAnalogueRoster


@dataclass(frozen=True)
class ManagementAssessment:
    at: datetime
    reason: str
    prior_paths: int
    prior_dates: int
    values: Values | None
    planning_cap: datetime
    attempt: Literal[1, 2] = 1
    sources: tuple[SourceCoordinate, ...] = ()


@dataclass(frozen=True)
class ManagedCase:
    comparison: ManagementComparison
    decisions: tuple[ManagementAssessment, ...]


def replay_matched_breakout(
    initial: Episode, session: SessionInputs, opens: ModeledOpens, event: ScenarioEvent,
    frames: tuple[DecisionFrame, ...], roster: ChartAnalogueRoster, *,
    read_prior_session: Callable[[str, str], SessionInputs | None], downside_penalty: Fraction,
    fresh_reentry: bool, held_roster: HeldAnalogueRoster | None = None,
) -> ManagedCase:
    """Keep one known breakout fill, then replay only matching observed decision clocks."""
    _require(type(fresh_reentry) is bool and type(downside_penalty) is Fraction and downside_penalty >= 0
             and event.breakout is not None and initial.symbol == session.symbol == event.level.symbol
             and initial.start_at == event.origin.available_at and initial.end_at == event.expires_at
             and (held_roster is None or held_roster.fold == roster.fold),
             "eh_management_case_breakout_identity_differs")
    clocks = tuple(frame.observed_at for frame in frames)
    _require(clocks == tuple(sorted(set(clocks))) and all(
        (frame.symbol, frame.event_at, frame.event_expires_at, frame.initial.stage)
        == (initial.symbol, initial.start_at, initial.end_at, "breakout")
        and frame.initial.question is not None and frame.initial.question.start_at == event.breakout.available_at
        and (frame.chart is None or frame.chart.forecast_id == (frame.symbol, *frame.initial.question.forecast_id(frame.observed_at)))
        for frame in frames), "eh_management_case_decision_roster_differs")
    timeline = dict(zip(clocks, frames, strict=True))
    assessments = []

    def manage(view: DecisionObservation):
        if view.episode.attempts == 2:
            # The fresh route is an independent, rule-managed second attempt.
            assessments.append(ManagementAssessment(view.at, "fresh_holding_rule_only", 0, 0, None,
                                                    view.planning_cap, attempt=2))
            return None
        if held_roster is None:
            frame = timeline.get(view.at)
            chart = frame.chart if frame is not None else None
            if chart is None or chart.status != "ready":
                assessments.append(ManagementAssessment(view.at, "chart_question_unavailable", 0, 0, None, view.planning_cap))
                return None
            value = evaluate_chart_actions(roster, "breakout", chart, view.episode, visible_minute=view.minute,
                query_opening=session.opening, query_closing=session.closing, cap=view.planning_cap,
                read_prior_session=read_prior_session, downside_penalty=downside_penalty)
        else:
            held = held_decision_inputs(session, view.episode, level=event.level.price, band=event.band,
                                        decision_at=view.at, planning_cap=view.planning_cap)
            value = evaluate_held_actions(held_roster, held, view.episode, visible_minute=view.minute,
                query_opening=session.opening, query_closing=session.closing,
                read_prior_session=read_prior_session, downside_penalty=downside_penalty)
        utilities = value.rollout.values if value.rollout is not None else None
        assessments.append(ManagementAssessment(view.at, value.reason, value.selection.neighbors, value.selection.dates,
                                                utilities, view.planning_cap, sources=value.source_coordinates))
        return utilities

    def consider_fresh(view: DecisionObservation):
        evidence = view.fresh
        _require(evidence is not None and evidence.status == "ready" and evidence.confirmation is not None,
                 "eh_management_case_fresh_confirmation_required")
        stop = min(Fraction(evidence.confirmation.low), event.level.price - event.band)
        maximum = Fraction(view.minute.ohlc[3]) + event.band
        if not 0 < stop < maximum:
            return None
        return Values(view.at, wait=Fraction(), enter=Fraction(1)), EntryBounds(stop, maximum)

    comparison = compare_management(initial, session, opens, values_at=manage, event=event,
                                    reentry_at=consider_fresh if fresh_reentry else None)
    return ManagedCase(comparison, tuple(assessments))
