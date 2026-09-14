"""Backfill KRX daily bars into the kline store — Multi-Asset Task F prep.

Follows `backfill.py` and `backfill_binance.py`, with three differences
that come from what KIS and KRX actually do rather than from preference:

**1. Coverage is measured against an index, not an arithmetic grid.**
`store.find_missing_ranges` diffs stored bars against `ts + interval_ms`.
That is right for a market trading every day and wrong for one trading
about 245 days a year: it reports every weekend and holiday as a gap, on
the order of 116 per symbol per year (58 measured over six months). An
index prints on exactly the days the market is open, so the index series
*is* the trading calendar — no holiday table to go stale, moving lunar
holidays handled because KRX itself decided them, and a market closure
distinguishable from a stock-specific halt. Full reasoning:
`.planning/ms-b-kis-history-probe-result.md` §6.1.

Consequence: **the reference index must be backfilled and verified first**,
and `--verify` refuses to judge a symbol whose reference is incomplete.

**2. One session for the whole run.** `POST /oauth2/tokenP` rate-limits
after roughly three issuances in a few minutes, on an allowance shared
with the live `kis-paper` JVM. A backfill that re-authenticates per symbol
can stop a running loop from renewing its own token.

**3. `--adjusted` is required.** `FID_ORG_ADJ_PRC` is `0` for 수정주가 and
`1` for 원주가, and KIS's own published sample defaults to raw — under
which 삼성전자's 2018 50:1 split shows as a −98% day. There is no default
here; a caller states which series it wants.

Credentials come from `KIS_APP_KEY` / `KIS_APP_SECRET` and are never
logged. This module places no orders and imports nothing that can;
`tests/test_kis_probe_cannot_trade.py` enforces both.

    python -m data.backfill_kis --symbols 005930,000660 --index 0001 \
        --start 2019-01-01 --end 2026-09-01 --adjusted 0
    python -m data.backfill_kis --verify --symbols 005930 --index 0001 \
        --start 2019-01-01 --end 2026-09-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

from data.kis_klines import (
    ADJUSTED,
    RAW,
    KisKlinesError,
    KisSession,
    PAPER_HOST,
    ReferenceCalendarError,
    default_window_days,
    equity_storage_symbol,
    index_storage_symbol,
    iter_daily_range,
    missing_trading_days,
    ms_to_trading_date,
    trading_date_to_ms,
)
from data.store import connect, fetch_klines, upsert_klines

LOGGER = logging.getLogger(__name__)

INTERVAL = "1d"
# Canonical, resolved from the module rather than the working
# directory -- see data/_paths.py for the second database the two
# old relative defaults silently created.
from data._paths import DEFAULT_DB_PATH


def _stored_days(conn, storage_symbol: str, start: str, end: str) -> set[int]:
    """Trading days already in the store, as open-time milliseconds."""
    rows = fetch_klines(
        conn,
        storage_symbol,
        INTERVAL,
        trading_date_to_ms(start),
        trading_date_to_ms(end) + 86_400_000,  # end is inclusive here
    )
    return {r.open_time_ms for r in rows}


def sync_symbol(
    session: KisSession,
    conn,
    code: str,
    start: str,
    end: str,
    *,
    adjusted: str,
    is_index: bool = False,
    reference_days: set[int] | None = None,
) -> int:
    """Fetch and store `[start, end]` for one symbol. Returns rows inserted.

    Windows whose expected trading days are already stored are skipped
    rather than refetched. "Expected" comes from `reference_days` when a
    reference is supplied; without one — which is the case for the
    reference index itself — every window is fetched, since there is
    nothing yet to say which days should exist.
    """
    storage = index_storage_symbol(code) if is_index else equity_storage_symbol(code)
    have = _stored_days(conn, storage, start, end)
    window = default_window_days(is_index=is_index)

    first = dt.date(int(start[:4]), int(start[4:6]), int(start[6:]))
    last = dt.date(int(end[:4]), int(end[4:6]), int(end[6:]))
    inserted = 0
    cursor = first
    while cursor <= last:
        w_end = min(cursor + dt.timedelta(days=window - 1), last)
        if reference_days is not None:
            lo, hi = trading_date_to_ms(cursor.strftime("%Y%m%d")), trading_date_to_ms(
                w_end.strftime("%Y%m%d")
            )
            expected = {d for d in reference_days if lo <= d <= hi}
            if expected and expected <= have:
                cursor = w_end + dt.timedelta(days=1)
                continue
        rows = list(
            iter_daily_range(
                session,
                code,
                cursor.strftime("%Y%m%d"),
                w_end.strftime("%Y%m%d"),
                adjusted=adjusted,
                is_index=is_index,
                window_days=window,
            )
        )
        if rows:
            inserted += upsert_klines(conn, storage, INTERVAL, rows)
            have.update(r.open_time_ms for r in rows)
        cursor = w_end + dt.timedelta(days=1)
    LOGGER.info("%s: %d rows inserted, %d stored in range", storage, inserted, len(have))
    return inserted


def verify_symbol(conn, code: str, reference_days: set[int], start: str, end: str) -> list[int]:
    """Trading days the reference has and this symbol does not.

    Raises `ReferenceCalendarError` if the symbol has bars the reference
    lacks — that is proof the *reference* is incomplete, not a fact about
    the symbol, and every "no gaps" verdict against it would be worthless.
    """
    have = _stored_days(conn, equity_storage_symbol(code), start, end)
    return missing_trading_days(reference_days, have)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", required=True, help="comma-separated 6-digit equity codes")
    p.add_argument("--index", required=True, help="reference index code, e.g. 0001 (KOSPI)")
    p.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    p.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive")
    p.add_argument(
        "--adjusted",
        choices=(ADJUSTED, RAW),
        help="0 = 수정주가, 1 = 원주가. Required unless --verify; no default, "
        "because KIS's own sample defaults to raw and a raw series shows "
        "삼성전자's 2018 split as a -98%% day.",
    )
    p.add_argument("--db-path", default=DEFAULT_DB_PATH)
    p.add_argument("--host", default=PAPER_HOST)
    p.add_argument(
        "--verify",
        action="store_true",
        help="check stored coverage against the reference index; fetch nothing",
    )
    args = p.parse_args(argv)
    if not args.verify and args.adjusted is None:
        p.error("--adjusted is required unless --verify")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    if not codes:
        LOGGER.error("--symbols listed no codes")
        return 2
    start, end = args.start.replace("-", ""), args.end.replace("-", "")

    conn = connect(args.db_path)
    try:
        if args.verify:
            reference = _stored_days(conn, index_storage_symbol(args.index), start, end)
            if not reference:
                LOGGER.error(
                    "reference index %s has no stored bars in range -- backfill it first; "
                    "coverage cannot be judged against an empty calendar",
                    args.index,
                )
                return 1
            LOGGER.info("reference %s: %d trading days", args.index, len(reference))
            bad = 0
            for code in codes:
                try:
                    missing = verify_symbol(conn, code, reference, start, end)
                except ReferenceCalendarError as exc:
                    LOGGER.error("%s: %s", code, exc)
                    return 1
                if missing:
                    bad += 1
                    LOGGER.error(
                        "%s: %d missing trading days, e.g. %s",
                        code,
                        len(missing),
                        [ms_to_trading_date(m) for m in missing[:5]],
                    )
                else:
                    LOGGER.info("%s: complete", code)
            # Fail closed: a verification that always exits 0 lets automation
            # read an incomplete series as a verified one.
            return 0 if bad == 0 else 1

        key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
        if not key or not secret:
            LOGGER.error("KIS_APP_KEY / KIS_APP_SECRET are not set")
            return 2
        session = KisSession(key, secret, host=args.host)

        # The index first, and on its own terms -- it becomes the calendar
        # every equity is then judged against, so a truncated one would
        # silently make every symbol look complete.
        sync_symbol(session, conn, args.index, start, end, adjusted=ADJUSTED, is_index=True)
        reference = _stored_days(conn, index_storage_symbol(args.index), start, end)
        if not reference:
            LOGGER.error("reference index %s returned no bars; refusing to continue", args.index)
            return 1
        LOGGER.info("reference calendar: %d trading days", len(reference))

        for code in codes:
            sync_symbol(
                session,
                conn,
                code,
                start,
                end,
                adjusted=args.adjusted,
                reference_days=reference,
            )
        return 0
    except KisKlinesError as exc:
        LOGGER.error("%s", exc)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
