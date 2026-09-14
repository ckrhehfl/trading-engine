"""Tests for `data.krx_universe`.

The defect this module is most exposed to is **silent**: KOSPI's and
KOSDAQ's master files carry fixed tails of different lengths (228 vs 222),
so one shared offset reads the wrong two characters for one market and
every group code comes back as whitespace. That produces a universe of
zero common stocks while the download, the unzip, and the row count all
look perfect. It was hit for real during the 2026-09-14 probe.

So the offsets are pinned per market here, and `parse_master` fails closed
when a file yields no common stock at all.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from data.krx_universe import (
    COMMON_STOCK,
    GROUP_CODE_OFFSET,
    KrxUniverseError,
    Listing,
    parse_master,
    snapshot,
)
from data.store import connect, fetch_krx_universe, krx_universe_snapshots


def _row(code: str, name: str, group: str, market: str) -> str:
    """A synthetic master row with the real layout: 단축코드(9) +
    표준코드(12) + variable-length name + a fixed tail whose first two
    characters are the group code."""
    tail_len = GROUP_CODE_OFFSET[market]
    tail = group + "X" * (tail_len - 2)
    return f"{code:<9}{'KR' + code + '00':<12}{name}{tail}"


def _zip(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("code.mst", "\n".join(rows).encode("cp949"))
    return buf.getvalue()


def _kospi(rows):
    return _zip([_row(c, n, g, "KOSPI") for c, n, g in rows])


def _kosdaq(rows):
    return _zip([_row(c, n, g, "KOSDAQ") for c, n, g in rows])


# ------------------------------------------------------------- parsing


def test_a_kospi_row_parses_into_its_fields():
    (listing,) = parse_master(_kospi([("005930", "삼성전자", "ST")]), "KOSPI")
    assert listing == Listing("005930", "KOSPI", "삼성전자", "ST")


def test_a_kosdaq_row_parses_at_its_own_offset():
    """The whole point: KOSDAQ's tail is 6 bytes shorter than KOSPI's."""
    (listing,) = parse_master(_kosdaq([("900110", "딥커머스", "ST")]), "KOSDAQ")
    assert listing == Listing("900110", "KOSDAQ", "딥커머스", "ST")


def test_the_two_markets_do_not_share_an_offset():
    """A regression against the real bug. Parsing a KOSDAQ file with
    KOSPI's offset must not quietly succeed."""
    assert GROUP_CODE_OFFSET["KOSPI"] != GROUP_CODE_OFFSET["KOSDAQ"]
    with pytest.raises(KrxUniverseError, match="offset is probably wrong"):
        parse_master(_kosdaq([("900110", "딥커머스", "ST")]), "KOSPI")


def test_a_file_with_no_common_stock_fails_closed():
    """Zero common stock is never a legitimate answer for a whole market,
    and a silent empty universe would look exactly like a quiet day."""
    with pytest.raises(KrxUniverseError, match="zero common stock"):
        parse_master(_kospi([("069500", "KODEX 200", "EF")]), "KOSPI")


def test_etfs_are_parsed_rather_than_dropped():
    """The record keeps them so a future reader can confirm the `ST`
    filter was applied, rather than trust that it was."""
    listings = parse_master(
        _kospi([("005930", "삼성전자", "ST"), ("069500", "KODEX 200", "EF")]), "KOSPI"
    )
    assert {x.group_code for x in listings} == {"ST", "EF"}


def test_korean_names_survive_the_cp949_round_trip():
    (listing,) = parse_master(_kospi([("000660", "SK하이닉스", "ST")]), "KOSPI")
    assert listing.name == "SK하이닉스"


def test_an_empty_file_is_refused():
    with pytest.raises(KrxUniverseError, match="zero rows"):
        parse_master(_zip([]), "KOSPI")


def test_a_non_zip_payload_is_refused():
    with pytest.raises(KrxUniverseError, match="not a zip"):
        parse_master(b"not a zip at all", "KOSPI")


def test_an_unknown_market_is_refused():
    with pytest.raises(KrxUniverseError, match="unknown market"):
        parse_master(_kospi([("005930", "삼성전자", "ST")]), "KONEX")


# ------------------------------------------------------------ snapshot


def test_a_snapshot_records_both_markets(tmp_path, monkeypatch):
    conn = connect(tmp_path / "k.sqlite3")
    monkeypatch.setattr(
        "data.krx_universe.fetch_universe",
        lambda: [
            Listing("005930", "KOSPI", "삼성전자", "ST"),
            Listing("069500", "KOSPI", "KODEX 200", "EF"),
            Listing("900110", "KOSDAQ", "딥커머스", "ST"),
        ],
    )
    date, written, common = snapshot(conn, "2026-09-14")
    assert (date, written, common) == ("2026-09-14", 3, 2)
    assert [c for c, _, _, _ in fetch_krx_universe(conn, "2026-09-14")] == [
        "005930",
        "900110",
    ]
    conn.close()


def test_rerunning_a_snapshot_is_a_no_op(tmp_path, monkeypatch):
    """A cron that fires twice must not corrupt the survivorship record."""
    conn = connect(tmp_path / "k.sqlite3")
    monkeypatch.setattr(
        "data.krx_universe.fetch_universe",
        lambda: [Listing("005930", "KOSPI", "삼성전자", COMMON_STOCK)],
    )
    assert snapshot(conn, "2026-09-14")[1] == 1
    assert snapshot(conn, "2026-09-14")[1] == 0
    assert krx_universe_snapshots(conn) == [("2026-09-14", 1, 1)]
    conn.close()


def test_snapshots_on_different_dates_are_separate_records(tmp_path, monkeypatch):
    """The point of the table: 'who was listed on date D' must be
    answerable per date, or it records nothing about delisting."""
    conn = connect(tmp_path / "k.sqlite3")
    monkeypatch.setattr(
        "data.krx_universe.fetch_universe",
        lambda: [Listing("005930", "KOSPI", "삼성전자", "ST")],
    )
    snapshot(conn, "2026-09-14")
    monkeypatch.setattr(
        "data.krx_universe.fetch_universe",
        lambda: [
            Listing("005930", "KOSPI", "삼성전자", "ST"),
            Listing("123456", "KOSDAQ", "신규상장", "ST"),
        ],
    )
    snapshot(conn, "2026-09-15")
    assert krx_universe_snapshots(conn) == [("2026-09-14", 1, 1), ("2026-09-15", 2, 2)]
    assert len(fetch_krx_universe(conn, "2026-09-14")) == 1
    assert len(fetch_krx_universe(conn, "2026-09-15")) == 2
    conn.close()


def test_a_malformed_snapshot_date_is_refused(tmp_path, monkeypatch):
    conn = connect(tmp_path / "k.sqlite3")
    monkeypatch.setattr(
        "data.krx_universe.fetch_universe",
        lambda: [Listing("005930", "KOSPI", "삼성전자", "ST")],
    )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        snapshot(conn, "20260914")
    conn.close()


def test_this_module_never_authenticates():
    """It fetches public static files. If it ever grew a credential it
    would need the AST order-capability guard, which it currently does not
    appear in -- so the absence is asserted rather than assumed."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "data" / "krx_universe.py").read_text(
        encoding="utf-8"
    )
    for secret in ("KIS_APP_KEY", "KIS_APP_SECRET", "appkey", "appsecret", "tokenP"):
        assert secret not in source, f"{secret} appeared in a module that must stay anonymous"
