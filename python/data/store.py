"""Local SQLite cache for BingX klines.

Schema exactly as specified in CLAUDE.md's "Strategy Research
Operational Design" section, keyed on `(symbol, interval,
open_time_ms)`. OHLCV values are stored as exact `TEXT`, never SQLite
`REAL`/float -- same reasoning as `schemas/_types.py`'s
`PositiveDecimalString`: this codebase treats float round-tripping of
price/volume values as a correctness bug, not a style preference.

`upsert_klines` uses `INSERT OR IGNORE` against the primary key -- this
is the resumability mechanism: re-fetching an overlapping range becomes
a no-op for rows already present, with no separate "have I already
fetched this" bookkeeping needed anywhere else in the pipeline.
"""

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from data._grid import interval_ms, require_valid_range
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


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the local kline cache and ensure the
    schema exists. `db_path` may be `":memory:"` for tests.

    `sqlite3.connect` creates the database file itself but not any
    missing parent directory, so the parent is created here (a no-op
    when it already exists) -- otherwise a fresh clone's first
    `backfill.py` run would fail on a missing `python/data/var/`
    directory before ever reaching BingX.
    """
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
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
