"""Does a day-one-selected universe still drift at +54%/yr?

`rd-v` §1 measured the `rd-r` ten returning **+694% equal-weight, +54%/yr**
against KOSPI's +19%, and identified the cause: they were ranked on
**2026Q1** turnover, a date inside the window they are then measured over.
This is the test of that diagnosis. Same measurement, same panel, same
index -- one thing changed, the selection date.

**If the diagnosis is right the premium collapses toward the index.** If it
does not, the drift has some other source and every conclusion resting on
`rd-v` §1 needs revisiting, which is why this is worth running rather than
assuming.

**The comparison is deliberately like-for-like and therefore imperfect.**
`rd-v`'s figure is a buy-at-the-second-open, hold-to-the-last-close ratio
per name, equal-weighted. Reproduced exactly here, including its
no-reinvestment property: **a name that delists mid-window contributes its
terminal ratio and nothing after**, because the money has nowhere
pre-specified to go and inventing a reinvestment rule would make this a
different measurement. That is conservative in the direction that matters
-- it cannot manufacture a low number.

**`load_daily_panel` cannot be used here, and the reason is the finding in
miniature.** It inner-joins every name onto the dates they *all* share, so
a single name delisted in 2020 truncates the entire panel at 2020. That is
a survivorship filter expressed as a join, and it is why this module reads
per-name series directly instead.

**Discovery mode.** KRX daily is spent. Nothing here may be promoted,
quoted as evidence of an edge, or reported as a pass.

Run:

    python -m research.krx_dayone_drift --fetch     # history for the 30
    python -m research.krx_dayone_drift --measure
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from data.kis_klines import (
    ADJUSTED,
    DAILY_ITEM_PATH,
    PAPER_HOST,
    TR_DAILY_ITEM,
    KisKlinesError,
    KisSession,
    _get_with_retry,
)
from data.store import connect
from research.krx_dayone_universe import DEFAULT_OUT, DEFAULT_SCRATCH_DB

#: The panel every document this bounds is measured over -- `ms-f`,
#: `rd-t`, `rd-u`, `tm-e` and `rd-v` §1. Held fixed so the comparison is
#: against `rd-v`'s own number rather than a differently-windowed one.
PANEL_START = "20190102"
PANEL_END = "20260918"

#: KIS caps an equity daily request at 100 rows and keeps the NEWEST,
#: silently. Pages are sized well under it -- a capped page would be a
#: different window than the one asked for.
PAGE_DAYS = 120

INDEX_CODE = "0001"  # KOSPI
_SPACING_S = 0.05


@dataclass(frozen=True)
class NameDrift:
    code: str
    name: str
    listed_now: bool
    first_date: str
    last_date: str
    first_open: float
    last_close: float
    #: Sessions where KIS printed a bar with O==H==L==C and zero turnover
    #: -- a TRADING HALT, not a quiet day. See `frozen_sessions`.
    frozen: int = 0

    @property
    def ratio(self) -> float:
        return self.last_close / self.first_open if self.first_open > 0 else float("nan")


class DayOneDriftError(RuntimeError):
    """The measurement could not be trusted, so it was refused."""


def _page(session: KisSession, code: str, start: str, end: str, *, is_index=False):
    params = {
        "FID_COND_MRKT_DIV_CODE": "U" if is_index else "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
    }
    if not is_index:
        params["FID_ORG_ADJ_PRC"] = ADJUSTED
    path = (
        "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
        if is_index
        else DAILY_ITEM_PATH
    )
    tr = "FHKUP03500100" if is_index else TR_DAILY_ITEM
    url = f"{PAPER_HOST}{path}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(tr))
    if payload.get("rt_cd") != "0":
        raise KisKlinesError(f"{code} {start}..{end}: rt_cd={payload.get('rt_cd')}")
    rows = [
        r
        for r in (payload.get("output2") or [])
        if isinstance(r, dict) and r.get("stck_bsop_date")
    ]
    # Indices cap at 50, equities at 100 -- **a cap is a property of an
    # endpoint, not of a venue** (CLAUDE.md). Both truncate silently.
    cap = 50 if is_index else 100
    if len(rows) >= cap:
        raise KisKlinesError(
            f"{code} {start}..{end}: {len(rows)} rows at the {cap}-row cap; "
            f"this page is silently truncated and cannot be trusted"
        )
    return rows


def _date_pages(start: str, end: str, span_days: int):
    """Inclusive `[start, end]` split into spans small enough to stay under
    the row cap."""
    import datetime as dt

    a = dt.datetime.strptime(start, "%Y%m%d").date()
    b = dt.datetime.strptime(end, "%Y%m%d").date()
    while a <= b:
        stop = min(b, a + dt.timedelta(days=span_days - 1))
        yield a.strftime("%Y%m%d"), stop.strftime("%Y%m%d")
        a = stop + dt.timedelta(days=1)


def fetch_series(session: KisSession, code: str, *, is_index=False) -> list[dict]:
    """Every bar in the panel window, ascending, de-duplicated."""
    span = PAGE_DAYS if not is_index else 60
    seen: dict[str, dict] = {}
    for start, end in _date_pages(PANEL_START, PANEL_END, span):
        time.sleep(_SPACING_S)
        for row in _page(session, code, start, end, is_index=is_index):
            seen[row["stck_bsop_date"]] = row
    return [seen[d] for d in sorted(seen)]


#: **The index endpoint uses different price field names for the same
#: bar.** `kis_klines._parse_row` already documents this -- index bars
#: carry `bstp_nmix_*` where equities carry `stck_*`, on an otherwise
#: identical row. Reading the equity names against an index response
#: yields `None` for every price, which stores as NULL and then vanishes
#: from any `open IS NOT NULL` query -- a silent empty series rather than
#: an error. It is only visible here because the selection-premium line
#: refuses to quote a figure it could not compute.
_EQUITY_FIELDS = ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr")
_INDEX_FIELDS = ("bstp_nmix_oprc", "bstp_nmix_hgpr", "bstp_nmix_lwpr",
                 "bstp_nmix_prpr")


def store_series(
    conn: sqlite3.Connection, code: str, rows: list[dict], *, is_index: bool = False
) -> int:
    """Into the SCRATCH database, never the KRX record.

    CLAUDE.md's "exactly one writer per series" makes the instance the only
    writer of `KRX:` klines, and `sync-krx-from-instance.sh` is
    `INSERT OR IGNORE`, so a locally-written row would never be corrected
    by a later sync.
    """
    o, h, low, c = _INDEX_FIELDS if is_index else _EQUITY_FIELDS
    params = [
        (code, r["stck_bsop_date"], r.get(o), r.get(h), r.get(low), r.get(c),
         r.get("acml_tr_pbmn"))
        for r in rows
    ]
    missing = sum(1 for p in params if p[2] is None or p[5] is None)
    if missing == len(params) and params:
        # Every price NULL means the field names are wrong for this
        # endpoint, not that the market was closed. Storing it would be a
        # silently empty series.
        raise DayOneDriftError(
            f"{code}: all {len(params)} rows parsed to NULL prices using the "
            f"{'index' if is_index else 'equity'} field names -- wrong "
            f"endpoint mapping, not missing data"
        )
    conn.executemany(
        "INSERT OR IGNORE INTO dayone_bars "
        "(code, bsop_date, open, high, low, close, turnover) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        params,
    )
    conn.commit()
    return len(rows)


SCRATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS dayone_bars (
  code TEXT NOT NULL,
  bsop_date TEXT NOT NULL,
  open TEXT, high TEXT, low TEXT, close TEXT, turnover TEXT,
  PRIMARY KEY (code, bsop_date)
);
"""


def frozen_sessions(conn: sqlite3.Connection, code: str) -> int:
    """Sessions KIS printed for a name that could not be traded.

    **Measured, not assumed** (2026-09-20): 신라젠 `215600` carries **604
    consecutive sessions, 2020-05-04 to 2022-10-12**, every one with
    `O == H == L == C` at 7,757 and `acml_tr_pbmn` 0 -- its real 거래정지.
    The next bar opens at that same 7,757 and closes at 10,043, a **+29%
    resumption gap**.

    This is the equity counterpart of the single-stock-futures fact
    CLAUDE.md already records, and it is more dangerous: a return series
    reads 603 zeros, which deflates volatility and inflates any Sharpe
    computed over it; a backtest holds a position it could not have exited
    for two and a half years; and the resumption gap is taken as a
    tradeable one-day return. **A bar is not evidence the name was
    tradeable** -- only that it was listed.

    Counted and reported rather than filtered, because what to do about it
    depends on the measurement. A first-open-to-last-close ratio is
    unaffected: a holder really was stuck.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM dayone_bars WHERE code = ? "
        "AND open = high AND high = low AND low = close "
        "AND (turnover IS NULL OR CAST(turnover AS REAL) = 0)",
        (code,),
    ).fetchone()[0]


def drift_for(conn: sqlite3.Connection, code: str, name: str, listed_now: bool):
    """First open to last close, from that name's OWN series.

    **Not from an aligned panel**, which would truncate at whatever the
    shortest-lived member shares with the rest -- the survivorship filter
    this module exists to avoid.
    """
    rows = conn.execute(
        "SELECT bsop_date, CAST(open AS REAL), CAST(close AS REAL) "
        "FROM dayone_bars WHERE code = ? AND open IS NOT NULL "
        "AND CAST(open AS REAL) > 0 ORDER BY bsop_date",
        (code,),
    ).fetchall()
    if len(rows) < 2:
        return None
    return NameDrift(
        code, name, listed_now, rows[0][0], rows[-1][0], rows[0][1], rows[-1][2],
        frozen_sessions(conn, code),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=str(DEFAULT_SCRATCH_DB))
    ap.add_argument("--universe", default=str(DEFAULT_OUT))
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--fetch", action="store_true")
    group.add_argument("--measure", action="store_true")
    group.add_argument(
        "--compare",
        action="store_true",
        help="fetch and measure the rd-r ten over the SAME window, which is "
        "what makes the comparison single-variable",
    )
    args = ap.parse_args(argv)

    universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))["universe"]
    conn = connect(args.db_path)
    conn.execute(SCRATCH_SCHEMA)
    conn.commit()

    if args.fetch:
        key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
        if not key or not secret:
            print("KIS_APP_KEY / KIS_APP_SECRET must both be set", file=sys.stderr)
            return 2
        session = KisSession(key, secret, host=PAPER_HOST)
        for i, row in enumerate(universe, 1):
            try:
                rows = fetch_series(session, row["code"])
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i:>2}/{len(universe)}] {row['code']} FAILED "
                      f"{type(exc).__name__}: {exc}", flush=True)
                continue
            n = store_series(conn, row["code"], rows)
            print(f"  [{i:>2}/{len(universe)}] {row['code']} {row['name']:<18} "
                  f"{n:>5} bars  {rows[0]['stck_bsop_date']}..{rows[-1]['stck_bsop_date']}",
                  flush=True)
        try:
            idx = fetch_series(session, INDEX_CODE, is_index=True)
            store_series(conn, f"IDX{INDEX_CODE}", idx, is_index=True)
            print(f"  KOSPI {len(idx)} bars")
        except Exception as exc:  # noqa: BLE001
            print(f"  KOSPI FAILED {type(exc).__name__}: {exc}")
        conn.close()
        return 0

    if args.compare:
        # **The same window, the same construction, only the selection rule
        # different.** Without this arm the comparison against `rd-v` §1
        # changes two things at once: `rd-v`'s panel is the rd-r ten's own
        # inner join (2021-11-29 .. 2026-09-17, 4.80 years) and this one is
        # 2019-01-02 .. 2026-09-18. Annualised figures are comparable across
        # those; totals are not, and "vs the index" only means something
        # against each window's own index.
        from research.krx_signal_ic import UNIVERSE as RD_R_TEN

        key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
        if not key or not secret:
            print("KIS_APP_KEY / KIS_APP_SECRET must both be set", file=sys.stderr)
            return 2
        session = KisSession(key, secret, host=PAPER_HOST)
        arm = []
        for code in RD_R_TEN:
            try:
                rows = fetch_series(session, code)
            except Exception as exc:  # noqa: BLE001
                print(f"  {code} FAILED {type(exc).__name__}: {exc}", flush=True)
                continue
            store_series(conn, code, rows)
            d = drift_for(conn, code, code, True)
            if d is not None:
                arm.append(d)
                print(f"  {d.code}  {d.first_date}..{d.last_date}  "
                      f"{d.ratio - 1:>+8.0%}", flush=True)
        idx = drift_for(conn, f"IDX{INDEX_CODE}", "KOSPI", True)
        conn.close()
        if not arm:
            raise DayOneDriftError("no rd-r series fetched")
        years = 7.71
        eq = statistics.fmean(d.ratio for d in arm)
        med = statistics.median(d.ratio for d in arm)
        print(f"\n  rd-r ten, 2019-2026: {eq:.2f}x = {eq - 1:+.0%}, "
              f"{eq ** (1/years) - 1:+.0%}/yr;  median {med - 1:+.0%}")
        if idx is not None:
            print(f"  KOSPI same window:   {idx.ratio:.2f}x, "
                  f"{idx.ratio ** (1/years) - 1:+.0%}/yr")
            print(f"  ** premium {eq / idx.ratio:.2f}x the index, "
                  f"{eq ** (1/years) - idx.ratio ** (1/years):+.0%}/yr excess **")
        print("  A name listed after 2019-01 starts at its own first bar -- the "
              "same\n  treatment a delisted member gets at the other end.")
        return 0

    drifts = []
    for row in universe:
        d = drift_for(conn, row["code"], row["name"], row["listed_now"])
        if d is not None:
            drifts.append(d)
    if not drifts:
        raise DayOneDriftError("no series stored; run --fetch first")

    idx = drift_for(conn, f"IDX{INDEX_CODE}", "KOSPI", True)
    conn.close()

    years = 7.71  # 2019-01-02 .. 2026-09-18, the panel rd-v §1 uses
    eq = statistics.fmean(d.ratio for d in drifts)
    med = statistics.median(d.ratio for d in drifts)
    dead = [d for d in drifts if not d.listed_now]

    print(f"=== day-one universe, ranked on {PANEL_START[:6]}, held to its own end ===")
    print(f"  {'code':<8} {'name':<20} {'first':>10} {'last':>10} {'total':>9} "
          f"{'halted':>7}")
    print("  " + "-" * 72)
    for d in sorted(drifts, key=lambda x: -x.ratio):
        flag = "" if d.listed_now else f"  DELISTED {d.last_date}"
        halt = f"{d.frozen:>7,}" if d.frozen else " " * 7
        print(f"  {d.code:<8} {d.name:<20} {d.first_open:>10,.0f} "
              f"{d.last_close:>10,.0f} {d.ratio - 1:>+8.0%} {halt}{flag}")
    halted = [d for d in drifts if d.frozen > 20]
    if halted:
        print(f"\n  ** {len(halted)} name(s) carry a real TRADING HALT KIS still "
              f"printed bars for: "
              f"{', '.join(f'{d.name} {d.frozen:,}' for d in halted)}")
        print("     O==H==L==C, zero turnover. A bar is not evidence the name "
              "was tradeable.")

    print(f"\n  members {len(drifts)} ({len(dead)} delisted during the window)")
    print(f"  equal-weight {eq:.2f}x = {eq - 1:+.0%} total, "
          f"{eq ** (1/years) - 1:+.0%}/yr;  median name {med - 1:+.0%}")
    if idx is None:
        print("  KOSPI unavailable -- no selection premium quoted.")
    else:
        print(f"  KOSPI        {idx.ratio:.2f}x = {idx.ratio - 1:+.0%} total, "
              f"{idx.ratio ** (1/years) - 1:+.0%}/yr")
        print(f"  ** selection premium: {eq / idx.ratio:.2f}x the index, "
              f"{(eq ** (1/years)) - (idx.ratio ** (1/years)):+.0%}/yr excess **")
    print(f"\n  rd-v §1, for comparison: the rd-r ten were 7.94x, +54%/yr, "
          f"3.4x the index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
