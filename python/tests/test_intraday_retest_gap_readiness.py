"""Invented completed-source metadata and timestamp snapshots, no private IO."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json

import pytest

from research import intraday_retest_gap_readiness as readiness


DAY = "2025-09-04"
SNAPSHOT = "2026-10-23T01:00:00+00:00"
RETRIEVED = "2026-10-23T01:00:01+00:00"
SYMBOLS = [f"KRX:{number:06d}" for number in range(1, 21)]


def clock(day, offset):
    return (readiness._opening(day) + offset * timedelta(minutes=1)).isoformat()


def gap(day, start, end, state="missing"):
    return {"start": clock(day, start), "end": clock(day, end), "state": state}


def source(days=(DAY,), changes=None, d2_last_symbol=None):
    """Twenty target rows/session; no signal production or copied raw bars."""
    changes = changes or {}
    report = {"study_id": readiness.SOURCE_ID, "mode": readiness.SOURCE_MODE,
        "window": dict(readiness.WINDOW), **dict.fromkeys(readiness.SOURCE_FALSE_FLAGS, False),
        "exploratory_only": True, "calibration_only": True,
        "reference_sessions": list(days),
        "reference_weeks": sorted({f"{date.fromisoformat(day).isocalendar().year}-W{date.fromisoformat(day).isocalendar().week:02}" for day in days}),
        "source_target_matrix": {}, "variants": {}, "assumptions": {"source": "invented unverified vintage"},
        "requires_operator_choice_before_comparison": True,
        "input_evidence": {**readiness.SOURCE_EVIDENCE, "unique_target_days": len(days) * (21 if d2_last_symbol else 20), "queried_rows": 0}}
    for policy in readiness.POLICIES:
        memberships, records = [], []
        symbols = SYMBOLS[:-1] + [d2_last_symbol] if policy == "D+2" and d2_last_symbol else SYMBOLS
        for day in days:
            membership = {"session": day, "available_at": "2025-09-01T08:30:00+09:00",
                "selected_at": day + "T08:30:00+09:00", "observation_date": "2025-08-29",
                "retrieved_at": {"fixture": "2026-10-20T00:00:00+00:00"}, "source": "invented monthly source",
                "is_final": None, "data_vintage": {"fixture": "b" * 64}, "evidence_level": "assumed",
                "targets": [{"symbol": symbol, "rank": index, "state": "no_stored_rows_unknown_reason", "stored_row_count": None}
                            for index, symbol in enumerate(symbols, 1)]}
            memberships.append(membership)
            for target in membership["targets"]:
                change = changes.get((day, target["symbol"]), {})
                status, label_status = change.get("status", "known_no_signal"), change.get("label_status", "known")
                gaps = change.get("gaps", [])
                counts = {state: sum(min(end, 235) - start for start, end, gap_state in change.get("counts", [])
                                    if gap_state == state and start < 235) for state in readiness.GAP_STATES}
                label_counts = change.get("label_counts", {state: 0 for state in readiness.GAP_STATES})
                arm = {"status": status, "label": None, "event": {"must_not_inspect_prices": object()},
                    "path": {"must_not_inspect_prices": object()},
                    "unknown_reasons": change.get("unknown_reasons", ["decision_prefix_missing_minute_buckets"] if status == "unknown" else []),
                    "known_no_signal_reason": "no_breakout_before_cutoff" if status == "known_no_signal" else None,
                    "decision_prefix_unknown_bucket_counts": counts}
                if status == "signal":
                    arm["label"] = {"status": label_status, "entry_at": clock(day, 35), "exit_at": clock(day, 65),
                        "unknown_reasons": ["label_missing_minute_buckets"] if label_status == "unknown" else [],
                        "unknown_bucket_reason_counts": label_counts, "price_change": object()}
                records.append({"session": day, "symbol": target["symbol"], "arms": {"15_retest": arm},
                    "gaps": deepcopy(gaps), "input_rows": change.get("input_rows", 390), "data_vintage": "a" * 64,
                    "raw_chunk_metadata": {"observation_date": day, "available_at": None,
                        "retrieved_at": "2026-10-20T00:00:00+00:00", "source": "collector klines", "is_final": None, "evidence_level": "assumed"},
                    "source_fetched_at": ["2026-10-19T00:00:00+00:00"], "membership_target": deepcopy(target),
                    "membership_source": {key: deepcopy(value) for key, value in membership.items() if key != "targets"}})
        report["source_target_matrix"][policy] = {"days": memberships, "summary": {"original_unknowns_preserved": True}}
        report["variants"][policy] = {"days": records, "by_arm": {"15_retest": {
            "requires_operator_choice_before_comparison": True,
            "comparison_choice_reasons": ["unknown_own_labels", "own_label_mde_exceeds_assumed_cost_floor"]}}}
    return report


def unknown(day=DAY, symbol=SYMBOLS[0], gaps=None):
    session_minutes = int((readiness._closing(day) - readiness._opening(day)) // timedelta(minutes=1))
    gaps = [(0, session_minutes, "missing")] if gaps is None else gaps
    empty = gaps == [(0, session_minutes, "missing")]
    return {(day, symbol): {"status": "unknown", "input_rows": 0 if empty else 389,
        "gaps": [gap(day, start, end, state) for start, end, state in gaps], "counts": gaps,
        "unknown_reasons": ["no_saved_rows_unknown_cause"] if empty else ["decision_prefix_missing_minute_buckets"]}}


def chunk(day=DAY, symbol=SYMBOLS[0], offsets=(), extra_rows=()):
    return {"session": day, "symbol": symbol, "retrieved_at": RETRIEVED,
        "rows": [{"open_time_ms": int(readiness._opening(day).timestamp()) * 1000 + offset,
                  "fetched_at": "2026-10-19T00:00:00+00:00"} for offset in offsets] + list(extra_rows)}


def audit(report, chunks=()):
    return readiness.build_audit(report, chunks, snapshot_at=SNAPSHOT)


def test_fixed_specification_and_resource_constants():
    readiness.validate_spec(json.loads(readiness.SPECIFICATION_PATH.read_text()))
    assert readiness.RESOURCES["maximum_source_bytes"] == 64 * 1024 * 1024
    assert readiness.RESOURCES["maximum_queries"] == 6000
    assert readiness.RESOURCES["maximum_rows"] == 3_600_000
    assert readiness.RESOURCES["cpu_seconds"] == 300
    assert readiness.RESOURCES["address_space_mib"] == 512
    assert readiness.RESOURCES["wall_seconds"] == 1200


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update(source_report_sha256="b" * 64),
    lambda spec: spec.update(source_specification_sha256="c" * 64),
    lambda spec: spec.update(source_database="/tmp/other.sqlite3"),
    lambda spec: spec["resources"].update(automatic_retries=1),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec["resources"].update(maximum_source_bytes=33_554_432),
    lambda spec: spec["parameters"].update(new_signals_calculated=True),
    lambda spec: spec.update(extra=True),
])
def test_spec_mutations_refuse(mutate):
    spec = json.loads(readiness.SPECIFICATION_PATH.read_text())
    mutate(spec)
    with pytest.raises(readiness.ReadinessRefusal, match="specification_scope_mismatch"):
        readiness.validate_spec(spec)


def test_unknown_prefix_uses_exactly235_minutes_and_unions_policies():
    report = source(changes=unknown(gaps=[(230, 240, "missing")]))
    plan = readiness.derive_plan(report, as_of="2026-10-23")
    first = plan["coordinates"][0]
    assert first["required_regions_by_policy"]["D+1"] == {"group": "unknown_signal_prefix", "start": clock(DAY, 0), "end": clock(DAY, 235)}
    assert first["required_gap_intervals"] == [gap(DAY, 230, 235)]
    assert first["source_session_gaps"] == [gap(DAY, 230, 240)]
    result = audit(report, [chunk(offsets=[230 * 60000, 234 * 60000, 235 * 60000])])
    assert result["summary"]["union"]["required_gap_counts"]["missing"] == 5
    assert result["summary"]["union"]["candidate_missing_timestamp_buckets"] == 2
    assert result["summary"]["by_variant"]["D+1"]["candidate_missing_timestamp_buckets"] == 2
    assert result["summary"]["by_variant"]["D+2"]["candidate_missing_timestamp_buckets"] == 2
    assert result["summary"]["union"]["remaining_missing_timestamp_buckets"] == 3


def test_no_rows_cause_stays_unknown_and_never_invents_signal_or_price():
    result = audit(source(changes=unknown()), [chunk()])
    union = result["summary"]["union"]
    assert union["source_absence_unknown_cause_coordinates"] == 1
    assert union["source_unknown_reason_counts"] == {"no_saved_rows_unknown_cause": 1}
    assert union["required_gap_counts"]["missing"] == 235
    assert union["remaining_missing_timestamp_buckets"] == 235
    assert result["coordinates"][0]["absence_cause"] == "unknown"
    assert result["claims"]["new_signals_calculated"] is False
    assert result["claims"]["performance_calculated"] is False


def test_known_states_need_no_reads_even_with_later_session_gaps():
    changes = {(DAY, SYMBOLS[0]): {"status": "signal", "gaps": [gap(DAY, 200, 201)]},
        (DAY, SYMBOLS[1]): {"gaps": [gap(DAY, 50, 51)]}}
    result = audit(source(changes=changes))
    assert all(not row["requires_read"] for row in result["plan"]["coordinates"])
    assert result["summary"]["union"]["query_coordinates"] == 0
    with pytest.raises(readiness.ReadinessRefusal, match="extra_current_chunk"):
        audit(source(changes=changes), [chunk()])


def test_two_label_gaps_include_entry_and_exit_and_exclude_next_minute():
    changes = {(DAY, SYMBOLS[0]): {"status": "signal", "label_status": "unknown",
        "gaps": [gap(DAY, 34, 36), gap(DAY, 65, 67)],
        "label_counts": {"missing": 2, "conflict": 0, "invalid_price": 0}}}
    result = audit(source(changes=changes), [chunk(offsets=[35 * 60000, 66 * 60000])])
    first = result["plan"]["coordinates"][0]
    assert first["required_gap_intervals"] == [gap(DAY, 35, 36), gap(DAY, 65, 66)]
    assert first["required_regions_by_policy"]["D+1"]["end"] == clock(DAY, 66)
    group = result["summary"]["union"]["by_group"]["known_signal_unknown_label"]
    assert group["coordinates"] == 1
    assert group["required_gap_counts"]["missing"] == 2
    assert group["candidate_missing_timestamp_buckets"] == 1
    assert group["remaining_missing_timestamp_buckets"] == 1
    assert result["summary"]["union"]["source_label_unknown_reason_counts"] == {"label_missing_minute_buckets": 1}


def test_two_known_signals_with_unknown_labels_remain_two_separate_targets():
    changes = {(DAY, symbol): {"status": "signal", "label_status": "unknown", "gaps": [gap(DAY, 50, 51)],
        "label_counts": {"missing": 1, "conflict": 0, "invalid_price": 0}} for symbol in SYMBOLS[:2]}
    result = audit(source(changes=changes), [chunk(symbol=symbol) for symbol in SYMBOLS[:2]])
    group = result["summary"]["union"]["by_group"]["known_signal_unknown_label"]
    assert group["coordinates"] == 2
    assert group["remaining_missing_timestamp_buckets"] == 2
    assert result["summary"]["union"]["source_label_status_counts"]["unknown"] == 2


@pytest.mark.parametrize("day", ["2025-11-13", "2026-01-02"])
def test_assumed_special_opening_is_preserved_in_prefix_and_timestamp_query(day):
    result = audit(source(days=(day,), changes=unknown(day=day)), [chunk(day=day, offsets=[0])])
    region = result["plan"]["coordinates"][0]["required_regions_by_policy"]["D+1"]
    assert datetime.fromisoformat(region["start"]).hour == 10
    assert region["end"] == clock(day, 235)
    assert result["summary"]["union"]["candidate_missing_timestamp_buckets"] == 1


def test_conflict_invalid_price_require_ohlc_and_request_no_timestamp_reads():
    report = source(changes=unknown(gaps=[(10, 11, "conflict"), (20, 22, "invalid_price")]))
    result = audit(report)
    assert result["summary"]["union"]["query_coordinates"] == 0
    assert result["summary"]["union"]["pending_conflict_minute_buckets"] == 1
    assert result["summary"]["union"]["pending_invalid_price_minute_buckets"] == 2
    assert result["coordinates"][0]["current_timestamp_inventory"] is None


def test_timestamp_candidates_include_conflicts_but_never_certify_valid_price():
    report = source(changes=unknown(gaps=[(3, 5, "missing")]))
    result = audit(report, [chunk(offsets=[180001, 180999, 240000],
        extra_rows=[{"open_time_ms": None, "fetched_at": None}, {"open_time_ms": "not-a-time", "fetched_at": None}])])
    first = result["coordinates"][0]
    inv = first["current_timestamp_inventory"]
    assert inv["valid_timestamp_rows"] == 3
    assert inv["invalid_timestamp_rows"] == 2
    assert inv["unique_minute_buckets"] == 2
    assert inv["duplicate_minute_buckets"] == 1
    assert inv["duplicate_timestamp_rows"] == 1
    assert inv["source_offset_counts_ms"] == {"0": 1, "1": 1, "999": 1}
    assert first["valid_ohlc_certified"] is False
    assert first["signal_repaired"] is False
    assert result["summary"]["union"]["candidate_missing_timestamp_buckets"] == 2


def test_invalid_scalar_positions_do_not_affect_integer_order():
    report = source(changes=unknown(gaps=[(0, 1, "missing")]))
    current = chunk(offsets=[0, 60000])
    current["rows"].insert(1, {"open_time_ms": None, "fetched_at": None})
    assert audit(report, [current])["coordinates"][0]["current_timestamp_inventory"]["invalid_timestamp_rows"] == 1


@pytest.mark.parametrize("invalid", [True, False, 1.5, b"blob", [], {}])
def test_unsupported_typed_values_refuse(invalid):
    with pytest.raises(readiness.ReadinessRefusal, match="current_typed_columns_mismatch"):
        audit(source(changes=unknown()), [chunk(extra_rows=[{"open_time_ms": invalid, "fetched_at": None}])])


@pytest.mark.parametrize("mutation,code", [
    (lambda current: current.update(symbol=SYMBOLS[1]), "current_chunk_order_or_identity_mismatch"),
    (lambda current: current.update(retrieved_at="2026-10-22T00:00:00Z"), "current_retrieval_predates_snapshot"),
    (lambda current: current.update(retrieved_at="2026-10-23T00:00:00"), "invalid_aware_clock"),
    (lambda current: current["rows"].append({"open_time_ms": 0, "fetched_at": None}), "current_timestamp_outside_session"),
    (lambda current: current["rows"].append({"open_time_ms": 99999999999999999999999999, "fetched_at": None}), "current_timestamp_outside_session"),
    (lambda current: current.update(extra=True), "current_chunk_order_or_identity_mismatch"),
])
def test_current_metadata_coordinate_and_clock_mutations_refuse(mutation, code):
    current = chunk()
    mutation(current)
    with pytest.raises(readiness.ReadinessRefusal, match=code):
        audit(source(changes=unknown()), [current])


def test_missing_extra_and_out_of_order_current_chunks_refuse():
    changes = {**unknown(symbol=SYMBOLS[0]), **unknown(symbol=SYMBOLS[1])}
    report = source(changes=changes)
    with pytest.raises(readiness.ReadinessRefusal, match="missing_current_chunk"):
        audit(report, [chunk()])
    with pytest.raises(readiness.ReadinessRefusal, match="current_chunk_order_or_identity_mismatch"):
        audit(report, [chunk(symbol=SYMBOLS[1]), chunk()])
    with pytest.raises(readiness.ReadinessRefusal, match="current_integer_timestamp_order_mismatch"):
        audit(source(changes=unknown()), [chunk(offsets=[60000, 0])])


def test_full_original_union_and_policy_counts_are_retained():
    report = source(d2_last_symbol="KRX:000021", changes=unknown())
    result = audit(report, [chunk()])
    assert result["summary"]["union"]["target_coordinates"] == 21
    assert result["summary"]["by_variant"]["D+1"]["target_coordinates"] == 20
    assert result["summary"]["by_variant"]["D+2"]["target_coordinates"] == 20
    assert result["plan"]["source_target_matrix"] == report["source_target_matrix"]
    assert result["plan"]["coordinates"][-1]["policies"] == ["D+2"]


def test_retention_cutoff_equal_is_inside_and_day_before_is_outside():
    days = ("2025-10-22", "2025-10-23")
    plan = readiness.derive_plan(source(days), as_of="2026-10-23")
    assert plan["retention_cutoff"] == "2025-10-23"
    assert plan["coordinates"][0]["retention_status"] == readiness.OUTSIDE
    assert plan["coordinates"][-1]["retention_status"] == readiness.INSIDE


def test_leap_date_clips_one_calendar_year_and_snapshot_uses_kst_date():
    assert readiness.derive_plan(source(), as_of="2028-02-29")["retention_cutoff"] == "2027-02-28"
    result = readiness.build_audit(source(), (), snapshot_at="2026-10-22T15:00:00Z")
    assert result["as_of"] == "2026-10-23"


def test_source_metadata_flags_and_unknowns_are_copied_without_inspecting_prices():
    report = source(changes=unknown())
    plan = readiness.derive_plan(report, as_of="2026-10-23")
    assert plan["source_flags"]["underpower_exception_applied"] is False
    assert plan["source_choice_state"]["requires_operator_choice_before_comparison"] is True
    record = plan["coordinates"][0]["source_records"]["D+1"]
    assert record["unknown_reasons"] == ["no_saved_rows_unknown_cause"]
    assert record["raw_chunk_metadata"]["available_at"] is None
    assert record["raw_chunk_metadata"]["retrieved_at"] != record["source_fetched_at"][0]
    plan["source_target_matrix"]["D+1"]["days"][0]["targets"][0]["rank"] = 999
    assert report["source_target_matrix"]["D+1"]["days"][0]["targets"][0]["rank"] == 1


@pytest.mark.parametrize("mutation,code", [
    (lambda report: report.update(study_id="different"), "source_identity_mismatch"),
    (lambda report: report.update(mode="performance"), "source_identity_mismatch"),
    (lambda report: report.update(promotion_allowed=True), "source_historical_flags_changed"),
    (lambda report: report["input_evidence"].update(stream_sha256="b" * 64), "source_input_identity_mismatch"),
    (lambda report: report["input_evidence"].update(unique_target_days=19), "source_input_coordinate_count_mismatch"),
    (lambda report: report["variants"]["D+1"]["days"].pop(), "source_target_population_mismatch"),
    (lambda report: report["variants"]["D+1"]["days"][0]["raw_chunk_metadata"].update(available_at="2025-09-04T00:00:00Z"), "source_row_provenance_mismatch"),
    (lambda report: report["variants"]["D+1"]["days"][0].update(data_vintage="not-a-sha"), "source_row_provenance_mismatch"),
    (lambda report: report["variants"]["D+1"]["days"][0]["arms"]["15_retest"]["decision_prefix_unknown_bucket_counts"].update(missing=True), "source_gap_count_type_mismatch"),
])
def test_source_identity_provenance_and_population_mutations_refuse(mutation, code):
    report = source(changes=unknown())
    mutation(report)
    with pytest.raises(readiness.ReadinessRefusal, match=code):
        readiness.derive_plan(report, as_of="2026-10-23")


@pytest.mark.parametrize("limit,code", [("maximum_session_rows", "current_session_row_budget_exceeded"),
    ("maximum_rows", "current_population_budget_exceeded"), ("maximum_queries", "current_population_budget_exceeded"),
    ("maximum_chunk_bytes", "current_chunk_byte_budget_exceeded"), ("maximum_uncompressed_bytes", "current_population_budget_exceeded")])
def test_current_resource_limits_fail_closed(monkeypatch, limit, code):
    monkeypatch.setitem(readiness.RESOURCES, limit, 0)
    with pytest.raises(readiness.ReadinessRefusal, match=code):
        audit(source(changes=unknown()), [chunk(offsets=[0])])


def test_checkpoint_and_input_mutation_purity():
    report, calls = source(changes=unknown()), []
    metadata_before = deepcopy(report["source_target_matrix"])
    current = chunk()
    current_before = deepcopy(current)
    readiness.build_audit(report, [current], snapshot_at=SNAPSHOT, checkpoint=lambda: calls.append(True))
    assert len(calls) >= 22
    assert report["source_target_matrix"] == metadata_before
    assert current == current_before
