"""Invented stores only: EC metadata scope, independence and refusal boundaries."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from research import intraday_campaign_inventory as inventory
from research import intraday_scope_inventory as original


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _database(path: Path, rows: list[tuple] | None = None) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE klines (
                symbol TEXT, interval TEXT, open_time_ms INTEGER,
                open TEXT, high TEXT, low TEXT, close TEXT,
                volume TEXT, fetched_at TEXT, quote_volume TEXT,
                PRIMARY KEY (symbol, interval, open_time_ms)
            )
        """)
        connection.executemany(
            "INSERT INTO klines VALUES (?, ?, ?, 'PRIVATE-PRICE', 'PRIVATE-PRICE', "
            "'PRIVATE-PRICE', 'PRIVATE-PRICE', ?, ?, ?)", rows or [],
        )
    return path


def test_fixed_scope_and_explicit_namespaces_with_gaps(tmp_path):
    start = _ms("2025-09-04T00:00:00+09:00")
    end = _ms("2026-09-24T00:00:00+09:00")
    rows = [
        (symbol, "1m", stamp, "0", "fetched", None)
        for symbol in ("KRX:000001", "KRX-INDEX:0001")
        for stamp in (start - 1, start, start + 172800000, end - 1, end)
    ]
    rows += [
        ("KRX:000001", "1d", start, "1", "fetched", "2"),
        ("KRX-RAW:000001", "1m", start, "1", "fetched", "2"),
        ("KRX-INDEX-RAW:0001", "1m", start, "1", "fetched", "2"),
        ("krx:000001", "1m", start, "1", "fetched", "2"),
        ("BTC-USDT", "1m", start, "1", "fetched", "2"),
        ("KRX:000001", "1m", _ms("2018-12-31T09:00:00+09:00"), "1", "fetched", "2"),
    ]
    database = _database(tmp_path / "invented.db", rows)
    result = inventory.collect_verified_inventory(database)
    report = result["inventory"]
    assert report["summary"]["row_count"] == 6
    assert report["summary"]["symbol_count"] == 2
    assert {row["namespace"] for row in report["rows"]} == {"KRX", "KRX-INDEX"}
    assert {row["kst_date"] for row in report["rows"]} == {"2025-09-04", "2025-09-06", "2026-09-23"}
    assert report["rows"][0]["first_open_time_ms"] == start
    assert report["rows"][-1]["last_open_time_ms"] == end - 1
    assert result["verification"]["compared_aggregate_rows"] == 6
    # The prior callable still has exactly its original KRX-only scope.
    assert original.collect_inventory(database)["summary"]["row_count"] == 3


def test_presence_offsets_extrema_and_digest_do_not_disclose_amounts(tmp_path):
    base = _ms("2025-09-04T09:00:00+09:00")
    rows = [
        ("KRX:000001", "1m", base, None, "later", ""),
        ("KRX:000001", "1m", base + 30000, "", "earlier", None),
        ("KRX:000001", "1m", base + 60001, "900000000000000001", None, "800000000000000002"),
        ("KRX:000001", "1m", base + 90000, "0", "middle", "0"),
    ]
    database = _database(tmp_path / "invented.db", rows)
    result = inventory.collect_verified_inventory(database)
    report = result["inventory"]
    row = report["rows"][0]
    assert row["row_count"] == 4
    assert row["timestamp_second_offset_counts"] == {"0": 2, "30": 2}
    assert row["subsecond_timestamp_count"] == 1
    assert (row["fetched_at_min"], row["fetched_at_max"]) == ("earlier", "middle")
    for field in ("volume", "quote_volume"):
        assert [row[f"{field}_{state}_count"] for state in ("null", "empty", "populated")] == [1, 1, 2]
    text = json.dumps(result)
    for forbidden in ("PRIVATE-PRICE", "900000000000000001", "800000000000000002"):
        assert forbidden not in text
    payload = {"summary": report["summary"], "rows": report["rows"]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    assert report["inventory_sha256"] == digest
    assert result["verification"]["producer_sha256"] == result["verification"]["verifier_sha256"] == digest
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE klines SET volume = '123' WHERE volume = '900000000000000001'")
    assert inventory.collect_verified_inventory(database) == result


def test_absent_index_namespace_is_not_economic_absence(tmp_path):
    database = _database(tmp_path / "invented.db", [
        ("KRX:000001", "1m", inventory.START_MS, "0", None, None),
    ])
    report = inventory.collect_verified_inventory(database)["inventory"]
    assert report["summary"]["namespaces"]["KRX-INDEX"] == {
        "row_count": 0, "symbol_count": 0, "kst_date_count": 0,
        "aggregate_row_count": 0, "status": "stored_namespace_absent_in_fixed_scope",
    }
    assert "not absence of economic market data" in report["summary"]["limitation"]
    assert report["rows"][0]["fetched_at_min"] is None
    assert original.collect_inventory(database)["summary"]["window"]["symbol"] == "KRX:*"


@pytest.mark.parametrize("sql", [
    "SELECT open FROM klines", "SELECT high FROM klines", "SELECT low FROM klines",
    "SELECT close FROM klines", "SELECT * FROM klines", "DELETE FROM klines",
    "UPDATE klines SET volume = '42'", "PRAGMA query_only = OFF",
    "ATTACH DATABASE ':memory:' AS other", "SELECT randomblob(1000)",
])
def test_authorizer_denies_prices_writes_and_unapproved_operations(tmp_path, sql):
    database = _database(tmp_path / "invented.db")
    before = database.read_bytes()
    with inventory._readonly_inventory_connection(database) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(sql)
    assert database.read_bytes() == before


@pytest.mark.parametrize("missing", ["volume", "quote_volume", "fetched_at"])
def test_missing_required_column_refuses_instead_of_manufacturing_zero(tmp_path, missing):
    database = tmp_path / "invented.db"
    columns = ["symbol TEXT", "interval TEXT", "open_time_ms INTEGER", "volume TEXT", "quote_volume TEXT", "fetched_at TEXT"]
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE klines (" + ", ".join(column for column in columns if not column.startswith(missing + " ")) + ")")
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        inventory.collect_verified_inventory(database)


def test_independent_stream_detects_mutated_producer_counts(tmp_path, monkeypatch):
    database = _database(tmp_path / "invented.db", [
        ("KRX:000001", "1m", inventory.START_MS, "0", "fetched", None),
    ])
    real_producer = inventory._produce_inventory

    def corrupt_producer(connection, checkpoint):
        produced = real_producer(connection, checkpoint)
        produced["rows"][0]["volume_null_count"] += 1
        return produced

    monkeypatch.setattr(inventory, "_produce_inventory", corrupt_producer)
    with pytest.raises(ValueError, match="independent verification mismatch"):
        inventory.collect_verified_inventory(database)


def test_independent_scope_query_detects_omitted_index_namespace(tmp_path, monkeypatch):
    database = _database(tmp_path / "invented.db", [
        ("KRX:000001", "1m", inventory.START_MS, "0", "fetched", None),
        ("KRX-INDEX:0001", "1m", inventory.START_MS, "0", "fetched", None),
    ])
    monkeypatch.setattr(
        inventory, "_PRODUCER_SQL",
        inventory._PRODUCER_SQL.replace(" OR symbol GLOB 'KRX-INDEX:*'", ""),
    )
    with pytest.raises(ValueError, match="independent verification mismatch"):
        inventory.collect_verified_inventory(database)


@pytest.mark.parametrize("stamp,fetched", [
    (inventory.START_MS + 0.5, "fetched"), (inventory.START_MS, b"invalid-retrieval-type"),
])
def test_malformed_metadata_refuses_without_partial_report(tmp_path, stamp, fetched):
    database = _database(tmp_path / "invented.db", [
        ("KRX:000001", "1m", stamp, "0", fetched, None),
    ])
    with pytest.raises(ValueError, match="invalid timestamp or retrieval metadata"):
        inventory.collect_verified_inventory(database)
    # The independent stream itself rejects invalid source types, even if a
    # producer implementation accidentally accepts an aggregate hiding them.
    with inventory._readonly_inventory_connection(database) as connection:
        with pytest.raises(ValueError, match="invalid timestamp or retrieval metadata"):
            inventory._reconstruct_inventory(connection, None)


def test_numeric_day_groups_handle_year_boundary_and_interleaved_stream(tmp_path, monkeypatch):
    stamps = [
        _ms("2025-12-31T23:59:59.999+09:00"),
        _ms("2026-01-01T00:00:00+09:00"),
        _ms("2026-01-01T00:01:00.001+09:00"),
    ]
    database = _database(tmp_path / "invented.db", [
        (symbol, "1m", stamp, None, None, None)
        for symbol in ("KRX:000001", "KRX-INDEX:0001") for stamp in stamps
    ])
    monkeypatch.setattr(
        inventory, "_VERIFIER_SQL",
        inventory._VERIFIER_SQL + " ORDER BY open_time_ms DESC, symbol",
    )
    rows = inventory.collect_verified_inventory(database)["inventory"]["rows"]
    assert [row["kst_date"] for row in rows] == ["2025-12-31", "2026-01-01"] * 2
    assert [row["row_count"] for row in rows] == [1, 2, 1, 2]
    assert [row["subsecond_timestamp_count"] for row in rows] == [1, 1, 1, 1]
    assert rows[0]["timestamp_second_offset_counts"] == {"59": 1}
    for row in rows:
        for field in ("volume", "quote_volume"):
            assert row[f"{field}_null_count"] == row["row_count"]
            assert row[f"{field}_empty_count"] == row[f"{field}_populated_count"] == 0


def test_fixed_queries_keep_representative_vm_work_below_registered_budget(tmp_path):
    source_rows = 20_000
    database = _database(tmp_path / "invented.db", [
        ("KRX:000001", "1m",
         inventory.START_MS + i // 390 * 86400000 + 32400000 + i % 390 * 60000,
         (None, "", "0")[i % 3], "invented-retrieval", ("0", None, "")[i % 3])
        for i in range(source_rows)
    ])
    steps = 0

    def count_invented_steps():
        nonlocal steps
        steps += 100
        return 0

    with inventory._readonly_inventory_connection(database) as connection:
        # Diagnostic only on this invented store; production's cumulative
        # 60-second/200M-step handler and outer CPU/RAM limits stay unchanged.
        connection.set_progress_handler(count_invented_steps, 100)
        produced = inventory._produce_inventory(connection, None)
        reconstructed = inventory._reconstruct_inventory(connection, None)
    assert produced == reconstructed
    # The failed 2M-row version needed about 131 VM steps/source row. This
    # smaller geometry catches its return without making every test run 2M rows.
    assert steps < source_rows * 100


def test_both_passes_use_same_snapshot_even_when_writer_commits(tmp_path, monkeypatch):
    database = _database(tmp_path / "invented.db", [
        ("KRX:000001", "1m", inventory.START_MS, "0", "fetched", None),
    ])
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    real_verifier = inventory._reconstruct_inventory

    def verify_after_writer(connection, checkpoint):
        with sqlite3.connect(database) as writer:
            writer.execute("UPDATE klines SET volume = NULL")
        return real_verifier(connection, checkpoint)

    monkeypatch.setattr(inventory, "_reconstruct_inventory", verify_after_writer)
    result = inventory.collect_verified_inventory(database)
    assert result["inventory"]["rows"][0]["volume_populated_count"] == 1
    assert result["verification"]["same_snapshot"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT volume IS NULL FROM klines").fetchone() == (1,)


def test_cumulative_query_budget_and_aggregate_limit_refuse(tmp_path, monkeypatch):
    database = _database(tmp_path / "invented.db", [
        ("KRX:000001", "1m", inventory.START_MS, "0", None, None),
        ("KRX:000002", "1m", inventory.START_MS, "0", None, None),
    ])
    monkeypatch.setattr(inventory, "MAX_AGGREGATE_ROWS", 1)
    with pytest.raises(ValueError, match="aggregate row limit"):
        inventory.collect_verified_inventory(database)
    monkeypatch.setattr(inventory, "MAX_AGGREGATE_ROWS", 200000)
    monkeypatch.setattr(original, "_PROGRESS_STEPS", 1)
    monkeypatch.setattr(original, "_MAX_QUERY_STEPS", 1)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        inventory.collect_verified_inventory(database)


def test_checkpoint_failure_precedes_database_access(tmp_path):
    missing = tmp_path / "missing.db"

    def stop():
        raise TimeoutError("outer budget exhausted")

    with pytest.raises(TimeoutError, match="outer budget"):
        inventory.collect_verified_inventory(missing, checkpoint=stop)
    assert not missing.exists()


def test_readonly_empty_inventory_and_missing_file(tmp_path):
    database = _database(tmp_path / "invented.db")
    before = database.read_bytes()
    mtime = database.stat().st_mtime_ns
    result = inventory.collect_verified_inventory(database)
    assert result["inventory"]["rows"] == []
    assert result["verification"]["status"] == "verified"
    assert database.read_bytes() == before
    assert database.stat().st_mtime_ns == mtime
    missing = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        inventory.collect_verified_inventory(missing)
    assert not missing.exists()
