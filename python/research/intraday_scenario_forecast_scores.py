"""Matched, date-weighted EH forecast scores after prediction has finished."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import fsum

from research.intraday_chart_inputs import _clock, _need
from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_forecast_targets import horizon_probabilities
from research.intraday_scenario_labels import HORIZONS_MINUTES, QuestionId


ForecastKey = tuple[str, str, QuestionId, datetime]
HorizonOutcomes = tuple[str | None, str | None, str | None]
HorizonBrier = tuple[float | None, ...]
ScorePair = tuple[float, float]
_KST = timezone(timedelta(hours=9))
_PATHS = ("P1", "P2", "P3", "P4")


@dataclass(frozen=True)
class ForecastPair:
    key: ForecastKey
    first: tuple[float, ...] | None
    second: tuple[float, ...] | None


@dataclass(frozen=True)
class ForecastScore:
    candidates: int
    first_supported: int
    second_supported: int
    paired_forecasts: int
    paired_outcomes: int
    scored_events: int
    scored_dates: int
    first_brier: HorizonBrier | None
    second_brier: HorizonBrier | None
    daily_difference: tuple[tuple[date, HorizonBrier], ...]
    horizon_pairs: tuple[int, ...]
    horizon_outcomes: tuple[int, ...]
    horizon_events: tuple[int, ...]
    horizon_dates: tuple[int, ...]


def _brier(probabilities: tuple[float, float, float, float], outcome: str) -> float:
    return fsum((prob - (path == outcome)) ** 2 for path, prob in zip(_PATHS, probabilities, strict=True))


def _mean(rows: list[ScorePair]) -> ScorePair:
    return fsum(left for left, _ in rows) / len(rows), fsum(right for _, right in rows) / len(rows)


def matched_forecast_scores(
    predictions: tuple[ForecastPair, ...], observed_outcome: Callable[[ForecastKey], HorizonOutcomes | None],
) -> ForecastScore:
    """Score each still-future horizon with a known outcome on the exact paired rows.

    The immutable input contains precomputed forecasts, with unavailable arms
    explicit. Weight decisions within each event, events within each date and
    dates evenly for each horizon. Scalar scored counts are unions across horizons.
    """
    _need(type(predictions) is tuple and callable(observed_outcome), "fixed_EH_prediction_pairs_required")
    keys = set()
    first_supported = second_supported = 0
    matched = []
    for row in predictions:
        _need(type(row) is ForecastPair and row.key not in keys, "invalid_or_duplicate_EH_forecast_pair")
        keys.add(row.key)
        first = None if row.first is None else horizon_probabilities(row.first)
        second = None if row.second is None else horizon_probabilities(row.second)
        first_supported += first is not None
        second_supported += second is not None
        if first is not None and second is not None:
            matched.append((row.key, first, second))

    outcomes = 0
    horizon_pairs = [0 for _ in HORIZONS_MINUTES]
    horizon_outcomes = [0 for _ in HORIZONS_MINUTES]
    event_scores: tuple[dict[tuple[date, str], list[ScorePair]], ...] = tuple(
        defaultdict(list) for _ in HORIZONS_MINUTES
    )
    for key, first, second in matched:
        symbol, _, question, decision_at = key
        start_at = question[0]
        _clock(start_at)
        _clock(decision_at)
        _need(start_at <= decision_at, "EH_evaluation_forecast_precedes_question")
        pending = tuple(index for index, horizon in enumerate(HORIZONS_MINUTES)
                        if decision_at < start_at + horizon * MINUTE)
        if not pending:
            continue
        for index in pending:
            horizon_pairs[index] += 1
        labels = observed_outcome(key)
        if labels is None:
            continue
        _need(type(labels) is tuple and len(labels) == len(HORIZONS_MINUTES)
              and all(label is None or type(label) is str and label in _PATHS for label in labels),
              "invalid_EH_evaluation_outcome")
        day = start_at.astimezone(_KST).date()
        scored = False
        for index in pending:
            label = labels[index]
            if label is not None:
                event_scores[index][day, symbol].append((_brier(first[index], label), _brier(second[index], label)))
                horizon_outcomes[index] += 1
                scored = True
        outcomes += int(scored)

    daily_means = []
    for events in event_scores:
        by_day: dict[date, list[ScorePair]] = defaultdict(list)
        for (day, _), observations in events.items():
            by_day[day].append(_mean(observations))
        daily_means.append({day: _mean(rows) for day, rows in by_day.items()})
    dates = sorted(set().union(*daily_means))
    differences = tuple((day, tuple(daily[day][1] - daily[day][0] if day in daily else None
                                    for daily in daily_means)) for day in dates)
    means = tuple(_mean(list(daily.values())) if daily else (None, None) for daily in daily_means)
    return ForecastScore(len(predictions), first_supported, second_supported, len(matched), outcomes,
                         len(set().union(*event_scores)), len(dates),
                         tuple(left for left, _ in means) if dates else None,
                         tuple(right for _, right in means) if dates else None, differences,
                         tuple(horizon_pairs), tuple(horizon_outcomes), tuple(map(len, event_scores)),
                         tuple(map(len, daily_means)))
