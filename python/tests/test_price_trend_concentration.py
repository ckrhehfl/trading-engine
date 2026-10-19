"""Fabricated saved paths; no real saved artifact, API, DB or replay is read."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D, localcontext
import hashlib
import json

import pytest

from research import price_trend_concentration as module
from research.activity_accounting import Lot, sum_decimal_values
from research.activity_book import ActivityBook


SHA, SOURCE = "a" * 64, "b" * 64
CALENDAR = tuple(date(2022, 1, 1) + timedelta(days=index) for index in range(11))
PARAMETERS = dict(slots=20, lookback=1, holding_sessions=5, end=str(CALENDAR[8]),
                  commission_bps_per_side="1.77", slippage_bps_per_side="5", seed=20261005)


def fixture(*, lag=1, codes=("111111", "222222"), sale_prices=("137", "111"), unfilled=False,
            terminal=False, split=False):
    decision, due = CALENDAR[1 + lag], CALENDAR[6 + lag]
    quotes = {}
    for code in {*codes, "383800"}:
        basis = module.RAW if code in module.RAW_CODES else module.ADJUSTED
        for day in CALENDAR:
            quotes[code, day] = module.SavedOpening(D(100), False, SOURCE, basis)
    lots, cash = [], D(1)
    execution_codes = sorted(codes, key=lambda code: hashlib.sha256(f"20261005:{CALENDAR[1]}:{code}".encode()).digest())
    for code in execution_codes:
        if unfilled and code == codes[0]:
            quotes[code, decision] = module.SavedOpening(None, True, SOURCE,
                module.RAW if code in module.RAW_CODES else module.ADJUSTED, "absent_requested")
            continue
        shares = D("0.05") / (D(100) * D("1.000677"))
        lots.append(Lot(f"{code}:{decision}", code, shares, D(100), decision, decision, due, decision, SHA))
        cash -= D("0.05")
    entry_cash, entry_lots = cash, tuple(lots)
    books, event_ids = [], ()
    for day in CALENDAR[2:9]:
        if day < decision:
            books.append(ActivityBook(D(1), (), day))
            continue
        if day == decision:
            cash, lots = entry_cash, list(entry_lots)
        elif split and day == CALENDAR[4]:
            old = lots[0]
            identity = json.dumps(("LG-spin", old.lot_id), separators=(",", ":"))
            investment = "spin-off-investment:" + identity
            # Already verified saved action terms are reliance, not recomputed
            # by this diagnostic; both components retain their original slot.
            lots = [replace(old, shares=old.shares * D("0.6"), investment_id=investment,
                            available_on=CALENDAR[5], mark_date=day),
                    replace(old, lot_id="spin-off-component:" + identity, code="383800",
                            shares=old.shares * D("0.4"), investment_id=investment,
                            available_on=CALENDAR[6], mark_date=day)]
            event_ids = ("LG-spin",)
        else:
            remaining = []
            for old in lots:
                if day >= due:
                    if terminal:
                        quotes[old.code, day] = module.SavedOpening(None, True, SOURCE,
                            module.RAW if old.code in module.RAW_CODES else module.ADJUSTED, "absent_requested")
                    else:
                        index = 1 if split and old.code == "383800" else codes.index(old.code)
                        price = D(sale_prices[index])
                        quotes[old.code, day] = module.SavedOpening(price, False, SOURCE,
                            module.RAW if old.code in module.RAW_CODES else module.ADJUSTED)
                        # 2022 Korean cash-equity sales pay 23bp; literal oracle.
                        cash += old.shares * price * D("0.997023")
                        continue
                remaining.append(replace(old, mark_date=day))
            lots = remaining
        books.append(ActivityBook(cash, tuple(lots), day, event_ids))
    arguments = dict(lag=lag, formation_on=CALENDAR[1], decision_on=decision, selected_codes=codes,
        target_ceiling=len(codes), calendar=CALENDAR, parameters=deepcopy(PARAMETERS),
        openings=quotes, quote_snapshot_sha256=SHA)
    return tuple(books), arguments


def test_simultaneous_sales_follow_own_openings_and_exact_ordered_rounding():
    books, arguments = fixture()
    result = module.attribute_saved_cohort(books, **arguments)
    assert result["assigned_capital"] == D("0.1") and result["filled_entries"] == 2
    rows = result["investments"]
    shares = D("0.05") / (D(100) * D("1.000677"))
    receipts = [shares * D(price) * D("0.997023") for price in (137, 111)]
    assert [row["formula_net_exit_cash"] for row in rows] == receipts
    expected_cash = D("0.90")
    for code in result["execution_codes"]:
        row = next(row for row in rows if row["original_code"] == code)
        receipt = receipts[arguments["selected_codes"].index(code)]
        before = expected_cash
        expected_cash += receipt
        credited = sum_decimal_values((expected_cash, before.copy_negate()))
        assert row["credited_net_exit_cash"] == credited
        assert row["pnl"] == sum_decimal_values((credited, D("-0.05")))
    assert any(row["cash_rounding_adjustment"] != 0 for row in rows)
    assert sum_decimal_values(tuple(row["pnl"] for row in rows)) == result["total_book_pnl"]
    assert result["total_book_pnl"] == sum_decimal_values((expected_cash, D(-1)))
    assert books[-1].cash == expected_cash


def test_declared_rank_order_is_distinct_from_actual_hash_execution_order():
    codes = ("111111", "222222")
    execution = tuple(sorted(codes, key=lambda code: hashlib.sha256(f"20261005:{CALENDAR[1]}:{code}".encode()).digest()))
    ranked = tuple(reversed(execution))
    books, arguments = fixture(codes=ranked)
    assert tuple(lot.code for lot in books[0].lots) != ranked
    result = module.attribute_saved_cohort(books, **arguments)
    assert result["execution_codes"] == execution and result["selected_codes"] == ranked
    assert tuple(row["original_code"] for row in result["investments"]) == ranked


def test_ambient_precision_does_not_change_attribution_or_summary():
    cases = [fixture(lag=lag) for lag in (1, 2)]
    expected = tuple(module.attribute_saved_cohort(books, **arguments) for books, arguments in cases)
    with localcontext() as context:
        context.prec = 6
        actual = tuple(module.attribute_saved_cohort(books, **arguments) for books, arguments in cases)
        report = module.summarize_concentration(actual)
    assert actual == expected
    assert report == module.summarize_concentration(expected)


def test_unfilled_budget_and_terminal_mark_are_retained_without_invented_sales():
    books, arguments = fixture(unfilled=True, terminal=True)
    result = module.attribute_saved_cohort(books, **arguments)
    unfilled, held = result["investments"]
    assert unfilled["pnl"] == unfilled["entry_cash_debit"] == 0
    assert result["assigned_capital"] == D("0.1") and result["filled_entries"] == 1
    assert held["credited_net_exit_cash"] == held["sale_count"] == 0
    assert held["terminal_marked_value"] == books[-1].lots[0].marked_value
    assert held["pnl"] == sum_decimal_values((held["terminal_marked_value"], D("-0.05")))


def test_lg_lx_components_stay_one_original_issuer_in_both_lags():
    values = []
    for lag in (1, 2):
        books, arguments = fixture(lag=lag, codes=("003550",), split=True)
        result = module.attribute_saved_cohort(books, **arguments)
        investment, = result["investments"]
        assert investment["original_code"] == "003550" and investment["sale_count"] == 2
        assert investment["component_codes"] == ("003550", "383800")
        assert investment["entry_cash_debit"] == D("0.05")
        values.append(result)
    report = module.summarize_concentration(tuple(values))
    for arm in report["arms"].values():
        assert [row["unit"] for row in arm["by_original_issuer"]["rows"]] == ["003550"]


@pytest.mark.parametrize("tamper", ["cash", "shares", "order", "new_lot", "due", "lineage", "basis"])
def test_no_tolerance_or_unexplained_inventory_cash_or_basis(tamper):
    books, arguments = fixture(codes=("003550",) if tamper == "lineage" else ("111111", "222222"), split=tamper == "lineage")
    books = list(books)
    if tamper == "cash":
        books[-1] = replace(books[-1], cash=sum_decimal_values((books[-1].cash, D("1e-27"))))
    elif tamper in ("shares", "due", "new_lot"):
        book = books[1]
        old = book.lots[0]
        change = {"shares": old.shares * 2} if tamper == "shares" else ({"due_on": CALENDAR[8]} if tamper == "due" else {"lot_id": "unexplained"})
        books[1] = replace(book, lots=(replace(old, **change), *book.lots[1:]))
    elif tamper == "order":
        books[0] = replace(books[0], lots=tuple(reversed(books[0].lots)))
    elif tamper == "lineage":
        book = books[2]
        books[2] = replace(book, lots=tuple(replace(lot, investment_id="wrong") for lot in book.lots))
    else:
        key = arguments["selected_codes"][0], arguments["decision_on"]
        arguments["openings"][key] = module.SavedOpening(D(100), False, SOURCE, module.RAW)
    with pytest.raises(ValueError):
        module.attribute_saved_cohort(tuple(books), **arguments)


def test_zero_original_budget_has_none_return_and_no_price_requirements():
    values = []
    for lag in (1, 2):
        books, arguments = fixture(lag=lag, codes=())
        arguments["openings"] = {}
        result = module.attribute_saved_cohort(books, **arguments)
        assert result["budget_normalized_pnl"] is None and result["total_book_pnl"] == 0
        values.append(result)
    report = module.summarize_concentration(tuple(values))
    assert report["arms"]["D1"]["budget_normalized_pnl"] is None
    assert report["arms"]["D1"]["by_formation_year"]["rows"][0]["status"] == "zero_assigned_capital"
    assert report["arms"]["D1"]["by_original_issuer"]["top_positive_contribution_subtraction"]["top3"]["residual_budget_normalized_pnl"] is None


def scalar(lag, pnls):
    codes = tuple(f"{index:06}" for index in range(len(pnls)))
    return dict(schema=module.SCHEMA + ":cohort", lag=lag, formation_on=date(2022, 1, 2),
        selected_codes=codes, target_ceiling=len(codes), slots=20, assigned_capital=D(len(codes)) / 20,
        total_book_pnl=sum_decimal_values(tuple(pnls)), filled_entries=len(codes),
        investments=tuple(dict(original_code=code, pnl=pnl, filled_entries=1) for code, pnl in zip(codes, pnls)),
        calendar_years=(2021, 2022, 2023), formation_schedule=(date(2022, 1, 2),))


def test_loss_masking_all_years_and_fixed_denominator_subtraction():
    result = module.summarize_concentration(tuple(scalar(lag, (D(".020"), D("-.019"))) for lag in (1, 2)))
    arm = result["arms"]["D1"]
    assert arm["original_assigned_capital"] == D(".1")
    concentration = arm["by_original_issuer"]
    assert concentration["positive_profit_pnl"] == D(".020")
    assert concentration["absolute_pnl"] == D(".039")
    assert concentration["rows"][0]["signed_net_pnl_share"] == 20
    assert concentration["rows"][0]["positive_profit_share"] == 1
    top = concentration["top_positive_contribution_subtraction"]["top1"]
    assert top["residual_pnl"] == D("-.019") and top["residual_budget_normalized_pnl"] == D("-.19")
    years = {row["formation_year"]: row for row in arm["by_formation_year"]["rows"]}
    assert set(years) == {2021, 2022, 2023}
    assert years[2021]["status"] == years[2023]["status"] == "no_formations"
    assert years[2022]["pnl"] == D(".001")
    assert "not an annual" in result["time_unit"]


def test_zero_net_and_no_positive_profit_do_not_divide_by_zero():
    for pnls in ((D(".02"), D("-.02")), (D("-.02"), D("-.01")), (D(0), D(0))):
        result = module.summarize_concentration(tuple(scalar(lag, pnls) for lag in (1, 2)))
        table = result["arms"]["D1"]["by_original_issuer"]
        if sum(pnls) == 0:
            assert all(row["signed_net_pnl_share"] is None for row in table["rows"])
        if max(pnls) <= 0:
            assert table["top_positive_contribution_subtraction"]["top3"]["units"] == ()
            assert all(row["positive_profit_share"] is None for row in table["rows"])


@pytest.mark.parametrize("mutation", ["one_lag", "duplicate", "budget", "pnl", "population", "missing_both"])
def test_summary_rejects_geometry_and_scalar_tampering(mutation):
    values = [scalar(lag, (D(".02"),)) for lag in (1, 2)]
    if mutation == "one_lag": values.pop()
    elif mutation == "duplicate": values.append(deepcopy(values[0]))
    elif mutation == "budget":
        for value in values: value["assigned_capital"] = D(".1")
    elif mutation == "pnl": values[0]["total_book_pnl"] += D("1e-27")
    elif mutation == "population": values[1]["selected_codes"] = ("999999",)
    else:
        for value in values: value["formation_schedule"] += (date(2022, 2, 1),)
    with pytest.raises(ValueError): module.summarize_concentration(tuple(values))


def audit_row(*, state="observed", opening="100", kind="text"):
    return dict(code="111111", observation_date="2022-01-03", state=state, issues=[],
        typed_values=None if state == "absent_requested" else [["text", "20220103"], [kind, opening],
            ["text", "101"], ["text", "99"], ["text", "100"], ["text", "1"], ["text", "100"]])


def audit(rows):
    return dict(schema="activity-holding-price-preflight-v1:holding-input-audit-v1", rows=rows)


def test_projection_preserves_exact_text_absence_frozen_and_source_identity():
    row = audit_row(opening="100.00000000000000000000000001")
    key = "111111", date(2022, 1, 3)
    result = module.project_saved_openings(audit([row]), frozenset({key}), source_sha256=SOURCE)
    assert result[key].opening.as_tuple() == D(row["typed_values"][1][1]).as_tuple()
    assert result[key].source_sha256 == SOURCE and result[key].basis == module.ADJUSTED
    for state in ("frozen", "absent_requested"):
        result = module.project_saved_openings(audit([audit_row(state=state)]), source_sha256=SOURCE)
        assert result[key].frozen and result[key].state == state
    envelope = dict(schema="activity-quote-extension-envelope-v1", holding_input_audit=audit([row]))
    assert module.project_saved_openings(envelope, source_sha256=SOURCE)[key].opening == D(row["typed_values"][1][1])


@pytest.mark.parametrize("kind,opening", [("integer", "100"), ("real", float(100).hex()), ("text", "0"), ("text", "NaN"), ("text", 100.0)])
def test_projection_rejects_float_storage_zero_and_nonfinite(kind, opening):
    with pytest.raises(ValueError):
        module.project_saved_openings(audit([audit_row(kind=kind, opening=opening)]), source_sha256=SOURCE)


def test_projection_retains_unresolved_and_refuses_duplicate_and_missing_consumed_coordinates():
    row = audit_row(state="unresolved_observed")
    row["issues"] = ["invalid"]
    result = module.project_saved_openings(audit([row]), source_sha256=SOURCE)
    assert result["111111", date(2022, 1, 3)].state == "unresolved"
    with pytest.raises(ValueError): module._opening(result, "111111", date(2022, 1, 3))
    with pytest.raises(ValueError): module._opening(result, "111111", date(2022, 1, 4))
    with pytest.raises(ValueError): module.project_saved_openings(audit([row, row]), source_sha256=SOURCE)


def test_raw_projection_preserves_raw_priority_no_trade_and_absence():
    row = dict(code="003550", requested_bas_dd="20220103", status="observed", state="locked", issues=[], open="123.45")
    source = dict(schema="activity-raw-episode-quotes-v1", rows=[row])
    key = "003550", date(2022, 1, 3)
    quote = module.project_saved_openings(source, source_sha256=SOURCE)[key]
    assert quote.basis == module.RAW and quote.opening == D("123.45") and not quote.frozen
    for state, issues, expected in (("observed_no_trade_zero_prices", [], "no_trade"),
            ("unresolved", ["target_absent_cause_unknown"], "absent_requested")):
        row.update(state=state, issues=issues, status="observed" if not issues else "unresolved", open=None)
        quote = module.project_saved_openings(source, source_sha256=SOURCE)[key]
        assert quote.state == expected and quote.opening is None and quote.frozen
    row.update(state="unresolved", issues=["mixed_or_active_zero_prices"], canonical_decimal_strings=[
        [name, "100" if name == "TDD_CLSPRC" else "0"] for name in
        ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL", "ACC_TRDVAL")])
    assert module.project_saved_openings(source, source_sha256=SOURCE)[key].state == "no_trade"
