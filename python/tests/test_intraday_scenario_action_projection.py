"""Invented prior-only action comparison with unsupported branch mass."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_scenario_action_projection import evaluate_chart_actions
from research.intraday_scenario_analogue_roster import ChartAnalogueRoster, HistoricalChartDecision
from research.intraday_scenario_decision_inputs import ChartDecisionInputs
from research.intraday_scenario_decision_schedule import DecisionFrame
from research.intraday_scenario_labels import PathQuestion
from research.intraday_scenario_management_case import replay_matched_breakout
from research.intraday_scenario_policy import EntryBounds, start_episode
from research.intraday_scenario_questions import opening_range_questions
from test_intraday_scenario_management_compare import OPEN as POSITION_OPEN, SYMBOL as POSITION_SYMBOL, fixture


START = datetime(2030, 1, 1, tzinfo=UTC)
QUERY = START + timedelta(days=30)
DAY = QUERY.date().isoformat()
AT = QUERY + 22 * MINUTE
CAP = AT + 10 * MINUTE
END = QUERY + 60 * MINUTE


def _chart(day, symbol):
    at = day + 22 * MINUTE
    question = PathQuestion(at, at + 30 * MINUTE, 600, 5, 610, 590)
    row = tuple(map(Fraction, (0, 1, -1, 0)))
    return ChartDecisionInputs((symbol, *question.forecast_id(at)), "ready", (row,) * 20, (row,) * 4,
                               tuple(map(Fraction, (2, 2, 1, 0, 0, 0))))


def _minute(symbol, at):
    return Minute(symbol, at, at + 3 * MINUTE, (Decimal(600),) * 4, None, volume_state="missing")


def run(*, missing_session=False, missing_open=False):
    dates = tuple((START + timedelta(days=offset)).date().isoformat() for offset in range(30))
    fold = ChronologicalFold(1, (0, 30), (30, 31), dates, (DAY,), QUERY)
    roster = ChartAnalogueRoster((HistoricalChartDecision("approach", _chart(START + timedelta(days=offset), "old"))
                                  for offset in range(30)), fold=fold)
    asked = []

    def load(day, symbol):
        assert day in dates and symbol == "old"
        asked.append((day, symbol))
        if missing_session and day == dates[0]:
            return None
        opening = datetime.fromisoformat(day).replace(tzinfo=UTC)
        minutes = {opening + index * MINUTE: _minute(symbol, opening + index * MINUTE)
                   for index in range(60) if not (missing_open and day == dates[0] and index == 23)}
        return SessionInputs(symbol, opening, opening + 60 * MINUTE, minutes)

    position = start_episode(cash.new_book("invented-chart-actions"), "query", AT, END - MINUTE,
                             risk_budget=Fraction(880), cost_bps=33)
    result = evaluate_chart_actions(roster, "approach", _chart(QUERY, "query"), position,
        visible_minute=_minute("query", AT - 3 * MINUTE), query_opening=QUERY, query_closing=END, cap=CAP,
        read_prior_session=load, downside_penalty=Fraction(1, 2), bounds=EntryBounds(Fraction(585), Fraction(605)))
    return result, asked


def test_replay_compares_actions_across_the_same_prior_paths():
    result, asked = run()
    assert result.reason == "ready" and result.selection.neighbors == len(asked) == 30
    assert {row.action for row in result.rollout.outcomes} == {"wait", "enter"}
    assert len(result.rollout.outcomes) == 60 and result.rollout.values.wait == 0
    assert result.rollout.values.enter < 0


def test_unknown_path_does_not_get_replaced_or_disappear():
    missing, requested = run(missing_session=True)
    assert missing.reason == "selected_prior_session_missing" and missing.rollout is None
    assert len(requested) == 1 and missing.selection.neighbors == 30
    unresolved, requested = run(missing_open=True)
    assert unresolved.reason == "unresolved_path_mass" and unresolved.rollout.values is None
    rows = [row for row in unresolved.rollout.outcomes if row.status == "unresolved"]
    assert len(requested) == 30 and len(rows) == 1 and rows[0].weight == Fraction(1, 30)


def test_matched_management_uses_prior_paths_without_replacing_the_initial_trade():
    initial, actual, actual_opens, event = fixture(opened={28: 600, 53: 605})
    days = tuple(POSITION_OPEN - timedelta(days=remaining) for remaining in range(30, 0, -1))
    dates = tuple(day.date().isoformat() for day in days)
    fold = ChronologicalFold(1, (0, 30), (30, 31), dates, (POSITION_OPEN.date().isoformat(),), POSITION_OPEN)

    def at_update(opening, symbol):
        before = _chart(opening, symbol)
        identity = symbol, before.forecast_id[1], opening + 27 * MINUTE
        return ChartDecisionInputs(identity, "ready", before.minutes, before.five_minute_bars,
                                   (*before.state[:2], Fraction(9, 10), *before.state[3:]))

    roster = ChartAnalogueRoster((HistoricalChartDecision("breakout", at_update(day, "prior")) for day in days), fold=fold)

    def load(day, symbol):
        assert day in dates
        opening = days[dates.index(day)]
        prices = {opening + offset * MINUTE: _minute(symbol, opening + offset * MINUTE) for offset in range(61)}
        end_price = opening + 53 * MINUTE
        prices[end_price] = Minute(symbol, end_price, end_price + 3 * MINUTE, (Decimal(590),) * 4,
                                   None, volume_state="missing")
        return SessionInputs(symbol, opening, opening + 61 * MINUTE, prices)

    path, = opening_range_questions(actual, event)
    at = POSITION_OPEN + 27 * MINUTE
    chart = replace(at_update(POSITION_OPEN, POSITION_SYMBOL), forecast_id=(POSITION_SYMBOL, *path.question.forecast_id(at)))
    frame = DecisionFrame(POSITION_SYMBOL, at, event.origin.available_at, event.expires_at, path, chart)
    managed = replay_matched_breakout(initial, actual, actual_opens, event, (frame,), roster,
        read_prior_session=load, downside_penalty=Fraction(1, 2), fresh_reentry=False)
    comparison = managed.comparison
    assert len(managed.decisions) == 1 and managed.decisions[0].reason == "ready"
    assert comparison.initial is initial
    assert comparison.dynamic.episode.closed_at == POSITION_OPEN + 28 * MINUTE
    assert comparison.fixed.episode.closed_at == POSITION_OPEN + 53 * MINUTE
    assert comparison.dynamic.episode.book.realized_gross == 0
    assert comparison.fixed.episode.book.realized_gross == 55
