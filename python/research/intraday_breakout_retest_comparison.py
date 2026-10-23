"""Compare saved basic breakout and first-retest policies on common targets.

No IO, raw replay, signal detection or label recovery occurs here. The caller
authenticates the two completed original reports and all execution resources.
Common masks are target masks; each arm retains its own original signal/entry.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from fractions import Fraction
import hashlib
import math

from research import intraday_opening_range_comparison as cu
from research import intraday_retest_recovery as de


ct, dc, cc = cu.ct, de.dc, de.cc
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-breakout-retest-comparison-v1"
MODE = "common-target-original-breakout-retest-conditional-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-breakout-retest-comparison-v1.json"
WINDOW = dict(ct.WINDOW)
ARMS = ("basic", "retest")
POLICIES = ("D+1", "D+2")
COST = Fraction(33, 10000)
SOURCE_ROLES = {"basic": "pinned_completed_original_CT_report",
    "retest": "pinned_completed_original_DC_report"}
OUTPUT_NAME = "intraday-dh-breakout-retest-comparison-20261024-v1"
RESOURCES = {"maximum_source_bytes": 67_108_864, "cpu_seconds": 300,
    "address_space_mib": 512, "wall_seconds": 1200, "nice": 10,
    "minimum_free_bytes": 1_073_741_824,
    "maximum_market_gets": 0, "maximum_token_posts": 0, "automatic_retries": 0}
PARAMETERS = {
    "axis": "original_OR15_basic_breakout_versus_original_first_retest_only",
    "signal_observable": "both_original_status_in_signal_or_known_no_signal",
    "outcome_complete": "signal_observable_and_every_signaled_arm_original_label_known",
    "selection": "same_target_mask_both_arms_then_each_own_original_signal_no_zero_fill",
    "entry_delay_minutes": 3, "cap_minutes_after_entry": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "own_sizing_gate": "all_four_common_outcome_arm_MDE_defined_finite_at_most33bp_before_any_mean",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "contrast": "retest_minus_basic_conditional_per_entry_means_separately_D+1_D+2",
    "uncertainty": "CU_joint_whole_session_circular5session_max_naive_cluster_bootstrapSE_normal95",
    "timing_diagnostics": "same_target_both_signal_count_and_minutes_only_no_subgroup_performance",
    "underpower_exception_applied": False,
    "output_scope": "full_source_common_observable_common_outcome_counts_frequency_all_original_weeks_and_gated_exact_gross_net",
}
ASSUMPTIONS = {
    "source": "original_completed_CT_DC_source_pins_authenticated_by_caller_no_raw_replay_or_new_historical_certification",
    "population": "common_observed_targets_with_each_arm_own_signal_unequal_entry_counts_selection_and_timing_not_causal_effect",
    "missingness": "all_original_states_labels_metadata_and_exclusion_reasons_retained_unknown_never_zero_or_no_signal",
    "timing": "original_decision_plus3minute_entry_plus30minute_cap_minute_open_prices_are_not_actual_fills",
    "cost": "fixed_assumed33bp_per_entry_not_historical_cost_or_economic_unit_certificate",
    "uncertainty": "whole_original_calendar_including_zero_dates_and_weeks_descriptive_not_confirmatory",
    "policies": "D+1_D+2_overlap_and_are_information_timing_not_holding_periods_or_independent_replications",
    "claims": "no_winner_incremental_edge_portfolio_PnL_strategy_pass_direction_closure_reserved_access_or_promotion",
}
PROVENANCE_EXCLUDE = ("variants", "source_target_matrix", "upstream_calendar")
_canonical = ct._canonical


class BreakoutRetestComparisonRefusal(RuntimeError):
    """Stable reason codes exclude private supplied values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, "source_roles": SOURCE_ROLES,
        "output_name": OUTPUT_NAME}
    if _canonical(spec) != _canonical(expected):
        raise BreakoutRetestComparisonRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise BreakoutRetestComparisonRefusal(reason)


def _bar(bar: Mapping, opening, prefix: int) -> None:
    start, end, available = (de._instant(bar[key]) for key in ("start_at", "end_at", "available_at"))
    if (start < opening or (start - opening) % cc.FIVE or end - start != cc.FIVE
            or available != end + 2 * cc.MINUTE or end > opening + prefix * cc.FIVE):
        raise BreakoutRetestComparisonRefusal("source_retest_bar_clock_changed")
    op, hi, lo, close = (cu._positive_price(bar[key]) for key in ("open", "high", "low", "close"))
    if not lo <= min(op, close) <= max(op, close) <= hi:
        raise BreakoutRetestComparisonRefusal("source_retest_bar_price_changed")


def _retest(row: Mapping, basic: Mapping) -> None:
    finding = row["arms"]["15_retest"]
    prefix = cu._integer(finding["valid_prefix_bars"], 47)
    if finding["required_bars"] != 47 or row["input_rows"] < prefix * 5:
        raise BreakoutRetestComparisonRefusal("source_retest_prefix_changed")
    opening = cc.session_open(row["session"])
    path = finding["path"]
    for name in ("breakout_bar", "first_contact_bar", "invalidation_bar"):
        if path[name] is not None:
            _bar(path[name], opening, prefix)
    if finding["status"] != "signal":
        if finding["event"] is not None:
            raise BreakoutRetestComparisonRefusal("source_retest_nonsignal_has_event")
        reasons = (["no_saved_rows_unknown_cause"] if not row["input_rows"] else
            [f"decision_prefix_{state}_minute_buckets" for state in ct.GAP_STATES
                if finding["decision_prefix_unknown_bucket_counts"][state]])
        if finding["status"] == "unknown":
            if prefix == 47 or not reasons or finding["known_no_signal_reason"] is not None:
                raise BreakoutRetestComparisonRefusal("source_retest_unknown_state_changed")
            _same(finding["unknown_reasons"], reasons, "source_retest_unknown_reasons_changed")
        else:
            if finding["unknown_reasons"]:
                raise BreakoutRetestComparisonRefusal("source_retest_no_signal_reasons_changed")
            reason = finding["known_no_signal_reason"]
            if reason == "first_contact_close_at_or_below_range_high":
                contact = path["first_contact_bar"]
                if (contact is None or path["breakout_bar"] is None or path["invalidation_bar"] != contact
                        or path["invalidation_reason"] != reason
                        or cu._positive_price(contact["low"]) > cu._positive_price(path["range_high"])
                        or cu._positive_price(contact["close"]) > cu._positive_price(path["range_high"])):
                    raise BreakoutRetestComparisonRefusal("source_retest_invalidation_changed")
            elif (reason not in {"no_breakout_before_cutoff", "no_contact_before_cutoff"}
                    or prefix != 47 or any(finding["decision_prefix_unknown_bucket_counts"].values())
                    or path["invalidation_bar"] is not None
                    or (path["breakout_bar"] is None) != (reason == "no_breakout_before_cutoff")):
                raise BreakoutRetestComparisonRefusal("source_retest_no_signal_state_changed")
        return
    event, label = finding["event"], finding["label"]
    decision = de._instant(event["decision_at"])
    if (basic["status"] != "signal" or decision <= de._instant(basic["event"]["decision_at"])
            or event["family"] != "opening_range15_first_retest_bounce"
            or event["range_minutes"] != 15 or event["parameters_sha256"] != hashlib.sha256(_canonical(dc.PARAMETERS)).hexdigest()
            or event["observation_date"] != row["session"] or event["data_vintage"] != row["data_vintage"]
            or event["retrieved_at"] != row["raw_chunk_metadata"]["retrieved_at"]
            or event["source"] != "collector klines; assumed completed five-minute bars"
            or event["is_final"] is not None or event["evidence_level"] != "assumed"
            or de._instant(event["available_at"]) != decision or decision > opening + 240 * cc.MINUTE
            or finding["unknown_reasons"] or finding["known_no_signal_reason"] is not None):
        raise BreakoutRetestComparisonRefusal("source_retest_signal_or_provenance_changed")
    breakout, contact = event["breakout_bar"], event["contact_bar"]
    _bar(breakout, opening, prefix)
    _bar(contact, opening, prefix)
    if (de._instant(breakout["start_at"]) != de._instant(basic["event"]["signal_bar_start"])
            or de._instant(breakout["end_at"]) != de._instant(basic["event"]["signal_bar_end"])
            or de._instant(contact["start_at"]) < de._instant(breakout["end_at"])
            or de._instant(contact["start_at"]) != de._instant(event["signal_bar_start"])
            or de._instant(contact["end_at"]) != de._instant(event["signal_bar_end"])
            or de._instant(contact["available_at"]) != decision
            or cu._positive_price(breakout["close"]) <= cu._positive_price(event["range_high"])
            or cu._positive_price(contact["low"]) > cu._positive_price(event["range_high"])
            or cu._positive_price(contact["close"]) <= cu._positive_price(event["range_high"])
            or path["invalidation_bar"] is not None or path["invalidation_reason"] is not None):
        raise BreakoutRetestComparisonRefusal("source_retest_chronology_or_rule_changed")
    _same(event["context"], basic["event"]["context"], "source_retest_context_changed")
    if cu._positive_price(event["range_high"]) != max(cu._positive_price(bar["high"]) for bar in event["context"]):
        raise BreakoutRetestComparisonRefusal("source_retest_range_changed")
    for key, value in (("breakout_bar", breakout), ("first_contact_bar", contact), ("range_high", event["range_high"])):
        _same(path[key], value, "source_retest_path_changed")
    # DE validates the original +3m / +30m / availability clocks first.
    if (label["evidence_level"] != "assumed" or label["actual_fill_certified"] is not False
            or de._instant(label["exit_at"]) >= cc.session_close(row["session"])
            or (de._instant(label["entry_at"]) - opening) % cc.FIVE):
        raise BreakoutRetestComparisonRefusal("source_retest_label_clock_or_claim_changed")
    unknown = cu._integer(label["unknown_minute_buckets"], 31)
    gaps = label["unknown_bucket_reason_counts"]
    if sum(cu._integer(value, 31) for value in gaps.values()) != unknown:
        raise BreakoutRetestComparisonRefusal("source_retest_label_unknown_count_changed")
    _same(label["unknown_reasons"], [f"label_{state}_minute_buckets" for state in ct.GAP_STATES if gaps[state]],
        "source_retest_label_unknown_reasons_changed")
    if label["status"] == "known":
        if unknown or de._ratio(label["price_change"]) != cu._positive_price(label["exit_open"]) / cu._positive_price(label["entry_open"]) - 1:
            raise BreakoutRetestComparisonRefusal("source_retest_label_price_ratio_changed")
    elif not unknown or label["price_change"] is not None:
        raise BreakoutRetestComparisonRefusal("source_retest_unknown_label_changed")


def _source(ct_report: Mapping, dc_report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    # Authenticate saved provenance/state contracts, without raw inputs/redetection.
    cu._validate_source(ct_report, checkpoint)
    reference, dc_rows, _ = de._source(dc_report)
    for name in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "input_evidence"):
        _same(ct_report[name], dc_report[name], "common_source_" + name + "_mismatch")
    for policy in POLICIES:
        checkpoint()
        source_rows = ct_report["variants"][policy]["days"]
        if len(source_rows) != len(dc_rows[policy]):
            raise BreakoutRetestComparisonRefusal("common_source_target_population_mismatch")
        for row in source_rows:
            checkpoint()
            other = dc_rows[policy][row["session"], row["symbol"]]
            _same({key: value for key, value in row.items() if key not in {"arms", "event_timing_state"}},
                {key: value for key, value in other.items() if key != "arms"}, "common_source_target_metadata_mismatch")
            _retest(other, row["arms"]["15"])
        variant = dc_report["variants"][policy]
        summary = variant["by_arm"]["15_retest"]
        rows = variant["days"]
        for name, expected in dc._counts(rows, "15_retest").items():
            _same(summary[name], expected, "source_retest_counts_changed")
        weeks = dc_report["reference_weeks"]
        expected_weeks = {week: dc._counts([row for row in rows if ct._week(row["session"]) == week], "15_retest") for week in weeks}
        _same(summary["by_week"], expected_weeks, "source_retest_week_counts_changed")
        _same(summary["by_month"], {month: dc._counts([row for row in rows if row["session"][:7] == month], "15_retest")
            for month in sorted({day[:7] for day in reference})}, "source_retest_month_counts_changed")
        _same(summary["frequency"], dc._frequency(dc._counts(rows, "15_retest"), expected_weeks, reference), "source_retest_frequency_changed")
    return reference


def _counts(rows: list[dict], arm: str) -> dict:
    signals = [row["arms"][arm] for row in rows if row["arms"][arm]["status"] == "signal"]
    return {"signal_counts": {state: sum(row["arms"][arm]["status"] == state for row in rows) for state in ct.STATES},
        "label_counts": {state: sum(item["label"]["status"] == state for item in signals) for state in ("known", "unknown")}}


def _cohort(rows: list[dict], reference: list[str], weeks: list[str]) -> dict:
    by_week = {week: {"target_count": sum(ct._week(row["session"]) == week for row in rows),
        "by_arm": {arm: _counts([row for row in rows if ct._week(row["session"]) == week], arm) for arm in ARMS}} for week in weeks}
    arms = {}
    for arm in ARMS:
        counts = _counts(rows, arm)
        arms[arm] = {**counts, "frequency": {"reference_sessions": len(reference), "reference_weeks": len(weeks),
            "signals_per_reference_week": Fraction(counts["signal_counts"]["signal"], len(weeks)),
            "known_labels_per_reference_week": Fraction(counts["label_counts"]["known"], len(weeks)),
            "weeks_with_at_least15_signals": sum(item["by_arm"][arm]["signal_counts"]["signal"] >= 15 for item in by_week.values()),
            "zero_signal_weeks": sum(item["by_arm"][arm]["signal_counts"]["signal"] == 0 for item in by_week.values()),
            "weekly_operating_objective": 15, "forced_trades": False, "executable_frequency_certified": False}}
    return {"target_count": len(rows), "by_arm": arms, "by_week": by_week}


def _timing(rows: list[dict]) -> dict:
    deltas = []
    for row in rows:
        if all(row["arms"][arm]["status"] == "signal" for arm in ARMS):
            delta = de._instant(row["arms"]["retest"]["event"]["decision_at"]) - de._instant(row["arms"]["basic"]["event"]["decision_at"])
            deltas.append(int(delta // cc.MINUTE))
    return {"both_signal_target_count": len(deltas),
        "retest_minus_basic_decision_minutes_counts": dict(sorted(Counter(str(value) for value in deltas).items(), key=lambda item: int(item[0]))),
        "minimum_minutes": min(deltas) if deltas else None, "maximum_minutes": max(deltas) if deltas else None,
        "performance_calculated": False}


def build_comparison(ct_report: Mapping, dc_report: Mapping,
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Gate all four own selected populations before any new outcome mean.

    Small invented twenty-name grids work. The private caller separately checks
    original exact byte pins, 257 sessions, 56 weeks and 5140 targets per policy.
    A fresh failed/undefined own gate returns preserved cohorts and no means.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference = _source(ct_report, dc_report, checkpoint)
    except (KeyError, TypeError, ValueError, cu.OpeningRangeComparisonRefusal, de.RecoveryRefusal,
            ct.OpeningRangeRefusal, cc.CalibrationRefusal) as exc:
        raise BreakoutRetestComparisonRefusal("malformed_or_mismatched_original_sources") from exc
    weeks = list(ct_report["reference_weeks"])
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS),
        **ct.FLAGS, "calibration_only": False, "exploratory_only": True,
        "source_provenance": {arm: {key: deepcopy(value) for key, value in report.items() if key not in PROVENANCE_EXCLUDE}
            for arm, report in (("basic", ct_report), ("retest", dc_report))},
        "source_target_matrix": deepcopy(ct_report["source_target_matrix"]),
        "upstream_calendar": deepcopy(ct_report["upstream_calendar"]),
        "reference_sessions": list(reference), "reference_weeks": weeks, "variants": {},
        "comparison_not_run_reasons": [], "requires_operator_choice_before_comparison": False,
        "new_performance_families": 0, "new_signals_calculated": False, "new_labels_calculated": False,
        "raw_replay_performed": False, "recovery_performed": False,
        "matched_causal_effect_claimed": False, "incremental_edge_claimed": False,
        "source_rows_overwritten": 0, "matched_control_comparison": False,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
    selected = {}
    for policy in POLICIES:
        checkpoint()
        days = []
        for basic, retest in zip(ct_report["variants"][policy]["days"], dc_report["variants"][policy]["days"], strict=True):
            checkpoint()
            arms = {"basic": deepcopy(basic["arms"]["15"]), "retest": deepcopy(retest["arms"]["15_retest"])}
            observable = all(finding["status"] in {"signal", "known_no_signal"} for finding in arms.values())
            reasons = [arm + ":unknown_signal" for arm in ARMS if arms[arm]["status"] == "unknown"]
            reasons.extend(arm + ":unknown_label" for arm in ARMS if arms[arm]["status"] == "signal" and arms[arm]["label"]["status"] != "known")
            days.append({**{key: deepcopy(value) for key, value in basic.items() if key not in {"arms", "event_timing_state"}},
                "source_basic_event_timing_state": basic["event_timing_state"], "arms": arms,
                "signal_observable": observable, "outcome_complete": observable and not reasons,
                "exclusion_reasons": reasons})
        common = [row for row in days if row["outcome_complete"]]
        records = {arm: [{"session": row["session"], "symbol": row["symbol"], "label": de._ratio(row["arms"][arm]["label"]["price_change"])}
            for row in common if row["arms"][arm]["status"] == "signal"] for arm in ARMS}
        selected[policy] = records
        by_arm = {}
        for arm in ARMS:
            checkpoint()
            own = cc.dispersion(records[arm], reference)
            reasons = []
            if own["unknown_labels"]:
                reasons.append("unknown_selected_own_labels")
            if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
                reasons.append("undefined_own_label_sizing")
            elif own["mde_bps"] > 33:
                reasons.append("own_label_mde_exceeds_assumed_cost_floor")
            by_arm[arm] = {"own_label_dispersion": own, "sizing_passed": not reasons, "comparison_choice_reasons": reasons}
            result["comparison_not_run_reasons"].extend(policy + ":" + arm + ":" + reason for reason in reasons)
        result["variants"][policy] = {"days": days,
            "source_by_arm": {"basic": deepcopy(ct_report["variants"][policy]["by_arm"]["15"]),
                "retest": deepcopy(dc_report["variants"][policy]["by_arm"]["15_retest"])},
            "cohorts": {"full_source": _cohort(days, reference, weeks),
                "common_signal_observable": _cohort([row for row in days if row["signal_observable"]], reference, weeks),
                "common_outcome_complete": _cohort(common, reference, weeks)},
            "timing_diagnostics": _timing(days), "by_arm": by_arm, "comparison": None}
    if not result["comparison_not_run_reasons"]:
        for policy, variant in result["variants"].items():
            checkpoint()
            summaries = {}
            for arm in ARMS:
                summary = de._summary(selected[policy][arm])
                own = variant["by_arm"][arm]["own_label_dispersion"]
                summary.update(gross_descriptive_interval=cu._interval(summary["gross_mean"], own["design_se"]),
                    net_descriptive_interval=cu._interval(summary["net_mean"], own["design_se"]),
                    by_week={week: de._summary([record for record in selected[policy][arm] if ct._week(record["session"]) == week]) for week in weeks})
                summaries[arm] = summary
            # CU's frozen 15-minus-30 joint-session contrast becomes retest-basic.
            uncertainty = cu._uncertainty({"15": selected[policy]["retest"], "30": selected[policy]["basic"]}, reference,
                {"15": summaries["retest"]["gross_mean"], "30": summaries["basic"]["gross_mean"]}, checkpoint)
            for name in ("known_labels_by_arm", "known_label_sessions_by_arm"):
                uncertainty[name] = {"basic": uncertainty[name]["30"], "retest": uncertainty[name]["15"]}
            difference = summaries["retest"]["gross_mean"] - summaries["basic"]["gross_mean"]
            variant["comparison"] = {"by_arm": summaries, "retest_minus_basic": {
                "gross_mean_difference": difference, "net_mean_difference": difference,
                "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
                "descriptive_normal95_interval": cu._interval(difference, uncertainty["design_se"])}}
        result["comparison_performed"] = True
        result["new_performance_families"] = 1
    result["requires_operator_choice_before_comparison"] = bool(result["comparison_not_run_reasons"])
    checkpoint()
    return result
