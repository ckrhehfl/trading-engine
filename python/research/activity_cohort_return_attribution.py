"""Describe versioned named issuers' PnL in one existing immutable cohort replay.

This helper neither selects nor replays a portfolio. It follows original lots
and the existing opening-price/cost/tax convention, leaving approved unmodeled
assets unknown. The residual is an attribution of the same book, not a strategy
that excluded those issuers. Supported action lineage for the three v1 issuers
or four v2/five v3 issuers is deliberately outside this narrow diagnostic.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

from research.activity_accounting import sum_decimal_values
from research.activity_book import ActivityBook, CompulsorySpinOff, CompulsoryStockExchange, FinalCashPayment
from research.activity_cohort_selection import CohortSelection
from research.activity_partition_selection import PartitionSelection
from research.activity_cohort_return_policy import ACTION_COVERAGE_SCOPE, SCOPE_DECISION_RECORDED_ON, attribution_codes
from research.activity_preflight import require
from research.activity_replay import SyntheticReplay, _number, _quote
from research.krx_tax_schedule import KOSPI, total_bp


KNOWN_ISSUER_CODES = ("003670", "005070", "006400")
ZERO, ONE = Decimal(0), Decimal(1)
ATTRIBUTION_KEY = "known_issuer_price_return_attribution"


def _difference(left: Decimal, right: Decimal) -> Decimal:
    return sum_decimal_values((left, right.copy_negate()))


def require_supported_attribution_actions(events: tuple, *, return_policy_json: bytes | None = None) -> None:
    """Refuse any supplied action that would need new affected-lot lineage."""
    require(type(events) is tuple, "immutable attribution events required")
    for event in events:
        if type(event) is FinalCashPayment:
            codes = {event.distribution.code}
        else:
            require(type(event) in (CompulsorySpinOff, CompulsoryStockExchange),
                "unknown action cannot support issuer attribution")
            codes = {event.old_basis.code, event.new_basis.code}
        require(not codes.intersection(attribution_codes(return_policy_json)),
            "supported action touching known unmodeled asset issuer needs explicit attribution lineage")


def _unmodeled_assets(code: str | None = None, *, return_policy_json: bytes | None = None) -> dict:
    codes = attribution_codes(return_policy_json)
    result = {} if code == "006800" or (code == "086280" and code in codes) else dict(automatic_subscription_rights_cash=None, rights_cash_model="not_modeled")
    if "006800" in codes and code in (None, "006800"):
        result.update(automatic_stock_dividend_shares=None, automatic_stock_dividend_fractional_cash=None,
            stock_dividend_assets_model="not_modeled")
    if "086280" in codes and code in (None, "086280"):
        result.update(automatic_bonus_shares=None, automatic_bonus_fractional_cash=None,
            bonus_share_assets_model="not_modeled")
    if "086280" in codes:
        result.update(action_coverage_scope=ACTION_COVERAGE_SCOPE,
            scope_decision_recorded_on=SCOPE_DECISION_RECORDED_ON)
    return result


def _result(by_code: dict, assigned: Decimal, total_pnl: Decimal, *, return_policy_json: bytes | None = None) -> dict:
    known = sum_decimal_values(tuple(row["pnl"] for row in by_code.values()))
    remainder = _difference(total_pnl, known)
    require(sum_decimal_values((known, remainder)) == total_pnl, "issuer attribution must reconcile same-book PnL")
    return {"by_code": by_code, "known_issuer_pnl": known, "remainder_pnl": remainder,
        "total_book_pnl": total_pnl, "assigned_capital": assigned,
        "known_issuer_return_contribution": None if assigned == 0 else known / assigned,
        "remainder_return_contribution": None if assigned == 0 else remainder / assigned,
        "denominator": "same original assigned acquisition capital; unfilled budgets are not redistributed",
        "interpretation": "descriptive attribution of the same immutable replay; not issuer exclusion or a replayed strategy",
        "actual_investment_return": False, **_unmodeled_assets(return_policy_json=return_policy_json)}


def attribute_known_issuer_returns(
    replay: SyntheticReplay, selection: CohortSelection, quotes, parameters: dict, *, events: tuple = (),
    return_policy_json: bytes | None = None,
) -> dict:
    """Retain the strict original BS declaration boundary for existing callers."""
    require(type(selection) is CohortSelection, "original cohort declaration required")
    projected = PartitionSelection(selection.formation_on, selection.decision_on, selection.selection_at,
        selection.chosen_codes, selection.source_population_count, selection.source_partition_sha256)
    return attribute_selected_issuer_returns(replay, projected, selection.target_ceiling, quotes, parameters,
        events=events, return_policy_json=return_policy_json)


def attribute_selected_issuer_returns(
    replay: SyntheticReplay, selection: PartitionSelection, target_ceiling: int, quotes, parameters: dict, *,
    events: tuple = (), return_policy_json: bytes | None = None,
) -> dict:
    """Follow original entry, ordinary disappearance and terminal mark only.

    Each cohort begins cash-only at one and has one active formation. Verify
    every initial fill's budget/share identity, then follow only the versioned
    affected original lot ids. Simultaneous sales are priced separately from
    their own shares/openings; a whole-book cash delta is never assigned to one
    issuer. No synthetic asset value or second replay is produced.
    """
    require(type(replay) is SyntheticReplay and type(replay.books) is tuple and bool(replay.books),
        "existing immutable replay books required for issuer attribution")
    require(type(selection) is PartitionSelection and replace(selection) == selection,
        "explicit valid selected-target projection required")
    require(type(target_ceiling) is int and len(selection.eligible_codes) <= target_ceiling <= parameters["slots"]
        and target_ceiling >= 0, "assigned original target ceiling required")
    require_supported_attribution_actions(events, return_policy_json=return_policy_json)
    calendar = quotes.calendar
    require(type(calendar) is tuple and all(type(day) is date for day in calendar)
        and all(a < b for a, b in zip(calendar, calendar[1:])), "ordered immutable attribution calendar required")
    end = calendar.index(date.fromisoformat(parameters["end"]))
    days = calendar[parameters["lookback"] + 1:end + 1]
    require(tuple(book.as_of for book in replay.books) == days, "complete original attribution book path required")
    require(all(type(book) is ActivityBook and replace(book) == book for book in replay.books),
        "valid immutable attribution books required")
    positions = {day: index for index, day in enumerate(calendar)}
    book_positions = {book.as_of: index for index, book in enumerate(replay.books)}
    decision = selection.decision_on
    require(decision in book_positions, "original entry must be in replay path")
    require(all(not book.lots and book.cash == ONE for book in replay.books[:book_positions[decision]]),
        "cohort must retain cash-only NAV one before its original entry")
    require(all(set(codes) == set(selection.eligible_codes) if formation == selection.formation_on else not codes
        for formation, codes in replay.selections), "attribution requires the unchanged single active target declaration")
    require(sum(formation == selection.formation_on for formation, _ in replay.selections) == 1,
        "one original active formation required for attribution")
    selected = next(codes for formation, codes in replay.selections if formation == selection.formation_on)
    with localcontext() as context:
        context.prec = 28
        cost = sum((_number(parameters[key], key) for key in
            ("commission_bps_per_side", "slippage_bps_per_side")), ZERO)
        slots = parameters["slots"]
        require(type(slots) is int and slots > 0, "positive original slot count required")
        allocation, cash = ONE / slots, ONE
        entry = replay.books[book_positions[decision]]
        actual = {lot.lot_id: lot for lot in entry.lots}
        expected_ids, budgets = set(), {}
        due = calendar[positions[decision] + parameters["holding_sessions"]]
        for code in selected:
            opening, _, frozen, _ = _quote(quotes.panel[code], positions[decision])
            lot_id = f"{code}:{decision}"
            lot = actual.get(lot_id)
            budget = min(cash, allocation)
            filled = bool(opening and not frozen and budget > 0)
            require((lot is not None) == filled, "actual entry must agree with original executable quote and cash")
            budgets[code] = budget if filled else ZERO
            if not filled:
                continue
            expected_ids.add(lot_id)
            require(lot.code == code and lot.shares == budget / (opening * (ONE + cost / 10000))
                and lot.entered_on == lot.available_on == decision and lot.due_on == due
                and lot.dataset_sha256 == quotes.quote_snapshot_sha256
                and lot.investment_id is None and lot.carried_value is None,
                "actual entry must retain original lot/share/budget identity")
            cash -= budget
        require(set(actual) == expected_ids and entry.cash == cash,
            "original entry book must retain every actual fill and unfilled cash")
        by_code = {}
        for code in attribution_codes(return_policy_json):
            lot_id = f"{code}:{decision}"
            initial = actual.get(lot_id)
            previous, sold_on, exit_cash = None, None, ZERO
            terminal_value = ZERO
            for book in replay.books:
                matching = tuple(lot for lot in book.lots if lot.code == code or lot.lot_id == lot_id)
                require(len(matching) <= 1 and all(lot.lot_id == lot_id and lot.code == code for lot in matching),
                    "unknown affected issuer lot transition cannot support attribution")
                current = matching[0] if matching else None
                if book.as_of < decision:
                    require(current is None, "affected issuer cannot precede original entry")
                    continue
                if book.as_of == decision:
                    require(current == initial, "affected issuer entry trace changed")
                    previous = current
                    if current is not None:
                        _, close, frozen, _ = _quote(quotes.panel[code], positions[book.as_of])
                        require(current.mark == (close if close and not frozen else
                            _quote(quotes.panel[code], positions[decision])[0]) and current.mark_date == decision,
                            "affected entry mark must follow existing observable quote")
                    continue
                if previous is None:
                    require(current is None, "unfilled or sold affected lot cannot appear later")
                    continue
                opening, close, frozen, _ = _quote(quotes.panel[code], positions[book.as_of])
                executable_exit = book.as_of >= previous.due_on and book.as_of >= previous.available_on and opening and not frozen
                if current is None:
                    require(executable_exit and sold_on is None,
                        "affected lot disappearance needs its first executable ordinary sale")
                    tax = Decimal(str(total_bp(KOSPI, book.as_of, calendar)))
                    require(cost + tax <= 10000, "ordinary sale costs cannot exceed notional")
                    exit_cash = previous.shares * opening * (ONE - (cost + tax) / 10000)
                    prior_book = replay.books[book_positions[book.as_of] - 1]
                    if prior_book.applied_event_ids == book.applied_event_ids:
                        # The original engine credits simultaneous ordinary
                        # sales in lot order. Check that aggregate receipt;
                        # never allocate its whole delta to this issuer.
                        remaining_ids = {lot.lot_id for lot in book.lots}
                        expected_cash = prior_book.cash
                        for old in prior_book.lots:
                            if old.lot_id in remaining_ids:
                                continue
                            sale_open, _, sale_frozen, _ = _quote(quotes.panel[old.code], positions[book.as_of])
                            require(book.as_of >= max(old.due_on, old.available_on) and sale_open and not sale_frozen,
                                "same-day ordinary cash receipt needs executable disappeared lots")
                            expected_cash += old.shares * sale_open * (ONE - (cost + tax) / 10000)
                        require(book.cash == expected_cash, "actual ordinary sale cash receipt differs from same replay")
                    sold_on, previous = book.as_of, None
                    continue
                require(not executable_exit, "affected due lot cannot survive an executable ordinary sale")
                require(replace(current, mark=previous.mark, mark_date=previous.mark_date) == previous,
                    "affected original lot/share identity changed without supported lineage")
                expected_mark = close if close and not frozen and book.as_of >= previous.available_on else previous.mark
                expected_day = book.as_of if close and not frozen and book.as_of >= previous.available_on else previous.mark_date
                require(current.mark == expected_mark and current.mark_date == expected_day,
                    "affected original lot mark differs from observable quote trace")
                previous = current
            if previous is not None:
                terminal_value = previous.marked_value
            budget = budgets.get(code, ZERO)
            pnl = sum_decimal_values((exit_cash, terminal_value, budget.copy_negate()))
            assigned = Decimal(target_ceiling) / slots
            by_code[code] = {"targeted": code in selection.eligible_codes, "filled_entries": int(initial is not None),
                "original_lot_id": None if initial is None else lot_id,
                "entry_on": None if initial is None else decision, "exit_on": sold_on,
                "entry_budget": budget, "net_exit_cash": exit_cash,
                "terminal_marked_value": terminal_value, "pnl": pnl,
                "assigned_capital_return_contribution": None if assigned == 0 else pnl / assigned,
                **_unmodeled_assets(code, return_policy_json=return_policy_json)}
        return _result(by_code, Decimal(target_ceiling) / slots,
            _difference(replay.books[-1].nav, ONE), return_policy_json=return_policy_json)


def aggregate_known_issuer_returns(rows: tuple[dict, ...], *, assigned_capital: Decimal, total_pnl: Decimal,
    return_policy_json: bytes | None = None) -> dict:
    """Sum scalar attribution from the same cohorts and original denominator."""
    require(type(rows) is tuple and bool(rows), "nonempty immutable cohort attribution rows required")
    codes = attribution_codes(return_policy_json)
    require(all(set(row[ATTRIBUTION_KEY]["by_code"]) == set(codes) for row in rows),
        "issuer attribution rows must retain the same versioned policy scope")
    with localcontext() as context:
        context.prec = 28
        by_code = {}
        for code in codes:
            values = tuple(row[ATTRIBUTION_KEY]["by_code"][code] for row in rows)
            summary = {field: sum_decimal_values(tuple(row[field] for row in values))
                for field in ("entry_budget", "net_exit_cash", "terminal_marked_value", "pnl")}
            summary.update(targeted_cohort_count=sum(row["targeted"] for row in values),
                filled_entries=sum(row["filled_entries"] for row in values),
                assigned_capital_return_contribution=None if assigned_capital == 0 else summary["pnl"] / assigned_capital,
                **_unmodeled_assets(code, return_policy_json=return_policy_json))
            by_code[code] = summary
        return _result(by_code, assigned_capital, total_pnl, return_policy_json=return_policy_json)
