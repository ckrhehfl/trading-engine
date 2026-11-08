"""Invented lifecycle receipts for the EI forecast input handoff."""

import builtins
import json
import os
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_predecessor as handoff
from research import intraday_context_snapshot_unit as snapshot


def _invent(root, monkeypatch, mutate=lambda key, row: None):
    monkeypatch.setattr(handoff, "NATIVE", root)
    paths = {}

    def save(key, path, value, *, raw=False, sealed=False):
        mutate(key, value)
        data = value if raw else wire.encode(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if sealed:
            path.chmod(0o400)
        paths[key] = path
        return wire.digest(data)

    binding = handoff._BINDING
    profile = wire.EI_LOCAL_MODEL
    sources = {binding.specification: "1" * 64, binding.runtime: "2" * 64,
               binding.auditor: "3" * 64, snapshot.ORIGINAL_SPEC: snapshot.ORIGINAL_PIN}
    pin = {"schema": handoff.SCHEMA, "unit_id": snapshot.UNIT_ID, "registration": binding.specification,
           "qualification_attempt": binding.qualification_attempt, "actual_attempt": "actual1",
           "merge_commit": "a" * 40, "reviewed_commit": "b" * 40, "tree": "c" * 40,
           "registration_sha256": sources[binding.specification]}
    identity = {"source_manifest_sha256": wire.digest(wire.encode(sources)), "runtime_sha256": "4" * 64,
                "machine_sha256": "5" * 64, "merge_commit": pin["merge_commit"], "tree": pin["tree"],
                "execution_host": bridge.LOCAL_HOST, "resource_profile": profile.id}
    modes = {"local_coordinator": True, "local_execution_root": str(root), "execution_host": bridge.LOCAL_HOST}
    actual_cb = b"Invented bytes standing in for the original CB roster"
    original = tuple(bridge.InputPin(name, f"/invented/raw/{name}",
                     wire.digest(actual_cb if name == "CB" else name.encode()), maximum)
                     for name, maximum in wire.EI_SUPPORT_INPUT_BOUNDS)
    for mode, attempt in (("qualification", binding.qualification_attempt), ("actual", "actual1")):
        invented = mode == "qualification"
        leaf = snapshot.UNIT_ID + ("-invented-" if invented else "-") + attempt
        cache, out, diagnostics = (root / key / leaf for key in ("cache", "output", "diagnostics"))
        directory = root / "launches" / snapshot.UNIT_ID / attempt
        journal = out / "invented-experiments.jsonl" if invented else root / "canonical" / "experiments.jsonl"
        launch = {**modes, "unit_id": snapshot.UNIT_ID, "attempt": attempt, "invented_only": invented,
                  "local_pipe_test": False, "sessions": snapshot.FULL["sessions"], "wall_seconds": profile.wall_seconds,
                  "automatic_retries": 0, "resource_profile": profile.id, "cache": str(cache), "output": str(out),
                  "diagnostics": str(diagnostics), "journal": str(journal), "deadline_epoch": 1924995600,
                  "source_hashes": dict(sources), "local_identity": dict(identity), "remote_identity": dict(identity),
                  **{key: pin[key] for key in ("merge_commit", "reviewed_commit", "tree")}}
        if not invented:
            prior = pin["qualification"]
            launch["qualification"] = {"output": str(paths["qualification.publication"].parent),
                "publication_sha256": prior["publication_sha256"], "audit_sha256": prior["audit_sha256"]}
        launch_sha = save(mode + ".launch", directory / "launch.json", launch)
        save(mode + ".launch_identity", directory / "launch-identity.json", {"launch_sha256": launch_sha,
             "runtime_sha256": sources[binding.runtime], "merge": pin["merge_commit"], "reviewed": pin["reviewed_commit"],
             "tree": pin["tree"], "mode": mode, "invented_only": invented, "qualifies_actual_by_this_record": False})

        cb = b"Distinct invented qualification CB" if invented else actual_cb
        cb_sha = save(mode + ".cb", cache / "received" / "CB", cb, raw=True, sealed=True)
        manifest = [item.metadata(len(cb) if item.name == "CB" else len(item.name)) for item in original]
        if invented:
            for item in manifest:
                item["original_path"] = str(cache / "generated" / item["name"])
            manifest[0]["sha256"] = cb_sha
        mutate(mode + ".manifest", manifest)
        bundle = b"Invented sealed bundle, body must stay unread during handoff"
        bundle_sha = save(mode + ".bundle", out / snapshot.OUTPUT, bundle, raw=True, sealed=True)
        members = {name: {"schema": schema, "size": 10, "records": snapshot.FULL["union_coordinates"], "sha256": sha}
                   for name, schema, sha in (("features", handoff.FEATURE_SCHEMA, "6" * 64),
                                             ("labels", handoff.LABEL_SCHEMA, "7" * 64))}
        counts = {"invented_unit_test_counts": True}
        summary = {"schema": handoff.SUMMARY_SCHEMA, "verified": True, "full_eof_verified": True,
                   "all_original_coordinates_verified": True, "original_dates": snapshot.FULL["sessions"],
                   "original_coordinates": snapshot.FULL["union_coordinates"], "original_memberships": 2 * snapshot.FULL["targets_per_scenario"],
                   "reference_session_calls": snapshot.FULL["union_coordinates"], "universe_sha256": manifest[0]["sha256"],
                   "calendar_sha256": "8" * 64, "feature_sha256": members["features"]["sha256"], "label_sha256": members["labels"]["sha256"],
                   "counts": counts, "counts_sha256": wire.digest(wire.encode(counts)), "members": members}
        mutate(mode + ".summary", summary)
        proof = {"schema": snapshot.SCHEMA, "verified": True, "full_eof_verified": True,
                 "all_original_coordinates_verified": True, "invented_only": invented, "full_geometry": True,
                 "historical_availability_certified": False, "stored_volume_units_certified": False,
                 "contains_model_fits_or_returns": False, "input_manifest": manifest,
                 "input_manifest_sha256": wire.digest(wire.encode(manifest)), "raw_byte_audit": {},
                 "snapshot_summary": summary, "summary_sha256": wire.digest(wire.encode(summary)), "support_totals": counts}
        run_id = "00000000-0000-0000-0000-00000000000" + ("1" if invented else "2")
        result = {"output_sha256": bundle_sha, "output_bytes": len(bundle), "verification_complete": True,
                  "independent_verification": proof, "aggregate_budget": {"invented_prior_audit_scope": True}}
        common = {"record_type": "discovery_trial", "study_id": snapshot.UNIT_ID, "run_id": run_id,
                  "specification_sha256": pin["registration_sha256"], "window": snapshot.WINDOW,
                  "code_version": pin["merge_commit"], "promotion_allowed": False, "promotion_trial_increment": 0,
                  "parameters": {**modes, "invented_only": invented, "attempt": attempt,
                  "source_manifest_sha256": identity["source_manifest_sha256"], "resource_profile": profile.id,
                  "automatic_retries": 0, "input_contract": binding.input_contract}}
        started = {**common, "status": "started", "logged_at": "2031-01-01T00:00:00+00:00"}
        mutate(mode + ".started", started)
        completed = {**common, "status": "completed", "logged_at": "2031-01-01T00:00:10+00:00",
                     "result": {**result, "automatic_retries": 0}}
        lines = [(json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode() for row in (started, completed)]
        prior = b"" if invented else b'{"preserved":"earlier unrelated run"}\n'
        span = [len(prior), len(prior) + sum(map(len, lines))]
        save(mode + ".journal", journal, prior + b"".join(lines) + prior, raw=True)
        terminal_sha = wire.digest(lines[-1])
        publication = {**modes, "status": "completed", "unit_id": snapshot.UNIT_ID, "resource_profile": profile.id,
                       "invented_only": invented, "full_bridge": True, "local_pipe_test": False, "input_contract": binding.input_contract,
                       "invented_journal": str(journal) if invented else None, "source_hashes": sources, "local_identity": identity,
                       "remote_identity": identity, "geometry": snapshot.FULL, "candidate_counts": snapshot.COUNTS,
                       "run_id": run_id, "canonical_offsets": span, "terminal_record_sha256": terminal_sha,
                       "coordinator_after_terminal": {}, **result}
        publication_sha = save(mode + ".publication", out / "publication.json", publication)
        audit = {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": invented,
                 "science_reexecuted": False, "unit_id": snapshot.UNIT_ID, "attempt": attempt,
                 "publication_sha256": publication_sha, "source_manifest_sha256": identity["source_manifest_sha256"],
                 "output_sha256": bundle_sha, "merge_commit": pin["merge_commit"], "tree": pin["tree"],
                 "runtime_sha256": identity["runtime_sha256"], "auditor_source_sha256": sources[binding.auditor]}
        audit_sha = save(mode + ".audit", directory / "original-receipt-audit.json", audit)
        process = {**modes, "status": "completed_ack_received", "canonical_truth": "local WSL journal only",
                   "automatic_retries": 0, "controller": {}, "children": {role: {"returncode": 0} for role in ("worker", "coordinator")},
                   "terminal": {"kind": "COMPLETED", "status": "completed", "run_id": run_id, "output_sha256": bundle_sha,
                                "record_sha256": terminal_sha, "publication_sha256": publication_sha}}
        process_sha = save(mode + ".process", diagnostics / "process-receipt.json", process)
        pin[mode] = {"launch_sha256": launch_sha, "publication_sha256": publication_sha, "audit_sha256": audit_sha,
                     "output_sha256": bundle_sha, "output_bytes": len(bundle), "run_id": run_id, "canonical_offsets": span,
                     "terminal_record_sha256": terminal_sha, "source_manifest_sha256": identity["source_manifest_sha256"],
                     "runtime_sha256": identity["runtime_sha256"], "auditor_source_sha256": sources[binding.auditor],
                     "process_receipt_sha256": process_sha}
    return pin, original, paths


def _repair(pin, paths, mutate=lambda mode, receipt: None):
    repair = {"registration": handoff.REPAIR_REGISTRATION,
              "repair_release": {"merge_commit": "d" * 40, "reviewed_commit": "e" * 40, "tree": "f" * 40},
              "repair_registration_sha256": "9" * 64, "repair_runner_sha256": "a" * 64,
              "repair_header_sha256": "b" * 64,
              "original_audit_failure": {"reference": "efd7df", "status": "failed", "sha256": "c" * 64}}
    for mode in ("qualification", "actual"):
        original = paths[mode + ".audit"]
        receipt = {**json.loads(original.read_bytes()), "schema": "ei-snapshot-supplemental-saved-receipt-audit-v2",
                   "original_launch_sha256": pin[mode]["launch_sha256"], "repair_release": dict(repair["repair_release"]),
                   **{key: repair[key] for key in ("repair_registration_sha256", "repair_runner_sha256", "repair_header_sha256")},
                   "original_audit_failure": dict(repair["original_audit_failure"]) if mode == "actual" else None,
                   "qualification_supplement_sha256": repair.get("qualification_supplement_sha256")}
        mutate(mode, receipt)
        raw = wire.encode(receipt)
        paths[mode + ".supplement"] = original.with_name("supplemental-saved-receipt-audit-v2.json")
        paths[mode + ".supplement"].write_bytes(raw)
        if mode == "qualification":
            repair["qualification_supplement_sha256"] = wire.digest(raw)
        else:
            original.unlink()
            pin["actual"]["audit_sha256"] = wire.digest(raw)
    pin["actual_saved_audit_repair"] = repair


def test_reads_receipts_without_opening_either_input_payload(tmp_path, monkeypatch):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    protected = {path for name, path in paths.items() if name.endswith((".cb", ".bundle"))}
    protected.update(Path(item.original_path) for item in original)
    old_failure = tmp_path / "canonical" / bridge.EI_SUPPORT_MARKER
    old_failure.write_bytes(b"Other unit's marker")
    def exclude_payload(open_file):
        def observed(path, *args, **kwargs):
            assert isinstance(path, int) or Path(path) not in protected
            return open_file(path, *args, **kwargs)

        return observed

    monkeypatch.setattr(os, "open", exclude_payload(os.open))
    monkeypatch.setattr(Path, "open", exclude_payload(Path.open))
    monkeypatch.setattr(builtins, "open", exclude_payload(builtins.open))
    modes = handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)
    assert set(modes) == {"actual", "qualification"}
    for mode, bound in modes.items():
        assert bound.invented_only is (mode == "qualification")
        cb, bundle = bound.inputs
        assert cb.name == "CB" and cb.original_path == str(paths[mode + ".cb"])
        assert cb.sha256 == bound.summary["universe_sha256"]
        assert bundle == bridge.InputFile("EI-SNAPSHOT", str(paths[mode + ".bundle"]), pin[mode]["output_sha256"],
                                          snapshot.MAX_OUTPUT, pin[mode]["output_bytes"])
        assert bound.members["features"]["sha256"] == bound.summary["feature_sha256"]
        assert bound.audit_sha256 == pin[mode]["audit_sha256"]
    assert modes["actual"].inputs[0].sha256 == original[0].sha256
    assert modes["qualification"].inputs[0].sha256 != original[0].sha256
    assert old_failure.read_bytes() == b"Other unit's marker"


@pytest.mark.parametrize(("phase", "mutate", "message"), [
    ("actual.launch", lambda row: row.update(resource_profile=wire.ORDINARY300.id), "launch identity"),
    ("actual.launch", lambda row: row["qualification"].update(audit_sha256="0" * 64), "launch identity"),
    ("qualification.manifest", lambda row: row[0].update(original_path="/invented/alternate/CB"), "provenance"),
    ("actual.manifest", lambda row: row[0].update(sha256="0" * 64), "original registration"),
    ("actual.summary", lambda row: row.update(universe_sha256="0" * 64), "original CB"),
    ("actual.summary", lambda row: row["members"]["features"].update(records=True), "member seal"),
    ("actual.started", lambda row: row.update(promotion_allowed=True), "journal identity"),
    ("actual.audit", lambda row: row.update(invented_only=True), "original receipt audit"),
    ("actual.process", lambda row: row["children"]["worker"].update(returncode=1), "child exits"),
])
def test_rejects_consistently_rehashed_metadata_mutations(tmp_path, monkeypatch, phase, mutate, message):
    pin, original, _ = _invent(tmp_path, monkeypatch, lambda key, row: mutate(row) if key == phase else None)
    with pytest.raises(ValueError, match=message):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)


def test_rejects_changed_seal_and_inflight_payload_replacement(tmp_path, monkeypatch):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    path = paths["actual.cb"]
    path.chmod(0o600)
    with pytest.raises(ValueError, match="size or mode"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)
    path.chmod(0o400)

    def alter(stage):
        if stage == "EI_snapshot_predecessor_complete":
            path.chmod(0o600)

    with pytest.raises(ValueError, match="evidence changed"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, alter)


def test_refuses_original_snapshot_failure_marker_before_reads(tmp_path, monkeypatch):
    pin, original, _ = _invent(tmp_path, monkeypatch)
    marker = tmp_path / "canonical" / bridge.EI_SNAPSHOT_MARKER
    marker.write_bytes(b"Invented unresolved snapshot")
    with pytest.raises(ValueError, match="unresolved or failed marker"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: pytest.fail("metadata read before marker check"))


def test_repaired_actual_preserves_original_qualification_and_failed_slot(tmp_path, monkeypatch):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    originals = {name: paths[name].read_bytes() for name in ("qualification.audit", "actual.publication", "actual.journal")}
    _repair(pin, paths)
    results = handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)
    assert results["qualification"].audit_sha256 == wire.digest(originals["qualification.audit"])
    assert results["actual"].audit_sha256 == wire.digest(paths["actual.supplement"].read_bytes())
    assert results["actual"].summary["universe_sha256"] == original[0].sha256
    assert not paths["actual.audit"].exists()
    assert all(paths[name].read_bytes() == raw for name, raw in originals.items())


@pytest.mark.parametrize(("phase", "mutate"), [
    ("qualification", lambda row: row.update(qualification_supplement_sha256="0" * 64)),
    ("qualification", lambda row: row.update(repair_registration_sha256="0" * 64)),
    ("actual", lambda row: row["repair_release"].update(tree="0" * 40)),
    ("actual", lambda row: row.update(qualification_supplement_sha256="0" * 64)),
    ("actual", lambda row: row.update(original_launch_sha256="0" * 64)),
    ("actual", lambda row: row["original_audit_failure"].update(status="PASS")),
    ("actual", lambda row: row.update(science_reexecuted=True)),
    ("actual", lambda row: row.update(unregistered_field=False)),
])
def test_repair_refuses_consistently_rehashed_supplement_tampering(tmp_path, monkeypatch, phase, mutate):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    _repair(pin, paths, lambda key, receipt: mutate(receipt) if key == phase else None)
    with pytest.raises(ValueError, match="supplemental saved receipt audit differs"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)


def test_repair_still_requires_original_qualification_pin(tmp_path, monkeypatch):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    _repair(pin, paths)
    paths["qualification.audit"].write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="pinned snapshot metadata bytes differ"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)


def test_repair_refuses_original_actual_pass_slot_at_either_checkpoint(tmp_path, monkeypatch):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    original_actual = paths["actual.audit"].read_bytes()
    _repair(pin, paths)

    def add_original_slot(stage):
        if stage == "EI_snapshot_predecessor_complete":
            paths["actual.audit"].write_bytes(original_actual)

    with pytest.raises(ValueError, match="unresolved or failed marker"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, add_original_slot)
    with pytest.raises(ValueError, match="unresolved or failed marker"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: pytest.fail("read before original slot check"))


def test_repair_requires_fixed_registration_and_explicit_shape(tmp_path, monkeypatch):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    _repair(pin, paths)
    pin["actual_saved_audit_repair"]["registration"] = "invented-alternate.json"
    with pytest.raises(ValueError, match="repair registration"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)
    pin["actual_saved_audit_repair"] = None
    with pytest.raises(ValueError, match="repair pin fields"):
        handoff.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)
