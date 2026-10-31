"""Invented-store tests; never open trading stores, APIs or experiment logs."""

import copy
import hashlib
import importlib
import json
import sqlite3

import pytest

from research import intraday_ed_input_inventory as inventory


def _database(path, klines=(), positioning=(), *, tables=True):
    with sqlite3.connect(path) as connection:
        if tables:
            connection.execute("""CREATE TABLE klines (
                symbol TEXT, interval TEXT, open_time_ms INTEGER, fetched_at TEXT,
                volume TEXT, quote_volume TEXT, open TEXT, high TEXT, low TEXT, close TEXT
            )""")
            connection.executemany(
                "INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, 'price', 'price', 'price', 'price')",
                klines,
            )
            connection.execute("""CREATE TABLE positioning (
                symbol TEXT, metric TEXT, period TEXT, timestamp_ms INTEGER,
                fetched_at TEXT, value TEXT
            )""")
            connection.executemany(
                "INSERT INTO positioning VALUES (?, ?, ?, ?, ?, 'numeric-quote-value')",
                positioning,
            )
    return path


def _bar(symbol="KRX:005930", stamp=None, *, volume="0", quote="0", interval="1m"):
    return symbol, interval, inventory.START_MS if stamp is None else stamp, "retrieved", volume, quote


def test_fixed_scope_excludes_reserved_forward_other_namespaces_and_intervals(tmp_path):
    stamps = (inventory.START_MS, inventory.END_EXCLUSIVE_MS - 1)
    database = _database(tmp_path / "invented.db", [
        *[_bar(symbol, stamp) for symbol in ("KRX:005930", "KRX-INDEX:0001") for stamp in stamps],
        _bar(stamp=0), _bar(stamp=inventory.START_MS - 1),
        _bar(stamp=inventory.END_EXCLUSIVE_MS), _bar("KRX-FUT:101"),
        _bar("KRX-RAW:005930"), _bar("BINANCE:BTCUSDT"),
        _bar(interval="1d"), _bar(interval="1M"),
    ])
    result = inventory.collect_verified_inventory(database)
    observed = result["inventory"]["klines"]
    assert observed["summary"]["row_count"] == 4
    assert observed["summary"]["symbol_count"] == 2
    assert {row["kst_date"] for row in observed["rows"]} == {"2025-09-04", "2026-09-23"}
    assert observed["summary"]["namespaces"]["KRX-INDEX"]["row_count"] == 2
    assert result["verification"]["same_snapshot"] is True
    assert result["verification"]["producer_sha256"] == result["verification"]["verifier_sha256"]
    metadata = copy.deepcopy(result["inventory"])
    digest = metadata.pop("inventory_sha256")
    expected = hashlib.sha256(json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()
    assert digest == expected
    assert result["verification"]["digest_scope"] == "returned_metadata_not_database_file"


def test_actual_zero_is_populated_and_null_empty_remain_separate(tmp_path):
    database = _database(tmp_path / "invented.db", [
        _bar(stamp=inventory.START_MS + i, volume=value, quote=quote)
        for i, (value, quote) in enumerate(((None, ""), ("", "0"), (0, None), ("0.0", "100")))
    ])
    observed = inventory.collect_verified_inventory(database)["inventory"]["klines"]["rows"][0]
    for field in ("volume", "quote_volume"):
        assert observed[f"{field}_null_count"] == 1
        assert observed[f"{field}_empty_count"] == 1
        assert observed[f"{field}_populated_count"] == 2
    assert observed["first_timestamp_ms"] == inventory.START_MS
    assert observed["last_timestamp_ms"] == inventory.START_MS + 3


def test_positioning_is_only_krx_quote_presence_with_unknown_period_preserved(tmp_path):
    database = _database(tmp_path / "invented.db", positioning=[
        ("KRX:005930", "krx_quote_ask1", "snapshot", inventory.START_MS, None),
        ("KRX:005930", "krx_quote_ask1", "snapshot", inventory.START_MS + 1, ""),
        ("KRX:005930", "krx_quote_ask1", "snapshot", inventory.START_MS + 2, "fetched"),
        ("KRX:005930", "krx_quote_unknown", "unknown-period", inventory.START_MS, "fetched"),
        ("KRX-FUT:101", "krx_quote_ask1", "snapshot", inventory.START_MS, "fetched"),
        ("KRX-INDEX:0001", "krx_quote_ask1", "snapshot", inventory.START_MS, "fetched"),
        ("KRX:005930", "investor_flow", "snapshot", inventory.START_MS, "fetched"),
        ("KRX:005930", "krx_quote_ask1", "snapshot", inventory.START_MS - 1, "fetched"),
        ("KRX:005930", "krx_quote_ask1", "snapshot", inventory.END_EXCLUSIVE_MS, "fetched"),
    ])
    result = inventory.collect_verified_inventory(database)
    observed = result["inventory"]["positioning"]
    assert observed["summary"]["row_count"] == 4
    assert observed["rows"][0]["fetched_at_null_count"] == 1
    assert observed["rows"][0]["fetched_at_empty_count"] == 1
    assert observed["rows"][0]["fetched_at_populated_count"] == 1
    assert observed["rows"][1]["period"] == "unknown-period"
    assert "numeric-quote-value" not in json.dumps(result)


def test_absent_and_incompatible_are_not_zero_coverage(tmp_path):
    database = _database(tmp_path / "invented.db", tables=False)
    result = inventory.collect_verified_inventory(database)["inventory"]
    assert result["klines"] == {"status": "table_absent"}
    assert result["positioning"] == {"status": "table_absent"}
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE klines (symbol TEXT, open_time_ms INTEGER)")
        connection.execute("CREATE TABLE positioning (symbol TEXT, value TEXT)")
    result = inventory.collect_verified_inventory(database)["inventory"]
    assert result["klines"]["status"] == "schema_incompatible"
    assert "volume" in result["klines"]["missing_required_columns"]
    assert result["positioning"]["status"] == "schema_incompatible"
    for family in ("klines", "positioning"):
        assert "rows" not in result[family]
        assert "summary" not in result[family]


def test_existing_empty_compatible_table_has_observed_zero_and_no_false_economic_claim(tmp_path):
    database = _database(tmp_path / "invented.db")
    before, mtime = database.read_bytes(), database.stat().st_mtime_ns
    result = inventory.collect_verified_inventory(database)["inventory"]
    assert result["klines"]["status"] == "metadata_observed"
    assert result["klines"]["summary"]["row_count"] == 0
    assert result["klines"]["rows"] == []
    assert result["context_candidates"]["disclosure"]["source_fitness"] == "unresolved"
    assert "do not establish" in result["summary"]["limitation"]
    assert database.read_bytes() == before
    assert database.stat().st_mtime_ns == mtime


def test_schema_candidates_are_names_columns_only_and_views_are_not_tables(tmp_path):
    database = _database(tmp_path / "invented.db")
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE "sector""mapping" (industry_code TEXT, secret_price TEXT)')
        connection.execute('INSERT INTO "sector""mapping" VALUES (\'secret-value\', \'price-value\')')
        connection.execute("CREATE TABLE opaque (rcept_no TEXT, body TEXT)")
        connection.execute("INSERT INTO opaque VALUES ('filing-secret', 'private-body')")
        connection.execute("CREATE VIEW dart_view AS SELECT close FROM klines")
    result = inventory.collect_verified_inventory(database)["inventory"]
    assert result["context_candidates"]["sector"]["tables"] == ['sector"mapping']
    assert result["context_candidates"]["disclosure"]["tables"] == ["opaque"]
    names = {table["name"] for table in result["schema"]["tables"]}
    assert "dart_view" not in names
    encoded = json.dumps(result)
    for private in ("secret-value", "price-value", "filing-secret", "private-body"):
        assert private not in encoded
    assert all(not value["data_rows_read"] for value in result["context_candidates"].values())


def test_exact_query_allowlist_and_column_authorizer_deny_price_value_and_writes(tmp_path):
    database = _database(tmp_path / "invented.db", [_bar()])
    with inventory._readonly_inventory_connection(database) as session:
        session.read_schema()
        for sql in ("SELECT volume FROM klines", "SELECT close FROM klines", "DELETE FROM klines"):
            with pytest.raises(ValueError, match="not allowlisted"):
                session.execute(sql, (inventory.START_MS, inventory.END_EXCLUSIVE_MS))
        for sql in ("SELECT close FROM klines", "SELECT value FROM positioning",
                    "DELETE FROM klines", "ATTACH DATABASE ':memory:' AS other",
                    "PRAGMA table_info('nonexistent')", "SELECT sql FROM sqlite_master"):
            with pytest.raises(sqlite3.DatabaseError):
                session._connection.execute(sql)
        with pytest.raises(ValueError, match="not allowlisted"):
            session.execute(inventory._KLINES_PRODUCER_SQL, (0, inventory.END_EXCLUSIVE_MS))


@pytest.mark.parametrize("method", ["_produce_inventory", "_reconstruct_inventory"])
def test_independent_pass_rejects_mutated_presences(tmp_path, monkeypatch, method):
    database = _database(tmp_path / "invented.db", [_bar()])
    original = getattr(inventory, method)

    def tampered(session, checkpoint):
        result = original(session, checkpoint)
        result["klines"]["rows"][0]["volume_populated_count"] += 1
        return result

    monkeypatch.setattr(inventory, method, tampered)
    with pytest.raises(ValueError, match="independent verification mismatch"):
        inventory.collect_verified_inventory(database)


def test_schema_metadata_tampering_refuses(tmp_path, monkeypatch):
    database = _database(tmp_path / "invented.db")
    original = inventory._produce_inventory

    def tampered(session, checkpoint):
        result = original(session, checkpoint)
        result["schema"]["tables"][0]["columns"][0]["name"] = "wrong-name"
        return result

    monkeypatch.setattr(inventory, "_produce_inventory", tampered)
    with pytest.raises(ValueError, match="independent verification mismatch"):
        inventory.collect_verified_inventory(database)


def test_single_snapshot_survives_concurrent_writer(tmp_path, monkeypatch):
    database = _database(tmp_path / "invented.db", [_bar()])
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    original = inventory._reconstruct_inventory

    def after_write(session, checkpoint):
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE klines SET volume = NULL")
        return original(session, checkpoint)

    monkeypatch.setattr(inventory, "_reconstruct_inventory", after_write)
    result = inventory.collect_verified_inventory(database)
    assert result["inventory"]["klines"]["rows"][0]["volume_populated_count"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT volume IS NULL FROM klines").fetchone() == (1,)


@pytest.mark.parametrize("kind", ["tables", "columns", "groups", "sources"])
def test_schema_and_result_limits_refuse(tmp_path, monkeypatch, kind):
    database = _database(tmp_path / "invented.db", [_bar(), _bar("KRX:000660")])
    attribute, limit = {
        "tables": ("MAX_SCHEMA_TABLES", 1), "columns": ("MAX_SCHEMA_COLUMNS", 1),
        "groups": ("MAX_AGGREGATE_ROWS", 1), "sources": ("MAX_SOURCE_ROWS", 1),
    }[kind]
    monkeypatch.setattr(inventory, attribute, limit)
    with pytest.raises(ValueError, match="limit exceeded"):
        inventory.collect_verified_inventory(database)


def test_shared_vm_and_wall_budgets_refuse(tmp_path, monkeypatch):
    database = _database(tmp_path / "invented.db", [_bar()])
    monkeypatch.setattr(inventory, "_PROGRESS_STEPS", 1)
    monkeypatch.setattr(inventory, "_MAX_QUERY_STEPS", 1)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        inventory.collect_verified_inventory(database)
    monkeypatch.setattr(inventory, "_MAX_QUERY_STEPS", 200_000_000)
    monkeypatch.setattr(inventory, "_MAX_QUERY_SECONDS", -1)
    with pytest.raises((TimeoutError, sqlite3.OperationalError)):
        inventory.collect_verified_inventory(database)


def test_checkpoint_failure_precedes_file_access_and_missing_file_is_not_created(tmp_path):
    missing = tmp_path / "missing.db"

    def stop():
        raise TimeoutError("outer resource budget")

    with pytest.raises(TimeoutError, match="outer resource budget"):
        inventory.collect_verified_inventory(missing, checkpoint=stop)
    with pytest.raises(FileNotFoundError):
        inventory.collect_verified_inventory(missing)
    assert not missing.exists()


def test_independent_stream_validates_every_timestamp_and_retrieval_type(tmp_path):
    database = _database(tmp_path / "invented.db", [_bar()])
    with sqlite3.connect(database) as connection:
        # A real-valued timestamp can survive SQLite's aggregate extrema; the
        # independent raw pass must reject every such source-row type.
        connection.execute("UPDATE klines SET open_time_ms = open_time_ms + 0.5")
    with pytest.raises(ValueError, match="timestamp"):
        inventory.collect_verified_inventory(database)


def test_digest_ignores_price_values_but_tracks_presence(tmp_path):
    database = _database(tmp_path / "invented.db", [_bar()])
    first = inventory.collect_verified_inventory(database)["inventory"]["inventory_sha256"]
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE klines SET close = 'very-different-price', volume = '999999'")
    second = inventory.collect_verified_inventory(database)["inventory"]["inventory_sha256"]
    assert first == second
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE klines SET volume = NULL")
    third = inventory.collect_verified_inventory(database)["inventory"]["inventory_sha256"]
    assert third != second


def test_module_import_does_not_open_database_or_inspect_path(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("import attempted IO")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(inventory.Path, "resolve", forbidden)
    importlib.reload(inventory)


def test_independent_stream_handles_unordered_midnight_rows(tmp_path):
    midnight = inventory.START_MS + 86_400_000
    database = _database(tmp_path / "invented.db", [
        _bar(stamp=midnight + 1000), _bar("KRX-INDEX:0001", midnight),
        _bar(stamp=midnight - 1), _bar(stamp=midnight), _bar(stamp=midnight - 1000),
    ])
    rows = inventory.collect_verified_inventory(database)["inventory"]["klines"]["rows"]
    target = [row for row in rows if row["namespace"] == "KRX"]
    assert [(row["kst_date"], row["row_count"]) for row in target] == [
        ("2025-09-04", 2), ("2025-09-05", 2),
    ]


def test_fixed_queries_keep_representative_vm_work_bounded(tmp_path):
    source_rows = 20_000
    database = _database(tmp_path / "invented.db", [
        _bar(stamp=inventory.START_MS + i // 390 * 86_400_000 + i % 390 * 60_000,
             volume=(None, "", "0")[i % 3])
        for i in range(source_rows)
    ])
    steps = 0

    def count_steps():
        nonlocal steps
        steps += 100
        return 0

    with inventory._readonly_inventory_connection(database) as session:
        session._connection.set_progress_handler(count_steps, 100)
        produced = inventory._produce_inventory(session, None)
        rebuilt = inventory._reconstruct_inventory(session, None)
    assert produced == rebuilt
    # This invented diagnostic catches a query shape that cannot fit the
    # preregistered 2M-klines qualification under the shared 200M-step budget.
    assert steps < source_rows * 98
