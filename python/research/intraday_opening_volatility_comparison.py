"""One fixed opening-volatility subset of the completed DH basic population.

Only a caller-authenticated completed DH report is consumed. There is no raw
replay, redetection, label recovery, IO or inherited underpower exception.
All four fresh own-label gates precede the new descriptive comparison.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date
from fractions import Fraction
import math
import re

from research import intraday_breakout_retest_comparison as dh


ct, cu, de, cc = dh.ct, dh.cu, dh.de, dh.cc
ROOT = dh.ROOT
SPECIFICATION_ID = "intraday-opening-volatility-comparison-v1"
MODE = "DH-common-complete-basic-opening-volatility-conditional-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-volatility-comparison-v1.json"
WINDOW = dict(dh.WINDOW)
POLICIES = dh.POLICIES
ARMS = ("basic", "volatility_selected")
STATES = ("selected", "rejected", "unknown", "not_applicable")
COST = Fraction(33, 10000)
UPSTREAM_SPECIFICATION_SHA256 = "be151704d4a158272360b8a383554bb464db3d373b690cd6622a02a748e47601"
SOURCE_ROLES = {"common_observation": "pinned_completed_full_DH_common_observation_report"}
OUTPUT_NAME = "intraday-di-opening-volatility-comparison-20261024-v1"
RESOURCES = dict(dh.RESOURCES)
PARAMETERS = {
    "axis": "DH_common_outcome_complete_OR15_basic_versus_fixed_opening_volatility_subset",
    "predicate": "first_three_completed5minute_context_max_high_minus_min_low_divided_by_first_open_greater_or_equal_threshold",
    "opening_volatility_threshold": {"numerator": "33", "denominator": "10000"},
    "context": "original_basic_event_context_first_three_bars_full_opening_clock_and_all_OHLC_validated_even_rejected",
    "cohort": "unchanged_DH_common_outcome_complete_then_basic_signal_and_fixed_subset_no_fresh_outcome_exclusion",
    "entry_delay_minutes": 3, "cap_minutes_after_entry": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "own_sizing_gate": "all_four_new_basic_selected_own_MDE_defined_finite_at_most33bp_before_any_new_mean",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "contrast": "volatility_selected_minus_basic_conditional_entry_means_separately_D+1_D+2",
    "uncertainty": "CU_joint_whole_session_circular5session_max_naive_cluster_bootstrapSE_normal95",
    "weekly_operating_objective": 15,
    "underpower_exception_applied": False,
    "output_scope": "full_source_inherited_common_and_selected_frequency_missingness_fresh_sizing_then_conditional_exact_gross_net_no_subgroups",
}
ASSUMPTIONS = {
    "source": "completed_pinned_DH_parent_trusted_no_CT_DC_raw_replay_or_new_historical_certification",
    "population": "same_DH_common_outcome_complete_basic_signals_selected_is_subset_no_new_outcome_waiver_or_exclusion",
    "predicate": "causal_opening_high_low_range_over_first_open_at_fixed33bp_not_signed_impulse_volume_or_fitted_threshold",
    "metadata": "available_at_is_maximum_of_three_opening_bar_availability_other_metadata_from_original_basic_event_unknowns_retained",
    "missingness": "all_original_DH_targets_masks_states_labels_unknowns_flags_and_provenance_retained_not_full_source_population_returns",
    "timing": "unchanged_original_basic_decision_plus3minute_entry_plus30minute_cap_minute_open_proxies_not_fills",
    "cost": "fixed_assumed33bp_cost_exceedance_uses_original_endpoint_gross_not_MFE_or_historical_cost_certificate",
    "power": "new_four_population_own_dispersion_before_new_means_no_old_waiver_or_recalibration_after_operating_goal_miss",
    "uncertainty": "overlapping_selected_and_basic_joint_whole_calendar_including_zero_dates_weeks_descriptive_not_confirmatory",
    "frequency": "15per_week_is_operating_fit_only_not_forced_trades_executable_entries_or_independent_observations",
    "policies": "D+1_D+2_overlap_information_availability_policies_not_holding_days_or_independent_replications",
    "claims": "no_winner_causal_filter_effect_incremental_edge_portfolio_PnL_strategy_pass_direction_closure_reserved_access_or_promotion",
}
PROVENANCE_EXCLUDE = ("variants", "source_target_matrix", "upstream_calendar")
_METADATA = ("observation_date", "available_at", "retrieved_at", "source", "is_final", "data_vintage", "evidence_level")
_CLAIMS = {"new_signals_calculated": False, "new_labels_calculated": False,
    "raw_replay_performed": False, "recovery_performed": False,
    "matched_causal_effect_claimed": False, "incremental_edge_claimed": False,
    "source_rows_overwritten": 0, "matched_control_comparison": False}
_canonical = ct._canonical


class OpeningVolatilityComparisonRefusal(RuntimeError):
    """Stable reason codes exclude private supplied values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, "source_roles": SOURCE_ROLES,
        "output_name": OUTPUT_NAME, "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise OpeningVolatilityComparisonRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise OpeningVolatilityComparisonRefusal(reason)


def _feature(row: Mapping) -> dict:
    finding = row["arms"]["basic"]
    status, event = finding["status"], finding["event"]
    metadata = ({**row["raw_chunk_metadata"], "data_vintage": row["data_vintage"]}
        if event is None else event)
    result = {name: deepcopy(metadata[name]) for name in _METADATA}
    result.update(original_signal_status=status,
        status="unknown" if status == "unknown" else "not_applicable", opening_volatility=None)
    if status != "signal":
        return result
    context, decision, opening = event["context"], cu._instant(event["decision_at"]), cc.session_open(row["session"])
    if len(context) != 3:
        raise OpeningVolatilityComparisonRefusal("invalid_opening_context_length")
    highs, lows, availability = [], [], []
    for index, bar in enumerate(context):
        start, end, available = (cu._instant(bar[name]) for name in ("start_at", "end_at", "available_at"))
        if (start != opening + index * cc.FIVE or end != start + cc.FIVE
                or available != end + 2 * cc.MINUTE or available > decision):
            raise OpeningVolatilityComparisonRefusal("invalid_or_noncausal_opening_context")
        op, high, low, close = (cu._positive_price(bar[name]) for name in ("open", "high", "low", "close"))
        if not low <= min(op, close) <= max(op, close) <= high:
            raise OpeningVolatilityComparisonRefusal("invalid_opening_context_OHLC")
        highs.append(high)
        lows.append(low)
        availability.append((available, bar["available_at"]))
    value = (max(highs) - min(lows)) / cu._positive_price(context[0]["open"])
    result.update(status="selected" if value >= COST else "rejected", opening_volatility=value,
        available_at=deepcopy(max(availability, key=lambda item: item[0])[1]))
    return result


def _retest_label(row: Mapping) -> None:
    finding = row["arms"]["retest"]
    if finding["status"] not in ct.STATES:
        raise OpeningVolatilityComparisonRefusal("invalid_source_retest_state")
    gaps = finding["decision_prefix_unknown_bucket_counts"]
    if set(gaps) != set(ct.GAP_STATES):
        raise OpeningVolatilityComparisonRefusal("invalid_source_retest_prefix_gaps")
    for value in gaps.values():
        cu._integer(value, 235)
    if finding["status"] != "signal":
        if finding["label"] is not None:
            raise OpeningVolatilityComparisonRefusal("source_retest_nonsignal_has_label")
        return
    label, event = finding["label"], finding["event"]
    decision, entry, exit_at = (cu._instant(value) for value in
        (event["decision_at"], label["entry_at"], label["exit_at"]))
    if (entry != decision + 3 * cc.MINUTE or exit_at != entry + 30 * cc.MINUTE
            or cu._instant(label["time_cap_decided_at"]) != decision
            or cu._instant(label["entry_price_available_at"]) != entry + 3 * cc.MINUTE
            or cu._instant(label["label_available_at"]) != exit_at + 3 * cc.MINUTE
            or label["required_minute_buckets"] != 31
            or set(label["unknown_bucket_reason_counts"]) != set(ct.GAP_STATES)
            or label["status"] not in {"known", "unknown"}):
        raise OpeningVolatilityComparisonRefusal("source_retest_label_clock_or_state_changed")
    for name in ("entry_open", "exit_open"):
        if label[name] is not None:
            cu._positive_price(label[name])


def _source(source: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    """Validate the supplied DH projection without replaying its CT/DC parents."""
    if (source["study_id"] != dh.SPECIFICATION_ID or source["mode"] != dh.MODE
            or source["specification_sha256"] != UPSTREAM_SPECIFICATION_SHA256):
        raise OpeningVolatilityComparisonRefusal("source_identity_or_specification_changed")
    for name, expected in (("window", dh.WINDOW), ("parameters", dh.PARAMETERS), ("assumptions", dh.ASSUMPTIONS)):
        _same(source[name], expected, "source_scope_changed")
    flags = {**ct.FLAGS, "calibration_only": False, "comparison_performed": True,
        "requires_operator_choice_before_comparison": False, "new_performance_families": 1, **_CLAIMS}
    for name, expected in flags.items():
        if type(source.get(name)) is not type(expected) or source[name] != expected:
            raise OpeningVolatilityComparisonRefusal("source_claim_or_choice_flag_changed")
    _same(source["comparison_not_run_reasons"], [], "source_comparison_not_completed")
    if source["pin_authentication"] != "caller_obligation_not_proven_by_pure_builder":
        raise OpeningVolatilityComparisonRefusal("source_pin_claim_changed")
    reference = source["reference_sessions"]
    if (not isinstance(reference, list) or len(reference) < 2 or reference != sorted(set(reference))
            or any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or not WINDOW["start"] <= day <= WINDOW["end"] for day in reference)):
        raise OpeningVolatilityComparisonRefusal("invalid_source_reference_sessions")
    weeks = sorted({ct._week(day) for day in reference})
    _same(source["reference_weeks"], weeks, "source_reference_weeks_changed")
    _same([row["date"] for row in source["upstream_calendar"]["rows"]
        if WINDOW["start"] <= row["date"] <= WINDOW["end"]], reference, "source_calendar_changed")
    if set(source["variants"]) != set(POLICIES) or set(source["source_target_matrix"]) != set(POLICIES):
        raise OpeningVolatilityComparisonRefusal("source_policy_variants_changed")
    provenance = source["source_provenance"]
    if set(provenance) != {"basic", "retest"}:
        raise OpeningVolatilityComparisonRefusal("source_original_provenance_changed")
    for arm, module in (("basic", ct), ("retest", dh.dc)):
        original = provenance[arm]
        for name, expected in (("study_id", module.SPECIFICATION_ID), ("mode", module.MODE),
                ("window", module.WINDOW), ("parameters", module.PARAMETERS), ("assumptions", module.ASSUMPTIONS)):
            _same(original[name], expected, "source_original_scope_changed")
        for name, expected in module.FLAGS.items():
            if original.get(name) is not expected:
                raise OpeningVolatilityComparisonRefusal("source_original_flag_changed")
        _same(original["reference_sessions"], reference, "source_original_calendar_changed")
        _same(original["reference_weeks"], weeks, "source_original_calendar_changed")
    shared = {}
    for policy in POLICIES:
        checkpoint()
        variant, memberships = source["variants"][policy], source["source_target_matrix"][policy]["days"]
        _same([item["session"] for item in memberships], reference, "source_target_dates_changed")
        expected = []
        for membership in memberships:
            day, targets = membership["session"], membership["targets"]
            if not cu._instant(membership["available_at"]) <= cu._instant(membership["selected_at"]) < cc.session_open(day):
                raise OpeningVolatilityComparisonRefusal("noncausal_source_membership")
            symbols = [target["symbol"] for target in targets]
            if (not 1 <= len(targets) <= 20 or len(symbols) != len(set(symbols))
                    or any(not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) for symbol in symbols)
                    or any(type(target["rank"]) is not int for target in targets)
                    or {target["rank"] for target in targets} != set(range(1, len(targets) + 1))):
                raise OpeningVolatilityComparisonRefusal("source_target_population_changed")
            expected.extend((day, target, membership) for target in targets)
        rows = variant["days"]
        if len(rows) != len(expected):
            raise OpeningVolatilityComparisonRefusal("source_target_population_changed")
        for row, (day, target, membership) in zip(rows, expected, strict=True):
            checkpoint()
            if (row["session"] != day or row["symbol"] != target["symbol"]
                    or set(row["arms"]) != set(dh.ARMS)):
                raise OpeningVolatilityComparisonRefusal("source_target_identity_changed")
            _same(row["membership_target"], target, "source_membership_target_changed")
            _same(row["membership_source"], {key: value for key, value in membership.items() if key != "targets"}, "source_membership_metadata_changed")
            cu._integer(row["input_rows"], 600)
            if not re.fullmatch(r"[0-9a-f]{64}", row["data_vintage"]):
                raise OpeningVolatilityComparisonRefusal("invalid_source_vintage")
            metadata = row["raw_chunk_metadata"]
            cu._instant(metadata["retrieved_at"])
            if (metadata["observation_date"] != day or metadata["available_at"] is not None
                    or metadata["is_final"] is not None or metadata["evidence_level"] != "assumed"
                    or metadata["source"] != "collector klines"):
                raise OpeningVolatilityComparisonRefusal("source_raw_metadata_changed")
            offsets = row["source_offset_counts_ms"]
            if (any(not re.fullmatch(r"0|[1-9][0-9]{0,4}", str(offset)) or int(offset) >= 60000 for offset in offsets)
                    or sum(cu._integer(count, 600) for count in offsets.values()) != row["input_rows"]):
                raise OpeningVolatilityComparisonRefusal("source_raw_offsets_changed")
            cu._finding({**row, "arms": {"15": row["arms"]["basic"]}}, "15")
            _retest_label(row)
            dh._retest({**row, "arms": {"15_retest": row["arms"]["retest"]}}, row["arms"]["basic"])
            if row["source_basic_event_timing_state"] not in ct.TIMING_STATES:
                raise OpeningVolatilityComparisonRefusal("source_original_timing_state_changed")
            observable = all(item["status"] in {"signal", "known_no_signal"} for item in row["arms"].values())
            reasons = [arm + ":unknown_signal" for arm in dh.ARMS if row["arms"][arm]["status"] == "unknown"]
            reasons.extend(arm + ":unknown_label" for arm in dh.ARMS if row["arms"][arm]["status"] == "signal" and row["arms"][arm]["label"]["status"] != "known")
            if row["signal_observable"] is not observable or row["outcome_complete"] is not (observable and not reasons):
                raise OpeningVolatilityComparisonRefusal("source_common_mask_changed")
            _same(row["exclusion_reasons"], reasons, "source_common_exclusion_reasons_changed")
            physical = {key: value for key, value in row.items() if key not in {"membership_target", "membership_source"}}
            key = day, row["symbol"]
            if key in shared:
                _same(physical, shared[key], "source_cross_policy_observation_changed")
            else:
                shared[key] = physical
        for name, cohort in (("full_source", rows), ("common_signal_observable", [row for row in rows if row["signal_observable"]]),
                ("common_outcome_complete", [row for row in rows if row["outcome_complete"]])):
            _same(variant["cohorts"][name], dh._cohort(cohort, reference, weeks), "source_cohort_counts_or_frequency_changed")
        _same(variant["timing_diagnostics"], dh._timing(rows), "source_timing_diagnostics_changed")
        for arm in dh.ARMS:
            for key, counts in dh._counts(rows, arm).items():
                _same(variant["source_by_arm"][arm][key], counts, "source_original_counts_changed")
            own = variant["by_arm"][arm]
            mde = own["own_label_dispersion"]["mde_bps"]
            if (own["sizing_passed"] is not True or own["comparison_choice_reasons"]
                    or mde is None or not math.isfinite(mde) or mde > 33 or mde < 0):
                raise OpeningVolatilityComparisonRefusal("source_incomplete_own_sizing")
        if variant["comparison"] is None:
            raise OpeningVolatilityComparisonRefusal("source_comparison_not_completed")
    return reference


def _selection_counts(rows: list[dict]) -> dict:
    counts = Counter(row["opening_volatility_feature"]["status"] for row in rows)
    return {state: counts[state] for state in STATES}


def _frequency(records: list[dict], reference: list[str], weeks: list[str]) -> dict:
    counts = Counter(ct._week(row["session"]) for row in records)
    return {"reference_sessions": len(reference), "reference_weeks": len(weeks),
        "signals_per_reference_week": Fraction(len(records), len(weeks)),
        "known_labels_per_reference_week": Fraction(len(records), len(weeks)),
        "by_week": {week: counts[week] for week in weeks},
        "weeks_with_at_least15_signals": sum(counts[week] >= 15 for week in weeks),
        "zero_signal_weeks": sum(counts[week] == 0 for week in weeks),
        "weekly_operating_objective": 15, "forced_trades": False, "executable_frequency_certified": False}


def build_comparison(dh_report: Mapping, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Preserve every DH target, calibrate four populations, then compare once.

    Invented smaller grids are supported. The private caller authenticates the
    sole parent bytes and enforces 257 sessions, 56 weeks and 5140 targets/policy.
    Undefined/failed fresh sizing completes calibration with no new means.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference = _source(dh_report, checkpoint)
    except (KeyError, TypeError, ValueError, cu.OpeningRangeComparisonRefusal, dh.BreakoutRetestComparisonRefusal,
            de.RecoveryRefusal, ct.OpeningRangeRefusal, cc.CalibrationRefusal) as exc:
        raise OpeningVolatilityComparisonRefusal("malformed_or_mismatched_DH_source") from exc
    weeks = list(dh_report["reference_weeks"])
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS),
        **ct.FLAGS, "calibration_only": False, "comparison_performed": False,
        "source_provenance": {key: deepcopy(value) for key, value in dh_report.items() if key not in PROVENANCE_EXCLUDE},
        "source_target_matrix": deepcopy(dh_report["source_target_matrix"]),
        "upstream_calendar": deepcopy(dh_report["upstream_calendar"]),
        "reference_sessions": list(reference), "reference_weeks": weeks, "variants": {},
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "comparison_not_run_reasons": [], "requires_operator_choice_before_comparison": False,
        "new_performance_families": 0, **_CLAIMS,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
    records = {}
    for policy in POLICIES:
        checkpoint()
        source_variant = dh_report["variants"][policy]
        days = []
        for row in source_variant["days"]:
            checkpoint()
            days.append({**deepcopy(row), "opening_volatility_feature": _feature(row)})
        common = [row for row in days if row["outcome_complete"]]
        selected = [row for row in common if row["opening_volatility_feature"]["status"] == "selected"]
        populations = {"basic": [row for row in common if row["arms"]["basic"]["status"] == "signal"],
            "volatility_selected": selected}
        records[policy] = {arm: [{"session": row["session"], "symbol": row["symbol"],
            "label": de._ratio(row["arms"]["basic"]["label"]["price_change"])} for row in population]
            for arm, population in populations.items()}
        by_arm = {}
        for arm in ARMS:
            checkpoint()
            own = cc.dispersion(records[policy][arm], reference)
            reasons = ["unknown_selected_own_labels"] if own["unknown_labels"] else []
            if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
                reasons.append("undefined_own_label_sizing")
            elif own["mde_bps"] > 33:
                reasons.append("own_label_mde_exceeds_assumed_cost_floor")
            by_arm[arm] = {"own_label_dispersion": own, "sizing_passed": not reasons,
                "comparison_choice_reasons": reasons, "frequency": _frequency(records[policy][arm], reference, weeks)}
            result["comparison_not_run_reasons"].extend(policy + ":" + arm + ":" + reason for reason in reasons)
        result["variants"][policy] = {"days": days,
            "source_variant_provenance": {key: deepcopy(value) for key, value in source_variant.items() if key != "days"},
            "cohorts": {**deepcopy(source_variant["cohorts"]), "volatility_selected": dh._cohort(selected, reference, weeks)},
            "selection_counts": {"full_source": _selection_counts(days), "common_outcome_complete": _selection_counts(common),
                "by_week": {week: {"full_source": _selection_counts([row for row in days if ct._week(row["session"]) == week]),
                    "common_outcome_complete": _selection_counts([row for row in common if ct._week(row["session"]) == week])} for week in weeks}},
            "by_arm": by_arm, "comparison": None}
    if not result["comparison_not_run_reasons"]:
        for policy, variant in result["variants"].items():
            checkpoint()
            summaries = {}
            for arm in ARMS:
                summary = de._summary(records[policy][arm])
                own = variant["by_arm"][arm]["own_label_dispersion"]
                summary.update(gross_descriptive_interval=cu._interval(summary["gross_mean"], own["design_se"]),
                    net_descriptive_interval=cu._interval(summary["net_mean"], own["design_se"]),
                    by_week={week: de._summary([record for record in records[policy][arm] if ct._week(record["session"]) == week]) for week in weeks})
                summaries[arm] = summary
            uncertainty = cu._uncertainty({"15": records[policy]["volatility_selected"], "30": records[policy]["basic"]}, reference,
                {"15": summaries["volatility_selected"]["gross_mean"], "30": summaries["basic"]["gross_mean"]}, checkpoint)
            for name in ("known_labels_by_arm", "known_label_sessions_by_arm"):
                uncertainty[name] = {"basic": uncertainty[name]["30"], "volatility_selected": uncertainty[name]["15"]}
            difference = summaries["volatility_selected"]["gross_mean"] - summaries["basic"]["gross_mean"]
            variant["comparison"] = {"by_arm": summaries, "volatility_selected_minus_basic": {
                "gross_mean_difference": difference, "net_mean_difference": difference,
                "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
                "descriptive_normal95_interval": cu._interval(difference, uncertainty["design_se"])}}
        result["comparison_performed"] = True
        result["new_performance_families"] = 1
    result["requires_operator_choice_before_comparison"] = bool(result["comparison_not_run_reasons"])
    checkpoint()
    return result
