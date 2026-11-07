"""Sealed EH price-support rows and a receipt derived from verified saved rows.

The caller owns source authentication and the execution lifecycle. The artifact
contains support counts and roster identities, with no raw prices, target
metadata payloads, event decisions or outcomes.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as sources
from research import intraday_scenario_support as producer
from research import intraday_scenario_support_reference as reference
from research.intraday_trader_inputs import AuditedChunkReader, _groups


FILENAME = "eh-support.jsonl"
SCHEMA = "EH-SUPPORT-1-price-support-v1"
MAX_BYTES = 16 * 1024 * 1024
MAX_LINE = 4096
MAX_RECORDS = 6000
_SCOPES = ("first15", "first20", "first60", "full")
_POPULATIONS = ("union", "S1", "S2", "overlap")

Counts = tuple[tuple[str, int], ...]
Checkpoint = Callable[[str], object]


@dataclass(frozen=True)
class SupportSeal:
    sha256: str
    size: int
    records: int


@dataclass(frozen=True)
class SupportCounts:
    coordinates: int
    price_chunk_absent_records: int
    complete_coverage_counts: Counts
    prior_status_counts: Counts
    cross_session_basis_counts: Counts


@dataclass(frozen=True)
class SupportPopulation:
    population: str
    counts: SupportCounts
    coverage_counts: tuple[tuple[str, Counts], ...]
    prior_full_counts: Counts
    by_session: tuple[tuple[str, SupportCounts], ...]
    by_symbol: tuple[tuple[str, SupportCounts], ...]


@dataclass(frozen=True)
class VerifiedSupport:
    verification: reference.SupportVerificationReceipt
    populations: tuple[SupportPopulation, ...]


def _header(universe: sources.UniverseCalendar) -> dict:
    return {"kind": "header", "schema": SCHEMA, "universe_sha256": universe.raw_sha256,
            "calendar_sha256": wire.digest(wire.encode(universe.sessions)),
            "calendar_sessions": len(universe.sessions), "original_targets": len(universe.targets)}


def _footer(records: int) -> dict:
    return {"kind": "footer", "records": records}


def _price(cell: producer.PriceCoverage) -> dict:
    return {"scope": cell.scope, "expected": cell.expected, "cutoff_at": cell.cutoff_at.isoformat(),
            "counts": cell.counts}


def _record(row: producer.SupportRecord) -> dict:
    return {
        "kind": "record", "session": row.session, "symbol": row.symbol,
        "opening": row.opening.isoformat(), "closing": row.closing.isoformat(),
        "targets": [
            {"scenario": target.scenario, "symbol": target.symbol, "session": target.session,
             "rank": target.rank, "stored_row_count": target.stored_row_count, "state": target.state,
             "metadata_sha256": wire.digest(target.metadata)}
            for target in row.targets
        ],
        "price_chunk_present": row.price_chunk_present, "coverage": [_price(cell) for cell in row.coverage],
        "prior_session": row.prior_session, "prior_status": row.prior_status,
        "prior_full": _price(row.prior_full) if row.prior_full is not None else None,
        "cross_session_basis": row.cross_session_basis,
    }


def _decode_price(value: dict) -> producer.PriceCoverage:
    return producer.PriceCoverage(**{**value, "cutoff_at": sources._clock(value["cutoff_at"]),
                                     "counts": tuple(tuple(pair) for pair in value["counts"])})


def _decode_record(value: dict, metadata: dict[tuple[str, str, str], bytes]) -> producer.SupportRecord:
    # Use the roster only for opaque metadata. Every other field comes from the
    # saved row and is checked by the independent raw verifier.
    fields = dict(value)
    fields.pop("kind")
    targets = []
    for target in fields["targets"]:
        decoded = dict(target)
        decoded.pop("metadata_sha256")
        key = (target["scenario"], target["session"], target["symbol"])
        targets.append(sources.TargetCoordinate(**decoded, metadata=metadata[key]))
    fields["targets"] = tuple(targets)
    fields["opening"] = sources._clock(fields["opening"])
    fields["closing"] = sources._clock(fields["closing"])
    fields["coverage"] = tuple(_decode_price(cell) for cell in fields["coverage"])
    if fields["prior_full"] is not None:
        fields["prior_full"] = _decode_price(fields["prior_full"])
    return producer.SupportRecord(**fields)


def write_support(path: Path, universe: sources.UniverseCalendar, raw_reader: AuditedChunkReader,
                  checkpoint: Checkpoint) -> SupportSeal:
    """Create one exclusive deterministic artifact and seal its bytes."""
    expected = len(_groups(universe))
    sources._need(0 < expected <= MAX_RECORDS and callable(checkpoint), "invalid_support_write_contract")
    wire.safe_path(path)
    digest, size, count = hashlib.sha256(), 0, 0
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        def write(value: dict) -> None:
            nonlocal size
            line = wire.encode(value)
            size += len(line)
            sources._need(len(line) <= MAX_LINE and size <= MAX_BYTES, "support_artifact_bound")
            wire.write_all(stream, line)
            digest.update(line)

        write(_header(universe))
        for row in producer.iter_support_records(universe, raw_reader, checkpoint):
            checkpoint("eh_support_write_record")
            count += 1
            sources._need(count <= expected, "extra_produced_support_record")
            write(_record(row))
        sources._need(count == expected, "missing_produced_support_record")
        raw_reader.assert_unchanged()
        write(_footer(count))
        stream.flush()
        os.fsync(stream.fileno())
        written = os.fstat(stream.fileno())
    wire.fsync_directory(path.parent)
    sources._need(wire.file_identity(wire.regular(path)) == wire.file_identity(written)
                  and written.st_size == size, "support_artifact_changed_during_write")
    return SupportSeal(digest.hexdigest(), size, count)


def _stored_rows(path: Path, seal: SupportSeal, universe: sources.UniverseCalendar,
                 checkpoint: Checkpoint) -> Iterator[producer.SupportRecord]:
    metadata = {(target.scenario, target.session, target.symbol): target.metadata for target in universe.targets}
    before = wire.regular(path)
    identity = wire.file_identity(before)
    sources._need(before.st_size == seal.size, "support_artifact_size_differs")
    digest, size, count = hashlib.sha256(), 0, 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        sources._need(wire.file_identity(os.fstat(stream.fileno())) == identity, "support_artifact_changed_before_read")

        def read_line() -> bytes:
            nonlocal size
            checkpoint("eh_support_read_record")
            line = stream.readline(MAX_LINE + 1)
            size += len(line)
            sources._need(line.endswith(b"\n") and len(line) <= MAX_LINE and size <= seal.size,
                          "support_artifact_framing_or_bound")
            digest.update(line)
            return line

        sources._need(read_line() == wire.encode(_header(universe)), "support_artifact_header_differs")
        while True:
            line = read_line()
            try:
                value = json.loads(line)
                if type(value) is dict and value.get("kind") == "footer":
                    break
                row = _decode_record(value, metadata)
                canonical = wire.encode(_record(row))
            except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as error:
                raise sources.SourceRefusal("invalid_saved_support_record") from error
            sources._need(line == canonical, "noncanonical_saved_support_record")
            count += 1
            sources._need(count <= seal.records, "extra_saved_support_record")
            yield row
        sources._need(line == wire.encode(_footer(count)) and count == seal.records,
                      "support_artifact_footer_differs")
        sources._need(stream.read(1) == b"", "support_artifact_trailing_bytes")
        sources._need(size == seal.size and digest.hexdigest() == seal.sha256, "support_artifact_seal_differs")
        sources._need(wire.file_identity(os.fstat(stream.fileno())) == identity,
                      "support_artifact_changed_during_read")
    sources._need(wire.file_identity(wire.regular(path)) == identity, "support_artifact_replaced")


def _counts(counter: Counter) -> Counts:
    return tuple(sorted(counter.items()))


class _SupportCounts:
    def __init__(self) -> None:
        self.coordinates = self.absent = 0
        self.complete = Counter(dict.fromkeys(_SCOPES, 0))
        self.prior = Counter()
        self.basis = Counter()

    def add(self, row: producer.SupportRecord) -> None:
        self.coordinates += 1
        self.absent += int(not row.price_chunk_present)
        self.complete.update(cell.scope for cell in row.coverage if cell.counts == (("observed", cell.expected),))
        self.prior[row.prior_status] += 1
        self.basis[row.cross_session_basis] += 1

    def finish(self) -> SupportCounts:
        return SupportCounts(self.coordinates, self.absent, tuple((scope, self.complete[scope]) for scope in _SCOPES),
                             _counts(self.prior), _counts(self.basis))


class _Population:
    def __init__(self) -> None:
        self.counts = _SupportCounts()
        self.coverage = {scope: Counter() for scope in _SCOPES}
        self.prior_full = Counter()
        self.by_session = defaultdict(_SupportCounts)
        self.by_symbol = defaultdict(_SupportCounts)

    def add(self, row: producer.SupportRecord) -> None:
        self.counts.add(row)
        self.by_session[row.session].add(row)
        self.by_symbol[row.symbol].add(row)
        for cell in row.coverage:
            self.coverage[cell.scope].update(dict(cell.counts))
        if row.prior_full is not None:
            self.prior_full.update(dict(row.prior_full.counts))

    def finish(self, name: str, sessions: tuple[str, ...]) -> SupportPopulation:
        return SupportPopulation(
            name, self.counts.finish(), tuple((scope, _counts(self.coverage[scope])) for scope in _SCOPES),
            _counts(self.prior_full), tuple((day, self.by_session[day].finish()) for day in sessions),
            tuple((symbol, values.finish()) for symbol, values in sorted(self.by_symbol.items())),
        )


def verify_support(path: Path, seal: SupportSeal, universe: sources.UniverseCalendar,
                   raw_reader: AuditedChunkReader, checkpoint: Checkpoint) -> VerifiedSupport:
    """Reconstruct every saved row from raw CC and summarize after authenticated EOF."""
    expected = len(_groups(universe))
    sources._need(type(seal) is SupportSeal and wire.valid_sha(seal.sha256)
                  and type(seal.size) is int and 0 < seal.size <= MAX_BYTES
                  and type(seal.records) is int and 0 < seal.records == expected <= MAX_RECORDS
                  and callable(checkpoint), "invalid_support_artifact_seal")
    populations = {name: _Population() for name in _POPULATIONS}

    def checked_rows(rows: Iterator[producer.SupportRecord]) -> Iterator[producer.SupportRecord]:
        for row in rows:
            yield row
            populations["union"].add(row)
            for target in row.targets:
                populations[target.scenario].add(row)
            if len(row.targets) == 2:
                populations["overlap"].add(row)

    rows = _stored_rows(path, seal, universe, checkpoint)
    try:
        verification = reference.verify_support_records(checked_rows(rows), universe, raw_reader, checkpoint)
    finally:
        rows.close()
    return VerifiedSupport(verification, tuple(population.finish(name, universe.sessions)
                                               for name, population in populations.items()))
