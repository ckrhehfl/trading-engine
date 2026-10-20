"""Synthetic-only verification of the bounded metadata inventory."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

import pytest

from research import experiment_log
from research import intraday_scope_inventory as inventory


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _database(path: Path, rows: list[tuple] | None = None) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE klines (
                symbol TEXT, interval TEXT, open_time_ms INTEGER,
                open TEXT, high TEXT, low TEXT, close TEXT,
                volume TEXT, fetched_at TEXT, quote_volume TEXT
            )
        """)
        connection.executemany(
            "INSERT INTO klines VALUES (?, ?, ?, 'PRIVATE-PRICE', 'PRIVATE-PRICE', "
            "'PRIVATE-PRICE', 'PRIVATE-PRICE', ?, ?, ?)", rows or [],
        )
    return path


def _specification(path: Path, **changes) -> Path:
    content = {
        "id": "intraday-scope-inventory-v1", "mode": "discovery-metadata-only",
        "window": {
            "symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23",
        },
        "allow_columns": ["symbol", "interval", "open_time_ms", "fetched_at", "volume", "quote_volume"],
        "price_or_return_access": False, "api_requests": 0,
        "maximum_aggregate_rows": 200000, "query_wall_seconds": 60,
        "source_database": str(path.parent / "synthetic.db"),
        "canonical_runs_path": str(path.parent / "trials.jsonl"),
        "output_directory": str(path.parent),
        **changes,
    }
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _run(tmp_path: Path, database: Path, specification: Path | None = None) -> dict:
    return inventory.run_inventory(
        db_path=database,
        specification_path=specification or _specification(
            tmp_path / "spec.json", source_database=str(database),
        ),
        output_path=tmp_path / "inventory.json", runs_path=tmp_path / "trials.jsonl",
    )


def test_kst_boundaries_exclude_reserved_future_and_other_series(tmp_path):
    start = _ms("2025-09-04T00:00:00+09:00")
    end = _ms("2026-09-24T00:00:00+09:00")
    stamps = [start - 1, start, end - 1, end, _ms("2018-12-31T09:00:00+09:00")]
    rows = [("KRX:000001", "1m", stamp, "0", "fetched", None) for stamp in stamps]
    rows += [
        ("KRX:000001", "1d", start, "0", "fetched", None),
        ("KRX-RAW:000001", "1m", start, "0", "fetched", None),
        ("BTC-USDT", "1m", start, "0", "fetched", None),
    ]
    result = inventory.collect_inventory(_database(tmp_path / "synthetic.db", rows))
    assert inventory.START_MS == start
    assert inventory.END_EXCLUSIVE_MS == end
    assert result["summary"]["row_count"] == 2
    assert [row["kst_date"] for row in result["rows"]] == ["2025-09-04", "2026-09-23"]
    assert result["rows"][0]["first_open_time_ms"] == start
    assert result["rows"][1]["last_open_time_ms"] == end - 1
    assert "current top-20" in result["summary"]["limitation"]


def test_presence_counts_offsets_and_digest_do_not_disclose_amounts(tmp_path):
    base = _ms("2025-09-04T09:00:00+09:00")
    rows = [
        ("KRX:000001", "1m", base, None, "later", ""),
        ("KRX:000001", "1m", base + 30_000, "", "earlier", None),
        ("KRX:000001", "1m", base + 60_001, "900000000000000001", None, "800000000000000002"),
        ("KRX:000001", "1m", base + 90_000, "0", "middle", "0"),
        ("KRX:000002", "1m", base, "7", "other", "9"),
    ]
    database = _database(tmp_path / "synthetic.db", rows)
    result = inventory.collect_inventory(database)
    row = result["rows"][0]
    assert row["row_count"] == 4
    for field in ("volume", "quote_volume"):
        assert row[f"{field}_null_count"] == 1
        assert row[f"{field}_empty_count"] == 1
        assert row[f"{field}_populated_count"] == 2
    assert row["timestamp_second_offset_counts"] == {"0": 2, "30": 2}
    assert row["subsecond_timestamp_count"] == 1
    assert (row["fetched_at_min"], row["fetched_at_max"]) == ("earlier", "middle")
    text = json.dumps(result)
    assert "PRIVATE-PRICE" not in text
    assert "900000000000000001" not in text
    assert "800000000000000002" not in text
    assert result == inventory.collect_inventory(database)
    payload = {"summary": result["summary"], "rows": result["rows"]}
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert result["inventory_sha256"] == hashlib.sha256(normalized).hexdigest()
    # Altering amounts without altering presence leaves the metadata digest unchanged.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE klines SET volume = '123' WHERE volume = '900000000000000001'")
    assert inventory.collect_inventory(database) == result


@pytest.mark.parametrize("sql", [
    "SELECT open FROM klines", "SELECT high FROM klines", "SELECT low FROM klines",
    "SELECT close FROM klines", "SELECT * FROM klines",
    "UPDATE klines SET volume = '42'", "DELETE FROM klines",
    "CREATE TABLE forbidden (value TEXT)", "PRAGMA query_only = OFF",
    "ATTACH DATABASE ':memory:' AS other", "SELECT randomblob(1000)",
])
def test_authorizer_denies_prices_writes_and_unapproved_operations(tmp_path, sql):
    database = _database(tmp_path / "synthetic.db")
    before = database.read_bytes()
    with inventory._readonly_inventory_connection(database) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(sql)
    assert database.read_bytes() == before


def test_read_does_not_change_database_or_create_missing_database(tmp_path):
    database = _database(tmp_path / "synthetic.db")
    before = database.read_bytes()
    mtime = database.stat().st_mtime_ns
    assert inventory.collect_inventory(database)["rows"] == []
    assert database.read_bytes() == before
    assert database.stat().st_mtime_ns == mtime
    missing = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        inventory.collect_inventory(missing)
    assert not missing.exists()


def test_progress_budget_interrupts_query(tmp_path, monkeypatch):
    base = _ms("2025-09-04T09:00:00+09:00")
    database = _database(tmp_path / "synthetic.db", [
        ("KRX:000001", "1m", base + offset * 60_000, "0", "fetched", None)
        for offset in range(500)
    ])
    monkeypatch.setattr(inventory, "_PROGRESS_STEPS", 1)
    monkeypatch.setattr(inventory, "_MAX_QUERY_STEPS", 1)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        inventory.collect_inventory(database)


def test_aggregate_limit_fails_without_partial_success(tmp_path, monkeypatch):
    base = _ms("2025-09-04T09:00:00+09:00")
    database = _database(tmp_path / "synthetic.db", [
        ("KRX:000001", "1m", base, "0", "fetched", None),
        ("KRX:000002", "1m", base, "0", "fetched", None),
    ])
    monkeypatch.setattr(inventory, "MAX_AGGREGATE_ROWS", 1)
    with pytest.raises(ValueError, match="aggregate row limit"):
        inventory.collect_inventory(database)


def test_started_is_durable_before_database_callback_and_completion_has_receipts(tmp_path, monkeypatch):
    database = _database(tmp_path / "synthetic.db")
    specification = _specification(tmp_path / "spec.json", source_commit="registered-pin")
    original = inventory.collect_inventory
    original_stat = inventory._database_stat
    observed: list[str] = []

    def assert_started():
        records = list(experiment_log.read_records(tmp_path / "trials.jsonl"))
        assert [record["status"] for record in records] == ["started"]
        observed.append("started-before-access")

    def read_after_start(path):
        assert_started()
        return original(path)

    def stat_after_start(path):
        assert_started()
        return original_stat(path)

    monkeypatch.setattr(inventory, "collect_inventory", read_after_start)
    monkeypatch.setattr(inventory, "_database_stat", stat_after_start)
    result = _run(tmp_path, database, specification)
    assert len(observed) == 3
    records = list(experiment_log.read_records(tmp_path / "trials.jsonl"))
    assert [record["status"] for record in records] == ["started", "completed"]
    assert records[0]["run_id"] == records[1]["run_id"]
    assert records[0]["promotion_allowed"] is False
    assert records[0]["promotion_trial_increment"] == 0
    assert records[0]["specification_sha256"] == hashlib.sha256(specification.read_bytes()).hexdigest()
    assert "code_version" in records[0]
    assert result["result"]["database_before"] == result["result"]["database_after"]
    output_bytes = (tmp_path / "inventory.json").read_bytes()
    assert result["result"]["output_sha256"] == hashlib.sha256(output_bytes).hexdigest()
    assert json.loads(output_bytes)["inventory"]["inventory_sha256"] == result["result"]["inventory_sha256"]


def test_malformed_schema_is_logged_failed_and_has_no_output(tmp_path):
    database = tmp_path / "malformed.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        _run(tmp_path, database)
    records = list(experiment_log.read_records(tmp_path / "trials.jsonl"))
    assert [record["status"] for record in records] == ["started", "failed"]
    assert records[1]["error_type"] == "OperationalError"
    assert "error" not in records[1]
    assert not (tmp_path / "inventory.json").exists()


def test_existing_output_is_preserved_and_no_database_read_follows(tmp_path, monkeypatch):
    database = _database(tmp_path / "synthetic.db")
    output = tmp_path / "inventory.json"
    output.write_bytes(b"KEEP")

    def forbidden(_):
        pytest.fail("existing output must be refused before database access")

    monkeypatch.setattr(inventory, "_database_stat", forbidden)
    with pytest.raises(FileExistsError):
        _run(tmp_path, database)
    assert output.read_bytes() == b"KEEP"
    assert [record["status"] for record in experiment_log.read_records(tmp_path / "trials.jsonl")] == ["started", "failed"]


@pytest.mark.parametrize("change", [
    {"id": "other"}, {"mode": "performance"}, {"api_requests": 1},
    {"api_requests": False}, {"price_or_return_access": 0},
    {"allow_columns": ["symbol", "close"]},
    {"window": {"symbol": "KRX:*", "interval": "1m", "start": "2018-01-01", "end": "2026-09-23"}},
])
def test_changed_specification_refused_before_callback(tmp_path, monkeypatch, change):
    specification = _specification(tmp_path / "spec.json", **change)

    def forbidden(_):
        pytest.fail("invalid registration must not read data")

    monkeypatch.setattr(inventory, "collect_inventory", forbidden)
    with pytest.raises(ValueError):
        _run(tmp_path, tmp_path / "absent.db", specification)
    assert not (tmp_path / "trials.jsonl").exists()
    assert not (tmp_path / "absent.db").exists()


def test_log_hardlink_to_database_is_refused_before_writes(tmp_path):
    database = _database(tmp_path / "synthetic.db")
    before = database.read_bytes()
    log = tmp_path / "log.jsonl"
    log.hardlink_to(database)
    with pytest.raises(ValueError, match="must be distinct"):
        inventory.run_inventory(
            db_path=database, specification_path=_specification(tmp_path / "spec.json"),
            output_path=tmp_path / "inventory.json", runs_path=log,
        )
    assert database.read_bytes() == before


def test_cli_failure_does_not_print_database_values(tmp_path, capsys):
    database = tmp_path / "malformed.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    specification = _specification(tmp_path / "spec.json", source_database=str(database))
    with pytest.raises(SystemExit) as failure:
        inventory.main([
            "--db", str(database), "--specification", str(specification),
            "--output", str(tmp_path / "inventory.json"),
            "--runs-path", str(tmp_path / "trials.jsonl"),
        ])
    assert failure.value.code == 1
    assert "inventory failed (OperationalError)" in capsys.readouterr().err


@pytest.mark.parametrize("change", [
    {"source_database": "/tmp/unregistered.db"},
    {"canonical_runs_path": "/tmp/unregistered.jsonl"},
    {"output_directory": "/tmp/unregistered-output"},
    {"maximum_aggregate_rows": 200001}, {"query_wall_seconds": 61},
])
def test_registered_paths_and_limits_cannot_be_overridden(tmp_path, monkeypatch, change):
    database = _database(tmp_path / "synthetic.db")
    specification = _specification(tmp_path / "spec.json", **change)

    def forbidden(_):
        pytest.fail("mismatched paths or limits must fail before database access")

    monkeypatch.setattr(inventory, "collect_inventory", forbidden)
    with pytest.raises(ValueError):
        _run(tmp_path, database, specification)
    assert not (tmp_path / "trials.jsonl").exists()


def test_before_completion_runs_after_output_before_completed_record(tmp_path, monkeypatch):
    database = _database(tmp_path / "synthetic.db")
    specification = _specification(tmp_path / "spec.json")
    observed = []
    synchronized = []
    real_fsync = os.fsync

    def observe_fsync(descriptor):
        if (tmp_path / "inventory.json").exists():
            synchronized.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
        return real_fsync(descriptor)

    monkeypatch.setattr(inventory.os, "fsync", observe_fsync)

    def check_frozen_source():
        assert (tmp_path / "inventory.json").is_file()
        records = list(experiment_log.read_records(tmp_path / "trials.jsonl"))
        assert [record["status"] for record in records] == ["started"]
        if os.name == "posix":
            assert synchronized == ["file", "directory"]
        observed.append("checked")

    result = inventory.run_inventory(
        db_path=database, specification_path=specification,
        output_path=tmp_path / "inventory.json", runs_path=tmp_path / "trials.jsonl",
        before_completion=check_frozen_source,
    )
    assert observed == ["checked"]
    assert result["status"] == "completed"


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is a POSIX durability step")
def test_output_directory_fsync_failure_preserves_output_and_logs_failed(tmp_path, monkeypatch):
    database = _database(tmp_path / "synthetic.db")
    real_fsync = os.fsync

    def fail_output_directory_fsync(descriptor):
        if ((tmp_path / "inventory.json").exists()
                and stat.S_ISDIR(os.fstat(descriptor).st_mode)):
            raise OSError("output directory fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr(inventory.os, "fsync", fail_output_directory_fsync)
    with pytest.raises(OSError, match="output directory fsync failed"):
        _run(tmp_path, database)
    assert json.loads((tmp_path / "inventory.json").read_bytes())["study_id"] == inventory.SPECIFICATION_ID
    records = list(experiment_log.read_records(tmp_path / "trials.jsonl"))
    assert [record["status"] for record in records] == ["started", "failed"]
    assert records[1]["error_type"] == "OSError"


def test_source_validator_failure_retains_output_and_logs_failed(tmp_path):
    database = _database(tmp_path / "synthetic.db")
    specification = _specification(tmp_path / "spec.json")

    def reject_changed_source():
        raise RuntimeError("source pin mismatch")

    with pytest.raises(RuntimeError, match="source pin mismatch"):
        inventory.run_inventory(
            db_path=database, specification_path=specification,
            output_path=tmp_path / "inventory.json", runs_path=tmp_path / "trials.jsonl",
            before_completion=reject_changed_source,
        )
    assert (tmp_path / "inventory.json").is_file()
    assert [record["status"] for record in experiment_log.read_records(tmp_path / "trials.jsonl")] == ["started", "failed"]


def test_changed_specification_before_completion_is_logged_failed(tmp_path):
    database = _database(tmp_path / "synthetic.db")
    specification = _specification(tmp_path / "spec.json")

    def mutate_specification():
        specification.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="changed before completion"):
        inventory.run_inventory(
            db_path=database, specification_path=specification,
            output_path=tmp_path / "inventory.json", runs_path=tmp_path / "trials.jsonl",
            before_completion=mutate_specification,
        )
    assert (tmp_path / "inventory.json").is_file()
    assert [record["status"] for record in experiment_log.read_records(tmp_path / "trials.jsonl")] == ["started", "failed"]
