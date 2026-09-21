"""The KRX **delisted** universe -- the half `krx_universe` cannot supply.

`krx_universe.py` snapshots who is listed *today*, and says plainly in its
own docstring that this "fixes the future, not the past": a snapshot taken
now cannot say who was listed in 2019. That gap is the survivorship
problem CLAUDE.md names as open, and
`.planning/rd-d-discovery-mode-and-the-full-universe.md` §2.2 records it as
the blocker on any full-universe scan.

**It is not open any more, and this module is why.** KRX's own data portal
publishes a finder of delisted issues, and KIS serves those names' daily
bars right up to their final session. Measured 2026-09-20; full account in
`.planning/rd-w-the-delisted-universe.md`.

Three facts this module is built on, each measured rather than assumed:

1. **`finder_listdelisu` is delisted-ONLY.** 4,182 codes against
   `finder_stkisu`'s 2,869 listed ones, and the two sets have **zero
   overlap** -- checked, not inferred from the name. 유가증권 2,133 /
   코스닥 1,928 / 코넥스 121.
2. **KIS retains the history.** 한진해운 `117930`, delisted 2017-02, still
   answers with its final sessions ending **2017-03-06 at 12 KRW** -- the
   collapse itself, which is exactly the observation survivorship bias
   deletes. 21 of 25 randomly sampled plain 6-digit delisted codes are
   retained, with final bars spanning 2000 to 2026.
3. **The delisting date comes free from the row cap.** KIS caps an equity
   daily request at 100 rows and keeps the **newest** (CLAUDE.md, KIS
   section). So a deliberately over-wide window returns a delisted name's
   *last* 100 sessions, and the last of those is its final trading day.
   `fetch_daily_page` refuses a capped page on purpose, which is right for
   a backfill and wrong here, so this module asks directly and treats the
   cap as the mechanism.

**Two traps, both real and both cheap to fall into:**

- **The finder mixes instruments.** Of 4,182 codes only **2,350** are plain
  6-digit; the rest are rights (`3686001G`), 신주 (`007121`), fund classes
  (`702071KB`) and similar. Preferred shares and SPACs *do* have plain
  codes and are in there too. `plain_codes()` is the floor, not the filter
  -- a scan wanting common stock must still exclude what it does not want.
- **Delisting is not failure.** 루트로닉 left at 36,700 and 락앤락 at
  8,660, both take-privates. A rule that books −100% on delisting is wrong
  in the opposite direction to survivorship bias, and just as wrong.

**No credentials, and nothing here can place an order.** The finder is a
public endpoint on KRX's portal reached over HTTP with a session cookie
and no account at all.

Run:

    python -m data.krx_delisted --snapshot
    python -m data.krx_delisted --list | head
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from data._paths import DEFAULT_DB_PATH
from data.krx_instrument import (
    InstrumentClass,
    instrument_class,
    is_common_stock,
    is_reit,
    is_spac,
)
from data.store import (
    connect,
    fetch_krx_delisted,
    krx_delisted_snapshots,
    upsert_krx_delisted,
)

PORTAL = "http://data.krx.co.kr"
JSON_ENDPOINT = f"{PORTAL}/comm/bldAttendant/getJsonData.cmd"
#: The loader page exists only to obtain a session. Every `getJsonData`
#: POST without one answers `HTTP 400 LOGOUT` -- not an auth failure, a
#: missing `JSESSIONID`, and the body says so rather than the status code.
LOADER = f"{PORTAL}/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201"

#: **Verified by content, not by name.** The listed finder is fetched
#: alongside it and the two are asserted disjoint, because a `bld` id that
#: silently answers with the wrong dataset is this project's most-repeated
#: API trap (CLAUDE.md: a guessed KIS contract code returns `rt_cd=0` with
#: zero rows, indistinguishable from a dead instrument).
DELISTED_BLD = "dbms/comm/finder/finder_listdelisu"
LISTED_BLD = "dbms/comm/finder/finder_stkisu"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TIMEOUT_S = 60.0

SHORT_CODE_LEN = 6


class KrxDelistedError(RuntimeError):
    """The delisted universe could not be enumerated."""


@dataclass(frozen=True)
class Delisting:
    code: str
    market: str
    name: str
    #: The finder's `full_code`, i.e. the 12-character 표준코드 (ISIN).
    #: The only field here that separates 보통주 from 우선주 -- 312 of
    #: the 2,350 plain-code delisted issues are not common stock. See
    #: `data.krx_instrument`.
    standard_code: str = ""


def _opener() -> urllib.request.OpenerDirector:
    """A cookie-carrying opener with the portal session already primed."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", USER_AGENT), ("Accept-Language", "ko-KR,ko;q=0.9")]
    try:
        with opener.open(LOADER, timeout=TIMEOUT_S) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise KrxDelistedError(
            f"could not open a KRX portal session: {type(exc).__name__}"
        ) from None
    if not any(cookie.name == "JSESSIONID" for cookie in jar):
        raise KrxDelistedError(
            "the KRX loader page returned no JSESSIONID; every data request "
            "would answer HTTP 400 LOGOUT"
        )
    return opener


def _finder(opener: urllib.request.OpenerDirector, bld: str) -> list[dict]:
    body = urllib.parse.urlencode(
        {"bld": bld, "locale": "ko_KR", "mktsel": "ALL", "typeNo": "0", "searchText": ""}
    ).encode()
    request = urllib.request.Request(
        JSON_ENDPOINT,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": LOADER,
            "Origin": PORTAL,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    try:
        with opener.open(request, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise KrxDelistedError(f"{bld} request failed: {type(exc).__name__}") from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # The portal answers a bad `bld` with a 200 carrying HTML, so a
        # status check alone would take that for success.
        raise KrxDelistedError(
            f"{bld} answered HTTP 200 with a non-JSON body; the bld id is "
            f"probably not a finder"
        ) from None
    rows = payload.get("block1")
    if not isinstance(rows, list) or not rows:
        raise KrxDelistedError(f"{bld} returned no block1 rows")
    return rows


def fetch_delistings() -> list[Delisting]:
    """Every delisted issue KRX's finder knows about.

    **Fails closed on overlap with the listed finder.** The one thing that
    would quietly ruin this record is the endpoint changing to return
    listed names too -- every such name would then be treated as delisted
    on a date it was still trading. Two requests instead of one is a cheap
    price for a check the data itself can answer.
    """
    opener = _opener()
    delisted_rows = _finder(opener, DELISTED_BLD)
    listed_rows = _finder(opener, LISTED_BLD)

    listed = {str(row.get("short_code", "")) for row in listed_rows}
    delistings: list[Delisting] = []
    for row in delisted_rows:
        code = str(row.get("short_code", "")).strip()
        name = str(row.get("codeName", "")).strip()
        market = str(row.get("marketName", "")).strip()
        standard_code = str(row.get("full_code", "")).strip()
        if not code:
            continue
        delistings.append(Delisting(code, market, name, standard_code))

    overlap = {d.code for d in delistings} & listed
    if overlap:
        raise KrxDelistedError(
            f"{len(overlap)} codes appear in BOTH the delisted and listed "
            f"finders (e.g. {sorted(overlap)[:5]}). {DELISTED_BLD} is supposed "
            f"to be delisted-only; treating a listed name as delisted would "
            f"corrupt the survivorship record in the opposite direction"
        )
    if not delistings:
        raise KrxDelistedError("the delisted finder parsed to zero rows")
    return delistings


def plain_codes(delistings: list[Delisting]) -> list[Delisting]:
    """The plain 6-digit codes, which is what an equity request accepts.

    **A floor, not a filter.** It drops rights, 신주 and fund classes,
    whose codes carry letters or extra digits; it keeps preferred shares
    and SPACs, which look exactly like common stock here. The finder
    carries no instrument-type field at all, so the remaining distinction
    has to come from somewhere else.
    """
    return [d for d in delistings if len(d.code) == SHORT_CODE_LEN and d.code.isdigit()]


def common_stock(delistings: list[Delisting]) -> list[Delisting]:
    """The plain-coded issues whose ISIN positively says 보통주.

    **This is the filter `plain_codes` is only the floor for**, and it is
    not a formality: **312 of the 2,350 plain 6-digit delisted codes are
    preferred lines or foreign listings** (measured 2026-09-20). A scan
    that skips this ranks 삼성전자우-shaped names beside their own commons.

    Fails closed: an ISIN this cannot read is dropped rather than assumed
    common. Use `krx_instrument.classify` where the *count* of those
    matters, because dropping names silently is itself a survivorship
    hazard.

    **Four filters, and only two of them are structural.** Measured
    2026-09-21 over the 2,353 plain codes: the ISIN's issue type removes
    **314** 우선주, its instrument class removes the ETNs, funds and DRs,
    and then two **name** rules remove **179 SPACs and 14 REITs** --
    weaker evidence, kept separate in `krx_instrument` for that reason.
    A SPAC passes every structural test because it legally is a 주식회사
    with a `KR7...0` ISIN. **2,353 -> 1,846.**
    """
    return [
        d
        for d in plain_codes(delistings)
        if is_common_stock(d.standard_code)
        and instrument_class(d.standard_code) is InstrumentClass.STOCK_LIKE
        and not is_spac(d.name)
        and not is_reit(d.name)
    ]


def snapshot(conn, snapshot_date: str | None = None) -> tuple[str, int, int]:
    """Record today's delisted universe. `(date, rows_written, plain_codes)`.

    Snapshotted by date like `krx_universe`, and for a different reason:
    this list only *grows*, so a dated record makes "when did KRX first
    publish this name as delisted" answerable, which bounds the delisting
    date for any name KIS no longer serves.
    """
    date = (
        dt.datetime.now(dt.timezone.utc).date().isoformat()
        if snapshot_date is None
        else snapshot_date
    )
    delistings = fetch_delistings()
    written = upsert_krx_delisted(
        conn,
        date,
        [(d.code, d.market, d.name, d.standard_code) for d in delistings],
    )
    return date, written, len(plain_codes(delistings))


def latest_snapshot_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(snapshot_date) FROM krx_delisted").fetchone()
    return row[0] if row and row[0] else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true", help="fetch and record today")
    group.add_argument("--list", action="store_true", help="print the latest snapshot")
    group.add_argument("--coverage", action="store_true", help="snapshots on record")
    parser.add_argument("--date", help="snapshot date to read (default: latest)")
    parser.add_argument(
        "--plain-only",
        action="store_true",
        help="print only plain 6-digit codes (see plain_codes)",
    )
    args = parser.parse_args(argv)

    conn = connect(args.db_path)
    try:
        if args.snapshot:
            date, written, plain = snapshot(conn)
            print(f"{date}: {written} rows written, {plain} plain 6-digit codes")
            return 0
        if args.coverage:
            rows = krx_delisted_snapshots(conn)
            if not rows:
                print("no snapshots on record", file=sys.stderr)
                return 1
            for date, total, plain in rows:
                print(f"{date}  {total:>5} rows  {plain:>5} plain codes")
            return 0
        date = args.date or latest_snapshot_date(conn)
        if date is None:
            print("no snapshots on record; run --snapshot first", file=sys.stderr)
            return 1
        try:
            for code, market, name, _ in fetch_krx_delisted(conn, date):
                if args.plain_only and not (
                    len(code) == SHORT_CODE_LEN and code.isdigit()
                ):
                    continue
                print(f"{code}\t{market}\t{name}")
        except BrokenPipeError:
            # `--list | head` is this module's own documented usage, and
            # 4,182 lines into a closed pipe is a traceback rather than a
            # clean exit without this. Closing the fd stops the
            # interpreter re-raising at shutdown.
            try:
                sys.stdout.close()
            finally:
                return 0
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
