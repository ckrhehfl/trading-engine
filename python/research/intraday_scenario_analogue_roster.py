"""Select prior EH analogue identities from the chart known at each decision."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from math import isfinite
from typing import Literal, TypeVar

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_chart_inputs import _clock, _need
from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_decision_inputs import ChartDecisionInputs, WINDOW_MINUTES
from research.intraday_scenario_empirical import Group, MAX_NEIGHBORS, MAX_PER_DAY, MIN_NEIGHBOR_DAYS, Stage, _state
from research.intraday_scenario_held_inputs import HeldDecisionInputs
from research.intraday_scenario_labels import QuestionId


_KST = timezone(timedelta(hours=9))
ForecastId = tuple[str, QuestionId, datetime]
HeldCoordinate = tuple[str, str, datetime]
SelectionStatus = Literal["ready", "insufficient_dates", "query_chart_unsupported"]
_Source = TypeVar("_Source")


@dataclass(frozen=True)
class HistoricalChartDecision:
    stage: Stage
    chart: ChartDecisionInputs


@dataclass(frozen=True)
class AnalogueSelection:
    stage: Stage
    query_forecast_id: ForecastId
    source_forecast_ids: tuple[ForecastId, ...]
    dates: int
    candidate_rows: int
    status: SelectionStatus

    @property
    def neighbors(self) -> int:
        return len(self.source_forecast_ids)


@dataclass(frozen=True)
class _Case:
    day: date
    forecast_id: ForecastId
    values: tuple[float, ...]


def _day(stage: Stage, chart: ChartDecisionInputs) -> date:
    _need(stage in ("approach", "breakout") and type(chart) is ChartDecisionInputs
          and chart.status in ("ready", "resolved", "unknown", "unsupported"), "eh_analogue_invalid_chart")
    _need(type(chart.forecast_id) is tuple and len(chart.forecast_id) == 3, "eh_analogue_invalid_forecast_id")
    symbol, question, at = chart.forecast_id
    _need(type(symbol) is str and bool(symbol) and type(question) is tuple and len(question) == 6
          and all(type(value) is Fraction for value in question[1:5]), "eh_analogue_invalid_forecast_id")
    start, end = question[0], question[-1]
    for clock in (start, end, at):
        _clock(clock)
    day = at.astimezone(_KST).date()
    _need(start < end and start <= at and start.astimezone(_KST).date() == day
          and (chart.status != "ready" or at <= end), "eh_analogue_forecast_clock_differs")
    return day


def _bounded_episodes(ranked: Iterable[tuple[date, str, _Source]]) -> tuple[tuple[_Source, ...], int]:
    dates: Counter[date] = Counter()
    episodes = set()
    selected = []
    for day, symbol, source in ranked:
        episode = day, symbol
        if episode in episodes or dates[day] >= MAX_PER_DAY:
            continue
        episodes.add(episode)
        dates[day] += 1
        selected.append(source)
        if len(selected) == MAX_NEIGHBORS:
            break
    return tuple(selected), len(dates)


class ChartAnalogueRoster:
    """Freeze chart-only candidates, then select a bounded roster for one query."""

    def __init__(self, history: Iterable[HistoricalChartDecision], *, fold: ChronologicalFold):
        _need(type(fold) is ChronologicalFold and fold.training_sessions and fold.test_sessions,
              "eh_analogue_requires_chronological_fold")
        self._fold = fold
        self._cutoff = fold.first_test_opening
        _clock(self._cutoff)
        self._first_test_day = self._cutoff.astimezone(_KST).date().isoformat()
        training = frozenset(fold.training_sessions)
        self._test_dates = frozenset(fold.test_sessions)
        _need(self._first_test_day == min(self._test_dates) and max(training) < self._first_test_day,
              "eh_analogue_training_and_test_dates_overlap")

        groups: dict[Group, list[_Case]] = defaultdict(list)
        seen = set()
        for row in history:
            _need(type(row) is HistoricalChartDecision, "eh_analogue_requires_chart_only_history")
            day = _day(row.stage, row.chart)
            identity = row.chart.forecast_id
            key = row.stage, identity[0], identity[2]
            _need(day.isoformat() in training and identity[2] < self._cutoff and key not in seen,
                  "eh_analogue_history_outside_fold_or_duplicate")
            seen.add(key)
            if row.chart.status == "ready":
                group, values = _state(row.stage, row.chart)
                groups[group].append(_Case(day, identity, values))
        self._groups = {group: tuple(cases) for group, cases in groups.items()}

    @property
    def fold(self) -> ChronologicalFold:
        return self._fold

    def match(self, stage: Stage, chart: ChartDecisionInputs) -> AnalogueSelection:
        day = _day(stage, chart)
        identity = chart.forecast_id
        _need(day.isoformat() in self._test_dates and identity[2] >= self._cutoff,
              "eh_analogue_query_outside_fold")
        if chart.status != "ready":
            return AnalogueSelection(stage, identity, (), 0, 0, "query_chart_unsupported")

        group, values = _state(stage, chart)
        cases = self._groups.get(group, ())

        def distance(case: _Case) -> tuple[float, date, str, datetime]:
            error = sum(abs(left - right) for left, right in zip(case.values, values, strict=True))
            return error, case.day, case.forecast_id[0], case.forecast_id[2]

        selected, dates = _bounded_episodes((case.day, case.forecast_id[0], case.forecast_id)
                                           for case in sorted(cases, key=distance))
        status = "ready" if dates >= MIN_NEIGHBOR_DAYS else "insufficient_dates"
        return AnalogueSelection(stage, identity, selected, dates, len(cases), status)


@dataclass(frozen=True)
class HeldAnalogueSelection:
    query_coordinate: HeldCoordinate
    source_coordinates: tuple[HeldCoordinate, ...]
    dates: int
    candidate_rows: int
    status: SelectionStatus

    @property
    def neighbors(self) -> int:
        return len(self.source_coordinates)


@dataclass(frozen=True)
class _HeldCase:
    day: date
    coordinate: HeldCoordinate
    values: tuple[float, ...]


def _held_day(row: HeldDecisionInputs) -> date:
    _need(type(row) is HeldDecisionInputs and type(row.attempt) is int and row.attempt == 1
          and type(row.symbol) is str and bool(row.symbol) and row.status in ("ready", "unknown")
          and type(row.entry_in_bands) is Fraction and type(row.stop_in_bands) is Fraction
          and row.stop_in_bands < row.entry_in_bands, "eh_held_analogue_requires_first_position")
    for clock in (row.entered_at, row.decision_at, row.planning_cap):
        _clock(clock)
    day = row.decision_at.astimezone(_KST).date()
    _need(row.entered_at.astimezone(_KST).date() == row.planning_cap.astimezone(_KST).date() == day
          and type(row.minutes_held) is int and type(row.minutes_remaining) is int
          and 3 <= row.minutes_held and 1 <= row.minutes_remaining and row.minutes_held + row.minutes_remaining <= 30
          and row.decision_at - row.entered_at == row.minutes_held * MINUTE
          and row.planning_cap - row.decision_at == row.minutes_remaining * MINUTE,
          "eh_held_analogue_clock_geometry_differs")
    return day


def _held_state(row: HeldDecisionInputs) -> tuple[tuple[int, int], tuple[float, ...]]:
    _need(type(row.minutes) is tuple and len(row.minutes) == WINDOW_MINUTES
          and all(type(item) is tuple and len(item) == 4 and all(type(price) is Fraction for price in item)
                  for item in row.minutes), "eh_held_analogue_ready_chart_required")
    first, latest = row.minutes[0][0], row.minutes[-1][-1]
    trailing_range = max(item[1] for item in row.minutes) - min(item[2] for item in row.minutes)
    share_above = Fraction(sum(item[-1] > 1 for item in row.minutes), WINDOW_MINUTES)
    values = tuple(map(float, (latest, latest - row.entry_in_bands, latest - row.stop_in_bands, share_above,
                               trailing_range, latest - first, latest - row.minutes[-5][0])))
    _need(all(isfinite(value) for value in values), "eh_held_analogue_nonfinite_distance_feature")
    return (row.minutes_held // 5, row.minutes_remaining // 5), values


class HeldAnalogueRoster:
    """Freeze prior first-position charts, independent of the original question's outcome."""

    def __init__(self, history: Iterable[HeldDecisionInputs], *, fold: ChronologicalFold):
        _need(type(fold) is ChronologicalFold and fold.training_sessions and fold.test_sessions,
              "eh_held_analogue_requires_chronological_fold")
        self._fold = fold
        self._cutoff = fold.first_test_opening
        _clock(self._cutoff)
        training = frozenset(fold.training_sessions)
        self._test_dates = frozenset(fold.test_sessions)
        first_test_day = self._cutoff.astimezone(_KST).date().isoformat()
        _need(first_test_day == min(self._test_dates) and max(training) < first_test_day,
              "eh_held_analogue_training_and_test_dates_overlap")

        groups: dict[tuple[int, int], list[_HeldCase]] = defaultdict(list)
        seen = set()
        for row in history:
            day = _held_day(row)
            coordinate = day.isoformat(), row.symbol, row.decision_at.astimezone(UTC)
            _need(coordinate[0] in training and row.planning_cap < self._cutoff and coordinate not in seen,
                  "eh_held_analogue_history_outside_fold_or_duplicate")
            seen.add(coordinate)
            if row.status == "ready" and row.minutes_remaining > 1:
                group, values = _held_state(row)
                groups[group].append(_HeldCase(day, coordinate, values))
        self._groups = {group: tuple(cases) for group, cases in groups.items()}

    @property
    def fold(self) -> ChronologicalFold:
        return self._fold

    def match(self, query: HeldDecisionInputs) -> HeldAnalogueSelection:
        day = _held_day(query)
        coordinate = day.isoformat(), query.symbol, query.decision_at.astimezone(UTC)
        _need(coordinate[0] in self._test_dates and coordinate[2] >= self._cutoff,
              "eh_held_analogue_query_outside_fold")
        if query.status != "ready" or query.minutes_remaining == 1:
            return HeldAnalogueSelection(coordinate, (), 0, 0, "query_chart_unsupported")

        group, values = _held_state(query)
        cases = self._groups.get(group, ())

        def distance(case: _HeldCase) -> tuple[float, HeldCoordinate]:
            error = sum(abs(left - right) for left, right in zip(case.values, values, strict=True))
            return error, case.coordinate

        selected, dates = _bounded_episodes((case.day, case.coordinate[1], case.coordinate)
                                           for case in sorted(cases, key=distance))
        status = "ready" if dates >= MIN_NEIGHBOR_DAYS else "insufficient_dates"
        return HeldAnalogueSelection(coordinate, selected, dates, len(cases), status)
