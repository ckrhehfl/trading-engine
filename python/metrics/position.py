"""Reconstructs a single-symbol net position from `(OrderIntent, Fill)`
pairs, aggregating each 0 -> nonzero -> 0 position lifecycle into one
`ClosedTrade` record — not one record per fill.

This is a genuinely separate concern from `backtest/`, which stays scoped
to fill simulation only (see `backtest/engine.py`'s `run_backtest`
docstring). This module is the first consumer of a `BacktestResult`'s
`fills`/`filled_intents`, turning a sequence of individual fills into
position-level economics (realized P&L, trade boundaries) that
`backtest/` deliberately does not compute.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from backtest.fill import Fill
from schemas.order_intent import OrderIntent, Side


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


@dataclass(frozen=True)
class ClosedTrade:
    """One full position lifecycle: flat -> nonzero -> flat again.

    P&L across every intermediate partial-reduction fill within the
    lifecycle is aggregated into `realized_pnl` — a strategy that scales
    out of a position in three pieces is one trade here, not three, for
    win-rate/profit-factor purposes (see `metrics.py`).
    """

    side: Side
    entry_time: datetime
    exit_time: datetime
    quantity: Decimal  # total quantity opened over the lifecycle
    entry_price: Decimal  # size-weighted average entry price
    exit_price: Decimal  # size-weighted average exit price
    realized_pnl: Decimal


class PositionTracker:
    """Walks `(OrderIntent, Fill)` pairs one at a time via `apply()`,
    maintaining a running signed position (`position_qty`: positive long,
    negative short, zero flat) and returning a `ClosedTrade` whenever a
    lifecycle fully closes — including the close half of a same-fill flip,
    per the reconstruction rules below.

    Reconstruction rules (from CLAUDE.md's "Strategy Research Operational
    Design"):

    - `signed_qty = fill.quantity if intent.side == Side.LONG else
      -fill.quantity`.
    - Same sign as the current position (or currently flat): update the
      running size-weighted `avg_entry_price`, `position_qty +=
      signed_qty`. No trade closes.
    - Opposite sign (reducing/closing/flipping):
      `closing_qty = min(abs(signed_qty), abs(position_qty))`; realize
      `closing_qty * (fill.fill_price - avg_entry_price) *
      sign(position_qty)` into `realized_pnl`. If `abs(signed_qty) >
      abs(position_qty)` (a flip), this is two economic events: close the
      existing lifecycle at this fill, then immediately open a fresh
      lifecycle for the residual in the new direction at this same fill's
      price — never one trade spanning a direction change.

    Exposes running state (`position_qty`, `avg_entry_price`,
    `realized_pnl`, `cumulative_fees`) as read-only properties so a caller
    building a bar-by-bar mark-to-market equity curve (see
    `metrics.py::build_equity_curve`) can read the current open position
    between fills, not just the end-of-run result.
    """

    def __init__(self) -> None:
        self._position_qty = Decimal(0)
        self._avg_entry_price = Decimal(0)
        self._realized_pnl = Decimal(0)
        self._cumulative_fees = Decimal(0)

        # Current open lifecycle's accumulated state — meaningful only
        # while _position_qty != 0.
        self._lifecycle_side: Side | None = None
        self._lifecycle_entry_time: datetime | None = None
        self._lifecycle_quantity_opened = Decimal(0)
        self._lifecycle_realized_pnl = Decimal(0)
        self._lifecycle_exit_notional = Decimal(0)
        self._lifecycle_exit_qty = Decimal(0)

    @property
    def position_qty(self) -> Decimal:
        return self._position_qty

    @property
    def avg_entry_price(self) -> Decimal:
        return self._avg_entry_price

    @property
    def realized_pnl(self) -> Decimal:
        """Cumulative realized P&L across every fill processed so far —
        including partial-reduction fills whose lifecycle hasn't closed
        yet, not just fully-closed trades. See `ClosedTrade.realized_pnl`
        for the per-lifecycle-aggregated view instead.
        """
        return self._realized_pnl

    @property
    def cumulative_fees(self) -> Decimal:
        return self._cumulative_fees

    def apply(self, intent: OrderIntent, fill: Fill) -> ClosedTrade | None:
        """Applies one (intent, fill) pair. Returns the just-closed
        `ClosedTrade` if this fill closed a lifecycle (exactly, or as the
        close half of a flip), else `None`.
        """
        self._cumulative_fees += fill.fee
        signed_qty = fill.quantity if intent.side == Side.LONG else -fill.quantity

        if self._position_qty == 0 or _sign(signed_qty) == _sign(self._position_qty):
            self._open_or_add(signed_qty, fill.fill_price, fill.fill_time)
            return None

        return self._reduce_or_close_or_flip(signed_qty, fill.fill_price, fill.fill_time)

    def force_close(self, price: Decimal, time: datetime) -> ClosedTrade | None:
        """Closes the current open lifecycle (if any) at `price`/`time`.

        Used by `metrics.py` to force-close any position still open at the
        final bar of the input kline sequence, per CLAUDE.md's "Strategy
        Research Operational Design" degenerate-input rule: marks it
        realized so it isn't silently dropped from trade-count/win-rate/
        profit-factor metrics. Returns `None` if already flat.
        """
        if self._position_qty == 0:
            return None
        self._realize(abs(self._position_qty), price)
        trade = self._close_lifecycle(time)
        self._position_qty = Decimal(0)
        self._avg_entry_price = Decimal(0)
        return trade

    def _open_or_add(self, signed_qty: Decimal, price: Decimal, time: datetime) -> None:
        if self._position_qty == 0:
            self._lifecycle_side = Side.LONG if signed_qty > 0 else Side.SHORT
            self._lifecycle_entry_time = time
            self._lifecycle_quantity_opened = Decimal(0)
            self._lifecycle_realized_pnl = Decimal(0)
            self._lifecycle_exit_notional = Decimal(0)
            self._lifecycle_exit_qty = Decimal(0)
            self._avg_entry_price = price
            self._position_qty = signed_qty
        else:
            new_qty = self._position_qty + signed_qty
            self._avg_entry_price = (
                self._avg_entry_price * abs(self._position_qty) + price * abs(signed_qty)
            ) / abs(new_qty)
            self._position_qty = new_qty
        self._lifecycle_quantity_opened += abs(signed_qty)

    def _reduce_or_close_or_flip(
        self, signed_qty: Decimal, price: Decimal, time: datetime
    ) -> ClosedTrade | None:
        closing_qty = min(abs(signed_qty), abs(self._position_qty))
        self._realize(closing_qty, price)

        remaining = abs(signed_qty) - abs(self._position_qty)
        if remaining < 0:
            # Partial reduction — lifecycle continues; cost basis (the
            # entries' size-weighted average) is unchanged by a reduction.
            self._position_qty += signed_qty
            return None

        trade = self._close_lifecycle(time)
        self._position_qty = Decimal(0)
        self._avg_entry_price = Decimal(0)

        if remaining > 0:
            # Flip: the residual, in the new direction, opens a fresh
            # lifecycle at this same fill's price — two economic events at
            # one fill, per the reconstruction rules above.
            residual = remaining if signed_qty > 0 else -remaining
            self._open_or_add(residual, price, time)

        return trade

    def _realize(self, closing_qty: Decimal, price: Decimal) -> None:
        pnl = closing_qty * (price - self._avg_entry_price) * _sign(self._position_qty)
        self._realized_pnl += pnl
        self._lifecycle_realized_pnl += pnl
        self._lifecycle_exit_notional += closing_qty * price
        self._lifecycle_exit_qty += closing_qty

    def _close_lifecycle(self, exit_time: datetime) -> ClosedTrade:
        assert self._lifecycle_side is not None
        assert self._lifecycle_entry_time is not None
        return ClosedTrade(
            side=self._lifecycle_side,
            entry_time=self._lifecycle_entry_time,
            exit_time=exit_time,
            quantity=self._lifecycle_quantity_opened,
            entry_price=self._avg_entry_price,
            exit_price=self._lifecycle_exit_notional / self._lifecycle_exit_qty,
            realized_pnl=self._lifecycle_realized_pnl,
        )


def reconstruct_trades(filled_intents: list[OrderIntent], fills: list[Fill]) -> list[ClosedTrade]:
    """Convenience entry point: walks index-aligned `filled_intents`/
    `fills` (as produced by `backtest.engine.run_backtest`'s
    `BacktestResult`) end to end and returns every fully-closed trade.

    Does not force-close a still-open final position — that requires a
    kline to close against, which is `metrics.py`'s concern (it owns the
    klines sequence), not this module's. Use `PositionTracker` directly
    (as `metrics.py` does) when that's needed.

    Raises `ValueError` (via `zip(..., strict=True)`) if `filled_intents`
    and `fills` have different lengths — that's a caller bug against the
    index-aligned `BacktestResult` contract, and must fail loudly rather
    than silently reconstruct trades from a truncated, misaligned pairing.
    """
    tracker = PositionTracker()
    trades: list[ClosedTrade] = []
    for intent, fill in zip(filled_intents, fills, strict=True):
        closed = tracker.apply(intent, fill)
        if closed is not None:
            trades.append(closed)
    return trades
