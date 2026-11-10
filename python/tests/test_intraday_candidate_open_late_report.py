from fractions import Fraction
from statistics import median

import pytest

from research.intraday_candidate_open_late_report import selection_summary


def _arm(status, net=None):
    if status == "closed":
        return {"status": status, "net": str(net), "fill": {"outcome": "modeled"}}
    return {"status": status, "net": None if status == "unresolved" else "0", "fill": None}


def _rows(day, returns, cases):
    observed = sum(value is not None for value in returns)
    rows = []
    for index, value in enumerate(returns):
        peers = [peer for i, peer in enumerate(returns) if index != i and peer is not None]
        center = median(peers) if len(peers) >= 10 else None
        gate = "unresolved" if center is None else "select" if center > Fraction(33, 10_000) else "decline"
        late, baseline, candidate = cases.get(index, (Fraction(), _arm("no_entry"), _arm("no_entry")))
        state = "unresolved" if late is None else "select" if late > 0 else "decline"
        plan = {"opening_first_open": None if value is None else "10000",
                "opening_last_close": None if value is None else str(10000 * (1 + value)),
                "opening_return": None if value is None else str(value),
                "late_return": None if late is None else str(late),
                "opening_peer_count": len(peers), "opening_peer_median": None if center is None else str(center),
                "opening_gate": gate, "rank_state": state, "gated_state": gate if state == "select" else state,
                "order_state": "ready" if state == "select" else "retain"}
        rows.append({"session": day, "symbol": f"KRX:{index + 100000}",
                     "questions": [{"observed_opening_members": observed, "rank_decision": plan,
                                    "baseline": baseline, "candidate": candidate}]})
    return rows


def test_reports_weekly_opportunities_and_common_known_economics_by_gate():
    rise = Fraction(1, 100)
    rows = _rows("2025-09-04", [rise] * 15 + [None] * 5,
                 {0: (rise, _arm("closed", 5), _arm("closed", 5)),
                  1: (None, _arm("unresolved"), _arm("unresolved"))})
    rows += _rows("2025-09-11", [Fraction(33, 10_000)] * 20,
                  {0: (rise, _arm("closed", -2), _arm("no_entry")),
                   1: (rise, _arm("unresolved"), _arm("no_entry"))})
    rows += _rows("2025-09-18", [rise] * 10 + [None] * 10,
                  {0: (rise, _arm("closed", 3), _arm("unresolved"))})
    rows += _rows("2025-09-25", [None] * 20, {})
    rows[15]["questions"][0]["rank_decision"]["opening_first_open"] = "10000"
    rows[16]["questions"][0]["rank_decision"]["opening_last_close"] = "10000"

    result = selection_summary(rows)
    assert result == selection_summary(rows, audit=True)
    assert (result["sessions"], result["weeks"]) == (4, 4)
    totals = result["totals"]
    assert totals["coordinates"] == 80
    assert totals["opening_endpoints"] == {"observed": 45, "missing": 35}
    assert totals["opening_endpoint_patterns"] == {
        "both_observed": 45, "first_missing_only": 1, "last_missing_only": 1, "both_missing": 33,
    }
    assert totals["late_paths"] == {"observed": 79, "missing": 1}
    assert totals["opening_member_counts"]["10"] == 1
    assert totals["opening_peer_counts"]["9"] == 10
    assert totals["opening_gate_counts"] == {"select": 30, "decline": 20, "unresolved": 30}
    assert totals["eligible_opening_gate_counts"] == {"select": 1, "decline": 2, "unresolved": 1}
    assert totals["arms"]["baseline"] == {
        "selected": 4, "declined": 75, "unresolved": 1, "order_ready": 4, "order_declined": 0,
    }
    assert totals["arms"]["candidate"] == {
        "selected": 1, "declined": 77, "unresolved": 2, "order_ready": 1, "order_declined": 0,
    }
    assert result["weekly_counts"]["baseline"]["selected"] == {
        "maximum": 2, "zero_weeks": 1, "weeks_at_least_15": 0,
    }
    assert result["weekly_counts"]["candidate"]["selected"]["zero_weeks"] == 3
    passed, failed, unknown = (totals["common_known_by_opening_gate"][gate]
                               for gate in ("select", "decline", "unresolved"))
    assert passed == {"coordinates": 29, "both_closed": 1, "arms": {
        "baseline": {"closed": 1, "cost_clearing": 1, "net_cash": "5"},
        "candidate": {"closed": 1, "cost_clearing": 1, "net_cash": "5"},
    }}
    assert failed == {"coordinates": 19, "both_closed": 0, "arms": {
        "baseline": {"closed": 1, "cost_clearing": 0, "net_cash": "-2"},
        "candidate": {"closed": 0, "cost_clearing": 0, "net_cash": "0"},
    }}
    assert unknown["coordinates"] == 29
    assert unknown["arms"]["baseline"] == {"closed": 0, "cost_clearing": 0, "net_cash": "0"}


def test_rejects_tampered_peer_count_and_duplicate_coordinate():
    rows = _rows("2025-09-04", [Fraction(1, 100)] * 10 + [None] * 10, {})
    with pytest.raises(ValueError, match="unique coordinate"):
        selection_summary(rows + [rows[0]])
    with pytest.raises(ValueError, match="20 original members"):
        selection_summary(rows[:-1])
    rows[0]["questions"][0]["rank_decision"]["opening_peer_count"] = 10
    with pytest.raises(ValueError, match="leave-one-out support"):
        selection_summary(rows, audit=True)


def test_rejects_return_inconsistent_with_preserved_opening_endpoints():
    rows = _rows("2025-09-04", [Fraction(1, 100)] * 20, {})
    rows[0]["questions"][0]["rank_decision"]["opening_last_close"] = "10033"
    with pytest.raises(ValueError, match="return differs from endpoints"):
        selection_summary(rows, audit=True)
