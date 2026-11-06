"""Closed EG coverage computation; the local lifecycle owns execution permission.

This file composes existing raw-byte authentication and the new producer and
independent verifier. It never launches a process or borrows an old unit's
execution permission. No public command accepts arbitrary source paths.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import json

from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_wsl_bridge import bounded_read


UNIT = "EG-COVERAGE-1"
SPEC = "configs/research/discovery/intraday-highvol-coverage-v1.json"
SCIENCE = "configs/research/discovery/intraday-highvol-v1.json"
ORIGINAL = "configs/research/discovery/intraday-trader-local-input-v1.json"
OUTPUT = "eg-coverage-receipt.json"
INPUT_CONTRACT = "eg-highvol-original-three-v1"
PROFILE = "eg-local-cpu4-cuda-v1"
WINDOW = ["2025-09-04", "2026-09-23"]
FULL = {"sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
        "cc_chunks": 5158, "cc_rows": 1002211, "cx_chunks": 5158, "cx_rows": 1002211,
        "cx_daily_rows": 2674}


def _json(raw: bytes) -> dict:
    wire.require(type(raw) is bytes and 0 < len(raw) <= wire.MIB, "bounded EG registration required")

    def pairs(items):
        result = {}
        for key, value in items:
            wire.require(key not in result, "duplicate EG registration field")
            result[key] = value
        return result

    def reject(_):
        raise ValueError("nonfinite EG registration")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
    wire.require(type(value) is dict, "EG registration object required")
    return value


def contract(specification: bytes, science: bytes, original: bytes, *, actual: bool) -> tuple[dict, dict]:
    """Validate scope before the caller supplies any market input bytes."""
    spec, design, saved = map(_json, (specification, science, original))
    wire.require(type(actual) is bool and spec["id"] == "intraday-highvol-coverage-v1"
        and spec["campaign"] == "EG" and spec["unit_id"] == UNIT
        and spec["input_contract"] == INPUT_CONTRACT and spec["resource_profile"] == PROFILE
        and spec["execution_host"] == "local_wsl_only" and spec["uses_cuda"] is False
        and spec["actual_attempt"] == "actual1" and spec["automatic_actual_retry"] is False
        and spec["live_or_paper_order_capable"] is False and spec["geometry"] == FULL
        and spec["science_registration"] == {"path": SCIENCE, "sha256": wire.digest(science)}
        and spec["source_registration"] == {"path": ORIGINAL, "sha256": wire.digest(original)}
        and design["id"] == "intraday-highvol-v1" and design["campaign"] == "EG"
        and design["resource_approval"]["profile"] == PROFILE
        and saved["id"] == "intraday-trader-local-input-v1", "EG closed registration differs")
    source = saved["actual_source_preparation"]
    wire.require(source["window"] == WINDOW and source["geometry"] == FULL,
                 "EG original window or geometry differs")
    if actual:
        wire.require(spec["actual_runner_ready"] is True and spec["actual_execution_authorized_by_this_file"] is True,
                     "EG coverage actual source not registered ready")
    return spec, source


@contextmanager
def open_original_inputs(directory: Path, source: dict, checkpoint, *, invented: dict | None):
    """Authenticate the fixed CB/CC/CX bytes and their existing actual index."""
    from research import intraday_ed_sources as archive

    is_invented = invented is not None
    active = invented if is_invented else source
    geometry = active["geometry"]
    if not is_invented:
        wire.require(source["window"] == WINDOW and geometry == FULL, "EG actual input scope changed")
    roster = active["local_received_sources"]
    wire.require(type(roster) is list and len(roster) == 3 and
                 [(row["name"], row["maximum"]) for row in roster] == list(wire.EE_INPUT_BOUNDS),
                 "EG original three source roster differs")
    manifest = []
    paths = {}
    for row in roster:
        checkpoint("eg_original_source_pin")
        path = Path(row["path"])
        digest, size = wire.file_digest(path, row["maximum"], checkpoint)
        wire.require(digest == row["sha256"], "EG original source changed")
        paths[row["name"]] = path
        manifest.append({**row, "size": size})
    universe = archive.load_universe(bounded_read(paths["CB"], roster[0]["maximum"]), roster[0]["sha256"],
                                     expected_sessions=geometry["sessions"])
    union = {(target.session, target.symbol) for target in universe.targets}
    wire.require(len(union) == geometry["union_coordinates"] and all(
        len(universe.coordinates(scenario)) == geometry["targets_per_scenario"] for scenario in ("S1", "S2")),
        "EG original target geometry differs")
    if not is_invented:
        wire.require([universe.sessions[0], universe.sessions[-1]] == WINDOW, "EG original calendar changed")
    archives = {kind: (paths[kind], archive.RawArchivePin(kind,
        next(row["sha256"] for row in roster if row["name"] == kind), active["decoded_archives"][kind],
        geometry[kind.lower() + "_chunks"], geometry[kind.lower() + "_rows"],
        geometry["cx_daily_rows"] if kind == "CX" else 0)) for kind in ("CC", "CX")}
    if is_invented:
        cache = directory / "raw-byte-chunks.sqlite"
        cache_sha = archive.ByteChunkIndex.build(cache, archives, checkpoint)["sha256"]
    else:
        cache = Path(source["local_raw_cache"]["path"])
        cache_sha = source["local_raw_cache"]["sha256"]
    with archive.audit_index(cache, archives, checkpoint, expected_cache_sha256=cache_sha) as reader:
        yield universe, reader, geometry, wire.digest(wire.encode(manifest))


def compute_coverage(
    directory: Path, source: dict, binding: dict, checkpoint: Callable[[str], object], *,
    invented: dict | None = None,
) -> dict:
    """Read only the closed registered originals after the caller's durable start."""
    from research.intraday_highvol_coverage import FILENAME, write_coverage
    from research.intraday_highvol_population import iter_coverage_records
    from research.intraday_highvol_reference import verify_coverage

    wire.require(type(binding) is dict and binding.get("unit_id") == UNIT
                 and binding.get("invented_only") is (invented is not None)
                 and binding.get("input_contract") == INPUT_CONTRACT,
                 "EG computation binding differs")
    wire.require(all(wire.valid_sha(binding.get(key)) for key in
                     ("source_sha256", "runtime_sha256", "registration_sha256")), "EG binding pins required")
    with open_original_inputs(directory, source, checkpoint, invented=invented) as opened:
        universe, reader, geometry, manifest_sha = opened
        bound = {**binding, "input_manifest_sha256": manifest_sha}
        seal, report = write_coverage(directory / FILENAME, universe, bound,
                                     iter_coverage_records(universe, reader, checkpoint), checkpoint)
        independently_rebuilt = verify_coverage(directory / FILENAME, seal, universe, reader, bound, checkpoint)
        wire.require(report == independently_rebuilt, "EG independent coverage summary differs")
        raw_audit = reader.receipt
    receipt = {"schema": "EG-coverage-receipt-v1", "binding": bound, "geometry": geometry,
               "seal": asdict(seal), "report": report,
               "independent_verification": {"complete": True, "records": seal.records,
                                             "raw_byte_audit": raw_audit,
                                             "report_sha256": wire.digest(wire.encode(independently_rebuilt))}}
    wire.exclusive_bytes(directory / OUTPUT, wire.encode(receipt))
    checkpoint("eg_coverage_persisted")
    return receipt


def verify_persisted(directory: Path, receipt: dict, checkpoint: Callable[[str], object]) -> dict:
    """Authenticate the persisted result after its separate raw reconstruction.

    This does not replace the raw verifier above or rerun market science. It
    closes the handoff to the execution journal with the two fixed artifact
    digests and refuses an incomplete or altered reconstruction receipt.
    """
    from research.intraday_highvol_coverage import FILENAME, MAX_COMPRESSED

    stored = _json(bounded_read(directory / OUTPUT, wire.MIB))
    wire.require(wire.encode(stored) == wire.encode(receipt)
                 and stored.get("schema") == "EG-coverage-receipt-v1",
                 "EG persisted receipt differs")
    verified = stored["independent_verification"]
    seal = stored["seal"]
    wire.require(verified["complete"] is True
                 and verified["records"] == seal["records"] == stored["geometry"]["union_coordinates"]
                 and verified["report_sha256"] == wire.digest(wire.encode(stored["report"]))
                 and type(verified["raw_byte_audit"]) is dict,
                 "EG persisted independent verification incomplete")
    artifact_sha, size = wire.file_digest(directory / FILENAME, MAX_COMPRESSED, checkpoint)
    wire.require((artifact_sha, size) == (seal["sha256"], seal["compressed_bytes"]),
                 "EG persisted coverage artifact changed")
    receipt_sha, receipt_size = wire.file_digest(directory / OUTPUT, wire.MIB, checkpoint)
    return {"schema": "EG-persisted-artifacts-v1", "independent_reconstruction_complete": True,
            "artifacts": [{"name": FILENAME, "sha256": artifact_sha, "size": size},
                          {"name": OUTPUT, "sha256": receipt_sha, "size": receipt_size}]}
