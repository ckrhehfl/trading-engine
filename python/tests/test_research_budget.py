"""Synthetic budget checks plus a bounded Linux hard-limit subprocess."""

from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("resource")

from research import research_budget as rb


EB_CPU_SCOPES = (
    "EB-EA-A-operator-2026-10-28",
    "EB-EA-B-operator-2026-10-28",
)
EC_CPU_SCOPE = "EC-P01-P03-operator-2026-10-29"
ED_CPU_SCOPE = "ED-H1-operator-2026-10-31"


class FakeResources:
    def __init__(self, monkeypatch):
        self.pid = 81
        self.wall = 1000.0
        self.process = 8.0
        self.user = 6.0
        self.system = 2.0
        self.rss = 32 * 1024
        self.address_space = 64 * 1024**2
        self.nice = 0
        self.limits = {
            rb.resource.RLIMIT_CPU: (rb.resource.RLIM_INFINITY,) * 2,
            rb.resource.RLIMIT_AS: (rb.resource.RLIM_INFINITY,) * 2,
        }
        self.set_calls = []
        monkeypatch.setattr(rb.os, "getpid", lambda: self.pid)
        monkeypatch.setattr(rb.os, "getpriority", lambda *_: self.nice)
        monkeypatch.setattr(rb.os, "setpriority", self.setpriority)
        monkeypatch.setattr(rb.time, "monotonic", lambda: self.wall)
        monkeypatch.setattr(rb.time, "process_time", lambda: self.process)
        monkeypatch.setattr(rb.resource, "getrusage", self.getrusage)
        monkeypatch.setattr(rb.resource, "getrlimit", lambda kind: self.limits[kind])
        monkeypatch.setattr(rb.resource, "setrlimit", self.setrlimit)
        monkeypatch.setattr(rb, "_address_space_bytes", lambda: self.address_space)

    def getrusage(self, kind):
        assert kind == rb.resource.RUSAGE_SELF
        return SimpleNamespace(ru_utime=self.user, ru_stime=self.system, ru_maxrss=self.rss)

    def setrlimit(self, kind, values):
        self.set_calls.append((kind, values))
        self.limits[kind] = values

    def setpriority(self, kind, who, value):
        assert (kind, who) == (rb.os.PRIO_PROCESS, 0)
        self.nice = value

    def consume(self, user, system=0, wall=None):
        self.user += user
        self.system += system
        self.process += user + system
        self.wall += user + system if wall is None else wall


@pytest.fixture
def machine(monkeypatch):
    return FakeResources(monkeypatch)


def test_receipt_separates_absolute_usage_from_one_unchanged_baseline(machine):
    budget = rb.ResearchBudget()
    assert machine.set_calls == [
        (rb.resource.RLIMIT_CPU, (300, 300)),
        (rb.resource.RLIMIT_AS, (512 * 1024**2,) * 2),
    ]
    machine.consume(4, 1, wall=12)
    producer = budget.checkpoint("after_producer")
    machine.consume(2, 1, wall=4)
    verified = budget.finish()
    assert producer["status"] == "within_budget"
    assert verified["status"] == "complete"
    assert verified["baseline"] == producer["baseline"]
    assert verified["absolute"]["cpu_process_seconds"] == 16
    assert verified["absolute"]["cpu_rusage_seconds"] == 16
    assert verified["delta"] == {
        "wall_seconds": 16,
        "cpu_process_seconds": 8,
        "cpu_user_seconds": 6,
        "cpu_system_seconds": 2,
        "cpu_rusage_seconds": 8,
    }
    assert verified["readback"] == {
        "rlimit_cpu": [300, 300],
        "rlimit_as": [512 * 1024**2] * 2,
        "nice": 10,
    }
    assert "operator_cpu_scope" not in verified
    assert json.loads(json.dumps(verified)) == verified


@pytest.mark.parametrize("scope", (*EB_CPU_SCOPES, EC_CPU_SCOPE, ED_CPU_SCOPE))
def test_fixed_operator_scope_sets_cpu_600_and_retains_other_limits(machine, scope):
    budget = rb.ResearchBudget(cpu_seconds=600, operator_cpu_scope=scope)
    assert machine.set_calls == [
        (rb.resource.RLIMIT_CPU, (600, 600)),
        (rb.resource.RLIMIT_AS, (512 * 1024**2,) * 2),
    ]
    receipt = budget.finish()
    assert receipt["operator_cpu_scope"] == scope
    assert receipt["limits"] == {
        "cpu_seconds": 600,
        "address_space_bytes": 512 * 1024**2,
        "wall_seconds": 1200,
        "nice": 10,
    }
    assert receipt["readback"] == {
        "rlimit_cpu": [600, 600],
        "rlimit_as": [512 * 1024**2] * 2,
        "nice": 10,
    }
    assert json.loads(json.dumps(receipt)) == receipt


@pytest.mark.parametrize("scope", ["", "EA-A", "EB-EA-C-operator-2026-10-28",
                                  "EB-EA-A-operator-2026-10-27",
                                  "EC-P01-P04-operator-2026-10-29",
                                  "EC-P01-P03-operator-2026-10-28",
                                  "ED-H2-operator-2026-10-31", "ED-H1-operator-2026-10-30",
                                  "ED-H1-operator-2026-10-31 ",
                                  EC_CPU_SCOPE + " ", True, 600, [], {}])
def test_invalid_operator_scope_is_rejected_before_os_access(machine, monkeypatch, scope):
    monkeypatch.setattr(rb.ResearchBudget, "_sample", lambda: pytest.fail("OS access before validation"))
    with pytest.raises(ValueError, match="operator_cpu_scope"):
        rb.ResearchBudget(cpu_seconds=600, operator_cpu_scope=scope)
    assert machine.set_calls == []
    assert machine.nice == 0


def test_ed_worker480_scope_does_not_raise_as_wall_nice_or_reset_absolute_cpu(machine):
    machine.process, machine.user, machine.system = 470, 460, 10
    budget = rb.ResearchBudget(cpu_seconds=480, address_space_bytes=352 * 1024**2,
                               operator_cpu_scope=ED_CPU_SCOPE)
    receipt = budget.checkpoint("ed_snapshot_or_fit")
    assert receipt["operator_cpu_scope"] == ED_CPU_SCOPE
    assert receipt["limits"] == {"cpu_seconds": 480, "address_space_bytes": 352 * 1024**2,
                                 "wall_seconds": 1200, "nice": 10}
    assert receipt["absolute"]["cpu_process_seconds"] == 470
    assert receipt["readback"]["rlimit_cpu"] == [480, 480]
    machine.consume(11)
    with pytest.raises(rb.BudgetError) as failed:
        budget.checkpoint("after_independent_verification")
    assert failed.value.receipt["operator_cpu_scope"] == ED_CPU_SCOPE
    assert failed.value.receipt["absolute"]["cpu_process_seconds"] == 481


@pytest.mark.parametrize("clock", ["process", "rusage"])
def test_scoped_cpu_boundary_remains_absolute_and_failure_stays_sticky(machine, clock):
    machine.process, machine.user, machine.system = 580, 570, 10
    budget = rb.ResearchBudget(cpu_seconds=600, operator_cpu_scope=EB_CPU_SCOPES[0])
    if clock == "process":
        machine.process = 599
    else:
        machine.user = 589
    receipt = budget.checkpoint("after_producer")
    assert receipt["baseline"]["cpu_process_seconds"] == 580
    if clock == "process":
        machine.process = 600
    else:
        machine.user = 590
    with pytest.raises(rb.BudgetError, match=f"absolute {clock} CPU budget exhausted") as first:
        budget.finish()
    assert first.value.receipt["delta"][f"cpu_{clock}_seconds"] == 20
    assert first.value.receipt["operator_cpu_scope"] == EB_CPU_SCOPES[0]
    machine.process, machine.user, machine.system = 599, 589, 10
    with pytest.raises(rb.BudgetError) as second:
        budget.checkpoint("retry")
    assert second.value is first.value


def test_scoped_producer_and_verifier_share_one_cpu_allowance(machine):
    machine.process, machine.user, machine.system = 580, 570, 10
    budget = rb.ResearchBudget(cpu_seconds=600, operator_cpu_scope=EB_CPU_SCOPES[1])
    machine.consume(10)
    producer = budget.checkpoint("after_producer")
    machine.consume(10)
    with pytest.raises(rb.BudgetError, match="CPU budget exhausted") as caught:
        budget.checkpoint("after_verifier")
    assert caught.value.receipt["baseline"] == producer["baseline"]
    assert caught.value.receipt["absolute"]["cpu_process_seconds"] == 600
    assert caught.value.receipt["delta"]["cpu_process_seconds"] == 20
    assert machine.set_calls.count((rb.resource.RLIMIT_CPU, (600, 600))) == 1


@pytest.mark.parametrize("change, message", [
    ("cpu_soft", "RLIMIT_CPU"), ("cpu_hard", "RLIMIT_CPU"),
    ("address_space", "address space"), ("wall", "wall budget"), ("nice", "nice"),
])
def test_scoped_cpu_allowance_keeps_readback_memory_wall_and_priority_guards(machine, change, message):
    budget = rb.ResearchBudget(cpu_seconds=600, operator_cpu_scope=EB_CPU_SCOPES[0])
    if change == "cpu_soft":
        machine.limits[rb.resource.RLIMIT_CPU] = (601, 600)
    elif change == "cpu_hard":
        machine.limits[rb.resource.RLIMIT_CPU] = (600, 601)
    elif change == "address_space":
        machine.address_space = 512 * 1024**2 + 1
    elif change == "wall":
        machine.wall += 1200
    else:
        machine.nice = 11
    with pytest.raises(rb.BudgetError, match=message):
        budget.finish()


@pytest.mark.parametrize("limits", [(300, 300), (300, 600), (600, 300)])
def test_operator_scope_cannot_raise_an_existing_tighter_cpu_limit(machine, limits):
    machine.limits[rb.resource.RLIMIT_CPU] = limits
    with pytest.raises(rb.BudgetError, match="refusing to raise existing RLIMIT_CPU") as caught:
        rb.ResearchBudget(cpu_seconds=600, operator_cpu_scope=EB_CPU_SCOPES[0])
    assert caught.value.receipt["operator_cpu_scope"] == EB_CPU_SCOPES[0]
    assert machine.set_calls == []
    assert machine.nice == 0


@pytest.mark.parametrize("kwargs", [
    {"cpu_seconds": 601}, {"cpu_seconds": True}, {"cpu_seconds": 0},
    {"address_space_bytes": 513 * 1024**2}, {"wall_seconds": 1201},
])
def test_scoped_constructor_cannot_exceed_other_registered_ceilings(machine, kwargs):
    with pytest.raises(ValueError):
        rb.ResearchBudget(operator_cpu_scope=EB_CPU_SCOPES[0], **kwargs)
    assert machine.set_calls == []


def test_prior_cpu_and_verifier_both_count_toward_the_same_total(machine):
    machine.process, machine.user, machine.system = 280, 270, 10
    budget = rb.ResearchBudget()
    machine.consume(10)
    budget.checkpoint("after_producer")
    machine.consume(11)
    with pytest.raises(rb.BudgetError, match="CPU budget exhausted") as caught:
        budget.finish()
    assert caught.value.receipt["delta"]["cpu_process_seconds"] == 21
    assert caught.value.receipt["absolute"]["cpu_process_seconds"] == 301
    assert caught.value.receipt["status"] == "failed"


@pytest.mark.parametrize("clock", ["process", "rusage", "both"])
def test_recorded_351_second_overrun_cannot_finish(machine, clock):
    budget = rb.ResearchBudget()
    if clock in ("process", "both"):
        machine.process = 351.748990
    if clock in ("rusage", "both"):
        machine.user, machine.system = 342.178401, 9.570589
    with pytest.raises(rb.BudgetError, match="CPU budget exhausted") as caught:
        budget.finish()
    assert caught.value.receipt["stage"] == "finish"
    assert caught.value.receipt["violations"]


@pytest.mark.parametrize("clock", ["process", "rusage"])
def test_exact_cpu_boundary_is_rejected_before_more_work(machine, clock):
    budget = rb.ResearchBudget()
    if clock == "process":
        machine.process = 300
    else:
        machine.user, machine.system = 299, 1
    with pytest.raises(rb.BudgetError, match="CPU budget exhausted"):
        budget.checkpoint("hot_loop")


def test_wall_checkpoint_and_finish_share_the_original_deadline(machine):
    budget = rb.ResearchBudget()
    machine.wall += 1199
    budget.checkpoint("after_producer")
    machine.wall += 1
    with pytest.raises(rb.BudgetError, match="wall budget exhausted"):
        budget.finish()


@pytest.mark.parametrize("change, message", [
    ("cpu_soft", "RLIMIT_CPU"),
    ("cpu_hard", "RLIMIT_CPU"),
    ("as_soft", "RLIMIT_AS"),
    ("as_hard", "RLIMIT_AS"),
    ("nice", "nice"),
    ("pid", "identity"),
    ("address_space", "address space"),
])
def test_finish_rejects_limit_or_identity_drift(machine, change, message):
    budget = rb.ResearchBudget()
    changes = {
        "cpu_soft": (rb.resource.RLIMIT_CPU, (301, 300)),
        "cpu_hard": (rb.resource.RLIMIT_CPU, (300, 301)),
        "as_soft": (rb.resource.RLIMIT_AS, (513 * 1024**2, 512 * 1024**2)),
        "as_hard": (rb.resource.RLIMIT_AS, (512 * 1024**2, 513 * 1024**2)),
    }
    if change in changes:
        kind, values = changes[change]
        machine.limits[kind] = values
    elif change == "nice":
        machine.nice = 11
    elif change == "pid":
        machine.pid += 1
    else:
        machine.address_space = 512 * 1024**2 + 1
    with pytest.raises(rb.BudgetError, match=message):
        budget.finish()


def test_failure_cannot_be_cleared_by_restoring_readings(machine):
    budget = rb.ResearchBudget()
    machine.nice = 11
    with pytest.raises(rb.BudgetError) as first:
        budget.checkpoint("after_producer")
    machine.nice = 10
    with pytest.raises(rb.BudgetError) as second:
        budget.finish()
    assert second.value is first.value
    assert second.value.receipt["stage"] == "after_producer"


@pytest.mark.parametrize("counter", ["wall", "process", "user", "system"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_cpu_or_wall_measurement_fails_closed(machine, counter, value):
    budget = rb.ResearchBudget()
    setattr(machine, counter, value)
    with pytest.raises(rb.BudgetError, match="invalid or decreasing"):
        budget.finish()


@pytest.mark.parametrize("kind", [rb.resource.RLIMIT_CPU, rb.resource.RLIMIT_AS])
def test_existing_tighter_os_limit_is_never_raised(machine, kind):
    machine.limits[kind] = (1, 1)
    with pytest.raises(rb.BudgetError, match="refusing to raise existing"):
        rb.ResearchBudget()
    assert machine.set_calls == []


def test_already_lower_scheduling_priority_is_not_raised(machine):
    machine.nice = 11
    with pytest.raises(rb.BudgetError, match="refusing to raise existing"):
        rb.ResearchBudget()
    assert machine.set_calls == []


def test_setrlimit_noop_is_detected_by_readback(machine, monkeypatch):
    monkeypatch.setattr(rb.resource, "setrlimit", lambda *_: None)
    with pytest.raises(rb.BudgetError, match="readback drift"):
        rb.ResearchBudget()


def test_setup_error_retains_last_measurements(machine, monkeypatch):
    def denied(*_):
        raise PermissionError("synthetic denial")
    monkeypatch.setattr(rb.resource, "setrlimit", denied)
    with pytest.raises(rb.BudgetError, match="limit setup failed") as caught:
        rb.ResearchBudget()
    assert caught.value.receipt["absolute"]["pid"] == machine.pid
    assert caught.value.receipt["readback"]["rlimit_cpu"] == [-1, -1]


def test_measurement_failure_is_sticky_and_keeps_partial_receipt(machine, monkeypatch):
    budget = rb.ResearchBudget()
    def unavailable():
        raise OSError("synthetic /proc failure")
    monkeypatch.setattr(rb, "_address_space_bytes", unavailable)
    with pytest.raises(rb.BudgetError, match="measurement failed") as caught:
        budget.checkpoint("after_verifier")
    assert caught.value.receipt["stage"] == "after_verifier"
    assert caught.value.receipt["absolute"]["cpu_process_seconds"] == 8


def test_finish_is_terminal(machine):
    budget = rb.ResearchBudget()
    budget.finish()
    with pytest.raises(rb.BudgetError, match="already finished"):
        budget.checkpoint("new_pipeline")


@pytest.mark.parametrize("kwargs", [
    {"cpu_seconds": 301}, {"cpu_seconds": 600},
    {"cpu_seconds": 600, "operator_cpu_scope": None},
    {"cpu_seconds": True}, {"cpu_seconds": 0},
    {"address_space_bytes": 513 * 1024**2}, {"address_space_bytes": True},
    {"wall_seconds": 1201}, {"wall_seconds": 0}, {"wall_seconds": float("nan")},
])
def test_constructor_cannot_relax_registered_ceilings(machine, kwargs):
    with pytest.raises(ValueError):
        rb.ResearchBudget(**kwargs)
    assert machine.set_calls == []


@pytest.mark.skipif(sys.platform != "linux", reason="Linux resource limit readback")
@pytest.mark.parametrize("scope", [EB_CPU_SCOPES[0], EC_CPU_SCOPE])
def test_actual_scoped_cpu_600_os_readback_in_a_bounded_child(scope):
    code = f"""
import json
from research.research_budget import ResearchBudget
budget = ResearchBudget(cpu_seconds=600, operator_cpu_scope={scope!r})
print(json.dumps(budget.finish()), flush=True)
"""
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    receipt = json.loads(child.stdout)
    assert receipt["status"] == "complete"
    assert receipt["operator_cpu_scope"] == scope
    assert receipt["readback"] == {
        "rlimit_cpu": [600, 600],
        "rlimit_as": [512 * 1024**2] * 2,
        "nice": 10,
    }


def test_ec_account_amendment_changes_only_the_approved_execution_cpu():
    root = Path(__file__).resolve().parents[2]
    directory = root / "configs/research/discovery"
    original = json.loads((directory / "intraday-campaign-account-v1.json").read_text())
    amended = json.loads((directory / "intraday-campaign-account-v2.json").read_text())
    assert original["id"] == "intraday-campaign-account-v1"
    assert amended.pop("id") == "intraday-campaign-account-v2"
    assert amended.pop("version") == 2
    assert amended.pop("supersedes_for_execution") == original["id"]
    assert amended.pop("scientific_parameters_changed") is False
    assert amended.pop("resource_authorization")
    original.pop("id")
    assert original["execution_contract"]["cpu_seconds_including_independent_verification"] == 300
    assert "operator_cpu_scope" not in original["execution_contract"]
    assert amended["execution_contract"].pop("operator_cpu_scope") == EC_CPU_SCOPE
    assert amended["execution_contract"]["cpu_seconds_including_independent_verification"] == 600
    amended["execution_contract"]["cpu_seconds_including_independent_verification"] = 300
    assert amended == original


@pytest.mark.skipif(sys.platform != "linux", reason="Linux RLIMIT_CPU terminal behavior")
def test_actual_tiny_hard_cpu_limit_kills_even_without_checkpoints():
    """Synthetic busy loop only; the parent has an independent eight-second cap."""
    code = """
import json
from research.research_budget import ResearchBudget
budget = ResearchBudget(cpu_seconds=1, wall_seconds=5)
print(json.dumps(budget.checkpoint('before_synthetic_spin')), flush=True)
while True:
    pass
"""
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert child.returncode == -signal.SIGKILL, child.stderr
    receipt = json.loads(child.stdout)
    assert receipt["readback"]["rlimit_cpu"] == [1, 1]
    assert receipt["status"] == "within_budget"
    assert receipt["absolute"]["cpu_process_seconds"] < 1
