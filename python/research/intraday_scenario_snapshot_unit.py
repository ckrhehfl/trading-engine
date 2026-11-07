"""Build one verified EH forecast snapshot from the original CB/CC archives."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING, BinaryIO

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_scenario_open_unit as opening
from research import intraday_scenario_support_unit as price_archive

if TYPE_CHECKING:
    from research import intraday_scenario_snapshot as snapshot


WINDOW = opening.WINDOW
FULL = opening.FULL
COUNTS = opening.COUNTS
OUTPUT = bridge.EH_SNAPSHOT_OUTPUT_FILENAME
MAX_OUTPUT = bridge.EH_SNAPSHOT_OUTPUT_MAX_BYTES
SCHEMA = bridge.EH_SNAPSHOT_OUTPUT_SCHEMA
FEATURE_FILENAME = "eh-opening-features.jsonl"
LABEL_FILENAME = "eh-opening-labels.jsonl"
MAX_HEADER = 4096
Checkpoint = price_archive.CHECKPOINT


@dataclass(frozen=True)
class BundleSeal:
    sha256: str
    size: int


def contract(specification: bytes, original: bytes, *,
             retry: bool = False) -> tuple[dict, tuple[bridge.InputPin, ...], str]:
    unit_id = bridge.EH_SNAPSHOT_RETRY_UNIT_ID if retry else bridge.EH_SNAPSHOT_UNIT_ID
    return price_archive._price_contract(specification, original, unit_id)


def make_invented(directory: Path, sessions: int, checkpoint: Checkpoint) -> bridge.InputEnvelope:
    return opening.make_invented(directory, sessions, checkpoint)


def _member_seal(raw: dict, schema: str, minimum: int, maximum: int) -> snapshot.SnapshotSeal:
    from research import intraday_scenario_snapshot as snapshot

    wire.require(type(raw) is dict and set(raw) == {"schema", "sha256", "size", "records"}
                 and raw["schema"] == schema and wire.valid_sha(raw["sha256"])
                 and type(raw["size"]) is int and 0 < raw["size"] <= snapshot.MAX_BYTES
                 and type(raw["records"]) is int and minimum <= raw["records"] <= maximum,
                 "EH bundle member seal differs")
    return snapshot.SnapshotSeal(**raw)


def _members(header: dict) -> tuple[snapshot.SnapshotSeal, snapshot.SnapshotSeal]:
    from research import intraday_scenario_snapshot as snapshot

    wire.require(type(header) is dict and set(header) == {"schema", "features", "labels"}
                 and header["schema"] == SCHEMA, "EH bundle header differs")
    return (_member_seal(header["features"], snapshot.FEATURE_SCHEMA, 1, snapshot.MAX_DAYS),
            _member_seal(header["labels"], snapshot.LABEL_SCHEMA, 0, snapshot.MAX_QUESTIONS))


@contextmanager
def _sealed_input(path: Path) -> Iterator[tuple[BinaryIO, int]]:
    before = wire.regular(path)
    identity = wire.file_identity(before)
    wire.require(stat.S_IMODE(before.st_mode) == 0o400, "EH bundle source mode differs")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == identity, "EH bundle source changed before open")
        yield stream, before.st_size
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == identity
                     == wire.file_identity(wire.regular(path)), "EH bundle source changed while reading")


@contextmanager
def _sealed_output(path: Path) -> Iterator[BinaryIO]:
    wire.safe_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        yield stream
        stream.flush()
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())
        frozen = wire.file_identity(os.fstat(stream.fileno()))
    wire.fsync_directory(path.parent)
    wire.require(wire.file_identity(wire.regular(path)) == frozen, "EH bundle output changed while sealing")


def _stream_member(source: BinaryIO, seal: snapshot.SnapshotSeal, first: bytes, checkpoint: Checkpoint,
                   *, target: BinaryIO | None = None, bundle_digest=None) -> None:
    from research import intraday_scenario_snapshot as snapshot

    footer = wire.encode({"records": seal.records})
    wire.require(seal.size >= len(first) + len(footer), "EH bundle member length differs")
    digest, remaining, lines, span, tail = sha256(), seal.size, 0, 0, b""
    while remaining:
        checkpoint("eh_snapshot_bundle_member")
        raw = source.read(min(wire.BLOCK, remaining))
        wire.require(bool(raw), "EH bundle member truncated")
        if remaining == seal.size:
            wire.require(raw.startswith(first), "EH bundle member header differs")
        remaining -= len(raw)
        count = raw.count(b"\n")
        if count:
            wire.require(span + raw.find(b"\n") + 1 <= snapshot.MAX_LINE, "EH bundle member line exceeded")
            span = len(raw) - raw.rfind(b"\n") - 1
            lines += count
        else:
            span += len(raw)
            wire.require(span < snapshot.MAX_LINE, "EH bundle member line exceeded")
        tail = raw[-len(footer):] if len(raw) >= len(footer) else (tail + raw)[-len(footer):]
        digest.update(raw)
        if bundle_digest is not None:
            bundle_digest.update(raw)
        if target is not None:
            wire.write_all(target, raw)
    wire.require(tail == footer and lines == seal.records + 2 and digest.hexdigest() == seal.sha256,
                 "EH bundle member hash, record count or footer differs")


def _headers(features: snapshot.SnapshotSeal) -> tuple[bytes, bytes]:
    from research import intraday_scenario_snapshot as snapshot

    return wire.encode({"schema": snapshot.FEATURE_SCHEMA}), wire.encode(snapshot._label_header(features))


def _pack_bundle(path: Path, feature_path: Path, feature_seal: snapshot.SnapshotSeal,
                 label_path: Path, label_seal: snapshot.SnapshotSeal, checkpoint: Checkpoint) -> BundleSeal:
    value = {"schema": SCHEMA, "features": asdict(feature_seal), "labels": asdict(label_seal)}
    features, labels = _members(value)
    header = wire.encode(value)
    size = len(header) + features.size + labels.size
    wire.require(len(header) <= MAX_HEADER and size <= MAX_OUTPUT, "EH bundle size exceeded")
    digest = sha256(header)
    with _sealed_output(path) as target:
        wire.write_all(target, header)
        for item, seal, first in zip((feature_path, label_path), (features, labels), _headers(features), strict=True):
            with _sealed_input(item) as (source, item_size):
                wire.require(item_size == seal.size, "EH bundle member size differs")
                _stream_member(source, seal, first, checkpoint, target=target, bundle_digest=digest)
                wire.require(not source.read(1), "EH bundle member trailing data")
    return BundleSeal(digest.hexdigest(), size)


def _read_bundle(path: Path, checkpoint: Checkpoint, destinations: tuple[Path, Path] | None = None
                 ) -> tuple[snapshot.SnapshotSeal, snapshot.SnapshotSeal]:
    with _sealed_input(path) as (source, size):
        wire.require(0 < size <= MAX_OUTPUT, "EH bundle size exceeded")
        raw = source.readline(MAX_HEADER + 1)
        wire.require(raw.endswith(b"\n") and len(raw) <= MAX_HEADER, "EH bundle header exceeded")
        header = json.loads(raw)
        wire.require(wire.encode(header) == raw, "EH bundle header noncanonical")
        features, labels = _members(header)
        wire.require(len(raw) + features.size + labels.size == size, "EH bundle member length differs")
        for index, (seal, first) in enumerate(zip((features, labels), _headers(features), strict=True)):
            if destinations is None:
                _stream_member(source, seal, first, checkpoint)
            else:
                with _sealed_output(destinations[index]) as target:
                    _stream_member(source, seal, first, checkpoint, target=target)
        wire.require(not source.read(1), "EH bundle trailing data")
    return features, labels


def verify_bundle(path: Path, checkpoint: Checkpoint) -> tuple[snapshot.SnapshotSeal, snapshot.SnapshotSeal]:
    """Check both ordered members without materializing them."""
    return _read_bundle(path, checkpoint)


def open_bundle(path: Path, directory: Path, checkpoint: Checkpoint
                ) -> tuple[Path, snapshot.SnapshotSeal, Path, snapshot.SnapshotSeal]:
    """Verify and unpack to a caller-owned, empty private scratch directory."""
    wire.safe_path(directory)
    mode = directory.stat().st_mode
    wire.require(stat.S_ISDIR(mode) and stat.S_IMODE(mode) == 0o700 and not any(directory.iterdir()),
                 "EH bundle fresh private directory required")
    features, labels = directory / FEATURE_FILENAME, directory / LABEL_FILENAME
    feature_seal, label_seal = _read_bundle(path, checkpoint, (features, labels))
    return features, feature_seal, labels, label_seal


def _rosters(universe):
    from research import intraday_ed_sources as sources
    from research.intraday_trader_inputs import _groups

    by_day, by_scenario = defaultdict(list), defaultdict(list)
    for (day, _), targets in _groups(universe).items():
        by_day[day].extend(targets)
        for target in targets:
            by_scenario[day, target.scenario].append(target)

    selected_at = {}
    for (day, scenario), targets in by_scenario.items():
        wire.require(len({item.metadata for item in targets}) == 1
                     and {item.rank for item in targets} == set(range(1, 21)), "EH snapshot roster differs")
        raw = targets[0].metadata
        metadata = json.loads(raw)
        wire.require(type(metadata) is dict and sources._canonical(metadata) == raw and metadata.get("session") == day,
                     "EH snapshot roster metadata differs")
        available, selected = (sources._clock(metadata.get(name)) for name in ("available_at", "selected_at"))
        wire.require(available <= selected < sources.calendar_source.session_open(day),
                     "EH snapshot roster is noncausal")
        if scenario == "S2":
            selected_at[day] = selected
    return by_day, selected_at


def _projected_days(universe, reader, checkpoint: Checkpoint):
    from research import intraday_ed_sources as sources
    from research import intraday_scenario_snapshot as snapshot
    from research.intraday_scenario_state import OpeningRangePriceEvidence

    by_day, selected_at = _rosters(universe)
    for day in universe.sessions:
        checkpoint("eh_snapshot_before_feature_day")
        reader.assert_unchanged()
        targets = tuple(by_day[day])
        sessions, evidence = {}, {}
        for symbol in sorted({item.symbol for item in targets}):
            raw = reader.load_raw_chunk("CC", symbol, day)
            if raw is not None:
                sessions[symbol] = sources.full_reference_session_inputs(symbol, day, raw, None)
                evidence[symbol] = OpeningRangePriceEvidence(f"CC:{day}:{symbol}", "stored_cc_same_session_numeric_unit")
        projected = snapshot.project_opening_day(day, targets, sessions, evidence, members_known_at=selected_at[day])
        reader.assert_unchanged()
        checkpoint("eh_snapshot_after_feature_day")
        yield projected


def compute(directory: Path, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan,
            *, invented_only: bool, checkpoint: Checkpoint) -> bridge.OutputReceipt:
    """Seal causal features first, then produce and independently verify outcomes."""
    from tempfile import TemporaryDirectory

    from research import intraday_ed_sources as sources
    from research import intraday_scenario_snapshot as snapshot
    from research.intraday_scenario_snapshot_reference import verify_snapshot

    summary = {}

    def measure(output, universe, reader):
        def outcome_session(day, symbol):
            checkpoint("eh_snapshot_label_coordinate")
            raw = reader.load_raw_chunk("CC", symbol, day)
            return None if raw is None else sources.full_reference_session_inputs(symbol, day, raw, None)

        with TemporaryDirectory(prefix="eh-snapshot-members-", dir=directory) as temporary:
            feature_path, label_path = (Path(temporary) / item for item in (FEATURE_FILENAME, LABEL_FILENAME))
            feature_seal = snapshot.write_features(feature_path, _projected_days(universe, reader, checkpoint))
            label_seal = snapshot.write_labels(label_path, feature_path, feature_seal, outcome_session)
            summary.update(verify_snapshot(feature_path, feature_seal, label_path, label_seal, universe, reader, checkpoint))
            summary["members"] = {"features": asdict(feature_seal), "labels": asdict(label_seal)}
            summary["universe_sha256"] = universe.raw_sha256
            seal = _pack_bundle(output, feature_path, feature_seal, label_path, label_seal, checkpoint)
        return seal, summary, summary["scenarios"]

    receipt = price_archive._compute_price_archive(
        directory, envelope, plan, invented_only=invented_only, checkpoint=checkpoint, filename=OUTPUT,
        maximum=MAX_OUTPUT, schema=SCHEMA, measure=measure,
    )
    proof = bridge.canonical(receipt.independent_verification)
    proof.update(snapshot_summary=summary, input_manifest=envelope.payload()["inputs"])
    return replace(receipt, independent_verification=wire.encode(proof))
