"""Invented evidence checks for fixed-population EH management summaries."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_management_compare import compare_management
from research.intraday_scenario_management_report import ManagementObservation, summarize_management
from research.intraday_scenario_policy import Values, apply_modeled_open, decide, start_episode
from test_intraday_scenario_management_compare import BOUNDS, SYMBOL, at, early_exit, fresh_fixture, fresh_forecast, row


DAY = "2031-02-03"
NEXT_WEEK = "2031-02-10"


def _replay(*, missing_open=None, fresh=True):
    initial, session, opens, event = fresh_fixture()
    if missing_open is not None:
        del opens[at(missing_open)]
    return compare_management(initial, session, opens, values_at=early_exit, event=event,
                              reentry_at=fresh_forecast if fresh else None)


def _with_symbol(comparison, symbol):
    def rename(episode):
        book = replace(episode.book, lots=tuple(replace(lot, symbol=symbol) for lot in episode.book.lots),
                       realized_exits=tuple(replace(leg, symbol=symbol) for leg in episode.book.realized_exits))
        return replace(episode, symbol=symbol, book=book)

    reentry = comparison.reentry
    if reentry is not None:
        holding = reentry.holding
        reentry = replace(reentry, episode=rename(reentry.episode),
                          holding=replace(holding, episode=rename(holding.episode)) if holding else None)
    return replace(comparison, initial=rename(comparison.initial), fixed=replace(comparison.fixed, episode=rename(comparison.fixed.episode)),
                   dynamic=replace(comparison.dynamic, episode=rename(comparison.dynamic.episode)), reentry=reentry)


def test_each_contrast_uses_its_own_both_known_events_and_exact_totals():
    cases = (ManagementObservation("confirmed", _replay()),
             ManagementObservation("confirmed", _with_symbol(_replay(missing_open=53), "fixed-gap")),
             ManagementObservation("confirmed", _with_symbol(_replay(missing_open=28, fresh=False), "dynamic-gap")))
    calendar = {DAY: (SYMBOL, "fixed-gap", "dynamic-gap"), NEXT_WEEK: ("no-breakout",)}
    rows = dict(zip(((DAY, symbol) for symbol in calendar[DAY]), cases, strict=True))
    rows[NEXT_WEEK, "no-breakout"] = ManagementObservation("no_event")
    report = summarize_management(calendar, rows, cost_bps=33, include_reentry=True)

    assert (report.original_sessions, report.original_symbol_days, report.cost_bps) == (2, 4, 33)
    assert report.initial_status_counts == {"confirmed": 3, "no_event": 1, "no_entry": 0,
                                            "declined": 0, "unconfirmed": 0, "unsupported": 0}
    fixed, dynamic, reentry = report.arms
    assert (fixed.known_outcomes, fixed.unresolved_outcomes, fixed.unsupported_after_entry) == (2, 1, 0)
    assert (dynamic.known_outcomes, dynamic.unresolved_outcomes, dynamic.unsupported_after_entry) == (2, 1, 0)
    assert (reentry.known_outcomes, reentry.unresolved_outcomes, reentry.unsupported_after_entry) == (2, 0, 1)
    assert [arm.weeks[0].confirmed_entries for arm in report.arms] == [3, 3, 5]
    assert [arm.weeks[1].confirmed_entries for arm in report.arms] == [0, 0, 0]
    assert [arm.weeks[1].original_symbol_days for arm in report.arms] == [1, 1, 1]

    management, fresh = report.contrasts
    assert (management.first_arm, management.second_arm, management.both_known_events) == ("fixed", "dynamic", 1)
    assert (fresh.first_arm, fresh.second_arm, fresh.both_known_events) == ("dynamic", "reentry", 2)
    first_cost = Fraction(11 * 600 * 33, 10_000)
    fresh_cost = Fraction(18 * 596 * 33, 10_000)
    assert management.first.net_pnl == -first_cost
    assert management.second.gross_pnl == -110 and management.second.net_pnl == -110 - first_cost
    assert management.second.net_loss == 110 + first_cost and management.second.losing_events == 1
    assert management.second.worst_event_net_pnl == management.second.net_pnl
    assert management.second.mean_event_gross_bps == Fraction(-110 * 10_000, 11 * 600)
    assert management.second.mean_event_net_bps == management.second.mean_event_gross_bps - 33
    assert management.second.mean_entry_basis_exposure < management.first.mean_entry_basis_exposure
    assert fresh.second.net_pnl - fresh.first.net_pnl == 2 * (18 * (610 - 596) - fresh_cost)
    assert fresh.second.gross_turnover > fresh.first.gross_turnover
    assert fresh.second.paid_cost - fresh.first.paid_cost == 2 * fresh_cost
    assert management.by_symbol[0].second_minus_first_net_pnl == -110
    assert management.by_week[1].both_known_events == 0
    assert management.by_week[1].second_minus_first_net_pnl is None


def test_confirmed_fresh_entry_still_counts_if_its_exit_is_unknown():
    observation = ManagementObservation("confirmed", _replay(missing_open=60))
    report = summarize_management({DAY: (SYMBOL,)}, {(DAY, SYMBOL): observation}, cost_bps=33, include_reentry=True)
    fresh = report.arms[2]
    assert fresh.weeks[0].confirmed_initial_entries == fresh.weeks[0].confirmed_fresh_entries == 1
    assert (fresh.known_outcomes, fresh.unresolved_outcomes) == (0, 1)
    assert report.contrasts[1].both_known_events == 0 and report.contrasts[1].second is None


def test_known_fresh_policy_without_a_second_entry_stays_in_the_contrast():
    initial, session, opens, event = fresh_fixture(closing=50)
    comparison = compare_management(initial, session, opens, values_at=early_exit, event=event, reentry_at=fresh_forecast)
    assert comparison.reentry.status == "not_reentered"
    rows = {(DAY, SYMBOL): ManagementObservation("confirmed", comparison)}
    report = summarize_management({DAY: (SYMBOL,)}, rows, cost_bps=33, include_reentry=True)
    fresh = report.contrasts[1]
    assert fresh.both_known_events == 1 and fresh.first == fresh.second
    assert fresh.by_week[0].second_minus_first_net_pnl == 0
    assert report.arms[2].weeks[0].confirmed_fresh_entries == 0


def test_overlapping_confirmed_episodes_count_individually_and_keep_zero_week():
    original = (SYMBOL, *(f"other-symbol-{index}" for index in range(14)))
    rows = {(DAY, symbol): ManagementObservation("confirmed") for symbol in original}
    rows[DAY, SYMBOL] = ManagementObservation("confirmed", _replay(fresh=False))
    rows[NEXT_WEEK, SYMBOL] = ManagementObservation("unconfirmed")
    report = summarize_management({NEXT_WEEK: (SYMBOL,), DAY: original}, rows, cost_bps=33, include_reentry=False)
    assert report.original_symbol_days == 16
    assert report.initial_status_counts["unconfirmed"] == 1
    for arm in report.arms:
        assert [item.week for item in arm.weeks] == [DAY, NEXT_WEEK]
        assert [item.confirmed_entries for item in arm.weeks] == [15, 0]
        assert arm.weeks_at_least_15 == 1 and arm.unsupported_after_entry == 14
    assert report.contrasts[0].both_known_events == 1


def test_weekly_coverage_distinguishes_equal_frequency_with_different_missingness():
    symbols = (SYMBOL, "missing", "known-no-event")
    rows = {
        (DAY, SYMBOL): ManagementObservation("confirmed", _replay(missing_open=53, fresh=False)),
        (DAY, "missing"): ManagementObservation("unsupported"),
        (DAY, "known-no-event"): ManagementObservation("no_event"),
        (NEXT_WEEK, SYMBOL): ManagementObservation("confirmed"),
        (NEXT_WEEK, "missing"): ManagementObservation("unconfirmed"),
        (NEXT_WEEK, "known-no-event"): ManagementObservation("no_event"),
    }
    report = summarize_management({DAY: symbols, NEXT_WEEK: symbols}, rows, cost_bps=33, include_reentry=False)
    fixed, dynamic = report.arms
    earlier, later = fixed.weeks
    assert earlier.original_symbol_days == later.original_symbol_days == 3
    assert earlier.confirmed_entries == later.confirmed_entries == 1
    assert (earlier.unconfirmed_initial_entries, later.unconfirmed_initial_entries) == (0, 1)
    assert (earlier.unsupported_initial_entries, later.unsupported_initial_entries) == (1, 0)
    assert (earlier.unresolved_outcomes, later.unresolved_outcomes) == (1, 0)
    assert (earlier.unsupported_after_entry, later.unsupported_after_entry) == (0, 1)
    assert (earlier.known_outcomes, later.known_outcomes) == (0, 0)
    assert dynamic.weeks[0].known_outcomes == 1 and dynamic.weeks[0].unresolved_outcomes == 0


def test_rejects_missing_coordinate_and_mixed_cost_or_lot_identity():
    calendar = {DAY: (SYMBOL,)}
    comparison = _replay()
    row = {(DAY, SYMBOL): ManagementObservation("confirmed", comparison)}
    with pytest.raises(InputRefusal, match="coordinates_differ"):
        summarize_management(calendar, {}, cost_bps=33, include_reentry=True)
    with pytest.raises(InputRefusal, match="initial_identity_or_cost_differs"):
        summarize_management(calendar, row, cost_bps=43, include_reentry=True)
    arm = replace(comparison.fixed, episode=replace(comparison.fixed.episode, cost_bps=43))
    row[DAY, SYMBOL] = ManagementObservation("confirmed", replace(comparison, fixed=arm))
    with pytest.raises(InputRefusal, match="arm_identity_or_cost_differs"):
        summarize_management(calendar, row, cost_bps=33, include_reentry=True)


def test_stress_cost_uses_its_own_initial_quantity_and_cost():
    _, session, opens, event = fresh_fixture()
    episode = start_episode(cash.new_book("invented-EH-stress"), SYMBOL, at(22), event.expires_at,
                            risk_budget=Fraction(880), cost_bps=43)
    entered = decide(episode, at(22), visible_minute=row(19), bounds=BOUNDS,
                     values=Values(at(22), wait=Fraction(), enter=Fraction(1)))
    initial, fill = apply_modeled_open(entered.episode, at(23), Fraction(600))
    comparison = compare_management(initial, session, opens, values_at=early_exit)
    observations = {(DAY, SYMBOL): ManagementObservation("confirmed", comparison)}
    report = summarize_management({DAY: (SYMBOL,)}, observations, cost_bps=43, include_reentry=False)
    assert report.cost_bps == 43
    assert report.contrasts[0].first.paid_cost == Fraction(600 * fill.quantity * 43, 10_000)
    assert report.contrasts[0].first.mean_event_net_bps == -43
