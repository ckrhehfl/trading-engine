"""Compare nested per-entry opening means using the sole verified CO report.

Selection intentionally changes entry counts. Preserve every upstream row and
own-sizing cell; jointly resample sessions for the overlapping ratio estimates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
from statistics import NormalDist, stdev
import sys

from research import experiment_log
from research import intraday_peer_context as co


cn, cm, ci, ch, cg, horizons, cd, cc = co.cn, co.cm, co.ci, co.ch, co.cg, co.horizons, co.cd, co.cc
ROOT, WINDOW, OPENING = co.ROOT, dict(co.WINDOW), co.OPENING
FAMILIES = co.FAMILIES
SPECIFICATION_ID = "intraday-peer-context-comparison-v1"
MODE = "fixed-opening-breakout-positive-original-peer-context-conditional-per-entry-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-peer-context-comparison-v1.json"
PARAMETERS = {
    "scope": "opening_range_breakout_only_both_original_membership_variants_recovery_retained_as_upstream_evidence",
    "condition": "original_cg_event_prior30m_return_gt_original_equal_weight_peer_mean_and_peer_mean_strictly_gt_zero",
    "cohort": "all_original_cg_passing_complete_openings_vs_nested_positive_peer_subset_full_targets_unknowns_original120minute_cohort_retained",
    "entry_exit": "same_original_three_minute_wait_entry_open_and_time_only_entry_plus30minute_exit_open",
    "unit": "conditional_per_entry_equal_hypothetical_cash_price_change_sample_counts_intentionally_differ_selection_axis",
    "cost": "subtract_exact33_over10000_per_event_same_assumed_cost_cancels_in_difference",
    "comparison": "filtered_minus_original_conditional_mean_not_matched_pair_exit_or_portfolio_exposure_equal_causal_effect",
    "power": "retain_exact_two_co_selected_and_eight_cm_own_sizing_require_original_and_filtered_prerequisites_no_remeasurement_or_paired_error_substitution",
    "missingness": "no_zero_fill_no_rejected_arm_no_rescue_no_excluded_name_replacement_no_new_unknowns",
    "opportunities": "original_filtered_retained_removed_counts_and_all_reference_iso_week_counts_not_actual_trades_or_independent_n",
    "uncertainty": "nested_influence_naive_and_session_cluster_joint_circular_five_session_ratio_mean_bootstrap2000_seed0_maximum_se_descriptive_normal95_interval",
    "influence": "n_over_m_times_selected_times_y_minus_mu_selected_minus_y_minus_mu_all",
    "bootstrap": "joint_original_filtered_session_totals_and_counts_recompute_both_ratio_means_each_draw_discard_zero_denominator_draws_explicitly",
    "upstream_reuse": "sole_pinned_verified_co_report_saved_contract_validation_no_raw_or_cg_report_read_or_prior_truth_recertification",
    "output_scope": "two_timing_cells_original_filtered_price_cost_and_nested_mean_difference_descriptions_no_p_value_winner_edge_or_promotion"}
UPSTREAM_SHA256 = "f615f2f91ed5ccea08b0dd450e0b49e1f5f32b47467dd0cf030cedb1dccb4da2"
UPSTREAM_SPECIFICATION_SHA256 = "b1371afd86f6633215e983d32bc68fa5167b53d11b520d8ae5a9c270d49c37fc"
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-co-peer-context-20261022-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cp-peer-context-comparison-20261022-v1",
    "canonical_runs_path": co.REGISTERED_PATHS["canonical_runs_path"]}
RESOURCES = dict(co.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = co.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = co.EXPECTED_SELECTED_COMPLETE
EXPECTED_FILTERED_COMPLETE = {"D+1": 577, "D+2": 574}
EXPECTED_INPUT_COUNTS = co.EXPECTED_INPUT_COUNTS
UPSTREAM_SOURCE_ROOT = "/home/minjun4897/research-checkouts/activity-discovery-20261005"
UPSTREAM_SOURCE_PATHS = (
    "configs/research/discovery/intraday-peer-context-v1.json", "python/research/intraday_peer_context.py",
    "python/research/intraday_exit_policy_comparison.py", "python/research/intraday_exit_policy.py",
    "python/research/intraday_signal_paths.py", "python/research/intraday_entry_strength_comparison.py",
    "python/research/intraday_entry_strength.py", "python/research/research_budget.py",
    "python/research/intraday_pattern_horizons.py", "python/research/intraday_pattern_comparison.py",
    "python/research/intraday_pattern_calibration.py", "python/research/intraday_pattern_events.py",
    "python/research/experiment_log.py", "python/research/conclusion_check.py",
    "python/research/__init__.py", "python/pyproject.toml", "python/uv.lock")
FLAGS = {**co.FLAGS, "calibration_only": False, "comparison_performed": True, "peer_context_compared": True}
ASSUMPTIONS = {
    "measurement": "conditional per-entry means on original complete CG-passing openings and their nested positive-peer subset; entry counts intentionally differ",
    "selection": "same preserved underlying original events and labels; selected/rejected membership changes conditional composition, not a matched-pair exit policy or exposure-equal causal comparison",
    "cohort": "all original twenty targets per reference session per timing variant, unknowns, causal peers and original120minute future-complete conditioning retained without rescue or replacement",
    "timing": "same original three-minute entry wait and time-only30minute cap-open labels; D+1/D+2 delay universe information and overlap, not holding periods or independent replications",
    "unit": "one equal hypothetical cash unit per entry; overlapping labels are neither account returns nor actual fills and are not compounded",
    "cost": "exact33/10000 assumed per-entry cost; identical subtraction cancels from filtered-minus-original mean; historical costs remain uncertified",
    "power": "exact previous two CO selected and eight CM own-sizing cells retained; original and filtered opening arms must pass with zero new unknowns before means; no inherited exception or new own-power calculation",
    "dependence": "nested influence reflects overlapping estimates; all reference sessions including zero-event days enter cluster error and joint circular five-session2000draw seed0 bootstrap with both ratio denominators recomputed",
    "interval": "maximum naive/session-cluster/bootstrap error and corrected-to-naive ratio; normal95 difference interval is descriptive only without p-value or selection decision",
    "opportunities": "original/filtered entry counts, retention and reductions including all reference ISO weeks are hypothetical opportunities, not actual trades or independent sample size",
    "upstream": "sole pinned independently verified CO report; validate saved lineage, original CM contract and causal feature derivations without raw replay or prior vendor truth recertification",
    "interpretation": "repeated same-data discovery remains exploratory; no rejected-arm performance, winner, edge, strategy pass, direction closure, reserved access, risk change or paper/live promotion"}
COST = Fraction(33, 10000)
_canonical, _sha = co._canonical, co._sha


class ComparisonRefusal(RuntimeError):
    """Stable reason codes omit private supplied evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise ComparisonRefusal("specification_scope_mismatch")


def _validate_contract(report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    expected = {"study_id": co.SPECIFICATION_ID, "mode": co.MODE, "window": WINDOW,
        "parameters": co.PARAMETERS, "assumptions": co.ASSUMPTIONS, **co.FLAGS,
        "requires_operator_choice": False, "upstream_calibration_requires_operator_choice": False,
        "specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if (any(_canonical(report.get(name)) != _canonical(value) for name, value in expected.items())
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
        if set(variant) != {"days", "families", "peer_context"}:
            raise ComparisonRefusal("upstream_variant_contract_mismatch")
        days = []
        for row in variant["days"]:
            saved = row["families"][OPENING]
            if set(saved) != {*cm.ORIGINAL_FIELDS, "labels", "peer_context"}:
                raise ComparisonRefusal("upstream_opening_contract_mismatch")
            days.append({**row, "families": {**row["families"], OPENING: {
                name: value for name, value in saved.items() if name != "peer_context"}}})
        projected["variants"][key] = {"days": days, "families": variant["families"]}
    reference = cn._validate_upstream(projected, checkpoint)
    member_values, graphs = {}, {}
    for variant in report["variants"].values():
        rows = []
        for row in variant["days"]:
            checkpoint()
            saved, day, symbol = row["families"][OPENING], row["session"], row["symbol"]
            context = saved["peer_context"]
            if set(context) != {"feature", "selection_state", "selection_unknown_reasons", "label_cohort_state"}:
                raise ComparisonRefusal("upstream_context_contract_mismatch")
            state, reasons = co._feature({**{name: saved[name] for name in cm.ORIGINAL_FIELDS},
                "feature": context["feature"]}, day, symbol)
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
        summary = variant["peer_context"]
        counts = co._counts(rows)
        selected = counts["selected_complete"]
        sessions = len({row["session"] for row in rows if row["label_cohort_state"] == "selected_complete"})
        if (set(summary) != {*counts, "known_labels", "unknown_labels", "own_label_dispersion",
                "requires_operator_choice", "by_month", "by_week"}
                or any(_canonical(summary[name]) != _canonical(value) for name, value in counts.items())
                or type(summary["known_labels"]) is not int or summary["known_labels"] != selected
                or type(summary["unknown_labels"]) is not int or summary["unknown_labels"] != 0
                or counts["feature_unknown"] != 0 or summary["requires_operator_choice"] is not False
                or not cn._own_power_valid(summary["own_label_dispersion"], selected, sessions, reference)):
            raise ComparisonRefusal("upstream_context_counts_or_own_power_mismatch")
        for field, period in (("by_month", lambda day: day[:7]), ("by_week", co._week)):
            expected_periods = {value: co._counts([row for row in rows if period(row["session"]) == value])
                for value in sorted({period(day) for day in reference})}
            if _canonical(summary[field]) != _canonical(expected_periods):
                raise ComparisonRefusal("upstream_context_calendar_counts_mismatch")
    evidence = report["input_evidence"]
    expected_evidence = {"upstream_report_sha256": co.UPSTREAM_SHA256,
        "upstream_report_path": co.REGISTERED_PATHS["upstream_path"],
        "feature_report_sha256": co.FEATURE_SHA256, "feature_report_path": co.REGISTERED_PATHS["feature_path"],
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
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, co.PeerContextRefusal,
            cn.ComparisonRefusal, horizons.HorizonRefusal) as exc:
        raise ComparisonRefusal("malformed_upstream_contract") from exc


def _describe(values: list[Fraction]) -> dict:
    total, count = sum(values, Fraction()), len(values)
    return {"events": count, "sum_price_change": total, "mean_price_change": float(total / count),
        "mean_after_assumed_cost": float(total / count - COST),
        "gross_positive_events": sum(value > 0 for value in values),
        "cost_exceeding_events": sum(value > COST for value in values),
        "cost_exceeding_fraction": sum(value > COST for value in values) / count}


def _selection_counts(records: list[dict]) -> dict:
    original, filtered = len(records), sum(row["selected"] for row in records)
    return {"original_events": original, "filtered_events": filtered, "removed_events": original - filtered,
        "retention_fraction": filtered / original if original else None}


def _statistics(records: list[dict], reference: list[str], checkpoint: Callable[[], None]) -> dict:
    """Nested mean influence and joint session bootstrap, with varying counts."""
    n, m, groups = len(records), sum(row["selected"] for row in records), len(reference)
    if n < 2 or m < 2 or groups < 2:
        raise ComparisonRefusal("nested_uncertainty_unavailable")
    mean_all = sum((row["label"] for row in records), Fraction()) / n
    mean_selected = sum((row["label"] for row in records if row["selected"]), Fraction()) / m
    daily = {day: [Fraction(), 0, Fraction(), 0, Fraction()] for day in reference}
    influences = []
    for row in records:
        value, selected = row["label"], row["selected"]
        influence = Fraction(n, m) * selected * (value - mean_selected) - (value - mean_all)
        influences.append(float(influence))
        total = daily[row["session"]]
        total[0] += value
        total[1] += 1
        total[2] += value * selected
        total[3] += selected
        total[4] += influence
    known = sum(item[1] > 0 for item in daily.values())
    if known < 2 or sum(item[3] > 0 for item in daily.values()) < 2:
        raise ComparisonRefusal("nested_uncertainty_unavailable")
    sd = stdev(influences)
    naive = sd / math.sqrt(n)
    cluster = math.sqrt(groups / (groups - 1) * math.fsum(float(item[4]) ** 2 for item in daily.values())) / n
    totals = [(float(item[0]), item[1], float(item[2]), item[3]) for item in daily.values()]
    blocks = {size: [(math.fsum(totals[(index + offset) % groups][0] for offset in range(size)),
        sum(totals[(index + offset) % groups][1] for offset in range(size)),
        math.fsum(totals[(index + offset) % groups][2] for offset in range(size)),
        sum(totals[(index + offset) % groups][3] for offset in range(size))) for index in range(groups)]
        for size in {5, groups % 5} if size}
    rng, draws = random.Random(0), []
    for draw in range(2000):
        if draw % 32 == 0:
            checkpoint()
        sampled = [blocks[5][rng.randrange(groups)] for _ in range(groups // 5)]
        if groups % 5:
            sampled.append(blocks[groups % 5][rng.randrange(groups)])
        original_count, selected_count = (sum(item[index] for item in sampled) for index in (1, 3))
        if original_count and selected_count:
            draws.append(math.fsum(item[2] for item in sampled) / selected_count
                - math.fsum(item[0] for item in sampled) / original_count)
    if len(draws) < 2:
        raise ComparisonRefusal("insufficient_valid_bootstrap_draws")
    bootstrap = stdev(draws)
    design = max(naive, cluster, bootstrap)
    checkpoint()
    return {"original_events": n, "filtered_events": m, "reference_sessions": groups,
        "known_label_sessions": known, "influence_sd": sd, "naive_se": naive,
        "session_cluster_se": cluster, "bootstrap_se": bootstrap, "design_se": design,
        "corrected_to_naive_se_ratio": design / naive if naive else None,
        "bootstrap_zero_denominator_draws": 2000 - len(draws), "bootstrap_valid_draws": len(draws)}


def build_comparison(co_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-input selection comparison; retained own gates precede means."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    reference = _validate_upstream(co_report, checkpoint)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "qualified_cells_power_prerequisite_met": True, "requires_operator_choice": False,
        "upstream_calibration_requires_operator_choice": co_report["requires_operator_choice"],
        "upstream_original_calibration_requires_operator_choice": co_report["upstream_calibration_requires_operator_choice"],
        "upstream_legacy_calibration_requires_operator_choice": co_report["upstream_legacy_calibration_requires_operator_choice"],
        "input_evidence": {"upstream_report_sha256": UPSTREAM_SHA256,
            "upstream_report_path": REGISTERED_PATHS["upstream_path"],
            "calibration_input_evidence": co_report["input_evidence"],
            "upstream_provenance": {name: co_report[name] for name in ("specification_sha256", "source_sha256", "runtime")}},
        "variants": {}}
    for key, variant in co_report["variants"].items():
        checkpoint()
        original = [row for row in variant["days"] if row["families"][OPENING]["label_cohort_state"] == "selected_complete"]
        ids = [(row["session"], row["symbol"], row["families"][OPENING]["event"]["signal_bar_end"],
            row["families"][OPENING]["event"]["decision_at"]) for row in original]
        records = [{"session": row["session"], "label": horizons._ratio(row["families"][OPENING]["labels"]["time_only"]["price_change"]),
            "selected": row["families"][OPENING]["peer_context"]["label_cohort_state"] == "selected_complete"} for row in original]
        represented = [(row["session"], row["symbol"], row["families"][OPENING]["event"]["signal_bar_end"],
            row["families"][OPENING]["event"]["decision_at"]) for row in variant["days"]
            if row["families"][OPENING]["labels"] is not None]
        cd.conclusion_check.require_no_blockers([
            cd.conclusion_check.check_same_population({"original_opening_events": ids, "comparison_underlying_events": represented}),
            cd.conclusion_check.check_clustered_observations([row["session"] for row in records],
                reported_n=len({row["session"] for row in records}))])
        all_values = [row["label"] for row in records]
        selected_values = [row["label"] for row in records if row["selected"]]
        uncertainty = _statistics(records, reference, checkpoint)
        difference = float(sum(selected_values, Fraction()) / len(selected_values) - sum(all_values, Fraction()) / len(all_values))
        margin = NormalDist().inv_cdf(.975) * uncertainty["design_se"]
        selection = {**_selection_counts(records), "by_week": {
            week: _selection_counts([row for row in records if co._week(row["session"]) == week])
            for week in sorted({co._week(day) for day in reference})}}
        result["variants"][key] = {**variant, "comparison": {"original": _describe(all_values), "filtered": _describe(selected_values),
            "selection": selection, "difference": {"mean_change": difference, "uncertainty": uncertainty,
                "descriptive_normal_95_interval": [difference - margin, difference + margin]}}}
    checkpoint()
    return result


def run_comparison(*, specification_path: str | Path, upstream_path: str | Path,
        output_directory: str | Path, runs_path: str | Path, budget=None,
        before_completion: Callable[[], None] | None = None) -> dict:
    """Durably start before the sole CO input read; caller owns budget finish."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("peer_context_comparison_pipeline")
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
    sources = (spec_path, Path(__file__), Path(co.__file__), Path(cn.__file__), Path(cm.__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__),
        Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
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
            if variant["peer_context"]["selected_complete"] != EXPECTED_FILTERED_COMPLETE[key]:
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
            "requires_operator_choice": False, **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
            "resources": RESOURCES}, window=dict(WINDOW), evaluate=evaluate, runs_path=log)
