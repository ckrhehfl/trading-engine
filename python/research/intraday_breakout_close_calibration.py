"""Own-label sizing for the fixed original-breakout close-location hypothesis.

Only an already decoded, caller-authenticated completed DK report is accepted.
Selection reads original causal bars only; labels are validated separately and
used solely at the fresh conditional dispersion boundary. No market IO occurs.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import math
import random
from statistics import NormalDist, median, stdev

from research import intraday_breakout_loss_concentration as dl


ROOT = dl.ROOT
SPECIFICATION_ID = "intraday-breakout-close-calibration-v1"
MODE = "fixed-original-basic-breakout-close-location-own-label-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-breakout-close-calibration-v1.json"
OUTPUT_NAME = "intraday-dm-breakout-close-calibration-20261024-v1"
WINDOW, POLICIES = dict(dl.WINDOW), dl.POLICIES
UPSTREAM_SHA256 = dl.UPSTREAM_SHA256
UPSTREAM_SPECIFICATION_SHA256 = dl.UPSTREAM_SPECIFICATION_SHA256
SOURCE_ROLES = dict(dl.SOURCE_ROLES)
NEXT_HYPOTHESIS = deepcopy(dl.NEXT_HYPOTHESIS)
RESOURCES = {**dl.RESOURCES, "maximum_report_bytes": 67108864}
EXPECTED_GEOMETRY = {"reference_sessions": 257, "reference_weeks": 56,
    "targets_per_policy": 5140, "original_book_events": {"D+1": 1169, "D+2": 1167}}
PARAMETERS = {
    "next_hypothesis": NEXT_HYPOTHESIS,
    "cohort": "same_original_DK_common_outcome_complete_basic_time_only_book_no_new_outcome_exclusion",
    "all_targets": "retain_all_original_targets_masks_labels_unknowns_and_outside_original_book_without_feature_evaluation",
    "entry_delay_minutes": 3, "time_cap_minutes": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "sizing": "CC_exact_eventSD_naiveSE_all_reference_session_clusterSE_circular5session_bootstrap_maxSE_normal_two_sided5percent_power80percent",
    "weekly_operating_objective": 15,
    "frequency": "all_original_reference_weeks_including_zero_selected_unknown_outside_book_loose_bound_not_portfolio_entries",
    "future_comparison_gate": "finish_calibration_regardless_unknown_features_or_selected_labels_undefined_or_nonfinite_MDE_or_MDE_above33bp_require_operator_choice_before_future_comparison_no_transferred_waiver",
    "output_scope": "frequency_missingness_and_fresh_selected_own_label_dispersion_only_no_mean_net_winrate_contrast_new_label_or_performance_family",
    "expected_actual_geometry": EXPECTED_GEOMETRY,
}
ASSUMPTIONS = {
    "source": "caller_authenticated_completed_DK_only_no_parent_replay_API_DB_or_archive_read",
    "identity": "full_source_metadata_matrix_rows_stream_hashed_without_parent_copy_prior_statistics_authenticated_not_recalculated",
    "population": "original_DK_common_complete_basic_time_only_book_and_all_original_targets_masks_unknowns_no_expansion",
    "selection": "original_first_B_only_causal_initial15minute_context_no_retry_no_future_entry_OPEN_or_label_in_predicate",
    "availability": "original_assumed_D_Bendplus2_E_Dplus3_OPEN_cap_Eplus30_OPEN_original_label_availability_preserved",
    "measurement": "selected_original_own_price_labels_conditional_not_fills_or_account_entries_or_portfolio_returns",
    "cost": "33bp_assumed_planning_floor_not_verified_historical_cost",
    "policies": "overlapping_Dplus1_Dplus2_input_availability_assumptions_not_independent_replications",
    "claims": "calibration_only_no_mean_comparison_edge_direction_closure_reserved_access_or_promotion",
}
FLAGS = {**dl.FLAGS, "diagnostic_only": False, "retrospective_only": False,
    "calibration_only": True, "candidate_measured": True, "resampling_performed": True,
    "new_selection_calculated": True, "original_signal_detector_replayed": False}
STATES = ("selected", "rejected", "unknown", "not_applicable", "outside_original_book")
_MINUTE = timedelta(minutes=1)
_KST = timezone(timedelta(hours=9))
_canonical, _digest = dl._canonical, dl._digest


class BreakoutCloseCalibrationRefusal(RuntimeError):
    """Stable refusal codes never expose supplied private evidence."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise BreakoutCloseCalibrationRefusal(reason)


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


def _opening(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=_KST,
        hour=10 if day in {"2025-11-13", "2026-01-02"} else 9)


def _bar(bar: object, decision: datetime) -> tuple | None:
    """Validate every supplied field even when another required field is absent."""
    if bar is None:
        return None
    _require(isinstance(bar, Mapping), "malformed_feature_bar")
    prices = {key: dl._price(bar[key]) for key in ("open", "high", "low", "close")
        if bar.get(key) is not None}
    clocks = {key: dl._instant(bar[key]) for key in ("start_at", "end_at", "available_at")
        if bar.get(key) is not None}
    low, high = prices.get("low"), prices.get("high")
    if low is not None and high is not None:
        _require(low <= high, "contradictory_feature_OHLC")
    for key in ("open", "close"):
        if key in prices:
            _require((low is None or low <= prices[key]) and (high is None or prices[key] <= high),
                "contradictory_feature_OHLC")
    if "start_at" in clocks and "end_at" in clocks:
        _require(clocks["end_at"] - clocks["start_at"] == 5 * _MINUTE,
            "feature_bar_duration_changed")
    if "available_at" in clocks:
        _require(clocks["available_at"] <= decision, "feature_uses_future_information")
        if "end_at" in clocks:
            _require(clocks["available_at"] == clocks["end_at"] + 2 * _MINUTE,
                "feature_bar_availability_changed")
    if len(prices) != 4 or len(clocks) != 3:
        return None
    return clocks, prices


def _feature(event: Mapping, breakout_bar: object, session: str) -> tuple[str, str | None]:
    """No label, entry price, later contact or outcome is accepted by this API."""
    decision = dl._instant(event["decision_at"])
    context = event.get("context")
    _require(context is None or isinstance(context, (list, tuple)), "malformed_initial_context")
    _require(context is None or len(context) <= 3, "initial_context_not_exactly_three")
    bars = [_bar(bar, decision) for bar in (context or [])]
    breakout = _bar(breakout_bar, decision)
    opening = _opening(session)
    # Present clocks cannot contradict the original initial range, even if a
    # missing price elsewhere makes the final feature unknown.
    for index, raw in enumerate(context or []):
        if isinstance(raw, Mapping):
            for key, expected in (("start_at", opening + index * 5 * _MINUTE),
                    ("end_at", opening + (index + 1) * 5 * _MINUTE)):
                if raw.get(key) is not None:
                    _require(dl._instant(raw[key]) == expected, "initial_context_clock_changed")
    if isinstance(breakout_bar, Mapping):
        for key, expected in (("start_at", dl._instant(event["signal_bar_start"])),
                ("end_at", dl._instant(event["signal_bar_end"])), ("available_at", decision)):
            if breakout_bar.get(key) is not None:
                _require(dl._instant(breakout_bar[key]) == expected, "breakout_original_clock_mismatch")
        if breakout_bar.get("close") is not None and context is not None and len(context) == 3 \
                and all(isinstance(bar, Mapping) and bar.get("high") is not None for bar in context):
            _require(dl._price(breakout_bar["close"]) > max(dl._price(bar["high"]) for bar in context),
                "original_breakout_not_above_initial15_high")
    if breakout is None:
        return "unknown", "missing_breakout_bar_or_OHLC_or_clock"
    if context is None or len(context) < 3 or any(bar is None for bar in bars):
        return "unknown", "missing_initial_context_or_OHLC_or_clock"
    _require(dl._instant(event["signal_bar_start"]) >= opening + 15 * _MINUTE,
        "breakout_overlaps_initial_context")
    _, prices = breakout
    initial_high = max(bar[1]["high"] for bar in bars)
    _require(prices["close"] > initial_high, "original_breakout_not_above_initial15_high")
    if prices["high"] == prices["low"]:
        return "unknown", "degenerate_breakout_range"
    selected = 4 * (prices["close"] - prices["low"]) >= 3 * (prices["high"] - prices["low"])
    return ("selected", None) if selected else ("rejected", None)


def _original_label(row: Mapping, in_book: bool) -> Fraction | None:
    """Authenticate original source clocks/prices without replay or loss metrics."""
    basic = row["arms"]["basic"]
    if basic["status"] != "signal":
        _require(row["breakout_exit_policy"] is None, "unselected_source_has_exit_policy")
        return None
    event, label = basic["event"], basic["label"]
    decision, end = dl._instant(event["decision_at"]), dl._instant(event["signal_bar_end"])
    start = dl._instant(event["signal_bar_start"])
    entry, cap = dl._instant(label["entry_at"]), dl._instant(label["exit_at"])
    opening = _opening(row["session"])
    _require(end - start == 5 * _MINUTE and start >= opening + 15 * _MINUTE
        and (start - opening) % (5 * _MINUTE) == timedelta()
        and decision == end + 2 * _MINUTE and dl._instant(event["available_at"]) == decision
        and entry == decision + 3 * _MINUTE and cap == entry + 30 * _MINUTE,
        "source_original_signal_entry_or_cap_clock_changed")
    _require(dl._instant(label["time_cap_decided_at"]) == decision
        and dl._instant(label["entry_price_available_at"]) == entry + 3 * _MINUTE
        and dl._instant(label["label_available_at"]) == cap + 3 * _MINUTE,
        "source_original_label_availability_changed")
    _require(label["actual_fill_certified"] is False and label["evidence_level"] == "assumed"
        and type(label["required_minute_buckets"]) is int and label["required_minute_buckets"] == 31,
        "source_original_label_metadata_changed")
    gross = None
    if label["status"] == "known":
        gross = dl._ratio(label["price_change"])
        _require(gross == dl._price(label["exit_open"]) / dl._price(label["entry_open"]) - 1
            and label["unknown_minute_buckets"] == 0 and label["unknown_reasons"] == []
            and not any(label["unknown_bucket_reason_counts"].values()), "source_original_label_changed")
    else:
        _require(label["price_change"] is None and type(label["unknown_minute_buckets"]) is int
            and 0 < label["unknown_minute_buckets"] <= 31 and bool(label["unknown_reasons"]),
            "source_unknown_label_changed")
        for name in ("entry_open", "exit_open"):
            if label.get(name) is not None:
                dl._price(label[name])
    pair = row["breakout_exit_policy"]
    if not in_book:
        _require(pair is None, "outside_original_book_has_exit_policy")
        return gross
    _require(isinstance(pair, Mapping), "selected_source_label_missing")
    common, timed = pair["common"], pair["time_only"]
    _require(label["status"] == timed["status"] == "known" and timed["unknown_reason"] is None
        and timed["actual_fill_certified"] is False and timed["holding_minutes"] == 30,
        "selected_original_outcome_not_known")
    for actual, expected in ((common["decision_at"], decision), (common["entry_at"], entry),
            (common["time_cap_at"], cap), (common["time_cap_decided_at"], decision),
            (common["entry_available_at"], entry + 3 * _MINUTE), (timed["exit_at"], cap),
            (timed["label_available_at"], cap + 3 * _MINUTE)):
        _require(dl._instant(actual) == expected, "source_time_only_clock_or_availability_changed")
    _require(gross == dl._ratio(timed["price_change"]) == dl._ratio(common["anchor_price_change"])
        and dl._price(timed["entry_open"]) == dl._price(label["entry_open"])
        and dl._price(timed["exit_open"]) == dl._price(label["exit_open"])
        == dl._price(common["anchor_open"]), "source_time_only_price_or_label_anchor_changed")
    dl._same(common["raw_chunk_metadata"], row["raw_chunk_metadata"], "source_time_only_metadata_changed")
    dl._same(common["data_vintage"], row["data_vintage"], "source_time_only_vintage_changed")
    return gross


def _finite_dispersion(records: list[dict], reference: list[str], checkpoint: Callable[[], None]) -> dict:
    """CC dispersion methodology; mean is internal centering, never output."""
    totals = {day: [0.0, 0] for day in reference}
    values = []
    for row in records:
        if row["label"] is not None:
            value = float(row["label"])
            if not math.isfinite(value):
                raise OverflowError("nonfinite_statistical_label")
            values.append(value)
            totals[row["session"]][0] += value
            totals[row["session"]][1] += 1
    n, groups = len(values), len(totals)
    known_groups = sum(count > 0 for _, count in totals.values())
    result = {"events": len(records), "known_labels": n, "unknown_labels": len(records) - n,
        "reference_sessions": groups, "known_label_sessions": known_groups,
        "event_sd": None, "naive_se": None, "session_cluster_se": None, "bootstrap_se": None,
        "design_se": None, "corrected_to_naive_se_ratio": None, "mde_bps": None,
        "bootstrap_zero_label_draws": 0, "cost_floor_bps_assumed": "33",
        "would_require_operator_underpower_choice": False,
        "planning_constraint": "insufficient_known_labels_or_reference_sessions",
        "planning_constraint_scope": "conditional own labels; not final family readiness or an adopted waiver",
        "family_adopted": False, "full_family_certified": False}
    if n < 2 or groups < 2:
        return result
    sd, center = stdev(values), math.fsum(values) / n
    naive = sd / math.sqrt(n)
    result.update(event_sd=sd, naive_se=naive)
    if known_groups < 2:
        result["planning_constraint"] = "insufficient_known_label_sessions"
        return result
    cluster = math.sqrt(groups / (groups - 1) * math.fsum(
        (total - count * center) ** 2 for total, count in totals.values())) / n
    daily = list(totals.values())
    blocks = {size: [(math.fsum(daily[(index + offset) % groups][0] for offset in range(size)),
        sum(daily[(index + offset) % groups][1] for offset in range(size)))
        for index in range(groups)] for size in {5, groups % 5} if size}
    randomizer, draws = random.Random(0), []
    for index in range(2000):
        if index % 64 == 0:
            checkpoint()
        sampled = [blocks[5][randomizer.randrange(groups)] for _ in range(groups // 5)]
        if groups % 5:
            sampled.append(blocks[groups % 5][randomizer.randrange(groups)])
        count = sum(item[1] for item in sampled)
        if count:
            draws.append(math.fsum(item[0] for item in sampled) / count)
    bootstrap = stdev(draws) if len(draws) >= 2 else None
    design = max(naive, cluster, bootstrap) if bootstrap is not None else None
    result.update(session_cluster_se=cluster, bootstrap_se=bootstrap, design_se=design,
        corrected_to_naive_se_ratio=design / naive if naive and design is not None else None,
        mde_bps=(NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * design * 10000
        if design is not None else None, bootstrap_zero_label_draws=2000 - len(draws))
    result["would_require_operator_underpower_choice"] = result["mde_bps"] is not None and result["mde_bps"] > 33
    result["planning_constraint"] = ("insufficient_valid_bootstrap_draws" if design is None else
        "own_label_mde_exceeds_assumed_cost_floor" if result["would_require_operator_underpower_choice"]
        else "no_underpower_choice_inferred")
    return result


def _dispersion(records: list[dict], reference: list[str], checkpoint: Callable[[], None]) -> dict:
    """A nonfinite sizing boundary leaves calibration complete and MDE unknown."""
    try:
        result = _finite_dispersion(records, reference, checkpoint)
        if any(isinstance(value, float) and not math.isfinite(value) for value in result.values()):
            raise OverflowError("nonfinite_statistical_dispersion")
        return result
    except OverflowError:
        known = [row for row in records if row["label"] is not None]
        return {"events": len(records), "known_labels": len(known), "unknown_labels": len(records) - len(known),
            "reference_sessions": len(reference), "known_label_sessions": len({row["session"] for row in known}),
            "event_sd": None, "naive_se": None, "session_cluster_se": None, "bootstrap_se": None,
            "design_se": None, "corrected_to_naive_se_ratio": None, "mde_bps": None,
            "bootstrap_zero_label_draws": 0, "cost_floor_bps_assumed": "33",
            "would_require_operator_underpower_choice": False,
            "planning_constraint": "nonfinite_own_label_dispersion",
            "planning_constraint_scope": "conditional own labels; not final family readiness or an adopted waiver",
            "family_adopted": False, "full_family_certified": False}


def _cell() -> dict:
    return {"targets": 0, "original_book_events": 0, "state_counts": dict.fromkeys(STATES, 0),
        "selected_known_labels": 0, "selected_unknown_labels": 0}


def _variant(source: Mapping, policy: str, reference: list[str], weeks: list[str], checkpoint: Callable[[], None]) -> dict:
    variant = source["variants"][policy]
    _require(set(variant) == {"days", "source_variant_provenance", "by_arm", "comparison"}, "source_variant_schema_changed")
    rows, matrix = variant["days"], source["source_target_matrix"][policy]["days"]
    _require(isinstance(rows, list) and [day["session"] for day in matrix] == reference, "source_target_calendar_changed")
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
    population, states, reasons, records = dl._empty_population(), _cell(), Counter(), []
    by_week = {week: _cell() for week in weeks}
    by_month = {month: _cell() for month in sorted({day[:7] for day in reference})}
    by_session = {day: _cell() for day in reference}
    target_states, selected_coordinates = [], []
    for row, (membership, target) in zip(rows, expected, strict=True):
        checkpoint()
        _require(row["session"] == membership["session"] and row["symbol"] == target["symbol"]
            and dl._SYMBOL.fullmatch(row["symbol"]) is not None, "source_target_identity_changed")
        dl._same(row["membership_target"], target, "source_membership_target_changed")
        dl._same(row["membership_source"], {key: value for key, value in membership.items() if key != "targets"},
            "source_membership_metadata_changed")
        _require(isinstance(row["data_vintage"], str) and dl._SHA.fullmatch(row["data_vintage"]) is not None,
            "source_vintage_changed")
        metadata = row["raw_chunk_metadata"]
        _require(metadata["observation_date"] == row["session"] and metadata["available_at"] is None
            and metadata["source"] == "collector klines" and metadata["is_final"] is None
            and metadata["evidence_level"] == "assumed", "source_raw_metadata_changed")
        dl._instant(metadata["retrieved_at"])
        in_book = dl._row_state(row)
        dl._account(population, row, in_book)
        gross = _original_label(row, in_book)
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
            state, reason = _feature(basic["event"], path.get("breakout_bar") if path is not None else None, row["session"])
        if state == "unknown":
            reasons[reason] += 1
        for cell in (states, by_session[row["session"]], by_week[dl._week(row["session"])], by_month[row["session"][:7]]):
            cell["targets"] += 1
            cell["original_book_events"] += in_book
            cell["state_counts"][state] += 1
            if state == "selected":
                cell["selected_known_labels"] += gross is not None
                cell["selected_unknown_labels"] += gross is None
        item = {"session": row["session"], "symbol": row["symbol"], "state": state, "reason": reason,
            "in_original_book": in_book, "source_row_sha256": _digest(row, checkpoint),
            "data_vintage": row["data_vintage"], "raw_chunk_metadata": deepcopy(metadata),
            "source_fetched_at": deepcopy(row["source_fetched_at"]),
            "feature_evaluated": in_book,
            "feature_available_at": basic["event"]["decision_at"] if in_book
                and reason in {None, "degenerate_breakout_range"} else None,
            "feature_availability_evidence_level": "assumed" if in_book
                and reason in {None, "degenerate_breakout_range"} else None,
            "basic_status": basic["status"], "retest_status": row["arms"]["retest"]["status"],
            "basic_label_status": basic["label"]["status"] if basic["label"] is not None else "not_applicable",
            "signal_observable": row["signal_observable"], "outcome_complete": row["outcome_complete"],
            "exclusion_reasons": list(row["exclusion_reasons"])}
        target_states.append(item)
        if state == "selected":
            selected_coordinates.append([row["session"], row["symbol"]])
            records.append({"session": row["session"], "label": gross})
    receipts = dl._receipts(variant, states["original_book_events"], len(reference))
    own = _dispersion(records, reference, checkpoint)
    choice = []
    if own["unknown_labels"]:
        choice.append("selected_unknown_original_labels")
    if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
        choice.append("own_label_mde_undefined_or_nonfinite")
    elif own["mde_bps"] > 33:
        choice.append("own_label_mde_exceeds_assumed_cost_floor")
    sizing_passed = not choice
    if any(item["in_original_book"] and item["state"] == "unknown" for item in target_states):
        choice.append("unknown_original_book_features")
    weekly = [cell["state_counts"]["selected"] for cell in by_week.values()]
    upper = [cell["state_counts"]["selected"] + cell["state_counts"]["unknown"]
        + cell["state_counts"]["outside_original_book"] for cell in by_week.values()]
    frequency = {"reference_weeks": len(weeks), "selected_events": len(records),
        "selected_distinct_dates": len({row["session"] for row in records}),
        "selected_known_labels": own["known_labels"], "selected_unknown_labels": own["unknown_labels"],
        "selected_mean_per_all_reference_weeks": Fraction(len(records), len(weeks)),
        "selected_weekly_min": min(weekly), "selected_weekly_median": median(weekly), "selected_weekly_max": max(weekly),
        "weeks_at_least15_selected": sum(value >= 15 for value in weekly),
        "loose_selected_unknown_outside_book_upper_events": sum(upper),
        "loose_upper_mean_per_all_reference_weeks": Fraction(sum(upper), len(weeks)),
        "loose_upper_weeks_at_least15": sum(value >= 15 for value in upper),
        "upper_bound_is_estimate": False, "opportunities_are_fills_or_account_entries": False}
    population.update(original_book_events=states["original_book_events"], state_counts=states["state_counts"],
        selected_known_labels=own["known_labels"], selected_unknown_labels=own["unknown_labels"])
    return {"population": population, "unknown_reason_counts": dict(sorted(reasons.items())),
        "target_states": target_states, "selected_coordinates": selected_coordinates,
        "by_session": by_session, "by_week": by_week, "by_month": by_month,
        "frequency": frequency, "own_label_dispersion": own,
        "sizing_passed": sizing_passed, "requires_operator_choice_before_comparison": bool(choice),
        "comparison_choice_reasons": choice,
        "source_variant_sha256": _digest({key: value for key, value in variant.items() if key != "days"}, checkpoint),
        "source_rows_sha256": _digest(rows, checkpoint), "source_calibration_receipts": receipts,
        "reconciliation": {"all_source_rows_accounted": True, "original_book_preserved": True,
            "state_counts_match_targets": sum(states["state_counts"].values()) == len(rows),
            "weekly_monthly_match_selected": all(sum(cell["state_counts"]["selected"] for cell in panel.values())
                == len(records) for panel in (by_session, by_week, by_month))}}


def build_calibration(dk_report: Mapping, *, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Finish calibration even when a future comparison requires operator choice.

    The caller authenticates the exact source bytes and actual full geometry.
    Small invented grids remain supported for arithmetic and contract tests.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference, weeks = dl._source_header(dk_report)
        variants = {policy: _variant(dk_report, policy, reference, weeks, checkpoint) for policy in POLICIES}
        identity = {"completed_DK_sha256": UPSTREAM_SHA256,
            "DK_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
            "canonical_source_sha256": _digest(dk_report, checkpoint),
            "source_target_matrix_sha256": _digest(dk_report["source_target_matrix"], checkpoint),
            "upstream_calendar_sha256": _digest(dk_report["upstream_calendar"], checkpoint),
            "source_metadata_sha256": _digest({key: value for key, value in dk_report.items()
                if key not in {"variants", "source_target_matrix", "upstream_calendar"}}, checkpoint)}
    except (dl.BreakoutLossConcentrationRefusal, KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        raise BreakoutCloseCalibrationRefusal("malformed_or_mismatched_completed_DK_source") from exc
    reasons = [policy + ":" + reason for policy, variant in variants.items()
        for reason in variant["comparison_choice_reasons"]]
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "next_hypothesis": deepcopy(NEXT_HYPOTHESIS),
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "source_identity": identity, "reference_sessions": list(reference), "reference_weeks": weeks,
        "variants": variants, "requires_operator_choice": bool(reasons),
        "requires_operator_choice_before_comparison": bool(reasons), "comparison_not_run_reasons": reasons,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
