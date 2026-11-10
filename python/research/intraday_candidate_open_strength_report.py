"""Reconcile opening strength, peer support and exact event cash."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from statistics import median

from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_report as support
from research.intraday_candidate_laggard_peers_report import _reference_symbol_concentration, _symbol_concentration


_PRICES = ("first_open", "mid_close", "last_close")
_STATES = ("select", "decline", "unresolved")


def _exact(value):
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("EJ strength report: expected exact fraction string")
    return Fraction(value)


def _day(questions):
    prices = {symbol: tuple(_exact(q["rank_decision"][key]) for key in _PRICES)
              for symbol, q in questions.items()}
    if len(prices) != 20 or any(price is not None and price <= 0 for values in prices.values() for price in values):
        raise ValueError("EJ strength report: invalid roster or price evidence")
    peer_returns = {symbol: (last - mid) / mid for symbol, (_, mid, last) in prices.items()
                    if mid is not None and last is not None}
    for symbol, q in questions.items():
        plan = q["rank_decision"]
        first, mid, last = prices[symbol]
        own = None if first is None or last is None else (last - first) / first
        values = [value for peer, value in peer_returns.items() if peer != symbol]
        center = median(values) if len(values) >= 8 else None
        peer_state = "unresolved" if center is None else "select" if center < 0 else "decline"
        state = ("unresolved" if None in (first, mid, last) else "select"
                 if own >= Fraction(66, 10_000) and last >= mid else "decline")
        gate = peer_state if state == "select" else state
        if (q["observed_peer_members"] != len(peer_returns) or _exact(plan["own_return"]) != own
                or plan["peer_count"] != len(values) or _exact(plan["peer_median"]) != center
                or (plan["rank_state"], plan["peer_state"], plan["gated_state"]) != (state, peer_state, gate)):
            raise ValueError("EJ strength report: own or leave-one-out decision differs")
        limit, stop, order = _exact(plan["limit"]), _exact(plan["stop"]), plan["order_state"]
        if state != "select":
            geometry = (order, limit, stop) == ("retain", None, None)
        else:
            geometry = limit == last and (
                (order == "unresolved" and stop is None) or (order == "decline" and stop == limit)
                or (order == "ready" and stop is not None and 0 < stop < limit))
        if not geometry:
            raise ValueError("EJ strength report: entry geometry differs")
        baseline, candidate = (support._read_execution(q[arm]) for arm in ("baseline", "candidate"))
        if gate == state:
            valid = q["baseline"] == q["candidate"]
        elif gate == "decline":
            valid = candidate.status == "no_entry" and not candidate.requested
        else:
            valid = candidate.status == "unresolved" and not candidate.requested and not candidate.modeled
        if (not valid or (state == "unresolved" and baseline.status != "unresolved")
                or (state == "decline" and baseline.status != "no_entry")
                or (state == "select" and order == "unresolved" and baseline.status != "unresolved")
                or (state == "select" and order == "decline" and baseline.status != "no_entry")):
            raise ValueError("EJ strength report: projected outcome differs from the frozen decision")
    return len(peer_returns), [q["rank_decision"] for q in questions.values()]


def _panel(days):
    plans = [plan for _, members in days for plan in members]
    result = {"sessions": len(days), "coordinates": len(plans),
              "own_endpoints_missing": {key: sum(plan[key] is None for plan in plans) for key in _PRICES},
              "peer_pairs_by_day": dict(Counter(str(observed) for observed, _ in days)),
              "eligible_peer_states": {state: sum(plan["rank_state"] == "select" and plan["peer_state"] == state
                                                 for plan in plans) for state in _STATES},
              "arms": {}}
    for arm, key in (("baseline", "rank_state"), ("candidate", "gated_state")):
        counts = {name: sum(plan[key] == state for plan in plans) for state, name in support.SELECTION.items()}
        counts.update({f"order_{state}": sum(plan[key] == "select" and plan["order_state"] == state for plan in plans)
                       for state in ("ready", "decline", "unresolved")})
        result["arms"][arm] = counts
    return result


def summarize(rows, *, audit=False):
    """Summarize the whole comparison and each week with no dropped coordinates."""
    rows = list(rows)
    dates = defaultdict(dict)
    for row in rows:
        day, symbol = date.fromisoformat(row["session"]), row["symbol"]
        if symbol in dates[day] or len(row["questions"]) != 1:
            raise ValueError("EJ strength report: expected one decision per unique coordinate")
        dates[day][symbol], = row["questions"]
    weeks = defaultdict(list)
    for day, questions in dates.items():
        year, week, _ = day.isocalendar()
        weeks[f"{year:04d}-W{week:02d}"].append(_day(questions))
    weekly = {week: _panel(days) for week, days in sorted(weeks.items())}
    totals = _panel([day for days in weeks.values() for day in days])
    frequencies = support._reference_frequencies if audit else support._frequencies
    comparison = cash_summary.summarize(rows)
    cash_reference.verify_summary(rows, comparison)
    return {"comparison": comparison, "execution": support.execution_summary(rows, audit=audit),
            "selection": {"totals": totals, "weekly": weekly,
                          "weekly_counts": frequencies(weekly, ("selected", "order_ready"))},
            "symbol_concentration": _reference_symbol_concentration(rows, comparison) if audit else _symbol_concentration(comparison),
            "support_scope": {"common_known": "execution.totals.common_known includes different known outcomes",
                              "paired_closed": "comparison.groups.all.totals.paired cash includes only common closes",
                              "legacy_paired_known": "comparison.groups.all.totals.paired.known_outcomes counts identical known states"}}
