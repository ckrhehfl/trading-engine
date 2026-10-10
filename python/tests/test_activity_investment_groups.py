"""Synthetic original-investment slots and component lifecycle invariants."""

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from research.activity_accounting import FinalCashDistribution, Lot, PriceBasis, observable_mark
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment


SNAPSHOT = "a" * 64


def day(number: int) -> date:
    return date(2025, 1, number)


def original() -> Lot:
    return Lot("entry-1", "OLD", D(100), D(50), day(5), day(2), day(10), day(2), SNAPSHOT)


def component() -> Lot:
    return replace(original(), lot_id="component-1", code="RIGHT", shares=D(20),
                   mark=D(25), available_on=day(20), investment_id="original-1")


def exchange(
    event_id: str, old_code: str, new_code: str, old_day: int, new_day: int,
) -> CompulsoryStockExchange:
    return CompulsoryStockExchange(
        event_id, D("0.5"),
        PriceBasis(old_code, day(old_day), D(100), D(50), SNAPSHOT, "synthetic old basis"),
        PriceBasis(new_code, day(new_day), D(200), D(50), SNAPSHOT, "synthetic new basis"),
        day(old_day + 1), day(new_day), "synthetic compulsory exchange evidence",
    )


def payment(code: str, event_id: str) -> FinalCashPayment:
    return FinalCashPayment(
        event_id,
        FinalCashDistribution(
            code, day(5), day(7), day(15), D(10),
            PriceBasis(code, day(5), D(100), D(50), SNAPSHOT, "synthetic cash basis"),
            "synthetic verified final payment evidence",
        ),
    )


def test_legacy_positional_and_explicit_none_lots_keep_independent_slots() -> None:
    first = original()
    second = replace(first, lot_id="entry-2", investment_id=None)
    book = ActivityBook(D(0), (first, second), day(5))
    assert first.investment_id is None
    assert first.investment_key == ("lot", "entry-1")
    assert second.investment_key == ("lot", "entry-2")
    assert book.slot_count == 2


def test_grouped_components_share_one_slot_and_keep_distinct_dates_and_inventory() -> None:
    retained = replace(original(), investment_id="original-1")
    pending = replace(component(), mark_date=day(4))
    book = ActivityBook(D(123), (retained, pending), day(5))
    assert retained.investment_key == pending.investment_key == ("investment", "original-1")
    assert book.slot_count == 1
    assert len(book.lots) == 2
    assert book.nav == D(5623)
    assert pending.available_on == day(20) and pending.mark_date == day(4)


def test_explicit_group_id_cannot_merge_with_a_same_named_legacy_lot() -> None:
    legacy = original()
    grouped = replace(component(), investment_id=legacy.lot_id)
    distinct_group = replace(component(), lot_id="component-2", investment_id="another")
    book = ActivityBook(D(0), (legacy, grouped, distinct_group), day(5))
    assert book.slot_count == 3
    assert legacy.investment_key != grouped.investment_key


@pytest.mark.parametrize("investment_id", ["", " ", " padded", "padded ", "\t", 1, True, [], {}])
def test_lot_and_book_reject_invalid_explicit_investment_ids(investment_id: object) -> None:
    with pytest.raises(ValueError, match="investment id"):
        replace(original(), investment_id=investment_id)
    malformed = original()
    object.__setattr__(malformed, "investment_id", investment_id)
    with pytest.raises(ValueError, match="investment id"):
        ActivityBook(D(0), (malformed,), day(5))


@pytest.mark.parametrize("change", [
    {"entered_on": day(3), "available_on": day(3)},
    {"due_on": day(11)},
    {"dataset_sha256": "b" * 64},
])
def test_group_cannot_mix_acquisition_due_date_or_snapshot(change: dict[str, object]) -> None:
    retained = replace(original(), investment_id="original-1")
    conflicting = replace(component(), **change)
    for lots in ((retained, conflicting), (conflicting, retained)):
        with pytest.raises(ValueError, match="investment components must share"):
            ActivityBook(D(0), lots, day(5))


def test_removing_one_component_retains_slot_until_last_component_is_removed() -> None:
    retained = replace(original(), investment_id="original-1")
    pending = component()
    book = ActivityBook(D(0), (retained, pending), day(5))
    after_sale = replace(book, lots=(pending,), as_of=day(10))
    assert after_sale.slot_count == book.slot_count == 1
    assert after_sale.lots == (pending,)
    assert replace(after_sale, lots=(), as_of=day(20)).slot_count == 0
    assert book.lots == (retained, pending)


def test_successive_exchanges_and_observable_mark_preserve_original_group() -> None:
    retained = replace(original(), investment_id="original-1")
    pending = component()
    book = ActivityBook(D(0), (retained, pending), day(5))
    first = book.apply_stock_exchange(exchange("exchange-1", "OLD", "NEW", 5, 9), session=day(6))
    marked = observable_mark(first.lots[0], session=day(9), close=D(50), frozen=False)
    observed = replace(first, lots=(marked, pending), as_of=day(9))
    second = observed.apply_stock_exchange(exchange("exchange-2", "NEW", "NEXT", 9, 13), session=day(10))
    assert second.lots[0].code == "NEXT"
    assert second.slot_count == book.slot_count == 1
    assert second.nav == book.nav == D(5500)
    assert second.lots[1] == pending
    for lot in second.lots:
        assert lot.investment_id == "original-1"
        assert lot.entered_on == day(2) and lot.due_on == day(10)
        assert lot.dataset_sha256 == SNAPSHOT


def test_final_cash_removes_only_paid_component_and_retains_original_slot() -> None:
    retained = replace(original(), investment_id="original-1")
    pending = component()
    book = ActivityBook(D(123), (retained, pending), day(5))
    waiting = book.apply_final_cash(payment("OLD", "cash-old"), session=day(14))
    assert waiting.lots == book.lots and waiting.slot_count == 1
    paid = waiting.apply_final_cash(payment("OLD", "cash-old"), session=day(15))
    assert paid.cash == D(623)
    assert paid.lots == (pending,)
    assert paid.lots[0].investment_id == "original-1" and paid.slot_count == 1
    assert book.cash == D(123) and book.lots == (retained, pending)


def test_final_payment_of_last_group_component_releases_slot() -> None:
    retained = replace(original(), investment_id="original-1")
    another = replace(retained, lot_id="entry-2", code="OTHER", investment_id="original-2")
    book = ActivityBook(D(0), (retained, another), day(5))
    paid = book.apply_final_cash(payment("OLD", "cash-old"), session=day(15))
    assert paid.lots == (another,) and paid.slot_count == 1
    settled = paid.apply_final_cash(payment("OTHER", "cash-other"), session=day(15))
    assert settled.cash == D(1000) and settled.lots == () and settled.slot_count == 0
