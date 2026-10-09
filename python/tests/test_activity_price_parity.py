"""Registered cache parity tests use only small/synthetic in-memory or SQLite data."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
import sqlite3
import stat

import pytest

from research import activity_price_parity as module
from research import activity_preflight as saved
from test_activity_scope_preflight import fixture as scope_fixture, plan as original_scope
from test_activity_failure_inputs import database, mutate, DAYS, A, B
from test_krx_formation_audit import rows_for_day


def small_scope():
    data = scope_fixture()
    result = original_scope(data)
    counts = {key: result["summary"][key] for key in (
        "potential_windows", "potential_codes", "required_unique_code_dates", "prior_windows",
        "bh_failure_windows", "bi_failure_windows")}
    counts.update(target_codes=3, retained_controls=1, cached_dates=9)
    return result, data[1], counts


def test_scope_recomputes_full_union_then_intersects_without_selecting_observed_dates():
    scope, params, counts = small_scope()
    result = module.plan_scope(scope, params, counts=counts)
    calendar = scope["calendar_dates"]
    expected_cache = [calendar[index] for index in (0, 1, 2, 4, 5, 6, 8, 9, 10)]
    assert result["cached_dates"] == expected_cache
    assert result["original_code_dates"] == 14
    assert result["intersection_code_dates_count"] == 6
    assert result["uncached_code_dates_count"] == 8
    assert result["requested_code_dates"] == scope["requested_code_dates"]
    assert "not a missing trade" in result["uncached_meaning"]
    all_days = result["intersection_code_dates"]["111111"] + result["uncached_code_dates"]["111111"]
    assert sorted(all_days) == scope["requested_code_dates"]["111111"]
    assert result["uncached_code_dates"]["111111"][-1] == params["end"]


@pytest.mark.parametrize("change", [
    lambda value: value["requested_code_dates"]["111111"].pop(),
    lambda value: value["requested_code_dates"].update({"999999": ["2020-01-07"]}),
    lambda value: value["summary"].update(required_unique_code_dates=13),
    lambda value: value["summary"].update(potential_codes=True),
    lambda value: value["possible_windows"][0]["arms"][0].update(investigation_end="2020-01-10"),
    lambda value: value["possible_windows"].append(deepcopy(value["possible_windows"][0])),
    lambda value: value["bi_failure_windows"].clear(),
])
def test_bad_scope_union_summary_and_geometry_refused(change):
    scope, params, counts = small_scope()
    change(scope)
    with pytest.raises(ValueError):
        module.plan_scope(scope, params, counts=counts)


@pytest.mark.parametrize("change", ["partition", "reference", "controls", "cutoff", "calendar", "flags", "reason"])
def test_scope_partition_and_receipt_boundaries(change):
    scope, params, counts = small_scope()
    if change == "partition":
        scope["bi_failure_windows"][0].update(code="111111")
    elif change == "reference":
        scope["prior_dispositions_reference"]["sha256"] = "0" * 64
    elif change == "controls":
        scope["retained_controls_reference"]["count"] = 2
    elif change == "cutoff":
        scope["paired_schedule"][0]["arms"][0]["selection_at"] = "2020-01-04T08:30:00+09:00"
    elif change == "calendar":
        scope["calendar_dates"].pop(4)
    elif change == "flags":
        scope["returns_computed"] = True
    else:
        scope["bh_failure_windows"][0]["proven_numeric_failures"] = ["unknown"]
    with pytest.raises(ValueError):
        module.plan_scope(scope, params, counts=counts)


def test_production_spec_freezes_exact_inputs_parameters_and_nine_existing_receipt_pins():
    root = Path(__file__).resolve().parents[2]
    spec = saved.strict_json((root / "configs/research/discovery/activity-cached-price-parity-v1.json").read_bytes())
    reference = saved.strict_json((root / saved.REFERENCE).read_bytes())
    timing = saved.strict_json((root / "configs/research/discovery/activity-timing-preflight-v1.json").read_bytes())
    module.validate_spec(spec, reference)
    assert spec["parameters"] == reference["parameters"]
    assert module.COUNTS["required_unique_code_dates"] == 24598
    assert module.COUNTS["cached_dates"] == 854
    assert set(spec["inputs"]) == {"scope_result", "aq", "ar_original", "ar_recovery", "scan_database"}
    for role in ("aq", "ar_original", "ar_recovery"):
        assert spec["inputs"][role] == {key: value for key, value in timing["inputs"][role].items()
                                       if key != "candidates_sha256"}
    for field in ("schema", "mode", "study_id", "promotion_allowed", "reference_specification", "parameters", "window", "inputs"):
        changed = deepcopy(spec)
        changed[field] = None
        with pytest.raises(ValueError):
            module.validate_spec(changed, reference)
    for role, field in (("scope_result", "path"), ("scope_result", "sha256"), ("aq", "root"),
                        ("ar_recovery", "ledger_sha256"), ("scan_database", "path"), ("scan_database", "columns")):
        changed = deepcopy(spec)
        changed["inputs"][role][field] = "unregistered"
        with pytest.raises(ValueError):
            module.validate_spec(changed, reference)


def receipt_fixture():
    formations = saved.formation.DATES
    aq_matrix = [{"bas_dd": day, "service": service} for day in formations for service in saved.probe.SERVICES]
    before, after = [], []
    day = date(2020, 1, 1)
    while len(before) < 274:
        if day.strftime("%Y%m%d") not in formations:
            before.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    day = date(2021, 3, 11)
    while len(after) < 565:
        if day.strftime("%Y%m%d") not in formations:
            after.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    ar_days = before + ["20210310"] + after
    ar_matrix = [{"bas_dd": day, "service": service} for day in ar_days for service in saved.probe.SERVICES[:2]]
    def observations(matrix, offset=0, recovery=False):
        return [{"service": item["service"], "requested_bas_dd": item["bas_dd"],
                 "schema_valid": True, "response_sha256": "a" * 64, "at": "2026-10-07T00:00:00+00:00",
                 **({"logical_number": index + offset + 1, "number": index + 1} if recovery else {})}
                for index, item in enumerate(matrix)]
    def ledger(matrix, offset=0, recovery=False):
        return [{**item, "number": index + 1, "event": event,
                 **({"logical_number": index + offset + 1} if recovery else {}),
                 **({"http_status": 200} if event == "response" else {})}
                for index, item in enumerate(matrix) for event in ("attempt", "response")]
    aq = {"status": "formations_audited", "report_persisted": True, "observations": observations(aq_matrix)}
    original = {"status": "failed", "failure": "transport_failure", "requests_attempted": 550,
                "observations": observations(ar_matrix[:549])}
    recovery = {"status": "liquidity_audited", "combined_valid_responses": 1680, "candidates_persisted": True,
                "transport_retries_used": 0, "observations": observations(ar_matrix[549:], 549, True)}
    original_ledger = ledger(ar_matrix[:549]) + [
        {**ar_matrix[549], "number": 550, "event": "attempt"},
        {**ar_matrix[549], "number": 550, "event": "response", "http_status": None}]
    values = {"aq": (aq, {"request_matrix": aq_matrix}, ledger(aq_matrix)),
              "ar_original": (original, {"request_matrix": ar_matrix}, original_ledger),
              "ar_recovery": (recovery, {"request_matrix": ar_matrix, "new_request_matrix": ar_matrix[549:]},
                              ledger(ar_matrix[549:], 549, True))}
    return values, {"history_windows": {"synthetic": ar_days}}


def pinned_receipts(tmp_path, values):
    store, declarations = {}, {}
    for role, (report, started, ledger) in values.items():
        root = tmp_path / role
        report_bytes, started_bytes = saved.encoded(report), saved.encoded(started)
        ledger_bytes = b"".join(saved.encoded(row).replace(b"\n", b"") + b"\n" for row in ledger)
        declarations[role] = {"root": str(root), "report_sha256": saved.digest(report_bytes),
                              "started_sha256": saved.digest(started_bytes), "ledger_sha256": saved.digest(ledger_bytes)}
        store[root / "report.json"] = report_bytes
        store[root / "started.json"] = started_bytes
        store[root / "requests.jsonl"] = ledger_bytes
    return store, declarations


def test_all_nine_receipts_and_complete_transport_failure_ledgers_verified(tmp_path):
    values, plan = receipt_fixture()
    store, declarations = pinned_receipts(tmp_path, values)
    inputs = saved.PinnedInputs(store.__getitem__)
    receipts = module.cache_receipts(inputs, declarations, plan)
    assert len(inputs.manifest) == 9 and all(item["read_status"] == "hash_verified" for item in inputs.manifest)
    assert len(receipts["aq_matrix"]) == 56 and len(receipts["ar_matrix"]) == 1680
    service = saved.probe.SERVICES[1]
    role, number, _, observation, aq = module.raw_coordinate("20210310", service, receipts)
    assert (role, number, observation["logical_number"], observation["number"], aq) == ("ar_recovery", 550, 550, 1, False)
    role, number, _, _, aq = module.raw_coordinate("20210310", saved.probe.SERVICES[0], receipts)
    assert (role, number, aq) == ("ar_original", 549, False)
    role, number, _, _, aq = module.raw_coordinate(saved.formation.DATES[3], service, receipts)
    assert (role, number, aq) == ("aq", 14, True)


@pytest.mark.parametrize("change", ["failure", "original_matrix", "recovery_matrix", "logical", "status", "ledger", "aq"])
def test_receipt_matrix_ledger_and_original_failure_cannot_be_skipped(tmp_path, change):
    values, plan = receipt_fixture()
    if change == "failure":
        values["ar_original"][2][-1]["http_status"] = 200
    elif change == "original_matrix":
        values["ar_original"][1]["request_matrix"] = []
    elif change == "recovery_matrix":
        values["ar_recovery"][1]["new_request_matrix"] = []
    elif change == "logical":
        values["ar_recovery"][2][0]["logical_number"] = 551
    elif change == "status":
        values["ar_recovery"][0]["transport_retries_used"] = 1
    elif change == "ledger":
        values["aq"][2].pop()
    else:
        values["aq"][0]["observations"].pop()
    store, declarations = pinned_receipts(tmp_path, values)
    with pytest.raises(ValueError):
        module.cache_receipts(saved.PinnedInputs(store.__getitem__), declarations, plan)


def test_load_raw_only_two_requested_trading_envelopes_keeps_strings_and_unknown_absence(tmp_path):
    values, history_plan = receipt_fixture()
    store, declarations = pinned_receipts(tmp_path, values)
    inputs = saved.PinnedInputs(store.__getitem__)
    receipts = module.cache_receipts(inputs, declarations, history_plan)
    day = saved.formation.DATES[1]
    raw_days = rows_for_day(day)
    for service in saved.probe.SERVICES[:2]:
        role, number, item, _, aq = module.raw_coordinate(day, service, receipts)
        rows = raw_days[service]
        body = saved.encoded({"OutBlock_1": rows})
        observation = saved.formation._summarize(rows, service, day)
        observation.update(response_sha256=saved.digest(body), at="2026-10-07T00:00:00+00:00")
        receipts[role]["observations"][number - 1] = observation
        store[Path(declarations[role]["root"]) / f"response-{number:02d}-{service}-{day}.json"] = body
    calendar = [day, (date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}") + timedelta(days=3)).strftime("%Y%m%d")]
    plan = {"calendar_dates": calendar, "intersection_code_dates": {
        "111111": [f"{day[:4]}-{day[4:6]}-{day[6:]}"], "999999": [f"{day[:4]}-{day[4:6]}-{day[6:]}"]}}
    result = module.load_raw(inputs, declarations, plan, receipts)
    assert len(inputs.manifest) == 11
    paths = [item["path"] for item in inputs.manifest[9:]]
    assert "response-05-" in paths[0] and "response-06-" in paths[1]
    assert all("candidates" not in path and "bas_info" not in path for path in paths)
    assert result["111111", day]["values"]["close"] == "50000"
    absent = result["999999", day]
    assert absent["values"] is None and absent["row_position"] is None
    assert len(absent["provenance"]["response_receipts"]) == 2
    assert absent["provenance"]["retrieved_at"] == "unknown"
    first_raw = Path(paths[0])
    store[first_raw] = b"not the pinned body"
    with pytest.raises(ValueError, match="hash mismatch"):
        module.load_raw(inputs, declarations, plan, receipts)
    assert inputs.manifest[-1]["read_status"] == "hash_mismatch"


def test_ar_original_and_recovery_read_logical_four_digit_raw_filenames(tmp_path):
    values, history_plan = receipt_fixture()
    store, declarations = pinned_receipts(tmp_path, values)
    inputs = saved.PinnedInputs(store.__getitem__)
    receipts = module.cache_receipts(inputs, declarations, history_plan)
    for service in saved.probe.SERVICES[:2]:
        role, number, _, _, _ = module.raw_coordinate("20210310", service, receipts)
        rows = rows_for_day("20210310")[service]
        body = saved.encoded({"OutBlock_1": rows})
        observation = saved.formation.summarize_trading_day(rows, service, "20210310")
        observation.update(response_sha256=saved.digest(body), at="2026-10-07T00:00:00+00:00")
        index = number - 1 if role == "ar_original" else number - 550
        if role == "ar_recovery":
            observation.update(number=1, logical_number=550)
        receipts[role]["observations"][index] = observation
        store[Path(declarations[role]["root"]) / f"response-{number:04d}-{service}-20210310.json"] = body
    plan = {"calendar_dates": ["20210310", "20210311"],
            "intersection_code_dates": {"111111": ["2021-03-10"]}}
    module.load_raw(inputs, declarations, plan, receipts)
    assert "response-0549-" in inputs.manifest[-2]["path"]
    assert "response-0550-" in inputs.manifest[-1]["path"]


@pytest.mark.parametrize("change", ["unrequested_cross_market_duplicate", "capitalization_mismatch"])
def test_full_two_market_relation_checks_not_only_requested_rows(monkeypatch, change):
    day = saved.formation.DATES[0]
    rows = rows_for_day(day)
    if change == "unrequested_cross_market_duplicate":
        for service in saved.probe.SERVICES[:2]:
            extra = deepcopy(rows[service][0])
            extra["ISU_CD"] = "999998"
            rows[service].append(extra)
    else:
        rows[saved.probe.SERVICES[1]][0]["MKTCAP"] = "1"
    receipts = {"aq_matrix": [{"service": service, "bas_dd": day} for service in saved.probe.SERVICES],
                "aq": {"observations": [{"response_sha256": "a" * 64}] * 4}, "ar_matrix": []}
    monkeypatch.setattr(saved, "_rows", lambda inputs, root, number, item, obs, role, **kwargs: rows[item["service"]])
    plan = {"calendar_dates": [day, "20190403"], "intersection_code_dates": {"111111": ["2019-04-02"]}}
    with pytest.raises(saved.probe.ProbeRefusal):
        module.load_raw(saved.PinnedInputs(), {"aq": {"root": "/synthetic"}}, plan, receipts)


def scan_plan(requested=None):
    requested = requested or {A: [DAYS[0].isoformat(), DAYS[1].isoformat(), DAYS[-2].isoformat()]}
    return {"calendar_dates": [day.strftime("%Y%m%d") for day in DAYS],
            "intersection_code_dates": requested, "original_code_dates": sum(map(len, requested.values())),
            "uncached_code_dates_count": 0}


def scan_fixture(tmp_path):
    path = database(tmp_path)
    mutate(path, "UPDATE scan_bars SET volume='100'")
    return path


def test_scan_exact_projection_includes_volume_and_2026_dates_with_seven_provenance_fields(tmp_path, monkeypatch):
    path = scan_fixture(tmp_path)
    before = path.read_bytes()
    trace = []
    original = sqlite3.connect
    def connect(*args, **kwargs):
        assert args[0].endswith("?mode=ro") and kwargs["uri"] is True
        connection = original(*args, **kwargs)
        connection.set_trace_callback(trace.append)
        return connection
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    inputs = saved.PinnedInputs()
    result = module.load_scan(path, scan_plan(), inputs, "a" * 64)
    assert path.read_bytes() == before
    assert trace.count("BEGIN") == trace.count("ROLLBACK") == 1
    queries = [query for query in trace if "FROM scan_bars" in query]
    assert len(queries) == 1
    assert queries[0].startswith("SELECT bsop_date,open,high,low,close,volume,turnover FROM scan_bars")
    assert "WHERE code='000001' AND bsop_date IN ('20190102','20190103','20260918')" in queries[0]
    provenance = result["rows"][A, "20260918"]["provenance"]
    assert provenance["available_at"] == "2026-09-21T08:30:00+09:00"
    assert {"observation_date", "available_at", "retrieved_at", "source", "is_final", "data_vintage", "evidence_level"} <= provenance.keys()
    assert provenance["retrieved_at"] == "unknown" and provenance["is_final"] is None
    assert provenance["data_vintage"] is None and provenance["evidence_level"] == "assumed"
    assert provenance["source_public_available_at"] is None
    assert result["typed_snapshot_sha256"] == saved.digest(saved.encoded(result["fingerprint"]))
    assert result["whole_database_hash"] is result["BI_snapshot_equality_checked"] is False
    assert inputs.manifest[0]["read_status"] == "snapshot_consumed"


def test_scope_and_all_progress_checked_before_quotes(tmp_path, monkeypatch):
    path = scan_fixture(tmp_path)
    mutate(path, "UPDATE scan_progress SET status='failed' WHERE code=?", (B,))
    original, reads = sqlite3.connect, []
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        def authorizer(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and table == "scan_bars":
                reads.append(column)
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorizer)
        return connection
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    plan = scan_plan({A: [DAYS[0].isoformat()], B: [DAYS[0].isoformat()]})
    with pytest.raises(ValueError, match="scan_progress"):
        module.load_scan(path, plan, saved.PinnedInputs(), "a" * 64)
    assert reads == []
    bad = scan_plan({A: ["2019-01-01"]})
    with pytest.raises(ValueError, match="outside"):
        module.load_scan(path, bad, saved.PinnedInputs(), "a" * 64)
    assert reads == []


def test_fingerprint_preserves_invalid_types_null_absence_progress_and_scope_not_unrequested_rows(tmp_path):
    path = scan_fixture(tmp_path)
    def load(scope="a" * 64):
        return module.load_scan(path, scan_plan(), saved.PinnedInputs(), scope)
    first = load()["typed_snapshot_sha256"]
    mutate(path, "UPDATE scan_bars SET close='unrequested' WHERE code=?", (B,))
    mutate(path, "UPDATE scan_bars SET close='unrequested' WHERE code=? AND bsop_date=?", (A, DAYS[4].strftime("%Y%m%d")))
    assert load()["typed_snapshot_sha256"] == first
    hashes = [first]
    for value in ("100.0", 100, 100.0, b"100", None):
        mutate(path, "UPDATE scan_bars SET volume=? WHERE code=? AND bsop_date=?", (value, A, "20190102"))
        current = load()
        hashes.append(current["typed_snapshot_sha256"])
        assert saved.encoded(current["rows"][A, "20190102"])
    mutate(path, "DELETE FROM scan_bars WHERE code=? AND bsop_date=?", (A, "20190102"))
    result = load()
    hashes.append(result["typed_snapshot_sha256"])
    assert result["rows"][A, "20190102"]["values"] is None
    hashes.append(load("b" * 64)["typed_snapshot_sha256"])
    mutate(path, "UPDATE scan_progress SET fetched_at='2026-10-10T00:00:00+00:00' WHERE code=?", (A,))
    hashes.append(load()["typed_snapshot_sha256"])
    assert len(set(hashes)) == len(hashes)


def values(**changes):
    result = {"open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000", "turnover": "100000"}
    result.update(changes)
    return result


def comparison(raw, adjusted):
    plan = scan_plan({A: [DAYS[0].isoformat()]})
    raw_row = {} if raw is None else {(A, "20190102"): {"values": raw, "service": "synthetic",
                "row_position": 0, "logical_number": 1, "provenance": {"retrieved_at": "unknown"}}}
    scan = {"rows": {(A, "20190102"): {"values": adjusted, "typed_values": [], "provenance": {"retrieved_at": "unknown"}}}}
    return module.compare_rows(plan, raw_row, scan)


def test_grouped_source_numbers_compare_exactly_without_changing_source_strings():
    raw = values(open="50,000", high="51,000", low="49,000", close="50,000",
                 volume="9,007,199,254,740,993", turnover="450,359,962,737,049,650,000")
    scan = {field: value.replace(",", "") for field, value in raw.items()}
    rows, summary = comparison(raw, scan)
    assert rows[0]["raw"]["values"] == raw
    assert rows[0]["raw"]["state"] == "positive_ohlc"
    assert all(value["equal"] == 1 for value in summary["field_equality"].values())
    assert rows[0]["close_factor"]["denominator"] == "50,000"
    assert rows[0]["close_factor"]["numerator"] == "50000"
    assert rows[0]["close_factor"]["unit"] is True
    assert rows[0]["uniform_ohlc_factor"] is True


@pytest.mark.parametrize("amount", ["1,,000", "1,00", ",100", "100,", "1234,000", "1e3", "1_000"])
def test_invalid_numeric_syntax_is_not_silently_normalized(amount):
    for raw, scan in [(values(volume=amount), values()), (values(), values(volume=amount))]:
        rows, summary = comparison(raw, scan)
        assert rows[0]["field_equality"]["volume"] is None
        assert summary["field_equality"]["volume"]["uncomparable"] == 1


def test_exact_factor_strings_and_crossproducts_do_not_round_even_at_low_decimal_precision():
    raw = values(open="100000000000000000000000000000001", high="100000000000000000000000000000003",
                 low="100000000000000000000000000000000", close="100000000000000000000000000000002",
                 volume="9007199254740993000001", turnover="123456789012345678901234567890123456789")
    scan = {field: str(int(value) * 2) if field in module.FIELDS[:4] else value for field, value in raw.items()}
    with localcontext() as context:
        context.prec = 2
        rows, summary = comparison(raw, scan)
    assert rows[0]["close_factor"] == {"numerator": scan["close"], "denominator": raw["close"],
        "orientation": "stored_scan_close / official_raw_close", "unit": False}
    assert rows[0]["uniform_ohlc_factor"] is True
    assert summary["nonunit_positive_close_pairs"] == summary["uniform_ohlc_factor_pairs"] == 1
    assert summary["field_equality"]["volume"]["equal"] == summary["field_equality"]["turnover"]["equal"] == 1
    scan["high"] = str(int(scan["high"]) + 1)
    with localcontext() as context:
        context.prec = 2
        rows, summary = comparison(raw, scan)
    assert rows[0]["uniform_ohlc_factor"] is False
    assert summary["nonuniform_ohlc_factor_pairs"] == 1


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "", " 1", "1 ", "unknown", 1, 1.0, {"sqlite_storage": ["blob", "31"]}])
def test_invalid_volume_turnover_and_prices_remain_explicit(amount):
    for field in ("close", "volume", "turnover"):
        rows, summary = comparison(values(), values(**{field: amount}))
        assert rows[0]["scan"]["state"] == "invalid"
        assert "invalid:" + field in rows[0]["scan"]["issues"]
        assert summary["field_equality"][field]["uncomparable"] == 1
        assert rows[0]["field_equality"][field] is None


def test_missing_null_zero_and_frozen_do_not_coalesce_and_individual_fields_keep_denominators():
    rows, summary = comparison(None, values(turnover=None))
    assert rows[0]["raw"]["state"] == "missing" and rows[0]["scan"]["state"] == "null"
    assert summary["positive_close_pairs"] == 0 and summary["uncomparable_close_pairs"] == 1
    assert rows[0]["close_factor"] is None
    rows, summary = comparison(values(), values(turnover=None))
    assert summary["field_equality"]["close"]["equal"] == 1
    assert summary["field_equality"]["turnover"]["uncomparable"] == 1
    zeros = values(open="0", high="0", low="0", close="0", volume="0", turnover="0")
    rows, summary = comparison(zeros, zeros)
    assert rows[0]["raw"]["state"] == "zero_ohlc" and summary["positive_close_pairs"] == 0
    assert summary["field_equality"]["close"]["equal"] == 1
    rows, _ = comparison(values(open="0", high="0", low="0"), values())
    assert rows[0]["raw"]["state"] == "zero_ohlc"
    assert rows[0]["uniform_ohlc_factor"] is None
    frozen = values(open="100", high="100", low="100", close="100", volume="0", turnover="0")
    rows, summary = comparison(frozen, frozen)
    assert rows[0]["raw"]["state"] == "frozen_zero_turnover"
    assert summary["positive_close_pairs"] == 1 and summary["nonunit_positive_close_pairs"] == 0
    rows, summary = comparison(values(), values(high="99"))
    assert "invalid:ohlc_bounds" in rows[0]["scan"]["issues"]
    assert summary["uncomparable_close_pairs"] == 1


def runner_fixture(tmp_path, monkeypatch):
    root, private, evidence = tmp_path / "repo", tmp_path / "private", tmp_path / "evidence"
    root.mkdir()
    private.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    spec = {"study_id": module.SCHEMA, "parameters": {}, "window": saved.WINDOW,
            "inputs": {"scope_result": {"path": str(evidence / "scope.json"), "sha256": "0" * 64},
                       "scan_database": {"path": str(evidence / "scan.sqlite3"), "mode": "ro", "columns": list(module.READ_COLUMNS)}}}
    sources = {"files": [{"path": "python/synthetic.py", "sha256": "a" * 64}], "code_version": "a" * 40}
    monkeypatch.chdir(root)
    monkeypatch.setattr(saved, "freeze_sources", lambda *args, **kwargs: (sources, b"{}", spec))
    return root, private / "run", tmp_path / "runs.jsonl", spec, sources


def test_durable_start_and_source_snapshots_precede_first_input_and_hash_failure_is_preserved(tmp_path, monkeypatch):
    root, output, runs, _, _ = runner_fixture(tmp_path, monkeypatch)
    attempted = []
    def reader(path):
        attempted.append(path)
        assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started"]
        assert (output / "specification.json").exists() and (output / "source-manifest.json").exists()
        return b"{}"
    with pytest.raises(ValueError, match="hash mismatch"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert len(attempted) == 1
    assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started", "failed"]
    failure = json.loads((output / "failure.json").read_text())
    assert failure["automatic_retries"] == 0 and failure["partial_package_preserved"] is True
    assert failure["actual_inputs_attempted"][0]["read_status"] == "hash_mismatch"
    assert json.loads((output / "input-manifest.json").read_text()) == failure["actual_inputs_attempted"]
    assert not (output / "result.json").exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_read_scope_fsynced_before_db_and_complete_private_audit_precedes_result(tmp_path, monkeypatch):
    database_dir = tmp_path / "database-input"
    database_dir.mkdir()
    path = scan_fixture(database_dir)
    root, output, runs, spec, _ = runner_fixture(tmp_path, monkeypatch)
    spec["inputs"]["scope_result"]["sha256"] = saved.digest(b"{}")
    spec["inputs"]["scan_database"]["path"] = str(path)
    plan = scan_plan()
    monkeypatch.setattr(module, "plan_scope", lambda *args: plan)
    monkeypatch.setattr(module, "cache_receipts", lambda *args: {})
    monkeypatch.setattr(module, "load_raw", lambda *args: {})
    original = sqlite3.connect
    def connect(*args, **kwargs):
        assert (output / "read-scope.json").exists()
        scope = json.loads((output / "read-scope.json").read_text())
        assert scope["input_manifest_before_database"][0]["read_status"] == "hash_verified"
        assert not (output / "rows-audit.json").exists() and not (output / "result.json").exists()
        assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started"]
        return original(*args, **kwargs)
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=lambda path: b"{}")
    result = json.loads((output / "result.json").read_text())
    audit = json.loads((output / "rows-audit.json").read_text())
    assert result["rows_audit_sha256"] == saved.digest((output / "rows-audit.json").read_bytes())
    assert result["read_scope_sha256"] == saved.digest((output / "read-scope.json").read_bytes())
    assert audit["typed_snapshot_sha256"] == saved.digest(saved.encoded(audit["fingerprint"]))
    assert len(audit["rows"]) == 3
    assert record["result"]["result_sha256"] == saved.digest((output / "result.json").read_bytes())
    assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started", "completed"]
    for key in ("returns_computed", "holding_selection_executed", "historical_eligibility_certified", "price_basis_certified",
                "identity_period_created", "actions_applied", "BI_turnover_replaced", "BI_failures_reused", "promotion_allowed"):
        assert result[key] is False
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == result["new_source_requests"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_existing_exclusive_output_preserves_bytes_and_makes_zero_reads(tmp_path, monkeypatch):
    root, output, runs, _, _ = runner_fixture(tmp_path, monkeypatch)
    output.mkdir(mode=0o700)
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"original")
    attempted = []
    with pytest.raises(FileExistsError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda path: attempted.append(path))
    assert attempted == [] and list(output.iterdir()) == [sentinel] and sentinel.read_bytes() == b"original"


def test_source_drift_preserves_row_audit_and_failure_without_result(tmp_path, monkeypatch):
    root, output, runs, spec, sources = runner_fixture(tmp_path, monkeypatch)
    manifests = iter([sources, {**sources, "code_version": "b" * 40}])
    monkeypatch.setattr(saved, "freeze_sources", lambda *args, **kwargs: (next(manifests), b"{}", spec))
    def evaluate(spec, inputs, directory):
        saved.write_exclusive(directory / "rows-audit.json", b"{}")
        return {"status": "cached_price_parity_audited", "summary": {}}
    monkeypatch.setattr(module, "evaluate_inputs", evaluate)
    with pytest.raises(ValueError, match="source/runtime changed"):
        module.run_preflight(root, root / "spec.json", output, runs)
    assert (output / "rows-audit.json").exists() and (output / "failure.json").exists()
    assert not (output / "result.json").exists()


@pytest.mark.parametrize("alias", ["input_log", "source_log", "db_output", "checkout_output", "output_log"])
def test_boundaries_refused_before_logging_or_actual_reads(tmp_path, monkeypatch, alias):
    root, output, runs, spec, _ = runner_fixture(tmp_path, monkeypatch)
    if alias == "input_log":
        runs = Path(spec["inputs"]["scope_result"]["path"])
    elif alias == "source_log":
        runs = root / "python/synthetic.py"
    elif alias == "db_output":
        output = Path(spec["inputs"]["scan_database"]["path"]).parent / "run"
    elif alias == "checkout_output":
        output = root / "output"
    else:
        runs = output / "runs.jsonl"
    attempted = []
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda path: attempted.append(path))
    assert attempted == [] and not output.exists() and not runs.exists()
