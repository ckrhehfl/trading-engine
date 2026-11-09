"""Thin local EJ exit study; reuse EI receipts, raw decoding and trial logging."""

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha1, sha256
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys


REPO = Path(__file__).absolute().parents[2]
NATIVE = Path("/home/minju/research-evidence/eh-sequential-support-20261107")
SPEC = "configs/research/discovery/intraday-candidate-search-v1.json"
PARENTS = "configs/research/discovery/intraday-context-executable-cost-census-v1.json"
UNIT = "EJ-EXIT-66"
MIB = 1024**2
ROOT_MODULES = (
    "research.intraday_context_cost_census_run", "research.intraday_context_cost_census_reference",
    "research.intraday_context_cost_census_inputs", "research.intraday_ed_sources",
    "research.intraday_ed_snapshot_verify", "research.intraday_candidate_exit",
    "research.intraday_candidate_exit_reference",
)


def _need(condition, reason):
    if not condition:
        raise ValueError(reason)


def _git(*args):
    def caps():
        resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
        resource.setrlimit(resource.RLIMIT_AS, (96 * MIB,) * 2)

    return subprocess.check_output(
        ["/usr/bin/git", "--no-optional-locks", "-c", "core.safecrlf=false", "-c", "core.fsmonitor=false",
         "-c", "core.preloadindex=false", "-c", "index.threads=1", "-C", str(REPO), *args],
        env={"PATH": "/usr/bin:/bin", "HOME": "/home/minju", "LC_ALL": "C"},
        preexec_fn=caps, timeout=20,
    )


class _ReviewedModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile admitted source bytes, without importing a stale bytecode cache."""

    def __init__(self, modules):
        self.modules = modules

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "research" or fullname.startswith("research."):
            _need(fullname in self.modules, "Unregistered EJ project import")
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == "research")
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, raw = self.modules[module.__name__]
        module.__file__ = str(REPO / path)
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)


def _freeze(merge, reviewed, tree):
    import re
    import stat

    _need(all(re.fullmatch("[0-9a-f]{40}", value) for value in (merge, reviewed, tree)), "Exact release IDs required")
    head, got_tree, parents = _git("show", "-s", "--format=%H%n%T%n%P", "HEAD").decode().splitlines()
    _need((head, got_tree) == (merge, tree) and len(parents.split()) == 2
          and parents.split()[1] == reviewed
          and _git("rev-parse", reviewed + "^{tree}").decode().strip() == tree,
          "EJ requires the normal reviewed merge with its identical tree")
    _git("merge-base", "--is-ancestor", merge, "refs/remotes/origin/main")
    _need(not _git("status", "--porcelain", "--untracked-files=no"), "Tracked EJ source differs")
    entries = {}
    for item in _git("ls-tree", "-rz", "--full-tree", reviewed, "--", "python/research", SPEC, PARENTS).split(b"\0"):
        if item:
            mode, name = item.decode().split("\t")
            entries[name] = mode

    def read(name):
        path = REPO / name
        _need(name in entries and not any(p.is_symlink() for p in (path, *path.parents)), "Untracked/aliased EJ source")
        before = path.stat()
        _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size < 2 * MIB, "EJ source bound")
        raw = path.read_bytes()
        after = path.stat()
        stable = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink, s.st_mode)
        _need(stable(after) == stable(before) and len(raw) == before.st_size, "EJ source changed")
        blob = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
        _need(entries[name] == "100644 blob " + blob, "Reviewed EJ source bytes differ")
        return raw

    registration, parent_bytes = read(SPEC), read(PARENTS)
    spec, parent_spec = json.loads(registration), json.loads(parent_bytes)
    _need(spec["id"] == UNIT and spec["batch"] == 1 and spec["threshold_bps"] == 66
          and spec["cost_bps"] == 33 and spec["saved_registration"] == PARENTS
          and spec["policies"] == ["original", "first_available_close_66bp"]
          and spec["maximum_actual_attempts"] == 3 and spec["orders_enabled"] is False
          and spec["confirmation"] is False and spec["window"] == parent_spec["window"], "EJ registration differs")
    modules, todo = {}, ["research", *ROOT_MODULES]
    while todo:
        name = todo.pop()
        if name in modules:
            continue
        relative = "python/" + name.replace(".", "/") + ("/__init__.py" if name == "research" else ".py")
        raw = read(relative)
        modules[name] = relative, raw
        for node in ast.walk(ast.parse(raw)):
            if isinstance(node, ast.ImportFrom):
                _need(not node.level, "Relative import needs explicit EJ source admission")
                if node.module == "research":
                    todo.extend("research." + alias.name for alias in node.names)
                elif node.module and node.module.startswith("research."):
                    todo.append(node.module)
            elif isinstance(node, ast.Import):
                todo.extend(alias.name for alias in node.names if alias.name.startswith("research."))
    own = read("python/research/intraday_candidate_search_run.py")
    closure = {path: sha256(raw).hexdigest() for path, raw in modules.values()}
    closure["python/research/intraday_candidate_search_run.py"] = sha256(own).hexdigest()
    identity = {"merge": merge, "reviewed": reviewed, "tree": tree,
                "registration_sha256": sha256(registration).hexdigest(),
                "parents_sha256": sha256(parent_bytes).hexdigest(), "sources": closure}
    _need("research" not in sys.modules, "EJ dependencies were imported before admission")
    sys.meta_path.insert(0, _ReviewedModules(modules))
    return spec, parent_spec, identity


def _budget():
    def expired(*_):
        signal.setitimer(signal.ITIMER_REAL, 0)
        raise TimeoutError("EJ bounded local execution deadline")

    os.setpriority(os.PRIO_PROCESS, 0, 10)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    # One process does calculation and verification; leave shared CPU for Git.
    resource.setrlimit(resource.RLIMIT_CPU, (1500, 1510))
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024 * MIB,) * 2)
    signal.signal(signal.SIGXCPU, expired)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 3600)


def _slot(attempt):
    return NATIVE / "output" / f"{UNIT}-{attempt}"


def _marker(attempt):
    return NATIVE / "canonical" / f"{UNIT}-{attempt}-unresolved.json"


def _doc(path):
    from research.intraday_context_cost_census_run import _document
    return _document(path)


def _invented_session(row):
    from research.intraday_ed_inputs import Minute, SessionInputs

    day, symbol = row["session"], row["symbol"]
    opening = datetime.fromisoformat(day + "T09:00:00+09:00")
    minutes = {}
    opens = {at: Decimal(price) for at, price, issue in row["modeled_opens"] if price and issue is None}
    for offset in range(390):
        at = opening + timedelta(minutes=offset)
        price = opens.get(at.isoformat(), Decimal(10050))
        close = Decimal(10066) if offset >= 33 else price
        ohlc = price, max(price, close), min(price, close), close
        minutes[at] = Minute(symbol, at, at + timedelta(minutes=3), ohlc, None, "observed", "missing")
    return SessionInputs(symbol, opening, opening + timedelta(minutes=390), minutes)


@contextmanager
def _sessions(spec, slot, invented, audit):
    if invented:
        yield None
        return
    from research import intraday_campaign_wsl_transport as wire, intraday_ed_sources as sources

    pin = spec["price_archive"]
    relative = Path(pin["relative_path"])
    _need(not relative.is_absolute() and ".." not in relative.parts, "EJ archive must remain in admitted native root")
    path = NATIVE / relative
    _need(wire.regular(path).st_size == pin["size"], "EJ original archive size differs")
    archive = sources.RawArchivePin("CC", pin["compressed_sha256"], pin["decoded_sha256"], pin["chunks"], pin["rows"])
    archives = {"CC": (path, archive)}
    index = slot / "eh-price-byte-chunks.sqlite"
    seal_path = slot / "price-index.json"
    if not audit:
        seal = sources.ByteChunkIndex.build(index, archives, lambda _: None, price_only=True)
        wire.exclusive_bytes(seal_path, wire.encode(seal))
    else:
        seal = _doc(seal_path)
    with sources.audit_index(index, archives, lambda _: None, expected_cache_sha256=seal["sha256"], price_only=True) as reader:
        yield reader
        reader.assert_unchanged()


def _calculate(spec, parent_spec, slot, invented, *, audit=False, stored=None):
    from research import intraday_candidate_exit as calc, intraday_candidate_exit_reference as ref
    from research import intraday_context_cost_census as census, intraday_context_cost_census_reference as old_ref
    from research import intraday_context_cost_census_inputs as old_inputs, intraday_ed_sources as sources
    from research.intraday_ed_snapshot_verify import reconstruct_reference_session
    from research.intraday_pattern_calibration import session_open, session_close

    reports, outputs = {}, {}
    with _sessions(spec, slot, invented, audit) as reader:
        for parent in parent_spec["inputs"]:
            scenario = parent["scenario"]
            originals = list(old_inputs.invented_rows() if invented else old_inputs.saved_rows(NATIVE, parent))
            baseline_census = census.calculate(iter(originals))
            old_ref.verify(iter(originals), baseline_census)
            _need((baseline_census["coordinates"], baseline_census["sessions"], baseline_census["weeks"])
                  == (3140, 157, 34), "EJ requires all original evaluation coordinates")
            result = []
            for ordinal, (row, roster) in enumerate(originals):
                needed = any(q["status"] == "opportunity" and q["baseline"]["status"] == "closed" for q in row["questions"])
                session = reference = None
                if needed:
                    if invented:
                        session = _invented_session(row)
                        reference = _invented_session(row)
                    else:
                        day, symbol = row["session"], row["symbol"]
                        raw = reader.load_raw_chunk("CC", symbol, day)
                        session = sources.full_reference_session_inputs(symbol, day, raw, None)
                        starts = tuple(session_open(day) + timedelta(minutes=i)
                                       for i in range((session_close(day) - session_open(day)) // timedelta(minutes=1)))
                        reference = reconstruct_reference_session(symbol, day, raw, None, requested_starts=starts)
                if audit:
                    record = stored[scenario][ordinal]
                else:
                    record = calc.evaluate_coordinate(row, roster, lambda day, symbol: session)
                ref.verify_coordinate(row, roster, reference, record)
                result.append(record)
            _need(not audit or len(stored[scenario]) == len(result), "EJ saved output coordinate count differs")
            report = calc.summarize(result)
            ref.verify_summary(result, report)
            reports[scenario] = {"baseline_census": baseline_census, "comparison": report}
            outputs[scenario] = result
    return reports, outputs


def _proof(path, identity, result, start, end, status):
    from research import intraday_campaign_wsl_transport as wire

    with path.open("rb") as stream:
        _need(0 <= start < end <= wire.regular(path).st_size and end - start < MIB, "EJ journal bounds")
        stream.seek(start)
        raw = stream.read(end - start)
    records = list(map(json.loads, raw.splitlines()))
    _need(len(records) == 2 and raw.endswith(b"\n"), "EJ journal pair missing")
    first, last = records
    _need(first["status"] == "started" and last["status"] == status and first["run_id"] == last["run_id"]
          and all(r["study_id"] == UNIT and r["parameters"] == identity and r["promotion_allowed"] is False
                  and r["code_version"] == identity["merge"] and r["specification_sha256"] == identity["registration_sha256"]
                  for r in records), "EJ journal identity differs")
    if result is not None:
        _need(last["result"] == result, "EJ journal result differs")
    return {"start": start, "end": end, "run_id": last["run_id"], "terminal_sha256": sha256(raw.splitlines(keepends=True)[1]).hexdigest()}


def _write_output(slot, identity, reports, outputs):
    from research import intraday_campaign_wsl_transport as wire

    seals = {}
    for scenario, rows in outputs.items():
        path = slot / (scenario + ".jsonl")
        digest = sha256()
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            for row in rows:
                raw = wire.encode(row)
                stream.write(raw)
                digest.update(raw)
            stream.flush()
            os.fsync(stream.fileno())
        seals[scenario] = {"sha256": digest.hexdigest(), "size": path.stat().st_size, "rows": len(rows)}
    raw = wire.encode({"identity": identity, "scenarios": reports, "outputs": seals})
    wire.exclusive_bytes(slot / "report.json", raw)
    return {"report_sha256": sha256(raw).hexdigest(), "report_bytes": len(raw), "outputs": seals}


def _publication(identity):
    from research import intraday_campaign_wsl_transport as wire

    slot = _slot(identity["attempt"])
    done = _doc(slot / "completion.json")
    _need(done["identity"] == identity and not os.path.lexists(_marker(identity["attempt"])), "EJ publication incomplete")
    journal = slot / "experiments.jsonl" if identity["invented"] else NATIVE / "canonical/experiments.jsonl"
    proof = _proof(journal, identity, done["result"], done["journal"]["start"], done["journal"]["end"], "completed")
    _need(proof == done["journal"], "EJ terminal proof differs")
    path = slot / "report.json"
    _need(wire.file_digest(path, 16 * MIB, lambda _: None) == (done["result"]["report_sha256"], done["result"]["report_bytes"]),
          "EJ saved report bytes differ")
    report = json.loads(path.read_bytes())
    _need(report["identity"] == identity and report["outputs"] == done["result"]["outputs"]
          and set(report["outputs"]) == {"S1", "S2"}, "EJ report identity differs")
    outputs = {}
    for scenario, pin in report["outputs"].items():
        path = slot / (scenario + ".jsonl")
        _need(wire.file_digest(path, 128 * MIB, lambda _: None) == (pin["sha256"], pin["size"]), "EJ saved row digest differs")
        with path.open("rb") as stream:
            rows = [json.loads(line) for line in stream]
        _need(pin["rows"] == len(rows) == 3140, "EJ saved full population differs")
        outputs[scenario] = rows
    return report, done, outputs


def _audit_receipt(done):
    from research import intraday_campaign_wsl_transport as wire
    return {"identity": done["identity"], "completion_sha256": sha256(wire.encode(done)).hexdigest(),
            "report_sha256": done["result"]["report_sha256"], "independently_verified": True}


def _qualification(release):
    from research import intraday_campaign_wsl_transport as wire
    identity = {**release, "attempt": "qualification1", "invented": True, "qualification_sha256": None}
    _, done, _ = _publication(identity)
    receipt = _doc(_slot("qualification1") / "audit.json")
    _need(receipt == _audit_receipt(done), "EJ requires the new logic qualification and separate audit")
    return sha256(wire.encode(receipt)).hexdigest()


def _retry(identity):
    number = int(identity["attempt"][-1])
    for previous in range(1, number):
        old = {**identity, "attempt": f"actual{previous}"}
        slot = _slot(old["attempt"])
        failure = _doc(slot / "timeout.json")
        _need(failure["identity"] == old and failure["error_type"] == "TimeoutError"
              and _doc(_marker(old["attempt"])) == old
              and not any(os.path.lexists(slot / name) for name in ("report.json", "completion.json", "audit.json")),
              "EJ retry requires a controlled timeout without ambiguous publication")
        span = failure["journal"]
        _need(_proof(NATIVE / "canonical/experiments.jsonl", old, None, span["start"], span["end"], "failed") == span,
              "EJ failed trial proof differs")
    # Third identical run would be a retry loop; implement equivalent partitioning first.
    _need(number <= 2, "Two consecutive failures require an equivalent implementation alternative")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualification", "qualification-audit", "actual", "actual-audit"))
    for key in ("merge", "reviewed", "tree"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--attempt", choices=("actual1", "actual2"), default="actual1")
    args = parser.parse_args()
    _need(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux"
          and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower(), "Use local WSL Python -I -S")
    _budget()
    spec, parents, release = _freeze(args.merge, args.reviewed, args.tree)
    from research import experiment_log, intraday_campaign_wsl_transport as wire
    from research.intraday_context_cost_census_run import _locks
    from research.intraday_context_cost_census_inputs import verify_parents

    invented = args.mode.startswith("qualification")
    attempt = "qualification1" if invented else args.attempt
    wire.prohibit_ef_tasks()
    with _locks():
        qualification = None if invented else _qualification(release)
        identity = {**release, "attempt": attempt, "invented": invented, "qualification_sha256": qualification}
        if not invented:
            for parent in parents["inputs"]:
                verify_parents(NATIVE, parent)
            if not args.mode.endswith("-audit"):
                _retry(identity)
        slot = _slot(attempt)
        if args.mode.endswith("-audit"):
            saved, done, outputs = _publication(identity)
            _need(not os.path.lexists(slot / "audit.json"), "EJ audit already exists")
            reports, _ = _calculate(spec, parents, slot, invented, audit=True, stored=outputs)
            _need(reports == saved["scenarios"], "EJ independent saved report differs")
            result = _audit_receipt(done)
            wire.exclusive_bytes(slot / "audit.json", wire.encode(result))
        else:
            wire.exclusive_directory(slot)
            marker = _marker(attempt)
            wire.exclusive_bytes(marker, wire.encode(identity))
            journal = slot / "experiments.jsonl" if invented else NATIVE / "canonical/experiments.jsonl"
            start = 0 if invented else wire.regular(journal).st_size

            def evaluate():
                reports, outputs = _calculate(spec, parents, slot, invented)
                return _write_output(slot, identity, reports, outputs)

            try:
                terminal = experiment_log.run_discovery_trial(
                    study_id=UNIT, specification_sha256=identity["registration_sha256"], parameters=identity,
                    window=spec["window"], evaluate=evaluate, runs_path=journal, code_version_provider=lambda: identity["merge"],
                )
            except TimeoutError:
                span = _proof(journal, identity, None, start, wire.regular(journal).st_size, "failed")
                wire.exclusive_bytes(slot / "timeout.json", wire.encode({"identity": identity, "journal": span, "error_type": "TimeoutError"}))
                raise
            span = _proof(journal, identity, terminal["result"], start, wire.regular(journal).st_size, "completed")
            result = {"identity": identity, "journal": span, "result": terminal["result"]}
            wire.exclusive_bytes(slot / "completion.json", wire.encode(result))
            _need(_doc(marker) == identity, "EJ attempt marker changed")
            marker.unlink()
            wire.fsync_directory(marker.parent)
    print(json.dumps({"mode": args.mode, "attempt": attempt, "status": "completed", "path": str(slot)}, sort_keys=True))


if __name__ == "__main__":
    main()
