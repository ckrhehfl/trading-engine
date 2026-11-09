"""Producer-independent EJ exit and report reconstruction from original inputs."""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import json

from research.intraday_ed_inputs import Minute, SessionInputs


def _check(condition, message):
    if not condition:
        raise ValueError(f"EJ reference: {message}")


def _same(left, right, message):
    _check(json.dumps(left, sort_keys=True, allow_nan=False)
           == json.dumps(right, sort_keys=True, allow_nan=False), message)


def _quote(session, at):
    if session is None:
        return None
    minute = session.minutes.get(at)
    if not isinstance(minute, Minute):
        return None
    if (minute.symbol != session.symbol or minute.start_at != at or minute.price_state != "observed"
            or minute.available_at - at != timedelta(minutes=3)):
        return None
    if not isinstance(minute.ohlc, tuple) or len(minute.ohlc) != 4:
        return None
    if any(not isinstance(p, Decimal) or not p.is_finite() or p <= 0 for p in minute.ohlc):
        return None
    op, high, low, close = minute.ohlc
    if not low <= op <= high or not low <= close <= high:
        return None
    return tuple(Fraction(p) for p in minute.ohlc)


def _candidate(question, symbol, opens, session):
    base = question["baseline"]
    expected = {name: base[name] for name in ("status", "gross", "cost", "net", "entry_notional")}
    expected.update({name: None for name in ("entry_at", "request_at", "exit_at", "entry_price", "exit_price",
                                            "quantity", "trigger_start_at", "trigger_close", "issue")})
    expected["selected_trigger"] = "retained"
    if question["status"] != "opportunity" or base["status"] != "closed":
        return expected
    one = timedelta(minutes=1)
    entry_time = datetime.fromisoformat(question["decision_at"]) + one
    old_order, old_fill = base["holding"]["events"]
    old_order_time = datetime.fromisoformat(old_order["at"])
    old_fill_time = datetime.fromisoformat(old_fill["at"])
    sale, = base["holding"]["realized_exits"]
    count = base["fill"]["quantity"]
    _check(type(count) is int and count > 0 and sale["quantity"] == old_fill["quantity"] == count
           and sale["symbol"] == symbol and old_order["action"] == old_fill["action"] == "exit"
           and old_order["execute_at"] == old_fill["at"] and old_fill_time - old_order_time == one
           and one <= old_fill_time - entry_time <= 30 * one and entry_time.tzinfo is not None
           and base["holding"]["status"] == "closed" and base["fill"]["outcome"] == "modeled"
           and old_fill["fill_status"] == "modeled", "original holding differs")
    entry_saved, entry_issue = opens[entry_time.isoformat()]
    exit_saved, exit_issue = opens[old_fill_time.isoformat()]
    _check(entry_issue is None and exit_issue is None, "original quotes are unresolved")
    buy, sell = Fraction(entry_saved), Fraction(exit_saved)
    basis, proceeds = buy * count, sell * count
    fee = basis * 33 / 10000
    _check(buy > 0 and sell > 0 and Fraction(old_fill["open_price"]) == sell
           and Fraction(sale["entry_basis"]) == basis and Fraction(sale["gross_proceeds"]) == proceeds
           and Fraction(sale["allocated_entry_cost"]) == Fraction(base["fill"]["cost"]) == fee
           and [Fraction(base[k]) for k in ("entry_notional", "gross", "cost", "net")]
           == [basis, proceeds - basis, fee, proceeds - basis - fee], "original cash differs")
    expected.update(selected_trigger="original", entry_at=entry_time.isoformat(),
                    request_at=old_order_time.isoformat(), exit_at=old_fill_time.isoformat(),
                    entry_price=str(buy), exit_price=str(sell), quantity=count)
    issue = None
    if session is None:
        issue = "missing_session"
    else:
        _check(isinstance(session, SessionInputs) and session.symbol == symbol
               and session.opening.date() == entry_time.date()
               and session.opening <= entry_time < old_fill_time <= session.closing,
               "session identity differs")
        initial = _quote(session, entry_time)
        if initial is None:
            issue = "entry_price_unknown"
        else:
            _check(initial[0] == buy, "archived entry differs")
    if issue is None:
        # Build the bounded observation stream first; the first unknown or
        # crossing is decisive. Later bars cannot repair either observation.
        count_minutes = int((old_order_time - entry_time) // one)
        observations = ((entry_time + index * one, _quote(session, entry_time + index * one))
                        for index in range(max(0, count_minutes - 3)))
        for clock, quote in observations:
            if quote is None:
                issue = "first_trigger_unknown"
                break
            if quote[3] * 5000 >= buy * 5033:
                expected.update(selected_trigger="profit66", trigger_start_at=clock.isoformat(),
                                trigger_close=str(quote[3]), request_at=(clock + 3 * one).isoformat(),
                                exit_at=(clock + 4 * one).isoformat())
                break
    if issue is None:
        execution_time = datetime.fromisoformat(expected["exit_at"])
        _check(execution_time <= old_fill_time, "absolute expiry differs")
        quote = _quote(session, execution_time)
        if quote is None:
            expected["exit_price"] = None
            issue = "exit_price_unknown"
        else:
            if expected["selected_trigger"] == "original":
                _check(quote[0] == sell, "archived original exit differs")
            cash = quote[0] * count - basis
            expected.update(gross=str(cash), net=str(cash - fee), cost=str(fee),
                            entry_notional=str(basis), exit_price=str(quote[0]))
    if issue is not None:
        expected.update(status="unresolved", issue=issue,
                        **dict.fromkeys(("gross", "cost", "net", "entry_notional")))
        if issue in ("missing_session", "entry_price_unknown", "first_trigger_unknown"):
            expected.update(selected_trigger="unknown", request_at=None, exit_at=None, exit_price=None)
    return expected


def verify_coordinate(row, roster, session, produced):
    """Use a separately decoded session; never invoke the producer replay."""
    _check((row["session"], row["symbol"], row["original_frames"], row["coordinate_status"])
           == (roster["session"], roster["symbol"], roster["original_frames"], roster["coordinate_status"])
           and row["entry_independently_verified"] is True and row["fold"] in range(1, 6),
           "coordinate provenance differs")
    opens = {at: (price, issue) for at, price, issue in row["modeled_opens"]}
    _check(len(opens) == len(row["modeled_opens"]), "duplicate saved price")
    expected = deepcopy(row)
    expected["roster"] = deepcopy(roster)
    for question in expected["questions"]:
        _check("candidate" not in question, "old input contains candidate")
        question["candidate"] = _candidate(question, row["symbol"], opens, session)
    _same(expected, produced, "candidate coordinate or payoff differs")


def _economics(arms):
    status = {state: 0 for state in ("closed", "no_entry", "no_fill", "unsupported", "unresolved")}
    cash = {key: Fraction() for key in ("gross", "cost", "net")}
    gross_returns, net_returns = [], []
    wins = equals = losses = 0
    profit, loss = Fraction(), Fraction()
    for arm in arms:
        _check(arm["status"] in status, "unrecognized economic state")
        status[arm["status"]] += 1
        if arm["status"] != "closed":
            continue
        g, fee, n, principal = (Fraction(arm[key]) for key in ("gross", "cost", "net", "entry_notional"))
        _check(principal > 0 and fee * 10000 == principal * 33 and n + fee == g,
               "cash arithmetic differs")
        for key, value in (("gross", g), ("cost", fee), ("net", n)):
            cash[key] += value
        gross_returns.append(g / principal)
        net_returns.append(n / principal)
        wins += n > 0
        equals += n == 0
        losses += n < 0
        profit += max(n, 0)
        loss += max(-n, 0)
    answer = {"status_counts": status, "closed_trades": status["closed"],
              "known_outcomes": status["closed"] + status["no_entry"] + status["no_fill"],
              "cost_clearing": wins, "cost_equal": equals, "below_cost": losses,
              "profit_factor": str(profit / loss) if loss else None}
    answer.update({key + "_cash": str(value) for key, value in cash.items()})
    for label, values in (("gross", gross_returns), ("net", net_returns)):
        values.sort()
        size = len(values)
        answer["mean_" + label + "_return"] = str(sum(values, Fraction()) / size) if size else None
        answer["median_" + label + "_return"] = str((values[(size - 1) // 2] + values[size // 2]) / 2) if size else None
    return answer


def _comparison(questions):
    originals, candidates, paired_old, paired_new = [], [], [], []
    entries = known = newly_unknown = 0
    selected = Counter()
    for q in questions:
        old, new = q["baseline"], q["candidate"]
        originals.append(old)
        candidates.append(new)
        entries += (old.get("fill") or {}).get("outcome") == "modeled"
        newly_unknown += old["status"] == "closed" and new["status"] == "unresolved"
        selected[new["selected_trigger"]] += 1
        if old["status"] == new["status"] and old["status"] in ("no_entry", "no_fill", "closed"):
            known += 1
            if old["status"] == "closed":
                paired_old.append(old)
                paired_new.append(new)
    old_summary, new_summary = _economics(paired_old), _economics(paired_new)
    differences = [Fraction(new["net"]) / Fraction(new["entry_notional"])
                   - Fraction(old["net"]) / Fraction(old["entry_notional"])
                   for old, new in zip(paired_old, paired_new, strict=True)]
    return {"opportunities": len(originals), "original_modeled_entries": entries,
            "baseline": _economics(originals), "candidate": _economics(candidates),
            "newly_unresolved": newly_unknown, "selected_triggers": dict(selected),
            "paired": {"known_outcomes": known, "closed_trades": len(paired_old),
                       "baseline": old_summary, "candidate": new_summary,
                       "delta_net_cash": str(sum((Fraction(q["net"]) for q in paired_new), Fraction())
                                             - sum((Fraction(q["net"]) for q in paired_old), Fraction())),
                       "mean_delta_net_return": str(sum(differences, Fraction()) / len(differences))
                       if differences else None}}


def verify_summary(rows, report):
    """Rebuild grouping and statistics independently from verified coordinates."""
    rows = list(rows)
    seen, days, weeks = set(), set(), set()
    frames, states = Counter(), Counter()
    groups = {name: [] for name in ("all", "A", "B", "A_or_B", "other")}
    fold_rows = {str(index): [] for index in range(1, 6)}
    day_rows, symbol_rows = {}, {}
    for row in rows:
        day, symbol = row["session"], row["symbol"]
        _check((day, symbol) not in seen, "duplicate coordinate")
        seen.add((day, symbol))
        days.add(day)
        cal = date.fromisoformat(day).isocalendar()
        week = f"{cal.year:04d}-W{cal.week:02d}"
        weeks.add(week)
        day_rows.setdefault(day, [])
        symbol_rows.setdefault(symbol, [])
        frames.update(dict(row["roster"]["frame_states"]))
        for q in row["questions"]:
            states[q["status"]] += 1
            if q["status"] != "opportunity":
                continue
            origin = q["episode_origin_context"]
            _check(origin in ("A", "B", "other"), "origin context differs")
            for group in ("all", origin, "A_or_B") if origin in ("A", "B") else ("all", origin):
                groups[group].append((week, q))
            fold_rows[str(row["fold"])].append(q)
            day_rows[day].append(q)
            symbol_rows[symbol].append(q)
    group_reports = {}
    for name, members in groups.items():
        weekly = {week: _comparison([q for w, q in members if w == week]) for week in sorted(weeks)}
        group_reports[name] = {"totals": _comparison([q for _, q in members]), "weekly": weekly,
                               "baseline_weeks_at_least_15": sum(v["baseline"]["cost_clearing"] >= 15 for v in weekly.values()),
                               "candidate_weeks_at_least_15": sum(v["candidate"]["cost_clearing"] >= 15 for v in weekly.values())}
    day_reports = {day: _comparison(day_rows[day]) for day in sorted(day_rows)}
    concentration = {}
    for arm in ("baseline", "candidate"):
        cash = [(day, Fraction(panel[arm]["net_cash"])) for day, panel in day_reports.items()]
        positive = [(day, amount) for day, amount in cash if amount > 0]
        largest = max(positive, key=lambda pair: pair[1]) if positive else (None, Fraction())
        size, total = len(cash), sum((v for _, v in cash), Fraction())
        avg = total / size if size else None
        variance = (sum((v * v for _, v in cash), Fraction()) - total * total / size) / (size - 1) if size > 1 else None
        concentration[arm] = {"independent_sessions": size, "largest_positive_session": largest[0],
                              "net_cash_without_largest_positive_session": str(total - largest[1]),
                              "mean_session_net_cash": str(avg) if avg is not None else None,
                              "sample_variance_session_net_cash": str(variance) if variance is not None else None}
    expected = {"coordinates": len(seen), "sessions": len(days), "weeks": len(weeks),
                "frames": sum(frames.values()), "frame_states": dict(frames), "question_states": dict(states),
                "groups": group_reports, "folds": {key: _comparison(qs) for key, qs in fold_rows.items()},
                "by_session": day_reports,
                "by_symbol": {symbol: _comparison(qs) for symbol, qs in symbol_rows.items()},
                "session_concentration": concentration,
                "cash_scope": "known closed event cash; not account NAV; paired means use common closed trades"}
    _same(expected, report, "published summary differs")
