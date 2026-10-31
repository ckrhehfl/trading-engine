"""Pure ED-H1 unit outcomes and integer-share research account replay.

Only caller-supplied typed actions, session clocks and Minute coordinates enter
this module. There is no reader, logger, transport, broker or order route.
Archived OPEN is an explicitly assumed scheduled fill proxy: its publication
availability and executable liquidity are NOT certified by the later OHLC row.
Completed CLOSE decisions do enforce both end+2 and the declared available_at.
The legacy round-trip 33/43bp assumptions are charged once on entry notional;
they are not certified current commission, tax, spread, impact or execution.
Integer vendor-price units do not certify physical shares or corporate-action
basis. Missing histories remain sticky; a conditional result is never promoted
to an identified full-population account.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research import intraday_campaign_account as account
from research import intraday_campaign_cash_book as cash
from research.intraday_ed_inputs import H1Parent, Minute, STATES
from research.intraday_pattern_events import Bar


MINUTE = account.MINUTE
PriceKey = tuple[str, datetime]
ARMS = frozenset({"ED-H1-RULE", "ED-H1-WITHOUT-FILING", "ED-H1-LGBM",
                  "ED-H1-LGBM-WITHOUT-FILING"})
LabelStatus = Literal["known_traded", "known_declined_entry", "unknown"]
ASSUMPTIONS = (
    "scheduled_OPEN_proxy_not_certified_available_or_executable",
    "same_time_sale_proceeds_reusable",
    "legacy_cost_scenario_not_current_fee_tax_certification",
    "liquidity_partial_fill_gap_and_physical_price_basis_uncertified",
    "adverse_LOW_diagnostic_covers_only_causally_observed_close_prefix",
)


class EDReplayRefusal(ValueError):
    """Malformed contracts fail; absent observations produce explicit unknowns."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise EDReplayRefusal(reason)


@dataclass(frozen=True)
class EDCandidate:
    parent_id: str
    symbol: str
    opening: datetime
    closing: datetime
    decision_at: datetime
    entry_at: datetime
    cap_exit_at: datetime
    structural_stop: Fraction

    def __post_init__(self) -> None:
        cash._name(self.parent_id, "parent_id")
        cash._name(self.symbol, "symbol")
        for field in ("opening", "closing", "decision_at", "entry_at", "cap_exit_at"):
            object.__setattr__(self, field, account._clock(getattr(self, field)))
        object.__setattr__(self, "structural_stop", account._price(self.structural_stop))
        trigger_end = self.decision_at - 2 * MINUTE
        _require(self.opening.date() == self.closing.date() == self.entry_at.date()
                 and self.opening < self.closing, "invalid_session_clocks")
        _require(trigger_end >= self.opening + 30 * MINUTE
                 and (trigger_end - self.opening) % (5 * MINUTE) == 0 * MINUTE,
                 "ed_completed_five_minute_grid_required")
        expected = self.opening + (
            (self.decision_at + MINUTE - self.opening) // (5 * MINUTE) + 1) * 5 * MINUTE
        _require(self.entry_at == expected and self.entry_at == trigger_end + 5 * MINUTE,
                 "ed_strict_next_open_after_latency_required")
        _require(self.cap_exit_at == self.entry_at + 60 * MINUTE,
                 "sixty_minute_cap_required")
        _require(self.cap_exit_at <= self.closing - 15 * MINUTE,
                 "full_cap_before_liquidation_required")


def candidate_for_parent(parent: H1Parent, *, closing: datetime, parent_id: str) -> EDCandidate:
    """Adapt only a genuine H1 parent; caller owns its immutable identity."""
    _require(type(parent) is H1Parent and len(parent.preceding) == 5,
             "typed_five_bar_h1_parent_required")
    bars = (*parent.preceding, parent.trigger)
    for index, bar in enumerate(bars):
        _require(type(bar) is Bar, "typed_bar_required")
        for clock in (bar.start_at, bar.end_at, bar.available_at):
            account._clock(clock)
        _require(bar.end_at == bar.start_at + 5 * MINUTE
                 and bar.available_at == bar.end_at + 2 * MINUTE,
                 "ed_bar_clock_required")
        if index:
            _require(bars[index - 1].end_at == bar.start_at, "contiguous_parent_bars_required")
        op, hi, lo, cl = map(account._price, (bar.open, bar.high, bar.low, bar.close))
        _require(lo <= min(op, cl) <= max(op, cl) <= hi, "invalid_parent_ohlc")
    _require(parent.trigger.close > max(bar.high for bar in parent.preceding),
             "upside_break_parent_required")
    entry = parent.trigger.end_at + 5 * MINUTE
    return EDCandidate(parent_id, parent.symbol, parent.opening, closing,
                       parent.decision_at, entry, entry + 60 * MINUTE,
                       Fraction(parent.structural_stop))


def _row(minutes: Mapping[PriceKey, Minute], symbol: str, at: datetime) -> Minute | None:
    row = minutes.get((symbol, at))
    if row is None:
        return None
    _require(type(row) is Minute and row.symbol == symbol and row.start_at == at,
             "minute_identity_changed")
    account._clock(row.start_at)
    available = account._clock(row.available_at)
    _require(available >= at + MINUTE, "availability_precedes_minute_completion")
    _require(row.price_state in STATES, "invalid_price_state")
    if row.price_state != "observed":
        _require(row.ohlc is None, "unknown_price_has_payload")
    else:
        _require(type(row.ohlc) is tuple and len(row.ohlc) == 4,
                 "observed_price_requires_ohlc")
    return row


def _opening(minutes: Mapping[PriceKey, Minute], symbol: str, at: datetime) -> Fraction | None:
    row = _row(minutes, symbol, at)
    if row is None or row.price_state != "observed":
        return None
    # Only OPEN is consulted. H/L/C and their later availability cannot decide
    # admission here. This proxy assumption is retained in every result.
    assert row.ohlc is not None
    return account._price(row.ohlc[0])


def _completed(
    minutes: Mapping[PriceKey, Minute], symbol: str, start: datetime, observed_at: datetime,
) -> tuple[Minute | None, str | None]:
    row = _row(minutes, symbol, start)
    if row is None:
        return None, "missing_minute"
    if row.price_state != "observed":
        return row, "price_" + row.price_state
    if start + 3 * MINUTE > observed_at or row.available_at > observed_at:
        return row, "close_not_available"
    assert row.ohlc is not None
    op, hi, lo, cl = map(account._price, row.ohlc)
    _require(lo <= min(op, cl) <= max(op, cl) <= hi, "invalid_minute_ohlc")
    return row, None


@dataclass(frozen=True)
class EDPathObservation:
    minute_start: datetime
    observed_at: datetime
    minute: Minute | None
    issue: str | None = None


@dataclass(frozen=True)
class EDUnitLabel:
    candidate: EDCandidate
    status: LabelStatus
    reason: str
    entry_price: Fraction | None = None
    exit_price: Fraction | None = None
    exit_at: datetime | None = None
    mature_at: datetime | None = None
    gross: Fraction | None = None
    net33: Fraction | None = None
    net43: Fraction | None = None
    path: tuple[EDPathObservation, ...] = ()
    first_unknown: PriceKey | None = None
    minimum_observed_low_return: Fraction | None = None
    low_touched_stop: bool = False
    assumptions: tuple[str, ...] = ASSUMPTIONS
    entry_reason: str = "scheduled_OPEN_proxy"
    exit_reason: str | None = None
    entry_source: str = "caller_supplied_Minute.ohlc[0]_scheduled_OPEN_proxy"
    exit_source: str | None = None

    @property
    def known_filled_unit(self) -> bool:
        """Only these labels count toward minimum filled-unit training support."""
        return self.status == "known_traded"


def ed_unit_label(candidate: EDCandidate, minutes: Mapping[PriceKey, Minute]) -> EDUnitLabel:
    """Same prospective one-unit exit contract for every method.

    OPEN<=stop is a known policy no-fill outcome of zero, with zero trades and
    no cost. It is not a realized zero trade or a filled training example.
    Missing possible earlier stops terminate the identifiable path immediately.
    Scheduled exits run before same-clock completed CLOSE observations, so no
    bar observed at/after a chosen exit can select the stop or completeness.
    """
    _require(type(candidate) is EDCandidate and isinstance(minutes, Mapping),
             "typed_candidate_and_minutes_required")
    price = _opening(minutes, candidate.symbol, candidate.entry_at)
    if price is None:
        return EDUnitLabel(candidate, "unknown", "entry_price_unknown",
                           first_unknown=(candidate.symbol, candidate.entry_at),
                           entry_reason="entry_price_unknown")
    if price <= candidate.structural_stop:
        return EDUnitLabel(candidate, "known_declined_entry", "invalidated_entry", price,
                           mature_at=candidate.entry_at + 3 * MINUTE,
                           gross=Fraction(), net33=Fraction(), net43=Fraction(),
                           entry_reason="invalidated_entry")
    exit_at, reason = candidate.cap_exit_at, "time_cap"
    path = []
    low = None
    touched = False
    at = candidate.entry_at + 3 * MINUTE
    while at < exit_at:
        start = at - 3 * MINUTE
        row, issue = _completed(minutes, candidate.symbol, start, at)
        path.append(EDPathObservation(start, at, row, issue))
        if issue is not None:
            return EDUnitLabel(candidate, "unknown", issue, price,
                               path=tuple(path), first_unknown=(candidate.symbol, start),
                               minimum_observed_low_return=low, low_touched_stop=touched)
        assert row is not None and row.ohlc is not None
        current_low = Fraction(row.ohlc[2]) / price - 1
        low = current_low if low is None else min(low, current_low)
        touched = touched or row.ohlc[2] <= candidate.structural_stop
        if row.ohlc[3] <= candidate.structural_stop:
            exit_at, reason = account._stop_schedule(at, candidate.cap_exit_at)
        at += MINUTE
    exit_price = _opening(minutes, candidate.symbol, exit_at)
    if exit_price is None:
        return EDUnitLabel(candidate, "unknown", "exit_price_unknown", price,
                           exit_at=exit_at, path=tuple(path),
                           first_unknown=(candidate.symbol, exit_at),
                           minimum_observed_low_return=low, low_touched_stop=touched,
                           exit_reason=reason,
                           exit_source="caller_supplied_Minute.ohlc[0]_scheduled_OPEN_proxy")
    gross = exit_price / price - 1
    return EDUnitLabel(candidate, "known_traded", reason, price, exit_price,
                       exit_at, exit_at + 3 * MINUTE, gross,
                       gross - Fraction(33, 10_000), gross - Fraction(43, 10_000),
                       tuple(path), minimum_observed_low_return=low,
                       low_touched_stop=touched, exit_reason=reason,
                       exit_source="caller_supplied_Minute.ohlc[0]_scheduled_OPEN_proxy")


@dataclass(frozen=True)
class EDSession:
    opening: datetime
    closing: datetime
    policy_state: Literal["available", "unavailable"] = "available"
    reason: str = "policy_available"

    def __post_init__(self) -> None:
        object.__setattr__(self, "opening", account._clock(self.opening))
        object.__setattr__(self, "closing", account._clock(self.closing))
        _require(self.opening.date() == self.closing.date() and self.opening < self.closing,
                 "invalid_session_clocks")
        _require(self.policy_state in ("available", "unavailable"), "invalid_policy_state")
        cash._name(self.reason, "reason")


@dataclass(frozen=True)
class EDAction:
    at: datetime
    status: Literal["accept", "decline", "unknown"]
    reason: str
    candidate: EDCandidate | None = None
    symbol: str | None = None
    parent_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", account._clock(self.at))
        _require(self.status in ("accept", "decline", "unknown"), "invalid_action_state")
        cash._name(self.reason, "reason")
        if self.candidate is not None:
            _require(type(self.candidate) is EDCandidate and self.at == self.candidate.decision_at,
                     "action_candidate_clock_mismatch")
            _require(self.symbol in (None, self.candidate.symbol)
                     and self.parent_id in (None, self.candidate.parent_id),
                     "action_candidate_identity_mismatch")
            object.__setattr__(self, "symbol", self.candidate.symbol)
            object.__setattr__(self, "parent_id", self.candidate.parent_id)
        _require(self.status != "accept" or self.candidate is not None,
                 "accepted_action_requires_candidate")
        if self.symbol is not None:
            cash._name(self.symbol, "symbol")
        if self.parent_id is not None:
            cash._name(self.parent_id, "parent_id")


@dataclass(frozen=True)
class EDLot:
    candidate: EDCandidate
    quantity: int
    entry_price: Fraction
    entry_cost: Fraction
    exit_at: datetime
    exit_reason: str = "time_cap"

    def __post_init__(self) -> None:
        _require(type(self.candidate) is EDCandidate and type(self.quantity) is int
                 and self.quantity > 0, "invalid_ed_lot")
        object.__setattr__(self, "entry_price", cash._amount(self.entry_price, "entry_price"))
        object.__setattr__(self, "entry_cost", cash._amount(self.entry_cost, "entry_cost", zero=True))
        object.__setattr__(self, "exit_at", account._clock(self.exit_at))
        _require(self.candidate.entry_at < self.exit_at <= self.candidate.cap_exit_at,
                 "invalid_scheduled_exit")


@dataclass(frozen=True)
class EDBook:
    arm: str
    cash: Fraction = cash.INITIAL_CAPITAL
    lots: tuple[EDLot, ...] = ()
    indeterminate_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.arm in ARMS, "unknown_ed_arm")
        object.__setattr__(self, "cash", cash._amount(self.cash, "cash", zero=True))
        _require(type(self.lots) is tuple and all(type(lot) is EDLot for lot in self.lots),
                 "typed_ed_lots_required")
        _require(len({lot.candidate.symbol for lot in self.lots}) == len(self.lots),
                 "duplicate_position")
        _require(type(self.indeterminate_reasons) is tuple, "immutable_reasons_required")
        for reason in self.indeterminate_reasons:
            cash._name(reason, "reason")

    @property
    def indeterminate(self) -> bool:
        return bool(self.indeterminate_reasons)

    @property
    def initial_capital(self) -> Fraction:
        return cash.INITIAL_CAPITAL


@dataclass(frozen=True)
class EDAccountState:
    book: EDBook
    cost_bps: Fraction = Fraction(33)
    risk_scope: account.RiskScope = "continuous"
    daily_anchor: account.PeriodAnchor | None = None
    weekly_anchor: account.PeriodAnchor | None = None
    monthly_anchor: account.PeriodAnchor | None = None
    peak_nav: Fraction = cash.INITIAL_CAPITAL
    maximum_drawdown: Fraction = Fraction()
    last_at: datetime | None = None
    first_unknown_at: datetime | None = None
    total_entry_cost: Fraction = Fraction()
    realized_gross_pnl: Fraction = Fraction()


@dataclass(frozen=True)
class EDAccountEvent:
    at: datetime
    kind: str
    reason: str
    parent_id: str | None = None
    symbol: str | None = None
    quantity: int = 0
    cash_delta: Fraction = Fraction()
    assumed_cost: Fraction = Fraction()
    scheduled_exit_at: datetime | None = None


@dataclass(frozen=True)
class EDNAVSnapshot:
    at: datetime
    phase: str
    full_nav: Fraction | None
    observed_nav: Fraction | None
    cash: Fraction
    period_returns: tuple[tuple[str, Fraction], ...] = ()
    entry_gates: tuple[str, ...] = ()
    drawdown: Fraction | None = None
    total_entry_cost: Fraction = Fraction()
    same_quantity_gross_nav: Fraction | None = None


@dataclass(frozen=True)
class EDAccountResult:
    state: EDAccountState
    events: tuple[EDAccountEvent, ...]
    snapshots: tuple[EDNAVSnapshot, ...]
    scenario: str = "full_population_with_explicit_unknown_actions"
    assumptions: tuple[str, ...] = ASSUMPTIONS


def new_ed_state(arm: str, *, cost_bps: cash.Exact = 33,
                 risk_scope: account.RiskScope = "continuous") -> EDAccountState:
    _require(risk_scope in ("continuous", "day_local_counterfactual"), "invalid_risk_scope")
    return EDAccountState(EDBook(arm), account._registered_cost(cost_bps), risk_scope)


def _freeze(state: EDAccountState, at: datetime, reason: str) -> EDAccountState:
    return replace(state, book=replace(state.book, indeterminate_reasons=cash._freeze_reasons(
        state.book.indeterminate_reasons, reason)), first_unknown_at=state.first_unknown_at or at)


def _value(
    state: EDAccountState, at: datetime, marks: Mapping[str, Fraction | None], phase: str,
) -> tuple[EDAccountState, EDNAVSnapshot, tuple[str, ...]]:
    nav, missing = cash._marked_nav(state.book.cash, tuple(
        (lot.candidate.symbol, lot.quantity) for lot in state.book.lots), marks)
    for symbol in missing:
        state = _freeze(state, at, "missing_held_price:" + symbol)
    if state.book.indeterminate:
        gates = ("account_indeterminate",)
        return state, EDNAVSnapshot(at, phase, None, nav, state.book.cash,
                                    entry_gates=gates, total_entry_cost=state.total_entry_cost), gates
    assert nav is not None
    updates, returns, gates, peak, maxdd, dd = account._risk_observation(
        nav, at, state.risk_scope, state.daily_anchor, state.weekly_anchor,
        state.monthly_anchor, state.peak_nav, state.maximum_drawdown)
    state = replace(state, peak_nav=peak, maximum_drawdown=maxdd, **updates)
    # Restore the actually charged cost on the identical quantity path. This is
    # attribution, not a zero-cost rerun that might admit different quantities.
    return state, EDNAVSnapshot(at, phase, nav, nav, state.book.cash, returns, gates, dd,
                                state.total_entry_cost, nav + state.total_entry_cost), gates


def _validate_state(state: EDAccountState, arm: str, cost_bps: cash.Exact) -> None:
    _require(type(state) is EDAccountState and type(state.book) is EDBook
             and state.book.arm == arm and state.cost_bps == account._registered_cost(cost_bps),
             "account_contract_changed")
    _require(state.risk_scope in ("continuous", "day_local_counterfactual"), "invalid_risk_scope")
    _require(all(type(value) is Fraction for value in (
        state.cost_bps, state.peak_nav, state.maximum_drawdown,
        state.total_entry_cost, state.realized_gross_pnl)), "exact_account_amounts_required")
    _require(state.book.indeterminate == (state.first_unknown_at is not None),
             "unknown_history_mismatch")
    account._price(state.peak_nav)
    cash._amount(state.maximum_drawdown, "maximum_drawdown", zero=True)
    cash._amount(state.total_entry_cost, "total_entry_cost", zero=True)
    _require(state.book.cash + sum((lot.quantity * lot.entry_price for lot in state.book.lots), Fraction())
             == cash.INITIAL_CAPITAL + state.realized_gross_pnl - state.total_entry_cost,
             "cash_flow_conservation_mismatch")
    _require(all(lot.entry_price > lot.candidate.structural_stop
                 and lot.entry_cost == lot.quantity * lot.entry_price * state.cost_bps / 10_000
                 for lot in state.book.lots), "held_entry_contract_mismatch")
    if state.last_at is not None:
        account._clock(state.last_at)
    if state.first_unknown_at is not None:
        account._clock(state.first_unknown_at)
    for anchor in (state.daily_anchor, state.weekly_anchor, state.monthly_anchor):
        if anchor is not None:
            _require(type(anchor) is account.PeriodAnchor, "typed_period_anchor_required")
            account._price(anchor.nav)


def replay_ed_account(
    actions: Iterable[EDAction], minutes: Mapping[PriceKey, Minute],
    sessions: Iterable[EDSession], *, arm: str, cost_bps: cash.Exact = 33,
    state: EDAccountState | None = None,
    compress_idle_minutes: bool = False,
) -> EDAccountResult:
    """Replay a continuous full-budget ED arm on the supplied complete calendar.

    At each minute: scheduled OPEN exits; actions/unknowns; available completed
    CLOSE marks and stop scheduling; valuation/risk; then deterministic entries.
    Full-population unknowns freeze entry permanently while known scheduled
    exits continue. Missing scheduled exits retain their exact lot and cash and
    are never retried. A model-unavailable session freezes even with no parents.
    """
    _require(type(compress_idle_minutes) is bool, "boolean_idle_compression_required")
    initial = new_ed_state(arm, cost_bps=cost_bps)
    state = initial if state is None else state
    _validate_state(state, arm, cost_bps)
    _require(isinstance(minutes, Mapping), "minute_mapping_required")
    calendar = tuple(sessions)
    _require(all(type(session) is EDSession for session in calendar), "typed_sessions_required")
    _require(all(a.closing < b.opening for a, b in zip(calendar, calendar[1:])),
             "chronological_nonoverlapping_sessions_required")
    _require(state.last_at is None or not calendar or calendar[0].opening > state.last_at,
             "nonchronological_resumption")
    by_day = {session.opening.date(): session for session in calendar}
    _require(len(by_day) == len(calendar), "duplicate_session")
    decisions: dict[datetime, list[EDAction]] = {}
    entries: dict[datetime, list[EDCandidate]] = {}
    parent_ids = set()
    parent_coordinates = set()
    for action in actions:
        _require(type(action) is EDAction, "typed_action_required")
        session = by_day.get(action.at.date())
        _require(session is not None and session.opening <= action.at <= session.closing,
                 "action_outside_calendar")
        if action.parent_id is not None:
            _require(action.parent_id not in parent_ids, "duplicate_parent_action")
            parent_ids.add(action.parent_id)
        if action.candidate is not None:
            candidate = action.candidate
            _require((candidate.opening, candidate.closing) == (session.opening, session.closing),
                     "candidate_calendar_mismatch")
            coordinate = candidate.symbol, candidate.opening
            _require(coordinate not in parent_coordinates, "one_parent_per_symbol_session_required")
            parent_coordinates.add(coordinate)
            if action.status == "accept":
                entries.setdefault(candidate.entry_at, []).append(candidate)
        decisions.setdefault(action.at, []).append(action)
    for batch in entries.values():
        batch.sort(key=lambda item: (item.decision_at, item.symbol, item.parent_id))
    event_clocks = sorted(set(decisions) | set(entries))
    events: list[EDAccountEvent] = []
    snapshots: list[EDNAVSnapshot] = []

    def freeze_at(at: datetime, reason: str, *, symbol=None, parent_id=None) -> None:
        nonlocal state
        before = state.book.indeterminate_reasons
        state = _freeze(state, at, reason)
        if state.book.indeterminate_reasons != before:
            events.append(EDAccountEvent(at, "unknown", reason, parent_id, symbol))

    def value_at(at: datetime, marks, phase: str):
        nonlocal state
        before = state.book.indeterminate_reasons
        state, snapshot, gates = _value(state, at, marks, phase)
        snapshots.append(snapshot)
        for reason in state.book.indeterminate_reasons:
            if reason not in before:
                events.append(EDAccountEvent(at, "unknown", reason))
        return gates

    for session in calendar:
        at = session.opening
        while at <= session.closing:
            for lot in tuple(state.book.lots):
                if lot.exit_at != at:
                    continue
                candidate = lot.candidate
                price = _opening(minutes, candidate.symbol, at)
                if price is None:
                    freeze_at(at, "missing_exit_price:" + candidate.symbol,
                              symbol=candidate.symbol, parent_id=candidate.parent_id)
                    events.append(EDAccountEvent(at, "exit", "exit_price_unknown",
                                                candidate.parent_id, candidate.symbol))
                else:
                    proceeds = cash._gross_exit_proceeds(lot.quantity, price)
                    state = replace(state, book=replace(state.book, cash=state.book.cash + proceeds,
                        lots=tuple(held for held in state.book.lots if held != lot)),
                        realized_gross_pnl=state.realized_gross_pnl + lot.quantity * (price - lot.entry_price))
                    events.append(EDAccountEvent(at, "exit", lot.exit_reason, candidate.parent_id,
                                                candidate.symbol, lot.quantity, proceeds))
            if at == session.opening and session.policy_state == "unavailable":
                freeze_at(at, "policy_unavailable:" + session.reason)
            for action in sorted(decisions.get(at, ()), key=lambda item: (
                    item.symbol or "", item.parent_id or "", item.status)):
                events.append(EDAccountEvent(at, "action", action.reason,
                                            action.parent_id, action.symbol))
                if action.status == "unknown":
                    freeze_at(at, "unknown_action:" + action.reason,
                              symbol=action.symbol, parent_id=action.parent_id)
            marks: dict[str, Fraction | None] = {}
            for lot in tuple(state.book.lots):
                candidate = lot.candidate
                if lot.exit_at <= at:
                    # An unknown scheduled exit never becomes a later fill or
                    # requests any post-exit holding-price path.
                    marks[candidate.symbol] = None
                    continue
                start = at - 3 * MINUTE
                if start < candidate.entry_at:
                    marks[candidate.symbol] = lot.entry_price
                    continue
                row, issue = _completed(minutes, candidate.symbol, start, at)
                if issue is not None:
                    marks[candidate.symbol] = None
                    freeze_at(at, f"holding_path_unknown:{candidate.symbol}:{start.isoformat()}:{issue}",
                              symbol=candidate.symbol, parent_id=candidate.parent_id)
                    continue
                assert row is not None and row.ohlc is not None
                close = Fraction(row.ohlc[3])
                marks[candidate.symbol] = close
                if close <= candidate.structural_stop:
                    exit_at, reason = account._stop_schedule(at, candidate.cap_exit_at)
                    changed = replace(lot, exit_at=exit_at, exit_reason=reason)
                    state = replace(state, book=replace(state.book, lots=tuple(
                        changed if item == lot else item for item in state.book.lots)))
                    events.append(EDAccountEvent(at, "stop", reason, candidate.parent_id,
                                                candidate.symbol, scheduled_exit_at=exit_at))
            gates = value_at(at, marks, "before_entries")
            for candidate in entries.get(at, ()):
                if gates:
                    events.append(EDAccountEvent(at, "entry", "entry_blocked:" + ",".join(gates),
                                                candidate.parent_id, candidate.symbol))
                    continue
                if any(lot.candidate.symbol == candidate.symbol for lot in state.book.lots):
                    events.append(EDAccountEvent(at, "entry", "duplicate_position",
                                                candidate.parent_id, candidate.symbol))
                    continue
                price = _opening(minutes, candidate.symbol, at)
                if price is None:
                    freeze_at(at, "missing_entry_price:" + candidate.symbol,
                              symbol=candidate.symbol, parent_id=candidate.parent_id)
                    events.append(EDAccountEvent(at, "entry", "entry_price_unknown",
                                                candidate.parent_id, candidate.symbol))
                elif price <= candidate.structural_stop:
                    events.append(EDAccountEvent(at, "entry", "invalidated_entry",
                                                candidate.parent_id, candidate.symbol))
                else:
                    nav, missing = cash._marked_nav(state.book.cash, tuple(
                        (lot.candidate.symbol, lot.quantity) for lot in state.book.lots), marks)
                    assert nav is not None and not missing
                    quantity, _, cost, delta, reason = cash._cash_entry_amounts(
                        nav, nav, state.book.cash, price, state.cost_bps / 10_000)
                    if quantity:
                        lot = EDLot(candidate, quantity, price, cost, candidate.cap_exit_at)
                        state = replace(state, book=replace(state.book, cash=state.book.cash + delta,
                            lots=state.book.lots + (lot,)), total_entry_cost=state.total_entry_cost + cost)
                        marks[candidate.symbol] = price
                    events.append(EDAccountEvent(at, "entry", reason, candidate.parent_id,
                                                candidate.symbol, quantity, delta, cost))
                gates = value_at(at, marks, "after_entry")
            state = replace(state, last_at=at)
            if compress_idle_minutes and not state.book.lots and at < session.closing:
                # No exposure, action or pending entry changes cash, anchors or
                # gates inside this same session before the next event. Keep
                # openings/closings and every action/entry/held minute exactly.
                index = bisect_right(event_clocks, at)
                next_event = event_clocks[index] if index < len(event_clocks) else session.closing
                at = min(next_event, session.closing)
            else:
                at += MINUTE
    return EDAccountResult(state, tuple(events), tuple(snapshots))


def replay_ed_daily_counterfactuals(
    actions: Iterable[EDAction], minutes: Mapping[PriceKey, Minute], sessions: Iterable[EDSession],
    *, arm: str, cost_bps: cash.Exact = 33,
    compress_idle_minutes: bool = False,
) -> tuple[EDAccountResult, ...]:
    """Fresh-capital day-local diagnostics, never stitched continuous equity."""
    calendar, decisions = tuple(sessions), tuple(actions)
    _require(all(type(session) is EDSession for session in calendar), "typed_sessions_required")
    days = {session.opening.date() for session in calendar}
    _require(all(type(action) is EDAction and action.at.date() in days for action in decisions),
             "action_outside_calendar")
    return tuple(replace(replay_ed_account(
        (action for action in decisions if action.at.date() == session.opening.date()),
        minutes, (session,), arm=arm, cost_bps=cost_bps,
        state=new_ed_state(arm, cost_bps=cost_bps, risk_scope="day_local_counterfactual"),
        compress_idle_minutes=compress_idle_minutes),
        scenario="fresh_day_counterfactual_no_continuous_weekly_monthly_claim")
        for session in calendar)
