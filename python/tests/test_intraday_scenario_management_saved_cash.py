"""Independent cash readback of invented saved EH trades."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from research.intraday_campaign_cash_book import INITIAL_CAPITAL
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_management_metrics import ArmMetrics
from research.intraday_scenario_management_saved_cash import saved_management_metrics


START = datetime(2024, 1, 2, tzinfo=UTC)


def _at(minute):
    return (START + timedelta(minutes=minute)).isoformat()


def _trade(action, minute, units, price, *, status="modeled", reason=None):
    if reason is None:
        reason = {"enter": "higher_value_of_entry", "reenter": "higher_value_of_entry",
                  "reduce_half": "higher_value_of_reduction", "exit": "structural_close_failure"}[action]
    return [action, _at(minute - 1), _at(minute), units, status, units if status == "modeled" else 0,
            str(price) if price is not None else None, reason]


def _row():
    return {"status": "confirmed", "event_expiry": _at(20),
            "initial": {"trade": _trade("enter", 3, 5, 101)},
            "arms": {"fixed": ["closed", [_trade("exit", 13, 5, 104)]],
                     "dynamic": ["closed", [_trade("reduce_half", 8, 2, 110), _trade("exit", 13, 3, 95)]],
                     "fresh_increment": ["closed", [_trade("reenter", 15, 4, 100), _trade("exit", 18, 4, 102)]]}}


@pytest.mark.parametrize("cost_bps", [33, 43])
def test_saved_fills_reconcile_once_paid_cost_and_common_window(cost_bps):
    basis, (fixed, dynamic, reentry) = saved_management_metrics(_row(), event_start=START, cost_bps=cost_bps)

    assert basis == 505
    cost = 505 * Fraction(cost_bps, 10_000)
    combined_cost = 905 * Fraction(cost_bps, 10_000)
    denominator = 20 * INITIAL_CAPITAL
    assert fixed == ArmMetrics("fixed", "closed", 1, 15, 15 - cost, cost, 5050 / denominator, 1025)
    assert dynamic == ArmMetrics("dynamic", "closed", 1, 0, -cost, cost, 4040 / denominator, 1010)
    assert reentry == ArmMetrics("reentry", "closed", 2, 8, 8 - combined_cost, combined_cost,
                                5240 / denominator, 1818)


def test_unresolved_fresh_close_keeps_two_confirmed_entries_but_no_economics():
    row = _row()
    row["arms"]["fresh_increment"] = ["unresolved", [_trade("reenter", 15, 4, 100),
        _trade("exit", 18, 4, None, status="indeterminate")]]

    basis, (_, dynamic, fresh) = saved_management_metrics(row, event_start=START, cost_bps=33)

    assert basis == 505 and dynamic.gross_pnl == 0
    assert fresh == ArmMetrics("reentry", "unresolved", 2, None, None, None, None, None)


def test_unresolved_dynamic_close_carries_through_fresh_arm():
    row = _row()
    row["arms"]["dynamic"] = ["unresolved", [_trade("reduce_half", 8, 2, 110),
        _trade("exit", 13, 3, None, status="indeterminate")]]
    row["arms"]["fresh_increment"] = ["unresolved", []]

    _, (fixed, dynamic, fresh) = saved_management_metrics(row, event_start=START, cost_bps=43)

    assert fixed.gross_pnl == 15
    assert dynamic == ArmMetrics("dynamic", "unresolved", 1, None, None, None, None, None)
    assert fresh == ArmMetrics("reentry", "unresolved", 1, None, None, None, None, None)


def test_declined_fresh_request_preserves_dynamic_economics():
    row = _row()
    row["arms"]["fresh_increment"] = ["not_reentered", [_trade("reenter", 15, 4, 200, status="declined")]]

    _, (_, dynamic, fresh) = saved_management_metrics(row, event_start=START, cost_bps=43)

    assert fresh == ArmMetrics("reentry", "not_reentered", 1, dynamic.gross_pnl, dynamic.net_pnl,
                                dynamic.paid_cost, dynamic.entry_basis_exposure, dynamic.gross_turnover)


@pytest.mark.parametrize("status", ["no_event", "no_entry", "declined", "unresolved", "unknown", "unsupported"])
def test_initial_entry_without_confirmed_fill_has_no_basis_or_arms(status):
    assert saved_management_metrics({"status": status}, event_start=None, cost_bps=33) == (None, ())


def test_known_arm_refuses_nonflat_inventory():
    row = _row()
    row["arms"]["fixed"] = ["closed", []]

    with pytest.raises(InputRefusal, match="known_arm_requires_flat_inventory"):
        saved_management_metrics(row, event_start=START, cost_bps=33)


def test_event_window_must_begin_before_entry_fill():
    with pytest.raises(InputRefusal, match="event_window_differs"):
        saved_management_metrics(_row(), event_start=START + timedelta(minutes=3), cost_bps=33)
