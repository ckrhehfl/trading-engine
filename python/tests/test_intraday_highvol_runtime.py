"""Invented lifecycle/resource failures; never launch a service or open sources."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from research import intraday_highvol_runtime as runtime


def sample():
    return {"root": {"path": "/sys/fs/cgroup/user.slice/eg.service", "memory": runtime.MEMORY,
            "swap": 0, "pids": 128, "cpu_quota": 400000, "cpu_period": 100000, "cpus": None},
            "ancestors": [], "descendants": [], "cpu_usec": 25_000, "memory_current": 1_000_000,
            "memory_peak": 2_000_000, "swap_current": 0, "pids_current": 2,
            "memory_events": {"max": 0, "oom": 0, "oom_kill": 0}, "pids_events": {"max": 0},
            "processes": [os.getpid()], "threads": [{"pid": os.getpid(), "tid": os.getpid(),
            "nice": 10, "cpus": [0, 1, 2, 3]}], "elapsed_seconds": 1.0}


class Guard:
    def __init__(self):
        self.last_sample = sample()
        self.stages = []
        self.started = 1.0

    def checkpoint(self, stage):
        self.stages.append(stage)
        runtime.validate_resources(self.last_sample, self.last_sample["elapsed_seconds"])


@pytest.fixture
def contract(tmp_path, monkeypatch):
    evidence = tmp_path / "private"
    evidence.mkdir(mode=0o700)
    (evidence / runtime.UNIT).mkdir(mode=0o700)
    monkeypatch.setattr(runtime, "NATIVE_ROOT", evidence)
    return runtime.RunContract(worktree=tmp_path, attempt="qualification1", invented_only=True,
        service="eg-highvol-coverage-qualification1-0123456789abcdef.service",
        source_hashes=tuple((name, "a" * 64) for name in (runtime.SPEC, runtime.ENTRYPOINT, runtime.RUNTIME_SOURCE)),
        runtime_files=((str(Path(sys.executable).resolve()), "b" * 64, 32 * runtime.files.MIB),),
        merge_commit="c" * 40, reviewed_commit="d" * 40, tree="e" * 40)


def closed_unit(observed):
    def preflight():
        observed.append("preflight")

    def compute(directory, binding, checkpoint):
        rows = [json.loads(row) for row in (directory / "discovery.jsonl").read_text().splitlines()]
        assert [row["status"] for row in rows] == ["started"]
        assert (directory / "unresolved.json").exists()
        assert rows[0]["parameters"] == binding
        observed.append("compute_after_durable_start")
        raw = b"invented bytes only\n"
        runtime.files.exclusive_bytes(directory / "coverage.bin", raw)
        return runtime.UnitOutput((runtime.Artifact("coverage.bin", runtime.files.digest(raw), len(raw), 1024),),
                                  runtime.files.encode(runtime.FULL), runtime.files.encode({"invented": True}))

    def verify(directory, output, binding, checkpoint):
        observed.append("independent_verification")
        return {"complete": True, "records": runtime.FULL["union_coordinates"]}

    return runtime.ClosedUnit(preflight, compute, verify)


def test_profile_is_separate_and_cpuset_absence_is_not_claimed_as_hard_isolation():
    state = sample()
    runtime.validate_resources(state, 0.25)
    assert state["root"]["cpus"] is None
    assert runtime.PROFILE.startswith("eg-")


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(cpu_usec=runtime.CPU_USEC),
    lambda row: row.update(memory_peak=runtime.MEMORY + 1),
    lambda row: row.update(memory_current=runtime.MEMORY + 1),
    lambda row: row.update(swap_current=1),
    lambda row: row.update(pids_current=129),
    lambda row: row["memory_events"].update(max=1),
    lambda row: row["memory_events"].update(oom_kill=1),
    lambda row: row["pids_events"].update(max=1),
    lambda row: row["root"].update(memory=None),
    lambda row: row["root"].update(swap=None),
    lambda row: row["root"].update(cpu_quota=800000),
    lambda row: row["root"].update(pids=None),
    lambda row: row["threads"][0].update(cpus=[0, 1, 2, 3, 4]),
    lambda row: row["threads"][0].update(nice=0),
])
def test_resource_counter_and_all_thread_failures_are_rejected(mutation):
    state = sample()
    mutation(state)
    with pytest.raises(ValueError):
        runtime.validate_resources(state, 1)


def test_whole_service_wall_budget_includes_preflight():
    with pytest.raises(ValueError, match="wall budget"):
        runtime.validate_resources(sample(), runtime.WALL_SECONDS)


def test_tighter_ancestor_and_escaped_descendant_are_rejected():
    state = sample()
    state["ancestors"] = [{**state["root"], "memory": runtime.MEMORY - 1}]
    with pytest.raises(ValueError, match="ancestor"):
        runtime.validate_resources(state, 1)
    state["ancestors"] = []
    state["descendants"] = [{**state["root"], "path": "/another/cgroup"}]
    with pytest.raises(ValueError, match="descendant"):
        runtime.validate_resources(state, 1)


def test_child_unlimited_counter_is_still_under_service_aggregate_caps():
    state = sample()
    state["descendants"] = [{**state["root"], "path": state["root"]["path"] + "/child", "memory": None,
                             "cpu_quota": None, "pids": None}]
    runtime.validate_resources(state, 1)


def test_command_has_same_subtree_all_lifecycle_limits_and_no_gpu_for_coverage(contract):
    argv = runtime.launch_argv(contract, Path(sys.executable).resolve(), contract.worktree / "var/eg-highvol-qualification1.json")
    assert "--property=MemoryMax=3087007744" in argv
    assert "--property=MemorySwapMax=0" in argv
    assert "--property=CPUQuota=400%" in argv
    assert "--property=CPUAffinity=0 1 2 3" in argv
    assert "--property=KillMode=control-group" in argv
    assert "--property=RuntimeMaxSec=3550" in argv
    assert "--property=TimeoutStopSec=10" in argv
    assert any(row.startswith("--property=ExecStopPost=") for row in argv)
    assert "-I" in argv and "-S" in argv
    assert not any("RLIMIT_AS" in value or "ssh" in value or "AllowedCPUs" in value for value in argv)


@pytest.mark.parametrize("change", [
    {"attempt": "actual2", "invented_only": False},
    {"attempt": "../actual1"},
    {"service": "ef-previous.service"},
    {"source_hashes": ((runtime.SPEC, "a" * 64),)},
    {"runtime_files": ()},
    {"merge_commit": "not-a-commit"},
    {"qualification_attempt": "qualification1"},
])
def test_contract_rejects_permissions_or_identity_reuse(contract, change):
    with pytest.raises(ValueError):
        replace(contract, **change).validate()


def test_success_orders_start_verify_terminal_publish_and_refuses_a_second_attempt(contract, monkeypatch):
    events = []
    monkeypatch.setattr(runtime, "verify_release", lambda *_: events.append("release_rechecked"))
    publication = runtime._execute(contract, closed_unit(events), Guard())
    assert events == ["preflight", "compute_after_durable_start", "independent_verification", "release_rechecked"]
    assert not (contract.directory / "unresolved.json").exists()
    assert publication == runtime._publication(contract, contract.directory, lambda _: None)
    with pytest.raises(FileExistsError):
        runtime._execute(contract, closed_unit(events), Guard())


def test_compute_failure_keeps_start_failure_and_sticky_marker(contract, monkeypatch):
    observed = []
    unit = closed_unit(observed)

    def fail(directory, binding, checkpoint):
        assert json.loads((directory / "discovery.jsonl").read_text())["status"] == "started"
        raise RuntimeError("invented compute failure")

    monkeypatch.setattr(runtime, "verify_release", lambda *_: None)
    with pytest.raises(RuntimeError, match="invented compute"):
        runtime._execute(contract, replace(unit, compute=fail), Guard())
    assert (contract.directory / "unresolved.json").exists()
    assert not (contract.directory / "publication.json").exists()
    records = [json.loads(row) for row in (contract.directory / "discovery.jsonl").read_text().splitlines()]
    assert [record["status"] for record in records] == ["started", "failed"]
    with pytest.raises(FileExistsError):
        runtime._execute(contract, unit, Guard())


def test_incomplete_independent_verification_prevents_completed_terminal(contract, monkeypatch):
    unit = closed_unit([])
    monkeypatch.setattr(runtime, "verify_release", lambda *_: None)
    with pytest.raises(ValueError, match="incomplete"):
        runtime._execute(contract, replace(unit, independent_verify=lambda *_: {"complete": False}), Guard())
    rows = [json.loads(row) for row in (contract.directory / "discovery.jsonl").read_text().splitlines()]
    assert [row["status"] for row in rows] == ["started", "failed"]


def test_source_drift_after_compute_is_failed_not_published(contract, monkeypatch):
    def fail(*_):
        raise ValueError("changed source")

    monkeypatch.setattr(runtime, "verify_release", fail)
    with pytest.raises(ValueError, match="changed source"):
        runtime._execute(contract, closed_unit([]), Guard())
    assert not (contract.directory / "publication.json").exists()


def test_artifact_modified_by_verifier_cannot_reach_terminal(contract, monkeypatch):
    def tamper(directory, *_):
        (directory / "coverage.bin").write_bytes(b"modified")
        return {"complete": True}

    monkeypatch.setattr(runtime, "verify_release", lambda *_: None)
    with pytest.raises(ValueError, match="artifact changed"):
        runtime._execute(contract, replace(closed_unit([]), independent_verify=tamper), Guard())


def test_publication_tamper_or_new_artifact_change_is_rejected(contract, monkeypatch):
    monkeypatch.setattr(runtime, "verify_release", lambda *_: None)
    runtime._execute(contract, closed_unit([]), Guard())
    (contract.directory / "coverage.bin").write_bytes(b"changed after completed")
    with pytest.raises(ValueError, match="artifact changed"):
        runtime._publication(contract, contract.directory, lambda _: None)


def test_actual_requires_original_full_qualification_before_compute(contract, monkeypatch):
    actual = replace(contract, attempt="actual1", invented_only=False,
        service="eg-highvol-coverage-actual1-0123456789abcdef.service", qualification_attempt="qualification1",
        qualification_audit_sha256="f" * 64)
    monkeypatch.setattr(runtime, "verify_release", lambda *_: None)
    observed = []
    with pytest.raises(FileNotFoundError):
        runtime._execute(actual, closed_unit(observed), Guard())
    assert observed == ["preflight"]
    assert not actual.directory.exists()


def test_formal_path_has_no_test_or_unmerged_release_bypass(contract, monkeypatch):
    events = []

    class ContextGuard(Guard):
        def __init__(self, service):
            super().__init__()
            events.append("cgroup_" + service)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def fail(*_):
        raise ValueError("unmerged release")

    monkeypatch.setattr(runtime, "BudgetGuard", ContextGuard)
    monkeypatch.setattr(runtime, "verify_release", fail)
    with pytest.raises(ValueError, match="unmerged"):
        runtime.run_registered_unit(contract, closed_unit(events))
    assert len(events) == 1 and events[0].startswith("cgroup_")
    assert not contract.directory.exists()


def test_campaign_lock_is_exclusive_and_private(contract):
    with runtime._campaign_lock():
        with pytest.raises(BlockingIOError):
            with runtime._campaign_lock():
                pytest.fail("parallel lifecycle admitted")
    runtime.NATIVE_ROOT.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        with runtime._campaign_lock():
            pytest.fail("public evidence parent admitted")


def test_hot_checkpoints_are_throttled_but_final_phase_forces_kernel_read(monkeypatch):
    guard = runtime.BudgetGuard.__new__(runtime.BudgetGuard)
    guard.path = Path("/invented-cgroup")
    guard.started = 1.0
    guard.next_sample = 0.0
    guard.lock = threading.Lock()
    guard.last_sample = {}
    calls = []
    monkeypatch.setattr(runtime.time, "clock_gettime", lambda _: 2.0)
    monkeypatch.setattr(runtime, "sample_cgroup", lambda path: calls.append(path) or sample())
    for _ in range(10_000):
        guard.checkpoint("coverage_window")
    assert len(calls) == 1
    guard.checkpoint("verification_and_persistence_complete")
    assert len(calls) == 2


def test_throttled_checkpoint_still_checks_whole_wall_deadline(monkeypatch):
    guard = runtime.BudgetGuard.__new__(runtime.BudgetGuard)
    guard.started = 0.0
    guard.next_sample = runtime.WALL_SECONDS + 100
    monkeypatch.setattr(runtime.time, "clock_gettime", lambda _: runtime.MAIN_WALL_SECONDS)
    with pytest.raises(ValueError, match="wall budget"):
        guard.checkpoint("coverage_window")


@pytest.mark.parametrize("role", ["controller", "audit"])
def test_metadata_wrapper_is_a_closed_swap_free_128mib_service(contract, role):
    argv = runtime.metadata_launch_argv(contract, role, Path(sys.executable).resolve(),
                                       contract.worktree / "var/eg-highvol-qualification1.json")
    assert "--property=MemoryMax=134217728" in argv
    assert "--property=MemorySwapMax=0" in argv
    assert "--property=CPUAffinity=0 1 2 3" in argv
    assert "--unit=" + runtime.metadata_service(contract.service, role) in argv
    value = sample()
    value["root"].update(path="/sys/fs/cgroup/user.slice/" + runtime.metadata_service(contract.service, role),
                          memory=128 * runtime.files.MIB, pids=16)
    runtime.validate_metadata_resources(value, contract.service, role)
    value["root"]["swap"] = None
    with pytest.raises(ValueError, match="hard limits"):
        runtime.validate_metadata_resources(value, contract.service, role)


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(swap_current=1),
    lambda row: row.update(memory_peak=128 * runtime.files.MIB + 1),
    lambda row: row["memory_events"].update(oom_kill=1),
    lambda row: row["threads"][0].update(cpus=[4]),
])
def test_metadata_children_cannot_evade_envelope(contract, mutation):
    value = sample()
    value["root"].update(path="/sys/fs/cgroup/user.slice/" + runtime.metadata_service(contract.service, "audit"),
                          memory=128 * runtime.files.MIB, pids=16)
    mutation(value)
    with pytest.raises(ValueError):
        runtime.validate_metadata_resources(value, contract.service, "audit")


def snapshot_contract(contract):
    descriptor = runtime.registered_unit("EG-SCENARIO-SNAPSHOT-1")
    return replace(contract, unit_id=descriptor.unit_id,
        service=descriptor.service_prefix + "qualification1-0123456789abcdef.service",
        source_hashes=tuple((name, "a" * 64) for name in
                           (descriptor.specification, descriptor.entrypoint, runtime.RUNTIME_SOURCE)))


def test_snapshot_uses_its_own_registered_paths_without_changing_budget(contract):
    snapshot = snapshot_contract(contract)
    snapshot.validate()
    descriptor = snapshot.descriptor
    config = snapshot.worktree / "var/eg-highvol-snapshot-qualification1.json"
    argv = runtime.launch_argv(snapshot, Path(sys.executable).resolve(), config)
    assert argv[-3:] == [str(snapshot.worktree / descriptor.entrypoint), "worker", str(config)]
    assert "--property=MemoryMax=3087007744" in argv and "--property=CPUQuota=400%" in argv
    assert snapshot.directory == runtime.NATIVE_ROOT / "EG-SCENARIO-SNAPSHOT-1/qualification1"
    assert snapshot.binding["input_contract"] == "eg-highvol-scenario-snapshot-v1"
    assert snapshot.binding["geometry"] == contract.binding["geometry"]
    assert contract.unit_id == runtime.UNIT and contract.descriptor.entrypoint == runtime.ENTRYPOINT
    for role in ("controller", "audit"):
        helper = runtime.metadata_launch_argv(snapshot, role, Path(sys.executable).resolve(), config)
        assert "--unit=eg-highvol-snapshot-" + role + "-qualification1-0123456789abcdef.service" in helper
        expected = descriptor.entrypoint if role == "controller" else descriptor.auditor
        assert str(snapshot.worktree / expected) in helper


@pytest.mark.parametrize("unit_id", ["EG-MODEL-1", "EG-SCENARIO-SNAPSHOT-2", "../EG-COVERAGE-1", None])
def test_only_explicit_registered_units_have_paths_or_permissions(contract, unit_id):
    with pytest.raises(ValueError, match="not separately registered"):
        replace(contract, unit_id=unit_id).validate()


def test_snapshot_cannot_borrow_coverage_configuration_or_source_identity(contract):
    snapshot = snapshot_contract(contract)
    with pytest.raises(ValueError, match="configuration path"):
        runtime.launch_argv(snapshot, Path(sys.executable).resolve(),
                            snapshot.worktree / "var/eg-highvol-qualification1.json")
    with pytest.raises(ValueError, match="source roster"):
        replace(snapshot, source_hashes=contract.source_hashes).validate()
    with pytest.raises(ValueError, match="service identity"):
        replace(snapshot, service=contract.service).validate()
    with pytest.raises(ValueError, match="metadata service"):
        runtime.metadata_service("eg-highvol-unregistered-actual1-0123456789abcdef.service", "audit")


def test_snapshot_durable_lifecycle_is_separate_from_completed_coverage(contract, monkeypatch):
    monkeypatch.setattr(runtime, "verify_release", lambda *_: None)
    runtime._execute(contract, closed_unit([]), Guard())
    original = (contract.directory / "publication.json").read_bytes()
    snapshot = snapshot_contract(contract)
    snapshot.directory.parent.mkdir(mode=0o700)
    result = runtime._execute(snapshot, closed_unit([]), Guard())
    assert result["binding"]["unit_id"] == "EG-SCENARIO-SNAPSHOT-1"
    assert (contract.directory / "publication.json").read_bytes() == original
    assert runtime._publication(snapshot, snapshot.directory, lambda _: None) == result
    with pytest.raises(ValueError, match="publication binding"):
        runtime._publication(snapshot, contract.directory, lambda _: None)


def test_snapshot_actual_cannot_use_coverage_qualification_directory(contract):
    snapshot = replace(snapshot_contract(contract), invented_only=False, attempt="actual1",
        service="eg-highvol-snapshot-actual1-0123456789abcdef.service",
        qualification_attempt="qualification1", qualification_audit_sha256="a" * 64)
    contract.directory.mkdir(mode=0o700)
    (contract.directory / "original-audit.json").write_bytes(b"coverage original is not snapshot evidence\n")
    with pytest.raises((FileNotFoundError, ValueError)):
        runtime.validate_qualification(snapshot, lambda _: None)
