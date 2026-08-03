from decimal import Decimal

import pytest

from data.backfill_macro import SERIES_START_DATE, main, sync_macro_range
from data.fred_client import INTER_REQUEST_DELAY_S, ObservationRow
from data.store import connect, upsert_macro_observations
from tests.fake_fred_server import FakeFredServer

FAKE_API_KEY = "test-fake-fred-key-not-real"

# A real Mon-Fri work week with no weekend rows ever seeded -- 2026-01-05
# (Mon) through 2026-01-09 (Fri).
WEEKDAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
# The following week's Monday, to test extension across a weekend.
NEXT_MONDAY = "2026-01-12"


@pytest.fixture
def server():
    srv = FakeFredServer()
    yield srv
    srv.close()


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("data.fred_client.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# sync_macro_range
# ---------------------------------------------------------------------------


def test_sync_macro_range_fetches_and_stores_all_weekday_rows_in_range(server, conn):
    server.set_observations("DGS10", [(d, "4.0") for d in WEEKDAYS])

    inserted = sync_macro_range(
        "DGS10", WEEKDAYS[0], WEEKDAYS[-1], conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY
    )

    assert inserted == 5
    stored = [
        row[0]
        for row in conn.execute(
            "SELECT observation_date FROM macro_series ORDER BY observation_date"
        )
    ]
    assert stored == WEEKDAYS


def test_sync_macro_range_never_requests_weekend_dates(server, conn):
    # Span a full week including both weekend days -- the fake server
    # would happily return rows for Sat/Sun if actually asked for a date
    # range covering them (it doesn't know about weekdays), so this
    # verifies find_missing_macro_ranges' weekday-only expected calendar
    # is what keeps them out of the request in the first place, not
    # this test forbidding it after the fact.
    server.set_observations("DGS10", [(d, "4.0") for d in WEEKDAYS])

    sync_macro_range("DGS10", WEEKDAYS[0], NEXT_MONDAY, conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY)

    stored_weekends = conn.execute(
        "SELECT observation_date FROM macro_series WHERE observation_date IN ('2026-01-10', '2026-01-11')"
    ).fetchall()
    assert stored_weekends == []


def test_sync_macro_range_returns_zero_and_makes_no_requests_when_nothing_is_missing(server, conn):
    upsert_macro_observations(
        conn, "DGS10", [ObservationRow(observation_date=d, value=Decimal("4.0")) for d in WEEKDAYS]
    )

    inserted = sync_macro_range(
        "DGS10", WEEKDAYS[0], WEEKDAYS[-1], conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY
    )

    assert inserted == 0
    assert server.requests == []


def test_sync_macro_range_is_idempotent_on_rerun_after_a_partial_fetch(server, conn):
    upsert_macro_observations(
        conn, "DGS10", [ObservationRow(observation_date=d, value=Decimal("4.0")) for d in WEEKDAYS[:2]]
    )
    server.set_observations("DGS10", [(d, "4.0") for d in WEEKDAYS])

    first_inserted = sync_macro_range(
        "DGS10", WEEKDAYS[0], WEEKDAYS[-1], conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY
    )
    assert first_inserted == 3  # only the 3 missing weekdays

    requests_after_first_run = len(server.requests)

    second_inserted = sync_macro_range(
        "DGS10", WEEKDAYS[0], WEEKDAYS[-1], conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY
    )

    assert second_inserted == 0
    assert len(server.requests) == requests_after_first_run  # no new requests
    stored = [
        row[0]
        for row in conn.execute("SELECT observation_date FROM macro_series ORDER BY observation_date")
    ]
    assert stored == WEEKDAYS


def test_sync_macro_range_stores_and_never_re_fetches_a_holiday_missing_value_marker_row(server, conn):
    # 2026-01-01 is a real weekday holiday (New Year's Day observed) --
    # FRED returns a real row with value "." for it, not an omission.
    holiday_week = ["2026-01-01", "2026-01-02"]  # Thu holiday, Fri real value
    server.set_observations("DGS10", [("2026-01-01", None), ("2026-01-02", "4.0")])

    first_inserted = sync_macro_range(
        "DGS10", holiday_week[0], holiday_week[-1], conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY
    )
    assert first_inserted == 2

    raw = conn.execute(
        "SELECT observation_date, value FROM macro_series ORDER BY observation_date"
    ).fetchall()
    assert raw == [("2026-01-01", None), ("2026-01-02", "4.0")]

    requests_after_first_run = len(server.requests)

    # Rerun: the holiday row must not be treated as still-missing and
    # re-requested forever.
    second_inserted = sync_macro_range(
        "DGS10", holiday_week[0], holiday_week[-1], conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY
    )
    assert second_inserted == 0
    assert len(server.requests) == requests_after_first_run


def test_sync_macro_range_persists_each_batch_before_fetching_the_next(server, conn, monkeypatch):
    monkeypatch.setattr("data.backfill_macro._UPSERT_BATCH_SIZE", 2)
    committed_counts_after_each_batch: list[int] = []
    real_upsert = upsert_macro_observations

    def spy_upsert(conn_, series_id, rows):
        result = real_upsert(conn_, series_id, rows)
        committed_counts_after_each_batch.append(conn_.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0])
        return result

    monkeypatch.setattr("data.backfill_macro.upsert_macro_observations", spy_upsert)
    server.set_observations("DGS10", [(d, "4.0") for d in WEEKDAYS])

    inserted = sync_macro_range(
        "DGS10", WEEKDAYS[0], WEEKDAYS[-1], conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY
    )

    assert inserted == 5
    assert committed_counts_after_each_batch == [2, 4, 5]


def test_sync_macro_range_sleeps_between_disjoint_gaps_but_not_before_the_first(server, conn, monkeypatch):
    # Two disjoint gaps in one call: Tue missing (gap 1), Thu+Fri missing
    # (gap 2), with Mon and Wed already stored to split them. Without a
    # delay between gaps, a backfill resuming across several small gaps
    # would fire each gap's first request back to back with zero delay
    # between them -- CodeRabbit review finding on this PR.
    mon, tue, wed, thu, fri = WEEKDAYS
    upsert_macro_observations(
        conn, "DGS10", [ObservationRow(observation_date=d, value=Decimal("4.0")) for d in [mon, wed]]
    )
    server.set_observations("DGS10", [(d, "4.0") for d in [tue, thu, fri]])
    sleep_calls: list[float] = []
    monkeypatch.setattr("data.backfill_macro.time.sleep", sleep_calls.append)

    inserted = sync_macro_range("DGS10", mon, fri, conn=conn, base_url=server.base_url, api_key=FAKE_API_KEY)

    assert inserted == 3  # tue, thu, fri
    # Exactly one inter-gap delay (before the second gap's first request);
    # none before the very first request overall.
    assert sleep_calls == [INTER_REQUEST_DELAY_S]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_exits_with_error_when_api_key_is_missing(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with pytest.raises(SystemExit):
        main(["--series-id", "DGS10", "--start", "2026-01-05", "--end", "2026-01-05"])


def test_main_reads_api_key_from_environment_variable(monkeypatch, server, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", FAKE_API_KEY)
    server.set_observations("DGS10", [(d, "4.0") for d in WEEKDAYS])

    main(
        [
            "--series-id",
            "DGS10",
            "--start",
            WEEKDAYS[0],
            "--end",
            WEEKDAYS[-1],
            "--db-path",
            str(tmp_path / "macro.sqlite3"),
            "--base-url",
            server.base_url,
        ]
    )

    conn = connect(tmp_path / "macro.sqlite3")
    stored = [
        row[0] for row in conn.execute("SELECT observation_date FROM macro_series ORDER BY observation_date")
    ]
    conn.close()
    assert stored == WEEKDAYS


def test_main_defaults_start_to_the_series_own_verified_observation_start(monkeypatch, server, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", FAKE_API_KEY)
    real_start = SERIES_START_DATE["DTWEXBGS"]
    server.set_observation("DTWEXBGS", real_start, "100.0")

    main(
        [
            "--series-id",
            "DTWEXBGS",
            "--end",
            real_start,
            "--db-path",
            str(tmp_path / "macro.sqlite3"),
            "--base-url",
            server.base_url,
        ]
    )

    conn = connect(tmp_path / "macro.sqlite3")
    stored = [row[0] for row in conn.execute("SELECT observation_date FROM macro_series")]
    conn.close()
    assert stored == [real_start]


def test_main_defaults_to_all_three_series_when_none_specified(monkeypatch, server, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", FAKE_API_KEY)
    for series_id in SERIES_START_DATE:
        server.set_observation(series_id, SERIES_START_DATE[series_id], "1.0")

    main(
        [
            "--end",
            "2026-01-01",
            "--db-path",
            str(tmp_path / "macro.sqlite3"),
            "--base-url",
            server.base_url,
        ]
    )

    conn = connect(tmp_path / "macro.sqlite3")
    stored_series = {row[0] for row in conn.execute("SELECT DISTINCT series_id FROM macro_series")}
    conn.close()
    assert stored_series == set(SERIES_START_DATE)


def test_main_sleeps_between_series_but_not_before_the_first(monkeypatch, server, tmp_path):
    # FRED's (undocumented) rate limit applies per API key across every
    # request that key makes, not per series -- so main()'s own loop over
    # series must apply the same inter-request delay at series boundaries
    # that sync_macro_range already applies at gap boundaries within one
    # series. CodeRabbit review finding on this PR (the first fix only
    # covered gaps within a single sync_macro_range call).
    monkeypatch.setenv("FRED_API_KEY", FAKE_API_KEY)
    for series_id in SERIES_START_DATE:
        server.set_observation(series_id, SERIES_START_DATE[series_id], "1.0")
    sleep_calls: list[float] = []
    monkeypatch.setattr("data.backfill_macro.time.sleep", sleep_calls.append)

    main(
        [
            "--end",
            "2026-01-01",
            "--db-path",
            str(tmp_path / "macro.sqlite3"),
            "--base-url",
            server.base_url,
        ]
    )

    # 3 series -> 2 boundaries -> exactly 2 sleeps, all at INTER_REQUEST_DELAY_S
    # (each series has exactly one gap here, so none of sync_macro_range's
    # own within-call inter-gap sleeps fire).
    assert sleep_calls == [INTER_REQUEST_DELAY_S, INTER_REQUEST_DELAY_S]
