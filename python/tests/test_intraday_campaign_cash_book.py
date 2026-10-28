"""Invented-only EC cash arithmetic and missing-state checks."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research.intraday_campaign_cash_book import enter, exit, freeze, mark, new_book


@pytest.mark.parametrize("mode,strategies", [("A", ("A",)), ("B", ("B",)), ("A+B", ("A", "B"))])
def test_initial_capital_and_fixed_allocations(mode, strategies):
    book = new_book(mode)
    assert book.initial_capital == book.cash == 100_000_000
    assert tuple(s.strategy for s in book.sleeves) == strategies
    assert all(s.initial_allocation == s.cash == 100_000_000 // len(strategies) for s in book.sleeves)
    assert mark(book, {}).full_nav == book.initial_capital


@pytest.mark.parametrize("cost_bps", [33, 43])
def test_integer_floor_cash_conservation_and_cost_paid_only_once(cost_bps):
    initial = new_book("A")
    bought = enter(initial, "A", "invented", entry_price=700_000, observed_prices={}, cost_bps=cost_bps)
    assert bought.quantity == 2
    assert bought.notional_budget == 2_000_000
    assert bought.assumed_cost == Fraction(1_400_000 * cost_bps, 10_000)
    assert bought.cash_delta == -1_400_000 - bought.assumed_cost
    assert bought.book.cash + 1_400_000 + bought.assumed_cost == initial.cash
    assert mark(bought.book, {"invented": 700_000}).full_nav == initial.cash - bought.assumed_cost
    sold = exit(bought.book, "A", "invented", exit_price=710_000)
    assert sold.cash_delta == 1_420_000
    assert sold.assumed_cost == 0
    assert sold.book.cash == initial.cash + 20_000 - bought.assumed_cost
    assert sold.book.lots == ()
    assert initial.cash == 100_000_000 and initial.lots == ()
    assert len(bought.book.lots) == 1


def test_joint_orders_use_sleeve_nav_and_profits_do_not_transfer():
    initial = new_book("A+B")
    first = enter(initial, "A", "invented", entry_price=100_000, observed_prices={}, cost_bps=33)
    assert first.quantity == 10
    assert first.notional_budget == 1_000_000
    marked = mark(first.book, {"invented": 150_000})
    assert dict(marked.sleeve_navs) == {"A": 50_496_700, "B": 50_000_000}
    second = enter(first.book, "A", "another", entry_price=1, observed_prices={"invented": 150_000}, cost_bps=33)
    assert second.notional_budget == Fraction(50_496_700, 50)
    sold = exit(second.book, "A", "invented", exit_price=150_000)
    assert sold.book.sleeve("B") == initial.sleeve("B")
    assert sold.book.sleeve("A").initial_allocation == 50_000_000


def test_cost_inclusive_cash_floor_and_no_borrowing_from_other_sleeve():
    book = new_book("A+B")
    quotes = {}
    # Exact zero assumed cost isolates the cash ceiling from entry-cost loss.
    for index in range(49):
        symbol = f"invented-{index}"
        result = enter(book, "A", symbol, entry_price=100, observed_prices=quotes, cost_bps=0)
        assert result.quantity == 10_000
        book = result.book
        quotes[symbol] = 100
    last = enter(book, "A", "last", entry_price=100, observed_prices=quotes, cost_bps=33)
    assert last.quantity == 9967
    assert last.notional_budget == Fraction(1_000_000 * 10_000, 10_033)
    # 1,000,000 - 996,700 - 3,289.11 = 10.89, with no currency rounding.
    assert last.book.sleeve("A").cash == Fraction(1089, 100)
    assert last.book.sleeve("B").cash == 50_000_000
    quotes["last"] = 100
    refused = enter(last.book, "A", "too-poor", entry_price=100, observed_prices=quotes, cost_bps=33)
    assert not refused.filled and refused.reason == "insufficient_cash_for_one_share"
    assert refused.book is last.book
    other = enter(last.book, "B", "b-only", entry_price=100, observed_prices=quotes, cost_bps=33)
    assert other.filled and other.book.sleeve("A") == last.book.sleeve("A")


def test_one_share_above_nav_limit_is_explicit_and_keeps_cash():
    book = new_book("A+B")
    result = enter(book, "A", "expensive", entry_price=1_000_001, observed_prices={}, cost_bps=43)
    assert result.reason == "one_share_exceeds_nav_limit"
    assert not result.filled and result.book is book and result.notional_budget == 1_000_000


def test_same_symbol_cannot_be_held_twice_even_across_strategies():
    book = enter(new_book("A+B"), "A", "shared", entry_price=100, observed_prices={}, cost_bps=33).book
    for strategy in ("A", "B"):
        result = enter(book, strategy, "shared", entry_price=100, observed_prices={"shared": 100}, cost_bps=33)
        assert result.reason == "duplicate_position" and result.book is book
    wrong_owner = exit(book, "B", "shared", exit_price=100)
    assert wrong_owner.reason == "position_not_held" and wrong_owner.book is book
    flat = exit(book, "A", "shared", exit_price=100).book
    assert enter(flat, "B", "shared", entry_price=100, observed_prices={}, cost_bps=33).filled


def test_missing_entry_quote_is_not_filled_or_replaced_with_zero():
    book = new_book("A")
    result = enter(book, "A", "unknown", entry_price=None, observed_prices={}, cost_bps=33)
    assert result.reason == "entry_price_unknown" and not result.filled
    assert result.book.cash == book.cash and result.book.lots == book.lots
    assert result.book.indeterminate_reasons == ("missing_entry_price:A:unknown",)
    later = enter(result.book, "A", "later", entry_price=100, observed_prices={}, cost_bps=33)
    assert later.reason == "account_indeterminate" and not later.filled
    assert later.book is result.book


def test_missing_holding_quote_preserves_partial_known_state_and_freezes_path():
    book = enter(new_book("A+B"), "A", "a", entry_price=100, observed_prices={}, cost_bps=33).book
    book = enter(book, "B", "b", entry_price=200, observed_prices={"a": 100}, cost_bps=33).book
    observed = mark(book, {"a": None, "b": 210})
    assert observed.full_nav is None
    assert dict(observed.sleeve_navs) == {"A": None, "B": 50_046_700}
    assert observed.book.cash == book.cash and observed.book.lots == book.lots
    assert observed.book.indeterminate_reasons == ("missing_held_price:A:a",)
    later = mark(observed.book, {"a": 110, "b": 220})
    assert later.full_nav is None and all(nav is not None for _, nav in later.sleeve_navs)
    declined = enter(later.book, "B", "next-day", entry_price=100, observed_prices={"a": 110, "b": 220}, cost_bps=33)
    assert declined.reason == "account_indeterminate" and not declined.filled
    assert declined.book.lots == book.lots


def test_prior_mark_is_not_used_as_fallback_and_equity_unknown_blocks_entry():
    book = enter(new_book("A"), "A", "held", entry_price=100, observed_prices={}, cost_bps=33).book
    known = mark(book, {"held": 120})
    assert known.full_nav is not None
    missing = enter(known.book, "A", "new", entry_price=100, observed_prices={}, cost_bps=33)
    assert missing.reason == "equity_unknown" and missing.book.indeterminate
    assert missing.book.cash == book.cash and missing.book.lots == book.lots


def test_missing_exit_keeps_inventory_and_later_known_exit_cannot_reset_path():
    book = enter(new_book("A"), "A", "held", entry_price=100, observed_prices={}, cost_bps=33).book
    missing = exit(book, "A", "held", exit_price=None)
    assert missing.reason == "exit_price_unknown" and not missing.filled
    assert missing.book.lots == book.lots and missing.book.cash == book.cash
    assert missing.book.indeterminate_reasons == ("missing_exit_price:A:held",)
    sold = exit(missing.book, "A", "held", exit_price=100)
    assert sold.filled and not sold.book.lots and sold.book.indeterminate
    assert mark(sold.book, {}).full_nav is None


def test_exact_decimal_arithmetic_is_independent_of_decimal_context():
    with localcontext() as context:
        context.prec = 2
        result = enter(new_book("A"), "A", "fractional", entry_price=Decimal("700000.1"), observed_prices={}, cost_bps=Decimal("33"))
    assert result.quantity == 2
    assert result.assumed_cost == Fraction(231_000_033, 50_000)
    assert result.cash_delta == -(2 * Fraction(7_000_001, 10) + result.assumed_cost)


@pytest.mark.parametrize("bad", [1.0, True, "100", 0, -1, Decimal("NaN"), Decimal("Infinity")])
def test_invalid_entry_prices_are_rejected(bad):
    with pytest.raises(ValueError):
        enter(new_book("A"), "A", "invented", entry_price=bad, observed_prices={}, cost_bps=33)


@pytest.mark.parametrize("bad", [33.0, True, "33", -1, Decimal("NaN")])
def test_invalid_cost_assumptions_are_rejected(bad):
    with pytest.raises(ValueError):
        enter(new_book("A"), "A", "invented", entry_price=100, observed_prices={}, cost_bps=bad)


def test_invalid_held_and_exit_prices_do_not_mutate_book():
    book = enter(new_book("A"), "A", "held", entry_price=100, observed_prices={}, cost_bps=33).book
    for bad in (0, -1, 100.0, True):
        with pytest.raises(ValueError):
            mark(book, {"held": bad})
        with pytest.raises(ValueError):
            exit(book, "A", "held", exit_price=bad)
    assert not book.indeterminate and len(book.lots) == 1


@pytest.mark.parametrize("missing", ["first", "second"])
def test_a_known_lot_never_masks_an_unknown_lot_in_the_same_sleeve(missing):
    book = enter(new_book("A"), "A", "first", entry_price=100, observed_prices={}, cost_bps=33).book
    book = enter(book, "A", "second", entry_price=100, observed_prices={"first": 100}, cost_bps=33).book
    quotes = {"first": 100, "second": 100}
    del quotes[missing]
    valued = mark(book, quotes)
    assert valued.full_nav is None and valued.sleeve_navs == (("A", None),)
    assert valued.book.lots == book.lots and valued.book.cash == book.cash


def test_immutable_state_and_explicit_freeze_reasons():
    original = new_book("A")
    book = enter(original, "A", "held", entry_price=100, observed_prices={}, cost_bps=33).book
    with pytest.raises(FrozenInstanceError):
        book.initial_capital = Fraction(1)
    with pytest.raises(FrozenInstanceError):
        book.sleeves[0].cash = Fraction(0)
    with pytest.raises(FrozenInstanceError):
        book.lots[0].quantity = 1
    frozen = freeze(book, "signal_state_unknown")
    assert freeze(frozen, "signal_state_unknown") is frozen
    assert freeze(frozen, "session_incomplete").indeterminate_reasons == ("signal_state_unknown", "session_incomplete")
    assert not original.indeterminate and not book.indeterminate
    with pytest.raises(ValueError):
        replace(book.sleeves[0], cash=-1)
    with pytest.raises(ValueError):
        replace(book.lots[0], quantity=Fraction(1, 2))
    with pytest.raises(ValueError):
        replace(book, lots=book.lots + book.lots)
    with pytest.raises(ValueError):
        freeze(book, "")


def test_unknown_modes_and_unallocated_sleeves_are_rejected():
    with pytest.raises(ValueError):
        new_book("C")
    with pytest.raises(ValueError):
        enter(new_book("A"), "B", "invented", entry_price=100, observed_prices={}, cost_bps=33)
