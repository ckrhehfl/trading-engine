"""Closed local EK snapshot lifecycle using the existing journal and locks."""

import argparse
import ast
from contextlib import contextmanager
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
SPEC = "configs/research/discovery/intraday-chart-recovery-v1.json"
ORIGINAL = "configs/research/discovery/intraday-trader-local-input-v1.json"
UNIT = "EK-SNAPSHOT-1"
MIB = 1024**2
MAX_REPORT = 16*MIB
STARTED_CLOCK = time.monotonic()
ROOTS = ("research.intraday_recovery_snapshot", "research.intraday_recovery_payoff",
         "research.intraday_candidate_search_run", "research.intraday_trader_unit",
         "research.intraday_context_cost_census_run", "research.experiment_log",
         "research.intraday_recovery_comparison", "research.intraday_recovery_collect",
         "research.intraday_ed_numerical")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def validate_spec(spec):
    """Bind every implemented snapshot clock and scientific boundary to source."""
    expected = {
        "id":"EK-CHART-RECOVERY", "decision_offset":62, "entry_offset":63, "expiry_offset":123,
        "availability_lag_minutes":3, "cost_bps":33, "path_target_bps":66,
        "path_observation":"first_available_completed_close_not_intrabar_high_or_fill",
        "own_eligibility":"close59_strictly_below_open0_without_peer_condition",
        "price_support":"all60_for_model_only;original_control_keeps_own_support",
        "limit":"close59", "stop":"minimum_low45_through59",
        "geometry":{"sessions":257,"per_scenario":5140,"union":5158},
        "management_end_offsets":[75,90,105], "management_decision_offsets":[77,92,107],
        "folds":[[0,100,100,132],[0,132,132,164],[0,164,164,195],[0,195,195,226],[0,226,226,257]],
        "resource_profile":"ej-local-cpu1800-as4096-v1", "maximum_actual_attempts_per_unit":2,
        "orders_enabled":False, "confirmation":False, "new_data_acquisition":False,
        "execution_host":"local_wsl_only",
        "window":{"symbol":"KRX:*","interval":"1m","start":"2025-09-04","end":"2026-09-23"},
    }
    require(type(spec) is dict and all(json.dumps(spec.get(k),sort_keys=True) == json.dumps(v,sort_keys=True)
                                     for k,v in expected.items()), "EK snapshot science contract differs")


def _git(*args):
    def caps():
        resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
        resource.setrlimit(resource.RLIMIT_AS, (96*MIB,)*2)
    return subprocess.check_output(["/usr/bin/git", "--no-optional-locks", "-c", "core.safecrlf=false",
        "-c", "core.fsmonitor=false", "-c", "core.preloadindex=false", "-c", "index.threads=1",
        "-C", str(REPO), *args], env={"PATH":"/usr/bin:/bin", "HOME":"/home/minju", "LC_ALL":"C"},
        preexec_fn=caps, timeout=20)


class ReviewedModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, modules):
        self.modules = modules

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "research" or fullname.startswith("research."):
            require(fullname in self.modules, "Unregistered EK project import")
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == "research")
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, raw = self.modules[module.__name__]
        module.__file__ = str(REPO / path)
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)


def freeze(merge, reviewed, tree):
    require(all(re.fullmatch("[0-9a-f]{40}", v) for v in (merge, reviewed, tree)), "Exact EK source identities required")
    head, current_tree, parents = _git("show", "-s", "--format=%H%n%T%n%P", "HEAD").decode().splitlines()
    require(head == merge and current_tree == tree and len(parents.split()) == 2
            and parents.split()[1] == reviewed
            and _git("rev-parse", reviewed + "^{tree}").decode().strip() == tree, "EK normal reviewed merge required")
    _git("merge-base", "--is-ancestor", merge, "refs/remotes/origin/main")
    require(not _git("status", "--porcelain", "--untracked-files=no"), "EK tracked source changed")
    entries = {}
    for item in _git("ls-tree", "-rz", "--full-tree", reviewed, "--", "python/research", SPEC, ORIGINAL).split(b"\0"):
        if item:
            mode, name = item.decode().split("\t")
            entries[name] = mode

    def read(name):
        path = REPO / name
        require(name in entries and not any(p.is_symlink() for p in (path, *path.parents)), "EK tracked unaliased source required")
        before = path.stat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size < 2*MIB, "EK source bound")
        raw = path.read_bytes()
        after = path.stat()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink, s.st_mode)
        require(stamp(before) == stamp(after) and len(raw) == before.st_size, "EK unstable source")
        blob = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
        require(entries[name] == "100644 blob " + blob, "EK reviewed blob differs")
        return raw

    registration, original = read(SPEC), read(ORIGINAL)
    spec, prior = json.loads(registration), json.loads(original)
    validate_spec(spec)
    require(spec["id"] == "EK-CHART-RECOVERY" and spec["source_registration"] == ORIGINAL
            and spec["source_registration_sha256"] == sha256(original).hexdigest()
            and spec["cost_bps"] == 33 and spec["orders_enabled"] is False and spec["confirmation"] is False
            and spec["resource_profile"] == "ej-local-cpu1800-as4096-v1", "EK registered scope differs")
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
                require(not node.level, "EK relative import requires admission")
                if node.module == "research":
                    todo.extend("research." + a.name for a in node.names)
                elif node.module and node.module.startswith("research."):
                    todo.append(node.module)
            elif isinstance(node, ast.Import):
                todo.extend(a.name for a in node.names if a.name.startswith("research."))
    own = "python/research/intraday_recovery_run.py"
    closure = {path:sha256(raw).hexdigest() for path,raw in modules.values()}
    closure[own] = sha256(read(own)).hexdigest()
    require("research" not in sys.modules, "EK imports preceded source admission")
    sys.meta_path.insert(0, ReviewedModules(modules))
    return spec, prior, {"merge":merge, "reviewed":reviewed, "tree":tree,
        "registration_sha256":sha256(registration).hexdigest(), "parents_sha256":sha256(original).hexdigest(),
        "sources":closure}


def budget():
    def expired(*_):
        signal.setitimer(signal.ITIMER_REAL, 0)
        raise TimeoutError("EK bounded local execution deadline")
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    resource.setrlimit(resource.RLIMIT_CPU, (1500, 1510))
    resource.setrlimit(resource.RLIMIT_AS, (3*1024*MIB,)*2)
    signal.signal(signal.SIGXCPU, expired)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 3600)


def usage():
    own = resource.getrusage(resource.RUSAGE_SELF)
    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    # Reserve final receipt/fsync bookkeeping rather than undercount it.
    return {"worker_cpu_seconds": math.ceil(own.ru_utime+own.ru_stime)+5,
            "helper_cpu_seconds": math.ceil(child.ru_utime+child.ru_stime),
            "wall_seconds": math.ceil(time.monotonic()-STARTED_CLOCK)+5}


def audit_budget(producer):
    current = usage()
    available = min(1500-producer["worker_cpu_seconds"],
                    1800-producer["worker_cpu_seconds"]-producer["helper_cpu_seconds"]-current["helper_cpu_seconds"])
    require(available > current["worker_cpu_seconds"], "EK shared scientific CPU exhausted before audit")
    resource.setrlimit(resource.RLIMIT_CPU,(available,available+10))
    wall = 3600-producer["wall_seconds"]-current["wall_seconds"]
    require(wall > 0, "EK shared wall budget exhausted before audit")
    signal.setitimer(signal.ITIMER_REAL,wall)


def verify_usage(producer, auditor):
    require(set(producer) == set(auditor) == {"worker_cpu_seconds","helper_cpu_seconds","wall_seconds"}
            and all(type(v) is int and v >= 0 for row in (producer,auditor) for v in row.values())
            and producer["worker_cpu_seconds"]+auditor["worker_cpu_seconds"] <= 1500
            and sum(row["worker_cpu_seconds"]+row["helper_cpu_seconds"] for row in (producer,auditor)) <= 1800
            and producer["wall_seconds"]+auditor["wall_seconds"] <= 3600, "EK combined resource envelope differs")


@contextmanager
def original_inputs(slot, prior, invented, audit):
    from research import intraday_campaign_wsl_bridge as bridge
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
            _make_invented(directory, geometry["sessions"], lambda _:None, profile="recovery")
        paths = {name:directory/name for name in ("CB", "CC", "CX")}
        hashes = {name:wire.file_digest(path, 512*MIB, lambda _:None)[0] for name,path in paths.items()}
        decoded = {kind:_decoded_digest(paths[kind], sources.MAX_STREAM, lambda _:None) for kind in ("CC", "CX")}
        index = directory / "raw-byte-chunks.sqlite"
    else:
        rows = source["local_received_sources"]
        require({r["name"] for r in rows} == {"CB", "CC", "CX"}, "EK original input names differ")
        paths, hashes = {}, {}
        for row in rows:
            path = Path(row["path"])
            digest, _ = wire.file_digest(path, row["maximum"], lambda _:None)
            require(digest == row["sha256"], "EK original input digest differs")
            paths[row["name"]], hashes[row["name"]] = path, digest
        decoded = source["decoded_archives"]
        index = Path(source["local_raw_cache"]["path"])
    universe = sources.load_universe(bridge.bounded_read(paths["CB"], 16*MIB), hashes["CB"], expected_sessions=257)
    require(len(universe.targets) == 10280 and len({(t.session,t.symbol) for t in universe.targets}) == 5158,
            "EK original full population differs")
    if not invented:
        require([universe.sessions[0], universe.sessions[-1]] == source["window"], "EK original dates differ")
    archives = {kind:(paths[kind], sources.RawArchivePin(kind, hashes[kind], decoded[kind],
                geometry[kind.lower()+"_chunks"], geometry[kind.lower()+"_rows"],
                geometry["cx_daily_rows"] if kind == "CX" else 0)) for kind in ("CC", "CX")}
    if invented:
        seal_path = directory / "index-seal.json"
        if not audit:
            wire.exclusive_bytes(seal_path, wire.encode(sources.ByteChunkIndex.build(index, archives, lambda _:None)))
        index_sha = json.loads(seal_path.read_bytes())["sha256"]
    else:
        index_sha = source["local_raw_cache"]["sha256"]
    with sources.audit_index(index, archives, lambda _:None, expected_cache_sha256=index_sha) as reader:
        yield universe, reader
        reader.assert_unchanged()


def publication(identity, unit=UNIT, *, verify_files=True):
    from research import intraday_candidate_search_run as lifecycle
    from research import intraday_campaign_wsl_transport as wire
    from research.intraday_context_cost_census_run import _read
    slot = lifecycle._slot(identity["attempt"], unit)
    done = lifecycle._doc(slot/"completion.json")
    raw = _read(slot/"report.json",MAX_REPORT)
    require((sha256(raw).hexdigest(),len(raw)) == (done["result"]["report_sha256"],done["result"]["report_bytes"]),
            "EK report changed")
    result = json.loads(raw)
    require(done["identity"] == identity and result["identity"] == identity, "EK publication identity differs")
    require(wire.encode(result) == raw, "EK report is noncanonical")
    if unit == UNIT and verify_files:
        verify_snapshot_files(slot,result["snapshot"])
    span = done["journal"]
    journal = slot/"experiments.jsonl" if identity["invented"] else NATIVE/"canonical/experiments.jsonl"
    require(lifecycle._proof(journal,identity,done["result"],span["start"],span["end"],"completed",unit) == span,
            "EK canonical journal differs")
    require(not os.path.lexists(lifecycle._marker(identity["attempt"], unit)), "EK publication unresolved")
    return result, done


def verify_snapshot_files(slot, snapshot):
    from research import intraday_campaign_wsl_transport as wire
    for name, key in (("ek-features.jsonl","features"), ("ek-holding-features.jsonl","holding_features"),
                      ("ek-labels.jsonl","labels")):
        pin = snapshot[key]
        require(wire.file_digest(slot/name,512*MIB,lambda _:None) == (pin["sha256"],pin["size"]), "EK saved snapshot changed")


def audited(identity, unit, *, verify_files=True):
    from research import intraday_candidate_search_run as lifecycle
    from research import intraday_campaign_wsl_transport as wire
    result, done = (publication(identity) if unit == UNIT and verify_files
                    else publication(identity,unit,verify_files=verify_files))
    slot = lifecycle._slot(identity["attempt"], unit)
    receipt = lifecycle._doc(slot/"audit.json")
    measured = lifecycle._doc(slot/"audit-resources.json")
    require(receipt == {**lifecycle._audit_receipt(done),"resource_sha256":wire.digest(wire.encode(measured))},
            "EK independent audit absent or differs")
    verify_usage(done["result"]["resources"], measured)
    return result, wire.digest(wire.encode(receipt))


def snapshot_input(release, invented, attempt):
    from research import intraday_candidate_search_run as lifecycle
    qualification = None
    if not invented:
        _, qualification = audited({**release,"attempt":"qualification1","invented":True,
                                    "qualification_sha256":None}, UNIT)
    name = "qualification1" if invented else attempt
    identity = {**release,"attempt":name,"invented":invented,"qualification_sha256":qualification}
    result, seal = audited(identity,UNIT,verify_files=invented)
    return lifecycle._slot(name, UNIT), result["snapshot"], seal


def compute_model(slot, inputs, identity, scenario, fold, audit):
    # The pinned installation proof includes the decimal and SQLite extensions.
    import decimal
    import sqlite3
    from research import intraday_campaign_wsl_transport as wire
    from research import intraday_ed_numerical as numerical
    from research.intraday_recovery_comparison import compare

    snapshot_slot, snapshot, _ = inputs
    verify_snapshot_files(snapshot_slot,snapshot)
    receipt = numerical.activate(numerical.ENVIRONMENT, lambda _:None)
    provenance = {"source_sha256":wire.digest(wire.encode(identity["sources"])),
        "runtime_sha256":receipt["runtime_sha256"],"registration_sha256":identity["registration_sha256"],
        "features_sha256":wire.digest(wire.encode([snapshot["features"]["sha256"],snapshot["holding_features"]["sha256"]])),
        "labels_sha256":snapshot["labels"]["sha256"]}
    summary = compare(snapshot_slot,slot,snapshot,scenario=scenario,fold=fold,provenance=provenance,audit=audit)
    return {"comparison":summary,"numerical_environment":numerical.verify_after(receipt,lambda _:None)}


def finalize_audit(slot, identity, done, calculate):
    from research import intraday_campaign_wsl_transport as wire
    from research import intraday_candidate_search_run as lifecycle
    receipt_path, measured_path = slot/"audit.json",slot/"audit-resources.json"
    require(not os.path.lexists(receipt_path), "EK audit already exists")
    used = done["result"]["resources"]
    if os.path.lexists(measured_path):
        # The resources are published only after the reconstructed report
        # matches. Complete the interrupted receipt without reopening inputs.
        marker = slot/"audit-start-1.json"
        require(lifecycle._doc(marker) == identity, "EK audit completion identity differs")
        verify_usage(used,lifecycle._doc(measured_path))
    else:
        marker = slot/"audit-start-1.json"
        previous = slot/"audit-timeout-1.json"
        consumed = None
        if os.path.lexists(marker):
            old = lifecycle._doc(previous)
            require(lifecycle._doc(marker) == identity and old["identity"] == identity,
                    "EK earlier audit is unresolved")
            consumed = old["resources"]
            marker = slot/"audit-start-2.json"
        combined = used if consumed is None else {k:used[k]+consumed[k] for k in used}
        audit_budget(combined)
        wire.exclusive_bytes(marker,wire.encode(identity))
        try:
            calculate()
        except TimeoutError:
            wire.exclusive_bytes(marker.with_name(marker.name.replace("start","timeout")),
                                 wire.encode({"identity":identity,"resources":usage()}))
            raise
        measured = usage()
        if consumed is not None:
            measured = {key:value+consumed[key] for key,value in measured.items()}
        verify_usage(used,measured)
        wire.exclusive_bytes(measured_path,wire.encode(measured))
    measured = lifecycle._doc(measured_path)
    receipt = {**lifecycle._audit_receipt(done),"resource_sha256":wire.digest(wire.encode(measured))}
    wire.exclusive_bytes(receipt_path,wire.encode(receipt))


def check_unresolved_campaign(current):
    from research import intraday_candidate_search_run as lifecycle
    units = (UNIT, *(f"EK-COMPARISON-{s}-F{f}" for s in ("S1","S2") for f in range(1,6)))
    for unit in units:
        for attempt in ("actual1","actual2"):
            marker = lifecycle._marker(attempt,unit)
            slot = lifecycle._slot(attempt,unit)
            if os.path.lexists(marker):
                old = lifecycle._doc(marker)
                failure = lifecycle._doc(slot/"timeout.json")
                require(old.get("attempt") == attempt and old.get("invented") is False
                        and failure["identity"] == old and failure["error_type"] == "TimeoutError"
                        and not any(os.path.lexists(slot/name) for name in ("report.json","completion.json","audit.json")),
                        "EK sibling publication is ambiguous")
                span = failure["journal"]
                require(lifecycle._proof(NATIVE/"canonical/experiments.jsonl",old,None,span["start"],span["end"],"failed",unit)
                        == span, "EK sibling timeout proof differs")
            if unit != current and not os.path.lexists(slot/"audit.json"):
                require(not any(os.path.lexists(slot/f"audit-start-{i}.json") for i in (1,2)),
                        "EK sibling audit needs reconciliation")


def summarize_campaign(release, snapshot_attempt):
    from research import intraday_campaign_wsl_transport as wire
    from research import intraday_candidate_search_run as lifecycle
    from research.intraday_recovery_collect import collect

    _, _, actual_pin = snapshot_input(release,False,snapshot_attempt)
    _, _, invented_pin = snapshot_input(release,True,"actual1")
    _, qualified = audited({**release,"attempt":"qualification1","invented":True,
        "qualification_sha256":None,"snapshot_audit_sha256":invented_pin},"EK-COMPARISON-S1-F5")
    panels = {}
    for scenario in ("S1","S2"):
        folds, audits = [], []
        for fold in range(1,6):
            unit = f"EK-COMPARISON-{scenario}-F{fold}"
            attempt = "actual1" if os.path.lexists(lifecycle._slot("actual1",unit)/"completion.json") else "actual2"
            identity = {**release,"attempt":attempt,"invented":False,
                        "qualification_sha256":qualified,"snapshot_audit_sha256":actual_pin}
            saved, pin = audited(identity,unit)
            folds.append((fold,lifecycle._slot(attempt,unit),saved["comparison"]))
            audits.append(pin)
        panels[scenario] = {**collect(folds),"audits":audits}
    path = NATIVE/"output"/"EK-CAMPAIGN-SUMMARY.json"
    wire.exclusive_bytes(path,wire.encode({"release":release,"snapshot_audit_sha256":actual_pin,"scenarios":panels}))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualification", "qualification-audit", "actual", "actual-audit", "actual-summary"))
    for name in ("merge", "reviewed", "tree"):
        parser.add_argument("--"+name, required=True)
    parser.add_argument("--attempt", choices=("actual1", "actual2"), default="actual1")
    parser.add_argument("--scenario", choices=("S1","S2"))
    parser.add_argument("--fold", type=int, choices=range(1,6))
    parser.add_argument("--snapshot-attempt", choices=("actual1","actual2"), default="actual1")
    args = parser.parse_args()
    require((args.scenario is None) == (args.fold is None), "EK comparison requires scenario and fold")
    require(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux"
            and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower(), "EK requires local WSL Python -I -S")
    budget()
    spec, prior, release = freeze(args.merge,args.reviewed,args.tree)
    from research import intraday_campaign_wsl_transport as wire, experiment_log
    from research import intraday_candidate_search_run as lifecycle
    from research.intraday_context_cost_census_run import _locks
    from research.intraday_recovery_snapshot import build_snapshot

    invented = args.mode.startswith("qualification")
    attempt = "qualification1" if invented else args.attempt
    unit = UNIT if args.scenario is None else f"EK-COMPARISON-{args.scenario}-F{args.fold}"
    require(not invented or unit in (UNIT,"EK-COMPARISON-S1-F5"), "Use the fixed maximum-training invented comparison")
    wire.prohibit_ef_tasks()
    with _locks():
        if args.mode == "actual-summary":
            require(unit == UNIT,"EK summary requires all scenarios and folds")
            path = summarize_campaign(release,args.snapshot_attempt)
            print(json.dumps({"mode":args.mode,"status":"completed","path":str(path)},sort_keys=True))
            return
        if not invented:
            check_unresolved_campaign(unit)
        qualification = None
        inputs = None
        if unit != UNIT:
            if not invented:
                _, _, invented_input = snapshot_input(release,True,"actual1")
                _, qualification = audited({**release,"attempt":"qualification1","invented":True,
                    "qualification_sha256":None,"snapshot_audit_sha256":invented_input},"EK-COMPARISON-S1-F5")
            inputs = snapshot_input(release,invented,args.snapshot_attempt)
        elif not invented:
            qualified = {**release,"attempt":"qualification1","invented":True,"qualification_sha256":None}
            _, qualification = audited(qualified, UNIT)
        identity = {**release,"attempt":attempt,"invented":invented,"qualification_sha256":qualification}
        if inputs is not None:
            identity["snapshot_audit_sha256"] = inputs[2]
        slot = lifecycle._slot(attempt,unit)

        def calculate(audit):
            if inputs is not None:
                return compute_model(slot,inputs,identity,args.scenario,args.fold,audit)
            with original_inputs(slot,prior,invented,audit) as (universe,reader):
                return {"snapshot":build_snapshot(slot,universe,reader,binding=identity,audit=audit)}

        if args.mode.endswith("-audit"):
            saved, done = publication(identity,unit,verify_files=False)
            def audit_calculation():
                if unit == UNIT:
                    verify_snapshot_files(slot,saved["snapshot"])
                rebuilt = calculate(True)
                require({"identity":identity,**rebuilt} == saved, "EK independent report differs")
            finalize_audit(slot,identity,done,audit_calculation)
        else:
            if not invented:
                lifecycle._retry(identity,unit)
            wire.exclusive_directory(slot)
            marker = lifecycle._marker(attempt,unit)
            wire.exclusive_bytes(marker,wire.encode(identity))
            journal = slot/"experiments.jsonl" if invented else NATIVE/"canonical/experiments.jsonl"
            start = 0 if invented else wire.regular(journal).st_size
            def evaluate():
                raw = wire.encode({"identity":identity,**calculate(False)})
                require(len(raw) <= MAX_REPORT,"EK report exceeds fixed bound")
                wire.exclusive_bytes(slot/"report.json",raw)
                return {"report_sha256":sha256(raw).hexdigest(),"report_bytes":len(raw),"resources":usage()}
            try:
                terminal = experiment_log.run_discovery_trial(study_id=unit,
                    specification_sha256=identity["registration_sha256"], parameters=identity,window=spec["window"],
                    evaluate=evaluate,runs_path=journal,code_version_provider=lambda:identity["merge"])
            except TimeoutError:
                span = lifecycle._proof(journal,identity,None,start,wire.regular(journal).st_size,"failed",unit)
                wire.exclusive_bytes(slot/"timeout.json",wire.encode({"identity":identity,"journal":span,"error_type":"TimeoutError"}))
                raise
            span = lifecycle._proof(journal,identity,terminal["result"],start,wire.regular(journal).st_size,"completed",unit)
            wire.exclusive_bytes(slot/"completion.json",wire.encode({"identity":identity,"journal":span,"result":terminal["result"]}))
            require(lifecycle._doc(marker) == identity,"EK attempt marker changed")
            marker.unlink()
            wire.fsync_directory(marker.parent)
    print(json.dumps({"unit":unit,"mode":args.mode,"status":"completed","path":str(slot)},sort_keys=True))


if __name__ == "__main__":
    main()
