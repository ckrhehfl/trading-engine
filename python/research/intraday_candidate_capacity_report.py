"""EJ exit-capacity summaries over independently audited coordinates."""

from datetime import date, datetime, timedelta, timezone
from fractions import Fraction


GROUPS = ("all", "A", "B", "other")
PERIODS = ("before_10", "10_to_12", "from_12")
KST = timezone(timedelta(hours=9))


def _entry_period(entry_at):
    clock = datetime.fromisoformat(entry_at)
    if clock.utcoffset() is None:
        raise ValueError("EJ capacity: entry clock must be timezone aware")
    hour = clock.astimezone(KST).hour
    return PERIODS[0] if hour < 10 else PERIODS[1] if hour < 12 else PERIODS[2]


def _panel(questions):
    earlier_opens = missing_opens = missing_paths = no_earlier_open = 0
    best, control_net, timer_net, paired_control = [], [], [], []
    triple_closed = triple_best_clears = triple_control_clears = triple_timer_clears = 0
    for question in questions:
        capacity = question["exit_capacity"]
        required, missing = capacity["earlier_opens"], capacity["missing_opens"]
        best_open, best_bps = capacity["best_open"], capacity["best_net_bps"]
        if not (0 <= missing <= required and (best_open is None) == (best_bps is None) == (missing > 0)):
            raise ValueError("EJ capacity: inconsistent earlier open coverage")
        earlier_opens += required
        missing_opens += missing
        missing_paths += missing > 0
        no_earlier_open += required == 0
        best_net = None if best_bps is None else Fraction(best_bps)
        if best_net is not None:
            best.append(best_net)

        baseline, candidate = question["baseline"], question["candidate"]
        if baseline["status"] != "closed" or candidate["status"] not in ("closed", "unresolved"):
            raise ValueError("EJ capacity: inconsistent activity trade states")
        control = Fraction(baseline["net"])
        control_net.append(control)
        if candidate["status"] == "closed":
            timer = Fraction(candidate["net"])
            timer_net.append(timer)
            paired_control.append(control)
            if best_net is not None:
                triple_closed += 1
                triple_best_clears += best_net > 0
                triple_control_clears += control > 0
                triple_timer_clears += timer > 0

    control_clears = sum(net > 0 for net in control_net)
    timer_clears = sum(net > 0 for net in timer_net)
    return {
        "closed_activity_trades": len(questions),
        "resolved_capacity": len(best),
        "missing_capacity": missing_paths,
        "trades_without_earlier_open": no_earlier_open,
        "required_earlier_opens": earlier_opens,
        "missing_earlier_opens": missing_opens,
        "best_open_cost_clearing": sum(net > 0 for net in best),
        "best_open_cost_equal": sum(net == 0 for net in best),
        "best_open_below_cost": sum(net < 0 for net in best),
        "mean_best_net_bps": str(sum(best, Fraction()) / len(best)) if best else None,
        "baseline": {"closed_trades": len(control_net), "cost_clearing": control_clears,
                     "net_cash": str(sum(control_net, Fraction()))},
        "candidate": {"closed_trades": len(timer_net), "unresolved_trades": len(questions) - len(timer_net),
                      "cost_clearing": timer_clears, "net_cash": str(sum(timer_net, Fraction()))},
        "common_support": {"closed_trades": len(timer_net),
                           "baseline_cost_clearing": sum(net > 0 for net in paired_control),
                           "candidate_cost_clearing": timer_clears,
                           "baseline_net_cash": str(sum(paired_control, Fraction())),
                           "candidate_net_cash": str(sum(timer_net, Fraction()))},
        "triple_common_support": {"closed_trades": triple_closed, "best_open_cost_clearing": triple_best_clears,
                                  "baseline_cost_clearing": triple_control_clears,
                                  "candidate_cost_clearing": triple_timer_clears},
    }


def _weekly_counts(counts):
    return {"maximum": max(counts, default=0), "weeks_at_least_15": sum(count >= 15 for count in counts)}


def summarize_capacity(rows):
    """Keep hindsight capacity and paired policy economics in separate counts."""
    records, weeks = [], set()
    for row in rows:
        year, week_number, _ = date.fromisoformat(row["session"]).isocalendar()
        week = f"{year:04d}-W{week_number:02d}"
        weeks.add(week)
        for question in row["questions"]:
            if question["exit_capacity"] is None:
                continue
            context = question["episode_origin_context"]
            if question["status"] != "opportunity" or context not in GROUPS[1:]:
                raise ValueError("EJ capacity: inconsistent opportunity context")
            records.append((week, context, _entry_period(question["baseline"]["entry_at"]), question))

    groups = {}
    for group in GROUPS:
        selected = [(week, period, q) for week, context, period, q in records
                    if group == "all" or context == group]
        weekly = {week: _panel([q for current, _, q in selected if current == week])
                  for week in sorted(weeks)}
        groups[group] = {
            "totals": _panel([q for _, _, q in selected]),
            "weekly": weekly,
            "by_entry_period_kst": {period: _panel([q for _, current, q in selected if current == period])
                                    for period in PERIODS},
            "zero_trade_weeks": sum(panel["closed_activity_trades"] == 0 for panel in weekly.values()),
            "weekly_counts": {
                "closed_activity_trades": _weekly_counts([p["closed_activity_trades"] for p in weekly.values()]),
                "best_open_cost_clearing": _weekly_counts([p["best_open_cost_clearing"] for p in weekly.values()]),
                "common_baseline_cost_clearing": _weekly_counts(
                    [p["common_support"]["baseline_cost_clearing"] for p in weekly.values()]),
                "common_candidate_cost_clearing": _weekly_counts(
                    [p["common_support"]["candidate_cost_clearing"] for p in weekly.values()]),
                "triple_best_open_cost_clearing": _weekly_counts(
                    [p["triple_common_support"]["best_open_cost_clearing"] for p in weekly.values()]),
                "triple_baseline_cost_clearing": _weekly_counts(
                    [p["triple_common_support"]["baseline_cost_clearing"] for p in weekly.values()]),
                "triple_candidate_cost_clearing": _weekly_counts(
                    [p["triple_common_support"]["candidate_cost_clearing"] for p in weekly.values()]),
            },
        }
    return {"weeks": len(weeks), "groups": groups}
