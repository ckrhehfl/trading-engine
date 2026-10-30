"""Pure EC causal cash-account replay over caller-supplied price coordinates.

This is an observed-candidate research scenario, not full-universe execution or
a live RiskGateway implementation. Source identity and signal missingness belong
to the caller. No future outcome/status field is accepted as an admission rule.
Prices are exact archived units; OPEN fills and immediate reuse of sale proceeds
are assumptions. Costs are paid once, on entry, by the shared cash-book helper.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
from typing import Literal

from research import intraday_campaign_cash_book as cash


MINUTE = timedelta(minutes=1)
KST = timezone(timedelta(hours=9))
PriceKey = tuple[str, datetime]
MinutePrices = tuple[cash.Exact, cash.Exact, cash.Exact, cash.Exact]
RiskScope = Literal["continuous", "day_local_counterfactual"]


class AccountRefusal(ValueError):
    """Malformed contracts/prices fail; absent evidence freezes the account."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise AccountRefusal(reason)


def _clock(value: object) -> datetime:
    _require(isinstance(value, datetime) and value.utcoffset() is not None,
             "aware_clock_required")
    _require(value.second == value.microsecond == 0, "minute_aligned_clock_required")
    return value.astimezone(KST)


def _price(value: object) -> Fraction:
    _require(not isinstance(value, bool) and isinstance(value, (int, Decimal, Fraction)),
             "exact_price_required")
    _require(not isinstance(value, Decimal) or value.is_finite(), "finite_price_required")
    result = Fraction(value)
    _require(result > 0, "positive_price_required")
    return result


@dataclass(frozen=True)
class CampaignCandidate:
    """Original causal plans: A/B stops, or registered H4/H5 time-only exits."""

    strategy: Literal["A", "B", "H4", "H5"]
    symbol: str
    decision_at: datetime
    entry_at: datetime
    cap_exit_at: datetime
    structural_stop: Fraction | None

    def __post_init__(self) -> None:
        _require(self.strategy in ("A", "B", "H4", "H5"), "unknown_strategy")
        _require(isinstance(self.symbol, str) and bool(self.symbol)
                 and self.symbol == self.symbol.strip(), "invalid_symbol")
        for field in ("decision_at", "entry_at", "cap_exit_at"):
            object.__setattr__(self, field, _clock(getattr(self, field)))
        if self.strategy in ("H4", "H5"):
            _require(self.structural_stop is None, "time_only_stop_required")
        else:
            object.__setattr__(self, "structural_stop", _price(self.structural_stop))
        _require(self.decision_at < self.entry_at < self.cap_exit_at,
                 "candidate_clock_order")
        _require(self.cap_exit_at == self.entry_at + 60 * MINUTE,
                 "sixty_minute_cap_required")


@dataclass(frozen=True)
class HeldCandidate:
    candidate: CampaignCandidate
    exit_at: datetime
    exit_reason: str = "time_cap"


@dataclass(frozen=True)
class PeriodAnchor:
    period: str
    nav: Fraction


@dataclass(frozen=True)
class AccountState:
    """Carry this state across reference days; unknown history is never reset."""

    mode: cash.Mode
    cost_bps: Fraction
    book: cash.CashBook
    risk_scope: RiskScope = "continuous"
    holdings: tuple[HeldCandidate, ...] = ()
    daily_anchor: PeriodAnchor | None = None
    weekly_anchor: PeriodAnchor | None = None
    monthly_anchor: PeriodAnchor | None = None
    peak_nav: Fraction = cash.INITIAL_CAPITAL
    maximum_drawdown: Fraction = Fraction()
    last_at: datetime | None = None
    unknown_at: datetime | None = None


@dataclass(frozen=True)
class AccountEvent:
    at: datetime
    kind: str
    reason: str
    strategy: str | None = None
    symbol: str | None = None
    quantity: int = 0
    cash_delta: Fraction = Fraction()
    assumed_cost: Fraction = Fraction()
    decision_at: datetime | None = None
    scheduled_exit_at: datetime | None = None


@dataclass(frozen=True)
class NAVSnapshot:
    at: datetime
    phase: str
    nav: Fraction
    cash: Fraction
    sleeve_navs: tuple[tuple[str, Fraction | None], ...]
    period_returns: tuple[tuple[str, Fraction], ...]
    entry_gates: tuple[str, ...]
    drawdown: Fraction


@dataclass(frozen=True)
class AccountResult:
    state: AccountState
    events: tuple[AccountEvent, ...]
    snapshots: tuple[NAVSnapshot, ...]
    scenario: str = "observed_candidates_conditional_on_archive_availability"


def new_state(mode: cash.Mode = "A+B", *, cost_bps: cash.Exact = 33,
              risk_scope: RiskScope = "continuous") -> AccountState:
    cost = _price(cost_bps)
    _require(cost in (33, 43), "registered_cost_required")
    _require(risk_scope in ("continuous", "day_local_counterfactual"), "invalid_risk_scope")
    return AccountState(mode, cost, cash.new_book(mode), risk_scope)


def _bucket(minutes: Mapping[PriceKey, MinutePrices | None], key: PriceKey):
    row = minutes.get(key)
    if row is not None:
        _require(type(row) is tuple and len(row) == 4, "invalid_minute_prices")
    return row


def _opening(minutes: Mapping[PriceKey, MinutePrices | None],
             opens: Mapping[PriceKey, cash.Exact | None], key: PriceKey) -> Fraction | None:
    value = opens.get(key)
    if value is None:
        return None
    price = _price(value)
    # At the OPEN, only the row's OPEN may be consulted. Its H/L/C is future.
    row = _bucket(minutes, key)
    if row is not None and price != _price(row[0]):
        return None
    return price


def _closed_close(minutes: Mapping[PriceKey, MinutePrices | None],
                  opens: Mapping[PriceKey, cash.Exact | None], key: PriceKey) -> Fraction | None:
    row = _bucket(minutes, key)
    if row is None:
        return None
    op, high, low, close = map(_price, row)
    _require(low <= min(op, close) <= max(op, close) <= high, "invalid_minute_ohlc")
    supplied_open = opens.get(key)
    if supplied_open is None or _price(supplied_open) != op:
        return None
    return close


def _periods(at: datetime) -> tuple[str, str, str]:
    return _date_periods(at.astimezone(KST).date())


@lru_cache(maxsize=512)
def _date_periods(day: date) -> tuple[str, str, str]:
    iso = day.isocalendar()
    return day.isoformat(), f"{iso.year}-W{iso.week:02d}", day.strftime("%Y-%m")


def _validate_state(state: AccountState) -> None:
    """Reject mismatched resumptions rather than silently lose a held plan."""
    expected = cash.new_book(state.mode)
    _require(state.book.initial_capital == expected.initial_capital
             and tuple((s.strategy, s.initial_allocation) for s in state.book.sleeves)
             == tuple((s.strategy, s.initial_allocation) for s in expected.sleeves),
             "account_allocation_changed")
    plans = {(item.candidate.strategy, item.candidate.symbol) for item in state.holdings}
    _require(len(plans) == len(state.holdings)
             and plans == {(lot.strategy, lot.symbol) for lot in state.book.lots},
             "holding_plan_mismatch")
    for held in state.holdings:
        _require(held.candidate.entry_at < _clock(held.exit_at) <= held.candidate.cap_exit_at,
                 "invalid_scheduled_exit")
    if state.last_at is not None:
        _clock(state.last_at)
    for anchor in (state.daily_anchor, state.weekly_anchor, state.monthly_anchor):
        if anchor is not None:
            _price(anchor.nav)


def _value(state: AccountState, at: datetime, prices: Mapping[str, cash.Exact | None],
           phase: str) -> tuple[AccountState, NAVSnapshot | None, tuple[str, ...]]:
    valuation = cash.mark(state.book, prices)
    if valuation.full_nav is None:
        return replace(state, book=valuation.book, unknown_at=state.unknown_at or at), \
            None, ("account_indeterminate",)
    nav = valuation.full_nav
    keys = _periods(at)
    updates = {}
    anchors = {}
    names = ("daily_anchor", "weekly_anchor", "monthly_anchor") \
        if state.risk_scope == "continuous" else ("daily_anchor",)
    for name, key in zip(names, keys):
        previous = getattr(state, name)
        if previous is None or previous.period != key:
            previous = updates[name] = PeriodAnchor(key, nav)
        anchors[name] = previous
    returns = tuple((name.removesuffix("_anchor"), nav / anchors[name].nav - 1)
                    for name in names)
    current = dict(returns)
    limits = (("daily", Fraction(-1, 200)),)
    if state.risk_scope == "continuous":
        limits += (("weekly", Fraction(-3, 200)), ("monthly", Fraction(-3, 100)),
                   ("hard_monthly", Fraction(-1, 25)))
    gates = tuple(name for name, limit in limits
                  if current["monthly" if name == "hard_monthly" else name] <= limit)
    # Existing Java gates compare CURRENT period return inclusively, without a
    # new sticky kill switch. Only missing account history is sticky here.
    peak = max(state.peak_nav, nav)
    drawdown = 1 - nav / peak
    state = replace(state, book=valuation.book, peak_nav=peak,
                    maximum_drawdown=max(state.maximum_drawdown, drawdown), **updates)
    snapshot = NAVSnapshot(at, phase, nav, state.book.cash, valuation.sleeve_navs,
                           returns, gates, drawdown)
    return state, snapshot, gates


def replay_account(
    candidates: Iterable[CampaignCandidate],
    minute_bars: Mapping[PriceKey, MinutePrices | None],
    open_prices: Mapping[PriceKey, cash.Exact | None],
    session_openings: Mapping[date, datetime],
    session_closes: Mapping[date, datetime],
    *,
    mode: cash.Mode = "A+B",
    cost_bps: cash.Exact = 33,
    state: AccountState | None = None,
    compress_idle_minutes: bool = False,
) -> AccountResult:
    """Replay included sessions in order, including zero-candidate sessions.

    At minute t: scheduled OPEN exits, exact held minute t-3, valuation/gates,
    then entries sorted by original decision, symbol, strategy. Entry OPEN is the
    mark only at e/e+1/e+2; missing expected later closes never forward-fill.
    Known scheduled exits continue after an unknown; missing exits are not
    retried. Output dataclasses retain exact Fractions for caller serialization.
    Optional idle compression omits only flat, event-free minute snapshots:
    openings, closings, entry attempts and every held minute remain evaluated.
    """
    _require(type(compress_idle_minutes) is bool, "boolean_idle_compression_required")
    initial = new_state(mode, cost_bps=cost_bps)
    if state is None:
        state = initial
    _require(isinstance(state, AccountState), "invalid_account_state")
    _require(state.mode == mode and state.cost_bps == initial.cost_bps,
             "account_contract_changed")
    _require(state.risk_scope in ("continuous", "day_local_counterfactual"), "invalid_risk_scope")
    _validate_state(state)
    _require(isinstance(minute_bars, Mapping) and isinstance(open_prices, Mapping),
             "price_maps_required")
    _require(set(session_openings) == set(session_closes), "session_calendar_mismatch")
    sessions = []
    for day in sorted(session_openings):
        _require(type(day) is date, "session_date_required")
        opening, closing = _clock(session_openings[day]), _clock(session_closes[day])
        _require(opening.date() == closing.date() == day and opening < closing,
                 "invalid_session_clocks")
        _require(state.last_at is None or opening > state.last_at, "nonchronological_sessions")
        sessions.append((day, opening, closing))
    plans = tuple(candidates)
    entries: dict[datetime, list[CampaignCandidate]] = {}
    for candidate in plans:
        _require(type(candidate) is CampaignCandidate, "causal_candidate_required")
        day = candidate.entry_at.date()
        _require(day in session_openings, "candidate_outside_calendar")
        opening, closing = _clock(session_openings[day]), _clock(session_closes[day])
        expected = opening + ((candidate.decision_at - opening) // (5 * MINUTE) + 1) * 5 * MINUTE
        _require(opening < candidate.decision_at < candidate.entry_at
                 < candidate.cap_exit_at < closing and candidate.entry_at == expected,
                 "original_candidate_clock_required")
        if candidate.strategy == "H4":
            _require(opening + 92 * MINUTE <= candidate.decision_at <= opening + 240 * MINUTE
                     and (candidate.decision_at - opening - 2 * MINUTE) % (5 * MINUTE)
                     == timedelta(0), "original_h4_clock_required")
        elif candidate.strategy == "H5":
            _require(candidate.decision_at == closing - 78 * MINUTE
                     and candidate.entry_at == closing - 75 * MINUTE
                     and candidate.cap_exit_at == closing - 15 * MINUTE,
                     "original_h5_clock_required")
        _require(candidate.strategy in {s.strategy for s in state.book.sleeves},
                 "candidate_strategy_not_allocated")
        entries.setdefault(candidate.entry_at, []).append(candidate)
    _require(len(set(plans)) == len(plans), "duplicate_candidate_plan")
    for batch in entries.values():
        batch.sort(key=lambda item: (item.decision_at, item.symbol, item.strategy))
    entry_clocks = sorted(entries)
    events: list[AccountEvent] = []
    snapshots: list[NAVSnapshot] = []

    def record_value(at, marks, phase):
        nonlocal state
        old_reasons = state.book.indeterminate_reasons
        state, snapshot, gates = _value(state, at, marks, phase)
        if snapshot is not None:
            snapshots.append(snapshot)
        for reason in state.book.indeterminate_reasons:
            if reason not in old_reasons:
                events.append(AccountEvent(at, "unknown", reason))
        return gates

    for _, opening, closing in sessions:
        at = opening
        while at <= closing:
            # Exits at t do not inspect closes whose availability is t or later.
            for held in tuple(state.holdings):
                if held.exit_at != at:
                    continue
                candidate = held.candidate
                decision = cash.exit(state.book, candidate.strategy, candidate.symbol,
                                     exit_price=_opening(minute_bars, open_prices,
                                                         (candidate.symbol, at)))
                remaining = tuple(item for item in state.holdings if item != held) \
                    if decision.filled else state.holdings
                state = replace(state, book=decision.book, holdings=remaining,
                                unknown_at=(state.unknown_at or at)
                                if decision.book.indeterminate else state.unknown_at)
                events.append(AccountEvent(at, "exit", held.exit_reason if decision.filled
                                           else decision.reason, candidate.strategy,
                                           candidate.symbol, decision.quantity,
                                           decision.cash_delta, decision.assumed_cost,
                                           candidate.decision_at))
            marks: dict[str, Fraction | None] = {}
            for held in tuple(state.holdings):
                candidate = held.candidate
                if held.exit_at <= at:
                    # The scheduled sale was unknown, so neither a surviving
                    # position nor a later recovery quote can repair that path.
                    # Do not extend the registered price window beyond its exit.
                    marks[candidate.symbol] = None
                    continue
                minute = at - 3 * MINUTE
                lot = next(lot for lot in state.book.lots if lot.symbol == candidate.symbol)
                if minute < candidate.entry_at:
                    marks[candidate.symbol] = lot.entry_price
                    continue
                close = _closed_close(minute_bars, open_prices, (candidate.symbol, minute))
                marks[candidate.symbol] = close
                if (candidate.structural_stop is not None and close is not None
                        and close <= candidate.structural_stop and held.exit_at > at):
                    exit_at = min(at + MINUTE, candidate.cap_exit_at)
                    reason = "delayed_structural_stop" if exit_at < candidate.cap_exit_at \
                        else "stop_at_time_cap"
                    changed = replace(held, exit_at=exit_at, exit_reason=reason)
                    state = replace(state, holdings=tuple(changed if item == held else item
                                                         for item in state.holdings))
                    events.append(AccountEvent(at, "stop", reason, candidate.strategy,
                                               candidate.symbol, decision_at=candidate.decision_at,
                                               scheduled_exit_at=exit_at))
            gates = record_value(at, marks, "before_entries")
            for candidate in entries.get(at, ()):
                if gates:
                    events.append(AccountEvent(at, "entry", "entry_blocked:" + ",".join(gates),
                                               candidate.strategy, candidate.symbol,
                                               decision_at=candidate.decision_at))
                    continue
                # Duplicate rejection does not require reading that candidate's
                # entry quote; neither does a period loss gate above.
                duplicate = any(lot.symbol == candidate.symbol for lot in state.book.lots)
                price = None if duplicate else _opening(minute_bars, open_prices,
                                                       (candidate.symbol, at))
                if (candidate.structural_stop is not None and price is not None
                        and price <= candidate.structural_stop):
                    events.append(AccountEvent(at, "entry", "invalidated_entry", candidate.strategy,
                                               candidate.symbol, decision_at=candidate.decision_at))
                    continue
                decision = cash.enter(state.book, candidate.strategy, candidate.symbol,
                                      entry_price=price, observed_prices=marks, cost_bps=state.cost_bps)
                holdings = state.holdings
                if decision.filled:
                    holdings += (HeldCandidate(candidate, candidate.cap_exit_at),)
                    marks[candidate.symbol] = price
                state = replace(state, book=decision.book, holdings=holdings)
                events.append(AccountEvent(at, "entry", decision.reason, candidate.strategy,
                                           candidate.symbol, decision.quantity, decision.cash_delta,
                                           decision.assumed_cost, candidate.decision_at))
                gates = record_value(at, marks, "after_entry")
            state = replace(state, last_at=at)
            if compress_idle_minutes and not state.holdings and not state.book.lots and at < closing:
                # Within this KST session, flat NAV, cash and period anchors
                # cannot change before the next registered entry attempt.
                index = bisect_right(entry_clocks, at)
                next_entry = entry_clocks[index] if index < len(entry_clocks) else closing
                at = min(next_entry, closing)
            else:
                at += MINUTE
    return AccountResult(state, tuple(events), tuple(snapshots))


def replay_daily_counterfactuals(
    candidates: Iterable[CampaignCandidate],
    minute_bars: Mapping[PriceKey, MinutePrices | None],
    open_prices: Mapping[PriceKey, cash.Exact | None],
    session_openings: Mapping[date, datetime],
    session_closes: Mapping[date, datetime],
    *, mode: cash.Mode = "A+B", cost_bps: cash.Exact = 33,
) -> dict[date, AccountResult]:
    """Independent fresh-capital days, with day-local gates only.

    These are conditional diagnostics, never a continuous NAV/weekly/monthly
    account. All supplied reference days, even empty/incomplete days, survive.
    """
    _require(set(session_openings) == set(session_closes), "session_calendar_mismatch")
    plans = tuple(candidates)
    _require(all(type(item) is CampaignCandidate and item.entry_at.date() in session_openings
                 for item in plans), "candidate_outside_calendar")
    return {day: replace(replay_account(
        (item for item in plans if item.entry_at.date() == day), minute_bars, open_prices,
        {day: session_openings[day]}, {day: session_closes[day]}, mode=mode, cost_bps=cost_bps,
        state=new_state(mode, cost_bps=cost_bps, risk_scope="day_local_counterfactual")),
        scenario="fresh_daily_counterfactual_no_continuous_weekly_monthly_claim")
        for day in sorted(session_openings)}
