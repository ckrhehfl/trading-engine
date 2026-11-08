"""The same-release retry gate authenticates controls without reading ledgers."""

from hashlib import sha1
import json
import os
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_scenario_management_recovery as recovery


def _put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return wire.digest(raw)


def _line(row):
    return (json.dumps(row, sort_keys=True) + "\n").encode()


def _scope(root, unit):
    leaf = f"{unit}-actual1"
    return {"unit_id": unit, "attempt": "actual1", "invented_only": False, "local_coordinator": True,
            "local_execution_root": str(root), "execution_host": "local_wsl_only",
            "resource_profile": "ec-ordinary300-v1", "automatic_retries": 0,
            "journal": str(root / "canonical" / "experiments.jsonl"),
            **{key: str(root / key / leaf) for key in ("cache", "output", "diagnostics")}}


def test_original_failure_is_authenticated_and_preserved_without_reading_worker_ledger(tmp_path):
    scope = _scope(tmp_path, recovery.ORIGINAL_UNIT)
    launch = tmp_path / "launches" / recovery.ORIGINAL_UNIT / "actual1" / "launch.json"
    marker = tmp_path / "canonical" / recovery.ORIGINAL_MARKER
    failure = Path(scope["diagnostics"]) / "process-failure.json"
    common = {"record_type": "discovery_trial", "study_id": recovery.ORIGINAL_UNIT, "run_id": "run",
              "code_version": "a" * 40, "promotion_allowed": False}
    started = _line({**common, "status": "started", "logged_at": "first"})
    failed = _line({**common, "status": "failed", "logged_at": "second", "error_type": "EndOfFrames"})
    _put(Path(scope["journal"]), started + failed)
    ledger = Path(scope["cache"]) / "eh-management-ledger.jsonl"
    _put(ledger, b"unverified")
    ledger.chmod(0o400)
    pin = {"run_id": "run", "merge_commit": common["code_version"], "ledger_bytes": len(b"unverified"),
           "launch_sha256": _put(launch, wire.encode({**scope, "merge_commit": common["code_version"]})),
           "marker_sha256": _put(marker, wire.encode({"attempt": "actual1", "state": "unresolved", "automatic_retries": 0})),
           "process_failure_sha256": _put(failure, wire.encode({"status": "unknown", "automatic_retries": 0,
                                                               "children": {"worker": {"returncode": 1}}})),
           "started": {"offsets": [0, len(started)], "sha256": wire.digest(started)},
           "failed": {"offsets": [len(started), len(started + failed)], "sha256": wire.digest(failed)}}
    assert recovery.verify_failed_attempt(tmp_path, pin) == len(started + failed)
    assert wire.digest(marker.read_bytes()) == pin["marker_sha256"]
    with pytest.raises(ValueError, match="ledger metadata differs"):
        recovery.verify_failed_attempt(tmp_path, {**pin, "ledger_bytes": 1})
    _put(Path(scope["output"]) / "publication.json", b"{}")
    with pytest.raises(ValueError, match="must remain unpublished"):
        recovery.verify_failed_attempt(tmp_path, pin)
    _put(marker, wire.encode({}))
    with pytest.raises(ValueError, match="original marker differs"):
        recovery.verify_failed_attempt(tmp_path, pin)


def test_followup_requires_same_release_actual_audit_and_canonical_completion(tmp_path):
    scope = _scope(tmp_path, recovery.RECOVERY_UNIT)
    directory = tmp_path / "launches" / recovery.RECOVERY_UNIT / "actual1"
    release = {"merge_commit": "a" * 40, "reviewed_commit": "b" * 40, "tree": "c" * 40}
    hashes = {recovery.RECOVERY_SPEC: "1" * 64, recovery.RECOVERY_AUDITOR: "2" * 64}
    identity = {"runtime_sha256": "3" * 64, "machine_sha256": "4" * 64}
    config = {**release, "local_identity": {**identity, "source_manifest_sha256": "5" * 64}}
    manifest_sha = wire.digest(wire.encode(hashes))
    identity = {**identity, "source_manifest_sha256": manifest_sha}
    launch = {**scope, **release, "source_hashes": hashes, "local_identity": identity, "remote_identity": identity,
              "sessions": 257}
    launch_path = directory / "launch.json"
    launch_sha = _put(launch_path, wire.encode(launch))
    process_path = Path(scope["diagnostics"]) / "process-receipt.json"
    process = wire.encode({"status": "completed_ack_received"})
    process_sha = _put(process_path, process)

    def verify(value=config, after=0):
        return recovery.verify_audited_predecessor(tmp_path, value, hashes, after,
            unit=recovery.RECOVERY_UNIT, specification=recovery.RECOVERY_SPEC)

    common = {"study_id": recovery.RECOVERY_UNIT, "run_id": "recovery", "code_version": release["merge_commit"],
              "specification_sha256": hashes[recovery.RECOVERY_SPEC], "promotion_allowed": False}
    output_sha = "6" * 64
    start = _line({**common, "status": "started", "logged_at": "before"})
    done = _line({**common, "status": "completed", "result": {"output_sha256": output_sha}})
    end = len(start + done)
    _put(Path(scope["journal"]), start + done)
    pub = {"status": "completed", "unit_id": recovery.RECOVERY_UNIT, "invented_only": False, "full_bridge": True,
           "local_pipe_test": False, "verification_complete": True, "input_contract": wire.EH_MANAGEMENT_INPUT_CONTRACT,
           "resource_profile": wire.ORDINARY300.id, "source_hashes": hashes, "local_identity": identity,
           "remote_identity": identity, "independent_verification": {"verified": True}, "output_sha256": output_sha,
           "canonical_offsets": [0, end], "terminal_record_sha256": wire.digest(done), "run_id": common["run_id"]}
    pub_sha = _put(Path(scope["output"]) / "publication.json", wire.encode(pub))
    with pytest.raises(FileNotFoundError):
        verify()
    audit = {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": False,
             "science_reexecuted": False, "unit_id": recovery.RECOVERY_UNIT, "attempt": "actual1",
             "canonical_span_sha256": wire.digest(start + done),
             "launch_sha256": launch_sha, "process_receipt_sha256": process_sha,
             "publication_sha256": pub_sha, "output_sha256": output_sha, "source_manifest_sha256": manifest_sha,
             "merge_commit": release["merge_commit"], "tree": release["tree"],
             "runtime_sha256": identity["runtime_sha256"], "auditor_source_sha256": hashes[recovery.RECOVERY_AUDITOR]}
    audit_path = directory / "original-receipt-audit.json"
    _put(audit_path, wire.encode({**audit, "invented_only": True, "attempt": "qualification-v1"}))
    with pytest.raises(ValueError, match="independent receipt differs"):
        verify()
    _put(audit_path, wire.encode(audit))
    assert verify() == end
    with pytest.raises(ValueError, match="journal precedes"):
        verify(after=1)
    with pytest.raises(ValueError, match="release differs"):
        verify({**config, "merge_commit": "f" * 40})
    _put(launch_path, wire.encode({**launch, "sessions": 256}))
    with pytest.raises(ValueError, match="independent receipt differs"):
        verify()
    _put(launch_path, wire.encode(launch))
    process_path.unlink()
    with pytest.raises(FileNotFoundError):
        verify()
    _put(process_path, process)
    _put(Path(scope["journal"]), start.replace(b"before", b"beside") + done)
    with pytest.raises(ValueError, match="audited journal span differs"):
        verify()
    _put(Path(scope["journal"]), start + done.replace(b"completed", b"no_result"))
    with pytest.raises(ValueError, match="audited journal span differs"):
        verify()


def test_same_release_hashes_replaces_only_the_profile_registration(tmp_path):
    raw = b"{}\n"
    digest = _put(tmp_path / recovery.RECOVERY_SPEC, raw)
    blob = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
    common = {"python/research/worker.py": "a" * 64}
    current = {**common, "followup.json": "b" * 64}
    assert recovery.same_release_sources(tmp_path, current, "followup.json", recovery.RECOVERY_SPEC, "revision", lambda *_: blob) == {
        **common, recovery.RECOVERY_SPEC: digest}
    with pytest.raises(ValueError, match="reviewed release"):
        recovery.same_release_sources(tmp_path, current, "followup.json", recovery.RECOVERY_SPEC, "revision", lambda *_: "f" * 40)


def test_admission_checks_the_full_fixed_audited_prefix(tmp_path, monkeypatch):
    pin_digest = _put(tmp_path / recovery.FAILURE_PIN, wire.encode({}))
    config = {"unit_id": recovery.SEQUENCE[-1], "source_hashes": {recovery.FAILURE_PIN: pin_digest}, "merge_commit": "r"}
    monkeypatch.setattr(recovery, "verify_failed_attempt", lambda *_: 10)
    monkeypatch.setattr(recovery, "same_release_sources", lambda *args: {})
    observed = []

    def proof(root, current, hashes, end, *, unit, specification):
        assert root == tmp_path and current is config
        observed.append((unit, end))
        if unit == recovery.SEQUENCE[1]:
            raise FileNotFoundError("missing predecessor audit")
        return end + 10

    monkeypatch.setattr(recovery, "verify_audited_predecessor", proof)
    with pytest.raises(FileNotFoundError, match="missing predecessor audit"):
        recovery.verify_admission(tmp_path, tmp_path, "followup.json", config, lambda *_: "")
    assert observed == list(zip(recovery.SEQUENCE[:2], (10, 20), strict=True))
    observed.clear()
    replaced_launch = tmp_path / "launches" / recovery.SPENT_QUALIFICATION_UNIT / "actual1"
    replaced_launch.mkdir(parents=True)
    with pytest.raises(ValueError, match="must have no actual launch"):
        recovery.verify_admission(tmp_path, tmp_path, "followup.json", config, lambda *_: "")
    assert observed == []


@pytest.mark.skipif(os.name != "posix", reason="WSL flock required")
def test_predecessor_refusal_does_not_spend_shared_marker(tmp_path):
    lock = tmp_path / "research.lock"
    marker = tmp_path / recovery.RECOVERY_MARKER

    def refuse():
        assert not marker.exists()
        with pytest.raises(BlockingIOError):
            with wire.research_locks([lock], tmp_path / "other.json", "actual1"):
                pytest.fail("the original lock must already be held")
        raise ValueError("missing predecessor")

    with pytest.raises(ValueError, match="missing predecessor"):
        with wire.research_locks([lock], marker, "actual1", before_marker=refuse):
            pytest.fail("refusal must happen before STARTED")
    assert not marker.exists()
    with wire.research_locks([lock], marker, "actual1"):
        assert marker.is_file()
