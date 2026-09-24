"""Tests for `data.backfill_kis`.

The cases that matter here are the ones about *coverage*, because KRX
breaks the assumption every other backfill in this repo is built on: the
store's `find_missing_ranges` diffs against an arithmetic grid, and a
market trading ~245 days a year produces ~116 false gaps per symbol per
year against it. This module measures coverage against an index instead,
and these tests pin that it fails closed when the index cannot serve as a
calendar.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from data.backfill_kis import main, sync_symbol, verify_symbol
from data.bingx_klines import KlineRow
from data.kis_klines import (
    ADJUSTED,
    ReferenceCalendarError,
    equity_storage_symbol,
    index_storage_symbol,
    trading_date_to_ms,
)
from data.store import connect, upsert_klines


def _row(date: str, close="70000"):
    return KlineRow(
        open_time_ms=trading_date_to_ms(date),
        open=Decimal("69000"),
        high=Decimal("71000"),
        low=Decimal("68000"),
        close=Decimal(close),
        volume=Decimal("1000"),
        quote_volume=Decimal("70000000"),
    )


# Jan 2024: 1st is a holiday, 6/7 and 13/14 are weekends.
TRADING = ["20240102", "20240103", "20240104", "20240105", "20240108"]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "k.sqlite3")
    yield c
    c.close()


class _FakeSession:
    """Serves canned bars and records what was asked for."""

    def __init__(self, by_code):
        self.host = "https://example.invalid"
        self.by_code = by_code
        self.requests: list[tuple[str, str, str]] = []

    def headers(self, tr_id):
        return {"tr_id": tr_id}


@pytest.fixture
def fake_fetch(monkeypatch):
    def install(session):
        def fake_iter(sess, code, start, end, *, adjusted, is_index=False, window_days=None):
            sess.requests.append((code, start, end))
            for d in sess.by_code.get(code, []):
                if start <= d <= end:
                    yield _row(d)

        monkeypatch.setattr("data.backfill_kis.iter_daily_range", fake_iter)

    return install


# ------------------------------------------------------------- coverage


def test_a_symbol_missing_a_reference_day_is_reported(conn):
    upsert_klines(conn, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d",
                  [_row(d) for d in TRADING if d != "20240104"])
    reference = {trading_date_to_ms(d) for d in TRADING}
    missing = verify_symbol(conn, "005930", reference, "20240101", "20240131", adjusted=ADJUSTED)
    assert [m for m in missing] == [trading_date_to_ms("20240104")]


def test_a_complete_symbol_reports_nothing(conn):
    upsert_klines(conn, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d", [_row(d) for d in TRADING])
    reference = {trading_date_to_ms(d) for d in TRADING}
    assert verify_symbol(conn, "005930", reference, "20240101", "20240131", adjusted=ADJUSTED) == []


def test_weekends_and_holidays_are_not_gaps(conn):
    """The whole reason this module exists. An arithmetic grid over
    2024-01-02..01-08 expects seven days; the market opened on five."""
    upsert_klines(conn, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d", [_row(d) for d in TRADING])
    reference = {trading_date_to_ms(d) for d in TRADING}
    assert verify_symbol(conn, "005930", reference, "20240101", "20240131", adjusted=ADJUSTED) == []

    from data.store import find_missing_ranges

    arithmetic = find_missing_ranges(
        conn, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d",
        trading_date_to_ms("20240102"), trading_date_to_ms("20240109"),
    )
    assert arithmetic, "the arithmetic grid must disagree, or this module is unnecessary"


def test_a_symbol_trading_when_the_reference_did_not_fails_closed(conn):
    """A stock cannot print on a day the market index did not. If it looks
    like it did, the reference is truncated -- which is exactly what a
    silently capped index fetch produces."""
    upsert_klines(conn, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d",
                  [_row(d) for d in TRADING])
    truncated = {trading_date_to_ms(d) for d in TRADING[2:]}
    with pytest.raises(ReferenceCalendarError):
        verify_symbol(conn, "005930", truncated, "20240101", "20240131", adjusted=ADJUSTED)


# ---------------------------------------------------------------- sync


def test_sync_stores_rows_under_the_namespaced_symbol(conn, fake_fetch):
    s = _FakeSession({"005930": TRADING})
    fake_fetch(s)
    n = sync_symbol(s, conn, "005930", "20240101", "20240131", adjusted=ADJUSTED)
    assert n == len(TRADING)
    stored = {r[0] for r in conn.execute("SELECT symbol FROM klines")}
    assert stored == {"KRX:005930"}


def test_an_index_is_stored_in_its_own_namespace(conn, fake_fetch):
    s = _FakeSession({"0001": TRADING})
    fake_fetch(s)
    sync_symbol(s, conn, "0001", "20240101", "20240131", adjusted=ADJUSTED, is_index=True)
    stored = {r[0] for r in conn.execute("SELECT symbol FROM klines")}
    assert stored == {"KRX-INDEX:0001"}


def test_a_fully_covered_window_is_not_refetched(conn, fake_fetch):
    s = _FakeSession({"005930": TRADING})
    fake_fetch(s)
    reference = {trading_date_to_ms(d) for d in TRADING}
    sync_symbol(s, conn, "005930", "20240101", "20240131",
                adjusted=ADJUSTED, reference_days=reference)
    first = len(s.requests)
    assert first >= 1
    sync_symbol(s, conn, "005930", "20240101", "20240131",
                adjusted=ADJUSTED, reference_days=reference)
    assert len(s.requests) == first, "an already-covered window was refetched"


def test_a_partially_covered_window_is_refetched(conn, fake_fetch):
    s = _FakeSession({"005930": TRADING})
    fake_fetch(s)
    upsert_klines(conn, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d", [_row(TRADING[0])])
    reference = {trading_date_to_ms(d) for d in TRADING}
    sync_symbol(s, conn, "005930", "20240101", "20240131",
                adjusted=ADJUSTED, reference_days=reference)
    assert s.requests, "a window missing days must be fetched"


def test_without_a_reference_every_window_is_fetched(conn, fake_fetch):
    """The reference index itself has nothing to be judged against, so it
    cannot skip -- otherwise a half-built calendar would freeze."""
    s = _FakeSession({"0001": TRADING})
    fake_fetch(s)
    sync_symbol(s, conn, "0001", "20240101", "20240131", adjusted=ADJUSTED, is_index=True)
    n = len(s.requests)
    sync_symbol(s, conn, "0001", "20240101", "20240131", adjusted=ADJUSTED, is_index=True)
    assert len(s.requests) == 2 * n


# ----------------------------------------------------------------- cli


def test_verify_refuses_an_empty_reference(conn, tmp_path, caplog):
    db = tmp_path / "k.sqlite3"
    connect(db).close()
    rc = main(["--verify", "--adjusted", "0", "--symbols", "005930", "--index", "0001",
               "--start", "2024-01-01", "--end", "2024-01-31", "--db-path", str(db)])
    assert rc == 1, "an empty calendar must not yield a clean verdict"


def test_verify_exits_nonzero_when_a_symbol_is_incomplete(tmp_path):
    db = tmp_path / "k.sqlite3"
    c = connect(db)
    upsert_klines(c, index_storage_symbol("0001"), "1d", [_row(d) for d in TRADING])
    upsert_klines(c, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d",
                  [_row(d) for d in TRADING if d != "20240104"])
    c.close()
    rc = main(["--verify", "--adjusted", "0", "--symbols", "005930", "--index", "0001",
               "--start", "2024-01-01", "--end", "2024-01-31", "--db-path", str(db)])
    assert rc == 1


def test_verify_exits_zero_when_complete(tmp_path):
    db = tmp_path / "k.sqlite3"
    c = connect(db)
    upsert_klines(c, index_storage_symbol("0001"), "1d", [_row(d) for d in TRADING])
    upsert_klines(c, equity_storage_symbol("005930", adjusted=ADJUSTED), "1d", [_row(d) for d in TRADING])
    c.close()
    rc = main(["--verify", "--adjusted", "0", "--symbols", "005930", "--index", "0001",
               "--start", "2024-01-01", "--end", "2024-01-31", "--db-path", str(db)])
    assert rc == 0


def test_adjusted_is_required_for_a_fetch(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--symbols", "005930", "--index", "0001",
              "--start", "2024-01-01", "--end", "2024-01-31",
              "--db-path", str(tmp_path / "k.sqlite3")])
    assert exc.value.code == 2


def test_adjusted_IS_now_required_for_verify(tmp_path):
    """**Deliberately reversed** (audit F-4 / decision D4). This asserted that
    `--verify` ran without `--adjusted`, which was true and is no longer
    wanted: the basis is part of the storage symbol now, so the two bases are
    two different series, and a coverage verdict that does not say which one
    it read is a verdict about an unnamed thing.

    Kept as a renamed test rather than deleted, because a reversal that
    disappears from the suite teaches nothing about why it happened.
    """
    db = tmp_path / "k.sqlite3"
    connect(db).close()
    with pytest.raises(SystemExit) as exc:
        # **No `--adjusted`, deliberately** -- that omission is the whole test.
        main(["--verify", "--symbols", "005930", "--index", "0001",
              "--start", "2024-01-01", "--end", "2024-01-31",
              "--db-path", str(db)])
    assert exc.value.code == 2, "argparse should refuse, not run"


def test_verify_runs_once_the_basis_is_named(tmp_path):
    """The other half: requiring it must not make `--verify` unusable."""
    db = tmp_path / "k.sqlite3"
    connect(db).close()
    # Reaches the empty-reference check rather than dying in argparse.
    assert main(["--verify", "--adjusted", "0", "--symbols", "005930",
                 "--index", "0001", "--start", "2024-01-01",
                 "--end", "2024-01-31", "--db-path", str(db)]) == 1


@pytest.mark.parametrize("bad", ["2", "adjusted", ""])
def test_an_unknown_adjusted_value_is_rejected(tmp_path, bad):
    with pytest.raises(SystemExit):
        main(["--symbols", "005930", "--index", "0001", "--adjusted", bad,
              "--start", "2024-01-01", "--end", "2024-01-31",
              "--db-path", str(tmp_path / "k.sqlite3")])


def test_a_RAW_backfill_lands_under_the_raw_symbol(tmp_path, monkeypatch):
    """**The F-4 defect driven end to end**, which nothing else covered: a
    mutation replacing `adjusted=adjusted` with a hardcoded `"0"` in
    `backfill_symbol` left the whole suite green, so no test had ever run a
    raw backfill and checked where its rows went.

    `backfill_kis --adjusted 1` is a real, reachable invocation — the CLI
    accepts `1` — and before D4 it wrote 원주가 into `KRX:005930` beside
    수정주가 under the same primary key.
    """
    from data.kis_klines import RAW, equity_storage_symbol

    db = tmp_path / "k.sqlite3"
    conn = connect(db)
    seen: list[str] = []

    def fake_iter(sess, code, start, end, *, adjusted, is_index=False, window_days=None):
        seen.append(adjusted)
        yield _row(TRADING[0])

    monkeypatch.setattr("data.backfill_kis.iter_daily_range", fake_iter)

    s = _FakeSession({"005930": TRADING})
    for basis in (ADJUSTED, RAW):
        sync_symbol(s, conn, "005930", "20240101", "20240131", adjusted=basis)

    stored = {r[0] for r in conn.execute("SELECT symbol FROM klines")}
    assert stored == {
        equity_storage_symbol("005930", adjusted=ADJUSTED),
        equity_storage_symbol("005930", adjusted=RAW),
    }, f"the two bases did not land under separate symbols: {stored}"
    assert stored == {"KRX:005930", "KRX-RAW:005930"}
    assert seen == [ADJUSTED, RAW], "the basis did not reach the fetch"
