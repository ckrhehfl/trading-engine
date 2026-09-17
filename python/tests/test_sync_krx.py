"""Tests for `data.sync_krx`.

Collection moved to the GCP instance on 2026-09-17 and research stayed
here, so this module is the only path by which local KRX reads stop being
frozen at the migration snapshot. The properties that matter are the ones
that would let a sync **lose or corrupt** data rather than merely fail:

- **It never writes a Binance row the instance alone has.** Both `klines`
  and `positioning` hold two venues in one table, and the instance's file
  still carries a frozen copy of this machine's Binance rows. Two separate
  guarantees do that work and are tested separately, because conflating
  them produced a test that could not fail: `INSERT OR IGNORE` protects a
  row whose primary key exists in both, and the **prefix filter** is what
  stops an instance-only row crossing the venue boundary.
- **It never overwrites or deletes.** `INSERT OR IGNORE` only, so the
  worst outcome of a bad sync is that nothing moves.
- **It refuses a source with no KRX rows**, which is what a wrong file
  looks like — and reports the rows it actually inserted, not the rows it
  intended to.
"""

from __future__ import annotations

import sqlite3

import pytest

from data.store import connect
from data.sync_krx import OWNED, merge_krx


def _kline(conn, symbol, ms, close="1"):
    conn.execute(
        "INSERT OR IGNORE INTO klines (symbol, interval, open_time_ms, open, high, "
        "low, close, volume, fetched_at) VALUES (?, '1d', ?, ?, ?, ?, ?, '1', 'x')",
        (symbol, ms, close, close, close, close),
    )


def _positioning(conn, symbol, metric, ms, value="1"):
    conn.execute(
        "INSERT OR IGNORE INTO positioning (symbol, metric, period, timestamp_ms, "
        "value, fetched_at) VALUES (?, ?, '1d', ?, ?, 'x')",
        (symbol, metric, ms, value),
    )


@pytest.fixture
def instance(tmp_path):
    """The instance's snapshot: fresh KRX rows, plus the FROZEN Binance
    rows it inherited from the migration copy."""
    path = str(tmp_path / "instance.sqlite3")
    conn = connect(path)
    _kline(conn, "KRX:005930", 1_000, close="instance-side")
    _kline(conn, "KRX:005930", 2_000)          # collected after the migration
    _kline(conn, "KRX-INDEX:0001", 1_000)
    _kline(conn, "BINANCE:BTCUSDT", 1_000, close="stale")
    # A Binance row the LOCAL database does not have. This is what the
    # prefix filter actually guards: `INSERT OR IGNORE` already protects a
    # row present in both, so only an instance-only row can demonstrate
    # the filter working.
    _kline(conn, "BINANCE:BTCUSDT", 7_000, close="instance-only")
    _positioning(conn, "KRX:005930", "krx_quote.ask1", 1_000)
    _positioning(conn, "BTC-USDT", "open_interest", 1_000, value="stale")
    conn.execute(
        "INSERT OR IGNORE INTO krx_universe (snapshot_date, code, market, name, "
        "group_code, fetched_at) VALUES ('20260917', '005930', 'KOSPI', 'x', 'ST', 'x')"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def local(tmp_path):
    """This machine: the KRX rows as of the migration, and Binance rows
    that have advanced since."""
    path = str(tmp_path / "local.sqlite3")
    conn = connect(path)
    _kline(conn, "KRX:005930", 1_000, close="local-side")   # already had this one
    _kline(conn, "BINANCE:BTCUSDT", 1_000, close="fresh")
    _kline(conn, "BINANCE:BTCUSDT", 9_000, close="fresh")   # collected since
    _positioning(conn, "BTC-USDT", "open_interest", 1_000, value="fresh")
    conn.commit()
    conn.close()
    return path


def _rows(path, sql):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------ what moves


def test_krx_rows_arrive(instance, local):
    merge_krx(instance, local)
    got = {s for (s,) in _rows(local, "SELECT DISTINCT symbol FROM klines")}
    assert "KRX-INDEX:0001" in got
    assert len(_rows(local, "SELECT 1 FROM klines WHERE symbol='KRX:005930'")) == 2


def test_the_report_counts_rows_actually_inserted(instance, local):
    """Not the rows it intended to move — `check_reported_from_actual`'s
    own lesson. One of the three KRX klines was already present."""
    by_table = {r.table: r for r in merge_krx(instance, local)}
    assert by_table["klines"].available == 3
    assert by_table["klines"].inserted == 2
    assert by_table["klines"].already_present == 1


# ---------------------------------------------- what must NOT move


def test_an_instance_only_binance_row_does_not_arrive(instance, local):
    """**The guard the prefix filter actually provides**, and the version
    of this test that can fail.

    An earlier version asserted only that a local Binance row kept its own
    value — which passes with the filter *removed*, because `INSERT OR
    IGNORE` already protects a row whose primary key exists in both. It
    was passing for the wrong reason. Only a row the instance has and this
    machine does not can show the filter doing anything."""
    merge_krx(instance, local)
    closes = {c for (c,) in _rows(
        local, "SELECT close FROM klines WHERE symbol='BINANCE:BTCUSDT'"
    )}
    assert "instance-only" not in closes, "a Binance row crossed the venue boundary"
    assert closes == {"fresh"}


def test_insert_or_ignore_protects_a_row_present_in_both(instance, local):
    """A separate guarantee from the filter, and worth its own test since
    the two were conflated: where the primary key collides, the local
    value wins and is never overwritten.

    **Checked on a KRX row too, not only a filtered-out Binance one.** The
    Binance assertions below pass whatever the merge does to KRX, because
    the filter excludes them — so on their own they could not catch a
    `REPLACE` or an upsert reaching the rows this module actually copies."""
    merge_krx(instance, local)
    assert _rows(
        local, "SELECT close FROM klines WHERE symbol='KRX:005930' "
        "AND open_time_ms=1000"
    ) == [("local-side",)], "a colliding KRX row was overwritten"
    assert _rows(
        local, "SELECT close FROM klines WHERE symbol='BINANCE:BTCUSDT' "
        "AND open_time_ms=1000"
    ) == [("fresh",)]
    assert _rows(
        local, "SELECT value FROM positioning WHERE symbol='BTC-USDT'"
    ) == [("fresh",)]


def test_a_correction_on_the_instance_does_NOT_propagate(instance, local):
    """The honest cost of `INSERT OR IGNORE`, asserted so it is a known
    property rather than a surprise: a row the instance later *fixed*
    keeps its old local value, and re-fetching the range locally is the
    only way to pick the correction up.

    The same mechanism as the test above, stated as its own claim because
    the two readings have opposite consequences for a reader — one is a
    safety guarantee, the other is a limitation."""
    merge_krx(instance, local)
    closes = {c for (c,) in _rows(
        local, "SELECT close FROM klines WHERE symbol='KRX:005930'"
    )}
    assert "instance-side" not in closes, (
        "a corrected instance value reached the local row -- the sync is no "
        "longer additive-only"
    )
    assert "local-side" in closes


def test_binance_rows_the_instance_never_had_survive(instance, local):
    """The local series advanced after the migration; a sync must not
    notice or touch it."""
    merge_krx(instance, local)
    assert _rows(local, "SELECT 1 FROM klines WHERE symbol='BINANCE:BTCUSDT'")
    assert len(_rows(local, "SELECT 1 FROM klines WHERE symbol='BINANCE:BTCUSDT'")) == 2


def test_nothing_is_ever_deleted(instance, local):
    before = _rows(local, "SELECT COUNT(*) FROM klines")[0][0]
    merge_krx(instance, local)
    after = _rows(local, "SELECT COUNT(*) FROM klines")[0][0]
    assert after >= before


# --------------------------------------------------- refusals


def test_a_source_with_no_krx_rows_is_refused(tmp_path, local):
    """What a wrong file looks like. Reporting "0 rows synced, success"
    would be indistinguishable from being up to date."""
    empty = str(tmp_path / "wrong.sqlite3")
    conn = connect(empty)
    _kline(conn, "BINANCE:BTCUSDT", 1_000)
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="no KRX rows"):
        merge_krx(empty, local)


def test_a_refused_sync_changes_nothing(tmp_path, local):
    """The negative control: the refusal above must roll back, not leave
    a half-applied merge."""
    empty = str(tmp_path / "wrong.sqlite3")
    conn = connect(empty)
    _kline(conn, "BINANCE:BTCUSDT", 5_000, close="stale")
    conn.commit()
    conn.close()
    before = _rows(local, "SELECT COUNT(*) FROM klines")[0][0]
    with pytest.raises(ValueError):
        merge_krx(empty, local)
    assert _rows(local, "SELECT COUNT(*) FROM klines")[0][0] == before


# --------------------------------------------------- idempotence


def test_a_second_run_moves_nothing(instance, local):
    """Safe to run before every research session, which is the point."""
    merge_krx(instance, local)
    again = merge_krx(instance, local)
    assert all(row.inserted == 0 for row in again)


def test_the_ownership_rule_covers_every_shared_table():
    """`klines` and `positioning` hold two venues each, so both need a
    filter; `krx_universe` is KRX by definition and correctly has none."""
    rules = dict(OWNED)
    assert rules["klines"] and "KRX" in rules["klines"]
    assert rules["positioning"] and "KRX" in rules["positioning"]
    assert rules["krx_universe"] is None


# ------------------------------------------- an older instance schema


def _older_schema_source(tmp_path):
    """A `klines` table as it existed before the additive migrations —
    without `quote_volume` or the two taker-buy columns.

    Not hypothetical: `store.py` adds those to an existing table, and the
    instance's checkout was 16 commits behind this machine's when
    collection moved there.
    """
    path = str(tmp_path / "old-instance.sqlite3")
    conn = connect(path)                    # current schema, then narrow it
    conn.executescript(
        """
        CREATE TABLE klines_old (
          symbol TEXT NOT NULL, interval TEXT NOT NULL,
          open_time_ms INTEGER NOT NULL, open TEXT NOT NULL, high TEXT NOT NULL,
          low TEXT NOT NULL, close TEXT NOT NULL, volume TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          PRIMARY KEY (symbol, interval, open_time_ms)
        );
        INSERT INTO klines_old SELECT symbol, interval, open_time_ms, open, high,
          low, close, volume, fetched_at FROM klines;
        DROP TABLE klines;
        ALTER TABLE klines_old RENAME TO klines;
        """
    )
    _kline(conn, "KRX:005930", 4_000)
    conn.commit()
    conn.close()
    return path


def test_a_source_missing_a_later_column_still_merges(tmp_path, local):
    """**The failure this guard prevents is not a wrong number, it is a
    traceback.** Projecting the destination's full column list onto an
    older source makes SQLite raise `no such column` before the merge runs
    at all, and `main` catches only `ValueError` — so the sync would die
    without a diagnosis."""
    source = _older_schema_source(tmp_path)
    assert "quote_volume" not in {
        r[1] for r in sqlite3.connect(source).execute("PRAGMA table_info(klines)")
    }, "the fixture did not actually narrow the schema"

    merge_krx(source, local)
    assert _rows(local, "SELECT 1 FROM klines WHERE open_time_ms=4000")


def test_the_column_the_source_lacks_is_left_to_its_default(tmp_path, local):
    """Exactly right for a nullable column added later: the row arrives,
    and the newer column is NULL rather than absent or wrong."""
    merge_krx(_older_schema_source(tmp_path), local)
    assert _rows(
        local, "SELECT quote_volume FROM klines WHERE open_time_ms=4000"
    ) == [(None,)]
