"""Pure proposed short-swing holding comparisons; no IO, orders or selection.

The caller authenticates the complete trading calendar, source units, original
request and dated rights evidence. Their presence here is not certification.
Receipt-triggered stops use the next regular minute OPEN, never the triggering
CLOSE or an already-passed overnight opening. Cash is nominal event cash only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction

from research import intraday_event_execution as original
from research.intraday_event_scenarios import MinuteBar


POLICIES = ("fixed30", "session_end", "next_session_end", "conditional_next")
THIRD_SESSION_POLICY = "third_session_end"
CLAIM = "proposed nominal event comparison; not concurrent NAV, actual fills or profitability evidence"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _day(value: str) -> None:
    _require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value,
             "Canonical ISO trading date required")


def _calendar(values: Sequence[str]) -> tuple[str, ...]:
    days = tuple(values)
    _require(bool(days) and days == tuple(sorted(set(days))), "Complete ordered trading calendar required")
    for day in days:
        _day(day)
    return days


@dataclass(frozen=True)
class SessionTape:
    session: str
    symbol: str
    session_length: int
    bars: Mapping[int, MinuteBar]
    price_basis: str | None = None

    def __post_init__(self) -> None:
        _day(self.session)
        _require(isinstance(self.symbol, str) and bool(self.symbol), "Symbol required")
        _require(type(self.session_length) is int and 45 < self.session_length <= 600,
                 "Known full regular session length required")
        _require(isinstance(self.bars, Mapping), "Minute mapping required")
        _require(self.price_basis is None or isinstance(self.price_basis, str) and bool(self.price_basis),
                 "Explicit price basis or unknown required")

    @property
    def continuous_end(self) -> int:
        """Closing auction's final ten minutes are excluded from execution."""
        return self.session_length - 10


@dataclass(frozen=True)
class SwingRequest:
    entry_session: str
    symbol: str
    entry: original.EntryRequest
    comparison_level: Fraction | Decimal | int | None
    level_known_offset: int | None
    family: str = "pullback"

    def __post_init__(self) -> None:
        _day(self.entry_session)
        _require(isinstance(self.symbol, str) and bool(self.symbol), "Symbol required")
        _require(isinstance(self.entry, original.EntryRequest), "Original fixed entry request required")
        _require(self.family in ("pullback", "reversal"), "Registered scene family required")
        _require(self.level_known_offset is None or type(self.level_known_offset) is int
                 and 0 <= self.level_known_offset <= self.entry.decision_offset,
                 "Comparison level must be known at original confirmation")
        value = self.comparison_level
        if value is not None:
            _require(not isinstance(value, bool) and isinstance(value, (Fraction, Decimal, int))
                     and (not isinstance(value, Decimal) or value.is_finite()) and value > 0,
                     "Positive exact comparison level or unknown required")
            object.__setattr__(self, "comparison_level", Fraction(value))

    @property
    def level_source(self) -> str:
        return "pre_pullback_peak" if self.family == "pullback" else "second_bearish_high"


@dataclass(frozen=True)
class CarrySupport:
    symbol: str
    from_session: str
    to_session: str
    price_units_verified: bool | None
    rights_unchanged: bool | None
    available_session: str
    available_offset: int

    def __post_init__(self) -> None:
        for day in (self.from_session, self.to_session, self.available_session):
            _day(day)
        _require(isinstance(self.symbol, str) and bool(self.symbol)
                 and self.from_session < self.to_session, "Dated carry identity required")
        _require(all(value is None or type(value) is bool
                     for value in (self.price_units_verified, self.rights_unchanged)), "Explicit carry support state required")
        _require(type(self.available_offset) is int and self.available_offset >= 0, "Carry receipt clock required")


def _tape(sessions: Mapping, day: str, symbol: str) -> SessionTape | None:
    tape = sessions.get((day, symbol))
    _require(tape is None or isinstance(tape, SessionTape) and (tape.session, tape.symbol) == (day, symbol),
             "Session tape identity differs")
    return tape


def _bar(tape: SessionTape, offset: int) -> MinuteBar | None:
    bar = tape.bars.get(offset)
    _require(bar is None or isinstance(bar, MinuteBar) and bar.offset == offset, "Minute identity differs")
    return bar


def _advance_receipts(tape: SessionTape, clock: int, held_start: int, observed: int,
                      last_receipt: int | None, stop: Fraction) -> tuple[int, int | None, str | None, bool]:
    """An absent and an unreceived predecessor expose the same causal prefix."""
    end = tape.continuous_end
    due_offset = clock - 3
    if held_start <= due_offset < end:
        due = _bar(tape, due_offset)
        if due is None or due.available_offset > clock:
            return observed, last_receipt, "held_close_unknown", False
    while observed < min(clock, end):
        quote = _bar(tape, observed)
        if quote is None or quote.available_offset > clock:
            if any((later := _bar(tape, offset)) is not None and later.available_offset <= clock
                   for offset in range(observed + 1, min(clock, end))):
                return observed, last_receipt, "nonchronological_close_availability", False
            if observed + 3 <= clock:
                return observed, last_receipt, "held_close_unknown", False
            break
        if last_receipt is not None and quote.available_offset < last_receipt:
            return observed, last_receipt, "nonchronological_close_availability", False
        last_receipt = quote.available_offset
        observed += 1
        if quote.close <= stop:
            return observed, last_receipt, None, True
    return observed, last_receipt, None, False


def evaluate(request: SwingRequest, sessions: Mapping[tuple[str, str], SessionTape],
             calendar: Sequence[str], *, policy: str,
             carry_support: Sequence[CarrySupport] = ()) -> dict:
    """Replay one fixed request, preserving unknowns at the demanded boundary."""
    _require(isinstance(request, SwingRequest) and isinstance(sessions, Mapping), "Typed request and sessions required")
    _require(policy in (*POLICIES, THIRD_SESSION_POLICY), "Unknown holding policy")
    days = _calendar(calendar)
    _require(request.entry_session in days, "Entry outside registered trading calendar")
    support = {}
    for item in carry_support:
        _require(isinstance(item, CarrySupport), "Typed carry support required")
        key = (item.symbol, item.from_session, item.to_session)
        # Even identical duplicates are ambiguous, but only at their crossing.
        support[key] = item if key not in support else None
    req = request.entry
    entry = req.decision_offset + 1
    cap, stop = req.reference_close + req.scale, req.structure_stop
    quantity = int(min(original.FIRST_RISK // (cap * (1 + original.COST) - stop),
                       min(req.capital / 50, req.capital / (1 + original.COST)) // cap))
    result = {"schema": "short-swing-execution-v1", "claim": CLAIM, "policy": policy,
              "entry_session": request.entry_session, "symbol": request.symbol,
              "level_source": request.level_source,
              "comparison_level": None if request.comparison_level is None else str(request.comparison_level),
              "requested_quantity": quantity, "filled_quantity": 0, "entry_offset": None,
              "entry_price": None, "entry_notional": "0", "entry_cost": "0",
              "status": "no_entry", "reason": None, "sales": [], "carry_decisions": [],
              "gross": "0", "cost": "0", "net": "0", "unknown_session": None,
              "unknown_offset": None, "original_fixed30": None, "pending_exit": None}

    def unknown(reason: str, day: str, clock: int) -> dict:
        result.update(status="unresolved", reason=reason, gross=None, cost=None, net=None,
                      unknown_session=day, unknown_offset=clock)
        return result

    if not quantity:
        result["reason"] = "insufficient_risk_or_cash_for_one_unit"
        return result
    tape = _tape(sessions, request.entry_session, request.symbol)
    if tape is None:
        result.update(filled_quantity=None, entry_notional=None, entry_cost=None)
        return unknown("entry_session_unknown", request.entry_session, entry)
    end = tape.continuous_end
    # Every arm retains the original entry's thirty-minute clock admission.
    if entry + 30 >= end:
        result["reason"] = "expiry_not_inside_session"
        return result
    opening = _bar(tape, entry)
    if opening is None or opening.available_offset >= end:
        result.update(filled_quantity=None, entry_notional=None, entry_cost=None)
        return unknown("entry_open_unknown", tape.session, entry)
    # The registered original entry is a one-shot reference limit, without a low touch fill.
    purchase = opening.open
    if purchase <= stop or purchase > req.reference_close:
        result.update(status="no_fill", reason="entry_open_at_or_below_stop" if purchase <= stop
                      else "entry_open_above_policy_cap")
        return result
    basis, fee = quantity * purchase, quantity * purchase * original.COST
    result.update(status="holding", reason="holding", filled_quantity=quantity, entry_offset=entry,
                  entry_price=str(purchase), entry_notional=str(basis), entry_cost=str(fee),
                  gross=None, cost=None, net=None)

    if policy == "fixed30":
        reads = []

        class Consumed(Mapping):
            def __getitem__(self, key):
                value = tape.bars[key]
                reads.append(value)
                return value

            def __iter__(self):
                return iter(tape.bars)

            def __len__(self):
                return len(tape.bars)

        baseline = original.evaluate(req, Consumed(), end, entry_policy="reference_limit_one_shot")
        result["original_fixed30"] = baseline
        observed, last_receipt = entry, None
        for clock in range(entry + 1, entry + 30):
            observed, last_receipt, issue, stopped = _advance_receipts(
                tape, clock, entry, observed, last_receipt, stop)
            if issue is not None:
                # No sale was attempted before this unknown prefix. Raw historical
                # engine output stays diagnostic, never top-level settled cash.
                return unknown(issue, tape.session, clock)
            if stopped:
                break  # Do not inspect any close after the liquidation decision.
        result["sales"] = [{**sale, "session": tape.session, "decision_session": tape.session}
                           for sale in baseline["sales"]]
        maturity = max([req.decision_offset, *(bar.available_offset for bar in reads)])
        if baseline["unknown_offset"] is not None:
            maturity = max(maturity, baseline["unknown_offset"] +
                           (3 if baseline["reason"] in ("entry_open_unknown", "exit_open_unknown") else 0))
        if maturity >= end:
            for sale in result["sales"]:
                sale.update(price=None, gross=None, allocated_cost=None, net=None)
            return unknown("outcome_not_mature_in_session", tape.session, baseline["unknown_offset"] or end)
        result.update(status=baseline["status"], reason=baseline["reason"], gross=baseline["gross"],
                      cost=baseline["cost"], net=baseline["net"],
                      unknown_session=tape.session if baseline["unknown_offset"] is not None else None,
                      unknown_offset=baseline["unknown_offset"])
        return result

    def sell(day_tape: SessionTape, clock: int, reason: str, decision_day: str, decision: int) -> dict:
        leg = {"session": day_tape.session, "decision_session": decision_day,
               "decision_offset": decision, "execute_offset": clock, "quantity": quantity,
               "reason": reason, "price": None, "gross": None, "allocated_cost": None, "net": None}
        result["sales"].append(leg)
        quote = _bar(day_tape, clock)
        if quote is None:
            return unknown("exit_open_unknown", day_tape.session, clock)
        if quote.available_offset >= day_tape.continuous_end:
            return unknown("outcome_not_mature_in_session", day_tape.session, clock)
        gross = quantity * (quote.open - purchase)
        leg.update(price=str(quote.open), gross=str(gross), allocated_cost=str(fee), net=str(gross - fee))
        result.update(status="closed", reason=reason, gross=str(gross), cost=str(fee), net=str(gross - fee))
        result["pending_exit"] = None
        return result

    def schedule(reason: str, day: str, clock: int) -> tuple[str, str, int]:
        result["pending_exit"] = {"decision_session": day, "decision_offset": clock,
                                  "reason": reason, "quantity": quantity}
        return reason, day, clock

    start_index = days.index(request.entry_session)
    last_index = start_index + (2 if policy == THIRD_SESSION_POLICY else 0 if policy == "session_end" else 1)
    index, observed, pending = start_index, entry, None
    while True:
        end, last_receipt = tape.continuous_end, None
        late_decision = end - 5
        first_clock = entry + 1 if index == start_index else 0
        for clock in range(first_clock, end + 3):
            if pending is not None:
                reason, decision_day, decision = pending
                if clock < end:
                    return sell(tape, clock, reason, decision_day, decision)
                break  # Already ordered, but no regular minute opening remains today.
            held_start = entry if index == start_index else 0
            observed, last_receipt, issue, stopped = _advance_receipts(
                tape, clock, held_start, observed, last_receipt, stop)
            if issue is not None:
                return unknown(issue, tape.session, clock)
            if stopped:
                pending = schedule("structural", tape.session, clock)
            if pending is not None or clock != late_decision:
                continue
            if index == last_index:
                pending = schedule("session_time", tape.session, clock)
            elif policy == "conditional_next" and index == start_index:
                if request.comparison_level is None or request.level_known_offset is None:
                    return unknown("conditional_comparison_level_unknown", tape.session, clock)
                # Last opening-aligned five-minute block whose five rows are fully received.
                block_end = (min(observed, clock) // 5) * 5
                block = [_bar(tape, offset) for offset in range(block_end - 5, block_end)] if block_end >= 5 else []
                if not block or any(bar is None or bar.available_offset > clock for bar in block):
                    return unknown("conditional_five_minute_unknown", tape.session, clock)
                carry = block[-1].close > request.comparison_level
                result["carry_decisions"].append({"session": tape.session, "decision_offset": clock,
                    "block_start": block_end - 5, "block_end": block_end,
                    "close": str(block[-1].close), "level": str(request.comparison_level), "carry": carry})
                if not carry:
                    pending = schedule("conditional_flat", tape.session, clock)
        if index + 1 >= len(days):
            return unknown("next_trading_session_unknown", tape.session, end)
        next_day = days[index + 1]
        crossing = (request.symbol, tape.session, next_day)
        bridge = support.get(crossing)
        # Refuse before even looking up next-day prices. No skipped-date substitution.
        if crossing in support and bridge is None:
            return unknown("carry_support_ambiguous", next_day, 0)
        if (bridge is None or bridge.price_units_verified is not True or bridge.rights_unchanged is not True
                or (bridge.available_session, bridge.available_offset) > (next_day, 0)):
            return unknown("carry_units_or_rights_unknown", next_day, 0)
        following = _tape(sessions, next_day, request.symbol)
        if following is None:
            return unknown("next_session_unknown", next_day, 0)
        if tape.price_basis is None or following.price_basis != tape.price_basis:
            return unknown("carry_price_basis_unknown", next_day, 0)
        tape, index, observed = following, index + 1, 0


def compare(request: SwingRequest, sessions: Mapping[tuple[str, str], SessionTape],
            calendar: Sequence[str], *, carry_support: Sequence[CarrySupport] = (),
            include_third_session: bool = False) -> dict[str, dict]:
    """Identical original request, size, entry and stop across the fixed arms."""
    _require(type(include_third_session) is bool, "Explicit third-session comparison flag required")
    policies = (*POLICIES, THIRD_SESSION_POLICY) if include_third_session else POLICIES
    return {policy: evaluate(request, sessions, calendar, policy=policy, carry_support=carry_support)
            for policy in policies}
