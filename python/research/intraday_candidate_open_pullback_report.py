"""Reconcile opening pullback eligibility and exact event cash."""

from collections import defaultdict
from datetime import date
from fractions import Fraction

from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_report as support
from research.intraday_candidate_laggard_peers_report import _reference_symbol_concentration, _symbol_concentration


_OWN_PRICES = ("first_open", "mid_close", "last_close")
_REBOUND_PRICE = "rebound_reference_close"


def _exact(value):
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("EJ pullback report: expected exact fraction string")
    return Fraction(value)


def _decisions(plan):
    first, mid, last, reference = (_exact(plan[key]) for key in (*_OWN_PRICES, _REBOUND_PRICE))
    if any(price is not None and price <= 0 for price in (first, mid, last, reference)):
        raise ValueError("EJ pullback report: invalid price evidence")
    if None in (first, mid, last):
        own = "unresolved"
    elif (mid - first) * 10_000 >= 66 * first and last < mid and 2 * last >= first + mid:
        own = "select"
    else:
        own = "decline"
    if own == "select":
        rebound = "unresolved" if reference is None else "select" if last > reference else "decline"
    else:
        if reference is not None:
            raise ValueError("EJ pullback report: rebound observed outside own eligibility")
        rebound = "not_applicable"
    gated = rebound if own == "select" else own
    if (plan["rank_state"], plan["rebound_state"], plan["gated_state"]) != (own, rebound, gated):
        raise ValueError("EJ pullback report: own or rebound decision differs")

    limit, stop, order = _exact(plan["limit"]), _exact(plan["stop"]), plan["order_state"]
    if own != "select":
        valid = (order, limit, stop) == ("retain", None, None)
    else:
        valid = limit == last and (
            (order == "unresolved" and stop is None)
            or (reference is not None and order == "decline" and stop == limit)
            or (reference is not None and order == "ready" and stop is not None and 0 < stop < limit)
        )
    if not valid:
        raise ValueError("EJ pullback report: entry geometry differs")
    return own, gated


def _validate(question):
    plan = question["rank_decision"]
    own, gated = _decisions(plan)
    baseline, candidate = (support._read_execution(question[arm]) for arm in ("baseline", "candidate"))
    order = plan["order_state"]
    if own == "unresolved" or order == "unresolved":
        valid_baseline = baseline.status == "unresolved" and not baseline.requested and not baseline.modeled
    elif own == "decline" or order == "decline":
        valid_baseline = baseline.status == "no_entry" and not baseline.requested
    else:
        valid_baseline = baseline.status != "no_entry"
    if gated == own:
        valid_candidate = question["candidate"] == question["baseline"]
    elif gated == "decline":
        valid_candidate = candidate.status == "no_entry" and not candidate.requested
    else:
        valid_candidate = candidate.status == "unresolved" and not candidate.requested and not candidate.modeled
    if not valid_baseline or not valid_candidate:
        raise ValueError("EJ pullback report: projected outcome differs from the frozen decision")
    return plan


def _panel(days):
    plans = [plan for members in days for plan in members]
    result = {"sessions": len(days), "coordinates": len(plans),
              "own_endpoints_missing": {key: sum(plan[key] is None for plan in plans) for key in _OWN_PRICES},
              "eligible_rebound_states": {state: sum(plan["rebound_state"] == state for plan in plans)
                                          for state in support.SELECTION},
              "arms": {}}
    for arm, key in (("baseline", "rank_state"), ("candidate", "gated_state")):
        counts = {name: sum(plan[key] == state for plan in plans) for state, name in support.SELECTION.items()}
        counts.update({f"order_{state}": sum(plan[key] == "select" and plan["order_state"] == state for plan in plans)
                       for state in ("ready", "decline", "unresolved")})
        result["arms"][arm] = counts
    return result


def summarize(rows, *, audit=False):
    """Verify frozen decisions and keep all coordinates and original weeks."""
    rows = list(rows)
    dates = defaultdict(dict)
    for row in rows:
        day, symbol = date.fromisoformat(row["session"]), row["symbol"]
        if symbol in dates[day] or len(row["questions"]) != 1:
            raise ValueError("EJ pullback report: expected one decision per unique coordinate")
        question, = row["questions"]
        dates[day][symbol] = _validate(question)
    weeks = defaultdict(list)
    for day, plans in dates.items():
        if len(plans) != 20:
            raise ValueError("EJ pullback report: invalid roster")
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
