"""EF snapshot hooks on the existing all-local durable execution lifecycle."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


UNIT = "EF-SNAPSHOT-1"
SPEC = "configs/research/discovery/intraday-chart-sequence-input-v1.json"
ORIGINAL = "configs/research/discovery/intraday-trader-local-input-v1.json"
SCIENCE = "configs/research/discovery/intraday-chart-sequence-comparison-v1.json"
INPUT_CONTRACT = "ef-chart-original-three-v1"
PROFILE = "ef-local-sequence-cpu1800-as4096-v1"
OUTPUT = "ef-snapshot-receipt.json"
FULL = {"sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
        "cc_chunks": 5158, "cc_rows": 1002211, "cx_chunks": 5158, "cx_rows": 1002211,
        "cx_daily_rows": 2674}
WINDOW = ["2025-09-04", "2026-09-23"]
SCIENTIFIC_WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": WINDOW[0], "end": WINDOW[1]}
STATES = ("outside_clock", "chart_unavailable", "chart_only", "common")
BOUNDS = {scenario: dict.fromkeys(STATES, 5140 * 17) for scenario in ("S1", "S2")}


def _unique_pairs(items):
    result = {}
    for key, value in items:
        wire.require(key not in result, "duplicate EF registration field")
        result[key] = value
    return result


def _json(raw):
    wire.require(type(raw) is bytes and 0 < len(raw) <= wire.MIB, "bounded EF registration required")
    def reject_constant(_):
        raise ValueError("nonfinite EF registration")

    return json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=reject_constant)


def contract(specification: bytes, original: bytes, science: bytes, *, actual: bool = False) -> tuple[dict, dict]:
    spec, saved, design = _json(specification), _json(original), _json(science)
    wire.require(spec["id"] == "intraday-chart-sequence-input-v1" and spec["campaign"] == "EF"
                 and spec["unit_id"] == UNIT and spec["input_contract"] == INPUT_CONTRACT
                 and spec["resource_profile"] == PROFILE and spec["execution_host"] == bridge.LOCAL_HOST
                 and type(spec["actual_runner_ready"]) is bool and spec["live_or_paper_order_capable"] is False
                 and spec["source_registration"]["path"] == ORIGINAL
                 and spec["source_registration"]["sha256"] == wire.digest(original)
                 and spec["science_registration"]["path"] == SCIENCE
                 and spec["science_registration"]["sha256"] == wire.digest(science)
                 and design["id"] == "intraday-chart-sequence-comparison-v1"
                 and saved["id"] == "intraday-trader-local-input-v1", "EF exact registrations differ")
    source = saved["actual_source_preparation"]
    wire.require(source["window"] == WINDOW and source["geometry"] == FULL,
                 "EF original date window or geometry differs")
    if actual:
        wire.require(spec["actual_runner_ready"], "EF actual snapshot source is not registered ready")
    return spec, source


def input_pins(source: dict) -> tuple[bridge.InputPin, ...]:
    pins = tuple(bridge.InputPin(row["name"], row["path"], row["sha256"], row["maximum"])
                 for row in source["local_received_sources"])
    wire.validate_registered_input_manifest([pin.metadata(1) for pin in pins], input_contract=INPUT_CONTRACT)
    return pins


def decoded_pin(decoded: dict) -> str:
    wire.require(set(decoded) == {"CC", "CX"} and all(wire.valid_sha(value) for value in decoded.values()),
                 "EF decoded archive pins differ")
    return wire.digest(wire.encode(decoded))


@contextmanager
def _original_three(directory, envelope, plan, source, invented_only, checkpoint):
    from research import intraday_ed_sources as archive
    from research.intraday_ed_comparison_unit import _decoded_digest

    wire.require(envelope.input_contract == INPUT_CONTRACT, "EF original three input contract differs")
    geometry = bridge.canonical(envelope.geometry)
    if not invented_only:
        bridge._actual_envelope(envelope, plan)
    paths, manifest = {}, {item.name: item for item in envelope.manifest}
    wire.validate_registered_input_manifest([item.metadata() for item in envelope.manifest], input_contract=INPUT_CONTRACT)
    for item in envelope.manifest:
        path = directory / "received" / item.name
        wire.require(wire.file_digest(path, item.maximum, checkpoint) == (item.sha256, item.size), "EF received input changed")
        paths[item.name] = path
    universe = archive.load_universe(bridge.bounded_read(paths["CB"], manifest["CB"].maximum), manifest["CB"].sha256,
                                     expected_sessions=geometry["sessions"])
    union = {(target.session, target.symbol) for target in universe.targets}
    wire.require(len(union) == geometry["union_coordinates"] and all(
        len(universe.coordinates(scenario)) == geometry["targets_per_scenario"] for scenario in ("S1", "S2")),
        "EF original target geometry differs")
    if not invented_only:
        wire.require([universe.sessions[0], universe.sessions[-1]] == WINDOW, "EF original dates differ")
    decoded = ({kind: _decoded_digest(paths[kind], archive.MAX_STREAM, checkpoint) for kind in ("CC", "CX")}
               if invented_only else source["decoded_archives"])
    wire.require(envelope.stream_sha256 == decoded_pin(decoded), "EF decoded source envelope differs")
    archives = {kind: (paths[kind], archive.RawArchivePin(kind, manifest[kind].sha256, decoded[kind],
                geometry[f"{kind.lower()}_chunks"], geometry[f"{kind.lower()}_rows"],
                geometry["cx_daily_rows"] if kind == "CX" else 0)) for kind in ("CC", "CX")}
    if invented_only:
        cache = directory / "raw-byte-chunks.sqlite"
        cache_sha = archive.ByteChunkIndex.build(cache, archives, checkpoint)["sha256"]
    else:
        cache, cache_sha = Path(source["local_raw_cache"]["path"]), source["local_raw_cache"]["sha256"]
    with archive.audit_index(cache, archives, checkpoint, expected_cache_sha256=cache_sha) as reader:
        yield universe, reader


def snapshot_hooks(plan, specification, original, science, *, invented_only, source_sha256, runtime_sha256):
    """Bind the exact closed unit without loading scientific modules in the controller."""
    spec, source = contract(specification, original, science, actual=not invented_only)
    bridge.profile_for_plan(plan)
    pins = {SPEC: wire.digest(specification), ORIGINAL: wire.digest(original), SCIENCE: wire.digest(science)}
    wire.require(plan.unit_id == UNIT and plan.specification_path == SPEC and plan.input_contract == INPUT_CONTRACT
                 and plan.resource_profile == PROFILE and plan.local_coordinator is True
                 and plan.input_pins == input_pins(source) and plan.window == wire.encode(SCIENTIFIC_WINDOW)
                 and plan.decoded_stream_sha256 == decoded_pin(source["decoded_archives"])
                 and plan.full_geometry == wire.encode(FULL) and plan.candidate_bounds == wire.encode(BOUNDS)
                 and plan.output_filename == OUTPUT and plan.output_max_bytes == wire.MIB
                 and plan.actual_runner_ready is spec["actual_runner_ready"] and set(pins) <= set(plan.source_names)
                 and type(invented_only) is bool and wire.valid_sha(source_sha256) and wire.valid_sha(runtime_sha256),
                 "EF fixed snapshot hook binding differs")

    def load_plan(config):
        hashes = config["source_hashes"]
        wire.require(config["invented_only"] is invented_only and (invented_only or
                     (config["attempt"] == "actual1" and config["sessions"] == FULL["sessions"]))
                     and all(hashes.get(name) == pin for name, pin in pins.items())
                     and wire.digest(wire.encode(hashes)) == source_sha256
                     and config["local_identity"]["runtime_sha256"] == runtime_sha256, "EF frozen source/runtime differs")
        return plan

    def make_invented(directory, sessions, checkpoint):
        from research.intraday_trader_unit import _make_invented

        wire.require(invented_only, "EF actual hook cannot create invented input")
        return replace(_make_invented(directory, sessions, checkpoint), input_contract=INPUT_CONTRACT)

    def compute(directory, envelope, checkpoint):
        from research.intraday_chart_snapshot_run import FEATURE_FILE, LABEL_FILE, build_snapshot

        binding = {"source_sha256": source_sha256, "runtime_sha256": runtime_sha256, "registrations": pins,
                   "input_manifest_sha256": wire.digest(wire.encode([item.metadata() for item in envelope.manifest])),
                   "caller": UNIT, "invented_only": invented_only}
        with _original_three(directory, envelope, plan, source, invented_only, checkpoint) as (universe, reader):
            seal, verification = build_snapshot(directory, universe, reader, wire.encode(binding), checkpoint)
            verification = {**verification, "raw_byte_audit": reader.receipt}
        result = {"schema": "EF-chart-snapshot-receipt-v1", "binding": binding, "seal": asdict(seal),
                  "files": {name: wire.regular(directory / name).st_size for name in (FEATURE_FILE, LABEL_FILE)},
                  "independent_verification": verification}
        wire.exclusive_bytes(directory / OUTPUT, wire.encode(result))
        digest, size = wire.file_digest(directory / OUTPUT, plan.output_max_bytes, checkpoint)
        return bridge.OutputReceipt(digest, size, envelope.geometry, wire.encode(verification),
                                    wire.encode(verification["candidate_counts"]))

    return bridge.UnitHooks(load_plan, make_invented, compute)
