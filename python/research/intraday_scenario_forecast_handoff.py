"""Bind a model worker to one independently audited EH snapshot publication."""

from dataclasses import dataclass
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


@dataclass(frozen=True)
class SnapshotPredecessor:
    invented_only: bool
    publication_sha256: str
    receipt_audit_sha256: str
    output_sha256: str
    output_bytes: int
    merge_commit: str
    tree: str
    source_manifest_sha256: str
    runtime_sha256: str


def _read_pinned(path: Path, sha: str) -> dict:
    raw = bridge.bounded_read(path, 65536)
    wire.require(wire.digest(raw) == sha, "EH predecessor pinned document changed")
    return bridge.canonical(raw)


def validate_predecessor(root: Path, pin: SnapshotPredecessor, *, geometry: dict, window: dict) -> tuple[Path, dict]:
    """Read only original receipt metadata and its exact completed journal span."""
    unit = bridge.EH_SNAPSHOT_UNIT_ID
    wire.require(type(pin) is SnapshotPredecessor and type(pin.invented_only) is bool
                 and all(wire.valid_sha(value) for value in (pin.publication_sha256, pin.receipt_audit_sha256,
                         pin.output_sha256, pin.source_manifest_sha256, pin.runtime_sha256))
                 and type(pin.output_bytes) is int and 0 < pin.output_bytes <= bridge.EH_SNAPSHOT_OUTPUT_MAX_BYTES,
                 "EH predecessor fixed pins required")
    attempt = bridge.EH_SUPPORT_QUALIFICATION_ATTEMPT if pin.invented_only else bridge.EH_SUPPORT_ATTEMPT
    output = root / "output" / (unit + ("-invented-" if pin.invented_only else "-") + attempt)
    audit_path = root / "launches" / unit / attempt / "original-receipt-audit.json"
    audit = _read_pinned(audit_path, pin.receipt_audit_sha256)
    publication = _read_pinned(output / "publication.json", pin.publication_sha256)
    hashes = publication["source_hashes"]
    identity = publication["local_identity"]
    specification_sha = hashes[bridge.EH_SNAPSHOT_SPECIFICATION_PATH]
    expected_identity = {"merge_commit": pin.merge_commit, "tree": pin.tree, "resource_profile": wire.ORDINARY300.id,
                         "runtime_sha256": pin.runtime_sha256, "execution_host": bridge.LOCAL_HOST,
                         "source_manifest_sha256": pin.source_manifest_sha256}
    wire.require(publication["remote_identity"] == identity
                 and all(identity[key] == value for key, value in expected_identity.items())
                 and wire.digest(wire.encode(hashes)) == pin.source_manifest_sha256
                 and all(wire.valid_sha(hashes.get(name)) for name in (bridge.EH_SNAPSHOT_RECEIPT_AUDITOR,
                         bridge.EH_SNAPSHOT_SPECIFICATION_PATH)), "EH predecessor reviewed source identity differs")
    wire.require(audit == {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS",
                 "invented_only": pin.invented_only, "science_reexecuted": False, "unit_id": unit, "attempt": attempt,
                 "publication_sha256": pin.publication_sha256, "source_manifest_sha256": pin.source_manifest_sha256,
                 "output_sha256": pin.output_sha256, "merge_commit": pin.merge_commit, "tree": pin.tree,
                 "runtime_sha256": pin.runtime_sha256, "auditor_source_sha256": hashes[bridge.EH_SNAPSHOT_RECEIPT_AUDITOR]},
                 "EH predecessor original receipt audit differs")
    proof = publication["independent_verification"]
    wire.require(publication["status"] == "completed" and publication["unit_id"] == unit
                 and publication["invented_only"] is pin.invented_only and publication["full_bridge"] is True
                 and publication["local_pipe_test"] is False and publication["resource_profile"] == wire.ORDINARY300.id
                 and publication["input_contract"] == wire.EH_SUPPORT_INPUT_CONTRACT
                 and publication["geometry"] == geometry and publication["verification_complete"] is True
                 and publication["output_sha256"] == pin.output_sha256 and publication["output_bytes"] == pin.output_bytes
                 and proof["schema"] == bridge.EH_SNAPSHOT_OUTPUT_SCHEMA and proof["verified"] is True
                 and proof["all_original_coordinates_verified"] is True and proof["full_eof_verified"] is True
                 and proof["full_geometry"] is True, "EH predecessor completed full publication differs")

    journal = output / "invented-experiments.jsonl" if pin.invented_only else root / "canonical" / "experiments.jsonl"
    wire.require(publication["invented_journal"] == (str(journal) if pin.invented_only else None),
                 "EH predecessor journal path differs")
    start, finish = publication["canonical_offsets"]
    terminal, digest, end = bridge.journal_record(journal, start, "completed", publication["run_id"], end=finish)
    parameters, result = terminal["parameters"], terminal["result"]
    wire.require(end == finish and digest == publication["terminal_record_sha256"]
                 and terminal["study_id"] == unit and terminal["code_version"] == pin.merge_commit
                 and terminal["window"] == window and terminal["specification_sha256"] == specification_sha
                 and parameters["invented_only"] is pin.invented_only and parameters["attempt"] == attempt
                 and parameters["source_manifest_sha256"] == pin.source_manifest_sha256
                 and all(result[key] == publication[key] for key in ("output_sha256", "output_bytes",
                         "verification_complete", "independent_verification")), "EH predecessor terminal span differs")
    return output / bridge.EH_SNAPSHOT_OUTPUT_FILENAME, proof["snapshot_summary"]["members"]
