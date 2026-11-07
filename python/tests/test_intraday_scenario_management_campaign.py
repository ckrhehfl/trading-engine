"""Invented chronological and original-roster checks for EH management."""

from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest

from research.intraday_campaign_readiness import _opening
from research.intraday_ed_inputs import InputRefusal
from research.intraday_ed_sources import TargetCoordinate
from research import intraday_scenario_management_campaign as campaign
from research.intraday_scenario_management_report import ManagementObservation
from research.intraday_scenario_snapshot import project_opening_day
from test_intraday_scenario_state import BELOW, OPENING_EVIDENCE, session
from test_intraday_scenario_management_compare import at, row
from test_intraday_scenario_management_entry import breakout_frame


DATES = tuple((date(2030, 1, 1) + timedelta(days=index)).isoformat() for index in range(257))


def projected(day: str, with_breakout: bool = False, series=None):
    if series is None:
        series = session(BELOW, BELOW, (94, 100, 89, 99), (99, 101, 98, 100), *((100, 101, 99, 100),) * 2,
                         (100, 104, 99, 103), *((103, 104, 102, 103),) * 7, opening=_opening(day), closing=70)
    symbols = (series.symbol, *(f"INVENTED-{rank}" for rank in range(2, 21)))
    targets = tuple(TargetCoordinate("S1", symbol, day, rank, None, "no_stored_rows_unknown_reason", b"test")
                    for rank, symbol in enumerate(symbols, 1))
    sessions = {series.symbol: series} if with_breakout else {}
    return project_opening_day(day, targets, sessions, dict.fromkeys(sessions, OPENING_EVIDENCE), members_known_at=None)


def run(days, read_session, *, held_comparison=False):
    return campaign.run_management_campaign(days, DATES, scenario="S1", read_session=read_session,
        risk_budget=Fraction(100), cost_bps=33, downside_penalty=Fraction(1), fresh_reentry=True,
        held_comparison=held_comparison)


def test_missing_sessions_keep_all_five_folds_and_original_coordinates():
    result = run((projected(day) for day in DATES), lambda *args: pytest.fail(f"unexpected source read {args}"))
    assert result.scenario == "S1" and tuple(day.day for day in result.days) == DATES[100:]
    assert result.report.original_sessions == 157 and result.report.original_symbol_days == 3140
    assert result.report.initial_status_counts["unconfirmed"] == 3140
    assert all(item.confirmed_entries == 0 for arm in result.report.arms for item in arm.weeks)


def test_fold_rebuilds_roster_after_evaluation_boundary(monkeypatch):
    event_days = {DATES[index] for index in (99, 100, 132)}
    matches = []
    source_reads = []

    def evaluated(saved, *, scenario, roster, read_session, **_kwargs):
        if saved.day in event_days:
            frame = next(row.frame for row in saved.rows if row.frame.initial.stage == "breakout")
            matched = roster.match("breakout", frame.chart)
            matches.append(tuple(source[1][0].date().isoformat() for source in matched.source_forecast_ids))
            for symbol, question, _ in matched.source_forecast_ids:
                read_session(question[0].date().isoformat(), symbol)
            with pytest.raises(InputRefusal, match="source_coordinate_outside_scope"):
                read_session(DATES[133], frame.symbol)
            with pytest.raises(InputRefusal, match="source_coordinate_outside_scope"):
                read_session(DATES[99], "UNKNOWN-SYMBOL")
        symbols = tuple(item.symbol for item in sorted(saved.coordinates, key=lambda item: item.targets[0].rank))
        return SimpleNamespace(day=saved.day, scenario=scenario, symbols=symbols,
                               observations={(saved.day, symbol): ManagementObservation("unconfirmed") for symbol in symbols})

    monkeypatch.setattr(campaign, "run_management_day", evaluated)
    result = run((projected(day, day in event_days) for day in DATES), lambda *args: source_reads.append(args))
    assert matches == [(DATES[99],), (DATES[99], DATES[100])]
    assert tuple(day for day, _ in source_reads) == (DATES[99], DATES[99], DATES[100])
    assert result.report.original_symbol_days == 3140


def test_rejects_different_feature_date_before_source_access():
    with pytest.raises(InputRefusal, match="feature_date_differs"):
        run(iter((projected(DATES[1]),)), lambda *args: pytest.fail(f"unexpected source read {args}"))


def test_held_model_compares_an_exit_after_original_target_is_known():
    _, _, template, _ = breakout_frame()
    prices = template.minutes | {at(29): row(29, 600, high=680)} | {
        at(index): row(index, 580) for index in range(36, 65)
    }

    def shifted(day):
        offset = _opening(day) - template.opening
        return replace(template, opening=template.opening + offset, closing=template.closing + offset,
                       minutes={clock + offset: replace(minute, start_at=minute.start_at + offset,
                                available_at=minute.available_at + offset) for clock, minute in prices.items()})

    known = {day: shifted(day) for day in (*DATES[:30], DATES[100])}
    sealed = tuple(projected(day, day in known, series=known.get(day)) for day in DATES)
    first_test = sealed[100]
    original = tuple(item.frame.chart.status for item in first_test.rows if item.frame.initial.stage == "breakout")
    assert original == ("ready", "resolved")

    def provider(day, symbol):
        assert symbol == template.symbol and day in known
        return known[day]

    limited = run(sealed, provider)
    expanded = run(sealed, provider, held_comparison=True)
    assert (limited.management_mode, expanded.management_mode) == ("pending_question", "held")
    old, new = (item.days[0].cases[0].managed for item in (limited, expanded))
    assert old.comparison.initial == new.comparison.initial and old.comparison.fixed == new.comparison.fixed
    assert old.decisions[0].reason == "chart_question_unavailable"
    assert (new.decisions[0].reason, new.decisions[0].prior_dates) == ("ready", 30)
    opening = _opening(DATES[100])
    assert new.comparison.dynamic.episode.closed_at == opening + timedelta(minutes=33)
    assert old.comparison.dynamic.episode.closed_at == opening + timedelta(minutes=58)
    assert new.comparison.dynamic.episode.book.realized_net - old.comparison.dynamic.episode.book.realized_net == 20
