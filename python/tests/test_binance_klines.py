from decimal import Decimal

import pytest

from data.binance_klines import (
    FUTURES_KLINES_PATH,
    INTER_REQUEST_DELAY_S,
    SPOT_KLINES_PATH,
    BinanceKlinesError,
    fetch_klines_page,
    iter_klines_range,
)
from tests.fake_binance_server import FakeBinanceKlinesServer

STEP = 86_400_000  # 1d -- Binance's own real earliest-history granularity for this task
BASE = (1_700_000_000_000 // STEP) * STEP  # grid-aligned test epoch


@pytest.fixture
def server():
    srv = FakeBinanceKlinesServer()
    yield srv
    srv.close()


def _times(n: int, start: int = BASE) -> list[int]:
    return [start + i * STEP for i in range(n)]


# ---------------------------------------------------------------------------
# fetch_klines_page: parsing
# ---------------------------------------------------------------------------


def test_fetch_klines_page_parses_rows_with_exact_decimal_values(server):
    server.set_kline(BASE, "64175.4", "64200.0", "64100.5", "64160.1", "1.7765")

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert len(rows) == 1
    row = rows[0]
    assert row.open_time_ms == BASE
    assert row.open == Decimal("64175.4")
    assert row.high == Decimal("64200.0")
    assert row.low == Decimal("64100.5")
    assert row.close == Decimal("64160.1")
    assert row.volume == Decimal("1.7765")


def test_fetch_klines_page_uses_parse_float_decimal_to_avoid_float_roundtrip_precision_loss(server):
    # Binance is verified (see binance_klines.py's module docstring) to
    # always send OHLCV fields as quoted strings -- this guards the
    # defensive case where it sends a bare JSON number instead. A value
    # with enough significant digits to exceed float precision proves
    # json.loads(..., parse_float=Decimal) is actually wired up.
    precise_price = "64175.123456789012345"
    assert Decimal(str(float(precise_price))) != Decimal(precise_price), (
        "test value doesn't actually exercise float precision loss -- pick a more precise literal"
    )
    body = f'[[{BASE},{precise_price},1,1,1,1,{BASE + 59999},"0",0,"0","0","0"]]'
    server.force_response(200, body)

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert rows[0].open == Decimal(precise_price)


def test_fetch_klines_page_parses_a_realistic_12_element_row_ignoring_trailing_fields(server):
    server.set_kline(BASE, "1", "1", "1", "1", "1")

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert len(rows) == 1  # the row parses despite carrying 12 fields, not just the 6 load-bearing ones


def test_fetch_klines_page_sends_expected_path_and_query_params(server):
    server.set_kline(BASE, "1", "1", "1", "1", "1")

    fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP, limit=500)

    assert len(server.requests) == 1
    req = server.requests[0]
    assert req["path"] == SPOT_KLINES_PATH
    assert req["params"]["symbol"] == "BTCUSDT"
    assert req["params"]["interval"] == "1d"
    assert req["params"]["startTime"] == str(BASE)
    assert req["params"]["limit"] == "500"


def test_fetch_klines_page_works_against_the_futures_path_too(monkeypatch):
    # Same client code, different path -- proves `path` is a genuine
    # caller-supplied parameter, not something the client hardcodes to
    # the spot endpoint.
    srv = FakeBinanceKlinesServer(path=FUTURES_KLINES_PATH)
    try:
        srv.set_kline(BASE, "1", "1", "1", "1", "1")
        rows = fetch_klines_page(srv.base_url, FUTURES_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)
        assert len(rows) == 1
        assert srv.requests[0]["path"] == FUTURES_KLINES_PATH
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# fetch_klines_page: half-open -> Binance's real inclusive-both-ends wire
# translation (the load-bearing divergence from bingx_klines.py)
# ---------------------------------------------------------------------------


def test_fetch_klines_page_translates_half_open_end_to_binance_inclusive_end_minus_one_ms(server):
    server.set_kline(BASE, "1", "1", "1", "1", "1")

    fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    # Binance's real endTime is inclusive (verified live -- see module
    # docstring): the wire request must be end_ms - 1, not end_ms itself,
    # or a half-open [BASE, BASE+STEP) request would also (wrongly) match
    # a real candle sitting exactly at BASE+STEP.
    assert server.requests[0]["params"]["endTime"] == str(BASE + STEP - 1)


def test_fetch_klines_page_excludes_a_candle_exactly_at_the_half_open_end_boundary(server):
    server.set_klines([BASE, BASE + STEP])  # the second candle sits exactly at the exclusive end

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert [r.open_time_ms for r in rows] == [BASE]


def test_fetch_klines_page_includes_every_candle_up_to_but_not_past_the_half_open_end(server):
    server.set_klines(_times(5))

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + 5 * STEP)

    assert sorted(r.open_time_ms for r in rows) == _times(5)


# ---------------------------------------------------------------------------
# fetch_klines_page: pagination-boundary / silent-capping behavior --
# verified live to cap to the OLDEST rows, the opposite of BingX
# ---------------------------------------------------------------------------


def test_fetch_klines_page_returns_every_row_when_range_has_exactly_limit_candles(server):
    times = _times(5)
    server.set_klines(times)

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", times[0], times[-1] + STEP, limit=5)

    assert sorted(r.open_time_ms for r in rows) == times


def test_fetch_klines_page_silently_caps_to_oldest_rows_when_range_spans_more_than_limit(server):
    # Verified empirically against the live production endpoint
    # (both api.binance.com and fapi.binance.com) this session: a request
    # whose range spans more candles than `limit` is capped to the
    # `limit` candles closest to `startTime` -- the *oldest* ones, not
    # the newest. This is the opposite of BingX's own verified behavior.
    times = _times(8)
    server.set_klines(times)

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", times[0], times[-1] + STEP, limit=5)

    assert len(rows) == 5
    assert sorted(r.open_time_ms for r in rows) == times[:5]  # oldest 5, not newest 5


# ---------------------------------------------------------------------------
# fetch_klines_page: input validation (fail loud, never silently round)
# ---------------------------------------------------------------------------


def test_fetch_klines_page_rejects_misaligned_start_ms(server):
    with pytest.raises(ValueError, match="start_ms"):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE + 1, BASE + STEP)


def test_fetch_klines_page_rejects_misaligned_end_ms(server):
    with pytest.raises(ValueError, match="end_ms"):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP + 1)


def test_fetch_klines_page_rejects_start_not_before_end(server):
    with pytest.raises(ValueError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE + STEP, BASE)


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_fetch_klines_page_rejects_limit_out_of_range(server, limit):
    with pytest.raises(ValueError, match="limit"):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP, limit=limit)


def test_fetch_klines_page_rejects_unsupported_interval(server):
    # "5m" is named in CLAUDE.md's Current Scope but deliberately still
    # not wired into data._grid.INTERVAL_MS -- same stand-in role as
    # test_bingx_klines.py's own equivalent test.
    with pytest.raises(ValueError, match="interval"):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "5m", BASE, BASE + STEP)


# ---------------------------------------------------------------------------
# fetch_klines_page: malformed / error responses
# ---------------------------------------------------------------------------


def test_fetch_klines_page_raises_on_error_object_response(server):
    # Verified live: a malformed Binance request (bad symbol, limit out
    # of range, etc.) is a real non-2xx HTTP status with a JSON object
    # body `{"code": <int>, "msg": "..."}` -- never a 2xx with the error
    # embedded in the body the way BingX works. force_response bypasses
    # the fake server's normal 400-on-wrong-path handling to simulate a
    # 2xx-with-error-object response too, as defense in depth for a shape
    # this client has never actually observed live but still handles.
    server.force_response(200, '{"code":-1121,"msg":"Invalid symbol."}')

    with pytest.raises(BinanceKlinesError, match="-1121"):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)


def test_fetch_klines_page_raises_on_non_retryable_http_error_status(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    server.force_response(400, '{"code":-1121,"msg":"Invalid symbol."}')

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)


def test_fetch_klines_page_raises_on_malformed_json(server):
    server.force_response(200, "[not valid json")

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)


def test_fetch_klines_page_raises_when_body_is_neither_list_nor_object(server):
    server.force_response(200, "42")

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)


def test_fetch_klines_page_raises_when_row_has_too_few_fields(server):
    server.force_response(200, f"[[{BASE},\"1\",\"1\"]]")

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)


def test_fetch_klines_page_raises_when_row_is_not_an_array(server):
    server.force_response(200, '[{"open_time": 1}]')

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)


# ---------------------------------------------------------------------------
# fetch_klines_page: retry / backoff on transient errors
# ---------------------------------------------------------------------------


def test_fetch_klines_page_retries_on_429_then_succeeds(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    server.set_kline(BASE, "1", "1", "1", "1", "1")
    server.force_response(429, "rate limited", times=2)

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert len(rows) == 1
    assert len(server.requests) == 3  # 2 failed + 1 succeeded


def test_fetch_klines_page_retries_on_503_then_succeeds(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    server.set_kline(BASE, "1", "1", "1", "1", "1")
    server.force_response(503, "service unavailable", times=1)

    rows = fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert len(rows) == 1


def test_fetch_klines_page_raises_after_exhausting_retries_on_persistent_429(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    server.force_response(429, "rate limited", times=10)  # more than _MAX_RETRIES

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)


def test_fetch_klines_page_raises_immediately_on_418_ip_ban_without_retrying(server, monkeypatch):
    # Binance uses HTTP 418 specifically for an IP that got auto-banned
    # after repeated rate-limit violations -- retrying into a ban would
    # only extend it. Deliberately NOT in _RETRYABLE_STATUSES (unlike
    # 429), same fail-loud-rather-than-compound-the-problem reasoning as
    # bingx_klines.py's non-retryable-status test.
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    server.force_response(418, '{"code":-1003,"msg":"IP banned"}', times=10)

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert len(server.requests) == 1  # no retry wasted on a ban


def test_fetch_klines_page_raises_immediately_on_non_retryable_status_without_retrying(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    server.force_response(404, "not found", times=10)

    with pytest.raises(BinanceKlinesError):
        fetch_klines_page(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + STEP)

    assert len(server.requests) == 1  # no retry wasted on a non-retryable status


# ---------------------------------------------------------------------------
# iter_klines_range: pagination across chunks
# ---------------------------------------------------------------------------


def test_iter_klines_range_walks_multiple_chunks_for_a_range_wider_than_limit(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    times = _times(12)
    server.set_klines(times)

    rows = list(
        iter_klines_range(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", times[0], times[-1] + STEP, limit=5)
    )

    assert sorted(r.open_time_ms for r in rows) == times
    assert len(rows) == len(set(r.open_time_ms for r in rows))  # no duplicates
    assert len(server.requests) == 3  # chunks of 5, 5, 2


def test_iter_klines_range_cursor_derives_from_actual_max_row_time_not_naive_limit_arithmetic(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    times = _times(3)
    server.set_klines(times)

    rows = list(
        iter_klines_range(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + 5 * STEP, limit=5)
    )

    assert sorted(r.open_time_ms for r in rows) == times
    assert len(server.requests) == 2  # naive "start_ms + limit*step" arithmetic would have stopped after 1
    second_request_start = int(server.requests[1]["params"]["startTime"])
    assert second_request_start == BASE + 3 * STEP  # last real row's time + step
    assert second_request_start != BASE + 5 * STEP  # what start_ms + limit * interval_ms would give


def test_iter_klines_range_treats_empty_leading_region_as_normal_not_error(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)
    real_times = _times(2, start=BASE + 6 * STEP)
    server.set_klines(real_times)

    rows = list(
        iter_klines_range(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + 8 * STEP, limit=3)
    )

    assert sorted(r.open_time_ms for r in rows) == real_times
    assert len(server.requests) == 3  # 3 chunks of width 3 cover [0, 9); walk stops at end_ms=8*STEP


def test_iter_klines_range_returns_empty_for_a_range_with_no_data_at_all(server, monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)

    rows = list(
        iter_klines_range(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE, BASE + 4 * STEP, limit=2)
    )

    assert rows == []
    assert len(server.requests) == 2  # forward progress guaranteed, no infinite loop


def test_iter_klines_range_rejects_misaligned_range(server):
    with pytest.raises(ValueError):
        list(iter_klines_range(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE + 1, BASE + STEP))


def test_iter_klines_range_rejects_inverted_range(server):
    with pytest.raises(ValueError):
        list(iter_klines_range(server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", BASE + STEP, BASE))


def test_iter_klines_range_sleeps_between_page_requests_but_not_before_the_first(server):
    sleep_calls: list[float] = []

    import data.binance_klines as binance_klines_module

    original_sleep = binance_klines_module.time.sleep
    binance_klines_module.time.sleep = lambda s: sleep_calls.append(s)
    try:
        times = _times(6)
        server.set_klines(times)
        list(
            iter_klines_range(
                server.base_url, SPOT_KLINES_PATH, "BTCUSDT", "1d", times[0], times[-1] + STEP, limit=2
            )
        )
    finally:
        binance_klines_module.time.sleep = original_sleep

    assert len(server.requests) == 3  # chunks of 2, 2, 2
    assert sleep_calls == [INTER_REQUEST_DELAY_S, INTER_REQUEST_DELAY_S]  # between pages, not before the first
