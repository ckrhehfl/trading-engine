"""Invented-only bridge boundaries for the saved EI S2 follow-up."""

from dataclasses import replace
import fcntl
from io import BytesIO
from pathlib import Path
import time

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_saved_fixed_exit_binding as binding
from test_intraday_campaign_wsl_bridge import plan_at


@pytest.fixture
def fixed_plan(tmp_path, monkeypatch):
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    unit = bridge.EI_SAVED_FIXED_EXIT_UNIT_ID
    registered = bridge._EH_BINDINGS[unit]
    generic = plan_at(tmp_path / "repo", native)
    roster = tuple(sorted((set(generic.source_names) - {generic.private_entrypoint, generic.specification_path}) | {
        registered.specification, registered.runtime, registered.auditor, bridge.EE_SPECIFICATION_PATH,
        *registered.private_dependencies}))
    leaf = unit + "-actual1"
    plan = replace(generic, unit_id=unit, specification_path=registered.specification,
        private_entrypoint=registered.runtime, source_names=roster,
        source_roster_sha256=wire.digest(wire.encode(list(roster))),
        input_pins=tuple(bridge.InputPin(name, "/invented-native/" + name, "a" * 64, maximum)
                         for name, maximum in wire.EI_SAVED_FIXED_EXIT_INPUT_BOUNDS),
        input_contract=wire.EI_SAVED_FIXED_EXIT_INPUT_CONTRACT, resource_profile=wire.EI_LOCAL_MODEL.id,
        local_coordinator=True, local_execution_root=native, ssh_argv=(), remote_prefix=(),
        actual_cache=native / "cache" / leaf, actual_output=native / "output" / leaf,
        actual_diagnostics=native / "diagnostics" / leaf,
        global_marker=native / "canonical" / bridge.EI_SAVED_FIXED_EXIT_MARKER,
        research_locks=(native / "canonical" / "intraday-research.lock", native / "output" / bridge.EH_SUPPORT_CAMPAIGN_LOCK),
        output_filename=registered.output, output_max_bytes=registered.output_max,
        candidate_bounds=wire.encode({"S2": {"original": 3140}}), actual_runner_ready=True)
    monkeypatch.setattr(bridge, "EI_SUPPORT_EXECUTION_ROOT", native)
    monkeypatch.setattr(binding, "saved_fixed_exit_envelope", lambda *_: None)
    return plan


def _invented_config(plan, attempt):
    leaf = plan.unit_id + "-invented-" + attempt
    identity = {"execution_host": bridge.LOCAL_HOST}
    output = plan.invented_output_parent / leaf
    return {**bridge._mode_metadata(plan), "unit_id": plan.unit_id, "invented_only": True,
            "local_pipe_test": False, "resource_profile": plan.resource_profile, "attempt": attempt,
            "automatic_retries": 0, "wall_seconds": wire.EI_LOCAL_MODEL.wall_seconds,
            "sessions": bridge.canonical(plan.full_geometry)["sessions"], "deadline_epoch": time.time() + 45,
            "source_hashes": dict.fromkeys(plan.source_names, "c" * 64), "merge_commit": "d" * 40,
            "tree": "e" * 40, "reviewed_commit": "f" * 40, "local_identity": identity, "remote_identity": identity,
            "cache": str(plan.invented_cache_parent / leaf), "output": str(output),
            "diagnostics": str(plan.invented_diagnostics_parent / leaf), "journal": str(output / "invented-experiments.jsonl")}


def test_saved_fixed_exit_binding_rejects_foreign_plan_marker_or_attempt(fixed_plan):
    plan = fixed_plan
    bridge.validate_plan(plan)
    for patch in ({"local_coordinator": False}, {"resource_profile": wire.ORDINARY300.id},
                  {"input_contract": wire.EI_ECONOMIC_INPUT_CONTRACT}):
        with pytest.raises(ValueError, match="pairing differs"):
            bridge.validate_plan(replace(plan, **patch))
    with pytest.raises(ValueError, match="exclusive paths"):
        bridge.validate_plan(replace(plan, global_marker=plan.global_marker.with_name(bridge.EI_ECONOMIC_MARKER)))
    with pytest.raises(ValueError, match="qualification slot differs"):
        bridge.validate_config(_invented_config(plan, "wrong-qualification"), plan)


@pytest.mark.parametrize("invented_only", [True, False])
def test_saved_fixed_exit_predecessor_is_selected_under_shared_locks_before_marker_or_input(
        fixed_plan, monkeypatch, invented_only):
    plan = fixed_plan
    config = _invented_config(plan, "qualification-v1")
    config["invented_only"] = invented_only
    if not invented_only:
        config.update(attempt="actual1", output=str(plan.actual_output), journal=str(plan.canonical_journal))
    output = Path(config["output"])
    output.parent.mkdir()
    plan.canonical_journal.parent.mkdir()
    marker = output / "unresolved.json" if invented_only else plan.global_marker
    admissions = []

    def prior(candidate, *, invented_only, checkpoint):
        assert candidate is plan and invented_only is config["invented_only"]
        assert admissions == ([] if invented_only else ["receipt", "qualification"])
        assert not marker.exists() and not Path(config["journal"]).exists()
        for lock in plan.research_locks:
            with lock.open("rb") as reader, pytest.raises(BlockingIOError):
                fcntl.flock(reader, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise ValueError("invented predecessor refusal")

    monkeypatch.setattr(binding, "verify_saved_fixed_exit_handoff", prior)
    monkeypatch.setattr(wire, "apply_role_limits", lambda *args, **kwargs: None)
    monkeypatch.setattr(wire, "fixed_deadline", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge, "GitHelpers", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge, "verify_sources", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge, "validate_eh_support_receipt_audit", lambda *_: admissions.append("receipt"))
    monkeypatch.setattr(bridge, "validate_qualification", lambda *_: admissions.append("qualification"))
    monkeypatch.setattr(bridge, "require_free_disk", lambda *_: None)
    monkeypatch.setattr(wire, "read_header", lambda *_: pytest.fail("input protocol before predecessor"))
    monkeypatch.setattr(wire, "send_header", lambda *_: pytest.fail("STARTED before predecessor"))
    monkeypatch.setattr(bridge, "actual_input_metadata", lambda *_: pytest.fail("payload before predecessor"))
    with pytest.raises(ValueError, match="invented predecessor refusal"):
        bridge.coordinator(config, plan, plan.remote_worktree, BytesIO(), BytesIO(), unit_lock_fd=123)
    assert not marker.exists() and not Path(config["journal"]).exists()
