"""Pull the KRX series back from the instance that now collects them.

**Why this exists.** On 2026-09-17 KRX/KIS collection moved to the GCP
instance, because the local machine is not reliably on during the KRX
session and an order-book sample not taken is gone the same second. But
**research still runs locally** -- the instance is a 955MB e2-micro
already carrying two paper-trading JVMs, and a backtest does not fit
there. So the data has to come back, and without this module the move
would leave every local KRX read silently frozen at the migration
snapshot.

## Who owns what, and why the split is not a compromise

**The instance owns KRX. This machine owns Binance.** That is forced
rather than chosen: Binance geo-blocks the instance entirely -- every
endpoint returns `HTTP 451`, re-verified 2026-09-17 -- so
`collect-positioning.sh` cannot run there, and its series cannot be
backfilled either. CLAUDE.md already states the principle this lands on:
*"a collector's home is chosen per venue, not once for the project."*

So each series has **exactly one writer**, which is what actually
prevents two databases drifting. This module only ever moves rows in one
direction, for one venue.

## What it will and will not do

- **`INSERT OR IGNORE` only.** No `UPDATE`, no `DELETE`. A local row is
  never overwritten or removed, so a botched sync cannot cost data -- the
  worst case is that nothing moves.
- **Only KRX-prefixed rows.** `klines` and `positioning` are shared
  tables holding both venues, and the source file still contains a frozen
  copy of this machine's Binance rows from the migration snapshot.

  Two different guarantees are at work here and they are worth keeping
  apart, because conflating them produced a test that could not fail:
  `INSERT OR IGNORE` protects a row whose **primary key exists in both**,
  so a local value is never overwritten by a stale one. The **prefix
  filter** is what stops a row the instance has and this machine does not
  from crossing the venue boundary at all. Only the second is this
  module's own decision, and it is the reason this is not a whole-file
  copy.
- **It refuses a source with no KRX rows**, which is what a wrong file
  looks like.

Run (the shell wrapper fetches the snapshot first):

    scripts/sync-krx-from-instance.sh

Or against an already-downloaded file:

    python -m data.sync_krx --source /path/to/instance-snapshot.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass

from data._paths import DEFAULT_DB_PATH

#: Every symbol this machine does NOT collect any more. Both `klines` and
#: `positioning` hold two venues in one table, so ownership is by prefix.
KRX_SYMBOL_PREFIX = "KRX"

#: `(table, filter)` -- the filter is the ownership rule. `krx_universe`
#: has no filter because the whole table is KRX by definition.
OWNED: tuple[tuple[str, str | None], ...] = (
    ("klines", f"symbol LIKE '{KRX_SYMBOL_PREFIX}%'"),
    ("positioning", f"symbol LIKE '{KRX_SYMBOL_PREFIX}%'"),
    ("krx_universe", None),
)


@dataclass(frozen=True)
class TableSync:
    table: str
    available: int
    inserted: int

    @property
    def already_present(self) -> int:
        return self.available - self.inserted


def _columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def _required_columns(
    conn: sqlite3.Connection, table: str, schema: str = "main"
) -> set[str]:
    """Columns that cannot be left to a default -- NOT NULL with no default.

    `PRAGMA table_info` returns `(cid, name, type, notnull, dflt_value, pk)`.
    """
    return {
        row[1]
        for row in conn.execute(f"PRAGMA {schema}.table_info({table})")
        if row[3] and row[4] is None and not row[5]
    }


def shared_columns(
    conn: sqlite3.Connection, table: str
) -> list[str]:
    """The columns both databases have, in the destination's order.

    **The instance's schema can lag this machine's**, which is not
    hypothetical: its checkout was 16 commits behind when collection moved
    there, and `store.py` migrates `klines` additively -- `quote_volume`,
    `taker_buy_base_volume` and `taker_buy_quote_volume` were each added
    to an existing table. Projecting the destination's full column list
    onto an older source makes SQLite raise `no such column` before the
    merge runs at all, and `main` catches only `ValueError`, so the sync
    would die with a traceback rather than a diagnosis.

    A destination column the source lacks is simply left to its default,
    which is exactly right for a nullable column added later. A **required**
    one is a real incompatibility and is refused by name.
    """
    dest = _columns(conn, table)
    source = set(_columns(conn, table, schema="src"))
    missing_and_required = _required_columns(conn, table) - source
    if missing_and_required:
        raise ValueError(
            f"the source's {table} is missing {sorted(missing_and_required)}, which "
            f"this database requires and cannot default. The snapshot predates a "
            f"schema change that was not additive -- update the instance's checkout "
            f"and take a fresh snapshot rather than merging a partial row."
        )
    return [column for column in dest if column in source]


def merge_krx(source_db: str, dest_db: str) -> list[TableSync]:
    """Copy KRX-owned rows from `source_db` into `dest_db`.

    Additive and idempotent: re-running moves nothing the second time.
    Returns what each table actually gained, so a caller can report the
    real figure rather than the intent -- `check_reported_from_actual`'s
    own lesson.
    """
    dest = sqlite3.connect(dest_db)
    try:
        dest.execute("ATTACH DATABASE ? AS src", (f"file:{source_db}?mode=ro",))
    except sqlite3.OperationalError:
        # Older SQLite builds reject a URI in ATTACH; fall back to a plain
        # path, which is read-only in effect because nothing here writes
        # to `src`.
        dest.execute("ATTACH DATABASE ? AS src", (source_db,))

    try:
        results: list[TableSync] = []
        total_available = 0
        for table, where in OWNED:
            clause = f" WHERE {where}" if where else ""
            available = dest.execute(
                f"SELECT COUNT(*) FROM src.{table}{clause}"
            ).fetchone()[0]
            total_available += available

            # The INTERSECTION, named explicitly on both sides, so a source
            # whose schema lags cannot break the merge and a source whose
            # schema is ahead cannot shift values into the wrong columns.
            names = ", ".join(shared_columns(dest, table))
            before = dest.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
            dest.execute(
                f"INSERT OR IGNORE INTO main.{table} ({names}) "
                f"SELECT {names} FROM src.{table}{clause}"
            )
            after = dest.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
            results.append(TableSync(table, available, after - before))

        if total_available == 0:
            dest.rollback()
            raise ValueError(
                f"{source_db} holds no {KRX_SYMBOL_PREFIX} rows at all. That is "
                f"what a wrong file looks like, not an up-to-date one -- refusing "
                f"rather than reporting a successful sync of nothing."
            )
        dest.commit()
        return results
    finally:
        dest.execute("DETACH DATABASE src")
        dest.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="the instance's snapshot")
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = ap.parse_args(argv)

    try:
        results = merge_krx(args.source, args.db_path)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    print(f"{'table':<16} {'in source':>12} {'inserted':>10} {'already had':>12}")
    print("-" * 54)
    for row in results:
        print(
            f"{row.table:<16} {row.available:>12,} {row.inserted:>10,} "
            f"{row.already_present:>12,}"
        )
    moved = sum(row.inserted for row in results)
    print(
        f"\n{moved:,} row(s) pulled from the instance."
        + ("" if moved else "  Already up to date.")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
