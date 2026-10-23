"""Invented original CT/CV reports only; never read saved research inputs."""

from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
import json
import math
import random
from statistics import NormalDist, stdev

import pytest

from research import intraday_opening_impulse_comparison as comparison
from test_intraday_opening_impulse import build_synthetic_source as build_original
from test_intraday_opening_range_comparison import DEFAULT_DAYS, _refresh_own


cv = comparison.cv


def build_synthetic_upstream(*, days=DEFAULT_DAYS, **options):
    """Generate genuine CV from invented CT; explicit full calendars are supported."""
    return json.loads(comparison._canonical(cv.build_calibration(build_original(days=days, **options))))


@lru_cache(maxsize=1)
def _base():
    return build_synthetic_upstream()


def source():
    return deepcopy(_base())


def specification():
    return {"id": comparison.SPECIFICATION_ID, "mode": comparison.MODE,
        "window": dict(comparison.WINDOW), "parameters": deepcopy(comparison.PARAMETERS),
        "resources": dict(comparison.RESOURCES), **comparison.REGISTERED_PATHS,
        "upstream_sha256": comparison.UPSTREAM_SHA256,
        "upstream_specification_sha256": comparison.UPSTREAM_SPECIFICATION_SHA256}


def records(report, arm, group, policy="D+1"):
    original = group == "original"
    provenance = report["upstream_provenance"] if original else report
    return [{"session": row["session"], "label": None if row["arms"][arm]["label"]["status"] == "unknown"
        else cv.cu._ratio(row["arms"][arm]["label"]["price_change"])}
        for row in provenance["variants"][policy]["days"]
        if row["arms"][arm]["status"] == ("signal" if original else "selected")]


def test_fixed_registration_sourcebound_choice_and_resource_constants():
    comparison.validate_spec(specification())
    assert comparison.SPECIFICATION_ID == "intraday-opening-impulse-comparison-v1"
    assert comparison.MODE == "observed-label-conditional-opening-impulse-descriptive-comparison"
    assert comparison.UPSTREAM_SHA256 == "e791c690b8a916c1b1ed7b111719ffc2ba06aca92e31b05a5ac146760e3b4f48"
    assert comparison.UPSTREAM_SPECIFICATION_SHA256 == "d012f691d4011e8aadcafe1dbecc89437bb27f83fbcae342ac233a3bf267015f"
    assert comparison.RESOURCES == cv.RESOURCES
    assert comparison.PARAMETERS["source_parameters"] == cv.PARAMETERS
    assert comparison.AUTHORIZATION["unknown_label_conditional_comparison_authorized"] is True
    assert comparison.AUTHORIZATION["underpower_exception_applied"] is False
    assert comparison.REGISTERED_PATHS["output_directory"].endswith("intraday-cw-opening-impulse-comparison-20261023-v1")


def test_repository_registration_exactly_matches_constants():
    comparison.validate_spec(json.loads(comparison.SPECIFICATION_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(extra=True),
    lambda value: value.update(upstream_sha256="a" * 64),
    lambda value: value.update(upstream_specification_sha256="a" * 64),
    lambda value: value.update(output_directory="/tmp/other"),
    lambda value: value["parameters"].update(bootstrap_seed=1),
    lambda value: value["parameters"].update(block_sessions=4),
    lambda value: value["parameters"].update(bootstrap_draws=1999),
    lambda value: value["parameters"].update(assumed_cost={"numerator": "34", "denominator": "10000"}),
    lambda value: value["parameters"].update(underpower_exception_applied=True),
    lambda value: value["resources"].update(cpu_seconds=600),
])
def test_registration_rejects_scope_changes(mutate):
    value = specification()
    mutate(value)
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal, match="specification_scope_mismatch"):
        comparison.validate_spec(value)


def test_eight_exact_means_four_contrasts_and_original_denominators():
    report = source()
    result = comparison.build_comparison(report)
    for policy, variant in result["variants"].items():
        assert set(variant) == {"by_arm"}
        for arm, cell in variant["by_arm"].items():
            for group, actual in cell["groups"].items():
                group_records = records(report, arm, group, policy)
                known = [row["label"] for row in group_records if row["label"] is not None]
                summary_source = report if group == "selected" else report["upstream_provenance"]
                summary = summary_source["variants"][policy]["by_arm"][arm]
                assert actual["known_labels"] == len(known)
                assert actual["event_count"] == len(group_records)
                assert actual["unknown_labels"] == len(group_records) - len(known)
                assert actual["gross_sum"] == sum(known, Fraction(0))
                assert actual["gross_mean"] == sum(known, Fraction(0)) / len(known)
                assert actual["net_mean"] == actual["gross_mean"] - Fraction(33, 10000)
                assert actual["assumed_cost"] == Fraction(33, 10000)
                assert actual["gross_positive_count"] == sum(value > 0 for value in known)
                assert actual["net_positive_count"] == sum(value > Fraction(33, 10000) for value in known)
                assert actual["gross_positive_fraction"] == Fraction(actual["gross_positive_count"], len(known))
                assert actual["net_positive_fraction"] == Fraction(actual["net_positive_count"], len(known))
                assert actual["source_own_label_dispersion"] == summary["own_label_dispersion"]
                assert actual["frequency"] == summary["frequency"]
                width = NormalDist().inv_cdf(.975) * summary["own_label_dispersion"]["design_se"]
                assert actual["descriptive_normal95_interval"] == {
                    "lower": float(actual["gross_mean"]) - width, "upper": float(actual["gross_mean"]) + width}
            groups = cell["groups"]
            assert groups["selected"]["event_count"] < groups["original"]["event_count"]
            contrast = cell["selected_minus_original"]
            expected = groups["selected"]["gross_mean"] - groups["original"]["gross_mean"]
            assert contrast["gross_mean_difference"] == contrast["net_mean_difference"] == expected
            assert contrast["fixed_cost_cancels_in_mean_difference"] is True


def test_whole_CV_CT_unknowns_source_flags_metadata_are_preserved_and_detached():
    report = source()
    report.update(specification_sha256=comparison.UPSTREAM_SPECIFICATION_SHA256,
        execution_manifest={"invented": {"cpu_seconds": 1.2}}, runtime={"invented": "runtime"})
    before = deepcopy(report)
    result = comparison.build_comparison(report)
    assert report == before
    assert result["upstream_provenance"] == before
    assert result["upstream_provenance"]["requires_operator_choice_before_comparison"] is True
    assert result["upstream_provenance"]["upstream_provenance"]["requires_operator_choice_before_comparison"] is True
    assert "requires_operator_choice_before_comparison" not in result
    assert result["requires_operator_choice"] is False
    assert result["operator_authorization"] == comparison.AUTHORIZATION
    assert result["comparison_performed"] is True and result["calibration_only"] is False
    for name in ("family_adopted", "full_family_certified", "performance_certified", "promotion_allowed",
            "actual_portfolio_pnl", "historical_costs_certified", "actual_fills_certified", "underpower_exception_applied"):
        assert result[name] is False
    assert result["reference_sessions"] == list(DEFAULT_DAYS)
    assert len(result["upstream_provenance"]["variants"]["D+1"]["days"]) == 20 * len(DEFAULT_DAYS)
    assert result["variants"]["D+1"]["by_arm"]["30"]["groups"]["selected"]["unknown_labels"] > 0
    result["upstream_provenance"]["execution_manifest"]["invented"]["cpu_seconds"] = 9
    result["upstream_provenance"]["variants"]["D+1"]["days"][0]["arms"]["15"]["label"]["entry_open"] = "999"
    result["variants"]["D+1"]["by_arm"]["15"]["groups"]["selected"]["frequency"]["forced_trades"] = True
    assert report == before


def test_joint_wholecalendar_influence_and_expanded_bootstrap_oracle():
    report = source()
    actual = comparison.build_comparison(report)["variants"]["D+1"]["by_arm"]["15"]["selected_minus_original"]
    uncertainty = actual["uncertainty"]
    reference = report["reference_sessions"]
    by_day = {day: {group: [] for group in comparison.GROUPS} for day in reference}
    known = {group: [row for row in records(report, "15", group) if row["label"] is not None]
        for group in comparison.GROUPS}
    means = {group: sum((row["label"] for row in known[group]), Fraction(0)) / len(known[group])
        for group in comparison.GROUPS}
    for group in comparison.GROUPS:
        for row in known[group]:
            by_day[row["session"]][group].append(row["label"])
    influences = [sum((value - means["selected"] for value in by_day[day]["selected"]), Fraction(0)) / len(known["selected"])
        - sum((value - means["original"] for value in by_day[day]["original"]), Fraction(0)) / len(known["original"])
        for day in reference]
    cluster = math.sqrt(len(reference) / (len(reference) - 1) * sum(float(value) ** 2 for value in influences))
    assert uncertainty["session_cluster_se"] == pytest.approx(cluster, rel=1e-13)
    naive = math.sqrt(sum(stdev(float(row["label"]) for row in known[group]) ** 2 / len(known[group])
        for group in comparison.GROUPS))
    assert uncertainty["naive_se"] == pytest.approx(naive, rel=1e-13)
    randomizer, draws, skipped = random.Random(0), [], 0
    for _ in range(2000):
        positions, remaining = [], len(reference)
        while remaining:
            start = randomizer.randrange(len(reference))
            length = min(5, remaining)
            positions.extend((start + offset) % len(reference) for offset in range(length))
            remaining -= length
        sampled = {group: [value for position in positions for value in by_day[reference[position]][group]]
            for group in comparison.GROUPS}
        if not all(sampled.values()):
            skipped += 1
            continue
        draws.append(float(sum(sampled["selected"], Fraction(0)) / len(sampled["selected"])
            - sum(sampled["original"], Fraction(0)) / len(sampled["original"])))
    assert uncertainty["bootstrap_se"] == pytest.approx(stdev(draws), rel=1e-12)
    assert uncertainty["bootstrap_valid_draws"] == len(draws)
    assert uncertainty["bootstrap_skipped_zero_denominator_draws"] == skipped
    assert uncertainty["design_se"] == max(uncertainty[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert uncertainty["corrected_to_naive_ratio"] == uncertainty["design_se"] / naive
    assert uncertainty["reference_sessions"] == len(reference)
    assert uncertainty["known_labels_by_group"] == {group: len(known[group]) for group in comparison.GROUPS}
    assert uncertainty["naive_reference"] == "independent_arm_formula_maxSE_floor_not_an_independence_or_coverage_claim"
    width = NormalDist().inv_cdf(.975) * uncertainty["design_se"]
    assert actual["descriptive_normal95_interval"] == {
        "lower": float(actual["gross_mean_difference"]) - width, "upper": float(actual["gross_mean_difference"]) + width}


def test_shared_subset_preserves_dependence_zero_dates_zero_denominator_draws():
    reference = [f"2025-09-{day:02d}" for day in range(4, 24)]
    joint = {group: [{"session": reference[0], "label": Fraction(1, 10000)},
        {"session": reference[1], "label": Fraction(-1, 10000)}] for group in comparison.GROUPS}
    actual = comparison._uncertainty(joint, reference, {group: Fraction(0) for group in comparison.GROUPS}, lambda: None)
    assert actual["session_cluster_se"] == actual["bootstrap_se"] == 0
    assert actual["naive_se"] > 0
    assert actual["bootstrap_skipped_zero_denominator_draws"] > 0
    assert actual["design_se"] == actual["naive_se"]


def test_zero_uncertainty_does_not_invent_corrected_ratio():
    joint = {group: [{"session": day, "label": Fraction(0)} for day in DEFAULT_DAYS] for group in comparison.GROUPS}
    actual = comparison._uncertainty(joint, list(DEFAULT_DAYS), {group: Fraction(0) for group in comparison.GROUPS}, lambda: None)
    assert actual["design_se"] == 0
    assert actual["corrected_to_naive_ratio"] is None


@pytest.mark.parametrize("mutate", [
    lambda report: report.update(study_id="other"),
    lambda report: report.update(mode="old_comparison"),
    lambda report: report.update(calibration_only=False),
    lambda report: report.update(full_family_certified=True),
    lambda report: report.update(specification_sha256="a" * 64),
    lambda report: report["parameters"].update(bootstrap_seed=1),
    lambda report: report["assumptions"].update(cost="certified"),
    lambda report: report["reference_sessions"].reverse(),
    lambda report: report["reference_weeks"].pop(),
    lambda report: report["variants"].pop("D+2"),
    lambda report: report["variants"]["D+1"]["days"].pop(),
    lambda report: report["variants"]["D+1"].update(comparison={}),
    lambda report: report["variants"]["D+1"]["days"][0].update(symbol="KRX:999999"),
    lambda report: report["variants"]["D+1"]["days"][0]["arms"]["15"].update(status="rejected"),
    lambda report: report["variants"]["D+1"]["days"][0]["arms"]["15"].update(opening_impulse=Fraction(34, 10000)),
    lambda report: report["variants"]["D+1"]["days"][0]["arms"]["15"].update(available_at="2025-09-04T09:18:00+09:00"),
    lambda report: report["variants"]["D+1"]["days"][0]["arms"]["15"].update(data_vintage="a" * 64),
    lambda report: report["variants"]["D+1"]["days"][0]["arms"]["15"]["label"].update(entry_open="999"),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["selection_counts"].update(selected=0),
    lambda report: report["variants"]["D+1"]["by_arm"]["30"]["label_counts"].update(unknown=0),
    lambda report: report["variants"]["D+1"]["by_arm"]["30"].update(requires_operator_choice_before_comparison=False),
    lambda report: report["variants"]["D+1"]["by_arm"]["30"]["comparison_choice_reasons"].append("old_waiver"),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"].update(design_se=0),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["frequency"].update(reference_weeks=1),
    lambda report: next(iter(report["variants"]["D+1"]["by_arm"]["15"]["by_week"].values()))["label_counts"].update(known=0),
    lambda report: next(iter(report["variants"]["D+1"]["by_arm"]["15"]["by_month"].values()))["selection_counts"].update(unknown=0),
    lambda report: report.update(requires_operator_choice_before_comparison=False),
    lambda report: report["upstream_provenance"]["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"].update(design_se=0),
    lambda report: report["upstream_provenance"]["variants"]["D+1"]["days"][0]["arms"]["15"]["label"].update(actual_fill_certified=True),
    lambda report: report["upstream_provenance"]["source_target_matrix"]["D+1"]["days"][0]["targets"].pop(),
])
def test_CV_or_embedded_CT_corruption_refuses_before_comparison(mutate):
    report = source()
    mutate(report)
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal):
        comparison.build_comparison(report)


def test_selected_unknown_cannot_be_zero_filled():
    report = source()
    feature = next(row["arms"]["30"] for row in report["variants"]["D+1"]["days"]
        if row["arms"]["30"]["status"] == "selected" and row["arms"]["30"]["label"]["status"] == "unknown")
    feature["label"]["price_change"] = Fraction(0)
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal, match="feature_or_metadata"):
        comparison.build_comparison(report)


def test_cost_boundary_is_strict_and_gross_zero_is_distinct():
    original = build_original()
    outcomes = (Fraction(0), Fraction(33, 10000), Fraction(331, 100000), Fraction(-1, 100000))
    for variant in original["variants"].values():
        for arm in comparison.ARMS:
            indices = {"selected": 0, "rejected": 0}
            for row in variant["days"]:
                finding = row["arms"][arm]
                if finding["status"] == "signal" and finding["label"]["status"] == "known":
                    selection = cv._feature(row, arm)["status"]
                    value = outcomes[indices[selection] % len(outcomes)]
                    finding["label"].update(entry_open=Fraction(100), exit_open=100 * (1 + value), price_change=value)
                    indices[selection] += 1
    _refresh_own(original)
    report = cv.build_calibration(original)
    result = comparison.build_comparison(report)
    for policy in result["variants"]:
        for arm, cell in result["variants"][policy]["by_arm"].items():
            for group, actual in cell["groups"].items():
                known = [row["label"] for row in records(report, arm, group, policy) if row["label"] is not None]
                assert Fraction(0) in known and Fraction(33, 10000) in known
                assert actual["gross_positive_count"] == sum(value > 0 for value in known)
                assert actual["net_positive_count"] == sum(value > Fraction(33, 10000) for value in known)
                assert actual["net_positive_count"] < sum(value >= Fraction(33, 10000) for value in known)


def test_validate_complete_source_before_means_without_rebuilding_CV(monkeypatch):
    report = source()
    monkeypatch.setattr(cv, "build_calibration", lambda *args, **kwargs: pytest.fail("whole_CV_rebuild"))
    assert comparison.build_comparison(report)["study_id"] == comparison.SPECIFICATION_ID
    intervals = []
    monkeypatch.setattr(comparison, "_interval", lambda *args: intervals.append(args))
    report["variants"]["D+2"]["by_arm"]["30"]["frequency"]["forced_trades"] = True
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal, match="source_selected_summary"):
        comparison.build_comparison(report)
    assert not intervals


def test_genuine_selected_underpower_failure_is_not_waived():
    report = build_synthetic_upstream(sparse_volatile_selection=True)
    assert report["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] > 33
    assert report["upstream_provenance"]["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] < 33
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal, match="sizing_failure_not_authorized"):
        comparison.build_comparison(report)


def test_undefined_selected_sizing_is_not_waived():
    original = build_original()
    for variant in original["variants"].values():
        for row in variant["days"]:
            for arm in comparison.ARMS:
                if row["arms"][arm]["status"] == "signal":
                    context = row["arms"][arm]["event"]["context"]
                    context[-1].update(close="100", high="101", low="99")
    report = cv.build_calibration(original)
    assert report["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] is None
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal, match="sizing_failure_not_authorized"):
        comparison.build_comparison(report)


def test_checkpoint_propagates_no_partial_return():
    def stop():
        raise RuntimeError("invented_budget_stop")
    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        comparison.build_comparison(source(), checkpoint=stop)


def test_exact_fraction_serialization_and_frequent_budget_callbacks():
    callbacks = []
    result = comparison.build_comparison(source(), checkpoint=lambda: callbacks.append(True))
    assert len(callbacks) > 8000
    encoded = json.loads(comparison._canonical(result))
    for policy in result["variants"]:
        for arm in comparison.ARMS:
            for group in comparison.GROUPS:
                mean = result["variants"][policy]["by_arm"][arm]["groups"][group]["gross_mean"]
                assert encoded["variants"][policy]["by_arm"][arm]["groups"][group]["gross_mean"] == {
                    "numerator": str(mean.numerator), "denominator": str(mean.denominator)}


def test_joint_uncertainty_refuses_single_known_date_and_zero_valid_draws(monkeypatch):
    reference = [f"2025-09-{day:02d}" for day in range(4, 24)]
    joint = {group: [{"session": reference[0], "label": Fraction(1, 10000)},
        {"session": reference[0], "label": Fraction(-1, 10000)}] for group in comparison.GROUPS}
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal, match="insufficient_known_labels_or_dates"):
        comparison._uncertainty(joint, reference, {group: Fraction(0) for group in comparison.GROUPS}, lambda: None)
    for group in comparison.GROUPS:
        joint[group][1]["session"] = reference[1]
    monkeypatch.setattr(comparison.random.Random, "randrange", lambda self, size: 5)
    with pytest.raises(comparison.OpeningImpulseComparisonRefusal, match="insufficient_valid_joint_bootstrap"):
        comparison._uncertainty(joint, reference, {group: Fraction(0) for group in comparison.GROUPS}, lambda: None)
