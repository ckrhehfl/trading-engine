"""One chronological EH forecast fold, with no evaluation-outcome reader."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from math import fsum
from typing import TYPE_CHECKING

from research.intraday_chart_inputs import _need
from research.intraday_scenario_decision_inputs import ChartDecisionInputs
from research.intraday_scenario_empirical import EmpiricalPathModel, HistoricalDecision
from research.intraday_scenario_forecast_scores import ForecastKey
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome, horizon_probabilities
from research.intraday_scenario_labels import QuestionId
from research.intraday_scenario_sequence import (
    SequenceInputs, SequenceTrainingRow, fit_sequence, pack_training_inputs, predict_sequence, sequence_inputs,
)
from research.intraday_scenario_sequence_reference import verify_sequence_probabilities

if TYPE_CHECKING:
    from research.intraday_campaign_ml_labels import ChronologicalFold
    from research.intraday_scenario_snapshot import ProjectedDay, ProjectedDecision


MIN_TRAIN_EVENTS = 100
MIN_TRAIN_DATES = 30
QuestionKey = tuple[str, str, QuestionId]


@dataclass(frozen=True)
class FoldDecision:
    day: str
    symbol: str
    stage: str
    observed_at: datetime
    key: ForecastKey | None
    chart_status: str
    peers_supported: bool
    empirical: tuple[float, ...] | None
    chart: tuple[float, ...] | None
    chart_matched: tuple[float, ...] | None
    market_matched: tuple[float, ...] | None


@dataclass(frozen=True)
class FoldForecast:
    scenario: str
    index: int
    original_test_coordinates: int
    training_events: int
    training_dates: int
    matched_training_events: int
    matched_training_dates: int
    artifacts: tuple[dict | None, dict | None, dict | None]
    decisions: tuple[FoldDecision, ...]


def _condition(chart: ChartDecisionInputs, probabilities: tuple[float, ...] | None) -> tuple[float, ...] | None:
    if probabilities is None:
        return None
    horizon_probabilities(probabilities)
    remaining, departed, retested = chart.state[2:5]
    elapsed = 30 - 30 * remaining
    retained = []
    for (path, horizon), weight in zip(OUTCOMES, probabilities, strict=True):
        excluded = (path != "P4" and horizon <= elapsed) or (path == "P1" and retested) or (
            path == "P2" and not departed and horizon - elapsed < 2)
        retained.append(0.0 if excluded else weight)
    mass = fsum(retained)
    return tuple(weight / mass for weight in retained) if mass else None


def _case_sequence(row: ProjectedDecision) -> SequenceInputs:
    peer = row.peer if row.peer is not None and row.peer.sequence is not None else None
    return sequence_inputs(row.frame.chart, row.frame.initial.stage, peer)


def _coverage(rows: set[tuple[str, str]]) -> tuple[int, int]:
    return len(rows), len({day for day, _ in rows})


def forecast_fold(
    days: Iterable[ProjectedDay], fold: ChronologicalFold, scenario: str,
    read_training_outcome: Callable[[QuestionKey], TrainingOutcome | None],
) -> FoldForecast:
    """Fit chart and context on identical matched training rows where required."""
    _need(scenario in ("S1", "S2") and 1 <= fold.index <= 5 and callable(read_training_outcome),
          "registered_EH_fold_and_scenario_required")
    _need(fold.training_sessions and fold.test_sessions and fold.training_sessions[-1] < fold.test_sessions[0]
          and fold.first_test_opening.date() == date.fromisoformat(fold.test_sessions[0]), "invalid_EH_fold_clock")
    past, test = set(fold.training_sessions), set(fold.test_sessions)
    training, matched_training, query_rows = [], [], []
    events, matched_events = set(), set()
    outcomes: dict[QuestionKey, TrainingOutcome | None] = {}
    original_test_coordinates = 0

    def history():
        nonlocal original_test_coordinates
        for expected_day, projected in zip((*fold.training_sessions, *fold.test_sessions), days, strict=True):
            _need(projected.day == expected_day, "EH_fold_projection_dates_differ")
            if projected.day in test:
                original_test_coordinates += sum(scenario in coordinate.scenarios for coordinate in projected.coordinates)
            for row in projected.rows:
                if scenario not in row.scenarios:
                    continue
                frame = row.frame
                _need(frame.observed_at.date() == date.fromisoformat(projected.day), "EH_fold_frame_day_differs")
                ready = frame.chart is not None and frame.chart.status == "ready"
                if projected.day in test:
                    query_rows.append((projected.day, row, _case_sequence(row) if ready else None))
                    continue
                _need(projected.day in past, "EH_fold_feature_day_unregistered")
                if not ready:
                    continue
                _need(row.question_key is not None and frame.observed_at < fold.first_test_opening,
                      "EH_training_forecast_after_split")
                if row.question_key not in outcomes:
                    outcomes[row.question_key] = read_training_outcome(row.question_key)
                outcome = outcomes[row.question_key]
                if outcome is not None and outcome.mature_at >= fold.first_test_opening:
                    outcome = None
                yield HistoricalDecision(frame.initial.stage, frame.chart, outcome)
                if outcome is None:
                    continue
                sequence = _case_sequence(row)
                case = SequenceTrainingRow(pack_training_inputs(sequence), outcome)
                event = projected.day, frame.symbol
                training.append(case)
                events.add(event)
                if sequence.peer is not None:
                    matched_training.append(case)
                    matched_events.add(event)

    cutoff = fold.first_test_opening
    empirical = EmpiricalPathModel(history(), evaluation_starts_at=cutoff)
    train_count, train_days = _coverage(events)
    common_count, common_days = _coverage(matched_events)

    def fit(rows, event_count, day_count, *, peers=False):
        if event_count < MIN_TRAIN_EVENTS or day_count < MIN_TRAIN_DATES:
            return None
        return fit_sequence(tuple(rows), evaluation_starts_at=cutoff, fold_index=fold.index, with_peers=peers)

    chart_model = fit(training, train_count, train_days)
    matched_chart = chart_model if len(training) == len(matched_training) else fit(
        matched_training, common_count, common_days)
    market_model = fit(matched_training, common_count, common_days, peers=True)

    def infer(model, *, paired=False):
        if model is None:
            return {}
        keys, rows = [], []
        for index, (_, _, sequence) in enumerate(query_rows):
            if sequence is not None and (not paired or sequence.peer is not None):
                keys.append(index)
                rows.append(sequence)
        queries = tuple(rows)
        probabilities = predict_sequence(model, queries)
        if queries:
            verify_sequence_probabilities(model, queries, tuple(
                (row.forecast_id, values) for row, values in zip(queries, probabilities, strict=True)))
        return dict(zip(keys, probabilities, strict=True))

    wide = infer(chart_model)
    chart_context = infer(matched_chart, paired=True)
    market_context = infer(market_model, paired=True)
    decisions = []
    for index, (day, row, sequence) in enumerate(query_rows):
        frame, stage = row.frame, row.frame.initial.stage
        chart = frame.chart
        key = (frame.symbol, stage, chart.forecast_id[1], frame.observed_at) if chart is not None else None
        support = chart.status if chart is not None else "unsupported"
        baseline = empirical.predict(stage, chart).weights if sequence is not None else None
        projections = (baseline, wide.get(index), chart_context.get(index), market_context.get(index))
        weights = tuple(_condition(chart, item) for item in projections)
        decisions.append(FoldDecision(day, frame.symbol, stage, frame.observed_at, key, support,
                                      sequence is not None and sequence.peer is not None, *weights))
    return FoldForecast(scenario, fold.index, original_test_coordinates, train_count, train_days,
                        common_count, common_days, (chart_model, matched_chart, market_model), tuple(decisions))
