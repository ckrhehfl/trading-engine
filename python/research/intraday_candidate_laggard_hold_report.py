"""Report fixed-hold comparisons on the original opening-laggard ledger."""

from fractions import Fraction

from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_report as support


_ENTRY_FIELDS = ("fill", "requested_quantity", "entry_request_at", "entry_execute_at", "entry_at", "entry_price")


def _comparison_rows(rows, arm):
    paired, time_cohort = [], []
    for row in rows:
        question, = row["questions"]
        old, new = question["baseline"], question[arm]
        if old["status"] == "closed" and old["selected_trigger"] == "time":
            if new["status"] not in ("closed", "unresolved") or any(old[key] != new[key] for key in _ENTRY_FIELDS):
                raise ValueError("EJ hold report: extension changed the original entry")
            if new["status"] == "closed" and (
                new["selected_trigger"] not in ("structural", "time")
                or any(old[key] != new[key] for key in ("entry_notional", "cost"))
            ):
                raise ValueError("EJ hold report: invalid extension exit or entry basis")
            time_cohort.append((old, new))
        elif old != new:
            raise ValueError("EJ hold report: extension changed a retained outcome")
        paired.append({**row, "questions": [{**question, "candidate": new}]})
    return paired, time_cohort


def _cohort_summary(cohort):
    panel = dict.fromkeys(("candidate_known", "candidate_closed", "candidate_unresolved",
                           "candidate_structural", "candidate_expiry", "baseline_cost_clearing",
                           "common_baseline_cost_clearing", "common_candidate_cost_clearing",
                           "improved", "decreased", "equal"), 0)
    before = after = Fraction()
    for old, new in cohort:
        old_net = Fraction(old["net"])
        panel["baseline_cost_clearing"] += old_net > 0
        if new["status"] == "unresolved":
            panel["candidate_unresolved"] += 1
            continue
        new_net = Fraction(new["net"])
        panel["candidate_known"] += 1
        panel["candidate_closed"] += 1
        panel["candidate_structural" if new["selected_trigger"] == "structural" else "candidate_expiry"] += 1
        panel["common_baseline_cost_clearing"] += old_net > 0
        panel["common_candidate_cost_clearing"] += new_net > 0
        panel["improved" if new_net > old_net else "decreased" if new_net < old_net else "equal"] += 1
        before += old_net
        after += new_net
    return {"baseline_closed": len(cohort), **panel, "common_baseline_net_cash": str(before),
            "common_candidate_net_cash": str(after), "delta_net_cash": str(after - before)}


def _reference_cohort(cohort):
    known = [(Fraction(old["net"]), Fraction(new["net"]), new["selected_trigger"])
             for old, new in cohort if new["net"] is not None]
    differences = [after - before for before, after, _ in known]
    return {
        "baseline_closed": len(cohort),
        "candidate_known": len(known), "candidate_closed": len(known),
        "candidate_unresolved": len(cohort) - len(known),
        "candidate_structural": sum(trigger == "structural" for _, _, trigger in known),
        "candidate_expiry": sum(trigger == "time" for _, _, trigger in known),
        "baseline_cost_clearing": sum(Fraction(old["net"]) > 0 for old, _ in cohort),
        "common_baseline_cost_clearing": sum(before > 0 for before, _, _ in known),
        "common_candidate_cost_clearing": sum(after > 0 for _, after, _ in known),
        "improved": sum(delta > 0 for delta in differences),
        "decreased": sum(delta < 0 for delta in differences),
        "equal": differences.count(0),
        "common_baseline_net_cash": str(sum((before for before, _, _ in known), Fraction())),
        "common_candidate_net_cash": str(sum((after for _, after, _ in known), Fraction())),
        "delta_net_cash": str(sum(differences, Fraction())),
    }


def summarize(rows, *, audit=False):
    """Keep all roster weeks and separate the 30-minute time-exit cohort."""
    rows = list(rows)
    report = {}
    for arm, label in (("candidate", "hold30_vs_hold60"), ("hold90", "hold30_vs_hold90")):
        paired, cohort = _comparison_rows(rows, arm)
        comparison = cash_summary.summarize(paired)
        cash_reference.verify_summary(paired, comparison)
        execution = support.execution_summary(paired, audit=audit)
        time_cohort = _reference_cohort(cohort) if audit else _cohort_summary(cohort)
        all_pairs = comparison["groups"]["all"]["totals"]["paired"]
        common = execution["totals"]["common_known"]
        if (all_pairs["known_outcomes"] != common["coordinates"]
                or not all_pairs["delta_net_cash"] == common["delta_net_cash"] == time_cohort["delta_net_cash"]):
            raise ValueError("EJ hold report: common cash or support differs from the extension cohort")
        report[label] = {"comparison": comparison, "execution": execution, "time_cohort": time_cohort}
    return report
