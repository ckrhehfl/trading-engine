"""CLI entry point: idempotent BingX kline backfill into the local
SQLite cache. See CLAUDE.md's "Strategy Research Operational Design"
section and `.planning/sr-a-data-pipeline.md`.

`sync_range` is the reusable, testable core (idempotent, safe to
interrupt and rerun -- only genuinely missing sub-ranges are ever
fetched, via `store.find_missing_ranges` feeding
`bingx_klines.iter_klines_range`). `main()` is a thin CLI wrapper.

The BingX base URL is deliberately never a literal in this module --
read from `--base-url` or the `BINGX_BASE_URL` environment variable at
run time. Same "caller decides the host" pattern as
`BingXAdapter`/`BingXPriceFeed` in `java/`; see
`.github/workflows/bingx-hostname-guard.yml`, which blocks the
production hostname string from ever appearing literally under `java/`
or `python/`.
"""

import argparse
import logging
import os
import sqlite3
from datetime import datetime, timezone

from data._grid import interval_ms, require_valid_range
from data.bingx_klines import MAX_CANDLES_PER_REQUEST, KlineRow, iter_klines_range
from data.store import connect, find_missing_ranges, upsert_klines

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "python/data/var/klines.sqlite3"

# Rows are upserted in batches of roughly one page's worth rather than
# once per gap. For a store that starts empty, find_missing_ranges
# returns a single gap covering the *entire* requested range -- e.g. the
# whole multi-year sentinel-to-now span used to discover BingX's real
# retention depth (see .planning/sr-a-data-pipeline.md). Buffering that
# whole gap's rows in memory before writing any of them would mean a
# crash/interrupt partway through loses *all* progress, not just the
# unfetched tail -- directly undermining "safe to interrupt/rerun".
# Batching at page granularity makes an interruption lose at most one
# in-flight page's worth of already-fetched-but-unwritten rows.
_UPSERT_BATCH_SIZE = MAX_CANDLES_PER_REQUEST


def sync_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    conn: sqlite3.Connection,
    base_url: str,
) -> int:
    """Fetch and store only the klines actually missing from the local
    cache within the half-open range `[start_ms, end_ms)`.

    Idempotent and safe to interrupt: a rerun with the same arguments
    after a partial prior run (crash, Ctrl-C, exhausted retries) only
    re-fetches whatever `find_missing_ranges` still reports as missing --
    already-stored rows are never re-requested, and re-fetching an
    overlapping range is a no-op via `upsert_klines`'s `INSERT OR
    IGNORE`. Rows are written incrementally (see `_UPSERT_BATCH_SIZE`),
    not buffered for the whole gap, so an interruption loses at most one
    batch of already-fetched work. Returns the total number of newly
    inserted rows.
    """
    step = interval_ms(interval)
    require_valid_range(start_ms, end_ms, step)

    total_inserted = 0
    for gap_start, gap_end in find_missing_ranges(conn, symbol, interval, start_ms, end_ms):
        logger.info("fetching missing range [%d, %d) for %s/%s", gap_start, gap_end, symbol, interval)
        fetched_in_gap = 0
        batch: list[KlineRow] = []
        for row in iter_klines_range(base_url, symbol, interval, gap_start, gap_end):
            batch.append(row)
            fetched_in_gap += 1
            if len(batch) >= _UPSERT_BATCH_SIZE:
                total_inserted += upsert_klines(conn, symbol, interval, batch)
                batch = []
        if batch:
            total_inserted += upsert_klines(conn, symbol, interval, batch)
        logger.info(
            "range [%d, %d): fetched %d rows (total newly inserted so far: %d)",
            gap_start,
            gap_end,
            fetched_in_gap,
            total_inserted,
        )

    return total_inserted


def _to_ms(iso_str: str) -> int:
    """Parse an ISO 8601 timestamp to epoch milliseconds. A naive
    (timezone-less) input is treated as UTC. Deliberately does not
    round/align to the interval grid -- misaligned input is a caller
    error and `sync_range` (via `require_valid_range`) fails loud on it,
    same as everywhere else in this pipeline.
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTC-USDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument(
        "--start",
        required=True,
        help="ISO 8601 start timestamp, e.g. 2020-01-01T00:00:00+00:00 (naive input treated as UTC)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="ISO 8601 end timestamp (default: now, floored to the interval grid)",
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BINGX_BASE_URL"),
        help="BingX API base URL (default: $BINGX_BASE_URL env var)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    if not args.base_url:
        raise SystemExit(
            "base URL is required: pass --base-url or set BINGX_BASE_URL "
            "(never hardcoded in source -- see bingx-hostname-guard.yml)"
        )

    step = interval_ms(args.interval)
    start_ms = _to_ms(args.start)
    if args.end:
        end_ms = _to_ms(args.end)
    else:
        # "now", floored to the grid -- the one place this module rounds
        # rather than fails loud, since this default is computed by us,
        # not user-supplied input. See .planning/sr-a-data-pipeline.md.
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        end_ms = (now_ms // step) * step

    conn = connect(args.db_path)
    try:
        total = sync_range(
            args.symbol,
            args.interval,
            start_ms,
            end_ms,
            conn=conn,
            base_url=args.base_url,
        )
        logger.info(
            "done: %d new row(s) inserted for %s/%s over [%d, %d)",
            total,
            args.symbol,
            args.interval,
            start_ms,
            end_ms,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
