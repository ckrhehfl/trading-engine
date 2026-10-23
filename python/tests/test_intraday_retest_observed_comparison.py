"""Invented original-known-only selection, source retention and sizing gates."""
from copy import deepcopy
from fractions import Fraction
import json

import pytest

from research import intraday_retest_observed_comparison as comparison
from test_intraday_retest_recovery import example, DAYS


def source(*, recovered=False, cap_prices=("102", "102")):
    original, chunks, responses = example(cap_prices=cap_prices)
    if not recovered:
        responses = {key: None for key in responses}
    return comparison.de.build_recovery(original, chunks, responses)


@pytest.fixture(scope="module")
def baseline():
    return source()


def test_public_specification_exact_and_private_roles_only():
    spec = json.loads(comparison.SPECIFICATION_PATH.read_bytes())
    comparison.validate_spec(spec)
    assert "/home/" not in json.dumps(spec)
    assert spec["resources"]["market_gets"] == spec["resources"]["token_posts"] == 0
    assert spec["resources"]["automatic_retries"] == 0


@pytest.mark.parametrize("mutate", [
    lambda spec: spec["parameters"].update(bootstrap_seed=1),
    lambda spec: spec["parameters"].update(bootstrap_draws=1000),
    lambda spec: spec["parameters"].update(assumed_cost={"numerator": "20", "denominator": "10000"}),
    lambda spec: spec["parameters"].update(underpower_exception_applied=True),
    lambda spec: spec["resources"].update(market_gets=1),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(output_name="unregistered"),
])
def test_specification_changes_refuse_before_comparison(baseline, mutate):
    spec = json.loads(comparison.SPECIFICATION_PATH.read_bytes()); mutate(spec)
    with pytest.raises(comparison.ObservedComparisonRefusal, match="specification_scope_mismatch"):
        comparison.build_comparison(baseline, spec)


def test_new_original_known_scope_is_separate_from_failed_recovery_gate(baseline):
    assert baseline["comparison_performed"] is False
    assert baseline["requires_operator_choice_before_comparison"] is True
    result = comparison.build_comparison(baseline)
    assert result["comparison_performed"] is True
    assert result["source_bound_observed_label_authorization_applied"] is True
    assert result["underpower_exception_applied"] is False
    for policy, variant in result["variants"].items():
        assert variant["source_target_count"] == 60
        assert variant["source_signal_count"] == 4
        assert variant["selected_known_label_count"] == 2
        assert variant["excluded_unknown_label_count"] == 2
        assert variant["source_unknown_signal_count"] == 56
        assert variant["selected_known_label_count"] + variant["excluded_unknown_label_count"] == variant["source_signal_count"]
        assert variant["own_label_dispersion"]["events"] == 2
        assert variant["own_label_dispersion"]["unknown_labels"] == 0
        assert variant["comparison"]["event_count"] == 2
        assert result["source_provenance"]["variants"][policy]["days"] == baseline["variants"][policy]["days"]


def test_recovered_original_unknown_labels_are_still_excluded():
    recovered = source(recovered=True)
    assert recovered["comparison_performed"] is True
    result = comparison.build_comparison(recovered)
    for variant in result["variants"].values():
        assert len(variant["records"]) == 2
        assert len(variant["excluded_unknown_labels"]) == 2
        assert all(row["original_label"]["status"] == "unknown" for row in variant["excluded_unknown_labels"])
        assert all(row["original_label"]["price_change"] is None for row in variant["excluded_unknown_labels"])


def test_full_source_flags_unknowns_metadata_and_original_labels_remain_unchanged(baseline):
    before = comparison._canonical(baseline)
    result = comparison.build_comparison(baseline)
    assert comparison._canonical(baseline) == before
    assert result["source_provenance"] is baseline
    for variant in result["variants"].values():
        assert all(row["unknown_reasons"] == ["label_missing_minute_buckets"] for row in variant["excluded_unknown_labels"])
    for flag in ("promotion_allowed", "performance_certified", "actual_portfolio_pnl", "incremental_edge_claimed", "matched_control_comparison"):
        assert result[flag] is False


def test_gross_net_exact_arithmetic_zero_event_weeks_and_same_own_uncertainty(baseline):
    result = comparison.build_comparison(baseline)
    for variant in result["variants"].values():
        summary = variant["comparison"]
        assert summary["gross_mean"] == summary["gross_median"] == Fraction(0)
        assert summary["net_mean"] == summary["net_median"] == Fraction(-33, 10000)
        assert summary["gross_wins"] == summary["net_wins"] == summary["cost_exceeding"] == 0
        assert summary["uncertainty"] == variant["own_label_dispersion"]
        assert len(summary["by_week"]) == len(result["reference_weeks"]) == 3
        empty = summary["by_week"][comparison.ct._week(DAYS[-1])]
        assert empty["event_count"] == 0 and empty["gross_mean"] is None and empty["net_mean"] is None
        expected = comparison.cc.dispersion(variant["records"], list(DAYS))
        assert expected == variant["own_label_dispersion"]


@pytest.mark.parametrize("mutate", [
    lambda report: report.update(study_id="other"),
    lambda report: report.update(mode="other"),
    lambda report: report.update(promotion_allowed=True),
    lambda report: report.update(underpower_exception_applied=True),
    lambda report: report.update(source_rows_overwritten=False),
    lambda report: report["parameters"].update(axis="another"),
    lambda report: report["reference_sessions"].pop(),
    lambda report: report["variants"]["D+1"]["records"].pop(),
    lambda report: report["variants"]["D+1"]["records"][1].update(label=Fraction(1, 10)),
    lambda report: report["variants"]["D+1"]["records"][1].update(recovered=True),
    lambda report: report["variants"]["D+1"]["records"][1].update(original_label_status="unknown"),
])
def test_changed_source_identity_flags_population_and_known_labels_refuse(baseline, mutate):
    changed = deepcopy(baseline); mutate(changed)
    with pytest.raises(comparison.ObservedComparisonRefusal):
        comparison.build_comparison(changed)


def test_failed_selected_own_sizing_never_calls_mean(monkeypatch):
    failed = source(cap_prices=("90", "120"))
    monkeypatch.setattr(comparison.de, "_summary", lambda *_: pytest.fail("mean before both own-sizing passes"))
    result = comparison.build_comparison(failed)
    assert result["comparison_performed"] is False
    assert result["requires_operator_choice"] is True
    for variant in result["variants"].values():
        assert variant["own_label_dispersion"]["mde_bps"] > 33
        assert variant["comparison"] is None
        assert variant["own_sizing_passed"] is False


def test_undefined_sizing_in_either_policy_blocks_every_mean(baseline, monkeypatch):
    original = comparison.cc.dispersion
    calls = []
    def undefined(records, reference):
        result = original(records, reference); calls.append(True)
        if len(calls) == 2:
            result["mde_bps"] = None
        return result
    monkeypatch.setattr(comparison.cc, "dispersion", undefined)
    monkeypatch.setattr(comparison.de, "_summary", lambda *_: pytest.fail("one policy failure emitted mean"))
    result = comparison.build_comparison(baseline)
    assert result["comparison_performed"] is False
    assert "D+2:undefined_selected_own_sizing" in result["comparison_not_run_reasons"]


def test_determinism_and_checkpoint_are_exercised(baseline):
    ticks = []
    first = comparison.build_comparison(baseline, checkpoint=lambda: ticks.append(True))
    second = comparison.build_comparison(baseline)
    assert comparison._canonical(first) == comparison._canonical(second)
    assert len(ticks) >= 8
