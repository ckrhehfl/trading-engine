"""One logged BS normal-only assembly and updated metadata frontier.

Reuse exact reviewed BK/BR windows supplied in a new ledger. Restore the saved
BI observations and official normal-source cache; never read holding prices,
execute a book or access a database. New normal exclusions remain explicit and
can expose another unknown prefix, requiring a separate registered operation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path
import re
import resource
import signal
import time
import traceback

from research import activity_cohort_scope_preflight as frontier
from research import activity_normal_preflight as normal
from research import activity_preflight as saved
from research import experiment_log
from research.activity_cohort_selection import CohortSelection, CONTROL_IDS
from research.activity_control_assembly import assemble_control_window
from research.activity_control_connection import ARMS, _keyed
from research.activity_control_inputs_preflight import _source_agreement
from research.activity_control_normal_inputs import assemble_control_normal_inputs
from research.activity_control_selection import population_fingerprint
from research.activity_failure_inputs import restore_activity_failure
from research.activity_failure_preflight import day, plan_windows
from research.activity_holding_preflight import _read_population
from research.activity_normal_sources import load_sources
from research.activity_normal_assembly import accepted_period
from research.activity_partition_selection import _amount
from research.activity_screen import PublishedValue
from research.activity_pair_calibration import PARAMETERS


SCHEMA = "activity-cohort-normal-inputs-v1"
MODE = "discovery_cohort_normal_input_assembly"
SOURCE_PATH = "python/research/activity_cohort_normal_preflight.py"
FIXED_INPUTS = {role: deepcopy(row) for role, row in normal.INPUTS.items() if role != "bk_adjudication"}
FIXED_INPUTS.update({role: deepcopy(frontier.INPUTS[role]) for role in (
    "br_scope_result", "br_population_audit", "br_control_dispositions", "br_normal_input_audit")})
NEW_ROLES = ("bs_scope_result", "bs_scope_verification", "bs_adjudication")
SCOPE_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json", "resource-manifest.json")
OLD_RECEIPT_ROLES = ("br_scope_result", "br_population_audit", "br_control_dispositions", "br_normal_input_audit", "original_activity_replay")
FALSE_FLAGS = ("books_replayed", "returns_computed", "database_access", "api_access", "holding_prices_read",
    "holding_selection_executed", "source_truth_certified", "historical_publication_certified", "historical_eligibility_certified",
    "price_basis_certified", "compulsory_successor_closure_complete", "potential_holding_origins_bound_complete", "control_targets_frozen",
    "promotion_allowed", *frontier.POWER_FLAGS)


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs", "controls", "design", *frontier.POWER_FLAGS}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and all(spec[name] is False for name in frontier.POWER_FLAGS)
        and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW) and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]) == saved.encoded(PARAMETERS)
        and spec["controls"] == list(CONTROL_IDS) and spec["design"] == frontier.DESIGN,
        "fixed BS normal-only specification required")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(declared) == {*FIXED_INPUTS, *NEW_ROLES}
        and all(declared[role] == row for role, row in FIXED_INPUTS.items()), "fixed normal-source inputs required")
    paths = []
    for role, row in declared.items():
        field = "root" if "root" in row else "path"
        saved.require(type(row[field]) is str and Path(row[field]).is_absolute() and ".." not in Path(row[field]).parts,
            "exact absolute normal-source path required")
        paths.append(str(saved._safe_path(Path(row[field]))))
        if role in NEW_ROLES:
            saved.require(set(row) == {"path", "sha256"}, "exact new metadata/adjudication pin required")
            saved._pin(row["sha256"])
    saved.require(len(paths) == len(set(paths)), "normal-source input alias")


def validate_scope(scope: dict, receipt: dict, declared: dict) -> None:
    """Bind the independently verified BS output, including every manifest link."""
    pins = receipt.get("file_sha256")
    saved.require(type(pins) is dict and set(pins) == set(SCOPE_FILES)
        and pins["result.json"] == declared["bs_scope_result"]["sha256"]
        and receipt.get("schema") == "bs-cohort-scope-independent-verification-v1"
        and receipt.get("status") == "verified" and receipt.get("verified") is True
        and type(receipt.get("producer_code_version")) is str
        and re.fullmatch(r"[0-9a-f]{40}", receipt["producer_code_version"]) is not None
        and receipt["producer_code_version"] == receipt.get("expected_commit")
        and all(type(receipt.get(name)) is int and receipt[name] == count for name, count in (
            ("new_output_files_read", 5), ("saved_input_files_read", 5), ("completed_reads", 10),
            ("automatic_retries", 0), ("performance_studies_added", 0)))
        and receipt.get("scope") == "new_metadata_population_targets_salted_prefixes_unknown_tails_and_needed_windows_only"
        and receipt.get("input_sha256") == {role: frontier.INPUTS[role]["sha256"] for role in OLD_RECEIPT_ROLES}
        and receipt.get("summary") == scope.get("summary"), "independent BS scope receipt required")
    for value in pins.values(): saved._pin(value)
    for field, filename in (("specification_sha256", "specification.json"), ("source_manifest_sha256", "source-manifest.json"),
        ("input_manifest_sha256", "input-manifest.json"), ("resource_manifest_sha256", "resource-manifest.json")):
        saved.require(scope.get(field) == pins[filename], "BS scope manifest linkage changed")
    saved.require(scope.get("schema") == frontier.SCHEMA and scope.get("status") == frontier.STATUS
        and scope.get("design") == frontier.DESIGN and scope.get("controls") == list(CONTROL_IDS)
        and scope.get("input_sha256") == {role: row["sha256"] for role, row in frontier.INPUTS.items()}
        and all(scope.get(name) is False for name in ("books_replayed", "returns_computed", "database_access", "api_access",
            "holding_selection_executed", "control_targets_frozen", "potential_holding_origins_bound_complete",
            "compulsory_successor_closure_complete", "promotion_allowed", *frontier.POWER_FLAGS))
        and all(receipt.get(name) is False for name in ("books_replayed", "returns_computed", "original_ACTIVITY_replayed",
            "original_books_or_metrics_inspected", "source_truth_certified", "promotion_allowed", *frontier.POWER_FLAGS))
        and all(type(value.get(name)) is int and value[name] == 0 for value in (scope, receipt)
            for name in ("D1_completed_studies", "D2_completed_studies"))
        and type(scope.get("new_source_requests")) is int and scope["new_source_requests"] == 0
        and type(scope.get("automatic_retries")) is int and scope["automatic_retries"] == 0,
        "BS metadata claim boundaries changed")


def activity_declarations(bs_scope: dict, population: dict, dispositions: list[dict], parameters: dict,
                          *, require_original_partition: bool) -> dict:
    """Keep original targets/times while binding the current complete partition."""
    source, partition = _keyed(population["rows"]), _keyed(dispositions)
    saved.require(source.keys() == partition.keys(), "whole source/normal partition required")
    result = {arm: [] for arm in ARMS}
    formations = tuple(date.fromisoformat(row["formation"]) for row in bs_scope["formations"])
    saved.require(formations == tuple(sorted(set(formations))) and {key[0] for key in source} == set(formations),
        "complete ordered BS formation population required")
    for formation, row in zip(formations, bs_scope["formations"], strict=True):
        identities = tuple(sorted((code, value["isin"]) for (day, code), value in source.items() if day == formation))
        rows = [partition[key] for key in sorted(partition) if key[0] == formation]
        partition_sha = saved.digest(saved.encoded(rows))
        for arm in ARMS:
            old = row["ACTIVITY"][arm]
            saved.require(old["source_population_count"] == len(identities)
                and old["source_population_sha256"] == population_fingerprint(identities)
                and old["target_ceiling"] == len(old["target_codes"]), "original ACTIVITY population or ceiling changed")
            if require_original_partition:
                saved.require(old["source_partition_sha256"] == partition_sha, "original BS partition changed")
            result[arm].append(CohortSelection(group_id="ACTIVITY", arm=arm, formation_on=formation,
                decision_on=date.fromisoformat(old["decision_on"]), selection_at=datetime.fromisoformat(old["selection_at"]),
                seed=parameters["seed"], activity_targets=tuple(old["target_codes"]),
                activity_targets_sha256=old["activity_targets_sha256"], source_population=identities,
                source_population_count=len(identities), source_population_sha256=population_fingerprint(identities),
                source_partition_sha256=partition_sha, resolved_prefix=()))
    return {arm: tuple(values) for arm, values in result.items()}


def adjudication_contexts(ledger: dict, needed: dict) -> dict:
    """Bind one positive source context per exact window, allowing repeated codes.

    A context may cover several disjoint windows. Its own known-on, report and
    passages apply only to those declared windows; another context for the same
    code can use a later report without either backdating or blocking early work.
    """
    records = ledger.get("records") if type(ledger) is dict else None
    saved.require(type(records) is list, "explicit BS adjudication contexts required")
    result = {}
    ref_fields = ("raw_sha256", "readable_sha256", "source_url", "receipt")
    for record in records:
        saved.require(type(record) is dict and record.get("decision") == "accepted"
            and type(record.get("code")) is str and type(record.get("required_windows")) is list
            and bool(record["required_windows"]), "positive bounded BS context required")
        code = record["code"]
        pairs = [record]
        if "source_evidence" in record:
            saved.require(type(record["source_evidence"]) is list and bool(record["source_evidence"]), "explicit context source pairs required")
            pairs.extend(record["source_evidence"])
        known = date.fromisoformat(record["known_on_conservative_candidate"])
        for pair in pairs:
            saved.require(type(pair) is dict and pair.get("code", code) == code
                and all(type(pair.get(name)) is str and bool(pair[name]) for name in ref_fields), "cross-code or incomplete context source reference")
            saved._pin(pair["raw_sha256"]); saved._pin(pair["readable_sha256"])
            for field in ("published_on", "published_on_date_only", "report_publication_date", "source_publication_date"):
                if field in pair:
                    published = date.fromisoformat(pair[field])
                    saved.require(pair[field] in (published.isoformat(), published.strftime("%Y%m%d"))
                        and published <= known, "context source publication backdated")
            if re.fullmatch(r"[0-9]{14}", pair["receipt"]):
                saved.require(date.fromisoformat(pair["receipt"][:8]) <= known, "context dated report receipt backdated")
        for passage in record.get("passages", []):
            saved.require(type(passage) is dict and passage.get("code", code) == code, "cross-code context passage")
            supplied = {name: passage[name] for name in ref_fields if name in passage}
            if supplied:
                saved.require(any(all(pair[name] == value for name, value in supplied.items()) for pair in pairs),
                    "passage source reference outside accepted context")
        for window in record["required_windows"]:
            saved.require(type(window) is dict and type(window.get("formation")) is str, "explicit BS context window required")
            formation = date.fromisoformat(window["formation"])
            key = formation, code
            saved.require(formation.isoformat() == window["formation"] and key in needed and key not in result,
                "BS context window outside exact needed union or overlapping another context")
            target = needed[key]
            saved.require(window.get("krx_metadata_provenance") == target["krx_metadata_provenance"], "context/window source provenance mismatch")
            accepted_period(record, formation=formation, code=code, isin=target["isin"],
                required_dates=tuple(date.fromisoformat(value) for value in target["required_dates"]), evidence_label="BS context")
            result[key] = record
    saved.require(set(result) == set(needed), "BS contexts must cover exactly every needed window")
    return result


def assemble_delta(calendar: tuple[date, ...], original_scope: dict, population: dict,
                   previous_dispositions: list[dict], previous_normal_audits: list[dict], bs_scope: dict,
                   ledger: dict, observations, potential_sources, parameters: dict) -> dict:
    """Recompute exactly the needed union, retaining every known and unknown tail."""
    original = activity_declarations(bs_scope, population, previous_dispositions, parameters, require_original_partition=True)
    expected = frontier.plan_frontiers(original_scope, population, previous_dispositions, previous_normal_audits, original, parameters)
    saved.require(all(saved.encoded(bs_scope.get(name)) == saved.encoded(value) for name, value in expected.items()),
        "recomputed original BS frontier differs")
    del expected, original
    needed = _keyed(bs_scope["needed_normal_windows"], iso=True)
    contexts = adjudication_contexts(ledger, needed)
    screen_params = {**parameters, "threshold": Decimal(str(parameters["threshold"]))}
    # Reuse the existing whole-population/source/timing validator once, without
    # its legacy one-record-per-code accepted-subset contract.
    connected = assemble_control_normal_inputs(calendar, original_scope, population, [], observations, {}, screen_params)
    possible = _keyed([row for formation in original_scope["formations"] for row in formation["ranked_numeric_possible"]])
    source_population = _keyed(population["rows"])
    compact = {(formation.strftime("%Y%m%d"), code) for formation, code in needed}
    saved.require(set(potential_sources) == compact, "exact per-window BS source subset required")
    evaluated = []
    for key, context in sorted(contexts.items()):
        formation, code = key
        candidate, source_row = possible[key], source_population[key]
        source = potential_sources[formation.strftime("%Y%m%d"), code]
        saved.require(source.get("isin") == candidate["isin"] == source_row["isin"]
            and source.get("krx_metadata_provenance") == candidate["krx_metadata_provenance"] == source_row["krx_metadata_provenance"]
            and type(source.get("listing_date")) is date and source["listing_date"].isoformat() == candidate["listing_date"]
            and isinstance(source.get("capitalization"), PublishedValue)
            and source["capitalization"].value == _amount(source_row["capitalization"]), "per-window source issue, listing or capitalization mismatch")
        dates = tuple(date.fromisoformat(value) for value in candidate["required_dates"])
        rows = observations.get(code)
        saved.require(rows is not None and all(value in rows for value in dates), "missing per-window restored activity input")
        audit = assemble_control_window(calendar, formation, code, candidate["isin"], source["listing_date"],
            {value: rows[value] for value in dates}, source["capitalization"], source["liquidity"], context, screen_params)
        saved.require(all(arm["normal_dates"] == candidate["normal_dates"] and arm["skipped_frozen_dates"] == candidate["frozen_dates"]
            for arm in audit["activity_screen"]["arms"].values()), "per-window normal/frozen dates differ from fixed metadata")
        audit.update(formation=formation.isoformat(), code=code, isin=candidate["isin"],
            formation_source_provenance=deepcopy(source["source_provenance"]),
            adjudication_context_sha256=saved.digest(saved.encoded(context)),
            adjudication_context_window_id=f"{formation:%Y%m%d}/{code}")
        evaluated.append(audit)
    previous, prior_audits = _keyed(previous_dispositions), _keyed(previous_normal_audits, iso=True)
    updates = _keyed(connected["dispositions"])
    current = _keyed(evaluated, iso=True)
    saved.require(set(current) == set(needed), "normal assembly must cover exactly all BS needed windows")
    for key, audit in current.items():
        arm = audit["control_arms"]["D1"]
        updates[key].update(normal_input_disposition="eligible" if arm["eligible"] else "excluded",
            normal_input_exclusion_reasons=deepcopy(arm["exclusion_reasons"]))
    for key, audit in prior_audits.items():
        saved.require(key in previous and previous[key]["normal_input_disposition"] in {"eligible", "excluded"}
            and all(audit["control_arms"][arm]["eligible"] == (previous[key]["normal_input_disposition"] == "eligible")
                and audit["control_arms"][arm]["exclusion_reasons"] == previous[key]["normal_input_exclusion_reasons"] for arm in ARMS),
            "previous normal audit/disposition changed")
        if key in current:
            saved.require(saved.encoded(audit["control_arms"]) == saved.encoded(current[key]["control_arms"]),
                "recomputed prior shared-normal eligibility changed")
    merged = []
    for key, row in sorted(previous.items()):
        saved.require(key in updates and all(row[name] == updates[key][name] for name in
            ("formation", "code", "isin", "prior_disposition", "control_disposition")), "full source partition identity changed")
        merged.append(deepcopy(updates[key] if key in needed else row))
    audits = {**prior_audits, **current}
    exclusions = [{"formation": key[0].isoformat(), "code": key[1], "isin": row["isin"],
        "exclusion_reasons": deepcopy(row["normal_input_exclusion_reasons"])} for key, row in sorted(updates.items())
        if key in needed and row["normal_input_disposition"] == "excluded"]
    return {"normal_input_delta": evaluated,
        "normal_input_audit": [audits[key] for key in sorted(audits)], "dispositions": merged,
        "new_normal_exclusions": exclusions, "summary": {"source_rows": len(merged),
            "numeric_possible_windows": connected["summary"]["numeric_possible_windows"],
            "needed_windows_recomputed": len(current), "merged_normal_audit_windows": len(audits),
            "adjudication_contexts": len(ledger["records"]),
            "preserved_prior_normal_windows": len(prior_audits.keys() - current.keys()),
            "normal_disposition_counts": dict(sorted(Counter(row["normal_input_disposition"] for row in merged).items())),
            "unresolved_numeric_windows": sum(row["control_disposition"] == "numeric_possible"
                and row["normal_input_disposition"] == "unresolved" for row in merged)}}


def updated_frontier(original_scope: dict, population: dict, bs_scope: dict, delta: dict, parameters: dict) -> dict:
    declarations = activity_declarations(bs_scope, population, delta["dispositions"], parameters, require_original_partition=False)
    result = frontier.plan_frontiers(original_scope, population, delta["dispositions"], delta["normal_input_audit"], declarations, parameters)
    result.update(previous_scope_sha256=saved.digest(saved.encoded(bs_scope)),
        normal_input_audit_sha256=saved.digest(saved.encoded(delta["normal_input_audit"])),
        control_dispositions_sha256=saved.digest(saved.encoded(delta["dispositions"])),
        new_normal_exclusions=deepcopy(delta["new_normal_exclusions"]), independent_output_verification="pending")
    return result


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    declared = spec["inputs"]
    def load(role):
        row = declared[role]
        return inputs.json(Path(row["path"]), row["sha256"], role)
    bs_scope, receipt = load("bs_scope_result"), load("bs_scope_verification")
    validate_scope(bs_scope, receipt, declared)
    del receipt
    original_scope, population, previous, prior_audits = (load(role) for role in (
        "br_scope_result", "br_population_audit", "br_control_dispositions", "br_normal_input_audit"))
    values = {role: load(role) for role in ("calendar_manifest", "bb_result", "bh_result", "au_result", "bi_result")}
    calendar_strings, _, schedule = saved.calendar_schedule(values.pop("calendar_manifest"), spec["parameters"])
    calendar = tuple(day(value) for value in calendar_strings)
    saved.require(original_scope["paired_schedule"] == schedule, "original normal calendar changed")
    bi = values.pop("bi_result")
    activity_sha = bi["scan_snapshot"]["dataset_sha256"]
    saved.require(bs_scope["activity_snapshot_sha256"] == original_scope["activity_snapshot_sha256"] == activity_sha,
        "unchanged BI activity snapshot required")
    bb_windows = saved._bb_windows(values.pop("bb_result"), calendar_strings)[0]
    windows, requested = plan_windows(bb_windows, values.pop("bh_result")["coverage"]["windows"],
        values.pop("au_result")["rows"], calendar_strings)
    del values, bb_windows
    ledger = load("bs_adjudication")
    needed = {(date.fromisoformat(row["formation"]).strftime("%Y%m%d"), row["code"]) for row in bs_scope["needed_normal_windows"]}
    contexts = adjudication_contexts(ledger, _keyed(bs_scope["needed_normal_windows"], iso=True))
    read_scope = {"schema": SCHEMA + ":normal-read-scope-v1", "previous_BS_scope": declared["bs_scope_result"],
        "accepted_adjudication": declared["bs_adjudication"], "needed_windows": [list(key) for key in sorted(needed)],
        "adjudication_context_windows": [{"formation": formation.isoformat(), "code": code,
            "context_sha256": saved.digest(saved.encoded(context)), **{name: context[name] for name in (
                "known_on_conservative_candidate", "raw_sha256", "readable_sha256", "source_url", "receipt")}}
            for (formation, code), context in sorted(contexts.items())],
        "restored_activity_source": declared["bi_result"], "upstream_dataset_sha256": activity_sha,
        "typed_OHLC_fingerprint_recomputed": False,
        "requested_original_BI_code_dates": {code: [value.isoformat() for value in dates] for code, dates in requested.items()},
        "database_access": False, "holding_prices_read": False, "returns_computed": False}
    read_raw = saved.encoded(read_scope)
    saved.write_exclusive(output / "normal-read-scope.json", read_raw)
    restored = restore_activity_failure(bi["scan_snapshot"], calendar, requested)
    proofs = normal.replay_proofs(windows, restored, bi, calendar, spec["parameters"])
    del bi, windows, requested
    sources = load_sources(declared, inputs, schedule, calendar, needed, output, expected_window_count=len(needed)) if needed else {"potential_sources": {}}
    raw_source_scope_sha = sources.get("raw_source_read_scope_sha256")
    if needed:
        saved._pin(raw_source_scope_sha)
        _source_agreement(sources.pop("population"), population)
    delta = assemble_delta(calendar, original_scope, population, previous, prior_audits, bs_scope, ledger,
        restored.observations, sources["potential_sources"], spec["parameters"])
    del restored, sources, ledger, previous, prior_audits
    hashes = {}
    for filename, value in (("normal-input-delta.json", delta["normal_input_delta"]),
        ("normal-input-audit.json", delta["normal_input_audit"]), ("control-dispositions.json", delta["dispositions"]),
        ("normal-exclusions.json", delta["new_normal_exclusions"]), ("restored-activity-proof-audit.json", {
            "upstream_dataset_sha256": activity_sha, "typed_OHLC_fingerprint_recomputed": False, "replayed_proofs": proofs})):
        raw = saved.encoded(value); saved.write_exclusive(output / filename, raw); hashes[filename] = saved.digest(raw)
    planned = updated_frontier(original_scope, population, bs_scope, delta, spec["parameters"])
    planned_raw = saved.encoded(planned)
    saved.write_exclusive(output / "frontier.json", planned_raw); hashes["frontier.json"] = saved.digest(planned_raw)
    return {"schema": SCHEMA, "status": "cohort_normal_inputs_and_frontier_assembled", "summary": {
        **delta["summary"], "needed_windows_recomputed": len(delta["normal_input_delta"]),
        "new_normal_exclusions": len(delta["new_normal_exclusions"]), "updated_frontier": planned["summary"]},
        "artifact_sha256": hashes, "normal_read_scope_sha256": saved.digest(read_raw),
        "raw_source_read_scope_sha256": raw_source_scope_sha,
        "input_sha256": {role: row["sha256"] for role, row in declared.items() if "sha256" in row},
        "activity_snapshot_sha256": activity_sha, "typed_OHLC_fingerprint_recomputed": False,
        "complete_target_prefix": planned["complete_target_prefix"], "independent_output_verification": "pending",
        "classification_evidence_level": "inferred", "historical_publication_evidence_level": "assumed",
        "new_source_requests": 0, "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0,
        **dict.fromkeys(FALSE_FLAGS, False)}


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "wall_limit_seconds": 300}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None, freeze=None, evaluator=None) -> dict:
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "run committed BS normal source from root")
    freezer = saved.freeze_sources if freeze is None else freeze
    evaluate_saved = evaluate_inputs if evaluator is None else evaluator
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive BS normal output already exists")
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
            resources = saved.encoded(_resources(started)); manifest = saved.encoded(inputs.manifest)
            saved.write_exclusive(output / "resource-manifest.json", resources); saved.write_exclusive(output / "input-manifest.json", manifest)
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(manifest))
            body = saved.encoded(result); saved.write_exclusive(output / "result.json", body)
            return {"status": result["status"], "output_dir": str(output), "result_sha256": saved.digest(body), "summary": result["summary"],
                **{key: result[key] for key in ("source_manifest_sha256", "specification_sha256", "resource_manifest_sha256", "input_manifest_sha256",
                    "D1_completed_studies", "D2_completed_studies")}}
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
    finally: os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True); args = parser.parse_args(argv)
    def expired(signum, frame): raise TimeoutError("registered BS normal wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2,) * 2); resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    signal.signal(signal.SIGALRM, expired); signal.alarm(300)
    try: record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as error:
        print(saved.encoded({"status": "failed", "error_type": type(error).__name__, "automatic_retries": 0}).decode()); return 1
    finally: signal.alarm(0)
    print(saved.encoded(record["result"]).decode()); return 0


if __name__ == "__main__": raise SystemExit(main())
