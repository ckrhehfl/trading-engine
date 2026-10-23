"""Required-gap timestamp readiness for the completed, unchanged DC retest.

Pure metadata projections only. The caller authenticates the pinned completed
report, captures a bounded read-only timestamp snapshot and enforces budgets.
Timestamp presence cannot certify prices, repair a signal or qualify a trade.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SPECIFICATION_ID = "intraday-retest-gap-readiness-v1"
MODE = "fixed-retest-required-gap-timestamp-readiness-only"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-retest-gap-readiness-v1.json"
INPUT_REPORT_SHA256 = "8d2f0393f92f18094e4d6c0dc7150de0db512cca58ab884dc2ea100bf60c2b44"
INPUT_SPEC_SHA256 = "4f7e85aaea3880789171287fe78fe6be0cd316cfc49a9f854f3663b637215b60"
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
REGISTERED_PATHS = {
    "source_report_path": "/home/minjun4897/research-evidence/intraday-dc-opening-retest-20261023-v1/report.json",
    "source_database": "/home/minjun4897/trading-engine/python/data/var/klines.sqlite3",
    "output_directory": "/home/minjun4897/research-evidence/intraday-dd-retest-gap-readiness-20261023-v1",
    "canonical_runs_path": "/home/minjun4897/trading-engine/runs/experiments.jsonl",
}
RESOURCES = {
    "maximum_source_bytes": 67_108_864, "maximum_target_days": 6000,
    "maximum_queries": 6000, "maximum_session_rows": 600, "maximum_rows": 3_600_000,
    "maximum_query_seconds": 10, "maximum_query_vm_steps": 5_000_000,
    "maximum_chunk_bytes": 1_048_576, "maximum_chunks": 6000,
    "maximum_compressed_bytes": 536_870_912, "maximum_uncompressed_bytes": 1_073_741_824,
    "minimum_free_bytes": 1_073_741_824, "cpu_seconds": 300,
    "address_space_mib": 512, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0,
}
PARAMETERS = {
    "coordinates": "all_original_DC_D+1_D+2_target_union_no_replacement",
    "unknown_signal_required_region": "session_open_through_open_plus235minutes_exclusive_all47_causal5minute_bars",
    "unknown_label_required_region": "original_entry_through_original_exit_plus1minute_exclusive_all31_buckets",
    "known_states": "known_signal_known_label_and_known_no_signal_request_no_data",
    "current_queries": "one_exact_symbol_original_session_timestamp_only_query_per_coordinate_with_required_missing_buckets",
    "current_columns": ["open_time_ms", "fetched_at"],
    "minute_bucket": "floor_original_integer_milliseconds_preserve_offsets_duplicates_and_invalid_timestamp_types",
    "candidate": "original_required_missing_minute_bucket_intersection_current_timestamp_presence_only_not_valid_OHLC_or_repair",
    "other_defects": "conflict_and_invalid_price_remain_unvalidated_need_OHLC_not_timestamp_presence",
    "absence_cause": "unknown_no_imputed_halt_listing_or_provider_failure",
    "retention": "snapshot_started_at_KST_date_minus1calendar_year_clip_Feb29_inside_unprobed_outside_not_promised_by_this_endpoint",
    "api_attempts": 0, "new_signals_calculated": False,
    "new_labels_calculated": False, "performance_calculated": False,
}
SOURCE_ID = "intraday-opening-retest-calibration-v1"
SOURCE_MODE = "fixed-opening-retest-own-label-frequency-calibration"
SOURCE_EVIDENCE = {
    "upstream_report_sha256": "f1ac24fed9ee3a54dfcac0d25ef1ff339be0a872d8915d849e4369e4d5c5eb91",
    "upstream_report_path": "/home/minjun4897/research-evidence/intraday-cb-universe-20261020-v1/report.json",
    "archive_path": "/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/inputs.jsonl.gz",
    "compressed_sha256": "4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de",
    "stream_sha256": "d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da",
    "pin_authentication": "caller_obligation_not_proven_by_pure_builder",
}
SOURCE_FALSE_FLAGS = (
    "historical_eligibility_certified", "liquidity_certified", "historical_revision_status_certified",
    "calendar_completeness_certified", "historical_api_availability_proven", "price_basis_certified",
    "performance_certified", "promotion_allowed", "full_family_certified", "actual_portfolio_pnl",
    "comparison_performed", "family_adopted", "underpower_exception_applied",
    "historical_costs_certified", "actual_fills_certified",
)
POLICIES = ("D+1", "D+2")
GAP_STATES = ("missing", "conflict", "invalid_price")
GROUPS = ("unknown_signal_prefix", "known_signal_unknown_label", "no_required_read_region")
KST = timezone(timedelta(hours=9))
MINUTE = timedelta(minutes=1)
INSIDE = "inside_documented_maximum_horizon_unprobed"
OUTSIDE = "outside_documented_maximum_horizon_not_promised_by_endpoint"


class ReadinessRefusal(RuntimeError):
    """Stable codes avoid exposing source values or credentials."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "source_report_sha256": INPUT_REPORT_SHA256, "source_specification_sha256": INPUT_SPEC_SHA256}
    try:
        equal = _canonical(spec) == _canonical(expected)
    except (TypeError, ValueError):
        equal = False
    if not equal:
        raise ReadinessRefusal("specification_scope_mismatch")


def _day(value: object) -> date:
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError as exc:
        raise ReadinessRefusal("invalid_date") from exc
    if parsed is None or parsed.isoformat() != value:
        raise ReadinessRefusal("invalid_date")
    return parsed


def _instant(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError as exc:
        raise ReadinessRefusal("invalid_aware_clock") from exc
    if parsed is None or parsed.utcoffset() is None:
        raise ReadinessRefusal("invalid_aware_clock")
    return parsed


def _opening(day: str) -> datetime:
    return datetime.combine(_day(day), time(10 if day in {"2025-11-13", "2026-01-02"} else 9), KST)


def _closing(day: str) -> datetime:
    return _opening(day).replace(hour=16 if day == "2025-11-13" else 15, minute=30)


def _iso(clock: datetime) -> str:
    return clock.astimezone(KST).isoformat()


def _zero_counts() -> dict:
    return dict.fromkeys(GAP_STATES, 0)


def _intervals(buckets: Mapping[datetime, str]) -> list[dict]:
    result = []
    for minute, state in sorted(buckets.items()):
        if result and result[-1]["end"] == _iso(minute) and result[-1]["state"] == state:
            result[-1]["end"] = _iso(minute + MINUTE)
        else:
            result.append({"start": _iso(minute), "end": _iso(minute + MINUTE), "state": state})
    return result


def _bucket_map(intervals: object, day: str) -> dict[datetime, str]:
    if not isinstance(intervals, (list, tuple)):
        raise ReadinessRefusal("invalid_source_gap_intervals")
    result, previous = {}, _opening(day)
    for interval in intervals:
        if not isinstance(interval, Mapping) or set(interval) != {"start", "end", "state"}:
            raise ReadinessRefusal("invalid_source_gap_intervals")
        start, end = (_instant(interval[key]).astimezone(KST) for key in ("start", "end"))
        if (interval["state"] not in GAP_STATES or not previous <= start < end <= _closing(day)
                or start.second or start.microsecond or end.second or end.microsecond):
            raise ReadinessRefusal("invalid_source_gap_intervals")
        previous = end
        for offset in range(int((end - start) // MINUTE)):
            result[start + offset * MINUTE] = interval["state"]
    return result


def _source_rows(report: Mapping) -> tuple[list[str], dict, dict]:
    if (report.get("study_id") != SOURCE_ID or report.get("mode") != SOURCE_MODE
            or report.get("window") != WINDOW):
        raise ReadinessRefusal("source_identity_mismatch")
    if any(report.get(key) is not False for key in SOURCE_FALSE_FLAGS):
        raise ReadinessRefusal("source_historical_flags_changed")
    if report.get("exploratory_only") is not True or report.get("calibration_only") is not True:
        raise ReadinessRefusal("source_historical_flags_changed")
    evidence = report.get("input_evidence")
    if not isinstance(evidence, Mapping) or any(evidence.get(key) != value for key, value in SOURCE_EVIDENCE.items()):
        raise ReadinessRefusal("source_input_identity_mismatch")
    reference = report.get("reference_sessions")
    if (not isinstance(reference, list) or not reference or reference != sorted(set(reference))
            or any(not WINDOW["start"] <= _day(day).isoformat() <= WINDOW["end"] for day in reference)):
        raise ReadinessRefusal("source_reference_sessions_mismatch")
    weeks = sorted({f"{_day(day).isocalendar().year}-W{_day(day).isocalendar().week:02}" for day in reference})
    if report.get("reference_weeks") != weeks:
        raise ReadinessRefusal("source_reference_weeks_mismatch")
    matrix, variants = report.get("source_target_matrix"), report.get("variants")
    if not isinstance(matrix, Mapping) or not isinstance(variants, Mapping) or set(matrix) != set(POLICIES) or set(variants) != set(POLICIES):
        raise ReadinessRefusal("source_policy_population_mismatch")
    rows_by_policy, union = {}, {}
    for policy in POLICIES:
        memberships, source_rows = matrix[policy].get("days"), variants[policy].get("days")
        if not isinstance(memberships, list) or [row.get("session") for row in memberships] != reference or not isinstance(source_rows, list):
            raise ReadinessRefusal("source_target_population_mismatch")
        expected = []
        for membership in memberships:
            day = membership["session"]
            targets = membership.get("targets")
            if (not isinstance(targets, list) or len(targets) != 20
                    or {row.get("rank") for row in targets} != set(range(1, 21))):
                raise ReadinessRefusal("source_target_population_mismatch")
            symbols = [row.get("symbol") for row in targets]
            if any(not isinstance(symbol, str) or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) for symbol in symbols) or len(set(symbols)) != 20:
                raise ReadinessRefusal("source_target_population_mismatch")
            if not _instant(membership["available_at"]) <= _instant(membership["selected_at"]) < _opening(day):
                raise ReadinessRefusal("source_membership_timing_mismatch")
            for target in targets:
                expected.append((day, target["symbol"], target, membership))
        if len(source_rows) != len(expected):
            raise ReadinessRefusal("source_target_population_mismatch")
        rows_by_policy[policy] = {}
        for row, (day, symbol, target, membership) in zip(source_rows, expected, strict=True):
            if (not isinstance(row, Mapping) or row.get("session") != day or row.get("symbol") != symbol
                    or row.get("membership_target") != target
                    or row.get("membership_source") != {key: value for key, value in membership.items() if key != "targets"}):
                raise ReadinessRefusal("source_membership_projection_mismatch")
            key = day, symbol
            rows_by_policy[policy][key] = row
            union.setdefault(key, []).append(policy)
    if len(union) > RESOURCES["maximum_target_days"]:
        raise ReadinessRefusal("source_coordinate_budget_exceeded")
    if type(evidence.get("unique_target_days")) is not int or evidence["unique_target_days"] != len(union):
        raise ReadinessRefusal("source_input_coordinate_count_mismatch")
    return reference, rows_by_policy, union


def _projection(row: Mapping, gaps: Mapping[datetime, str]) -> tuple[dict, dict | None, dict]:
    arm = row.get("arms", {}).get("15_retest")
    if not isinstance(arm, Mapping) or arm.get("status") not in {"signal", "known_no_signal", "unknown"}:
        raise ReadinessRefusal("source_signal_state_mismatch")
    status, label = arm["status"], arm.get("label")
    if (type(row.get("input_rows")) is not int or row["input_rows"] < 0
            or not isinstance(row.get("data_vintage"), str) or not re.fullmatch("[0-9a-f]{64}", row["data_vintage"])
            or not isinstance(arm.get("unknown_reasons"), list)):
        raise ReadinessRefusal("source_row_provenance_mismatch")
    metadata = row.get("raw_chunk_metadata")
    if (not isinstance(metadata, Mapping) or set(metadata) != {"observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level"}
            or metadata["observation_date"] != row["session"] or metadata["available_at"] is not None
            or metadata["source"] != "collector klines" or metadata["is_final"] is not None
            or metadata["evidence_level"] != "assumed" or not isinstance(row.get("source_fetched_at"), list)
            or any(not isinstance(value, str) for value in row["source_fetched_at"])):
        raise ReadinessRefusal("source_row_provenance_mismatch")
    _instant(metadata["retrieved_at"])
    prefix_counts = arm.get("decision_prefix_unknown_bucket_counts")
    if (not isinstance(prefix_counts, Mapping) or set(prefix_counts) != set(GAP_STATES)
            or any(type(value) is not int or value < 0 for value in prefix_counts.values())):
        raise ReadinessRefusal("source_gap_count_type_mismatch")
    if (status == "signal" and (not isinstance(label, Mapping) or label.get("status") not in {"known", "unknown"})
            or status != "signal" and label is not None):
        raise ReadinessRefusal("source_label_state_mismatch")
    if label:
        label_counts = label.get("unknown_bucket_reason_counts")
        if (not isinstance(label_counts, Mapping) or set(label_counts) != set(GAP_STATES)
                or any(type(value) is not int or value < 0 for value in label_counts.values())
                or not isinstance(label.get("unknown_reasons"), list)):
            raise ReadinessRefusal("source_gap_count_type_mismatch")
    opening, region = _opening(row["session"]), None
    if status == "unknown":
        region = {"group": GROUPS[0], "start": _iso(opening), "end": _iso(opening + 235 * MINUTE)}
    elif status == "signal" and label["status"] == "unknown":
        entry, exit_at = (_instant(label[key]).astimezone(KST) for key in ("entry_at", "exit_at"))
        if (not opening < entry < exit_at < _closing(row["session"]) or exit_at - entry != 30 * MINUTE
                or entry.second or entry.microsecond or exit_at.second or exit_at.microsecond):
            raise ReadinessRefusal("source_label_clock_mismatch")
        region = {"group": GROUPS[1], "start": _iso(entry), "end": _iso(exit_at + MINUTE)}
    required = {minute: state for minute, state in gaps.items()
                if region and _instant(region["start"]) <= minute < _instant(region["end"])}
    counts = dict(Counter(required.values()))
    expected_counts = arm.get("decision_prefix_unknown_bucket_counts") if status == "unknown" else label.get("unknown_bucket_reason_counts") if region else None
    if region and (expected_counts != {state: counts.get(state, 0) for state in GAP_STATES} or not required):
        raise ReadinessRefusal("source_required_gap_counts_mismatch")
    projection = {"status": status, "unknown_reasons": deepcopy(arm["unknown_reasons"]),
        "known_no_signal_reason": arm.get("known_no_signal_reason"),
        "label_status": label["status"] if label else "not_applicable",
        "label_unknown_reasons": deepcopy(label["unknown_reasons"]) if label else [],
        "label_entry_at": label["entry_at"] if label else None,
        "label_exit_at": label["exit_at"] if label else None,
        "decision_prefix_unknown_bucket_counts": deepcopy(arm["decision_prefix_unknown_bucket_counts"]),
        "label_unknown_bucket_reason_counts": deepcopy(label["unknown_bucket_reason_counts"]) if label else _zero_counts(),
        **{key: deepcopy(row[key]) for key in ("input_rows", "data_vintage", "raw_chunk_metadata", "source_fetched_at", "membership_target", "membership_source")}}
    return projection, region, required


def derive_plan(dc_report: Mapping, *, as_of: str) -> dict:
    """Project the full original union before the caller queries any timestamp.

    Small invented twenty-target session grids are supported; the actual caller
    also checks the pinned 257 dates/56 weeks/5140 targets/5158 union identity.
    """
    audit_day = _day(as_of)
    try:
        cutoff = audit_day.replace(year=audit_day.year - 1)
    except ValueError:
        cutoff = audit_day.replace(year=audit_day.year - 1, day=28)
    reference, rows_by_policy, union = _source_rows(dc_report)
    coordinates = []
    for (day, symbol), policies in sorted(union.items()):
        source_records, regions, by_policy, required, source_gaps = {}, {}, {}, {}, None
        for policy in policies:
            row = rows_by_policy[policy][day, symbol]
            gaps = _bucket_map(row.get("gaps"), day)
            if source_gaps is not None and gaps != source_gaps:
                raise ReadinessRefusal("source_shared_coordinate_gaps_changed")
            source_gaps = gaps
            projection, region, needed = _projection(row, gaps)
            if source_records:
                common = {key: value for key, value in projection.items() if key not in {"membership_target", "membership_source"}}
                previous = next(iter(source_records.values()))
                if common != {key: value for key, value in previous.items() if key not in {"membership_target", "membership_source"}}:
                    raise ReadinessRefusal("source_shared_coordinate_state_changed")
            source_records[policy], regions[policy], by_policy[policy] = projection, region, _intervals(needed)
            required.update(needed)
        coordinates.append({"session": day, "symbol": symbol, "policies": list(policies),
            "source_records": source_records, "required_regions_by_policy": regions,
            "required_gap_intervals_by_policy": by_policy, "source_session_gaps": _intervals(source_gaps),
            "required_gap_intervals": _intervals(required), "requires_read": "missing" in required.values(),
            "retention_status": INSIDE if _day(day) >= cutoff else OUTSIDE})
    choices = {policy: {key: deepcopy(dc_report["variants"][policy]["by_arm"]["15_retest"][key])
        for key in ("requires_operator_choice_before_comparison", "comparison_choice_reasons")} for policy in POLICIES}
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW), "as_of": as_of,
        "retention_cutoff": cutoff.isoformat(), "reference_sessions": list(reference),
        "reference_weeks": deepcopy(dc_report["reference_weeks"]),
        "source_identity": {"report_path": REGISTERED_PATHS["source_report_path"],
            "report_sha256": INPUT_REPORT_SHA256, "specification_sha256": INPUT_SPEC_SHA256,
            "source_study_id": SOURCE_ID, "source_mode": SOURCE_MODE,
            "source_input_evidence": deepcopy(dc_report["input_evidence"]),
            "authentication": "caller_obligation_not_proven_by_pure_builder"},
        "source_flags": {key: value for key, value in dc_report.items() if type(value) is bool},
        "source_assumptions": deepcopy(dc_report["assumptions"]),
        "source_choice_state": {"requires_operator_choice_before_comparison": dc_report["requires_operator_choice_before_comparison"], "variants": choices},
        "source_target_matrix": deepcopy(dc_report["source_target_matrix"]), "coordinates": coordinates}


def _inventory(chunk: Mapping, coordinate: Mapping, snapshot_at: datetime) -> tuple[dict, set[datetime]]:
    if (not isinstance(chunk, Mapping) or set(chunk) != {"session", "symbol", "retrieved_at", "rows"}
            or (chunk["session"], chunk["symbol"]) != (coordinate["session"], coordinate["symbol"])):
        raise ReadinessRefusal("current_chunk_order_or_identity_mismatch")
    retrieved_at = _instant(chunk["retrieved_at"])
    if retrieved_at < snapshot_at:
        raise ReadinessRefusal("current_retrieval_predates_snapshot")
    rows = chunk["rows"]
    if not isinstance(rows, list) or len(rows) > RESOURCES["maximum_session_rows"]:
        raise ReadinessRefusal("current_session_row_budget_exceeded")
    try:
        chunk_bytes = _canonical(chunk)
    except (TypeError, ValueError) as exc:
        raise ReadinessRefusal("current_typed_columns_mismatch") from exc
    if len(chunk_bytes) + 1 > RESOURCES["maximum_chunk_bytes"]:
        raise ReadinessRefusal("current_chunk_byte_budget_exceeded")
    buckets, invalid, offsets, previous, fetched = Counter(), 0, Counter(), None, set()
    opening, closing = _opening(coordinate["session"]), _closing(coordinate["session"])
    for row in rows:
        if (not isinstance(row, Mapping) or set(row) != {"open_time_ms", "fetched_at"}
                or row["fetched_at"] is not None and not isinstance(row["fetched_at"], str)
                or type(row["open_time_ms"]) not in {int, str, type(None)}):
            raise ReadinessRefusal("current_typed_columns_mismatch")
        if row["fetched_at"] is not None:
            fetched.add(row["fetched_at"])
        stamp = row["open_time_ms"]
        if type(stamp) is not int:
            invalid += 1
            continue
        if previous is not None and stamp < previous:
            raise ReadinessRefusal("current_integer_timestamp_order_mismatch")
        previous = stamp
        try:
            minute = datetime.fromtimestamp(stamp // 1000, KST).replace(second=0, microsecond=0)
        except (ValueError, OSError, OverflowError) as exc:
            raise ReadinessRefusal("current_timestamp_outside_session") from exc
        if not opening <= minute < closing:
            raise ReadinessRefusal("current_timestamp_outside_session")
        buckets[minute] += 1
        offsets[str(stamp % 60_000)] += 1
    return {"retrieved_at": chunk["retrieved_at"], "observed_rows": len(rows),
        "valid_timestamp_rows": len(rows) - invalid, "invalid_timestamp_rows": invalid,
        "unique_minute_buckets": len(buckets), "duplicate_minute_buckets": sum(count > 1 for count in buckets.values()),
        "duplicate_timestamp_rows": sum(count - 1 for count in buckets.values()),
        "source_offset_counts_ms": dict(sorted(offsets.items(), key=lambda item: int(item[0]))),
        "source_fetched_at": sorted(fetched), "current_chunk_sha256": hashlib.sha256(chunk_bytes + b"\n").hexdigest()}, set(buckets)


def _summary(coordinates: list[Mapping], audits: list[Mapping], policy: str | None = None) -> dict:
    selected = [(coordinate, audit) for coordinate, audit in zip(coordinates, audits, strict=True)
                if policy is None or policy in coordinate["policies"]]
    queried = [coordinate for coordinate, _ in selected if coordinate["requires_read"]]
    source_states = Counter()
    label_states = Counter()
    signal_unknown_reasons, label_unknown_reasons, known_no_signal_reasons = Counter(), Counter(), Counter()
    groups = {group: {"coordinates": 0, "required_gap_counts": _zero_counts(),
        "candidate_missing_timestamp_buckets": 0, "remaining_missing_timestamp_buckets": 0} for group in GROUPS}
    required_counts, retention = _zero_counts(), {INSIDE: 0, OUTSIDE: 0}
    candidate_count = remaining_count = absence_unknown = 0
    for coordinate, audit in selected:
        representative = policy or coordinate["policies"][0]
        record = coordinate["source_records"][representative]
        region = coordinate["required_regions_by_policy"][representative]
        group = region["group"] if region else GROUPS[2]
        groups[group]["coordinates"] += 1
        source_states[record["status"]] += 1
        label_states[record["label_status"]] += 1
        signal_unknown_reasons.update(record["unknown_reasons"])
        label_unknown_reasons.update(record["label_unknown_reasons"])
        if record["status"] == "known_no_signal":
            known_no_signal_reasons[record["known_no_signal_reason"]] += 1
        absence_unknown += record["input_rows"] == 0
        gaps = _bucket_map(coordinate["required_gap_intervals_by_policy"][representative] if policy else coordinate["required_gap_intervals"], coordinate["session"])
        counts = Counter(gaps.values())
        for state in GAP_STATES:
            required_counts[state] += counts[state]
            groups[group]["required_gap_counts"][state] += counts[state]
        candidates = sum(int((_instant(interval["end"]) - _instant(interval["start"])) // MINUTE) for interval in audit["candidate_missing_intervals"])
        missing = counts["missing"] - candidates
        candidate_count += candidates
        remaining_count += missing
        groups[group]["candidate_missing_timestamp_buckets"] += candidates
        groups[group]["remaining_missing_timestamp_buckets"] += missing
        if counts["missing"]:
            retention[coordinate["retention_status"]] += 1
    return {"target_coordinates": len(selected), "target_dates": len({row["session"] for row, _ in selected}),
        "target_symbols": len({row["symbol"] for row, _ in selected}), "query_coordinates": len(queried),
        "query_dates": len({row["session"] for row in queried}), "query_symbols": len({row["symbol"] for row in queried}),
        "source_signal_status_counts": {state: source_states[state] for state in ("signal", "known_no_signal", "unknown")},
        "source_label_status_counts": {state: label_states[state] for state in ("known", "unknown", "not_applicable")},
        "source_unknown_reason_counts": dict(sorted(signal_unknown_reasons.items())),
        "source_label_unknown_reason_counts": dict(sorted(label_unknown_reasons.items())),
        "source_known_no_signal_reason_counts": dict(sorted(known_no_signal_reasons.items())),
        "source_absence_unknown_cause_coordinates": absence_unknown,
        "required_gap_counts": required_counts, "candidate_missing_timestamp_buckets": candidate_count,
        "remaining_missing_timestamp_buckets": remaining_count, "pending_conflict_minute_buckets": required_counts["conflict"],
        "pending_invalid_price_minute_buckets": required_counts["invalid_price"],
        "missing_coordinate_retention_counts": retention, "by_group": groups}


def build_audit(dc_report: Mapping, current_chunks: Iterable[Mapping], *, snapshot_at: str,
                checkpoint: Callable[[], None] | None = None) -> dict:
    """Consume ordered typed timestamp chunks, only for plan.requires_read rows."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    snapshot = _instant(snapshot_at)
    plan = derive_plan(dc_report, as_of=snapshot.astimezone(KST).date().isoformat())
    iterator, audits, row_count, chunk_count, byte_count = iter(current_chunks), [], 0, 0, 0
    for coordinate in plan["coordinates"]:
        checkpoint()
        required = _bucket_map(coordinate["required_gap_intervals"], coordinate["session"])
        missing = {minute for minute, state in required.items() if state == "missing"}
        inventory, current = None, set()
        if coordinate["requires_read"]:
            try:
                chunk = next(iterator)
            except StopIteration as exc:
                raise ReadinessRefusal("missing_current_chunk") from exc
            inventory, current = _inventory(chunk, coordinate, snapshot)
            row_count += inventory["observed_rows"]
            chunk_count += 1
            byte_count += len(_canonical(chunk)) + 1
            if (row_count > RESOURCES["maximum_rows"] or chunk_count > min(RESOURCES["maximum_queries"], RESOURCES["maximum_chunks"])
                    or byte_count > RESOURCES["maximum_uncompressed_bytes"]):
                raise ReadinessRefusal("current_population_budget_exceeded")
        candidates = missing & current
        audits.append({"session": coordinate["session"], "symbol": coordinate["symbol"],
            "queried": coordinate["requires_read"], "current_timestamp_inventory": inventory,
            "candidate_missing_intervals": _intervals(dict.fromkeys(candidates, "missing")),
            "remaining_missing_intervals": _intervals(dict.fromkeys(missing - candidates, "missing")),
            "pending_conflict_minute_buckets": sum(state == "conflict" for state in required.values()),
            "pending_invalid_price_minute_buckets": sum(state == "invalid_price" for state in required.values()),
            "absence_cause": "unknown", "valid_ohlc_certified": False, "signal_repaired": False,
            "tradability_certified": False})
    try:
        next(iterator)
    except StopIteration:
        pass
    else:
        raise ReadinessRefusal("extra_current_chunk")
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "snapshot_at": snapshot_at, "as_of": plan["as_of"], "retention_cutoff": plan["retention_cutoff"],
        "plan": plan, "coordinates": audits,
        "summary": {"union": _summary(plan["coordinates"], audits),
            "by_variant": {policy: _summary(plan["coordinates"], audits, policy) for policy in POLICIES}},
        "input_evidence": {"query_count": chunk_count, "row_count": row_count,
            "timestamp_stream_bytes": byte_count, "source_database": REGISTERED_PATHS["source_database"],
            "pin_authentication": "caller_obligation_not_proven_by_pure_builder"},
        "claims": {"timestamp_presence_only": True, "api_attempts": 0, "new_signals_calculated": False,
            "new_labels_calculated": False, "performance_calculated": False, "old_report_replaced": False,
            "valid_ohlc_certified": False, "tradability_certified": False, "recovery_promised": False,
            "outside_horizon_impossible_from_all_providers": False, "historical_availability_certified": False}}
