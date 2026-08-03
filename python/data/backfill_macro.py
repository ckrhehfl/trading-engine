"""CLI entry point: idempotent FRED macro-series backfill into the local
SQLite cache. Mirrors `backfill.py`/`backfill_funding.py` structurally
(see those modules' docstrings and `.planning/sr-w-macro-data-pipeline.md`);
adapted for FRED's inclusive `YYYY-MM-DD` date range instead of a
half-open ms range, and for three named series instead of one
symbol/interval pair.

`sync_macro_range` is the reusable, testable core (idempotent, safe to
interrupt and rerun -- only genuinely missing weekdays are ever
fetched, via `store.find_missing_macro_ranges` feeding
`fred_client.iter_observations`). `main()` is a thin CLI wrapper.

Unlike `backfill.py`/`backfill_funding.py`, the FRED base URL has a real
default in this module (`DEFAULT_FRED_BASE_URL`) rather than requiring
an environment variable: FRED has no live/demo host split the way
BingX does (see `fred_client.py`'s module docstring), so there is no
`bingx-hostname-guard`-style safety reason to force it through an env
var -- `--base-url` exists purely for test injection. The API key,
by contrast, always comes from `--api-key` or the `FRED_API_KEY`
environment variable, never a literal -- same credential-handling
discipline as BingX's `BINGX_API_KEY`/`BINGX_API_SECRET` (see
CLAUDE.md's Non-negotiable Rules).
"""

import argparse
import logging
import os
import sqlite3
from datetime import datetime, timezone

from data.backfill import DEFAULT_DB_PATH
from data.fred_client import MAX_OBSERVATIONS_PER_REQUEST, ObservationRow, iter_observations
from data.store import connect, find_missing_macro_ranges, upsert_macro_observations

logger = logging.getLogger(__name__)

DEFAULT_FRED_BASE_URL = "https://api.stlouisfed.org"

# Real, verified `observation_start` per series (fetched live
# 2026-08-03 via the `/fred/series` metadata endpoint -- see
# .planning/sr-w-macro-data-pipeline.md). Used as each series' default
# --start for a full-history backfill: per fred_client.py's module
# docstring, requesting further back than a series' true start isn't an
# error, it's just silently unproductive, so there's no "unknown
# retention, probe to discover it" step here the way bingx_klines.py
# originally needed for BingX -- FRED already publishes each series'
# real start date, there's nothing to discover by probing blindly.
SERIES_START_DATE = {
    "DTWEXBGS": "2006-01-02",  # Nominal Broad U.S. Dollar Index
    "SP500": "2016-08-01",  # S&P 500
    "DGS10": "1962-01-02",  # 10-Year Treasury Constant Maturity Yield
}

# Same incremental-write reasoning as backfill.py's/backfill_funding.py's
# _UPSERT_BATCH_SIZE: batching at (roughly) one page's worth rather than
# buffering a whole gap in memory means an interrupted run loses at most
# one in-flight page's worth of already-fetched-but-unwritten rows.
_UPSERT_BATCH_SIZE = MAX_OBSERVATIONS_PER_REQUEST


def sync_macro_range(
    series_id: str,
    start_date: str,
    end_date: str,
    *,
    conn: sqlite3.Connection,
    base_url: str,
    api_key: str,
) -> int:
    """Fetch and store only the macro observations actually missing from
    the local cache within the inclusive range `[start_date, end_date]`.

    Idempotent and safe to interrupt -- identical resumability contract
    to `backfill.sync_range`/`backfill_funding.sync_funding_range`, see
    those functions' docstrings. `find_missing_macro_ranges`'s weekday-
    aware gap detection (see `store.py`) is what makes an ordinary
    weekend a permanent non-gap rather than something this function ever
    tries to fetch. Returns the total number of newly inserted rows.
    """
    total_inserted = 0
    for gap_start, gap_end in find_missing_macro_ranges(conn, series_id, start_date, end_date):
        logger.info("fetching missing macro range [%s, %s] for %s", gap_start, gap_end, series_id)
        fetched_in_gap = 0
        batch: list[ObservationRow] = []
        for row in iter_observations(base_url, series_id, api_key, gap_start, gap_end):
            batch.append(row)
            fetched_in_gap += 1
            if len(batch) >= _UPSERT_BATCH_SIZE:
                total_inserted += upsert_macro_observations(conn, series_id, batch)
                batch = []
        if batch:
            total_inserted += upsert_macro_observations(conn, series_id, batch)
        logger.info(
            "range [%s, %s]: fetched %d rows (total newly inserted so far: %d)",
            gap_start,
            gap_end,
            fetched_in_gap,
            total_inserted,
        )

    return total_inserted


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--series-id",
        action="append",
        dest="series_ids",
        choices=sorted(SERIES_START_DATE),
        help="repeatable; default: all three candidate series (%s)" % ", ".join(sorted(SERIES_START_DATE)),
    )
    parser.add_argument(
        "--start",
        default=None,
        help="ISO date YYYY-MM-DD (default: the series' own verified observation_start, see SERIES_START_DATE)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="ISO date YYYY-MM-DD (default: today, UTC)",
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="shares backfill.py's default DB path")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_FRED_BASE_URL,
        help="FRED API base URL (default: the real FRED host -- see module docstring for why this has a real default, unlike backfill.py's BINGX_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("FRED_API_KEY"),
        help="FRED API key (default: $FRED_API_KEY env var; never hardcoded -- see CLAUDE.md's Non-negotiable Rules)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    if not args.api_key:
        raise SystemExit("FRED API key is required: pass --api-key or set FRED_API_KEY")

    series_ids = args.series_ids or sorted(SERIES_START_DATE)
    end_date = args.end or datetime.now(timezone.utc).date().isoformat()

    conn = connect(args.db_path)
    try:
        for series_id in series_ids:
            start_date = args.start or SERIES_START_DATE[series_id]
            total = sync_macro_range(
                series_id,
                start_date,
                end_date,
                conn=conn,
                base_url=args.base_url,
                api_key=args.api_key,
            )
            logger.info(
                "done: %d new row(s) inserted for %s over [%s, %s]",
                total,
                series_id,
                start_date,
                end_date,
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
