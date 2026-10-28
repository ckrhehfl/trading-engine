"""EC's fixed-window, price-free inventory and same-snapshot independent check.

Actual use requires an outer registered caller to durably log ``started`` before
calling this module, and to enforce the ordinary shared producer/verifier process
budget. This module neither logs trials nor opens APIs. Missing required columns
refuse the read; an absent column is never manufactured as a zero presence count.
No price, numeric volume or turnover amount leaves SQLite. The metadata digest
identifies the returned projection, not the database file or economic coverage.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from research.intraday_scope_inventory import (
    END_EXCLUSIVE_MS,
    MAX_AGGREGATE_ROWS,
    START_MS,
    _readonly_inventory_connection,
)


WINDOW = {
    "namespaces": ["KRX:*", "KRX-INDEX:*"], "interval": "1m",
    "start": "2025-09-04", "end": "2026-09-23",
}
_UNIX_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_NAMESPACES = ("KRX", "KRX-INDEX")
_LIMITATION = (
    "Stored metadata in the fixed discovery scope only. An absent namespace "
    "means no stored rows in that scope, not absence of economic market data. "
    "Counts do not establish session completeness, historical universe "
    "eligibility, tradability, or a current top-20 ranking."
)

# The old KRX-only callable remains unchanged. Both new queries fix the two
# namespaces and use unwrapped time bounds; no caller-supplied SQL is accepted.
_PRODUCER_SQL = """
SELECT symbol,
       date(open_time_ms / 1000, 'unixepoch', '+09:00') AS kst_date,
       (open_time_ms / 1000) % 60 AS second_offset,
       count(*), min(open_time_ms), max(open_time_ms),
       min(fetched_at), max(fetched_at),
       count(volume), sum(volume = ''),
       count(quote_volume), sum(quote_volume = ''),
       sum(open_time_ms % 1000 != 0)
FROM klines
WHERE interval = '1m'
  AND (symbol GLOB 'KRX:*' OR symbol GLOB 'KRX-INDEX:*')
  AND open_time_ms >= ? AND open_time_ms < ?
GROUP BY symbol, kst_date, second_offset
ORDER BY symbol, kst_date, second_offset
"""

# No aggregate, SQLite date conversion or producer grouping is reused here.
# Only timestamps, retrieval text and categorical presence codes are returned;
# even the verifier never fetches the volume/turnover amounts into Python.
_VERIFIER_SQL = """
SELECT symbol, open_time_ms, fetched_at,
       CASE WHEN volume IS NULL THEN 0 WHEN volume = '' THEN 1 ELSE 2 END,
       CASE WHEN quote_volume IS NULL THEN 0 WHEN quote_volume = '' THEN 1 ELSE 2 END
FROM klines
WHERE interval = '1m'
  AND symbol >= 'KRX:' AND symbol < 'KRX;'
  AND open_time_ms >= ? AND open_time_ms < ?
UNION ALL
SELECT symbol, open_time_ms, fetched_at,
       CASE WHEN volume IS NULL THEN 0 WHEN volume = '' THEN 1 ELSE 2 END,
       CASE WHEN quote_volume IS NULL THEN 0 WHEN quote_volume = '' THEN 1 ELSE 2 END
FROM klines
WHERE interval = '1m'
  AND symbol >= 'KRX-INDEX:' AND symbol < 'KRX-INDEX;'
  AND open_time_ms >= ? AND open_time_ms < ?
"""


def _checkpoint(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _produce_inventory(
    connection: sqlite3.Connection, checkpoint: Callable[[], None] | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for number, part in enumerate(connection.execute(
        _PRODUCER_SQL, (START_MS, END_EXCLUSIVE_MS),
    )):
        if number % 1024 == 0:
            _checkpoint(checkpoint)
        (symbol, day, offset, count, first_ms, last_ms, fetched_min, fetched_max,
         volume_nonnull, volume_empty, quote_nonnull, quote_empty, subsecond) = part
        if (day is None or type(offset) is not int
                or type(first_ms) is not int or type(last_ms) is not int
                or any(value is not None and not isinstance(value, str)
                       for value in (fetched_min, fetched_max))):
            raise ValueError("invalid timestamp or retrieval metadata in campaign scope")
        # SUM(comparison) is NULL only when every value is NULL. COUNT(column)
        # independently distinguishes that case; populated includes numeric zero.
        # Full per-source-row type validation belongs to the independent stream
        # and must succeed before this aggregate can leave collect_verified_inventory.
        volume_empty = 0 if volume_empty is None else volume_empty
        quote_empty = 0 if quote_empty is None else quote_empty
        if not rows or (rows[-1]["symbol"], rows[-1]["kst_date"]) != (symbol, day):
            if len(rows) >= MAX_AGGREGATE_ROWS:
                raise ValueError("campaign inventory aggregate row limit exceeded")
            rows.append({
                "namespace": symbol.split(":", 1)[0], "symbol": symbol,
                "interval": "1m", "kst_date": day, "row_count": 0,
                "first_open_time_ms": first_ms, "last_open_time_ms": last_ms,
                "fetched_at_min": None, "fetched_at_max": None,
                "volume_null_count": 0, "volume_empty_count": 0,
                "volume_populated_count": 0, "quote_volume_null_count": 0,
                "quote_volume_empty_count": 0, "quote_volume_populated_count": 0,
                "subsecond_timestamp_count": 0, "timestamp_second_offset_counts": {},
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
            ("volume_null_count", count - volume_nonnull), ("volume_empty_count", volume_empty),
            ("volume_populated_count", volume_nonnull - volume_empty),
            ("quote_volume_null_count", count - quote_nonnull),
            ("quote_volume_empty_count", quote_empty),
            ("quote_volume_populated_count", quote_nonnull - quote_empty),
            ("subsecond_timestamp_count", subsecond),
        ):
            row[key] += value
        row["timestamp_second_offset_counts"][str(offset)] = count
    namespaces = {}
    for namespace in _NAMESPACES:
        selected = [row for row in rows if row["namespace"] == namespace]
        namespaces[namespace] = {
            "row_count": sum(row["row_count"] for row in selected),
            "symbol_count": len({row["symbol"] for row in selected}),
            "kst_date_count": len({row["kst_date"] for row in selected}),
            "aggregate_row_count": len(selected),
            "status": "stored_rows_present" if selected else "stored_namespace_absent_in_fixed_scope",
        }
    return {
        "summary": {
            "mode": "discovery-metadata-only", "window": dict(WINDOW),
            "timezone": "Asia/Seoul", "row_count": sum(row["row_count"] for row in rows),
            "symbol_count": len({row["symbol"] for row in rows}),
            "kst_date_count": len({row["kst_date"] for row in rows}),
            "aggregate_row_count": len(rows), "namespaces": namespaces,
            "limitation": _LIMITATION,
        },
        "rows": rows,
    }


def _reconstruct_inventory(
    connection: sqlite3.Connection, checkpoint: Callable[[], None] | None,
) -> dict[str, Any]:
    # Numeric KST day buckets avoid per-bar datetime and string construction.
    # Each group stores only fixed counters, 60 offset counts, and four extrema;
    # source rows/timestamps are never retained. Calendar text is made once per
    # group, independently of the producer's SQLite date conversion.
    groups: dict[tuple[str, int], tuple[list[int], list[int], list[Any]]] = {}
    previous_symbol = None
    previous_day = None
    counts: list[int] = []
    offsets: list[int] = []
    extrema: list[Any] = []
    totals = {namespace: 0 for namespace in _NAMESPACES}
    symbols: dict[str, set[str]] = {namespace: set() for namespace in _NAMESPACES}
    days: dict[str, set[int]] = {namespace: set() for namespace in _NAMESPACES}
    aggregate_counts = {namespace: 0 for namespace in _NAMESPACES}
    labels = ("null", "empty", "populated")
    for number, (symbol, stamp, fetched, volume_state, quote_state) in enumerate(
        connection.execute(_VERIFIER_SQL, (START_MS, END_EXCLUSIVE_MS) * 2)
    ):
        if number % 4096 == 0:
            _checkpoint(checkpoint)
        if type(stamp) is not int or (fetched is not None and not isinstance(fetched, str)):
            raise ValueError("invalid timestamp or retrieval metadata during independent check")
        day = (stamp + 32_400_000) // 86_400_000
        if symbol != previous_symbol or day != previous_day:
            key = (symbol, day)
            state = groups.get(key)
            if state is None:
                if len(groups) >= MAX_AGGREGATE_ROWS:
                    raise ValueError("campaign inventory aggregate row limit exceeded")
                namespace = symbol.partition(":")[0]
                if namespace not in _NAMESPACES:
                    raise ValueError("unexpected stored namespace during independent check")
                # count, volume null/empty/populated, quote null/empty/populated,
                # subsecond count; extrema: first/last/fetched min/fetched max.
                state = ([0] * 8, [0] * 60, [stamp, stamp, fetched, fetched])
                groups[key] = state
                aggregate_counts[namespace] += 1
                symbols[namespace].add(symbol)
                days[namespace].add(day)
            counts, offsets, extrema = state
            previous_symbol, previous_day = symbol, day
        counts[0] += 1
        counts[1 + volume_state] += 1
        counts[4 + quote_state] += 1
        counts[7] += bool(stamp % 1000)
        offsets[(stamp // 1000) % 60] += 1
        if stamp < extrema[0]:
            extrema[0] = stamp
        if stamp > extrema[1]:
            extrema[1] = stamp
        if fetched is not None:
            if extrema[2] is None or fetched < extrema[2]:
                extrema[2] = fetched
            if extrema[3] is None or fetched > extrema[3]:
                extrema[3] = fetched
    rows = []
    for (symbol, day), (counts, offsets, extrema) in sorted(groups.items()):
        namespace = symbol.partition(":")[0]
        totals[namespace] += counts[0]
        rows.append({
            "namespace": namespace, "symbol": symbol, "interval": "1m",
            "kst_date": date.fromordinal(_UNIX_EPOCH_ORDINAL + day).isoformat(),
            "row_count": counts[0], "first_open_time_ms": extrema[0],
            "last_open_time_ms": extrema[1], "fetched_at_min": extrema[2],
            "fetched_at_max": extrema[3],
            **{f"{field}_{label}_count": counts[1 + field_index * 3 + label_index]
               for field_index, field in enumerate(("volume", "quote_volume"))
               for label_index, label in enumerate(labels)},
            "subsecond_timestamp_count": counts[7],
            "timestamp_second_offset_counts": {
                str(offset): count for offset, count in enumerate(offsets) if count
            },
        })
    namespaces = {
        namespace: {
            "row_count": totals[namespace], "symbol_count": len(symbols[namespace]),
            "kst_date_count": len(days[namespace]),
            "aggregate_row_count": aggregate_counts[namespace],
            "status": "stored_rows_present" if totals[namespace] else "stored_namespace_absent_in_fixed_scope",
        }
        for namespace in _NAMESPACES
    }
    return {
        "summary": {
            "mode": "discovery-metadata-only", "window": dict(WINDOW),
            "timezone": "Asia/Seoul", "row_count": sum(totals.values()),
            "symbol_count": len(set().union(*symbols.values())),
            "kst_date_count": len(set().union(*days.values())),
            "aggregate_row_count": len(groups), "namespaces": namespaces,
            "limitation": _LIMITATION,
        },
        "rows": rows,
    }


def collect_verified_inventory(
    db_path: str | Path, *, checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Return inventory plus receipt only after independent exact reconstruction.

    Durable started and the ordinary shared process limits are the outer
    caller's responsibility. The existing read-only authorizer and its one
    cumulative 60-second/200-million-step query budget cover both passes in one
    transaction. ``checkpoint`` lets that caller check its broader budget.
    Failure returns no partial success and causes no automatic retry.
    """
    _checkpoint(checkpoint)
    with _readonly_inventory_connection(db_path) as connection:
        produced = _produce_inventory(connection, checkpoint)
        _checkpoint(checkpoint)
        reconstructed = _reconstruct_inventory(connection, checkpoint)
        _checkpoint(checkpoint)
        producer_hash = _digest(produced)
        verifier_hash = _digest(reconstructed)
        if produced != reconstructed or producer_hash != verifier_hash:
            raise ValueError("campaign inventory independent verification mismatch")
    _checkpoint(checkpoint)
    return {
        "inventory": {**produced, "inventory_sha256": producer_hash},
        "verification": {
            "status": "verified", "method": "independent-timestamp-stream-v1",
            "same_snapshot": True, "producer_sha256": producer_hash,
            "verifier_sha256": verifier_hash, "compared_aggregate_rows": len(produced["rows"]),
        },
    }
