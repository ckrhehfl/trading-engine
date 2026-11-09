"""Independent verification of saved EI cost-crossing frequencies."""

from collections import Counter
from datetime import date, datetime, timedelta
from fractions import Fraction
import json


_OUTCOMES = ("above", "equal", "below", "no_entry", "no_fill", "unsupported", "unresolved")
_GROUPS = {
    "all": ("A", "B", "other"),
    "A": ("A",),
    "B": ("B",),
    "A_or_B": ("A", "B"),
    "other": ("other",),
}
_FRAME_STATES = {"no_anchor", "unknown", "invalidated", "no_event", "questions"}
_QUESTION_STATES = {"opportunity", "feature_unsupported", "question_unsupported", "overlap_suppressed"}
_COST = Fraction(33, 10_000)
_MINUTE = timedelta(minutes=1)


def _require(valid, reason):
    if not valid:
        raise ValueError(f"EI cost census reference: {reason}")


def _amount(value):
    _require(type(value) is str, "exact saved cash amount required")
    return Fraction(value)


def _open_price(opens, at):
    price, issue = opens[at]
    _require(issue is None and _amount(price) > 0, "modeled execution open is unknown or invalid")
    return _amount(price)


def _outcome(question, symbol, opens):
    arm = question["baseline"]
    status = arm["status"]
    fill, holding = arm["fill"], arm["holding"]
    cash = tuple(arm[key] for key in ("gross", "cost", "net", "entry_notional"))
    if status in ("no_entry", "no_fill"):
        _require(cash == ("0",) * 4 and holding is None, "nontrade carries realized cash")
        _require(fill is None if status == "no_entry" else fill["outcome"] == "declined",
                 "nontrade fill state differs")
        return status
    if status in ("unresolved", "unsupported"):
        _require(cash == (None,) * 4, "unknown outcome carries realized cash")
        _require(holding is None or status == "unresolved" and holding["status"] == "unresolved",
                 "unknown outcome carries resolved holding")
        return status
    _require(status == "closed" and holding["status"] == "closed" and fill["outcome"] == "modeled",
             "closed modeled trade required")
    sale, = holding["realized_exits"]
    request, execution = holding["events"]
    quantity = sale["quantity"]
    _require(type(quantity) is int and quantity > 0 and fill["quantity"] == quantity
             and sale["symbol"] == symbol, "closed sale quantity or symbol differs")
    entry_at = datetime.fromisoformat(question["decision_at"]) + _MINUTE
    exit_at = datetime.fromisoformat(execution["at"])
    _require(request["action"] == execution["action"] == "exit"
             and request["execute_at"] == execution["at"]
             and datetime.fromisoformat(request["at"]) + _MINUTE == exit_at
             and entry_at < exit_at <= entry_at + 30 * _MINUTE
             and execution["fill_status"] == "modeled" and execution["quantity"] == quantity,
             "fixed exit request or receipt differs")

    basis = quantity * _open_price(opens, entry_at.isoformat())
    exit_price = _open_price(opens, execution["at"])
    proceeds = quantity * exit_price
    fee = basis * _COST
    _require(_amount(execution["open_price"]) == exit_price
             and tuple(_amount(sale[key]) for key in ("entry_basis", "gross_proceeds", "allocated_entry_cost"))
             == (basis, proceeds, fee) and _amount(fill["cost"]) == fee, "saved open or sale cash differs")
    gross = proceeds - basis
    net = gross - fee
    _require(tuple(map(_amount, cash)) == (gross, fee, net, basis), "baseline aggregate cash differs")
    return "above" if net > 0 else "below" if net < 0 else "equal"


def verify(rows, report):
    """Rebuild cash-derived outcomes and week counts from independently saved legs."""
    coordinates, dates, weeks = set(), set(), set()
    frames, questions, observed = Counter(), Counter(), Counter()
    for row, roster in rows:
        day, symbol = row["session"], row["symbol"]
        parsed = date.fromisoformat(day)
        _require(day == parsed.isoformat() and type(symbol) is str and bool(symbol)
                 and (day, symbol) not in coordinates and row["entry_independently_verified"] is True,
                 "coordinate identity, uniqueness or independent receipt differs")
        coordinates.add((day, symbol))
        dates.add(day)
        calendar = parsed.isocalendar()
        week = f"{calendar.year:04d}-W{calendar.week:02d}"
        weeks.add(week)
        frame_states = dict(roster["frame_states"])
        _require((roster["session"], roster["symbol"]) == (day, symbol)
                 and len(frame_states) == len(roster["frame_states"])
                 and frame_states.keys() <= _FRAME_STATES
                 and all(type(count) is int and count > 0 for count in frame_states.values())
                 and type(row["original_frames"]) is int and type(roster["original_frames"]) is int
                 and sum(frame_states.values()) == roster["original_frames"] == row["original_frames"],
                 "sealed decision frame states differ")
        frames.update(frame_states)
        opens = {at: (price, issue) for at, price, issue in row["modeled_opens"]}
        _require(len(opens) == len(row["modeled_opens"]), "duplicate modeled open")
        seen = set()
        for question in row["questions"]:
            identity, state = question["question_sha256"], question["status"]
            _require(identity not in seen and state in _QUESTION_STATES, "question identity or state differs")
            seen.add(identity)
            questions[state] += 1
            if state == "opportunity":
                origin = question["episode_origin_context"]
                _require(origin in _GROUPS["all"], "unknown frozen opportunity origin")
                observed[week, origin, _outcome(question, symbol, opens)] += 1

    groups = {}
    for group, origins in _GROUPS.items():
        weekly = {week: {status: sum(observed[week, origin, status] for origin in origins)
                         for status in _OUTCOMES} for week in sorted(weeks)}
        groups[group] = {
            "totals": {status: sum(row[status] for row in weekly.values()) for status in _OUTCOMES},
            "weekly": weekly,
            "weeks_at_least_15": sum(row["above"] >= 15 for row in weekly.values()),
            "weeks_at_least_15_if_unresolved_clear": sum(
                row["above"] + row["unsupported"] + row["unresolved"] >= 15 for row in weekly.values()),
        }
    expected = {"coordinates": len(coordinates), "sessions": len(dates), "weeks": len(weeks),
                "frames": sum(frames.values()), "frame_states": dict(frames),
                "question_states": dict(questions), "groups": groups}
    _require(json.dumps(report, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True),
             "published cost census differs")
