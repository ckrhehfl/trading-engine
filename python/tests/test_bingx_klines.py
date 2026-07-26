from decimal import Decimal

import pytest

from data.bingx_klines import (
    INTER_REQUEST_DELAY_S,
    BingXKlinesError,
    fetch_klines_page,
    iter_klines_range,
)
from tests.fake_bingx_server import KLINES_PATH, FakeBingXKlinesServer

STEP = 900_000
BASE = (1_700_000_000_000 // STEP) * STEP  # grid-aligned test epoch


@pytest.fixture
def server():
    srv = FakeBingXKlinesServer()
    yield srv
    srv.close()


def _times(n: int, start: int = BASE) -> list[int]:
    return [start + i * STEP for i in range(n)]


# ---------------------------------------------------------------------------
# fetch_klines_page: parsing
# ---------------------------------------------------------------------------


def test_fetch_klines_page_parses_rows_with_exact_decimal_values(server):
    server.set_kline(BASE, "64175.4", "64200.0", "64100.5", "64160.1", "1.7765")

    rows = fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert len(rows) == 1
    row = rows[0]
    assert row.open_time_ms == BASE
    assert row.open == Decimal("64175.4")
    assert row.high == Decimal("64200.0")
    assert row.low == Decimal("64100.5")
    assert row.close == Decimal("64160.1")
    assert row.volume == Decimal("1.7765")


def test_fetch_klines_page_uses_parse_float_decimal_to_avoid_float_roundtrip_precision_loss(server):
    # BingX is verified (see bingx_klines.py's module docstring) to
    # always send OHLCV fields as quoted strings -- this guards the
    # defensive case where it sends a bare JSON number instead. A value
    # with enough significant digits to exceed float precision proves
    # json.loads(..., parse_float=Decimal) is actually wired up: a
    # json.loads() (no parse_float) followed by Decimal(str(x)) would
    # silently round this through float first.
    precise_price = "64175.123456789012345"
    assert Decimal(str(float(precise_price))) != Decimal(precise_price), (
        "test value doesn't actually exercise float precision loss -- pick a more precise literal"
    )
    body = (
        '{"code":0,"msg":"","data":[{"open":'
        + precise_price
        + ',"high":1,"low":1,"close":1,"volume":1,"time":'
        + str(BASE)
        + "}]}"
    )
    server.force_response(200, body)

    rows = fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert rows[0].open == Decimal(precise_price)


def test_fetch_klines_page_sends_expected_path_and_query_params(server):
    server.set_kline(BASE, "1", "1", "1", "1", "1")

    fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP, limit=500)

    assert len(server.requests) == 1
    req = server.requests[0]
    assert req["path"] == KLINES_PATH
    assert req["params"]["symbol"] == "BTC-USDT"
    assert req["params"]["interval"] == "15m"
    assert req["params"]["startTime"] == str(BASE)
    assert req["params"]["endTime"] == str(BASE + STEP)
    assert req["params"]["limit"] == "500"


# ---------------------------------------------------------------------------
# fetch_klines_page: pagination-boundary / silent-capping behavior
# ---------------------------------------------------------------------------


def test_fetch_klines_page_returns_every_row_when_range_has_exactly_limit_candles(server):
    times = _times(5)
    server.set_klines(times)

    rows = fetch_klines_page(server.base_url, "BTC-USDT", "15m", times[0], times[-1] + STEP, limit=5)

    assert sorted(r.open_time_ms for r in rows) == times


def test_fetch_klines_page_silently_caps_to_newest_rows_when_range_spans_more_than_limit(server):
    # Verified empirically against the live production endpoint
    # 2026-07-26: a request whose range spans more candles than `limit`
    # is capped to the `limit` candles closest to endTime -- the
    # *newest* ones, not the oldest. This is the exact behavior
    # `iter_klines_range`'s chunking exists to never actually trigger.
    times = _times(8)
    server.set_klines(times)

    rows = fetch_klines_page(server.base_url, "BTC-USDT", "15m", times[0], times[-1] + STEP, limit=5)

    assert len(rows) == 5
    assert sorted(r.open_time_ms for r in rows) == times[-5:]  # newest 5, not oldest 5


# ---------------------------------------------------------------------------
# fetch_klines_page: input validation (fail loud, never silently round)
# ---------------------------------------------------------------------------


def test_fetch_klines_page_rejects_misaligned_start_ms(server):
    with pytest.raises(ValueError, match="start_ms"):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE + 1, BASE + STEP)


def test_fetch_klines_page_rejects_misaligned_end_ms(server):
    with pytest.raises(ValueError, match="end_ms"):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP + 1)


def test_fetch_klines_page_rejects_start_not_before_end(server):
    with pytest.raises(ValueError):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE + STEP, BASE)


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_fetch_klines_page_rejects_limit_out_of_range(server, limit):
    with pytest.raises(ValueError, match="limit"):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP, limit=limit)


def test_fetch_klines_page_rejects_unsupported_interval(server):
    with pytest.raises(ValueError, match="interval"):
        fetch_klines_page(server.base_url, "BTC-USDT", "1d", BASE, BASE + STEP)


# ---------------------------------------------------------------------------
# fetch_klines_page: malformed / error responses
# ---------------------------------------------------------------------------


def test_fetch_klines_page_raises_on_non_zero_code(server):
    server.force_response(200, '{"code":100410,"msg":"symbol not found","data":[]}')

    with pytest.raises(BingXKlinesError, match="100410"):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)


def test_fetch_klines_page_raises_on_malformed_json(server):
    server.force_response(200, "{not valid json")

    with pytest.raises(BingXKlinesError):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)


def test_fetch_klines_page_raises_when_data_is_not_a_list(server):
    server.force_response(200, '{"code":0,"msg":"","data":{}}')

    with pytest.raises(BingXKlinesError):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)


def test_fetch_klines_page_raises_when_row_is_missing_a_required_field(server):
    server.force_response(200, f'{{"code":0,"msg":"","data":[{{"time":{BASE}}}]}}')

    with pytest.raises(BingXKlinesError):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)


# ---------------------------------------------------------------------------
# fetch_klines_page: retry / backoff on transient errors
# ---------------------------------------------------------------------------


def test_fetch_klines_page_retries_on_429_then_succeeds(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    server.set_kline(BASE, "1", "1", "1", "1", "1")
    server.force_response(429, "rate limited", times=2)

    rows = fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert len(rows) == 1
    assert len(server.requests) == 3  # 2 failed + 1 succeeded


def test_fetch_klines_page_retries_on_503_then_succeeds(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    server.set_kline(BASE, "1", "1", "1", "1", "1")
    server.force_response(503, "service unavailable", times=1)

    rows = fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert len(rows) == 1


def test_fetch_klines_page_retries_on_a_read_time_os_error_then_succeeds(server, monkeypatch):
    # A connection can be reset *after* urlopen() already succeeded in
    # establishing the response -- e.g. mid-body-read -- which surfaces
    # as a raw OSError (ConnectionResetError is a subclass), not
    # urllib.error.URLError (that only covers failures up through
    # getting a response at all). Simulates this by making the first
    # urlopen() call return a fake response whose .read() raises;
    # the second call goes through to the real fake server and succeeds,
    # proving the retry loop's OSError handling actually triggers a
    # fresh request rather than propagating the read failure.
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    server.set_kline(BASE, "1", "1", "1", "1", "1")

    import data.bingx_klines as bingx_klines_module

    real_urlopen = bingx_klines_module.urllib.request.urlopen
    call_count = {"n": 0}

    class _ResetOnRead:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            raise ConnectionResetError("simulated connection reset mid-read")

    def flaky_urlopen(request, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _ResetOnRead()
        return real_urlopen(request, timeout=timeout)

    monkeypatch.setattr("data.bingx_klines.urllib.request.urlopen", flaky_urlopen)

    rows = fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert len(rows) == 1
    assert call_count["n"] == 2


def test_fetch_klines_page_raises_after_exhausting_retries_on_persistent_429(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    server.force_response(429, "rate limited", times=10)  # more than _MAX_RETRIES

    with pytest.raises(BingXKlinesError):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)


def test_fetch_klines_page_raises_immediately_on_non_retryable_status_without_retrying(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    server.force_response(404, "not found", times=10)

    with pytest.raises(BingXKlinesError):
        fetch_klines_page(server.base_url, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert len(server.requests) == 1  # no retry wasted on a non-retryable status


# ---------------------------------------------------------------------------
# iter_klines_range: pagination across chunks
# ---------------------------------------------------------------------------


def test_iter_klines_range_walks_multiple_chunks_for_a_range_wider_than_limit(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    times = _times(12)
    server.set_klines(times)

    rows = list(
        iter_klines_range(server.base_url, "BTC-USDT", "15m", times[0], times[-1] + STEP, limit=5)
    )

    assert sorted(r.open_time_ms for r in rows) == times
    assert len(rows) == len(set(r.open_time_ms for r in rows))  # no duplicates
    assert len(server.requests) == 3  # chunks of 5, 5, 2


def test_iter_klines_range_cursor_derives_from_actual_max_row_time_not_naive_limit_arithmetic(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    # Populate only the first 3 of a 5-slot (limit=5) window -- slots 3
    # and 4 are a real trailing gap (e.g. the walk has reached "now" or
    # the exchange's retention boundary mid-chunk). A naive
    # "start_ms + limit * interval_ms" cursor would treat the single
    # first request as having fully covered up through start_ms + 5*step
    # and stop there. The correct behavior re-derives the cursor from
    # the actual last returned row (slot 2's time + step) and issues a
    # second, narrower request for the still-unconfirmed tail.
    times = _times(3)
    server.set_klines(times)

    rows = list(
        iter_klines_range(server.base_url, "BTC-USDT", "15m", BASE, BASE + 5 * STEP, limit=5)
    )

    assert sorted(r.open_time_ms for r in rows) == times
    assert len(server.requests) == 2  # naive "start_ms + limit*step" arithmetic would have stopped after 1
    second_request_start = int(server.requests[1]["params"]["startTime"])
    assert second_request_start == BASE + 3 * STEP  # last real row's time + step
    assert second_request_start != BASE + 5 * STEP  # what start_ms + limit * interval_ms would give


def test_iter_klines_range_treats_empty_leading_region_as_normal_not_error(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)
    # No data in the first two chunks (walking forward from an early
    # sentinel past the exchange's real retention start) -- real data
    # only appears in the third chunk.
    real_times = _times(2, start=BASE + 6 * STEP)
    server.set_klines(real_times)

    rows = list(
        iter_klines_range(server.base_url, "BTC-USDT", "15m", BASE, BASE + 8 * STEP, limit=3)
    )

    assert sorted(r.open_time_ms for r in rows) == real_times
    assert len(server.requests) == 3  # 3 chunks of width 3 cover [0, 9); walk stops at end_ms=8*STEP


def test_iter_klines_range_returns_empty_for_a_range_with_no_data_at_all(server, monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)

    rows = list(
        iter_klines_range(server.base_url, "BTC-USDT", "15m", BASE, BASE + 4 * STEP, limit=2)
    )

    assert rows == []
    assert len(server.requests) == 2  # forward progress guaranteed, no infinite loop


def test_iter_klines_range_rejects_misaligned_range(server):
    with pytest.raises(ValueError):
        list(iter_klines_range(server.base_url, "BTC-USDT", "15m", BASE + 1, BASE + STEP))


def test_iter_klines_range_rejects_inverted_range(server):
    with pytest.raises(ValueError):
        list(iter_klines_range(server.base_url, "BTC-USDT", "15m", BASE + STEP, BASE))


def test_iter_klines_range_sleeps_between_page_requests_but_not_before_the_first(server):
    sleep_calls: list[float] = []

    import data.bingx_klines as bingx_klines_module

    original_sleep = bingx_klines_module.time.sleep
    bingx_klines_module.time.sleep = lambda s: sleep_calls.append(s)
    try:
        times = _times(6)
        server.set_klines(times)
        list(iter_klines_range(server.base_url, "BTC-USDT", "15m", times[0], times[-1] + STEP, limit=2))
    finally:
        bingx_klines_module.time.sleep = original_sleep

    assert len(server.requests) == 3  # chunks of 2, 2, 2
    assert sleep_calls == [INTER_REQUEST_DELAY_S, INTER_REQUEST_DELAY_S]  # between pages, not before the first
