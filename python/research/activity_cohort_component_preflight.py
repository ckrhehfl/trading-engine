"""Log and recompute BS component evidence bounds from verified saved outputs.

This terms-only diagnostic preserves the full original family and quote union.
It emits the component proof itself, before any final interval coverage. Actual
pins, reviewed terms/priority intervals and independent new-output verification
are separate registrations. No source body, database, API or book is opened.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import os
import resource
import signal
import time
import traceback

from research import activity_cohort_preflight as cohort
from research import activity_preflight as saved
from research import experiment_log
from research.activity_cohort_connection import cohort_proof_selections
from research.activity_cohort_return_policy import SCHEMA_V2, SCHEMA_V3, declared_policy_report, decode_return_policy
from research.activity_component_bounds import component_exit_bounds
from research.activity_holding_restore import restore_holding_inputs
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _require_raw_potential_scope
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_component_preflight import _bind_raw_basis, _pre_effective_marks
from research.activity_raw_quote_view import calendar_sha256, restore_raw_quote_view
from research.activity_reviewed_actions import reviewed_action_terms


SCHEMA = "activity-cohort-component-preflight-v1"
MODE = "discovery_cohort_component_lifetime_scope"
SOURCE_PATH = "python/research/activity_cohort_component_preflight.py"
INPUT_ROLES = (*cohort.HOLDING_ROLES, "holding_verification", "quote_extension", "quote_verification",
    "raw_episode_quotes", "raw_episode_unit_plan", "raw_episode_verification", "reviewed_terms", "basis_evidence",
    "unresolved_priority_intervals", "return_policy")
OUTPUT_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json",
    "resource-manifest.json", "canonical-terms.json", "component-exit-scope.json")
QUOTE_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json", "resource-manifest.json",
    "read-scope.json", "holding-input-audit.json", "quote-extension.json", "composite-lineage.json")
QUOTE_REFERENCES = ("original-read-scope.json", "holding-verification.json", "original-provisional-exit-scope.json", "coordinate-plan.json")
RAW_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json", "resource-manifest.json",
    "acquisition-plan.json", "unit-plan.json", "cache-manifest.json", "request-manifest.json", "requests.jsonl",
    "new-response-manifest.json", "raw-episode-quotes.json")
QUOTE_FALSE = ("api_access", "returns_computed", "books_computed", "books_replayed", "actions_applied", "source_truth_certified",
    "event_coverage_certified", "no_event_coverage_certified", "price_basis_certified", "historical_vintage_certified",
    "historical_publication_certified", "historical_eligibility_certified", "unit_bridge_certified", "custody_delivery_certified",
    "actual_exits_certified", "atomic_source_vintage_certified", "actual_study_completed", "normal_sources_reread",
    "normal_predicates_reexecuted", "compulsory_successor_closure_complete", "potential_holding_origins_bound_complete",
    "holding_selection_executed", "actual_input_reuse_readiness_established", "resource_enforcement_independently_verified",
    "promotion_allowed", *cohort.frontier.POWER_FLAGS)
RAW_FALSE = ("source_truth_certified", "historical_vintage_certified", "historical_publication_certified", "event_coverage_certified",
    "entitlement_certified", "price_basis_certified", "unit_bridge_certified", "custody_delivery_certified", "isin_join_certified",
    "resource_enforcement_independently_verified", "full_original_cohort_scope_independently_rederived",
    "actual_input_reuse_readiness_established", "component_proof_verified", "original_end_obligation_discharged",
    "returns_computed", "books_computed", *cohort.frontier.POWER_FLAGS, "promotion_allowed", "producer_imported",
    "database_access", "api_access", "credential_access", "canonical_log_reread")
ZERO = ("D1_completed_studies", "D2_completed_studies", "performance_studies_added", "new_source_requests", "automatic_retries")
FALSE_FLAGS = tuple(sorted(set((*QUOTE_FALSE, *RAW_FALSE, "final_coverage_verified", "raw_response_bodies_read",
    "original_source_bodies_read", "actual_investment_return", "actual_study_completed"))))
LIMITS = dict(address_space_bytes=768 * 1024**2, cpu_seconds=300, wall_seconds=900, nice=10)
TEMPLATE_BINDING = "reviewed_lg_template_rebind_current_raw_identity_v1"
TEMPLATE_PINS = dict(
    reviewed_terms="48a65cfde7bfc3fe471a75f256933a0abf43bc00c7186d5c1dc5ec9244eb7333",
    basis_evidence="b63e91db18f149e347ca094ba5a0f1005506e86f10e995ef94f9a76dec91232d")


def _same(left, right, message):
    saved.require(saved.encoded(left) == saved.encoded(right), message)


def validate_contract(spec: dict, reference: dict) -> None:
    """Reuse the original thirteen-field identity; require fifteen explicit pins."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "window", "reference_specification",
        "parameters", "controls", "design", "inputs", "holding_verification_identity", "return_policy_schema",
        "reviewed_terms_binding", "promotion_allowed", *cohort.frontier.POWER_FLAGS} and spec["schema"] == spec["study_id"] == SCHEMA
        and spec["mode"] == MODE and spec["return_policy_schema"] in (SCHEMA_V2, SCHEMA_V3),
        "fixed BS terms-only component diagnostic and explicit v2/v3 policy required")
    saved.require(spec["reviewed_terms_binding"] in ("current_snapshot", TEMPLATE_BINDING),
        "explicit reviewed terms binding required")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(declared) == set(INPUT_ROLES), "exact fifteen saved BS roles required")
    # This projection invokes the unchanged holding identity/parameter validator;
    # it supplies no component proof or executable final coverage.
    projected = {**spec, "schema": cohort.SCHEMA, "study_id": cohort.SCHEMA, "mode": cohort.RETURN_MODE,
        "inputs": {role: declared[role] for role in (*cohort.HOLDING_ROLES, "holding_verification", "basis_evidence", "return_policy")}
            | {"reviewed_action_coverage": declared["reviewed_terms"]}}
    projected.pop("reviewed_terms_binding")
    cohort.validate_contract(projected, reference)
    if spec["reviewed_terms_binding"] == TEMPLATE_BINDING:
        saved.require(all(declared[role]["sha256"] == pin for role, pin in TEMPLATE_PINS.items()),
            "only the previously reviewed LG raw template pair may be rebound")
    paths = []
    for row in declared.values():
        saved.require(type(row) is dict and set(row) == {"path", "sha256"} and type(row["path"]) is str
            and Path(row["path"]).is_absolute() and ".." not in Path(row["path"]).parts, "absolute pinned saved input required")
        saved._pin(row["sha256"])
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "saved input aliases or duplicate reads refused")


validate_spec = validate_contract


def _pins(value, names):
    saved.require(type(value) is dict and set(value) == set(names), "exact independent byte-pin membership required")
    for pin in value.values(): saved._pin(pin)


def _zero(value, names=ZERO):
    saved.require(all(type(value.get(name)) is int and value[name] == 0 for name in names), "non-performance receipt boundary changed")


def _checkout(value, commit):
    cohort._commit(commit)
    _same(value, dict(research=dict(head=commit, clean=True), collector=dict(head=cohort.OPERATIONAL_COMMIT, clean=True)),
        "independent reviewed source/unchanged collector identity changed")


def _restore_original(spec, load):
    """Release the large decoded audit before loading any extension/raw artifact."""
    identity = spec["holding_verification_identity"]
    receipt = saved.strict_json(load("holding_verification")); cohort._receipt(receipt, identity)
    result = saved.strict_json(load("holding_result"))
    declarations = cohort._declarations(saved.strict_json(load("cohort_declarations")))
    scope = saved.strict_json(load("holding_read_scope"))
    ordinary = saved.strict_json(load("ordinary_exit_scope"))
    cohort._family(result, scope, receipt, declarations, ordinary, identity)
    audit = saved.strict_json(load("holding_input_audit"))
    _same(audit.get("summary"), result.get("holding_summary"), "holding audit/result summary changed")
    _same(audit.get("summary"), receipt.get("holding_summary"), "independent typed holding summary changed")
    original = restore_holding_inputs(audit, scope, quote_snapshot_sha256=receipt["quote_snapshot_sha256"],
        activity_snapshot_sha256=receipt["activity_snapshot_sha256"], read_scope_sha256=spec["inputs"]["holding_read_scope"]["sha256"])
    del audit, result, receipt
    return declarations, original, scope, ordinary


def _restore_extension(spec, load, original):
    receipt = saved.strict_json(load("quote_verification"))
    saved.require(type(receipt) is dict and set(receipt) == {"schema", "verified", "expected_commit", "registration_sha256",
        "protocol_sha256", "source_sha256", "file_sha256", "reference_sha256", "publication_sha256", "publication_config_sha256",
        "publication_or_trial_records_verified", "checkouts", "automatic_retries", "performance_studies_added",
        "D1_completed_studies", "D2_completed_studies", "pure_verification", "read_manifest", "tracked_source_manifest"}
        and receipt.get("schema") == "bs-cohort-quote-independent-receipt-v1"
        and receipt.get("verified") is True and receipt.get("publication_or_trial_records_verified") is True,
        "final independent BS quote wrapper required")
    _zero(receipt, tuple(name for name in ZERO if name != "new_source_requests"))
    for name in ("registration_sha256", "protocol_sha256", "publication_config_sha256"): saved._pin(receipt.get(name))
    _pins(receipt.get("source_sha256"), ("helper.py", "register.py", "verifier.py", "bp.py", "io.py", "publication.py"))
    _pins(receipt.get("publication_sha256"), ("receipt.json", "trial-receipts.jsonl"))
    _checkout(receipt.get("checkouts"), receipt.get("expected_commit"))
    _pins(receipt.get("file_sha256"), QUOTE_FILES)
    _pins(receipt.get("reference_sha256"), QUOTE_REFERENCES)
    pure = receipt.get("pure_verification")
    saved.require(type(pure) is dict and pure.get("verified") is True
        and pure.get("scope") == "BS_quote_output_typed_rows_full_origin_union_and_internal_hash_links_only"
        and all(pure.get(name) is True for name in ("original_component_relies_on_prior_independent_verification",
            "independent_coordinate_union_recomputed", "original_full_family_metadata_reread", "injected_BP_source_freeze_is_caller_obligation"))
        and all(pure.get(name) is False for name in (*QUOTE_FALSE, "producer_imported", "database_access", "database_transaction_verified",
            "rights_accounting_certified", "actual_historical_availability_certified", "publication_or_trial_records_verified",
            "original_typed_rows_reread", "original_cohort_declarations_reread", "original_receipt_time_independently_verified",
            "original_source_inputs_opened", "source_body_opened")), "independent quote verification meaning changed")
    _zero(pure, tuple(name for name in ZERO if name != "new_source_requests"))
    for name in ("file_sha256", "reference_sha256"): _same(pure.get(name), receipt[name], "independent quote pin maps disagree")
    declared = spec["inputs"]
    saved.require(receipt["file_sha256"]["quote-extension.json"] == declared["quote_extension"]["sha256"], "quote extension byte pin changed")
    for name, role in (("original-read-scope.json", "holding_read_scope"), ("holding-verification.json", "holding_verification"),
            ("original-provisional-exit-scope.json", "ordinary_exit_scope")):
        saved.require(receipt["reference_sha256"][name] == declared[role]["sha256"], "quote receipt original reference changed")
    package = json.dumps([[name, receipt["file_sha256"][name]] for name in QUOTE_FILES],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    _same(pure.get("package_sha256"), saved.digest(package),
        "independent quote package pin changed")
    envelope = saved.strict_json(load("quote_extension"))
    saved.require(type(envelope) is dict and set(envelope) == {"schema", "original_quote_snapshot_sha256", "read_scope", "holding_input_audit"}
        and envelope["schema"] == "activity-quote-extension-envelope-v1"
        and envelope["original_quote_snapshot_sha256"] == original.quote_snapshot_sha256, "full original quote extension envelope required")
    scope, audit = envelope["read_scope"], envelope["holding_input_audit"]
    saved.require(type(scope) is dict and type(audit) is dict and scope.get("original_quote_snapshot_sha256") == original.quote_snapshot_sha256
        and scope.get("original_read_scope_sha256") == original.read_scope_sha256
        and scope.get("original_input_sha256") == spec["holding_verification_identity"]["input_sha256"], "extension original lineage changed")
    saved.require(saved.digest(saved.encoded(scope)) == receipt["file_sha256"]["read-scope.json"]
        and saved.digest(saved.encoded(audit)) == receipt["file_sha256"]["holding-input-audit.json"], "extension typed scope/audit output pins changed")
    extension = restore_holding_inputs(audit, scope, quote_snapshot_sha256=pure.get("extension_quote_snapshot_sha256"),
        activity_snapshot_sha256=original.activity_snapshot_sha256, read_scope_sha256=receipt["file_sha256"]["read-scope.json"])
    quotes = extend_holding_quotes(original, extension)
    saved.require(pure.get("activity_snapshot_sha256") == original.activity_snapshot_sha256
        and pure.get("original_quote_snapshot_sha256") == original.quote_snapshot_sha256
        and pure.get("composite_quote_snapshot_sha256") == quotes.quote_snapshot_sha256 == receipt["file_sha256"]["composite-lineage.json"],
        "independent quote composite lineage changed")
    return quotes


def _verify_raw(receipt, spec, quotes):
    saved.require(type(receipt) is dict and set(receipt) == {"schema", "verified", "scope", "expected_commit", "specification_sha256",
        "output_inventory", "planning_metadata_sha256", "composite_lineage_sha256", "acquisition_plan_sha256", "unit_plan_sha256",
        "publication_or_trial_records_verified", "all_original_cached_bodies_independently_redecoded", "all_new_bodies_independently_redecoded",
        "typed_raw_rows_verified", "relies_on_pinned_preregistered_cohort_plan", "counts", "read_manifest", "registration_sha256",
        "source_sha256", "publication_sha256", "checkouts", *RAW_FALSE, *ZERO}
        and receipt.get("schema") == "bs-cohort-raw-independent-verification-v1"
        and receipt.get("verified") is True
        and receipt.get("scope") == "saved_raw_original_bodies_typed_rows_receipts_and_registered_internal_links_only"
        and all(receipt.get(name) is False for name in RAW_FALSE)
        and all(receipt.get(name) is True for name in ("publication_or_trial_records_verified", "all_original_cached_bodies_independently_redecoded",
            "all_new_bodies_independently_redecoded", "typed_raw_rows_verified", "relies_on_pinned_preregistered_cohort_plan")),
        "final independent BS raw body/row receipt required")
    _zero(receipt)
    _checkout(receipt.get("checkouts"), receipt.get("expected_commit"))
    for name in ("registration_sha256", "specification_sha256", "planning_metadata_sha256", "acquisition_plan_sha256", "unit_plan_sha256"):
        saved._pin(receipt.get(name))
    _pins(receipt.get("source_sha256"), ("helper.py", "numeric.py", "io.py", "publication.py"))
    _pins(receipt.get("publication_sha256"), ("receipt.json", "trial-receipts.jsonl"))
    inventory = receipt.get("output_inventory")
    saved.require(type(inventory) is dict and set(RAW_FILES) <= set(inventory),
        "complete raw output byte inventory required")
    for name, row in inventory.items():
        saved.require(type(name) is str and PurePosixPath(name).name == name and name not in (".", "..") and "\0" not in name
            and type(row) is dict and set(row) == {"sha256", "bytes"} and type(row["bytes"]) is int
            and 0 <= row["bytes"] <= 256 * 1024**2, "bounded raw output byte receipt required")
        saved._pin(row["sha256"])
        saved.require((row["bytes"] == 0) is (row["sha256"] == saved.digest(b"")), "empty raw output identity changed")
    for name, pin in (("raw-episode-quotes.json", spec["inputs"]["raw_episode_quotes"]["sha256"]),
            ("unit-plan.json", spec["inputs"]["raw_episode_unit_plan"]["sha256"]),
            ("specification.json", receipt["specification_sha256"]), ("acquisition-plan.json", receipt["acquisition_plan_sha256"])):
        saved.require(inventory[name]["sha256"] == pin, "raw source/unit/output pins changed")
    saved.require(receipt["unit_plan_sha256"] == quotes.unit_plan_sha256
        and receipt.get("composite_lineage_sha256") == quotes.base.quote_snapshot_sha256
        and inventory["raw-episode-quotes.json"]["bytes"] == len(quotes.source_artifact_json)
        and inventory["unit-plan.json"]["bytes"] == len(quotes.unit_plan_json), "raw receipt typed base/unit/source identity changed")
    counts, manifest = receipt.get("counts"), receipt.get("read_manifest")
    saved.require(type(counts) is dict and set(counts) == {"cached_response_bodies_redecoded", "new_response_bodies_redecoded", "raw_rows_verified", "response_bytes"}
        and all(type(number) is int and number >= 0 for number in counts.values()) and type(manifest) is list,
        "exact independent raw read counts required")
    responses = []
    for row in manifest:
        saved.require(type(row) is dict and set(row) == {"role", "path", "sha256", "bytes", "read_status"}
            and type(row["role"]) is str and type(row["path"]) is str and Path(row["path"]).is_absolute()
            and type(row["bytes"]) is int and row["bytes"] >= 0 and row["read_status"] == "hash_verified", "raw completed-read receipt required")
        saved._pin(row["sha256"])
        if row["role"] in ("cached_response", "new_response"):
            saved.require(0 < row["bytes"] <= 8 * 1024**2, "bounded original raw response receipt required")
            responses.append(row)
            if row["role"] == "new_response":
                _same(inventory.get(PurePosixPath(row["path"]).name), {key: row[key] for key in ("sha256", "bytes")}, "new raw response output link changed")
    saved.require(len({row["path"] for row in responses}) == len(responses)
        and all(counts[name] == sum(row["role"] == role for row in responses) for name, role in
            (("cached_response_bodies_redecoded", "cached_response"), ("new_response_bodies_redecoded", "new_response")))
        and counts["response_bytes"] == sum(row["bytes"] for row in responses)
        and counts["raw_rows_verified"] == len(quotes.raw_observations)
        and {row["sha256"] for row in responses} == {row.response_sha256 for row in quotes.raw_observations},
        "raw body/typed-row verification scope changed")


def _policy_obligations(proof, policy):
    """Keep v1/v2 full horizons; v3 retains the existing recomputed proof bounds."""
    if policy["schema"] == SCHEMA_V3:
        return
    for row in proof["entries"]:
        origin = row["origin"]
        for event in policy["events"]:
            if (not any((part["code"], part["isin"]) == (event["code"], event["isin"]) for part in row["components"])
                    or origin["entry_on"] > event["end"] or event["start"] > row["original_end_on"]):
                continue
            saved.require(row["end_on"] == row["original_end_on"], "unmodeled asset origin must retain its full original horizon")
            saved.require(any((priority["code"], priority["isin"]) == (event["code"], event["isin"])
                and priority["start"] <= max(origin["entry_on"], event["start"])
                and min(row["original_end_on"], event["end"]) <= priority["end"]
                for priority in proof["unresolved_priority_intervals"]), "explicit unmodeled-asset priority interval required")


def _bind_reviewed_template(spec, coverage, evidence, original, quotes, *, policy, windows):
    """Preserve reviewed LG facts/values; derive only this run's snapshot labels.

    The prior complete coverage is not transferred. Actual raw anchors and all
    terms are checked again below against this run's independently verified raw
    view. The exact prior template pins are part of the registered specification.
    """
    if spec["reviewed_terms_binding"] == "current_snapshot":
        return coverage, evidence, None
    saved.require(all(spec["inputs"][role]["sha256"] == pin for role, pin in TEMPLATE_PINS.items()),
        "reviewed template pair changed")
    saved.require(type(coverage) is dict and coverage.get("schema") == "activity-reviewed-actions-v1"
        and coverage.get("status") == "reviewed_complete" and coverage.get("unresolved") == []
        and type(coverage.get("events")) is list and len(coverage["events"]) == 1
        and coverage["events"][0].get("event_id") == "lg-2021-compulsory-spin-off"
        and coverage["events"][0].get("kind") == "compulsory_spin_off"
        and type(evidence) is dict and set(evidence) == {"references", "bases"}
        and type(evidence["bases"]) is dict and len(evidence["bases"]) == 3,
        "reviewed LG-only template required")
    old_snapshot = coverage.get("quote_snapshot_sha256")
    saved._pin(old_snapshot)
    saved.require(all(type(row) is dict and row.get("quote_snapshot_sha256") == old_snapshot
        and row.get("code") in ("003550", "383800") for row in evidence["bases"].values()),
        "template bases must retain one reviewed LG/LX snapshot")
    bound, bases = deepcopy(coverage), deepcopy(evidence)
    bound.update(activity_snapshot_sha256=original.activity_snapshot_sha256,
        quote_snapshot_sha256=quotes.quote_snapshot_sha256, required_scope_sha256=original.read_scope_sha256,
        windows=[], noops=[], unresolved=[])
    # Calendar dates do not change between the saved study and this BS family.
    saved.require(bound.get("calendar_sha256") == calendar_sha256(original.calendar),
        "reviewed template calendar changed")
    for row in bases["bases"].values():
        row["quote_snapshot_sha256"] = quotes.quote_snapshot_sha256
    policy_ids = []
    for event in policy["events"]:
        if not any((row["code"], row["isin"]) == (event["code"], event["isin"])
                and row["start"] <= event["end"] and event["start"] <= row["end"] for row in windows):
            continue
        fields = ("event_id", "kind", "code", "isin", "start", "end", "evidence_ref")
        declaration = {key: event[key] for key in fields}
        declaration["status"] = "reviewed_unmodeled"
        if "date_role" in event: declaration["date_role"] = event["date_role"]
        reference_id = event["evidence_ref"]
        saved.require(reference_id not in bases["references"], "policy reference collides with reviewed LG template")
        bases["references"][reference_id] = dict(
            **{key: event[key] for key in ("code", "isin", "start", "end", "raw_sha256", "review_sha256", "coordinate")},
            reviewed_on=(max(policy["approved_on"], policy["scope_decision_recorded_on"])
                if policy["schema"] == SCHEMA_V3 else policy["approved_on"]),
            finding="reviewed_" + event["kind"], event_ids=[event["event_id"]])
        bound["events"].append(declaration)
        policy_ids.append(event["event_id"])
    binding = dict(mode=TEMPLATE_BINDING, template_sha256=dict(TEMPLATE_PINS),
        previous_activity_snapshot_sha256=coverage.get("activity_snapshot_sha256"),
        previous_quote_snapshot_sha256=old_snapshot, previous_required_scope_sha256=coverage.get("required_scope_sha256"),
        activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        required_scope_sha256=original.read_scope_sha256, modeled_event_declarations_unchanged=True,
        template_source_references_unchanged=True, basis_values_unchanged=True, prior_interval_coverage_transferred=False,
        policy_event_ids_connected=sorted(policy_ids), policy_source_references_added=len(policy_ids),
        raw_anchor_count=len(bases["bases"]), source_truth_certified=False)
    return bound, bases, binding


def evaluate_inputs(spec, inputs, output):
    validate_contract(spec, {"parameters": PARAMETERS})
    def load(role):
        row = spec["inputs"][role]
        return inputs.read(Path(row["path"]), row["sha256"], role)
    declarations, original, scope, ordinary = _restore_original(spec, load)
    base = _restore_extension(spec, load, original)
    raw, unit = load("raw_episode_quotes"), load("raw_episode_unit_plan")
    quotes = restore_raw_quote_view(base, raw, spec["inputs"]["raw_episode_quotes"]["sha256"], unit, spec["inputs"]["raw_episode_unit_plan"]["sha256"])
    del raw, unit
    raw_receipt = saved.strict_json(load("raw_episode_verification")); _verify_raw(raw_receipt, spec, quotes)
    policy_raw = load("return_policy"); policy = decode_return_policy(policy_raw)
    saved.require(policy["schema"] == spec["return_policy_schema"], "loaded policy differs from registered version")
    coverage, evidence = saved.strict_json(load("reviewed_terms")), saved.strict_json(load("basis_evidence"))
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"]) for row in ordinary["entries"])
    coverage, evidence, template_binding = _bind_reviewed_template(spec, coverage, evidence, original, quotes,
        policy=policy, windows=windows)
    saved.require(type(coverage) is dict and all(coverage.get(name) == [] for name in ("windows", "noops", "unresolved"))
        and coverage.get("required_scope_sha256") == original.read_scope_sha256, "terms-only original-scope envelope required")
    selections = cohort_proof_selections(declarations, original.calendar, spec["parameters"])
    _bind_raw_basis(evidence, quotes)
    terms = reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=original.calendar,
        activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256, return_policy_json=policy_raw)
    checks = _pre_effective_marks(terms, quotes)
    saved.require(not any(row["later_observable_sessions"] for row in checks), "old basis conflicts with a later observable pre-event mark")
    saved.require(not any(row["state"] == "unresolved" for row in checks), "old basis role has unresolved pre-event coordinates")
    unresolved = saved.strict_json(load("unresolved_priority_intervals"))
    saved.require(type(unresolved) is list, "explicit pinned unresolved-priority interval list required")
    proof = component_exit_bounds(ordinary, selections["D1"], selections["D2"], original, spec["parameters"], scope["requested_code_dates"],
        scope["issue_isins"], input_sha256=spec["holding_verification_identity"]["input_sha256"], quotes=quotes, terms=terms,
        unresolved_priority_intervals=tuple(unresolved))
    _policy_obligations(proof, policy)
    _require_raw_potential_scope(quotes, proof, terms.events)
    proof_raw = saved.encoded(proof)
    saved.write_exclusive(output / "canonical-terms.json", terms.canonical_json)
    saved.write_exclusive(output / "component-exit-scope.json", proof_raw)
    summary = dict(cohort_declarations=len(declarations), potential_entries=len(proof["entries"]),
        entries_by_arm={arm: sum(row["origin"]["arm"] == arm for row in proof["entries"]) for arm in ("D1", "D2")},
        mode_counts=dict(sorted(Counter(row["mode"] for row in proof["entries"]).items())),
        original_code_dates=len(original.requested_coordinates), extension_code_dates=len(base.extension.requested_coordinates),
        raw_code_dates=len(quotes.raw_observations), action_windows=len(proof["windows"]))
    return dict(schema=SCHEMA, status="provisional_cohort_component_scope_planned", terms_status="terms_only", summary=summary,
        input_sha256={role: row["sha256"] for role, row in spec["inputs"].items()},
        original_input_sha256=spec["holding_verification_identity"]["input_sha256"],
        original_read_scope_sha256=original.read_scope_sha256, activity_snapshot_sha256=original.activity_snapshot_sha256,
        original_quote_snapshot_sha256=original.quote_snapshot_sha256, extension_quote_snapshot_sha256=base.extension.quote_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, raw_quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        raw_source_artifact_sha256=quotes.source_artifact_sha256, raw_unit_plan_sha256=quotes.unit_plan_sha256,
        terms_sha256=terms.terms_sha256, reviewed_template_binding=template_binding,
        ordinary_scope_sha256=proof["ordinary_scope_sha256"], component_scope_sha256=proof["component_scope_sha256"],
        pre_effective_mark_checks=checks, raw_verification_counts=raw_receipt["counts"],
        prior_independent_receipts_relied_on=True, original_quote_requests_preserved=True, independent_output_verification="pending",
        unmodeled_asset_values={event["event_id"]: None for event in policy["events"]},
        output_sha256={"canonical-terms.json": saved.digest(terms.canonical_json), "component-exit-scope.json": saved.digest(proof_raw)},
        **{**dict.fromkeys(FALSE_FLAGS, False), **dict.fromkeys(ZERO, 0), **declared_policy_report(policy["schema"], saved.digest(policy_raw))})


def _resources(started):
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return dict(elapsed_seconds=time.monotonic() - started, process_peak_rss_kib=usage.ru_maxrss,
        process_user_cpu_seconds=usage.ru_utime, process_system_cpu_seconds=usage.ru_stime,
        address_space_limit_bytes=list(resource.getrlimit(resource.RLIMIT_AS)), cpu_limit_seconds=list(resource.getrlimit(resource.RLIMIT_CPU)),
        wall_limit_seconds=LIMITS["wall_seconds"], nice=os.getpriority(os.PRIO_PROCESS, 0))


def _secondary(output, name, primary, operation, secondary):
    """Preserve cleanup evidence without replacing the explicitly saved failure."""
    try:
        primary.add_note(f"secondary {operation} failed: {type(secondary).__name__}")
        saved.write_exclusive(output / name, saved.encoded(dict(status="failed", primary_error_type=type(primary).__name__,
            secondary_errors=[dict(operation=operation, error_type=type(secondary).__name__)], **dict.fromkeys(ZERO, 0))))
    except BaseException:
        pass


def run_preflight(root, spec_path, output_dir, runs_path, *, reader=None, freeze=None):
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "run committed BS component caller from root")
    freezer = saved.freeze_sources if freeze is None else freeze
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_contract)
    saved.require(any(row["path"] == SOURCE_PATH and row["sha256"] == saved.digest(Path(__file__).read_bytes()) for row in sources["files"]),
        "caller must belong to committed frozen source")
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive BS component output already exists")
    large = {Path(spec["inputs"][role]["path"]) for role in ("holding_input_audit", "quote_extension", "raw_episode_quotes")}
    inputs = saved.PinnedInputs((lambda path: cohort._read_large(path) if path in large else saved.read_private(path)) if reader is None else reader)
    source_pin, spec_pin = saved.digest(saved.encoded(sources)), saved.digest(raw)
    metadata = declared_policy_report(spec["return_policy_schema"], spec["inputs"]["return_policy"]["sha256"])
    primary_error = None
    def evaluate():
        nonlocal primary_error
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent); output.mkdir(mode=0o700); created = True; saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_inputs(spec, inputs, output)
            after, after_raw, _ = freezer(root, spec_path, spec_validator=validate_contract)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed during component diagnostic")
            resources, manifest = saved.encoded(_resources(started)), saved.encoded(inputs.manifest)
            saved.write_exclusive(output / "resource-manifest.json", resources); saved.write_exclusive(output / "input-manifest.json", manifest)
            result.update(source_manifest_sha256=source_pin, specification_sha256=spec_pin,
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(manifest))
            body = saved.encoded(result); saved.write_exclusive(output / "result.json", body)
            return dict(status=result["status"], output_dir=str(output), result_sha256=saved.digest(body), summary=result["summary"],
                terms_sha256=result["terms_sha256"], component_scope_sha256=result["component_scope_sha256"],
                output_sha256={"result.json": saved.digest(body), "specification.json": spec_pin, "source-manifest.json": source_pin,
                    "input-manifest.json": saved.digest(manifest), "resource-manifest.json": saved.digest(resources), **result["output_sha256"]},
                independent_output_verification="pending", **dict.fromkeys(ZERO, 0), **metadata)
        except BaseException as error:
            primary_error = error
            if created:
                artifacts = (("resource-manifest.json", lambda: _resources(started)), ("input-manifest.json", lambda: inputs.manifest),
                    ("failure.json", lambda: dict(status="failed", error_type=type(error).__name__,
                        error_frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                            for frame in traceback.extract_tb(error.__traceback__)], actual_inputs_attempted=inputs.manifest,
                        source_manifest_sha256=source_pin, specification_sha256=spec_pin, partial_package_preserved=True,
                        **{**dict.fromkeys(FALSE_FLAGS, False), **dict.fromkeys(ZERO, 0), **metadata})))
                for name, value in artifacts:
                    try:
                        if not (output / name).exists(): saved.write_exclusive(output / name, saved.encoded(value()))
                    except BaseException as secondary:
                        error.add_note(f"secondary {name} preservation failed: {type(secondary).__name__}")
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=spec_pin, window=spec["window"], runs_path=runs,
            parameters={**spec["parameters"], "mode": spec["mode"], "source_manifest_sha256": source_pin,
                "reviewed_terms_binding": spec["reviewed_terms_binding"], "declared_inputs": spec["inputs"],
                "output_dir": str(output), **dict.fromkeys(ZERO, 0), **metadata}, evaluate=evaluate)
    except BaseException as error:
        if primary_error is not None and error is not primary_error:
            _secondary(output, "logging-failure.json", primary_error, "canonical_failure_append", error)
            raise primary_error from None
        primary_error = error
        raise
    finally:
        try: os.umask(previous)
        except BaseException as secondary:
            if primary_error is None: raise
            _secondary(output, "umask-cleanup-failure.json", primary_error, "restore_umask", secondary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("spec", "output-dir", "runs-path"): parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    def expired(signum, frame): raise TimeoutError("registered BS component wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (LIMITS["address_space_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (LIMITS["cpu_seconds"],) * 2)
    signal.signal(signal.SIGALRM, expired); signal.alarm(LIMITS["wall_seconds"])
    primary_error = None
    try:
        priority = os.getpriority(os.PRIO_PROCESS, 0)
        saved.require(priority <= LIMITS["nice"] and os.nice(LIMITS["nice"] - priority) == LIMITS["nice"], "exact nice10 required")
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except BaseException as error:
        primary_error = error
        if not isinstance(error, Exception): raise
        try: print(saved.encoded(dict(status="failed", error_type=type(error).__name__, **dict.fromkeys(ZERO, 0))).decode())
        except BaseException: raise error from None
        return 1
    finally:
        try: signal.alarm(0)
        except BaseException as secondary:
            if primary_error is None: raise
            _secondary(args.output_dir, "alarm-cleanup-failure.json", primary_error, "cancel_wall_alarm", secondary)
    print(saved.encoded(record["result"]).decode()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
