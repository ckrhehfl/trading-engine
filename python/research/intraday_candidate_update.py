"""Documentary normalization of completed DB, DH, DI, DK, DN and DS aggregates.

The caller authenticates publication bytes. This pure module reads no files,
creates no labels and performs no statistical estimation or candidate ranking.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
import math
import re

SOURCES = ("DB", "DH", "DI", "DK", "DN", "DS")
VARIANTS = ("D+1", "D+2")
REFERENCE_SESSIONS, REFERENCE_WEEKS = 257, 56
COST = Fraction(33, 10000)
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
IDENTITIES = {
    "DB": ("intraday-candidate-synthesis-v1", "completed_candidate_synthesis_within_budget",
        "completed-candidate-aggregate-synthesis-no-new-labels", "verified"),
    "DH": ("intraday-breakout-retest-comparison-v1", "breakout_retest_comparison_verified_within_budget",
        "common-target-original-breakout-retest-conditional-comparison", "verified_new_comparison_math"),
    "DI": ("intraday-opening-volatility-comparison-v1", "opening_volatility_comparison_verified_within_budget",
        "DH-common-complete-basic-opening-volatility-conditional-comparison", "verified_new_opening_volatility_math"),
    "DK": ("intraday-breakout-exit-policy-v1", "breakout_exit_policy_verified_within_budget",
        "fixed-basic-breakout-time-only-versus-close-trigger-managed-exit-conditional-comparison",
        "verified_new_breakout_exit_math"),
    "DN": ("intraday-breakout-close-comparison-v1", "breakout_close_comparison_verified_within_budget",
        "fixed-original-basic-versus-top-quarter-breakout-close-conditional-comparison",
        "verified_new_breakout_close_comparison_math"),
    "DS": ("intraday-market-context-comparison-v1", "market_context_comparison_verified_within_budget",
        "fixed-observed-basic-versus-positive-observed-peer-conditional-comparison",
        "verified_new_market_context_comparison_math"),
}
ARMS = {"DH": ("basic", "retest"), "DI": ("basic", "volatility_selected"),
    "DK": ("time_only", "managed"), "DN": ("basic", "selected"), "DS": ("basic", "selected")}
COHORTS = {
    "DH": "original_basic_and_retest_common_signal_observable_and_common_outcome_complete_targets",
    "DI": "DH_common_outcome_complete_basic_and_fixed_opening_volatility_subset",
    "DK": "inherited_DH_common_complete_basic_same_entries_paired_time_only_and_managed_exit",
    "DN": "original_DK_common_complete_basic_and_fixed_top_quarter_close_subset_no_new_exclusion",
    "DS": "original_DK_book_restricted_in_both_arms_to_DR_selected_or_rejected_feature_observable_scope",
}
LIMITATIONS = [
    "common_cost_and_week_units_do_not_make_different_source_cohorts_matched",
    "DB_historical_close_endpoint_appendix_is_separate_from_cap_open_and_managed_open_rows",
    "DH_retest_changes_entry_timing_and_selection_and_is_not_a_causal_confirmation_effect",
    "DS_observed_basic_control_excludes_source_unknown_features_from_both_arms_and_is_not_unrestricted_basic",
    "overlapping_candidates_and_D_policies_are_not_independent_replications_and_frequencies_are_not_additive",
    "known_outcome_frequency_and_observed_signal_frequency_remain_distinct_unknown_is_not_zero",
    "source_statistics_MDE_intervals_flags_exceptions_unknowns_and_denominators_are_retained_unchanged",
    "no_source_bound_underpower_or_missingness_exception_transfers_to_the_next_hypothesis",
    "no_pooled_mean_new_contrast_uncertainty_estimate_ranking_winner_or_strategy_qualification",
    "conditional_price_responses_and_assumed_costs_are_not_realized_portfolio_returns_or_fills",
    "no_new_hypothesis_measurement_reserved_forward_holdout_access_direction_closure_or_promotion",
]


class SynthesisRefusal(ValueError):
    """Stable refusal codes disclose no supplied source values."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise SynthesisRefusal(reason)


def _finite(value: object) -> None:
    if type(value) is dict:
        _require(all(type(key) is str for key in value), "invalid_json_key")
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


def _same(left: object, right: object) -> None:
    _require(json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False),
        "verified_summary_mismatch")


def _identity(source: str, publication: dict, pin: dict) -> dict:
    _require(type(pin) is dict and set(pin) == {"run_id", "report_sha256", "specification_sha256"},
        "invalid_expected_pins")
    _require(type(pin["run_id"]) is str and bool(pin["run_id"])
        and all(type(pin[key]) is str and re.fullmatch("[0-9a-f]{64}", pin[key]) is not None
            for key in ("report_sha256", "specification_sha256")), "invalid_expected_pins")
    study, status, mode, flag = IDENTITIES[source]
    record, verified = publication["record"], publication["verification"]
    _require(publication["status"] == status and record["status"] == "completed"
        and record["study_id"] == study
        and publication["run_id"] == record["run_id"] == pin["run_id"]
        and publication["output_sha256"] == record["result"]["output_sha256"]
        == verified["report_sha256"] == pin["report_sha256"]
        and record["specification_sha256"] == verified["specification_sha256"] == pin["specification_sha256"],
        "source_identity_mismatch")
    _require(verified[flag] is True, "unverified_source")
    if source != "DS":
        _require(verified["study_id"] == study, "source_identity_mismatch")
    _require(record["window"] == WINDOW and record["parameters"]["mode"] == mode,
        "source_convention_mismatch")
    _require(record["promotion_allowed"] is False and _integer(record["promotion_trial_increment"]) == 0,
        "source_claim_mismatch")
    if source == "DB":
        body = publication["synthesis"]
        _require(body["window"] == WINDOW and _integer(body["reference_sessions"]) == 257
            and _integer(body["reference_weeks"]) == 56 and _integer(body["assumed_cost_bps"]) == 33,
            "source_convention_mismatch")
        for key in ("uncertainty_recomputed", "ranking_performed", "strategy_qualified", "direction_closed",
                "promotion_allowed", "actual_portfolio_pnl"):
            _require(body[key] is False, "source_claim_mismatch")
        _require(_integer(body["new_labels"]) == _integer(body["new_performance_families"])
            == _integer(body["promotion_trial_increment"]) == 0, "source_claim_mismatch")
        return body
    _require(_ratio(record["parameters"]["parameters"]["assumed_cost"]) == COST, "source_cost_mismatch")
    body = publication["diagnostic"] if source in ("DN", "DS") else publication
    _require(body["comparison_performed"] is True and body["requires_operator_choice_before_comparison"] is False
        and body["comparison_not_run_reasons"] == [], "source_comparison_unavailable")
    if source in ("DN", "DS"):
        _same(body, verified["compact_report"])
        _require(body["study_id"] == study and body["mode"] == mode and body["window"] == WINDOW
            and body["promotion_allowed"] is False and body["underpower_exception_applied"] is False,
            "source_claim_mismatch")
        _require(len(body["reference_sessions"]) == 257 and len(body["reference_weeks"]) == 56,
            "source_convention_mismatch")
    else:
        _same(body["variants"], verified["variants"])
        for key in ("comparison_performed", "comparison_not_run_reasons", "requires_operator_choice_before_comparison"):
            _same(body[key], verified[key])
        _require(body["claims"]["promotion_allowed"] is False
            and body["claims"]["underpower_exception_applied"] is False, "source_claim_mismatch")
    _require(_integer(verified["verified_reference_sessions"]) == 257
        and _integer(verified["verified_reference_weeks"]) == 56, "source_convention_mismatch")
    _require(set(body["variants"]) == set(VARIANTS), "variant_population_mismatch")
    return body


def _normalized(source: str, policy: str, family: str, arm: str, cohort: str,
        endpoint: str, n: int, observed: int | None, unknown: int | None,
        gross: Fraction, net: Fraction, total: Fraction, statistics: dict, own: object,
        frequency: object) -> dict:
    _require(net == gross - 33 and total == gross * n, "source_arithmetic_mismatch")
    _require(observed is None and unknown is None or observed == n + unknown, "source_count_mismatch")
    return {"source": source, "policy": policy, "family": family, "arm": arm, "cohort": cohort,
        "endpoint_convention": endpoint, "known_labels": n, "observed_signals": observed,
        "unknown_labels": unknown, "assumed_cost_bps": 33, "gross_sum_bps_exact": _exact(total),
        "gross_mean_bps_exact": _exact(gross), "net_mean_bps_exact": _exact(net),
        "gross_mean_bps": float(gross), "net_mean_bps": float(net),
        "known_outcomes_per_week_exact": _exact(Fraction(n, 56)),
        "observed_signals_per_week_exact": None if observed is None else _exact(Fraction(observed, 56)),
        "source_statistics": deepcopy(statistics), "source_own_label_dispersion": deepcopy(own),
        "source_frequency": deepcopy(frequency)}


def _db_rows(body: dict, panel: str) -> list[dict]:
    source_rows = body[panel]["rows"]
    _require(type(source_rows) is list, "invalid_DB_rows")
    expected = set()
    for policy in VARIANTS:
        for family in ("opening_range_breakout", "post_drop_recovery"):
            arms = ("baseline", "managed_exit", "context_filter") if panel == "cap_minute_open_30m" else ("three_minute_wait", "one_minute_wait")
            expected.update(("CS", policy, family, arm) for arm in arms)
        if panel == "cap_minute_open_30m":
            expected.update(("CW", policy, "opening_range_" + length, arm)
                for length in ("15", "30") for arm in ("original", "selected"))
            expected.update(("DA", policy, "volume_ready_opening_range_15", arm) for arm in ("original", "selected"))
    seen, rows = set(), []
    for original in source_rows:
        key = tuple(original[name] for name in ("source", "policy", "family", "arm"))
        _require(key in expected and key not in seen, "DB_row_population_mismatch")
        seen.add(key)
        n = _integer(original["known_labels"], 1)
        observed, unknown = original["observed_signals"], original["unknown_labels"]
        if observed is not None:
            _integer(observed); _integer(unknown)
        _require(_integer(original["assumed_cost_bps"]) == 33
            and _ratio(original["known_outcomes_per_week_exact"]) == Fraction(n, 56), "source_frequency_mismatch")
        _require(original["observed_signals_per_week_exact"] is None if observed is None else
            _ratio(original["observed_signals_per_week_exact"]) == Fraction(observed, 56), "source_frequency_mismatch")
        gross, net, total = (_ratio(original[name]) for name in
            ("gross_mean_bps_exact", "net_mean_bps_exact", "gross_sum_bps_exact"))
        _require(type(original["gross_mean_bps"]) in (int, float) and original["gross_mean_bps"] == float(gross)
            and type(original["net_mean_bps"]) in (int, float) and original["net_mean_bps"] == float(net),
            "source_arithmetic_mismatch")
        if panel == "close_endpoint_30m":
            endpoint = "original_three_minute_entry_plus30m_terminal_minute_close_fixed_for_both_waits"
        elif key[0] == "CS" and key[3] == "managed_exit":
            endpoint = "managed_exit_triggered_minute_open_or_entry_plus30minute_cap_open"
        else:
            endpoint = "entry_open_to_entry_plus30minute_cap_open"
        row = _normalized("DB", key[1], key[2], key[3], original["cohort"], endpoint,
            n, observed, unknown, gross, net, total, original,
            original["source_own_label_dispersion"], original["source_statistics"].get("frequency"))
        row["published_source"] = key[0]
        rows.append(row)
    _require(seen == expected, "DB_row_population_mismatch")
    return rows


def _current_row(source: str, policy: str, arm: str, variant: dict) -> dict:
    cell = variant["by_arm"][arm]
    own = cell["own_label_dispersion"]
    n, unknown = _integer(own["known_labels"], 1), _integer(own["unknown_labels"])
    observed = _integer(own["events"], 1)
    _require(observed == n + unknown and unknown == 0 and _integer(own["reference_sessions"]) == 257,
        "source_count_mismatch")
    _require(cell["sizing_passed"] is True and cell["comparison_choice_reasons"] == []
        and type(own["mde_bps"]) in (int, float) and 0 <= own["mde_bps"] <= 33,
        "source_sizing_mismatch")
    if source == "DH":
        cohort = variant["cohorts"]["common_outcome_complete"]["by_arm"][arm]
        _require(_integer(cohort["signal_counts"]["signal"]) == observed
            and _integer(cohort["label_counts"]["known"]) == n
            and _integer(cohort["label_counts"]["unknown"]) == unknown, "source_count_mismatch")
        frequency = cohort["frequency"]
    else:
        frequency = cell["frequency"]
    _require(_integer(frequency["reference_weeks"]) == 56, "source_frequency_mismatch")
    if source in ("DN", "DS"):
        _require(_integer(cell["events"]) == _integer(frequency["events"]) == observed
            and _ratio(frequency["mean_per_all_reference_weeks"]) == Fraction(observed, 56), "source_frequency_mismatch")
        _require(_integer(cell["known_labels"]) == n and _integer(cell["unknown_labels"]) == unknown,
            "source_count_mismatch")
        weekly = cell["by_week"]
        statistics = cell
    else:
        _require(_integer(frequency["reference_sessions"]) == 257
            and _ratio(frequency["signals_per_reference_week"]) == Fraction(observed, 56)
            and _ratio(frequency["known_labels_per_reference_week"]) == Fraction(n, 56), "source_frequency_mismatch")
        statistics = variant["comparison"]["by_arm"][arm]
        _require(_integer(statistics["event_count"]) == n, "source_count_mismatch")
        weekly = frequency.get("by_week")
    if weekly is not None:
        _require(type(weekly) is dict and len(weekly) == 56
            and sum(_integer(value) for value in weekly.values()) == observed, "source_frequency_mismatch")
    total, gross, net = (_ratio(statistics[name]) for name in ("gross_sum", "gross_mean", "net_mean"))
    _require(_ratio(statistics["assumed_cost"]) == COST and gross == total / n
        and net == gross - COST and _ratio(statistics["net_sum"]) == total - n * COST,
        "source_arithmetic_mismatch")
    endpoint = "managed_exit_triggered_minute_open_or_entry_plus30minute_cap_open" if source == "DK" and arm == "managed" else "entry_open_to_entry_plus30minute_cap_open"
    return _normalized(source, policy, "opening_range_15", arm, COHORTS[source], endpoint,
        n, observed, unknown, gross * 10000, net * 10000, total * 10000, statistics, own, frequency)


def synthesize(publications: dict[str, dict], *, expected_pins: dict[str, dict], next_hypothesis: dict) -> dict:
    """Re-express authenticated aggregates; retain complete source evidence."""
    try:
        _require(type(publications) is dict and set(publications) == set(SOURCES)
            and type(expected_pins) is dict and set(expected_pins) == set(SOURCES), "source_population_mismatch")
        _finite(publications); _finite(next_hypothesis)
        _require(type(next_hypothesis) is dict and bool(next_hypothesis), "missing_next_hypothesis")
        bodies = {source: _identity(source, publications[source], expected_pins[source]) for source in SOURCES}
        opened = _db_rows(bodies["DB"], "cap_minute_open_30m")
        closed = _db_rows(bodies["DB"], "close_endpoint_30m")
        for source in SOURCES[1:]:
            for policy in VARIANTS:
                variant = bodies[source]["variants"][policy]
                _require(set(variant["by_arm"]) == set(ARMS[source]), "arm_population_mismatch")
                if source in ("DH", "DI", "DK"):
                    _require(set(variant["comparison"]["by_arm"]) == set(ARMS[source]), "arm_population_mismatch")
                for arm in ARMS[source]:
                    opened.append(_current_row(source, policy, arm, variant))
        return {"kind": "documentary_update_of_verified_candidate_publications", "window": dict(WINDOW),
            "reference_sessions": 257, "reference_weeks": 56, "assumed_cost_bps": 33,
            "cap_minute_open_30m": {"rows": opened}, "close_endpoint_30m": {"rows": closed},
            "source_publications": deepcopy(publications), "source_pins": deepcopy(expected_pins),
            "next_hypothesis": deepcopy(next_hypothesis), "limitations": list(LIMITATIONS),
            "new_labels": 0, "new_performance_families": 0, "promotion_trial_increment": 0,
            "uncertainty_recomputed": False, "ranking_performed": False, "pooled_estimation_performed": False,
            "strategy_qualified": False, "direction_closed": False, "promotion_allowed": False,
            "actual_portfolio_pnl": False, "next_hypothesis_measured": False}
    except SynthesisRefusal:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, RecursionError) as error:
        raise SynthesisRefusal("malformed_source_publication") from error
