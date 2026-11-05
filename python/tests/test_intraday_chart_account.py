"""Invented EF account evidence with independently calculated expectations."""
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_chart_account as account
from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_pattern_calibration import session_open


DAY = "2025-06-02"
NEXT = "2025-06-03"
LATER = "2025-06-04"
A, B, C = "KRX:000001", "KRX:000002", "KRX:000003"


def replay(rows, *, prices=None, scores=None, days=(DAY,)):
    quotes, forecasts = [], []

    def quote(key):
        quotes.append(key)
        return (prices or {}).get(key, 400_000)

    def gross15(key):
        forecasts.append(key)
        return (scores or {}).get(key, 1.0)

    return account.replay_accounts(days, rows, quote_at=quote, gross15_at=gross15), quotes, forecasts


def test_exact_partial_then_full_sale_and_later_reentry():
    rows = [((DAY, A, 105), 0.4), ((DAY, B, 75), 0.33), ((DAY, A, 60), 0.4)]
    books, quotes, forecasts = replay(
        rows,
        prices={(DAY, A, 65): 450_000, (DAY, A, 70): 500_000, (DAY, A, 90): 425_000, (DAY, A, 105): 425_000},
        scores={(DAY, A, 65): 0.0, (DAY, A, 70): -1.0},
    )
    assert tuple((book.cost_bps, book.policy) for book in books) == (
        (33, "fixed"), (33, "dynamic"), (43, "fixed"), (43, "dynamic"),
    )
    assert forecasts == [(DAY, A, offset) for offset in (65, 70, 110, 115, 120)]
    assert quotes == [(DAY, A, offset) for offset in (*range(60, 91, 5), *range(105, 136, 5))]
    # Both arms buy 5 units initially and 4 after the original fixed exit.
    # The second fixed sale is worth 1,600,000 at the default OPEN.
    for fixed, dynamic in zip(books[::2], books[1::2], strict=True):
        cost = 3_700_000 * Fraction(fixed.cost_bps, 10_000)
        for book, proceeds in ((fixed, 3_725_000), (dynamic, 4_000_000)):
            assert book.days[0].common_rows == 3 and book.days[0].eligible_entries == 2
            assert book.entry_counts == (("entered", 2),)
            assert book.modeled_bought_units == book.modeled_sold_units == 9
            assert book.modeled_entry_basis == book.modeled_sold_basis == 3_700_000
            assert book.modeled_paid_cost == book.modeled_allocated_cost == cost
            assert book.modeled_sale_proceeds == proceeds
            assert book.days[0].closing_cash == 100_000_000 + proceeds - 3_700_000 - cost
            assert book.continuous_return == (proceeds - 3_700_000 - cost) / 100_000_000
        assert fixed.sale_counts == (("fixed_cap", 2),)
        assert dynamic.sale_counts == (("consecutive_nonpositive", 1), ("first_nonpositive", 1), ("fixed_cap", 1))


def test_valuation_gap_blocks_new_entries_but_preserves_known_sales_and_cash():
    books, quotes, forecasts = replay(
        [((DAY, A, 60), 1.0), ((DAY, B, 90), 1.0), ((NEXT, C, 60), 1.0)],
        prices={(DAY, A, 80): None}, days=(DAY, NEXT),
    )
    assert quotes == [(DAY, A, offset) for offset in range(60, 91, 5)]
    assert forecasts == [(DAY, A, offset) for offset in (65, 70, 75)]
    for book in books:
        assert book.unknown_at == session_open(DAY) + 87 * MINUTE
        assert len(book.unknown_reasons) == 1 and "missing_held_price:" in book.unknown_reasons[0]
        assert not book.inventory_uncertain and book.last_known_lots == ()
        assert book.sale_counts == (("fixed_cap", 1),)
        assert dict(book.entry_counts) == {"entered": 1, "blocked:account_indeterminate": 2}
        assert book.continuous_return is None and book.continuous_maximum_drawdown is None
        assert all(day.closing_nav is None for day in book.days)
        assert all(day.closing_cash == 100_000_000 - 200 * book.cost_bps for day in book.days)


def test_missing_execution_freezes_inventory_and_symbol_order_even_with_known_later_prices():
    books, quotes, _ = replay(
        [((DAY, B, 60), 1.0), ((DAY, A, 60), 1.0), ((NEXT, C, 60), 1.0)],
        prices={(DAY, A, 90): None, (DAY, B, 90): 450_000}, days=(DAY, NEXT),
    )
    assert quotes[:2] == [(DAY, A, 60), (DAY, B, 60)]
    assert quotes[-1] == (DAY, A, 90)
    for book in books:
        assert book.unknown_at == session_open(DAY) + 93 * MINUTE
        assert book.inventory_uncertain
        assert book.unknown_reasons[0].startswith("missing_exit_price:")
        assert tuple((lot.symbol, lot.quantity) for lot in book.last_known_lots) == ((A, 5), (B, 5))
        assert book.last_known_cash == 96_000_000 - 400 * book.cost_bps
        assert book.modeled_sold_units == 0
        assert dict(book.sale_counts) == {"exit_price_unknown": 1, "account_indeterminate": 1}
        assert all(day.closing_cash is None and day.closing_nav is None for day in book.days)


def test_missing_entry_open_is_not_a_no_fill_or_a_completed_outcome_filter():
    books, quotes, forecasts = replay(
        [((DAY, B, 60), 1.0), ((DAY, A, 60), 1.0)], prices={(DAY, A, 60): None},
    )
    assert quotes == [(DAY, A, 60)] and not forecasts
    for book in books:
        assert book.days[0].eligible_entries == 2
        assert book.inventory_uncertain and book.last_known_lots == ()
        assert book.last_known_cash == 100_000_000 and book.days[0].closing_cash is None
        assert dict(book.entry_counts) == {"entry_price_unknown": 1, "blocked:account_indeterminate": 1}


def test_missing_forecast_breaks_streak_and_duplicate_stays_blocked_until_cap():
    books, _, forecasts = replay(
        [((DAY, A, 60), 1.0), ((DAY, A, 75), 1.0), ((DAY, A, 90), 1.0), ((DAY, A, 105), 1.0)],
        scores={(DAY, A, 65): -1.0, (DAY, A, 70): None, (DAY, A, 75): -1.0},
    )
    assert forecasts == [(DAY, A, offset) for offset in (65, 70, 75, 110, 115, 120)]
    for book in books:
        assert dict(book.entry_counts) == {"duplicate_position": 2, "entered": 2}
        assert book.modeled_sold_units == book.modeled_bought_units
    assert dict(books[1].holding_counts) == {
        "first_nonpositive": 1, "forecast_unavailable": 1, "positive_forecast": 3,
        "reduction_already_requested": 1,
    }
    assert dict(books[1].sale_counts) == {"first_nonpositive": 1, "fixed_cap": 2}


def test_existing_daily_gate_at_inclusive_limit_uses_mark_before_buy_and_allows_recovery():
    books, quotes, _ = replay(
        [((DAY, A, 60), 1.0), ((DAY, B, 75), 1.0), ((DAY, C, 90), 1.0)],
        prices={(DAY, A, 70): 301_320},
    )
    assert (DAY, B, 75) not in quotes and (DAY, C, 90) in quotes
    for book in books:
        assert dict(book.entry_counts) == {"entered": 2, "blocked:daily": 1}
    assert books[0].continuous_maximum_drawdown == Fraction(1, 200)


@pytest.mark.parametrize("offset", [75, 90])
def test_current_open_cannot_gate_or_resize_another_symbol_at_that_open(offset):
    rows = [((DAY, A, 60), 1.0), ((DAY, B, offset), 1.0)]
    low, low_reads, _ = replay(rows, prices={(DAY, A, offset): 10_000})
    high, _, _ = replay(rows, prices={(DAY, A, offset): 450_000})
    if offset == 75:
        assert low_reads.index((DAY, B, offset)) < low_reads.index((DAY, A, offset))
    for first, second in zip(low, high, strict=True):
        assert dict(first.entry_counts) == dict(second.entry_counts) == {"entered": 2}
        assert first.modeled_bought_units == second.modeled_bought_units == 9
        assert first.modeled_paid_cost == second.modeled_paid_cost
        assert first.continuous_maximum_drawdown > second.continuous_maximum_drawdown


def test_weekly_anchor_spans_daily_rolls_and_reopens_next_week():
    thursday, monday = "2025-06-05", "2025-06-09"
    week = (DAY, NEXT, LATER, thursday)
    rows = [((day, A, 60), 1.0) for day in week]
    rows += [((thursday, B, 75), 1.0), ((monday, B, 60), 1.0)]
    books, quotes, _ = replay(
        rows,
        prices={(DAY, A, 90): 320_000, (NEXT, A, 90): 300_000, (LATER, A, 90): 300_000,
                (thursday, A, 70): 300_000, (thursday, A, 90): 300_000},
        days=(*week, monday),
    )
    assert (thursday, B, 75) not in quotes and (monday, B, 60) in quotes
    for book in books:
        assert dict(book.entry_counts) == {"entered": 5, "blocked:weekly": 1}
        assert book.days[-1].closing_cash < book.days[0].closing_cash


def test_unfillable_half_action_does_not_turn_a_missing_mark_into_unknown_execution():
    books, quotes, forecasts = replay(
        [((DAY, A, 60), 1.0)],
        prices={(DAY, A, 60): 1_500_000, (DAY, A, 65): None, (DAY, A, 70): 1_600_000},
        scores={(DAY, A, 65): -1.0, (DAY, A, 70): -1.0},
    )
    dynamic = books[1]
    assert quotes.count((DAY, A, 65)) == 1
    assert forecasts == [(DAY, A, 65), (DAY, A, 70)]
    assert dict(dynamic.sale_counts) == {"reduction_below_one_unit": 1, "consecutive_nonpositive": 1}
    assert dynamic.unknown_at == session_open(DAY) + 72 * MINUTE
    assert not dynamic.inventory_uncertain and dynamic.last_known_lots == ()
    assert dynamic.days[0].closing_cash == 100_095_050
    assert dynamic.continuous_return is None


def test_model_support_and_flat_cash_are_separate_and_do_not_reset_capital():
    books, quotes, _ = replay(
        [((DAY, A, 60), 1.0), ((NEXT, B, 60), None), ((LATER, C, 60), 0.33)],
        days=(DAY, NEXT, LATER, "2025-06-05"),
    )
    assert quotes == [(DAY, A, offset) for offset in range(60, 91, 5)]
    for book in books:
        assert tuple(day.support for day in book.days) == ("supported", "no_forecast", "supported", "no_common_rows")
        cash_balance = 100_000_000 - 200 * book.cost_bps
        assert tuple(day.closing_nav for day in book.days) == (cash_balance, None, cash_balance, None)
        assert all(day.closing_cash == cash_balance for day in book.days)
        assert book.unknown_at is None and book.continuous_return is None


def test_fixed_only_arms_never_request_holding_forecasts():
    def unexpected(_):
        raise AssertionError("fixed-only arm requested a holding forecast")

    books = account.replay_accounts(
        (DAY,), [((DAY, A, 60), 1.0)], quote_at=lambda _: 400_000, gross15_at=unexpected,
        include_dynamic=False,
    )
    assert tuple((book.cost_bps, book.policy) for book in books) == ((33, "fixed"), (43, "fixed"))
    assert all(book.sale_counts == (("fixed_cap", 1),) for book in books)


@pytest.mark.parametrize("corruption", ["entry_quantity", "sale_allocated_cost", "risk_anchor"])
def test_bounded_reconciliation_detects_invented_wrong_accounting(monkeypatch, corruption):
    if corruption == "entry_quantity":
        original = cash.enter

        def bad_enter(*args, **kwargs):
            decision = original(*args, **kwargs)
            return replace(decision, quantity=decision.quantity + 1)

        monkeypatch.setattr(cash, "enter", bad_enter)
        error = "ef_account_movement_mismatch"
    elif corruption == "sale_allocated_cost":
        original = cash.exit

        def bad_exit(*args, **kwargs):
            decision = original(*args, **kwargs)
            record = replace(decision.book.realized_exits[0], allocated_entry_cost=Fraction(0))
            return replace(decision, book=replace(decision.book, realized_exits=(record,)))

        monkeypatch.setattr(cash, "exit", bad_exit)
        error = "ef_account_realized_sale_mismatch"
    else:
        original = account._risk_observation

        def bad_risk(*args, **kwargs):
            updates, *rest = original(*args, **kwargs)
            if updates:
                updates["daily_anchor"] = replace(updates["daily_anchor"], nav=Fraction(1))
            return updates, *rest

        monkeypatch.setattr(account, "_risk_observation", bad_risk)
        error = "ef_account_risk_anchor_mismatch"
    with pytest.raises(InputRefusal, match=error):
        replay([((DAY, A, 60), 1.0)])


@pytest.mark.parametrize("rows,days", [
    ([((DAY, A, 60), 1.0), ((DAY, A, 60), 0.0)], (DAY,)),
    ([((DAY, A, 60), float("nan"))], (DAY,)),
    ([((DAY, A, 300), 1.0)], ("2026-01-02",)),
    ([(("2026-01-02", A, 300), 1.0)], ("2026-01-02",)),
    ([((DAY, A, 65), 1.0)], (DAY,)),
    ([], (NEXT, DAY)),
])
def test_invalid_coordinates_calendar_and_forecasts_fail_closed(rows, days):
    with pytest.raises(InputRefusal, match="ef_account_"):
        replay(rows, days=days)
