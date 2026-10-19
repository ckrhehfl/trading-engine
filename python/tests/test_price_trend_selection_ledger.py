"""Synthetic saved books produced by the existing replay, with corruptions."""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext

import pytest

from research.activity_accounting import Lot, sum_decimal_values
from research.activity_book import ActivityBook
from research.activity_partition_selection import PartitionSelection
from research.activity_replay import DecimalSeries, replay_synthetic
from research.activity_timing import SessionLagPolicy
from research.price_trend_selection_ledger import audit_saved_cohort
from research.price_trend_concentration import SavedOpening, attribute_saved_cohort


D = Decimal
PIN = "a" * 64


def _json(value):
    if is_dataclass(value):
        return _json(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if type(value) in (date, datetime):
        return value.isoformat()
    if type(value) is dict:
        return {key: _json(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json(item) for item in value]
    return value


def _fixture(*, names=2, lag=1, delayed=False):
    # Entirely synthetic calendar, spanning a real statutory settlement seam.
    calendar = tuple(date.fromisoformat(value) for value in (
        "2024-12-20", "2024-12-23", "2024-12-24", "2024-12-26", "2024-12-27", "2024-12-30",
        "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09",
        "2025-01-10", "2025-01-13"))
    parameters = dict(lookback=1, holding_sessions=3, slots=20, seed=271,
        threshold=3, end=calendar[-3].isoformat(), commission_bps_per_side=1.5, slippage_bps_per_side=2)
    policy = SessionLagPolicy(lag, paired=True)
    indices = policy.formation_indices(calendar, lookback=1, holding_sessions=3, end_index=len(calendar) - 3)
    codes = ("035720", "011780", "012450")[:names]
    entry_index, due_index = 1 + lag, 4 + lag
    panel = {}
    for index, code in enumerate(codes):
        opens = tuple(D(70 + 60 * index + position) for position in range(len(calendar)))
        closes = tuple(value + 10 for value in opens)  # A mark cannot stand in for its opening.
        frozen = tuple(delayed and index == 0 and position == due_index for position in range(len(calendar)))
        locked = tuple(index == 1 and position in (entry_index, due_index) for position in range(len(calendar)))
        panel[code] = DecimalSeries(opens, closes, tuple(D(1000) for _ in calendar), frozen, locked,
                                    tuple(True for _ in calendar))
    selections = tuple(PartitionSelection(calendar[index], calendar[index + lag], policy.selection_at(calendar, index),
        codes if index == 1 else (), names, PIN) for index in indices)
    with localcontext() as context:
        context.prec = 28
        result = replay_synthetic(calendar, panel, parameters, selections, dataset_sha256=PIN, timing_policy=policy)
        entry = next(book for book in result.books if book.as_of == calendar[entry_index])
        principal = sum_decimal_values(tuple(lot.shares * panel[lot.code].opens[entry_index] for lot in entry.lots))
        filled = D(names) / 20
        row = dict(key=(f"D{lag}", "COMBO", calendar[1]), arm=f"D{lag}", group_id="COMBO",
            formation_on=calendar[1], decision_on=calendar[entry_index], selection_at=policy.selection_at(calendar, 1),
            chosen_codes=codes, target_ceiling=names + 2, eligible_shortfall=2, assigned_capital=D(names + 2) / 20,
            filled_capital=filled, filled_entries=names, filled_principal=principal,
            entry_cost_amount=sum_decimal_values((filled, principal.copy_negate())),
            ordinary_closed_trades=result.closed_trades, terminal_open_lots=0, terminal_open_investments=0,
            raw_metrics={"path": [{"date": book.as_of, "nav": book.nav} for book in result.books]}, replay=result)
    quotes = {}
    for code in codes:
        for position in (entry_index, due_index + int(delayed and code == codes[0])):
            quotes[code, calendar[position].isoformat()] = dict(open=str(panel[code].opens[position]),
                state="locked" if panel[code].locked[position] else "observed", artifact_sha256="b" * 64,
                provenance={"source": "synthetic fixture", "true_publication_time": None})
    return _json(row), dict(calendar=[day.isoformat() for day in calendar], parameters=parameters, quotes=quotes)


@pytest.mark.parametrize("names,lag", [(1, 1), (2, 1), (3, 2)])
def test_existing_engine_books_reconcile_every_target_and_exact_nav(names, lag):
    row, inputs = _fixture(names=names, lag=lag)
    before = deepcopy((row, inputs))
    result = audit_saved_cohort(row, **inputs)
    assert len(result["investments"]) == names
    assert result["chosen_codes"] == row["chosen_codes"]
    assert result["nominal_budget_per_target"] == D(".05")
    assert result["initial_cash"] == D(1)
    assert result["terminal_nav"] == D(row["replay"]["books"][-1]["cash"])
    assert sum_decimal_values(tuple(item["net_accounted_pnl"] for item in result["investments"])) == result["total_book_pnl"]
    assert result["all_targets_audited"] and result["cash_and_nav_reconciled"]
    assert not result["execution_certified"] and not result["source_truth_certified"] and not result["replayed"]
    assert result["unmodeled_automatic_assets"] == "unknown"
    assert (row, inputs) == before


def test_openings_cost_components_tax_seam_and_rounding_are_explicit():
    row, inputs = _fixture()
    result = audit_saved_cohort(row, **inputs)
    assert any(item["entry_cash_rounding"] or item["exit_cash_rounding"] for item in result["investments"])
    assert any(item["modeled_cost_rounding_residual"] for item in result["investments"])
    for item in result["investments"]:
        # December 30 settles after the January 2025 cut; the old rate is 18bp.
        assert item["sale_tax_bps"] == D("15.0")
        assert item["entry_sale_tax"] == 0
        assert item["entry_commission"] > 0 and item["entry_slippage"] > 0
        assert item["exit_commission"] > 0 and item["exit_slippage"] > 0 and item["exit_sale_tax"] > 0
        assert sum_decimal_values((item["gross_price_pnl"], item["total_cost_components"].copy_negate(),
                                   item["modeled_cost_rounding_residual"])) == item["net_modeled_pnl"]
        assert sum_decimal_values((item["net_modeled_pnl"], item["exit_cash_rounding"],
                                   item["entry_cash_rounding"].copy_negate())) == item["net_accounted_pnl"]
        assert item["entry_open"] != D(next(book for book in row["replay"]["books"]
            if book["as_of"] == row["decision_on"])["lots"][0]["mark"])
    assert result["investments"][0]["entry_quote"]["provenance"]["true_publication_time"] is None


def test_delayed_exit_uses_first_disappearance_without_inventing_delay_cause():
    row, inputs = _fixture(delayed=True)
    result = audit_saved_cohort(row, **inputs)
    delayed, ordinary = result["investments"]
    assert delayed["delayed_exit_sessions"] == 1
    assert delayed["last_lot_appearance"] == delayed["due_on"]
    assert delayed["first_disappearance"] == "2025-01-02"
    assert delayed["delayed_exit_cause"] == "unknown"
    assert not delayed["first_executable_exit_verified"]
    assert ordinary["delayed_exit_sessions"] == 0


def test_decimal_context_is_local_and_existing_engine_order_is_preserved():
    row, inputs = _fixture(names=3)
    with localcontext() as context:
        context.prec = 7
        result = audit_saved_cohort(row, **inputs)
        assert context.prec == 7
    assert result == audit_saved_cohort(row, **inputs)
    entry = next(book for book in row["replay"]["books"] if book["as_of"] == row["decision_on"])
    assert result["execution_codes"] == [lot["code"] for lot in entry["lots"]]


def test_existing_saved_attribution_agrees_with_all_target_credits_and_pnl():
    row, inputs = _fixture(names=3)
    books = []
    for raw in row["replay"]["books"]:
        lots = []
        for value in raw["lots"]:
            converted = {**value, "shares": D(value["shares"]), "mark": D(value["mark"])}
            converted.update({name: date.fromisoformat(value[name])
                for name in ("entered_on", "due_on", "available_on", "mark_date")})
            lots.append(Lot(**converted))
        books.append(ActivityBook(D(raw["cash"]), tuple(lots), date.fromisoformat(raw["as_of"]),
                                  tuple(raw["applied_event_ids"])))
    openings = {(code, date.fromisoformat(day)): SavedOpening(D(value["open"]), False,
        value["artifact_sha256"], "KIS_adjusted_daily_declared", value["state"])
        for (code, day), value in inputs["quotes"].items()}
    existing = attribute_saved_cohort(tuple(books), lag=1,
        formation_on=date.fromisoformat(row["formation_on"]), decision_on=date.fromisoformat(row["decision_on"]),
        selected_codes=tuple(row["chosen_codes"]), target_ceiling=row["target_ceiling"],
        calendar=tuple(date.fromisoformat(day) for day in inputs["calendar"]), parameters=inputs["parameters"],
        openings=openings, quote_snapshot_sha256=PIN)
    result = audit_saved_cohort(row, **inputs)
    assert result["total_book_pnl"] == existing["total_book_pnl"]
    for item, previous in zip(result["investments"], existing["investments"], strict=True):
        assert item["net_accounted_pnl"] == previous["pnl"]
        assert item["entry_cash_debit"] == previous["entry_cash_debit"]
        assert item["credited_net_exit_cash"] == previous["credited_net_exit_cash"]


@pytest.mark.parametrize("mutation", ["quantity", "cash", "tiny_cash", "mutation", "component", "carried", "duplicate", "event_drop",
                                      "unexpected_acquisition", "nav", "principal", "selection", "calendar", "due"])
def test_saved_book_corruption_fails_closed(mutation):
    row, inputs = _fixture()
    entry = next(book for book in row["replay"]["books"] if book["as_of"] == row["decision_on"])
    following = row["replay"]["books"][1]
    if mutation == "quantity":
        entry["lots"][0]["shares"] = "7"
    elif mutation == "cash":
        row["replay"]["books"][-1]["cash"] = "1.01"
    elif mutation == "tiny_cash":
        row["replay"]["books"][1]["cash"] = "0.9000000000000000000000000000000000000001"
    elif mutation == "mutation":
        following["lots"][0]["shares"] = "7"
    elif mutation == "component":
        entry["lots"][0]["investment_id"] = "invented"
    elif mutation == "carried":
        entry["lots"][0]["carried_value"] = "1"
    elif mutation == "duplicate":
        entry["lots"].append(deepcopy(entry["lots"][0]))
    elif mutation == "event_drop":
        entry["applied_event_ids"] = ["synthetic-consumed-event"]
    elif mutation == "unexpected_acquisition":
        row["replay"]["books"][-1]["lots"] = deepcopy(entry["lots"])
    elif mutation == "nav":
        row["raw_metrics"]["path"][-1]["nav"] = "1.99"
    elif mutation == "principal":
        row["filled_principal"] = "0.1"
    elif mutation == "selection":
        row["replay"]["selections"][0][1].reverse()
    elif mutation == "calendar":
        inputs["calendar"].insert(1, inputs["calendar"][1])
    elif mutation == "due":
        entry["lots"][0]["due_on"] = "2025-01-03"
    with pytest.raises(ValueError):
        audit_saved_cohort(row, **inputs)


@pytest.mark.parametrize("mutation", ["missing", "frozen", "zero", "nan", "float", "pin", "provenance", "close"])
def test_entry_exit_quotes_fail_closed(mutation):
    row, inputs = _fixture()
    key = row["chosen_codes"][0], row["decision_on"]
    quote = inputs["quotes"][key]
    if mutation == "missing":
        del inputs["quotes"][key]
    elif mutation == "frozen":
        quote["state"] = "frozen"
    elif mutation == "zero":
        quote["open"] = "0"
    elif mutation == "nan":
        quote["open"] = "NaN"
    elif mutation == "float":
        quote["open"] = 72.0
    elif mutation == "pin":
        quote["artifact_sha256"] = "unverified"
    elif mutation == "provenance":
        quote["provenance"] = {}
    elif mutation == "close":
        quote["open"] = "82"  # Saved entry close, deliberately not its open.
    with pytest.raises(ValueError):
        audit_saved_cohort(row, **inputs)


def test_nonholding_global_event_ids_remain_saved_reliance():
    row, inputs = _fixture()
    for book in row["replay"]["books"]:
        book["applied_event_ids"] = ["unrelated-global-event"]
    result = audit_saved_cohort(row, **inputs)
    assert result["saved_event_ids"] == ["unrelated-global-event"]
    assert not result["source_truth_certified"]


@pytest.mark.parametrize("state", [None, "frozen", "absent_requested"])
def test_missing_or_nonexecutable_exit_quote_is_not_a_cash_receipt(state):
    row, inputs = _fixture()
    key = row["chosen_codes"][0], "2024-12-30"
    if state is None:
        del inputs["quotes"][key]
    else:
        inputs["quotes"][key]["state"] = state
    with pytest.raises(ValueError):
        audit_saved_cohort(row, **inputs)
