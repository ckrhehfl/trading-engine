"""Invented saved-package bytes only; no market artifacts, DB or acquisition."""

from copy import deepcopy
from datetime import datetime
from decimal import localcontext
import hashlib
import json

import pytest

from research import intraday_acceptance_gap_followup as followup
from research import intraday_acceptance_gap_recovery as dv


def encode(value):
    return dv._canonical(value) + b"\n"


def specification():
    return json.loads(followup.SPECIFICATION_PATH.read_bytes())


def fixture():
    """Build the reduced saved DV wire geometry without any DU report."""
    plan, chunks, diagnostics, db_windows, prior, complete = [], [], [], [], {}, {}
    for index, day in enumerate(("2025-09-04", "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06")):
        opening, closing = dv._bounds(day)
        entry, cap = opening + 30 * dv.MINUTE, opening + 60 * dv.MINUTE
        missing, symbol = opening + 45 * dv.MINUTE, f"KRX:{index + 1:06d}"
        raw = [{"symbol": symbol, "interval": "1m", "open_time_ms": int(minute.timestamp()) * 1000,
            **dict.fromkeys(dv.PRICE_FIELDS, "100"), "fetched_at": "2026-10-20T00:00:00+00:00"}
            for minute in (opening + offset * dv.MINUTE for offset in range(int((closing - opening) / dv.MINUTE)))
            if minute != missing]
        chunk = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
            "retrieved_at": "2026-10-20T01:00:00+00:00", "source": "collector klines",
            "is_final": None, "evidence_level": "assumed", "rows": raw}
        vintage = hashlib.sha256(encode(chunk)).hexdigest()
        identity = {"session": day, "symbol": symbol, "entry_at": dv._iso(entry), "exit_at": dv._iso(cap)}
        identifier = hashlib.sha256(dv._canonical(identity)).hexdigest()
        intervals = [{"start": dv._iso(missing), "end": dv._iso(missing + dv.MINUTE), "state": "missing"}]
        label = {"status": "unknown", "entry_at": dv._iso(entry), "exit_at": dv._iso(cap),
            "time_cap_decided_at": dv._iso(entry - 3 * dv.MINUTE), "entry_open": "100", "exit_open": "100",
            "required_minute_buckets": 31, "unknown_minute_buckets": 1,
            "unknown_bucket_reason_counts": {"missing": 1, "conflict": 0, "invalid_price": 0},
            "unknown_reasons": ["label_missing_minute_buckets"], "entry_price_available_at": dv._iso(entry + 3 * dv.MINUTE),
            "label_available_at": dv._iso(cap + 3 * dv.MINUTE), "evidence_level": "assumed", "actual_fill_certified": False}
        request = {"request_id": identifier, **identity, "policies": list(dv.POLICIES),
            "policy_links": [{"policy": policy, "source_data_vintage": vintage,
                "time_cap_decided_at": label["time_cap_decided_at"], "label_metadata": deepcopy(label)} for policy in dv.POLICIES],
            "required_minute_buckets": [dv._iso(entry + offset * dv.MINUTE) for offset in range(31)],
            "original_gap_intervals": intervals, "original_missing_minute_buckets": [dv._iso(missing)],
            "request_parameters": {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol[4:],
                "FID_INPUT_DATE_1": day.replace("-", ""), "FID_INPUT_HOUR_1": cap.strftime("%H%M") + "59",
                "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N"}}
        metadata = {key: chunk[key] for key in chunk if key not in {"session", "symbol", "rows"}}
        source_row = {"session": day, "symbol": symbol, "raw_chunk_metadata": metadata,
            "data_vintage": vintage, "input_rows": len(raw), "source_offset_counts_ms": {"0": len(raw)}, "gaps": intervals}
        original_all, gaps_all, original_meta = dv._source_chunk(chunk, source_row)
        original = {minute: value for minute, value in original_all.items() if entry <= minute <= cap}
        gaps = {minute: value for minute, value in gaps_all.items() if entry <= minute <= cap}
        db_window = {"request_id": identifier, "retrieved_at": "2026-10-26T09:00:01+09:00",
            "rows": [row for row in raw if entry.timestamp() * 1000 <= row["open_time_ms"] <= cap.timestamp() * 1000]}
        buckets, db_meta, reasons = dv._db_minutes(request, db_window)
        database, _ = dv._candidate(request, original, gaps, buckets, db_meta, reasons)
        api_rows = [{"stck_bsop_date": day.replace("-", ""), "stck_cntg_hour": (entry + offset * dv.MINUTE).strftime("%H%M%S"),
            **dict.fromkeys(dv.API_PRICE_FIELDS, "100")} for offset in range(31)]
        complete[identifier] = {"requested_at": "2026-10-26T10:00:00+09:00", "retrieved_at": "2026-10-26T10:00:01+09:00",
            "status": "received", "response": {"rt_cd": "0", "msg_cd": "MCA00000", "output2": api_rows}, "reason": None}
        response = deepcopy(complete[identifier])
        response.update(requested_at="2026-10-26T09:00:03+09:00", retrieved_at="2026-10-26T09:00:04+09:00")
        if index in (0, 4):
            response.update(status="unprobed" if index == 0 else "unavailable", response=None,
                reason="outside_documented_retention_horizon" if index == 0 else "market_http_500")
        else:
            response["response"]["output2"].pop(15)
        api, _ = dv._api_candidate(request, response, original, gaps)
        diagnostics.append({"request_id": identifier, "original": {**original_meta,
            "gap_counts": {"missing": 1, "conflict": 0, "invalid_price": 0}, "missing_minute_buckets": [dv._iso(missing)]},
            "db": database, "api": api, "cross_source_conflicts": [], "status": "unresolved",
            "verified_complete_candidates": [], "missing_only_additions": [], "source_rows_overwritten": 0})
        plan.append(request)
        chunks.append(chunk)
        db_windows.append(db_window)
        prior[identifier] = response
    report = {"study_id": dv.SPECIFICATION_ID, "mode": dv.MODE, "window": dict(dv.WINDOW),
        "requests": plan, "diagnostics": diagnostics, "source": {"variants": {
            policy: {"target_symbol_sessions": 5, "label_counts": {"known": 0, "unknown": 5}}
            for policy in dv.POLICIES}}, "summary": {"request_windows": 5,
            "policy_unknown_outcomes": {policy: 5 for policy in dv.POLICIES}, "verified_price_candidate_windows": 0,
            "unresolved_windows": 5, "db_queries": 5, "market_gets": 4}, "source_rows_overwritten": 0, **dv.FALSE_CLAIMS}
    capture = {"snapshot_started_at": "2026-10-26T09:00:00+09:00", "snapshot_finished_at": "2026-10-26T09:00:02+09:00",
        "query_count": 5, "windows": db_windows}
    retry_id = plan[-1]["request_id"]
    package = {"responses": {retry_id: complete[retry_id]}, "api_counts": {"market_gets": 1,
        "token_posts": 0, "credential_read": True, "cached_token_reused": True, "automatic_retries": 0}}
    return {"source_report": report, "request_plan": plan, "chunks": chunks, "db": capture,
        "prior_responses": prior, "new_responses": package}


def build(inputs=None):
    return followup.assemble_report(specification(), {name: encode(value) for name, value in (inputs or fixture()).items()})


def retry(inputs):
    return next(iter(inputs["new_responses"]["responses"].values()))


def diagnostic(inputs=None):
    return build(inputs)["diagnostics"][-1]


def test_public_spec_has_roles_and_unchanged_budget():
    spec = specification()
    followup.validate_spec(spec)
    assert spec["resources"]["maximum_new_market_gets"] == 1
    assert spec["resources"]["maximum_new_db_queries"] == 0
    assert all(value not in json.dumps(spec) for value in ("/home/", "C:\\", ".env"))
    spec["resources"]["cpu_seconds"] += 1
    with pytest.raises(followup.AcceptanceGapFollowupRefusal, match="specification_scope_mismatch"):
        followup.validate_spec(spec)


def test_complete_retry_preserves_five_windows_both_policies_and_old_counters():
    inputs = fixture()
    before = encode(inputs)
    result = build(inputs)
    assert result["summary"] == {"request_windows": 5, "policy_unknown_outcomes": {"D+1": 5, "D+2": 5},
        "prior_market_gets": 4, "new_market_gets": 1, "new_token_posts": 0,
        "verified_price_candidate_windows": 1, "unresolved_windows": 4}
    assert all(result[key] is False for key in followup.FALSE_CLAIMS)
    assert result["requests"] == inputs["request_plan"]
    assert len(result["diagnostics"][-1]["missing_only_additions"]) == 1
    assert all(row["new_api"] is None for row in result["diagnostics"][:-1])
    assert "price_change" not in json.dumps(result)
    assert encode(inputs) == before
    roles = {name: encode(inputs[name]) for name in followup.SAVED_ROLES}
    assert followup.derive_retry_plan(specification(), roles) == inputs["request_plan"][-1:]


def test_literal_spillover_seconds_and_missing_minutes_never_relax_acceptance():
    inputs = fixture()
    raw = retry(inputs)["response"]["output2"]
    raw.pop(15)
    raw.extend([dict(raw[0], stck_bsop_date="20260805"), dict(raw[0], stck_cntg_hour="085959"),
        dict(raw[0], stck_cntg_hour="092947"), dict(raw[0], stck_cntg_hour="100159")])
    result = diagnostic(inputs)
    literal = result["new_response_diagnostics"]
    assert result["status"] == "unresolved"
    assert result["new_api"]["status"] == "invalid"
    assert len(literal["required_present_minute_buckets"]) == 30
    assert literal["required_missing_minute_buckets"] == ["2026-08-06T09:45:00+09:00"]
    assert literal["row_category_counts"] == {"malformed_schema": 0, "invalid_date": 0, "invalid_clock": 0,
        "prior_session": 1, "future_session": 0, "preopen": 1, "before_entry": 1, "required_window": 30, "after_cap": 1}
    assert literal["source_offset_counts_seconds"] == {"0": 31, "47": 1, "59": 2}
    assert literal["required_offset_counts_seconds"] == {"0": 30}
    assert literal["timestamp_semantics_certified"] is False
    assert literal["absence_cause_inferred"] is False
    assert result["missing_only_additions"] == []


def test_all_required_prices_with_extra_aftercap_still_fail_strict_candidate():
    inputs = fixture()
    raw = retry(inputs)["response"]["output2"]
    raw.append(dict(raw[0], stck_cntg_hour="100100"))
    result = diagnostic(inputs)
    assert len(result["new_response_diagnostics"]["required_valid_minute_buckets"]) == 31
    assert result["new_response_diagnostics"]["required_missing_minute_buckets"] == []
    assert result["status"] == "unresolved" and result["verified_complete_candidates"] == []
    assert "api_row_outside_requested_session_or_exit" in result["new_api"]["reasons"]


@pytest.mark.parametrize("defect", ["overlap", "invalid", "duplicate", "future_date", "invalid_clock"])
def test_complete_timestamp_coverage_cannot_hide_price_or_row_defects(defect):
    inputs = fixture()
    raw = retry(inputs)["response"]["output2"]
    if defect == "overlap":
        raw[0].update(dict.fromkeys(dv.API_PRICE_FIELDS, "101"))
    elif defect == "invalid":
        raw[15]["stck_oprc"] = "NaN"
    elif defect == "duplicate":
        raw.append(dict(raw[15], **dict.fromkeys(dv.API_PRICE_FIELDS, "101")))
    elif defect == "future_date":
        raw.append(dict(raw[0], stck_bsop_date="20260807"))
    else:
        raw.append(dict(raw[0], stck_cntg_hour="096000"))
    result = diagnostic(inputs)
    assert result["status"] == "unresolved" and result["missing_only_additions"] == []
    assert result["new_api"]["status"] == "invalid"


def test_second_offsets_are_retained_without_shifting_and_exact_decimal_ignores_context():
    inputs = fixture()
    for row in retry(inputs)["response"]["output2"]:
        row["stck_cntg_hour"] = row["stck_cntg_hour"][:-2] + "47"
        row.update(dict.fromkeys(dv.API_PRICE_FIELDS, "100.0000000000000000000000000000000000000000"))
    with localcontext() as context:
        context.prec = 2
        result = diagnostic(inputs)
    assert result["status"] == "verified_price_candidate"
    assert result["new_response_diagnostics"]["source_offset_counts_seconds"] == {"47": 31}
    assert result["new_response_diagnostics"]["required_offset_counts_seconds"] == {"47": 31}
    assert result["new_response_diagnostics"]["first_timestamp"] == "2026-08-06T09:30:47+09:00"
    assert result["new_api"]["minutes"][0]["minute"] == "2026-08-06T09:30:00+09:00"


def test_two_incomplete_vintages_cannot_fill_each_other():
    inputs = fixture()
    retry(inputs)["response"]["output2"].pop(0)
    result = diagnostic(inputs)
    assert result["prior_diagnostic"]["db"]["required_bucket_counts"]["valid"] == 30
    assert result["new_api"]["required_bucket_counts"]["valid"] == 30
    assert result["status"] == "unresolved" and result["missing_only_additions"] == []


@pytest.mark.parametrize("reason,gets", [("market_http_500", 1), ("authentication_unavailable_no_retry", 0),
    ("market_response_unavailable_no_retry", 1)])
def test_retry_failure_is_preserved_without_automatic_attempt(reason, gets):
    inputs = fixture()
    retry(inputs).update(status="unavailable", response=None, reason=reason)
    inputs["new_responses"]["api_counts"].update(market_gets=gets, cached_token_reused=False)
    result = build(inputs)
    assert result["summary"]["prior_market_gets"] == 4
    assert result["summary"]["new_market_gets"] == gets
    assert result["summary"]["verified_price_candidate_windows"] == 0
    assert result["diagnostics"][-1]["new_api"]["status"] == "unavailable"


def test_new_retention_clock_cannot_probe_an_old_date():
    inputs = fixture()
    retry(inputs).update(requested_at="2027-08-07T09:00:00+09:00", retrieved_at="2027-08-07T09:00:01+09:00")
    with pytest.raises(followup.AcceptanceGapFollowupRefusal, match="outside_retention"):
        build(inputs)


@pytest.mark.parametrize("mutation", ["new_other_id", "extra_new", "missing_new", "counter", "retry", "prior_summary",
    "prior_response", "plan_time", "chunk", "prior_flags", "prior_id", "label_unknown"])
def test_population_provenance_and_originals_fail_closed(mutation):
    inputs = fixture()
    package = inputs["new_responses"]
    if mutation == "new_other_id":
        package["responses"] = {inputs["request_plan"][1]["request_id"]: retry(inputs)}
    elif mutation == "extra_new":
        package["responses"]["extra"] = retry(inputs)
    elif mutation == "missing_new":
        package["responses"] = {}
    elif mutation == "counter":
        package["api_counts"]["market_gets"] = 2
    elif mutation == "retry":
        package["api_counts"]["automatic_retries"] = 1
    elif mutation == "prior_summary":
        inputs["source_report"]["summary"]["market_gets"] = 3
    elif mutation == "prior_response":
        inputs["prior_responses"][inputs["request_plan"][1]["request_id"]]["response"]["output2"].pop()
    elif mutation == "plan_time":
        inputs["request_plan"][-1]["request_parameters"]["FID_INPUT_HOUR_1"] = "100000"
    elif mutation == "chunk":
        inputs["chunks"][-1]["rows"][0]["open"] = "101"
    elif mutation == "prior_flags":
        inputs["source_report"]["comparison_performed"] = True
    elif mutation == "prior_id":
        inputs["source_report"]["diagnostics"][-1]["request_id"] = "wrong"
    else:
        inputs["request_plan"][-1]["policy_links"][0]["label_metadata"]["unknown_minute_buckets"] = 2
    with pytest.raises(followup.AcceptanceGapFollowupRefusal):
        build(inputs)


def test_duplicate_json_keys_extra_roles_and_non_bytes_are_rejected():
    inputs = {name: encode(value) for name, value in fixture().items()}
    for changed in ({**inputs, "extra": b"{}"}, {**inputs, "chunks": "[]"},
            {**inputs, "chunks": b'{"x":1,"x":2}'}):
        with pytest.raises(followup.AcceptanceGapFollowupRefusal):
            followup.assemble_report(specification(), changed)
