"""Closed EG scenario snapshot composition over the audited coverage predecessor."""

from dataclasses import asdict
from itertools import groupby
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as archive
from research.intraday_campaign_wsl_bridge import bounded_read
from research.intraday_highvol_coverage import CoverageSeal, FILENAME, MAX_COMPRESSED, open_coverage, project, write_coverage
from research.intraday_highvol_population import iter_coverage_records
from research.intraday_highvol_reference import verify_coverage
from research.intraday_highvol_snapshot import iter_scenario_day
from research.intraday_highvol_snapshot_io import FILENAMES, ROLES, write_snapshot
from research.intraday_highvol_unit import FULL, ORIGINAL, PROFILE, SCIENCE, WINDOW, _json, open_original_inputs
from research.intraday_trader_inputs import _groups


UNIT = "EG-SCENARIO-SNAPSHOT-1"
SPEC = "configs/research/discovery/intraday-highvol-snapshot-v1.json"
INPUT_CONTRACT = "eg-highvol-scenario-snapshot-v1"
OUTPUT = "eg-scenario-receipt.json"
COVERAGE_RELEASE = "25116aa02375010123a34e24a27da6cbdf2e3f75"
COVERAGE_DIRECTORY = Path("/home/minju/research-evidence/eg-highvol-20261106/EG-COVERAGE-1/actual1")
COVERAGE_RECEIPT = "eg-coverage-receipt.json"


def contract(specification: bytes, science: bytes, original: bytes, *, actual: bool) -> tuple[dict, dict]:
    spec, design, saved = map(_json, (specification, science, original))
    wire.require(type(actual) is bool and spec["id"] == "intraday-highvol-snapshot-v1"
                 and spec["campaign"] == "EG" and spec["unit_id"] == UNIT
                 and spec["input_contract"] == INPUT_CONTRACT and spec["resource_profile"] == PROFILE
                 and spec["execution_host"] == "local_wsl_only" and spec["uses_cuda"] is False
                 and spec["actual_attempt"] == "actual1" and spec["automatic_actual_retry"] is False
                 and spec["live_or_paper_order_capable"] is False and spec["geometry"] == FULL
                 and spec["coverage_release"] == COVERAGE_RELEASE
                 and spec["science_registration"] == {"path": SCIENCE, "sha256": wire.digest(science)}
                 and spec["source_registration"] == {"path": ORIGINAL, "sha256": wire.digest(original)}
                 and design["id"] == "intraday-highvol-v1" and design["campaign"] == "EG"
                 and UNIT in design["campaign_bounds"]["primary_units"]
                 and design["resource_approval"]["profile"] == PROFILE
                 and saved["id"] == "intraday-trader-local-input-v1", "EG snapshot registration differs")
    source = saved["actual_source_preparation"]
    wire.require(source["window"] == WINDOW and source["geometry"] == FULL, "EG snapshot source geometry differs")
    if actual:
        wire.require(spec["actual_runner_ready"] is True and spec["actual_execution_authorized_by_this_file"] is True,
                     "EG snapshot actual source not registered ready")
    return spec, source


def _predecessor(audit_sha256: str) -> dict:
    """Bind only the once-executed, independently audited actual coverage output."""
    wire.require(wire.valid_sha(audit_sha256), "EG audited coverage predecessor pin required")
    root = COVERAGE_DIRECTORY
    audit_raw = bounded_read(root / "original-audit.json", wire.MIB)
    wire.require(wire.digest(audit_raw) == audit_sha256, "EG coverage predecessor audit changed")
    audit = _json(audit_raw)
    publication_raw = bounded_read(root / "publication.json", wire.MIB)
    publication = _json(publication_raw)
    binding = publication["binding"]
    result = publication["result"]
    wire.require(audit["status"] == "PASS" and audit["science_reexecuted"] is False
                 and audit["binding"] == binding and audit["full_geometry"] == result["geometry"] == FULL
                 and audit["publication_sha256"] == wire.digest(publication_raw)
                 and binding["unit_id"] == "EG-COVERAGE-1" and binding["attempt"] == "actual1"
                 and binding["invented_only"] is False and binding["merge_commit"] == COVERAGE_RELEASE
                 and result["independent_verification"]["complete"] is True, "EG coverage predecessor scope differs")
    pins = {row["name"]: row for row in result["artifacts"]}
    receipt_raw = bounded_read(root / COVERAGE_RECEIPT, wire.MIB)
    receipt = _json(receipt_raw)
    wire.require(set(pins) == {FILENAME, COVERAGE_RECEIPT}
                 and (wire.digest(receipt_raw), len(receipt_raw)) ==
                 (pins[COVERAGE_RECEIPT]["sha256"], pins[COVERAGE_RECEIPT]["size"])
                 and receipt["schema"] == "EG-coverage-receipt-v1" and receipt["geometry"] == FULL
                 and all(receipt["binding"].get(key) == value for key, value in binding.items())
                 and receipt["report"] == result["summary"] and receipt["independent_verification"]["complete"] is True
                 and (receipt["seal"]["sha256"], receipt["seal"]["compressed_bytes"]) ==
                 (pins[FILENAME]["sha256"], pins[FILENAME]["size"]), "EG coverage predecessor artifact differs")
    return receipt


def source_days(records, universe, reader, checkpoint, *, decode):
    """Visit exactly the original union coordinates and per-day timing rosters."""
    population = _groups(universe)
    by_day = groupby(sorted(population), key=lambda item: item[0])
    stored = iter(records)
    for day, coordinates in by_day:
        sessions, frames = {}, []
        rosters = {timing: [] for timing in ("S1", "S2")}
        for _, symbol in coordinates:
            checkpoint("eg_snapshot_source_coordinate")
            frame = next(stored, None)
            targets = tuple(sorted(population[(day, symbol)], key=lambda target: (target.scenario, target.rank)))
            wire.require(frame is not None and (frame["session"], frame["symbol"]) == (day, symbol)
                         and frame["targets"] == project(targets), "EG coverage original population differs")
            frames.append(frame)
            for target in targets:
                rosters[target.scenario].append(symbol)
        if any(frame["episodes"]["episodes"] for frame in frames):
            reader.assert_unchanged()
            for frame in frames:
                symbol = frame["symbol"]
                price = reader.load_raw_chunk("CC", symbol, day)
                volume = reader.load_raw_chunk("CX", symbol, day)
                sessions[symbol] = decode(symbol, day, price, volume).session
                checkpoint("eg_snapshot_decode_session")
            reader.assert_unchanged()
        yield frames, sessions, {timing: tuple(symbols) for timing, symbols in rosters.items()}
    wire.require(next(stored, None) is None, "EG extra predecessor coordinate")


def _produced(coverage_path, coverage_seal, coverage_binding, universe, raw_reader, checkpoint):
    with open_coverage(coverage_path, coverage_seal, checkpoint) as coverage:
        wire.require(coverage.header["binding"] == coverage_binding, "EG coverage predecessor header differs")
        for frames, sessions, rosters in source_days(coverage.records(), universe, raw_reader, checkpoint,
                                                      decode=archive.session_inputs):
            if sessions:
                yield from sorted(iter_scenario_day(frames, sessions, rosters, checkpoint),
                                  key=lambda row: (row[0]["key"][2], row[0]["key"][1]))


def compute_snapshot(directory: Path, source: dict, binding: dict, checkpoint, *, invented: dict | None = None,
                     coverage_audit_sha256: str | None = None) -> dict:
    from research.intraday_highvol_snapshot_reference import verify_snapshot

    is_invented = invented is not None
    wire.require(type(binding) is dict and binding.get("unit_id") == UNIT
                 and binding.get("invented_only") is is_invented and binding.get("input_contract") == INPUT_CONTRACT
                 and all(wire.valid_sha(binding.get(key)) for key in
                         ("source_sha256", "runtime_sha256", "registration_sha256")), "EG snapshot run binding differs")
    if is_invented:
        wire.require(coverage_audit_sha256 is None, "EG invented run cannot access actual predecessor")
        prior_receipt = None
    else:
        prior_receipt = _predecessor(coverage_audit_sha256)
    with open_original_inputs(directory, source, checkpoint, invented=invented) as opened:
        universe, raw_reader, geometry, manifest_sha = opened
        if is_invented:
            coverage_path = directory / FILENAME
            coverage_binding = {**binding, "kind": "EG-new-invented-predecessor", "input_manifest_sha256": manifest_sha}
            coverage_seal, coverage_report = write_coverage(coverage_path, universe, coverage_binding,
                iter_coverage_records(universe, raw_reader, checkpoint), checkpoint)
            verified_coverage = verify_coverage(coverage_path, coverage_seal, universe, raw_reader,
                                                coverage_binding, checkpoint)
            wire.require(verified_coverage == coverage_report, "EG invented predecessor reconstruction differs")
        else:
            coverage_path = COVERAGE_DIRECTORY / FILENAME
            coverage_binding = prior_receipt["binding"]
            wire.require(coverage_binding["input_manifest_sha256"] == manifest_sha,
                         "EG snapshot originals differ from the coverage predecessor")
            coverage_seal = CoverageSeal(**prior_receipt["seal"])
            coverage_report = prior_receipt["report"]
        bound = {**binding, "input_manifest_sha256": manifest_sha,
                 "coverage_seal_sha256": coverage_seal.sha256, "coverage_audit_sha256": coverage_audit_sha256}
        scope = {"sessions": list(universe.sessions), "raw_sha256": universe.raw_sha256,
                 "target_count": len(universe.targets)}
        seals, report = write_snapshot(directory,
            _produced(coverage_path, coverage_seal, coverage_binding, universe, raw_reader, checkpoint),
            bound, scope, checkpoint)
        independent = verify_snapshot(directory, seals, bound, scope, coverage_path, coverage_seal,
                                      coverage_binding, universe, raw_reader, checkpoint)
        wire.require(report == independent, "EG independent snapshot summary differs")
        for timing in ("S1", "S2"):
            wire.require(report["timings"][timing]["counts"].get("observed_clusters", 0) ==
                         coverage_report["scenarios"][timing]["episodes"]["observed_clusters"],
                         "EG snapshot omitted an observed cluster")
        raw_audit = raw_reader.receipt
    receipt = {"schema": "EG-scenario-snapshot-receipt-v1", "binding": bound, "geometry": geometry,
               "seals": {role: asdict(seals[role]) for role in ROLES}, "report": report,
               "independent_verification": {"complete": True, "records": geometry["union_coordinates"],
                   "events": report["union_events"], "raw_byte_audit": raw_audit,
                   "report_sha256": wire.digest(wire.encode(independent))}}
    wire.exclusive_bytes(directory / OUTPUT, wire.encode(receipt))
    checkpoint("eg_snapshot_persisted")
    return receipt


def verify_persisted(directory: Path, receipt: dict, checkpoint) -> dict:
    raw = bounded_read(directory / OUTPUT, wire.MIB)
    stored = _json(raw)
    independent = stored["independent_verification"]
    wire.require(raw == wire.encode(receipt) and stored["schema"] == "EG-scenario-snapshot-receipt-v1"
                 and independent["complete"] is True and independent["records"] == stored["geometry"]["union_coordinates"]
                 and independent["events"] == stored["report"]["union_events"]
                 and independent["report_sha256"] == wire.digest(wire.encode(stored["report"])),
                 "EG snapshot persisted reconstruction differs")
    artifacts = []
    for role in ROLES:
        seal = CoverageSeal(**stored["seals"][role])
        name = FILENAMES[role]
        digest, size = wire.file_digest(directory / name, MAX_COMPRESSED, checkpoint)
        wire.require((digest, size, seal.records) == (seal.sha256, seal.compressed_bytes, independent["events"]),
                     "EG snapshot persisted artifact differs")
        artifacts.append({"name": name, "sha256": digest, "size": size})
    artifacts.append({"name": OUTPUT, "sha256": wire.digest(raw), "size": len(raw)})
    return {"schema": "EG-persisted-artifacts-v1", "independent_reconstruction_complete": True,
            "artifacts": artifacts}
