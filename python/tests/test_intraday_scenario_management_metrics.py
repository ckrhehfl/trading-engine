"""Inventory duration, exact costs and missingness in EH management reports."""

from fractions import Fraction

from research.intraday_scenario_management_compare import compare_management
from research.intraday_scenario_management_metrics import management_metrics
from research.intraday_scenario_policy import Values
from test_intraday_scenario_management_compare import at, early_exit, fixture, fresh_fixture, fresh_forecast


def test_paired_same_entry_separates_cost_pnl_and_exposure():
    initial, session, opens, _ = fixture(opened={28: 590, 33: 610, 53: 605})

    def manager(view):
        return (Values(view.at, hold=Fraction(), reduce_half=Fraction(1), exit=Fraction())
                if view.at == at(27) else Values(view.at, hold=Fraction(), exit=Fraction(1)))

    fixed, dynamic = management_metrics(compare_management(initial, session, opens, values_at=manager))
    denominator = initial.book.initial_capital * 38
    assert fixed.gross_pnl == 55 and dynamic.gross_pnl == 10
    assert fixed.paid_cost == dynamic.paid_cost == Fraction(1089, 50)
    assert fixed.net_pnl == fixed.gross_pnl - fixed.paid_cost
    assert dynamic.net_pnl == dynamic.gross_pnl - dynamic.paid_cost
    assert fixed.entry_basis_exposure == Fraction(600 * 11 * 30, denominator)
    assert dynamic.entry_basis_exposure == Fraction(600 * (11 * 5 + 6 * 5), denominator)


def test_reentry_paid_exposure_and_unresolved_exit_are_distinct():
    initial, session, opens, event = fresh_fixture()
    _, dynamic, reentry = management_metrics(compare_management(initial, session, opens, values_at=early_exit,
                                                                 event=event, reentry_at=fresh_forecast))
    assert dynamic.confirmed_entries == 1 and reentry.confirmed_entries == 2
    assert reentry.paid_cost > dynamic.paid_cost and reentry.gross_turnover > dynamic.gross_turnover
    no_exit = {clock: price for clock, price in opens.items() if clock != at(60)}
    _, _, unresolved = management_metrics(compare_management(initial, session, no_exit, values_at=early_exit,
                                                              event=event, reentry_at=fresh_forecast))
    assert unresolved.confirmed_entries == 2 and unresolved.net_pnl is None
    assert unresolved.entry_basis_exposure is None
