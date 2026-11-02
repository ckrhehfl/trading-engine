"""Pure EE joint account: frozen causal inputs, no IO/orders.

The archived nominal price unit is not certified physical shares, KRW or fills.
Policy selection, source authentication, actual execution and storage are absent.
"""

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from fractions import Fraction

from research import intraday_campaign_account as ec
from research import intraday_campaign_cash_book as cash
from research.intraday_ed_inputs import Minute, STATES
from research.intraday_trader_paths import PRIMARY_COST, SENSITIVITY_COST


M = timedelta(minutes=1)
CLAIM = "archived_price_unit_nominal_counterfactual_no_physical_share_KRW_or_fill_certification"


def _need(ok, reason):
    if not ok:
        raise ec.AccountRefusal(reason)


def _aware(value):
    _need(isinstance(value, datetime) and value.utcoffset() is not None, "aware_clock_required")
    return value.astimezone(ec.KST)


@dataclass(frozen=True)
class Candidate:
    """Already chosen policy/slot and pre-entry levels; no future outcome fields."""

    slot: str
    policy_id: str
    symbol: str
    signal_at: datetime
    available_at: datetime
    stop: Fraction
    target: Fraction

    def __post_init__(self):
        _need(self.slot in ("A", "B"), "unknown_slot")
        for value in (self.policy_id, self.symbol):
            _need(type(value) is str and bool(value) and value == value.strip(), "invalid_identity")
        object.__setattr__(self, "signal_at", ec._clock(self.signal_at))
        object.__setattr__(self, "available_at", _aware(self.available_at))
        _need(self.available_at == self.signal_at + 2 * M, "signal_end_plus_two_required")
        object.__setattr__(self, "stop", ec._price(self.stop))
        object.__setattr__(self, "target", ec._price(self.target))
        _need(self.stop < self.target, "ordered_frozen_levels_required")

    @property
    def strategy(self):
        return self.slot

    @property
    def decision_at(self):
        return self.available_at

    @property
    def entry_at(self):
        return self.available_at.replace(second=0, microsecond=0) + M

    @property
    def cap_exit_at(self):
        return self.entry_at + 60 * M


@dataclass(frozen=True)
class Held:
    candidate: Candidate
    exit_at: datetime
    exit_reason: str = "time_cap"
    observed_count: int = 0
    maximum_close: Fraction | None = None
    path_unknown: bool = False


@dataclass(frozen=True)
class State(ec.AccountState):
    holdings: tuple[Held, ...] = ()
    policies: tuple[str, str] = ()


@dataclass(frozen=True)
class AccountResult:
    state: State
    events: tuple[ec.AccountEvent, ...]
    snapshots: tuple[ec.NAVSnapshot, ...]
    scenario: str = CLAIM
    idle_minutes_compressed: bool = False


def new_state(policies, mode="A+B", *, cost_bps=33):
    _need(type(policies) is tuple and len(policies) == 2
          and len(set(policies)) == 2
          and all(type(x) is str and x and x == x.strip() for x in policies),
          "two_frozen_policies_required")
    _need(mode in ("A", "B", "A+B"), "EE_mode_required")
    _need(Fraction(cost_bps) in (PRIMARY_COST * 10000, SENSITIVITY_COST * 10000),
          "registered_cost_required")
    return State(**vars(ec.new_state(mode, cost_bps=cost_bps)), policies=policies)


def _row(minutes, symbol, at):
    row = minutes.get((symbol, at))
    if row is None:
        return None
    _need(type(row) is Minute and row.symbol == symbol and row.start_at == at,
          "minute_identity_changed")
    _need(row.price_state in STATES, "invalid_price_state")
    _need((type(row.ohlc) is tuple and len(row.ohlc) == 4) if row.price_state == "observed"
          else row.ohlc is None, "price_state_payload_mismatch")
    return row


def _opening(minutes, opens, symbol, at):
    supplied = opens.get((symbol, at))
    if supplied is None:
        return None
    price = ec._price(supplied)
    row = _row(minutes, symbol, at)
    if row is not None and (row.price_state != "observed" or ec._price(row.ohlc[0]) != price):
        return None
    return price


def _close(minutes, symbol, at, decision):
    row = _row(minutes, symbol, at)
    if row is None:
        return None
    _need(at + M <= _aware(row.available_at) <= decision, "held_minute_unavailable_at_decision")
    if row.price_state != "observed":
        return None
    high, low, close = map(ec._price, row.ohlc[1:])
    _need(low <= close <= high, "invalid_held_hlc_order")
    return close


def replay(candidates: Iterable[Candidate], minutes: Mapping, open_prices: Mapping,
           session_openings: Mapping, session_closes: Mapping, *, policies,
           mode="A+B", cost_bps=33, state: State | None = None,
           compress_idle_minutes: bool = False):
    """OPEN exits → newly available held CLOSE → NAV/risk → sorted entries.

    A close at q is observed at q+3 and can fill only at q+4. An earlier
    unresolved close prohibits later trigger inference for that lot. A known
    scheduled cap may still sell after a freeze; that never repairs history.
    Current account marks remain required even where the path's cap absorbs a
    late close trigger. Missing scheduled exit is retained and never retried.
    Carry only the preceding returned typed state for this same frozen account;
    this pure continuation initializes capital once and opens no saved state.
    """
    contract = new_state(policies, mode, cost_bps=cost_bps)
    _need(type(compress_idle_minutes) is bool, "boolean_idle_compression_required")
    state = contract if state is None else state
    _need(type(state) is State and state.policies == policies and state.mode == mode
          and state.cost_bps == contract.cost_bps and state.risk_scope == "continuous",
          "frozen_policy_or_account_contract_changed")
    ec._validate_state(state)
    for held in state.holdings:
        _need(type(held) is Held and type(held.candidate) is Candidate
              and held.candidate.policy_id == policies[held.candidate.slot == "B"]
              and type(held.observed_count) is int and held.observed_count >= 0
              and type(held.path_unknown) is bool
              and ((held.observed_count == 0 and held.maximum_close is None)
                   or (held.observed_count > 0 and held.maximum_close is not None)),
              "invalid_held_continuation")
        if held.maximum_close is not None:
            ec._price(held.maximum_close)
    _need((state.unknown_at is not None) == state.book.indeterminate,
          "unknown_history_continuation_mismatch")
    if state.unknown_at is not None:
        _need(state.last_at is not None and ec._clock(state.unknown_at) <= ec._clock(state.last_at),
              "unknown_history_clock_changed")
    _need(isinstance(minutes, Mapping) and isinstance(open_prices, Mapping), "price_maps_required")
    _need(set(session_openings) == set(session_closes), "session_calendar_mismatch")
    sessions = []
    for day in sorted(session_openings):
        opening, closing = ec._clock(session_openings[day]), ec._clock(session_closes[day])
        _need(opening.date() == closing.date() == day and opening < closing
              and (state.last_at is None or opening > state.last_at), "invalid_session_order")
        sessions.append((day, opening, closing))
    plans = tuple(candidates)
    entries = {}
    for plan in plans:
        _need(type(plan) is Candidate, "causal_candidate_required")
        _need(plan.policy_id == policies[plan.slot == "B"], "frozen_policy_mismatch")
        _need(plan.slot in {s.strategy for s in state.book.sleeves}, "candidate_slot_not_allocated")
        day = plan.signal_at.date()
        _need(day in session_openings, "candidate_outside_calendar")
        opening, closing = ec._clock(session_openings[day]), ec._clock(session_closes[day])
        _need(opening + 30 * M <= plan.signal_at and plan.decision_at <= opening + 240 * M
              and (plan.signal_at - opening) % (5 * M) == timedelta(0)
              and plan.cap_exit_at <= closing, "EE_candidate_clock_required")
        entries.setdefault(plan.entry_at, []).append(plan)
    _need(len(set(plans)) == len(plans), "duplicate_candidate_plan")
    for batch in entries.values():
        batch.sort(key=lambda x: (x.decision_at, x.symbol, x.slot))
    entry_clocks = sorted(entries)
    events, snapshots = [], []

    def value(at, marks, phase):
        nonlocal state
        old = state.book.indeterminate_reasons
        state, snapshot, gates = ec._value(state, at, marks, phase)
        if snapshot is not None:
            snapshots.append(snapshot)
        for reason in state.book.indeterminate_reasons:
            if reason not in old:
                events.append(ec.AccountEvent(at, "unknown", reason))
        return gates

    for _, opening, closing in sessions:
        at = opening
        while at <= closing:
            for held in tuple(state.holdings):
                if held.exit_at != at:
                    continue
                plan = held.candidate
                decision = cash.exit(state.book, plan.slot, plan.symbol,
                                     exit_price=_opening(minutes, open_prices, plan.symbol, at))
                state = replace(state, book=decision.book,
                    holdings=tuple(x for x in state.holdings if x != held) if decision.filled else state.holdings,
                    unknown_at=(state.unknown_at or at) if decision.book.indeterminate else state.unknown_at)
                events.append(ec.AccountEvent(at, "exit", held.exit_reason if decision.filled else decision.reason,
                    plan.slot, plan.symbol, decision.quantity, decision.cash_delta, decision.assumed_cost,
                    plan.decision_at))
            marks = {}
            for held in tuple(state.holdings):
                plan = held.candidate
                if held.exit_at <= at or held.path_unknown:
                    marks[plan.symbol] = None
                    continue
                lot = next(lot for lot in state.book.lots if lot.symbol == plan.symbol)
                if at < plan.entry_at + 3 * M:
                    marks[plan.symbol] = lot.entry_price
                    continue
                close = _close(minutes, plan.symbol, at - 3 * M, at)
                marks[plan.symbol] = close
                if close is None:
                    changed = replace(held, path_unknown=True)
                else:
                    count = held.observed_count + 1
                    maximum = close if held.maximum_close is None else max(held.maximum_close, close)
                    changed = replace(held, observed_count=count, maximum_close=maximum)
                    reason = ("structural_close_invalidation" if close <= plan.stop else
                              "known_target_close_reached" if close >= plan.target else
                              "response_failure" if count >= 10 and maximum <= lot.entry_price
                              and close < lot.entry_price else None)
                    if reason is not None and at + M < plan.cap_exit_at:
                        changed = replace(changed, exit_at=at + M, exit_reason=reason)
                        events.append(ec.AccountEvent(at, "trigger", reason, plan.slot, plan.symbol,
                            decision_at=plan.decision_at, scheduled_exit_at=at + M))
                state = replace(state, holdings=tuple(changed if x == held else x for x in state.holdings))
            gates = value(at, marks, "before_entries")
            for plan in entries.get(at, ()):
                if gates:
                    events.append(ec.AccountEvent(at, "entry", "entry_blocked:" + ",".join(gates),
                                                   plan.slot, plan.symbol, decision_at=plan.decision_at))
                    continue
                duplicate = any(lot.symbol == plan.symbol for lot in state.book.lots)
                price = None if duplicate else _opening(minutes, open_prices, plan.symbol, at)
                refusal = ("entry_outside_frozen_levels" if price is not None
                           and not plan.stop < price < plan.target else
                           "entry_cost_space_not_strict" if price is not None
                           and plan.target - price <= price * PRIMARY_COST else None)
                if refusal:
                    events.append(ec.AccountEvent(at, "entry", refusal, plan.slot, plan.symbol,
                                                   decision_at=plan.decision_at))
                    continue
                decision = cash.enter(state.book, plan.slot, plan.symbol, entry_price=price,
                                      observed_prices=marks, cost_bps=state.cost_bps)
                holdings = state.holdings
                if decision.filled:
                    holdings += (Held(plan, plan.cap_exit_at),)
                    marks[plan.symbol] = price
                state = replace(state, book=decision.book, holdings=holdings)
                events.append(ec.AccountEvent(at, "entry", decision.reason, plan.slot, plan.symbol,
                    decision.quantity, decision.cash_delta, decision.assumed_cost, plan.decision_at))
                gates = value(at, marks, "after_entry")
            state = replace(state, last_at=at)
            if compress_idle_minutes and not state.holdings and not state.book.lots and at < closing:
                index = bisect_right(entry_clocks, at)
                next_entry = entry_clocks[index] if index < len(entry_clocks) else closing
                at = min(next_entry, closing)
            else:
                at += M
    return AccountResult(state, tuple(events), tuple(snapshots), CLAIM, compress_idle_minutes)
