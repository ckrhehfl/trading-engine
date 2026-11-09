"""Run the registered EI cost census against sealed local evidence."""

from argparse import ArgumentParser
from collections import Counter
from contextlib import ExitStack, contextmanager
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
from types import ModuleType


REPO = Path(__file__).absolute().parents[2]
NATIVE = Path("/home/minju/research-evidence/eh-sequential-support-20261107")
UNIT = "EI-EXECUTABLE-COST-CENSUS"
REGISTRATION = "configs/research/discovery/intraday-context-executable-cost-census-v1.json"
QUALIFICATION = "qualification-v1"
ACTUAL_ATTEMPTS = ("actual1", "actual2")
DEPENDENCIES = tuple(f"python/research/{name}.py" for name in (
    "__init__", "intraday_campaign_wsl_transport", "experiment_log",
    "intraday_context_cost_census", "intraday_context_cost_census_reference",
    "intraday_context_cost_census_inputs",
))
SOURCES = (*DEPENDENCIES, "python/research/intraday_context_cost_census_run.py")
LOCKS = ("canonical/intraday-research.lock", "output/eh-support-campaign.lock")
JOURNAL = "canonical/experiments.jsonl"
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
MIB = 1024**2
FOLD_ROWS = {1: 640, 2: 640, 3: 620, 4: 620, 5: 620}
MAX_RECEIPT = MIB
MAX_SPAN = 32768


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _budget():
    # Five Git helpers may each consume up to 2 CPU seconds and 64 MiB.
    # Git calls are serial, and all other work occurs in this one process.
    timed_out = False

    def expire(*_):
        nonlocal timed_out
        timed_out = True
        signal.setitimer(signal.ITIMER_REAL, 0)
        raise TimeoutError("EI cost census resource deadline")

    os.setpriority(os.PRIO_PROCESS, 0, 10)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    resource.setrlimit(resource.RLIMIT_CPU, (285, 290))
    resource.setrlimit(resource.RLIMIT_AS, (448 * MIB,) * 2)
    signal.signal(signal.SIGXCPU, expire)
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, 1200)
    return lambda: timed_out


def _git(*arguments):
    def helper_limits():
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_AS, (64 * MIB,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

    return subprocess.check_output(
        ["/usr/bin/git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.safecrlf=false",
         "-c", "core.preloadindex=false", "-c", "index.threads=1",
         "-C", str(REPO), *arguments],
        env={"PATH": "/usr/bin:/bin", "HOME": "/home/minju", "LC_ALL": "C"},
        timeout=15, preexec_fn=helper_limits,
    )


def _source_bytes(name):
    path = REPO / name
    _need(not any(part.is_symlink() for part in (path, *path.parents)), "aliased EI census source")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MIB,
              "EI census source must be a bounded regular file")
        raw = stream.read(MIB + 1)
        _need(len(raw) == info.st_size, "EI census source changed while reading")
    return raw


def _release(merge, reviewed, tree):
    _need(all(re.fullmatch("[0-9a-f]{40}", value) for value in (merge, reviewed, tree)),
          "EI census release requires exact Git IDs")
    head, head_tree, parents = _git("show", "-s", "--format=%H%n%T%n%P", "HEAD").decode().splitlines()
    parents = parents.split()
    _need((head, head_tree) == (merge, tree) and len(parents) == 2 and parents[1] == reviewed
          and parents[0] != reviewed and _git("rev-parse", reviewed + "^{tree}").decode().strip() == tree,
          "EI census requires the normal reviewed merge and identical tree")
    _git("merge-base", "--is-ancestor", merge, "refs/remotes/origin/main")
    _need(not _git("status", "--porcelain", "--untracked-files=no"), "EI census tracked worktree differs")
    names = (*SOURCES, REGISTRATION)
    entries = _git("ls-tree", "-z", "--full-tree", reviewed, "--", *names).decode().rstrip("\0").split("\0")
    entries = dict((name, mode) for mode, name in (entry.split("\t", 1) for entry in entries))
    _need(entries.keys() == set(names), "EI census reviewed source roster differs")
    sources = {name: _source_bytes(name) for name in names}
    for name, raw in sources.items():
        blob = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
        _need(entries[name] == f"100644 blob {blob}", f"EI census reviewed source bytes differ: {name}")
    registration = sources.pop(REGISTRATION)
    spec = json.loads(registration)
    _need(spec["id"] == UNIT and spec["window"] == WINDOW and spec["cost_bps"] == 33
          and spec["weekly_target"] == 15 and spec["contexts"] == ["A", "B"]
          and len(spec["source_roster"]) == len(SOURCES)
          and set(spec["source_roster"]) == set(SOURCES)
          and [row["scenario"] for row in spec["inputs"]] == ["S1", "S2"],
          "EI census registration differs")
    registration_sha256 = sha256(registration).hexdigest()
    return {"merge": merge, "reviewed": reviewed, "tree": tree, "registration_sha256": registration_sha256}, spec, sources


def _load_dependencies(sources):
    _need("research" not in sys.modules, "EI census project sources must be loaded from reviewed bytes")
    for name in DEPENDENCIES:
        stem = Path(name).stem
        qualified = "research" if stem == "__init__" else "research." + stem
        module = ModuleType(qualified)
        module.__file__ = str(REPO / name)
        module.__package__ = "research"
        if stem == "__init__":
            module.__path__ = []
        sys.modules[qualified] = module
        exec(compile(sources[name], module.__file__, "exec", dont_inherit=True), module.__dict__)
        if stem != "__init__":
            setattr(sys.modules["research"], stem, module)


def _read(path, maximum=MAX_RECEIPT):
    from research import intraday_campaign_wsl_transport as wire

    before = wire.regular(path)
    _need(before.st_size <= maximum, "EI census receipt exceeds its fixed bound")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        _need(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before), "EI census receipt changed")
        raw = stream.read(maximum + 1)
    _need(len(raw) == before.st_size and wire.file_identity(wire.regular(path)) == wire.file_identity(before),
          "EI census receipt changed while reading")
    return raw


def _document(path):
    from research import intraday_campaign_wsl_transport as wire

    raw = _read(path)
    value = json.loads(raw)
    _need(type(value) is dict and wire.encode(value) == raw, "noncanonical EI census receipt")
    return value


@contextmanager
def _locks():
    import fcntl
    from research import intraday_campaign_wsl_transport as wire

    wire.native_cache(NATIVE, REPO)
    with ExitStack() as stack:
        for relative in LOCKS:
            path = NATIVE / relative
            before = wire.regular(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stream = stack.enter_context(os.fdopen(fd, "rb"))
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _need(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before)
                  == wire.file_identity(wire.regular(path)), "EI census research lock changed")
        yield


def _slot(attempt):
    return NATIVE / "output" / f"{UNIT}-{attempt}"


def _marker(attempt):
    return NATIVE / "canonical" / f"{UNIT}-{attempt}-unresolved.json"


def _journal(attempt):
    return _slot(attempt) / "experiments.jsonl" if attempt == QUALIFICATION else NATIVE / JOURNAL


def _identity(release, attempt, qualification_pin):
    return {**release, "attempt": attempt, "qualification_audit_sha256": qualification_pin}


def _journal_proof(identity, start, end, status):
    from research import intraday_campaign_wsl_transport as wire

    path = _journal(identity["attempt"])
    before = wire.regular(path)
    _need(type(start) is int and type(end) is int and 0 <= start < end <= before.st_size
          and end - start <= MAX_SPAN, "EI census journal span differs")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        _need(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before), "EI census journal changed")
        stream.seek(start)
        raw = stream.read(end - start)
    lines = raw.splitlines(keepends=True)
    _need(len(raw) == end - start and len(lines) == 2 and all(line.endswith(b"\n") for line in lines),
          "EI census journal transition is incomplete")
    opened, terminal = map(json.loads, lines)
    _need(all(row["record_type"] == "discovery_trial" and row["study_id"] == UNIT
              and row["specification_sha256"] == identity["registration_sha256"]
              and row["parameters"] == identity and row["code_version"] == identity["merge"]
              and row["promotion_allowed"] is False and row["promotion_trial_increment"] == 0
              for row in (opened, terminal))
          and opened["run_id"] == terminal["run_id"] and opened["status"] == "started"
          and terminal["status"] == status, "EI census journal identity or state differs")
    return {"start": start, "end": end, "terminal_sha256": sha256(lines[-1]).hexdigest(),
            "run_id": terminal["run_id"]}, terminal


def _full_rows(rows):
    folds, sessions = Counter(), Counter()
    for coordinate, roster in rows:
        folds[coordinate["fold"]] += 1
        sessions[coordinate["session"]] += 1
        yield coordinate, roster
    _need(folds == FOLD_ROWS and len(sessions) == 157 and all(count == 20 for count in sessions.values()),
          "EI census original five folds and date geometry required")


def _full_report(report):
    _need((report["coordinates"], report["sessions"], report["weeks"]) == (3140, 157, 34),
          "EI census original report geometry required")


def _row_source(attempt, pin, synthetic_inputs):
    from research.intraday_context_cost_census_inputs import saved_rows

    if attempt == QUALIFICATION:
        synthetic = synthetic_inputs[pin["scenario"]]
        _need(synthetic["scenario"] == pin["scenario"], "EI census synthetic scenario differs")
        return _full_rows(saved_rows(_slot(QUALIFICATION) / "invented", synthetic))
    return _full_rows(saved_rows(NATIVE, pin))


def _verify_parents(spec):
    from research.intraday_context_cost_census_inputs import verify_parents

    for pin in spec["inputs"]:
        verify_parents(NATIVE, pin)


def _output_proof(identity):
    from research import intraday_campaign_wsl_transport as wire

    slot = _slot(identity["attempt"])
    completion = _document(slot / "completion.json")
    _need(completion["identity"] == identity and not os.path.lexists(_marker(identity["attempt"])),
          "EI census completed attempt identity differs")
    journal = completion["journal"]
    proof, terminal = _journal_proof(identity, journal["start"], journal["end"], "completed")
    raw = _read(slot / "report.json")
    result = {"report_sha256": sha256(raw).hexdigest(), "report_bytes": len(raw)}
    report = json.loads(raw)
    invented = identity["attempt"] == QUALIFICATION
    _need(completion == {"identity": identity, "journal": proof, **result} and terminal["result"] == result
          and wire.encode(report) == raw and report["identity"] == identity
          and set(report) == {"identity", "scenarios", "synthetic_inputs"}
          and set(report["scenarios"]) == {"S1", "S2"}
          and set(report["synthetic_inputs"]) == ({"S1", "S2"} if invented else set()),
          "EI census report or completion differs")
    for scenario in report["scenarios"].values():
        _full_report(scenario)
    return report, completion


def _audit_receipt(completion):
    from research import intraday_campaign_wsl_transport as wire

    return {"identity": completion["identity"], "completion_sha256": sha256(wire.encode(completion)).hexdigest(),
            "report_sha256": completion["report_sha256"], "verified": True}


def _qualification_gate(release):
    from research import intraday_campaign_wsl_transport as wire

    _, completion = _output_proof(_identity(release, QUALIFICATION, None))
    receipt = _document(_slot(QUALIFICATION) / "audit.json")
    _need(receipt == _audit_receipt(completion), "EI census requires the separate same-release qualification audit")
    return sha256(wire.encode(receipt)).hexdigest()


def _allow_replacement(release, qualification_pin):
    original = _identity(release, ACTUAL_ATTEMPTS[0], qualification_pin)
    slot = _slot(ACTUAL_ATTEMPTS[0])
    failure = _document(slot / "timeout.json")
    journal = failure["journal"]
    proof, terminal = _journal_proof(original, journal["start"], journal["end"], "failed")
    _need(failure == {"identity": original, "journal": proof, "error_type": "TimeoutError"}
          and terminal["error_type"] == "TimeoutError" and _document(_marker(ACTUAL_ATTEMPTS[0])) == original
          and not any(os.path.lexists(slot / name) for name in ("report.json", "completion.json", "audit.json")),
          "EI census replacement requires an intact same-release controlled timeout")


def _execute(identity, spec, timed_out):
    from research import experiment_log, intraday_campaign_wsl_transport as wire
    from research.intraday_context_cost_census import calculate
    from research.intraday_context_cost_census_inputs import write_invented_inputs
    from research.intraday_context_cost_census_reference import verify

    attempt = identity["attempt"]
    slot, journal, marker = _slot(attempt), _journal(attempt), _marker(attempt)
    wire.safe_path(journal)
    start = 0 if attempt == QUALIFICATION else wire.regular(journal).st_size
    _need(not os.path.lexists(marker), "EI census attempt marker already exists")
    wire.exclusive_directory(slot)
    wire.exclusive_bytes(marker, wire.encode(identity))

    def evaluate():
        synthetic_inputs = {}
        if attempt == QUALIFICATION:
            root = slot / "invented"
            wire.exclusive_directory(root)
            for scenario in ("S1", "S2"):
                synthetic_inputs[scenario] = write_invented_inputs(root, scenario)
        scenarios = {}
        for pin in spec["inputs"]:
            report = calculate(_row_source(attempt, pin, synthetic_inputs))
            _full_report(report)
            verify(_row_source(attempt, pin, synthetic_inputs), report)
            scenarios[pin["scenario"]] = report
        raw = wire.encode({"identity": identity, "scenarios": scenarios, "synthetic_inputs": synthetic_inputs})
        _need(len(raw) <= MAX_RECEIPT, "EI census report exceeds its fixed bound")
        wire.exclusive_bytes(slot / "report.json", raw)
        return {"report_sha256": sha256(raw).hexdigest(), "report_bytes": len(raw)}

    try:
        terminal = experiment_log.run_discovery_trial(
            study_id=UNIT, specification_sha256=identity["registration_sha256"], parameters=identity,
            window=spec["window"], evaluate=evaluate, runs_path=journal,
            code_version_provider=lambda: identity["merge"],
        )
    except TimeoutError:
        if timed_out():
            proof, failed = _journal_proof(identity, start, wire.regular(journal).st_size, "failed")
            _need(failed["error_type"] == "TimeoutError", "EI census controlled timeout journal differs")
            wire.exclusive_bytes(slot / "timeout.json", wire.encode(
                {"identity": identity, "journal": proof, "error_type": "TimeoutError"}))
        raise
    proof, durable = _journal_proof(identity, start, wire.regular(journal).st_size, "completed")
    _need(terminal == durable, "EI census terminal differs from its durable journal")
    receipt = {"identity": identity, "journal": proof, **terminal["result"]}
    wire.exclusive_bytes(slot / "completion.json", wire.encode(receipt))
    _need(_document(marker) == identity, "EI census attempt marker changed")
    marker.unlink()
    wire.fsync_directory(marker.parent)
    return receipt


def _audit(identity, spec):
    from research import intraday_campaign_wsl_transport as wire
    from research.intraday_context_cost_census_reference import verify

    output, completion = _output_proof(identity)
    path = _slot(identity["attempt"]) / "audit.json"
    _need(not os.path.lexists(path), "EI census audit slot already exists")
    for pin in spec["inputs"]:
        verify(_row_source(identity["attempt"], pin, output["synthetic_inputs"]), output["scenarios"][pin["scenario"]])
    receipt = _audit_receipt(completion)
    wire.exclusive_bytes(path, wire.encode(receipt))
    return receipt


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualification", "qualification-audit", "actual", "actual-audit"))
    for key in ("merge", "reviewed", "tree"):
        parser.add_argument(f"--{key}", required=True)
    parser.add_argument("--attempt", choices=ACTUAL_ATTEMPTS)
    args = parser.parse_args()
    _need(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux"
          and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower(),
          "EI census requires isolated local WSL Python (-I -S)")
    qualification = args.mode.startswith("qualification")
    _need(not qualification or args.attempt is None, "EI census qualification has its own fixed attempt")
    attempt = QUALIFICATION if qualification else (args.attempt or ACTUAL_ATTEMPTS[0])
    timed_out = _budget()
    release, spec, sources = _release(args.merge, args.reviewed, args.tree)
    _load_dependencies(sources)
    from research import intraday_campaign_wsl_transport as wire

    wire.prohibit_ef_tasks()
    with _locks():
        qualification_pin = None if qualification else _qualification_gate(release)
        identity = _identity(release, attempt, qualification_pin)
        if not qualification:
            if attempt == ACTUAL_ATTEMPTS[1]:
                _allow_replacement(release, qualification_pin)
            _verify_parents(spec)
        if args.mode.endswith("-audit"):
            result = _audit(identity, spec)
        else:
            if attempt == ACTUAL_ATTEMPTS[0]:
                _need(not os.path.lexists(_slot(ACTUAL_ATTEMPTS[1])), "EI census replacement slot already exists")
            result = _execute(identity, spec, timed_out)
    print(wire.encode(result).decode(), end="")


if __name__ == "__main__":
    main()
