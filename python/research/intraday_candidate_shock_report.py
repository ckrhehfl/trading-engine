"""Selection support for the fixed same-stock shock and recovery comparison."""

from collections import Counter, defaultdict
from datetime import date, datetime
from fractions import Fraction

from research.intraday_candidate_laggard_report import (
    ARMS, SELECTION, _frequencies, _reference_frequencies,
)
from research.intraday_ed_inputs import MINUTE
from research.intraday_pattern_calibration import session_open


OFFSETS = (62, 122, 182)
CHECK_STATES = ("observed", "missing", "unexamined")
VOLUME_STATES = ("known", "unresolved", "not_applicable")


def _exact(value):
    if type(value) is not str:
        raise ValueError("EJ shock: expected exact fraction string")
    return Fraction(value)


def _validate_plan(day, plan):
    price, gated, order = (plan[key] for key in ("rank_state", "gated_state", "order_state"))
    unknown = plan["unknown_offsets"]
    offset = plan["decision_offset"]
    ratio = plan["volume_ratio"]
    if (price not in SELECTION or type(unknown) is not list
            or any(type(minute) is not int or minute not in OFFSETS for minute in unknown)
            or unknown != sorted(set(unknown))):
        raise ValueError("EJ shock: inconsistent price support")

    if price != "select":
        if (offset is not None or plan["decision_at"] is not None or ratio is not None
                or plan["limit"] is not None or plan["stop"] is not None
                or gated != price or order != "retain" or bool(unknown) != (price == "unresolved")):
            raise ValueError("EJ shock: inconsistent inactive selection")
        return

    if (type(offset) is not int or offset not in OFFSETS
            or any(minute >= offset for minute in unknown)):
        raise ValueError("EJ shock: inconsistent first event or earlier gaps")
    clock = datetime.fromisoformat(plan["decision_at"])
    if clock != session_open(day.isoformat()) + offset * MINUTE:
        raise ValueError("EJ shock: inconsistent decision clock")
    limit, stop = _exact(plan["limit"]), _exact(plan["stop"])
    if not 0 < stop <= limit or order != ("ready" if stop < limit else "decline"):
        raise ValueError("EJ shock: inconsistent order geometry")
    if ratio is None:
        expected_gate = "unresolved"
    else:
        volume_ratio = _exact(ratio)
        if volume_ratio < 0:
            raise ValueError("EJ shock: negative volume ratio")
        expected_gate = "select" if volume_ratio >= 2 else "decline"
    if gated != expected_gate or type(plan["volume_reason"]) is not str or not plan["volume_reason"]:
        raise ValueError("EJ shock: inconsistent volume gate")


def _empty_panel():
    return {
        "coordinates": 0, "sessions": 0,
        "arms": {arm: dict.fromkeys((*SELECTION.values(), "order_ready", "order_declined"), 0) for arm in ARMS},
        "price_checks": {str(offset): dict.fromkeys(CHECK_STATES, 0) for offset in OFFSETS},
        "first_price_events_by_offset": dict.fromkeys(map(str, OFFSETS), 0),
        "first_volume_events_by_offset": dict.fromkeys(map(str, OFFSETS), 0),
        "selected_with_earlier_unknown": 0,
        "volume_evidence": dict.fromkeys(VOLUME_STATES, 0),
        "volume_reason_counts": {},
    }


def _add_plan(panel, plan):
    panel["coordinates"] += 1
    offset, unknown = plan["decision_offset"], plan["unknown_offsets"]
    for minute in OFFSETS:
        state = "unexamined" if offset is not None and minute > offset else (
            "missing" if minute in unknown else "observed")
        panel["price_checks"][str(minute)][state] += 1

    for arm, key in ARMS.items():
        counts = panel["arms"][arm]
        state = plan[key]
        counts[SELECTION[state]] += 1
        if state == "select":
            counts["order_ready" if plan["order_state"] == "ready" else "order_declined"] += 1

    if offset is None:
        panel["volume_evidence"]["not_applicable"] += 1
        return
    panel["first_price_events_by_offset"][str(offset)] += 1
    panel["first_volume_events_by_offset"][str(offset)] += plan["gated_state"] == "select"
    panel["selected_with_earlier_unknown"] += bool(unknown)
    panel["volume_evidence"]["unresolved" if plan["volume_ratio"] is None else "known"] += 1
    reasons = panel["volume_reason_counts"]
    reason = plan["volume_reason"]
    reasons[reason] = reasons.get(reason, 0) + 1


def _summarize(dated):
    totals, weeks = _empty_panel(), defaultdict(_empty_panel)
    days, week_days = set(), defaultdict(set)
    for day, plan in dated:
        cal = day.isocalendar()
        week = f"{cal.year:04d}-W{cal.week:02d}"
        _add_plan(totals, plan)
        _add_plan(weeks[week], plan)
        days.add(day)
        week_days[week].add(day)
    totals["sessions"] = len(days)
    weekly = dict(sorted(weeks.items()))
    for week, panel in weekly.items():
        panel["sessions"] = len(week_days[week])
    return {"sessions": len(days), "weeks": len(weekly), "totals": totals, "weekly": weekly,
            "weekly_counts": _frequencies(weekly, ("selected", "order_ready"))}


def _reference_panel(group):
    plans = [plan for _, plan in group]
    price_events = [plan for plan in plans if plan["rank_state"] == "select"]
    volume_events = [plan for plan in plans if plan["gated_state"] == "select"]
    known_volume = sum(plan["volume_ratio"] is not None for plan in price_events)
    arms = {}
    for arm, key in ARMS.items():
        states = Counter(plan[key] for plan in plans)
        orders = Counter(plan["order_state"] for plan in plans if plan[key] == "select")
        arms[arm] = {"selected": states["select"], "declined": states["decline"], "unresolved": states["unresolved"],
                     "order_ready": orders["ready"], "order_declined": orders["decline"]}

    checks = {}
    for offset in OFFSETS:
        missing = sum(offset in plan["unknown_offsets"] for plan in plans)
        unexamined = sum(plan["decision_offset"] < offset for plan in price_events)
        checks[str(offset)] = {"observed": len(plans) - missing - unexamined,
                              "missing": missing, "unexamined": unexamined}
    return {
        "coordinates": len(plans), "sessions": len({day for day, _ in group}), "arms": arms,
        "price_checks": checks,
        "first_price_events_by_offset": {str(offset): sum(p["decision_offset"] == offset for p in price_events)
                                         for offset in OFFSETS},
        "first_volume_events_by_offset": {str(offset): sum(p["decision_offset"] == offset for p in volume_events)
                                          for offset in OFFSETS},
        "selected_with_earlier_unknown": sum(bool(plan["unknown_offsets"]) for plan in price_events),
        "volume_evidence": {"known": known_volume, "unresolved": len(price_events) - known_volume,
                            "not_applicable": len(plans) - len(price_events)},
        "volume_reason_counts": dict(Counter(plan["volume_reason"] for plan in price_events)),
    }


def _reference(dated):
    labels = ["{:04d}-W{:02d}".format(*day.isocalendar()[:2]) for day, _ in dated]
    weekly = {label: _reference_panel([row for week, row in zip(labels, dated, strict=True) if week == label])
              for label in sorted(set(labels))}
    totals = _reference_panel(dated)
    return {"sessions": totals["sessions"], "weeks": len(weekly), "totals": totals, "weekly": weekly,
            "weekly_counts": _reference_frequencies(weekly, ("selected", "order_ready"))}


def selection_summary(rows, *, audit=False):
    """Count observed, missing, and deliberately unexamined scheduled windows."""
    dated, seen = [], set()
    for row in rows:
        day = date.fromisoformat(row["session"])
        key = day, row["symbol"]
        if key in seen or len(row["questions"]) != 1:
            raise ValueError("EJ shock: expected one decision per unique coordinate")
        seen.add(key)
        question, = row["questions"]
        plan = question["rank_decision"]
        _validate_plan(day, plan)
        dated.append((day, plan))
    return _reference(dated) if audit else _summarize(dated)
