"""Logged provisional ordinary-exit scope over verified saved packages only.

This child preflight reads no database, API or original source body and runs no
book or return calculation. It preserves the original full BM quote snapshot;
its separate price-only child scope supplies no action or exit certificate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import resource
import time
import traceback

from research import activity_holding_preflight as holding
from research import activity_preflight as saved
from research import experiment_log
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_paired_replay import BM_ROLES, PACKAGE_ROLES, prepare_inputs


SCHEMA = "activity-ordinary-exit-preflight-v1"


def validate_spec(spec: dict, reference: dict) -> None:
    """Freeze the exact saved-input roles and reference parameter representations."""
    saved.require(type(spec) is dict and set(spec) == {
        "schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs"}
        and spec["schema"] == spec["study_id"] == SCHEMA
        and spec["mode"] == "discovery_conditional_exit_scope"
        and spec["promotion_allowed"] is False and spec["window"] == saved.WINDOW
        and spec["reference_specification"] == saved.REFERENCE
        and type(spec["parameters"]) is dict
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]),
        "fixed ordinary-exit specification mismatch")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(declared) == set(PACKAGE_ROLES),
                  "exact eighteen saved-input roles required")
    paths = []
    for row in declared.values():
        saved.require(type(row) is dict and set(row) == {"path", "sha256"}
                      and type(row["path"]) is str and Path(row["path"]).is_absolute(),
                      "exact absolute pinned input declaration required")
        saved._pin(row["sha256"])
        paths.append(row["path"])
    saved.require(len(paths) == len(set(paths)), "saved input paths must be distinct")
    for roles in (holding.BL_ROLES, BM_ROLES):
        saved.require(len({str(Path(declared[role]["path"]).parent) for role in roles}) == 1
                      and all(Path(declared[role]["path"]).name == name for role, name in roles.items()),
                      "each saved package must retain its exact filenames and directory")


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    """Read all registered bytes after durable start, restore, then plan only."""
    raw, pins = {}, {}
    for role in PACKAGE_ROLES:
        row = spec["inputs"][role]
        pins[role] = row["sha256"]
        raw[role] = inputs.read(Path(row["path"]), pins[role], role)
    bl = {"files": {name: raw[role] for role, name in holding.BL_ROLES.items()},
          "verification": raw["bl_verification"], "bj_scope": raw["bj_scope"]}
    bm = {"files": {name: raw[role] for role, name in BM_ROLES.items()},
          "verification": raw["bm_verification"]}
    prepared = prepare_inputs(bl, bm, parameters=spec["parameters"], expected_pins=pins)
    issue_isins = {}
    for row in prepared.expected_windows:
        saved.require(row["code"] not in issue_isins or issue_isins[row["code"]] == row["isin"],
                      "conflicting original issue identity")
        issue_isins[row["code"]] = row["isin"]
    plan = provisional_exit_bounds(prepared.selections_d1, prepared.selections_d2,
        prepared.holding_inputs, spec["parameters"], prepared.required_code_dates,
        issue_isins, input_sha256=prepared.input_sha256)
    outcome = saved.encoded(plan)
    saved.write_exclusive(output / "exit-scope.json", outcome)
    summary = {"potential_entries": len(plan["entries"]),
        "entries_by_arm": {arm: sum(row["arm"] == arm for row in plan["entries"]) for arm in ("D1", "D2")},
        "provisional_candidates": sum(row["candidate_on"] is not None for row in plan["entries"]),
        "state_counts": dict(sorted(Counter(row["state"] for row in plan["entries"]).items())),
        "original_codes": len(prepared.required_code_dates),
        "original_code_dates": sum(map(len, prepared.required_code_dates.values())),
        "child_codes": len(plan["child_code_dates"]),
        "child_code_dates": sum(map(len, plan["child_code_dates"].values()))}
    return {"schema": SCHEMA, "status": "provisional_exit_scope_planned", "summary": summary,
        "activity_snapshot_sha256": prepared.activity_snapshot_sha256,
        "quote_snapshot_sha256": prepared.quote_snapshot_sha256,
        "original_read_scope_sha256": prepared.read_scope_sha256,
        "input_sha256": prepared.input_sha256, "input_manifest": inputs.manifest,
        "exit_scope_sha256": saved.digest(outcome), "child_scope_sha256": plan["child_scope_sha256"],
        "new_source_requests": 0, "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0,
        "database_access": False, "api_access": False, "returns_computed": False, "books_computed": False,
        "actions_applied": False, "promotion_allowed": False, "source_truth_certified": False,
        "event_coverage_certified": False, "no_event_coverage_certified": False,
        "price_basis_certified": False, "actual_exits_certified": False, "historical_vintage_certified": False}


def _resources(started: float) -> dict:
    """Record Linux process resource observations without imposing new limits."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started,
        "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU))}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """Log one attempt before saved inputs; preserve every private partial output."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    population_path = Path(spec["inputs"]["bl_population_audit"]["path"])

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
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)),
                specification_sha256=saved.digest(raw), resource_manifest_sha256=saved.digest(resources))
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return {"status": result["status"], "output_dir": str(output), "summary": result["summary"],
                "result_sha256": saved.digest(saved.encoded(result)),
                **{key: result[key] for key in ("exit_scope_sha256", "child_scope_sha256",
                    "resource_manifest_sha256", "source_manifest_sha256")},
                "input_manifest_sha256": saved.digest(saved.encoded(inputs.manifest)),
                "D1_completed_studies": 0, "D2_completed_studies": 0}
        except BaseException as exc:
            if created:
                if not (output / "resource-manifest.json").exists():
                    saved.write_exclusive(output / "resource-manifest.json", saved.encoded(_resources(started)))
                if not (output / "input-manifest.json").exists():
                    saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "actual_inputs_attempted": inputs.manifest,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno,
                        "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)],
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)),
                    "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0}))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    """Run the registered child preflight and print only its completion receipt."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded({"status": "failed", "error_type": type(exc).__name__, "automatic_retries": 0}).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
