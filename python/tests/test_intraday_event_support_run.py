"""EL admission, resource and journal tests using invented files only."""

from contextlib import contextmanager
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from research import experiment_log
from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as sources
from research import intraday_event_support as support
from research import intraday_event_support_run as run


MERGE, REVIEWED, TREE = "1" * 40, "2" * 40, "3" * 40
SOURCE_ROOT = Path(__file__).absolute().parents[2]
RESOURCES = {"worker_cpu_seconds": 10, "helper_cpu_seconds": 1, "wall_seconds": 20}


def specification():
    return json.loads((SOURCE_ROOT / run.SPEC).read_bytes())


def test_release_mismatch_precedes_source_and_original_reads(monkeypatch, tmp_path):
    calls = []
    def git(*args):
        calls.append(args)
        return f'{"9" * 40}\n{TREE}\n{"4" * 40} {REVIEWED}\n'.encode()
    monkeypatch.setattr(run, "_git", git)
    monkeypatch.setattr(run, "REPO", tmp_path / "absent-source")
    with pytest.raises(ValueError, match="normal reviewed merge"):
        run.freeze(MERGE, REVIEWED, TREE)
    assert calls == [("show", "-s", "--format=%H%n%T%n%P", "HEAD")]
    assert not list(tmp_path.iterdir())


@pytest.fixture
def reviewed_source(monkeypatch, tmp_path):
    # Source registrations are public; no declared source pathname is opened.
    raw = {run.SPEC: (SOURCE_ROOT / run.SPEC).read_bytes(),
           run.ORIGINAL: (SOURCE_ROOT / run.ORIGINAL).read_bytes(),
           "python/research/__init__.py": b"",
           run.OWN: b'"""Invented admitted entry point."""\n',
           "python/research/invented_child.py": b"VALUE = 7\n"}
    raw["python/research/invented_parent.py"] = b"from research import invented_child\n"
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
    monkeypatch.setattr(run, "ROOTS", ("research.invented_parent",))
    monkeypatch.setattr(run, "_git", git)
    monkeypatch.setattr(run, "sys", SimpleNamespace(modules={}, meta_path=[]))
    return tmp_path, raw


def test_exact_reviewed_source_binds_new_spec_raw_registration_and_dependency_closure(reviewed_source):
    _, raw = reviewed_source
    spec, _, identity = run.freeze(MERGE, REVIEWED, TREE)
    assert identity["registration_sha256"] == sha256(raw[run.SPEC]).hexdigest()
    assert identity["parents_sha256"] == spec["source_registration_sha256"] == run.ORIGINAL_SHA256
    assert identity["sources"] == {name: sha256(value).hexdigest() for name, value in raw.items()
                                    if name.startswith("python/")}
    assert len(run.sys.meta_path) == 1


def test_clean_git_status_cannot_hide_changed_source_bytes(reviewed_source):
    root, _ = reviewed_source
    (root / run.SPEC).write_bytes(b'{"unreviewed": true}\n')
    with pytest.raises(ValueError, match="reviewed blob differs"):
        run.freeze(MERGE, REVIEWED, TREE)


def test_pinned_parent_registration_cannot_be_substituted(reviewed_source):
    root, raw = reviewed_source
    raw[run.ORIGINAL] += b"\n"
    (root / run.ORIGINAL).write_bytes(raw[run.ORIGINAL])
    with pytest.raises(ValueError, match="raw registration pin differs"):
        run.freeze(MERGE, REVIEWED, TREE)


def test_project_import_before_admission_is_refused(reviewed_source):
    run.sys.modules["research.invented_preimport"] = object()
    with pytest.raises(ValueError, match="imports preceded source admission"):
        run.freeze(MERGE, REVIEWED, TREE)


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_reviewed_source_alias_is_not_admitted(reviewed_source, alias):
    root, raw = reviewed_source
    source = root / run.SPEC
    alias_target = root / "alias-source.json"
    source.rename(alias_target)
    if alias == "symlink":
        source.symlink_to(alias_target)
    else:
        os.link(alias_target, source)
    with pytest.raises(ValueError, match="unaliased source|source bound"):
        run.freeze(MERGE, REVIEWED, TREE)


def test_runtime_pin_refuses_another_interpreter_before_hashing(monkeypatch):
    monkeypatch.setattr(run, "sys", SimpleNamespace(executable="/invented/other-python"))
    monkeypatch.setattr(bridge, "runtime_sha", lambda: pytest.fail("wrong runtime hashed"))
    with pytest.raises(ValueError, match="pinned local interpreter"):
        run.runtime_pin()


def test_runtime_pin_hashes_the_exact_registered_system_interpreter(monkeypatch):
    monkeypatch.setattr(run, "sys", SimpleNamespace(executable="/usr/bin/python3.12"))
    monkeypatch.setattr(bridge, "runtime_sha", lambda: "e" * 64)
    assert run.runtime_pin() == "e" * 64


def test_closed_loader_executes_pinned_bytes_and_refuses_dynamic_project_import():
    loader = run.ReviewedModules({"research.invented": ("python/research/invented.py", b"ANSWER = 7\n")})
    module = ModuleType("research.invented")
    loader.exec_module(module)
    assert module.ANSWER == 7 and loader.find_spec("json") is None
    with pytest.raises(ValueError, match="Unregistered EL project import"):
        loader.find_spec("research.unregistered_dynamic")


@pytest.mark.parametrize("field,value", [
    ("id", "EK-SNAPSHOT-1"), ("cost_bps", 0), ("orders_enabled", True),
    ("confirmation", True), ("model_fits_enabled", True), ("returns_enabled", True),
    ("signals_enabled", True), ("new_data_acquisition", True), ("maximum_actual_attempts", 4),
    ("availability_lag_minutes", 2), ("support_frames_minutes", [1, 5]),
    ("geometry", {"sessions": 256, "per_scenario": 5140, "union": 5158}),
    ("window", {**run.WINDOW, "start": "2018-01-01"}), ("source_registration_sha256", "0" * 64),
])
def test_scope_mutations_are_refused(field, value):
    spec = specification()
    run.validate_spec(spec)
    spec[field] = value
    with pytest.raises(ValueError, match="registered scope differs"):
        run.validate_spec(spec)


def test_resource_budget_caps_worker_and_helper_aggregate(monkeypatch):
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

    def git(command, **kwargs):
        assert command[0] == "/usr/bin/git" and kwargs["timeout"] == 20
        kwargs["preexec_fn"]()
        return b"invented git metadata"
    monkeypatch.setattr(run.subprocess, "check_output", git)
    assert run._git("status") == b"invented git metadata"
    assert ("limit", (run.resource.RLIMIT_CPU, (4, 4))) in calls
    assert ("limit", (run.resource.RLIMIT_AS, (96 * run.MIB,) * 2)) in calls
    assert ("limit", (run.resource.RLIMIT_NPROC, (0, 0))) in calls
    # Only one serial helper can coexist with the worker: 3072 + 96 < 4096 MiB.
    assert 3 * 1024 + 96 < 4096


@pytest.mark.parametrize("producer,auditor", [
    ({**RESOURCES, "worker_cpu_seconds": 1000}, {**RESOURCES, "worker_cpu_seconds": 501}),
    ({**RESOURCES, "helper_cpu_seconds": 1780}, {**RESOURCES, "helper_cpu_seconds": 1}),
    ({**RESOURCES, "wall_seconds": 1800}, {**RESOURCES, "wall_seconds": 1801}),
    (RESOURCES, {**RESOURCES, "worker_cpu_seconds": True}),
    (RESOURCES, {**RESOURCES, "helper_cpu_seconds": -1}),
])
def test_producer_and_audit_share_the_same_envelope(producer, auditor):
    with pytest.raises(ValueError, match="combined resource envelope differs"):
        run.verify_usage(producer, auditor)


def test_audit_budget_subtracts_producer_and_all_helpers(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "usage", lambda: {"worker_cpu_seconds": 20, "helper_cpu_seconds": 10, "wall_seconds": 25})
    monkeypatch.setattr(run.resource, "setrlimit", lambda *args: calls.append(("limit", args)))
    monkeypatch.setattr(run.signal, "setitimer", lambda *args: calls.append(("timer", args)))
    run.audit_budget({"worker_cpu_seconds": 400, "helper_cpu_seconds": 90, "wall_seconds": 1000})
    assert calls == [("limit", (run.resource.RLIMIT_CPU, (1100, 1110))), ("timer", (run.signal.ITIMER_REAL, 2575))]
    with pytest.raises(ValueError, match="CPU exhausted"):
        run.audit_budget({"worker_cpu_seconds": 1490, "helper_cpu_seconds": 0, "wall_seconds": 1})


@pytest.fixture
def local_run(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "NATIVE", tmp_path)
    (tmp_path / "canonical").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "canonical/experiments.jsonl").write_bytes(b"")
    for relative in ("canonical/intraday-research.lock", "output/eh-support-campaign.lock"):
        (tmp_path / relative).write_bytes(b"")
    monkeypatch.setattr(run, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), platform="linux"))
    real_path = run.Path
    monkeypatch.setattr(run, "Path", lambda value: SimpleNamespace(read_text=lambda: "invented microsoft kernel")
                        if value == "/proc/sys/kernel/osrelease" else real_path(value))
    monkeypatch.setattr(run, "budget", lambda: None)
    monkeypatch.setattr(run, "runtime_pin", lambda: "d" * 64)
    monkeypatch.setattr(run, "usage", lambda: dict(RESOURCES))
    monkeypatch.setattr(run, "audit_budget", lambda _: None)
    release = {"merge": MERGE, "reviewed": REVIEWED, "tree": TREE, "registration_sha256": "a" * 64,
               "parents_sha256": "b" * 64, "sources": {}}
    monkeypatch.setattr(run, "freeze", lambda *args: ({"window": run.WINDOW}, {}, dict(release)))
    monkeypatch.setattr(wire, "prohibit_ef_tasks", lambda: None)
    monkeypatch.setattr(bridge, "restrict_local_network", lambda: None)
    def qualified(identity):
        assert identity["invented"] is True and identity["attempt"] == "qualification1"
        assert identity["qualification_sha256"] is None
        return {}, "c" * 64
    monkeypatch.setattr(run, "audited", qualified)
    return tmp_path, release


def invoke(monkeypatch, mode="actual", attempt="actual1"):
    monkeypatch.setattr(sys, "argv", ["invented-el", mode, "--merge", MERGE, "--reviewed", REVIEWED,
                                     "--tree", TREE, "--attempt", attempt])
    run.main()


def test_actual_reads_only_after_durable_started_and_same_release_qualification(local_run, monkeypatch):
    root, _ = local_run
    appended = []
    append = experiment_log._append_record
    def durable(record, path):
        result = append(record, path)
        appended.append(result["status"])
        return result
    monkeypatch.setattr(experiment_log, "_append_record", durable)
    @contextmanager
    def inputs(slot, prior, invented, audit):
        assert invented is False and audit is False and appended == ["started"]
        first = json.loads((root / "canonical/experiments.jsonl").read_bytes())
        assert first["parameters"]["qualification_sha256"] == "c" * 64
        assert run._doc(run._marker("actual1")) == first["parameters"]
        yield object(), object()
    monkeypatch.setattr(run, "original_inputs", inputs)
    monkeypatch.setattr(support, "build_support", lambda *args, **kwargs: {"invented_stub": True})
    invoke(monkeypatch)
    assert appended == ["started", "completed"]
    identity = run._doc(run._slot("actual1") / "completion.json")["identity"]
    report, _ = run.publication(identity)
    assert report["support"] == {"invented_stub": True}
    assert not run._marker("actual1").exists()
    with pytest.raises(ValueError, match="attempt already exists"):
        invoke(monkeypatch)


def test_bad_qualification_stops_before_new_actual_slot_or_archive_access(local_run, monkeypatch):
    def missing(_):
        raise ValueError("invented same-release qualification audit missing")
    monkeypatch.setattr(run, "audited", missing)
    monkeypatch.setattr(run, "original_inputs", lambda *args: pytest.fail("original read before qualification"))
    with pytest.raises(ValueError, match="qualification audit missing"):
        invoke(monkeypatch)
    assert not run._slot("actual1").exists()
    assert not run._marker("actual1").exists()


def test_controlled_timeout_preserves_old_attempt_and_allows_only_fresh_paths(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        raise TimeoutError("invented worker timeout")
        yield
    monkeypatch.setattr(run, "original_inputs", inputs)
    for attempt in run.ATTEMPTS[:2]:
        with pytest.raises(TimeoutError, match="invented worker timeout"):
            invoke(monkeypatch, attempt=attempt)
        slot = run._slot(attempt)
        old = run._doc(slot / "timeout.json")["identity"]
        run._failed(old)
        assert run._marker(attempt).exists()
    assert len((local_run[0] / "canonical/experiments.jsonl").read_bytes().splitlines()) == 4
    with pytest.raises(ValueError, match="two consecutive failures"):
        invoke(monkeypatch, attempt="actual3")
    assert not run._slot("actual3").exists()
    with pytest.raises(ValueError, match="bounded actual attempt"):
        run.admit_attempt({**old, "attempt": "actual4"})


def test_non_timeout_failure_retains_failed_journal_and_blocks_retry(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        raise ValueError("invented integrity refusal")
        yield
    monkeypatch.setattr(run, "original_inputs", inputs)
    with pytest.raises(ValueError, match="integrity refusal"):
        invoke(monkeypatch)
    assert run._marker("actual1").exists()
    assert not (run._slot("actual1") / "timeout.json").exists()
    records = list(map(json.loads, (local_run[0] / "canonical/experiments.jsonl").read_bytes().splitlines()))
    assert [row["status"] for row in records] == ["started", "failed"]
    assert records[-1]["error_type"] == "ValueError"
    with pytest.raises(FileNotFoundError):
        invoke(monkeypatch, attempt="actual2")
    assert not run._slot("actual2").exists()


def test_canonical_timeout_type_cannot_be_forged_with_a_receipt(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        raise TimeoutError("invented")
        yield
    monkeypatch.setattr(run, "original_inputs", inputs)
    with pytest.raises(TimeoutError):
        invoke(monkeypatch)
    journal = local_run[0] / "canonical/experiments.jsonl"
    journal.write_bytes(journal.read_bytes().replace(b'"error_type": "TimeoutError"', b'"error_type": "RuntimeError"'))
    with pytest.raises(ValueError, match="controlled timeout"):
        invoke(monkeypatch, attempt="actual2")


def test_shared_locks_refuse_a_running_sibling(local_run):
    with run._locks():
        with pytest.raises(BlockingIOError):
            with run._locks():
                pytest.fail("second unit acquired a held lock")


def test_original_digest_mismatch_precedes_universe_and_index(monkeypatch, tmp_path):
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
            pytest.fail("unverified original source yielded")
    assert calls == [("/invented-not-opened/CB", 100)]


def test_audit_publication_reconciles_report_and_journal_before_reopening_original(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        yield object(), object()
    monkeypatch.setattr(run, "original_inputs", inputs)
    monkeypatch.setattr(support, "build_support", lambda *args, **kwargs: {"invented_stub": True})
    invoke(monkeypatch)
    (run._slot("actual1") / "report.json").write_bytes(b'{"changed": true}\n')
    monkeypatch.setattr(run, "original_inputs", lambda *args: pytest.fail("audit input before publication verification"))
    with pytest.raises(ValueError, match="report changed"):
        invoke(monkeypatch, mode="actual-audit")
    assert not (run._slot("actual1") / "audit-start.json").exists()


def test_audit_requires_independent_rebuilt_report_agreement(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        yield object(), object()
    monkeypatch.setattr(run, "original_inputs", inputs)
    monkeypatch.setattr(run, "verify_support_files", lambda *args: None)
    monkeypatch.setattr(support, "build_support", lambda *args, **kwargs: {"invented_count": 1 if not kwargs["audit"] else 2})
    invoke(monkeypatch)
    with pytest.raises(ValueError, match="independent report differs"):
        invoke(monkeypatch, mode="actual-audit")
    assert (run._slot("actual1") / "audit-start.json").exists()
    assert not (run._slot("actual1") / "audit.json").exists()
    with pytest.raises(FileExistsError):
        invoke(monkeypatch, mode="actual-audit")


def test_support_pin_paths_cannot_escape_the_registered_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(wire, "file_digest", lambda *args: pytest.fail("untrusted report path opened"))
    with pytest.raises(ValueError, match="saved support changed"):
        run.verify_support_files(tmp_path, {"records": {"filename": "../outside", "rows": 5160}})


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_receipt_reader_refuses_aliases(alias, tmp_path):
    original, target = tmp_path / "original.json", tmp_path / "receipt.json"
    original.write_bytes(wire.encode({"invented": True}))
    if alias == "symlink":
        target.symlink_to(original)
    else:
        os.link(original, target)
    with pytest.raises(ValueError, match="symlink|regular file"):
        run._doc(target)


def test_receipt_reader_enforces_bounded_regular_files(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (run.MIB + 1))
    with pytest.raises(ValueError, match="bounded source/evidence exceeded"):
        run._doc(path)
