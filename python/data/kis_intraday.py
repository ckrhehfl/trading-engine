"""KRX minute bars from KIS `inquire-time-dailychartprice` (`FHKST03010230`).

**This is the only endpoint KIS offers that serves a *past* session's
minute bars, and its history is a rolling ~250 trading days that advances
one session every session.** `inquire-time-itemchartprice`
(`FHKST03010200`) takes no date at all and can only ever describe the
current session. So intraday history that is not collected is not merely
inconvenient to obtain later -- it is **gone**, permanently, one day at a
time. That is the whole reason this module exists and why it was built
before anything that would consume it.

Full probe record: `.planning/rd-c-kis-flow-probe-result.md` §3.2.

## How a session is fetched: four calls, and the fourth is mostly waste

The endpoint returns the **120 bars ending at `FID_INPUT_HOUR_1`**, so a
session is tiled backwards. Measured on 2026-09-15 against the live paper
host for 005930 on 2026-09-11:

    153000 -> 13:21..15:30   120 rows, all of the requested date
    132000 -> 11:21..13:20   120 rows, all of the requested date
    112000 -> 09:21..11:20   120 rows, all of the requested date
    092000 -> 09:00..09:20    21 rows of the requested date, 99 from the day before

**381 bars for a full KRX session**, and the last page necessarily
overruns into the previous session because 09:00-09:20 is only 21 minutes
long. Those overrun rows are discarded here rather than kept: they belong
to a session this call was not asked about, and silently folding them in
would make "which sessions have been fetched" unanswerable.

Three pages would be cheaper and **wrong** -- 153000/132000/112000 leaves
09:00-09:20 missing, which looks like an ordinary quiet open rather than a
hole.

## Four traps, each measured rather than assumed

1. **An empty result is `rt_cd=0`, not an error.** A date outside the
   rolling window returns 정상처리 with zero rows -- the same convention
   expired futures contracts return. A backfill treating `rt_cd=0` as
   success records nothing and reports a clean run, so the caller must
   check coverage against a real trading calendar. `fetch_session` returns
   an empty list and says nothing about why; `backfill_kis_intraday` is
   where that is adjudicated.
2. **`stck_cntg_hour` is not reliably on the minute grid.** 2026-01-02
   returned `132011` … `152911` -- seconds `:11` -- where every other
   probed date returned `:00`. The stamp is therefore parsed as literal
   `HHMMSS` and stored as given; **no grid alignment is asserted**, for
   the same reason the funding endpoint's range validation does not.
3. **`acml_tr_pbmn` here is CUMULATIVE, not per-bar.** It runs
   2,373,467,841,750 at 13:21 to 3,602,177,532,500 at 15:30 on one
   session. `KlineRow.quote_volume` is documented as *the bar's* traded
   value, so this column is **not** stored -- writing a running total into
   a per-bar field is the kind of silent lie that survives every test. It
   is recoverable by differencing consecutive bars of a **complete**
   session; on a partial one the difference across the hole is wrong and
   looks entirely plausible, which is why it is not done here.
4. **`output1` describes *now*, not the requested date.** On a 2026-09-15
   call for 2026-09-11 it reported `stck_prpr` 250,500 against that
   session's real 15:30 close of 259,500. Only `output2` is read.

## A fifth thing, not a trap but a sizing fact

**A minute with no trade has no bar.** 007390 (네이처셀) returned its 120
rows spanning 13:15-15:30 where 005930's spanned 13:21-15:30 — 16 minutes
of that window simply had no trades. So a session's bar count is a
function of liquidity and **380 is an upper bound, not an expectation**.
Any completeness check that assumes a fixed count will be wrong for the
illiquid half of a universe.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import time
import urllib.parse
from decimal import Decimal, InvalidOperation
from typing import Any

from data.bingx_klines import KlineRow
from data.kis_klines import (
    INTER_REQUEST_DELAY_S,
    KisKlinesError,
    KisSession,
    _get_with_retry,
    equity_storage_symbol,
)
from data.store import upsert_klines

LOGGER = logging.getLogger(__name__)

MINUTE_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
TR_MINUTE_DAILY = "FHKST03010230"

INTERVAL = "1m"
MS_PER_MINUTE = 60_000

#: KRX trades in KST and KIS reports in it. A KRX trading date maps
#: exactly onto UTC midnight (09:00 KST = 00:00 UTC), which is why the
#: daily path needs no special case -- but a *minute* stamp does, and this
#: is the offset it needs.
KST = dt.timezone(dt.timedelta(hours=9))

ROWS_PER_CALL = 120

#: The four `FID_INPUT_HOUR_1` values that tile one regular KRX session
#: (09:00-15:30), newest first. Derived from the measured 120-rows-ending-
#: at-the-hour behaviour, not from the session length: each page must
#: start where the previous one ended, and the last necessarily overruns.
SESSION_PAGES = ("153000", "132000", "112000", "092000")


#: A session is treated as already collected when the stored bars reach
#: from at or before this time to at or after the other.
#:
#: **Not a bar count.** 380 is an upper bound that only a liquid name
#: reaches, so "we have 380 rows" would refetch every illiquid symbol
#: forever. Spanning the session is what actually distinguishes "all four
#: pages landed" from "the run died halfway", and it tolerates a name
#: whose first trade is late or whose last is early.
COMPLETE_FROM = "093000"
COMPLETE_TO = "150000"


def timestamp_ms(date: str, hhmmss: str) -> int:
    """`(YYYYMMDD, HHMMSS)` in KST to epoch milliseconds.

    Parsed as a literal wall-clock stamp with **no grid assertion** --
    2026-01-02 really did return `:11` seconds on every bar, and a
    minute-alignment check copied from the daily path would reject real
    data. Trap 2 in this module's docstring.
    """
    if len(date) != 8 or not date.isdigit():
        raise KisKlinesError(f"expected YYYYMMDD, got {date!r}")
    if len(hhmmss) != 6 or not hhmmss.isdigit():
        raise KisKlinesError(f"expected HHMMSS, got {hhmmss!r}")
    try:
        stamp = dt.datetime(
            int(date[:4]), int(date[4:6]), int(date[6:]),
            int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:]),
            tzinfo=KST,
        )
    except ValueError as exc:
        raise KisKlinesError(f"{date} {hhmmss} is not a real instant: {exc}") from exc
    return int(stamp.timestamp() * 1000)


def _decimal(raw: object, field: str, where: str) -> Decimal:
    if raw is None or raw == "":
        raise KisKlinesError(f"{where}: {field} is missing")
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise KisKlinesError(f"{where}: {field} is not a number: {raw!r}") from exc


def parse_row(row: dict[str, Any], date: str) -> KlineRow:
    """One `output2` entry to a `KlineRow`.

    Fails closed on a missing or unparseable field rather than
    substituting a zero, on the same reasoning as the daily path: KIS
    returns per-endpoint field casing, and a `.get()` that quietly yields
    `None` is exactly how this project's KIS integration got three
    endpoints wrong once already.

    `quote_volume` is deliberately **not** populated -- see trap 3.
    """
    hour = str(row.get("stck_cntg_hour") or "")
    where = f"{date} {hour or '?'}"
    if not hour:
        raise KisKlinesError(f"{date}: a row arrived with no stck_cntg_hour")
    return KlineRow(
        open_time_ms=timestamp_ms(date, hour),
        open=_decimal(row.get("stck_oprc"), "stck_oprc", where),
        high=_decimal(row.get("stck_hgpr"), "stck_hgpr", where),
        low=_decimal(row.get("stck_lwpr"), "stck_lwpr", where),
        close=_decimal(row.get("stck_prpr"), "stck_prpr", where),
        volume=_decimal(row.get("cntg_vol"), "cntg_vol", where),
    )


def fetch_page(session: KisSession, code: str, date: str, hour: str) -> list[dict[str, Any]]:
    """The raw `output2` rows for one call, **filtered to `date`**.

    The filter is the fix for the fourth page overrunning into the
    previous session. It is applied here rather than by the caller so that
    no path can accidentally keep those rows: they are real bars, correctly
    stamped, and would be silently written under a session nobody asked
    about -- which is how "which sessions have I fetched" stops having an
    answer.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": date,
        "FID_INPUT_HOUR_1": hour,
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
    }
    url = f"{session.host}{MINUTE_DAILY_PATH}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(TR_MINUTE_DAILY))

    if payload.get("rt_cd") != "0":
        raise KisKlinesError(
            f"KIS rejected {code} {date} {hour}: rt_cd={payload.get('rt_cd')} "
            f"msg_cd={payload.get('msg_cd')}"
        )

    raw = payload.get("output2") or []
    if not isinstance(raw, list):
        raise KisKlinesError(f"output2 is not a list for {code} {date} {hour}")
    return [
        r for r in raw
        if isinstance(r, dict) and str(r.get("stck_bsop_date") or "") == date
    ]


def fetch_session(
    session: KisSession,
    code: str,
    date: str,
    *,
    delay_s: float = INTER_REQUEST_DELAY_S,
) -> list[KlineRow]:
    """Every minute bar of one KRX session, via the four-page tiling.

    Returns `[]` for a date outside the rolling window, because that is
    what the endpoint says: `rt_cd=0` with zero rows. **The emptiness is
    returned, not interpreted** -- whether it means "not a trading day",
    "older than ~250 sessions" or "something is broken" is a question only
    a trading calendar can answer, and that belongs to the backfill.
    """
    by_stamp: dict[int, KlineRow] = {}
    for index, hour in enumerate(SESSION_PAGES):
        if index and delay_s:
            time.sleep(delay_s)
        for raw in fetch_page(session, code, date, hour):
            bar = parse_row(raw, date)
            # Pages overlap by construction at their boundaries; last write
            # wins and they agree, since it is the same bar from the same
            # session.
            by_stamp[bar.open_time_ms] = bar
    return [by_stamp[k] for k in sorted(by_stamp)]


def stored_span(
    conn: sqlite3.Connection, storage_symbol: str, date: str
) -> tuple[int, str | None, str | None]:
    """`(bar count, earliest HHMMSS, latest HHMMSS)` already in the store."""
    lo = timestamp_ms(date, "000000")
    hi = lo + 24 * 60 * MS_PER_MINUTE
    row = conn.execute(
        "SELECT COUNT(*), MIN(open_time_ms), MAX(open_time_ms) FROM klines "
        "WHERE symbol=? AND interval=? AND open_time_ms>=? AND open_time_ms<?",
        (storage_symbol, INTERVAL, lo, hi),
    ).fetchone()
    count = int(row[0] or 0)
    if not count:
        return 0, None, None
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, KST).strftime("%H%M%S")  # noqa: E731
    return count, fmt(row[1]), fmt(row[2])


def session_is_collected(
    conn: sqlite3.Connection, storage_symbol: str, date: str
) -> bool:
    """Whether this session's bars already span the trading day.

    See `COMPLETE_FROM`/`COMPLETE_TO` for why this is a span and not a
    count.
    """
    _, first, last = stored_span(conn, storage_symbol, date)
    if first is None or last is None:
        return False
    return first <= COMPLETE_FROM and last >= COMPLETE_TO


def sync_session(
    conn: sqlite3.Connection,
    session: KisSession,
    code: str,
    date: str,
    *,
    delay_s: float = INTER_REQUEST_DELAY_S,
    force: bool = False,
) -> tuple[int, int]:
    """Fetch and store one session. `(fetched, newly inserted)`.

    Skips a session already spanning the day unless `force`, which is what
    makes a multi-hour backfill resumable after an interruption without
    re-spending thousands of calls.
    """
    symbol = equity_storage_symbol(code)
    if not force and session_is_collected(conn, symbol, date):
        return 0, 0
    bars = fetch_session(session, code, date, delay_s=delay_s)
    if not bars:
        return 0, 0
    inserted = upsert_klines(conn, symbol, INTERVAL, bars)
    return len(bars), inserted
