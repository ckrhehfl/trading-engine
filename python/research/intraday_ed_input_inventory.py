"""ED G1's bounded, price-free inventory with a same-snapshot raw check.

Importing this module performs no IO. Actual use requires a separately registered
caller to log started before access and enforce the outer shared process budget.
Only schema and fixed-window presence metadata are returned. A metadata digest
is neither a database-file hash nor evidence of completeness or source fitness.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from research.intraday_scope_inventory import END_EXCLUSIVE_MS, START_MS


SPECIFICATION_ID = "intraday-ed-input-inventory-v1"
MODE = "discovery-metadata-only"
WINDOW = {
    "namespaces": ["KRX:*", "KRX-INDEX:*"], "interval": "1m",
    "start": "2025-09-04", "end": "2026-09-23",
}
KLINES_COLUMNS = (
    "symbol", "interval", "open_time_ms", "fetched_at", "volume", "quote_volume",
)
POSITIONING_COLUMNS = ("symbol", "metric", "period", "timestamp_ms", "fetched_at")
MAX_SCHEMA_TABLES = 128
MAX_SCHEMA_COLUMNS = 64
MAX_AGGREGATE_ROWS = 200_000
MAX_SOURCE_ROWS = 10_000_000
_MAX_METADATA_TEXT = 1024
_MAX_QUERY_SECONDS = 60.0
_MAX_QUERY_STEPS = 200_000_000
_PROGRESS_STEPS = 10_000
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_SCHEMA_SQL = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name LIMIT 129"
_FUNCTIONS = frozenset({"date", "count", "min", "max", "sum", "glob"})
_LIMITATION = (
    "Stored metadata in the fixed spent-discovery window only. Presence counts "
    "do not establish session completeness, economic availability, historical "
    "top-20 membership, equity tradability, official sector identity or source "
    "fitness. Missing tables and schema candidates leave source fitness unresolved."
)

_KLINES_PRODUCER_SQL = """
SELECT symbol, date(open_time_ms / 1000, 'unixepoch', '+09:00'),
       count(*), min(open_time_ms), max(open_time_ms),
       min(fetched_at), max(fetched_at),
       count(volume), sum(volume = ''), count(quote_volume), sum(quote_volume = '')
FROM klines
WHERE interval COLLATE BINARY = '1m'
  AND (symbol GLOB 'KRX:*' OR symbol GLOB 'KRX-INDEX:*')
  AND open_time_ms >= ? AND open_time_ms < ?
GROUP BY symbol COLLATE BINARY, 2
ORDER BY symbol COLLATE BINARY, 2
"""
_KLINES_VERIFIER_SQL = """
SELECT symbol, open_time_ms, fetched_at,
       CASE WHEN volume IS NULL THEN 0 WHEN volume = '' THEN 1 ELSE 2 END,
       CASE WHEN quote_volume IS NULL THEN 0 WHEN quote_volume = '' THEN 1 ELSE 2 END
FROM klines
WHERE interval COLLATE BINARY = '1m'
  AND (symbol GLOB 'KRX:*' OR symbol GLOB 'KRX-INDEX:*')
  AND open_time_ms >= ? AND open_time_ms < ?
"""
_POSITIONING_PRODUCER_SQL = """
SELECT symbol, metric, period,
       date(timestamp_ms / 1000, 'unixepoch', '+09:00'),
       count(*), min(timestamp_ms), max(timestamp_ms),
       min(fetched_at), max(fetched_at), count(fetched_at), sum(fetched_at = '')
FROM positioning
WHERE symbol GLOB 'KRX:*' AND metric GLOB 'krx_quote*'
  AND timestamp_ms >= ? AND timestamp_ms < ?
GROUP BY symbol COLLATE BINARY, metric COLLATE BINARY, period COLLATE BINARY, 4
ORDER BY symbol COLLATE BINARY, metric COLLATE BINARY, period COLLATE BINARY, 4
"""
_POSITIONING_VERIFIER_SQL = """
SELECT symbol, metric, period, timestamp_ms, fetched_at
FROM positioning
WHERE symbol GLOB 'KRX:*' AND metric GLOB 'krx_quote*'
  AND timestamp_ms >= ? AND timestamp_ms < ?
"""
_DATA_QUERIES = frozenset({
    _KLINES_PRODUCER_SQL, _KLINES_VERIFIER_SQL,
    _POSITIONING_PRODUCER_SQL, _POSITIONING_VERIFIER_SQL,
})


def _text(value: Any, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) > _MAX_METADATA_TEXT:
        raise ValueError("invalid or oversized inventory text metadata")


class _MetadataConnection:
    """Exact statement allowlist in addition to SQLite's column authorizer.

    A column authorizer alone cannot distinguish COUNT(volume) from returning
    raw volume. No arbitrary SELECT, including allowed numeric columns, is exposed.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._tables: set[str] = set()
        self._deadline = time.monotonic() + _MAX_QUERY_SECONDS
        self._steps = 0

    def authorize(self, action: int, arg1: str | None, arg2: str | None,
                  database: str | None, source: str | None) -> int:
        if action == sqlite3.SQLITE_READ:
            allowed = database == "main" and source is None and (
                (arg1 == "sqlite_master" and arg2 in {"name", "type"})
                or (arg1 == "klines" and arg2 in KLINES_COLUMNS)
                or (arg1 == "positioning" and arg2 in POSITIONING_COLUMNS)
            )
        elif action == sqlite3.SQLITE_SELECT:
            allowed = True
        elif action == sqlite3.SQLITE_FUNCTION:
            allowed = arg2 in _FUNCTIONS
        elif action == sqlite3.SQLITE_TRANSACTION:
            allowed = arg1 in {"BEGIN", "ROLLBACK"}
        elif action == sqlite3.SQLITE_PRAGMA:
            allowed = arg1 == "table_info" and arg2 in self._tables
        else:
            allowed = False
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    def progress(self) -> int:
        self._steps += _PROGRESS_STEPS
        return int(self._steps >= _MAX_QUERY_STEPS or time.monotonic() >= self._deadline)

    def check_budget(self) -> None:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("ED inventory shared query wall budget exceeded")

    def execute(self, sql: str, parameters: tuple[int, ...]) -> sqlite3.Cursor:
        self.check_budget()
        if sql not in _DATA_QUERIES or parameters != (START_MS, END_EXCLUSIVE_MS):
            raise ValueError("ED inventory query or scope is not allowlisted")
        return self._connection.execute(sql, parameters)

    def read_schema(self) -> list[dict[str, Any]]:
        self.check_budget()
        names = [row[0] for row in self._connection.execute(_SCHEMA_SQL)]
        if len(names) > MAX_SCHEMA_TABLES:
            raise ValueError("ED inventory schema table limit exceeded")
        for name in names:
            _text(name)
        self._tables = set(names)
        tables = []
        for name in names:
            self.check_budget()
            columns = []
            # Names come only from sqlite_master; quoting also handles embedded
            # quotes. No candidate disclosure/sector table data query is built.
            quoted = '"' + name.replace('"', '""') + '"'
            for cid, column, declared_type, required, _default, primary in (
                self._connection.execute(f"PRAGMA table_info({quoted})")
            ):
                if len(columns) >= MAX_SCHEMA_COLUMNS:
                    raise ValueError("ED inventory schema column limit exceeded")
                _text(column)
                _text(declared_type)
                columns.append({
                    "cid": cid, "name": column, "declared_type": declared_type,
                    "not_null": bool(required), "primary_key_position": primary,
                })
            tables.append({"name": name, "columns": columns})
        return tables


@contextmanager
def _readonly_inventory_connection(db_path: str | Path) -> Iterator[_MetadataConnection]:
    path = Path(db_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("inventory database must be an existing regular file")
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA trusted_schema = OFF")
        session = _MetadataConnection(connection)
        connection.set_authorizer(session.authorize)
        connection.set_progress_handler(session.progress, _PROGRESS_STEPS)
        connection.execute("BEGIN")
        try:
            yield session
        finally:
            connection.set_progress_handler(None, 0)
            connection.execute("ROLLBACK")
    finally:
        connection.close()


def _checkpoint(callback: Callable[[], None] | None, session: _MetadataConnection) -> None:
    if callback is not None:
        callback()
    session.check_budget()


def _table_state(tables: list[dict[str, Any]], name: str,
                 required: tuple[str, ...]) -> dict[str, Any]:
    table = next((table for table in tables if table["name"] == name), None)
    if table is None:
        return {"status": "table_absent"}
    missing = sorted(set(required) - {column["name"] for column in table["columns"]})
    if missing:
        return {"status": "schema_incompatible", "missing_required_columns": missing}
    return {"status": "metadata_observed"}


def _row(identity: tuple[str, ...], day: str, first: int, last: int,
         fetched_min: str | None, fetched_max: str | None, count: int,
         presence: dict[str, int]) -> dict[str, Any]:
    _text(identity[0])
    _text(fetched_min, nullable=True)
    _text(fetched_max, nullable=True)
    if (type(first) is not int or type(last) is not int
            or not START_MS <= first <= last < END_EXCLUSIVE_MS or day is None):
        raise ValueError("invalid timestamp metadata in ED inventory")
    return {
        "namespace": identity[0].partition(":")[0], "symbol": identity[0],
        **({"interval": "1m"} if len(identity) == 1 else {
            "metric": identity[1], "period": identity[2],
        }),
        "kst_date": day, "row_count": count,
        "first_timestamp_ms": first, "last_timestamp_ms": last,
        "fetched_at_min": fetched_min, "fetched_at_max": fetched_max,
        **presence,
    }


def _summarize(rows: list[dict[str, Any]], namespaces: tuple[str, ...]) -> dict[str, Any]:
    return {
        "row_count": sum(row["row_count"] for row in rows),
        "aggregate_row_count": len(rows),
        "symbol_count": len({row["symbol"] for row in rows}),
        "kst_date_count": len({row["kst_date"] for row in rows}),
        "namespaces": {
            namespace: {
                "row_count": sum(row["row_count"] for row in rows
                                 if row["namespace"] == namespace),
                "symbol_count": len({row["symbol"] for row in rows
                                     if row["namespace"] == namespace}),
                "status": "stored_rows_present" if any(
                    row["namespace"] == namespace for row in rows
                ) else "no_stored_rows_in_fixed_scope",
            }
            for namespace in namespaces
        },
    }


def _observe(session: _MetadataConnection, family: str,
             callback: Callable[[], None] | None) -> list[dict[str, Any]]:
    sql = _KLINES_PRODUCER_SQL if family == "klines" else _POSITIONING_PRODUCER_SQL
    rows = []
    source_count = 0
    for number, part in enumerate(session.execute(sql, (START_MS, END_EXCLUSIVE_MS))):
        if number % 1024 == 0:
            _checkpoint(callback, session)
        if len(rows) >= MAX_AGGREGATE_ROWS:
            raise ValueError("ED inventory aggregate row limit exceeded")
        if family == "klines":
            symbol, day, count, first, last, fetched_min, fetched_max, vn, ve, qn, qe = part
            identity = (symbol,)
            presence = {}
            for field, nonnull, empty in (("volume", vn, ve), ("quote_volume", qn, qe)):
                empty = empty or 0
                presence.update({
                    f"{field}_null_count": count - nonnull,
                    f"{field}_empty_count": empty,
                    f"{field}_populated_count": nonnull - empty,
                })
        else:
            symbol, metric, period, day, count, first, last, fetched_min, fetched_max, fn, fe = part
            _text(metric)
            _text(period)
            identity = (symbol, metric, period)
            presence = {
                "fetched_at_null_count": count - fn,
                "fetched_at_empty_count": fe or 0,
                "fetched_at_populated_count": fn - (fe or 0),
            }
        source_count += count
        if source_count > MAX_SOURCE_ROWS:
            raise ValueError("ED inventory source row limit exceeded")
        rows.append(_row(identity, day, first, last, fetched_min, fetched_max, count, presence))
    return rows


def _rebuild(session: _MetadataConnection, family: str,
             callback: Callable[[], None] | None) -> list[dict[str, Any]]:
    sql = _KLINES_VERIFIER_SQL if family == "klines" else _POSITIONING_VERIFIER_SQL
    groups: dict[tuple[tuple[str, ...], int], list[Any]] = {}
    labels = ("null", "empty", "populated")
    for number, part in enumerate(session.execute(sql, (START_MS, END_EXCLUSIVE_MS))):
        if number % 4096 == 0:
            _checkpoint(callback, session)
        if number >= MAX_SOURCE_ROWS:
            raise ValueError("ED inventory source row limit exceeded")
        if family == "klines":
            symbol, stamp, fetched, volume_state, quote_state = part
            identity = (symbol,)
            if not symbol.startswith(("KRX:", "KRX-INDEX:")):
                raise ValueError("unexpected klines namespace")
            if volume_state not in (0, 1, 2) or quote_state not in (0, 1, 2):
                raise ValueError("invalid categorical presence metadata")
            increments = {
                f"volume_{labels[volume_state]}_count": 1,
                f"quote_volume_{labels[quote_state]}_count": 1,
            }
            fields = ("volume", "quote_volume")
        else:
            symbol, metric, period, stamp, fetched = part
            _text(metric)
            _text(period)
            if not symbol.startswith("KRX:") or not metric.startswith("krx_quote"):
                raise ValueError("unexpected positioning scope")
            identity = (symbol, metric, period)
            state = "null" if fetched is None else "empty" if fetched == "" else "populated"
            increments = {f"fetched_at_{state}_count": 1}
            fields = ("fetched_at",)
        _text(symbol)
        _text(fetched, nullable=True)
        if type(stamp) is not int or not START_MS <= stamp < END_EXCLUSIVE_MS:
            raise ValueError("invalid timestamp in independent ED inventory check")
        day_number = (stamp + 32_400_000) // 86_400_000
        key = (identity, day_number)
        group = groups.get(key)
        if group is None:
            if len(groups) >= MAX_AGGREGATE_ROWS:
                raise ValueError("ED inventory aggregate row limit exceeded")
            group = [0, stamp, stamp, None, None, {
                f"{field}_{label}_count": 0 for field in fields for label in labels
            }]
            groups[key] = group
        group[0] += 1
        group[1] = min(group[1], stamp)
        group[2] = max(group[2], stamp)
        if fetched is not None:
            group[3] = fetched if group[3] is None else min(group[3], fetched)
            group[4] = fetched if group[4] is None else max(group[4], fetched)
        for name, increment in increments.items():
            group[5][name] += increment
    return [
        _row(identity, date.fromordinal(_EPOCH_ORDINAL + day_number).isoformat(),
             group[1], group[2], group[3], group[4], group[0], group[5])
        for (identity, day_number), group in sorted(groups.items())
    ]


def _inventory(session: _MetadataConnection, callback: Callable[[], None] | None,
               *, verifier: bool) -> dict[str, Any]:
    tables = session.read_schema()
    result: dict[str, Any] = {
        "summary": {
            "mode": MODE, "window": dict(WINDOW), "timezone": "Asia/Seoul",
            "limitation": _LIMITATION,
        },
        "schema": {"tables": tables, "table_count": len(tables)},
    }
    total_groups = total_sources = 0
    for family, required, namespaces in (
        ("klines", KLINES_COLUMNS, ("KRX", "KRX-INDEX")),
        ("positioning", POSITIONING_COLUMNS, ("KRX",)),
    ):
        state = _table_state(tables, family, required)
        if state["status"] == "metadata_observed":
            rows = (_rebuild if verifier else _observe)(session, family, callback)
            state.update({"rows": rows, "summary": _summarize(rows, namespaces)})
            total_groups += len(rows)
            total_sources += state["summary"]["row_count"]
        result[family] = state
    if total_groups > MAX_AGGREGATE_ROWS or total_sources > MAX_SOURCE_ROWS:
        raise ValueError("ED inventory combined result limit exceeded")
    candidates = {}
    for family, keywords in (
        ("disclosure", ("dart", "kind", "disclosure", "filing", "rcept", "report_nm")),
        ("sector", ("sector", "industry", "industr", "classification", "ksic")),
    ):
        selected = [table["name"] for table in tables if any(
            word in table["name"].lower() or any(
                word in column["name"].lower() for column in table["columns"]
            ) for word in keywords
        )]
        candidates[family] = {
            "status": "schema_candidates_only" if selected else "no_schema_candidate_identified",
            "tables": selected, "source_fitness": "unresolved", "data_rows_read": False,
        }
    result["context_candidates"] = candidates
    return result


def _produce_inventory(session: _MetadataConnection,
                       checkpoint: Callable[[], None] | None) -> dict[str, Any]:
    return _inventory(session, checkpoint, verifier=False)


def _reconstruct_inventory(session: _MetadataConnection,
                           checkpoint: Callable[[], None] | None) -> dict[str, Any]:
    return _inventory(session, checkpoint, verifier=True)


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def collect_verified_inventory(
    db_path: str | Path, checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Return metadata only after aggregate/raw-stream equality in one snapshot.

    A shared 60-second/200M-VM-step budget covers schema and both data passes.
    No automatic retry, acquisition, migration, credentials or operational IO is
    performed. Absence/incompatibility is explicit, never invented zero coverage.
    """
    if checkpoint is not None:
        checkpoint()
    with _readonly_inventory_connection(db_path) as session:
        produced = _produce_inventory(session, checkpoint)
        _checkpoint(checkpoint, session)
        rebuilt = _reconstruct_inventory(session, checkpoint)
        _checkpoint(checkpoint, session)
        producer_hash, verifier_hash = _digest(produced), _digest(rebuilt)
        if produced != rebuilt or producer_hash != verifier_hash:
            raise ValueError("ED inventory independent verification mismatch")
    if checkpoint is not None:
        checkpoint()
    return {
        "inventory": {**produced, "inventory_sha256": producer_hash},
        "verification": {
            "status": "verified", "method": "independent-timestamp-presence-stream-v1",
            "same_snapshot": True, "digest_scope": "returned_metadata_not_database_file",
            "producer_sha256": producer_hash, "verifier_sha256": verifier_hash,
        },
    }
