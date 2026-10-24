"""Retrospective loss mass on the unchanged completed DK time-only events.

Only the caller-authenticated, completed DK mapping is consumed. No ancestor
module, market source, detector, feature, label, mean, sizing or resampling is
run. Every original target participates in accounting and canonical hashes;
only the already selected events are projected into the small output ledger.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SPECIFICATION_ID = "intraday-breakout-loss-concentration-v1"
MODE = "fixed-original-basic-breakout-time-only-retrospective-loss-concentration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-breakout-loss-concentration-v1.json"
OUTPUT_NAME = "intraday-dl-breakout-loss-concentration-20261024-v1"
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
POLICIES = ("D+1", "D+2")
UPSTREAM_SHA256 = "2fe92663361acfb68b2caf1e279c74bf556cb6f6994af62f7a029e33668033bb"
UPSTREAM_SPECIFICATION_SHA256 = "26b8eff9c75266670a12517a034f3922b52486d9dce86aae2cce8881474ec214"
SOURCE_STUDY_ID = "intraday-breakout-exit-policy-v1"
SOURCE_MODE = "fixed-basic-breakout-time-only-versus-close-trigger-managed-exit-conditional-comparison"
SOURCE_ROLES = {"parent": "pinned_completed_full_DK_basic_breakout_exit_policy_report"}
COST, BIG_LOSS = Fraction(33, 10000), Fraction(-1, 100)
RESOURCES = {"maximum_source_bytes": 67108864, "cpu_seconds": 300,
    "address_space_mib": 512, "wall_seconds": 1200, "nice": 10,
    "minimum_free_bytes": 1073741824, "maximum_market_gets": 0,
    "maximum_token_posts": 0, "automatic_retries": 0}
NEXT_HYPOTHESIS = {
    "id": "breakout_clv_top_quarter_v1",
    "status": "fixed_hypothesis_only_not_measured_in_DL",
    "population": "all_original_targets_and_unknowns_original_first_basic_OR15_breakout_only",
    "feature_source": "row.arms.retest.path.breakout_bar_original_first_B_completed_OHLC",
    "predicate": "H_gt_L_and_4_times_C_minus_L_ge_3_times_H_minus_L_equality_included",
    "threshold": {"numerator": "3", "denominator": "4"},
    "timing_validation": "B.start_at_equals_basic.event.signal_bar_start_B.end_at_equals_basic.event.signal_bar_end_B.available_at_equals_original_decision_D_equals_B.end_at_plus2minutes",
    "breakout_validation": "exactly_three_original_initial5minute_context_bars_B.close_strictly_above_their_max_high_crossmatch_for_every_original_basic_signal_not_only_retest_signals",
    "forbidden_selection_input": "entry_open_available_Eplus3_is_future_information_never_used_for_selection",
    "first_event_rule": "failed_original_first_B_means_rejected_no_retry_later_breakout_or_replacement",
    "missingness": "missing_B_or_OHLC_unknown_flat_H_equals_L_unknown_degenerate_malformed_or_clock_mismatch_refuses_keep_all_denominators",
    "entry": "unchanged_original_decision_D_plus3minute_OPEN_E",
    "exit": "unchanged_original_E_plus30minute_OPEN",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "future_gate": "own_missingness_frequency_and_new_own_label_sizing_before_any_future_means_no_waiver_from_DL",
    "basis_limit": "DL_concentration_does_not_measure_CLV_loss_correlation_or_predictive_effect",
    "issuer_or_date_bans": False,
}
PARAMETERS = {
    "cohort": "all_original_DK_targets_selected_iff_outcome_complete_and_basic_status_signal",
    "label": "original_DK_time_only_price_change_exact_match_basic_label_and_entry_exit_anchors",
    "entry_delay_minutes": 3, "time_cap_minutes": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "big_loss_gross_lte": {"numerator": "-1", "denominator": "100"},
    "gross_loss_mass": "max_minus_original_gross_zero",
    "net_loss_mass": "max_assumed_cost_minus_original_gross_zero",
    "tail_loss_mass": "gross_loss_mass_only_for_gross_lte_minus1percent_inclusive",
    "panels": ["sessions", "months", "quarters", "symbols"],
    "calendar": "all_original_reference_sessions_calendar_months_quarters_and_all_inherited_target_symbols_including_zero_events",
    "shares": "own_event_or_loss_or_tail_mass_divided_by_corresponding_whole_policy_total_zero_denominator_null",
    "lift": "corresponding_share_divided_by_event_share_zero_denominator_null",
    "ranking": "top1_top3_top5_descending_gross_loss_mass_net_loss_mass_and_tail_loss_mass_lexicographic_ties_exposure_alongside_loss",
    "own_sizing_gate": "nonnegative_retrospective_loss_mass_diagnostic_no_new_mean_or_MDE_no_future_waiver",
    "next_hypothesis": NEXT_HYPOTHESIS,
    "output_scope": "retrospective_decomposition_only_no_subgroup_means_return_contrast_active_subset_or_candidate_measurement",
}
ASSUMPTIONS = {
    "source": "completed_DK_authenticated_by_caller_exact_pin_no_parent_or_raw_archive_reread",
    "identity": "all_supplied_source_rows_and_metadata_stream_hashed_canonical_JSON_plus_newline_no_full_parent_copy",
    "population": "original_common_outcome_complete_basic_selection_and_all_target_unknown_denominators_retained",
    "availability": "original_Dplus2_assumed_information_entry_E_Dplus3_OPEN_available_Eplus3_cap_Eplus30_label_available_capplus3",
    "metadata": "original_observation_retrieval_unknown_finality_assumed_evidence_and_vintage_preserved_retrieval_not_historical_availability",
    "measurement": "gross_and_assumed33bp_net_event_price_changes_not_portfolio_PnL_or_certified_fills",
    "tail": "outcome_defined_gross_lte_minus1percent_hindsight_diagnostic_never_tradable_screen_or_symbol_date_exclusion",
    "power": "nonnegative_loss_mass_description_not_new_payoff_test_future_entry_or_exit_studies_require_own_gates",
    "policies": "Dplus1_Dplus2_overlap_input_availability_policies_not_holding_periods_or_independent_replications",
    "claims": "no_CLV_loss_relationship_measured_edge_qualification_direction_closure_reserved_access_or_promotion",
}
FLAGS = {name: False for name in (
    "historical_eligibility_certified", "liquidity_certified", "historical_revision_status_certified",
    "calendar_completeness_certified", "historical_api_availability_proven", "price_basis_certified",
    "performance_certified", "promotion_allowed", "full_family_certified", "family_adopted",
    "underpower_exception_applied", "historical_costs_certified", "actual_fills_certified",
    "actual_portfolio_pnl", "account_maximum_drawdown_calculated", "orders_simulated",
    "calibration_only", "new_signals_calculated", "new_labels_calculated", "raw_replay_performed",
    "upstream_statistics_recalculated", "comparison_performed", "exit_policy_compared",
    "candidate_measured", "subgroup_return_means_calculated", "resampling_performed")}
FLAGS.update(exploratory_only=True, diagnostic_only=True, retrospective_only=True,
    new_performance_families=0, source_rows_overwritten=0)
STATES = ("signal", "known_no_signal", "unknown")
LABEL_STATES = ("known", "unknown", "not_applicable")
DIMENSIONS = ("sessions", "months", "quarters", "symbols")
RANK_BASES = ("gross_loss_mass", "net_loss_mass", "tail_loss_mass")
COUNT_METRICS = ("events", "gross_positive_events", "gross_negative_events", "gross_zero_events",
    "net_negative_events", "tail_events")
MASS_METRICS = ("gross_sum", "net_sum", "gross_positive_mass", "gross_loss_mass", "net_loss_mass", "tail_loss_mass")
RAW_METRICS = COUNT_METRICS + MASS_METRICS
_MINUTE = timedelta(minutes=1)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"KRX:[0-9A-Z]{6}\Z")


class BreakoutLossConcentrationRefusal(RuntimeError):
    """Stable reason codes never include supplied private evidence."""


def _encode(value: object) -> object:
    if isinstance(value, Fraction):
        return {"numerator": str(value.numerator), "denominator": str(value.denominator)}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("unsupported_canonical_type")


def _encoder() -> json.JSONEncoder:
    return json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=_encode)


def _canonical(value: object) -> bytes:
    """Canonical publication encoding; hashes additionally append one newline."""
    return _encoder().encode(value).encode("utf-8")


def _digest(value: object, checkpoint: Callable[[], None] | None = None) -> str:
    """Hash without normalizing or serializing the complete parent in memory."""
    checkpoint = checkpoint or (lambda: None)
    digest = hashlib.sha256()
    for index, fragment in enumerate(_encoder().iterencode(value)):
        if index % 4096 == 0:
            checkpoint()
        digest.update(fragment.encode("utf-8"))
    digest.update(b"\n")
    return digest.hexdigest()


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "output_name": OUTPUT_NAME,
        "window": WINDOW, "source_roles": SOURCE_ROLES, "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "parameters": PARAMETERS, "resources": RESOURCES}
    try:
        same = _canonical(spec) == _canonical(expected)
    except (TypeError, ValueError):
        same = False
    if not same:
        raise BreakoutLossConcentrationRefusal("specification_scope_mismatch")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise BreakoutLossConcentrationRefusal(reason)


def _ratio(value: object) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Mapping):
        _require(set(value) == {"numerator", "denominator"}, "invalid_exact_ratio")
        numerator, denominator = value["numerator"], value["denominator"]
        _require(isinstance(numerator, str) and isinstance(denominator, str)
            and re.fullmatch(r"-?\d+", numerator) is not None
            and re.fullmatch(r"[1-9]\d*", denominator) is not None, "invalid_exact_ratio")
        return Fraction(int(numerator), int(denominator))
    _require(type(value) is int or isinstance(value, (str, Decimal)), "invalid_exact_number")
    return Fraction(value)


def _price(value: object) -> Fraction:
    result = _ratio(value)
    _require(result > 0, "nonpositive_source_price")
    return result


def _instant(value: object) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    _require(isinstance(result, datetime) and result.utcoffset() is not None, "invalid_source_clock")
    return result


def _day(value: object) -> str:
    _require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value,
        "invalid_source_session")
    return value


def _week(day: str) -> str:
    value = date.fromisoformat(day).isocalendar()
    return f"{value.year}-W{value.week:02d}"


def _quarter(day: str) -> str:
    return f"{day[:4]}-Q{(int(day[5:7]) - 1) // 3 + 1}"


def _same(left: object, right: object, reason: str) -> None:
    _require(_canonical(left) == _canonical(right), reason)


def _empty_population() -> dict:
    return {"targets": 0, "selected_events": 0, "unselected_targets": 0,
        "signal_observable_targets": 0, "outcome_complete_targets": 0,
        "basic_status_counts": {key: 0 for key in STATES},
        "retest_status_counts": {key: 0 for key in STATES},
        "basic_label_status_counts": {key: 0 for key in LABEL_STATES},
        "exclusion_reason_counts": {}}


def _account(population: dict, row: Mapping, selected: bool) -> None:
    population["targets"] += 1
    population["selected_events"] += selected
    population["unselected_targets"] += not selected
    population["signal_observable_targets"] += row["signal_observable"]
    population["outcome_complete_targets"] += row["outcome_complete"]
    for arm in ("basic", "retest"):
        population[arm + "_status_counts"][row["arms"][arm]["status"]] += 1
    label = row["arms"]["basic"]["label"]
    population["basic_label_status_counts"]["not_applicable" if label is None else label["status"]] += 1
    reasons = Counter(population["exclusion_reason_counts"])
    reasons.update(row["exclusion_reasons"])
    population["exclusion_reason_counts"] = dict(sorted(reasons.items()))


def _empty_metrics() -> dict:
    return {**{key: 0 for key in COUNT_METRICS}, **{key: Fraction() for key in MASS_METRICS}}


def _add(metrics: dict, event: Mapping) -> None:
    gross, net = event["gross"], event["net"]
    metrics["events"] += 1
    metrics["gross_positive_events"] += gross > 0
    metrics["gross_negative_events"] += gross < 0
    metrics["gross_zero_events"] += gross == 0
    metrics["net_negative_events"] += net < 0
    metrics["gross_sum"] += gross
    metrics["net_sum"] += net
    metrics["gross_positive_mass"] += max(gross, 0)
    for key in ("gross_loss_mass", "net_loss_mass", "tail_loss_mass"):
        metrics[key] += event[key]
    metrics["tail_events"] += event["big_loss"]


def _divide(numerator: int | Fraction, denominator: int | Fraction) -> Fraction | None:
    return Fraction(numerator) / denominator if denominator else None


def _shares(metrics: Mapping, whole: Mapping) -> dict:
    event_share = _divide(metrics["events"], whole["events"])
    result = {"event_share": event_share}
    for prefix, metric in (("gross_loss", "gross_loss_mass"), ("net_loss", "net_loss_mass"),
            ("tail_event", "tail_events"), ("tail_loss", "tail_loss_mass")):
        share = _divide(metrics[metric], whole[metric])
        result[prefix + "_share"] = share
        result[prefix + "_lift"] = _divide(share, event_share) if share is not None and event_share else None
    return result


def _source_header(source: Mapping) -> tuple[list[str], list[str]]:
    _require(source["study_id"] == SOURCE_STUDY_ID and source["mode"] == SOURCE_MODE
        and source["specification_sha256"] == UPSTREAM_SPECIFICATION_SHA256,
        "source_identity_or_specification_changed")
    _same(source["window"], WINDOW, "source_window_changed")
    parameters = source["parameters"]
    _require(parameters["entry_delay_minutes"] == 3 and parameters["time_cap_minutes"] == 30
        and _ratio(parameters["assumed_cost"]) == COST
        and parameters["cohort"] == "unchanged_DJ_inherited_DH_common_outcome_complete_basic_signals_no_new_exclusion"
        and parameters["anchors"] == "original_entry_OPEN_and_original_entry_plus30minute_OPEN_exact_raw_checks",
        "source_scope_changed")
    expected_flags = {name: False for name in (
        "historical_eligibility_certified", "liquidity_certified", "historical_revision_status_certified",
        "calendar_completeness_certified", "historical_api_availability_proven", "price_basis_certified",
        "performance_certified", "promotion_allowed", "full_family_certified", "family_adopted",
        "underpower_exception_applied", "historical_costs_certified", "actual_fills_certified",
        "actual_portfolio_pnl", "account_maximum_drawdown_calculated", "orders_simulated", "calibration_only",
        "diagnostic_only", "new_signals_calculated", "upstream_statistics_recalculated",
        "requires_operator_choice", "requires_operator_choice_before_comparison")}
    expected_flags.update(exploratory_only=True, new_labels_calculated=True,
        raw_replay_performed=True, comparison_performed=True, exit_policy_compared=True,
        new_performance_families=1, source_rows_overwritten=0)
    for key, expected in expected_flags.items():
        _require(type(source.get(key)) is type(expected) and source[key] == expected, "source_claim_changed")
    _require(source["comparison_not_run_reasons"] == []
        and source["pin_authentication"] == "caller_obligation_not_proven_by_pure_builder",
        "source_not_completed")
    _require(isinstance(source["assumptions"], Mapping) and isinstance(source["source_provenance"], Mapping),
        "source_metadata_missing")
    reference = source["reference_sessions"]
    _require(isinstance(reference, list) and bool(reference) and reference == sorted(set(reference))
        and all(WINDOW["start"] <= _day(day) <= WINDOW["end"] for day in reference), "source_calendar_changed")
    weeks = sorted({_week(day) for day in reference})
    _same(source["reference_weeks"], weeks, "source_reference_weeks_changed")
    _require(set(source["variants"]) == set(POLICIES)
        and set(source["source_target_matrix"]) == set(POLICIES), "source_policies_changed")
    calendar = [row["date"] for row in source["upstream_calendar"]["rows"]
        if WINDOW["start"] <= row["date"] <= WINDOW["end"]]
    _same(calendar, reference, "source_upstream_calendar_changed")
    return reference, weeks


def _row_state(row: Mapping) -> bool:
    _require(set(row["arms"]) == {"basic", "retest"}, "source_arm_population_changed")
    reasons = []
    for arm in ("basic", "retest"):
        finding = row["arms"][arm]
        status = finding["status"]
        _require(status in STATES, "source_signal_state_changed")
        if status == "unknown":
            reasons.append(arm + ":unknown_signal")
        if status == "signal":
            _require(isinstance(finding["event"], Mapping)
                and finding["label"]["status"] in {"known", "unknown"}, "source_signal_label_changed")
        else:
            _require(finding["event"] is None and finding["label"] is None, "source_nonsignal_has_label")
    reasons.extend(arm + ":unknown_label" for arm in ("basic", "retest")
        if row["arms"][arm]["status"] == "signal" and row["arms"][arm]["label"]["status"] != "known")
    observable = all(row["arms"][arm]["status"] != "unknown" for arm in ("basic", "retest"))
    _require(type(row["signal_observable"]) is bool and row["signal_observable"] == observable
        and type(row["outcome_complete"]) is bool and row["outcome_complete"] == (observable and not reasons),
        "source_selection_mask_changed")
    _same(row["exclusion_reasons"], reasons, "source_unknown_accounting_changed")
    return row["outcome_complete"] and row["arms"]["basic"]["status"] == "signal"


def _event(row: Mapping, checkpoint: Callable[[], None]) -> dict:
    basic, pair = row["arms"]["basic"], row["breakout_exit_policy"]
    _require(isinstance(pair, Mapping), "selected_source_label_missing")
    label, common, timed = basic["label"], pair["common"], pair["time_only"]
    decision = _instant(basic["event"]["decision_at"])
    signal_end = _instant(basic["event"]["signal_bar_end"])
    entry, exit_at = _instant(label["entry_at"]), _instant(label["exit_at"])
    _require(decision == signal_end + 2 * _MINUTE and _instant(basic["event"]["available_at"]) == decision
        and entry == decision + 3 * _MINUTE and exit_at == entry + 30 * _MINUTE,
        "source_original_signal_entry_or_cap_clock_changed")
    _require(_instant(label["time_cap_decided_at"]) == decision
        and _instant(label["entry_price_available_at"]) == entry + 3 * _MINUTE
        and _instant(label["label_available_at"]) == exit_at + 3 * _MINUTE,
        "source_original_label_availability_changed")
    _require(_instant(common["decision_at"]) == decision and _instant(common["entry_at"]) == entry
        and _instant(common["time_cap_at"]) == exit_at and _instant(common["time_cap_decided_at"]) == decision
        and _instant(common["entry_available_at"]) == entry + 3 * _MINUTE
        and _instant(timed["exit_at"]) == exit_at
        and _instant(timed["label_available_at"]) == exit_at + 3 * _MINUTE,
        "source_time_only_clock_or_availability_changed")
    entry_open, exit_open = _price(label["entry_open"]), _price(label["exit_open"])
    gross = _ratio(label["price_change"])
    _require(label["status"] == timed["status"] == "known"
        and label["actual_fill_certified"] is False and label["evidence_level"] == "assumed"
        and label["required_minute_buckets"] == 31 and label["unknown_minute_buckets"] == 0
        and label["unknown_reasons"] == [] and not any(label["unknown_bucket_reason_counts"].values())
        and timed["unknown_reason"] is None and timed["actual_fill_certified"] is False
        and timed["holding_minutes"] == 30, "selected_original_outcome_not_known")
    _require(gross == exit_open / entry_open - 1 == _ratio(timed["price_change"])
        and _price(timed["entry_open"]) == entry_open and _price(timed["exit_open"]) == exit_open
        and _price(common["anchor_open"]) == exit_open and _ratio(common["anchor_price_change"]) == gross,
        "source_time_only_price_or_label_anchor_changed")
    metadata = row["raw_chunk_metadata"]
    _require(metadata["observation_date"] == row["session"] and metadata["available_at"] is None
        and metadata["source"] == "collector klines" and metadata["is_final"] is None
        and metadata["evidence_level"] == "assumed", "source_raw_metadata_changed")
    _instant(metadata["retrieved_at"])
    _same(common["raw_chunk_metadata"], metadata, "source_time_only_metadata_changed")
    _same(common["data_vintage"], row["data_vintage"], "source_time_only_vintage_changed")
    result = {"session": row["session"], "symbol": row["symbol"],
        "source_rank": row["membership_target"].get("rank"),
        "decision_at": deepcopy(basic["event"]["decision_at"]),
        "entry_at": deepcopy(label["entry_at"]), "exit_at": deepcopy(label["exit_at"]),
        "entry_price_available_at": deepcopy(label["entry_price_available_at"]),
        "label_available_at": deepcopy(label["label_available_at"]),
        "entry_open": entry_open, "exit_open": exit_open, "gross": gross, "net": gross - COST,
        "gross_loss_mass": max(-gross, Fraction()), "net_loss_mass": max(COST - gross, Fraction()),
        "big_loss": gross <= BIG_LOSS, "tail_loss_mass": -gross if gross <= BIG_LOSS else Fraction(),
        "source_row_sha256": _digest(row, checkpoint)}
    for key in ("data_vintage", "raw_chunk_metadata", "source_fetched_at", "membership_target", "membership_source"):
        result[key] = deepcopy(row[key])
    return result


def _receipts(variant: Mapping, selected: int, sessions: int) -> dict:
    _require(set(variant["by_arm"]) == {"time_only", "managed"} and isinstance(variant["comparison"], Mapping),
        "source_completed_comparison_missing")
    for receipt in variant["by_arm"].values():
        own = receipt["own_label_dispersion"]
        mde = own["mde_bps"]
        _require(receipt["sizing_passed"] is True and receipt["comparison_choice_reasons"] == []
            and isinstance(mde, (int, float)) and not isinstance(mde, bool) and math.isfinite(mde) and 0 <= mde <= 33
            and own["known_labels"] == own["events"] == selected and own["unknown_labels"] == 0
            and own["reference_sessions"] == sessions, "source_incomplete_own_sizing")
    # These are authenticated prior receipts, never rerun or claimed as DL sizing.
    return deepcopy(variant["by_arm"])


def _rankings(panels: Mapping, whole: Mapping) -> dict:
    result = {}
    for dimension, cells in panels.items():
        result[dimension] = {}
        for basis in RANK_BASES:
            ordered = sorted(cells, key=lambda key: (-cells[key][basis], key))
            ranks = {}
            for count in (1, 3, 5):
                keys = ordered[:count]
                subtotal = {name: sum((cells[key][name] for key in keys), Fraction() if name in MASS_METRICS else 0)
                    for name in RAW_METRICS}
                ranks[f"top{count}"] = {"keys": keys, **subtotal, **_shares(subtotal, whole)}
            result[dimension][basis] = ranks
    return result


def _variant(source: Mapping, policy: str, reference: list[str], checkpoint: Callable[[], None]) -> dict:
    variant = source["variants"][policy]
    _require(set(variant) == {"days", "source_variant_provenance", "by_arm", "comparison"}, "source_variant_schema_changed")
    rows, matrix = variant["days"], source["source_target_matrix"][policy]["days"]
    _require(isinstance(rows, list) and [day["session"] for day in matrix] == reference,
        "source_target_calendar_changed")
    expected = []
    for membership in matrix:
        targets = membership["targets"]
        _require(isinstance(targets, list) and 1 <= len(targets) <= 20
            and len({target["symbol"] for target in targets}) == len(targets), "source_target_population_changed")
        ranks = [target.get("rank") for target in targets]
        _require(all(type(rank) is int for rank in ranks) and set(ranks) == set(range(1, len(targets) + 1)),
            "source_target_ranks_changed")
        expected.extend((membership, target) for target in targets)
    _require(len(rows) == len(expected), "source_target_population_changed")
    symbols = sorted({target["symbol"] for _, target in expected})
    keys = {"sessions": reference, "months": sorted({day[:7] for day in reference}),
        "quarters": sorted({_quarter(day) for day in reference}), "symbols": symbols}
    panels = {dimension: {key: {**_empty_metrics(), "target_accounting": _empty_population()}
        for key in values} for dimension, values in keys.items()}
    population, whole, ledger = _empty_population(), _empty_metrics(), []
    for row, (membership, target) in zip(rows, expected, strict=True):
        checkpoint()
        _require(row["session"] == membership["session"] and row["symbol"] == target["symbol"]
            and _SYMBOL.fullmatch(row["symbol"]) is not None, "source_target_identity_changed")
        _same(row["membership_target"], target, "source_membership_target_changed")
        _same(row["membership_source"], {key: value for key, value in membership.items() if key != "targets"},
            "source_membership_metadata_changed")
        _require(isinstance(row["data_vintage"], str) and _SHA.fullmatch(row["data_vintage"]) is not None,
            "source_vintage_changed")
        selected = _row_state(row)
        groups = {"sessions": row["session"], "months": row["session"][:7],
            "quarters": _quarter(row["session"]), "symbols": row["symbol"]}
        _account(population, row, selected)
        for dimension, key in groups.items():
            _account(panels[dimension][key]["target_accounting"], row, selected)
        if not selected:
            _require(row["breakout_exit_policy"] is None, "unselected_source_has_exit_policy")
            continue
        event = _event(row, checkpoint)
        ledger.append(event)
        _add(whole, event)
        for dimension, key in groups.items():
            _add(panels[dimension][key], event)
    receipts = _receipts(variant, len(ledger), len(reference))
    for cells in panels.values():
        for cell in cells.values():
            cell.update(_shares(cell, whole))
    reconciliation = {}
    for dimension, cells in panels.items():
        _require(all(sum(cell[name] for cell in cells.values()) == whole[name] for name in RAW_METRICS),
            "loss_subtotals_do_not_reconcile")
        _require(sum(cell["target_accounting"]["targets"] for cell in cells.values()) == population["targets"],
            "target_subtotals_do_not_reconcile")
        reconciliation[dimension] = True
    return {"population": population,
        "source_variant_sha256": _digest({key: value for key, value in variant.items() if key != "days"}, checkpoint),
        "source_rows_sha256": _digest(rows, checkpoint), "source_calibration_receipts": receipts,
        "selected_events": ledger, "whole": {**whole, **_shares(whole, whole)}, "panels": panels,
        "rankings": _rankings(panels, whole),
        "reconciliation": {"all_source_rows_accounted": True, "all_selected_events_retained": True,
            "panels_match_whole": reconciliation}}


def build_diagnostics(dk_report: Mapping, *, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Describe existing losses without new source access or candidate evaluation.

    Smaller invented grids are supported. Exact completed source bytes and the
    actual 257-session/56-week/5140-target/1169-or-1167-event geometry are caller
    obligations, independent of this structural validation.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference, weeks = _source_header(dk_report)
        variants = {policy: _variant(dk_report, policy, reference, checkpoint) for policy in POLICIES}
        identity = {"completed_DK_sha256": UPSTREAM_SHA256,
            "DK_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
            "canonical_source_sha256": _digest(dk_report, checkpoint),
            "source_target_matrix_sha256": _digest(dk_report["source_target_matrix"], checkpoint),
            "upstream_calendar_sha256": _digest(dk_report["upstream_calendar"], checkpoint),
            "source_metadata_sha256": _digest({key: value for key, value in dk_report.items()
                if key not in {"variants", "source_target_matrix", "upstream_calendar"}}, checkpoint)}
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        raise BreakoutLossConcentrationRefusal("malformed_or_mismatched_completed_DK_source") from exc
    basis = {policy: {"events": variant["whole"]["events"],
        **{name: variant["whole"][name] for name in ("gross_loss_mass", "net_loss_mass", "tail_events", "tail_loss_mass")},
        "concentration": deepcopy(variant["rankings"])} for policy, variant in variants.items()}
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "source_identity": identity, "reference_sessions": list(reference), "reference_weeks": weeks,
        "variants": variants, "next_hypothesis": {**deepcopy(NEXT_HYPOTHESIS),
            "basis": {"scope": "retrospective_loss_concentration_only_not_measured_CLV_relationship", "by_policy": basis}},
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
