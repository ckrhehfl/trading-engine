"""Closed EH support computation over the original CB and CC archives."""

from collections.abc import Callable
from dataclasses import asdict
import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


ORIGINAL_SPEC = "configs/research/discovery/intraday-trader-local-input-v1.json"
ORIGINAL_PIN = "027eb3461fed02730859ccb2b5de141fa22dd80b7c81973c869e2d5a3bde8932"
FULL = {"sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
        "cc_chunks": 5158, "cc_rows": 1002211}
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
COUNTS = {clock: {"original": FULL["targets_per_scenario"]} for clock in ("S1", "S2")}
OUTPUT = bridge.EH_SUPPORT_OUTPUT_FILENAME
MAX_OUTPUT = bridge.EH_SUPPORT_OUTPUT_MAX_BYTES
CHECKPOINT = Callable[[str], object]


def contract(specification: bytes, original: bytes) -> tuple[dict, tuple[bridge.InputPin, ...], str]:
    """Admit only the predeclared original price archive and calendar."""
    return _price_contract(specification, original, bridge.EH_SUPPORT_UNIT_ID)


def _price_contract(specification: bytes, original: bytes, unit_id: str) -> tuple[dict, tuple[bridge.InputPin, ...], str]:
    wire.require(wire.digest(original) == ORIGINAL_PIN, "EH original registration changed")
    spec = json.loads(specification)
    wire.require(spec["unit_id"] == unit_id
                 and spec["source_registration"] == {"path": ORIGINAL_SPEC, "sha256": ORIGINAL_PIN}
                 and spec["geometry"] == FULL and type(spec["actual_runner_ready"]) is bool,
                 "EH support registration changed")
    source = json.loads(original)["actual_source_preparation"]
    wire.require(source["window"] == [WINDOW["start"], WINDOW["end"]]
                 and {key: source["geometry"][key] for key in FULL} == FULL,
                 "EH original support geometry changed")
    original_sources = {row["name"]: row for row in source["local_received_sources"]}
    pins = []
    for name, maximum in wire.EH_SUPPORT_INPUT_BOUNDS:
        row = original_sources[name]
        wire.require(row["maximum"] == maximum and wire.valid_sha(row["sha256"]),
                     "EH original input pin changed")
        pins.append(bridge.InputPin(name, row["path"], row["sha256"], maximum))
    return spec, tuple(pins), source["decoded_archives"]["CC"]


def invented_geometry(sessions: int) -> dict:
    wire.require(type(sessions) is int and 1 <= sessions <= FULL["sessions"], "EH invented calendar bound")
    union = 20 * sessions + min(sessions, 18)
    return {"sessions": sessions, "targets_per_scenario": 20 * sessions, "union_coordinates": union,
            "cc_chunks": union, "cc_rows": FULL["cc_rows"] * union // FULL["cc_chunks"]}


def make_invented(directory: Path, sessions: int, checkpoint: CHECKPOINT) -> bridge.InputEnvelope:
    return _make_invented_price_archive(directory, sessions, checkpoint)


def _make_invented_price_archive(
    directory: Path, sessions: int, checkpoint: CHECKPOINT, *,
    adjust_chunk: Callable[[dict, int], None] | None = None,
) -> bridge.InputEnvelope:
    import gzip
    import hashlib

    from research.intraday_ed_comparison_unit import _invented_calendar, _invented_chunk, _invented_universe

    geometry = invented_geometry(sessions)
    days = _invented_calendar(sessions)
    universe, coordinates = _invented_universe(days, tuple(f"{n:06}" for n in range(1, 19)), geometry)
    sizes = {(day, symbol): count for day, symbol, count in coordinates}
    keys = tuple(sizes)
    # Exercise a complete prior session and an empty chunk without changing the
    # fixed row population. Absorb the small difference in a third chunk.
    remaining = sizes[keys[0]] + sizes[keys[1]] - 390
    sizes[keys[0]], sizes[keys[1]] = 390, 0
    sizes[keys[2]] += remaining
    for variant in universe["variants"].values():
        for row in variant["days"]:
            for target in row["targets"]:
                count = sizes[row["session"], target["symbol"]]
                target["stored_row_count"] = count or None
                if not count:
                    target["state"] = "no_stored_rows_unknown_reason"
    wire.exclusive_bytes(directory / "CB", wire.encode(universe))
    decoded = hashlib.sha256()
    descriptor = os.open(directory / "CC", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    day_index = {day: index for index, day in enumerate(days)}
    with os.fdopen(descriptor, "wb") as stream:
        with gzip.GzipFile(filename="", fileobj=stream, mode="wb", compresslevel=1, mtime=0) as zipped:
            for index, ((day, symbol), count) in enumerate(sizes.items()):
                checkpoint("eh_support_invented_chunk")
                chunk = _invented_chunk("CC", day, symbol, count, day_index[day], index % 21, False)
                if adjust_chunk is not None:
                    adjust_chunk(chunk, index)
                if index == 3:
                    chunk["rows"][16]["open_time_ms"] = chunk["rows"][15]["open_time_ms"] + 1
                raw = wire.encode(chunk)
                zipped.write(raw)
                decoded.update(raw)
        stream.flush()
        os.fsync(stream.fileno())
    files = []
    for name, maximum in wire.EH_SUPPORT_INPUT_BOUNDS:
        digest, size = wire.file_digest(directory / name, maximum, checkpoint)
        files.append(bridge.InputFile(name, str(directory / name), digest, maximum, size))
    return bridge.InputEnvelope(tuple(files), decoded.hexdigest(), wire.encode(geometry),
                                wire.EH_SUPPORT_INPUT_CONTRACT)


def compute(directory: Path, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan,
            *, invented_only: bool, checkpoint: CHECKPOINT) -> bridge.OutputReceipt:
    """Runs only inside the existing post-STARTED worker lifecycle."""
    from research import intraday_scenario_support_storage as storage

    def measure(output, universe, reader):
        seal = storage.write_support(output, universe, reader, checkpoint)
        verified = storage.verify_support(output, seal, universe, reader, checkpoint)
        totals = {row.population: asdict(row.counts) for row in verified.populations}
        return seal, asdict(verified), totals

    return _compute_price_archive(directory, envelope, plan, invented_only=invented_only, checkpoint=checkpoint,
                                  filename=OUTPUT, maximum=MAX_OUTPUT, schema=storage.SCHEMA, measure=measure)


def _compute_price_archive(directory, envelope, plan, *, invented_only, checkpoint, filename, maximum, schema, measure):
    """Authenticate one immutable price archive and seal its verified output."""
    from research import intraday_ed_sources as archive

    wire.require(envelope.input_contract == wire.EH_SUPPORT_INPUT_CONTRACT, "EH input contract changed")
    geometry = bridge.canonical(envelope.geometry)
    if invented_only:
        wire.require(geometry == invented_geometry(geometry["sessions"]), "EH invented geometry changed")
    else:
        bridge._actual_envelope(envelope, plan)
    manifest = envelope.payload()["inputs"]
    cb, cc = envelope.manifest
    for item in (cb, cc):
        wire.require(wire.file_digest(directory / "received" / item.name, item.maximum, checkpoint)
                     == (item.sha256, item.size), "EH received source changed")
    raw_universe = bridge.bounded_read(directory / "received" / "CB", cb.maximum)
    universe = archive.load_universe(raw_universe, cb.sha256, expected_sessions=geometry["sessions"])
    counts = {clock: {"original": len(universe.coordinates(clock))} for clock in ("S1", "S2")}
    wire.require(len({(row.session, row.symbol) for row in universe.targets}) == geometry["union_coordinates"]
                 and all(row["original"] == geometry["targets_per_scenario"] for row in counts.values()),
                 "EH population geometry changed")
    if not invented_only:
        wire.require((universe.sessions[0], universe.sessions[-1]) == (WINDOW["start"], WINDOW["end"]),
                     "EH actual calendar changed")
    archives = {"CC": (directory / "received" / "CC", archive.RawArchivePin(
        "CC", cc.sha256, envelope.stream_sha256, geometry["cc_chunks"], geometry["cc_rows"], 0))}
    index = directory / "eh-price-byte-chunks.sqlite"
    index_seal = archive.ByteChunkIndex.build(index, archives, checkpoint, price_only=True)
    output = directory / filename
    with archive.audit_index(index, archives, checkpoint, expected_cache_sha256=index_seal["sha256"],
                             price_only=True) as reader:
        seal, summary, totals = measure(output, universe, reader)
        raw_audit = reader.receipt
    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())
    wire.fsync_directory(directory)
    wire.require(wire.file_digest(output, maximum, checkpoint) == (seal.sha256, seal.size),
                 "EH saved support changed")
    receipt = {"verified": True, "schema": schema, "full_eof_verified": True,
               "all_original_coordinates_verified": True, "invented_only": invented_only,
               "full_geometry": geometry == FULL, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
               "raw_byte_audit": raw_audit, "summary_sha256": wire.digest(wire.encode(summary)),
               "support_totals": totals,
               "historical_availability_certified": False}
    return bridge.OutputReceipt(seal.sha256, seal.size, envelope.geometry, wire.encode(receipt), wire.encode(counts))
