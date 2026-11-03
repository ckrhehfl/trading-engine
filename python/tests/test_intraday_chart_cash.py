"""Synthetic EF cash examples with hand-derived basis/cost/cash expectations."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research.intraday_chart_cash import (
    INITIAL_CAPITAL,
    ORDER_FRACTION,
    CashBook,
    Lot,
    RealizedExit,
    enter,
    exit,
    freeze,
    mark,
    new_book,
    reduce_half,
)


def bought(*, price=400_000, cost=33, strategy="invented-arm"):
    return enter(new_book(strategy), "synthetic", entry_price=price, observed_prices={}, cost_bps=cost)


@pytest.mark.parametrize("cost,paid,allocated,remaining", [(33, 6600, 2640, 3960), (43, 8600, 3440, 5160)])
def test_partial_then_full_exit_conserves_cash_basis_and_once_paid_cost(cost, paid, allocated, remaining):
    purchase = bought(cost=cost)
    assert purchase.quantity == 5 and purchase.notional_budget == 2_000_000
    assert purchase.assumed_cost == paid
    assert purchase.book.cash == 98_000_000 - paid
    first = reduce_half(purchase.book, "synthetic", exit_price=450_000)
    assert first.quantity == 2 and first.cash_delta == 900_000 and first.assumed_cost == 0
    assert first.book.cash == 98_900_000 - paid
    lot = first.book.lots[0]
    assert lot.original_quantity == 5 and lot.quantity == 3 and lot.half_reduced
    assert lot.remaining_entry_basis == 1_200_000
    assert lot.remaining_entry_cost == remaining
    record = first.book.realized_exits[0]
    assert record.entry_basis == 800_000 and record.allocated_entry_cost == allocated
    assert record.gross_proceeds == 900_000 and record.gross_pnl == 100_000
    assert record.net_pnl == 100_000 - allocated
    assert record.entry_basis + lot.remaining_entry_basis == 2_000_000
    assert record.allocated_entry_cost + lot.remaining_entry_cost == paid
    final = exit(first.book, "synthetic", quantity=3, exit_price=300_000)
    assert final.reason == "exited" and final.quantity == 3 and final.assumed_cost == 0
    assert final.cash_delta == 900_000 and final.book.lots == ()
    assert final.book.realized_gross == -200_000
    assert final.book.realized_net == -200_000 - paid
    assert final.book.cash == 99_800_000 - paid
    assert sum(item.allocated_entry_cost for item in final.book.realized_exits) == paid
    assert final.book.cash == INITIAL_CAPITAL + final.book.realized_net
    assert purchase.book.lots[0].quantity == 5 and not purchase.book.realized_exits


def test_repeated_arbitrary_reductions_allocate_original_cost_exactly():
    purchase = bought(price=600_000, cost=Fraction(1, 3))
    # Three nominal units; 1,800,000 / 30,000 = 60 once-paid cost.
    assert purchase.quantity == 3 and purchase.assumed_cost == 60
    book = purchase.book
    for price, proceeds, pnl in [(500_000, 500_000, -100_000), (700_000, 700_000, 100_000)]:
        sale = exit(book, "synthetic", quantity=1, exit_price=price)
        assert sale.cash_delta == proceeds and sale.assumed_cost == 0
        assert sale.book.realized_exits[-1].allocated_entry_cost == 20
        assert sale.book.realized_exits[-1].gross_pnl == pnl
        book = sale.book
    assert book.lots[0].remaining_entry_basis == 600_000
    assert book.lots[0].remaining_entry_cost == 20
    assert book.realized_gross == 0 and book.realized_net == -40
    assert book.cash + 600_000 + 20 == 100_000_000 - 40
    final = exit(book, "synthetic", quantity=1, exit_price=600_000)
    assert final.book.cash == 99_999_940 and final.book.realized_net == -60


def test_nonterminating_fraction_allocation_does_not_round_or_depend_on_decimal_context():
    # Cost per nominal unit is 200,000/3 * 1/10,000 = 20/3.
    with localcontext() as context:
        context.prec = 2
        purchase = bought(price=Fraction(200_000, 3), cost=1)
        partial = exit(purchase.book, "synthetic", quantity=7, exit_price=Decimal("70000.1"))
    assert purchase.quantity == 30 and purchase.assumed_cost == 200
    assert partial.book.realized_exits[0].entry_basis == Fraction(1_400_000, 3)
    assert partial.book.realized_exits[0].allocated_entry_cost == Fraction(140, 3)
    assert partial.cash_delta == Fraction(4_900_007, 10)
    assert partial.book.lots[0].remaining_entry_cost == Fraction(460, 3)
    assert partial.book.lots[0].remaining_entry_basis == Fraction(4_600_000, 3)


@pytest.mark.parametrize("price,quantity,half,remaining", [(1_000_000, 2, 1, 1), (600_000, 3, 1, 2), (400_000, 5, 2, 3)])
def test_half_is_floor_of_current_quantity_and_used_once(price, quantity, half, remaining):
    purchase = bought(price=price)
    assert purchase.quantity == quantity
    first = reduce_half(purchase.book, "synthetic", exit_price=price, requested_quantity=half)
    assert first.quantity == half and first.book.lots[0].quantity == remaining
    second = reduce_half(first.book, "synthetic", exit_price=price)
    assert second.reason == "half_reduction_already_used" and second.book is first.book
    assert second.outcome == "declined" and not second.filled


def test_one_remaining_unit_declines_half_but_allows_explicit_full_exit():
    purchase = bought(price=1_500_000)
    first = reduce_half(purchase.book, "synthetic", exit_price=1_400_000)
    assert first.reason == "reduction_below_one_unit" and first.book is purchase.book
    assert not first.book.lots[0].half_reduced
    final = exit(first.book, "synthetic", quantity=1, exit_price=1_400_000)
    assert final.reason == "exited" and final.book.lots == ()
    assert final.book.realized_gross == -100_000


def test_half_uses_remaining_quantity_and_conflicting_request_changes_nothing():
    book = exit(bought().book, "synthetic", quantity=2, exit_price=400_000).book
    conflict = reduce_half(book, "synthetic", exit_price=None, requested_quantity=2)
    assert conflict.reason == "requested_quantity_conflict" and conflict.book is book
    assert not conflict.book.indeterminate
    correct = reduce_half(book, "synthetic", exit_price=400_000, requested_quantity=1)
    assert correct.quantity == 1 and correct.book.lots[0].quantity == 2


def test_half_flag_resets_only_for_a_new_lot_after_full_closure():
    first = reduce_half(bought().book, "synthetic", exit_price=400_000).book
    flat = exit(first, "synthetic", quantity=3, exit_price=400_000).book
    reentry = enter(flat, "synthetic", entry_price=400_000, observed_prices={}, cost_bps=33)
    assert reentry.book.lots[0].lot_id == 2 and not reentry.book.lots[0].half_reduced
    assert reentry.book.realized_exits[0].lot_id == 1
    assert reduce_half(reentry.book, "synthetic", exit_price=400_000).filled


@pytest.mark.parametrize("action", ["partial", "half", "full"])
def test_unknown_sale_is_sticky_and_preserves_last_known_lot_cash_and_ledger(action):
    book = bought().book
    if action == "half":
        missing = reduce_half(book, "synthetic", exit_price=None)
    else:
        missing = exit(book, "synthetic", quantity=1 if action == "partial" else 5, exit_price=None)
    assert missing.outcome == "indeterminate" and missing.reason == "exit_price_unknown"
    assert missing.quantity == 0 and missing.cash_delta == 0
    assert missing.book.lots == book.lots and missing.book.cash == book.cash
    assert missing.book.realized_exits == () and not missing.book.lots[0].half_reduced
    assert missing.book.indeterminate_reasons == ("missing_exit_price:invented-arm:synthetic",)
    for later in (
        exit(missing.book, "synthetic", quantity=1, exit_price=400_000),
        reduce_half(missing.book, "synthetic", exit_price=400_000),
        enter(missing.book, "other", entry_price=1, observed_prices={"synthetic": 400_000}, cost_bps=33),
    ):
        assert later.reason == "account_indeterminate" and later.book is missing.book
        assert later.outcome == "indeterminate" and not later.filled
    observed = mark(missing.book, {"synthetic": 400_000})
    assert observed.observed_nav == 99_993_400 and observed.full_nav is None


def test_unknown_sale_after_partial_keeps_existing_realized_ledger():
    book = reduce_half(bought().book, "synthetic", exit_price=450_000).book
    missing = exit(book, "synthetic", quantity=3, exit_price=None)
    assert missing.book.realized_exits is book.realized_exits
    assert missing.book.realized_gross == 100_000
    assert missing.book.lots[0].remaining_entry_cost == 3960


def test_missing_entry_and_held_marks_never_fabricate_no_fill_or_use_previous_prices():
    empty = new_book("single")
    unknown = enter(empty, "unknown", entry_price=None, observed_prices={}, cost_bps=33)
    assert unknown.reason == "entry_price_unknown" and unknown.outcome == "indeterminate"
    assert unknown.book.cash == empty.cash and unknown.book.lots == ()
    book = bought().book
    assert mark(book, {"synthetic": 500_000}).full_nav == 100_493_400
    missing = enter(book, "new", entry_price=1, observed_prices={}, cost_bps=33)
    assert missing.reason == "equity_unknown" and missing.book.indeterminate
    assert missing.book.cash == book.cash and missing.book.lots == book.lots
    assert missing.book.indeterminate_reasons == ("missing_held_price:invented-arm:synthetic",)
    assert mark(missing.book, {"synthetic": 500_000}).full_nav is None


def test_multisymbol_single_arm_keeps_every_missing_mark_visible():
    book = bought(cost=0).book
    book = enter(book, "other", entry_price=200_000, observed_prices={"synthetic": 400_000}, cost_bps=0).book
    assert book.strategy == "invented-arm" and book.cash == 96_000_000 and len(book.lots) == 2
    for prices in ({"synthetic": 400_000}, {"other": 200_000}, {}):
        valued = mark(book, prices)
        assert valued.observed_nav is None and valued.full_nav is None
        assert len(valued.book.indeterminate_reasons) == 2 - len(prices)
    assert mark(book, {"synthetic": 400_000, "other": 200_000}).full_nav == 100_000_000


def test_nav_ceiling_uses_current_mark_and_never_adds_to_existing_position():
    book = bought().book
    result = enter(book, "other", entry_price=1_000_000, observed_prices={"synthetic": 200_000}, cost_bps=33)
    # 97,993,400 cash + five * 200,000 = 98,993,400 NAV.
    assert ORDER_FRACTION == Fraction(1, 50)
    assert result.notional_budget == 1_979_868 and result.quantity == 1
    duplicate = enter(book, "synthetic", entry_price=400_000, observed_prices={}, cost_bps=33)
    assert duplicate.reason == "duplicate_position" and duplicate.book is book
    oversized = enter(new_book("arm"), "big", entry_price=2_000_001, observed_prices={}, cost_bps=33)
    assert oversized.reason == "one_share_exceeds_nav_limit" and oversized.quantity == 0
    assert oversized.notional_budget == 2_000_000 and not oversized.book.lots


def test_cost_inclusive_cash_limit_cannot_borrow_after_many_symbols():
    book = new_book("single")
    prices = {}
    for index in range(49):
        symbol = f"invented-{index}"
        result = enter(book, symbol, entry_price=100, observed_prices=prices, cost_bps=0)
        assert result.quantity == 20_000
        book = result.book
        prices[symbol] = 100
    last = enter(book, "last", entry_price=100, observed_prices=prices, cost_bps=33)
    assert last.quantity == 19_934 and last.notional_budget == Fraction(20_000_000_000, 10_033)
    assert last.book.cash == Fraction(1089, 50)
    prices["last"] = 100
    refused = enter(last.book, "too-poor", entry_price=100, observed_prices=prices, cost_bps=33)
    assert refused.reason == "insufficient_cash_for_one_share" and refused.book is last.book


def test_excessive_sale_and_absent_position_are_declined_without_unknown_state():
    book = bought().book
    excessive = exit(book, "synthetic", quantity=6, exit_price=None)
    absent = exit(book, "absent", quantity=1, exit_price=None)
    absent_half = reduce_half(book, "absent", exit_price=None)
    assert excessive.reason == "quantity_exceeds_remaining"
    assert absent.reason == absent_half.reason == "position_not_held"
    assert excessive.book is absent.book is absent_half.book is book


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", 0, -1, Fraction(1), Decimal("1"), Decimal("NaN"), Decimal("Infinity")])
def test_quantities_require_exact_positive_int(bad):
    book = bought().book
    with pytest.raises(ValueError):
        exit(book, "synthetic", quantity=bad, exit_price=400_000)
    with pytest.raises(ValueError):
        reduce_half(book, "synthetic", requested_quantity=bad, exit_price=400_000)
    with pytest.raises(ValueError):
        replace(book.lots[0], quantity=bad)


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", 0, -1, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_prices_reject_bool_float_nonfinite_nonpositive_and_text(bad):
    book = bought().book
    with pytest.raises(ValueError):
        enter(new_book("arm"), "new", entry_price=bad, observed_prices={}, cost_bps=33)
    with pytest.raises(ValueError):
        exit(book, "synthetic", quantity=1, exit_price=bad)
    with pytest.raises(ValueError):
        reduce_half(book, "synthetic", exit_price=bad)
    with pytest.raises(ValueError):
        mark(book, {"synthetic": bad})


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", -1, Decimal("NaN"), Decimal("Infinity")])
def test_cost_and_cash_values_reject_inexact_or_invalid_numbers(bad):
    with pytest.raises(ValueError):
        bought(cost=bad)
    with pytest.raises(ValueError):
        replace(new_book("arm"), cash=bad)
    with pytest.raises(ValueError):
        replace(bought().book.lots[0], original_entry_cost=bad)


def test_immutable_books_have_fresh_lot_identity_and_reject_duplicate_positions():
    original = new_book("arm")
    purchase = enter(original, "synthetic", entry_price=400_000, observed_prices={}, cost_bps=33).book
    with pytest.raises(FrozenInstanceError):
        purchase.cash = 0
    with pytest.raises(FrozenInstanceError):
        purchase.lots[0].quantity = 1
    with pytest.raises(ValueError, match="duplicate_position"):
        replace(purchase, lots=purchase.lots + purchase.lots)
    with pytest.raises(ValueError, match="duplicate_lot_id"):
        replace(purchase, lots=purchase.lots + (replace(purchase.lots[0], symbol="other"),))
    with pytest.raises(ValueError, match="next_lot_id_not_new"):
        replace(purchase, next_lot_id=1)
    with pytest.raises(ValueError):
        replace(purchase.lots[0], quantity=6)
    with pytest.raises(ValueError):
        replace(purchase.lots[0], half_reduced=1)
    assert original.cash == INITIAL_CAPITAL and original.lots == () and original.next_lot_id == 1
    frozen = freeze(purchase, "unknown_signal")
    assert freeze(frozen, "unknown_signal") is frozen
    assert freeze(frozen, "other_unknown").indeterminate_reasons == ("unknown_signal", "other_unknown")
    assert not purchase.indeterminate


@pytest.mark.parametrize("bad", ["", " padded", "padded ", None, 1])
def test_schema_neutral_names_still_require_nonempty_unpadded_strings(bad):
    with pytest.raises(ValueError):
        new_book(bad)
    with pytest.raises(ValueError):
        enter(new_book("valid"), bad, entry_price=1, observed_prices={}, cost_bps=0)
    with pytest.raises(ValueError):
        freeze(new_book("valid"), bad)


def test_snapshot_containers_are_copied_and_realized_records_are_immutable():
    lot = Lot(1, "synthetic", 2, 2, Fraction(100), Fraction(1))
    lots = [lot]
    book = CashBook("arm", INITIAL_CAPITAL, Fraction(99_999_799), lots=lots, next_lot_id=2)
    lots.clear()
    assert book.lots == (lot,)
    sold = exit(book, "synthetic", quantity=1, exit_price=110).book
    record = sold.realized_exits[0]
    assert record == RealizedExit(1, "synthetic", 1, Fraction(100), Fraction(1, 2), Fraction(110))
    with pytest.raises(FrozenInstanceError):
        record.quantity = 2
