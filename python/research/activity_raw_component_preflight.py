"""One logged raw component-scope diagnostic over 27 frozen saved artifacts.

No raw response body, credential, database, network, final coverage or book is
opened here. Raw/raw bases use the separate declared raw share unit; retained
adjusted anchors never establish a vendor bridge or source/date-role truth.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import re
import time
import traceback

from research import activity_exit_preflight as logged
from research import activity_holding_preflight as holding
from research import activity_preflight as saved
from research import experiment_log
from research.activity_book import CompulsorySpinOff, CompulsoryStockExchange
from research.activity_component_bounds import WRAPPER_SCHEMA, component_exit_bounds
from research.activity_holding_restore import restore_holding_inputs
from research.activity_paired_replay import BM_ROLES, PACKAGE_ROLES, _require_raw_potential_scope, prepare_inputs
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_episode import TARGET_CODES
from research.activity_raw_quote_view import calendar_sha256, raw_price_basis, restore_raw_quote_view
from research.activity_reviewed_actions import reviewed_action_terms


SCHEMA = "activity-raw-component-scope-preflight-v1"
SOURCE_PATH = "python/research/activity_raw_component_preflight.py"
INPUT_FILES = {**holding.BL_ROLES, "bl_verification": "verification.json", "bj_scope": "result.json",
    **BM_ROLES, "bm_verification": "verification.json", "ordinary_exit_scope": "exit-scope.json",
    "quote_extension": "quote-extension.json", "quote_verification": "verification.json",
    "raw_episode_quotes": "raw-episode-quotes.json", "raw_episode_unit_plan": "unit-plan.json",
    "raw_episode_verification": "verification.json", "reviewed_terms": "reviewed-terms.json",
    "basis_evidence": "basis-evidence.json", "unresolved_priority_intervals": "unresolved-priority-intervals.json"}
OUTPUT_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json",
                "resource-manifest.json", "canonical-terms.json", "component-exit-scope.json")
RAW_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json",
    "resource-manifest.json", "acquisition-plan.json", "unit-plan.json", "cache-manifest.json",
    "request-manifest.json", "requests.jsonl", "new-response-manifest.json", "raw-episode-quotes.json")
RAW_FALSE = ("source_truth_certified", "historical_vintage_certified", "historical_publication_certified",
    "event_coverage_certified", "price_basis_certified", "units_certified", "custody_delivery_certified",
    "books_computed", "returns_computed", "promotion_allowed", "unit_bridge_certified", "isin_join_certified",
    "atomic_source_vintage_certified", "actions_applied", "actual_study_completed", "episode_end_is_actual_exit",
    "original_end_obligation_discharged")
COUNT_FIELDS = ("calendar_sessions", "potential_origins", "raw_unique_days", "raw_code_dates", "cached_responses",
    "new_requests", "cached_code_dates", "new_code_dates", "cache_geometry_unique_days",
    "aq_responses", "ar_original_responses", "ar_recovery_responses", "bp_raw_responses")
FALSE_FLAGS = (*RAW_FALSE, "no_event_coverage_certified", "actual_exits_certified", "database_access",
    "api_access", "credential_access", "raw_response_bodies_read", "final_coverage_verified",
    "whole_package_independent_verification_performed")
INPUT_MAX_BYTES = {role: (128 if role == "bl_population_audit" else 64) * 1024**2 for role in INPUT_FILES}


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs"} and spec["schema"] == spec["study_id"] == SCHEMA
        and spec["mode"] == "discovery_raw_component_lifetime_scope" and spec["promotion_allowed"] is False
        and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW)
        and spec["reference_specification"] == saved.REFERENCE and type(spec["parameters"]) is dict
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]), "fixed raw component specification mismatch")
    saved.require(type(spec["inputs"]) is dict and set(spec["inputs"]) == set(INPUT_FILES), "exact 27 saved roles required")
    paths = []
    for role, filename in INPUT_FILES.items():
        row = spec["inputs"][role]
        saved.require(type(row) is dict and set(row) == {"path", "sha256"} and type(row["path"]) is str
            and Path(row["path"]).is_absolute() and ".." not in Path(row["path"]).parts
            and Path(row["path"]).name == filename, "exact absolute saved filename/pin required")
        saved._pin(row["sha256"])
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "distinct saved paths required")
    for roles in (holding.BL_ROLES, BM_ROLES):
        saved.require(len({str(Path(spec["inputs"][role]["path"]).parent) for role in roles}) == 1,
                      "each original package must retain one directory")


def _zero(value: dict, names=("new_source_requests", "automatic_retries", "D1_completed_studies", "D2_completed_studies")) -> None:
    saved.require(all(type(value.get(name)) is int and value[name] == 0 for name in names), "zero-count receipt boundary mismatch")


def _restore_extension(documents: dict, pins: dict, prepared):
    """Apply the existing committed basis caller's BP binding, without its raw anchors."""
    envelope, receipt = documents["quote_extension"], documents["quote_verification"]
    saved.require(type(receipt) is dict and receipt.get("verified") is True
        and receipt.get("scope") == "BP_new_output_typed_rows_exact_extension_scope_composite_and_publication_links_only"
        and type(receipt.get("file_sha256")) is dict and type(receipt.get("reference_sha256")) is dict
        and receipt["file_sha256"].get("quote-extension.json") == pins["quote_extension"]
        and receipt["reference_sha256"].get("bm-read-scope.json") == pins["bm_read_scope"]
        and receipt["reference_sha256"].get("bm-verification.json") == pins["bm_verification"]
        and all(receipt.get(name) is False for name in ("database_access", "api_access", "returns_computed",
            "historical_vintage_certified", "price_basis_certified", "event_coverage_certified")), "prior BP receipt mismatch")
    _zero(receipt, ("D1_completed_studies", "D2_completed_studies"))
    saved.require(type(envelope) is dict and set(envelope) == {"schema", "original_quote_snapshot_sha256", "read_scope", "holding_input_audit"}
        and envelope["schema"] == "activity-quote-extension-envelope-v1"
        and envelope["original_quote_snapshot_sha256"] == prepared.quote_snapshot_sha256, "full extension envelope mismatch")
    scope, audit = envelope["read_scope"], envelope["holding_input_audit"]
    saved.require(type(scope) is dict and type(audit) is dict
        and scope.get("original_quote_snapshot_sha256") == prepared.quote_snapshot_sha256
        and scope.get("original_read_scope_sha256") == prepared.read_scope_sha256
        and scope.get("original_input_sha256") == prepared.input_sha256, "extension original full package mismatch")
    extension = restore_holding_inputs(audit, scope, quote_snapshot_sha256=receipt.get("extension_quote_snapshot_sha256"),
        activity_snapshot_sha256=prepared.activity_snapshot_sha256, read_scope_sha256=saved.digest(saved.encoded(scope)))
    quotes = extend_holding_quotes(prepared.holding_inputs, extension)
    saved.require(receipt.get("activity_snapshot_sha256") == prepared.activity_snapshot_sha256
        and receipt.get("original_quote_snapshot_sha256") == prepared.quote_snapshot_sha256
        and receipt.get("composite_quote_snapshot_sha256") == quotes.quote_snapshot_sha256, "BP composite lineage mismatch")
    return quotes


def _verify_raw(receipt: dict, pins: dict, quotes) -> None:
    """Bind a prior limited independent pass; cached source truth stays unverified."""
    saved.require(type(receipt) is dict and receipt.get("schema") == "bq-raw-episode-independent-verification-v1"
        and receipt.get("verified") is True and type(receipt.get("file_sha256")) is dict
        and set(receipt["file_sha256"]) == set(RAW_FILES)
        and receipt["file_sha256"]["raw-episode-quotes.json"] == pins["raw_episode_quotes"]
        and receipt["file_sha256"]["unit-plan.json"] == pins["raw_episode_unit_plan"]
        and receipt.get("specification_sha256") == receipt["file_sha256"]["specification.json"]
        and receipt.get("unit_plan_sha256") == pins["raw_episode_unit_plan"]
        and receipt.get("acquisition_plan_sha256") == receipt["file_sha256"]["acquisition-plan.json"],
        "prior raw verification output links mismatch")
    publications = receipt.get("publication_sha256")
    saved.require(type(publications) is dict and set(publications) == {"receipt.json", "trial-receipts.jsonl"}
        and type(receipt.get("expected_commit")) is str and re.fullmatch(r"[0-9a-f]{40}", receipt["expected_commit"]),
        "prior raw publication/source identity mismatch")
    for pin in (*receipt["file_sha256"].values(), *publications.values(),
            *(receipt.get(name) for name in ("helper_sha256", "protocol_sha256", "producer_sha256", "caller_sha256",
                "credential_reader_sha256", "verification_registration_sha256", "scope_review_sha256"))):
        saved._pin(pin)
    saved.require(receipt.get("activity_snapshot_sha256") == quotes.activity_snapshot_sha256
        and receipt.get("base_quote_snapshot_sha256") == quotes.base.quote_snapshot_sha256
        and receipt.get("calendar_sha256") == calendar_sha256(quotes.calendar), "raw receipt/base/calendar lineage mismatch")
    saved.require(all(receipt.get(name) is False for name in (*RAW_FALSE, "credential_access", "database_access", "api_access",
            "cached_body_truth_independently_verified", "cached_row_positions_independently_verified",
            "missing_market_certifies_market_identity", "resource_enforcement_independently_verified", "source_files_reread"))
        and all(receipt.get(name) is True for name in ("new_response_row_extract_verified", "raw_response_bodies_read",
            "current_retrieval_only", "resource_records_verified", "durable_start_before_recorded_requests_verified"))
        and receipt.get("response_pin_source") == "root_frozen_new_response_manifest_before_body_reads",
        "prior raw verification meaning mismatch")
    _zero(receipt)
    _zero(receipt, ("cached_source_bodies_read", "upstream_inputs_read"))
    summary, dependencies = receipt.get("summary"), receipt.get("response_dependency_pins")
    saved.require(type(summary) is dict and set(summary) == {*COUNT_FIELDS, "observed_rows", "unresolved_rows", "no_trade_zero_price_rows"}
        and all(type(value) is int and value >= 0 for value in summary.values())
        and type(dependencies) is dict and len(dependencies) == summary["new_requests"]
        and type(receipt.get("new_response_bodies_read")) is int and receipt["new_response_bodies_read"] == len(dependencies)
        and type(receipt.get("new_code_dates_verified")) is int and receipt["new_code_dates_verified"] == summary["new_code_dates"]
        and type(receipt.get("cached_code_dates_structurally_verified")) is int
        and receipt["cached_code_dates_structurally_verified"] == summary["cached_code_dates"],
        "prior raw verification count mismatch")
    raw_rows = quotes.raw_observations
    rows_by_day = {}
    for observation in raw_rows:
        rows_by_day.setdefault(observation.requested_bas_dd, []).append(observation)
    new_days, new_coordinates = set(), set()
    for number, (filename, row) in enumerate(sorted(dependencies.items()), 1):
        saved.require(type(filename) is str and re.fullmatch(r"response-[0-9]{4}-stk_bydd_trd-[0-9]{8}\.json", filename)
            and type(row) is dict and set(row) == {"sha256", "bytes"} and type(row["bytes"]) is int
            and 0 < row["bytes"] <= 8 * 1024**2, "raw response dependency receipt mismatch")
        saved._pin(row["sha256"])
        response_day = filename.removesuffix(".json").rsplit("-", 1)[1]
        observations = rows_by_day.get(response_day, ())
        saved.require(filename == f"response-{number:04d}-stk_bydd_trd-{response_day}.json"
            and response_day not in new_days and observations
            and all(observation.response_sha256 == row["sha256"] for observation in observations),
            "raw response dependency differs from restored date/hash coordinates")
        new_days.add(response_day)
        new_coordinates.update((observation.code, observation.requested_bas_dd) for observation in observations)
    saved.require(len(new_coordinates) == summary["new_code_dates"]
        and len(raw_rows) - len(new_coordinates) == summary["cached_code_dates"]
        and len(rows_by_day) - len(new_days) == summary["cached_responses"],
        "raw dependency coordinate partition differs from verification counts")
    saved.require(summary["calendar_sessions"] == len(quotes.calendar)
        and summary["raw_code_dates"] == len(raw_rows)
        and summary["raw_unique_days"] == len({row.requested_bas_dd for row in raw_rows})
        and summary["observed_rows"] == sum(row.status == "observed" for row in raw_rows)
        and summary["unresolved_rows"] == sum(row.status != "observed" for row in raw_rows)
        and summary["no_trade_zero_price_rows"] == sum(row.state == "observed_no_trade_zero_prices" for row in raw_rows)
        and summary["cached_code_dates"] + summary["new_code_dates"] == len(raw_rows)
        and summary["cached_responses"] == sum(summary[name] for name in
            ("aq_responses", "ar_original_responses", "ar_recovery_responses", "bp_raw_responses")),
        "raw verification counts differ from restored artifact")


def _bind_raw_basis(evidence: dict, quotes) -> None:
    saved.require(type(evidence) is dict and set(evidence) == {"references", "bases"} and type(evidence["bases"]) is dict,
                  "reviewed basis declarations required")
    for row in evidence["bases"].values():
        saved.require(type(row) is dict, "reviewed basis row required")
        if row.get("code") in TARGET_CODES:
            basis = raw_price_basis(quotes, row["code"], date.fromisoformat(row["session"]))
            saved.require(Decimal(row["raw_close"]) == Decimal(row["adjusted_close"]) == basis.raw_close
                and row.get("quote_snapshot_sha256") == quotes.quote_snapshot_sha256,
                "raw basis must match the validated requested raw/raw close")


def _pre_effective_marks(terms, quotes) -> list[dict]:
    raw_rows = {(row.code, date.fromisoformat(row.requested_bas_dd)): row for row in quotes.raw_observations}
    base_rows = {(row.code, row.observation_date): row for row in quotes.base.observations}
    checks = []
    for event in terms.events:
        if not isinstance(event, (CompulsorySpinOff, CompulsoryStockExchange)):
            continue
        counts = Counter()
        latest = event.old_basis.session
        for index, day in enumerate(quotes.calendar):
            if not event.old_basis.session < day < event.effective_on:
                continue
            counts["checked_sessions"] += 1
            if event.old_basis.code in TARGET_CODES:
                row = raw_rows.get((event.old_basis.code, day))
                if row is None:
                    counts["unrequested_sessions"] += 1
                elif row.issues == ("target_absent_cause_unknown",):
                    counts["absent_requested_sessions"] += 1
                elif row.state == "observed_no_trade_zero_prices":
                    counts["no_trade_zero_price_sessions"] += 1
                elif row.status != "observed" or row.issues or row.close is None:
                    counts["invalid_sessions"] += 1
                elif row.state == "frozen":
                    counts["frozen_sessions"] += 1
                else:
                    counts["later_observable_sessions"] += 1
                    latest = day
            else:
                row, series = base_rows.get((event.old_basis.code, day)), quotes.panel[event.old_basis.code]
                if row is None:
                    counts["unrequested_sessions"] += 1
                elif row.typed_values is None:
                    counts["absent_requested_sessions"] += 1
                elif row.issues or series.closes[index] is None or series.observed[index] is not True:
                    counts["invalid_sessions"] += 1
                elif series.frozen[index] is True:
                    counts["frozen_sessions"] += 1
                else:
                    counts["later_observable_sessions"] += 1
                    latest = day
        unknown = counts["unrequested_sessions"] + counts["invalid_sessions"]
        checks.append(dict(event_id=event.event_id, code=event.old_basis.code, old_basis_on=str(event.old_basis.session),
            effective_on=str(event.effective_on), latest_requested_resolved_observable_on=str(latest),
            **{name: counts[name] for name in ("checked_sessions", "unrequested_sessions", "invalid_sessions",
                "absent_requested_sessions", "no_trade_zero_price_sessions", "frozen_sessions", "later_observable_sessions")},
            state="contradictory_later_observable" if counts["later_observable_sessions"] else
                "unresolved" if unknown else "no_later_observable_in_retained_quotes", basis_role_certified=False))
    return checks


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    pins = {role: spec["inputs"][role]["sha256"] for role in INPUT_FILES}
    raw = {}
    for role in INPUT_FILES:
        raw[role] = inputs.read(Path(spec["inputs"][role]["path"]), pins[role], role)
        saved.require(len(raw[role]) <= INPUT_MAX_BYTES[role], "input exceeds fixed role ceiling")
    prepared = prepare_inputs({"files": {name: raw[role] for role, name in holding.BL_ROLES.items()},
        "verification": raw["bl_verification"], "bj_scope": raw["bj_scope"]},
        {"files": {name: raw[role] for role, name in BM_ROLES.items()}, "verification": raw["bm_verification"]},
        parameters=spec["parameters"], expected_pins={role: pins[role] for role in PACKAGE_ROLES})
    documents = {role: saved.strict_json(raw[role]) for role in INPUT_FILES
                 if role not in (*PACKAGE_ROLES, "raw_episode_quotes", "raw_episode_unit_plan")}
    base = _restore_extension(documents, pins, prepared)
    quotes = restore_raw_quote_view(base, raw["raw_episode_quotes"], pins["raw_episode_quotes"],
        raw["raw_episode_unit_plan"], pins["raw_episode_unit_plan"])
    del raw
    receipt = documents["raw_episode_verification"]
    _verify_raw(receipt, pins, quotes)
    declarations, evidence = documents["reviewed_terms"], documents["basis_evidence"]
    saved.require(type(declarations) is dict and all(declarations.get(name) == [] for name in ("windows", "noops", "unresolved"))
        and declarations.get("required_scope_sha256") == prepared.read_scope_sha256, "terms-only original-scope envelope required")
    _bind_raw_basis(evidence, quotes)
    terms = reviewed_action_terms(declarations, evidence, expected_windows=prepared.expected_windows,
        calendar=prepared.calendar, activity_snapshot_sha256=prepared.activity_snapshot_sha256,
        quote_snapshot_sha256=quotes.quote_snapshot_sha256)
    checks = _pre_effective_marks(terms, quotes)
    saved.require(not any(row["later_observable_sessions"] for row in checks), "old basis conflicts with a later observable pre-event mark")
    saved.require(not any(row["state"] == "unresolved" for row in checks), "old basis role has unresolved pre-event coordinates")
    unresolved = documents["unresolved_priority_intervals"]
    saved.require(type(unresolved) is list, "pinned unresolved-priority interval list required")
    proof = component_exit_bounds(documents["ordinary_exit_scope"], prepared.selections_d1, prepared.selections_d2,
        prepared.holding_inputs, spec["parameters"], prepared.required_code_dates,
        {row["code"]: row["isin"] for row in prepared.expected_windows}, input_sha256=prepared.input_sha256,
        quotes=quotes, terms=terms, unresolved_priority_intervals=tuple(unresolved))
    _require_raw_potential_scope(quotes, proof, terms.events)
    wrapper = saved.encoded(dict(schema=WRAPPER_SCHEMA, ordinary_exit_scope=documents["ordinary_exit_scope"], component_scope=proof))
    saved.write_exclusive(output / "canonical-terms.json", terms.canonical_json)
    saved.write_exclusive(output / "component-exit-scope.json", wrapper)
    entries = proof["entries"]
    summary = dict(potential_entries=len(entries), entries_by_arm={arm: sum(row["origin"]["arm"] == arm for row in entries) for arm in ("D1", "D2")},
        mode_counts=dict(sorted(Counter(row["mode"] for row in entries).items())),
        reason_counts=dict(sorted(Counter(row["reason"] for row in entries).items())),
        shortened_entries=sum(row["end_on"] < row["original_end_on"] for row in entries),
        original_end_entries=sum(row["end_on"] == row["original_end_on"] for row in entries),
        original_code_dates=len(base.original.requested_coordinates), extension_code_dates=len(base.extension.requested_coordinates),
        full_base_code_dates=len(base.requested_coordinates), raw_code_dates=len(quotes.raw_observations),
        action_windows=len(proof["windows"]), old_basis_checks=len(checks))
    return dict(schema=SCHEMA, status="provisional_raw_component_scope_planned", terms_status="terms_only", summary=summary,
        input_sha256=pins, original_input_sha256=prepared.input_sha256, input_manifest=inputs.manifest,
        activity_snapshot_sha256=prepared.activity_snapshot_sha256, original_read_scope_sha256=prepared.read_scope_sha256,
        original_quote_snapshot_sha256=prepared.quote_snapshot_sha256, extension_quote_snapshot_sha256=base.extension.quote_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, raw_quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        raw_source_artifact_sha256=quotes.source_artifact_sha256, raw_unit_plan_sha256=quotes.unit_plan_sha256,
        terms_sha256=terms.terms_sha256, pre_effective_mark_checks=checks, ordinary_scope_sha256=proof["ordinary_scope_sha256"],
        component_scope_sha256=proof["component_scope_sha256"], raw_verification_summary=receipt["summary"],
        prior_new_response_row_extract_verified=True, prior_cached_body_truth_independently_verified=False,
        prior_cached_row_positions_independently_verified=False, prior_independent_receipts_relied_on=True,
        independent_output_verification="pending", new_source_requests=0, automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0,
        output_sha256={"canonical-terms.json": saved.digest(terms.canonical_json), "component-exit-scope.json": saved.digest(wrapper)},
        **dict.fromkeys(FALSE_FLAGS, False))


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """Freeze source, durably log before 27 reads, retain one exclusive attempt."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "use committed caller from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    saved.require(any(row["path"] == SOURCE_PATH and row["sha256"] == saved.digest(Path(__file__).read_bytes())
        for row in sources["files"]), "caller must belong to committed frozen source")
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    population = Path(spec["inputs"]["bl_population_audit"]["path"])
    inputs = saved.PinnedInputs((lambda path: holding._read_population(path) if path == population else saved.read_private(path))
                               if reader is None else reader)

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
            resources, manifest = saved.encoded(logged._resources(started)), saved.encoded(inputs.manifest)
            saved.write_exclusive(output / "resource-manifest.json", resources)
            saved.write_exclusive(output / "input-manifest.json", manifest)
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(manifest))
            result_raw = saved.encoded(result)
            saved.write_exclusive(output / "result.json", result_raw)
            hashes = {"result.json": saved.digest(result_raw), "specification.json": saved.digest(raw),
                "source-manifest.json": result["source_manifest_sha256"], "input-manifest.json": saved.digest(manifest),
                "resource-manifest.json": saved.digest(resources), **result["output_sha256"]}
            return dict(status=result["status"], output_dir=str(output), summary=result["summary"], result_sha256=hashes["result.json"],
                output_sha256=hashes, terms_sha256=result["terms_sha256"], component_scope_sha256=result["component_scope_sha256"],
                raw_quote_snapshot_sha256=result["raw_quote_snapshot_sha256"], D1_completed_studies=0, D2_completed_studies=0)
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", logged._resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded(dict(status="failed", error_type=type(exc).__name__,
                    actual_inputs_attempted=inputs.manifest, error_frames=[dict(file=Path(frame.filename).name, line=frame.lineno,
                        function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)], source_manifest_sha256=saved.digest(saved.encoded(sources)),
                    specification_sha256=saved.digest(raw), partial_package_preserved=True, automatic_retries=0,
                    D1_completed_studies=0, D2_completed_studies=0)))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw), window=spec["window"],
            runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources, "declared_inputs": spec["inputs"],
                "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("spec", "output-dir", "runs-path"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded(dict(status="failed", error_type=type(exc).__name__, automatic_retries=0)).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
