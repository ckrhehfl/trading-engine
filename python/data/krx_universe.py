"""The KRX listed universe, from KIS's own master files, snapshotted daily.

Two jobs, and the second is the reason this runs on a schedule rather than
once:

1. **Enumerate** every KOSPI and KOSDAQ common stock, so a full-universe
   scan has a pool to scan. Measured 2026-09-14: **915 KOSPI + 1,803
   KOSDAQ = 2,718** names with 증권그룹구분코드 `ST`.
2. **Retain a dated snapshot**, because these files list *currently
   listed* symbols only. A per-day selection rule needs every name that
   traded on that day, including ones since delisted, or the scan is
   biased upward by construction -- the delisted names are
   disproportionately the ones that collapsed.

**This fixes the future, not the past.** A snapshot taken today cannot say
who was listed in 2019, and no amount of running it later will. That is
exactly why it starts now, on the same "cannot be backfilled" argument as
`binance_positioning.py` and `kis_investor_flow.py`.
`.planning/rd-d-discovery-mode-and-the-full-universe.md` §2.2.

**No credentials.** These are public static files on KIS's CDN, fetched
over HTTPS with no key, no token, and no account. This module cannot place
an order because it never authenticates at all.

Run:

    python -m data.krx_universe --snapshot
    python -m data.krx_universe --list | head
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

from data._paths import DEFAULT_DB_PATH
from data.store import connect, fetch_krx_universe, krx_universe_snapshots, upsert_krx_universe

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"

# KIS's master files are 완성형 Korean, not UTF-8.
MASTER_ENCODING = "cp949"

# The fixed tail each file carries after the variable-length Korean name,
# and the offset of 증권그룹구분코드 within it. **The two markets differ**,
# which is the trap: KOSPI's tail is 228 bytes and KOSDAQ's is 222, so a
# single offset silently reads the wrong two characters for one of them
# and every group code comes back as whitespace. Measured against the real
# files rather than taken from a sample -- at the wrong offset KOSPI
# yielded `' S'`/`' E'` and KOSDAQ yielded `'  '` for every row.
GROUP_CODE_OFFSET = {"KOSPI": 227, "KOSDAQ": 221}

COMMON_STOCK = "ST"

SHORT_CODE_LEN = 9
STANDARD_CODE_LEN = 12

TIMEOUT_S = 60.0


class KrxUniverseError(RuntimeError):
    """The universe could not be enumerated."""


@dataclass(frozen=True)
class Listing:
    code: str
    market: str
    name: str
    group_code: str


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise KrxUniverseError(
            f"master file download failed: {type(exc).__name__}"
        ) from None


def parse_master(data: bytes, market: str) -> list[Listing]:
    """Parse one `.mst.zip` payload into listings.

    The row layout is 단축코드(9) + 표준코드(12) + 한글종목명(variable) +
    a fixed tail. The name is variable-length, so the tail is located from
    the **end** of the row, never from the start.
    """
    offset = GROUP_CODE_OFFSET.get(market)
    if offset is None:
        raise KrxUniverseError(f"unknown market {market!r}")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if not names:
                raise KrxUniverseError(f"{market} master archive is empty")
            raw = zf.read(names[0])
    except zipfile.BadZipFile:
        raise KrxUniverseError(f"{market} master file is not a zip archive") from None

    listings: list[Listing] = []
    for row in raw.decode(MASTER_ENCODING, errors="replace").splitlines():
        if len(row) <= offset + 2:
            continue
        code = row[:SHORT_CODE_LEN].strip()
        name = row[SHORT_CODE_LEN + STANDARD_CODE_LEN : -offset].strip()
        group_code = row[-offset : -offset + 2]
        if not code:
            continue
        listings.append(Listing(code, market, name, group_code))

    if not listings:
        raise KrxUniverseError(f"{market} master file parsed to zero rows")
    # A layout change upstream would most likely show up as group codes
    # that are not two letters. Fail loudly rather than silently record a
    # universe of zero common stocks.
    common = sum(1 for listing in listings if listing.group_code == COMMON_STOCK)
    if common == 0:
        raise KrxUniverseError(
            f"{market} master parsed {len(listings)} rows but zero common stock "
            f"({COMMON_STOCK}); the fixed-tail offset is probably wrong"
        )
    return listings


def fetch_universe() -> list[Listing]:
    """Both markets, unfiltered. Filtering happens at read time, not here
    -- see `krx_universe`'s schema docstring for why the record keeps
    ETFs and ETNs."""
    return parse_master(_download(KOSPI_URL), "KOSPI") + parse_master(
        _download(KOSDAQ_URL), "KOSDAQ"
    )


def snapshot(conn, snapshot_date: str | None = None) -> tuple[str, int, int]:
    """Record today's universe. Returns `(date, rows_written, common_stock)`."""
    date = snapshot_date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    listings = fetch_universe()
    written = upsert_krx_universe(
        conn, date, [(x.code, x.market, x.name, x.group_code) for x in listings]
    )
    common = sum(1 for x in listings if x.group_code == COMMON_STOCK)
    return date, written, common


def latest_snapshot_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(snapshot_date) FROM krx_universe").fetchone()
    return row[0] if row and row[0] else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true", help="fetch and record today")
    group.add_argument("--list", action="store_true", help="print the latest snapshot")
    group.add_argument("--coverage", action="store_true", help="snapshots on record")
    parser.add_argument("--date", help="snapshot date to read (default: latest)")
    args = parser.parse_args(argv)

    conn = connect(args.db_path)
    try:
        if args.snapshot:
            date, written, common = snapshot(conn)
            print(f"{date}: {written} rows written, {common} common stock")
            return 0
        if args.coverage:
            rows = krx_universe_snapshots(conn)
            if not rows:
                print("no snapshots on record", file=sys.stderr)
                return 1
            for date, total, common in rows:
                print(f"{date}  {total:>5} rows  {common:>5} common stock")
            return 0
        date = args.date or latest_snapshot_date(conn)
        if date is None:
            print("no snapshots on record; run --snapshot first", file=sys.stderr)
            return 1
        for code, market, name, _ in fetch_krx_universe(conn, date):
            print(f"{code}\t{market}\t{name}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
