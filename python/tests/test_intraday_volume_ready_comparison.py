"""Invented CY reports only; no actual market-body, DB, archive or network IO."""
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
import json
import math
import random
from statistics import NormalDist, stdev

import pytest

from research import intraday_volume_ready_comparison as comparison
from test_intraday_opening_volume import build_synthetic_sources

cy, ct = comparison.cy, comparison.ct


@lru_cache(maxsize=1)
def _source():
    return cy.build_calibration(*build_synthetic_sources(selected_unknown=True))


def source():
    return deepcopy(_source())


def records(report, group, policy="D+1"):
    return [{"session": row["session"], "label": None if row["original_finding"]["label"]["status"] == "unknown"
        else comparison.cu._ratio(row["original_finding"]["label"]["price_change"])}
        for row in report["variants"][policy]["days"]
        if row["original_finding"]["status"] == "signal" and row["feature"]["status"] in {"selected", "rejected"}
        and (group == "original" or row["feature"]["status"] == "selected")]


def test_exact_preregistration_and_source_bound_authorization():
    spec = json.loads(comparison.SPECIFICATION_PATH.read_bytes())
    comparison.validate_spec(spec)
    assert comparison.PARAMETERS["source_parameters"]["underpower_exception_applied"] is False
    assert comparison.AUTHORIZATION["unknown_label_groups"] == ["original", "selected"]
    for mutate in (lambda x: x["parameters"].update(underpower_exception_applied=False),
            lambda x: x["parameters"]["source_parameters"].update(arms=["15", "30"]),
            lambda x: x.update(upstream_sha256="a" * 64),
            lambda x: x["resources"].update(cpu_seconds=600)):
        changed = deepcopy(spec)
        mutate(changed)
        with pytest.raises(comparison.VolumeReadyComparisonRefusal):
            comparison.validate_spec(changed)


def test_own_known_labels_exact_means_unknown_counts_and_immutable_links():
    report = source()
    before = deepcopy(report)
    result = comparison.build_comparison(report)
    assert report == before
    assert result["operator_authorization"] == comparison.AUTHORIZATION
    assert result["underpower_exception_applied"] is True
    assert result["new_performance_families"] == 1
    linked = result["source_links"]["opening_volume"]
    assert linked["flags"] == cy.FLAGS
    assert linked["flags"]["underpower_exception_applied"] is False
    assert linked["source_links"] == report["source_links"]
    assert linked["by_policy"] == {key: value["by_arm"] for key, value in report["variants"].items()}
    assert "upstream_provenance" not in result
    for policy, variant in result["variants"].items():
        cell = variant["by_arm"]["15"]
        for group, actual in cell["groups"].items():
            own_records = records(report, group, policy)
            values = [row["label"] for row in own_records if row["label"] is not None]
            mean = sum(values, Fraction(0)) / len(values)
            assert actual["event_count"] == len(own_records)
            assert actual["unknown_labels"] == len(own_records) - len(values) > 0
            assert actual["gross_mean"] == mean
            assert actual["net_mean"] == mean - Fraction(33, 10000)
            assert actual["gross_positive_count"] == sum(value > 0 for value in values)
            assert actual["net_positive_count"] == sum(value > Fraction(33, 10000) for value in values)
        assert cell["selected_minus_original"]["gross_mean_difference"] == cell["groups"]["selected"]["gross_mean"] - cell["groups"]["original"]["gross_mean"]
    linked["by_policy"]["D+1"]["15"]["selection_counts"]["selected"] = 0
    assert report == before


def test_joint_calendar_influence_and_expanded_bootstrap_oracle():
    report = source()
    cell = comparison.build_comparison(report)["variants"]["D+1"]["by_arm"]["15"]
    actual = cell["selected_minus_original"]
    uncertainty, reference = actual["uncertainty"], report["reference_sessions"]
    known = {group: [row for row in records(report, group) if row["label"] is not None] for group in comparison.GROUPS}
    means = {group: cell["groups"][group]["gross_mean"] for group in comparison.GROUPS}
    by_day = {day: {group: [] for group in comparison.GROUPS} for day in reference}
    for group in comparison.GROUPS:
        for row in known[group]:
            by_day[row["session"]][group].append(row["label"])
    influences = [sum((value - means["selected"] for value in by_day[day]["selected"]), Fraction(0)) / len(known["selected"])
        - sum((value - means["original"] for value in by_day[day]["original"]), Fraction(0)) / len(known["original"]) for day in reference]
    expected_cluster = math.sqrt(len(reference) / (len(reference) - 1) * sum(float(value) ** 2 for value in influences))
    assert uncertainty["session_cluster_se"] == pytest.approx(expected_cluster, rel=1e-13)
    rng, draws, skipped = random.Random(0), [], 0
    for _ in range(2000):
        positions, remaining = [], len(reference)
        while remaining:
            start, length = rng.randrange(len(reference)), min(5, remaining)
            positions.extend((start + offset) % len(reference) for offset in range(length))
            remaining -= length
        sampled = {group: [value for position in positions for value in by_day[reference[position]][group]] for group in comparison.GROUPS}
        if not all(sampled.values()):
            skipped += 1
            continue
        draws.append(float(sum(sampled["selected"], Fraction(0)) / len(sampled["selected"])
            - sum(sampled["original"], Fraction(0)) / len(sampled["original"])))
    assert uncertainty["bootstrap_se"] == pytest.approx(stdev(draws), rel=1e-12)
    assert uncertainty["bootstrap_skipped_zero_denominator_draws"] == skipped
    assert uncertainty["design_se"] == max(uncertainty[key] for key in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert uncertainty["corrected_to_naive_ratio"] == uncertainty["design_se"] / uncertainty["naive_se"]
    width = NormalDist().inv_cdf(.975) * uncertainty["design_se"]
    assert actual["descriptive_normal95_interval"] == {"lower": float(actual["gross_mean_difference"]) - width,
        "upper": float(actual["gross_mean_difference"]) + width}


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(study_id="other"),
    lambda x: x.update(underpower_exception_applied=True),
    lambda x: x.update(requires_operator_choice_before_comparison=False),
    lambda x: x["parameters"].update(arms=["15", "30"]),
    lambda x: x["reference_sessions"].reverse(),
    lambda x: x["variants"].pop("D+2"),
    lambda x: x["variants"]["D+1"]["days"].pop(),
    lambda x: x["variants"]["D+1"]["days"][0].update(symbol="KRX:999999"),
    lambda x: x["variants"]["D+1"]["days"][0]["membership_target"].update(rank=2),
    lambda x: x["variants"]["D+1"]["days"][0]["feature"].update(available_at="2025-09-04T09:18:00+09:00"),
    lambda x: x["variants"]["D+1"]["days"][400]["feature"].update(prior20_median=Fraction(1)),
    lambda x: x["variants"]["D+1"]["days"][400]["feature"].update(opening_volume_ratio=Fraction(1)),
    lambda x: x["variants"]["D+1"]["days"][400]["feature"]["prior_sessions"].pop(),
    lambda x: x["variants"]["D+1"]["days"][400]["feature"]["volume_source"].update(saved_chunk_sha256="a" * 64),
    lambda x: x["variants"]["D+1"]["by_arm"]["15"]["selection_counts"].update(selected=0),
    lambda x: x["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"].update(design_se=0),
    lambda x: x["source_links"]["opening_range"].update(sha256="a" * 64),
    lambda x: x["source_links"]["opening_range"]["flags"].update(underpower_exception_applied=True),
    lambda x: x["source_links"]["opening_range"]["by_policy"]["D+1"]["15"]["frequency"].update(forced_trades=True),
    lambda x: x["source_links"]["activity_readiness"].update(path="/tmp/replacement"),
])
def test_corruption_refuses_before_any_mean(mutate, monkeypatch):
    report = source()
    mutate(report)
    calls = []
    monkeypatch.setattr(comparison, "_interval", lambda *args: calls.append(args))
    with pytest.raises(comparison.VolumeReadyComparisonRefusal):
        comparison.build_comparison(report)
    assert calls == []


def test_genuine_finite_underpower_is_authorized_but_not_reclassified():
    report = cy.build_calibration(*build_synthetic_sources(sparse_volatile=True,
        volume=lambda index, number: "20" if index >= 20 and number == 1 else "10"))
    before = deepcopy(report)
    assert report["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] > 33
    result = comparison.build_comparison(report)
    assert result["source_links"]["opening_volume"]["by_policy"]["D+1"]["15"]["own_label_dispersion"]["mde_bps"] > 33
    assert report == before
    assert result["source_links"]["opening_volume"]["flags"]["underpower_exception_applied"] is False


def test_undefined_sizing_not_authorized():
    report = cy.build_calibration(*build_synthetic_sources(volume=lambda *_: "10"))
    assert report["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] is None
    with pytest.raises(comparison.VolumeReadyComparisonRefusal, match="malformed_source_calibration") as failure:
        comparison.build_comparison(report)
    assert str(failure.value.__cause__) == "undefined_source_own_sizing_not_authorized"


def test_unknown_outcome_cannot_be_zero_filled():
    report = source()
    row = next(row for row in report["variants"]["D+1"]["days"]
        if row["feature"]["status"] == "selected" and row["original_finding"]["label"]["status"] == "unknown")
    row["original_finding"]["label"]["price_change"] = Fraction(0)
    with pytest.raises(comparison.VolumeReadyComparisonRefusal):
        comparison.build_comparison(report)


def test_checkpoint_and_fraction_serialization():
    calls = []
    result = comparison.build_comparison(source(), checkpoint=lambda: calls.append(True))
    assert len(calls) > 5000
    encoded = json.loads(comparison._canonical(result))
    mean = result["variants"]["D+1"]["by_arm"]["15"]["groups"]["selected"]["gross_mean"]
    assert encoded["variants"]["D+1"]["by_arm"]["15"]["groups"]["selected"]["gross_mean"] == {
        "numerator": str(mean.numerator), "denominator": str(mean.denominator)}
    def stop():
        raise RuntimeError("invented_budget_stop")
    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        comparison.build_comparison(source(), checkpoint=stop)


def test_ready_population_accounting_new_own_sizing_and_calibration_precedes_means(monkeypatch):
    report, trace = source(), []
    interval = comparison._interval
    def traced_interval(*args):
        trace.append("mean")
        return interval(*args)
    monkeypatch.setattr(comparison, "_interval", traced_interval)
    def calibrated(receipt):
        assert not trace
        assert receipt["performance_means_calculated"] is False
        assert set(receipt["by_policy"]) == {"D+1", "D+2"}
        trace.append("both_controls_calibrated")
        receipt["by_policy"].clear()  # Callback cannot modify the report.
    result = comparison.build_comparison(report, calibration_callback=calibrated)
    assert trace[0] == "both_controls_calibrated" and trace.count("mean") == 6
    for policy, variant in report["variants"].items():
        cell = result["variants"][policy]["by_arm"]["15"]
        signals = [row for row in variant["days"] if row["original_finding"]["status"] == "signal"]
        ready = [row for row in signals if row["feature"]["status"] in {"selected", "rejected"}]
        selected = [row for row in ready if row["feature"]["status"] == "selected"]
        assert cell["readiness_accounting"] == {"original_signal_count": len(signals),
            "feature_ready_original_signal_count": len(ready), "feature_ready_selected_signal_count": len(selected),
            "feature_ready_rejected_signal_count": len(ready) - len(selected), "feature_unready_signal_count": len(signals) - len(ready)}
        assert len(ready) < len(signals)
        own = ct.cc.dispersion(records(report, "original", policy), report["reference_sessions"])
        control = cell["volume_ready_control_summary"]
        assert control["own_label_dispersion"] == own == cell["groups"]["original"]["source_own_label_dispersion"]
        assert control["known_labels"] + control["unknown_labels"] == len(ready)
        assert control["unknown_labels"] > 0  # Selection precedes future completeness.
        assert control["frequency"] == cell["groups"]["original"]["frequency"]
        assert set(control["by_week"]) == set(report["reference_weeks"])
        assert set(control["by_month"]) == {day[:7] for day in report["reference_sessions"]}
        assert result["control_calibration"]["by_policy"][policy]["own_label_dispersion"] == own
        assert cell["groups"]["selected"]["source_own_label_dispersion"] == variant["by_arm"]["15"]["own_label_dispersion"]


@pytest.mark.parametrize("policy", ["D+1", "D+2"])
@pytest.mark.parametrize("mde", [None, math.nan, math.inf, 33.000001])
def test_either_new_control_failure_prevents_all_means_and_calibration_receipt(monkeypatch, policy, mde):
    report = source()
    authenticated = comparison.cz._validate_source(report, lambda: None)
    monkeypatch.setattr(comparison.cz, "_validate_source", lambda *_: authenticated)
    dispersion, calls, computed = ct.cc.dispersion, [], {}
    counter = 0
    def sizing(rows, reference):
        nonlocal counter
        current = ("D+1", "D+2")[counter]
        counter += 1
        own = dispersion(rows, reference)
        if current == policy:
            own["mde_bps"] = mde
            computed.update(deepcopy(own))
        return own
    monkeypatch.setattr(ct.cc, "dispersion", sizing)
    monkeypatch.setattr(comparison, "_interval", lambda *_: calls.append("mean"))
    with pytest.raises(comparison.VolumeReadyComparisonRefusal, match="new_control_own_sizing") as stopped:
        comparison.build_comparison(report, calibration_callback=lambda _: calls.append("calibrated"))
    assert calls == []
    failure = stopped.value.calibration_failure
    assert failure["stage"] == "volume_ready_control_own_sizing_failed_before_comparison"
    assert failure["policy"] == policy and failure["reason"] == str(stopped.value)
    assert failure["performance_means_calculated"] is False
    assert failure["new_performance_families"] == 0
    assert failure["requires_operator_choice"] is True
    assert failure["new_control_underpower_exception_applied"] is False
    assert failure["upstream_sha256"] == comparison.UPSTREAM_SHA256
    if mde is not None and not math.isfinite(mde):
        computed["mde_bps"] = None
        assert failure["nonfinite_statistic_fields"] == ["own_label_dispersion.mde_bps"]
    else:
        assert failure["nonfinite_statistic_fields"] == []
    assert failure["own_label_dispersion"] == computed
    assert json.loads(comparison._canonical(failure))["own_label_dispersion"] == computed


def test_failed_calibration_publication_stops_before_any_mean(monkeypatch):
    calls = []
    monkeypatch.setattr(comparison, "_interval", lambda *_: calls.append("mean"))
    def fail(_):
        raise OSError("invented durable receipt failure")
    with pytest.raises(OSError, match="durable receipt"):
        comparison.build_comparison(source(), calibration_callback=fail)
    assert calls == []
