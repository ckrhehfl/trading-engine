"""Bounded, read-only authentication of the two fixed EI support attempts.

The caller authenticates the registration supplying the pins and holds the
existing research locks. Published census bytes are hashed without semantic
replay; original CB/CC/CX inputs and the current checkout are never opened.
"""

from datetime import datetime, timezone
import json
from math import isfinite
import os
from pathlib import Path
from uuid import UUID

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_support_unit import FULL, WINDOW


NATIVE = bridge.EI_SUPPORT_EXECUTION_ROOT
QUALIFICATION = "qualification-v3"
_UNIT, _SPEC = bridge.EI_SUPPORT_UNIT_ID, bridge.EI_SUPPORT_SPECIFICATION_PATH
_RUNTIME, _AUDITOR = bridge.EI_SUPPORT_RUNTIME, bridge.EI_SUPPORT_RECEIPT_AUDITOR
_SCHEMA = "EI-SNAPSHOT-1-audited-support-predecessor-v1"
_PROFILE, _CONTRACT = wire.ORDINARY300, wire.EI_SUPPORT_INPUT_CONTRACT
_RECORD_FIELDS = {"launch_sha256", "publication_sha256", "audit_sha256", "output_sha256", "output_bytes",
    "source_manifest_sha256", "runtime_sha256", "auditor_source_sha256", "terminal_record_sha256",
    "canonical_offsets", "process_receipt_sha256", "run_id"}
_RESULT = {"output_sha256", "output_bytes", "geometry", "verification_complete", "independent_verification", "candidate_counts"}


def _need(condition, reason):
    wire.require(condition, "EI predecessor " + reason)


def _same(value, expected, reason):
    _need(wire.encode(value) == wire.encode(expected), reason)


def _shape(pin):
    fixed = {"schema": _SCHEMA, "unit_id": _UNIT, "registration": _SPEC,
             "qualification_attempt": QUALIFICATION, "actual_attempt": "actual1"}
    _need(type(pin) is dict and set(pin) == set(fixed) | {
        "merge_commit", "reviewed_commit", "tree", "registration_sha256", "qualification", "actual"}, "pin fields differ")
    _same({key: pin[key] for key in fixed}, fixed, "fixed predecessor differs")
    for key in ("merge_commit", "reviewed_commit", "tree"):
        _need(type(pin[key]) is str and len(pin[key]) == 40 and all(char in "0123456789abcdef" for char in pin[key]), "Git pin differs")
    _need(wire.valid_sha(pin["registration_sha256"]), "registration pin differs")
    for name in ("qualification", "actual"):
        row = pin[name]
        _need(type(row) is dict and set(row) == _RECORD_FIELDS, "receipt pin fields differ")
        _need(all(wire.valid_sha(row[key]) for key in _RECORD_FIELDS - {"output_bytes", "canonical_offsets", "run_id"}), "receipt digest differs")
        offsets = row["canonical_offsets"]
        start, end = offsets if type(offsets) is list and len(offsets) == 2 else (None, None)
        _need(type(row["output_bytes"]) is int and 0 < row["output_bytes"] <= bridge.EI_SUPPORT_OUTPUT_MAX_BYTES
              and type(start) is int and type(end) is int and 0 <= start < end and end - start <= 32768
              and (name != "qualification" or start == 0), "bounded receipt span differs")
        try:
            _need(type(row["run_id"]) is str and str(UUID(row["run_id"])) == row["run_id"], "run identity differs")
        except (ValueError, AttributeError) as error:
            raise ValueError("EI predecessor run identity differs") from error
    for key in ("source_manifest_sha256", "runtime_sha256", "auditor_source_sha256"):
        _same(pin["qualification"][key], pin["actual"][key], "qualification and actual releases differ")


def _absent(paths):
    for path in paths:
        wire.safe_path(path.parent)
        _need(not os.path.lexists(path), "unresolved or failed marker present")


def _journal(path, row, launch, publication, modes, remember):
    before = wire.regular(path)
    start, end = row["canonical_offsets"]
    _need(end <= before.st_size and (not launch["invented_only"] or end == before.st_size), "journal extent differs")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        _need(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before), "journal changed before read")
        stream.seek(start)
        raw = stream.read(end - start)
        _need(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before), "journal changed during read")
    remember(path, before)
    lines = raw.splitlines(keepends=True)
    _need(len(raw) == end - start and len(lines) == 2 and wire.digest(lines[-1]) == row["terminal_record_sha256"], "terminal journal bytes differ")
    parameters = {**modes, "invented_only": launch["invented_only"], "attempt": launch["attempt"],
        "source_manifest_sha256": row["source_manifest_sha256"], "resource_profile": _PROFILE.id,
        "automatic_retries": 0, "input_contract": _CONTRACT}
    common = {"record_type": "discovery_trial", "study_id": _UNIT, "run_id": row["run_id"],
        "specification_sha256": launch["source_hashes"][_SPEC], "parameters": parameters, "window": WINDOW,
        "code_version": launch["merge_commit"], "promotion_allowed": False, "promotion_trial_increment": 0}
    terminal = {key: publication[key] for key in ("output_sha256", "output_bytes", "verification_complete", "independent_verification", "aggregate_budget")}
    clocks = []
    for line, expected in zip(lines, ({**common, "status": "started"},
            {**common, "status": "completed", "result": {**terminal, "automatic_retries": 0}}), strict=True):
        value = json.loads(line)
        _need(type(value) is dict and (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode() == line, "journal encoding differs")
        stamp = value.pop("logged_at")
        at = datetime.fromisoformat(stamp)
        _need(at.utcoffset() == timezone.utc.utcoffset(at) and at.isoformat() == stamp, "journal clock differs")
        clocks.append(at.timestamp())
        _same(value, expected, "journal identity or result differs")
    _need(0 <= clocks[1] - clocks[0] < _PROFILE.wall_seconds and clocks[1] <= launch["deadline_epoch"], "journal deadline differs")


def _process(process, publication, modes, ack):
    aggregate = wire.aggregate_local_roles(publication["aggregate_budget"]["samples"], profile=_PROFILE)
    _same(publication["aggregate_budget"], aggregate, "ordinary aggregate differs")
    fixed = {**modes, "status": "completed_ack_received", "terminal": ack,
             "canonical_truth": "local WSL journal only", "automatic_retries": 0}
    _need(type(process) is dict and set(process) == set(fixed) | {"children", "controller"}, "process fields differ")
    _same({key: process[key] for key in fixed}, fixed, "process acknowledgement differs")
    children, samples = process["children"], aggregate["samples"]
    _need(type(children) is dict and set(children) == {"worker", "coordinator"}, "child roster differs")
    for role, child in children.items():
        sample = samples[role]
        _need(type(child) is dict and set(child) == {"pid", "returncode", "cpu_self_seconds", "peak_rss_kib", "measurement"}
              and type(child["returncode"]) is int and child["returncode"] == 0 and type(child["pid"]) is int
              and child["pid"] == sample["pid"] and child["measurement"] == "wait4 dedicated child/descendants; diagnostic only"
              and type(child["cpu_self_seconds"]) in (int, float) and isfinite(child["cpu_self_seconds"])
              and sample["cpu_self_seconds"] <= child["cpu_self_seconds"] <= sample["limits"]["cpu_seconds"]
              and type(child["peak_rss_kib"]) is int and 0 < child["peak_rss_kib"] <= sample["limits"]["address_space_bytes"] // 1024,
              "child exit or counters differ")
    for role, after in (("controller", process["controller"]), ("coordinator", publication["coordinator_after_terminal"])):
        wire.validate_role_sample(after, role, profile=_PROFILE)
        before = samples[role]
        _need(after["pid"] == before["pid"] and after["readback"] == before["readback"]
              and after["cpu_self_seconds"] >= before["cpu_self_seconds"]
              and after["helpers"]["cpu_seconds"] >= before["helpers"]["cpu_seconds"]
              and after["helpers"]["readbacks"][:before["helpers"]["count"]] == before["helpers"]["readbacks"], "post-terminal counters differ")


def _proof(proof, invented, expected_inputs):
    expected = {"verified": True, "schema": bridge.EI_SUPPORT_OUTPUT_SCHEMA, "full_eof_verified": True,
        "all_original_coordinates_verified": True, "invented_only": invented, "full_geometry": True, "historical_availability_certified": False}
    _need(type(proof) is dict and set(proof) == set(expected) | {
        "input_manifest", "input_manifest_sha256", "raw_byte_audit", "summary_sha256", "support_totals"}, "independent proof fields differ")
    _same({key: proof[key] for key in expected}, expected, "independent proof differs")
    manifest = wire.validate_registered_input_manifest(proof["input_manifest"], input_contract=_CONTRACT)
    _need(wire.valid_sha(proof["summary_sha256"]) and wire.digest(wire.encode(manifest)) == proof["input_manifest_sha256"], "input manifest digest differs")
    if not invented:
        _same(manifest, [item.metadata(saved["size"]) for item, saved in zip(expected_inputs, manifest, strict=True)], "actual original inputs differ")
    raw = proof["raw_byte_audit"]
    expected = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True, "full_eof_verified": True, "raw_keys": FULL["cc_chunks"] + FULL["cx_chunks"]}
    _need(type(raw) is dict and set(raw) == set(expected) | {"archives", "cache_sha256"}
          and wire.valid_sha(raw["cache_sha256"]) and type(raw["archives"]) is dict and set(raw["archives"]) == {"CC", "CX"}, "raw audit fields differ")
    _same({key: raw[key] for key in expected}, expected, "raw audit geometry differs")
    for kind, item in zip(("CC", "CX"), manifest[1:], strict=True):
        archive = raw["archives"][kind]
        expected = {"kind": kind, "compressed_sha256": item["sha256"], "expected_chunks": FULL[kind.lower() + "_chunks"],
            "expected_rows": FULL[kind.lower() + "_rows"], "expected_daily_rows": FULL["cx_daily_rows"] if kind == "CX" else 0}
        _need(type(archive) is dict and set(archive) == set(expected) | {"decoded_sha256"} and wire.valid_sha(archive["decoded_sha256"]), "archive audit fields differ")
        _same({key: archive[key] for key in expected}, expected, "archive audit identity differs")


def validate_support_predecessor(pin, native, expected_inputs, checkpoint) -> dict:
    """Verify exact prior receipts and published hashes under the caller's locks."""
    _shape(pin)
    _need(isinstance(native, Path) and native == NATIVE and callable(checkpoint), "fixed native root required")
    wire.safe_path(native)
    _need(type(expected_inputs) is tuple and len(expected_inputs) == 3
          and all(type(item) is bridge.InputPin for item in expected_inputs), "typed original input pins required")
    wire.validate_registered_input_manifest([item.metadata(1) for item in expected_inputs], input_contract=_CONTRACT)
    identities = {}

    def remember(path, before):
        identity = wire.file_identity(before)
        _need(identity == wire.file_identity(wire.regular(path)), "prior file changed")
        identities[path] = identity

    def read(path, expected_sha=None):
        checkpoint("EI_predecessor_metadata")
        before = wire.regular(path)
        raw = bridge.bounded_read(path, 65536)
        remember(path, before)
        _need(expected_sha is None or wire.digest(raw) == expected_sha, "pinned metadata bytes differ")
        return bridge.canonical(raw)

    paths, markers = {}, [native / "canonical" / bridge.EI_SUPPORT_MARKER]
    for name, attempt in (("qualification", QUALIFICATION), ("actual", "actual1")):
        leaf = _UNIT + ("-invented-" if name == "qualification" else "-") + attempt
        cache, output, diagnostics = (native / kind / leaf for kind in ("cache", "output", "diagnostics"))
        journal = output / "invented-experiments.jsonl" if name == "qualification" else native / "canonical" / "experiments.jsonl"
        paths[name] = attempt, cache, output, diagnostics, journal
        markers.extend((output / "unresolved.json", output / "bridge-failure.json", cache / "worker-failure.json", diagnostics / "process-failure.json"))
    _absent(markers)
    publications, launches = {}, {}
    modes = {"local_coordinator": True, "local_execution_root": str(native), "execution_host": bridge.LOCAL_HOST}
    for name, (attempt, cache, output, diagnostics, journal) in paths.items():
        row, invented = pin[name], name == "qualification"
        directory = native / "launches" / _UNIT / attempt
        launch = read(directory / "launch.json", row["launch_sha256"])
        fixed = {**modes, "unit_id": _UNIT, "attempt": attempt, "invented_only": invented, "local_pipe_test": False,
            "sessions": FULL["sessions"], "wall_seconds": _PROFILE.wall_seconds, "automatic_retries": 0,
            "resource_profile": _PROFILE.id, "cache": str(cache), "output": str(output), "diagnostics": str(diagnostics), "journal": str(journal),
            **{key: pin[key] for key in ("merge_commit", "reviewed_commit", "tree")}}
        _need(set(launch) == set(fixed) | {"deadline_epoch", "source_hashes", "local_identity", "remote_identity"}
              | (set() if invented else {"qualification"}), "launch fields differ")
        _same({key: launch[key] for key in fixed}, fixed, "launch identity differs")
        _need(type(launch["deadline_epoch"]) in (int, float) and isfinite(launch["deadline_epoch"])
              and 0 < launch["deadline_epoch"] < 10**12, "launch deadline differs")
        hashes = launch["source_hashes"]
        _need(type(hashes) is dict and {_SPEC, _RUNTIME, _AUDITOR} <= set(hashes) and len(hashes) <= 256
              and all(type(key) is str and wire.valid_sha(value) for key, value in hashes.items())
              and wire.digest(wire.encode(hashes)) == row["source_manifest_sha256"]
              and hashes[_SPEC] == pin["registration_sha256"] and hashes[_AUDITOR] == row["auditor_source_sha256"], "reviewed source pins differ")
        identity = launch["local_identity"]
        expected = {"source_manifest_sha256": row["source_manifest_sha256"], "runtime_sha256": row["runtime_sha256"],
            "merge_commit": pin["merge_commit"], "tree": pin["tree"], "execution_host": bridge.LOCAL_HOST, "resource_profile": _PROFILE.id}
        _need(type(identity) is dict and set(identity) == set(expected) | {"machine_sha256"}
              and wire.valid_sha(identity["machine_sha256"]), "runtime identity fields differ")
        _same({key: identity[key] for key in expected}, expected, "runtime identity differs")
        _same(launch["remote_identity"], identity, "local source identities differ")
        _same(read(directory / "launch-identity.json"), {"launch_sha256": row["launch_sha256"], "runtime_sha256": hashes[_RUNTIME],
            "merge": pin["merge_commit"], "reviewed": pin["reviewed_commit"], "tree": pin["tree"], "mode": name,
            "invented_only": invented, "qualifies_actual_by_this_record": False}, "launch receipt differs")
        publication = read(output / "publication.json", row["publication_sha256"])
        fixed = {**modes, "status": "completed", "unit_id": _UNIT, "resource_profile": _PROFILE.id, "invented_only": invented,
            "full_bridge": True, "local_pipe_test": False, "input_contract": _CONTRACT,
            "invented_journal": str(journal) if invented else None,
            **{key: launch[key] for key in ("source_hashes", "local_identity", "remote_identity")}}
        _need(set(publication) == set(fixed) | _RESULT | {"run_id", "canonical_offsets", "terminal_record_sha256", "aggregate_budget", "coordinator_after_terminal"}, "publication fields differ")
        _same({key: publication[key] for key in fixed}, fixed, "completed publication identity differs")
        keys = ("run_id", "canonical_offsets", "terminal_record_sha256", "output_sha256", "output_bytes")
        _same({key: publication[key] for key in keys}, {key: row[key] for key in keys}, "publication pins differ")
        _same(publication["geometry"], FULL, "full geometry differs")
        _same(publication["candidate_counts"], {clock: {"original": FULL["targets_per_scenario"]} for clock in ("S1", "S2")}, "original population differs")
        _need(publication["verification_complete"] is True, "verification incomplete")
        _proof(publication["independent_verification"], invented, expected_inputs)
        if not invented:
            _same(launch["qualification"], {"output": str(paths["qualification"][2]),
                "publication_sha256": pin["qualification"]["publication_sha256"], "audit_sha256": pin["qualification"]["audit_sha256"]}, "actual qualification binding differs")
        _same(read(directory / "original-receipt-audit.json", row["audit_sha256"]), {
            "schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": invented,
            "science_reexecuted": False, "unit_id": _UNIT, "attempt": attempt, "publication_sha256": row["publication_sha256"],
            **{key: row[key] for key in ("source_manifest_sha256", "output_sha256", "runtime_sha256", "auditor_source_sha256")},
            "merge_commit": pin["merge_commit"], "tree": pin["tree"]}, "original receipt audit differs")
        ack = {"kind": "COMPLETED", "status": "completed", "run_id": row["run_id"], "output_sha256": row["output_sha256"],
               "record_sha256": row["terminal_record_sha256"], "publication_sha256": row["publication_sha256"]}
        _process(read(diagnostics / "process-receipt.json", row["process_receipt_sha256"]), publication, modes, ack)
        _journal(journal, row, launch, publication, modes, remember)
        artifact = output / bridge.EI_SUPPORT_OUTPUT_FILENAME
        before = wire.regular(artifact)
        _need(wire.file_digest(artifact, bridge.EI_SUPPORT_OUTPUT_MAX_BYTES, checkpoint) == (row["output_sha256"], row["output_bytes"]), "support output bytes differ")
        remember(artifact, before)
        publications[name], launches[name] = publication, launch
    for key in ("source_hashes", "local_identity", "remote_identity"):
        _same(launches["qualification"][key], launches["actual"][key], "same-release predecessor differs")
    checkpoint("EI_predecessor_complete")
    _absent(markers)
    _need(all(wire.file_identity(wire.regular(path)) == identity for path, identity in identities.items()), "evidence changed during verification")
    return {"verified": True, "unit_id": _UNIT, "science_reexecuted": False, "output_hash_verified": True,
            **{key: pin[key] for key in ("merge_commit", "reviewed_commit", "tree", "registration_sha256")},
            "qualification": dict(pin["qualification"]), "actual": dict(pin["actual"]),
            "actual_input_manifest": publications["actual"]["independent_verification"]["input_manifest"]}
