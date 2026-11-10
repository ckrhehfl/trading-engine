"""Report additional opening orders separately from retained original trades."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from typing import NamedTuple

from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_report as support
from research.intraday_candidate_laggard_peers_report import _reference_symbol_concentration, _symbol_concentration


class _Outcome(NamedTuple):
    first_request: bool
    above_limit: bool
    newly_unresolved: bool
    decision: str
    reason: str
    added: support._Execution | None


def _outcome(question):
    old, new, gate = (question[key] for key in ("baseline", "candidate", "retry"))
    baseline, candidate = (support._read_execution(arm) for arm in (old, new))
    state = gate["state"]
    open_price = gate["original_open"]
    above_limit = open_price is not None and Fraction(open_price) > Fraction(question["rank_decision"]["limit"])
    added = None
    if state == "retry":
        if (not above_limit or baseline.status != "no_fill" or not baseline.requested or baseline.modeled
                or not candidate.requested or candidate.status == "no_entry"
                or new["requested_quantity"] != old["requested_quantity"] or old["requested_quantity"] != gate["quantity"]):
            raise ValueError("EJ retry report: added request differs from original unfilled order")
        added = candidate
    elif state == "unresolved":
        if baseline.modeled or candidate.status != "unresolved" or candidate.requested or candidate.modeled:
            raise ValueError("EJ retry report: uncertain eligibility has a modeled trade")
    elif state != "retain" or old != new:
        raise ValueError("EJ retry report: retained original outcome differs")
    return _Outcome(baseline.requested, above_limit, baseline.net is not None and candidate.net is None,
                    state, gate["reason"], added)


def _empty():
    return {"first_requests": 0, "initial_above_limit": 0, "decisions": dict.fromkeys(("retain", "unresolved", "retry"), 0),
            "reasons": Counter(), "newly_unresolved": 0, "added_requests": 0, "added_modeled_fills": 0, "added_closed": 0,
            "added_no_fill": 0, "added_cost_clearing": 0, "added_unresolved_before_fill": 0,
            "added_unresolved_after_fill": 0, "added_net_cash": Fraction()}


def _add(panel, row):
    panel["first_requests"] += row.first_request
    panel["initial_above_limit"] += row.above_limit
    panel["newly_unresolved"] += row.newly_unresolved
    panel["decisions"][row.decision] += 1
    panel["reasons"][row.reason] += 1
    added = row.added
    if added is not None:
        panel["added_requests"] += 1
        panel["added_modeled_fills"] += added.modeled
        if added.net is None:
            panel["added_unresolved_after_fill" if added.modeled else "added_unresolved_before_fill"] += 1
        elif added.status == "closed":
            panel["added_closed"] += 1
            panel["added_cost_clearing"] += added.net > 0
            panel["added_net_cash"] += added.net
        else:
            panel["added_no_fill"] += 1


def _finish(panel):
    return {**panel, "reasons": dict(panel["reasons"]), "added_net_cash": str(panel["added_net_cash"]),
            "candidate_total_requests": panel["first_requests"] + panel["added_requests"]}


def _reference_panel(rows):
    added = [row.added for row in rows if row.added is not None]
    closed = [outcome.net for outcome in added if outcome.status == "closed"]
    return {"first_requests": sum(row.first_request for row in rows),
            "candidate_total_requests": sum(row.first_request + (row.added is not None) for row in rows),
            "initial_above_limit": sum(row.above_limit for row in rows),
            "newly_unresolved": sum(row.newly_unresolved for row in rows),
            "decisions": {state: sum(row.decision == state for row in rows) for state in ("retain", "unresolved", "retry")},
            "reasons": dict(Counter(row.reason for row in rows)), "added_requests": len(added),
            "added_modeled_fills": sum(outcome.modeled for outcome in added), "added_closed": len(closed),
            "added_no_fill": sum(outcome.status == "no_fill" for outcome in added),
            "added_cost_clearing": sum(net > 0 for net in closed),
            "added_unresolved_before_fill": sum(outcome.status == "unresolved" and not outcome.modeled for outcome in added),
            "added_unresolved_after_fill": sum(outcome.status == "unresolved" and outcome.modeled for outcome in added),
            "added_net_cash": str(sum(closed, Fraction()))}


def _panels(dated, audit):
    if audit:
        weeks = {week: _reference_panel([row for label, row in dated if label == week])
                 for week in sorted({label for label, _ in dated})}
        return _reference_panel([row for _, row in dated]), weeks
    totals, weekly = _empty(), defaultdict(_empty)
    for week, row in dated:
        _add(totals, row)
        _add(weekly[week], row)
    return _finish(totals), {week: _finish(panel) for week, panel in sorted(weekly.items())}


def _reconcile(panel, execution, comparison):
    old, new = (execution["arms"][arm] for arm in ("baseline", "candidate"))
    paired = comparison["paired"]
    if (panel["first_requests"] != old["requests"]
            or panel["added_requests"] != panel["decisions"]["retry"]
            or panel["newly_unresolved"] != comparison["baseline"]["known_outcomes"] - execution["common_known"]["coordinates"]
            or panel["added_modeled_fills"] != new["modeled_fills"] - old["modeled_fills"]
            or panel["added_closed"] != new["closed"] - old["closed"]
            or panel["added_cost_clearing"] != new["cost_clearing"] - old["cost_clearing"]
            or panel["added_net_cash"] != execution["common_known"]["delta_net_cash"]
            or paired["closed_trades"] != old["closed"] or paired["delta_net_cash"] != "0"):
        raise ValueError("EJ retry report: extra requests or payoffs do not reconcile")


def summarize(rows, *, audit=False):
    """Count both original and added requests, including all original weeks."""
    rows = list(rows)
    dated = []
    for row in rows:
        question, = row["questions"]
        year, week, _ = date.fromisoformat(row["session"]).isocalendar()
        dated.append((f"{year:04d}-W{week:02d}", _outcome(question)))
    totals, weekly = _panels(dated, audit)
    comparison = cash_summary.summarize(rows)
    cash_reference.verify_summary(rows, comparison)
    execution = support.execution_summary(rows, audit=audit)
    all_rows = comparison["groups"]["all"]
    _reconcile(totals, execution["totals"], all_rows["totals"])
    for week, panel in weekly.items():
        _reconcile(panel, execution["weekly"][week], all_rows["weekly"][week])
    concentration = _reference_symbol_concentration(rows, comparison) if audit else _symbol_concentration(comparison)
    return {"comparison": comparison, "execution": execution, "symbol_concentration": concentration,
            "retry": {"totals": totals, "weekly": weekly, "scope": {
                "candidate_total_requests": "all original and added requests",
                "execution_requests": "one final receipt per coordinate and arm",
                "newly_unresolved": "original known, extended policy unresolved",
                "comparison_newly_unresolved": "original closed, extended policy unresolved",
                "comparison_paired_known_outcomes": "identical known states only; use execution common_known for all",
            }}}
