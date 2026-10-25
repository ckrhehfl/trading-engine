"""One descriptive comparison using the immutable completed DK and DR books.

The caller authenticates bytes before this pure builder receives them.  The
completed DR states are authoritative observations, not instructions to rerun
its peer predicate.  Only the two current response books are freshly sized and
compared; no parent experiment, acquisition or selection search is performed.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from fractions import Fraction
import math
from statistics import median

from research import intraday_market_context_calibration as dr
from research import intraday_breakout_close_comparison as dn


dm, dl = dr.dm, dr.dl
ROOT, WINDOW, POLICIES = dr.ROOT, dict(dr.WINDOW), dr.POLICIES
SPECIFICATION_ID = "intraday-market-context-comparison-v1"
MODE = "fixed-observed-basic-versus-positive-observed-peer-conditional-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-market-context-comparison-v1.json"
OUTPUT_NAME = "intraday-ds-market-context-comparison-20261025-v1"
DK_SHA256 = dr.ORIGINAL_DK_SHA256
DK_SPECIFICATION_SHA256 = dr.ORIGINAL_DK_SPECIFICATION_SHA256
DR_SHA256 = "a408ac6ad3199dc8415e7c7ef8a36d3d3de8f5cb7dea8967f9da327059abf2ac"
DR_SPECIFICATION_SHA256 = "d02be25b96cdb3f3e81fd1c527cf8fa4edf7f93f61ddebd6f7f71307ebf0c887"
UPSTREAM_SHA256 = {"DK": DK_SHA256, "DR": DR_SHA256}
UPSTREAM_SPECIFICATION_SHA256 = {"DK": DK_SPECIFICATION_SHA256, "DR": DR_SPECIFICATION_SHA256}
SOURCE_ROLES = {"DK": "pinned_completed_full_DK_basic_breakout_exit_policy_report",
    "DR": "pinned_completed_full_DR_fixed_observed_peer_calibration_report"}
ARMS, COST = ("basic", "selected"), Fraction(33, 10000)
EXPECTED_GEOMETRY = {**deepcopy(dr.EXPECTED_GEOMETRY),
    "observed_basic_events": {"D+1": 1166, "D+2": 1164},
    "selected_events": {"D+1": 886, "D+2": 883},
    "excluded_unknown_book_features": {"D+1": 3, "D+2": 3}}
RESOURCES = dict(dr.RESOURCES)
PARAMETERS = {
    "axis": "retain_original_first_basic_breakout_iff_immutable_DR_positive_observed_peer_state_only",
    "next_hypothesis": deepcopy(dr.NEXT_HYPOTHESIS),
    "cohort": "original_DK_common_complete_book_restricted_to_DR_selected_or_rejected_states_in_both_arms_unknown_features_retained_separately",
    "selection": "immutable_DR_target_states_selected_coordinates_source_fingerprints_clocks_and_original_labels_crossmatched_to_DK_no_peer_predicate_replay",
    "all_targets": "retain_all_original_targets_masks_labels_unknowns_and_outside_original_book",
    "operator_scope": "one_limited_exploratory_comparison_approved_unknown3_each_retained_separately_not_imputed_or_added_to_either_arm",
    "entry_delay_minutes": 3, "time_cap_minutes": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "sizing": dr.PARAMETERS["sizing"],
    "comparison_gate": "all_four_fresh_current_own_MDE_defined_finite_lte33bp_and_no_unknown_comparison_labels_no_transferred_underpower_waiver",
    "weekly_operating_objective": 15,
    "frequency": "all_original_reference_weeks_including_zero_opportunities_not_executable_account_trades",
    "performance": dn.PARAMETERS["performance"], "contrast": dn.PARAMETERS["contrast"],
    "uncertainty": dn.PARAMETERS["uncertainty"],
    "descriptive_interval": dn.PARAMETERS["descriptive_interval"],
    "output_scope": "one_conditional_descriptive_family_after_four_gates_weekly_frequency_only_no_subgroup_performance_or_promotion",
    "expected_actual_geometry": EXPECTED_GEOMETRY,
}
ASSUMPTIONS = {"source": "caller_authenticated_completed_DK_and_DR_only_no_market_IO_parent_replay_API_DB_or_archive",
    "identity": "original_DK_projection_and_all_DR_target_rows_metadata_clocks_and_coordinate_membership_joined_no_parent_statistic_replay",
    "population": "inherited_retrospective_original_book_observed_feature_scope_unknown_feature_ledger_preserved",
    "selection": "completed_DR_fixed_states_authoritative_no_recomputed_peer_returns_signs_or_new_selection",
    "availability": dr.ASSUMPTIONS["availability"], "basket": dr.ASSUMPTIONS["basket"],
    "quorum": dr.ASSUMPTIONS["quorum"],
    "planning": dn.ASSUMPTIONS["planning"], "uncertainty": dn.ASSUMPTIONS["uncertainty"],
    "measurement": dn.ASSUMPTIONS["measurement"], "policies": dn.ASSUMPTIONS["policies"],
    "claims": dn.ASSUMPTIONS["claims"]}
FLAGS = {**dr.FLAGS, "calibration_only": False, "comparison_performed": False,
    "new_performance_families": 0, "new_selection_calculated": False,
    "peer_returns_calculated": False, "peer_return_mean_or_sign_calculated": False,
    "subgroup_return_means_calculated": False}
_canonical, _digest = dr._canonical, dr._digest
# These established, source-free response/contrast primitives have the same
# arm names, fixed cost and resampling parameters. No DN pipeline is called.
_performance, _uncertainty, _interval = dn._performance, dn._uncertainty, dn._interval
_FEATURE_FIELDS = {"state", "reason", "feature_evaluated", "feature_available_at",
    "feature_availability_evidence_level", "complete_peer_windows",
    "observed_peer_mean_price_change", "source_context_event_sha256"}
_COMPACT_FIELDS = ("session", "symbol", "state", "reason", "in_original_book",
    "source_row_sha256", "source_context_event_sha256", "data_vintage", "feature_evaluated",
    "feature_available_at", "feature_availability_evidence_level", "complete_peer_windows",
    "observed_peer_mean_price_change", "basic_status", "basic_label_status",
    "signal_observable", "outcome_complete", "exclusion_reasons")


class MarketContextComparisonRefusal(RuntimeError):
    """Stable refusal codes never expose supplied private evidence."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MarketContextComparisonRefusal(reason)


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


def project_dk(dk_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Only original schema/provenance/mask/label projection; no DR selection."""
    return dr.project_dk(dk_report, checkpoint)


def _source_header(projection: Mapping, calibration: Mapping, checkpoint: Callable[[], None]) -> dict:
    _require(projection["source_identity"]["completed_DK_sha256"] == DK_SHA256
        and projection["source_identity"]["DK_specification_sha256"] == DK_SPECIFICATION_SHA256,
        "DK_projection_completed_identity_changed")
    _require(calibration["study_id"] == dr.SPECIFICATION_ID and calibration["mode"] == dr.MODE
        and calibration["specification_sha256"] == DR_SPECIFICATION_SHA256,
        "DR_identity_or_specification_changed")
    for name, expected in (("window", WINDOW), ("parameters", dr.PARAMETERS),
            ("assumptions", dr.ASSUMPTIONS), ("next_hypothesis", dr.NEXT_HYPOTHESIS),
            ("reference_sessions", projection["reference_sessions"]),
            ("reference_weeks", projection["reference_weeks"]),
            ("source_target_matrix", projection["source_target_matrix"]),
            ("upstream_calendar", projection["upstream_calendar"]),
            ("upstream_specification_sha256", dr.UPSTREAM_SPECIFICATION_SHA256)):
        _same(calibration[name], expected, "DR_scope_calendar_or_source_changed")
    for name, expected in dr.FLAGS.items():
        _require(type(calibration.get(name)) is type(expected) and calibration[name] == expected,
            "DR_claim_changed")
    _require(calibration["pin_authentication"] == "caller_obligation_not_proven_by_pure_builder"
        and set(calibration["variants"]) == set(POLICIES), "DR_not_completed_calibration")
    identity = calibration["source_identity"]
    _require(set(identity) == set(projection["source_identity"]) | {
        "completed_DQ_sha256", "DQ_specification_sha256", "DQ_header_sha256"}, "DR_lineage_schema_changed")
    for name, value in projection["source_identity"].items():
        _same(identity[name], value, "DR_DK_source_fingerprint_changed")
    _require(identity["completed_DQ_sha256"] == dr.UPSTREAM_SHA256
        and identity["DQ_specification_sha256"] == dr.UPSTREAM_SPECIFICATION_SHA256
        and isinstance(identity["DQ_header_sha256"], str)
        and dl._SHA.fullmatch(identity["DQ_header_sha256"]) is not None, "DR_completed_parent_changed")
    # Prior sizing and readiness are authenticated provenance only. Their
    # numeric results never select the new family or substitute for own sizing.
    return {"DK_report_sha256": DK_SHA256, "DK_specification_sha256": DK_SPECIFICATION_SHA256,
        "DR_report_sha256": DR_SHA256, "DR_specification_sha256": DR_SPECIFICATION_SHA256,
        "canonical_DK_sha256": projection["source_identity"]["canonical_source_sha256"],
        "canonical_DR_sha256": _digest(calibration, checkpoint),
        "DK_source_target_matrix_sha256": identity["source_target_matrix_sha256"],
        "DK_upstream_calendar_sha256": identity["upstream_calendar_sha256"],
        "DK_source_metadata_sha256": identity["source_metadata_sha256"],
        "DR_source_metadata_sha256": _digest({key: value for key, value in calibration.items()
            if key != "variants"}, checkpoint)}


def _choice(own: Mapping) -> list[str]:
    reasons = ["unknown_comparison_labels"] if own["unknown_labels"] else []
    mde = own["mde_bps"]
    if isinstance(mde, bool) or not isinstance(mde, (int, float)) or not math.isfinite(mde):
        reasons.append("own_label_mde_undefined_or_nonfinite")
    elif mde < 0 or mde > 33:
        reasons.append("own_label_mde_exceeds_assumed_cost_floor")
    return reasons


def _frequency(records: list[dict], weeks: list[str], observed_count: int, original_count: int) -> tuple[dict, dict]:
    weekly = dict.fromkeys(weeks, 0)
    for row in records:
        weekly[dl._week(row["session"])] += 1
    counts = list(weekly.values())
    return {"reference_weeks": len(weeks), "events": len(records),
        "distinct_dates": len({row["session"] for row in records}),
        "mean_per_all_reference_weeks": Fraction(len(records), len(weeks)),
        "weekly_min": min(counts), "weekly_median": median(counts), "weekly_max": max(counts),
        "weeks_at_least15": sum(value >= 15 for value in counts),
        "zero_event_weeks": sum(value == 0 for value in counts),
        "retained_opportunity_fraction": Fraction(len(records), observed_count) if observed_count else None,
        "original_book_retained_fraction": Fraction(len(records), original_count) if original_count else None,
        "opportunities_are_fills_or_account_entries": False}, weekly


def _cell() -> dict:
    return {"targets": 0, "original_book_events": 0, "state_counts": dict.fromkeys(dr.STATES, 0),
        "selected_known_labels": 0, "selected_unknown_labels": 0}


def _frozen_state(original: Mapping, receipt: Mapping) -> None:
    base = {key: value for key, value in original.items() if key != "label"}
    _require(set(receipt) == set(base) | _FEATURE_FIELDS, "DR_target_state_schema_changed")
    _same({key: receipt[key] for key in base}, base, "DR_original_target_linkage_changed")
    state, reason = receipt["state"], receipt["reason"]
    _require(state in dr.STATES, "DR_target_state_changed")
    if not original["in_original_book"]:
        expected = (("unknown", "original_signal_unknown") if original["basic_status"] == "unknown" else
            ("not_applicable", None) if original["basic_status"] == "known_no_signal" else
            ("outside_original_book", "original_common_complete_exclusion"))
        _require((state, reason) == expected and receipt["feature_evaluated"] is False
            and all(receipt[key] is None for key in _FEATURE_FIELDS - {"state", "reason", "feature_evaluated"}),
            "DR_original_mask_or_outside_book_changed")
        return
    _require(state in {"selected", "rejected", "unknown"} and receipt["feature_evaluated"] is True
        and receipt["feature_availability_evidence_level"] == "assumed"
        and receipt["feature_available_at"] == original["original_event"]["decision_at"],
        "DR_causal_feature_receipt_changed")
    decision = dl._instant(receipt["feature_available_at"])
    event, label = original["original_event"], original["original_label"]
    _require(dl._instant(event["signal_bar_end"]) + 2 * dr._MINUTE == decision
        and dl._instant(event["available_at"]) <= decision
        and dl._instant(label["entry_at"]) == decision + 3 * dr._MINUTE
        and dl._instant(label["exit_at"]) == decision + 33 * dr._MINUTE,
        "DR_feature_uses_future_observation_or_label_clock_changed")
    complete, mean = receipt["complete_peer_windows"], receipt["observed_peer_mean_price_change"]
    _require(type(complete) is int and 0 <= complete <= 19
        and isinstance(receipt["source_context_event_sha256"], str)
        and dl._SHA.fullmatch(receipt["source_context_event_sha256"]) is not None,
        "DR_feature_count_or_context_fingerprint_changed")
    if state == "unknown":
        _require(reason == "fewer_than_five_complete_peer_windows" and complete < 5 and mean is None,
            "DR_unknown_feature_receipt_changed")
    else:
        _require(reason is None and complete >= 5 and mean is not None,
            "DR_observed_feature_receipt_changed")
        dl._ratio(mean)  # Validate exact stored wire type, not its sign/predicate.


def _variant(projection: Mapping, calibration: Mapping, policy: str, checkpoint: Callable[[], None]) -> tuple[dict, dict]:
    source, frozen = projection["variants"][policy], calibration["variants"][policy]
    reference, weeks = projection["reference_sessions"], projection["reference_weeks"]
    targets, states = source["targets"], frozen["target_states"]
    _require(isinstance(states, list) and len(states) == len(targets), "DR_target_population_changed")
    records = {arm: [] for arm in ARMS}
    coordinates, selected, observed, excluded, excluded_ledger, compact_states = [], [], [], [], [], []
    totals, unknown_reasons = _cell(), Counter()
    by_session = {day: _cell() for day in reference}
    by_week = {week: _cell() for week in weeks}
    by_month = {month: _cell() for month in sorted({day[:7] for day in reference})}
    for original, receipt in zip(targets, states, strict=True):
        checkpoint()
        _frozen_state(original, receipt)
        compact = {key: deepcopy(receipt[key]) for key in _COMPACT_FIELDS}
        compact["source_target_state_sha256"] = _digest(receipt, checkpoint)
        compact_states.append(compact)
        state, label, in_book = receipt["state"], original["label"], original["in_original_book"]
        coordinate = [original["session"], original["symbol"]]
        if state == "unknown":
            unknown_reasons[receipt["reason"]] += 1
        for cell in (totals, by_session[original["session"]], by_week[dl._week(original["session"])],
                by_month[original["session"][:7]]):
            cell["targets"] += 1
            cell["original_book_events"] += in_book
            cell["state_counts"][state] += 1
            if state == "selected":
                cell["selected_known_labels"] += label is not None
                cell["selected_unknown_labels"] += label is None
        if in_book:
            coordinates.append(coordinate)
            if state == "unknown":
                excluded.append(coordinate)
                excluded_ledger.append(compact)
            else:
                observed.append(coordinate)
                records["basic"].append({"session": original["session"], "label": label})
        if state == "selected":
            selected.append(coordinate)
            records["selected"].append({"session": original["session"], "label": label})
    population = {**deepcopy(source["population"]), "original_book_events": totals["original_book_events"],
        "state_counts": totals["state_counts"], "selected_known_labels": totals["selected_known_labels"],
        "selected_unknown_labels": totals["selected_unknown_labels"]}
    for name, value in (("population", population), ("selected_coordinates", selected),
            ("unknown_reason_counts", dict(sorted(unknown_reasons.items()))),
            ("by_session", by_session), ("by_week", by_week), ("by_month", by_month),
            ("source_variant_sha256", source["source_variant_sha256"]),
            ("source_rows_sha256", source["source_rows_sha256"])):
        _same(frozen[name], value, "DR_population_membership_or_fingerprint_changed")
    _same(frozen["reconciliation"], {"all_source_rows_accounted": True, "original_book_preserved": True,
        "state_counts_match_targets": True, "weekly_monthly_match_selected": True}, "DR_reconciliation_changed")
    arms = {}
    for arm in ARMS:
        own = dm._dispersion(records[arm], reference, checkpoint)
        reasons = _choice(own)
        frequency, weekly = _frequency(records[arm], weeks, len(observed), len(coordinates))
        arms[arm] = {"events": len(records[arm]), "own_label_dispersion": own,
            "frequency": frequency, "by_week": weekly, "sizing_passed": not reasons,
            "comparison_choice_reasons": reasons}
    reasons = [arm + ":" + reason for arm in ARMS for reason in arms[arm]["comparison_choice_reasons"]]
    return {"population": population, "target_states": compact_states, "selected_coordinates": selected,
        "original_book_coordinates": coordinates, "observed_basic_coordinates": observed,
        "excluded_unknown_feature_coordinates": excluded, "excluded_unknown_feature_ledger": excluded_ledger,
        "unknown_reason_counts": dict(sorted(unknown_reasons.items())), "by_arm": arms,
        "source_variant_sha256": source["source_variant_sha256"], "source_rows_sha256": source["source_rows_sha256"],
        "source_calibration_receipts": {"DR_variant_sha256": _digest(frozen, checkpoint),
            "DR_own_label_dispersion_sha256": _digest(frozen["own_label_dispersion"], checkpoint)},
        "sizing_passed": all(arms[arm]["sizing_passed"] for arm in ARMS),
        "requires_operator_choice_before_comparison": bool(reasons), "comparison_choice_reasons": reasons,
        "comparison": None, "reconciliation": {"all_source_rows_accounted": True,
            "original_book_preserved": True, "DR_selection_preserved": True,
            "feature_unknowns_retained_separately": True,
            "observed_basic_plus_excluded_equals_original_book": len(observed) + len(excluded) == len(coordinates),
            "selected_is_subset_of_observed_basic": set(map(tuple, selected)) <= set(map(tuple, observed)),
            "weekly_counts_match_arm_events": True}}, records


def build_comparison(projection: Mapping, dr_report: Mapping, *, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Complete all four fresh own plannings before any conditional means.

    Full actual geometry and exact byte authentication belong to the reviewed
    caller; small invented grids remain supported for deterministic tests.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        identity = _source_header(projection, dr_report, checkpoint)
        variants, records = {}, {}
        for policy in POLICIES:
            variants[policy], records[policy] = _variant(projection, dr_report, policy, checkpoint)
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError,
            dr.MarketContextCalibrationRefusal, dl.BreakoutLossConcentrationRefusal) as exc:
        raise MarketContextComparisonRefusal("malformed_or_mismatched_completed_DK_DR_sources") from exc
    reasons = [policy + ":" + reason for policy in POLICIES
        for reason in variants[policy]["comparison_choice_reasons"]]
    if not reasons:
        for policy in POLICIES:
            checkpoint()
            means = {}
            for arm in ARMS:
                performance = _performance(records[policy][arm])
                variants[policy]["by_arm"][arm].update(performance)
                means[arm] = performance["gross_mean"]
            uncertainty = _uncertainty(records[policy], projection["reference_sessions"], means, checkpoint)
            difference = means["selected"] - means["basic"]
            variants[policy]["comparison"] = {"selected_minus_basic": {
                "gross_mean_difference": difference, "net_mean_difference": difference,
                "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
                "descriptive_normal95_interval": _interval(difference, uncertainty["design_se"])}}
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "source_identity": identity, "reference_sessions": list(projection["reference_sessions"]),
        "reference_weeks": list(projection["reference_weeks"]), "variants": variants,
        "comparison_performed": not reasons, "new_performance_families": int(not reasons),
        "requires_operator_choice": bool(reasons), "requires_operator_choice_before_comparison": bool(reasons),
        "comparison_not_run_reasons": reasons,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
