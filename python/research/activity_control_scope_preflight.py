"""Log complete control rank metadata and prior pre-opening target ceilings.

Saved activity failures are not control exclusions. This first planner leaves
operating periods and compulsory-successor closure unresolved; it produces no
eligible control, targets, holding-origin bound, prices or performance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import resource
import signal
import time
import traceback

from research import activity_preflight as saved
from research import activity_scope_preflight as prior
from research import experiment_log
from research.activity_holding_preflight import _read_population
from research.activity_normal_sources import _negative_type
from research.activity_partition_selection import _availability


SCHEMA = "activity-control-scope-v1"
MODE = "discovery_control_rank_metadata_only"
SOURCE_PATH = "python/research/activity_control_scope_preflight.py"
INPUTS = {
    **prior.INPUTS,
    "bl_population_audit": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bl-normal-input-assembly-20261010-recovery-v2/run/population-audit.json",
        "sha256": "20c126919c3b2a5c9c911b0bf371bc1c99f78727c134fbea807fef1db62dca76"},
    "bl_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bl-normal-input-assembly-20261010-recovery-v2/run/result.json",
        "sha256": "f9a8ba2d7b84259eaa54560a7095e32adae762dddbd1cb52082dd5acaa409530"},
    "bl_verification": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bl-independent-20261010-v2/verification.json",
        "sha256": "915237d4306b4c93d2b6ac4e503f759d3866d3e61346cfac0009b0ac4678d860"},
    "paired_report": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bq-raw-paired-20261010-v1/run/report.json",
        "sha256": "d1993f2705e7fb3258a52db1f709300aba61701eda2dcc8fbfcd804ee0c4211c"},
    "paired_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bq-raw-paired-20261010-v1/run/result.json",
        "sha256": "29d61f5df6651f7a8f84c28a5cb3038f3967e5324815792a23304a1a3c5af4fc"},
    "paired_verification": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bq-raw-paired-independent-20261014-recovery-v1/verification.json",
        "sha256": "6cdace23457d4f4382f2c6dcee4cd139ed03638eaee321b1ab70f858247d127f"},
    "paired_replay": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bq-raw-paired-20261010-v1/run/paired-replay.json",
        "sha256": "d0bf2992e7c5b7a0d2ec962d12dbd52b12ed23c82792023f571c909b8e74eef2"},
}
PARTITION_COUNTS = {"cap_below": 34327, "negative_type": 27, "bh_failure": 41,
                    "bi_failure": 840, "normal_window": 36}
BI_FIELDS = ("formation", "code", "proven_failure", "reason", "unresolved",
             "consumed_dates", "normal_dates", "frozen_dates", "selection_at",
             "historical_eligibility_certified")


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(type(spec) is dict and set(spec) == {
        "schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs"}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and spec["window"] == saved.WINDOW
        and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])
        and spec["inputs"] == INPUTS, "fixed control metadata specification mismatch")


def bi_metadata(population: dict, bi: dict, calendar: list[str], schedule: list[dict],
                params: dict, *, counts: dict = prior.REGISTERED_COUNTS) -> dict:
    """Validate prior proofs, then retain dates/states/provenance without amounts."""
    prior.plan_scope(population, bi, calendar, schedule, params, counts=counts)
    snapshot = bi["scan_snapshot"]
    saved._pin(snapshot["dataset_sha256"])
    saved.require(snapshot.get("schema") == "task-bi-activity-failure-snapshot-v1"
                  and snapshot.get("whole_database_hash") is False
                  and snapshot.get("original_source_vintage_certified") is False,
                  "BI snapshot identity/meaning mismatch")
    states, metadata = {}, {}
    for name, target in (("row_states", states), ("row_provenance", metadata)):
        for row in snapshot[name]:
            key = row["code"], row["observation_date"]
            saved.require(key not in target and row["state"] in {"observed", "frozen"}
                          and row["issues"] == [], "unknown or duplicate BI state metadata")
            target[key] = row
    saved.require(set(states) == set(metadata), "BI state/provenance key mismatch")
    days = [date.fromisoformat(value) for value in (
        f"{day[:4]}-{day[4:6]}-{day[6:]}" for day in calendar)]
    iso = [day.isoformat() for day in days]
    result, union = [], set()
    for row in bi["windows"]:
        projected = {name: deepcopy(row[name]) for name in BI_FIELDS}
        consumed = projected["consumed_dates"]
        formation = date.fromisoformat(row["formation"]).isoformat()
        saved.require(bool(consumed) and consumed[0] in iso and formation in iso
                      and consumed == iso[iso.index(consumed[0]):iso.index(formation) + 1],
                      "BI consumed dates must be the complete formation interval")
        keys = [(row["code"], day) for day in consumed]
        saved.require(all(key in states for key in keys), "BI consumed state missing")
        observed = [day for day in consumed[:-1] if states[row["code"], day]["state"] == "observed"]
        frozen = [day for day in consumed[:-1] if states[row["code"], day]["state"] == "frozen"]
        saved.require(projected["normal_dates"] == observed[-params["lookback"]:]
                      and len(projected["normal_dates"]) == params["lookback"]
                      and projected["frozen_dates"] == frozen
                      and (row["reason"] == "formation_frozen")
                      == (states[keys[-1]]["state"] == "frozen"), "BI normal/frozen proof metadata mismatch")
        cutoffs = tuple(datetime.fromisoformat(row["selection_at"][arm]) for arm in ("D1", "D2"))
        for key in keys:
            source = metadata[key]
            saved.require(source["state"] == states[key]["state"] and source["issues"] == []
                          and source["snapshot_sha256"] == snapshot["dataset_sha256"],
                          "BI metadata snapshot/state mismatch")
            _availability(source, date.fromisoformat(key[1]), cutoffs)
        projected.update(formation_state=states[keys[-1]]["state"],
            first_required_observation=consumed[0], normal_candidate_start=projected["normal_dates"][0])
        result.append(projected)
        union.update(keys)
    saved.require(union == set(states) and snapshot["read_scope"] == [
        {"code": code, "dates": sorted(day for candidate, day in union if candidate == code)}
        for code in sorted({code for code, _ in union})], "BI complete requested metadata union mismatch")
    return {"windows": result, "snapshot_sha256": snapshot["dataset_sha256"],
        "row_provenance_reference": {"input": "bi_result", "sha256": INPUTS["bi_result"]["sha256"],
            "pointer": "/scan_snapshot/row_provenance", "lookup": "exact code/observation_date",
            "rows": len(union), "seven_fields_preserved_by_pinned_reference": True}}


def saved_targets(docs: dict, schedule: list[dict], pins: dict) -> tuple[dict, dict]:
    """Read declaration counts only; never inspect arm NAV or successful fills."""
    bl, verified = docs["bl_result"], docs["bl_verification"]
    saved.require(bl.get("status") == "normal_inputs_assembled"
        and bl.get("paired_schedule") == schedule and bl.get("returns_computed") is False
        and verified.get("verified") is True and verified.get("returns_computed") is False
        and verified.get("summary") == bl.get("summary")
        and verified["file_sha256"]["result.json"] == pins["bl_result"]
        and verified["file_sha256"]["population-audit.json"] == pins["bl_population_audit"]
        and bl["audit_sha256"]["population-audit.json"] == pins["bl_population_audit"],
        "BL result/population/independent verification links mismatch")
    report, result, verification, replay = (docs[role] for role in (
        "paired_report", "paired_result", "paired_verification", "paired_replay"))
    saved.require(report.get("schema") == "activity-paired-timing-report-v1"
        and result.get("status") == "descriptive_timing_pair_executed"
        and result.get("logged_paired_execution_completed") is True
        and verification.get("verified") is True and verification.get("hash_links_verified") is True
        and replay.get("schema") == "activity-paired-replay-v1"
        and replay.get("status") == "pure_paired_replay"
        and result["report_sha256"] == verification["file_sha256"]["report.json"] == pins["paired_report"]
        and result["paired_replay_sha256"] == verification["file_sha256"]["paired-replay.json"] == pins["paired_replay"]
        and verification["file_sha256"]["result.json"] == pins["paired_result"]
        and saved.digest(saved.encoded(replay["report"])) == pins["paired_report"],
        "saved paired report/replay/verification links mismatch")
    for role in ("bl_result", "bl_population_audit", "bl_verification"):
        saved.require(result["input_sha256"][role] == verification["input_sha256"][role] == pins[role],
                      "saved pair references another BL package")
    formations = [date.fromisoformat(row["formation"]).isoformat() for row in schedule]
    overlap = report["target_overlap"]["formations"]
    saved.require([row["formation"] for row in overlap] == formations,
                  "complete ordered pre-opening target schedule required")
    targets = {}
    for arm in ("D1", "D2"):
        declared = replay["replays"][arm]["selections"]
        expected = [[row["formation"], row[f"{arm}_codes"]] for row in overlap]
        saved.require(declared == expected, "report targets differ from replay pre-opening declarations")
        for _, codes in declared:
            saved.require(type(codes) is list and all(type(code) is str and code for code in codes)
                          and len(codes) == len(set(codes)), "duplicate or invalid declared target")
        targets[arm] = {formation.replace("-", ""): list(codes) for formation, codes in declared}
    return targets, bl["summary"]


def plan_control(audit: dict, population: dict, bi: dict, targets: dict, bl_summary: dict,
                 schedule: list[dict], params: dict, *, partition_counts: dict = PARTITION_COUNTS,
                 expected_possible: int = 875) -> dict:
    """Preserve all source keys and rank numeric possibilities with unknown periods."""
    saved.require(audit.get("schema") == "activity-normal-population-audit-v1",
                  "full BL population audit required")
    formations = {row["formation"]: row["arms"] for row in schedule}
    source = prior._keyed(audit["rows"], set(formations), "BL source")
    previous = prior._keyed(population["prior_dispositions"], set(formations), "BH")
    numeric = prior._keyed(bi["windows"], set(formations), "BI metadata")
    saved.require(set(previous) <= set(source) and set(numeric) <= set(previous), "proof outside full source")
    actual = dict(Counter(row["disposition"] for row in source.values()))
    saved.require(actual == partition_counts and bl_summary["source_rows"] == len(source)
                  and bl_summary["disposition_counts"] == actual,
                  "BL complete population denominator mismatch")
    possible, partitions, prior_keys, bi_keys = [], [], set(), set()
    used_metadata = set()
    for key, row in sorted(source.items()):
        saved.require(type(row["isin"]) is str and bool(row["isin"]), "source ISIN missing")
        provenance = row["krx_metadata_provenance"]
        saved.require((provenance["formation"], provenance["code"]) == key
            and all(type(provenance[field]) is int and provenance[field] >= 1
                    for field in ("source_observation_position", "source_row_position")),
            "source identity/row provenance mismatch")
        saved._pin(provenance["source_response_sha256"])
        cap = Decimal(row["capitalization"])
        saved.require(cap.is_finite() and cap >= 0 and cap == cap.to_integral_value()
                      and type(row["cap_pass"]) is bool and row["cap_pass"] == (cap >= 5000000000000),
                      "source cap proof mismatch")
        cutoffs = tuple(datetime.fromisoformat(arm["selection_at"]) for arm in formations[key[0]])
        availability = {}
        for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
            reference = row[field]
            saved.require(reference in audit["availability_metadata"], "unresolved source metadata reference")
            metadata = audit["availability_metadata"][reference]
            _availability(metadata, date.fromisoformat(key[0]), cutoffs)
            availability[field] = reference
            used_metadata.add(reference)
        disposition, proof = row["disposition"], row["proof"]
        if not row["cap_pass"]:
            saved.require(disposition == "cap_below" and proof == {"capitalization": row["capitalization"]}
                          and key not in previous and key not in numeric, "cap exclusion partition mismatch")
            control = "cap_below"
        else:
            negative = _negative_type(key[1], row["literal_source_labels"])
            saved.require(negative == row["negative_type"], "literal type disposition mismatch")
            if negative:
                saved.require(disposition == "negative_type" and proof == {"literal_type": negative}
                              and key not in previous and key not in numeric, "negative control partition mismatch")
                control = "negative_type"
            else:
                saved.require(key in previous, "common source lacks BH disposition")
                prior_keys.add(key)
                if previous[key]["proven_numeric_failures"]:
                    saved.require(disposition == "bh_failure" and proof == previous[key]
                                  and key not in numeric, "BH exclusion mismatch")
                    control = "bh_failure"
                else:
                    saved.require(key in numeric, "BH unresolved source lacks BI metadata")
                    bi_keys.add(key)
                    current = numeric[key]
                    if current["proven_failure"]:
                        saved.require(disposition == "bi_failure"
                            and {name: proof[name] for name in BI_FIELDS} == {
                                name: current[name] for name in BI_FIELDS}, "BI partition proof metadata mismatch")
                    else:
                        saved.require(disposition == "normal_window" and proof == {"reason": current["reason"]},
                                      "normal partition mismatch")
                    control = "formation_frozen" if current["reason"] == "formation_frozen" else "numeric_possible"
                    if control == "numeric_possible":
                        saved.require(date.fromisoformat(row["listing_date"]).isoformat()
                                      <= current["first_required_observation"], "listing after required history")
                        possible.append({"formation": key[0], "code": key[1], "isin": row["isin"],
                            "activity_reason": current["reason"], "first_required_observation": current["first_required_observation"],
                            "normal_candidate_start": current["normal_candidate_start"],
                            "required_dates": current["consumed_dates"], "normal_dates": current["normal_dates"],
                            "frozen_dates": current["frozen_dates"], "formation_state": current["formation_state"],
                            "listing_date": row["listing_date"], "selection_at": current["selection_at"],
                            "operating_period": None, "operating_period_status": "unresolved",
                            "krx_metadata_provenance": deepcopy(row["krx_metadata_provenance"]),
                            "source_metadata_refs": availability})
        partitions.append({"formation": key[0], "code": key[1], "isin": row["isin"],
                           "prior_disposition": disposition, "control_disposition": control})
    saved.require(prior_keys == set(previous) and bi_keys == set(numeric)
                  and used_metadata == set(audit["availability_metadata"])
                  and len(possible) == expected_possible, "incomplete control/proof/metadata population")
    ranked = []
    for formation, arms in formations.items():
        candidates = [row for row in possible if row["formation"] == formation]
        for row in candidates:
            row["ordering_sha256"] = hashlib.sha256(
                f"{params['seed']}:{date.fromisoformat(formation)}:{row['code']}".encode()).hexdigest()
        candidates.sort(key=lambda row: (row["ordering_sha256"], row["code"]))
        for rank, row in enumerate(candidates, 1):
            row["rank"] = rank
        declared = {arm: targets[arm][formation] for arm in ("D1", "D2")}
        saved.require(all(set(codes) <= {row["code"] for row in candidates}
                          and len(codes) <= params["slots"] for codes in declared.values()),
                      "prior declared targets outside numeric possible pool")
        ranked.append({"formation": formation, "arms": arms, "ranked_numeric_possible": candidates,
            "activity_preopening_targets": declared,
            "activity_preopening_k": {arm: len(codes) for arm, codes in declared.items()}})
    return {"schema": SCHEMA, "status": "complete_control_rank_metadata_planned",
        "scope_kind": "complete_numeric_rank_metadata_only_not_eligible_control_or_holding_bound",
        "paired_schedule": schedule, "formations": ranked, "population_partition": partitions,
        "availability_metadata": deepcopy(audit["availability_metadata"]),
        "activity_snapshot_sha256": bi["snapshot_sha256"], "activity_row_provenance_reference": bi["row_provenance_reference"],
        "full_population_reference": {"input": "bl_population_audit", "sha256": INPUTS["bl_population_audit"]["sha256"],
            "pointer": "/rows", "lookup": "exact formation/code/ISIN", "rows": len(source)},
        "summary": {"source_rows": len(source), "prior_windows": len(previous), "BI_windows": len(numeric),
            "prior_disposition_counts": actual, "control_disposition_counts": dict(sorted(Counter(
                row["control_disposition"] for row in partitions).items())),
            "numeric_possible_windows": len(possible), "formations": len(ranked)},
        "ordering_policy": "sha256(seed:ISO-formation:code); full numeric pool before eligibility adjudication",
        "frontier_complete": False, "compulsory_successor_closure_complete": False,
        "control_targets_frozen": False, "potential_holding_origins_bound_complete": False,
        "remaining_obligations": ["positive operating-period adjudication without skipping earlier unknown candidates",
            "rerank and freeze control only after eligibility and compulsory-successor/action scope reconciliation"],
        "historical_eligibility_certified": False, "holding_selection_executed": False,
        "holding_lot_denominator": None, "action_lot_denominator": None,
        "returns_computed": False, "books_replayed": False, "database_access": False,
        "api_access": False, "new_source_requests": 0, "promotion_allowed": False,
        "D1_completed_studies": 0, "D2_completed_studies": 0}


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs) -> dict:
    def load(role):
        declaration = spec["inputs"][role]
        return inputs.json(Path(declaration["path"]), declaration["sha256"], role)
    manifest = load("calendar_manifest")
    calendar, _, schedule = saved.calendar_schedule(manifest, spec["parameters"])
    del manifest
    bh = load("bh_result")
    population = prior.population_snapshot(bh, schedule)
    del bh
    bi = load("bi_result")
    metadata = bi_metadata(population, bi, calendar, schedule, spec["parameters"])
    del bi
    roles = ("bl_result", "bl_verification", "paired_report", "paired_result", "paired_verification", "paired_replay")
    documents = {role: load(role) for role in roles}
    saved.require(documents["bl_result"]["activity_input"]["source_sha256"] == spec["inputs"]["bi_result"]["sha256"]
        and documents["bl_result"]["activity_input"]["upstream_dataset_sha256"] == metadata["snapshot_sha256"],
        "BL and control metadata use different BI snapshots")
    targets, summary = saved_targets(documents, schedule, {role: row["sha256"] for role, row in spec["inputs"].items()})
    del documents
    audit = load("bl_population_audit")
    return plan_control(audit, population, metadata, targets, summary, schedule, spec["parameters"])


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "wall_limit_seconds": 300}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH,
                  "run committed source from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    population_path = Path(spec["inputs"]["bl_population_audit"]["path"])
    def bounded_reader(path):
        return _read_population(path) if path == population_path else saved.read_private(path)
    inputs = saved.PinnedInputs(bounded_reader if reader is None else reader)
    def evaluate():
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_inputs(spec, inputs)
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            resources = saved.encoded(_resources(started))
            saved.write_exclusive(output / "resource-manifest.json", resources)
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(saved.encoded(inputs.manifest)))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return {"status": result["status"], "output_dir": str(output), "result_sha256": saved.digest(saved.encoded(result)),
                "summary": result["summary"], **{key: result[key] for key in ("source_manifest_sha256", "specification_sha256",
                    "resource_manifest_sha256", "input_manifest_sha256", "D1_completed_studies", "D2_completed_studies")}}
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed", "error_type": type(exc).__name__,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(exc.__traceback__)], "actual_inputs_attempted": inputs.manifest,
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)), "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0}))
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0, "mode": MODE}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    def timed_out(signum, frame):
        raise TimeoutError("registered wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(300)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded({"status": "failed", "error_type": type(exc).__name__, "automatic_retries": 0}).decode())
        return 1
    finally:
        signal.alarm(0)
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
