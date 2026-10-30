"""Source and tiny invented-only ordinary bridge tests; no actual/GCP access."""
from dataclasses import FrozenInstanceError, replace
from copy import deepcopy
import json
from pathlib import Path
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


def sample(role):
    caps = bridge.PROFILE.role_limits()[role]
    parent = wire.parent_limits(role, profile=bridge.PROFILE)
    return {"role": role, "pid": 123, "resource_profile": bridge.PROFILE.id, "limits": caps,
        "parent_limits": parent, "cpu_self_seconds": 0.01, "children_cpu_diagnostic_seconds": 99,
        "cpu_counting": "invented sample; no SSH qualification", "address_space_bytes": 8 * wire.MIB,
        "helpers": {"cpu_seconds": 0, "count": 0, "readbacks": []},
        "readback": {"rlimit_cpu": [parent["cpu_seconds"]] * 2,
            "rlimit_as": [parent["address_space_bytes"]] * 2, "nice": 10,
            "affinity": [0], "oom_score_adj": "1000"}}


def config_at(plan, attempt="tiny"):
    name = plan.unit_id + "-invented-" + attempt
    return {"invented_only": True, "resource_profile": bridge.PROFILE.id, "automatic_retries": 0,
        "wall_seconds": 1200, "sessions": 2, "source_hashes": {name: "c" * 64 for name in NAMES},
        "merge_commit": "d" * 40, "tree": "e" * 40, "reviewed_commit": "f" * 40,
        "attempt": attempt, "deadline_epoch": time.time() + 45, "local_pipe_test": True,
        "cache": str(plan.invented_cache_parent / name), "output": str(plan.invented_output_parent / name),
        "journal": str(plan.invented_output_parent / name / "invented-experiments.jsonl"),
        "diagnostics": str(plan.invented_diagnostics_parent / name), "test_ssh_sample": sample("ssh_client")}


def test_plan_is_frozen_and_rejects_roster_and_mutable_metadata(tmp_path):
    plan = plan_at(tmp_path / "repo", tmp_path)
    bridge.validate_plan(plan)
    with pytest.raises(FrozenInstanceError):
        plan.unit_id = "changed"
    for changed in (replace(plan, source_names=NAMES[:-1]), replace(plan, full_geometry={"sessions": 3}),
                    replace(plan, source_roster_sha256="0" * 64), replace(plan, input_pins=list(plan.input_pins))):
        with pytest.raises(ValueError):
            bridge.validate_plan(changed)


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


def qualification_evidence(tmp_path):
    """A fabricated qualification envelope tests guards, never actual readiness."""
    plan = plan_at(tmp_path / "repo", tmp_path)
    out = plan.invented_output_parent / (plan.unit_id + "-invented-fabricated")
    out.mkdir(parents=True)
    config = config_at(plan)
    config.update(local_identity={"invented": "local"}, remote_identity={"invented": "remote"})
    raw = b"invented verified output\n"
    (out / plan.output_filename).write_bytes(raw)
    aggregate = wire.aggregate_roles({role: sample(role) for role in bridge.PROFILE.role_limits()}, profile=bridge.PROFILE)
    result = {"output_sha256": wire.digest(raw), "output_bytes": len(raw), "verification_complete": True,
              "aggregate_budget": aggregate, "automatic_retries": 0}
    started = {"status": "started", "run_id": "fabricated-guard-test", "study_id": plan.unit_id,
        "specification_sha256": config["source_hashes"][plan.specification_path], "code_version": config["merge_commit"],
        "window": bridge.canonical(plan.window), "parameters": {"invented_only": True,
            "source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])), "resource_profile": bridge.PROFILE.id}}
    terminal = {**started, "status": "completed", "result": result}
    start_raw, terminal_raw = wire.encode(started), wire.encode(terminal)
    journal = out / "invented-experiments.jsonl"
    journal.write_bytes(start_raw + terminal_raw)
    publication = {"status": "completed", "unit_id": plan.unit_id, "resource_profile": bridge.PROFILE.id,
        "invented_only": True, "geometry": bridge.canonical(plan.full_geometry),
        "verification_complete": True, "independent_verification": {"verified": True},
        "candidate_counts": {"A": {"D+1": 3, "D+2": 3}}, "full_bridge": True, "local_pipe_test": False,
        "source_hashes": config["source_hashes"], "local_identity": config["local_identity"],
        "remote_identity": config["remote_identity"], "aggregate_budget": aggregate,
        "output_sha256": wire.digest(raw), "output_bytes": len(raw), "invented_journal": str(journal),
        "canonical_offsets": [0, len(start_raw) + len(terminal_raw)], "run_id": terminal["run_id"],
        "terminal_record_sha256": wire.digest(terminal_raw)}
    config["qualification"] = {"output": str(out), "publication_sha256": "0" * 64}
    return plan, config, publication


def save_publication(config, publication):
    raw = wire.encode(publication)
    path = Path(config["qualification"]["output"]) / "publication.json"
    path.write_bytes(raw)
    config["qualification"]["publication_sha256"] = wire.digest(raw)


@pytest.mark.parametrize("mutation", ["profile", "geometry", "verification", "source", "runtime",
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


DRIVER = '''from pathlib import Path
import base64, json, os, sys
ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "python"))
from research import intraday_campaign_wsl_bridge as b
from research import intraday_campaign_wsl_transport as w
PARENT = ROOT.parent
NAMES = __NAMES__
def load_plan(config):
    return b.UnitPlan(unit_id="invented-ordinary", specification_path="configs/invented.json",
        source_names=NAMES, source_roster_sha256=w.digest(w.encode(list(NAMES))),
        input_pins=tuple(b.InputPin(name,"/invented-actual/"+name,"a"*64,(512 if name=="CC" else 64)*w.MIB)
                         for name in ("EA-A","EA-B","CC")), decoded_stream_sha256="b"*64,
        window=__WINDOW__, full_geometry=__GEOMETRY__, candidate_bounds=__BOUNDS__,
        private_entrypoint="var/invented-driver.py",local_worktree=ROOT,remote_worktree=ROOT,
        actual_cache=PARENT/"cache"/"actual",actual_output=PARENT/"output"/"actual",
        actual_diagnostics=PARENT/"diagnostics"/"actual",canonical_journal=PARENT/"canonical"/"experiments.jsonl",
        global_marker=PARENT/"canonical"/"ordinary-unresolved.json",
        research_locks=(PARENT/"canonical"/"research.lock",PARENT/"output"/"campaign.lock"),
        invented_cache_parent=PARENT/"cache",invented_output_parent=PARENT/"output",
        invented_diagnostics_parent=PARENT/"diagnostics",output_filename="result.json",output_max_bytes=4096,
        ssh_argv=("ssh","-i","/invented-unused/key","invented-unused.invalid"),
        remote_prefix=("python3","-I","-S",str(ROOT/"var/invented-driver.py"),"coordinator"))
def make_invented(directory,sessions,checkpoint):
    manifest=[]
    for name in ("EA-A","EA-B","CC"):
        checkpoint("invented_generation")
        raw=("invented "+name+"\\n").encode()
        w.exclusive_bytes(directory/name,raw)
        manifest.append(b.InputFile(name,str(directory/name),w.digest(raw),(512 if name=="CC" else 64)*w.MIB,len(raw)))
    return b.InputEnvelope(tuple(manifest),"b"*64,w.encode({"sessions":sessions,"targets":sessions}))
def compute_verify(directory,envelope,checkpoint):
    # This deliberately tiny producer and separately reconstructed verifier use
    # only invented byte counts, never scientific market/account calculations.
    if config.get("test_fault")=="worker_network":
        import socket
        socket.create_connection(("invented-never-resolve.invalid",9))
    counts=[]
    for item in envelope.manifest:
        checkpoint("invented_producer")
        counts.append(len(b.bounded_read(directory/"received"/item.name,64*w.MIB)))
    raw=w.encode({"invented_total_bytes":sum(counts)})
    checkpoint("invented_independent_verifier")
    independent=sum((directory/"received"/name).stat().st_size for name in ("EA-A","EA-B","CC"))
    w.require(json.loads(raw)["invented_total_bytes"]==independent,"independent verification differs")
    w.exclusive_bytes(directory/"result.json",raw)
    pin,size=w.file_digest(directory/"result.json",4096,checkpoint)
    return b.OutputReceipt(pin,size,envelope.geometry,w.encode({"verified":True,"method":"independent invented byte count"}),
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
        return self.stream.write(raw)
    def flush(self): return self.stream.flush()
mode=sys.argv[1]
config=json.loads(base64.b64decode(sys.argv[2],validate=True))
result=b.run_role(mode,config,b.UnitHooks(load_plan,make_invented,compute_verify),root=ROOT,
                  outgoing=FaultStream(sys.stdout.buffer,mode,config))
raise SystemExit(7 if mode=="worker" and config.get("test_fault")=="post_ack_nonzero" else result)
'''


def invented_repository(tmp_path):
    root = tmp_path / "repo"
    for name in NAMES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("python/research/"):
            target.write_bytes((ROOT / name).read_bytes())
    (root / "configs/invented.json").write_bytes(wire.encode({"id": "invented-ordinary"}))
    source = DRIVER.replace("__NAMES__", repr(NAMES)).replace("__WINDOW__", repr(WINDOW))
    source = source.replace("__GEOMETRY__", repr(GEOMETRY)).replace("__BOUNDS__", repr(BOUNDS))
    (root / "var/invented-driver.py").write_text(source)
    for parent in ("cache", "output", "diagnostics", "canonical"):
        (tmp_path / parent).mkdir()
    for args in (("init", "-q"), ("add", "."),
                 ("-c", "user.name=Invented", "-c", "user.email=invented@example.invalid", "commit", "-qm", "invented source")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    plan = plan_at(root, tmp_path)
    config = config_at(plan)
    config["source_hashes"] = {name: wire.file_digest(root / name, 16 * wire.MIB, lambda _: None)[0] for name in NAMES}
    for key, arg in (("merge_commit", "HEAD"), ("tree", "HEAD^{tree}"), ("reviewed_commit", "HEAD")):
        config[key] = subprocess.check_output(["git", "-C", str(root), "rev-parse", arg], text=True).strip()
    identity = {"source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
        "runtime_sha256": bridge.runtime_sha(), "machine_sha256": bridge.machine_sha(),
        "merge_commit": config["merge_commit"], "tree": config["tree"], "execution_host": bridge.HOST,
        "resource_profile": bridge.PROFILE.id}
    config.update(local_identity=identity, remote_identity=identity)
    return root, plan, config


@pytest.mark.parametrize("fault", [None, "remote_output", "completed_loss", "accepted_loss", "worker_network"])
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
    records = [json.loads(line) for line in Path(config["journal"]).read_bytes().splitlines()]
    assert len(records) == 2
    assert records[0]["status"] == "started"
    assert records[-1]["status"] == ("failed" if fault in ("remote_output", "worker_network") else "completed")
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
        assert (cache / "completed-ack.json").exists()
        assert (diagnostics / "process-receipt.json").exists()
    else:
        assert (out / "unresolved.json").exists()
        assert (out / "bridge-failure.json").exists()
        assert (diagnostics / "process-failure.json").exists()
        before = Path(config["journal"]).read_bytes()
        # A new attempt cannot reuse the exclusive output or clear the orphan.
        again = subprocess.run(command, cwd=root, capture_output=True, timeout=10)
        assert again.returncode != 0
        assert Path(config["journal"]).read_bytes() == before
        if fault == "worker_network":
            worker_failure = json.loads((cache / "worker-failure.json").read_bytes())
            assert worker_failure["error_type"] == "RuntimeError"
            assert "stdio IPC only" in (diagnostics / "worker-stderr.txt").read_text()


def test_cold_probe_is_lean_source_only_and_nonqualifying(tmp_path):
    root, plan, config = invented_repository(tmp_path)
    completed = subprocess.run(bridge.process_command("source-probe", config, plan, root),
                               cwd=root, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode()
    receipt = json.loads(completed.stdout)
    assert receipt["qualifies_actual"] is False
    assert receipt["market_input_accesses"] == receipt["journal_accesses"] == receipt["marker_accesses"] == 0
    assert receipt["role_sample"]["address_space_bytes"] < 32 * wire.MIB
    assert not Path(config["output"]).exists()
    assert not Path(config["journal"]).exists()
    assert not Path(config["cache"]).exists()


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
