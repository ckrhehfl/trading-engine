from copy import deepcopy

import pytest

from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_open_pullback_report as report


def _inactive(status):
    return laggard._inactive_arm(status, "synthetic")


def _closed(net):
    return {"status": "closed", "net": str(net), "cost": "33", "gross": str(net + 33),
            "entry_notional": "10000", "selected_trigger": "time",
            "fill": {"outcome": "modeled"}, "entry_request_at": "request"}


def _question(*, first="10000", mid="10066", last="10033", reference="10032", own="select",
              rebound="select", order="ready", baseline=None, candidate=None):
    plan = {"first_open": first, "mid_close": mid, "last_close": last,
            "rebound_reference_close": reference, "rank_state": own, "rebound_state": rebound,
            "gated_state": rebound if own == "select" else own, "order_state": order,
            "limit": last if own == "select" else None, "stop": "9900" if order == "ready" else None}
    if baseline is None:
        baseline = _closed(7) if order == "ready" else _inactive(
            "unresolved" if own == "unresolved" or order == "unresolved" else "no_entry")
    if candidate is None:
        candidate = baseline if own != "select" or rebound == "select" else _inactive(
            "unresolved" if rebound == "unresolved" else "no_entry")
    return {"status": "opportunity", "episode_origin_context": "other", "rank_decision": plan,
            "baseline": baseline, "candidate": candidate}


def _rows(day, *decisions):
    return [{"session": day, "symbol": str(100_000 + index), "fold": 1, "roster": {"frame_states": {}},
             "questions": [decisions[index] if index < len(decisions) else
                           _question(mid="10065", reference=None, own="decline", rebound="not_applicable",
                                     order="retain")]}
            for index in range(20)]


def test_keeps_mixed_support_and_exact_cash_across_all_weeks():
    rows = _rows("2026-01-05", _question(), _question(reference="10033", rebound="decline", baseline=_closed(-2)))
    rows += _rows("2026-01-12", _question(reference=None, rebound="unresolved", order="unresolved"),
                  _question(reference="10034", rebound="decline", order="unresolved"))
    rows += _rows("2026-01-19", _question(mid=None, reference=None, own="unresolved", rebound="not_applicable",
                                          order="retain"))
    result = report.summarize(rows)
    assert result == report.summarize(rows, audit=True)
    selection = result["selection"]
    assert selection["totals"]["eligible_rebound_states"] == {"select": 1, "decline": 2, "unresolved": 1}
    assert selection["totals"]["own_endpoints_missing"] == {"first_open": 0, "mid_close": 1, "last_close": 0}
    assert selection["totals"]["arms"]["baseline"]["order_unresolved"] == 2
    assert selection["totals"]["arms"]["candidate"]["order_ready"] == 1
    assert selection["weekly_counts"]["candidate"]["selected"]["zero_weeks"] == 2
    execution = result["execution"]["totals"]
    assert execution["common_known"] == {
        "coordinates": 57, "baseline_net_cash": "5", "candidate_net_cash": "7", "delta_net_cash": "2",
    }
    paired = result["comparison"]["groups"]["all"]["totals"]["paired"]
    assert paired["known_outcomes"] == 56
    assert paired["closed_trades"] == 1
    assert result["symbol_concentration"]["candidate"]["net_cash_without_largest_positive_symbol"] == "0"


@pytest.mark.parametrize("changed", [{"mid_close": "10065"}, {"last_close": "10032"},
                                     {"last_close": "10066"}, {"rebound_reference_close": "10033"}])
def test_rejects_threshold_tampering(changed):
    rows = _rows("2026-01-05", _question())
    rows[0]["questions"][0]["rank_decision"].update(changed)
    with pytest.raises(ValueError, match="own or rebound decision differs|outside own eligibility"):
        report.summarize(rows)


def test_rejects_treating_known_decline_as_missing_with_unknown_structure():
    question = _question(reference="10034", rebound="decline", order="unresolved")
    rows = _rows("2026-01-05", question)
    assert report.summarize(rows)["execution"]["totals"]["arms"]["candidate"]["no_entry"] == 20
    question["candidate"] = deepcopy(question["baseline"])
    with pytest.raises(ValueError, match="projected outcome differs"):
        report.summarize(rows, audit=True)


def test_requires_reference_bar_for_ready_geometry():
    rows = _rows("2026-01-05", _question(reference=None, rebound="unresolved", order="ready"))
    with pytest.raises(ValueError, match="entry geometry differs"):
        report.summarize(rows)


def test_rebound_preserves_the_same_execution():
    rows = _rows("2026-01-05", _question(candidate=_closed(8)))
    with pytest.raises(ValueError, match="projected outcome differs"):
        report.summarize(rows)
