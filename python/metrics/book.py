"""A position book that can hold several legs on one instrument.

Trade Management Task A, Stage 1. See
`.planning/tm-a-trader-style-position-model.md` for the full design and
the research behind it.

## Why this exists

Every strategy in this project has tracked its position as one signed
quantity -- `_position_sign` plus `_position_quantity`, netted into
`metrics.position.PositionTracker`'s single `position_qty`. Under that
model a short opened against an existing long does not become a second
position: it **reduces** the long. There is no object to close later, no
separate entry price, no separate P&L.

So the sentence the operator kept describing -- *hold the long, add a
short when the move weakens, close only the short on the drop* -- had no
representation at all. Several things were measured and rejected in its
place (a stop, a delayed entry, a conviction resize); those measurements
are sound but they were not measurements of that idea.

This module is the representation.

## The constraint this design is built around

Researched, not assumed (Binance and Bybit hedge-mode API docs,
2026-08-29): **the venue tracks exactly one position per direction.** A
single liquidation price covers everything on a side. So "several longs"
is *client-side bookkeeping*, never exchange state.

That splits cleanly and the split must never blur:

    exchange truth   <=1 LONG + <=1 SHORT per symbol
    our book         the legs that COMPOSE those two

`reconcile()` is the contract between them. It is an assertion rather
than a report, because a book that can silently drift from venue state is
a position-mismatch generator, and "zero position mismatches" is a Paper
Trading Pass Criteria line item.

## Three conventions, decided once here rather than per strategy

**Purpose is a fixed enum.** Free-form strings would be more expressive
and would make reconciliation and reporting unauditable.

**Legs close by explicit id, not FIFO or LIFO.** This follows directly
from the requirement: "close only the short" is a statement about a
specific leg. A queue discipline would close whichever leg happened to be
oldest and defeat the entire point. FIFO remains available as a
convenience for callers that genuinely do not care which leg goes.

**Exposure is reported gross as well as net.** Margin is not netted by
the venue -- holding both sides uses *more* margin than either alone --
so a delta-flat book is not risk-free and must never be sized as if it
were. `gross()` exists so a risk check can refuse to treat a hedge as
free.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from schemas.order_intent import Side


class LegPurpose(StrEnum):
    """What a leg is *for*, which is not derivable from its side.

    A short can be the whole thesis or a hedge against a long held on a
    different one, and the two want different management. Fixed rather
    than free-form so reconciliation and per-purpose reporting stay
    auditable.
    """

    CORE = "core"
    """The primary thesis. Closing it means the view has changed."""

    TACTICAL = "tactical"
    """A trade around the core -- opened on a shorter-horizon read and
    closed independently, leaving the core untouched."""

    HEDGE = "hedge"
    """Opened to reduce the book's exposure rather than to express a
    view. Costs funding on its own side and does not free the margin of
    the leg it offsets."""

    RUNNER = "runner"
    """The remainder left on after partial exits, usually managed with a
    trailing rule rather than a fixed target."""


@dataclass(frozen=True)
class Leg:
    """One tranche of exposure, with its own entry and its own exit rule.

    Frozen: a leg's identity and entry are facts about something that
    already happened. Reducing a leg produces a new `Leg` with a smaller
    quantity rather than mutating this one, so a caller holding a
    reference cannot be surprised.
    """

    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    entry_time: datetime
    purpose: LegPurpose
    leg_id: UUID = field(default_factory=uuid4)
    invalidation: Decimal | None = None
    """Price at which THIS leg's own thesis is wrong. Per-leg, because a
    tactical leg can be wrong while the core is still right -- which the
    single-stop model could not express."""
    target: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"leg quantity must be positive, got {self.quantity}")
        if self.entry_price <= 0:
            raise ValueError(f"leg entry_price must be positive, got {self.entry_price}")

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity if self.side is Side.LONG else -self.quantity

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        """Mark-to-market P&L for this leg alone.

        The figure the single-position model could not produce: "the
        short made money while the long was still open".
        """
        move = mark_price - self.entry_price
        return move * self.quantity * (1 if self.side is Side.LONG else -1)


@dataclass(frozen=True)
class LegClose:
    """The realised outcome of closing all or part of one leg."""

    leg_id: UUID
    symbol: str
    side: Side
    purpose: LegPurpose
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_time: datetime
    exit_time: datetime
    realized_pnl: Decimal


class BookReconciliationError(AssertionError):
    """The book and the venue disagree about what is held.

    An `AssertionError` subclass because this is a broken invariant, not
    a runtime condition to recover from: continuing to trade against a
    book that no longer describes reality is how duplicate orders and
    position mismatches happen.
    """


class Book:
    """The legs open on one or more symbols.

    Deliberately not a `PositionTracker` replacement. `PositionTracker`
    reconstructs realised P&L from `(OrderIntent, Fill)` pairs and stays
    the authority on that; this holds the *intent-side* structure that
    tracker nets away, and `reconcile` checks the two still agree.
    """

    __slots__ = ("_legs", "_closes")

    def __init__(self, legs: Iterable[Leg] = ()) -> None:
        self._legs: dict[UUID, Leg] = {}
        for leg in legs:
            self._legs[leg.leg_id] = leg
        self._closes: list[LegClose] = []

    # -- reading -----------------------------------------------------

    @property
    def legs(self) -> tuple[Leg, ...]:
        """Open legs, in insertion order. A tuple so a caller cannot
        mutate the book by accident."""
        return tuple(self._legs.values())

    @property
    def closes(self) -> tuple[LegClose, ...]:
        return tuple(self._closes)

    def leg(self, leg_id: UUID) -> Leg | None:
        return self._legs.get(leg_id)

    def for_symbol(self, symbol: str) -> tuple[Leg, ...]:
        return tuple(leg for leg in self._legs.values() if leg.symbol == symbol)

    def by_purpose(self, symbol: str, purpose: LegPurpose) -> tuple[Leg, ...]:
        return tuple(leg for leg in self.for_symbol(symbol) if leg.purpose is purpose)

    def net_by_side(self, symbol: str) -> dict[Side, Decimal]:
        """Quantity per side -- exactly the shape the venue reports in
        hedge mode, so this is what `reconcile` compares against."""
        totals = {Side.LONG: Decimal(0), Side.SHORT: Decimal(0)}
        for leg in self.for_symbol(symbol):
            totals[leg.side] += leg.quantity
        return totals

    def net(self, symbol: str) -> Decimal:
        """Signed net quantity -- what a one-way-mode venue would show,
        and what `PositionTracker.position_qty` holds."""
        return sum((leg.signed_quantity for leg in self.for_symbol(symbol)), Decimal(0))

    def gross(self, symbol: str) -> Decimal:
        """Total quantity across both sides.

        Reported separately from `net` because the venue does **not** net
        margin: a long and a short of equal size consume more margin than
        either alone while showing `net == 0`. A risk check reading only
        `net` would treat a fully hedged book as free.
        """
        return sum((leg.quantity for leg in self.for_symbol(symbol)), Decimal(0))

    def unrealized_pnl(self, symbol: str, mark_price: Decimal) -> Decimal:
        return sum(
            (leg.unrealized_pnl(mark_price) for leg in self.for_symbol(symbol)),
            Decimal(0),
        )

    # -- writing -----------------------------------------------------

    def open(self, leg: Leg) -> Leg:
        if leg.leg_id in self._legs:
            raise ValueError(f"leg {leg.leg_id} is already open")
        self._legs[leg.leg_id] = leg
        return leg

    def close(
        self,
        leg_id: UUID,
        *,
        exit_price: Decimal,
        exit_time: datetime,
        quantity: Decimal | None = None,
    ) -> LegClose:
        """Close all or part of one leg, **named explicitly**.

        The explicit id is the whole point: "close only the short" is a
        statement about a particular leg, and a FIFO or LIFO queue would
        close whichever happened to be oldest instead.

        `quantity=None` closes the leg fully. A partial close leaves the
        remainder open under the same `leg_id`, so a runner keeps its
        identity and its original entry price rather than being
        re-averaged.
        """
        leg = self._legs.get(leg_id)
        if leg is None:
            raise KeyError(f"no open leg with id {leg_id}")
        closing = leg.quantity if quantity is None else quantity
        if closing <= 0:
            raise ValueError(f"closing quantity must be positive, got {closing}")
        if closing > leg.quantity:
            raise ValueError(
                f"cannot close {closing} of a {leg.quantity} leg -- a close larger than "
                f"the leg is a flip, which must be expressed as a close plus a new leg "
                f"so the two legs keep separate entries"
            )

        move = exit_price - leg.entry_price
        realized = move * closing * (1 if leg.side is Side.LONG else -1)
        record = LegClose(
            leg_id=leg.leg_id,
            symbol=leg.symbol,
            side=leg.side,
            purpose=leg.purpose,
            quantity=closing,
            entry_price=leg.entry_price,
            exit_price=exit_price,
            entry_time=leg.entry_time,
            exit_time=exit_time,
            realized_pnl=realized,
        )
        self._closes.append(record)

        if closing == leg.quantity:
            del self._legs[leg_id]
        else:
            self._legs[leg_id] = replace(leg, quantity=leg.quantity - closing)
        return record

    def realized_pnl(self, symbol: str | None = None) -> Decimal:
        return sum(
            (c.realized_pnl for c in self._closes if symbol is None or c.symbol == symbol),
            Decimal(0),
        )

    def realized_pnl_by_purpose(self, symbol: str | None = None) -> dict[LegPurpose, Decimal]:
        """Realised P&L split by what each leg was for.

        The question the single-position model could not answer: did the
        tactical overlay pay for itself, or was the core carrying it?
        """
        out: dict[LegPurpose, Decimal] = {p: Decimal(0) for p in LegPurpose}
        for c in self._closes:
            if symbol is None or c.symbol == symbol:
                out[c.purpose] += c.realized_pnl
        return out

    # -- the contract with the venue ---------------------------------

    def reconcile(
        self,
        symbol: str,
        *,
        venue_long: Decimal,
        venue_short: Decimal,
        tolerance: Decimal = Decimal("0"),
    ) -> None:
        """Assert the book still describes what the venue holds.

        Raises `BookReconciliationError` on a mismatch. Deliberately
        raising rather than returning a flag: a book that has drifted is
        already producing wrong sizes, and the failure mode of continuing
        is duplicate orders and position mismatches -- both of which this
        project's Paper Trading Pass Criteria require to be zero.

        `tolerance` exists for venues that round reported quantities, and
        defaults to exact so a caller has to opt into slack rather than
        inherit it.
        """
        held = self.net_by_side(symbol)
        problems = []
        for side, venue_qty in ((Side.LONG, venue_long), (Side.SHORT, venue_short)):
            drift = abs(held[side] - venue_qty)
            if drift > tolerance:
                problems.append(
                    f"{side.value}: book {held[side]} vs venue {venue_qty} (drift {drift})"
                )
        if problems:
            raise BookReconciliationError(
                f"book and venue disagree on {symbol}: " + "; ".join(problems)
            )
