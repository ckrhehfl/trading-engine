"""Invented tests of the fixed chart-only EH analogue roster."""

from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_inputs import InputRefusal, MINUTE
from research.intraday_scenario_analogue_roster import ChartAnalogueRoster, HeldAnalogueRoster, HistoricalChartDecision
from research.intraday_scenario_empirical import EmpiricalPathModel, HistoricalDecision
from research.intraday_scenario_held_inputs import HeldDecisionInputs
from test_intraday_scenario_empirical import CUTOFF, START, chart


DAYS = tuple(START + timedelta(days=offset) for offset in range(40))
FOLD = ChronologicalFold(1, (0, 40), (40, 41), tuple(day.date().isoformat() for day in DAYS),
                         (CUTOFF.date().isoformat(),), CUTOFF - 22 * MINUTE)


def history(day, symbol, *, stage="approach", target=5):
    point = chart(day, symbol)
    point = replace(point, state=(Fraction(target), *point.state[1:]))
    return HistoricalChartDecision(stage, point)


def test_distance_ties_are_stable_and_selection_respects_day_symbol_bounds():
    rows = tuple(row for day in DAYS for row in (
        history(day, "C"), history(day + 5 * MINUTE, "A"), history(day, "B"), history(day, "A"),
    ))
    query = chart(CUTOFF, "query")
    selection = ChartAnalogueRoster(rows, fold=FOLD).match("approach", query)
    assert selection.status == "ready" and selection.query_forecast_id == query.forecast_id
    assert (selection.neighbors, selection.dates, selection.candidate_rows) == (64, 32, 160)
    assert selection.source_forecast_ids == tuple(chart(day, symbol).forecast_id
                                                  for day in DAYS[:32] for symbol in ("A", "B"))
    assert ChartAnalogueRoster(reversed(rows), fold=FOLD).match("approach", query) == selection


def test_chart_nearness_precedes_date_and_needs_matching_stage_and_state():
    later_nearest = history(DAYS[-1], "Z")
    other_stage = history(DAYS[0], "Y", stage="breakout")
    wrong_progress = HistoricalChartDecision("approach", chart(DAYS[1], "X", retested=True))
    rows = (history(DAYS[0], "A", target=15), later_nearest, other_stage, wrong_progress)
    roster = ChartAnalogueRoster(rows, fold=FOLD)
    selected = roster.match("approach", chart(CUTOFF, "query"))
    assert selected.source_forecast_ids == (later_nearest.chart.forecast_id, rows[0].chart.forecast_id)
    assert selected.status == "insufficient_dates" and selected.dates == selected.candidate_rows == 2
    assert roster.match("breakout", chart(CUTOFF, "query")).source_forecast_ids == (other_stage.chart.forecast_id,)


def test_missing_training_outcomes_do_not_remove_ready_chart_cases():
    rows = tuple(history(day, "A") for day in DAYS[:30])
    query = chart(CUTOFF, "query")
    labeled_baseline = EmpiricalPathModel((HistoricalDecision(row.stage, row.chart, None) for row in rows),
                                          evaluation_starts_at=FOLD.first_test_opening)
    assert labeled_baseline.predict("approach", query).neighbors == 0
    assert ChartAnalogueRoster(rows, fold=FOLD).match("approach", query).status == "ready"
    unknown = replace(rows[-1].chart, status="unknown", minutes=(), state=())
    roster = ChartAnalogueRoster((*rows[:-1], HistoricalChartDecision("approach", unknown)), fold=FOLD)
    selected = roster.match("approach", query)
    assert selected.status == "insufficient_dates" and selected.neighbors == selected.dates == 29
    assert roster.match("approach", replace(query, status="unknown")).status == "query_chart_unsupported"


def test_selection_refuses_unregistered_history_and_query_dates_before_use():
    actual = history(DAYS[0], "A")
    for invalid_history in ((actual, actual), (history(CUTOFF, "A"),), (history(START - timedelta(days=1), "A"),)):
        with pytest.raises(InputRefusal, match="history_outside_fold_or_duplicate"):
            ChartAnalogueRoster(invalid_history, fold=FOLD)
    roster = ChartAnalogueRoster((actual,), fold=FOLD)
    for invalid_query in (START, CUTOFF + timedelta(days=1), CUTOFF - 23 * MINUTE):
        with pytest.raises(InputRefusal, match="query_outside_fold"):
            roster.match("approach", chart(invalid_query, "query"))


def held(at, symbol, *, age=4, remaining=23, entry=2, stop=-1):
    rows = chart(at, symbol).minutes
    return HeldDecisionInputs(symbol, at, at - age * MINUTE, at + remaining * MINUTE, 1, "ready",
                              Fraction(entry), Fraction(stop), False, Fraction(1), age, remaining,
                              rows, rows[:4])


def test_held_ties_are_stable_and_keep_one_episode_and_two_symbols_per_date():
    rows = tuple(row for day in DAYS for row in (
        held(day, "C"), held(day - MINUTE, "A", age=3, remaining=24), held(day, "B"), held(day, "A"),
    ))
    query = held(CUTOFF, "query")
    selection = HeldAnalogueRoster(rows, fold=FOLD).match(query)
    assert selection.query_coordinate == (CUTOFF.date().isoformat(), "query", CUTOFF)
    assert selection.status == "ready"
    assert (selection.neighbors, selection.dates, selection.candidate_rows) == (64, 32, 160)
    assert selection.source_coordinates == tuple(
        (day.date().isoformat(), symbol, day + offset * MINUTE)
        for day in DAYS[:32] for symbol, offset in (("A", -1), ("B", 0))
    )
    assert HeldAnalogueRoster(reversed(rows), fold=FOLD).match(query) == selection


def test_held_matches_entry_and_stop_distance_within_fixed_time_groups():
    rows = (held(DAYS[0], "A", entry=5), held(DAYS[1], "B", stop=-6), held(DAYS[2], "C"),
            held(DAYS[3], "D", age=9, remaining=18), held(DAYS[4], "E", remaining=18),
            held(DAYS[5], "F", age=24, remaining=1))
    query = replace(held(CUTOFF, "query"), half_reduced=True, remaining_fraction=Fraction(1, 2))
    roster = HeldAnalogueRoster(rows, fold=FOLD)
    selection = roster.match(query)
    assert tuple(symbol for _, symbol, _ in selection.source_coordinates) == ("C", "A", "B")
    assert selection.status == "insufficient_dates" and selection.dates == selection.candidate_rows == 3
    assert roster.match(replace(query, status="unknown", minutes=(), five_minute_bars=())).status == "query_chart_unsupported"
    assert roster.match(held(CUTOFF, "query", age=24, remaining=1)).status == "query_chart_unsupported"


def test_held_roster_refuses_future_duplicate_and_reentry_inputs():
    first = held(START, "A")
    query = held(CUTOFF, "query")
    for rows in ((first, first), (query,), (held(START - timedelta(days=1), "A"),)):
        with pytest.raises(InputRefusal, match="history_outside_fold_or_duplicate"):
            HeldAnalogueRoster(rows, fold=FOLD)
    for invalid in (replace(first, attempt=2), replace(first, planning_cap=first.decision_at - MINUTE)):
        with pytest.raises(InputRefusal, match="first_position|clock_geometry_differs"):
            HeldAnalogueRoster((invalid,), fold=FOLD)
    roster = HeldAnalogueRoster((first,), fold=FOLD)
    with pytest.raises(InputRefusal, match="query_outside_fold"):
        roster.match(first)
    with pytest.raises(InputRefusal, match="first_position"):
        roster.match(replace(query, attempt=2))
