"""Source and tiny invented-only ordinary bridge tests; no actual/GCP access."""
from dataclasses import FrozenInstanceError, replace
from copy import deepcopy
from io import BytesIO
import json
import base64
from pathlib import Path
import signal
import subprocess
import time

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


ROOT = Path(__file__).resolve().parents[2]
NAMES = tuple(sorted(("python/research/__init__.py", "python/research/experiment_log.py",
    "python/research/research_budget.py", "python/research/intraday_campaign_wsl_transport.py",
    "python/research/intraday_campaign_wsl_bridge.py", "configs/invented.json", "var/invented-driver.py")))
GEOMETRY = wire.encode({"sessions": 3, "targets": 3})
BOUNDS = wire.encode({"A": {"D+1": 3, "D+2": 3}})
WINDOW = wire.encode({"symbol": "INVENTED", "interval": "1m", "start": "2020-01-01", "end": "2020-01-03"})
ED_SPECIFICATION = "configs/research/discovery/intraday-ed-h1-common-comparison-v1.json"


def plan_at(root, parent):
    return bridge.UnitPlan(unit_id="invented-ordinary", specification_path="configs/invented.json",
        source_names=NAMES, source_roster_sha256=wire.digest(wire.encode(list(NAMES))),
        input_pins=tuple(bridge.InputPin(name, "/invented-actual/" + name, "a" * 64,
            (512 if name == "CC" else 64) * wire.MIB) for name in ("EA-A", "EA-B", "CC")),
        decoded_stream_sha256="b" * 64, window=WINDOW, full_geometry=GEOMETRY, candidate_bounds=BOUNDS,
        private_entrypoint="var/invented-driver.py", local_worktree=root, remote_worktree=root,
        actual_cache=parent / "cache" / "actual", actual_output=parent / "output" / "actual",
        actual_diagnostics=parent / "diagnostics" / "actual", canonical_journal=parent / "canonical" / "experiments.jsonl",
        global_marker=parent / "canonical" / "ordinary-unresolved.json",
        research_locks=(parent / "canonical" / "research.lock", parent / "output" / "campaign.lock"),
        invented_cache_parent=parent / "cache", invented_output_parent=parent / "output",
        invented_diagnostics_parent=parent / "diagnostics", output_filename="result.json", output_max_bytes=4096,
        ssh_argv=("ssh", "-i", "/invented-unused/key", "-o", "BatchMode=yes", "invented-unused.invalid"),
        remote_prefix=("python3", "-I", "-S", str(root / "var/invented-driver.py"), "coordinator"))


def ed_plan_at(root, parent, input_contract=wire.ED_INPUTS_CONTRACT, unit_id=None):
    plan = plan_at(root, parent)
    names = tuple(sorted((set(plan.source_names) - {plan.specification_path}) | {ED_SPECIFICATION}))
    bounds = (wire.ED_H1_INPUT_BOUNDS if input_contract == wire.ED_INPUTS_CONTRACT else
              wire.ED_H1_SNAPSHOT_INPUT_BOUNDS)
    return replace(plan, unit_id=unit_id or ("ED-H1-SNAPSHOT" if input_contract == wire.ED_INPUTS_CONTRACT else
                                            "ED-H1-RULES"), specification_path=ED_SPECIFICATION,
        source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names))),
        input_pins=tuple(bridge.InputPin(name, "/invented-native/ed-h1/original/" + name, "a" * 64, maximum)
                         for name, maximum in bounds), input_contract=input_contract)


def envelope_for(plan, *, geometry=None):
    return bridge.InputEnvelope(tuple(bridge.InputFile(**pin.metadata(1)) for pin in plan.input_pins),
                                plan.decoded_stream_sha256, geometry or plan.full_geometry, plan.input_contract)


def sample(role, profile=bridge.PROFILE):
    caps = profile.role_limits()[role]
    parent = wire.parent_limits(role, profile=profile)
    return {"role": role, "pid": 123, "resource_profile": profile.id, "limits": caps,
        "parent_limits": parent, "cpu_self_seconds": 0.01, "children_cpu_diagnostic_seconds": 99,
        "cpu_counting": "invented sample; no SSH qualification", "address_space_bytes": 8 * wire.MIB,
        "helpers": {"cpu_seconds": 0, "count": 0, "readbacks": []},
        "readback": {"rlimit_cpu": [parent["cpu_seconds"]] * 2,
            "rlimit_as": [parent["address_space_bytes"]] * 2, "nice": 10,
            "affinity": [0], "oom_score_adj": "1000"}}


def config_at(plan, attempt="tiny"):
    name = plan.unit_id + "-invented-" + attempt
    result = {"invented_only": True, "resource_profile": plan.resource_profile, "automatic_retries": 0,
        "wall_seconds": 1200, "sessions": 2, "source_hashes": {name: "c" * 64 for name in plan.source_names},
        "merge_commit": "d" * 40, "tree": "e" * 40, "reviewed_commit": "f" * 40,
        "attempt": attempt, "deadline_epoch": time.time() + 45, "local_pipe_test": True,
        "cache": str(plan.invented_cache_parent / name), "output": str(plan.invented_output_parent / name),
        "journal": str(plan.invented_output_parent / name / "invented-experiments.jsonl"),
        "diagnostics": str(plan.invented_diagnostics_parent / name),
        "test_ssh_sample": sample("ssh_client", bridge.profile_for_plan(plan))}
    if plan.resource_profile == wire.ED_H1_600.id:
        result["unit_id"] = plan.unit_id
    return result


def test_plan_is_frozen_and_rejects_roster_and_mutable_metadata(tmp_path):
    plan = plan_at(tmp_path / "repo", tmp_path)
    bridge.validate_plan(plan)
    with pytest.raises(FrozenInstanceError):
        plan.unit_id = "changed"
    for changed in (replace(plan, source_names=NAMES[:-1]), replace(plan, full_geometry={"sessions": 3}),
                    replace(plan, source_roster_sha256="0" * 64), replace(plan, input_pins=list(plan.input_pins))):
        with pytest.raises(ValueError):
            bridge.validate_plan(changed)


def test_default_ec_envelope_retains_exact_four_key_payload(tmp_path):
    plan = plan_at(tmp_path / "repo", tmp_path)
    envelope = envelope_for(plan)
    expected = {"kind": "INPUTS", "inputs": [pin.metadata(1) for pin in plan.input_pins],
                "stream_sha256": plan.decoded_stream_sha256, "geometry": {"sessions": 3, "targets": 3}}
    assert envelope.input_contract == plan.input_contract == "ec-original-three-v1"
    assert wire.encode(envelope.payload()) == wire.encode(expected)
    assert bridge.InputEnvelope.from_payload(expected) == envelope
    with pytest.raises(ValueError, match="envelope fields"):
        bridge.InputEnvelope.from_payload({**expected, "input_contract": wire.EC_INPUT_CONTRACT})


@pytest.mark.parametrize("input_contract,unit_id", [(wire.ED_INPUTS_CONTRACT, "ED-H1-SNAPSHOT"),
    (wire.ED_SNAPSHOT_CONTRACT, "ED-H1-RULES"), (wire.ED_SNAPSHOT_CONTRACT, "ED-H1-LIGHTGBM"),
    (wire.ED_SNAPSHOT_CONTRACT, "ED-H1-LIGHTGBM-ABLATION")])
def test_ed_plan_envelope_and_bounded_header_are_explicit(tmp_path, input_contract, unit_id):
    plan = ed_plan_at(tmp_path / "repo", tmp_path, input_contract, unit_id)
    bridge.validate_plan(plan)
    envelope = envelope_for(plan)
    payload = envelope.payload()
    assert payload["input_contract"] == input_contract
    assert bridge.InputEnvelope.from_payload(payload, input_contract=input_contract) == envelope
    largest = replace(envelope, manifest=tuple(bridge.InputFile(**pin.metadata(pin.maximum))
                                              for pin in plan.input_pins))
    stream = BytesIO()
    wire.send_header(stream, largest.payload("PROFILE_INPUTS"))
    assert len(wire.encode(largest.payload("PROFILE_INPUTS"))) <= 16384 == wire.HEADER_MAX
    stream.seek(0)
    assert bridge.InputEnvelope.from_payload(wire.read_header(stream), "PROFILE_INPUTS",
                                             input_contract=input_contract) == largest
    with pytest.raises(ValueError, match="envelope fields"):
        bridge.InputEnvelope.from_payload(payload)
    without_contract = {key: value for key, value in payload.items() if key != "input_contract"}
    with pytest.raises(ValueError, match="three registered inputs"):
        bridge.InputEnvelope.from_payload(without_contract)
    with pytest.raises(ValueError, match="envelope fields"):
        bridge.InputEnvelope.from_payload(without_contract, input_contract=input_contract)
    for contract in ("unknown", wire.ED_SNAPSHOT_CONTRACT if input_contract == wire.ED_INPUTS_CONTRACT else
                     wire.ED_INPUTS_CONTRACT):
        with pytest.raises(ValueError):
            bridge.InputEnvelope.from_payload(payload, input_contract=contract)


@pytest.mark.parametrize("input_contract", [wire.ED_INPUTS_CONTRACT, wire.ED_SNAPSHOT_CONTRACT])
def test_ed_plan_rejects_wrong_unit_spec_contract_pins_and_header(tmp_path, input_contract):
    plan = ed_plan_at(tmp_path / "repo", tmp_path, input_contract)
    for changed in (replace(plan, unit_id="ED-H1-UNREGISTERED"),
        replace(plan, unit_id="ED-H1-RULES" if input_contract == wire.ED_INPUTS_CONTRACT else "ED-H1-SNAPSHOT"),
        replace(plan, specification_path="configs/invented.json"), replace(plan, input_contract="unknown"),
        replace(plan, input_contract=wire.EC_INPUT_CONTRACT), replace(plan, output_filename=plan.input_pins[0].name),
        replace(plan, input_pins=(replace(plan.input_pins[0], sha256="bad"), *plan.input_pins[1:])),
        replace(plan, input_pins=(replace(plan.input_pins[0], original_path="relative"), *plan.input_pins[1:])),
        replace(plan, input_pins=(replace(plan.input_pins[0], maximum=1), *plan.input_pins[1:])),
        replace(plan, input_pins=(replace(plan.input_pins[0], original_path="/" + "p" * 16384), *plan.input_pins[1:]))):
        with pytest.raises(ValueError):
            bridge.validate_plan(changed)
    legacy = plan_at(tmp_path / "repo", tmp_path)
    with pytest.raises(ValueError, match="explicit ED"):
        bridge.validate_plan(replace(legacy, unit_id=plan.unit_id))


@pytest.mark.parametrize("input_contract", [wire.ED_INPUTS_CONTRACT, wire.ED_SNAPSHOT_CONTRACT])
def test_ed_actual_metadata_requires_same_started_ack_before_any_stat(monkeypatch, tmp_path, input_contract):
    plan = ed_plan_at(tmp_path / "repo", tmp_path, input_contract)
    attempts = []
    def invented_stat(path):
        attempts.append(path)
        return type("InventedStat", (), {"st_size": 1})()
    monkeypatch.setattr(wire, "regular", invented_stat)
    phases = wire.WorkerPhases({"source": "invented"})
    with pytest.raises(ValueError, match="before durable"):
        bridge.actual_input_metadata(plan, phases)
    assert attempts == []
    phases.accept_started({"kind": "STARTED", "identity": {"source": "invented"}, "run_id": "invented",
                           "record_sha256": "a" * 64})
    envelope = bridge.actual_input_metadata(plan, phases)
    assert attempts == [Path(pin.original_path) for pin in plan.input_pins]
    assert envelope == envelope_for(plan)
    bridge._actual_envelope(envelope, plan)
    for changed in (replace(envelope, stream_sha256="c" * 64), replace(envelope, geometry=wire.encode({"sessions": 1})),
        replace(envelope, manifest=(replace(envelope.manifest[0], original_path="/different/input"),
                                    *envelope.manifest[1:])),
        replace(envelope, manifest=(replace(envelope.manifest[0], sha256="c" * 64), *envelope.manifest[1:])),
        replace(envelope, input_contract=wire.EC_INPUT_CONTRACT)):
        with pytest.raises(ValueError, match="actual source"):
            bridge._actual_envelope(changed, plan)


@pytest.mark.parametrize("input_contract", [wire.ED_INPUTS_CONTRACT, wire.ED_SNAPSHOT_CONTRACT])
def test_ed_keeps_existing_geometry_count_output_and_verification_guards(tmp_path, input_contract):
    plan = ed_plan_at(tmp_path / "repo", tmp_path, input_contract)
    envelope = envelope_for(plan, geometry=wire.encode({"sessions": 2, "targets": 2}))
    bridge._invented_envelope(envelope, plan, 2)
    receipt = bridge.OutputReceipt("a" * 64, 4, envelope.geometry, wire.encode({"verified": True}),
                                   wire.encode({"A": {"D+1": 2, "D+2": 1}}))
    bridge.validate_result(receipt.payload(), envelope, plan)
    for changed in (replace(receipt, output_bytes=plan.output_max_bytes + 1),
        replace(receipt, independent_verification=wire.encode({"verified": False})),
        replace(receipt, geometry=plan.full_geometry), replace(receipt, candidate_counts=wire.encode({"A": {"D+1": 4}}))):
        with pytest.raises(ValueError):
            bridge.validate_result(changed.payload(), envelope, plan)
    for changed in (replace(envelope, geometry=wire.encode({"sessions": 2, "targets": 4})),
                    replace(envelope, input_contract=wire.EC_INPUT_CONTRACT)):
        with pytest.raises(ValueError):
            bridge._invented_envelope(changed, plan, 2)
    with pytest.raises(ValueError, match="result input contract"):
        bridge.validate_result(receipt.payload(), replace(envelope, input_contract=wire.EC_INPUT_CONTRACT), plan)


@pytest.mark.parametrize("change", ["missing", "extra", "changed"])
def test_source_missing_extra_and_changed_are_rejected_before_identity(tmp_path, change):
    root = tmp_path / "repo"
    plan = plan_at(root, tmp_path)
    config = config_at(plan)
    for name in NAMES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"invented source\n")
        config["source_hashes"][name] = wire.digest(path.read_bytes())
    if change == "missing":
        config["source_hashes"].pop(NAMES[0])
    elif change == "extra":
        config["source_hashes"]["python/extra.py"] = "a" * 64
    else:
        (root / NAMES[0]).write_bytes(b"changed source\n")
    with pytest.raises(ValueError, match="source map|source manifest"):
        bridge.verify_sources(config, plan, root, "local")


def test_before_started_actual_metadata_has_zero_filesystem_access(monkeypatch, tmp_path):
    plan = plan_at(tmp_path / "repo", tmp_path)
    attempts = []
    monkeypatch.setattr(wire, "regular", lambda path: attempts.append(path))
    with pytest.raises(ValueError, match="before durable"):
        bridge.actual_input_metadata(plan, wire.WorkerPhases({}))
    assert attempts == []


def test_actual_rejects_local_pipe_and_arbitrary_paths_before_any_input(tmp_path):
    plan = replace(plan_at(tmp_path / "repo", tmp_path), actual_runner_ready=True)
    config = config_at(plan)
    config.update(invented_only=False, cache=str(plan.actual_cache), output=str(plan.actual_output),
        journal=str(plan.canonical_journal), diagnostics=str(plan.actual_diagnostics))
    with pytest.raises(ValueError, match="local pipe"):
        bridge.validate_config(config, plan)
    config["local_pipe_test"] = False
    bridge.validate_config(config, plan)
    for field in ("cache", "output", "journal", "diagnostics"):
        changed = {**config, field: str(tmp_path / "escape")}
        with pytest.raises(ValueError, match="fixed actual"):
            bridge.validate_config(changed, plan)


def test_hooks_result_requires_independent_verification_and_full_candidate_coordinates(tmp_path):
    plan = plan_at(tmp_path / "repo", tmp_path)
    envelope = bridge.InputEnvelope(tuple(bridge.InputFile(**pin.metadata(1)) for pin in plan.input_pins),
                                    plan.decoded_stream_sha256, GEOMETRY)
    receipt = bridge.OutputReceipt("a" * 64, 4, GEOMETRY, wire.encode({"verified": True}),
                                   wire.encode({"A": {"D+1": 2, "D+2": 1}}))
    bridge.validate_result(receipt.payload(), envelope, plan)
    for changed in (replace(receipt, independent_verification=wire.encode({"verified": False})),
                    replace(receipt, candidate_counts=wire.encode({"A": {"D+1": 2}})),
                    replace(receipt, candidate_counts=wire.encode({"A": {"D+1": 4, "D+2": 1}}))):
        with pytest.raises(ValueError):
            bridge.validate_result(changed.payload(), envelope, plan)


def test_ordinary_budget_uses_no_core_scope(monkeypatch):
    from research import research_budget
    passed = []
    monkeypatch.setattr(research_budget, "ResearchBudget", lambda **kwargs: passed.append(kwargs))
    bridge.worker_budget()
    assert passed == [{"cpu_seconds": 180, "address_space_bytes": 352 * wire.MIB}]


@pytest.mark.parametrize("unit_id,input_contract", [("ED-H1-SNAPSHOT", wire.ED_INPUTS_CONTRACT),
    ("ED-H1-RULES", wire.ED_SNAPSHOT_CONTRACT), ("ED-H1-LIGHTGBM", wire.ED_SNAPSHOT_CONTRACT),
    ("ED-H1-LIGHTGBM-ABLATION", wire.ED_SNAPSHOT_CONTRACT)])
def test_ed_600_profile_is_immutable_and_scoped_to_the_four_registered_plans(tmp_path, unit_id, input_contract):
    plan = replace(ed_plan_at(tmp_path / "repo", tmp_path, input_contract, unit_id),
                   resource_profile=wire.ED_H1_600.id)
    bridge.validate_plan(plan)
    assert bridge.profile_for_plan(plan) is wire.ED_H1_600
    bridge.validate_config(config_at(plan), plan)
    assert bridge.PROFILE is wire.ORDINARY300
    assert plan_at(tmp_path / "repo", tmp_path).resource_profile == wire.ORDINARY300.id
    with pytest.raises(FrozenInstanceError):
        plan.resource_profile = wire.ORDINARY300.id


@pytest.mark.parametrize("change", ["ec", "wrong_spec", "wrong_unit", "old_contract", "core", "unknown", "mutable"])
def test_ed_cpu600_cannot_be_borrowed_by_another_plan(tmp_path, change):
    plan = replace(ed_plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    if change == "ec":
        plan = replace(plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    elif change == "wrong_spec":
        plan = replace(plan, specification_path="configs/invented.json")
    elif change == "wrong_unit":
        plan = replace(plan, unit_id="ED-H2-SNAPSHOT")
    elif change == "old_contract":
        plan = replace(plan, input_contract=wire.EC_INPUT_CONTRACT)
    elif change == "core":
        plan = replace(plan, resource_profile=wire.CORE600.id)
    elif change == "unknown":
        plan = replace(plan, resource_profile="unknown")
    else:
        plan = replace(plan, resource_profile=wire.ED_H1_600)
    with pytest.raises(ValueError):
        bridge.profile_for_plan(plan)


def test_ed_worker_budget_has_only_the_approved480_scope_and_unchanged_as(monkeypatch, tmp_path):
    from research import research_budget
    plan = replace(ed_plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    passed = []
    monkeypatch.setattr(research_budget, "ResearchBudget", lambda **kwargs: passed.append(kwargs))
    bridge.worker_budget(plan)
    bridge.worker_budget(replace(plan, resource_profile=wire.ORDINARY300.id))
    assert passed == [
        {"cpu_seconds": 480, "address_space_bytes": 352 * wire.MIB,
         "operator_cpu_scope": "ED-H1-operator-2026-10-31"},
        {"cpu_seconds": 180, "address_space_bytes": 352 * wire.MIB}]


@pytest.mark.parametrize("change", ["config_ordinary", "plan_ordinary", "other_ed_unit"])
def test_config_cannot_override_a_plans_approved_profile_or_unit(tmp_path, change):
    plan = replace(ed_plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    config = config_at(plan)
    if change == "config_ordinary":
        config["resource_profile"] = wire.ORDINARY300.id
    elif change == "plan_ordinary":
        plan = replace(plan, resource_profile=wire.ORDINARY300.id)
    else:
        config["unit_id"] = "ED-H1-RULES"
    with pytest.raises(ValueError, match="profile|unit/plan"):
        bridge.validate_config(config, plan)


def test_worker_bootstrap_applies480_before_plan_load_without_an_initial180(monkeypatch, tmp_path):
    plan = replace(ed_plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    config = config_at(plan)
    events = []
    monkeypatch.setattr(wire, "fixed_deadline", lambda *args: None)
    monkeypatch.setattr(wire, "apply_role_limits", lambda role, *, profile: events.append(
        ("caps", role, profile.id, profile.role_limits()[role]["cpu_seconds"])))
    monkeypatch.setattr(bridge, "restrict_worker_network", lambda: None)
    def load_plan(value):
        events.append(("load_plan",))
        return plan
    def dispatched(*args):
        events.append(("worker",))
        return 7
    monkeypatch.setattr(bridge, "worker", dispatched)
    hooks = bridge.UnitHooks(load_plan, lambda *args: pytest.fail("generation during admission"),
                             lambda *args: pytest.fail("computation during admission"))
    assert bridge.run_role("worker", config, hooks, root=plan.local_worktree,
                            incoming=BytesIO(), outgoing=BytesIO()) == 7
    assert events == [("caps", "worker", wire.ED_H1_600.id, 480), ("load_plan",), ("worker",)]


@pytest.mark.parametrize("change", ["ec_unit", "missing_unit", "missing_spec", "invalid_spec_sha", "core", "unknown"])
def test_invalid_bootstrap_cannot_load_a_plan_apply_caps_or_open_inputs(monkeypatch, tmp_path, change):
    plan = replace(ed_plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    config = config_at(plan)
    if change == "ec_unit":
        config["unit_id"] = "EC-P09"
    elif change == "missing_unit":
        config.pop("unit_id")
    elif change == "missing_spec":
        config["source_hashes"].pop(ED_SPECIFICATION)
    elif change == "invalid_spec_sha":
        config["source_hashes"][ED_SPECIFICATION] = "invalid"
    elif change == "core":
        config["resource_profile"] = wire.CORE600.id
    else:
        config["resource_profile"] = "unknown"
    monkeypatch.setattr(wire, "apply_role_limits", lambda *args, **kwargs: pytest.fail("unadmitted role cap"))
    hooks = bridge.UnitHooks(lambda *args: pytest.fail("unadmitted source plan"),
        lambda *args: pytest.fail("unadmitted generation"), lambda *args: pytest.fail("unadmitted computation"))
    with pytest.raises(ValueError):
        bridge.run_role("worker", config, hooks, root=plan.local_worktree, incoming=BytesIO(), outgoing=BytesIO())


def test_valid_bootstrap_but_mismatched_plan_is_rejected_before_worker_dispatch(monkeypatch, tmp_path):
    selected = replace(ed_plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    plan = replace(selected, resource_profile=wire.ORDINARY300.id)
    config = config_at(selected)
    monkeypatch.setattr(wire, "fixed_deadline", lambda *args: None)
    applied = []
    monkeypatch.setattr(wire, "apply_role_limits", lambda role, *, profile: applied.append(profile.id))
    monkeypatch.setattr(bridge, "restrict_worker_network", lambda: None)
    monkeypatch.setattr(bridge, "worker", lambda *args: pytest.fail("mismatched profile reached worker"))
    hooks = bridge.UnitHooks(lambda _config: plan, lambda *args: None, lambda *args: None)
    with pytest.raises(ValueError, match="resource profile"):
        bridge.run_role("worker", config, hooks, root=plan.local_worktree, incoming=BytesIO(), outgoing=BytesIO())
    assert applied == [wire.ED_H1_600.id]


def test_worker_network_guard_rejects_all_existing_routes_without_a_socket(monkeypatch):
    import socket
    # Save through monkeypatch so this process's networking is restored even
    # though the production installer uses direct assignments in its child.
    for method in ("connect", "connect_ex", "sendto", "sendmsg"):
        monkeypatch.setattr(socket.socket, method, getattr(socket.socket, method))
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    bridge.restrict_worker_network()
    for method in ("connect", "connect_ex", "sendto", "sendmsg"):
        with pytest.raises(RuntimeError, match="stdio IPC only"):
            getattr(socket.socket, method)(None, ("invented-unused.invalid", 9))
    with pytest.raises(RuntimeError, match="stdio IPC only"):
        socket.create_connection(("invented-unused.invalid", 9))


def test_git_reservation_is_cumulative_and_exhaustion_starts_no_helper(monkeypatch, tmp_path):
    helper = bridge.GitHelpers(tmp_path, "controller")
    helper.receipt["cpu_seconds"] = 4.1
    called = []
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda *args, **kwargs: called.append(args))
    with pytest.raises(ValueError, match="reserve exhausted"):
        helper.git("rev-parse", "HEAD")
    assert called == []


def test_private_ssh_command_disables_routes_agents_config_and_mux(tmp_path):
    plan = plan_at(tmp_path / "repo", tmp_path)
    command = bridge.remote_command(config_at(plan), plan)
    for item in ("/dev/null", "ControlMaster=no", "ControlPath=none", "ClearAllForwardings=yes",
                 "ProxyCommand=none", "ProxyJump=none", "IdentityAgent=none", "ForwardAgent=no"):
        assert item in command
    for bad in (("ssh", "-L", "123:target:123", "invented"),
                ("ssh", "-o", "ProxyCommand=untrusted", "invented")):
        with pytest.raises(ValueError, match="SSH"):
            bridge.validate_plan(replace(plan, ssh_argv=bad))


def test_private_ssh_accepts_fixed_known_host_alias_without_changing_guards(tmp_path):
    plan = plan_at(tmp_path / "repo", tmp_path)
    plan = replace(plan, ssh_argv=("ssh", "-i", "/invented/key",
        "-o", "UserKnownHostsFile=/invented/known-hosts",
        "-o", "HostKeyAlias=test.01", "tester@example.test"))
    bridge.validate_plan(plan)
    command = bridge.remote_command(config_at(plan), plan)
    assert "HostKeyAlias=test.01" in command
    for option in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "ControlMaster=no", "ControlPath=none",
                   "ClearAllForwardings=yes", "ProxyCommand=none", "ProxyJump=none", "ForwardAgent=no"):
        assert option in command
    assert command[1:3] == ["-F", "/dev/null"]


@pytest.mark.parametrize("alias", ["", "-leading", "white space", "tab\tvalue", "line\nvalue", "nul\x00value",
    "shell;value", "$(command)", "alias/value", "x" * 256])
def test_private_ssh_rejects_unsafe_host_key_alias(tmp_path, alias):
    plan = plan_at(tmp_path / "repo", tmp_path)
    changed = replace(plan, ssh_argv=("ssh", "-i", "/invented-unused/key", "-o", "HostKeyAlias=" + alias,
                                    "invented-unused.invalid"))
    with pytest.raises(ValueError, match="SSH"):
        bridge.validate_plan(changed)


def qualification_evidence(tmp_path, plan=None):
    """A fabricated qualification envelope tests guards, never actual readiness."""
    plan = plan or plan_at(tmp_path / "repo", tmp_path)
    out = plan.invented_output_parent / (plan.unit_id + "-invented-fabricated")
    out.mkdir(parents=True)
    config = config_at(plan)
    config.update(local_identity={"invented": "local"}, remote_identity={"invented": "remote"})
    raw = b"invented verified output\n"
    (out / plan.output_filename).write_bytes(raw)
    profile = bridge.profile_for_plan(plan)
    aggregate = wire.aggregate_roles({role: sample(role, profile) for role in profile.role_limits()}, profile=profile)
    result = {"output_sha256": wire.digest(raw), "output_bytes": len(raw), "verification_complete": True,
              "independent_verification": {"verified": True},
              "aggregate_budget": aggregate, "automatic_retries": 0}
    started = {"status": "started", "run_id": "fabricated-guard-test", "study_id": plan.unit_id,
        "specification_sha256": config["source_hashes"][plan.specification_path], "code_version": config["merge_commit"],
        "window": bridge.canonical(plan.window), "parameters": {"invented_only": True,
            "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])), "resource_profile": profile.id}}
    if plan.input_contract != wire.EC_INPUT_CONTRACT:
        started["parameters"]["input_contract"] = plan.input_contract
    terminal = {**started, "status": "completed", "result": result}
    start_raw, terminal_raw = wire.encode(started), wire.encode(terminal)
    journal = out / "invented-experiments.jsonl"
    journal.write_bytes(start_raw + terminal_raw)
    publication = {"status": "completed", "unit_id": plan.unit_id, "resource_profile": profile.id,
        "invented_only": True, "geometry": bridge.canonical(plan.full_geometry),
        "verification_complete": True, "independent_verification": {"verified": True},
        "candidate_counts": {"A": {"D+1": 3, "D+2": 3}}, "full_bridge": True, "local_pipe_test": False,
        "source_hashes": config["source_hashes"], "local_identity": config["local_identity"],
        "remote_identity": config["remote_identity"], "aggregate_budget": aggregate,
        "output_sha256": wire.digest(raw), "output_bytes": len(raw), "invented_journal": str(journal),
        "canonical_offsets": [0, len(start_raw) + len(terminal_raw)], "run_id": terminal["run_id"],
        "terminal_record_sha256": wire.digest(terminal_raw)}
    if plan.input_contract != wire.EC_INPUT_CONTRACT:
        publication["input_contract"] = plan.input_contract
    config["qualification"] = {"output": str(out), "publication_sha256": "0" * 64}
    return plan, config, publication


def save_publication(config, publication):
    raw = wire.encode(publication)
    path = Path(config["qualification"]["output"]) / "publication.json"
    path.write_bytes(raw)
    config["qualification"]["publication_sha256"] = wire.digest(raw)


def prior_snapshot_evidence(tmp_path, resource_profile=wire.ORDINARY300.id):
    plan = ed_plan_at(tmp_path / "repo", tmp_path, wire.ED_SNAPSHOT_CONTRACT)
    plan = replace(plan, resource_profile=resource_profile)
    source = tmp_path / "prior" / "h1-snapshot.jsonl.gz"
    source.parent.mkdir(parents=True)
    plan = replace(plan, input_pins=(bridge.InputPin("ED-SNAPSHOT", str(source), "a" * 64, 512 * wire.MIB),))
    config = config_at(plan)
    config.update(invented_only=False, local_identity={"invented": "local"},
                  remote_identity={"invented": "remote"})
    verified = {"verified": True, "full_geometry": True, "all_original_coordinates_verified": True,
                "sampled_scientific_rows": False, "decoded_sha256": plan.decoded_stream_sha256}
    result = {"output_sha256": "a" * 64, "output_bytes": 123,
              "verification_complete": True, "independent_verification": verified}
    started = {"status": "started", "run_id": "invented-prior-run", "study_id": "ED-H1-SNAPSHOT",
        "specification_sha256": config["source_hashes"][plan.specification_path],
        "code_version": config["merge_commit"], "window": bridge.canonical(plan.window),
        "parameters": {"invented_only": False, "input_contract": wire.ED_INPUTS_CONTRACT,
            "resource_profile": plan.resource_profile,
            "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"]))}}
    terminal = {**started, "status": "completed", "result": result}
    first, last = wire.encode(started), wire.encode(terminal)
    plan.canonical_journal.parent.mkdir(parents=True)
    # A subsequent trial has begun; the old completion remains a fixed span.
    plan.canonical_journal.write_bytes(first + last + wire.encode({"status": "started", "run_id": "later"}))
    pub = {"unit_id": "ED-H1-SNAPSHOT", "status": "completed", "invented_only": False,
        "full_bridge": True, "local_pipe_test": False, "resource_profile": plan.resource_profile,
        "input_contract": wire.ED_INPUTS_CONTRACT, "geometry": bridge.canonical(plan.full_geometry),
        **result, "source_hashes": config["source_hashes"], "local_identity": config["local_identity"],
        "remote_identity": config["remote_identity"], "canonical_offsets": [0, len(first) + len(last)],
        "run_id": started["run_id"], "terminal_record_sha256": wire.digest(last)}
    raw = wire.encode(pub)
    (source.parent / "publication.json").write_bytes(raw)
    config.update(snapshot_actual_publication_b64=base64.b64encode(raw).decode(),
        snapshot_actual_publication_sha256=wire.digest(raw), resolved_snapshot={"sha256": "a" * 64,
            "decoded_sha256": plan.decoded_stream_sha256, "publication_sha256": wire.digest(raw)})
    return plan, config, pub


def test_prior_snapshot_uses_exact_completed_span_and_opens_no_market_body(tmp_path, monkeypatch):
    plan, config, pub = prior_snapshot_evidence(tmp_path)
    real_regular, reads = wire.regular, []
    def regular(path):
        assert path != Path(plan.input_pins[0].original_path), "body stat before input access"
        reads.append(path)
        return real_regular(path)
    monkeypatch.setattr(wire, "regular", regular)
    proof = bridge.validate_ed_prior_snapshot(config, plan)
    assert proof["verified"] is True and proof["snapshot_body_opened"] is False
    assert proof["canonical_offsets"] == pub["canonical_offsets"]
    assert not Path(plan.input_pins[0].original_path).exists()
    assert plan.canonical_journal in reads
    with pytest.raises(ValueError, match="canonical journal span count differs"):
        bridge.journal_record(plan.canonical_journal, 0, "completed", pub["run_id"])


@pytest.mark.parametrize("mutation", ["publication", "source", "decoded", "terminal", "output", "end"])
def test_prior_snapshot_rejects_changed_metadata_or_canonical_anchor(tmp_path, mutation):
    plan, config, pub = prior_snapshot_evidence(tmp_path)
    if mutation == "publication":
        config["snapshot_actual_publication_sha256"] = "0" * 64
    elif mutation == "decoded":
        config["resolved_snapshot"]["decoded_sha256"] = "0" * 64
    else:
        if mutation == "source":
            pub["local_identity"] = {"invented": "other"}
        elif mutation == "terminal":
            pub["terminal_record_sha256"] = "0" * 64
        elif mutation == "output":
            pub["output_sha256"] = "0" * 64
        else:
            pub["canonical_offsets"][1] += 1
        raw = wire.encode(pub)
        (Path(plan.input_pins[0].original_path).parent / "publication.json").write_bytes(raw)
        config["snapshot_actual_publication_b64"] = base64.b64encode(raw).decode()
        config["snapshot_actual_publication_sha256"] = wire.digest(raw)
        config["resolved_snapshot"]["publication_sha256"] = wire.digest(raw)
    with pytest.raises((ValueError, KeyError)):
        bridge.validate_ed_prior_snapshot(config, plan)


@pytest.mark.parametrize("mutation", ["profile", "geometry", "verification", "verification_detail", "source", "runtime",
    "local_pipe", "remote_output", "journal", "terminal", "marker", "broken_marker"])
def test_qualification_requires_whole_geometry_identity_original_terminal_and_marker_absence(tmp_path, mutation):
    plan, config, publication = qualification_evidence(tmp_path)
    save_publication(config, publication)
    bridge.validate_qualification(config, plan)
    changed = deepcopy(publication)
    output = Path(config["qualification"]["output"])
    if mutation == "profile":
        changed["resource_profile"] = wire.CORE600.id
    elif mutation == "geometry":
        changed["geometry"]["sessions"] = 2
    elif mutation == "verification":
        changed["independent_verification"]["verified"] = False
    elif mutation == "verification_detail":
        changed["independent_verification"]["unit_work"] = {"own_label_planning_count": 116}
    elif mutation == "source":
        changed["source_hashes"][NAMES[0]] = "f" * 64
    elif mutation == "runtime":
        changed["remote_identity"] = {"invented": "other-runtime"}
    elif mutation == "local_pipe":
        changed["local_pipe_test"] = True
    elif mutation == "remote_output":
        (output / plan.output_filename).write_bytes(b"changed invented output\n")
    elif mutation == "journal":
        changed["invented_journal"] = str(tmp_path / "copied-journal.jsonl")
    elif mutation == "terminal":
        changed["terminal_record_sha256"] = "0" * 64
    elif mutation == "marker":
        (output / "unresolved.json").write_bytes(b"unresolved")
    else:
        (output / "unresolved.json").symlink_to(output / "absent-target")
    save_publication(config, changed)
    with pytest.raises(ValueError):
        bridge.validate_qualification(config, plan)


@pytest.mark.parametrize("input_contract", [wire.ED_INPUTS_CONTRACT, wire.ED_SNAPSHOT_CONTRACT])
def test_ed_qualification_preserves_exact_input_contract(tmp_path, input_contract):
    plan = ed_plan_at(tmp_path / "repo", tmp_path, input_contract)
    plan, config, publication = qualification_evidence(tmp_path, plan)
    save_publication(config, publication)
    bridge.validate_qualification(config, plan)
    for contract in (None, wire.EC_INPUT_CONTRACT, "unknown"):
        changed = deepcopy(publication)
        if contract is None:
            del changed["input_contract"]
        else:
            changed["input_contract"] = contract
        save_publication(config, changed)
        with pytest.raises(ValueError, match="qualification input contract"):
            bridge.validate_qualification(config, plan)
    save_publication(config, publication)
    journal = Path(publication["invented_journal"])
    records = [json.loads(line) for line in journal.read_bytes().splitlines()]
    records[-1]["parameters"].pop("input_contract")
    raw = b"".join(wire.encode(record) for record in records)
    journal.write_bytes(raw)
    publication["terminal_record_sha256"] = wire.digest(wire.encode(records[-1]))
    publication["canonical_offsets"][1] = len(raw)
    save_publication(config, publication)
    with pytest.raises(ValueError, match="canonical input contract"):
        bridge.validate_qualification(config, plan)


def test_ed600_qualification_rejects_an_ordinary_profile_receipt(tmp_path):
    plan = replace(ed_plan_at(tmp_path / "repo", tmp_path), resource_profile=wire.ED_H1_600.id)
    plan, config, publication = qualification_evidence(tmp_path, plan)
    save_publication(config, publication)
    bridge.validate_qualification(config, plan)
    publication["resource_profile"] = wire.ORDINARY300.id
    save_publication(config, publication)
    with pytest.raises(ValueError, match="same-source bridge qualification"):
        bridge.validate_qualification(config, plan)


def test_ed600_prior_snapshot_requires_same_profile_in_publication_and_canonical_span(tmp_path):
    plan, config, pub = prior_snapshot_evidence(tmp_path, wire.ED_H1_600.id)
    bridge.validate_ed_prior_snapshot(config, plan)
    pub["resource_profile"] = wire.ORDINARY300.id
    raw = wire.encode(pub)
    (Path(plan.input_pins[0].original_path).parent / "publication.json").write_bytes(raw)
    config.update(snapshot_actual_publication_sha256=wire.digest(raw),
        snapshot_actual_publication_b64=base64.b64encode(raw).decode())
    config["resolved_snapshot"]["publication_sha256"] = wire.digest(raw)
    with pytest.raises(ValueError, match="completed full prior"):
        bridge.validate_ed_prior_snapshot(config, plan)


DRIVER = '''from pathlib import Path
import base64, json, os, sys
ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "python"))
from research import intraday_campaign_wsl_bridge as b
from research import intraday_campaign_wsl_transport as w
PARENT = ROOT.parent
NAMES = __NAMES__
UNIT_ID = __UNIT_ID__
SPECIFICATION_PATH = __SPECIFICATION_PATH__
INPUT_BOUNDS = __INPUT_BOUNDS__
INPUT_CONTRACT = __INPUT_CONTRACT__
RESOURCE_PROFILE = __RESOURCE_PROFILE__
def load_plan(config):
    return b.UnitPlan(unit_id=UNIT_ID, specification_path=SPECIFICATION_PATH,
        source_names=NAMES, source_roster_sha256=w.digest(w.encode(list(NAMES))),
        input_pins=tuple(b.InputPin(name,"/invented-actual/"+name,"a"*64,maximum)
                         for name,maximum in INPUT_BOUNDS), decoded_stream_sha256="b"*64,
        window=__WINDOW__, full_geometry=__GEOMETRY__, candidate_bounds=__BOUNDS__,
        private_entrypoint="var/invented-driver.py",local_worktree=ROOT,remote_worktree=ROOT,
        actual_cache=PARENT/"cache"/"actual",actual_output=PARENT/"output"/"actual",
        actual_diagnostics=PARENT/"diagnostics"/"actual",canonical_journal=PARENT/"canonical"/"experiments.jsonl",
        global_marker=PARENT/"canonical"/"ordinary-unresolved.json",
        research_locks=(PARENT/"canonical"/"research.lock",PARENT/"output"/"campaign.lock"),
        invented_cache_parent=PARENT/"cache",invented_output_parent=PARENT/"output",
        invented_diagnostics_parent=PARENT/"diagnostics",output_filename="result.json",output_max_bytes=4096,
        ssh_argv=("ssh","-i","/invented-unused/key","invented-unused.invalid"),
        remote_prefix=("python3","-I","-S",str(ROOT/"var/invented-driver.py"),"coordinator"),
        input_contract=INPUT_CONTRACT,resource_profile=RESOURCE_PROFILE)
def make_invented(directory,sessions,checkpoint):
    manifest=[]
    for name,maximum in INPUT_BOUNDS:
        checkpoint("invented_generation")
        raw=("invented "+name+"\\n").encode()
        w.exclusive_bytes(directory/name,raw)
        manifest.append(b.InputFile(name,str(directory/name),w.digest(raw),maximum,len(raw)))
    return b.InputEnvelope(tuple(manifest),"b"*64,w.encode({"sessions":sessions,"targets":sessions}),INPUT_CONTRACT)
def compute_verify(directory,envelope,checkpoint):
    # This deliberately tiny producer and separately reconstructed verifier use
    # only invented byte counts, never scientific market/account calculations.
    if config.get("test_fault") in ("worker_network","worker_network_no_receipt"):
        import socket
        socket.create_connection(("invented-never-resolve.invalid",9))
    counts=[]
    for item in envelope.manifest:
        checkpoint("invented_producer")
        counts.append(len(b.bounded_read(directory/"received"/item.name,item.maximum)))
    raw=w.encode({"invented_total_bytes":sum(counts)})
    checkpoint("invented_independent_verifier")
    independent=sum((directory/"received"/name).stat().st_size for name,maximum in INPUT_BOUNDS)
    w.require(json.loads(raw)["invented_total_bytes"]==independent,"independent verification differs")
    w.exclusive_bytes(directory/"result.json",raw)
    pin,size=w.file_digest(directory/"result.json",4096,checkpoint)
    return b.OutputReceipt(pin,size,envelope.geometry,w.encode({"verified":True,"method":"independent invented byte count",
                           "unit_work":{"arms":["control","selected"],"own_label_planning_count":52}}),
                           w.encode({"A":{"D+1":1,"D+2":1}}))
class FaultStream:
    def __init__(self,stream,mode,config): self.stream,self.mode,self.config=stream,mode,config
    def write(self,raw):
        data=bytes(raw)
        if data.startswith(w.MAGIC):
            header=json.loads(data[len(w.MAGIC)+4:])
            fault=self.config.get("test_fault")
            if self.mode=="coordinator" and fault=="remote_output" and header["kind"]=="STARTED":
                w.exclusive_bytes(Path(self.config["output"])/"result.json",b"retained collision")
            if ((self.mode=="coordinator" and fault=="completed_loss" and header["kind"]=="COMPLETED")
                or (self.mode=="worker" and fault=="accepted_loss" and header["kind"]=="ACK_ACCEPTED")):
                raise BrokenPipeError("invented terminal frame loss")
            if self.mode=="worker" and fault=="trailing_frame" and header["kind"]=="ACK_ACCEPTED":
                written=self.stream.write(raw)
                self.stream.write(w.MAGIC[:2])
                return written
            if self.mode=="worker" and fault=="budget_scope" and header["kind"]=="WORKER_DONE":
                header["budget"]["operator_cpu_scope"]=self.config["test_budget_scope"]
                altered=w.encode(header)
                return self.stream.write(w.MAGIC+len(altered).to_bytes(4,"big")+altered)
        return self.stream.write(raw)
    def flush(self): return self.stream.flush()
mode=sys.argv[1]
config=json.loads(base64.b64decode(sys.argv[2],validate=True))
if mode=="coordinator" and config.get("test_fault")=="worker_network_no_receipt":
    exclusive_bytes=w.exclusive_bytes
    def omit_auxiliary_failure_receipt(path,raw):
        if path.name=="bridge-failure.json":
            raise OSError("invented auxiliary failure receipt unavailable")
        return exclusive_bytes(path,raw)
    w.exclusive_bytes=omit_auxiliary_failure_receipt
result=b.run_role(mode,config,b.UnitHooks(load_plan,make_invented,compute_verify),root=ROOT,
                  outgoing=FaultStream(sys.stdout.buffer,mode,config))
raise SystemExit(7 if mode=="worker" and config.get("test_fault")=="post_ack_nonzero" else result)
'''


def invented_repository(tmp_path, input_contract=wire.EC_INPUT_CONTRACT, resource_profile=wire.ORDINARY300.id):
    root = tmp_path / "repo"
    plan = (plan_at(root, tmp_path) if input_contract == wire.EC_INPUT_CONTRACT else
            ed_plan_at(root, tmp_path, input_contract))
    plan = replace(plan, resource_profile=resource_profile)
    for name in plan.source_names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("python/research/"):
            target.write_bytes((ROOT / name).read_bytes())
    (root / plan.specification_path).write_bytes(wire.encode({"id": plan.unit_id}))
    source = DRIVER.replace("__NAMES__", repr(plan.source_names)).replace("__WINDOW__", repr(WINDOW))
    source = source.replace("__GEOMETRY__", repr(GEOMETRY)).replace("__BOUNDS__", repr(BOUNDS))
    source = source.replace("__UNIT_ID__", repr(plan.unit_id)).replace("__SPECIFICATION_PATH__", repr(plan.specification_path))
    source = source.replace("__INPUT_BOUNDS__", repr(tuple((pin.name, pin.maximum) for pin in plan.input_pins)))
    source = source.replace("__INPUT_CONTRACT__", repr(plan.input_contract))
    source = source.replace("__RESOURCE_PROFILE__", repr(plan.resource_profile))
    (root / "var/invented-driver.py").write_text(source)
    for parent in ("cache", "output", "diagnostics", "canonical"):
        (tmp_path / parent).mkdir()
    for args in (("init", "-q"), ("add", "."),
                 ("-c", "user.name=Invented", "-c", "user.email=invented@example.invalid", "commit", "-qm", "invented source")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    config = config_at(plan)
    config["source_hashes"] = {name: wire.file_digest(root / name, 16 * wire.MIB, lambda _: None)[0]
                               for name in plan.source_names}
    for key, arg in (("merge_commit", "HEAD"), ("tree", "HEAD^{tree}"), ("reviewed_commit", "HEAD")):
        config[key] = subprocess.check_output(["git", "-C", str(root), "rev-parse", arg], text=True).strip()
    identity = {"source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
        "runtime_sha256": bridge.runtime_sha(), "machine_sha256": bridge.machine_sha(),
        "merge_commit": config["merge_commit"], "tree": config["tree"], "execution_host": bridge.HOST,
        "resource_profile": plan.resource_profile}
    config.update(local_identity=identity, remote_identity=identity)
    return root, plan, config


def assert_failed_bridge_receipts(config, journal_bytes):
    out, diagnostics = Path(config["output"]), Path(config["diagnostics"])
    process_failure = json.loads((diagnostics / "process-failure.json").read_bytes())
    assert process_failure["status"] == "unknown"
    assert process_failure["automatic_retries"] == 0
    assert not (diagnostics / "process-receipt.json").exists()
    coordinator_exit = process_failure["children"]["ssh"].get("returncode")
    forced_exit = coordinator_exit in (-signal.SIGTERM, -signal.SIGKILL)
    injected_loss = config.get("test_fault") == "worker_network_no_receipt"
    failure_path = out / "bridge-failure.json"
    if injected_loss:
        assert not failure_path.exists()
    try:
        failure = json.loads(failure_path.read_bytes())
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        # Termination may occur before creation or after open but before flush.
        assert forced_exit or (injected_loss and isinstance(coordinator_exit, int) and coordinator_exit != 0
            and "invented auxiliary failure receipt unavailable" in
            (diagnostics / "ssh-stderr.txt").read_text())
        return
    assert failure["status"] in ("failed", "unknown")
    assert failure["automatic_retries"] == 0
    assert failure["partial_evidence_preserved"] is True
    assert failure["unresolved_marker"] == str(out / "unresolved.json")
    if failure["status"] == "failed":
        terminal_bytes = journal_bytes.splitlines(keepends=True)[-1]
        assert json.loads(terminal_bytes)["status"] == "failed"
        assert failure["terminal_record_sha256"] == wire.digest(terminal_bytes)


@pytest.mark.parametrize("fault", [None, "remote_output", "completed_loss", "accepted_loss", "worker_network",
                                    "worker_network_no_receipt"])
def test_tiny_whole_common_pipe_and_terminal_failures(tmp_path, fault):
    root, plan, config = invented_repository(tmp_path)
    config["test_fault"] = fault
    command = bridge.process_command("controller", config, plan, root)
    completed = subprocess.run(command, cwd=root, capture_output=True, timeout=55)
    out, cache, diagnostics = Path(config["output"]), Path(config["cache"]), Path(config["diagnostics"])
    detail = completed.stderr.decode(errors="replace")
    if completed.returncode:
        detail += "\n" + "\n".join(path.read_text() for path in diagnostics.glob("*-stderr.txt"))
    assert (completed.returncode == 0) == (fault is None), detail
    journal_bytes = Path(config["journal"]).read_bytes()
    records = [json.loads(line) for line in journal_bytes.splitlines()]
    assert len(records) == 2
    assert records[0]["status"] == "started"
    network_failure = fault in ("worker_network", "worker_network_no_receipt")
    assert records[-1]["status"] == ("failed" if fault == "remote_output" or network_failure else "completed")
    assert records[0]["parameters"]["invented_only"] is True
    assert records[0]["parameters"]["resource_profile"] == bridge.PROFILE.id
    assert not plan.canonical_journal.exists()
    if fault is None:
        assert not (out / "unresolved.json").exists()
        publication = json.loads((out / "publication.json").read_bytes())
        assert publication["full_bridge"] is False
        assert publication["local_pipe_test"] is True
        assert publication["aggregate_budget"]["hard_cpu_sum_seconds"] == 300
        assert publication["output_sha256"] == wire.digest((out / "result.json").read_bytes())
        assert records[-1]["result"]["independent_verification"] == publication["independent_verification"]
        assert records[-1]["result"]["independent_verification"]["unit_work"] == {
            "arms": ["control", "selected"], "own_label_planning_count": 52}
        assert (cache / "completed-ack.json").exists()
        assert (diagnostics / "process-receipt.json").exists()
    else:
        assert (out / "unresolved.json").exists()
        assert_failed_bridge_receipts(config, journal_bytes)
        if network_failure:
            worker_failure = json.loads((cache / "worker-failure.json").read_bytes())
            assert worker_failure["error_type"] == "RuntimeError"
            assert "stdio IPC only" in (diagnostics / "worker-stderr.txt").read_text()
            assert not (cache / "completed-ack.json").exists()
            assert not (out / "publication.json").exists()
        # A new attempt cannot reuse the exclusive output or clear the orphan.
        again = subprocess.run(command, cwd=root, capture_output=True, timeout=10)
        assert again.returncode != 0
        assert Path(config["journal"]).read_bytes() == journal_bytes
        assert (out / "unresolved.json").exists()


@pytest.mark.parametrize("input_contract", [wire.ED_INPUTS_CONTRACT, wire.ED_SNAPSHOT_CONTRACT])
@pytest.mark.parametrize("fault", [None, "completed_loss", "accepted_loss"])
def test_ed_tiny_pipe_uses_same_started_completion_ack_and_ordinary_budget(tmp_path, input_contract, fault):
    root, plan, config = invented_repository(tmp_path, input_contract)
    config["test_fault"] = fault
    command = bridge.process_command("controller", config, plan, root)
    completed = subprocess.run(command, cwd=root, capture_output=True, timeout=55)
    out, cache, diagnostics = Path(config["output"]), Path(config["cache"]), Path(config["diagnostics"])
    detail = completed.stderr.decode(errors="replace")
    if completed.returncode:
        detail += "\n" + "\n".join(path.read_text() for path in diagnostics.glob("*-stderr.txt"))
    assert (completed.returncode == 0) == (fault is None), detail
    journal_bytes = Path(config["journal"]).read_bytes()
    records = [json.loads(line) for line in journal_bytes.splitlines()]
    assert [record["status"] for record in records] == ["started", "completed"]
    assert records[0]["parameters"]["input_contract"] == input_contract
    assert not plan.canonical_journal.exists()
    publication = json.loads((out / "publication.json").read_bytes())
    assert publication["input_contract"] == input_contract
    assert publication["aggregate_budget"]["hard_cpu_sum_seconds"] == 300
    assert publication["aggregate_budget"]["hard_address_space_sum_bytes"] == 512 * wire.MIB
    assert publication["local_pipe_test"] is True and publication["full_bridge"] is False
    assert len(list((cache / "received").iterdir())) == len(plan.input_pins)
    if fault is None:
        assert not (out / "unresolved.json").exists()
        assert (cache / "completed-ack.json").exists()
        assert (diagnostics / "process-receipt.json").exists()
    else:
        assert (out / "unresolved.json").exists()
        assert_failed_bridge_receipts(config, journal_bytes)
        again = subprocess.run(command, cwd=root, capture_output=True, timeout=10)
        assert again.returncode != 0
        assert Path(config["journal"]).read_bytes() == journal_bytes
        assert (out / "unresolved.json").exists()


@pytest.mark.parametrize("input_contract", [wire.ED_INPUTS_CONTRACT, wire.ED_SNAPSHOT_CONTRACT])
def test_ed600_tiny_pipe_keeps_same_ack_and_as_with_the_approved_worker_scope(tmp_path, input_contract):
    root, plan, config = invented_repository(tmp_path, input_contract, wire.ED_H1_600.id)
    completed = subprocess.run(bridge.process_command("controller", config, plan, root),
                               cwd=root, capture_output=True, timeout=55)
    diagnostics = Path(config["diagnostics"])
    detail = completed.stderr.decode(errors="replace")
    if completed.returncode:
        detail += "\n" + "\n".join(path.read_text() for path in diagnostics.glob("*-stderr.txt"))
    assert completed.returncode == 0, detail
    publication = json.loads((Path(config["output"]) / "publication.json").read_bytes())
    assert publication["resource_profile"] == wire.ED_H1_600.id
    assert publication["full_bridge"] is False and publication["local_pipe_test"] is True
    aggregate = publication["aggregate_budget"]
    assert aggregate["hard_cpu_sum_seconds"] == 600
    assert aggregate["hard_address_space_sum_bytes"] == 512 * wire.MIB
    assert aggregate["samples"]["worker"]["readback"]["rlimit_cpu"] == [480, 480]
    assert aggregate["samples"]["worker"]["readback"]["rlimit_as"] == [352 * wire.MIB] * 2
    budget = json.loads((Path(config["cache"]) / "worker-budget.json").read_bytes())["budget"]
    assert budget["operator_cpu_scope"] == "ED-H1-operator-2026-10-31"
    assert budget["limits"] == {"cpu_seconds": 480, "address_space_bytes": 352 * wire.MIB,
                                 "wall_seconds": 1200, "nice": 10}
    assert (Path(config["cache"]) / "completed-ack.json").exists()
    records = [json.loads(line) for line in Path(config["journal"]).read_bytes().splitlines()]
    assert [record["status"] for record in records] == ["started", "completed"]
    assert records[0]["parameters"]["resource_profile"] == wire.ED_H1_600.id
    assert bridge.PROFILE is wire.ORDINARY300


@pytest.mark.parametrize("profile,input_contract,scope", [
    (wire.ORDINARY300.id, wire.EC_INPUT_CONTRACT, "ED-H1-operator-2026-10-31"),
    (wire.ED_H1_600.id, wire.ED_INPUTS_CONTRACT, "ED-H1-operator-2026-10-30")])
def test_coordinator_rejects_operator_override_or_wrong_ed_scope(tmp_path, profile, input_contract, scope):
    root, plan, config = invented_repository(tmp_path, input_contract, profile)
    config.update(test_fault="budget_scope", test_budget_scope=scope)
    completed = subprocess.run(bridge.process_command("controller", config, plan, root),
                               cwd=root, capture_output=True, timeout=55)
    assert completed.returncode != 0
    diagnostic = Path(config["diagnostics"])
    assert "registered worker budget not complete" in (diagnostic / "ssh-stderr.txt").read_text()
    records = [json.loads(line) for line in Path(config["journal"]).read_bytes().splitlines()]
    assert [record["status"] for record in records] == ["started", "failed"]
    assert (Path(config["output"]) / "unresolved.json").exists()


@pytest.mark.parametrize("resource_profile", [wire.ORDINARY300.id, wire.ED_H1_600.id])
def test_cold_probe_is_lean_source_only_and_nonqualifying(tmp_path, resource_profile):
    input_contract = wire.ED_INPUTS_CONTRACT if resource_profile == wire.ED_H1_600.id else wire.EC_INPUT_CONTRACT
    root, plan, config = invented_repository(tmp_path, input_contract, resource_profile)
    completed = subprocess.run(bridge.process_command("source-probe", config, plan, root),
                               cwd=root, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode()
    receipt = json.loads(completed.stdout)
    assert receipt["qualifies_actual"] is False
    assert receipt["journal_module_imported"] is True
    assert receipt["market_input_accesses"] == receipt["journal_accesses"] == receipt["marker_accesses"] == 0
    assert receipt["output_directories_created"] == 0
    assert receipt["role_sample"]["address_space_bytes"] < 32 * wire.MIB
    assert receipt["role_sample"]["readback"]["rlimit_as"] == [32 * wire.MIB] * 2
    assert not Path(config["output"]).exists()
    assert not Path(config["journal"]).exists()
    assert not Path(config["cache"]).exists()


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_isolated_json_native_discovery_journal_needs_no_decimal(tmp_path, terminal_status):
    import sys

    program = r'''
import importlib.util
import json
from pathlib import Path
import sys

blocked = {"decimal", "_decimal", "_pydecimal", "_contextvars"}
assert blocked.isdisjoint(sys.modules), "fresh child already loaded Decimal runtime"
class RejectDecimalImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError("JSON-native journal loaded " + fullname)
sys.meta_path.insert(0, RejectDecimalImports())

source, journal, terminal_status = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("isolated_experiment_log", source)
logger = importlib.util.module_from_spec(spec)
# Execute the named source bytes, never a timestamp-matching project pyc.
exec(compile(source.read_bytes(), str(source), "exec", dont_inherit=True), logger.__dict__)
assert blocked.isdisjoint(sys.modules)

retained = b'{"record_type":"invented-retained","status":"untouched"}\n'
journal.write_bytes(retained)
parameters = {"nested": {"items": [None, True, False, 0, 2.5, "invented"]}, "automatic_retries": 0}
window = {"symbol": "INVENTED", "interval": "1m", "start": "2020-01-01", "end": "2020-01-03"}
original_parameters = json.loads(json.dumps(parameters))
original_window = dict(window)
result = {"verified": True, "count": 0, "details": [None, False, 1.25, "invented"]}
observed = {}
def evaluate():
    raw = journal.read_bytes()
    assert raw.startswith(retained) and raw.endswith(b"\n")
    assert len(raw.splitlines()) == 2
    started = json.loads(raw.splitlines()[1])
    assert started["status"] == "started"
    assert started["parameters"] == original_parameters and started["window"] == original_window
    observed.update(raw=raw, started=started)
    parameters["nested"]["items"].append("caller mutation")
    window["symbol"] = "MUTATED"
    if terminal_status == "failed":
        raise RuntimeError("invented callback failure")
    return result

try:
    record = logger.run_discovery_trial(study_id="invented-json-native", specification_sha256="a" * 64,
        parameters=parameters, window=window, evaluate=evaluate, runs_path=journal,
        code_version_provider=lambda: "b" * 40)
except RuntimeError as error:
    assert terminal_status == "failed" and str(error) == "invented callback failure"
else:
    assert terminal_status == "completed" and record["result"] == result

raw = journal.read_bytes()
assert raw.startswith(observed["raw"]) and raw.endswith(b"\n")
assert len(raw.splitlines()) == 3
terminal = json.loads(raw.splitlines()[2])
expected = {**observed["started"], "status": terminal_status, "logged_at": terminal["logged_at"]}
expected.update({"result": result} if terminal_status == "completed" else {"error_type": "RuntimeError"})
assert terminal == expected
assert blocked.isdisjoint(sys.modules)
print("json-native-journal-" + terminal_status)
'''
    completed = subprocess.run([sys.executable, "-I", "-S", "-c", program,
        str(ROOT / "python/research/experiment_log.py"), str(tmp_path / "invented-experiments.jsonl"), terminal_status],
        capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout.decode().strip() == "json-native-journal-" + terminal_status


@pytest.mark.parametrize("fault", ["trailing_frame", "post_ack_nonzero"])
def test_terminal_alone_cannot_hide_truncated_frame_or_nonzero_child(tmp_path, fault):
    root, plan, config = invented_repository(tmp_path)
    config["test_fault"] = fault
    completed = subprocess.run(bridge.process_command("controller", config, plan, root),
                               cwd=root, capture_output=True, timeout=55)
    assert completed.returncode != 0
    journal = Path(config["journal"]).read_bytes().splitlines()
    assert [json.loads(line)["status"] for line in journal] == ["started", "completed"]
    diagnostics = Path(config["diagnostics"])
    assert (diagnostics / "process-failure.json").exists()
    assert not (diagnostics / "process-receipt.json").exists()
