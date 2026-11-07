"""Source and tiny invented-only ordinary bridge tests; no actual/GCP access."""
from dataclasses import FrozenInstanceError, replace
from copy import deepcopy
from contextlib import contextmanager
from io import BytesIO
import json
import base64
import os
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
ED_PROFILES = (wire.ED_H1_600, wire.ED_H1_600_AS520)


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


def with_profile(plan, resource_profile):
    plan = replace(plan, resource_profile=resource_profile, recovery_contract=None)
    if resource_profile != wire.ED_H1_600_AS520.id:
        return plan
    recovery = bridge.canonical(bridge.ED_H1_RECOVERY_CONTRACT)
    plan = replace(plan, recovery_contract=bridge.ED_H1_RECOVERY_CONTRACT,
        global_marker=plan.canonical_journal.parent / recovery["recovery_marker_name"])
    if plan.unit_id == "ED-H1-SNAPSHOT":
        return replace(plan, actual_output=plan.actual_output.parent / recovery["snapshot_output_directory"])
    return replace(plan, input_pins=(replace(plan.input_pins[0], original_path=str(
        plan.actual_output.parent / recovery["snapshot_output_directory"] / "h1-snapshot.jsonl.gz")),))


def invented_recovery_predecessor(plan, monkeypatch):
    """Create only invented control metadata, and pin its tiny span in this test."""
    recovery = bridge.canonical(bridge.ED_H1_RECOVERY_CONTRACT)
    started = {"record_type": "discovery_trial", "status": "started", "run_id": recovery["previous_run_id"], "study_id": recovery["unit_id"],
        "code_version": recovery["previous_code_version"], "window": bridge.canonical(plan.window),
        "parameters": {"invented_only": False, "attempt": recovery["previous_attempt"],
            "input_contract": wire.ED_INPUTS_CONTRACT, "resource_profile": recovery["resource_profile"],
            "automatic_retries": 0, "source_manifest_sha256": recovery["previous_source_manifest_sha256"]}}
    terminal = {**started, "status": "failed", "error_type": "InventedControlMetadataFailure"}
    # The approved offsets identify one spaced experiment_log terminal line,
    # not its two-line started/terminal lifecycle. Match the collector/auditor.
    raw = (json.dumps(terminal, sort_keys=True, allow_nan=False) + "\n").encode()
    recovery.update(previous_terminal_offsets=[0, len(raw)], previous_terminal_sha256=wire.digest(raw))
    monkeypatch.setattr(bridge, "ED_H1_RECOVERY_CONTRACT", wire.encode(recovery))
    plan = with_profile(plan, wire.ED_H1_600_AS520.id)
    plan.canonical_journal.parent.mkdir(parents=True, exist_ok=True)
    plan.canonical_journal.write_bytes(raw)
    (plan.canonical_journal.parent / recovery["previous_marker_name"]).write_bytes(wire.encode({
        "attempt": "actual1", "state": "unresolved", "automatic_retries": 0}))
    return plan, terminal


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
    if plan.resource_profile in (wire.ED_H1_600.id, wire.ED_H1_600_AS520.id):
        result["unit_id"] = plan.unit_id
    return result


def eh_support_plan_at(tmp_path, opening=False, *, snapshot=False, retry=False):
    private = tmp_path / "native"
    private.mkdir(mode=0o700)
    plan = plan_at(tmp_path / "repo", private)
    if retry:
        unit = bridge.EH_SNAPSHOT_RETRY_UNIT_ID
        binding = bridge._EH_BINDINGS[unit]
        spec, entry, output, maximum = binding.specification, binding.runtime, binding.output, binding.output_max
        dependencies = {binding.auditor, *binding.private_dependencies, bridge.EH_SNAPSHOT_RETRY_GUARD}
    elif snapshot:
        unit, spec, entry = (bridge.EH_SNAPSHOT_UNIT_ID, bridge.EH_SNAPSHOT_SPECIFICATION_PATH,
                             bridge.EH_SNAPSHOT_RUNTIME)
        output, maximum = bridge.EH_SNAPSHOT_OUTPUT_FILENAME, bridge.EH_SNAPSHOT_OUTPUT_MAX_BYTES
        dependencies = {bridge.EH_SNAPSHOT_RECEIPT_AUDITOR, bridge.EH_OPEN_RUNTIME, bridge.EH_OPEN_RECEIPT_AUDITOR}
    elif opening:
        unit, spec, entry = bridge.EH_OPEN_UNIT_ID, bridge.EH_OPEN_SPECIFICATION_PATH, bridge.EH_OPEN_RUNTIME
        output, maximum = bridge.EH_OPEN_OUTPUT_FILENAME, bridge.EH_OPEN_OUTPUT_MAX_BYTES
        dependencies = {bridge.EH_OPEN_RECEIPT_AUDITOR}
    else:
        unit, spec, entry = bridge.EH_SUPPORT_UNIT_ID, bridge.EH_SUPPORT_SPECIFICATION_PATH, bridge.EH_SUPPORT_RUNTIME
        output, maximum = bridge.EH_SUPPORT_OUTPUT_FILENAME, bridge.EH_SUPPORT_OUTPUT_MAX_BYTES
        dependencies = set()
    names = tuple(sorted((set(plan.source_names) - {plan.specification_path, plan.private_entrypoint}) | {
        spec, bridge.EE_SPECIFICATION_PATH, entry, bridge.EH_SUPPORT_RECEIPT_AUDITOR, *dependencies}))
    specification = json.loads((ROOT / spec).read_bytes())
    plan = replace(plan, unit_id=unit, specification_path=spec, private_entrypoint=entry,
        source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names))),
        input_contract=wire.EH_SUPPORT_INPUT_CONTRACT, ssh_argv=(), remote_prefix=(), local_coordinator=True,
        local_execution_root=private, full_geometry=wire.encode(specification["geometry"]),
        candidate_bounds=wire.encode({arm: {"original": specification["geometry"]["targets_per_scenario"]}
                                      for arm in ("S1", "S2")}),
        input_pins=tuple(bridge.InputPin(name, "/invented/native/original/" + name, "a" * 64, maximum)
                         for name, maximum in wire.EH_SUPPORT_INPUT_BOUNDS),
        output_filename=output, output_max_bytes=maximum,
        research_locks=(plan.research_locks[0], plan.invented_output_parent / bridge.EH_SUPPORT_CAMPAIGN_LOCK))
    if not retry:
        return plan
    leaf = unit + "-actual1"
    return replace(plan, actual_cache=plan.invented_cache_parent / leaf, actual_output=plan.invented_output_parent / leaf,
        actual_diagnostics=plan.invented_diagnostics_parent / leaf, actual_runner_ready=True,
        global_marker=private / "canonical" / bridge.EH_SNAPSHOT_RETRY_MARKER,
        research_locks=(private / "canonical" / "intraday-research.lock", plan.research_locks[1]))


def eh_support_config(plan):
    config = config_at(plan)
    for key in ("local_pipe_test", "test_ssh_sample"):
        del config[key]
    identity = {"execution_host": bridge.LOCAL_HOST, "runtime_sha256": "b" * 64}
    config.update(bridge._mode_metadata(plan), unit_id=plan.unit_id, local_identity=identity, remote_identity=identity)
    return config


@pytest.mark.parametrize("opening", [False, True])
def test_eh_support_plan_is_one_closed_ordinary_local_pair(tmp_path, opening):
    plan = eh_support_plan_at(tmp_path, opening)
    bridge.validate_plan(plan)
    assert bridge.profile_for_plan(plan) is wire.ORDINARY300
    envelope = envelope_for(plan)
    assert bridge.InputEnvelope.from_payload(envelope.payload(), input_contract=wire.EH_SUPPORT_INPUT_CONTRACT) == envelope
    for changes in ({"local_coordinator": False}, {"unit_id": bridge.EE_UNIT_ID},
                    {"specification_path": bridge.EE_SPECIFICATION_PATH}, {"input_contract": wire.EE_INPUT_CONTRACT},
                    {"specification_path": bridge.EH_SUPPORT_SPECIFICATION_PATH if opening else bridge.EH_OPEN_SPECIFICATION_PATH},
                    {"resource_profile": wire.EF_LOCAL_SEQUENCE.id}, {"resource_profile": wire.ED_H1_600.id},
                    {"ssh_argv": ("ssh",)}, {"remote_prefix": ("remote",)},
                    {"remote_worktree": tmp_path / "different-repo"}, {"output_max_bytes": wire.MIB},
                    {"actual_cache": plan.actual_cache.parent.parent / "wrong-cache"},
                    {"research_locks": (plan.research_locks[0], plan.research_locks[1].with_suffix(".other"))}):
        with pytest.raises(ValueError):
            bridge.validate_plan(replace(plan, **changes))
    with pytest.raises(ValueError, match="forbids remote_command"):
        bridge.remote_command({}, plan)


@pytest.mark.parametrize("opening", [False, True])
def test_eh_support_bootstrap_and_actual_scope_reject_drift_before_inputs(tmp_path, monkeypatch, opening):
    plan = eh_support_plan_at(tmp_path, opening)
    config = eh_support_config(plan)
    monkeypatch.setattr(wire, "regular", lambda _: pytest.fail("bootstrap opened an input"))
    assert bridge._bootstrap_profile(config) is wire.ORDINARY300
    bridge.validate_config(config, plan)
    for changes in ({"execution_host": bridge.HOST}, {"local_pipe_test": True}, {"test_ssh_sample": None},
                    {"unit_id": bridge.EE_UNIT_ID}, {"local_coordinator": False},
                    {"resource_profile": wire.ED_H1_600.id}, {"resource_profile": wire.EF_LOCAL_SEQUENCE.id}):
        with pytest.raises(ValueError):
            bridge._bootstrap_profile({**config, **changes})
    unrelated_spec = bridge.EH_SUPPORT_SPECIFICATION_PATH if opening else bridge.EH_OPEN_SPECIFICATION_PATH
    with pytest.raises(ValueError, match="EH support local bootstrap"):
        bridge._bootstrap_profile({**config, "source_hashes": {**config["source_hashes"], unrelated_spec: "a" * 64}})
    sessions = bridge.canonical(plan.full_geometry)["sessions"]
    config.update(invented_only=False, attempt="actual1", cache=str(plan.actual_cache), output=str(plan.actual_output),
                  diagnostics=str(plan.actual_diagnostics), journal=str(plan.canonical_journal), sessions=sessions)
    with pytest.raises(ValueError, match="actual bridge not ready"):
        bridge.validate_config(config, plan)
    ready = replace(plan, actual_runner_ready=True)
    bridge.validate_config(config, ready)
    for changes in ({"attempt": "actual2"}, {"sessions": sessions - 1}):
        with pytest.raises(ValueError, match="fixed EH actual1 and full geometry"):
            bridge.validate_config({**config, **changes}, ready)
    for changes in ({"automatic_retries": False}, {"wall_seconds": 1200.0}):
        with pytest.raises(ValueError, match="EH exact retry and wall"):
            bridge.validate_config({**config, **changes}, ready)


@pytest.mark.parametrize("opening,retry", [(False, False), (True, False), (False, True)])
def test_eh_support_readiness_must_match_the_hashed_source_registration(tmp_path, monkeypatch, opening, retry):
    plan = eh_support_plan_at(tmp_path, opening, retry=retry)
    if retry:
        from research import intraday_scenario_snapshot_unit as snapshot

        _, pins, decoded = snapshot.contract((ROOT / plan.specification_path).read_bytes(),
                                            (ROOT / bridge.EE_SPECIFICATION_PATH).read_bytes(), retry=True)
        plan = replace(plan, input_pins=pins, decoded_stream_sha256=decoded, window=wire.encode(snapshot.WINDOW))
    config = eh_support_config(plan)
    private_source = b"invented source fixture\n"
    specification = json.loads((ROOT / plan.specification_path).read_bytes())
    specification["private_source_pins"] = {name: wire.digest(private_source)
                                            for name in plan.source_names if name.startswith("var/")}
    for name in plan.source_names:
        path = plan.local_worktree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = private_source
        if name == bridge.EE_SPECIFICATION_PATH:
            raw = (ROOT / name).read_bytes()
        elif name == plan.specification_path:
            raw = wire.encode(specification)
        path.write_bytes(raw)
        config["source_hashes"][name] = wire.digest(raw)
    ready = json.loads((plan.local_worktree / plan.specification_path).read_bytes())["actual_runner_ready"]
    plan = replace(plan, actual_runner_ready=ready)
    identity = {**config["local_identity"], "merge_commit": config["merge_commit"], "tree": config["tree"],
        "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
        "machine_sha256": "e" * 64, "resource_profile": wire.ORDINARY300.id}
    config.update(local_identity=identity, remote_identity=identity)
    monkeypatch.setattr(bridge, "runtime_sha", lambda: identity["runtime_sha256"])
    monkeypatch.setattr(bridge, "machine_sha", lambda: identity["machine_sha256"])
    assert bridge.verify_sources(config, plan, plan.local_worktree, "local") == identity
    with pytest.raises(ValueError, match="EH support source registration or readiness differs"):
        bridge.verify_sources(config, replace(plan, actual_runner_ready=not ready), plan.local_worktree, "local")
    new_runtime = b"changed private fixture\n"
    (plan.local_worktree / plan.private_entrypoint).write_bytes(new_runtime)
    config["source_hashes"][plan.private_entrypoint] = wire.digest(new_runtime)
    with pytest.raises(ValueError, match="EH support source registration or readiness differs"):
        bridge.verify_sources(config, plan, plan.local_worktree, "local")


def test_eh_support_roles_require_one_inherited_already_held_unit_lock(tmp_path, monkeypatch):
    plan = eh_support_plan_at(tmp_path)

    def lease(mode):
        return bridge._local_unit_lock(mode, plan, name=bridge.EH_SUPPORT_UNIT_LOCK,
            environment_key=bridge.EH_SUPPORT_LOCK_ENV, campaign="EH")

    monkeypatch.delenv(bridge.EH_SUPPORT_LOCK_ENV, raising=False)
    with pytest.raises(ValueError, match="requires an inherited lifecycle lock"), lease("coordinator"):
        pytest.fail("standalone role admitted")
    with lease("controller") as fd:
        duplicate = os.dup(fd)
    monkeypatch.setenv(bridge.EH_SUPPORT_LOCK_ENV, str(duplicate))
    with lease("worker"):
        with pytest.raises(BlockingIOError), lease("controller"):
            pytest.fail("parallel controller admitted")
    unlocked = os.open(plan.local_execution_root / bridge.EH_SUPPORT_UNIT_LOCK, os.O_RDWR)
    monkeypatch.setenv(bridge.EH_SUPPORT_LOCK_ENV, str(unlocked))
    with pytest.raises(ValueError, match="requires an already held lifecycle lock"), lease("worker"):
        pytest.fail("unlocked standalone role admitted")


def test_eh_support_result_requires_exact_manifest_full_reconstruction_and_denominators(tmp_path):
    plan = eh_support_plan_at(tmp_path)
    envelope = envelope_for(plan)
    geometry = bridge.canonical(envelope.geometry)
    raw_audit = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True, "full_eof_verified": True,
        "cache_sha256": "c" * 64, "raw_keys": geometry["cc_chunks"],
        "archives": {"CC": {"kind": "CC", "compressed_sha256": envelope.manifest[1].sha256,
            "decoded_sha256": envelope.stream_sha256, "expected_chunks": geometry["cc_chunks"],
            "expected_rows": geometry["cc_rows"], "expected_daily_rows": 0}}}
    verification = {"verified": True, "invented_only": True, "schema": bridge.EH_SUPPORT_OUTPUT_SCHEMA,
        "full_eof_verified": True, "all_original_coordinates_verified": True, "full_geometry": True,
        "input_manifest_sha256": wire.digest(wire.encode([item.metadata() for item in envelope.manifest])),
        "summary_sha256": "a" * 64, "raw_byte_audit": raw_audit}
    counts = bridge.canonical(plan.candidate_bounds)
    result = bridge.OutputReceipt("a" * 64, 10, envelope.geometry, wire.encode(verification),
                                  wire.encode(counts)).payload()
    bridge.validate_result(result, envelope, plan, invented_only=True)
    for changed in ({"full_eof_verified": False}, {"all_original_coordinates_verified": 1},
                    {"input_manifest_sha256": "d" * 64}, {"full_geometry": False},
                    {"raw_byte_audit": {}}, {"invented_only": False},
                    {"raw_byte_audit": {**raw_audit, "archives": {**raw_audit["archives"], "CX": {}}}},
                    {"raw_byte_audit": {**raw_audit, "raw_keys": geometry["cc_chunks"] - 1}}):
        with pytest.raises(ValueError, match="EH support independent reconstruction"):
            bridge.validate_result({**result, "independent_verification": {**verification, **changed}},
                                   envelope, plan, invented_only=True)
    with pytest.raises(ValueError, match="original denominators"):
        bridge.validate_result({**result, "candidate_counts": {**counts, "S1": {"original": 1}}},
                               envelope, plan, invented_only=True)


@pytest.mark.parametrize("opening,snapshot", [(False, False), (True, False), (False, True)])
def test_eh_support_original_receipt_audit_binds_exact_full_attempt_and_release(tmp_path, opening, snapshot):
    plan = replace(eh_support_plan_at(tmp_path, opening, snapshot=snapshot), actual_runner_ready=True)
    bridge.validate_plan(plan)
    config = eh_support_config(plan)
    config.update(invented_only=False, attempt=bridge.EH_SUPPORT_ATTEMPT)
    attempt = bridge.EH_SUPPORT_QUALIFICATION_ATTEMPT
    out = plan.invented_output_parent / (plan.unit_id + "-invented-" + attempt)
    out.mkdir(parents=True)
    publication = {**bridge._mode_metadata(plan), "invented_only": True, "unit_id": plan.unit_id,
        "source_hashes": config["source_hashes"], "local_identity": config["local_identity"],
        "remote_identity": config["remote_identity"], "output_sha256": "d" * 64}
    raw = wire.encode(publication)
    (out / "publication.json").write_bytes(raw)
    config["qualification"] = {"output": str(out), "publication_sha256": wire.digest(raw), "audit_sha256": "a" * 64}
    launches = plan.local_execution_root / "launches"
    audit_path = (launches / plan.unit_id if opening or snapshot else launches) / attempt / "original-receipt-audit.json"
    audit_path.parent.mkdir(parents=True)
    audit = {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": True,
        "science_reexecuted": False, "unit_id": plan.unit_id, "attempt": attempt,
        "publication_sha256": config["qualification"]["publication_sha256"], "output_sha256": "d" * 64,
        "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
        "merge_commit": config["merge_commit"], "tree": config["tree"], "runtime_sha256": "b" * 64,
        "auditor_source_sha256": config["source_hashes"][bridge._EH_BINDINGS[plan.unit_id].auditor]}

    def save(value):
        raw = wire.encode(value)
        audit_path.write_bytes(raw)
        config["qualification"]["audit_sha256"] = wire.digest(raw)

    save(audit)
    bridge.validate_eh_support_receipt_audit(config, plan)
    for changes in ({"runtime_sha256": "f" * 64}, {"auditor_source_sha256": "f" * 64},
                    {"output_sha256": "f" * 64}, {"science_reexecuted": 0}, {"attempt": "tiny"},
                    {"status": "FAIL"}, {"unit_id": bridge.EH_SUPPORT_UNIT_ID if opening else bridge.EH_OPEN_UNIT_ID}):
        save({**audit, **changes})
        with pytest.raises(ValueError, match="EH original receipt audit binding differs"):
            bridge.validate_eh_support_receipt_audit(config, plan)
    save(audit)
    with pytest.raises(ValueError, match="requires actual1"):
        bridge.validate_eh_support_receipt_audit(config, replace(plan, actual_runner_ready=False))
    audit_path.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="audit hash differs"):
        bridge.validate_eh_support_receipt_audit(config, plan)


def test_eh_snapshot_cannot_replace_the_opening_release_or_omit_its_private_dependency(tmp_path):
    plan = eh_support_plan_at(tmp_path, snapshot=True)
    assert bridge.profile_for_plan(plan) is wire.ORDINARY300
    config = eh_support_config(plan)
    bridge.validate_config(config, plan)
    names = tuple(name for name in plan.source_names if name != bridge.EH_OPEN_RUNTIME)
    with pytest.raises(ValueError, match="registration, auditor or output closure"):
        bridge.validate_plan(replace(plan, source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names)))))
    with pytest.raises(ValueError, match="EH support local bootstrap"):
        bridge._bootstrap_profile({**config, "source_hashes": {
            **config["source_hashes"], bridge.EH_OPEN_SPECIFICATION_PATH: "a" * 64}})


def test_eh_snapshot_retry_requires_its_paths_original_locks_and_exact_attempt(tmp_path):
    plan = eh_support_plan_at(tmp_path, retry=True)
    config = eh_support_config(plan)
    assert bridge._bootstrap_profile(config) is wire.ORDINARY300
    bridge.validate_config(config, plan)
    for changes in (
        {"global_marker": plan.global_marker.with_name("eh-support-unresolved.json")},
        {"actual_output": plan.actual_output.with_name("EH-SNAPSHOT-1-actual1")},
        {"actual_cache": plan.actual_cache.with_name("EH-SNAPSHOT-1-actual1")},
        {"actual_diagnostics": plan.actual_diagnostics.with_name("EH-SNAPSHOT-1-actual1")},
        {"canonical_journal": plan.canonical_journal.with_name("other.jsonl")},
        {"research_locks": (plan.research_locks[0].with_name("other.lock"), plan.research_locks[1])},
    ):
        with pytest.raises(ValueError, match="exclusive paths"):
            bridge.validate_plan(replace(plan, **changes))
    config.update(invented_only=False, attempt="actual1", cache=str(plan.actual_cache), output=str(plan.actual_output),
                  diagnostics=str(plan.actual_diagnostics), journal=str(plan.canonical_journal),
                  sessions=bridge.canonical(plan.full_geometry)["sessions"])
    bridge.validate_config(config, plan)
    with pytest.raises(ValueError, match="fixed EH actual1"):
        bridge.validate_config({**config, "attempt": "actual2"}, plan)

    original_root = tmp_path / "original"
    original_root.mkdir()
    original = eh_support_plan_at(original_root, snapshot=True)
    with pytest.raises(ValueError, match="retry marker is reserved"):
        bridge.validate_plan(replace(original,
                                     global_marker=original.global_marker.with_name(bridge.EH_SNAPSHOT_RETRY_MARKER)))


def test_eh_snapshot_retry_checks_prior_failure_under_locks_before_started(tmp_path, monkeypatch):
    import fcntl

    from research import intraday_scenario_snapshot_retry as retry

    plan = eh_support_plan_at(tmp_path, retry=True)
    config = eh_support_config(plan)
    config.update(invented_only=False, attempt="actual1", output=str(plan.actual_output),
                  journal=str(plan.canonical_journal))
    for directory in (plan.actual_output.parent, plan.canonical_journal.parent, plan.local_worktree / "var"):
        directory.mkdir(parents=True)
    prior = {"invented": "failed evidence"}
    prior_raw = wire.encode(prior)
    (plan.local_worktree / bridge.EH_SNAPSHOT_RETRY_PRIOR).write_bytes(prior_raw)
    config["source_hashes"][bridge.EH_SNAPSHOT_RETRY_PRIOR] = wire.digest(prior_raw)
    for module, names in (
        (bridge, ("_set_deadline", "GitHelpers", "verify_sources", "validate_eh_support_receipt_audit",
                  "validate_qualification", "require_free_disk")),
        (wire, ("apply_role_limits", "native_cache")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, lambda *args, **kwargs: None)

    def reject_prior(root, pin):
        assert (root, pin) == (plan.local_execution_root, prior)
        for path in plan.research_locks:
            with path.open("rb") as handle, pytest.raises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise ValueError("invented prior failure changed")

    monkeypatch.setattr(retry, "verify_original_failure", reject_prior)
    outgoing = BytesIO()
    with pytest.raises(ValueError, match="invented prior failure changed"):
        bridge.coordinator(config, plan, plan.local_worktree, BytesIO(), outgoing, unit_lock_fd=1)
    assert not plan.canonical_journal.exists()
    assert outgoing.getvalue() == b""


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
@pytest.mark.parametrize("profile", (wire.ORDINARY300, *ED_PROFILES), ids=lambda profile: profile.id)
def test_ed_keeps_existing_geometry_count_output_and_verification_guards(tmp_path, input_contract, profile):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path, input_contract), profile.id)
    bridge.validate_plan(replace(plan, output_max_bytes=512 * wire.MIB))
    with pytest.raises(ValueError, match="single output"):
        bridge.validate_plan(replace(plan, output_max_bytes=520 * wire.MIB))
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
@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
def test_ed_600_profile_is_immutable_and_scoped_to_the_four_registered_plans(tmp_path, unit_id, input_contract, profile):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path, input_contract, unit_id), profile.id)
    bridge.validate_plan(plan)
    assert bridge.profile_for_plan(plan) is profile
    bridge.validate_config(config_at(plan), plan)
    assert bridge.PROFILE is wire.ORDINARY300
    assert plan_at(tmp_path / "repo", tmp_path).resource_profile == wire.ORDINARY300.id
    with pytest.raises(FrozenInstanceError):
        plan.resource_profile = wire.ORDINARY300.id


@pytest.mark.parametrize("contract", [None, b"{}\n", bytearray(bridge.ED_H1_RECOVERY_CONTRACT),
    bridge.ED_H1_RECOVERY_CONTRACT + b" ", wire.encode({"approved": True})])
def test_as520_plan_requires_exact_immutable_recovery_contract_before_io(tmp_path, monkeypatch, contract):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), wire.ED_H1_600_AS520.id)
    monkeypatch.setattr(wire, "regular", lambda *_: pytest.fail("unadmitted recovery metadata IO"))
    with pytest.raises(ValueError, match="recovery contract"):
        bridge.validate_plan(replace(plan, recovery_contract=contract))


@pytest.mark.parametrize("profile", [wire.ORDINARY300, wire.ED_H1_600], ids=lambda profile: profile.id)
def test_recovery_contract_cannot_be_attached_to_legacy_or_ordinary_profile(tmp_path, profile):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), profile.id)
    with pytest.raises(ValueError, match="recovery contract requires"):
        bridge.validate_plan(replace(plan, recovery_contract=bridge.ED_H1_RECOVERY_CONTRACT))


@pytest.mark.parametrize("unit_id,input_contract,expected", [("ED-H1-SNAPSHOT", wire.ED_INPUTS_CONTRACT, "actual2"),
    ("ED-H1-RULES", wire.ED_SNAPSHOT_CONTRACT, "actual1"),
    ("ED-H1-LIGHTGBM", wire.ED_SNAPSHOT_CONTRACT, "actual1")])
def test_approved_recovery_keeps_one_fixed_actual_attempt_per_unit(tmp_path, unit_id, input_contract, expected):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path, input_contract, unit_id), wire.ED_H1_600_AS520.id)
    plan = replace(plan, actual_runner_ready=True)
    config = config_at(plan)
    config.update(invented_only=False, local_pipe_test=False, attempt=expected, cache=str(plan.actual_cache),
        output=str(plan.actual_output), diagnostics=str(plan.actual_diagnostics), journal=str(plan.canonical_journal))
    bridge.validate_config(config, plan)
    for attempt in ("actual1", "actual2", "actual3", "profile-v1"):
        if attempt != expected:
            with pytest.raises(ValueError, match="actual attempt"):
                bridge.validate_config({**config, "attempt": attempt}, plan)


def test_predecessor_guard_authenticates_only_fixed_control_metadata_and_retains_old_marker(tmp_path, monkeypatch):
    plan, _ = invented_recovery_predecessor(ed_plan_at(tmp_path / "repo", tmp_path), monkeypatch)
    raw = plan.canonical_journal.read_bytes()
    original_marker = plan.canonical_journal.parent / "ed-h1-common-unresolved.json"
    marker_raw = original_marker.read_bytes()
    real_bounded, real_record, accesses = bridge.bounded_read, bridge.failed_journal_record, []
    def bounded(path, maximum):
        assert path == original_marker
        accesses.append(("marker", path, maximum))
        return real_bounded(path, maximum)
    def record(path, offset, end, run_id):
        assert path == plan.canonical_journal and (offset, end) == (0, len(raw))
        accesses.append(("journal", path, offset, end))
        return real_record(path, offset, end, run_id)
    monkeypatch.setattr(bridge, "bounded_read", bounded)
    monkeypatch.setattr(bridge, "failed_journal_record", record)
    proof = bridge.validate_ed_recovery_predecessor({"invented_only": True}, plan)
    assert proof == bridge.ed_recovery_proof()
    assert [item[0] for item in accesses] == ["journal", "marker"]
    assert proof["market_input_accesses"] == 0
    assert plan.canonical_journal.read_bytes() == raw and original_marker.read_bytes() == marker_raw
    proof["canonical_offsets"][0] = 999
    assert bridge.ed_recovery_proof()["canonical_offsets"][0] == 0


def test_failed_terminal_reader_uses_only_fixed_line_inside_a_longer_journal(tmp_path):
    journal = tmp_path / "journal.jsonl"
    prefix = b'{"unrelated":"before"}\n'
    record = {"record_type": "discovery_trial", "status": "failed", "run_id": "invented"}
    raw = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode()
    journal.write_bytes(prefix + raw + b'{"unrelated":"after"}\n')
    assert bridge.failed_journal_record(journal, len(prefix), len(prefix + raw), "invented") == (
        record, wire.digest(raw), len(prefix + raw))
    # The ordinary lifecycle reader continues to require started+terminal.
    with pytest.raises(ValueError, match="span count"):
        bridge.journal_record(journal, len(prefix), "failed", "invented", end=len(prefix + raw))


@pytest.mark.parametrize("change", ["two_lines", "no_newline", "compact", "duplicate", "wrong_run",
    "started", "completed", "wrong_type", "negative", "bool_offset", "beyond_end", "oversize"])
def test_failed_terminal_reader_rejects_noncanonical_or_wrong_bounded_record(tmp_path, change):
    journal = tmp_path / "journal.jsonl"
    record = {"record_type": "discovery_trial", "status": "failed", "run_id": "invented"}
    if change in ("started", "completed"):
        record["status"] = change
    if change == "wrong_type":
        record["record_type"] = "other"
    raw = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode()
    if change == "two_lines":
        raw += raw
    elif change == "no_newline":
        raw = raw[:-1]
    elif change == "compact":
        raw = wire.encode(record)
    elif change == "duplicate":
        raw = raw.replace(b'{', b'{"status": "started", ', 1)
    elif change == "oversize":
        raw = b" " * 32769
    journal.write_bytes(raw)
    offset, end = (False if change == "bool_offset" else -1 if change == "negative" else 0), len(raw)
    if change == "beyond_end":
        end += 1
    with pytest.raises(ValueError):
        bridge.failed_journal_record(journal, offset, end, "other" if change == "wrong_run" else "invented")


@pytest.mark.parametrize("change", ["sha", "status", "run", "code", "source", "study", "window", "attempt",
    "invented", "input_contract", "resource_profile", "automatic_bool", "truncated", "missing"])
def test_recovery_predecessor_rejects_forged_or_incomplete_failed_control_metadata(tmp_path, monkeypatch, change):
    plan, terminal = invented_recovery_predecessor(ed_plan_at(tmp_path / "repo", tmp_path), monkeypatch)
    raw = plan.canonical_journal.read_bytes()
    if change == "missing":
        plan.canonical_journal.unlink()
    elif change == "truncated":
        plan.canonical_journal.write_bytes(raw[:-1])
    else:
        if change in ("sha", "code"):
            terminal["code_version"] = "0" * 40
        elif change == "status":
            terminal["status"] = "completed"
        elif change == "run":
            terminal["run_id"] = "another-run"
        elif change == "study":
            terminal["study_id"] = "ED-H1-RULES"
        elif change == "window":
            terminal["window"] = {**terminal["window"], "start": "2020-01-02"}
        else:
            key, value = {"source": ("source_manifest_sha256", "0" * 64), "attempt": ("attempt", "actual2"),
                "invented": ("invented_only", True), "input_contract": ("input_contract", wire.EC_INPUT_CONTRACT),
                "resource_profile": ("resource_profile", wire.ED_H1_600.id),
                "automatic_bool": ("automatic_retries", False)}[change]
            terminal["parameters"][key] = value
        last = (json.dumps(terminal, sort_keys=True, allow_nan=False) + "\n").encode()
        plan.canonical_journal.write_bytes(last)
        if change != "sha":
            # Re-pin only the bytes, so semantic guards are exercised separately.
            recovery = bridge.canonical(bridge.ED_H1_RECOVERY_CONTRACT)
            recovery.update(previous_terminal_sha256=wire.digest(last), previous_terminal_offsets=[0, len(last)])
            monkeypatch.setattr(bridge, "ED_H1_RECOVERY_CONTRACT", wire.encode(recovery))
            plan = replace(plan, recovery_contract=bridge.ED_H1_RECOVERY_CONTRACT)
    with pytest.raises((ValueError, FileNotFoundError)):
        bridge.validate_ed_recovery_predecessor({"invented_only": True}, plan)


@pytest.mark.parametrize("change", ["missing", "symlink", "hardlink", "directory", "whitespace", "attempt",
    "completed", "automatic_bool", "extra", "long", "journal_symlink", "marker_name", "output_path"])
def test_recovery_requires_exact_original_marker_bytes_and_safe_fixed_paths(tmp_path, monkeypatch, change):
    plan, _ = invented_recovery_predecessor(ed_plan_at(tmp_path / "repo", tmp_path), monkeypatch)
    marker = plan.canonical_journal.parent / "ed-h1-common-unresolved.json"
    if change in ("missing", "symlink", "directory"):
        marker.unlink()
        if change == "symlink":
            marker.symlink_to(tmp_path / "absent")
        elif change == "directory":
            marker.mkdir()
    elif change == "hardlink":
        (tmp_path / "linked-marker").hardlink_to(marker)
    elif change == "journal_symlink":
        target = tmp_path / "copied-invented-control"
        target.write_bytes(plan.canonical_journal.read_bytes())
        plan.canonical_journal.unlink()
        plan.canonical_journal.symlink_to(target)
    elif change == "marker_name":
        plan = replace(plan, global_marker=marker)
    elif change == "output_path":
        plan = replace(plan, actual_output=plan.actual_output.parent / "old-snapshot")
    elif change == "whitespace":
        marker.write_bytes(b" " + marker.read_bytes())
    elif change == "long":
        marker.write_bytes(b"x" * 4096)
    else:
        payload = json.loads(marker.read_bytes())
        if change == "attempt":
            payload["attempt"] = "actual2"
        elif change == "completed":
            payload["state"] = "completed"
        elif change == "automatic_bool":
            payload["automatic_retries"] = False
        else:
            payload["override"] = True
        marker.write_bytes(wire.encode(payload))
    with pytest.raises((ValueError, FileNotFoundError)):
        bridge.validate_ed_recovery_predecessor({"invented_only": True}, plan)


@pytest.mark.parametrize("invented_only", [True, False])
def test_recovery_guard_runs_under_locks_before_started_or_any_input_and_keeps_failure_evidence(
        tmp_path, monkeypatch, invented_only):
    plan, _ = invented_recovery_predecessor(ed_plan_at(tmp_path / "repo", tmp_path), monkeypatch)
    config = config_at(plan)
    if not invented_only:
        config.update(invented_only=False, attempt="actual2", cache=str(plan.actual_cache),
            output=str(plan.actual_output), diagnostics=str(plan.actual_diagnostics), journal=str(plan.canonical_journal))
    out = Path(config["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    original_control = plan.canonical_journal.read_bytes()
    marker = out / "unresolved.json" if invented_only else plan.global_marker
    lock_events = []
    real_locks = wire.research_locks
    @contextmanager
    def locks(paths, active_marker, attempt):
        with real_locks(paths, active_marker, attempt):
            lock_events.append("held")
            try:
                yield
            finally:
                lock_events.append("released")
    def guard(value, fixed):
        assert lock_events == ["held"] and marker.exists()
        assert value is config and fixed is plan
        raise ValueError("invented predecessor refusal")
    monkeypatch.setattr(wire, "research_locks", locks)
    monkeypatch.setattr(wire, "apply_role_limits", lambda *args, **kwargs: None)
    monkeypatch.setattr(wire, "fixed_deadline", lambda *_: None)
    monkeypatch.setattr(bridge, "GitHelpers", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge, "verify_sources", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge, "validate_qualification", lambda *_: None)
    monkeypatch.setattr(bridge, "require_free_disk", lambda *_: None)
    monkeypatch.setattr(bridge, "validate_ed_recovery_predecessor", guard)
    monkeypatch.setattr(wire, "read_header", lambda *_: pytest.fail("input protocol before predecessor guard"))
    monkeypatch.setattr(wire, "send_header", lambda *_: pytest.fail("STARTED before predecessor guard"))
    monkeypatch.setattr(bridge, "actual_input_metadata", lambda *_: pytest.fail("price input before guard"))
    with pytest.raises(ValueError, match="predecessor refusal"):
        bridge.coordinator(config, plan, plan.remote_worktree, BytesIO(), BytesIO())
    assert lock_events == ["held", "released"]
    assert plan.canonical_journal.read_bytes() == original_control
    assert not invented_only or not Path(config["journal"]).exists()
    assert marker.exists() and (out / "bridge-failure.json").exists()
    failure = json.loads((out / "bridge-failure.json").read_bytes())
    assert failure["status"] == "unknown" and failure["partial_evidence_preserved"] is True
    assert (plan.canonical_journal.parent / "ed-h1-common-unresolved.json").exists()


def test_legacy_predecessor_guard_performs_no_io(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "bounded_read", lambda *_: pytest.fail("legacy marker read"))
    monkeypatch.setattr(bridge, "journal_record", lambda *args, **kwargs: pytest.fail("legacy journal read"))
    assert bridge.validate_ed_recovery_predecessor({}, plan_at(tmp_path / "repo", tmp_path)) is None


@pytest.mark.parametrize("change", ["ec", "wrong_spec", "wrong_unit", "tcn_unit", "old_contract", "core",
                                    "unknown", "mutable", "forged"])
@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
def test_ed_cpu600_cannot_be_borrowed_by_another_plan(tmp_path, change, profile):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), profile.id)
    if change == "ec":
        plan = replace(plan_at(tmp_path / "repo", tmp_path), resource_profile=profile.id)
    elif change == "wrong_spec":
        plan = replace(plan, specification_path="configs/invented.json")
    elif change == "wrong_unit":
        plan = replace(plan, unit_id="ED-H2-SNAPSHOT")
    elif change == "tcn_unit":
        plan = replace(plan, unit_id="ED-H1-TCN")
    elif change == "old_contract":
        plan = replace(plan, input_contract=wire.EC_INPUT_CONTRACT)
    elif change == "core":
        plan = replace(plan, resource_profile=wire.CORE600.id)
    elif change == "unknown":
        plan = replace(plan, resource_profile="unknown")
    elif change == "mutable":
        plan = replace(plan, resource_profile=profile)
    else:
        plan = replace(plan, resource_profile=replace(profile))
    with pytest.raises(ValueError):
        bridge.profile_for_plan(plan)


@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
def test_ed_worker_budget_has_only_the_approved480_scope_and_unchanged_as(monkeypatch, tmp_path, profile):
    from research import research_budget
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), profile.id)
    passed = []
    monkeypatch.setattr(research_budget, "ResearchBudget", lambda **kwargs: passed.append(kwargs))
    bridge.worker_budget(plan)
    bridge.worker_budget(replace(plan, resource_profile=wire.ORDINARY300.id, recovery_contract=None))
    assert passed == [
        {"cpu_seconds": 480, "address_space_bytes": 352 * wire.MIB,
         "operator_cpu_scope": "ED-H1-operator-2026-10-31"},
        {"cpu_seconds": 180, "address_space_bytes": 352 * wire.MIB}]


@pytest.mark.parametrize("change", ["config_ordinary", "config_other_ed", "plan_ordinary", "other_ed_unit"])
@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
def test_config_cannot_override_a_plans_approved_profile_or_unit(tmp_path, change, profile):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), profile.id)
    config = config_at(plan)
    if change == "config_ordinary":
        config["resource_profile"] = wire.ORDINARY300.id
    elif change == "config_other_ed":
        config["resource_profile"] = next(other.id for other in ED_PROFILES if other is not profile)
    elif change == "plan_ordinary":
        plan = replace(plan, resource_profile=wire.ORDINARY300.id)
    else:
        config["unit_id"] = "ED-H1-RULES"
    with pytest.raises(ValueError, match="profile|unit/plan"):
        bridge.validate_config(config, plan)


@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
def test_worker_bootstrap_applies480_before_plan_load_without_an_initial180(monkeypatch, tmp_path, profile):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), profile.id)
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
    assert events == [("caps", "worker", profile.id, 480), ("load_plan",), ("worker",)]


@pytest.mark.parametrize("change", ["ec_unit", "h2_unit", "tcn_unit", "missing_unit", "missing_spec",
                                    "invalid_spec_sha", "core", "unknown", "forged"])
@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
def test_invalid_bootstrap_cannot_load_a_plan_apply_caps_or_open_inputs(monkeypatch, tmp_path, change, profile):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), profile.id)
    config = config_at(plan)
    if change == "ec_unit":
        config["unit_id"] = "EC-P09"
    elif change == "h2_unit":
        config["unit_id"] = "ED-H2-SNAPSHOT"
    elif change == "tcn_unit":
        config["unit_id"] = "ED-H1-TCN"
    elif change == "missing_unit":
        config.pop("unit_id")
    elif change == "missing_spec":
        config["source_hashes"].pop(ED_SPECIFICATION)
    elif change == "invalid_spec_sha":
        config["source_hashes"][ED_SPECIFICATION] = "invalid"
    elif change == "core":
        config["resource_profile"] = wire.CORE600.id
    elif change == "forged":
        config["resource_profile"] = replace(profile)
    else:
        config["resource_profile"] = "unknown"
    monkeypatch.setattr(wire, "apply_role_limits", lambda *args, **kwargs: pytest.fail("unadmitted role cap"))
    hooks = bridge.UnitHooks(lambda *args: pytest.fail("unadmitted source plan"),
        lambda *args: pytest.fail("unadmitted generation"), lambda *args: pytest.fail("unadmitted computation"))
    with pytest.raises(ValueError):
        bridge.run_role("worker", config, hooks, root=plan.local_worktree, incoming=BytesIO(), outgoing=BytesIO())


@pytest.mark.parametrize("selected_profile,plan_profile", [
    (wire.ED_H1_600, wire.ORDINARY300), (wire.ED_H1_600_AS520, wire.ORDINARY300),
    (wire.ED_H1_600_AS520, wire.ED_H1_600), (wire.ED_H1_600, wire.ED_H1_600_AS520)],
    ids=lambda profile: profile.id)
def test_valid_bootstrap_but_mismatched_plan_is_rejected_before_worker_dispatch(monkeypatch, tmp_path,
                                                                             selected_profile, plan_profile):
    selected = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), selected_profile.id)
    plan = with_profile(selected, plan_profile.id)
    config = config_at(selected)
    monkeypatch.setattr(wire, "fixed_deadline", lambda *args: None)
    applied = []
    monkeypatch.setattr(wire, "apply_role_limits", lambda role, *, profile: applied.append(profile.id))
    monkeypatch.setattr(bridge, "restrict_worker_network", lambda: None)
    monkeypatch.setattr(bridge, "worker", lambda *args: pytest.fail("mismatched profile reached worker"))
    hooks = bridge.UnitHooks(lambda _config: plan, lambda *args: None, lambda *args: None)
    with pytest.raises(ValueError, match="resource profile"):
        bridge.run_role("worker", config, hooks, root=plan.local_worktree, incoming=BytesIO(), outgoing=BytesIO())
    assert applied == [selected_profile.id]


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
    if plan.recovery_contract is not None:
        started["parameters"].update(bridge._ed_recovery_parameters(plan))
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
    if plan.recovery_contract is not None:
        publication["recovery_predecessor"] = bridge.ed_recovery_proof()
    config["qualification"] = {"output": str(out), "publication_sha256": "0" * 64}
    return plan, config, publication


def save_publication(config, publication):
    raw = wire.encode(publication)
    path = Path(config["qualification"]["output"]) / "publication.json"
    path.write_bytes(raw)
    config["qualification"]["publication_sha256"] = wire.digest(raw)


@pytest.mark.parametrize("change", ["missing_proof", "proof_type", "terminal_sha", "contract_sha", "canonical_sha",
    "canonical_terminal"])
def test_as520_qualification_binds_the_original_predecessor_proof_and_canonical_parameters(tmp_path, change):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), wire.ED_H1_600_AS520.id)
    plan, config, publication = qualification_evidence(tmp_path, plan)
    save_publication(config, publication)
    bridge.validate_qualification(config, plan)
    if change == "missing_proof":
        publication.pop("recovery_predecessor")
    elif change == "proof_type":
        publication["recovery_predecessor"]["verified"] = 1
    elif change in ("terminal_sha", "contract_sha"):
        publication["recovery_predecessor"]["terminal_record_sha256" if change == "terminal_sha" else
            "recovery_contract_sha256"] = "0" * 64
    else:
        journal = Path(publication["invented_journal"])
        records = [json.loads(line) for line in journal.read_bytes().splitlines()]
        key = "recovery_contract_sha256" if change == "canonical_sha" else "recovery_predecessor_terminal_sha256"
        for record in records:
            record["parameters"][key] = "0" * 64
        raw = b"".join(wire.encode(record) for record in records)
        journal.write_bytes(raw)
        publication["terminal_record_sha256"] = wire.digest(wire.encode(records[-1]))
        publication["canonical_offsets"][1] = len(raw)
    save_publication(config, publication)
    with pytest.raises(ValueError, match="recovery publication/canonical proof"):
        bridge.validate_qualification(config, plan)


def prior_snapshot_evidence(tmp_path, resource_profile=wire.ORDINARY300.id):
    plan = ed_plan_at(tmp_path / "repo", tmp_path, wire.ED_SNAPSHOT_CONTRACT)
    plan = with_profile(plan, resource_profile)
    source = (Path(plan.input_pins[0].original_path) if plan.recovery_contract is not None else
              tmp_path / "prior" / "h1-snapshot.jsonl.gz")
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
    if plan.recovery_contract is not None:
        started["parameters"].update(bridge._ed_recovery_parameters(plan))
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
    if plan.recovery_contract is not None:
        pub["recovery_predecessor"] = bridge.ed_recovery_proof()
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


@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
@pytest.mark.parametrize("foreign", ["ordinary", "other_ed"])
@pytest.mark.parametrize("evidence", ["publication", "canonical"])
def test_ed600_qualification_rejects_a_foreign_profile_receipt(tmp_path, profile, foreign, evidence):
    plan = with_profile(ed_plan_at(tmp_path / "repo", tmp_path), profile.id)
    plan, config, publication = qualification_evidence(tmp_path, plan)
    save_publication(config, publication)
    bridge.validate_qualification(config, plan)
    foreign_id = (wire.ORDINARY300.id if foreign == "ordinary" else
                  next(other.id for other in ED_PROFILES if other is not profile))
    if evidence == "publication":
        publication["resource_profile"] = foreign_id
    else:
        journal = Path(publication["invented_journal"])
        records = [json.loads(line) for line in journal.read_bytes().splitlines()]
        for record in records:
            record["parameters"]["resource_profile"] = foreign_id
        raw = b"".join(wire.encode(record) for record in records)
        journal.write_bytes(raw)
        publication["terminal_record_sha256"] = wire.digest(wire.encode(records[-1]))
        publication["canonical_offsets"][1] = len(raw)
    save_publication(config, publication)
    with pytest.raises(ValueError, match="same-source bridge qualification|canonical terminal"):
        bridge.validate_qualification(config, plan)


@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
@pytest.mark.parametrize("foreign", ["ordinary", "other_ed"])
@pytest.mark.parametrize("evidence", ["publication", "canonical"])
def test_ed600_prior_snapshot_requires_same_profile_in_publication_and_canonical_span(tmp_path, profile, foreign, evidence):
    plan, config, pub = prior_snapshot_evidence(tmp_path, profile.id)
    bridge.validate_ed_prior_snapshot(config, plan)
    foreign_id = (wire.ORDINARY300.id if foreign == "ordinary" else
                  next(other.id for other in ED_PROFILES if other is not profile))
    if evidence == "publication":
        pub["resource_profile"] = foreign_id
    else:
        records = [json.loads(line) for line in plan.canonical_journal.read_bytes().splitlines()]
        for record in records[:2]:
            record["parameters"]["resource_profile"] = foreign_id
        first, last = wire.encode(records[0]), wire.encode(records[1])
        plan.canonical_journal.write_bytes(first + last + wire.encode(records[2]))
        pub["terminal_record_sha256"] = wire.digest(last)
        pub["canonical_offsets"][1] = len(first) + len(last)
    raw = wire.encode(pub)
    (Path(plan.input_pins[0].original_path).parent / "publication.json").write_bytes(raw)
    config.update(snapshot_actual_publication_sha256=wire.digest(raw),
        snapshot_actual_publication_b64=base64.b64encode(raw).decode())
    config["resolved_snapshot"]["publication_sha256"] = wire.digest(raw)
    with pytest.raises(ValueError, match="completed full prior|canonical completion"):
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
RECOVERY_CONTRACT = __RECOVERY_CONTRACT__
if RECOVERY_CONTRACT is not None:
    # This driver and its prior failed control file are entirely invented.
    b.ED_H1_RECOVERY_CONTRACT = RECOVERY_CONTRACT
def load_plan(config):
    return b.UnitPlan(unit_id=UNIT_ID, specification_path=SPECIFICATION_PATH,
        source_names=NAMES, source_roster_sha256=w.digest(w.encode(list(NAMES))),
        input_pins=tuple(b.InputPin(*pin) for pin in __INPUT_PINS__), decoded_stream_sha256="b"*64,
        window=__WINDOW__, full_geometry=__GEOMETRY__, candidate_bounds=__BOUNDS__,
        private_entrypoint="var/invented-driver.py",local_worktree=ROOT,remote_worktree=ROOT,
        actual_cache=PARENT/"cache"/"actual",actual_output=Path(__ACTUAL_OUTPUT__),
        actual_diagnostics=PARENT/"diagnostics"/"actual",canonical_journal=PARENT/"canonical"/"experiments.jsonl",
        global_marker=Path(__GLOBAL_MARKER__),
        research_locks=(PARENT/"canonical"/"research.lock",PARENT/"output"/"campaign.lock"),
        invented_cache_parent=PARENT/"cache",invented_output_parent=PARENT/"output",
        invented_diagnostics_parent=PARENT/"diagnostics",output_filename="result.json",output_max_bytes=4096,
        ssh_argv=("ssh","-i","/invented-unused/key","invented-unused.invalid"),
        remote_prefix=("python3","-I","-S",str(ROOT/"var/invented-driver.py"),"coordinator"),
        input_contract=INPUT_CONTRACT,resource_profile=RESOURCE_PROFILE,recovery_contract=RECOVERY_CONTRACT)
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
if mode=="coordinator" and config.get("test_suppress_traceback"):
    sys.excepthook=lambda *args: None
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


def invented_repository(tmp_path, input_contract=wire.EC_INPUT_CONTRACT, resource_profile=wire.ORDINARY300.id,
                        *, monkeypatch=None):
    root = tmp_path / "repo"
    plan = (plan_at(root, tmp_path) if input_contract == wire.EC_INPUT_CONTRACT else
            ed_plan_at(root, tmp_path, input_contract))
    plan = with_profile(plan, resource_profile)
    if resource_profile == wire.ED_H1_600_AS520.id:
        assert monkeypatch is not None
        plan, _ = invented_recovery_predecessor(plan, monkeypatch)
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
    source = source.replace("__RECOVERY_CONTRACT__", repr(plan.recovery_contract))
    source = source.replace("__INPUT_PINS__", repr(tuple((pin.name, pin.original_path, pin.sha256, pin.maximum)
        for pin in plan.input_pins)))
    source = source.replace("__ACTUAL_OUTPUT__", repr(str(plan.actual_output)))
    source = source.replace("__GLOBAL_MARKER__", repr(str(plan.global_marker)))
    (root / "var/invented-driver.py").write_text(source)
    for parent in ("cache", "output", "diagnostics", "canonical"):
        (tmp_path / parent).mkdir(exist_ok=True)
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
@pytest.mark.parametrize("profile", [wire.ORDINARY300, wire.ED_H1_600_AS520], ids=lambda profile: profile.id)
def test_tiny_whole_common_pipe_and_terminal_failures(tmp_path, monkeypatch, fault, profile):
    input_contract = wire.EC_INPUT_CONTRACT if profile is wire.ORDINARY300 else wire.ED_INPUTS_CONTRACT
    root, plan, config = invented_repository(tmp_path, input_contract, profile.id, monkeypatch=monkeypatch)
    original_control = plan.canonical_journal.read_bytes() if plan.recovery_contract is not None else None
    config["test_fault"] = fault
    command = bridge.process_command("controller", config, plan, root)
    completed = subprocess.run(command, cwd=root, capture_output=True, timeout=55)
    out, cache, diagnostics = Path(config["output"]), Path(config["cache"]), Path(config["diagnostics"])
    detail = completed.stderr.decode(errors="replace")
    if completed.returncode:
        detail += "\n" + "\n".join(path.read_text() for path in diagnostics.glob("*-stderr.txt"))
    assert (completed.returncode == 0) == (fault is None), detail
    assert Path(config["journal"]).exists(), detail
    journal_bytes = Path(config["journal"]).read_bytes()
    records = [json.loads(line) for line in journal_bytes.splitlines()]
    assert len(records) == 2, detail
    assert records[0]["status"] == "started"
    network_failure = fault in ("worker_network", "worker_network_no_receipt")
    assert records[-1]["status"] == ("failed" if fault == "remote_output" or network_failure else "completed")
    assert records[0]["parameters"]["invented_only"] is True
    assert records[0]["parameters"]["resource_profile"] == profile.id
    assert plan.canonical_journal.exists() is (plan.recovery_contract is not None)
    if fault is None:
        assert not (out / "unresolved.json").exists()
        publication = json.loads((out / "publication.json").read_bytes())
        assert publication["full_bridge"] is False
        assert publication["local_pipe_test"] is True
        assert publication["aggregate_budget"]["hard_cpu_sum_seconds"] == profile.cpu_seconds
        assert publication["aggregate_budget"]["hard_address_space_sum_bytes"] == profile.address_space_bytes
        assert publication["output_sha256"] == wire.digest((out / "result.json").read_bytes())
        if plan.recovery_contract is not None:
            assert publication["recovery_predecessor"] == bridge.ed_recovery_proof()
            assert records[0]["parameters"] | bridge._ed_recovery_parameters(plan) == records[0]["parameters"]
            assert plan.canonical_journal.read_bytes() == original_control
            assert (plan.canonical_journal.parent / "ed-h1-common-unresolved.json").exists()
        else:
            assert "recovery_predecessor" not in publication
            assert not any(key.startswith("recovery_") for key in records[0]["parameters"])
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
@pytest.mark.parametrize("profile", ED_PROFILES, ids=lambda profile: profile.id)
def test_ed600_tiny_pipe_keeps_same_ack_and_as_with_the_approved_worker_scope(tmp_path, monkeypatch,
                                                                          input_contract, profile):
    root, plan, config = invented_repository(tmp_path, input_contract, profile.id, monkeypatch=monkeypatch)
    completed = subprocess.run(bridge.process_command("controller", config, plan, root),
                               cwd=root, capture_output=True, timeout=55)
    diagnostics = Path(config["diagnostics"])
    detail = completed.stderr.decode(errors="replace")
    if completed.returncode:
        detail += "\n" + "\n".join(path.read_text() for path in diagnostics.glob("*-stderr.txt"))
    assert completed.returncode == 0, detail
    publication = json.loads((Path(config["output"]) / "publication.json").read_bytes())
    assert publication["resource_profile"] == profile.id
    assert publication["full_bridge"] is False and publication["local_pipe_test"] is True
    aggregate = publication["aggregate_budget"]
    assert aggregate["hard_cpu_sum_seconds"] == 600
    assert aggregate["hard_address_space_sum_bytes"] == profile.address_space_bytes
    assert aggregate["role_limits"] == profile.role_limits()
    for role in profile.role_limits():
        expected = wire.parent_limits(role, profile=profile)
        assert aggregate["samples"][role]["parent_limits"] == expected
        assert aggregate["samples"][role]["readback"]["rlimit_as"] == [expected["address_space_bytes"]] * 2
        assert aggregate["samples"][role]["readback"]["rlimit_cpu"] == [expected["cpu_seconds"]] * 2
    assert aggregate["samples"]["worker"]["readback"]["rlimit_cpu"] == [480, 480]
    assert aggregate["samples"]["worker"]["readback"]["rlimit_as"] == [352 * wire.MIB] * 2
    budget = json.loads((Path(config["cache"]) / "worker-budget.json").read_bytes())["budget"]
    assert budget["operator_cpu_scope"] == "ED-H1-operator-2026-10-31"
    assert budget["limits"] == {"cpu_seconds": 480, "address_space_bytes": 352 * wire.MIB,
                                 "wall_seconds": 1200, "nice": 10}
    assert (Path(config["cache"]) / "completed-ack.json").exists()
    records = [json.loads(line) for line in Path(config["journal"]).read_bytes().splitlines()]
    assert [record["status"] for record in records] == ["started", "completed"]
    assert records[0]["parameters"]["resource_profile"] == profile.id
    assert not (Path(config["output"]) / "unresolved.json").exists()
    assert (diagnostics / "process-receipt.json").exists()
    assert bridge.PROFILE is wire.ORDINARY300


@pytest.mark.parametrize("profile,input_contract,scope", [
    (wire.ORDINARY300.id, wire.EC_INPUT_CONTRACT, "ED-H1-operator-2026-10-31"),
    (wire.ED_H1_600.id, wire.ED_INPUTS_CONTRACT, "ED-H1-operator-2026-10-30"),
    (wire.ED_H1_600_AS520.id, wire.ED_INPUTS_CONTRACT, "ED-H1-operator-2026-10-30")])
@pytest.mark.parametrize("suppress_traceback", [False, True])
def test_coordinator_rejects_operator_override_or_wrong_ed_scope(tmp_path, monkeypatch, profile, input_contract, scope,
                                                               suppress_traceback):
    root, plan, config = invented_repository(tmp_path, input_contract, profile, monkeypatch=monkeypatch)
    config.update(test_fault="budget_scope", test_budget_scope=scope, test_suppress_traceback=suppress_traceback)
    completed = subprocess.run(bridge.process_command("controller", config, plan, root),
                               cwd=root, capture_output=True, timeout=55)
    assert completed.returncode != 0
    diagnostic = Path(config["diagnostics"])
    assert "registered worker budget not complete" in (diagnostic / "ssh-stderr.txt").read_text()
    journal_bytes = Path(config["journal"]).read_bytes()
    records = [json.loads(line) for line in journal_bytes.splitlines()]
    assert [record["status"] for record in records] == ["started", "failed"]
    assert (Path(config["output"]) / "unresolved.json").exists()
    assert_failed_bridge_receipts(config, journal_bytes)


@pytest.mark.parametrize("resource_profile", [wire.ORDINARY300.id, wire.ED_H1_600.id, wire.ED_H1_600_AS520.id])
def test_cold_probe_is_lean_source_only_and_nonqualifying(tmp_path, monkeypatch, resource_profile):
    input_contract = wire.EC_INPUT_CONTRACT if resource_profile == wire.ORDINARY300.id else wire.ED_INPUTS_CONTRACT
    root, plan, config = invented_repository(tmp_path, input_contract, resource_profile, monkeypatch=monkeypatch)
    if plan.recovery_contract is not None:
        # A source probe must still succeed when operational control evidence is
        # absent: plan loading is pure and the predecessor guard is not called.
        plan.canonical_journal.unlink()
        (plan.canonical_journal.parent / "ed-h1-common-unresolved.json").unlink()
    completed = subprocess.run(bridge.process_command("source-probe", config, plan, root),
                               cwd=root, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode()
    receipt = json.loads(completed.stdout)
    assert receipt["qualifies_actual"] is False
    assert receipt["journal_module_imported"] is True
    assert receipt["market_input_accesses"] == receipt["journal_accesses"] == receipt["marker_accesses"] == 0
    assert receipt["output_directories_created"] == 0
    assert receipt["role_sample"]["address_space_bytes"] < 32 * wire.MIB
    parent = wire.parent_limits("coordinator", profile=resource_profile)
    assert receipt["role_sample"]["readback"]["rlimit_as"] == [parent["address_space_bytes"]] * 2
    assert not Path(config["output"]).exists()
    assert not Path(config["journal"]).exists()
    assert not Path(config["cache"]).exists()


def test_cold_identity_uses_the_public_hostname_callable_without_socket_wrapper():
    import sys

    program = r'''
import importlib.util
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(sys.argv[1]) / "python"))
blocked = {"socket", "shlex"}
if importlib.util.find_spec("_sha2") is not None:
    blocked.add("_hashlib")
assert blocked.isdisjoint(sys.modules)
class RejectUnneededIdentityImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError("cold identity loaded " + fullname)
fence = RejectUnneededIdentityImports()
sys.meta_path.insert(0, fence)
from research import intraday_campaign_wsl_bridge as bridge
observed = bridge.machine_sha()
assert blocked.isdisjoint(sys.modules)
sys.meta_path.remove(fence)

import socket
assert bridge.gethostname is socket.gethostname
expected = bridge.wire.digest(bridge.wire.encode({"hostname": socket.gethostname(), "kernel": list(os.uname()),
    "python": sys.version, "executable": str(Path(sys.executable).resolve())}))
assert observed == expected
print("same-public-host-identity")
'''
    completed = subprocess.run([sys.executable, "-I", "-S", "-c", program, str(ROOT)],
                               capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout.decode().strip() == "same-public-host-identity"


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
@pytest.mark.parametrize("profile", [wire.ORDINARY300, wire.ED_H1_600_AS520], ids=lambda profile: profile.id)
def test_terminal_alone_cannot_hide_truncated_frame_or_nonzero_child(tmp_path, monkeypatch, fault, profile):
    input_contract = wire.EC_INPUT_CONTRACT if profile is wire.ORDINARY300 else wire.ED_INPUTS_CONTRACT
    root, plan, config = invented_repository(tmp_path, input_contract, profile.id, monkeypatch=monkeypatch)
    config["test_fault"] = fault
    completed = subprocess.run(bridge.process_command("controller", config, plan, root),
                               cwd=root, capture_output=True, timeout=55)
    assert completed.returncode != 0
    journal = Path(config["journal"]).read_bytes().splitlines()
    assert [json.loads(line)["status"] for line in journal] == ["started", "completed"]
    diagnostics = Path(config["diagnostics"])
    assert (diagnostics / "process-failure.json").exists()
    assert not (diagnostics / "process-receipt.json").exists()
