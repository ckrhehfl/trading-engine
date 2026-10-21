"""Fixed qualified-cell price comparison on the pinned CG selection mask.

Only two independently verified saved reports are read. No raw observations,
detectors, feature reconstruction, new peers or execution model are involved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys

from research import experiment_log
from research import intraday_entry_strength as cg


horizons, cd, cc = cg.horizons, cg.cd, cg.cc
ROOT = cg.ROOT
SPECIFICATION_ID = "intraday-entry-strength-comparison-v1"
MODE = "fixed-qualified-horizon-entry-strength-price-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-entry-strength-comparison-v1.json"
WINDOW = dict(cg.WINDOW)
FAMILIES = cg.FAMILIES
QUALIFIED_HORIZONS = {FAMILIES[0]: (5, 15, 30, 60, 120), FAMILIES[1]: (5, 15, 30)}
EXCLUDED_HORIZONS = {FAMILIES[1]: (60, 120)}
PARAMETERS = {**cg.PARAMETERS,
    "response_horizons_minutes": {family: list(values) for family, values in QUALIFIED_HORIZONS.items()},
    "upstream_reuse": "pinned_verified_cf_labels_and_cg_selected_complete_mask_no_raw_replay",
    "output_scope": "fixed_qualified_cells_conditional_price_label_comparison_no_executable_portfolio"}
CF_SHA256 = cg.UPSTREAM_SHA256
CG_SHA256 = "b507b29998aa797d79f12e6da64ac6e39fd233a542fbedfb1f0ed98cee57d3f0"
REGISTERED_PATHS = {
    "cf_report_path": cg.REGISTERED_PATHS["upstream_path"],
    "cg_report_path": "/home/minjun4897/research-evidence/intraday-cg-entry-strength-20261021-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-ch-entry-strength-comparison-20261021-v1",
    "canonical_runs_path": cg.REGISTERED_PATHS["canonical_runs_path"]}
RESOURCES = {"maximum_upstream_bytes": 67108864, "minimum_free_bytes": 1073741824,
    "address_space_mib": 512, "cpu_seconds": 300, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
EXPECTED_REFERENCE_SESSIONS = cg.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = {"D+1": dict(zip(FAMILIES, (787, 377))),
    "D+2": dict(zip(FAMILIES, (785, 376)))}
FLAGS = {**cc.FLAGS, "exploratory_only": True, "actual_portfolio_pnl": False,
    "family_adopted": False, "horizon_selected": False, "underpower_exception_applied": False,
    "comparison_performed": True}
ASSUMPTIONS = {
    "upstream": "reuse pinned independently verified CF labels and CG causal selection; no new archive, database, API, detector or feature replay",
    "selection": "exact original CG selected_complete mask with original causal peers; no future-return-based replacement or short-horizon rescue",
    "cohort": "original CD 120-minute complete pairs inherited through CF and passing CG; conditional future completeness, not full top-20 coverage or executable inclusion",
    "qualified_cells": "continuation 5/15/30/60/120 minutes and recovery 5/15/30 minutes only, approved before comparison; recovery 60/120 remain excluded by upstream power failure",
    "power": "reuse each qualified cell's CG event-own-label dispersion and MDE prerequisite before any new future-return mean; excluded cells do not gain a waiver or full-family certification",
    "cost": "subtract the same fixed assumed 33bp from event and equal-weight peer price labels; costs cancel in paired excess; not historically verified execution costs",
    "unit": "one matched hypothetical event/peer price-label budget unit; opportunities are not fills, realized exposure or portfolio PnL",
    "baseline": "unfiltered pinned CF qualified-cell summaries are descriptive anchors on a different population; no causal improvement estimate",
    "dependence": "paired excess uncertainty clusters and circularly resamples all reference sessions, including zero-event dates; raw counts are not independent samples",
    "source": "upstream observation/publication, revision, calendar, price-basis, eligibility and liquidity qualifications remain uncertified",
    "runtime": "upstream CG independently verified; original CD runtime conformance remains unresolved; this attempt requires its own shared-budget receipt"}
ORIGINAL_FIELDS = ("status", "event", "original_pair_status", "original_pair_unknown_reasons",
    "original_unknown_control_symbols", "original_event_label_state", "selected_controls", "excluded_controls")
SELECTION_FIELDS = ("selection_state", "selection_unknown_reasons", "label_cohort_state")
_canonical, _sha = cg._canonical, cg._sha


class ComparisonRefusal(RuntimeError):
    """Stable reason codes avoid disclosing private saved values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "cf_report_sha256": CF_SHA256, "cg_report_sha256": CG_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise ComparisonRefusal("specification_scope_mismatch")


def _compact_cg(report: Mapping) -> dict:
    """Discard already verified feature graphs before the larger CF read."""
    return {**report, "variants": {key: {**variant, "days": [
        {**row, "families": {family: {name: finding[name] for name in (*ORIGINAL_FIELDS, *SELECTION_FIELDS)}
            for family, finding in row["families"].items()}} for row in variant["days"]]}
        for key, variant in report["variants"].items()}}


def _matrices(cf_report: Mapping, cg_report: Mapping, checkpoint: Callable[[], None]) -> tuple[dict, dict, list[str]]:
    cf_matrix, reference, _ = cg._matrix(cf_report)
    if (cg_report.get("study_id") != cg.SPECIFICATION_ID or cg_report.get("mode") != cg.MODE
            or cg_report.get("parameters") != cg.PARAMETERS or cg_report.get("window") != WINDOW
            or any(cg_report.get(key) != value for key, value in cg.FLAGS.items())
            or type(cg_report.get("requires_operator_choice")) is not bool):
        raise ComparisonRefusal("wrong_calibration_study_or_scope")
    projection = {"study_id": cc.SPECIFICATION_ID, "parameters": cc.PARAMETERS,
        **cc.FLAGS, "variants": cg_report["variants"]}
    cg_matrix, cg_reference, _ = cd._matrix(projection)
    if cg_reference != reference or cg_report.get("reference_sessions") != reference:
        raise ComparisonRefusal("upstream_reference_sessions_mismatch")
    for key, grouped in cf_matrix.items():
        for day, rows in grouped.items():
            checkpoint()
            actual_rows = cg_matrix[key][day]
            if [row["symbol"] for row in actual_rows] != [row["symbol"] for row in rows]:
                raise ComparisonRefusal("calibration_target_matrix_mismatch")
            for original, calibrated in zip(rows, actual_rows, strict=True):
                if any(_canonical(original[name]) != _canonical(calibrated.get(name))
                        for name in original if name != "families"):
                    raise ComparisonRefusal("calibration_target_metadata_mismatch")
                if set(calibrated.get("families", {})) != set(FAMILIES):
                    raise ComparisonRefusal("calibration_family_population_mismatch")
                for family, saved in original["families"].items():
                    selected = calibrated["families"][family]
                    expected = {name: saved[name] for name in ORIGINAL_FIELDS if name not in {"event", "selected_controls"}}
                    expected["event"] = ({name: saved["event"][name] for name in ("decision_at", "signal_bar_end")}
                        if saved["status"] == "signal" else None)
                    expected["selected_controls"] = [{"symbol": item["symbol"]} for item in saved["selected_controls"]]
                    if any(_canonical(selected.get(name)) != _canonical(value) for name, value in expected.items()):
                        raise ComparisonRefusal("calibration_original_finding_mismatch")
                    state = selected.get("selection_state")
                    reasons = selected.get("selection_unknown_reasons")
                    if (state not in cg.SELECTION_STATES or not isinstance(reasons, list)
                            or any(not isinstance(reason, str) for reason in reasons)
                            or (state == "unknown") != bool(reasons)
                            or (saved["status"] != "signal") != (state == "non_signal")):
                        raise ComparisonRefusal("invalid_calibration_selection_state")
                    cohort = ("non_signal" if saved["status"] != "signal"
                        else "outside_original_complete_cohort" if saved["original_pair_status"] != "complete"
                        else "selected_complete" if state == "pass" else "not_selected")
                    if selected.get("label_cohort_state") != cohort:
                        raise ComparisonRefusal("calibration_selection_cohort_mismatch")
        for family in FAMILIES:
            records = [row["families"][family] for row in cg_report["variants"][key]["days"]]
            counts = cg._counts(records)
            summary = cg_report["variants"][key]["families"][family]
            if any(summary.get(name) != value for name, value in counts.items()):
                raise ComparisonRefusal("calibration_counts_mismatch")
            for horizon in QUALIFIED_HORIZONS[family]:
                own = summary["by_horizon"][str(horizon)]
                mde = own.get("mde_bps")
                if (type(mde) not in {int, float} or not math.isfinite(mde) or not 0 <= mde <= 33
                        or own.get("requires_operator_choice") is not False
                        or own.get("underpower_exception_applied") is not False
                        or own.get("cost_floor_bps_assumed") != "33"
                        or own.get("measured_labels") != counts["selected_complete"]
                        or own.get("reference_sessions") != len(reference)):
                    raise ComparisonRefusal("qualified_cell_power_prerequisite_failed")
    return cf_matrix, cg_matrix, reference


def _describe(rows: list[dict]) -> dict:
    pairs = [row for row in rows if row["response"]["status"] == "measured"]
    return {"measured_pairs": len(pairs),
        "event": cd._distribution([row["response"]["event_price_change"] for row in pairs]),
        "control": cd._distribution([row["response"]["control_mean_price_change"] for row in pairs]),
        "excess_mean": math.fsum(float(row["response"]["excess_price_change"]) for row in pairs) / len(pairs) if pairs else None}


def build_comparison(cf_report: Mapping, cg_report: Mapping, *,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Reuse fixed input labels after every qualified-cell sizing check passes."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    cf_matrix, cg_matrix, reference = _matrices(cf_report, cg_report, checkpoint)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "qualified_cells_power_prerequisite_met": True,
        "upstream_calibration_requires_operator_choice": cg_report["requires_operator_choice"],
        "excluded_horizons_minutes": {family: list(values) for family, values in EXCLUDED_HORIZONS.items()},
        "input_evidence": {"cf_report_sha256": CF_SHA256, "cg_report_sha256": CG_SHA256,
            "cf_report_path": REGISTERED_PATHS["cf_report_path"], "cg_report_path": REGISTERED_PATHS["cg_report_path"]},
        "variants": {key: {"days": [], "families": {}} for key in cf_matrix}}
    for key, grouped in cf_matrix.items():
        for day, rows in grouped.items():
            checkpoint()
            for saved_row, mask_row in zip(rows, cg_matrix[key][day], strict=True):
                output = {name: saved_row[name] for name in ("symbol", "session", "data_vintage")}
                output["families"] = {}
                for family in FAMILIES:
                    saved, mask = saved_row["families"][family], mask_row["families"][family]
                    finding = {name: mask[name] for name in (*ORIGINAL_FIELDS, *SELECTION_FIELDS)}
                    finding["responses"] = {}
                    if saved["status"] == "signal":
                        selected = mask["label_cohort_state"] == "selected_complete"
                        for horizon in QUALIFIED_HORIZONS[family]:
                            response = {"status": "measured" if selected else f"not_measured_{mask['label_cohort_state']}"}
                            for name in ("event_price_change", "control_mean_price_change", "excess_price_change"):
                                response[name] = horizons._ratio(saved["responses"][str(horizon)][name]) if selected else None
                            if selected and response["event_price_change"] - response["control_mean_price_change"] != response["excess_price_change"]:
                                raise ComparisonRefusal("upstream_paired_label_arithmetic_mismatch")
                            finding["responses"][str(horizon)] = response
                    output["families"][family] = finding
                result["variants"][key]["days"].append(output)
    for key, variant in result["variants"].items():
        for family in FAMILIES:
            counts = cg._counts([row["families"][family] for row in variant["days"]])
            summaries = {}
            for horizon in QUALIFIED_HORIZONS[family]:
                checkpoint()
                rows = [{"session": row["session"], "symbol": row["symbol"],
                    "response": row["families"][family]["responses"][str(horizon)]}
                    for row in variant["days"] if row["families"][family]["status"] == "signal"]
                pairs = [row for row in rows if row["response"]["status"] == "measured"]
                ids = [(row["session"], row["symbol"]) for row in pairs]
                cd.conclusion_check.require_no_blockers([
                    cd.conclusion_check.check_same_population({"event": ids, "control": ids}),
                    cd.conclusion_check.check_clustered_observations([row["session"] for row in pairs],
                        reported_n=len({row["session"] for row in pairs}))])
                uncertainty = horizons._statistics([{"session": row["session"],
                    "label": row["response"]["excess_price_change"]} for row in pairs], reference, checkpoint)
                paired = {("known_pair_sessions" if name == "known_label_sessions" else
                    "bootstrap_zero_pair_draws" if name == "bootstrap_zero_label_draws" else name): value
                    for name, value in uncertainty.items() if name != "event_sd"}
                baseline = cf_report["variants"][key]["families"][family]["by_horizon"][str(horizon)]
                summaries[str(horizon)] = {**_describe(rows), "paired_mean_uncertainty": paired,
                    "own_label_dispersion": cg_report["variants"][key]["families"][family]["by_horizon"][str(horizon)],
                    "baseline_unfiltered_cf": {name: baseline[name] for name in ("measured_pairs", "event", "control", "excess_mean")},
                    "by_month": {month: _describe([row for row in rows if row["session"][:7] == month])
                        for month in sorted({day[:7] for day in reference})},
                    "by_symbol": {symbol: _describe([row for row in rows if row["symbol"] == symbol])
                        for symbol in sorted({row["symbol"] for row in variant["days"]})}}
            variant["families"][family] = {**counts, "by_horizon": summaries}
    checkpoint()
    return result


def run_comparison(*, specification_path: str | Path, cf_report_path: str | Path,
        cg_report_path: str | Path, output_directory: str | Path, runs_path: str | Path,
        budget=None, before_completion: Callable[[], None] | None = None) -> dict:
    """One durable trial with bounded saved-report reads and exclusive output."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("entry_strength_comparison_pipeline")
    checkpoint()
    spec_path, cf_path, cg_path, output, log = map(Path,
        (specification_path, cf_report_path, cg_report_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise ComparisonRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, cf_path, cg_path, output, log)
    for path, name in zip((cf_path, cg_path, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(spec[name]):
            raise ComparisonRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ComparisonRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise ComparisonRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(cg.__file__), ROOT / "python/research/research_budget.py",
        Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__), Path(cc.core.__file__),
        Path(experiment_log.__file__), Path(cd.conclusion_check.__file__), ROOT / "python/research/__init__.py",
        ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    pins = {str(path): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise ComparisonRefusal("specification_changed")

    def read_report(path: Path, digest: str) -> dict:
        with path.open("rb") as stream:
            content = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(content) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(content).hexdigest() != digest:
            raise ComparisonRefusal("upstream_size_or_hash_mismatch")
        return json.loads(content)

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise ComparisonRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise ComparisonRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise ComparisonRefusal("unsafe_output_permissions")
        calibration = _compact_cg(read_report(cg_path, CG_SHA256))
        upstream = read_report(cf_path, CF_SHA256)
        report = build_comparison(upstream, calibration, checkpoint=checkpoint)
        del upstream, calibration
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise ComparisonRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise ComparisonRefusal("registered_selected_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False, default=lambda value: json.loads(_canonical(value)))
            for index, chunk in enumerate(encoder.iterencode(report)):
                if index % 1024 == 0:
                    checkpoint()
                stream.write(chunk.encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        cc._sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None:
            before_completion()
        checkpoint()
        if (any(_sha(path) != pins[str(path)] for path in sources)
                or _sha(cf_path) != CF_SHA256 or _sha(cg_path) != CG_SHA256):
            raise ComparisonRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise ComparisonRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha,
            "input_evidence": report["input_evidence"], **FLAGS,
            "qualified_cells_power_prerequisite_met": report["qualified_cells_power_prerequisite_met"],
            "upstream_calibration_requires_operator_choice": report["upstream_calibration_requires_operator_choice"]}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "cf_report_sha256": CF_SHA256, "cg_report_sha256": CG_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
