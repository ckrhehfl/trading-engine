"""One source-bound observed-label comparison of original and fixed CV signals.

The complete supplied CV report, including its embedded CT, is provenance.
This pure builder does no IO, authentication, actual execution or retuning.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from fractions import Fraction
import math
import random
from statistics import NormalDist, stdev

from research import intraday_opening_impulse as cv


ct = cv.ct
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-opening-impulse-comparison-v1"
MODE = "observed-label-conditional-opening-impulse-descriptive-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-impulse-comparison-v1.json"
WINDOW = dict(cv.WINDOW)
ARMS = cv.ARMS
GROUPS = ("original", "selected")
UPSTREAM_SHA256 = "e791c690b8a916c1b1ed7b111719ffc2ba06aca92e31b05a5ac146760e3b4f48"
UPSTREAM_SPECIFICATION_SHA256 = "d012f691d4011e8aadcafe1dbecc89437bb27f83fbcae342ac233a3bf267015f"
PARAMETERS = {
    "axis": "original_CT_signals_versus_fixed_CV_opening_impulse_subset_only",
    "source_parameters": deepcopy(cv.PARAMETERS),
    "cohort": "each_original_or_selected_group_own_known_labels_no_common_complete_mask",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "contrasts": "selected_minus_original_conditional_per_entry_means_separately_each15_30arm_D+1_D+2",
    "uncertainty": "max_independent_arm_reference_joint_session_cluster_joint_circular_block_bootstrap",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "descriptive_interval": "normal95percent_not_confirmatory",
    "missingness_choice": "one_source_bound_observed_label_comparison_preserve_unknowns",
    "underpower_exception_applied": False,
    "output_scope": "eight_conditional_group_means_four_contrasts_no_subgroups_or_winner",
}
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cv-opening-impulse-20261023-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cw-opening-impulse-comparison-20261023-v1",
    "canonical_runs_path": cv.REGISTERED_PATHS["canonical_runs_path"],
}
RESOURCES = dict(cv.RESOURCES)
FLAGS = {**ct.FLAGS, "calibration_only": False, "comparison_performed": True,
    "requires_operator_choice": False, "observed_label_authorization_applied": True,
    "new_performance_families": 1}
AUTHORIZATION = {
    "operator_choice": "option1_one_limited_observed_label_descriptive_comparison",
    "upstream_report_sha256": UPSTREAM_SHA256,
    "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
    "unknown_label_conditional_comparison_authorized": True,
    "underpower_exception_applied": False,
    "scope": "this_CV_report_this_original_versus_fixed_subset_comparison_only_no_other_family_or_source_failure",
}
ASSUMPTIONS = {
    "population": "overlapping_original_and_selected_own_observed_labels_unequal_conditional_groups_not_matched_causal_or_equal_total_exposure",
    "missingness": "all_original_targets_signals_unknowns_retained_no_zero_fill_or_name_replacement_selection_bias_remains",
    "cost": "fixed_assumed33bp_per_entry_not_historical_cost_or_actual_fill_certificate_cancels_only_in_mean_contrast",
    "uncertainty": "joint_whole_reference_calendar_including_zero_dates_maxSE_descriptive_normal95percent_not_a_significance_test",
    "naive_reference": "independent_arm_formula_maxSE_floor_not_an_independence_or_coverage_claim",
    "frequency": "observed_opportunities_not_fills15perweek_operating_fit_never_forced_trades_or_summed_overlapping_arms",
    "policies": "D+1_and_D+2_overlap_not_independent_replications_or_holding_periods",
    "claims": "conditional_price_responses_not_portfolio_PnL_no_winner_edge_family_certification_direction_closure_reserved_access_or_promotion",
    "source": "whole_CV_and_embedded_CT_provenance_retains_original_unknowns_flags_own_sizing_metadata_and_previous_searches",
}
_canonical = ct._canonical
_COST = Fraction(33, 10000)


class OpeningImpulseComparisonRefusal(RuntimeError):
    """Stable refusal reasons never include supplied source values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise OpeningImpulseComparisonRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise OpeningImpulseComparisonRefusal(reason)


def _validate_source(source: Mapping, checkpoint: Callable[[], None]) -> dict:
    if source["study_id"] != cv.SPECIFICATION_ID or source["mode"] != cv.MODE:
        raise OpeningImpulseComparisonRefusal("wrong_source_study_or_mode")
    for name, expected in (("window", cv.WINDOW), ("parameters", cv.PARAMETERS),
            ("assumptions", cv.ASSUMPTIONS)):
        _same(source[name], expected, "source_scope_changed")
    for name, expected in cv.FLAGS.items():
        if source.get(name) is not expected:
            raise OpeningImpulseComparisonRefusal("source_certification_or_mode_flag_changed")
    if "specification_sha256" in source and source["specification_sha256"] != UPSTREAM_SPECIFICATION_SHA256:
        raise OpeningImpulseComparisonRefusal("source_specification_changed")
    original = source["upstream_provenance"]
    original_records = cv.cu._validate_source(original, checkpoint)
    reference, weeks = original["reference_sessions"], original["reference_weeks"]
    _same(source["reference_sessions"], reference, "source_reference_sessions_changed")
    _same(source["reference_weeks"], weeks, "source_reference_weeks_changed")
    if set(source["variants"]) != {"D+1", "D+2"}:
        raise OpeningImpulseComparisonRefusal("source_policy_variants_changed")
    records, choices = {}, []
    months = sorted({day[:7] for day in reference})
    for policy, variant in source["variants"].items():
        checkpoint()
        source_rows = original["variants"][policy]["days"]
        if (set(variant) != {"days", "by_arm"} or len(variant["days"]) != len(source_rows)
                or set(variant["by_arm"]) != set(ARMS)):
            raise OpeningImpulseComparisonRefusal("source_target_population_changed")
        selected = {arm: [] for arm in ARMS}
        for row, original_row in zip(variant["days"], source_rows, strict=True):
            checkpoint()
            expected = {"session": original_row["session"], "symbol": original_row["symbol"],
                "arms": {arm: cv._feature(original_row, arm) for arm in ARMS}}
            _same(row, expected, "source_feature_or_metadata_changed")
            for arm in ARMS:
                feature = row["arms"][arm]
                if feature["status"] == "selected":
                    label = feature["label"]
                    selected[arm].append({"session": row["session"], "label": None
                        if label["status"] == "unknown" else cv.cu._ratio(label["price_change"])})
        records[policy] = {}
        for arm in ARMS:
            checkpoint()
            own = ct.cc.dispersion(selected[arm], reference)
            # Only the pinned missing outcomes were authorized; sizing never was.
            if own["mde_bps"] is None or own["mde_bps"] > 33:
                raise OpeningImpulseComparisonRefusal("new_source_own_sizing_failure_not_authorized")
            reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
            weekly = {week: cv._counts([row for row in variant["days"]
                if ct._week(row["session"]) == week], arm) for week in weeks}
            expected = {**cv._counts(variant["days"], arm), "by_week": weekly,
                "by_month": {month: cv._counts([row for row in variant["days"]
                    if row["session"][:7] == month], arm) for month in months},
                "frequency": {"reference_sessions": len(reference), "reference_weeks": len(weeks),
                    "selected_per_reference_week": Fraction(len(selected[arm]), len(weeks)),
                    "known_labels_per_reference_week": Fraction(own["known_labels"], len(weeks)),
                    "weekly_operating_objective": 15,
                    "weeks_with_at_least15_selected": sum(item["selection_counts"]["selected"] >= 15
                        for item in weekly.values()),
                    "forced_trades": False, "executable_frequency_certified": False},
                "own_label_dispersion": own,
                "requires_operator_choice_before_comparison": bool(reasons),
                "comparison_choice_reasons": reasons}
            _same(variant["by_arm"][arm], expected, "source_selected_summary_or_own_sizing_changed")
            choices.append(bool(reasons))
            records[policy][arm] = {"original": original_records[policy][arm], "selected": selected[arm]}
    if source["requires_operator_choice_before_comparison"] is not any(choices):
        raise OpeningImpulseComparisonRefusal("source_choice_flag_changed")
    return records


def _interval(mean: Fraction, se: float) -> dict:
    width = NormalDist().inv_cdf(.975) * se
    return {"lower": float(mean) - width, "upper": float(mean) + width}


def _uncertainty(records: Mapping, reference: list[str], means: Mapping,
        checkpoint: Callable[[], None]) -> dict:
    known = {group: [row for row in records[group] if row["label"] is not None] for group in GROUPS}
    counts = {group: len(known[group]) for group in GROUPS}
    daily = {day: {group: [Fraction(0), 0] for group in GROUPS} for day in reference}
    for group in GROUPS:
        for row in known[group]:
            daily[row["session"]][group][0] += row["label"]
            daily[row["session"]][group][1] += 1
    known_dates = {group: sum(daily[day][group][1] > 0 for day in reference) for group in GROUPS}
    if any(counts[group] < 2 or known_dates[group] < 2 for group in GROUPS):
        raise OpeningImpulseComparisonRefusal("insufficient_known_labels_or_dates")
    sessions = len(reference)
    naive = math.sqrt(math.fsum(stdev(float(row["label"]) for row in known[group]) ** 2 / counts[group]
        for group in GROUPS))
    influences = [(daily[day]["selected"][0] - daily[day]["selected"][1] * means["selected"]) / counts["selected"]
        - (daily[day]["original"][0] - daily[day]["original"][1] * means["original"]) / counts["original"]
        for day in reference]
    cluster = math.sqrt(sessions / (sessions - 1) * math.fsum(float(value) ** 2 for value in influences))
    calendar = [daily[day] for day in reference]
    blocks = {size: [{group: (math.fsum(float(calendar[(index + offset) % sessions][group][0])
        for offset in range(size)), sum(calendar[(index + offset) % sessions][group][1]
        for offset in range(size))) for group in GROUPS} for index in range(sessions)]
        for size in {5, sessions % 5} if size}
    randomizer, draws = random.Random(0), []
    for _ in range(2000):
        checkpoint()
        sampled = [blocks[5][randomizer.randrange(sessions)] for _ in range(sessions // 5)]
        if sessions % 5:
            sampled.append(blocks[sessions % 5][randomizer.randrange(sessions)])
        n = {group: sum(block[group][1] for block in sampled) for group in GROUPS}
        if all(n.values()):
            draws.append(math.fsum(block["selected"][0] for block in sampled) / n["selected"]
                - math.fsum(block["original"][0] for block in sampled) / n["original"])
    if len(draws) < 2:
        raise OpeningImpulseComparisonRefusal("insufficient_valid_joint_bootstrap_draws")
    bootstrap = stdev(draws)
    design = max(naive, cluster, bootstrap)
    if any(not math.isfinite(value) or value < 0 for value in (naive, cluster, bootstrap, design)):
        raise OpeningImpulseComparisonRefusal("invalid_joint_uncertainty")
    return {"known_labels_by_group": counts, "known_label_sessions_by_group": known_dates,
        "reference_sessions": sessions, "naive_se": naive, "session_cluster_se": cluster,
        "bootstrap_se": bootstrap, "design_se": design,
        "corrected_to_naive_ratio": design / naive if naive else None,
        "bootstrap_valid_draws": len(draws), "bootstrap_skipped_zero_denominator_draws": 2000 - len(draws),
        "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
        "naive_reference": ASSUMPTIONS["naive_reference"]}


def build_comparison(source: Mapping, *, checkpoint: Callable[[], None] | None = None) -> dict:
    """Validate CT and CV prerequisites before the eight descriptive means.

    Small invented grids are supported. Authentication, actual full geometry,
    logging and one shared execution budget remain the caller's obligations.
    """
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        records = _validate_source(source, checkpoint)
    except (KeyError, TypeError, ValueError, cv.OpeningImpulseRefusal,
            cv.cu.OpeningRangeComparisonRefusal, ct.OpeningRangeRefusal, ct.cc.CalibrationRefusal) as exc:
        raise OpeningImpulseComparisonRefusal("malformed_source_calibration") from exc
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "operator_authorization": dict(AUTHORIZATION), "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "upstream_provenance": deepcopy(source), "reference_sessions": list(source["reference_sessions"]),
        "reference_weeks": list(source["reference_weeks"]), "variants": {}}
    for policy in source["variants"]:
        by_arm = {}
        for arm in ARMS:
            checkpoint()
            groups, means = {}, {}
            for group in GROUPS:
                group_records = records[policy][arm][group]
                values = [row["label"] for row in group_records if row["label"] is not None]
                total, n = sum(values, Fraction(0)), len(values)
                mean = total / n
                provenance = source if group == "selected" else source["upstream_provenance"]
                summary = provenance["variants"][policy]["by_arm"][arm]
                own = deepcopy(summary["own_label_dispersion"])
                positive, cost_exceeding = sum(value > 0 for value in values), sum(value > _COST for value in values)
                groups[group] = {"known_labels": n, "event_count": len(group_records),
                    "unknown_labels": len(group_records) - n, "gross_sum": total, "gross_mean": mean,
                    "net_mean": mean - _COST, "assumed_cost": _COST,
                    "gross_positive_count": positive, "gross_positive_fraction": Fraction(positive, n),
                    "net_positive_count": cost_exceeding, "net_positive_fraction": Fraction(cost_exceeding, n),
                    "source_own_label_dispersion": own, "frequency": deepcopy(summary["frequency"]),
                    "descriptive_normal95_interval": _interval(mean, own["design_se"])}
                means[group] = mean
            uncertainty = _uncertainty(records[policy][arm], result["reference_sessions"], means, checkpoint)
            difference = means["selected"] - means["original"]
            by_arm[arm] = {"groups": groups, "selected_minus_original": {
                "gross_mean_difference": difference, "net_mean_difference": difference,
                "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
                "descriptive_normal95_interval": _interval(difference, uncertainty["design_se"])}}
        result["variants"][policy] = {"by_arm": by_arm}
    checkpoint()
    return result
