"""Independent roster, attribution and date-weighted EI Brier verification.

The supplied forecast and evaluation reader must already be authenticated by
the caller. This module does not fit, acquire data, or call the report scorer.
Shared snapshot validation checks representation; all masks, marginalization,
score arithmetic and aggregation are recomputed here.
"""

from collections import Counter, defaultdict
from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone
from math import fsum, isclose, isfinite

from research.intraday_context_forecast_fold import ArmPrediction, FoldForecast, QuestionForecast
from research.intraday_context_forecast_report import ContextForecastGroup, ContextForecastReport
from research.intraday_context_snapshot import (
    LabeledCoordinate, ProjectedCoordinate, QuestionLabel, _validate_coordinate, _validate_labels, question_digest,
)
from research.intraday_ed_inputs import InputRefusal
from research.intraday_ed_sources import TargetCoordinate


_ARMS = ("pooled_empirical", "context_empirical", "chart_lightgbm", "context_lightgbm", "chart_sequence", "context_sequence")
_CONTRASTS = (
    ("context_empirical_increment", 0, 1), ("chart_tree_vs_pooled", 0, 2),
    ("context_tree_increment", 2, 3), ("chart_sequence_vs_tree", 2, 4),
    ("context_sequence_increment", 4, 5), ("context_sequence_vs_tree", 3, 5),
)
_CONTEXTS = ("all", "A", "B", "other")
_HORIZONS = (5, 15, 30)
_KST = timezone(timedelta(hours=9))
_ABS_TOL, _REL_TOL = 1e-12, 1e-10


def _need(condition, reason):
    if not condition:
        raise InputRefusal(f"EI_report_reference_{reason}")


def _day(value):
    _need(type(value) is str, "invalid_date")
    try:
        result = date.fromisoformat(value)
    except ValueError as error:
        raise InputRefusal("EI_report_reference_invalid_date") from error
    _need(result.isoformat() == value, "noncanonical_date")
    return result


def _clock(value):
    _need(type(value) is datetime and value.utcoffset() is not None
          and value.second == value.microsecond == 0, "invalid_clock")


def _original_targets(targets):
    _need(type(targets) is tuple and 0 < len(targets) <= 366 * 40, "original_targets_required")
    groups, ranks, members = defaultdict(list), set(), set()
    for target in targets:
        _need(type(target) is TargetCoordinate and target.scenario in ("S1", "S2")
              and type(target.symbol) is str and 0 < len(target.symbol) <= 64
              and type(target.rank) is int and 1 <= target.rank <= 20
              and (target.stored_row_count is None or type(target.stored_row_count) is int and target.stored_row_count >= 0)
              and type(target.state) is str and bool(target.state)
              and type(target.metadata) is bytes and len(target.metadata) <= 4096, "invalid_original_target")
        _day(target.session)
        rank = target.session, target.scenario, target.rank
        member = target.session, target.scenario, target.symbol
        _need(rank not in ranks and member not in members, "duplicate_original_target")
        ranks.add(rank)
        members.add(member)
        groups[target.session, target.symbol].append(target)
    _need(len({day for day, _ in groups}) <= 366, "original_date_bound_exceeded")
    return {key: tuple(sorted(rows, key=lambda row: row.scenario)) for key, rows in sorted(groups.items())}


def _marginals(arms):
    _need(type(arms) is tuple and len(arms) == 6 and all(type(arm) is ArmPrediction for arm in arms)
          and tuple(arm.key for arm in arms) == _ARMS, "prediction_arms_differ")
    result = []
    for arm in arms:
        if arm.status == "unsupported":
            _need(arm.probabilities is None and type(arm.reason) is str and bool(arm.reason), "unsupported_arm_differs")
            result.append(None)
            continue
        _need(arm.status == "predicted" and arm.reason is None, "predicted_arm_status_differs")
        values = arm.probabilities
        _need(type(values) is tuple and len(values) == 10
              and all(type(value) in (float, int) and 0 <= value <= 1 for value in values), "probability_values_differ")
        total = fsum(values)
        _need(isfinite(total) and abs(total - 1) <= 1e-9, "probability_mass_differs")
        projected = []
        for boundary in range(3):
            terminal = tuple(fsum(values[path + 3 * bucket] for bucket in range(boundary + 1)) / total
                             for path in range(3))
            unresolved = fsum(values[index] for index in range(3 * (boundary + 1), 10)) / total
            projected.append((*terminal, unresolved))
        result.append(tuple(projected))
    return tuple(result)


def _roster(forecast, allowed_dates, expected_targets):
    _need(type(forecast) is FoldForecast and forecast.schema == "ei-context-forecast-fold-draft-v1"
          and forecast.scenario in ("S1", "S2") and type(forecast.index) is int and 1 <= forecast.index <= 5
          and forecast.timing_scenarios_are_independent is False, "fixed_forecast_required")
    _need(type(allowed_dates) is tuple and bool(allowed_dates)
          and all(type(value) is str for value in allowed_dates)
          and allowed_dates == tuple(sorted(set(allowed_dates))), "allowed_dates_differ")
    for value in allowed_dates:
        _day(value)
    _clock(forecast.first_test_opening)
    _need(forecast.first_test_opening.astimezone(_KST).date().isoformat() == allowed_dates[0], "split_date_differs")
    expected = {key: rows for key, rows in _original_targets(expected_targets).items()
                if key[0] in allowed_dates and any(row.scenario == forecast.scenario for row in rows)}
    _need({day for day, _ in expected} == set(allowed_dates), "original_evaluation_dates_differ")
    _need(type(forecast.original_test_coordinates) is tuple
          and all(type(row) is ProjectedCoordinate for row in forecast.original_test_coordinates)
          and tuple((row.session, row.symbol) for row in forecast.original_test_coordinates) == tuple(expected),
          "original_coordinate_roster_differs")
    questions, frames, statuses = [], Counter(), Counter()
    for coordinate in forecast.original_test_coordinates:
        _validate_coordinate(coordinate, expected[coordinate.session, coordinate.symbol])
        _need(coordinate.opening is None or coordinate.opening >= forecast.first_test_opening, "coordinate_before_split")
        frames.update(row.status for row in coordinate.frames)
        statuses[coordinate.status] += 1
        questions.extend((coordinate, feature) for feature in coordinate.questions)
    _need(type(forecast.predictions) is tuple and len(forecast.predictions) == len(questions), "question_roster_differs")
    rows, seen = [], set()
    for prediction, (coordinate, feature) in zip(forecast.predictions, questions, strict=True):
        _need(type(prediction) is QuestionForecast
              and (prediction.session, prediction.symbol, prediction.question) == (coordinate.session, coordinate.symbol, feature),
              "prediction_feature_identity_differs")
        question = feature.question
        _clock(feature.forecast_at)
        _need(question.stage in ("approach", "breakout") and question.episode_origin_context in _CONTEXTS[1:]
              and question.anchor_id[0] == prediction.symbol and question.path.start_at == feature.forecast_at
              and feature.forecast_at >= forecast.first_test_opening
              and feature.forecast_at.astimezone(_KST).date().isoformat() == coordinate.session, "question_clock_or_context_differs")
        key = coordinate.session, coordinate.symbol, question_digest(question)
        _need(key not in seen, "duplicate_question")
        seen.add(key)
        rows.append((key, coordinate, feature, _marginals(prediction.predictions)))
    return tuple(rows), frames, statuses


def _outcome(coordinate, feature, label):
    if label is None:
        return (None,) * 3, tuple((horizon, "label_missing") for horizon in _HORIZONS)
    _need(type(label) is QuestionLabel and label.question == feature.question, "evaluation_label_identity_differs")
    _validate_labels(LabeledCoordinate(coordinate.session, coordinate.symbol, label.status, (label,)),
                     replace(coordinate, questions=(feature,)))
    if not label.results:
        return (None,) * 3, tuple((horizon, label.status) for horizon in _HORIZONS)
    outcomes, unavailable = [], []
    terminal = None
    prior_no_touch_end = feature.forecast_at
    for horizon, result in zip(_HORIZONS, label.results, strict=True):
        if result.status == "resolved":
            _clock(result.mature_at)
            _need(result.observed_through > feature.forecast_at, "resolved_label_has_no_future_observation")
            if result.path == "P4":
                _need(terminal is None and result.observed_through == result.question.end_at, "timeout_label_incomplete")
                prior_no_touch_end = result.question.end_at
            else:
                identity = result.path, result.observed_through, result.mature_at
                _need((terminal is None and result.observed_through > prior_no_touch_end)
                      or identity == terminal, "terminal_label_changed")
                terminal = identity
            outcomes.append(result.path)
        else:
            # A larger outside-session horizon can remain unsupported despite a
            # resolved shorter child. Do not erase the child's observed outcome.
            _need(terminal is None or result.status == "unsupported" and result.reason == "outside_session",
                  "terminal_label_became_unresolved")
            outcomes.append(None)
            unavailable.append((horizon, result.reason or result.status))
    return tuple(outcomes), tuple(unavailable)


def _scores(rows, labels, first, second):
    paired = [row for row in rows if row[3][first] is not None and row[3][second] is not None]
    cells = [defaultdict(list) for _ in _HORIZONS]
    paired_outcomes = 0
    horizon_outcomes = [0, 0, 0]
    for key, _, _, arms in paired:
        known = False
        for horizon, outcome in enumerate(labels[key]):
            if outcome is None:
                continue
            target = ("P1", "P2", "P3", "P4").index(outcome)
            scores = tuple(fsum((probability - int(index == target)) ** 2
                               for index, probability in enumerate(arms[arm][horizon])) for arm in (first, second))
            cells[horizon][_day(key[0]), key[1]].append(scores)
            horizon_outcomes[horizon] += 1
            known = True
        paired_outcomes += int(known)
    daily = []
    for observations in cells:
        symbols = defaultdict(list)
        for (day, _), values in observations.items():
            symbols[day].append(tuple(fsum(value[arm] for value in values) / len(values) for arm in (0, 1)))
        daily.append({day: tuple(fsum(value[arm] for value in values) / len(values) for arm in (0, 1))
                      for day, values in symbols.items()})
    days = sorted({day for horizon in daily for day in horizon})
    first_brier = tuple(fsum(values[0] for values in horizon.values()) / len(horizon) if horizon else None for horizon in daily)
    second_brier = tuple(fsum(values[1] for values in horizon.values()) / len(horizon) if horizon else None for horizon in daily)
    return {"candidates": len(rows), "first_supported": sum(row[3][first] is not None for row in rows),
            "second_supported": sum(row[3][second] is not None for row in rows), "paired_forecasts": len(paired),
            "paired_outcomes": paired_outcomes, "scored_events": len({key for horizon in cells for key in horizon}),
            "scored_dates": len(days), "first_brier": first_brier if days else None,
            "second_brier": second_brier if days else None,
            "daily_difference": tuple((day, tuple(horizon[day][1] - horizon[day][0] if day in horizon else None
                                                  for horizon in daily)) for day in days),
            "horizon_pairs": (len(paired),) * 3, "horizon_outcomes": tuple(horizon_outcomes),
            "horizon_events": tuple(len(horizon) for horizon in cells), "horizon_dates": tuple(len(horizon) for horizon in daily)}


def _same(actual, expected):
    if type(expected) is float:
        _need(type(actual) is float and isfinite(actual)
              and isclose(actual, expected, rel_tol=_REL_TOL, abs_tol=_ABS_TOL), "score_value_differs")
        return abs(actual - expected)
    if type(expected) is tuple:
        _need(type(actual) is tuple and len(actual) == len(expected), "report_tuple_differs")
        return max((_same(left, right) for left, right in zip(actual, expected, strict=True)), default=0.0)
    _need(type(actual) is type(expected) and actual == expected, "report_identity_or_count_differs")
    return 0.0


def verify_forecast_report(forecast, report, read_eval, *, allowed_dates, expected_targets) -> dict:
    """Recompute all 48 groups and every original coordinate before accepting a report."""
    _need(type(report) is ContextForecastReport and callable(read_eval), "typed_report_and_reader_required")
    rows, frames, statuses = _roster(forecast, allowed_dates, expected_targets)
    identities = tuple((stage, context, name) for stage in ("approach", "breakout")
                       for context in _CONTEXTS for name, _, _ in _CONTRASTS)
    _need(type(report.groups) is tuple and all(type(group) is ContextForecastGroup for group in report.groups)
          and tuple((group.stage, group.origin_context, group.contrast) for group in report.groups) == identities,
          "complete_group_roster_differs")
    metadata = {"scenario": forecast.scenario, "fold": forecast.index, "evaluation_dates": allowed_dates,
                "original_coordinates": len(forecast.original_test_coordinates), "original_questions": len(rows),
                "original_frames_by_status": tuple(sorted(frames.items())), "coordinate_status": tuple(sorted(statuses.items()))}
    for key, expected in metadata.items():
        _same(getattr(report, key), expected)
    labels, unavailable = {}, Counter()
    for key, coordinate, feature, arms in rows:
        if not any(arms[first] is not None and arms[second] is not None for _, first, second in _CONTRASTS):
            continue
        labels[key], missing = _outcome(coordinate, feature, read_eval(*key))
        unavailable.update(missing)
    _same(report.unavailable_labels_by_horizon, tuple((*key, count) for key, count in sorted(unavailable.items())))
    maximum = 0.0
    for group, (stage, context, _), (_, first, second) in zip(
            report.groups, identities, _CONTRASTS * 8, strict=True):
        subset = tuple(row for row in rows if row[2].question.stage == stage
                       and (context == "all" or row[2].question.episode_origin_context == context))
        expected = _scores(subset, labels, first, second)
        _need(hasattr(group.counts, "__dataclass_fields__")
              and {field.name for field in fields(group.counts)} == set(expected), "score_fields_differ")
        maximum = max(maximum, *(_same(getattr(group.counts, key), value) for key, value in expected.items()))
    return {"verified": True, "scenario": forecast.scenario, "fold": forecast.index,
            "original_coordinates": len(forecast.original_test_coordinates), "original_questions": len(rows),
            "groups": len(identities), "evaluation_labels_read": len(labels),
            "maximum_absolute_difference": maximum, "evaluation_dates": allowed_dates}
