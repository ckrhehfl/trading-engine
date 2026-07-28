"""CLI entry point: idempotent BingX funding-rate backfill into the local
SQLite cache. Mirrors `backfill.py` structurally (see that module's
docstring and `.planning/sr-a-data-pipeline.md`); adapted for funding's
fixed 8-hour cadence (`FUNDING_INTERVAL_MS`) instead of a caller-supplied
kline interval, and for the read/gap-detection functions being scoped by
`symbol` only (no `interval`) since funding rate has no interval concept.
See `.planning/sr-m-funding-rate-pipeline.md`.

`sync_funding_range` is the reusable, testable core (idempotent, safe to
interrupt and rerun -- only genuinely missing sub-ranges are ever
fetched, via `store.find_missing_funding_ranges` feeding
`bingx_funding.iter_funding_range`). `main()` is a thin CLI wrapper.

The BingX base URL is deliberately never a literal in this module --
read from `--base-url` or the `BINGX_BASE_URL` environment variable at
run time, same as `backfill.py`; see
`.github/workflows/bingx-hostname-guard.yml`.
"""

import argparse
import logging
import os
import sqlite3
from datetime import datetime, timezone

from data.backfill import DEFAULT_DB_PATH, _to_ms
from data.bingx_funding import (
    FUNDING_INTERVAL_MS,
    MAX_FUNDING_ROWS_PER_REQUEST,
    FundingRow,
    iter_funding_range,
)
from data.store import connect, find_missing_funding_ranges, upsert_funding_rates

logger = logging.getLogger(__name__)


def _validate_range(start_ms: int, end_ms: int) -> None:
    # Deliberately `start_ms < end_ms` only, not grid alignment -- same
    # reasoning as `bingx_funding._validate_range` and
    # `store._validate_funding_range`: real historical funding
    # timestamps are not always aligned to `FUNDING_INTERVAL_MS` (see
    # `bingx_funding.py`'s module docstring), so this module must not
    # reject an off-grid `start_ms`/`end_ms` either.
    if start_ms >= end_ms:
        raise ValueError(f"start_ms ({start_ms}) must be < end_ms ({end_ms})")


# Same incremental-write reasoning as backfill.py's _UPSERT_BATCH_SIZE:
# batching at (roughly) one page's worth rather than buffering a whole
# gap in memory means an interrupted run loses at most one in-flight
# page's worth of already-fetched-but-unwritten rows, not the entire
# gap. See backfill.py's own comment and .planning/sr-a-data-pipeline.md
# ("Judgment calls resolved without asking") for the incident that made
# this a hard requirement rather than a nice-to-have.
_UPSERT_BATCH_SIZE = MAX_FUNDING_ROWS_PER_REQUEST


def sync_funding_range(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    conn: sqlite3.Connection,
    base_url: str,
) -> int:
    """Fetch and store only the funding rows actually missing from the
    local cache within the half-open range `[start_ms, end_ms)`.

    Idempotent and safe to interrupt -- identical resumability contract
    to `backfill.sync_range`, see that function's docstring. Also the
    practical mitigation for `bingx_funding`'s confirmed flaky-null
    history behavior (see that module's docstring): even though
    `fetch_funding_page` already retries a `null` response several times
    before giving up, a rerun of this function will pick up and re-fetch
    any range a prior run's exhausted retries still left empty, since
    that range simply shows up as still-missing in
    `find_missing_funding_ranges` next time. Returns the total number of
    newly inserted rows.
    """
    _validate_range(start_ms, end_ms)

    total_inserted = 0
    for gap_start, gap_end in find_missing_funding_ranges(conn, symbol, start_ms, end_ms):
        logger.info("fetching missing funding range [%d, %d) for %s", gap_start, gap_end, symbol)
        fetched_in_gap = 0
        batch: list[FundingRow] = []
        for row in iter_funding_range(base_url, symbol, gap_start, gap_end):
            batch.append(row)
            fetched_in_gap += 1
            if len(batch) >= _UPSERT_BATCH_SIZE:
                total_inserted += upsert_funding_rates(conn, symbol, batch)
                batch = []
        if batch:
            total_inserted += upsert_funding_rates(conn, symbol, batch)
        logger.info(
            "range [%d, %d): fetched %d rows (total newly inserted so far: %d)",
            gap_start,
            gap_end,
            fetched_in_gap,
            total_inserted,
        )

    return total_inserted


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTC-USDT")
    parser.add_argument(
        "--start",
        required=True,
        help="ISO 8601 start timestamp, e.g. 2020-01-01T00:00:00+00:00 (naive input treated as UTC)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="ISO 8601 end timestamp (default: now, floored to the 8h funding grid)",
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="shares backfill.py's default DB path -- klines and funding rates live in the same cache file",
    )
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

    start_ms = _to_ms(args.start)
    if args.end:
        end_ms = _to_ms(args.end)
    else:
        # "now", floored to the funding grid -- same one deliberate
        # rounding exception as backfill.py's main() (see that module's
        # docstring): every explicit, user-supplied start/end is
        # validated and rejected if misaligned, but our own computed
        # "now" default is never grid-aligned by construction.
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        end_ms = (now_ms // FUNDING_INTERVAL_MS) * FUNDING_INTERVAL_MS

    conn = connect(args.db_path)
    try:
        total = sync_funding_range(
            args.symbol,
            start_ms,
            end_ms,
            conn=conn,
            base_url=args.base_url,
        )
        logger.info(
            "done: %d new row(s) inserted for %s over [%d, %d)",
            total,
            args.symbol,
            start_ms,
            end_ms,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
