from fractions import Fraction

import pytest

from research import intraday_candidate_laggard_hold_report as report


def _closed(net, trigger="time"):
    return {
        "status": "closed", "selected_trigger": trigger, "net": str(net),
        "gross": str(net + 33), "cost": "33", "entry_notional": "10000",
        "fill": {"outcome": "modeled", "quantity": 1, "cost": "33"},
        "requested_quantity": 1, "entry_request_at": "2026-01-05T10:02:00+09:00",
        "entry_execute_at": "2026-01-05T10:03:00+09:00", "entry_at": "2026-01-05T10:03:00+09:00",
        "entry_price": "10000",
    }


def _unresolved_exit(old):
    return {**old, "status": "unresolved", "selected_trigger": "unknown", "net": None,
            "gross": None, "cost": None, "entry_notional": None}


def _row(index, baseline, hold60, hold90, day="2026-01-05"):
    return {"session": day, "symbol": str(100_000 + index), "fold": 1, "roster": {"frame_states": {}},
            "questions": [{"status": "opportunity", "episode_origin_context": "other",
                           "baseline": baseline, "candidate": hold60, "hold90": hold90}]}


def test_time_cohort_deltas_exclude_unknown_exits_and_keep_zero_fill_weeks():
    equal = _closed(0)
    later_unknown = _closed(7)
    earlier_structural = _closed(-4, "structural")
    extended_structural = _closed(-3, "structural")
    no_entry = {"status": "no_entry", "selected_trigger": "retained", "net": "0", "fill": None}
    missing = {"status": "unresolved", "selected_trigger": "unknown", "net": None, "fill": None}
    rows = [
        _row(0, _closed(Fraction(9, 2)), _closed(Fraction(13, 2)), _closed(-2, "structural")),
        _row(1, _closed(-2), extended_structural, extended_structural),
        _row(2, equal, equal, _unresolved_exit(equal)),
        _row(3, later_unknown, _unresolved_exit(later_unknown), _unresolved_exit(later_unknown)),
        _row(4, earlier_structural, earlier_structural, earlier_structural),
        _row(5, no_entry, no_entry, no_entry, day="2026-01-12"),
        _row(6, missing, missing, missing),
    ]
    result = report.summarize(iter(rows))
    assert result == report.summarize(rows, audit=True)
    sixty, ninety = (result[name] for name in ("hold30_vs_hold60", "hold30_vs_hold90"))
    assert sixty["time_cohort"] == {
        "baseline_closed": 4, "candidate_known": 3, "candidate_closed": 3, "candidate_unresolved": 1,
        "candidate_structural": 1, "candidate_expiry": 2, "baseline_cost_clearing": 2,
        "common_baseline_cost_clearing": 1, "common_candidate_cost_clearing": 1,
        "common_baseline_net_cash": "5/2", "common_candidate_net_cash": "7/2", "delta_net_cash": "1",
        "improved": 1, "decreased": 1, "equal": 1,
    }
    assert ninety["time_cohort"] == {
        "baseline_closed": 4, "candidate_known": 2, "candidate_closed": 2, "candidate_unresolved": 2,
        "candidate_structural": 2, "candidate_expiry": 0, "baseline_cost_clearing": 2,
        "common_baseline_cost_clearing": 1, "common_candidate_cost_clearing": 0,
        "common_baseline_net_cash": "5/2", "common_candidate_net_cash": "-5", "delta_net_cash": "-15/2",
        "improved": 0, "decreased": 2, "equal": 0,
    }
    assert sixty["execution"]["totals"]["common_known"]["coordinates"] == 5
    assert ninety["execution"]["totals"]["common_known"]["coordinates"] == 4
    assert ninety["execution"]["totals"]["arms"]["candidate"]["unresolved_after_entry"] == 2
    assert ninety["execution"]["weekly_counts"]["candidate"]["modeled_fills"] == {
        "maximum": 5, "zero_weeks": 1, "weeks_at_least_15": 0,
    }
    assert sixty["comparison"]["groups"]["all"]["totals"]["baseline"]["net_cash"] == "11/2"


@pytest.mark.parametrize(("baseline", "update", "message"), [
    (_closed(3), {"entry_price": "10001"}, "changed the original entry"),
    (_closed(3), {"cost": "34"}, "invalid extension exit or entry basis"),
    (_closed(-4, "structural"), {"net": "-3"}, "changed a retained outcome"),
])
def test_rejects_changes_outside_the_time_exit(baseline, update, message):
    with pytest.raises(ValueError, match=message):
        report.summarize([_row(0, baseline, baseline, {**baseline, **update})], audit=True)
