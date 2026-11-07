"""Receipt-only EH snapshot handoff on invented lifecycle metadata."""

from dataclasses import replace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_scenario_forecast_handoff import SnapshotPredecessor, validate_predecessor


GEOMETRY = {"sessions": 257}
WINDOW = {"start": "2030-01-01", "end": "2031-01-31"}
PROFILE = wire.ORDINARY300.id


def invent(root, invented):
    unit = bridge.EH_SNAPSHOT_UNIT_ID
    attempt = "qualification-v1" if invented else "actual1"
    out = root / "output" / (unit + ("-invented-" if invented else "-") + attempt)
    receipts = root / "launches" / unit / attempt
    journal = out / "invented-experiments.jsonl" if invented else root / "canonical" / "experiments.jsonl"
    for directory in (out, receipts, journal.parent):
        directory.mkdir(parents=True, exist_ok=True)
    sources = {bridge.EH_SNAPSHOT_SPECIFICATION_PATH: "1" * 64, bridge.EH_SNAPSHOT_RECEIPT_AUDITOR: "2" * 64}
    source_hash = wire.digest(wire.encode(sources))
    identity = {"merge_commit": "3" * 40, "tree": "4" * 40, "resource_profile": PROFILE, "runtime_sha256": "5" * 64,
                "execution_host": bridge.LOCAL_HOST, "source_manifest_sha256": source_hash}
    members = {"features": {"sha256": "6" * 64}, "labels": {"sha256": "7" * 64}}
    proof = {"schema": bridge.EH_SNAPSHOT_OUTPUT_SCHEMA, "verified": True, "all_original_coordinates_verified": True,
             "full_eof_verified": True, "full_geometry": True, "snapshot_summary": {"members": members}}
    result = {"output_sha256": "8" * 64, "output_bytes": 123, "verification_complete": True,
              "independent_verification": proof}
    terminal = {"status": "completed", "study_id": unit, "code_version": identity["merge_commit"], "window": WINDOW,
                "specification_sha256": sources[bridge.EH_SNAPSHOT_SPECIFICATION_PATH], "run_id": "invented-snapshot",
                "parameters": {"invented_only": invented, "attempt": attempt, "source_manifest_sha256": source_hash},
                "result": result}
    start_line = wire.encode({"status": "started", "run_id": terminal["run_id"]})
    terminal_line = wire.encode(terminal)
    journal.write_bytes(start_line + terminal_line + wire.encode({"status": "started", "run_id": "future-invented"}))
    publication = {"status": "completed", "unit_id": unit, "invented_only": invented, "full_bridge": True,
                   "local_pipe_test": False, "resource_profile": PROFILE, "input_contract": wire.EH_SUPPORT_INPUT_CONTRACT,
                   "geometry": GEOMETRY, "local_identity": identity, "remote_identity": identity, "source_hashes": sources,
                   "invented_journal": str(journal) if invented else None, "run_id": terminal["run_id"],
                   "canonical_offsets": [0, len(start_line + terminal_line)],
                   "terminal_record_sha256": wire.digest(terminal_line), **result}
    pub_raw = wire.encode(publication)
    (out / "publication.json").write_bytes(pub_raw)
    audit = {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": invented,
             "science_reexecuted": False, "unit_id": unit, "attempt": attempt, "publication_sha256": wire.digest(pub_raw),
             "source_manifest_sha256": source_hash, "output_sha256": result["output_sha256"],
             "merge_commit": identity["merge_commit"], "tree": identity["tree"], "runtime_sha256": identity["runtime_sha256"],
             "auditor_source_sha256": sources[bridge.EH_SNAPSHOT_RECEIPT_AUDITOR]}
    audit_raw = wire.encode(audit)
    (receipts / "original-receipt-audit.json").write_bytes(audit_raw)
    pin = SnapshotPredecessor(invented, wire.digest(pub_raw), wire.digest(audit_raw), result["output_sha256"],
                              result["output_bytes"], identity["merge_commit"], identity["tree"], source_hash,
                              identity["runtime_sha256"])
    return pin, out, journal, members


@pytest.mark.parametrize("invented", (True, False))
def test_predecessor_metadata_does_not_open_bundle(tmp_path, invented):
    pin, output, _, members = invent(tmp_path, invented)
    artifact, sealed = validate_predecessor(tmp_path, pin, geometry=GEOMETRY, window=WINDOW)
    assert artifact == output / bridge.EH_SNAPSHOT_OUTPUT_FILENAME and not artifact.exists()
    assert sealed == members
    with pytest.raises(ValueError, match="reviewed source identity differs"):
        validate_predecessor(tmp_path, replace(pin, merge_commit="a" * 40), geometry=GEOMETRY, window=WINDOW)


def test_predecessor_rejects_different_completed_span(tmp_path):
    pin, _, journal, _ = invent(tmp_path, False)
    journal.write_bytes(journal.read_bytes().replace(b'"2030-01-01"', b'"2030-01-02"'))
    with pytest.raises(ValueError, match="terminal span differs"):
        validate_predecessor(tmp_path, pin, geometry=GEOMETRY, window=WINDOW)
