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

**Off-hours are refused, and there is no flag that both bypasses the
session and writes.** KRX trades continuously 09:00-15:20 KST, and KIS
answers outside those hours with the **last** book rather than an empty
one -- measured at 23:00 KST, which returned 삼성전자 at 253,500/253,000.
So an off-hours sample is not missing data, it is plausible data from a
different market state, and pooling it into a median biases the result
without ever looking wrong. `--probe` fetches and prints without storing,
which is the only way to look; an earlier `--force` skipped the check and
stored anyway, and wrote sixteen contaminated rows during its own
verification before they were deleted.

**A failure on one instrument does not cost the rest of the pass.** An
order book cannot be backfilled at any price, so one rejected call
aborting the run would permanently lose this tick for every symbol after
it, for a reason unrelated to them. Failures are isolated, named and
counted; the process exits non-zero only when *nothing* was sampled, or
when futures were asked for and not one contract was quoted.

Run:

    python -m data.krx_quote_sampler --symbols 005930,000660 --futures
    python -m data.krx_quote_sampler --symbols 005930 --futures --probe
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

#: Which block each endpoint answers in -- measured, not symmetric. Pinned
#: rather than searched, because the quantity fields are unprefixed on both
#: and so a futures block satisfies three quarters of a spot field map.
SPOT_BLOCK = "output1"
FUTURES_BLOCK = "output2"

SPOT_PREFIX = "KRX:"
FUTURES_PREFIX = "KRX-FUT:"

#: `snapshot`, not a duration. Every other series in this table is an
#: aggregate over a window; this one is an instant, and naming it "1m"
#: would invite a later reader to average it as though it were a bar.
PERIOD = "snapshot"

METRIC_PREFIX = "krx_quote"

#: What gets stored. The four quote numbers, plus **how old the book was**
#: when it was sampled.
#:
#: `accepted_age_s` is stored rather than used to filter, and that is a
#: deliberate choice against the obvious alternative of dropping an old
#: book. Dropping one systematically removes the names whose quotes do not
#: refresh -- the thin ones -- and biases every spread median **downward**,
#: which is the flattering direction and the error this whole arc has been
#: about. An old resting book is also still the book: it is what a taker
#: would actually cross right now, which is precisely the cost being
#: measured. So nothing is discarded, the age travels with the quote, and
#: the analysis that computes a median decides what freshness it wants --
#: a decision it can revisit, where a dropped sample is gone for good on a
#: series nothing can backfill.
PRIMITIVES = ("ask1", "bid1", "ask1_qty", "bid1_qty", "accepted_age_s")

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

    Weekday and clock only. **Holidays are handled downstream, by
    `is_stale`, not here** -- and the reason is a correction.

    An earlier version of this docstring claimed holidays needed no check
    because "a holiday returns an empty book". **That was refuted by this
    module's own measurement**: KIS answers outside the session with the
    LAST book, not an empty one, so a weekday holiday at 10:00 returns
    the previous session's quotes and every check here passes.

    A holiday table would fix only holidays, and `KrxMarketCalendar`
    already records its own as incomplete for moving lunar dates. Asking
    the book whether it is current fixes holidays, unscheduled closures,
    a halted symbol and a venue outage with one test, and needs no table
    to be kept up to date.
    """
    now = now or dt.datetime.now(KST)
    if now.weekday() >= 5:
        return False
    # Half-open. 15:20:00 is the closing call auction's FIRST instant, not
    # the continuous session's last -- `KrxMarketCalendar.isOpen` uses the
    # same exclusive boundary, and a sampler disagreeing with the venue
    # calendar by one second is a disagreement nobody would look for.
    return SESSION_OPEN <= now.timetz().replace(tzinfo=None) < SESSION_CLOSE


#: How far ahead of the wall clock a book's acceptance time may sit
#: before it is called stale. Absorbs clock skew between this machine and
#: the venue; far below the ~25 minutes a stale book is actually off by,
#: since the previous session's last quote is at or after 15:20 while any
#: sample is taken before it.
STALE_TOLERANCE_S = 300


def _seconds(hhmmss: object) -> int | None:
    """`"120000"` -> 43200, or `None` for anything unusable.

    **Type-checked, not just format-checked.** `aspr_acpt_hour` reaches
    here straight off the wire, and a JSON number rather than a string
    would make `len()` raise a `TypeError` that `attempt` does not catch
    -- aborting the whole pass and losing every later symbol's sample.
    That is the same failure this module already fixed once for the fetch
    itself, re-entering through a field nobody would think of as risky.
    """
    if not isinstance(hhmmss, str) or len(hhmmss) != 6 or not hhmmss.isdigit():
        return None
    h, m, sec = int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:])
    if h > 23 or m > 59 or sec > 59:
        return None
    return h * 3600 + m * 60 + sec


def accepted_age_s(
    accepted_hhmmss: object, now: dt.datetime | None = None
) -> float | None:
    """How long before `now` this book's last quote was accepted.

    **Never negative.** `is_stale` tolerates an acceptance time a few
    minutes ahead of the clock, because that is skew between this machine
    and the venue rather than a stale book -- so a book accepted at 12:04
    against a 12:00 sample is correctly kept, and its *age* is zero, not
    minus four minutes. Storing a negative age would put a value into the
    series that the quantity cannot take, and any later filter written as
    `age <= threshold` would silently treat it as the freshest sample
    there is.

    `None` when the field is unreadable, in which case no age row is
    written and the quote rows still are.
    """
    accepted = _seconds(accepted_hhmmss)
    if accepted is None:
        return None
    now = now or dt.datetime.now(KST)
    elapsed = now.hour * 3600 + now.minute * 60 + now.second - accepted
    return float(max(elapsed, 0))


def is_stale(accepted_hhmmss: object, now: dt.datetime | None = None) -> bool:
    """Is this book from an earlier session rather than from now?

    KIS carries 호가접수시각 (`aspr_acpt_hour`) but **no date**, so the
    test is on the clock alone -- and that is sufficient precisely
    because sampling only ever happens inside 09:00-15:20. A book left
    over from a previous session carries that session's last acceptance
    time, which is at or after the 15:20 close (15:45 and 20:00 were both
    observed), and so lies **ahead** of any instant this sampler runs at.

    So "accepted later in the day than it is now" means the book is not
    from today. That one test covers a weekday holiday, an unscheduled
    closure, a halted symbol and a venue outage, none of which a holiday
    table would catch beyond the first.

    An unreadable or absent time returns `False` -- not stale. The field
    is a check on the book, not the book itself, and refusing to store a
    real quote because a secondary field changed shape would lose data
    that cannot be re-fetched.

    **The blind spot, stated rather than glossed.** A book whose last
    acceptance was at the close itself is only minutes ahead of a sample
    taken late in the window, so it falls inside the tolerance: accepted
    15:20:00 against a 15:19 sample is not flagged. The two values
    actually observed from a closed market -- 15:45 and 20:00 -- are
    caught at every sampling instant, and the auction and after-hours
    sessions are why a genuinely stale book tends to carry one of those
    rather than 15:20. It is a narrowing, not an elimination, and pairing
    it with the weekday-and-clock gate above is what makes the residue
    small.

    **Only the forward direction is refused, and that is deliberate.** A
    book accepted at 10:00 and sampled at 12:00 is two hours old and is
    **not** rejected: on a thin name that is simply what the book is, and
    it is what a taker would cross right now, which is the cost this
    measures. Refusing it would drop exactly the names whose quotes do not
    refresh and bias every spread median downward -- a selection effect in
    the flattering direction, worse than the staleness it removes. The age
    is stored instead (`accepted_age_s`), so the analysis filters and
    nothing is lost from a series nothing can backfill.

    The cost of that choice, stated: on a weekday holiday, a thin name
    whose previous session ended early enough reads as merely old rather
    than stale, and is stored. The forward test catches every name whose
    previous session ran to the close, which is most of them.
    """
    accepted = _seconds(accepted_hhmmss)
    if accepted is None:
        return False
    now = now or dt.datetime.now(KST)
    seconds_now = now.hour * 3600 + now.minute * 60 + now.second
    return accepted > seconds_now + STALE_TOLERANCE_S


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
    price_prefix: str, qty_prefix: str = "", block_key: str = "output1",
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

    # **One block, named by the caller.** Searching both would let a spot
    # request read a block it did not ask for: the quantity fields are
    # unprefixed on BOTH endpoints, so a futures-shaped `output2` satisfies
    # a spot field map for three of its four fields and differs only in the
    # price key. Since the block is a measured property of each endpoint
    # (spot output1, futures output2), pin it rather than search.
    block = payload.get(block_key)
    if isinstance(block, dict):
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
            # 호가접수시각, carried so the caller can ask whether this book
            # is from now. The raw string is not stored; its derived age
            # is -- see `PRIMITIVES`.
            book["accepted"] = block.get("aspr_acpt_hour")  # type: ignore[assignment]
            return book  # type: ignore[return-value]
    return None


def rows_for(book: dict[str, float], contract: str, at_ms: int) -> list[PositioningRow]:
    """One `PositioningRow` per primitive, with the contract in the metric.

    The contract code rides in the metric rather than the symbol so that a
    futures series stays one symbol across a roll -- see the module
    docstring -- while still recording exactly which contract was sampled.
    """
    suffix = f".{contract}" if contract else ""
    # `accepted` rides along on the book so the caller can test freshness;
    # it is a check ON the quote, not a quote, and storing it would put a
    # non-numeric value into a column every other row parses as a float.
    return [
        PositioningRow(
            metric=f"{METRIC_PREFIX}.{name}{suffix}",
            period=PERIOD,
            timestamp_ms=at_ms,
            value=repr(value),
        )
        for name, value in sorted(book.items())
        if name in PRIMITIVES
    ]


def front_month(master: FuturesMaster, code: str) -> str | None:
    """The earliest expiry the master still lists for `code`.

    **Resolved per run, never hardcoded.** The master carries only live
    contracts, so its minimum expiry is the front month by construction --
    and a hardcoded one degrades in the worst possible way once it rolls:
    an expired contract answers with an empty book, `sample_book`
    correctly reports that as "not quoted", and the collector goes on
    exiting 0 while silently recording spot alone. Nothing in the log
    would say so.
    """
    listed = master.contracts.get(code)
    return min(listed) if listed else None


def sample_once(
    session: KisSession, conn, codes: list[str], master: FuturesMaster | None,
    *, pause_s: float = 0.25, now_ms: int | None = None, store: bool = True,
    now: dt.datetime | None = None,
) -> tuple[dict[str, dict[str, float]], list[str], list[str]]:
    """One pass. Returns `(books by symbol, failures, stale)`.

    **A failure on one instrument must not cost every later one.** An
    order book cannot be backfilled at any price, so a single rejected
    call aborting the pass would throw away this tick's sample for every
    symbol after it -- permanently, and for a reason unrelated to them.
    Each fetch is isolated, its failure named and recorded, and the pass
    continues.

    `store=False` fetches and returns without writing, which is the only
    way to look at an out-of-session book without contaminating a series
    whose entire purpose is an unbiased median.
    """
    at_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    books: dict[str, dict[str, float]] = {}
    failures: list[str] = []
    stale: list[str] = []

    def attempt(symbol: str, contract: str, fetch) -> None:
        try:
            book = fetch()
        except (KisKlinesError, OSError) as exc:
            # The symbol is named because the alternative -- one line
            # saying a pass failed -- cannot distinguish a dead contract
            # from a rejected key.
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
            return
        if not book:
            return
        if is_stale(book.get("accepted"), now):  # type: ignore[arg-type]
            # A leftover book from an earlier session -- a weekday
            # holiday, an unscheduled closure, a halted symbol. Recorded
            # separately from a failure: nothing went wrong, the market
            # simply was not open for this instrument.
            stale.append(f"{symbol}: accepted {book.get('accepted')}")
            return
        age = accepted_age_s(book.get("accepted"), now)  # type: ignore[arg-type]
        if age is not None:
            book["accepted_age_s"] = age
        books[symbol] = book
        if store:
            upsert_positioning(conn, symbol, rows_for(book, contract, at_ms))

    for code in codes:
        attempt(
            SPOT_PREFIX + code, "",
            lambda code=code: sample_book(
                session, SPOT_QUOTE, TR_SPOT, "J", code, "", "", SPOT_BLOCK
            ),
        )
        time.sleep(pause_s)

        if master is None:
            continue
        expiry = front_month(master, code)
        if expiry is None:
            # A name with no listed future is a real answer, not a failure:
            # two of the KR-10 had none at all (rd-r 1.2).
            continue
        contract = master.contract_code(code, expiry)
        attempt(
            FUTURES_PREFIX + code, contract,
            lambda contract=contract: sample_book(
                session, FUTURES_QUOTE, TR_FUTURES, "JF", contract,
                "futs_", "", FUTURES_BLOCK
            ),
        )
        time.sleep(pause_s)
    return books, failures, stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="", help="comma-separated 6-digit codes")
    ap.add_argument(
        "--futures", action="store_true",
        help="sample the front-month future alongside spot. The expiry is "
             "resolved from the master on every run, never passed in.",
    )
    ap.add_argument("--coverage", action="store_true", help="what has accumulated")
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument(
        "--probe", action="store_true",
        help="fetch and print WITHOUT storing, and without the session "
             "check. The only way to look at an out-of-session book: there "
             "is deliberately no flag that both bypasses the session and "
             "writes, because such a flag puts a stale book into a series "
             "whose whole purpose is an unbiased median.",
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

    if not args.probe and not in_session():
        # Exit 0, not a failure: a cron tick outside the session is the
        # expected case, and a non-zero exit there would train whoever
        # reads the log to ignore real failures.
        print("outside the KRX continuous session; nothing sampled")
        return 0

    key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not secret:
        print("KIS_APP_KEY / KIS_APP_SECRET are not set", file=sys.stderr)
        return 2

    master = parse_master(download_master()) if args.futures else None
    session = KisSession(key, secret)
    books, failures, stale = sample_once(
        session, conn, codes, master, store=not args.probe
    )

    for line in failures:
        print(f"FAILED {line}", file=sys.stderr)
    for line in stale:
        print(f"STALE  {line}", file=sys.stderr)
    print(
        f"{'probed' if args.probe else 'stored'} {len(books)} instrument books"
        + (f", {len(failures)} failed" if failures else "")
        + (f", {len(stale)} stale" if stale else "")
    )

    # **Before the probe's own exit, not after.** A probe where every
    # request was rejected -- a bad key, an expired token, a venue outage
    # -- would otherwise report success, which is the one thing a
    # diagnostic must never do.
    if not books:
        print("no book was sampled at all", file=sys.stderr)
        return 1
    if master is not None and not any(s.startswith(FUTURES_PREFIX) for s in books):
        # The silent-degradation case a fixed expiry used to cause: spot
        # keeps flowing, futures quietly stops, and nothing says so.
        print(
            "futures were requested and NOT ONE contract was quoted; the "
            "front month may have rolled out of the master",
            file=sys.stderr,
        )
        return 1

    if args.probe:
        for symbol, book in sorted(books.items()):
            mid = (book["ask1"] + book["bid1"]) / 2
            print(
                f"  {symbol:<16} {book['bid1']:>12,.0f} x {book['bid1_qty']:>9,.0f}"
                f"  |  {book['ask1']:>12,.0f} x {book['ask1_qty']:>9,.0f}"
                f"   {(book['ask1'] - book['bid1']) / mid * 1e4:>6.1f}bp"
            )
    # Partial failure is reported and tolerated; total failure was
    # refused above. One dead contract is not worth waking anyone for.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
