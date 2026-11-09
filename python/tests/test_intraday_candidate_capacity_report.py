from copy import deepcopy
from fractions import Fraction

from research.intraday_candidate_capacity_reference import reconstruct_capacity
from research.intraday_candidate_capacity_report import summarize_capacity


def _question(context, entry_at, best_bps, control_net, timer_net, *, earlier=3, missing=0):
    return {
        "status": "opportunity",
        "episode_origin_context": context,
        "baseline": {"status": "closed", "entry_at": entry_at, "net": control_net},
        "candidate": {"status": "unresolved" if timer_net is None else "closed", "net": timer_net},
        "exit_capacity": {"earlier_opens": earlier, "missing_opens": missing,
                          "best_open": str(10_033 + Fraction(best_bps)) if best_bps is not None else None,
                          "best_net_bps": best_bps},
    }


def test_exact_cost_clears_and_separate_capacity_and_timer_support():
    rows = [
        {"session": "2026-01-05", "questions": [
            _question("A", "2026-01-05T09:59:00+09:00", "7/2", "-2", "5/3"),
            _question("A", "2026-01-05T01:00:00+00:00", "0", "0", "0", earlier=0),
            _question("other", "2026-01-05T12:00:00+09:00", "-3", "-4", "-3"),
            _question("B", "2026-01-05T11:59:00+09:00", None, "7", None, missing=2),
        ]},
        {"session": "2026-01-12", "questions": [
            {"status": "opportunity", "baseline": {"status": "unsupported"}, "exit_capacity": None},
        ]},
        {"session": "2026-01-19", "questions": [
            _question("B", "2026-01-19T12:01:00+09:00", None, "3", "2", missing=1),
        ]},
    ]

    report = summarize_capacity(iter(rows))
    assert reconstruct_capacity(iter(rows)) == report
    groups = report["groups"]
    all_group = groups["all"]
    totals = all_group["totals"]
    assert report["weeks"] == 3
    assert all_group["zero_trade_weeks"] == 1
    assert all_group["weekly"]["2026-W03"]["closed_activity_trades"] == 0
    assert (totals["closed_activity_trades"], totals["resolved_capacity"], totals["missing_capacity"]) == (5, 3, 2)
    assert (totals["trades_without_earlier_open"], totals["required_earlier_opens"],
            totals["missing_earlier_opens"]) == (1, 12, 3)
    assert (totals["best_open_cost_clearing"], totals["best_open_cost_equal"],
            totals["best_open_below_cost"], totals["mean_best_net_bps"]) == (1, 1, 1, "1/6")
    assert totals["baseline"] == {"closed_trades": 5, "cost_clearing": 2, "net_cash": "4"}
    assert totals["candidate"] == {"closed_trades": 4, "unresolved_trades": 1,
                                  "cost_clearing": 2, "net_cash": "2/3"}
    assert totals["common_support"] == {"closed_trades": 4, "baseline_cost_clearing": 1,
                                       "candidate_cost_clearing": 2, "baseline_net_cash": "-3",
                                       "candidate_net_cash": "2/3"}
    assert totals["triple_common_support"] == {"closed_trades": 3, "best_open_cost_clearing": 1,
                                              "baseline_cost_clearing": 0, "candidate_cost_clearing": 1}

    assert groups["A"]["totals"]["mean_best_net_bps"] == "7/4"
    assert groups["A"]["zero_trade_weeks"] == 2
    assert groups["B"]["totals"]["mean_best_net_bps"] is None
    assert groups["B"]["totals"]["missing_capacity"] == 2
    assert groups["B"]["totals"]["common_support"]["closed_trades"] == 1
    assert groups["B"]["totals"]["triple_common_support"]["closed_trades"] == 0
    assert groups["other"]["totals"]["best_open_below_cost"] == 1

    periods = all_group["by_entry_period_kst"]
    assert {name: panel["closed_activity_trades"] for name, panel in periods.items()} == {
        "before_10": 1, "10_to_12": 2, "from_12": 2,
    }
    assert periods["10_to_12"]["best_open_cost_equal"] == 1
    assert periods["from_12"]["best_open_below_cost"] == 1


def test_weekly_maximum_and_inclusive_fifteen_clear_threshold():
    first, second, empty = "2026-01-05", "2026-01-12", "2026-01-19"
    rows = [
        {"session": first, "questions": [
            _question("A", f"{first}T10:{minute:02d}:00+09:00", "1", "1", "0" if minute == 0 else "1")
            for minute in range(15)
        ]},
        {"session": second, "questions": [
            _question("A", f"{second}T10:{minute:02d}:00+09:00", "1", "1", "1")
            for minute in range(14)
        ]},
        {"session": empty, "questions": []},
    ]
    report = summarize_capacity(rows)
    assert reconstruct_capacity(rows) == report
    group = report["groups"]["A"]
    assert group["zero_trade_weeks"] == 1
    assert group["weekly_counts"] == {
        "closed_activity_trades": {"maximum": 15, "weeks_at_least_15": 1},
        "best_open_cost_clearing": {"maximum": 15, "weeks_at_least_15": 1},
        "common_baseline_cost_clearing": {"maximum": 15, "weeks_at_least_15": 1},
        "common_candidate_cost_clearing": {"maximum": 14, "weeks_at_least_15": 0},
        "triple_best_open_cost_clearing": {"maximum": 15, "weeks_at_least_15": 1},
        "triple_baseline_cost_clearing": {"maximum": 15, "weeks_at_least_15": 1},
        "triple_candidate_cost_clearing": {"maximum": 14, "weeks_at_least_15": 0},
    }


def test_independent_reconstruction_detects_corrupted_saved_count():
    row = {"session": "2026-01-05", "questions": [
        _question("B", "2026-01-05T10:00:00+09:00", "1", "1", "0"),
    ]}
    saved = summarize_capacity([row])
    altered = deepcopy(saved)
    altered["groups"]["all"]["weekly"]["2026-W02"]["triple_common_support"]["candidate_cost_clearing"] += 1
    assert reconstruct_capacity([row]) == saved
    assert reconstruct_capacity([row]) != altered
