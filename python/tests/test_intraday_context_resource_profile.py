"""Invented EI resource receipts and mocked OS limits; no research input access."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import research_budget as rb
from test_intraday_context_support_contract import configuration, plan as support_plan
from test_research_budget import FakeResources


PROFILE = wire.EI_LOCAL_MODEL
WORKER = {"cpu_seconds": 1500, "address_space_bytes": 3072 * wire.MIB,
          "wall_seconds": 3600}


def sample(role, pid, profile=PROFILE):
    parent = wire.parent_limits(role, profile=profile)
    return {"role": role, "pid": pid, "resource_profile": profile.id,
        "limits": profile.role_limits()[role], "parent_limits": parent,
        "cpu_self_seconds": 1.0, "children_cpu_diagnostic_seconds": 0.0,
        "address_space_bytes": 32 * wire.MIB,
        "helpers": {"cpu_seconds": 0, "count": 0, "readbacks": []},
        "readback": {"rlimit_cpu": [parent["cpu_seconds"]] * 2,
            "rlimit_as": [parent["address_space_bytes"]] * 2, "nice": 10,
            "affinity": [2], "oom_score_adj": "1000", "tasks": 1,
            **({"rlimit_nproc": [0, 0]} if role == "worker" else {})}}


def samples():
    return {role: sample(role, pid) for pid, role in enumerate(PROFILE.role_limits(), start=10)}


def test_profile_identity_is_separate_even_when_ef_partition_matches():
    assert PROFILE.id == "ei-local-model-cpu1800-as4096-v1"
    assert PROFILE is not wire.EF_LOCAL_SEQUENCE
    assert PROFILE.roles == wire.EF_LOCAL_SEQUENCE.roles
    assert PROFILE.helpers == wire.CORE600.helpers
    assert PROFILE.role_limits() == {
        "worker": {"cpu_seconds": 1500, "address_space_bytes": 3072 * wire.MIB},
        "coordinator": {"cpu_seconds": 180, "address_space_bytes": 512 * wire.MIB},
        "controller": {"cpu_seconds": 120, "address_space_bytes": 512 * wire.MIB}}
    assert (PROFILE.cpu_seconds, PROFILE.address_space_bytes, PROFILE.wall_seconds) == (1800, 4096 * wire.MIB, 3600)
    assert wire.resource_profile(PROFILE) is PROFILE
    assert wire.resource_profile(PROFILE.id) is PROFILE
    with pytest.raises(ValueError, match="unsupported registered"):
        wire.resource_profile(replace(PROFILE))
    wire.validate_role_limits(PROFILE.role_limits(), profile=PROFILE)
    assert wire.parent_limits("coordinator", profile=PROFILE) == {
        "cpu_seconds": 170, "address_space_bytes": 480 * wire.MIB}
    assert wire.parent_limits("controller", profile=PROFILE) == {
        "cpu_seconds": 115, "address_space_bytes": 480 * wire.MIB}


@pytest.mark.parametrize("profile,cpu,space,wall", [
    (wire.CORE600, 600, 512, 1200), (wire.ORDINARY300, 300, 512, 1200),
    (wire.ED_H1_600, 600, 512, 1200), (wire.ED_H1_600_AS520, 600, 520, 1200),
    (wire.EF_LOCAL_SEQUENCE, 1800, 4096, 3600)])
def test_existing_profile_totals_are_unchanged(profile, cpu, space, wall):
    assert (profile.cpu_seconds, profile.address_space_bytes, profile.wall_seconds) == (cpu, space * wire.MIB, wall)
    wire.validate_role_limits(profile.role_limits(), profile=profile)
    assert wire.resource_profile(profile.id) is profile
    assert wire.ORDINARY300.role_limits()["worker"] == {
        "cpu_seconds": 180, "address_space_bytes": 352 * wire.MIB}


def test_support_remains_ordinary_and_rejects_ei_model_before_any_input_open(support_plan, monkeypatch):
    monkeypatch.setattr(wire, "regular", lambda *_: pytest.fail("admission opened an input"))
    assert bridge.profile_for_plan(support_plan) is wire.ORDINARY300
    with pytest.raises(ValueError):
        bridge.validate_plan(replace(support_plan, resource_profile=PROFILE.id))
    config = configuration(support_plan)
    assert bridge._bootstrap_profile(config) is wire.ORDINARY300
    with pytest.raises(ValueError):
        bridge._bootstrap_profile({**config, "resource_profile": PROFILE.id})


def test_ei_roles_are_local_counted_once_and_cannot_use_cross_host_aggregation():
    rows = samples()
    result = wire.aggregate_local_roles(rows, profile=PROFILE.id)
    assert result["resource_profile"] == PROFILE.id
    assert result["execution_host"] == "local_wsl_only"
    assert result["cpu_seconds_counted_once"] == 3
    assert result["hard_cpu_sum_seconds"] == 1800
    assert result["hard_address_space_sum_bytes"] == 4096 * wire.MIB
    assert "unused_ssh_reserve" not in result
    with pytest.raises(ValueError, match="cross-host"):
        wire.aggregate_roles(rows, profile=PROFILE)


@pytest.mark.parametrize("change", ["missing", "same_pid", "cpu", "ef", "ordinary"])
def test_ei_aggregation_rejects_missing_roles_same_pid_cpu_or_profile_mix(change):
    rows = samples()
    if change == "missing":
        del rows["controller"]
    elif change == "same_pid":
        rows["controller"]["pid"] = rows["worker"]["pid"]
    elif change == "cpu":
        rows["controller"]["readback"]["affinity"] = [3]
    else:
        profile = wire.EF_LOCAL_SEQUENCE if change == "ef" else wire.ORDINARY300
        rows["worker"] = sample("worker", 10, profile)
    with pytest.raises(ValueError):
        wire.aggregate_local_roles(rows, profile=PROFILE)


@pytest.mark.parametrize("field,value", [
    ("tasks", 2), ("rlimit_nproc", [1, 1]), ("rlimit_nproc", None),
    ("nice", 0), ("affinity", [2, 3]), ("rlimit_cpu", [1500, 1501]),
    ("rlimit_as", [3072 * wire.MIB, 4096 * wire.MIB])])
def test_worker_rejects_weakened_task_and_os_limits(field, value):
    row = sample("worker", 10)
    row["readback"][field] = value
    with pytest.raises(ValueError):
        wire.validate_role_sample(row, "worker", profile=PROFILE)


@pytest.mark.parametrize("field,value", [
    ("children_cpu_diagnostic_seconds", 0.01), ("cpu_self_seconds", 1500),
    ("address_space_bytes", 3072 * wire.MIB + 1)])
def test_worker_rejects_children_and_exhausted_resources(field, value):
    row = sample("worker", 10)
    row[field] = value
    with pytest.raises(ValueError):
        wire.validate_role_sample(row, "worker", profile=PROFILE)


def test_helpers_charge_remaining_reserve_and_require_same_task_guards():
    row = sample("coordinator", 11)
    first = {"cpu_seconds": 10, "address_space_bytes": 32 * wire.MIB,
             "cpu_self_seconds": 2.25, "rlimit_nproc": [0, 0], "nice": 10, "affinity": [2]}
    second = {**first, "cpu_seconds": 7, "cpu_self_seconds": 1.25}
    row["helpers"] = {"cpu_seconds": 3.5, "count": 2, "readbacks": [first, second]}
    wire.validate_role_sample(row, "coordinator", profile=PROFILE)
    for changes in ({"cpu_seconds": 10}, {"cpu_self_seconds": 7}, {"rlimit_nproc": [1, 1]},
                    {"nice": 0}, {"affinity": [3]}, {"address_space_bytes": 33 * wire.MIB}):
        bad = deepcopy(row)
        bad["helpers"]["readbacks"][1].update(changes)
        with pytest.raises(ValueError):
            wire.validate_role_sample(bad, "coordinator", profile=PROFILE)
    row["helpers"]["cpu_seconds"] = 0
    with pytest.raises(ValueError, match="cumulative CPU receipt"):
        wire.validate_role_sample(row, "coordinator", profile=PROFILE)


@pytest.mark.parametrize("role", ["worker", "coordinator", "controller"])
def test_apply_limits_retains_task_guards_without_changing_host_limits(monkeypatch, role):
    calls = []
    monkeypatch.setattr(wire.resource, "setrlimit", lambda kind, limits: calls.append((kind, limits)))
    monkeypatch.setattr(wire, "_schedule_role", lambda: calls.append("schedule"))
    monkeypatch.setattr(wire, "require_unprivileged_single_task", lambda: calls.append("tasks"))
    wire.apply_role_limits(role, profile=PROFILE)
    caps = wire.parent_limits(role, profile=PROFILE)
    assert calls == [(wire.resource.RLIMIT_CPU, (caps["cpu_seconds"],) * 2),
                     (wire.resource.RLIMIT_AS, (caps["address_space_bytes"],) * 2),
                     "schedule", "tasks", *([(wire.resource.RLIMIT_NPROC, (0, 0))] if role == "worker" else [])]


def test_controller_preparation_preserves_soft_parent_hard_child_caps_and_ef_entry(monkeypatch):
    calls = []
    monkeypatch.setattr(wire, "require_unprivileged_single_task", lambda: calls.append("tasks"))
    monkeypatch.setattr(wire.signal, "signal", lambda *args: calls.append(args))
    monkeypatch.setattr(wire.resource, "setrlimit", lambda kind, limits: calls.append((kind, limits)))
    monkeypatch.setattr(wire, "_schedule_role", lambda: calls.append("schedule"))
    wire.prepare_local_model_controller(PROFILE.id)
    assert calls == ["tasks", (wire.signal.SIGXCPU, wire.signal.SIG_DFL),
                     (wire.resource.RLIMIT_CPU, (115, 1500)),
                     (wire.resource.RLIMIT_AS, (480 * wire.MIB, 3072 * wire.MIB)), "schedule"]
    calls.clear()
    for bad in (wire.ORDINARY300, wire.ED_H1_600, replace(PROFILE)):
        with pytest.raises(ValueError):
            wire.prepare_local_model_controller(bad)
        assert calls == []
    monkeypatch.setattr(wire, "prepare_local_model_controller", calls.append)
    wire.prepare_ef_controller()
    assert calls == [wire.EF_LOCAL_SEQUENCE]


def test_role_sample_records_ei_task_and_worker_nproc(monkeypatch):
    monkeypatch.setattr(wire, "require_unprivileged_single_task", lambda: None)
    monkeypatch.setattr(wire.resource, "getrusage", lambda *_: SimpleNamespace(ru_utime=0, ru_stime=0))
    monkeypatch.setattr(wire.resource, "getrlimit", lambda *_: (0, 0))
    monkeypatch.setattr(wire.os, "getpriority", lambda *_: 10)
    monkeypatch.setattr(wire.os, "sched_getaffinity", lambda *_: {2})
    monkeypatch.setattr(wire.Path, "read_text", lambda path: "1000" if path.name == "oom_score_adj" else "1 1")
    for role in PROFILE.role_limits():
        row = wire.role_sample(role, profile=PROFILE)
        assert row["resource_profile"] == PROFILE.id
        assert row["readback"]["tasks"] == 1
        assert ("rlimit_nproc" in row["readback"]) == (role == "worker")
        if role == "worker":
            assert row["readback"]["rlimit_nproc"] == [0, 0]


def test_ei_wall_deadline_uses_approved_absolute_window(monkeypatch):
    calls = []
    monkeypatch.setattr(wire.time, "time", lambda: 1000)
    monkeypatch.setattr(wire.signal, "signal", lambda *args: calls.append(args))
    monkeypatch.setattr(wire.signal, "setitimer", lambda *args: calls.append(args))
    wire.fixed_deadline(4600, profile=PROFILE)
    assert calls[1] == (wire.signal.ITIMER_REAL, 3600)
    with pytest.raises(TimeoutError, match="local model"):
        calls[0][1]()
    for deadline in (1000, 4601):
        with pytest.raises(ValueError):
            wire.fixed_deadline(deadline, profile=PROFILE)


def test_ei_budget_uses_exact_partition_and_preserves_scope_in_receipt(monkeypatch):
    machine = FakeResources(monkeypatch)
    assert rb.EI_WORKER_SCOPE == "EI-remaining-model-operator-2026-11-09"
    budget = rb.ResearchBudget(**WORKER, operator_cpu_scope=rb.EI_WORKER_SCOPE)
    assert machine.set_calls == [(rb.resource.RLIMIT_CPU, (1500, 1500)),
                                 (rb.resource.RLIMIT_AS, (3072 * wire.MIB,) * 2)]
    machine.consume(2, 1)
    receipt = budget.finish()
    assert receipt["operator_cpu_scope"] == rb.EI_WORKER_SCOPE
    assert receipt["scope"] == "single_process_including_threads"
    assert receipt["status"] == "complete"


@pytest.mark.parametrize("change", [{"cpu_seconds": 1499}, {"cpu_seconds": 1501},
    {"address_space_bytes": 3072 * wire.MIB - 1}, {"address_space_bytes": 3072 * wire.MIB + 1},
    {"wall_seconds": 3599}, {"wall_seconds": 3601},
    {"cpu_seconds": 180, "address_space_bytes": 352 * wire.MIB, "wall_seconds": 1200}])
def test_ei_scope_refuses_any_other_partition_before_os_changes(monkeypatch, change):
    machine = FakeResources(monkeypatch)
    with pytest.raises(ValueError, match="exact registered limits"):
        rb.ResearchBudget(**{**WORKER, **change}, operator_cpu_scope=rb.EI_WORKER_SCOPE)
    assert machine.set_calls == []


@pytest.mark.parametrize("scope", [None, "EB-EA-A-operator-2026-10-28", "EB-EA-B-operator-2026-10-28",
    "EC-P01-P03-operator-2026-10-29", "ED-H1-operator-2026-10-31"])
def test_ordinary_and_older_scopes_cannot_inherit_ei_grant(monkeypatch, scope):
    machine = FakeResources(monkeypatch)
    with pytest.raises(ValueError, match="cpu_seconds"):
        rb.ResearchBudget(**WORKER, operator_cpu_scope=scope)
    assert machine.set_calls == []


def test_equal_ef_partition_retains_its_own_scope(monkeypatch):
    FakeResources(monkeypatch)
    receipt = rb.ResearchBudget(**WORKER, operator_cpu_scope=rb.EF_WORKER_SCOPE).finish()
    assert receipt["operator_cpu_scope"] == rb.EF_WORKER_SCOPE
    assert receipt["operator_cpu_scope"] != rb.EI_WORKER_SCOPE
