"""One logged, cached KRX raw / stored KIS scan parity diagnostic.

Only the registered original-issue union's cached intersection is consumed.
Exact source strings and SQLite storage types survive in private row audits;
no return, action application, selection, eligibility or basis certificate is
produced. The new scan fingerprint must never replace or be compared with BI's
failure-input fingerprint as evidence of an unchanged research snapshot.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import sqlite3
import stat

from research import activity_preflight as saved
from research import activity_scope_preflight as scope_source
from research import experiment_log
from research.activity_failure_inputs import _progress, _scan_date, _typed
from research.activity_timing import SessionLagPolicy


SCHEMA = "activity-cached-price-parity-v1"
READ_COLUMNS = ("bsop_date", "open", "high", "low", "close", "volume", "turnover")
FIELDS = READ_COLUMNS[1:]
RAW_FIELDS = ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL", "ACC_TRDVAL")
INPUTS = {
    "scope_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bj-price-action-scope-20261009-v1/run/result.json",
        "sha256": "69b6829903cb03fdb835ee084a91d06941cb4c8ce5048ea75189a298c63109ea"},
    "aq": {
        "root": "/home/minjun4897/research-evidence/krx-formations-20261007-v2/run",
        "report_sha256": "17ae2a87663ed592c2968b9ce051f4fa248574a6d70561e704e7b5a27c8c593b",
        "started_sha256": "2bce0c4b916a1f67415e91dd1b4372ae8425e518d98624661725c58407ee48e9",
        "ledger_sha256": "f85c8a4403eaacde5d6fd0195054568262cbbb4cdab98ffdc846ab45933f2487"},
    "ar_original": {
        "root": "/home/minjun4897/research-evidence/krx-liquidity-20261007-v1/run",
        "report_sha256": "53effb672a849084148511be55cda1e92e90d181d0d6ee47291545ecdf12b612",
        "started_sha256": "34b0abbd41bd360a9309114b6cdb34dd30516bff747586d7190cc6d8acb0a79f",
        "ledger_sha256": "8b2c2367f1ac6e0d3ca5d8f07dd2cf9380bb99a593b0fd769d5e8495fb8da622"},
    "ar_recovery": {
        "root": "/home/minjun4897/research-evidence/krx-liquidity-recovery-20261007-v1/run",
        "report_sha256": "f23e37b0b6613a4720c21949b63f8c3ba1ba0a6c97546478b9abae522ca983ad",
        "started_sha256": "e7a3f7290abc6e7541a1ff005c39e0ec07a1e4d99bfcd64b182b7cb404141e27",
        "ledger_sha256": "2f86c7af7db76f2fcf6410bcef477eef7b5e25973db87b4e4a8ff13f39e34518"},
    "scan_database": {
        "path": "/home/minjun4897/trading-engine/python/data/var/krx_scan.sqlite3",
        "mode": "ro", "columns": list(READ_COLUMNS)},
}
COUNTS = {"potential_windows": 36, "potential_codes": 31,
          "required_unique_code_dates": 24598, "prior_windows": 917,
          "bh_failure_windows": 41, "bi_failure_windows": 840,
          "target_codes": 136, "retained_controls": 27, "cached_dates": 854}


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(isinstance(spec, dict) and spec.get("schema") == SCHEMA
                  and spec.get("study_id") == SCHEMA
                  and spec.get("mode") == "discovery_cached_price_parity"
                  and spec.get("promotion_allowed") is False
                  and spec.get("reference_specification") == saved.REFERENCE
                  and spec.get("parameters") == reference["parameters"]
                  and spec.get("window") == saved.WINDOW and spec.get("inputs") == INPUTS,
                  "fixed cached-price parity specification mismatch")


def plan_scope(scope: dict, params: dict, *, counts: dict = COUNTS) -> dict:
    """Validate the original complete union, then intersect fixed cache geometry.

    Explicit small counts/parameters are a pure synthetic-test seam. Production
    validates the pinned calendar digest and all fourteen fixed formations.
    """
    saved.require(scope.get("schema") == scope_source.SCHEMA
                  and scope.get("status") == "conservative_original_issue_scope_planned"
                  and scope.get("scope_kind") == "upper_bound_for_BI_potential_subset_only"
                  and scope.get("interval_policy") == (
                      "every index session from possible entry through fixed evaluation end inclusive")
                  and scope.get("evaluation_end") == params["end"]
                  and all(scope.get(key) is False for key in (
                      "historical_eligibility_certified", "holding_selection_executed", "returns_computed",
                      "promotion_allowed", "database_access", "baseline_formation_price_basis_certified")),
                  "BJ original-issue scope receipt mismatch")
    calendar = scope["calendar_dates"]
    days, end, schedule = scope_source._paired_schedule(calendar, params)
    saved.require(scope.get("paired_schedule") == schedule, "BJ paired schedule differs from calendar")
    if counts is COUNTS:
        saved.calendar_schedule({"calendar_dates": calendar,
                                 "formation_dates": [row["formation"] for row in schedule]}, params)
    formations = {row["formation"]: row["arms"] for row in schedule}
    possible = scope_source._keyed(scope["possible_windows"], set(formations), "BJ potential")
    bh = scope_source._keyed(scope["bh_failure_windows"], set(formations), "BJ BH failure")
    bi = scope_source._keyed(scope["bi_failure_windows"], set(formations), "BJ BI failure")
    saved.require(not set(possible) & set(bh) and not set(possible) & set(bi)
                  and not set(bh) & set(bi)
                  and len(possible) == counts["potential_windows"]
                  and len(bh) == counts["bh_failure_windows"]
                  and len(bi) == counts["bi_failure_windows"]
                  and len(possible) + len(bh) + len(bi) == counts["prior_windows"]
                  and len({code for _, code in set(possible) | set(bh) | set(bi)}) == counts["target_codes"],
                  "BJ failure/potential partition mismatch")
    for row in bh.values():
        reasons = row.get("proven_numeric_failures")
        saved.require(isinstance(reasons, list) and bool(reasons) and reasons == sorted(set(reasons))
                      and all(reason in scope_source.BH_FAILURE_REASONS for reason in reasons),
                      "BJ BH failure reason mismatch")
    saved.require(all(row.get("reason") in scope_source.BI_FAILURE_REASONS for row in bi.values()),
                  "BJ BI failure reason mismatch")
    reference = {"role": "bh_result", "sha256": scope_source.INPUTS["bh_result"]["sha256"]}
    saved.require(scope.get("prior_dispositions_reference") == {**reference, "count": counts["prior_windows"]}
                  and scope.get("retained_controls_reference") == {
                      **reference, "field": "bb_population.retained_controls", "count": counts["retained_controls"]}
                  and type(scope["prior_dispositions_reference"]["count"]) is int
                  and type(scope["retained_controls_reference"]["count"]) is int,
                  "BJ full population/control reference mismatch")
    expected_population = {"population_rows": counts["prior_windows"] + counts["retained_controls"],
                           "target_windows": counts["prior_windows"], "target_codes": counts["target_codes"],
                           "retained_controls": counts["retained_controls"]}
    scope_source._integer_counts(scope["bb_population"], expected_population, "BJ population")
    if counts is COUNTS:
        scope_source._integer_counts(scope["formation_totals"], saved.AQ_TOTALS, "BJ full formation")
    expected_union: dict[str, set[str]] = {}
    for (formation, code), row in possible.items():
        arms = []
        for arm in formations[formation]:
            entry = date.fromisoformat(arm["execution_on"])
            interval = [day.isoformat() for day in days[days.index(entry):end + 1]]
            saved.require(bool(interval), "empty BJ possible holding interval")
            expected_union.setdefault(code, set()).update(interval)
            arms.append({"session_lag": arm["session_lag"], "possible_entry_on": arm["execution_on"],
                         "selection_at": arm["selection_at"], "scheduled_due_on": arm["due_on"],
                         "investigation_start": interval[0], "investigation_end": interval[-1],
                         "investigation_sessions": len(interval)})
        saved.require(row.get("arms") == arms, "BJ potential interval geometry mismatch")
    union = {code: sorted(values) for code, values in sorted(expected_union.items())}
    saved.require(scope.get("requested_code_dates") == union and len(union) == counts["potential_codes"]
                  and sum(map(len, union.values())) == counts["required_unique_code_dates"],
                  "BJ full original-issue union mismatch")
    expected_summary = {key: counts[key] for key in (
        "potential_windows", "potential_codes", "required_unique_code_dates", "prior_windows",
        "bh_failure_windows", "bi_failure_windows")}
    expected_summary.update(arm_windows={"D1": len(possible), "D2": len(possible)}, unresolved_input_windows=0)
    saved.require(scope.get("summary") == expected_summary
                  and all(type(value) is int for key, value in scope["summary"].items() if key != "arm_windows")
                  and all(type(value) is int for value in scope["summary"]["arm_windows"].values()),
                  "BJ union summary mismatch")
    positions = {day: index for index, day in enumerate(calendar)}
    histories = {formation: calendar[positions[formation] - params["lookback"]:positions[formation]]
                 for formation in formations}
    cache = sorted(set(formations) | {day for values in histories.values() for day in values})
    saved.require(len(cache) == counts["cached_dates"], "fixed cached-date union mismatch")
    cached_iso = {date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}").isoformat() for day in cache}
    intersection = {code: [day for day in values if day in cached_iso] for code, values in union.items()}
    intersection = {code: values for code, values in intersection.items() if values}
    uncached = {code: [day for day in values if day not in cached_iso] for code, values in union.items()}
    return {"calendar_dates": calendar, "paired_schedule": schedule, "history_windows": histories,
            "cached_dates": cache, "requested_code_dates": union, "intersection_code_dates": intersection,
            "uncached_code_dates": uncached, "original_code_dates": sum(map(len, union.values())),
            "intersection_code_dates_count": sum(map(len, intersection.values())),
            "uncached_code_dates_count": sum(map(len, uncached.values())),
            "uncached_meaning": "outside preserved cache-date geometry; not a missing trade"}


def cache_receipts(inputs: saved.PinnedInputs, declared: dict, plan: dict) -> dict:
    """Verify all nine pinned receipts and ledgers before selecting raw bodies."""
    formation = saved.formation
    services = saved.probe.SERVICES
    aq_matrix = [{"service": service, "bas_dd": day} for day in formation.DATES for service in services]
    aq, aq_started, aq_ledger = saved._receipt(inputs, declared["aq"], "aq")
    saved.require(aq.get("status") == "formations_audited" and aq.get("report_persisted") is True
                  and aq_started.get("request_matrix") == aq_matrix and len(aq["observations"]) == 56,
                  "AQ acquisition receipt mismatch")
    saved._ledger(aq_ledger, aq_matrix)
    ar_days = sorted({day for values in plan["history_windows"].values() for day in values})
    matrix = [{"bas_dd": day, "service": service} for day in ar_days for service in services[:2]]
    saved.require(len(matrix) == 1680 and not set(ar_days) & set(formation.DATES), "AR fixed matrix mismatch")
    original, original_started, original_ledger = saved._receipt(inputs, declared["ar_original"], "ar_original")
    recovery, recovery_started, recovery_ledger = saved._receipt(inputs, declared["ar_recovery"], "ar_recovery")
    saved.require(original.get("status") == "failed" and original.get("failure") == "transport_failure"
                  and original.get("requests_attempted") == 550 and len(original["observations"]) == 549
                  and original_started.get("request_matrix") == matrix
                  and recovery.get("status") == "liquidity_audited"
                  and recovery.get("combined_valid_responses") == 1680
                  and recovery.get("candidates_persisted") is True
                  and recovery.get("transport_retries_used") == 0 and len(recovery["observations"]) == 1131
                  and recovery_started.get("request_matrix") == matrix
                  and recovery_started.get("new_request_matrix") == matrix[549:], "AR acquisition receipt mismatch")
    saved._ledger(original_ledger, matrix[:549], trailing_transport_failure=True)
    saved._ledger(recovery_ledger, matrix[549:], logical_first=550, recovery=True)
    return {"aq": aq, "ar_original": original, "ar_recovery": recovery,
            "aq_matrix": aq_matrix, "ar_matrix": matrix}


def raw_coordinate(day: str, service: str, receipts: dict) -> tuple[str, int, dict, dict, bool]:
    """AQ filenames use full four-service ordinals; AR filenames use logical ordinals."""
    saved.require(service in saved.probe.SERVICES[:2], "only cached trading services are allowed")
    item = {"bas_dd": day, "service": service}
    if item in receipts["aq_matrix"]:
        number = receipts["aq_matrix"].index(item) + 1
        observation = receipts["aq"]["observations"][number - 1]
        return "aq", number, item, observation, True
    saved.require(item in receipts["ar_matrix"], "requested date is outside cache")
    number = receipts["ar_matrix"].index(item) + 1
    if number <= 549:
        observation = receipts["ar_original"]["observations"][number - 1]
        return "ar_original", number, item, observation, False
    observation = receipts["ar_recovery"]["observations"][number - 550]
    saved.require(observation.get("logical_number") == number and observation.get("number") == number - 549,
                  "AR recovery observation number mismatch")
    return "ar_recovery", number, item, observation, False


def load_raw(inputs: saved.PinnedInputs, declared: dict, plan: dict, receipts: dict) -> dict:
    wanted = {(code, day.replace("-", "")) for code, days in plan["intersection_code_dates"].items() for day in days}
    result, response_metadata = {}, {}
    calendar = [date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}") for day in plan["calendar_dates"]]
    positions = {day: index for index, day in enumerate(plan["calendar_dates"])}
    for day in sorted({day for _, day in wanted}):
        rows_by_service = {}
        for service in saved.probe.SERVICES[:2]:
            role, number, item, observation, aq = raw_coordinate(day, service, receipts)
            rows = saved._rows(inputs, Path(declared[role]["root"]), number, item, observation, role, aq=aq)
            rows_by_service[service] = rows
            metadata = saved.response_metadata(day, service, observation, calendar, positions[day], f"{role}:{number}")
            response_metadata.setdefault(day, []).append(metadata)
            for position, row in enumerate(rows):
                key = row["ISU_CD"], day
                if key not in wanted:
                    continue
                saved.require(key not in result, "duplicate requested raw code/date across trading services")
                result[key] = {"values": {field: row.get(raw_field) for field, raw_field in zip(FIELDS, RAW_FIELDS)},
                               "row_position": position, "service": service, "logical_number": number,
                               "provenance": metadata}
        saved.formation._check_relations(rows_by_service)
    for key in sorted(wanted - result.keys()):
        metadata = response_metadata[key[1]]
        result[key] = {"values": None, "row_position": None, "service": None, "logical_number": None,
                       "provenance": {"observation_date": metadata[0]["observation_date"],
                           "available_at": metadata[0]["available_at"], "retrieved_at": "unknown",
                           "source": "both verified KRX cached trading envelopes; requested issue absent",
                           "is_final": None, "data_vintage": None, "evidence_level": "assumed",
                           "evidence_level_scope": "modeled_availability_only",
                           "source_public_available_at": None,
                           "availability_policy": metadata[0]["availability_policy"],
                           "response_receipts": metadata}}
    return result


def numeric_state(values: dict | None) -> tuple[str, dict[str, Decimal], list[str]]:
    """Interpret exact nonnegative source text; absence/NULL/invalid never becomes zero."""
    if values is None:
        return "missing", {}, ["absent_requested_row:cause_unknown"]
    parsed, issues = {}, []
    for field in FIELDS:
        value = values.get(field)
        if value is None:
            issues.append("null:" + field)
            continue
        candidate = None
        if type(value) is str and saved.probe.NUMBER.fullmatch(value):
            try:
                amount = Decimal(value.replace(",", ""))
            except InvalidOperation:
                pass
            else:
                if amount.is_finite() and amount >= 0:
                    candidate = amount
        if candidate is None:
            issues.append("invalid:" + field)
        else:
            parsed[field] = candidate
    if all(field in parsed for field in FIELDS[:4]):
        opening, high, low, close = (parsed[field] for field in FIELDS[:4])
        # KRX's observed zero OHL with a carried positive close is a distinct
        # untradable state, not an invented missing bar or a positive quote.
        if not opening == high == low == 0 and not low <= min(opening, close) <= max(opening, close) <= high:
            issues.append("invalid:ohlc_bounds")
            for field in FIELDS[:4]:
                parsed.pop(field)
    if issues:
        return ("invalid" if any(item.startswith("invalid:") for item in issues) else "null"), parsed, issues
    if any(parsed[field] == 0 for field in FIELDS[:4]):
        return "zero_ohlc", parsed, []
    if len({parsed[field] for field in FIELDS[:4]}) == 1 and parsed["turnover"] == 0:
        return "frozen_zero_turnover", parsed, []
    return "positive_ohlc", parsed, []


def load_scan(path: Path, plan: dict, inputs: saved.PinnedInputs, scope_sha256: str) -> dict:
    """Read exact intersection in one ro transaction, metadata before any bars.

    Fingerprint input is encoded JSON containing the full pinned calendar,
    scope hash and exact intersection, typed panel/progress and EVERY requested
    row (including an explicit absent marker). Text, integer, REAL.hex(), blob
    and NULL storage identities are preserved, including invalid values. This
    identifies consumed rows/metadata/scope only, never the entire DB or BI's
    old snapshot, original vintage, adjustment policy or completeness.
    """
    scope = plan["intersection_code_dates"]
    calendar = [date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}") for day in plan["calendar_dates"]]
    requested = {code: tuple(date.fromisoformat(day) for day in days) for code, days in scope.items()}
    saved.require(bool(requested) and all(re.fullmatch(r"[0-9A-Z]{6}", code) for code in requested),
                  "nonempty exact code/date intersection is required")
    for days in requested.values():
        saved.require(bool(days) and list(days) == sorted(set(days))
                      and all(date(2019, 1, 2) <= day <= date(2026, 9, 18)
                              and day in calendar and calendar.index(day) + 1 < len(calendar) for day in days),
                      "scan request outside registered spent calendar")
    entry = {"role": "scan_database", "path": str(path), "mode": "ro", "columns": list(READ_COLUMNS),
             "read_scope_sha256": scope_sha256, "read_status": "started"}
    inputs.manifest.append(entry)
    path = saved._safe_path(path)
    info = path.lstat()
    saved.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "scan database must be a regular single-link file")
    fingerprint = {"schema": SCHEMA + ":typed-scan-v1", "calendar_dates": plan["calendar_dates"],
                   "read_scope_sha256": scope_sha256, "intersection_code_dates": scope,
                   "panel": [], "progress": [], "rows": []}
    rows, progress = {}, []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                timeout=10, isolation_level=None)) as scan:
        scan.execute("PRAGMA query_only=ON")
        scan.execute("BEGIN")
        tables = scan.execute("SELECT name,type FROM sqlite_master WHERE name IN "
                              "('scan_panel','scan_progress','scan_bars') ORDER BY name").fetchall()
        saved.require(tables == [("scan_bars", "table"), ("scan_panel", "table"), ("scan_progress", "table")],
                      "registered scan inputs must be SQLite tables")
        panel = scan.execute("SELECT id,start,end FROM scan_panel ORDER BY id").fetchall()
        saved.require(panel == [(1, "20190102", "20260918")], "exact spent scan_panel required")
        fingerprint["panel"] = [[_typed(value) for value in row] for row in panel]
        for code in sorted(scope):
            metadata = scan.execute("SELECT first_date,last_date,bars,frozen,status,fetched_at "
                                    "FROM scan_progress WHERE code=?", (code,)).fetchall()
            progress.append(_progress(code, metadata))
            fingerprint["progress"].append([code, [[_typed(value) for value in row] for row in metadata]])
        by_code = {row.code: row for row in progress}
        for code, days in sorted(requested.items()):
            count = 0
            for offset in range(0, len(days), 200):
                batch = days[offset:offset + 200]
                placeholders = ",".join("?" for _ in batch)
                for raw in scan.execute(
                    "SELECT bsop_date,open,high,low,close,volume,turnover FROM scan_bars "
                    f"WHERE code=? AND bsop_date IN ({placeholders}) ORDER BY bsop_date",
                    (code, *(day.strftime("%Y%m%d") for day in batch)),
                ):
                    key = code, raw[0]
                    bounds = by_code[code]
                    saved.require(key not in rows and raw[0] in {day.strftime("%Y%m%d") for day in batch},
                                  "duplicate or out-of-scope scan row")
                    saved.require(bounds.first_date <= _scan_date(raw[0]) <= bounds.last_date,
                                  "scan row outside progress bounds")
                    count += 1
                    saved.require(count <= bounds.bars, "requested row count exceeds progress bars")
                    rows[key] = raw
            for day in days:
                raw = rows.get((code, day.strftime("%Y%m%d")))
                fingerprint["rows"].append([code, day.isoformat(),
                    "absent" if raw is None else [_typed(value) for value in raw]])
        scan.execute("ROLLBACK")
    sha256 = saved.digest(saved.encoded(fingerprint))
    received_at = datetime.now(timezone.utc).isoformat()
    entry.update(read_status="snapshot_consumed", typed_snapshot_sha256=sha256,
                 fetched_row_count=len(rows), snapshot_received_at=received_at)
    positions = {day: index for index, day in enumerate(calendar)}
    result = {}
    for code, days in sorted(requested.items()):
        for day in days:
            raw = rows.get((code, day.strftime("%Y%m%d")))
            result[code, day.strftime("%Y%m%d")] = {
                "values": None if raw is None else {
                    field: value if value is None or type(value) is str else {"sqlite_storage": _typed(value)}
                    for field, value in zip(FIELDS, raw[1:])},
                "typed_values": None if raw is None else [_typed(value) for value in raw],
                "provenance": {
                    "observation_date": day.isoformat(),
                    "available_at": SessionLagPolicy(1).selection_at(calendar, positions[day]).isoformat(),
                    "retrieved_at": "unknown", "source": "stored KIS adjusted daily scan_bars",
                    "is_final": None, "data_vintage": None, "evidence_level": "assumed",
                    "evidence_level_scope": "modeled_availability_only", "source_public_available_at": None,
                    "availability_policy": "assumed_next_trading_session_0830_Asia_Seoul",
                    "snapshot_received_at": received_at,
                    "snapshot_received_at_meaning": "local read snapshot receipt; original row retrieval unknown",
                    "typed_snapshot_sha256": sha256}}
    return {"rows": result, "fingerprint": fingerprint, "typed_snapshot_sha256": sha256,
            "snapshot_received_at": received_at,
            "fingerprint_scope": "typed consumed requested rows including absence, panel, complete requested-code progress, calendar and read scope hash",
            "whole_database_hash": False, "BI_snapshot_equality_checked": False,
            "progress_counts_recomputed_from_prices": False}


def _product_equal(a: Decimal, b: Decimal, c: Decimal, d: Decimal) -> bool:
    # Decimal multiplication uses the ambient precision; integer ratios do not.
    an, ad = a.as_integer_ratio()
    bn, bd = b.as_integer_ratio()
    cn, cd = c.as_integer_ratio()
    dn, dd = d.as_integer_ratio()
    return an * bn * cd * dd == cn * dn * ad * bd


def compare_rows(plan: dict, raw_rows: dict, scan: dict) -> tuple[list[dict], dict]:
    audits = []
    fields = {field: {"comparable": 0, "equal": 0, "unequal": 0, "uncomparable": 0} for field in FIELDS}
    causes, raw_states, scan_states = Counter(), Counter(), Counter()
    close_pairs = nonunit = common_pairs = common = 0
    for code, days in sorted(plan["intersection_code_dates"].items()):
        for day in days:
            key = code, day.replace("-", "")
            raw, adjusted = raw_rows.get(key), scan["rows"][key]
            raw_values = None if raw is None else raw["values"]
            raw_state, raw_numbers, raw_issues = numeric_state(raw_values)
            scan_state, scan_numbers, scan_issues = numeric_state(adjusted["values"])
            raw_states[raw_state] += 1
            scan_states[scan_state] += 1
            equality = {}
            for field in FIELDS:
                valid = field in raw_numbers and field in scan_numbers
                equality[field] = raw_numbers[field] == scan_numbers[field] if valid else None
                fields[field]["comparable" if valid else "uncomparable"] += 1
                if valid:
                    fields[field]["equal" if equality[field] else "unequal"] += 1
            pair_causes = ["raw:" + issue for issue in raw_issues] + ["scan:" + issue for issue in scan_issues]
            positive_close = all("close" in values and values["close"] > 0 for values in (raw_numbers, scan_numbers))
            factor = None
            if positive_close:
                close_pairs += 1
                unit = raw_numbers["close"] == scan_numbers["close"]
                nonunit += not unit
                factor = {"numerator": adjusted["values"]["close"], "denominator": raw_values["close"],
                          "orientation": "stored_scan_close / official_raw_close", "unit": unit}
            else:
                pair_causes.append("close_factor:requires_two_valid_positive_closes")
            uniform = None
            if all(all(field in values and values[field] > 0 for field in FIELDS[:4])
                   for values in (raw_numbers, scan_numbers)):
                common_pairs += 1
                uniform = all(_product_equal(scan_numbers[field], raw_numbers["close"],
                                             raw_numbers[field], scan_numbers["close"]) for field in FIELDS[:4])
                common += uniform
            else:
                pair_causes.append("common_ohlc_factor:requires_eight_valid_positive_prices")
            causes.update(pair_causes)
            audits.append({"code": code, "observation_date": day,
                           "raw": {"state": raw_state, "issues": raw_issues, "values": raw_values,
                                   "coordinate": None if raw is None else {key: raw[key] for key in (
                                       "service", "row_position", "logical_number")},
                                   "provenance": None if raw is None else raw["provenance"]},
                           "scan": {**adjusted, "state": scan_state, "issues": scan_issues},
                           "field_equality": equality, "close_factor": factor,
                           "uniform_ohlc_factor": uniform, "uncomparable_causes": pair_causes})
    denominator = len(audits)
    return audits, {"intersection_code_dates": denominator, "original_code_dates": plan["original_code_dates"],
                    "uncached_code_dates": plan["uncached_code_dates_count"], "field_equality": fields,
                    "positive_close_pairs": close_pairs, "nonunit_positive_close_pairs": nonunit,
                    "uncomparable_close_pairs": denominator - close_pairs,
                    "positive_ohlc_pairs": common_pairs, "uniform_ohlc_factor_pairs": common,
                    "nonuniform_ohlc_factor_pairs": common_pairs - common,
                    "uncomparable_ohlc_factor_pairs": denominator - common_pairs,
                    "raw_states": dict(sorted(raw_states.items())), "scan_states": dict(sorted(scan_states.items())),
                    "uncomparable_causes": dict(sorted(causes.items())),
                    "cause_denominator": "requested intersection code/dates; causes may overlap"}


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    declaration = spec["inputs"]["scope_result"]
    scope = inputs.json(Path(declaration["path"]), declaration["sha256"], "scope_result")
    plan = plan_scope(scope, spec["parameters"])
    del scope
    receipts = cache_receipts(inputs, spec["inputs"], plan)
    raw = load_raw(inputs, spec["inputs"], plan, receipts)
    del receipts
    read_scope = {"schema": SCHEMA + ":read-scope-v1", **plan,
                  "scope_result_sha256": declaration["sha256"], "scan_database": spec["inputs"]["scan_database"],
                  "input_manifest_before_database": inputs.manifest,
                  "new_source_requests": 0, "automatic_retries": 0}
    scope_bytes = saved.encoded(read_scope)
    saved.write_exclusive(output / "read-scope.json", scope_bytes)
    scope_sha = saved.digest(scope_bytes)
    scan = load_scan(Path(spec["inputs"]["scan_database"]["path"]), plan, inputs, scope_sha)
    rows, summary = compare_rows(plan, raw, scan)
    audit = {"schema": SCHEMA + ":rows-audit-v1", "read_scope_sha256": scope_sha,
             **{key: value for key, value in scan.items() if key != "rows"}, "rows": rows,
             "raw_fields": dict(zip(FIELDS, RAW_FIELDS)), "read_columns": list(READ_COLUMNS),
             "arithmetic": "exact Decimal equality and integer cross products; no divided/rounded factors"}
    audit_bytes = saved.encoded(audit)
    saved.write_exclusive(output / "rows-audit.json", audit_bytes)
    return {"schema": SCHEMA, "status": "cached_price_parity_audited", "summary": summary,
            "read_scope_sha256": scope_sha, "rows_audit_sha256": saved.digest(audit_bytes),
            "typed_scan_snapshot_sha256": scan["typed_snapshot_sha256"], "input_manifest": inputs.manifest,
            "holding_lot_denominator": None, "action_lot_denominator": None,
            "D1_completed_studies": 0, "D2_completed_studies": 0,
            "database_access": True, "new_source_requests": 0, "automatic_retries": 0,
            "returns_computed": False, "holding_selection_executed": False,
            "historical_eligibility_certified": False, "price_basis_certified": False,
            "historical_vintage_certified": False, "identity_period_created": False,
            "actions_applied": False, "BI_turnover_replaced": False, "BI_failures_reused": False,
            "promotion_allowed": False,
            "limitations": ["original-issue cached intersection only; baseline/formation outside this union remains open",
                "observed factors do not establish vendor rules, successor conversion or complete action coverage",
                "original row retrieval, publication, finality and historical vintage remain unknown",
                "availability is explicitly assumed next session; no eligibility inference follows"]}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path,
                  *, reader=saved.read_private) -> dict:
    """One durable logged attempt; exclusive partial packages survive errors."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    inputs = saved.PinnedInputs(reader)

    def evaluate():
        created = False
        try:
            saved.private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_inputs(spec, inputs, output)
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items()
                                      if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)),
                          specification_sha256=saved.digest(raw))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            return {"status": result["status"], "output_dir": str(output), "summary": result["summary"],
                    "result_sha256": saved.digest(saved.encoded(result)),
                    "rows_audit_sha256": result["rows_audit_sha256"],
                    "read_scope_sha256": result["read_scope_sha256"],
                    "source_manifest_sha256": result["source_manifest_sha256"],
                    "input_manifest_sha256": saved.digest(saved.encoded(inputs.manifest)),
                    "holding_lot_denominator": None, "action_lot_denominator": None,
                    "D1_completed_studies": 0, "D2_completed_studies": 0}
        except BaseException as exc:
            if created:
                if not (output / "input-manifest.json").exists():
                    saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "actual_inputs_attempted": inputs.manifest,
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)),
                    "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0}))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"],
            specification_sha256=saved.digest(raw), window=spec["window"], runs_path=runs,
            parameters={**spec["parameters"], "source_manifest": sources, "declared_inputs": spec["inputs"],
                        "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded({"status": "failed", "error_type": type(exc).__name__, "automatic_retries": 0}).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
