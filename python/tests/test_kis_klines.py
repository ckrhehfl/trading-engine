"""Tests for `data.kis_klines` -- Multi-Asset Task C.

Every case here corresponds to something the Phase 0 probe actually
measured against the live KIS paper host
(`.planning/ms-b-kis-history-probe-result.md`), or to a failure mode this
project has already had once somewhere else. No HTTP is performed: the
transport is replaced, so what is under test is this module's own
handling of shapes the real endpoint is known to produce.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from data.kis_klines import (
    ADJUSTED,
    EQUITY_ROWS_PER_CALL_CAP,
    INDEX_ROWS_PER_CALL_CAP,
    RAW,
    ReferenceCalendarError,
    KisKlinesError,
    KisSession,
    _parse_row,
    equity_storage_symbol,
    fetch_daily_page,
    index_storage_symbol,
    iter_daily_range,
    missing_trading_days,
    ms_to_trading_date,
    trading_date_to_ms,
)


def _equity_row(date: str, *, close="70000", volume="1000", value="70000000", **over):
    row = {
        "stck_bsop_date": date,
        "stck_oprc": "69000",
        "stck_hgpr": "71000",
        "stck_lwpr": "68000",
        "stck_clpr": close,
        "acml_vol": volume,
        "acml_tr_pbmn": value,
    }
    row.update(over)
    return row


def _index_row(date: str, *, close="2700", **over):
    # The real field set, verified against the live endpoint 2026-09-13.
    row = {
        "stck_bsop_date": date,
        "bstp_nmix_oprc": "2690",
        "bstp_nmix_hgpr": "2710",
        "bstp_nmix_lwpr": "2680",
        "bstp_nmix_prpr": close,
        "acml_vol": "613718",
        "acml_tr_pbmn": "16353907",
        "mod_yn": "N",
    }
    row.update(over)
    return row


class _FakeSession:
    """Stands in for `KisSession` without issuing a token."""

    def __init__(self, host="https://example.invalid"):
        self.host = host

    def headers(self, tr_id):
        return {"tr_id": tr_id}


@pytest.fixture
def session():
    return _FakeSession()


@pytest.fixture
def responses(monkeypatch):
    """Queue of payloads `_get_with_retry` will return, in order."""
    queued: list[dict] = []
    seen: list[str] = []

    def fake_get(url, headers):
        seen.append(url)
        if not queued:
            raise AssertionError(f"an unexpected extra request was made: {url}")
        return queued.pop(0)

    monkeypatch.setattr("data.kis_klines._get_with_retry", fake_get)
    return queued, seen


# ------------------------------------------------------------ date mapping


def test_a_krx_trading_date_maps_onto_utc_midnight():
    # KRX opens 09:00 KST and KST is UTC+9, so this is an equality, not an
    # approximation -- which is why the existing 1d grid alignment holds.
    ms = trading_date_to_ms("20240502")
    assert ms % 86_400_000 == 0
    assert dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc) == dt.datetime(
        2024, 5, 2, tzinfo=dt.timezone.utc
    )
    assert ms_to_trading_date(ms) == "20240502"


@pytest.mark.parametrize("bad", ["2024050", "20240502x", "", "abcdefgh"])
def test_a_malformed_trading_date_is_rejected(bad):
    with pytest.raises(KisKlinesError):
        trading_date_to_ms(bad)


@pytest.mark.parametrize("bad", ["20241332", "20240230", "20240001", "20240100"])
def test_eight_digits_that_are_not_a_real_date_are_rejected_as_KisKlinesError(bad):
    """Shape is not validity. These pass the digit check and would have
    surfaced as a bare `ValueError` from `dt.date`, which a caller catching
    this module's own error type would not see."""
    with pytest.raises(KisKlinesError):
        trading_date_to_ms(bad)


@pytest.mark.parametrize("bad", ["2024", "", "20241332"])
def test_a_malformed_range_bound_raises_this_modules_error_type(session, bad):
    with pytest.raises(KisKlinesError):
        list(iter_daily_range(session, "005930", bad, "20240630", adjusted=ADJUSTED))
    with pytest.raises(KisKlinesError):
        list(iter_daily_range(session, "005930", "20240101", bad, adjusted=ADJUSTED))


def test_storage_symbols_keep_equities_and_indices_in_separate_namespaces():
    from data.kis_klines import RAW

    assert equity_storage_symbol("005930", adjusted=ADJUSTED) == "KRX:005930"
    assert index_storage_symbol("2001") == "KRX-INDEX:2001"
    assert equity_storage_symbol("005930", adjusted=ADJUSTED) != index_storage_symbol("005930")
    # Three namespaces now, and the index shares with neither. `KRX-RAW:` is
    # not a prefix of `KRX:` and vice versa, which is what keeps a
    # `startswith` reader from crossing them.
    assert equity_storage_symbol("005930", adjusted=RAW) != index_storage_symbol("005930")
    assert not "KRX-RAW:005930".startswith("KRX:")
    assert not "KRX:005930".startswith("KRX-RAW:")
    assert not "KRX-INDEX:2001".startswith(("KRX:", "KRX-RAW:"))


# ----------------------------------------------------------------- parsing


def test_an_equity_row_carries_traded_value_as_quote_volume():
    row = _parse_row(_equity_row("20240502"), is_index=False)
    assert row.close == Decimal("70000")
    assert row.volume == Decimal("1000")
    # 거래대금 -- the field the KR-10 universe rule ranks on.
    assert row.quote_volume == Decimal("70000000")


def test_an_index_row_keeps_the_traded_value_it_actually_carries():
    """An earlier version hardcoded `None` here on the claim that indices
    carry no traded value. The live endpoint's index `output2` includes
    `acml_tr_pbmn`, so that claim was untrue and discarded a real field."""
    row = _parse_row(_index_row("20240502"), is_index=True)
    assert row.close == Decimal("2700")
    assert row.quote_volume == Decimal("16353907")


def test_an_index_row_without_a_traded_value_records_absent_not_zero():
    row = _parse_row(_index_row("20240502", acml_tr_pbmn=""), is_index=True)
    assert row.quote_volume is None


@pytest.mark.parametrize(
    "field", ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol", "acml_tr_pbmn"]
)
def test_a_missing_price_field_fails_closed_rather_than_becoming_none(field):
    """KIS's per-endpoint field casing already caused three endpoints to
    parse every field as a silent null in this project once. The Python
    equivalent is a `.get()` that quietly yields `None`."""
    row = _equity_row("20240502")
    del row[field]
    with pytest.raises(KisKlinesError, match=field):
        _parse_row(row, is_index=False)


def test_a_non_numeric_price_fails_closed():
    with pytest.raises(KisKlinesError):
        _parse_row(_equity_row("20240502", stck_clpr="-"), is_index=False)


def test_internally_inconsistent_ohlc_is_rejected():
    # A low above the close cannot be a real bar; accepting it would put a
    # nonsense bar into a series nothing downstream re-checks.
    with pytest.raises(KisKlinesError, match="inconsistent"):
        _parse_row(_equity_row("20240502", stck_lwpr="99999"), is_index=False)


def test_a_row_without_a_date_is_rejected():
    row = _equity_row("20240502")
    del row["stck_bsop_date"]
    with pytest.raises(KisKlinesError):
        _parse_row(row, is_index=False)


# ---------------------------------------------------------------- fetching


def test_a_page_is_returned_ascending_regardless_of_wire_order(session, responses):
    queued, _ = responses
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240503"), _equity_row("20240502")]})
    rows = fetch_daily_page(session, "005930", "20240501", "20240510", adjusted=ADJUSTED)
    assert [ms_to_trading_date(r.open_time_ms) for r in rows] == ["20240502", "20240503"]


def test_an_application_error_raises_rather_than_returning_nothing(session, responses):
    queued, _ = responses
    queued.append({"rt_cd": "1", "msg_cd": "OPSQ0002", "output2": []})
    with pytest.raises(KisKlinesError, match="rt_cd=1"):
        fetch_daily_page(session, "005930", "20240501", "20240510", adjusted=ADJUSTED)


def test_a_TRANSIENT_rejection_is_retried_rather_than_aborting(session, responses,
                                                               monkeypatch):
    """**`OPSQ0003` is a real HTTP 200 carrying `rt_cd=1` for a request
    that answers normally next time.** Measured 2026-09-22: the index
    endpoint failed 3 of 15 identical calls on one window. A 47-call
    reference backfill at that rate completes with probability ~3e-5, and
    the first real attempt died on its 32nd call.
    """
    monkeypatch.setattr("data.kis_klines.time.sleep", lambda _s: None)
    queued, _ = responses
    queued.append({"rt_cd": "1", "msg_cd": "OPSQ0003", "output2": []})
    queued.append({"rt_cd": "1", "msg_cd": "OPSQ0003", "output2": []})
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240502")]})
    rows = fetch_daily_page(session, "005930", "20240501", "20240510", adjusted=ADJUSTED)
    assert [ms_to_trading_date(r.open_time_ms) for r in rows] == ["20240502"]
    assert not queued, "it stopped retrying as soon as the call succeeded"


def test_a_PERMANENT_rejection_is_NOT_retried(session, responses, monkeypatch):
    """**An allowlist, and this is what it buys.** `OPSQ0002` is "no such
    service code" -- a wrong TR id answers that way every time, and
    retrying into a real error is the mistake CLAUDE.md already records
    for Binance's HTTP 418. Exactly one call must be made."""
    monkeypatch.setattr("data.kis_klines.time.sleep", lambda _s: None)
    queued, seen = responses
    queued.append({"rt_cd": "1", "msg_cd": "OPSQ0002", "output2": []})
    with pytest.raises(KisKlinesError, match="rt_cd=1"):
        fetch_daily_page(session, "005930", "20240501", "20240510", adjusted=ADJUSTED)
    assert len(seen) == 1, f"a permanent error was retried {len(seen)} times"


def test_a_transient_rejection_that_never_clears_still_raises(session, responses,
                                                             monkeypatch):
    """Bounded. A retry that never gives up turns a venue outage into a
    hang, and the caller can no longer tell the two apart."""
    from data.kis_klines import _REJECTION_ATTEMPTS

    monkeypatch.setattr("data.kis_klines.time.sleep", lambda _s: None)
    queued, seen = responses
    for _ in range(_REJECTION_ATTEMPTS):
        queued.append({"rt_cd": "1", "msg_cd": "OPSQ0003", "output2": []})
    with pytest.raises(KisKlinesError, match="OPSQ0003"):
        fetch_daily_page(session, "005930", "20240501", "20240510", adjusted=ADJUSTED)
    assert len(seen) == _REJECTION_ATTEMPTS


def test_a_page_at_the_row_cap_raises_because_kis_truncates_silently(session, responses):
    """The probe measured a ~5-year request returning exactly 100 rows with
    `rt_cd=0` -- BingX's silent-cap behaviour, not Binance futures' real
    HTTP 400. A truncated page must never be mistaken for a complete one."""
    queued, _ = responses
    day = dt.date(2024, 1, 1)
    rows = []
    while len(rows) < EQUITY_ROWS_PER_CALL_CAP:
        if day.weekday() < 5:
            rows.append(_equity_row(day.strftime("%Y%m%d")))
        day += dt.timedelta(days=1)
    queued.append({"rt_cd": "0", "output2": rows})
    with pytest.raises(KisKlinesError, match="cap"):
        fetch_daily_page(session, "005930", "20240101", "20241231", adjusted=ADJUSTED)


def test_the_adjusted_flag_is_sent_and_has_no_default(session, responses):
    queued, seen = responses
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240502")]})
    fetch_daily_page(session, "005930", "20240501", "20240510", adjusted=ADJUSTED)
    assert "FID_ORG_ADJ_PRC=0" in seen[0]

    queued.append({"rt_cd": "0", "output2": [_equity_row("20240502")]})
    fetch_daily_page(session, "005930", "20240501", "20240510", adjusted=RAW)
    assert "FID_ORG_ADJ_PRC=1" in seen[1]

    with pytest.raises(TypeError):
        # No default: a caller must state which series it wants, because
        # KIS's own published sample defaults to the raw one and an
        # unadjusted series shows Samsung's 2018 split as a ~-98% day.
        fetch_daily_page(session, "005930", "20240501", "20240510")


def test_an_unknown_adjusted_value_is_rejected(session, responses):
    with pytest.raises(KisKlinesError):
        fetch_daily_page(session, "005930", "20240501", "20240510", adjusted="2")


def test_an_index_request_refuses_a_raw_price_distinction_it_does_not_have(session, responses):
    with pytest.raises(KisKlinesError):
        fetch_daily_page(
            session, "2001", "20240501", "20240510", adjusted=RAW, is_index=True
        )


def test_an_index_request_uses_the_index_endpoint_and_market_division(session, responses):
    queued, seen = responses
    queued.append({"rt_cd": "0", "output2": [_index_row("20240502")]})
    fetch_daily_page(session, "2001", "20240501", "20240510", adjusted=ADJUSTED, is_index=True)
    assert "inquire-daily-indexchartprice" in seen[0]
    assert "FID_COND_MRKT_DIV_CODE=U" in seen[0]
    assert "FID_ORG_ADJ_PRC" not in seen[0]


# ------------------------------------------------------------------ paging


def test_a_range_wider_than_one_window_is_paged(session, responses):
    queued, seen = responses
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240102")]})
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240502")]})
    rows = list(
        iter_daily_range(
            session, "005930", "20240101", "20240630", adjusted=ADJUSTED, window_days=120
        )
    )
    assert len(seen) == 2, "a 182-day range at a 120-day window is two pages"
    assert [ms_to_trading_date(r.open_time_ms) for r in rows] == ["20240102", "20240502"]


def test_a_date_repeated_across_a_page_boundary_is_yielded_once(session, responses):
    """KIS's date range is inclusive at both ends, so a boundary date can
    legitimately appear in two pages. Double-counting one would corrupt a
    return series without ever looking wrong."""
    queued, _ = responses
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240102"), _equity_row("20240430")]})
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240430"), _equity_row("20240502")]})
    rows = list(
        iter_daily_range(
            session, "005930", "20240101", "20240630", adjusted=ADJUSTED, window_days=120
        )
    )
    dates = [ms_to_trading_date(r.open_time_ms) for r in rows]
    assert dates == ["20240102", "20240430", "20240502"]
    assert len(dates) == len(set(dates))


def test_a_range_within_one_window_makes_exactly_one_request(session, responses):
    queued, seen = responses
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240502")]})
    list(iter_daily_range(session, "005930", "20240501", "20240510", adjusted=ADJUSTED))
    assert len(seen) == 1


def test_a_reversed_range_is_rejected(session):
    with pytest.raises(KisKlinesError):
        list(iter_daily_range(session, "005930", "20240630", "20240101", adjusted=ADJUSTED))


# ----------------------------------------------------- calendar-aware gaps


def test_missing_days_are_measured_against_the_index_not_an_arithmetic_grid():
    """`store.find_missing_ranges` diffs against `ts + interval_ms`, which
    reports every weekend and holiday as a gap for a market trading ~245
    days a year. The index prints exactly when the market is open."""
    index_days = {trading_date_to_ms(d) for d in ("20240502", "20240503", "20240507")}
    # 20240504-06 are a weekend plus a holiday: absent from the index too,
    # so they are not gaps at all.
    stock_days = {trading_date_to_ms(d) for d in ("20240502", "20240507")}
    missing = missing_trading_days(index_days, stock_days)
    assert [ms_to_trading_date(m) for m in missing] == ["20240503"]


@pytest.mark.parametrize("code", [400, 403, 429])
def test_a_4xx_is_not_retried(session, monkeypatch, code):
    """A 4xx is the server saying the request is wrong; retrying cannot fix
    it, and retrying a 403 spends more of the token allowance the live
    kis-paper JVM shares."""
    import urllib.error

    from data import kis_klines as kk

    attempts = []

    def fake_open(req, timeout=None):
        attempts.append(1)
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    with pytest.raises(KisKlinesError, match="not retried"):
        kk._get_with_retry("https://example.invalid/x", {})
    assert len(attempts) == 1, f"HTTP {code} was attempted {len(attempts)} times"


def test_a_5xx_is_retried(session, monkeypatch):
    import urllib.error

    from data import kis_klines as kk

    attempts = []

    def fake_open(req, timeout=None):
        attempts.append(1)
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(KisKlinesError):
        kk._get_with_retry("https://example.invalid/x", {})
    assert len(attempts) > 1, "a transient 500 must be retried"


def test_the_index_endpoint_has_its_own_lower_cap(session, responses):
    """Measured 2026-09-13: a 120-day KOSPI request came back with exactly
    50 bars covering only the newest 73 days of it, `rt_cd=0`, nothing to
    say so -- while the stock endpoint answered the identical range in
    full. A single shared cap constant let that truncated page through."""
    assert INDEX_ROWS_PER_CALL_CAP < EQUITY_ROWS_PER_CALL_CAP
    queued, _ = responses
    day = dt.date(2024, 1, 1)
    rows = []
    while len(rows) < INDEX_ROWS_PER_CALL_CAP:
        if day.weekday() < 5:
            rows.append(_index_row(day.strftime("%Y%m%d")))
        day += dt.timedelta(days=1)
    queued.append({"rt_cd": "0", "output2": rows})
    with pytest.raises(KisKlinesError, match="cap"):
        fetch_daily_page(
            session, "0001", "20240101", "20240429", adjusted=ADJUSTED, is_index=True
        )


def test_that_many_rows_is_fine_for_an_equity(session, responses):
    """The same count must NOT trip the equity path -- otherwise the fix is
    just a lower shared cap, which would reject legitimate stock pages."""
    queued, _ = responses
    day = dt.date(2024, 1, 1)
    rows = []
    while len(rows) < INDEX_ROWS_PER_CALL_CAP:
        if day.weekday() < 5:
            rows.append(_equity_row(day.strftime("%Y%m%d")))
        day += dt.timedelta(days=1)
    queued.append({"rt_cd": "0", "output2": rows})
    got = fetch_daily_page(session, "005930", "20240101", "20240429", adjusted=ADJUSTED)
    assert len(got) == INDEX_ROWS_PER_CALL_CAP


def test_an_index_range_pages_more_finely_than_an_equity_range(session, responses):
    queued, seen = responses
    for _ in range(4):
        queued.append({"rt_cd": "0", "output2": [_index_row("20240102")]})
    list(iter_daily_range(session, "0001", "20240101", "20240630", adjusted=ADJUSTED, is_index=True))
    index_pages = len(seen)
    seen.clear()
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240102")]})
    queued.append({"rt_cd": "0", "output2": [_equity_row("20240502")]})
    list(iter_daily_range(session, "005930", "20240101", "20240630", adjusted=ADJUSTED))
    assert index_pages > len(seen), "the lower index cap needs a smaller window"


def test_a_series_trading_when_the_reference_did_not_is_a_broken_reference():
    """The defect this catches, found on the real API: a truncated KOSPI
    series left `missing_trading_days` reporting ZERO gaps for Samsung
    while silently discarding the 31 dates that proved the reference was
    incomplete. Comparing one direction only is half a diff."""
    reference = {trading_date_to_ms(d) for d in ("20240216", "20240219")}
    series = {trading_date_to_ms(d) for d in ("20240102", "20240216", "20240219")}
    with pytest.raises(ReferenceCalendarError, match="20240102"):
        missing_trading_days(reference, series)


def test_a_complete_series_has_no_missing_days():
    days = {trading_date_to_ms(d) for d in ("20240502", "20240503")}
    assert missing_trading_days(days, days) == []


def test_missing_days_come_back_ascending():
    index_days = {trading_date_to_ms(d) for d in ("20240502", "20240503", "20240507")}
    missing = missing_trading_days(index_days, set())
    assert missing == sorted(missing)


# ------------------------------------------------------------------ session


def test_a_session_refuses_to_build_without_credentials():
    with pytest.raises(KisKlinesError):
        KisSession("", "secret")
    with pytest.raises(KisKlinesError):
        KisSession("key", "")


# --------------------- the basis is part of the symbol (audit F-4 / D4)


def test_the_two_bases_are_DIFFERENT_symbols():
    """**The defect this closes.** Raw and split-adjusted prices shared a
    storage symbol and primary key with no provenance, so
    `backfill_kis --adjusted 1` wrote 원주가 under `KRX:005930` and a later
    reader asking for adjusted prices got them with no way to tell.

    Measured across 삼성전자's 50:1 split (2018-05-04): adjusted
    53,000 -> 51,900, raw 2,650,000 -> 51,900. A momentum signal reads that
    second series as a -98% single day.
    """
    from data.kis_klines import RAW, equity_storage_symbol

    adj = equity_storage_symbol("005930", adjusted=ADJUSTED)
    raw = equity_storage_symbol("005930", adjusted=RAW)
    assert adj != raw, "the two bases can still collide under one primary key"
    assert adj == "KRX:005930"
    assert raw == "KRX-RAW:005930"


def test_the_adjusted_prefix_is_UNCHANGED_so_nothing_migrates():
    """4.6M existing rows sit under `KRX:`. Every one of them is either a
    daily bar fetched `--adjusted 0`, a minute bar (whose endpoint offers no
    basis at all), or 투자자별 매매동향 (quantities, not prices) -- so only the
    raw series moves and the migration is empty."""
    from data.kis_klines import equity_storage_symbol

    assert equity_storage_symbol("005930", adjusted=ADJUSTED) == "KRX:005930"


def test_the_basis_has_no_DEFAULT():
    """Keyword-only and required, the same discipline `fetch_daily_page`
    already applies to the same argument: KIS's own published sample defaults
    `FID_ORG_ADJ_PRC` to `1` (raw), so a default here would be a default on
    the most dangerous parameter in this module. Making it required is the
    fix -- the basis cannot be forgotten at a call site."""
    import inspect

    from data.kis_klines import equity_storage_symbol

    sig = inspect.signature(equity_storage_symbol)
    param = sig.parameters["adjusted"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty, "the basis acquired a default"
    with pytest.raises(TypeError):
        equity_storage_symbol("005930")


@pytest.mark.parametrize("bad", ["2", "", "adjusted", None, "00"])
def test_an_unknown_basis_cannot_name_a_symbol(bad):
    """A series whose basis is unknown must not be stored at all -- storing
    it is what created the ambiguity in the first place."""
    from data.kis_klines import KisKlinesError, equity_storage_symbol

    with pytest.raises(KisKlinesError, match="basis is unknown|adjusted must be"):
        equity_storage_symbol("005930", adjusted=bad)


def test_a_symbol_can_be_asked_what_basis_it_declares():
    """So a reader can check rather than assume, which is the half of F-4
    that a prefix alone does not give you."""
    from data.kis_klines import (
        RAW,
        KisKlinesError,
        equity_storage_symbol,
        index_storage_symbol,
        storage_symbol_basis,
    )

    assert storage_symbol_basis(equity_storage_symbol("005930", adjusted=ADJUSTED)) == ADJUSTED
    assert storage_symbol_basis(equity_storage_symbol("005930", adjusted=RAW)) == RAW
    for other in (index_storage_symbol("2001"), "BTC-USDT", "", "KRX", "kRX:005930"):
        with pytest.raises(KisKlinesError, match="not a KRX equity storage symbol"):
            storage_symbol_basis(other)


def test_no_module_builds_a_KRX_equity_symbol_by_hand():
    """A formatted prefix bypasses the required argument entirely, which is
    how a rule in one place gets ignored in another -- the same failure the
    pool predicate had."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "data"
    for path in root.glob("*.py"):
        if path.name == "kis_klines.py":
            continue
        text = path.read_text(encoding="utf-8")
        for bad in ('f"KRX:{', "'KRX:' +", '"KRX:" +', 'f"KRX-RAW:{'):
            assert bad not in text, (
                f"{path.name} formats a KRX equity symbol by hand ({bad!r}) "
                f"instead of calling equity_storage_symbol"
            )
