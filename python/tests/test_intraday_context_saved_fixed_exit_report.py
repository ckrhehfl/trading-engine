"""Invented receipts for the fixed-exit join and exact three-arm report."""

from copy import deepcopy
from fractions import Fraction

import pytest

from research.intraday_chart_cash import INITIAL_CAPITAL
from research.intraday_context_saved_fixed_exit_report import join_saved_fixed_exit_row, summarize_saved_fixed_exit


def arm(status, reason, money=(None,) * 4, fill=None, holding=None):
    return {"status": status, "reason": reason, "fill": fill, "holding": holding,
            **dict(zip(("gross", "cost", "net", "entry_notional"), money, strict=True))}


def closed():
    holding = {"status": "closed", "cash": str(INITIAL_CAPITAL + Fraction(967, 100)),
               "realized_exits": [{"symbol": "invented", "quantity": 1, "entry_basis": "100",
                                   "gross_proceeds": "110", "allocated_entry_cost": "33/100"}]}
    return arm("closed", "terminal", ("10", "33/100", "967/100", "100"),
               {"outcome": "modeled", "quantity": 1, "cost": "33/100"}, holding)


def coordinate(*, day="2025-11-10", questions=()):
    return {"fold": 1, "session": day, "symbol": "invented", "coordinate_status": "eligible",
            "original_frames": 20, "questions": list(questions)}


def case(baseline, sequence, *, key="1", status="opportunity", static=None, baseline_static=None):
    identity = {"question_sha256": key * 64, "status": status, "stage": "approach",
                "decision_at": "2025-11-10T00:45:00+00:00", "episode_origin_context": "A"}
    delta = None if baseline["net"] is None or sequence["net"] is None else str(
        Fraction(sequence["net"]) - Fraction(baseline["net"]))
    decision = {**identity, "static": static, "baseline_static": baseline_static,
                "expected_net": ("1/" + "9" * 180) if static is None else None,
                "request_sha256": "a" * 64 if static is None else None}
    saved = {**identity, "baseline": baseline, "selected": sequence, "selected_minus_baseline_net": delta,
             "same_fill_management": None}
    return decision, saved


def joined(*pairs):
    decisions, original = zip(*pairs, strict=True)
    return join_saved_fixed_exit_row(coordinate(questions=decisions),
                                     {**coordinate(questions=original), "entry_independently_verified": True})


def test_exact_join_and_support_report_include_non_opportunity_and_zero_fill_week():
    wait = arm("no_entry", "no_edge", ("0",) * 4)
    unknown = arm("unresolved", "halted", fill={"outcome": "modeled", "quantity": 1, "cost": "33/100"},
                  holding={"status": "unresolved", "realized_exits": []})
    inactive = arm("overlap_suppressed", "reserved")
    row = joined(case(closed(), wait), case(unknown, unknown, key="2", static=["no_entry", "no_edge"]),
                 case(inactive, inactive, key="3", status="overlap_suppressed",
                      static=["overlap_suppressed", "reserved"], baseline_static=["overlap_suppressed", "reserved"]))

    profit, unresolved, suppressed = row["questions"]
    assert profit["baseline"] == profit["lightgbm"] and profit["sequence"]["net"] == "0"
    assert unresolved["baseline"]["net"] is None and unresolved["baseline"]["modeled_fill"] is True
    assert unresolved["lightgbm"]["net"] == "0" and suppressed["lightgbm"]["net"] is None
    report = summarize_saved_fixed_exit((row, coordinate(day="2025-11-17")))
    assert report["original_evaluation_coordinates"] == report["original_evaluation_sessions"] == 2
    assert report["question_statuses"] == {"opportunity": 2, "overlap_suppressed": 1}
    overall = report["overall"]
    assert overall["original_opportunities"] == 2 and overall["all_arms_known_opportunities"] == 1
    assert overall["arms"]["baseline"]["modeled_fills"] == 2
    assert overall["arms"]["baseline"]["fills_with_unknown_outcome"] == 1
    assert overall["arms"]["lightgbm"]["closed_net_pnl"] == "967/100"
    assert overall["arms"]["lightgbm"]["mean_closed_trade_net_bps"] == "967"
    _, base_vs_tree, seq_vs_tree = overall["paired"]
    assert base_vs_tree["both_known_opportunities"] == base_vs_tree["paired_positive_net_fills_captured"] == 1
    assert base_vs_tree["paired_net_delta"] == "0" and seq_vs_tree["paired_net_delta"] == "967/100"
    assert report["by_episode_origin"]["A"] == report["by_stage"]["approach"] == overall
    assert report["weekly"][1]["modeled_fills"] == dict.fromkeys(("baseline", "sequence", "lightgbm"), 0)
    assert report["date_contributions"][1]["paired"][0]["paired_net_delta"] is None


@pytest.mark.parametrize("corruption", ("independent", "question", "basis", "delta", "nonpositive", "request", "static"))
def test_join_refuses_invalid_binding_payoff_or_reuse(corruption):
    chosen, old = case(closed(), arm("no_entry", "no_edge", ("0",) * 4))
    decisions = coordinate(questions=[deepcopy(chosen)])
    original = {**coordinate(questions=[deepcopy(old)]), "entry_independently_verified": True}
    if corruption == "independent":
        original["entry_independently_verified"] = 1
    elif corruption == "question":
        original["questions"][0]["decision_at"] = "2025-11-10T00:46:00+00:00"
    elif corruption == "basis":
        original["questions"][0]["baseline"]["entry_notional"] = "101"
    elif corruption == "delta":
        original["questions"][0]["selected_minus_baseline_net"] = "0"
    elif corruption == "nonpositive":
        decisions["questions"][0]["expected_net"] = "0"
    elif corruption == "request":
        decisions["questions"][0]["request_sha256"] = None
    else:
        decisions["questions"][0]["baseline_static"] = ["no_entry", "no_edge"]

    with pytest.raises(ValueError):
        join_saved_fixed_exit_row(decisions, original)
