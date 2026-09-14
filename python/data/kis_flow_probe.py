"""Task RD-C probe: how deep is KIS's investor-type flow, and its intraday?

Two questions, and both are **clocks** rather than curiosities
(`.planning/rd-c-mechanism-catalogue.md` §6 and §7):

1. **투자자별 매매동향** -- KRX mandatorily discloses daily net buying by
   개인 / 기관 / 외국인 per stock, and no crypto venue and no US equity
   feed has an equivalent. KIS's `inquire-investor` is documented as
   "최근 30일". **If that is real, the series cannot be backfilled**, so a
   collector has to start now or the history never exists -- exactly
   `binance_positioning.py`'s situation.
2. **Intraday bars** -- the KR-10 store holds daily bars only, so every
   intraday mechanism in the catalogue is currently untestable on Korean
   names. Retention here has never been measured.

Read-only and exploratory, deliberately **not** a pipeline, following
`kis_probe.py`'s precedent. It writes nothing to the kline store.

**This module cannot place an order and must stay that way**, and
`python/tests/test_kis_probe_cannot_trade.py` enforces that structurally
rather than on the strength of this sentence. It knows only quotation TR
ids, sends no account number, and imports nothing from the trading path.

Credentials come from the environment and are never logged or echoed --
presence and length only.

Run (on the instance, per CLAUDE.md's "Run it where it will run"):

    python -m data.kis_flow_probe --host paper
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from typing import Any

from data.kis_klines import (
    PAPER_HOST,
    REAL_HOST,
    KisKlinesError,
    KisSession,
    _get_with_retry,
)

# ---------------------------------------------------------------- endpoints

INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
TR_INVESTOR = "FHKST01010900"

MINUTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
TR_MINUTE = "FHKST03010200"

# The decision-critical one. `inquire-time-itemchartprice` takes a time
# but **no date**, so it can only ever describe the current session; this
# one takes `FID_INPUT_DATE_1` and is therefore the only candidate for
# intraday *history*. Whether it serves a past date, and how far back,
# decides whether every intraday mechanism in the catalogue is testable on
# Korean names at all -- or whether a collector has to start today.
MINUTE_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
TR_MINUTE_DAILY = "FHKST03010230"

FOREIGN_TOTAL_PATH = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
TR_FOREIGN_TOTAL = "FHPTJ04400000"

SAMSUNG = "005930"
SK_HYNIX = "000660"

# The three columns the whole §6 argument rests on. Named here so a
# response that silently omits one is visible rather than read as a zero.
INVESTOR_COLUMNS = ("prsn_ntby_qty", "frgn_ntby_qty", "orgn_ntby_qty")

# The dates that bracketed the real 2026-09-14 run: 20250903 served 120
# bars and 20250902 served none, which is the boundary itself. They are
# kept as the default so a later run **re-measures the same edge** rather
# than a fresh guess -- the retention is a rolling trading-day count, so
# these should both fall off in time, and seeing that happen is the point.
# Every one is a real KRX trading day taken from the KR-10 series in the
# store: asking for a holiday would return nothing and mean nothing.
DEFAULT_RETENTION_DATES = ("20250905", "20250904", "20250903", "20250902")


def _get(session: KisSession, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"{session.host}{path}?{urllib.parse.urlencode(params)}"
    return _get_with_retry(url, session.headers(tr_id))


def _envelope(body: dict[str, Any]) -> tuple[str, str]:
    """`(rt_cd, msg1)`. `rt_cd == "0"` is KIS's success token."""
    return str(body.get("rt_cd", "?")), str(body.get("msg1", "")).strip()


def _rows(body: dict[str, Any], key: str = "output") -> list[dict[str, Any]]:
    out = body.get(key)
    if isinstance(out, list):
        return [r for r in out if isinstance(r, dict)]
    if isinstance(out, dict):
        return [out]
    return []


# ------------------------------------------------------------------ probes


def probe_investor(session: KisSession, code: str) -> dict[str, Any]:
    """How many days of investor-type flow does one call return?

    The answer is the whole point: a documented "최근 30일" that is real
    means the series is non-backfillable and a collector is urgent.
    """
    body = _get(
        session,
        INVESTOR_PATH,
        TR_INVESTOR,
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
    )
    rt_cd, msg = _envelope(body)
    rows = _rows(body)
    dates = sorted(str(r.get("stck_bsop_date", "")) for r in rows if r.get("stck_bsop_date"))
    present = [c for c in INVESTOR_COLUMNS if rows and c in rows[0]]
    return {
        "code": code,
        "rt_cd": rt_cd,
        "msg": msg,
        "rows": len(rows),
        "earliest": dates[0] if dates else None,
        "latest": dates[-1] if dates else None,
        "investor_columns_present": present,
        "missing_columns": [c for c in INVESTOR_COLUMNS if c not in present],
        "sample_keys": sorted(rows[0].keys()) if rows else [],
    }


def probe_minutes(session: KisSession, code: str, hour: str) -> dict[str, Any]:
    """How many minute bars come back, and from when?

    `FID_INPUT_HOUR_1` is the *end* of the requested window in HHMMSS, so
    asking for a late hour and reading the earliest bar returned measures
    one call's reach. Whether earlier days are reachable at all is the
    second question and is answered by asking for a past session.
    """
    body = _get(
        session,
        MINUTE_PATH,
        TR_MINUTE,
        {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hour,
            "FID_PW_DATA_INCU_YN": "Y",
        },
    )
    rt_cd, msg = _envelope(body)
    rows = _rows(body, "output2")
    stamps = sorted(
        f"{r.get('stck_bsop_date', '')}{r.get('stck_cntg_hour', '')}"
        for r in rows
        if r.get("stck_cntg_hour")
    )
    return {
        "code": code,
        "requested_hour": hour,
        "rt_cd": rt_cd,
        "msg": msg,
        "rows": len(rows),
        "earliest": stamps[0] if stamps else None,
        "latest": stamps[-1] if stamps else None,
        "sample_keys": sorted(rows[0].keys()) if rows else [],
    }


def probe_minutes_on_date(session: KisSession, code: str, date: str, hour: str) -> dict[str, Any]:
    """Can a *past* session's minute bars be fetched, and how far back?

    Asked separately per date rather than by binary search, because the
    interesting answer is not a single boundary: a `rt_cd=0` carrying zero
    rows is KIS's documented way of saying "nothing here" (it is what every
    expired futures contract returned in MS-B §2.1), and that is a
    different finding from an outright rejection.
    """
    body = _get(
        session,
        MINUTE_DAILY_PATH,
        TR_MINUTE_DAILY,
        {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": date,
            "FID_INPUT_HOUR_1": hour,
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "N",
        },
    )
    rt_cd, msg = _envelope(body)
    rows = _rows(body, "output2")
    stamps = sorted(
        f"{r.get('stck_bsop_date', '')}{r.get('stck_cntg_hour', '')}"
        for r in rows
        if r.get("stck_cntg_hour")
    )
    return {
        "code": code,
        "requested_date": date,
        "requested_hour": hour,
        "rt_cd": rt_cd,
        "msg": msg,
        "rows": len(rows),
        "earliest": stamps[0] if stamps else None,
        "latest": stamps[-1] if stamps else None,
        # The trap MS-B already hit once: a date echoed back that is not the
        # date asked for means the endpoint silently served something else.
        "served_requested_date": bool(stamps) and stamps[0][:8] == date,
    }


def probe_foreign_total(session: KisSession) -> dict[str, Any]:
    """The cross-sectional aggregate -- 외국인/기관 순매수 across names.

    Different shape from `inquire-investor`: one day, many stocks, rather
    than one stock, many days. Which of the two a collector should use
    depends on whether this one carries the same columns.
    """
    body = _get(
        session,
        FOREIGN_TOTAL_PATH,
        TR_FOREIGN_TOTAL,
        {
            "FID_COND_MRKT_DIV_CODE": "V",
            "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_RANK_SORT_CLS_CODE": "0",
            "FID_ETC_CLS_CODE": "0",
        },
    )
    rt_cd, msg = _envelope(body)
    rows = _rows(body)
    return {
        "rt_cd": rt_cd,
        "msg": msg,
        "rows": len(rows),
        "sample_keys": sorted(rows[0].keys()) if rows else [],
    }


def _attempt(label: str, fn) -> dict[str, Any]:
    """Run one probe; a failure is a *result*, not the end of the run.

    A 4xx here is informative -- it says the paper host does not serve
    that TR -- and `_get_with_retry` deliberately does not retry one, so
    recording it costs nothing from the token allowance the live
    `kis-paper` JVM shares.
    """
    try:
        return {"probe": label, "ok": True, **fn()}
    except KisKlinesError as exc:
        return {"probe": label, "ok": False, "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("paper", "real"), default="paper")
    parser.add_argument("--code", default=SAMSUNG)
    parser.add_argument(
        "--hour",
        default="153000",
        help="HHMMSS end of the requested minute window",
    )
    parser.add_argument(
        "--dates",
        default=",".join(DEFAULT_RETENTION_DATES),
        help=(
            "comma-separated YYYYMMDD past sessions to ask for intraday bars on. "
            "Must be real KRX trading days, or an empty result says nothing"
        ),
    )
    args = parser.parse_args(argv)

    key = os.environ.get("KIS_APP_KEY")
    sec = os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        print("KIS_APP_KEY / KIS_APP_SECRET must both be set", file=sys.stderr)
        return 2
    # Presence and length only -- never the value. CLAUDE.md, "A real
    # credential-handling incident".
    print(f"credentials present: app_key len={len(key)} app_secret len={len(sec)}")

    host = PAPER_HOST if args.host == "paper" else REAL_HOST
    session = KisSession(key, sec, host=host)

    results = [
        _attempt("investor_samsung", lambda: probe_investor(session, args.code)),
        _attempt("investor_hynix", lambda: probe_investor(session, SK_HYNIX)),
        _attempt("minutes", lambda: probe_minutes(session, args.code, args.hour)),
        _attempt("foreign_institution_total", lambda: probe_foreign_total(session)),
    ]
    for date in [d.strip() for d in args.dates.split(",") if d.strip()]:
        results.append(
            _attempt(
                f"minutes_on_{date}",
                lambda d=date: probe_minutes_on_date(session, args.code, d, args.hour),
            )
        )

    print(json.dumps({"host": args.host, "results": results}, ensure_ascii=False, indent=2))
    return 0 if any(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
