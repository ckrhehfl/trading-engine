from fractions import Fraction

import pytest

from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_open_strength_report as report


def _rows(day, peer_end, complete=19, *, missing_structure=False):
    rows = []
    all_returns = [Fraction()] + [Fraction(peer_end, 10_000) - 1] * complete
    for index in range(20):
        first = 10_000
        mid, last = (10_066, 10_066) if index == 0 else (10_000, peer_end)
        pair_known = index <= complete
        peers = all_returns[:index] + all_returns[index + 1:] if pair_known else all_returns
        peers = sorted(peers)
        count = len(peers)
        center = None if count < 8 else (peers[(count - 1) // 2] + peers[count // 2]) / 2
        gate = "unresolved" if center is None else "select" if center < 0 else "decline"
        state = "select" if index == 0 else "decline" if pair_known else "unresolved"
        order = ("unresolved" if missing_structure else "ready") if index == 0 else "retain"
        plan = {"first_open": str(first), "mid_close": str(mid) if pair_known else None, "last_close": str(last),
                "own_return": str(Fraction(last, first) - 1), "peer_count": count,
                "peer_median": None if center is None else str(center), "peer_state": gate,
                "rank_state": state, "gated_state": gate if index == 0 else state, "order_state": order,
                "limit": str(last) if index == 0 else None,
                "stop": "9900" if order == "ready" else None}
        if order == "ready":
            old = {"status": "closed", "net": "7", "cost": "33", "gross": "40", "entry_notional": "10000",
                   "selected_trigger": "time", "fill": {"outcome": "modeled"}, "entry_request_at": "request"}
        else:
            status = "unresolved" if order == "unresolved" or state == "unresolved" else "no_entry"
            old = laggard._inactive_arm(status, "original")
        new = old if index != 0 or gate == "select" else laggard._inactive_arm(
            "unresolved" if gate == "unresolved" else "no_entry", "peer")
        rows.append({"session": day, "symbol": str(100_000 + index), "fold": 1, "roster": {"frame_states": {}},
                     "questions": [{"status": "opportunity", "episode_origin_context": "other",
                                    "rank_decision": plan, "observed_peer_members": len(all_returns),
                                    "baseline": old, "candidate": new}]})
    return rows


def test_distinguishes_peer_gate_unknown_and_geometry_unknown():
    rows = _rows("2026-01-05", 9990)
    rows += _rows("2026-01-12", 10_000, missing_structure=True)
    rows += _rows("2026-01-19", 9990, complete=7)
    result = report.summarize(rows)
    assert result == report.summarize(rows, audit=True)
    totals = result["selection"]["totals"]
    assert totals["eligible_peer_states"] == {"select": 1, "decline": 1, "unresolved": 1}
    assert totals["own_endpoints_missing"]["mid_close"] == 12
    assert totals["arms"]["baseline"]["order_ready"] == 2
    assert totals["arms"]["candidate"]["order_ready"] == 1
    execution = result["execution"]["totals"]
    assert execution["arms"]["baseline"]["closed"] == 2 and execution["arms"]["candidate"]["closed"] == 1
    assert execution["common_known"] == {
        "coordinates": 46, "baseline_net_cash": "7", "candidate_net_cash": "7", "delta_net_cash": "0",
    }
    assert result["selection"]["weekly_counts"]["candidate"]["selected"]["zero_weeks"] == 2


def test_rejects_wrong_peer_count_and_reclassifying_known_nontrade():
    rows = _rows("2026-01-05", 10_000, missing_structure=True)
    first, = rows[0]["questions"]
    first["rank_decision"]["peer_count"] = 20
    with pytest.raises(ValueError, match="leave-one-out decision differs"):
        report.summarize(rows, audit=True)
    first["rank_decision"]["peer_count"] = 19
    first["candidate"] = first["baseline"]
    with pytest.raises(ValueError, match="projected outcome differs"):
        report.summarize(rows)
