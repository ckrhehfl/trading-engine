"""Bounded metadata inventory of the already-spent KRX intraday window.

This does not select stocks or establish session completeness, tradability,
historical eligibility, or a current top-20 ranking. No prices, returns, volume
amounts, or turnover amounts leave SQLite. Actual use requires the separately
reviewed specification and logged caller; tests use temporary synthetic stores.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator

from research import experiment_log


SPECIFICATION_ID = "intraday-scope-inventory-v1"
MODE = "discovery-metadata-only"
ALLOW_COLUMNS = (
    "symbol", "interval", "open_time_ms", "fetched_at", "volume", "quote_volume",
)
_WINDOW = {
    "symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23",
}
_KST = timezone(timedelta(hours=9))
START_MS = int(datetime(2025, 9, 4, tzinfo=_KST).timestamp() * 1000)
END_EXCLUSIVE_MS = int(datetime(2026, 9, 24, tzinfo=_KST).timestamp() * 1000)
MAX_AGGREGATE_ROWS = 200_000
_PROGRESS_STEPS = 10_000
_MAX_QUERY_STEPS = 200_000_000
_MAX_QUERY_SECONDS = 60.0
_FUNCTIONS = frozenset({"date", "count", "min", "max", "sum", "typeof", "glob"})
_LIMITATION = (
    "Stored row metadata only; does not establish session completeness, "
    "tradability, historical universe eligibility, or a current top-20 ranking."
)

# Offset buckets are aggregated inside SQLite, never raw-bar materialization.
# Both the symbol pattern and the half-open UTC-ms bounds are fixed literals or
# module constants, not specification/CLI-controlled SQL fragments.
_INVENTORY_SQL = """
SELECT symbol,
       date(open_time_ms / 1000, 'unixepoch', '+09:00') AS kst_date,
       (open_time_ms / 1000) % 60 AS second_offset,
       count(*) AS row_count,
       min(open_time_ms), max(open_time_ms), min(fetched_at), max(fetched_at),
       sum(CASE WHEN volume IS NULL THEN 1 ELSE 0 END),
       sum(CASE WHEN volume = '' THEN 1 ELSE 0 END),
       sum(CASE WHEN volume IS NOT NULL AND volume != '' THEN 1 ELSE 0 END),
       sum(CASE WHEN quote_volume IS NULL THEN 1 ELSE 0 END),
       sum(CASE WHEN quote_volume = '' THEN 1 ELSE 0 END),
       sum(CASE WHEN quote_volume IS NOT NULL AND quote_volume != '' THEN 1 ELSE 0 END),
       sum(CASE WHEN open_time_ms % 1000 != 0 THEN 1 ELSE 0 END),
       sum(CASE WHEN typeof(open_time_ms) != 'integer'
                     OR typeof(fetched_at) NOT IN ('text', 'null') THEN 1 ELSE 0 END)
FROM klines
WHERE interval = '1m' AND symbol GLOB 'KRX:*'
  AND open_time_ms >= ? AND open_time_ms < ?
GROUP BY symbol, kst_date, second_offset
ORDER BY symbol, kst_date, second_offset
"""


def _authorize_metadata(
    action: int, arg1: str | None, arg2: str | None,
    database: str | None, source: str | None,
) -> int:
    if action == sqlite3.SQLITE_READ:
        allowed = (
            database == "main" and source is None
            and arg1 == "klines" and arg2 in ALLOW_COLUMNS
        )
    elif action == sqlite3.SQLITE_SELECT:
        allowed = True
    elif action == sqlite3.SQLITE_FUNCTION:
        allowed = arg2 in _FUNCTIONS
    elif action == sqlite3.SQLITE_TRANSACTION:
        allowed = arg1 in {"BEGIN", "ROLLBACK"}
    else:
        allowed = False
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


@contextmanager
def _readonly_inventory_connection(db_path: str | Path) -> Iterator[sqlite3.Connection]:
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
        connection.set_authorizer(_authorize_metadata)
        deadline = time.monotonic() + _MAX_QUERY_SECONDS
        steps = 0

        def stop_at_budget() -> int:
            nonlocal steps
            steps += _PROGRESS_STEPS
            return int(steps >= _MAX_QUERY_STEPS or time.monotonic() >= deadline)

        connection.set_progress_handler(stop_at_budget, _PROGRESS_STEPS)
        connection.execute("BEGIN")
        try:
            yield connection
        finally:
            connection.set_progress_handler(None, 0)
            connection.execute("ROLLBACK")
    finally:
        connection.close()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def collect_inventory(db_path: str | Path) -> dict[str, Any]:
    """Read fixed-window metadata in one snapshot and return a stable digest.

    This reusable reader itself does not append research logs. Actual-data
    callers must use ``run_inventory`` so durable started precedes SQL access.
    The digest covers normalized returned metadata, not database file contents.
    """
    rows: list[dict[str, Any]] = []
    with _readonly_inventory_connection(db_path) as connection:
        for part in connection.execute(_INVENTORY_SQL, (START_MS, END_EXCLUSIVE_MS)):
            (symbol, kst_date, second_offset, count, first_ms, last_ms,
             fetched_min, fetched_max, volume_null, volume_empty, volume_populated,
             quote_null, quote_empty, quote_populated, subsecond, invalid) = part
            if invalid or kst_date is None or not isinstance(second_offset, int):
                raise ValueError("invalid timestamp or retrieval metadata in inventory scope")
            if not rows or (rows[-1]["symbol"], rows[-1]["kst_date"]) != (symbol, kst_date):
                if len(rows) >= MAX_AGGREGATE_ROWS:
                    raise ValueError("inventory aggregate row limit exceeded")
                rows.append({
                    "symbol": symbol, "interval": "1m", "kst_date": kst_date,
                    "row_count": 0, "first_open_time_ms": first_ms,
                    "last_open_time_ms": last_ms, "fetched_at_min": None,
                    "fetched_at_max": None, "volume_null_count": 0,
                    "volume_empty_count": 0, "volume_populated_count": 0,
                    "quote_volume_null_count": 0, "quote_volume_empty_count": 0,
                    "quote_volume_populated_count": 0, "subsecond_timestamp_count": 0,
                    "timestamp_second_offset_counts": {},
                })
            row = rows[-1]
            row["row_count"] += count
            row["first_open_time_ms"] = min(row["first_open_time_ms"], first_ms)
            row["last_open_time_ms"] = max(row["last_open_time_ms"], last_ms)
            for key, value, reducer in (
                ("fetched_at_min", fetched_min, min), ("fetched_at_max", fetched_max, max),
            ):
                if value is not None:
                    row[key] = value if row[key] is None else reducer(row[key], value)
            for key, value in (
                ("volume_null_count", volume_null), ("volume_empty_count", volume_empty),
                ("volume_populated_count", volume_populated),
                ("quote_volume_null_count", quote_null),
                ("quote_volume_empty_count", quote_empty),
                ("quote_volume_populated_count", quote_populated),
                ("subsecond_timestamp_count", subsecond),
            ):
                row[key] += value
            row["timestamp_second_offset_counts"][str(second_offset)] = count
    summary = {
        "mode": MODE, "window": dict(_WINDOW), "timezone": "Asia/Seoul",
        "row_count": sum(row["row_count"] for row in rows),
        "symbol_count": len({row["symbol"] for row in rows}),
        "kst_date_count": len({row["kst_date"] for row in rows}),
        "aggregate_row_count": len(rows), "limitation": _LIMITATION,
    }
    metadata = {"summary": summary, "rows": rows}
    return {**metadata, "inventory_sha256": hashlib.sha256(_canonical_bytes(metadata)).hexdigest()}


def _load_specification(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_bytes()
    specification = json.loads(content)
    if not isinstance(specification, dict):
        raise ValueError("inventory specification must be a JSON object")
    expected = {
        "id": SPECIFICATION_ID, "mode": MODE, "window": _WINDOW,
        "allow_columns": list(ALLOW_COLUMNS),
    }
    if any(specification.get(key) != value for key, value in expected.items()):
        raise ValueError("inventory specification does not match the fixed metadata scope")
    if (specification.get("price_or_return_access") is not False
            or type(specification.get("api_requests")) is not int
            or specification["api_requests"] != 0):
        raise ValueError("inventory specification must forbid prices, returns and API requests")
    if (type(specification.get("maximum_aggregate_rows")) is not int
            or specification["maximum_aggregate_rows"] != MAX_AGGREGATE_ROWS
            or type(specification.get("query_wall_seconds")) not in (int, float)
            or specification["query_wall_seconds"] != _MAX_QUERY_SECONDS):
        raise ValueError("inventory specification must retain the fixed resource limits")
    for key in ("source_database", "canonical_runs_path", "output_directory"):
        value = specification.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("inventory specification must pin absolute input/log/output paths")
    # Additional provenance pins are hashed, but cannot alter the fixed query.
    return specification, hashlib.sha256(content).hexdigest()


def _require_distinct_paths(paths: tuple[Path, ...]) -> None:
    """Metadata-only alias check before a logger can accidentally append to input."""
    for index, path in enumerate(paths):
        for other in paths[:index]:
            if path.resolve() == other.resolve() or (
                path.exists() and other.exists() and path.samefile(other)
            ):
                raise ValueError("database, specification, output and log paths must be distinct")


def _database_stat(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()), "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "device": stat.st_dev, "inode": stat.st_ino,
    }


def run_inventory(
    *, db_path: str | Path, specification_path: str | Path,
    output_path: str | Path, runs_path: str | Path,
    before_completion: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Validate registration, log started, read once, and exclusively save JSON.

    File-name/alias checks inspect filesystem metadata before the start write;
    database stat receipts and all SQLite access occur inside the logged callback.
    An existing output is never overwritten and a failed attempt is never retried.
    """
    database, specification_file, output, log = map(
        Path, (db_path, specification_path, output_path, runs_path),
    )
    _require_distinct_paths((database, specification_file, output, log))
    specification, specification_sha256 = _load_specification(specification_file)
    if (database.resolve() != Path(specification["source_database"]).resolve()
            or log.resolve() != Path(specification["canonical_runs_path"]).resolve()
            or output.resolve().parent != Path(specification["output_directory"]).resolve()):
        raise ValueError("inventory paths do not match the registered source/log/output paths")
    source_paths = (Path(__file__), Path(experiment_log.__file__), specification_file)
    source_sha256 = {
        str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    if source_sha256[str(specification_file.resolve())] != specification_sha256:
        raise ValueError("inventory specification changed during validation")

    def evaluate() -> dict[str, Any]:
        if output.exists():
            raise FileExistsError("inventory output already exists")
        before = _database_stat(database)
        inventory = collect_inventory(database)
        after = _database_stat(database)
        envelope = {
            "study_id": SPECIFICATION_ID, "specification_sha256": specification_sha256,
            "database_before": before, "database_after": after, "inventory": inventory,
        }
        content = _canonical_bytes(envelope) + b"\n"
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        if before_completion is not None:
            before_completion()
        if any(
            hashlib.sha256(path.read_bytes()).hexdigest() != source_sha256[str(path.resolve())]
            for path in source_paths
        ):
            raise ValueError("inventory source or specification changed before completion")
        return {
            "output_path": str(output.resolve()), "output_sha256": hashlib.sha256(content).hexdigest(),
            "inventory_sha256": inventory["inventory_sha256"], "summary": inventory["summary"],
            "database_before": before, "database_after": after,
        }

    return experiment_log.run_discovery_trial(
        study_id=SPECIFICATION_ID, specification_sha256=specification_sha256,
        parameters={
            "mode": MODE, "db_path": str(database), "output_path": str(output),
            "specification_path": str(specification_file), "allow_columns": list(ALLOW_COLUMNS),
            "price_or_return_access": False, "api_requests": 0,
            "source_sha256": source_sha256,
        },
        window=dict(_WINDOW), evaluate=evaluate, runs_path=log,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--specification", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs-path", required=True)
    arguments = parser.parse_args(argv)
    try:
        record = run_inventory(
            db_path=arguments.db, specification_path=arguments.specification,
            output_path=arguments.output, runs_path=arguments.runs_path,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"inventory failed ({type(exc).__name__}); no automatic retry\n")
    print(json.dumps({
        "run_id": record["run_id"], "status": record["status"],
        "inventory_sha256": record["result"]["inventory_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
