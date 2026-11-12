"""Reconcile late support-reclaim decisions and exact event cash."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction

from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_report as support
from research.intraday_candidate_laggard_peers_report import _reference_symbol_concentration, _symbol_concentration


_OWN_PRICES = ("support_low", "break_close", "reclaim_close")
_HOLD_PRICES = ("hold_close_117", "hold_close_118")
_CASH_FIELDS = ("gross", "cost", "net", "entry_notional")


def _price(plan, key):
    value = plan[key]
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("EJ mid-reclaim report: expected exact fraction string")
    try:
        price = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("EJ mid-reclaim report: invalid price evidence") from exc
    if price <= 0:
        raise ValueError("EJ mid-reclaim report: invalid price evidence")
    return price


def _decisions(plan):
    anchor, broken, reclaimed = (_price(plan, key) for key in _OWN_PRICES)
    holds = tuple(_price(plan, key) for key in _HOLD_PRICES)
    limit, stop = (_price(plan, key) for key in ("limit", "stop"))
    if None in (anchor, broken, reclaimed):
        own, reason = "unresolved", "own_prices_unavailable"
    elif broken >= anchor:
        own, reason = "decline", "break_close_not_below_support"
    elif reclaimed <= anchor:
        own, reason = "decline", "reclaim_close_not_above_support"
    else:
        own = "select"

    if own == "select":
        if any(close is not None and close <= anchor for close in holds):
            hold = "decline"
        else:
            hold = "unresolved" if None in holds else "select"
        order, reason = ("unresolved", "structure_prices_unavailable") if stop is None else ("ready", "order_ready")
        # The stop uses the same bars as the observed closes and cannot exceed any of them.
        valid_geometry = limit == reclaimed and (
            stop is None or (None not in holds and stop <= min(broken, reclaimed, *holds))
        )
    else:
        hold, order = "not_applicable", "retain"
        valid_geometry = (*holds, limit, stop) == (None, None, None, None)
    gated = hold if own == "select" else own

    if (plan["rank_state"], plan["hold_state"], plan["gated_state"]) != (own, hold, gated):
        raise ValueError("EJ mid-reclaim report: own or hold decision differs")
    if not valid_geometry or (plan["order_state"], plan["reason"]) != (order, reason):
        raise ValueError("EJ mid-reclaim report: entry geometry or reason differs")
    return own, gated, order


def _inactive(arm, status):
    expected_cash = None if status == "unresolved" else "0"
    return (arm["status"] == status and arm["fill"] is None and arm.get("entry_request_at") is None
            and all(arm[key] == expected_cash for key in _CASH_FIELDS))


def _validate(question):
    if question["status"] != "opportunity":
        raise ValueError("EJ mid-reclaim report: invalid opportunity status")
    plan = question["rank_decision"]
    own, gated, order = _decisions(plan)
    baseline, candidate = (question[arm] for arm in ("baseline", "candidate"))
    if order == "ready":
        outcome = support._read_execution(baseline)
        valid_baseline = (outcome.status != "no_entry"
                          and outcome.requested == (baseline["fill"] is not None)
                          and (outcome.status == "no_fill" or outcome.requested))
    else:
        valid_baseline = _inactive(baseline, "no_entry" if own == "decline" else "unresolved")
    if gated == own:
        valid_candidate = candidate == baseline
    else:
        valid_candidate = _inactive(candidate, "no_entry" if gated == "decline" else "unresolved")
    if not valid_baseline or not valid_candidate:
        raise ValueError("EJ mid-reclaim report: projected outcome differs from the frozen decision")
    return plan


def _panel(days):
    plans = [plan for members in days for plan in members]
    holds = Counter(plan["hold_state"] for plan in plans)
    result = {"sessions": len(days), "coordinates": len(plans),
              "own_endpoints_missing": {key: sum(plan[key] is None for plan in plans) for key in _OWN_PRICES},
              "eligible_hold_states": {state: holds[state] for state in support.SELECTION}, "arms": {}}
    for arm, key in (("baseline", "rank_state"), ("candidate", "gated_state")):
        states = Counter(plan[key] for plan in plans)
        orders = Counter(plan["order_state"] for plan in plans if plan[key] == "select")
        result["arms"][arm] = {**{name: states[state] for state, name in support.SELECTION.items()},
                               **{f"order_{state}": orders[state] for state in ("ready", "decline", "unresolved")}}
    return result


def summarize(rows, *, audit=False):
    """Check frozen evidence and compare both policies on the original roster and weeks."""
    rows = list(rows)
    dates = defaultdict(dict)
    for row in rows:
        day, symbol = date.fromisoformat(row["session"]), row["symbol"]
        if symbol in dates[day] or len(row["questions"]) != 1:
            raise ValueError("EJ mid-reclaim report: expected one decision per unique coordinate")
        question, = row["questions"]
        dates[day][symbol] = _validate(question)
    weeks = defaultdict(list)
    for day, plans in dates.items():
        if len(plans) != 20:
            raise ValueError("EJ mid-reclaim report: invalid roster")
        year, week, _ = day.isocalendar()
        weeks[f"{year:04d}-W{week:02d}"].append(plans.values())
    weekly = {week: _panel(days) for week, days in sorted(weeks.items())}
    totals = _panel([day for days in weeks.values() for day in days])
    frequencies = support._reference_frequencies if audit else support._frequencies
    comparison = cash_summary.summarize(rows)
    cash_reference.verify_summary(rows, comparison)
    concentration = _reference_symbol_concentration(rows, comparison) if audit else _symbol_concentration(comparison)
    return {"comparison": comparison, "execution": support.execution_summary(rows, audit=audit),
            "selection": {"totals": totals, "weekly": weekly,
                          "weekly_counts": frequencies(weekly, ("selected", "order_ready"))},
            "symbol_concentration": concentration,
            "support_scope": {"common_known": "execution.totals.common_known includes different known outcomes",
                              "paired_closed": "comparison.groups.all.totals.paired cash includes only common closes",
                              "legacy_paired_known": "comparison.groups.all.totals.paired.known_outcomes counts identical known states"}}
