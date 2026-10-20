"""Synthetic-only fixed snapshot acquisition, timing and row-presence checks."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from data import krx_openapi_probe as probe
from research import experiment_log
from research import intraday_universe_sources as source


def _rows(day):
    rows = {service: [] for service in source.SERVICES}
    for index in range(24):
        market_index = int(index >= 12)
        code, cap = f"{(index + 1) * 10:06d}", str(1000 - index)
        trade = {field: "0" for field in probe.TRADE_FIELDS}
        trade.update(BAS_DD=day, ISU_CD=code, ISU_NM="synthetic", MKT_NM=("KOSPI", "KOSDAQ")[market_index],
                     SECT_TP_NM="synthetic", TDD_CLSPRC=cap, MKTCAP=cap, LIST_SHRS="1")
        basic = {field: "synthetic" for field in probe.BASIC_FIELDS}
        basic.update(ISU_CD="KR7" + code + "000", ISU_SRT_CD=code, LIST_DD="20200101",
                     MKT_TP_NM=trade["MKT_NM"], SECUGRP_NM="주권", KIND_STKCERT_TP_NM="보통주", LIST_SHRS="1")
        rows[source.SERVICES[market_index]].append(trade)
        rows[source.SERVICES[market_index + 2]].append(basic)
    return rows


def _evidence():
    return {service: {"sha256": str(index) * 64, "retrieved_at": "2026-10-20T00:00:00+00:00"}
            for index, service in enumerate(source.SERVICES)}


def _calendar_dates():
    days, day = [], date(2025, 8, 1)
    while day <= date(2026, 9, 23):
        if day.weekday() < 5 and day != date(2025, 12, 31):
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _database(path, days):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE klines(symbol TEXT, interval TEXT, open_time_ms INTEGER, fetched_at TEXT, close TEXT)")
        connection.executemany("INSERT INTO klines VALUES('KRX-INDEX:0001','1d',?,'synthetic','unread')", [
            (int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000),) for day in days])
        connection.execute("INSERT INTO klines VALUES('KRX:000010','1m',0,'synthetic','never read')")
    return path


def _inventory(path):
    rows = [{"symbol": "KRX:000010", "interval": "1m", "kst_date": "2025-09-04", "row_count": 2,
             "timestamp_second_offset_counts": {"0": 1, "17": 1}}]
    summary = {"mode": "discovery-metadata-only", "window": source.WINDOW,
               "row_count": 2, "symbol_count": 1, "kst_date_count": 1, "aggregate_row_count": 1}
    metadata = {"summary": summary, "rows": rows}
    payload = {"study_id": "intraday-scope-inventory-v1", "inventory": {
        **metadata, "inventory_sha256": hashlib.sha256(probe.canonical(metadata)).hexdigest()}}
    path.write_bytes(probe.canonical(payload))
    return path


class _Response:
    status = 200

    def __init__(self, url, body):
        self.url, self.body = url, body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.body[:limit]


@pytest.fixture
def registered(tmp_path, monkeypatch):
    spec = json.loads(source.SPECIFICATION_PATH.read_bytes())
    database = _database(tmp_path / "synthetic.db", _calendar_dates())
    inventory = _inventory(tmp_path / "inventory.json")
    output, log, spec_path = tmp_path / "evidence", tmp_path / "trials.jsonl", tmp_path / "spec.json"
    paths = {"source_database": str(database), "inventory_path": str(inventory),
             "output_directory": str(output), "canonical_runs_path": str(log)}
    spec.update(paths, inventory_file_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest())
    spec_path.write_bytes(probe.canonical(spec))
    monkeypatch.setattr(source, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(source, "INVENTORY_SHA256", spec["inventory_file_sha256"])
    monkeypatch.setattr(source, "SPECIFICATION_PATH", spec_path)
    monkeypatch.setattr(probe, "credential", lambda: "synthetic-test-secret")
    calls, sleeps = [], []

    class Opener:
        def open(self, request, timeout):
            service = request.full_url.split("/")[-1].split("?")[0]
            day = request.full_url.split("basDd=")[1]
            calls.append((day, service, timeout))
            return _Response(request.full_url, probe.canonical({"OutBlock_1": _rows(day)[service]}))

    monkeypatch.setattr(probe, "make_opener", Opener)
    monkeypatch.setattr(source.time, "sleep", sleeps.append)
    return {"db_path": database, "inventory_path": inventory, "output_directory": output,
            "runs_path": log, "specification_path": spec_path}, calls, sleeps


def test_full_synthetic_run_keeps_fixed_requests_raw_hashes_and_missingness(registered):
    args, calls, sleeps = registered
    record = source.run_sources(**args)
    report = json.loads((args["output_directory"] / "report.json").read_bytes())
    assert record["status"] == "completed"
    assert calls == [(day, service, 20) for day in source.DATES for service in source.SERVICES]
    assert sleeps == [1] * 51
    assert len(report["responses"]) == 52
    for entry in report["responses"]:
        body = (args["output_directory"] / entry["filename"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
        assert len(body) == entry["bytes"]
    for variant in report["variants"].values():
        summary = variant["summary"]
        assert summary["target_symbol_sessions"] == summary["reference_sessions"] * 20
        assert summary["stored_symbol_sessions"] == 1
        assert summary["missing_symbol_sessions"] == summary["target_symbol_sessions"] - 1
        assert sum(month["missing_symbol_sessions"] for month in summary["by_month"].values()) == summary["missing_symbol_sessions"]
        first = variant["days"][0]
        assert first["targets"][0]["stored_row_count"] == 2
        assert first["targets"][1]["stored_row_count"] is None
        assert first["targets"][1]["state"] == "no_stored_rows_unknown_reason"
        assert first["is_final"] is None and first["evidence_level"] == "assumed"
        assert all(day["available_at"] <= day["selected_at"] for day in variant["days"])
    assert report["inventory_observed_union_dates"] == 1
    assert all(report[key] is False for key in source._FLAGS)
    assert [entry["status"] for entry in experiment_log.read_records(args["runs_path"])] == ["started", "completed"]


def test_integer_precision_and_cutoff_ties_do_not_use_float():
    rows = _rows(source.DATES[0])
    huge = 10 ** 65
    for service in source.SERVICES[:2]:
        for row in rows[service]:
            row.update(TDD_CLSPRC=str(huge), MKTCAP=str(huge))
    rows[source.SERVICES[1]][-1].update(TDD_CLSPRC=str(huge + 1), MKTCAP=str(huge + 1))
    result = source.rank_snapshot(source.DATES[0], rows, _evidence())
    assert result["members"][0]["symbol"] == "KRX:000240"
    assert result["members"][1]["symbol"] == "KRX:000010"
    assert result["rank_21"]["symbol"] == "KRX:000200"
    assert result["cutoff_tied_candidate_count"] == 23


@pytest.mark.parametrize("field,value", [("MKTCAP", "1.0"), ("TDD_CLSPRC", "0.5"),
    ("LIST_SHRS", "1.0"), ("MKTCAP", "0"), ("MKTCAP", ""), ("MKTCAP", "NaN"),
    ("MKTCAP", "1,00"), ("MKTCAP", "100000"), ("LIST_SHRS", "-1")])
def test_missing_fractional_zero_or_inconsistent_rank_inputs_fail(field, value):
    rows = _rows(source.DATES[0])
    rows[source.SERVICES[0]][0][field] = value
    with pytest.raises((source.SourcesRefusal, probe.ProbeRefusal)):
        source.rank_snapshot(source.DATES[0], rows, _evidence())


@pytest.mark.parametrize("change", ["membership", "cross_market", "shares", "date", "market", "duplicate"])
def test_one_to_one_dated_market_join_is_required(change):
    rows = _rows(source.DATES[0])
    if change == "membership":
        rows[source.SERVICES[2]].pop()
    elif change == "cross_market":
        rows[source.SERVICES[1]][0]["ISU_CD"] = rows[source.SERVICES[0]][0]["ISU_CD"]
    elif change == "shares":
        rows[source.SERVICES[2]][0]["LIST_SHRS"] = "2"
    elif change == "date":
        rows[source.SERVICES[0]][0]["BAS_DD"] = "20260923"
    elif change == "market":
        rows[source.SERVICES[0]][0]["MKT_NM"] = "KOSDAQ"
    else:
        rows[source.SERVICES[2]].append(deepcopy(rows[source.SERVICES[2]][0]))
    with pytest.raises((source.SourcesRefusal, probe.ProbeRefusal)):
        source.rank_snapshot(source.DATES[0], rows, _evidence())


@pytest.mark.parametrize("field,value", [("SECUGRP_NM", "ETF"), ("KIND_STKCERT_TP_NM", "우선주"),
                                       ("ISU_CD", "HK7000010000"), ("ISU_CD", "KR7000011000")])
def test_source_labels_domestic_prefix_and_issue_type_filter(field, value):
    rows = _rows(source.DATES[0])
    rows[source.SERVICES[2]][0][field] = value
    result = source.rank_snapshot(source.DATES[0], rows, _evidence())
    assert result["candidate_count"] == 23
    assert "KRX:000010" not in {row["symbol"] for row in result["members"]}


def test_d2_keeps_old_snapshot_at_month_transition_and_availability_equality():
    calendar = [{"date": day} for day in ["2025-08-29", "2025-09-01", "2025-09-02",
                                         "2025-09-30", "2025-10-01", "2025-10-02"]]
    snapshots = [{"observation_date": day, "members": [{"symbol": symbol, "rank": 1}], "sources": _evidence()}
                 for day, symbol in [("2025-08-29", "KRX:000010"), ("2025-09-30", "KRX:000020")]]
    variants = source.join_coverage(calendar, snapshots, {})
    first = {day["session"]: day for day in variants["D+1"]["days"]}
    second = {day["session"]: day for day in variants["D+2"]["days"]}
    assert first["2025-10-01"]["targets"][0]["symbol"] == "KRX:000020"
    assert second["2025-10-01"]["targets"][0]["symbol"] == "KRX:000010"
    assert second["2025-10-02"]["targets"][0]["symbol"] == "KRX:000020"
    assert second["2025-10-02"]["available_at"] == second["2025-10-02"]["selected_at"]


@pytest.mark.parametrize("sql", ["SELECT close FROM klines", "SELECT volume FROM klines",
    "SELECT name FROM sqlite_master", "DELETE FROM klines", "PRAGMA table_info(klines)",
    "SELECT count(*) FROM klines", "ATTACH DATABASE ':memory:' AS other"])
def test_calendar_authorizer_denies_other_columns_operations_and_tables(tmp_path, sql):
    database = _database(tmp_path / "calendar.db", _calendar_dates())
    with source._calendar_connection(database) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(sql).fetchall()


def test_calendar_fixed_scope_and_observed_month_end(tmp_path):
    database = _database(tmp_path / "calendar.db", _calendar_dates() + ["2025-07-31", "2026-09-24"])
    before = database.read_bytes()
    rows = source.read_calendar(database)
    assert rows[0]["date"] == "2025-08-01" and rows[-1]["date"] == "2026-09-23"
    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        stamp = int(datetime(2025, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)
        connection.execute("INSERT INTO klines VALUES('KRX-INDEX:0001','1d',?,'synthetic','unread')", (stamp,))
    with pytest.raises(source.SourcesRefusal, match="snapshot_not_last"):
        source.read_calendar(database)


def test_calendar_budget_and_missing_database_fail(tmp_path, monkeypatch):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        source.read_calendar(missing)
    assert not missing.exists()
    database = _database(tmp_path / "calendar.db", _calendar_dates())
    monkeypatch.setattr(source, "_PROGRESS_STEPS", 1)
    monkeypatch.setattr(source, "_MAX_QUERY_STEPS", 1)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        source.read_calendar(database)


def test_started_is_durable_before_all_data_and_credentials(registered, monkeypatch):
    args, _, _ = registered
    events = []
    for name in ("read_calendar", "read_inventory"):
        original = getattr(source, name)

        def after_start(*values, original=original, name=name):
            assert list(experiment_log.read_records(args["runs_path"]))[-1]["status"] == "started"
            events.append(name)
            return original(*values)

        monkeypatch.setattr(source, name, after_start)

    def credential():
        assert list(experiment_log.read_records(args["runs_path"]))[-1]["status"] == "started"
        events.append("credential")
        return "synthetic-test-secret"

    monkeypatch.setattr(probe, "credential", credential)
    source.run_sources(**args)
    assert events == ["read_calendar", "read_inventory", "credential"]


@pytest.mark.parametrize("mutation", ["window", "maximum", "timing", "count", "float", "extra"])
def test_specification_scope_changes_fail_before_start(registered, mutation):
    args, calls, _ = registered
    path = args["specification_path"]
    spec = json.loads(path.read_bytes())
    if mutation == "window":
        spec["window"]["end"] = "2026-09-24"
    elif mutation == "maximum":
        spec["requests"]["maximum"] = 53
    elif mutation == "timing":
        spec["timing"]["evidence_level"] = "confirmed"
    elif mutation == "count":
        spec["selection"]["count"] = 10
    elif mutation == "float":
        spec["requests"]["maximum"] = 52.0
    else:
        spec["unregistered"] = True
    path.write_bytes(probe.canonical(spec))
    with pytest.raises(source.SourcesRefusal, match="specification_scope"):
        source.run_sources(**args)
    assert not args["runs_path"].exists() and not calls


def test_unregistered_or_existing_output_does_not_read_or_overwrite(registered):
    args, calls, _ = registered
    with pytest.raises(source.SourcesRefusal, match="unregistered"):
        source.run_sources(**{**args, "output_directory": args["output_directory"].with_name("other")})
    args["output_directory"].mkdir()
    marker = args["output_directory"] / "keep"
    marker.write_text("preserved")
    with pytest.raises(probe.ProbeRefusal):
        source.run_sources(**args)
    assert marker.read_text() == "preserved" and not calls
    assert [row["status"] for row in experiment_log.read_records(args["runs_path"])] == ["started", "failed"]


@pytest.mark.parametrize("failure", ["network", "secret", "escaped_secret", "schema", "oversize"])
def test_first_http_failure_stops_with_durable_attempt_and_sanitized_logs(registered, monkeypatch, failure):
    args, _, _ = registered
    secret = "synthetic-test-secret"
    calls = []

    class Failing:
        def open(self, request, timeout):
            attempt = json.loads((args["output_directory"] / "requests.jsonl").read_text().splitlines()[-1])
            assert attempt["event"] == "attempt" and attempt["number"] == 1
            calls.append(1)
            if failure == "network":
                raise OSError(secret)
            if failure == "secret":
                body = probe.canonical({"OutBlock_1": [{"echo": secret}]})
            elif failure == "escaped_secret":
                body = ('{"OutBlock_1":[{"echo":"' + ''.join('\\u%04x' % ord(char) for char in secret) + '"}]}').encode()
            elif failure == "schema":
                body = probe.canonical({"OutBlock_1": [{"bad": "row"}]})
            else:
                body = b" " * (source.REQUESTS["maximum_response_bytes"] + 1)
            return _Response(request.full_url, body)

    monkeypatch.setattr(probe, "make_opener", Failing)
    with pytest.raises(source.SourcesRefusal) as error:
        source.run_sources(**args)
    assert secret not in str(error.value) and calls == [1]
    assert not (args["output_directory"] / "report.json").exists()
    logs = args["runs_path"].read_text() + (args["output_directory"] / "requests.jsonl").read_text()
    assert secret not in logs
    assert [row["status"] for row in experiment_log.read_records(args["runs_path"])] == ["started", "failed"]
    attempts = [json.loads(line) for line in (args["output_directory"] / "requests.jsonl").read_text().splitlines()]
    assert [row["event"] for row in attempts] == ["attempt", "failed"]
    if failure in {"secret", "escaped_secret"}:
        assert not list(args["output_directory"].glob("response-*.json"))
    if failure == "schema":
        assert attempts[-1]["raw_response"].startswith("response-01-")
        assert attempts[-1]["sha256"] == hashlib.sha256(
            (args["output_directory"] / attempts[-1]["raw_response"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("changed", ["specification_path", "inventory_path", "report"])
def test_completion_pin_change_leaves_partials_and_failed_canonical_record(registered, changed):
    args, _, _ = registered

    def mutate():
        path = args["output_directory"] / "report.json" if changed == "report" else args[changed]
        path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(source.SourcesRefusal, match="changed"):
        source.run_sources(**args, before_completion=mutate)
    assert (args["output_directory"] / "report.json").exists()
    assert [row["status"] for row in experiment_log.read_records(args["runs_path"])] == ["started", "failed"]


def test_inventory_hash_failure_is_logged_before_any_request(registered):
    args, calls, _ = registered
    args["inventory_path"].write_bytes(b"not the registered inventory")
    with pytest.raises(source.SourcesRefusal, match="inventory_file_hash"):
        source.run_sources(**args)
    assert not calls
    assert [row["status"] for row in experiment_log.read_records(args["runs_path"])] == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["duplicate", "count", "offset", "calendar_gap", "summary", "digest"])
def test_pinned_inventory_invalid_structure_never_produces_partial_coverage(registered, monkeypatch, mutation):
    args, calls, _ = registered
    payload = json.loads(args["inventory_path"].read_bytes())
    inventory = payload["inventory"]
    row = inventory["rows"][0]
    if mutation == "duplicate":
        inventory["rows"].append(deepcopy(row))
    elif mutation == "count":
        row["row_count"] = 0
    elif mutation == "offset":
        row["timestamp_second_offset_counts"] = {"60": 2}
    elif mutation == "calendar_gap":
        row["kst_date"] = "2025-09-06"
    elif mutation == "summary":
        inventory["summary"]["row_count"] += 1
    inventory["inventory_sha256"] = hashlib.sha256(probe.canonical(
        {"summary": inventory["summary"], "rows": inventory["rows"]})).hexdigest()
    if mutation == "digest":
        inventory["inventory_sha256"] = "0" * 64
    args["inventory_path"].write_bytes(probe.canonical(payload))
    digest = hashlib.sha256(args["inventory_path"].read_bytes()).hexdigest()
    monkeypatch.setattr(source, "INVENTORY_SHA256", digest)
    specification = json.loads(args["specification_path"].read_bytes())
    specification["inventory_file_sha256"] = digest
    args["specification_path"].write_bytes(probe.canonical(specification))
    with pytest.raises(source.SourcesRefusal, match="inventory"):
        source.run_sources(**args)
    assert not calls and not (args["output_directory"] / "report.json").exists()


def test_unwritable_canonical_start_prevents_all_data_reads(registered, monkeypatch):
    args, calls, _ = registered
    args["runs_path"].mkdir()
    monkeypatch.setattr(source, "read_calendar", lambda *_: pytest.fail("read before durable start"))
    with pytest.raises(IsADirectoryError):
        source.run_sources(**args)
    assert not calls and not args["output_directory"].exists()


def test_attempt_evidence_failure_prevents_request(registered, monkeypatch):
    args, calls, _ = registered
    original = probe.persist

    def refuse_attempt(path, *values, **kwargs):
        if path.name == "requests.jsonl":
            raise OSError("synthetic disk failure")
        return original(path, *values, **kwargs)

    monkeypatch.setattr(probe, "persist", refuse_attempt)
    with pytest.raises(OSError):
        source.run_sources(**args)
    assert not calls
    assert [row["status"] for row in experiment_log.read_records(args["runs_path"])] == ["started", "failed"]


def test_total_response_budget_stops_without_retry(tmp_path, monkeypatch):
    output = probe.prepare_output(tmp_path / "private")
    monkeypatch.setattr(probe, "credential", lambda: "synthetic-test-secret")
    monkeypatch.setattr(source, "REQUESTS", {**source.REQUESTS, "maximum_total_response_bytes": 1})
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(1)
            return _Response(request.full_url, b"too big")

    monkeypatch.setattr(probe, "make_opener", Opener)
    with pytest.raises(source.SourcesRefusal, match="response_byte_limit"):
        source._acquire(output)
    assert calls == [1]


def test_report_durability_failure_never_completes(registered, monkeypatch):
    args, _, _ = registered
    original = probe.persist

    def refuse_report(path, *values, **kwargs):
        if path.name == "report.json":
            raise OSError("synthetic fsync failure")
        return original(path, *values, **kwargs)

    monkeypatch.setattr(probe, "persist", refuse_report)
    with pytest.raises(OSError):
        source.run_sources(**args)
    assert [row["status"] for row in experiment_log.read_records(args["runs_path"])] == ["started", "failed"]
