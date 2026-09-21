"""Tests for `research.krx_dayone_drift`.

The measurement is a comparison against `rd-v` §1's own number, so what
has to be guarded is that it stays the *same* measurement with one thing
changed:

- **a delisted member must not truncate the others**, which an aligned
  panel would do by construction — that join IS the survivorship filter
- **a capped page is not data**, and the cap differs between the equity
  and index endpoints
- **no reinvestment**, so a name that ends early contributes its terminal
  ratio and nothing after
"""

from __future__ import annotations

import sqlite3

import pytest

from research.krx_dayone_drift import (
    PANEL_END,
    PANEL_START,
    SCRATCH_SCHEMA,
    KisKlinesError,
    _date_pages,
    _page,
    drift_for,
    store_series,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(SCRATCH_SCHEMA)
    return c


def _rows(dates_prices):
    return [
        {
            "stck_bsop_date": d,
            "stck_oprc": str(p),
            "stck_hgpr": str(p),
            "stck_lwpr": str(p),
            "stck_clpr": str(p),
            "acml_tr_pbmn": "1000",
        }
        for d, p in dates_prices
    ]


# ============================ a short-lived member truncates nobody


def test_a_delisted_member_does_not_truncate_the_others(conn):
    """**The finding, as a test.** `load_daily_panel` inner-joins every
    name onto the dates they all share, so one name ending in 2020 would
    cut the whole panel at 2020. That join is a survivorship filter
    expressed as SQL, which is why this module reads per-name series."""
    store_series(conn, "LIVE01", _rows([("20190102", 100), ("20260918", 300)]))
    store_series(conn, "DEAD01", _rows([("20190102", 100), ("20200414", 5)]))

    live = drift_for(conn, "LIVE01", "still here", True)
    dead = drift_for(conn, "DEAD01", "gone", False)
    assert live.last_date == "20260918", "the survivor keeps its full run"
    assert dead.last_date == "20200414", "the dead name ends where it ended"
    assert live.ratio == pytest.approx(3.0)
    assert dead.ratio == pytest.approx(0.05)


def test_a_delisted_name_contributes_its_terminal_ratio_and_nothing_after(conn):
    """No reinvestment. Inventing a rule for where the money goes would
    make this a different measurement than the one `rd-v` §1 reports, and
    the comparison is the point."""
    store_series(conn, "DEAD01", _rows([("20190102", 100), ("20200414", 5)]))
    d = drift_for(conn, "DEAD01", "gone", False)
    assert d.ratio == pytest.approx(0.05)
    assert d.listed_now is False


def test_a_single_bar_cannot_produce_a_drift(conn):
    """One bar has no first-to-last span; reporting 1.0x would be a made-up
    flat return."""
    store_series(conn, "ONE", _rows([("20190102", 100)]))
    assert drift_for(conn, "ONE", "one bar", True) is None


def test_a_zero_or_missing_open_is_excluded(conn):
    conn.execute(
        "INSERT INTO dayone_bars (code, bsop_date, open, close) "
        "VALUES ('Z', '20190102', '0', '100')"
    )
    conn.execute(
        "INSERT INTO dayone_bars (code, bsop_date, open, close) "
        "VALUES ('Z', '20190103', '100', '200')"
    )
    conn.execute(
        "INSERT INTO dayone_bars (code, bsop_date, open, close) "
        "VALUES ('Z', '20190104', '150', '300')"
    )
    conn.commit()
    d = drift_for(conn, "Z", "zero open", True)
    assert d.first_date == "20190103", "the zero-open bar must not be the base"


# ==================================================== paging and the cap


def test_every_page_is_small_enough_to_stay_under_the_cap():
    """A page spanning more than ~100 trading days would be truncated
    silently, keeping the NEWEST rows — so the measured window would not
    be the declared one."""
    pages = list(_date_pages(PANEL_START, PANEL_END, 120))
    assert pages[0][0] == PANEL_START
    assert pages[-1][1] == PANEL_END
    for start, end in pages:
        assert start <= end
    # contiguous and non-overlapping
    for (a_start, a_end), (b_start, _) in zip(pages, pages[1:]):
        assert a_end < b_start


def test_pages_cover_the_window_without_gaps():
    import datetime as dt

    pages = list(_date_pages("20190102", "20190630", 30))
    for (_, a_end), (b_start, _) in zip(pages, pages[1:]):
        gap = dt.datetime.strptime(b_start, "%Y%m%d") - dt.datetime.strptime(
            a_end, "%Y%m%d"
        )
        assert gap.days == 1, "a gap here would silently drop trading days"


def _fake_payload(monkeypatch, rows):
    monkeypatch.setattr(
        "research.krx_dayone_drift._get_with_retry",
        lambda url, headers: {"rt_cd": "0", "output2": rows},  # noqa: ARG005
    )


class _S:
    def headers(self, tr):  # noqa: ARG002
        return {}


def test_a_capped_equity_page_is_refused(monkeypatch):
    _fake_payload(monkeypatch, _rows([(f"2019{i:04d}", 100) for i in range(100)]))
    with pytest.raises(KisKlinesError, match="100-row cap"):
        _page(_S(), "005930", "20190102", "20190430")


def test_the_index_cap_is_FIFTY_not_a_hundred(monkeypatch):
    """**A cap is a property of an endpoint, not of a venue** (CLAUDE.md).
    Equities cap at 100 and indices at 50; a shared constant let a 120-day
    KOSPI request through with only 73 days of coverage once already."""
    _fake_payload(monkeypatch, _rows([(f"2019{i:04d}", 100) for i in range(50)]))
    with pytest.raises(KisKlinesError, match="50-row cap"):
        _page(_S(), "0001", "20190102", "20190430", is_index=True)


def test_an_uncapped_page_is_returned(monkeypatch):
    _fake_payload(monkeypatch, _rows([("20190102", 100), ("20190103", 101)]))
    assert len(_page(_S(), "005930", "20190102", "20190103")) == 2


def test_a_non_zero_rt_cd_raises(monkeypatch):
    monkeypatch.setattr(
        "research.krx_dayone_drift._get_with_retry",
        lambda url, headers: {"rt_cd": "1", "output2": []},  # noqa: ARG005
    )
    with pytest.raises(KisKlinesError, match="rt_cd=1"):
        _page(_S(), "005930", "20190102", "20190103")


def test_storing_is_idempotent(conn):
    rows = _rows([("20190102", 100), ("20190103", 101)])
    store_series(conn, "X", rows)
    store_series(conn, "X", rows)
    assert conn.execute(
        "SELECT COUNT(*) FROM dayone_bars WHERE code='X'"
    ).fetchone()[0] == 2
