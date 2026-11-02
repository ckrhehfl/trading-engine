"""Pure EE delayed-OPEN price labels over caller-supplied invented/typed rows.

There is no reader, account, order, model or eligibility certification. The
caller freezes the signal, levels and admission before providing its outcome
path. Decimal prices become exact Fractions before any arithmetic. net33 and
net43 subtract fixed assumed round-trip costs from a price return; neither is
account PnL or evidence that an OPEN was liquid or executable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from research.intraday_ed_inputs import Minute, STATES


MINUTE = timedelta(minutes=1)
PRIMARY_COST = Fraction(33, 10000)
SENSITIVITY_COST = Fraction(43, 10000)
ASSUMPTIONS = (
    "OPEN prices are modeled prices, not certified executable fills or liquidity",
    "caller supplies common physical price units and price basis; neither is certified here",
    "completed minute decisions assume end+2 minutes; source availability must not be later",
    "net33/net43 subtract fixed assumed round-trip 33/43 bp from price labels, not account PnL",
    "no cash, flatness, risk, source entitlement or trade eligibility certification",
)


class PathRefusal(ValueError):
    """Malformed identity, prices or clocks are not ordinary missingness."""


@dataclass(frozen=True)
class PathPlan:
    family: str
    symbol: str
    decision_at: datetime
    stop: Decimal
    target: Decimal
    opening: datetime
    closing: datetime
    admission: Literal["pass", "decline", "unknown"]


@dataclass(frozen=True)
class PathOutcome:
    plan: PathPlan
    status: Literal["traded", "declined", "unknown"]
    reason: str
    entry_at: datetime | None = None
    entry_open: Decimal | None = None
    exit_at: datetime | None = None
    exit_open: Decimal | None = None
    trigger_minute: datetime | None = None
    trigger_at: datetime | None = None
    gross: Fraction | None = None
    net33: Fraction | None = None
    net43: Fraction | None = None
    simultaneous_touch_minutes: tuple[datetime, ...] = ()
    assumptions: tuple[str, ...] = ASSUMPTIONS


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PathRefusal(reason)


def _clock(value: datetime) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None,
             "aware_clock_required")


def _price(value: Decimal) -> Fraction:
    _require(isinstance(value, Decimal) and value.is_finite() and value > 0,
             "positive_finite_decimal_price_required")
    return Fraction(value)


def _next_open(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0) + MINUTE


def _validate_plan(plan: PathPlan) -> None:
    _require(isinstance(plan, PathPlan), "typed_path_plan_required")
    _require(type(plan.family) is str and bool(plan.family.strip())
             and type(plan.symbol) is str and bool(plan.symbol.strip()), "invalid_plan_identity")
    for value in (plan.opening, plan.closing, plan.decision_at):
        _clock(value)
    _require(plan.opening.second == plan.opening.microsecond == 0
             and plan.closing.second == plan.closing.microsecond == 0,
             "whole_minute_session_required")
    _require(plan.opening.utcoffset() == plan.closing.utcoffset() == plan.decision_at.utcoffset()
             and plan.opening.date() == plan.closing.date() == plan.decision_at.date()
             and plan.opening <= plan.decision_at < plan.closing,
             "invalid_same_day_session")
    _require(type(plan.admission) is str and plan.admission in {"pass", "decline", "unknown"},
             "invalid_admission")
    _price(plan.stop)
    _price(plan.target)


def _row(minutes: Mapping[datetime, Minute], plan: PathPlan, at: datetime) -> Minute | None:
    item = minutes.get(at)
    if item is None:
        return None
    _require(isinstance(item, Minute), "typed_minute_required")
    _clock(item.start_at)
    _require(item.symbol == plan.symbol and item.start_at == at, "minute_identity_changed")
    _require(type(item.price_state) is str and item.price_state in STATES,
             "invalid_price_state")
    if item.price_state != "observed":
        _require(item.ohlc is None, "unknown_price_has_payload")
    else:
        _require(type(item.ohlc) is tuple and len(item.ohlc) == 4, "observed_price_requires_ohlc")
    return item


def _open(minutes: Mapping[datetime, Minute], plan: PathPlan,
          at: datetime) -> tuple[Decimal | None, str | None]:
    item = _row(minutes, plan, at)
    if item is None:
        return None, "missing_open_minute"
    if item.price_state != "observed":
        return None, f"open_price_{item.price_state}"
    # Whole-row available_at describes the archived completed minute, not the
    # modeled OPEN. Its later H/L/C and availability do not gate this OPEN.
    value = item.ohlc[0]
    _price(value)
    return value, None


def evaluate_path(plan: PathPlan, minutes: Mapping[datetime, Minute]) -> PathOutcome:
    """Label one frozen plan, consuming only the causal required price prefix.

    A held minute starting q matures at q+3. Its close may schedule the first
    whole-minute OPEN strictly after that time, capped by entry+60. A scheduled
    exit executes before any further held-minute H/L/C is consumed. Even the
    earliest possible exit from q>=cap-4 cannot precede the cap, so those close
    rows are irrelevant and are never requested. Simultaneous H/L touches are
    unknown intraminute order diagnostics only, never stop/target-level fills.
    """
    _validate_plan(plan)
    if plan.admission != "pass":
        return PathOutcome(plan, "declined" if plan.admission == "decline" else "unknown",
                           f"admission_{plan.admission}")
    entry_at = _next_open(plan.decision_at)
    cap = entry_at + 60 * MINUTE
    if cap > plan.closing:
        return PathOutcome(plan, "declined", "same_day_hold_cap_does_not_fit")
    _require(isinstance(minutes, Mapping), "minute_mapping_required")
    entry_open, issue = _open(minutes, plan, entry_at)
    if issue:
        return PathOutcome(plan, "unknown", issue, entry_at=entry_at)
    entry = _price(entry_open)
    stop, target = _price(plan.stop), _price(plan.target)
    entry_fields = {"entry_at": entry_at, "entry_open": entry_open}
    if not stop < entry < target:
        return PathOutcome(plan, "declined", "entry_outside_frozen_levels", **entry_fields)
    if target - entry <= entry * PRIMARY_COST:
        return PathOutcome(plan, "declined", "entry_cost_space_not_strict", **entry_fields)

    touches: list[datetime] = []

    def finish(exit_at: datetime, reason: str, *, trigger_minute: datetime | None = None,
               trigger_at: datetime | None = None) -> PathOutcome:
        exit_open, issue = _open(minutes, plan, exit_at)
        fields = {**entry_fields, "exit_at": exit_at, "trigger_minute": trigger_minute,
                  "trigger_at": trigger_at, "simultaneous_touch_minutes": tuple(touches)}
        if issue:
            return PathOutcome(plan, "unknown", issue, **fields)
        gross = _price(exit_open) / entry - 1
        return PathOutcome(plan, "traded", reason, exit_open=exit_open, gross=gross,
                           net33=gross - PRIMARY_COST, net43=gross - SENSITIVITY_COST, **fields)

    at = entry_at
    maximum_close: Fraction | None = None
    count = 0
    while at + 4 * MINUTE < cap:
        decision = at + 3 * MINUTE
        item = _row(minutes, plan, at)
        unknown_fields = {**entry_fields, "simultaneous_touch_minutes": tuple(touches)}
        if item is None:
            return PathOutcome(plan, "unknown", "missing_held_minute", **unknown_fields)
        _clock(item.available_at)
        _require(at + MINUTE <= item.available_at <= decision,
                 "held_minute_unavailable_at_decision")
        if item.price_state != "observed":
            return PathOutcome(plan, "unknown", f"held_price_{item.price_state}", **unknown_fields)
        # The held OPEN is unnecessary. Only matured H/L/C are consumed here.
        high, low, close = (_price(value) for value in item.ohlc[1:])
        _require(low <= close <= high, "invalid_held_hlc_order")
        if low <= stop and high >= target:
            touches.append(at)
        count += 1
        maximum_close = close if maximum_close is None else max(maximum_close, close)
        reason = ("structural_close_invalidation" if close <= stop else
                  "known_target_close_reached" if close >= target else
                  "response_failure" if count >= 10 and maximum_close <= entry and close < entry else
                  None)
        if reason is not None:
            return finish(min(_next_open(decision), cap), reason,
                          trigger_minute=at, trigger_at=decision)
        at += MINUTE
    return finish(cap, "time_cap")
