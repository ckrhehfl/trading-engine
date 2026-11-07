"""Invented checks for independent EH report reconciliation."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_management_compare import compare_management
from research.intraday_scenario_management_report import ManagementObservation, summarize_management
from research.intraday_scenario_management_report_audit import verify_management_report
from research.intraday_scenario_policy import Values
from test_intraday_scenario_management_compare import SYMBOL, at, early_exit, fixture, fresh_fixture, fresh_forecast
from test_intraday_scenario_management_report import DAY, NEXT_WEEK, _replay, _with_symbol


@pytest.fixture
def evidence():
    calendar = {DAY: (SYMBOL, "fixed-gap", "fresh-gap", "dynamic-gap"),
                NEXT_WEEK: ("no-breakout", "initial-gap", "initial-unsupported")}
    cases = (_replay(), _with_symbol(_replay(missing_open=53), "fixed-gap"),
             _with_symbol(_replay(missing_open=60), "fresh-gap"),
             _with_symbol(_replay(missing_open=28, fresh=False), "dynamic-gap"))
    rows = {(DAY, symbol): ManagementObservation("confirmed", comparison)
            for symbol, comparison in zip(calendar[DAY], cases, strict=True)}
    rows[NEXT_WEEK, "no-breakout"] = ManagementObservation("no_event")
    rows[NEXT_WEEK, "initial-gap"] = ManagementObservation("unconfirmed")
    rows[NEXT_WEEK, "initial-unsupported"] = ManagementObservation("unsupported")
    report = summarize_management(calendar, rows, cost_bps=33, include_reentry=True)
    return report, calendar, rows


def test_reconciles_both_known_pairs_and_counts_fresh_entry_with_unknown_exit(evidence):
    report, calendar, rows = evidence
    assert [contrast.both_known_events for contrast in report.contrasts] == [2, 2]
    assert report.arms[2].weeks[0].confirmed_fresh_entries == 3
    verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=True)


def test_reconciles_partial_sale_and_independent_exposure():
    initial, session, opens, _ = fixture(opened={28: 598, 53: 610})

    def reduce_once(view):
        return Values(view.at, hold=Fraction(), reduce_half=Fraction(1), exit=Fraction())

    comparison = compare_management(initial, session, opens, values_at=reduce_once)
    calendar = {DAY: (SYMBOL,)}
    rows = {(DAY, SYMBOL): ManagementObservation("confirmed", comparison)}
    report = summarize_management(calendar, rows, cost_bps=33, include_reentry=False)
    left, right = report.contrasts[0].first, report.contrasts[0].second
    assert right.mean_entry_basis_exposure < left.mean_entry_basis_exposure
    assert right.paid_cost == left.paid_cost
    verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=False)


@pytest.mark.parametrize("tamper", ("empty_week", "fees", "pair_count", "symbol_contribution"))
def test_rejects_report_tampering(evidence, tamper):
    report, calendar, rows = evidence
    first_arm, *other_arms = report.arms
    management, fresh = report.contrasts
    if tamper == "empty_week":
        changed = replace(report, arms=(replace(first_arm, weeks=first_arm.weeks[:1]), *other_arms))
        reason = "arm_counts_or_frequency"
    else:
        if tamper == "fees":
            management = replace(management, first=replace(management.first, paid_cost=Fraction()))
        elif tamper == "pair_count":
            management = replace(management, both_known_events=management.both_known_events + 1)
        else:
            contribution, *rest = management.by_symbol
            management = replace(management, by_symbol=(replace(contribution, second_minus_first_net_pnl=Fraction()), *rest))
        changed = replace(report, contrasts=(management, fresh))
        reason = "matched_outcomes"
    with pytest.raises(InputRefusal, match=reason):
        verify_management_report(changed, calendar, rows, cost_bps=33, include_reentry=True)


@pytest.mark.parametrize("field", ("unconfirmed_initial_entries", "unsupported_initial_entries",
                                   "unresolved_outcomes", "unsupported_after_entry"))
def test_rejects_weekly_missingness_shift_even_with_unchanged_global_counts(evidence, field):
    report, calendar, rows = evidence
    fixed, dynamic, reentry = report.arms
    first_week, next_week = reentry.weeks
    first_count, next_count = getattr(first_week, field), getattr(next_week, field)
    assert first_count != next_count
    shifted_weeks = replace(first_week, **{field: next_count}), replace(next_week, **{field: first_count})
    changed = replace(report, arms=(fixed, dynamic, replace(reentry, weeks=shifted_weeks)))

    with pytest.raises(InputRefusal, match="arm_counts_or_frequency"):
        verify_management_report(changed, calendar, rows, cost_bps=33, include_reentry=True)


def test_rejects_ledger_and_report_consistent_with_each_other_but_not_modeled_fill(evidence):
    _, calendar, rows = evidence
    comparison = rows[DAY, SYMBOL].comparison
    fixed = comparison.fixed
    book = fixed.episode.book
    leg, = book.realized_exits
    changed_book = replace(book, cash=book.cash + 1,
                           realized_exits=(replace(leg, gross_proceeds=leg.gross_proceeds + 1),))
    changed = replace(comparison, fixed=replace(fixed, episode=replace(fixed.episode, book=changed_book)))
    rows[DAY, SYMBOL] = ManagementObservation("confirmed", changed)
    report = summarize_management(calendar, rows, cost_bps=33, include_reentry=True)

    with pytest.raises(InputRefusal, match="realized_exits"):
        verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=True)


@pytest.mark.parametrize(("offset", "wrong_price", "reason"),
                         ((23, 599, "initial_open_readback"), (53, 610, "fill_open_readback")))
def test_independent_open_readback_rejects_internally_consistent_wrong_price(offset, wrong_price, reason):
    initial, session, opens, _ = fixture(opened={53: 611})
    comparison = compare_management(initial, session, opens, values_at=None)
    calendar = {DAY: (SYMBOL,)}
    rows = {(DAY, SYMBOL): ManagementObservation("confirmed", comparison)}
    report = summarize_management(calendar, rows, cost_bps=33, include_reentry=False)
    verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=False)
    assert report.contrasts[0].first.gross_pnl == 121

    independent_opens = {at(23): Fraction(600), at(53): Fraction(611)}

    def readback(day, symbol, clock):
        assert (day, symbol) == (DAY, SYMBOL)
        return independent_opens[clock]

    verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=False, modeled_open_at=readback)
    independent_opens[at(offset)] = Fraction(wrong_price)
    with pytest.raises(InputRefusal, match=reason):
        verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=False, modeled_open_at=readback)


@pytest.mark.parametrize(("fresh_price", "status"), ((606, "declined"), (None, "indeterminate")))
def test_independent_open_readback_checks_unmodeled_fresh_fills(fresh_price, status):
    initial, session, opens, event = fresh_fixture(reentry_open=False)
    if fresh_price is not None:
        opens[at(48)] = Fraction(fresh_price)
    comparison = compare_management(initial, session, opens, values_at=early_exit, event=event,
                                    reentry_at=fresh_forecast)
    assert comparison.reentry.events[-1].fill_status == status
    calendar = {DAY: (SYMBOL,)}
    rows = {(DAY, SYMBOL): ManagementObservation("confirmed", comparison)}
    report = summarize_management(calendar, rows, cost_bps=33, include_reentry=True)
    independent_opens = {at(23): Fraction(600), at(28): Fraction(590), at(53): Fraction(600),
                         at(48): None if fresh_price is None else Fraction(fresh_price)}

    def readback(day, symbol, clock):
        assert (day, symbol) == (DAY, SYMBOL)
        return independent_opens[clock]

    verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=True, modeled_open_at=readback)
    independent_opens[at(48)] = Fraction(607)
    with pytest.raises(InputRefusal, match="fill_open_readback"):
        verify_management_report(report, calendar, rows, cost_bps=33, include_reentry=True, modeled_open_at=readback)
