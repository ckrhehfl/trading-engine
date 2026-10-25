"""Supplied-byte diagnostics for DU's fixed unknown outcome price windows.

This module has no acquisition, filesystem, authentication or database code. It
does not calculate labels or returns. The frozen caller authenticates complete
input bytes, actual population counts and process budgets before publication.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SPECIFICATION_ID = "intraday-acceptance-gap-recovery-v1"
MODE = "fixed-acceptance-unknown-outcome-price-recovery-diagnostic-only"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-acceptance-gap-recovery-v1.json"
SOURCE_SPECIFICATION_SHA256 = "ee6f2d36773972d3371b7a3030b19494ad93783452cc95846f2e1431b7172e88"
SOURCE_PARAMETERS_SHA256 = "3420caa10a4fb659da9b65695411102e6b81e128b266eec1d20e0aaad1aac16b"
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
SOURCE_ROLES = {"source_report_role": "pinned_DU_completed_calibration_report",
    "input_archive_role": "original_CC_canonical_raw_archive",
    "database_capture_role": "bounded_current_collector_minute_snapshot",
    "quotation_capture_role": "bounded_source_equivalent_KIS_minute_responses",
    "output_role": "new_private_verified_price_evidence_package"}
PARAMETERS = {
    "recovery_scope": "every_original_signal_unknown_outcome_only_exact_window_union",
    "identity": "sha256_canonical_session_symbol_entry_at_exit_at_KST_ISO",
    "required_prices": "all31_entry_through_original_cap_inclusive_exact_positive_finite_Decimal_OHLC",
    "overlap": "every_original_effective_OHLC_exact_match_missing_only_additions_no_overwrite",
    "vintages": "separate_DB_and_API_no_incomplete_vintage_union_no_favorable_conflict_selection",
    "request": "one_source_equivalent_KIS_page_ending_cap_second59_J_pastY_fakeN_max120rows",
    "retention": "request_time_KST_date_minus_one_calendar_year_Feb29_clips_Feb28_upper_bound_only",
    "output_scope": "verified_complete_price_candidates_and_missing_only_additions_no_labels_or_comparison",
    "label_adoption": False, "underpower_exception_applied": False,
}
RESOURCES = {"maximum_source_bytes": 67108864, "maximum_compressed_bytes": 536870912,
    "maximum_uncompressed_bytes": 1073741824, "maximum_chunk_bytes": 1048576,
    "maximum_chunks": 6000, "maximum_rows": 3600000, "maximum_session_rows": 600,
    "maximum_recovery_coordinates": 10, "maximum_db_queries": 10,
    "maximum_db_rows_per_window": 600, "maximum_db_rows": 6000,
    "db_query_seconds": 10, "db_query_vm_steps": 5000000,
    "maximum_market_gets": 10, "maximum_rows_per_response": 120,
    "maximum_token_posts": 1, "minimum_free_bytes": 1073741824,
    "cpu_seconds": 300, "address_space_mib": 512, "wall_seconds": 1200,
    "nice": 10, "automatic_retries": 0}
POLICIES = ("D+1", "D+2")
ARM = "15_acceptance"
KST = timezone(timedelta(hours=9))
MINUTE = timedelta(minutes=1)
COLUMNS = ("symbol", "interval", "open_time_ms", "open", "high", "low", "close", "fetched_at")
PRICE_FIELDS = ("open", "high", "low", "close")
API_PRICE_FIELDS = ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr")
LABEL_METADATA_FIELDS = ("status", "entry_at", "exit_at", "time_cap_decided_at", "entry_open",
    "exit_open", "required_minute_buckets", "unknown_minute_buckets", "unknown_bucket_reason_counts",
    "unknown_reasons", "entry_price_available_at", "label_available_at", "evidence_level", "actual_fill_certified")
FALSE_CLAIMS = {key: False for key in ("original_labels_adopted", "comparison_performed",
    "performance_means_calculated", "own_label_sizing_performed", "underpower_exception_applied",
    "historical_availability_certified", "actual_fills_certified", "actual_portfolio_pnl",
    "strategy_pass", "direction_closed", "paper_live_promotion")}


class AcceptanceGapRefusal(RuntimeError):
    """Stable reason codes carry no private source values."""


def _canonical(value: object) -> bytes:
    def encode(item: object) -> str:
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError("unsupported_canonical_type")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False, default=encode).encode()


def validate_spec(specification: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES,
        "source_specification_sha256": SOURCE_SPECIFICATION_SHA256, **SOURCE_ROLES}
    if _canonical(specification) != _canonical(expected):
        raise AcceptanceGapRefusal("specification_scope_mismatch")


def _instant(value: object) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(KST)
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise AcceptanceGapRefusal("invalid_aware_clock") from exc


def _iso(value: datetime) -> str:
    return value.astimezone(KST).isoformat()


def _day(value: object) -> date:
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise AcceptanceGapRefusal("invalid_session_date") from exc


def _bounds(day: str) -> tuple[datetime, datetime]:
    opening = "10:00" if day in {"2025-11-13", "2026-01-02"} else "09:00"
    closing = "16:30" if day == "2025-11-13" else "15:30"
    return tuple(datetime.fromisoformat(f"{day}T{clock}:00+09:00") for clock in (opening, closing))


def _gaps(raw: object, day: str) -> dict:
    if not isinstance(raw, (list, tuple)):
        raise AcceptanceGapRefusal("source_gap_schema_mismatch")
    opening, closing = _bounds(day)
    result = {}
    for gap in raw:
        if not isinstance(gap, Mapping) or set(gap) != {"start", "end", "state"}:
            raise AcceptanceGapRefusal("source_gap_schema_mismatch")
        start, end = _instant(gap["start"]), _instant(gap["end"])
        if (gap["state"] not in {"missing", "conflict", "invalid_price"}
                or not opening <= start < end <= closing
                or any(value.second or value.microsecond for value in (start, end))):
            raise AcceptanceGapRefusal("source_gap_schema_mismatch")
        while start < end:
            if start in result:
                raise AcceptanceGapRefusal("source_gap_overlap")
            result[start] = gap["state"]
            start += MINUTE
    return result


def _intervals(gaps: Mapping) -> list[dict]:
    result = []
    for minute, state in sorted(gaps.items()):
        if result and result[-1]["state"] == state and result[-1]["end"] == _iso(minute):
            result[-1]["end"] = _iso(minute + MINUTE)
        else:
            result.append({"start": _iso(minute), "end": _iso(minute + MINUTE), "state": state})
    return result


def _source(report: Mapping) -> tuple[dict, list[dict], dict]:
    try:
        if (report["study_id"] != "intraday-opening-acceptance-calibration-v1"
                or report["mode"] != "fixed-opening-acceptance-own-label-frequency-calibration"
                or report["window"] != WINDOW
                or hashlib.sha256(_canonical(report["parameters"])).hexdigest() != SOURCE_PARAMETERS_SHA256
                or set(report["variants"]) != set(POLICIES)):
            raise AcceptanceGapRefusal("source_scope_mismatch")
        reference = report["reference_sessions"]
        if not isinstance(reference, list) or reference != sorted(set(reference)) or not reference:
            raise AcceptanceGapRefusal("source_reference_calendar_mismatch")
        for day in reference:
            _day(day)
            if not WINDOW["start"] <= day <= WINDOW["end"]:
                raise AcceptanceGapRefusal("source_reference_calendar_mismatch")
        weeks = sorted({f"{_day(day).isocalendar().year:04d}-W{_day(day).isocalendar().week:02d}" for day in reference})
        if report["reference_weeks"] != weeks:
            raise AcceptanceGapRefusal("source_reference_weeks_mismatch")
        requests, rows, counts = {}, {}, {}
        for policy in POLICIES:
            seen, signals, labels, signal_reasons, label_reasons = set(), Counter(), Counter(), Counter(), Counter()
            for row in report["variants"][policy]["days"]:
                day, symbol = row["session"], row["symbol"]
                if day not in reference or not isinstance(symbol, str) or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol):
                    raise AcceptanceGapRefusal("source_target_identity_mismatch")
                if (day, symbol) in seen:
                    raise AcceptanceGapRefusal("source_duplicate_policy_target")
                seen.add((day, symbol))
                arm = row["arms"][ARM]
                state = arm["status"]
                if state not in {"signal", "known_no_signal", "unknown"}:
                    raise AcceptanceGapRefusal("source_signal_state_mismatch")
                signals[state] += 1
                signal_reasons.update(arm["unknown_reasons"])
                if state != "signal":
                    if arm["label"] is not None:
                        raise AcceptanceGapRefusal("source_non_signal_has_label")
                    continue
                label = arm["label"]
                if label["status"] not in {"known", "unknown"}:
                    raise AcceptanceGapRefusal("source_label_state_mismatch")
                labels[label["status"]] += 1
                label_reasons.update(label["unknown_reasons"])
                # No price_change read, even for original known outcomes.
                decision, entry, cap = (_instant(label[key]) for key in ("time_cap_decided_at", "entry_at", "exit_at"))
                opening, closing = _bounds(day)
                if (not opening < decision < entry < cap < closing or entry - decision != 3 * MINUTE
                        or cap - entry != 30 * MINUTE or (entry - opening) % (5 * MINUTE)
                        or decision != _instant(arm["event"]["decision_at"])
                        or _instant(label["entry_price_available_at"]) != entry + 3 * MINUTE
                        or _instant(label["label_available_at"]) != cap + 3 * MINUTE
                        or any(value.second or value.microsecond for value in (decision, entry, cap))
                        or label["required_minute_buckets"] != 31):
                    raise AcceptanceGapRefusal("source_label_clock_mismatch")
                if label["status"] != "unknown":
                    continue
                identity = {"session": day, "symbol": symbol, "entry_at": _iso(entry), "exit_at": _iso(cap)}
                request_id = hashlib.sha256(_canonical(identity)).hexdigest()
                required = [entry + offset * MINUTE for offset in range(31)]
                gaps = {minute: state for minute, state in _gaps(row["gaps"], day).items() if minute in required}
                if not gaps or label["unknown_minute_buckets"] != len(gaps):
                    raise AcceptanceGapRefusal("source_unknown_gap_count_mismatch")
                gap_counts = {state: list(gaps.values()).count(state) for state in ("missing", "conflict", "invalid_price")}
                if label["unknown_bucket_reason_counts"] != gap_counts:
                    raise AcceptanceGapRefusal("source_unknown_gap_count_mismatch")
                if label["unknown_reasons"] != [f"label_{state}_minute_buckets" for state, count in gap_counts.items() if count]:
                    raise AcceptanceGapRefusal("source_unknown_gap_reason_mismatch")
                link = {"policy": policy, "source_data_vintage": row["data_vintage"],
                    "time_cap_decided_at": _iso(decision),
                    "label_metadata": {key: deepcopy(label[key]) for key in LABEL_METADATA_FIELDS}}
                if request_id not in requests:
                    requests[request_id] = {"request_id": request_id, **identity, "policies": [], "policy_links": [],
                        "required_minute_buckets": [_iso(minute) for minute in required],
                        "original_gap_intervals": _intervals(gaps),
                        "original_missing_minute_buckets": [_iso(minute) for minute, state in sorted(gaps.items()) if state == "missing"],
                        "request_parameters": {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol[4:],
                            "FID_INPUT_DATE_1": day.replace("-", ""), "FID_INPUT_HOUR_1": cap.strftime("%H%M") + "59",
                            "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N"}}
                elif (requests[request_id]["original_gap_intervals"] != _intervals(gaps)
                        or requests[request_id]["policy_links"][0]["source_data_vintage"] != row["data_vintage"]):
                    raise AcceptanceGapRefusal("source_shared_window_mismatch")
                requests[request_id]["policies"].append(policy)
                requests[request_id]["policy_links"].append(link)
                rows[policy, request_id] = row
            counts[policy] = {"target_symbol_sessions": len(seen),
                "signal_counts": {state: signals[state] for state in ("signal", "known_no_signal", "unknown")},
                "label_counts": {state: labels[state] for state in ("known", "unknown")},
                "signal_unknown_reason_counts": dict(sorted(signal_reasons.items())),
                "label_unknown_reason_counts": dict(sorted(label_reasons.items()))}
            summary = report["variants"][policy].get("by_arm", {}).get(ARM, {})
            if any(key in summary and _canonical(summary[key]) != _canonical(value) for key, value in counts[policy].items()):
                raise AcceptanceGapRefusal("source_summary_count_mismatch")
        plan = sorted(requests.values(), key=lambda item: (item["session"], item["symbol"], item["entry_at"], item["exit_at"]))
        if len(plan) > RESOURCES["maximum_recovery_coordinates"]:
            raise AcceptanceGapRefusal("recovery_coordinate_budget_exceeded")
        source = {key: deepcopy(report[key]) for key in ("study_id", "mode", "reference_sessions", "reference_weeks", "parameters", "assumptions")}
        source["flags"] = {key: value for key, value in report.items() if type(value) is bool}
        source["variants"] = counts
        return source, plan, rows
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AcceptanceGapRefusal("malformed_source_calibration") from exc


def derive_requests(du_report: Mapping, specification: Mapping | None = None) -> list[dict]:
    """Derive only original signal/unknown-label windows, retaining policy links."""
    if specification is not None:
        validate_spec(specification)
    return _source(du_report)[1]


def _prices(row: Mapping, fields: tuple[str, ...]) -> tuple[Decimal, ...]:
    try:
        if any(not isinstance(row[key], str) for key in fields):
            raise ValueError
        prices = tuple(Decimal(row[key]) for key in fields)
        if any(not value.is_finite() or value <= 0 for value in prices):
            raise ValueError
        op, hi, lo, cl = prices
        if not lo <= min(op, cl) <= max(op, cl) <= hi:
            raise ValueError
        return prices
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise AcceptanceGapRefusal("invalid_OHLC") from exc


def _stamp(value: object) -> datetime:
    try:
        if type(value) is not int:
            raise ValueError
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise AcceptanceGapRefusal("invalid_raw_timestamp") from exc


def _source_chunk(chunk: Mapping, row: Mapping) -> tuple[dict, dict, dict]:
    fields = {"symbol", "session", "observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level", "rows"}
    if not isinstance(chunk, Mapping) or set(chunk) != fields:
        raise AcceptanceGapRefusal("source_chunk_metadata_mismatch")
    if (chunk["session"], chunk["symbol"]) != (row["session"], row["symbol"]):
        raise AcceptanceGapRefusal("source_chunk_coordinate_mismatch")
    metadata = {key: deepcopy(chunk[key]) for key in fields - {"session", "symbol", "rows"}}
    if (_canonical(metadata) != _canonical(row["raw_chunk_metadata"])
            or chunk["observation_date"] != row["session"] or chunk["available_at"] is not None
            or chunk["source"] != "collector klines" or chunk["is_final"] is not None
            or chunk["evidence_level"] != "assumed"):
        raise AcceptanceGapRefusal("source_chunk_metadata_mismatch")
    _instant(chunk["retrieved_at"])
    raw = chunk["rows"]
    if not isinstance(raw, list) or len(raw) != row["input_rows"] or len(raw) > RESOURCES["maximum_session_rows"]:
        raise AcceptanceGapRefusal("source_chunk_rows_mismatch")
    if hashlib.sha256(_canonical(chunk) + b"\n").hexdigest() != row["data_vintage"]:
        raise AcceptanceGapRefusal("source_chunk_vintage_mismatch")
    opening, closing = _bounds(row["session"])
    buckets, offsets = {}, Counter()
    for item in raw:
        if (not isinstance(item, Mapping) or set(item) != set(COLUMNS)
                or item["symbol"] != row["symbol"] or item["interval"] != "1m"
                or item["fetched_at"] is not None and not isinstance(item["fetched_at"], str)):
            raise AcceptanceGapRefusal("source_chunk_rows_mismatch")
        stamp = _stamp(item["open_time_ms"]).astimezone(KST)
        minute = stamp.replace(second=0, microsecond=0)
        if not opening <= minute < closing:
            raise AcceptanceGapRefusal("source_chunk_outside_session")
        offsets[str(item["open_time_ms"] % 60000)] += 1
        buckets.setdefault(minute, []).append(item)
    minutes, gaps = {}, {}
    minute = opening
    while minute < closing:
        group = buckets.get(minute, [])
        if len(group) > 1:
            gaps[minute] = "conflict"
        elif not group:
            gaps[minute] = "missing"
        else:
            try:
                minutes[minute] = _prices(group[0], PRICE_FIELDS)
            except AcceptanceGapRefusal:
                gaps[minute] = "invalid_price"
        minute += MINUTE
    if gaps != _gaps(row["gaps"], row["session"]):
        raise AcceptanceGapRefusal("source_chunk_gaps_mismatch")
    if _canonical(dict(offsets)) != _canonical(row["source_offset_counts_ms"]):
        raise AcceptanceGapRefusal("source_chunk_offsets_mismatch")
    return minutes, gaps, {"data_vintage": row["data_vintage"], "metadata": metadata,
        "input_rows": len(raw), "source_offset_counts_ms": dict(sorted(offsets.items(), key=lambda item: int(item[0])))}


def _wire_prices(minutes: Mapping) -> list[dict]:
    return [{"minute": _iso(minute), **dict(zip(PRICE_FIELDS, map(str, prices), strict=True))}
        for minute, prices in sorted(minutes.items())]


def _candidate(request: Mapping, original: Mapping, gaps: Mapping, buckets: Mapping,
        metadata: Mapping, reasons: list[str]) -> tuple[dict, dict]:
    required = [_instant(value) for value in request["required_minute_buckets"]]
    valid, conflicts, invalid, duplicates = {}, 0, 0, 0
    for minute, group in buckets.items():
        parsed = []
        for item in group:
            try:
                parsed.append(_prices(item, PRICE_FIELDS))
            except AcceptanceGapRefusal:
                parsed.append(None)
        if minute not in required:
            continue
        if None in parsed:
            invalid += 1
            reasons.append("invalid_OHLC")
        elif any(value != parsed[0] for value in parsed[1:]):
            conflicts += 1
            reasons.append("duplicate_minute_conflict")
        else:
            valid[minute] = parsed[0]
            duplicates += len(parsed) - 1
    overlap = sorted(set(valid) & set(original))
    mismatches = [minute for minute in overlap if valid[minute] != original[minute]]
    if mismatches:
        reasons.append("source_vintage_or_price_unit_mismatch")
    if any(state != "missing" for state in gaps.values()):
        reasons.append("original_price_defect_not_missing")
    missing = len(required) - len(valid) - conflicts - invalid
    if missing:
        reasons.append("required_window_incomplete")
    reasons = sorted(set(reasons))
    status = "complete_compatible" if not reasons else "incomplete" if reasons == ["required_window_incomplete"] else "invalid"
    result = {"status": status, "reasons": reasons,
        "metadata": {**metadata, "identical_duplicate_rows": duplicates},
        "required_bucket_counts": {"required": 31, "valid": len(valid), "missing": missing,
            "conflict": conflicts, "invalid_price": invalid},
        "overlap_checked_buckets": len(overlap), "overlap_mismatches": [_iso(minute) for minute in mismatches],
        "minutes": _wire_prices(valid)}
    return result, valid


def _db_capture(capture: Mapping, plan: list[dict]) -> dict:
    if not isinstance(capture, Mapping) or set(capture) != {"snapshot_started_at", "snapshot_finished_at", "windows", "query_count"}:
        raise AcceptanceGapRefusal("db_capture_schema_mismatch")
    started, finished = (_instant(capture[key]) for key in ("snapshot_started_at", "snapshot_finished_at"))
    windows = capture["windows"]
    if (started > finished or not isinstance(windows, list) or type(capture["query_count"]) is not int
            or capture["query_count"] != len(plan) or len(windows) != len(plan)
            or len(windows) > RESOURCES["maximum_db_queries"]):
        raise AcceptanceGapRefusal("db_capture_query_or_clock_mismatch")
    result, total = {}, 0
    for window in windows:
        if not isinstance(window, Mapping) or set(window) != {"request_id", "retrieved_at", "rows"}:
            raise AcceptanceGapRefusal("db_window_schema_mismatch")
        if window["request_id"] in result:
            raise AcceptanceGapRefusal("db_duplicate_request_id")
        retrieved = _instant(window["retrieved_at"])
        raw = window["rows"]
        if not started <= retrieved <= finished:
            raise AcceptanceGapRefusal("db_capture_retrieval_clock_mismatch")
        if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_db_rows_per_window"]:
            raise AcceptanceGapRefusal("db_capture_row_budget_exceeded")
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != set(COLUMNS):
                raise AcceptanceGapRefusal("db_row_schema_mismatch")
            _stamp(item["open_time_ms"])
            if item["fetched_at"] is not None and _instant(item["fetched_at"]) > retrieved:
                raise AcceptanceGapRefusal("db_fetched_after_retrieval")
        total += len(raw)
        result[window["request_id"]] = window
    if total > RESOURCES["maximum_db_rows"] or set(result) != {item["request_id"] for item in plan}:
        raise AcceptanceGapRefusal("db_capture_population_or_row_budget_mismatch")
    return result


def _db_minutes(request: Mapping, window: Mapping) -> tuple[dict, dict, list[str]]:
    retrieved = _instant(window["retrieved_at"])
    if retrieved < _instant(request["exit_at"]) + 3 * MINUTE:
        raise AcceptanceGapRefusal("db_capture_before_label_availability")
    entry, cap = _instant(request["entry_at"]), _instant(request["exit_at"])
    buckets, offsets, reasons = {}, Counter(), []
    for item in window["rows"]:
        if item["symbol"] != request["symbol"] or item["interval"] != "1m":
            raise AcceptanceGapRefusal("db_row_identity_mismatch")
        stamp = _stamp(item["open_time_ms"]).astimezone(KST)
        minute = stamp.replace(second=0, microsecond=0)
        if not entry <= minute <= cap:
            raise AcceptanceGapRefusal("db_row_outside_registered_window")
        offsets[str(item["open_time_ms"] % 60000)] += 1
        buckets.setdefault(minute, []).append(item)
    return buckets, {"response_rows": len(window["rows"]), "retrieved_at": window["retrieved_at"],
        "source_offset_counts_ms": dict(sorted(offsets.items(), key=lambda item: int(item[0])))}, reasons


def _empty_candidate(status: str, reason: str, metadata: Mapping | None = None) -> dict:
    return {"status": status, "reasons": [reason], "metadata": dict(metadata or {}),
        "required_bucket_counts": {"required": 31, "valid": 0, "missing": 31, "conflict": 0, "invalid_price": 0},
        "overlap_checked_buckets": 0, "overlap_mismatches": [], "minutes": []}


def _api_candidate(request: Mapping, envelope: object, original: Mapping, gaps: Mapping) -> tuple[dict, dict]:
    if envelope is None:
        return _empty_candidate("unprobed", "not_supplied"), {}
    try:
        if (not isinstance(envelope, Mapping) or set(envelope) - {"requested_at", "retrieved_at", "status", "response", "reason"}
                or not {"requested_at", "retrieved_at", "status", "response"} <= set(envelope)):
            raise AcceptanceGapRefusal("api_envelope_schema_mismatch")
        requested, retrieved = (_instant(envelope[key]) for key in ("requested_at", "retrieved_at"))
        if requested > retrieved or requested < _instant(request["exit_at"]) + 3 * MINUTE:
            raise AcceptanceGapRefusal("api_retrieval_clock_mismatch")
        today = requested.date()
        try:
            cutoff = today.replace(year=today.year - 1)
        except ValueError:
            cutoff = today.replace(year=today.year - 1, day=28)
        metadata = {"requested_at": envelope["requested_at"], "retrieved_at": envelope["retrieved_at"], "retention_cutoff": cutoff.isoformat()}
        eligible = cutoff <= _day(request["session"]) <= today
        if not eligible:
            if envelope["status"] != "unprobed" or envelope["response"] is not None or envelope.get("reason") != "outside_documented_retention_horizon":
                raise AcceptanceGapRefusal("api_probed_outside_documented_retention_horizon")
            return _empty_candidate("unprobed", "outside_documented_retention_horizon", metadata), {}
        if envelope["status"] == "unavailable" and envelope["response"] is None:
            reason = envelope.get("reason", "api_unavailable")
            allowed = (reason in {"authentication_unavailable_no_retry", "market_response_unavailable_no_retry"}
                if isinstance(reason, str) else False)
            allowed = allowed or isinstance(reason, str) and bool(re.fullmatch(r"market_http_[0-9]{3}", reason))
            metadata["unavailable_reason"] = reason if allowed else "invalid_unavailable_reason"
            return _empty_candidate("unavailable", "api_unavailable", metadata), {}
        payload = envelope["response"]
        if (envelope["status"] != "received" or not isinstance(payload, Mapping)
                or set(payload) != {"rt_cd", "msg_cd", "output2"}
                or payload["rt_cd"] != "0" or not isinstance(payload["msg_cd"], str)):
            raise AcceptanceGapRefusal("api_response_unsuccessful_or_schema_mismatch")
        raw = payload["output2"]
        if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_rows_per_response"]:
            raise AcceptanceGapRefusal("api_response_row_budget_exceeded")
        buckets, offsets, reasons = {}, Counter(), []
        prior, preopen, before_entry = 0, 0, 0
        opening, _ = _bounds(request["session"])
        entry, cap = _instant(request["entry_at"]), _instant(request["exit_at"])
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != set(API_PRICE_FIELDS) | {"stck_bsop_date", "stck_cntg_hour"}:
                raise AcceptanceGapRefusal("api_row_schema_mismatch")
            business_date, clock = item["stck_bsop_date"], item["stck_cntg_hour"]
            if not isinstance(business_date, str) or not re.fullmatch(r"[0-9]{8}", business_date):
                raise AcceptanceGapRefusal("api_row_date_mismatch")
            try:
                parsed_day = datetime.strptime(business_date, "%Y%m%d").date()
            except ValueError as exc:
                raise AcceptanceGapRefusal("api_row_date_mismatch") from exc
            if not isinstance(clock, str) or not re.fullmatch(r"[0-9]{6}", clock):
                raise AcceptanceGapRefusal("api_row_minute_clock_mismatch")
            try:
                stamp = datetime.strptime(business_date + clock, "%Y%m%d%H%M%S").replace(tzinfo=KST)
            except ValueError as exc:
                raise AcceptanceGapRefusal("api_row_minute_clock_mismatch") from exc
            minute = stamp.replace(second=0)
            offsets[str(stamp.second)] += 1
            if parsed_day < _day(request["session"]):
                prior += 1
                continue
            if parsed_day > _day(request["session"]):
                raise AcceptanceGapRefusal("api_row_date_mismatch")
            if minute < opening:
                preopen += 1
                continue
            if minute > cap:
                raise AcceptanceGapRefusal("api_row_outside_requested_session_or_exit")
            if minute < entry:
                before_entry += 1
            buckets.setdefault(minute, []).append(dict(zip(PRICE_FIELDS, (item[key] for key in API_PRICE_FIELDS), strict=True)))
        metadata.update(response_rows=len(raw), unique_minute_buckets=len(buckets), discarded_prior_date_rows=prior,
            discarded_preopen_rows=preopen, before_entry_rows=before_entry,
            source_offset_counts_seconds=dict(sorted(offsets.items(), key=lambda item: int(item[0]))))
        return _candidate(request, original, gaps, buckets, metadata, reasons)
    except AcceptanceGapRefusal as exc:
        return _empty_candidate("invalid", str(exc)), {}


def build_diagnostics(du_report: Mapping, source_chunks: Mapping, db_capture: Mapping,
        responses: Mapping, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Validate each vintage separately without modifying original DU labels.

    Missing response keys support the caller's DB-first planning pass; a final
    caller must require every remaining eligible probe or explicit failure.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    source, plan, rows = _source(du_report)
    coordinates = {(request["session"], request["symbol"]) for request in plan}
    if not isinstance(source_chunks, Mapping) or set(source_chunks) != coordinates:
        raise AcceptanceGapRefusal("source_chunk_population_mismatch")
    if not isinstance(responses, Mapping) or set(responses) - {request["request_id"] for request in plan}:
        raise AcceptanceGapRefusal("api_response_population_mismatch")
    windows = _db_capture(db_capture, plan)
    diagnostics, market_gets = [], 0
    authenticated = {}
    for request in plan:
        checkpoint()
        request_id, key = request["request_id"], (request["session"], request["symbol"])
        row = rows[request["policies"][0], request_id]
        if key not in authenticated:
            authenticated[key] = _source_chunk(source_chunks[key], row)
        original_all, gaps_all, metadata = authenticated[key]
        # Different policy windows may share one chunk, but must each name it.
        for policy in request["policies"]:
            linked = rows[policy, request_id]
            if (linked["data_vintage"] != metadata["data_vintage"]
                    or linked["input_rows"] != metadata["input_rows"]
                    or _canonical(linked["raw_chunk_metadata"]) != _canonical(metadata["metadata"])
                    or _canonical(linked["source_offset_counts_ms"]) != _canonical(metadata["source_offset_counts_ms"])
                    or _gaps(linked["gaps"], key[0]) != gaps_all):
                raise AcceptanceGapRefusal("source_shared_chunk_mismatch")
        required = {_instant(value) for value in request["required_minute_buckets"]}
        original = {minute: prices for minute, prices in original_all.items() if minute in required}
        gaps = {minute: state for minute, state in gaps_all.items() if minute in required}
        missing = [_iso(minute) for minute, state in sorted(gaps.items()) if state == "missing"]
        if missing != request["original_missing_minute_buckets"]:
            raise AcceptanceGapRefusal("source_missing_window_mismatch")
        for policy in request["policies"]:
            label = rows[policy, request_id]["arms"][ARM]["label"]
            for name, clock in (("entry_open", "entry_at"), ("exit_open", "exit_at")):
                effective = original.get(_instant(request[clock]))
                endpoint = label[name]
                if effective is None:
                    if endpoint is not None:
                        raise AcceptanceGapRefusal("source_label_endpoint_mismatch")
                else:
                    try:
                        if (not isinstance(endpoint, (str, Decimal)) or not Decimal(endpoint).is_finite()
                                or Decimal(endpoint) != effective[0]):
                            raise ValueError
                    except (ValueError, TypeError, InvalidOperation) as exc:
                        raise AcceptanceGapRefusal("source_label_endpoint_mismatch") from exc
        buckets, db_metadata, reasons = _db_minutes(request, windows[request_id])
        db, db_prices = _candidate(request, original, gaps, buckets, db_metadata, reasons)
        if db["status"] == "complete_compatible":
            if request_id in responses:
                raise AcceptanceGapRefusal("api_probe_after_complete_compatible_db")
            api, api_prices = _empty_candidate("unprobed", "db_complete_compatible"), {}
        else:
            envelope = responses.get(request_id)
            if isinstance(envelope, Mapping) and envelope.get("status") in {"received", "unavailable"}:
                # Authentication failure precedes a GET; all other unavailable
                # reasons conservatively consume an attempt, never hide one.
                if not (envelope.get("status") == "unavailable"
                        and envelope.get("reason") == "authentication_unavailable_no_retry"):
                    market_gets += 1
            api, api_prices = _api_candidate(request, envelope, original, gaps)
        conflicts = []
        for minute in sorted(required):
            available = {name: prices[minute] for name, prices in (("original", original), ("db", db_prices), ("api", api_prices)) if minute in prices}
            if len(set(available.values())) > 1:
                conflicts.append({"minute": _iso(minute), "sources": sorted(available)})
        candidates = []
        for name, candidate, prices in (("db", db, db_prices), ("api", api, api_prices)):
            if candidate["status"] == "complete_compatible":
                candidates.append({"source": name, "minutes": candidate["minutes"],
                    "missing_only_additions": _wire_prices({minute: prices[minute] for minute, state in gaps.items() if state == "missing"})})
        usable = (bool(candidates) and not conflicts and all(state == "missing" for state in gaps.values())
            and all("duplicate_minute_conflict" not in candidate["reasons"] for candidate in (db, api)))
        diagnostics.append({"request_id": request_id,
            "original": {**metadata, "gap_counts": {state: list(gaps.values()).count(state) for state in ("missing", "conflict", "invalid_price")}, "missing_minute_buckets": missing},
            "db": db, "api": api, "cross_source_conflicts": conflicts,
            "status": "verified_price_candidate" if usable else "unresolved",
            "verified_complete_candidates": candidates,
            "missing_only_additions": candidates[0]["missing_only_additions"] if usable else [],
            "source_rows_overwritten": 0})
    if market_gets > RESOURCES["maximum_market_gets"]:
        raise AcceptanceGapRefusal("market_get_budget_exceeded")
    verified = sum(item["status"] == "verified_price_candidate" for item in diagnostics)
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "source_roles": dict(SOURCE_ROLES), "source": source, "requests": plan, "diagnostics": diagnostics,
        "summary": {"request_windows": len(plan),
            "policy_unknown_outcomes": {policy: source["variants"][policy]["label_counts"]["unknown"] for policy in POLICIES},
            "verified_price_candidate_windows": verified, "unresolved_windows": len(plan) - verified,
            "db_queries": db_capture["query_count"], "market_gets": market_gets},
        "source_rows_overwritten": 0, **FALSE_CLAIMS}
