"""Fixed DY attribution from supplied completed CT/DU/DX evidence only.

Original DX statistics are authenticated receipts, never recomputed. Only the
two newly timed views and the registered contrasts receive fresh statistics.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from fractions import Fraction
import math

from research import intraday_opening_acceptance_comparison as dx


ct, du, cc, dl, dm, dn = dx.ct, dx.du, dx.cc, dx.dl, dx.dm, dx.dn
ROOT, WINDOW, POLICIES = dx.ROOT, dict(dx.WINDOW), dx.POLICIES
ARMS, NEW_ARMS = ("A", "B", "C", "D"), ("B", "C")
SPECIFICATION_ID = "intraday-acceptance-timing-attribution-v1"
MODE = "fixed-same-selected-acceptance-selection-entry-exit-attribution"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-acceptance-timing-attribution-v1.json"
OUTPUT_NAME = "intraday-dy-acceptance-timing-attribution-20261026-v1"
CT_SHA256, DU_SHA256 = dx.CT_SHA256, dx.DU_SHA256
CT_SPECIFICATION_SHA256, DU_SPECIFICATION_SHA256 = dx.CT_SPECIFICATION_SHA256, dx.DU_SPECIFICATION_SHA256
DX_SHA256 = "60bc2c0ac1a6d48ec34ef09164b183495ca808758ccccfc0aa1b69b0b2da80a6"
DX_SPECIFICATION_SHA256 = "5079a61923d4d6a4b1f12ba8176632c12f60cee3cf5835fda06dcd281e06bb98"
UPSTREAM_SHA256 = {**dx.UPSTREAM_SHA256, "DX": DX_SHA256}
UPSTREAM_SPECIFICATION_SHA256 = {**dx.UPSTREAM_SPECIFICATION_SHA256, "DX": DX_SPECIFICATION_SHA256}
SOURCE_ROLES = {**dx.SOURCE_ROLES, "DX": "pinned_completed_DX_common_selected_masks_and_opaque_statistics"}
COST, RESOURCES = dx.COST, dict(dx.RESOURCES)
EXPECTED_GEOMETRY = {**dx.EXPECTED_GEOMETRY,
    "common_basic_events": {"D+1": 1174, "D+2": 1172},
    "common_selected_events": {"D+1": 806, "D+2": 803}}
PARAMETERS = {
    "cohort": "exact_completed_DX_common_basic_and_selected_coordinates_all_original_targets_unknowns_vintages",
    "views": {"A": "published_DX_basic_E0_X0", "B": "same_selected_E0_X0_hindsight_not_executable",
        "C": "same_selected_E1_X0_fixed_original_exit25minutes", "D": "published_DX_selected_E1_X1"},
    "contrasts": ["selection_B_minus_A", "waiting_C_minus_B", "exit_shift_D_minus_C", "same_name_D_minus_B"],
    "decomposition": "B_minus_A_plus_C_minus_B_plus_D_minus_C_equals_published_D_minus_A_exact_points_only",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "sizing": "four_new_B_C_own_labels_naive_sessioncluster_circular5session2000drawseed0_maxSE_two_sided5percent80percent_MDE",
    "comparison_gate": "all_four_new_own_MDE_defined_finite_lte33bp_no_underpower_waiver",
    "selection_uncertainty": "established_joint_nested_calendar_block_maxSE",
    "timing_uncertainty": "exact_paired_coordinate_differences_naive_sessioncluster_circular5session2000drawseed0_maxSE",
    "descriptive_interval": "normal95percent_no_pvalue_winner_or_contrast_power_certification",
    "frequency": "unchanged_DX_all_reference_weeks_including_zero_weeks_and_weeks_at_least15",
    "upstream_statistics": "authenticate_and_copy_A_D_plans_means_and_total_interval_without_replay",
    "expected_actual_geometry": EXPECTED_GEOMETRY,
}
ASSUMPTIONS = {**dx.ASSUMPTIONS,
    "source": "caller_authenticated_original_CT_DU_completed_DX_only_no_parent_pipeline_or_statistic_replay",
    "timing": "B_uses_future_acceptance_hindsight_not_fillable_E0_strategy_C_fixed_X0_25minute_exposure",
    "attribution": "arithmetic_equal_event_means_selection_waiting_exit_shift_not_independent_causal_effects",
    "derived_label": "C_is_new_exact_X0_over_E1_minus1_from_existing_original_OPEN_endpoints_no_new_price_read",
    "intervals": "point_components_add_exactly_intervals_do_not_add_paired_dependence_retained"}
FLAGS = {**dx.FLAGS, "new_labels_calculated": True, "new_endpoint_ratios_derived": True,
    "new_market_prices_acquired": False, "upstream_statistics_recalculated": False,
    "hindsight_selected_original_entry_is_executable": False}
COHORT_KEYS = ("cohort_flow", "source_counts", "unknown_reason_counts", "target_states",
    "common_basic_coordinates", "selected_coordinates", "by_session", "by_week", "source_calibration_receipts")
_canonical, _digest = dx._canonical, dx._digest
project_ct, project_du = dx.project_ct, dx.project_du
_performance, _uncertainty, _interval = dn._performance, dn._uncertainty, dn._interval


class AcceptanceTimingAttributionRefusal(RuntimeError):
    """Stable errors do not expose private source values."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AcceptanceTimingAttributionRefusal(reason)


def _same(left: object, right: object, reason: str) -> None:
    _require(_canonical(left) == _canonical(right), reason)


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "output_name": OUTPUT_NAME,
        "window": WINDOW, "source_roles": SOURCE_ROLES, "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "parameters": PARAMETERS, "resources": RESOURCES}
    try:
        valid = _canonical(spec) == _canonical(expected)
    except (TypeError, ValueError):
        valid = False
    _require(valid, "specification_scope_mismatch")


def _header(first: Mapping, second: Mapping, parent: Mapping, checkpoint: Callable[[], None]) -> dict:
    for role, projection in (("CT", first), ("DU", second)):
        identity = projection["source_identity"]
        _require(identity["role"] == role and identity["report_sha256"] == UPSTREAM_SHA256[role]
            and identity["specification_sha256"] == UPSTREAM_SPECIFICATION_SHA256[role],
            "projection_source_identity_changed")
        _same(parent["source_identity"][role], identity, "DX_original_projection_identity_changed")
    for key in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar"):
        _same(first[key], second[key], "original_calendars_or_targets_disagree")
    _require(parent["study_id"] == dx.SPECIFICATION_ID and parent["mode"] == dx.MODE
        and parent["specification_sha256"] == DX_SPECIFICATION_SHA256, "DX_identity_changed")
    for key, value in (("window", dx.WINDOW), ("parameters", dx.PARAMETERS), ("assumptions", dx.ASSUMPTIONS),
            ("reference_sessions", first["reference_sessions"]), ("reference_weeks", first["reference_weeks"])):
        _same(parent[key], value, "DX_header_scope_changed")
    flags = {**dx.FLAGS, "comparison_performed": True, "new_performance_families": 1,
        "calibration_only": False, "requires_operator_choice": False,
        "requires_operator_choice_before_comparison": False}
    for key, value in flags.items():
        _require(type(parent[key]) is type(value) and parent[key] == value, "DX_completion_flag_changed")
    _same(parent["comparison_not_run_reasons"], [], "DX_not_completed_comparison")
    _require(set(parent["variants"]) == set(POLICIES), "DX_policy_schema_changed")
    return {"CT": deepcopy(first["source_identity"]), "DU": deepcopy(second["source_identity"]),
        "DX": {"role": "DX", "report_sha256": DX_SHA256, "specification_sha256": DX_SPECIFICATION_SHA256,
            "header_sha256": _digest({key: value for key, value in parent.items() if key != "variants"}, checkpoint)}}


def _cohort(first: Mapping, second: Mapping, policy: str, checkpoint: Callable[[], None]) -> tuple[dict, dict, list]:
    """Reconcile frozen DX masks; never size or calculate an old-arm statistic."""
    a, b = first["variants"][policy], second["variants"][policy]
    reference, weeks = first["reference_sessions"], first["reference_weeks"]
    _require(len(a["targets"]) == len(b["targets"]), "original_target_population_disagrees")
    flow, states, records, endpoints = dx._cell(), [], {arm: [] for arm in ARMS}, []
    by_session, by_week = {day: dx._cell() for day in reference}, {week: dx._cell() for week in weeks}
    reasons = Counter()
    for basic, selected in zip(a["targets"], b["targets"], strict=True):
        checkpoint()
        for key in ("session", "symbol", "data_vintage", "membership_sha256", "source_metadata_sha256"):
            _same(basic[key], selected[key], "original_target_membership_or_source_vintage_disagrees")
        for original in (basic, selected):
            if original["label"] is not None:
                dx._label(original["label"], original["event"]["decision_at"], original["session"])
        state = dx._state(basic, selected)
        bs = basic["label"]["status"] if basic["label"] else "not_applicable"
        ss = selected["label"]["status"] if selected["label"] else "not_applicable"
        observable = state in {"accepted", "rejected"}
        exclusions = ["acceptance_feature_unknown"] if state == "feature_unknown" else []
        if observable and bs == "unknown":
            exclusions.append("original_basic_label_unknown")
        if state == "accepted" and ss == "unknown":
            exclusions.append("original_acceptance_label_unknown")
        common = observable and not exclusions
        row = {"session": basic["session"], "symbol": basic["symbol"], "data_vintage": basic["data_vintage"],
            "CT_source_row_sha256": basic["source_row_sha256"], "DU_source_row_sha256": selected["source_row_sha256"],
            "membership_sha256": basic["membership_sha256"], "source_metadata_sha256": basic["source_metadata_sha256"],
            "basic_status": basic["status"], "acceptance_status": selected["status"],
            "basic_label_status": bs, "selected_label_status": ss, "state": state,
            "feature_observable": observable, "in_common_scope": common, "exclusion_reasons": exclusions,
            "CT_unknown_reasons": basic["unknown_reasons"], "DU_unknown_reasons": selected["unknown_reasons"]}
        states.append(row)
        for cell in (flow, by_session[row["session"]], by_week[ct._week(row["session"])]):
            cell["targets"] += 1
            cell["state_counts"][state] += 1
            cell["CT_signal_counts"][basic["status"]] += 1
            cell["DU_signal_counts"][selected["status"]] += 1
            if bs != "not_applicable":
                cell["CT_label_counts"][bs] += 1
            if ss != "not_applicable":
                cell["DU_label_counts"][ss] += 1
            cell["feature_observable_basic_signals"] += observable
            cell["base_label_unknown_on_feature_scope"] += observable and bs == "unknown"
            cell["accepted_label_unknown_on_feature_scope"] += state == "accepted" and ss == "unknown"
            cell["accepted_known_lost_to_base_label_unknown"] += state == "accepted" and ss == "known" and bs == "unknown"
            cell["excluded_basic_signal_targets"] += basic["status"] == "signal" and not common
            cell["common_basic_events"] += common
            cell["common_selected_events"] += common and state == "accepted"
        reasons.update(exclusions)
        if not common:
            continue
        coordinate = {"session": row["session"], "symbol": row["symbol"]}
        original = basic["label"]
        gross_b = dx._label(original, basic["event"]["decision_at"], row["session"])
        records["A"].append({**coordinate, "label": gross_b})
        if state != "accepted":
            continue
        delayed = selected["label"]
        gross_d = dx._label(delayed, selected["event"]["decision_at"], row["session"])
        _require(dl._instant(delayed["entry_at"]) == dl._instant(original["entry_at"]) + cc.FIVE
            and dl._instant(delayed["exit_at"]) == dl._instant(original["exit_at"]) + cc.FIVE
            and dl._instant(original["exit_at"]) - dl._instant(delayed["entry_at"]) == 25 * cc.MINUTE,
            "attribution_endpoint_clocks_changed")
        gross_c = dl._price(original["exit_open"]) / dl._price(delayed["entry_open"]) - 1
        for arm, value in (("B", gross_b), ("C", gross_c), ("D", gross_d)):
            records[arm].append({**coordinate, "label": value})
        endpoints.append({**row, "original_CT_label": deepcopy(original), "original_DU_label": deepcopy(delayed),
            "B_gross": gross_b, "C_gross": gross_c, "D_gross": gross_d,
            "C_entry_at": delayed["entry_at"], "C_exit_at": original["exit_at"],
            "C_entry_open": delayed["entry_open"], "C_exit_open": original["exit_open"],
            "C_exposure_minutes": 25, "B_is_hindsight_selection": True,
            "C_is_derived_endpoint_ratio": True, "actual_fill_certified": False})
    cohort = {"cohort_flow": flow, "source_counts": {role: {"signal_counts": flow[role + "_signal_counts"],
            "label_counts": flow[role + "_label_counts"]} for role in ("CT", "DU")},
        "unknown_reason_counts": dict(sorted(reasons.items())), "target_states": states,
        "common_basic_coordinates": [{key: row[key] for key in ("session", "symbol")} for row in records["A"]],
        "selected_coordinates": [{key: row[key] for key in ("session", "symbol")} for row in records["B"]],
        "by_session": by_session, "by_week": by_week,
        "source_calibration_receipts": {"CT": deepcopy(a["source_calibration_receipts"]),
            "DU": deepcopy(b["source_calibration_receipts"])}}
    return cohort, records, endpoints


def _old_receipt(arm: Mapping, records: list) -> None:
    """Authenticate schema and internal receipt identities without old math."""
    count = len(records)
    _require(set(arm) == {"events", "own_label_dispersion", "frequency", "by_week", "sizing_passed",
        "comparison_choice_reasons", "known_labels", "unknown_labels", "gross_sum", "net_sum",
        "gross_mean", "net_mean", "gross_median", "net_median", "assumed_cost", "gross_positive_count",
        "gross_positive_fraction", "net_positive_count", "net_positive_fraction",
        "endpoint_gross_strictly_above_cost_count", "endpoint_gross_strictly_above_cost_fraction"},
        "DX_prior_arm_schema_changed")
    _require(arm["events"] == count and arm["known_labels"] == count and arm["unknown_labels"] == 0
        and arm["sizing_passed"] is True and arm["comparison_choice_reasons"] == []
        and not dx._choice(arm["own_label_dispersion"]), "DX_prior_arm_not_passed")
    for key in ("event_sd", "naive_se", "session_cluster_se", "bootstrap_se", "design_se"):
        value = arm["own_label_dispersion"][key]
        _require(not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)
            and value >= 0, "DX_prior_plan_schema_changed")
    mean, total = dl._ratio(arm["gross_mean"]), dl._ratio(arm["gross_sum"])
    _require(count > 0 and mean * count == total and dl._ratio(arm["assumed_cost"]) == COST
        and dl._ratio(arm["net_mean"]) == mean - COST and dl._ratio(arm["net_sum"]) == total - count * COST,
        "DX_prior_performance_receipt_inconsistent")
    _require(total == sum((row["label"] for row in records), Fraction()),
        "DX_prior_sum_does_not_match_original_labels")


def _paired_uncertainty(records: list, reference: list[str], checkpoint: Callable[[], None]) -> dict:
    own = dm._dispersion(records, reference, checkpoint)
    keys = ("events", "known_labels", "unknown_labels", "reference_sessions", "known_label_sessions", "event_sd",
        "naive_se", "session_cluster_se", "bootstrap_se", "design_se", "corrected_to_naive_se_ratio",
        "bootstrap_zero_label_draws")
    result = {key: own[key] for key in keys}
    _require(result["design_se"] is not None and math.isfinite(result["design_se"]), "paired_uncertainty_undefined")
    return {**result, "bootstrap_valid_draws": 2000 - own["bootstrap_zero_label_draws"],
        "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
        "paired_coordinate_differences": True, "own_arm_planning_certifies_contrast_power": False}


def _contrast(center: Fraction, uncertainty: Mapping) -> dict:
    return {"gross_mean_difference": center, "net_mean_difference": center,
        "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
        "descriptive_normal95_interval": _interval(center, uncertainty["design_se"])}


def build_attribution(ct_projection: Mapping, du_projection: Mapping, dx_report: Mapping, *,
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Complete all four new own plans before any new performance family."""
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        identity = _header(ct_projection, du_projection, dx_report, checkpoint)
        reference, weeks = ct_projection["reference_sessions"], ct_projection["reference_weeks"]
        variants, books = {}, {}
        for policy in POLICIES:
            parent = dx_report["variants"][policy]
            _require(set(parent) == set(COHORT_KEYS) | {"by_arm", "comparison_choice_reasons", "sizing_passed",
                "requires_operator_choice_before_comparison", "comparison", "reconciliation"},
                "DX_variant_schema_changed")
            cohort, records, endpoints = _cohort(ct_projection, du_projection, policy, checkpoint)
            for key in COHORT_KEYS:
                _same(parent[key], cohort[key], "DX_original_cohort_or_state_changed")
            _require(set(parent["by_arm"]) == {"basic", "selected"} and parent["sizing_passed"] is True
                and parent["requires_operator_choice_before_comparison"] is False
                and parent["comparison_choice_reasons"] == [], "DX_variant_schema_or_gate_changed")
            arms = {}
            for arm, old in (("A", "basic"), ("D", "selected")):
                _old_receipt(parent["by_arm"][old], records[arm])
                frequency, weekly = dx._frequency(records[arm], weeks, len(records["A"]))
                _same(parent["by_arm"][old]["frequency"], frequency, "DX_frequency_changed")
                _same(parent["by_arm"][old]["by_week"], weekly, "DX_weekly_counts_changed")
                arms[arm] = deepcopy(parent["by_arm"][old])
            _require(set(parent["comparison"]) == {"selected_minus_basic"}, "DX_contrast_schema_changed")
            old = parent["comparison"]["selected_minus_basic"]
            total = dl._ratio(arms["D"]["gross_mean"]) - dl._ratio(arms["A"]["gross_mean"])
            _require(dl._ratio(old["gross_mean_difference"]) == total
                and dl._ratio(old["net_mean_difference"]) == total
                and old["fixed_cost_cancels_in_mean_difference"] is True, "DX_total_receipt_inconsistent")
            _require(set(old) == {"gross_mean_difference", "net_mean_difference", "fixed_cost_cancels_in_mean_difference",
                "uncertainty", "descriptive_normal95_interval"}, "DX_contrast_schema_changed")
            uncertainty = old["uncertainty"]
            _require(set(uncertainty) == {"known_labels_by_arm", "known_label_sessions_by_arm",
                "overlapping_known_labels", "reference_sessions", "naive_se", "session_cluster_se", "bootstrap_se",
                "design_se", "corrected_to_naive_ratio", "bootstrap_valid_draws",
                "bootstrap_skipped_zero_denominator_draws", "block_sessions", "bootstrap_draws", "bootstrap_seed",
                "same_blocks_for_both_arms", "naive_reference", "own_arm_planning_certifies_contrast_power"},
                "DX_uncertainty_schema_changed")
            for key in ("naive_se", "session_cluster_se", "bootstrap_se", "design_se"):
                value = uncertainty[key]
                _require(not isinstance(value, bool) and isinstance(value, (int, float))
                    and math.isfinite(value) and value >= 0, "DX_uncertainty_schema_changed")
            _require(uncertainty["block_sessions"] == 5 and uncertainty["bootstrap_draws"] == 2000
                and uncertainty["bootstrap_seed"] == 0 and uncertainty["same_blocks_for_both_arms"] is True
                and uncertainty["own_arm_planning_certifies_contrast_power"] is False
                and uncertainty["reference_sessions"] == len(reference)
                and uncertainty["known_labels_by_arm"] == {"basic": len(records["A"]), "selected": len(records["D"])},
                "DX_uncertainty_scope_changed")
            for bound in ("lower", "upper"):
                value = old["descriptive_normal95_interval"][bound]
                _require(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value),
                    "DX_interval_schema_changed")
            for arm in NEW_ARMS:
                own = dm._dispersion(records[arm], reference, checkpoint)
                reasons = dx._choice(own)
                frequency, weekly = dx._frequency(records[arm], weeks, len(records["A"]))
                arms[arm] = {"events": len(records[arm]), "own_label_dispersion": own, "frequency": frequency,
                    "by_week": weekly, "sizing_passed": not reasons, "comparison_choice_reasons": reasons}
            reasons = [arm + ":" + reason for arm in NEW_ARMS for reason in arms[arm]["comparison_choice_reasons"]]
            variants[policy] = {**cohort, "by_arm": arms, "selected_price_attribution": endpoints,
                "comparison_choice_reasons": reasons, "sizing_passed": not reasons,
                "requires_operator_choice_before_comparison": bool(reasons), "comparison": None,
                "published_DX_total": deepcopy(old), "DX_variant_sha256": _digest(parent, checkpoint),
                "reconciliation": {"all_DX_target_states_and_cohorts_preserved": True,
                    "same_selected_coordinates_B_C_D": True, "original_unknowns_preserved": True,
                    "original_A_D_statistics_copied_without_replay": True}}
            books[policy] = records
        reasons = [policy + ":" + reason for policy in POLICIES for reason in variants[policy]["comparison_choice_reasons"]]
        if not reasons:
            for policy in POLICIES:
                variant, records = variants[policy], books[policy]
                for arm in NEW_ARMS:
                    checkpoint()
                    variant["by_arm"][arm].update(_performance(records[arm]))
                means = {arm: dl._ratio(variant["by_arm"][arm]["gross_mean"]) for arm in ARMS}
                uncertainty = _uncertainty({"basic": records["A"], "selected": records["B"]}, reference,
                    {"basic": means["A"], "selected": means["B"]}, checkpoint)
                comparison = {"selection_B_minus_A": _contrast(means["B"] - means["A"], uncertainty)}
                for name, high, low in (("waiting_C_minus_B", "C", "B"), ("exit_shift_D_minus_C", "D", "C"),
                        ("same_name_D_minus_B", "D", "B")):
                    pairs = [{"session": left["session"], "symbol": left["symbol"], "label": left["label"] - right["label"]}
                        for left, right in zip(records[high], records[low], strict=True)]
                    comparison[name] = _contrast(means[high] - means[low], _paired_uncertainty(pairs, reference, checkpoint))
                pieces = sum((comparison[key]["gross_mean_difference"] for key in
                    ("selection_B_minus_A", "waiting_C_minus_B", "exit_shift_D_minus_C")), Fraction())
                total = dl._ratio(variant["published_DX_total"]["gross_mean_difference"])
                _require(pieces == total, "point_decomposition_failed")
                comparison["published_D_minus_A"] = deepcopy(variant["published_DX_total"])
                comparison["point_decomposition"] = {"components_sum": pieces, "published_total": total,
                    "exact_identity": True, "intervals_are_additive": False}
                variant["comparison"] = comparison
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError,
            dx.OpeningAcceptanceComparisonRefusal, dl.BreakoutLossConcentrationRefusal,
            dn.BreakoutCloseComparisonRefusal) as exc:
        raise AcceptanceTimingAttributionRefusal("malformed_or_mismatched_original_sources") from exc
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "source_identity": identity, "reference_sessions": list(reference), "reference_weeks": list(weeks),
        "variants": variants, "comparison_performed": not reasons, "new_performance_families": int(not reasons),
        "calibration_only": bool(reasons), "requires_operator_choice": bool(reasons),
        "requires_operator_choice_before_comparison": bool(reasons), "comparison_not_run_reasons": reasons,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
