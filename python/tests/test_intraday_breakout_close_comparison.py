"""Invented completed DK/DM wires only; actual DN inputs are never opened."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
import inspect
import json
import math
import random
from statistics import stdev

import pytest

from research import intraday_breakout_close_calibration as dm
from research import intraday_breakout_close_comparison as close
from test_intraday_breakout_close_calibration import build_synthetic_fixture as _dm_dk_fixture
from test_intraday_breakout_loss_concentration import _set_return


def build_synthetic_fixture(*, full=False, metadata_padding=0):
    """Prepare invented DK and completed DM before measuring the DN path.

    Full geometry matches the frozen counts, with varied integer prices and
    251 exact return values. Missing source signals/outside-book labels remain;
    in-book missing/flat features are exercised by separate blocking cases.
    No actual input or ancestor experiment is read or executed.
    """
    source = _dm_dk_fixture(full=full, metadata_padding=metadata_padding)
    for policy in close.POLICIES:
        originals = [row for row in source["variants"][policy]["days"] if row["breakout_exit_policy"] is not None]
        for index, row in enumerate(originals):
            base = 1000000 + index * 17
            opening = dm._opening(row["session"])
            context = []
            for offset in range(3):
                start = opening + offset * timedelta(minutes=5)
                context.append({"start_at": start.isoformat(), "end_at": (start + timedelta(minutes=5)).isoformat(),
                    "available_at": (start + timedelta(minutes=7)).isoformat(),
                    "open": str(base), "high": str(base + 1), "low": str(base - 1), "close": str(base)})
            row["arms"]["basic"]["event"]["context"] = context
            selected = index < close.EXPECTED_GEOMETRY["selected_events"][policy] if full else index % 3 != 2
            start = close.dl._instant(row["arms"]["basic"]["event"]["signal_bar_start"])
            row["arms"]["retest"]["path"]["breakout_bar"] = {"start_at": start.isoformat(),
                "end_at": (start + timedelta(minutes=5)).isoformat(),
                "available_at": (start + timedelta(minutes=7)).isoformat(),
                "open": str(base + 1), "high": str(base + 4), "low": str(base),
                "close": str(base + (3 if selected else 2))}
            gross = Fraction((index * 73) % 251 - 125, 100000)
            _set_return(row, gross, entry_price=str(1000000 + index * 137))
    calibration = dm.build_calibration(source)
    calibration["specification_sha256"] = close.DM_SPECIFICATION_SHA256
    return source, calibration


def _fixture():
    return build_synthetic_fixture()


def _first(source, policy="D+1"):
    return next(row for row in source["variants"][policy]["days"] if row["breakout_exit_policy"] is not None)


def _recalibrate(source):
    calibration = dm.build_calibration(source)
    calibration["specification_sha256"] = close.DM_SPECIFICATION_SHA256
    return calibration


def test_exact_specification_resources_and_narrow_source_only_import_closure():
    close.validate_spec(json.loads(close.SPECIFICATION_PATH.read_text()))
    assert close.RESOURCES == dm.RESOURCES
    source = inspect.getsource(close)
    assert "dm.build_calibration(" not in source and "dl._variant(" not in source and "dl._event(" not in source
    assert "intraday_opening_range_comparison" not in source and "sqlite3" not in source
    assert close.EXPECTED_GEOMETRY["selected_events"] == {"D+1": 895, "D+2": 893}


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"].update(bootstrap_seed=1),
    lambda spec: spec["parameters"]["next_hypothesis"]["threshold"].update(numerator="2"),
    lambda spec: spec["parameters"]["assumed_cost"].update(numerator="34"),
    lambda spec: spec["parameters"]["expected_actual_geometry"]["selected_events"].update(**{"D+1": 896}),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec["upstream_sha256"].update(DM="a" * 64),
    lambda spec: spec.update(extra=True),
])
def test_specification_refuses_before_source(change):
    spec = json.loads(close.SPECIFICATION_PATH.read_text())
    change(spec)
    with pytest.raises(close.BreakoutCloseComparisonRefusal, match="specification_scope"):
        close.build_comparison({}, {}, specification=spec)


def test_all_targets_states_original_book_and_exact_DM_selection_are_preserved():
    dk, calibration = _fixture()
    before = deepcopy((dk, calibration))
    result = close.build_comparison(dk, calibration)
    assert (dk, calibration) == before
    assert result["comparison_performed"] and result["new_performance_families"] == 1
    assert not result["new_signals_calculated"] and not result["new_labels_calculated"]
    assert not result["new_selection_calculated"] and not result["upstream_statistics_recalculated"]
    assert not result["subgroup_return_means_calculated"] and not result["promotion_allowed"]
    for policy, variant in result["variants"].items():
        original = calibration["variants"][policy]
        assert variant["target_states"] == original["target_states"]
        assert variant["population"] == original["population"]
        assert variant["selected_coordinates"] == original["selected_coordinates"]
        assert variant["population"]["targets"] == 16
        assert variant["population"]["original_book_events"] == 6
        assert variant["population"]["state_counts"] == {
            "selected": 4, "rejected": 2, "unknown": 1, "not_applicable": 8, "outside_original_book": 1}
        assert len(variant["original_book_coordinates"]) == 6  # Never expands to seven signals.
        assert variant["source_rows_sha256"] == original["source_rows_sha256"]
    assert result["source_identity"]["canonical_DK_sha256"] == close._digest(dk)
    assert result["source_identity"]["canonical_DM_sha256"] == close._digest(calibration)


@pytest.mark.parametrize("change", [
    lambda source: source.update(specification_sha256="a" * 64),
    lambda source: source.update(reference_weeks=[]),
    lambda source: source["source_target_matrix"]["D+1"]["days"][0]["targets"][0].update(rank=2),
    lambda source: source["variants"]["D+1"]["days"][-1].update(retained_metadata="new"),
    lambda source: source["variants"]["D+1"]["days"][0].update(outcome_complete=False),
    lambda source: source["variants"]["D+1"]["days"][0]["arms"]["basic"]["label"].update(exit_open="1"),
])
def test_DK_tampering_without_frozen_DM_linkage_refuses(change):
    dk, calibration = _fixture()
    change(dk)
    with pytest.raises(close.BreakoutCloseComparisonRefusal):
        close.build_comparison(dk, calibration)


@pytest.mark.parametrize("change", [
    lambda source: source.update(specification_sha256="a" * 64),
    lambda source: source.update(comparison_performed=True),
    lambda source: source["source_identity"].update(source_target_matrix_sha256="b" * 64),
    lambda source: source["variants"]["D+1"]["selected_coordinates"].pop(),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(state="rejected"),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(feature_available_at="2025-09-04T09:25:00+09:00"),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(source_row_sha256="a" * 64),
    lambda source: source["variants"]["D+1"]["by_week"][next(iter(source["variants"]["D+1"]["by_week"]))].update(targets=999),
    lambda source: source["variants"]["D+1"]["own_label_dispersion"].update(events=999),
    lambda source: source["variants"]["D+1"]["comparison_choice_reasons"].append("invented_waiver"),
    lambda source: source["variants"]["D+1"].update(source_variant_sha256="a" * 64),
])
def test_DM_tampering_refuses_without_replaying_calibration(change):
    dk, calibration = _fixture()
    change(calibration)
    with pytest.raises(close.BreakoutCloseComparisonRefusal):
        close.build_comparison(dk, calibration)


@pytest.mark.parametrize("change", [
    lambda row: row["arms"]["retest"]["path"]["breakout_bar"].update(available_at="2025-09-04T09:25:00+09:00"),
    lambda row: row["arms"]["basic"]["event"]["context"][1].update(start_at="2025-09-04T09:06:00+09:00"),
    lambda row: row["arms"]["basic"]["event"]["context"][2].update(available_at="2025-09-04T09:25:00+09:00"),
    lambda row: row["arms"]["basic"]["label"].update(entry_at="2025-09-04T09:26:00+09:00"),
    lambda row: row["breakout_exit_policy"]["time_only"].update(label_available_at="2025-09-04T10:00:00+09:00"),
    lambda row: row["breakout_exit_policy"]["common"].update(anchor_open="1"),
    lambda row: row.update(signal_observable=False),
])
def test_even_rebound_DK_fingerprints_cannot_hide_changed_causal_clocks_prices_or_masks(change):
    dk, calibration = _fixture()
    change(_first(dk))
    calibration["source_identity"]["canonical_source_sha256"] = close._digest(dk)
    with pytest.raises(close.BreakoutCloseComparisonRefusal):
        close.build_comparison(dk, calibration)


def test_exact_costs_positive_equality_and_conditional_difference():
    dk, _ = _fixture()
    # Cost equality is neither strictly cost-exceeding nor net-positive.
    values = [Fraction(-1, 10000), close.COST, Fraction(34, 10000),
        Fraction(), close.COST, Fraction(35, 10000)]
    for policy in close.POLICIES:
        originals = [row for row in dk["variants"][policy]["days"] if row["breakout_exit_policy"] is not None]
        for row, value in zip(originals, values, strict=True):
            _set_return(row, value)
    result = close.build_comparison(dk, _recalibrate(dk))
    assert result["comparison_performed"]
    for variant in result["variants"].values():
        basic, selected = (variant["by_arm"][arm] for arm in close.ARMS)
        selected_values = [values[index] for index in (0, 1, 3, 4)]
        assert basic["gross_sum"] == sum(values) and basic["net_sum"] == sum(values) - 6 * close.COST
        assert selected["gross_mean"] == sum(selected_values) / 4
        assert selected["net_mean"] == selected["gross_mean"] - close.COST
        assert basic["gross_median"] == close.COST
        assert basic["net_median"] == 0
        assert basic["gross_positive_count"] == 4 and basic["endpoint_gross_strictly_above_cost_count"] == 2
        assert basic["endpoint_gross_strictly_above_cost_fraction"] == Fraction(1, 3)
        assert selected["endpoint_gross_strictly_above_cost_count"] == 0
        contrast = variant["comparison"]["selected_minus_basic"]
        assert contrast["gross_mean_difference"] == selected["gross_mean"] - basic["gross_mean"]
        assert contrast["net_mean_difference"] == selected["net_mean"] - basic["net_mean"]
        assert contrast["gross_mean_difference"] == contrast["net_mean_difference"]


def test_all_four_fresh_own_plannings_precede_first_new_performance_and_no_old_pipeline(monkeypatch):
    dk, calibration = _fixture()
    expected = {(policy, arm): close.dm._dispersion([
        {"session": row["session"], "label": close.dl._ratio(row["arms"]["basic"]["label"]["price_change"])}
        for row in dk["variants"][policy]["days"] if row["breakout_exit_policy"] is not None
        and (arm == "basic" or [row["session"], row["symbol"]] in calibration["variants"][policy]["selected_coordinates"])],
        dk["reference_sessions"], lambda: None) for policy in close.POLICIES for arm in close.ARMS}
    original_dispersion, original_performance = dm._dispersion, close._performance
    calls = []
    def fresh(records, reference, checkpoint):
        calls.append(len(records))
        return original_dispersion(records, reference, checkpoint)
    def performance(records):
        assert calls == [6, 4, 6, 4]
        return original_performance(records)
    def forbidden(*args, **kwargs):
        raise AssertionError("old experiment replay")
    monkeypatch.setattr(dm, "_dispersion", fresh)
    monkeypatch.setattr(dm, "build_calibration", forbidden)
    monkeypatch.setattr(close.dl, "_variant", forbidden)
    monkeypatch.setattr(close, "_performance", performance)
    result = close.build_comparison(dk, calibration)
    for (policy, arm), own in expected.items():
        assert result["variants"][policy]["by_arm"][arm]["own_label_dispersion"] == own


@pytest.mark.parametrize("mde", [None, float("inf"), 34.0])
def test_failed_or_undefined_any_fresh_planning_finishes_calibration_without_any_new_mean(monkeypatch, mde):
    dk, calibration = _fixture()
    original, calls = dm._dispersion, []
    def fresh(records, reference, checkpoint):
        own = original(records, reference, checkpoint)
        calls.append(len(records))
        if len(calls) == 4:
            own["mde_bps"] = mde
        return own
    def forbidden(*args):
        raise AssertionError("new means before all-four gate")
    monkeypatch.setattr(dm, "_dispersion", fresh)
    monkeypatch.setattr(close, "_performance", forbidden)
    result = close.build_comparison(dk, calibration)
    assert calls == [6, 4, 6, 4]
    assert not result["comparison_performed"] and result["new_performance_families"] == 0
    assert result["requires_operator_choice"] and result["comparison_not_run_reasons"]
    for variant in result["variants"].values():
        assert variant["comparison"] is None
        assert all("gross_mean" not in arm and "net_mean" not in arm for arm in variant["by_arm"].values())


@pytest.mark.parametrize("feature", ["missing", "flat", "missing_context"])
def test_unknown_original_book_features_retain_existing_checkpoint_even_with_fresh_sizing_pass(feature):
    dk, _ = _fixture()
    row = _first(dk)
    if feature == "missing":
        row["arms"]["retest"]["path"]["breakout_bar"] = None
    elif feature == "flat":
        row["arms"]["retest"]["path"]["breakout_bar"].update(open="1000004", high="1000004", low="1000004", close="1000004")
    else:
        row["arms"]["basic"]["event"].pop("context")
    calibration = _recalibrate(dk)
    result = close.build_comparison(dk, calibration)
    assert not result["comparison_performed"] and result["requires_operator_choice"]
    assert result["variants"]["D+1"]["sizing_passed"]
    assert "D+1:unknown_original_book_features" in result["comparison_not_run_reasons"]
    assert result["variants"]["D+1"]["target_states"][0]["state"] == "unknown"
    assert result["variants"]["D+1"]["population"]["original_book_events"] == 6


def test_weekly_counts_include_zero_weeks_and_retained_fraction_without_weekly_performance():
    dk, calibration = _fixture()
    result = close.build_comparison(dk, calibration)
    for variant in result["variants"].values():
        for arm, events in (("basic", 6), ("selected", 4)):
            summary = variant["by_arm"][arm]
            assert len(summary["by_week"]) == 4 and list(summary["by_week"].values())[-2:] == [0, 0]
            assert summary["frequency"]["mean_per_all_reference_weeks"] == Fraction(events, 4)
            assert summary["frequency"]["retained_opportunity_fraction"] == Fraction(events, 6)
            assert summary["frequency"]["weeks_at_least15"] == 0
            assert sum(summary["by_week"].values()) == events
            assert all(type(count) is int for count in summary["by_week"].values())
    empty = close._performance([])
    assert empty["gross_sum"] == empty["net_sum"] == 0
    assert empty["gross_mean"] is empty["net_mean"] is empty["gross_median"] is empty["net_median"] is None
    assert empty["gross_positive_fraction"] is empty["net_positive_fraction"] is None


def test_overlap_aware_joint_uncertainty_matches_explicit_shared_session_resampling():
    reference = ["2025-09-04", "2025-09-11", "2025-09-18", "2025-09-25", "2025-10-02", "2025-10-09", "2025-10-16"]
    records = {"basic": [{"session": day, "label": Fraction(index, 1000)}
        for index, day in enumerate(reference[:-1])], "selected": []}
    records["selected"] = [records["basic"][index] for index in (0, 2, 4)]
    means = {arm: sum(row["label"] for row in rows) / len(rows) for arm, rows in records.items()}
    actual = close._uncertainty(records, reference, means, lambda: None)
    randomizer, draws = random.Random(0), []
    for _ in range(2000):
        sampled = []
        for size in (5, 2):
            start = randomizer.randrange(7)
            sampled.extend(reference[(start + offset) % 7] for offset in range(size))
        values = {arm: [row["label"] for day in sampled for row in rows if row["session"] == day]
            for arm, rows in records.items()}
        if all(values.values()):
            draws.append(float(sum(values["selected"]) / len(values["selected"]))
                - float(sum(values["basic"]) / len(values["basic"])))
    assert actual["bootstrap_se"] == pytest.approx(stdev(draws), abs=1e-18)
    assert actual["bootstrap_valid_draws"] == len(draws)
    assert actual["bootstrap_skipped_zero_denominator_draws"] == 2000 - len(draws)
    assert actual["same_blocks_for_both_arms"] and actual["overlapping_known_labels"] == 3
    assert actual["design_se"] == max(actual["naive_se"], actual["session_cluster_se"], actual["bootstrap_se"])
    assert actual["corrected_to_naive_ratio"] == actual["design_se"] / actual["naive_se"]
    assert not actual["own_arm_planning_certifies_contrast_power"]


def test_identical_overlapping_arms_have_zero_joint_variance_but_retain_naive_reference_floor():
    reference = ["2025-09-04", "2025-09-11"]
    rows = [{"session": day, "label": Fraction(index, 100)} for index, day in enumerate(reference)]
    records = {arm: rows for arm in close.ARMS}
    uncertainty = close._uncertainty(records, reference, {arm: Fraction(1, 200) for arm in close.ARMS}, lambda: None)
    assert uncertainty["session_cluster_se"] == uncertainty["bootstrap_se"] == 0
    assert uncertainty["design_se"] == uncertainty["naive_se"] > 0


def test_full_invented_geometry_and_varied_exact_prices_and_ratios():
    dk, calibration = build_synthetic_fixture(full=True)
    result = close.build_comparison(dk, calibration)
    assert result["comparison_performed"]
    assert len(result["reference_sessions"]) == 257 and len(result["reference_weeks"]) == 56
    for policy, count in (("D+1", 1169), ("D+2", 1167)):
        variant = result["variants"][policy]
        assert variant["population"]["targets"] == 5140
        assert variant["population"]["original_book_events"] == variant["by_arm"]["basic"]["events"] == count
        assert variant["by_arm"]["selected"]["events"] == close.EXPECTED_GEOMETRY["selected_events"][policy]
        assert variant["population"]["state_counts"]["outside_original_book"] == 12
        originals = [row for row in dk["variants"][policy]["days"] if row["breakout_exit_policy"] is not None]
        assert len({row["arms"]["basic"]["label"]["entry_open"] for row in originals}) >= 250
        assert len({close.dl._ratio(row["arms"]["basic"]["label"]["price_change"]) for row in originals}) >= 250
        for arm in close.ARMS:
            own = variant["by_arm"][arm]["own_label_dispersion"]
            assert own["reference_sessions"] == 257 and math.isfinite(own["mde_bps"]) and own["mde_bps"] <= 33
            assert len(variant["by_arm"][arm]["by_week"]) == 56
