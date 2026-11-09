"""Selection and order support for the fixed late-session leader comparison."""

from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from statistics import median

from research.intraday_candidate_laggard_report import (
    ARMS, KST, ORDERS, SELECTION, _frequencies, _reference_frequencies,
)


THRESHOLD = Fraction(33, 10_000)


def _exact(value):
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("EJ late leader: expected exact fraction string")
    return Fraction(value)


def _read_day(day):
    clock = datetime.fromisoformat(day["decision_at"])
    symbols = day["symbols"]
    observed = day["observed_leaders"]
    if (clock.utcoffset() is None or len(symbols) != 20 or type(observed) is not int
            or not 0 <= observed <= 5):
        raise ValueError("EJ late leader: invalid day coverage or decision clock")
    ranks = [plan["rank"] for plan in symbols.values()]
    if any(type(rank) is not int for rank in ranks) or set(ranks) != set(range(1, 21)):
        raise ValueError("EJ late leader: expected one member per rank")

    leader_returns = []
    targets = []
    gate = day["leader_state"]
    for plan in symbols.values():
        value = _exact(plan["return"])
        baseline = plan["rank_state"]
        if plan["rank"] <= 5:
            if value is not None:
                leader_returns.append(value)
            if (baseline, plan["gated_state"], plan["order_state"]) != ("decline", "decline", "retain"):
                raise ValueError("EJ late leader: signal member cannot be a target")
            continue

        expected = "unresolved" if value is None else "select" if 0 <= value <= THRESHOLD else "decline"
        order = plan["order_state"]
        if (baseline != expected or plan["gated_state"] != (gate if expected == "select" else expected)
                or (order not in ORDERS if expected == "select" else order != "retain")):
            raise ValueError("EJ late leader: inconsistent target selection or order support")
        if order in ("ready", "decline"):
            limit, stop = _exact(plan["limit"]), _exact(plan["stop"])
            if limit is None or stop is None or not (0 < stop <= limit) or (order == "ready") != (stop < limit):
                raise ValueError("EJ late leader: inconsistent order geometry")
        targets.append(plan)

    center = median(leader_returns) if len(leader_returns) >= 4 else None
    expected_gate = "unresolved" if center is None else "select" if center > THRESHOLD else "decline"
    if (observed != len(leader_returns) or _exact(day["leader_median_return"]) != center or gate != expected_gate):
        raise ValueError("EJ late leader: inconsistent leader support or gate")
    return clock.astimezone(KST).date(), observed, gate, targets


def _empty_panel():
    return {
        "sessions": 0, "coordinates": 0, "leader_coordinates": 0, "target_coordinates": 0,
        "observed_leader_counts": dict.fromkeys(map(str, range(6)), 0),
        "quorum_supported_sessions": 0, "quorum_missing_sessions": 0,
        "leader_state_counts": dict.fromkeys(SELECTION, 0),
        "observed_target_paths": 0, "missing_target_paths": 0,
        "arms": {arm: dict.fromkeys((*SELECTION.values(), *ORDERS.values()), 0) for arm in ARMS},
    }


def _add_day(panel, observed, gate, targets):
    panel["sessions"] += 1
    panel["coordinates"] += 20
    panel["leader_coordinates"] += 5
    panel["target_coordinates"] += len(targets)
    panel["observed_leader_counts"][str(observed)] += 1
    panel["leader_state_counts"][gate] += 1
    panel["quorum_supported_sessions"] += observed >= 4
    panel["quorum_missing_sessions"] += observed < 4
    for target in targets:
        panel["observed_target_paths"] += target["return"] is not None
        panel["missing_target_paths"] += target["return"] is None
        for arm, state_key in ARMS.items():
            counts = panel["arms"][arm]
            state = target[state_key]
            counts[SELECTION[state]] += 1
            if state == "select":
                counts[ORDERS[target["order_state"]]] += 1


def _summarize(dated):
    totals, weeks = _empty_panel(), defaultdict(_empty_panel)
    for session, observed, gate, targets in dated:
        calendar = session.isocalendar()
        _add_day(totals, observed, gate, targets)
        _add_day(weeks[f"{calendar.year:04d}-W{calendar.week:02d}"], observed, gate, targets)
    weekly = dict(sorted(weeks.items()))
    return {"sessions": len(dated), "weeks": len(weekly), "totals": totals, "weekly": weekly,
            "weekly_counts": _frequencies(weekly, ("selected", "order_ready"))}


def _reference_panel(group):
    counts = [observed for _, observed, _, _ in group]
    gates = [gate for _, _, gate, _ in group]
    targets = [target for _, _, _, plans in group for target in plans]
    unknown = sum(target["return"] is None for target in targets)
    arms = {}
    for arm, key in ARMS.items():
        states = [target[key] for target in targets]
        orders = [target["order_state"] for target in targets if target[key] == "select"]
        arms[arm] = {"selected": states.count("select"), "declined": states.count("decline"),
                     "unresolved": states.count("unresolved"), "order_ready": orders.count("ready"),
                     "order_declined": orders.count("decline"), "order_unresolved": orders.count("unresolved")}
    leader_count = len(group) * 5
    return {
        "sessions": len(group), "coordinates": leader_count + len(targets), "leader_coordinates": leader_count,
        "target_coordinates": len(targets), "observed_leader_counts": {str(n): counts.count(n) for n in range(6)},
        "quorum_supported_sessions": gates.count("select") + gates.count("decline"),
        "quorum_missing_sessions": gates.count("unresolved"),
        "leader_state_counts": {state: gates.count(state) for state in SELECTION},
        "observed_target_paths": len(targets) - unknown, "missing_target_paths": unknown, "arms": arms,
    }


def _reference(dated):
    labels = ["{:04d}-W{:02d}".format(*day.isocalendar()[:2]) for day, *_ in dated]
    weekly = {label: _reference_panel([item for week, item in zip(labels, dated, strict=True) if week == label])
              for label in sorted(set(labels))}
    return {"sessions": len(dated), "weeks": len(weekly), "totals": _reference_panel(dated), "weekly": weekly,
            "weekly_counts": _reference_frequencies(weekly, ("selected", "order_ready"))}


def selection_summary(days, *, audit=False):
    """Count both policies on the original week calendar with separate leader support."""
    dated, seen = [], set()
    for day in days:
        receipt = _read_day(day)
        session = receipt[0]
        if session in seen:
            raise ValueError("EJ late leader: duplicate session")
        seen.add(session)
        dated.append(receipt)
    return _reference(dated) if audit else _summarize(dated)
