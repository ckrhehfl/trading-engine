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
import os
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path

from data.kis_klines import (
    ADJUSTED,
    DAILY_ITEM_PATH,
    PAPER_HOST,
    TR_DAILY_ITEM,
    KisKlinesError,
    KisSession,
    _get_with_retry,
    rows_per_call_cap,
    validated_output2,
)
from data.krx_instrument import (
    InstrumentClass,
    instrument_class,
    is_common_stock,
    is_reit,
    is_spac,
)
from data.store import connect, fetch_krx_delisted, fetch_krx_universe

PANEL_START = "20190102"
PANEL_END = "20260918"

#: Calendar days per request. ~82 trading days, comfortably under the
#: equity endpoint's silent 100-row cap.
PAGE_DAYS = 120

NEGATIVE_CONTROLS = ("999999", "ZZZZZZ", "000000")

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


def candidates(conn) -> list[tuple[str, str, bool]]:
    """Every survivorship-safe common-stock code: live plus delisted.

    **The two sides apply the SAME four filters**, which they did not when
    the SPAC rule landed: `fetch_krx_universe(common_stock_only=True)`
    picked it up on the live side immediately, leaving the delisted side
    with its 178 SPACs still in. Half a filter is worse than none, because
    the pool then looks filtered.
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
    live = [
        (code, name, True)
        for code, _m, name, _g, _i in fetch_krx_universe(
            conn, u_date, common_stock_only=True
        )
    ]
    dead = [
        (code, name, False)
        for code, _m, name, isin in fetch_krx_delisted(conn, d_date)
        if len(code) == 6
        and code.isdigit()
        and is_common_stock(isin)
        and instrument_class(isin) is InstrumentClass.STOCK_LIKE
        and not is_spac(name)
        and not is_reit(name)
    ]
    return live + dead


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


def verify_negative_controls(session: KisSession) -> None:
    """Refuse the scan unless a nonsense code really answers empty.

    Without this every `absent` in a 4,643-name pass is unreadable, and
    the pass would report a clean run having learned nothing.
    """
    for code in NEGATIVE_CONTROLS:
        time.sleep(_SPACING_S)
        try:
            rows = _page(session, code, PANEL_START, "20190430")
        except Exception as exc:  # noqa: BLE001
            raise KrxScanError(
                f"negative control {code} could not be asked "
                f"({type(exc).__name__}: {exc}); a zero-row answer from a "
                f"real candidate would be unreadable"
            ) from None
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
            "SELECT code FROM scan_progress WHERE status IN ('done', 'absent')"
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
    for i, (code, name, _listed) in enumerate(todo):
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
                record_progress(conn, code, f"failed:{type(exc).__name__}")
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
            record_progress(conn, code, "absent")
            counts["absent"] += 1
        if progress and i % 50 == 0:
            worked = time.monotonic() - started - paused
            progress(i, len(todo), code, name, (i + 1) / max(1e-9, worked))
    return counts


def coverage(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM scan_progress").fetchone()[0]
    bars = conn.execute("SELECT COUNT(*) FROM scan_bars").fetchone()[0]
    print(f"symbols recorded {total:,}   bars {bars:,}")
    for status, n, b in conn.execute(
        "SELECT CASE WHEN status LIKE 'failed:%' THEN 'failed' ELSE status END, "
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=str(DEFAULT_SCAN_DB))
    ap.add_argument("--universe-db", default=None,
                    help="where the krx_universe/krx_delisted snapshots live")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true")
    group.add_argument("--coverage", action="store_true")
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

    universe = connect(args.universe_db) if args.universe_db else conn
    pool = candidates(universe)
    if args.universe_db:
        universe.close()
    if args.limit:
        pool = pool[: args.limit]
    done = already_done(conn)
    print(f"{len(pool):,} candidates, {len(done):,} already complete, "
          f"{len(pool) - len(done):,} to fetch "
          f"(~{(len(pool) - len(done)) * 24 / 0.6 / 3600:.0f}h at 0.6/s)",
          flush=True)

    session = KisSession(key, secret, host=PAPER_HOST)
    verify_negative_controls(session)
    print("negative controls pass: an empty answer really means empty", flush=True)

    def progress(i, n, code, name, rate):
        # `rate is None` is a state change (paused, resumed), not a
        # measurement -- printing an ETA there invents one.
        tail = (
            "" if rate is None
            else f"  {rate:.2f} sym/s  eta {(n - i - 1) / max(rate, 1e-9) / 3600:.1f}h"
        )
        print(f"  [{i + 1:>5}/{n}] {code} {name[:28]:<28}{tail}", flush=True)

    counts = scan(session, conn, pool, progress=progress,
                  allow_in_session=args.in_session)
    print(f"\n{counts}")
    coverage(conn)
    conn.close()
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
