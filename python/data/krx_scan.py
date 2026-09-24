"""Daily bars for the whole survivorship-safe KRX common-stock universe.

**What this is for.** `rd-c` §2's finding is that *the filter is the
strategy*: a per-day selection rule needs a pool to select from on every
day, and `rd-x` showed that ranking a pool on a date inside the window is
worth +20%/yr of pure artefact. A per-day rule is the honest version of
that, and it cannot be run without the whole pool's history.

**The cost, stated before anything starts, because it decides how this is
built.** ~4,400 candidates over 2019-01-02..2026-09-18 is ~24 pages each
and **~105,000 real API calls**. (The first pass ran against 4,643, before
the SPAC and instrument-class rules narrowed both sides of the pool; a
resumed pass simply fetches fewer, since coverage is read back from the
database.) At the throughput measured in `rd-x` --
0.5-0.7/s after the KRX close, 0.3/s during the session -- that is **two
to three days of continuous fetching**. Every property below follows from
that number rather than from taste:

- **Resumable.** A pass this long WILL be interrupted. Coverage is read
  back from the database, so a re-run fetches only what is missing. `rd-x`
  lost two and a half hours to one `KisKlinesError` before the ranking was
  made resumable; this is twenty times that pass.
- **A failure does not abort the run.** That is the opposite of
  `krx_dayone_drift`, deliberately: there, a missing name made the
  measurement wrong, so it refused. Here partial progress is the whole
  point, and what protects the reader is the **coverage report** -- which
  symbols have what, stated rather than assumed.
- **It refuses to run during the KRX continuous session** unless told
  otherwise. **The instance's collectors share this app key**, they
  collect series that cannot be backfilled, and a multi-day scan measured
  throughput dropping to 0.3/s during the session -- contention that
  costs the collectors more than it costs this.

**It does NOT write the KRX record.** CLAUDE.md's "exactly one writer per
series" makes the instance the only writer of `KRX:` klines, and
`sync-krx-from-instance.sh` is `INSERT OR IGNORE`, so a locally-written
row would never be corrected by a later sync. This writes a separate
research database. Promoting the scan to the record is an operator
decision about running it on the instance, not something a research
module may do by writing there.

**Three traps named before the scan meets them**, all measured:

1. **A zero-row answer is not "not listed"** -- a dead name and a code
   that never existed answer identically. Negative controls run first and
   the whole scan refuses if they do not answer empty.
2. **The 100-row cap truncates silently and keeps the NEWEST rows**, so
   pages are sized under it and a capped page is a failure, not data.
3. **KIS prints a bar for every session a HALTED name is listed**, with
   `O==H==L==C` and zero turnover -- 신라젠 carries 604 consecutive such
   sessions. They are stored (they are what the tape said) and **counted
   in the coverage report**, because a bar is not evidence the name was
   tradeable.

**Discovery mode.** KRX daily was spent by `ms-f` on 2026-09-13, so
nothing measured on this data may be promoted or quoted as evidence of an
edge.

Run:

    python -m data.krx_scan --scan          # resumable; refuses in-session
    python -m data.krx_scan --coverage
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import os
import sqlite3
import sys
import time
import urllib.parse
from enum import Enum
from pathlib import Path

from data.kis_klines import (
    ADJUSTED,
    EQUITY_ROWS_PER_CALL_CAP,
    DAILY_ITEM_PATH,
    PAPER_HOST,
    TR_DAILY_ITEM,
    KisKlinesError,
    KisSession,
    _get_with_retry,
    rows_per_call_cap,
    validated_output2,
)
from data.krx_instrument import has_plain_equity_code, is_common_stock_issue
from data.store import connect, fetch_krx_delisted, fetch_krx_universe

PANEL_START = "20190102"
PANEL_END = "20260918"

#: Calendar days per request. ~82 trading days, comfortably under the
#: equity endpoint's silent 100-row cap.
PAGE_DAYS = 120

NEGATIVE_CONTROLS = ("999999", "ZZZZZZ", "000000")

#: The wide probe's **positive** control. 삼성전자 is served back to
#: 1991-08-28, which is KIS's own floor rather than a listing date, so an
#: empty answer for it cannot mean "this name has no bars".
#:
#: A negative control alone is the wrong shape for `_wide_probe`. It proves a
#: nonsense code answers empty on a NARROW `_page` request, which is a
#: different request: different date range, and the probe deliberately asks
#: `19900101`..today. If KIS ever answers a wide request with `rt_cd=0` and an
#: empty `output2` for every code -- a range limit, a date-format change, an
#: `FID_ORG_ADJ_PRC` interaction -- the negative control still passes, because
#: empty is what it expects, and every `absent:unknown` is then recorded
#: `NEVER_SERVED`. That removes real names from the pool as "KIS does not
#: price this", which is the survivorship direction this universe exists to
#: remove. Caught on review.
WIDE_PROBE_POSITIVE_CONTROL = "005930"

#: KRX's continuous session in KST. Outside it the endpoint is not
#: competing with the instance's collectors.
SESSION_OPEN_KST = dt.time(9, 0)
SESSION_CLOSE_KST = dt.time(15, 30)
KST = dt.timezone(dt.timedelta(hours=9))

DEFAULT_SCAN_DB = Path(
    os.environ.get("TMPDIR", "/tmp")
) / "krx_scan.sqlite3"

SCAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_bars (
  code TEXT NOT NULL,
  bsop_date TEXT NOT NULL,
  open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, turnover TEXT,
  PRIMARY KEY (code, bsop_date)
);
CREATE TABLE IF NOT EXISTS scan_progress (
  code TEXT PRIMARY KEY,
  first_date TEXT,
  last_date TEXT,
  bars INTEGER NOT NULL,
  frozen INTEGER NOT NULL,
  status TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
"""

_SPACING_S = 0.05
#: How often a paused scan re-checks whether the session has closed.
_SESSION_POLL_S = 300.0


class KrxScanError(RuntimeError):
    """The scan could not be trusted, so it was refused."""


def in_continuous_session(now: dt.datetime | None = None) -> bool:
    """True during KRX's continuous session, in KST, on a weekday.

    Holidays are not resolved -- deliberately. Getting this wrong in the
    permissive direction costs the shared collectors; getting it wrong in
    the restrictive direction costs this scan a few hours, and that is the
    cheaper error. `KrxMarketCalendar`'s own lunar-holiday gap is why a
    calendar is not consulted here.
    """
    now = now or dt.datetime.now(KST)
    if now.weekday() >= 5:
        return False
    return SESSION_OPEN_KST <= now.timetz().replace(tzinfo=None) <= SESSION_CLOSE_KST


class Listing(Enum):
    """Where a pool member stands, from the two sources that disagree.

    Two-valued `is_live` could not express the third case, and the third
    case is real: **four codes appear on KIS's live master AND on KRX's
    delisted finder, with the same name, market and ISIN on both**
    (카프로 `006380`, 원풍물산 `008290`, 코다코 `046070`, 코스나인 `082660`
    -- measured 2026-09-24 against the 2026-09-23 snapshots). The most
    likely reading is a 상장폐지 already published by the finder while the
    name still trades.

    **This does not contradict `rd-w`'s "zero overlap" measurement**, which
    compared the finder against KRX's own `finder_stkisu`. `krx_universe`
    reads the **KIS master files** instead, and those two listed sources
    disagree about these four names. Both measurements stand; they are
    about different sources.

    The flag's only job is to interpret a missing bar, so a contradictory
    value is not a cosmetic duplicate -- `LIVE` says an absence is UNKNOWN
    and `DELISTED` says it is an exit. `LEAVING` says the series may stop at
    any session and must still never be dropped from the pool.
    """

    LIVE = "live"
    DELISTED = "delisted"
    LEAVING = "delisting_announced"


class Absence(Enum):
    """Why a symbol produced no bars. **`absent` conflated all three.**

    A symbol-day with no bar must resolve to UNKNOWN rather than to "not
    listed" until the request itself is known well-formed, because dropping
    a real name from the pool is precisely the survivorship bias the
    delisted universe exists to remove. A single `absent` bucket cannot say
    which of these it holds, so a 1,524-symbol bucket was unreadable.
    """

    #: KIS serves no bar in any window, and a nonsense-code negative
    #: control in the same run answered empty too -- so the request shape
    #: was fine and this code really is one KIS does not price.
    NEVER_SERVED = "absent:never_served"
    #: Bars exist; none fall inside the requested panel. A later listing,
    #: normally.
    OUTSIDE_WINDOW = "absent:outside_window"
    #: Cannot tell the two apart. **The default**, and what every first-pass
    #: `absent` becomes on read, because `rt_cd=0` with zero rows is the
    #: same answer for a dead name, an out-of-range window and a code that
    #: never existed.
    UNKNOWN = "absent:unknown"


class Failure(Enum):
    """Why a symbol failed, at the granularity a retry decision needs.

    `failed:KisKlinesError` was recorded for 193 of the first pass's 199
    failures, which is the exception *type* and not the *kind*: it covers a
    transient venue rejection (retry), a row-cap breach (a bug in the
    paging, never retry blindly) and a parse refusal (the response shape
    changed) alike. A second pass cannot decide what to do with one bucket.
    """

    #: `rt_cd != 0` after the bounded rejection retry -- the transient case.
    REJECTED = "failed:rejected"
    #: A page reached the endpoint's silent row cap. Paging is wrong, or the
    #: name trades far denser than the window assumed. Not transient.
    CAPPED = "failed:capped"
    #: `output2` or a row inside it did not have the shape this module
    #: requires. A venue-side change, not a transient.
    MALFORMED = "failed:malformed"
    #: Transport: a timeout, a reset, a truncated read.
    TRANSPORT = "failed:transport"
    #: Anything else, carrying the exception type as before.
    OTHER = "failed:other"


def failure_detail(exc: BaseException) -> str:
    """The status string a failure is recorded as, for **either** pass.

    `scan()` and `resolve_absences()` each wrote this themselves and only one
    of them appended the exception type, so a probe-path `OTHER` lost the only
    clue it had -- and `OTHER` is precisely the kind that is never retried, so
    manual diagnosis is all it gets. One formatter, on the same reasoning as
    the pool predicate and the page contract.
    """
    kind = classify_failure(exc)
    if kind is Failure.OTHER:
        return f"{kind.value}:{type(exc).__name__}"
    return kind.value


#: Which failures a second pass may retry. An allowlist, not a blocklist --
#: a blocklist bets on having thought of every kind, and this project has
#: already recorded that bet losing (`change_check.check_guard_is_an_allowlist`).
RETRYABLE_FAILURES = frozenset({Failure.REJECTED, Failure.TRANSPORT})


def classify_failure(exc: BaseException) -> Failure:
    """An exception to the kind a retry decision can be made from.

    Matched on the refusal text this package itself writes, which is why
    `validated_output2`'s messages are stable strings rather than free
    prose. A message change that breaks this shows up as `OTHER`, which is
    the safe direction: `OTHER` is not retried.
    """
    # `http.client.HTTPException` covers `IncompleteRead`, which is not an
    # `OSError` and so fell through to the type-name bucket -- 6 of the
    # first pass's 199 failures were literally a truncated read, the most
    # retryable thing there is. Making `_get_with_retry` itself retry it is
    # the deeper fix and is deliberately not bundled here, because that
    # function is being changed in the page-read contract PR.
    if isinstance(exc, (TimeoutError, OSError, http.client.HTTPException)):
        return Failure.TRANSPORT
    text = str(exc)
    # **One spelling, and a test that produces it rather than quotes it.**
    # This matched only `kis_klines.validated_output2`'s wording while
    # `_page` -- the function `scan()` calls -- raised its own "rows at the
    # 100-row cap", so a cap breach filed as `OTHER` and the scan path could
    # never record `CAPPED`. Reported on review.
    #
    # The second spelling was added here as a fix and is deliberately gone
    # again: routing `_page` through the shared contract left it with no
    # caller, and a branch nothing can reach is a guard that silently does
    # nothing. What protects this instead is
    # `test_the_cap_refusal_this_MODULE_raises_classifies_as_CAPPED`, which
    # obtains the message by TRIGGERING the refusal. Quoting a message is
    # what let the mismatch through -- it confirmed my own wording instead
    # of the caller's -- so a future wording drift fails that test rather
    # than passing an unreachable `or`.
    if "at or over this endpoint" in text:
        return Failure.CAPPED
    if "request failed after" in text:
        return Failure.TRANSPORT
    if "KIS rejected the request" in text or "rt_cd=" in text:
        return Failure.REJECTED
    if (
        "output2" in text
        or "stck_bsop_date" in text
        or "not an object" in text
        or "not a list" in text
    ):
        return Failure.MALFORMED
    return Failure.OTHER


def candidates(conn) -> list[tuple[str, str, Listing]]:
    """Every survivorship-safe common-stock code, once each.

    **The two sides apply the SAME four filters**, which they did not when
    the SPAC rule landed: `fetch_krx_universe(common_stock_only=True)`
    picked it up on the live side immediately, leaving the delisted side
    with its 178 SPACs still in. Half a filter is worse than none, because
    the pool then looks filtered. Both now call the one predicate,
    `krx_instrument.is_common_stock_issue`.

    **And the two sides overlap, which a concatenation could not express.**
    `live + dead` returned 4,375 rows over 4,371 distinct codes, with four
    codes carrying `is_live=True` on one row and `False` on the other --
    contradictory, because that flag is what decides whether a missing bar
    is an unknown or an exit. They resolve to `Listing.LEAVING`; see its
    docstring for the measurement and for why it is not an error in either
    source.
    """
    u_date = conn.execute("SELECT MAX(snapshot_date) FROM krx_universe").fetchone()[0]
    d_date = conn.execute("SELECT MAX(snapshot_date) FROM krx_delisted").fetchone()[0]
    if not u_date:
        raise KrxScanError("no krx_universe snapshot; run data.krx_universe --snapshot")
    if not d_date:
        raise KrxScanError(
            "no krx_delisted snapshot; a pool of listed names alone is "
            "survivorship-contaminated by construction. Run "
            "data.krx_delisted --snapshot"
        )
    live = {
        code: name
        for code, _m, name, _g, _i in fetch_krx_universe(
            conn, u_date, common_stock_only=True
        )
    }
    dead = {
        code: name
        for code, _m, name, isin in fetch_krx_delisted(conn, d_date)
        if has_plain_equity_code(code) and is_common_stock_issue(name, isin)
    }

    pool: list[tuple[str, str, Listing]] = []
    for code in sorted(live.keys() | dead.keys()):
        # Membership, never truthiness. `live.get(code) or dead[code]` looked
        # equivalent and is not: a live-only code whose name is the empty
        # string falls through to `dead[code]` and raises `KeyError`, which
        # takes the whole pool build down and so stops the scan from starting
        # at all. `parse_master` strips names, so it does not rule an empty
        # one out. Caught on review.
        if code in live and code in dead:
            status = Listing.LEAVING
            name = live[code] or dead[code]
        elif code in live:
            status = Listing.LIVE
            name = live[code]
        else:
            status = Listing.DELISTED
            name = dead[code]
        pool.append((code, name, status))
    return pool


def _pages(start: str, end: str, span_days: int):
    a = dt.datetime.strptime(start, "%Y%m%d").date()
    b = dt.datetime.strptime(end, "%Y%m%d").date()
    while a <= b:
        stop = min(b, a + dt.timedelta(days=span_days - 1))
        yield a.strftime("%Y%m%d"), stop.strftime("%Y%m%d")
        a = stop + dt.timedelta(days=1)


def _page(session: KisSession, code: str, start: str, end: str) -> list[dict]:
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": ADJUSTED,
    }
    url = f"{PAPER_HOST}{DAILY_ITEM_PATH}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(TR_DAILY_ITEM))
    if payload.get("rt_cd") != "0":
        raise KisKlinesError(f"{code} {start}..{end}: rt_cd={payload.get('rt_cd')}")
    # One contract, not a second copy of it. This module's own version
    # filtered malformed rows before comparing the count against a
    # hardcoded 100, so a truncated page with one bad row read as
    # complete -- see `kis_klines.validated_output2`.
    return validated_output2(
        payload,
        cap=rows_per_call_cap(is_index=False),
        what=f"{code} {start}..{end}",
    )


#: How many times a control may fail to be *asked* before the pass refuses.
#: Not a weakening of the control: see `_ask_control`'s asymmetry.
_CONTROL_ATTEMPTS = 4
_CONTROL_BACKOFF_S = 2.0


def _control_refusal(what: str, code: str, last: BaseException, *, attempts: int) -> str:
    """One refusal message for both controls, so they cannot drift apart.

    **It carries the real attempt count and the underlying message.** The
    positive control's own version said "in 4 attempts" even when a `rt_cd`
    rejection stopped it after one, and dropped `last` entirely — losing the
    `rt_cd`/`msg_cd` that is the exact diagnostic needed to decide whether the
    code belongs on `_RETRYABLE_MSG_CD`. That is the open item `_ask_control`'s
    own docstring names, so the message throwing it away was working against
    the plan written beside it. Reported on review.
    """
    kind = classify_failure(last)
    why = (
        f"retried {attempts} time{'s' if attempts != 1 else ''}"
        if kind is Failure.TRANSPORT
        else f"not retried after {attempts}: {kind.value} is an answer from the "
        f"venue, not a failure to reach it"
    )
    return (
        f"{what} {code} could not be asked ({type(last).__name__}: {last}); "
        f"{why}"
    )


def _ask_control(session: KisSession, code: str, what: str) -> list[dict]:
    """Ask one control, retrying a failure to **ask** but never an answer.

    **The asymmetry is the whole design, and it is measured rather than
    assumed.** A control answers the question *"does a nonsense code return
    rows?"*, and 25 consecutive calls to `999999` on 2026-09-24 returned:

    | | |
    |---|---|
    | `rt_cd=0`, **zero rows** | 18 |
    | `HTTPError 500` | 5 |
    | `TimeoutError` | 2 |
    | **rows returned** | **0** |

    So the control's premise was never once contradicted; what failed was
    reaching the endpoint at all. Refusing the pass on that is refusing on
    evidence the run does not have — and it cost two multi-hour scan launches,
    aborted at startup seconds in.

    Hence: **a failure to ask is retried; an answer is never retried.** If a
    control comes back carrying bars, that is the real signal the pass exists
    to catch and it refuses immediately — retrying it would be sampling until
    the answer is convenient. If it cannot be asked after
    `_CONTROL_ATTEMPTS`, it still refuses, because a control that is
    consistently unreachable leaves every `absent` unreadable exactly as
    before.

    **Only `Failure.TRANSPORT` counts as a failure to ask, and that is
    narrower than the abort which prompted this.** A `rt_cd` rejection is a
    *completed answer* from the venue, not a transport failure, so by this
    function's own asymmetry it may not be retried — reported on review, and
    the reviewer is right against the first version of this code, which
    retried any exception at all.

    **The consequence is stated rather than papered over: the `rt_cd=1` abort
    observed on 2026-09-24 is NOT covered by this fix.** Covering it would
    mean allowlisting the `msg_cd` behind it the way `fetch_daily_page` does
    for the measured-transient `OPSQ0003`, and 25 probe calls never reproduced
    a `rt_cd=1` at all — so there is no `msg_cd` to allowlist and adding one on
    a guess is the mistake this project already recorded for Binance's HTTP
    418. What *is* covered is the transport class, 7 of those 25 calls. If the
    `rt_cd=1` abort recurs, capture its `msg_cd` and decide then.
    """
    last: Exception | None = None
    for attempt in range(_CONTROL_ATTEMPTS):
        time.sleep(_SPACING_S)
        try:
            return _page(session, code, PANEL_START, "20190430")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if classify_failure(exc) is not Failure.TRANSPORT:
                break
            if attempt + 1 < _CONTROL_ATTEMPTS:
                time.sleep(_CONTROL_BACKOFF_S * (attempt + 1))
    raise KrxScanError(
        _control_refusal(what, code, last, attempts=attempt + 1)
        + "; a zero-row answer from a real candidate would be unreadable"
    ) from None


def verify_negative_controls(session: KisSession) -> None:
    """Refuse the scan unless a nonsense code really answers empty.

    Without this every `absent` in a 4,371-name pass is unreadable, and the
    pass would report a clean run having learned nothing.
    """
    for code in NEGATIVE_CONTROLS:
        rows = _ask_control(session, code, "negative control")
        if rows:
            raise KrxScanError(
                f"negative control {code} returned {len(rows)} bars; the "
                f"request is reaching something other than what this thinks"
            )


def store(conn: sqlite3.Connection, code: str, rows: list[dict]) -> int:
    params = [
        (
            code,
            r["stck_bsop_date"],
            r.get("stck_oprc"),
            r.get("stck_hgpr"),
            r.get("stck_lwpr"),
            r.get("stck_clpr"),
            r.get("acml_vol"),
            r.get("acml_tr_pbmn"),
        )
        for r in rows
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO scan_bars "
        "(code, bsop_date, open, high, low, close, volume, turnover) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        params,
    )
    conn.commit()
    return len(params)


def already_done(conn: sqlite3.Connection) -> set[str]:
    """Codes a previous pass finished. Resumption reads the database
    rather than a sidecar, so a half-written sidecar cannot disagree with
    what is actually stored."""
    return {
        row[0]
        for row in conn.execute(
            # `absent:%` as well as the bare legacy `absent`, so a pass
            # already recorded under the old single bucket still resumes.
            # `failed:%` is deliberately absent from this set -- a failure is
            # what a second pass exists to retry, and `RETRYABLE_FAILURES`
            # decides which.
            "SELECT code FROM scan_progress WHERE status = 'done' "
            "OR status = 'absent' OR status LIKE 'absent:%'"
        )
    }


def record_progress(conn: sqlite3.Connection, code: str, status: str) -> None:
    row = conn.execute(
        "SELECT MIN(bsop_date), MAX(bsop_date), COUNT(*) FROM scan_bars WHERE code = ?",
        (code,),
    ).fetchone()
    frozen = conn.execute(
        "SELECT COUNT(*) FROM scan_bars WHERE code = ? AND open = high "
        "AND high = low AND low = close "
        "AND (turnover IS NULL OR CAST(turnover AS REAL) = 0)",
        (code,),
    ).fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO scan_progress "
        "(code, first_date, last_date, bars, frozen, status, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, row[0], row[1], row[2], frozen, status,
         dt.datetime.now(dt.timezone.utc).isoformat()),
    )
    conn.commit()


def scan(
    session: KisSession,
    conn: sqlite3.Connection,
    pool,
    *,
    progress=None,
    allow_in_session: bool = True,
) -> dict:
    done = already_done(conn)
    counts = {"done": 0, "absent": 0, "failed": 0, "skipped": len(
        [c for c, _n, _l in pool if c in done]
    )}
    started = time.monotonic()
    paused = 0.0
    todo = [c for c in pool if c[0] not in done]
    for i, (code, name, _listing) in enumerate(todo):
        # **Pause for the session, do not merely refuse to start in it.**
        # A 64-hour pass begun after one close runs straight through the
        # next day's session otherwise, which is exactly the contention
        # the start-up guard exists to avoid -- the guard would have been
        # protecting only the first eight hours of three days.
        paused_at = None
        while not allow_in_session and in_continuous_session():
            if paused_at is None:
                paused_at = time.monotonic()
                if progress:
                    # Once on entry, not once per poll: a six-hour session
                    # is ~360 identical lines otherwise, and the run that
                    # produced them read as a hang rather than a pause.
                    progress(i, len(todo), code, "PAUSED for the KRX session", None)
            time.sleep(_SESSION_POLL_S)
        if paused_at is not None:
            # **Paused time is not work time.** Leaving it in the divisor
            # made the post-pause ETA read in the billions of hours, which
            # is a reported figure taken from the wrong denominator.
            paused += time.monotonic() - paused_at
            if progress:
                progress(i, len(todo), code, "resumed after the session", None)
        rows: list[dict] = []
        failed = False
        for start, end in _pages(PANEL_START, PANEL_END, PAGE_DAYS):
            time.sleep(_SPACING_S)
            try:
                rows.extend(_page(session, code, start, end))
            except Exception as exc:  # noqa: BLE001
                # **Recorded, not fatal.** A pass this long must survive a
                # transient failure; the coverage report is what stops a
                # partial scan being read as a complete one.
                # The KIND, not the exception type. `failed:KisKlinesError`
                # covered a transient rejection, a row-cap breach and a
                # parse refusal alike, so a second pass could not tell
                # which of the 193 it should retry.
                record_progress(conn, code, failure_detail(exc))
                counts["failed"] += 1
                failed = True
                break
        if failed:
            continue
        if rows:
            store(conn, code, rows)
            record_progress(conn, code, "done")
            counts["done"] += 1
        else:
            # UNKNOWN, never "not listed". `rt_cd=0` with zero rows is the
            # same answer for a dead name, an out-of-range window and a
            # code that never existed, and resolving that needs a separate
            # over-wide probe plus a negative control -- which is what a
            # second pass does, not something a first pass may assume.
            record_progress(conn, code, Absence.UNKNOWN.value)
            counts["absent"] += 1
        if progress and i % 50 == 0:
            worked = time.monotonic() - started - paused
            progress(i, len(todo), code, name, (i + 1) / max(1e-9, worked))
    return counts



def resolve_absences(
    session: KisSession,
    conn: sqlite3.Connection,
    *,
    progress=None,
    limit: int | None = None,
) -> dict:
    """Turn each `absent:unknown` into one of the two answers it hides.

    **The over-wide request, used for the one job it is right for.** KIS's
    100-row cap keeps the NEWEST rows, so an intentionally over-wide window
    returns a name's last 100 sessions whatever its span -- which is how a
    delisted name's final trading day is read. It is the wrong tool for
    per-day membership across a window, because every session before the
    newest 100 is truncated away and would read as "not in the pool", and
    `fetch_daily_page` refuses a capped page for exactly that reason. Here
    the question is only *does KIS price this code at all*, so the
    truncation is irrelevant and the refusal has to be bypassed
    deliberately.

    The negative controls run first, in this same pass. Without them a
    zero-row answer cannot be read at all -- `999999` and `ZZZZZZ` return
    `rt_cd=0` with zero rows exactly as a dead name does -- so an
    unresolvable control leaves every symbol at `UNKNOWN` rather than
    letting the pass guess.
    """
    verify_negative_controls(session)
    verify_wide_probe_positive_control(session)

    todo = [
        row[0]
        for row in conn.execute(
            "SELECT code FROM scan_progress WHERE status = ? OR status = 'absent' "
            "ORDER BY code",
            (Absence.UNKNOWN.value,),
        )
    ]
    if limit is not None:
        todo = todo[:limit]

    counts = {a.value: 0 for a in Absence}
    counts["failed"] = 0
    #: Probed bars land inside the panel the first pass found empty. Not an
    #: absence at all -- see the branch that increments it.
    counts["contradicted"] = 0
    #: The probe came back at its row cap with every date after the panel, so
    #: older in-panel rows may have been truncated away unseen.
    counts["truncated"] = 0
    for i, code in enumerate(todo):
        time.sleep(_SPACING_S)
        try:
            dates = _wide_probe(session, code)
        except Exception as exc:  # noqa: BLE001
            record_progress(conn, code, failure_detail(exc))
            counts["failed"] += 1
            continue
        if not dates:
            status = Absence.NEVER_SERVED
        elif len(dates) >= EQUITY_ROWS_PER_CALL_CAP and min(dates) > PANEL_END:
            # **Truncated, so undecidable.** The probe keeps only the newest
            # rows. If it came back full AND every date is after the panel,
            # then older rows were dropped and some of them may be in-panel
            # bars -- so this is not "outside the window", it is "we cannot
            # see". Recording OUTSIDE_WINDOW would file a real hole as an
            # expected absence and `already_done` would call the code
            # complete.
            #
            # Not reachable today: `PANEL_END` is a handful of sessions back,
            # so 100 newest rows cannot all postdate it. It becomes reachable
            # once a second pass runs ~100 sessions after `PANEL_END`, which
            # is exactly the kind of latent condition that surfaces when
            # nobody is looking for it. Caught on review.
            status = Absence.UNKNOWN
            counts["truncated"] += 1
        elif any(PANEL_START <= d <= PANEL_END for d in dates):
            # **The probe contradicts the scan, so nothing is resolved.**
            # KIS prices this code inside the very panel the pass found no
            # bars for, which is not "outside the window" -- it means a
            # session that should have been fetched was not. Calling it
            # OUTSIDE_WINDOW would file a real hole as an expected absence,
            # which is the survivorship direction. It stays UNKNOWN, and is
            # counted separately so it is visible rather than merely
            # unresolved.
            status = Absence.UNKNOWN
            counts["contradicted"] += 1
        else:
            status = Absence.OUTSIDE_WINDOW
        record_progress(conn, code, status.value)
        counts[status.value] += 1
        if progress and i % 50 == 0:
            progress(i, len(todo), code, status.value, None)
    return counts


def verify_wide_probe_positive_control(session: KisSession) -> None:
    """Refuse the resolver unless the wide probe really returns bars.

    The counterpart to `verify_negative_controls`, and not a duplicate of it:
    that one proves an empty answer is possible, this one proves a non-empty
    answer is. Both are needed before an empty answer may be read as
    `NEVER_SERVED`, because an instrument that answers empty for everything
    passes the negative control by construction.
    """
    # Same asymmetry as the negative controls -- see `_ask_control`. A failure
    # to reach the endpoint is retried; an empty ANSWER is not, because that is
    # the signal this control exists to catch.
    last: Exception | None = None
    dates: list[str] | None = None
    for attempt in range(_CONTROL_ATTEMPTS):
        time.sleep(_SPACING_S)
        try:
            dates = _wide_probe(session, WIDE_PROBE_POSITIVE_CONTROL)
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if classify_failure(exc) is not Failure.TRANSPORT:
                break
            if attempt + 1 < _CONTROL_ATTEMPTS:
                time.sleep(_CONTROL_BACKOFF_S * (attempt + 1))
    if dates is None:
        raise KrxScanError(
            _control_refusal(
                "the wide probe's positive control",
                WIDE_PROBE_POSITIVE_CONTROL,
                last,
                attempts=attempt + 1,
            )
            + ". Until it answers, an empty probe result cannot be read as "
            "'KIS does not price this code'."
        ) from None
    if not dates:
        raise KrxScanError(
            f"the wide probe returned no bars for "
            f"{WIDE_PROBE_POSITIVE_CONTROL}, which KIS serves back to 1991. "
            f"Every absent:unknown would be recorded NEVER_SERVED on the "
            f"strength of an answer the probe gives for everything, removing "
            f"real names from the pool."
        )


def _wide_probe(session: KisSession, code: str) -> list[str]:
    """Does KIS price this code at all? One deliberately capped request.

    Bypasses the page contract's cap refusal on purpose -- see
    `resolve_absences`. It returns the bars' **dates** rather than a bool so
    a caller can read the final trading day off the newest one without a
    second call, and can check whether any of them fall inside the panel.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": "19900101",
        # **Today, not `PANEL_END`.** This asks whether KIS prices the code at
        # all, which is a different question from what the panel covers. Ending
        # at `PANEL_END` meant a name listed after it returned zero rows here
        # exactly as it had in the first pass, and got recorded
        # `NEVER_SERVED` -- asserting KIS does not price a name it prices
        # perfectly well. That is the unsafe direction: it is the pool losing a
        # real name, which is the survivorship bias this whole universe exists
        # to remove. With today as the end, such a name comes back with dates
        # entirely after `PANEL_END` and resolves to `OUTSIDE_WINDOW`, which is
        # what it is. Caught on review.
        #
        # KST rather than UTC because a KRX trading date maps exactly onto UTC
        # midnight, so during 00:00-09:00 KST the UTC date is still yesterday
        # and would exclude a session that has already opened.
        "FID_INPUT_DATE_2": dt.datetime.now(KST).strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": ADJUSTED,
    }
    url = f"{PAPER_HOST}{DAILY_ITEM_PATH}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(TR_DAILY_ITEM))
    if payload.get("rt_cd") != "0":
        raise KisKlinesError(
            f"KIS rejected the request for {code} (wide probe): "
            f"rt_cd={payload.get('rt_cd')} msg_cd={payload.get('msg_cd')}"
        )
    raw = payload.get("output2")
    if raw is None or not isinstance(raw, list):
        raise KisKlinesError(f"output2 is not a list for {code} (wide probe)")
    # **A dateless row must not count as a bar.** This function's answer is
    # "does KIS price this code at all", and `rows` being non-empty is the
    # whole test -- so a placeholder dict would resolve a genuinely
    # never-served code to `OUTSIDE_WINDOW`, which is the wrong direction:
    # it asserts the name existed. Every other reader in this package
    # requires the date; this one skipped it. Caught on review.
    dates = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise KisKlinesError(
                f"output2 row {index} is not an object for {code} (wide probe)"
            )
        date = str(row.get("stck_bsop_date") or "").strip()
        if not date:
            raise KisKlinesError(
                f"output2 row {index} carries no stck_bsop_date for {code} "
                f"(wide probe)"
            )
        dates.append(date)
    return dates


def retryable_failures(conn: sqlite3.Connection) -> list[str]:
    """The codes a second pass may legitimately retry.

    An allowlist of failure kinds, so a kind nobody anticipated is left
    alone rather than retried into whatever caused it. `failed:capped` in
    particular must not be retried: it means the paging is wrong, and
    retrying reproduces it at the same cost.
    """
    allowed = tuple(f.value for f in RETRYABLE_FAILURES)
    return [
        row[0]
        for row in conn.execute(
            "SELECT code FROM scan_progress WHERE status IN "
            f"({','.join('?' * len(allowed))}) ORDER BY code",
            allowed,
        )
    ]


def coverage(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM scan_progress").fetchone()[0]
    bars = conn.execute("SELECT COUNT(*) FROM scan_bars").fetchone()[0]
    print(f"symbols recorded {total:,}   bars {bars:,}")
    for status, n, b in conn.execute(
        # Grouped on the KIND, not on a character count. A previous version
        # used `substr(status, 1, 14)`, whose arithmetic did not match the
        # status names this change introduced: `failed:rejected` printed as
        # `failed:rejecte`, `failed:transport` and `failed:malformed` both
        # lost their last character, and `failed:other:<Type>` split into one
        # group per exception type's first letter. Deciding whether to run a
        # second pass means reading this report, so it has to show the retry
        # kinds exactly.
        "SELECT CASE WHEN status LIKE 'failed:other:%' THEN 'failed:other' "
        "ELSE status END, "
        "COUNT(*), SUM(bars) FROM scan_progress GROUP BY 1 ORDER BY 2 DESC"
    ):
        print(f"  {status:<10} {n:>6,} symbols  {b or 0:>10,} bars")
    frozen = conn.execute(
        "SELECT COUNT(*), SUM(frozen) FROM scan_progress WHERE frozen > 20"
    ).fetchone()
    print(f"\n  {frozen[0] or 0:,} symbols carry >20 frozen sessions "
          f"(O==H==L==C, zero turnover), {frozen[1] or 0:,} in total")
    print("  A bar is not evidence the name was tradeable -- only that it "
          "was listed.")



def progress_printer():
    """The one progress line, shared by both passes."""

    def progress(i, n, code, name, rate):
        # `rate is None` is a state change (paused, resumed), not a
        # measurement -- printing an ETA there invents one.
        tail = (
            "" if rate is None
            else f"  {rate:.2f} sym/s  eta {(n - i - 1) / max(rate, 1e-9) / 3600:.1f}h"
        )
        print(f"  [{i + 1:>5}/{n}] {code} {name[:28]:<28}{tail}", flush=True)

    return progress


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=str(DEFAULT_SCAN_DB))
    ap.add_argument("--universe-db", default=None,
                    help="where the krx_universe/krx_delisted snapshots live")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true")
    group.add_argument("--coverage", action="store_true")
    group.add_argument(
        "--second-pass",
        action="store_true",
        help="resolve every absent:unknown into never_served or "
        "outside_window, and retry the failures whose kind is retryable. "
        "Reads the same database; adds no new candidates.",
    )
    ap.add_argument("--limit", type=int, default=0, help="first N candidates (probe)")
    ap.add_argument(
        "--in-session",
        action="store_true",
        help="run even during the KRX continuous session. The instance's "
        "collectors share this app key and collect series that cannot be "
        "backfilled; throughput was measured at 0.3/s in-session against "
        "0.5-0.7/s after the close.",
    )
    args = ap.parse_args(argv)

    conn = connect(args.db_path)
    conn.executescript(SCAN_SCHEMA)
    conn.commit()

    if args.coverage:
        coverage(conn)
        conn.close()
        return 0

    if in_continuous_session() and not args.in_session:
        print(
            "refusing to scan during the KRX continuous session (09:00-15:30 "
            "KST). The instance's collectors share this app key and collect "
            "series that cannot be backfilled. Pass --in-session to override.",
            file=sys.stderr,
        )
        conn.close()
        return 2

    key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not secret:
        print("KIS_APP_KEY / KIS_APP_SECRET must both be set", file=sys.stderr)
        conn.close()
        return 2

    if args.second_pass:
        session = KisSession(key, secret, host=PAPER_HOST)
        retry = retryable_failures(conn)
        print(
            f"second pass: {len(retry):,} retryable failures, then every "
            f"absent:unknown",
            flush=True,
        )
        if retry:
            # Re-fetched through the ordinary path, so a retry is subject to
            # the same page contract as a first fetch. `pool` entries are
            # rebuilt from the progress table rather than the universe, so a
            # code KRX has since delisted is still retried.
            universe = connect(args.universe_db) if args.universe_db else conn
            by_code = {c: (n, st) for c, n, st in candidates(universe)}
            if args.universe_db:
                universe.close()
            missing = [c for c in retry if c not in by_code]
            if missing:
                print(
                    f"  {len(missing)} retryable code(s) are no longer in the "
                    f"pool and are left alone: {', '.join(missing[:5])}"
                    f"{' …' if len(missing) > 5 else ''}",
                    flush=True,
                )
            again = [(c, by_code[c][0], by_code[c][1]) for c in retry if c in by_code]
            # The status has to be cleared first, or `already_done` -- which
            # counts `absent:%` as complete -- would skip a code whose retry
            # is the entire point of this pass.
            conn.executemany(
                "DELETE FROM scan_progress WHERE code = ?", [(c,) for c, _, _ in again]
            )
            conn.commit()
            print(f"{scan(session, conn, again, progress=progress_printer())}", flush=True)
        print(f"{resolve_absences(session, conn, progress=progress_printer())}", flush=True)
        coverage(conn)
        conn.close()
        return 0

    universe = connect(args.universe_db) if args.universe_db else conn
    pool = candidates(universe)
    if args.universe_db:
        universe.close()
    if args.limit:
        pool = pool[: args.limit]
    done = already_done(conn)
    # **Counted against the pool, not by subtracting two totals.** `done` can
    # hold codes that are no longer candidates -- the progress table still
    # carries rows from the pre-deduplication pool of 4,643 -- so
    # `len(pool) - len(done)` printed "-9 to fetch" on a run that had 257
    # failures to retry. A negative count is obvious; a wrong positive one
    # would not have been.
    todo_count = len([c for c, _n, _s in pool if c not in done])
    # **"not in this selection", not "no longer candidates."** `--limit`
    # truncates the pool, so a completed code can be a perfectly current
    # candidate that simply falls outside this run's slice -- calling it
    # delisted would be a wrong statement rather than a vague one. Reported on
    # review.
    outside = len(done) - (len(pool) - todo_count)
    print(f"{len(pool):,} candidates, {len(pool) - todo_count:,} already "
          f"complete, {todo_count:,} to fetch "
          f"(~{todo_count * 24 / 0.6 / 3600:.1f}h at 0.6/s)"
          + (f"; {outside:,} recorded codes are not in this candidate selection"
             if outside else ""),
          flush=True)

    session = KisSession(key, secret, host=PAPER_HOST)
    verify_negative_controls(session)
    print("negative controls pass: an empty answer really means empty", flush=True)

    counts = scan(session, conn, pool, progress=progress_printer(),
                  allow_in_session=args.in_session)
    print(f"\n{counts}")
    coverage(conn)
    conn.close()
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
