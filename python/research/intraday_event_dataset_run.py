"""Closed EL event-dataset shards; source gates and independent receipt audits."""

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
SPEC = "configs/research/discovery/intraday-event-dataset-v1.json"
ORIGINAL = "configs/research/discovery/intraday-trader-local-input-v1.json"
ORIGINAL_SHA256 = "027eb3461fed02730859ccb2b5de141fa22dd80b7c81973c869e2d5a3bde8932"
OWN = "python/research/intraday_event_dataset_run.py"
UNIT = "EL-DATASET-RECOVERY-1"
ORIGINAL_UNIT = "EL-DATASET-1"
FAILED_RELEASE = {"merge": "f24b50d4c9b1665b0fc7473d2c4c3c8de5140079",
    "reviewed": "d7e0c6a7262a4266bebf8fa56d942ec1ac0003ae", "tree": "d884eec8c61a0d605706e41580c4767fe1cf3114"}
MIB = 1024**2
MAX_REPORT = 16 * MIB
STARTED_CLOCK = time.monotonic()
ATTEMPTS = ("actual1", "actual2", "actual3")
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
ROOTS = ("research.intraday_event_dataset_run",)
SHARDS = tuple((start, min(start + 32, 257)) for start in range(0, 257, 32))
SUPPORT = {"unit": "EL-SUPPORT-1", "attempt": "actual1",
    "merge": "3474352a7e08e63cad55a7c6cc4d1cac57942d3f",
    "reviewed": "ba95799249de371f08435ae927d4efc04a4a2c9a"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def validate_spec(spec):
    expected = {
        "id": ORIGINAL_UNIT, "source_registration": ORIGINAL, "source_registration_sha256": ORIGINAL_SHA256,
        "window": WINDOW, "geometry": {"sessions": 257, "per_scenario": 5140, "union": 5158},
        "cost_bps": 33, "orders_enabled": False, "confirmation": False,
        "new_data_acquisition": False, "execution_host": "local_wsl_only",
        "resource_profile": "ej-local-cpu1800-as4096-v1", "maximum_actual_attempts": 3,
        "signals_enabled": True, "returns_enabled": True, "model_fits_enabled": False,
        "shard_sessions": 32, "session_shards": [list(pair) for pair in SHARDS],
        "qualification_session_indices": [0, 32], "qualification_full_source_geometry": True,
        "support_predecessor": SUPPORT,
    }
    require(type(spec) is dict and all(json.dumps(spec.get(k), sort_keys=True) == json.dumps(v, sort_keys=True)
                                     for k, v in expected.items()), "EL dataset registered scope differs")
    require(type(spec.get("scientific_contract")) is dict and bool(spec["scientific_contract"]),
            "EL dataset scientific contract is required")


def verify_contract(spec):
    from research import intraday_event_dataset as dataset
    require(spec["scientific_contract"] == dataset.CONTRACT, "EL dataset scientific implementation contract differs")


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


def _proof(identity, result, start, end, status, *, unit=UNIT, error_type="TimeoutError"):
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
            and all(r["record_type"] == "discovery_trial" and r["study_id"] == unit and r["parameters"] == identity
                    and r["promotion_allowed"] is False and r["code_version"] == identity["merge"]
                    and r["window"] == WINDOW and r["promotion_trial_increment"] == 0
                    and r["specification_sha256"] == identity["registration_sha256"] for r in (first, last)),
            "EL canonical journal identity differs")
    if result is not None:
        require(last["result"] == result, "EL canonical journal result differs")
    elif status == "failed":
        require(last["error_type"] == error_type, "EL predecessor failure type differs")
    return {"start": start, "end": end, "run_id": last["run_id"], "terminal_sha256": sha256(lines[1]).hexdigest()}


@contextmanager
def original_inputs(slot, prior, invented, audit):
    """Reuse only raw framing and fixture geometry; original bytes open after STARTED."""
    from research import intraday_campaign_wsl_transport as wire
    from research import intraday_ed_sources as sources
    from research.intraday_ed_comparison_unit import _decoded_digest
    from research.intraday_event_fixtures import make_invented
    source = prior["actual_source_preparation"]
    geometry = source["geometry"]
    if invented:
        directory = slot / "invented-inputs"
        if not audit:
            wire.exclusive_directory(directory)
            make_invented(directory, geometry["sessions"], lambda _: None)
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


def verify_dataset_files(slot, manifest):
    from research import intraday_campaign_wsl_transport as wire
    from research.intraday_event_dataset import FILES, MAX_FILE
    require(set(manifest["files"]) == set(FILES), "EL dataset output roster differs")
    pins = [*manifest["files"].items(), ("dataset-manifest.json", manifest["manifest_file"])]
    for filename, pin in pins:
        require(pin["filename"] == filename and type(pin["rows"]) is int and pin["rows"] >= 1
                and wire.file_digest(slot / filename, MAX_FILE, lambda _: None) == (pin["sha256"], pin["bytes"]),
                "EL saved dataset changed")


def qualification_coverage(manifest):
    counts = manifest["counts"]
    obligations = {
        "events_by_family": ("A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK"),
        "frames_by_state": ("CONFIRMED", "INVALIDATED", "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP"),
        "path_status": ("known", "unknown", "unknown_intrabar"),
        "immediate_status": ("known", "no_fill", "unknown", "no_entry"),
        "held_checkpoint_status": ("free_alive",), "held_target_status": ("known",),
    }
    require(manifest["session_indices"] == [0, 32] and counts["coordinates"] == 658
            and counts["scenario_coordinates"] == {"S1": 640, "S2": 640}
            and manifest["observed_maximum_names_on_day"] == 23,
            "EL qualification requires the maximum thirty-two-session geometry")
    require(all(type(counts[group].get(state)) is int and counts[group][state] > 0
                for group, states in obligations.items() for state in states)
            and all(type(counts.get(key)) is int and counts[key] > 0
                    for key in ("counterfactual_books", "held_queries")),
            "EL invented qualification did not exercise the registered cases")


def verify_support_predecessor(expected_sha256):
    from research import intraday_event_support_run as support
    require(re.fullmatch("[0-9a-f]{64}", expected_sha256) is not None, "EL support audit SHA256 required")
    path = NATIVE / "output" / "EL-SUPPORT-1-actual1" / "audit.json"
    raw = _read(path)
    require(sha256(raw).hexdigest() == expected_sha256, "EL predecessor audit pin differs")
    receipt = _doc(path)
    identity = receipt["identity"]
    require(identity["merge"] == SUPPORT["merge"] and identity["reviewed"] == SUPPORT["reviewed"]
            and identity["attempt"] == SUPPORT["attempt"] and identity["invented"] is False
            and identity["parents_sha256"] == ORIGINAL_SHA256,
            "EL predecessor actual source identity differs")
    _, authenticated = support.audited(identity)
    require(authenticated == expected_sha256, "EL predecessor independent audit differs")
    return authenticated


def verify_failed_predecessor(marker_sha256, terminal_sha256, start, end, registration_sha256, support_sha256):
    from research import intraday_campaign_wsl_transport as wire

    require(wire.valid_sha(marker_sha256) and wire.valid_sha(terminal_sha256), "EL original failure pins required")
    attempt = "actual1-s00"
    marker = NATIVE / "canonical" / f"{ORIGINAL_UNIT}-{attempt}-unresolved.json"
    raw = _read(marker)
    require(sha256(raw).hexdigest() == marker_sha256, "EL original failure marker differs")
    identity = json.loads(raw)
    expected = {**FAILED_RELEASE, "attempt": attempt, "invented": False, "shard": 0,
                "session_indices": [0, 32], "registration_sha256": registration_sha256,
                "support_audit_sha256": support_sha256, "parents_sha256": ORIGINAL_SHA256}
    require(type(identity) is dict and wire.encode(identity) == raw
            and all(identity.get(key) == value for key, value in expected.items())
            and wire.valid_sha(identity["qualification_sha256"]), "EL original failure identity differs")
    slot = NATIVE / "output" / f"{ORIGINAL_UNIT}-{attempt}"
    wire.safe_path(slot)
    require(slot.is_dir() and not any(slot.iterdir()), "EL original failed shard has unexpected output")
    proof = _proof(identity, None, start, end, "failed", unit=ORIGINAL_UNIT, error_type="ValueError")
    require(proof["terminal_sha256"] == terminal_sha256, "EL original terminal failure pin differs")
    return {"marker_sha256": marker_sha256, **proof}


def _audit_receipt(done, measured):
    from research import intraday_campaign_wsl_transport as wire
    return {"identity": done["identity"], "completion_sha256": sha256(wire.encode(done)).hexdigest(),
            "report_sha256": done["result"]["report_sha256"], "independently_verified": True,
            "resource_sha256": sha256(wire.encode(measured)).hexdigest()}


def audited(identity):
    from research import intraday_campaign_wsl_transport as wire
    report, done = publication(identity)
    slot = _slot(identity["attempt"])
    verify_dataset_files(slot, report["dataset"])
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


def attempt_name(base, shard):
    require(base in ATTEMPTS and type(shard) is int and 0 <= shard < len(SHARDS), "EL bounded shard attempt required")
    return f"{base}-s{shard:02d}"


def identity_for(release, *, invented, base="actual1", shard=0, qualification=None):
    return {**release, "attempt": "qualification1" if invented else attempt_name(base, shard),
        "shard": None if invented else shard, "session_indices": list(SHARDS[0 if invented else shard]),
        "invented": invented, "qualification_sha256": qualification}


def _other_shard(identity, shard, base):
    return {**identity, "attempt": attempt_name(base, shard), "shard": shard, "session_indices": list(SHARDS[shard])}


def audited_shard(identity, shard):
    present = [base for base in ATTEMPTS
               if os.path.lexists(_slot(attempt_name(base, shard))) or os.path.lexists(_marker(attempt_name(base, shard)))]
    require(present and present == list(ATTEMPTS[:len(present)]) and len(present) <= 2,
            "EL preceding shard is absent or its attempt history is ambiguous")
    for base in present[:-1]:
        _failed(_other_shard(identity, shard, base))
    report, pin = audited(_other_shard(identity, shard, present[-1]))
    return report, pin


def admit_attempt(identity, *, audit=False):
    shard = identity["shard"]
    require(type(shard) is int and 0 <= shard < len(SHARDS) and identity["invented"] is False
            and identity["session_indices"] == list(SHARDS[shard]), "EL bounded actual shard required")
    names = [attempt_name(base, shard) for base in ATTEMPTS]
    require(identity["attempt"] in names, "EL bounded actual attempt required")
    number = names.index(identity["attempt"])
    for other in range(len(SHARDS)):
        if other < shard:
            audited_shard(identity, other)
        elif other > shard:
            require(not any(os.path.lexists(path) for base in ATTEMPTS
                for path in (_slot(attempt_name(base, other)), _marker(attempt_name(base, other)))),
                "EL later shard already exists; fixed order or publication needs reconciliation")
    for index, attempt in enumerate(names):
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
    for name in ("merge", "reviewed", "tree", "support-audit-sha256", "failed-marker-sha256", "failed-terminal-sha256"):
        parser.add_argument("--" + name, required=True)
    for name in ("failed-start", "failed-end"):
        parser.add_argument("--" + name, required=True, type=int)
    parser.add_argument("--attempt", choices=ATTEMPTS, default="actual1")
    parser.add_argument("--shard", type=int, choices=range(len(SHARDS)), default=0)
    args = parser.parse_args()
    invented, audit = args.mode.startswith("qualification"), args.mode.endswith("-audit")
    require(not invented or (args.shard == 0 and args.attempt == "actual1"), "EL fixed maximum-shard qualification required")
    require(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux"
            and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower(), "EL requires local WSL Python -I -S")
    budget()
    spec, prior, release = freeze(args.merge, args.reviewed, args.tree)
    verify_contract(spec)
    release["runtime_sha256"] = runtime_pin()
    from research import intraday_campaign_wsl_bridge as bridge
    from research import intraday_campaign_wsl_transport as wire, experiment_log
    from research.intraday_event_dataset import build_dataset
    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    with _locks():
        release["support_audit_sha256"] = verify_support_predecessor(args.support_audit_sha256)
        release["original_failed_attempt"] = verify_failed_predecessor(args.failed_marker_sha256,
            args.failed_terminal_sha256, args.failed_start, args.failed_end,
            release["registration_sha256"], release["support_audit_sha256"])
        qualification = None
        if not invented:
            _, qualification = audited(identity_for(release, invented=True))
        identity = identity_for(release, invented=invented, base=args.attempt, shard=args.shard, qualification=qualification)
        if not invented:
            admit_attempt(identity, audit=audit)
        slot = _slot(identity["attempt"])

        def calculate(is_audit):
            with original_inputs(slot, prior, invented, is_audit) as (universe, reader):
                manifest = build_dataset(slot, universe, reader, audit=is_audit,
                                         session_indices=tuple(identity["session_indices"]))
            if invented:
                qualification_coverage(manifest)
            return {"dataset": manifest}

        if audit:
            saved, done = publication(identity)
            def check():
                verify_dataset_files(slot, saved["dataset"])
                require({"identity": identity, **calculate(True)} == saved, "EL independent dataset report differs")
            finalize_audit(slot, identity, done, check)
        else:
            wire.exclusive_directory(slot)
            marker = _marker(identity["attempt"])
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
