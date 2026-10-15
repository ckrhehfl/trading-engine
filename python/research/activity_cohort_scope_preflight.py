"""Log the initial BS independent-cohort eligibility frontier from saved metadata.

Validate the original BR partition and saved ACTIVITY pair without replaying a
book. Unknown normal candidates count only as potential ranks; they never become
exclusions or final targets. This scope closes no holding or action obligation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import os
from pathlib import Path
import resource
import signal
import time
import traceback

from research import activity_control_preflight as original
from research import activity_preflight as saved
from research import experiment_log
from research.activity_cohort_selection import CONTROL_IDS, CohortSelection, rank_control_codes
from research.activity_control_connection import ARMS, _keyed, counted_selections_from_br
from research.activity_holding_preflight import _read_population
from research.activity_pair_calibration import PARAMETERS


SCHEMA = "activity-cohort-scope-v1"
MODE = "discovery_equal_capital_cohort_metadata"
DESIGN = "independent_formation_equal_assigned_capital"
STATUS = "independent_cohort_frontier_metadata_planned"
SOURCE_PATH = "python/research/activity_cohort_scope_preflight.py"
POWER_FLAGS = original.POWER_FLAGS
BR_ROOT = "/home/minjun4897/research-evidence/large-liquid-br-control-inputs-recovery-20261015-v1/run"
BQ_ROOT = "/home/minjun4897/research-evidence/large-liquid-bq-raw-paired-20261010-v1/run"
COVERAGE_ROOT = "/home/minjun4897/research-evidence/large-liquid-br-control-holding-coverage-20261015-v1"
# Fixed identities copied from the tracked BR comparison specification. That
# specification is not opened at runtime and introduces no undeclared input.
INPUTS = {
    "basis_evidence": {"path": COVERAGE_ROOT + "/basis-evidence.json",
        "sha256": "e7add016921a8710ee2701961f3d28b8e8ce9bc2a208fea1867fa136d9f9e1b0"},
    "br_control_dispositions": {"path": BR_ROOT + "/control-dispositions.json",
        "sha256": "742a00f1f0b76894351265e6f4d56bc3d0a3ed89a0822c56ccb811ef32b11701"},
    "br_holding_input_audit": {"path": BR_ROOT + "/holding-input-audit.json",
        "sha256": "753dd22b6f3d3a558790ff88bd6d2da6cd229dfc03b279d7d9dce0138298f70e"},
    "br_normal_input_audit": {"path": BR_ROOT + "/normal-input-audit.json",
        "sha256": "ab2a5cd9c4ed00b2574f41a954440f8d5ac8e1036d1d1dd50a28f533808f78fe"},
    "br_population_audit": {"path": BR_ROOT + "/population-audit.json",
        "sha256": "20c126919c3b2a5c9c911b0bf371bc1c99f78727c134fbea807fef1db62dca76"},
    "br_provisional_exit_scope": {"path": BR_ROOT + "/provisional-exit-scope.json",
        "sha256": "5b0d54705b9e128acb069292d2fbe09bc18dcc46a08ca515068ace134f1f6154"},
    "br_read_scope": {"path": BR_ROOT + "/read-scope.json",
        "sha256": "abf66d0b1a95729007303149f72712cbf9c603e268946cccf1f74b7053c6ea6b"},
    "br_result": {"path": BR_ROOT + "/result.json",
        "sha256": "ad2fd1a3bc75acccf51a2034987a18f1207966839d5865f32596dfc306dadf5e"},
    "br_scope_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-br-control-scope-20261015-v1/run/result.json",
        "sha256": "9b808013d51c893af4ccb66662ba75e4756b12d42d99d80ebae3da7e2cf6bb90"},
    "br_verification": {
        "path": "/home/minjun4897/research-evidence/large-liquid-br-control-inputs-independent-recovery-20261015-v1/verification.json",
        "sha256": "d21d5bcd6a36c0a597b7109bf0143cab781d12aacceb03c003c40c516f7d4ca9"},
    "control_bounds": {"path": COVERAGE_ROOT + "/control-bounds.json",
        "sha256": "28a21bfa879d8b4c3ead7489e23dc69ebb94bf7bc08bac2774adc559e57ab5fc"},
    "original_activity_replay": {"path": BQ_ROOT + "/paired-replay.json",
        "sha256": "d0bf2992e7c5b7a0d2ec962d12dbd52b12ed23c82792023f571c909b8e74eef2"},
    "original_activity_report": {"path": BQ_ROOT + "/report.json",
        "sha256": "d1993f2705e7fb3258a52db1f709300aba61701eda2dcc8fbfcd804ee0c4211c"},
    "original_activity_specification": {"path": BQ_ROOT + "/specification.json",
        "sha256": "513b0472b3f406134a3c685df512cb0070df0f5dac99cee65c2f18555eba8cfc"},
    "original_activity_verification": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bq-raw-paired-independent-20261014-recovery-v1/verification.json",
        "sha256": "6cdace23457d4f4382f2c6dcee4cd139ed03638eaee321b1ab70f858247d127f"},
    "reviewed_action_coverage": {"path": COVERAGE_ROOT + "/reviewed-action-coverage.json",
        "sha256": "8c5ef50d8b3832d1068e60d43ad0a6f37863f03546f11216e85e82ebaa617dbb"},
}
UNREAD_ROLES = ("br_holding_input_audit", "basis_evidence", "reviewed_action_coverage")
READ_ROLES = tuple(role for role in original.ROLES if role not in UNREAD_ROLES)
ORIGINAL_ROLES = tuple(role for role in READ_ROLES if role.startswith("original_activity_"))
WINDOW_FIELDS = ("activity_reason", "first_required_observation", "normal_candidate_start",
    "required_dates", "normal_dates", "frozen_dates", "formation_state", "listing_date",
    "selection_at", "krx_metadata_provenance", "source_metadata_refs")


def validate_spec(spec: dict, reference: dict) -> None:
    """Freeze five independent IDs, unchanged parameters and all sixteen pins."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode",
        "promotion_allowed", "window", "reference_specification", "parameters", "inputs",
        "controls", "design", *POWER_FLAGS}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and all(spec[key] is False for key in POWER_FLAGS)
        and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW)
        and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])
        == saved.encoded(PARAMETERS) and spec["controls"] == list(CONTROL_IDS)
        and spec["design"] == DESIGN and spec["inputs"] == INPUTS,
        "fixed BS cohort metadata specification mismatch")


def plan_frontiers(scope: dict, population_audit: dict, dispositions: list[dict],
                   normal_audits: list[dict], selections: dict, parameters: dict) -> dict:
    """Pure metadata projection after the complete BR consistency validation.

    Every formation starts empty. An unknown rank is a possible eligible code,
    so an initial prefix can stop there but cannot freeze targets. A separately
    logged recomputation must advance it if later evidence proves exclusions.
    """
    saved.require(set(selections) == set(ARMS), "both verified original target schedules required")
    formations = tuple(row.formation_on for row in selections["D1"])
    saved.require(formations == tuple(row.formation_on for row in selections["D2"])
        and len(formations) == len(set(formations)), "complete paired formation schedule required")
    population = _keyed(population_audit["rows"])
    partition, audited = _keyed(dispositions), _keyed(normal_audits, iso=True)
    saved.require(population.keys() == partition.keys() and audited.keys() <= partition.keys()
        and {key[0] for key in population} <= set(formations), "complete source partition required")
    scoped = {date.fromisoformat(row["formation"]): row for row in scope["formations"]}
    saved.require(tuple(scoped) == formations, "complete ordered scope formations required")
    metadata = {}
    for formation, row in scoped.items():
        for candidate in row["ranked_numeric_possible"]:
            key = formation, candidate["code"]
            saved.require(key not in metadata and key in partition
                and candidate["isin"] == partition[key]["isin"], "numeric window identity mismatch")
            metadata[key] = candidate
    saved.require(set(metadata) == {key for key, row in partition.items()
        if row["control_disposition"] == "numeric_possible"}, "complete numeric metadata population required")
    projected, evidence = [], {}
    for key, row in sorted(partition.items()):
        saved.require(row["isin"] == population[key]["isin"]
            and row["normal_input_disposition"] in {"eligible", "excluded", "unresolved"},
            "source identity and explicit normal disposition required")
        pin = saved.digest(saved.encoded({"disposition": row, "normal_input_audit": audited.get(key)}))
        evidence[key] = pin
        projected.append({name: deepcopy(row[name]) for name in ("formation", "code", "isin",
            "prior_disposition", "control_disposition", "normal_input_disposition", "normal_input_exclusion_reasons")}
            | {"normal_evidence_sha256": pin})
    needed, planned, prefix_unknowns = {}, [], set()

    def need(key, reason):
        saved.require(key in metadata and partition[key]["normal_input_disposition"] != "excluded",
            "potential normal window outside complete numeric metadata")
        if key not in needed:
            row = partition[key]
            needed[key] = {"formation": key[0].isoformat(), "code": key[1], "isin": row["isin"],
                "normal_input_disposition": row["normal_input_disposition"],
                "normal_evidence_sha256": evidence[key], "needed_by": [],
                "positive_operating_normal_adjudication_required": row["normal_input_disposition"] == "unresolved",
                **{name: deepcopy(metadata[key][name]) for name in WINDOW_FIELDS}}
        if reason not in needed[key]["needed_by"]:
            needed[key]["needed_by"].append(reason)

    for index, formation in enumerate(formations):
        rows = {code: row for (day, code), row in partition.items() if day == formation}
        identities = tuple(sorted((code, row["isin"]) for code, row in rows.items()))
        activity, controls = {}, {control_id: {} for control_id in CONTROL_IDS}
        for arm in ARMS:
            old = selections[arm][index]
            saved.require(old.source_population == identities, "original full source population changed")
            original_target = CohortSelection(group_id="ACTIVITY", arm=arm,
                formation_on=formation, decision_on=old.decision_on, selection_at=old.selection_at,
                seed=old.seed, activity_targets=old.activity_targets,
                activity_targets_sha256=old.activity_targets_sha256, source_population=old.source_population,
                source_population_count=old.source_population_count,
                source_population_sha256=old.source_population_sha256,
                source_partition_sha256=old.source_partition_sha256, resolved_prefix=())
            targets, k = original_target.activity_targets, original_target.target_ceiling
            activity[arm] = {"decision_on": old.decision_on.isoformat(), "selection_at": old.selection_at.isoformat(),
                "target_codes": list(targets), "target_ceiling": k,
                "activity_targets_sha256": old.activity_targets_sha256,
                "source_population_count": old.source_population_count,
                "source_population_sha256": old.source_population_sha256,
                "source_partition_sha256": old.source_partition_sha256,
                "assigned_capital_fraction": {"numerator": k, "denominator": parameters["slots"]}}
            for code in targets:
                need((formation, code), f"ACTIVITY/{arm}")
            for control_id in CONTROL_IDS:
                ranked = rank_control_codes(tuple(rows), control_id=control_id,
                    formation_on=formation, seed=parameters["seed"])
                prefix, potential, unknown, excluded = [], 0, [], []
                if k:
                    for rank, code in enumerate(ranked, 1):
                        row, key = rows[code], (formation, code)
                        state = row["normal_input_disposition"]
                        prefix.append({"source_rank": rank, "code": code, "isin": row["isin"],
                            "ordering_sha256": hashlib.sha256(
                                f"{parameters['seed']}:BS-v1:{control_id}:{formation}:{code}".encode()).hexdigest(),
                            "normal_input_disposition": state, "normal_evidence_sha256": evidence[key]})
                        if state == "excluded":
                            excluded.append(code)
                        else:
                            potential += 1
                            need(key, f"{control_id}/{arm}")
                            if state == "unresolved":
                                unknown.append(code)
                                prefix_unknowns.add(key)
                        if potential == k:
                            break
                complete = not unknown
                controls[control_id][arm] = {"target_ceiling": k, "prefix": prefix,
                    "potentially_eligible_codes": [row["code"] for row in prefix
                        if row["normal_input_disposition"] != "excluded"],
                    "known_excluded_codes": excluded, "unknown_codes": unknown,
                    "complete_target_prefix": complete,
                    "target_codes": [row["code"] for row in prefix
                        if row["normal_input_disposition"] == "eligible"] if complete else None,
                    "population_exhausted": len(prefix) == len(ranked),
                    "unknown_tail_count": sum(rows[code]["normal_input_disposition"] == "unresolved"
                        for code in ranked[len(prefix):]),
                    "assigned_capital_fraction": {"numerator": k, "denominator": parameters["slots"]}}
        planned.append({"formation": formation.isoformat(), "ACTIVITY": activity, "controls": controls})
    windows = [needed[key] for key in sorted(needed)]
    state_counts = dict(sorted(Counter(row["normal_input_disposition"] for row in projected).items()))
    complete = all(row["normal_input_disposition"] != "unresolved" for row in windows)
    return {"schema": SCHEMA, "status": STATUS, "design": DESIGN, "controls": list(CONTROL_IDS),
        "ordering_policy": "sha256(seed:BS-v1:control_id:ISO-formation:code), then code; whole source population",
        "frontier_policy": "first k eligible-or-unresolved candidates including every preceding known exclusion; no prior-held exclusion",
        "formations": planned, "population_partition": projected, "needed_normal_windows": windows,
        "activity_snapshot_sha256": scope["activity_snapshot_sha256"],
        "summary": {"formations": len(planned), "controls": len(CONTROL_IDS), "source_rows": len(projected),
            "normal_disposition_counts": state_counts, "needed_normal_windows": len(windows),
            "reused_normal_windows": sum(row["normal_input_disposition"] == "eligible" for row in windows),
            "unresolved_normal_windows": sum(row["normal_input_disposition"] == "unresolved" for row in windows),
            "unknown_prefix_windows": len(prefix_unknowns), "complete_target_prefix": complete},
        "complete_target_prefix": complete, "frontier_complete": complete,
        "control_targets_frozen": False, "potential_holding_origins_bound_complete": False,
        "compulsory_successor_closure_complete": False, "historical_eligibility_certified": False,
        "source_truth_certified": False, "event_coverage_certified": False,
        "price_basis_certified": False, "holding_selection_executed": False,
        "books_replayed": False, "returns_computed": False, "original_ACTIVITY_replayed": False,
        "database_access": False, "api_access": False, "original_source_bodies_read": False,
        "new_source_requests": 0, "automatic_retries": 0, "promotion_allowed": False,
        **dict.fromkeys(POWER_FLAGS, False), "D1_completed_studies": 0, "D2_completed_studies": 0,
        "remaining_obligations": ["positive operating-period and normal adjudication for exact unresolved windows",
            "separately logged prefix recomputation after new exclusions", "reviewed holding/action/successor scope before books"]}


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs) -> dict:
    """Thirteen saved roles only, after durable start; no holding audit or replay."""
    pins = {role: row["sha256"] for role, row in spec["inputs"].items()}
    def load(role):
        return inputs.json(Path(spec["inputs"][role]["path"]), pins[role], role)
    documents = {role: load(role) for role in READ_ROLES
        if role not in ("br_population_audit", *ORIGINAL_ROLES)}
    original._verify_br(documents, pins)
    scope = documents["br_scope_result"]
    calendar = tuple(date.fromisoformat(day) for day in documents["br_read_scope"]["calendar_dates"])
    bounds = original._bounds(documents.pop("control_bounds"), spec["parameters"],
        documents["br_provisional_exit_scope"]["calendar_sha256"], pins["reviewed_action_coverage"])
    population = load("br_population_audit")
    selections = counted_selections_from_br(calendar, spec["parameters"], scope, population,
        documents["br_control_dispositions"], documents["br_normal_input_audit"], bounds)
    del bounds
    result = plan_frontiers(scope, population, documents["br_control_dispositions"],
        documents["br_normal_input_audit"], selections, spec["parameters"])
    activity_sha = documents["br_result"]["activity_snapshot_sha256"]
    del population, scope
    documents = {role: load(role) for role in ORIGINAL_ROLES}
    original._verify_original(documents, pins, calendar, activity_sha)
    replay = documents["original_activity_replay"]
    for arm in ARMS:
        saved.require(replay["replays"][arm]["selections"] == [
            [row.formation_on.isoformat(), list(row.activity_targets)] for row in selections[arm]],
            "BR target ceilings differ from unchanged original ACTIVITY declarations")
    result.update(input_sha256=pins, declared_unread_roles=list(UNREAD_ROLES),
        original_BR_partition_consistency_verified=True, original_ACTIVITY_saved_metrics_verified=True,
        independent_output_verification="pending")
    return result


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "wall_limit_seconds": 300}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *,
                  reader=None, freeze=None, evaluator=None) -> dict:
    """Retain one metadata started/completed-or-failed trial without retry.

    Local injected seams support fabricated test packages without changing
    shared source-freezing or package-validation functions.
    """
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH,
        "run committed BS source from repository root")
    freezer = saved.freeze_sources if freeze is None else freeze
    evaluate_saved = evaluate_inputs if evaluator is None else evaluator
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive BS output already exists")
    population_path = Path(spec["inputs"]["br_population_audit"]["path"])
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
            result = evaluate_saved(spec, inputs)
            after, after_raw, _ = freezer(root, spec_path, spec_validator=validate_spec)
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
                    "partial_package_preserved": True, "automatic_retries": 0,
                    "books_replayed": False, "returns_computed": False, "D1_completed_studies": 0, "D2_completed_studies": 0}))
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0,
                "mode": MODE, "design": DESIGN, "controls": list(CONTROL_IDS),
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
        raise TimeoutError("registered BS metadata wall limit exceeded")
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
