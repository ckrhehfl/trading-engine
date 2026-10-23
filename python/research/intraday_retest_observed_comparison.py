"""One source-bound comparison of original known retest labels, without IO.

Unknown signal targets and original unknown labels remain full provenance.
Current recovered labels never enter this newly authorized original-known scope.
Fresh selected-label own sizing precedes every conditional descriptive mean.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
import math
from statistics import NormalDist

from research import intraday_retest_recovery as de


cc, ct, dc = de.cc, de.ct, de.dc
ROOT = de.ROOT
SPECIFICATION_ID = "intraday-retest-observed-comparison-v1"
MODE = "original-known-label-retest-descriptive-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-retest-observed-comparison-v1.json"
WINDOW = dict(de.WINDOW)
REGISTERED_ROLES = {
    "source_report_role": "completed_DF_scientific_retest_report",
    "output_name": "intraday-dg-retest-observed-comparison-20261024-v1",
    "canonical_role": "existing_collector_experiment_log",
}
PARAMETERS = {
    "hypothesis": "unchanged_original_OR15_first_retest_signal_entry_and30minute_cap",
    "population": "all_original_targets_and_signal_states_retained_original_known_labels_only_no_recovered_unknown_inclusion",
    "axis": "same_original_known_retest_labels_gross_versus_net_assumed33bp",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "missing_label_choice": "source_bound_original_known_only_exploration_exclude_and_disclose_each_original_unknown_label",
    "own_sizing": "fresh_selected_known_label_cc_dispersion_unknown0_defined_finite_MDE_at_most33bp_both_policies_before_any_mean",
    "reference_calendar": "all_original_dates_and_weeks_including_zero_event_dates_and_weeks",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "uncertainty": "maximum_naive_session_cluster_and_circular5session_bootstrapSE_normal95percent_descriptive_only",
    "output_scope": "aggregate_and_original_week_counts_gross_net_mean_median_win_cost_exceedance_no_subgroups_controls_or_ranking",
    "underpower_exception_applied": False,
}
RESOURCES = {"maximum_source_bytes": 67_108_864, "minimum_free_bytes": 1_073_741_824,
    "market_gets": 0, "token_posts": 0, "cpu_seconds": 300, "address_space_mib": 512,
    "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
ASSUMPTIONS = {
    "population": "only_original_DC_known_labels_from_completed_DF_source_no_recovered_unknown_labels_or_new_signals",
    "missingness": "all_original_targets_unknown_signals_and_original_unknown_labels_retained_conditional_selection_bias_remains",
    "authorization": "new_source_bound_original_known_only_choice_not_a_global_missingness_or_underpower_waiver",
    "timing": "unchanged_original_DC_assumed_decision_entry_and30minute_cap_minute_open_prices_not_actual_fills",
    "cost": "fixed_assumed33bp_round_trip_per_event_net_equals_gross_minus33_over10000_not_historical_cost_certificate",
    "uncertainty": "all_original_reference_dates_include_zero_event_dates_session_cluster_circular5session_bootstrap2000seed0_maxSE_normal95percent_descriptive",
    "policies": "D+1_D+2_overlap_not_independent_replications_opportunities_not_executable_portfolio_entries",
    "claims": "no_controls_subgroups_ranking_incremental_edge_portfolio_PnL_strategy_pass_direction_closure_reserved_access_or_promotion",
}
FLAGS = {**{key: False for key in dc.FLAGS}, "exploratory_only": True,
    "source_bound_observed_label_authorization_applied": True,
    "incremental_edge_claimed": False, "matched_control_comparison": False,
    "new_signals_calculated": False, "source_rows_overwritten": 0}
POLICIES, ARM = de.POLICIES, de.ARM
_canonical = de._canonical


class ObservedComparisonRefusal(RuntimeError):
    """Stable scope/integrity failures exclude supplied source values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        **REGISTERED_ROLES, "parameters": PARAMETERS, "resources": RESOURCES}
    if _canonical(spec) != _canonical(expected):
        raise ObservedComparisonRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise ObservedComparisonRefusal(reason)


def _source(source: Mapping) -> tuple[list[str], dict]:
    try:
        if source["study_id"] != de.SPECIFICATION_ID or source["mode"] != de.MODE:
            raise ObservedComparisonRefusal("wrong_source_study_or_mode")
        for name, expected in (("window", de.WINDOW), ("parameters", de.PARAMETERS),
                ("assumptions", de.ASSUMPTIONS)):
            _same(source[name], expected, "source_scientific_scope_changed")
        for name in ("historical_eligibility_certified", "liquidity_certified", "historical_revision_status_certified",
                "calendar_completeness_certified", "historical_api_availability_proven", "price_basis_certified",
                "performance_certified", "promotion_allowed", "full_family_certified", "actual_portfolio_pnl",
                "family_adopted", "underpower_exception_applied", "historical_costs_certified", "actual_fills_certified",
                "incremental_edge_claimed", "matched_control_comparison", "new_signals_calculated"):
            if source.get(name) is not False:
                raise ObservedComparisonRefusal("source_claim_flags_changed")
        if (source.get("exploratory_only") is not True or type(source.get("source_rows_overwritten")) is not int
                or source["source_rows_overwritten"] != 0):
            raise ObservedComparisonRefusal("source_claim_flags_changed")
        if set(source["variants"]) != set(POLICIES):
            raise ObservedComparisonRefusal("source_policy_population_changed")
        original = {**source["source_provenance"], "variants": {
            policy: {"days": [{key: value for key, value in row.items() if key != "effective_label"}
                for row in source["variants"][policy]["days"]],
                "by_arm": source["variants"][policy]["source_by_arm"]} for policy in POLICIES}}
        reference, rows, _ = de._source(original)
        for name in ("source_target_matrix", "reference_sessions", "reference_weeks"):
            _same(source[name], original[name], "source_original_population_changed")
        for policy in POLICIES:
            expected = [row for row in rows[policy].values() if row["arms"][ARM]["status"] == "signal"]
            records = source["variants"][policy]["records"]
            if not isinstance(records, list) or len(records) != len(expected):
                raise ObservedComparisonRefusal("source_signal_records_changed")
            for row, record in zip(expected, records, strict=True):
                label = row["arms"][ARM]["label"]
                if ((record["session"], record["symbol"]) != (row["session"], row["symbol"])
                        or record["original_label_status"] != label["status"]):
                    raise ObservedComparisonRefusal("source_original_label_population_changed")
                if label["status"] == "known":
                    if (record["effective_label_status"] != "known" or record["recovered"] is not False
                            or de._ratio(record["label"]) != de._ratio(label["price_change"])):
                        raise ObservedComparisonRefusal("source_original_known_label_changed")
        return reference, rows
    except (KeyError, TypeError, ValueError, de.RecoveryRefusal) as exc:
        raise ObservedComparisonRefusal("malformed_source_recovery_report") from exc


def build_comparison(source: Mapping, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Select original known labels, preserve all provenance, size before means.

    Small invented grids are supported. Completion/pin authentication and actual
    257-date/56-week/5140-target/523-or520-known geometry belong to the caller.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    reference, rows = _source(source)
    weeks = list(source["reference_weeks"])
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "source_provenance": source, "reference_sessions": list(reference), "reference_weeks": weeks,
        "variants": {}, "comparison_performed": False, "comparison_not_run_reasons": [],
        "new_performance_families": 0, "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
    for policy in POLICIES:
        checkpoint()
        selected, excluded = [], []
        for row in rows[policy].values():
            arm = row["arms"][ARM]
            if arm["status"] != "signal":
                continue
            label = arm["label"]
            identity = {"session": row["session"], "symbol": row["symbol"]}
            if label["status"] == "known":
                selected.append({**identity, "label": de._ratio(label["price_change"])})
            else:
                excluded.append({**identity, "original_label": deepcopy(label),
                    "unknown_reasons": deepcopy(label["unknown_reasons"])})
        own = cc.dispersion(selected, reference)
        checkpoint()
        reasons = []
        if own["unknown_labels"]:
            reasons.append("unknown_selected_labels")
        if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
            reasons.append("undefined_selected_own_sizing")
        elif own["mde_bps"] > 33:
            reasons.append("selected_own_mde_exceeds_assumed_cost_floor")
        counts = Counter(row["arms"][ARM]["status"] for row in rows[policy].values())
        result["variants"][policy] = {
            "source_target_count": len(rows[policy]),
            "signal_counts": {state: counts[state] for state in dc.STATES},
            "source_signal_count": counts["signal"], "selected_known_label_count": len(selected),
            "excluded_unknown_label_count": len(excluded), "source_unknown_signal_count": counts["unknown"],
            "records": selected, "excluded_unknown_labels": excluded,
            "own_label_dispersion": own, "own_sizing_passed": not reasons,
            "comparison_choice_reasons": reasons, "comparison": None,
        }
        result["comparison_not_run_reasons"].extend(policy + ":" + reason for reason in reasons)
    if not result["comparison_not_run_reasons"]:
        for variant in result["variants"].values():
            checkpoint()
            records, own = variant["records"], variant["own_label_dispersion"]
            summary = de._summary(records)
            width = NormalDist().inv_cdf(.975) * own["design_se"]
            summary.update(gross_descriptive_interval={"lower": float(summary["gross_mean"]) - width,
                "upper": float(summary["gross_mean"]) + width},
                net_descriptive_interval={"lower": float(summary["net_mean"]) - width,
                    "upper": float(summary["net_mean"]) + width}, uncertainty=deepcopy(own),
                by_week={week: de._summary([record for record in records if ct._week(record["session"]) == week]) for week in weeks})
            variant["comparison"] = summary
        result["comparison_performed"] = True
        result["new_performance_families"] = 1
    result["requires_operator_choice"] = bool(result["comparison_not_run_reasons"])
    checkpoint()
    return result
