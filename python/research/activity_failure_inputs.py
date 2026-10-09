"""Task BI's exact code/session reader; no selection or performance replay.

The caller registers and pins the requested union and complete index calendar
before calling this function. Only date/OHLC/turnover for that union is read.
Progress is validated as collector metadata before ANY price query, without
scanning other sessions to recalculate its full-code counts. A snapshot digest
identifies typed consumed rows, not the database, original vintage, price basis
or historical completeness. Missing, NULL and invalid rows remain unresolved.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
from types import MappingProxyType

from research.activity_screen import ScreenObservation
from research.activity_timing import AvailabilityMetadata, SessionLagPolicy


PANEL_START = date(2019, 1, 2)
PANEL_END = date(2026, 9, 18)
MAX_FORMATION = date(2025, 12, 1)
READ_COLUMNS = ("bsop_date", "open", "high", "low", "close", "turnover")
_POLICY = "task-bi-next-trading-session-0830-kst"
_SOURCE = "stored KIS adjusted daily scan_bars OHLC/turnover"


@dataclass(frozen=True)
class ActivityFailureProgress:
    code: str
    first_date: date
    last_date: date
    bars: int
    frozen: int
    status: str
    fetched_at: str


@dataclass(frozen=True)
class ActivityFailureRowState:
    code: str
    observation_date: date
    state: str
    issues: tuple[str, ...]
    availability: AvailabilityMetadata
    turnover: Decimal | None


@dataclass(frozen=True)
class ActivityFailureSnapshot:
    dataset_sha256: str
    snapshot_received_at: datetime
    read_scope: tuple[tuple[str, tuple[date, ...]], ...]
    progress: tuple[ActivityFailureProgress, ...]
    row_states: tuple[ActivityFailureRowState, ...]
    fetched_row_count: int
    missing_row_count: int

    def to_dict(self) -> dict:
        """Private JSON audit with exact valid turnover; no OHLC price values."""
        return {
            "schema": "task-bi-activity-failure-snapshot-v1",
            "dataset_sha256": self.dataset_sha256,
            "fingerprint_scope": "typed consumed rows, exact requested union, panel and progress",
            "whole_database_hash": False,
            "original_source_vintage_certified": False,
            "snapshot_received_at": self.snapshot_received_at.isoformat(),
            "snapshot_received_at_meaning": "local snapshot receipt, not original row retrieval",
            "read_columns": list(READ_COLUMNS),
            "panel": {"id": 1, "start": PANEL_START.isoformat(), "end": PANEL_END.isoformat()},
            "read_scope": [
                {"code": code, "dates": [day.isoformat() for day in days]}
                for code, days in self.read_scope
            ],
            "progress": [
                {
                    "code": row.code, "first_date": row.first_date.isoformat(),
                    "last_date": row.last_date.isoformat(), "bars": row.bars,
                    "frozen": row.frozen, "status": row.status, "fetched_at": row.fetched_at,
                    "fetched_at_meaning": "collector progress receipt; row retrieval remains unknown",
                }
                for row in self.progress
            ],
            "progress_counts_recomputed_from_prices": False,
            "requested_rows_checked_against_progress": True,
            "fetched_row_count": self.fetched_row_count,
            "missing_row_count": self.missing_row_count,
            "row_states": [
                {"code": row.code, "observation_date": row.observation_date.isoformat(),
                 "state": row.state, "issues": list(row.issues)}
                for row in self.row_states
            ],
            "row_provenance": [
                {
                    "code": row.code,
                    "state": row.state,
                    "issues": list(row.issues),
                    "turnover": None if row.turnover is None else str(row.turnover),
                    "observation_date": row.availability.observation_date.isoformat(),
                    "available_at": row.availability.available_at.isoformat(),
                    "retrieved_at": row.availability.retrieved_at,
                    "source": row.availability.source,
                    "is_final": row.availability.is_final,
                    "data_vintage": row.availability.data_vintage,
                    "evidence_level": row.availability.evidence_level,
                    "source_public_available_at": None,
                    "availability_policy": row.availability.availability_policy,
                    "evidence_reference": row.availability.evidence_reference,
                    "snapshot_sha256": self.dataset_sha256,
                }
                for row in self.row_states
            ],
        }


@dataclass(frozen=True)
class ActivityFailureInputs:
    observations: Mapping[str, Mapping[date, ScreenObservation]]
    snapshot: ActivityFailureSnapshot


def _scope(
    calendar: tuple[date, ...], requested_dates: Mapping[str, tuple[date, ...]],
) -> tuple[tuple[str, tuple[date, ...]], ...]:
    if (type(calendar) is not tuple or not calendar
            or any(type(day) is not date for day in calendar)
            or any(a >= b for a, b in zip(calendar, calendar[1:]))):
        raise ValueError("pinned index calendar must be an increasing immutable date tuple")
    if not isinstance(requested_dates, Mapping) or not requested_dates:
        raise ValueError("an explicit nonempty code/date union is required")
    positions = {day: index for index, day in enumerate(calendar)}
    result = []
    for code, days in requested_dates.items():
        if not isinstance(code, str) or re.fullmatch(r"[0-9A-Z]{6}", code) is None:
            raise ValueError("requested codes must be exact six-character issue codes")
        if (type(days) is not tuple or not days or any(type(day) is not date for day in days)
                or any(a >= b for a, b in zip(days, days[1:]))):
            raise ValueError("requested sessions must be explicit increasing date tuples")
        if any(not PANEL_START <= day <= MAX_FORMATION or day not in positions
               or positions[day] + 1 >= len(calendar) for day in days):
            raise ValueError("requested session is outside Task BI scope or lacks its next session")
        result.append((code, days))
    return tuple(sorted(result))


def _scan_date(value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise ValueError("scan_progress dates must be canonical YYYYMMDD text")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError as exc:
        raise ValueError("invalid scan_progress date") from exc


def _progress(code: str, rows: list[tuple]) -> ActivityFailureProgress:
    if len(rows) != 1:
        raise ValueError("every requested code needs exactly one complete scan_progress row")
    first, last, bars, frozen, status, fetched_at = rows[0]
    if (status != "done" or type(bars) is not int or bars < 1
            or type(frozen) is not int or not 0 <= frozen <= bars):
        raise ValueError("requested scan_progress status/counts must be complete and valid")
    first_day, last_day = _scan_date(first), _scan_date(last)
    if (not PANEL_START <= first_day <= last_day <= PANEL_END
            or bars > (last_day - first_day).days + 1):
        raise ValueError("requested scan_progress bounds/count disagree with the spent panel")
    if not isinstance(fetched_at, str) or not fetched_at or fetched_at != fetched_at.strip():
        raise ValueError("scan_progress fetched_at must be an explicit aware receipt")
    try:
        timestamp = datetime.fromisoformat(fetched_at)
    except ValueError as exc:
        raise ValueError("invalid scan_progress fetched_at") from exc
    if timestamp.utcoffset() is None:
        raise ValueError("scan_progress fetched_at must be aware")
    return ActivityFailureProgress(code, first_day, last_day, bars, frozen, status, fetched_at)


def _typed(value: object) -> list:
    # Preserve storage types AND exact text, including invalid values. Float hex
    # is fingerprinting only; no float is accepted for numeric interpretation.
    if value is None:
        return ["null", None]
    if type(value) is str:
        return ["text", value]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        return ["real", value.hex()]
    if type(value) is bytes:
        return ["blob", value.hex()]
    raise ValueError("unsupported SQLite storage value")


def _observation(raw: tuple | None) -> tuple[str, Decimal | None, tuple[str, ...]]:
    if raw is None:
        return "missing", None, ("absent_requested_row:unknown_cause",)
    amounts, issues = [], []
    for field, value in zip(READ_COLUMNS[1:], raw[1:]):
        if value is None:
            amounts.append(None)
            issues.append(f"null:{field}")
            continue
        parsed = None
        if isinstance(value, str) and value and value == value.strip():
            try:
                candidate = Decimal(value)
            except InvalidOperation:
                pass
            else:
                if candidate.is_finite() and (candidate >= 0 if field == "turnover" else candidate > 0):
                    parsed = candidate
        if parsed is None:
            issues.append(f"invalid:{field}")
        amounts.append(parsed)
    opening, high, low, close, turnover = amounts
    if all(value is not None for value in amounts[:4]):
        if not low <= min(opening, close) <= max(opening, close) <= high:
            issues.append("invalid:ohlc_bounds")
    if issues:
        state = "invalid" if any(issue.startswith("invalid:") for issue in issues) else "null"
        return state, None, tuple(issues)
    return ("frozen" if opening == high == low == close and turnover == 0 else "observed"), turnover, ()


def load_activity_failure_inputs(
    scan_path: Path, calendar: tuple[date, ...],
    requested_dates: Mapping[str, tuple[date, ...]],
) -> ActivityFailureInputs:
    """Load the registered exact union in one read-only SQLite transaction.

    A caller must log/freeze actual access beforehand. This API performs no
    retries and cannot expand its own scope. The calendar's completeness and
    BB normal/frozen reconciliation remain caller obligations. Its D+1 08:30
    availability is assumed, with original publication/retrieval/finality and
    source vintage unknown. ``fetched_at`` is never promoted to row retrieval.
    """
    scope = _scope(calendar, requested_dates)
    if not isinstance(scan_path, Path):
        raise ValueError("scan_path must be an explicit Path")
    scan_stat = scan_path.lstat()
    if scan_path.is_symlink() or not stat.S_ISREG(scan_stat.st_mode):
        raise ValueError("scan database must be an existing nonsymlink regular file")
    digest = hashlib.sha256()

    def add(value: object) -> None:
        digest.update(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")

    add(["schema", "task-bi-activity-failure-inputs-v1"])
    add(["calendar", [day.isoformat() for day in calendar]])
    add(["scope", [[code, [day.isoformat() for day in days]] for code, days in scope]])
    raw_rows = {}
    with closing(sqlite3.connect(scan_path.resolve().as_uri() + "?mode=ro", uri=True,
                                timeout=10, isolation_level=None)) as scan:
        scan.execute("PRAGMA query_only=ON")
        scan.execute("BEGIN")
        table_types = scan.execute(
            "SELECT name,type FROM sqlite_master WHERE name IN ('scan_panel','scan_progress','scan_bars') "
            "ORDER BY name",
        ).fetchall()
        if table_types != [("scan_bars", "table"), ("scan_panel", "table"), ("scan_progress", "table")]:
            raise ValueError("registered scan inputs must be SQLite tables")
        panel = scan.execute("SELECT id,start,end FROM scan_panel ORDER BY id").fetchall()
        if panel != [(1, "20190102", "20260918")]:
            raise ValueError("only the exact registered spent scan_panel is allowed")
        add(["panel", [[_typed(value) for value in row] for row in panel]])
        progress = []
        # Complete the validation for ALL codes before any scan_bars read.
        for code, _ in scope:
            rows = scan.execute(
                "SELECT first_date,last_date,bars,frozen,status,fetched_at "
                "FROM scan_progress WHERE code=?", (code,),
            ).fetchall()
            progress.append(_progress(code, rows))
            add(["progress", code, [[_typed(value) for value in row] for row in rows]])
        progress_by_code = {row.code: row for row in progress}
        for code, days in scope:
            code_count = 0
            # Bound parameters below old SQLite variable limits. Each query
            # still projects exactly the requested code/date set, no envelope.
            for offset in range(0, len(days), 200):
                batch = days[offset:offset + 200]
                placeholders = ",".join("?" for _ in batch)
                query = ("SELECT bsop_date,open,high,low,close,turnover FROM scan_bars "
                         f"WHERE code=? AND bsop_date IN ({placeholders}) ORDER BY bsop_date")
                for raw in scan.execute(query, (code, *(day.strftime("%Y%m%d") for day in batch))):
                    key = (code, raw[0])
                    if key in raw_rows:
                        raise ValueError("duplicate requested code/date observation")
                    bounds = progress_by_code[code]
                    if not bounds.first_date <= _scan_date(raw[0]) <= bounds.last_date:
                        raise ValueError("requested observation is outside its scan_progress bounds")
                    code_count += 1
                    if code_count > bounds.bars:
                        raise ValueError("requested row count exceeds full-code scan_progress bars")
                    raw_rows[key] = raw
        for code, days in scope:
            for day in days:
                raw = raw_rows.get((code, day.strftime("%Y%m%d")))
                add(["row", code, day.isoformat(),
                     "absent" if raw is None else [_typed(value) for value in raw]])
        scan.execute("ROLLBACK")
    snapshot_received_at = datetime.now(timezone.utc)
    dataset_sha256 = digest.hexdigest()
    positions = {day: index for index, day in enumerate(calendar)}
    policy = SessionLagPolicy(1)
    # Reuse each calendar-derived cutoff across codes, validating the pinned
    # calendar once per distinct requested session rather than once per row.
    modeled_available = {
        day: policy.selection_at(calendar, positions[day])
        for day in {day for _, days in scope for day in days}
    }
    observations, states = {}, []
    for code, days in scope:
        rows = {}
        for day in days:
            state, turnover, issues = _observation(raw_rows.get((code, day.strftime("%Y%m%d"))))
            metadata = AvailabilityMetadata(
                observation_date=day, available_at=modeled_available[day],
                retrieved_at="unknown", source=_SOURCE, is_final=None, data_vintage=None,
                evidence_level="assumed", availability_policy=_POLICY,
                evidence_reference=f"Task BI registered snapshot sha256:{dataset_sha256}",
            )
            rows[day] = ScreenObservation(state, turnover, None, metadata)
            states.append(ActivityFailureRowState(code, day, state, issues, metadata, turnover))
        observations[code] = MappingProxyType(rows)
    snapshot = ActivityFailureSnapshot(
        dataset_sha256, snapshot_received_at, scope, tuple(progress), tuple(states),
        len(raw_rows), sum(row.state == "missing" for row in states),
    )
    return ActivityFailureInputs(MappingProxyType(observations), snapshot)
