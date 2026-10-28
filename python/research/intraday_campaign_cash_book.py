"""Pure EC integer-share cash arithmetic; no signals, clocks, IO or orders.

The caller must supply contemporaneous observed prices and enforce the registered
event order, session boundaries and loss limits. The conservative research order
ceiling here does not change Java risk limits or apply a live-canary policy.
Round-trip cost is an explicit assumption charged on entry notional once; exits
credit gross proceeds. Neither observed prices nor these fills certify execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction
from typing import Literal


Exact = int | Decimal | Fraction
Mode = Literal["A", "B", "A+B"]
INITIAL_CAPITAL = Fraction(100_000_000)
ORDER_FRACTION = Fraction(1, 50)


def _amount(value: Exact, field: str, *, zero: bool = False) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, Fraction)):
        raise ValueError(f"{field}: exact int, Decimal or Fraction required")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError(f"{field}: finite value required")
    result = Fraction(value)
    if result < 0 or (not zero and result == 0):
        raise ValueError(f"{field}: {'nonnegative' if zero else 'positive'} value required")
    return result


def _name(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field}: nonempty unpadded string required")


@dataclass(frozen=True)
class Sleeve:
    strategy: str
    initial_allocation: Fraction
    cash: Fraction

    def __post_init__(self) -> None:
        if self.strategy not in ("A", "B"):
            raise ValueError("unknown_strategy")
        object.__setattr__(self, "initial_allocation", _amount(self.initial_allocation, "allocation"))
        object.__setattr__(self, "cash", _amount(self.cash, "cash", zero=True))


@dataclass(frozen=True)
class Lot:
    strategy: str
    symbol: str
    quantity: int
    entry_price: Fraction
    entry_cost: Fraction

    def __post_init__(self) -> None:
        _name(self.symbol, "symbol")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("positive_integer_quantity_required")
        object.__setattr__(self, "entry_price", _amount(self.entry_price, "entry_price"))
        object.__setattr__(self, "entry_cost", _amount(self.entry_cost, "entry_cost", zero=True))


@dataclass(frozen=True)
class CashBook:
    initial_capital: Fraction
    sleeves: tuple[Sleeve, ...]
    lots: tuple[Lot, ...] = ()
    indeterminate_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_capital", _amount(self.initial_capital, "initial_capital"))
        object.__setattr__(self, "sleeves", tuple(self.sleeves))
        object.__setattr__(self, "lots", tuple(self.lots))
        object.__setattr__(self, "indeterminate_reasons", tuple(self.indeterminate_reasons))
        if not self.sleeves or any(not isinstance(s, Sleeve) for s in self.sleeves):
            raise ValueError("sleeves_required")
        strategies = tuple(s.strategy for s in self.sleeves)
        if len(set(strategies)) != len(strategies):
            raise ValueError("duplicate_strategy")
        if sum(s.initial_allocation for s in self.sleeves) != self.initial_capital:
            raise ValueError("allocation_total_mismatch")
        if any(not isinstance(lot, Lot) or lot.strategy not in strategies for lot in self.lots):
            raise ValueError("invalid_lot_strategy")
        if len({lot.symbol for lot in self.lots}) != len(self.lots):
            raise ValueError("duplicate_position")
        for reason in self.indeterminate_reasons:
            _name(reason, "reason")

    @property
    def indeterminate(self) -> bool:
        return bool(self.indeterminate_reasons)

    @property
    def cash(self) -> Fraction:
        return sum((s.cash for s in self.sleeves), Fraction())

    def sleeve(self, strategy: str) -> Sleeve:
        for sleeve in self.sleeves:
            if sleeve.strategy == strategy:
                return sleeve
        raise ValueError("strategy_not_allocated")


@dataclass(frozen=True)
class Valuation:
    book: CashBook
    # Current supplied observations only. A known sleeve can coexist with an
    # unknown other sleeve; these snapshots never clear the book's sticky status.
    sleeve_navs: tuple[tuple[str, Fraction | None], ...]
    full_nav: Fraction | None


@dataclass(frozen=True)
class Decision:
    book: CashBook
    reason: str
    quantity: int = 0
    cash_delta: Fraction = Fraction()
    assumed_cost: Fraction = Fraction()
    notional_budget: Fraction | None = None

    @property
    def filled(self) -> bool:
        return self.quantity > 0


def new_book(mode: Mode) -> CashBook:
    """Create the approved fictional account with fixed initial sleeve budgets."""
    if mode not in ("A", "B", "A+B"):
        raise ValueError("unknown_mode")
    strategies = ("A", "B") if mode == "A+B" else (mode,)
    allocation = INITIAL_CAPITAL / len(strategies)
    return CashBook(INITIAL_CAPITAL, tuple(Sleeve(s, allocation, allocation) for s in strategies))


def freeze(book: CashBook, reason: str) -> CashBook:
    """Record an indeterminate path permanently; there is no implicit reset."""
    _name(reason, "reason")
    if reason in book.indeterminate_reasons:
        return book
    return replace(book, indeterminate_reasons=book.indeterminate_reasons + (reason,))


def mark(book: CashBook, observed_prices: Mapping[str, Exact | None]) -> Valuation:
    """Value every held lot from this call only; missing prices freeze new entry.

    No previous mark or entry price is carried forward. Once indeterminate,
    full_nav remains unknown even if later snapshots price all remaining lots.
    Per-sleeve snapshots still disclose what is currently observable.
    """
    navs: dict[str, Fraction | None] = {s.strategy: s.cash for s in book.sleeves}
    for lot in book.lots:
        supplied = observed_prices.get(lot.symbol)
        if supplied is None:
            book = freeze(book, f"missing_held_price:{lot.strategy}:{lot.symbol}")
            navs[lot.strategy] = None
            continue
        price = _amount(supplied, "held_price")
        current = navs[lot.strategy]
        if current is not None:
            navs[lot.strategy] = current + lot.quantity * price
    full_nav = None if book.indeterminate else sum(
        (nav for nav in navs.values() if nav is not None), Fraction()
    )
    return Valuation(book, tuple(navs.items()), full_nav)


def _cash_change(book: CashBook, strategy: str, delta: Fraction, lots: tuple[Lot, ...]) -> CashBook:
    return replace(book, sleeves=tuple(
        replace(s, cash=s.cash + delta) if s.strategy == strategy else s for s in book.sleeves
    ), lots=lots)


def enter(
    book: CashBook,
    strategy: str,
    symbol: str,
    *,
    entry_price: Exact | None,
    observed_prices: Mapping[str, Exact | None],
    cost_bps: Exact,
) -> Decision:
    """Buy floor(min(full NAV/50, sleeve NAV/50, cash/(1+c))/price).

    Cost c is the caller's assumed round-trip bps, reserved and paid once now.
    An unavailable entry or held quote makes the account path indeterminate:
    absent historical price evidence does not establish that an order failed.
    No quantity or cash movement is fabricated for that unknown fill.
    """
    sleeve = book.sleeve(strategy)
    _name(symbol, "symbol")
    cost = _amount(cost_bps, "cost_bps", zero=True) / 10_000
    if book.indeterminate:
        return Decision(book, "account_indeterminate")
    if any(lot.symbol == symbol for lot in book.lots):
        return Decision(book, "duplicate_position")
    valuation = mark(book, observed_prices)
    if valuation.full_nav is None:
        return Decision(valuation.book, "equity_unknown")
    if entry_price is None:
        return Decision(freeze(book, f"missing_entry_price:{strategy}:{symbol}"), "entry_price_unknown")
    price = _amount(entry_price, "entry_price")
    sleeve_nav = dict(valuation.sleeve_navs)[strategy]
    assert sleeve_nav is not None
    cash_budget = sleeve.cash / (1 + cost)
    budget = min(valuation.full_nav * ORDER_FRACTION, sleeve_nav * ORDER_FRACTION, cash_budget)
    quantity = budget // price
    if quantity == 0:
        reason = "insufficient_cash_for_one_share" if cash_budget < price else "one_share_exceeds_nav_limit"
        return Decision(book, reason, notional_budget=budget)
    notional = quantity * price
    entry_cost = notional * cost
    cash_delta = -(notional + entry_cost)
    lot = Lot(strategy, symbol, quantity, price, entry_cost)
    changed = _cash_change(book, strategy, cash_delta, book.lots + (lot,))
    return Decision(changed, "entered", quantity, cash_delta, entry_cost, budget)


def exit(book: CashBook, strategy: str, symbol: str, *, exit_price: Exact | None) -> Decision:
    """Sell an entire known lot at the supplied price; never charge cost twice.

    Known exits can reduce holdings after a freeze, but cannot repair its history
    or enable another entry. A missing exit retains both cash and the entire lot.
    """
    book.sleeve(strategy)
    _name(symbol, "symbol")
    lot = next((lot for lot in book.lots if lot.strategy == strategy and lot.symbol == symbol), None)
    if lot is None:
        return Decision(book, "position_not_held")
    if exit_price is None:
        return Decision(freeze(book, f"missing_exit_price:{strategy}:{symbol}"), "exit_price_unknown")
    proceeds = lot.quantity * _amount(exit_price, "exit_price")
    changed = _cash_change(book, strategy, proceeds, tuple(held for held in book.lots if held != lot))
    return Decision(changed, "exited", lot.quantity, proceeds)
