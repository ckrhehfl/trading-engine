"""One logged fixed BR CONTROL D1/D2 comparison over pinned saved evidence.

Restore the original ACTIVITY pair, never execute it again. Reuse the BR
counted-prefix/nominee binding and the reviewed ordinary replay path. Source
content, independent new-output verification and actual-run authorization remain
separate. No database, external API or source-body loader is used here.
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import resource
import signal
import time
import traceback

from research import activity_control_inputs_preflight as diagnostic
from research import activity_control_report as reporting
from research import activity_control_replay as control
from research import activity_holding_preflight as holding
from research import activity_pair_calibration as calibration
from research import activity_paired_preflight as paired
from research import activity_preflight as saved
from research import experiment_log
from research.activity_control_connection import (
    ARMS, ControlBoundDeclaration, bind_control_holding_scope, counted_selections_from_br,
)
from research.activity_timing_report import paired_timing_report


SCHEMA = "activity-control-comparison-v1"
MODE = "discovery_descriptive_same_universe_control"
SOURCE_PATH = "python/research/activity_control_preflight.py"
BR_FILES = {
    "br_result": "result.json", "br_population_audit": "population-audit.json",
    "br_control_dispositions": "control-dispositions.json", "br_normal_input_audit": "normal-input-audit.json",
    "br_read_scope": "read-scope.json", "br_holding_input_audit": "holding-input-audit.json",
    "br_provisional_exit_scope": "provisional-exit-scope.json",
}
BR_OUTPUTS = (*diagnostic.SCOPE_FILES, "normal-read-scope.json", "raw-source-read-scope.json",
    "normal-input-audit.json", "population-audit.json", "control-dispositions.json",
    "restored-activity-proof-audit.json", "read-scope.json", "holding-input-audit.json", "provisional-exit-scope.json")
ROLES = ("br_scope_result", "br_result", "br_verification", "br_population_audit",
    "br_control_dispositions", "br_normal_input_audit", "br_read_scope", "br_holding_input_audit",
    "br_provisional_exit_scope", "control_bounds", "reviewed_action_coverage", "basis_evidence",
    "original_activity_report", "original_activity_replay", "original_activity_specification",
    "original_activity_verification")
DEFERRED_ROLES = ("br_holding_input_audit", "original_activity_report", "original_activity_replay",
    "original_activity_specification", "original_activity_verification")
POWER_FLAGS = ("comparison_power_prerequisite_met", "power_design_passed")


def validate_spec(spec: dict, reference: dict) -> None:
    """The exploratory exception fixes the failed power flags, not new policy."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed",
        "window", "reference_specification", "parameters", "inputs", *POWER_FLAGS}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and all(spec[key] is False for key in POWER_FLAGS)
        and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW)
        and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])
        == saved.encoded(calibration.PARAMETERS), "fixed descriptive CONTROL specification mismatch")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(declared) == set(ROLES), "exact sixteen saved CONTROL roles required")
    paths = []
    for row in declared.values():
        saved.require(type(row) is dict and set(row) == {"path", "sha256"}
            and type(row["path"]) is str and Path(row["path"]).is_absolute()
            and ".." not in Path(row["path"]).parts, "exact absolute pinned input declaration required")
        saved._pin(row["sha256"])
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "saved CONTROL input paths must be distinct")
    for files in (BR_FILES, {"original_activity_report": "report.json", "original_activity_replay": "paired-replay.json",
                           "original_activity_specification": "specification.json"}):
        saved.require(len({str(Path(declared[role]["path"]).parent) for role in files}) == 1
            and all(Path(declared[role]["path"]).name == name for role, name in files.items()),
            "saved package filenames and directory must remain unchanged")


def _zero(value: dict, names: tuple[str, ...]) -> None:
    saved.require(all(type(value.get(key)) is int and value[key] == 0 for key in names), "zero prior execution/request counts required")


def _verify_br(documents: dict, pins: dict) -> dict:
    """Consume the actual fourteen-output independent receipt, not a BL alias."""
    result, receipt, scope = (documents[key] for key in ("br_result", "br_verification", "br_scope_result"))
    outputs = receipt.get("output_sha256")
    saved.require(receipt.get("schema") == "br-control-inputs-independent-verification-v1"
        and receipt.get("verified") is True
        and receipt.get("scope") == "new_BR_nominee_normal_population_typed_holding_and_provisional_exit_consistency_only"
        and type(outputs) is dict and set(outputs) == set(BR_OUTPUTS), "independent fourteen-output BR receipt required")
    for pin in outputs.values():
        saved._pin(pin)
    saved.require(all(outputs[name] == pins[role] for role, name in BR_FILES.items()), "BR consumed output pins mismatch")
    saved.require(result.get("schema") == diagnostic.SCHEMA and result.get("status") == "reviewed_nominee_inputs_audited"
        and result.get("new_scope_required") is False and result.get("database_access") is True
        and result.get("api_access") is False and all(result.get(key) is False for key in diagnostic.FALSE_FLAGS),
        "unchanged noncertifying BR diagnostic required")
    for key, filename in (("specification_sha256", "specification.json"), ("source_manifest_sha256", "source-manifest.json"),
            ("input_manifest_sha256", "input-manifest.json"), ("resource_manifest_sha256", "resource-manifest.json"),
            ("normal_input_audit_sha256", "normal-input-audit.json"), ("read_scope_sha256", "read-scope.json"),
            ("holding_input_audit_sha256", "holding-input-audit.json"), ("provisional_exit_scope_sha256", "provisional-exit-scope.json")):
        saved.require(result.get(key) == outputs[filename], "BR internal output link mismatch")
    evidence, producer_pins = receipt.get("evidence_sha256"), result.get("input_sha256")
    saved.require(type(evidence) is dict and set(evidence) == {"br_scope_result", "br_scope_verification", "br_adjudication", "bi_result"}
        and type(producer_pins) is dict and bool(producer_pins)
        and all(producer_pins.get(role) == pin for role, pin in evidence.items())
        and evidence["br_scope_result"] == pins["br_scope_result"]
        and producer_pins.get("bl_population_audit") == pins["br_population_audit"]
        and producer_pins.get("normal_input_audit") == pins["br_normal_input_audit"], "BR original evidence/population/input links mismatch")
    for pin in producer_pins.values():
        saved._pin(pin)
    saved.require(scope.get("schema") == "activity-control-scope-v1"
        and scope.get("status") == "complete_control_rank_metadata_planned"
        and all(scope.get(key) is False for key in ("frontier_complete", "compulsory_successor_closure_complete",
            "control_targets_frozen", "potential_holding_origins_bound_complete", "holding_selection_executed", "returns_computed")),
        "original BR rank scope required")
    for key in ("activity_snapshot_sha256", "quote_snapshot_sha256", "read_scope_sha256"):
        saved._pin(result.get(key))
        saved.require(result[key] == receipt.get(key), "BR independent snapshot/read-scope linkage mismatch")
    saved.require(scope.get("activity_snapshot_sha256") == result["activity_snapshot_sha256"]
        and documents["br_read_scope"].get("input_sha256") == producer_pins
        and documents["br_read_scope"].get("normal_input_audit_sha256") == pins["br_normal_input_audit"]
        and result.get("holding_summary") == receipt.get("holding_summary")
        and result.get("normal_summary") == receipt.get("normal_summary")
        and type(result.get("provisional_exit_entries")) is int
        and result["provisional_exit_entries"] == receipt.get("provisional_exit_entries")
        == len(documents["br_provisional_exit_scope"].get("entries", ())), "BR audit summaries and preserved scope mismatch")
    _zero(result, ("automatic_retries", "new_source_requests", "D1_completed_studies", "D2_completed_studies"))
    _zero(receipt, ("automatic_retries", "D1_completed_studies", "D2_completed_studies"))
    saved.require(receipt.get("original_input_files_read") == 4 and receipt.get("new_output_files_read") == 14
        and all(receipt.get(key) is False for key in ("database_access", "api_access", "source_truth_certified",
            "price_basis_certified", "raw_source_bodies_opened", "source_body_truth_verified", "original_database_transaction_verified",
            "historical_eligibility_certified", "historical_publication_certified", "event_coverage_certified",
            "no_event_coverage_certified", "complete_control_prefix_proved", "compulsory_successor_closure_complete",
            "books_replayed", "returns_computed", "promotion_allowed")), "BR independent receipt claim boundaries mismatch")
    return producer_pins


def _verify_original(documents: dict, pins: dict, calendar: tuple[date, ...], activity_sha: str) -> None:
    """Restore/check saved books and headline metrics before any CONTROL book."""
    report, replay, spec, receipt = (documents[role] for role in (
        "original_activity_report", "original_activity_replay", "original_activity_specification", "original_activity_verification"))
    paired.validate_spec(spec, {"parameters": calibration.PARAMETERS})
    saved.require(receipt.get("schema") == "bq-raw-paired-independent-verification-v1"
        and receipt.get("verified") is True
        and receipt.get("scope") == "new_seven_outputs_links_and_five_saved_book_headline_metrics_only"
        and all(receipt.get(key) is True for key in calibration.VERIFY_TRUE)
        and all(receipt.get(key) is False for key in (*calibration.FALSE_FLAGS, *calibration.VERIFY_FALSE)),
        "original limited BQ independent verification required")
    files = receipt.get("file_sha256")
    saved.require(type(files) is dict and set(files) == {"result.json", *calibration.LINK_FILES.values()}
        and all(files[name] == pins[role] for role, name in (
            ("original_activity_report", "report.json"), ("original_activity_replay", "paired-replay.json"),
            ("original_activity_specification", "specification.json")))
        and receipt.get("specification_sha256") == files["specification.json"], "original BQ report/replay/spec pins mismatch")
    for pin in files.values():
        saved._pin(pin)
    upstream = {role: row["sha256"] for role, row in spec["inputs"].items()}
    saved.require(receipt.get("input_sha256") == replay.get("input_sha256") == upstream
        and upstream["bl_population_audit"] == pins["br_population_audit"]
        and receipt.get("activity_snapshot_sha256") == replay.get("activity_snapshot_sha256") == activity_sha,
        "original ACTIVITY input/population/activity snapshot mismatch")
    for key in calibration.SNAPSHOTS:
        saved._pin(receipt.get(key))
        saved.require(receipt[key] == replay.get(key), "original BQ snapshot link mismatch")
    _zero(receipt, ("D1_completed_studies", "D2_completed_studies", "automatic_retries", "original_input_files_read"))
    saved.require(all(type(receipt.get(key)) is int and receipt[key] == count for key, count in (
        ("published_D1_completed_studies", 1), ("published_D2_completed_studies", 1),
        ("output_files_read", 7), ("publication_files_read", 2)))
        and replay.get("schema") == "activity-paired-replay-v1" and replay.get("status") == "pure_paired_replay"
        and replay.get("initial_nav") == "1" and replay.get("actual_study_completed") is False
        and all(replay.get(key) is False for key in calibration.FALSE_FLAGS)
        and saved.encoded(replay.get("report")) == saved.encoded(report), "unchanged original saved ACTIVITY pair required")
    activities, partitions = {}, {}
    expected_days = calendar[calibration.PARAMETERS["lookback"] + 1:calendar.index(date.fromisoformat(calibration.PARAMETERS["end"])) + 1]
    saved.require(type(replay.get("replays")) is dict and type(replay.get("selections")) is dict
        and set(replay["replays"]) == set(replay["selections"]) == set(ARMS), "both saved ACTIVITY arms required")
    for arm in ARMS:
        activities[arm] = reporting._restore_replay(replay["replays"][arm])
        partitions[arm] = tuple(reporting._restore_selection(row) for row in replay["selections"][arm])
        saved.require(tuple(book.as_of for book in activities[arm].books) == expected_days,
            "complete original ACTIVITY session calendar required before CONTROL books")
        calibration._arm(report["arms"][arm], receipt["metrics"]["arms"][arm], Decimal(1), saved.WINDOW)
    reporting._compare_report(report, paired_timing_report(activities["D1"], activities["D2"],
        partitions["D1"], partitions["D2"], initial_nav=Decimal(1)))


def _bounds(value: dict, parameters: dict, calendar_sha: str, coverage_sha: str) -> dict:
    saved.require(type(value) is dict and set(value) == {"schema", "parameters", "calendar_sha256",
        "reviewed_action_coverage_sha256", "arms"} and value["schema"] == "br-control-bound-declarations-v1"
        and saved.encoded(value["parameters"]) == saved.encoded(parameters)
        and value["calendar_sha256"] == calendar_sha and value["reviewed_action_coverage_sha256"] == coverage_sha
        and type(value["arms"]) is dict and set(value["arms"]) == set(ARMS), "explicit pinned CONTROL bound declarations required")
    result = {}
    for arm in ARMS:
        saved.require(type(value["arms"][arm]) is list, "ordered CONTROL bound records required")
        records = []
        for row in value["arms"][arm]:
            saved.require(type(row) is dict and set(row) == {"formation_on", "prior_potential_codes", "successor_codes", "evidence_sha256"}
                and type(row["formation_on"]) is str and row["evidence_sha256"] == coverage_sha,
                "CONTROL closure evidence must bind reviewed coverage")
            formation = date.fromisoformat(row["formation_on"])
            saved.require(formation.isoformat() == row["formation_on"], "canonical CONTROL bound formation required")
            for key in ("prior_potential_codes", "successor_codes"):
                codes = row[key]
                saved.require(type(codes) is list and all(type(code) is str and code and code == code.strip() for code in codes)
                    and codes == sorted(set(codes)), "unique sorted explicit CONTROL bound codes required")
            saved.require(not row["successor_codes"], "new event preparation required: ordinary CONTROL path has no successors")
            records.append(ControlBoundDeclaration(formation, frozenset(row["prior_potential_codes"]), frozenset(), coverage_sha))
        result[arm] = tuple(records)
    return result


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    pins = {role: spec["inputs"][role]["sha256"] for role in ROLES}
    def load(role):
        return inputs.json(Path(spec["inputs"][role]["path"]), pins[role], role)
    # Keep bulky holding/original books out of the population JSON decode peak.
    documents = {role: load(role) for role in ROLES if role not in ("br_population_audit", *DEFERRED_ROLES)}
    producer_pins = _verify_br(documents, pins)
    scope, read_scope = documents["br_scope_result"], documents["br_read_scope"]
    calendar = tuple(date.fromisoformat(day) for day in read_scope["calendar_dates"])
    declarations = _bounds(documents.pop("control_bounds"), spec["parameters"],
        documents["br_provisional_exit_scope"]["calendar_sha256"], pins["reviewed_action_coverage"])
    population = load("br_population_audit")
    selections = counted_selections_from_br(calendar, spec["parameters"], scope, population,
        documents["br_control_dispositions"], documents["br_normal_input_audit"], declarations)
    del population, declarations
    documents.update({role: load(role) for role in DEFERRED_ROLES})
    saved.require(documents["br_holding_input_audit"].get("summary") == documents["br_result"].get("holding_summary"),
        "BR holding audit summary mismatch")
    _verify_original(documents, pins, calendar, documents["br_result"]["activity_snapshot_sha256"])
    result_input = documents["br_result"]
    bound = bind_control_holding_scope(selections, calendar=calendar, parameters=spec["parameters"], scope=scope,
        dispositions=documents["br_control_dispositions"], normal_audits=documents["br_normal_input_audit"],
        holding_audit=documents.pop("br_holding_input_audit"), read_scope=read_scope,
        quote_snapshot_sha256=result_input["quote_snapshot_sha256"], activity_snapshot_sha256=result_input["activity_snapshot_sha256"],
        read_scope_sha256=pins["br_read_scope"], provisional_exit_scope=documents.pop("br_provisional_exit_scope"),
        producer_input_sha256=producer_pins)
    original_report, original_replay, coverage, evidence = (documents.pop(role) for role in (
        "original_activity_report", "original_activity_replay", "reviewed_action_coverage", "basis_evidence"))
    del documents, scope, read_scope, producer_pins, result_input, selections
    replay = control.replay_bound_control(bound, coverage, evidence, original_report, original_replay, parameters=spec["parameters"])
    del bound, coverage, evidence, original_report, original_replay
    replay_raw, report_raw = saved.encoded(replay), saved.encoded(replay["report"])
    saved.write_exclusive(output / "control-replay.json", replay_raw)
    saved.write_exclusive(output / "comparison-report.json", report_raw)
    return {"schema": SCHEMA, "status": "descriptive_control_pair_executed",
        "control_replay_sha256": saved.digest(replay_raw), "comparison_report_sha256": saved.digest(report_raw),
        "input_sha256": pins, "input_manifest": inputs.manifest,
        **{key: replay[key] for key in ("activity_snapshot_sha256", "quote_snapshot_sha256", "read_scope_sha256", "action_scope_sha256")},
        "books_computed": True, "returns_computed": True, "original_ACTIVITY_replayed": False,
        "independent_output_verification": "pending", "promotion_allowed": False,
        **dict.fromkeys(POWER_FLAGS, False), "source_truth_certified": False, "event_coverage_certified": False,
        "price_basis_certified": False, "historical_vintage_certified": False, "full_economic_accounting_verified": False,
        "database_access": False, "api_access": False, "original_source_bodies_read": False,
        "new_source_requests": 0, "automatic_retries": 0}


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "wall_limit_seconds": 900}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """Preserve one started/completed-or-failed trial, with no automatic retry."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH,
        "run committed CONTROL source from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive CONTROL output already exists")
    population_path = Path(spec["inputs"]["br_population_audit"]["path"])
    def bounded_reader(path):
        return holding._read_population(path) if path == population_path else saved.read_private(path)
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
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(saved.encoded(inputs.manifest)),
                logged_paired_execution_completed=True, D1_completed_studies=1, D2_completed_studies=1)
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return {"status": result["status"], "output_dir": str(output), "result_sha256": saved.digest(saved.encoded(result)),
                **{key: result[key] for key in ("control_replay_sha256", "comparison_report_sha256", "resource_manifest_sha256",
                    "source_manifest_sha256", "specification_sha256", "input_manifest_sha256", "logged_paired_execution_completed",
                    "independent_output_verification", "D1_completed_studies", "D2_completed_studies")}}
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed", "error_type": type(exc).__name__,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(exc.__traceback__)], "actual_inputs_attempted": inputs.manifest,
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)), "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "paired_execution_may_be_partial": True,
                    "logged_paired_execution_completed": False, "original_ACTIVITY_replayed": False,
                    "promotion_allowed": False, **dict.fromkeys(POWER_FLAGS, False),
                    "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0}))
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "mode": MODE, "automatic_retries": 0,
                "session_lags": {"D1": 1, "D2": 2}, "paired": True, "initial_nav": "1", "initial_lots": [],
                "initial_as_of_rule": "restored BR calendar[lookback]", "original_ACTIVITY_replayed": False,
                **dict.fromkeys(POWER_FLAGS, False)}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    def timed_out(signum, frame):
        raise TimeoutError("registered CONTROL wall limit exceeded")
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
