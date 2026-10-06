"""Metadata audit cannot read prices, rewrite collectors or filter history."""

from datetime import datetime, timezone
import sqlite3

import pytest

from research import activity_readiness as ar


def databases(tmp_path, *, start="20190102"):
    scan, master = tmp_path / "scan.db", tmp_path / "master.db"
    with sqlite3.connect(scan) as con:
        con.executescript("""
            CREATE TABLE scan_panel(id INTEGER,start TEXT,end TEXT);
            CREATE TABLE scan_progress(code TEXT,status TEXT);
        """)
        con.execute("INSERT INTO scan_panel VALUES(1,?,'20260918')", (start,))
        con.executemany("INSERT INTO scan_progress VALUES(?,?)",
                        [(c, "done") for c in ("005930", "367480", "084180", "999999")] +
                        [("000001", "failed:transport")])
    with sqlite3.connect(master) as con:
        # No price columns or scan_bars table exist: accidental price reads fail.
        con.executescript("""
            CREATE TABLE krx_universe(snapshot_date TEXT,code TEXT,name TEXT,
              standard_code TEXT,group_code TEXT,market TEXT);
            CREATE TABLE krx_delisted(snapshot_date TEXT,code TEXT,name TEXT,
              standard_code TEXT,market TEXT);
            CREATE TABLE klines(symbol TEXT,interval TEXT,open_time_ms INTEGER);
            INSERT INTO krx_universe VALUES
              ('2026-09-14','005930','삼성전자','KR7005930003','ST','KOSPI'),
              ('2026-10-05','005930','삼성전자','KR7005930003','ST','KOSPI'),
              ('2026-10-05','084180','수성웹툰','KR7084180009','ST','KOSDAQ');
            INSERT INTO krx_delisted VALUES
              ('2026-10-05','367480','유안타제8호스팩','KR7367480001','코스닥'),
              ('2026-10-05','084180','수성웹툰','KR7084180009','코스닥');
        """)
        for year, interval in ((2018, "1d"), (2019, "1d"), (2027, "1d"), (2019, "1m")):
            ms = int(datetime(year, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
            con.execute("INSERT INTO klines VALUES('KRX-RAW:005930',?,?)", (interval, ms))
    return scan, master


def test_inventory_is_metadata_only_and_does_not_change_either_database(tmp_path):
    scan, master = databases(tmp_path)
    before = [p.read_bytes() for p in (scan, master)]
    result = ar.inventory(scan, master)
    assert result["completed_codes"] == 4
    assert result["current_label_counts"] == {
        "passes_current_filter": 1, "spac": 1, "missing_or_multiple_identity": 2,
    }
    assert result["raw_daily_rows_in_spent_window"] == [["KRX-RAW:005930", 1]]
    assert result["snapshots"]["live"] == {"first": "2026-09-14", "last": "2026-10-05", "count": 2}
    assert result == ar.inventory(scan, master)
    assert [p.read_bytes() for p in (scan, master)] == before
    assert not {"returns", "ready", "eligible_codes", "cost_floor_resolvable"} & result.keys()
    with sqlite3.connect(master) as con:
        con.execute("UPDATE krx_universe SET name='changed label' WHERE code='005930'")
    assert ar.inventory(scan, master)["metadata_sha256"] != result["metadata_sha256"]


def test_reserved_panel_refused_before_identity_or_bar_query(tmp_path, monkeypatch):
    scan, master = databases(tmp_path, start="19910828")
    statements = []
    original = ar.readonly

    def traced(path):
        con = original(path)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(ar, "readonly", traced)
    with pytest.raises(ValueError, match="spent"):
        ar.inventory(scan, master)
    selects = [s for s in statements if s.startswith("SELECT")]
    assert selects == ["SELECT start,end FROM scan_panel WHERE id=1"]


def test_complete_code_inventory_preserves_unknowns_and_conflicts_without_prices(tmp_path):
    """The coverage denominator includes closed, missing and ambiguous identities."""
    scan, master = databases(tmp_path)
    before = [p.read_bytes() for p in (scan, master)]
    compact = ar.inventory(scan, master)
    complete = ar.inventory(scan, master, include_code_inventory=True)
    rows = complete.pop("code_inventory")
    assert complete == compact  # Same transactions, inputs, hash and category counts.
    assert [row["code"] for row in rows] == ["005930", "084180", "367480", "999999"]
    assert [len(row["identities"]) for row in rows] == [1, 2, 1, 0]
    assert rows[1]["category"] == rows[3]["category"] == "missing_or_multiple_identity"
    assert {r["kind"] for r in rows[1]["identities"]} == {"live", "delisted"}
    assert rows[2]["category"] == "spac"
    assert not any("known_on" in row or "eligible" in row for row in rows)
    assert [p.read_bytes() for p in (scan, master)] == before


def test_absent_snapshot_refuses_instead_of_assigning_historical_eligibility(tmp_path):
    scan, master = databases(tmp_path)
    with sqlite3.connect(master) as con:
        con.execute("DELETE FROM krx_delisted")
    with pytest.raises(ValueError, match="missing identity snapshot"):
        ar.inventory(scan, master)


def test_cli_refuses_dirty_transitive_source_before_opening_data(monkeypatch):
    seen = []

    def dirty(command, **kwargs):
        seen.extend(command)
        return b" M python/data/krx_instrument.py\n"

    monkeypatch.setattr(ar.subprocess, "check_output", dirty)
    monkeypatch.setattr(ar, "inventory", lambda *a: pytest.fail("must not open data"))
    with pytest.raises(ValueError, match="committed"):
        ar.main(["--scan-db", "absent-scan", "--identity-db", "absent-master"])
    assert "python/data/krx_instrument.py" in seen
    assert "python/research/activity_readiness.py" in seen
