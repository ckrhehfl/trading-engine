"""Invented receipt fixtures for S2 saved-only predecessor admission."""

import json
import os
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_saved_fixed_exit_predecessor as saved


def _write(path, content, *, mode=0o400):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(content)
    path.chmod(mode)
    return wire.digest(content)


def _journal_line(record):
    return (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode()


def _summary(cb, snapshot_size):
    counts = {"invented": 1}
    return {"schema": "ei-context-snapshot-independent-v1", "verified": True, "full_eof_verified": True,
            "all_original_coordinates_verified": True, "original_dates": 257, "original_coordinates": 5158,
            "reference_session_calls": 5158, "original_memberships": 10280, "universe_sha256": cb,
            "calendar_sha256": "a" * 64, "counts": counts, "counts_sha256": wire.digest(wire.encode(counts)),
            "feature_sha256": "b" * 64, "label_sha256": "c" * 64,
            "members": {"features": {"schema": "ei-context-feature-snapshot-draft-v1", "size": snapshot_size // 2,
                                      "records": 5158, "sha256": "b" * 64},
                        "labels": {"schema": "ei-context-path-labels-draft-v1", "size": snapshot_size // 2 - 1,
                                   "records": 5158, "sha256": "c" * 64}}}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "EI_SUPPORT_EXECUTION_ROOT", tmp_path)
    release = {"merge_commit": "1" * 40, "reviewed_commit": "2" * 40, "tree": "3" * 40,
               "registration_sha256": "4" * 64}
    hashes = {saved._SPEC: release["registration_sha256"], bridge.EI_ECONOMIC_RECEIPT_AUDITOR: "5" * 64}
    identity = {"merge_commit": release["merge_commit"], "tree": release["tree"],
                "source_manifest_sha256": wire.digest(wire.encode(hashes)), "runtime_sha256": "6" * 64,
                "machine_sha256": "7" * 64, "resource_profile": wire.EI_LOCAL_MODEL.id, "execution_host": bridge.LOCAL_HOST}
    attempts, metadata_paths, expected = {}, set(), {}
    modes = {"local_coordinator": True, "local_execution_root": str(tmp_path), "execution_host": bridge.LOCAL_HOST}
    for mode, attempt in (("qualification", "qualification-v1"), ("actual", "actual1")):
        invented = mode == "qualification"
        leaf = f"EI-ECONOMIC-S2-{'invented-' if invented else ''}{attempt}"
        cache, output, diagnostics = (tmp_path / key / leaf for key in ("cache", "output", "diagnostics"))
        journal = output / "invented-experiments.jsonl" if invented else tmp_path / "canonical" / "experiments.jsonl"
        launch = tmp_path / "launches" / "EI-ECONOMIC-S2" / attempt / "launch.json"
        common = {**modes, "unit_id": "EI-ECONOMIC-S2", "invented_only": invented,
                  "resource_profile": wire.EI_LOCAL_MODEL.id, "local_pipe_test": False}
        config = {**common, **{k: release[k] for k in ("merge_commit", "reviewed_commit", "tree")},
                  "attempt": attempt, "cache": str(cache), "output": str(output), "diagnostics": str(diagnostics),
                  "journal": str(journal), "automatic_retries": 0, "sessions": 257, "deadline_epoch": 10.0,
                  "wall_seconds": wire.EI_LOCAL_MODEL.wall_seconds, "source_hashes": hashes,
                  "local_identity": identity, "remote_identity": identity}
        if not invented:
            config["qualification"] = {"output": str(tmp_path / "output/EI-ECONOMIC-S2-invented-qualification-v1"),
                                       **{k: attempts["qualification"][k] for k in ("publication_sha256", "audit_sha256")}}
        launch_sha = _write(launch, wire.encode(config))
        manifest, paths = [], []
        for name, maximum in wire.EI_ECONOMIC_INPUT_BOUNDS:
            raw = b"invented input payload only"
            manifest.append({"name": name, "maximum": maximum, "size": len(raw), "sha256": wire.digest(raw),
                             "original_path": str(cache / "unopened-provenance" / name)})
            if name not in ("CC", "CX"):
                path = cache / "received" / name
                _write(path, raw)
                paths.append(path)
        decisions = []
        for fold, count in enumerate((640, 640, 620, 620, 620), 1):
            raw = f"invented decision {fold}".encode()
            path = cache / f"ei-entry-decisions-fold-{fold}.jsonl"
            decisions.append({"sha256": _write(path, raw), "size": len(raw), "coordinates": count})
            paths.append(path)
        ledger, raw = output / "ei-context-economic-ledger.jsonl", b"invented ledger"
        output_sha = _write(ledger, raw)
        paths.append(ledger)
        proof = {"schema": "EI-context-economic-comparison-v1", "verified": True, "full_eof_verified": True,
                 "full_geometry": True, "all_original_coordinates_verified": True, "invented_only": invented,
                 "scenario": "S2", "cost_bps": 33, "include_reentry": True, "folds": 5,
                 "original_evaluation_coordinates": 3140, "initial_model": "context_sequence", "event_level": True,
                 "account_return": False, "execution_policy_sha256": saved._POLICY, "saved_ledger_audit_complete": False,
                 "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
                 "entry_decision_seals": decisions, "training_source_sha256": "8" * 64}
        result = {"output_sha256": output_sha, "output_bytes": len(raw), "verification_complete": True,
                  "independent_verification": proof, "aggregate_budget": {"invented": True}, "automatic_retries": 0}
        run_id = "00000000-0000-0000-0000-000000000001"
        terminal = {"record_type": "discovery_trial", "study_id": "EI-ECONOMIC-S2", "run_id": run_id, "status": "completed",
                    "specification_sha256": release["registration_sha256"], "code_version": release["merge_commit"],
                    "promotion_allowed": False, "promotion_trial_increment": 0, "window": saved.WINDOW,
                    "parameters": {**modes, "invented_only": invented, "attempt": attempt,
                                   "source_manifest_sha256": identity["source_manifest_sha256"],
                                   "resource_profile": wire.EI_LOCAL_MODEL.id, "automatic_retries": 0,
                                   "input_contract": wire.EI_ECONOMIC_INPUT_CONTRACT}, "result": result}
        terminal_raw = _journal_line({**terminal, "logged_at": "1970-01-01T00:00:02+00:00"})
        started = {key: value for key, value in terminal.items() if key != "result"}
        span = _journal_line({**started, "status": "started", "logged_at": "1970-01-01T00:00:01+00:00"}) + terminal_raw
        prefix = b"" if invented else b"independent earlier journal event\n"
        _write(journal, prefix + span, mode=0o600)
        pub = {**common, **{k: v for k, v in result.items() if k != "automatic_retries"},
               "status": "completed", "full_bridge": True, "source_hashes": hashes,
               "local_identity": identity, "remote_identity": identity, "input_contract": wire.EI_ECONOMIC_INPUT_CONTRACT,
               "geometry": saved.FULL, "candidate_counts": {"S2": {"original": 3140}}, "run_id": run_id,
               "invented_journal": str(journal) if invented else None,
               "canonical_offsets": [len(prefix), len(prefix + span)], "terminal_record_sha256": wire.digest(terminal_raw)}
        publication = output / "publication.json"
        pub_sha = _write(publication, wire.encode(pub))
        audit = launch.parent / "original-receipt-audit.json"
        audit_sha = _write(audit, wire.encode({"schema": "eh-support-original-receipt-audit-v1", "status": "PASS",
            "invented_only": invented, "science_reexecuted": False, "unit_id": "EI-ECONOMIC-S2", "attempt": attempt,
            "publication_sha256": pub_sha, "output_sha256": output_sha, "source_manifest_sha256": identity["source_manifest_sha256"],
            "runtime_sha256": identity["runtime_sha256"], "merge_commit": release["merge_commit"], "tree": release["tree"],
            "auditor_source_sha256": hashes[bridge.EI_ECONOMIC_RECEIPT_AUDITOR]}))
        attempts[mode] = {"launch_sha256": launch_sha, "publication_sha256": pub_sha, "audit_sha256": audit_sha,
                          "snapshot_summary": _summary(manifest[0]["sha256"], manifest[3]["size"])}
        metadata_paths.update((launch, publication, audit, journal))
        expected[mode] = tuple(map(str, paths))
    return {"schema": saved.SCHEMA, "release": release, "attempts": attempts}, metadata_paths, expected


@pytest.mark.parametrize("invented", (True, False))
def test_accepts_fixed_roster_with_only_metadata_opens(fixture, tmp_path, monkeypatch, invented):
    pin, metadata, expected = fixture
    opened, real_open = set(), os.open

    def guarded_open(path, *args, **kwargs):
        candidate = Path(path)
        assert candidate in metadata, f"unexpected open: {candidate}"
        opened.add(candidate)
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", guarded_open)
        files, summary = saved.validate_saved_fixed_exit_predecessor(pin, tmp_path, invented_only=invented, checkpoint=lambda _: None)
    mode = "qualification" if invented else "actual"
    assert len(opened) == (4 if invented else 8)
    assert tuple(row.name for row in files) == tuple(name for name, _ in wire.EI_SAVED_FIXED_EXIT_INPUT_BOUNDS)
    assert tuple(row.original_path for row in files) == expected[mode]
    assert summary == pin["attempts"][mode]["snapshot_summary"]


def test_resealed_actual_launch_cannot_change_original_qualification(fixture, tmp_path):
    pin, _, _ = fixture
    path = tmp_path / "launches/EI-ECONOMIC-S2/actual1/launch.json"
    document = json.loads(path.read_bytes())
    document["qualification"]["audit_sha256"] = "f" * 64
    pin["attempts"]["actual"]["launch_sha256"] = _write(path, wire.encode(document))
    with pytest.raises(ValueError, match="original launch identity differs"):
        saved.validate_saved_fixed_exit_predecessor(pin, tmp_path, invented_only=False, checkpoint=lambda _: None)


def test_wrong_payload_mode_refuses_even_when_metadata_matches(fixture, tmp_path):
    pin, _, expected = fixture
    Path(expected["actual"][-1]).chmod(0o600)
    with pytest.raises(ValueError, match="payload size or mode differs"):
        saved.validate_saved_fixed_exit_predecessor(pin, tmp_path, invented_only=False, checkpoint=lambda _: None)


def test_same_length_changed_start_cannot_keep_original_completion_authority(fixture, tmp_path):
    pin, _, _ = fixture
    path = tmp_path / "canonical" / "experiments.jsonl"
    prefix, started, terminal = path.read_bytes().splitlines(keepends=True)
    record = json.loads(started)
    record["code_version"] = "f" * 40
    wrong_start = _journal_line(record)
    assert len(started) == len(wrong_start)
    _write(path, prefix + wrong_start + terminal, mode=0o600)
    with pytest.raises(ValueError, match="original durable journal record differs"):
        saved.validate_saved_fixed_exit_predecessor(pin, tmp_path, invented_only=False, checkpoint=lambda _: None)
