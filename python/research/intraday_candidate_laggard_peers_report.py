"""Report peer-relative contrasts on the fixed weak-path laggard ledger."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction

from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_path_report as path_report
from research import intraday_candidate_laggard_report as support


_STATES = ("weaker", "other", "unresolved", "retain")
_ARMS = {"weaker": "candidate", "other": "other"}
_QUORUM = 8
_OTHER_MEMBERS = 19


def _peer_state(question):
    peer = question["peer"]
    count = peer["peer_count"]
    target, center = peer["target_return"], peer["peer_median"]
    if path_report._path_state(question) != "weak":
        if any(value is not None for value in (count, target, center)):
            raise ValueError("EJ peer report: unexpected peer evidence on a retained decision")
        expected = "retain"
    else:
        if type(count) is not int or not 0 <= count <= _OTHER_MEMBERS or (center is None) != (count < _QUORUM):
            raise ValueError("EJ peer report: peer median and quorum differ")
        if any(value is not None and (type(value) is not str or Fraction(value) <= -1) for value in (target, center)):
            raise ValueError("EJ peer report: invalid exact peer return")
        if target is not None:
            prior = question["path"]
            mid, last = Fraction(prior["mid_close"]), Fraction(prior["last_close"])
            if mid <= 0 or last <= 0 or Fraction(target) != (last - mid) / mid:
                raise ValueError("EJ peer report: target return differs from sealed path")
        if target is None or center is None:
            expected = "unresolved"
        else:
            expected = "weaker" if Fraction(target) < Fraction(center) else "other"
    if peer["state"] != expected:
        raise ValueError("EJ peer report: state differs from sealed path or peer returns")
    return expected, count


def _validate_projection(question, state):
    original = question["baseline"]
    for path, arm in _ARMS.items():
        projected = question[arm]
        if state in (path, "retain"):
            if projected != original:
                raise ValueError("EJ peer report: selected or retained arm changed the fixed trade")
        else:
            unresolved = state == "unresolved"
            status, cash = ("unresolved", None) if unresolved else ("no_entry", "0")
            if (projected["status"] != status or projected["fill"] is not None
                    or projected.get("entry_request_at") is not None
                    or any(projected[key] != cash for key in path_report._CASH_FIELDS)):
                raise ValueError("EJ peer report: excluded or unresolved arm has an unexpected trade or cash")


def _empty_panel():
    return {state: {**path_report._empty_path(), "peer_counts": {}} for state in _STATES}


def _panels(dated):
    totals, weekly = _empty_panel(), defaultdict(_empty_panel)
    for week, state, count, outcome in dated:
        for group in (totals, weekly[week]):
            panel = group[state]
            path_report._add_path(panel, outcome)
            if count is not None:
                counts = panel["peer_counts"]
                counts[str(count)] = counts.get(str(count), 0) + 1
    for group in (totals, *weekly.values()):
        for panel in group.values():
            panel["baseline_net_cash"] = str(panel["baseline_net_cash"])
            panel["peer_counts"] = dict(sorted(panel["peer_counts"].items()))
    return {"totals": totals, "weekly": dict(sorted(weekly.items()))}


def _reference_panel(group):
    panels = {}
    for state in _STATES:
        matching = [(count, original) for _, key, count, original in group if key == state]
        closed_cash = [original.net for _, original in matching if original.status == "closed"]
        panels[state] = {
            "coordinates": len(matching),
            "baseline_known": sum(original.status != "unresolved" for _, original in matching),
            "baseline_modeled_fills": sum(original.modeled for _, original in matching),
            "baseline_closed": len(closed_cash),
            "baseline_cost_clearing": sum(cash > 0 for cash in closed_cash),
            "baseline_net_cash": str(sum(closed_cash, Fraction())),
            "peer_counts": dict(sorted(Counter(str(count) for count, _ in matching if count is not None).items())),
        }
    return panels


def _reference_panels(dated):
    weekly = {week: _reference_panel([row for row in dated if row[0] == week])
              for week in sorted({week for week, _, _, _ in dated})}
    return {"totals": _reference_panel(dated), "weekly": weekly}


def _symbol_concentration(comparison):
    concentration = {}
    totals = comparison["groups"]["all"]["totals"]
    for arm in ("baseline", "candidate"):
        cash = {symbol: Fraction(panel[arm]["net_cash"]) for symbol, panel in sorted(comparison["by_symbol"].items())}
        total = sum(cash.values(), Fraction())
        if total != Fraction(totals[arm]["net_cash"]):
            raise ValueError("EJ peer report: symbol cash does not reconcile")
        largest = max((symbol for symbol, net in cash.items() if net > 0), key=cash.get, default=None)
        concentration[arm] = {
            "largest_positive_symbol": largest,
            "net_cash_without_largest_positive_symbol": str(total - cash.get(largest, Fraction())),
        }
    return concentration


def _reference_symbol_concentration(rows, comparison):
    concentration = {}
    for arm in ("baseline", "candidate"):
        cash = {}
        for row in rows:
            symbol = row["symbol"]
            cash.setdefault(symbol, Fraction())
            for question in row["questions"]:
                if question["status"] == "opportunity" and question[arm]["status"] == "closed":
                    cash[symbol] += Fraction(question[arm]["net"])
        expected = {symbol: Fraction(panel[arm]["net_cash"]) for symbol, panel in comparison["by_symbol"].items()}
        total = Fraction(comparison["groups"]["all"]["totals"][arm]["net_cash"])
        if cash != expected or sum(cash.values(), Fraction()) != total:
            raise ValueError("EJ peer report: symbol cash does not reconcile")
        positive = [symbol for symbol, net in cash.items() if net > 0]
        largest = min(positive, key=lambda symbol: (-cash[symbol], symbol), default=None)
        concentration[arm] = {
            "largest_positive_symbol": largest,
            "net_cash_without_largest_positive_symbol": str(sum(
                (net for symbol, net in cash.items() if symbol != largest), Fraction())),
        }
    return concentration


def summarize(rows, *, audit=False):
    """Reconcile each peer filter against all original coordinates and weeks."""
    rows = list(rows)
    dated = []
    for row in rows:
        question, = row["questions"]
        state, count = _peer_state(question)
        _validate_projection(question, state)
        year, week, _ = date.fromisoformat(row["session"]).isocalendar()
        dated.append((f"{year:04d}-W{week:02d}", state, count, support._read_execution(question["baseline"])))
    peers = _reference_panels(dated) if audit else _panels(dated)
    report = {"peers": peers}
    for state, arm in _ARMS.items():
        pairs = []
        for row in rows:
            question, = row["questions"]
            pairs.append({**row, "questions": [{**question, "candidate": question[arm]}]})
        comparison = cash_summary.summarize(pairs)
        cash_reference.verify_summary(pairs, comparison)
        execution = support.execution_summary(pairs, audit=audit)
        all_rows = comparison["groups"]["all"]
        path_report._reconcile(state, peers["totals"], execution["totals"], all_rows["totals"])
        for week, panel in peers["weekly"].items():
            path_report._reconcile(state, panel, execution["weekly"][week], all_rows["weekly"][week])
        concentration = _reference_symbol_concentration(pairs, comparison) if audit else _symbol_concentration(comparison)
        report[f"weak_vs_{state}"] = {"comparison": comparison, "execution": execution,
                                      "symbol_concentration": concentration}
    return report
