"""Pure EF long-only cash arithmetic in nominal archived price units.

Each book belongs to one strategy arm and can hold multiple symbols. Quantities
are integer *nominal archived units*, not certified physical shares or KRW.
The caller owns chronology, admission, period loss limits and source evidence.
This module has no clocks, IO, orders or learned decisions.

The existing round-trip cost assumption is paid once on original entry. Partial
sales credit known gross proceeds only and allocate the original basis and paid
cost proportionally, without a second charge or refund. Unknown price evidence
does not establish a no-fill: preserve inventory/cash and stop subsequent cash
mutations permanently. No later observation repairs that missing history.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal

from research.intraday_campaign_cash_book import (
    INITIAL_CAPITAL,
    ORDER_FRACTION,
    Exact,
    _amount,
    _cash_entry_amounts,
    _freeze_reasons,
    _marked_nav,
    _name,
)


def _positive_integer(value: int, field: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field}: positive_integer_required")


@dataclass(frozen=True)
class Lot:
    lot_id: int
    symbol: str
    original_quantity: int
    quantity: int
    entry_price: Fraction
    original_entry_cost: Fraction
    half_reduced: bool = False

    def __post_init__(self) -> None:
        _positive_integer(self.lot_id, "lot_id")
        _name(self.symbol, "symbol")
        _positive_integer(self.original_quantity, "original_quantity")
        _positive_integer(self.quantity, "quantity")
        if self.quantity > self.original_quantity:
            raise ValueError("remaining_quantity_exceeds_original")
        if type(self.half_reduced) is not bool:
            raise ValueError("half_reduced: bool_required")
        object.__setattr__(self, "entry_price", _amount(self.entry_price, "entry_price"))
        object.__setattr__(self, "original_entry_cost", _amount(
            self.original_entry_cost, "original_entry_cost", zero=True))

    @property
    def remaining_entry_basis(self) -> Fraction:
        return self.quantity * self.entry_price

    @property
    def remaining_entry_cost(self) -> Fraction:
        return self.original_entry_cost * Fraction(self.quantity, self.original_quantity)


@dataclass(frozen=True)
class RealizedExit:
    lot_id: int
    symbol: str
    quantity: int
    entry_basis: Fraction
    allocated_entry_cost: Fraction
    gross_proceeds: Fraction

    def __post_init__(self) -> None:
        _positive_integer(self.lot_id, "lot_id")
        _name(self.symbol, "symbol")
        _positive_integer(self.quantity, "quantity")
        object.__setattr__(self, "entry_basis", _amount(self.entry_basis, "entry_basis"))
        object.__setattr__(self, "allocated_entry_cost", _amount(
            self.allocated_entry_cost, "allocated_entry_cost", zero=True))
        object.__setattr__(self, "gross_proceeds", _amount(self.gross_proceeds, "gross_proceeds"))

    @property
    def gross_pnl(self) -> Fraction:
        return self.gross_proceeds - self.entry_basis

    @property
    def net_pnl(self) -> Fraction:
        return self.gross_pnl - self.allocated_entry_cost


@dataclass(frozen=True)
class CashBook:
    strategy: str
    initial_capital: Fraction
    cash: Fraction
    lots: tuple[Lot, ...] = ()
    realized_exits: tuple[RealizedExit, ...] = ()
    indeterminate_reasons: tuple[str, ...] = ()
    next_lot_id: int = 1

    def __post_init__(self) -> None:
        _name(self.strategy, "strategy")
        object.__setattr__(self, "initial_capital", _amount(self.initial_capital, "initial_capital"))
        object.__setattr__(self, "cash", _amount(self.cash, "cash", zero=True))
        object.__setattr__(self, "lots", tuple(self.lots))
        object.__setattr__(self, "realized_exits", tuple(self.realized_exits))
        object.__setattr__(self, "indeterminate_reasons", tuple(self.indeterminate_reasons))
        _positive_integer(self.next_lot_id, "next_lot_id")
        if any(not isinstance(lot, Lot) for lot in self.lots):
            raise ValueError("invalid_lot")
        if any(not isinstance(item, RealizedExit) for item in self.realized_exits):
            raise ValueError("invalid_realized_exit")
        if len({lot.symbol for lot in self.lots}) != len(self.lots):
            raise ValueError("duplicate_position")
        if len({lot.lot_id for lot in self.lots}) != len(self.lots):
            raise ValueError("duplicate_lot_id")
        if any(item.lot_id >= self.next_lot_id for item in (*self.lots, *self.realized_exits)):
            raise ValueError("next_lot_id_not_new")
        for reason in self.indeterminate_reasons:
            _name(reason, "reason")

    @property
    def indeterminate(self) -> bool:
        return bool(self.indeterminate_reasons)

    @property
    def realized_gross(self) -> Fraction:
        return sum((item.gross_pnl for item in self.realized_exits), Fraction())

    @property
    def realized_net(self) -> Fraction:
        return sum((item.net_pnl for item in self.realized_exits), Fraction())


@dataclass(frozen=True)
class Valuation:
    book: CashBook
    observed_nav: Fraction | None
    # A current observed snapshot cannot repair a previously unknown path.
    full_nav: Fraction | None


@dataclass(frozen=True)
class Decision:
    book: CashBook
    reason: str
    outcome: Literal["modeled", "declined", "indeterminate"] = "declined"
    quantity: int = 0
    cash_delta: Fraction = Fraction()
    assumed_cost: Fraction = Fraction()
    notional_budget: Fraction | None = None

    @property
    def filled(self) -> bool:
        """Known modeled movement only; false is not proof of an actual no-fill."""
        return self.outcome == "modeled"


def new_book(strategy: str) -> CashBook:
    """Give one schema-neutral arm the unchanged fictional initial capital."""
    return CashBook(strategy, INITIAL_CAPITAL, INITIAL_CAPITAL)


def freeze(book: CashBook, reason: str) -> CashBook:
    reasons = _freeze_reasons(book.indeterminate_reasons, reason)
    return book if reasons == book.indeterminate_reasons else replace(book, indeterminate_reasons=reasons)


def mark(book: CashBook, observed_prices: Mapping[str, Exact | None]) -> Valuation:
    """Use only supplied current observations; missing holdings stop new entry."""
    nav, missing = _marked_nav(book.cash, tuple((lot.symbol, lot.quantity) for lot in book.lots), observed_prices)
    for symbol in missing:
        book = freeze(book, f"missing_held_price:{book.strategy}:{symbol}")
    return Valuation(book, nav, None if book.indeterminate else nav)


def enter(
    book: CashBook,
    symbol: str,
    *,
    entry_price: Exact | None,
    observed_prices: Mapping[str, Exact | None],
    cost_bps: Exact,
) -> Decision:
    """Floor the existing NAV/cash budget into positive nominal integer units.

    The single arm owns the whole NAV; there is no add-on to a held symbol.
    ``ORDER_FRACTION`` and the shared helper retain the existing order ceiling.
    The caller supplies the registered cost assumption, including 33/43 continuity.
    """
    _name(symbol, "symbol")
    cost = _amount(cost_bps, "cost_bps", zero=True) / 10_000
    price = None if entry_price is None else _amount(entry_price, "entry_price")
    if book.indeterminate:
        return Decision(book, "account_indeterminate", "indeterminate")
    if any(lot.symbol == symbol for lot in book.lots):
        return Decision(book, "duplicate_position")
    valuation = mark(book, observed_prices)
    if valuation.full_nav is None:
        return Decision(valuation.book, "equity_unknown", "indeterminate")
    if price is None:
        changed = freeze(book, f"missing_entry_price:{book.strategy}:{symbol}")
        return Decision(changed, "entry_price_unknown", "indeterminate")
    quantity, budget, entry_cost, delta, reason = _cash_entry_amounts(
        valuation.full_nav, valuation.full_nav, book.cash, price, cost)
    if quantity == 0:
        return Decision(book, reason, notional_budget=budget)
    lot = Lot(book.next_lot_id, symbol, quantity, quantity, price, entry_cost)
    changed = replace(book, cash=book.cash + delta, lots=book.lots + (lot,), next_lot_id=book.next_lot_id + 1)
    return Decision(changed, "entered", "modeled", quantity, delta, entry_cost, budget)


def _held(book: CashBook, symbol: str) -> Lot | None:
    return next((lot for lot in book.lots if lot.symbol == symbol), None)


def _sell(book: CashBook, lot: Lot, quantity: int, price: Fraction | None, *, half: bool) -> Decision:
    if price is None:
        changed = freeze(book, f"missing_exit_price:{book.strategy}:{lot.symbol}")
        return Decision(changed, "exit_price_unknown", "indeterminate")
    record = RealizedExit(
        lot.lot_id, lot.symbol, quantity, quantity * lot.entry_price,
        lot.original_entry_cost * Fraction(quantity, lot.original_quantity), quantity * price)
    remaining = lot.quantity - quantity
    lots = tuple(
        replace(held, quantity=remaining, half_reduced=held.half_reduced or half)
        if held.lot_id == lot.lot_id else held
        for held in book.lots if held.lot_id != lot.lot_id or remaining
    )
    changed = replace(book, cash=book.cash + record.gross_proceeds, lots=lots,
                      realized_exits=book.realized_exits + (record,))
    reason = "reduced_half" if half else ("exited" if remaining == 0 else "reduced")
    return Decision(changed, reason, "modeled", quantity, record.gross_proceeds)


def exit(book: CashBook, symbol: str, *, quantity: int, exit_price: Exact | None) -> Decision:
    """Sell 1..remaining nominal units, allocating paid cost without charging it.

    An indeterminate book refuses every sale: an earlier unknown sale might have
    already consumed those units. The original lot and cash are retained as the last
    known state, not a claim that the earlier unknown attempt failed to fill.
    """
    _name(symbol, "symbol")
    _positive_integer(quantity, "quantity")
    price = None if exit_price is None else _amount(exit_price, "exit_price")
    if book.indeterminate:
        return Decision(book, "account_indeterminate", "indeterminate")
    lot = _held(book, symbol)
    if lot is None:
        return Decision(book, "position_not_held")
    if quantity > lot.quantity:
        return Decision(book, "quantity_exceeds_remaining")
    return _sell(book, lot, quantity, price, half=False)


def reduce_half(
    book: CashBook, symbol: str, *, exit_price: Exact | None, requested_quantity: int | None = None,
) -> Decision:
    """Apply floor(remaining/2) once per lot; one unit needs a separate full exit.

    An optional caller quantity must exactly equal this deterministic action. An
    unknown sale preserves the flag too and freezes all subsequent cash mutations.
    """
    _name(symbol, "symbol")
    if requested_quantity is not None:
        _positive_integer(requested_quantity, "requested_quantity")
    price = None if exit_price is None else _amount(exit_price, "exit_price")
    if book.indeterminate:
        return Decision(book, "account_indeterminate", "indeterminate")
    lot = _held(book, symbol)
    if lot is None:
        return Decision(book, "position_not_held")
    if lot.half_reduced:
        return Decision(book, "half_reduction_already_used")
    quantity = lot.quantity // 2
    if quantity == 0:
        return Decision(book, "reduction_below_one_unit")
    if requested_quantity is not None and requested_quantity != quantity:
        return Decision(book, "requested_quantity_conflict")
    return _sell(book, lot, quantity, price, half=True)
