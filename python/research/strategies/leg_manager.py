"""Turns leg decisions into order intents, and keeps the book honest.

Trade Management Task A, Stage 2. See
`.planning/tm-a-trader-style-position-model.md`.

A strategy using this says what it wants in leg terms -- *open a tactical
short*, *close that leg*, *take a third off the runner* -- and gets back
the `OrderIntent`s that express it, with `metrics.book.Book` updated to
match. It never has to reason about net quantity, which is precisely the
reasoning that made the operator's scenario inexpressible.

## What this is not

It is **not** a strategy, and it takes no view. It has no signal, no
thresholds and no parameters to fit; it is the bookkeeping layer a
leg-based strategy sits on top of. That separation is deliberate: the
project's failures have come from signals, and a mechanism with nothing
to tune cannot add to `N`.

It is also **not** a risk check. `RiskGateway` remains the only thing
that approves an order, and `gross_exposure` is exposed here precisely so
a caller can hand the venue-relevant number -- not the netted one -- to
whatever does check it.

## The one thing worth reading twice

`close_leg` emits an order on the **opposite** side of the leg it closes.
Under one-way netting that is indistinguishable from opening a position
the other way, and that ambiguity is exactly what made "close only the
short" unrepresentable before. The distinction is carried by the book,
not by the order: `Book.close` names the leg by id, so the strategy's own
accounting knows a hedge was retired rather than a new short opened, even
though the wire sees one `BUY`.

Under hedge mode the venue keeps the distinction too, via `positionSide`
-- but that translation belongs to Stage 4 and the adapters, not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from metrics.book import Book, Leg, LegClose, LegPurpose
from schemas.order_intent import OrderIntent, OrderType, Side


def _opposite(side: Side) -> Side:
    return Side.SHORT if side is Side.LONG else Side.LONG


class LegManager:
    """Owns a `Book` and produces the intents that keep it true.

    One instance per strategy instance. Intents accumulate in a pending
    list that the strategy drains once per bar via `drain()`, so several
    leg actions taken on one bar arrive at `run_backtest` together and
    execute at that bar's price rather than being spread across bars.
    """

    __slots__ = ("_symbol", "_book", "_pending", "_signal_timeframe")

    def __init__(self, *, symbol: str, signal_timeframe: str) -> None:
        self._symbol = symbol
        self._signal_timeframe = signal_timeframe
        self._book = Book()
        self._pending: list[OrderIntent] = []

    @property
    def book(self) -> Book:
        return self._book

    @property
    def symbol(self) -> str:
        return self._symbol

    # -- exposure ----------------------------------------------------

    def net_exposure(self) -> Decimal:
        return self._book.net(self._symbol)

    def gross_exposure(self) -> Decimal:
        """Total across both sides.

        The number a risk check must use. The venue does not net margin,
        so a long and a short of equal size consume more margin than
        either alone while `net_exposure()` reads zero -- a check on the
        net would treat a fully hedged book as free.
        """
        return self._book.gross(self._symbol)

    def legs(self, purpose: LegPurpose | None = None) -> tuple[Leg, ...]:
        if purpose is None:
            return self._book.for_symbol(self._symbol)
        return self._book.by_purpose(self._symbol, purpose)

    def has(self, purpose: LegPurpose) -> bool:
        return bool(self.legs(purpose))

    # -- actions -----------------------------------------------------

    def open_leg(
        self,
        *,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        time: datetime,
        purpose: LegPurpose,
        invalidation: Decimal | None = None,
        target: Decimal | None = None,
    ) -> Leg:
        """Open a new leg and queue the order that establishes it.

        `price` is the strategy's own decision-time reference (the signal
        bar's close, by this codebase's convention), not the eventual fill
        -- `backtest.fill` fills a `GUARDED_MARKET` at the next bar's
        open. Same documented approximation `risk_management.OpenPosition`
        already carries: it affects the strategy's own exit arithmetic,
        never the reported P&L, which `metrics` reconstructs from real
        fills.
        """
        leg = Leg(
            symbol=self._symbol,
            side=side,
            quantity=quantity,
            entry_price=price,
            entry_time=time,
            purpose=purpose,
            invalidation=invalidation,
            target=target,
        )
        self._book.open(leg)
        self._queue(side, quantity, time)
        return leg

    def close_leg(
        self,
        leg_id: UUID,
        *,
        price: Decimal,
        time: datetime,
        quantity: Decimal | None = None,
    ) -> LegClose:
        """Close all or part of one named leg.

        The emitted order is on the opposite side. On the wire that is
        indistinguishable from opening the other way -- the book is what
        preserves the distinction, which is the entire reason a book
        exists here.
        """
        leg = self._book.leg(leg_id)
        if leg is None:
            raise KeyError(f"no open leg with id {leg_id}")
        closing = leg.quantity if quantity is None else quantity
        record = self._book.close(leg_id, exit_price=price, exit_time=time, quantity=closing)
        self._queue(_opposite(leg.side), closing, time)
        return record

    def add_to_leg(
        self, leg_id: UUID, *, quantity: Decimal, price: Decimal, time: datetime
    ) -> Leg:
        """Scale into an existing leg.

        Implemented as a **new leg with the same purpose**, not as a
        re-average of the old one. Keeping the tranches separate is what
        lets each keep its own entry and its own invalidation -- averaging
        them would rebuild exactly the single blended position this module
        exists to get away from.

        Risk note, from the research behind this task: adding is only safe
        while the *combined* size still fits the budget. This class does
        not enforce that -- it has no view on the budget -- so a caller
        must re-check `gross_exposure()` after adding.
        """
        existing = self._book.leg(leg_id)
        if existing is None:
            raise KeyError(f"no open leg with id {leg_id}")
        return self.open_leg(
            side=existing.side,
            quantity=quantity,
            price=price,
            time=time,
            purpose=existing.purpose,
            invalidation=existing.invalidation,
            target=existing.target,
        )

    def close_all(
        self, *, price: Decimal, time: datetime, purpose: LegPurpose | None = None
    ) -> tuple[LegClose, ...]:
        """Close every leg, or every leg of one purpose.

        Iterates over a snapshot because closing mutates the book.
        """
        return tuple(
            self.close_leg(leg.leg_id, price=price, time=time)
            for leg in tuple(self.legs(purpose))
        )

    def triggered(self, bar_high: Decimal, bar_low: Decimal) -> tuple[Leg, ...]:
        """Legs whose own invalidation was touched by this bar.

        Per-leg, which is the point: a tactical leg can be invalidated
        while the core's thesis is untouched. The caller decides what to
        do -- this only reports, so a strategy that wants to widen or
        ignore a level can.
        """
        hit = []
        for leg in self.legs():
            if leg.invalidation is None:
                continue
            if leg.side is Side.LONG and bar_low <= leg.invalidation:
                hit.append(leg)
            elif leg.side is Side.SHORT and bar_high >= leg.invalidation:
                hit.append(leg)
        return tuple(hit)

    # -- emission ----------------------------------------------------

    def _queue(self, side: Side, quantity: Decimal, time: datetime) -> None:
        self._pending.append(
            OrderIntent(
                intent_id=uuid4(),
                symbol=self._symbol,
                side=side,
                order_type=OrderType.GUARDED_MARKET,
                quantity=quantity,
                limit_price=None,
                signal_timeframe=self._signal_timeframe,
                created_at=time,
            )
        )

    def drain(self) -> Sequence[OrderIntent] | None:
        """Everything queued since the last call, or `None` if nothing.

        `None` rather than an empty list so a strategy can return this
        straight through: `run_backtest` treats `None` as "no action" and
        an empty sequence identically, but `None` matches what every
        existing strategy returns on a quiet bar.
        """
        if not self._pending:
            return None
        out = tuple(self._pending)
        self._pending.clear()
        return out
