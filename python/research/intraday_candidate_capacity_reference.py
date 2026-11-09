"""Independent reconstruction of the EJ exit-capacity report from audited rows."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from typing import NamedTuple


class _Trade(NamedTuple):
    earlier: int
    missing: int
    best: Fraction | None
    control: Fraction
    timer: Fraction | None


def _cash(amounts):
    return {"closed_trades": len(amounts), "cost_clearing": sum(value > 0 for value in amounts),
            "net_cash": str(sum(amounts))}


def _statistics(trades):
    complete = [trade for trade in trades if trade.missing == 0]
    paired = [trade for trade in trades if trade.timer is not None]
    triple = [trade for trade in paired if trade.missing == 0]
    best = [trade.best for trade in complete]
    control = _cash([trade.control for trade in trades])
    timer = _cash([trade.timer for trade in paired])
    paired_control = _cash([trade.control for trade in paired])
    return {
        "closed_activity_trades": len(trades),
        "resolved_capacity": len(complete),
        "missing_capacity": len(trades) - len(complete),
        "trades_without_earlier_open": sum(trade.earlier == 0 for trade in trades),
        "required_earlier_opens": sum(trade.earlier for trade in trades),
        "missing_earlier_opens": sum(trade.missing for trade in trades),
        "best_open_cost_clearing": sum(value > 0 for value in best),
        "best_open_cost_equal": best.count(Fraction(0)),
        "best_open_below_cost": sum(value < 0 for value in best),
        "mean_best_net_bps": str(sum(best) / len(best)) if best else None,
        "baseline": control,
        "candidate": dict(timer, unresolved_trades=len(trades) - len(paired)),
        "common_support": {"closed_trades": len(paired),
                           "baseline_cost_clearing": paired_control["cost_clearing"],
                           "candidate_cost_clearing": timer["cost_clearing"],
                           "baseline_net_cash": paired_control["net_cash"],
                           "candidate_net_cash": timer["net_cash"]},
        "triple_common_support": {"closed_trades": len(triple),
                                  "best_open_cost_clearing": sum(trade.best > 0 for trade in triple),
                                  "baseline_cost_clearing": sum(trade.control > 0 for trade in triple),
                                  "candidate_cost_clearing": sum(trade.timer > 0 for trade in triple)},
    }


def _frequency(weekly):
    keys = {
        "closed_activity_trades": ("closed_activity_trades",),
        "best_open_cost_clearing": ("best_open_cost_clearing",),
        "common_baseline_cost_clearing": ("common_support", "baseline_cost_clearing"),
        "common_candidate_cost_clearing": ("common_support", "candidate_cost_clearing"),
        "triple_best_open_cost_clearing": ("triple_common_support", "best_open_cost_clearing"),
        "triple_baseline_cost_clearing": ("triple_common_support", "baseline_cost_clearing"),
        "triple_candidate_cost_clearing": ("triple_common_support", "candidate_cost_clearing"),
    }
    result = {}
    for name, path in keys.items():
        values = []
        for panel in weekly.values():
            value = panel
            for key in path:
                value = value[key]
            values.append(value)
        result[name] = {"maximum": max([0, *values]), "weeks_at_least_15": len([v for v in values if v >= 15])}
    return result


def reconstruct_capacity(rows):
    """Project each eligible trade into its overall, weekly and KST entry panels."""
    contexts, periods = ("A", "B", "other"), ("before_10", "10_to_12", "from_12")
    weeks = set()
    totals, by_week, by_period = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        calendar = date.fromisoformat(row["session"]).isocalendar()
        week = f"{calendar.year:04d}-W{calendar.week:02d}"
        weeks.add(week)
        for question in row["questions"]:
            observed = question["exit_capacity"]
            if observed is None:
                continue
            context = question["episode_origin_context"]
            baseline, timer = question["baseline"], question["candidate"]
            clock = datetime.fromisoformat(baseline["entry_at"])
            if (question["status"] != "opportunity" or context not in contexts or baseline["status"] != "closed"
                    or timer["status"] not in ("closed", "unresolved") or clock.utcoffset() is None):
                raise ValueError("EJ capacity reference: invalid trade identity or state")
            earlier, missing = observed["earlier_opens"], observed["missing_opens"]
            known = observed["best_net_bps"] is not None
            if not (0 <= missing <= earlier and known == (missing == 0)
                    and known == (observed["best_open"] is not None)):
                raise ValueError("EJ capacity reference: invalid open support")
            trade = _Trade(earlier, missing, Fraction(observed["best_net_bps"]) if known else None,
                           Fraction(baseline["net"]), Fraction(timer["net"]) if timer["status"] == "closed" else None)
            local = (clock.astimezone(timezone.utc) + timedelta(hours=9)).time()
            period = periods[(local >= time(10)) + (local >= time(12))]
            for group in ("all", context):
                totals[group].append(trade)
                by_week[group, week].append(trade)
                by_period[group, period].append(trade)

    groups = {}
    for group in ("all", *contexts):
        weekly = {week: _statistics(by_week[group, week]) for week in sorted(weeks)}
        groups[group] = {
            "totals": _statistics(totals[group]),
            "weekly": weekly,
            "by_entry_period_kst": {period: _statistics(by_period[group, period]) for period in periods},
            "zero_trade_weeks": len(weeks) - sum(bool(by_week[group, week]) for week in weeks),
            "weekly_counts": _frequency(weekly),
        }
    return {"weeks": len(weeks), "groups": groups}
