"""Bounded EF chart and supervised snapshots over caller-supplied evidence.

The feature database has no supervised table. Opening it never opens the
label database. A complete seal binds both immutable files to the same
original population and caller provenance. Source authentication and
independent reconstruction belong to the callers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction
import hashlib
from itertools import groupby, product
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct

from research import intraday_campaign_wsl_transport as wire
from research.intraday_chart_tensors import ChartTensor, PeerTensor
from research.intraday_ed_inputs import MINUTE
from research.intraday_ed_sources import TargetCoordinate, _canonical, _database, _private_parent


FEATURE_OFFSETS = range(60, 316, 5)
ENTRY_OFFSETS = range(60, 301, 15)
QUOTE_OFFSETS = range(60, 331, 5)
_HEAD_HORIZONS = (30, 15, 15, 15, 30, 30)
_SCENARIOS = ("S1", "S2")
_CHART = struct.Struct("<749d")
_PEER = struct.Struct("<181d")
_MAX_EVIDENCE = 256 * 1024
_MAX_FILE = 12 * 1024**3
_MAX_RATIO = 2048
_SCHEMA_ID = "EF-chart-snapshot-v1"
_Checkpoint = Callable[[str], object]
_END = object()
_SIDECARS = ("-journal", "-wal", "-shm")


class SnapshotRefusal(ValueError):
    """An invalid or unsealed snapshot, rather than unavailable evidence."""


@dataclass(frozen=True)
class SnapshotMetadata:
    sessions: tuple[str, ...]
    targets: tuple[TargetCoordinate, ...]
    provenance: bytes


@dataclass(frozen=True)
class DayMetadata:
    day: str
    opening: datetime
    closing: datetime
    provenance: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class PeerRow:
    scenario: str
    tensor: PeerTensor | None
    evidence: bytes


@dataclass(frozen=True)
class SnapshotRow:
    session: str
    symbol: str
    offset: int
    chart: ChartTensor | None
    peers: tuple[PeerRow, ...]
    execution_open: Fraction | None
    execution_state: str
    evidence: bytes


@dataclass(frozen=True)
class LabelHead:
    value: Fraction
    mature_at: datetime


@dataclass(frozen=True)
class LabelRow:
    session: str
    symbol: str
    offset: int
    heads: tuple[LabelHead | None, ...]
    evidence: bytes


@dataclass(frozen=True)
class SnapshotDay(DayMetadata):
    rows: Iterable[tuple[SnapshotRow, LabelRow | None]]


@dataclass(frozen=True)
class SnapshotSeal:
    fingerprint: str
    features_sha256: str
    labels_sha256: str


_FEATURE_SCHEMA = (
    "CREATE TABLE manifest (fingerprint TEXT NOT NULL, provenance BLOB NOT NULL) STRICT",
    "CREATE TABLE days (session TEXT PRIMARY KEY, opening TEXT NOT NULL, closing TEXT NOT NULL) STRICT, WITHOUT ROWID",
    "CREATE TABLE targets (session TEXT NOT NULL, scenario TEXT NOT NULL, rank INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, count INTEGER, state TEXT NOT NULL, metadata BLOB NOT NULL, "
    "PRIMARY KEY(session,scenario,rank), UNIQUE(session,scenario,symbol)) STRICT, WITHOUT ROWID",
    "CREATE TABLE provenance (session TEXT NOT NULL, symbol TEXT NOT NULL, raw BLOB NOT NULL, "
    "PRIMARY KEY(session,symbol)) STRICT, WITHOUT ROWID",
    "CREATE TABLE features (session TEXT NOT NULL, offset INTEGER NOT NULL, symbol TEXT NOT NULL, "
    "chart BLOB, execution_open TEXT, execution_state TEXT NOT NULL, evidence BLOB NOT NULL, "
    "PRIMARY KEY(session,offset,symbol)) STRICT, WITHOUT ROWID",
    "CREATE TABLE peers (session TEXT NOT NULL, offset INTEGER NOT NULL, symbol TEXT NOT NULL, "
    "scenario TEXT NOT NULL, tensor BLOB, evidence BLOB NOT NULL, "
    "PRIMARY KEY(session,offset,symbol,scenario)) STRICT, WITHOUT ROWID",
)
_LABEL_SCHEMA = (
    "CREATE TABLE manifest (fingerprint TEXT NOT NULL, features_sha256 TEXT NOT NULL) STRICT",
    "CREATE TABLE labels (session TEXT NOT NULL, offset INTEGER NOT NULL, symbol TEXT NOT NULL, "
    "value0 TEXT, time0 TEXT, value1 TEXT, time1 TEXT, value2 TEXT, time2 TEXT, "
    "value3 TEXT, time3 TEXT, value4 TEXT, time4 TEXT, value5 TEXT, time5 TEXT, "
    "evidence BLOB NOT NULL, PRIMARY KEY(session,offset,symbol)) STRICT, WITHOUT ROWID",
)
_TARGET_FIELDS = "session,scenario,rank,symbol,count,state,metadata"
_LABEL_FIELDS = "session,offset,symbol,value0,time0,value1,time1,value2,time2,value3,time3,value4,time4,value5,time5"


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise SnapshotRefusal(reason)


def _bytes(raw: bytes) -> bytes:
    _need(type(raw) is bytes and 0 < len(raw) <= _MAX_EVIDENCE, "invalid_snapshot_evidence")
    return raw


def _clock(value: datetime) -> str:
    _need(type(value) is datetime and value.utcoffset() is not None
          and value.second == value.microsecond == 0, "invalid_snapshot_clock")
    return value.isoformat(timespec="minutes")


def _datetime(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    _need(_clock(value) == raw, "noncanonical_snapshot_clock")
    return value


def _ratio(value: Fraction) -> str:
    _need(type(value) is Fraction and max(value.numerator.bit_length(),
          value.denominator.bit_length()) <= 3000, "invalid_snapshot_ratio")
    raw = str(value)
    _need(len(raw) <= _MAX_RATIO, "snapshot_ratio_too_long")
    return raw


def _fraction(raw: str) -> Fraction:
    _need(type(raw) is str and len(raw) <= _MAX_RATIO, "invalid_snapshot_ratio")
    result = Fraction(raw)
    _need(_ratio(result) == raw, "noncanonical_snapshot_ratio")
    return result


def _finite(row: tuple[float, ...], width: int) -> None:
    _need(type(row) is tuple and len(row) == width
          and all(type(value) is float and math.isfinite(value) for value in row),
          "invalid_snapshot_tensor_shape_or_value")


def _chart_values(tensor: ChartTensor) -> Iterator[float]:
    _need(type(tensor) is ChartTensor and type(tensor.minute) is type(tensor.coarse) is tuple
          and len(tensor.minute) == 60 and len(tensor.coarse) == 24, "invalid_snapshot_chart_shape")
    for minute in tensor.minute:
        _finite(minute, 10)
        _need(minute[7] in (0, 1) and minute[9] in (0, 1)
              and (minute[9] == 1 or minute[8] == 0), "invalid_snapshot_minute_mask")
        yield from minute
    for bar in tensor.coarse:
        _finite(bar, 6)
        _need(bar[4:] in ((0, 0), (0, 1), (1, 0))
              and (bar[4] == 1 or not any(bar[:4])), "invalid_snapshot_coarse_mask")
        yield from bar
    _finite(tensor.static, 5)
    _need(tensor.static[4] in (0, 1)
          and (tensor.static[4] == 1 or not any(tensor.static[1:4])), "invalid_snapshot_prior_mask")
    yield from tensor.static


def _peer_values(tensor: PeerTensor) -> Iterator[float]:
    _need(type(tensor) is PeerTensor and type(tensor.minute) is tuple
          and len(tensor.minute) == 60, "invalid_snapshot_peer_shape")
    for minute in tensor.minute:
        _finite(minute, 3)
        _need(0 <= minute[2] <= 1, "invalid_snapshot_peer_fraction")
        yield from minute
    _finite((tensor.coverage,), 1)
    _need(0 < tensor.coverage <= 1, "invalid_snapshot_peer_coverage")
    yield tensor.coverage


def _chart(raw: bytes | None) -> ChartTensor | None:
    if raw is None:
        return None
    _need(type(raw) is bytes and len(raw) == _CHART.size, "invalid_snapshot_chart_bytes")
    values = _CHART.unpack(raw)
    tensor = ChartTensor(tuple(values[i:i + 10] for i in range(0, 600, 10)),
                         tuple(values[i:i + 6] for i in range(600, 744, 6)), values[744:])
    tuple(_chart_values(tensor))
    return tensor


def _peer(raw: bytes | None) -> PeerTensor | None:
    if raw is None:
        return None
    _need(type(raw) is bytes and len(raw) == _PEER.size, "invalid_snapshot_peer_bytes")
    values = _PEER.unpack(raw)
    tensor = PeerTensor(tuple(values[i:i + 3] for i in range(0, 180, 3)), values[180])
    tuple(_peer_values(tensor))
    return tensor


def _target_values(target: TargetCoordinate) -> tuple:
    return (target.session, target.scenario, target.rank, target.symbol,
            target.stored_row_count, target.state, target.metadata)


def _metadata(metadata: SnapshotMetadata) -> tuple[str, dict[str, dict[str, tuple[str, ...]]]]:
    _need(type(metadata) is SnapshotMetadata and type(metadata.sessions) is tuple
          and 0 < len(metadata.sessions) <= 257 and type(metadata.targets) is tuple
          and 0 < len(metadata.targets) <= 2 * 20 * len(metadata.sessions), "invalid_snapshot_metadata")
    sessions = metadata.sessions
    _need(all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in sessions)
          and sessions == tuple(sorted(set(sessions))), "invalid_snapshot_sessions")
    roster = {day: {} for day in sessions}
    ranks = {(day, scenario): set() for day in sessions for scenario in _SCENARIOS}
    fingerprint = hashlib.sha256()
    fingerprint.update(_canonical((_SCHEMA_ID, sessions, _bytes(metadata.provenance).hex())))
    _need(all(type(target) is TargetCoordinate for target in metadata.targets), "invalid_snapshot_target")
    for target in sorted(metadata.targets, key=lambda item: (item.session, item.scenario, item.rank)):
        day, scenario, rank, symbol, count, state, raw = _target_values(target)
        _need(day in roster and scenario in _SCENARIOS
              and type(rank) is int and 1 <= rank <= 20
              and type(symbol) is str and 0 < len(symbol) <= 32
              and (count is None and state == "no_stored_rows_unknown_reason"
                   or type(count) is int and count > 0 and state == "stored_rows_completeness_unverified"),
              "invalid_snapshot_target")
        by_symbol = roster[day]
        memberships = by_symbol.get(symbol, ())
        _need(scenario not in memberships and rank not in ranks[day, scenario], "duplicate_snapshot_target")
        by_symbol[symbol] = (*memberships, scenario)
        ranks[day, scenario].add(rank)
        fingerprint.update(_canonical((day, scenario, rank, symbol, count, state, _bytes(raw).hex())))
    _need(all(found == set(range(1, len(found) + 1)) and found for found in ranks.values()),
          "incomplete_snapshot_target_roster")
    return fingerprint.hexdigest(), roster


def _checked_file(parent: int, name: str, descriptor: int, mode: int) -> tuple:
    info = os.fstat(descriptor)
    identity = wire.file_identity(info)
    _need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == mode
          and info.st_uid == os.geteuid() and info.st_nlink == 1 and info.st_size <= _MAX_FILE
          and wire.file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity,
          "snapshot_file_identity_differs")
    return identity


def _file_hash(parent: int, name: str, descriptor: int, checkpoint: _Checkpoint) -> str:
    before = _checked_file(parent, name, descriptor, 0o400)
    hashed = hashlib.sha256()
    size = 0
    with os.fdopen(os.dup(descriptor), "rb") as stream:
        stream.seek(0)
        while block := stream.read(1024 * 1024):
            checkpoint("ef_snapshot_file_hash")
            size += len(block)
            _need(size <= _MAX_FILE, "snapshot_file_too_large")
            hashed.update(block)
    _need(before == _checked_file(parent, name, descriptor, 0o400)
          and size == before[4], "snapshot_changed_while_hashing")
    return hashed.hexdigest()


def _file(stack: ExitStack, path: Path, *, create: bool) -> tuple[int, str, int]:
    _need(isinstance(path, Path) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", path.name) is not None
          and not path.name.endswith(_SIDECARS),
          "invalid_snapshot_path")
    parent = _private_parent(path)
    stack.callback(os.close, parent)
    for suffix in _SIDECARS:
        _need(not os.path.lexists(f"/proc/self/fd/{parent}/{path.name}{suffix}"), "snapshot_sidecar_exists")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY | os.O_NONBLOCK
    descriptor = os.open(path.name, flags | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    stack.callback(os.close, descriptor)
    _checked_file(parent, path.name, descriptor, 0o600 if create else 0o400)
    return parent, path.name, descriptor


def _seal_file(parent: int, name: str, descriptor: int, checkpoint: _Checkpoint) -> str:
    _checked_file(parent, name, descriptor, 0o600)
    os.fchmod(descriptor, 0o400)
    os.fsync(descriptor)
    os.fsync(parent)
    return _file_hash(parent, name, descriptor, checkpoint)


def _writer(stack: ExitStack, file: tuple[int, str, int], schema: tuple[str, ...]) -> sqlite3.Connection:
    parent, name, descriptor = file
    before = _checked_file(parent, name, descriptor, 0o600)
    connection = _database(parent, name, readonly=False)
    stack.callback(connection.close)
    _need(before == _checked_file(parent, name, descriptor, 0o600), "snapshot_open_identity_differs")
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    connection.execute(f"PRAGMA max_page_count={_MAX_FILE // page_size}")
    connection.execute("BEGIN")
    for ddl in schema:
        connection.execute(ddl)
    return connection


def _write_label(connection: sqlite3.Connection, day: DayMetadata, label: LabelRow) -> None:
    _need(type(label.heads) is tuple and len(label.heads) == 6, "invalid_snapshot_label_heads")
    columns = [label.session, label.offset, label.symbol]
    for head, horizon in zip(label.heads, _HEAD_HORIZONS, strict=True):
        if head is None:
            columns.extend((None, None))
        else:
            _need(type(head) is LabelHead, "invalid_snapshot_label_head")
            mature_at = _clock(head.mature_at)
            exit_at = day.opening + (label.offset + 3 + horizon) * MINUTE
            _need(exit_at < day.closing - 15 * MINUTE and head.mature_at >= exit_at + 3 * MINUTE,
                  "invalid_snapshot_label_maturity")
            columns.extend((_ratio(head.value), mature_at))
    columns.append(_bytes(label.evidence))
    connection.execute("INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", columns)


def _write_day(features: sqlite3.Connection, labels: sqlite3.Connection, day: SnapshotDay,
               memberships: dict[str, tuple[str, ...]]) -> None:
    opening, closing = _clock(day.opening), _clock(day.closing)
    _need(day.opening < day.closing and day.opening.date().isoformat() == day.day
          and day.opening.date() == day.closing.date(), "invalid_snapshot_day_clock")
    symbols = tuple(sorted(memberships))
    _need(type(day.provenance) is tuple
          and tuple(sorted(symbol for symbol, _ in day.provenance)) == symbols,
          "incomplete_snapshot_day_provenance")
    features.execute("INSERT INTO days VALUES (?,?,?)", (day.day, opening, closing))
    features.executemany("INSERT INTO provenance VALUES (?,?,?)",
                         ((day.day, symbol, _bytes(raw)) for symbol, raw in day.provenance))
    rows = iter(day.rows)
    for offset, symbol in product(QUOTE_OFFSETS, symbols):
        key = (day.day, offset, symbol)
        pair = next(rows, None)
        _need(type(pair) is tuple and len(pair) == 2, "incomplete_snapshot_rows")
        row, label = pair
        _need(type(row) is SnapshotRow and type(row.offset) is int
              and (row.session, row.offset, row.symbol) == key, "snapshot_row_key_differs")
        bar_end = day.opening + offset * MINUTE
        feature_clock = offset in FEATURE_OFFSETS and bar_end <= day.closing
        expected_peers = memberships[symbol] if feature_clock else ()
        _need(type(row.peers) is tuple and all(type(peer) is PeerRow for peer in row.peers)
              and tuple(peer.scenario for peer in row.peers) == expected_peers
              and (feature_clock or row.chart is None), "snapshot_feature_geometry_differs")
        eligible_quote = bar_end + 3 * MINUTE < day.closing - 15 * MINUTE
        _need((row.execution_state in ("available", "unavailable") if eligible_quote
               else row.execution_state == "outside_clock")
              and (row.execution_state == "available") == (row.execution_open is not None)
              and (row.execution_open is None or row.execution_open > 0), "invalid_snapshot_execution_state")
        quote = None if row.execution_open is None else _ratio(row.execution_open)
        chart = None if row.chart is None else _CHART.pack(*_chart_values(row.chart))
        features.execute("INSERT INTO features VALUES (?,?,?,?,?,?,?)",
                         (*key, chart, quote, row.execution_state, _bytes(row.evidence)))
        for peer in row.peers:
            tensor = None if peer.tensor is None else _PEER.pack(*_peer_values(peer.tensor))
            features.execute("INSERT INTO peers VALUES (?,?,?,?,?,?)",
                             (*key, peer.scenario, tensor, _bytes(peer.evidence)))
        if offset in ENTRY_OFFSETS:
            _need(type(label) is LabelRow and type(label.offset) is int
                  and (label.session, label.offset, label.symbol) == key, "snapshot_label_key_differs")
            _write_label(labels, day, label)
        else:
            _need(label is None, "snapshot_label_outside_entry_grid")
    _need(next(rows, _END) is _END, "extra_snapshot_rows")


def write_snapshot(features_path: Path, labels_path: Path, metadata: SnapshotMetadata,
                   days: Iterable[SnapshotDay], checkpoint: _Checkpoint) -> SnapshotSeal:
    """Write a complete ordered geometry to fresh, separate private files."""
    fingerprint, roster = _metadata(metadata)
    _need(features_path != labels_path, "snapshot_paths_must_be_distinct")
    with ExitStack() as files:
        feature_file = _file(files, features_path, create=True)
        label_file = _file(files, labels_path, create=True)
        with ExitStack() as connections:
            features = _writer(connections, feature_file, _FEATURE_SCHEMA)
            labels = _writer(connections, label_file, _LABEL_SCHEMA)
            features.execute("INSERT INTO manifest VALUES (?,?)", (fingerprint, metadata.provenance))
            features.executemany("INSERT INTO targets VALUES (?,?,?,?,?,?,?)", map(_target_values, metadata.targets))
            source = iter(days)
            for session in metadata.sessions:
                checkpoint("ef_snapshot_write_day")
                day = next(source, None)
                _need(type(day) is SnapshotDay and day.day == session, "snapshot_day_roster_differs")
                _write_day(features, labels, day, roster[session])
            _need(next(source, _END) is _END, "extra_snapshot_days")
            features.commit()
            features.close()
            features_sha256 = _seal_file(*feature_file, checkpoint)
            labels.execute("INSERT INTO manifest VALUES (?,?)", (fingerprint, features_sha256))
            labels.commit()
        labels_sha256 = _seal_file(*label_file, checkpoint)
    return SnapshotSeal(fingerprint, features_sha256, labels_sha256)


@contextmanager
def _open(path: Path, expected_sha: str, schema: tuple[str, ...],
          checkpoint: _Checkpoint) -> Iterator[sqlite3.Connection]:
    _need(wire.valid_sha(expected_sha), "invalid_snapshot_file_pin")
    with ExitStack() as stack:
        parent, name, descriptor = _file(stack, path, create=False)
        identity = _checked_file(parent, name, descriptor, 0o400)
        _need(_file_hash(parent, name, descriptor, checkpoint) == expected_sha, "snapshot_file_pin_differs")
        connection = _database(parent, name, readonly=True)
        stack.callback(connection.close)
        _need(identity == _checked_file(parent, name, descriptor, 0o400), "snapshot_open_identity_differs")
        stored = connection.execute("SELECT sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' "
                                    "LIMIT ?", (len(schema) + 1,)).fetchall()
        _need(sorted(stored) == sorted((ddl,) for ddl in schema), "snapshot_schema_differs")
        yield connection
        _need(identity == _checked_file(parent, name, descriptor, 0o400), "snapshot_changed_during_read")


def _selected(sessions: Iterable[str] | None, allowed: tuple[str, ...]) -> tuple[str, ...]:
    selected = allowed if sessions is None else tuple(sessions)
    _need(selected == tuple(sorted(set(selected))) and set(selected).issubset(allowed),
          "invalid_snapshot_session_selection")
    return selected


class FeatureSnapshot:
    def __init__(self, connection: sqlite3.Connection, metadata: SnapshotMetadata) -> None:
        self._connection = connection
        self.metadata = metadata

    def days(self) -> Iterator[DayMetadata]:
        for day, opening, closing in self._connection.execute("SELECT * FROM days ORDER BY session"):
            provenance = tuple(self._connection.execute(
                "SELECT symbol,raw FROM provenance WHERE session=? ORDER BY symbol", (day,)))
            yield DayMetadata(day, _datetime(opening), _datetime(closing), provenance)

    def rows(self, sessions: Iterable[str] | None = None) -> Iterator[SnapshotRow]:
        for day in _selected(sessions, self.metadata.sessions):
            rows = self._connection.execute(
                "SELECT * FROM features WHERE session=? ORDER BY offset,symbol", (day,))
            peers = self._connection.execute(
                "SELECT offset,symbol,scenario,tensor,evidence FROM peers WHERE session=? "
                "ORDER BY offset,symbol,scenario", (day,))
            groups = iter(groupby(peers, key=lambda item: item[:2]))
            group = next(groups, None)
            for session, offset, symbol, chart, quote, state, raw in rows:
                peer_rows = ()
                if group is not None and group[0] == (offset, symbol):
                    peer_rows = tuple(PeerRow(scenario, _peer(tensor), _bytes(evidence))
                                      for _, _, scenario, tensor, evidence in group[1])
                    group = next(groups, None)
                yield SnapshotRow(session, symbol, offset, _chart(chart), peer_rows,
                                  None if quote is None else _fraction(quote), state, _bytes(raw))
            _need(group is None, "orphan_snapshot_peers")

    def row(self, session: str, symbol: str, offset: int) -> SnapshotRow:
        """Read an explicit saved coordinate without scanning other rows."""
        key = session, offset, symbol
        saved = self._connection.execute("SELECT chart,execution_open,execution_state,evidence FROM features "
                                         "WHERE (session,offset,symbol)=(?,?,?)", key).fetchone()
        _need(saved is not None, "missing_snapshot_feature_coordinate")
        chart, quote, state, evidence = saved
        peers = tuple(PeerRow(scenario, _peer(tensor), _bytes(raw)) for scenario, tensor, raw in
                      self._connection.execute("SELECT scenario,tensor,evidence FROM peers "
                                               "WHERE (session,offset,symbol)=(?,?,?) ORDER BY scenario", key))
        return SnapshotRow(session, symbol, offset, _chart(chart), peers,
                           None if quote is None else _fraction(quote), state, _bytes(evidence))

    def execution_quote(self, session: str, symbol: str, offset: int) -> tuple[Fraction | None, str]:
        """Get only the modeled OPEN and state, without decoding model inputs."""
        saved = self._connection.execute("SELECT execution_open,execution_state FROM features "
                                         "WHERE (session,offset,symbol)=(?,?,?)", (session, offset, symbol)).fetchone()
        _need(saved is not None, "missing_snapshot_execution_coordinate")
        quote, state = saved
        _need(state in ("available", "unavailable", "outside_clock") and (state == "available") == (quote is not None),
              "snapshot_execution_state_differs")
        return None if quote is None else _fraction(quote), state

    def feature_support(self, scenario: str, sessions: Iterable[str] | None = None
                        ) -> Iterator[tuple[str, str, int, bool, bool]]:
        """Original entry coordinates with chart/peer presence, without decoding tensors."""
        _need(scenario in _SCENARIOS, "invalid_snapshot_scenario")
        for day in _selected(sessions, self.metadata.sessions):
            for session, symbol, offset, chart, peer in self._connection.execute(
                "SELECT f.session,f.symbol,f.offset,f.chart IS NOT NULL,p.tensor IS NOT NULL "
                "FROM features f JOIN targets t ON (f.session,f.symbol)=(t.session,t.symbol) "
                "LEFT JOIN peers p ON (f.session,f.offset,f.symbol,t.scenario)="
                "(p.session,p.offset,p.symbol,p.scenario) "
                "WHERE f.session=? AND t.scenario=? AND f.offset BETWEEN 60 AND 300 AND f.offset%15=0 "
                "ORDER BY f.offset,f.symbol", (day, scenario),
            ):
                yield session, symbol, offset, bool(chart), bool(peer)


class LabelSnapshot:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._sessions = tuple(day for (day,) in connection.execute(
            "SELECT DISTINCT session FROM labels ORDER BY session LIMIT 258"))
        _need(0 < len(self._sessions) <= 257, "invalid_snapshot_label_session_count")

    def training_rows(self, sessions: Iterable[str], before: datetime) -> Iterator[LabelRow]:
        """Read explicit past sessions and individually purge heads at or after the cutoff."""
        _clock(before)
        _need(sessions is not None, "training_label_sessions_required")
        days = _selected(sessions, self._sessions)
        _need(all(date.fromisoformat(day) < before.date() for day in days), "invalid_training_label_sessions")
        for day in days:
            rows = self._connection.execute(
                f"SELECT {_LABEL_FIELDS} FROM labels WHERE session=? ORDER BY offset,symbol", (day,))
            for session, offset, symbol, *columns in rows:
                yield LabelRow(session, symbol, offset, self._heads(columns, before), b"")

    def audit_rows(self) -> Iterator[LabelRow]:
        """Exact retained labels and evidence for independent reconstruction."""
        for session, offset, symbol, *columns, evidence in self._connection.execute(
            f"SELECT {_LABEL_FIELDS},evidence FROM labels ORDER BY session,offset,symbol",
        ):
            yield LabelRow(session, symbol, offset, self._heads(columns, None), _bytes(evidence))

    @staticmethod
    def _heads(columns: list, before: datetime | None) -> tuple[LabelHead | None, ...]:
        heads = []
        for raw, clock in zip(columns[::2], columns[1::2], strict=True):
            _need((raw is None) == (clock is None), "snapshot_label_maturity_differs")
            mature = None if clock is None else _datetime(clock)
            heads.append(None if mature is None or (before is not None and mature >= before)
                         else LabelHead(_fraction(raw), mature))
        return tuple(heads)


@contextmanager
def open_feature_snapshot(path: Path, seal: SnapshotSeal, checkpoint: _Checkpoint) -> Iterator[FeatureSnapshot]:
    with _open(path, seal.features_sha256, _FEATURE_SCHEMA, checkpoint) as connection:
        manifest = connection.execute("SELECT * FROM manifest LIMIT 2").fetchall()
        _need(len(manifest) == 1 and manifest[0][0] == seal.fingerprint, "snapshot_fingerprint_differs")
        sessions = tuple(day for (day,) in connection.execute("SELECT session FROM days ORDER BY session LIMIT 258"))
        targets = tuple(TargetCoordinate(scenario, symbol, day, rank, count, state, raw)
                        for day, scenario, rank, symbol, count, state, raw in connection.execute(
                            f"SELECT {_TARGET_FIELDS} FROM targets ORDER BY session,scenario,rank LIMIT ?",
                            (len(sessions) * 40 + 1,)))
        metadata = SnapshotMetadata(sessions, targets, manifest[0][1])
        _need(_metadata(metadata)[0] == seal.fingerprint, "snapshot_population_fingerprint_differs")
        yield FeatureSnapshot(connection, metadata)


@contextmanager
def open_label_snapshot(path: Path, seal: SnapshotSeal, checkpoint: _Checkpoint) -> Iterator[LabelSnapshot]:
    with _open(path, seal.labels_sha256, _LABEL_SCHEMA, checkpoint) as connection:
        manifest = connection.execute("SELECT * FROM manifest LIMIT 2").fetchall()
        _need(manifest == [(seal.fingerprint, seal.features_sha256)], "snapshot_label_binding_differs")
        yield LabelSnapshot(connection)
