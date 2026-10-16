"""Log the BS input boundary, then stream the existing independently logged cohorts.

An actual specification and its evidence pins must be registered separately.
This caller restores only verified saved holding artifacts and explicitly pinned
extensions/actions. It never rereads normal inputs, source populations, prices,
databases or APIs, and supplies no new accounting or selection engine.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
import os
from pathlib import Path
import re
import resource
import signal
import stat
import time
import traceback

from research import activity_cohort_execution as execution
from research import activity_cohort_holding_preflight as holding
from research import activity_cohort_scope_preflight as frontier
from research import activity_preflight as saved
from research import experiment_log
from research.activity_cohort_connection import cohort_proof_selections, prepare_cohort_scope
from research.activity_cohort_return_policy import (SCHEMA as RETURN_POLICY_SCHEMA, SCHEMA_V2 as RETURN_POLICY_SCHEMA_V2,
    MODE as RETURN_MODE, declared_policy_report, decode_return_policy, policy_report)
from research.activity_cohort_selection import CohortSelection, CONTROL_IDS
from research.activity_holding_restore import restore_holding_inputs
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_price_parity import READ_COLUMNS
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_quote_view import RawEpisodeQuoteInputs, restore_raw_quote_view


SCHEMA = "activity-cohort-preflight-v1"
SOURCE_PATH = "python/research/activity_cohort_preflight.py"
MODE = "discovery_cohort_input_orchestration"
OPERATIONAL_COMMIT = "4ce85d714890b87f1a57ae89d4942660e41c0483"
HOLDING_ROLES = {"holding_result": "result.json", "cohort_declarations": "cohort-declarations.json",
    "holding_read_scope": "read-scope.json", "holding_input_audit": "holding-input-audit.json",
    "ordinary_exit_scope": "provisional-exit-scope.json"}
HOLDING_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json", "resource-manifest.json",
    "cohort-declarations.json", "proof-selections.json", "read-scope.json", "holding-input-audit.json", "provisional-exit-scope.json")
HOLDING_INPUT_ROLES = (*holding.NORMAL_ROLES, "bs_normal_verification", *holding.FIXED_INPUTS, "bs_initial_scope_result")
BASE_ROLES = (*HOLDING_ROLES, "holding_verification", "reviewed_action_coverage", "basis_evidence")
RAW_ROLES = ("component_exit_scope", "quote_extension", "raw_episode_quotes", "raw_episode_unit_plan")
OPTIONAL_ROLES = (*RAW_ROLES, "return_policy")
VERIFY_TRUE = ("verified", "full_family_declarations_recomputed", "salted_full_population_frontier_recomputed",
    "proof_only_full_family_union_recomputed", "typed_rows_missingness_and_provenance_recomputed",
    "provisional_first_tradable_opening_bounds_recomputed", "original_quote_requests_preserved")
VERIFY_FALSE = ("producer_imported", "source_truth_certified", "database_transaction_verified", "historical_vintage_certified",
    "historical_publication_certified", "historical_eligibility_certified", "price_basis_certified", "no_event_coverage_certified",
    "event_coverage_certified", "compulsory_successor_closure_complete", "potential_holding_origins_bound_complete",
    "actual_input_reuse_readiness_established", "actual_exits_certified", "books_replayed", "returns_computed", "database_access",
    "api_access", "normal_predicates_reexecuted", "actions_applied", "holding_selection_executed",
    "resource_enforcement_independently_verified", "original_source_inputs_opened", "promotion_allowed",
    "comparison_power_prerequisite_met", "power_design_passed")
VERIFY_EXTRA_FALSE = ("pending", "raw_response_bodies_read", "original_source_bodies_read", "original_ACTIVITY_replayed",
    "original_books_or_metrics_inspected")
VERIFY_ZERO = ("D1_completed_studies", "D2_completed_studies", "automatic_retries", "performance_studies_added")
VERIFICATION_FIELDS = {"schema", "status", "file_sha256", "input_sha256", "expected_commit", "producer_code_version",
    "producer_sha256", "dependency_sha256", "summary", "holding_summary", "provisional_exit_entries",
    "activity_snapshot_sha256", "quote_snapshot_sha256", *VERIFY_TRUE, *VERIFY_FALSE, *VERIFY_ZERO,
    "helper_sha256", "io_sha256", "verifier_sha256", "protocol_sha256", "specification_sha256", "expected_normal_commit",
    "expected_normal_producer_sha256", "new_output_files_read", "saved_input_files_read", "completed_reads", "checkouts",
    "independent_output_verification", *VERIFY_EXTRA_FALSE}
IDENTITY_FIELDS = {"expected_commit", "producer_sha256", "specification_sha256", "expected_normal_commit",
    "expected_normal_producer_sha256", "helper_sha256", "io_sha256", "verifier_sha256", "protocol_sha256",
    "dependency_sha256", "file_sha256", "input_sha256", "collector_commit"}
FALSE_FLAGS = ("source_truth_certified", "event_coverage_certified", "price_basis_certified", "historical_publication_certified",
    "historical_vintage_certified", "historical_eligibility_certified", "database_access", "api_access",
    "normal_sources_reread", "original_source_bodies_read", "promotion_allowed", *frontier.POWER_FLAGS)


def _same(left, right, message):
    saved.require(saved.encoded(left) == saved.encoded(right), message)


def _commit(value):
    saved.require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value), "explicit reviewed source commit required")


def _declared_policy_report(spec):
    """Label the registered convention before reads, including failed attempts."""
    if "return_policy" not in spec["inputs"]:
        return {}
    return declared_policy_report(spec.get("return_policy_schema", RETURN_POLICY_SCHEMA),
        spec["inputs"]["return_policy"]["sha256"])


def validate_contract(spec: dict, reference: dict) -> None:
    """Validate a generic registered contract, without creating actual pins."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "window", "reference_specification",
        "parameters", "controls", "design", "inputs", "holding_verification_identity", "promotion_allowed", *frontier.POWER_FLAGS}
        | ({"return_policy_schema"} if "return_policy_schema" in spec else set())
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] in (MODE, RETURN_MODE)
        and spec["promotion_allowed"] is False and all(spec[name] is False for name in frontier.POWER_FLAGS)
        and spec["controls"] == list(CONTROL_IDS) and spec["design"] == frontier.DESIGN
        and spec["reference_specification"] == saved.REFERENCE, "fixed cohort orchestration contract required")
    _same(spec["parameters"], PARAMETERS, "canonical cohort parameters required")
    _same(spec["parameters"], reference["parameters"], "reference parameter representations changed")
    _same(spec["window"], saved.WINDOW, "spent discovery window required")
    declared, identity = spec["inputs"], spec["holding_verification_identity"]
    saved.require(type(declared) is dict and set(BASE_ROLES) <= set(declared)
        and set(declared) <= set(BASE_ROLES + OPTIONAL_ROLES), "exact saved cohort input roles required")
    saved.require(("return_policy" in declared) == (spec["mode"] == RETURN_MODE),
        "explicit approved price-return mode and policy must be supplied together")
    saved.require("return_policy_schema" not in spec or ("return_policy" in declared
        and spec["return_policy_schema"] == RETURN_POLICY_SCHEMA_V2), "explicit v2 policy registration required")
    saved.require(not {"raw_episode_quotes", "raw_episode_unit_plan"} & set(declared)
        or set(RAW_ROLES) <= set(declared), "raw cohort inputs require both artifacts, extension and component proof")
    paths = []
    for row in declared.values():
        saved.require(type(row) is dict and set(row) == {"path", "sha256"} and type(row["path"]) is str
            and Path(row["path"]).is_absolute() and ".." not in Path(row["path"]).parts, "absolute pinned saved input required")
        saved._pin(row["sha256"])
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "input aliases or duplicate reads refused")
    saved.require(len({str(Path(declared[role]["path"]).parent) for role in HOLDING_ROLES}) == 1
        and all(Path(declared[role]["path"]).name == name for role, name in HOLDING_ROLES.items()), "one exact selected holding package required")
    saved.require(type(identity) is dict and set(identity) == IDENTITY_FIELDS, "explicit holding producer/verifier identity required")
    for key in ("expected_commit", "expected_normal_commit", "collector_commit"):
        _commit(identity[key])
    saved.require(identity["collector_commit"] == OPERATIONAL_COMMIT, "unchanged operational collector identity required")
    for key in IDENTITY_FIELDS - {"expected_commit", "expected_normal_commit", "collector_commit", "dependency_sha256", "file_sha256", "input_sha256"}:
        saved._pin(identity[key])
    for key, names in (("file_sha256", HOLDING_FILES), ("input_sha256", HOLDING_INPUT_ROLES), ("dependency_sha256", ("normal", "holding"))):
        saved.require(type(identity[key]) is dict and set(identity[key]) == set(names), "exact holding verification pin membership required")
        for value in identity[key].values(): saved._pin(value)
    saved.require(identity["file_sha256"]["specification.json"] == identity["specification_sha256"]
        and all(declared[role]["sha256"] == identity["file_sha256"][name] for role, name in HOLDING_ROLES.items()),
        "selected holding byte pins differ from frozen complete package")


validate_spec = validate_contract


def _declarations(value):
    saved.require(type(value) is list and bool(value), "complete saved cohort declarations required")
    names, result = {field.name for field in fields(CohortSelection)}, []
    for raw in value:
        saved.require(type(raw) is dict and set(raw) == names, "exact original cohort fields required")
        row = dict(raw)
        for key in ("formation_on", "decision_on"):
            saved.require(type(row[key]) is str, "canonical declared date required")
            row[key] = date.fromisoformat(row[key])
        saved.require(type(row["selection_at"]) is str, "declared selection timestamp required")
        row["selection_at"] = datetime.fromisoformat(row["selection_at"])
        for key in ("activity_targets", "source_population", "resolved_prefix"):
            saved.require(type(row[key]) is list, "original immutable selection lists required")
            row[key] = tuple(tuple(part) if type(part) is list else part for part in row[key])
        restored = CohortSelection(**row)
        _same(_json_value(asdict(restored)), raw, "noncanonical original cohort declaration")
        result.append(restored)
    return tuple(result)


def _receipt(value, identity):
    saved.require(type(value) is dict and set(value) == VERIFICATION_FIELDS
        and value["schema"] == "bs-cohort-holding-independent-verification-v1"
        and value["status"] == value["independent_output_verification"] == "verified",
        "final independent holding wrapper receipt required")
    saved.require(all(value[name] is True for name in VERIFY_TRUE)
        and all(value[name] is False for name in (*VERIFY_FALSE, *VERIFY_EXTRA_FALSE))
        and all(type(value[name]) is int and value[name] == 0 for name in VERIFY_ZERO), "holding receipt claim boundaries changed")
    for key in IDENTITY_FIELDS - {"collector_commit"}:
        _same(value[key], identity[key], "holding receipt producer/verifier/input identity changed")
    saved.require(value["producer_code_version"] == identity["expected_commit"], "holding producer commit changed")
    for key, expected in (("new_output_files_read", len(HOLDING_FILES)), ("saved_input_files_read", len(HOLDING_INPUT_ROLES)),
                          ("completed_reads", len(HOLDING_FILES) + len(HOLDING_INPUT_ROLES))):
        saved.require(type(value[key]) is int and value[key] == expected, "holding independent read count changed")
    _same(value["checkouts"], {"research": {"head": identity["expected_commit"], "clean": True},
        "collector": {"head": identity["collector_commit"], "clean": True}}, "holding independent checkout identity changed")


def _family(result, scope, receipt, declarations, ordinary, identity):
    saved.require(type(result) is dict and result.get("schema") == holding.SCHEMA
        and result.get("status") == "cohort_original_issue_inputs_audited"
        and all(result.get(name) is False for name in holding.FALSE_FLAGS)
        and all(result.get(name) is True for name in ("control_targets_frozen", "complete_control_prefix_proved", "original_quote_requests_preserved"))
        and all(type(result.get(name)) is int and result[name] == 0 for name in ("new_source_requests", "automatic_retries", "D1_completed_studies", "D2_completed_studies")),
        "verified non-performance holding result required")
    links = {"specification.json": "specification_sha256", "source-manifest.json": "source_manifest_sha256",
        "input-manifest.json": "input_manifest_sha256", "resource-manifest.json": "resource_manifest_sha256",
        "cohort-declarations.json": "cohort_declarations_sha256", "proof-selections.json": "proof_selections_sha256",
        "read-scope.json": "read_scope_sha256", "holding-input-audit.json": "holding_input_audit_sha256", "provisional-exit-scope.json": "provisional_exit_scope_sha256"}
    saved.require(all(result.get(field) == identity["file_sha256"][name] for name, field in links.items()), "holding result output links changed")
    saved.require(type(scope) is dict and scope.get("producer_schema") == holding.SCHEMA
        and scope.get("scope_kind") == "complete_BS_family_proof_only_original_issue_union_not_successor_closure"
        and scope.get("returns_computed") is False
        and all(type(scope.get(name)) is int and scope[name] == 0 for name in ("new_source_requests", "automatic_retries")), "original full holding read scope required")
    for value in (result, scope, ordinary):
        _same(value.get("input_sha256"), identity["input_sha256"], "original nineteen-input lineage changed")
        saved.require(value.get("activity_snapshot_sha256") == receipt["activity_snapshot_sha256"], "activity lineage changed")
    normal_pins = {name: identity["input_sha256"][role] for role, name in holding.NORMAL_ROLES.items()}
    for value in (result, scope):
        _same(value.get("normal_file_sha256"), normal_pins, "verified normal output lineage changed")
    saved.require(scope.get("normal_verification_sha256") == identity["input_sha256"]["bs_normal_verification"]
        and scope.get("cohort_declarations_sha256") == result["cohort_declarations_sha256"]
        and scope.get("proof_selections_sha256") == result["proof_selections_sha256"], "normal receipt/declaration links changed")
    before = scope.get("input_manifest_before_database")
    saved.require(type(before) is list and [row.get("role") for row in before] == list(HOLDING_INPUT_ROLES), "nineteen ordered saved input receipts required")
    for row in before:
        saved.require(type(row) is dict and set(row) == {"role", "path", "expected_sha256", "read_status", "bytes", "sha256"}
            and row["expected_sha256"] == row["sha256"] == identity["input_sha256"][row["role"]]
            and row["read_status"] == "hash_verified" and type(row["bytes"]) is int and row["bytes"] > 0,
            "holding saved-input receipt changed")
    scan = scope.get("scan_database")
    saved.require(type(scan) is dict and set(scan) == {"path", "mode", "columns"} and scan["mode"] == "ro"
        and scan["columns"] == list(READ_COLUMNS), "declared-only original typed scan required")
    dates = scope.get("calendar_dates")
    saved.require(type(dates) is list and bool(dates), "complete original calendar required")
    calendar = tuple(date.fromisoformat(day) for day in dates)
    saved.require([day.strftime("%Y%m%d") for day in calendar] == dates and calendar == tuple(sorted(set(calendar)))
        and calendar[0] >= date.fromisoformat(saved.WINDOW["start"]), "canonical spent holding calendar required")
    proofs = cohort_proof_selections(declarations, calendar, PARAMETERS)
    proof_value = {arm: [_json_value(asdict(row)) for row in values] for arm, values in proofs.items()}
    saved.require(saved.digest(saved.encoded(proof_value)) == identity["file_sha256"]["proof-selections.json"], "complete paired proof union changed")
    requested, isins = {}, {}
    end = calendar.index(date.fromisoformat(PARAMETERS["end"]))
    for row in declarations:
        population = dict(row.source_population)
        for code in row.chosen_codes:
            saved.require(code not in isins or isins[code] == population[code], "original issue-code reuse refused")
            isins[code] = population[code]
    for values in proofs.values():
        for row in values:
            for code in row.eligible_codes:
                requested.setdefault(code, set()).update(calendar[calendar.index(row.decision_on):end + 1])
    requested = {code: [str(day) for day in sorted(days)] for code, days in sorted(requested.items())}
    _same(scope.get("requested_code_dates"), requested, "complete original holding coordinate union changed")
    _same(scope.get("issue_isins"), dict(sorted(isins.items())), "complete original issue identities changed")
    summary = {"cohort_declarations": len(declarations), "formation_count": len(proofs["D1"]), "groups": list(execution.comparison.GROUPS),
        "union_original_codes": len(requested), "union_potential_origins": sum(len(row.eligible_codes) for values in proofs.values() for row in values),
        "cohort_chosen_windows": sum(len(row.chosen_codes) for row in declarations),
        "assigned_slots_by_arm": {arm: sum(row.target_ceiling for row in declarations if row.arm == arm) for arm in execution.comparison.ARMS}}
    for value in (result, receipt):
        _same(value.get("summary"), summary, "holding full family summary changed")
        saved.require(type(value.get("provisional_exit_entries")) is int
            and value["provisional_exit_entries"] == summary["union_potential_origins"], "holding potential origin count changed")
    saved.require(result.get("database_access") is bool(requested) and result.get("quote_snapshot_sha256") == receipt["quote_snapshot_sha256"]
        == ordinary.get("quote_snapshot_sha256"), "holding diagnostic/snapshot identity changed")
    return calendar


@dataclass(frozen=True)
class PreparedCohorts:
    declarations: tuple
    original: object
    quotes: object
    binding: object


def prepare_inputs(spec: dict, inputs: saved.PinnedInputs) -> PreparedCohorts:
    """Consume selected verified saved outputs once; prepare all proofs before books."""
    validate_contract(spec, {"parameters": PARAMETERS})
    declared, identity = spec["inputs"], spec["holding_verification_identity"]
    def load(role):
        row = declared[role]
        return inputs.read(Path(row["path"]), row["sha256"], role)
    receipt = saved.strict_json(load("holding_verification"))
    _receipt(receipt, identity)
    result = saved.strict_json(load("holding_result"))
    declarations = _declarations(saved.strict_json(load("cohort_declarations")))
    scope = saved.strict_json(load("holding_read_scope"))
    ordinary_raw = load("ordinary_exit_scope")
    ordinary = saved.strict_json(ordinary_raw)
    _family(result, scope, receipt, declarations, ordinary, identity)
    del ordinary
    audit = saved.strict_json(load("holding_input_audit"))
    _same(audit.get("summary"), result.get("holding_summary"), "holding audit result summary changed")
    _same(audit.get("summary"), receipt.get("holding_summary"), "independent typed summary changed")
    original = restore_holding_inputs(audit, scope, quote_snapshot_sha256=receipt["quote_snapshot_sha256"],
        activity_snapshot_sha256=receipt["activity_snapshot_sha256"], read_scope_sha256=declared["holding_read_scope"]["sha256"])
    del audit, result, receipt
    quotes = original
    if "quote_extension" in declared:
        envelope = saved.strict_json(load("quote_extension"))
        saved.require(type(envelope) is dict and set(envelope) == {"schema", "original_quote_snapshot_sha256", "read_scope", "holding_input_audit"}
            and envelope["schema"] == "activity-quote-extension-envelope-v1"
            and envelope["original_quote_snapshot_sha256"] == original.quote_snapshot_sha256, "coherent original quote extension envelope required")
        extra_audit, extra_scope = envelope["holding_input_audit"], envelope["read_scope"]
        component = restore_holding_inputs(extra_audit, extra_scope, quote_snapshot_sha256=extra_audit.get("quote_snapshot_sha256"),
            activity_snapshot_sha256=original.activity_snapshot_sha256, read_scope_sha256=saved.digest(saved.encoded(extra_scope)))
        quotes = extend_holding_quotes(original, component)
        del envelope, extra_audit, extra_scope, component
    if "raw_episode_quotes" in declared:
        raw, unit = load("raw_episode_quotes"), load("raw_episode_unit_plan")
        quotes = restore_raw_quote_view(quotes, raw, declared["raw_episode_quotes"]["sha256"], unit, declared["raw_episode_unit_plan"]["sha256"])
        del raw, unit
    coverage, evidence = load("reviewed_action_coverage"), load("basis_evidence")
    component_raw = load("component_exit_scope") if "component_exit_scope" in declared else None
    pins = {"ordinary_exit_scope": declared["ordinary_exit_scope"]["sha256"],
        "reviewed_action_coverage": declared["reviewed_action_coverage"]["sha256"], "basis_evidence": declared["basis_evidence"]["sha256"]}
    if component_raw is not None: pins["component_exit_scope"] = declared["component_exit_scope"]["sha256"]
    return_policy = load("return_policy") if "return_policy" in declared else None
    if return_policy is not None:
        saved.require(decode_return_policy(return_policy)["schema"] == spec.get("return_policy_schema", RETURN_POLICY_SCHEMA),
            "loaded return policy differs from registered version")
        pins["return_policy"] = declared["return_policy"]["sha256"]
    binding = prepare_cohort_scope(declarations, original, quotes, ordinary_raw, coverage, evidence,
        parameters=spec["parameters"], original_required_code_dates=scope["requested_code_dates"], issue_isins=scope["issue_isins"],
        input_sha256=identity["input_sha256"], expected_pins=pins, component_exit_scope=component_raw,
        return_policy_json=return_policy)
    return PreparedCohorts(declarations, original, quotes, binding)


def evaluate_inputs(spec, inputs, output, *, runs_path, source_manifest_sha256, specification_sha256, family_runner=None):
    prepared = prepare_inputs(spec, inputs)
    saved.write_exclusive(output / "input-manifest-before-cohorts.json", saved.encoded(inputs.manifest))
    runner = execution.run_cohort_family if family_runner is None else family_runner
    raw = prepared.quotes if type(prepared.quotes) is RawEpisodeQuoteInputs else None
    result = runner(prepared.declarations, prepared.quotes.base if raw is not None else prepared.quotes, prepared.binding.events,
        parameters=spec["parameters"], scope_binding=prepared.binding, study_id=spec["study_id"],
        source_manifest_sha256=source_manifest_sha256, specification_sha256=specification_sha256,
        runs_path=runs_path, output_dir=output / "cohorts", raw_quote_view=raw)
    return {"schema": SCHEMA, "status": "logged_cohort_family_executed", "cohort_report_sha256": result["report_sha256"],
        "cohort_completion_sha256": result["completion_sha256"], "cohort_plan_sha256": result["cohort_plan_sha256"],
        "cohort_evaluation_counts": result["completed_cohort_studies_by_arm"], "D1_completed_studies": result["D1_completed_studies"],
        "D2_completed_studies": result["D2_completed_studies"], "conditional_scope_binding_sha256": prepared.binding.binding_sha256,
        "activity_snapshot_sha256": prepared.original.activity_snapshot_sha256, "quote_snapshot_sha256": prepared.quotes.quote_snapshot_sha256,
        "input_sha256": {role: row["sha256"] for role, row in spec["inputs"].items()}, "independent_output_verification": "pending",
        "books_replayed": True, "returns_computed": True,
        "outer_diagnostic_performance_studies_added": 0, "automatic_retries": 0, **dict.fromkeys(FALSE_FLAGS, False),
        **policy_report(prepared.binding.return_policy_json)}


def _read_large(path: Path) -> bytes:
    """The selected typed audit/extension may exceed the shared 64 MiB ceiling."""
    path = saved._safe_path(path)
    saved.private_directory(path.parent)
    limit = 256 * 1024**2
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        saved.require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
            and info.st_size <= limit, "bounded single-link private typed holding artifact required")
        raw = stream.read(limit + 1)
        saved.require(len(raw) <= limit, "typed holding artifact grew beyond bound")
        return raw


def _resources(started):
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)), "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)),
        "wall_limit_seconds": 1800, "nice": os.getpriority(os.PRIO_PROCESS, 0)}


def _completed_counts(output, result):
    if result is not None:
        return result["cohort_evaluation_counts"]
    failure = output / "cohorts" / "failure.json"
    if failure.exists():
        try:
            value = saved.strict_json(saved.read_private(failure)).get("completed_cohort_studies_by_arm")
            saved.require(type(value) is dict and set(value) == set(execution.comparison.ARMS)
                and all(type(number) is int and number >= 0 for number in value.values()), "preserved canonical cohort counts required")
            return value
        except (OSError, ValueError, TypeError, AttributeError):
            # A partial child failure artifact is retained. Never replace the
            # originating exception or manufacture zero canonical completions.
            return None
    return None if (output / "cohorts").exists() else dict.fromkeys(execution.comparison.ARMS, 0)


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None, freeze=None, evaluator=None):
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "run committed cohort caller from root")
    freezer = saved.freeze_sources if freeze is None else freeze
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_contract)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive cohort output already exists")
    large = {Path(spec["inputs"][role]["path"]) for role in ("holding_input_audit", "quote_extension", "raw_episode_quotes") if role in spec["inputs"]}
    def bounded_reader(path): return _read_large(path) if path in large else saved.read_private(path)
    inputs = saved.PinnedInputs(bounded_reader if reader is None else reader)
    evaluate_saved = evaluate_inputs if evaluator is None else evaluator
    source_pin, spec_pin = saved.digest(saved.encoded(sources)), saved.digest(raw)
    policy_metadata = _declared_policy_report(spec)
    def evaluate():
        created, result, started = False, None, time.monotonic()
        try:
            saved.private_directory(output.parent); output.mkdir(mode=0o700); created = True; saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_saved(spec, inputs, output, runs_path=runs, source_manifest_sha256=source_pin, specification_sha256=spec_pin)
            after, after_raw, _ = freezer(root, spec_path, spec_validator=validate_contract)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed after cohort execution")
            resources, manifest = saved.encoded(_resources(started)), saved.encoded(inputs.manifest)
            saved.write_exclusive(output / "resource-manifest.json", resources)
            saved.write_exclusive(output / "input-manifest.json", manifest)
            result.update(source_manifest_sha256=source_pin, specification_sha256=spec_pin,
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(manifest))
            body = saved.encoded(result); saved.write_exclusive(output / "result.json", body)
            # Individual cohort completions are the performance accounting units.
            return {"status": result["status"], "output_dir": str(output), "result_sha256": saved.digest(body),
                "family_cohort_evaluation_counts": result["cohort_evaluation_counts"], "D1_completed_studies": 0, "D2_completed_studies": 0,
                "performance_studies_added": 0, "outer_diagnostic_performance_studies_added": 0,
                "independent_output_verification": "pending", "automatic_retries": 0,
                **policy_metadata}
        except BaseException as error:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists(): saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed", "error_type": type(error).__name__,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)],
                    "actual_inputs_attempted": inputs.manifest, "source_manifest_sha256": source_pin, "specification_sha256": spec_pin,
                    "family_cohort_evaluation_counts": _completed_counts(output, result), "outer_diagnostic_performance_studies_added": 0,
                    "cohort_execution_may_be_partial": (output / "cohorts").exists(),
                    "partial_package_preserved": True, "automatic_retries": 0, **dict.fromkeys(FALSE_FLAGS, False),
                    **policy_metadata}))
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=spec_pin, window=spec["window"], runs_path=runs,
            parameters={**spec["parameters"], "mode": spec["mode"], "evaluation_unit": "input_only_orchestration_not_additional_performance_evaluation",
                "source_manifest_sha256": source_pin, "declared_inputs": spec["inputs"], "output_dir": str(output),
                "performance_studies_added": 0, "automatic_retries": 0, **policy_metadata}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True); args = parser.parse_args(argv)
    def expired(signum, frame): raise TimeoutError("registered cohort wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2,) * 2); resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
    signal.signal(signal.SIGALRM, expired); signal.alarm(1800)
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
