"""One conditional comparison of the immutable completed DK and DM books.

The caller authenticates source bytes. This pure builder crossmatches every
original target and DM feature receipt, freshly sizes all four current arms,
and only then calculates aggregate price responses. No source IO or ancestor
experiment pipeline is used; completed DM calibration is never replayed.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from fractions import Fraction
import math
import random
from statistics import NormalDist, median, stdev

from research import intraday_breakout_close_calibration as dm


dl = dm.dl
ROOT, WINDOW, POLICIES = dm.ROOT, dict(dm.WINDOW), dm.POLICIES
SPECIFICATION_ID = "intraday-breakout-close-comparison-v1"
MODE = "fixed-original-basic-versus-top-quarter-breakout-close-conditional-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-breakout-close-comparison-v1.json"
OUTPUT_NAME = "intraday-dn-breakout-close-comparison-20261024-v1"
DK_SHA256 = dm.UPSTREAM_SHA256
DK_SPECIFICATION_SHA256 = dm.UPSTREAM_SPECIFICATION_SHA256
DM_SHA256 = "3c89f8f842670b73d4e7bba87b70ea5a6b137fd7fa590ee159879cd3e28a9934"
DM_SPECIFICATION_SHA256 = "6a6b31f0d308c3edac80091a99b3940f6b1b8fa036f47ce3f81b29544fbe5525"
UPSTREAM_SHA256 = {"DK": DK_SHA256, "DM": DM_SHA256}
UPSTREAM_SPECIFICATION_SHA256 = {"DK": DK_SPECIFICATION_SHA256, "DM": DM_SPECIFICATION_SHA256}
SOURCE_ROLES = {"DK": "pinned_completed_full_DK_basic_breakout_exit_policy_report",
    "DM": "pinned_completed_full_DM_fixed_breakout_close_calibration_report"}
ARMS = ("basic", "selected")
COST = Fraction(33, 10000)
EXPECTED_GEOMETRY = {**dm.EXPECTED_GEOMETRY, "selected_events": {"D+1": 895, "D+2": 893}}
RESOURCES = dict(dm.RESOURCES)
PARAMETERS = {
    "axis": "retain_original_first_basic_breakout_iff_fixed_DM_top_quarter_close_only",
    "next_hypothesis": deepcopy(dm.NEXT_HYPOTHESIS),
    "cohort": "same_original_DK_common_outcome_complete_basic_time_only_book_no_new_outcome_exclusion",
    "selection": "immutable_DM_target_states_selected_coordinates_all_source_fingerprints_and_causal_feature_metadata_crossmatched_to_DK",
    "all_targets": "retain_all_original_targets_masks_labels_unknowns_and_outside_original_book_without_feature_evaluation",
    "entry_delay_minutes": 3, "time_cap_minutes": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "sizing": dm.PARAMETERS["sizing"],
    "comparison_gate": "all_four_fresh_current_own_MDE_defined_finite_lte33bp_no_unknown_original_book_features_or_selected_labels_no_transferred_waiver",
    "weekly_operating_objective": 15,
    "frequency": "all_original_reference_weeks_including_zero_opportunities_not_executable_account_trades",
    "performance": "overall_arm_exact_count_gross_net_sum_mean_median_positive_and_endpoint_gross_strictly_above33bp_proportions_only",
    "contrast": "selected_minus_basic_conditional_mean_gross_net_same_cost_cancels_not_causal_or_portfolio",
    "uncertainty": "CU_max_independent_arm_naive_reference_joint_whole_session_cluster_joint_circular5session2000drawseed0_bootstrap",
    "descriptive_interval": "normal95percent_no_pvalue_winner_or_contrast_power_certification",
    "output_scope": "one_conditional_descriptive_family_after_four_gates_weekly_frequency_only_no_subgroup_performance_or_promotion",
    "expected_actual_geometry": EXPECTED_GEOMETRY,
}
ASSUMPTIONS = {
    "source": "caller_authenticated_completed_DK_and_DM_only_no_market_IO_parent_replay_API_DB_or_archive",
    "identity": "all_source_rows_matrix_calendar_and_metadata_stream_hashed_no_full_parent_copy",
    "population": "inherited_retrospective_common_complete_basic_book_all_targets_and_unknowns_retained",
    "selection": "fixed_original_first_breakout_H_gt_L_4times_CminusL_ge3times_HminusL_equality_included_no_future_entry_input",
    "availability": dm.ASSUMPTIONS["availability"],
    "planning": "fresh_four_own_dispersions_before_any_new_mean_not_certified_power_for_the_contrast",
    "uncertainty": "joint_same_reference_session_blocks_preserve_nested_arm_overlap_including_zero_event_sessions",
    "measurement": "conditional_price_responses_and_assumed33bp_cost_not_fills_account_trades_or_portfolio_returns",
    "policies": "overlapping_Dplus1_Dplus2_input_availability_assumptions_not_independent_replications_or_holding_periods",
    "claims": "no_pvalue_winner_edge_causal_intervention_family_certification_direction_closure_reserved_access_or_promotion",
}
FLAGS = {**dm.FLAGS, "calibration_only": False, "comparison_performed": False,
    "new_performance_families": 0, "new_selection_calculated": False,
    "subgroup_return_means_calculated": False}
_canonical, _digest = dm._canonical, dm._digest


class BreakoutCloseComparisonRefusal(RuntimeError):
    """Stable refusal codes never expose supplied private evidence."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise BreakoutCloseComparisonRefusal(reason)


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


def _source_header(dk: Mapping, calibration: Mapping, checkpoint: Callable[[], None]) -> tuple[list[str], list[str], dict]:
    reference, weeks = dl._source_header(dk)
    _require(calibration["study_id"] == dm.SPECIFICATION_ID and calibration["mode"] == dm.MODE
        and calibration["specification_sha256"] == DM_SPECIFICATION_SHA256,
        "DM_identity_or_specification_changed")
    for name, expected in (("window", WINDOW), ("parameters", dm.PARAMETERS),
            ("assumptions", dm.ASSUMPTIONS), ("next_hypothesis", dm.NEXT_HYPOTHESIS),
            ("reference_sessions", reference), ("reference_weeks", weeks),
            ("upstream_specification_sha256", DK_SPECIFICATION_SHA256)):
        _same(calibration[name], expected, "DM_scope_or_calendar_changed")
    for name, expected in dm.FLAGS.items():
        _require(type(calibration.get(name)) is type(expected) and calibration[name] == expected,
            "DM_claim_changed")
    _require(calibration["pin_authentication"] == "caller_obligation_not_proven_by_pure_builder"
        and set(calibration["variants"]) == set(POLICIES), "DM_not_completed_calibration")
    dk_identity = {"completed_DK_sha256": DK_SHA256,
        "DK_specification_sha256": DK_SPECIFICATION_SHA256,
        "canonical_source_sha256": _digest(dk, checkpoint),
        "source_target_matrix_sha256": _digest(dk["source_target_matrix"], checkpoint),
        "upstream_calendar_sha256": _digest(dk["upstream_calendar"], checkpoint),
        "source_metadata_sha256": _digest({key: value for key, value in dk.items()
            if key not in {"variants", "source_target_matrix", "upstream_calendar"}}, checkpoint)}
    _same(calibration["source_identity"], dk_identity, "DM_DK_source_fingerprint_changed")
    identity = {"DK_report_sha256": DK_SHA256, "DK_specification_sha256": DK_SPECIFICATION_SHA256,
        "DM_report_sha256": DM_SHA256, "DM_specification_sha256": DM_SPECIFICATION_SHA256,
        "canonical_DK_sha256": dk_identity["canonical_source_sha256"],
        "canonical_DM_sha256": _digest(calibration, checkpoint),
        "DK_source_target_matrix_sha256": dk_identity["source_target_matrix_sha256"],
        "DK_upstream_calendar_sha256": dk_identity["upstream_calendar_sha256"],
        "DK_source_metadata_sha256": dk_identity["source_metadata_sha256"],
        "DM_source_metadata_sha256": _digest({key: value for key, value in calibration.items()
            if key != "variants"}, checkpoint)}
    return reference, weeks, identity


def _choice(own: Mapping) -> list[str]:
    reasons = ["selected_unknown_original_labels"] if own["unknown_labels"] else []
    mde = own["mde_bps"]
    if mde is None or isinstance(mde, bool) or not isinstance(mde, (int, float)) or not math.isfinite(mde):
        reasons.append("own_label_mde_undefined_or_nonfinite")
    elif mde < 0 or mde > 33:
        reasons.append("own_label_mde_exceeds_assumed_cost_floor")
    return reasons


def _frequency(records: list[dict], weeks: list[str], original_count: int) -> tuple[dict, dict]:
    counts = dict.fromkeys(weeks, 0)
    for row in records:
        counts[dl._week(row["session"])] += 1
    weekly = list(counts.values())
    return {"reference_weeks": len(weeks), "events": len(records),
        "distinct_dates": len({row["session"] for row in records}),
        "mean_per_all_reference_weeks": Fraction(len(records), len(weeks)),
        "weekly_min": min(weekly), "weekly_median": median(weekly), "weekly_max": max(weekly),
        "weeks_at_least15": sum(value >= 15 for value in weekly),
        "retained_opportunity_fraction": Fraction(len(records), original_count) if original_count else None,
        "opportunities_are_fills_or_account_entries": False}, counts


def _dm_frequency(records: list[dict], weeks: list[str], panels: Mapping, own: Mapping) -> dict:
    weekly = [cell["state_counts"]["selected"] for cell in panels.values()]
    upper = [cell["state_counts"]["selected"] + cell["state_counts"]["unknown"]
        + cell["state_counts"]["outside_original_book"] for cell in panels.values()]
    return {"reference_weeks": len(weeks), "selected_events": len(records),
        "selected_distinct_dates": len({row["session"] for row in records}),
        "selected_known_labels": own["known_labels"], "selected_unknown_labels": own["unknown_labels"],
        "selected_mean_per_all_reference_weeks": Fraction(len(records), len(weeks)),
        "selected_weekly_min": min(weekly), "selected_weekly_median": median(weekly),
        "selected_weekly_max": max(weekly), "weeks_at_least15_selected": sum(value >= 15 for value in weekly),
        "loose_selected_unknown_outside_book_upper_events": sum(upper),
        "loose_upper_mean_per_all_reference_weeks": Fraction(sum(upper), len(weeks)),
        "loose_upper_weeks_at_least15": sum(value >= 15 for value in upper),
        "upper_bound_is_estimate": False, "opportunities_are_fills_or_account_entries": False}


def _variant(dk: Mapping, calibration: Mapping, policy: str, reference: list[str], weeks: list[str],
        checkpoint: Callable[[], None]) -> tuple[dict, dict]:
    source, frozen = dk["variants"][policy], calibration["variants"][policy]
    _require(set(source) == {"days", "source_variant_provenance", "by_arm", "comparison"},
        "DK_variant_schema_changed")
    rows, matrix = source["days"], dk["source_target_matrix"][policy]["days"]
    _require(isinstance(rows, list) and [item["session"] for item in matrix] == reference,
        "DK_target_calendar_changed")
    expected = []
    for membership in matrix:
        targets = membership["targets"]
        _require(isinstance(targets, list) and 1 <= len(targets) <= 20
            and len({target["symbol"] for target in targets}) == len(targets), "DK_target_population_changed")
        ranks = [target.get("rank") for target in targets]
        _require(all(type(rank) is int for rank in ranks) and set(ranks) == set(range(1, len(targets) + 1)),
            "DK_target_ranks_changed")
        expected.extend((membership, target) for target in targets)
    _require(len(rows) == len(expected) == len(frozen["target_states"]), "DM_target_population_changed")
    population, cell, unknown_reasons = dl._empty_population(), dm._cell(), Counter()
    by_session = {day: dm._cell() for day in reference}
    by_week = {week: dm._cell() for week in weeks}
    by_month = {month: dm._cell() for month in sorted({day[:7] for day in reference})}
    states, selected_coordinates, original_coordinates = [], [], []
    records = {arm: [] for arm in ARMS}
    for row, (membership, target), receipt in zip(rows, expected, frozen["target_states"], strict=True):
        checkpoint()
        _require(row["session"] == membership["session"] and row["symbol"] == target["symbol"]
            and dl._SYMBOL.fullmatch(row["symbol"]) is not None, "DK_target_identity_changed")
        _same(row["membership_target"], target, "DK_membership_target_changed")
        _same(row["membership_source"], {key: value for key, value in membership.items() if key != "targets"},
            "DK_membership_metadata_changed")
        _require(isinstance(row["data_vintage"], str) and dl._SHA.fullmatch(row["data_vintage"]) is not None,
            "DK_vintage_changed")
        metadata = row["raw_chunk_metadata"]
        _require(metadata["observation_date"] == row["session"] and metadata["available_at"] is None
            and metadata["source"] == "collector klines" and metadata["is_final"] is None
            and metadata["evidence_level"] == "assumed", "DK_raw_metadata_changed")
        dl._instant(metadata["retrieved_at"])
        in_book = dl._row_state(row)
        dl._account(population, row, in_book)
        gross = dm._original_label(row, in_book)
        basic = row["arms"]["basic"]
        if basic["status"] == "unknown":
            state, reason = "unknown", "original_signal_unknown"
        elif basic["status"] == "known_no_signal":
            state, reason = "not_applicable", None
        elif not in_book:
            state, reason = "outside_original_book", "original_common_complete_exclusion"
        else:
            path = row["arms"]["retest"].get("path")
            _require(path is None or isinstance(path, Mapping), "malformed_breakout_path")
            state, reason = dm._feature(basic["event"], path.get("breakout_bar") if path is not None else None,
                row["session"])
        if state == "unknown":
            unknown_reasons[reason] += 1
        for panel in (cell, by_session[row["session"]], by_week[dl._week(row["session"])], by_month[row["session"][:7]]):
            panel["targets"] += 1
            panel["original_book_events"] += in_book
            panel["state_counts"][state] += 1
            if state == "selected":
                panel["selected_known_labels"] += gross is not None
                panel["selected_unknown_labels"] += gross is None
        item = {"session": row["session"], "symbol": row["symbol"], "state": state, "reason": reason,
            "in_original_book": in_book, "source_row_sha256": _digest(row, checkpoint),
            "data_vintage": row["data_vintage"], "raw_chunk_metadata": deepcopy(metadata),
            "source_fetched_at": deepcopy(row["source_fetched_at"]), "feature_evaluated": in_book,
            "feature_available_at": basic["event"]["decision_at"] if in_book
                and reason in {None, "degenerate_breakout_range"} else None,
            "feature_availability_evidence_level": "assumed" if in_book
                and reason in {None, "degenerate_breakout_range"} else None,
            "basic_status": basic["status"], "retest_status": row["arms"]["retest"]["status"],
            "basic_label_status": basic["label"]["status"] if basic["label"] is not None else "not_applicable",
            "signal_observable": row["signal_observable"], "outcome_complete": row["outcome_complete"],
            "exclusion_reasons": list(row["exclusion_reasons"])}
        _same(receipt, item, "DM_target_state_or_feature_linkage_changed")
        states.append(item)
        if in_book:
            original_coordinates.append([row["session"], row["symbol"]])
            records["basic"].append({"session": row["session"], "label": gross})
        if state == "selected":
            selected_coordinates.append([row["session"], row["symbol"]])
            records["selected"].append({"session": row["session"], "label": gross})
    population.update(original_book_events=cell["original_book_events"], state_counts=cell["state_counts"],
        selected_known_labels=cell["selected_known_labels"], selected_unknown_labels=cell["selected_unknown_labels"])
    for name, computed in (("population", population), ("selected_coordinates", selected_coordinates),
            ("unknown_reason_counts", dict(sorted(unknown_reasons.items()))), ("by_session", by_session),
            ("by_week", by_week), ("by_month", by_month),
            ("source_variant_sha256", _digest({key: value for key, value in source.items() if key != "days"}, checkpoint)),
            ("source_rows_sha256", _digest(rows, checkpoint))):
        _same(frozen[name], computed, "DM_population_panel_or_fingerprint_changed")
    prior = frozen["own_label_dispersion"]
    _require(prior["events"] == len(records["selected"])
        and prior["known_labels"] == cell["selected_known_labels"]
        and prior["unknown_labels"] == cell["selected_unknown_labels"]
        and prior["reference_sessions"] == len(reference)
        and prior["known_label_sessions"] == len({row["session"] for row in records["selected"] if row["label"] is not None}),
        "DM_prior_planning_population_changed")
    prior_reasons = _choice(prior)
    _require(frozen["sizing_passed"] is (not prior_reasons), "DM_prior_sizing_flag_changed")
    has_unknown = any(item["in_original_book"] and item["state"] == "unknown" for item in states)
    if has_unknown:
        prior_reasons.append("unknown_original_book_features")
    _same(frozen["comparison_choice_reasons"], prior_reasons, "DM_prior_choice_reasons_changed")
    _require(frozen["requires_operator_choice_before_comparison"] is bool(prior_reasons), "DM_prior_choice_flag_changed")
    _same(frozen["frequency"], _dm_frequency(records["selected"], weeks, by_week, prior), "DM_frequency_changed")
    _same(frozen["reconciliation"], {"all_source_rows_accounted": True, "original_book_preserved": True,
        "state_counts_match_targets": True, "weekly_monthly_match_selected": True}, "DM_reconciliation_changed")
    dl._receipts(source, len(records["basic"]), len(reference))
    _same(frozen["source_calibration_receipts"], source["by_arm"], "DM_DK_prior_receipts_changed")
    arms = {}
    for arm in ARMS:
        own = dm._dispersion(records[arm], reference, checkpoint)
        reasons = _choice(own)
        frequency, weekly = _frequency(records[arm], weeks, len(records["basic"]))
        arms[arm] = {"events": len(records[arm]), "frequency": frequency, "by_week": weekly,
            "own_label_dispersion": own, "sizing_passed": not reasons, "comparison_choice_reasons": reasons}
    reasons = [arm + ":" + reason for arm in ARMS for reason in arms[arm]["comparison_choice_reasons"]]
    if has_unknown:
        reasons.append("unknown_original_book_features")
    return {"population": population, "unknown_reason_counts": dict(sorted(unknown_reasons.items())),
        "target_states": states, "selected_coordinates": selected_coordinates,
        "original_book_coordinates": original_coordinates,
        "by_session": by_session, "by_week": by_week, "by_month": by_month,
        "frequency": {arm: arms[arm]["frequency"] for arm in ARMS}, "by_arm": arms,
        "source_variant_sha256": frozen["source_variant_sha256"], "source_rows_sha256": frozen["source_rows_sha256"],
        "source_calibration_receipts": {"DK_by_arm_sha256": _digest(source["by_arm"], checkpoint),
            "DM_own_label_dispersion": deepcopy(prior), "DM_sizing_passed": frozen["sizing_passed"],
            "DM_requires_operator_choice_before_comparison": frozen["requires_operator_choice_before_comparison"],
            "DM_comparison_choice_reasons": list(prior_reasons)},
        "sizing_passed": all(arms[arm]["sizing_passed"] for arm in ARMS),
        "requires_operator_choice_before_comparison": bool(reasons), "comparison_choice_reasons": reasons,
        "comparison": None,
        "reconciliation": {"all_source_rows_accounted": True, "original_book_preserved": True,
            "DM_selection_preserved": True, "weekly_counts_match_arm_events": True}}, records


def _performance(records: list[dict]) -> dict:
    values = [row["label"] for row in records if row["label"] is not None]
    n, gross_sum = len(values), sum(values, Fraction())
    positive, above_cost = sum(value > 0 for value in values), sum(value > COST for value in values)
    center, middle = gross_sum / n if n else None, median(values) if n else None
    return {"known_labels": n, "unknown_labels": len(records) - n,
        "gross_sum": gross_sum, "net_sum": gross_sum - n * COST,
        "gross_mean": center, "net_mean": center - COST if center is not None else None,
        "gross_median": middle, "net_median": middle - COST if middle is not None else None,
        "assumed_cost": COST, "gross_positive_count": positive,
        "gross_positive_fraction": Fraction(positive, n) if n else None,
        "net_positive_count": above_cost, "net_positive_fraction": Fraction(above_cost, n) if n else None,
        "endpoint_gross_strictly_above_cost_count": above_cost,
        "endpoint_gross_strictly_above_cost_fraction": Fraction(above_cost, n) if n else None}


def _interval(center: Fraction, se: float) -> dict:
    width = NormalDist().inv_cdf(.975) * se
    return {"lower": float(center) - width, "upper": float(center) + width}


def _uncertainty(records: Mapping, reference: list[str], means: Mapping,
        checkpoint: Callable[[], None]) -> dict:
    """CU method with shared draws for the nested selected and basic books."""
    known = {arm: [row for row in records[arm] if row["label"] is not None] for arm in ARMS}
    counts = {arm: len(known[arm]) for arm in ARMS}
    daily = {day: {arm: [Fraction(), 0] for arm in ARMS} for day in reference}
    for arm in ARMS:
        for row in known[arm]:
            daily[row["session"]][arm][0] += row["label"]
            daily[row["session"]][arm][1] += 1
    known_dates = {arm: sum(daily[day][arm][1] > 0 for day in reference) for arm in ARMS}
    _require(len(reference) >= 2 and all(counts[arm] >= 2 and known_dates[arm] >= 2 for arm in ARMS),
        "insufficient_known_labels_or_dates_for_contrast")
    groups = len(reference)
    naive = math.sqrt(math.fsum(stdev(float(row["label"]) for row in known[arm]) ** 2 / counts[arm] for arm in ARMS))
    influences = [(daily[day]["selected"][0] - daily[day]["selected"][1] * means["selected"]) / counts["selected"]
        - (daily[day]["basic"][0] - daily[day]["basic"][1] * means["basic"]) / counts["basic"] for day in reference]
    cluster = math.sqrt(groups / (groups - 1) * math.fsum(float(value) ** 2 for value in influences))
    calendar = [daily[day] for day in reference]
    blocks = {size: [{arm: (math.fsum(float(calendar[(index + offset) % groups][arm][0]) for offset in range(size)),
        sum(calendar[(index + offset) % groups][arm][1] for offset in range(size))) for arm in ARMS}
        for index in range(groups)] for size in {5, groups % 5} if size}
    randomizer, draws = random.Random(0), []
    for index in range(2000):
        if index % 64 == 0:
            checkpoint()
        sampled = [blocks[5][randomizer.randrange(groups)] for _ in range(groups // 5)]
        if groups % 5:
            sampled.append(blocks[groups % 5][randomizer.randrange(groups)])
        sampled_counts = {arm: sum(block[arm][1] for block in sampled) for arm in ARMS}
        if all(sampled_counts.values()):
            draws.append(math.fsum(block["selected"][0] for block in sampled) / sampled_counts["selected"]
                - math.fsum(block["basic"][0] for block in sampled) / sampled_counts["basic"])
    _require(len(draws) >= 2, "insufficient_valid_joint_bootstrap_draws")
    bootstrap = stdev(draws)
    design = max(naive, cluster, bootstrap)
    _require(all(math.isfinite(value) and value >= 0 for value in (naive, cluster, bootstrap, design)),
        "invalid_joint_uncertainty")
    return {"known_labels_by_arm": counts, "known_label_sessions_by_arm": known_dates,
        "overlapping_known_labels": counts["selected"], "reference_sessions": groups,
        "naive_se": naive, "session_cluster_se": cluster, "bootstrap_se": bootstrap, "design_se": design,
        "corrected_to_naive_ratio": design / naive if naive else None,
        "bootstrap_valid_draws": len(draws), "bootstrap_skipped_zero_denominator_draws": 2000 - len(draws),
        "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
        "same_blocks_for_both_arms": True,
        "naive_reference": "unpaired_independent_arm_formula_only_not_an_independence_claim",
        "own_arm_planning_certifies_contrast_power": False}


def build_comparison(dk_report: Mapping, dm_report: Mapping, *, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Finish all four fresh plannings before conditionally activating means.

    Exact actual source bytes and full geometry are enforced by the caller.
    Small invented grids remain supported for deterministic contract tests.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference, weeks, identity = _source_header(dk_report, dm_report, checkpoint)
        variants, records = {}, {}
        for policy in POLICIES:
            variants[policy], records[policy] = _variant(dk_report, dm_report, policy, reference, weeks, checkpoint)
        prior_reasons = [policy + ":" + reason for policy in POLICIES
            for reason in dm_report["variants"][policy]["comparison_choice_reasons"]]
        _same(dm_report["comparison_not_run_reasons"], prior_reasons, "DM_global_choice_reasons_changed")
        _require(dm_report["requires_operator_choice"] is bool(prior_reasons)
            and dm_report["requires_operator_choice_before_comparison"] is bool(prior_reasons),
            "DM_global_choice_flag_changed")
    except (dl.BreakoutLossConcentrationRefusal, dm.BreakoutCloseCalibrationRefusal,
            KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        raise BreakoutCloseComparisonRefusal("malformed_or_mismatched_completed_DK_DM_sources") from exc
    reasons = [policy + ":" + reason for policy in POLICIES for reason in variants[policy]["comparison_choice_reasons"]]
    if not reasons:
        for policy in POLICIES:
            checkpoint()
            means = {}
            for arm in ARMS:
                performance = _performance(records[policy][arm])
                variants[policy]["by_arm"][arm].update(performance)
                means[arm] = performance["gross_mean"]
            uncertainty = _uncertainty(records[policy], reference, means, checkpoint)
            difference = means["selected"] - means["basic"]
            variants[policy]["comparison"] = {"selected_minus_basic": {
                "gross_mean_difference": difference, "net_mean_difference": difference,
                "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
                "descriptive_normal95_interval": _interval(difference, uncertainty["design_se"])}}
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "source_identity": identity, "reference_sessions": list(reference), "reference_weeks": list(weeks),
        "variants": variants, "comparison_performed": not reasons, "new_performance_families": int(not reasons),
        "requires_operator_choice": bool(reasons), "requires_operator_choice_before_comparison": bool(reasons),
        "comparison_not_run_reasons": reasons,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
