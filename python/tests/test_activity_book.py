"""Synthetic book-level conservation, immutable rejection and event timing."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from research.activity_accounting import FinalCashDistribution, Lot, PriceBasis, observable_mark
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment


SNAPSHOT = "a" * 64


def day(number: int) -> date:
    return date(2025, 1, number)


def first_lot() -> Lot:
    return Lot("entry-1", "OLD", D(100), D(50), day(5), day(2), day(10), day(2), SNAPSHOT)


def second_lot() -> Lot:
    return replace(first_lot(), lot_id="entry-2", shares=D(40), entered_on=day(3),
                   due_on=day(20), available_on=day(3))


def exchange() -> CompulsoryStockExchange:
    return CompulsoryStockExchange(
        "mandatory-1", D("0.5"),
        PriceBasis("OLD", day(5), D(100), D(50), SNAPSHOT, "synthetic old basis"),
        PriceBasis("NEW", day(9), D(200), D(25), SNAPSHOT, "synthetic new basis"),
        day(6), day(9), "synthetic inspected compulsory stock exchange",
    )


def payment() -> FinalCashPayment:
    return FinalCashPayment(
        "payment-1",
        FinalCashDistribution(
            "OLD", day(5), day(7), day(15), D("2182.35"), exchange().old_basis,
            "synthetic verified actual net payment evidence",
        ),
    )


def book() -> ActivityBook:
    return ActivityBook(D(123), (first_lot(), second_lot()), day(5))


def test_two_old_lots_become_separate_successor_lots_without_cash_or_slot_release() -> None:
    original = book()
    result = original.apply_stock_exchange(exchange(), session=day(6))
    # 100/40 adjusted old * (50/100) * 0.5 / (25/200) = 200/80 new.
    assert tuple(lot.shares for lot in result.lots) == (D(200), D(80))
    assert tuple(lot.mark for lot in result.lots) == (D(25), D(25))
    assert tuple(lot.code for lot in result.lots) == ("NEW", "NEW")
    assert tuple(lot.lot_id for lot in result.lots) == ("entry-1", "entry-2")
    assert tuple(lot.entered_on for lot in result.lots) == (day(2), day(3))
    assert tuple(lot.due_on for lot in result.lots) == (day(10), day(20))
    assert result.cash == original.cash == D(123)
    assert result.slot_count == original.slot_count == 2
    assert result.nav == original.nav == D(7123)
    assert result.as_of == day(6)
    assert result.applied_event_ids == ("mandatory-1",)
    assert original == book() and original.applied_event_ids == ()
    with pytest.raises(ValueError, match="already been applied"):
        result.apply_stock_exchange(exchange(), session=day(9))


def test_future_exchange_does_not_convert_inventory_or_consume_the_event_id() -> None:
    original = replace(book(), as_of=day(4), lots=(replace(first_lot(), mark_date=day(4)),))
    pending = original.apply_stock_exchange(exchange(), session=day(5))
    assert pending.cash == original.cash and pending.lots == original.lots
    assert pending.applied_event_ids == () and pending.as_of == day(5)


def test_unavailable_successor_keeps_carried_value_and_observation_date() -> None:
    converted = book().apply_stock_exchange(exchange(), session=day(6))
    waiting = replace(converted, as_of=day(8), lots=tuple(
        observable_mark(lot, session=day(8), close=D(999), frozen=False)
        for lot in converted.lots
    ))
    assert waiting.lots == converted.lots
    assert waiting.nav == converted.nav == D(7123)
    assert all(lot.mark_date == day(5) and lot.available_on == day(9) for lot in waiting.lots)
    assert observable_mark(waiting.lots[0], session=day(9), close=D(999), frozen=True) == waiting.lots[0]
    observed = observable_mark(waiting.lots[0], session=day(9), close=D(30), frozen=False)
    assert observed.mark_date == day(9) and observed.mark == D(30)
    assert observed.due_on == day(10) and observed.entered_on == day(2)


@pytest.mark.parametrize("price_scale", [D("0.1"), D(8)])
def test_future_price_level_at_constant_unit_factor_cannot_change_early_book(price_scale: D) -> None:
    event = exchange()
    scaled_basis = replace(event.new_basis, raw_close=D(200) * price_scale,
                           adjusted_close=D(25) * price_scale)
    original = book()
    baseline = original.apply_stock_exchange(event, session=day(6))
    scaled = original.apply_stock_exchange(replace(event, new_basis=scaled_basis), session=day(6))
    assert scaled == baseline
    assert original == book()


def test_changed_unit_factor_rebases_claims_but_keeps_economic_value_and_delivery_timing() -> None:
    event = exchange()
    rebased_event = replace(event, new_basis=replace(event.new_basis, raw_close=D(800), adjusted_close=D(200)))
    original = book()
    first_frame = original.apply_stock_exchange(event, session=day(6))
    second_frame = original.apply_stock_exchange(rebased_event, session=day(6))
    assert tuple(lot.shares for lot in first_frame.lots) == (D(200), D(80))
    assert tuple(lot.shares for lot in second_frame.lots) == (D(100), D(40))
    assert tuple(lot.mark for lot in first_frame.lots) == (D(25), D(25))
    assert tuple(lot.mark for lot in second_frame.lots) == (D(50), D(50))
    for converted, unit_factor in ((first_frame, D("0.125")), (second_frame, D("0.25"))):
        # Each old adjusted lot represents 50/20 raw old shares. The mandatory
        # half-share exchange grants exactly 25/10 raw successor shares.
        assert tuple(lot.shares * unit_factor for lot in converted.lots) == (D(25), D(10))
        assert converted.cash == original.cash == D(123)
        assert converted.nav == original.nav == D(7123)
        assert converted.slot_count == original.slot_count == 2
        assert converted.as_of == day(6)
        assert converted.applied_event_ids == (event.event_id,)
        for old, new in zip(original.lots, converted.lots, strict=True):
            assert new.lot_id == old.lot_id and new.code == "NEW"
            assert new.entered_on == old.entered_on and new.due_on == old.due_on
            assert new.mark_date == old.mark_date == day(5)
            assert new.available_on == day(9) and new.dataset_sha256 == old.dataset_sha256
            assert observable_mark(new, session=day(8), close=D(999), frozen=False) == new

    # At delivery, one economic raw close of 320 is 40 or 80 in the two
    # adjusted coordinates. Both books then hold 35 raw shares worth 11,200.
    first_observed = replace(first_frame, as_of=day(9), lots=tuple(
        observable_mark(lot, session=day(9), close=D(40), frozen=False)
        for lot in first_frame.lots
    ))
    second_observed = replace(second_frame, as_of=day(9), lots=tuple(
        observable_mark(lot, session=day(9), close=D(80), frozen=False)
        for lot in second_frame.lots
    ))
    assert first_observed.nav == second_observed.nav == D(11323)
    assert tuple(lot.shares * lot.mark for lot in first_observed.lots) == (D(8000), D(3200))
    assert tuple(lot.shares * lot.mark for lot in second_observed.lots) == (D(8000), D(3200))
    assert all(lot.mark_date == day(9) for lot in first_observed.lots + second_observed.lots)


def test_final_cash_waits_then_removes_all_matching_lots_with_exact_net_credit() -> None:
    other = replace(first_lot(), lot_id="other", code="OTHER")
    original = replace(book(), lots=book().lots + (other,))
    waiting = original.apply_final_cash(payment(), session=day(14))
    assert waiting.cash == D(123) and waiting.lots == original.lots
    assert waiting.slot_count == 3 and waiting.applied_event_ids == ()
    paid = waiting.apply_final_cash(payment(), session=day(15))
    # (100 + 40) adjusted shares * 0.5 raw/adjusted * 2182.35 net.
    assert paid.cash == D("152887.50")
    assert paid.lots == (other,) and paid.slot_count == 1
    assert paid.applied_event_ids == ("payment-1",)
    assert original == replace(book(), lots=book().lots + (other,))
    for session in (day(15), day(16)):
        with pytest.raises(ValueError, match="already been applied"):
            paid.apply_final_cash(payment(), session=session)


def test_verified_zero_cash_still_removes_lots_and_consumes_the_event() -> None:
    event = replace(payment(), distribution=replace(payment().distribution, net_cash_per_raw_share=D(0)))
    result = book().apply_final_cash(event, session=day(15))
    assert result.cash == D(123) and result.lots == ()
    assert result.applied_event_ids == (event.event_id,)


@pytest.mark.parametrize("bad_lot", [
    replace(second_lot(), mark=D(49)),
    replace(second_lot(), dataset_sha256="b" * 64),
    replace(second_lot(), mark_date=day(6)),
    replace(second_lot(), entered_on=day(6), mark_date=day(6), available_on=day(6)),
])
def test_exchange_rejects_everything_atomically_when_a_later_lot_is_invalid(bad_lot: Lot) -> None:
    original = ActivityBook(D(123), (first_lot(), bad_lot), day(6))
    before = (original.cash, original.lots, original.as_of, original.applied_event_ids)
    with pytest.raises(ValueError):
        original.apply_stock_exchange(exchange(), session=day(6))
    assert (original.cash, original.lots, original.as_of, original.applied_event_ids) == before
    assert original.lots[0].code == "OLD"


@pytest.mark.parametrize("bad_lot", [
    replace(second_lot(), dataset_sha256="b" * 64),
    replace(second_lot(), available_on=day(8)),
    replace(second_lot(), mark_date=day(6)),
])
def test_cash_and_inventory_remain_unchanged_when_a_later_payout_lot_is_invalid(bad_lot: Lot) -> None:
    original = ActivityBook(D(123), (first_lot(), bad_lot), day(14))
    with pytest.raises(ValueError):
        original.apply_final_cash(payment(), session=day(15))
    assert original.cash == D(123) and original.lots == (first_lot(), bad_lot)
    assert original.applied_event_ids == () and original.as_of == day(14)


def test_no_holdings_still_consume_a_due_event_and_ids_are_shared_between_types() -> None:
    empty = ActivityBook(D(0), (), day(5))
    consumed = empty.apply_stock_exchange(exchange(), session=day(6))
    assert consumed.cash == D(0) and consumed.lots == ()
    with pytest.raises(ValueError, match="already been applied"):
        consumed.apply_final_cash(replace(payment(), event_id=exchange().event_id), session=day(15))


@pytest.mark.parametrize("cash", [D(-1), D("NaN"), D("Infinity"), D("-Infinity"), 123, 1.0, True])
def test_book_refuses_invalid_cash(cash: object) -> None:
    with pytest.raises(ValueError, match="cash"):
        replace(book(), cash=cash)


@pytest.mark.parametrize("identifier", ["", " ", " padded", "padded ", 1, None])
def test_book_and_events_refuse_bad_ids(identifier: object) -> None:
    with pytest.raises(ValueError, match="lot id"):
        replace(book(), lots=(replace(first_lot(), lot_id=identifier),))
    with pytest.raises(ValueError, match="event id"):
        replace(exchange(), event_id=identifier)
    with pytest.raises(ValueError, match="event id"):
        replace(payment(), event_id=identifier)
    with pytest.raises(ValueError, match="event id"):
        replace(book(), applied_event_ids=(identifier,))


def test_duplicate_lot_ids_event_ids_and_mutable_containers_are_rejected() -> None:
    with pytest.raises(ValueError, match="lot ids must be unique"):
        replace(book(), lots=(first_lot(), first_lot()))
    with pytest.raises(ValueError, match="event ids must be unique"):
        replace(book(), applied_event_ids=("same", "same"))
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(book(), lots=list(book().lots))
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(book(), applied_event_ids=[])
    with pytest.raises(FrozenInstanceError):
        book().cash = D(999)
    with pytest.raises(FrozenInstanceError):
        book().lots[0].shares = D(999)


@pytest.mark.parametrize("ratio", [D(0), D(-1), D("NaN"), D("Infinity"), 0.5, True, None])
def test_exchange_rejects_bad_ratio(ratio: object) -> None:
    with pytest.raises(ValueError, match="ratio"):
        replace(exchange(), ratio=ratio)


@pytest.mark.parametrize("change", [
    {"effective_on": day(5)}, {"available_on": day(5)},
    {"effective_on": datetime(2025, 1, 6)}, {"available_on": "2025-01-09"},
    {"old_basis": None}, {"new_basis": None}, {"source": " "},
])
def test_event_rejects_invalid_dates_missing_basis_or_evidence(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(exchange(), **change)


def test_event_refuses_same_code_mixed_snapshot_and_wrong_availability_anchor() -> None:
    event = exchange()
    for new_basis in (
        replace(event.new_basis, code="OLD"),
        replace(event.new_basis, dataset_sha256="b" * 64),
        replace(event.new_basis, session=day(10)),
    ):
        with pytest.raises(ValueError):
            replace(event, new_basis=new_basis)


@pytest.mark.parametrize("amount", [D(-1), D("NaN"), D("Infinity"), 2182, 2182.35, None])
def test_cash_payment_cannot_wrap_an_unknown_or_invalid_net_amount(amount: object) -> None:
    with pytest.raises(ValueError, match="verified net cash"):
        replace(payment(), distribution=replace(payment().distribution, net_cash_per_raw_share=amount))


def test_book_checks_date_types_at_its_boundary_and_allows_future_availability() -> None:
    waiting = replace(first_lot(), available_on=day(9))
    assert replace(book(), lots=(waiting,)).lots == (waiting,)
    with pytest.raises(ValueError, match="book date must be a date"):
        replace(book(), as_of=datetime(2025, 1, 5))
    datetime_lot = replace(first_lot(), entered_on=datetime(2025, 1, 2),
                           mark_date=datetime(2025, 1, 5), due_on=datetime(2025, 1, 10),
                           available_on=datetime(2025, 1, 2))
    with pytest.raises(ValueError, match="lot mark_date must be a date"):
        replace(book(), lots=(datetime_lot,))
    with pytest.raises(ValueError, match="basis session must be a date"):
        replace(exchange(), old_basis=replace(exchange().old_basis, session=datetime(2025, 1, 5)))


@pytest.mark.parametrize("snapshot", [list(SNAPSHOT), tuple(SNAPSHOT), None, 1, "A" * 64, "a" * 63])
def test_book_and_events_reject_nonstring_or_invalid_snapshot_hashes(snapshot: object) -> None:
    # Isolate the new boundary from upstream validation, including values the
    # old constructors reject themselves. No malformed object reaches a book.
    lot = first_lot()
    old_basis, new_basis = exchange().old_basis, exchange().new_basis
    for upstream in (lot, old_basis, new_basis):
        object.__setattr__(upstream, "dataset_sha256", snapshot)
    with pytest.raises(ValueError, match="SHA-256.*string"):
        replace(book(), lots=(lot,))
    with pytest.raises(ValueError, match="SHA-256.*string"):
        replace(exchange(), old_basis=old_basis)
    with pytest.raises(ValueError, match="SHA-256.*string"):
        replace(exchange(), new_basis=new_basis)
    with pytest.raises(ValueError, match="SHA-256.*string"):
        replace(payment(), distribution=replace(payment().distribution, basis=old_basis))


def test_shared_mutable_snapshot_cannot_enter_a_book_or_event() -> None:
    shared_snapshot = list(SNAPSHOT)
    mutable_lot = replace(first_lot(), dataset_sha256=shared_snapshot)
    old_basis = replace(exchange().old_basis, dataset_sha256=shared_snapshot)
    new_basis = replace(exchange().new_basis, dataset_sha256=shared_snapshot)
    with pytest.raises(ValueError, match="SHA-256.*string"):
        replace(book(), lots=(mutable_lot,))
    with pytest.raises(ValueError, match="SHA-256.*string"):
        replace(exchange(), old_basis=old_basis, new_basis=new_basis)
    with pytest.raises(ValueError, match="SHA-256.*string"):
        replace(payment(), distribution=replace(payment().distribution, basis=old_basis))
    shared_snapshot[0] = "b"
    assert book().lots[0].dataset_sha256 == SNAPSHOT
    assert exchange().old_basis.dataset_sha256 == SNAPSHOT


def test_book_rejects_future_acquisition_marks_and_processing_time_reversal() -> None:
    for lots in ((replace(first_lot(), mark_date=day(6)),),
                 (replace(first_lot(), entered_on=day(6), mark_date=day(6), available_on=day(6)),)):
        with pytest.raises(ValueError, match="book date"):
            replace(book(), lots=lots)
    for event_method, event in ((book().apply_stock_exchange, exchange()), (book().apply_final_cash, payment())):
        with pytest.raises(ValueError, match="precede the book date"):
            event_method(event, session=day(4))
        with pytest.raises(ValueError, match="must be a date"):
            event_method(event, session=datetime(2025, 1, 6))


def test_processed_future_book_cannot_return_to_an_earlier_session() -> None:
    future = book().apply_final_cash(payment(), session=day(14))
    with pytest.raises(ValueError, match="precede the book date"):
        future.apply_stock_exchange(exchange(), session=day(6))
