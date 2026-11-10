from copy import deepcopy

import pytest

from research import intraday_candidate_laggard_retry_report as report


def _arm(status, *, requested=False, modeled=False, net=None):
    fill = {"outcome": "modeled" if modeled else "declined", "quantity": int(modeled)} if requested else None
    return {"status": status, "selected_trigger": "time" if status == "closed" else "retained", "fill": fill,
            "entry_request_at": "request" if requested else None, "requested_quantity": int(requested),
            "entry_notional": "10000" if status == "closed" else None if status == "unresolved" else "0",
            "gross": str(net + 33) if status == "closed" else None if status == "unresolved" else "0",
            "cost": "33" if status == "closed" else None if status == "unresolved" else "0",
            "net": str(net) if status == "closed" else None if status == "unresolved" else "0"}


def _row(symbol, state, old, new, *, day="2026-01-05", original_open="101"):
    return {"session": day, "symbol": symbol, "fold": 1, "roster": {"frame_states": {}},
            "questions": [{"status": "opportunity", "episode_origin_context": "other", "baseline": old,
                           "candidate": new, "rank_decision": {"limit": "100"},
                           "retry": {"state": state, "reason": state, "original_open": original_open,
                                     "quantity": old["requested_quantity"]}}]}


def test_counts_both_attempts_and_common_cash():
    held = _arm("closed", requested=True, modeled=True, net=8)
    skipped = _arm("no_fill", requested=True)
    rows = [
        _row("A", "retain", held, held, original_open="99"),
        _row("B", "retry", skipped, _arm("closed", requested=True, modeled=True, net=-7)),
        _row("C", "unresolved", skipped, _arm("unresolved")),
        _row("D", "retry", skipped, _arm("unresolved", requested=True, modeled=True)),
        _row("E", "retry", skipped, skipped),
        _row("F", "retain", _arm("no_entry"), _arm("no_entry"), day="2026-01-12", original_open=None),
    ]
    result = report.summarize(rows)
    assert result == report.summarize(rows, audit=True)
    totals = result["retry"]["totals"]
    assert totals["first_requests"] == 5 and totals["added_requests"] == 3
    assert totals["candidate_total_requests"] == 8
    assert totals["newly_unresolved"] == 2
    assert result["execution"]["totals"]["arms"]["candidate"]["requests"] == 4
    assert totals["initial_above_limit"] == 4 and totals["added_modeled_fills"] == 2
    assert totals["added_closed"] == totals["added_unresolved_after_fill"] == totals["added_no_fill"] == 1
    assert totals["added_net_cash"] == "-7"
    assert result["execution"]["totals"]["common_known"] == {
        "coordinates": 4, "baseline_net_cash": "8", "candidate_net_cash": "1", "delta_net_cash": "-7",
    }
    panel = result["comparison"]["groups"]["all"]["totals"]
    assert panel["paired"]["closed_trades"] == 1
    assert panel["paired"]["known_outcomes"] == 3 and panel["newly_unresolved"] == 0
    assert result["retry"]["weekly"]["2026-W03"]["added_requests"] == 0
    assert result["retry"]["weekly"]["2026-W03"]["candidate_total_requests"] == 0
    assert result["symbol_concentration"]["candidate"]["net_cash_without_largest_positive_symbol"] == "-7"


def test_rejects_replacing_original_fill_or_increasing_quantity():
    old = _arm("no_fill", requested=True)
    retry = _row("A", "retry", old, deepcopy(old))
    retry["questions"][0]["candidate"]["requested_quantity"] = 2
    with pytest.raises(ValueError, match="added request differs"):
        report.summarize([retry], audit=True)

    filled = _arm("closed", requested=True, modeled=True, net=1)
    changed = _row("A", "retain", filled, old, original_open="99")
    with pytest.raises(ValueError, match="retained original outcome differs"):
        report.summarize([changed])
