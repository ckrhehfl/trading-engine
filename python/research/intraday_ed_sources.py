"""ED raw-source boundaries; no acquisition, feature, label or account code.

The gzip decoder is a small ED copy of CC's canonical framing boundary: legacy
CC/CX helpers embed their old identities or omit a decoded digest. Both ED
streams authenticate compressed bytes, canonical framing, full EOF and declared
counts. SQLite contains only unchanged raw JSONL bytes, never scientific
intermediates. A separately traversed raw stream must audit it before use.

Public DART parser/tree and stdlib decoding are named trusted boundaries. New
report-cell semantic admission is NOT an independent scientific verification;
the caller's independent verifier must reconstruct that judgment separately.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_disclosures as dart
from research import intraday_ed_inputs as inputs
from research import intraday_pattern_calibration as calendar_source


MAX_LINE = 2 * 1024 * 1024
MAX_STREAM = 1024 * 1024 * 1024
MAX_CHUNKS = 6000
MAX_ROWS = 3_600_000
CHECKPOINT = Callable[[str], object]
TYPE_MAP = {"사업보고서": "ORIGINAL_ANNUAL", "반기보고서": "ORIGINAL_HALF_YEAR",
            "분기보고서": "ORIGINAL_QUARTER"}


class SourceRefusal(ValueError):
    """Integrity failure; never silently converted to a no-event or actual zero."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise SourceRefusal(reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _clock(value: str) -> datetime:
    try:
        at = datetime.fromisoformat(value)
    except (ValueError, TypeError) as error:
        raise SourceRefusal("invalid_source_clock") from error
    _need(at.utcoffset() is not None, "naive_source_clock")
    return at


def received_inputs(directory: Path, metadata: list[dict], checkpoint: CHECKPOINT) -> dict[str, Path]:
    """Open only the bridge's received names; original_path is provenance text."""
    manifest = wire.validate_ed_input_manifest(metadata, input_contract=wire.ED_INPUTS_CONTRACT)
    _native_boundary(directory)
    result = {}
    for item in manifest:
        path = directory / "received" / item["name"]
        digest, size = wire.file_digest(path, item["maximum"], checkpoint)
        _need((digest, size) == (item["sha256"], item["size"]), "received_input_pin_differs")
        result[item["name"]] = path
    return result


@dataclass(frozen=True)
class TargetCoordinate:
    scenario: str
    symbol: str
    session: str
    rank: int
    stored_row_count: int | None
    state: str
    metadata: bytes


@dataclass(frozen=True)
class UniverseCalendar:
    sessions: tuple[str, ...]
    targets: tuple[TargetCoordinate, ...]
    raw_sha256: str

    def coordinates(self, scenario: str) -> tuple[TargetCoordinate, ...]:
        _need(scenario in {"S1", "S2"}, "unknown_timing_scenario")
        return tuple(item for item in self.targets if item.scenario == scenario)


def load_universe(raw: bytes, expected_sha256: str, *, expected_sessions: int = 257) -> UniverseCalendar:
    _need(type(raw) is bytes and _sha(raw) == expected_sha256, "universe_pin_differs")
    value = json.loads(raw)
    # This helper validates only original population/metadata. No old detector,
    # label, price projection or EA signal mask is called.
    variants, union = calendar_source._target_matrix(value)
    sessions = tuple(sorted(union))
    _need(len(sessions) == expected_sessions, "original_session_population_differs")
    targets = []
    for variant, scenario in (("D+1", "S1"), ("D+2", "S2")):
        for day in variants[variant]["days"]:
            metadata = _canonical({key: item for key, item in day.items() if key != "targets"})
            for target in day["targets"]:
                count, state = target["stored_row_count"], target["state"]
                _need((count is None and state == "no_stored_rows_unknown_reason") or
                      (type(count) is int and count > 0 and state == "stored_rows_completeness_unverified"),
                      "universe_missingness_contract_differs")
                targets.append(TargetCoordinate(scenario, target["symbol"], day["session"],
                                                target["rank"], count, state, metadata))
    return UniverseCalendar(sessions, tuple(targets), expected_sha256)


@dataclass(frozen=True)
class RawArchivePin:
    kind: str
    compressed_sha256: str
    decoded_sha256: str
    expected_chunks: int
    expected_rows: int
    expected_daily_rows: int = 0

    def validate(self) -> None:
        _need(self.kind in {"CC", "CX"} and wire.valid_sha(self.compressed_sha256)
              and wire.valid_sha(self.decoded_sha256), "invalid_raw_archive_identity")
        for value, limit in ((self.expected_chunks, MAX_CHUNKS), (self.expected_rows, MAX_ROWS),
                             (self.expected_daily_rows, MAX_CHUNKS)):
            _need(type(value) is int and 0 <= value <= limit, "invalid_raw_archive_geometry")
        _need(self.expected_chunks > 0 and (self.kind != "CC" or self.expected_daily_rows == 0),
              "invalid_raw_archive_geometry")


@dataclass(frozen=True)
class RawChunk:
    kind: str
    symbol: str
    session: str
    raw: bytes
    sha256: str
    row_count: int
    daily_row_count: int


def _raw_identity(chunk: dict, kind: str) -> tuple[str, str, int, int]:
    base = {"symbol", "session", "observation_date", "available_at", "retrieved_at", "source",
            "is_final", "evidence_level", "rows"}
    _need(type(chunk) is dict and set(chunk) == base | ({"daily_rows", "daily_retrieved_at"}
          if kind == "CX" else set()), "raw_chunk_fields_differ")
    symbol, session = chunk["symbol"], chunk["session"]
    _need(type(symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None,
          "invalid_raw_symbol")
    _need(type(session) is str and date.fromisoformat(session).isoformat() == session
          and chunk["observation_date"] == session and chunk["available_at"] is None
          and chunk["source"] == "collector klines" and chunk["is_final"] is None
          and chunk["evidence_level"] == "assumed", "raw_chunk_metadata_differs")
    _clock(chunk["retrieved_at"])
    rows = chunk["rows"]
    _need(type(rows) is list and len(rows) <= 600, "raw_row_bound")
    columns = ({"symbol", "interval", "open_time_ms", "open", "high", "low", "close", "fetched_at"}
               if kind == "CC" else {"open_time_ms", "volume", "quote_volume", "fetched_at"})
    opening, closing = calendar_source.session_open(session), calendar_source.session_close(session)
    start, end = int(opening.timestamp()) * 1000, int(closing.timestamp()) * 1000
    for row in rows:
        _need(type(row) is dict and set(row) == columns and type(row["open_time_ms"]) is int
              and start <= row["open_time_ms"] < end
              and (row["fetched_at"] is None or type(row["fetched_at"]) is str)
              and not any(type(value) is float for value in row.values()), "raw_row_contract_differs")
        _need(kind != "CC" or (row["symbol"] == symbol and row["interval"] == "1m"),
              "raw_row_identity_differs")
    daily = chunk.get("daily_rows", [])
    if kind == "CX":
        _clock(chunk["daily_retrieved_at"])
        _need(type(daily) is list and len(daily) <= 1, "raw_daily_row_bound")
        for row in daily:
            _need(type(row) is dict and set(row) == columns
                  and type(row["open_time_ms"]) is int
                  and not any(type(value) is float for value in row.values()), "raw_daily_row_contract_differs")
    return symbol, session, len(rows), len(daily)


def canonical_chunks(path: Path, pin: RawArchivePin, checkpoint: CHECKPOINT) -> Iterator[RawChunk]:
    """EOF must be exhausted; a yielded prefix is never a verified archive."""
    pin.validate()
    identity = wire.file_identity(wire.regular(path))
    _need(wire.file_digest(path, MAX_STREAM, checkpoint)[0] == pin.compressed_sha256,
          "compressed_archive_pin_differs")
    digest, size, count, rows, daily = hashlib.sha256(), 0, 0, 0, 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as original, gzip.GzipFile(fileobj=original, mode="rb") as stream:
        _need(wire.file_identity(os.fstat(original.fileno())) == identity, "raw_file_changed_before_decode")
        while True:
            checkpoint("raw_gzip_chunk")
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            size += len(line)
            count += 1
            _need(line.endswith(b"\n") and len(line) <= MAX_LINE and size <= MAX_STREAM
                  and count <= MAX_CHUNKS, "raw_gzip_framing_or_bound")
            chunk = json.loads(line)
            _need(type(chunk) is dict and line == _canonical(chunk) + b"\n", "noncanonical_raw_jsonl")
            symbol, session, row_count, daily_count = _raw_identity(chunk, pin.kind)
            rows += row_count
            daily += daily_count
            _need(rows <= MAX_ROWS and daily <= MAX_CHUNKS, "raw_stream_row_bound")
            digest.update(line)
            yield RawChunk(pin.kind, symbol, session, line, _sha(line), row_count, daily_count)
        _need(wire.file_identity(os.fstat(original.fileno())) == identity, "raw_file_changed_during_decode")
    _need(wire.file_identity(wire.regular(path)) == identity, "raw_file_replaced")
    _need((count, rows, daily) == (pin.expected_chunks, pin.expected_rows, pin.expected_daily_rows),
          "raw_archive_full_geometry_differs")
    _need(digest.hexdigest() == pin.decoded_sha256, "decoded_archive_eof_pin_differs")


def _native_boundary(path: Path) -> None:
    _need(os.name == "posix" and isinstance(path, Path), "native_wsl_path_required")
    wire.native_cache(path, Path(__file__).absolute().parents[2])


def _private_parent(path: Path) -> int:
    """Anchor every parent without symlinks, including the SQLite journal path."""
    _native_boundary(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, flags)
    try:
        for component in path.parent.parts[1:]:
            try:
                os.stat(".git", dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SourceRefusal("byte_cache_inside_worktree")
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        try:
            os.stat(".git", dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SourceRefusal("byte_cache_inside_worktree")
        info = os.fstat(parent)
        _need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
              "private_native_cache_parent_required")
        return parent
    except BaseException:
        os.close(parent)
        raise


def _database(parent: int, name: str, *, readonly: bool) -> sqlite3.Connection:
    # /proc/self/fd keeps the parent inode anchored during SQLite's own opens.
    suffix = "?mode=ro&immutable=1" if readonly else "?mode=rw"
    connection = sqlite3.connect(f"file:/proc/self/fd/{parent}/{name}{suffix}", uri=True)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_LINE + 1024)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16 * 1024)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    connection.enable_load_extension(False)
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA cache_size=-1024")
    connection.execute("PRAGMA mmap_size=0")
    connection.execute("PRAGMA temp_store=FILE")
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    else:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
    return connection


class ByteChunkIndex:
    """Fresh scratch SQLite, exclusively raw byte cache; never an operational DB."""

    @staticmethod
    def build(path: Path, archives: Mapping[str, tuple[Path, RawArchivePin]],
              checkpoint: CHECKPOINT) -> dict:
        _need(set(archives) == {"CC", "CX"} and path.name == "raw-byte-chunks.sqlite", "byte_cache_roster_differs")
        parent = _private_parent(path)
        connection = None
        try:
            fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            created = wire.file_identity(os.fstat(fd))
            os.close(fd)
            connection = _database(parent, path.name, readonly=False)
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _need((info.st_dev, info.st_ino) == created[:2] and stat.S_IMODE(info.st_mode) == 0o600,
                  "byte_cache_creation_identity_differs")
            connection.execute("CREATE TABLE chunks(kind TEXT NOT NULL,symbol TEXT NOT NULL,session TEXT NOT NULL,"
                               "raw BLOB NOT NULL,sha TEXT NOT NULL,PRIMARY KEY(kind,symbol,session)) WITHOUT ROWID")
            connection.execute("CREATE TABLE manifest(raw BLOB NOT NULL)")
            manifest = {kind: asdict(pin) for kind, (_, pin) in archives.items()}
            with connection:
                connection.execute("INSERT INTO manifest VALUES (?)", (_canonical(manifest),))
                for kind in ("CC", "CX"):
                    source, pin = archives[kind]
                    _need(pin.kind == kind, "archive_kind_differs")
                    for chunk in canonical_chunks(source, pin, checkpoint):
                        try:
                            connection.execute("INSERT INTO chunks VALUES (?,?,?,?,?)",
                                               (kind, chunk.symbol, chunk.session, chunk.raw, chunk.sha256))
                        except sqlite3.IntegrityError as error:
                            raise SourceRefusal("duplicate_raw_chunk_key") from error
            connection.close()
            connection = None
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                os.fsync(fd)
                os.fchmod(fd, 0o400)
            finally:
                os.close(fd)
            os.fsync(parent)
            return {"schema": "ED-byte-preserving-raw-cache-v1", "archives": manifest,
                    "sha256": wire.file_digest(path, 2 * MAX_STREAM, checkpoint)[0]}
        finally:
            if connection is not None:
                connection.close()
            os.close(parent)


class AuditedChunkReader:
    """Constructed only by audit_index; no feature/label values are cached."""

    def __init__(self, path: Path, parent: int, connection: sqlite3.Connection, identity: tuple,
                 pins: dict[tuple[str, str, str], str], receipt: dict) -> None:
        self.path, self._parent, self._connection = path, parent, connection
        self._identity, self._pins, self.receipt = identity, pins, receipt

    def load_raw_chunk(self, kind: str, symbol: str, session: str) -> dict | None:
        _need(wire.file_identity(wire.regular(self.path)) == self._identity, "audited_cache_changed")
        key = kind, symbol, session
        found = self._connection.execute("SELECT raw,sha FROM chunks WHERE kind=? AND symbol=? AND session=?", key).fetchone()
        if key not in self._pins:
            _need(found is None, "unexpected_raw_cache_key")
            return None
        _need(found is not None and type(found[0]) is bytes and _sha(found[0]) == found[1] == self._pins[key],
              "audited_raw_blob_changed")
        value = json.loads(found[0])
        _need(_canonical(value) + b"\n" == found[0], "audited_raw_framing_changed")
        return value

    def close(self) -> None:
        self._connection.close()
        os.close(self._parent)

    def __enter__(self) -> AuditedChunkReader:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def audit_index(path: Path, archives: Mapping[str, tuple[Path, RawArchivePin]], checkpoint: CHECKPOINT,
                *, expected_cache_sha256: str) -> AuditedChunkReader:
    """Independent raw traversal/key/blob comparison; does not call build/insert."""
    _need(set(archives) == {"CC", "CX"} and path.name == "raw-byte-chunks.sqlite",
          "byte_cache_audit_roster_differs")
    parent = _private_parent(path)
    connection = None
    try:
        before = wire.regular(path)
        _need(stat.S_IMODE(before.st_mode) == 0o400 and before.st_uid == os.geteuid(), "unsealed_byte_cache")
        identity = wire.file_identity(before)
        digest = wire.file_digest(path, 2 * MAX_STREAM, checkpoint)[0]
        _need(digest == expected_cache_sha256, "byte_cache_file_pin_differs")
        connection = _database(parent, path.name, readonly=True)
        schema = connection.execute("SELECT type,name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        _need(schema == [("table", "chunks"), ("table", "manifest")], "byte_cache_schema_differs")
        manifest = connection.execute("SELECT raw FROM manifest").fetchall()
        declared = {kind: asdict(pin) for kind, (_, pin) in archives.items()}
        _need(manifest == [(_canonical(declared),)], "byte_cache_manifest_differs")
        pins = {}
        for kind in ("CC", "CX"):
            source, pin = archives[kind]
            _need(pin.kind == kind, "audit_archive_kind_differs")
            for raw in canonical_chunks(source, pin, checkpoint):
                key = (kind, raw.symbol, raw.session)
                _need(key not in pins, "duplicate_audit_raw_key")
                found = connection.execute("SELECT raw,sha FROM chunks WHERE kind=? AND symbol=? AND session=?", key).fetchone()
                _need(found == (raw.raw, hashlib.sha256(raw.raw).hexdigest()), "raw_byte_cache_differs")
                pins[key] = hashlib.sha256(raw.raw).hexdigest()
        _need(connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == len(pins), "byte_cache_extra_keys")
        _need(wire.file_identity(wire.regular(path)) == identity
              and wire.file_digest(path, 2 * MAX_STREAM, checkpoint)[0] == digest, "byte_cache_changed_during_audit")
        receipt = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True, "raw_keys": len(pins),
                   "cache_sha256": digest, "archives": declared, "full_eof_verified": True}
        return AuditedChunkReader(path, parent, connection, identity, pins, receipt)
    except BaseException:
        if connection is not None:
            connection.close()
        os.close(parent)
        raise


@dataclass(frozen=True)
class FilingRow:
    symbol: str
    receipt: str
    observation_date: date
    event_type: str | None
    semantic_state: str
    metadata: bytes


@dataclass(frozen=True)
class FilingSource:
    query_codes: tuple[str, ...]
    complete_codes: tuple[str, ...]
    unknown_codes: tuple[str, ...]
    rows: tuple[FilingRow, ...]
    window_start: date
    window_end: date
    publication_sha256: str
    query_metadata: bytes = b"{}"


def report_cell_semantics(raw: bytes) -> dict[str, str]:
    """Conservative new admission, separate from existing parser.is_original.

    Plain span wrappers and whitespace are accepted. Any marker, image, title,
    alt, unfamiliar tag/attribute or non-anchor text remains semantic unknown.
    Later-correction remarks in another column do not erase an earlier original.
    """
    tree = dart._Document(raw.decode("utf-8-sig", errors="strict")).root
    bodies = tree.find("tbody", id="tbody")
    _need(len(bodies) == 1, "semantic_listing_container_differs")
    result = {}
    for row in bodies[0].find("tr"):
        cells = [item for item in row.children if isinstance(item, dart._Element) and item.tag == "td"]
        _need(len(cells) == 6, "semantic_listing_columns_differ")
        anchors = cells[2].find("a")
        _need(len(anchors) == 1, "semantic_report_anchor_differs")
        anchor = anchors[0]
        receipt = (anchor.attrs.get("id") or "").removeprefix("r_")
        _need(re.fullmatch(r"[0-9]{14}", receipt) is not None and receipt not in result,
              "semantic_receipt_identity_differs")

        def plain(node: object, *, inside: bool = False, root: bool = False) -> bool:
            if isinstance(node, str):
                return inside or not node.strip()
            if not isinstance(node, dart._Element):
                return False
            if node is anchor:
                safe = {"href", "id", "onclick", "target"}
                return (set(node.attrs) <= safe and node.attrs.get("target", "_blank") == "_blank"
                        and all(plain(child, inside=True) for child in node.children))
            if (node.tag != "span" and not root) or node.attrs:
                return False
            return all(plain(child, inside=inside) for child in node.children)

        result[receipt] = "plain_original_title_grammar" if plain(cells[2], root=True) else "semantic_unknown_annotation"
    return result


def load_filing_source(raw_files: Mapping[str, bytes], expected_pins: Mapping[str, str], *,
                       expected_query_codes: tuple[str, ...], expected_complete_codes: int = 14,
                       expected_rows: int = 61) -> FilingSource:
    """Caller supplies already received bytes and externally adopted exact pins.

    Response metadata has no query field. The source report's independently
    checked sorted one-page attempt/code projection provides that mapping.
    Neither this lineage nor current corporate IDs certify historical issuers.
    """
    names = {"DART-REPORT", "DART-PUBLICATION", "DART-ORIGIN-MANIFEST"} | {
        f"DART-{kind}{index:03d}" for kind in ("R", "M") for index in range(1, 19)}
    _need(set(raw_files) == set(expected_pins) == names, "filing_input_roster_differs")
    for name in names:
        _need(type(raw_files[name]) is bytes and wire.valid_sha(expected_pins[name])
              and _sha(raw_files[name]) == expected_pins[name], "filing_external_pin_differs")
    publication = json.loads(raw_files["DART-PUBLICATION"])
    origin = json.loads(raw_files["DART-ORIGIN-MANIFEST"])
    report = json.loads(raw_files["DART-REPORT"])
    _need(publication["status"] == "completed" and publication["verification_complete"] is True
          and publication["automatic_retries"] == 0
          and publication["artifacts"]["origin-manifest.json"] == expected_pins["DART-ORIGIN-MANIFEST"],
          "adopted_repair_publication_differs")
    _need(origin["artifacts"]["report.json"] == expected_pins["DART-REPORT"], "filing_origin_report_pin_differs")
    codes = expected_query_codes
    _need(type(codes) is tuple and len(codes) == 18 and list(codes) == sorted(set(codes))
          and all(re.fullmatch(r"[0-9]{6}", code) for code in codes)
          and report["codes"] == list(codes) and report["source"] == dart.SOURCE
          and report["start_date"] == dart.WINDOW_START.isoformat()
          and report["end_date"] == dart.WINDOW_END.isoformat()
          and report["state"] == "complete" and report["failure"] is None
          and report["attempts"] == [f"attempt-{index:03d}" for index in range(1, 19)]
          and [item["query_code"] for item in report["listings"]] == list(codes), "filing_attempt_code_projection_differs")
    rows, complete, unknown, queries = [], [], [], {}
    for index, (code, item) in enumerate(zip(codes, report["listings"], strict=True), 1):
        raw_name, meta_name = f"DART-R{index:03d}", f"DART-M{index:03d}"
        raw, metadata = raw_files[raw_name], json.loads(raw_files[meta_name])
        for name, suffix in ((raw_name, "-raw.html"), (meta_name, "-response.json")):
            _need(origin["artifacts"][f"attempt-{index:03d}" + suffix] == expected_pins[name],
                  "filing_origin_attempt_pin_differs")
        _need(metadata["sha256"] == _sha(raw) and metadata["bytes"] == len(raw)
              and metadata["received_bytes"] == len(raw) <= dart.MAX_BYTES
              and metadata["url"] == dart.SOURCE and type(metadata["status"]) is int
              and metadata["status"] == 200, "filing_response_raw_link_differs")
        retrieved = _clock(metadata["received_at"])
        _need(retrieved.utcoffset() == timedelta(0), "filing_response_not_utc")
        queries[code] = {"state": item["state"], "source": dart.SOURCE,
                         "retrieved_at": metadata["received_at"], "raw_sha256": _sha(raw),
                         "response_metadata_sha256": expected_pins[meta_name],
                         "observation_date_bounds": [report["start_date"], report["end_date"]],
                         "source_event_at": None, "first_seen_at": None, "is_final": None}
        if item["state"] == "unknown":
            _need(item["rows"] == [], "unknown_filing_has_admitted_rows")
            unknown.append(code)
            continue
        _need(item["state"] == "complete", "filing_listing_state_differs")
        page = dart.parse_listing_page(raw, dart.ListingRequest(code, dart.WINDOW_START, dart.WINDOW_END, 1))
        _need(page.pages == 1 and page.total == len(item["rows"])
              and json.loads(_canonical([asdict(row) | {"observation_date": row.observation_date.isoformat()}
                                        for row in page.rows])) == item["rows"], "filing_saved_rows_raw_projection_differs")
        semantics = report_cell_semantics(raw)
        complete.append(code)
        for row in page.rows:
            state = semantics[row.receipt]
            event_type = TYPE_MAP[row.report_type] if row.is_original and state == "plain_original_title_grammar" else None
            rowmeta = dict(item["rows"][row.row_number - 1]) | {
                "retrieved_at": metadata["received_at"], "raw_sha256": _sha(raw),
                "data_vintage": expected_pins["DART-PUBLICATION"], "evidence_level": "current_query_only",
                "semantic_state": state, "historical_issuer_binding_certified": False,
                "historical_completeness_certified": False, "remarks_are_later_current_status": True}
            rows.append(FilingRow("KRX:" + code, row.receipt, row.observation_date, event_type, state, _canonical(rowmeta)))
    summary = {"wire_attempts": 18, "complete_codes": len(complete), "unknown_codes": len(unknown),
               "complete_listing_rows": len(rows)}
    _need(len(complete) == expected_complete_codes and len(rows) == expected_rows
          and report["summary"] == summary and publication["verified_support"] == {
              "schema": report["schema"], "verified": True, "state": "complete", **summary},
          "filing_adopted_support_differs")
    return FilingSource(codes, tuple(complete), tuple(unknown), tuple(rows), dart.WINDOW_START,
                        dart.WINDOW_END, expected_pins["DART-PUBLICATION"], _canonical(queries))


@dataclass(frozen=True)
class OpeningContextRecord:
    context: inputs.OpeningContext
    reason: str
    metadata: bytes


def filing_event_opening(universe: UniverseCalendar, row: FilingRow, scenario: str) -> datetime | None:
    """None explicitly means outside-defined-calendar, never a postponed event."""
    _need(scenario in {"S1", "S2"}, "unknown_timing_scenario")
    index = bisect_right(universe.sessions, row.observation_date.isoformat()) + (0 if scenario == "S1" else 1)
    return calendar_source.session_open(universe.sessions[index]) if index < len(universe.sessions) else None


def build_opening_contexts(universe: UniverseCalendar, filing_source: FilingSource,
                           scenario: str) -> tuple[OpeningContextRecord, ...]:
    """Events stay on first market-eligible opening, never first supported day."""
    _need(scenario in {"S1", "S2"}, "unknown_timing_scenario")
    lag = 1 if scenario == "S1" else 2
    sessions, source = universe.sessions, filing_source
    queries = json.loads(source.query_metadata)
    events: dict[tuple[str, str], list[FilingRow]] = {}
    semantic_unknown: set[tuple[str, str]] = set()
    for row in source.rows:
        event_opening = filing_event_opening(universe, row, scenario)
        if event_opening is None:
            continue
        key = row.symbol, event_opening.date().isoformat()
        if row.semantic_state != "plain_original_title_grammar":
            semantic_unknown.add(key)
        if row.event_type is not None:
            events.setdefault(key, []).append(row)
    result = []
    for coordinate in universe.coordinates(scenario):
        day, symbol = coordinate.session, coordinate.symbol
        index = sessions.index(day)
        opening = calendar_source.session_open(day)
        available = opening.replace(hour=8, minute=30)
        key = symbol, day
        admitted = events.get(key, [])
        # For S2 the date interval begins after session[index-2]. Without that
        # preceding in-window boundary we cannot certify absence from this query.
        boundary_covered = index >= lag and sessions[index - lag] >= source.window_start.isoformat()
        if symbol.removeprefix("KRX:") not in source.complete_codes:
            state, reason = "UNKNOWN", "query_unknown_or_unqueried_symbol"
        elif key in semantic_unknown:
            state, reason = "UNKNOWN", "unadjudicated_report_cell_annotation"
        elif not boundary_covered or day > source.window_end.isoformat():
            state, reason = "UNKNOWN", "outside_defined_query_or_prior_date_boundary"
        elif admitted:
            state, reason = "ELIGIBLE_ORIGINAL", "unprefixed_current_list_original"
        else:
            state, reason = "KNOWN_NO_WHITELISTED_LIST_EVENT", "conditional_complete_current_query_only"
        context = inputs.OpeningContext(symbol, opening, state, available if state != "UNKNOWN" else None,
                    tuple(sorted({row.event_type for row in admitted})) if state == "ELIGIBLE_ORIGINAL" else (),
                    tuple(sorted(row.receipt for row in admitted)) if state == "ELIGIBLE_ORIGINAL" else (),
                    opening if state == "ELIGIBLE_ORIGINAL" else None)
        metadata = _canonical({"scenario": scenario, "available_at_assumption": "date-only-strict-future-session-0830",
            "source_event_at": None, "first_seen_at": None, "is_final": None,
            "publication_sha256": source.publication_sha256, "evidence_level": "current_query_only",
            "query_metadata": queries.get(symbol.removeprefix("KRX:")),
            "filing_rows": [json.loads(row.metadata) for row in admitted],
            "historical_issuer_binding_certified": False, "historical_completeness_certified": False})
        result.append(OpeningContextRecord(context, reason, metadata))
    return tuple(result)


@dataclass(frozen=True)
class SessionSourceView:
    session: inputs.SessionInputs
    metadata: bytes


def _number(value: object, *, positive: bool) -> Decimal | None:
    if type(value) is not str:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() and (result > 0 if positive else result >= 0) else None


def session_inputs(symbol: str, session: str, price: dict | None, volume: dict | None, *,
                   requested_starts: tuple[datetime, ...] | None = None) -> SessionSourceView:
    """Stored numeric observations survive unknown source units/finality.

    Source chunks do not contain a no-trade/halt certificate. Empty rows stay
    missing; real volume text '0' is observed zero, not a synthesized flat bar.
    No close*volume or quote-volume-to-share conversion is performed.
    """
    opening, closing = calendar_source.session_open(session), calendar_source.session_close(session)
    if requested_starts is not None:
        _need(type(requested_starts) is tuple
              and all(type(at) is datetime and at.utcoffset() is not None
                      and opening <= at < closing and (at - opening) % inputs.MINUTE == timedelta(0)
                      for at in requested_starts)
              and tuple(sorted(set(requested_starts))) == requested_starts,
              "invalid_requested_minute_projection")
    requested = None if requested_starts is None else frozenset(requested_starts)
    buckets: dict[str, dict[datetime, list[dict]]] = {"CC": {}, "CX": {}}
    for kind, chunk in (("CC", price), ("CX", volume)):
        if chunk is None:
            continue
        got_symbol, got_session, _, _ = _raw_identity(chunk, kind)
        _need((got_symbol, got_session) == (symbol, session), "session_source_identity_differs")
        for row in chunk["rows"]:
            # Preserve original milliseconds in metadata/cache. Minute-flooring
            # follows the preregistered source model and preserves collisions.
            at = datetime.fromtimestamp(row["open_time_ms"] // 60_000 * 60, opening.tzinfo)
            if requested is not None and at not in requested:
                continue
            buckets[kind].setdefault(at, []).append(row)
    minutes, quality = {}, []
    for at in sorted(set(buckets["CC"]) | set(buckets["CX"])):
        prows, vrows = buckets["CC"].get(at, []), buckets["CX"].get(at, [])
        pstate = "missing" if not prows else "conflict" if len(prows) > 1 else "observed"
        vstate = "missing" if not vrows else "conflict" if len(vrows) > 1 else "observed"
        ohlc = tuple(_number(prows[0][name], positive=True) for name in ("open", "high", "low", "close")) if pstate == "observed" else None
        pquality, vquality = pstate, vstate
        if ohlc is not None and (None in ohlc or not ohlc[2] <= min(ohlc[0], ohlc[3]) <= max(ohlc[0], ohlc[3]) <= ohlc[1]):
            pstate, ohlc = "conflict", None
            pquality = "raw_invalid_numeric_or_ohlc"
        shares = _number(vrows[0]["volume"], positive=False) if vstate == "observed" else None
        if vstate == "observed" and shares is None:
            vstate = "conflict"
            vquality = "raw_invalid_numeric"
        if pquality != "observed" or vquality != "observed":
            quality.append({"minute": at.isoformat(), "price": pquality, "volume": vquality})
        minutes[at] = inputs.Minute(symbol, at, at + 3 * inputs.MINUTE, ohlc, shares, pstate, vstate)
    metadata_value = {"symbol": symbol, "session": session, "source_event_at": None,
        "available_at_assumption": "minute-end-plus2-not-retrieved-at", "price_basis": None,
        "physical_unit_status": None, "venue_channel": None, "session_state": None,
        "share_quantity_interpretation": "stored volume field; physical share units unverified",
        "stored_numeric_observations_are_unit_certification": False,
        "minute_quality": quality,
        "sources": {kind: None if chunk is None else {key: value for key, value in chunk.items()
                    if key not in {"rows", "daily_rows"}} | {"raw_chunk_sha256": _sha(_canonical(chunk) + b"\n"),
                    "stored_fetched_at": [[row["open_time_ms"], row["fetched_at"]] for row in chunk["rows"]]}
                    for kind, chunk in (("CC", price), ("CX", volume))}}
    if requested_starts is not None:
        metadata_value["requested_minute_starts"] = [at.isoformat() for at in requested_starts]
    metadata = _canonical(metadata_value)
    return SessionSourceView(inputs.SessionInputs(symbol, opening, closing, minutes), metadata)
