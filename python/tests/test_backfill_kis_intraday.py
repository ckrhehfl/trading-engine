"""Tests for `data.backfill_kis_intraday`.

Two properties carry this runner, and each exists because the first
version got it wrong:

- **Oldest session first.** In a rolling window the oldest expires next,
  so an interrupted run must have secured those. The first implementation
  went newest-first while its own docstring argued for perishability.
- **Only contention is tolerated.** Absorbing every `OperationalError`
  would log a schema fault 2,500 times while the run produced nothing and
  reported a tidy list of "errors".
"""

from __future__ import annotations

import sqlite3

import pytest

from data.backfill_kis_intraday import (
    _is_contention,
    backfill,
    coverage,
    halted_days,
)
from data.kis_klines import KisKlinesError
from data.store import connect


@pytest.fixture
def conn(tmp_path):
    return connect(str(tmp_path / "k.sqlite3"))


DATES = ["20260915", "20260914", "20260911", "20260910"]  # newest first


class _Recorder:
    """Stands in for `sync_session`, recording the order it is called in."""

    def __init__(self, fail_on=None, raises=None):
        self.seen: list[tuple[str, str]] = []
        self.fail_on = fail_on or ()
        self.raises = raises

    def __call__(self, conn, session, code, date, *, delay_s=0, force=False):
        self.seen.append((code, date))
        if (code, date) in self.fail_on:
            raise self.raises
        return 10, 10


def _run(monkeypatch, conn, rec, codes=("005930", "000660")):
    monkeypatch.setattr("data.backfill_kis_intraday.sync_session", rec)
    monkeypatch.setattr(
        "data.backfill_kis_intraday.session_is_collected", lambda *a, **k: False
    )
    return backfill(conn, object(), list(codes), DATES, delay_s=0)


def test_the_oldest_session_is_fetched_first(monkeypatch, conn):
    """The rolling window expires from the old end, so an interrupted run
    must have secured the oldest — the newest will still be there
    tomorrow."""
    rec = _Recorder()
    _run(monkeypatch, conn, rec)
    assert [d for _, d in rec.seen][:2] == ["20260910", "20260910"]
    assert [d for _, d in rec.seen][-2:] == ["20260915", "20260915"]


def test_every_symbol_advances_together(monkeypatch, conn):
    """Date-outer, so an interruption leaves ten partial symbols covering
    one common range rather than four complete symbols and six empty."""
    rec = _Recorder()
    _run(monkeypatch, conn, rec)
    # Each date is attempted for both symbols before the next date starts.
    for i in range(0, len(rec.seen), 2):
        assert rec.seen[i][1] == rec.seen[i + 1][1]


def test_a_busy_database_costs_one_session_not_the_run(monkeypatch, conn):
    busy = sqlite3.OperationalError("database is locked")
    rec = _Recorder(fail_on=[("005930", "20260911")], raises=busy)
    stats = _run(monkeypatch, conn, rec)
    assert len(stats["errors"]) == 1 and "20260911" in stats["errors"][0]
    assert stats["sessions_fetched"] == 7  # 8 attempted, 1 lost


def test_any_other_operational_error_stops_the_run(monkeypatch, conn):
    """**The one that matters.** A schema fault absorbed per session would
    be logged 2,500 times while the run produced nothing and reported a
    tidy list of 'errors'."""
    broken = sqlite3.OperationalError("no such column: nonsense")
    rec = _Recorder(fail_on=[("005930", "20260911")], raises=broken)
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        _run(monkeypatch, conn, rec)


def test_a_kis_error_is_recorded_and_the_run_continues(monkeypatch, conn):
    rec = _Recorder(
        fail_on=[("000660", "20260914")], raises=KisKlinesError("rejected")
    )
    stats = _run(monkeypatch, conn, rec)
    assert len(stats["errors"]) == 1
    assert stats["sessions_fetched"] == 7


@pytest.mark.parametrize("msg", ["database is locked", "database table is locked"])
def test_contention_is_recognised_from_the_message_as_a_fallback(msg):
    assert _is_contention(sqlite3.OperationalError(msg))


def test_a_real_fault_is_not_mistaken_for_contention():
    assert not _is_contention(sqlite3.OperationalError("no such table: klines"))


def test_contention_prefers_the_error_code_over_the_message():
    """`sqlite_errorcode` is the reliable signal; the message check is only
    a fallback for an interpreter that lacks it."""
    exc = sqlite3.OperationalError("something entirely unrelated")
    exc.sqlite_errorcode = 5  # SQLITE_BUSY
    assert _is_contention(exc)


@pytest.mark.parametrize(
    "code,name",
    [
        (5, "SQLITE_BUSY"),
        (6, "SQLITE_LOCKED"),
        (261, "SQLITE_BUSY_RECOVERY"),      # 5  | (1 << 8)
        (517, "SQLITE_BUSY_SNAPSHOT"),      # 5  | (2 << 8)
        (262, "SQLITE_LOCKED_SHAREDCACHE"), # 6  | (1 << 8)
    ],
)
def test_an_extended_contention_code_is_still_contention(code, name):
    """**SQLite returns extended codes**, and matching exact names treats
    `SQLITE_BUSY_SNAPSHOT` as fatal — stopping the run for precisely the
    condition this is meant to survive. The primary code lives in the low
    8 bits, which is the documented relationship rather than a guess about
    naming."""
    exc = sqlite3.OperationalError(name)
    exc.sqlite_errorcode = code
    assert _is_contention(exc), name


@pytest.mark.parametrize("code", [1, 8, 11])  # ERROR, READONLY, CORRUPT
def test_an_extended_code_that_is_not_contention_is_still_fatal(code):
    exc = sqlite3.OperationalError("not a lock")
    exc.sqlite_errorcode = code | (3 << 8)
    assert not _is_contention(exc)


# --------------------------------------------------------- coverage


def _daily(conn, code, date, volume):
    from data.kis_klines import trading_date_to_ms

    conn.execute(
        "INSERT INTO klines (symbol, interval, open_time_ms, open, high, low, "
        "close, volume, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"KRX:{code}", "1d", trading_date_to_ms(date), "1", "1", "1", "1",
         str(volume), "2026-09-15"),
    )
    conn.commit()


def test_a_zero_volume_day_is_a_halt_not_a_gap(conn):
    """**Not hypothetical.** 207940 was halted for 17 consecutive sessions,
    2025-10-30 to 2025-11-21, which the first real run reported as 17
    missing sessions. A coverage report that cries wolf seventeen times
    for one symbol is a report nobody reads the eighteenth time."""
    _daily(conn, "207940", "20251030", 0)
    _daily(conn, "207940", "20251029", 12345)
    assert halted_days(conn, "207940") == {"20251030"}


def test_a_symbol_with_no_daily_bars_has_no_halts(conn):
    """Absence of evidence is not a halt: without daily bars nothing can
    be classified, and calling everything halted would hide real gaps."""
    assert halted_days(conn, "005930") == set()


def test_a_halted_session_is_not_counted_as_a_real_gap(monkeypatch, conn):
    _daily(conn, "005930", "20260914", 0)
    # Two collected sessions bracketing the halted one, so the observed
    # horizon reaches back past it -- otherwise it is classified as older
    # than the window and the halt logic is never consulted.
    monkeypatch.setattr(
        "data.backfill_kis_intraday.session_is_collected",
        lambda c, symbol, date: date in {"20260915", "20260911"},
    )
    cov = coverage(conn, ["005930"], DATES)
    assert cov["symbols"]["005930"]["halted"] == ["20260914"]
    assert cov["symbols"]["005930"]["missing_in_window"] == []


def test_coverage_splits_missing_by_cause(monkeypatch, conn):
    """Everything older than the rolling window is missing and always will
    be; reporting it beside a real gap buries the second in the first."""
    collected = {("005930", "20260915"), ("005930", "20260914"),
                 ("000660", "20260915")}
    monkeypatch.setattr(
        "data.backfill_kis_intraday.session_is_collected",
        lambda c, symbol, date: (symbol.removeprefix("KRX:"), date) in collected,
    )
    cov = coverage(conn, ["005930", "000660"], DATES)
    assert cov["horizon"] == "20260914"
    # 000660 is missing 20260914 *inside* the window -- a real gap.
    assert cov["symbols"]["000660"]["missing_in_window"] == ["20260914"]
    assert cov["symbols"]["000660"]["missing_older_than_window"] == [
        "20260911", "20260910"
    ]


def test_with_nothing_collected_every_gap_is_outside_the_window(monkeypatch, conn):
    """No horizon can be inferred, so nothing may be called a real gap —
    claiming otherwise would report 2,600 failures on a fresh clone."""
    monkeypatch.setattr(
        "data.backfill_kis_intraday.session_is_collected", lambda *a, **k: False
    )
    cov = coverage(conn, ["005930"], DATES)
    assert cov["horizon"] is None
    assert cov["symbols"]["005930"]["missing_in_window"] == []
    assert len(cov["symbols"]["005930"]["missing_older_than_window"]) == 4
