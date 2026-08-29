"""Local SQLite cache for BingX klines/funding rates and FRED macro
series.

Klines schema exactly as specified in CLAUDE.md's "Strategy Research
Operational Design" section, keyed on `(symbol, interval,
open_time_ms)`. Funding-rate schema added by Strategy Research Task M
(see `.planning/sr-m-funding-rate-pipeline.md`), keyed on `(symbol,
funding_time_ms)` -- no `interval` column, since funding rate has no
interval concept the way klines does (see `bingx_funding.py`'s
docstring). Macro-series schema added by Strategy Research Task W (see
`.planning/sr-w-macro-data-pipeline.md`), keyed on `(series_id,
observation_date)` -- a `TEXT` calendar date (FRED's own `YYYY-MM-DD`),
not an `INTEGER` ms timestamp the way the other two tables are, since
FRED's own unit is a date, not a millisecond epoch (see
`fred_client.py`'s module docstring). All three tables live in the same
cache file/connection so a research script needing any combination of
klines, funding, and macro data only ever needs one `connect()` call.
Price/rate/macro values are stored as exact `TEXT`, never SQLite
`REAL`/float -- same reasoning as `schemas/_types.py`'s
`PositiveDecimalString`: this codebase treats float round-tripping of
numeric values as a correctness bug, not a style preference.

`upsert_klines`/`upsert_funding_rates`/`upsert_macro_observations` all
use `INSERT OR IGNORE` against their primary key -- this is the
resumability mechanism: re-fetching an overlapping range becomes a
no-op for rows already present, with no separate "have I already
fetched this" bookkeeping needed anywhere else in the pipeline.

`klines.taker_buy_base_volume`/`taker_buy_quote_volume` were added by
Scalping Strategy Research Task S5 -- nullable, additive columns (`NULL`
for every BingX-sourced row and any pre-Task-S5 row of any source; see
`bingx_klines.KlineRow`'s own docstring). Because `CREATE TABLE IF NOT
EXISTS` cannot retroactively add a column to the real, already-populated
production `klines` table, `connect()` also runs `_ensure_klines_columns`
on every call -- a real, idempotent `ALTER TABLE ... ADD COLUMN`
migration, not baked into `SCHEMA` itself.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from data._grid import interval_ms, require_valid_range
from data.bingx_funding import FUNDING_INTERVAL_MS, FundingRow
from data.bingx_klines import KlineRow
from data.fred_client import ObservationRow

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

# Additive columns (Scalping Strategy Research Task S5) -- nullable, no
# DEFAULT needed since every existing row simply has no value for a
# column that didn't exist when it was inserted. `NULL` for every
# BingX-sourced row (no buyer/seller breakdown on that wire at all) and
# for any Binance-sourced row that predates this task; populated only for
# Binance-sourced rows fetched after this task via
# `binance_klines.py::_parse_row`. Migrated via `_ensure_klines_columns`
# below, not baked into `SCHEMA` itself, because `CREATE TABLE IF NOT
# EXISTS` is a genuine no-op against the real, already-populated
# production `klines` table -- it does not retroactively add a column to
# a table that already exists.
_KLINES_ORDER_FLOW_COLUMNS = ("taker_buy_base_volume", "taker_buy_quote_volume")


def _ensure_klines_columns(conn: sqlite3.Connection) -> None:
    """Add any of `_KLINES_ORDER_FLOW_COLUMNS` missing from the real
    `klines` table -- SQLite's `ALTER TABLE ... ADD COLUMN` has no
    portable `IF NOT EXISTS` clause, so existence is checked explicitly
    via `PRAGMA table_info` first, making this safe to call on every
    `connect()` (idempotent: a column already present is left untouched,
    never re-added, never dropped, never rewritten).

    The check-then-`ALTER` sequence is wrapped in a real `BEGIN
    IMMEDIATE` transaction (real CodeRabbit review finding): without it,
    two concurrent `connect()` calls against the same database file could
    both read the column as missing before either commits, and the
    second `ALTER TABLE` would then fail with "duplicate column name".
    `BEGIN IMMEDIATE` acquires SQLite's write lock up front, so a second,
    concurrent caller blocks (see the `busy_timeout` below -- without it,
    SQLite's own default is to fail immediately rather than wait) until
    the first's transaction resolves and then correctly sees the column
    already present -- serialized, not racing. `column` is always drawn
    from the fixed, hardcoded `_KLINES_ORDER_FLOW_COLUMNS` tuple above,
    never external input, so the f-string here is not a SQL-injection
    surface (SQLite has no parameter-binding syntax for a column *name*
    in `ALTER TABLE ADD COLUMN`, only for values).
    """
    conn.execute("PRAGMA busy_timeout = 5000")  # wait, don't fail-fast, on real lock contention
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(klines)").fetchall()}
        for column in _KLINES_ORDER_FLOW_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE klines ADD COLUMN {column} TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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

MACRO_SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_series (
  series_id TEXT NOT NULL,
  observation_date TEXT NOT NULL,
  value TEXT,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (series_id, observation_date)
);
"""


POSITIONING_SCHEMA = """
CREATE TABLE IF NOT EXISTS positioning (
  symbol TEXT NOT NULL,
  metric TEXT NOT NULL,
  period TEXT NOT NULL,
  timestamp_ms INTEGER NOT NULL,
  value TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, metric, period, timestamp_ms)
);
"""
"""Positioning and forced-flow series (Trade Management Task B).

**Long format on purpose.** The five endpoints this holds return five
different shapes -- open interest gives two numbers per timestamp, the
three long/short ratios give three each, the taker ratio gives three
more. A column-per-field design would mean a schema migration every time
another metric is added, and this table exists precisely because the
catalogue found three whole signal families we had never fetched. One
row per (symbol, metric, period, timestamp) absorbs any of them.

**`period` is part of the key, not a detail.** These endpoints are
served per aggregation window ("5m", "1h", "1d", ...) and the same
instant carries a different value at each. Collapsing them would silently
overwrite one resolution with another.

**Why this needs to start collecting now.** The `/futures/data/`
endpoints retain only ~30 days. Unlike klines, this history cannot be
backfilled later -- whatever is not captured is gone permanently. That is
the whole reason this table is created before any strategy needs it.
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the local market-data cache and ensure
    every schema exists. `db_path` may be `":memory:"` for
    tests.

    `sqlite3.connect` creates the database file itself but not any
    missing parent directory, so the parent is created here (a no-op
    when it already exists) -- otherwise a fresh clone's first
    `backfill.py`/`backfill_funding.py`/`backfill_macro.py` run would
    fail on a missing `python/data/var/` directory before ever reaching
    BingX or FRED.
    """
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.execute(FUNDING_SCHEMA)
    conn.execute(MACRO_SCHEMA)
    conn.execute(POSITIONING_SCHEMA)
    conn.commit()
    _ensure_klines_columns(conn)
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
            str(row.taker_buy_base_volume) if row.taker_buy_base_volume is not None else None,
            str(row.taker_buy_quote_volume) if row.taker_buy_quote_volume is not None else None,
        )
        for row in rows
    ]

    cursor = conn.executemany(
        "INSERT OR IGNORE INTO klines "
        "(symbol, interval, open_time_ms, open, high, low, close, volume, fetched_at, "
        "taker_buy_base_volume, taker_buy_quote_volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        "SELECT open_time_ms, open, high, low, close, volume, "
        "taker_buy_base_volume, taker_buy_quote_volume FROM klines "
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
            taker_buy_base_volume=Decimal(row[6]) if row[6] is not None else None,
            taker_buy_quote_volume=Decimal(row[7]) if row[7] is not None else None,
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
    `(gap_start_ms, gap_end_ms)` tuples -- same algorithm shape as
    `find_missing_ranges`, just scoped by `symbol` only (no `interval`).

    **Disclosed limitation, not fixed by this function** (CodeRabbit
    review finding on this task's PR, judged a genuine but low-probability
    residual risk rather than fixed outright -- see
    `.planning/sr-m-funding-rate-pipeline.md`): because real funding
    cadence is only *typically* `FUNDING_INTERVAL_MS` and not a guaranteed
    fixed grid (see `bingx_funding.py`'s module docstring), this function
    cannot detect a genuinely-missing row that happens to leave its two
    stored neighbors spaced exactly `FUNDING_INTERVAL_MS` apart from each
    other -- e.g. if the real, confirmed-present 2021-11-11T18:00:00Z row
    (2h after 16:00:00Z, 6h before the next day's 00:00:00Z) had instead
    been dropped by some transient fetch failure, its neighbors (16:00 and
    next-day 00:00) are themselves exactly one `FUNDING_INTERVAL_MS`
    apart, so no gap would ever be flagged for it, on this run or any
    future rerun. This is a fundamental limit of diffing *stored*
    timestamps against an *assumed* step with no independent oracle for
    "was there supposed to be an extra row here" -- closing it fully would
    need a structurally different re-verification strategy (e.g.
    periodic full-range re-fetching regardless of apparent completeness),
    which is a genuinely bigger change than this task's scope. Mitigated,
    not solved, by `iter_funding_range`'s `+ 1`-not-`+ step` cursor
    (closes the pagination-boundary version of this risk) and by this
    being a rare, real-data-confirmed edge case (2 known anomalies across
    ~5.7 years of history, both actually captured intact by this task's
    real backfill run).
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


# ---------------------------------------------------------------------------
# macro_series -- FRED daily macro observations. Keyed by (series_id,
# observation_date), a calendar date, not a fixed-step ms timestamp --
# see fred_client.py's module docstring for why FRED's own inclusive
# date range is preserved as-is rather than translated to this
# pipeline's usual half-open ms convention. `find_missing_macro_ranges`
# below is the load-bearing piece: gap detection here cannot reuse
# find_missing_ranges'/find_missing_funding_ranges' "diff against an
# arithmetic step sequence" algorithm, because daily macro data has no
# fixed step -- FRED simply never returns a row at all for a Saturday or
# Sunday (see fred_client.py's module docstring), so an "expect one row
# per calendar day" gap check would misfire on every single weekend,
# forever. The actual invariant this module enforces instead: the
# expected calendar for a macro series is *weekdays only*
# (Monday-Friday), not *every* calendar day and not a full
# market-holiday-aware trading calendar either -- a real market holiday
# that falls on a weekday still gets a real row from FRED (value "."),
# so it is never a gap once fetched once; only a weekday FRED has never
# returned anything for at all (not yet fetched, or not yet published)
# is a gap.
# ---------------------------------------------------------------------------


def _validate_macro_date_range(start_date: str, end_date: str) -> None:
    # Mirrors fred_client._validate_date_range -- duplicated rather than
    # imported, same "each module owns its own range check" precedent as
    # bingx_funding.py's _validate_range vs. store.py's
    # _validate_funding_range. FRED's own range is inclusive both ends
    # (see fred_client.py's module docstring), so `start_date ==
    # end_date` is a legitimate single-day request and only
    # `start_date > end_date` is rejected.
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError(f"start_date ({start_date}) must be <= end_date ({end_date})")


def _expected_weekdays(start_date: str, end_date: str) -> list[str]:
    """Every Monday-Friday calendar date in the inclusive range
    `[start_date, end_date]`, ascending, as `YYYY-MM-DD` strings. This is
    the "expected calendar" `find_missing_macro_ranges` diffs stored
    rows against -- see this section's header comment for why weekdays
    (not every calendar day, not a holiday-aware trading calendar) is
    the right invariant.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    one_day = timedelta(days=1)
    days: list[str] = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Monday=0 .. Friday=4
            days.append(current.isoformat())
        current += one_day
    return days


def upsert_macro_observations(
    conn: sqlite3.Connection,
    series_id: str,
    rows: Iterable[ObservationRow],
) -> int:
    """Insert `rows`, skipping any already present at the same
    `(series_id, observation_date)`. Returns the number of rows actually
    newly inserted -- same `INSERT OR IGNORE` resumability mechanism and
    `cursor.rowcount` caveat as `upsert_klines`/`upsert_funding_rates`.

    A row whose `value is None` (FRED's own "." missing-observation
    marker, see `fred_client.py`'s module docstring) is stored with a
    real SQL `NULL` in the `value` column -- deliberately still inserted,
    not skipped. Skipping it would make `find_missing_macro_ranges`
    re-request that same date forever (it would never stop looking
    "missing"); storing it with `NULL` records "FRED told us there is no
    observation for this date" as a real, fetched fact, distinct from
    "we have never asked".
    """
    rows = list(rows)
    if not rows:
        return 0

    fetched_at = datetime.now(timezone.utc).isoformat()
    params = [
        (
            series_id,
            row.observation_date,
            str(row.value) if row.value is not None else None,
            fetched_at,
        )
        for row in rows
    ]

    cursor = conn.executemany(
        "INSERT OR IGNORE INTO macro_series (series_id, observation_date, value, fetched_at) VALUES (?, ?, ?, ?)",
        params,
    )
    conn.commit()
    return cursor.rowcount


def find_missing_macro_ranges(
    conn: sqlite3.Connection,
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, str]]:
    """Diff stored `observation_date` values in the inclusive range
    `[start_date, end_date]` against the *weekday-only* expected calendar
    (`_expected_weekdays` -- see this section's header comment for why
    weekends must never be treated as gaps) and return the gaps as
    inclusive `(gap_start_date, gap_end_date)` date-string pairs --
    directly usable as `fred_client.iter_observations`'s own inclusive
    `start_date`/`end_date` arguments.

    A gap's `(start, end)` may itself span a weekend internally (e.g. a
    missing Thursday and the following Monday, with nothing stored for
    either) -- that's fine and deliberate: re-requesting that whole
    inclusive span from FRED is harmless (FRED simply won't return rows
    for the Saturday/Sunday inside it, same as any other request), and
    coalescing adjacent missing weekdays into one range keeps this
    function's contiguous-run merging identical in shape to
    `find_missing_ranges`/`find_missing_funding_ranges` above, just over
    a non-uniform (weekdays-only) calendar instead of a fixed ms step.

    A stored row with `value IS NULL` (FRED's "." marker) counts as
    *present* here, same as any other stored row -- only an
    `observation_date` with no row at all is a gap. See
    `upsert_macro_observations`'s docstring for why that row is stored
    at all rather than skipped.
    """
    _validate_macro_date_range(start_date, end_date)

    expected = _expected_weekdays(start_date, end_date)
    if not expected:
        return []

    existing = {
        row[0]
        for row in conn.execute(
            "SELECT observation_date FROM macro_series "
            "WHERE series_id = ? AND observation_date >= ? AND observation_date <= ?",
            (series_id, start_date, end_date),
        )
    }

    gaps: list[tuple[str, str]] = []
    run_start: str | None = None
    run_end: str | None = None
    for day in expected:
        if day not in existing:
            if run_start is None:
                run_start = day
            run_end = day
        elif run_start is not None:
            gaps.append((run_start, run_end))
            run_start = None
    if run_start is not None:
        gaps.append((run_start, run_end))

    return gaps


def fetch_macro_observations(
    conn: sqlite3.Connection,
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[ObservationRow]:
    """Read stored macro observations in the inclusive range
    `[start_date, end_date]`, ordered ascending by `observation_date`.
    Read-path counterpart to `upsert_macro_observations` -- returns typed
    `ObservationRow`s with `Decimal | None` values parsed back from exact
    `TEXT`/`NULL` storage, same discipline as `fetch_klines`/
    `fetch_funding_rates`.
    """
    _validate_macro_date_range(start_date, end_date)

    rows = conn.execute(
        "SELECT observation_date, value FROM macro_series "
        "WHERE series_id = ? AND observation_date >= ? AND observation_date <= ? "
        "ORDER BY observation_date",
        (series_id, start_date, end_date),
    ).fetchall()

    return [
        ObservationRow(
            observation_date=row[0],
            value=Decimal(row[1]) if row[1] is not None else None,
        )
        for row in rows
    ]


@dataclass(frozen=True)
class PositioningRow:
    """One (metric, timestamp) observation.

    `value` is a `str` for the same reason every other numeric column in
    this module is: SQLite has no exact decimal type, and a float
    round-trip would silently perturb a ratio the strategy layer compares
    against a threshold.
    """

    metric: str
    period: str
    timestamp_ms: int
    value: str


def upsert_positioning(
    conn: sqlite3.Connection,
    symbol: str,
    rows: Iterable[PositioningRow],
) -> int:
    """Insert `rows`, skipping any already present at the same
    `(symbol, metric, period, timestamp_ms)`.

    Same `INSERT OR IGNORE` resumability as every other upsert here, and
    the same `cursor.rowcount` caveat: it counts rows actually inserted,
    so a re-poll over an overlapping window reports 0 rather than
    duplicating. That matters more here than elsewhere -- a collector
    running on a short cron interval re-requests the same recent window
    constantly by design.
    """
    rows = list(rows)
    if not rows:
        return 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    params = [
        (symbol, r.metric, r.period, r.timestamp_ms, r.value, fetched_at)
        for r in rows
    ]
    try:
        cursor = conn.executemany(
            "INSERT OR IGNORE INTO positioning "
            "(symbol, metric, period, timestamp_ms, value, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            params,
        )
        conn.commit()
        return cursor.rowcount
    except Exception:
        conn.rollback()
        raise


def fetch_positioning(
    conn: sqlite3.Connection,
    symbol: str,
    metric: str,
    period: str,
    start_ms: int,
    end_ms: int,
) -> list[PositioningRow]:
    """Rows in the half-open range `[start_ms, end_ms)`, ascending --
    matching this codebase's range convention everywhere else."""
    cursor = conn.execute(
        "SELECT metric, period, timestamp_ms, value FROM positioning "
        "WHERE symbol = ? AND metric = ? AND period = ? "
        "AND timestamp_ms >= ? AND timestamp_ms < ? ORDER BY timestamp_ms",
        (symbol, metric, period, start_ms, end_ms),
    )
    return [PositioningRow(m, p, t, v) for m, p, t, v in cursor.fetchall()]


def positioning_coverage(conn: sqlite3.Connection) -> list[tuple]:
    """`(symbol, metric, period, rows, earliest_ms, latest_ms)` per series.

    Exists so a human can answer "how much history have we actually
    accumulated" without writing SQL -- the question that matters most
    for a store whose upstream keeps only 30 days.
    """
    return conn.execute(
        "SELECT symbol, metric, period, COUNT(*), MIN(timestamp_ms), MAX(timestamp_ms) "
        "FROM positioning GROUP BY symbol, metric, period ORDER BY symbol, metric, period"
    ).fetchall()
