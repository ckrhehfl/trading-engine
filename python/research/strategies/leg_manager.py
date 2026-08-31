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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from backtest.fill import Fill
from metrics.book import Book, Leg, LegClose, LegPurpose
from schemas.order_intent import OrderIntent, OrderType, Side


@dataclass(frozen=True)
class LegAction:
    """What one emitted `OrderIntent` was meant to do to the book.

    The signal-time book records a leg at the **signal bar's close**,
    because that is the only price a strategy knows when it decides. The
    real fill happens at the next bar's open, with slippage. For a
    strategy's own exit arithmetic that approximation is fine and this
    module always said so.

    It stops being fine the moment a *reported* figure is taken from the
    signal book. Trade Management Task C did exactly that -- it published
    `Book.realized_pnl_by_purpose` as the tactical overlay's edge -- and
    at that size the gap is not a rounding detail: a 2bps round trip on
    150 hedges is the same order of magnitude as the edge being measured.

    So the intent carries this record, and `replay_fills` rebuilds a
    second, **execution** book from the real `Fill` prices. Two books,
    deliberately: the signal book is what the strategy reasons with, the
    execution book is what may be reported.
    """

    kind: str  # "open" | "close"
    leg_id: UUID
    purpose: LegPurpose
    side: Side
    quantity: Decimal
    invalidation: Decimal | None = None
    target: Decimal | None = None


def replay_fills(
    actions: Mapping[UUID, LegAction],
    fills: Sequence[Fill],
    *,
    symbol: str,
) -> Book:
    """Rebuild a `Book` from real fills, so reported P&L is real P&L.

    Correlated by `Fill.intent_id` rather than by list position: the
    engine does guarantee `filled_intents[i]` produced `fills[i]`, but an
    id match cannot silently drift if that contract ever changes, and the
    id is already on the `Fill`. A fill with no entry in `actions` is a
    non-leg order and is skipped; an action whose opening order never
    filled leaves nothing to close, and the matching close is skipped
    too rather than raising -- an unfilled order is a real outcome, not a
    bookkeeping error.

    Partial closes are supported by closing the recorded quantity, which
    is what the signal book did.
    """
    book = Book()
    live: dict[UUID, UUID] = {}  # signal leg id -> execution leg id
    for fill in fills:
        action = actions.get(fill.intent_id)
        if action is None:
            continue
        if action.kind == "open":
            leg = book.open(
                Leg(
                    symbol=symbol,
                    side=action.side,
                    quantity=fill.quantity,
                    entry_price=fill.fill_price,
                    entry_time=fill.fill_time,
                    purpose=action.purpose,
                    invalidation=action.invalidation,
                    target=action.target,
                )
            )
            live[action.leg_id] = leg.leg_id
        else:
            # `get`, not `pop`. `Book.close` supports a partial close and
            # leaves the leg open with the remainder, so popping here
            # discarded the mapping while the leg was still live -- and
            # the action closing the rest would then find no execution
            # leg and be skipped, silently dropping both the open
            # quantity and its realised P&L from the execution book.
            execution_id = live.get(action.leg_id)
            if execution_id is None:
                continue
            open_leg = book.leg(execution_id)
            if open_leg is None:
                continue
            book.close(
                execution_id,
                exit_price=fill.fill_price,
                exit_time=fill.fill_time,
                quantity=min(fill.quantity, open_leg.quantity),
            )
            # Retire the mapping only once the leg is genuinely gone.
            if book.leg(execution_id) is None:
                live.pop(action.leg_id, None)
    return book


def _opposite(side: Side) -> Side:
    return Side.SHORT if side is Side.LONG else Side.LONG


class LegManager:
    """Owns a `Book` and produces the intents that keep it true.

    One instance per strategy instance. Intents accumulate in a pending
    list that the strategy drains once per bar via `drain()`, so several
    leg actions taken on one bar arrive at `run_backtest` together and
    execute at that bar's price rather than being spread across bars.
    """

    __slots__ = ("_actions", "_book", "_pending", "_signal_timeframe", "_symbol")

    def __init__(self, *, symbol: str, signal_timeframe: str) -> None:
        self._symbol = symbol
        self._signal_timeframe = signal_timeframe
        self._book = Book()
        self._pending: list[OrderIntent] = []
        self._actions: dict[UUID, LegAction] = {}

    @property
    def book(self) -> Book:
        return self._book

    @property
    def actions(self) -> Mapping[UUID, LegAction]:
        """Every emitted intent's meaning, for `replay_fills`."""
        return self._actions

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
        self._queue(
            side, quantity, time,
            LegAction(kind="open", leg_id=leg.leg_id, purpose=purpose, side=side,
                      quantity=quantity, invalidation=invalidation, target=target),
        )
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
        self._queue(
            _opposite(leg.side), closing, time,
            LegAction(kind="close", leg_id=leg_id, purpose=leg.purpose, side=leg.side,
                      quantity=closing),
        )
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

    def _queue(
        self, side: Side, quantity: Decimal, time: datetime, action: LegAction
    ) -> None:
        intent_id = uuid4()
        self._actions[intent_id] = action
        self._pending.append(
            OrderIntent(
                intent_id=intent_id,
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
