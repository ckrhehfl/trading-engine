"""Invented fold, forecast and quote cases for the EF comparison report."""

from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
from json import dumps, loads
from math import sqrt
from types import SimpleNamespace

import pytest

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_chart_evaluation import ARMS, MANAGEMENT_ARMS, FoldForecasts, evaluate_scenario, verify_evaluation
from research.intraday_chart_folds import EvaluationRow, FoldSelection, ObservedLabel
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_pattern_calibration import session_open


DAYS = tuple((date(2025, 2, 1) + timedelta(days=index)).isoformat() for index in range(257))
FIRST, SECOND = fixed_calendar(DAYS).folds[:2]
A, B, C = "KRX:000001", "KRX:000002", "KRX:000003"


def _labels(key, value):
    day, _, offset = key
    return ((None if value is None else ObservedLabel(Fraction(value), session_open(day) + (offset + 36) * MINUTE)),
            *(None,) * 5)


def _fixture(rows, *, fold=FIRST, supported=True, holding=None):
    selection = FoldSelection(fold, "S1", (), tuple(EvaluationRow(key, support) for key, support, _ in rows),
                              (), supported)
    return FoldForecasts(selection, {arm: {key: forecasts.get(arm) for key, _, forecasts in rows} for arm in ARMS},
                         {arm: {} for arm in MANAGEMENT_ARMS} if holding is None else holding)


class _Quotes:
    def __init__(self, prices):
        self.prices = prices

    def row(self, day, symbol, offset):
        price = self.prices[day, symbol, offset]
        return SimpleNamespace(session=day, symbol=symbol, offset=offset,
                               execution_open=None if price is None else Fraction(price),
                               execution_state="unavailable" if price is None else "available")


def test_common_pair_support_unknown_outcomes_and_zero_week():
    day = FIRST.test_sessions[0]
    late = FIRST.test_sessions[-1]
    a, b, c = (day, A, 60), (day, B, 60), (day, C, 60)
    outside = (late, A, 60)
    missing_chart = (late, A, 75)
    rows = [
        (a, "common", {"LGB_CHART_PEER": 0.40, "LGB_CHART": 0.33, "LAST15_CHART_RETURN": 0.0}),
        (b, "common", {"LGB_CHART_PEER": 0.30, "LGB_CHART": 0.41, "LAST15_CHART_RETURN": 0.0}),
        (c, "common", {"LGB_CHART_PEER": 0.50, "LGB_CHART": 0.0}),
        (outside, "chart_only", {"LGB_CHART": 0.70}),
        (missing_chart, "chart_unavailable", {}),
    ]
    labels = {key: _labels(key, value) for key, value in ((a, "0.008"), (b, "-0.004"), (c, None),
                                                        (outside, "0.009"), (missing_chart, None))}
    fold = _fixture(rows)
    result = evaluate_scenario((fold,), labels_at=labels.__getitem__, quotes=_Quotes({}))
    report = result["pooled"]
    assert report["support"] == {"common": 3, "chart_only": 1, "chart_unavailable": 1, "outside_clock": 0}
    assert report["unavailable_label_heads"]["common"] == [1, 3, 3, 3, 3, 3]
    arm = report["common_arms"]["LGB_CHART_PEER"]
    assert arm["status"] == "observed_partial" and arm["admitted_missing_outcome"] == 1
    assert arm["observed_net_pp"]["33"]["mean"] == pytest.approx(0.47)
    assert arm["observed_net_pp"]["43"]["mean"] == pytest.approx(0.37)
    assert arm["observed_gross_pp"]["precision"] == "insufficient_precision"
    assert sum(week["admitted_opportunities"] for week in arm["weeks"].values()) == 2
    assert any(week["admitted_opportunities"] == 0 for week in arm["weeks"].values())
    assert report["common_arms"]["LAST15_CHART_RETURN"]["status"] == "no_trade"
    assert report["common_arms"]["TCN_CHART"]["status"] == "prediction_unavailable"
    assert report["chart_only_outside_common_diagnostic"]["LGB_CHART"]["observed_gross_pp"]["mean"] == 0.9
    pair = report["common_contrasts"]["LGB_CHART_PEER_minus_LGB_CHART"]
    assert pair["jointly_scored"] == 3 and pair["unknown_policy_opportunities"] == 1
    assert pair["left_minus_right_absolute_error_pp"]["mean"] == pytest.approx(-0.09)
    assert pair["left_minus_right_within_session_spearman"]["mean"] == 2.0
    economics = pair["matched_opportunity_net_pp"]["33"]
    assert economics["left_mean"] == pytest.approx(0.235)
    assert economics["right_mean"] == pytest.approx(-0.365)
    assert economics["left_minus_right"]["mean"] == pytest.approx(0.6)
    verify_evaluation(loads(dumps(result, allow_nan=False)), (fold,), labels_at=labels.__getitem__, quotes=_Quotes({}))


def test_dynamic_paths_share_lot_reduce_once_and_preserve_unknown_fill():
    day = FIRST.test_sessions[0]
    a, b = (day, A, 60), (day, B, 60)
    rows = [(key, "common", {"TCN_CHART": 0.4}) for key in (a, b)]
    updates = {(day, symbol, offset): -0.1 for symbol in (A, B) for offset in (65, 70, 75)}
    fold = _fixture(rows, holding={"TCN_CHART": updates, "TCN_CHART_PEER": {}})
    prices = _Quotes({a: 400_000, b: 400_000, (day, A, 65): 410_000, (day, A, 70): 390_000,
                      (day, A, 90): 350_000, (day, B, 65): None, (day, B, 90): 350_000})
    labels = {key: _labels(key, "-0.125") for key in (a, b)}
    result = evaluate_scenario((fold,), labels_at=labels.__getitem__, quotes=prices)
    managed = result["pooled"]["matched_lots"]["TCN_CHART"]
    for cost in (33, 43):
        group = managed[str(cost)]
        assert group["admitted_opportunities"] == 2 and group["paired_known"] == 1
        assert group["dynamic"]["statuses"] == {"closed": 1, "unavailable": 1}
        assert group["dynamic"]["exit_legs_by_reason_and_status"] == {
            "first_nonpositive:modeled": 1, "consecutive_nonpositive:modeled": 1, "first_nonpositive:indeterminate": 1}
        assert group["dynamic"]["last_known_units_at_unresolved_exit"] == 5
        assert group["dynamic"]["paired_net_pp"]["mean"] == pytest.approx(-0.5 - cost / 100)
        assert group["dynamic_minus_fixed_net_pp"]["mean"] == 12.0
        assert group["dynamic_minus_fixed_quantity_weighted_hold_minutes"]["mean"] == -22.0
    proof = verify_evaluation(result, (fold,), labels_at=labels.__getitem__, quotes=prices)
    assert proof["verified"] and proof["paired_lot_cases_across_costs"] == 4
    changed = deepcopy(result)
    changed["pooled"]["matched_lots"]["TCN_CHART"]["33"]["dynamic_minus_fixed_net_pp"]["mean"] = 12.5
    with pytest.raises(InputRefusal, match="arithmetic"):
        verify_evaluation(changed, (fold,), labels_at=labels.__getitem__, quotes=prices)
    with pytest.raises(InputRefusal, match="path_and_label"):
        evaluate_scenario((fold,), labels_at=lambda key: _labels(key, None), quotes=prices)


def test_session_cluster_precision_uses_correlated_same_day_opportunities():
    # Two identical returns per contributing day; reference dates with no
    # opportunity remain in the finite-sample cluster adjustment.
    days = (*FIRST.test_sessions[:6], *SECOND.test_sessions[:6])
    inputs = [(day, symbol, 60) for day in days for symbol in (A, B)]
    rows = [(key, "common", {"LGB_CHART": 1.0}) for key in inputs]
    labels = {key: _labels(key, Fraction(days.index(key[0]) % 2, 100)) for key in inputs}
    folds = _fixture(rows[:12]), _fixture(rows[12:], fold=SECOND)
    result = evaluate_scenario(folds, labels_at=labels.__getitem__, quotes=_Quotes({}))
    cluster = result["pooled"]["common_arms"]["LGB_CHART"]["observed_gross_pp"]
    assert cluster["observations"] == 24 and cluster["contributing_sessions"] == 12
    assert cluster["reference_sessions"] == 64 and cluster["precision"] == "estimated"
    assert cluster["session_cluster_se"] == pytest.approx(sqrt(64 / 63 * 12) / 24)
    assert cluster["cluster_to_naive_se_ratio"] > 1
    for fold in result["folds"].values():
        assert fold["common_arms"]["LGB_CHART"]["observed_gross_pp"]["normal95_exploratory"] is None
    verify_evaluation(result, folds, labels_at=labels.__getitem__, quotes=_Quotes({}))


def test_rejects_nonfinite_or_missing_forecasts_before_label_lookup():
    key = FIRST.test_sessions[0], A, 60

    def unexpected_label(_):
        raise AssertionError("Labels opened before forecast validation")

    invalid = _fixture([(key, "common", {"LGB_CHART": float("nan")})])
    with pytest.raises(InputRefusal, match="finite"):
        evaluate_scenario((invalid,), labels_at=unexpected_label, quotes=_Quotes({}))
    incomplete = _fixture([(key, "common", {"TCN_CHART": 0.4})])
    with pytest.raises(InputRefusal, match="holding_forecast_coordinates"):
        evaluate_scenario((incomplete,), labels_at=unexpected_label, quotes=_Quotes({}))


def test_unsupported_fold_and_missing_outcome_are_distinct_from_no_trade():
    key = FIRST.test_sessions[0], A, 60
    fold = _fixture([(key, "common", {"LAST15_CHART_RETURN": 0.4})], supported=False)
    result = evaluate_scenario((fold,), labels_at=lambda key: _labels(key, None), quotes=_Quotes({}))
    arms = result["pooled"]["common_arms"]
    assert arms["LAST15_CHART_RETURN"]["status"] == "missing_outcome"
    assert arms["LAST15_CHART_RETURN"]["observed_gross_pp"]["mean"] is None
    assert arms["LGB_CHART"]["status"] == "prediction_unavailable"
    assert result["folds"]["1"]["training_supported"] is False
