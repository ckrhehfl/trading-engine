"""Fixed close-triggered exits on the unchanged completed DJ basic population.

The caller authenticates the completed parent and saved raw archive. This pure
builder never replays DJ statistics. Four new own-label sizing cells precede
every new aggregate performance mean and the one conditional comparison family.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from fractions import Fraction
import hashlib
import math

from research import intraday_breakout_path_diagnostics as dj


di, ct, cu, cc = dj.di, dj.ct, dj.cu, dj.cc
ROOT = dj.ROOT
SPECIFICATION_ID = "intraday-breakout-exit-policy-v1"
MODE = "fixed-basic-breakout-time-only-versus-close-trigger-managed-exit-conditional-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-breakout-exit-policy-v1.json"
OUTPUT_NAME = "intraday-dk-breakout-exit-policy-20261024-v1"
WINDOW, POLICIES = dict(dj.WINDOW), dj.POLICIES
ARMS = ("time_only", "managed")
COST, TAKE_PROFIT, STOP_LOSS = Fraction(33, 10000), Fraction(1, 100), Fraction(-1, 200)
UPSTREAM_SHA256 = "e68e63fffd0338e99c5f360364128a77f1dfb7405df52875beeec191349af89f"
UPSTREAM_SPECIFICATION_SHA256 = "aada6bd387de85185d34811e01e70319c0ece81c105376619f9622d9abfc2913"
INPUT_COMPRESSED_SHA256, INPUT_STREAM_SHA256 = ct.INPUT_COMPRESSED_SHA256, ct.INPUT_STREAM_SHA256
DJ_PARENT_SHA256 = "2ca745f6cf872f0ca44d12a9d78637322f537fc5ab03c0f6a7093c02e74c5255"
DJ_AUTHENTICATION = "caller_authenticated_completed_DI_parent_and_original_CC_archive_pins"
SOURCE_ROLES = {"parent": "pinned_completed_full_DJ_basic_breakout_path_report",
    "archive": "pinned_original_CC_saved_raw_minute_archive"}
RESOURCES = dict(dj.RESOURCES)
PARAMETERS = {
    "cohort": "unchanged_DJ_inherited_DH_common_outcome_complete_basic_signals_no_new_exclusion",
    "entry_delay_minutes": 3, "time_cap_minutes": 30,
    "take_profit_gross_return": {"numerator": "1", "denominator": "100"},
    "stop_loss_gross_return": {"numerator": "-1", "denominator": "200"},
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "trigger": "first_completed_minute_CLOSE_T_gt_entry_ge_TP_or_le_SL_T_through_cap_minus4_inclusive",
    "managed_fill": "availability_Tplus2_decision_then_actual_minute_OPEN_Tplus3_strictly_before_cap",
    "pending": "first_eligible_trigger_wins_later_reversal_ignored_no_threshold_or_high_low_fill",
    "fallback": "no_eligible_trigger_or_fill_tied_cap_uses_original_cap_minute_OPEN",
    "anchors": "original_entry_OPEN_and_original_entry_plus30minute_OPEN_exact_raw_checks",
    "missingness": "all31_original_entry_through_cap_buckets_required_any_gap_refuses_no_drop_no_zero",
    "touch_diagnostic": "inclusive_TP_SL_high_low_touches_hindsight_only_same_minute_order_unknown",
    "own_sizing_gate": "all_four_time_only_managed_own_MDE_defined_finite_at_most33bp_before_any_new_mean",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "paired": "same_original_event_managed_minus_time_only_max_naive_session_cluster_circular5session_bootstrapSE",
    "weekly_operating_objective": 15, "underpower_exception_applied": False,
    "output_scope": "fresh_calibration_then_one_conditional_exact_gross_net_paired_reason_fill_duration_comparison",
}
ASSUMPTIONS = {
    "source": "completed_DJ_authenticated_by_caller_pin_no_DJ_statistic_or_detector_replay",
    "population": "all_DJ_rows_unknowns_masks_features_diagnostics_and_provenance_retained_no_new_outcome_exclusion",
    "clocks": "original_decision_D_entry_E_Dplus3_cap_X_Eplus30_preplanned_D_close_T_available_Tplus2_fill_Tplus3_lt_X",
    "availability": "entry_open_available_Eplus3_outcome_exitplus3_assumed_future_labels_not_historical_publication_proof",
    "execution": "actual_observed_minute_OPEN_price_proxies_no_order_simulation_or_actual_fill_certification",
    "fill_feasibility": "saved_OHLC_has_no_volume_depth_bid_ask_queue_halt_or_partial_fill_evidence_open_plus_lag_is_only_proxy",
    "touches": "high_low_TP_SL_envelopes_have_unknown_intraminute_order_never_trigger_policy_orders",
    "missingness": "any_selected31bucket_missing_invalid_conflict_or_anchor_contradiction_refuses_never_drop_or_zero_fill",
    "power": "four_new_own_dispersions_full_reference_calendar_before_means_no_DJ_extremum_or_old_underpower_waiver",
    "paired": "same_event_cost_cancels_whole_session_circular5session_uncertainty_descriptive_normal95_not_confirmation",
    "frequency": "original_full_calendar_zero_event_dates_weeks_weekly15_operating_fit_not_executable_account_trades",
    "policies": "Dplus1_Dplus2_overlap_input_availability_assumptions_not_holding_days_or_replication",
    "cost": "unchanged_assumed33bp_not_historical_cost_or_total_return_certificate",
    "claims": "no_account_MDD_portfolio_PnL_optimized_winner_edge_qualification_direction_closure_reserved_access_or_promotion",
}
FLAGS = {**ct.FLAGS, "calibration_only": False, "diagnostic_only": False,
    "new_signals_calculated": False, "new_labels_calculated": True,
    "raw_replay_performed": True, "upstream_statistics_recalculated": False,
    "comparison_performed": False, "exit_policy_compared": False,
    "new_performance_families": 0, "source_rows_overwritten": 0,
    "actual_fills_certified": False, "orders_simulated": False,
    "actual_portfolio_pnl": False, "account_maximum_drawdown_calculated": False}
PROVENANCE_EXCLUDE = dj.PROVENANCE_EXCLUDE
REASONS = ("take_profit", "stop_loss", "time_cap")
_canonical = dj._canonical


class BreakoutExitPolicyRefusal(RuntimeError):
    """Stable refusal reason codes contain no supplied private evidence."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "output_name": OUTPUT_NAME,
        "window": WINDOW, "source_roles": SOURCE_ROLES, "parameters": PARAMETERS,
        "resources": RESOURCES, "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise BreakoutExitPolicyRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise BreakoutExitPolicyRefusal(reason)


def _source(source: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    """Validate pinned DJ identity/schema and its inherited source projection.

    Parent diagnostic aggregates, extrema and upstream statistics are retained,
    not recomputed. Authentication of the exact complete parent is caller-owned.
    """
    if (source["study_id"] != dj.SPECIFICATION_ID or source["mode"] != dj.MODE
            or source["specification_sha256"] != UPSTREAM_SPECIFICATION_SHA256
            or source["upstream_specification_sha256"] != dj.UPSTREAM_SPECIFICATION_SHA256):
        raise BreakoutExitPolicyRefusal("source_identity_or_specification_changed")
    for name, expected in (("window", dj.WINDOW), ("parameters", dj.PARAMETERS),
            ("assumptions", dj.ASSUMPTIONS)):
        _same(source[name], expected, "source_scope_changed")
    for name, expected in dj.FLAGS.items():
        if type(source.get(name)) is not type(expected) or source[name] != expected:
            raise BreakoutExitPolicyRefusal("source_claim_changed")
    if source["pin_authentication"] != "caller_obligation_not_proven_by_pure_builder":
        raise BreakoutExitPolicyRefusal("source_pin_claim_changed")
    projection = {**source["source_provenance"],
        "source_target_matrix": source["source_target_matrix"], "upstream_calendar": source["upstream_calendar"],
        "variants": {policy: {**variant["source_variant_provenance"],
            "days": [{key: value for key, value in row.items() if key != "breakout_path_diagnostic"}
                for row in variant["days"]]} for policy, variant in source["variants"].items()}}
    reference = dj._source(projection, checkpoint)
    _same(source["reference_sessions"], reference, "source_reference_sessions_changed")
    _same(source["reference_weeks"], projection["reference_weeks"], "source_reference_weeks_changed")
    keys, total_rows = {}, {}
    for policy in POLICIES:
        variant = source["variants"][policy]
        if set(variant) != {"days", "source_variant_provenance", "summary"}:
            raise BreakoutExitPolicyRefusal("source_variant_schema_changed")
        if set(variant["summary"]) != {"overall", "by_week"} or set(variant["summary"]["by_week"]) != set(source["reference_weeks"]):
            raise BreakoutExitPolicyRefusal("source_diagnostic_summary_schema_changed")
        eligible = 0
        for row in variant["days"]:
            checkpoint()
            key = row["session"], row["symbol"]
            keys[key] = True
            total_rows[key] = row["input_rows"]
            diagnostic = row["breakout_path_diagnostic"]
            if not dj._eligible(row):
                if diagnostic is not None:
                    raise BreakoutExitPolicyRefusal("source_nonsignal_has_diagnostic")
                continue
            eligible += 1
            label = row["arms"]["basic"]["label"]
            entry, cap = (cu._instant(label[name]) for name in ("entry_at", "exit_at"))
            if (not isinstance(diagnostic, Mapping) or diagnostic["status"] not in {"complete", "path_unknown"}
                    or diagnostic["category"] not in dj.CATEGORIES
                    or type(diagnostic["path_known"]) is not bool
                    or diagnostic["path_known"] != (diagnostic["status"] == "complete")
                    or cu._instant(diagnostic["entry_at"]) != entry or cu._instant(diagnostic["end_at"]) != cap
                    or cu._instant(diagnostic["path_available_at"]) != cap + 2 * cc.MINUTE
                    or cu._ratio(diagnostic["entry_open"]) != cu._positive_price(label["entry_open"])
                    or cu._ratio(diagnostic["endpoint_open"]) != cu._positive_price(label["exit_open"])
                    or cu._ratio(diagnostic["endpoint_price_change"]) != cu._ratio(label["price_change"])):
                raise BreakoutExitPolicyRefusal("source_diagnostic_schema_or_anchor_changed")
            _same(diagnostic["raw_chunk_metadata"], row["raw_chunk_metadata"], "source_diagnostic_metadata_changed")
            _same(diagnostic["data_vintage"], row["data_vintage"], "source_diagnostic_vintage_changed")
            missing = cu._integer(diagnostic["missing_minute_buckets"], 30)
            gaps = diagnostic["unknown_bucket_reason_counts"]
            if (set(gaps) != set(ct.GAP_STATES) or sum(cu._integer(value, 30) for value in gaps.values()) != missing
                    or diagnostic["path_known"] != (missing == 0)):
                raise BreakoutExitPolicyRefusal("source_diagnostic_missingness_changed")
        if variant["summary"]["overall"]["total_cases"] != eligible:
            raise BreakoutExitPolicyRefusal("source_diagnostic_population_changed")
    _same(source["input_evidence"], {"unique_target_days": len(keys), "queried_rows": sum(total_rows.values()),
        "source_sha256": {"parent": DJ_PARENT_SHA256, "archive": INPUT_COMPRESSED_SHA256},
        "source_roles": dj.SOURCE_ROLES, "authentication": DJ_AUTHENTICATION},
        "source_input_geometry_changed")
    return reference


def _label_pair(row: Mapping, minutes: Mapping) -> dict:
    """Exact CLOSED-minute trigger, delayed next-OPEN fill and original anchors."""
    label, event = row["arms"]["basic"]["label"], row["arms"]["basic"]["event"]
    entry, cap, decision = (cu._instant(value) for value in (label["entry_at"], label["exit_at"], event["decision_at"]))
    if entry != decision + 3 * cc.MINUTE or cap != entry + 30 * cc.MINUTE:
        raise BreakoutExitPolicyRefusal("source_entry_or_cap_clock_changed")
    path = [minutes.get(entry + offset * cc.MINUTE) for offset in range(31)]
    if any(prices is None for prices in path):
        raise BreakoutExitPolicyRefusal("selected_original31minute_path_incomplete")
    if row["breakout_path_diagnostic"]["path_known"] is not True:
        raise BreakoutExitPolicyRefusal("source_original_path_completeness_contradiction")
    entry_open, cap_open = Fraction(path[0][0]), Fraction(path[30][0])
    anchor = cu._ratio(label["price_change"])
    if (entry_open != cu._positive_price(label["entry_open"]) or cap_open != cu._positive_price(label["exit_open"])
            or cap_open / entry_open - 1 != anchor):
        raise BreakoutExitPolicyRefusal("source_entry_or_endpoint_raw_anchor_mismatch")
    entry_available = entry + 3 * cc.MINUTE

    def outcome(exit_at):
        offset = int((exit_at - entry) / cc.MINUTE)
        price = Fraction(path[offset][0])
        return {"status": "known", "entry_open": entry_open, "exit_open": price,
            "exit_at": exit_at, "price_change": price / entry_open - 1,
            "label_available_at": exit_at + 3 * cc.MINUTE, "unknown_reason": None,
            "actual_fill_certified": False, "holding_minutes": offset}

    managed = {**outcome(cap), "reason": "time_cap", "trigger_trace": None,
        "fill_minus_trigger_price_change": None, "fill_minus_trigger_threshold": None}
    for offset in range(1, 27):
        close_at = entry + offset * cc.MINUTE
        close = Fraction(path[offset - 1][3])
        change = close / entry_open - 1
        reason = "take_profit" if change >= TAKE_PROFIT else "stop_loss" if change <= STOP_LOSS else None
        if reason is None:
            continue
        available, fill = close_at + 2 * cc.MINUTE, close_at + 3 * cc.MINUTE
        if not entry_available <= available < fill < cap:
            raise BreakoutExitPolicyRefusal("invalid_policy_information_or_execution_boundary")
        managed = {**outcome(fill), "reason": reason, "trigger_trace": {
            "bar_start": close_at - cc.MINUTE, "close_at": close_at, "close": close,
            "price_change": change, "available_at": available, "decision_at": available,
            "scheduled_fill_at": fill}}
        managed["fill_minus_trigger_price_change"] = managed["price_change"] - change
        managed["fill_minus_trigger_threshold"] = managed["price_change"] - (TAKE_PROFIT if reason == "take_profit" else STOP_LOSS)
        break
    up = [offset for offset, prices in enumerate(path[:30]) if Fraction(prices[1]) / entry_open - 1 >= TAKE_PROFIT]
    down = [offset for offset, prices in enumerate(path[:30]) if Fraction(prices[2]) / entry_open - 1 <= STOP_LOSS]
    both = sorted(set(up) & set(down))
    first_up, first_down = up[0] if up else None, down[0] if down else None
    order = ("neither" if first_up is None and first_down is None else "down_only" if first_up is None
        else "up_only" if first_down is None else "same_minute_order_unknown" if first_up == first_down
        else "up_before_down" if first_up < first_down else "down_before_up")
    return {"common": {"decision_at": decision, "entry_at": entry, "entry_available_at": entry_available,
        "time_cap_at": cap, "time_cap_decided_at": decision, "anchor_open": cap_open,
        "anchor_price_change": anchor, "data_vintage": row["data_vintage"],
        "raw_chunk_metadata": deepcopy(row["raw_chunk_metadata"])},
        "time_only": outcome(cap), "managed": managed,
        "touch_diagnostic": {"scope": "original30minute_window_not_actual_managed_holding_or_ambiguous_execution_count",
            "first_TP_touch_offset_minutes": first_up, "first_SL_touch_offset_minutes": first_down,
            "touch_order": order, "both_touched_same_minute_offsets": both,
            "intraminute_order_known": False, "touches_used_as_fills": False}}


def _describe(values: list) -> dict:
    values = sorted(Fraction(value) for value in values if value is not None)
    n = len(values)
    return {"count": n, "sum": sum(values, Fraction()), "mean": sum(values, Fraction()) / n if n else None,
        "median": (values[(n - 1) // 2] + values[n // 2]) / 2 if n else None,
        "min": values[0] if n else None, "max": values[-1] if n else None}


def _comparison(rows: list[dict], records: Mapping, by_arm: Mapping, reference: list[str],
        weeks: list[str], checkpoint: Callable[[], None]) -> dict:
    summaries = {}
    for arm in ARMS:
        summary = di.de._summary(records[arm])
        se = by_arm[arm]["own_label_dispersion"]["design_se"]
        summary.update(gross_descriptive_interval=cu._interval(summary["gross_mean"], se),
            net_descriptive_interval=cu._interval(summary["net_mean"], se),
            by_week={week: di.de._summary([record for record in records[arm] if ct._week(record["session"]) == week]) for week in weeks})
        summaries[arm] = summary
    pairs = [row["breakout_exit_policy"] for row in rows]
    delta = [pair["managed"]["price_change"] - pair["time_only"]["price_change"] for pair in pairs]
    uncertainty = cc.dispersion([{"session": row["session"], "label": value}
        for row, value in zip(rows, delta, strict=True)], reference)
    checkpoint()
    mean = sum(delta, Fraction()) / len(delta)
    if uncertainty["design_se"] is None:
        raise BreakoutExitPolicyRefusal("paired_uncertainty_unavailable")
    managed = [pair["managed"] for pair in pairs]
    return {"by_arm": summaries,
        "paired": {"events": len(delta), "sum_change": sum(delta, Fraction()), "mean_change": mean,
            "net_mean_change": mean, "fixed_cost_cancels": True,
            "improved_events": sum(value > 0 for value in delta), "equal_events": sum(value == 0 for value in delta),
            "worse_events": sum(value < 0 for value in delta), "uncertainty": uncertainty,
            "descriptive_normal95_interval": cu._interval(mean, uncertainty["design_se"])},
        "exit_usage": {"reasons": {reason: sum(item["reason"] == reason for item in managed) for reason in REASONS},
            "holding_minutes": {arm: _describe([pair[arm]["holding_minutes"] for pair in pairs]) for arm in ARMS},
            "fill_overshoot": {"fill_minus_trigger_price_change": _describe([item["fill_minus_trigger_price_change"] for item in managed]),
                "fill_minus_trigger_threshold": _describe([item["fill_minus_trigger_threshold"] for item in managed]),
                "by_trigger_reason": {reason: {
                    "events": sum(item["reason"] == reason for item in managed),
                    "fill_below_trigger_close": sum(item["fill_minus_trigger_price_change"] < 0 for item in managed if item["reason"] == reason),
                    "fill_below_trigger_threshold": sum(item["fill_minus_trigger_threshold"] < 0 for item in managed if item["reason"] == reason),
                    "nonpositive_after_assumed_cost": sum(item["price_change"] <= COST for item in managed if item["reason"] == reason),
                    "fill_minus_trigger_price_change": _describe([item["fill_minus_trigger_price_change"] for item in managed if item["reason"] == reason]),
                    "fill_minus_trigger_threshold": _describe([item["fill_minus_trigger_threshold"] for item in managed if item["reason"] == reason])}
                    for reason in REASONS[:2]}}},
        "touch_diagnostic": {"touch_orders": {name: sum(pair["touch_diagnostic"]["touch_order"] == name for pair in pairs) for name in dj.ORDER_CLASSES},
            "same_minute_ambiguity_events": sum(bool(pair["touch_diagnostic"]["both_touched_same_minute_offsets"]) for pair in pairs),
            "scope": "original30minute_window_not_actual_managed_holding_or_ambiguous_execution_count",
            "touches_used_as_fills": False}}


def build_comparison(dj_report: Mapping, raw_chunks: Mapping | Iterable[Mapping], *,
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Stream original archive, preserve every row, size four cells, compare once.

    Smaller invented grids are supported. Actual immutable bytes and the full
    257-session/56-week/5140-target-per-policy geometry are caller obligations.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference = _source(dj_report, checkpoint)
    except (KeyError, TypeError, ValueError, OverflowError, dj.BreakoutPathRefusal,
            di.OpeningVolatilityComparisonRefusal, di.dh.BreakoutRetestComparisonRefusal,
            cu.OpeningRangeComparisonRefusal, ct.OpeningRangeRefusal, di.de.RecoveryRefusal,
            cc.CalibrationRefusal) as exc:
        raise BreakoutExitPolicyRefusal("malformed_or_mismatched_DJ_source") from exc
    weeks = list(dj_report["reference_weeks"])
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "source_provenance": {key: deepcopy(value) for key, value in dj_report.items() if key not in PROVENANCE_EXCLUDE},
        "source_target_matrix": deepcopy(dj_report["source_target_matrix"]),
        "upstream_calendar": deepcopy(dj_report["upstream_calendar"]),
        "reference_sessions": list(reference), "reference_weeks": weeks, "variants": {},
        "comparison_not_run_reasons": [], "requires_operator_choice": False,
        "requires_operator_choice_before_comparison": False,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
    by_key = {}
    for policy in POLICIES:
        source = dj_report["variants"][policy]
        days = [{**deepcopy(row), "breakout_exit_policy": None} for row in source["days"]]
        for row in days:
            by_key.setdefault((row["session"], row["symbol"]), []).append(row)
        result["variants"][policy] = {"days": days,
            "source_variant_provenance": {key: deepcopy(value) for key, value in source.items() if key != "days"},
            "by_arm": {}, "comparison": None}
    expected = sorted(by_key)
    if len(expected) > RESOURCES["maximum_chunks"]:
        raise BreakoutExitPolicyRefusal("input_chunk_budget_exceeded")
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise BreakoutExitPolicyRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    row_count = 0
    for day, symbol in expected:
        checkpoint()
        try:
            chunk = next(chunks)
        except StopIteration as exc:
            raise BreakoutExitPolicyRefusal("missing_input_chunk") from exc
        if not isinstance(chunk, Mapping) or chunk.get("session") != day or chunk.get("symbol") != symbol:
            raise BreakoutExitPolicyRefusal("input_chunk_order_or_identity_mismatch")
        if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                or chunk.get("evidence_level") != "assumed"):
            raise BreakoutExitPolicyRefusal("input_chunk_metadata_mismatch")
        cu._instant(chunk.get("retrieved_at"))
        raw = chunk.get("rows")
        if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
            raise BreakoutExitPolicyRefusal("input_session_row_budget_exceeded")
        row_count += len(raw)
        if row_count > RESOURCES["maximum_rows"]:
            raise BreakoutExitPolicyRefusal("input_row_budget_exceeded")
        vintage = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
        metadata = {name: chunk[name] for name in dj.METADATA}
        minutes, offsets, _ = dj._minutes(raw, day, symbol, checkpoint)
        for row in by_key[day, symbol]:
            if row["data_vintage"] != vintage or row["input_rows"] != len(raw):
                raise BreakoutExitPolicyRefusal("source_raw_vintage_or_count_mismatch")
            _same(row["raw_chunk_metadata"], metadata, "source_raw_metadata_mismatch")
            _same(row["source_offset_counts_ms"], dict(offsets), "source_raw_offsets_mismatch")
            if dj._eligible(row):
                row["breakout_exit_policy"] = _label_pair(row, minutes)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise BreakoutExitPolicyRefusal("extra_input_chunk")
    result["input_evidence"] = {"unique_target_days": len(expected), "queried_rows": row_count}
    records, populations = {}, {}
    for policy, variant in result["variants"].items():
        population = [row for row in variant["days"] if row["breakout_exit_policy"] is not None]
        populations[policy] = population
        records[policy] = {arm: [{"session": row["session"], "symbol": row["symbol"],
            "label": row["breakout_exit_policy"][arm]["price_change"]} for row in population] for arm in ARMS}
        for arm in ARMS:
            checkpoint()
            own = cc.dispersion(records[policy][arm], reference)
            reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
            if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
                reasons.append("undefined_own_label_sizing")
            elif own["mde_bps"] > 33:
                reasons.append("own_label_mde_exceeds_assumed_cost_floor")
            variant["by_arm"][arm] = {"own_label_dispersion": own, "sizing_passed": not reasons,
                "comparison_choice_reasons": reasons, "frequency": di._frequency(records[policy][arm], reference, weeks)}
            result["comparison_not_run_reasons"].extend(policy + ":" + arm + ":" + reason for reason in reasons)
    if not result["comparison_not_run_reasons"]:
        for policy, variant in result["variants"].items():
            checkpoint()
            variant["comparison"] = _comparison(populations[policy], records[policy], variant["by_arm"], reference, weeks, checkpoint)
        result.update(comparison_performed=True, exit_policy_compared=True, new_performance_families=1)
    result["requires_operator_choice"] = result["requires_operator_choice_before_comparison"] = bool(result["comparison_not_run_reasons"])
    result["calibration_only"] = bool(result["comparison_not_run_reasons"])
    checkpoint()
    return result
