"""Calibrate verified saved D1/D2 event-arm dispersion; never replay or rank.

Only the four caller-pinned JSON inputs are consumed after a durable start.
The existing portfolio calibration supplies all bootstrap and power arithmetic.
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, localcontext
import math
import os
from pathlib import Path
import resource
from statistics import stdev
import time
import traceback

from research import activity_preflight as saved
from research import experiment_log
from research.activity_portfolio import calibration


SCHEMA = "activity-pair-calibration-v1"
MODE = "discovery_event_arm_dispersion_only"
SOURCE_PATH = "python/research/activity_pair_calibration.py"
ROLES = ("paired_report", "paired_result", "paired_specification", "paired_verification")
PARAMETERS = dict(lookback=60, threshold=3.0, holding_sessions=126, slots=20,
    seed=20261005, end="2026-09-18", commission_bps_per_side=1.77,
    slippage_bps_per_side=5.0, sizing_round_trip_bps=43.54,
    bootstrap_blocks=[63, 126, 252], bootstrap_repetitions=2000, alpha=0.05, power=0.8)
FALSE_FLAGS = ("promotion_allowed", "source_truth_certified", "event_coverage_certified",
               "price_basis_certified", "historical_vintage_certified")
VERIFY_FALSE = ("upstream_inputs_read", "raw_response_bodies_read", "database_access", "api_access",
    "credential_access", "producer_imported", "books_replayed", "full_economic_accounting_verified",
    "trade_event_ledger_recomputed", "bootstrap_statistics_recomputed", "source_files_reread",
    "resource_enforcement_independently_verified", "original_source_truth_verified", "rights_proof_certified",
    "historical_publication_certified", "actual_exits_certified", "pending_carry_certified",
    "whole_package_independent_verification_performed")
VERIFY_TRUE = ("headline_metrics_recomputed", "stored_trade_counters_reconciled", "hash_links_verified",
               "scope_identity_verified", "durable_trial_pair_verified", "resource_records_verified")
LINK_FILES = {"report_sha256": "report.json", "specification_sha256": "specification.json",
    "paired_replay_sha256": "paired-replay.json", "source_manifest_sha256": "source-manifest.json",
    "resource_manifest_sha256": "resource-manifest.json", "input_manifest_sha256": "input-manifest.json"}
SNAPSHOTS = ("activity_snapshot_sha256", "quote_snapshot_sha256", "original_quote_snapshot_sha256",
             "read_scope_sha256", "action_scope_sha256")
HEADLINES = ("total_return", "maximum_drawdown", "session_return_sd", "session_return_count",
             "filled_entries", "ordinary_closed_trades", "terminal_open_lots")
UNITS = {"nav": "caller accounting units; exact Decimal text", "nav_ratio": "multiple of initial NAV",
    "returns": "fraction; exact Decimal text", "maximum_drawdown": "nonnegative fraction; exact Decimal text",
    "session_return_sd": "unannualized sample session-return fraction; float or null",
    "overlap_rate": "summed symmetric-difference count / summed union count; Decimal text or null"}


def _fields(value: dict, names, message: str) -> None:
    saved.require(type(value) is dict and set(value) == set(names), message)


def _parameters(parameters: dict, window: dict) -> None:
    saved.require(saved.encoded(parameters) == saved.encoded(PARAMETERS)
        and saved.encoded(window) == saved.encoded(saved.WINDOW), "frozen calibration parameters/window changed")


def validate_spec(spec: dict, reference: dict) -> None:
    """Require the committed reference's complete values and JSON representations."""
    _fields(spec, ("schema", "study_id", "mode", "promotion_allowed", "window",
                  "reference_specification", "parameters", "inputs"), "exact calibration specification required")
    _parameters(spec["parameters"], spec["window"])
    saved.require(spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]),
        "fixed event-arm calibration specification mismatch")
    _fields(spec["inputs"], ROLES, "exact four saved paired input roles required")
    paths = []
    for row in spec["inputs"].values():
        _fields(row, ("path", "sha256"), "exact path/hash input declaration required")
        saved.require(type(row["path"]) is str and Path(row["path"]).is_absolute()
            and ".." not in Path(row["path"]).parts, "absolute nonalias input path required")
        saved._pin(row["sha256"])
        paths.append(saved._safe_path(Path(row["path"])))
    saved.require(len(paths) == len(set(paths)), "saved input paths must be distinct")


def _decimal(value: str) -> Decimal:
    saved.require(type(value) is str and 0 < len(value) <= 8200, "bounded canonical Decimal text required")
    try:
        number = Decimal(value)
    except ArithmeticError as exc:
        raise ValueError("canonical Decimal text required") from exc
    saved.require(number.is_finite() and str(number) == value
        and len(number.as_tuple().digits) <= 4096 and abs(number.as_tuple().exponent) <= 4096,
        "finite canonical Decimal text required")
    return number


def _sd(value, expected: float) -> None:
    saved.require(type(value) is float and math.isfinite(value) and value >= 0
        and math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-15), "stored session-return SD mismatch")


def _arm(row: dict, verified: dict, initial: Decimal, window: dict) -> tuple[list[str], dict]:
    _fields(row, (*HEADLINES, "path"), "exact saved arm path and headlines required")
    _fields(verified, HEADLINES, "exact independently verified arm headlines required")
    saved.require(type(row["path"]) is list and bool(row["path"]), "nonempty complete session path required")
    previous = peak = initial
    drawdown, values, dates = Decimal(0), [], []
    with localcontext() as context:
        context.prec = 28
        for point in row["path"]:
            _fields(point, ("date", "nav", "nav_ratio", "session_return"), "exact session path row required")
            day = point["date"]
            saved.require(type(day) is str and date.fromisoformat(day).isoformat() == day
                and window["start"] <= day <= window["end"] and (not dates or dates[-1] < day),
                "unique ordered session dates within frozen window required")
            nav = _decimal(point["nav"])
            saved.require(nav >= 0 and (previous != 0 or nav == 0), "nonnegative NAV cannot recover from zero")
            change = Decimal(0) if previous == 0 else nav / previous - 1
            saved.require(_decimal(point["nav_ratio"]) == nav / initial
                and _decimal(point["session_return"]) == change, "saved NAV/return path mismatch")
            statistical_return = float(change)
            saved.require(math.isfinite(statistical_return), "finite statistical session return required")
            values.append(statistical_return)
            dates.append(day)
            peak = max(peak, nav)
            drawdown = max(drawdown, 1 - nav / peak)
            previous = nav
        total = previous / initial - 1
    saved.require(dates[-1] == window["end"], "complete evaluation-end session path required")
    saved.require(len(values) >= 2, "sample session dispersion requires at least two sessions")
    dispersion = stdev(values)
    for name, expected in (("total_return", total), ("maximum_drawdown", drawdown)):
        saved.require(_decimal(row[name]) == _decimal(verified[name]) == expected,
            "stored return/drawdown differs from session path")
    for name in HEADLINES[3:]:
        saved.require(type(row[name]) is int and row[name] >= 0 and type(verified[name]) is int
            and row[name] == verified[name], "stored/verified session or trade counts mismatch")
    saved.require(row["session_return_count"] == len(values), "complete session-return count mismatch")
    _sd(row["session_return_sd"], dispersion)
    _sd(verified["session_return_sd"], dispersion)
    return dates, {"returns": values, "diagnostics": {"filled_entries": row["filled_entries"]},
        "closed_trades": row["ordinary_closed_trades"], "unresolved_positions": row["terminal_open_lots"]}


def calibrate_pair(raw: dict[str, bytes], *, parameters: dict, window: dict, expected_pins: dict) -> dict:
    """Pure saved-byte consistency and dispersion sizing, without IO or replay."""
    _parameters(parameters, window)
    _fields(raw, ROLES, "exact four saved paired byte streams required")
    _fields(expected_pins, ROLES, "exact four caller pins required")
    for role in ROLES:
        saved._pin(expected_pins[role])
        saved.require(type(raw[role]) is bytes and len(raw[role]) <= 64 * 1024**2
            and saved.digest(raw[role]) == expected_pins[role], "saved paired input pin mismatch")
    report, result, spec, verification = (saved.strict_json(raw[role]) for role in ROLES)
    _fields(spec, ("schema", "study_id", "mode", "promotion_allowed", "window",
                  "reference_specification", "parameters", "inputs"), "exact saved paired specification required")
    saved.require(spec["schema"] == spec["study_id"] == "activity-paired-preflight-v1"
        and spec["mode"] == "discovery_descriptive_timing_sensitivity" and spec["promotion_allowed"] is False
        and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(parameters)
        and saved.encoded(spec["window"]) == saved.encoded(window), "paired frozen reference parameters/window mismatch")
    saved.require(type(spec["inputs"]) is dict and bool(spec["inputs"]), "saved upstream input declarations required")
    pins = {}
    for role, declaration in spec["inputs"].items():
        _fields(declaration, ("path", "sha256"), "saved upstream path/hash declaration required")
        saved._pin(declaration["sha256"])
        pins[role] = declaration["sha256"]
    saved.require(type(result) is dict and result.get("schema") == "activity-paired-preflight-v1"
        and result.get("status") == "descriptive_timing_pair_executed"
        and all(result.get(key) is True for key in ("books_computed", "returns_computed", "logged_paired_execution_completed"))
        and result.get("independent_output_verification") == "pending"
        and all(result.get(key) is False for key in (*FALSE_FLAGS, "database_access", "api_access", "original_source_bodies_read"))
        and all(type(result.get(key)) is int and result[key] == value for key, value in (
            ("D1_completed_studies", 1), ("D2_completed_studies", 1), ("new_source_requests", 0), ("automatic_retries", 0))),
        "successful noncertifying saved paired result required")
    saved.require(type(verification) is dict and verification.get("schema") == "bq-raw-paired-independent-verification-v1"
        and verification.get("verified") is True
        and verification.get("scope") == "new_seven_outputs_links_and_five_saved_book_headline_metrics_only"
        and all(verification.get(key) is True for key in VERIFY_TRUE)
        and all(verification.get(key) is False for key in (*FALSE_FLAGS, *VERIFY_FALSE))
        and all(type(verification.get(key)) is int and verification[key] == value for key, value in (
            ("published_D1_completed_studies", 1), ("published_D2_completed_studies", 1),
            ("D1_completed_studies", 0), ("D2_completed_studies", 0), ("automatic_retries", 0),
            ("output_files_read", 7), ("publication_files_read", 2), ("original_input_files_read", 0))),
        "limited independent verification and published 1/1 pair required")
    files = verification["file_sha256"]
    _fields(files, ("result.json", *LINK_FILES.values()), "exact seven independently verified output pins required")
    for pin in files.values():
        saved._pin(pin)
    saved.require(files["result.json"] == expected_pins["paired_result"]
        and files["report.json"] == expected_pins["paired_report"]
        and files["specification.json"] == expected_pins["paired_specification"]
        and verification.get("specification_sha256") == files["specification.json"]
        and all(result.get(key) == files[name] for key, name in LINK_FILES.items()), "paired output/result/verification hash links mismatch")
    saved.require(result.get("input_sha256") == verification.get("input_sha256") == pins,
        "paired upstream declaration/result/verification pins mismatch")
    for key in SNAPSHOTS:
        saved._pin(result.get(key))
        saved.require(result[key] == verification.get(key), "paired snapshot verification link mismatch")
    _fields(report, ("schema", "initial_nav", "units", "arms", "signal_overlap", "target_overlap"),
        "exact saved paired report required")
    saved.require(report["schema"] == "activity-paired-timing-report-v1" and report["initial_nav"] == "1"
        and report["units"] == UNITS, "fixed report units and normalized initial NAV required")
    _fields(report["arms"], ("D1", "D2"), "both event arms required")
    _fields(verification["metrics"], ("arms", "signal_overlap", "target_overlap"), "verified headline metrics required")
    _fields(verification["metrics"]["arms"], ("D1", "D2"), "both verified event arms required")
    results, dates = {}, []
    for arm in ("D1", "D2"):
        days, book = _arm(report["arms"][arm], verification["metrics"]["arms"][arm], Decimal(1), window)
        saved.require(not dates or dates == days, "paired common ordered session paths required")
        dates = days
        results[arm] = calibration(book, parameters)
    permitted = all(row["cost_floor_resolvable"] for row in results.values())
    return {"schema": SCHEMA, "mode": MODE, "status": "event_arm_dispersion_calibrated",
        "input_sha256": dict(expected_pins), "arms": results,
        "sampling_unit": "one_portfolio_return_per_session_per_arm", "power_design_passed": permitted,
        "comparison_power_prerequisite_met": permitted,
        "interpretation": "power prerequisite only; no input-readiness approval, edge evidence or strategy pass; a comparison needs its own frozen specification",
        "stop_reason": None if permitted else "event_arm_cost_floor_not_resolvable",
        "full_economic_accounting_verified": False, "source_truth_certified": False,
        "historical_vintage_certified": False, "promotion_allowed": False,
        "books_replayed": False, "database_access": False, "api_access": False, "credential_access": False,
        "new_source_requests": 0, "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0}


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    pins = {role: spec["inputs"][role]["sha256"] for role in ROLES}
    raw = {role: inputs.read(Path(spec["inputs"][role]["path"]), pins[role], role) for role in ROLES}
    result = calibrate_pair(raw, parameters=spec["parameters"], window=spec["window"], expected_pins=pins)
    saved.write_exclusive(output / "calibration.json", saved.encoded(result))
    return {**result, "calibration_sha256": saved.digest(saved.encoded(result)), "input_manifest": inputs.manifest}


def _resources(started: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU))}


def run_calibration(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """One logged attempt with exclusive outputs, source recheck and failures."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    saved.require(Path(__file__).resolve() == root / SOURCE_PATH, "caller must use committed source location before saved-input reads")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    inputs = saved.PinnedInputs(saved.read_private if reader is None else reader)

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
                input_manifest_sha256=saved.digest(saved.encoded(inputs.manifest)))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return {"status": result["status"], "output_dir": str(output), "result_sha256": saved.digest(saved.encoded(result)),
                **{key: result[key] for key in ("calibration_sha256", "resource_manifest_sha256", "source_manifest_sha256",
                    "specification_sha256", "input_manifest_sha256", "comparison_power_prerequisite_met", "power_design_passed",
                    "D1_completed_studies", "D2_completed_studies")}}
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "actual_inputs_attempted": inputs.manifest,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                                     for frame in traceback.extract_tb(exc.__traceback__)],
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)), "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "calibration_may_be_partial": True,
                    "comparison_power_prerequisite_met": False, "D1_completed_studies": 0, "D2_completed_studies": 0, "automatic_retries": 0}))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0, "mode": MODE,
                "books_replayed": False, "D1_additional_completed_studies": 0, "D2_additional_completed_studies": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_calibration(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded({"status": "failed", "error_type": type(exc).__name__, "automatic_retries": 0}).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
