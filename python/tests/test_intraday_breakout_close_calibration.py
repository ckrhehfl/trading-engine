"""Invented DK wire only; never read market files or run an ancestor study."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
import inspect
import json
import math

import pytest

from research import intraday_breakout_close_calibration as close
from research import intraday_breakout_loss_concentration as dl
from test_intraday_breakout_loss_concentration import build_synthetic_fixture as _dk_fixture
from test_intraday_breakout_loss_concentration import _set_return


def _bar(start, *, open="100", high="101", low="99", close="100"):
    return {"start_at": start.isoformat(), "end_at": (start + timedelta(minutes=5)).isoformat(),
        "available_at": (start + timedelta(minutes=7)).isoformat(),
        "open": open, "high": high, "low": low, "close": close}


def build_synthetic_fixture(*, full=False, metadata_padding=0):
    """Full invented 10280-row geometry; mixed CLV and source missingness."""
    source = _dk_fixture(full=full, metadata_padding=metadata_padding)
    for policy in close.POLICIES:
        rows = source["variants"][policy]["days"]
        originals = [row for row in rows if row["breakout_exit_policy"] is not None]
        by_day = {row["session"]: row for row in originals}
        for index, row in enumerate(originals):
            opening = close._opening(row["session"])
            row["arms"]["basic"]["event"]["context"] = [_bar(opening + offset * timedelta(minutes=5))
                for offset in range(3)]
            start = dl._instant(row["arms"]["basic"]["event"]["signal_bar_start"])
            breakout = _bar(start, open="101", high="104", low="100", close="103")
            if index % 4 == 1:
                breakout["close"] = "102"
            elif index % 4 == 2:
                breakout = None
            elif index % 4 == 3:
                breakout.update(open="104", high="104", low="104", close="104")
            row["arms"]["retest"]["path"]["breakout_bar"] = breakout
        outside = 0
        for row in rows:
            if row["breakout_exit_policy"] is not None or row["arms"]["basic"]["status"] != "known_no_signal":
                continue
            if outside < (12 if full else 1) and row["session"] in by_day:
                original = by_day[row["session"]]
                row["arms"]["basic"] = deepcopy(original["arms"]["basic"])
                if full and outside >= 5:
                    row["arms"]["basic"]["label"].update(status="unknown", exit_open=None, price_change=None,
                        unknown_minute_buckets=1, unknown_reasons=["label_missing_minute_buckets"],
                        unknown_bucket_reason_counts={"absent": 1, "invalid": 0, "conflicting": 0})
                row["arms"]["retest"] = {"status": "unknown", "event": None, "label": None,
                    "path": {"breakout_bar": {"high": "MALFORMED_OUTSIDE_BOOK_NOT_EVALUATED"}}}
                row.update(signal_observable=False, outcome_complete=False,
                    exclusion_reasons=["retest:unknown_signal"]
                        + (["basic:unknown_label"] if row["arms"]["basic"]["label"]["status"] == "unknown" else []))
                outside += 1
        if full:
            candidates = [row for row in rows if row["arms"]["basic"]["status"] == "known_no_signal"]
            unknown_count = 2609 if policy == "D+1" else 2611
            for index, row in enumerate(candidates[:unknown_count]):
                row["arms"]["basic"]["status"] = "unknown"
                reasons = ["basic:unknown_signal"]
                if index < unknown_count - 3:
                    row["arms"]["retest"]["status"] = "unknown"
                    reasons.append("retest:unknown_signal")
                row.update(signal_observable=False, outcome_complete=False, exclusion_reasons=reasons)
    return source


def fixture():
    return build_synthetic_fixture()


def _first(source, policy="D+1"):
    return next(row for row in source["variants"][policy]["days"] if row["breakout_exit_policy"] is not None)


def test_fixed_specification_resources_and_small_import_closure():
    close.validate_spec(json.loads(close.SPECIFICATION_PATH.read_text()))
    assert close.NEXT_HYPOTHESIS == dl.NEXT_HYPOTHESIS
    assert [close.RESOURCES[key] for key in ("cpu_seconds", "address_space_mib", "wall_seconds", "nice")] == [300, 512, 1200, 10]
    assert close.RESOURCES["maximum_report_bytes"] == close.RESOURCES["maximum_source_bytes"] == 67108864
    source = inspect.getsource(close)
    assert "dl.build_diagnostics(" not in source and "dl._variant(" not in source and "dl._event(" not in source
    assert "import sqlite3" not in source and "import intraday_pattern_calibration" not in source


@pytest.mark.parametrize("change", [
    lambda s: s["parameters"].update(bootstrap_seed=1),
    lambda s: s["parameters"]["next_hypothesis"]["threshold"].update(numerator="2"),
    lambda s: s["resources"].update(cpu_seconds=301),
    lambda s: s.update(upstream_sha256="a" * 64),
    lambda s: s.update(extra=True),
])
def test_specification_refuses_before_source(change):
    spec = json.loads(close.SPECIFICATION_PATH.read_text())
    change(spec)
    with pytest.raises(close.BreakoutCloseCalibrationRefusal, match="specification_scope"):
        close.build_calibration({}, specification=spec)


def test_threshold_equality_rejection_missing_flat_and_all_source_states():
    source = fixture()
    result = close.build_calibration(source)
    variant = result["variants"]["D+1"]
    assert variant["population"]["targets"] == 16
    assert variant["population"]["original_book_events"] == 6
    assert variant["population"]["state_counts"] == {
        "selected": 2, "rejected": 2, "unknown": 3, "not_applicable": 8, "outside_original_book": 1}
    assert variant["unknown_reason_counts"] == {"missing_breakout_bar_or_OHLC_or_clock": 1,
        "degenerate_breakout_range": 1, "original_signal_unknown": 1}
    assert variant["source_calibration_receipts"] == source["variants"]["D+1"]["by_arm"]
    assert variant["target_states"][0]["state"] == "selected"  # Exact 3/4.
    assert variant["target_states"][0]["feature_evaluated"]
    assert variant["target_states"][0]["raw_chunk_metadata"]["available_at"] is None
    assert variant["target_states"][0]["feature_available_at"] == _first(source)["arms"]["basic"]["event"]["decision_at"]
    assert any(item["state"] == "outside_original_book" and not item["feature_evaluated"]
        and item["feature_available_at"] is None for item in variant["target_states"])
    assert variant["population"]["basic_status_counts"]["signal"] == 7  # No book expansion.
    assert variant["own_label_dispersion"]["known_labels"] == 2
    assert result["calibration_only"] and result["candidate_measured"]
    assert not result["comparison_performed"] and result["new_performance_families"] == 0
    assert not result["new_labels_calculated"] and not result["upstream_statistics_recalculated"]


@pytest.mark.parametrize("change", [
    lambda row: row["arms"]["retest"]["path"]["breakout_bar"].update(high="102"),
    lambda row: row["arms"]["retest"]["path"]["breakout_bar"].update(open="0"),
    lambda row: row["arms"]["retest"]["path"]["breakout_bar"].update(low="NaN"),
    lambda row: row["arms"]["retest"]["path"]["breakout_bar"].update(start_at="2025-09-04T09:16:00+09:00"),
    lambda row: row["arms"]["retest"]["path"]["breakout_bar"].update(available_at="2025-09-04T09:23:00+09:00"),
    lambda row: row["arms"]["retest"]["path"]["breakout_bar"].update(close="101"),
    lambda row: row["arms"]["basic"]["event"]["context"][1].update(start_at="2025-09-04T09:06:00+09:00"),
    lambda row: row["arms"]["basic"]["event"]["context"][2].update(available_at="2025-09-04T09:25:00+09:00"),
    lambda row: row["arms"]["basic"]["event"]["context"].append(row["arms"]["basic"]["event"]["context"][0]),
    lambda row: row["arms"]["basic"]["label"].update(entry_at="2025-09-04T09:26:00+09:00"),
    lambda row: row["breakout_exit_policy"]["common"].update(anchor_open="77"),
    lambda row: row.update(outcome_complete=False),
])
def test_contradictory_prices_clocks_context_labels_and_masks_refuse(change):
    source = fixture()
    change(_first(source))
    with pytest.raises(close.BreakoutCloseCalibrationRefusal):
        close.build_calibration(source)


@pytest.mark.parametrize("change,reason", [
    (lambda row: row["arms"]["retest"]["path"]["breakout_bar"].pop("open"), "missing_breakout_bar_or_OHLC_or_clock"),
    (lambda row: row["arms"]["basic"]["event"].pop("context"), "missing_initial_context_or_OHLC_or_clock"),
    (lambda row: row["arms"]["basic"]["event"]["context"].pop(), "missing_initial_context_or_OHLC_or_clock"),
])
def test_missing_features_preserve_original_book_and_all_denominators(change, reason):
    source = fixture()
    change(_first(source))
    variant = close.build_calibration(source)["variants"]["D+1"]
    assert variant["population"]["targets"] == 16 and variant["population"]["original_book_events"] == 6
    assert variant["target_states"][0]["state"] == "unknown" and variant["target_states"][0]["reason"] == reason


def test_partial_missing_does_not_hide_present_contradiction_or_future_context():
    source = fixture()
    row = _first(source)
    row["arms"]["retest"]["path"]["breakout_bar"].pop("open")
    row["arms"]["basic"]["event"]["context"][1]["close"] = "200"
    with pytest.raises(close.BreakoutCloseCalibrationRefusal):
        close.build_calibration(source)


def test_missing_context_open_does_not_hide_contradictory_known_range_high():
    source = fixture()
    row = _first(source)
    row["arms"]["basic"]["event"]["context"][0].pop("open")
    row["arms"]["basic"]["event"]["context"][0]["high"] = "105"
    with pytest.raises(close.BreakoutCloseCalibrationRefusal):
        close.build_calibration(source)


def test_feature_unknown_blocks_future_choice_even_when_fresh_sizing_passes():
    source = fixture()
    for policy in close.POLICIES:
        for row in source["variants"][policy]["days"]:
            if row["breakout_exit_policy"] is not None:
                _set_return(row, Fraction(1, 1000))
    variant = close.build_calibration(source)["variants"]["D+1"]
    assert variant["sizing_passed"] and variant["own_label_dispersion"]["mde_bps"] == 0
    assert variant["requires_operator_choice_before_comparison"]
    assert variant["comparison_choice_reasons"] == ["unknown_original_book_features"]


def test_feature_is_independent_of_future_entry_price_and_original_first_failure_never_retries():
    source = fixture()
    before = close.build_calibration(source)
    for policy in close.POLICIES:
        for row in source["variants"][policy]["days"]:
            if row["breakout_exit_policy"] is not None:
                _set_return(row, Fraction(17, 10000), entry_price="9973")
                row["arms"]["retest"]["path"]["first_contact_bar"] = {"close": "999999"}
    after = close.build_calibration(source)
    for policy in close.POLICIES:
        assert after["variants"][policy]["selected_coordinates"] == before["variants"][policy]["selected_coordinates"]
        assert after["variants"][policy]["population"]["state_counts"] == before["variants"][policy]["population"]["state_counts"]


def test_source_immutability_stream_fingerprints_full_calendar_zeros_and_no_mean_output():
    source = fixture()
    before = deepcopy(source)
    result = close.build_calibration(source)
    assert source == before
    assert result["source_identity"]["canonical_source_sha256"] == dl._digest(source)
    variant = result["variants"]["D+1"]
    assert len(variant["by_session"]) == len(source["reference_sessions"])
    assert variant["by_session"][source["reference_sessions"][-1]]["state_counts"]["selected"] == 0
    assert len(variant["by_week"]) == len(source["reference_weeks"])
    assert variant["frequency"]["selected_mean_per_all_reference_weeks"] == Fraction(2, 4)
    assert variant["frequency"]["loose_selected_unknown_outside_book_upper_events"] == 6
    wire = close._canonical(result).decode()
    for forbidden in ('"gross_mean"', '"net_mean"', '"winrate"', '"mean"', '"net"', '"contrast"'):
        assert forbidden not in wire
    source["variants"]["D+1"]["days"][-1]["retained_metadata"] = "synthetic"
    changed = close.build_calibration(source)
    assert changed["source_identity"] != result["source_identity"]
    assert changed["variants"]["D+1"]["selected_coordinates"] == variant["selected_coordinates"]


def test_fresh_selected_sizing_matches_CC_exact_method_on_small_invented_only():
    from research import intraday_pattern_calibration as cc

    source = fixture()
    variant = close.build_calibration(source)["variants"]["D+1"]
    selected = set(map(tuple, variant["selected_coordinates"]))
    records = [{"session": row["session"], "label": dl._ratio(row["arms"]["basic"]["label"]["price_change"])}
        for row in source["variants"]["D+1"]["days"] if (row["session"], row["symbol"]) in selected]
    assert variant["own_label_dispersion"] == cc.dispersion(records, source["reference_sessions"])
    assert variant["requires_operator_choice_before_comparison"]
    assert "own_label_mde_exceeds_assumed_cost_floor" in variant["comparison_choice_reasons"]


def test_single_known_date_has_no_corrected_replication_even_with_many_names():
    own = close._dispersion([{"session": "2025-09-04", "label": Fraction(i, 100)} for i in range(4)],
        ["2025-09-04", "2025-09-11"], lambda: None)
    assert own["event_sd"] is not None and own["mde_bps"] is None
    assert own["planning_constraint"] == "insufficient_known_label_sessions"


def test_nonfinite_statistical_boundary_preserves_counts_with_undefined_MDE():
    own = close._dispersion([{"session": day, "label": Fraction(10 ** 400)}
        for day in ("2025-09-04", "2025-09-11")], ["2025-09-04", "2025-09-11"], lambda: None)
    assert own["events"] == own["known_labels"] == own["known_label_sessions"] == 2
    assert own["mde_bps"] is None and own["planning_constraint"] == "nonfinite_own_label_dispersion"
    assert b"Infinity" not in close._canonical(own)


def test_full_fixture_keeps_original_book_all_5140_targets_and_56_week_geometry():
    source = build_synthetic_fixture(full=True)
    result = close.build_calibration(source)
    assert len(result["reference_sessions"]) == 257 and len(result["reference_weeks"]) == 56
    for policy, expected in (("D+1", 1169), ("D+2", 1167)):
        variant = result["variants"][policy]
        population = variant["population"]
        assert population["targets"] == 5140 and population["original_book_events"] == expected
        assert population["state_counts"]["outside_original_book"] == 12
        assert sum(population["state_counts"].values()) == 5140
        assert population["basic_status_counts"]["signal"] == expected + 12
        assert population["basic_label_status_counts"]["unknown"] == 7
        assert population["basic_label_status_counts"]["known"] == expected + 5
        assert variant["frequency"]["reference_weeks"] == 56
        assert variant["own_label_dispersion"]["reference_sessions"] == 257
        assert math.isfinite(variant["own_label_dispersion"]["mde_bps"])
