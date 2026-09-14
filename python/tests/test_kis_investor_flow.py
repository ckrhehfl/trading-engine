"""Tests for `data.kis_investor_flow`.

The cases that matter are about **integrity on ingest**, because this
series cannot be backfilled: a wrong value written today cannot be
re-fetched tomorrow and corrected. So every parse failure fails closed,
and the buy/sell/net identity is checked rather than assumed.
"""

from __future__ import annotations

import pytest

from data.kis_investor_flow import (
    INVESTOR_TYPES,
    METRIC_PREFIX,
    FlowPoint,
    InvestorFlowError,
    metric_name,
    parse_rows,
    resolve_symbols,
    sync_symbol,
)
from data.kis_klines import trading_date_to_ms
from data.store import connect, fetch_positioning, upsert_krx_universe


def _row(date="20260914", **over):
    """One KIS `output` row with a consistent buy/sell/net triple."""
    row = {"stck_bsop_date": date}
    for i, prefix in enumerate(INVESTOR_TYPES):
        buy, sell = 1000 + i * 100, 400 + i * 10
        row.update(
            {
                f"{prefix}_shnu_vol": str(buy),
                f"{prefix}_seln_vol": str(sell),
                f"{prefix}_ntby_qty": str(buy - sell),
                f"{prefix}_shnu_tr_pbmn": str(buy * 70000),
                f"{prefix}_seln_tr_pbmn": str(sell * 70000),
            }
        )
    row.update(over)
    return row


def _body(*rows):
    return {"rt_cd": "0", "msg1": "정상처리 되었습니다.", "output": list(rows)}


# ------------------------------------------------------------- parsing


def test_every_investor_side_and_unit_becomes_a_metric():
    points = parse_rows(_body(_row()))
    # 3 investor types x 2 sides x 2 units
    assert len(points) == 12
    assert {p.metric for p in points} == {
        metric_name(inv, side, unit)
        for inv in INVESTOR_TYPES.values()
        for side in ("buy", "sell")
        for unit in ("qty", "value")
    }


def test_metric_names_are_namespaced():
    """The `positioning` table is shared with Binance series, so a bare
    `buy.qty` would be ambiguous about which venue it came from."""
    assert all(p.metric.startswith(f"{METRIC_PREFIX}.") for p in parse_rows(_body(_row())))


def test_traded_value_is_converted_from_millions_to_won():
    """`tr_pbmn` is denominated in 백만원 and KIS documents this nowhere.

    Measured against 삼성전자 2026-09-14: 개인 buy was 6,697,584 shares for
    a raw value of 1,675,852. Read as 원 that is 0.25 원/share; read as
    백만원 it is 250,217 원/share, against a close near 261,000. Stored
    in 원 so it is directly comparable to `klines.quote_volume`.
    """
    row = _row(prsn_shnu_vol="6697584", prsn_seln_vol="0", prsn_ntby_qty="6697584",
               prsn_shnu_tr_pbmn="1675852")
    points = {p.metric: int(p.value) for p in parse_rows(_body(row))}
    value = points[metric_name("individual", "buy", "value")]
    qty = points[metric_name("individual", "buy", "qty")]
    assert value == 1675852 * 1_000_000
    assert 200_000 < value / qty < 320_000, "implied price is not a plausible 삼성전자 price"


def test_quantities_are_not_scaled():
    """Only `tr_pbmn` carries the 백만원 quirk; share counts are share counts."""
    row = _row(prsn_shnu_vol="6697584", prsn_seln_vol="0", prsn_ntby_qty="6697584")
    points = {p.metric: int(p.value) for p in parse_rows(_body(row))}
    assert points[metric_name("individual", "buy", "qty")] == 6697584


def test_the_net_is_not_stored():
    """It is `buy - sell` by definition. Storing it too would be 50% more
    rows carrying no information, and would let the two disagree."""
    metrics = {p.metric for p in parse_rows(_body(_row()))}
    assert not any("net" in m for m in metrics)


def test_a_net_that_disagrees_with_its_legs_fails_closed():
    """The regression that matters most: a silent disagreement would mean
    the gross legs are not what their names say, and the gross legs are
    the whole reason for preferring them."""
    bad = _row(prsn_ntby_qty="999999")
    with pytest.raises(InvestorFlowError, match="net does not equal"):
        parse_rows(_body(bad))


@pytest.mark.parametrize(
    "field", ["prsn_shnu_vol", "frgn_seln_tr_pbmn", "orgn_shnu_tr_pbmn"]
)
def test_a_missing_numeric_field_is_refused_not_defaulted(field):
    """A missing field read as zero is indistinguishable from a real zero
    and cannot be re-fetched later."""
    with pytest.raises(InvestorFlowError, match=field):
        parse_rows(_body(_row(**{field: ""})))


def test_a_blank_field_is_refused():
    with pytest.raises(InvestorFlowError):
        parse_rows(_body(_row(prsn_shnu_vol=None)))


def test_a_non_numeric_field_is_refused():
    with pytest.raises(InvestorFlowError, match="not an integer"):
        parse_rows(_body(_row(prsn_shnu_vol="1,000")))


@pytest.mark.parametrize("date", ["", "2026-09-14", "2026091", "abcdefgh"])
def test_an_unusable_date_is_refused(date):
    with pytest.raises(InvestorFlowError, match="stck_bsop_date"):
        parse_rows(_body(_row(stck_bsop_date=date)))


def test_a_rejected_request_is_refused_rather_than_parsed_as_empty():
    with pytest.raises(InvestorFlowError, match="rt_cd"):
        parse_rows({"rt_cd": "1", "msg1": "오류", "output": []})


def test_a_missing_output_is_refused():
    with pytest.raises(InvestorFlowError, match="output"):
        parse_rows({"rt_cd": "0"})


def test_no_exception_message_embeds_the_response_body():
    """Every exception in the KIS data path reports field and date, never
    a value -- `kis_klines._decimal` set that precedent.

    This endpoint returns aggregate market data rather than account data,
    so the strict reading is arguably unnecessary here. It is applied
    anyway: deciding per-endpoint what counts as sensitive is exactly the
    judgment that drifts, and these messages land in a persisted cron log.
    """
    body = _body(_row(prsn_ntby_qty="123456789"))
    with pytest.raises(InvestorFlowError) as exc:
        parse_rows(body)
    assert "123456789" not in str(exc.value)


# ------------------------------------------------------------- storage


class _FakeSession:
    host = "https://example.invalid"

    def headers(self, tr_id):
        return {"tr_id": tr_id}


def test_sync_writes_under_the_namespaced_symbol(tmp_path, monkeypatch):
    conn = connect(tmp_path / "k.sqlite3")
    monkeypatch.setattr(
        "data.kis_investor_flow.fetch_flow",
        lambda s, c: parse_rows(_body(_row("20260914"))),
    )
    assert sync_symbol(_FakeSession(), conn, "005930") == 12
    stored = {r[0] for r in conn.execute("SELECT symbol FROM positioning")}
    assert stored == {"KRX:005930"}
    conn.close()


def test_a_rerun_writes_nothing_new(tmp_path, monkeypatch):
    """The collector re-requests the same 30-day window every day by
    design, so re-running must be a no-op rather than a duplicate."""
    conn = connect(tmp_path / "k.sqlite3")
    monkeypatch.setattr(
        "data.kis_investor_flow.fetch_flow",
        lambda s, c: parse_rows(_body(_row("20260914"))),
    )
    assert sync_symbol(_FakeSession(), conn, "005930") == 12
    assert sync_symbol(_FakeSession(), conn, "005930") == 0
    conn.close()


def test_a_trading_date_is_stored_at_utc_midnight(tmp_path, monkeypatch):
    """A KRX trading date maps exactly onto UTC midnight -- an equality
    this project already verified, not a rounding."""
    conn = connect(tmp_path / "k.sqlite3")
    monkeypatch.setattr(
        "data.kis_investor_flow.fetch_flow",
        lambda s, c: parse_rows(_body(_row("20240502"))),
    )
    sync_symbol(_FakeSession(), conn, "005930")
    rows = fetch_positioning(
        conn,
        "KRX:005930",
        metric_name("individual", "buy", "qty"),
        "1d",
        trading_date_to_ms("20240501"),
        trading_date_to_ms("20240503"),
    )
    assert [r.timestamp_ms for r in rows] == [trading_date_to_ms("20240502")]
    conn.close()


# ------------------------------------------------------------ universe


class _Args:
    def __init__(self, symbols=None, universe_date=None):
        self.symbols = symbols
        self.universe_date = universe_date


def test_explicit_symbols_win_over_the_universe(tmp_path):
    conn = connect(tmp_path / "k.sqlite3")
    assert resolve_symbols(conn, _Args(symbols="005930, 000660")) == ["005930", "000660"]
    conn.close()


def test_the_universe_resolves_to_common_stock_only(tmp_path):
    conn = connect(tmp_path / "k.sqlite3")
    upsert_krx_universe(
        conn,
        "2026-09-14",
        [
            ("005930", "KOSPI", "삼성전자", "ST"),
            ("069500", "KOSPI", "KODEX 200", "EF"),
            ("900110", "KOSDAQ", "딥커머스", "ST"),
        ],
    )
    assert resolve_symbols(conn, _Args()) == ["005930", "900110"]
    conn.close()


def test_the_universe_without_a_snapshot_fails_closed(tmp_path):
    """Silently collecting nothing would look like a clean run on a
    series whose whole problem is that a missed day is unrecoverable."""
    conn = connect(tmp_path / "k.sqlite3")
    with pytest.raises(InvestorFlowError, match="krx_universe snapshot"):
        resolve_symbols(conn, _Args())
    conn.close()
