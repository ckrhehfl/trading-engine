"""Invented receipts for the fixed EL pooled population and cash contrasts."""

from datetime import date, timedelta
from itertools import product

import pytest

from research import intraday_event_pooled_report as pooled
from research.intraday_event_evaluation import CONTROLS
from research.intraday_event_execution import ENTRY_POLICIES, MANAGEMENT_POLICIES
from research.intraday_event_model_workflow import FOLDS, KINDS, SCOPES
from research.intraday_event_models import HEADS


CALENDAR = tuple((date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(257))


def _reports():
    result = []
    for kind, scope, fold in product(KINDS, SCOPES, range(len(FOLDS))):
        _, start, end = FOLDS[fold]
        session = CALENDAR[start]
        roster_sha = ("a" if scope == "full_chart" else "b") * 64
        policies = [{"control": control, "sha256": str(i + 1) * 64, "roster_sha256": roster_sha}
                    for i, control in enumerate(CONTROLS)]
        ledgers = []
        for policy, entry, management in product(policies, ENTRY_POLICIES, MANAGEMENT_POLICIES):
            control = policy["control"]
            net = "6" if scope == "common_chart_peer" and control == "model" else "1"
            ledgers.append({"model": kind, "scope": scope, "control": control, "entry_policy": entry,
                            "management": management, "policy_sha256": policy["sha256"], "session": session,
                            "original_episode_id": f"episode-{fold}", "symbol": "KRX:000001",
                            "cluster_id": f"cluster-{fold}", "family": "A_DOWNFAIL", "state": "WAITING",
                            "action": "immediate", "candidate_key": f"candidate-{fold}", "initial_denominator": "100",
                            "outcome": {"net": net, "gross": net, "status": "closed",
                                        "filled_quantity": 1, "entry_notional": "100"}})
        result.append({"schema": "EL-fold-comparison-v1", "kind": kind, "scope": scope, "fold": fold,
                       "model_status": "completed", "evaluation_sessions": CALENDAR[start:end],
                       "head_statuses": dict.fromkeys(HEADS, {"empirical": "fitted_empirical", "lightgbm": "trained_lightgbm", "tcn": "trained_tcn"}[kind]),
                       "original_episodes": 1, "causal_frames": 1, "scope_frames": 1, "policies": policies,
                       "decision_support": {"without_causal_frame": 0, "without_supported_chart": 0, "without_required_context": 0},
                       "fit_counts": {}, "training_diagnostics": None,
                       "ledgers": ledgers, "forecast": {"groups": []}})
    return result


def test_missing_unit_and_shifted_calendar_fail_before_reporting():
    units = _reports()
    with pytest.raises(ValueError, match="all 36"):
        pooled.pool_cash(CALENDAR, units[:-1])
    units[-1]["evaluation_sessions"] = units[0]["evaluation_sessions"]
    with pytest.raises(ValueError, match="calendar"):
        pooled.pool_cash(CALENDAR, units)


def test_complete_units_still_require_same_opportunities_and_common_support():
    units = _reports()
    for row in units[-1]["ledgers"]:
        row["original_episode_id"] = "different-event"
    with pytest.raises(ValueError, match="original episode roster"):
        pooled.pool_cash(CALENDAR, units)
    units = _reports()
    for policy in units[-1]["policies"]:
        policy["roster_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="matched scope candidate roster"):
        pooled.pool_cash(CALENDAR, units)


def test_missing_arm_is_rejected_even_with_original_episode_count():
    units = _reports()
    units[0]["ledgers"].pop()
    with pytest.raises(ValueError, match="cash arm is missing"):
        pooled.pool_cash(CALENDAR, units)


def test_registered_order_and_eof_are_required(monkeypatch):
    monkeypatch.setattr(pooled.evaluation, "_blocks", lambda values: {"dates": len(values)})
    units = _reports()
    with pytest.raises(ValueError, match="unordered"):
        pooled.pool_cash(CALENDAR, reversed(units))
    with pytest.raises(ValueError, match="after the registered roster"):
        pooled.pool_cash(CALENDAR, iter((*units, units[-1])))


def test_streamed_pool_keeps_exact_cash_report_and_releases_full_ledgers(monkeypatch):
    monkeypatch.setattr(pooled.evaluation, "_blocks", lambda values: {"dates": len(values)})
    units = _reports()
    units[0]["forecast"]["groups"] = [{"dimension": "family_phase", "value": "A_DOWNFAIL:onset"},
                                       {"dimension": "symbol", "value": "KRX:000001"}]
    expected = pooled.evaluation.cash_report((row for unit in units for row in unit["ledgers"]), CALENDAR[97:])

    class OneUseLedger:
        def __init__(self, rows):
            self.rows = rows
            self.consumed = False

        def __iter__(self):
            assert not self.consumed
            self.consumed = True
            yield from self.rows

    def reports():
        for unit in units:
            ledger = OneUseLedger(unit["ledgers"])
            yield {**unit, "ledgers": ledger}
            assert ledger.consumed  # Earlier pooling buffered every unit before reading its ledger.

    result = pooled.pool_cash(CALENDAR, reports())
    assert result["cash"] == expected
    assert result["fold_support"][0]["forecast_support"] == [{"dimension": "family_phase", "value": "A_DOWNFAIL:onset"}]


def test_paired_effect_retains_unknown_and_unavailable_without_zero_cash(monkeypatch):
    # The shared date-block bootstrap has its own tests. Use only its input
    # count here to check that missing dates do not enter the pooled estimate.
    monkeypatch.setattr(pooled.evaluation, "_blocks", lambda values: {"dates": len(values)})
    units = _reports()
    unavailable = next(unit for unit in units if (unit["kind"], unit["scope"], unit["fold"])
                       == ("empirical", "common_chart", 0))
    unavailable["head_statuses"].update(net_immediate="unsupported", net_wait3="unsupported", remaining_gross="failed")
    for row in unavailable["ledgers"]:
        if row["control"] == "model":
            row.update(action="no_commit", candidate_key=None)
            row["outcome"].update(net="0", gross="0", status="no_entry", filled_quantity=0, entry_notional="0")
    unknown = next(unit for unit in units if (unit["kind"], unit["scope"], unit["fold"])
                   == ("empirical", "common_chart_peer", 1))
    for row in unknown["ledgers"]:
        if row["control"] == "model":
            row["outcome"].update(net=None, gross=None, status="unknown")

    result = pooled.pool_cash(CALENDAR, units)
    effect = next(row for row in result["cross_unit_paired_differences"]
                  if row["baseline"] == ["empirical", "common_chart"] and row["comparison"] == "peer_context"
                  and row["control"] == "model" and row["entry_policy"] == "next_open_capped" and row["family"] == "all")
    assert len(result["evaluation_sessions"]) == 160 and result["original_episodes"] == 4
    assert effect["joint_known_opportunities"] == 2
    assert effect["model_unavailable_opportunities"] == effect["unknown_outcome_opportunities"] == 1
    assert effect["all_opportunity_cash_difference"] is None
    assert effect["alternative_minus_baseline_known_cash"] == "10" and effect["paired_mean_cash_difference"] == "5"
    assert effect["complete_date_uncertainty"]["dates"] == 119
    assert len(effect["model_unavailable_dates"]) == 40
    base = next(row for row in result["cash"]["groups"] if row["model"] == "empirical"
                and row["scope"] == "common_chart" and row["control"] == "model" and row["dimension"] == "all"
                and row["entry_policy"] == "next_open_capped" and row["management"] == "fixed_exit")
    assert base["status"]["model_unavailable"] == 1 and base["total_net_cash"] is None
    assert base["zero_opportunity_weeks"] > 0
    assert unavailable["ledgers"][-1]["outcome"]["net"] == "0"  # Saved replay remains unchanged.
    fixed_onset = next(row for row in result["cash"]["groups"] if row["model"] == "empirical"
                       and row["scope"] == "common_chart" and row["control"] == "onset" and row["dimension"] == "all"
                       and row["entry_policy"] == "next_open_capped" and row["management"] == "fixed_exit")
    assert fixed_onset["total_net_cash"] == "4"
    managed_onset = next(row for row in result["cash"]["groups"] if row["model"] == "empirical"
                         and row["scope"] == "common_chart" and row["control"] == "onset" and row["dimension"] == "all"
                         and row["entry_policy"] == "next_open_capped" and row["management"] == "forecast_full_exit")
    assert managed_onset["status"]["model_unavailable"] == 1 and managed_onset["total_net_cash"] is None
    assert managed_onset["fills"] == fixed_onset["fills"] - 1
    assert sum(week["fills"] for week in managed_onset["weekly"].values()) == managed_onset["fills"]
