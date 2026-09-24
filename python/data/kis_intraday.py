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

## How a session is fetched: page backwards until the date runs out

The endpoint returns the **120 bars ending at `FID_INPUT_HOUR_1`**, so a
session is read backwards from above its close. Measured on 2026-09-15
against the live paper host for 005930 on 2026-09-11:

    153000 -> 13:21..15:30   120 rows, all of the requested date
    132000 -> 11:21..13:20   120 rows, all of the requested date
    112000 -> 09:21..11:20   120 rows, all of the requested date
    092000 -> 09:00..09:20    21 rows of the requested date, 99 from the day before

**381 bars for an ordinary session.** The last page necessarily overruns
into the previous session, and those rows are discarded rather than kept:
they belong to a session this call was not asked about, and folding them
in would make "which sessions have been fetched" unanswerable.

**The hour boundaries are computed from what came back, not fixed**, and
that is a correction rather than a refinement. A fixed
`153000/132000/112000/092000` tiling assumes every session runs
09:00-15:30, and **KRX sessions do not**:

| session | why | real span |
|---|---|---|
| 2025-11-13 | 수능, the national exam | **09:59 -> 16:29** |
| 2026-01-02 | the year's first trading day | 09:59 -> 15:29 |
| an ordinary day | | 09:00 -> 15:30 |

The fixed tiling dropped **60 bars off the end of 2025-11-13** and stored
a session that looked like an ordinary one which happened to finish early
-- the exact shape of silent loss this module exists to prevent.

So paging starts above any close (`SESSION_END_PROBE`) and each request
after the first ends one minute before the earliest row the previous one
returned, until a page yields nothing new for the date. Asking for a later
hour than the market reached costs nothing: an ordinary session asked at
16:30 still returns rows ending 15:30.

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
function of liquidity and **381 is an upper bound, not an expectation**:
005930 gives 381 a session, 007390 gives 351. Any completeness check that
assumes a fixed count will be wrong for the illiquid half of a universe.

(381, not 380: the four pages contribute 120 + 120 + 120 + 21 for the
requested date. An earlier draft carried 380 from the *minute* arithmetic
-- 390 minutes less a ~10-minute closing auction -- and that figure is
contradicted by this module's own first measurement.)
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
    ADJUSTED,
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

#: Where backwards paging starts: above any KRX close.
#:
#: **Not 15:30.** KRX runs late-open sessions -- 수능일 and the year's
#: first trading day open at 10:00 -- and at least 수능일 also closes late.
#: Probed directly: **2025-11-13 traded 09:59 -> 16:29**, so a tiling
#: anchored at 15:30 silently dropped 60 bars off the end of it and the
#: stored session looked like an ordinary one that finished early.
SESSION_END_PROBE = "163000"

#: A hard stop on backwards paging. A late-open, late-close KRX session is
#: ~390 bars, so reaching this many pages means the loop is not
#: terminating and the run should fail rather than spin.
MAX_PAGES_PER_SESSION = 8


#: A session is treated as already collected when the stored bars reach
#: from at or before this time to at or after the other.
#:
#: **Not a bar count.** 381 is an upper bound that only a liquid name
#: reaches, so "we have 381 rows" would refetch every illiquid symbol
#: forever. Spanning the session is what actually distinguishes "all four
#: pages landed" from "the run died halfway", and it tolerates a name
#: whose first trade is late or whose last is early.
#: Tolerant of a **late open**: 수능일 and the year's first trading day
#: begin at 10:00, so a 09:30 floor would mark every such session
#: incomplete forever and refetch it on every run.
#:
#: Checking the *start* is what matters, because paging runs backwards —
#: an interrupted fetch loses the morning, never the close.
COMPLETE_FROM = "100500"
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

    # **No `or []`.** That would turn a missing key, a `None` or a `{}` into
    # a clean empty page, and an empty page is indistinguishable from a
    # legitimately out-of-range date. Combined with span-based
    # completeness, a page silently lost this way leaves a hole the session
    # is never refetched to fill.
    #
    # Measured before removing it, because the out-of-range path depends on
    # the answer: 2025-09-02 (outside the rolling window) returns
    # `rt_cd=0` with `output2` present and equal to `[]`. The key is always
    # there, so its absence is genuinely anomalous.
    raw = payload.get("output2")
    if not isinstance(raw, list):
        raise KisKlinesError(
            f"output2 is {type(raw).__name__}, not a list, for {code} {date} {hour}"
        )

    # **A malformed row fails the whole page rather than being filtered
    # out.** Dropping it silently loses a real minute, and completeness
    # here is a *span* rather than a count -- so a hole in the middle of a
    # session still spans the day, still counts as collected, and is never
    # fetched again. Filtering is safe only where the thing filtered is
    # known not to matter, and that is true of the previous session's rows
    # below and not of a row that should have parsed.
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise KisKlinesError(
                f"output2 row {index} is not an object for {code} {date} {hour}"
            )
        row_date = str(row.get("stck_bsop_date") or "").strip()
        # **A row carrying no date at all is malformed, not "the previous
        # session".** The comment above already argued that a malformed row
        # must fail the page, and the non-dict branch honours it -- but a
        # dict with a blank or missing `stck_bsop_date` failed the `== date`
        # comparison and was dropped down the previous-session path, which
        # is the one branch here that is allowed to discard a row silently.
        # Completeness on this series is a **span**, so the lost minute
        # still sits inside a day that counts as collected and is never
        # requested again -- and the endpoint's rolling ~250 sessions
        # eventually carry it off for good. The only rows that may be
        # dropped are ones that positively identify themselves as a
        # different session.
        if not row_date:
            raise KisKlinesError(
                f"output2 row {index} carries no stck_bsop_date for {code} "
                f"{date} {hour}; dropping it would leave a minute-shaped hole "
                f"inside a session that still spans the day and so would "
                f"never be fetched again"
            )
        if row_date == date:
            rows.append(row)
    return rows


def _one_minute_before(hhmmss: str) -> str | None:
    """`HHMMSS` one minute earlier, or `None` at the start of the day.

    Seconds are carried through rather than zeroed: a session whose stamps
    carry `:11` (2026-01-02 did) must have its next request end at `:11`
    too, or the boundary bar is requested twice and the walk advances by
    59 seconds instead of a minute.
    """
    if len(hhmmss) != 6 or not hhmmss.isdigit():
        raise KisKlinesError(f"expected HHMMSS, got {hhmmss!r}")
    total = int(hhmmss[:2]) * 60 + int(hhmmss[2:4]) - 1
    if total < 0:
        return None
    return f"{total // 60:02d}{total % 60:02d}{hhmmss[4:]}"


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
    hour = SESSION_END_PROBE
    for page in range(MAX_PAGES_PER_SESSION):
        if page and delay_s:
            time.sleep(delay_s)
        rows = fetch_page(session, code, date, hour)
        if not rows:
            # Either the date is outside the rolling window (first page) or
            # the session's open has been passed (later pages). Both mean
            # there is nothing further back to ask for.
            break
        before = len(by_stamp)
        for raw in rows:
            bar = parse_row(raw, date)
            # Pages meet at a boundary minute, so an overlap is normal;
            # last write wins and they agree, being the same bar.
            by_stamp[bar.open_time_ms] = bar
        if len(by_stamp) == before:
            # The page returned only bars already held, so the walk is not
            # advancing. Stopping here rather than on the hour arithmetic
            # means a duplicate page cannot spin the loop.
            break
        earliest = min(r["stck_cntg_hour"] for r in rows)
        hour = _one_minute_before(earliest)
        if hour is None:
            break
    else:
        raise KisKlinesError(
            f"{code} {date}: still paging after {MAX_PAGES_PER_SESSION} requests "
            f"({len(by_stamp)} bars). A KRX session is ~390 bars at most, so the "
            f"walk is not terminating."
        )
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
    symbol = equity_storage_symbol(code, adjusted=ADJUSTED)
    if not force and session_is_collected(conn, symbol, date):
        return 0, 0
    bars = fetch_session(session, code, date, delay_s=delay_s)
    if not bars:
        return 0, 0
    inserted = upsert_klines(conn, symbol, INTERVAL, bars)
    return len(bars), inserted
