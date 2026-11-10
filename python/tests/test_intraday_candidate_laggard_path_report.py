from fractions import Fraction

import pytest

from research import intraday_candidate_laggard_path_report as report


def _closed(net):
    return {"status": "closed", "selected_trigger": "time", "gross": str(net + 33),
            "cost": "33", "net": str(net), "entry_notional": "10000",
            "entry_request_at": "2026-01-05T10:02:00+09:00",
            "fill": {"outcome": "modeled", "quantity": 1, "cost": "33"}}


def _inactive(status):
    unresolved = status == "unresolved"
    return {"status": status, "selected_trigger": "unknown" if unresolved else "retained", "fill": None,
            **dict.fromkeys(("gross", "cost", "net", "entry_notional"), None if unresolved else "0")}


def _row(index, path, original, day="2026-01-05"):
    prices = {"rebound": ("100", "80", "90"), "weak": ("100", "90", "90"),
              "unresolved": ("100", None, "90"), "retain": (None, None, None)}
    ready = path != "retain"
    other = _inactive("unresolved" if path == "unresolved" else "no_entry")
    question = {
        "status": "opportunity", "episode_origin_context": "other", "baseline": original,
        "candidate": original if path in ("rebound", "retain") else other,
        "weak": original if path in ("weak", "retain") else other,
        "path": {"state": path, "reason": path,
                 **dict(zip(("first_open", "mid_close", "last_close"), prices[path], strict=True))},
        "rank_decision": {"rank_state": "select" if ready else "decline",
                          "order_state": "ready" if ready else "decline"},
    }
    return {"session": day, "symbol": str(100_000 + index), "fold": 1, "roster": {"frame_states": {}},
            "questions": [question]}


def test_path_partition_preserves_cash_and_all_weeks():
    rows = [
        _row(0, "rebound", _closed(Fraction(5, 2))),
        _row(1, "weak", _closed(-7)),
        _row(2, "unresolved", _closed(3)),
        _row(3, "retain", _inactive("no_entry")),
        _row(4, "rebound", _inactive("no_fill")),
        _row(5, "weak", {**_inactive("unresolved"), "fill": {"outcome": "modeled"}}),
        _row(6, "retain", _inactive("unresolved")),
        _row(7, "retain", _inactive("no_entry"), day="2026-01-12"),
    ]
    result = report.summarize(iter(rows))
    assert result == report.summarize(rows, audit=True)
    assert result["paths"]["totals"] == {
        "rebound": {"coordinates": 2, "baseline_known": 2, "baseline_modeled_fills": 1, "baseline_closed": 1,
                    "baseline_cost_clearing": 1, "baseline_net_cash": "5/2"},
        "weak": {"coordinates": 2, "baseline_known": 1, "baseline_modeled_fills": 2, "baseline_closed": 1,
                 "baseline_cost_clearing": 0, "baseline_net_cash": "-7"},
        "unresolved": {"coordinates": 1, "baseline_known": 1, "baseline_modeled_fills": 1, "baseline_closed": 1,
                       "baseline_cost_clearing": 1, "baseline_net_cash": "3"},
        "retain": {"coordinates": 3, "baseline_known": 2, "baseline_modeled_fills": 0, "baseline_closed": 0,
                   "baseline_cost_clearing": 0, "baseline_net_cash": "0"},
    }
    for path, net, delta in (("rebound", "5/2", "7"), ("weak", "-7", "-5/2")):
        contrast = result[f"hold60_vs_{path}"]
        comparison = contrast["comparison"]["groups"]["all"]["totals"]
        assert comparison["baseline"]["net_cash"] == "-3/2"
        assert comparison["paired"]["closed_trades"] == 1
        assert comparison["paired"]["delta_net_cash"] == "0"
        assert contrast["execution"]["totals"]["common_known"] == {
            "coordinates": 5, "baseline_net_cash": "-9/2", "candidate_net_cash": net, "delta_net_cash": delta,
        }
        assert comparison["paired"]["known_outcomes"] < 5
        assert contrast["execution"]["weekly_counts"]["candidate"]["modeled_fills"]["zero_weeks"] == 1
        assert result["paths"]["weekly"]["2026-W03"][path]["coordinates"] == 0


@pytest.mark.parametrize(("path", "arm", "replacement", "message"), [
    ("rebound", "candidate", _closed(9), "changed the fixed trade"),
    ("weak", "candidate", _inactive("unresolved"), "unexpected trade or cash"),
    ("unresolved", "weak", _inactive("no_entry"), "unexpected trade or cash"),
    ("retain", "weak", _inactive("no_entry"), "changed the fixed trade"),
])
def test_rejects_changed_or_misclassified_projection(path, arm, replacement, message):
    original = _inactive("unresolved") if path == "retain" else _closed(5)
    row = _row(0, path, original)
    row["questions"][0][arm] = replacement
    with pytest.raises(ValueError, match=message):
        report.summarize([row], audit=True)


def test_equal_closes_cannot_be_reported_as_rebound():
    row = _row(0, "weak", _closed(5))
    row["questions"][0]["path"]["state"] = "rebound"
    with pytest.raises(ValueError, match="state differs"):
        report.summarize([row])
