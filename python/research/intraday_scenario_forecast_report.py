"""Stage and decision-clock reporting for sealed EH forecasts."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from math import fsum

from research.intraday_chart_inputs import _need
from research.intraday_scenario_forecast_fold import FoldDecision, FoldForecast
from research.intraday_scenario_forecast_scores import (
    ForecastKey, ForecastPair, ForecastScore, HorizonOutcomes, matched_forecast_scores,
)
from research.intraday_scenario_labels import HORIZONS_MINUTES


CONTRASTS = (("chart_vs_empirical", "empirical", "chart"),
             ("market_vs_matched_chart", "chart_matched", "market_matched"))
_HORIZON_COUNTS = ("horizon_pairs", "horizon_outcomes", "horizon_events", "horizon_dates")


@dataclass(frozen=True)
class ForecastGroup:
    stage: str
    clock: str
    contrast: str
    counts: ForecastScore


@dataclass(frozen=True)
class ForecastReport:
    scenario: str
    fold: int
    original_coordinates: int
    decisions_without_question: int
    groups: tuple[ForecastGroup, ...]


def _clock(decision: FoldDecision) -> str:
    _need(decision.key is not None and decision.key[3] == decision.observed_at
          and decision.key[2][0] <= decision.observed_at, "EH_report_decision_clock_differs")
    return "initial" if decision.key[2][0] == decision.observed_at else "update"


def score_fold(forecast: FoldForecast, read_evaluation_outcome: Callable[[ForecastKey], HorizonOutcomes | None], *,
               allowed_dates: tuple[str, ...]) -> ForecastReport:
    """Close both paired prediction sets before requesting held-aside fold labels."""
    _need(type(forecast) is FoldForecast and callable(read_evaluation_outcome)
          and allowed_dates and allowed_dates == tuple(sorted(set(allowed_dates))), "EH_report_fold_and_dates_required")
    dates = set(allowed_dates)
    _need(all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in dates),
          "EH_report_dates_noncanonical")
    groups = {(stage, clock): [] for stage in ("approach", "breakout") for clock in ("initial", "update")}
    unknown_question = 0
    seen = set()
    for decision in forecast.decisions:
        _need(decision.day in dates and decision.observed_at.date().isoformat() == decision.day
              and decision.stage in ("approach", "breakout"), "EH_report_decision_outside_fold")
        if decision.key is None:
            _need(all(getattr(decision, arm) is None for _, first, second in CONTRASTS for arm in (first, second)),
                  "EH_report_missing_question_has_prediction")
            unknown_question += 1
            continue
        _need(decision.key[:2] == (decision.symbol, decision.stage) and decision.key not in seen,
              "EH_report_duplicate_or_mismatched_question")
        seen.add(decision.key)
        groups[decision.stage, _clock(decision)].append(decision)

    pairs = tuple((stage, clock, contrast, tuple(ForecastPair(decision.key, getattr(decision, first),
                   getattr(decision, second)) for decision in decisions))
                  for (stage, clock), decisions in groups.items() for contrast, first, second in CONTRASTS)
    outcomes = {}

    def observe(key: ForecastKey) -> HorizonOutcomes | None:
        question = key[:3]
        if question not in outcomes:
            outcomes[question] = read_evaluation_outcome(key)
        return outcomes[question]

    scores = tuple(ForecastGroup(stage, clock, contrast, matched_forecast_scores(rows, observe))
                   for stage, clock, contrast, rows in pairs)
    return ForecastReport(forecast.scenario, forecast.index, forecast.original_test_coordinates, unknown_question, scores)


def combine_fold_reports(reports: tuple[ForecastReport, ...]) -> tuple[dict, ...]:
    """Weight each horizon's scored dates equally across nonoverlapping folds."""
    _need(reports and all(item.scenario == reports[0].scenario for item in reports)
          and tuple(item.fold for item in reports) == tuple(range(1, len(reports) + 1)), "EH_report_fold_order_differs")
    combined = []
    for groups in zip(*(item.groups for item in reports), strict=True):
        head = groups[0]
        _need(all((item.stage, item.clock, item.contrast) == (head.stage, head.clock, head.contrast)
                  for item in groups), "EH_report_contrast_order_differs")
        scores = tuple(item.counts for item in groups)
        dates = tuple(day for score in scores for day, _ in score.daily_difference)
        slots = range(len(HORIZONS_MINUTES))
        _need(dates == tuple(sorted(set(dates))) and all(score.scored_dates == len(score.daily_difference)
              and score.horizon_dates == tuple(sum(deltas[index] is not None for _, deltas in score.daily_difference)
                                               for index in slots)
              for score in scores), "EH_report_scored_dates_overlap_or_differ")
        horizon_counts = {name: tuple(sum(getattr(score, name)[index] for score in scores) for index in slots)
                          for name in _HORIZON_COUNTS}

        def weighted(name):
            if not dates:
                return None
            return tuple(fsum(getattr(score, name)[index] * score.horizon_dates[index]
                              for score in scores if score.horizon_dates[index]) / count if count else None
                         for index, count in enumerate(horizon_counts["horizon_dates"]))

        first, second = weighted("first_brier"), weighted("second_brier")
        difference = None if first is None else tuple(right - left if left is not None else None
                                                      for left, right in zip(first, second, strict=True))
        combined.append({"stage": head.stage, "clock": head.clock, "contrast": head.contrast,
                         **{name: sum(getattr(score, name) for score in scores) for name in (
                             "candidates", "first_supported", "second_supported", "paired_forecasts",
                             "paired_outcomes", "scored_events", "scored_dates")},
                         **horizon_counts,
                         "first_brier": first, "second_brier": second,
                         "second_minus_first": difference})
    return tuple(combined)
