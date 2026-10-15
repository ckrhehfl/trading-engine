"""One logged BR normal/nominee-price/exit diagnostic, never a control replay.

The reviewed nominee union is an initial input frontier, not a CountedControl
prefix or a successor-closed holding bound. Every nominee must pass all shared
normal predicates before a frozen original-issue price scope is read. A failure
preserves its evidence and requires a separately registered scope, never a skip
or automatic substitute. Existing books, collectors and confirmation are unused.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import resource
import signal
import time
import traceback

from research import activity_control_scope_preflight as scope_source
from research import activity_normal_preflight as normal
from research import activity_preflight as saved
from research import activity_price_parity as prices
from research import experiment_log
from research.activity_control_normal_inputs import assemble_control_normal_inputs
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_failure_inputs import restore_activity_failure
from research.activity_failure_preflight import day, plan_windows
from research.activity_holding_inputs import adapt_holding_scan, holding_requirements
from research.activity_holding_preflight import SCHEMA as HOLDING_SCHEMA, _read_population
from research.activity_normal_sources import load_sources
from research.activity_partition_selection import PartitionSelection
from research.activity_quote_preflight import _audit
from research.activity_timing import SessionLagPolicy


SCHEMA = "activity-control-inputs-v1"
MODE = "discovery_control_nominee_input_diagnostic"
SOURCE_PATH = "python/research/activity_control_inputs_preflight.py"
FIXED_INPUTS = {role: declaration for role, declaration in normal.INPUTS.items() if role != "bk_adjudication"}
FIXED_INPUTS["bl_population_audit"] = scope_source.INPUTS["bl_population_audit"]
NEW_ROLES = ("br_scope_result", "br_scope_verification", "br_adjudication")
SCOPE_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json", "resource-manifest.json")
FALSE_FLAGS = ("complete_control_prefix_proved", "compulsory_successor_closure_complete", "control_targets_frozen",
    "potential_holding_origins_bound_complete", "holding_selection_executed", "books_replayed", "returns_computed",
    "actions_applied", "event_coverage_certified", "no_event_coverage_certified", "price_basis_certified",
    "historical_eligibility_certified", "historical_publication_certified", "source_truth_certified", "promotion_allowed")


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs"} and spec["schema"] == spec["study_id"] == SCHEMA
        and spec["mode"] == MODE and spec["promotion_allowed"] is False and spec["window"] == saved.WINDOW
        and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]), "fixed BR input specification mismatch")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(declared) == {*FIXED_INPUTS, *NEW_ROLES, "scan_database"}
        and all(declared[role] == value for role, value in FIXED_INPUTS.items()), "fixed upstream input pins mismatch")
    paths = [value.get("path", value.get("root")) for value in declared.values()]
    saved.require(all(type(path) is str and Path(path).is_absolute() and ".." not in Path(path).parts for path in paths)
        and len(paths) == len(set(paths)), "distinct exact absolute input paths required")
    for role in NEW_ROLES:
        saved.require(type(declared[role]) is dict and set(declared[role]) == {"path", "sha256"}, "exact new saved input pin required")
        saved._pin(declared[role]["sha256"])
    scan = declared["scan_database"]
    saved.require(type(scan) is dict and set(scan) == {"path", "mode", "columns"} and scan["mode"] == "ro"
        and scan["columns"] == list(prices.READ_COLUMNS), "exact read-only typed scan declaration required")


def validate_scope(scope: dict, receipt: dict, declared: dict, activity_sha: str) -> None:
    """Bind the independently verified metadata output without rereading its books."""
    pins = {role: row["sha256"] for role, row in scope_source.INPUTS.items()}
    outputs = receipt.get("output_sha256")
    saved.require(type(outputs) is dict and set(outputs) == set(SCOPE_FILES)
        and outputs["result.json"] == declared["br_scope_result"]["sha256"]
        and receipt.get("schema") == "br-control-scope-independent-verification-v1"
        and receipt.get("verified") is True
        and receipt.get("scope") == "new_scope_metadata_partitions_dates_ranks_targets_and_hash_links_only"
        and receipt.get("input_sha256") == pins and receipt.get("summary") == scope.get("summary"),
        "independent BR scope receipt/output links mismatch")
    for pin in outputs.values():
        saved._pin(pin)
    for field, filename in (("specification_sha256", "specification.json"), ("source_manifest_sha256", "source-manifest.json"),
                            ("input_manifest_sha256", "input-manifest.json"), ("resource_manifest_sha256", "resource-manifest.json")):
        saved.require(scope.get(field) == outputs[filename], "scope internal output links mismatch")
    saved.require(scope.get("schema") == scope_source.SCHEMA and scope.get("status") == "complete_control_rank_metadata_planned"
        and scope.get("activity_snapshot_sha256") == activity_sha
        and all(scope.get(name) is False for name in ("frontier_complete", "compulsory_successor_closure_complete",
            "control_targets_frozen", "potential_holding_origins_bound_complete", "holding_selection_executed", "returns_computed")),
        "scope metadata boundaries mismatch")
    saved.require(receipt.get("activity_preopening_k") == {
        row["formation"]: row["activity_preopening_k"] for row in scope["formations"]}, "scope target-count receipt mismatch")
    saved.require(all(receipt.get(name) is False for name in ("eligible_control_certified", "compulsory_successor_closure_complete",
        "event_coverage_certified", "price_basis_certified", "source_truth_certified", "books_replayed", "returns_computed", "promotion_allowed"))
        and all(type(receipt.get(name)) is int and receipt[name] == 0 for name in (
            "automatic_retries", "D1_completed_studies", "D2_completed_studies")), "scope receipt claim boundaries mismatch")


def _source_agreement(fresh: list[dict], population: dict) -> None:
    old = {(row["formation"], row["code"]): row for row in population["rows"]}
    saved.require(len(old) == len(population["rows"]) == len(fresh), "fresh complete source population count mismatch")
    seen = set()
    metadata_fields = {"capitalization_availability", "classification_availability", "trade_section_availability"}
    for row in fresh:
        key = row["formation"], row["code"]
        saved.require(key in old and key not in seen, "fresh source population key mismatch")
        seen.add(key)
        for field, value in row.items():
            expected = old[key].get(field)
            if field in metadata_fields:
                expected = population["availability_metadata"].get(expected)
            saved.require(value == expected, "fresh source population field/provenance mismatch")
    saved.require(seen == set(old), "fresh source population omitted a key")


def nominee_selections(audits: list[dict], scope: dict, calendar: tuple[date, ...], params: dict,
                       dispositions: list[dict]) -> tuple[dict, dict, dict]:
    """Project all reviewed nominees solely for conservative input/exit proofs."""
    rows = {(row["formation"].replace("-", ""), row["code"]): row for row in audits}
    saved.require(len(rows) == len(audits) and bool(rows), "unique nonempty reviewed nominee windows required")
    identities = {}
    for row in audits:
        code, isin = row["code"], row["isin"]
        saved.require(code not in identities or identities[code] == isin, "nominee code/ISIN reuse unsupported")
        identities[code] = isin
    arms, upper = {"D1": [], "D2": []}, {}
    end = calendar.index(date.fromisoformat(params["end"]))
    for formation in scope["formations"]:
        compact = formation["formation"]
        source_rows = [row for row in dispositions if row["formation"] == compact]
        codes = tuple(sorted(code for day, code in rows if day == compact))
        partition_sha = saved.digest(saved.encoded(source_rows))
        for lag in (1, 2):
            policy, index = SessionLagPolicy(lag, paired=True), calendar.index(day(compact))
            decision = policy.execution_on(calendar, index)
            arms[f"D{lag}"].append(PartitionSelection(day(compact), decision, policy.selection_at(calendar, index),
                codes, len(source_rows), partition_sha))
            for code in codes:
                upper.setdefault(code, set()).update(calendar[calendar.index(decision):end + 1])
    arms = {arm: tuple(values) for arm, values in arms.items()}
    upper = {code: [value.isoformat() for value in sorted(days)] for code, days in sorted(upper.items())}
    requested = {code: list(days) for code, days in holding_requirements(arms["D1"], arms["D2"], calendar, params, upper).items()}
    saved.require(requested == upper, "exact nominee entry-through-end union mismatch")
    return arms, requested, identities


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    declared = spec["inputs"]
    def load(role):
        row = declared[role]
        return inputs.json(Path(row["path"]), row["sha256"], role)
    values = {role: load(role) for role in ("calendar_manifest", "bb_result", "bh_result", "au_result", "bi_result",
        "br_scope_result", "br_scope_verification", "bl_population_audit", "br_adjudication")}
    calendar_strings, history, schedule = saved.calendar_schedule(values.pop("calendar_manifest"), spec["parameters"])
    del history
    calendar = tuple(day(value) for value in calendar_strings)
    scope, population, ledger = (values.pop(role) for role in ("br_scope_result", "bl_population_audit", "br_adjudication"))
    bi = values.pop("bi_result")
    activity_sha = bi["scan_snapshot"]["dataset_sha256"]
    validate_scope(scope, values.pop("br_scope_verification"), declared, activity_sha)
    saved.require(scope["paired_schedule"] == schedule, "scope/original paired calendar mismatch")
    bb_windows = saved._bb_windows(values.pop("bb_result"), calendar_strings)[0]
    bh = values.pop("bh_result")
    windows, requested_activity = plan_windows(bb_windows, bh["coverage"]["windows"],
        values.pop("au_result")["rows"], calendar_strings)
    previous = normal.prior.population_snapshot(bh, schedule)
    del values, bb_windows, bh
    numeric = scope_source.bi_metadata(previous, bi, calendar_strings, schedule, spec["parameters"])
    targets = {arm: {row["formation"]: row["activity_preopening_targets"][arm] for row in scope["formations"]} for arm in ("D1", "D2")}
    summary = {"source_rows": scope["summary"]["source_rows"], "disposition_counts": scope["summary"]["prior_disposition_counts"]}
    recomputed = scope_source.plan_control(population, previous, numeric, targets, summary, schedule, spec["parameters"])
    saved.require(all(saved.encoded(scope.get(key)) == saved.encoded(value) for key, value in recomputed.items()),
                  "recomputed complete BR rank/proof scope mismatch")
    del numeric, recomputed, previous, targets, summary
    records = ledger.get("records")
    saved.require(type(records) is list and bool(records), "nonempty BR positive adjudication records required")
    reviewed = {(date.fromisoformat(window["formation"]).strftime("%Y%m%d"), record["code"])
                for record in records for window in record["required_windows"]}
    normal.adjudication_records(ledger, reviewed)
    possible = {(row["formation"], row["code"]) for formation in scope["formations"] for row in formation["ranked_numeric_possible"]}
    saved.require(reviewed <= possible, "BR accepted nominees outside complete numeric possible pool")
    normal_scope = {"schema": SCHEMA + ":normal-read-scope-v1", "restored_activity_source": declared["bi_result"],
        "upstream_dataset_sha256": activity_sha, "typed_OHLC_fingerprint_recomputed": False,
        "requested_code_dates": {code: [value.isoformat() for value in dates] for code, dates in requested_activity.items()},
        "reviewed_nominee_windows": [list(key) for key in sorted(reviewed)], "numeric_possible_windows": len(possible),
        "complete_control_prefix_proved": False, "database_access": False, "returns_computed": False}
    saved.write_exclusive(output / "normal-read-scope.json", saved.encoded(normal_scope))
    del normal_scope
    restored = restore_activity_failure(bi["scan_snapshot"], calendar, requested_activity)
    proofs = normal.replay_proofs(windows, restored, bi, calendar, spec["parameters"])
    del bi, windows, requested_activity
    source = load_sources(declared, inputs, schedule, calendar, reviewed, output, expected_window_count=len(reviewed))
    _source_agreement(source["population"], population)
    del source["population"]
    screen_params = {**spec["parameters"], "threshold": Decimal(str(spec["parameters"]["threshold"]))}
    connected = assemble_control_normal_inputs(calendar, scope, population, records, restored.observations,
        source["potential_sources"], screen_params)
    del population, restored, records, ledger
    audits, dispositions, normal_summary = (connected[key] for key in ("normal_input_audit", "dispositions", "summary"))
    normal_raw = saved.encoded(audits)
    saved.write_exclusive(output / "normal-input-audit.json", normal_raw)
    saved.write_exclusive(output / "population-audit.json", saved.encoded(connected["population_audit"]))
    saved.write_exclusive(output / "control-dispositions.json", saved.encoded(dispositions))
    saved.write_exclusive(output / "restored-activity-proof-audit.json", saved.encoded({"upstream_dataset_sha256": activity_sha,
        "typed_OHLC_fingerprint_recomputed": False, "replayed_proofs": proofs}))
    failed = [{"formation": row["formation"], "code": row["code"], "control_arms": row["control_arms"]} for row in audits
              if any(arm["eligible"] is not True for arm in row["control_arms"].values())]
    if failed:
        saved.write_exclusive(output / "scope-refusal.json", saved.encoded({"new_scope_required": True,
            "failed_reviewed_windows": failed, "database_access": False, "automatic_retries": 0, "returns_computed": False}))
        raise ValueError("reviewed nominee failed shared normal predicates; new scope required before database access")
    arms, requested, identities = nominee_selections(audits, scope, calendar, spec["parameters"], dispositions)
    pins = {role: row["sha256"] for role, row in declared.items() if "sha256" in row}
    pins["normal_input_audit"] = saved.digest(normal_raw)
    read_scope = {"schema": HOLDING_SCHEMA + ":read-scope-v1", "producer_schema": SCHEMA,
        "scope_kind": "reviewed_nominee_original_issue_union_not_final_control_or_successor_closure",
        "calendar_dates": calendar_strings, "requested_code_dates": requested, "activity_snapshot_sha256": activity_sha,
        "scan_database": declared["scan_database"], "normal_input_audit_sha256": pins["normal_input_audit"],
        "input_sha256": pins, "input_manifest_before_database": deepcopy(inputs.manifest),
        "new_source_requests": 0, "automatic_retries": 0, "returns_computed": False}
    scope_raw = saved.encoded(read_scope)
    saved.write_exclusive(output / "read-scope.json", scope_raw)
    read_sha = saved.digest(scope_raw)
    del connected, source, proofs
    scan = prices.load_scan(Path(declared["scan_database"]["path"]),
        {"calendar_dates": calendar_strings, "intersection_code_dates": requested}, inputs, read_sha)
    quote_sha = scan["typed_snapshot_sha256"]
    quotes = adapt_holding_scan(scan, calendar, requested, quote_snapshot_sha256=quote_sha,
        read_scope_sha256=read_sha, activity_snapshot_sha256=activity_sha)
    audit = _audit(scan, quotes, requested, read_sha)
    audit_raw = saved.encoded(audit)
    saved.write_exclusive(output / "holding-input-audit.json", audit_raw)
    exit_scope = provisional_exit_bounds(arms["D1"], arms["D2"], quotes, spec["parameters"], requested, identities, input_sha256=pins)
    exit_raw = saved.encoded(exit_scope)
    saved.write_exclusive(output / "provisional-exit-scope.json", exit_raw)
    return {"schema": SCHEMA, "status": "reviewed_nominee_inputs_audited", "normal_summary": normal_summary,
        "holding_summary": audit["summary"], "provisional_exit_entries": len(exit_scope["entries"]),
        "activity_snapshot_sha256": activity_sha, "quote_snapshot_sha256": quote_sha, "read_scope_sha256": read_sha,
        "holding_input_audit_sha256": saved.digest(audit_raw), "provisional_exit_scope_sha256": saved.digest(exit_raw),
        "normal_input_audit_sha256": pins["normal_input_audit"], "input_sha256": pins, "input_manifest": inputs.manifest,
        "nominee_scope_meaning": read_scope["scope_kind"], "new_scope_required": False,
        "database_access": True, "api_access": False, "new_source_requests": 0, "automatic_retries": 0,
        "D1_completed_studies": 0, "D2_completed_studies": 0, **dict.fromkeys(FALSE_FLAGS, False)}


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "wall_limit_seconds": 900}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "run committed source from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive output already exists")
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
            result = evaluate_inputs(spec, inputs, output)
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
                **{key: result[key] for key in ("source_manifest_sha256", "specification_sha256", "resource_manifest_sha256",
                    "input_manifest_sha256", "D1_completed_studies", "D2_completed_studies")}}
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed", "error_type": type(exc).__name__,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(exc.__traceback__)], "actual_inputs_attempted": inputs.manifest,
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)), "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0}))
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
    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(900)
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
