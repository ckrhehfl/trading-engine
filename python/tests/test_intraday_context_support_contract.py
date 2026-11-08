"""Invented metadata tests for the fixed local EI support lifecycle."""

from copy import deepcopy
from dataclasses import replace
import time

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


GEOMETRY = {"sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
            "cc_chunks": 5158, "cc_rows": 1002211, "cx_chunks": 5158, "cx_rows": 1002211,
            "cx_daily_rows": 2674}


@pytest.fixture
def plan(tmp_path, monkeypatch):
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    monkeypatch.setattr(bridge, "EI_SUPPORT_EXECUTION_ROOT", native)
    binding = bridge._EH_BINDINGS[bridge.EI_SUPPORT_UNIT_ID]
    names = tuple(sorted({binding.specification, binding.runtime, binding.auditor,
        *binding.private_dependencies, bridge.EE_SPECIFICATION_PATH,
        "python/research/intraday_campaign_wsl_transport.py",
        "python/research/intraday_campaign_wsl_bridge.py",
        "python/research/experiment_log.py", "python/research/research_budget.py"}))
    leaf = bridge.EI_SUPPORT_UNIT_ID + "-actual1"
    return bridge.UnitPlan(unit_id=bridge.EI_SUPPORT_UNIT_ID, specification_path=binding.specification,
        source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names))),
        input_pins=tuple(bridge.InputPin(name, "/invented/native/" + name, "a" * 64, maximum)
                         for name, maximum in wire.EI_SUPPORT_INPUT_BOUNDS),
        decoded_stream_sha256="b" * 64,
        window=wire.encode({"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}),
        full_geometry=wire.encode(GEOMETRY), candidate_bounds=wire.encode({
            scenario: {"original": 5140} for scenario in ("S1", "S2")}),
        private_entrypoint=binding.runtime, local_worktree=tmp_path / "repo", remote_worktree=tmp_path / "repo",
        actual_cache=native / "cache" / leaf, actual_output=native / "output" / leaf,
        actual_diagnostics=native / "diagnostics" / leaf,
        canonical_journal=native / "canonical" / "experiments.jsonl",
        global_marker=native / "canonical" / bridge.EI_SUPPORT_MARKER,
        research_locks=(native / "canonical" / "intraday-research.lock",
                        native / "output" / bridge.EH_SUPPORT_CAMPAIGN_LOCK),
        invented_cache_parent=native / "cache", invented_output_parent=native / "output",
        invented_diagnostics_parent=native / "diagnostics", output_filename=binding.output,
        output_max_bytes=binding.output_max, ssh_argv=(), remote_prefix=(), actual_runner_ready=True,
        input_contract=wire.EI_SUPPORT_INPUT_CONTRACT, resource_profile=wire.ORDINARY300.id,
        local_coordinator=True, local_execution_root=native)


def configuration(plan):
    attempt = bridge._qualification_attempt(plan)
    leaf = plan.unit_id + "-invented-" + attempt
    identity = {"execution_host": bridge.LOCAL_HOST, "runtime_sha256": "b" * 64}
    return {"unit_id": plan.unit_id, "invented_only": True, "resource_profile": plan.resource_profile,
        "automatic_retries": 0, "wall_seconds": 1200, "sessions": 257,
        "source_hashes": {name: "c" * 64 for name in plan.source_names},
        "merge_commit": "d" * 40, "tree": "e" * 40, "reviewed_commit": "f" * 40,
        "attempt": attempt, "deadline_epoch": time.time() + 45,
        "cache": str(plan.invented_cache_parent / leaf), "output": str(plan.invented_output_parent / leaf),
        "journal": str(plan.invented_output_parent / leaf / "invented-experiments.jsonl"),
        "diagnostics": str(plan.invented_diagnostics_parent / leaf),
        "local_identity": identity, "remote_identity": identity, **bridge._mode_metadata(plan)}


def test_ei_contract_keeps_original_three_native_inputs_separate(plan):
    manifest = [pin.metadata(1) for pin in plan.input_pins]
    assert wire.validate_registered_input_manifest(manifest, input_contract=plan.input_contract) == manifest
    for changed in (manifest[:-1], list(reversed(manifest)), manifest + [manifest[0]],
                    [{**manifest[0], "original_path": "/mnt/c/original/CB"}, *manifest[1:]],
                    [{**manifest[0], "original_path": "/invented//CB"}, *manifest[1:]],
                    [*manifest[:2], {**manifest[2], "maximum": 513 * wire.MIB}]):
        with pytest.raises(ValueError):
            wire.validate_registered_input_manifest(changed, input_contract=plan.input_contract)
    with pytest.raises(ValueError):
        wire.validate_registered_input_manifest(manifest, input_contract=wire.EH_SUPPORT_INPUT_CONTRACT)
    assert wire.EH_SUPPORT_INPUT_BOUNDS == (("CB", 16 * wire.MIB), ("CC", 512 * wire.MIB))


def test_ei_pair_and_canonical_paths_cannot_drift(plan):
    bridge.validate_plan(plan)
    assert bridge.profile_for_plan(plan) is wire.ORDINARY300
    for changes in ({"input_contract": wire.EE_INPUT_CONTRACT}, {"input_contract": wire.EH_SUPPORT_INPUT_CONTRACT},
                    {"unit_id": "EI-SUPPORT-2"}, {"specification_path": bridge.EH_SUPPORT_SPECIFICATION_PATH},
                    {"resource_profile": wire.EF_LOCAL_SEQUENCE.id}, {"local_coordinator": False},
                    {"ssh_argv": ("ssh",)}, {"output_max_bytes": 33 * wire.MIB},
                    {"local_execution_root": plan.local_execution_root.with_name("separate-lifecycle")},
                    {"canonical_journal": plan.canonical_journal.with_name("separate.jsonl")},
                    {"global_marker": plan.global_marker.with_name("eh-support-unresolved.json")},
                    {"actual_output": plan.actual_output.with_name("EI-SUPPORT-1-actual2")},
                    {"actual_cache": plan.actual_cache.with_name("old-cache")},
                    {"actual_diagnostics": plan.actual_diagnostics.with_name("old-diagnostics")},
                    {"research_locks": (plan.research_locks[0].with_name("ei-only.lock"), plan.research_locks[1])}):
        with pytest.raises(ValueError):
            bridge.validate_plan(replace(plan, **changes))


def test_ei_bootstrap_and_actual_attempt_fail_before_input_access(plan, monkeypatch):
    config = configuration(plan)
    monkeypatch.setattr(wire, "regular", lambda _: pytest.fail("metadata admission opened an input"))
    assert bridge._bootstrap_profile(config) is wire.ORDINARY300
    bridge.validate_config(config, plan)
    for changes in ({"unit_id": "EI-SUPPORT-2"}, {"local_pipe_test": True}, {"test_ssh_sample": None},
                    {"local_coordinator": False}, {"execution_host": bridge.HOST},
                    {"resource_profile": wire.EF_LOCAL_SEQUENCE.id},
                    {"source_hashes": {**config["source_hashes"], bridge.EH_SUPPORT_SPECIFICATION_PATH: "a" * 64}}):
        with pytest.raises(ValueError):
            bridge._bootstrap_profile({**config, **changes})
    config.update(invented_only=False, attempt="actual1", cache=str(plan.actual_cache), output=str(plan.actual_output),
                  diagnostics=str(plan.actual_diagnostics), journal=str(plan.canonical_journal))
    bridge.validate_config(config, plan)
    for changes in ({"attempt": "actual2"}, {"sessions": 256}, {"automatic_retries": 1}):
        with pytest.raises(ValueError):
            bridge.validate_config({**config, **changes}, plan)


def test_ei_actual_requires_its_exact_same_release_receipt_audit(plan):
    config = configuration(plan)
    config.update(invented_only=False, attempt="actual1")
    qualification = plan.invented_output_parent / (plan.unit_id + "-invented-qualification-v3")
    qualification.mkdir(parents=True)
    publication = {"invented_only": True, "unit_id": plan.unit_id, "output_sha256": "a" * 64,
                   **{key: config[key] for key in ("source_hashes", "local_identity", "remote_identity")},
                   **bridge._mode_metadata(plan)}
    raw = wire.encode(publication)
    (qualification / "publication.json").write_bytes(raw)
    audit = {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": True,
        "science_reexecuted": False, "unit_id": plan.unit_id, "attempt": "qualification-v3",
        "publication_sha256": wire.digest(raw), "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
        "output_sha256": publication["output_sha256"], "merge_commit": config["merge_commit"], "tree": config["tree"],
        "runtime_sha256": config["local_identity"]["runtime_sha256"],
        "auditor_source_sha256": config["source_hashes"][bridge.EI_SUPPORT_RECEIPT_AUDITOR]}
    audit_path = plan.local_execution_root / "launches" / plan.unit_id / "qualification-v3" / "original-receipt-audit.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_bytes(wire.encode(audit))
    config["qualification"] = {"output": str(qualification), "publication_sha256": wire.digest(raw),
                               "audit_sha256": wire.digest(wire.encode(audit))}
    bridge.validate_eh_support_receipt_audit(config, plan)
    for key, value in (("unit_id", bridge.EH_SUPPORT_UNIT_ID), ("merge_commit", "0" * 40),
                       ("invented_only", False), ("science_reexecuted", True), ("status", "FAIL"),
                       ("attempt", "qualification-v1"), ("attempt", "qualification-v2")):
        changed = {**audit, key: value}
        audit_path.write_bytes(wire.encode(changed))
        drifted = deepcopy(config)
        drifted["qualification"]["audit_sha256"] = wire.digest(wire.encode(changed))
        with pytest.raises(ValueError):
            bridge.validate_eh_support_receipt_audit(drifted, plan)
    with pytest.raises(ValueError):
        bridge.validate_eh_support_receipt_audit(config, replace(plan, actual_runner_ready=False))
    for prior in ("qualification-v1", "qualification-v2"):
        old = deepcopy(config)
        old["qualification"]["output"] = str(qualification.with_name(plan.unit_id + "-invented-" + prior))
        with pytest.raises(ValueError, match="independently audited qualification"):
            bridge.validate_eh_support_receipt_audit(old, plan)


def test_ei_qualification_version_does_not_change_other_units(plan):
    assert bridge._qualification_attempt(plan) == "qualification-v3"
    assert bridge.EH_SUPPORT_QUALIFICATION_ATTEMPT == "qualification-v1"
    assert all(binding.qualification_attempt == "qualification-v1"
               for unit, binding in bridge._EH_BINDINGS.items() if unit != bridge.EI_SUPPORT_UNIT_ID)
    config = configuration(plan)
    with pytest.raises(ValueError, match="full invented geometry"):
        bridge.validate_config({**config, "sessions": 256}, plan)


def test_ei_result_requires_three_input_identity_full_eof_and_original_denominators(plan):
    decoded = {"CC": "b" * 64, "CX": "c" * 64}
    envelope = bridge.InputEnvelope(tuple(bridge.InputFile(**pin.metadata(1)) for pin in plan.input_pins),
        wire.digest(wire.encode(decoded)), plan.full_geometry, plan.input_contract)
    manifest = envelope.payload()["inputs"]
    raw_audit = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True, "full_eof_verified": True,
        "cache_sha256": "d" * 64, "raw_keys": GEOMETRY["cc_chunks"] + GEOMETRY["cx_chunks"],
        "archives": {kind: {"kind": kind, "compressed_sha256": manifest[index]["sha256"],
            "decoded_sha256": decoded[kind], "expected_chunks": GEOMETRY[kind.lower() + "_chunks"],
            "expected_rows": GEOMETRY[kind.lower() + "_rows"],
            "expected_daily_rows": 0 if kind == "CC" else GEOMETRY["cx_daily_rows"]}
            for index, kind in ((1, "CC"), (2, "CX"))}}
    proof = {"verified": True, "schema": bridge.EI_SUPPORT_OUTPUT_SCHEMA, "invented_only": True,
             "full_eof_verified": True, "all_original_coordinates_verified": True, "full_geometry": True,
             "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
             "summary_sha256": "e" * 64, "raw_byte_audit": raw_audit}
    result = {"output_sha256": "a" * 64, "output_bytes": 100, "geometry": GEOMETRY,
              "verification_complete": True, "independent_verification": proof,
              "candidate_counts": bridge.canonical(plan.candidate_bounds)}
    bridge.validate_result(result, envelope, plan, invented_only=True)
    for key, value in (("full_eof_verified", False), ("all_original_coordinates_verified", False),
                       ("full_geometry", False), ("input_manifest_sha256", "0" * 64)):
        with pytest.raises(ValueError):
            bridge.validate_result({**result, "independent_verification": {**proof, key: value}},
                                   envelope, plan, invented_only=True)
    changed = deepcopy(proof)
    changed["raw_byte_audit"]["archives"]["CX"]["decoded_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="combined decoded archive"):
        bridge.validate_result({**result, "independent_verification": changed}, envelope, plan, invented_only=True)
