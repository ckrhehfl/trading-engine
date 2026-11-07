"""Compare available actions on an unchanged roster of prior chart paths."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from typing import TYPE_CHECKING

from research.intraday_campaign_readiness import KST
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _require
from research.intraday_scenario_analogue_path import rebase_analogue_path
from research.intraday_scenario_decision_inputs import ChartDecisionInputs
from research.intraday_scenario_held_inputs import HeldDecisionInputs
from research.intraday_scenario_policy import EntryBounds, Episode
from research.intraday_scenario_rollout import RolloutComparison, compare_actions

if TYPE_CHECKING:
    from research.intraday_campaign_ml_labels import ChronologicalFold
    from research.intraday_scenario_analogue_roster import (
        AnalogueSelection, ChartAnalogueRoster, HeldAnalogueRoster, HeldAnalogueSelection,
    )


SourceCoordinate = tuple[str, str, datetime]


@dataclass(frozen=True)
class ChartActionAssessment:
    selection: AnalogueSelection | HeldAnalogueSelection
    rollout: RolloutComparison | None
    reason: str
    source_coordinates: tuple[SourceCoordinate, ...] = ()


def _on_selected_paths(
    selection: AnalogueSelection | HeldAnalogueSelection, coordinates: tuple[SourceCoordinate, ...],
    fold: ChronologicalFold, episode: Episode, *, at: datetime, visible_minute: Minute,
    query_opening: datetime, query_closing: datetime, cap: datetime,
    read_prior_session: Callable[[str, str], SessionInputs | None], downside_penalty: Fraction,
    bounds: EntryBounds | None = None, wait_trigger: Fraction | None = None,
) -> ChartActionAssessment:
    if selection.status != "ready":
        return ChartActionAssessment(selection, None, "insufficient_prior_chart_paths", coordinates)
    _require(len(coordinates) == selection.neighbors, "eh_action_selected_count_differs")
    weight = Fraction(1, selection.neighbors)
    paths = []
    for index, (day, original_symbol, original_at) in enumerate(coordinates):
        _require(day in fold.training_sessions and original_at < fold.first_test_opening, "eh_chart_action_nonprior_path")
        source = read_prior_session(day, original_symbol)
        if source is None:
            return ChartActionAssessment(selection, None, "selected_prior_session_missing", coordinates)
        _require(source.symbol == original_symbol and source.opening.astimezone(KST).date().isoformat() == day,
                 "eh_chart_action_prior_session_identity_differs")
        paths.append(rebase_analogue_path(source, original_at, fold, key=f"{day}/{original_symbol}/{index}",
                     weight=weight, decision_at=at, visible_minute=visible_minute, query_symbol=episode.symbol,
                     query_opening=query_opening, query_closing=query_closing, cap=cap))
    result = compare_actions(episode, at, visible_minute=visible_minute, paths=tuple(paths),
                             planning_minutes=(cap - at) // MINUTE, downside_penalty=downside_penalty,
                             bounds=bounds, wait_trigger=wait_trigger)
    return ChartActionAssessment(selection, result, "ready" if result.values is not None else "unresolved_path_mass",
                                 coordinates)


def evaluate_chart_actions(
    roster: ChartAnalogueRoster, stage: str, chart: ChartDecisionInputs, episode: Episode, *,
    visible_minute: Minute, query_opening: datetime, query_closing: datetime, cap: datetime,
    read_prior_session: Callable[[str, str], SessionInputs | None], downside_penalty: Fraction,
    bounds: EntryBounds | None = None, wait_trigger: Fraction | None = None,
) -> ChartActionAssessment:
    """Select paths before accessing their suffixes, retaining unsupported mass."""
    symbol, _, at = chart.forecast_id
    horizon = cap - at
    _require(episode.symbol == symbol and query_opening < at < cap <= episode.end_at < query_closing
             and MINUTE <= horizon <= 30 * MINUTE and not horizon % MINUTE
             and visible_minute.symbol == symbol and visible_minute.start_at == at - 3 * MINUTE
             and callable(read_prior_session), "eh_chart_action_current_geometry_differs")
    selection = roster.match(stage, chart)
    coordinates = tuple((original_at.astimezone(KST).date().isoformat(), original_symbol, original_at.astimezone(UTC))
                        for original_symbol, _, original_at in selection.source_forecast_ids)
    return _on_selected_paths(selection, coordinates, roster.fold, episode, at=at, visible_minute=visible_minute,
        query_opening=query_opening, query_closing=query_closing, cap=cap, read_prior_session=read_prior_session,
        downside_penalty=downside_penalty, bounds=bounds, wait_trigger=wait_trigger)


def evaluate_held_actions(
    roster: HeldAnalogueRoster, held: HeldDecisionInputs, episode: Episode, *, visible_minute: Minute,
    query_opening: datetime, query_closing: datetime, read_prior_session: Callable[[str, str], SessionInputs | None],
    downside_penalty: Fraction,
) -> ChartActionAssessment:
    """Compare the held lot's actions even after its initial question resolves."""
    at, cap = held.decision_at, held.planning_cap
    _require(held.symbol == visible_minute.symbol == episode.symbol and held.entered_at == episode.entered_at
             and held.attempt == episode.attempts and query_opening < at < cap <= episode.end_at < query_closing
             and MINUTE <= cap - at <= 30 * MINUTE and not (cap - at) % MINUTE
             and visible_minute.start_at == at - 3 * MINUTE and callable(read_prior_session),
             "eh_held_action_current_geometry_differs")
    selection = roster.match(held)
    if held.status != "ready":
        return ChartActionAssessment(selection, None, "held_chart_prefix_unknown", selection.source_coordinates)
    return _on_selected_paths(selection, selection.source_coordinates, roster.fold, episode, at=at,
        visible_minute=visible_minute, query_opening=query_opening, query_closing=query_closing, cap=cap,
        read_prior_session=read_prior_session, downside_penalty=downside_penalty)
