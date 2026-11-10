"""EK release, resource and lifecycle admission using invented metadata only."""
from contextlib import contextmanager
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_search_run as lifecycle
from research import intraday_context_cost_census_run as census
from research import intraday_ed_sources as sources
from research import intraday_recovery_run as run


MERGE, REVIEWED, TREE = "1" * 40, "2" * 40, "3" * 40
SOURCE_ROOT = Path(__file__).absolute().parents[2]


def test_release_mismatch_stops_before_source_or_original_input_reads(monkeypatch, tmp_path):
    calls = []
    def git(*args):
        calls.append(args)
        return f'{"9" * 40}\n{TREE}\n{"4" * 40} {REVIEWED}\n'.encode()
    monkeypatch.setattr(run, "_git", git)
    monkeypatch.setattr(run, "REPO", tmp_path / "uncreated-no-source-files")
    with pytest.raises(ValueError, match="normal reviewed merge"):
        run.freeze(MERGE, REVIEWED, TREE)
    assert calls == [("show", "-s", "--format=%H%n%T%n%P", "HEAD")]
    assert not list(tmp_path.iterdir())


@pytest.fixture
def reviewed_source(monkeypatch, tmp_path):
    # Read public source registrations only. None of their declared input paths
    # is opened; tiny dummy modules test the closed loader's byte admission.
    raw = {run.SPEC: (SOURCE_ROOT / run.SPEC).read_bytes(),
           run.ORIGINAL: (SOURCE_ROOT / run.ORIGINAL).read_bytes(),
           "python/research/__init__.py": b"",
           "python/research/intraday_recovery_run.py": b'"""Invented source closure."""\n'}
    for name, value in raw.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def git(*args):
        if args[0] == "show":
            return f'{MERGE}\n{TREE}\n{"4" * 40} {REVIEWED}\n'.encode()
        if args[0] == "rev-parse":
            return (TREE + "\n").encode()
        if args[0] in ("merge-base", "status"):
            return b""
        assert args[0] == "ls-tree"
        return b"".join(("100644 blob " + sha1(f"blob {len(value)}\0".encode() + value,
                        usedforsecurity=False).hexdigest() + "\t" + name).encode() + b"\0"
                        for name, value in raw.items())

    monkeypatch.setattr(run, "REPO", tmp_path)
    monkeypatch.setattr(run, "ROOTS", ())
    monkeypatch.setattr(run, "_git", git)
    monkeypatch.setattr(run, "sys", SimpleNamespace(modules={}, meta_path=[]))
    return tmp_path, raw


def test_reviewed_source_closure_binds_exact_registration_and_original_hash(reviewed_source):
    _, raw = reviewed_source
    spec, _, identity = run.freeze(MERGE, REVIEWED, TREE)
    assert identity["registration_sha256"] == sha256(raw[run.SPEC]).hexdigest()
    assert identity["parents_sha256"] == spec["source_registration_sha256"] == sha256(raw[run.ORIGINAL]).hexdigest()
    assert identity["sources"] == {name: sha256(value).hexdigest() for name, value in raw.items()
                                    if name.startswith("python/")}
    assert len(run.sys.meta_path) == 1


def test_wrong_original_registration_hash_is_not_source_admission(reviewed_source):
    root, raw = reviewed_source
    spec = json.loads(raw[run.SPEC])
    spec["source_registration_sha256"] = "0" * 64
    raw[run.SPEC] = wire.encode(spec)
    (root / run.SPEC).write_bytes(raw[run.SPEC])
    with pytest.raises(ValueError, match="registered scope differs"):
        run.freeze(MERGE, REVIEWED, TREE)
    assert not run.sys.meta_path


def test_unreviewed_source_bytes_cannot_be_hidden_by_clean_git_status(reviewed_source):
    root, _ = reviewed_source
    (root / run.SPEC).write_bytes(b'{"unreviewed":true}\n')
    with pytest.raises(ValueError, match="reviewed blob differs"):
        run.freeze(MERGE, REVIEWED, TREE)


def test_closed_loader_executes_admitted_bytes_and_refuses_an_extra_import():
    loader = run.ReviewedModules({"research.invented": ("python/research/invented.py", b"ANSWER = 7\n")})
    module = ModuleType("research.invented")
    loader.exec_module(module)
    assert module.ANSWER == 7 and loader.find_spec("json") is None
    with pytest.raises(ValueError, match="Unregistered EK project import"):
        loader.find_spec("research.not_reviewed")


def test_budget_enforces_declared_single_cpu_memory_nice_and_deadline(monkeypatch):
    calls, handlers = [], {}
    monkeypatch.setattr(run.os, "setpriority", lambda *args: calls.append(("nice", args)))
    monkeypatch.setattr(run.os, "sched_getaffinity", lambda _: {3, 5})
    monkeypatch.setattr(run.os, "sched_setaffinity", lambda *args: calls.append(("affinity", args)))
    monkeypatch.setattr(run.resource, "setrlimit", lambda *args: calls.append(("limit", args)))
    monkeypatch.setattr(run.signal, "signal", lambda event, handler: handlers.setdefault(event, handler))
    monkeypatch.setattr(run.signal, "setitimer", lambda *args: calls.append(("timer", args)))
    run.budget()
    assert ("nice", (run.os.PRIO_PROCESS, 0, 10)) in calls
    assert ("affinity", (0, {3})) in calls
    assert ("limit", (run.resource.RLIMIT_CPU, (1500, 1510))) in calls
    assert ("limit", (run.resource.RLIMIT_AS, (3 * 1024 * run.MIB,) * 2)) in calls
    assert ("timer", (run.signal.ITIMER_REAL, 3600)) in calls
    with pytest.raises(TimeoutError, match="deadline"):
        handlers[run.signal.SIGXCPU]()
    assert calls[-1] == ("timer", (run.signal.ITIMER_REAL, 0))


def configure_actual(monkeypatch, tmp_path, receipt):
    monkeypatch.setattr(sys, "argv", ["invented-ek", "actual", "--merge", MERGE,
                                     "--reviewed", REVIEWED, "--tree", TREE])
    monkeypatch.setattr(run, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), platform="linux"))
    real_path = run.Path
    monkeypatch.setattr(run, "Path", lambda value: SimpleNamespace(read_text=lambda: "invented microsoft kernel")
                        if value == "/proc/sys/kernel/osrelease" else real_path(value))
    monkeypatch.setattr(run, "budget", lambda: None)
    monkeypatch.setattr(run, "runtime_pin", lambda: "d" * 64)
    monkeypatch.setattr(run, "check_unresolved_campaign", lambda *args: None)
    monkeypatch.setattr(run, "comparison_qualification", lambda *args: "c" * 64)
    release = {"merge": MERGE, "reviewed": REVIEWED, "tree": TREE, "registration_sha256": "a" * 64,
               "parents_sha256": "b" * 64, "sources": {}}
    monkeypatch.setattr(run, "freeze", lambda *args: ({"window": {}}, {}, release))
    monkeypatch.setattr(wire, "prohibit_ef_tasks", lambda: None)

    @contextmanager
    def locks():
        yield
    monkeypatch.setattr(census, "_locks", locks)
    resources = {"worker_cpu_seconds": 10, "helper_cpu_seconds": 1, "wall_seconds": 20}
    receipt = {**receipt, "resource_sha256": wire.digest(wire.encode(resources))}
    completed = {"invented": "completion", "result": {"resources": resources}}
    def publication(identity):
        assert identity == {**release, "attempt": "qualification1", "invented": True, "qualification_sha256": None}
        return {}, completed
    monkeypatch.setattr(run, "publication", publication)
    monkeypatch.setattr(lifecycle, "_slot", lambda attempt, unit: tmp_path / f"{unit}-{attempt}")
    monkeypatch.setattr(lifecycle, "_doc", lambda path: resources if path.name == "audit-resources.json" else receipt)
    monkeypatch.setattr(lifecycle, "_audit_receipt", lambda done: {"verified": True} if done == completed else None)
    monkeypatch.setattr(run, "original_inputs", lambda *args: pytest.fail("original inputs reached before admission"))


def test_actual_requires_same_release_independent_invented_audit_before_read(monkeypatch, tmp_path):
    configure_actual(monkeypatch, tmp_path, {"verified": False})
    monkeypatch.setattr(lifecycle, "_retry", lambda *args: pytest.fail("retry precedes qualification"))
    with pytest.raises(ValueError, match="independent audit absent"):
        run.main()
    assert not list(tmp_path.iterdir())


def test_actual_applies_controlled_retry_gate_before_any_new_slot_or_input(monkeypatch, tmp_path):
    configure_actual(monkeypatch, tmp_path, {"verified": True})
    def reject(identity, unit):
        resources = {"worker_cpu_seconds": 10, "helper_cpu_seconds": 1, "wall_seconds": 20}
        receipt = {"verified": True, "resource_sha256": wire.digest(wire.encode(resources))}
        assert unit == run.UNIT and identity["qualification_sha256"] == wire.digest(wire.encode(receipt))
        assert identity["comparison_qualification_sha256"] == "c" * 64
        raise ValueError("invented ambiguous old publication")
    monkeypatch.setattr(lifecycle, "_retry", reject)
    with pytest.raises(ValueError, match="ambiguous old publication"):
        run.main()
    assert not list(tmp_path.iterdir())


def test_actual_snapshot_requires_comparison_qualification_before_opening_original(monkeypatch,tmp_path):
    configure_actual(monkeypatch,tmp_path,{"verified":True})
    def reject(*_):
        raise ValueError("invented comparison audit absent")
    monkeypatch.setattr(run,"comparison_qualification",reject)
    with pytest.raises(ValueError,match="comparison audit absent"):
        run.main()
    assert not list(tmp_path.iterdir())


def test_timeout_retry_retains_failure_identity_and_refuses_ambiguity(monkeypatch):
    identity = {"attempt": "actual2", "invented": False, "registration_sha256": "a" * 64}
    previous = {**identity, "attempt": "actual1"}
    span = {"start": 0, "end": 1, "run_id": "invented", "terminal_sha256": "b" * 64}
    def document(path):
        return ({"identity": previous, "error_type": "TimeoutError", "journal": span}
                if path.name == "timeout.json" else previous)
    monkeypatch.setattr(lifecycle, "_doc", document)
    monkeypatch.setattr(lifecycle.os.path, "lexists", lambda _: False)
    def proof(path, old, result, start, end, status, unit):
        assert (old, result, start, end, status, unit) == (previous, None, 0, 1, "failed", run.UNIT)
        return span
    monkeypatch.setattr(lifecycle, "_proof", proof)
    lifecycle._retry(identity, run.UNIT)
    monkeypatch.setattr(lifecycle.os.path, "lexists", lambda path: path.name == "report.json")
    with pytest.raises(ValueError, match="ambiguous publication"):
        lifecycle._retry(identity, run.UNIT)


@pytest.mark.parametrize("field,value", [("decision_offset", 61), ("cost_bps", 0), ("expiry_offset", 124),
                                        ("orders_enabled", True), ("management_end_offsets", [60, 75, 90])])
def test_registered_snapshot_science_cannot_drift_from_implemented_clocks(field, value):
    spec = json.loads((SOURCE_ROOT / run.SPEC).read_bytes())
    run.validate_spec(spec)
    spec[field] = value
    with pytest.raises(ValueError, match="science contract differs"):
        run.validate_spec(spec)


@pytest.mark.parametrize("producer,auditor", [
    ({"worker_cpu_seconds": 1000, "helper_cpu_seconds": 1, "wall_seconds": 1},
     {"worker_cpu_seconds": 501, "helper_cpu_seconds": 1, "wall_seconds": 1}),
    ({"worker_cpu_seconds": 500, "helper_cpu_seconds": 801, "wall_seconds": 1},
     {"worker_cpu_seconds": 500, "helper_cpu_seconds": 1, "wall_seconds": 1}),
    ({"worker_cpu_seconds": 500, "helper_cpu_seconds": 1, "wall_seconds": 1800},
     {"worker_cpu_seconds": 500, "helper_cpu_seconds": 1, "wall_seconds": 1801}),
    ({"worker_cpu_seconds": 1, "helper_cpu_seconds": 1, "wall_seconds": 1},
     {"worker_cpu_seconds": True, "helper_cpu_seconds": 1, "wall_seconds": 1}),
])
def test_producer_and_independent_audit_share_one_resource_envelope(producer, auditor):
    with pytest.raises(ValueError, match="combined resource envelope differs"):
        run.verify_usage(producer, auditor)


def test_audit_budget_reserves_already_used_worker_helpers_and_wall(monkeypatch):
    used = {"worker_cpu_seconds": 400, "helper_cpu_seconds": 90, "wall_seconds": 1000}
    current = {"worker_cpu_seconds": 20, "helper_cpu_seconds": 10, "wall_seconds": 25}
    calls = []
    monkeypatch.setattr(run, "usage", lambda: current)
    monkeypatch.setattr(run.resource, "setrlimit", lambda *args: calls.append(("limit", args)))
    monkeypatch.setattr(run.signal, "setitimer", lambda *args: calls.append(("timer", args)))
    run.audit_budget(used)
    assert calls == [("limit", (run.resource.RLIMIT_CPU, (1100, 1110))),
                     ("timer", (run.signal.ITIMER_REAL, 2575))]
    with pytest.raises(ValueError, match="CPU exhausted"):
        run.audit_budget({**used, "worker_cpu_seconds": 1490})


def test_original_archive_digest_rejected_before_universe_or_raw_index(monkeypatch, tmp_path):
    prior = {"actual_source_preparation": {"geometry": {}, "local_received_sources": [
        {"name": name, "path": f"/invented-not-opened/{name}", "maximum": 100, "sha256": "a" * 64}
        for name in ("CB", "CC", "CX")]}}
    calls = []
    def digest(path, maximum, checkpoint):
        calls.append((str(path), maximum))
        return "b" * 64, 1
    monkeypatch.setattr(wire, "file_digest", digest)
    monkeypatch.setattr(sources, "load_universe", lambda *args, **kwargs: pytest.fail("unauthenticated CB decoded"))
    monkeypatch.setattr(sources, "audit_index", lambda *args, **kwargs: pytest.fail("unauthenticated archive indexed"))
    with pytest.raises(ValueError, match="original input digest differs"):
        with run.original_inputs(tmp_path, prior, False, False):
            pytest.fail("unauthenticated source admitted")
    assert calls == [("/invented-not-opened/CB", 100)]
