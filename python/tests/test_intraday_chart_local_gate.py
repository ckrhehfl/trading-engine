"""Invented EF snapshot transport with real local PIDs, locks and hard limits."""
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.research_budget import EF_WORKER_SCOPE, ResearchBudget


ROOT = Path(__file__).resolve().parents[2]
PROFILE = wire.EF_LOCAL_SEQUENCE
DRIVER_PATH = "driver.py"
SOURCES = tuple(sorted((bridge.EF_SPECIFICATION_PATH, DRIVER_PATH,
    "python/research/__init__.py", "python/research/experiment_log.py", "python/research/research_budget.py",
    "python/research/intraday_campaign_wsl_bridge.py", "python/research/intraday_campaign_wsl_transport.py")))


def test_direct_actual_roles_cannot_start_without_independent_original_audit(plan, monkeypatch):
    # The earlier full lifecycle check succeeds. The independently written
    # audit is a second pre-STARTED gate even through the direct role API.
    monkeypatch.setattr(bridge, "_set_deadline", lambda *_: None)
    monkeypatch.setattr(wire, "apply_role_limits", lambda *_, **__: None)
    monkeypatch.setattr(bridge, "verify_sources", lambda *_, **__: None)
    monkeypatch.setattr(bridge, "validate_qualification", lambda *_: None)
    monkeypatch.setattr(bridge, "restrict_worker_network", lambda: None)
    monkeypatch.setattr(bridge, "worker_budget", lambda _: None)
    monkeypatch.setattr(bridge, "actual_input_metadata", lambda *_: pytest.fail("original input access"))
    kernel = os.uname()
    monkeypatch.setattr(bridge.os, "uname", lambda: os.uname_result(
        (kernel.sysname, kernel.nodename, "microsoft-invented-wsl-fixture", kernel.version, kernel.machine)))
    config = {"invented_only": False, "cache": str(plan.actual_cache), "output": str(plan.actual_output),
              "journal": str(plan.canonical_journal), "source_hashes": {},
              "qualification": {"output": str(plan.invented_output_parent / (plan.unit_id + "-invented-qualification-v1")),
                                "publication_sha256": "1" * 64, "audit_sha256": "2" * 64}}
    outgoing = BytesIO()
    with pytest.raises(ValueError, match="original receipt audit is missing"):
        bridge.coordinator(config, plan, plan.local_worktree, BytesIO(), outgoing)
    with pytest.raises(ValueError, match="original receipt audit is missing"):
        bridge.worker(config, plan, None, plan.local_worktree, BytesIO(), outgoing)
    assert outgoing.getvalue() == b""
    assert not plan.actual_output.exists() and not plan.actual_cache.exists() and not plan.canonical_journal.exists()


def test_original_audit_binds_the_same_receipt_and_reviewed_auditor(plan):
    out = plan.invented_output_parent / (plan.unit_id + "-invented-qualification-v1")
    out.mkdir()
    audit_path = plan.local_execution_root / "launches/qualification-v1/original-receipt-audit.json"
    audit_path.parent.mkdir(parents=True)
    seal = {"fingerprint": "a" * 64, "features_sha256": "b" * 64, "labels_sha256": "c" * 64}
    hashes = {bridge.EF_RECEIPT_AUDITOR: "d" * 64}
    receipt = wire.encode({"seal": seal})
    publication = wire.encode({"output_sha256": wire.digest(receipt), "source_hashes": hashes, "invented_only": True})
    (out / plan.output_filename).write_bytes(receipt)
    (out / "publication.json").write_bytes(publication)
    audit = {"status": "PASS", "invented_only": True, "science_reexecuted": False,
             "auditor_source_sha256": hashes[bridge.EF_RECEIPT_AUDITOR], "publication_sha256": wire.digest(publication),
             "source_manifest_sha256": wire.digest(wire.encode(hashes)), "receipt_sha256": wire.digest(receipt), "seal": seal}
    raw = wire.encode(audit)
    audit_path.write_bytes(raw)
    config = {"source_hashes": hashes, "qualification": {"output": str(out),
              "publication_sha256": wire.digest(publication), "audit_sha256": wire.digest(raw)}}
    bridge.validate_ef_receipt_audit(config, plan)
    changed = wire.encode({**audit, "seal": {**seal, "features_sha256": "e" * 64}})
    audit_path.write_bytes(changed)
    with pytest.raises(ValueError, match="audit hash differs"):
        bridge.validate_ef_receipt_audit(config, plan)
    config["qualification"]["audit_sha256"] = wire.digest(changed)
    with pytest.raises(ValueError, match="audit binding differs"):
        bridge.validate_ef_receipt_audit(config, plan)


@pytest.fixture
def plan(tmp_path):
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir()
    private.mkdir(mode=0o700)
    cache, output, diagnostics, canonical = (private / name for name in ("cache", "out", "diag", "journal"))
    for path in (cache, output, diagnostics, canonical):
        path.mkdir()
    return bridge.UnitPlan(unit_id=bridge.EF_UNIT_ID, specification_path=bridge.EF_SPECIFICATION_PATH,
        source_names=SOURCES, source_roster_sha256=wire.digest(wire.encode(list(SOURCES))),
        input_pins=tuple(bridge.InputPin(name, str(private / "never-open" / name), "a" * 64, maximum)
                         for name, maximum in wire.EF_INPUT_BOUNDS), decoded_stream_sha256="b" * 64,
        window=wire.encode({"symbol": "INVENTED", "interval": "1m", "start": "2020-01-01", "end": "2020-01-02"}),
        full_geometry=wire.encode({"sessions": 2, "targets": 2}), candidate_bounds=wire.encode({"invented": 2}),
        private_entrypoint=DRIVER_PATH, local_worktree=root, remote_worktree=root,
        actual_cache=cache / "actual", actual_output=output / "actual", actual_diagnostics=diagnostics / "actual",
        canonical_journal=canonical / "experiments.jsonl", global_marker=canonical / "ef-unresolved.json",
        research_locks=(canonical / "research.lock", output / "campaign.lock"), invented_cache_parent=cache,
        invented_output_parent=output, invented_diagnostics_parent=diagnostics, output_filename="receipt.json",
        output_max_bytes=4096, ssh_argv=(), remote_prefix=(), input_contract=wire.EF_INPUT_CONTRACT,
        resource_profile=PROFILE.id, local_coordinator=True, local_execution_root=private)


def test_closed_ef_snapshot_pair_and_exact_caps(plan):
    bridge.validate_plan(plan)
    assert bridge.profile_for_plan(plan) is PROFILE
    for change in ({"unit_id": bridge.EE_UNIT_ID}, {"specification_path": bridge.ED_SPECIFICATION_PATH},
                   {"input_contract": wire.EE_INPUT_CONTRACT}, {"input_contract": wire.ED_INPUTS_CONTRACT},
                   {"input_contract": wire.EC_INPUT_CONTRACT}, {"resource_profile": wire.ORDINARY300.id},
                   {"local_coordinator": False}, {"recovery_contract": bridge.ED_H1_RECOVERY_CONTRACT}):
        with pytest.raises(ValueError, match="EF local requires"):
            bridge.profile_for_plan(replace(plan, **change))
    with pytest.raises(ValueError, match="forbids SSH"):
        bridge.validate_plan(replace(plan, ssh_argv=("/usr/bin/ssh",)))
    with pytest.raises(ValueError, match="must be native"):
        bridge.validate_plan(replace(plan, input_pins=(replace(plan.input_pins[0], original_path="/mnt/c/invented"),
                                                      *plan.input_pins[1:])))
    with pytest.raises(ValueError, match="canonical EF provenance"):
        bridge.validate_plan(replace(plan, input_pins=(replace(plan.input_pins[0], original_path="//mnt/c/invented"),
                                                      *plan.input_pins[1:])))
    envelope = bridge.InputEnvelope(tuple(bridge.InputFile(**pin.metadata(1)) for pin in plan.input_pins),
        plan.decoded_stream_sha256, plan.full_geometry, plan.input_contract)
    assert bridge.InputEnvelope.from_payload(envelope.payload(), input_contract=wire.EF_INPUT_CONTRACT) == envelope
    with pytest.raises(ValueError, match="envelope contract"):
        bridge.InputEnvelope.from_payload(envelope.payload(), input_contract=wire.EE_INPUT_CONTRACT)
    assert PROFILE.cpu_seconds == 1800 and PROFILE.wall_seconds == 3600
    assert PROFILE.address_space_bytes == 4096 * wire.MIB
    assert tuple(PROFILE.role_limits()) == ("worker", "coordinator", "controller")
    for role, cpu, space in (("worker", 1500, 3072), ("coordinator", 180, 512), ("controller", 120, 512)):
        assert PROFILE.role_limits()[role] == {"cpu_seconds": cpu, "address_space_bytes": space * wire.MIB}
    with pytest.raises(ValueError, match="exact registered limits"):
        ResearchBudget(operator_cpu_scope=EF_WORKER_SCOPE)
    with pytest.raises(ValueError, match="address_space_bytes"):
        ResearchBudget(address_space_bytes=3072 * wire.MIB, operator_cpu_scope=bridge.ED_H1_CPU_SCOPE)


def test_ef_comparison_uses_only_the_native_sealed_input_contract(plan):
    name, maximum = wire.EF_SEALED_BOUNDS[0]
    sources = tuple(sorted((*SOURCES, bridge.EF_COMPARISON_SPEC)))
    model = replace(plan, unit_id=next(iter(bridge.EF_FIT_UNITS)), specification_path=bridge.EF_COMPARISON_SPEC,
                    source_names=sources, source_roster_sha256=wire.digest(wire.encode(list(sources))),
                    input_contract=wire.EF_SEALED_CONTRACT, input_pins=(bridge.InputPin(
                        name, str(plan.local_execution_root / "invented-handoff"), "a" * 64, maximum),))
    bridge.validate_plan(model)
    assert bridge.profile_for_plan(model) is PROFILE
    with pytest.raises(ValueError, match="EF local requires"):
        bridge.validate_plan(replace(model, input_contract=wire.EF_INPUT_CONTRACT))
    with pytest.raises(ValueError, match="EF input roster"):
        bridge.validate_plan(replace(model, input_pins=plan.input_pins))
    with pytest.raises(ValueError, match="canonical EF provenance"):
        bridge.validate_plan(replace(model, input_pins=(replace(model.input_pins[0], original_path="//a"),)))


def test_ef_lock_is_retained_by_an_inherited_descriptor(plan, monkeypatch):
    with bridge._ef_unit_lock("controller", plan) as fd:
        duplicate = os.dup(fd)
    monkeypatch.setenv(bridge.EF_LOCK_ENV, str(duplicate))
    with bridge._ef_unit_lock("worker", plan):
        with pytest.raises(BlockingIOError):
            with bridge._ef_unit_lock("controller", plan):
                pytest.fail("parallel controller admitted")
    with bridge._ef_unit_lock("controller", plan):
        pass


DRIVER = '''from pathlib import Path, PosixPath
import base64, errno, json, os, resource, socket, sys, threading
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "python"))
from research import intraday_campaign_wsl_bridge as b
from research import intraday_campaign_wsl_transport as w
from research.intraday_campaign_wsl_bridge import UnitPlan, InputPin
PLAN = __PLAN__
config = json.loads(base64.b64decode(sys.argv[2], validate=True))
if config["invented_only"] is not True:
    raise AssertionError("invented test cannot start actual roles")
# CI runs Linux. Only the invented fixture can emulate the WSL name there.
if "microsoft" not in os.uname().release.lower():
    native = os.uname()
    os.uname = lambda: os.uname_result((native.sysname, native.nodename, "microsoft-invented-wsl-fixture",
                                       native.version, native.machine))
def audit(event, args):
    if event == "subprocess.Popen" and Path(args[0]).name == "ssh":
        raise AssertionError("unexpected SSH execution")
    if event == "open" and "never-open" in str(args[0]):
        raise AssertionError("unexpected original input access")
sys.addaudithook(audit)
def generate(directory, sessions, checkpoint):
    started = [json.loads(line) for line in Path(config["journal"]).read_bytes().splitlines()]
    assert len(started) == 1 and started[0]["status"] == "started"
    assert resource.getrlimit(resource.RLIMIT_NPROC) == (0, 0)
    try:
        pid = os.fork()
    except OSError as error:
        assert error.errno == errno.EAGAIN
    else:
        if pid == 0:
            os._exit(19)
        os.waitpid(pid, 0)
        raise AssertionError("worker fork bypassed hard cap")
    try:
        thread = threading.Thread(target=lambda: None)
        thread.start()
    except RuntimeError:
        pass
    else:
        thread.join()
        raise AssertionError("worker thread bypassed hard cap")
    try:
        socket.socket()
    except RuntimeError as error:
        assert "stdio IPC only" in str(error)
    else:
        raise AssertionError("worker created a socket")
    manifest = []
    for name, maximum in w.EF_INPUT_BOUNDS:
        raw = name.encode()
        w.exclusive_bytes(directory / name, raw)
        manifest.append(b.InputFile(name, str(directory / name), w.digest(raw), maximum, len(raw)))
    return b.InputEnvelope(tuple(manifest), "b" * 64, PLAN.full_geometry, w.EF_INPUT_CONTRACT)
def compute(directory, envelope, checkpoint):
    received = directory / "received"
    computed = b"".join((received / item.name).read_bytes() for item in envelope.manifest)
    independent = tuple(item.read_text() for item in sorted(received.iterdir()))
    assert computed == b"CBCCCX" and independent == ("CB", "CC", "CX")
    raw = w.encode({"bytes": len(computed)})
    w.exclusive_bytes(directory / PLAN.output_filename, raw)
    return b.OutputReceipt(w.digest(raw), len(raw), envelope.geometry, w.encode({"verified": True}),
                           w.encode({"invented": 2}))
raise SystemExit(b.run_role(sys.argv[1], config, b.UnitHooks(lambda _: PLAN, generate, compute), root=ROOT))
'''


@pytest.mark.skipif(sys.platform != "linux" or os.getuid() == 0, reason="unprivileged WSL/Linux lifecycle")
def test_full_invented_ef_lifecycle_and_original_worker_receipt(plan):
    root = plan.local_worktree
    for name in SOURCES:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("python/"):
            destination.write_bytes((ROOT / name).read_bytes())
    (root / plan.specification_path).write_bytes(wire.encode({"id": "invented-only"}))
    (root / DRIVER_PATH).write_text(DRIVER.replace("__PLAN__", repr(plan)))

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Invented")
    git("config", "user.email", "invented@example.invalid")
    git("commit", "--allow-empty", "-qm", "invented base")
    git("checkout", "-qb", "reviewed")
    git("add", ".")
    git("commit", "-qm", "invented exact source")
    reviewed = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    git("merge", "--no-ff", "-qm", "invented reviewed merge", "reviewed")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    attempt = plan.unit_id + "-invented-tiny"
    cache, output, diagnostics = (parent / attempt for parent in
        (plan.invented_cache_parent, plan.invented_output_parent, plan.invented_diagnostics_parent))
    source_hashes = {name: wire.file_digest(root / name, 16 * wire.MIB, lambda _: None)[0] for name in SOURCES}
    kernel = list(os.uname())
    if "microsoft" not in kernel[2].lower():
        kernel[2] = "microsoft-invented-wsl-fixture"
    machine = wire.digest(wire.encode({"hostname": bridge.gethostname(), "kernel": kernel,
        "python": sys.version, "executable": str(Path(sys.executable).resolve())}))
    identity = {"source_manifest_sha256": wire.digest(wire.encode(source_hashes)),
        "runtime_sha256": bridge.runtime_sha(), "machine_sha256": machine, "merge_commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"), "execution_host": bridge.LOCAL_HOST, "resource_profile": PROFILE.id}
    config = {"unit_id": plan.unit_id, "invented_only": True, "resource_profile": PROFILE.id,
        "automatic_retries": 0, "wall_seconds": PROFILE.wall_seconds, "sessions": 2, "attempt": "tiny",
        "local_coordinator": True, "local_execution_root": str(plan.local_execution_root),
        "execution_host": bridge.LOCAL_HOST, "local_identity": identity, "remote_identity": identity,
        "source_hashes": source_hashes, "merge_commit": identity["merge_commit"], "tree": identity["tree"],
        "reviewed_commit": reviewed, "deadline_epoch": time.time() + 45,
        "cache": str(cache), "output": str(output), "journal": str(output / "invented-experiments.jsonl"),
        "diagnostics": str(diagnostics)}
    for change in ({"unit_id": bridge.EE_UNIT_ID}, {"local_coordinator": False}, {"local_pipe_test": True},
                   {"resource_profile": wire.ORDINARY300.id}):
        with pytest.raises(ValueError, match="EF snapshot local bootstrap"):
            bridge._bootstrap_profile({**config, **change})
    rejected = {**config, "invented_only": False, "attempt": "actual2", "cache": str(plan.actual_cache),
        "output": str(plan.actual_output), "journal": str(plan.canonical_journal),
        "diagnostics": str(plan.actual_diagnostics)}
    with pytest.raises(ValueError, match="fixed EF first actual attempt"):
        bridge.validate_config(rejected, replace(plan, actual_runner_ready=True))

    process = subprocess.run(bridge.process_command("controller", config, plan, root), cwd=root,
                             capture_output=True, timeout=50)
    assert process.returncode == 0, (process.stderr.decode() + "\n" +
        "\n".join(path.read_text() for path in diagnostics.glob("*-stderr.txt")))
    publication = (output / "publication.json").read_bytes()
    aggregate = json.loads(publication)["aggregate_budget"]
    assert aggregate == wire.aggregate_local_roles(aggregate["samples"], profile=PROFILE)
    assert "unused_ssh_reserve" not in aggregate
    for role in ("controller", "coordinator"):
        readbacks = aggregate["samples"][role]["helpers"]["readbacks"]
        assert readbacks and all(item["rlimit_nproc"] == [0, 0] for item in readbacks)
        assert sum(item["cpu_self_seconds"] for item in readbacks) == pytest.approx(
            aggregate["samples"][role]["helpers"]["cpu_seconds"], rel=0, abs=1e-9)
    assert aggregate["samples"]["worker"]["readback"]["rlimit_nproc"] == [0, 0]
    assert not (output / "unresolved.json").exists() and not plan.canonical_journal.exists()
    qualified = {**config, "qualification": {"output": str(output), "publication_sha256": wire.digest(publication)}}
    bridge.validate_qualification(qualified, plan)
    (cache / "completed-ack.json").write_bytes(wire.encode({"tampered": True}))
    with pytest.raises(ValueError, match="original worker ACK/source"):
        bridge.validate_qualification(qualified, plan)
