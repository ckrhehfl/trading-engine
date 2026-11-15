"""A fixed pre-entry price headroom mask for the saved EL first-pullback arm."""

from datetime import date
from fractions import Fraction


FAMILY = "C_FIRSTPULLBACK"
ARM = ("tcn", "common_chart_peer", "confirmation", "reference_limit_one_shot", "forecast_full_exit")
ARM_FIELDS = ("model", "scope", "control", "entry_policy", "management")
MINIMUM_BPS = 40
SCREEN_STATUSES = ("pass", "reject", "unknown", "no_commit")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decision(row):
    frame = row["frame"]
    clock = frame["decision_offset"]
    event = frame["event_id"]
    conditions = frame["conditions"]
    require(row["query_kind"] == "entry" and frame["family"] == FAMILY
            and frame["state"] == "CONFIRMED" and row["key"] == f"{event}@{clock}"
            and conditions["pullback_at"] + 2 <= frame["offset"] < clock,
            "Expected a causal first-pullback confirmation")
    peak = Fraction(frame["level"])
    reference = row["reference_close"]
    label = row["label_query"]
    require(peak > 0 and label is not None and label["decision_offset"] == clock
            and label["event_id"] == event and (reference is None) == (label["reference_close"] is None),
            "Confirmation clock or price support differs")
    status = "unknown"
    if reference is not None:
        reference = Fraction(reference)
        require(reference > 0 and reference == Fraction(label["reference_close"])
                and label["reference_offset"] < clock, "Confirmation reference differs from known close")
        status = "pass" if 10_000 * (peak - reference) >= MINIMUM_BPS * reference else "reject"
    return {"event_id": event, "session": row["session"], "symbol": row["symbol"],
            "cluster_id": frame["overlap_group_id"], "decision_offset": clock,
            "peak": str(peak), "reference": None if reference is None else str(reference), "status": status}


def joined_status(row, decisions):
    key = row["candidate_key"]
    if row["action"] == "no_commit":
        require(key is None and row["entry_decision_offset"] is None, "No-commit arm has an entry decision")
        return "no_commit"
    chosen = decisions.get(key)
    require(row["action"] == "immediate" and row["state"] == "CONFIRMED"
            and row["selection_reason"] == "fixed_confirmation" and chosen is not None
            and (row["original_episode_id"], row["session"], row["symbol"], row["cluster_id"],
                 row["entry_decision_offset"]) == tuple(chosen[field] for field in (
                 "event_id", "session", "symbol", "cluster_id", "decision_offset")),
            "Saved confirmation order differs from sealed decision")
    price = row["execution"].get("reference_close")
    require((price is None and chosen["reference"] is None)
            or (price is not None and chosen["reference"] is not None
                and Fraction(price) == Fraction(chosen["reference"])), "Saved limit differs from causal price")
    return chosen["status"]


def _empty():
    return {"commits": 0, "fills": 0, "settled_fills": 0, "unknown_fills": 0, "unknown_cash": 0,
            "gross_cash": Fraction(), "net_cash": Fraction(), "entry_notional": Fraction()}


def _add(totals, outcome, committed):
    quantity = outcome["filled_quantity"]
    totals["commits"] += int(committed)
    totals["fills"] += int(bool(quantity))
    totals["unknown_fills"] += int(quantity is None)
    if outcome["net"] is None:
        totals["unknown_cash"] += 1
        return None
    net = Fraction(outcome["net"])
    if quantity:
        totals["settled_fills"] += 1
        totals["gross_cash"] += Fraction(outcome["gross"])
        totals["net_cash"] += net
        totals["entry_notional"] += Fraction(outcome["entry_notional"])
    else:
        require(net == 0 and Fraction(outcome["gross"]) == 0, "Known no-fill cash must be zero")
    return net


def _week(day):
    iso = date.fromisoformat(day).isocalendar()
    return f"{iso.year}-W{iso.week:02}"


def summarize(calendar, decisions, ledgers):
    require(calendar and sorted(set(calendar)) == list(calendar), "Ordered unique calendar required")
    weeks = {day: _week(day) for day in calendar}
    weekly = {week: dict.fromkeys(("baseline_fills", "filtered_fills", "filtered_unknown_fills"), 0)
              for week in weeks.values()}
    baseline, filtered, screen = _empty(), _empty(), dict.fromkeys(SCREEN_STATUSES, 0)
    noop = {"filled_quantity": 0, "net": "0", "gross": "0"}
    unknown = {"filled_quantity": None, "net": None}
    paired = Fraction()
    joint_known = 0
    seen = set()
    for row in ledgers:
        event, day = row["original_episode_id"], row["session"]
        require(row["family"] == FAMILY and tuple(row[name] for name in ARM_FIELDS) == ARM
                and day in weeks and event not in seen, "Unexpected or duplicate original opportunity")
        seen.add(event)
        status = joined_status(row, decisions)
        screen[status] += 1
        original = row["outcome"]
        alternative = noop if status == "reject" else unknown if status == "unknown" else original
        left = _add(baseline, original, status != "no_commit")
        right = _add(filtered, alternative, status == "pass")
        group = weekly[weeks[day]]
        group["baseline_fills"] += int(bool(original["filled_quantity"]))
        group["filtered_fills"] += int(bool(alternative["filled_quantity"]))
        group["filtered_unknown_fills"] += int(alternative["filled_quantity"] is None)
        if left is not None and right is not None:
            joint_known += 1
            paired += right - left
    def serialize(values):
        return {key: str(value) if isinstance(value, Fraction) else value for key, value in values.items()}
    return {"opportunities": len(seen), "baseline": serialize(baseline), "filtered": serialize(filtered),
            "screen": screen, "joint_known": joint_known, "paired_net_cash": str(paired), "weekly": weekly}
