from fractions import Fraction

import pytest

from research import intraday_candidate_laggard_peers_report as report


def _closed(net):
    return {"status": "closed", "selected_trigger": "time", "gross": str(net + 33),
            "cost": "33", "net": str(net), "entry_notional": "10000",
            "entry_request_at": "2026-01-05T10:02:00+09:00",
            "fill": {"outcome": "modeled", "quantity": 1, "cost": "33"}}


def _inactive(status):
    unresolved = status == "unresolved"
    return {"status": status, "selected_trigger": "unknown" if unresolved else "retained", "fill": None,
            **dict.fromkeys(("gross", "cost", "net", "entry_notional"), None if unresolved else "0")}


def _row(index, state, original, day="2026-01-05"):
    if state == "retain":
        first, mid, last, count, target, center = (None,) * 6
    else:
        first, mid, last = "100", "90", "90"
        count, target, center = {
            "weaker": (8, "0", "1/100"),
            "other": (19, "0", "0"),
            "unresolved": (7, "0", None),
        }[state]
    other = _inactive("unresolved" if state == "unresolved" else "no_entry")
    question = {
        "status": "opportunity", "episode_origin_context": "other", "baseline": original,
        "candidate": original if state in ("weaker", "retain") else other,
        "other": original if state in ("other", "retain") else other,
        "path": {"state": "retain" if state == "retain" else "weak",
                 "first_open": first, "mid_close": mid, "last_close": last},
        "peer": {"state": state, "peer_count": count, "target_return": target, "peer_median": center},
        "rank_decision": {"rank_state": "decline" if state == "retain" else "select",
                          "order_state": "decline" if state == "retain" else "ready"},
    }
    return {"session": day, "symbol": str(100_000 + index), "fold": 1, "roster": {"frame_states": {}},
            "questions": [question]}


def test_peer_partition_preserves_cash_and_original_weeks():
    rows = [
        _row(0, "weaker", _closed(Fraction(5, 2))),
        _row(1, "other", _closed(-7)),
        _row(2, "unresolved", _closed(3)),
        _row(3, "retain", _inactive("no_entry")),
        _row(4, "weaker", _inactive("no_fill")),
        _row(5, "other", {**_inactive("unresolved"), "fill": {"outcome": "modeled"}}),
        _row(6, "retain", _inactive("unresolved")),
        _row(7, "retain", _inactive("no_entry"), day="2026-01-12"),
        _row(8, "unresolved", _inactive("no_fill")),
    ]
    missing_target = rows[8]["questions"][0]["peer"]
    missing_target.update(peer_count=8, peer_median="0", target_return=None)
    result = report.summarize(iter(rows))
    assert result == report.summarize(rows, audit=True)
    assert result["peers"]["totals"] == {
        "weaker": {"coordinates": 2, "baseline_known": 2, "baseline_modeled_fills": 1, "baseline_closed": 1,
                   "baseline_cost_clearing": 1, "baseline_net_cash": "5/2", "peer_counts": {"8": 2}},
        "other": {"coordinates": 2, "baseline_known": 1, "baseline_modeled_fills": 2, "baseline_closed": 1,
                  "baseline_cost_clearing": 0, "baseline_net_cash": "-7", "peer_counts": {"19": 2}},
        "unresolved": {"coordinates": 2, "baseline_known": 2, "baseline_modeled_fills": 1, "baseline_closed": 1,
                       "baseline_cost_clearing": 1, "baseline_net_cash": "3", "peer_counts": {"7": 1, "8": 1}},
        "retain": {"coordinates": 3, "baseline_known": 2, "baseline_modeled_fills": 0, "baseline_closed": 0,
                   "baseline_cost_clearing": 0, "baseline_net_cash": "0", "peer_counts": {}},
    }
    for state, net, delta in (("weaker", "5/2", "7"), ("other", "-7", "-5/2")):
        contrast = result[f"weak_vs_{state}"]
        comparison = contrast["comparison"]["groups"]["all"]["totals"]
        assert comparison["baseline"]["net_cash"] == "-3/2"
        assert comparison["paired"]["closed_trades"] == 1
        assert comparison["paired"]["delta_net_cash"] == "0"
        assert contrast["execution"]["totals"]["common_known"] == {
            "coordinates": 5, "baseline_net_cash": "-9/2", "candidate_net_cash": net, "delta_net_cash": delta,
        }
        assert comparison["paired"]["known_outcomes"] < 5
        assert contrast["execution"]["weekly_counts"]["candidate"]["modeled_fills"]["zero_weeks"] == 1
        assert result["peers"]["weekly"]["2026-W03"][state]["coordinates"] == 0


@pytest.mark.parametrize(("state", "arm", "replacement", "message"), [
    ("weaker", "candidate", _closed(9), "changed the fixed trade"),
    ("other", "candidate", _inactive("unresolved"), "unexpected trade or cash"),
    ("unresolved", "other", _inactive("no_entry"), "unexpected trade or cash"),
    ("retain", "other", _inactive("no_entry"), "changed the fixed trade"),
])
def test_rejects_changed_or_misclassified_projection(state, arm, replacement, message):
    original = _inactive("unresolved") if state == "retain" else _closed(5)
    row = _row(0, state, original)
    row["questions"][0][arm] = replacement
    with pytest.raises(ValueError, match=message):
        report.summarize([row], audit=True)


@pytest.mark.parametrize(("update", "message"), [
    ({"state": "weaker"}, "state differs"),
    ({"target_return": "-1/10"}, "target return differs"),
    ({"peer_count": 7}, "median and quorum differ"),
    ({"peer_median": None}, "median and quorum differ"),
])
def test_rejects_inconsistent_sealed_peer_evidence(update, message):
    row = _row(0, "other", _closed(5))
    row["questions"][0]["peer"].update(update)
    with pytest.raises(ValueError, match=message):
        report.summarize([row])


def test_largest_positive_symbol_groups_trades_and_breaks_ties_alphabetically():
    rows = [
        _row(1, "weaker", _closed(Fraction(5, 2))),
        _row(1, "weaker", _closed(Fraction(5, 2)), day="2026-01-12"),
        _row(0, "weaker", _closed(5)),
        _row(2, "other", _closed(-7)),
        _row(3, "other", _closed(0)),
    ]
    result = report.summarize(rows)
    assert result == report.summarize(rows, audit=True)
    for state, largest, remaining in (("weaker", "100000", "5"), ("other", None, "-7")):
        assert result[f"weak_vs_{state}"]["symbol_concentration"] == {
            "baseline": {"largest_positive_symbol": "100000", "net_cash_without_largest_positive_symbol": "-2"},
            "candidate": {"largest_positive_symbol": largest, "net_cash_without_largest_positive_symbol": remaining},
        }


def test_symbol_audit_rejects_reallocation_with_unchanged_total():
    rows = [_row(0, "weaker", _closed(5)), _row(1, "weaker", _closed(2))]
    comparison = report.cash_summary.summarize(rows)
    comparison["by_symbol"]["100000"]["baseline"]["net_cash"] = "2"
    comparison["by_symbol"]["100001"]["baseline"]["net_cash"] = "5"
    with pytest.raises(ValueError, match="symbol cash does not reconcile"):
        report._reference_symbol_concentration(rows, comparison)
