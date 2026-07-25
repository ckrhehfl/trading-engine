from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data.backfill import _to_ms, main, sync_range
from data.store import connect, upsert_klines
from data.bingx_klines import KlineRow
from tests.fake_bingx_server import FakeBingXKlinesServer

STEP = 900_000
BASE = (1_700_000_000_000 // STEP) * STEP


def _row(offset: int) -> KlineRow:
    price = Decimal("100")
    return KlineRow(
        open_time_ms=BASE + offset * STEP,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
    )


@pytest.fixture
def server():
    srv = FakeBingXKlinesServer()
    yield srv
    srv.close()


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# sync_range
# ---------------------------------------------------------------------------


def test_sync_range_fetches_and_stores_all_klines_in_range(server, conn):
    times = [BASE + i * STEP for i in range(5)]
    server.set_klines(times)

    inserted = sync_range(
        "BTC-USDT", "15m", times[0], times[-1] + STEP, conn=conn, base_url=server.base_url
    )

    assert inserted == 5
    stored = [row[0] for row in conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms")]
    assert stored == times


def test_sync_range_returns_zero_and_makes_no_requests_when_nothing_is_missing(server, conn):
    times = [BASE + i * STEP for i in range(3)]
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(3)])

    inserted = sync_range(
        "BTC-USDT", "15m", times[0], times[-1] + STEP, conn=conn, base_url=server.base_url
    )

    assert inserted == 0
    assert server.requests == []  # already fully cached -- no network call at all


def test_sync_range_is_idempotent_on_rerun_after_a_partial_fetch(server, conn):
    # Simulate an interrupted prior run: bars 0-2 already stored, bars
    # 3-6 still missing.
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(3)])
    all_times = [BASE + i * STEP for i in range(7)]
    server.set_klines(all_times)

    first_inserted = sync_range(
        "BTC-USDT", "15m", BASE, BASE + 7 * STEP, conn=conn, base_url=server.base_url
    )
    assert first_inserted == 4  # only bars 3-6

    # Confirm the already-present bars were never re-requested: the fake
    # server should only have seen requests covering [3*STEP, 7*STEP).
    for req in server.requests:
        start = int(req["params"]["startTime"])
        assert start >= BASE + 3 * STEP

    requests_after_first_run = len(server.requests)

    # Rerun with the exact same range -- everything is now cached.
    second_inserted = sync_range(
        "BTC-USDT", "15m", BASE, BASE + 7 * STEP, conn=conn, base_url=server.base_url
    )

    assert second_inserted == 0
    assert len(server.requests) == requests_after_first_run  # no new requests on the no-op rerun
    stored = [row[0] for row in conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms")]
    assert stored == all_times


def test_sync_range_persists_each_batch_before_fetching_the_next(server, conn, monkeypatch):
    # A store starting empty means find_missing_ranges returns a single
    # gap covering the *entire* requested range -- e.g. the real
    # multi-year sentinel-to-now backfill this pipeline is meant to run
    # (see .planning/sr-a-data-pipeline.md). If sync_range buffered that
    # whole gap's rows in memory and only wrote them at the very end, an
    # interruption partway through would lose all progress, not just the
    # unfetched tail. This proves rows are actually committed to `conn`
    # incrementally, batch by batch, not just accumulated in Python
    # memory until sync_range returns.
    monkeypatch.setattr("data.backfill._UPSERT_BATCH_SIZE", 2)
    committed_counts_after_each_batch: list[int] = []
    real_upsert = upsert_klines

    def spy_upsert(conn_, symbol, interval, rows):
        result = real_upsert(conn_, symbol, interval, rows)
        committed_counts_after_each_batch.append(conn_.execute("SELECT COUNT(*) FROM klines").fetchone()[0])
        return result

    monkeypatch.setattr("data.backfill.upsert_klines", spy_upsert)

    times = [BASE + i * STEP for i in range(5)]
    server.set_klines(times)

    inserted = sync_range("BTC-USDT", "15m", times[0], times[-1] + STEP, conn=conn, base_url=server.base_url)

    assert inserted == 5
    # Batches of 2, 2, then a final partial batch of 1 -- and each
    # batch's rows are visible in `conn` (queried fresh, not from
    # in-memory state) immediately after that batch's upsert call, not
    # only after the whole gap finishes.
    assert committed_counts_after_each_batch == [2, 4, 5]


def test_sync_range_rejects_misaligned_range(server, conn):
    with pytest.raises(ValueError):
        sync_range("BTC-USDT", "15m", BASE + 1, BASE + STEP, conn=conn, base_url=server.base_url)


def test_sync_range_rejects_inverted_range(server, conn):
    with pytest.raises(ValueError):
        sync_range("BTC-USDT", "15m", BASE + STEP, BASE, conn=conn, base_url=server.base_url)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def test_to_ms_treats_naive_input_as_utc():
    assert _to_ms("2020-01-01T00:00:00") == _to_ms("2020-01-01T00:00:00+00:00")


def test_to_ms_matches_known_epoch_value():
    assert _to_ms("2020-01-01T00:00:00+00:00") == 1577836800000


def test_to_ms_normalizes_a_non_utc_offset():
    # 2020-01-01T09:00:00+09:00 is the same instant as
    # 2020-01-01T00:00:00+00:00 -- _to_ms must normalize to UTC before
    # converting to epoch ms, not just strip the offset.
    assert _to_ms("2020-01-01T09:00:00+09:00") == _to_ms("2020-01-01T00:00:00+00:00")


def test_main_exits_with_error_when_base_url_is_missing(monkeypatch):
    monkeypatch.delenv("BINGX_BASE_URL", raising=False)

    with pytest.raises(SystemExit):
        main(["--start", "2020-01-01T00:00:00+00:00"])


def test_main_reads_base_url_from_environment_variable(monkeypatch, server, tmp_path):
    monkeypatch.setenv("BINGX_BASE_URL", server.base_url)
    times = [BASE + i * STEP for i in range(2)]
    server.set_klines(times)
    start_iso = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp((times[-1] + STEP) / 1000, tz=timezone.utc).isoformat()

    main(
        [
            "--start",
            start_iso,
            "--end",
            end_iso,
            "--db-path",
            str(tmp_path / "klines.sqlite3"),
        ]
    )

    conn = connect(tmp_path / "klines.sqlite3")
    stored = [row[0] for row in conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms")]
    conn.close()
    assert stored == times
