"""Independent raw-timeline and exact-cash EL event reconstruction.

Only immutable input dataclasses are shared with production. No production
quote, sizing, event ordering, cash update or summary function is called.
"""

from collections.abc import Mapping
from fractions import Fraction

from research.intraday_event_execution import EntryRequest, Forecast
from research.intraday_event_scenarios import MinuteBar


def evaluate(request, bars, session_length, *, entry_policy="next_open_capped",
             management="fixed_exit", forecasts=()):
    if not isinstance(request, EntryRequest) or not isinstance(bars, Mapping):
        raise TypeError("Reference input contract differs")
    if type(session_length) is not int or session_length < 1:
        raise ValueError("Reference session length differs")
    if entry_policy not in ("next_open_capped", "reference_limit_one_shot") or management not in (
            "fixed_exit", "forecast_full_exit", "forecast_half_then_exit"):
        raise ValueError("Reference policy differs")
    start = request.decision_offset + 1
    finish = start + request.expiry_minutes
    forecast_map = {}
    for prediction in forecasts:
        if not isinstance(prediction, Forecast) or prediction.decision_offset not in (start + 10, start + 20):
            raise ValueError("Reference forecast schedule differs")
        if prediction.decision_offset in forecast_map:
            raise ValueError("Reference duplicate forecast")
        forecast_map[prediction.decision_offset] = prediction
    ceiling = request.reference_close + request.scale
    charge = Fraction(33, 10000)
    affordable = min(request.capital * Fraction(1, 50), request.capital / (1 + charge))
    by_risk = Fraction(50000) / (ceiling - request.structure_stop + ceiling * charge)
    requested = min(by_risk.numerator // by_risk.denominator,
                    (affordable / ceiling).numerator // (affordable / ceiling).denominator)
    out = {"schema": "EL-event-execution-v1",
        "claim": "nominal event cash only; not concurrent account NAV, actual fills or profitability evidence",
        "entry_policy": entry_policy, "management": management, "status": "no_entry", "reason": None,
        "initial_capital": str(request.capital), "cost_bps": 33, "first_entry_risk_budget": "50000",
        "decision_offset": request.decision_offset, "planned_entry_offset": start, "expiry_offset": finish,
        "reference_close": str(request.reference_close), "guard_cap": str(ceiling), "structure_stop": str(request.structure_stop),
        "requested_quantity": requested, "filled_quantity": 0, "remaining_quantity": 0,
        "entry_offset": None, "entry_price": None, "entry_notional": "0", "entry_cost": "0", "sales": [],
        "cash": str(request.capital), "last_known_cash": str(request.capital), "gross": "0", "cost": "0", "net": "0",
        "gross_return": None, "net_return": None, "realized_gross": "0", "allocated_cost": "0", "realized_net": "0",
        "unknown_offset": None, "counts": {"filled": 0, "no_fill": 0, "no_entry": 0, "unknown": 0, "entry_unknown": 0}}

    def quote(offset):
        found = bars.get(offset)
        if found is not None and (not isinstance(found, MinuteBar) or found.offset != offset):
            raise ValueError("Reference bar identity differs")
        return found

    def fail(reason, offset):
        out["status"], out["reason"], out["unknown_offset"] = "unresolved", reason, offset
        for field in ("remaining_quantity", "cash", "gross", "cost", "net", "gross_return", "net_return"):
            out[field] = None
        out["counts"]["unknown"] = 1
        return out

    if finish >= session_length or requested == 0:
        out["reason"] = "expiry_not_inside_session" if finish >= session_length else "insufficient_risk_or_cash_for_one_unit"
        out["counts"]["no_entry"] = 1
        return out
    first = quote(start)
    if first is None:
        out.update(filled_quantity=None, entry_notional=None, entry_cost=None)
        out["counts"]["entry_unknown"] = 1
        return fail("entry_open_unknown", start)
    purchase = first.open
    upper = ceiling if entry_policy == "next_open_capped" else request.reference_close
    if purchase <= request.structure_stop or purchase > upper:
        out["status"] = "no_fill"
        out["reason"] = "entry_open_at_or_below_stop" if purchase <= request.structure_stop else "entry_open_above_policy_cap"
        out["counts"]["no_fill"] = 1
        return out
    entry_value = requested * purchase
    original_fee = entry_value * charge
    remaining, proceeds, earned, used_fee = requested, Fraction(), Fraction(), Fraction()
    out.update(status="holding", reason="holding", filled_quantity=requested, remaining_quantity=requested,
               entry_offset=start, entry_price=str(purchase), entry_notional=str(entry_value), entry_cost=str(original_fee),
               cash=str(request.capital - entry_value - original_fee), last_known_cash=str(request.capital - entry_value - original_fee))
    out["counts"]["filled"] = 1
    next_close = start
    previous_clock = -1
    orders = {}
    previous_nonpositive = False
    attempted_half = False
    for now in range(start + 1, finish + 1):
        order = orders.pop(now, None)
        if order is None and now == finish:
            order = (remaining, "time", request.decision_offset)
        if order is not None:
            amount, why, ordered = order
            bar = quote(now)
            sale = {"decision_offset": ordered, "execute_offset": now, "quantity": amount, "reason": why,
                    "price": None, "gross": None, "allocated_cost": None, "net": None}
            out["sales"].append(sale)
            if bar is None:
                return fail("exit_open_unknown", now)
            gain = (bar.open - purchase) * amount
            fee_part = entry_value * charge * Fraction(amount, requested)
            sale.update(price=str(bar.open), gross=str(gain), allocated_cost=str(fee_part), net=str(gain - fee_part))
            proceeds += bar.open * amount
            remaining -= amount
            earned += gain
            used_fee += fee_part
            out.update(remaining_quantity=remaining, cash=str(request.capital - entry_value - original_fee + proceeds),
                       last_known_cash=str(request.capital - entry_value - original_fee + proceeds),
                       realized_gross=str(earned), allocated_cost=str(used_fee), realized_net=str(earned - used_fee))
            if remaining == 0:
                out.update(status="closed", reason=why, gross=str(earned), cost=str(original_fee), net=str(earned - original_fee),
                           gross_return=str(earned / entry_value), net_return=str((earned - original_fee) / entry_value))
                return out
        if now == finish:
            raise AssertionError("Registered forecast schedule cannot half-exit at expiry")
        if now >= start + 3:
            scheduled = quote(now - 3)
            if scheduled is None or scheduled.available_offset > now:
                return fail("held_close_unknown", now)
        stopped = False
        while next_close < now:
            minute = quote(next_close)
            if minute is None:
                if next_close + 3 <= now:
                    return fail("held_close_unknown", now)
                break
            if minute.available_offset > now:
                for offset in range(next_close + 1, now):
                    candidate = quote(offset)
                    if candidate is not None and candidate.available_offset <= now:
                        return fail("nonchronological_close_availability", now)
                break
            if minute.available_offset < previous_clock:
                return fail("nonchronological_close_availability", now)
            previous_clock = minute.available_offset
            next_close += 1
            if minute.close <= request.structure_stop:
                orders[now + 1] = (remaining, "structural", now)
                stopped = True
                break
        if stopped or management == "fixed_exit" or now not in (start + 10, start + 20):
            continue
        prediction = forecast_map.get(now)
        negative = (prediction is not None and prediction.available_offset == now
                    and prediction.remaining_gross is not None and prediction.remaining_gross <= 0)
        if negative:
            if management == "forecast_full_exit" or previous_nonpositive:
                orders[now + 1] = (remaining, "forecast_full", now)
            elif not attempted_half:
                attempted_half = True
                if requested // 2:
                    orders[now + 1] = (requested // 2, "forecast_half", now)
        previous_nonpositive = negative
    raise AssertionError("Reference expiry did not close")


def compare_management(request, bars, session_length, *, entry_policy="next_open_capped", forecasts=()):
    forecasts = tuple(forecasts)
    return {mode: evaluate(request, bars, session_length, entry_policy=entry_policy, management=mode, forecasts=forecasts)
            for mode in ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit")}
