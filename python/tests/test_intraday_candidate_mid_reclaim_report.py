import pytest

from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_mid_reclaim_report as report


def _inactive(status):
    return laggard._inactive_arm(status, "synthetic")


def _closed(net):
    return {"status": "closed", "net": str(net), "cost": "33", "gross": str(net + 33),
            "entry_notional": "10000", "selected_trigger": "time",
            "fill": {"outcome": "modeled"}, "entry_request_at": "request"}


def _question(*, baseline=None, candidate=None, **changes):
    plan = {"rank_state": "select", "gated_state": "select", "order_state": "ready",
            "reason": "order_ready", "support_low": "10001/100", "break_close": "100",
            "reclaim_close": "101", "hold_close_117": "5001/50", "hold_close_118": "501/5",
            "limit": "101", "stop": "99", "hold_state": "select", **changes}
    if baseline is None:
        baseline = _closed(7)
    if candidate is None:
        candidate = baseline
    return {"status": "opportunity", "episode_origin_context": "other", "rank_decision": plan,
            "baseline": baseline, "candidate": candidate}


def _nonselection(*, unresolved=False):
    state = "unresolved" if unresolved else "decline"
    return _question(rank_state=state, gated_state=state, order_state="retain", hold_state="not_applicable",
                     reason="own_prices_unavailable" if unresolved else "break_close_not_below_support",
                     support_low=None if unresolved else "100", hold_close_117=None, hold_close_118=None,
                     limit=None, stop=None, baseline=_inactive("unresolved" if unresolved else "no_entry"))


def _rows(day, *questions):
    return [{"session": day, "symbol": str(100_000 + index), "fold": 1, "roster": {"frame_states": {}},
             "questions": [questions[index] if index < len(questions) else _nonselection()]}
            for index in range(20)]


def test_keeps_mixed_support_and_exact_cash_across_original_weeks():
    rows = _rows("2026-01-05", _question(),
                 _question(hold_close_118="10001/100", hold_state="decline", gated_state="decline",
                           baseline=_closed(-2), candidate=_inactive("no_entry")))
    rows += _rows("2026-01-12",
                  _question(hold_close_118=None, hold_state="unresolved", gated_state="unresolved",
                            order_state="unresolved", reason="structure_prices_unavailable", stop=None,
                            baseline=_inactive("unresolved")),
                  _question(hold_close_117=None, hold_close_118="100", hold_state="decline", gated_state="decline",
                            order_state="unresolved", reason="structure_prices_unavailable", stop=None,
                            baseline=_inactive("unresolved"), candidate=_inactive("no_entry")))
    rows += _rows("2026-01-19", _nonselection(unresolved=True))

    result = report.summarize(rows)

    assert result == report.summarize(rows, audit=True)
    selection = result["selection"]
    assert selection["totals"]["eligible_hold_states"] == {"select": 1, "decline": 2, "unresolved": 1}
    assert selection["totals"]["own_endpoints_missing"] == {"support_low": 1, "break_close": 0, "reclaim_close": 0}
    assert selection["totals"]["arms"]["baseline"]["order_unresolved"] == 2
    assert selection["totals"]["arms"]["candidate"]["order_ready"] == 1
    assert selection["weekly_counts"]["candidate"]["selected"]["zero_weeks"] == 2
    assert result["execution"]["totals"]["common_known"] == {
        "coordinates": 57, "baseline_net_cash": "5", "candidate_net_cash": "7", "delta_net_cash": "2",
    }
    paired = result["comparison"]["groups"]["all"]["totals"]["paired"]
    assert paired["known_outcomes"] == 56
    assert paired["closed_trades"] == 1
    assert result["symbol_concentration"]["candidate"]["net_cash_without_largest_positive_symbol"] == "0"


@pytest.mark.parametrize("field", ["break_close", "reclaim_close", "hold_close_117", "hold_close_118"])
def test_rejects_claimed_selection_when_a_close_equals_support(field):
    question = _question(**{field: "10001/100"})
    with pytest.raises(ValueError, match="own or hold decision differs"):
        report.summarize(_rows("2026-01-05", question))


@pytest.mark.parametrize("change", [{"limit": "102"}, {"stop": "10001/100"}, {"reason": "limit_at_stop"}])
def test_rejects_inconsistent_order_evidence(change):
    with pytest.raises(ValueError, match="entry geometry or reason differs"):
        report.summarize(_rows("2026-01-05", _question(**change)))


def test_ready_geometry_requires_both_hold_bars():
    question = _question(hold_close_118=None, hold_state="unresolved", gated_state="unresolved")
    with pytest.raises(ValueError, match="entry geometry or reason differs"):
        report.summarize(_rows("2026-01-05", question))


def test_outside_own_selection_cannot_have_hold_evidence():
    question = _nonselection()
    question["rank_decision"]["hold_close_117"] = "100"
    with pytest.raises(ValueError, match="entry geometry or reason differs"):
        report.summarize(_rows("2026-01-05", question))


def test_hold_failure_is_known_no_entry_even_when_geometry_is_unknown():
    question = _question(hold_close_117="10001/100", hold_close_118=None,
                         hold_state="decline", gated_state="decline", stop=None,
                         order_state="unresolved", reason="structure_prices_unavailable",
                         baseline=_inactive("unresolved"))
    with pytest.raises(ValueError, match="projected outcome differs"):
        report.summarize(_rows("2026-01-05", question), audit=True)


def test_selected_hold_arm_keeps_the_same_trade():
    with pytest.raises(ValueError, match="projected outcome differs"):
        report.summarize(_rows("2026-01-05", _question(candidate=_closed(8))))


def test_rejects_short_or_repeated_roster():
    rows = _rows("2026-01-05")
    with pytest.raises(ValueError, match="invalid roster"):
        report.summarize(rows[:-1])
    with pytest.raises(ValueError, match="unique coordinate"):
        report.summarize([*rows, rows[0]])
