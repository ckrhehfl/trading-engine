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
ROOM_SPEC = "configs/research/discovery/intraday-candidate-room-v1.json"
ACTIVITY_SPEC = "configs/research/discovery/intraday-candidate-activity-v1.json"
DIRECTION_SPEC = "configs/research/discovery/intraday-candidate-direction-v1.json"
ADVERSE_SPEC = "configs/research/discovery/intraday-candidate-adverse-v1.json"
TIMING_SPEC = "configs/research/discovery/intraday-candidate-timing-v1.json"
RANGE_SPEC = "configs/research/discovery/intraday-candidate-range-v1.json"
LAGGARD_SPEC = "configs/research/discovery/intraday-candidate-laggard-v1.json"
STABILITY_SPEC = "configs/research/discovery/intraday-candidate-laggard-stability-v1.json"
LAGGARD_PROFIT_SPEC = "configs/research/discovery/intraday-candidate-laggard-profit-v1.json"
LATE_LEADER_SPEC = "configs/research/discovery/intraday-candidate-late-leader-v1.json"
SHOCK_SPEC = "configs/research/discovery/intraday-candidate-shock-v1.json"
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


def _freeze(merge, reviewed, tree, *, study="exit"):
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
    spec_path = {"exit": SPEC, "room": ROOM_SPEC, "activity": ACTIVITY_SPEC, "direction": DIRECTION_SPEC,
                 "adverse": ADVERSE_SPEC, "timing": TIMING_SPEC, "range": RANGE_SPEC, "laggard": LAGGARD_SPEC,
                 "stability": STABILITY_SPEC, "laggard_profit": LAGGARD_PROFIT_SPEC, "late_leader": LATE_LEADER_SPEC,
                 "shock": SHOCK_SPEC}[study]
    entries = {}
    for item in _git("ls-tree", "-rz", "--full-tree", reviewed, "--", "python/research", spec_path, PARENTS,
                     ROOM_SPEC, ACTIVITY_SPEC, DIRECTION_SPEC, ADVERSE_SPEC, LAGGARD_SPEC, STABILITY_SPEC, SPEC).split(b"\0"):
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

    registration, parent_bytes = read(spec_path), read(PARENTS)
    spec, parent_spec = json.loads(registration), json.loads(parent_bytes)
    expected = {
        "exit": (UNIT, 1, ["original", "first_available_close_66bp"], 3),
        "room": ("EJ-ENTRY-ROOM-66", 2, ["room66_original_exit", "room66_profit66_exit"], 2),
        "activity": ("EJ-ENTRY-ACTIVITY-2X", 3, ["activity2x_original_exit", "activity2x_profit66_exit"], 2),
        "direction": ("EJ-ENTRY-ACTIVITY-DIRECTION-5M", 4,
                      ["activity2x_direction5m_original_exit", "activity2x_direction5m_profit66_exit"], 2),
        "adverse": ("EJ-EXIT-ACTIVITY-LOSS33", 5, ["activity2x_profit66_exit", "activity2x_profit66_or_loss33_exit"], 2),
        "timing": ("EJ-EXIT-ACTIVITY-TIME10", 6, ["activity2x_profit66_exit", "activity2x_profit66_or_time10_exit"], 2),
        "range": ("EJ-ENTRY-RANGE-EXPANSION", 7, ["range_expansion_original_exit", "range_expansion_profit66_exit"], 2),
        "laggard": ("EJ-ENTRY-OPENING-LAGGARDS", 8, ["opening_laggards", "opening_laggards_nonnegative_median"], 2),
        "stability": ("EJ-ENTRY-LAGGARD-STABILITY", 9,
                      ["opening_laggards", "laggards_wait5", "laggards_wait5_stable3"], 2),
        "laggard_profit": ("EJ-EXIT-LAGGARD-PROFIT66", 10, ["opening_laggards", "opening_laggards_profit66"], 2),
        "late_leader": ("EJ-ENTRY-LATE-LEADER", 11, ["late_target_only", "late_target_with_leader"], 2),
        "shock": ("EJ-ENTRY-SHOCK-REPAIR", 12, ["first_price_repair", "first_price_repair_volume2x"], 2),
    }[study]
    _need(tuple(spec[key] for key in ("id", "batch", "policies", "maximum_actual_attempts")) == expected
          and spec["cost_bps"] == 33 and spec["saved_registration"] == PARENTS
          and spec["orders_enabled"] is False
          and spec["confirmation"] is False and spec["window"] == parent_spec["window"], "EJ registration differs")
    modules, todo = {}, ["research", *ROOT_MODULES]
    if study not in ("exit", "laggard", "stability", "laggard_profit", "late_leader", "shock"):
        todo.append("research.intraday_candidate_room_saved")
    if study in ("activity", "direction"):
        todo.append("research.intraday_candidate_activity")
        activity_spec = spec
        if study == "direction":
            todo.append("research.intraday_candidate_direction")
            activity_registration = read(ACTIVITY_SPEC)
            _need(sha256(activity_registration).hexdigest() == spec["activity_registration_sha256"]
                  and spec["close_lookback_minutes"] == 5 and spec["close_comparison"] == "strictly_higher",
                  "EJ direction or activity control registration differs")
            activity_spec = json.loads(activity_registration)
        _need(tuple(activity_spec[k] for k in ("recent_minutes", "reference_minutes", "min_ratio")) == (5, 25, 2),
              "EJ activity threshold differs")
        exit_registration = read(ROOM_SPEC)
        _need(sha256(exit_registration).hexdigest() == activity_spec["exit_parent_registration_sha256"],
              "EJ activity exit parent registration differs")
        spec["exit_parent"] = json.loads(exit_registration)["exit_parent"]
    elif study == "range":
        todo.append("research.intraday_candidate_range")
        exit_registration = read(ROOM_SPEC)
        _need(sha256(exit_registration).hexdigest() == spec["exit_parent_registration_sha256"]
              and tuple(spec[k] for k in ("recent_minutes", "reference_minutes", "min_ratio", "availability_lag_minutes"))
              == (5, 25, 2, 3)
              and spec["range_measure"] == "mean_high_minus_low"
              and spec["breakout"] == "latest_close_strictly_above_reference_high",
              "EJ range or exit parent registration differs")
        spec["exit_parent"] = json.loads(exit_registration)["exit_parent"]
    elif study == "laggard":
        todo.append("research.intraday_candidate_laggard_run")
        price_registration = read(SPEC)
        _need(sha256(price_registration).hexdigest() == spec["price_registration_sha256"]
              and re.fullmatch("[0-9a-f]{64}", spec["price_index_parent_sha256"]) is not None
              and tuple(spec[key] for key in ("observation_minutes", "decision_minutes", "entry_minutes",
                                              "quorum", "rank_divisor", "structural_lookback_minutes", "hold_minutes"))
              == (60, 62, 63, 10, 5, 15, 30)
              and spec["entry_limit"] == "latest_close" and spec["market_gate"] == "observed_median_nonnegative",
              "EJ laggard registration differs")
        spec["price_archive"] = json.loads(price_registration)["price_archive"]
    elif study in ("stability", "laggard_profit", "late_leader"):
        if study == "stability":
            todo.append("research.intraday_candidate_laggard_stability_run")
            _need((spec["delay_minutes"], spec["confirmation_offsets"], spec["absolute_expiry_offset"]) == (5, [62, 63, 64], 93)
                  and spec["confirmation_rule"] == "all_closes_above_stop_and_last_at_least_first",
                  "EJ stability rule differs")
        elif study == "laggard_profit":
            todo.append("research.intraday_candidate_laggard_profit_run")
            stability_registration = read(STABILITY_SPEC)
            _need(sha256(stability_registration).hexdigest() == spec["stability_registration_sha256"]
                  and (spec["minimum_profit_close_bps"], spec["earliest_capacity_open_minutes"]) == (66, 4),
                  "EJ laggard profit registration differs")
            parent_registration = json.loads(stability_registration)
            for key in ("laggard_parent_registration_sha256", "laggard_parent"):
                spec[key] = parent_registration[key]
        else:
            todo.append("research.intraday_candidate_late_leader_run")
            _need(tuple(spec[key] for key in ("decision_before_close_minutes", "lookback_minutes", "leader_count",
                                              "leader_quorum", "leader_threshold_bps", "target_min_bps",
                                              "target_max_bps", "hold_minutes")) == (76, 15, 5, 4, 33, 0, 33, 30)
                  and re.fullmatch("[0-9a-f]{64}", spec["universe"]["sha256"]) is not None,
                  "EJ late leader registration differs")
        laggard_registration = read(LAGGARD_SPEC)
        laggard_spec = json.loads(laggard_registration)
        price_registration = read(SPEC)
        _need(sha256(laggard_registration).hexdigest() == spec["laggard_parent_registration_sha256"]
              and sha256(price_registration).hexdigest() == laggard_spec["price_registration_sha256"]
              and re.fullmatch("[0-9a-f]{64}", laggard_spec["price_index_parent_sha256"]) is not None,
              "EJ laggard parent or price registration differs")
        spec["price_archive"] = json.loads(price_registration)["price_archive"]
        spec["price_index_parent_sha256"] = laggard_spec["price_index_parent_sha256"]
    elif study == "shock":
        todo.append("research.intraday_candidate_shock_run")
        price_registration = read(SPEC)
        _need(sha256(price_registration).hexdigest() == spec["price_registration_sha256"]
              and tuple(spec[key] for key in ("decision_offsets", "shock_minutes", "repair_minutes", "shock_bps",
                                              "minimum_repair_fraction", "volume_reference_minutes", "volume_ratio",
                                              "hold_minutes")) == ([62, 122, 182], 5, 5, 66, "1/3", 25, 2, 30)
              and set(spec["snapshot_receipts"]) == {"publication_sha256", "audit_sha256"}
              and all(re.fullmatch("[0-9a-f]{64}", value) for value in spec["snapshot_receipts"].values()),
              "EJ shock source or rule registration differs")
        spec["price_archive"] = json.loads(price_registration)["price_archive"]
    elif study in ("adverse", "timing"):
        matched_spec = spec
        if study == "timing":
            adverse_registration = read(ADVERSE_SPEC)
            _need(sha256(adverse_registration).hexdigest() == spec["adverse_registration_sha256"]
                  and (spec["elapsed_open_minutes"], spec["earliest_capacity_open_minutes"]) == (10, 4),
                  "EJ timing or matched control registration differs")
            matched_spec = json.loads(adverse_registration)
            todo.extend(("research.intraday_candidate_timing", "research.intraday_candidate_capacity_report",
                         "research.intraday_candidate_capacity_reference"))
        else:
            todo.append("research.intraday_candidate_adverse")
        direction_registration = read(DIRECTION_SPEC)
        price_registration = read(SPEC)
        _need(sha256(direction_registration).hexdigest() == matched_spec["activity_parent_registration_sha256"]
              and sha256(price_registration).hexdigest() == matched_spec["price_registration_sha256"]
              and matched_spec["adverse_threshold_bps"] == 33, "EJ matched parent registration or threshold differs")
        spec["activity_parent"] = json.loads(direction_registration)["activity_control_parent"]
        spec["price_archive"] = json.loads(price_registration)["price_archive"]
        spec["price_index_parent_sha256"] = matched_spec["price_index_parent_sha256"]
    else:
        _need(spec["threshold_bps"] == 66, "EJ price threshold differs")
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


def _slot(attempt, unit=UNIT):
    return NATIVE / "output" / f"{unit}-{attempt}"


def _marker(attempt, unit=UNIT):
    return NATIVE / "canonical" / f"{unit}-{attempt}-unresolved.json"


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
    if "price_index_parent_sha256" in spec:
        from research.intraday_context_cost_census_inputs import _metadata
        prior = _slot("actual1")
        index = prior / index.name
        seal = _metadata(prior / seal_path.name, spec["price_index_parent_sha256"])
    elif not audit:
        seal = sources.ByteChunkIndex.build(index, archives, lambda _: None, price_only=True)
        wire.exclusive_bytes(seal_path, wire.encode(seal))
    else:
        seal = _doc(seal_path)
    with sources.audit_index(index, archives, lambda _: None, expected_cache_sha256=seal["sha256"], price_only=True) as reader:
        yield reader
        reader.assert_unchanged()


def _session_pair(reader, row, invented):
    from research import intraday_ed_sources as sources
    from research.intraday_ed_snapshot_verify import reconstruct_reference_session
    from research.intraday_pattern_calibration import session_open, session_close

    if invented:
        return _invented_session(row), _invented_session(row)
    day, symbol = row["session"], row["symbol"]
    raw = reader.load_raw_chunk("CC", symbol, day)
    start, end = session_open(day), session_close(day)
    minute = timedelta(minutes=1)
    starts = tuple(start + i * minute for i in range((end - start) // minute))
    return (sources.full_reference_session_inputs(symbol, day, raw, None),
            reconstruct_reference_session(symbol, day, raw, None, requested_starts=starts))


def _calculate(spec, parent_spec, slot, invented, *, audit=False, stored=None):
    from research import intraday_candidate_exit as calc, intraday_candidate_exit_reference as ref
    from research import intraday_context_cost_census as census, intraday_context_cost_census_reference as old_ref
    from research import intraday_context_cost_census_inputs as old_inputs

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
                    session, reference = _session_pair(reader, row, invented)
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


def _loss_fill_diagnostics(rows):
    from fractions import Fraction

    triggered = filled = 0
    close_bps = gap_bps = Fraction()
    for row in rows:
        for question in row["questions"]:
            arm = question["candidate"]
            if arm["selected_trigger"] != "loss33":
                continue
            triggered += 1
            if arm["status"] == "closed":
                entry = Fraction(arm["entry_price"])
                close = Fraction(arm["trigger_close"])
                filled += 1
                close_bps += (close - entry) * 10_000 / entry
                gap_bps += (Fraction(arm["exit_price"]) - close) * 10_000 / entry
    return {"triggered": triggered, "filled": filled,
            "mean_trigger_bps_on_fills": str(close_bps / filled) if filled else None,
            "mean_next_open_gap_bps_on_fills": str(gap_bps / filled) if filled else None,
            "mean_realized_gross_bps_on_fills": str((close_bps + gap_bps) / filled) if filled else None}


def _calculate_matched(spec, _parent_spec, slot, invented, *, rule, needs_session, details_key, summarize_details,
                       audit=False, stored=None):
    from research import intraday_candidate_exit as calc, intraday_candidate_exit_reference as ref
    from research.intraday_candidate_room_saved import _parent

    earlier, prior = _parent(spec["activity_parent"], "EJ-ENTRY-ACTIVITY-2X", NATIVE, invented,
                             _publication, _audit_receipt)
    reports, outputs = {}, {}
    with _sessions(spec, slot, invented, audit) as reader:
        for scenario, rows in prior.items():
            result = []
            saved = stored[scenario] if audit else [None] * len(rows)
            for row, record in zip(rows, saved, strict=True):
                session, reference = _session_pair(reader, row, invented) if needs_session(row) else (None, None)
                if not audit:
                    record = rule.evaluate_coordinate(row, session)
                rule.verify_coordinate(row, reference, record)
                result.append(record)
            report = calc.summarize(result)
            ref.verify_summary(result, report)
            control = earlier[scenario]["comparison"]
            _need((report["coordinates"], report["sessions"], report["weeks"]) == (3140, 157, 34)
                  and all(report["groups"][group]["totals"]["baseline"] == panel["totals"]["candidate"]
                          for group, panel in control["groups"].items()), "EJ matched activity control differs")
            reports[scenario] = {"comparison": report, details_key: summarize_details(result)}
            outputs[scenario] = result
    return reports, outputs


def _proof(path, identity, result, start, end, status, unit=UNIT):
    from research import intraday_campaign_wsl_transport as wire

    with path.open("rb") as stream:
        _need(0 <= start < end <= wire.regular(path).st_size and end - start < MIB, "EJ journal bounds")
        stream.seek(start)
        raw = stream.read(end - start)
    records = list(map(json.loads, raw.splitlines()))
    _need(len(records) == 2 and raw.endswith(b"\n"), "EJ journal pair missing")
    first, last = records
    _need(first["status"] == "started" and last["status"] == status and first["run_id"] == last["run_id"]
          and all(r["study_id"] == unit and r["parameters"] == identity and r["promotion_allowed"] is False
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


def _publication(identity, unit=UNIT, *, load_outputs=True):
    from research.intraday_context_cost_census_inputs import _Rows, _source

    slot = _slot(identity["attempt"], unit)
    done = _doc(slot / "completion.json")
    _need(done["identity"] == identity and not os.path.lexists(_marker(identity["attempt"], unit)), "EJ publication incomplete")
    journal = slot / "experiments.jsonl" if identity["invented"] else NATIVE / "canonical/experiments.jsonl"
    proof = _proof(journal, identity, done["result"], done["journal"]["start"], done["journal"]["end"], "completed", unit)
    _need(proof == done["journal"], "EJ terminal proof differs")
    path = slot / "report.json"
    with _source(path, 16 * MIB) as (stream, size):
        raw = stream.read(size + 1)
        _need(len(raw) == size == done["result"]["report_bytes"]
              and sha256(raw).hexdigest() == done["result"]["report_sha256"], "EJ saved report bytes differ")
        report = json.loads(raw)
    _need(report["identity"] == identity and report["outputs"] == done["result"]["outputs"]
          and set(report["outputs"]) == {"S1", "S2"}, "EJ report identity differs")
    outputs = {}
    if not load_outputs:
        return report, done, outputs
    for scenario, pin in report["outputs"].items():
        path = slot / (scenario + ".jsonl")
        with _source(path, 128 * MIB) as (stream, size):
            _need(size == pin["size"] and pin["rows"] == 3140, "EJ saved row extent differs")
            reader = _Rows(stream)
            rows = [reader.next() for _ in range(pin["rows"])]
            reader.finish(pin)
        outputs[scenario] = rows
    return report, done, outputs


def _audit_receipt(done):
    from research import intraday_campaign_wsl_transport as wire
    return {"identity": done["identity"], "completion_sha256": sha256(wire.encode(done)).hexdigest(),
            "report_sha256": done["result"]["report_sha256"], "independently_verified": True}


def _qualification(release, unit=UNIT):
    from research import intraday_campaign_wsl_transport as wire
    identity = {**release, "attempt": "qualification1", "invented": True, "qualification_sha256": None}
    _, done, _ = _publication(identity, unit)
    receipt = _doc(_slot("qualification1", unit) / "audit.json")
    _need(receipt == _audit_receipt(done), "EJ requires the new logic qualification and separate audit")
    return sha256(wire.encode(receipt)).hexdigest()


def _retry(identity, unit=UNIT):
    number = int(identity["attempt"][-1])
    for previous in range(1, number):
        old = {**identity, "attempt": f"actual{previous}"}
        slot = _slot(old["attempt"], unit)
        failure = _doc(slot / "timeout.json")
        _need(failure["identity"] == old and failure["error_type"] == "TimeoutError"
              and _doc(_marker(old["attempt"], unit)) == old
              and not any(os.path.lexists(slot / name) for name in ("report.json", "completion.json", "audit.json")),
              "EJ retry requires a controlled timeout without ambiguous publication")
        span = failure["journal"]
        _need(_proof(NATIVE / "canonical/experiments.jsonl", old, None, span["start"], span["end"], "failed", unit) == span,
              "EJ failed trial proof differs")
    # Third identical run would be a retry loop; implement equivalent partitioning first.
    _need(number <= 2, "Two consecutive failures require an equivalent implementation alternative")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("qualification", "qualification-audit", "actual", "actual-audit"))
    for key in ("merge", "reviewed", "tree"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--attempt", choices=("actual1", "actual2"), default="actual1")
    parser.add_argument("--study", choices=("exit", "room", "activity", "direction", "adverse", "timing", "range",
                                             "laggard", "stability", "laggard_profit", "late_leader", "shock"),
                        default="exit")
    args = parser.parse_args()
    _need(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux"
          and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower(), "Use local WSL Python -I -S")
    _budget()
    spec, parents, release = _freeze(args.merge, args.reviewed, args.tree, study=args.study)
    from research import experiment_log, intraday_campaign_wsl_transport as wire
    from research.intraday_context_cost_census_run import _locks
    from research.intraday_context_cost_census_inputs import verify_parents

    unit = spec["id"]
    if args.study == "shock":
        from research import intraday_candidate_shock_run as shock

        def calculate(spec, parents, slot, invented, **kwargs):
            return shock.calculate(spec, parents, slot, invented, NATIVE, **kwargs)
    elif args.study in ("laggard", "late_leader"):
        if args.study == "laggard":
            from research import intraday_candidate_laggard_run as entry_study
        else:
            from research import intraday_candidate_late_leader_run as entry_study

        def calculate(spec, parents, slot, invented, **kwargs):
            return entry_study.calculate(spec, parents, slot, invented, NATIVE, _sessions, **kwargs)
    elif args.study in ("stability", "laggard_profit"):
        if args.study == "stability":
            from research import intraday_candidate_laggard_stability_run as laggard_replay
        else:
            from research import intraday_candidate_laggard_profit_run as laggard_replay

        def calculate(spec, parents, slot, invented, **kwargs):
            return laggard_replay.calculate(spec, parents, slot, invented, NATIVE, _sessions, _publication,
                                            _audit_receipt, **kwargs)
    elif args.study in ("adverse", "timing"):
        if args.study == "adverse":
            from research import intraday_candidate_adverse as rule

            details_key = "loss_trigger_to_fill"
            summarize_details = reconstruct_details = _loss_fill_diagnostics
        else:
            from research import intraday_candidate_timing as rule
            from research.intraday_candidate_capacity_report import summarize_capacity
            from research.intraday_candidate_capacity_reference import reconstruct_capacity

            details_key = "exit_capacity"
            summarize_details, reconstruct_details = summarize_capacity, reconstruct_capacity

        def calculate(spec, parents, slot, invented, *, audit=False, stored=None):
            needs_session = rule.needs_reference_session if audit and args.study == "timing" else rule.needs_session
            return _calculate_matched(spec, parents, slot, invented, rule=rule, needs_session=needs_session,
                                      details_key=details_key,
                                      summarize_details=reconstruct_details if audit else summarize_details,
                                      audit=audit, stored=stored)
    elif args.study != "exit":
        from research import intraday_candidate_room as selector
        from research.intraday_candidate_room_saved import calculate_saved_gate
        control_choices = None
        if args.study == "activity":
            from research import intraday_candidate_activity as selector
        elif args.study == "direction":
            from research import intraday_candidate_direction as selector
            control_choices = selector.control_choices
        elif args.study == "range":
            from research import intraday_candidate_range as selector
        choice_field = args.study + "_selection"

        def calculate(spec, parents, slot, invented, **kwargs):
            return calculate_saved_gate(spec, parents, slot, invented, NATIVE, _publication, _audit_receipt,
                                        selector=selector, choice_field=choice_field, control_choices=control_choices,
                                        **kwargs)
    else:
        calculate = _calculate

    invented = args.mode.startswith("qualification")
    attempt = "qualification1" if invented else args.attempt
    wire.prohibit_ef_tasks()
    with _locks():
        qualification = None if invented else _qualification(release, unit)
        identity = {**release, "attempt": attempt, "invented": invented, "qualification_sha256": qualification}
        if not invented:
            for parent in parents["inputs"]:
                verify_parents(NATIVE, parent)
            if not args.mode.endswith("-audit"):
                _retry(identity, unit)
        slot = _slot(attempt, unit)
        if args.mode.endswith("-audit"):
            saved, done, outputs = _publication(identity, unit)
            _need(not os.path.lexists(slot / "audit.json"), "EJ audit already exists")
            reports, _ = calculate(spec, parents, slot, invented, audit=True, stored=outputs)
            _need(wire.encode(reports) == wire.encode(saved["scenarios"]), "EJ independent saved report differs")
            result = _audit_receipt(done)
            wire.exclusive_bytes(slot / "audit.json", wire.encode(result))
        else:
            wire.exclusive_directory(slot)
            marker = _marker(attempt, unit)
            wire.exclusive_bytes(marker, wire.encode(identity))
            journal = slot / "experiments.jsonl" if invented else NATIVE / "canonical/experiments.jsonl"
            start = 0 if invented else wire.regular(journal).st_size

            def evaluate():
                reports, outputs = calculate(spec, parents, slot, invented)
                return _write_output(slot, identity, reports, outputs)

            try:
                terminal = experiment_log.run_discovery_trial(
                    study_id=unit, specification_sha256=identity["registration_sha256"], parameters=identity,
                    window=spec["window"], evaluate=evaluate, runs_path=journal, code_version_provider=lambda: identity["merge"],
                )
            except TimeoutError:
                span = _proof(journal, identity, None, start, wire.regular(journal).st_size, "failed", unit)
                wire.exclusive_bytes(slot / "timeout.json", wire.encode({"identity": identity, "journal": span, "error_type": "TimeoutError"}))
                raise
            span = _proof(journal, identity, terminal["result"], start, wire.regular(journal).st_size, "completed", unit)
            result = {"identity": identity, "journal": span, "result": terminal["result"]}
            wire.exclusive_bytes(slot / "completion.json", wire.encode(result))
            _need(_doc(marker) == identity, "EJ attempt marker changed")
            marker.unlink()
            wire.fsync_directory(marker.parent)
    print(json.dumps({"mode": args.mode, "attempt": attempt, "status": "completed", "path": str(slot)}, sort_keys=True))


if __name__ == "__main__":
    main()
