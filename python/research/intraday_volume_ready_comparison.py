"""Fresh source-bound CY comparison within the fixed volume-ready population.

The existing CZ validator authenticates every supplied CY feature and label.
This pure builder selects the new control before examining label availability,
and requires both controls' own sizing before any performance mean.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from fractions import Fraction
import math

from research import intraday_opening_volume_comparison as cz

cy, ct, cu, cw = cz.cy, cz.ct, cz.cu, cz.cw
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-volume-ready-comparison-v1"
MODE = "observed-label-volume-ready-opening-volume-descriptive-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-volume-ready-comparison-v1.json"
WINDOW, ARMS, GROUPS = dict(cz.WINDOW), cz.ARMS, cz.GROUPS
UPSTREAM_SHA256 = cz.UPSTREAM_SHA256
UPSTREAM_SPECIFICATION_SHA256 = cz.UPSTREAM_SPECIFICATION_SHA256
REGISTERED_PATHS = {**cz.REGISTERED_PATHS,
    "output_directory": "/home/minjun4897/research-evidence/intraday-da-volume-ready-comparison-20261023-v1"}
RESOURCES = dict(cz.RESOURCES)
PARAMETERS = {**deepcopy(cz.PARAMETERS),
    "axis": "feature_ready_original_CT_OR15_signals_versus_fixed_CY_opening_volume_subset_only",
    "cohort": "original_signal_and_CY_feature_selected_or_rejected_before_own_label_availability_no_common_complete_mask",
    "missingness_choice": "fresh_source_bound_observed_label_comparison_preserve_unknowns",
    "new_control_sizing": "both_policies_finite_own_MDE_at_most33bp_before_any_performance_mean_no_exception"}
FLAGS = dict(cz.FLAGS)
AUTHORIZATION = {
    "operator_choice": "fresh_request_one_limited_observed_label_volume_ready_control_comparison",
    "upstream_report_sha256": UPSTREAM_SHA256,
    "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
    "unknown_label_conditional_comparison_authorized": True,
    "unknown_label_groups": ["original", "selected"],
    "underpower_exception_applied": True,
    "new_control_underpower_exception_applied": False,
    "scope": "this_CY_report_feature_ready_original_OR15_versus_fixed_volume_subset_only_control_unknown_events_subset_of_original_source_unknown_events_selected_unknown_events_unchanged_selected_finite_over33bp_MDE_new_control_finite_at_most33bp_no_undefined_or_other_source_failure",
}
ASSUMPTIONS = {**cz.ASSUMPTIONS,
    "power": "fresh_source_bound_selected_finite_over33bp_exception_only_new_volume_ready_control_must_pass_both_policies_source_flags_unchanged",
    "assessment_scope": "selected_minus_volume_ready_original_conditional_descriptive_response_calendar_name_and_label_composition_remain_not_causal_volume_filter_benefit",
    "control": "CY_feature_selected_or_rejected_on_original_signal_fixed_before_label_availability_no_unready_events_or_outcome_complete_mask"}
_canonical, _interval, _COST = cz._canonical, cz._interval, Fraction(33, 10000)


class VolumeReadyComparisonRefusal(RuntimeError):
    """Stable codes with optional mean-free, finite calibration diagnostics."""

    def __init__(self, reason: str, *, calibration_failure: Mapping | None = None):
        super().__init__(reason)
        if calibration_failure is not None:
            self.calibration_failure = deepcopy(calibration_failure)


def _sizing_failure(reason, policy, own):
    nonfinite = []
    def safe(value, path):
        if isinstance(value, float) and not math.isfinite(value):
            nonfinite.append(path)
            return None
        if isinstance(value, Mapping):
            return {key: safe(item, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(item, f"{path}[{index}]") for index, item in enumerate(value)]
        return deepcopy(value)
    failure = {"stage": "volume_ready_control_own_sizing_failed_before_comparison",
        "policy": policy, "reason": reason, "own_label_dispersion": safe(own, "own_label_dispersion"),
        "nonfinite_statistic_fields": nonfinite, "performance_means_calculated": False,
        "new_performance_families": 0, "requires_operator_choice": True,
        "new_control_underpower_exception_applied": False, "upstream_sha256": UPSTREAM_SHA256}
    return VolumeReadyComparisonRefusal(reason, calibration_failure=failure)


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise VolumeReadyComparisonRefusal("specification_scope_mismatch")


def _counts(records):
    known = sum(row["label"] is not None for row in records)
    return {"event_count": len(records), "known_labels": known, "unknown_labels": len(records) - known}


def _ready_control(source, checkpoint):
    # Reuse the complete original-source authentication, including all CY
    # feature math, original/selected labels, summaries and historical flags.
    authenticated = cz._validate_source(source, checkpoint)
    reference, weeks = source["reference_sessions"], source["reference_weeks"]
    records, summaries, accounting = {}, {}, {}
    for policy, variant in source["variants"].items():
        ready, selected, all_signals = [], [], 0
        for row in variant["days"]:
            checkpoint()
            if row["original_finding"]["status"] != "signal":
                continue
            all_signals += 1
            state = row["feature"]["status"]
            if state not in {"selected", "rejected"}:
                continue
            label = row["original_finding"]["label"]
            record = {"session": row["session"], "label": None if label["status"] == "unknown"
                else cu._ratio(label["price_change"])}
            ready.append(record)
            if state == "selected":
                selected.append(dict(record))
        cz._same(selected, authenticated[policy]["selected"], "selected_population_changed")
        own = ct.cc.dispersion(ready, reference)
        if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
            raise _sizing_failure("undefined_new_control_own_sizing_not_authorized", policy, own)
        if own["mde_bps"] > 33:
            raise _sizing_failure("new_control_own_sizing_failure_not_authorized", policy, own)
        weekly = {week: _counts([row for row in ready if ct._week(row["session"]) == week]) for week in weeks}
        summary = {**_counts(ready), "by_week": weekly,
            "by_month": {month: _counts([row for row in ready if row["session"][:7] == month])
                for month in sorted({day[:7] for day in reference})},
            "frequency": {"reference_sessions": len(reference), "reference_weeks": len(weeks),
                "signals_per_reference_week": Fraction(len(ready), len(weeks)),
                "known_labels_per_reference_week": Fraction(own["known_labels"], len(weeks)),
                "weekly_operating_objective": 15,
                "weeks_with_at_least15_signals": sum(value["event_count"] >= 15 for value in weekly.values()),
                "forced_trades": False, "executable_frequency_certified": False},
            "own_label_dispersion": own}
        records[policy] = {"original": ready, "selected": selected}
        summaries[policy] = summary
        accounting[policy] = {"original_signal_count": all_signals,
            "feature_ready_original_signal_count": len(ready),
            "feature_ready_selected_signal_count": len(selected),
            "feature_ready_rejected_signal_count": len(ready) - len(selected),
            "feature_unready_signal_count": all_signals - len(ready)}
    # Returning only after both policies pass prevents partial mean disclosure.
    return records, summaries, accounting


def build_comparison(source: Mapping, *, checkpoint: Callable[[], None] | None = None,
        calibration_callback: Callable[[dict], None] | None = None) -> dict:
    """Authenticate CY and size both new controls before four means/two contrasts."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        records, summaries, accounting = _ready_control(source, checkpoint)
    except (KeyError, TypeError, ValueError, cz.OpeningVolumeComparisonRefusal,
            cy.OpeningVolumeRefusal, cu.OpeningRangeComparisonRefusal,
            ct.OpeningRangeRefusal, ct.cc.CalibrationRefusal) as exc:
        raise VolumeReadyComparisonRefusal("malformed_source_calibration") from exc
    calibration = {"stage": "both_volume_ready_control_own_sizing_passed_before_comparison",
        "performance_means_calculated": False, "requires_operator_choice": False,
        "new_control_underpower_exception_applied": False, "upstream_sha256": UPSTREAM_SHA256,
        "by_policy": {policy: {"own_label_dispersion": deepcopy(summaries[policy]["own_label_dispersion"]),
            "readiness_accounting": deepcopy(accounting[policy])} for policy in records}}
    if calibration_callback is not None:
        calibration_callback(deepcopy(calibration))
    checkpoint()
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "operator_authorization": deepcopy(AUTHORIZATION), "upstream_sha256": UPSTREAM_SHA256,
        "control_calibration": calibration,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "source_links": {"opening_volume": {"path": REGISTERED_PATHS["upstream_path"], "sha256": UPSTREAM_SHA256,
            "specification_sha256": UPSTREAM_SPECIFICATION_SHA256, "window": deepcopy(source["window"]),
            "flags": {key: source[key] for key in cy.FLAGS},
            "by_policy": {key: deepcopy(value["by_arm"]) for key, value in source["variants"].items()},
            "requires_operator_choice_before_comparison": source["requires_operator_choice_before_comparison"],
            "source_links": deepcopy(source["source_links"]), "execution_manifest": deepcopy(source.get("execution_manifest"))}},
        "reference_sessions": list(source["reference_sessions"]),
        "reference_weeks": list(source["reference_weeks"]), "variants": {}}
    for policy, group_records in records.items():
        groups, means = {}, {}
        for group in GROUPS:
            checkpoint()
            values = [row["label"] for row in group_records[group] if row["label"] is not None]
            n, total = len(values), sum(values, Fraction(0))
            mean = total / n
            summary = source["variants"][policy]["by_arm"]["15"] if group == "selected" else summaries[policy]
            own = deepcopy(summary["own_label_dispersion"])
            positive, net_positive = sum(value > 0 for value in values), sum(value > _COST for value in values)
            groups[group] = {**_counts(group_records[group]), "gross_sum": total, "gross_mean": mean,
                "net_mean": mean - _COST, "assumed_cost": _COST,
                "gross_positive_count": positive, "gross_positive_fraction": Fraction(positive, n),
                "net_positive_count": net_positive, "net_positive_fraction": Fraction(net_positive, n),
                "source_own_label_dispersion": own, "frequency": deepcopy(summary["frequency"]),
                "descriptive_normal95_interval": _interval(mean, own["design_se"])}
            means[group] = mean
        try:
            uncertainty = cw._uncertainty(group_records, result["reference_sessions"], means, checkpoint)
        except cw.OpeningImpulseComparisonRefusal as exc:
            raise VolumeReadyComparisonRefusal("invalid_joint_uncertainty") from exc
        difference = means["selected"] - means["original"]
        result["variants"][policy] = {"by_arm": {"15": {"groups": groups,
            "readiness_accounting": accounting[policy], "volume_ready_control_summary": summaries[policy],
            "selected_minus_original": {"gross_mean_difference": difference, "net_mean_difference": difference,
                "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
                "descriptive_normal95_interval": _interval(difference, uncertainty["design_se"])}}}}
    checkpoint()
    return result
