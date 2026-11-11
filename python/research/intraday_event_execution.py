"""Pure EL nominal event execution; no IO, fitting, orders or account NAV.

All entry policies reserve the same integer quantity from the decision-time
guard cap. A bar's OPEN is only a modeled execution outcome; its later high/low
never implies a limit fill. Held CLOSE observations follow their availability.
Missing demanded observations and reversed availability leave the path unknown.
"""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Mapping, Literal

from research.intraday_event_scenarios import MinuteBar


EntryPolicy = Literal["next_open_capped", "reference_limit_one_shot"]
Management = Literal["fixed_exit", "forecast_full_exit", "forecast_half_then_exit"]
ENTRY_POLICIES = ("next_open_capped", "reference_limit_one_shot")
MANAGEMENT_POLICIES = ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit")
CAPITAL = Fraction(100_000_000)
COST = Fraction(33, 10_000)
FIRST_RISK = Fraction(50_000)
CLAIM = "nominal event cash only; not concurrent account NAV, actual fills or profitability evidence"


def _exact(value):
    if isinstance(value, bool) or not isinstance(value, (Fraction, Decimal, int)):
        raise TypeError("Exact Fraction, Decimal or int required")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("Finite amount required")
    return Fraction(value)


@dataclass(frozen=True)
class EntryRequest:
    decision_offset: int
    reference_close: Fraction | Decimal | int
    scale: Fraction | Decimal | int
    structure_stop: Fraction | Decimal | int
    expiry_minutes: int = 30
    capital: Fraction | Decimal | int = CAPITAL

    def __post_init__(self):
        if type(self.decision_offset) is not int or self.decision_offset < 0:
            raise ValueError("Nonnegative integer decision offset required")
        if type(self.expiry_minutes) is not int or self.expiry_minutes != 30:
            raise ValueError("This registration fixes thirty-minute expiry")
        for field in ("reference_close", "scale", "structure_stop", "capital"):
            object.__setattr__(self, field, _exact(getattr(self, field)))
        if self.capital != CAPITAL:
            raise ValueError("Fictional capital is frozen at 100000000")
        if not (self.reference_close > 0 and self.scale > 0
                and 0 < self.structure_stop < self.reference_close + self.scale):
            raise ValueError("Positive scale and stop below guard cap required")


@dataclass(frozen=True)
class Forecast:
    decision_offset: int
    available_offset: int
    remaining_gross: Fraction | Decimal | int | None

    def __post_init__(self):
        if any(type(value) is not int or value < 0 for value in (self.decision_offset, self.available_offset)):
            raise ValueError("Nonnegative integer forecast clocks required")
        if self.remaining_gross is not None:
            object.__setattr__(self, "remaining_gross", _exact(self.remaining_gross))


def _bar(bars, at):
    value = bars.get(at)
    if value is not None and (not isinstance(value, MinuteBar) or value.offset != at):
        raise ValueError("Minute bar identity differs")
    return value


def _forecasts(values, entry):
    result = {}
    for value in values:
        if (not isinstance(value, Forecast) or value.decision_offset not in (entry + 10, entry + 20)
                or value.decision_offset in result):
            raise ValueError("Unique entry+10/entry+20 forecasts required")
        result[value.decision_offset] = value
    return result


def evaluate(request: EntryRequest, bars: Mapping[int, MinuteBar], session_length: int, *,
             entry_policy: EntryPolicy = "next_open_capped", management: Management = "fixed_exit",
             forecasts: tuple[Forecast, ...] = ()) -> dict:
    if not isinstance(request, EntryRequest) or not isinstance(bars, Mapping):
        raise TypeError("EntryRequest and minute mapping required")
    if type(session_length) is not int or session_length <= 0:
        raise ValueError("Positive integer session length required")
    if entry_policy not in ENTRY_POLICIES or management not in MANAGEMENT_POLICIES:
        raise ValueError("Unknown EL execution policy")
    entry, expiry = request.decision_offset + 1, request.decision_offset + 1 + request.expiry_minutes
    predictions = _forecasts(forecasts, entry)
    cap, stop = request.reference_close + request.scale, request.structure_stop
    quantity = int(min(FIRST_RISK // (cap * (1 + COST) - stop),
                       min(request.capital / 50, request.capital / (1 + COST)) // cap))
    result = {"schema": "EL-event-execution-v1", "claim": CLAIM, "entry_policy": entry_policy,
        "management": management, "status": "no_entry", "reason": None,
        "initial_capital": str(request.capital), "cost_bps": 33, "first_entry_risk_budget": "50000",
        "decision_offset": request.decision_offset, "planned_entry_offset": entry, "expiry_offset": expiry,
        "reference_close": str(request.reference_close), "guard_cap": str(cap), "structure_stop": str(stop),
        "requested_quantity": quantity, "filled_quantity": 0, "remaining_quantity": 0,
        "entry_offset": None, "entry_price": None, "entry_notional": "0", "entry_cost": "0",
        "sales": [], "cash": str(request.capital), "last_known_cash": str(request.capital),
        "gross": "0", "cost": "0", "net": "0", "gross_return": None, "net_return": None,
        "realized_gross": "0", "allocated_cost": "0", "realized_net": "0", "unknown_offset": None,
        "counts": {"filled": 0, "no_fill": 0, "no_entry": 0, "unknown": 0, "entry_unknown": 0}}

    def unknown(reason, at):
        result.update(status="unresolved", reason=reason, unknown_offset=at,
                      remaining_quantity=None, cash=None, gross=None, cost=None, net=None,
                      gross_return=None, net_return=None)
        result["counts"]["unknown"] = 1
        return result

    if expiry >= session_length:
        result["reason"] = "expiry_not_inside_session"
        result["counts"]["no_entry"] = 1
        return result
    if not quantity:
        result["reason"] = "insufficient_risk_or_cash_for_one_unit"
        result["counts"]["no_entry"] = 1
        return result
    opening = _bar(bars, entry)
    if opening is None:
        result.update(filled_quantity=None, entry_notional=None, entry_cost=None)
        result["counts"]["entry_unknown"] = 1
        return unknown("entry_open_unknown", entry)
    purchase = opening.open
    threshold = cap if entry_policy == "next_open_capped" else request.reference_close
    if purchase <= stop or purchase > threshold:
        result.update(status="no_fill", reason="entry_open_at_or_below_stop" if purchase <= stop
                      else "entry_open_above_policy_cap")
        result["counts"]["no_fill"] = 1
        return result
    basis, paid = quantity * purchase, quantity * purchase * COST
    balance = request.capital - basis - paid
    remaining, realized_gross, allocated_cost = quantity, Fraction(), Fraction()
    result.update(status="holding", reason="holding", filled_quantity=quantity, remaining_quantity=quantity,
                  entry_offset=entry, entry_price=str(purchase), entry_notional=str(basis), entry_cost=str(paid),
                  cash=str(balance), last_known_cash=str(balance))
    result["counts"]["filled"] = 1

    def sell(at, amount, reason, decision):
        nonlocal remaining, balance, realized_gross, allocated_cost
        quote = _bar(bars, at)
        leg = {"decision_offset": decision, "execute_offset": at, "quantity": amount,
               "reason": reason, "price": None, "gross": None, "allocated_cost": None, "net": None}
        result["sales"].append(leg)
        if quote is None:
            unknown("exit_open_unknown", at)
            return False
        gross, cost = amount * (quote.open - purchase), paid * amount / quantity
        leg.update(price=str(quote.open), gross=str(gross), allocated_cost=str(cost), net=str(gross - cost))
        balance += amount * quote.open
        remaining -= amount
        realized_gross += gross
        allocated_cost += cost
        result.update(remaining_quantity=remaining, cash=str(balance), last_known_cash=str(balance),
                      realized_gross=str(realized_gross), allocated_cost=str(allocated_cost),
                      realized_net=str(realized_gross - allocated_cost))
        if not remaining:
            result.update(status="closed", reason=reason, gross=str(realized_gross), cost=str(paid),
                          net=str(realized_gross - paid), gross_return=str(realized_gross / basis),
                          net_return=str((realized_gross - paid) / basis))
        return True

    observed, last_availability = entry, None
    pending = None
    negative_streak, reduced_once = 0, False
    for clock in range(entry + 1, expiry + 1):
        if pending is not None:
            due, amount, reason, decision = pending
            if due == clock:
                if not sell(clock, amount, reason, decision) or not remaining:
                    return result
                pending = None
        if clock == expiry:
            sell(clock, remaining, "time", request.decision_offset)
            return result
        due_missing = clock - 3
        if due_missing >= entry:
            due = _bar(bars, due_missing)
            if due is None or due.available_offset > clock:
                return unknown("held_close_unknown", clock)
        while observed < clock:
            quote = _bar(bars, observed)
            if quote is None:
                if observed + 3 <= clock:
                    return unknown("held_close_unknown", clock)
                break
            if quote.available_offset > clock:
                if any((later := _bar(bars, offset)) is not None and later.available_offset <= clock
                       for offset in range(observed + 1, clock)):
                    return unknown("nonchronological_close_availability", clock)
                break
            if last_availability is not None and quote.available_offset < last_availability:
                return unknown("nonchronological_close_availability", clock)
            last_availability = quote.available_offset
            observed += 1
            if quote.close <= stop:
                pending = (clock + 1, remaining, "structural", clock)
                break
        if pending is not None or management == "fixed_exit" or clock not in (entry + 10, entry + 20):
            continue
        forecast = predictions.get(clock)
        value = None if forecast is None or forecast.available_offset != clock else forecast.remaining_gross
        negative_streak = negative_streak + 1 if value is not None and value <= 0 else 0
        if not negative_streak:
            continue
        if management == "forecast_full_exit" or negative_streak >= 2:
            pending = (clock + 1, remaining, "forecast_full", clock)
        elif not reduced_once:
            reduced_once = True
            half = quantity // 2
            if half:
                pending = (clock + 1, half, "forecast_half", clock)
    raise AssertionError("Expiry must terminate every admitted event")


def compare_management(request, bars, session_length, *, entry_policy="next_open_capped", forecasts=()):
    """All arms use the identical fixed request and reserve/fill calculation."""
    forecasts = tuple(forecasts)
    return {mode: evaluate(request, bars, session_length, entry_policy=entry_policy,
                           management=mode, forecasts=forecasts) for mode in MANAGEMENT_POLICIES}
