"""Invented coordinate receipts for the shock support report."""

import pytest

from research import intraday_candidate_shock_report as report
from research.intraday_ed_inputs import MINUTE
from research.intraday_pattern_calibration import session_open


def _row(day, symbol, offset=None, *, unknown=(), ratio=None, stop="99"):
    if offset is None:
        plan = {"decision_at": None, "rank_state": "unresolved" if unknown else "decline",
                "gated_state": "unresolved" if unknown else "decline", "order_state": "retain",
                "limit": None, "stop": None, "reason": "no_event", "volume_reason": "not_applicable"}
    else:
        plan = {"decision_at": (session_open(day) + offset * MINUTE).isoformat(), "rank_state": "select",
                "gated_state": "unresolved" if ratio is None else "select" if ratio == "2" else "decline",
                "order_state": "decline" if stop == "100" else "ready", "limit": "100", "stop": stop,
                "reason": "price_event", "volume_reason": "zero_reference" if ratio is None else "ratio_known"}
    plan.update(decision_offset=offset, unknown_offsets=list(unknown), volume_ratio=ratio)
    return {"session": day, "symbol": symbol, "questions": [{"rank_decision": plan}]}


def test_weekly_support_keeps_missing_and_unexamined_windows_distinct():
    rows = [
        _row("2026-01-05", "100001", 62, ratio="2"),
        _row("2026-01-05", "100002", 122, unknown=(62,), ratio="3/2"),
        _row("2026-01-05", "100003"),
        _row("2026-01-06", "100001", 182, unknown=(122,), stop="100"),
        _row("2026-01-06", "100002", unknown=(62, 182)),
        _row("2026-01-12", "100001"),
        _row("2026-01-12", "100002", unknown=(122,)),
    ]
    summary = report.selection_summary(iter(rows))
    assert summary == report.selection_summary(reversed(rows), audit=True)
    assert (summary["sessions"], summary["weeks"]) == (3, 2)
    assert summary["totals"] == {
        "sessions": 3, "coordinates": 7,
        "arms": {
            "baseline": {"selected": 3, "declined": 2, "unresolved": 2, "order_ready": 2, "order_declined": 1},
            "candidate": {"selected": 1, "declined": 3, "unresolved": 3, "order_ready": 1, "order_declined": 0},
        },
        "price_checks": {
            "62": {"observed": 5, "missing": 2, "unexamined": 0},
            "122": {"observed": 4, "missing": 2, "unexamined": 1},
            "182": {"observed": 4, "missing": 1, "unexamined": 2},
        },
        "first_price_events_by_offset": {"62": 1, "122": 1, "182": 1},
        "first_volume_events_by_offset": {"62": 1, "122": 0, "182": 0},
        "selected_with_earlier_unknown": 2,
        "volume_evidence": {"known": 2, "unresolved": 1, "not_applicable": 4},
        "volume_reason_counts": {"ratio_known": 2, "zero_reference": 1},
    }
    assert list(summary["weekly"]) == ["2026-W02", "2026-W03"]
    assert summary["weekly"]["2026-W03"]["arms"]["baseline"]["selected"] == 0
    assert summary["weekly_counts"]["candidate"]["order_ready"]["zero_weeks"] == 1


def test_weekly_target_includes_exact_threshold_and_order_decline():
    rows = [_row("2026-01-02", str(symbol), 62, ratio="2", stop="100" if symbol == 14 else "99")
            for symbol in range(15)]
    summary = report.selection_summary(rows, audit=True)
    assert summary == report.selection_summary(rows)
    assert summary["weekly_counts"]["candidate"] == {
        "selected": {"maximum": 15, "zero_weeks": 0, "weeks_at_least_15": 1},
        "order_ready": {"maximum": 14, "zero_weeks": 0, "weeks_at_least_15": 0},
    }


def test_independent_summary_detects_producer_counting_error(monkeypatch):
    row = _row("2026-01-05", "100001", 122, unknown=(62,), ratio="2")
    original = report._add_plan

    def corrupt(panel, plan):
        original(panel, plan)
        panel["selected_with_earlier_unknown"] = 0

    monkeypatch.setattr(report, "_add_plan", corrupt)
    assert report.selection_summary([row])["totals"]["selected_with_earlier_unknown"] == 0
    assert report.selection_summary([row], audit=True)["totals"]["selected_with_earlier_unknown"] == 1


@pytest.mark.parametrize(("changes", "reason"), [
    ({"unknown_offsets": [182]}, "first event or earlier gaps"),
    ({"unknown_offsets": [62, 62]}, "price support"),
    ({"decision_at": "2026-01-05T09:00:00+09:00"}, "decision clock"),
    ({"decision_offset": True}, "first event or earlier gaps"),
    ({"stop": "100"}, "order geometry"),
    ({"volume_ratio": "199/100"}, "volume gate"),
    ({"volume_ratio": "-1"}, "negative volume ratio"),
])
def test_inconsistent_selection_receipts_fail_closed(changes, reason):
    row = _row("2026-01-05", "100001", 122, ratio="2")
    row["questions"][0]["rank_decision"].update(changes)
    with pytest.raises(ValueError, match=reason):
        report.selection_summary([row], audit=True)


def test_duplicate_coordinate_fails_closed():
    row = _row("2026-01-05", "100001")
    with pytest.raises(ValueError, match="unique coordinate"):
        report.selection_summary([row, row])
