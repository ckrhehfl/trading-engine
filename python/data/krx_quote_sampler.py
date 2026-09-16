"""Top-of-book snapshots for the Korean universe -- rd-q §8 item 1.

[`rd-q`](../../.planning/rd-q-which-korean-instrument.md) measured the
spot and futures quoted spread **once, at one close**, and said so:
*"One snapshot, one close … it must be re-measured intraday across days
before anything is registered against it."* This is that sampler.

It also closes a second gap rd-q left, found on review of PR #176. rd-q
described its winners' futures books as **"27x deeper"** when the figure
it had was a ratio of `acml_vol` -- **cumulative volume that changed
hands**, which is not depth at all. Resting size at the touch is on the
same wire as the prices and had simply never been read. So this stores
**quantities alongside prices**, and a depth claim becomes measurable
rather than asserted.

## The wire fact that decides the field map, measured 2026-09-16

**On the futures book the prices are `futs_`-prefixed and the quantities
are not.** `futs_askp1` carries the price; the size beside it is
`askp_rsqn1`, with no prefix at all. Reading `futs_askp_rsqn1` -- the
obvious symmetric guess, and this module's first version -- returns
`None` for every level, which a caller that coalesces would record as a
book with prices and no size.

The two endpoints also answer in different blocks: **spot in `output1`,
futures in `output2`**. So neither the prefix nor the block generalises
from one to the other, and both are per-endpoint facts in the sense
CLAUDE.md's KIS section already records for row caps and 백만원.

## What is stored, and why primitives rather than the spread

Four numbers per instrument per sample: `ask1`, `bid1`, `ask1_qty`,
`bid1_qty`. **Not the spread**, which is derived.

That follows `kis_investor_flow.py`'s own stated rule -- *"primitives are
stored; the net is derived and checked, not stored"* -- and it matters
more here than it looks. A spread is `(ask - bid) / mid`, and a stored
spread cannot answer *how much size was actually there*, cannot be
recomputed against the bid or the ask if a later comparison needs that
convention, and cannot be checked for a crossed book after the fact. The
primitives answer all three.

## Where it goes

The `positioning` table, unchanged. Its own docstring already records the
generalization that makes this fit: long format
`(symbol, metric, period, timestamp_ms)` absorbs any shape without a
migration, symbols are venue-namespaced so nothing collides, and the
series exists only from the moment collection starts. A quote snapshot is
exactly that shape.

**Futures are namespaced `KRX-FUT:` by underlying, not by contract code.**
`KRX-FUT:005930`, never `KRX:A11610`. The contract rolls monthly and its
code changes with it, so keying on the code would scatter one economic
series across twelve symbols a year and make a year-long spread history
impossible to read back. The contract actually sampled is recorded in the
metric name's own suffix instead.

## What a sample is and is not

**It is a snapshot, not an average.** Each run records the book at one
instant. A representative figure comes from many samples across many
sessions, which is the entire point -- rd-q's single close is exactly what
this exists to replace.

**An empty book is skipped, not stored as zero.** The distinction rd-q's
own module had to learn twice: an absent quote is a failed measurement,
and a zero-priced one would be recorded as a free instrument.

**Off-hours are refused.** KRX's continuous session is 09:00-15:20 KST
and there is no continuous book outside it. A sample taken at 16:00 would
carry the closing auction's residue or nothing at all, and pooled into a
median it would bias the result without ever looking wrong.

Run:

    python -m data.krx_quote_sampler --symbols 005930,000660
    python -m data.krx_quote_sampler --coverage
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import urllib.parse

from data._paths import DEFAULT_DB_PATH
from data.kis_futures import FuturesMaster, download_master, parse_master
from data.kis_klines import KisKlinesError, KisSession, _get_with_retry
from data.store import PositioningRow, connect, positioning_coverage, upsert_positioning

SPOT_QUOTE = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
TR_SPOT = "FHKST01010200"
FUTURES_QUOTE = "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price"
TR_FUTURES = "FHMIF10010000"

SPOT_PREFIX = "KRX:"
FUTURES_PREFIX = "KRX-FUT:"

#: `snapshot`, not a duration. Every other series in this table is an
#: aggregate over a window; this one is an instant, and naming it "1m"
#: would invite a later reader to average it as though it were a bar.
PERIOD = "snapshot"

METRIC_PREFIX = "krx_quote"

#: KRX's continuous session. The closing call auction runs 15:20-15:30
#: with no continuous trade, so the book there is not the book this
#: measures -- see the module docstring.
SESSION_OPEN = dt.time(9, 0)
SESSION_CLOSE = dt.time(15, 20)
KST = dt.timezone(dt.timedelta(hours=9))


class QuoteSamplerError(KisKlinesError):
    """Raised for a refusal that is about this sampler's own contract."""


def in_session(now: dt.datetime | None = None) -> bool:
    """Is the KRX continuous session open right now?

    Weekday and clock only -- **holidays are not checked**, deliberately.
    A holiday returns an empty book, which `sample_book` already skips as
    a missing measurement, so the failure mode is a wasted call rather
    than a wrong number. Adding a holiday table here would duplicate the
    one `KrxMarketCalendar` already records as incomplete for moving
    lunar dates, and would fail in the more dangerous direction if it
    were wrong.
    """
    now = now or dt.datetime.now(KST)
    if now.weekday() >= 5:
        return False
    return SESSION_OPEN <= now.timetz().replace(tzinfo=None) <= SESSION_CLOSE


def _positive(raw: object, field: str, code: str) -> float | None:
    """A quote field as a positive finite float, or `None` if absent.

    `None` for an empty book -- a real fact about an instrument nobody is
    quoting. **Raises** for a value that is present and unusable, because
    that is a failed measurement rather than an observation, and the two
    were conflated twice in `krx_instrument_cost.py` before being split.
    """
    if raw in (None, "", "0"):
        return None
    if isinstance(raw, bool):
        # `float(True)` is 1.0 -- the only silent conversion into a
        # plausible price.
        raise QuoteSamplerError(f"{code}: {field} is a boolean, not a quote")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise QuoteSamplerError(f"{code}: {field} is not a number: {raw!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise QuoteSamplerError(f"{code}: {field} is not finite: {value}")
    if value < 0:
        raise QuoteSamplerError(f"{code}: {field} is negative: {value}")
    return value or None


def sample_book(
    session: KisSession, path: str, tr: str, division: str, code: str,
    price_prefix: str, qty_prefix: str = "",
) -> dict[str, float] | None:
    """`{ask1, bid1, ask1_qty, bid1_qty}` for one instrument, or `None`.

    `None` when the book is empty -- how KIS answers for an expired or
    unlisted contract, `rt_cd=0` with nothing in it. A partially present
    book (a price with no size, or one side only) is also `None`: half a
    book cannot produce either a spread or a depth figure, and storing
    the half would make the series look denser than it is.
    """
    params = {"FID_COND_MRKT_DIV_CODE": division, "FID_INPUT_ISCD": code}
    url = f"{session.host}{path}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(tr))
    if payload.get("rt_cd") != "0":
        raise QuoteSamplerError(
            f"KIS rejected the book for {code}: rt_cd={payload.get('rt_cd')} "
            f"msg_cd={payload.get('msg_cd')}"
        )

    for key in ("output1", "output2"):
        block = payload.get(key)
        if not isinstance(block, dict):
            continue
        # Two prefixes, not one: see the module docstring. On the futures
        # book `futs_askp1` is the price and `askp_rsqn1` is its size.
        fields = {
            "ask1": f"{price_prefix}askp1",
            "bid1": f"{price_prefix}bidp1",
            "ask1_qty": f"{qty_prefix}askp_rsqn1",
            "bid1_qty": f"{qty_prefix}bidp_rsqn1",
        }
        book = {
            name: _positive(block.get(wire), wire, code)
            for name, wire in fields.items()
        }
        if all(v is not None for v in book.values()):
            if book["ask1"] < book["bid1"]:  # type: ignore[operator]
                raise QuoteSamplerError(
                    f"{code}: crossed book, ask1={book['ask1']} < bid1={book['bid1']}"
                )
            return book  # type: ignore[return-value]
    return None


def rows_for(book: dict[str, float], contract: str, at_ms: int) -> list[PositioningRow]:
    """One `PositioningRow` per primitive, with the contract in the metric.

    The contract code rides in the metric rather than the symbol so that a
    futures series stays one symbol across a roll -- see the module
    docstring -- while still recording exactly which contract was sampled.
    """
    suffix = f".{contract}" if contract else ""
    return [
        PositioningRow(
            metric=f"{METRIC_PREFIX}.{name}{suffix}",
            period=PERIOD,
            timestamp_ms=at_ms,
            value=repr(value),
        )
        for name, value in sorted(book.items())
    ]


def sample_once(
    session: KisSession, conn, codes: list[str], master: FuturesMaster | None,
    expiry: str, *, pause_s: float = 0.25, now_ms: int | None = None,
) -> dict[str, int]:
    """One pass over the universe. Returns per-instrument row counts."""
    at_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    written: dict[str, int] = {}
    for code in codes:
        spot = sample_book(session, SPOT_QUOTE, TR_SPOT, "J", code, "", "")
        if spot:
            written[SPOT_PREFIX + code] = upsert_positioning(
                conn, SPOT_PREFIX + code, rows_for(spot, "", at_ms)
            )
        time.sleep(pause_s)

        if master is None:
            continue
        try:
            contract = master.contract_code(code, expiry)
        except KisKlinesError:
            # A name with no listed future is a real answer, not an error:
            # two of the KR-10 had none at all (rd-r §1.2).
            continue
        futures = sample_book(
            session, FUTURES_QUOTE, TR_FUTURES, "JF", contract, "futs_", ""
        )
        if futures:
            written[FUTURES_PREFIX + code] = upsert_positioning(
                conn, FUTURES_PREFIX + code, rows_for(futures, contract, at_ms)
            )
        time.sleep(pause_s)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="", help="comma-separated 6-digit codes")
    ap.add_argument("--expiry", default="", help="futures expiry YYYYMM; blank = spot only")
    ap.add_argument("--coverage", action="store_true", help="what has accumulated")
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument(
        "--force",
        action="store_true",
        help="sample outside the continuous session. Off by default because a "
             "book outside 09:00-15:20 KST is not the book this measures.",
    )
    args = ap.parse_args(argv)

    conn = connect(args.db_path)
    if args.coverage:
        for row in positioning_coverage(conn):
            if str(row[1]).startswith(METRIC_PREFIX):
                print(row)
        return 0

    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    if not codes:
        print("--symbols is required", file=sys.stderr)
        return 2

    if not args.force and not in_session():
        # Exit 0, not a failure: a cron tick outside the session is the
        # expected case, and a non-zero exit there would train whoever
        # reads the log to ignore real failures.
        print("outside the KRX continuous session; nothing sampled")
        return 0

    key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not secret:
        print("KIS_APP_KEY / KIS_APP_SECRET are not set", file=sys.stderr)
        return 2

    master = parse_master(download_master()) if args.expiry else None
    session = KisSession(key, secret)
    written = sample_once(session, conn, codes, master, args.expiry)
    print(
        f"sampled {len(written)} instrument books, "
        f"{sum(written.values())} rows written"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
