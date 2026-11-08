"""Matched EI probability scores; economic entry and holding are separate.

The runtime must seal forecasts before calling this pure evaluation layer.
Context attribution is the episode's original context, never a later outcome.
Scores give equal weight to questions within a symbol/date, then to symbols
within a date, then to dates. Repeated questions are not independent trials.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta, timezone
from math import fsum

from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_forecast_fold import (
    MODEL_KEYS, ArmPrediction, FoldForecast, QuestionForecast, _probabilities,
)
from research.intraday_context_snapshot import (
    LabeledCoordinate, QuestionLabel, _roster, _validate_coordinate, _validate_labels, question_digest,
)
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_forecast_scores import ForecastPair, ForecastScore, matched_forecast_scores


CONTRASTS = (
    ("context_empirical_increment", "pooled_empirical", "context_empirical"),
    ("chart_tree_vs_pooled", "pooled_empirical", "chart_lightgbm"),
    ("context_tree_increment", "chart_lightgbm", "context_lightgbm"),
    ("chart_sequence_vs_tree", "chart_lightgbm", "chart_sequence"),
    ("context_sequence_increment", "chart_sequence", "context_sequence"),
    ("context_sequence_vs_tree", "context_lightgbm", "context_sequence"),
)
ORIGIN_CONTEXTS = ("all", "A", "B", "other")
EvaluationReader = Callable[[str, str, str], QuestionLabel | None]
_HORIZON_COUNTS = ("horizon_pairs", "horizon_outcomes", "horizon_events", "horizon_dates")
_COUNTS = ("candidates", "first_supported", "second_supported", "paired_forecasts",
           "paired_outcomes", "scored_events", "scored_dates")


@dataclass(frozen=True)
class ContextForecastGroup:
    stage: str
    origin_context: str
    contrast: str
    counts: ForecastScore


@dataclass(frozen=True)
class ContextForecastReport:
    scenario: str
    fold: int
    evaluation_dates: tuple[str, ...]
    original_coordinates: int
    original_questions: int
    original_frames_by_status: tuple[tuple[str, int], ...]
    coordinate_status: tuple[tuple[str, int], ...]
    unavailable_labels_by_horizon: tuple[tuple[int, str, int], ...]
    groups: tuple[ContextForecastGroup, ...]


def _key(row: QuestionForecast) -> tuple:
    question = row.question.question
    _need(question.stage in ("approach", "breakout")
          and question.episode_origin_context in ORIGIN_CONTEXTS[1:]
          and question.anchor_id[0] == row.symbol
          and question.path.start_at == row.question.forecast_at,
          "EI_score_question_clock_or_identity_differs")
    return row.symbol, question.stage, question.path.question_id, row.question.forecast_at


def _arms(row: QuestionForecast) -> dict:
    _need(type(row.predictions) is tuple and tuple(arm.key for arm in row.predictions) == MODEL_KEYS,
          "EI_score_fixed_prediction_arms_required")
    result = {}
    for arm in row.predictions:
        _need(type(arm) is ArmPrediction, "EI_score_typed_prediction_required")
        if arm.status == "predicted":
            _need(arm.reason is None, "EI_score_predicted_reason_differs")
            result[arm.key] = _probabilities(arm.probabilities)
        else:
            _need(arm.status == "unsupported" and arm.probabilities is None
                  and type(arm.reason) is str and bool(arm.reason), "EI_score_unsupported_prediction_differs")
            result[arm.key] = None
    return result


def _consistent_paths(label: QuestionLabel) -> None:
    terminal, no_touch_through = None, label.question.path.start_at
    for result in label.results:
        if result.status == "resolved":
            if result.path == "P4":
                _need(terminal is None and result.observed_through == result.question.end_at,
                      "EI_score_timeout_path_incomplete_or_after_terminal")
                no_touch_through = result.question.end_at
            elif terminal is None:
                _need(result.observed_through > no_touch_through, "EI_score_touch_precedes_timeout_prefix")
                terminal = result.path, result.observed_through, result.mature_at
            else:
                _need((result.path, result.observed_through, result.mature_at) == terminal,
                      "EI_score_terminal_path_changed_across_horizons")
        elif terminal is not None:
            _need(result.status == "unsupported" and result.reason == "outside_session",
                  "EI_score_known_terminal_became_unknown")


def score_fold(forecast: FoldForecast, read_evaluation_label: EvaluationReader, *,
               allowed_dates: tuple[str, ...], expected_targets: tuple[TargetCoordinate, ...]) -> ContextForecastReport:
    """Validate the complete fixed prediction roster before opening any labels."""
    _need(type(forecast) is FoldForecast and forecast.scenario in ("S1", "S2")
          and type(forecast.index) is int and 1 <= forecast.index <= 5
          and callable(read_evaluation_label) and type(allowed_dates) is tuple and bool(allowed_dates)
          and tuple(sorted(set(allowed_dates))) == allowed_dates
          and all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in allowed_dates),
          "EI_score_fixed_fold_and_dates_required")
    _clock(forecast.first_test_opening)
    _need(forecast.first_test_opening.astimezone(timezone(timedelta(hours=9))).date().isoformat() == allowed_dates[0],
          "EI_score_evaluation_split_clock_differs")
    dates, coordinates, expected, frame_counts, coordinate_counts = set(allowed_dates), {}, [], Counter(), Counter()
    original = _roster(expected_targets)
    original = {key: targets for key, targets in original.items() if key[0] in dates
                and any(target.scenario == forecast.scenario for target in targets)}
    _need({key[0] for key in original} == dates, "EI_score_evaluation_dates_outside_original_roster")
    _need(tuple((row.session, row.symbol) for row in forecast.original_test_coordinates) == tuple(original),
          "EI_score_original_coordinate_roster_omitted_or_extra")
    previous = None
    for coordinate in forecast.original_test_coordinates:
        key = coordinate.session, coordinate.symbol
        _validate_coordinate(coordinate, original[key])
        _need(coordinate.opening is None or coordinate.opening >= forecast.first_test_opening,
              "EI_score_coordinate_precedes_training_cutoff")
        _need(coordinate.session in dates and key not in coordinates and (previous is None or key > previous)
              and any(target.scenario == forecast.scenario for target in coordinate.targets),
              "EI_score_original_coordinate_order_or_scope_differs")
        previous = key
        coordinates[key] = coordinate
        frame_counts.update(frame.status for frame in coordinate.frames)
        coordinate_counts[coordinate.status] += 1
        expected.extend((coordinate.session, coordinate.symbol, feature) for feature in coordinate.questions)
    _need(type(forecast.predictions) is tuple and len(forecast.predictions) == len(expected),
          "EI_score_question_roster_omitted_or_extra")
    rows, key_map, label_counts = [], {}, Counter()
    for predicted, (day, symbol, feature) in zip(forecast.predictions, expected, strict=True):
        _need(type(predicted) is QuestionForecast and (predicted.session, predicted.symbol, predicted.question)
              == (day, symbol, feature), "EI_score_prediction_does_not_match_original_feature")
        key, arms = _key(predicted), _arms(predicted)
        _need(key not in key_map, "EI_score_distinct_EI_questions_collide")
        key_map[key] = predicted
        rows.append((key, feature.question.stage, feature.question.episode_origin_context, arms))

    outcomes = {}

    def observe(key):
        if key in outcomes:
            return outcomes[key]
        row = key_map[key]
        label = read_evaluation_label(row.session, row.symbol, question_digest(row.question.question))
        if label is None:
            values = (None, None, None)
            label_counts.update((horizon, "label_missing") for horizon in (5, 15, 30))
        else:
            _need(type(label) is QuestionLabel and label.question == row.question.question,
                  "EI_score_evaluation_label_identity_differs")
            coordinate = replace(coordinates[row.session, row.symbol], questions=(row.question,))
            _validate_labels(LabeledCoordinate(row.session, row.symbol, label.status, (label,)), coordinate)
            _consistent_paths(label)
            values = tuple(result.path if result.status == "resolved" else None for result in label.results)
            if not label.results:
                values = (None, None, None)
                label_counts.update((horizon, label.status) for horizon in (5, 15, 30))
            else:
                label_counts.update((horizon, result.reason or result.status)
                    for horizon, result in zip((5, 15, 30), label.results, strict=True) if result.status != "resolved")
        outcomes[key] = values
        return values

    groups = []
    for stage in ("approach", "breakout"):
        for context in ORIGIN_CONTEXTS:
            subset = tuple(row for row in rows if row[1] == stage and (context == "all" or row[2] == context))
            for name, first, second in CONTRASTS:
                pairs = tuple(ForecastPair(key, arms[first], arms[second]) for key, _, _, arms in subset)
                groups.append(ContextForecastGroup(stage, context, name, matched_forecast_scores(pairs, observe)))
    return ContextForecastReport(forecast.scenario, forecast.index, allowed_dates, len(coordinates), len(rows),
        tuple(sorted(frame_counts.items())), tuple(sorted(coordinate_counts.items())),
        tuple((*key, count) for key, count in sorted(label_counts.items())), tuple(groups))


def combine_fold_reports(reports: tuple[ContextForecastReport, ...]) -> tuple[dict, ...]:
    """Aggregate nonoverlapping evaluation dates; do not combine S1 with S2."""
    _need(type(reports) is tuple and reports and all(type(item) is ContextForecastReport
          and item.scenario == reports[0].scenario for item in reports)
          and tuple(item.fold for item in reports) == tuple(range(1, len(reports) + 1)),
          "EI_score_fixed_report_order_required")
    dates = tuple(day for item in reports for day in item.evaluation_dates)
    _need(all(type(item.evaluation_dates) is tuple and item.evaluation_dates for item in reports)
          and dates == tuple(sorted(set(dates))), "EI_score_declared_evaluation_dates_overlap_or_differ")
    expected_groups = tuple((stage, context, name) for stage in ("approach", "breakout")
                            for context in ORIGIN_CONTEXTS for name, _, _ in CONTRASTS)
    _need(all(tuple((group.stage, group.origin_context, group.contrast) for group in item.groups)
              == expected_groups for item in reports), "EI_score_fixed_group_roster_differs")
    combined = []
    for identity, groups in zip(expected_groups, zip(*(item.groups for item in reports), strict=True), strict=True):
        scores = tuple(group.counts for group in groups)
        _need(all(all(day.isoformat() in item.evaluation_dates for day, _ in group.counts.daily_difference)
                  for item, group in zip(reports, groups, strict=True)), "EI_score_scored_date_outside_evaluation")
        days = tuple(day for score in scores for day, _ in score.daily_difference)
        _need(days == tuple(sorted(set(days))) and all(score.scored_dates == len(score.daily_difference)
              and score.horizon_dates == tuple(sum(row[index] is not None for _, row in score.daily_difference)
                                               for index in range(3)) for score in scores),
              "EI_score_scored_dates_overlap_or_differ")
        counts = {name: tuple(sum(getattr(score, name)[i] for score in scores) for i in range(3))
                  for name in _HORIZON_COUNTS}

        def weighted(name):
            return tuple(fsum(getattr(score, name)[i] * score.horizon_dates[i] for score in scores
                              if score.horizon_dates[i]) / count if count else None
                         for i, count in enumerate(counts["horizon_dates"]))

        first, second = weighted("first_brier"), weighted("second_brier")
        combined.append({"stage": identity[0], "origin_context": identity[1], "contrast": identity[2],
                         **{name: sum(getattr(score, name) for score in scores) for name in _COUNTS}, **counts,
                         "first_brier": first, "second_brier": second,
                         "second_minus_first": tuple(None if left is None else right - left
                                                      for left, right in zip(first, second, strict=True)),
                         "daily_difference": tuple(row for score in scores for row in score.daily_difference)})
    return tuple(combined)
