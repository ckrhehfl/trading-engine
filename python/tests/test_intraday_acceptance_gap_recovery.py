"""Invented bytes only; never read saved market reports, archives or databases."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
import json

import pytest

from research import intraday_acceptance_gap_recovery as recovery


DAY = "2026-08-03"
SYMBOL = "KRX:000001"
NOW = "2026-10-26T09:00:00+09:00"


def fixture(*, day=DAY, different=False, defect="missing", price="100"):
    """Construct the published DU wire shape directly, without old builders."""
    opening, closing = recovery._bounds(day)
    raw = [{"symbol": SYMBOL, "interval": "1m", "open_time_ms": int((opening + offset * recovery.MINUTE).timestamp()) * 1000,
        **dict.fromkeys(recovery.PRICE_FIELDS, price), "fetched_at": "2026-10-20T00:00:00+00:00"}
        for offset in range(int((closing - opening) / recovery.MINUTE))]
    complete = deepcopy(raw)
    if defect == "missing":
        raw.pop(45)
        state = "missing"
    elif defect == "conflict":
        raw.append(deepcopy(raw[45]))
        state = "conflict"
    else:
        raw[45]["open"] = "NaN"
        state = "invalid_price"
    chunk = {"symbol": SYMBOL, "session": day, "observation_date": day, "available_at": None,
        "retrieved_at": "2026-10-20T01:00:00+00:00", "source": "collector klines",
        "is_final": None, "evidence_level": "assumed", "rows": raw}
    vintage = hashlib.sha256(recovery._canonical(chunk) + b"\n").hexdigest()
    params = json.loads((recovery.ROOT / "configs/research/discovery/intraday-opening-acceptance-calibration-v1.json").read_text())["parameters"]
    iso = datetime.fromisoformat(day).isocalendar()
    source = {"study_id": "intraday-opening-acceptance-calibration-v1",
        "mode": "fixed-opening-acceptance-own-label-frequency-calibration", "window": dict(recovery.WINDOW),
        "parameters": params, "assumptions": {"fixture": "invented"},
        "reference_sessions": [day], "reference_weeks": [f"{iso.year:04d}-W{iso.week:02d}"],
        "comparison_performed": False, "calibration_only": True, "variants": {}}
    for policy in recovery.POLICIES:
        entry = opening + (35 if different and policy == "D+2" else 30) * recovery.MINUTE
        cap, decision = entry + 30 * recovery.MINUTE, entry - 3 * recovery.MINUTE
        counts = {item: int(state == item) for item in ("missing", "conflict", "invalid_price")}
        label = {"status": "unknown", "entry_at": entry.isoformat(), "exit_at": cap.isoformat(),
            "time_cap_decided_at": decision.isoformat(), "entry_open": price, "exit_open": price,
            "price_change": None, "required_minute_buckets": 31, "unknown_minute_buckets": 1,
            "unknown_bucket_reason_counts": counts, "unknown_reasons": [f"label_{state}_minute_buckets"],
            "entry_price_available_at": (entry + 3 * recovery.MINUTE).isoformat(),
            "label_available_at": (cap + 3 * recovery.MINUTE).isoformat(),
            "evidence_level": "assumed", "actual_fill_certified": False}
        row = {"session": day, "symbol": SYMBOL, "data_vintage": vintage,
            "raw_chunk_metadata": {key: value for key, value in chunk.items() if key not in {"symbol", "session", "rows"}},
            "input_rows": len(raw), "source_offset_counts_ms": {"0": len(raw)},
            "gaps": [{"start": (opening + 45 * recovery.MINUTE).isoformat(), "end": (opening + 46 * recovery.MINUTE).isoformat(), "state": state}],
            "arms": {recovery.ARM: {"status": "signal", "event": {"decision_at": decision.isoformat()}, "label": label, "unknown_reasons": []}}}
        source["variants"][policy] = {"days": [row]}
    plan = recovery.derive_requests(source)
    capture = {"snapshot_started_at": NOW, "snapshot_finished_at": "2026-10-26T09:00:02+09:00",
        "query_count": len(plan), "windows": []}
    responses = {}
    for request in plan:
        entry, cap = map(recovery._instant, (request["entry_at"], request["exit_at"]))
        prices = [item for item in complete if entry <= recovery._stamp(item["open_time_ms"]).astimezone(recovery.KST) <= cap]
        capture["windows"].append({"request_id": request["request_id"], "retrieved_at": "2026-10-26T09:00:01+09:00", "rows": []})
        api_rows = [{"stck_bsop_date": day.replace("-", ""),
            "stck_cntg_hour": recovery._stamp(item["open_time_ms"]).astimezone(recovery.KST).strftime("%H%M%S"),
            **dict(zip(recovery.API_PRICE_FIELDS, (item[field] for field in recovery.PRICE_FIELDS), strict=True))} for item in prices]
        responses[request["request_id"]] = {"requested_at": "2026-10-26T09:00:03+09:00", "retrieved_at": "2026-10-26T09:00:04+09:00",
            "status": "received", "response": {"rt_cd": "0", "msg_cd": "MCA00000", "output2": api_rows}}
    return source, {(day, SYMBOL): chunk}, capture, responses


def db_rows(response):
    return [{"symbol": SYMBOL, "interval": "1m",
        "open_time_ms": int(datetime.strptime(item["stck_bsop_date"] + item["stck_cntg_hour"], "%Y%m%d%H%M%S").replace(tzinfo=recovery.KST).timestamp()) * 1000,
        **dict(zip(recovery.PRICE_FIELDS, (item[field] for field in recovery.API_PRICE_FIELDS), strict=True)),
        "fetched_at": "2026-10-26T08:59:00+09:00"} for item in response["response"]["output2"]]


def first(inputs):
    return recovery.build_diagnostics(*inputs)["diagnostics"][0]


def test_public_specification_has_roles_and_frozen_limits_only():
    spec = json.loads(recovery.SPECIFICATION_PATH.read_text())
    recovery.validate_spec(spec)
    assert set(spec["resources"]) == set(recovery.RESOURCES)
    assert not any("path" in key or "output_name" == key for key in spec)
    assert "/home/" not in json.dumps(spec)
    spec["resources"]["maximum_market_gets"] += 1
    with pytest.raises(recovery.AcceptanceGapRefusal, match="specification_scope_mismatch"):
        recovery.validate_spec(spec)


def test_requests_deduplicate_exact_windows_and_retain_every_policy_link():
    inputs = fixture()
    plan = recovery.derive_requests(inputs[0])
    assert len(plan) == 1 and plan[0]["policies"] == ["D+1", "D+2"]
    assert len(plan[0]["policy_links"]) == 2
    assert len(plan[0]["required_minute_buckets"]) == 31
    assert plan[0]["request_parameters"]["FID_INPUT_HOUR_1"] == "100059"
    identity = {key: plan[0][key] for key in ("session", "symbol", "entry_at", "exit_at")}
    assert plan[0]["request_id"] == hashlib.sha256(recovery._canonical(identity)).hexdigest()
    other = recovery.derive_requests(fixture(different=True)[0])
    assert len(other) == 2 and other[0]["request_id"] != other[1]["request_id"]


def test_complete_api_is_price_evidence_only_and_inputs_remain_unchanged():
    inputs = fixture()
    before = [recovery._canonical(value) for value in (inputs[0], list(inputs[1].values()), inputs[2], inputs[3])]
    report = recovery.build_diagnostics(*inputs)
    diagnostic = report["diagnostics"][0]
    assert diagnostic["status"] == "verified_price_candidate"
    assert diagnostic["db"]["status"] == "incomplete"
    assert diagnostic["api"]["status"] == "complete_compatible"
    assert len(diagnostic["missing_only_additions"]) == 1
    assert len(diagnostic["verified_complete_candidates"][0]["minutes"]) == 31
    assert report["source"]["variants"]["D+1"]["label_counts"] == {"known": 0, "unknown": 1}
    assert all(report[key] is False for key in recovery.FALSE_CLAIMS)
    assert "price_change" not in json.dumps(report)
    assert report["source_rows_overwritten"] == 0
    assert before == [recovery._canonical(value) for value in (inputs[0], list(inputs[1].values()), inputs[2], inputs[3])]


def test_db_complete_compatible_leaves_api_unprobed_and_forbids_extra_probe():
    inputs = fixture()
    response = next(iter(inputs[3].values()))
    inputs[2]["windows"][0]["rows"] = db_rows(response)
    diagnostic = first((*inputs[:3], {}))
    assert diagnostic["db"]["status"] == "complete_compatible"
    assert diagnostic["api"]["status"] == "unprobed"
    with pytest.raises(recovery.AcceptanceGapRefusal, match="api_probe_after_complete_compatible_db"):
        recovery.build_diagnostics(*inputs)


def test_two_incomplete_new_vintages_cannot_be_combined():
    inputs = fixture()
    response = next(iter(inputs[3].values()))
    inputs[2]["windows"][0]["rows"] = db_rows(response)[:-1]
    response["response"]["output2"].pop(0)
    diagnostic = first(inputs)
    assert diagnostic["db"]["status"] == diagnostic["api"]["status"] == "incomplete"
    assert diagnostic["status"] == "unresolved"
    assert diagnostic["missing_only_additions"] == []


@pytest.mark.parametrize("defect", ["conflict", "invalid_price"])
def test_original_price_defects_cannot_be_replaced(defect):
    diagnostic = first(fixture(defect=defect))
    assert diagnostic["status"] == "unresolved"
    assert "original_price_defect_not_missing" in diagnostic["api"]["reasons"]


def test_explicit_db_disagreement_blocks_otherwise_complete_api():
    inputs = fixture()
    rows = db_rows(next(iter(inputs[3].values())))
    rows[0].update(open="101", high="101", low="101", close="101")
    inputs[2]["windows"][0]["rows"] = rows[:1]
    diagnostic = first(inputs)
    assert diagnostic["api"]["status"] == "complete_compatible"
    assert diagnostic["status"] == "unresolved"
    assert diagnostic["cross_source_conflicts"][0]["sources"] == ["api", "db", "original"]


def test_conflicting_db_duplicates_block_otherwise_complete_api():
    inputs = fixture()
    row = db_rows(next(iter(inputs[3].values())))[0]
    inputs[2]["windows"][0]["rows"] = [row, dict(row, open="101", high="101", low="101", close="101")]
    assert first(inputs)["status"] == "unresolved"


@pytest.mark.parametrize("defect,reason", [
    ("NaN", "invalid_OHLC"), ("0", "invalid_OHLC"), ("-1", "invalid_OHLC"),
    (100.0, "invalid_OHLC"), ("Infinity", "invalid_OHLC"),
])
def test_invalid_api_prices_are_separate_diagnostics(defect, reason):
    inputs = fixture()
    next(iter(inputs[3].values()))["response"]["output2"][0]["stck_oprc"] = defect
    diagnostic = first(inputs)
    assert diagnostic["status"] == "unresolved"
    assert reason in diagnostic["api"]["reasons"]


@pytest.mark.parametrize("field,value,reason", [
    ("stck_bsop_date", "20260804", "api_row_date_mismatch"),
    ("stck_bsop_date", "20260230", "api_row_date_mismatch"),
    ("stck_cntg_hour", "096000", "api_row_minute_clock_mismatch"),
    ("stck_cntg_hour", "100100", "api_row_outside_requested_session_or_exit"),
])
def test_wrong_api_dates_and_clocks_do_not_become_prices(field, value, reason):
    inputs = fixture()
    next(iter(inputs[3].values()))["response"]["output2"][0][field] = value
    assert reason in first(inputs)["api"]["reasons"]


def test_identical_duplicates_source_offsets_prior_dates_and_preopen_are_distinct():
    inputs = fixture()
    raw = next(iter(inputs[3].values()))["response"]["output2"]
    raw.extend([dict(raw[0], stck_cntg_hour="093047"), dict(raw[0], stck_bsop_date="20260802"), dict(raw[0], stck_cntg_hour="085959")])
    diagnostic = first(inputs)
    assert diagnostic["status"] == "verified_price_candidate"
    meta = diagnostic["api"]["metadata"]
    assert meta["identical_duplicate_rows"] == 1
    assert meta["source_offset_counts_seconds"] == {"0": 32, "47": 1, "59": 1}
    assert meta["discarded_prior_date_rows"] == meta["discarded_preopen_rows"] == 1


def test_mixed_api_errors_keep_all_diagnostics_and_never_certify_complete_prices():
    inputs = fixture()
    raw = next(iter(inputs[3].values()))["response"]["output2"]
    raw.extend([dict(raw[0], stck_bsop_date="20260802"), dict(raw[0], stck_cntg_hour="085959"),
        dict(raw[0], stck_cntg_hour="092900"), dict(raw[0], stck_cntg_hour="093047"),
        dict(raw[0], stck_bsop_date="20260804"), dict(raw[0], stck_cntg_hour="096000"),
        dict(raw[0], stck_cntg_hour="100100"), {"unexpected": "invented"}])
    diagnostic = first(inputs)
    api = diagnostic["api"]
    assert api["status"] == "invalid" and api["required_bucket_counts"]["valid"] == 31
    assert api["reasons"] == ["api_row_date_mismatch", "api_row_minute_clock_mismatch",
        "api_row_outside_requested_session_or_exit", "api_row_schema_mismatch"]
    assert api["metadata"] == {"requested_at": "2026-10-26T09:00:03+09:00",
        "retrieved_at": "2026-10-26T09:00:04+09:00", "retention_cutoff": "2025-10-26",
        "response_rows": 39, "unique_minute_buckets": 32, "discarded_prior_date_rows": 1,
        "discarded_preopen_rows": 1, "before_entry_rows": 1, "identical_duplicate_rows": 1,
        "source_offset_counts_seconds": {"0": 35, "47": 1, "59": 1}}
    assert diagnostic["status"] == "unresolved"
    assert diagnostic["verified_complete_candidates"] == diagnostic["missing_only_additions"] == []
    raw.reverse()
    assert first(inputs)["api"] == api


def test_large_exact_decimal_ohlc_survives_small_context():
    value = "123456789012345678901234567890.0000000000000000000000001"
    with localcontext() as context:
        context.prec = 5
        inputs = fixture(price=value)
        diagnostic = first(inputs)
    assert diagnostic["status"] == "verified_price_candidate"
    assert diagnostic["missing_only_additions"][0]["open"] == value


@pytest.mark.parametrize("mutate,reason", [
    (lambda inputs: inputs[0]["variants"]["D+1"]["days"][0]["arms"][recovery.ARM]["label"].update(entry_at=DAY + "T09:31:00+09:00"), "source_label_clock_mismatch"),
    (lambda inputs: inputs[1][DAY, SYMBOL].update(retrieved_at=NOW), "source_chunk_metadata_mismatch"),
    (lambda inputs: inputs[1][DAY, SYMBOL]["rows"][0].update(open="99"), "source_chunk_vintage_mismatch"),
    (lambda inputs: inputs[0]["variants"]["D+1"]["days"][0].update(input_rows=1), "source_chunk_rows_mismatch"),
    (lambda inputs: inputs[0]["variants"]["D+1"]["days"][0]["arms"][recovery.ARM]["label"].update(entry_open="99"), "source_label_endpoint_mismatch"),
    (lambda inputs: inputs[2].update(query_count=0), "db_capture_query_or_clock_mismatch"),
    (lambda inputs: inputs[2]["windows"][0].update(retrieved_at="2026-10-26T09:00:03+09:00"), "db_capture_retrieval_clock_mismatch"),
])
def test_authenticated_source_and_typed_capture_mismatches_fail_the_run(mutate, reason):
    inputs = fixture()
    mutate(inputs)
    with pytest.raises(recovery.AcceptanceGapRefusal, match=reason):
        recovery.build_diagnostics(*inputs)


def test_outside_horizon_requires_unprobed_and_kst_request_date_controls_boundary():
    inputs = fixture(day="2025-09-04")
    envelope = next(iter(inputs[3].values()))
    envelope.update(status="unprobed", response=None, reason="outside_documented_retention_horizon")
    diagnostic = first(inputs)
    assert diagnostic["api"]["status"] == "unprobed"
    assert diagnostic["api"]["metadata"]["retention_cutoff"] == "2025-10-26"
    envelope.update(status="received", response={"rt_cd": "0", "msg_cd": "", "output2": []})
    assert first(inputs)["api"]["reasons"] == ["api_probed_outside_documented_retention_horizon"]
    inputs = fixture(day="2025-09-04")
    envelope = next(iter(inputs[3].values()))
    envelope.update(requested_at="2026-09-03T15:00:00+00:00", retrieved_at="2026-09-03T15:00:01+00:00")
    assert first(inputs)["api"]["metadata"]["retention_cutoff"] == "2025-09-04"


def test_leap_day_calendar_horizon_clips_to_february28():
    inputs = fixture()
    envelope = next(iter(inputs[3].values()))
    envelope.update(requested_at="2028-02-29T09:00:00+09:00", retrieved_at="2028-02-29T09:00:01+09:00",
        status="unprobed", response=None, reason="outside_documented_retention_horizon")
    assert first(inputs)["api"]["metadata"]["retention_cutoff"] == "2027-02-28"


def test_unavailable_envelope_counts_attempt_and_restricts_private_reason():
    inputs = fixture()
    next(iter(inputs[3].values())).update(status="unavailable", response=None, reason="private sensitive error text")
    report = recovery.build_diagnostics(*inputs)
    assert report["summary"]["market_gets"] == 1
    assert report["diagnostics"][0]["api"]["metadata"]["unavailable_reason"] == "invalid_unavailable_reason"


@pytest.mark.parametrize("reason,attempts", [("authentication_unavailable_no_retry", 0),
    ("market_response_unavailable_no_retry", 1), ("market_http_503", 1)])
def test_actual_get_attempt_accounting_distinguishes_authentication(reason, attempts):
    inputs = fixture()
    next(iter(inputs[3].values())).update(status="unavailable", response=None, reason=reason)
    report = recovery.build_diagnostics(*inputs)
    assert report["summary"]["market_gets"] == attempts
    assert report["diagnostics"][0]["api"]["metadata"]["unavailable_reason"] == reason


def test_api_duplicate_disagreement_and_ohlc_bounds_are_not_coalesced():
    inputs = fixture()
    raw = next(iter(inputs[3].values()))["response"]["output2"]
    raw.append(dict(raw[0], stck_oprc="101", stck_hgpr="101", stck_lwpr="101", stck_prpr="101"))
    diagnostic = first(inputs)
    assert diagnostic["api"]["required_bucket_counts"]["conflict"] == 1
    assert "duplicate_minute_conflict" in diagnostic["api"]["reasons"]
    raw.pop()
    raw[0]["stck_hgpr"] = "99"
    assert "invalid_OHLC" in first(inputs)["api"]["reasons"]


def test_db_duplicate_offsets_coalesce_and_coverage_is_effective_unique_buckets():
    inputs = fixture()
    rows = db_rows(next(iter(inputs[3].values())))
    rows.append(dict(rows[0], open_time_ms=rows[0]["open_time_ms"] + 12345))
    inputs[2]["windows"][0]["rows"] = rows
    diagnostic = first((*inputs[:3], {}))
    assert diagnostic["db"]["status"] == "complete_compatible"
    assert diagnostic["db"]["metadata"]["identical_duplicate_rows"] == 1
    assert diagnostic["db"]["metadata"]["source_offset_counts_ms"] == {"0": 31, "12345": 1}
    assert diagnostic["db"]["required_bucket_counts"]["valid"] == 31


@pytest.mark.parametrize("which", ["missing_chunk", "extra_response", "missing_db", "duplicate_db", "extra_db_column", "outside_db_window", "future_fetched_at"])
def test_population_and_typed_db_boundaries_fail_closed(which):
    inputs = fixture()
    if which == "missing_chunk":
        inputs[1].clear()
    elif which == "extra_response":
        inputs[3]["f" * 64] = None
    elif which == "missing_db":
        inputs[2]["windows"].clear()
    elif which == "duplicate_db":
        inputs[2]["windows"].append(deepcopy(inputs[2]["windows"][0]))
    else:
        rows = db_rows(next(iter(inputs[3].values())))
        inputs[2]["windows"][0]["rows"] = rows
        inputs[3].clear()
        if which == "extra_db_column":
            rows[0]["volume"] = "1"
        elif which == "outside_db_window":
            rows[0]["open_time_ms"] -= 60000
        else:
            rows[0]["fetched_at"] = "2026-10-26T09:00:03+09:00"
    with pytest.raises(recovery.AcceptanceGapRefusal):
        recovery.build_diagnostics(*inputs)


def test_api_clock_and_payload_allowlist_rejection_is_diagnostic():
    inputs = fixture()
    envelope = next(iter(inputs[3].values()))
    envelope["retrieved_at"] = "2026-10-26T09:00:02+09:00"
    assert first(inputs)["api"]["reasons"] == ["api_retrieval_clock_mismatch"]
    envelope["retrieved_at"] = "2026-10-26T09:00:04"
    assert first(inputs)["api"]["reasons"] == ["invalid_aware_clock"]
    envelope["retrieved_at"] = "2026-10-26T09:00:04+09:00"
    envelope["response"]["output1"] = {"account": "invented"}
    assert first(inputs)["api"]["reasons"] == ["api_response_unsuccessful_or_schema_mismatch"]


def test_more_than_ten_fixed_unknown_windows_are_refused():
    inputs = fixture()
    for policy in recovery.POLICIES:
        rows = inputs[0]["variants"][policy]["days"]
        for index in range(2, 12):
            row = deepcopy(rows[0])
            row["symbol"] = f"KRX:{index:06d}"
            rows.append(row)
    with pytest.raises(recovery.AcceptanceGapRefusal, match="recovery_coordinate_budget_exceeded"):
        recovery.derive_requests(inputs[0])


def test_different_policy_windows_each_preserve_separate_labels_and_chunk_checks():
    inputs = fixture(different=True)
    report = recovery.build_diagnostics(*inputs)
    assert len(report["diagnostics"]) == 2
    assert all(item["status"] == "verified_price_candidate" for item in report["diagnostics"])
    inputs[0]["variants"]["D+2"]["days"][0]["source_offset_counts_ms"] = {"0": 1}
    with pytest.raises(recovery.AcceptanceGapRefusal, match="source_shared_chunk_mismatch"):
        recovery.build_diagnostics(*inputs)


def test_known_label_returns_are_never_accessed_and_unknown_signals_never_requested():
    class NoPriceChange(dict):
        def __getitem__(self, key):
            if key == "price_change":
                pytest.fail("return label accessed")
            return super().__getitem__(key)

    inputs = fixture()
    for policy in recovery.POLICIES:
        rows = inputs[0]["variants"][policy]["days"]
        known = deepcopy(rows[0])
        known["symbol"] = "KRX:000002"
        known["arms"][recovery.ARM]["label"]["status"] = "known"
        known["arms"][recovery.ARM]["label"] = NoPriceChange(known["arms"][recovery.ARM]["label"])
        rows.append(known)
        rows.append({"session": DAY, "symbol": "KRX:000003", "arms": {recovery.ARM: {"status": "unknown", "label": None, "unknown_reasons": ["no_saved_rows_unknown_cause"]}}})
    report = recovery.build_diagnostics(*inputs)
    assert report["summary"]["request_windows"] == 1
    assert report["source"]["variants"]["D+1"]["signal_counts"] == {"signal": 2, "known_no_signal": 0, "unknown": 1}
