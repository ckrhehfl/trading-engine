"""Collect the positioning series that cannot be backfilled later.

    PYTHONPATH=python python -m data.collect_positioning
    PYTHONPATH=python python -m data.collect_positioning --coverage

Trade Management Task B. Meant to run from cron; idempotent, so a
re-run over an overlapping window inserts nothing rather than
duplicating.

**Why this runs before any strategy needs it.** The `/futures/data/`
endpoints retain ~30 days and cannot be backfilled. Six months of this
project's research kept concluding "we have no data for that" -- this is
the one class of data where that becomes permanently true if collection
does not start. Starting it costs one cron line.

Read-only public market data. No credentials, no orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import time

from data.binance_positioning import (
    DEFAULT_PERIOD,
    SPECS,
    collect_gap,
    VALID_PERIODS,
    BinancePositioningError,
    fetch_metric,
)
from data.store import connect, positioning_coverage, upsert_positioning

DEFAULT_DB_PATH = "python/data/var/klines.sqlite3"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")

# Two resolutions rather than one. "1h" is what a multi-day setup is read
# on and gives ~20 days per request; "5m" is what a same-day entry needs
# and gives ~42 hours. Collecting only the coarse one would foreclose the
# faster reading later, and that choice could not be undone.
DEFAULT_PERIODS = ("5m", "1h")

logger = logging.getLogger(__name__)


def _latest_stored(conn, symbol: str, spec_name: str, period: str) -> int | None:
    """Newest stored timestamp across every metric this endpoint writes.

    Uses the **minimum** of the per-metric maxima, not the overall max:
    one endpoint writes several metrics, and if any of them is behind,
    resuming from the furthest-ahead one would skip the laggard's gap
    forever. Resuming from the furthest-behind re-requests a few rows the
    others already have, which `INSERT OR IGNORE` discards for free.
    """
    metrics = tuple(SPECS[spec_name].fields.values())
    placeholders = ",".join("?" * len(metrics))
    row = conn.execute(
        f"SELECT MIN(newest) FROM (SELECT MAX(timestamp_ms) AS newest FROM positioning "
        f"WHERE symbol=? AND period=? AND metric IN ({placeholders}) GROUP BY metric)",
        (symbol, period, *metrics),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    # A metric with no rows at all does not appear in the GROUP BY, so a
    # non-null answer here still means "every metric has something".
    counted = conn.execute(
        f"SELECT COUNT(DISTINCT metric) FROM positioning WHERE symbol=? AND period=? "
        f"AND metric IN ({placeholders})",
        (symbol, period, *metrics),
    ).fetchone()[0]
    return row[0] if counted == len(metrics) else None


def collect(
    *,
    db_path: str = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    periods: tuple[str, ...] = DEFAULT_PERIODS,
) -> int:
    """Fetch every (symbol, metric, period) and store what is new.

    Returns the number of rows newly inserted. A failure on one series is
    logged and the run continues: a transient error on one endpoint must
    not cost the other four their collection window, which is the whole
    point of running this on a schedule.
    """
    conn = connect(db_path)
    try:
        inserted = 0
        failures = 0
        for symbol in symbols:
            for period in periods:
                for spec_name in SPECS:
                    try:
                        # Page from the newest row already stored rather
                        # than taking the plain newest-500. At `5m` those
                        # 500 rows span ~41 hours, so any outage longer
                        # than that leaves a hole no later run ever asks
                        # for again -- and `INSERT OR IGNORE` over a
                        # 30-day retention window makes the hole
                        # permanent while the data is still on the
                        # server. This host has genuinely been dark for
                        # 13 hours at a stretch and down for days.
                        rows = collect_gap(
                            spec_name, symbol, period=period,
                            since_ms=_latest_stored(conn, symbol, spec_name, period),
                            now_ms=int(time.time() * 1000),
                        )
                    except (BinancePositioningError, ValueError) as exc:
                        failures += 1
                        logger.warning("%s %s %s: %s", symbol, spec_name, period, exc)
                        continue
                    new = upsert_positioning(conn, symbol, rows)
                    inserted += new
                    logger.info(
                        "%s %s %s: %d row(s) returned, %d new",
                        symbol, spec_name, period, len(rows), new,
                    )
        if failures:
            logger.warning("%d series failed this run", failures)
        return inserted
    finally:
        conn.close()


def show_coverage(db_path: str = DEFAULT_DB_PATH) -> None:
    """How much history has actually accumulated.

    The question that matters most for a store whose upstream keeps only
    30 days: a collector that has been silently failing looks exactly
    like one that has nothing to collect, until this is printed.
    """
    conn = connect(db_path)
    try:
        rows = positioning_coverage(conn)
    finally:
        conn.close()
    if not rows:
        print("no positioning data collected yet")
        return
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"{'symbol':10} {'metric':32} {'period':>7} {'rows':>7}  {'earliest':16} .. {'latest':16}  span")
    print("-" * 108)
    for symbol, metric, period, count, lo, hi in rows:
        span_days = (hi - lo) / 86_400_000
        print(f"{symbol:10} {metric:32} {period:>7} {count:>7,}  {fmt(lo):16} .. {fmt(hi):16}  {span_days:5.1f}d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    ap.add_argument("--periods", nargs="+", default=list(DEFAULT_PERIODS), choices=VALID_PERIODS)
    ap.add_argument("--coverage", action="store_true",
                    help="report accumulated history and exit without fetching")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.coverage:
        show_coverage(args.db_path)
        return 0
    inserted = collect(
        db_path=args.db_path,
        symbols=tuple(args.symbols),
        periods=tuple(args.periods),
    )
    logger.info("done: %d new row(s)", inserted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
