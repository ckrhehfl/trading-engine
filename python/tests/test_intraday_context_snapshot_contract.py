"""Invented-only admission checks for the separately approved EI snapshot."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_predecessor as predecessor
from research import research_budget
from test_intraday_context_support_contract import configuration, plan as support_plan
from test_intraday_context_snapshot_unit import completed


@pytest.fixture
def plan(tmp_path, monkeypatch):
    old = support_plan.__wrapped__(tmp_path, monkeypatch)
    binding = bridge._EH_BINDINGS[bridge.EI_SNAPSHOT_UNIT_ID]
    names = tuple(sorted({binding.specification, binding.runtime, binding.auditor,
        *binding.private_dependencies, bridge.EE_SPECIFICATION_PATH,
        "python/research/intraday_campaign_wsl_transport.py",
        "python/research/intraday_campaign_wsl_bridge.py",
        "python/research/experiment_log.py", "python/research/research_budget.py",
        "python/research/intraday_context_predecessor.py"}))
    leaf = bridge.EI_SNAPSHOT_UNIT_ID + "-actual1"
    return replace(old, unit_id=bridge.EI_SNAPSHOT_UNIT_ID, specification_path=binding.specification,
        source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names))),
        private_entrypoint=binding.runtime, resource_profile=wire.EI_LOCAL_MODEL.id,
        actual_cache=old.actual_cache.parent / leaf, actual_output=old.actual_output.parent / leaf,
        actual_diagnostics=old.actual_diagnostics.parent / leaf,
        global_marker=old.global_marker.parent / bridge.EI_SNAPSHOT_MARKER,
        output_filename=binding.output, output_max_bytes=binding.output_max)


def test_exact_snapshot_binding_and_bootstrap(plan):
    assert bridge.profile_for_plan(plan) is wire.EI_LOCAL_MODEL
    bridge.validate_plan(plan)
    config = configuration(plan)
    config["wall_seconds"] = wire.EI_LOCAL_MODEL.wall_seconds
    assert bridge._bootstrap_profile(config) is wire.EI_LOCAL_MODEL
    assert bridge._qualification_attempt(plan) == "qualification-v1"
    assert bridge._EH_BINDINGS[bridge.EI_SUPPORT_UNIT_ID].resource_profile == wire.ORDINARY300.id
    with pytest.raises(ValueError, match="local mode forbids"):
        bridge.remote_command(config, plan)


@pytest.mark.parametrize("change", [
    {"resource_profile": wire.ORDINARY300.id}, {"resource_profile": wire.EF_LOCAL_SEQUENCE.id},
    {"unit_id": bridge.EI_SUPPORT_UNIT_ID}, {"unit_id": "EI-UNREGISTERED"},
    {"specification_path": bridge.EI_SUPPORT_SPECIFICATION_PATH},
    {"input_contract": wire.EF_INPUT_CONTRACT}, {"local_coordinator": False},
])
def test_wrong_unit_scope_or_resource_cannot_borrow_approval(plan, change):
    with pytest.raises(ValueError):
        bridge.profile_for_plan(replace(plan, **change))


@pytest.mark.parametrize("change", [
    {"resource_profile": wire.ORDINARY300.id}, {"resource_profile": wire.EF_LOCAL_SEQUENCE.id},
    {"unit_id": bridge.EI_SUPPORT_UNIT_ID}, {"unit_id": "EI-UNREGISTERED"},
    {"execution_host": "remote"}, {"local_coordinator": False},
    {"local_pipe_test": True}, {"test_ssh_sample": {}},
])
def test_bootstrap_rejects_mixed_declarations_before_science_import(plan, change):
    config = configuration(plan)
    config.update(change)
    with pytest.raises(ValueError):
        bridge._bootstrap_profile(config)


def test_exact_worker_allocation_and_wall_are_ei_scoped(plan, monkeypatch):
    monkeypatch.setattr(research_budget, "ResearchBudget", lambda **kw: kw)
    assert bridge.worker_budget(plan) == {
        "cpu_seconds": 1500, "address_space_bytes": 3072 * wire.MIB,
        "wall_seconds": 3600, "operator_cpu_scope": research_budget.EI_WORKER_SCOPE}
    receipt = {"status": "complete", "violations": [],
        "limits": {**wire.EI_LOCAL_MODEL.role_limits()["worker"], "wall_seconds": 3600, "nice": 10},
        "operator_cpu_scope": research_budget.EI_WORKER_SCOPE}
    bridge._validate_worker_budget(receipt, wire.EI_LOCAL_MODEL)
    for changed in (None, research_budget.EF_WORKER_SCOPE):
        bad = {**receipt, "operator_cpu_scope": changed}
        with pytest.raises(ValueError):
            bridge._validate_worker_budget(bad, wire.EI_LOCAL_MODEL)
    calls = []
    monkeypatch.setattr(wire, "fixed_deadline", lambda deadline, **kw: calls.append((deadline, kw)))
    bridge._set_deadline({"deadline_epoch": 123}, wire.EI_LOCAL_MODEL)
    assert calls == [(123, {"profile": wire.EI_LOCAL_MODEL})]


def test_predecessor_metadata_is_bound_before_new_marker(plan, monkeypatch):
    raw = (Path(__file__).resolve().parents[2] / plan.specification_path).read_bytes()
    registered_predecessor = json.loads(raw)["predecessor"]
    destination = plan.local_worktree / plan.specification_path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(raw)
    config = {"source_hashes": {plan.specification_path: wire.digest(raw)}}
    calls = []
    def verify(pin, native, inputs, checkpoint):
        assert not plan.global_marker.exists()
        assert inputs == plan.input_pins and all(type(row) is bridge.InputPin for row in inputs)
        calls.append((pin, native))
        return {"verified": True}
    monkeypatch.setattr(predecessor, "validate_support_predecessor", verify)
    assert bridge._verify_ei_support_predecessor(config, plan, plan.local_worktree, None) == {"verified": True}
    assert calls == [(registered_predecessor, plan.local_execution_root)]
    destination.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="registration changed"):
        bridge._verify_ei_support_predecessor(config, plan, plan.local_worktree, None)
    assert len(calls) == 1


def test_snapshot_result_binds_both_archives_and_exact_feature_label_counts(completed, plan):
    _, envelope, receipt, _, _ = completed
    current = replace(plan, candidate_bounds=receipt.candidate_counts)
    payload = receipt.payload()
    bridge.validate_result(payload, envelope, current, invented_only=True)
    for field, value in (("contains_model_fits_or_returns", True), ("stored_volume_units_certified", True),
                         ("historical_availability_certified", True), ("support_totals", {})):
        changed = deepcopy(payload)
        changed["independent_verification"][field] = value
        with pytest.raises(ValueError):
            bridge.validate_result(changed, envelope, current, invented_only=True)
    for field, value in (("original_coordinates", 0), ("original_memberships", 0), ("reference_session_calls", 0),
                         ("universe_sha256", "f" * 64), ("members", {})):
        changed = deepcopy(payload)
        proof = changed["independent_verification"]
        proof["snapshot_summary"][field] = value
        proof["summary_sha256"] = wire.digest(wire.encode(proof["snapshot_summary"]))
        with pytest.raises(ValueError):
            bridge.validate_result(changed, envelope, current, invented_only=True)
