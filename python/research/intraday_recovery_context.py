"""Calibrate one preregistered negative prior-window context on recovery events.

Join pinned CM labels and original CG causal features. Preserve the entire
upstream matrix and calibrate only the new recovery subset's own-label power.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from research import experiment_log
from research import intraday_peer_context as co


cn, cm, ci, ch, cg, horizons, cd, cc = co.cn, co.cm, co.ci, co.ch, co.cg, co.horizons, co.cd, co.cc
ROOT = co.ROOT
SPECIFICATION_ID = "intraday-recovery-context-v1"
MODE = "fixed-post-drop-recovery-negative-original-event-context-own-label-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-recovery-context-v1.json"
WINDOW, FAMILIES = dict(cm.WINDOW), cm.FAMILIES
RECOVERY = "post_drop_recovery"
PARAMETERS = {
    "scope": "post_drop_recovery_only_both_original_membership_variants_opening_retained_as_upstream_evidence",
    "condition": "original_cg_event_prior30m_return_gt_original_equal_weight_peer_mean_and_event_return_strictly_lt_zero",
    "boundary": "exact_zero_event_return_reject_no_threshold_sweep",
    "feature": "pinned_cg_original_30minute_first_open_to_last_close_same_original_peers_and_clocks",
    "availability": "feature_end_plus_two_minutes_no_later_than_original_decision",
    "cohort": "original_full_top20_target_matrix_unknowns_and_original120minute_complete_cohort_no_rescue_or_reselection",
    "label": "pinned_cm_time_only_30minute_cap_open_price_change_original_three_minute_entry_wait",
    "power": "new_selected_complete_recovery_subset_own_label_dispersion_all_reference_dates_including_zero_event_dates",
    "resampling": "maximum_naive_session_cluster_circular_five_session_bootstrap_2000_draws_seed_zero",
    "cost_floor_bps": "33",
    "unknowns": "unknown_new_features_or_labels_or_failed_unknown_own_power_require_operator_choice_no_prior_waiver",
    "output_scope": "selection_frequency_week_month_and_own_label_dispersion_only_no_future_means_cost_returns_contrast_or_winner"}
UPSTREAM_SHA256, FEATURE_SHA256 = co.UPSTREAM_SHA256, co.FEATURE_SHA256
REGISTERED_PATHS = {**co.REGISTERED_PATHS,
    "output_directory": "/home/minjun4897/research-evidence/intraday-cq-recovery-context-20261022-v1"}
RESOURCES = dict(co.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = cm.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = cm.EXPECTED_SELECTED_COMPLETE
EXPECTED_INPUT_COUNTS = cm.EXPECTED_INPUT_COUNTS
FLAGS = {**cm.FLAGS, "recovery_context_calibrated": True}
ASSUMPTIONS = {
    "hypothesis": "fixed before new subgroup outcomes: original CG recovery pass plus strictly negative original event prior30minute return; exact zero rejects",
    "rationale": "event has not fully recovered its causal30minute first-open level; this is a research hypothesis not validated alpha or a claimed profitable recovery rule",
    "measurement": "new recovery selection frequency and own-label dispersion only; no future-return mean assumed-cost return contrast win rate winner or final comparison family",
    "cohort": "retain every CM target unknown label peer and original120minute future-completeness qualification; passing context cannot rescue original unknown pairs",
    "timing": "reuse CG original signal-end30minute feature end+2minute assumed availability no later than original decision; original three-minute entry and CM30minute cap-open label",
    "peers": "all original causal selected peers equally weighted with no outcome-complete replacement; no raw replay or new input window",
    "power": "new selected-complete recovery time-only labels use their own dispersion all reference dates circular five-session2000draws seed0 maximum error against assumed33bp; old8CM cells retained separately",
    "unknowns": "new feature or label unknown or unknown/failed own sizing requires operator choice; no legacy underpower exception transfers",
    "opening": "opening-range breakout retained unchanged as upstream evidence and excluded from the new hypothesis and sizing family",
    "source": "source calendar eligibility liquidity revisions historical availability price basis costs and actual fills remain uncertified",
    "interpretation": "repeated same-data discovery remains exploratory; no performance qualification strategy pass direction closure reserved access risk change or paper/live promotion"}
_canonical, _sha = cm._canonical, cm._sha
_week, _counts, _ratio = co._week, co._counts, co._ratio


class RecoveryContextRefusal(RuntimeError):
    """Stable reason codes omit private supplied evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256,
        "feature_sha256": FEATURE_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise RecoveryContextRefusal("specification_scope_mismatch")


def _feature(saved: Mapping) -> tuple[str, list[str]]:
    """Add the strict event sign only after authenticating the full CG graph."""
    state = saved["selection_state"]
    if state == "pass":
        state = "pass" if _ratio(saved["feature"]["event"]["price_change"]) < 0 else "reject"
    return state, saved["selection_unknown_reasons"]


def build_calibration(cm_report: Mapping, cg_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-input calibration, with no actual-input reads or means."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference = co._validate_inputs(cm_report, cg_report, checkpoint)
    except co.PeerContextRefusal as exc:
        raise RecoveryContextRefusal(str(exc)) from exc
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, cn.ComparisonRefusal,
            cd.ComparisonRefusal, horizons.HorizonRefusal) as exc:
        raise RecoveryContextRefusal("invalid_upstream_contract") from exc
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "upstream_calibration_requires_operator_choice": cm_report["requires_operator_choice"],
        "upstream_legacy_calibration_requires_operator_choice": cm_report["upstream_calibration_requires_operator_choice"],
        "input_evidence": {"upstream_report_sha256": UPSTREAM_SHA256, "upstream_report_path": REGISTERED_PATHS["upstream_path"],
            "feature_report_sha256": FEATURE_SHA256, "feature_report_path": REGISTERED_PATHS["feature_path"],
            "calibration_input_evidence": cm_report["input_evidence"], "feature_input_evidence": cg_report["input_evidence"]},
        "variants": {}}
    for key, variant in cm_report["variants"].items():
        index = {(row["session"], row["symbol"]): row for row in cg_report["variants"][key]["days"]}
        days, records, projected = [], [], []
        known, unknown = 0, 0
        for row in variant["days"]:
            checkpoint()
            saved = row["families"][RECOVERY]
            feature = index[row["session"], row["symbol"]]["families"][RECOVERY]
            state, reasons = _feature(feature)
            cohort = ("non_signal" if saved["status"] != "signal" else "outside_original_complete_cohort"
                if saved["original_pair_status"] != "complete" else "selected_complete" if state == "pass" else "not_selected")
            context = {"feature": feature["feature"], "selection_state": state,
                "selection_unknown_reasons": reasons, "label_cohort_state": cohort}
            days.append({**row, "families": {**row["families"], RECOVERY: {**saved, "recovery_context": context}}})
            projected.append({"session": row["session"], **saved, **context})
            if cohort == "selected_complete":
                outcome = saved["labels"]["time_only"]
                if outcome["status"] == "known":
                    known += 1
                    records.append({"session": row["session"], "label": horizons._ratio(outcome["price_change"])})
                else:
                    unknown += 1
        own = cg._own_power(records, reference, checkpoint)
        counts = _counts(projected)
        summary = {**counts, "known_labels": known, "unknown_labels": unknown, "own_label_dispersion": own,
            "requires_operator_choice": bool(counts["feature_unknown"] or unknown or own["requires_operator_choice"])}
        for field, period in (("by_month", lambda day: day[:7]), ("by_week", _week)):
            summary[field] = {value: _counts([row for row in projected if period(row["session"]) == value])
                for value in sorted({period(day) for day in reference})}
        result["variants"][key] = {"days": days, "families": variant["families"], "recovery_context": summary}
    result["requires_operator_choice"] = any(variant["recovery_context"]["requires_operator_choice"] for variant in result["variants"].values())
    checkpoint()
    return result


def run_calibration(*, specification_path: str | Path, upstream_path: str | Path, feature_path: str | Path,
        output_directory: str | Path, runs_path: str | Path, budget=None,
        before_completion: Callable[[], None] | None = None) -> dict:
    """Durably start before either pinned report read; caller finishes budget."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("recovery_context_pipeline")
    checkpoint()
    spec_path, upstream, feature, output, log = map(Path,
        (specification_path, upstream_path, feature_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise RecoveryContextRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, feature, output, log)
    for path, key in zip((upstream, feature, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(REGISTERED_PATHS[key]):
            raise RecoveryContextRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise RecoveryContextRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise RecoveryContextRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(co.__file__), Path(cn.__file__), Path(cm.__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__),
        Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    executable = Path(sys.executable).resolve(strict=True)
    for path in (upstream, feature, output, log):
        if any(path.resolve() == source.resolve()
                or (path.exists() and source.exists() and path.samefile(source)) for source in (*sources, executable)):
            raise RecoveryContextRefusal("aliased_source_or_runtime_path")
    pins = {str(path): _sha(path) for path in sources}
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise RecoveryContextRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise RecoveryContextRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise RecoveryContextRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise RecoveryContextRefusal("unsafe_output_permissions")
        reports = []
        for path, digest in ((upstream, UPSTREAM_SHA256), (feature, FEATURE_SHA256)):
            with path.open("rb") as stream:
                data = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
            checkpoint()
            if len(data) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(data).hexdigest() != digest:
                raise RecoveryContextRefusal("upstream_size_or_hash_mismatch")
            reports.append(json.loads(data))
            del data
        report = build_calibration(*reports, checkpoint=checkpoint)
        del reports
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise RecoveryContextRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise RecoveryContextRefusal("registered_selected_population_changed")
        if any(report["input_evidence"]["calibration_input_evidence"][name] != value for name, value in EXPECTED_INPUT_COUNTS.items()):
            raise RecoveryContextRefusal("registered_raw_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False, default=lambda value: json.loads(_canonical(value)))
            for index, chunk in enumerate(encoder.iterencode(report)):
                if index % 1024 == 0: checkpoint()
                stream.write(chunk.encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        cc._sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None: before_completion()
        checkpoint()
        if (any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256
                or _sha(feature) != FEATURE_SHA256):
            raise RecoveryContextRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise RecoveryContextRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha, "input_evidence": report["input_evidence"],
            "requires_operator_choice": report["requires_operator_choice"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "feature_sha256": FEATURE_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
