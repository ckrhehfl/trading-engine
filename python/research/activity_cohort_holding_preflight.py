"""Logged BS original-issue quote union from independently verified normal inputs.

Build the complete empty-start cohort family before any price read. Keep every
possible original entry through evaluation end and make one existing bounded
read-only scan. This provides no actions, successor closure, raw LG/LX units,
books, returns, actual-input readiness or source/publication certificate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import time
import traceback

from research import activity_cohort_normal_preflight as normal
from research import activity_cohort_scope_preflight as frontier
from research import activity_preflight as saved
from research import activity_price_parity as prices
from research import experiment_log
from research.activity_cohort_connection import cohort_proof_selections
from research.activity_cohort_selection import CONTROL_IDS, build_cohort_selection
from research.activity_control_connection import ARMS, _keyed
from research.activity_control_normal_inputs import assemble_control_normal_inputs
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_holding_inputs import adapt_holding_scan, holding_requirements
from research.activity_holding_preflight import SCHEMA as HOLDING_SCHEMA, _read_population
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_partition_selection import _normal
from research.activity_quote_preflight import _audit
from research.activity_timing import SessionLagPolicy


SCHEMA = "activity-cohort-holding-inputs-v1"
MODE = "discovery_cohort_original_issue_holding_input_diagnostic"
SOURCE_PATH = "python/research/activity_cohort_holding_preflight.py"
NORMAL_SPEC_PATH = "configs/research/discovery/activity-cohort-normal-inputs-v1.json"
NORMAL_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json", "resource-manifest.json",
    "normal-read-scope.json", "raw-source-read-scope.json", "normal-input-delta.json", "normal-input-audit.json",
    "control-dispositions.json", "normal-exclusions.json", "restored-activity-proof-audit.json", "frontier.json")
NORMAL_ROLES = {"bs_normal_" + name.removesuffix(".json").replace("-", "_"): name for name in NORMAL_FILES}
FIXED_INPUTS = {role: deepcopy(normal.FIXED_INPUTS[role]) for role in (
    "calendar_manifest", "bi_result", "br_scope_result", "br_population_audit")}
NEW_ROLES = (*NORMAL_ROLES, "bs_normal_verification", "bs_initial_scope_result")
VERIFY_TRUE = ("normal_predicates_recomputed", "salted_full_population_frontier_recomputed",
    "outside_window_dispositions_and_audits_preserved", "original_ACTIVITY_targets_and_budgets_unchanged",
    "cached_response_manifest_links_verified")
VERIFY_FALSE = ("pending", "producer_imported", "raw_response_bodies_read", "source_truth_certified",
    "typed_OHLC_fingerprint_recomputed", "historical_publication_certified", "historical_vintage_certified",
    "report_types_inferred", "books_replayed", "returns_computed", "database_access", "api_access",
    "promotion_allowed", "original_books_or_metrics_inspected", "resource_enforcement_independently_verified")
FALSE_FLAGS = ("books_replayed", "returns_computed", "api_access", "normal_sources_reread", "normal_predicates_reexecuted",
    "actions_applied", "event_coverage_certified", "no_event_coverage_certified", "price_basis_certified",
    "compulsory_successor_closure_complete", "potential_holding_origins_bound_complete", "holding_selection_executed",
    "actual_input_reuse_readiness_established", "source_truth_certified", "historical_publication_certified",
    "historical_eligibility_certified", "promotion_allowed", *frontier.POWER_FLAGS)


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs", "controls", "design", *frontier.POWER_FLAGS}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and all(spec[name] is False for name in frontier.POWER_FLAGS)
        and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW)
        and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]) == saved.encoded(PARAMETERS)
        and spec["controls"] == list(CONTROL_IDS) and spec["design"] == frontier.DESIGN, "fixed BS holding-input specification required")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(declared) == {*FIXED_INPUTS, *NEW_ROLES, "scan_database"}
        and all(declared[role] == value for role, value in FIXED_INPUTS.items()), "exact BS holding-input roles and upstream pins required")
    paths = []
    for role, value in declared.items():
        expected = {"path", "mode", "columns"} if role == "scan_database" else {"path", "sha256"}
        saved.require(type(value) is dict and set(value) == expected and type(value["path"]) is str
            and Path(value["path"]).is_absolute() and ".." not in Path(value["path"]).parts, "exact absolute input declaration required")
        paths.append(str(saved._safe_path(Path(value["path"]))))
        if role == "scan_database":
            saved.require(value["mode"] == "ro" and value["columns"] == list(prices.READ_COLUMNS), "exact typed read-only database declaration required")
        else:
            saved._pin(value["sha256"])
    saved.require(len(paths) == len(set(paths)), "holding-input paths alias")
    saved.require(len({str(Path(declared[role]["path"]).parent) for role in NORMAL_ROLES}) == 1
        and all(Path(declared[role]["path"]).name == name for role, name in NORMAL_ROLES.items()), "thirteen normal files must name one exact package")


def validate_normal_package(data: dict, hashes: dict, receipt: dict, declared: dict, parameters: dict) -> None:
    """Consume an independently verified package without reopening its inputs."""
    saved.require(set(data) == set(hashes) == set(NORMAL_FILES), "complete normal output package required")
    result, spec, sources = (data[name] for name in NORMAL_FILES[:3])
    saved.require(type(receipt) is dict and receipt.get("schema") == "bs-cohort-normal-independent-verification-v1"
        and receipt.get("status") == "verified" and receipt.get("verified") is True
        and receipt.get("independent_output_verification") == "verified" and receipt.get("file_sha256") == hashes
        and all(receipt.get(name) is True for name in VERIFY_TRUE) and all(receipt.get(name) is False for name in VERIFY_FALSE)
        and all(type(receipt.get(name)) is int and receipt[name] == 0 for name in (
            "D1_completed_studies", "D2_completed_studies", "automatic_retries", "performance_studies_added")),
        "independent BS normal receipt and exact output pins required")
    for pin in hashes.values(): saved._pin(pin)
    saved.require(type(receipt.get("expected_commit")) is str and re.fullmatch(r"[0-9a-f]{40}", receipt["expected_commit"])
        and receipt.get("producer_code_version") == receipt["expected_commit"] == sources.get("code_version"), "normal producer commit mismatch")
    saved._pin(receipt.get("producer_sha256"))
    rows = sources.get("files")
    saved.require(type(rows) is list and len({row["path"] for row in rows}) == len(rows)
        and any(row["path"] == normal.SOURCE_PATH and row["sha256"] == receipt["producer_sha256"] for row in rows)
        and any(row["path"] == NORMAL_SPEC_PATH and row["sha256"] == hashes["specification.json"] for row in rows),
        "normal frozen producer/specification source links required")
    saved.require(spec.get("schema") == spec.get("study_id") == normal.SCHEMA and spec.get("mode") == normal.MODE
        and saved.encoded(spec.get("parameters")) == saved.encoded(parameters) == saved.encoded(PARAMETERS)
        and saved.encoded(spec.get("window")) == saved.encoded(saved.WINDOW)
        and spec.get("reference_specification") == saved.REFERENCE and spec.get("controls") == list(CONTROL_IDS)
        and spec.get("design") == frontier.DESIGN and spec.get("promotion_allowed") is False
        and all(spec.get(name) is False for name in frontier.POWER_FLAGS), "normal fixed specification boundary mismatch")
    upstream = spec.get("inputs")
    saved.require(type(upstream) is dict and set(upstream) == {*normal.FIXED_INPUTS, *normal.NEW_ROLES}
        and all(upstream[role] == value for role, value in normal.FIXED_INPUTS.items()), "normal original source declarations changed")
    expected_pins = {}
    for role, value in upstream.items():
        if "path" in value:
            saved.require(set(value) == {"path", "sha256"}, "exact normal saved input declaration required")
            expected_pins[role] = value["sha256"]
        else:
            for part in ("report", "started", "ledger"):
                expected_pins[role + ":" + part] = value[part + "_sha256"]
    for value in expected_pins.values(): saved._pin(value)
    saved.require(receipt.get("input_sha256") == expected_pins
        and result.get("input_sha256") == {role: value["sha256"] for role, value in upstream.items() if "path" in value},
        "normal complete saved/cache receipt pin links mismatch")
    saved.require(all(upstream[role] == declared[role] for role in FIXED_INPUTS)
        and upstream["bs_scope_result"] == declared["bs_initial_scope_result"], "holding upstream context differs from verified normal inputs")
    saved.require(result.get("schema") == normal.SCHEMA and result.get("status") == "cohort_normal_inputs_and_frontier_assembled"
        and result.get("independent_output_verification") == "pending" and receipt.get("summary") == result.get("summary")
        and all(result.get(name) is False for name in normal.FALSE_FLAGS)
        and all(type(result.get(name)) is int and result[name] == 0 for name in (
            "new_source_requests", "automatic_retries", "D1_completed_studies", "D2_completed_studies"))
        and result.get("typed_OHLC_fingerprint_recomputed") is False
        and result.get("classification_evidence_level") == "inferred"
        and result.get("historical_publication_evidence_level") == "assumed", "normal completion/claim boundary mismatch")
    for filename, field in (("specification.json", "specification_sha256"), ("source-manifest.json", "source_manifest_sha256"),
        ("input-manifest.json", "input_manifest_sha256"), ("resource-manifest.json", "resource_manifest_sha256"),
        ("normal-read-scope.json", "normal_read_scope_sha256"), ("raw-source-read-scope.json", "raw_source_read_scope_sha256")):
        saved.require(result.get(field) == hashes[filename], "normal package internal hash link mismatch")
    saved.require(result.get("artifact_sha256") == {name: hashes[name] for name in NORMAL_FILES[7:]}, "normal exact artifact hash links mismatch")
    for name in NORMAL_FILES:
        if name != "specification.json":
            saved.require(saved.digest(saved.encoded(data[name])) == hashes[name], "normal canonical output body/pin mismatch")


def plan_holding_inputs(calendar: tuple[date, ...], original_scope: dict, population: dict, initial_scope: dict,
                        dispositions: list[dict], normal_audits: list[dict], updated: dict,
                        exclusions: list[dict], parameters: dict) -> dict:
    """Prove all original/control prefixes, then compute the full paired union."""
    saved.require(type(parameters) is dict and saved.encoded(parameters) == saved.encoded(PARAMETERS), "unchanged canonical cohort parameters required")
    # Whole-population identity, source availability, numeric pool and timing
    # validation only. No normal cache, predicate recomputation or old book.
    connected = assemble_control_normal_inputs(calendar, original_scope, population, [], {}, {},
        {**parameters, "threshold": Decimal(str(parameters["threshold"]))})
    previous = _keyed(connected["dispositions"])
    partition, audited, source = _keyed(dispositions), _keyed(normal_audits, iso=True), _keyed(population["rows"])
    saved.require(partition.keys() == previous.keys() == source.keys() and list(partition) == sorted(partition)
        and audited.keys() <= partition.keys(), "ordered complete normal source partition required")
    allowed = {"cap_below", "negative_type", "bh_failure", "formation_frozen"}
    for key, row in partition.items():
        saved.require(all(row[name] == previous[key][name] for name in
            ("formation", "code", "isin", "prior_disposition", "control_disposition")), "normal full source identity changed")
        state, reasons = row["normal_input_disposition"], row["normal_input_exclusion_reasons"]
        if key in audited:
            audit = audited[key]; arms = audit.get("control_arms")
            saved.require(row["control_disposition"] == "numeric_possible" and audit["isin"] == row["isin"]
                and type(arms) is dict and set(arms) == set(ARMS)
                and all(type(arms[arm].get("eligible")) is bool and type(arms[arm].get("exclusion_reasons")) is list for arm in ARMS),
                "normal reviewed numeric audit required")
            left, right = arms["D1"], arms["D2"]
            saved.require((left["eligible"], left["exclusion_reasons"]) == (right["eligible"], right["exclusion_reasons"])
                and left["eligible"] == (not left["exclusion_reasons"])
                and state == ("eligible" if left["eligible"] else "excluded") and reasons == left["exclusion_reasons"], "normal audit/disposition mismatch")
            index = calendar.index(key[0])
            cutoffs = tuple(SessionLagPolicy(lag, paired=True).selection_at(calendar, index) for lag in (1, 2))
            _normal(audit["activity_screen"], source[key], calendar, cutoffs)
            for arm, lag in (("D1", 1), ("D2", 2)):
                policy = SessionLagPolicy(lag, paired=True)
                expected_reasons = [reason for reason in audit["activity_screen"]["arms"][arm]["exclusion_reasons"]
                    if reason != "activity_predicate_failed"]
                saved.require(arms[arm].get("selection_at") == policy.selection_at(calendar, index).isoformat()
                    and arms[arm].get("decision_on") == policy.execution_on(calendar, index).isoformat()
                    and arms[arm]["exclusion_reasons"] == expected_reasons
                    and arms[arm]["eligible"] == (not expected_reasons), "control normal timing or activity-only predicate removal mismatch")
        elif row["control_disposition"] == "numeric_possible":
            saved.require(state == "unresolved" and reasons is None, "unaudited numeric row must remain unresolved")
        else:
            saved.require(row["control_disposition"] in allowed and state == "excluded" and reasons == [row["control_disposition"]],
                "non-activity exclusion proof changed")
    del connected, previous
    delta = {"dispositions": dispositions, "normal_input_audit": normal_audits, "new_normal_exclusions": exclusions}
    recomputed = normal.updated_frontier(original_scope, population, initial_scope, delta, parameters)
    saved.require(saved.encoded(updated) == saved.encoded(recomputed) and updated["complete_target_prefix"] is True
        and updated["frontier_complete"] is True and updated["summary"]["unknown_prefix_windows"] == 0
        and updated["summary"]["unresolved_normal_windows"] == 0, "complete independently bound recomputed cohort prefix required before database")
    originals = normal.activity_declarations(initial_scope, population, dispositions, parameters, require_original_partition=False)
    declarations = []
    for arm in ARMS:
        for original in originals[arm]:
            rows = tuple((code, "unknown" if row["normal_input_disposition"] == "unresolved" else row["normal_input_disposition"],
                saved.digest(saved.encoded({"disposition": row, "normal_input_audit": audited.get((formation, code))})))
                for (formation, code), row in partition.items() if formation == original.formation_on)
            values = {key: value for key, value in vars(original).items() if key not in ("group_id", "resolved_prefix")}
            for group in ("ACTIVITY", *CONTROL_IDS):
                declarations.append(build_cohort_selection(group_id=group, dispositions=rows, **values))
    declarations = tuple(declarations)
    proofs = cohort_proof_selections(declarations, calendar, parameters)
    end = calendar.index(date.fromisoformat(parameters["end"]))
    upper, identities = {}, {}
    for row in declarations:
        population_isins = dict(row.source_population)
        for code in row.chosen_codes:
            isin = population_isins[code]
            saved.require(code not in identities or identities[code] == isin, "cohort original issue-code reuse unsupported")
            identities[code] = isin
    for selections in proofs.values():
        for row in selections:
            for code in row.eligible_codes:
                upper.setdefault(code, set()).update(calendar[calendar.index(row.decision_on):end + 1])
    upper = {code: [day.isoformat() for day in sorted(days)] for code, days in sorted(upper.items())}
    requested = {code: list(days) for code, days in holding_requirements(proofs["D1"], proofs["D2"], calendar, parameters, upper).items()}
    saved.require(requested == upper and set(identities) == set(requested), "exact full cohort original entry-through-end union required")
    return {"declarations": declarations, "proof_selections": proofs, "requested_code_dates": requested,
        "issue_isins": dict(sorted(identities.items())), "frontier": recomputed}


def _empty_audit(quotes, requested, scope_sha):
    return {"schema": HOLDING_SCHEMA + ":holding-input-audit-v1", "read_scope_sha256": scope_sha,
        "activity_snapshot_sha256": quotes.activity_snapshot_sha256, "quote_snapshot_sha256": None,
        "snapshot_received_at": None, "fingerprint": None, "scan_metadata": None, "requested_code_dates": requested,
        "absent_coordinates": [], "unrequested_coordinates": [], "rows": [],
        "summary": {"requested_codes": 0, "requested_code_dates": 0, "absent_requested_code_dates": 0,
            "unrequested_code_dates": 0, "state_counts": {}}}


def _write_audit(path: Path, audit: dict) -> str:
    """Write the same canonical bytes without retaining a second complete JSON."""
    digest, pending = hashlib.sha256(), bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        def flush():
            if pending:
                stream.write(pending)
                digest.update(pending)
                pending.clear()
        for token in encoder.iterencode(audit):
            pending.extend(token.encode("utf-8"))
            if len(pending) >= 64 * 1024:
                flush()
        pending.extend(b"\n")
        flush()
        stream.flush()
        os.fsync(stream.fileno())
    saved._sync_directory(path.parent)
    return digest.hexdigest()


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path, *, scan_loader=None) -> dict:
    declared, data, hashes = spec["inputs"], {}, {}
    def load(role):
        row = declared[role]
        return inputs.json(Path(row["path"]), row["sha256"], role)
    for role, name in NORMAL_ROLES.items():
        data[name], hashes[name] = load(role), declared[role]["sha256"]
    receipt = load("bs_normal_verification")
    validate_normal_package(data, hashes, receipt, declared, spec["parameters"])
    # Every original normal body has been hash/canonical/receipt checked. Keep
    # only the planning bodies and small lineage fields before decoding the
    # full historical population; BI's bulky scan provenance is not a planner
    # input and must not overlap that decode peak.
    lineage = (data["result.json"]["activity_snapshot_sha256"], data["frontier.json"]["activity_snapshot_sha256"],
        data["normal-read-scope.json"]["upstream_dataset_sha256"],
        data["normal-read-scope.json"]["restored_activity_source"],
        data["restored-activity-proof-audit.json"]["upstream_dataset_sha256"])
    data = {name: data[name] for name in ("control-dispositions.json", "normal-input-audit.json", "frontier.json", "normal-exclusions.json")}
    del receipt
    calendar_strings, _, schedule = saved.calendar_schedule(load("calendar_manifest"), spec["parameters"])
    calendar = tuple(date.fromisoformat(value) for value in calendar_strings)
    bi = load("bi_result")
    activity_sha = bi["scan_snapshot"]["dataset_sha256"]
    saved._pin(activity_sha)
    del bi
    original_scope, population, initial = (load(role) for role in ("br_scope_result", "br_population_audit", "bs_initial_scope_result"))
    saved.require(original_scope["paired_schedule"] == schedule
        and original_scope["activity_snapshot_sha256"] == initial["activity_snapshot_sha256"]
        == lineage[0] == lineage[1] == activity_sha and lineage[2] == lineage[4] == activity_sha
        and lineage[3] == declared["bi_result"],
        "unchanged original calendar/activity lineage required")
    del lineage
    prepared = plan_holding_inputs(calendar, original_scope, population, initial, data["control-dispositions.json"],
        data["normal-input-audit.json"], data["frontier.json"], data["normal-exclusions.json"], spec["parameters"])
    requested, proofs = prepared["requested_code_dates"], prepared["proof_selections"]
    declarations_raw = saved.encoded([_json_value(asdict(row)) for row in prepared["declarations"]])
    proof_raw = saved.encoded({arm: [_json_value(asdict(row)) for row in values] for arm, values in proofs.items()})
    saved.write_exclusive(output / "cohort-declarations.json", declarations_raw)
    saved.write_exclusive(output / "proof-selections.json", proof_raw)
    pins = {role: row["sha256"] for role, row in declared.items() if "sha256" in row}
    scope = {"schema": HOLDING_SCHEMA + ":read-scope-v1", "producer_schema": SCHEMA,
        "scope_kind": "complete_BS_family_proof_only_original_issue_union_not_successor_closure",
        "calendar_dates": calendar_strings, "requested_code_dates": requested, "issue_isins": prepared["issue_isins"],
        "activity_snapshot_sha256": activity_sha, "scan_database": declared["scan_database"], "input_sha256": pins,
        "normal_file_sha256": hashes, "normal_verification_sha256": declared["bs_normal_verification"]["sha256"],
        "cohort_declarations_sha256": saved.digest(declarations_raw), "proof_selections_sha256": saved.digest(proof_raw),
        "input_manifest_before_database": deepcopy(inputs.manifest), "new_source_requests": 0,
        "automatic_retries": 0, "returns_computed": False}
    scope_raw = saved.encoded(scope); scope_sha = saved.digest(scope_raw)
    saved.write_exclusive(output / "read-scope.json", scope_raw)
    summary = {"cohort_declarations": len(prepared["declarations"]), "formation_count": len(proofs["D1"]),
        "groups": ["ACTIVITY", *CONTROL_IDS], "union_original_codes": len(requested),
        "union_potential_origins": sum(len(row.eligible_codes) for values in proofs.values() for row in values),
        "cohort_chosen_windows": sum(len(row.chosen_codes) for row in prepared["declarations"]),
        "assigned_slots_by_arm": {arm: sum(row.target_ceiling for row in prepared["declarations"] if row.arm == arm) for arm in ARMS}}
    del data, original_scope, population, initial, prepared, declarations_raw, proof_raw
    loader = prices.load_scan if scan_loader is None else scan_loader
    scan = loader(Path(declared["scan_database"]["path"]),
        {"calendar_dates": calendar_strings, "intersection_code_dates": requested}, inputs, scope_sha) if requested else None
    quote_sha = None if scan is None else scan["typed_snapshot_sha256"]
    quotes = adapt_holding_scan(scan, calendar, requested, quote_snapshot_sha256=quote_sha,
        read_scope_sha256=scope_sha, activity_snapshot_sha256=activity_sha)
    # The immutable adapter now retains every typed row and provenance receipt.
    # _audit uses that sidecar; release the reader's duplicate row dictionaries.
    if scan is not None:
        del scan["rows"]
    audit = _empty_audit(quotes, requested, scope_sha) if scan is None else _audit(scan, quotes, requested, scope_sha)
    audit_sha = _write_audit(output / "holding-input-audit.json", audit)
    holding_summary = audit["summary"]
    database_access = scan is not None
    del scan, audit
    exit_scope = provisional_exit_bounds(proofs["D1"], proofs["D2"], quotes, spec["parameters"], requested,
        scope["issue_isins"], input_sha256=pins)
    exit_raw = saved.encoded(exit_scope)
    saved.write_exclusive(output / "provisional-exit-scope.json", exit_raw)
    return {"schema": SCHEMA, "status": "cohort_original_issue_inputs_audited", "summary": summary,
        "holding_summary": holding_summary, "provisional_exit_entries": len(exit_scope["entries"]),
        "activity_snapshot_sha256": activity_sha, "quote_snapshot_sha256": quote_sha, "read_scope_sha256": scope_sha,
        "cohort_declarations_sha256": scope["cohort_declarations_sha256"], "proof_selections_sha256": scope["proof_selections_sha256"],
        "holding_input_audit_sha256": audit_sha, "provisional_exit_scope_sha256": saved.digest(exit_raw),
        "input_sha256": pins, "normal_file_sha256": hashes, "control_targets_frozen": True,
        "complete_control_prefix_proved": True, "original_quote_requests_preserved": True,
        "database_access": database_access, "new_source_requests": 0, "automatic_retries": 0,
        "D1_completed_studies": 0, "D2_completed_studies": 0, **dict.fromkeys(FALSE_FLAGS, False)}


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "wall_limit_seconds": 900,
        "nice": os.getpriority(os.PRIO_PROCESS, 0)}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None, freeze=None, evaluator=None) -> dict:
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "run committed BS holding source from root")
    freezer = saved.freeze_sources if freeze is None else freeze
    evaluate_saved = evaluate_inputs if evaluator is None else evaluator
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive BS holding output already exists")
    population_path = Path(spec["inputs"]["br_population_audit"]["path"])
    def bounded_reader(path): return _read_population(path) if path == population_path else saved.read_private(path)
    inputs = saved.PinnedInputs(bounded_reader if reader is None else reader)
    def evaluate():
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent); output.mkdir(mode=0o700); created = True; saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw); saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_saved(spec, inputs, output)
            after, after_raw, _ = freezer(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            resources, manifest = saved.encoded(_resources(started)), saved.encoded(inputs.manifest)
            saved.write_exclusive(output / "resource-manifest.json", resources); saved.write_exclusive(output / "input-manifest.json", manifest)
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(manifest))
            body = saved.encoded(result); saved.write_exclusive(output / "result.json", body)
            return {"status": result["status"], "output_dir": str(output), "result_sha256": saved.digest(body),
                "summary": result["summary"], **{key: result[key] for key in ("source_manifest_sha256", "specification_sha256",
                    "resource_manifest_sha256", "input_manifest_sha256", "D1_completed_studies", "D2_completed_studies")}}
        except BaseException as error:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists(): saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed", "error_type": type(error).__name__,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(error.__traceback__)], "actual_inputs_attempted": inputs.manifest,
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)), "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0,
                    **dict.fromkeys(FALSE_FLAGS, False)}))
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw), window=spec["window"], runs_path=runs,
            parameters={**spec["parameters"], "source_manifest": sources, "declared_inputs": spec["inputs"], "output_dir": str(output),
                "mode": MODE, "design": frontier.DESIGN, "controls": list(CONTROL_IDS), "automatic_retries": 0,
                **dict.fromkeys(frontier.POWER_FLAGS, False)}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True); args = parser.parse_args(argv)
    def expired(signum, frame): raise TimeoutError("registered BS holding wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2,) * 2); resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    signal.signal(signal.SIGALRM, expired); signal.alarm(900)
    try:
        priority = os.getpriority(os.PRIO_PROCESS, 0)
        saved.require(priority <= 10 and os.nice(10 - priority) == 10, "exact nice10 required")
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as error:
        print(saved.encoded({"status": "failed", "error_type": type(error).__name__, "automatic_retries": 0}).decode()); return 1
    finally:
        signal.alarm(0)
    print(saved.encoded(record["result"]).decode()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
