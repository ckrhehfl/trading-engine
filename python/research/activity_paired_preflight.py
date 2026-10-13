"""One logged descriptive D1/D2 paired execution over pinned reviewed inputs.

Reuse only existing package/action validators, books and descriptive reporting.
The committed specification pins the exact input bytes before any actual read.
New-output independent verification remains separate from execution completion.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import resource
import time
import traceback

from research import activity_holding_preflight as holding
from research import activity_paired_replay as paired
from research import activity_preflight as saved
from research import experiment_log


SCHEMA = "activity-paired-preflight-v1"
SOURCE_PATH = "python/research/activity_paired_preflight.py"
MODE = "discovery_descriptive_timing_sensitivity"
BASE_ROLES = paired.PACKAGE_ROLES + paired.ACTION_ROLES
OPTIONAL_ROLES = (paired.CONDITIONAL_ROLE, paired.EXTENSION_ROLE, *paired.RAW_ROLES)


def validate_spec(spec: dict, reference: dict) -> None:
    """Keep reference parameter values AND JSON representations unchanged."""
    saved.require(type(spec) is dict and set(spec) == {
        "schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs"}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and spec["window"] == saved.WINDOW
        and spec["reference_specification"] == saved.REFERENCE
        and type(spec["parameters"]) is dict
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]),
        "fixed descriptive paired specification mismatch")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(BASE_ROLES) <= set(declared)
        and set(declared) <= set(BASE_ROLES + OPTIONAL_ROLES),
        "exact eighteen package and two action roles plus declared optional roles required")
    saved.require(not set(paired.RAW_ROLES) & set(declared)
        or set(OPTIONAL_ROLES) <= set(declared),
        "raw projection requires both raw artifacts, full extension and component proof")
    paths = []
    for row in declared.values():
        saved.require(type(row) is dict and set(row) == {"path", "sha256"}
            and type(row["path"]) is str and Path(row["path"]).is_absolute(),
            "exact absolute pinned input declaration required")
        saved._pin(row["sha256"])
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "saved input paths must be distinct")
    for roles in (holding.BL_ROLES, paired.BM_ROLES):
        saved.require(len({str(Path(declared[role]["path"]).parent) for role in roles}) == 1
            and all(Path(declared[role]["path"]).name == name for role, name in roles.items()),
            "each saved package must retain its exact filenames and directory")


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    """Read once after start; the pure caller validates raw/component semantics."""
    roles = BASE_ROLES + tuple(role for role in OPTIONAL_ROLES if role in spec["inputs"])
    pins = {role: spec["inputs"][role]["sha256"] for role in roles}
    raw = {role: inputs.read(Path(spec["inputs"][role]["path"]), pins[role], role) for role in roles}
    bl = {"files": {name: raw[role] for role, name in holding.BL_ROLES.items()},
          "verification": raw["bl_verification"], "bj_scope": raw["bj_scope"]}
    bm = {"files": {name: raw[role] for role, name in paired.BM_ROLES.items()},
          "verification": raw["bm_verification"]}
    replay = paired.paired_replay(bl, bm, raw["reviewed_action_coverage"], raw["basis_evidence"],
        parameters=spec["parameters"], expected_pins=pins,
        conditional_exit_scope=raw.get(paired.CONDITIONAL_ROLE), quote_extension=raw.get(paired.EXTENSION_ROLE),
        raw_episode_quotes=raw.get(paired.RAW_ROLES[0]), raw_episode_unit_plan=raw.get(paired.RAW_ROLES[1]))
    # The pure helper's actual_study_completed=False remains untouched.
    replay_raw, report_raw = saved.encoded(replay), saved.encoded(replay["report"])
    saved.write_exclusive(output / "paired-replay.json", replay_raw)
    saved.write_exclusive(output / "report.json", report_raw)
    return {"schema": SCHEMA, "status": "descriptive_timing_pair_executed",
        "paired_replay_sha256": saved.digest(replay_raw), "report_sha256": saved.digest(report_raw),
        "input_sha256": pins, "input_manifest": inputs.manifest,
        **{key: replay[key] for key in ("activity_snapshot_sha256", "quote_snapshot_sha256",
            "original_quote_snapshot_sha256", "read_scope_sha256", "action_scope_sha256")},
        "books_computed": True, "returns_computed": True,
        "independent_output_verification": "pending", "promotion_allowed": False,
        "source_truth_certified": False, "event_coverage_certified": False,
        "price_basis_certified": False, "historical_vintage_certified": False,
        "database_access": False, "api_access": False, "original_source_bodies_read": False,
        "new_source_requests": 0, "automatic_retries": 0}


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU))}


def run_paired(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """One attempt; completion requires both books/report AND final source check."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    saved.require(Path(__file__).resolve() == root / SOURCE_PATH,
        "caller must use committed source location before saved-input reads")
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
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)),
                specification_sha256=saved.digest(raw), resource_manifest_sha256=saved.digest(resources),
                input_manifest_sha256=saved.digest(saved.encoded(inputs.manifest)),
                logged_paired_execution_completed=True, D1_completed_studies=1, D2_completed_studies=1)
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return {"status": result["status"], "output_dir": str(output),
                "result_sha256": saved.digest(saved.encoded(result)),
                **{key: result[key] for key in ("paired_replay_sha256", "report_sha256",
                    "resource_manifest_sha256", "source_manifest_sha256", "specification_sha256",
                    "input_manifest_sha256", "logged_paired_execution_completed", "independent_output_verification",
                    "D1_completed_studies", "D2_completed_studies")}}
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)),
                                    ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "actual_inputs_attempted": inputs.manifest,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno,
                        "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)],
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)),
                    "specification_sha256": saved.digest(raw), "partial_package_preserved": True,
                    "paired_execution_may_be_partial": True, "logged_paired_execution_completed": False,
                    "D1_completed_studies": 0, "D2_completed_studies": 0, "automatic_retries": 0}))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0,
                "mode": MODE, "session_lags": {"D1": 1, "D2": 2}, "paired": True,
                "initial_nav": "1", "initial_lots": [], "initial_as_of_rule": "restored BL calendar[lookback]"},
            evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_paired(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded({"status": "failed", "error_type": type(exc).__name__, "automatic_retries": 0}).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
