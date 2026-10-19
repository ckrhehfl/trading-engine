"""Describe pinned saved TREND books without loading, selecting or replaying.

Original investments, including linked LG/LX components, are the stock unit.
Cash is credited in the producer's Decimal-28 lot order; the exact incremental
credit and its difference from the sale formula are retained separately. Saved
book marks/action arithmetic and pinned source truth remain caller reliance.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext
import hashlib
import json
import re

from research.activity_accounting import Lot, sum_decimal_values
from research.activity_book import ActivityBook
from research.activity_holding_inputs import _decode_typed
from research.activity_preflight import require
from research.activity_timing import SessionLagPolicy
from research.krx_tax_schedule import KOSPI, total_bp


ZERO, ONE = Decimal(0), Decimal(1)
RAW_CODES = frozenset({"003550", "383800"})
ADJUSTED = "KIS_adjusted_daily_declared"
RAW = "KRX_unadjusted_raw"
SCHEMA = "price-trend-saved-concentration-v1"


def _difference(left: Decimal, right: Decimal) -> Decimal:
    return sum_decimal_values((left, right.copy_negate()))


def _pin(value: str) -> None:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "saved input SHA-256 required")


def _decimal(value, *, positive=False) -> Decimal:
    require(type(value) is Decimal and value.is_finite() and (not positive or value > 0),
            "finite exact Decimal required")
    return value


@dataclass(frozen=True)
class SavedOpening:
    opening: Decimal | None
    frozen: bool
    source_sha256: str
    basis: str
    state: str = "observed"

    def __post_init__(self) -> None:
        _pin(self.source_sha256)
        require(type(self.frozen) is bool and self.basis in (ADJUSTED, RAW), "explicit saved opening basis/state required")
        require(self.state in ("observed", "locked", "frozen", "absent_requested", "no_trade", "unresolved"),
                "explicit saved missingness required")
        if self.opening is not None:
            _decimal(self.opening, positive=True)
        require((self.state not in ("observed", "locked", "frozen") or self.opening is not None)
                and (self.state not in ("absent_requested", "no_trade", "unresolved") or self.opening is None)
                and self.frozen == (self.state not in ("observed", "locked")), "opening disagrees with saved state")


def project_saved_openings(artifact: dict, coordinates: frozenset | None = None, *, source_sha256: str) -> dict:
    """Project only retained opens; no full holding-panel reconstruction.

    The caller verifies artifact bytes/pins before decoding. Unknown rows stay
    unresolved and are refused if consumed. None projects all saved requested
    coordinates for a caller streaming each large pinned artifact only once.
    This checks consumed storage/state, not the original audit/source proof.
    """
    _pin(source_sha256)
    require(coordinates is None or (type(coordinates) is frozenset and all(type(key) is tuple and len(key) == 2
            and type(key[0]) is str and type(key[1]) is date for key in coordinates)), "explicit saved coordinate set required")
    require(type(artifact) is dict, "saved quote object required")
    schema = artifact.get("schema")
    if schema == "activity-quote-extension-envelope-v1":
        require(type(artifact.get("holding_input_audit")) is dict, "saved extension audit required")
        artifact = artifact["holding_input_audit"]
        schema = artifact.get("schema")
    raw = schema == "activity-raw-episode-quotes-v1"
    require(raw or schema == "activity-holding-price-preflight-v1:holding-input-audit-v1", "known saved quote schema required")
    require(type(artifact.get("rows")) is list, "saved quote rows required")
    result = {}
    for row in artifact["rows"]:
        require(type(row) is dict and type(row.get("code")) is str, "saved quote row coordinate required")
        text = row.get("requested_bas_dd" if raw else "observation_date")
        require(type(text) is str, "saved observation date text required")
        day = date.fromisoformat(text)
        require(text == (day.strftime("%Y%m%d") if raw else str(day)), "canonical saved date required")
        key = row["code"], day
        if coordinates is not None and key not in coordinates:
            continue
        require(key not in result, "duplicate saved opening coordinate")
        state, opening = row.get("state"), None
        issues = row.get("issues")
        require(type(issues) is list, "saved quote issue array required")
        if raw:
            require(row["code"] in RAW_CODES, "raw projection is limited to LG/LX")
            if row.get("status") == "observed" and not issues:
                if state == "observed_no_trade_zero_prices":
                    state = "no_trade"
                elif state in ("observed", "locked", "frozen"):
                    require(type(row.get("open")) is str, "raw opening must be Decimal text")
                    opening = _decimal(Decimal(row["open"]), positive=True)
                else:
                    raise ValueError("unknown observed raw state")
            elif issues == ["target_absent_cause_unknown"]:
                state = "absent_requested"
            elif issues == ["mixed_or_active_zero_prices"]:
                # Existing v2 raw-view policy: zero OHL/activity and a positive
                # reference close is no executable quote, never a frozen price.
                values = dict(row.get("canonical_decimal_strings", []))
                no_trade = all(values.get(name) == "0" for name in
                    ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL"))
                close = values.get("TDD_CLSPRC")
                state = "no_trade" if no_trade and type(close) is str and _decimal(Decimal(close), positive=True) else "unresolved"
            else:
                state = "unresolved"
        elif state == "absent_requested":
            require(row.get("typed_values") is None, "saved absence cannot contain typed prices")
        elif state in ("observed", "locked", "frozen") and not issues:
            typed = row.get("typed_values")
            require(type(typed) is list and len(typed) == 7, "seven saved typed fields required")
            require(_decode_typed(typed[0]) == day.strftime("%Y%m%d"), "typed opening date mismatch")
            value = _decode_typed(typed[1])
            require(type(value) is str, "only saved SQLite TEXT is a Decimal price")
            opening = _decimal(Decimal(value), positive=True)
        else:
            state = "unresolved"
        result[key] = SavedOpening(opening, state not in ("observed", "locked"), source_sha256,
                                   RAW if raw else ADJUSTED, state)
    return result


def _opening(openings: Mapping, code: str, session: date) -> SavedOpening:
    row = openings.get((code, session))
    require(type(row) is SavedOpening and replace(row) == row, "requested saved opening is missing or invalid")
    require(row.state != "unresolved", "unresolved saved opening cannot support attribution")
    require(row.basis == (RAW if code in RAW_CODES else ADJUSTED), "LG/LX raw priority or ordinary adjusted basis mismatch")
    return row


def _unchanged_lot(old: Lot, current: Lot) -> None:
    require(replace(current, mark=old.mark, mark_date=old.mark_date, carried_value=old.carried_value) == old,
            "saved lot identity changed without supported LG/LX lineage")
    require(current.carried_value in (old.carried_value, None) and current.mark_date >= old.mark_date,
            "saved pending component value changed")


def _split(old: Lot, current: dict, new_events: tuple[str, ...], session: date) -> tuple[Lot, Lot]:
    retained = current.get(old.lot_id)
    require(old.code == "003550" and old.investment_id is None and retained is not None,
            "only the saved LG/LX compulsory split is supported")
    matches = []
    for event_id in new_events:
        identity = json.dumps((event_id, old.lot_id), ensure_ascii=True, separators=(",", ":"))
        investment = "spin-off-investment:" + identity
        component = current.get("spin-off-component:" + identity)
        if component is not None and retained.investment_id == component.investment_id == investment:
            matches.append(component)
    require(len(matches) == 1, "LG/LX components need one deterministic saved event/investment link")
    component = matches[0]
    require(retained.code == "003550" and component.code == "383800"
            and all(lot.entered_on == old.entered_on and lot.due_on == old.due_on
                and lot.dataset_sha256 == old.dataset_sha256 and lot.available_on >= session
                for lot in (retained, component)), "saved spin-off component origin/terms mismatch")
    return retained, component


def attribute_saved_cohort(books: tuple[ActivityBook, ...], *, lag: int, formation_on: date, decision_on: date,
    selected_codes: tuple[str, ...], target_ceiling: int, calendar: tuple[date, ...], parameters: dict,
    openings: Mapping, quote_snapshot_sha256: str) -> dict:
    """Attribute one existing single-entry cohort, preserving ordered cash.

    Daily marks and the already verified LG/LX ratios/rights are saved-book
    reliance. New acquisitions, unrelated inventory transitions, final cash
    settlements, changed shares and unexplained cash are rejected. No tolerance
    is used. Component assets remain one original investment in all rankings.
    """
    _pin(quote_snapshot_sha256)
    require(type(calendar) is tuple, "immutable saved calendar required")
    policy = SessionLagPolicy(lag, paired=True)
    policy._calendar(calendar)
    require(type(formation_on) is date and type(decision_on) is date and formation_on in calendar,
            "explicit saved formation/decision required")
    require(policy.execution_on(calendar, calendar.index(formation_on)) == decision_on, "saved lag/decision mismatch")
    require(type(parameters) is dict and isinstance(openings, Mapping), "explicit parameters/openings required")
    for name in ("slots", "lookback", "holding_sessions"):
        require(type(parameters.get(name)) is int and parameters[name] > 0, "positive original parameter geometry required")
    slots, holding = parameters["slots"], parameters["holding_sessions"]
    require(type(parameters.get("seed")) is int and parameters["seed"] >= 0, "original deterministic execution seed required")
    end = date.fromisoformat(parameters["end"])
    require(str(end) == parameters["end"] and end in calendar, "saved end must be a canonical calendar date")
    indices = policy.formation_indices(calendar, lookback=parameters["lookback"], holding_sessions=holding,
                                      end_index=calendar.index(end))
    require(calendar.index(formation_on) in indices, "formation is outside original paired geometry")
    require(type(selected_codes) is tuple and len(set(selected_codes)) == len(selected_codes)
            and all(type(code) is str and re.fullmatch(r"[0-9A-Z]{6}", code) for code in selected_codes), "unique ordered saved targets required")
    require(type(target_ceiling) is int and 0 <= len(selected_codes) <= target_ceiling <= slots,
            "unchanged original assigned target ceiling required")
    require(type(books) is tuple and books and all(type(book) is ActivityBook and replace(book) == book for book in books),
            "valid immutable saved book path required")
    require(tuple(book.as_of for book in books) == calendar[parameters["lookback"] + 1:calendar.index(end) + 1],
            "complete original saved book session path required")
    entry_index = next(index for index, book in enumerate(books) if book.as_of == decision_on)
    require(all(book.cash == ONE and not book.lots for book in books[:entry_index]), "cash-only pre-entry NAV one required")
    with localcontext() as context:
        context.prec = 28
        costs = []
        for name in ("commission_bps_per_side", "slippage_bps_per_side"):
            value = parameters.get(name)
            require(type(value) in (int, float, str, Decimal) and type(value) is not bool, "original cost scalar required")
            amount = Decimal(str(value))
            require(amount.is_finite() and amount >= 0, "finite nonnegative original costs required")
            costs.append(amount)
        cost = sum(costs, ZERO)
        require(cost <= 10000, "original costs cannot exceed notional")
        entry, cash, allocation = books[entry_index], ONE, ONE / slots
        actual = {lot.lot_id: lot for lot in entry.lots}
        origins, rows, expected_ids = {}, {}, []
        due = calendar[calendar.index(decision_on) + holding]
        execution_codes = tuple(sorted(selected_codes, key=lambda code:
            hashlib.sha256(f"{parameters['seed']}:{formation_on}:{code}".encode()).digest()))
        for code in execution_codes:
            quote = _opening(openings, code, decision_on)
            lot_id, budget = f"{code}:{decision_on}", min(cash, allocation)
            filled = quote.opening is not None and not quote.frozen and budget > 0
            lot = actual.get(lot_id)
            require((lot is not None) == filled, "saved fill disagrees with original opening/budget")
            before = cash
            if filled:
                require(lot.code == code and lot.shares == budget / (quote.opening * (ONE + cost / 10000))
                        and lot.entered_on == lot.available_on == decision_on and lot.due_on == due
                        and lot.dataset_sha256 == quote_snapshot_sha256 and lot.investment_id is None
                        and lot.carried_value is None, "original saved share/budget identity mismatch")
                cash -= budget
                expected_ids.append(lot_id)
                origins[lot_id] = code
            debit = _difference(before, cash)
            rows[code] = dict(original_code=code, original_lot_id=lot_id if filled else None,
                filled_entries=int(filled), nominal_entry_budget=budget if filled else ZERO, entry_cash_debit=debit,
                formula_net_exit_cash=ZERO, credited_net_exit_cash=ZERO, cash_rounding_adjustment=ZERO,
                terminal_marked_value=ZERO, sale_count=0, exit_sessions=(), component_codes=(code,) if filled else ())
        require(tuple(lot.lot_id for lot in entry.lots) == tuple(expected_ids) and cash == entry.cash,
                "saved entry order/cash differs from exact original allocation")
        previous = entry
        for book in books[entry_index + 1:]:
            require(book.applied_event_ids[:len(previous.applied_event_ids)] == previous.applied_event_ids,
                    "saved event lineage must be monotone")
            current = {lot.lot_id: lot for lot in book.lots}
            expanded = []
            for old in previous.lots:
                retained = current.get(old.lot_id)
                if retained is not None and old.investment_id != retained.investment_id:
                    pair = _split(old, current, book.applied_event_ids[len(previous.applied_event_ids):], book.as_of)
                    expanded.extend(pair)
                    origins[pair[1].lot_id] = origins[old.lot_id]
                    rows[origins[old.lot_id]]["component_codes"] = ("003550", "383800")
                else:
                    expanded.append(old)
                    if retained is not None:
                        _unchanged_lot(old, retained)
            require(set(current) <= {lot.lot_id for lot in expanded}, "saved book contains an unexplained acquisition/component")
            require(tuple(current) == tuple(lot.lot_id for lot in expanded if lot.lot_id in current),
                    "saved surviving lot order changed")
            cash = previous.cash
            for old in expanded:
                quote = None
                if book.as_of >= max(old.due_on, old.available_on):
                    quote = _opening(openings, old.code, book.as_of)
                executable = quote is not None and quote.opening is not None and not quote.frozen
                if old.lot_id in current:
                    require(not executable, "due saved lot survived an executable ordinary opening")
                    continue
                require(executable, "saved disappearance requires an executable due ordinary sale")
                tax = Decimal(str(total_bp(KOSPI, book.as_of, calendar)))
                require(cost + tax <= 10000, "sale costs exceed notional")
                receipt = old.shares * quote.opening * (ONE - (cost + tax) / 10000)
                before = cash
                cash += receipt
                credited = _difference(cash, before)
                row = rows[origins[old.lot_id]]
                row["formula_net_exit_cash"] = sum_decimal_values((row["formula_net_exit_cash"], receipt))
                row["credited_net_exit_cash"] = sum_decimal_values((row["credited_net_exit_cash"], credited))
                row["cash_rounding_adjustment"] = _difference(row["credited_net_exit_cash"], row["formula_net_exit_cash"])
                row["sale_count"] += 1
                row["exit_sessions"] += (book.as_of,)
            require(cash == book.cash, "saved daily cash differs from ordered Decimal-28 ordinary credits")
            previous = book
        for lot in books[-1].lots:
            row = rows[origins[lot.lot_id]]
            row["terminal_marked_value"] = sum_decimal_values((row["terminal_marked_value"], lot.marked_value))
        assigned = Decimal(target_ceiling) / slots
        for row in rows.values():
            row["pnl"] = sum_decimal_values((row["credited_net_exit_cash"], row["terminal_marked_value"], row["entry_cash_debit"].copy_negate()))
            row["assigned_capital_return_contribution"] = None if assigned == 0 else row["pnl"] / assigned
        total = _difference(books[-1].nav, ONE)
        require(sum_decimal_values(tuple(row["pnl"] for row in rows.values())) == total, "original investment PnL must reconcile saved NAV exactly")
        return dict(schema=SCHEMA + ":cohort", lag=lag, formation_on=formation_on, decision_on=decision_on,
            selected_codes=selected_codes, target_ceiling=target_ceiling, assigned_capital=assigned,
            filled_entries=sum(row["filled_entries"] for row in rows.values()), total_book_pnl=total,
            budget_normalized_pnl=None if assigned == 0 else total / assigned,
            execution_codes=execution_codes, investments=tuple(rows[code] for code in selected_codes),
            calendar_years=tuple(range(calendar[0].year, end.year + 1)),
            formation_schedule=tuple(calendar[index] for index in indices), slots=slots,
            original_investment_components_linked=True, stock_unit="original issuer/investment; LG/LX components remain grouped",
            cash_convention="producer Decimal28 ordered cash credits; exact credited deltas retained separately from sale formula",
            saved_marks_and_action_terms_reverified=False, source_truth_certified=False,
            actual_investment_total_return=False, replayed=False)


def _concentration(rows: tuple[dict, ...], assigned: Decimal, total: Decimal) -> dict:
    require(sum_decimal_values(tuple(row["pnl"] for row in rows)) == total, "concentration units must reconcile total PnL")
    positive = sum_decimal_values(tuple(row["pnl"] for row in rows if row["pnl"] > 0))
    absolute = sum_decimal_values(tuple(row["pnl"].copy_abs() for row in rows))
    ordered = sorted(rows, key=lambda row: (row["pnl"].copy_negate(), row["unit"]))
    enriched = tuple({**row, "positive_profit_share": None if positive == 0 else max(row["pnl"], ZERO) / positive,
        "absolute_pnl_share": None if absolute == 0 else row["pnl"].copy_abs() / absolute,
        "signed_net_pnl_share": None if total == 0 else row["pnl"] / total,
        "original_assigned_capital_return_contribution": None if assigned == 0 else row["pnl"] / assigned} for row in ordered)
    leaders = tuple(row for row in enriched if row["pnl"] > 0)
    subtraction = {}
    for count in (1, 3):
        chosen = leaders[:count]
        removed = sum_decimal_values(tuple(row["pnl"] for row in chosen))
        residual = _difference(total, removed)
        subtraction[f"top{count}"] = dict(units=tuple(row["unit"] for row in chosen), contribution_pnl=removed,
            positive_profit_share=None if positive == 0 else removed / positive,
            absolute_pnl_share=None if absolute == 0 else removed / absolute,
            signed_net_pnl_share=None if total == 0 else removed / total,
            residual_pnl=residual, original_assigned_capital=assigned,
            residual_budget_normalized_pnl=None if assigned == 0 else residual / assigned)
    return dict(rows=enriched, positive_profit_pnl=positive, absolute_pnl=absolute, net_pnl=total,
                top_positive_contribution_subtraction=subtraction,
                interpretation="arithmetic subtraction from the same saved PnL with original denominator; no exclusion/replay/compounding")


def summarize_concentration(cohorts: tuple[dict, ...]) -> dict:
    """Describe paired formation-vintage/year and original-issuer concentration."""
    require(type(cohorts) is tuple and cohorts and all(type(row) is dict and row.get("schema") == SCHEMA + ":cohort" for row in cohorts),
            "immutable existing scalar cohort attributions required")
    years = cohorts[0]["calendar_years"]
    schedule = cohorts[0]["formation_schedule"]
    require(type(years) is tuple and years and years == tuple(range(years[0], years[-1] + 1))
            and type(schedule) is tuple and schedule == tuple(sorted(set(schedule))) and schedule,
            "complete original calendar/formation population required")
    require(all(row["calendar_years"] == years and row["formation_schedule"] == schedule
                and type(row["lag"]) is int and row["lag"] in (1, 2) for row in cohorts),
            "same complete calendar-year/formation population required")
    keys = [(row["lag"], row["formation_on"]) for row in cohorts]
    require(len(set(keys)) == len(keys) and {lag for lag, _ in keys} == {1, 2}, "unique complete paired lag cohorts required")
    formations = tuple(sorted(day for lag, day in keys if lag == 1))
    require(formations == schedule == tuple(sorted(day for lag, day in keys if lag == 2)), "paired formation population mismatch")
    for formation in formations:
        left, right = (next(row for row in cohorts if row["lag"] == lag and row["formation_on"] == formation) for lag in (1, 2))
        require(all(left[name] == right[name] for name in ("selected_codes", "target_ceiling", "assigned_capital", "slots")),
                "paired original target/budget geometry changed")
    with localcontext() as context:
        context.prec = 28
        arms = {}
        for lag in (1, 2):
            values = tuple(sorted((row for row in cohorts if row["lag"] == lag), key=lambda row: row["formation_on"]))
            for row in values:
                _decimal(row["assigned_capital"])
                _decimal(row["total_book_pnl"])
                require(type(row["slots"]) is int and row["slots"] > 0 and type(row["target_ceiling"]) is int
                        and 0 <= row["target_ceiling"] <= row["slots"]
                        and row["assigned_capital"] == Decimal(row["target_ceiling"]) / row["slots"],
                        "scalar original assigned denominator mismatch")
                require(type(row["investments"]) is tuple
                        and tuple(item["original_code"] for item in row["investments"]) == row["selected_codes"],
                        "scalar original investment/target population mismatch")
                for item in row["investments"]:
                    _decimal(item["pnl"])
                require(row["assigned_capital"] >= 0 and sum_decimal_values(tuple(item["pnl"] for item in row["investments"])) == row["total_book_pnl"],
                        "scalar original investment/cohort PnL mismatch")
            assigned = sum_decimal_values(tuple(row["assigned_capital"] for row in values))
            total = sum_decimal_values(tuple(row["total_book_pnl"] for row in values))
            vintage = tuple(dict(unit=str(row["formation_on"]), formation_on=row["formation_on"],
                assigned_capital=row["assigned_capital"], filled_entries=row["filled_entries"], pnl=row["total_book_pnl"]) for row in values)
            annual = []
            for year in years:
                members = tuple(row for row in values if row["formation_on"].year == year)
                budget = sum_decimal_values(tuple(row["assigned_capital"] for row in members))
                annual.append(dict(unit=str(year), formation_year=year, cohort_count=len(members), assigned_capital=budget,
                    status="no_formations" if not members else "zero_assigned_capital" if budget == 0 else "observed_cohort_vintages",
                    pnl=sum_decimal_values(tuple(row["total_book_pnl"] for row in members))))
            codes = sorted({item["original_code"] for row in values for item in row["investments"]})
            stock = tuple(dict(unit=code, original_code=code,
                targeted_cohort_count=sum(item["original_code"] == code for row in values for item in row["investments"]),
                filled_entries=sum(item["filled_entries"] for row in values for item in row["investments"] if item["original_code"] == code),
                pnl=sum_decimal_values(tuple(item["pnl"] for row in values for item in row["investments"] if item["original_code"] == code))) for code in codes)
            arms[f"D{lag}"] = dict(cohort_count=len(values), active_formations=sum(row["assigned_capital"] > 0 for row in values),
                filled_entries=sum(row["filled_entries"] for row in values), original_assigned_capital=assigned, total_book_pnl=total,
                budget_normalized_pnl=None if assigned == 0 else total / assigned,
                by_formation_vintage=_concentration(vintage, assigned, total),
                by_formation_year=_concentration(tuple(annual), assigned, total),
                by_original_issuer=_concentration(stock, assigned, total))
        return dict(schema=SCHEMA, arms=arms, time_unit="formation cohort vintage; formation-year PnL is not an annual investment return",
            original_denominator_preserved=True, new_evaluations=0, statistical_test_performed=False,
            replayed=False, promotion_allowed=False, actual_investment_total_return=False)
