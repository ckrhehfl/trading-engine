from decimal import Decimal

import pytest

from data.bingx_funding import (
    FUNDING_INTERVAL_MS,
    MAX_FUNDING_ROWS_PER_REQUEST,
    BingXFundingError,
    fetch_funding_page,
    iter_funding_range,
)
from tests.fake_bingx_funding_server import FUNDING_PATH, FakeBingXFundingServer

STEP = FUNDING_INTERVAL_MS  # 8h, 28_800_000ms
BASE = (1_700_000_000_000 // STEP) * STEP  # grid-aligned test epoch


@pytest.fixture
def server():
    srv = FakeBingXFundingServer()
    yield srv
    srv.close()


def _times(n: int, start: int = BASE) -> list[int]:
    return [start + i * STEP for i in range(n)]


# ---------------------------------------------------------------------------
# fetch_funding_page: parsing
# ---------------------------------------------------------------------------


def test_fetch_funding_page_parses_rows_with_exact_decimal_values(server):
    server.set_funding_rate(BASE, "0.00006500", "65198.5")

    rows = fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)

    assert len(rows) == 1
    row = rows[0]
    assert row.funding_time_ms == BASE
    assert row.funding_rate == Decimal("0.00006500")
    assert row.mark_price == Decimal("65198.5")


def test_fetch_funding_page_uses_parse_float_decimal_to_avoid_float_roundtrip_precision_loss(server):
    # Same defensive reasoning as bingx_klines.py: BingX is verified to
    # send fundingRate/markPrice as quoted strings, but parse_float=Decimal
    # guards the case where it ever sends a bare JSON number instead.
    precise_rate = "0.000123456789012345678"
    assert Decimal(str(float(precise_rate))) != Decimal(precise_rate), (
        "test value doesn't actually exercise float precision loss -- pick a more precise literal"
    )
    body = (
        '{"code":0,"msg":"","data":[{"symbol":"BTC-USDT","fundingRate":'
        + precise_rate
        + ',"fundingTime":'
        + str(BASE)
        + ',"markPrice":1}]}'
    )
    server.force_response(200, body)

    rows = fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)

    assert rows[0].funding_rate == Decimal(precise_rate)


def test_fetch_funding_page_sends_expected_path_and_query_params(server):
    server.set_funding_rate(BASE)

    fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP, limit=500)

    assert len(server.requests) == 1
    req = server.requests[0]
    assert req["path"] == FUNDING_PATH
    assert req["params"]["symbol"] == "BTC-USDT"
    assert req["params"]["startTime"] == str(BASE)
    assert req["params"]["endTime"] == str(BASE + STEP)
    assert req["params"]["limit"] == "500"


# ---------------------------------------------------------------------------
# fetch_funding_page: pagination-boundary / silent-capping behavior
# ---------------------------------------------------------------------------


def test_fetch_funding_page_returns_every_row_when_range_has_exactly_limit_rows(server):
    times = _times(5)
    server.set_funding_rates(times)

    rows = fetch_funding_page(server.base_url, "BTC-USDT", times[0], times[-1] + STEP, limit=5)

    assert sorted(r.funding_time_ms for r in rows) == times


def test_fetch_funding_page_silently_caps_to_newest_rows_when_range_spans_more_than_limit(server):
    # Verified empirically against the live production endpoint
    # 2026-07-27: same silent-capping-to-newest behavior as klines.
    times = _times(8)
    server.set_funding_rates(times)

    rows = fetch_funding_page(server.base_url, "BTC-USDT", times[0], times[-1] + STEP, limit=5)

    assert len(rows) == 5
    assert sorted(r.funding_time_ms for r in rows) == times[-5:]  # newest 5, not oldest 5


# ---------------------------------------------------------------------------
# fetch_funding_page: "no data" is `null`, not `[]` -- and must not crash
# ---------------------------------------------------------------------------


def test_fetch_funding_page_returns_empty_list_for_a_null_data_response(server):
    # Real BingX returns `data: null` (not `[]`) for a range with no
    # matching rows -- confirmed live 2026-07-27 for both a genuinely
    # out-of-retention range and an in-retention range with zero funding
    # events inside it. The fake server's default empty-result behavior
    # already replicates this; no forced response needed.
    rows = fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)

    assert rows == []


# ---------------------------------------------------------------------------
# fetch_funding_page: retry-on-null (the flaky-history finding)
# ---------------------------------------------------------------------------


def test_fetch_funding_page_retries_a_null_response_and_succeeds_once_real_data_appears(server, monkeypatch):
    # The single most important empirical finding for this endpoint (see
    # .planning/sr-m-funding-rate-pipeline.md): for older/historical
    # ranges, BingX returns `data: null` *flakily* even when the range
    # genuinely has data -- the exact same range can return null on one
    # call and real rows on the next. A single null must not be treated
    # as authoritative "no data" the way an empty list is.
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    server.set_funding_rate(BASE)
    server.force_response(200, '{"code":0,"msg":"","data":null}', times=2)

    rows = fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)

    assert len(rows) == 1
    assert len(server.requests) == 3  # 2 flaky nulls + 1 real


def test_fetch_funding_page_gives_up_and_returns_empty_after_exhausting_null_retries(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    server.set_funding_rate(BASE)  # real data exists...
    server.force_response(200, '{"code":0,"msg":"","data":null}', times=50)  # ...but every retry sees null

    rows = fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)

    assert rows == []  # exhausted retries -- treated as genuinely empty, not an error


# ---------------------------------------------------------------------------
# fetch_funding_page: input validation (fail loud, never silently round)
# ---------------------------------------------------------------------------


def test_fetch_funding_page_accepts_a_range_not_aligned_to_funding_interval_ms(server):
    # Deliberately NOT a rejection test, unlike bingx_klines' equivalent:
    # real historical funding timestamps are not always aligned to
    # FUNDING_INTERVAL_MS (see bingx_funding.py's module docstring and
    # .planning/sr-m-funding-rate-pipeline.md for the real backfill
    # finding that drove this), so start_ms/end_ms alignment is
    # deliberately not enforced here.
    server.set_funding_rate(BASE)

    rows = fetch_funding_page(server.base_url, "BTC-USDT", BASE - 1, BASE + STEP + 1)

    assert [r.funding_time_ms for r in rows] == [BASE]


def test_fetch_funding_page_rejects_start_not_before_end(server):
    with pytest.raises(ValueError):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE + STEP, BASE)


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_fetch_funding_page_rejects_limit_out_of_range(server, limit):
    # Real BingX errors server-side on limit > 1000 (code 109400) rather
    # than silently clamping -- validated client-side here instead, same
    # "fail loud" contract as bingx_klines.py, so a caller never actually
    # sends the invalid request.
    with pytest.raises(ValueError, match="limit"):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP, limit=limit)
    assert server.requests == []


# ---------------------------------------------------------------------------
# fetch_funding_page: malformed / error responses
# ---------------------------------------------------------------------------


def test_fetch_funding_page_raises_on_non_zero_code(server):
    server.force_response(200, '{"code":100410,"msg":"symbol not found","data":[]}')

    with pytest.raises(BingXFundingError, match="100410"):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)


def test_fetch_funding_page_raises_on_malformed_json(server):
    server.force_response(200, "{not valid json")

    with pytest.raises(BingXFundingError):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)


def test_fetch_funding_page_raises_when_data_is_neither_a_list_nor_null(server):
    server.force_response(200, '{"code":0,"msg":"","data":{}}')

    with pytest.raises(BingXFundingError):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)


def test_fetch_funding_page_raises_when_row_is_missing_a_required_field(server):
    server.force_response(200, f'{{"code":0,"msg":"","data":[{{"fundingTime":{BASE}}}]}}')

    with pytest.raises(BingXFundingError):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)


# ---------------------------------------------------------------------------
# fetch_funding_page: retry / backoff on transient HTTP errors
# ---------------------------------------------------------------------------


def test_fetch_funding_page_retries_on_429_then_succeeds(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    server.set_funding_rate(BASE)
    server.force_response(429, "rate limited", times=2)

    rows = fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)

    assert len(rows) == 1
    assert len(server.requests) == 3  # 2 failed + 1 succeeded


def test_fetch_funding_page_raises_after_exhausting_retries_on_persistent_429(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    server.force_response(429, "rate limited", times=10)

    with pytest.raises(BingXFundingError):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)


def test_fetch_funding_page_raises_immediately_on_non_retryable_status_without_retrying(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    server.force_response(404, "not found", times=10)

    with pytest.raises(BingXFundingError):
        fetch_funding_page(server.base_url, "BTC-USDT", BASE, BASE + STEP)

    assert len(server.requests) == 1  # no retry wasted on a non-retryable status


# ---------------------------------------------------------------------------
# iter_funding_range: pagination across chunks
# ---------------------------------------------------------------------------


def test_iter_funding_range_walks_multiple_chunks_for_a_range_wider_than_limit(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    times = _times(12)
    server.set_funding_rates(times)

    rows = list(iter_funding_range(server.base_url, "BTC-USDT", times[0], times[-1] + STEP, limit=5))

    assert sorted(r.funding_time_ms for r in rows) == times
    assert len(rows) == len(set(r.funding_time_ms for r in rows))  # no duplicates
    # 3 real-data chunks (5, 5, 2 rows) + _MAX_NULL_RETRIES (5) for one
    # final, genuinely-empty trailing chunk. The trailing chunk exists
    # because the cursor after the 3rd chunk is `max_time + 1` (not
    # `max_time + FUNDING_INTERVAL_MS`, see iter_funding_range's
    # docstring for why) -- `max_time + 1` doesn't overshoot all the way
    # to `end_ms` the way a full-interval jump often coincidentally does,
    # so one more (empty) chunk gets requested before the range is
    # exhausted. A deliberate trade-off: a bounded, fixed extra request
    # cost at the tail of a range, in exchange for never silently
    # skipping a real off-grid row mid-range (the actual correctness bug
    # this cursor change fixes -- see this task's CodeRabbit review).
    assert len(server.requests) == 3 + 5


def test_iter_funding_range_cursor_derives_from_actual_max_row_time_not_naive_limit_arithmetic(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    times = _times(3)
    server.set_funding_rates(times)

    rows = list(iter_funding_range(server.base_url, "BTC-USDT", BASE, BASE + 5 * STEP, limit=5))

    assert sorted(r.funding_time_ms for r in rows) == times
    # 1 request for the first (real-data) chunk + _MAX_NULL_RETRIES (5) for
    # the second, genuinely-empty chunk -- every null response is retried
    # before being trusted as "no data" (see bingx_funding.py's module
    # docstring), so a genuinely empty chunk costs _MAX_NULL_RETRIES
    # requests, not 1.
    assert len(server.requests) == 6
    second_request_start = int(server.requests[1]["params"]["startTime"])
    assert second_request_start == BASE + 2 * STEP + 1  # last real row's time + 1ms, not + step
    assert second_request_start != BASE + 5 * STEP  # naive start_ms + limit*step


def test_iter_funding_range_treats_empty_leading_region_as_normal_not_error(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    real_times = _times(2, start=BASE + 6 * STEP)
    server.set_funding_rates(real_times)

    rows = list(iter_funding_range(server.base_url, "BTC-USDT", BASE, BASE + 8 * STEP, limit=3))

    assert sorted(r.funding_time_ms for r in rows) == real_times
    # 2 genuinely-empty leading chunks x _MAX_NULL_RETRIES (5) each + 1
    # request for the real-data chunk (no retry needed) + _MAX_NULL_
    # RETRIES (5) more for one final, genuinely-empty trailing chunk --
    # same `max_time + 1`-doesn't-overshoot-to-end_ms reasoning as
    # test_iter_funding_range_walks_multiple_chunks_for_a_range_wider_
    # than_limit above. 5 + 5 + 1 + 5 = 16.
    assert len(server.requests) == 5 + 5 + 1 + 5


def test_iter_funding_range_returns_empty_for_a_range_with_no_data_at_all(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)

    rows = list(iter_funding_range(server.base_url, "BTC-USDT", BASE, BASE + 4 * STEP, limit=2))

    assert rows == []
    # forward progress guaranteed, no infinite loop -- 2 chunks x
    # _MAX_NULL_RETRIES (5) null-retries each, since both are genuinely
    # empty.
    assert len(server.requests) == 10


def test_iter_funding_range_accepts_a_range_not_aligned_to_funding_interval_ms(server, monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)
    server.set_funding_rate(BASE)

    rows = list(iter_funding_range(server.base_url, "BTC-USDT", BASE - 1, BASE + STEP + 1))

    assert [r.funding_time_ms for r in rows] == [BASE]


def test_iter_funding_range_rejects_inverted_range(server):
    with pytest.raises(ValueError):
        list(iter_funding_range(server.base_url, "BTC-USDT", BASE + STEP, BASE))


def test_max_funding_rows_per_request_matches_bingx_documented_cap():
    assert MAX_FUNDING_ROWS_PER_REQUEST == 1000
