"""Closed local EI census using the existing measured research lifecycle."""

import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


ORIGINAL_SPEC = "configs/research/discovery/intraday-trader-local-input-v1.json"
ORIGINAL_PIN = "027eb3461fed02730859ccb2b5de141fa22dd80b7c81973c869e2d5a3bde8932"
FULL = {"sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
        "cc_chunks": 5158, "cc_rows": 1002211, "cx_chunks": 5158, "cx_rows": 1002211,
        "cx_daily_rows": 2674}
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
COUNTS = {clock: {"original": 5140} for clock in ("S1", "S2")}
OUTPUT = "ei-context-support.jsonl"
MAX_OUTPUT = 32 * wire.MIB


def decoded_pin(decoded):
    wire.require(set(decoded) == {"CC", "CX"} and all(wire.valid_sha(value) for value in decoded.values()),
                 "EI two decoded source pins required")
    return wire.digest(wire.encode(decoded))


def original_inputs(original):
    wire.require(wire.digest(original) == ORIGINAL_PIN, "EI original registration changed")
    source = json.loads(original)["actual_source_preparation"]
    wire.require(source["geometry"] == FULL and source["window"] == [WINDOW["start"], WINDOW["end"]],
                 "EI original geometry differs")
    original_sources = {row["name"]: row for row in source["local_received_sources"]}
    pins = []
    for name, maximum in wire.EI_SUPPORT_INPUT_BOUNDS:
        row = original_sources[name]
        wire.require(row["maximum"] == maximum and wire.valid_sha(row["sha256"]), "EI source pin differs")
        pins.append(bridge.InputPin(name, row["path"], row["sha256"], maximum))
    return tuple(pins), source["decoded_archives"]


def contract(specification, original):
    pins, decoded = original_inputs(original)
    spec = json.loads(specification)
    wire.require(spec["unit_id"] == "EI-SUPPORT-1" and spec["input_contract"] == wire.EI_SUPPORT_INPUT_CONTRACT
                 and spec["source_registration"] == {"path": ORIGINAL_SPEC, "sha256": ORIGINAL_PIN}
                 and spec["geometry"] == FULL and type(spec["actual_runner_ready"]) is bool,
                 "EI support registration changed")
    return spec, pins, decoded_pin(decoded)


def invented_geometry(sessions):
    wire.require(type(sessions) is int and 1 <= sessions <= 257, "EI invented calendar bound")
    union = 20 * sessions + min(sessions, 18)
    rows = FULL["cc_rows"] * union // FULL["union_coordinates"]
    return {"sessions": sessions, "targets_per_scenario": 20 * sessions, "union_coordinates": union,
            "cc_chunks": union, "cc_rows": rows, "cx_chunks": union, "cx_rows": rows,
            "cx_daily_rows": FULL["cx_daily_rows"] * union // FULL["union_coordinates"]}


def _invented_bars(case):
    base = [(100, 102, 99, 101), (101, 104, 101, 103), (103, 106, 103, 105),
            (105, 113, 104, 112), (111, 112, 108, 109), (109, 111, 108, 110)]
    if case == 0:
        return [(100, 102, 99, 100)] * 78
    if case == 1:
        return base + [(110, 114, 110, 113)] + [(113, 115, 112, 114)] * 71
    if case == 2:
        return base + [(110, 113, 109, 111), (111, 111, 108, 110),
                       (110, 114, 109, 112)] + [(112, 113, 110, 111)] * 69
    if case == 3:
        return base + [(109, 110, 98, 99), (100, 113, 99, 112)] + [(112, 114, 111, 113)] * 70
    return base + [(110, 111, 107, 109)] * 72


def make_invented(directory, sessions, checkpoint):
    """New deterministic technical fixture; no prior actual archive is changed."""
    import gzip
    import hashlib

    from research.intraday_ed_comparison_unit import _invented_calendar, _invented_chunk, _invented_universe

    geometry = invented_geometry(sessions)
    days = _invented_calendar(sessions)
    universe, coordinates = _invented_universe(days, tuple(f"{n:06}" for n in range(1, 19)), geometry)
    sizes = {(day, symbol): count for day, symbol, count in coordinates}
    keys = tuple(sizes)
    # Full-prefix, absent-prefix and partial-tail fixtures retain exact geometry.
    balance = sizes[keys[0]] + sizes[keys[1]] - 390
    sizes[keys[0]], sizes[keys[1]] = 390, 0
    sizes[keys[2]] += balance
    for variant in universe["variants"].values():
        for day in variant["days"]:
            for target in day["targets"]:
                count = sizes[day["session"], target["symbol"]]
                target["stored_row_count"] = count or None
                if not count:
                    target["state"] = "no_stored_rows_unknown_reason"
    wire.exclusive_bytes(directory / "CB", wire.encode(universe))
    decoded = {}
    for kind in ("CC", "CX"):
        digest = hashlib.sha256()
        fd = os.open(directory / kind, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", compresslevel=1, mtime=0) as zipped:
                for index, ((day, symbol), count) in enumerate(sizes.items()):
                    checkpoint("ei_invented_chunk")
                    chunk = _invented_chunk(kind, day, symbol, count, index // 21, index % 21,
                                            kind == "CX" and index < geometry["cx_daily_rows"])
                    bars = _invented_bars(index % 7)
                    for minute, row in enumerate(chunk["rows"]):
                        if kind == "CC":
                            row.update(dict(zip(("open", "high", "low", "close"),
                                                map(str, bars[minute // 5]), strict=True)))
                            if index % 7 == 5 and minute == 17:
                                row["open"] = "invalid-invented-price"
                        elif index % 7 == 6 and minute == 26:
                            row["volume"] = "invalid-invented-volume"
                        if index % 7 == 4 and minute >= 60:
                            row["open_time_ms"] += 60000
                    raw = wire.encode(chunk)
                    zipped.write(raw)
                    digest.update(raw)
            stream.flush()
            os.fsync(stream.fileno())
        decoded[kind] = digest.hexdigest()
    entries = []
    for name, maximum in wire.EI_SUPPORT_INPUT_BOUNDS:
        digest, size = wire.file_digest(directory / name, maximum, checkpoint)
        entries.append(bridge.InputFile(name, str(directory / name), digest, maximum, size))
    return bridge.InputEnvelope(tuple(entries), decoded_pin(decoded), wire.encode(geometry), wire.EI_SUPPORT_INPUT_CONTRACT)


def _verify_science(path, universe, reader, checkpoint):
    from research import intraday_context_support as storage
    from research import intraday_context_support_reference as reference
    from research.intraday_ed_snapshot_verify import reconstruct_reference_session
    from research.intraday_pattern_calibration import session_open, session_close
    from research.intraday_ed_inputs import MINUTE
    from research.intraday_trader_inputs import _groups

    groups = _groups(universe)
    expected = iter(sorted(groups))
    cases = []
    case_counts = {kind: 0 for kind in storage.CLASSES}
    with path.open("rb") as stream:
        header = json.loads(stream.readline(storage.MAX_LINE + 1))
        wire.require(header["universe_sha256"] == universe.raw_sha256
                     and header["calendar_sha256"] == wire.digest(wire.encode(universe.sessions)),
                     "EI saved universe binding differs")
        for raw in stream:
            wire.require(len(raw) <= storage.MAX_LINE, "EI verification line bound")
            row = json.loads(raw)
            if row["kind"] != "record":
                break
            checkpoint("ei_independent_coordinate")
            key = next(expected, None)
            wire.require(key == (row["session"], row["symbol"]), "EI saved coordinate order differs")
            day, symbol = key
            price = reader.load_raw_chunk("CC", symbol, day)
            volume = reader.load_raw_chunk("CX", symbol, day)
            opening, closing = session_open(day), session_close(day)
            starts = tuple(opening + index * MINUTE for index in range((closing - opening) // MINUTE))
            original = reconstruct_reference_session(symbol, day, price, volume, requested_starts=starts)
            reference.verify_record(row, groups[key], original,
                                    price_present=price is not None, volume_present=volume is not None)
            needed = {kind: clock for kind, clock in row["first_frames"].items()
                      if case_counts[kind] < storage.CASE_LIMIT_PER_CLASS}
            selected = {}
            if needed:
                for frame in reference.reference_frames(original, through=original.closing):
                    kind = frame.classification
                    if kind in needed and frame.available_at.isoformat() == needed[kind]:
                        selected[kind] = {"kind": "chart_case", "session": day, "symbol": symbol,
                                          "classification": kind, "frame": storage.frame_payload(frame),
                                          "chart_prefix": storage.exact(frame.prefix)}
            for kind in storage.CLASSES:
                if kind in needed:
                    wire.require(kind in selected, "EI original chart case missing")
                    cases.append(selected[kind])
                    case_counts[kind] += 1
            reader.assert_unchanged()
        wire.require(next(expected, None) is None, "EI missing original coordinate")
        for case in cases:
            wire.require(wire.encode(row) == wire.encode(case), "EI chart differs from original causal prefix")
            raw = stream.readline(storage.MAX_LINE + 1)
            wire.require(len(raw) <= storage.MAX_LINE, "EI verification case bound")
            row = json.loads(raw)
        wire.require(row["kind"] == "footer" and stream.read(1) == b"", "EI original EOF mismatch")


def compute(directory, envelope, plan, *, invented_only, checkpoint):
    from research import intraday_context_support as storage
    from research import intraday_context_support_reference as reference
    from research import intraday_ed_sources as source
    from research.intraday_ed_comparison_unit import _decoded_digest

    wire.require(envelope.input_contract == wire.EI_SUPPORT_INPUT_CONTRACT, "EI input contract differs")
    geometry = bridge.canonical(envelope.geometry)
    if invented_only:
        wire.require(geometry == invented_geometry(geometry["sessions"]), "EI invented geometry differs")
    else:
        bridge._actual_envelope(envelope, plan)
    manifest = [item.metadata() for item in envelope.manifest]
    wire.validate_registered_input_manifest(manifest, input_contract=wire.EI_SUPPORT_INPUT_CONTRACT)
    files = {item.name: item for item in envelope.manifest}
    paths = {name: directory / "received" / name for name in files}
    for name, item in files.items():
        wire.require(wire.file_digest(paths[name], item.maximum, checkpoint) == (item.sha256, item.size),
                     "EI received source differs")
    universe = source.load_universe(bridge.bounded_read(paths["CB"], files["CB"].maximum),
                                    files["CB"].sha256, expected_sessions=geometry["sessions"])
    counts = {clock: {"original": len(universe.coordinates(clock))} for clock in ("S1", "S2")}
    wire.require(len({(row.session, row.symbol) for row in universe.targets}) == geometry["union_coordinates"]
                 and all(value["original"] == geometry["targets_per_scenario"] for value in counts.values()),
                 "EI original population differs")
    original_raw = bridge.bounded_read(Path(plan.local_worktree) / ORIGINAL_SPEC, wire.MIB)
    wire.require(wire.digest(original_raw) == ORIGINAL_PIN, "EI original registration changed during worker")
    original = json.loads(original_raw)
    source_spec = original["actual_source_preparation"]
    decoded = source_spec["decoded_archives"]
    if invented_only:
        decoded = {kind: _decoded_digest(paths[kind], source.MAX_STREAM, checkpoint) for kind in ("CC", "CX")}
    else:
        wire.require((universe.sessions[0], universe.sessions[-1]) == (WINDOW["start"], WINDOW["end"]),
                     "EI actual calendar differs")
    wire.require(envelope.stream_sha256 == decoded_pin(decoded), "EI decoded stream differs")
    archives = {kind: (paths[kind], source.RawArchivePin(kind, files[kind].sha256, decoded[kind],
                geometry[kind.lower() + "_chunks"], geometry[kind.lower() + "_rows"],
                0 if kind == "CC" else geometry["cx_daily_rows"])) for kind in ("CC", "CX")}
    cache = directory / "raw-byte-chunks.sqlite"
    cache_seal = source.ByteChunkIndex.build(cache, archives, checkpoint)
    output = directory / OUTPUT
    with source.audit_index(cache, archives, checkpoint, expected_cache_sha256=cache_seal["sha256"]) as reader:
        seal, summary = storage.write_support(output, universe, reader, geometry, manifest, checkpoint)
        _verify_science(output, universe, reader, checkpoint)
        saved = reference.audit_saved(output, expected_sha256=seal.sha256, expected_bytes=seal.size,
                                      expected_geometry=geometry, expected_input_manifest=manifest)
        wire.require(saved == summary, "EI saved summary differs")
        raw_audit = dict(reader.receipt)
    wire.require(wire.file_digest(output, MAX_OUTPUT, checkpoint) == (seal.sha256, seal.size), "EI saved output changed")
    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())
    wire.fsync_directory(directory)
    proof = {"verified": True, "schema": storage.SCHEMA, "full_eof_verified": True,
             "all_original_coordinates_verified": True, "invented_only": invented_only,
             "full_geometry": geometry == FULL, "input_manifest": manifest,
             "input_manifest_sha256": wire.digest(wire.encode(manifest)), "raw_byte_audit": raw_audit,
             "summary_sha256": wire.digest(wire.encode(summary)), "support_totals": summary["populations"],
             "historical_availability_certified": False}
    return bridge.OutputReceipt(seal.sha256, seal.size, envelope.geometry, wire.encode(proof), wire.encode(counts))
