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
