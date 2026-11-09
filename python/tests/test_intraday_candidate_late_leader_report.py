"""Invented receipts for the late-session leader support report."""

from copy import deepcopy
from fractions import Fraction
from statistics import median

import pytest

from research import intraday_candidate_late_leader_report as report


def _day(decision, leaders, targets, flat_rank=None, unready_rank=None):
    observed = [Fraction(value) for value in leaders if value is not None]
    center = median(observed) if len(observed) >= 4 else None
    gate = "unresolved" if center is None else "select" if center > Fraction(33, 10_000) else "decline"
    symbols = {}
    for rank, value in enumerate((*leaders, *targets), 1):
        baseline = "decline"
        if rank > 5:
            if value is None:
                baseline = "unresolved"
            elif 0 <= Fraction(value) <= Fraction(33, 10_000):
                baseline = "select"
        order, limit, stop = "retain", None, None
        if baseline == "select":
            order, limit, stop = "ready", "100", "99"
            if rank == flat_rank:
                order, stop = "decline", "100"
            elif rank == unready_rank:
                order, stop = "unresolved", None
        symbols[f"{100_000 + rank}"] = {
            "rank": rank, "return": value, "rank_state": baseline,
            "gated_state": gate if baseline == "select" else baseline,
            "order_state": order, "limit": limit, "stop": stop, "reason": "invented",
        }
    return {"decision_at": decision, "observed_leaders": len(observed),
            "leader_median_return": None if center is None else str(center),
            "leader_state": gate, "symbols": symbols}


def test_weekly_support_keeps_leaders_and_missing_targets_separate():
    days = [
        _day("2026-01-04T21:14:00-08:00", ("1/100",) * 4 + (None,), ("0",) * 15, flat_rank=20),
        _day("2026-01-06T14:14:00+09:00", ("1/100",) * 5,
             ("33/10000", "-1/100", "1/100") + (None,) * 12),
        _day("2026-01-12T05:14:00+00:00", ("33/10000",) * 5, ("0",) * 15),
        _day("2026-01-19T14:14:00+09:00", ("1/100",) * 3 + (None,) * 2,
             ("0",) + (None,) * 14, unready_rank=6),
    ]
    summary = report.selection_summary(iter(days))
    assert summary == report.selection_summary(reversed(days), audit=True)
    assert (summary["sessions"], summary["weeks"]) == (4, 3)
    assert summary["totals"] == {
        "sessions": 4, "coordinates": 80, "leader_coordinates": 20, "target_coordinates": 60,
        "observed_leader_counts": {"0": 0, "1": 0, "2": 0, "3": 1, "4": 1, "5": 2},
        "quorum_supported_sessions": 3, "quorum_missing_sessions": 1,
        "leader_state_counts": {"select": 2, "decline": 1, "unresolved": 1},
        "observed_target_paths": 34, "missing_target_paths": 26,
        "arms": {
            "baseline": {"selected": 32, "declined": 2, "unresolved": 26,
                         "order_ready": 30, "order_declined": 1, "order_unresolved": 1},
            "candidate": {"selected": 16, "declined": 17, "unresolved": 27,
                          "order_ready": 15, "order_declined": 1, "order_unresolved": 0},
        },
    }
    assert list(summary["weekly"]) == ["2026-W02", "2026-W03", "2026-W04"]
    assert summary["weekly_counts"] == {
        "baseline": {
            "selected": {"maximum": 16, "zero_weeks": 0, "weeks_at_least_15": 2},
            "order_ready": {"maximum": 15, "zero_weeks": 1, "weeks_at_least_15": 2},
        },
        "candidate": {
            "selected": {"maximum": 16, "zero_weeks": 2, "weeks_at_least_15": 1},
            "order_ready": {"maximum": 15, "zero_weeks": 2, "weeks_at_least_15": 1},
        },
    }
    assert summary["weekly"]["2026-W03"]["arms"]["candidate"]["selected"] == 0
    assert summary["weekly"]["2026-W04"]["arms"]["candidate"]["unresolved"] == 15


def test_empty_support_week_is_retained():
    day = _day("2026-01-05T14:14:00+09:00", (None,) * 5, (None,) * 15)
    summary = report.selection_summary([day])
    assert summary == report.selection_summary([day], audit=True)
    assert summary["weekly"]["2026-W02"]["observed_leader_counts"]["0"] == 1
    assert summary["totals"]["arms"]["baseline"]["unresolved"] == 15
    assert summary["weekly_counts"]["candidate"]["order_ready"]["zero_weeks"] == 1


def test_independent_summary_detects_a_producer_counting_error(monkeypatch):
    day = _day("2026-01-05T14:14:00+09:00", ("1/100",) * 5, ("0",) * 15)
    original = report._add_day

    def corrupt(panel, observed, gate, targets):
        original(panel, observed, gate, targets)
        panel["arms"]["candidate"]["order_ready"] -= 1

    monkeypatch.setattr(report, "_add_day", corrupt)
    assert report.selection_summary([day])["totals"]["arms"]["candidate"]["order_ready"] == 14
    assert report.selection_summary([day], audit=True)["totals"]["arms"]["candidate"]["order_ready"] == 15


def test_inconsistent_receipts_and_duplicate_sessions_fail_closed():
    day = _day("2026-01-05T14:14:00+09:00", ("1/100",) * 5, ("0",) * 15)
    invalid_day = (
        ({**day, "observed_leaders": 4}, "leader support or gate"),
        ({**day, "leader_median_return": "1/50"}, "leader support or gate"),
        ({**day, "decision_at": "2026-01-05T14:14:00"}, "day coverage or decision clock"),
    )
    for invalid, reason in invalid_day:
        with pytest.raises(ValueError, match=reason):
            report.selection_summary([invalid])

    invalid_symbol = (
        ("100001", "rank", 2, "one member per rank"),
        ("100001", "gated_state", "select", "signal member cannot be a target"),
        ("100006", "gated_state", "unresolved", "target selection or order support"),
        ("100006", "stop", "100", "order geometry"),
    )
    for symbol, field, value, reason in invalid_symbol:
        invalid = deepcopy(day)
        invalid["symbols"][symbol][field] = value
        with pytest.raises(ValueError, match=reason):
            report.selection_summary([invalid], audit=True)

    with pytest.raises(ValueError, match="duplicate session"):
        report.selection_summary([day, {**day, "decision_at": "2026-01-05T05:14:00+00:00"}], audit=True)
