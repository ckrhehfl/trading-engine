"""Supplied-byte follow-up of completed DV evidence; no IO or return math.

Only the frozen DV price-validation primitives are reused. Original experiment
selection, labels and statistics are never reconstructed or amended here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from research import intraday_acceptance_gap_recovery as dv


ROOT = Path(__file__).resolve().parents[2]
SPECIFICATION_ID = "intraday-acceptance-gap-followup-v1"
MODE = "fixed-http500-reprobe-and-saved-minute-diagnostics-only"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-acceptance-gap-followup-v1.json"
SOURCE_SPECIFICATION_SHA256 = "7ee3d5670b633f0e87bbe4bc8689416cde37b8738fb198ca3ad6aed14c0be2f5"
WINDOW = dict(dv.WINDOW)
SOURCE_ROLES = {
    "source_report_role": "pinned_DV_completed_diagnostic_report",
    "request_plan_role": "pinned_DV_original_request_plan",
    "selected_chunks_role": "pinned_DV_selected_original_source_chunks",
    "database_capture_role": "pinned_DV_saved_database_capture_no_new_query",
    "prior_responses_role": "pinned_DV_saved_quotation_responses",
    "new_responses_role": "one_new_HTTP500_window_source_equivalent_response",
    "output_role": "new_private_verified_followup_evidence_package",
}
PARAMETERS = {
    "scope": "all_five_original_DV_windows_and_both_policy_links_preserved",
    "retry": "unique_prior_market_http_500_only_one_source_equivalent_GET_no_automatic_retry",
    "request": "unchanged_cap_second59_J_pastY_fakeN_max120rows_no_pagination",
    "retention": "request_time_KST_minus_one_calendar_year_Feb29_clips_Feb28_upper_bound_only",
    "timestamps": "literal_dates_times_seconds_and_required_missing_minutes_no_shift_or_fill",
    "acceptance": "unchanged_all31_exact_positive_finite_Decimal_OHLC_every_original_overlap",
    "vintages": "separate_original_DB_prior_API_new_API_no_incomplete_vintage_union",
    "output_scope": "diagnostics_and_complete_price_candidates_only_no_labels_or_returns",
    "label_adoption": False,
    "underpower_exception_applied": False,
}
RESOURCES = {
    "maximum_saved_bytes": 67108864, "maximum_windows": 5,
    "maximum_rows_per_response": 120, "maximum_new_market_gets": 1,
    "maximum_new_db_queries": 0, "maximum_token_posts": 1,
    "minimum_free_bytes": 1073741824, "cpu_seconds": 300,
    "address_space_mib": 512, "wall_seconds": 1200, "nice": 10,
    "automatic_retries": 0,
}
SAVED_ROLES = ("source_report", "request_plan", "chunks", "db", "prior_responses")
COUNTERS = {"market_gets", "token_posts", "credential_read", "cached_token_reused", "automatic_retries"}
FALSE_CLAIMS = dict(dv.FALSE_CLAIMS)
AcceptanceGapFollowupRefusal = dv.AcceptanceGapRefusal


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AcceptanceGapFollowupRefusal(reason)


def validate_spec(specification: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES,
        "source_specification_sha256": SOURCE_SPECIFICATION_SHA256, **SOURCE_ROLES}
    _require(dv._canonical(specification) == dv._canonical(expected), "followup_specification_scope_mismatch")


def _decode(raw: bytes) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as exc:
        raise AcceptanceGapFollowupRefusal("invalid_supplied_JSON") from exc


def _inputs(specification: Mapping, role_bytes: Mapping, *, new: bool) -> dict:
    validate_spec(specification)
    expected = set(SAVED_ROLES) | ({"new_responses"} if new else set())
    _require(isinstance(role_bytes, Mapping) and set(role_bytes) == expected, "supplied_role_population_mismatch")
    _require(all(type(raw) is bytes for raw in role_bytes.values()), "supplied_role_not_bytes")
    _require(sum(map(len, role_bytes.values())) <= RESOURCES["maximum_saved_bytes"], "saved_byte_budget_exceeded")
    return {name: _decode(raw) for name, raw in role_bytes.items()}


def _request(request: Mapping) -> None:
    fields = {"request_id", "session", "symbol", "entry_at", "exit_at", "policies", "policy_links",
        "required_minute_buckets", "original_gap_intervals", "original_missing_minute_buckets", "request_parameters"}
    _require(isinstance(request, Mapping) and set(request) == fields, "prior_request_schema_mismatch")
    identity = {key: request[key] for key in ("session", "symbol", "entry_at", "exit_at")}
    _require(request["request_id"] == hashlib.sha256(dv._canonical(identity)).hexdigest(), "prior_request_identity_mismatch")
    day = dv._day(request["session"])
    entry, cap = map(dv._instant, (request["entry_at"], request["exit_at"]))
    opening, closing = dv._bounds(request["session"])
    _require(re.fullmatch(r"KRX:[0-9A-Z]{6}", request["symbol"]) is not None
        and WINDOW["start"] <= day.isoformat() <= WINDOW["end"]
        and opening < entry < cap < closing and cap - entry == 30 * dv.MINUTE
        and not any(clock.second or clock.microsecond for clock in (entry, cap))
        and (entry - opening) % (5 * dv.MINUTE) == dv.MINUTE * 0,
        "prior_request_coordinate_mismatch")
    required = [dv._iso(entry + index * dv.MINUTE) for index in range(31)]
    _require(request["required_minute_buckets"] == required, "prior_required_minutes_mismatch")
    _require(request["policies"] == list(dv.POLICIES) and len(request["policy_links"]) == 2,
        "prior_policy_links_mismatch")
    for policy, link in zip(dv.POLICIES, request["policy_links"], strict=True):
        label = link["label_metadata"]
        decision = entry - 3 * dv.MINUTE
        _require(set(link) == {"policy", "source_data_vintage", "time_cap_decided_at", "label_metadata"}
            and link["policy"] == policy and link["time_cap_decided_at"] == dv._iso(decision)
            and set(label) == set(dv.LABEL_METADATA_FIELDS) and label["status"] == "unknown"
            and label["entry_at"] == request["entry_at"] and label["exit_at"] == request["exit_at"]
            and label["time_cap_decided_at"] == dv._iso(decision)
            and label["entry_price_available_at"] == dv._iso(entry + 3 * dv.MINUTE)
            and label["label_available_at"] == dv._iso(cap + 3 * dv.MINUTE)
            and label["required_minute_buckets"] == 31 and label["evidence_level"] == "assumed"
            and label["actual_fill_certified"] is False, "prior_label_metadata_mismatch")
    _require(request["request_parameters"] == {"FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": request["symbol"][4:], "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
        "FID_INPUT_HOUR_1": cap.strftime("%H%M") + "59", "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N"}, "prior_request_parameters_mismatch")


def _original(request: Mapping, diagnostic: Mapping, chunk: Mapping) -> tuple[dict, dict]:
    metadata = diagnostic["original"]
    # Full-session reconstruction validates the saved chunk but reads no DU row.
    row = {"session": request["session"], "symbol": request["symbol"],
        "raw_chunk_metadata": metadata["metadata"], "data_vintage": metadata["data_vintage"],
        "input_rows": metadata["input_rows"], "source_offset_counts_ms": metadata["source_offset_counts_ms"]}
    opening, closing = dv._bounds(request["session"])
    buckets = {}
    for item in chunk["rows"]:
        minute = dv._stamp(item["open_time_ms"]).astimezone(dv.KST).replace(second=0, microsecond=0)
        buckets.setdefault(minute, []).append(item)
    all_gaps = {}
    minute = opening
    while minute < closing:
        group = buckets.get(minute, [])
        if len(group) != 1:
            all_gaps[minute] = "conflict" if group else "missing"
        else:
            try:
                dv._prices(group[0], dv.PRICE_FIELDS)
            except dv.AcceptanceGapRefusal:
                all_gaps[minute] = "invalid_price"
        minute += dv.MINUTE
    row["gaps"] = dv._intervals(all_gaps)
    originals, gaps, verified_metadata = dv._source_chunk(chunk, row)
    required = set(map(dv._instant, request["required_minute_buckets"]))
    originals = {minute: value for minute, value in originals.items() if minute in required}
    gaps = {minute: state for minute, state in gaps.items() if minute in required}
    missing = [dv._iso(minute) for minute, state in sorted(gaps.items()) if state == "missing"]
    counts = {state: list(gaps.values()).count(state) for state in ("missing", "conflict", "invalid_price")}
    _require(metadata == {**verified_metadata, "gap_counts": counts, "missing_minute_buckets": missing}
        and request["original_gap_intervals"] == dv._intervals(gaps)
        and request["original_missing_minute_buckets"] == missing and bool(gaps), "prior_original_gap_mismatch")
    for link in request["policy_links"]:
        label = link["label_metadata"]
        _require(link["source_data_vintage"] == metadata["data_vintage"]
            and label["unknown_minute_buckets"] == len(gaps)
            and label["unknown_bucket_reason_counts"] == counts
            and label["unknown_reasons"] == [f"label_{state}_minute_buckets" for state, count in counts.items() if count],
            "prior_link_gap_or_vintage_mismatch")
        for price_name, clock_name in (("entry_open", "entry_at"), ("exit_open", "exit_at")):
            value = originals.get(dv._instant(request[clock_name]))
            _require(label[price_name] is None if value is None else
                dv._prices(dict.fromkeys(dv.PRICE_FIELDS, label[price_name]), dv.PRICE_FIELDS)[0] == value[0],
                "prior_endpoint_mismatch")
    return originals, gaps


def _conflicts(required: list, sources: Mapping) -> list[dict]:
    result = []
    for minute in map(dv._instant, required):
        values = {name: prices[minute] for name, prices in sources.items() if minute in prices}
        if len(set(values.values())) > 1:
            result.append({"minute": dv._iso(minute), "sources": sorted(values)})
    return result


def _prior(inputs: Mapping, checkpoint: Callable) -> tuple[list, dict, dict, dict]:
    report, plan, chunks, capture, responses = (inputs[name] for name in SAVED_ROLES)
    _require(isinstance(report, Mapping) and report.get("study_id") == dv.SPECIFICATION_ID
        and report.get("mode") == dv.MODE and report.get("window") == WINDOW
        and all(report.get(key) is False for key in FALSE_CLAIMS)
        and report.get("source_rows_overwritten") == 0, "prior_report_scope_mismatch")
    _require(isinstance(plan, list) and len(plan) == RESOURCES["maximum_windows"]
        and report["requests"] == plan and len(report["diagnostics"]) == len(plan), "prior_plan_population_mismatch")
    _require(plan == sorted(plan, key=lambda row: tuple(row[key] for key in ("session", "symbol", "entry_at", "exit_at"))),
        "prior_plan_order_mismatch")
    for request in plan:
        _request(request)
    ids = {request["request_id"] for request in plan}
    _require(len(ids) == 5 and isinstance(responses, Mapping) and set(responses) == ids,
        "prior_response_population_mismatch")
    _require(isinstance(chunks, list), "prior_chunks_schema_mismatch")
    chunk_map = {(chunk["session"], chunk["symbol"]): chunk for chunk in chunks}
    _require(len(chunk_map) == len(chunks) and set(chunk_map) == {(row["session"], row["symbol"]) for row in plan},
        "prior_chunk_population_mismatch")
    windows = dv._db_capture(capture, plan)
    prior_by_id = {row["request_id"]: row for row in report["diagnostics"]}
    _require(len(prior_by_id) == 5 and set(prior_by_id) == ids, "prior_diagnostic_population_mismatch")
    values = {}
    for request in plan:
        checkpoint()
        identifier = request["request_id"]
        diagnostic = prior_by_id[identifier]
        _require(set(diagnostic) == {"request_id", "original", "db", "api", "cross_source_conflicts", "status",
            "verified_complete_candidates", "missing_only_additions", "source_rows_overwritten"},
            "prior_diagnostic_schema_mismatch")
        original, gaps = _original(request, diagnostic, chunk_map[request["session"], request["symbol"]])
        buckets, metadata, reasons = dv._db_minutes(request, windows[identifier])
        database, db_prices = dv._candidate(request, original, gaps, buckets, metadata, reasons)
        api, api_prices = dv._api_candidate(request, responses[identifier], original, gaps)
        conflicts = _conflicts(request["required_minute_buckets"], {"original": original, "db": db_prices, "api": api_prices})
        _require(diagnostic["db"] == database and diagnostic["api"] == api
            and diagnostic["cross_source_conflicts"] == conflicts and diagnostic["status"] == "unresolved"
            and diagnostic["verified_complete_candidates"] == [] and diagnostic["missing_only_additions"] == []
            and diagnostic["source_rows_overwritten"] == 0
            and database["status"] != "complete_compatible" and api["status"] != "complete_compatible",
            "prior_diagnostic_reconstruction_mismatch")
        values[identifier] = {"original": original, "gaps": gaps, "db": db_prices, "prior_api": api_prices}
    statuses = Counter(row["status"] for row in responses.values())
    retry = [request for request in plan if responses[request["request_id"]].get("status") == "unavailable"
        and responses[request["request_id"]].get("reason") == "market_http_500"]
    _require(statuses == {"received": 3, "unavailable": 1, "unprobed": 1} and len(retry) == 1
        and all(row.get("reason") == "outside_documented_retention_horizon" for row in responses.values() if row["status"] == "unprobed"),
        "prior_acquisition_scope_mismatch")
    _require(report["summary"] == {"request_windows": 5, "policy_unknown_outcomes": {policy: 5 for policy in dv.POLICIES},
        "verified_price_candidate_windows": 0, "unresolved_windows": 5, "db_queries": 5, "market_gets": 4}
        and set(report["source"]["variants"]) == set(dv.POLICIES)
        and all(report["source"]["variants"][policy]["label_counts"]["unknown"] == 5 for policy in dv.POLICIES),
        "prior_summary_mismatch")
    return plan, prior_by_id, values, retry


def derive_retry_plan(specification: Mapping, role_bytes: Mapping,
        checkpoint: Callable[[], None] | None = None) -> list[dict]:
    """Return the unchanged unique HTTP500 request for durable pre-GET freezing."""
    try:
        return deepcopy(_prior(_inputs(specification, role_bytes, new=False), checkpoint or (lambda: None))[3])
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise AcceptanceGapFollowupRefusal("malformed_saved_followup_package") from exc


def _literal(request: Mapping, envelope: Mapping, candidate: Mapping) -> dict:
    raw = envelope["response"]["output2"] if envelope["status"] == "received" else []
    dates, times, seconds, required_seconds, categories = Counter(), Counter(), Counter(), Counter(), Counter()
    stamps, minutes = set(), set()
    opening, _ = dv._bounds(request["session"])
    entry, cap = map(dv._instant, (request["entry_at"], request["exit_at"]))
    for row in raw:
        if not isinstance(row, Mapping) or set(row) != set(dv.API_PRICE_FIELDS) | {"stck_bsop_date", "stck_cntg_hour"}:
            categories["malformed_schema"] += 1
            continue
        day, clock = row["stck_bsop_date"], row["stck_cntg_hour"]
        if isinstance(day, str):
            dates[day] += 1
        if isinstance(clock, str):
            times[clock] += 1
        try:
            _require(isinstance(day, str) and re.fullmatch(r"[0-9]{8}", day) is not None, "literal_invalid_date")
            parsed_day = datetime.strptime(day, "%Y%m%d").date()
        except (dv.AcceptanceGapRefusal, ValueError):
            categories["invalid_date"] += 1
            continue
        try:
            _require(isinstance(clock, str) and re.fullmatch(r"[0-9]{6}", clock) is not None, "literal_invalid_clock")
            stamp = datetime.strptime(day + clock, "%Y%m%d%H%M%S").replace(tzinfo=dv.KST)
        except (dv.AcceptanceGapRefusal, ValueError):
            categories["invalid_clock"] += 1
            continue
        minute = stamp.replace(second=0)
        stamps.add(stamp)
        minutes.add(minute)
        seconds[str(stamp.second)] += 1
        category = ("prior_session" if parsed_day < entry.date() else "future_session" if parsed_day > entry.date()
            else "preopen" if minute < opening else "before_entry" if minute < entry
            else "after_cap" if minute > cap else "required_window")
        categories[category] += 1
        if category == "required_window":
            required_seconds[str(stamp.second)] += 1
    required = set(map(dv._instant, request["required_minute_buckets"]))
    valid = {row["minute"] for row in candidate["minutes"]}
    return {"status": envelope["status"], "reason": envelope.get("reason"), "response_rows": len(raw),
        "literal_date_counts": dict(sorted(dates.items())), "literal_time_counts": dict(sorted(times.items())),
        "source_offset_counts_seconds": dict(sorted(seconds.items(), key=lambda pair: int(pair[0]))),
        "required_offset_counts_seconds": dict(sorted(required_seconds.items(), key=lambda pair: int(pair[0]))),
        "row_category_counts": {name: categories[name] for name in ("malformed_schema", "invalid_date", "invalid_clock",
            "prior_session", "future_session", "preopen", "before_entry", "required_window", "after_cap")},
        "unique_timestamp_count": len(stamps), "unique_minute_buckets": len(minutes),
        "first_timestamp": dv._iso(min(stamps)) if stamps else None,
        "last_timestamp": dv._iso(max(stamps)) if stamps else None,
        "required_present_minute_buckets": [dv._iso(value) for value in sorted(required & minutes)],
        "required_missing_minute_buckets": [dv._iso(value) for value in sorted(required - minutes)],
        "required_valid_minute_buckets": sorted(valid),
        "required_not_valid_minute_buckets": [value for value in request["required_minute_buckets"] if value not in valid],
        "timestamp_semantics_certified": False, "absence_cause_inferred": False}


def _new_capture(package: Mapping, retry: Mapping, prior: Mapping) -> tuple[dict, dict]:
    _require(isinstance(package, Mapping) and set(package) == {"responses", "api_counts"}, "new_capture_schema_mismatch")
    responses, counts = package["responses"], package["api_counts"]
    identifier = retry["request_id"]
    _require(isinstance(responses, Mapping) and set(responses) == {identifier}, "new_response_not_unique_HTTP500_window")
    _require(isinstance(counts, Mapping) and set(counts) == COUNTERS
        and all(type(counts[name]) is int and 0 <= counts[name] <= 1 for name in ("market_gets", "token_posts"))
        and type(counts["automatic_retries"]) is int and counts["automatic_retries"] == 0
        and all(type(counts[name]) is bool for name in ("credential_read", "cached_token_reused")),
        "new_acquisition_counter_mismatch")
    envelope = responses[identifier]
    _require(isinstance(envelope, Mapping) and set(envelope) == {"requested_at", "retrieved_at", "status", "response", "reason"},
        "new_envelope_schema_mismatch")
    requested, retrieved = map(dv._instant, (envelope["requested_at"], envelope["retrieved_at"]))
    _require(dv._instant(prior["retrieved_at"]) <= requested <= retrieved, "new_capture_before_prior_or_reversed")
    try:
        cutoff = requested.date().replace(year=requested.year - 1)
    except ValueError:
        cutoff = requested.date().replace(year=requested.year - 1, day=28)
    eligible = cutoff <= dv._day(retry["session"]) <= requested.date()
    expected_gets = 0 if not eligible or envelope["reason"] == "authentication_unavailable_no_retry" else 1
    if not eligible:
        _require(envelope["status"] == "unprobed" and envelope["response"] is None
            and envelope["reason"] == "outside_documented_retention_horizon", "new_probe_outside_retention")
    else:
        _require(envelope["status"] in {"received", "unavailable"}, "new_eligible_probe_omitted")
    if envelope["status"] == "received":
        payload = envelope["response"]
        _require(envelope["reason"] is None and isinstance(payload, Mapping)
            and set(payload) == {"rt_cd", "msg_cd", "output2"} and payload["rt_cd"] == "0"
            and isinstance(payload["msg_cd"], str) and re.fullmatch(r"[A-Za-z0-9_]{0,32}", payload["msg_cd"]) is not None
            and isinstance(payload["output2"], list) and len(payload["output2"]) <= 120, "new_response_allowlist_mismatch")
        for row in payload["output2"]:
            _require(isinstance(row, Mapping) and set(row) == set(dv.API_PRICE_FIELDS) | {"stck_bsop_date", "stck_cntg_hour"}
                and all(value is None or isinstance(value, str) and len(value) <= 64 for value in row.values()),
                "new_row_allowlist_mismatch")
    else:
        reason = envelope["reason"]
        _require(envelope["response"] is None and isinstance(reason, str)
            and (reason in {"outside_documented_retention_horizon", "authentication_unavailable_no_retry",
                "market_response_unavailable_no_retry"} or re.fullmatch(r"market_http_[0-9]{3}", reason) is not None),
            "new_failure_reason_not_allowlisted")
    _require(counts["market_gets"] == expected_gets
        and (not (counts["market_gets"] or counts["token_posts"] or counts["cached_token_reused"]) or counts["credential_read"])
        and (not counts["cached_token_reused"] or counts["token_posts"] == 0)
        and (eligible or not any(counts[name] for name in ("market_gets", "token_posts", "credential_read", "cached_token_reused"))),
        "new_attempt_counter_or_auth_mismatch")
    return envelope, dict(counts)


def _assemble_report(specification: Mapping, role_bytes: Mapping,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Reconstruct five saved windows and validate the sole new response."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    inputs = _inputs(specification, role_bytes, new=True)
    plan, previous, prices, retry = _prior(inputs, checkpoint)
    retry_id = retry[0]["request_id"]
    envelope, counts = _new_capture(inputs["new_responses"], retry[0], inputs["prior_responses"][retry_id])
    diagnostics = []
    for request in plan:
        checkpoint()
        identifier = request["request_id"]
        saved = prices[identifier]
        sources = {key: saved[key] for key in ("original", "db", "prior_api")}
        new_api, literal, candidates = None, None, []
        if identifier == retry_id:
            new_api, new_prices = dv._api_candidate(request, envelope, saved["original"], saved["gaps"])
            sources["new_api"] = new_prices
            literal = _literal(request, envelope, new_api)
            if new_api["status"] == "complete_compatible":
                candidates.append({"source": "new_api", "minutes": new_api["minutes"],
                    "missing_only_additions": dv._wire_prices({minute: new_prices[minute]
                        for minute, state in saved["gaps"].items() if state == "missing"})})
        conflicts = _conflicts(request["required_minute_buckets"], sources)
        usable = bool(candidates) and not conflicts and all(state == "missing" for state in saved["gaps"].values())
        usable = usable and all("duplicate_minute_conflict" not in candidate["reasons"]
            for candidate in (previous[identifier]["db"], previous[identifier]["api"]))
        diagnostics.append({"request_id": identifier, "prior_diagnostic": deepcopy(previous[identifier]),
            "prior_response_diagnostics": _literal(request, inputs["prior_responses"][identifier], previous[identifier]["api"]),
            "new_response_diagnostics": literal, "new_api": new_api, "cross_source_conflicts": conflicts,
            "status": "verified_price_candidate" if usable else "unresolved",
            "verified_complete_candidates": candidates,
            "missing_only_additions": candidates[0]["missing_only_additions"] if usable else [], "source_rows_overwritten": 0})
    verified = sum(row["status"] == "verified_price_candidate" for row in diagnostics)
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW), "source_roles": dict(SOURCE_ROLES),
        "source": {"study_id": dv.SPECIFICATION_ID, "summary": deepcopy(inputs["source_report"]["summary"]),
            "variants": deepcopy(inputs["source_report"]["source"]["variants"])},
        "requests": deepcopy(plan), "retry_request_ids": [retry_id], "diagnostics": diagnostics,
        "summary": {"request_windows": 5, "policy_unknown_outcomes": {policy: 5 for policy in dv.POLICIES},
            "prior_market_gets": 4, "new_market_gets": counts["market_gets"], "new_token_posts": counts["token_posts"],
            "verified_price_candidate_windows": verified, "unresolved_windows": 5 - verified},
        "acquisition": {"prior_market_gets": 4, "new_api_counts": counts},
        "source_rows_overwritten": 0, **FALSE_CLAIMS}


def assemble_report(specification: Mapping, role_bytes: Mapping,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Reject malformed supplied packages with a source-value-free reason."""
    try:
        return _assemble_report(specification, role_bytes, checkpoint)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise AcceptanceGapFollowupRefusal("malformed_saved_followup_package") from exc


build_report = assemble_report
