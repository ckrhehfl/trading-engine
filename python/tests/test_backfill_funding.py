from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data.backfill_funding import main, sync_funding_range
from data.bingx_funding import FUNDING_INTERVAL_MS, FundingRow
from data.store import connect, upsert_funding_rates
from tests.fake_bingx_funding_server import FakeBingXFundingServer

STEP = FUNDING_INTERVAL_MS
BASE = (1_700_000_000_000 // STEP) * STEP


def _row(offset: int) -> FundingRow:
    return FundingRow(
        funding_time_ms=BASE + offset * STEP,
        funding_rate=Decimal("0.0001"),
        mark_price=Decimal("50000"),
    )


@pytest.fixture
def server():
    srv = FakeBingXFundingServer()
    yield srv
    srv.close()


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("data.bingx_funding.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# sync_funding_range
# ---------------------------------------------------------------------------


def test_sync_funding_range_fetches_and_stores_all_rows_in_range(server, conn):
    times = [BASE + i * STEP for i in range(5)]
    server.set_funding_rates(times)

    inserted = sync_funding_range("BTC-USDT", times[0], times[-1] + STEP, conn=conn, base_url=server.base_url)

    assert inserted == 5
    stored = [row[0] for row in conn.execute("SELECT funding_time_ms FROM funding_rates ORDER BY funding_time_ms")]
    assert stored == times


def test_sync_funding_range_returns_zero_and_makes_no_requests_when_nothing_is_missing(server, conn):
    times = [BASE + i * STEP for i in range(3)]
    upsert_funding_rates(conn, "BTC-USDT", [_row(i) for i in range(3)])

    inserted = sync_funding_range("BTC-USDT", times[0], times[-1] + STEP, conn=conn, base_url=server.base_url)

    assert inserted == 0
    assert server.requests == []


def test_sync_funding_range_is_idempotent_on_rerun_after_a_partial_fetch(server, conn):
    upsert_funding_rates(conn, "BTC-USDT", [_row(i) for i in range(3)])
    all_times = [BASE + i * STEP for i in range(7)]
    server.set_funding_rates(all_times)

    first_inserted = sync_funding_range("BTC-USDT", BASE, BASE + 7 * STEP, conn=conn, base_url=server.base_url)
    assert first_inserted == 4  # only bars 3-6

    for req in server.requests:
        start = int(req["params"]["startTime"])
        assert start >= BASE + 3 * STEP

    requests_after_first_run = len(server.requests)

    second_inserted = sync_funding_range("BTC-USDT", BASE, BASE + 7 * STEP, conn=conn, base_url=server.base_url)

    assert second_inserted == 0
    assert len(server.requests) == requests_after_first_run
    stored = [row[0] for row in conn.execute("SELECT funding_time_ms FROM funding_rates ORDER BY funding_time_ms")]
    assert stored == all_times


def test_sync_funding_range_persists_each_batch_before_fetching_the_next(server, conn, monkeypatch):
    monkeypatch.setattr("data.backfill_funding._UPSERT_BATCH_SIZE", 2)
    committed_counts_after_each_batch: list[int] = []
    real_upsert = upsert_funding_rates

    def spy_upsert(conn_, symbol, rows):
        result = real_upsert(conn_, symbol, rows)
        committed_counts_after_each_batch.append(conn_.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0])
        return result

    monkeypatch.setattr("data.backfill_funding.upsert_funding_rates", spy_upsert)

    times = [BASE + i * STEP for i in range(5)]
    server.set_funding_rates(times)

    inserted = sync_funding_range("BTC-USDT", times[0], times[-1] + STEP, conn=conn, base_url=server.base_url)

    assert inserted == 5
    assert committed_counts_after_each_batch == [2, 4, 5]


def test_sync_funding_range_accepts_a_range_not_aligned_to_funding_interval_ms(server, conn):
    # Deliberately NOT a rejection test -- see bingx_funding.py's module
    # docstring and .planning/sr-m-funding-rate-pipeline.md: real
    # historical funding timestamps are not always aligned to
    # FUNDING_INTERVAL_MS, so this module must not reject an off-grid
    # start_ms/end_ms either.
    server.set_funding_rate(BASE)

    inserted = sync_funding_range("BTC-USDT", BASE - 1, BASE + STEP + 1, conn=conn, base_url=server.base_url)

    assert inserted == 1


def test_sync_funding_range_rejects_inverted_range(server, conn):
    with pytest.raises(ValueError):
        sync_funding_range("BTC-USDT", BASE + STEP, BASE, conn=conn, base_url=server.base_url)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_exits_with_error_when_base_url_is_missing(monkeypatch):
    monkeypatch.delenv("BINGX_BASE_URL", raising=False)

    with pytest.raises(SystemExit):
        main(["--start", "2020-01-01T00:00:00+00:00"])


def test_main_reads_base_url_from_environment_variable(monkeypatch, server, tmp_path):
    monkeypatch.setenv("BINGX_BASE_URL", server.base_url)
    times = [BASE + i * STEP for i in range(2)]
    server.set_funding_rates(times)
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
    stored = [row[0] for row in conn.execute("SELECT funding_time_ms FROM funding_rates ORDER BY funding_time_ms")]
    conn.close()
    assert stored == times
