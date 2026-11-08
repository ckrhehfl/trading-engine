"""Authenticate the spent EH management attempt and its audited replacement."""

from collections.abc import Callable
from hashlib import sha1
import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_scenario_snapshot_retry import _bounded_read, _span


ORIGINAL_UNIT = "EH-MANAGEMENT-S1-C33-QUAL-RETRY"
RECOVERY_UNIT = "EH-MANAGEMENT-S1-C33-ACTUAL-RETRY"
ATTEMPT = "actual1"
ORIGINAL_MARKER = "eh-management-unresolved.json"
RECOVERY_MARKER = "eh-management-recovery-unresolved.json"
FAILURE_PIN = "var/eh-management-original-failure-v1.json"
RECOVERY_SPEC = "configs/research/discovery/intraday-sequential-management-s1-c33-actual-recovery-v1.json"
RECOVERY_AUDITOR = "var/eh-management-receipt-audit-v3.py"


def _document(raw: bytes, *, journal: bool = False) -> dict:
    value = json.loads(raw)
    canonical = ((json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()
                 if journal else wire.encode(value))
    wire.require(type(value) is dict and raw == canonical, "canonical EH management recovery control required")
    return value


def _control(path: Path) -> tuple[dict, str]:
    raw = _bounded_read(path, 65536)
    return _document(raw), wire.digest(raw)


def _launch_scope(root: Path, unit: str) -> dict:
    leaf = f"{unit}-{ATTEMPT}"
    return {"unit_id": unit, "attempt": ATTEMPT, "invented_only": False, "local_coordinator": True,
            "local_execution_root": str(root), "execution_host": "local_wsl_only",
            "resource_profile": wire.ORDINARY300.id, "automatic_retries": 0,
            "journal": str(root / "canonical" / "experiments.jsonl"),
            **{key: str(root / key / leaf) for key in ("cache", "output", "diagnostics")}}


def same_release_sources(root: Path, hashes: dict, specification: str, revision: str,
                         git: Callable[..., str]) -> dict:
    """Only the fixed unit registration differs across the recovery profiles."""
    raw = _bounded_read(root / RECOVERY_SPEC, 65536)
    blob = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
    wire.require(blob == git("rev-parse", f"{revision}:{RECOVERY_SPEC}"),
                 "EH management retry registration differs from the reviewed release")
    return {**{name: pin for name, pin in hashes.items() if name != specification}, RECOVERY_SPEC: wire.digest(raw)}


def verify_failed_attempt(root: Path, pin: dict) -> int:
    """Return the exact end of the preserved failure, without opening its ledger."""
    hashes = ("launch_sha256", "process_failure_sha256", "marker_sha256")
    wire.require(type(pin) is dict and set(pin) == {*hashes, "run_id", "merge_commit", "started", "failed"}
                 and all(wire.valid_sha(pin[key]) for key in hashes), "EH management original failure pin differs")
    start, split = _span(pin["started"])
    middle, end = _span(pin["failed"])
    wire.require(split == middle, "EH management original journal spans must be adjacent")
    scope = _launch_scope(root, ORIGINAL_UNIT)
    marker, digest = _control(root / "canonical" / ORIGINAL_MARKER)
    wire.require(digest == pin["marker_sha256"] and wire.encode(marker) == wire.encode(
        {"attempt": ATTEMPT, "state": "unresolved", "automatic_retries": 0}), "EH management original marker differs")
    launch, digest = _control(root / "launches" / ORIGINAL_UNIT / ATTEMPT / "launch.json")
    wire.require(digest == pin["launch_sha256"] and launch["merge_commit"] == pin["merge_commit"]
                 and wire.encode({key: launch[key] for key in scope}) == wire.encode(scope),
                 "EH management original launch differs")
    failure, digest = _control(Path(scope["diagnostics"]) / "process-failure.json")
    wire.require(digest == pin["process_failure_sha256"] and failure["status"] == "unknown"
                 and failure["automatic_retries"] == 0 and failure["children"]["worker"]["returncode"] == 1,
                 "EH management original process failure differs")
    raw = _bounded_read(Path(scope["journal"]), 32768, start, end)
    lines = (raw[:split - start], raw[split - start:])
    wire.require(all(wire.digest(line) == pin[key]["sha256"] for line, key in zip(lines, ("started", "failed"), strict=True)),
                 "EH management original journal digest differs")
    started, failed = (_document(line, journal=True) for line in lines)
    wire.require(started["record_type"] == "discovery_trial" and started["study_id"] == ORIGINAL_UNIT
                 and started["run_id"] == pin["run_id"] and started["code_version"] == pin["merge_commit"]
                 and started["status"] == "started" and started["promotion_allowed"] is False
                 and failed == {**started, "status": "failed", "error_type": "EndOfFrames", "logged_at": failed["logged_at"]},
                 "EH management original journal transition differs")
    for path in (Path(scope["output"]) / "publication.json", Path(scope["diagnostics"]) / "process-receipt.json"):
        wire.safe_path(path)
        wire.require(not os.path.lexists(path), "EH management original attempt must remain unpublished")
    return end


def verify_audited_recovery(root: Path, config: dict, recovery_hashes: dict, failure_end: int) -> int:
    """Gate later profiles on the fixed actual result, its original audit and journal."""
    scope = _launch_scope(root, RECOVERY_UNIT)
    directory = root / "launches" / RECOVERY_UNIT / ATTEMPT
    launch, _ = _control(directory / "launch.json")
    manifest_sha = wire.digest(wire.encode(recovery_hashes))
    identity = {**config["local_identity"], "source_manifest_sha256": manifest_sha}
    release = {key: config[key] for key in ("merge_commit", "reviewed_commit", "tree")}
    fixed = {**scope, **release, "source_hashes": recovery_hashes, "local_identity": identity, "remote_identity": identity}
    wire.require(wire.encode({key: launch[key] for key in fixed}) == wire.encode(fixed),
                 "EH management audited retry release differs")
    publication, pub_sha = _control(Path(scope["output"]) / "publication.json")
    expected = {"status": "completed", "unit_id": RECOVERY_UNIT, "invented_only": False,
                "full_bridge": True, "local_pipe_test": False, "verification_complete": True,
                "input_contract": wire.EH_MANAGEMENT_INPUT_CONTRACT, "resource_profile": wire.ORDINARY300.id,
                "source_hashes": recovery_hashes, "local_identity": identity, "remote_identity": identity}
    wire.require(wire.encode({key: publication[key] for key in expected}) == wire.encode(expected)
                 and publication["independent_verification"]["verified"] is True
                 and wire.valid_sha(publication["output_sha256"]), "EH management actual retry publication differs")
    audit, _ = _control(directory / "original-receipt-audit.json")
    wire.require(wire.encode(audit) == wire.encode({"schema": "eh-support-original-receipt-audit-v1", "status": "PASS",
        "invented_only": False, "science_reexecuted": False, "unit_id": RECOVERY_UNIT, "attempt": ATTEMPT,
        "publication_sha256": pub_sha, "output_sha256": publication["output_sha256"],
        "source_manifest_sha256": manifest_sha, "merge_commit": release["merge_commit"], "tree": release["tree"],
        "runtime_sha256": identity["runtime_sha256"], "auditor_source_sha256": recovery_hashes[RECOVERY_AUDITOR]}),
        "EH management actual retry independent receipt differs")
    start, end = _span({"offsets": publication["canonical_offsets"], "sha256": publication["terminal_record_sha256"]})
    wire.require(start >= failure_end, "EH management actual retry journal precedes the failed attempt")
    lines = _bounded_read(Path(scope["journal"]), 32768, start, end).splitlines(keepends=True)
    wire.require(len(lines) == 2 and wire.digest(lines[-1]) == publication["terminal_record_sha256"],
                 "EH management actual retry journal digest differs")
    started, completed = (_document(line, journal=True) for line in lines)
    common = {"study_id": RECOVERY_UNIT, "run_id": publication["run_id"], "code_version": release["merge_commit"],
              "specification_sha256": recovery_hashes[RECOVERY_SPEC], "promotion_allowed": False}
    wire.require(started["status"] == "started" and completed["status"] == "completed"
                 and all({key: record[key] for key in common} == common for record in (started, completed))
                 and completed["result"]["output_sha256"] == publication["output_sha256"],
                 "EH management actual retry journal transition differs")
    return end
