"""Continuous EF observed-candidate accounts over injected sealed evidence.

The account uses only the registered five-minute OPEN observations. The same
common-support forecasts admit opportunities in each arm. Account gates and
lot sizes can then differ as cash and risk evolve. Quantities are nominal
archived units. Same-clock proceeds and residual order budgets are available
from the next decision only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from fractions import Fraction
from math import isfinite
import re
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_campaign_account import _periods, _risk_observation
from research.intraday_chart_folds import ENTRY_OFFSETS, Key
from research.intraday_chart_management import FIXED_EXIT_MINUTES, UPDATE_MINUTES, HoldingDecision, holding_decision
from research.intraday_chart_model_inputs import ADMISSION_PCT
from research.intraday_chart_population import QUOTE_OFFSETS
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_pattern_calibration import session_close, session_open


Policy = Literal["fixed", "dynamic"]


@dataclass(frozen=True)
class AccountDay:
    session: str
    common_rows: int
    available_forecasts: int
    eligible_entries: int
    support: Literal["no_common_rows", "no_forecast", "supported"]
    modeled_entries: int
    # Absent when forecast support or the continuous valuation path is absent.
    closing_nav: Fraction | None
    # Known even after a valuation gap if every execution was known.
    closing_cash: Fraction | None


@dataclass
class _Ledger:
    bought_units: int = 0
    sold_units: int = 0
    entry_basis: Fraction = Fraction()
    paid_cost: Fraction = Fraction()
    sold_basis: Fraction = Fraction()
    allocated_cost: Fraction = Fraction()
    sale_proceeds: Fraction = Fraction()

    def verify(self, book: cash.CashBook) -> None:
        # This reconstructs conservation from checked movements, independently
        # of the cash kernel's internal ledger. Closed-lot history is not kept.
        _require(
            book.cash == cash.INITIAL_CAPITAL - self.entry_basis - self.paid_cost + self.sale_proceeds
            and self.bought_units == self.sold_units + sum(lot.quantity for lot in book.lots)
            and self.entry_basis == self.sold_basis + sum(lot.remaining_entry_basis for lot in book.lots)
            and self.paid_cost == self.allocated_cost + sum(lot.remaining_entry_cost for lot in book.lots),
            "ef_account_conservation_mismatch",
        )


@dataclass(frozen=True)
class AccountResult:
    policy: Policy
    cost_bps: int
    days: tuple[AccountDay, ...]
    entry_counts: tuple[tuple[str, int], ...]
    holding_counts: tuple[tuple[str, int], ...]
    sale_counts: tuple[tuple[str, int], ...]
    last_known_cash: Fraction
    last_known_lots: tuple[cash.Lot, ...]
    inventory_uncertain: bool
    unknown_at: datetime | None
    unknown_reasons: tuple[str, ...]
    modeled_bought_units: int
    modeled_sold_units: int
    modeled_entry_basis: Fraction
    modeled_paid_cost: Fraction
    modeled_sold_basis: Fraction
    modeled_allocated_cost: Fraction
    modeled_sale_proceeds: Fraction
    continuous_return: Fraction | None
    continuous_maximum_drawdown: Fraction | None


@dataclass
class _Holding:
    offset: int
    history: tuple[HoldingDecision, ...] = ()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal(reason)


def _check_movement(
    before: cash.CashBook, decision: cash.Decision, *,
    quantity: int, delta: Fraction = Fraction(), cost: Fraction = Fraction(),
    lots: tuple[cash.Lot, ...] | None = None, uncertain: bool = False,
) -> None:
    after = decision.book
    _require(
        decision.quantity == quantity and decision.cash_delta == delta and decision.assumed_cost == cost
        and decision.filled == (quantity > 0) and after.cash == before.cash + delta
        and after.lots == (before.lots if lots is None else lots)
        and after.inventory_uncertain == (before.inventory_uncertain or uncertain)
        and after.indeterminate == (before.indeterminate or uncertain)
        and after.next_lot_id == before.next_lot_id + (cost > 0),
        "ef_account_movement_mismatch",
    )


class _Account:
    def __init__(self, policy: Policy, cost_bps: int):
        self.policy = policy
        self.cost_bps = cost_bps
        self.book = cash.new_book(f"EF-{policy}-{cost_bps}")
        self.holdings: dict[str, _Holding] = {}
        self.ledger = _Ledger()
        self.anchors = {}
        self.peak = cash.INITIAL_CAPITAL
        self.drawdown = Fraction()
        self.nav: Fraction | None = self.book.cash
        self.gates: tuple[str, ...] = ()
        self.unknown_at: datetime | None = None
        self.days: list[AccountDay] = []
        self.entries: Counter[str] = Counter()
        self.updates: Counter[str] = Counter()
        self.sales: Counter[str] = Counter()

    def observe(self, at: datetime, marks: dict[str, Fraction | None]) -> None:
        if self.book.inventory_uncertain:
            self.unknown_at = self.unknown_at or at
            self.nav, self.gates = None, ("account_indeterminate",)
            return
        before = self.book
        valuation = cash.mark(before, marks)
        missing = any(marks.get(lot.symbol) is None for lot in before.lots)
        expected_nav = None if missing else before.cash + sum(lot.quantity * marks[lot.symbol] for lot in before.lots)
        expected_full = None if before.indeterminate else expected_nav
        _require(valuation.observed_nav == expected_nav and valuation.full_nav == expected_full
                 and valuation.book.indeterminate == (before.indeterminate or missing)
                 and not valuation.book.inventory_uncertain
                 and (valuation.book.cash, valuation.book.lots) == (before.cash, before.lots),
                 "ef_account_valuation_mismatch")
        self.book, self.nav = valuation.book, valuation.full_nav
        if self.nav is None:
            self.unknown_at = self.unknown_at or at
            self.gates = ("account_indeterminate",)
            return
        names = ("daily_anchor", "weekly_anchor", "monthly_anchor")
        changes, returns, self.gates, peak, maxdd, dd = _risk_observation(
            self.nav, at, "continuous", *(self.anchors.get(name) for name in names),
            self.peak, self.drawdown,
        )
        # Anchor and risk arithmetic is reconciled against independently
        # reconstructed NAV. The risk limits stay in the shared policy.
        expected_returns = []
        for name, period in zip(names, _periods(at), strict=True):
            previous = self.anchors.get(name)
            anchor = changes.get(name, previous)
            if previous is None or previous.period != period:
                _require(anchor is not None and anchor.period == period and anchor.nav == self.nav,
                         "ef_account_risk_anchor_mismatch")
            else:
                _require(name not in changes, "ef_account_risk_anchor_reset")
            expected_returns.append((name.removesuffix("_anchor"), self.nav / anchor.nav - 1))
        _require(returns == tuple(expected_returns) and peak == max(self.peak, self.nav)
                 and dd == 1 - self.nav / peak and maxdd == max(self.drawdown, dd),
                 "ef_account_risk_observation_mismatch")
        self.anchors.update(changes)
        self.peak, self.drawdown = peak, maxdd

    def planned_sales(
        self, opening: datetime, offset: int, forecast: Callable[[str], float | None],
    ) -> tuple[tuple[str, str], ...]:
        if self.book.inventory_uncertain:
            return ()
        planned = []
        for symbol, held in sorted(self.holdings.items()):
            age = offset - held.offset
            if age == FIXED_EXIT_MINUTES:
                planned.append((symbol, "fixed_cap"))
            elif self.policy == "dynamic" and age in UPDATE_MINUTES:
                entry_at = opening + (held.offset + 3) * MINUTE
                at = opening + (offset + 3) * MINUTE
                decision = holding_decision(entry_at, held.history, at - MINUTE, forecast(symbol))
                self.updates[decision.reason] += 1
                held.history += (decision,)
                if decision.action != "hold":
                    _require(decision.execute_at == at, "ef_account_sale_clock_mismatch")
                    planned.append((symbol, decision.reason))
        return tuple(planned)

    def sell(self, symbol: str, reason: str, quote: Callable[[str], Fraction | None]) -> None:
        before = self.book
        if before.inventory_uncertain:
            self.sales["account_indeterminate"] += 1
            return
        lot = next(item for item in before.lots if item.symbol == symbol)
        half = reason == "first_nonpositive"
        quantity = lot.quantity // 2 if half else lot.quantity
        if not quantity:
            self.sales["reduction_below_one_unit"] += 1
            return
        price = quote(symbol)
        decision = (cash.reduce_half(before, symbol, exit_price=price) if half
                    else cash.exit(before, symbol, quantity=quantity, exit_price=price))
        if price is None:
            _check_movement(before, decision, quantity=0, uncertain=True)
            _require(decision.reason == "exit_price_unknown" and not decision.book.realized_exits,
                     "ef_account_unknown_sale_mismatch")
            self.sales[decision.reason] += 1
            self.book = decision.book
            return
        basis = lot.entry_price * quantity
        allocated = lot.original_entry_cost * quantity / lot.original_quantity
        proceeds = price * quantity
        if half:
            remaining = replace(lot, quantity=lot.quantity - quantity, half_reduced=True)
            lots = tuple(remaining if item == lot else item for item in before.lots)
        else:
            lots = tuple(item for item in before.lots if item != lot)
            del self.holdings[symbol]
        _check_movement(before, decision, quantity=quantity, delta=proceeds, lots=lots)
        _require(decision.book.realized_exits == (
            cash.RealizedExit(lot.lot_id, symbol, quantity, basis, allocated, proceeds),
        ), "ef_account_realized_sale_mismatch")
        self.book = replace(decision.book, realized_exits=())
        self.ledger.sold_units += quantity
        self.ledger.sold_basis += basis
        self.ledger.allocated_cost += allocated
        self.ledger.sale_proceeds += proceeds
        self.sales[reason] += 1
        self.ledger.verify(self.book)

    def planned_buys(self, symbols: Iterable[str]) -> tuple[tuple[str, Fraction, Fraction], ...]:
        plans = []
        available = self.book.cash
        rate = Fraction(self.cost_bps, 10_000)
        for symbol in sorted(symbols):
            if self.gates:
                self.entries["blocked:" + ",".join(self.gates)] += 1
            elif symbol in self.holdings:
                self.entries["duplicate_position"] += 1
            else:
                _require(self.nav is not None and not self.book.indeterminate, "ef_account_entry_with_unknown_risk")
                reserved = min(available, self.nav * cash.ORDER_FRACTION * (1 + rate))
                if not reserved:
                    self.entries["insufficient_cash_for_one_share"] += 1
                    continue
                plans.append((symbol, self.nav, available))
                available -= reserved
        return tuple(plans)

    def buy(self, at: datetime, offset: int, plan: tuple[str, Fraction, Fraction],
            quote: Callable[[str], Fraction | None]) -> None:
        if self.book.indeterminate:
            self.entries["blocked:account_indeterminate"] += 1
            return
        symbol, nav, available = plan
        before = self.book
        price = quote(symbol)
        decision = cash.enter(before, symbol, entry_price=price, observed_prices={}, cost_bps=self.cost_bps,
                              decision_funds=(nav, available))
        self.entries[decision.reason] += 1
        if price is None:
            _check_movement(before, decision, quantity=0, uncertain=True)
            _require(decision.reason == "entry_price_unknown" and not decision.book.realized_exits,
                     "ef_account_unknown_purchase_mismatch")
        else:
            rate = Fraction(self.cost_bps, 10_000)
            cash_limit = available / (1 + rate)
            budget = min(cash_limit, nav * cash.ORDER_FRACTION)
            quantity = budget // price
            basis = quantity * price
            cost = basis * rate
            lots = (before.lots + (cash.Lot(before.next_lot_id, symbol, quantity, quantity, price, cost),)
                    if quantity else before.lots)
            _check_movement(before, decision, quantity=quantity, delta=-basis - cost, cost=cost, lots=lots)
            reason = "entered" if quantity else ("insufficient_cash_for_one_share" if cash_limit < price
                                                 else "one_share_exceeds_nav_limit")
            _require(decision.reason == reason and decision.notional_budget == budget
                     and not decision.book.realized_exits,
                     "ef_account_entry_budget_mismatch")
            if quantity:
                self.ledger.bought_units += quantity
                self.ledger.entry_basis += basis
                self.ledger.paid_cost += cost
                self.holdings[symbol] = _Holding(offset)
        self.book = decision.book
        self.ledger.verify(self.book)
        if self.book.inventory_uncertain:
            self.unknown_at = self.unknown_at or at

    def result(self) -> AccountResult:
        complete = self.nav is not None and all(day.support == "supported" for day in self.days)
        ledger = self.ledger
        return AccountResult(
            self.policy, self.cost_bps, tuple(self.days), tuple(sorted(self.entries.items())),
            tuple(sorted(self.updates.items())), tuple(sorted(self.sales.items())), self.book.cash,
            self.book.lots, self.book.inventory_uncertain, self.unknown_at, self.book.indeterminate_reasons,
            ledger.bought_units, ledger.sold_units, ledger.entry_basis, ledger.paid_cost,
            ledger.sold_basis, ledger.allocated_cost, ledger.sale_proceeds,
            self.nav / cash.INITIAL_CAPITAL - 1 if complete else None,
            self.drawdown if complete else None,
        )


def replay_accounts(
    sessions: Iterable[str],
    common_entries: Iterable[tuple[Key, float | None]],
    *,
    quote_at: Callable[[Key], cash.Exact | None],
    gross15_at: Callable[[Key], float | None],
    include_dynamic: bool = True,
) -> tuple[AccountResult, ...]:
    """Replay one model/scenario with continuous capital across all supplied sessions.

    Pass the original evaluation session calendar and every common-support
    entry coordinate, including unavailable gross30 forecasts. The caller
    owns calendar/support authenticity and must supply forecasts from the
    same chronological fits throughout each entry and holding path. No
    supervised outcomes or completion filters are accepted.

    Results are ordered by cost (33, 43) then fixed/dynamic. Fixed-only arms
    never request holding forecasts. An unsupported session has no strategy
    assessment even when cash is known. A path gap permanently removes
    continuous NAV; known inventory still permits exits. Only day summaries,
    current inventory and movement totals are retained.
    """
    _require(type(include_dynamic) is bool, "ef_account_dynamic_flag_required")
    days = tuple(sessions)
    _require(bool(days) and all(type(day) is str for day in days)
             and all(a < b for a, b in zip(days, days[1:])), "ef_account_calendar_required")
    for day in days:
        try:
            canonical = date.fromisoformat(day).isoformat()
        except ValueError as exc:
            raise InputRefusal("ef_account_calendar_required") from exc
        _require(day == canonical, "ef_account_calendar_required")
    clocks = {day: (session_open(day), session_close(day)) for day in days}
    by_day: dict[str, dict[int, list[str]]] = {day: defaultdict(list) for day in days}
    common, forecasts = Counter(), Counter()
    seen = set()
    for row in common_entries:
        _require(type(row) is tuple and len(row) == 2, "ef_account_entry_forecast_required")
        key, score = row
        _require(type(key) is tuple and len(key) == 3, "ef_account_entry_coordinate_required")
        day, symbol, offset = key
        _require(type(day) is str and day in clocks and type(symbol) is str
                 and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None
                 and type(offset) is int and offset in ENTRY_OFFSETS,
                 "ef_account_entry_coordinate_required")
        opening, closing = clocks[day]
        _require(opening + (offset + 3 + FIXED_EXIT_MINUTES) * MINUTE < closing - 15 * MINUTE
                 and key not in seen, "ef_account_entry_clock_or_duplicate")
        seen.add(key)
        common[day] += 1
        if score is not None:
            _require(type(score) is float and isfinite(score), "ef_account_finite_gross30_required")
            forecasts[day] += 1
            if score > ADMISSION_PCT:
                by_day[day][offset].append(symbol)

    policies: tuple[Policy, ...] = ("fixed", "dynamic") if include_dynamic else ("fixed",)
    accounts = tuple(_Account(policy, cost) for cost in (33, 43) for policy in policies)
    for day, (opening, closing) in clocks.items():
        entries = by_day[day]
        quotes = {}
        before = [book.entries["entered"] for book in accounts]
        for book in accounts:
            book.observe(opening, {})
        for offset in QUOTE_OFFSETS:
            at = opening + (offset + 3) * MINUTE
            if at >= closing - 15 * MINUTE:
                break
            if offset not in entries and not any(book.holdings and not book.book.inventory_uncertain
                                                 for book in accounts):
                continue
            predictions = {}

            def quote(symbol: str, quote_offset: int = offset) -> Fraction | None:
                key = symbol, quote_offset
                if key not in quotes:
                    value = quote_at((day, symbol, quote_offset))
                    quotes[key] = None if value is None else cash._amount(value, "ef_account_open")
                return quotes[key]

            def forecast(symbol: str) -> float | None:
                if symbol not in predictions:
                    predictions[symbol] = gross15_at((day, symbol, offset))
                return predictions[symbol]

            # The preceding grid OPEN matures after three minutes. It is
            # available at this decision one minute before the current OPEN.
            plans = []
            for book in accounts:
                marks = ({} if book.book.inventory_uncertain
                         else {symbol: quote(symbol, offset - 5) for symbol in sorted(book.holdings)})
                book.observe(at - MINUTE, marks)
                plans.append((book.planned_sales(opening, offset, forecast), book.planned_buys(entries.get(offset, ()))))
            for book, (sales, buys) in zip(accounts, plans, strict=True):
                for symbol, reason in sales:
                    book.sell(symbol, reason, quote)
                if book.book.inventory_uncertain:
                    book.unknown_at = book.unknown_at or at
                for buy in buys:
                    book.buy(at, offset, buy, quote)
        support = "supported" if forecasts[day] else ("no_forecast" if common[day] else "no_common_rows")
        for book, initial_entries in zip(accounts, before, strict=True):
            _require(book.book.inventory_uncertain or not book.holdings, "ef_account_lot_outside_clock")
            book.observe(closing, {})
            book.days.append(AccountDay(
                day, common[day], forecasts[day], sum(map(len, entries.values())), support,
                book.entries["entered"] - initial_entries, book.nav if support == "supported" else None,
                None if book.book.inventory_uncertain else book.book.cash,
            ))
    return tuple(book.result() for book in accounts)
