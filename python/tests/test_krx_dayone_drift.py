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


# ============================ review findings, PR #190


def test_a_partial_NULL_row_is_refused_not_stored(conn):
    """**An all-NULL check catches a wrong endpoint mapping and nothing
    else.** A single NULL open on the FIRST row silently moves the
    measurement's start date, because `drift_for` filters it out; a NULL
    close on the LAST row survives that filter and divides by None."""
    from research.krx_dayone_drift import DayOneDriftError

    rows = _rows([("20190102", 100), ("20190103", 101)])
    rows[0]["stck_oprc"] = None
    with pytest.raises(DayOneDriftError, match="missing or"):
        store_series(conn, "X", rows)
    assert conn.execute("SELECT COUNT(*) FROM dayone_bars").fetchone()[0] == 0


@pytest.mark.parametrize("bad", [None, "", "abc", "0", "-5", "nan"])
def test_every_unusable_price_shape_is_refused(conn, bad):
    from research.krx_dayone_drift import DayOneDriftError

    rows = _rows([("20190102", 100), ("20190103", 101)])
    rows[1]["stck_clpr"] = bad
    with pytest.raises(DayOneDriftError):
        store_series(conn, "X", rows)


def test_an_all_null_series_names_the_endpoint_mapping(conn):
    """Kept distinct from the partial case: every row NULL is a wrong
    field map, not missing data, and the message has to say which."""
    from research.krx_dayone_drift import DayOneDriftError

    rows = _rows([("20190102", 100), ("20190103", 101)])
    for r in rows:
        r["stck_oprc"] = r["stck_clpr"] = r["stck_hgpr"] = r["stck_lwpr"] = None
    with pytest.raises(DayOneDriftError, match="wrong endpoint mapping"):
        store_series(conn, "X", rows)


def test_the_index_field_names_are_used_for_an_index(conn):
    """`bstp_nmix_*` against `stck_*` was the real defect: every price
    stored NULL, the `open IS NOT NULL` filter removed every row, and
    nothing threw."""
    idx = [
        {"stck_bsop_date": "20190102", "bstp_nmix_oprc": "2000",
         "bstp_nmix_hgpr": "2010", "bstp_nmix_lwpr": "1990",
         "bstp_nmix_prpr": "2005", "acml_tr_pbmn": "1"},
        {"stck_bsop_date": "20190103", "bstp_nmix_oprc": "2005",
         "bstp_nmix_hgpr": "2020", "bstp_nmix_lwpr": "2000",
         "bstp_nmix_prpr": "2015", "acml_tr_pbmn": "1"},
    ]
    store_series(conn, "IDX0001", idx, is_index=True)
    d = drift_for(conn, "IDX0001", "KOSPI", True)
    assert d is not None and d.ratio == pytest.approx(2015 / 2000)


def test_reading_equity_names_against_an_index_response_now_raises(conn):
    from research.krx_dayone_drift import DayOneDriftError

    idx = [{"stck_bsop_date": "20190102", "bstp_nmix_oprc": "2000",
            "bstp_nmix_hgpr": "2010", "bstp_nmix_lwpr": "1990",
            "bstp_nmix_prpr": "2005", "acml_tr_pbmn": "1"}]
    with pytest.raises(DayOneDriftError, match="wrong endpoint mapping"):
        store_series(conn, "IDX0001", idx)      # equity names, index rows


def test_measure_refuses_a_partial_panel(conn, tmp_path, monkeypatch):
    """**An equal-weight mean over whichever names happen to be stored is
    not the universe's return**, and nothing in the output would say so."""
    import json

    from research.krx_dayone_drift import DayOneDriftError, main

    store_series(conn, "005930", _rows([("20190102", 100), ("20260918", 300)]))
    universe = {"universe": [
        {"code": "005930", "name": "삼성전자", "listed_now": True},
        {"code": "000660", "name": "SK하이닉스", "listed_now": True},
    ]}
    path = tmp_path / "u.json"
    path.write_text(json.dumps(universe), encoding="utf-8")
    monkeypatch.setattr("research.krx_dayone_drift.connect", lambda *_a: conn)
    with pytest.raises(DayOneDriftError, match="no usable series"):
        main(["--measure", "--universe", str(path), "--db-path", str(tmp_path / "x")])
