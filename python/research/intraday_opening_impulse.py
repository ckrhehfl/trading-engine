"""Calibrate one causal opening-impulse predicate on original CT signals.

The supplied CT report is retained in full. Only the initial range's signed
open-to-close move selects an original first signal; its clock and label never
change. This pure module performs no IO, comparison or actual-data execution.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from fractions import Fraction

from research import intraday_opening_range_comparison as cu


ct = cu.ct
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-opening-impulse-v1"
MODE = "opening-impulse-own-label-frequency-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-impulse-v1.json"
WINDOW = dict(ct.WINDOW)
ARMS = ct.ARMS
UPSTREAM_SHA256 = cu.UPSTREAM_SHA256
UPSTREAM_SPECIFICATION_SHA256 = cu.UPSTREAM_SPECIFICATION_SHA256
PARAMETERS = {
    "source_parameters": deepcopy(ct.PARAMETERS),
    "predicate": "initial_range_last_close_over_first_open_minus_one_greater_or_equal_threshold",
    "opening_impulse_threshold": {"numerator": "33", "denominator": "10000"},
    "cohort": "each_original_CT_first_signal_independently_no_later_retry_or_complete_label_mask",
    "arms": ["15", "30"],
    "label": "unchanged_original_CT_entry_plus30minute_cap_open",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "cost_floor_bps": "33", "weekly_operating_objective": 15,
    "output_scope": "selection_frequency_missingness_own_label_dispersion_only_no_performance_comparison",
    "underpower_exception_applied": False,
}
REGISTERED_PATHS = {
    **cu.REGISTERED_PATHS,
    "output_directory": "/home/minjun4897/research-evidence/intraday-cv-opening-impulse-20261023-v1",
}
RESOURCES = dict(cu.RESOURCES)
FLAGS = dict(ct.FLAGS)
ASSUMPTIONS = {
    "population": "all_original_CT_targets_and_both_membership_policies_retained_no_substitution_or_outcome_complete_selection",
    "predicate": "signed_initial_range_open_to_last_close_at_least_assumed33bp_cost_scale_fixed_before_outcomes_not_fitted_or_a_volume_measure",
    "timing": "range_context_complete_and_available_no_later_than_original_decision_original_first_signal_entry_and_cap_unchanged_no_retry",
    "metadata": "feature_available_at_is_last_initial_range_bar_availability_other_metadata_retained_from_original_event_retrieval_is_not_historical_availability",
    "states": "selected_or_rejected_only_original_signals_unknown_original_signal_stays_unknown_known_no_signal_is_not_applicable",
    "missingness": "all_original_labels_preserved_selected_unknown_labels_counted_unknown_no_zero_fill_or_later_replacement",
    "power": "selected_original_price_changes_cc_dispersion_all_reference_dates_including_zero_dates_naive_session_cluster_circular5session2000draw_seed0_maxSE",
    "cost": "unchanged33bp_assumed_planning_floor_only_no_return_mean_net_return_win_rate_or_contrast",
    "frequency": "selected_opportunities_not_executable_entries_or_independent_observations15perweek_operating_fit_never_forced_trades",
    "policies": "D+1_D+2_membership_availability_sensitivity_not_holding_periods_or_independent_replications",
    "checkpoint": "new_selected_unknown_labels_undefined_or_over33bp_own_MDE_require_operator_choice_before_separately_registered_comparison_no_old_waiver",
    "source": "complete_CT_provenance_targets_unknowns_flags_own_sizing_and_execution_metadata_retained_in_upstream_provenance",
    "claims": "new_persistence_assumption_previous_searches_remain_spent_no_winner_edge_family_certificate_direction_closure_reserved_access_or_paper_live_promotion",
}
STATES = ("selected", "rejected", "unknown", "not_applicable")
_canonical = ct._canonical
_THRESHOLD = Fraction(33, 10000)
_METADATA = ("observation_date", "available_at", "retrieved_at", "source", "is_final",
    "data_vintage", "evidence_level")


class OpeningImpulseRefusal(RuntimeError):
    """Stable refusal reasons never include supplied source values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise OpeningImpulseRefusal("specification_scope_mismatch")


def _feature(row: Mapping, arm: str) -> dict:
    finding = row["arms"][arm]
    original = finding["status"]
    event = finding["event"]
    metadata = ({**row["raw_chunk_metadata"], "data_vintage": row["data_vintage"]}
        if event is None else event)
    result = {name: deepcopy(metadata[name]) for name in _METADATA}
    result.update(status="unknown" if original == "unknown" else "not_applicable",
        opening_impulse=None, original_signal_status=original,
        label=deepcopy(finding["label"]))
    if original != "signal":
        return result
    context = event["context"]
    decision = cu._instant(event["decision_at"])
    if any(cu._instant(bar["available_at"]) > decision for bar in context):
        raise OpeningImpulseRefusal("noncausal_opening_context")
    impulse = cu._positive_price(context[-1]["close"]) / cu._positive_price(context[0]["open"]) - 1
    result.update(status="selected" if impulse >= _THRESHOLD else "rejected",
        opening_impulse=impulse, available_at=deepcopy(context[-1]["available_at"]))
    return result


def _counts(rows: list[dict], arm: str) -> dict:
    features = [row["arms"][arm] for row in rows]
    selection = Counter(item["status"] for item in features)
    labels = Counter(item["label"]["status"] for item in features if item["status"] == "selected")
    return {"selection_counts": {state: selection[state] for state in STATES},
        "label_counts": {state: labels[state] for state in ("known", "unknown")}}


def build_calibration(source: Mapping, *, checkpoint: Callable[[], None] | None = None) -> dict:
    """Validate all supplied CT targets before calibrating this fixed subset.

    Small invented date grids are supported. The actual caller authenticates
    saved bytes and enforces the registered full geometry, logging and budget.
    Upstream choices remain provenance; only the new selected labels and their
    own sizing determine the new comparison checkpoint.
    """
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        cu._validate_source(source, checkpoint)
    except (KeyError, TypeError, ValueError, cu.OpeningRangeComparisonRefusal,
            ct.OpeningRangeRefusal, ct.cc.CalibrationRefusal) as exc:
        raise OpeningImpulseRefusal("malformed_source_calibration") from exc
    reference = list(source["reference_sessions"])
    weeks = list(source["reference_weeks"])
    months = sorted({day[:7] for day in reference})
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "upstream_provenance": deepcopy(source), "reference_sessions": reference,
        "reference_weeks": weeks, "variants": {}}
    choices = []
    for policy, variant in source["variants"].items():
        days = []
        for row in variant["days"]:
            checkpoint()
            days.append({"session": row["session"], "symbol": row["symbol"],
                "arms": {arm: _feature(row, arm) for arm in ARMS}})
        by_arm = {}
        for arm in ARMS:
            checkpoint()
            records = [{"session": row["session"], "label": None if row["arms"][arm]["label"]["status"] == "unknown"
                else cu._ratio(row["arms"][arm]["label"]["price_change"])}
                for row in days if row["arms"][arm]["status"] == "selected"]
            own = ct.cc.dispersion(records, reference)
            reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
            if own["mde_bps"] is None:
                reasons.append("undefined_own_label_sizing")
            elif own["mde_bps"] > 33:
                reasons.append("own_label_mde_exceeds_assumed_cost_floor")
            weekly = {week: _counts([row for row in days if ct._week(row["session"]) == week], arm)
                for week in weeks}
            by_arm[arm] = {**_counts(days, arm), "by_week": weekly,
                "by_month": {month: _counts([row for row in days if row["session"][:7] == month], arm)
                    for month in months},
                "frequency": {"reference_sessions": len(reference), "reference_weeks": len(weeks),
                    "selected_per_reference_week": Fraction(len(records), len(weeks)),
                    "known_labels_per_reference_week": Fraction(own["known_labels"], len(weeks)),
                    "weekly_operating_objective": 15,
                    "weeks_with_at_least15_selected": sum(item["selection_counts"]["selected"] >= 15 for item in weekly.values()),
                    "forced_trades": False, "executable_frequency_certified": False},
                "own_label_dispersion": own,
                "requires_operator_choice_before_comparison": bool(reasons),
                "comparison_choice_reasons": reasons}
            choices.append(bool(reasons))
        result["variants"][policy] = {"days": days, "by_arm": by_arm}
    result["requires_operator_choice_before_comparison"] = any(choices)
    checkpoint()
    return result
