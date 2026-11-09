"""Admit the independently audited S2 economics receipts without reading inputs."""

from datetime import datetime, timezone
import json
from math import isfinite
import os
from pathlib import Path
import stat
from uuid import UUID

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_support_unit import FULL, WINDOW


SCHEMA = "EI-saved-fixed-exit-audited-economic-predecessor-v1"
_UNIT = "EI-ECONOMIC-S2"
_SPEC = "configs/research/discovery/intraday-context-economic-s2-v1.json"
_ARTIFACT = "ei-context-economic-ledger.jsonl"
_POLICY = "8c8ff1214f7510c48971d2a70d4c66a239c3071a0d7c322ebe79f0b18f98214b"
_MODES = {"qualification": "qualification-v1", "actual": "actual1"}
_ATTEMPT_FIELDS = {"launch_sha256", "publication_sha256", "audit_sha256", "snapshot_summary"}


def _require(condition, message):
    wire.require(condition, "EI saved fixed-exit predecessor " + message)


def _match(value, expected, message):
    _require(type(value) is dict and expected.keys() <= value.keys()
             and wire.encode({key: value[key] for key in expected}) == wire.encode(expected), message)


def _journal_span(path, offsets, common, result, terminal_pin, deadline):
    wire.safe_path(path)
    start, end = offsets
    before = wire.regular(path)
    _require(0 <= start < end <= before.st_size and end - start <= 32768, "original journal span differs")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        _require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before), "original journal changed")
        stream.seek(start)
        raw = stream.read(end - start)
        _require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before), "original journal changed")
    lines = raw.splitlines(keepends=True)
    _require(len(raw) == end - start and len(lines) == 2 and wire.digest(lines[-1]) == terminal_pin,
             "original terminal pin differs")
    clocks = []
    expected = ({**common, "status": "started"}, {**common, "status": "completed", "result": result})
    for line, fields in zip(lines, expected, strict=True):
        record = json.loads(line)
        _require(type(record) is dict and (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode() == line,
                 "original journal canonical bytes differ")
        stamp = record.pop("logged_at", None)
        _require(type(stamp) is str and wire.encode(record) == wire.encode(fields), "original durable journal record differs")
        clock = datetime.fromisoformat(stamp)
        _require(clock.tzinfo is not None and clock.utcoffset() == timezone.utc.utcoffset(clock) and clock.isoformat() == stamp,
                 "original journal clock differs")
        clocks.append(clock.timestamp())
    _require(0 <= clocks[1] - clocks[0] < wire.EI_LOCAL_MODEL.wall_seconds and clocks[1] <= deadline,
             "original journal wall deadline differs")


def _summary(summary, cb_sha, snapshot_size):
    expected = {"schema": "ei-context-snapshot-independent-v1", "verified": True, "full_eof_verified": True,
                "all_original_coordinates_verified": True, "original_dates": FULL["sessions"],
                "original_coordinates": FULL["union_coordinates"], "reference_session_calls": FULL["union_coordinates"],
                "original_memberships": 2 * FULL["targets_per_scenario"], "universe_sha256": cb_sha}
    _match(summary, expected, "snapshot summary identity differs")
    _require(set(summary) == set(expected) | {"calendar_sha256", "feature_sha256", "label_sha256", "counts",
                                               "counts_sha256", "members"}
             and wire.valid_sha(summary["calendar_sha256"]) and type(summary["counts"]) is dict
             and wire.digest(wire.encode(summary["counts"])) == summary["counts_sha256"], "snapshot summary differs")
    members = summary["members"]
    _require(type(members) is dict and set(members) == {"features", "labels"}, "snapshot member roster differs")
    for name, schema in (("feature", "ei-context-feature-snapshot-draft-v1"), ("label", "ei-context-path-labels-draft-v1")):
        row = members[name + "s"]
        _require(type(row) is dict and set(row) == {"schema", "size", "records", "sha256"}
                 and row["schema"] == schema and wire.valid_sha(row["sha256"]) and row["sha256"] == summary[name + "_sha256"]
                 and type(row["size"]) is int and 0 < row["size"] < snapshot_size
                 and type(row["records"]) is int and row["records"] == FULL["union_coordinates"], "snapshot member seal differs")
    _require(0 < snapshot_size - sum(row["size"] for row in members.values()) <= 4096, "snapshot framing differs")


def _files(publication, invented, cache, output):
    proof = publication["independent_verification"]
    expected = {"schema": "EI-context-economic-comparison-v1", "verified": True, "full_eof_verified": True,
                "full_geometry": True, "all_original_coordinates_verified": True, "invented_only": invented,
                "scenario": "S2", "cost_bps": 33, "include_reentry": True, "folds": 5,
                "original_evaluation_coordinates": 3140, "initial_model": "context_sequence", "event_level": True,
                "account_return": False, "execution_policy_sha256": _POLICY, "saved_ledger_audit_complete": False}
    _match(proof, expected, "original independent proof differs")
    _require(set(proof) == set(expected) | {"input_manifest", "input_manifest_sha256", "entry_decision_seals", "training_source_sha256"}
             and wire.valid_sha(proof["training_source_sha256"]), "original proof fields differ")
    manifest = wire.validate_registered_input_manifest(proof["input_manifest"], input_contract=wire.EI_ECONOMIC_INPUT_CONTRACT)
    _require(wire.digest(wire.encode(manifest)) == proof["input_manifest_sha256"], "original input manifest pin differs")
    decisions = proof["entry_decision_seals"]
    _require(type(decisions) is list and len(decisions) == 5, "original decision roster differs")
    maximum = 128 * wire.MIB
    files = [bridge.InputFile(**{**row, "original_path": str(cache / "received" / row["name"])})
             for row in (manifest[0], manifest[3], manifest[4])]
    for fold, (seal, coordinates) in enumerate(zip(decisions, (640, 640, 620, 620, 620), strict=True), 1):
        _require(type(seal) is dict and set(seal) == {"sha256", "size", "coordinates"}
                 and wire.valid_sha(seal["sha256"]) and type(seal["size"]) is int and 0 < seal["size"] <= maximum
                 and type(seal["coordinates"]) is int and seal["coordinates"] == coordinates, "original decision seal differs")
        files.append(bridge.InputFile(f"EI-DECISION-{fold}", str(cache / f"ei-entry-decisions-fold-{fold}.jsonl"),
                                      seal["sha256"], maximum, seal["size"]))
    files.append(bridge.InputFile("EI-ECONOMIC-LEDGER", str(output / _ARTIFACT),
                                  publication["output_sha256"], maximum, publication["output_bytes"]))
    wire.validate_registered_input_manifest([row.metadata() for row in files], input_contract=wire.EI_SAVED_FIXED_EXIT_INPUT_CONTRACT)
    return tuple(files)


def validate_saved_fixed_exit_predecessor(pin: dict, native: Path, *, invented_only: bool, checkpoint) -> tuple[tuple[bridge.InputFile, ...], dict]:
    """Resolve one mode's input manifests under the caller's release and research locks.

    The caller pins the complete registration and snapshot summaries. The old
    independent receipt audit already checked the normal merge and upstream
    snapshot/forecast chain. The new transport verifies payload hashes later.
    """
    _require(isinstance(native, Path) and native == bridge.EI_SUPPORT_EXECUTION_ROOT and type(invented_only) is bool
             and callable(checkpoint) and type(pin) is dict and set(pin) == {"schema", "release", "attempts"}
             and pin["schema"] == SCHEMA, "fixed registration or native root differs")
    wire.safe_path(native)
    release, attempts = pin["release"], pin["attempts"]
    _require(type(release) is dict and set(release) == {"merge_commit", "reviewed_commit", "tree", "registration_sha256"}
             and wire.valid_sha(release["registration_sha256"])
             and all(type(release[key]) is str and len(release[key]) == 40 and all(c in "0123456789abcdef" for c in release[key])
                     for key in ("merge_commit", "reviewed_commit", "tree"))
             and release["merge_commit"] != release["reviewed_commit"], "reviewed release differs")
    _require(type(attempts) is dict and set(attempts) == set(_MODES)
             and all(type(row) is dict and set(row) == _ATTEMPT_FIELDS
                     and all(wire.valid_sha(row[key]) for key in _ATTEMPT_FIELDS - {"snapshot_summary"}) for row in attempts.values()),
             "attempt pins differ")

    def read(path, digest):
        checkpoint("ei_saved_fixed_exit_predecessor_metadata")
        raw = bridge.bounded_read(path, 65536)
        _require(wire.digest(raw) == digest, "original receipt hash differs")
        return bridge.canonical(raw)

    checked, qualification = None, None
    modes = {"local_coordinator": True, "local_execution_root": str(native), "execution_host": bridge.LOCAL_HOST}
    for mode, attempt in _MODES.items():
        invented = mode == "qualification"
        refs = attempts[mode]
        leaf = f"{_UNIT}-{'invented-' if invented else ''}{attempt}"
        cache, output, diagnostics = (native / key / leaf for key in ("cache", "output", "diagnostics"))
        journal = output / "invented-experiments.jsonl" if invented else native / "canonical" / "experiments.jsonl"
        launch_dir = native / "launches" / _UNIT / attempt
        markers = (native / "canonical" / bridge.EI_ECONOMIC_MARKER, output / "unresolved.json", output / "bridge-failure.json",
                   cache / "worker-failure.json", diagnostics / "process-failure.json")
        _require(not any(path.exists() or path.is_symlink() for path in markers), "original evidence unresolved")
        common = {**modes, "unit_id": _UNIT, "invented_only": invented, "resource_profile": wire.EI_LOCAL_MODEL.id,
                  "local_pipe_test": False}
        launch = read(launch_dir / "launch.json", refs["launch_sha256"])
        fixed = {**common, **{key: release[key] for key in ("merge_commit", "reviewed_commit", "tree")},
                 "attempt": attempt, "cache": str(cache), "output": str(output), "diagnostics": str(diagnostics),
                 "journal": str(journal), "automatic_retries": 0, "sessions": FULL["sessions"],
                 "wall_seconds": wire.EI_LOCAL_MODEL.wall_seconds}
        if not invented:
            fixed["qualification"] = qualification
        _require(set(launch) == set(fixed) | {"deadline_epoch", "source_hashes", "local_identity", "remote_identity"},
                 "original launch fields differ")
        _match(launch, fixed, "original launch identity differs")
        hashes, identity = launch["source_hashes"], launch["local_identity"]
        _require(type(hashes) is dict and 0 < len(hashes) <= 128 and all(wire.valid_sha(value) for value in hashes.values())
                 and hashes.get(_SPEC) == release["registration_sha256"] and bridge.EI_ECONOMIC_RECEIPT_AUDITOR in hashes,
                 "original reviewed source manifest differs")
        source_sha = wire.digest(wire.encode(hashes))
        _match(identity, {"source_manifest_sha256": source_sha, "merge_commit": release["merge_commit"], "tree": release["tree"],
                          "resource_profile": wire.EI_LOCAL_MODEL.id, "execution_host": bridge.LOCAL_HOST}, "original runtime identity differs")
        _require(wire.valid_sha(identity.get("runtime_sha256")) and wire.valid_sha(identity.get("machine_sha256"))
                 and launch["remote_identity"] == identity and type(launch["deadline_epoch"]) in (int, float)
                 and isfinite(launch["deadline_epoch"]) and launch["deadline_epoch"] > 0, "original runtime or deadline differs")

        publication = read(output / "publication.json", refs["publication_sha256"])
        _match(publication, {**common, "status": "completed", "full_bridge": True, "verification_complete": True,
                             "source_hashes": hashes, "local_identity": identity, "remote_identity": identity,
                             "input_contract": wire.EI_ECONOMIC_INPUT_CONTRACT, "geometry": FULL,
                             "candidate_counts": {"S2": {"original": 3140}},
                             "invented_journal": str(journal) if invented else None}, "original publication identity differs")
        audit = read(launch_dir / "original-receipt-audit.json", refs["audit_sha256"])
        expected_audit = {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": invented,
                          "science_reexecuted": False, "unit_id": _UNIT, "attempt": attempt,
                          "publication_sha256": refs["publication_sha256"], "output_sha256": publication["output_sha256"],
                          "source_manifest_sha256": source_sha, "runtime_sha256": identity["runtime_sha256"],
                          "merge_commit": release["merge_commit"], "tree": release["tree"],
                          "auditor_source_sha256": hashes[bridge.EI_ECONOMIC_RECEIPT_AUDITOR]}
        _require(wire.encode(audit) == wire.encode(expected_audit), "original independent receipt differs")
        offsets, run_id = publication["canonical_offsets"], publication["run_id"]
        _require(type(offsets) is list and len(offsets) == 2 and all(type(value) is int for value in offsets)
                 and type(run_id) is str and str(UUID(run_id)) == run_id, "original journal binding differs")
        start, end = offsets
        if invented:
            _require(start == 0 and wire.regular(journal).st_size == end, "original qualification journal differs")
        journal_common = {"record_type": "discovery_trial", "study_id": _UNIT, "run_id": run_id,
                         "specification_sha256": release["registration_sha256"], "code_version": release["merge_commit"],
                         "promotion_allowed": False, "promotion_trial_increment": 0, "window": WINDOW,
                         "parameters": {**modes, "invented_only": invented, "attempt": attempt, "source_manifest_sha256": source_sha,
                                        "resource_profile": wire.EI_LOCAL_MODEL.id, "automatic_retries": 0,
                                        "input_contract": wire.EI_ECONOMIC_INPUT_CONTRACT}}
        result = {**{key: publication[key] for key in ("output_sha256", "output_bytes", "verification_complete",
                                                      "independent_verification", "aggregate_budget")}, "automatic_retries": 0}
        _journal_span(journal, offsets, journal_common, result, publication["terminal_record_sha256"], launch["deadline_epoch"])
        files = _files(publication, invented, cache, output)
        summary = refs["snapshot_summary"]
        _summary(summary, files[0].sha256, files[1].size)
        for row in files:
            info = wire.regular(Path(row.original_path))
            _require(info.st_size == row.size and stat.S_IMODE(info.st_mode) == 0o400, "original payload size or mode differs")
        _require(not any(path.exists() or path.is_symlink() for path in markers), "original evidence changed during validation")
        if invented:
            qualification = {"output": str(output), "publication_sha256": refs["publication_sha256"], "audit_sha256": refs["audit_sha256"]}
        if invented == invented_only:
            checked = files, summary
            break
    return checked
