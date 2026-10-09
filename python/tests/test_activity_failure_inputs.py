"""Task BI reader acceptance tests use temporary synthetic SQLite inputs only."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
import json
import sqlite3

import pytest

from research import activity_failure_inputs as reader
from research.activity_failure_inputs import load_activity_failure_inputs
from research.activity_timing import KST


A, B = "000001", "000002"
DAYS = tuple(date(2019, 1, 2) + timedelta(days=i) for i in range(8)) + (
    date(2025, 12, 1), date(2025, 12, 2), date(2026, 9, 18), date(2026, 9, 21),
)
RECEIPT = "2026-10-09T00:00:00+00:00"


def database(tmp_path, *, duplicate=False):
    path = tmp_path / "synthetic.sqlite3"
    with sqlite3.connect(path) as connection:
        # No price affinity, so synthetic invalid SQLite storage types survive.
        connection.executescript(
            "CREATE TABLE scan_panel(id INTEGER,start TEXT,end TEXT);"
            "CREATE TABLE scan_progress(code TEXT,first_date TEXT,last_date TEXT,"
            "bars INTEGER,frozen INTEGER,status TEXT,fetched_at TEXT);"
            "CREATE TABLE scan_bars(code TEXT,bsop_date TEXT,open,high,low,close,volume,turnover"
            + ("" if duplicate else ",PRIMARY KEY(code,bsop_date)") + ");"
        )
        connection.execute("INSERT INTO scan_panel VALUES(1,'20190102','20260918')")
        for code in (A, B):
            for day in DAYS[:-1]:
                connection.execute("INSERT INTO scan_bars VALUES(?,?,?,?,?,?,?,?)",
                                   (code, day.strftime("%Y%m%d"), "100", "100", "100", "100",
                                    "DO NOT READ VOLUME", "10"))
            connection.execute("INSERT INTO scan_progress VALUES(?,?,?,?,?,?,?)",
                               (code, "20190102", "20260918", len(DAYS) - 1, 0, "done", RECEIPT))
    return path


def mutate(path, sql, values=()):
    with sqlite3.connect(path) as connection:
        connection.execute(sql, values)


def load(path, requested=None, calendar=DAYS):
    return load_activity_failure_inputs(path, calendar, requested or {A: DAYS[:3]})


def observe(path, **values):
    fields = {"open": "100", "high": "100", "low": "100", "close": "100", "turnover": "10"}
    fields.update(values)
    mutate(path, "UPDATE scan_bars SET open=?,high=?,low=?,close=?,turnover=? "
           "WHERE code=? AND bsop_date=?", (*fields.values(), A, DAYS[0].strftime("%Y%m%d")))
    return load(path).observations[A][DAYS[0]]


def test_exact_decimal_rows_and_separate_unknown_historical_metadata(tmp_path):
    path = database(tmp_path)
    exact = "100000000000000000000000000000001.5"
    row = observe(path, turnover=exact)
    assert row.state == "observed" and row.turnover == D(exact)
    metadata = row.availability
    assert metadata.observation_date == DAYS[0]
    assert metadata.available_at == datetime(2019, 1, 3, 8, 30, tzinfo=KST)
    assert metadata.retrieved_at == "unknown"
    assert metadata.is_final is None and metadata.data_vintage is None
    assert metadata.source_public_available_at is None and row.public_available_at is None
    assert metadata.evidence_level == "assumed"
    result = load(path)
    assert result.snapshot.snapshot_received_at.utcoffset() == timedelta(0)
    assert result.snapshot.progress[0].fetched_at == RECEIPT
    assert result.snapshot.fetched_row_count == 3 and result.snapshot.missing_row_count == 0


@pytest.mark.parametrize("values,state,turnover", [
    ({"turnover": "0"}, "frozen", D(0)),
    ({"turnover": "-0"}, "frozen", D(0)),
    ({"turnover": "1"}, "observed", D(1)),
    ({"high": "101", "turnover": "0"}, "observed", D(0)),
    ({"high": "101", "low": "99", "close": "101", "turnover": "0"}, "observed", D(0)),
])
def test_frozen_locked_and_nonflat_actual_zero_are_distinct(tmp_path, values, state, turnover):
    row = observe(database(tmp_path), **values)
    assert row.state == state and row.turnover == turnover


@pytest.mark.parametrize("field,value", [
    ("open", "0"), ("high", "-1"), ("low", "NaN"), ("close", "Infinity"),
    ("turnover", "-1"), ("turnover", "NaN"), ("turnover", "Infinity"),
    ("turnover", "1e100000000000000000000"), ("turnover", " 1"),
    ("turnover", "1 "), ("turnover", ""), ("turnover", "unknown"),
    ("turnover", 1), ("turnover", 1.0), ("turnover", b"1"), ("high", "99"),
    ("low", "101"),
])
def test_invalid_amounts_and_bounds_preserve_unresolved_state(tmp_path, field, value):
    path = database(tmp_path)
    row = observe(path, **{field: value})
    assert row.state == "invalid" and row.turnover is None
    state = load(path).snapshot.row_states[0]
    assert any(issue.startswith("invalid:") for issue in state.issues)
    assert row.availability.retrieved_at == "unknown"


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "turnover"])
def test_null_and_absent_requested_rows_remain_different(tmp_path, field):
    path = database(tmp_path)
    row = observe(path, **{field: None})
    mutate(path, "DELETE FROM scan_bars WHERE code=? AND bsop_date=?", (A, DAYS[1].strftime("%Y%m%d")))
    result = load(path)
    assert row.state == "null" and row.turnover is None
    assert result.observations[A][DAYS[1]].state == "missing"
    assert result.observations[A][DAYS[1]].turnover is None
    assert result.snapshot.missing_row_count == 1
    assert result.snapshot.row_states[0].issues == (f"null:{field}",)
    assert result.snapshot.row_states[1].issues == ("absent_requested_row:unknown_cause",)


def test_private_manifest_preserves_exact_turnover_and_all_seven_metadata_fields(tmp_path):
    result = load(database(tmp_path))
    manifest = result.snapshot.to_dict()
    assert json.loads(json.dumps(manifest)) == manifest
    assert manifest["read_columns"] == ["bsop_date", "open", "high", "low", "close", "turnover"]
    assert manifest["whole_database_hash"] is False
    assert manifest["progress_counts_recomputed_from_prices"] is False
    assert manifest["read_scope"] == [{"code": A, "dates": [day.isoformat() for day in DAYS[:3]]}]
    provenance = manifest["row_provenance"][0]
    assert {"observation_date", "available_at", "retrieved_at", "source", "is_final",
            "data_vintage", "evidence_level"} <= provenance.keys()
    assert provenance["snapshot_sha256"] == result.snapshot.dataset_sha256
    assert provenance["retrieved_at"] == "unknown" and provenance["data_vintage"] is None
    assert provenance["turnover"] == "10" and provenance["state"] == "observed"
    assert provenance["issues"] == [] and "open" not in provenance


def test_scope_rows_and_snapshot_are_immutable(tmp_path):
    result = load(database(tmp_path))
    with pytest.raises(TypeError):
        result.observations[A][DAYS[0]] = None
    with pytest.raises(TypeError):
        result.observations[B] = {}
    with pytest.raises(FrozenInstanceError):
        result.snapshot.dataset_sha256 = "a" * 64


def test_fingerprint_covers_exact_raw_text_storage_types_and_missing(tmp_path):
    path = database(tmp_path)
    hashes = [load(path).snapshot.dataset_sha256]
    for value in ("10.0", 10, b"10", None):
        observe(path, turnover=value)
        hashes.append(load(path).snapshot.dataset_sha256)
    mutate(path, "DELETE FROM scan_bars WHERE code=? AND bsop_date=?", (A, DAYS[0].strftime("%Y%m%d")))
    hashes.append(load(path).snapshot.dataset_sha256)
    assert len(set(hashes)) == len(hashes)
    assert load(path).snapshot.dataset_sha256 == hashes[-1]


def test_nonrequested_values_and_volume_never_enter_query_or_digest(tmp_path, monkeypatch):
    path = database(tmp_path)
    first = load(path).snapshot.dataset_sha256
    mutate(path, "UPDATE scan_bars SET turnover=? WHERE code=?", ("INVALID OUTSIDE SCOPE", B))
    mutate(path, "UPDATE scan_bars SET turnover=? WHERE code=? AND bsop_date=?",
           ("INVALID NONREQUESTED DATE", A, DAYS[4].strftime("%Y%m%d")))
    original = sqlite3.connect
    trace = []

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(trace.append)
        connection.set_authorizer(lambda action, table, column, *_:
                                 sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ
                                 and table == "scan_bars" and column == "volume" else sqlite3.SQLITE_OK)
        return connection

    monkeypatch.setattr(reader.sqlite3, "connect", connect)
    assert load(path).snapshot.dataset_sha256 == first
    price_queries = [query for query in trace if "FROM scan_bars" in query]
    assert len(price_queries) == 1
    assert "20190102','20190103','20190104" in price_queries[0]
    assert "volume" not in price_queries[0] and "CAST" not in price_queries[0]
    assert "WHERE code='000001' AND bsop_date IN" in price_queries[0]


@pytest.mark.parametrize("sql,values", [
    ("UPDATE scan_progress SET status='failed' WHERE code=?", (B,)),
    ("DELETE FROM scan_progress WHERE code=?", (B,)),
    ("UPDATE scan_progress SET bars=0 WHERE code=?", (B,)),
    ("UPDATE scan_progress SET bars=1.5 WHERE code=?", (B,)),
    ("UPDATE scan_progress SET frozen=-1 WHERE code=?", (B,)),
    ("UPDATE scan_progress SET frozen=100000 WHERE code=?", (B,)),
    ("UPDATE scan_progress SET fetched_at='' WHERE code=?", (B,)),
    ("UPDATE scan_progress SET fetched_at='2026-10-09T00:00:00' WHERE code=?", (B,)),
    ("UPDATE scan_progress SET fetched_at='invalid' WHERE code=?", (B,)),
    ("UPDATE scan_progress SET first_date='20190101' WHERE code=?", (B,)),
    ("UPDATE scan_progress SET last_date='20260919' WHERE code=?", (B,)),
    ("UPDATE scan_progress SET first_date='20200101',last_date='20190102' WHERE code=?", (B,)),
    ("UPDATE scan_progress SET first_date='20190230' WHERE code=?", (B,)),
    ("UPDATE scan_progress SET bars=999999 WHERE code=?", (B,)),
    ("UPDATE scan_panel SET start='19910101'", ()),
    ("INSERT INTO scan_panel VALUES(2,'20190102','20260918')", ()),
])
def test_all_metadata_is_validated_before_any_prices(tmp_path, monkeypatch, sql, values):
    path = database(tmp_path)
    mutate(path, sql, values)
    original = sqlite3.connect
    price_reads = []

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)

        def authorizer(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and table == "scan_bars":
                price_reads.append(column)
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorizer)
        return connection

    monkeypatch.setattr(reader.sqlite3, "connect", connect)
    with pytest.raises(ValueError):
        load(path, {A: DAYS[:2], B: DAYS[:2]})
    assert price_reads == []


def test_duplicate_progress_and_duplicate_requested_bar_are_rejected(tmp_path):
    path = database(tmp_path, duplicate=True)
    mutate(path, "INSERT INTO scan_progress SELECT * FROM scan_progress WHERE code=?", (A,))
    with pytest.raises(ValueError, match="exactly one"):
        load(path)
    mutate(path, "DELETE FROM scan_progress WHERE rowid=(SELECT MAX(rowid) FROM scan_progress)")
    mutate(path, "INSERT INTO scan_bars SELECT * FROM scan_bars WHERE code=? AND bsop_date=?",
           (A, DAYS[0].strftime("%Y%m%d")))
    with pytest.raises(ValueError, match="duplicate requested"):
        load(path)


@pytest.mark.parametrize("sql,error", [
    ("UPDATE scan_progress SET first_date='20190103' WHERE code='000001'", "outside its"),
    ("UPDATE scan_progress SET bars=1 WHERE code='000001'", "exceeds full-code"),
])
def test_consumed_rows_cannot_contradict_validated_progress(tmp_path, sql, error):
    path = database(tmp_path)
    mutate(path, sql)
    with pytest.raises(ValueError, match=error):
        load(path)


@pytest.mark.parametrize("requested,calendar", [
    ({}, DAYS), ({"wrong": DAYS[:2]}, DAYS), ({A: []}, DAYS),
    ({A: (DAYS[1], DAYS[0])}, DAYS), ({A: (DAYS[0], DAYS[0])}, DAYS),
    ({A: (date(2019, 1, 1),)}, DAYS), ({A: (date(2025, 12, 2),)}, DAYS),
    ({A: (date(2020, 1, 1),)}, DAYS), ({A: DAYS[:2]}, list(DAYS)),
    ({A: DAYS[:2]}, tuple(reversed(DAYS))), ({A: (DAYS[0],)}, (DAYS[0],)),
])
def test_invalid_or_unregistered_code_date_scope_fails_before_open(tmp_path, monkeypatch, requested, calendar):
    def refuse(*args, **kwargs):
        pytest.fail("invalid scope opened the SQLite input")

    monkeypatch.setattr(reader.sqlite3, "connect", refuse)
    with pytest.raises(ValueError):
        load_activity_failure_inputs(tmp_path / "does-not-exist", calendar, requested)


def test_latest_registered_formation_uses_calendar_next_session(tmp_path):
    result = load(database(tmp_path), {A: (date(2025, 12, 1),)})
    assert result.observations[A][date(2025, 12, 1)].availability.available_at == datetime(
        2025, 12, 2, 8, 30, tzinfo=KST,
    )


def test_database_symlink_and_directory_are_refused(tmp_path):
    path = database(tmp_path)
    link = tmp_path / "scan-link.sqlite3"
    link.symlink_to(path)
    for invalid in (link, tmp_path):
        with pytest.raises(ValueError, match="nonsymlink regular"):
            load(invalid)


def test_scan_names_must_be_tables_not_views(tmp_path):
    path = database(tmp_path)
    mutate(path, "ALTER TABLE scan_bars RENAME TO hidden_bars")
    mutate(path, "CREATE VIEW scan_bars AS SELECT * FROM hidden_bars")
    with pytest.raises(ValueError, match="SQLite tables"):
        load(path)


def test_connection_is_readonly_query_only_and_not_immutable(tmp_path, monkeypatch):
    path = database(tmp_path)
    original = sqlite3.connect

    def connect(*args, **kwargs):
        assert args[0].endswith("?mode=ro") and "immutable" not in args[0]
        connection = original(*args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM scan_bars")
        return connection

    monkeypatch.setattr(reader.sqlite3, "connect", connect)
    assert load(path).observations[A][DAYS[0]].turnover == D(10)


def test_wal_writer_cannot_change_the_active_read_snapshot(tmp_path, monkeypatch):
    path = database(tmp_path)
    original = sqlite3.connect
    writer = original(path)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    changed = False
    trace = []

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)

        def on_query(query):
            nonlocal changed
            trace.append(query)
            if "FROM scan_bars" in query and not changed:
                changed = True
                writer.execute("UPDATE scan_bars SET turnover='999' WHERE code=?", (A,))
                writer.execute("UPDATE scan_progress SET fetched_at='2026-10-10T00:00:00+00:00' "
                               "WHERE code=?", (A,))
                writer.commit()

        connection.set_trace_callback(on_query)
        return connection

    monkeypatch.setattr(reader.sqlite3, "connect", connect)
    try:
        result = load(path)
        assert changed and all(row.turnover == D(10) for row in result.observations[A].values())
        assert result.snapshot.progress[0].fetched_at == RECEIPT
        assert trace.count("BEGIN") == 1 and trace.count("ROLLBACK") == 1
        assert "PRAGMA query_only=ON" in trace
    finally:
        writer.close()


def test_large_explicit_union_is_batched_without_expanding_scope(tmp_path):
    path = database(tmp_path)
    many = tuple(date(2019, 1, 2) + timedelta(days=i) for i in range(406))
    result = load(path, {A: many[:-1]}, many)
    assert len(result.observations[A]) == 405
    assert result.snapshot.fetched_row_count == 8
    assert result.snapshot.missing_row_count == 397
