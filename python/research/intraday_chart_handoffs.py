"""Closed EF provenance for downstream access to already-audited local artifacts."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


HANDOFF_NAME, HANDOFF_LIMIT = wire.EF_SEALED_BOUNDS[0]
SCHEMA = "EF-sealed-unit-handoff-v1"
FIT_RECEIPT = "ef-fit-receipt.json"
COMPARISON_RECEIPT = "ef-comparison-receipt.json"
SNAPSHOT_RECEIPT = "ef-snapshot-receipt.json"


def _predecessors(unit: str) -> tuple[str, ...]:
    wire.require(unit in bridge.EF_COMPARISON_UNITS, "registered EF handoff unit required")
    return (bridge.EF_UNIT_ID, *bridge.EF_FIT_UNITS) if unit == bridge.EF_REPORT_UNIT else (bridge.EF_UNIT_ID,)


def unit_leaf(unit: str, invented_only: bool) -> str:
    wire.require(unit in bridge.EF_LOCAL_BINDINGS and type(invented_only) is bool, "fixed EF unit lineage required")
    return unit + ("-invented-qualification-v1" if invented_only else "-actual1")


def receipt_name(unit: str) -> str:
    if unit == bridge.EF_UNIT_ID:
        return SNAPSHOT_RECEIPT
    if unit == bridge.EF_REPORT_UNIT:
        return COMPARISON_RECEIPT
    wire.require(unit in bridge.EF_FIT_UNITS, "fixed EF receipt unit required")
    return FIT_RECEIPT


def launch_dir(root: Path, unit: str, invented_only: bool) -> Path:
    parent = root / "launches" if unit == bridge.EF_UNIT_ID else root / "launches" / unit
    return parent / ("qualification-v1" if invented_only else "actual1")


def handoff_path(root: Path, unit: str, invented_only: bool) -> Path:
    return root / "handoffs" / (unit_leaf(unit, invented_only) + ".json")


@dataclass(frozen=True)
class AuditedArtifact:
    unit: str
    cache: Path
    publication_sha256: str
    audit_sha256: str
    receipt_sha256: str
    receipt: dict

    def pin(self) -> dict:
        return {"unit": self.unit, "publication_sha256": self.publication_sha256,
                "audit_sha256": self.audit_sha256, "receipt_sha256": self.receipt_sha256}


def audited_artifact(root: Path, unit: str, invented_only: bool) -> AuditedArtifact:
    """Read only original receipt metadata. Opening artifact bodies is separate."""
    leaf = unit_leaf(unit, invented_only)
    out, cache = root / "output" / leaf, root / "cache" / leaf
    wire.require(not os.path.lexists(out / "unresolved.json"), "unresolved EF provenance marker")
    audit_raw = bridge.bounded_read(launch_dir(root, unit, invented_only) / "original-receipt-audit.json", HANDOFF_LIMIT)
    pub_raw = bridge.bounded_read(out / "publication.json", HANDOFF_LIMIT)
    receipt_raw = bridge.bounded_read(out / receipt_name(unit), wire.MIB)
    audit, pub, receipt = map(bridge.canonical, (audit_raw, pub_raw, receipt_raw))
    audit_sha, pub_sha, receipt_sha = map(wire.digest, (audit_raw, pub_raw, receipt_raw))
    audit_fields = {"status", "invented_only", "publication_sha256", "source_manifest_sha256", "receipt_sha256",
                    "seal", "science_reexecuted", "auditor_source_sha256"}
    publication_fields = {"unit_id", "status", "invented_only", "full_bridge", "local_pipe_test", "execution_host",
                          "local_execution_root", "source_hashes", "output_sha256", "output_bytes",
                          "verification_complete", "independent_verification"}
    seal_fields = ({"fingerprint", "features_sha256", "labels_sha256"} if unit == bridge.EF_UNIT_ID else
                   {"artifact_sha256", "input_sha256"})
    wire.require(set(audit) == audit_fields and publication_fields <= pub.keys()
                 and {"seal", "independent_verification"} <= receipt.keys()
                 and type(receipt["seal"]) is dict and set(receipt["seal"]) == seal_fields
                 and all(wire.valid_sha(value) for value in receipt["seal"].values())
                 and type(pub["source_hashes"]) is dict and type(pub["independent_verification"]) is dict,
                 "EF upstream audit, scope or receipt differs")
    source_hashes = pub["source_hashes"]
    auditor = bridge.EF_RECEIPT_AUDITOR if unit == bridge.EF_UNIT_ID else bridge.EF_COMPARISON_AUDITOR
    wire.require(audit["status"] == "PASS" and audit["invented_only"] is invented_only
                 and audit["science_reexecuted"] is False and audit["publication_sha256"] == pub_sha
                 and audit["receipt_sha256"] == receipt_sha and audit["seal"] == receipt["seal"]
                 and wire.valid_sha(source_hashes.get(auditor)) and audit["auditor_source_sha256"] == source_hashes[auditor]
                 and audit["source_manifest_sha256"] == wire.digest(wire.encode(source_hashes))
                 and pub["unit_id"] == unit and pub["status"] == "completed" and pub["invented_only"] is invented_only
                 and pub["full_bridge"] is True and pub["local_pipe_test"] is False
                 and pub["execution_host"] == bridge.LOCAL_HOST and pub["local_execution_root"] == str(root)
                 and pub["output_sha256"] == receipt_sha and pub["output_bytes"] == len(receipt_raw)
                 and pub["verification_complete"] is True and pub["independent_verification"].get("verified") is True
                 and receipt["independent_verification"] == pub["independent_verification"],
                 "EF upstream audit, scope or receipt differs")
    return AuditedArtifact(unit, cache, pub_sha, audit_sha, receipt_sha, receipt)


def _pins(root: Path, unit: str, invented_only: bool) -> tuple[AuditedArtifact, ...]:
    artifacts = tuple(audited_artifact(root, name, invented_only) for name in _predecessors(unit))
    snapshot, *fits = artifacts
    for fit in fits:
        wire.require(fit.receipt["binding"]["snapshot_receipt_sha256"] == snapshot.receipt_sha256
                     and fit.receipt["binding"]["snapshot_seal"] == snapshot.receipt["seal"],
                     "EF fit belongs to a different snapshot")
    return artifacts


def make_handoff(root: Path, unit: str, invented_only: bool) -> bytes:
    """Create canonical metadata only. No source or model body is opened."""
    wire.require(not os.path.lexists(root / "canonical/ef-unresolved.json"), "unresolved EF campaign marker")
    return wire.encode({"schema": SCHEMA, "unit": unit, "invented_only": invented_only,
                        "predecessors": [item.pin() for item in _pins(root, unit, invented_only)]})


def resolve_handoff(raw: bytes, root: Path, unit: str, invented_only: bool) -> tuple[AuditedArtifact, ...]:
    """Recheck predecessor identity in the worker before opening artifact bodies."""
    wire.require(type(raw) is bytes and 0 < len(raw) <= HANDOFF_LIMIT, "bounded EF handoff required")
    artifacts = _pins(root, unit, invented_only)
    expected = wire.encode({"schema": SCHEMA, "unit": unit, "invented_only": invented_only,
                            "predecessors": [item.pin() for item in artifacts]})
    wire.require(raw == expected, "EF handoff changed or mixes invented and actual lineage")
    return artifacts
