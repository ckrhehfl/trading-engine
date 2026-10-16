"""Issuer price attribution uses fabricated inputs and the one original replay."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D, localcontext

import pytest

from research import activity_cohort_comparison as comparison
from research import activity_cohort_return_attribution as module
from research.activity_accounting import FinalCashDistribution, PriceBasis, sum_decimal_values
from research.activity_book import FinalCashPayment
from research.activity_pair_calibration import PARAMETERS
from research.krx_tax_schedule import KOSPI, total_bp
import test_activity_cohort_comparison as fixture


CODES = (*module.KNOWN_ISSUER_CODES, "999999")


def case(monkeypatch, *, changes=None):
    monkeypatch.setattr(fixture, "CODES", CODES)
    declarations, quotes = fixture.case((4, 0), changes=changes)
    declaration = next(row for row in declarations if row.arm == "D1" and row.group_id == "ACTIVITY")
    result = comparison.replay_equal_capital_cohorts(declarations, quotes, (), parameters=deepcopy(PARAMETERS),
        cohort_keys=((declaration.arm, declaration.group_id, declaration.formation_on),))
    replay = result["cohorts"][0]["replay"]
    return replay, declaration, quotes


def attribute(values, *, replay=None, events=()):
    original, declaration, quotes = values
    return module.attribute_known_issuer_returns(original if replay is None else replay, declaration, quotes,
        deepcopy(PARAMETERS), events=events)


def price(day, value):
    return (day.strftime("%Y%m%d"), str(value), str(value + 1), str(value - 1), str(value), "100", "10")


def test_manual_decimal_normal_and_concurrent_sales_use_separate_original_lots(monkeypatch):
    initial = case(monkeypatch)
    calendar = initial[2].calendar
    changes = {(code, calendar[187]): price(calendar[187], 110 + index * 10) for index, code in enumerate(CODES)}
    values = case(monkeypatch, changes=changes)
    original = deepcopy(values[0])
    result = attribute(values)
    tax, cost = D(str(total_bp(KOSPI, calendar[187], calendar))), D("6.77")
    shares = D("0.05") / (D(100) * (1 + cost / 10000))
    expected_pnls = []
    for index, code in enumerate(module.KNOWN_ISSUER_CODES):
        row = result["by_code"][code]
        cash = shares * D(110 + index * 10) * (1 - (cost + tax) / 10000)
        expected = sum_decimal_values((cash, D("-0.05")))
        expected_pnls.append(expected)
        assert row["entry_budget"] == D("0.05") and row["net_exit_cash"] == cash
        assert row["terminal_marked_value"] == 0 and row["pnl"] == expected
        assert row["original_lot_id"] == f"{code}:{calendar[61]}" and row["exit_on"] == calendar[187]
        assert row["assigned_capital_return_contribution"] == expected / D("0.2")
        assert row["automatic_subscription_rights_cash"] is None and row["rights_cash_model"] == "not_modeled"
    assert result["known_issuer_pnl"] == sum_decimal_values(tuple(expected_pnls))
    other_cash = shares * D(140) * (1 - (cost + tax) / 10000)
    # Engine cash += remains its own 28-digit sequential convention. The
    # descriptive residual retains any whole-book aggregation rounding.
    assert abs(result["remainder_pnl"] - (other_cash - D("0.05"))) < D("1e-26")
    assert sum_decimal_values((result["known_issuer_pnl"], result["remainder_pnl"])) == result["total_book_pnl"]
    assert values[0] == original


@pytest.mark.parametrize("state", ["absent", "frozen"])
def test_deferred_sales_use_actual_disappearance_day_and_era_tax(monkeypatch, state):
    initial = case(monkeypatch)
    code, calendar = module.KNOWN_ISSUER_CODES[0], initial[2].calendar
    due = calendar[187]
    changes = {(code, due): None if state == "absent" else
        (due.strftime("%Y%m%d"), "100", "100", "100", "100", "0", "0"),
        (code, calendar[188]): price(calendar[188], 125)}
    values = case(monkeypatch, changes=changes)
    row = attribute(values)["by_code"][code]
    shares = D("0.05") / (D(100) * (1 + D("6.77") / 10000))
    assert row["exit_on"] == calendar[188]
    assert row["net_exit_cash"] == shares * D(125) * (1 -
        (D("6.77") + D(str(total_bp(KOSPI, calendar[188], calendar)))) / 10000)


def test_terminal_marked_lot_is_not_an_invented_terminal_sale(monkeypatch):
    initial = case(monkeypatch)
    code, calendar = module.KNOWN_ISSUER_CODES[0], initial[2].calendar
    changes = {(code, day): None for day in calendar[187:315]}
    changes[code, calendar[186]] = price(calendar[186], 130)
    values = case(monkeypatch, changes=changes)
    row = attribute(values)["by_code"][code]
    terminal = next(lot for lot in values[0].books[-1].lots if lot.code == code)
    assert row["exit_on"] is None and row["net_exit_cash"] == 0
    assert row["terminal_marked_value"] == terminal.shares * D(130)
    assert row["pnl"] == sum_decimal_values((terminal.marked_value, D("-0.05")))


def test_unfilled_target_retains_zero_pnl_and_does_not_redistribute_budget(monkeypatch):
    initial = case(monkeypatch)
    code, calendar = module.KNOWN_ISSUER_CODES[0], initial[2].calendar
    values = case(monkeypatch, changes={(code, calendar[61]): None})
    result = attribute(values)
    row = result["by_code"][code]
    assert row["targeted"] and row["filled_entries"] == 0 and row["original_lot_id"] is None
    assert row["entry_budget"] == row["net_exit_cash"] == row["terminal_marked_value"] == row["pnl"] == 0
    assert row["automatic_subscription_rights_cash"] is None
    assert result["assigned_capital"] == D("0.2")
    assert all(result["by_code"][other]["entry_budget"] == D("0.05") for other in module.KNOWN_ISSUER_CODES[1:])
    assert values[0].books[0].cash == D("0.85")


@pytest.mark.parametrize("mutation", ["shares", "code", "early_disappearance", "cash_receipt", "mark", "entry_cash", "reappear", "missed_sale"])
def test_inconsistent_or_unknown_lot_trace_is_refused(monkeypatch, mutation):
    values = case(monkeypatch)
    replay, declaration, quotes = values
    books = list(replay.books)
    code = module.KNOWN_ISSUER_CODES[0]
    entry = next(lot for lot in books[0].lots if lot.code == code)
    if mutation in ("shares", "code", "mark"):
        lot = next(lot for lot in books[1].lots if lot.code == code)
        changed = replace(lot, **{"shares": {"shares": lot.shares * 2}, "code": {"code": "888888"},
            "mark": {"mark": lot.mark + 1}}[mutation])
        books[1] = replace(books[1], lots=tuple(changed if item.lot_id == lot.lot_id else item for item in books[1].lots))
    elif mutation == "early_disappearance":
        books[1] = replace(books[1], lots=tuple(lot for lot in books[1].lots if lot.code != code))
    elif mutation == "cash_receipt": books[126] = replace(books[126], cash=books[126].cash + D("0.01"))
    elif mutation == "entry_cash": books[0] = replace(books[0], cash=books[0].cash + D("0.01"))
    elif mutation == "reappear": books[-1] = replace(books[-1], lots=(replace(entry, mark_date=books[-1].as_of),))
    else: books[126] = replace(books[126], lots=(replace(entry, mark_date=books[126].as_of),))
    with pytest.raises(ValueError): attribute(values, replay=replace(replay, books=tuple(books)))


def test_supported_action_touching_affected_issuer_requires_new_explicit_lineage(monkeypatch):
    values = case(monkeypatch)
    calendar, code = values[2].calendar, module.KNOWN_ISSUER_CODES[0]
    basis = PriceBasis(code, calendar[99], D(100), D(100), values[2].quote_snapshot_sha256, "synthetic basis")
    event = FinalCashPayment("unsupported-affected-lineage", FinalCashDistribution(code,
        calendar[99], calendar[100], calendar[200], D(100), basis, "synthetic final payment"))
    with pytest.raises(ValueError, match="explicit attribution lineage"): attribute(values, events=(event,))
    with pytest.raises(ValueError, match="unknown action"): attribute(values, events=(object(),))


def test_same_original_capital_denominator_and_remainder_reconcile_without_replay(monkeypatch):
    values = case(monkeypatch)
    first = attribute(values)
    second = deepcopy(first)
    rows = ({module.ATTRIBUTION_KEY: first}, {module.ATTRIBUTION_KEY: second})
    total = sum_decimal_values((first["total_book_pnl"], second["total_book_pnl"]))
    monkeypatch.setattr(comparison, "replay_synthetic", lambda *a, **k: pytest.fail("attribution executed another replay"))
    with localcontext() as context:
        context.prec = 5
        result = module.aggregate_known_issuer_returns(rows, assigned_capital=D("0.4"), total_pnl=total)
        assert context.prec == 5
    for code in module.KNOWN_ISSUER_CODES:
        assert result["by_code"][code]["entry_budget"] == D("0.1")
        assert result["by_code"][code]["pnl"] == sum_decimal_values((first["by_code"][code]["pnl"],) * 2)
    assert sum_decimal_values((result["known_issuer_pnl"], result["remainder_pnl"])) == total
    assert result["known_issuer_return_contribution"] == result["known_issuer_pnl"] / D("0.4")
