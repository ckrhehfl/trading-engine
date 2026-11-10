"""Report first-hour path contrasts on the fixed 60-minute laggard ledger."""

from collections import defaultdict
from datetime import date
from fractions import Fraction

from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_report as support


_PATHS = ("rebound", "weak", "unresolved", "retain")
_ARMS = {"rebound": "candidate", "weak": "weak"}
_PRICES = ("first_open", "mid_close", "last_close")
_CASH_FIELDS = ("gross", "cost", "net", "entry_notional")


def _path_state(question):
    path, plan = question["path"], question["rank_decision"]
    prices = tuple(None if path[key] is None else Fraction(path[key]) for key in _PRICES)
    if plan["rank_state"] != "select" or plan["order_state"] != "ready":
        if any(price is not None for price in prices):
            raise ValueError("EJ path report: unexpected price on a retained decision")
        expected = "retain"
    elif None in prices:
        expected = "unresolved"
    else:
        _, mid, last = prices
        expected = "rebound" if last > mid else "weak"
    if path["state"] != expected:
        raise ValueError("EJ path report: state differs from original readiness or path prices")
    return expected


def _validate_projection(question, state):
    original = question["baseline"]
    for path, arm in _ARMS.items():
        projected = question[arm]
        if state in (path, "retain"):
            if projected != original:
                raise ValueError("EJ path report: selected or retained arm changed the fixed trade")
        else:
            unresolved = state == "unresolved"
            status = "unresolved" if unresolved else "no_entry"
            cash = None if unresolved else "0"
            if (projected["status"] != status or projected["fill"] is not None
                    or projected.get("entry_request_at") is not None
                    or any(projected[key] != cash for key in _CASH_FIELDS)):
                raise ValueError("EJ path report: excluded or unresolved arm has an unexpected trade or cash")


def _empty_path():
    return {"coordinates": 0, "baseline_known": 0, "baseline_modeled_fills": 0, "baseline_closed": 0,
            "baseline_cost_clearing": 0, "baseline_net_cash": Fraction()}


def _add_path(panel, outcome):
    panel["coordinates"] += 1
    panel["baseline_known"] += outcome.net is not None
    panel["baseline_modeled_fills"] += outcome.modeled
    if outcome.status == "closed":
        panel["baseline_closed"] += 1
        panel["baseline_cost_clearing"] += outcome.net > 0
        panel["baseline_net_cash"] += outcome.net


def _path_panels(dated):
    totals = {path: _empty_path() for path in _PATHS}
    weekly = defaultdict(lambda: {path: _empty_path() for path in _PATHS})
    for week, path, outcome in dated:
        _add_path(totals[path], outcome)
        _add_path(weekly[week][path], outcome)
    for group in (totals, *weekly.values()):
        for panel in group.values():
            panel["baseline_net_cash"] = str(panel["baseline_net_cash"])
    return {"totals": totals, "weekly": dict(sorted(weekly.items()))}


def _reference_path_panel(group):
    panels = {}
    for path in _PATHS:
        originals = [original for _, state, original in group if state == path]
        closed_cash = [original.net for original in originals if original.status == "closed"]
        panels[path] = {"coordinates": len(originals),
                        "baseline_known": sum(original.status != "unresolved" for original in originals),
                        "baseline_modeled_fills": sum(original.modeled for original in originals),
                        "baseline_closed": len(closed_cash),
                        "baseline_cost_clearing": sum(cash > 0 for cash in closed_cash),
                        "baseline_net_cash": str(sum(closed_cash, Fraction()))}
    return panels


def _reference_path_panels(dated):
    weekly = {week: _reference_path_panel([row for row in dated if row[0] == week])
              for week in sorted({week for week, _, _ in dated})}
    return {"totals": _reference_path_panel(dated), "weekly": weekly}


def _reconcile(path, paths, execution, cash):
    kept = (paths[path], paths["retain"])
    known = [panel for state, panel in paths.items() if state != "unresolved"]
    baseline_cash = sum((Fraction(panel["baseline_net_cash"]) for panel in known), Fraction())
    candidate_cash = sum((Fraction(panel["baseline_net_cash"]) for panel in kept), Fraction())
    expected_common = {"coordinates": sum(panel["baseline_known"] for panel in known),
                       "baseline_net_cash": str(baseline_cash), "candidate_net_cash": str(candidate_cash),
                       "delta_net_cash": str(candidate_cash - baseline_cash)}
    # Known no-entry outcomes can be common with a closed baseline trade.
    # Paired return differences cover only trades closed under both policies.
    if (execution["common_known"] != expected_common
            or cash["paired"]["closed_trades"] != sum(panel["baseline_closed"] for panel in kept)
            or cash["paired"]["delta_net_cash"] != "0"):
        raise ValueError("EJ path report: common cash or paired closes differ from the path partition")


def summarize(rows, *, audit=False):
    """Retain every original week and reconcile two separate path policies."""
    rows = list(rows)
    dated = []
    for row in rows:
        question, = row["questions"]
        state = _path_state(question)
        _validate_projection(question, state)
        year, week, _ = date.fromisoformat(row["session"]).isocalendar()
        dated.append((f"{year:04d}-W{week:02d}", state, support._read_execution(question["baseline"])))
    paths = _reference_path_panels(dated) if audit else _path_panels(dated)
    report = {"paths": paths}
    for path, arm in _ARMS.items():
        pairs = []
        for row in rows:
            question, = row["questions"]
            pairs.append({**row, "questions": [{**question, "candidate": question[arm]}]})
        comparison = cash_summary.summarize(pairs)
        cash_reference.verify_summary(pairs, comparison)
        execution = support.execution_summary(pairs, audit=audit)
        all_rows = comparison["groups"]["all"]
        _reconcile(path, paths["totals"], execution["totals"], all_rows["totals"])
        for week, panel in paths["weekly"].items():
            _reconcile(path, panel, execution["weekly"][week], all_rows["weekly"][week])
        report[f"hold60_vs_{path}"] = {"comparison": comparison, "execution": execution}
    return report
