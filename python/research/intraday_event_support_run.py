"""Closed local EL source-support census; no event rule, fit, fill or payoff."""

import argparse
import ast
from contextlib import ExitStack, contextmanager
from hashlib import sha1, sha256
import importlib.abc
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import time


REPO = Path(__file__).absolute().parents[2]
NATIVE = Path("/home/minju/research-evidence/eh-sequential-support-20261107")
SPEC = "configs/research/discovery/intraday-event-support-v1.json"
ORIGINAL = "configs/research/discovery/intraday-trader-local-input-v1.json"
ORIGINAL_SHA256 = "027eb3461fed02730859ccb2b5de141fa22dd80b7c81973c869e2d5a3bde8932"
OWN = "python/research/intraday_event_support_run.py"
UNIT = "EL-SUPPORT-1"
MIB = 1024**2
MAX_REPORT = 16 * MIB
STARTED_CLOCK = time.monotonic()
ATTEMPTS = ("actual1", "actual2", "actual3")
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
ROOTS = ("research.intraday_event_support_run",)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def validate_spec(spec):
    expected = {
        "id": UNIT, "source_registration": ORIGINAL, "source_registration_sha256": ORIGINAL_SHA256,
        "window": WINDOW, "geometry": {"sessions": 257, "per_scenario": 5140, "union": 5158},
        "cost_bps": 33, "orders_enabled": False, "confirmation": False,
        "new_data_acquisition": False, "execution_host": "local_wsl_only",
        "resource_profile": "ej-local-cpu1800-as4096-v1", "maximum_actual_attempts": 3,
        "signals_enabled": False, "returns_enabled": False, "model_fits_enabled": False,
        "availability_lag_minutes": 3, "support_frames_minutes": [5, 15],
        "support_decision_grid_minutes": 5, "opening_minutes": 60, "examples_per_support_state": 2,
    }
    require(type(spec) is dict and all(json.dumps(spec.get(k), sort_keys=True) == json.dumps(v, sort_keys=True)
                                     for k, v in expected.items()), "EL registered scope differs")


def _git(*args):
    def caps():
        resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
        resource.setrlimit(resource.RLIMIT_AS, (96 * MIB,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    return subprocess.check_output(
        ["/usr/bin/git", "--no-optional-locks", "-c", "core.safecrlf=false", "-c", "core.fsmonitor=false",
         "-c", "core.preloadindex=false", "-c", "index.threads=1", "-C", str(REPO), *args],
        env={"PATH": "/usr/bin:/bin", "HOME": "/home/minju", "LC_ALL": "C"}, preexec_fn=caps, timeout=20)


class ReviewedModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Every project import, including a dynamic import, needs admitted bytes."""

    def __init__(self, modules):
        self.modules = modules

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "research" or fullname.startswith("research."):
            require(fullname in self.modules, "Unregistered EL project import")
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == "research")
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, raw = self.modules[module.__name__]
        module.__file__ = str(REPO / path)
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)


def freeze(merge, reviewed, tree):
    require(all(re.fullmatch("[0-9a-f]{40}", v) for v in (merge, reviewed, tree)), "Exact EL source identities required")
    head, current_tree, parents = _git("show", "-s", "--format=%H%n%T%n%P", "HEAD").decode().splitlines()
    require(head == merge and current_tree == tree and len(parents.split()) == 2
            and parents.split()[1] == reviewed and parents.split()[0] != reviewed
            and _git("rev-parse", reviewed + "^{tree}").decode().strip() == tree,
            "EL normal reviewed merge required")
    _git("merge-base", "--is-ancestor", merge, "refs/remotes/origin/main")
    require(not _git("status", "--porcelain", "--untracked-files=no"), "EL tracked source changed")
    entries = {}
    for item in _git("ls-tree", "-rz", "--full-tree", reviewed, "--", "python/research", SPEC, ORIGINAL).split(b"\0"):
        if item:
            mode, name = item.decode().split("\t")
            entries[name] = mode

    def read(name):
        path = REPO / name
        require(name in entries and not any(p.is_symlink() for p in (path, *path.parents)), "EL tracked unaliased source required")
        before = path.stat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size < 2 * MIB, "EL source bound")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink, s.st_mode)
            require(stamp(os.fstat(stream.fileno())) == stamp(before), "EL unstable source before open")
            raw = stream.read(2 * MIB)
            require(stamp(before) == stamp(os.fstat(stream.fileno())) == stamp(path.stat())
                    and len(raw) == before.st_size, "EL unstable source")
        blob = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
        require(entries[name] == "100644 blob " + blob, "EL reviewed blob differs")
        return raw

    registration, original = read(SPEC), read(ORIGINAL)
    spec, prior = json.loads(registration), json.loads(original)
    validate_spec(spec)
    require(sha256(original).hexdigest() == ORIGINAL_SHA256, "EL raw registration pin differs")
    modules, todo = {}, ["research", *ROOTS]
    while todo:
        name = todo.pop()
        if name in modules:
            continue
        path = "python/" + name.replace(".", "/") + ("/__init__.py" if name == "research" else ".py")
        raw = read(path)
        modules[name] = path, raw
        for node in ast.walk(ast.parse(raw)):
            if isinstance(node, ast.ImportFrom):
                require(not node.level, "EL relative import requires admission")
                if node.module == "research":
                    todo.extend("research." + a.name for a in node.names)
                elif node.module and node.module.startswith("research."):
                    todo.append(node.module)
            elif isinstance(node, ast.Import):
                todo.extend(a.name for a in node.names if a.name.startswith("research."))
    closure = {path: sha256(raw).hexdigest() for path, raw in modules.values()}
    closure[OWN] = sha256(read(OWN)).hexdigest()
    require(not any(n == "research" or n.startswith("research.") for n in sys.modules), "EL imports preceded source admission")
    sys.meta_path.insert(0, ReviewedModules(modules))
    return spec, prior, {"merge": merge, "reviewed": reviewed, "tree": tree,
        "registration_sha256": sha256(registration).hexdigest(), "parents_sha256": sha256(original).hexdigest(),
        "sources": closure}


def budget():
    def expired(*_):
        signal.setitimer(signal.ITIMER_REAL, 0)
        raise TimeoutError("EL bounded local execution deadline")
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    resource.setrlimit(resource.RLIMIT_CPU, (1500, 1510))
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024 * MIB,) * 2)
    signal.signal(signal.SIGXCPU, expired)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 3600)


def runtime_pin():
    from research import intraday_campaign_wsl_bridge as bridge
    require(str(Path(sys.executable).resolve()) == "/usr/bin/python3.12", "EL requires the pinned local interpreter")
    return bridge.runtime_sha()


def usage():
    own, child = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"worker_cpu_seconds": math.ceil(own.ru_utime + own.ru_stime) + 5,
            "helper_cpu_seconds": math.ceil(child.ru_utime + child.ru_stime),
            "wall_seconds": math.ceil(time.monotonic() - STARTED_CLOCK) + 5}


def verify_usage(producer, auditor):
    require(set(producer) == set(auditor) == {"worker_cpu_seconds", "helper_cpu_seconds", "wall_seconds"}
            and all(type(v) is int and v >= 0 for row in (producer, auditor) for v in row.values())
            and producer["worker_cpu_seconds"] + auditor["worker_cpu_seconds"] <= 1500
            and sum(row["worker_cpu_seconds"] + row["helper_cpu_seconds"] for row in (producer, auditor)) <= 1800
            and producer["wall_seconds"] + auditor["wall_seconds"] <= 3600, "EL combined resource envelope differs")


def audit_budget(producer):
    verify_usage(producer, dict.fromkeys(("worker_cpu_seconds", "helper_cpu_seconds", "wall_seconds"), 0))
    current = usage()
    available = min(1500 - producer["worker_cpu_seconds"],
                    1800 - producer["worker_cpu_seconds"] - producer["helper_cpu_seconds"] - current["helper_cpu_seconds"])
    require(available > current["worker_cpu_seconds"], "EL shared CPU exhausted before audit")
    resource.setrlimit(resource.RLIMIT_CPU, (available, available + 10))
    wall = 3600 - producer["wall_seconds"] - current["wall_seconds"]
    require(wall > 0, "EL shared wall budget exhausted before audit")
    signal.setitimer(signal.ITIMER_REAL, wall)


def _slot(attempt):
    return NATIVE / "output" / f"{UNIT}-{attempt}"


def _marker(attempt):
    return NATIVE / "canonical" / f"{UNIT}-{attempt}-unresolved.json"


def _journal(identity):
    return _slot(identity["attempt"]) / "experiments.jsonl" if identity["invented"] else NATIVE / "canonical/experiments.jsonl"


def _read(path, maximum=MIB):
    from research import intraday_campaign_wsl_bridge as bridge
    return bridge.bounded_read(path, maximum)


def _doc(path):
    from research import intraday_campaign_wsl_transport as wire
    raw = _read(path)
    result = json.loads(raw)
    require(type(result) is dict and wire.encode(result) == raw, "EL receipt is noncanonical")
    return result


@contextmanager
def _locks():
    import fcntl
    from research import intraday_campaign_wsl_transport as wire
    wire.native_cache(NATIVE, REPO)
    with ExitStack() as stack:
        for relative in ("canonical/intraday-research.lock", "output/eh-support-campaign.lock"):
            path = NATIVE / relative
            before = wire.regular(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stream = stack.enter_context(os.fdopen(fd, "rb"))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before)
                    == wire.file_identity(wire.regular(path)), "EL shared research lock changed")
        yield


def _proof(identity, result, start, end, status):
    from research import intraday_campaign_wsl_transport as wire
    path = _journal(identity)
    before = wire.regular(path)
    require(type(start) is int and type(end) is int and 0 <= start < end <= before.st_size
            and end - start < MIB, "EL journal bounds")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before), "EL journal changed")
        stream.seek(start)
        raw = stream.read(end - start)
    require(wire.file_identity(wire.regular(path)) == wire.file_identity(before), "EL journal changed during read")
    lines = raw.splitlines(keepends=True)
    require(len(raw) == end - start and len(lines) == 2 and all(r.endswith(b"\n") for r in lines), "EL journal pair missing")
    first, last = map(json.loads, lines)
    require(first["status"] == "started" and last["status"] == status and first["run_id"] == last["run_id"]
            and all(r["record_type"] == "discovery_trial" and r["study_id"] == UNIT and r["parameters"] == identity
                    and r["promotion_allowed"] is False and r["code_version"] == identity["merge"]
                    and r["window"] == WINDOW and r["promotion_trial_increment"] == 0
                    and r["specification_sha256"] == identity["registration_sha256"] for r in (first, last)),
            "EL canonical journal identity differs")
    if result is not None:
        require(last["result"] == result, "EL canonical journal result differs")
    elif status == "failed":
        require(last["error_type"] == "TimeoutError", "EL retry is not a controlled timeout")
    return {"start": start, "end": end, "run_id": last["run_id"], "terminal_sha256": sha256(lines[1]).hexdigest()}


@contextmanager
def original_inputs(slot, prior, invented, audit):
    """Reuse only raw framing and fixture geometry; original bytes open after STARTED."""
    from research import intraday_campaign_wsl_transport as wire
    from research import intraday_ed_sources as sources
    from research.intraday_ed_comparison_unit import _decoded_digest
    from research.intraday_trader_unit import _make_invented
    source = prior["actual_source_preparation"]
    geometry = source["geometry"]
    if invented:
        directory = slot / "invented-inputs"
        if not audit:
            wire.exclusive_directory(directory)
            _make_invented(directory, geometry["sessions"], lambda _: None, profile="recovery")
        paths = {name: directory / name for name in ("CB", "CC", "CX")}
        hashes = {name: wire.file_digest(path, 512 * MIB, lambda _: None)[0] for name, path in paths.items()}
        decoded = {kind: _decoded_digest(paths[kind], sources.MAX_STREAM, lambda _: None) for kind in ("CC", "CX")}
        index = directory / "raw-byte-chunks.sqlite"
    else:
        rows = source["local_received_sources"]
        require(len(rows) == 3 and {r["name"] for r in rows} == {"CB", "CC", "CX"}, "EL original input names differ")
        paths, hashes = {}, {}
        for row in rows:
            path = Path(row["path"])
            digest, _ = wire.file_digest(path, row["maximum"], lambda _: None)
            require(digest == row["sha256"], "EL original input digest differs")
            paths[row["name"]], hashes[row["name"]] = path, digest
        decoded = source["decoded_archives"]
        index = Path(source["local_raw_cache"]["path"])
    universe = sources.load_universe(_read(paths["CB"], 16 * MIB), hashes["CB"], expected_sessions=257)
    require(len(universe.targets) == 10280 and len({(t.session, t.symbol) for t in universe.targets}) == 5158,
            "EL original full population differs")
    if not invented:
        require([universe.sessions[0], universe.sessions[-1]] == source["window"], "EL original dates differ")
    archives = {kind: (paths[kind], sources.RawArchivePin(kind, hashes[kind], decoded[kind],
                geometry[kind.lower() + "_chunks"], geometry[kind.lower() + "_rows"],
                geometry["cx_daily_rows"] if kind == "CX" else 0)) for kind in ("CC", "CX")}
    if invented:
        seal_path = directory / "index-seal.json"
        if not audit:
            wire.exclusive_bytes(seal_path, wire.encode(sources.ByteChunkIndex.build(index, archives, lambda _: None)))
        index_sha = _doc(seal_path)["sha256"]
    else:
        index_sha = source["local_raw_cache"]["sha256"]
    with sources.audit_index(index, archives, lambda _: None, expected_cache_sha256=index_sha) as reader:
        yield universe, reader
        reader.assert_unchanged()


def publication(identity):
    from research import intraday_campaign_wsl_transport as wire
    slot = _slot(identity["attempt"])
    done = _doc(slot / "completion.json")
    raw = _read(slot / "report.json", MAX_REPORT)
    require((sha256(raw).hexdigest(), len(raw)) == (done["result"]["report_sha256"], done["result"]["report_bytes"]),
            "EL report changed")
    report = json.loads(raw)
    require(done["identity"] == identity and report["identity"] == identity and wire.encode(report) == raw,
            "EL publication identity or encoding differs")
    span = done["journal"]
    require(_proof(identity, done["result"], span["start"], span["end"], "completed") == span,
            "EL canonical journal differs")
    require(not os.path.lexists(_marker(identity["attempt"])), "EL publication unresolved")
    return report, done


def verify_support_files(slot, support):
    from research import intraday_campaign_wsl_transport as wire
    for key, filename, rows in (("records", "el-event-support.jsonl", 5160),
                                ("summary_file", "el-event-support-summary.json", 1)):
        pin = support[key]
        require(pin["filename"] == filename and pin["rows"] == rows
                and wire.file_digest(slot / filename, 512 * MIB, lambda _: None) == (pin["sha256"], pin["bytes"]),
                "EL saved support changed")


def _audit_receipt(done, measured):
    from research import intraday_campaign_wsl_transport as wire
    return {"identity": done["identity"], "completion_sha256": sha256(wire.encode(done)).hexdigest(),
            "report_sha256": done["result"]["report_sha256"], "independently_verified": True,
            "resource_sha256": sha256(wire.encode(measured)).hexdigest()}


def audited(identity):
    from research import intraday_campaign_wsl_transport as wire
    report, done = publication(identity)
    slot = _slot(identity["attempt"])
    verify_support_files(slot, report["support"])
    measured, receipt = _doc(slot / "audit-resources.json"), _doc(slot / "audit.json")
    require(receipt == _audit_receipt(done, measured), "EL independent audit absent or differs")
    verify_usage(done["result"]["resources"], measured)
    return report, sha256(wire.encode(receipt)).hexdigest()


def _failed(identity):
    slot = _slot(identity["attempt"])
    failure = _doc(slot / "timeout.json")
    require(_doc(_marker(identity["attempt"])) == identity and failure["identity"] == identity
            and failure["error_type"] == "TimeoutError"
            and not any(os.path.lexists(slot / name) for name in ("report.json", "completion.json", "audit.json")),
            "EL retry requires a controlled timeout without ambiguous publication")
    span = failure["journal"]
    require(_proof(identity, None, span["start"], span["end"], "failed") == span, "EL failed journal differs")


def admit_attempt(identity, *, audit=False):
    require(identity["attempt"] in ATTEMPTS and identity["invented"] is False, "EL bounded actual attempt required")
    number = ATTEMPTS.index(identity["attempt"])
    for index, attempt in enumerate(ATTEMPTS):
        old = {**identity, "attempt": attempt}
        if index < number:
            _failed(old)
        elif index > number or not audit:
            require(not os.path.lexists(_slot(attempt)) and not os.path.lexists(_marker(attempt)),
                    "EL attempt already exists or sibling publication is unresolved")
    require(number < 2, "EL two consecutive failures require an implementation change or equivalent diagnostic")


def finalize_audit(slot, identity, done, calculate):
    from research import intraday_campaign_wsl_transport as wire
    receipt, measured_path, start = slot / "audit.json", slot / "audit-resources.json", slot / "audit-start.json"
    require(not os.path.lexists(receipt), "EL audit already exists")
    if os.path.lexists(measured_path):
        require(_doc(start) == identity, "EL audit completion identity differs")
        measured = _doc(measured_path)
        verify_usage(done["result"]["resources"], measured)
    else:
        audit_budget(done["result"]["resources"])
        wire.exclusive_bytes(start, wire.encode(identity))
        try:
            calculate()
        except TimeoutError:
            wire.exclusive_bytes(slot / "audit-timeout.json", wire.encode({"identity": identity, "resources": usage()}))
            raise
        measured = usage()
        verify_usage(done["result"]["resources"], measured)
        wire.exclusive_bytes(measured_path, wire.encode(measured))
    wire.exclusive_bytes(receipt, wire.encode(_audit_receipt(done, measured)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualification", "qualification-audit", "actual", "actual-audit"))
    for name in ("merge", "reviewed", "tree"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--attempt", choices=ATTEMPTS, default="actual1")
    args = parser.parse_args()
    require(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux"
            and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower(), "EL requires local WSL Python -I -S")
    budget()
    spec, prior, release = freeze(args.merge, args.reviewed, args.tree)
    release["runtime_sha256"] = runtime_pin()
    from research import intraday_campaign_wsl_bridge as bridge
    from research import intraday_campaign_wsl_transport as wire, experiment_log
    from research.intraday_event_support import build_support
    invented, audit = args.mode.startswith("qualification"), args.mode.endswith("-audit")
    attempt = "qualification1" if invented else args.attempt
    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    with _locks():
        qualification = None
        if not invented:
            _, qualification = audited({**release, "attempt": "qualification1", "invented": True, "qualification_sha256": None})
        identity = {**release, "attempt": attempt, "invented": invented, "qualification_sha256": qualification}
        if not invented:
            admit_attempt(identity, audit=audit)
        slot = _slot(attempt)

        def calculate(is_audit):
            with original_inputs(slot, prior, invented, is_audit) as (universe, reader):
                return {"support": build_support(slot, universe, reader, audit=is_audit)}

        if audit:
            saved, done = publication(identity)
            def check():
                verify_support_files(slot, saved["support"])
                require({"identity": identity, **calculate(True)} == saved, "EL independent report differs")
            finalize_audit(slot, identity, done, check)
        else:
            wire.exclusive_directory(slot)
            marker = _marker(attempt)
            wire.exclusive_bytes(marker, wire.encode(identity))
            journal = _journal(identity)
            start = 0 if invented else wire.regular(journal).st_size
            def evaluate():
                raw = wire.encode({"identity": identity, **calculate(False)})
                require(len(raw) <= MAX_REPORT, "EL report exceeds fixed bound")
                wire.exclusive_bytes(slot / "report.json", raw)
                return {"report_sha256": sha256(raw).hexdigest(), "report_bytes": len(raw), "resources": usage()}
            try:
                terminal = experiment_log.run_discovery_trial(study_id=UNIT,
                    specification_sha256=identity["registration_sha256"], parameters=identity, window=spec["window"],
                    evaluate=evaluate, runs_path=journal, code_version_provider=lambda: identity["merge"])
            except TimeoutError:
                span = _proof(identity, None, start, wire.regular(journal).st_size, "failed")
                wire.exclusive_bytes(slot / "timeout.json", wire.encode({"identity": identity, "journal": span, "error_type": "TimeoutError"}))
                raise
            span = _proof(identity, terminal["result"], start, wire.regular(journal).st_size, "completed")
            wire.exclusive_bytes(slot / "completion.json", wire.encode({"identity": identity, "journal": span, "result": terminal["result"]}))
            require(_doc(marker) == identity, "EL attempt marker changed")
            marker.unlink()
            wire.fsync_directory(marker.parent)
    print(json.dumps({"unit": UNIT, "mode": args.mode, "status": "completed", "path": str(slot)}, sort_keys=True))


if __name__ == "__main__":
    main()
