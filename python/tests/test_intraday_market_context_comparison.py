"""Invented completed books only; no actual market body or source replay."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import inspect
import json
import math

import pytest

from research import intraday_market_context_comparison as comparison
from research import intraday_market_context_calibration as calibration
from research import intraday_market_context_readiness as market
from test_intraday_market_context_readiness import build_synthetic_fixture as _market_fixture
from test_intraday_market_context_calibration import _result as _calibrate
from test_intraday_breakout_loss_concentration import _set_return


def build_synthetic_fixture(*, full=False, metadata_padding=0, provenance_density=0):
    """Fresh invented DK/DR source pair, never registered actual observations."""
    dk, lines = _market_fixture(full=full, metadata_padding=metadata_padding,
        provenance_density=provenance_density)
    for policy in comparison.POLICIES:
        originals = [row for row in dk["variants"][policy]["days"] if row["breakout_exit_policy"] is not None]
        for index, row in enumerate(originals):
            _set_return(row, Fraction((index * 73) % 251 - 125, 100000),
                entry_price=str(1000000 + index * 137))
        # The inherited minute-coverage fixture marks high-rank members' basic
        # status unknown. Restore separate invented common-mask exclusions so
        # an original basic signal outside the book is exercised explicitly.
        templates = {row["session"]: row for row in reversed(originals)}
        outside = [row for row in dk["variants"][policy]["days"]
            if row["arms"]["basic"]["status"] == "unknown" and row["session"] in templates]
        for row in outside[:12 if full else 1]:
            row["arms"]["basic"] = deepcopy(templates[row["session"]]["arms"]["basic"])
            row["arms"]["retest"].update(status="unknown", event=None, label=None)
            row.update(signal_observable=False, outcome_complete=False,
                exclusion_reasons=["retest:unknown_signal"], breakout_exit_policy=None)
    dq = market.build_readiness(dk, lines())
    for policy in comparison.POLICIES:
        for index, event in enumerate(dq["variants"][policy]["events"]):
            for peer in event["peers"]:
                if peer["state"] == "complete":
                    peer.update(first_open="100", last_close="101" if index % 3 != 2 else "99")
    dr = _calibrate(dk, dq)
    dr["specification_sha256"] = comparison.DR_SPECIFICATION_SHA256
    return dk, dr


@pytest.fixture
def sources():
    return build_synthetic_fixture()


def _build(dk, dr):
    return comparison.build_comparison(comparison.project_dk(dk), dr)


def _spec():
    return json.loads(comparison.SPECIFICATION_PATH.read_text())


def test_exact_registered_scope_and_source_free_dependency_boundary():
    comparison.validate_spec(_spec())
    assert comparison.RESOURCES == calibration.RESOURCES
    source = inspect.getsource(comparison)
    assert not any(value in source for value in ("calibration.Calibrator(", "dr.Calibrator(",
        "dr.build_calibration(", "dr._feature(", "dm._feature(", "dn.build_comparison(",
        "read_bytes(", "sqlite3", "gzip.open(", "market.build_readiness("))
    assert comparison._performance is comparison.dn._performance
    assert comparison._uncertainty is comparison.dn._uncertainty
    assert comparison.ARMS == comparison.dn.ARMS and comparison.COST == comparison.dn.COST
    assert comparison.PARAMETERS["block_sessions"] == comparison.dn.PARAMETERS["block_sessions"] == 5
    assert comparison.PARAMETERS["bootstrap_draws"] == comparison.dn.PARAMETERS["bootstrap_draws"] == 2000
    assert comparison.PARAMETERS["bootstrap_seed"] == comparison.dn.PARAMETERS["bootstrap_seed"] == 0


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"].update(bootstrap_seed=1),
    lambda spec: spec["parameters"]["next_hypothesis"].update(minimum_complete_peer_windows=4),
    lambda spec: spec["parameters"]["assumed_cost"].update(numerator="34"),
    lambda spec: spec["parameters"]["expected_actual_geometry"]["selected_events"].update(**{"D+1": 887}),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec["upstream_sha256"].update(DR="a" * 64),
    lambda spec: spec.update(extra=True),
])
def test_specification_refuses_before_access(change):
    spec = _spec()
    change(spec)
    with pytest.raises(comparison.MarketContextComparisonRefusal, match="specification_scope"):
        comparison.build_comparison({}, {}, specification=spec)


def test_same_observed_scope_preserves_unknowns_and_compact_hash_bound_ledger(sources):
    dk, dr = sources
    before = deepcopy((dk, dr))
    result = _build(dk, dr)
    assert (dk, dr) == before
    assert result["comparison_performed"] and result["new_performance_families"] == 1
    for name in ("new_signals_calculated", "new_labels_calculated", "new_selection_calculated",
            "upstream_statistics_recalculated", "peer_returns_calculated",
            "peer_return_mean_or_sign_calculated", "subgroup_return_means_calculated", "promotion_allowed"):
        assert result[name] is False
    for policy, variant in result["variants"].items():
        frozen = dr["variants"][policy]
        assert variant["population"] == frozen["population"]
        assert variant["selected_coordinates"] == frozen["selected_coordinates"]
        original = [row for row in frozen["target_states"] if row["in_original_book"]]
        observed = [row for row in original if row["state"] != "unknown"]
        excluded = [row for row in original if row["state"] == "unknown"]
        assert excluded  # This scope is explicitly approved, not an inherited waiver.
        assert variant["by_arm"]["basic"]["events"] == len(observed)
        assert variant["by_arm"]["selected"]["events"] == len(frozen["selected_coordinates"])
        assert variant["excluded_unknown_feature_coordinates"] == [[row["session"], row["symbol"]] for row in excluded]
        assert len(variant["observed_basic_coordinates"]) + len(excluded) == len(original)
        for receipt, source in zip(variant["target_states"], frozen["target_states"], strict=True):
            assert set(receipt) == set(comparison._COMPACT_FIELDS) | {"source_target_state_sha256"}
            assert receipt["source_target_state_sha256"] == comparison._digest(source)
            assert all(receipt[key] == source[key] for key in comparison._COMPACT_FIELDS)
            assert "raw_chunk_metadata" not in receipt and "membership_source" not in receipt
        assert all(row["state"] == "unknown" for row in variant["excluded_unknown_feature_ledger"])
        assert all(variant["reconciliation"].values())
    assert result["source_identity"]["canonical_DK_sha256"] == comparison._digest(dk)
    assert result["source_identity"]["canonical_DR_sha256"] == comparison._digest(dr)


@pytest.mark.parametrize("change", [
    lambda source: source.update(specification_sha256="a" * 64),
    lambda source: source["source_target_matrix"]["D+1"]["days"][0]["targets"][0].update(rank=2),
    lambda source: source["variants"]["D+1"]["days"][0].update(data_vintage="b" * 64),
    lambda source: source["variants"]["D+1"]["days"][0]["arms"]["basic"]["label"].update(exit_open="1"),
    lambda source: source["variants"]["D+1"]["days"][0]["arms"]["basic"]["event"].update(decision_at="2025-09-04T09:23:00+09:00"),
])
def test_original_DK_tampering_refuses(sources, change):
    dk, dr = sources
    change(dk)
    with pytest.raises((comparison.MarketContextComparisonRefusal, calibration.MarketContextCalibrationRefusal)):
        _build(dk, dr)


@pytest.mark.parametrize("change", [
    lambda source: source.update(specification_sha256="a" * 64),
    lambda source: source.update(peer_return_mean_or_sign_calculated=False),
    lambda source: source.update(reference_weeks=[]),
    lambda source: source["source_identity"].update(source_target_matrix_sha256="b" * 64),
    lambda source: source["source_identity"].update(completed_DQ_sha256="b" * 64),
    lambda source: source["variants"]["D+1"]["selected_coordinates"].pop(),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(source_row_sha256="a" * 64),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(feature_available_at="2025-09-04T09:25:00+09:00"),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(complete_peer_windows=True),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(source_context_event_sha256=None),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(original_label=None),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(observed_peer_mean_price_change=None),
    lambda source: source["variants"]["D+1"]["target_states"][0].update(unsupported=True),
    lambda source: source["variants"]["D+1"]["by_week"][next(iter(source["variants"]["D+1"]["by_week"]))].update(targets=999),
    lambda source: source["variants"]["D+1"].update(source_rows_sha256="b" * 64),
    lambda source: source["variants"]["D+1"]["reconciliation"].update(original_book_preserved=False),
])
def test_DR_scope_linkage_and_clock_tampering_refuses_without_predicate_replay(sources, change):
    dk, dr = sources
    change(dr)
    with pytest.raises(comparison.MarketContextComparisonRefusal):
        _build(dk, dr)


def test_unknown_features_are_not_zero_or_negative_and_never_enter_either_book(sources):
    dk, dr = sources
    result = _build(dk, dr)
    for policy in comparison.POLICIES:
        variant = result["variants"][policy]
        excluded = set(map(tuple, variant["excluded_unknown_feature_coordinates"]))
        assert excluded.isdisjoint(map(tuple, variant["observed_basic_coordinates"]))
        assert excluded.isdisjoint(map(tuple, variant["selected_coordinates"]))
        for row in variant["excluded_unknown_feature_ledger"]:
            assert row["observed_peer_mean_price_change"] is None
            assert row["complete_peer_windows"] < 5
            assert row["reason"] == "fewer_than_five_complete_peer_windows"


def test_all_four_new_plannings_precede_every_mean_and_old_statistics_do_not_authorize(sources, monkeypatch):
    dk, dr = sources
    # Authenticated source statistics are retained only through hashes. They do
    # not substitute for the fresh statistics, including when they look bad.
    for variant in dr["variants"].values():
        variant["own_label_dispersion"]["mde_bps"] = 999999
    original_dispersion, original_performance = comparison.dm._dispersion, comparison._performance
    calls = []
    def size(records, reference, checkpoint):
        calls.append(len(records))
        return original_dispersion(records, reference, checkpoint)
    def performance(records):
        assert len(calls) == 4
        return original_performance(records)
    def forbidden(*args, **kwargs):
        raise AssertionError("old source pipeline or predicate was replayed")
    monkeypatch.setattr(comparison.dm, "_dispersion", size)
    monkeypatch.setattr(comparison, "_performance", performance)
    monkeypatch.setattr(calibration, "Calibrator", forbidden)
    monkeypatch.setattr(calibration, "_feature", forbidden)
    monkeypatch.setattr(calibration, "build_calibration", forbidden)
    monkeypatch.setattr(comparison.dn, "build_comparison", forbidden)
    result = _build(dk, dr)
    assert len(calls) == 4 and result["comparison_performed"]
    assert calls == [result["variants"][policy]["by_arm"][arm]["events"]
        for policy in comparison.POLICIES for arm in comparison.ARMS]


@pytest.mark.parametrize("mde", [None, float("inf"), float("nan"), True, -1.0, 34.0])
def test_any_new_own_gate_failure_finishes_calibration_without_all_new_means(sources, monkeypatch, mde):
    dk, dr = sources
    original, calls = comparison.dm._dispersion, []
    def size(records, reference, checkpoint):
        own = original(records, reference, checkpoint)
        calls.append(len(records))
        if len(calls) == 4:
            own["mde_bps"] = mde
        return own
    def forbidden(*args):
        raise AssertionError("mean activated before all four own gates passed")
    monkeypatch.setattr(comparison.dm, "_dispersion", size)
    monkeypatch.setattr(comparison, "_performance", forbidden)
    result = _build(dk, dr)
    assert len(calls) == 4
    assert not result["comparison_performed"] and result["new_performance_families"] == 0
    assert result["requires_operator_choice"] and result["comparison_not_run_reasons"]
    for variant in result["variants"].values():
        assert variant["comparison"] is None
        for arm in variant["by_arm"].values():
            assert "gross_mean" not in arm and "net_mean" not in arm


def test_new_unknown_label_gate_is_not_waived_by_prior_scope_approval(sources, monkeypatch):
    dk, dr = sources
    original = comparison.dm._dispersion
    def size(records, reference, checkpoint):
        own = original(records, reference, checkpoint)
        own["unknown_labels"] = 1
        return own
    monkeypatch.setattr(comparison.dm, "_dispersion", size)
    result = _build(dk, dr)
    assert not result["comparison_performed"]
    assert any("unknown_comparison_labels" in value for value in result["comparison_not_run_reasons"])


def test_exact_cost_equality_gross_net_and_observed_scope_contrast(sources):
    dk, dr = sources
    projection = comparison.project_dk(dk)
    values = [Fraction(-1, 10000), comparison.COST, Fraction(34, 10000), Fraction(), Fraction(35, 10000)]
    for policy in comparison.POLICIES:
        for index, row in enumerate(projection["variants"][policy]["targets"]):
            if row["in_original_book"]:
                row["label"] = values[index % len(values)]
    # The projection's labels are already checked by project_dk in production;
    # this mathematical contract isolates exact response accounting.
    result = comparison.build_comparison(projection, dr)
    assert result["comparison_performed"]
    for policy, variant in result["variants"].items():
        expected = {arm: [] for arm in comparison.ARMS}
        states = dr["variants"][policy]["target_states"]
        for row, state in zip(projection["variants"][policy]["targets"], states, strict=True):
            if row["in_original_book"] and state["state"] in {"selected", "rejected"}:
                expected["basic"].append(row["label"])
            if state["state"] == "selected":
                expected["selected"].append(row["label"])
        for arm, gross in expected.items():
            summary = variant["by_arm"][arm]
            assert summary["gross_sum"] == sum(gross)
            assert summary["gross_mean"] == sum(gross) / len(gross)
            assert summary["net_sum"] == sum(gross) - len(gross) * comparison.COST
            assert summary["net_mean"] == summary["gross_mean"] - comparison.COST
            assert summary["net_positive_count"] == sum(value > comparison.COST for value in gross)
            assert summary["gross_positive_count"] == sum(value > 0 for value in gross)
        difference = variant["comparison"]["selected_minus_basic"]
        assert difference["gross_mean_difference"] == difference["net_mean_difference"]
        assert difference["gross_mean_difference"] == variant["by_arm"]["selected"]["gross_mean"] - variant["by_arm"]["basic"]["gross_mean"]


def test_all_calendar_weeks_including_zero_have_frequency_only(sources):
    dk, dr = sources
    result = _build(dk, dr)
    for variant in result["variants"].values():
        for arm in variant["by_arm"].values():
            weekly, frequency = arm["by_week"], arm["frequency"]
            assert len(weekly) == len(result["reference_weeks"])
            assert any(value == 0 for value in weekly.values())
            assert all(type(value) is int for value in weekly.values())
            assert sum(weekly.values()) == arm["events"]
            assert frequency["zero_event_weeks"] == sum(value == 0 for value in weekly.values())
            assert frequency["mean_per_all_reference_weeks"] == Fraction(arm["events"], len(weekly))
            assert frequency["retained_opportunity_fraction"] == Fraction(arm["events"], variant["by_arm"]["basic"]["events"])
            assert frequency["original_book_retained_fraction"] == Fraction(arm["events"], len(variant["original_book_coordinates"]))


def test_wire_encoded_ratios_keep_exact_frozen_state_hashes_and_responses(sources):
    dk, dr = sources
    first = _build(dk, dr)
    wire_dk, wire_dr = (json.loads(comparison._canonical(value)) for value in (dk, dr))
    second = _build(wire_dk, wire_dr)
    assert comparison._canonical(first) == comparison._canonical(second)
    assert hashlib.sha256(comparison._canonical(dr) + b"\n").hexdigest() == first["source_identity"]["canonical_DR_sha256"]


def test_joint_uncertainty_uses_shared_nested_scope_and_same_cost_cancels(sources):
    dk, dr = sources
    result = _build(dk, dr)
    for variant in result["variants"].values():
        contrast = variant["comparison"]["selected_minus_basic"]
        uncertainty = contrast["uncertainty"]
        assert uncertainty["same_blocks_for_both_arms"]
        assert uncertainty["overlapping_known_labels"] == variant["by_arm"]["selected"]["known_labels"]
        assert uncertainty["known_labels_by_arm"]["basic"] == variant["by_arm"]["basic"]["known_labels"]
        assert uncertainty["reference_sessions"] == len(result["reference_sessions"])
        assert uncertainty["design_se"] == max(uncertainty["naive_se"], uncertainty["session_cluster_se"], uncertainty["bootstrap_se"])
        assert uncertainty["bootstrap_valid_draws"] + uncertainty["bootstrap_skipped_zero_denominator_draws"] == 2000
        assert not uncertainty["own_arm_planning_certifies_contrast_power"]
        interval = contrast["descriptive_normal95_interval"]
        assert interval["lower"] <= float(contrast["gross_mean_difference"]) <= interval["upper"]


def test_full_invented_geometry_and_compact_source_pressure():
    dk, dr = build_synthetic_fixture(full=True)
    result = _build(dk, dr)
    assert result["comparison_performed"]
    assert len(result["reference_sessions"]) == 257 and len(result["reference_weeks"]) == 56
    for policy, count in (("D+1", 1169), ("D+2", 1167)):
        variant = result["variants"][policy]
        assert variant["population"]["targets"] == 5140
        assert variant["population"]["original_book_events"] == count
        assert len(variant["observed_basic_coordinates"]) + len(variant["excluded_unknown_feature_coordinates"]) == count
        assert variant["population"]["state_counts"]["outside_original_book"] == 12
        for arm in comparison.ARMS:
            own = variant["by_arm"][arm]["own_label_dispersion"]
            assert own["reference_sessions"] == 257 and math.isfinite(own["mde_bps"]) and own["mde_bps"] <= 33
            assert len(variant["by_arm"][arm]["by_week"]) == 56
        assert all("membership_source" not in item and "raw_chunk_metadata" not in item for item in variant["target_states"])
