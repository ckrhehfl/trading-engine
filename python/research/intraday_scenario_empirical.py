"""Past-only EH scenario frequencies among nearby observable states."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from math import isfinite
from typing import Literal

from research.intraday_chart_inputs import _clock, _need
from research.intraday_scenario_decision_inputs import ChartDecisionInputs
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome


MAX_NEIGHBORS = 64
MAX_PER_DAY = 2
MIN_NEIGHBOR_DAYS = 30
_KST = timezone(timedelta(hours=9))
Stage = Literal["approach", "breakout"]
Group = tuple[Stage, Fraction, Fraction, Fraction]


@dataclass(frozen=True)
class HistoricalDecision:
    stage: Stage
    chart: ChartDecisionInputs
    outcome: TrainingOutcome | None


@dataclass(frozen=True)
class _Case:
    day: date
    symbol: str
    forecast_at: datetime
    values: tuple[float, ...]
    outcome: int


@dataclass(frozen=True)
class EmpiricalPrediction:
    weights: tuple[float, ...] | None
    neighbors: int
    dates: int


def _state(stage: Stage, chart: ChartDecisionInputs) -> tuple[Group, tuple[float, ...]]:
    _need(stage in ("approach", "breakout") and type(chart) is ChartDecisionInputs and chart.status == "ready"
          and len(chart.minutes) == 20 and len(chart.state) == 6, "ready_EH_empirical_chart_required")
    target, stop, remaining, departed, retested, share_above = chart.state
    _need(type(remaining) is Fraction and 0 < remaining <= 1 and departed in (0, 1)
          and retested in (0, 1) and retested <= departed, "invalid_EH_empirical_progress_state")
    first, latest = chart.minutes[0][0], chart.minutes[-1][-1]
    trailing_range = max(row[1] for row in chart.minutes) - min(row[2] for row in chart.minutes)
    values = tuple(map(float, (latest, target, stop, share_above, trailing_range,
                               latest - first, latest - chart.minutes[-5][0])))
    _need(all(isfinite(value) for value in values), "nonfinite_EH_empirical_distance_feature")
    return (stage, remaining, departed, retested), values


class EmpiricalPathModel:
    """Require mature training labels and keep same-day repeats bounded."""

    def __init__(self, training: Iterable[HistoricalDecision], *, evaluation_starts_at: datetime):
        _clock(evaluation_starts_at)
        self.cutoff_at = evaluation_starts_at
        groups: dict[Group, list[_Case]] = defaultdict(list)
        seen = set()
        for row in training:
            _need(type(row) is HistoricalDecision, "typed_EH_empirical_training_row_required")
            group, values = _state(row.stage, row.chart)
            symbol, question_id, at = row.chart.forecast_id
            _clock(at)
            key = row.stage, row.chart.forecast_id
            day = question_id[0].astimezone(_KST).date()
            _need(at < self.cutoff_at and day < self.cutoff_at.astimezone(_KST).date() and key not in seen,
                  "EH_empirical_training_coordinate_after_split_or_duplicate")
            seen.add(key)
            outcome = row.outcome
            if outcome is None:
                continue
            _need(type(outcome) is TrainingOutcome and type(outcome.index) is int
                  and 0 <= outcome.index < len(OUTCOMES), "invalid_EH_empirical_outcome")
            _clock(outcome.mature_at)
            _need(at < outcome.mature_at, "EH_empirical_outcome_already_visible_at_forecast")
            if outcome.mature_at < self.cutoff_at:
                groups[group].append(_Case(day, symbol, at, values, outcome.index))
        self._groups = {group: tuple(cases) for group, cases in groups.items()}

    def predict(self, stage: Stage, chart: ChartDecisionInputs) -> EmpiricalPrediction:
        group, values = _state(stage, chart)
        _, _, at = chart.forecast_id
        _clock(at)
        _need(at >= self.cutoff_at, "EH_empirical_prediction_precedes_evaluation_split")

        def distance(case: _Case) -> tuple:
            error = sum(abs(left - right) for left, right in zip(case.values, values, strict=True))
            return error, case.day, case.symbol, case.forecast_at

        days: Counter[date] = Counter()
        outcomes = Counter()
        seen = set()
        count = 0
        for case in sorted(self._groups.get(group, ()), key=distance):
            episode = case.day, case.symbol
            if episode in seen or days[case.day] >= MAX_PER_DAY:
                continue
            seen.add(episode)
            days[case.day] += 1
            outcomes[case.outcome] += 1
            count += 1
            if count == MAX_NEIGHBORS:
                break
        weights = None if len(days) < MIN_NEIGHBOR_DAYS else tuple(outcomes[index] / count for index in range(len(OUTCOMES)))
        return EmpiricalPrediction(weights, count, len(days))
