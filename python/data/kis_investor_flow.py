"""KRX 투자자별 매매동향 -- the one data source Korea has that crypto does not.

The Korea Exchange **mandatorily discloses**, daily and per stock, the
buying and selling of 개인 / 기관 / 외국인. There is no crypto equivalent
and no US equity equivalent either -- the entire US literature on retail
order flow exists because researchers had to *infer* it (Boehmer, Jones,
Zhang & Zhang's 2021 sub-penny algorithm; Kelley & Tetlock's single
wholesaler). In Korea it is a legal disclosure.
`.planning/rd-c-mechanism-catalogue.md` §6.

**This runs on a schedule because it cannot be backfilled.** Measured
2026-09-14: `inquire-investor` returns **exactly 30 rows** and **takes no
date parameter at all**, so there is no request that reaches day 31. The
window is a rolling lookback, which means a collector started within ~30
trading days loses nothing *from its start date forward* -- but every day
it does not run is a day that eventually falls off the far end and is gone.
`.planning/rd-c-kis-flow-probe-result.md`.

**Collect after the KRX close.** 투자자별 매매동향 is 가집계 (provisional)
during the session and finalised only after it. A mid-session snapshot
records a number that will change -- a silent data-quality failure, and
`INSERT OR IGNORE` would then *keep the provisional one* on a later rerun.
Run after 15:30 KST (06:30 UTC).

**This module cannot place an order and must stay that way**, enforced
structurally by `python/tests/test_kis_probe_cannot_trade.py`: quotation
TR ids only, no account number, nothing imported from the trading path.
Credentials come from the environment and are never logged -- presence and
length only.

**Paper host only**, with no flag able to point it elsewhere, following
`kis_flow_probe.py`.

Run:

    python -m data.kis_investor_flow --symbols 005930,000660
    python -m data.kis_investor_flow --universe          # needs a krx_universe snapshot
    python -m data.kis_investor_flow --coverage
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from data._paths import DEFAULT_DB_PATH
from data.kis_klines import (
    PAPER_HOST,
    KisKlinesError,
    KisSession,
    _get_with_retry,
    equity_storage_symbol,
    trading_date_to_ms,
)
from data.store import (
    PositioningRow,
    connect,
    fetch_krx_universe,
    positioning_coverage,
    upsert_positioning,
)

INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
TR_INVESTOR = "FHKST01010900"

PERIOD = "1d"
METRIC_PREFIX = "krx_investor_flow"

# KIS's own column prefixes for the three investor types the disclosure
# separates. The English names are this project's, chosen so a metric
# string is readable without the KIS field dictionary to hand.
INVESTOR_TYPES = {"prsn": "individual", "frgn": "foreign", "orgn": "institution"}

# **Primitives are stored; the net is derived and checked, not stored.**
# `ntby_qty` is definitionally `shnu_vol - seln_vol`, so storing it too
# would be 50% more rows carrying no information -- and, worse, would let
# the two disagree silently. It is instead recomputed on ingest and a
# mismatch fails closed (`_verify_net`), which turns a redundancy into a
# real integrity check on KIS's own arithmetic.
SIDES = {"shnu": "buy", "seln": "sell"}
UNITS = {"vol": "qty", "tr_pbmn": "value"}

# **`tr_pbmn` (거래대금) is denominated in 백만원, not 원**, and KIS does not
# document this anywhere. Measured 2026-09-14 against 삼성전자 and confirmed
# two independent ways: the implied price `value x 1e6 / qty` lands within a
# few percent of that day's close on every overlapping day (259,516 vs
# 261,000; 254,576 vs 260,000; 266,670 vs 266,000 ...), and the three
# investor types' summed buy value comes to a steady 86-90% of the same
# day's `klines.quote_volume`, which is in 원.
#
# Stored **converted to 원**, so this column and `klines.quote_volume` are
# directly comparable and no downstream reader has to know the quirk. Left
# raw it is off by a factor of a million -- an error that would not look
# wrong, merely small.
TR_PBMN_TO_KRW = 1_000_000

# The residual 10-14% is 기타법인 / 내국인 / 국가·지자체, which
# `inquire-investor` does not break out -- it reports only these three.
# So these do NOT sum to market turnover, and a "share of volume"
# computed from them alone is overstated by roughly that much.
INVESTOR_TYPES_COVER_ALL_TURNOVER = False

# Throttle. Not calibrated to a measured rate limit -- KIS's per-second
# quota for the paper host has never been measured -- but set well under
# any plausible one, because the cost of being wrong is a failed
# `kis-paper` tick and `kis-paper`'s uptime is a Gate A criterion. The
# full universe is 2,718 names, so 1/s is 45 minutes inside an overnight
# window that has hours of slack. See rd-d §3.
DEFAULT_THROTTLE_S = 1.0


class InvestorFlowError(RuntimeError):
    """Flow could not be collected. Carries no response body -- a real KIS
    response can embed account identifiers."""


@dataclass(frozen=True)
class FlowPoint:
    date: str
    metric: str
    value: str


def metric_name(investor: str, side: str, unit: str) -> str:
    """`krx_investor_flow.individual.buy.value`, and so on."""
    return f"{METRIC_PREFIX}.{investor}.{side}.{unit}"


def _int(raw: object, field: str, date: str) -> int:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise InvestorFlowError(f"{field} missing for {date}")
    try:
        return int(str(raw).strip())
    except ValueError:
        raise InvestorFlowError(f"{field} is not an integer for {date}") from None


def _verify_net(row: dict[str, Any], date: str) -> None:
    """KIS returns the net alongside the gross legs. They must agree.

    A silent disagreement would mean one of the two is not what its name
    says, and every downstream statistic built on the gross legs -- which
    is the whole reason for preferring them (Kelley & Tetlock's
    market-order/limit-order split needs the decomposition) -- would be
    measuring something else.
    """
    for prefix in INVESTOR_TYPES:
        buy = _int(row.get(f"{prefix}_shnu_vol"), f"{prefix}_shnu_vol", date)
        sell = _int(row.get(f"{prefix}_seln_vol"), f"{prefix}_seln_vol", date)
        net = _int(row.get(f"{prefix}_ntby_qty"), f"{prefix}_ntby_qty", date)
        if buy - sell != net:
            # Field and date only, never the magnitudes -- the same shape
            # every other exception in the KIS data path uses. Whether
            # *this* endpoint's aggregate volumes are sensitive is exactly
            # the per-endpoint judgment that drifts, so the rule is applied
            # uniformly instead. Re-running the probe recovers the numbers.
            raise InvestorFlowError(
                f"{prefix} net does not equal buy minus sell on {date}"
            )


def parse_rows(body: dict[str, Any]) -> list[FlowPoint]:
    """`output` -> one `FlowPoint` per (date, investor, side, unit)."""
    if str(body.get("rt_cd", "")) != "0":
        raise InvestorFlowError(
            f"request rejected with rt_cd={body.get('rt_cd')!r}"
        )
    out = body.get("output")
    if not isinstance(out, list):
        raise InvestorFlowError("output missing or not a list")
    if not out:
        # `rt_cd=0` with an empty `output` is the exact silent-failure
        # shape this project already documented for the intraday endpoint
        # (an out-of-range date returns success and zero rows). Left
        # unguarded here it would write nothing, count no failure, and
        # exit 0 -- a clean-looking run on a series that cannot be
        # refetched tomorrow.
        raise InvestorFlowError("output is empty; KIS returned success with no rows")

    points: list[FlowPoint] = []
    for row in out:
        if not isinstance(row, dict):
            raise InvestorFlowError("output row is not an object")
        date = str(row.get("stck_bsop_date", "")).strip()
        if len(date) != 8 or not date.isdigit():
            raise InvestorFlowError(f"unusable stck_bsop_date {date!r}")
        _verify_net(row, date)
        for prefix, investor in INVESTOR_TYPES.items():
            for side_key, side in SIDES.items():
                for unit_key, unit in UNITS.items():
                    field = f"{prefix}_{side_key}_{unit_key}"
                    value = _int(row.get(field), field, date)
                    if unit_key == "tr_pbmn":
                        value *= TR_PBMN_TO_KRW
                    points.append(
                        FlowPoint(date, metric_name(investor, side, unit), str(value))
                    )
    return points


KST = dt.timezone(dt.timedelta(hours=9))
#: KRX's regular session close. Before it, the current trading date's
#: 투자자별 매매동향 is 가집계 -- provisional, and it will change.
KRX_CLOSE_KST = dt.time(15, 30)


def provisional_date(now: dt.datetime | None = None) -> str | None:
    """The `YYYYMMDD` whose flow is still provisional, or `None`.

    **Dropping the row is the fix, not warning about it.** `positioning`
    is keyed on `(symbol, metric, period, timestamp_ms)` and
    `upsert_positioning` is `INSERT OR IGNORE`, so a provisional row
    written at noon is never replaced by the finalised one after the
    close -- it is wrong permanently, on a series that cannot be
    refetched. So it is never written in the first place.

    (If one ever *were* stored by an older version, removing it would
    need an explicit update-on-conflict policy; there is deliberately no
    such path, because silently overwriting stored observations is a
    worse default than refusing to create the problem.)
    """
    now = now or dt.datetime.now(KST)
    now = now.astimezone(KST)
    return now.strftime("%Y%m%d") if now.time() < KRX_CLOSE_KST else None


def fetch_flow(session: KisSession, code: str) -> list[FlowPoint]:
    url = (
        f"{session.host}{INVESTOR_PATH}?"
        + urllib.parse.urlencode(
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        )
    )
    try:
        body = _get_with_retry(url, session.headers(TR_INVESTOR))
    except KisKlinesError as exc:
        raise InvestorFlowError(str(exc)) from None
    return parse_rows(body)


def sync_symbol(
    session: KisSession, conn, code: str, now: dt.datetime | None = None
) -> int:
    """Collect one symbol. Returns rows newly written (0 if all present)."""
    points = fetch_flow(session, code)
    skip = provisional_date(now)
    if skip is not None:
        points = [p for p in points if p.date != skip]
        if not points:
            raise InvestorFlowError(
                f"every returned row is the provisional {skip}; nothing final to store"
            )
    rows = [
        PositioningRow(
            metric=p.metric,
            period=PERIOD,
            timestamp_ms=trading_date_to_ms(p.date),
            value=p.value,
        )
        for p in points
    ]
    return upsert_positioning(conn, equity_storage_symbol(code), rows)


def resolve_symbols(conn, args) -> list[str]:
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    date = args.universe_date
    if date is None:
        row = conn.execute("SELECT MAX(snapshot_date) FROM krx_universe").fetchone()
        date = row[0] if row and row[0] else None
    if date is None:
        raise InvestorFlowError(
            "--universe needs a krx_universe snapshot; "
            "run `python -m data.krx_universe --snapshot` first"
        )
    return [code for code, _, _, _ in fetch_krx_universe(conn, date)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--symbols", help="comma-separated 6-digit codes")
    source.add_argument("--universe", action="store_true", help="every listed common stock")
    source.add_argument("--coverage", action="store_true", help="what has accumulated")
    parser.add_argument("--universe-date", help="krx_universe snapshot to read")
    parser.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE_S)
    args = parser.parse_args(argv)

    conn = connect(args.db_path)
    try:
        if args.coverage:
            rows = [r for r in positioning_coverage(conn) if r[1].startswith(METRIC_PREFIX)]
            if not rows:
                print("no investor flow collected yet", file=sys.stderr)
                return 1
            symbols = {r[0] for r in rows}
            earliest = min(r[4] for r in rows)
            latest = max(r[5] for r in rows)
            print(f"{len(symbols)} symbols, {len(rows)} series")
            print(f"earliest {earliest}  latest {latest}")
            return 0

        key = os.environ.get("KIS_APP_KEY")
        sec = os.environ.get("KIS_APP_SECRET")
        if not key or not sec:
            print("KIS_APP_KEY / KIS_APP_SECRET must both be set", file=sys.stderr)
            return 2
        print(f"credentials present: app_key len={len(key)} app_secret len={len(sec)}")

        codes = resolve_symbols(conn, args)
        session = KisSession(key, sec, host=PAPER_HOST)

        written = failed = 0
        for i, code in enumerate(codes):
            if i:
                time.sleep(args.throttle)
            try:
                written += sync_symbol(session, conn, code)
            except InvestorFlowError as exc:
                # One bad symbol must not abandon the remaining 2,717. The
                # data is non-backfillable, so a partial day beats no day.
                failed += 1
                print(f"  {code}: {exc}", file=sys.stderr)
        print(f"{len(codes)} symbols, {written} rows written, {failed} failed")
        return 1 if failed == len(codes) else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
