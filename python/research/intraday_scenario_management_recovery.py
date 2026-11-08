"""Authenticate the spent EH management attempt and its audited replacement."""

from collections.abc import Callable
from hashlib import sha1
import json
import os
from pathlib import Path
import stat

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
SEQUENCE = (RECOVERY_UNIT, "EH-MANAGEMENT-S1-C43", "EH-MANAGEMENT-S2-C33", "EH-MANAGEMENT-S2-C43")


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


def same_release_sources(root: Path, hashes: dict, current_specification: str, specification: str, revision: str,
                         git: Callable[..., str]) -> dict:
    """Only the fixed unit registration differs across the recovery profiles."""
    raw = _bounded_read(root / specification, 65536)
    blob = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
    wire.require(blob == git("rev-parse", f"{revision}:{specification}"),
                 "EH management predecessor registration differs from the reviewed release")
    return {**{name: pin for name, pin in hashes.items() if name != current_specification}, specification: wire.digest(raw)}


def verify_failed_attempt(root: Path, pin: dict) -> int:
    """Return the exact end of the preserved failure, without opening its ledger."""
    hashes = ("launch_sha256", "process_failure_sha256", "marker_sha256")
    wire.require(type(pin) is dict and set(pin) == {*hashes, "run_id", "merge_commit", "started", "failed", "ledger_bytes"}
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
    ledger = wire.regular(Path(scope["cache"]) / "eh-management-ledger.jsonl")
    wire.require(type(pin["ledger_bytes"]) is int and 0 < ledger.st_size == pin["ledger_bytes"]
                 and stat.S_IMODE(ledger.st_mode) == 0o400, "EH management original failed ledger metadata differs")
    return end


def verify_audited_predecessor(root: Path, config: dict, predecessor_hashes: dict, previous_end: int, *,
                              unit: str, specification: str) -> int:
    """Bind a prior profile to its fixed actual result, audit and journal."""
    scope = _launch_scope(root, unit)
    directory = root / "launches" / unit / ATTEMPT
    launch, launch_sha = _control(directory / "launch.json")
    manifest_sha = wire.digest(wire.encode(predecessor_hashes))
    identity = {**config["local_identity"], "source_manifest_sha256": manifest_sha}
    release = {key: config[key] for key in ("merge_commit", "reviewed_commit", "tree")}
    fixed = {**scope, **release, "source_hashes": predecessor_hashes, "local_identity": identity, "remote_identity": identity}
    wire.require(wire.encode({key: launch[key] for key in fixed}) == wire.encode(fixed),
                 "EH management audited retry release differs")
    publication, pub_sha = _control(Path(scope["output"]) / "publication.json")
    expected = {"status": "completed", "unit_id": unit, "invented_only": False,
                "full_bridge": True, "local_pipe_test": False, "verification_complete": True,
                "input_contract": wire.EH_MANAGEMENT_INPUT_CONTRACT, "resource_profile": wire.ORDINARY300.id,
                "source_hashes": predecessor_hashes, "local_identity": identity, "remote_identity": identity}
    wire.require(wire.encode({key: publication[key] for key in expected}) == wire.encode(expected)
                 and publication["independent_verification"]["verified"] is True
                 and wire.valid_sha(publication["output_sha256"]), "EH management actual retry publication differs")
    audit, _ = _control(directory / "original-receipt-audit.json")
    diagnostics = Path(scope["diagnostics"])
    failure = diagnostics / "process-failure.json"
    wire.safe_path(failure)
    wire.require(not os.path.lexists(failure), "EH management prior actual reported a process failure")
    _, process_sha = _control(diagnostics / "process-receipt.json")
    span_sha = audit.get("canonical_span_sha256")
    wire.require(wire.valid_sha(span_sha) and wire.encode(audit) == wire.encode({
        "schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "canonical_span_sha256": span_sha,
        "launch_sha256": launch_sha, "process_receipt_sha256": process_sha,
        "invented_only": False, "science_reexecuted": False, "unit_id": unit, "attempt": ATTEMPT,
        "publication_sha256": pub_sha, "output_sha256": publication["output_sha256"],
        "source_manifest_sha256": manifest_sha, "merge_commit": release["merge_commit"], "tree": release["tree"],
        "runtime_sha256": identity["runtime_sha256"], "auditor_source_sha256": predecessor_hashes[RECOVERY_AUDITOR]}),
        "EH management actual retry independent receipt differs")
    start, end = _span({"offsets": publication["canonical_offsets"], "sha256": publication["terminal_record_sha256"]})
    wire.require(start >= previous_end, "EH management actual journal precedes its required predecessor")
    raw_span = _bounded_read(Path(scope["journal"]), 32768, start, end)
    wire.require(wire.digest(raw_span) == span_sha, "EH management independently audited journal span differs")
    lines = raw_span.splitlines(keepends=True)
    wire.require(len(lines) == 2 and wire.digest(lines[-1]) == publication["terminal_record_sha256"],
                 "EH management actual retry journal digest differs")
    started, completed = (_document(line, journal=True) for line in lines)
    common = {"study_id": unit, "run_id": publication["run_id"], "code_version": release["merge_commit"],
              "specification_sha256": predecessor_hashes[specification], "promotion_allowed": False}
    wire.require(started["status"] == "started" and completed["status"] == "completed"
                 and all({key: record[key] for key in common} == common for record in (started, completed))
                 and completed["result"]["output_sha256"] == publication["output_sha256"],
                 "EH management actual retry journal transition differs")
    return end


def verify_admission(root: Path, source_root: Path, current_specification: str, config: dict,
                     git: Callable[..., str]) -> int:
    """Verify the preserved failure and the fixed prefix of independently audited actuals."""
    pin, digest = _control(source_root / FAILURE_PIN)
    hashes = config["source_hashes"]
    wire.require(digest == hashes[FAILURE_PIN], "EH management failure pin changed")
    end = verify_failed_attempt(root, pin)
    for unit in SEQUENCE[:SEQUENCE.index(config["unit_id"])]:
        profile = unit.removeprefix("EH-MANAGEMENT-").lower()
        specification = (RECOVERY_SPEC if unit == RECOVERY_UNIT else
                         f"configs/research/discovery/intraday-sequential-management-{profile}-v1.json")
        predecessor_hashes = same_release_sources(source_root, hashes, current_specification,
                                                   specification, config["merge_commit"], git)
        end = verify_audited_predecessor(root, config, predecessor_hashes, end, unit=unit, specification=specification)
    return end
