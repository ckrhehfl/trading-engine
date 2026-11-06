"""EG-owned deterministic original-format fixtures, never market-data readers.

Only the calendar/population geometry helpers are reused. This generator does
not invoke an old runtime, profile, unit or its execution authorization. Rows
are invented directly; compressed and decoded pins describe only files newly
created by this call. Partial failures remain exclusive, non-reusable paths.
"""
from __future__ import annotations

from collections.abc import Callable
import gzip
import hashlib
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire


RETRIEVED_AT = "2026-11-06T00:00:00Z"
CODES = tuple(f"{number:06}" for number in range(1, 19))


def _layout(sessions: int) -> tuple[dict, tuple[str, ...], dict, tuple]:
    """Pure original-sized invented coordinates; no legacy execution hooks."""
    from research.intraday_ed_comparison_unit import _invented_calendar, _invented_universe
    from research.intraday_trader_unit import invented_geometry

    geometry = invented_geometry(sessions)
    days = _invented_calendar(sessions)
    universe, coordinates = _invented_universe(days, CODES, geometry)
    return geometry, days, universe, coordinates


def _close(base: int, case: int, day_index: int, minute: int) -> int:
    if minute < 0:
        return base
    # Twenty ordinary sessions precede each four-session invented shock. The
    # first shock is day21, so a 21-session lifecycle reaches complete history.
    shock = day_index % 24 >= 20
    amplitude = 1
    if case == 4:
        amplitude = 0  # A genuinely flat path and a zero baseline, not missing.
    elif shock and case != 0 and (case != 2 or minute >= 30):
        amplitude = 4 + (day_index // 24) % 3
    if case == 5:
        # A directional downside path complements the alternating large moves.
        return base - amplitude * (minute + 1)
    return base + (amplitude if minute % 2 == 0 else 0)


def _chunk(kind: str, day: str, symbol: str, count: int, day_index: int,
           symbol_index: int, daily: bool) -> dict:
    from research.intraday_pattern_calibration import session_open

    opening_ms = int(session_open(day).timestamp()) * 1000
    case, base = symbol_index % 8, 10000 + 100 * symbol_index
    shock = day_index % 24 >= 20
    rows = []
    for offset in range(count):
        # Shift timestamps rather than dropping rows: total source geometry is
        # unchanged while one true minute is absent. No synthetic flat fill.
        gap = 45 if shock and case == 3 else 75 if shock and case == 6 and kind == "CX" else None
        minute = offset + int(gap is not None and offset >= gap)
        row = {"open_time_ms": opening_ms + minute * 60000, "fetched_at": RETRIEVED_AT}
        if kind == "CC":
            op = _close(base, case, day_index, minute - 1)
            close = _close(base, case, day_index, minute)
            row.update(symbol=symbol, interval="1m", open=str(op), high=str(max(op, close) + 1),
                       low=str(min(op, close) - 1), close=str(close))
            if shock and case == 7 and minute == 95:
                row["open"] = "invalid-invented-number"
        else:
            row.update(volume="0" if minute == 120 else str(100 + day_index), quote_volume=None)
        rows.append(row)
    value = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
             "retrieved_at": RETRIEVED_AT, "source": "collector klines", "is_final": None,
             "evidence_level": "assumed", "rows": rows}
    if kind == "CX":
        value.update(daily_rows=[{"open_time_ms": opening_ms, "fetched_at": RETRIEVED_AT,
                                 "volume": "100000", "quote_volume": None}] if daily else [],
                     daily_retrieved_at=RETRIEVED_AT)
    return value


def make_invented(directory: Path, sessions: int, checkpoint: Callable[[str], object], *,
                  full: bool = False) -> dict:
    """Create an exclusive CB/CC/CX bundle for the closed EG computation.

    ``full=True`` requires the complete 257-session geometry. The returned
    roster and decoded pins fit ``intraday_highvol_unit.compute_coverage``;
    no legacy envelope or resource profile is returned or authorized.
    """
    wire.require(isinstance(directory, Path) and type(full) is bool and callable(checkpoint),
                 "EG invented generator arguments differ")
    wire.require(type(sessions) is int and 1 <= sessions <= 257
                 and (not full or sessions == 257), "EG invented session geometry differs")
    geometry, days, universe, coordinates = _layout(sessions)
    checkpoint("eg_invented_before_create")
    directory.mkdir(mode=0o700, exist_ok=False)
    wire.exclusive_bytes(directory / "CB", wire.encode(universe))
    day_indexes = {day: index for index, day in enumerate(days)}
    symbol_indexes = {symbol: index for index, symbol in enumerate(sorted({row[1] for row in coordinates}))}
    decoded = {}
    for kind in ("CC", "CX"):
        digest = hashlib.sha256()
        descriptor = os.open(directory / kind, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", compresslevel=1, mtime=0) as compressed:
                for index, (day, symbol, count) in enumerate(coordinates):
                    checkpoint("eg_invented_raw_chunk")
                    value = _chunk(kind, day, symbol, count, day_indexes[day], symbol_indexes[symbol],
                                   kind == "CX" and index < geometry["cx_daily_rows"])
                    raw = wire.encode(value)
                    compressed.write(raw)
                    digest.update(raw)
            stream.flush()
            os.fsync(stream.fileno())
        decoded[kind] = digest.hexdigest()
    roster = []
    for name, maximum in wire.EE_INPUT_BOUNDS:
        digest, size = wire.file_digest(directory / name, maximum, checkpoint)
        roster.append({"name": name, "path": str(directory / name), "sha256": digest,
                       "maximum": maximum, "size": size})
    checkpoint("eg_invented_complete")
    return {"invented_only": True, "geometry": geometry, "local_received_sources": roster,
            "decoded_archives": decoded}
