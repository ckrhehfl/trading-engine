"""Reconstructs a single-symbol net position from `(OrderIntent, Fill)`
pairs, aggregating each 0 -> nonzero -> 0 position lifecycle into one
`ClosedTrade` record — not one record per fill.

This is a genuinely separate concern from `backtest/`, which stays scoped
to fill simulation only (see `backtest/engine.py`'s `run_backtest`
docstring). This module is the first consumer of a `BacktestResult`'s
`fills`/`filled_intents`, turning a sequence of individual fills into
position-level economics (realized P&L, trade boundaries) that
`backtest/` deliberately does not compute.

**Funding P&L attribution (Strategy Research Task M, additive/opt-in --
see `.planning/sr-m-funding-rate-pipeline.md`)**: `PositionTracker`
optionally accepts a `funding_rates` series (ascending by
`funding_time`) and, as it processes fills in time order, attributes a
funding payment/receipt into `realized_pnl` for every funding timestamp
the current position was open through. `PositionTracker()` /
`reconstruct_trades(filled_intents, fills)` with no `funding_rates`
argument -- the shape every pre-existing caller in this codebase already
uses -- behave byte-for-byte as before this feature existed; funding
awareness only activates when a non-empty series is supplied.

Sign convention (verified against BingX's own official documentation,
not assumed from generic crypto-exchange knowledge -- see
`.planning/sr-m-funding-rate-pipeline.md`): when `funding_rate > 0`,
longs pay shorts; when `funding_rate < 0`, shorts pay longs. Payment =
`abs(position_qty) * funding_row.mark_price * funding_rate`, using
BingX's own historical mark price *at that exact settlement* (returned
alongside the rate by the funding-rate history endpoint) rather than the
position's entry price or a kline's close -- this is what makes the
attribution match how a real exchange actually computes the payment.

**Timestamp-boundary judgment call** (a position entering or exiting at
the *exact* instant of a funding settlement -- documented and tested,
see `.planning/sr-m-funding-rate-pipeline.md` for the full reasoning):
funding for timestamp `T` is attributed using the position size that
existed strictly *before* any fill/close event at `T` is applied. This
means entering exactly at `T` is NOT charged for `T` (the position
didn't exist yet going into the settlement snapshot), while exiting
exactly at `T` IS charged for `T` (the position was still open going
into it). Concretely: `PositionTracker.apply()`/`force_close()` both
call `apply_funding_through(event_time)` — which catches up every
pending funding row with `funding_time <= event_time` — *before*
mutating `_position_qty` for that event.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from backtest.fill import Fill
from metrics.funding import FundingRate
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
    # Funding component of realized_pnl, broken out for transparency --
    # realized_pnl already includes this (it is not an additional amount
    # to add on top). Zero for every trade unless PositionTracker was
    # constructed with a non-empty funding_rates series -- see this
    # module's docstring. Defaults to 0 so any existing direct
    # ClosedTrade(...) construction (none in this codebase today, but
    # nothing prevents a future one) stays valid without naming this
    # field.
    funding_pnl: Decimal = Decimal(0)


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

    def __init__(self, funding_rates: Sequence[FundingRate] | None = None) -> None:
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
        self._lifecycle_funding_pnl = Decimal(0)

        # Funding attribution state -- see this module's docstring.
        # `_funding_rates` stays `()` (empty) when the caller passes
        # nothing, so `apply_funding_through` below is a guaranteed no-op
        # loop (zero iterations) for every pre-existing call site, not a
        # conditionally-skipped branch -- the cheapest possible way to
        # guarantee zero behavior change for an unsupplied series.
        if funding_rates:
            for previous, current in zip(funding_rates, funding_rates[1:]):
                if current.funding_time < previous.funding_time:
                    raise ValueError(
                        "funding_rates must be sorted ascending by funding_time "
                        f"(found {current.funding_time} after {previous.funding_time})"
                    )
        self._funding_rates: Sequence[FundingRate] = funding_rates if funding_rates else ()
        self._funding_cursor = 0
        self._cumulative_funding_pnl = Decimal(0)

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

    @property
    def funding_pnl(self) -> Decimal:
        """Cumulative funding P&L applied so far — always `Decimal(0)`
        when no `funding_rates` were supplied at construction. Tracked
        separately from (but already included in) `realized_pnl`, same
        relationship `ClosedTrade.funding_pnl` has to
        `ClosedTrade.realized_pnl` — see this module's docstring.
        """
        return self._cumulative_funding_pnl

    def apply_funding_through(self, cutoff_time: datetime) -> Decimal:
        """Attributes every pending funding row with `funding_time <=
        cutoff_time` to the position size in effect *right now* (i.e.
        before any fill/close event at `cutoff_time` itself has been
        applied — see this module's docstring for why this specific
        ordering is what makes the entering-at-T-vs-exiting-at-T
        boundary come out the way it does). A funding row is skipped
        (contributes nothing) when the tracker is flat at that instant,
        but the internal cursor still advances past it either way, so a
        later-reopened position is never retroactively charged for a
        timestamp that passed while flat.

        Safe to call redundantly with an already-passed or repeated
        `cutoff_time` (e.g. once per backtest bar in
        `metrics.metrics.build_equity_curve`, in addition to once per
        fill inside `apply`/`force_close`) — the cursor only ever moves
        forward, so a funding row already processed is never
        double-counted. Returns the total funding P&L applied by this
        specific call (not the running cumulative total — see
        `funding_pnl` for that), mainly useful for tests.
        """
        applied = Decimal(0)
        while self._funding_cursor < len(self._funding_rates):
            row = self._funding_rates[self._funding_cursor]
            if row.funding_time > cutoff_time:
                break
            if self._position_qty != 0:
                notional = abs(self._position_qty) * row.mark_price
                payment = -_sign(self._position_qty) * notional * row.funding_rate
                self._realized_pnl += payment
                self._lifecycle_funding_pnl += payment
                self._cumulative_funding_pnl += payment
                applied += payment
            self._funding_cursor += 1
        return applied

    def apply(self, intent: OrderIntent, fill: Fill) -> ClosedTrade | None:
        """Applies one (intent, fill) pair. Returns the just-closed
        `ClosedTrade` if this fill closed a lifecycle (exactly, or as the
        close half of a flip), else `None`.
        """
        self.apply_funding_through(fill.fill_time)
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
        self.apply_funding_through(time)
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
            self._lifecycle_funding_pnl = Decimal(0)
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
            # realized_pnl is price P&L + funding P&L combined (see this
            # module's docstring) -- funding_pnl below is the same amount
            # broken out for transparency, not an additional amount on
            # top of realized_pnl.
            realized_pnl=self._lifecycle_realized_pnl + self._lifecycle_funding_pnl,
            funding_pnl=self._lifecycle_funding_pnl,
        )


def reconstruct_trades(
    filled_intents: list[OrderIntent],
    fills: list[Fill],
    funding_rates: Sequence[FundingRate] | None = None,
) -> list[ClosedTrade]:
    """Convenience entry point: walks index-aligned `filled_intents`/
    `fills` (as produced by `backtest.engine.run_backtest`'s
    `BacktestResult`) end to end and returns every fully-closed trade.

    Does not force-close a still-open final position — that requires a
    kline to close against, which is `metrics.py`'s concern (it owns the
    klines sequence), not this module's. Use `PositionTracker` directly
    (as `metrics.py` does) when that's needed.

    `funding_rates` (default `None` — purely additive, every existing
    caller omitting it is unaffected) is passed straight through to a
    fresh `PositionTracker` — see this module's docstring for the
    attribution design and sign convention.

    Raises `ValueError` (via `zip(..., strict=True)`) if `filled_intents`
    and `fills` have different lengths — that's a caller bug against the
    index-aligned `BacktestResult` contract, and must fail loudly rather
    than silently reconstruct trades from a truncated, misaligned pairing.
    """
    tracker = PositionTracker(funding_rates)
    trades: list[ClosedTrade] = []
    for intent, fill in zip(filled_intents, fills, strict=True):
        closed = tracker.apply(intent, fill)
        if closed is not None:
            trades.append(closed)
    return trades
