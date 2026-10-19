"""Reconcile closed ordinary episodes from saved cohort JSON, without replay.

The caller verifies artifact bytes and projects opens as
``quotes[(code, ISO_date)] = {open: Decimal_text, state: observed|locked,
artifact_sha256: lower_hex_sha256, provenance: nonempty_dict}``.
Only entry and first-disappearance coordinates are consumed. Saved closing
marks, intervening missingness, economic share units and source truth remain
caller reliance. Unmodeled automatic assets remain unknown.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import re

from research.activity_accounting import sum_decimal_values
from research.activity_timing import SessionLagPolicy
from research.krx_tax_schedule import KOSPI, total_bp


ZERO, ONE = Decimal(0), Decimal(1)
SCHEMA = "price-trend-selection-ledger-v1"
LOT_FIELDS = {"lot_id", "code", "shares", "mark", "mark_date", "entered_on", "due_on",
              "available_on", "dataset_sha256", "investment_id", "carried_value"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _day(value):
    _require(type(value) is str, "canonical saved ISO date required")
    result = date.fromisoformat(value)
    _require(result.isoformat() == value, "canonical saved ISO date required")
    return result


def _amount(value, *, positive=False):
    _require(type(value) is str and value == value.strip(), "saved Decimal text required")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("saved Decimal text required") from error
    _require(result.is_finite() and result >= 0 and (not positive or result > 0),
             "finite nonnegative saved Decimal required")
    # Bound arithmetic before using the existing exact-sum primitive.
    _require(abs(result.adjusted()) <= 4096 and abs(result.as_tuple().exponent) <= 4096,
             "bounded saved Decimal required")
    return result


def _difference(left, right):
    return sum_decimal_values((left, right.copy_negate()))


def _pin(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             "saved artifact SHA-256 required")


def _quote(quotes, code, session):
    value = quotes.get((code, session))
    _require(type(value) is dict and set(value) == {"open", "state", "artifact_sha256", "provenance"},
             "explicit saved entry/exit quote and provenance required")
    _require(value["state"] in ("observed", "locked"), "entry/exit quote must be observed or locked")
    _pin(value["artifact_sha256"])
    _require(type(value["provenance"]) is dict and bool(value["provenance"]),
             "saved quote provenance mapping required")
    return _amount(value["open"], positive=True), value


def _book(raw):
    _require(type(raw) is dict and set(raw) == {"cash", "lots", "as_of", "applied_event_ids"},
             "complete saved book JSON required")
    day, cash = _day(raw["as_of"]), _amount(raw["cash"])
    _require(type(raw["lots"]) is list and type(raw["applied_event_ids"]) is list,
             "saved lot/event arrays required")
    events = raw["applied_event_ids"]
    _require(all(type(value) is str and value and value == value.strip() for value in events)
             and len(set(events)) == len(events), "unique saved event IDs required")
    lots = []
    for item in raw["lots"]:
        _require(type(item) is dict and set(item) == LOT_FIELDS, "complete ordinary saved lot required")
        _require(type(item["code"]) is str and re.fullmatch(r"[0-9A-Z]{6}", item["code"]) is not None,
                 "saved issue code required")
        _require(item["investment_id"] is None and item["carried_value"] is None,
                 "component or carried-value lots are unsupported")
        _pin(item["dataset_sha256"])
        lot = {**item, "shares": _amount(item["shares"], positive=True),
               "mark": _amount(item["mark"], positive=True)}
        dates = {key: _day(item[key]) for key in ("entered_on", "available_on", "due_on", "mark_date")}
        _require(dates["entered_on"] == dates["available_on"] <= dates["mark_date"] <= day
                 and dates["due_on"] >= dates["entered_on"], "ordinary saved lot dates are inconsistent")
        _require(item["lot_id"] == f"{item['code']}:{item['entered_on']}", "ordinary saved lot ID changed")
        lots.append(lot)
    _require(len({item["lot_id"] for item in lots}) == len(lots)
             and len({item["code"] for item in lots}) == len(lots), "duplicate saved lots/codes")
    return {"as_of": day, "cash": cash, "lots": lots, "events": events}


def audit_saved_cohort(row, *, calendar, parameters, quotes) -> dict:
    """Check all targets and daily cash/NAV of one saved closed ordinary cohort.

    No source reads, engine calls, tolerance, re-selection or economic basis
    conversion occur. Arithmetic uses the producer's Decimal28 operation order;
    exact aggregation and explicit residuals explain component/cash rounding.
    """
    _require(type(row) is dict and type(parameters) is dict and isinstance(quotes, Mapping),
             "saved cohort, original parameters and projected quotes required")
    _require(type(calendar) in (list, tuple), "complete saved calendar required")
    days = tuple(_day(value) for value in calendar)
    SessionLagPolicy._calendar(days)
    for key in ("lookback", "holding_sessions", "slots"):
        _require(type(parameters.get(key)) is int and parameters[key] > 0, "positive original geometry required")
    _require(parameters["slots"] == 20 and type(parameters.get("seed")) is int,
             "original twenty-slot deterministic book required")
    _require(row.get("arm") in ("D1", "D2") and row.get("group_id") in ("COMBO", "TREND_MATCHED"),
             "saved COMBO or TREND_MATCHED arm required")
    policy = SessionLagPolicy(int(row["arm"][-1]), paired=True)
    formation, decision = (_day(row[key]) for key in ("formation_on", "decision_on"))
    end = _day(parameters["end"])
    _require(formation in days and end in days and days.index(end) + 2 < len(days),
             "complete original calendar and settlement tail required")
    indices = policy.formation_indices(days, lookback=parameters["lookback"],
        holding_sessions=parameters["holding_sessions"], end_index=days.index(end))
    _require(days.index(formation) in indices and policy.execution_on(days, days.index(formation)) == decision,
             "saved formation/decision differs from original paired geometry")
    _require(row.get("selection_at") == policy.selection_at(days, days.index(formation)).isoformat(),
             "saved pre-opening selection cutoff changed")
    _require(row.get("key") == [row["arm"], row["group_id"], formation.isoformat()], "saved cohort key changed")
    chosen = row.get("chosen_codes")
    _require(type(chosen) is list and chosen and all(type(code) is str and re.fullmatch(r"[0-9A-Z]{6}", code)
             for code in chosen) and len(set(chosen)) == len(chosen), "unique nonempty complete target list required")
    ceiling = row.get("target_ceiling")
    _require(type(ceiling) is int and len(chosen) <= ceiling <= 20, "original target ceiling required")
    _require(row.get("eligible_shortfall") == ceiling - len(chosen), "saved target shortfall changed")
    replay = row.get("replay")
    _require(type(replay) is dict and set(replay) == {"books", "selections", "diagnostics", "closed_trades"},
             "complete saved replay JSON required")
    _require(type(replay["books"]) is list and replay["books"], "complete nonempty saved books required")
    books = [_book(value) for value in replay["books"]]
    expected_days = days[parameters["lookback"] + 1:days.index(end) + 1]
    _require(tuple(book["as_of"] for book in books) == expected_days, "complete saved daily book path required")
    execution_codes = sorted(chosen, key=lambda code:
        hashlib.sha256(f"{parameters['seed']}:{formation}:{code}".encode()).digest())
    _require(replay["selections"] == [[days[index].isoformat(), execution_codes if days[index] == formation else []]
             for index in indices], "saved full selection schedule or ordered targets changed")
    diagnostics = replay["diagnostics"]
    _require(type(diagnostics) is list and all(type(value) is list and len(value) == 2
             and type(value[0]) is str and type(value[1]) is int and value[1] >= 0 for value in diagnostics),
             "saved integer diagnostics required")
    _require(len(dict(diagnostics)) == len(diagnostics), "duplicate saved diagnostics")
    diagnostics = dict(diagnostics)
    _require(diagnostics.get("entries", 0) == len(chosen) and diagnostics.get("unfilled_entries", 0) == 0
             and type(replay["closed_trades"]) is int and replay["closed_trades"] == len(chosen),
             "every target must have one filled and closed ordinary lot")
    with localcontext() as context:
        context.prec = 28
        costs = []
        for key in ("commission_bps_per_side", "slippage_bps_per_side"):
            value = parameters.get(key)
            _require(type(value) in (str, int, float, Decimal), "original cost scalar required")
            costs.append(_amount(str(value)))
        commission, slippage = costs
        cost = commission + slippage
        _require(cost <= 10000, "entry costs exceed notional")
        budget, cash = ONE / 20, ONE
        due = days[days.index(decision) + parameters["holding_sessions"]].isoformat()
        investments, previous_lots, previous_events, path = {}, [], [], []
        snapshot = None
        for book in books:
            session = book["as_of"].isoformat()
            _require(book["events"][:len(previous_events)] == previous_events, "saved event lineage changed")
            current = {lot["lot_id"]: lot for lot in book["lots"]}
            if book["as_of"] == decision:
                _require(not previous_lots and not investments, "unexpected inventory before entry")
                _require([lot["code"] for lot in book["lots"]] == execution_codes,
                         "every saved target must enter once in producer order")
                for lot in book["lots"]:
                    code = lot["code"]
                    opening, quote = _quote(quotes, code, session)
                    _require(lot["entered_on"] == lot["available_on"] == session and lot["due_on"] == due,
                             "saved entry/availability/due coordinates changed")
                    _require(lot["shares"] == budget / (opening * (ONE + cost / 10000)),
                             "saved quantity differs from original opening/budget formula")
                    if snapshot is None:
                        snapshot = lot["dataset_sha256"]
                    _require(snapshot == lot["dataset_sha256"], "saved ordinary snapshot changed")
                    before, principal = cash, lot["shares"] * opening
                    cash -= budget
                    debit = _difference(before, cash)
                    entry_cost = _difference(budget, principal)
                    entry_commission, entry_slippage = principal * commission / 10000, principal * slippage / 10000
                    investments[code] = dict(code=code, lot_id=lot["lot_id"], shares=lot["shares"],
                        dataset_sha256=snapshot, entered_on=session, available_on=session, due_on=due,
                        first_lot_appearance=session, last_lot_appearance=session, first_disappearance=None,
                        entry_open=opening, entry_quote=quote, entry_budget=budget, entry_principal=principal,
                        entry_commission=entry_commission, entry_slippage=entry_slippage, entry_sale_tax=ZERO,
                        entry_cost_amount=entry_cost, entry_cost_rounding_residual=_difference(entry_cost,
                            sum_decimal_values((entry_commission, entry_slippage))),
                        entry_cash_debit=debit, entry_cash_rounding=_difference(debit, budget))
            else:
                _require(set(current) <= {lot["lot_id"] for lot in previous_lots},
                         "saved unexplained acquisition or reappearance")
                _require(list(current) == [lot["lot_id"] for lot in previous_lots if lot["lot_id"] in current],
                         "saved surviving lot order changed")
                for old in previous_lots:
                    item = investments[old["code"]]
                    retained = current.get(old["lot_id"])
                    if retained is not None:
                        _require(all(old[key] == retained[key] for key in LOT_FIELDS - {"mark", "mark_date"}),
                                 "unsupported saved lot mutation")
                        _require(_day(retained["mark_date"]) >= _day(old["mark_date"]), "saved mark date regressed")
                        item["last_lot_appearance"] = session
                        continue
                    _require(book["as_of"] >= _day(old["due_on"]), "ordinary saved sale precedes due date")
                    opening, quote = _quote(quotes, old["code"], session)
                    tax = Decimal(str(total_bp(KOSPI, book["as_of"], days)))
                    _require(cost + tax <= 10000, "sale costs exceed notional")
                    gross = old["shares"] * opening
                    receipt = old["shares"] * opening * (ONE - (cost + tax) / 10000)
                    before = cash
                    cash += receipt
                    credited = _difference(cash, before)
                    exit_commission, exit_slippage, sale_tax = (gross * value / 10000
                                                               for value in (commission, slippage, tax))
                    exit_cost = _difference(gross, receipt)
                    gross_pnl = _difference(gross, item["entry_principal"])
                    modeled_pnl = _difference(receipt, budget)
                    fees = sum_decimal_values((item["entry_commission"], item["entry_slippage"],
                                              exit_commission, exit_slippage, sale_tax))
                    cost_residual = _difference(modeled_pnl, _difference(gross_pnl, fees))
                    cash_residual = _difference(_difference(credited, item["entry_cash_debit"]), modeled_pnl)
                    item.update(first_disappearance=session, exit_open=opening, exit_quote=quote,
                        exit_gross_proceeds=gross, exit_commission=exit_commission, exit_slippage=exit_slippage,
                        exit_sale_tax=sale_tax, sale_tax_bps=tax, exit_cost_amount=exit_cost,
                        exit_cost_rounding_residual=_difference(exit_cost,
                            sum_decimal_values((exit_commission, exit_slippage, sale_tax))),
                        formula_net_exit_cash=receipt, credited_net_exit_cash=credited,
                        exit_cash_rounding=_difference(credited, receipt), gross_price_pnl=gross_pnl,
                        total_cost_components=fees, modeled_cost_rounding_residual=cost_residual,
                        net_modeled_pnl=modeled_pnl, cash_rounding_residual=cash_residual,
                        net_accounted_pnl=_difference(credited, item["entry_cash_debit"]),
                        delayed_exit_sessions=days.index(book["as_of"]) - days.index(_day(due)),
                        delayed_exit_cause="unknown", first_executable_exit_verified=False)
                    _require(sum_decimal_values((gross_pnl, fees.copy_negate(), cost_residual)) == modeled_pnl,
                             "modeled gross/cost/net identity failed")
            _require(cash == book["cash"], "saved cash differs from ordered Decimal28 entry/exit arithmetic")
            inventory = sum_decimal_values(tuple(lot["shares"] * lot["mark"] for lot in book["lots"]))
            nav = sum_decimal_values((cash, inventory))
            path.append(dict(date=session, cash=cash, marked_inventory_value=inventory, nav=nav))
            previous_lots, previous_events = book["lots"], book["events"]
        _require(set(investments) == set(chosen) and not previous_lots
                 and all(item["first_disappearance"] for item in investments.values()),
                 "all selected ordinary episodes must be closed")
        _require(type(row.get("raw_metrics")) is dict and type(row["raw_metrics"].get("path")) is list,
                 "saved raw NAV path required")
        raw_path = row["raw_metrics"]["path"]
        _require(len(raw_path) == len(path) and all(saved.get("date") == point["date"]
                 and _amount(saved.get("nav")) == point["nav"] for saved, point in zip(raw_path, path)),
                 "saved raw NAV differs from exact inventory/cash aggregation")
        total_pnl = _difference(path[-1]["nav"], ONE)
        _require(_amount(row["filled_capital"]) == Decimal(len(chosen)) / 20
                 and _amount(row["assigned_capital"]) == Decimal(ceiling) / 20,
                 "saved capital differs from original target budget")
        principal = sum_decimal_values(tuple(item["entry_principal"] for item in investments.values()))
        _require(_amount(row["filled_principal"]) == principal
                 and _amount(row["entry_cost_amount"]) == _difference(Decimal(len(chosen)) / 20, principal),
                 "saved entry principal/cost aggregate changed")
        _require(all(type(row.get(key)) is int for key in ("filled_entries", "ordinary_closed_trades",
                     "terminal_open_lots", "terminal_open_investments")) and row["filled_entries"] == len(chosen)
                 and row.get("ordinary_closed_trades") == len(chosen)
                 and row.get("terminal_open_lots") == row.get("terminal_open_investments") == 0,
                 "saved cohort count diagnostics changed")
        _require(sum_decimal_values(tuple(item["net_accounted_pnl"] for item in investments.values())) == total_pnl,
                 "all-target PnL differs from full saved terminal NAV")
        return dict(schema=SCHEMA, arm=row["arm"], group_id=row["group_id"], formation_on=formation.isoformat(),
            decision_on=decision.isoformat(), chosen_codes=chosen, execution_codes=execution_codes,
            first_book_on=path[0]["date"], last_book_on=path[-1]["date"], initial_cash=ONE,
            nominal_budget_per_target=budget, investments=[investments[code] for code in chosen],
            daily_reconciliation=path, terminal_nav=path[-1]["nav"], total_book_pnl=total_pnl,
            modeled_net_pnl=sum_decimal_values(tuple(item["net_modeled_pnl"] for item in investments.values())),
            cash_rounding_residual=sum_decimal_values(tuple(item["cash_rounding_residual"] for item in investments.values())),
            all_targets_audited=True, cash_and_nav_reconciled=True, sale_tax_convention="existing KOSPI total_bp with T+2 calendar",
            decimal_convention="producer Decimal28 ordered operations; exact aggregation; explicit residuals; no tolerance",
            saved_event_ids=previous_events, saved_marks_reverified=False, delay_causes_verified=False,
            source_truth_certified=False, execution_certified=False, price_basis_certified=False,
            unmodeled_automatic_assets="unknown", actual_investment_total_return=False, replayed=False)
