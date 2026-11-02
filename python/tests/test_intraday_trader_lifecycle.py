"""Invented-only EE local lifecycle; no market, remote, or actual execution."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


ROOT = Path(__file__).resolve().parents[2]
NAMES = tuple(sorted((bridge.EE_SPECIFICATION_PATH, "var/invented-driver.py",
    "python/research/__init__.py", "python/research/experiment_log.py", "python/research/research_budget.py",
    "python/research/intraday_campaign_wsl_bridge.py", "python/research/intraday_campaign_wsl_transport.py")))


def plan_at(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    return bridge.UnitPlan(unit_id=bridge.EE_UNIT_ID, specification_path=bridge.EE_SPECIFICATION_PATH,
        source_names=NAMES, source_roster_sha256=wire.digest(wire.encode(list(NAMES))),
        input_pins=tuple(bridge.InputPin(name, "/invented-unused-original/" + name, "a" * 64, maximum)
                         for name, maximum in wire.EE_INPUT_BOUNDS), decoded_stream_sha256="b" * 64,
        window=wire.encode({"symbol": "INVENTED", "interval": "1m", "start": "2020-01-01", "end": "2020-01-02"}),
        full_geometry=wire.encode({"sessions": 2, "targets": 2}), candidate_bounds=wire.encode({"invented": 2}),
        private_entrypoint="var/invented-driver.py", local_worktree=tmp_path / "repo", remote_worktree=tmp_path / "repo",
        actual_cache=private / "cache" / "actual", actual_output=private / "output" / "actual",
        actual_diagnostics=private / "diagnostics" / "actual", canonical_journal=private / "canonical" / "experiments.jsonl",
        global_marker=private / "canonical" / "ee-unresolved.json",
        research_locks=(private / "canonical" / "research.lock", private / "output" / "campaign.lock"),
        invented_cache_parent=private / "cache", invented_output_parent=private / "output",
        invented_diagnostics_parent=private / "diagnostics", output_filename="result.json", output_max_bytes=4096,
        ssh_argv=(), remote_prefix=(), input_contract=wire.EE_INPUT_CONTRACT,
        resource_profile=wire.ORDINARY300.id, local_coordinator=True, local_execution_root=private)


def config_at(plan):
    name = plan.unit_id + "-invented-tiny"
    identity = {"execution_host": bridge.LOCAL_HOST}
    return {"unit_id": plan.unit_id, "invented_only": True, "resource_profile": plan.resource_profile,
        "automatic_retries": 0, "wall_seconds": 1200, "sessions": 2, "attempt": "tiny",
        "local_coordinator": True, "local_execution_root": str(plan.local_execution_root),
        "execution_host": bridge.LOCAL_HOST, "local_pipe_test": False,
        "local_identity": identity, "remote_identity": identity,
        "source_hashes": {name: "c" * 64 for name in plan.source_names},
        "merge_commit": "d" * 40, "tree": "e" * 40, "reviewed_commit": "f" * 40,
        "deadline_epoch": time.time() + 40,
        "cache": str(plan.invented_cache_parent / name), "output": str(plan.invented_output_parent / name),
        "journal": str(plan.invented_output_parent / name / "invented-experiments.jsonl"),
        "diagnostics": str(plan.invented_diagnostics_parent / name)}


def envelope_for(plan):
    return bridge.InputEnvelope(tuple(bridge.InputFile(**pin.metadata(1)) for pin in plan.input_pins),
                                plan.decoded_stream_sha256, plan.full_geometry, plan.input_contract)


def test_exact_registered_roster_and_contract_confusion(tmp_path):
    plan = plan_at(tmp_path)
    bridge.validate_plan(plan)
    envelope = envelope_for(plan)
    assert [(item.name, item.maximum) for item in envelope.manifest] == [
        ("CB", 16777216), ("CC", 536870912), ("CX", 536870912)]
    payload = envelope.payload()
    assert bridge.InputEnvelope.from_payload(payload, input_contract=wire.EE_INPUT_CONTRACT) == envelope
    for contract in (wire.EC_INPUT_CONTRACT, wire.ED_INPUTS_CONTRACT, wire.ED_SNAPSHOT_CONTRACT, "unknown"):
        with pytest.raises(ValueError):
            bridge.InputEnvelope.from_payload(payload, input_contract=contract)
        with pytest.raises(ValueError):
            wire.validate_registered_input_manifest(payload["inputs"], input_contract=contract)
    with pytest.raises(ValueError, match="unsupported ED"):
        wire.validate_ed_input_manifest(payload["inputs"], input_contract=wire.EE_INPUT_CONTRACT)
    largest = replace(envelope, manifest=tuple(bridge.InputFile(**pin.metadata(pin.maximum)) for pin in plan.input_pins))
    assert bridge.InputEnvelope.from_payload(largest.payload(), input_contract=wire.EE_INPUT_CONTRACT) == largest
    for manifest in (list(reversed(payload["inputs"])), payload["inputs"][:-1], payload["inputs"] * 2,
        [dict(item, maximum=item["maximum"] + 1) for item in payload["inputs"]],
        [dict(item, size=0) for item in payload["inputs"]],
        [dict(item, size=item["maximum"] + 1) for item in payload["inputs"]],
        [dict(item, size=True) for item in payload["inputs"]],
        [dict(item, original_path="/same") for item in payload["inputs"]]):
        with pytest.raises(ValueError):
            wire.validate_registered_input_manifest(manifest, input_contract=wire.EE_INPUT_CONTRACT)


@pytest.mark.parametrize("field,value", [
    ("unit_id", "ED-H1-SNAPSHOT"), ("specification_path", bridge.ED_SPECIFICATION_PATH),
    ("input_contract", wire.EC_INPUT_CONTRACT), ("local_coordinator", False),
    ("resource_profile", wire.CORE600.id), ("resource_profile", wire.ED_H1_600.id),
    ("resource_profile", wire.ED_H1_600_AS520.id), ("resource_profile", None),
    ("recovery_contract", bridge.ED_H1_RECOVERY_CONTRACT), ("local_execution_root", None),
    ("ssh_argv", ("ssh",)), ("remote_prefix", ("python3",))])
def test_ee_plan_cannot_borrow_remote_or_resource_exceptions(tmp_path, field, value):
    with pytest.raises(ValueError):
        bridge.validate_plan(replace(plan_at(tmp_path), **{field: value}))


@pytest.mark.parametrize("field", ["canonical_journal", "global_marker", "actual_cache", "actual_diagnostics",
    "actual_output", "invented_cache_parent", "invented_output_parent", "invented_diagnostics_parent",
    "remote_worktree", "research_locks"])
def test_local_paths_and_worktree_cannot_escape(tmp_path, field):
    plan = plan_at(tmp_path)
    changed = (tmp_path / "old-ec.lock", plan.research_locks[1]) if field == "research_locks" else tmp_path / "old-ec"
    with pytest.raises(ValueError):
        bridge.validate_plan(replace(plan, **{field: changed}))
    with pytest.raises(ValueError, match="remote_command"):
        bridge.remote_command(config_at(plan), plan)


@pytest.mark.parametrize("field,value", [("local_coordinator", False), ("local_coordinator", 1), ("execution_host", bridge.HOST),
    ("local_execution_root", "/invented-other-root"), ("local_pipe_test", True), ("test_ssh_sample", {}),
    ("remote_identity", {"execution_host": bridge.HOST}), ("unit_id", "EC-P04")])
def test_config_mode_and_host_binding(tmp_path, field, value):
    plan = plan_at(tmp_path)
    config = config_at(plan)
    bridge.validate_config(config, plan)
    with pytest.raises(ValueError):
        bridge.validate_config({**config, field: value}, plan)


def test_actual_gate_and_started_precede_any_input_stat(tmp_path, monkeypatch):
    plan = plan_at(tmp_path)
    config = config_at(plan)
    config.update(invented_only=False, cache=str(plan.actual_cache), output=str(plan.actual_output),
                  diagnostics=str(plan.actual_diagnostics), journal=str(plan.canonical_journal))
    with pytest.raises(ValueError, match="not ready"):
        bridge.validate_config(config, plan)
    accesses = []
    monkeypatch.setattr(wire, "regular", lambda path: accesses.append(path) or type("Stat", (), {"st_size": 1})())
    phases = wire.WorkerPhases({"execution_host": bridge.LOCAL_HOST})
    with pytest.raises(ValueError, match="before durable"):
        bridge.actual_input_metadata(plan, phases)
    assert accesses == []
    phases.accept_started({"kind": "STARTED", "identity": {"execution_host": bridge.LOCAL_HOST},
                           "run_id": "invented", "record_sha256": "a" * 64})
    assert bridge.actual_input_metadata(plan, phases) == envelope_for(plan)
    assert accesses == [Path(pin.original_path) for pin in plan.input_pins]


DRIVER = '''from pathlib import Path, PosixPath
import base64, json, sys, os
ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'python'))
from research import intraday_campaign_wsl_bridge as b
from research import intraday_campaign_wsl_transport as w
from research.intraday_campaign_wsl_bridge import UnitPlan, InputPin
PLAN=__PLAN__
config=json.loads(base64.b64decode(sys.argv[2],validate=True))
def forbidden(*args,**kwargs): raise AssertionError('all-local reached SSH or remote_command')
b.remote_command=forbidden
b.sample_child=forbidden
def audit(event,args):
    if event=='subprocess.Popen' and Path(args[0]).name=='ssh': forbidden()
    if event.startswith('socket.') and event not in ('socket.gethostname',):
        raise AssertionError('all-local socket/network audit event '+event)
sys.addaudithook(audit)
def generate(directory,sessions,checkpoint):
    records=[json.loads(line) for line in Path(config['journal']).read_bytes().splitlines()]
    w.require(len(records)==1 and records[0]['status']=='started','input generation before STARTED')
    manifest=[]
    for name,maximum in w.EE_INPUT_BOUNDS:
        raw=('invented '+name+'\\n').encode()
        w.exclusive_bytes(directory/name,raw)
        manifest.append(b.InputFile(name,str(directory/name),w.digest(raw),maximum,len(raw)))
    return b.InputEnvelope(tuple(manifest),'b'*64,PLAN.full_geometry,w.EE_INPUT_CONTRACT)
def compute(directory,envelope,checkpoint):
    if config.get('fault')=='worker_failure': raise RuntimeError('invented computation failure')
    total=sum(len(b.bounded_read(directory/'received'/item.name,item.maximum)) for item in envelope.manifest)
    independent=sum((directory/'received'/name).stat().st_size for name,maximum in w.EE_INPUT_BOUNDS)
    w.require(total==independent,'invented byte reconstruction mismatch')
    raw=w.encode({'invented_bytes':total})
    w.exclusive_bytes(directory/'result.json',raw)
    return b.OutputReceipt(w.digest(raw),len(raw),envelope.geometry,w.encode({'verified':True,'invented_only':True}),
                           w.encode({'invented':1}))
class FaultStream:
    def write(self,raw):
        data=bytes(raw)
        if data.startswith(w.MAGIC):
            header=json.loads(data[len(w.MAGIC)+4:])
            if config.get('fault')=='ack_loss' and sys.argv[1]=='worker' and header['kind']=='ACK_ACCEPTED':
                raise BrokenPipeError('invented acceptance loss')
            if config.get('fault')=='truncated' and sys.argv[1]=='worker' and header['kind']=='PROFILE_INPUTS':
                sys.stdout.buffer.write(w.MAGIC[:2]); sys.stdout.buffer.flush(); os._exit(8)
            if config.get('fault')=='trailing_frame' and sys.argv[1]=='worker' and header['kind']=='ACK_ACCEPTED':
                written=sys.stdout.buffer.write(raw)
                sys.stdout.buffer.write(w.MAGIC[:2])
                return written
        return sys.stdout.buffer.write(raw)
    def flush(self): return sys.stdout.buffer.flush()
result=b.run_role(sys.argv[1],config,b.UnitHooks(lambda _:PLAN,generate,compute),root=ROOT,outgoing=FaultStream())
raise SystemExit(7 if config.get('fault')=='child_nonzero' and sys.argv[1]=='worker' else result)
'''


def invented_repository(tmp_path):
    plan = plan_at(tmp_path)
    root = plan.local_worktree
    root.mkdir()
    for name in plan.source_names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("python/research/"):
            path.write_bytes((ROOT / name).read_bytes())
    (root / plan.specification_path).write_bytes(wire.encode({"id": "invented-only"}))
    (root / plan.private_entrypoint).write_text(DRIVER.replace("__PLAN__", repr(plan)))
    for parent in (plan.invented_cache_parent, plan.invented_output_parent, plan.invented_diagnostics_parent,
                   plan.canonical_journal.parent):
        parent.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.STDOUT, text=True).strip()
    git("init", "-q", "-b", "main")
    git("config", "user.name", "Invented")
    git("config", "user.email", "invented@example.invalid")
    git("commit", "--allow-empty", "-qm", "invented base")
    git("checkout", "-qb", "reviewed")
    git("add", ".")
    git("commit", "-qm", "invented fixed source")
    reviewed = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    git("merge", "--no-ff", "-qm", "invented reviewed merge", "reviewed")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    config = config_at(plan)
    config.update(merge_commit=git("rev-parse", "HEAD"), tree=git("rev-parse", "HEAD^{tree}"), reviewed_commit=reviewed)
    config["source_hashes"] = {name: wire.file_digest(root / name, 16 * wire.MIB, lambda _: None)[0]
                               for name in plan.source_names}
    identity = {"source_manifest_sha256": wire.digest(wire.encode(config["source_hashes"])),
        "runtime_sha256": bridge.runtime_sha(), "machine_sha256": bridge.machine_sha(),
        "merge_commit": config["merge_commit"], "tree": config["tree"],
        "execution_host": bridge.LOCAL_HOST, "resource_profile": plan.resource_profile}
    config.update(local_identity=identity, remote_identity=identity)
    return plan, config


def run_invented(plan, config):
    completed = subprocess.run(bridge.process_command("controller", config, plan, plan.local_worktree),
                               cwd=plan.local_worktree, capture_output=True, timeout=50)
    detail = completed.stderr.decode(errors="replace")
    diagnostic = Path(config["diagnostics"])
    if diagnostic.exists():
        detail += "\n" + "\n".join(path.read_text() for path in diagnostic.glob("*-stderr.txt"))
    return completed, detail


@pytest.mark.skipif(not hasattr(os, "wait4"), reason="native WSL/Linux process lifecycle")
def test_real_three_role_local_lifecycle_qualifies_only_same_mode(tmp_path):
    plan, config = invented_repository(tmp_path)
    completed, detail = run_invented(plan, config)
    assert completed.returncode == 0, detail
    output, diagnostic = Path(config["output"]), Path(config["diagnostics"])
    publication = json.loads((output / "publication.json").read_bytes())
    aggregate = publication["aggregate_budget"]
    samples = aggregate["samples"]
    assert set(samples) == {"worker", "coordinator", "controller"}
    assert len({sample["pid"] for sample in samples.values()}) == 3
    assert aggregate == wire.aggregate_local_roles(samples, profile=wire.ORDINARY300)
    assert aggregate["unused_ssh_reserve"] == {"cpu_seconds": 20, "address_space_bytes": 32 * wire.MIB}
    assert aggregate["hard_cpu_sum_seconds"] == 300
    assert aggregate["hard_address_space_sum_bytes"] == 512 * wire.MIB
    assert aggregate["cpu_seconds_counted_once"] == sum(sample["cpu_self_seconds"] + sample["helpers"]["cpu_seconds"]
                                                       for sample in samples.values())
    for role, cpu in (("worker", 180), ("coordinator", 60), ("controller", 40)):
        assert samples[role]["limits"]["cpu_seconds"] == cpu
    receipt = json.loads((diagnostic / "process-receipt.json").read_bytes())
    assert set(receipt["children"]) == {"worker", "coordinator"}
    assert {role: child["pid"] for role, child in receipt["children"].items()} == {
        role: samples[role]["pid"] for role in ("worker", "coordinator")}
    assert receipt["controller"]["pid"] == samples["controller"]["pid"]
    assert receipt["canonical_truth"] == "local WSL journal only"
    assert publication["execution_host"] == bridge.LOCAL_HOST
    assert publication["full_bridge"] is True and publication["local_pipe_test"] is False
    assert not (output / "unresolved.json").exists()
    assert not plan.canonical_journal.exists()
    assert (Path(config["cache"]) / "completed-ack.json").exists()
    qualification = {**config, "qualification": {"output": str(output),
        "publication_sha256": wire.digest((output / "publication.json").read_bytes())}}
    bridge.validate_qualification(qualification, plan)
    receipt_path = diagnostic / "process-receipt.json"
    original_receipt = receipt_path.read_bytes()
    for field in ("pid", "returncode"):
        changed_receipt = json.loads(original_receipt)
        changed_receipt["children"]["worker"][field] += 1
        receipt_path.write_bytes(wire.encode(changed_receipt))
        with pytest.raises(ValueError, match="child PID/exit/ACK"):
            bridge.validate_qualification(qualification, plan)
    receipt_path.write_bytes(original_receipt)
    failure_path = diagnostic / "process-failure.json"
    failure_path.write_bytes(wire.encode({"status": "unknown", "invented": True}))
    with pytest.raises(ValueError, match="controller reported failure"):
        bridge.validate_qualification(qualification, plan)
    failure_path.unlink()
    for key, value in (("local_coordinator", False), ("execution_host", bridge.HOST),
                       ("local_execution_root", "/other"), ("local_pipe_test", True)):
        changed = {**publication, key: value}
        raw = wire.encode(changed)
        (output / "publication.json").write_bytes(raw)
        qualification["qualification"]["publication_sha256"] = wire.digest(raw)
        with pytest.raises(ValueError):
            bridge.validate_qualification(qualification, plan)
    with pytest.raises(ValueError, match="all four"):
        wire.aggregate_roles(samples, profile=wire.ORDINARY300)
    for changed in ({**samples, "ssh_client": samples["worker"]},
                    {**samples, "controller": samples["worker"]},
                    {**samples, "controller": {**samples["controller"], "pid": samples["worker"]["pid"]}}):
        with pytest.raises(ValueError):
            wire.aggregate_local_roles(changed, profile=wire.ORDINARY300)


def test_local_source_probe_loads_no_science_and_cannot_qualify(tmp_path):
    plan, config = invented_repository(tmp_path)
    completed = subprocess.run(bridge.process_command("source-probe", config, plan, plan.local_worktree),
                               cwd=plan.local_worktree, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    receipt = json.loads(completed.stdout)
    assert receipt["execution_host"] == receipt["identity"]["execution_host"] == bridge.LOCAL_HOST
    assert receipt["local_execution_root"] == str(plan.local_execution_root)
    assert receipt["qualifies_actual"] is False
    assert receipt["market_input_accesses"] == receipt["journal_accesses"] == receipt["marker_accesses"] == 0
    assert receipt["role_sample"]["parent_limits"] == {"cpu_seconds": 50, "address_space_bytes": 32 * wire.MIB}
    assert not Path(config["cache"]).exists() and not Path(config["output"]).exists()


@pytest.mark.parametrize("fault", ["truncated", "worker_failure", "ack_loss", "child_nonzero", "trailing_frame"])
def test_local_failures_preserve_protocol_evidence_and_never_replay(tmp_path, fault):
    plan, config = invented_repository(tmp_path)
    config["fault"] = fault
    completed, detail = run_invented(plan, config)
    assert completed.returncode != 0, detail
    output, diagnostics = Path(config["output"]), Path(config["diagnostics"])
    original = Path(config["journal"]).read_bytes()
    records = [json.loads(line) for line in original.splitlines()]
    assert records[0]["status"] == "started"
    assert records[0]["parameters"]["execution_host"] == bridge.LOCAL_HOST
    assert (diagnostics / "process-failure.json").exists()
    assert not (diagnostics / "process-receipt.json").exists()
    if fault not in ("child_nonzero", "trailing_frame"):
        assert (output / "unresolved.json").exists()
    if fault == "ack_loss":
        assert records[-1]["status"] == "completed"
    if (output / "publication.json").exists():
        qualification = {**config, "qualification": {"output": str(output),
            "publication_sha256": wire.digest((output / "publication.json").read_bytes())}}
        with pytest.raises((ValueError, FileNotFoundError)):
            bridge.validate_qualification(qualification, plan)
    again, _ = run_invented(plan, config)
    assert again.returncode != 0
    assert Path(config["journal"]).read_bytes() == original


def test_local_orphan_marker_blocks_under_existing_locks(tmp_path):
    plan = plan_at(tmp_path)
    marker = plan.local_execution_root / "orphan.json"
    locks = [plan.local_execution_root / "one.lock", plan.local_execution_root / "two.lock"]
    with wire.research_locks(locks, marker, "invented-failed"):
        original = marker.read_bytes()
    with pytest.raises(FileExistsError):
        with wire.research_locks(locks, marker, "invented-another"):
            pytest.fail("orphan replay admitted")
    assert marker.read_bytes() == original


def test_local_network_guard_rejects_socket_creation_without_a_socket(monkeypatch):
    import socket
    for name in ("socket", "socketpair", "create_connection"):
        monkeypatch.setattr(socket, name, getattr(socket, name))
    for method in ("connect", "connect_ex", "sendto", "sendmsg"):
        monkeypatch.setattr(socket.socket, method, getattr(socket.socket, method))
    bridge.restrict_local_network()
    for name in ("socket", "socketpair", "create_connection"):
        with pytest.raises(RuntimeError, match="stdio IPC only"):
            getattr(socket, name)()
