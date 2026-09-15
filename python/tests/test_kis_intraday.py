"""Tests for `data.kis_intraday`.

Every property here exists because the live endpoint does something a
reasonable implementation would not expect, and each was **measured**
against the paper host on 2026-09-15 rather than read from a document:

- the fourth page overruns into the **previous session**;
- an out-of-range date is `rt_cd=0` with **zero rows**, not an error;
- `stck_cntg_hour` is **not reliably on the minute grid**;
- `acml_tr_pbmn` is **cumulative**, so it must not reach `quote_volume`;
- a minute with no trade has **no bar**, so completeness is a span and
  never a count.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from data.kis_intraday import (
    COMPLETE_FROM,
    COMPLETE_TO,
    INTERVAL,
    SESSION_PAGES,
    fetch_session,
    parse_row,
    session_is_collected,
    stored_span,
    sync_session,
    timestamp_ms,
)
from data.kis_klines import KisKlinesError
from data.store import connect, upsert_klines


def _bar(hour, date="20260911", price="250000", vol="100"):
    return {
        "stck_bsop_date": date,
        "stck_cntg_hour": hour,
        "stck_oprc": price,
        "stck_hgpr": price,
        "stck_lwpr": price,
        "stck_prpr": price,
        "cntg_vol": vol,
        "acml_tr_pbmn": "2373467841750",
    }


class _FakeSession:
    """Answers `fetch_page`'s HTTP call from a scripted page map."""

    host = "https://fake"

    def __init__(self, pages, rt_cd="0"):
        self.pages = pages
        self.rt_cd = rt_cd
        self.calls: list[str] = []

    def headers(self, tr_id):
        return {"tr_id": tr_id}


@pytest.fixture
def patched(monkeypatch):
    """Route `_get_with_retry` to the fake session's page map."""
    holder = {}

    def install(session):
        holder["s"] = session

        def fake_get(url, headers):
            hour = url.split("FID_INPUT_HOUR_1=")[1].split("&")[0]
            session.calls.append(hour)
            return {
                "rt_cd": session.rt_cd,
                "msg_cd": "MCA00000",
                "output1": {"stck_prpr": "999999"},  # "now", not the date
                "output2": session.pages.get(hour, []),
            }

        monkeypatch.setattr("data.kis_intraday._get_with_retry", fake_get)
        return session

    return install


# --------------------------------------------------------- timestamps


def test_a_stamp_is_read_as_kst():
    """09:00 KST is 00:00 UTC, the same equality the daily path relies on."""
    import datetime as dt

    ms = timestamp_ms("20260911", "090000")
    assert dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M"
    ) == "2026-09-11T00:00"


def test_off_grid_seconds_are_preserved_not_rounded():
    """2026-01-02 really returned `:11` on every bar. Rounding to the
    minute would be a fabrication; asserting alignment would reject real
    data."""
    a = timestamp_ms("20260102", "132011")
    b = timestamp_ms("20260102", "132000")
    assert a - b == 11_000


def test_consecutive_off_grid_bars_stay_a_minute_apart():
    assert timestamp_ms("20260102", "132111") - timestamp_ms("20260102", "132011") == 60_000


@pytest.mark.parametrize("bad", ["2026091", "20260911x", ""])
def test_a_malformed_date_is_refused(bad):
    with pytest.raises(KisKlinesError, match="YYYYMMDD"):
        timestamp_ms(bad, "090000")


@pytest.mark.parametrize("bad", ["0900", "09000x", ""])
def test_a_malformed_time_is_refused(bad):
    with pytest.raises(KisKlinesError, match="HHMMSS"):
        timestamp_ms("20260911", bad)


def test_an_impossible_instant_is_refused():
    with pytest.raises(KisKlinesError, match="not a real instant"):
        timestamp_ms("20260911", "250000")


# ------------------------------------------------------------ parsing


def test_a_row_becomes_a_bar_with_per_bar_volume():
    bar = parse_row(_bar("153000", price="259500", vol="1594938"), "20260911")
    assert bar.close == Decimal("259500")
    assert bar.volume == Decimal("1594938")


def test_the_cumulative_traded_value_never_reaches_quote_volume():
    """**Trap 3.** `acml_tr_pbmn` runs from 2.37e12 at 13:21 to 3.60e12 at
    15:30 within one session — it is a running total, and `quote_volume`
    is documented as *the bar's* traded value. Writing one into the other
    is a lie that survives every test that does not check for it."""
    bar = parse_row(_bar("153000"), "20260911")
    assert bar.quote_volume is None


@pytest.mark.parametrize(
    "field", ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "cntg_vol"]
)
def test_a_missing_price_field_fails_closed(field):
    """A `.get()` yielding `None` is how this project's KIS integration
    got three endpoints wrong once already."""
    row = _bar("153000")
    del row[field]
    with pytest.raises(KisKlinesError, match=field):
        parse_row(row, "20260911")


def test_a_row_without_a_time_fails_closed():
    row = _bar("153000")
    del row["stck_cntg_hour"]
    with pytest.raises(KisKlinesError, match="stck_cntg_hour"):
        parse_row(row, "20260911")


def test_an_unparseable_number_fails_closed():
    row = _bar("153000")
    row["stck_prpr"] = "N/A"
    with pytest.raises(KisKlinesError, match="not a number"):
        parse_row(row, "20260911")


# ------------------------------------------------------ session fetch


def test_all_four_pages_are_requested_newest_first(patched):
    s = patched(_FakeSession({h: [_bar("0930%02d" % i)] for i, h in enumerate(SESSION_PAGES)}))
    fetch_session(s, "005930", "20260911", delay_s=0)
    assert s.calls == list(SESSION_PAGES)


def test_the_fourth_pages_previous_session_rows_are_discarded(patched):
    """**The trap the probe document did not record.** Requesting 09:20
    returns 120 rows of which only ~21 belong to the requested date; the
    other 99 are the previous session's tail. Keeping them would write
    bars under a session nobody asked about, and "which sessions have I
    fetched" would stop having an answer."""
    pages = {
        "092000": [_bar("090000"), _bar("091000")]
        + [_bar("152000", date="20260910"), _bar("153000", date="20260910")],
    }
    s = patched(_FakeSession(pages))
    bars = fetch_session(s, "005930", "20260911", delay_s=0)
    assert len(bars) == 2
    assert all(
        timestamp_ms("20260911", "000000")
        <= b.open_time_ms
        < timestamp_ms("20260912", "000000")
        for b in bars
    )


def test_an_out_of_range_date_is_empty_not_an_error(patched):
    """`rt_cd=0` with zero rows is how KIS says "nothing here" — the same
    convention an expired futures contract returns. `fetch_session`
    reports the emptiness and refuses to interpret it; only a trading
    calendar can say whether it means "not a trading day", "older than the
    rolling window" or "something broke"."""
    s = patched(_FakeSession({}))
    assert fetch_session(s, "005930", "20250902", delay_s=0) == []


def test_a_real_rejection_still_raises(patched):
    s = patched(_FakeSession({}, rt_cd="1"))
    with pytest.raises(KisKlinesError, match="rejected"):
        fetch_session(s, "005930", "20260911", delay_s=0)


def test_bars_come_back_sorted_though_the_endpoint_returns_them_newest_first(patched):
    s = patched(_FakeSession({"153000": [_bar("153000"), _bar("132100")]}))
    bars = fetch_session(s, "005930", "20260911", delay_s=0)
    assert [b.open_time_ms for b in bars] == sorted(b.open_time_ms for b in bars)


def test_a_bar_appearing_on_two_pages_is_stored_once(patched):
    """Adjacent pages meet at a boundary minute, so an overlap is normal
    rather than a fault."""
    s = patched(_FakeSession({"153000": [_bar("132100")], "132000": [_bar("132100")]}))
    assert len(fetch_session(s, "005930", "20260911", delay_s=0)) == 1


# ---------------------------------------------------- store and resume


@pytest.fixture
def conn(tmp_path):
    """`store.connect`, not a bare `executescript(SCHEMA)`.

    The additive order-flow columns are applied by `_ensure_klines_columns`
    at connect time rather than being in `SCHEMA` — `CREATE TABLE IF NOT
    EXISTS` is a no-op against the real populated table, so the migration
    is where they live. A fixture that skips it builds a schema the
    production path never has."""
    return connect(str(tmp_path / "k.sqlite3"))


def _store(conn, date, *hours):
    upsert_klines(
        conn, "KRX:005930", INTERVAL,
        [parse_row(_bar(h, date=date), date) for h in hours],
    )


def test_a_session_spanning_the_day_counts_as_collected(conn):
    _store(conn, "20260911", "090000", "120000", "153000")
    assert session_is_collected(conn, "KRX:005930", "20260911")


def test_a_half_fetched_session_does_not(conn):
    """The run died after two pages. Treating this as done is how a
    multi-hour backfill silently loses a third of its data."""
    _store(conn, "20260911", "132100", "153000")
    assert not session_is_collected(conn, "KRX:005930", "20260911")


def test_an_untouched_session_does_not(conn):
    assert not session_is_collected(conn, "KRX:005930", "20260911")


def test_completeness_is_a_span_not_a_count(conn):
    """**Measured**: 007390's 120 rows spanned 13:15-15:30 where 005930's
    spanned 13:21-15:30 — an illiquid name simply has minutes with no
    trade. A threshold on bar count would refetch it forever."""
    _store(conn, "20260911", "092500", "151000")
    assert session_is_collected(conn, "KRX:005930", "20260911")
    count, first, last = stored_span(conn, "KRX:005930", "20260911")
    assert (count, first, last) == (2, "092500", "151000")
    assert first <= COMPLETE_FROM and last >= COMPLETE_TO


def test_another_days_bars_do_not_make_a_session_look_collected(conn):
    _store(conn, "20260910", "090000", "153000")
    assert not session_is_collected(conn, "KRX:005930", "20260911")
    assert stored_span(conn, "KRX:005930", "20260911") == (0, None, None)


def test_sync_skips_a_session_already_collected(conn, patched):
    _store(conn, "20260911", "090000", "153000")
    s = patched(_FakeSession({"153000": [_bar("153000")]}))
    assert sync_session(conn, s, "005930", "20260911", delay_s=0) == (0, 0)
    assert s.calls == [], "a collected session must cost no API calls"


def test_force_refetches_a_collected_session(conn, patched):
    _store(conn, "20260911", "090000", "153000")
    s = patched(_FakeSession({"153000": [_bar("140000")]}))
    fetched, inserted = sync_session(conn, s, "005930", "20260911", delay_s=0, force=True)
    assert (fetched, inserted) == (1, 1)


def test_sync_stores_under_the_krx_symbol_convention(conn, patched):
    s = patched(_FakeSession({"153000": [_bar("153000")]}))
    sync_session(conn, s, "005930", "20260911", delay_s=0)
    stored = conn.execute(
        "SELECT symbol, interval FROM klines LIMIT 1"
    ).fetchone()
    assert stored == ("KRX:005930", "1m")


def test_an_empty_session_writes_nothing_and_says_so(conn, patched):
    s = patched(_FakeSession({}))
    assert sync_session(conn, s, "005930", "20250902", delay_s=0) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0] == 0


def test_a_malformed_output2_row_fails_the_page(patched):
    """**Filtering it out would be worse than crashing.** Completeness here
    is a span, not a count, so a dropped minute in the middle of a session
    still spans the day, still counts as collected, and is never fetched
    again — a permanent hole that looks like a complete session."""
    s = patched(_FakeSession({"153000": [_bar("153000"), "not an object"]}))
    with pytest.raises(KisKlinesError, match="row 1 is not an object"):
        fetch_session(s, "005930", "20260911", delay_s=0)


def test_the_previous_sessions_rows_are_still_filtered_not_refused(patched):
    """The two are different: a row from another date is *expected* on the
    fourth page and is dropped; a row that is not an object at all is a
    fault and stops the page."""
    s = patched(_FakeSession({"092000": [_bar("090000"), _bar("153000", date="20260910")]}))
    assert len(fetch_session(s, "005930", "20260911", delay_s=0)) == 1
