"""Bounded volume/value input readiness, without signals or price outcomes.

The caller owns durable registration, source pins and the single shared budget.
Only original CB coordinates may be read; missing baseline coordinates remain
unknown. Current retrieval and modeled historical availability are distinct.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from research import intraday_opening_range_calibration as ct

cc = ct.cc
_canonical = cc._canonical
SPECIFICATION_ID = "intraday-activity-readiness-v1"
MODE = "original-target-minute-volume-value-readiness-only"
WINDOW = dict(cc.WINDOW)
UPSTREAM_SHA256 = ct.UPSTREAM_SHA256
INPUT_COMPRESSED_SHA256 = ct.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = ct.INPUT_STREAM_SHA256
COLUMNS = ("open_time_ms", "volume", "quote_volume", "fetched_at")
REGISTERED_PATHS = {**ct.REGISTERED_PATHS,
    "source_database": cc.REGISTERED_PATHS["source_database"],
    "output_directory": "/home/minjun4897/research-evidence/intraday-cx-activity-readiness-20261023-v1"}
RESOURCES = {**ct.RESOURCES, "maximum_unique_symbols": 260,
    "maximum_target_days": 6000, "maximum_query_seconds": 10,
    "maximum_query_vm_steps": 5_000_000, "maximum_typed_chunk_bytes": 262144,
    "maximum_typed_uncompressed_bytes": 1_073_741_824,
    "maximum_daily_rows_per_target": 1, "maximum_queries": 12000}
NEXT_HYPOTHESIS = {
    "id": "OR15-initial15-volume-versus-prior20-same-clock-median-ge2-v1",
    "base": "original_CT_unfiltered_OR15_first_signal_only",
    "membership_timing_policies": ["D+1", "D+2"],
    "opening_range_minutes": 15, "decision_cutoff_minutes": 240,
    "maximum_signals_per_name_session": 1,
    "entry_wait_minutes_after_assumed_signal_availability": 3,
    "exit": "entry_plus30minute_cap_minute_open", "label_minutes": 30,
    "assumed_round_trip_cost_bps": "33", "volume_ratio_threshold": "2",
    "condition": "initial15_volume_greater_or_equal_2_times_prior20_same_wall_clock_median",
    "baseline": "all_previous20_original_reference_sessions_same_symbol_first15minutes_same_opening_HHMM_required",
    "baseline_scope": "original_CB_target_coordinate_union_only_no_added_coordinates",
    "required": "all15_exact_unique_valid_nonnegative_volume_buckets_each_window_and_positive_exact_baseline_median",
    "availability": "each_completed_minute_end_plus2minutes_assumed_not_fetched_at",
    "missingness": "unknown_if_any_prior_session_or_coordinate_or_bucket_missing_invalid_conflicting_or_opening_HHMM_differs",
    "price_impulse_conjunct": False, "parameter_search": False,
    "signal_selection_performed": False, "performance_calculated": False,
    "next_gate": "separate_registered_feature_frequency_and_own_label_sizing_before_any_comparison",
}
PARAMETERS = {
    "coordinates": "original_CB_D+1_D+2_dated_top20_union_only",
    "prefix_minutes": [15, 30], "baseline_sessions": 20,
    "minute_bucket": "floor_original_milliseconds_to_minute_preserve_offsets_and_conflicts",
    "valid_volume": "finite_exact_text_decimal_nonnegative_zero_is_observed_not_imputed",
    "value_semantics": "quote_volume_only_no_cumulative_acml_tr_pbmn_conversion_or_price_times_volume",
    "comparison": "saved_CC_and_current_DB_minute_bucket_sets_only_no_OHLC_or_returns",
    "daily_scope": "same_original_target_coordinates_only_1d_volume_quote_volume_fetched_at_max1row_no_prices",
    "daily_value": "daily_quote_volume_never_substitutes_for_missing_minute_value",
    "recovery_horizon": "as_of2026-10-23_date_before2025-10-23_outside_documented_one_year_upper_horizon_otherwise_unprobed_no_API_requests",
    "snapshot": "one_readonly_transaction_bounded_by_shared_budget_no_DB_copy_or_live_file_hash",
}
FLAGS = {**cc.FLAGS, "actual_portfolio_pnl": False, "signal_selection_performed": False,
    "performance_calculated": False, "new_performance_families": 0,
    "readiness_only": True, "historical_volume_availability_certified": False,
    "historical_value_availability_certified": False}
STATES = ("absent_column", "null", "invalid", "negative", "zero", "nonzero")
CHUNK_KEYS = {"symbol", "session", "observation_date", "available_at", "retrieved_at",
              "source", "is_final", "evidence_level", "rows"}


class ReadinessRefusal(RuntimeError):
    """Stable error codes avoid disclosing private values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "next_hypothesis": NEXT_HYPOTHESIS,
        "allowed_columns": list(COLUMNS), "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise ReadinessRefusal("specification_scope_mismatch")


def number_state(value: object, present: bool = True) -> str:
    if not present:
        return "absent_column"
    if value is None:
        return "null"
    if not isinstance(value, str) or len(value) > 256:
        return "invalid"
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return "invalid"
    # Bound pathological exponents before exact Fraction conversion.
    if not parsed.is_finite() or abs(parsed.as_tuple().exponent) > 100:
        return "invalid"
    return "negative" if parsed < 0 else "zero" if parsed == 0 else "nonzero"


def inventory_schema(connection: sqlite3.Connection) -> dict:
    """Inventory only the named table; never enumerate other database objects."""
    row = connection.execute(
        "SELECT type,sql FROM main.sqlite_schema WHERE name=?", ("klines",)).fetchone()
    if row is None or row[0] != "table":
        raise ReadinessRefusal("klines_regular_table_required")
    columns = [dict(zip(("cid", "name", "type", "notnull", "default", "pk"), item, strict=True))
               for item in connection.execute("PRAGMA main.table_info('klines')")]
    names = {item["name"] for item in columns}
    keys = {item["name"]: item["pk"] for item in columns if item["pk"]}
    if not {"symbol", "interval", "open_time_ms", "fetched_at"} <= names or keys != {
            "symbol": 1, "interval": 2, "open_time_ms": 3}:
        raise ReadinessRefusal("klines_coordinate_schema_mismatch")
    return {"table": "klines", "sql": row[1], "columns": columns,
            "field_presence": {name: name in names for name in ("volume", "quote_volume")}}


def authorize(action, table, column, database, source):
    allowed = action == sqlite3.SQLITE_SELECT or (action == sqlite3.SQLITE_READ
        and table == "klines" and database == "main" and source is None
        and column in {"symbol", "interval", *COLUMNS})
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def read_target(connection: sqlite3.Connection, symbol: str, day: str,
                allowed_coordinates: set[tuple[str, str]], schema: Mapping, *, interval: str = "1m") -> dict:
    """One parameterized per-target query, inside the caller's short snapshot."""
    if ((day, symbol) not in allowed_coordinates or not WINDOW["start"] <= day <= WINDOW["end"]
            or interval not in {"1m", "1d"}):
        raise ReadinessRefusal("unregistered_target_coordinate")
    start, end = (int(clock.timestamp()) * 1000 for clock in (cc.session_open(day), cc.session_close(day)))
    if interval == "1d":
        start = int(datetime.fromisoformat(day + "T00:00:00+00:00").timestamp()) * 1000
        end = start + 1
    deadline, steps = time.monotonic() + RESOURCES["maximum_query_seconds"], 0
    def progress():
        nonlocal steps
        steps += 1000
        return int(steps >= RESOURCES["maximum_query_vm_steps"] or time.monotonic() >= deadline)
    connection.set_progress_handler(progress, 1000)
    fields = [name if name not in schema["field_presence"] or schema["field_presence"][name]
              else f"NULL AS {name}" for name in COLUMNS]
    cursor = None
    try:
        cursor = connection.execute("SELECT " + ",".join(fields)
            + " FROM main.klines WHERE symbol=? AND interval=? AND open_time_ms>=? AND open_time_ms<?"
            + (" ORDER BY open_time_ms LIMIT 601" if interval == "1m" else " ORDER BY open_time_ms LIMIT 2"),
            (symbol, interval, start, end))
        rows = [dict(zip(COLUMNS, row, strict=True)) for row in cursor]
        if len(rows) > (RESOURCES["maximum_session_rows"] if interval == "1m" else 1):
            raise ReadinessRefusal("session_row_budget_exceeded")
        return {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "source": "collector klines",
            "is_final": None, "evidence_level": "assumed", "rows": rows}
    finally:
        if cursor is not None:
            cursor.close()
        connection.set_progress_handler(None, 0)


def typed_chunks(path: str | Path, checkpoint: Callable[[], None]) -> Iterable[dict]:
    size, count = 0, 0
    with gzip.open(path, "rb") as stream:
        while True:
            checkpoint()
            line = stream.readline(RESOURCES["maximum_typed_chunk_bytes"] + 1)
            if not line:
                break
            size += len(line)
            count += 1
            if (not line.endswith(b"\n") or len(line) > RESOURCES["maximum_typed_chunk_bytes"]
                    or size > RESOURCES["maximum_typed_uncompressed_bytes"] or count > 6000):
                raise ReadinessRefusal("typed_archive_budget_or_framing_mismatch")
            chunk = json.loads(line)
            if line != _canonical(chunk) + b"\n":
                raise ReadinessRefusal("noncanonical_typed_chunk")
            yield chunk


def _validated(chunk, day, symbol, columns):
    current = tuple(columns) == COLUMNS
    if (not isinstance(chunk, Mapping) or set(chunk) != CHUNK_KEYS | ({"daily_rows", "daily_retrieved_at"} if current else set())
            or chunk["symbol"] != symbol or chunk["session"] != day
            or chunk["observation_date"] != day or chunk["available_at"] is not None
            or chunk["source"] != "collector klines" or chunk["is_final"] is not None
            or chunk["evidence_level"] != "assumed"):
        raise ReadinessRefusal("chunk_identity_or_metadata_mismatch")
    ct._instant(chunk["retrieved_at"])
    if current:
        ct._instant(chunk["daily_retrieved_at"])
    rows = chunk["rows"]
    if not isinstance(rows, list) or len(rows) > 600:
        raise ReadinessRefusal("chunk_row_budget_exceeded")
    start, end = (int(clock.timestamp()) * 1000 for clock in (cc.session_open(day), cc.session_close(day)))
    for row in rows:
        if (not isinstance(row, Mapping) or set(row) != set(columns)
                or type(row["open_time_ms"]) is not int or not start <= row["open_time_ms"] < end
                or row["fetched_at"] is not None and not isinstance(row["fetched_at"], str)
                or "symbol" in row and (row["symbol"] != symbol or row["interval"] != "1m")):
            raise ReadinessRefusal("row_scope_or_storage_type_mismatch")
        if any(type(value) is float for value in row.values()):
            raise ReadinessRefusal("lossy_float_input_forbidden")
    return rows


def _window(buckets, day, clock, minutes, present):
    start = datetime.fromisoformat(day + "T" + clock + ":00+09:00")
    required = [int((start + timedelta(minutes=index)).timestamp()) * 1000 for index in range(minutes)]
    missing = sum(stamp not in buckets for stamp in required)
    conflict = sum(len(buckets.get(stamp, ())) > 1 for stamp in required)
    invalid = sum(len(buckets.get(stamp, ())) == 1
        and number_state(buckets[stamp][0]["volume"], present) not in {"zero", "nonzero"} for stamp in required)
    complete = not (missing or conflict or invalid)
    total = sum((Fraction(Decimal(buckets[stamp][0]["volume"])) for stamp in required), Fraction()) if complete else None
    return {"state": "ready" if complete else "unknown", "required_buckets": minutes,
            "missing_buckets": missing, "conflicting_buckets": conflict, "invalid_volume_buckets": invalid,
            "volume_sum": total}


def build_readiness(cb_report: Mapping, saved_chunks: Iterable[Mapping], current_chunks: Iterable[Mapping],
                    schema: Mapping, *, checkpoint: Callable[[], None] | None = None) -> dict:
    """Only readiness: no OR signal, threshold selection, labels, means or sizing."""
    checkpoint = checkpoint or (lambda: None)
    matrix, union = cc._target_matrix(cb_report)
    if cb_report.get("mode") != "discovery-universe-source-and-coverage-only":
        raise ReadinessRefusal("wrong_upstream_mode")
    reference = list(union)
    saved, current = iter(saved_chunks), iter(current_chunks)
    records, index, saved_rows, current_rows, daily_rows = [], {}, 0, 0, 0
    field_total = {field: Counter() for field in ("volume", "quote_volume")}
    daily_total = {field: Counter() for field in field_total}
    comparison_total = Counter()
    for day in reference:
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                old, new = next(saved), next(current)
            except StopIteration as exc:
                raise ReadinessRefusal("missing_target_chunk") from exc
            oldrows, rows = _validated(old, day, symbol, cc.COLUMNS), _validated(new, day, symbol, COLUMNS)
            daily = new["daily_rows"]
            daily_stamp = int(datetime.fromisoformat(day + "T00:00:00+00:00").timestamp()) * 1000
            if not isinstance(daily, list) or len(daily) > 1 or any(
                    set(row) != set(COLUMNS) or type(row["open_time_ms"]) is not int
                    or row["open_time_ms"] != daily_stamp
                    or row["fetched_at"] is not None and not isinstance(row["fetched_at"], str)
                    or any(type(value) is float for value in row.values()) for row in daily):
                raise ReadinessRefusal("daily_row_scope_or_type_mismatch")
            daily_rows += len(daily)
            saved_rows += len(oldrows)
            current_rows += len(rows)
            if max(saved_rows, current_rows) > RESOURCES["maximum_rows"]:
                raise ReadinessRefusal("total_row_budget_exceeded")
            oldset = {row["open_time_ms"] // 60000 * 60000 for row in oldrows}
            buckets = {}
            for row in rows:
                buckets.setdefault(row["open_time_ms"] // 60000 * 60000, []).append(row)
            newset = set(buckets)
            comparison = {"saved_unique_buckets": len(oldset), "current_unique_buckets": len(newset),
                "both": len(oldset & newset), "saved_only": len(oldset - newset), "current_only": len(newset - oldset)}
            comparison_total.update(comparison)
            counts = {field: {state: 0 for state in STATES} for field in field_total}
            for field in counts:
                counts[field].update(Counter(number_state(row[field], schema["field_presence"][field]) for row in rows))
                field_total[field].update(counts[field])
            daily_counts = {field: {state: sum(number_state(row[field], schema["field_presence"][field]) == state
                for row in daily) for state in STATES} for field in field_total}
            for field, values in daily_counts.items():
                daily_total[field].update(values)
            profiles = {f"{clock}/{minutes}": _window(buckets, day, clock, minutes, schema["field_presence"]["volume"])
                        for clock in ("09:00", "10:00") for minutes in (15, 30)}
            clock = cc.session_open(day).strftime("%H:%M")
            record = {"symbol": symbol, "session": day, "current_rows": len(rows), "saved_rows": len(oldrows),
                "absence": "no_rows_unknown_cause" if not rows else "rows_observed",
                "recovery_horizon": "outside_documented_upper_horizon" if day < "2025-10-23" else "within_documented_upper_horizon_but_unprobed",
                "saved_archive_activity_fields": "omitted_not_evidence_of_DB_absence",
                "field_counts": counts, "bucket_comparison": comparison,
                "daily_rows": len(daily), "daily_field_counts": daily_counts,
                "daily_source_fetched_at": [row["fetched_at"] for row in daily],
                "daily_retrieved_at": new["daily_retrieved_at"],
                "daily_rows_sha256": hashlib.sha256(_canonical(daily)).hexdigest(),
                "source_offset_counts_ms": dict(sorted(Counter(row["open_time_ms"] % 60000 for row in rows).items())),
                "conflicting_buckets": sum(len(values) > 1 for values in buckets.values()),
                "saved_chunk_sha256": hashlib.sha256(_canonical(old) + b"\n").hexdigest(),
                "current_chunk_sha256": hashlib.sha256(_canonical(new) + b"\n").hexdigest(),
                "current_rows_sha256": hashlib.sha256(_canonical(rows)).hexdigest(),
                "raw_chunk_metadata": {key: deepcopy(new[key]) for key in CHUNK_KEYS - {"rows", "symbol", "session"}},
                "source_fetched_at": sorted({row["fetched_at"] for row in rows if row["fetched_at"] is not None}),
                "same_clock_windows": profiles,
                "opening_prefixes": {str(n): deepcopy(profiles[f"{clock}/{n}"]) for n in (15, 30)}}
            records.append(record)
            index[day, symbol] = record
    for chunks in (saved, current):
        try:
            next(chunks)
        except StopIteration:
            pass
        else:
            raise ReadinessRefusal("extra_target_chunk")
    for record in records:
        day, symbol = record["session"], record["symbol"]
        position = reference.index(day)
        prior = reference[max(0, position - 20):position]
        clock = cc.session_open(day).strftime("%H:%M")
        windows = [index.get((past, symbol), {}).get("same_clock_windows", {}).get(f"{clock}/15") for past in prior]
        missing_coords = sum(value is None for value in windows)
        invalid_windows = sum(value is not None and value["state"] != "ready" for value in windows)
        clock_mismatches = sum(cc.session_open(past).strftime("%H:%M") != clock for past in prior)
        positive_median = None
        if len(prior) == 20 and not (missing_coords or invalid_windows or clock_mismatches):
            values = sorted(value["volume_sum"] for value in windows)
            positive_median = (values[9] + values[10]) > 0
        ready = record["opening_prefixes"]["15"]["state"] == "ready" and positive_median is True
        record["next_hypothesis_feature_readiness"] = {"state": "ready" if ready else "unknown",
            "same_wall_clock": clock, "required_prior_sessions": 20, "available_prior_reference_sessions": len(prior),
            "missing_target_coordinates": missing_coords, "invalid_prior_windows": invalid_windows,
            "prior_opening_clock_mismatches": clock_mismatches,
            "positive_exact_baseline_median": positive_median,
            "ratio_or_threshold_evaluated": False}
    variants = {}
    for policy, source in matrix.items():
        days, summaries = [], Counter()
        for membership in source["days"]:
            day = membership["session"]
            targets = []
            for target in membership["targets"]:
                record = index[day, target["symbol"]]
                state = record["next_hypothesis_feature_readiness"]["state"]
                summaries[state] += 1
                targets.append({"symbol": target["symbol"], "rank": target["rank"],
                    "feature_readiness": state,
                    "opening_prefixes": {arm: record["opening_prefixes"][arm]["state"] for arm in ("15", "30")}})
            days.append({"session": day, "membership_source": deepcopy({k: v for k, v in membership.items() if k != "targets"}),
                         "targets": targets})
        variants[policy] = {"days": days, "feature_readiness_counts": {state: summaries[state] for state in ("ready", "unknown")}}
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "next_hypothesis": NEXT_HYPOTHESIS, **FLAGS, "schema_inventory": deepcopy(schema),
        "reference_sessions": reference, "source_target_matrix": deepcopy(cb_report["variants"]),
        "upstream_calendar": deepcopy(cb_report["calendar"]), "variants": variants, "target_days": records,
        "summary": {"unique_target_days": len(records), "saved_rows": saved_rows, "current_rows": current_rows,
            "daily_rows": daily_rows, "daily_field_counts": {key: dict(value) for key, value in daily_total.items()},
            "saved_empty_current_found": sum(r["saved_rows"] == 0 and r["current_rows"] > 0 for r in records),
            "saved_empty_current_empty": sum(r["saved_rows"] == 0 and r["current_rows"] == 0 for r in records),
            "saved_empty_current_empty_by_recovery_horizon": dict(sorted(Counter(r["recovery_horizon"] for r in records
                if r["saved_rows"] == 0 and r["current_rows"] == 0).items())),
            "field_counts": {key: dict(value) for key, value in field_total.items()},
            "bucket_comparison": dict(comparison_total),
            "opening_prefix_ready_counts": {str(n): sum(r["opening_prefixes"][str(n)]["state"] == "ready" for r in records) for n in (15, 30)},
            "feature_ready_union": sum(r["next_hypothesis_feature_readiness"]["state"] == "ready" for r in records)},
        "availability": {"minute_model": "bucket_end_plus2minutes_assumed", "historical_available_at": None,
            "retrieved_at": "per_current_chunk_query_receipt", "fetched_at": "stored_collector_retrieval_not_historical_availability",
            "membership": "original_D+1_D+2_membership_source_preserved", "is_final": None,
            "absence_cause": "unknown_no_halt_delisting_or_listing_inference"}}
