"""Pure EH decision and later modeled-fill transitions for one episode."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _clock, _require, window


Action = Literal["wait", "enter", "hold", "reduce_half", "exit", "reenter", "end", "unresolved"]


@dataclass(frozen=True)
class EntryBounds:
    stop: Fraction
    max_price: Fraction

    def __post_init__(self) -> None:
        _require(type(self.stop) is Fraction and type(self.max_price) is Fraction
                 and 0 < self.stop < self.max_price, "invalid_eh_entry_bounds")


@dataclass(frozen=True)
class Values:
    """Additional utilities on the same information set and currency scale."""

    decision_at: datetime
    wait: Fraction | None = None
    enter: Fraction | None = None
    hold: Fraction | None = None
    reduce_half: Fraction | None = None
    exit: Fraction | None = None

    def __post_init__(self) -> None:
        _clock(self.decision_at)
        _require(all(x is None or type(x) is Fraction for x in
                     (self.wait, self.enter, self.hold, self.reduce_half, self.exit)),
                 "invalid_eh_action_value")


@dataclass(frozen=True)
class Request:
    action: Literal["enter", "reduce_half", "exit", "reenter"]
    execute_at: datetime
    quantity: int
    bounds: EntryBounds | None = None
    funds: tuple[Fraction, Fraction] | None = None


@dataclass(frozen=True)
class Episode:
    symbol: str
    start_at: datetime
    end_at: datetime
    risk_budget: Fraction
    cost_bps: int
    book: cash.CashBook
    first_lot_id: int
    last_at: datetime
    attempts: int = 0
    stop: Fraction | None = None
    entered_at: datetime | None = None
    closed_at: datetime | None = None
    pending: Request | None = None


@dataclass(frozen=True)
class Step:
    episode: Episode
    action: Action
    reason: str


def start_episode(
    book: cash.CashBook, symbol: str, start_at: datetime, end_at: datetime,
    *, risk_budget: Fraction, cost_bps: int,
) -> Episode:
    _clock(start_at)
    _clock(end_at)
    _require(type(symbol) is str and bool(symbol) and not book.lots and not book.indeterminate,
             "eh_episode_requires_known_flat_book")
    _require(MINUTE <= end_at - start_at <= 60 * MINUTE
             and (end_at - start_at) % MINUTE == MINUTE - MINUTE
             and start_at.second == start_at.microsecond == 0
             and type(risk_budget) is Fraction and risk_budget > 0
             and type(cost_bps) is int and cost_bps in (33, 43), "invalid_eh_episode_limits")
    return Episode(symbol, start_at, end_at, risk_budget, cost_bps,
                   book, book.next_lot_id, start_at - MINUTE)


def _quantity(episode: Episode, bounds: EntryBounds) -> tuple[int, tuple[Fraction, Fraction]]:
    """Freeze quantity at the worst permitted entry price, before reading its open."""
    balance = episode.book.cash
    cost = Fraction(episode.cost_bps, 10_000)
    if episode.attempts == 0:
        available_risk = episode.risk_budget / 2
    else:
        losses = sum((max(Fraction(), -leg.net_pnl) for leg in episode.book.realized_exits
                      if leg.lot_id >= episode.first_lot_id), Fraction())
        available_risk = max(Fraction(), episode.risk_budget - losses)
    worst_per_unit = bounds.max_price * (1 + cost) - bounds.stop
    maximum_notional = min(balance * cash.ORDER_FRACTION, balance / (1 + cost))
    return min(available_risk // worst_per_unit, maximum_notional // bounds.max_price), (balance, balance)


def decide(
    episode: Episode, at: datetime, *, visible_minute: Minute | None = None,
    values: Values | None = None, bounds: EntryBounds | None = None,
    reset_at: datetime | None = None, margin: Fraction = Fraction(),
) -> Step:
    """Choose from timestamped forecasts; a missing forecast cannot force a buy.

    The caller validates reset evidence from completed post-exit bars and supplies
    its confirmation clock. A minute used for the structural close must have
    finished and become available. The caller owns whole-session input support.
    """
    _clock(at)
    _require(episode.pending is None and at > episode.last_at and at >= episode.start_at
             and at.second == at.microsecond == 0,
             "eh_decision_before_prior_request_resolved")
    _require(type(margin) is Fraction and margin >= 0
             and (values is None or values.decision_at == at), "invalid_eh_decision_values")
    if reset_at is not None:
        _clock(reset_at)
        _require(episode.closed_at is not None and episode.closed_at < reset_at <= at,
                 "eh_reset_evidence_not_post_exit")
    close = None
    if visible_minute is not None:
        start = visible_minute.start_at
        _require(start + 3 * MINUTE <= at, "eh_close_before_scheduled_availability")
        observed = SessionInputs(episode.symbol, start, start + MINUTE, {start: visible_minute})
        if not window(observed, 0, 1, at).issues:
            close = Fraction(visible_minute.ohlc[3])

    episode = replace(episode, last_at=at)
    if episode.book.inventory_uncertain:
        return Step(episode, "unresolved", "inventory_unresolved")

    lot = episode.book.lots[0] if episode.book.lots else None
    execute_at = at + MINUTE
    if execute_at > episode.end_at:
        if lot is not None:
            book = cash.freeze(episode.book, "eh_exit_clock_missed")
            return Step(replace(episode, book=book), "unresolved", "exit_clock_missed")
        return Step(episode, "end", "episode_expired")

    if lot is not None:
        _require(episode.stop is not None and episode.entered_at is not None, "eh_held_stop_missing")
        if visible_minute is not None and visible_minute.start_at < episode.entered_at:
            close = None
        action: Literal["reduce_half", "exit"]
        if close is not None and close <= episode.stop:
            action, reason = "exit", "structural_close_failure"
        elif execute_at == episode.end_at:
            action, reason = "exit", "fixed_episode_end"
        elif close is None or values is None or values.hold is None or values.exit is None:
            return Step(episode, "hold", "management_unsupported")
        else:
            half = values.reduce_half if lot.quantity > 1 and not lot.half_reduced else None
            if half is not None and half > values.hold + margin and half > values.exit:
                action, reason = "reduce_half", "higher_value_of_reduction"
            elif values.exit > values.hold + margin:
                action, reason = "exit", "higher_value_of_exit"
            else:
                return Step(episode, "hold", "retain_position")
        quantity = lot.quantity // 2 if action == "reduce_half" else lot.quantity
        return Step(replace(episode, pending=Request(action, execute_at, quantity)), action, reason)

    if episode.book.indeterminate:
        return Step(episode, "unresolved", "book_unresolved")
    if episode.attempts >= 2 or (episode.attempts and episode.closed_at is None):
        return Step(episode, "end", "entry_attempts_closed")
    if episode.end_at - execute_at < 5 * MINUTE:
        return Step(episode, "end", "too_little_time_for_entry")
    if episode.attempts and reset_at is None:
        return Step(episode, "wait", "fresh_reset_required")
    if (values is None or values.enter is None or values.wait is None
            or bounds is None or close is None):
        return Step(episode, "wait", "entry_unsupported")
    if not bounds.stop < close <= bounds.max_price:
        return Step(episode, "wait", "observed_close_outside_entry_bounds")
    if values.enter <= max(Fraction(), values.wait) + margin:
        return Step(episode, "wait", "entry_value_not_higher")
    quantity, funds = _quantity(episode, bounds)
    if not quantity:
        return Step(episode, "wait", "insufficient_risk_or_cash_for_one_unit")
    enter_action: Literal["enter", "reenter"] = "reenter" if episode.attempts else "enter"
    request = Request(enter_action, execute_at, quantity, bounds, funds)
    return Step(replace(episode, pending=request, attempts=episode.attempts + 1),
                enter_action, "higher_value_of_entry")


def apply_modeled_open(episode: Episode, at: datetime, price: Fraction | None) -> tuple[Episode, cash.Decision]:
    """Apply the fixed request at its exact next-open clock, once."""
    _clock(at)
    request = episode.pending
    _require(request is not None and at == request.execute_at
             and (price is None or type(price) is Fraction and price > 0), "invalid_eh_execution")
    stop, closed_at, entered_at = episode.stop, episode.closed_at, episode.entered_at
    if request.action in ("enter", "reenter"):
        bounds = request.bounds
        _require(bounds is not None and request.funds is not None, "eh_entry_reservation_missing")
        if price is not None and not bounds.stop < price <= bounds.max_price:
            outcome = cash.Decision(episode.book, "open_outside_frozen_entry_bounds")
        else:
            outcome = cash.enter(episode.book, episode.symbol, entry_price=price, observed_prices={},
                                 cost_bps=episode.cost_bps, decision_funds=request.funds,
                                 requested_quantity=request.quantity)
        stop = bounds.stop if outcome.filled else None
        entered_at = at if outcome.filled else None
        closed_at = None
    elif request.action == "reduce_half":
        outcome = cash.reduce_half(episode.book, episode.symbol, exit_price=price,
                                   requested_quantity=request.quantity)
    else:
        outcome = cash.exit(episode.book, episode.symbol, quantity=request.quantity, exit_price=price)
        if outcome.filled:
            stop, closed_at, entered_at = None, at, None
    return replace(episode, book=outcome.book, stop=stop, closed_at=closed_at, entered_at=entered_at,
                   pending=None, last_at=at), outcome
