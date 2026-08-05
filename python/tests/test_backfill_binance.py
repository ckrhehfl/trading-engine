from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data.backfill_binance import (
    DEFAULT_BINANCE_FUTURES_BASE_URL,
    DEFAULT_BINANCE_SPOT_BASE_URL,
    MARKET_CONFIG,
    main,
    sync_range,
)
from data.bingx_klines import KlineRow
from data.store import connect, upsert_klines
from tests.fake_binance_server import FUTURES_KLINES_PATH, SPOT_KLINES_PATH, FakeBinanceKlinesServer

STEP = 86_400_000
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
def spot_server():
    srv = FakeBinanceKlinesServer(path=SPOT_KLINES_PATH)
    yield srv
    srv.close()


@pytest.fixture
def futures_server():
    srv = FakeBinanceKlinesServer(path=FUTURES_KLINES_PATH)
    yield srv
    srv.close()


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("data.binance_klines.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# sync_range: core resumable fetch, mirrors backfill.sync_range's own tests
# ---------------------------------------------------------------------------


def test_sync_range_fetches_and_stores_all_klines_in_range(spot_server, conn):
    times = [BASE + i * STEP for i in range(5)]
    spot_server.set_klines(times)

    inserted = sync_range(
        "spot", "BTCUSDT", "1d", times[0], times[-1] + STEP, conn=conn, base_url=spot_server.base_url
    )

    assert inserted == 5
    stored = [row[0] for row in conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms")]
    assert stored == times


def test_sync_range_returns_zero_and_makes_no_requests_when_nothing_is_missing(spot_server, conn):
    times = [BASE + i * STEP for i in range(3)]
    upsert_klines(conn, "BINANCE:BTCUSDT", "1d", [_row(i) for i in range(3)])

    inserted = sync_range(
        "spot", "BTCUSDT", "1d", times[0], times[-1] + STEP, conn=conn, base_url=spot_server.base_url
    )

    assert inserted == 0
    assert spot_server.requests == []


def test_sync_range_is_idempotent_on_rerun_after_a_partial_fetch(spot_server, conn):
    upsert_klines(conn, "BINANCE:BTCUSDT", "1d", [_row(i) for i in range(3)])
    all_times = [BASE + i * STEP for i in range(7)]
    spot_server.set_klines(all_times)

    first_inserted = sync_range("spot", "BTCUSDT", "1d", BASE, BASE + 7 * STEP, conn=conn, base_url=spot_server.base_url)
    assert first_inserted == 4

    requests_after_first_run = len(spot_server.requests)

    second_inserted = sync_range("spot", "BTCUSDT", "1d", BASE, BASE + 7 * STEP, conn=conn, base_url=spot_server.base_url)

    assert second_inserted == 0
    assert len(spot_server.requests) == requests_after_first_run
    stored = [row[0] for row in conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms")]
    assert stored == all_times


def test_sync_range_persists_each_batch_before_fetching_the_next(spot_server, conn, monkeypatch):
    monkeypatch.setattr("data.backfill_binance._UPSERT_BATCH_SIZE", 2)
    committed_counts_after_each_batch: list[int] = []
    real_upsert = upsert_klines

    def spy_upsert(conn_, symbol, interval, rows):
        result = real_upsert(conn_, symbol, interval, rows)
        committed_counts_after_each_batch.append(conn_.execute("SELECT COUNT(*) FROM klines").fetchone()[0])
        return result

    monkeypatch.setattr("data.backfill_binance.upsert_klines", spy_upsert)

    times = [BASE + i * STEP for i in range(5)]
    spot_server.set_klines(times)

    inserted = sync_range("spot", "BTCUSDT", "1d", times[0], times[-1] + STEP, conn=conn, base_url=spot_server.base_url)

    assert inserted == 5
    assert committed_counts_after_each_batch == [2, 4, 5]


def test_sync_range_rejects_misaligned_range(spot_server, conn):
    with pytest.raises(ValueError):
        sync_range("spot", "BTCUSDT", "1d", BASE + 1, BASE + STEP, conn=conn, base_url=spot_server.base_url)


def test_sync_range_rejects_inverted_range(spot_server, conn):
    with pytest.raises(ValueError):
        sync_range("spot", "BTCUSDT", "1d", BASE + STEP, BASE, conn=conn, base_url=spot_server.base_url)


def test_sync_range_rejects_an_unknown_market(spot_server, conn):
    with pytest.raises(ValueError, match="market"):
        sync_range("bybit", "BTCUSDT", "1d", BASE, BASE + STEP, conn=conn, base_url=spot_server.base_url)


# ---------------------------------------------------------------------------
# Symbol namespacing -- the load-bearing collision-avoidance design this
# task added: store.py's (symbol, interval, open_time_ms) primary key has
# no exchange/source column, so Binance rows are stored under a prefixed
# symbol ("BINANCE:BTCUSDT" / "BINANCE-FUTURES:BTCUSDT") rather than the
# bare wire symbol, with zero schema migration.
# ---------------------------------------------------------------------------


def test_sync_range_stores_spot_klines_under_the_binance_prefixed_symbol(spot_server, conn):
    spot_server.set_kline(BASE, "1", "1", "1", "1", "1")

    sync_range("spot", "BTCUSDT", "1d", BASE, BASE + STEP, conn=conn, base_url=spot_server.base_url)

    stored_symbols = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM klines")}
    assert stored_symbols == {"BINANCE:BTCUSDT"}


def test_sync_range_stores_futures_klines_under_a_different_prefixed_symbol(futures_server, conn):
    futures_server.set_kline(BASE, "1", "1", "1", "1", "1")

    sync_range("futures", "BTCUSDT", "1d", BASE, BASE + STEP, conn=conn, base_url=futures_server.base_url)

    stored_symbols = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM klines")}
    assert stored_symbols == {"BINANCE-FUTURES:BTCUSDT"}


def test_sync_range_sends_the_raw_unprefixed_symbol_to_the_wire(spot_server, conn):
    spot_server.set_kline(BASE, "1", "1", "1", "1", "1")

    sync_range("spot", "BTCUSDT", "1d", BASE, BASE + STEP, conn=conn, base_url=spot_server.base_url)

    assert spot_server.requests[0]["params"]["symbol"] == "BTCUSDT"  # never "BINANCE:BTCUSDT" on the wire


def test_sync_range_keeps_spot_and_futures_rows_independently_addressable(spot_server, futures_server, conn):
    # Same open time, same interval, same underlying market data horizon
    # -- but genuinely different symbols in different markets, so both
    # must coexist as distinct rows rather than colliding or overwriting
    # one another.
    spot_server.set_kline(BASE, "100", "100", "100", "100", "1")
    futures_server.set_kline(BASE, "101", "101", "101", "101", "1")  # deliberately different price (real basis)

    sync_range("spot", "BTCUSDT", "1d", BASE, BASE + STEP, conn=conn, base_url=spot_server.base_url)
    sync_range("futures", "BTCUSDT", "1d", BASE, BASE + STEP, conn=conn, base_url=futures_server.base_url)

    rows = conn.execute("SELECT symbol, open FROM klines ORDER BY symbol").fetchall()
    assert rows == [("BINANCE-FUTURES:BTCUSDT", "101"), ("BINANCE:BTCUSDT", "100")]


def test_sync_range_does_not_collide_with_an_existing_bingx_btc_usdt_row(spot_server, conn):
    # BingX's own storage symbol is "BTC-USDT" (with a dash); Binance's
    # raw wire symbol is "BTCUSDT" (no dash) -- a literal collision was
    # already unlikely by accident, but this pins the actual stored
    # namespacing decision rather than relying on that coincidence.
    upsert_klines(conn, "BTC-USDT", "1d", [_row(0)])
    spot_server.set_kline(BASE, "1", "1", "1", "1", "1")

    sync_range("spot", "BTCUSDT", "1d", BASE, BASE + STEP, conn=conn, base_url=spot_server.base_url)

    stored_symbols = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM klines")}
    assert stored_symbols == {"BTC-USDT", "BINANCE:BTCUSDT"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_rejects_an_unknown_market_via_argparse_choices(capsys):
    # main() itself does not re-validate args.market (see its own
    # comment) -- _parse_args' `choices=sorted(MARKET_CONFIG)` already
    # rejects an unknown market at the argparse layer, the same
    # trust-argparse convention backfill_macro.py's own `--series-id`
    # already uses. This test exercises that real argparse behavior
    # (SystemExit(2) + a usage message on stderr naming the bad value),
    # not a main()-level branch (CodeRabbit review finding on this PR:
    # the previous version of this test technically passed but only
    # ever exercised argparse, regardless of whether main() had its own
    # redundant check).
    with pytest.raises(SystemExit) as exc_info:
        main(["--market", "bybit", "--start", "2020-01-01T00:00:00+00:00", "--end", "2020-01-02T00:00:00+00:00"])

    assert exc_info.value.code == 2
    assert "bybit" in capsys.readouterr().err


def test_main_accepts_a_base_url_override_for_test_injection(monkeypatch, spot_server, tmp_path):
    # Unlike backfill.py's BINGX_BASE_URL (mandatory env var, no default
    # -- see bingx-hostname-guard.yml), Binance has no live-trading
    # surface in this project, so main() carries a real default base URL
    # per market (mirroring backfill_macro.py's DEFAULT_FRED_BASE_URL) --
    # overridden here only for test injection against the fake server.
    times = [BASE + i * STEP for i in range(2)]
    spot_server.set_klines(times)
    start_iso = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp((times[-1] + STEP) / 1000, tz=timezone.utc).isoformat()

    main(
        [
            "--market",
            "spot",
            "--start",
            start_iso,
            "--end",
            end_iso,
            "--db-path",
            str(tmp_path / "klines.sqlite3"),
            "--base-url",
            spot_server.base_url,
        ]
    )

    conn = connect(tmp_path / "klines.sqlite3")
    stored = [row[0] for row in conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms")]
    conn.close()
    assert stored == times


def test_main_defaults_symbol_to_btcusdt_and_market_to_spot(monkeypatch, spot_server, tmp_path):
    spot_server.set_kline(BASE, "1", "1", "1", "1", "1")
    start_iso = datetime.fromtimestamp(BASE / 1000, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp((BASE + STEP) / 1000, tz=timezone.utc).isoformat()

    main(
        [
            "--start",
            start_iso,
            "--end",
            end_iso,
            "--db-path",
            str(tmp_path / "klines.sqlite3"),
            "--base-url",
            spot_server.base_url,
        ]
    )

    conn = connect(tmp_path / "klines.sqlite3")
    stored_symbols = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM klines")}
    conn.close()
    assert stored_symbols == {"BINANCE:BTCUSDT"}


def test_main_floors_the_default_end_to_the_grid(monkeypatch, spot_server, tmp_path):
    # "Now" is frozen rather than left to the real wall clock (CodeRabbit
    # review finding on this PR): main() omits --end here specifically to
    # exercise its real datetime.now()-based default, but BASE is a fixed
    # ~2023-11 test epoch, so an unfrozen "now" would make the requested
    # range -- and therefore the request count and runtime -- grow
    # indefinitely as real calendar time passes after this test is
    # written. Freezing "now" a few days after BASE keeps the range (and
    # the test) small and deterministic regardless of when it's actually
    # run, while still exercising the real floor-to-grid code path.
    frozen_now_ms = BASE + 3 * STEP + STEP // 2  # a few days in, mid-day (not grid-aligned itself)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(frozen_now_ms / 1000, tz=tz)

    monkeypatch.setattr("data.backfill_binance.datetime", _FrozenDatetime)
    start_iso = datetime.fromtimestamp(BASE / 1000, tz=timezone.utc).isoformat()

    main(
        [
            "--market",
            "spot",
            "--interval",
            "1d",
            "--start",
            start_iso,
            "--db-path",
            str(tmp_path / "klines.sqlite3"),
            "--base-url",
            spot_server.base_url,
        ]
    )

    assert spot_server.requests, "expected at least one request to the exchange"
    # The wire endTime is inclusive and translated (end_ms - 1) -- so the
    # *pipeline-level* end_ms this run computed is one ms past whatever
    # was actually sent; reconstruct it before checking grid alignment.
    end_times = {int(r["params"]["endTime"]) + 1 for r in spot_server.requests}
    assert all(t % STEP == 0 for t in end_times), end_times
    # With "now" frozen, the floored end is exact and deterministic --
    # BASE + 3*STEP (the still-forming partial day is excluded).
    assert max(end_times) == BASE + 3 * STEP


def test_market_config_has_the_expected_real_defaults():
    assert set(MARKET_CONFIG) == {"spot", "futures"}
    assert DEFAULT_BINANCE_SPOT_BASE_URL == "https://api.binance.com"
    assert DEFAULT_BINANCE_FUTURES_BASE_URL == "https://fapi.binance.com"
    assert MARKET_CONFIG["spot"]["storage_symbol_prefix"] == "BINANCE:"
    assert MARKET_CONFIG["futures"]["storage_symbol_prefix"] == "BINANCE-FUTURES:"
