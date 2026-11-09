"""Selection and pre-entry order support for EJ's two morning rank policies."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction


KST = timezone(timedelta(hours=9))
ARMS = {"baseline": "rank_state", "candidate": "gated_state"}
SELECTION = {"select": "selected", "decline": "declined", "unresolved": "unresolved"}
ORDERS = {"ready": "order_ready", "decline": "order_declined", "unresolved": "order_unresolved"}
SIGNS = ("negative", "zero", "positive")


def _read_day(day):
    clock = datetime.fromisoformat(day["decision_at"])
    observed, encoded_median, symbols = day["observed"], day["median_return"], day["symbols"]
    if (clock.utcoffset() is None or type(observed) is not int or not 0 <= observed <= 20
            or len(symbols) != 20 or (encoded_median is None) != (observed < 10)):
        raise ValueError("EJ laggard: invalid day coverage or decision clock")
    if encoded_median is not None and type(encoded_median) is not str:
        raise ValueError("EJ laggard: median must be an exact fraction string")
    median = None if encoded_median is None else Fraction(encoded_median)
    observed_returns = 0
    for plan in symbols.values():
        available = plan["return"] is not None
        observed_returns += available
        rank, gated, order = plan["rank_state"], plan["gated_state"], plan["order_state"]
        if rank not in SELECTION or (rank == "unresolved") != (median is None or not available):
            raise ValueError("EJ laggard: inconsistent rank support")
        expected_gate = "decline" if rank == "select" and median < 0 else rank
        if gated != expected_gate or (order not in ORDERS if rank == "select" else order != "retain"):
            raise ValueError("EJ laggard: inconsistent gated selection or order support")
    if observed_returns != observed:
        raise ValueError("EJ laggard: endpoint count differs from symbol support")
    return clock.astimezone(KST).date(), median


def _empty_panel():
    return {"sessions": 0, "coordinates": 0, "observed_endpoints": 0, "missing_endpoints": 0,
            "quorum_supported_sessions": 0, "quorum_missing_sessions": 0, "median_sign_counts": dict.fromkeys(SIGNS, 0),
            "arms": {arm: dict.fromkeys((*SELECTION.values(), *ORDERS.values()), 0) for arm in ARMS}}


def _add_day(panel, day, median):
    panel["sessions"] += 1
    panel["coordinates"] += len(day["symbols"])
    panel["observed_endpoints"] += day["observed"]
    panel["missing_endpoints"] += len(day["symbols"]) - day["observed"]
    if median is None:
        panel["quorum_missing_sessions"] += 1
    else:
        panel["quorum_supported_sessions"] += 1
        panel["median_sign_counts"]["positive" if median > 0 else "negative" if median < 0 else "zero"] += 1
    for plan in day["symbols"].values():
        for arm, state_key in ARMS.items():
            counts = panel["arms"][arm]
            state = plan[state_key]
            counts[SELECTION[state]] += 1
            if state == "select":
                counts[ORDERS[plan["order_state"]]] += 1


def _summarize(dated):
    totals, weeks = _empty_panel(), defaultdict(_empty_panel)
    for session, day, median in dated:
        calendar = session.isocalendar()
        _add_day(totals, day, median)
        _add_day(weeks[f"{calendar.year:04d}-W{calendar.week:02d}"], day, median)
    weekly = dict(sorted(weeks.items()))
    frequency = {}
    for arm in ARMS:
        frequency[arm] = {}
        for field in ("selected", "order_ready"):
            counts = [panel["arms"][arm][field] for panel in weekly.values()]
            frequency[arm][field] = {"maximum": max(counts, default=0), "zero_weeks": counts.count(0),
                                     "weeks_at_least_15": sum(count >= 15 for count in counts)}
    return {"sessions": len(dated), "weeks": len(weekly), "totals": totals, "weekly": weekly, "weekly_counts": frequency}


def _reference_panel(group):
    medians = [median for _, _, median in group if median is not None]
    plans = [plan for _, day, _ in group for plan in day["symbols"].values()]
    missing = sum(plan["return"] is None for plan in plans)
    arms = {}
    for arm, state_key in ARMS.items():
        states = [plan[state_key] for plan in plans]
        orders = [plan["order_state"] for plan in plans if plan[state_key] == "select"]
        arms[arm] = {"selected": states.count("select"), "declined": states.count("decline"),
                     "unresolved": states.count("unresolved"), "order_ready": orders.count("ready"),
                     "order_declined": orders.count("decline"), "order_unresolved": orders.count("unresolved")}
    return {"sessions": len(group), "coordinates": len(plans), "observed_endpoints": len(plans) - missing,
            "missing_endpoints": missing, "quorum_supported_sessions": len(medians),
            "quorum_missing_sessions": len(group) - len(medians), "arms": arms,
            "median_sign_counts": {"negative": sum(value < 0 for value in medians), "zero": medians.count(Fraction()),
                                   "positive": sum(value > 0 for value in medians)}}


def _reference(dated):
    labels = ["{:04d}-W{:02d}".format(*session.isocalendar()[:2]) for session, _, _ in dated]
    weekly = {label: _reference_panel([row for week, row in zip(labels, dated, strict=True) if week == label])
              for label in sorted(set(labels))}
    frequency = {}
    for arm in ARMS:
        frequency[arm] = {}
        for field in ("selected", "order_ready"):
            counts = sorted(panel["arms"][arm][field] for panel in weekly.values())
            frequency[arm][field] = {"maximum": counts[-1] if counts else 0,
                                     "zero_weeks": sum(count < 1 for count in counts),
                                     "weeks_at_least_15": len(counts) - sum(count < 15 for count in counts)}
    return {"sessions": len(dated), "weeks": len(weekly), "totals": _reference_panel(dated), "weekly": weekly,
            "weekly_counts": frequency}


def selection_summary(days, *, audit=False):
    """Include every roster day and count geometry readiness only for selected arms."""
    dated, seen = [], set()
    for day in days:
        session, median = _read_day(day)
        if session in seen:
            raise ValueError("EJ laggard: duplicate session")
        seen.add(session)
        dated.append((session, day, median))
    return _reference(dated) if audit else _summarize(dated)
