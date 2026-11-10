"""Opening peer support and common-known outcomes for EJ's late entry comparison."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from statistics import median

from research.intraday_candidate_laggard_report import (
    ARMS, SELECTION, _frequencies, _read_execution, _reference_frequencies,
)


THRESHOLD = Fraction(33, 10_000)
QUORUM = 10
FIELDS = ("selected", "declined", "unresolved", "order_ready", "order_declined")
ENDPOINT_FIELDS = ("opening_first_open", "opening_last_close")
ENDPOINT_PATTERNS = {(True, True): "both_observed", (False, True): "first_missing_only",
                     (True, False): "last_missing_only", (False, False): "both_missing"}


def _value(encoded):
    if encoded is not None and type(encoded) is not str:
        raise ValueError("EJ open late: expected exact fraction string")
    return None if encoded is None else Fraction(encoded)


def _opening_return(plan):
    first, last = (_value(plan[field]) for field in ENDPOINT_FIELDS)
    if any(value is not None and value <= 0 for value in (first, last)):
        raise ValueError("EJ open late: opening endpoint must be positive")
    expected = None if first is None or last is None else (last - first) / first
    if _value(plan["opening_return"]) != expected:
        raise ValueError("EJ open late: opening return differs from endpoints")
    return expected


def _endpoint_pattern(plan):
    return ENDPOINT_PATTERNS[tuple(plan[field] is not None for field in ENDPOINT_FIELDS)]


def _read_days(rows):
    by_day = defaultdict(dict)
    for row in rows:
        day, symbol = date.fromisoformat(row["session"]), row["symbol"]
        if symbol in by_day[day] or len(row["questions"]) != 1:
            raise ValueError("EJ open late: expected one decision per unique coordinate")
        by_day[day][symbol], = row["questions"]
    for day, questions in by_day.items():
        if len(questions) != 20:
            raise ValueError("EJ open late: expected 20 original members")
        opening = {symbol: _opening_return(q["rank_decision"]) for symbol, q in questions.items()}
        observed = sum(value is not None for value in opening.values())
        outcomes = []
        for symbol, q in questions.items():
            plan = q["rank_decision"]
            peers = [value for peer, value in opening.items() if peer != symbol and value is not None]
            center = median(peers) if len(peers) >= QUORUM else None
            gate = "unresolved" if center is None else "select" if center > THRESHOLD else "decline"
            if (q["observed_opening_members"] != observed or type(plan["opening_peer_count"]) is not int
                    or plan["opening_peer_count"] != len(peers)
                    or _value(plan["opening_peer_median"]) != center or plan["opening_gate"] != gate):
                raise ValueError("EJ open late: inconsistent leave-one-out support")
            late = _value(plan["late_return"])
            state = "unresolved" if late is None else "select" if late > 0 else "decline"
            order = plan["order_state"]
            if (plan["rank_state"] != state or plan["gated_state"] != (gate if state == "select" else state)
                    or order not in (("ready", "decline") if state == "select" else ("retain",))):
                raise ValueError("EJ open late: inconsistent late selection")
            outcomes.append((plan, {arm: _read_execution(q[arm]) for arm in ARMS}))
        yield day, observed, outcomes


def _empty_panel():
    return {
        "sessions": 0, "coordinates": 0,
        "arms": {arm: dict.fromkeys(FIELDS, 0) for arm in ARMS},
        "opening_endpoints": dict.fromkeys(("observed", "missing"), 0),
        "opening_endpoint_patterns": dict.fromkeys(ENDPOINT_PATTERNS.values(), 0),
        "late_paths": dict.fromkeys(("observed", "missing"), 0),
        "opening_member_counts": dict.fromkeys(map(str, range(21)), 0),
        "opening_peer_counts": dict.fromkeys(map(str, range(20)), 0),
        "opening_gate_counts": dict.fromkeys(SELECTION, 0),
        "eligible_opening_gate_counts": dict.fromkeys(SELECTION, 0),
        "common_known_by_opening_gate": {
            gate: {"coordinates": 0, "both_closed": 0, "arms": {
                arm: {"closed": 0, "cost_clearing": 0, "net_cash": Fraction()} for arm in ARMS}}
            for gate in SELECTION},
    }


def _add_day(panel, observed, outcomes):
    panel["sessions"] += 1
    panel["coordinates"] += len(outcomes)
    panel["opening_member_counts"][str(observed)] += 1
    for plan, arms in outcomes:
        panel["opening_endpoints"]["missing" if plan["opening_return"] is None else "observed"] += 1
        panel["opening_endpoint_patterns"][_endpoint_pattern(plan)] += 1
        panel["late_paths"]["missing" if plan["late_return"] is None else "observed"] += 1
        panel["opening_peer_counts"][str(plan["opening_peer_count"])] += 1
        gate = plan["opening_gate"]
        panel["opening_gate_counts"][gate] += 1
        panel["eligible_opening_gate_counts"][gate] += plan["rank_state"] == "select"
        for arm, state_key in ARMS.items():
            state = plan[state_key]
            counts = panel["arms"][arm]
            counts[SELECTION[state]] += 1
            if state == "select":
                counts["order_" + ("ready" if plan["order_state"] == "ready" else "declined")] += 1
        if any(outcome.net is None for outcome in arms.values()):
            continue
        common = panel["common_known_by_opening_gate"][gate]
        common["coordinates"] += 1
        common["both_closed"] += all(outcome.status == "closed" for outcome in arms.values())
        for arm, outcome in arms.items():
            counts = common["arms"][arm]
            counts["closed"] += outcome.status == "closed"
            counts["cost_clearing"] += outcome.status == "closed" and outcome.net > 0
            counts["net_cash"] += outcome.net


def _finish(panel):
    for common in panel["common_known_by_opening_gate"].values():
        for arm in common["arms"].values():
            arm["net_cash"] = str(arm["net_cash"])
    return panel


def _reference_panel(days):
    panel = _empty_panel()
    entries = [entry for _, _, outcomes in days for entry in outcomes]
    plans = [plan for plan, _ in entries]
    panel["sessions"], panel["coordinates"] = len(days), len(plans)
    member_counts = Counter(str(observed) for _, observed, _ in days)
    peer_counts = Counter(str(plan["opening_peer_count"]) for plan in plans)
    panel["opening_member_counts"].update(member_counts)
    panel["opening_peer_counts"].update(peer_counts)
    panel["opening_endpoint_patterns"].update(Counter(_endpoint_pattern(plan) for plan in plans))
    for group, field in (("opening_endpoints", "opening_return"), ("late_paths", "late_return")):
        observed = sum(plan[field] is not None for plan in plans)
        panel[group] = {"observed": observed, "missing": len(plans) - observed}
    for arm, key in ARMS.items():
        counts = Counter(SELECTION[plan[key]] for plan in plans)
        selected = [plan for plan in plans if plan[key] == "select"]
        ready = sum(plan["order_state"] == "ready" for plan in selected)
        panel["arms"][arm].update(counts, order_ready=ready, order_declined=len(selected) - ready)
    for gate in SELECTION:
        group = [(plan, arms) for plan, arms in entries if plan["opening_gate"] == gate]
        panel["opening_gate_counts"][gate] = len(group)
        panel["eligible_opening_gate_counts"][gate] = sum(plan["rank_state"] == "select" for plan, _ in group)
        known = [arms for _, arms in group if all(a.status != "unresolved" for a in arms.values())]
        common = panel["common_known_by_opening_gate"][gate]
        common["coordinates"] = len(known)
        common["both_closed"] = sum(arms["baseline"].status == arms["candidate"].status == "closed" for arms in known)
        for arm in ARMS:
            closed = [arms[arm].net for arms in known if arms[arm].status == "closed"]
            common["arms"][arm] = {"closed": len(closed), "cost_clearing": sum(net > 0 for net in closed),
                                  "net_cash": sum(closed, Fraction())}
    return _finish(panel)


def selection_summary(rows, *, audit=False):
    """Report all roster weeks and isolate cash on outcomes known in both arms."""
    days = list(_read_days(rows))
    if audit:
        labels = ["{:04d}-W{:02d}".format(*day.isocalendar()[:2]) for day, _, _ in days]
        weekly = {label: _reference_panel([item for week, item in zip(labels, days, strict=True) if week == label])
                  for label in sorted(set(labels))}
        totals = _reference_panel(days)
        frequencies = _reference_frequencies
    else:
        totals, weeks = _empty_panel(), defaultdict(_empty_panel)
        for day, observed, outcomes in days:
            year, week, _ = day.isocalendar()
            _add_day(totals, observed, outcomes)
            _add_day(weeks[f"{year:04d}-W{week:02d}"], observed, outcomes)
        weekly = {label: _finish(panel) for label, panel in sorted(weeks.items())}
        _finish(totals)
        frequencies = _frequencies
    return {"sessions": len(days), "weeks": len(weekly), "totals": totals, "weekly": weekly,
            "weekly_counts": frequencies(weekly, ("selected", "order_ready"))}
