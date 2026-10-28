"""Independent EC account reconstruction from authenticated, in-memory inputs.

This module imports no producer accounting, replay, comparison or statistics
functions. The verifier owns its initial capital and all subsequent states;
producer states are assertions to check, never reconstruction inputs. It opens
no files and cannot authenticate source bytes: the caller owns that boundary.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from functools import lru_cache
from math import fsum, isclose, sqrt
from random import Random
from statistics import NormalDist, stdev


CAPITAL = Fraction(100_000_000)
MINUTE = timedelta(minutes=1)
SCOPES = ("continuous", "day_local_counterfactual")
_SCALAR_TYPES = frozenset((str, int, bool, float, Fraction, datetime, date, timedelta, type(None)))


class VerificationError(ValueError):
    """A reconstructed observation disagrees with the producer output."""


@lru_cache(maxsize=None)
def _record_fields(kind):
    """Dataclass schemas are fixed; cache names without invoking value equality."""
    if is_dataclass(kind):
        names = tuple(field.name for field in fields(kind))
        return names, frozenset(names)
    return None


def _plain(value):
    kind = type(value)
    if kind in _SCALAR_TYPES:
        return value
    if kind is dict:
        return {key: _plain(item) for key, item in value.items()}
    if kind is list or kind is tuple:
        return [_plain(item) for item in value]
    record = _record_fields(kind)
    if record is not None:
        return {name: _plain(getattr(value, name)) for name in record[0]}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _equal(actual, expected, path):
    """Compare every field lazily, retaining the original normalized contract."""
    _compare(actual, expected, path)


def _compare(actual, expected, path):
    expected_type, actual_type = type(expected), type(actual)
    if expected_type in _SCALAR_TYPES:
        _compare_scalar(actual, expected, path, expected_type, actual_type)
        return
    expected_record = _record_fields(expected_type)
    if expected_record is not None:
        names, expected_keys = expected_record
    elif expected_type is dict or isinstance(expected, Mapping):
        expected_keys = expected.keys()
        names = expected_keys
    else:
        expected_keys = None
    if expected_keys is not None:
        actual_record = _record_fields(actual_type)
        if actual_record is not None:
            actual_keys = actual_record[1]
        elif actual_type is dict or isinstance(actual, Mapping):
            actual_keys = actual.keys()
        else:
            raise VerificationError(f"{path}: field set differs")
        if actual_keys != expected_keys:
            raise VerificationError(f"{path}: field set differs")
        for key in names:
            left = getattr(actual, key) if actual_record is not None else actual[key]
            right = getattr(expected, key) if expected_record is not None else expected[key]
            _compare(left, right, f"{path}.{key}")
    elif expected_type is list or expected_type is tuple or isinstance(expected, (tuple, list)):
        if not (actual_type is list or actual_type is tuple or isinstance(actual, (tuple, list))) or len(actual) != len(expected):
            raise VerificationError(f"{path}: sequence length differs")
        for index, (left, right) in enumerate(zip(actual, expected)):
            _compare(left, right, f"{path}[{index}]")
    else:
        _compare_scalar(actual, expected, path, expected_type, actual_type)


def _compare_scalar(actual, expected, path, expected_type, actual_type):
    if expected_type is float or isinstance(expected, float):
        if not isinstance(actual, float) or not isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15):
            raise VerificationError(f"{path}: reconstructed statistical value differs")
    elif isinstance(expected, Fraction) and not isinstance(actual, Fraction):
        raise VerificationError(f"{path}: exact Fraction output required")
    elif ((actual_type not in _SCALAR_TYPES and _record_fields(actual_type) is not None)
          or actual != expected or (isinstance(actual, bool) != isinstance(expected, bool))):
        raise VerificationError(f"{path}: reconstructed value differs")


def _week(day):
    parsed = date.fromisoformat(day)
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def _fresh(mode, cost, scope):
    strategies = ["A", "B"] if mode == "A+B" else [mode]
    allocation = CAPITAL / len(strategies)
    return {"mode": mode, "cost_bps": Fraction(cost), "risk_scope": scope,
            "book": {"initial_capital": CAPITAL,
                     "sleeves": [{"strategy": strategy, "initial_allocation": allocation,
                                  "cash": allocation} for strategy in strategies],
                     "lots": [], "indeterminate_reasons": []},
            "holdings": [], "daily_anchor": None, "weekly_anchor": None,
            "monthly_anchor": None, "peak_nav": CAPITAL,
            "maximum_drawdown": Fraction(), "last_at": None, "unknown_at": None}


def _cash(state):
    return sum((sleeve["cash"] for sleeve in state["book"]["sleeves"]), Fraction())


def _freeze(state, reason, at):
    reasons = state["book"]["indeterminate_reasons"]
    if reason not in reasons:
        reasons.append(reason)
    if state["unknown_at"] is None:
        state["unknown_at"] = at


def _event(at, kind, reason, plan=None, **extra):
    fields = {"at": at, "kind": kind, "reason": reason,
              "strategy": plan["strategy"] if plan else None,
              "symbol": plan["symbol"] if plan else None, "quantity": 0,
              "cash_delta": Fraction(), "assumed_cost": Fraction(),
              "decision_at": plan["decision_at"] if plan else None,
              "scheduled_exit_at": None}
    fields.update(extra)
    return fields


def _open(day, symbol, at):
    coordinate = (symbol, at)
    price = day["open_prices"].get(coordinate)
    if price is None:
        return None
    bar = day["minute_bars"].get(coordinate)
    if bar is not None and Fraction(bar[0]) != Fraction(price):
        return None
    return Fraction(price)


def _valuation(state, at, prices, phase, events):
    sleeve_values = []
    for sleeve in state["book"]["sleeves"]:
        value = sleeve["cash"]
        for lot in state["book"]["lots"]:
            if lot["strategy"] != sleeve["strategy"]:
                continue
            price = prices.get(lot["symbol"])
            if price is None:
                reason = f"missing_held_price:{lot['strategy']}:{lot['symbol']}"
                if reason not in state["book"]["indeterminate_reasons"]:
                    events.append(_event(at, "unknown", reason))
                _freeze(state, reason, at)
                value = None
            elif value is not None:
                value += lot["quantity"] * price
        sleeve_values.append((sleeve["strategy"], value))
    if state["book"]["indeterminate_reasons"]:
        return None, ["account_indeterminate"]
    nav = sum(value for _, value in sleeve_values)
    iso_year, iso_week, _ = at.date().isocalendar()
    periods = [("daily", at.date().isoformat()), ("weekly", f"{iso_year}-W{iso_week:02d}"),
               ("monthly", at.date().isoformat()[:7])]
    if state["risk_scope"] == "day_local_counterfactual":
        periods = periods[:1]
    returns = []
    for name, period in periods:
        anchor_name = f"{name}_anchor"
        if state[anchor_name] is None or state[anchor_name]["period"] != period:
            state[anchor_name] = {"period": period, "nav": nav}
        returns.append((name, nav / state[anchor_name]["nav"] - 1))
    thresholds = {"daily": Fraction(-5, 1000), "weekly": Fraction(-15, 1000),
                  "monthly": Fraction(-3, 100)}
    gates = [name for name, change in returns if change <= thresholds[name]]
    if dict(returns).get("monthly", Fraction()) <= Fraction(-4, 100):
        gates.append("hard_monthly")
    state["peak_nav"] = max(state["peak_nav"], nav)
    drawdown = 1 - nav / state["peak_nav"]
    state["maximum_drawdown"] = max(state["maximum_drawdown"], drawdown)
    return {"at": at, "phase": phase, "nav": nav, "cash": _cash(state),
            "sleeve_navs": sleeve_values, "period_returns": returns,
            "entry_gates": gates, "drawdown": drawdown}, gates


def _reconstruct(day, candidates, initial, *, compress_idle_minutes=False):
    state = deepcopy(initial)
    events, snapshots, closed = [], [], []
    plans = sorted((_plain(candidate) for candidate in candidates),
                   key=lambda p: (p["decision_at"], p["symbol"], p["strategy"]))
    by_entry = {}
    for plan in plans:
        by_entry.setdefault(plan["entry_at"], []).append(plan)
    decision_clocks = sorted({day["session_open"], day["session_close"], *by_entry})
    at = day["session_open"]
    while at <= day["session_close"]:
        # A scheduled OPEN sale precedes all CLOSE observations and purchases.
        for held in list(state["holdings"]):
            if held["exit_at"] != at:
                continue
            plan = held["candidate"]
            lot = next(item for item in state["book"]["lots"] if item["symbol"] == plan["symbol"])
            price = _open(day, plan["symbol"], at)
            if price is None:
                _freeze(state, f"missing_exit_price:{plan['strategy']}:{plan['symbol']}", at)
                events.append(_event(at, "exit", "exit_price_unknown", plan))
            else:
                proceeds = lot["quantity"] * price
                sleeve = next(item for item in state["book"]["sleeves"] if item["strategy"] == plan["strategy"])
                sleeve["cash"] += proceeds
                events.append(_event(at, "exit", held["exit_reason"], plan,
                                     quantity=lot["quantity"], cash_delta=proceeds))
                net = proceeds - lot["quantity"] * lot["entry_price"] - lot["entry_cost"]
                closed.append({"strategy": plan["strategy"], "symbol": plan["symbol"],
                               "decision_at": plan["decision_at"], "entry_at": plan["entry_at"],
                               "exit_at": at, "quantity": lot["quantity"],
                               "entry_notional": lot["quantity"] * lot["entry_price"],
                               "exit_notional": proceeds, "assumed_cost": lot["entry_cost"],
                               "net_pnl": net, "gross_pnl": net + lot["entry_cost"]})
                state["book"]["lots"].remove(lot)
                state["holdings"].remove(held)
        prices = {}
        for held in state["holdings"]:
            plan = held["candidate"]
            lot = next(item for item in state["book"]["lots"] if item["symbol"] == plan["symbol"])
            if at >= held["exit_at"]:
                price = None
            elif at < plan["entry_at"] + 3 * MINUTE:
                price = lot["entry_price"]
            else:
                coordinate = (plan["symbol"], at - 3 * MINUTE)
                bar = day["minute_bars"].get(coordinate)
                observed_open = day["open_prices"].get(coordinate)
                price = (Fraction(bar[3]) if bar is not None and observed_open is not None
                         and Fraction(observed_open) == Fraction(bar[0]) else None)
            prices[plan["symbol"]] = price
            if price is not None and at >= plan["entry_at"] + 3 * MINUTE and price <= plan["structural_stop"]:
                new_exit = min(at + MINUTE, plan["cap_exit_at"])
                if held["exit_reason"] == "time_cap":
                    held["exit_at"] = new_exit
                    held["exit_reason"] = ("stop_at_time_cap" if new_exit == plan["cap_exit_at"]
                                           else "delayed_structural_stop")
                    events.append(_event(at, "stop", held["exit_reason"], plan,
                                         scheduled_exit_at=new_exit))
        snapshot, gates = _valuation(state, at, prices, "before_entries", events)
        if snapshot is not None:
            snapshots.append(snapshot)
        for plan in by_entry.get(at, []):
            if gates:
                events.append(_event(at, "entry", "entry_blocked:" + ",".join(gates), plan))
                continue
            duplicate = any(lot["symbol"] == plan["symbol"] for lot in state["book"]["lots"])
            price = None if duplicate else _open(day, plan["symbol"], at)
            if not duplicate and price is not None and price <= plan["structural_stop"]:
                events.append(_event(at, "entry", "invalidated_entry", plan))
                continue
            quantity, delta, cost = 0, Fraction(), Fraction()
            if duplicate:
                reason = "duplicate_position"
            elif price is None:
                reason = "entry_price_unknown"
                _freeze(state, f"missing_entry_price:{plan['strategy']}:{plan['symbol']}", at)
            else:
                sleeve = next(item for item in state["book"]["sleeves"] if item["strategy"] == plan["strategy"])
                cash_limit = sleeve["cash"] / (1 + state["cost_bps"] / 10000)
                budget = min(snapshot["nav"] / 50,
                             dict(snapshot["sleeve_navs"])[plan["strategy"]] / 50,
                             cash_limit)
                quantity = budget // price
                if quantity:
                    cost = quantity * price * state["cost_bps"] / 10000
                    delta = -quantity * price - cost
                    sleeve["cash"] += delta
                    state["book"]["lots"].append({"strategy": plan["strategy"], "symbol": plan["symbol"],
                                                   "quantity": quantity, "entry_price": price,
                                                   "entry_cost": cost})
                    state["holdings"].append({"candidate": plan, "exit_at": plan["cap_exit_at"],
                                              "exit_reason": "time_cap"})
                    prices[plan["symbol"]] = price
                    reason = "entered"
                else:
                    reason = ("insufficient_cash_for_one_share" if cash_limit < price
                              else "one_share_exceeds_nav_limit")
            events.append(_event(at, "entry", reason, plan, quantity=quantity,
                                 cash_delta=delta, assumed_cost=cost))
            snapshot, gates = _valuation(state, at, prices, "after_entry", events)
            if snapshot is not None:
                snapshots.append(snapshot)
        state["last_at"] = at
        if at == day["session_close"]:
            break
        if compress_idle_minutes and not state["holdings"] and not state["book"]["lots"]:
            # A flat balance cannot change between original decision clocks.
            # Keep the terminal valuation even after a sticky unknown entry.
            at = decision_clocks[bisect_right(decision_clocks, at)]
        else:
            at += MINUTE
    return state, events, snapshots, closed


def _compact(day, state, events, snapshots, closed, coverage):
    opening, closing = day["session_open"], day["session_close"]
    first = snapshots[0] if snapshots else None
    last = snapshots[-1] if snapshots else None
    start = first["nav"] if first and first["at"] == opening and first["phase"] == "before_entries" else None
    final = last["nav"] if last and last["at"] == closing and state["unknown_at"] is None else None
    identified = start is not None and final is not None
    cost = sum((event["assumed_cost"] for event in events), Fraction())
    end = max(opening, min(closing, state["unknown_at"] or closing))
    last_per_clock = {snapshot["at"]: snapshot for snapshot in snapshots}
    invested = fraction_minutes = maximum = maximum_fraction = Fraction()
    clocks = sorted(last_per_clock)
    for index, clock in enumerate(clocks):
        snapshot = last_per_clock[clock]
        until = min(clocks[index + 1] if index + 1 < len(clocks) else end, end)
        minutes = max(Fraction(), Fraction((until - clock) // timedelta(seconds=1), 60))
        exposure = snapshot["nav"] - snapshot["cash"]
        invested += minutes if exposure > 0 else 0
        fraction_minutes += exposure / snapshot["nav"] * minutes
    for snapshot in snapshots:
        exposure = snapshot["nav"] - snapshot["cash"]
        maximum = max(maximum, exposure)
        maximum_fraction = max(maximum_fraction, exposure / snapshot["nav"])
    reasons = Counter(event["reason"] for event in events if event["kind"] == "entry" and not event["quantity"])
    entries = [event for event in events if event["kind"] == "entry" and event["quantity"]]
    exits = [event for event in events if event["kind"] == "exit" and event["quantity"]]
    identified_exits = sum(state["unknown_at"] is None or event["at"] < state["unknown_at"] for event in exits)
    for lot in closed:
        lot["identification"] = ("identified_before_unknown" if state["unknown_at"] is None
                                 or lot["exit_at"] < state["unknown_at"] else "conditional_after_or_at_unknown")
    terminal = {"cash": _cash(state), "sleeves": state["book"]["sleeves"],
                "cash_scope": "conditional_bookkeeping_after_first_unknown" if state["unknown_at"] is not None else "identified_observed_candidate_scenario",
                "lots": state["book"]["lots"], "holdings": state["holdings"],
                "marks": {lot["symbol"]: None for lot in state["book"]["lots"]},
                "equity_status": "identified" if final is not None else "indeterminate",
                "unknown_at": state["unknown_at"], "unknown_reasons": state["book"]["indeterminate_reasons"],
                **{key: state[key] for key in ("daily_anchor", "weekly_anchor", "monthly_anchor")}}
    net = final - start if identified else None
    return {"session": day["session"], "week": _week(day["session"]),
            "status": "identified" if identified else "indeterminate",
            "full_universe_status": "indeterminate_missing_signals" if coverage["unknown_signal_units"] else "recorded_signal_population_only",
            "coverage": deepcopy(coverage), "start_nav": start, "final_nav": final,
            "net_pnl": net, "gross_pnl_same_fills": net + cost if net is not None else None,
            "assumed_cost": cost, "events": events, "known_entry_count": len(entries),
            "unique_entry_count": len({(e["strategy"], e["symbol"], e["decision_at"]) for e in entries}),
            "known_exit_count": len(exits), "exit_count_scope": "modeled_observed_OPEN_exits",
            "identified_exit_count": identified_exits, "conditional_exit_count": len(exits) - identified_exits,
            "entry_rejections": dict(sorted(reasons.items())), "same_symbol_declines": reasons["duplicate_position"],
            "cash_rejections": reasons["insufficient_cash_for_one_share"], "size_rejections": reasons["one_share_exceeds_nav_limit"],
            "risk_rejections": sum(n for reason, n in reasons.items() if reason.startswith("entry_blocked:") and reason != "entry_blocked:account_indeterminate"),
            "closed_lots": closed,
            "exposure": {"status": "complete" if identified else "identified_prefix_only",
                         "identified_minutes": (end - opening) // MINUTE, "invested_minutes": invested,
                         "fraction_minutes": fraction_minutes, "maximum_value": maximum,
                         "maximum_fraction": maximum_fraction},
            "prefix_maximum_drawdown": state["maximum_drawdown"],
            "last_identified_valuation": last, "terminal": terminal}


def _summary(days, weeks, reference):
    identified = [row for row in days if row["status"] == "identified"]
    count = len(identified)
    mean_net = sum((row["net_pnl"] for row in identified), Fraction()) / count if count else None
    mean_gross = sum((row["gross_pnl_same_fills"] for row in identified), Fraction()) / count if count else None
    totals = {field: sum(row[field] for row in days) for field in
              ("known_entry_count", "known_exit_count", "identified_exit_count", "conditional_exit_count",
               "same_symbol_declines", "cash_rejections", "size_rejections", "risk_rejections")}
    complete = count == len(reference)
    weekly = {}
    for week in weeks:
        rows = [row for row in days if row["week"] == week]
        expected = sum(_week(day) == week for day in reference)
        known = [row for row in rows if row["status"] == "identified"]
        complete_week = bool(rows) and len(rows) == len(known) == expected
        entries = sum(row["known_entry_count"] for row in rows)
        weekly[week] = {"reference_days": expected, "included_days": len(rows), "identified_days": len(known),
                        "status": "complete_observed_candidate_week" if complete_week else "incomplete",
                        "known_entry_count": entries, "entry_count_if_complete": entries if complete_week else None,
                        "unknown_signal_units": sum(row["coverage"]["unknown_signal_units"] for row in rows),
                        "net_pnl_if_complete": sum((row["net_pnl"] for row in rows), Fraction()) if complete_week else None}
    dates, symbols, conditional, identified_lots = {}, {}, [], 0
    for row in days:
        for lot in row["closed_lots"]:
            if lot["identification"] != "identified_before_unknown":
                conditional.append(lot)
                continue
            identified_lots += 1
            dates[row["session"]] = dates.get(row["session"], Fraction()) + lot["net_pnl"]
            symbols[lot["symbol"]] = symbols.get(lot["symbol"], Fraction()) + lot["net_pnl"]

    def concentration(groups):
        total = sum((abs(value) for value in groups.values()), Fraction())
        return {"net_pnl": groups, "absolute_net_pnl_share": {key: abs(value) / total if total else None for key, value in groups.items()},
                "sum_absolute_group_net_pnl": total}

    return {"reference_days": len(reference), "included_days": len(days), "excluded_days": len(reference) - len(days),
            "identified_days": count, "indeterminate_days": len(days) - count,
            "complete_zero_trade_days": sum(row["known_entry_count"] == 0 for row in identified),
            "mean_daily_net_pnl": mean_net, "mean_daily_gross_pnl_same_fills": mean_gross,
            "mean_daily_net_per_initial_capital": mean_net / CAPITAL if mean_net is not None else None,
            "mean_daily_gross_per_initial_capital": mean_gross / CAPITAL if mean_gross is not None else None,
            "known_total_assumed_cost": sum((row["assumed_cost"] for row in days), Fraction()),
            "known_unique_entry_count": sum(row["unique_entry_count"] for row in days), **totals,
            "exit_count_scope": "modeled_observed_OPEN_exits",
            "entry_count_if_complete": totals["known_entry_count"] if complete else None,
            "known_entries_per_reference_week": Fraction(totals["known_entry_count"], len(weeks)) if weeks else None,
            "frequency_status": "complete_observed_candidates" if complete else "known_entries_only_incomplete_calendar",
            "by_week": weekly,
            "closed_lot_concentration": {"scope": "identified_closed_lots_strictly_before_first_unknown_only",
                "identified_closed_lot_count": identified_lots, "conditional_closed_lot_count": len(conditional),
                "conditional_net_pnl": sum((lot["net_pnl"] for lot in conditional), Fraction()),
                "conditional_gross_pnl": sum((lot["gross_pnl"] for lot in conditional), Fraction()),
                "conditional_scope": "bookkeeping_after_or_at_unknown_not_confirmed_realized_pnl",
                "by_date": concentration(dates), "by_symbol": concentration(symbols)}}


def _sizing(days, reference, label="net_pnl", peer=None):
    """Rebuild account-unit dispersion and fixed calendar bootstrap independently."""
    own = {row["session"]: row for row in days if row["status"] == "identified"}
    peers = {row["session"]: row for row in peer if row["status"] == "identified"} if peer is not None else None
    known_dates = [day for day in reference if day in own and (peers is None or day in peers)]
    labels = {day: (own[day][label] - (peers[day][label] if peers is not None else 0)) / CAPITAL for day in known_dates}
    values = [float(labels[day]) for day in known_dates]
    n, groups = len(values), len(reference)
    benchmark = sum((own[day]["assumed_cost"] for day in known_dates), Fraction()) / (n * CAPITAL) if n else None
    if peers is not None and n:
        benchmark = max(benchmark, sum((peers[day]["assumed_cost"] for day in known_dates), Fraction()) / (n * CAPITAL))
    sd = naive = cluster = bootstrap = design = ratio = mde = None
    zero_draws = 0
    if n >= 2 and groups >= 2:
        sd, naive = stdev(values), stdev(values) / sqrt(n)
        mean = fsum(values) / n
        cluster = sqrt(groups / (groups - 1) * fsum((value - mean) ** 2 for value in values)) / n
        # Use original reference positions, including empty dates, in every draw.
        calendar = [(float(labels[day]), 1) if day in labels else (0.0, 0) for day in reference]
        widths = [5] * (groups // 5) + ([groups % 5] if groups % 5 else [])
        windows = {}
        for width in set(widths):
            windows[width] = []
            for start in range(groups):
                selected = [calendar[(start + offset) % groups] for offset in range(width)]
                windows[width].append((fsum(item[0] for item in selected), sum(item[1] for item in selected)))
        randomizer, draws = Random(0), []
        for _ in range(2000):
            blocks = [windows[width][randomizer.randrange(groups)] for width in widths]
            observed = sum(block[1] for block in blocks)
            if observed:
                draws.append(fsum(block[0] for block in blocks) / observed)
        zero_draws = 2000 - len(draws)
        bootstrap = stdev(draws) if len(draws) >= 2 else None
        design = max(naive, cluster, bootstrap) if bootstrap is not None else None
        ratio = design / naive if naive and design is not None else None
        mde = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * design * 10000 if design is not None else None
    if not n:
        reason = "no_known_labels"
    elif n < 2:
        reason = "fewer_than_two_known_days"
    elif sd == 0:
        reason = "zero_dispersion"
    elif design is None or not design:
        reason = "undefined_or_zero_design_se"
    elif not benchmark:
        reason = "zero_paid_cost_benchmark"
    else:
        reason = None
    underpowered = reason is None and mde > float(benchmark * 10000)
    status = "uninformative" if reason else "underpowered" if underpowered else "descriptive_only"
    return {"events": groups, "known_labels": n, "unknown_labels": groups - n,
            "reference_sessions": groups, "known_label_sessions": n, "event_sd": sd,
            "naive_se": naive, "session_cluster_se": cluster, "bootstrap_se": bootstrap, "design_se": design,
            "corrected_to_naive_se_ratio": ratio, "mde_bps": mde, "bootstrap_zero_label_draws": zero_draws,
            "cost_floor_bps_assumed": benchmark * 10000 if benchmark is not None else None,
            "would_require_operator_underpower_choice": False,
            "planning_constraint": reason or ("mde_exceeds_account_cost_scale" if underpowered else "descriptive_only_no_certification"),
            "planning_constraint_scope": "EC observed-only own account labels; underpower disclosure retained",
            "family_adopted": False, "full_family_certified": False, "label": label,
            "label_unit": "daily_cash_pnl_divided_by_initial_capital", "status": status,
            "economic_benchmark_bps": benchmark * 10000 if benchmark is not None else None,
            "economic_benchmark_exact": benchmark,
            "benchmark_formula": "max(mean_own_cost,mean_peer_cost)/initial_capital" if peers is not None else "mean_assumed_cost_on_own_identified_days/initial_capital",
            "benchmark_scope": "comparison_planning_cost_scale_not_formal_power_guarantee",
            "requires_operator_choice": False, "underpowered_descriptive_authorized": True,
            "uninformative_reason": reason, "strategy_pass": False, "sizing_pass": False,
            "method": "existing_naive_session_fixed5session_block2000_seed0"}


class Verifier:
    """Stateful day callback. A receipt names the verification actually done."""

    def __init__(self):
        self._states = {}
        self._days = {}
        self._sources = {}
        self._plans = {}
        self._clocks = {}
        self._compression = {}

    def verify_day(self, *, day_input, candidates, initial_state, result, compact, scenario):
        policy, cost, mode, scope = (scenario[key] for key in ("timing_policy", "cost_bps", "mode", "risk_scope"))
        compressed = scenario.get("compress_idle_minutes", False)
        if type(compressed) is not bool:
            raise VerificationError("compress_idle_minutes must be boolean")
        if policy not in ("D+1", "D+2") or cost not in (33, 43) or mode not in ("A", "B", "A+B") or scope not in SCOPES:
            raise VerificationError("unregistered scenario")
        key = (policy, cost, mode, scope)
        session = day_input["session"]
        days = self._days.get(key, [])
        if days and days[-1]["session"] >= session:
            raise VerificationError("day order or duplicate callback")
        initial = self._states.get(key, _fresh(mode, cost, scope)) if scope == "continuous" else _fresh(mode, cost, scope)
        _equal(initial_state, initial, "initial_state")
        state, events, snapshots, closed = _reconstruct(
            day_input, candidates, initial, compress_idle_minutes=compressed)
        _equal(result.state, state, "result.state")
        _equal(result.events, events, "result.events")
        _equal(result.snapshots, snapshots, "result.snapshots")
        expected_scenario = ("fresh_daily_counterfactual_no_continuous_weekly_monthly_claim"
                             if scope == "day_local_counterfactual" else
                             "observed_candidates_conditional_on_archive_availability")
        _equal(result.scenario, expected_scenario, "result.scenario")
        expected = _compact(day_input, state, events, snapshots, closed, _plain(compact["coverage"]))
        _equal(compact, expected, "compact")
        receipt = {name: deepcopy(day_input[name]) for name in
                   ("sources", "retained_price_symbols", "consumed_target_chunks", "consumed_price_rows", "original_union_complete")}
        if session in self._sources:
            _equal(receipt, self._sources[session], "day_input.receipt")
        self._sources[session] = receipt
        self._clocks[session] = (day_input["session_open"], day_input["session_close"])
        self._states[key] = state
        self._days.setdefault(key, []).append(deepcopy(expected))
        self._plans.setdefault(key, {})[session] = _plain(candidates)
        self._compression.setdefault(key, {})[session] = compressed
        return {"status": "verified", "session": session, "scenario": dict(scenario),
                "events": len(events), "snapshots": len(snapshots)}

    def finish_verify(self, report):
        """Check all retained days, aggregate fields, common dates and own sizing.

        Exact cash arithmetic has no tolerance. Only floating statistical
        outputs use relative 1e-12 / absolute 1e-15 comparison tolerances.
        This receipt does not certify upstream bytes or market availability.
        """
        reference = list(report["reference_sessions"])
        expected_keys = {(policy, cost, mode, scope) for policy in ("D+1", "D+2")
                         for cost in (33, 43) for mode in ("A", "B", "A+B") for scope in SCOPES}
        if set(self._days) != expected_keys:
            raise VerificationError("incomplete scenario verification")
        _equal(list(self._sources), reference, "reference_sessions")
        _equal(report["reference_weeks"], sorted({_week(day) for day in reference}), "reference_weeks")
        _equal(report["initial_capital"], CAPITAL, "initial_capital")
        _equal(report["day_replay_count"], len(reference) * 24, "day_replay_count")
        _equal(report["idle_minutes_compressed"], True, "idle_minutes_compressed")
        _equal(set(report["scenarios"]), {f"{p}|{c}|{m}" for p, c, m, _ in expected_keys}, "scenario_keys")
        _equal(report["mode"], "ec-p01-p02-p03-observed-account-comparison", "mode")
        for field, expected in {
            "account_path_scope": "recorded_detectable_candidates_conditioned_on_archive_availability",
            "continuous_scope": "identified_prefix_freezes_at_first_unknown_without_next_day_reset",
            "daily_scope": "fresh_initial_capital_day_local_gate_only_no_continuous_account_claim",
            "gross_scope": "net_plus_assumed_cost_on_same_fills_no_zero_cost_rerun",
            "performance_computed": True, "strategy_pass": False, "promotion_allowed": False,
        }.items():
            _equal(report[field], expected, field)
        evidence = report["input_evidence"]
        _equal(set(evidence), {"A", "B"}, "input_evidence.keys")
        for projection in evidence.values():
            _equal(projection["reference_sessions"], reference, "input_evidence.reference_sessions")
            _equal(projection["reference_weeks"], report["reference_weeks"], "input_evidence.reference_weeks")
        for key, days in self._days.items():
            policy, cost, mode, scope = key
            _equal([day["session"] for day in days], reference, "scenario.reference_sessions")
            _equal(self._compression[key], {day: True for day in reference}, "scenario.compression_callbacks")
            result = report["scenarios"][f"{policy}|{cost}|{mode}"]
            _equal(result["timing_policy"], policy, "scenario.timing_policy")
            _equal(result["cost_bps"], cost, "scenario.cost_bps")
            _equal(result["mode"], mode, "scenario.mode")
            _equal(result[scope]["days"], days, "scenario.days")
            for day in days:
                included = [evidence[strategy] for strategy in ("A", "B") if mode == "A+B" or mode == strategy]
                by_setup, plans = {}, []
                opening, closing = self._clocks[day["session"]]
                for projection in included:
                    content = projection["variants"][policy]["dates"][day["session"]]
                    _equal(sorted(row["symbol"] for row in content["targets"] if row["signal_state"] == "signal"),
                           sorted(row["symbol"] for row in content["candidates"]), "projected.signal_candidates")
                    counts = {"targets": len(content["targets"]), "signals": len(content["candidates"]),
                              "known_no_signal": sum(target["signal_state"] == "known_no_signal" for target in content["targets"]),
                              "unknown_signals": sum(target["signal_state"] == "unknown" for target in content["targets"]),
                              "clock_ineligible": 0}
                    for candidate in content["candidates"]:
                        eligible = opening < candidate["decision_at"] < candidate["entry_at"] < candidate["cap_exit_at"] < closing
                        _equal(candidate["clock_eligible"], eligible, "candidate.clock_eligible")
                        counts["clock_ineligible"] += not eligible
                        if eligible:
                            plans.append({field: candidate[field] for field in
                                          ("strategy", "symbol", "decision_at", "entry_at", "cap_exit_at", "structural_stop")})
                    _equal(content["counts"], counts, "projected.counts")
                    by_setup[projection["setup_id"]] = counts
                coverage = {"by_setup": by_setup,
                            "unknown_signal_units": sum(counts["unknown_signals"] for counts in by_setup.values()),
                            "clock_ineligible_units": sum(counts["clock_ineligible"] for counts in by_setup.values()),
                            "recorded_signal_units": sum(counts["signals"] for counts in by_setup.values())}
                _equal(day["coverage"], coverage, "day.coverage")
                _equal(self._plans[key][day["session"]], plans, "day.candidates")
            expected = {"days": days, "summary": _summary(days, report["reference_weeks"], reference),
                        "own_sizing": {label: _sizing(days, reference, label) for label in ("net_pnl", "gross_pnl_same_fills")}}
            _equal(result[scope], expected, f"scenario.{policy}.{cost}.{mode}.{scope}")
        for index, session in enumerate(reference):
            receipt = self._sources[session]
            _equal(receipt["original_union_complete"], index == len(reference) - 1, "day.original_union_complete")
            _equal(receipt["consumed_target_chunks"], len(receipt["sources"]), "day.consumed_target_chunks")
            _equal(receipt["consumed_price_rows"], sum(source["input_rows"] for source in receipt["sources"]), "day.consumed_price_rows")
            targets, retained = set(), set()
            for projection in evidence.values():
                for policy in ("D+1", "D+2"):
                    content = projection["variants"][policy]["dates"][session]
                    targets.update(row["symbol"] for row in content["targets"])
                    retained.update(row["symbol"] for row in content["candidates"])
                original = {row["symbol"]: row for row in projection["sources"] if row["session"] == session}
                for source in receipt["sources"]:
                    pin = original[source["symbol"]]
                    for field in ("data_vintage", "row_linkage_sha256", "input_rows"):
                        _equal(source[field], pin[field], f"day.source.{field}")
                    if "raw_chunk_metadata" in pin:
                        _equal(source["raw_chunk_metadata"], pin["raw_chunk_metadata"], "day.source.raw_chunk_metadata")
            _equal([source["symbol"] for source in receipt["sources"]], sorted(targets), "day.source_target_union")
            _equal(receipt["retained_price_symbols"], sorted(retained), "day.retained_price_symbols")
        expected_comparisons = {}
        for policy in ("D+1", "D+2"):
            for cost in (33, 43):
                all_days = {mode: self._days[policy, cost, mode, "day_local_counterfactual"] for mode in ("A", "B", "A+B")}
                identified = [{row["session"] for row in days if row["status"] == "identified"} for days in all_days.values()]
                common = [day for day in reference if all(day in dates for dates in identified)]
                selected = {mode: [row for row in days if row["session"] in common] for mode, days in all_days.items()}
                summaries = {mode: _summary(days, report["reference_weeks"], reference) for mode, days in selected.items()}
                expected_comparisons[f"{policy}|{cost}"] = {
                    "timing_policy": policy, "cost_bps": cost, "scope": "three_common_identified_fresh_daily_accounts",
                    "reference_sessions": reference, "reference_weeks": report["reference_weeks"],
                    "common_sessions": common, "excluded_sessions": [day for day in reference if day not in common],
                    "status": "conditional_descriptive_only" if common else "no_common_identified_days",
                    "own_sizing": {mode: {label: _sizing(days, reference, label) for label in ("net_pnl", "gross_pnl_same_fills")}
                                   for mode, days in selected.items()},
                    "paired_sizing": {f"A+B_minus_{mode}": _sizing(selected["A+B"], reference, peer=selected[mode]) for mode in ("A", "B")},
                    "accounts": summaries,
                    "paired_mean_daily_net_per_initial_capital": {f"A+B_minus_{mode}":
                        summaries["A+B"]["mean_daily_net_per_initial_capital"] - summaries[mode]["mean_daily_net_per_initial_capital"] if common else None
                        for mode in ("A", "B")}}
        _equal(report["comparisons"], expected_comparisons, "comparisons")
        for field in ("consumed_target_chunks", "consumed_price_rows"):
            _equal(report[field], sum(receipt[field] for receipt in self._sources.values()), field)
        _equal(report["original_union_complete"], True, "original_union_complete")
        _equal(report["independent_day_callback_completed"], True, "independent_day_callback_completed")
        for field in ("strategy_certification", "historical_brokerage_account_claim"):
            _equal(report[field], False, field)
        if "actual_account_pnl" in report:
            _equal(report["actual_account_pnl"], False, "actual_account_pnl")
        return {"status": "verified", "verified": True, "day_replays": len(reference) * 24,
                "verified_components": ["complete_events", "initial_and_terminal_state", "minute_NAV", "current_risk_gates",
                             "prefix_drawdown", "exposure", "compact_PnL_and_lots", "stream_receipt_totals",
                             "projected_candidate_and_coverage_retention", "aggregate_statistics", "common_date_comparisons", "bootstrap_sizing"],
                "statistical_float_tolerance": {"relative": 1e-12, "absolute": 1e-15},
                "unverified": ["source_byte_authentication", "raw_report_to_projection_transform", "historical_market_availability"]}
