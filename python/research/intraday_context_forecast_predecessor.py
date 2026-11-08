"""Bind EI forecasts to the independently audited EI snapshot publications.

The caller authenticates the registration supplying the pins and holds the
research locks. This gate checks saved receipt metadata and sealed file stats.
The worker must hash the two received inputs after its own durable STARTED.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import stat
from uuid import UUID

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_snapshot_unit as snapshot
from research.intraday_context_predecessor import _absent, _RECORD_FIELDS
from research.intraday_context_snapshot import FEATURE_SCHEMA, LABEL_SCHEMA
from research.intraday_context_snapshot_reference import SCHEMA as SUMMARY_SCHEMA


SCHEMA = "EI-FORECAST-1-audited-snapshot-predecessor-v1"
NATIVE = bridge.EI_SUPPORT_EXECUTION_ROOT
_BINDING = bridge._EH_BINDINGS[snapshot.UNIT_ID]
_PROFILE = wire.EI_LOCAL_MODEL
_GIT_KEYS = ("merge_commit", "reviewed_commit", "tree")
_ARTIFACT_KEYS = ("run_id", "canonical_offsets", "terminal_record_sha256", "output_sha256", "output_bytes")
_TERMINAL_KEYS = ("output_sha256", "output_bytes", "verification_complete", "independent_verification",
                  "aggregate_budget")
REPAIR_REGISTRATION = "configs/research/discovery/intraday-context-snapshot-saved-audit-v2.json"
REPAIR_REGISTRATION_SCHEMA = "ei-snapshot-saved-audit-registration-v2"
SUPPLEMENTAL_RECEIPT = "supplemental-saved-receipt-audit-v2.json"
_SUPPLEMENTAL_SCHEMA = "ei-snapshot-supplemental-saved-receipt-audit-v2"
_ORIGINAL_RECEIPT = "original-receipt-audit.json"
_REPAIR_DIGESTS = ("repair_registration_sha256", "repair_runner_sha256", "repair_header_sha256",
                   "qualification_supplement_sha256")


@dataclass(frozen=True)
class SnapshotInput:
    inputs: tuple[bridge.InputFile, bridge.InputFile]
    summary: dict
    publication_sha256: str
    audit_sha256: str
    invented_only: bool

    @property
    def members(self) -> dict:
        return self.summary["members"]


def _need(condition, message):
    wire.require(condition, "EI forecast predecessor " + message)


def _same(value, expected, message):
    _need(wire.encode(value) == wire.encode(expected), message)


def _git_pin(value):
    return type(value) is str and len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def _validate_repair_pin(repair, original_merge):
    _need(type(repair) is dict and set(repair) == {
        "registration", "repair_release", "original_audit_failure", *_REPAIR_DIGESTS}, "saved audit repair pin fields differ")
    _need(repair["registration"] == REPAIR_REGISTRATION and all(wire.valid_sha(repair[key]) for key in _REPAIR_DIGESTS),
          "saved audit repair registration or digest differs")
    release = repair["repair_release"]
    _need(type(release) is dict and set(release) == set(_GIT_KEYS) and all(map(_git_pin, release.values()))
          and release["merge_commit"] != original_merge, "saved audit repair release differs")
    failure = repair["original_audit_failure"]
    _need(type(failure) is dict and set(failure) == {"reference", "status", "sha256"}
          and failure["reference"] == "efd7df" and failure["status"] == "failed" and wire.valid_sha(failure["sha256"]),
          "saved audit repair failure pin differs")


def _validate_pins(pin):
    fixed = {"schema": SCHEMA, "unit_id": snapshot.UNIT_ID, "registration": _BINDING.specification,
             "qualification_attempt": _BINDING.qualification_attempt, "actual_attempt": bridge.EH_SUPPORT_ATTEMPT}
    fields = set(fixed) | {*_GIT_KEYS, "registration_sha256", "qualification", "actual"}
    _need(type(pin) is dict and set(pin) in (fields, fields | {"actual_saved_audit_repair"}), "pin fields differ")
    _same({key: pin[key] for key in fixed}, fixed, "fixed snapshot identity differs")
    _need(all(_git_pin(pin[key]) for key in _GIT_KEYS)
          and wire.valid_sha(pin["registration_sha256"]), "reviewed release pins differ")
    if "actual_saved_audit_repair" in pin:
        _validate_repair_pin(pin["actual_saved_audit_repair"], pin["merge_commit"])
    for name in ("qualification", "actual"):
        row = pin[name]
        _need(type(row) is dict and set(row) == _RECORD_FIELDS, "attempt pin fields differ")
        _need(all(wire.valid_sha(row[key]) for key in _RECORD_FIELDS - {"output_bytes", "canonical_offsets", "run_id"}),
              "attempt digest differs")
        offsets = row["canonical_offsets"]
        _need(type(offsets) is list and len(offsets) == 2 and all(type(x) is int for x in offsets)
              and 0 <= offsets[0] < offsets[1] and offsets[1] - offsets[0] <= 32768
              and (name != "qualification" or offsets[0] == 0)
              and type(row["output_bytes"]) is int and 0 < row["output_bytes"] <= snapshot.MAX_OUTPUT
              and type(row["run_id"]) is str and str(UUID(row["run_id"])) == row["run_id"],
              "bounded attempt coordinates differ")
    for key in ("source_manifest_sha256", "runtime_sha256", "auditor_source_sha256"):
        _same(pin["qualification"][key], pin["actual"][key], "snapshot attempts use different releases")
    _need(pin["qualification"]["run_id"] != pin["actual"]["run_id"], "snapshot attempts share a run identity")


def _supplemental_audit(original, row, repair, invented):
    return {**original, "schema": _SUPPLEMENTAL_SCHEMA, "original_launch_sha256": row["launch_sha256"],
            **{key: repair[key] for key in ("repair_release", *_REPAIR_DIGESTS[:3])},
            "original_audit_failure": None if invented else repair["original_audit_failure"],
            "qualification_supplement_sha256": None if invented else repair["qualification_supplement_sha256"]}


def _validate_journal(journal, publication, launch, modes, journal_stat):
    """Use the existing bounded journal reader for both durable records."""
    start, end = publication["canonical_offsets"]
    if launch["invented_only"]:
        _need(end == journal_stat.st_size, "qualification journal has trailing bytes")
    run_id = publication["run_id"]
    completed, digest, _ = bridge.journal_record(journal, start, "completed", run_id, end=end)
    completed_raw = (json.dumps(completed, sort_keys=True, allow_nan=False) + "\n").encode()
    _need(digest == publication["terminal_record_sha256"] == wire.digest(completed_raw),
          "completed journal digest differs")
    started, started_sha, _ = bridge.journal_record(journal, start, "started", run_id,
                                                      end=end - len(completed_raw))
    _need(started_sha == wire.digest((json.dumps(started, sort_keys=True, allow_nan=False) + "\n").encode()),
          "started journal encoding differs")
    parameters = {**modes, "invented_only": launch["invented_only"], "attempt": launch["attempt"],
                  "source_manifest_sha256": launch["local_identity"]["source_manifest_sha256"],
                  "resource_profile": _PROFILE.id, "automatic_retries": 0, "input_contract": _BINDING.input_contract}
    common = {"record_type": "discovery_trial", "study_id": snapshot.UNIT_ID, "run_id": run_id,
              "specification_sha256": launch["source_hashes"][_BINDING.specification],
              "parameters": parameters, "window": snapshot.WINDOW, "code_version": launch["merge_commit"],
              "promotion_allowed": False, "promotion_trial_increment": 0}
    result = {key: publication[key] for key in _TERMINAL_KEYS}
    clocks = []
    for actual, expected in ((started, {**common, "status": "started"}),
                             (completed, {**common, "status": "completed",
                                          "result": {**result, "automatic_retries": 0}})):
        stamp = actual.pop("logged_at")
        at = datetime.fromisoformat(stamp)
        _need(at.utcoffset() == timezone.utc.utcoffset(at) and at.isoformat() == stamp, "journal clock differs")
        _same(actual, expected, "durable journal identity or result differs")
        clocks.append(at.timestamp())
    _need(0 <= clocks[1] - clocks[0] < _PROFILE.wall_seconds and clocks[1] <= launch["deadline_epoch"],
          "journal deadline differs")


def _snapshot_summary(publication, invented, expected_inputs, cache):
    proof = publication["independent_verification"]
    fixed = {"schema": snapshot.SCHEMA, "verified": True, "full_eof_verified": True,
             "all_original_coordinates_verified": True, "invented_only": invented, "full_geometry": True,
             "historical_availability_certified": False, "stored_volume_units_certified": False,
             "contains_model_fits_or_returns": False}
    _need(type(proof) is dict and set(proof) == set(fixed) | {
        "input_manifest", "input_manifest_sha256", "raw_byte_audit", "snapshot_summary", "summary_sha256",
        "support_totals"}, "audited snapshot proof fields differ")
    _same({key: proof[key] for key in fixed}, fixed, "audited snapshot proof differs")
    manifest = wire.validate_registered_input_manifest(proof["input_manifest"], input_contract=_BINDING.input_contract)
    _need(proof["input_manifest_sha256"] == wire.digest(wire.encode(manifest)), "input manifest digest differs")
    if invented:
        _need(all(item["original_path"] == str(cache / "generated" / item["name"]) for item in manifest),
              "qualification input provenance differs")
    else:
        _same(manifest, [item.metadata(row["size"]) for item, row in zip(expected_inputs, manifest, strict=True)],
              "actual input manifest differs from original registration")
    cb = manifest[0]
    summary = proof["snapshot_summary"]
    fixed = {"schema": SUMMARY_SCHEMA, "verified": True, "full_eof_verified": True,
             "all_original_coordinates_verified": True, "original_dates": snapshot.FULL["sessions"],
             "original_coordinates": snapshot.FULL["union_coordinates"], "reference_session_calls": snapshot.FULL["union_coordinates"],
             "original_memberships": 2 * snapshot.FULL["targets_per_scenario"], "universe_sha256": cb["sha256"]}
    _need(type(summary) is dict and set(summary) == set(fixed) | {
        "calendar_sha256", "feature_sha256", "label_sha256", "counts", "counts_sha256", "members"},
        "snapshot summary fields differ")
    _same({key: summary[key] for key in fixed}, fixed, "snapshot summary or original CB differs")
    _need(wire.valid_sha(summary["calendar_sha256"]) and type(summary["counts"]) is dict
          and wire.digest(wire.encode(summary["counts"])) == summary["counts_sha256"]
          and wire.digest(wire.encode(summary)) == proof["summary_sha256"], "snapshot summary digest differs")
    _same(proof["support_totals"], summary["counts"], "snapshot support counts differ")
    members = summary["members"]
    _need(type(members) is dict and set(members) == {"features", "labels"}, "snapshot members differ")
    for name, short, schema in (("features", "feature", FEATURE_SCHEMA), ("labels", "label", LABEL_SCHEMA)):
        member = members[name]
        _need(type(member) is dict and set(member) == {"schema", "size", "records", "sha256"}
              and member["schema"] == schema and wire.valid_sha(member["sha256"])
              and member["sha256"] == summary[short + "_sha256"]
              and type(member["size"]) is int and 0 < member["size"] < publication["output_bytes"]
              and type(member["records"]) is int and member["records"] == snapshot.FULL["union_coordinates"],
              "snapshot member seal differs")
    header_size = publication["output_bytes"] - sum(member["size"] for member in members.values())
    _need(0 < header_size <= snapshot.MAX_HEADER, "snapshot member byte bounds differ")
    return cb, summary


def validate_snapshot_predecessor(pin: dict, native: Path, expected_inputs: tuple[bridge.InputPin, ...],
                                  checkpoint) -> dict[str, SnapshotInput]:
    """Return qualification and actual inputs from the same audited snapshot release.

    The prior independent audit covers reconstruction, full process budgets and
    artifact verification. A reviewed repair pin can require the separate
    same-release saved audit receipts while preserving the original failed slot.
    This handoff checks metadata without repeating the audit or reading payloads.
    """
    _validate_pins(pin)
    _need(isinstance(native, Path) and native == NATIVE and callable(checkpoint), "fixed native root required")
    wire.safe_path(native)
    _need(type(expected_inputs) is tuple and len(expected_inputs) == 3
          and all(type(item) is bridge.InputPin for item in expected_inputs), "typed original input pins required")
    wire.validate_registered_input_manifest([item.metadata(1) for item in expected_inputs],
                                            input_contract=_BINDING.input_contract)
    identities = {}

    def observe(path):
        observed = wire.regular(path)
        identity = wire.file_identity(observed)
        _need(identities.setdefault(path, identity) == identity, "evidence changed during verification")
        return observed

    def read(path, sha=None):
        checkpoint("EI_snapshot_predecessor_metadata")
        observe(path)
        raw = bridge.bounded_read(path, 65536)
        _need(sha is None or wire.digest(raw) == sha, "pinned snapshot metadata bytes differ")
        observe(path)
        return bridge.canonical(raw)

    markers = [native / "canonical" / bridge.EI_SNAPSHOT_MARKER]
    repair = pin.get("actual_saved_audit_repair")
    if repair is not None:
        markers.append(native / "launches" / snapshot.UNIT_ID / bridge.EH_SUPPORT_ATTEMPT / _ORIGINAL_RECEIPT)
    paths = {}
    for name, attempt in (("qualification", _BINDING.qualification_attempt), ("actual", bridge.EH_SUPPORT_ATTEMPT)):
        leaf = snapshot.UNIT_ID + ("-invented-" if name == "qualification" else "-") + attempt
        cache, out, diagnostics = (native / key / leaf for key in ("cache", "output", "diagnostics"))
        paths[name] = attempt, cache, out, diagnostics
        markers.extend((out / "unresolved.json", out / "bridge-failure.json", cache / "worker-failure.json",
                        diagnostics / "process-failure.json"))
    _absent(markers)

    modes = {"local_coordinator": True, "local_execution_root": str(native), "execution_host": bridge.LOCAL_HOST}
    results, launches = {}, {}
    for name, (attempt, cache, out, diagnostics) in paths.items():
        row, invented = pin[name], name == "qualification"
        journal = out / "invented-experiments.jsonl" if invented else native / "canonical" / "experiments.jsonl"
        directory = native / "launches" / snapshot.UNIT_ID / attempt
        launch = read(directory / "launch.json", row["launch_sha256"])
        fixed = {**modes, "unit_id": snapshot.UNIT_ID, "attempt": attempt, "invented_only": invented,
                 "local_pipe_test": False, "sessions": snapshot.FULL["sessions"], "wall_seconds": _PROFILE.wall_seconds,
                 "automatic_retries": 0, "resource_profile": _PROFILE.id, "cache": str(cache), "output": str(out),
                 "diagnostics": str(diagnostics), "journal": str(journal), **{key: pin[key] for key in _GIT_KEYS}}
        if not invented:
            fixed["qualification"] = {"output": str(paths["qualification"][2]),
                "publication_sha256": pin["qualification"]["publication_sha256"], "audit_sha256": pin["qualification"]["audit_sha256"]}
        _need(set(launch) == set(fixed) | {"deadline_epoch", "source_hashes", "local_identity", "remote_identity"},
              "launch fields differ")
        _same({key: launch[key] for key in fixed}, fixed, "launch identity differs")
        deadline = launch["deadline_epoch"]
        _need(type(deadline) in (int, float) and isfinite(deadline) and 0 < deadline < 10**12, "launch deadline differs")
        hashes = launch["source_hashes"]
        _need(type(hashes) is dict and len(hashes) <= 256 and all(wire.valid_sha(value) for value in hashes.values())
              and wire.valid_sha(hashes.get(_BINDING.runtime))
              and hashes.get(_BINDING.specification) == pin["registration_sha256"]
              and hashes.get(_BINDING.auditor) == row["auditor_source_sha256"]
              and hashes.get(snapshot.ORIGINAL_SPEC) == snapshot.ORIGINAL_PIN
              and wire.digest(wire.encode(hashes)) == row["source_manifest_sha256"], "reviewed source manifest differs")
        identity = launch["local_identity"]
        expected = {"source_manifest_sha256": row["source_manifest_sha256"], "runtime_sha256": row["runtime_sha256"],
                    "merge_commit": pin["merge_commit"], "tree": pin["tree"], "execution_host": bridge.LOCAL_HOST,
                    "resource_profile": _PROFILE.id}
        _need(type(identity) is dict and set(identity) == set(expected) | {"machine_sha256"}
              and wire.valid_sha(identity["machine_sha256"]), "runtime identity fields differ")
        _same({key: identity[key] for key in expected}, expected, "runtime identity differs")
        _same(launch["remote_identity"], identity, "local role identities differ")
        _same(read(directory / "launch-identity.json"), {"launch_sha256": row["launch_sha256"],
            "runtime_sha256": hashes[_BINDING.runtime], "merge": pin["merge_commit"], "reviewed": pin["reviewed_commit"],
            "tree": pin["tree"], "mode": name, "invented_only": invented, "qualifies_actual_by_this_record": False},
            "launch receipt differs")

        publication = read(out / "publication.json", row["publication_sha256"])
        fixed = {**modes, "status": "completed", "unit_id": snapshot.UNIT_ID, "resource_profile": _PROFILE.id,
                 "invented_only": invented, "full_bridge": True, "local_pipe_test": False, "input_contract": _BINDING.input_contract,
                 "invented_journal": str(journal) if invented else None, "verification_complete": True,
                 "geometry": snapshot.FULL, "candidate_counts": snapshot.COUNTS,
                 **{key: launch[key] for key in ("source_hashes", "local_identity", "remote_identity")},
                 **{key: row[key] for key in _ARTIFACT_KEYS}}
        _need(set(publication) == set(fixed) | {"independent_verification", "aggregate_budget", "coordinator_after_terminal"},
              "publication fields differ")
        _same({key: publication[key] for key in fixed}, fixed, "completed snapshot publication differs")
        original_audit = {
            "schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": invented,
            "science_reexecuted": False, "unit_id": snapshot.UNIT_ID, "attempt": attempt,
            **{key: row[key] for key in ("publication_sha256", "source_manifest_sha256", "output_sha256",
                                        "runtime_sha256", "auditor_source_sha256")},
            "merge_commit": pin["merge_commit"], "tree": pin["tree"]}
        if invented or repair is None:
            _same(read(directory / _ORIGINAL_RECEIPT, row["audit_sha256"]), original_audit, "original receipt audit differs")
        if repair is not None:
            digest = repair["qualification_supplement_sha256"] if invented else row["audit_sha256"]
            _same(read(directory / SUPPLEMENTAL_RECEIPT, digest), _supplemental_audit(original_audit, row, repair, invented),
                  "supplemental saved receipt audit differs")
        process = read(diagnostics / "process-receipt.json", row["process_receipt_sha256"])
        ack = {"kind": "COMPLETED", "status": "completed", "run_id": row["run_id"], "output_sha256": row["output_sha256"],
               "record_sha256": row["terminal_record_sha256"], "publication_sha256": row["publication_sha256"]}
        fixed = {**modes, "status": "completed_ack_received", "terminal": ack,
                 "canonical_truth": "local WSL journal only", "automatic_retries": 0}
        _need(set(process) == set(fixed) | {"children", "controller"}, "process receipt fields differ")
        _same({key: process[key] for key in fixed}, fixed, "process acknowledgement differs")
        children = process["children"]
        _need(type(children) is dict and set(children) == {"worker", "coordinator"}
              and all(type(child) is dict and type(child.get("returncode")) is int and child["returncode"] == 0
                      for child in children.values()),
              "whole bridge child exits differ")
        _validate_journal(journal, publication, launch, modes, observe(journal))
        cb, summary = _snapshot_summary(publication, invented, expected_inputs, cache)
        cb_path, artifact = cache / "received" / "CB", out / snapshot.OUTPUT
        for path, size in ((cb_path, cb["size"]), (artifact, row["output_bytes"])):
            observed = observe(path)
            _need(observed.st_size == size and stat.S_IMODE(observed.st_mode) == 0o400,
                  "sealed snapshot input size or mode differs")
        inputs = (bridge.InputFile("CB", str(cb_path), cb["sha256"], cb["maximum"], cb["size"]),
                  bridge.InputFile("EI-SNAPSHOT", str(artifact), row["output_sha256"], snapshot.MAX_OUTPUT, row["output_bytes"]))
        results[name] = SnapshotInput(inputs, summary, row["publication_sha256"], row["audit_sha256"], invented)
        launches[name] = launch
    for key in ("source_hashes", "local_identity", "remote_identity"):
        _same(launches["qualification"][key], launches["actual"][key], "same-release snapshot attempts differ")
    checkpoint("EI_snapshot_predecessor_complete")
    _absent(markers)
    _need(all(wire.file_identity(wire.regular(path)) == identity for path, identity in identities.items()),
          "evidence changed during verification")
    return results
