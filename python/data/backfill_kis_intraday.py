"""Backfill KRX minute bars, resumably, against a real trading calendar.

**The clock is running on this one.** `inquire-time-dailychartprice`
serves a rolling ~250 trading days that advances one session every
session, so a session not collected is permanently lost. This runner
exists to get that history into the store before it expires, not because
anything downstream is waiting for it.

## Why the calendar is not optional

An out-of-range date returns `rt_cd=0` with **zero rows** — KIS's normal
way of saying "nothing here". So a backfill that treats a successful call
as success will record nothing for every unavailable date and report a
clean run. Coverage therefore has to be judged against something that
knows which days the market was actually open, and that is the **index
daily series** already in the store (`KRX-INDEX:0001`): an index prints
exactly when the market is open, so it needs no holiday table and it
covers the moving lunar holidays `KrxMarketCalendar` still lists as
unresolved. This is the same reasoning `backfill_kis.py` already uses for
daily bars, and the same reason `store.find_missing_ranges` is unusable
here — it diffs against an arithmetic sequence and would report ~116
false gaps per symbol-year.

## What a "missing" session means, and why the two are reported apart

A trading day with no bars is either **outside the rolling window** (the
expected, permanent state for anything older than ~250 sessions) or
**inside it and absent** (a real failure worth investigating). Reporting
them together would bury the second in the first, so the run classifies
each against the oldest date any symbol did return.

Run:

    python -m data.backfill_kis_intraday --symbols 005930,000660 --sessions 250
    python -m data.backfill_kis_intraday --symbols 005930 --verify
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time

from data._paths import DEFAULT_DB_PATH
from data.kis_intraday import (
    INTERVAL,
    MAX_PAGES_PER_SESSION,
    equity_storage_symbol,
    session_is_collected,
    stored_span,
    sync_session,
)
from data.kis_klines import (
    INTER_REQUEST_DELAY_S,
    KisKlinesError,
    KisSession,
    index_storage_symbol,
    ms_to_trading_date,
)
from data.store import connect, fetch_klines

LOGGER = logging.getLogger(__name__)

DAILY_INTERVAL = "1d"
MS_PER_DAY = 86_400_000
DEFAULT_INDEX = "0001"  # KOSPI, the reference calendar

#: The measured rolling horizon. Used only to size a default run and to
#: phrase the report — never to decide whether a date is available, which
#: is what the endpoint's own answer is for.
ROLLING_SESSIONS = 250


def trading_days(conn, index_code: str, limit: int | None = None) -> list[str]:
    """Trading dates from the index daily series, newest first.

    The index is the calendar: it prints exactly when the market is open.
    An empty result is an error rather than "no trading days", because a
    backfill that silently has nothing to do is indistinguishable from one
    that succeeded.
    """
    symbol = index_storage_symbol(index_code)
    # `fetch_klines` validates that both bounds sit on the interval grid,
    # so "everything" has to be spelled as an aligned range rather than a
    # convenient large integer.
    end = (int(time.time() * 1000) // MS_PER_DAY + 2) * MS_PER_DAY
    rows = fetch_klines(conn, symbol, DAILY_INTERVAL, 0, end)
    if not rows:
        raise KisKlinesError(
            f"no {DAILY_INTERVAL} bars for {symbol}; the reference calendar is "
            f"empty, so coverage cannot be judged. Backfill the index first: "
            f"`python -m data.backfill_kis --index {index_code} ...`"
        )
    days = sorted((ms_to_trading_date(r.open_time_ms) for r in rows), reverse=True)
    return days[:limit] if limit else days


#: SQLite's two contention codes, as primary result codes. Anything else
#: is a real fault and must not be absorbed by a loop designed to survive
#: a busy database.
SQLITE_BUSY = 5
SQLITE_LOCKED = 6
_CONTENTION = (SQLITE_BUSY, SQLITE_LOCKED)


def _is_contention(exc: sqlite3.OperationalError) -> bool:
    """Whether this error is another writer holding the file.

    **Compared on the primary result code, not the name.** SQLite returns
    *extended* codes — `SQLITE_BUSY_SNAPSHOT`, `SQLITE_LOCKED_SHAREDCACHE`
    — and an exact name match treats those as fatal, stopping a run for
    exactly the condition this function exists to tolerate. The primary
    code lives in the **low 8 bits** of the extended one, which is the
    documented relationship rather than a guess about naming.

    The message check is a fallback so an interpreter without
    `sqlite_errorcode` (pre-3.11) degrades to tolerating a lock rather
    than to crashing on one.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in _CONTENTION
    return "locked" in str(exc).lower() or "busy" in str(exc).lower()


def backfill(
    conn,
    session: KisSession,
    codes: list[str],
    dates: list[str],
    *,
    delay_s: float = INTER_REQUEST_DELAY_S,
) -> dict:
    """Fetch every `(code, date)` not already collected.

    **Oldest session first, and all symbols abreast of each other.** In a
    rolling window it is the *oldest* session that expires next, so an
    interrupted run must have secured those and may safely leave the newest
    — they will still be there tomorrow. Iterating date-outer keeps every
    symbol at the same frontier, so an interruption leaves ten partial
    symbols covering one common range rather than four complete symbols and
    six empty ones.

    An earlier version did the exact opposite, newest-first and
    symbol-outer, while this docstring already argued for perishability.
    The reasoning was right and the loop contradicted it.
    """
    stats = {
        "calls": 0, "sessions_fetched": 0, "sessions_skipped": 0,
        "sessions_empty": 0, "bars_inserted": 0, "errors": [],
    }
    started = time.time()
    done: dict[str, int] = {code: 0 for code in codes}
    for date in reversed(dates):  # `dates` arrives newest-first
        for code in codes:
            symbol = equity_storage_symbol(code)
            if session_is_collected(conn, symbol, date):
                stats["sessions_skipped"] += 1
                continue
            try:
                fetched, inserted = sync_session(
                    conn, session, code, date, delay_s=delay_s
                )
            except KisKlinesError as exc:
                # One bad session must not end a multi-hour run; it is
                # recorded, the report names it, and the next run retries
                # it because `session_is_collected` will still say no.
                stats["errors"].append(f"{code} {date}: {exc}")
                LOGGER.warning("%s %s failed: %s", code, date, exc)
                continue
            except sqlite3.OperationalError as exc:
                # **Only contention is tolerated.** This store is written
                # concurrently by two cron collectors and SQLite locks the
                # whole file, so a run of this length should degrade by
                # losing one session to a busy database rather than by
                # stopping. Anything else — a schema fault, a full disk —
                # would otherwise be logged 2,500 times while the run
                # produced nothing and reported a tidy list of "errors".
                conn.rollback()
                if not _is_contention(exc):
                    raise
                stats["errors"].append(f"{code} {date}: {exc}")
                LOGGER.warning("%s %s lost to a busy database: %s", code, date, exc)
                continue
            # An upper bound: paging stops as soon as a page adds nothing,
            # so most sessions cost fewer than the maximum.
            stats["calls"] += MAX_PAGES_PER_SESSION
            if fetched:
                stats["sessions_fetched"] += 1
                stats["bars_inserted"] += inserted
                done[code] += 1
            else:
                stats["sessions_empty"] += 1
            conn.commit()
        if sum(done.values()) and date == dates[0]:
            LOGGER.info("reached the newest session %s", date)
    LOGGER.info(
        "%d fetched, %d skipped, %d empty, %d bars, %.1f min",
        stats["sessions_fetched"], stats["sessions_skipped"],
        stats["sessions_empty"], stats["bars_inserted"],
        (time.time() - started) / 60,
    )
    stats["minutes"] = (time.time() - started) / 60
    return stats


def coverage(conn, codes: list[str], dates: list[str]) -> dict:
    """Per symbol: collected sessions, and the missing ones split by cause.

    **The split is the point.** Everything older than the rolling window
    is missing and always will be; conflating that with a session that
    should be there and is not would hide every real failure inside an
    expected one. The boundary is taken from the data — the oldest date
    anything was actually collected for — rather than from the nominal
    ~250, because the real horizon moves daily.
    """
    collected: dict[str, list[str]] = {}
    for code in codes:
        symbol = equity_storage_symbol(code)
        collected[code] = [d for d in dates if session_is_collected(conn, symbol, d)]

    horizon = min(
        (days[-1] for days in collected.values() if days), default=None
    )
    out = {}
    for code in codes:
        have = set(collected[code])
        missing = [d for d in dates if d not in have]
        out[code] = {
            "collected": len(have),
            "missing_in_window": [
                d for d in missing if horizon is not None and d >= horizon
            ],
            "missing_older_than_window": [
                d for d in missing if horizon is None or d < horizon
            ],
        }
    return {"horizon": horizon, "symbols": out}


def _report(conn, codes: list[str], dates: list[str]) -> None:
    cov = coverage(conn, codes, dates)
    print(f"reference calendar: {len(dates)} trading days, "
          f"{dates[-1]} .. {dates[0]}")
    print(f"observed rolling horizon: {cov['horizon'] or '(nothing collected)'}\n")
    print(f"{'symbol':12} {'collected':>10} {'missing(in)':>12} {'missing(old)':>13}  span of newest")
    print("-" * 78)
    for code, c in cov["symbols"].items():
        newest = next((d for d in dates if session_is_collected(
            conn, equity_storage_symbol(code), d)), None)
        span = ""
        if newest:
            n, first, last = stored_span(conn, equity_storage_symbol(code), newest)
            span = f"{newest} {first}..{last} ({n} bars)"
        print(f"{code:12} {c['collected']:>10} {len(c['missing_in_window']):>12} "
              f"{len(c['missing_older_than_window']):>13}  {span}")
    bad = {k: v["missing_in_window"] for k, v in cov["symbols"].items()
           if v["missing_in_window"]}
    if bad:
        print("\nMISSING INSIDE THE ROLLING WINDOW -- these are real gaps:")
        for code, days in bad.items():
            print(f"  {code}: {len(days)} session(s), newest {days[0]}, oldest {days[-1]}")
    else:
        print("\nNo session is missing inside the observed rolling window.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", required=True, help="comma-separated 6-digit codes")
    p.add_argument("--index", default=DEFAULT_INDEX, help="reference calendar index code")
    p.add_argument("--sessions", type=int, default=ROLLING_SESSIONS,
                   help="how many trading days back to attempt")
    p.add_argument("--db-path", default=DEFAULT_DB_PATH)
    p.add_argument("--delay", type=float, default=INTER_REQUEST_DELAY_S)
    p.add_argument("--verify", action="store_true",
                   help="report coverage without fetching anything")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    if not codes:
        print("--symbols is empty", file=sys.stderr)
        return 2

    conn = connect(args.db_path)
    try:
        dates = trading_days(conn, args.index, limit=args.sessions)
        if args.verify:
            _report(conn, codes, dates)
            return 0

        key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
        if not key or not secret:
            # Presence only, never the value -- this project has had one
            # real credential-in-a-log incident already.
            print("KIS_APP_KEY / KIS_APP_SECRET are not set", file=sys.stderr)
            return 2

        session = KisSession(key, secret)
        LOGGER.info(
            "backfilling %d symbol(s) x %d session(s); at up to %d calls each, "
            "at most %d calls",
            len(codes), len(dates), MAX_PAGES_PER_SESSION,
            len(codes) * len(dates) * MAX_PAGES_PER_SESSION,
        )
        stats = backfill(conn, session, codes, dates, delay_s=args.delay)
        conn.commit()
        print(
            f"\n{stats['sessions_fetched']} session(s) fetched, "
            f"{stats['sessions_skipped']} already collected, "
            f"{stats['sessions_empty']} empty, "
            f"{stats['bars_inserted']:,} bars inserted, "
            f"{stats['calls']:,} calls, {stats['minutes']:.1f} min"
        )
        if stats["errors"]:
            print(f"\n{len(stats['errors'])} session(s) errored:")
            for line in stats["errors"][:20]:
                print(f"  {line}")
        print()
        _report(conn, codes, dates)
        return 1 if stats["errors"] else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
