"""Pure documentary normalization of authenticated CS, CW and DA publications.

The caller authenticates raw bytes. No events, labels, subsets, contrasts or
uncertainty are created here. All original evidence survives unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import math
import re

SOURCES = ("CS", "CW", "DA")
VARIANTS = ("D+1", "D+2")
FAMILIES = ("opening_range_breakout", "post_drop_recovery")
REFERENCE_SESSIONS, REFERENCE_WEEKS = 257, 56
COST = Fraction(33, 10000)
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
IDENTITIES = {
    "CS": ("intraday-result-synthesis-v1", "completed_result_synthesis_within_budget",
           "completed-publication-aggregate-synthesis-no-new-labels"),
    "CW": ("intraday-opening-impulse-comparison-v1", "opening_impulse_compared_within_budget",
           "observed-label-conditional-opening-impulse-descriptive-comparison"),
    "DA": ("intraday-volume-ready-comparison-v1", "volume_ready_compared_within_budget",
           "observed-label-volume-ready-opening-volume-descriptive-comparison"),
}
COHORTS = {
    "CS": "old_CG_selected_original_120minute_complete_mask",
    "CW": "unfiltered_opening_range_each_group_own_known_30minute_labels",
    "DA": "volume_feature_ready_original_OR15_each_group_own_known_30minute_labels",
}
NEXT_HYPOTHESIS = {
    "id": "opening-range15-first-retest-bounce-v1",
    "status": "design-fixed-not-measured-no-final-comparison-family",
    "family": "opening_range_breakout",
    "universe": "same_saved_point_in_time_monthly_around_top20_targets_both_D+1_and_D+2",
    "range_minutes": 15,
    "range_high": "R_is_high_of_original_initial15minute_range",
    "breakout": "B_is_original_first_subsequent_completed5minute_close_strictly_above_R",
    "signal": "first_completed5minute_touch_bar_strictly_after_B_with_low_at_or_below_R_triggers_only_if_close_strictly_above_R",
    "invalidation": "any_earlier_completed_post_B_close_at_or_below_R_or_first_touch_close_at_or_below_R_invalidates_name_session_no_retry",
    "same_bar_breakout_retest_allowed": False,
    "filters": "none_no_CG_no_peer_no_opening_impulse_no_volume_condition",
    "availability_lag_minutes": 2,
    "decision_cutoff_minutes_from_session_open": 240,
    "decision_cutoff_clock": "retest_bar_end_plus2minutes_at_or_before_session_open_plus240minutes",
    "causal_path": "post_B_touch_may_precede_B_availability_but_decision_only_after_completed_retest_availability_no_touch_limit_fill",
    "entry": "first5minute_open_strictly_after_signal_bar_end_plus2minutes_original3minute_wait",
    "exit": "entry_plus30minute_cap_minute_open",
    "maximum_signals_per_name_session": 1,
    "cost_floor_bps": "33",
    "coverage": "all_original_targets_first_events_D_policies_unknowns_no_original_CG_or_120minute_complete_mask",
    "unknown_prefix": "incomplete_causal_prefix_remains_unknown_never_skip_to_later_signal_later_gap_does_not_erase_known_state",
    "future_labels": "new_raw_labels_at_retest_entry_and_retest_entry_plus30minute_open_never_reuse_CT_original_entry_labels",
    "next_step": "separately_registered_signal_frequency_missingness_and_own_label_calibration_before_final_comparison_family",
    "planning": {"block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
                 "ordinary_own_power_checkpoint": True, "inherited_waiver": False},
    "performance_access_in_this_synthesis": False,
    "frequency_measured": False,
    "profitability_established": False,
    "frequency_target": "evaluate15perweek_operating_fit_never_force_trades",
}
LIMITATIONS = [
    "different_conditional_cohorts_CS_old_CG120complete_CW_unfiltered_own30labels_DA_volume_ready_not_interchangeable",
    "common_known_outcomes_per56weeks_separate_observed_signal_frequency_unknown_when_not_published",
    "overlapping_rows_and_D_policies_never_summed_or_treated_as_independent_replications",
    "CL_close_endpoint_appendix_separate_from_cap_open_matrix_no_cross_endpoint_ranking",
    "all_original_targets_unknowns_source_flags_MDE_intervals_and_repeated_search_history_retained",
    "source_bound_missing_label_and_underpower_authorizations_do_not_transfer",
    "no_pooled_mean_new_contrast_statistical_test_uncertainty_winner_or_strategy_pass",
    "conditional_price_labels_are_not_realized_portfolio_returns_or_fills",
    "new_retest_design_frequency_and_own_label_calibration_not_measured_here",
    "no_reserved_forward_or_holdout_access_direction_closure_or_trading_promotion",
]


class SynthesisRefusal(ValueError):
    """A stable refusal without source values."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise SynthesisRefusal(reason)


def _finite(value: object) -> None:
    if type(value) is dict:
        _require(all(type(k) is str for k in value), "invalid_json_key")
        for item in value.values():
            _finite(item)
    elif type(value) is list:
        for item in value:
            _finite(item)
    else:
        _require(value is None or type(value) in (str, int, float, bool), "invalid_json_value")
        if type(value) is float:
            _require(math.isfinite(value), "nonfinite_source")


def _integer(value: object, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, "invalid_count")
    return value


def _ratio(value: object) -> Fraction:
    _require(type(value) is dict and set(value) == {"numerator", "denominator"}, "invalid_exact_ratio")
    n, d = value["numerator"], value["denominator"]
    _require(type(n) is str and re.fullmatch(r"-?(0|[1-9][0-9]*)", n) is not None
             and type(d) is str and re.fullmatch(r"[1-9][0-9]*", d) is not None, "invalid_exact_ratio")
    return Fraction(int(n), int(d))


def _exact(value: Fraction) -> dict:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _matches(value: object, expected: Fraction) -> None:
    _require(type(value) in (int, float) and math.isfinite(value) and value == float(expected),
             "source_arithmetic_mismatch")


def _identity(source: str, publication: dict, pin: dict) -> None:
    _require(type(pin) is dict and set(pin) == {"run_id", "report_sha256", "specification_sha256"},
             "invalid_expected_pins")
    _require(type(pin["run_id"]) is str and bool(pin["run_id"]), "invalid_expected_pins")
    _require(all(type(pin[k]) is str and re.fullmatch("[0-9a-f]{64}", pin[k]) is not None
                 for k in ("report_sha256", "specification_sha256")), "invalid_expected_pins")
    study, status, mode = IDENTITIES[source]
    record, verified = publication["record"], publication["verification"]
    _require(publication["status"] == status and record["status"] == "completed"
             and record["study_id"] == verified["study_id"] == study
             and publication["run_id"] == record["run_id"] == pin["run_id"]
             and publication["output_sha256"] == record["result"]["output_sha256"]
             == verified["report_sha256"] == pin["report_sha256"]
             and record["specification_sha256"] == verified["specification_sha256"]
             == pin["specification_sha256"], "source_identity_mismatch")
    verification_key = "verified" if source == "CS" else "verified_new_comparison_math"
    _require(verified[verification_key] is True, "unverified_source")
    _require(record["window"] == WINDOW and record["parameters"]["mode"] == mode,
             "source_convention_mismatch")
    _require(record["promotion_allowed"] is False and _integer(record["promotion_trial_increment"]) == 0,
             "source_claim_mismatch")
    if source == "CS":
        value = publication["synthesis"]
        _require(value["reference_sessions"] == 257 and value["reference_weeks"] == 56
                 and value["assumed_cost_bps"] == 33 and value["window"] == WINDOW
                 and value["uncertainty_recomputed"] is False and value["new_performance_family"] is False,
                 "source_convention_mismatch")
        _require(value["cap_minute_open_30m"]["endpoint_convention"]
                 == "entry_open_to_cap_minute_open; managed_exit_uses_its_original_triggered_open"
                 and value["close_endpoint_30m"]["endpoint_convention"]
                 == "original_three_minute_entry_plus30m_terminal_minute_close_fixed_for_both_waits",
                 "source_endpoint_mismatch")
    else:
        _require(publication["reference_sessions"] == 257 and publication["reference_weeks"] == 56
                 and publication["current_requires_operator_choice"] is False, "source_convention_mismatch")
        flags = publication["flags"]
        _require(flags["observed_label_authorization_applied"] is True
                 and flags["underpower_exception_applied"] is (source == "DA")
                 and flags["promotion_allowed"] is False, "source_authorization_not_retained")
        _require(_ratio(record["parameters"]["parameters"]["assumed_cost"]) == COST,
                 "source_cost_mismatch")


def _row(source: str, policy: str, family: str, arm: str, statistics: dict) -> dict:
    if source == "CS":
        n = _integer(statistics["opportunities"], 1)
        total = _ratio(statistics["gross_sum_bps_exact"])
        gross, net = total / n, total / n - 33
        _require(_ratio(statistics["gross_mean_bps_exact"]) == gross
                 and _ratio(statistics["net_mean_bps_exact"]) == net
                 and _ratio(statistics["net_sum_bps_exact"]) == total - 33 * n
                 and statistics["reference_sessions"] == 257 and statistics["reference_weeks"] == 56
                 and _ratio(statistics["opportunities_per_week_exact"]) == Fraction(n, 56),
                 "source_arithmetic_mismatch")
        _matches(statistics["gross_mean_bps"], gross)
        _matches(statistics["net_mean_bps"], net)
        _matches(statistics["opportunities_per_week"], Fraction(n, 56))
        events = unknown = positive = net_positive = own = None
    else:
        n, events, unknown = (_integer(statistics[k], 1 if k == "known_labels" else 0)
                              for k in ("known_labels", "event_count", "unknown_labels"))
        _require(n + unknown == events, "source_count_mismatch")
        raw_total = _ratio(statistics["gross_sum"])
        gross, net, total = raw_total / n * 10000, (raw_total / n - COST) * 10000, raw_total * 10000
        _require(_ratio(statistics["gross_mean"]) == raw_total / n
                 and _ratio(statistics["net_mean"]) == raw_total / n - COST
                 and _ratio(statistics["assumed_cost"]) == COST, "source_arithmetic_mismatch")
        positive, net_positive = (_integer(statistics[k]) for k in ("gross_positive_count", "net_positive_count"))
        _require(net_positive <= positive <= n
                 and _ratio(statistics["gross_positive_fraction"]) == Fraction(positive, n)
                 and _ratio(statistics["net_positive_fraction"]) == Fraction(net_positive, n), "source_count_mismatch")
        own = statistics["source_own_label_dispersion"]
        frequency = statistics["frequency"]
        signal_key = "selected_per_reference_week" if arm == "selected" else "signals_per_reference_week"
        _require(frequency["reference_sessions"] == 257 and frequency["reference_weeks"] == 56
                 and _ratio(frequency[signal_key]) == Fraction(events, 56)
                 and _ratio(frequency["known_labels_per_reference_week"]) == Fraction(n, 56),
                 "source_frequency_mismatch")
    return {"source": source, "policy": policy, "family": family, "arm": arm, "cohort": COHORTS[source],
        "known_labels": n, "unknown_labels": unknown, "observed_signals": events,
        "gross_sum_bps_exact": _exact(total), "gross_mean_bps_exact": _exact(gross),
        "net_mean_bps_exact": _exact(net), "gross_mean_bps": float(gross), "net_mean_bps": float(net),
        "assumed_cost_bps": 33, "known_outcomes_per_week_exact": _exact(Fraction(n, 56)),
        "observed_signals_per_week_exact": None if events is None else _exact(Fraction(events, 56)),
        "gross_positive_count": positive, "net_positive_count": net_positive,
        "source_statistics": deepcopy(statistics), "source_own_label_dispersion": deepcopy(own)}


def _synthesize(publications: dict, expected_pins: dict) -> dict:
    _require(type(publications) is dict and set(publications) == set(SOURCES)
             and type(expected_pins) is dict and set(expected_pins) == set(SOURCES), "source_population_mismatch")
    _finite(publications)
    for source in SOURCES:
        _identity(source, publications[source], expected_pins[source])
    opened, closed = [], []
    cs = publications["CS"]["synthesis"]
    for panel in ("cap_minute_open_30m", "close_endpoint_30m"):
        _require(set(cs[panel]["variants"]) == set(VARIANTS), "variant_population_mismatch")
    for policy in VARIANTS:
        for panel, arms, target in (("cap_minute_open_30m", ("baseline", "managed_exit", "context_filter"), opened),
                                    ("close_endpoint_30m", ("three_minute_wait", "one_minute_wait"), closed)):
            families = cs[panel]["variants"][policy]
            _require(set(families) == set(FAMILIES), "family_population_mismatch")
            for family in FAMILIES:
                for arm in arms:
                    target.append(_row("CS", policy, family, arm, families[family][arm]))
        for source, ranges in (("CW", ("15", "30")), ("DA", ("15",))):
            _require(set(publications[source]["comparisons"]) == set(VARIANTS), "variant_population_mismatch")
            by_arm = publications[source]["comparisons"][policy]["by_arm"]
            _require(set(by_arm) == set(ranges), "range_population_mismatch")
            for length in ranges:
                groups = by_arm[length]["groups"]
                _require(set(groups) == {"original", "selected"}, "group_population_mismatch")
                family = "opening_range_" + length if source == "CW" else "volume_ready_opening_range_15"
                for group in ("original", "selected"):
                    opened.append(_row(source, policy, family, group, groups[group]))
    opened.sort(key=lambda row: SOURCES.index(row["source"]))
    return {"kind": "documentary_reexpression_of_verified_candidate_publications", "window": dict(WINDOW),
        "reference_sessions": 257, "reference_weeks": 56, "assumed_cost_bps": 33,
        "cap_minute_open_30m": {"rows": opened}, "close_endpoint_30m": {"rows": closed},
        "source_publications": deepcopy(publications), "limitations": list(LIMITATIONS),
        "new_labels": 0, "new_performance_families": 0, "promotion_trial_increment": 0,
        "uncertainty_recomputed": False, "ranking_performed": False, "strategy_qualified": False,
        "direction_closed": False, "promotion_allowed": False, "actual_portfolio_pnl": False}


def synthesize(publications: dict[str, dict], *, expected_pins: dict[str, dict]) -> dict:
    """Normalize supplied authenticated aggregates without I/O or inference."""
    try:
        return _synthesize(publications, expected_pins)
    except SynthesisRefusal:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, RecursionError) as error:
        raise SynthesisRefusal("malformed_source_publication") from error
