"""Reconstruct EH fold scores from frozen forecasts and scoped outcome access."""

from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta, timezone
from math import fsum, isclose
from statistics import fmean

from research.intraday_chart_inputs import _clock, _need
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_fold import FoldForecast
from research.intraday_scenario_forecast_report import ForecastGroup, ForecastReport
from research.intraday_scenario_forecast_scores import ForecastKey, ForecastScore, HorizonOutcomes


_KST = timezone(timedelta(hours=9))
_CONTRASTS = (("chart_vs_empirical", 0, 1), ("market_vs_matched_chart", 2, 3))
_BUCKETS = ((1, 2, 3, 0, 0, 0, 0, 0, 0, 0),
            (1, 2, 3, 1, 2, 3, 0, 0, 0, 0),
            (1, 2, 3, 1, 2, 3, 1, 2, 3, 0))
_DEADLINES = tuple(timedelta(minutes=minutes) for minutes in (5, 15, 30))
_TRUTH = {"P4": 0, "P1": 1, "P2": 2, "P3": 3}
_COUNT_FIELDS = ("candidates", "first_supported", "second_supported", "paired_forecasts",
                 "paired_outcomes", "scored_events", "scored_dates")
_HORIZON_COUNT_FIELDS = ("horizon_pairs", "horizon_outcomes", "horizon_events", "horizon_dates")


def _brier(weights: tuple[float, ...], actual: str, horizon: int) -> float:
    mass = fsum(weights)
    probabilities = [0.0] * 4
    for bucket, probability in zip(_BUCKETS[horizon], weights, strict=True):
        probabilities[bucket] += probability / mass
    truth = _TRUTH[actual]
    return fsum((value - int(bucket == truth)) ** 2 for bucket, value in enumerate(probabilities))


def _mean(rows):
    return tuple(map(fmean, zip(*rows, strict=True)))


def _close(actual, expected) -> bool:
    return (type(actual) is tuple and len(actual) == len(expected)
            and all(left is None if right is None else type(left) in (int, float)
                    and isclose(left, right, abs_tol=1e-12, rel_tol=0)
                    for left, right in zip(actual, expected, strict=True)))


def _check_group(scores, rows, first, second, outcome) -> None:
    events = tuple(defaultdict(list) for _ in _DEADLINES)
    live_pairs = [0] * len(events)
    resolved_pairs = [0] * len(events)
    supported_first = supported_second = paired = scored = 0
    for key, day, arms in rows:
        left, right = arms[first], arms[second]
        supported_first += left is not None
        supported_second += right is not None
        if left is None or right is None:
            continue
        paired += 1
        _, _, question, at = key
        live = tuple(index for index, deadline in enumerate(_DEADLINES) if at < question[0] + deadline)
        if not live:
            continue
        labels = outcome(key)
        resolved = False
        for index in live:
            live_pairs[index] += 1
            if labels is None or labels[index] is None:
                continue
            resolved = True
            resolved_pairs[index] += 1
            label = labels[index]
            events[index][day, key[0]].append((_brier(left, label, index), _brier(right, label, index)))
        scored += resolved

    daily = []
    for horizon_events in events:
        sessions = defaultdict(list)
        for (day, _), decisions in horizon_events.items():
            sessions[day].append(_mean(decisions))
        daily.append({day: _mean(event_means) for day, event_means in sessions.items()})
    all_days = sorted(set().union(*daily))
    all_events = set().union(*events)
    expected_counts = (len(rows), supported_first, supported_second, paired, scored, len(all_events), len(all_days))
    _need(type(scores) is ForecastScore and all(
        type(getattr(scores, field)) is int and getattr(scores, field) == expected
        for field, expected in zip(_COUNT_FIELDS, expected_counts, strict=True)), "EH_reference_score_counts_differ")
    expected_horizon_counts = (tuple(live_pairs), tuple(resolved_pairs), tuple(map(len, events)), tuple(map(len, daily)))
    for field, expected in zip(_HORIZON_COUNT_FIELDS, expected_horizon_counts, strict=True):
        actual = getattr(scores, field)
        _need(type(actual) is tuple and all(type(value) is int for value in actual) and actual == expected,
              "EH_reference_score_horizon_counts_differ")
    _need(type(scores.daily_difference) is tuple and len(scores.daily_difference) == len(all_days),
          "EH_reference_score_daily_roster_differs")
    for actual, day in zip(scores.daily_difference, all_days, strict=True):
        _need(type(actual) is tuple and len(actual) == 2, "EH_reference_score_daily_roster_differs")
        actual_day, difference = actual
        expected = tuple(session[day][1] - session[day][0] if day in session else None for session in daily)
        _need(type(actual_day) is date and actual_day == day and _close(difference, expected),
              "EH_reference_score_daily_values_differ")
    if all_days:
        pooled = tuple(_mean(list(session.values())) if session else (None, None) for session in daily)
        first_means, second_means = zip(*pooled, strict=True)
        _need(_close(scores.first_brier, first_means) and _close(scores.second_brier, second_means),
              "EH_reference_score_brier_differs")
    else:
        _need(scores.first_brier is None and scores.second_brier is None, "EH_reference_score_unobserved_brier")


def verify_forecast_report(forecast: FoldForecast, report: ForecastReport,
                           read_evaluation_outcome: Callable[[ForecastKey], HorizonOutcomes | None], *,
                           allowed_dates: tuple[str, ...]) -> None:
    """Check date, event and paired-row weights without calling the score producer."""
    _need(type(forecast) is FoldForecast and type(report) is ForecastReport and callable(read_evaluation_outcome)
          and type(allowed_dates) is tuple and bool(allowed_dates) and all(type(day) is str for day in allowed_dates)
          and tuple(sorted(set(allowed_dates))) == allowed_dates, "EH_reference_score_fold_required")
    try:
        dates = {date.fromisoformat(day) for day in allowed_dates}
    except ValueError as error:
        raise InputRefusal("EH_reference_score_dates_noncanonical") from error
    _need(tuple(day.isoformat() for day in sorted(dates)) == allowed_dates, "EH_reference_score_dates_noncanonical")
    _need(forecast.scenario in ("S1", "S2") and forecast.scenario == report.scenario
          and type(report.fold) is type(forecast.index) is int and 1 <= forecast.index <= 5 and report.fold == forecast.index
          and type(report.original_coordinates) is type(forecast.original_test_coordinates) is int
          and 0 <= report.original_coordinates == forecast.original_test_coordinates,
          "EH_reference_score_fold_identity_differs")

    roster = {(stage, clock): [] for stage in ("approach", "breakout") for clock in ("initial", "update")}
    keys, missing_questions = set(), 0
    for decision in forecast.decisions:
        _clock(decision.observed_at)
        day = decision.observed_at.astimezone(_KST).date()
        _need(day in dates and day.isoformat() == decision.day and decision.stage in ("approach", "breakout"),
              "EH_reference_score_decision_outside_fold")
        arms = (decision.empirical, decision.chart, decision.chart_matched, decision.market_matched)
        key = decision.key
        if key is None:
            _need(all(value is None for value in arms), "EH_reference_score_missing_question_has_prediction")
            missing_questions += 1
            continue
        _need(type(key) is tuple and len(key) == 4, "EH_reference_score_question_identity_differs")
        symbol, stage, question, at = key
        _need(type(question) is tuple and len(question) == 6, "EH_reference_score_question_identity_differs")
        _clock(at)
        _clock(question[0])
        _need((symbol, stage, at) == (decision.symbol, decision.stage, decision.observed_at)
              and question[0] <= at and question[0].astimezone(_KST).date() == day and key not in keys,
              "EH_reference_score_question_identity_differs")
        keys.add(key)
        for value in arms:
            _need(value is None or (type(value) is tuple and len(value) == 10
                  and all(type(weight) in (int, float) and 0 <= weight <= 1 for weight in value)
                  and isclose(fsum(value), 1, abs_tol=1e-9, rel_tol=0)), "EH_reference_score_probability_invalid")
        clock = "initial" if question[0] == at else "update"
        roster[stage, clock].append((key, day, arms))

    groups = tuple((stage, clock, contrast, rows, first, second) for (stage, clock), rows in roster.items()
                   for contrast, first, second in _CONTRASTS)
    _need(type(report.decisions_without_question) is int and report.decisions_without_question == missing_questions
          and type(report.groups) is tuple and len(report.groups) == len(groups), "EH_reference_score_roster_differs")
    for actual, (stage, clock, contrast, _, _, _) in zip(report.groups, groups, strict=True):
        _need(type(actual) is ForecastGroup and (actual.stage, actual.clock, actual.contrast) == (stage, clock, contrast),
              "EH_reference_score_group_identity_differs")

    labels = {}

    def outcome(key: ForecastKey) -> HorizonOutcomes | None:
        identity = key[:3]
        if identity not in labels:
            value = read_evaluation_outcome(key)
            _need(value is None or (type(value) is tuple and len(value) == len(_DEADLINES)
                  and all(item is None or type(item) is str and item in _TRUTH for item in value)),
                  "EH_reference_score_outcome_invalid")
            labels[identity] = value
        return labels[identity]

    for actual, (_, _, _, rows, first, second) in zip(report.groups, groups, strict=True):
        _check_group(actual.counts, rows, first, second, outcome)
