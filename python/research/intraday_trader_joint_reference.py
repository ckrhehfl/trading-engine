"""Producer-independent EE event ordering with existing independent NAV/risk.

No producer replay, cash transition, path evaluator or output state supplies an
expectation. Only the registered ORDER_FRACTION constant is shared with the cash
book; existing independent EC _fresh/_valuation own capital and risk math.
"""

from bisect import bisect_right
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

from research import intraday_campaign_independent as old
from research.intraday_campaign_cash_book import ORDER_FRACTION
from research.intraday_ed_inputs import Minute


M = timedelta(minutes=1)
KST = timezone(timedelta(hours=9))
CLAIM = "archived_price_unit_nominal_counterfactual_no_physical_share_KRW_or_fill_certification"


class _ReferenceState(dict):
    """Opaque in-memory continuation returned only by this reconstruction.

    The caller must keep this lineage separate from producer assertions. Plain
    producer projections and producer State objects are deliberately refused.
    No deserialization, persistence or reconstruction from producer output exists.
    """


def _need(ok, reason):
    if not ok:
        raise old.VerificationError(reason)


def _clock(at, *, whole=True):
    _need(type(at) is datetime and at.utcoffset() is not None, "aware reference clock required")
    _need(not whole or at.second == at.microsecond == 0, "whole reference minute required")
    return at.astimezone(KST)


def _price(value):
    _need(type(value) in (int, Decimal, Fraction) and (not isinstance(value, Decimal)
          or value.is_finite()), "exact reference price required")
    result = Fraction(value)
    _need(result > 0, "positive reference price required")
    return result


def _plan(candidate, policies):
    plan = old._plain(candidate)
    _need(isinstance(plan, Mapping) and set(plan) == {
        "slot", "policy_id", "symbol", "signal_at", "available_at", "stop", "target"},
        "causal candidate fields required")
    _need(plan["slot"] in ("A", "B") and plan["policy_id"] == policies[plan["slot"] == "B"],
          "reference frozen policy mismatch")
    _need(type(plan["symbol"]) is str and plan["symbol"]
          and plan["symbol"] == plan["symbol"].strip(), "reference symbol required")
    signal, available = _clock(plan["signal_at"]), _clock(plan["available_at"], whole=False)
    _need(available == signal + 2 * M, "reference signal availability mismatch")
    _need(_price(plan["stop"]) < _price(plan["target"]), "reference frozen level order")
    return dict(plan, signal_at=signal, available_at=available,
                stop=Fraction(plan["stop"]), target=Fraction(plan["target"]))


def _entry(plan):
    return plan["available_at"].replace(second=0, microsecond=0) + M


def _event(at, kind, reason, plan=None, **extra):
    causal = None if plan is None else {
        "strategy": plan["slot"], "symbol": plan["symbol"], "decision_at": plan["available_at"]}
    return old._event(at, kind, reason, causal, **extra)


def _row(minutes, symbol, at):
    row = minutes.get((symbol, at))
    if row is None:
        return None
    _need(type(row) is Minute and row.symbol == symbol and row.start_at == at,
          "reference minute identity changed")
    _need(row.price_state in {"observed", "missing", "no_trade", "halt", "transport_error",
                             "conflict", "unit_unknown"}, "reference price state changed")
    _need((type(row.ohlc) is tuple and len(row.ohlc) == 4) if row.price_state == "observed"
          else row.ohlc is None, "reference price payload changed")
    return row


def _opening(minutes, opens, symbol, at):
    value = opens.get((symbol, at))
    if value is None:
        return None
    price = _price(value)
    row = _row(minutes, symbol, at)
    if row is not None and (row.price_state != "observed" or _price(row.ohlc[0]) != price):
        return None
    return price


def _observed_close(minutes, symbol, at, now):
    row = _row(minutes, symbol, at)
    if row is None:
        return None
    _need(at + M <= _clock(row.available_at, whole=False) <= now,
          "reference close unavailable")
    if row.price_state != "observed":
        return None
    high, low, close = (_price(x) for x in row.ohlc[1:])
    _need(low <= close <= high, "reference HLC invalid")
    return close


def reconstruct(candidates, minutes, open_prices, session_openings, session_closes, *,
                policies, mode="A+B", cost_bps=33, reference_state=None,
                compress_idle_minutes=False):
    _need(type(compress_idle_minutes) is bool, "reference compression boolean required")
    _need(type(policies) is tuple and len(policies) == len(set(policies)) == 2
          and all(type(x) is str and x and x == x.strip() for x in policies),
          "two reference frozen policies required")
    _need(mode in ("A", "B", "A+B") and type(cost_bps) in (int, Decimal, Fraction)
          and Fraction(cost_bps) in (33, 43), "reference registered account required")
    _need(isinstance(minutes, Mapping) and isinstance(open_prices, Mapping)
          and set(session_openings) == set(session_closes), "reference maps/calendar mismatch")
    fresh = old._fresh(mode, Fraction(cost_bps), "continuous")
    fresh["policies"] = policies
    if reference_state is None:
        state = _ReferenceState(fresh)
    else:
        _need(type(reference_state) is _ReferenceState
              and set(reference_state) == set(fresh), "independent reference state required")
        for field in ("mode", "cost_bps", "risk_scope", "policies"):
            old._equal(reference_state[field], fresh[field], "reference_contract." + field)
        old._equal(reference_state["book"]["initial_capital"], fresh["book"]["initial_capital"],
                   "reference_contract.capital")
        old._equal([(s["strategy"], s["initial_allocation"]) for s in reference_state["book"]["sleeves"]],
                   [(s["strategy"], s["initial_allocation"]) for s in fresh["book"]["sleeves"]],
                   "reference_contract.allocations")
        # The reference owns and copies its predecessor; producer state is never
        # an input, and checking output cannot mutate earlier reference history.
        state = deepcopy(reference_state)
    plans = [_plan(x, policies) for x in candidates]
    _need(len({tuple(x.items()) for x in plans}) == len(plans), "reference duplicate plan")
    sessions = [(day, _clock(session_openings[day]), _clock(session_closes[day]))
                for day in sorted(session_openings)]
    for day, opening, closing in sessions:
        _need(opening.date() == closing.date() == day and opening < closing
              and (state["last_at"] is None or opening > _clock(state["last_at"])),
              "reference invalid session")
    entries = {}
    for plan in plans:
        _need(plan["slot"] in {s["strategy"] for s in state["book"]["sleeves"]}, "reference slot unallocated")
        day = plan["signal_at"].date()
        _need(day in session_openings, "reference plan outside calendar")
        opening, closing = _clock(session_openings[day]), _clock(session_closes[day])
        _need(opening + 30 * M <= plan["signal_at"] and plan["available_at"] <= opening + 240 * M
              and (plan["signal_at"] - opening) % (5 * M) == timedelta(0)
              and _entry(plan) + 60 * M <= closing, "reference EE clock mismatch")
        entries.setdefault(_entry(plan), []).append(plan)
    for batch in entries.values():
        batch.sort(key=lambda x: (x["available_at"], x["symbol"], x["slot"]))
    entry_clocks = sorted(entries)
    events, snapshots = [], []

    def value(at, marks, phase):
        snapshot, gates = old._valuation(state, at, marks, phase, events)
        if snapshot is not None:
            snapshots.append(snapshot)
        return gates

    for _, opening, closing in sessions:
        now = opening
        while now <= closing:
            for held in list(state["holdings"]):
                if held["exit_at"] != now:
                    continue
                plan = held["candidate"]
                lot = next(x for x in state["book"]["lots"] if x["symbol"] == plan["symbol"])
                price = _opening(minutes, open_prices, plan["symbol"], now)
                if price is None:
                    old._freeze(state, f"missing_exit_price:{plan['slot']}:{plan['symbol']}", now)
                    events.append(_event(now, "exit", "exit_price_unknown", plan))
                else:
                    proceeds = lot["quantity"] * price
                    sleeve = next(x for x in state["book"]["sleeves"] if x["strategy"] == plan["slot"])
                    sleeve["cash"] += proceeds
                    events.append(_event(now, "exit", held["exit_reason"], plan,
                                         quantity=lot["quantity"], cash_delta=proceeds))
                    state["book"]["lots"].remove(lot)
                    state["holdings"].remove(held)
            marks = {}
            for held in state["holdings"]:
                plan = held["candidate"]
                if held["exit_at"] <= now or held["path_unknown"]:
                    marks[plan["symbol"]] = None
                    continue
                lot = next(x for x in state["book"]["lots"] if x["symbol"] == plan["symbol"])
                if now < _entry(plan) + 3 * M:
                    marks[plan["symbol"]] = lot["entry_price"]
                    continue
                close = _observed_close(minutes, plan["symbol"], now - 3 * M, now)
                marks[plan["symbol"]] = close
                if close is None:
                    held["path_unknown"] = True
                    continue
                held["observed_count"] += 1
                held["maximum_close"] = close if held["maximum_close"] is None else max(held["maximum_close"], close)
                reason = None
                if close <= plan["stop"]:
                    reason = "structural_close_invalidation"
                elif close >= plan["target"]:
                    reason = "known_target_close_reached"
                elif (held["observed_count"] >= 10 and held["maximum_close"] <= lot["entry_price"]
                      and close < lot["entry_price"]):
                    reason = "response_failure"
                cap = _entry(plan) + 60 * M
                if reason and now + M < cap:
                    held["exit_at"], held["exit_reason"] = now + M, reason
                    events.append(_event(now, "trigger", reason, plan, scheduled_exit_at=now + M))
            gates = value(now, marks, "before_entries")
            for plan in entries.get(now, ()):
                if gates:
                    events.append(_event(now, "entry", "entry_blocked:" + ",".join(gates), plan))
                    continue
                if any(lot["symbol"] == plan["symbol"] for lot in state["book"]["lots"]):
                    events.append(_event(now, "entry", "duplicate_position", plan))
                    gates = value(now, marks, "after_entry")
                    continue
                price = _opening(minutes, open_prices, plan["symbol"], now)
                if price is not None and not plan["stop"] < price < plan["target"]:
                    events.append(_event(now, "entry", "entry_outside_frozen_levels", plan))
                    continue
                if price is not None and plan["target"] - price <= price * Fraction(33, 10000):
                    events.append(_event(now, "entry", "entry_cost_space_not_strict", plan))
                    continue
                if price is None:
                    old._freeze(state, f"missing_entry_price:{plan['slot']}:{plan['symbol']}", now)
                    events.append(_event(now, "entry", "entry_price_unknown", plan))
                else:
                    sleeve = next(s for s in state["book"]["sleeves"] if s["strategy"] == plan["slot"])
                    # Revalue directly; no producer sizing or transition helper.
                    full = old._cash(state) + sum(lot["quantity"] * marks[lot["symbol"]]
                                                  for lot in state["book"]["lots"])
                    own = sleeve["cash"] + sum(lot["quantity"] * marks[lot["symbol"]]
                        for lot in state["book"]["lots"] if lot["strategy"] == plan["slot"])
                    rate = state["cost_bps"] / 10000
                    cash_budget = sleeve["cash"] / (1 + rate)
                    budget = min(full * ORDER_FRACTION, own * ORDER_FRACTION, cash_budget)
                    quantity = int(budget // price)
                    if quantity:
                        cost = quantity * price * rate
                        delta = -(quantity * price + cost)
                        sleeve["cash"] += delta
                        state["book"]["lots"].append({"strategy": plan["slot"], "symbol": plan["symbol"],
                            "quantity": quantity, "entry_price": price, "entry_cost": cost})
                        state["holdings"].append({"candidate": plan, "exit_at": _entry(plan) + 60 * M,
                            "exit_reason": "time_cap", "observed_count": 0, "maximum_close": None,
                            "path_unknown": False})
                        marks[plan["symbol"]] = price
                        events.append(_event(now, "entry", "entered", plan, quantity=quantity,
                                             cash_delta=delta, assumed_cost=cost))
                    else:
                        reason = ("insufficient_cash_for_one_share" if cash_budget < price
                                  else "one_share_exceeds_nav_limit")
                        events.append(_event(now, "entry", reason, plan))
                gates = value(now, marks, "after_entry")
            state["last_at"] = now
            if compress_idle_minutes and not state["holdings"] and not state["book"]["lots"] and now < closing:
                index = bisect_right(entry_clocks, now)
                now = min(entry_clocks[index] if index < len(entry_clocks) else closing, closing)
            else:
                now += M
    return {"state": state, "events": events, "snapshots": snapshots, "scenario": CLAIM,
            "idle_minutes_compressed": compress_idle_minutes}


def verify(result, *args, **kwargs):
    expected = reconstruct(*args, **kwargs)
    old._equal(result, expected, "EE_joint_account")
    return {"verified": True, "claim": "invented typed inputs and exact accounting only",
            "reference_state": expected["state"]}
