"""Local SQLite cache for BingX klines and funding rates.

Klines schema exactly as specified in CLAUDE.md's "Strategy Research
Operational Design" section, keyed on `(symbol, interval,
open_time_ms)`. Funding-rate schema added by Strategy Research Task M
(see `.planning/sr-m-funding-rate-pipeline.md`), keyed on `(symbol,
funding_time_ms)` -- no `interval` column, since funding rate has no
interval concept the way klines does (see `bingx_funding.py`'s
docstring). Both tables live in the same cache file/connection so a
research script needing both klines and funding data only ever needs
one `connect()` call. OHLCV/funding values are stored as exact `TEXT`,
never SQLite `REAL`/float -- same reasoning as `schemas/_types.py`'s
`PositiveDecimalString`: this codebase treats float round-tripping of
price/volume/rate values as a correctness bug, not a style preference.

`upsert_klines`/`upsert_funding_rates` both use `INSERT OR IGNORE`
against their primary key -- this is the resumability mechanism:
re-fetching an overlapping range becomes a no-op for rows already
present, with no separate "have I already fetched this" bookkeeping
needed anywhere else in the pipeline.
"""

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from data._grid import interval_ms, require_valid_range
from data.bingx_funding import FUNDING_INTERVAL_MS, FundingRow
from data.bingx_klines import KlineRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,
  open_time_ms INTEGER NOT NULL,
  open TEXT NOT NULL,
  high TEXT NOT NULL,
  low TEXT NOT NULL,
  close TEXT NOT NULL,
  volume TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, interval, open_time_ms)
);
"""

FUNDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_rates (
  symbol TEXT NOT NULL,
  funding_time_ms INTEGER NOT NULL,
  funding_rate TEXT NOT NULL,
  mark_price TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, funding_time_ms)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the local kline+funding cache and ensure
    both schemas exist. `db_path` may be `":memory:"` for tests.

    `sqlite3.connect` creates the database file itself but not any
    missing parent directory, so the parent is created here (a no-op
    when it already exists) -- otherwise a fresh clone's first
    `backfill.py`/`backfill_funding.py` run would fail on a missing
    `python/data/var/` directory before ever reaching BingX.
    """
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.execute(FUNDING_SCHEMA)
    conn.commit()
    return conn


def upsert_klines(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    rows: Iterable[KlineRow],
) -> int:
    """Insert `rows`, skipping any already present at the same
    `(symbol, interval, open_time_ms)`. Returns the number of rows
    actually newly inserted (verified empirically against this project's
    Python version: `sqlite3`'s `executemany` + `INSERT OR IGNORE`
    reports only the non-ignored count via `cursor.rowcount`, not the
    full batch size -- not documented as a stdlib guarantee, so this is
    worth re-checking if the Python version this project targets ever
    changes).

    `str(Decimal)` is exact (unlike `str(float)`, this never round-trips
    through binary floating point), so this preserves the exact decimal
    text BingX sent, all the way from the wire to the database.
    """
    rows = list(rows)
    if not rows:
        return 0

    fetched_at = datetime.now(timezone.utc).isoformat()
    params = [
        (
            symbol,
            interval,
            row.open_time_ms,
            str(row.open),
            str(row.high),
            str(row.low),
            str(row.close),
            str(row.volume),
            fetched_at,
        )
        for row in rows
    ]

    cursor = conn.executemany(
        "INSERT OR IGNORE INTO klines "
        "(symbol, interval, open_time_ms, open, high, low, close, volume, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        params,
    )
    conn.commit()
    return cursor.rowcount


def find_missing_ranges(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, int]]:
    """Diff stored `open_time_ms` values in `[start_ms, end_ms)` against
    the expected arithmetic sequence (step = `interval_ms(interval)`)
    and return the gaps as half-open `(gap_start_ms, gap_end_ms)`
    tuples -- directly usable as fetch ranges.

    A single pass over the *existing* rows (not the full expected grid)
    handles all three resumability scenarios the same way, with no
    special-casing:
    - extending forward from the latest known bar (a trailing gap after
      the last stored row),
    - backfilling further into history (a leading gap before the first
      stored row),
    - recovering from a genuinely interrupted mid-range fetch (a gap
      between two stored rows).
    """
    step = interval_ms(interval)
    require_valid_range(start_ms, end_ms, step)

    existing = [
        row[0]
        for row in conn.execute(
            "SELECT open_time_ms FROM klines "
            "WHERE symbol = ? AND interval = ? AND open_time_ms >= ? AND open_time_ms < ? "
            "ORDER BY open_time_ms",
            (symbol, interval, start_ms, end_ms),
        )
    ]

    gaps: list[tuple[int, int]] = []
    expected = start_ms
    for ts in existing:
        if ts > expected:
            gaps.append((expected, ts))
        expected = ts + step
    if expected < end_ms:
        gaps.append((expected, end_ms))

    return gaps


def fetch_klines(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[KlineRow]:
    """Read stored klines in the half-open range `[start_ms, end_ms)`,
    ordered ascending by `open_time_ms`. This is the read-path counterpart
    to `upsert_klines`: it returns typed `KlineRow`s with `Decimal` OHLCV
    fields parsed back from the exact `TEXT` storage, not raw
    `sqlite3.Row` tuples -- so a caller (e.g. `python/research/holdout.py`,
    the first real consumer of the cache Task A built) never has to touch
    SQL or re-derive the `Decimal(str)` parsing rule itself.
    """
    step = interval_ms(interval)
    require_valid_range(start_ms, end_ms, step)

    rows = conn.execute(
        "SELECT open_time_ms, open, high, low, close, volume FROM klines "
        "WHERE symbol = ? AND interval = ? AND open_time_ms >= ? AND open_time_ms < ? "
        "ORDER BY open_time_ms",
        (symbol, interval, start_ms, end_ms),
    ).fetchall()

    return [
        KlineRow(
            open_time_ms=row[0],
            open=Decimal(row[1]),
            high=Decimal(row[2]),
            low=Decimal(row[3]),
            close=Decimal(row[4]),
            volume=Decimal(row[5]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# funding_rates -- mirrors the klines functions above, minus the
# `interval` dimension (funding rate has no interval concept -- see
# bingx_funding.py's docstring), and using `FUNDING_INTERVAL_MS`
# (28,800,000ms / 8h) as a *typical* step for gap-detection arithmetic --
# not a hard per-row alignment guarantee. Real historical funding rows
# are NOT always aligned to this grid (a real, verified finding -- see
# bingx_funding.py's module docstring), so `start_ms`/`end_ms` range
# validation here deliberately checks only `start_ms < end_ms`
# (`_validate_funding_range` below), unlike klines' `require_valid_range`
# (which enforces genuine, verified grid alignment for klines' own
# `open_time`). This matters concretely: `find_missing_funding_ranges`'s
# own gap output can legitimately end on an off-grid real timestamp, and
# that value is exactly what a caller (`backfill_funding.sync_funding_
# range`) feeds into the next fetch -- strict alignment here would reject
# a range boundary this same module just produced.
# ---------------------------------------------------------------------------


def _validate_funding_range(start_ms: int, end_ms: int) -> None:
    if start_ms >= end_ms:
        raise ValueError(f"start_ms ({start_ms}) must be < end_ms ({end_ms})")


def upsert_funding_rates(
    conn: sqlite3.Connection,
    symbol: str,
    rows: Iterable[FundingRow],
) -> int:
    """Insert `rows`, skipping any already present at the same
    `(symbol, funding_time_ms)`. Returns the number of rows actually
    newly inserted -- same `INSERT OR IGNORE` resumability mechanism and
    `cursor.rowcount` caveat as `upsert_klines`.
    """
    rows = list(rows)
    if not rows:
        return 0

    fetched_at = datetime.now(timezone.utc).isoformat()
    params = [
        (
            symbol,
            row.funding_time_ms,
            str(row.funding_rate),
            str(row.mark_price),
            fetched_at,
        )
        for row in rows
    ]

    cursor = conn.executemany(
        "INSERT OR IGNORE INTO funding_rates "
        "(symbol, funding_time_ms, funding_rate, mark_price, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        params,
    )
    conn.commit()
    return cursor.rowcount


def find_missing_funding_ranges(
    conn: sqlite3.Connection,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, int]]:
    """Diff stored `funding_time_ms` values in `[start_ms, end_ms)`
    against the expected arithmetic sequence (step =
    `FUNDING_INTERVAL_MS`) and return the gaps as half-open
    `(gap_start_ms, gap_end_ms)` tuples -- identical algorithm to
    `find_missing_ranges`, just scoped by `symbol` only (no `interval`).
    """
    _validate_funding_range(start_ms, end_ms)

    existing = [
        row[0]
        for row in conn.execute(
            "SELECT funding_time_ms FROM funding_rates "
            "WHERE symbol = ? AND funding_time_ms >= ? AND funding_time_ms < ? "
            "ORDER BY funding_time_ms",
            (symbol, start_ms, end_ms),
        )
    ]

    gaps: list[tuple[int, int]] = []
    expected = start_ms
    for ts in existing:
        if ts > expected:
            gaps.append((expected, ts))
        expected = ts + FUNDING_INTERVAL_MS
    if expected < end_ms:
        gaps.append((expected, end_ms))

    return gaps


def fetch_funding_rates(
    conn: sqlite3.Connection,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[FundingRow]:
    """Read stored funding rates in the half-open range `[start_ms,
    end_ms)`, ordered ascending by `funding_time_ms`. Read-path
    counterpart to `upsert_funding_rates` -- returns typed `FundingRow`s
    with `Decimal` fields parsed back from exact `TEXT` storage, same as
    `fetch_klines`. Ascending order matters beyond mere convention here:
    `metrics.position.PositionTracker`'s funding attribution (see
    `.planning/sr-m-funding-rate-pipeline.md`) requires its input funding
    series to be sorted ascending and fails loud if it isn't -- this is
    the intended, idiomatic way to satisfy that precondition without the
    caller needing to sort anything itself.
    """
    _validate_funding_range(start_ms, end_ms)

    rows = conn.execute(
        "SELECT funding_time_ms, funding_rate, mark_price FROM funding_rates "
        "WHERE symbol = ? AND funding_time_ms >= ? AND funding_time_ms < ? "
        "ORDER BY funding_time_ms",
        (symbol, start_ms, end_ms),
    ).fetchall()

    return [
        FundingRow(
            funding_time_ms=row[0],
            funding_rate=Decimal(row[1]),
            mark_price=Decimal(row[2]),
        )
        for row in rows
    ]
