"""Own-label calibration of the fixed OR15 opening-volume hypothesis.

Only the two supplied, independently verified reports are consumed. Original
reports remain immutable pinned evidence; this child retains every OR15 target,
first event, label and source linkage. No IO or performance comparison occurs.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
import re
from statistics import median

from research import intraday_activity_readiness as cx
from research import intraday_opening_range_comparison as cu

ct = cu.ct
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-opening-volume-v1"
MODE = "opening-volume-own-label-frequency-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-volume-v1.json"
WINDOW = dict(ct.WINDOW)
ARMS = ("15",)
UPSTREAM_SHA256 = cu.UPSTREAM_SHA256
UPSTREAM_SPECIFICATION_SHA256 = cu.UPSTREAM_SPECIFICATION_SHA256
ACTIVITY_SHA256 = "1a4d41204d421685e3f1ad2b8b93cb40b57240204e1918e965c0576585d2a2e6"
ACTIVITY_SPECIFICATION_SHA256 = "69c7c35c2fcf07d3bd0ee916edce67b5cffb91acc88c2d94f01448717bd55d7a"
REGISTERED_PATHS = {**cu.REGISTERED_PATHS,
    "activity_path": "/home/minjun4897/research-evidence/intraday-cx-activity-readiness-20261023-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cy-opening-volume-20261023-v1"}
RESOURCES = {**cu.RESOURCES, "maximum_activity_bytes": 33554432,
    "maximum_report_bytes": 67108864}
PARAMETERS = {
    "source_parameters": deepcopy(ct.PARAMETERS), "next_hypothesis": deepcopy(cx.NEXT_HYPOTHESIS),
    "arms": ["15"], "volume_ratio_threshold": {"numerator": "2", "denominator": "1"},
    "cohort": "original_CT_OR15_first_signal_only_no_retry_or_outcome_complete_mask",
    "label": "unchanged_original_CT_entry_plus30minute_cap_open",
    "feature_available_at": "current_session_open_plus15minutes_plus2minutes_assumed",
    "baseline": "all_previous20_original_reference_sessions_same_opening_HHMM_no_coordinate_additions",
    "frequency_metrics": ["selected_per_reference_week", "known_labels_per_reference_week",
        "weeks_with_at_least15_selected", "weekly_selected_median", "weekly_selected_min", "weekly_selected_max",
        "selected_plus_unknown_opportunity_upper_bound", "weeks_with_upper_bound_at_least15"],
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "cost_floor_bps": "33", "weekly_operating_objective": 15,
    "output_scope": "selection_frequency_missingness_own_label_dispersion_only_no_performance_comparison",
    "underpower_exception_applied": False,
}
FLAGS = {**ct.FLAGS, "historical_volume_availability_certified": False,
    "volume_units_or_revisions_certified": False}
ASSUMPTIONS = {
    "population": "all_original257_dates56_weeks5140_targets_per_policy5158_union_retained_actual_caller_enforces_geometry",
    "predicate": "original_OR15_first_signal_and_initial15_volume_at_least_twice_exact_prior20_median_equality_passes_no_price_impulse_conjunct",
    "baseline": "all_required_reference_sessions_same_opening_HHMM_complete_valid_nonnegative15minute_windows_positive_exact_median_no_observed_day_replacement",
    "timing": "current_open_plus17minutes_and_all_prior_windows_available_no_later_than_original_decision_even_when_rejected_original_entry_and_exit_unchanged",
    "metadata": "CX_query_retrieved_at_distinct_from_CC_price_retrieval_and_assumed_historical_availability_finality_unknown",
    "lineage": "CX_saved_chunk_hash_equals_CT_price_vintage_and_row_bucket_offset_fetched_sets_agree_current_volume_hash_remains_separate_sets_do_not_prove_per_row_vintage",
    "units": "raw_share_volume_units_and_historical_revisions_unknown_not_an_economic_activity_shock_certificate",
    "missingness": "original_unknown_retained_known_no_signal_not_applicable_original_signal_with_unavailable_feature_unknown_never_rejected_or_zero_filled",
    "power": "own_selected_labels_all_reference_dates_including_zero_naive_session_cluster_circular5session2000draw_seed0_maxSE",
    "frequency": "opportunities_not_fills_or_independent_observations15perweek_operating_fit_never_forced_trades",
    "checkpoint": "selected_unknown_labels_undefined_or_over33bp_own_MDE_require_operator_choice_before_any_separate_comparison_no_old_waiver",
    "source": "immutable_full_CT_CX_reports_retained_at_pinned_paths_child_retains_OR15_and_exact_source_links_flags_sizing_choices_manifests_no_new_raw_reconstruction",
    "claims": "no_mean_net_winrate_contrast_performance_family_winner_edge_direction_closure_reserved_access_or_promotion",
}
STATES = ("selected", "rejected", "unknown", "not_applicable")
_canonical = ct._canonical


class OpeningVolumeRefusal(RuntimeError):
    """Stable refusals do not disclose supplied source values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "activity_sha256": ACTIVITY_SHA256,
        "activity_specification_sha256": ACTIVITY_SPECIFICATION_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise OpeningVolumeRefusal("specification_scope_mismatch")


def _same(actual, expected):
    if (actual != expected if isinstance(expected, set) else _canonical(actual) != _canonical(expected)):
        raise OpeningVolumeRefusal("activity_source_structure_or_linkage_changed")


def _integer(value, maximum=600):
    if type(value) is not int or not 0 <= value <= maximum:
        raise OpeningVolumeRefusal("invalid_activity_count")
    return value


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise OpeningVolumeRefusal("invalid_activity_hash")


def _field_counts(values, rows, present):
    _same(set(values), set(cx.STATES))
    if sum(_integer(value) for value in values.values()) != rows:
        raise OpeningVolumeRefusal("activity_field_counts_disagree")
    if (present and values["absent_column"]) or (not present and values["absent_column"] != rows):
        raise OpeningVolumeRefusal("activity_column_presence_disagrees")


def _window(window, minutes, record):
    _same(set(window), {"state", "required_buckets", "missing_buckets", "conflicting_buckets",
        "invalid_volume_buckets", "volume_sum"})
    if window["required_buckets"] != minutes:
        raise OpeningVolumeRefusal("activity_window_length_changed")
    failures = [_integer(window[name], minutes) for name in
        ("missing_buckets", "conflicting_buckets", "invalid_volume_buckets")]
    if sum(failures) > minutes or window["state"] not in {"ready", "unknown"}:
        raise OpeningVolumeRefusal("activity_window_state_invalid")
    if window["state"] == "unknown":
        if not any(failures) or window["volume_sum"] is not None:
            raise OpeningVolumeRefusal("activity_unknown_window_inconsistent")
        return
    value = cu._ratio(window["volume_sum"])
    counts = record["field_counts"]["volume"]
    if (any(failures) or value < 0 or record["bucket_comparison"]["current_unique_buckets"] < minutes
            or counts["zero"] + counts["nonzero"] < minutes
            or value == 0 and counts["zero"] < minutes
            or value > 0 and not counts["nonzero"]):
        raise OpeningVolumeRefusal("activity_ready_window_inconsistent")


def _baseline(record, index, reference, position):
    day, symbol = record["session"], record["symbol"]
    prior = reference[max(0, position - 20):position]
    clock = ct.cc.session_open(day).strftime("%H:%M")
    windows = [index.get((past, symbol), {}).get("same_clock_windows", {}).get(f"{clock}/15")
        for past in prior]
    missing = sum(window is None for window in windows)
    invalid = sum(window is not None and window["state"] != "ready" for window in windows)
    mismatch = sum(ct.cc.session_open(past).strftime("%H:%M") != clock for past in prior)
    baseline = None
    if len(prior) == 20 and not (missing or invalid or mismatch):
        values = sorted(cu._ratio(window["volume_sum"]) for window in windows)
        baseline = (values[9] + values[10]) / 2
    positive = None if baseline is None else baseline > 0
    ready = record["opening_prefixes"]["15"]["state"] == "ready" and positive is True
    expected = {"state": "ready" if ready else "unknown", "same_wall_clock": clock,
        "required_prior_sessions": 20, "available_prior_reference_sessions": len(prior),
        "missing_target_coordinates": missing, "invalid_prior_windows": invalid,
        "prior_opening_clock_mismatches": mismatch, "positive_exact_baseline_median": positive,
        "ratio_or_threshold_evaluated": False}
    _same(record["next_hypothesis_feature_readiness"], expected)
    return prior, baseline, ready


def _validate_activity(source, activity, checkpoint):
    _same(activity["study_id"], cx.SPECIFICATION_ID)
    _same(activity["mode"], cx.MODE)
    for name, value in (("window", WINDOW), ("parameters", cx.PARAMETERS),
            ("next_hypothesis", cx.NEXT_HYPOTHESIS)):
        _same(activity[name], value)
    for name, value in cx.FLAGS.items():
        if activity.get(name) is not value:
            raise OpeningVolumeRefusal("activity_mode_or_certification_changed")
    if activity.get("specification_sha256", ACTIVITY_SPECIFICATION_SHA256) != ACTIVITY_SPECIFICATION_SHA256:
        raise OpeningVolumeRefusal("activity_specification_changed")
    for name in ("reference_sessions", "source_target_matrix", "upstream_calendar"):
        _same(activity[name], source[name])
    presence = activity["schema_inventory"]["field_presence"]
    _same(set(presence), {"volume", "quote_volume"})
    if any(type(value) is not bool for value in presence.values()):
        raise OpeningVolumeRefusal("invalid_activity_schema_presence")
    physical = {(row["session"], row["symbol"]): row for variant in source["variants"].values()
        for row in variant["days"]}
    records = activity["target_days"]
    _same([(row["session"], row["symbol"]) for row in records], sorted(physical))
    index = {}
    fields = {field: Counter() for field in presence}
    daily_fields = {field: Counter() for field in presence}
    buckets = Counter()
    for record in records:
        checkpoint()
        day, symbol = record["session"], record["symbol"]
        old = physical[day, symbol]
        rows = _integer(record["current_rows"])
        _same(record["saved_rows"], old["input_rows"])
        _same(rows, old["input_rows"])
        _same(record["saved_chunk_sha256"], old["data_vintage"])
        for name in ("saved_chunk_sha256", "current_chunk_sha256", "current_rows_sha256", "daily_rows_sha256"):
            _hash(record[name])
        _same(record["source_fetched_at"], old["source_fetched_at"])
        _same(record["source_offset_counts_ms"], old["source_offset_counts_ms"])
        if record["source_fetched_at"] != sorted(set(record["source_fetched_at"])) or any(
                not isinstance(value, str) for value in record["source_fetched_at"]):
            raise OpeningVolumeRefusal("activity_fetched_set_invalid")
        _same(record["absence"], "rows_observed" if rows else "no_rows_unknown_cause")
        _same(record["saved_archive_activity_fields"], "omitted_not_evidence_of_DB_absence")
        _same(record["recovery_horizon"], "outside_documented_upper_horizon" if day < "2025-10-23"
            else "within_documented_upper_horizon_but_unprobed")
        meta = record["raw_chunk_metadata"]
        _same(set(meta), {"observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level"})
        _same({key: value for key, value in meta.items() if key != "retrieved_at"},
            {"observation_date": day, "available_at": None, "source": "collector klines", "is_final": None, "evidence_level": "assumed"})
        cu._instant(meta["retrieved_at"])
        cu._instant(record["daily_retrieved_at"])
        comparison = record["bucket_comparison"]
        _same(set(comparison), {"saved_unique_buckets", "current_unique_buckets", "both", "saved_only", "current_only"})
        for value in comparison.values():
            _integer(value)
        unique = comparison["current_unique_buckets"]
        if (not unique <= rows or comparison != {"saved_unique_buckets": unique,
                "current_unique_buckets": unique, "both": unique, "saved_only": 0, "current_only": 0}
                or _integer(record["conflicting_buckets"]) > rows - unique):
            raise OpeningVolumeRefusal("activity_bucket_sets_or_conflicts_disagree")
        buckets.update(comparison)
        daily = _integer(record["daily_rows"], 1)
        if len(record["daily_source_fetched_at"]) != daily or any(value is not None and not isinstance(value, str)
                for value in record["daily_source_fetched_at"]):
            raise OpeningVolumeRefusal("activity_daily_fetched_count_invalid")
        for field, present in presence.items():
            _field_counts(record["field_counts"][field], rows, present)
            _field_counts(record["daily_field_counts"][field], daily, present)
            fields[field].update(record["field_counts"][field])
            daily_fields[field].update(record["daily_field_counts"][field])
        _same(set(record["field_counts"]), set(presence))
        _same(set(record["daily_field_counts"]), set(presence))
        profiles = record["same_clock_windows"]
        _same(set(profiles), {f"{clock}/{n}" for clock in ("09:00", "10:00") for n in (15, 30)})
        for name, window in profiles.items():
            _window(window, int(name.split("/")[1]), record)
        for clock in ("09:00", "10:00"):
            small, large = profiles[f"{clock}/15"], profiles[f"{clock}/30"]
            if small["state"] == large["state"] == "ready" and cu._ratio(small["volume_sum"]) > cu._ratio(large["volume_sum"]):
                raise OpeningVolumeRefusal("activity_nested_window_sum_disagrees")
        clock = ct.cc.session_open(day).strftime("%H:%M")
        _same(record["opening_prefixes"], {str(n): profiles[f"{clock}/{n}"] for n in (15, 30)})
        index[day, symbol] = record
    reference = source["reference_sessions"]
    positions = {day: position for position, day in enumerate(reference)}
    baselines = {key: _baseline(record, index, reference, positions[key[0]]) for key, record in index.items()}
    expected_summary = {"unique_target_days": len(records), "saved_rows": sum(row["saved_rows"] for row in records),
        "current_rows": sum(row["current_rows"] for row in records), "daily_rows": sum(row["daily_rows"] for row in records),
        "daily_field_counts": {key: dict(value) for key, value in daily_fields.items()},
        "saved_empty_current_found": 0, "saved_empty_current_empty": sum(not row["saved_rows"] for row in records),
        "saved_empty_current_empty_by_recovery_horizon": dict(sorted(Counter(row["recovery_horizon"] for row in records if not row["saved_rows"]).items())),
        "field_counts": {key: dict(value) for key, value in fields.items()}, "bucket_comparison": dict(buckets),
        "opening_prefix_ready_counts": {str(n): sum(row["opening_prefixes"][str(n)]["state"] == "ready" for row in records) for n in (15, 30)},
        "feature_ready_union": sum(value[2] for value in baselines.values())}
    _same(activity["summary"], expected_summary)
    _same(set(activity["variants"]), set(source["variants"]))
    for policy, matrix in source["source_target_matrix"].items():
        days, counts = [], Counter()
        for membership in matrix["days"]:
            targets = []
            for target in membership["targets"]:
                row = index[membership["session"], target["symbol"]]
                state = row["next_hypothesis_feature_readiness"]["state"]
                counts[state] += 1
                targets.append({"symbol": target["symbol"], "rank": target["rank"], "feature_readiness": state,
                    "opening_prefixes": {arm: row["opening_prefixes"][arm]["state"] for arm in ("15", "30")}})
            days.append({"session": membership["session"], "membership_source": {key: value for key, value in membership.items() if key != "targets"}, "targets": targets})
        _same(activity["variants"][policy], {"days": days, "feature_readiness_counts": {state: counts[state] for state in ("ready", "unknown")}})
    _same(activity["availability"], {"minute_model": "bucket_end_plus2minutes_assumed", "historical_available_at": None,
        "retrieved_at": "per_current_chunk_query_receipt", "fetched_at": "stored_collector_retrieval_not_historical_availability",
        "membership": "original_D+1_D+2_membership_source_preserved", "is_final": None,
        "absence_cause": "unknown_no_halt_delisting_or_listing_inference"})
    return index, baselines


def _feature(original, activity, baseline):
    prior, middle, ready = baseline
    day = original["session"]
    finding = original["arms"]["15"]
    available = ct.cc.session_open(day) + timedelta(minutes=17)
    if finding["status"] == "signal":
        decision = cu._instant(finding["event"]["decision_at"])
        if available > decision or any(ct.cc.session_open(past) + timedelta(minutes=17) > decision for past in prior):
            raise OpeningVolumeRefusal("noncausal_opening_volume_feature")
    status = "unknown" if finding["status"] == "unknown" else "not_applicable"
    ratio, reasons = None, []
    if finding["status"] == "signal":
        if ready:
            ratio = cu._ratio(activity["opening_prefixes"]["15"]["volume_sum"]) / middle
            status = "selected" if ratio >= 2 else "rejected"
        else:
            status, reasons = "unknown", ["opening_volume_feature_unavailable"]
    elif finding["status"] == "unknown":
        reasons = ["original_signal_unknown"]
    return {"status": status, "original_signal_status": finding["status"],
        "current_window": deepcopy(activity["opening_prefixes"]["15"]), "prior_sessions": list(prior),
        "prior20_median": middle, "opening_volume_ratio": ratio, "unknown_reasons": reasons,
        "observation_date": day, "available_at": available.isoformat(),
        "retrieved_at": activity["raw_chunk_metadata"]["retrieved_at"], "source": "collector klines",
        "is_final": None, "evidence_level": "assumed",
        "data_vintage": {"price_chunk_sha256": original["data_vintage"], "volume_chunk_sha256": activity["current_chunk_sha256"]},
        "volume_source": {key: deepcopy(activity[key]) for key in ("saved_chunk_sha256", "current_chunk_sha256",
            "current_rows_sha256", "source_fetched_at", "source_offset_counts_ms")}}


def _counts(rows):
    selection = Counter(row["feature"]["status"] for row in rows)
    original = Counter(row["original_finding"]["status"] for row in rows)
    labels = Counter(row["original_finding"]["label"]["status"] for row in rows if row["feature"]["status"] == "selected")
    return {"selection_counts": {state: selection[state] for state in STATES},
        "original_signal_counts": {state: original[state] for state in ("signal", "unknown", "known_no_signal")},
        "unknown_origin_counts": {"original_signal_unknown": original["unknown"],
            "feature_unavailable_on_original_signal": sum(row["feature"]["status"] == "unknown" and row["original_finding"]["status"] == "signal" for row in rows)},
        "label_counts": {state: labels[state] for state in ("known", "unknown")},
        "selected_plus_unknown_opportunity_upper_bound": selection["selected"] + selection["unknown"]}


def build_calibration(source: Mapping, activity: Mapping, *, checkpoint: Callable[[], None] | None = None) -> dict:
    """Validate both whole populations before selecting original OR15 events."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        cu._validate_source(source, checkpoint)
        index, baselines = _validate_activity(source, activity, checkpoint)
    except (KeyError, TypeError, ValueError, cu.OpeningRangeComparisonRefusal,
            ct.OpeningRangeRefusal, ct.cc.CalibrationRefusal) as exc:
        raise OpeningVolumeRefusal("malformed_source_calibration_or_activity") from exc
    reference, weeks = list(source["reference_sessions"]), list(source["reference_weeks"])
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "reference_sessions": reference, "reference_weeks": weeks, "variants": {},
        "source_links": {"opening_range": {"path": REGISTERED_PATHS["upstream_path"], "sha256": UPSTREAM_SHA256,
            "specification_sha256": UPSTREAM_SPECIFICATION_SHA256, "window": deepcopy(source["window"]),
            "flags": {key: source[key] for key in ct.FLAGS},
            "by_policy": {key: deepcopy(value["by_arm"]) for key, value in source["variants"].items()},
            "requires_operator_choice_before_comparison": source["requires_operator_choice_before_comparison"],
            "execution_manifest": deepcopy(source.get("execution_manifest"))},
            "activity_readiness": {"path": REGISTERED_PATHS["activity_path"], "sha256": ACTIVITY_SHA256,
                "specification_sha256": ACTIVITY_SPECIFICATION_SHA256, "window": deepcopy(activity["window"]),
                "flags": {key: activity[key] for key in cx.FLAGS}, "summary": deepcopy(activity["summary"]),
                "availability": deepcopy(activity["availability"]), "execution_manifest": deepcopy(activity.get("execution_manifest"))}}}
    choices = []
    for policy, variant in source["variants"].items():
        days = []
        for row in variant["days"]:
            checkpoint()
            key = row["session"], row["symbol"]
            days.append({"session": key[0], "symbol": key[1], "membership_target": deepcopy(row["membership_target"]),
                "membership_source": deepcopy(row["membership_source"]), "original_finding": deepcopy(row["arms"]["15"]),
                "price_source": {name: deepcopy(row[name]) for name in ("data_vintage", "input_rows", "raw_chunk_metadata", "source_fetched_at", "source_offset_counts_ms")},
                "feature": _feature(row, index[key], baselines[key])})
        records = [{"session": row["session"], "label": None if row["original_finding"]["label"]["status"] == "unknown"
            else cu._ratio(row["original_finding"]["label"]["price_change"])} for row in days if row["feature"]["status"] == "selected"]
        own = ct.cc.dispersion(records, reference)
        reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
        if own["mde_bps"] is None:
            reasons.append("undefined_own_label_sizing")
        elif own["mde_bps"] > 33:
            reasons.append("own_label_mde_exceeds_assumed_cost_floor")
        weekly = {week: _counts([row for row in days if ct._week(row["session"]) == week]) for week in weeks}
        totals = [value["selection_counts"]["selected"] for value in weekly.values()]
        summary = {**_counts(days), "by_week": weekly,
            "by_month": {month: _counts([row for row in days if row["session"][:7] == month]) for month in sorted({day[:7] for day in reference})},
            "frequency": {"reference_sessions": len(reference), "reference_weeks": len(weeks),
                "selected_per_reference_week": Fraction(len(records), len(weeks)),
                "known_labels_per_reference_week": Fraction(own["known_labels"], len(weeks)),
                "weekly_operating_objective": 15, "weeks_with_at_least15_selected": sum(value >= 15 for value in totals),
                "weekly_selected_median": Fraction(median(totals)), "weekly_selected_min": min(totals), "weekly_selected_max": max(totals),
                "selected_plus_unknown_per_reference_week_upper_bound": Fraction(sum(value["selected_plus_unknown_opportunity_upper_bound"] for value in weekly.values()), len(weeks)),
                "weeks_with_upper_bound_at_least15": sum(value["selected_plus_unknown_opportunity_upper_bound"] >= 15 for value in weekly.values()),
                "upper_bound_assumption": "each_unknown_target_at_most_one_opportunity_not_an_estimate_or_executability_certificate",
                "forced_trades": False, "executable_frequency_certified": False},
            "own_label_dispersion": own, "requires_operator_choice_before_comparison": bool(reasons), "comparison_choice_reasons": reasons}
        result["variants"][policy] = {"days": days, "by_arm": {"15": summary}}
        choices.append(bool(reasons))
    result["requires_operator_choice_before_comparison"] = any(choices)
    checkpoint()
    return result
