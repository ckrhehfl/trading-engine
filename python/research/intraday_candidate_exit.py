"""Pure EJ Batch 1 matched exits; no reader, order, model, or account state."""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from statistics import median

from research.intraday_ed_inputs import Minute, SessionInputs


MINUTE = timedelta(minutes=1)
COST = Fraction(33, 10_000)
THRESHOLD = Fraction(5033, 5000)
GROUPS = ("all", "A", "B", "A_or_B", "other")
STATES = ("closed", "no_entry", "no_fill", "unsupported", "unresolved")


def _require(condition, reason):
    if not condition:
        raise ValueError(f"EJ exit: {reason}")


def _number(value):
    _require(type(value) is str, "exact saved amount required")
    return Fraction(value)


def _minute(session, at):
    item = session.minutes.get(at)
    if not isinstance(item, Minute) or item.symbol != session.symbol or item.start_at != at:
        return None
    if item.price_state != "observed" or item.available_at != at + 3 * MINUTE:
        return None
    prices = item.ohlc
    if (type(prices) is not tuple or len(prices) != 4
            or not all(isinstance(p, Decimal) and p.is_finite() and p > 0 for p in prices)):
        return None
    op, high, low, close = prices
    return prices if low <= min(op, close) <= max(op, close) <= high else None


def _retained(arm):
    return {**{key: arm[key] for key in ("status", "gross", "cost", "net", "entry_notional")},
            "selected_trigger": "retained", "entry_at": None, "request_at": None,
            "exit_at": None, "entry_price": None, "exit_price": None, "quantity": None,
            "trigger_start_at": None, "trigger_close": None, "issue": None}


def _unknown(result, issue):
    result.update(status="unresolved", gross=None, cost=None, net=None, entry_notional=None,
                  issue=issue)
    if issue in ("missing_session", "entry_price_unknown", "first_trigger_unknown"):
        result.update(selected_trigger="unknown", request_at=None, exit_at=None, exit_price=None)
    return result


def _replay(question, symbol, opens, session):
    arm = question["baseline"]
    result = _retained(arm)
    sale, = arm["holding"]["realized_exits"]
    request, execution = arm["holding"]["events"]
    entry = datetime.fromisoformat(question["decision_at"]) + MINUTE
    original_request = datetime.fromisoformat(request["at"])
    original_exit = datetime.fromisoformat(execution["at"])
    quantity = sale["quantity"]
    _require(type(quantity) is int and quantity > 0 and arm["fill"]["quantity"] == quantity
             and execution["quantity"] == quantity and sale["symbol"] == symbol,
             "original integer quantity differs")
    _require(entry.tzinfo is not None and original_request + MINUTE == original_exit
             and entry < original_exit <= entry + 30 * MINUTE
             and request["execute_at"] == execution["at"]
             and request["action"] == execution["action"] == "exit"
             and arm["holding"]["status"] == "closed" and arm["fill"]["outcome"] == "modeled"
             and execution["fill_status"] == "modeled", "original exit geometry differs")
    entry_price = _number(arm["entry_notional"]) / quantity
    basis, cost = quantity * entry_price, quantity * entry_price * COST
    saved_entry, issue = opens[entry.isoformat()]
    saved_exit, exit_issue = opens[original_exit.isoformat()]
    original_price = _number(execution["open_price"])
    _require(issue is None and exit_issue is None and entry_price > 0 and original_price > 0
             and _number(saved_entry) == entry_price and _number(saved_exit) == original_price
             and _number(sale["entry_basis"]) == basis
             and _number(sale["gross_proceeds"]) == quantity * original_price
             and _number(sale["allocated_entry_cost"]) == _number(arm["fill"]["cost"]) == cost
             and tuple(_number(arm[key]) for key in ("gross", "cost", "net"))
             == (quantity * original_price - basis, cost, quantity * original_price - basis - cost),
             "original saved cash or price differs")
    result.update(selected_trigger="original", entry_at=entry.isoformat(),
                  request_at=original_request.isoformat(), exit_at=original_exit.isoformat(),
                  entry_price=str(entry_price), exit_price=str(original_price), quantity=quantity)
    if session is None:
        return _unknown(result, "missing_session")
    _require(isinstance(session, SessionInputs) and session.symbol == symbol
             and session.opening.date() == entry.date()
             and session.opening <= entry < original_exit <= session.closing,
             "session identity or bounds differ")
    entry_bar = _minute(session, entry)
    if entry_bar is None:
        return _unknown(result, "entry_price_unknown")
    _require(Fraction(entry_bar[0]) == entry_price, "archived entry price differs")

    start = entry
    while start + 3 * MINUTE < original_request:
        prices = _minute(session, start)
        if prices is None:
            return _unknown(result, "first_trigger_unknown")
        if Fraction(prices[3]) >= entry_price * THRESHOLD:
            requested = start + 3 * MINUTE
            result.update(selected_trigger="profit66", request_at=requested.isoformat(),
                          exit_at=(requested + MINUTE).isoformat(),
                          trigger_start_at=start.isoformat(), trigger_close=str(Fraction(prices[3])))
            break
        start += MINUTE
    exit_at = datetime.fromisoformat(result["exit_at"])
    _require(exit_at <= original_exit, "candidate exceeded original expiry")
    exit_bar = _minute(session, exit_at)
    if exit_bar is None:
        result["exit_price"] = None
        return _unknown(result, "exit_price_unknown")
    exit_price = Fraction(exit_bar[0])
    if result["selected_trigger"] == "original":
        _require(exit_price == original_price, "archived original exit price differs")
    gross = quantity * exit_price - basis
    result.update(gross=str(gross), cost=str(cost), net=str(gross - cost),
                  entry_notional=str(basis), exit_price=str(exit_price))
    return result


def evaluate_coordinate(row, roster, read_session):
    """Retain every original field; add an exit arm without changing reservations.

    The callback receives (ISO session date, symbol), at most once and only
    when an opportunity has an originally closed holding. Unknown old holdings
    are never reconstructed into a newly known matched comparison.
    """
    _require((row["session"], row["symbol"], row["original_frames"], row["coordinate_status"])
             == (roster["session"], roster["symbol"], roster["original_frames"], roster["coordinate_status"])
             and row["entry_independently_verified"] is True and row["fold"] in range(1, 6),
             "original coordinate or receipt differs")
    opens = {at: (price, issue) for at, price, issue in row["modeled_opens"]}
    _require(len(opens) == len(row["modeled_opens"]), "duplicate saved open")
    output = deepcopy(row)
    output["roster"] = deepcopy(roster)
    loaded, session = False, None
    for question in output["questions"]:
        _require("candidate" not in question, "candidate already exists")
        if question["status"] == "opportunity" and question["baseline"]["status"] == "closed":
            if not loaded:
                session, loaded = read_session(row["session"], row["symbol"]), True
            question["candidate"] = _replay(question, row["symbol"], opens, session)
        else:
            question["candidate"] = _retained(question["baseline"])
    return output


def _stats(arms):
    counts = Counter(arm["status"] for arm in arms)
    _require(set(counts) <= set(STATES), "unexpected opportunity state")
    closed = [arm for arm in arms if arm["status"] == "closed"]
    gross = [_number(arm["gross"]) for arm in closed]
    net = [_number(arm["net"]) for arm in closed]
    bases = [_number(arm["entry_notional"]) for arm in closed]
    costs = [_number(arm["cost"]) for arm in closed]
    _require(all(b > 0 and c == b * COST and n == g - c
                 for b, c, n, g in zip(bases, costs, net, gross, strict=True)), "closed accounting differs")
    returns = {"gross": [g / b for g, b in zip(gross, bases, strict=True)],
               "net": [n / b for n, b in zip(net, bases, strict=True)]}
    losses = -sum((n for n in net if n < 0), Fraction())
    return {"status_counts": {key: counts[key] for key in STATES}, "closed_trades": len(closed),
            "known_outcomes": sum(counts[key] for key in ("closed", "no_entry", "no_fill")),
            "cost_clearing": sum(n > 0 for n in net), "cost_equal": sum(n == 0 for n in net),
            "below_cost": sum(n < 0 for n in net), "gross_cash": str(sum(gross, Fraction())),
            "cost_cash": str(sum(costs, Fraction())), "net_cash": str(sum(net, Fraction())),
            "profit_factor": str(sum((n for n in net if n > 0), Fraction()) / losses) if losses else None,
            **{f"{measure}_{kind}_return": str(sum(values, Fraction()) / len(values)
                                               if measure == "mean" else median(values)) if values else None
               for kind, values in returns.items() for measure in ("mean", "median")}}


def _panel(questions):
    paired = [q for q in questions if q["baseline"]["status"] == q["candidate"]["status"] == "closed"]
    old = _stats([q["baseline"] for q in paired])
    new = _stats([q["candidate"] for q in paired])
    return {"opportunities": len(questions),
            "original_modeled_entries": sum((q["baseline"].get("fill") or {}).get("outcome") == "modeled"
                                             for q in questions),
            "baseline": _stats([q["baseline"] for q in questions]),
            "candidate": _stats([q["candidate"] for q in questions]),
            "newly_unresolved": sum(q["baseline"]["status"] == "closed"
                                     and q["candidate"]["status"] == "unresolved" for q in questions),
            "selected_triggers": dict(sorted(Counter(q["candidate"]["selected_trigger"] for q in questions).items())),
            "paired": {"known_outcomes": len(paired) + sum(q["baseline"]["status"] in ("no_entry", "no_fill")
                                                            and q["candidate"]["status"] == q["baseline"]["status"]
                                                            for q in questions),
                       "closed_trades": len(paired), "baseline": old, "candidate": new,
                       "delta_net_cash": str(Fraction(new["net_cash"]) - Fraction(old["net_cash"])),
                       "mean_delta_net_return": str(Fraction(new["mean_net_return"]) - Fraction(old["mean_net_return"]))
                       if paired else None}}


def summarize(rows):
    """Exact event economics, zero-trade weeks, and common-supported comparisons."""
    rows = list(rows)
    coordinates = [(row["session"], row["symbol"]) for row in rows]
    _require(len(set(coordinates)) == len(coordinates), "duplicate coordinate")
    questions, frames = Counter(), Counter()
    records, weeks = [], set()
    for row in rows:
        year, week, _ = date.fromisoformat(row["session"]).isocalendar()
        week = f"{year:04d}-W{week:02d}"
        weeks.add(week)
        frames.update(dict(row["roster"]["frame_states"]))
        for q in row["questions"]:
            questions[q["status"]] += 1
            if q["status"] == "opportunity":
                _require(q["episode_origin_context"] in ("A", "B", "other"), "unknown context")
                records.append((row, week, q))
    groups = {}
    for group in GROUPS:
        selected = [(row, week, q) for row, week, q in records
                    if group == "all" or q["episode_origin_context"] == group
                    or group == "A_or_B" and q["episode_origin_context"] in ("A", "B")]
        weekly = {week: _panel([q for _, w, q in selected if w == week]) for week in sorted(weeks)}
        groups[group] = {"totals": _panel([q for _, _, q in selected]), "weekly": weekly,
                         "baseline_weeks_at_least_15": sum(p["baseline"]["cost_clearing"] >= 15 for p in weekly.values()),
                         "candidate_weeks_at_least_15": sum(p["candidate"]["cost_clearing"] >= 15 for p in weekly.values())}
    partitions = {key: {str(value): _panel([q for row, _, q in records if row[key] == value])
                        for value in (range(1, 6) if key == "fold" else sorted({row[key] for row in rows}))}
                  for key in ("fold", "session", "symbol")}
    concentration = {}
    for arm in ("baseline", "candidate"):
        sessions = {day: Fraction(panel[arm]["net_cash"]) for day, panel in partitions["session"].items()}
        positive = {day: value for day, value in sessions.items() if value > 0}
        largest = max(positive, key=positive.get) if positive else None
        values = list(sessions.values())
        mean = sum(values, Fraction()) / len(values) if values else None
        concentration[arm] = {"independent_sessions": len(values), "largest_positive_session": largest,
                              "net_cash_without_largest_positive_session": str(sum(values, Fraction()) - positive.get(largest, 0)),
                              "mean_session_net_cash": str(mean) if mean is not None else None,
                              "sample_variance_session_net_cash": str(sum(((v - mean) ** 2 for v in values), Fraction())
                                                                       / (len(values) - 1)) if len(values) > 1 else None}
    return {"coordinates": len(rows), "sessions": len({row["session"] for row in rows}), "weeks": len(weeks),
            "frames": sum(frames.values()), "frame_states": dict(frames), "question_states": dict(questions),
            "groups": groups, "folds": partitions["fold"], "by_session": partitions["session"],
            "by_symbol": partitions["symbol"], "session_concentration": concentration,
            "cash_scope": "known closed event cash; not account NAV; paired means use common closed trades"}
