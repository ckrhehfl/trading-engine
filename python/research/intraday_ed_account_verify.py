"""Independent ED integer-account oracle with its own carried history.

No producer replay, cash, valuation, risk or state constructor is imported.
The caller supplies independently verified snapshot prices and separately
verified policy actions. Producer results are assertions, never starting state.
This verifies accounting, not source authentication or policy/model selection.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

from research.intraday_campaign_independent import VerificationError, _equal, _plain


CAPITAL = Fraction(100_000_000)
MINUTE = timedelta(minutes=1)
KST = timezone(timedelta(hours=9))
ARMS = frozenset({"ED-H1-RULE", "ED-H1-WITHOUT-FILING", "ED-H1-LGBM", "ED-H1-LGBM-WITHOUT-FILING"})
SCOPES = frozenset({"continuous", "day_local_counterfactual"})
ASSUMPTIONS = (
    "scheduled_OPEN_proxy_not_certified_available_or_executable",
    "same_time_sale_proceeds_reusable",
    "legacy_cost_scenario_not_current_fee_tax_certification",
    "liquidity_partial_fill_gap_and_physical_price_basis_uncertified",
    "adverse_LOW_diagnostic_covers_only_causally_observed_close_prefix",
)
STATES = frozenset({"observed", "missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"})


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise VerificationError("ED account: " + reason)


def _clock(value: object) -> datetime:
    _need(type(value) is datetime and value.utcoffset() is not None and not value.second and not value.microsecond,
          "aware whole-minute clock required")
    return value


def _exact(value: object, *, positive: bool = True) -> Fraction:
    _need(type(value) in (int, Decimal, Fraction) and not isinstance(value, bool), "exact amount required")
    if isinstance(value, Decimal):
        _need(value.is_finite(), "finite exact amount required")
    amount = Fraction(value)
    _need(amount > 0 if positive else amount >= 0, "positive/nonnegative amount required")
    return amount


def _fresh(arm: str, cost: Fraction, scope: str) -> dict:
    return {"book": {"arm": arm, "cash": CAPITAL, "lots": [], "indeterminate_reasons": []},
            "cost_bps": cost, "risk_scope": scope, "daily_anchor": None, "weekly_anchor": None,
            "monthly_anchor": None, "peak_nav": CAPITAL, "maximum_drawdown": Fraction(),
            "last_at": None, "first_unknown_at": None, "total_entry_cost": Fraction(),
            "realized_gross_pnl": Fraction()}


def _event(at: datetime, kind: str, reason: str, plan: dict | None = None, **extra) -> dict:
    value = {"at": at, "kind": kind, "reason": reason,
             "parent_id": plan["parent_id"] if plan else None,
             "symbol": plan["symbol"] if plan else None, "quantity": 0,
             "cash_delta": Fraction(), "assumed_cost": Fraction(), "scheduled_exit_at": None}
    value.update(extra)
    return value


def _freeze(state: dict, events: list, at: datetime, reason: str, *, plan: dict | None = None,
            parent_id: str | None = None, symbol: str | None = None) -> None:
    reasons = state["book"]["indeterminate_reasons"]
    if reason not in reasons:
        reasons.append(reason)
        event = _event(at, "unknown", reason, plan)
        if plan is None:
            event.update(parent_id=parent_id, symbol=symbol)
        events.append(event)
    if state["first_unknown_at"] is None:
        state["first_unknown_at"] = at


def _minute(minutes: Mapping, symbol: str, at: datetime) -> dict | None:
    raw = minutes.get((symbol, at))
    if raw is None:
        return None
    row = _plain(raw)
    _need(type(row) is dict and row.get("symbol") == symbol and row.get("start_at") == at,
          "minute identity differs")
    _clock(row["start_at"])
    available = _clock(row["available_at"])
    _need(available >= at + MINUTE and row.get("price_state") in STATES, "minute availability/state differs")
    if row["price_state"] != "observed":
        _need(row.get("ohlc") is None, "unknown price has payload")
    else:
        _need(isinstance(row.get("ohlc"), (tuple, list)) and len(row["ohlc"]) == 4, "OHLC required")
    return row


def _open(minutes: Mapping, symbol: str, at: datetime) -> Fraction | None:
    row = _minute(minutes, symbol, at)
    if row is None or row["price_state"] != "observed":
        return None
    # Only the scheduled OPEN proxy is consumed here; its later H/L/C cannot
    # select whether to enter or sell on this clock.
    return _exact(row["ohlc"][0])


def _close(minutes: Mapping, symbol: str, at: datetime) -> tuple[Fraction | None, str | None]:
    start = at - 3 * MINUTE
    row = _minute(minutes, symbol, start)
    if row is None:
        return None, "missing_minute"
    if row["price_state"] != "observed":
        return None, "price_" + row["price_state"]
    if row["available_at"] > at:
        return None, "close_not_available"
    op, high, low, close = (_exact(value) for value in row["ohlc"])
    _need(low <= min(op, close) <= max(op, close) <= high, "OHLC ordering differs")
    return close, None


def _candidate(plan: dict, session: dict) -> None:
    keys = {"parent_id", "symbol", "opening", "closing", "decision_at", "entry_at", "cap_exit_at", "structural_stop"}
    _need(type(plan) is dict and set(plan) == keys and type(plan["parent_id"]) is str and bool(plan["parent_id"])
          and type(plan["symbol"]) is str and bool(plan["symbol"]), "candidate schema differs")
    for field in ("opening", "closing", "decision_at", "entry_at", "cap_exit_at"):
        _clock(plan[field])
    opening, close, decision, entry, cap = (plan[field] for field in (
        "opening", "closing", "decision_at", "entry_at", "cap_exit_at"))
    end = decision - 2 * MINUTE
    _need((opening, close) == (session["opening"], session["closing"])
          and end >= opening + 30 * MINUTE and (end - opening) % (5 * MINUTE) == timedelta()
          and entry == end + 5 * MINUTE and entry > decision + MINUTE
          and cap == entry + 60 * MINUTE and cap <= close - 15 * MINUTE,
          "ED E+2/E+5/sixty-minute clock differs")
    _need(type(plan["structural_stop"]) is Fraction and plan["structural_stop"] > 0,
          "exact positive structural stop required")


def _nav(state: dict, marks: Mapping[str, Fraction | None]) -> Fraction | None:
    value = state["book"]["cash"]
    for lot in state["book"]["lots"]:
        price = marks.get(lot["candidate"]["symbol"])
        if price is None:
            return None
        value += lot["quantity"] * price
    return value


def _value(state: dict, events: list, snapshots: list, at: datetime, marks: Mapping, phase: str) -> list[str]:
    for lot in state["book"]["lots"]:
        symbol = lot["candidate"]["symbol"]
        if marks.get(symbol) is None:
            _freeze(state, events, at, "missing_held_price:" + symbol)
    nav = _nav(state, marks)
    snapshot = {"at": at, "phase": phase, "full_nav": None, "observed_nav": nav,
                "cash": state["book"]["cash"], "period_returns": [], "entry_gates": [],
                "drawdown": None, "total_entry_cost": state["total_entry_cost"],
                "same_quantity_gross_nav": None}
    if state["book"]["indeterminate_reasons"]:
        snapshot["entry_gates"] = ["account_indeterminate"]
        snapshots.append(snapshot)
        return snapshot["entry_gates"]
    _need(nav is not None and nav >= 0, "nonnegative identified NAV required")
    local_day = at.astimezone(KST).date()
    iso = local_day.isocalendar()
    periods = [("daily", local_day.isoformat()), ("weekly", f"{iso.year}-W{iso.week:02d}"),
               ("monthly", local_day.strftime("%Y-%m"))]
    if state["risk_scope"] == "day_local_counterfactual":
        periods = periods[:1]
    returns = []
    for name, period in periods:
        key = name + "_anchor"
        if state[key] is None or state[key]["period"] != period:
            state[key] = {"period": period, "nav": nav}
        _need(state[key]["nav"] > 0, "positive period anchor required")
        returns.append((name, nav / state[key]["nav"] - 1))
    limits = {"daily": Fraction(-5, 1000), "weekly": Fraction(-15, 1000), "monthly": Fraction(-3, 100)}
    gates = [name for name, value in returns if value <= limits[name]]
    if dict(returns).get("monthly", Fraction()) <= Fraction(-4, 100):
        gates.append("hard_monthly")
    state["peak_nav"] = max(state["peak_nav"], nav)
    drawdown = 1 - nav / state["peak_nav"]
    state["maximum_drawdown"] = max(state["maximum_drawdown"], drawdown)
    snapshot.update(full_nav=nav, period_returns=returns, entry_gates=gates, drawdown=drawdown,
                    same_quantity_gross_nav=nav + state["total_entry_cost"])
    snapshots.append(snapshot)
    return gates


def _check_cash_identity(state: dict) -> None:
    left = state["book"]["cash"] + sum((lot["quantity"] * lot["entry_price"]
                                       for lot in state["book"]["lots"]), Fraction())
    _need(left == CAPITAL + state["realized_gross_pnl"] - state["total_entry_cost"], "cash-flow identity differs")
    _need(state["book"]["cash"] >= 0 and bool(state["book"]["indeterminate_reasons"])
          == (state["first_unknown_at"] is not None), "cash/unknown history differs")


def _reconstruct(state: dict, actions: tuple, minutes: Mapping, session: dict, *,
                 compress_idle_minutes: bool = False) -> dict:
    decisions, entries = {}, {}
    parent_ids, parent_coordinates = set(), set()
    for raw in actions:
        action = _plain(raw)
        _need(type(action) is dict and set(action) == {"at", "status", "reason", "candidate", "symbol", "parent_id"},
              "action schema differs")
        at = _clock(action["at"])
        _need(session["opening"] <= at <= session["closing"] and action["status"] in ("accept", "decline", "unknown")
              and type(action["reason"]) is str and bool(action["reason"]), "action clock/status differs")
        if action["parent_id"] is not None:
            _need(action["parent_id"] not in parent_ids, "duplicate parent action")
            parent_ids.add(action["parent_id"])
        plan = action["candidate"]
        if plan is not None:
            _candidate(plan, session)
            _need(at == plan["decision_at"] and action["symbol"] == plan["symbol"]
                  and action["parent_id"] == plan["parent_id"], "action candidate identity differs")
            coordinate = plan["symbol"], plan["opening"]
            _need(coordinate not in parent_coordinates, "duplicate stock/session parent")
            parent_coordinates.add(coordinate)
            if action["status"] == "accept":
                entries.setdefault(plan["entry_at"], []).append(plan)
        else:
            _need(action["status"] != "accept", "accepted action lacks candidate")
        decisions.setdefault(at, []).append(action)
    for plans in entries.values():
        plans.sort(key=lambda p: (p["decision_at"], p["symbol"], p["parent_id"]))
    flat_clocks = sorted({session["opening"], session["closing"], *decisions, *entries})
    events, snapshots = [], []
    at = session["opening"]
    while at <= session["closing"]:
        for lot in list(state["book"]["lots"]):
            if lot["exit_at"] != at:
                continue
            plan = lot["candidate"]
            price = _open(minutes, plan["symbol"], at)
            if price is None:
                _freeze(state, events, at, "missing_exit_price:" + plan["symbol"], plan=plan)
                events.append(_event(at, "exit", "exit_price_unknown", plan))
            else:
                proceeds = lot["quantity"] * price
                state["book"]["cash"] += proceeds
                state["book"]["lots"].remove(lot)
                state["realized_gross_pnl"] += lot["quantity"] * (price - lot["entry_price"])
                events.append(_event(at, "exit", lot["exit_reason"], plan, quantity=lot["quantity"], cash_delta=proceeds))
        if at == session["opening"] and session["policy_state"] == "unavailable":
            _freeze(state, events, at, "policy_unavailable:" + session["reason"])
        for action in sorted(decisions.get(at, ()), key=lambda a: (a["symbol"] or "", a["parent_id"] or "", a["status"])):
            events.append(_event(at, "action", action["reason"], parent_id=action["parent_id"], symbol=action["symbol"]))
            if action["status"] == "unknown":
                _freeze(state, events, at, "unknown_action:" + action["reason"],
                        parent_id=action["parent_id"], symbol=action["symbol"])
        marks = {}
        for lot in state["book"]["lots"]:
            plan, symbol = lot["candidate"], lot["candidate"]["symbol"]
            if lot["exit_at"] <= at:
                marks[symbol] = None
            elif at < plan["entry_at"] + 3 * MINUTE:
                marks[symbol] = lot["entry_price"]
            else:
                close, issue = _close(minutes, symbol, at)
                marks[symbol] = close
                if issue is not None:
                    reason = f"holding_path_unknown:{symbol}:{(at - 3 * MINUTE).isoformat()}:{issue}"
                    _freeze(state, events, at, reason, plan=plan)
                elif close <= plan["structural_stop"]:
                    lot["exit_at"] = min(at + MINUTE, plan["cap_exit_at"])
                    lot["exit_reason"] = "stop_at_time_cap" if lot["exit_at"] == plan["cap_exit_at"] else "delayed_structural_stop"
                    events.append(_event(at, "stop", lot["exit_reason"], plan, scheduled_exit_at=lot["exit_at"]))
        gates = _value(state, events, snapshots, at, marks, "before_entries")
        for plan in entries.get(at, ()):
            if gates:
                events.append(_event(at, "entry", "entry_blocked:" + ",".join(gates), plan))
                continue
            if any(lot["candidate"]["symbol"] == plan["symbol"] for lot in state["book"]["lots"]):
                events.append(_event(at, "entry", "duplicate_position", plan))
                continue
            price = _open(minutes, plan["symbol"], at)
            quantity, delta, cost = 0, Fraction(), Fraction()
            if price is None:
                _freeze(state, events, at, "missing_entry_price:" + plan["symbol"], plan=plan)
                reason = "entry_price_unknown"
            elif price <= plan["structural_stop"]:
                reason = "invalidated_entry"
            else:
                nav = _nav(state, marks)
                _need(nav is not None, "identified entry NAV required")
                cash_budget = state["book"]["cash"] / (1 + state["cost_bps"] / 10000)
                budget = min(nav / 50, cash_budget)
                quantity = budget // price
                if quantity:
                    notional = quantity * price
                    cost = notional * state["cost_bps"] / 10000
                    delta = -notional - cost
                    state["book"]["cash"] += delta
                    state["total_entry_cost"] += cost
                    state["book"]["lots"].append({"candidate": plan, "quantity": quantity, "entry_price": price,
                        "entry_cost": cost, "exit_at": plan["cap_exit_at"], "exit_reason": "time_cap"})
                    marks[plan["symbol"]] = price
                    reason = "entered"
                else:
                    reason = "insufficient_cash_for_one_share" if cash_budget < price else "one_share_exceeds_nav_limit"
            events.append(_event(at, "entry", reason, plan, quantity=quantity, cash_delta=delta, assumed_cost=cost))
            gates = _value(state, events, snapshots, at, marks, "after_entry")
        state["last_at"] = at
        if compress_idle_minutes and not state["book"]["lots"] and at < session["closing"]:
            at = flat_clocks[bisect_right(flat_clocks, at)]
        else:
            at += MINUTE
    _check_cash_identity(state)
    return {"state": state, "events": events, "snapshots": snapshots,
            "scenario": "full_population_with_explicit_unknown_actions", "assumptions": list(ASSUMPTIONS)}


class EDAccountVerifier:
    """One oracle per arm/cost/scope, initialized solely from standing capital."""
    def __init__(self, arm: str, cost: int | Decimal | Fraction = 33, scope: str = "continuous", *,
                 domain: str | None = None):
        _need(arm in ARMS and scope in SCOPES, "registered ED arm/scope required")
        _need(domain in (None, "full_original_population", "observed_predecision_support_conditional"),
              "explicit registered account domain required")
        cost = _exact(cost)
        _need(cost in (33, 43), "registered 33/43 cost required")
        self.arm, self.cost, self.scope = arm, cost, scope
        self.domain = domain
        self._state = _fresh(arm, cost, scope)
        self._last_verified_close = None
        self.verified_days = 0

    def verify_day(self, actions, minutes: Mapping, session, actual_result, *,
                   compress_idle_minutes: bool = False) -> dict:
        """Compare every event, every NAV observation and the full final state.

        A failed assertion leaves this oracle's carried state untouched. Fresh
        day diagnostics reset their own capital on every call and cannot become
        a continuous equity path. No source-only result authenticates raw inputs.
        """
        session = _plain(session)
        _need(type(session) is dict and set(session) == {"opening", "closing", "policy_state", "reason"},
              "session schema differs")
        opening, close = _clock(session["opening"]), _clock(session["closing"])
        _need(opening < close and opening.date() == close.date() and session["policy_state"] in ("available", "unavailable")
              and type(session["reason"]) is str and bool(session["reason"]), "session contract differs")
        _need(self._last_verified_close is None or opening > self._last_verified_close, "nonchronological verification")
        _need(isinstance(minutes, Mapping), "minute mapping required")
        _need(type(compress_idle_minutes) is bool, "boolean idle-clock option required")
        working = deepcopy(self._state) if self.scope == "continuous" else _fresh(self.arm, self.cost, self.scope)
        expected = _reconstruct(working, tuple(actions), minutes, session,
                                compress_idle_minutes=compress_idle_minutes)
        if self.scope == "day_local_counterfactual":
            expected["scenario"] = "fresh_day_counterfactual_no_continuous_weekly_monthly_claim"
        if self.domain is not None:
            scope_label = "continuous_sticky_unknown" if self.scope == "continuous" else "fresh_day_counterfactual_never_continuous_NAV"
            expected["scenario"] = self.domain + "/" + scope_label
        # The generic low-level comparator intentionally accepts equivalent
        # scalar integers. Accounting additionally requires actual integer lots
        # and event quantities, never fractional vendor-price units.
        actual = _plain(actual_result)
        _need(type(actual) is dict and type(actual.get("state")) is dict
              and type(actual.get("events")) is list, "account result schema differs")
        _need(all(type(event.get("quantity")) is int for event in actual["events"])
              and all(type(lot.get("quantity")) is int for lot in actual["state"]["book"]["lots"]),
              "integer output quantities required")
        _equal(actual_result, expected, "EDAccountResult")
        self._state = expected["state"]
        self._last_verified_close = close
        self.verified_days += 1
        return {"schema": "ED-independent-account-verification-v1", "verified": True,
                "arm": self.arm, "cost_bps": self.cost, "scope": self.scope, "session": opening.date().isoformat(),
                "events": len(expected["events"]), "snapshots": len(expected["snapshots"]),
                "verified_days": self.verified_days, "domain": self.domain,
                "full_population_identified": self.domain != "observed_predecision_support_conditional" and not bool(
                    self._state["book"]["indeterminate_reasons"]), "producer_state_used_as_input": False}
