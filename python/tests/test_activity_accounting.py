"""Hand-computed economic identities, missing evidence and event timing."""

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from research.activity_accounting import (
    FinalCashDistribution, IdentityPeriod, Lot, PriceBasis, common_stock_at,
    observable_mark, settle_final_cash, successor_entitlement,
)


SNAPSHOT = "a" * 64


def d(day):
    return date(2025, 1, day)


def lot():
    return Lot("entry-A", "A", D(100), D(50), d(5), d(2), d(10), d(2), SNAPSHOT)


def exchange_inputs():
    return dict(ratio=D("0.5"),
                old_basis=PriceBasis("A", d(5), D(100), D(50), SNAPSHOT, "old raw quote"),
                new_basis=PriceBasis("B", d(9), D(200), D(50), SNAPSHOT, "new raw quote"),
                effective_on=d(6), available_on=d(9), session=d(6),
                source="completed mandatory exchange")


def test_raw_ratio_is_converted_between_both_adjustment_bases():
    # 100 adjusted A = 50 raw A -> 25 raw B = 100 adjusted B.
    original = lot()
    successor = successor_entitlement(original, **exchange_inputs())
    assert successor.shares == D(100)
    assert successor.code == "B"
    assert successor.shares * successor.mark == original.shares * original.mark == D(5000)
    assert successor.lot_id == original.lot_id
    assert successor.entered_on == original.entered_on
    assert successor.due_on == original.due_on
    assert successor.mark_date == d(5)  # no invented observation on conversion
    assert original.code == "A"  # input is not mutated
    with pytest.raises(ValueError, match="old holding"):
        successor_entitlement(successor, **exchange_inputs())


def test_delivery_blocks_new_mark_and_fractional_entitlements_are_retained():
    inputs = exchange_inputs() | {"ratio": D("0.1832341")}
    successor = successor_entitlement(lot(), **inputs)
    assert successor.shares == D("36.6468200")
    assert successor.shares * successor.mark == pytest.approx(D(5000))
    assert observable_mark(successor, session=d(8), close=D(999), frozen=False) == successor
    assert observable_mark(successor, session=d(9), close=D(999), frozen=True) == successor
    marked = observable_mark(successor, session=d(9), close=D(60), frozen=False)
    assert marked.mark == D(60)
    assert marked.mark_date == d(9)
    with pytest.raises(ValueError, match="backwards"):
        observable_mark(marked, session=d(8), close=D(60), frozen=False)


def test_same_successor_does_not_collapse_acquisition_lots():
    first = successor_entitlement(lot(), **exchange_inputs())
    other = replace(lot(), lot_id="entry-B", code="B", due_on=d(20))
    holdings = {first.lot_id: first, other.lot_id: other}
    assert len(holdings) == 2
    assert {p.due_on for p in holdings.values()} == {d(10), d(20)}


@pytest.mark.parametrize("change", [
    {"ratio": D(0)}, {"ratio": D("NaN")}, {"ratio": D("Infinity")},
    {"ratio": 0.5}, {"available_on": d(4)}, {"effective_on": d(5)},
    {"session": d(5)}, {"source": ""},
])
def test_exchange_refuses_invalid_or_unsupported_inputs(change):
    with pytest.raises(ValueError):
        successor_entitlement(lot(), **(exchange_inputs() | change))


def test_exchange_refuses_mixed_vintages_or_unreconciled_old_mark():
    inputs = exchange_inputs()
    with pytest.raises(ValueError, match="snapshot"):
        successor_entitlement(lot(), **(inputs | {
            "new_basis": replace(inputs["new_basis"], dataset_sha256="b" * 64)}))
    with pytest.raises(ValueError, match="snapshot"):
        successor_entitlement(replace(lot(), dataset_sha256="b" * 64), **inputs)
    with pytest.raises(ValueError, match="reconcile"):
        successor_entitlement(replace(lot(), mark=D(49)), **inputs)
    with pytest.raises(ValueError, match="availability"):
        successor_entitlement(lot(), **(inputs | {"available_on": d(10)}))


def payout():
    return FinalCashDistribution("A", d(5), d(7), d(15), D("2182"),
                                 exchange_inputs()["old_basis"], "actual net payment evidence")


def test_cash_waits_for_verified_payment_and_removes_the_redeemed_lot():
    # 100 adjusted shares = 50 raw shares. No sale tax/fee invented on a
    # compulsory distribution whose amount is already specified net.
    held, cash = settle_final_cash(lot(), payout(), session=d(14))
    assert held == lot() and cash == 0
    held, cash = settle_final_cash(held, payout(), session=d(15))
    assert held is None and cash == D(109100)
    assert settle_final_cash(held, payout(), session=d(16)) == (None, D(0))


def test_unknown_cash_is_not_a_verified_zero_and_eligibility_is_required():
    with pytest.raises(ValueError, match="verified net cash"):
        replace(payout(), net_cash_per_raw_share=None)
    with pytest.raises(ValueError, match="evidence"):
        replace(payout(), payment_source=" ")
    zero = replace(payout(), net_cash_per_raw_share=D(0))
    assert settle_final_cash(lot(), zero, session=d(15)) == (None, D(0))
    with pytest.raises(ValueError, match="eligibility"):
        settle_final_cash(replace(lot(), available_on=d(8)), payout(), session=d(15))
    with pytest.raises(ValueError, match="snapshot"):
        settle_final_cash(replace(lot(), dataset_sha256="b" * 64), payout(), session=d(15))


def test_final_payout_rejects_an_observation_after_the_issues_last_trade():
    inconsistent = observable_mark(lot(), session=d(6), close=D(60), frozen=False)
    with pytest.raises(ValueError, match="eligibility"):
        settle_final_cash(inconsistent, payout(), session=d(15))


def test_historical_spac_conversion_is_not_backdated_from_todays_name():
    periods = (
        IdentityPeriod("A", d(1), d(9), d(1), False, "IPO as SPAC"),
        IdentityPeriod("A", d(9), d(31), d(8), True, "effective operating-company conversion"),
    )
    assert common_stock_at(periods, code="A", session=d(8)) is False
    assert common_stock_at(periods, code="A", session=d(9)) is True
    late_snapshot = (IdentityPeriod("A", d(1), d(31), d(20), True, "current master"),)
    with pytest.raises(ValueError, match="unknown or conflicting"):
        common_stock_at(late_snapshot, code="A", session=d(5))


def test_missing_or_overlapping_identity_cannot_silently_exclude_a_casualty():
    identity = IdentityPeriod("A", d(1), d(31), d(1), True, "IPO evidence")
    for periods in ((), (identity, identity)):
        with pytest.raises(ValueError, match="unknown or conflicting"):
            common_stock_at(periods, code="A", session=d(5))
    # The record needs no future survivor flag or delisting outcome.
    assert common_stock_at((identity,), code="A", session=d(30)) is True


@pytest.mark.parametrize("value", [D(0), D(-1), D("NaN"), D("Infinity"), 1.0])
def test_price_basis_rejects_invalid_units(value):
    with pytest.raises(ValueError):
        PriceBasis("A", d(5), value, D(50), SNAPSHOT, "source")
