from decimal import Decimal

import pytest

from data.fred_client import (
    MAX_OBSERVATIONS_PER_REQUEST,
    FredClientError,
    fetch_observations_page,
    iter_observations,
)
from tests.fake_fred_server import OBSERVATIONS_PATH, FakeFredServer

FAKE_API_KEY = "test-fake-fred-key-not-real"


@pytest.fixture
def server():
    srv = FakeFredServer()
    yield srv
    srv.close()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("data.fred_client.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# fetch_observations_page: parsing
# ---------------------------------------------------------------------------


def test_fetch_observations_page_parses_rows_with_exact_decimal_values(server):
    server.set_observation("DGS10", "2026-01-02", "4.05")

    rows, count = fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02")

    assert count == 1
    assert len(rows) == 1
    assert rows[0].observation_date == "2026-01-02"
    assert rows[0].value == Decimal("4.05")


def test_fetch_observations_page_uses_parse_float_decimal_to_avoid_float_roundtrip_precision_loss(server):
    # FRED is verified (see fred_client.py's module docstring) to always
    # send `value` as a quoted string -- this guards the defensive case
    # where a bare JSON number appears instead. A value with enough
    # significant digits to exceed float precision proves
    # json.loads(..., parse_float=Decimal) is actually wired up.
    precise_value = "4.12345678901234567891"
    assert Decimal(str(float(precise_value))) != Decimal(precise_value), (
        "test value doesn't actually exercise float precision loss -- pick a more precise literal"
    )
    body = (
        '{"count":1,"offset":0,"limit":100000,"observations":'
        '[{"date":"2026-01-02","value":' + precise_value + "}]}"
    )
    server.force_response(200, body)

    rows, _count = fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02")

    assert rows[0].value == Decimal(precise_value)


def test_fetch_observations_page_rejects_a_non_finite_decimal_value(server):
    # Decimal("NaN")/Decimal("Infinity") parse without raising -- FRED is
    # not known to ever send these (only a real number or the "."
    # marker), but a non-finite value stored as text and read back later
    # would compare unequal to itself and be indistinguishable from a
    # genuine value at a glance. CodeRabbit review finding on this PR.
    body = '{"count":1,"offset":0,"limit":100000,"observations":[{"date":"2026-01-02","value":"NaN"}]}'
    server.force_response(200, body)

    with pytest.raises(FredClientError):
        fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02")


def test_fetch_observations_page_parses_the_missing_value_marker_to_none(server):
    # Real, live-verified finding: a US market holiday that falls on a
    # weekday (e.g. 2026-07-03, observed for July 4th) still gets a real
    # row from FRED, with value "." -- not omitted like a weekend.
    server.set_observation("DGS10", "2026-07-03", value=None)

    rows, _count = fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-07-03", "2026-07-03")

    assert len(rows) == 1
    assert rows[0].observation_date == "2026-07-03"
    assert rows[0].value is None


def test_fetch_observations_page_sends_expected_path_and_query_params(server):
    server.set_observation("SP500", "2026-01-02", "6900.0")

    fetch_observations_page(server.base_url, "SP500", FAKE_API_KEY, "2026-01-02", "2026-01-05", limit=500, offset=10)

    assert len(server.requests) == 1
    req = server.requests[0]
    assert req["path"] == OBSERVATIONS_PATH
    assert req["params"]["series_id"] == "SP500"
    assert req["params"]["api_key"] == FAKE_API_KEY
    assert req["params"]["observation_start"] == "2026-01-02"
    assert req["params"]["observation_end"] == "2026-01-05"
    assert req["params"]["limit"] == "500"
    assert req["params"]["offset"] == "10"


def test_fetch_observations_page_returns_rows_oldest_first_matching_fred_ordering(server):
    server.set_observations(
        "DGS10",
        [("2026-01-02", "4.0"), ("2026-01-05", "4.2"), ("2026-01-06", "4.1")],
    )

    rows, _count = fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-06")

    assert [r.observation_date for r in rows] == ["2026-01-02", "2026-01-05", "2026-01-06"]


# ---------------------------------------------------------------------------
# fetch_observations_page: input validation (fail loud, never silently round)
# ---------------------------------------------------------------------------


def test_fetch_observations_page_rejects_inverted_range(server):
    with pytest.raises(ValueError):
        fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-05", "2026-01-02")


def test_fetch_observations_page_accepts_a_single_day_range(server):
    server.set_observation("DGS10", "2026-01-02", "4.0")

    rows, count = fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02")

    assert count == 1
    assert len(rows) == 1


def test_fetch_observations_page_rejects_limit_below_one(server):
    with pytest.raises(ValueError):
        fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02", limit=0)


def test_fetch_observations_page_rejects_limit_above_max(server):
    with pytest.raises(ValueError):
        fetch_observations_page(
            server.base_url,
            "DGS10",
            FAKE_API_KEY,
            "2026-01-02",
            "2026-01-02",
            limit=MAX_OBSERVATIONS_PER_REQUEST + 1,
        )


def test_fetch_observations_page_rejects_negative_offset(server):
    with pytest.raises(ValueError):
        fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02", offset=-1)


def test_fetch_observations_page_rejects_malformed_date_string(server):
    with pytest.raises(ValueError):
        fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "not-a-date", "2026-01-02")


# ---------------------------------------------------------------------------
# fetch_observations_page: HTTP error handling
# ---------------------------------------------------------------------------


def test_fetch_observations_page_raises_on_a_real_bad_series_id_style_400(server):
    server.force_response(400, '{"error_code":400,"error_message":"Bad Request.  Invalid series_id."}')

    with pytest.raises(FredClientError):
        fetch_observations_page(server.base_url, "NOT_REAL", FAKE_API_KEY, "2026-01-02", "2026-01-02")


def test_fetch_observations_page_raises_when_api_key_is_missing(server):
    with pytest.raises(FredClientError):
        fetch_observations_page(server.base_url, "DGS10", "", "2026-01-02", "2026-01-02")


def test_fetch_observations_page_retries_a_retryable_5xx_then_succeeds(server):
    server.force_response(503, "service unavailable")
    server.set_observation("DGS10", "2026-01-02", "4.0")

    rows, _count = fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02")

    assert len(rows) == 1
    assert len(server.requests) == 2  # one failed attempt, one real retry


def test_fetch_observations_page_does_not_retry_a_non_retryable_400(server):
    server.force_response(400, '{"error_code":400,"error_message":"Bad Request."}')

    with pytest.raises(FredClientError):
        fetch_observations_page(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02")

    assert len(server.requests) == 1  # no retry burned on a non-retryable error


def test_fetch_observations_page_error_message_never_contains_the_api_key(server):
    distinctive_key = "SUPER-SECRET-DO-NOT-LEAK-987654321"
    server.force_response(400, '{"error_code":400,"error_message":"Bad Request."}')

    with pytest.raises(FredClientError) as exc_info:
        fetch_observations_page(server.base_url, "DGS10", distinctive_key, "2026-01-02", "2026-01-02")

    assert distinctive_key not in str(exc_info.value)


# ---------------------------------------------------------------------------
# iter_observations: pagination
# ---------------------------------------------------------------------------


def test_iter_observations_returns_every_row_in_a_single_page_when_under_the_limit(server):
    server.set_observations("DGS10", [("2026-01-02", "4.0"), ("2026-01-05", "4.1")])

    rows = list(iter_observations(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-05"))

    assert [r.observation_date for r in rows] == ["2026-01-02", "2026-01-05"]
    assert len(server.requests) == 1


def test_iter_observations_paginates_across_multiple_pages_via_real_offset(server):
    # Real FRED supports genuine offset/limit paging (verified live this
    # session) -- unlike BingX, there's no silent-capping to work around,
    # just a real total `count` to walk via offset. A small `limit` here
    # forces this path even though no real candidate series needs it in
    # practice (see fred_client.py's module docstring).
    dates = [f"2026-01-{d:02d}" for d in [2, 5, 6, 7, 8]]  # 5 weekdays
    server.set_observations("DGS10", [(d, "4.0") for d in dates])

    rows = list(iter_observations(server.base_url, "DGS10", FAKE_API_KEY, dates[0], dates[-1], limit=2))

    assert [r.observation_date for r in rows] == dates
    assert len(server.requests) == 3  # ceil(5/2)
    offsets_requested = [req["params"]["offset"] for req in server.requests]
    assert offsets_requested == ["0", "2", "4"]


def test_iter_observations_returns_empty_for_a_range_with_no_data(server):
    rows = list(iter_observations(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-02", "2026-01-02"))

    assert rows == []
    assert len(server.requests) == 1  # a single empty page, not an infinite loop


def test_iter_observations_includes_missing_value_marker_rows(server):
    server.set_observations("DGS10", [("2026-01-01", None), ("2026-01-02", "4.0")])

    rows = list(iter_observations(server.base_url, "DGS10", FAKE_API_KEY, "2026-01-01", "2026-01-02"))

    assert [(r.observation_date, r.value) for r in rows] == [
        ("2026-01-01", None),
        ("2026-01-02", Decimal("4.0")),
    ]
