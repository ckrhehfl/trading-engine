"""CLI entry point: idempotent Binance kline backfill into the local
SQLite cache. Mirrors `backfill.py`/`backfill_macro.py` structurally --
see `.planning/sr-z-binance-data-research.md` for the full investigation
this task ran (why a second exchange's data matters, real backfill
numbers, gap findings, cross-exchange correlation, and the proposed
regime segmentation).

`sync_range` is the reusable, testable core (idempotent, safe to
interrupt and rerun -- only genuinely missing sub-ranges are ever
fetched, via `store.find_missing_ranges` feeding
`binance_klines.iter_klines_range`). `main()` is a thin CLI wrapper.

Supports two markets, `"spot"` (primary target -- real full history back
to Binance's own verified 2017-08-17 listing date) and `"futures"`
(secondary/lighter comparison check only -- see the planning doc for why
spot, not futures, is the primary target: spot's history reaches ~2
years further back, and sample size is what this task is chasing).

**Symbol namespacing** -- the key design decision this task made:
`store.py`'s `klines` table has no `exchange`/`source` column, keyed only
on `(symbol, interval, open_time_ms)`. Binance's own raw wire symbol
(`"BTCUSDT"`, no dash) happens to differ from BingX's stored symbol
(`"BTC-USDT"`, with a dash) so a literal collision is unlikely by
accident -- but relying on that accident would be fragile and not
self-documenting, and spot/futures would collide with EACH OTHER even
though they are genuinely different markets with genuinely different
prices (a real, verified basis divergence -- see the planning doc).
`sync_range` therefore stores every Binance row under a prefixed
`storage_symbol` (`"BINANCE:BTCUSDT"` for spot, `"BINANCE-
FUTURES:BTCUSDT"` for futures) rather than the raw wire symbol --
additive, zero schema migration, same style as `1d`'s addition to
`_grid.py` and `holdout_side`'s addition to holdout configs. The raw,
unprefixed wire symbol is still what actually goes out over the network
(Binance has never heard of `"BINANCE:BTCUSDT"`) -- namespacing is a
`store.py`-layer concern only, applied here, never inside
`binance_klines.py` itself.

Like `backfill_macro.py`'s `DEFAULT_FRED_BASE_URL` (and unlike
`backfill.py`'s mandatory-env-var-only `BINGX_BASE_URL`), both Binance
base URLs have real hardcoded defaults here rather than requiring an
environment variable: this project builds no live-trading surface
against Binance at all (no `ExchangeAdapter`, no credentials, no order
placement -- read-only historical market data only), so there is no
`bingx-hostname-guard.yml`-style live/demo safety distinction to
enforce. `--base-url` exists purely for test injection against the fake
server, same reasoning `backfill_macro.py`'s own docstring gives for
`DEFAULT_FRED_BASE_URL`.
"""

import argparse
import logging
import sqlite3
from datetime import datetime, timezone

from data._grid import interval_ms, require_valid_range
from data.backfill import DEFAULT_DB_PATH
from data.binance_klines import (
    FUTURES_KLINES_PATH,
    MAX_CANDLES_PER_REQUEST,
    SPOT_KLINES_PATH,
    iter_klines_range,
)
from data.bingx_klines import KlineRow
from data.store import connect, find_missing_ranges, upsert_klines

logger = logging.getLogger(__name__)

DEFAULT_BINANCE_SPOT_BASE_URL = "https://api.binance.com"
DEFAULT_BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"

# Real, verified listing/retention starts (see module docstring and
# .planning/sr-z-binance-data-research.md) -- used as each market's
# default --start for a full-history backfill, same "the exchange
# already tells us its own real start, no blind probing needed"
# reasoning as backfill_macro.py's SERIES_START_DATE.
MARKET_CONFIG = {
    "spot": {
        "path": SPOT_KLINES_PATH,
        "default_base_url": DEFAULT_BINANCE_SPOT_BASE_URL,
        "storage_symbol_prefix": "BINANCE:",
        "default_start_iso": "2017-08-17T00:00:00+00:00",
    },
    "futures": {
        "path": FUTURES_KLINES_PATH,
        "default_base_url": DEFAULT_BINANCE_FUTURES_BASE_URL,
        "storage_symbol_prefix": "BINANCE-FUTURES:",
        "default_start_iso": "2019-09-08T00:00:00+00:00",
    },
}

# Same incremental-write reasoning as backfill.py's _UPSERT_BATCH_SIZE:
# batching at one page's worth rather than buffering a whole gap in
# memory means an interrupted run loses at most one in-flight page's
# worth of already-fetched-but-unwritten rows.
_UPSERT_BATCH_SIZE = MAX_CANDLES_PER_REQUEST


def sync_range(
    market: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    conn: sqlite3.Connection,
    base_url: str,
) -> int:
    """Fetch and store only the klines actually missing from the local
    cache within the half-open range `[start_ms, end_ms)`, for the given
    `market` (`"spot"` or `"futures"`) and raw Binance `symbol` (e.g.
    `"BTCUSDT"` -- never a namespaced storage symbol; namespacing is
    applied internally, see module docstring).

    Idempotent and safe to interrupt -- identical resumability contract
    to `backfill.sync_range`, see that function's docstring; the only
    real difference is the extra `storage_symbol` indirection between the
    wire symbol Binance is asked about and the symbol `store.py` actually
    reads/writes under. Returns the total number of newly inserted rows.
    """
    if market not in MARKET_CONFIG:
        raise ValueError(f"unknown market {market!r}; supported: {sorted(MARKET_CONFIG)}")
    config = MARKET_CONFIG[market]
    storage_symbol = config["storage_symbol_prefix"] + symbol

    step = interval_ms(interval)
    require_valid_range(start_ms, end_ms, step)

    total_inserted = 0
    for gap_start, gap_end in find_missing_ranges(conn, storage_symbol, interval, start_ms, end_ms):
        logger.info("fetching missing range [%d, %d) for %s/%s (%s)", gap_start, gap_end, symbol, interval, market)
        fetched_in_gap = 0
        batch: list[KlineRow] = []
        for row in iter_klines_range(base_url, config["path"], symbol, interval, gap_start, gap_end):
            batch.append(row)
            fetched_in_gap += 1
            if len(batch) >= _UPSERT_BATCH_SIZE:
                total_inserted += upsert_klines(conn, storage_symbol, interval, batch)
                batch = []
        if batch:
            total_inserted += upsert_klines(conn, storage_symbol, interval, batch)
        logger.info(
            "range [%d, %d): fetched %d rows (total newly inserted so far: %d)",
            gap_start,
            gap_end,
            fetched_in_gap,
            total_inserted,
        )

    return total_inserted


def _to_ms(iso_str: str) -> int:
    """Same ISO-8601-to-epoch-ms parsing as `backfill._to_ms` -- a naive
    (timezone-less) input is treated as UTC. Duplicated rather than
    imported for the same "each CLI module owns its own tiny parsing
    helper" precedent `backfill_macro.py` already established (it
    doesn't need this one at all, since FRED's unit is a calendar date,
    not epoch ms -- this module's need for it is closer to
    `backfill.py`'s than to `backfill_macro.py`'s).
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="spot", choices=sorted(MARKET_CONFIG))
    parser.add_argument("--symbol", default="BTCUSDT", help="Binance's own raw wire symbol, no exchange prefix")
    parser.add_argument("--interval", default="1d")
    parser.add_argument(
        "--start",
        default=None,
        help="ISO 8601 start timestamp (default: the market's own verified listing/retention start, see MARKET_CONFIG)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="ISO 8601 end timestamp (default: now, floored to the interval grid)",
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="shares backfill.py's default DB path")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Binance API base URL (default: the market's own real host -- see module docstring)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    # No redundant re-check of args.market here -- _parse_args' own
    # `choices=sorted(MARKET_CONFIG)` already rejects an unknown market
    # at the argparse layer (a real HTTP-400-style SystemExit(2) before
    # this function body ever runs), same trust-argparse convention
    # backfill_macro.py's own `--series-id choices=` already establishes
    # (CodeRabbit review finding on this PR: an extra check here would be
    # unreachable dead code, and the test that had targeted it would
    # only ever be exercising argparse's own behavior). `sync_range`'s
    # own `market not in MARKET_CONFIG` guard is unrelated and stays --
    # it's real defense-in-depth for a caller invoking it directly,
    # bypassing this CLI layer entirely.
    config = MARKET_CONFIG[args.market]

    step = interval_ms(args.interval)
    start_ms = _to_ms(args.start or config["default_start_iso"])
    # "now", floored to the grid -- excludes the still-forming current
    # candle. Applied as a hard CAP on end_ms regardless of whether
    # --end was given, not just as the default when it's omitted:
    # CodeRabbit review finding on this PR -- a caller-supplied --end at
    # or after "now" would otherwise request a grid slot Binance is
    # still actively updating (require_valid_range only checks grid
    # alignment and start < end, not "not in the future"), and once
    # such a row is stored, find_missing_ranges treats it as
    # permanently present -- a rerun after the candle actually closes
    # would never refetch and correct it. Capping here means the
    # partial-candle risk is structurally avoided rather than merely
    # documented.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    now_floored_ms = (now_ms // step) * step
    end_ms = min(_to_ms(args.end), now_floored_ms) if args.end else now_floored_ms

    base_url = args.base_url or config["default_base_url"]

    conn = connect(args.db_path)
    try:
        total = sync_range(
            args.market,
            args.symbol,
            args.interval,
            start_ms,
            end_ms,
            conn=conn,
            base_url=base_url,
        )
        logger.info(
            "done: %d new row(s) inserted for %s/%s (%s) over [%d, %d)",
            total,
            args.symbol,
            args.interval,
            args.market,
            start_ms,
            end_ms,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
