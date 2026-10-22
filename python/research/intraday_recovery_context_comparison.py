"""One authorized exploratory nested recovery comparison of the saved CQ report.

Retain CQ's failed own-power prerequisite exactly. This one-run authorization
does not waive unknown features or labels, transfer to another family, or qualify
a strategy. Original and filtered entry counts intentionally differ.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from statistics import NormalDist
import sys

from research import experiment_log
from research import intraday_peer_context_comparison as cp
from research import intraday_recovery_context as cq


co, cn, cm, ci, ch, cg, horizons, cd, cc = cq.co, cq.cn, cq.cm, cq.ci, cq.ch, cq.cg, cq.horizons, cq.cd, cq.cc
ROOT, WINDOW, RECOVERY, FAMILIES = cq.ROOT, dict(cq.WINDOW), cq.RECOVERY, cq.FAMILIES
SPECIFICATION_ID = "intraday-recovery-context-comparison-v1"
MODE = "fixed-post-drop-recovery-negative-original-event-context-exploratory-conditional-per-entry-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-recovery-context-comparison-v1.json"
UPSTREAM_SHA256 = "9dedf34d868ba6e8847bd8830943cbac2f92b62d1eb09a4bbca760ab4be61f6d"
UPSTREAM_SPECIFICATION_SHA256 = "abff79371866a31e3566d554df0f03a8173e4cef3f2dc1b06a977bf163d64588"
EXPLORATORY_AUTHORIZATION = {
    "scope": SPECIFICATION_ID,
    "operator_choice": "1",
    "upstream_report_sha256": UPSTREAM_SHA256,
    "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
    "decision": "one_exploratory_comparison_despite_failed_cq_own_label_power",
    "limitation": "no_unknown_feature_or_label_waiver_no_other_family_or_future_run_transfer"}
PARAMETERS = {
    "scope": "post_drop_recovery_only_both_original_membership_variants_opening_retained_as_upstream_evidence",
    "condition": "original_cg_event_prior30m_return_gt_original_equal_weight_peer_mean_and_event_return_strictly_lt_zero_exact_zero_rejects",
    "cohort": "all_original_cg_passing_complete_recoveries_vs_nested_negative_event_subset_full_targets_unknowns_original120minute_cohort_retained",
    "entry_exit": "same_original_three_minute_wait_entry_open_and_time_only_entry_plus30minute_exit_open",
    "unit": "conditional_per_entry_equal_hypothetical_cash_price_change_sample_counts_intentionally_differ_selection_axis",
    "cost": "subtract_exact33_over10000_per_event_same_assumed_cost_cancels_in_difference",
    "comparison": "filtered_minus_original_conditional_mean_not_matched_pair_exit_or_portfolio_exposure_equal_causal_effect",
    "power": "retain_exact_two_cq_selected_and_eight_cm_own_sizing_failed_cq_prerequisite_preserved_this_cr_only_explicit_operator_choice_no_remeasurement_or_contrast_error_substitution",
    "missingness": "no_zero_fill_no_rejected_arm_no_rescue_no_excluded_name_replacement_no_new_unknowns",
    "opportunities": "original_filtered_retained_removed_counts_and_all_reference_iso_week_counts_not_actual_trades_or_independent_n",
    "uncertainty": cp.PARAMETERS["uncertainty"],
    "influence": cp.PARAMETERS["influence"],
    "bootstrap": cp.PARAMETERS["bootstrap"],
    "upstream_reuse": "sole_pinned_verified_cq_report_saved_contract_validation_no_raw_cm_or_cg_report_read_or_prior_truth_recertification",
    "output_scope": "two_timing_cells_original_filtered_price_cost_and_nested_mean_difference_descriptions_no_p_value_winner_edge_or_promotion"}
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cq-recovery-context-20261022-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cr-recovery-context-comparison-20261022-v1",
    "canonical_runs_path": cq.REGISTERED_PATHS["canonical_runs_path"]}
RESOURCES = dict(cq.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = cq.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = cq.EXPECTED_SELECTED_COMPLETE
EXPECTED_FILTERED_COMPLETE = {"D+1": 130, "D+2": 130}
EXPECTED_INPUT_COUNTS = cq.EXPECTED_INPUT_COUNTS
UPSTREAM_SOURCE_ROOT = cp.UPSTREAM_SOURCE_ROOT
UPSTREAM_SOURCE_PATHS = (
    "configs/research/discovery/intraday-recovery-context-v1.json",
    "python/research/intraday_recovery_context.py", *cp.UPSTREAM_SOURCE_PATHS[1:])
FLAGS = {**cq.FLAGS, "calibration_only": False, "comparison_performed": True,
    "recovery_context_compared": True, "underpower_exception_applied": True}
ASSUMPTIONS = {
    "measurement": "conditional per-entry means on original complete CG-passing recoveries and their nested negative-event-context subset; entry counts intentionally differ",
    "selection": "same preserved underlying original events and labels; selected/rejected membership changes conditional composition, not a matched-pair exit policy or exposure-equal causal comparison",
    "cohort": cp.ASSUMPTIONS["cohort"],
    "timing": cp.ASSUMPTIONS["timing"],
    "unit": cp.ASSUMPTIONS["unit"],
    "cost": cp.ASSUMPTIONS["cost"],
    "power": "exact previous two CQ selected and eight CM own-sizing cells retained including failed CQ cost-floor MDE and false own underpower_exception_applied; one explicit CR-only operator exception permits descriptive means without changing prerequisite status or remeasuring power",
    "unknowns": "unknown features labels or unknown own sizing still refuse; this exception applies only to known failed CQ own sizing and never transfers to another family or future run",
    "dependence": cp.ASSUMPTIONS["dependence"],
    "interval": cp.ASSUMPTIONS["interval"],
    "opportunities": cp.ASSUMPTIONS["opportunities"],
    "upstream": "sole pinned independently verified CQ report; validate saved lineage original CM contract and original CG then strict-negative causal feature derivations without raw replay or prior vendor truth recertification",
    "interpretation": cp.ASSUMPTIONS["interpretation"]}
COST = cp.COST
_canonical, _sha = cq._canonical, cq._sha
_selection_counts = cp._selection_counts


class ComparisonRefusal(RuntimeError):
    """Stable reason codes omit private supplied evidence values."""


def exploratory_authorization() -> dict:
    """Bind the one registered exception to the current frozen input identities."""
    return {**EXPLORATORY_AUTHORIZATION, "upstream_report_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "exploratory_authorization": exploratory_authorization()}
    if _canonical(spec) != _canonical(expected):
        raise ComparisonRefusal("specification_scope_mismatch")


def _own_power_valid(own: Mapping, selected: int, sessions: int, reference: list[str]) -> bool:
    """Authenticate known saved own sizing, preserving its actual pass/failure.

    Do not feed a normalized fake pass to CN's pass-only validator or calculate
    new dispersion. Unknown sizing remains outside the explicit exception.
    """
    numbers = ("event_sd", "naive_se", "session_cluster_se", "bootstrap_se", "design_se", "mde_bps")
    fields = {*numbers, "measured_labels", "reference_sessions", "known_label_sessions",
        "corrected_to_naive_se_ratio", "bootstrap_zero_label_draws", "cost_floor_bps_assumed",
        "requires_operator_choice", "underpower_exception_applied", "planning_constraint"}
    if (not isinstance(own, Mapping) or set(own) != fields
            or any(type(own[name]) not in {int, float} or not math.isfinite(own[name])
                or own[name] < 0 for name in numbers)):
        return False
    mde = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * own["design_se"] * 10000
    ratio, failed = own["corrected_to_naive_se_ratio"], own["mde_bps"] > 33
    if (type(own["bootstrap_zero_label_draws"]) is not int or not 0 <= own["bootstrap_zero_label_draws"] <= 1998
            or (own["naive_se"] == 0 and ratio is not None)
            or (own["naive_se"] != 0 and (type(ratio) not in {int, float} or not math.isfinite(ratio)
                or not math.isclose(ratio, own["design_se"] / own["naive_se"], rel_tol=1e-12, abs_tol=1e-15)))):
        return False
    return (type(own["measured_labels"]) is int and own["measured_labels"] == selected and selected >= 2
        and type(own["reference_sessions"]) is int and own["reference_sessions"] == len(reference)
        and type(own["known_label_sessions"]) is int and own["known_label_sessions"] == sessions
        and 2 <= sessions <= min(selected, len(reference))
        and math.isclose(own["naive_se"], own["event_sd"] / math.sqrt(selected), rel_tol=1e-12, abs_tol=1e-15)
        and own["design_se"] == max(own[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
        and math.isclose(own["mde_bps"], mde, rel_tol=1e-12, abs_tol=1e-12)
        and own["cost_floor_bps_assumed"] == "33" and own["requires_operator_choice"] is failed
        and own["underpower_exception_applied"] is False
        and own["planning_constraint"] == ("own_label_mde_exceeds_assumed_cost_floor" if failed
            else "cost_floor_power_prerequisite_met"))


def _validate_contract(report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    expected = {"study_id": cq.SPECIFICATION_ID, "mode": cq.MODE, "window": WINDOW,
        "parameters": cq.PARAMETERS, "assumptions": cq.ASSUMPTIONS, **cq.FLAGS,
        "upstream_calibration_requires_operator_choice": False,
        "specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if (any(_canonical(report.get(name)) != _canonical(value) for name, value in expected.items())
            or type(report.get("requires_operator_choice")) is not bool
            or type(report.get("upstream_legacy_calibration_requires_operator_choice")) is not bool
            or set(report.get("variants", {})) != {"D+1", "D+2"}):
        raise ComparisonRefusal("wrong_upstream_study_or_prerequisites")
    sources, runtime = report.get("source_sha256"), report.get("runtime")
    source_keys = {UPSTREAM_SOURCE_ROOT + "/" + name for name in UPSTREAM_SOURCE_PATHS}
    if (not isinstance(sources, Mapping) or set(sources) != source_keys
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in sources.values())
            or sources[UPSTREAM_SOURCE_ROOT + "/" + UPSTREAM_SOURCE_PATHS[0]] != UPSTREAM_SPECIFICATION_SHA256
            or not isinstance(runtime, Mapping) or set(runtime) != {"python", "implementation", "executable", "executable_sha256"}
            or any(not isinstance(runtime[name], str) or not runtime[name] for name in runtime)
            or runtime["implementation"] != "cpython" or not runtime["executable"].startswith("/")
            or not re.fullmatch(r"[0-9a-f]{64}", runtime["executable_sha256"])):
        raise ComparisonRefusal("upstream_source_or_runtime_lineage_mismatch")
    projected = {"study_id": cm.SPECIFICATION_ID, "mode": cm.MODE, "window": WINDOW,
        "parameters": cm.PARAMETERS, "assumptions": cm.ASSUMPTIONS, **cm.FLAGS,
        "requires_operator_choice": False,
        "upstream_calibration_requires_operator_choice": report["upstream_legacy_calibration_requires_operator_choice"],
        "reference_sessions": report["reference_sessions"], "variants": {},
        "input_evidence": report["input_evidence"]["calibration_input_evidence"]}
    for key, variant in report["variants"].items():
        if set(variant) != {"days", "families", "recovery_context"}:
            raise ComparisonRefusal("upstream_variant_contract_mismatch")
        days = []
        for row in variant["days"]:
            saved = row["families"][RECOVERY]
            if set(saved) != {*cm.ORIGINAL_FIELDS, "labels", "recovery_context"}:
                raise ComparisonRefusal("upstream_recovery_contract_mismatch")
            days.append({**row, "families": {**row["families"], RECOVERY: {
                name: value for name, value in saved.items() if name != "recovery_context"}}})
        projected["variants"][key] = {"days": days, "families": variant["families"]}
    reference = cn._validate_upstream(projected, checkpoint)
    member_values, graphs, failed_prerequisites = {}, {}, []
    for variant in report["variants"].values():
        rows = []
        for row in variant["days"]:
            checkpoint()
            saved, day, symbol = row["families"][RECOVERY], row["session"], row["symbol"]
            context = saved["recovery_context"]
            if set(context) != {"feature", "selection_state", "selection_unknown_reasons", "label_cohort_state"}:
                raise ComparisonRefusal("upstream_context_contract_mismatch")
            original = {**{name: saved[name] for name in cm.ORIGINAL_FIELDS}, "feature": context["feature"]}
            co._feature(original, day, symbol)  # Its positive-peer refinement is irrelevant; CG validation is binding.
            state, reasons = cq._feature(original)
            cohort = ("non_signal" if saved["status"] != "signal" else "outside_original_complete_cohort"
                if saved["original_pair_status"] != "complete" else "selected_complete" if state == "pass" else "not_selected")
            if (context["selection_state"] != state or context["selection_unknown_reasons"] != reasons
                    or context["label_cohort_state"] != cohort
                    or (cohort == "selected_complete" and saved["label_cohort_state"] != "selected_complete")):
                raise ComparisonRefusal("upstream_context_selection_mismatch")
            if saved["status"] == "signal":
                feature = context["feature"]
                for item in (feature["event"], *feature["peers"]):
                    member_key = (day, item["symbol"], _canonical([feature["start"], feature["end"]]))
                    value = _canonical(item)
                    if member_key in member_values and member_values[member_key] != value:
                        raise ComparisonRefusal("upstream_cross_event_member_mismatch")
                    member_values[member_key] = value
                graph_key = (day, symbol, _canonical(saved["event"]), _canonical(saved["selected_controls"]))
                value = _canonical(feature)
                if graph_key in graphs and graphs[graph_key] != value:
                    raise ComparisonRefusal("upstream_cross_event_graph_mismatch")
                graphs[graph_key] = value
            rows.append({"session": day, **saved, **context})
        summary, counts = variant["recovery_context"], cq._counts(rows)
        selected = counts["selected_complete"]
        sessions = len({row["session"] for row in rows if row["label_cohort_state"] == "selected_complete"})
        own = summary["own_label_dispersion"]
        if (set(summary) != {*counts, "known_labels", "unknown_labels", "own_label_dispersion",
                "requires_operator_choice", "by_month", "by_week"}
                or any(_canonical(summary[name]) != _canonical(value) for name, value in counts.items())
                or type(summary["known_labels"]) is not int or summary["known_labels"] != selected
                or type(summary["unknown_labels"]) is not int or summary["unknown_labels"] != 0
                or counts["feature_unknown"] != 0 or not _own_power_valid(own, selected, sessions, reference)
                or summary["requires_operator_choice"] is not own["requires_operator_choice"]):
            raise ComparisonRefusal("upstream_context_counts_or_own_power_mismatch")
        failed_prerequisites.append(own["requires_operator_choice"])
        for field, period in (("by_month", lambda day: day[:7]), ("by_week", cq._week)):
            expected_periods = {value: cq._counts([row for row in rows if period(row["session"]) == value])
                for value in sorted({period(day) for day in reference})}
            if _canonical(summary[field]) != _canonical(expected_periods):
                raise ComparisonRefusal("upstream_context_calendar_counts_mismatch")
    if report["requires_operator_choice"] is not any(failed_prerequisites):
        raise ComparisonRefusal("upstream_power_failure_not_preserved")
    evidence = report["input_evidence"]
    expected_evidence = {"upstream_report_sha256": cq.UPSTREAM_SHA256,
        "upstream_report_path": cq.REGISTERED_PATHS["upstream_path"],
        "feature_report_sha256": cq.FEATURE_SHA256, "feature_report_path": cq.REGISTERED_PATHS["feature_path"],
        "calibration_input_evidence": projected["input_evidence"],
        "feature_input_evidence": {"upstream_report_sha256": cg.UPSTREAM_SHA256,
            "archive_path": cg.REGISTERED_PATHS["input_archive_path"], "compressed_sha256": cg.INPUT_COMPRESSED_SHA256,
            "stream_sha256": cg.INPUT_STREAM_SHA256,
            **{name: projected["input_evidence"][name] for name in EXPECTED_INPUT_COUNTS}}}
    if _canonical(evidence) != _canonical(expected_evidence):
        raise ComparisonRefusal("upstream_input_lineage_mismatch")
    return reference


def _validate_upstream(report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    try:
        return _validate_contract(report, checkpoint)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, cq.RecoveryContextRefusal,
            co.PeerContextRefusal, cn.ComparisonRefusal, horizons.HorizonRefusal) as exc:
        raise ComparisonRefusal("malformed_upstream_contract") from exc


def _describe(values: list[Fraction]) -> dict:
    return {**cp._describe(values), "gross_positive_fraction": sum(value > 0 for value in values) / len(values)}


def _statistics(records: list[dict], reference: list[str], checkpoint: Callable[[], None]) -> dict:
    try:
        return cp._statistics(records, reference, checkpoint)
    except cp.ComparisonRefusal as exc:
        raise ComparisonRefusal(str(exc)) from exc


def build_comparison(cq_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-input comparison under the specific failed-power exception."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    reference = _validate_upstream(cq_report, checkpoint)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "exploratory_authorization": exploratory_authorization(),
        "qualified_cells_power_prerequisite_met": not cq_report["requires_operator_choice"], "requires_operator_choice": False,
        "upstream_calibration_requires_operator_choice": cq_report["requires_operator_choice"],
        "upstream_original_calibration_requires_operator_choice": cq_report["upstream_calibration_requires_operator_choice"],
        "upstream_legacy_calibration_requires_operator_choice": cq_report["upstream_legacy_calibration_requires_operator_choice"],
        "input_evidence": {"upstream_report_sha256": UPSTREAM_SHA256,
            "upstream_report_path": REGISTERED_PATHS["upstream_path"],
            "calibration_input_evidence": cq_report["input_evidence"],
            "upstream_provenance": {name: cq_report[name] for name in ("specification_sha256", "source_sha256", "runtime")}},
        "variants": {}}
    for key, variant in cq_report["variants"].items():
        checkpoint()
        original = [row for row in variant["days"] if row["families"][RECOVERY]["label_cohort_state"] == "selected_complete"]
        ids = [(row["session"], row["symbol"], row["families"][RECOVERY]["event"]["signal_bar_end"],
            row["families"][RECOVERY]["event"]["decision_at"]) for row in original]
        records = [{"session": row["session"], "label": horizons._ratio(row["families"][RECOVERY]["labels"]["time_only"]["price_change"]),
            "selected": row["families"][RECOVERY]["recovery_context"]["label_cohort_state"] == "selected_complete"} for row in original]
        represented = [(row["session"], row["symbol"], row["families"][RECOVERY]["event"]["signal_bar_end"],
            row["families"][RECOVERY]["event"]["decision_at"]) for row in variant["days"]
            if row["families"][RECOVERY]["labels"] is not None]
        cd.conclusion_check.require_no_blockers([
            cd.conclusion_check.check_same_population({"original_recovery_events": ids, "comparison_underlying_events": represented}),
            cd.conclusion_check.check_clustered_observations([row["session"] for row in records],
                reported_n=len({row["session"] for row in records}))])
        all_values = [row["label"] for row in records]
        selected_values = [row["label"] for row in records if row["selected"]]
        uncertainty = _statistics(records, reference, checkpoint)
        difference = float(sum(selected_values, Fraction()) / len(selected_values) - sum(all_values, Fraction()) / len(all_values))
        margin = NormalDist().inv_cdf(.975) * uncertainty["design_se"]
        weeks = sorted({cq._week(day) for day in reference})
        selection = {**_selection_counts(records), "reference_weeks": len(weeks),
            "original_opportunities_per_week": len(records) / len(weeks),
            "filtered_opportunities_per_week": len(selected_values) / len(weeks),
            "removed_opportunities_per_week": (len(records) - len(selected_values)) / len(weeks),
            "by_week": {week: _selection_counts([row for row in records if cq._week(row["session"]) == week]) for week in weeks}}
        result["variants"][key] = {**variant, "comparison": {"original": _describe(all_values), "filtered": _describe(selected_values),
            "selection": selection, "difference": {"mean_change": difference, "uncertainty": uncertainty,
                "descriptive_normal_95_interval": [difference - margin, difference + margin]}}}
    checkpoint()
    return result


def run_comparison(*, specification_path: str | Path, upstream_path: str | Path,
        output_directory: str | Path, runs_path: str | Path, budget=None,
        before_completion: Callable[[], None] | None = None) -> dict:
    """Durably start before the sole CQ read; caller owns shared budget finish."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("recovery_context_comparison_pipeline")
    checkpoint()
    spec_path, upstream, output, log = map(Path, (specification_path, upstream_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise ComparisonRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, output, log)
    for path, key in zip((upstream, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(REGISTERED_PATHS[key]):
            raise ComparisonRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ComparisonRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise ComparisonRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(cq.__file__), Path(cp.__file__), Path(co.__file__), Path(cn.__file__),
        Path(cm.__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__), ROOT / "python/research/research_budget.py",
        Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__), Path(cc.core.__file__), Path(experiment_log.__file__),
        Path(cd.conclusion_check.__file__), ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    executable = Path(sys.executable).resolve(strict=True)
    for path in (upstream, output, log):
        if any(path.resolve() == source.resolve()
                or (path.exists() and source.exists() and path.samefile(source)) for source in (*sources, executable)):
            raise ComparisonRefusal("aliased_source_or_runtime_path")
    pins = {str(path): _sha(path) for path in sources}
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise ComparisonRefusal("specification_changed")

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
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise ComparisonRefusal("upstream_size_or_hash_mismatch")
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        report = build_comparison(upstream_report, checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise ComparisonRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            if variant["recovery_context"]["selected_complete"] != EXPECTED_FILTERED_COMPLETE[key]:
                raise ComparisonRefusal("registered_filtered_population_changed")
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise ComparisonRefusal("registered_selected_population_changed")
        evidence = report["input_evidence"]["calibration_input_evidence"]["calibration_input_evidence"]
        if any(evidence[name] != value for name, value in EXPECTED_INPUT_COUNTS.items()):
            raise ComparisonRefusal("registered_raw_population_changed")
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
        if any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256:
            raise ComparisonRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise ComparisonRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha, "input_evidence": report["input_evidence"],
            "requires_operator_choice": False, "qualified_cells_power_prerequisite_met": report["qualified_cells_power_prerequisite_met"],
            "exploratory_authorization": exploratory_authorization(), **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
            "exploratory_authorization": exploratory_authorization(), "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
