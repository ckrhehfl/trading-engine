"""Independent accounting check for the frozen EL first-pullback entry screen."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction


_ARM = ("C_FIRSTPULLBACK", "tcn", "common_chart_peer", "confirmation",
        "reference_limit_one_shot", "forecast_full_exit")
_ARM_FIELDS = ("family", "model", "scope", "control", "entry_policy", "management")
_ZERO = (Fraction(), Fraction(), Fraction())


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def _exact(value):
    _check(type(value) in (str, int), "EL headroom reference requires exact prices and cash")
    return Fraction(value)


def _week(day):
    parsed = date.fromisoformat(day)
    _check(parsed.isoformat() == day, "EL headroom reference requires ISO sessions")
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02}"


@dataclass
class _Book:
    commits: int = 0
    fills: int = 0
    settled_fills: int = 0
    unknown_fills: int = 0
    unknown_cash: int = 0
    gross_cash: Fraction = Fraction()
    net_cash: Fraction = Fraction()
    entry_notional: Fraction = Fraction()

    def add(self, committed, quantity, cash):
        self.commits += committed
        self.fills += bool(quantity)
        self.unknown_fills += quantity is None
        if cash is None:
            self.unknown_cash += 1
        else:
            gross, net, notional = cash
            self.settled_fills += bool(quantity)
            self.gross_cash += gross
            self.net_cash += net
            self.entry_notional += notional

    def totals(self):
        return {key: str(value) if isinstance(value, Fraction) else value
                for key, value in vars(self).items()}


def _decision(row, decisions, classifier):
    key = row["candidate_key"]
    event = row["original_episode_id"]
    if key is None:
        _check(row["action"] == "no_commit" and row["entry_decision_offset"] is None,
               "EL headroom reference found an unkeyed entry")
        return "no_commit"

    chosen = decisions.get(key)
    _check(chosen is not None, "EL headroom reference could not join a committed entry")
    at = chosen["decision_offset"]
    _check(type(at) is int and at >= 0 and key == f"{event}@{at}" and
           (event, row["session"], row["symbol"], row["cluster_id"], row["entry_decision_offset"]) ==
           (chosen["event_id"], chosen["session"], chosen["symbol"], chosen["cluster_id"], at) and
           row["action"] == "immediate" and row["state"] == "CONFIRMED",
           "EL headroom reference found a different confirmation identity")

    execution = row["execution"]
    _check(execution["decision_offset"] == at and execution["cost_bps"] == 33,
           "EL headroom reference found a changed execution clock or cost")
    execution_reference = _exact(execution["reference_close"])
    _check(execution_reference > 0, "EL headroom reference requires a positive entry ceiling")
    reference = None if chosen["reference"] is None else _exact(chosen["reference"])
    peak = None if chosen["peak"] is None else _exact(chosen["peak"])
    _check((peak is None or peak > 0) and (reference is None or reference == execution_reference),
           "EL headroom reference found different causal prices")
    if classifier is not None:
        status = classifier(chosen)
    elif peak is not None and reference is not None:
        # Cross-multiply the exact 1.004 price multiple as an integer comparison.
        enough = (250 * peak.numerator * reference.denominator >=
                  251 * reference.numerator * peak.denominator)
        status = "pass" if enough else "reject"
    else:
        status = "unknown"
    _check(status == chosen["status"], "EL headroom reference found a different screen decision")
    return status


def _cash(row):
    outcome, execution = row["outcome"], row["execution"]
    quantity = outcome["filled_quantity"]
    _check(quantity is None or type(quantity) is int and quantity >= 0,
           "EL headroom reference found an invalid fill quantity")
    _check((outcome["net"] is None) == (outcome["gross"] is None),
           "EL headroom reference found one-sided unknown cash")
    if outcome["net"] is None:
        if quantity:
            _check(execution["filled_quantity"] == quantity and
                   quantity * _exact(execution["entry_price"]) == _exact(outcome["entry_notional"]) > 0,
                   "EL headroom reference found mismatched notional for an unresolved position")
        return quantity, None
    if quantity == 0:
        _check(execution["filled_quantity"] == 0 and not execution["sales"] and
               all(_exact(outcome[name]) == 0 for name in ("gross", "net", "entry_notional")),
               "EL headroom reference found cash on a known no-fill")
        return 0, _ZERO

    _check(quantity is not None and execution["filled_quantity"] == quantity and
           execution["remaining_quantity"] == 0, "EL headroom reference requires settled quantities")
    purchase = _exact(execution["entry_price"])
    basis = purchase * quantity
    sales = execution["sales"]
    _check(0 < purchase <= _exact(execution["reference_close"]) and
           basis == _exact(outcome["entry_notional"]) == _exact(execution["entry_notional"]) and
           all(type(leg["quantity"]) is int and leg["quantity"] > 0 for leg in sales) and
           sum(leg["quantity"] for leg in sales) == quantity,
           "EL headroom reference found mismatched settlement quantities or prices")
    proceeds = sum((_exact(leg["price"]) * leg["quantity"] for leg in sales), Fraction())
    gross = proceeds - basis
    net = gross - basis * 33 / 10_000
    _check(gross == _exact(outcome["gross"]) and net == _exact(outcome["net"]),
           "EL headroom reference reconstruction differs from audited cash")
    return quantity, (gross, net, basis)


def verify(calendar, decisions, ledgers, report, *, classifier=None):
    """Reconstruct the exact paired totals from independently audited cash legs."""
    calendar = tuple(calendar)
    _check(calendar and tuple(sorted(set(calendar))) == calendar,
           "EL headroom reference requires the complete ordered calendar")
    by_day = {day: _week(day) for day in calendar}
    weekly = dict((week, {"baseline_fills": 0, "filtered_fills": 0, "filtered_unknown_fills": 0})
                  for week in by_day.values())
    baseline, filtered = _Book(), _Book()
    screen = dict.fromkeys(("pass", "reject", "unknown", "no_commit"), 0)
    paired_net = Fraction()
    joint_known = 0
    events = set()
    for row in ledgers:
        event, day = row["original_episode_id"], row["session"]
        _check(tuple(row[field] for field in _ARM_FIELDS) == _ARM and event not in events and day in by_day,
               "EL headroom reference found a duplicate, off-calendar or wrong-arm row")
        events.add(event)
        status = _decision(row, decisions, classifier)
        quantity, cash = _cash(row)
        _check(status != "no_commit" or (quantity, cash) == (0, _ZERO),
               "EL headroom reference found a trade without a commitment")
        screen[status] += 1
        baseline.add(status != "no_commit", quantity, cash)
        if status == "reject":
            filtered_quantity, filtered_cash = 0, _ZERO
        elif status == "unknown":
            filtered_quantity, filtered_cash = None, None
        else:
            filtered_quantity, filtered_cash = quantity, cash
        filtered.add(status == "pass", filtered_quantity, filtered_cash)
        counts = weekly[by_day[day]]
        counts["baseline_fills"] += bool(quantity)
        counts["filtered_fills"] += bool(filtered_quantity)
        counts["filtered_unknown_fills"] += filtered_quantity is None
        if cash is not None and filtered_cash is not None:
            joint_known += 1
            paired_net += filtered_cash[1] - cash[1]

    totals = {"opportunities": len(events), "baseline": baseline.totals(), "filtered": filtered.totals(),
              "screen": screen, "joint_known": joint_known, "paired_net_cash": str(paired_net), "weekly": weekly}
    _check(totals == report["totals"], "EL headroom reference differs from the producer report")
    return totals
