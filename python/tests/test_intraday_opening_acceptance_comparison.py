"""Invented completed CT/DU reports only; no market, archive or detector IO."""
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import hashlib
import inspect
import json
import math

import pytest

from research import intraday_opening_acceptance_comparison as comparison


ct, du, cc = comparison.ct, comparison.du, comparison.cc
SMALL_DAYS = ("2025-09-04", "2025-09-05", "2025-09-08", "2025-09-09", "2025-09-10", "2025-09-11", "2025-09-19")
SYMBOLS = [f"KRX:{number:06d}" for number in range(1, 21)]


def _bar(day, offset, *, close="101", low="100", high="102"):
    start = cc.session_open(day) + offset * cc.MINUTE
    return {"start_at": start, "end_at": start + cc.FIVE,
        "available_at": start + cc.FIVE + 2 * cc.MINUTE,
        "open": "100", "high": high, "low": low, "close": close}


def _label(day, decision, index, *, unknown=False, selected=False):
    entry, cap = decision + 3 * cc.MINUTE, decision + 33 * cc.MINUTE
    # Prices remain distinct exact rationals. Vary calendar and member signs.
    numerator = ((index * 73 + (19 if selected else 0)) % 251) - 125
    price = 1000000 + index * 137
    value = Fraction(numerator, 100000)
    scaled_exit = price * (100000 + numerator)
    return {"status": "unknown" if unknown else "known", "entry_at": entry, "exit_at": cap,
        "time_cap_decided_at": decision, "entry_open": str(price),
        "exit_open": f"{scaled_exit // 100000}.{scaled_exit % 100000:05d}", "price_change": None if unknown else value,
        "required_minute_buckets": 31, "unknown_minute_buckets": int(unknown),
        "unknown_bucket_reason_counts": {"missing": int(unknown), "conflict": 0, "invalid_price": 0},
        "unknown_reasons": ["label_missing_minute_buckets"] if unknown else [],
        "entry_price_available_at": entry + 3 * cc.MINUTE, "label_available_at": cap + 3 * cc.MINUTE,
        "evidence_level": "assumed", "actual_fill_certified": False}


def _findings(day, index, state, vintage):
    opening = cc.session_open(day)
    context = [_bar(day, offset, close="100", high="100") for offset in (0, 5, 10)]
    b = _bar(day, 230 if state == "late" else 15)
    w = _bar(day, 20, close="99", low="99") if state == "rejected" else _bar(day, 20)
    basic_status = "unknown" if state == "basic_unknown" else "known_no_signal" if state == "no_signal" else "signal"
    selected_status = ("unknown" if state in {"basic_unknown", "feature_unknown"} else
        "known_no_signal" if state in {"no_signal", "rejected", "late"} else "signal")
    metadata = {"observation_date": day, "retrieved_at": "2026-10-20T01:00:00+00:00",
        "source": "collector klines; assumed completed five-minute bars", "is_final": None,
        "data_vintage": vintage, "evidence_level": "assumed", "range_minutes": 15}
    basic_event = {"family": "opening_range_breakout", "signal_bar_start": b["start_at"],
        "signal_bar_end": b["end_at"], "decision_at": b["available_at"], "available_at": b["available_at"],
        "context": deepcopy(context), "drop_bar": None, "trough_bar": None,
        **metadata, "parameters_sha256": hashlib.sha256(comparison._canonical(ct.PARAMETERS)).hexdigest()}
    selected_event = {"family": "opening_range15_two_close_acceptance", "signal_bar_start": w["start_at"],
        "signal_bar_end": w["end_at"], "decision_at": w["available_at"], "available_at": w["available_at"],
        "context": deepcopy(context), "range_high": "100", "breakout_bar": deepcopy(b),
        "confirmation_bar": deepcopy(w), **metadata,
        "parameters_sha256": hashlib.sha256(comparison._canonical(du.PARAMETERS)).hexdigest()}
    unknown_gaps = {"missing": 235 if state == "basic_unknown" else 5 if state == "feature_unknown" else 0,
        "conflict": 0, "invalid_price": 0}
    basic_unknowns = ["no_saved_rows_unknown_cause"] if state == "basic_unknown" else []
    selected_unknowns = basic_unknowns or (["decision_prefix_missing_minute_buckets"] if state == "feature_unknown" else [])
    basic = {"status": basic_status, "event": basic_event if basic_status == "signal" else None,
        "label": _label(day, b["available_at"], index, unknown=state == "base_unknown") if basic_status == "signal" else None,
        "required_bars": 47, "valid_prefix_bars": 0 if state == "basic_unknown" else 47 if state == "no_signal" else
            47 if state == "late" else 4 if state == "feature_unknown" else 5,
        "unknown_reasons": basic_unknowns,
        "decision_prefix_unknown_bucket_counts": deepcopy(unknown_gaps)}
    reason = ("no_breakout_before_cutoff" if state == "no_signal" else
        "confirmation_after_cutoff" if state == "late" else
        "next_scheduled_close_at_or_below_range_high" if state == "rejected" else None)
    selected = {"status": selected_status, "event": selected_event if selected_status == "signal" else None,
        "label": _label(day, w["available_at"], index, unknown=state == "selected_unknown", selected=True)
            if selected_status == "signal" else None,
        "required_bars": 47, "valid_prefix_bars": basic["valid_prefix_bars"],
        "unknown_reasons": selected_unknowns,
        "decision_prefix_unknown_bucket_counts": deepcopy(unknown_gaps), "known_no_signal_reason": reason,
        "path": {"range_high": None if state == "basic_unknown" else "100",
            "breakout_bar": None if state in {"basic_unknown", "no_signal"} else deepcopy(b),
            "confirmation_bar": deepcopy(w) if state in {"accepted", "base_unknown", "selected_unknown", "rejected"} else None,
            "invalidation_bar": deepcopy(w) if state == "rejected" else None,
            "invalidation_reason": reason if state == "rejected" else None}}
    return basic, selected


def _saved_summary(rows, arm):
    statuses = Counter(row["arms"][arm]["status"] for row in rows)
    labels = Counter(row["arms"][arm]["label"]["status"] for row in rows if row["arms"][arm]["label"])
    # Old numerical sizing is deliberately opaque, and cannot open DX's gate.
    return {"target_symbol_sessions": len(rows), "signal_counts": {state: statuses[state] for state in ct.STATES},
        "label_counts": {state: labels[state] for state in ("known", "unknown")},
        "own_label_dispersion": {"mde_bps": 0.000001, "invented_opaque_receipt": True},
        "comparison_choice_reasons": ["unknown_own_labels"] if labels["unknown"] else [],
        "requires_operator_choice_before_comparison": bool(labels["unknown"]),
        "source_note": "invented prior calibration receipt; never used for current planning"}


def _unused30(day, index, state, basic):
    """Representative opaque CT arm30 structure, never an input to DX math."""
    finding = deepcopy(basic)
    if state == "feature_unknown":
        finding.update(status="unknown", event=None, label=None,
            unknown_reasons=["decision_prefix_missing_minute_buckets"])
    elif finding["event"] is not None:
        bar = _bar(day, 230 if state == "late" else 30)
        finding["event"].update(signal_bar_start=bar["start_at"], signal_bar_end=bar["end_at"],
            decision_at=bar["available_at"], available_at=bar["available_at"], range_minutes=30,
            context=[_bar(day, offset, close="100", high="100") for offset in range(0, 30, 5)])
        finding["label"] = _label(day, bar["available_at"], index,
            unknown=state in {"base_unknown", "selected_unknown"})
        finding["valid_prefix_bars"] = max(finding["valid_prefix_bars"],
            (bar["end_at"] - cc.session_open(day)) // cc.FIVE)
    return finding


def build_synthetic_fixture(*, full=False, metadata_padding=0, provenance_density=0):
    """Hand-built invented sources; factory work stays outside measured child.

    Full geometry is 257 sessions,56 weeks,5140 targets per policy. Padding and
    provenance density allow the caller to exceed both actual saved byte sizes.
    No calibration, detector, label maker or actual data source is called.
    """
    if full:
        start, end = date(2025, 9, 4), date(2026, 9, 23)
        candidates = [start + timedelta(days=index) for index in range((end - start).days + 1)
            if (start + timedelta(days=index)).weekday() < 5]
        removals = set(candidates[10::5][:len(candidates) - 257])
        days = [day.isoformat() for day in candidates if day not in removals]
    else:
        days = list(SMALL_DAYS)
    reports = []
    for role, model in (("CT", ct), ("DU", du)):
        reports.append({"study_id": model.SPECIFICATION_ID, "mode": model.MODE, "window": dict(model.WINDOW),
            "parameters": deepcopy(model.PARAMETERS), "assumptions": deepcopy(model.ASSUMPTIONS), **model.FLAGS,
            "specification_sha256": comparison.UPSTREAM_SPECIFICATION_SHA256[role],
            "reference_sessions": days, "reference_weeks": sorted({ct._week(day) for day in days}),
            "upstream_calendar": {"rows": [{"date": day, "fetched_at": None} for day in days], "certified": False},
            "source_target_matrix": {}, "variants": {},
            "input_evidence": {"upstream_report_sha256": ct.UPSTREAM_SHA256,
                "upstream_report_path": ct.REGISTERED_PATHS["upstream_path"],
                "archive_path": ct.REGISTERED_PATHS["input_archive_path"],
                "compressed_sha256": ct.INPUT_COMPRESSED_SHA256, "stream_sha256": ct.INPUT_STREAM_SHA256,
                "unique_target_days": len(days) * 20, "queried_rows": len(days) * 20 * 390,
                "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}})
    for policy in comparison.POLICIES:
        matrix, ct_rows, du_rows = [], [], []
        for day_index, day in enumerate(days):
            membership = {"session": day, "observation_date": "2025-08-29",
                "selected_at": day + "T08:30:00+09:00", "available_at": "2025-09-01T08:30:00+09:00",
                "retrieved_at": {"fixture": "2026-10-20T00:00:00+00:00"}, "source": "invented monthly source",
                "is_final": None, "data_vintage": {"fixture": "b" * 64}, "evidence_level": "assumed",
                "targets": [{"symbol": symbol, "rank": rank, "stored_row_count": None,
                    "state": "no_stored_rows_unknown_reason"} for rank, symbol in enumerate(SYMBOLS, 1)]}
            matrix.append(membership)
            for rank, symbol in enumerate(SYMBOLS):
                index = day_index * 20 + rank
                # The final week has genuinely zero invented opportunities.
                state = ("basic_unknown" if ct._week(day) == ct._week(days[-1]) or rank < 9 else
                    ("no_signal", "rejected", "feature_unknown", "late", "selected_unknown", "base_unknown",
                     "accepted", "accepted", "accepted", "accepted", "accepted")[rank - 9])
                vintage = hashlib.sha256((day + symbol + "invented").encode()).hexdigest()
                basic, selected = _findings(day, index, state, vintage)
                rows = 0 if state == "basic_unknown" else 390
                physical = {"session": day, "symbol": symbol, "data_vintage": vintage,
                    "raw_chunk_metadata": {"observation_date": day, "available_at": None,
                        "retrieved_at": "2026-10-20T01:00:00+00:00", "source": "collector klines",
                        "is_final": None, "evidence_level": "assumed"},
                    "input_rows": rows, "source_offset_counts_ms": {"0": rows} if rows else {},
                    "source_fetched_at": ["2026-10-20T00:00:00+00:00"] if rows else [], "gaps": [],
                    "membership_target": deepcopy(membership["targets"][rank]),
                    "membership_source": {key: deepcopy(value) for key, value in membership.items() if key != "targets"},
                    "invented_provenance": [{"origin": f"invented-{day}-{symbol}-{index}-{item}",
                        "observation_date": day, "available_at": None,
                        "retrieved_at": "2026-10-20T01:00:00+00:00", "source": "invented causal source archive",
                        "data_vintage": hashlib.sha256(f"{day}:{symbol}:{item}".encode()).hexdigest(),
                        "is_final": None, "evidence_level": "assumed",
                        "minute_offset": item % 390, "note": f"causal metadata atom {index}:{item}"}
                        for item in range(provenance_density + (metadata_padding + 299) // 300)]}
                ct_row, du_row = deepcopy(physical), deepcopy(physical)
                ct_row.update(arms={"15": basic, "30": _unused30(day, index, state, basic)},
                    event_timing_state="opaque_original_CT30_timing")
                du_row["arms"] = {"15_acceptance": selected}
                ct_rows.append(ct_row)
                du_rows.append(du_row)
        for report, rows, arm in ((reports[0], ct_rows, "15"), (reports[1], du_rows, "15_acceptance")):
            report["source_target_matrix"][policy] = {"days": deepcopy(matrix), "summary": {"invented": True}}
            report["variants"][policy] = {"days": rows, "by_arm": {arm: _saved_summary(rows, arm)}}
            report["requires_operator_choice_before_comparison"] = True
            report["input_evidence"]["queried_rows"] = sum(row["input_rows"] for row in rows)
    return tuple(reports)


def _build(sources):
    return comparison.build_comparison(comparison.project_ct(sources[0]), comparison.project_du(sources[1]))


@pytest.fixture
def sources():
    return build_synthetic_fixture()


def test_registered_source_only_scope_and_narrow_math_reuse():
    comparison.validate_spec(json.loads(comparison.SPECIFICATION_PATH.read_text()))
    assert comparison.COST == Fraction(33, 10000)
    assert comparison._performance is comparison.dn._performance
    assert comparison._uncertainty is comparison.dn._uncertainty
    text = inspect.getsource(comparison)
    for forbidden in ("build_calibration(", "detect_opening", "sqlite3", "read_bytes(", "gzip.open(", "dn.build_comparison("):
        assert forbidden not in text
    assert comparison.RESOURCES["cpu_seconds"] == 300
    assert comparison.RESOURCES["address_space_mib"] == 512
    assert comparison.RESOURCES["wall_seconds"] == 1200
    assert comparison.RESOURCES["automatic_retries"] == 0


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"].update(bootstrap_draws=1999),
    lambda spec: spec["parameters"]["assumed_cost"].update(numerator="34"),
    lambda spec: spec["upstream_sha256"].update(CT="0" * 64),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(extra=True),
])
def test_changed_spec_refused_before_sources(change):
    spec = json.loads(comparison.SPECIFICATION_PATH.read_text())
    change(spec)
    with pytest.raises(comparison.OpeningAcceptanceComparisonRefusal, match="specification_scope"):
        comparison.build_comparison({}, {}, specification=spec)


def test_common_scope_intersection_preserves_unknowns_and_five_minute_policy_effect(sources):
    before = deepcopy(sources)
    report = _build(sources)
    assert sources == before
    assert report["comparison_performed"] and report["new_performance_families"] == 1
    for variant in report["variants"].values():
        flow = variant["cohort_flow"]
        assert flow["targets"] == 140
        assert sum(flow["state_counts"].values()) == 140
        assert flow["DU_label_counts"]["unknown"] == 6
        assert flow["accepted_known_lost_to_base_label_unknown"] == 6
        assert flow["common_selected_events"] < flow["DU_label_counts"]["known"]
        assert flow["common_basic_events"] == flow["common_selected_events"] + flow["state_counts"]["rejected"]
        assert variant["by_arm"]["basic"]["events"] == flow["common_basic_events"]
        assert variant["by_arm"]["selected"]["events"] == flow["common_selected_events"]
        assert variant["by_arm"]["selected"]["frequency"]["zero_event_weeks"] == 1
        assert set(variant["by_arm"]["selected"]["by_week"]) == set(report["reference_weeks"])
        assert all(not row["in_common_scope"] for row in variant["target_states"] if row["exclusion_reasons"])
        assert variant["comparison"]["selected_minus_basic"]["fixed_cost_cancels_in_mean_difference"] is True
    assert report["new_labels_calculated"] is False and report["upstream_statistics_recalculated"] is False


def test_four_own_plans_precede_any_performance(monkeypatch, sources):
    original_plan, original_performance = comparison.dm._dispersion, comparison._performance
    observed = []
    def plan(*args):
        observed.append("plan")
        return original_plan(*args)
    def performance(*args):
        assert observed.count("plan") == 4
        observed.append("performance")
        return original_performance(*args)
    monkeypatch.setattr(comparison.dm, "_dispersion", plan)
    monkeypatch.setattr(comparison, "_performance", performance)
    assert _build(sources)["comparison_performed"]
    assert observed == ["plan"] * 4 + ["performance"] * 4


@pytest.mark.parametrize("mde", [None, math.inf, math.nan, 33.000001])
def test_failed_fresh_sizing_retains_calibration_without_any_means(monkeypatch, sources, mde):
    original = comparison.dm._dispersion
    def plan(*args):
        result = original(*args)
        result["mde_bps"] = mde
        return result
    monkeypatch.setattr(comparison.dm, "_dispersion", plan)
    monkeypatch.setattr(comparison, "_performance", lambda *_: pytest.fail("mean calculated before gates"))
    report = _build(sources)
    assert report["new_performance_families"] == 0 and not report["comparison_performed"]
    assert report["calibration_only"] is True
    assert len(report["comparison_not_run_reasons"]) == 4
    assert all(variant["comparison"] is None for variant in report["variants"].values())
    assert all("gross_mean" not in arm for variant in report["variants"].values() for arm in variant["by_arm"].values())


def test_exact33bp_boundary_passes_and_cost_exceedance_is_strict(monkeypatch, sources):
    original = comparison.dm._dispersion
    def plan(*args):
        result = original(*args)
        result["mde_bps"] = 33.0
        return result
    monkeypatch.setattr(comparison.dm, "_dispersion", plan)
    assert _build(sources)["comparison_performed"]
    stats = comparison._performance([{"label": comparison.COST}, {"label": comparison.COST + Fraction(1, 1000000)}])
    assert stats["endpoint_gross_strictly_above_cost_count"] == 1
    assert stats["net_sum"] == Fraction(1, 1000000)


@pytest.mark.parametrize("mutation", ["membership", "vintage", "first_B", "W", "label", "spec", "counts"])
def test_changed_original_linkage_or_label_refuses(sources, mutation):
    ct_report, du_report = deepcopy(sources)
    row = next(row for row in du_report["variants"]["D+1"]["days"] if row["arms"]["15_acceptance"]["status"] == "signal")
    finding = row["arms"]["15_acceptance"]
    if mutation == "membership":
        row["membership_source"]["source"] = "changed"
    elif mutation == "vintage":
        row["data_vintage"] = "a" * 64
    elif mutation == "first_B":
        finding["path"]["breakout_bar"]["close"] = "100"
    elif mutation == "W":
        finding["path"]["confirmation_bar"]["start_at"] += cc.FIVE
    elif mutation == "label":
        finding["label"]["price_change"] = Fraction(999)
    elif mutation == "spec":
        du_report["specification_sha256"] = "a" * 64
    else:
        du_report["variants"]["D+1"]["by_arm"]["15_acceptance"]["label_counts"]["known"] += 1
    with pytest.raises(comparison.OpeningAcceptanceComparisonRefusal):
        _build((ct_report, du_report))


def test_unused_CT30_remains_opaque_and_prior_sizing_cannot_open_gate(sources):
    report = _build(sources)
    changed = deepcopy(sources)
    for row in changed[0]["variants"]["D+1"]["days"]:
        row["arms"]["30"] = {"arbitrary_opaque_provenance": "not a performance input"}
    other = _build(changed)
    assert other["variants"]["D+1"]["by_arm"] == report["variants"]["D+1"]["by_arm"]
    assert other["variants"]["D+1"]["comparison"] == report["variants"]["D+1"]["comparison"]


def test_joint_uncertainty_uses_shared_calendar_remainder_and_retains_zero_draw_counts(sources):
    report = _build(sources)
    for variant in report["variants"].values():
        uncertainty = variant["comparison"]["selected_minus_basic"]["uncertainty"]
        assert uncertainty["reference_sessions"] == 7
        assert uncertainty["same_blocks_for_both_arms"]
        assert uncertainty["bootstrap_draws"] == 2000 and uncertainty["bootstrap_seed"] == 0
        assert uncertainty["bootstrap_valid_draws"] + uncertainty["bootstrap_skipped_zero_denominator_draws"] == 2000
        assert uncertainty["design_se"] == max(uncertainty[key] for key in ("naive_se", "session_cluster_se", "bootstrap_se"))
        assert uncertainty["own_arm_planning_certifies_contrast_power"] is False


def test_checkpoints_and_compact_report_do_not_copy_source_graphs(sources):
    checkpoints = []
    ct_projection = comparison.project_ct(sources[0], lambda: checkpoints.append(1))
    du_projection = comparison.project_du(sources[1], lambda: checkpoints.append(1))
    report = comparison.build_comparison(ct_projection, du_projection, checkpoint=lambda: checkpoints.append(1))
    assert len(checkpoints) > 600
    assert "source_target_matrix" not in report
    for variant in report["variants"].values():
        assert all("event" not in row and "label" not in row and "membership_source" not in row
            for row in variant["target_states"])


def test_wire_roundtrip_preserves_exact_labels_and_all_results(sources):
    wire = tuple(json.loads(comparison._canonical(report)) for report in sources)
    assert comparison._canonical(_build(wire)) == comparison._canonical(_build(sources))


def test_actual_own_label_failure_is_not_rescued_by_old_receipt(sources):
    altered = deepcopy(sources)
    for report in altered:
        for variant in report["variants"].values():
            for index, row in enumerate(variant["days"]):
                for finding in row["arms"].values():
                    label = finding.get("label")
                    if label is not None and label["status"] == "known":
                        value = Fraction(2 if index % 2 else -1, 2)
                        label.update(entry_open="100", exit_open=str(100 * (1 + value)), price_change=value)
    result = _build(altered)
    assert result["calibration_only"] and not result["comparison_performed"]
    assert result["underpower_exception_applied"] is False
    assert any(arm["own_label_dispersion"]["mde_bps"] > 33 for variant in result["variants"].values()
        for arm in variant["by_arm"].values())


def test_full_factory_has_exact_calendar_geometry_diverse_sources_and_opaque_CT30():
    originals = build_synthetic_fixture(full=True, metadata_padding=1, provenance_density=1)
    for report in originals:
        assert len(report["reference_sessions"]) == 257 and len(report["reference_weeks"]) == 56
        assert all(len(variant["days"]) == 5140 for variant in report["variants"].values())
        rows = report["variants"]["D+1"]["days"]
        assert len({row["data_vintage"] for row in rows}) == 5140
        assert rows[0]["invented_provenance"][0] != rows[1]["invented_provenance"][0]
    unused = next(row["arms"]["30"] for row in originals[0]["variants"]["D+1"]["days"]
        if row["arms"]["30"]["event"] is not None)
    assert len(unused["event"]["context"]) == 6 and unused["label"]["required_minute_buckets"] == 31
