"""Invented selection receipts for EJ's rank-policy support report."""

from datetime import date, timedelta

import pytest

from research import intraday_candidate_laggard_report as report


def _day(decision, observed, selected, median, orders=()):
    symbols = {}
    for index in range(20):
        eligible = median is not None and index < observed
        rank = ("select" if index < selected else "decline") if eligible else "unresolved"
        gated = "decline" if rank == "select" and median.startswith("-") else rank
        order = (orders[index] if orders else "ready") if rank == "select" else "retain"
        returns = "-1/100" if rank == "select" else (median or "0")
        symbols[f"{100_000 + index}"] = {"rank_state": rank, "gated_state": gated, "order_state": order,
                                           "return": returns if index < observed else None}
    return {"decision_at": decision, "observed": observed, "median_return": median, "symbols": symbols}


def test_missing_quorum_missing_endpoint_and_unready_order_are_separate():
    days = [
        _day("2026-01-04T17:02:00-08:00", 15, 3, "0", ("ready", "decline", "unresolved")),
        _day("2026-01-12T10:02:00+09:00", 9, 0, None),
        _day("2026-01-19T01:02:00+00:00", 20, 4, "-1/200"),
    ]
    summary = report.selection_summary(iter(days))
    assert summary == report.selection_summary(reversed(days), audit=True)
    assert summary["sessions"] == 3
    assert summary["weeks"] == 3
    assert summary["totals"] == {
        "sessions": 3, "coordinates": 60, "observed_endpoints": 44, "missing_endpoints": 16,
        "quorum_supported_sessions": 2, "quorum_missing_sessions": 1,
        "median_sign_counts": {"negative": 1, "zero": 1, "positive": 0},
        "arms": {
            "baseline": {"selected": 7, "declined": 28, "unresolved": 25,
                         "order_ready": 5, "order_declined": 1, "order_unresolved": 1},
            "candidate": {"selected": 3, "declined": 32, "unresolved": 25,
                          "order_ready": 1, "order_declined": 1, "order_unresolved": 1},
        },
    }
    assert list(summary["weekly"]) == ["2026-W02", "2026-W03", "2026-W04"]
    assert summary["weekly"]["2026-W03"]["arms"]["baseline"]["unresolved"] == 20
    assert summary["weekly"]["2026-W04"]["arms"]["candidate"]["selected"] == 0


def test_selected_and_order_ready_weekly_frequencies_use_the_same_calendar():
    days = []
    for index in range(5):
        day = date(2026, 1, 5) + timedelta(days=index)
        orders = ("ready", "unresolved", "ready") if index == 0 else ()
        days.append(_day(f"{day}T10:02:00+09:00", 15, 3, "1/200", orders))
    days.append(_day("2026-01-12T10:02:00+09:00", 20, 0, "0"))

    summary = report.selection_summary(days)
    assert summary == report.selection_summary(days, audit=True)
    expected = {"selected": {"maximum": 15, "zero_weeks": 1, "weeks_at_least_15": 1},
                "order_ready": {"maximum": 14, "zero_weeks": 1, "weeks_at_least_15": 0}}
    assert summary["weekly_counts"] == {"baseline": expected, "candidate": expected}
    assert summary["totals"]["median_sign_counts"] == {"negative": 0, "zero": 1, "positive": 5}


def test_independent_summary_detects_a_producer_counting_error(monkeypatch):
    days = [_day("2026-01-05T10:02:00+09:00", 10, 2, "0")]
    original = report._add_day

    def corrupt(panel, day, median):
        original(panel, day, median)
        panel["arms"]["candidate"]["order_ready"] -= 1

    monkeypatch.setattr(report, "_add_day", corrupt)
    assert report.selection_summary(days)["totals"]["arms"]["candidate"]["order_ready"] == 1
    assert report.selection_summary(days, audit=True)["totals"]["arms"]["candidate"]["order_ready"] == 2


def test_duplicate_and_inconsistent_daily_receipts_fail_closed():
    day = _day("2026-01-05T10:02:00+09:00", 10, 2, "0")
    with pytest.raises(ValueError, match="duplicate session"):
        report.selection_summary([day, {**day, "decision_at": "2026-01-05T01:02:00+00:00"}], audit=True)
    with pytest.raises(ValueError, match="endpoint count"):
        report.selection_summary([{**day, "observed": 11}])
    with pytest.raises(ValueError, match="day coverage or decision clock"):
        report.selection_summary([{**day, "decision_at": "2026-01-05T10:02:00"}])
