"""Source-bound descriptive comparison of original OR15 and fixed CY signals.

Only one supplied pinned CY report is consumed. Its immutable original remains
at its registered path; links retain its flags, summaries and choices. This
pure builder performs no IO, authentication, raw reconstruction or retuning.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date
from fractions import Fraction
import math
import re
from statistics import median

from research import intraday_opening_volume as cy
from research import intraday_opening_impulse_comparison as cw

ct, cu = cy.ct, cy.cu
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-opening-volume-comparison-v1"
MODE = "observed-label-conditional-opening-volume-descriptive-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-volume-comparison-v1.json"
WINDOW = dict(cy.WINDOW)
ARMS, GROUPS = ("15",), ("original", "selected")
UPSTREAM_SHA256 = "ab6342274a96615b5bbb7014c0142a30b528d69169d3fff4441e2b7f3acad380"
UPSTREAM_SPECIFICATION_SHA256 = "20d3b653f028cbb22fed69e6e268fa03fbb28ffc63d61204791280092ed5a10c"
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cy-opening-volume-20261023-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cz-opening-volume-comparison-20261023-v1",
    "canonical_runs_path": cy.REGISTERED_PATHS["canonical_runs_path"],
}
RESOURCES = dict(cu.RESOURCES)
PARAMETERS = {
    "axis": "original_CT_OR15_signals_versus_fixed_CY_opening_volume_subset_only",
    "source_parameters": deepcopy(cy.PARAMETERS),
    "cohort": "each_original_or_selected_group_own_known_labels_no_common_complete_mask",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "contrasts": "selected_minus_original_conditional_per_entry_means_OR15_separately_D+1_D+2",
    "uncertainty": "max_independent_arm_reference_joint_session_cluster_joint_circular_block_bootstrap",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "descriptive_interval": "normal95percent_not_confirmatory",
    "missingness_choice": "one_source_bound_observed_label_comparison_preserve_unknowns",
    "underpower_exception_applied": True,
    "output_scope": "four_conditional_group_means_two_contrasts_no_subgroups_or_winner",
}
FLAGS = {**cy.FLAGS, "calibration_only": False, "comparison_performed": True,
    "requires_operator_choice": False, "observed_label_authorization_applied": True,
    "new_performance_families": 1, "underpower_exception_applied": True}
AUTHORIZATION = {
    "operator_choice": "option1_one_limited_observed_label_descriptive_comparison_with_own_sizing_shortfall",
    "upstream_report_sha256": UPSTREAM_SHA256,
    "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
    "unknown_label_conditional_comparison_authorized": True,
    "unknown_label_groups": ["original", "selected"],
    "underpower_exception_applied": True,
    "scope": "this_CY_report_original_OR15_versus_fixed_volume_subset_only_unknown_own_labels_and_finite_over33bp_MDE_no_undefined_or_other_source_failure",
}
ASSUMPTIONS = {
    **cw.ASSUMPTIONS,
    "frequency": "observed_OR15_opportunities_not_fills15perweek_operating_fit_never_forced_trades",
    "source": "immutable_full_CY_at_pinned_path_child_retains_exact_links_flags_own_sizing_choices_manifests_no_new_raw_reconstruction",
    "power": "source_CY_finite_own_MDE_over33bp_remains_failed_source_flags_unchanged_one_new_source_bound_exception_only",
    "volume": "fixed_initial15_volume_at_least_twice_exact_prior20_same_opening_median_no_price_impulse_conjunct_units_revisions_historical_availability_unknown",
    "assessment_scope": "selected_minus_all_original_jointly_reflects_volume_threshold_selection_and_baseline_availability_calendar_name_composition_not_isolated_volume_filter_benefit",
}
_canonical, _interval, _COST = ct._canonical, cw._interval, Fraction(33, 10000)


class OpeningVolumeComparisonRefusal(RuntimeError):
    """Stable refusal codes never disclose supplied source values."""


def _same(left, right, reason="source_structure_or_linkage_changed"):
    if (left != right if isinstance(right, set) else _canonical(left) != _canonical(right)):
        raise OpeningVolumeComparisonRefusal(reason)


def validate_spec(spec: Mapping) -> None:
    _same(spec, {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}, "specification_scope_mismatch")


def _window(window, row):
    _same(set(window), {"state", "required_buckets", "missing_buckets", "conflicting_buckets",
        "invalid_volume_buckets", "volume_sum"})
    if type(window["required_buckets"]) is not int or window["required_buckets"] != 15:
        raise OpeningVolumeComparisonRefusal("source_current_window_length_changed")
    gaps = [cu._integer(window[name], 15) for name in
        ("missing_buckets", "conflicting_buckets", "invalid_volume_buckets")]
    if sum(gaps) > 15:
        raise OpeningVolumeComparisonRefusal("source_current_window_gaps_invalid")
    if window["state"] == "unknown":
        if not any(gaps) or window["volume_sum"] is not None:
            raise OpeningVolumeComparisonRefusal("source_unknown_window_changed")
    elif (window["state"] != "ready" or any(gaps) or row["price_source"]["input_rows"] < 15
            or cu._ratio(window["volume_sum"]) < 0):
        raise OpeningVolumeComparisonRefusal("source_ready_window_changed")


def _price_row(row):
    price = row["price_source"]
    _same(set(price), {"data_vintage", "input_rows", "raw_chunk_metadata", "source_fetched_at", "source_offset_counts_ms"})
    cu._integer(price["input_rows"], 600)
    if not isinstance(price["data_vintage"], str) or not re.fullmatch("[0-9a-f]{64}", price["data_vintage"]):
        raise OpeningVolumeComparisonRefusal("source_price_vintage_invalid")
    metadata = price["raw_chunk_metadata"]
    _same({key: value for key, value in metadata.items() if key != "retrieved_at"},
        {"observation_date": row["session"], "available_at": None, "is_final": None,
            "evidence_level": "assumed", "source": "collector klines"})
    cu._instant(metadata["retrieved_at"])
    offsets = price["source_offset_counts_ms"]
    if (any(not re.fullmatch(r"0|[1-9][0-9]{0,4}", str(key)) or int(key) >= 60000 for key in offsets)
            or sum(cu._integer(value, 600) for value in offsets.values()) != price["input_rows"]):
        raise OpeningVolumeComparisonRefusal("source_price_offsets_changed")
    fetched = price["source_fetched_at"]
    if not isinstance(fetched, list) or any(not isinstance(value, str) for value in fetched) or fetched != sorted(set(fetched)):
        raise OpeningVolumeComparisonRefusal("source_price_fetched_set_invalid")
    return {"session": row["session"], "symbol": row["symbol"], **price,
        "arms": {"15": row["original_finding"]}}


def _feature(row, original, index, reference, position):
    feature = row["feature"]
    _window(feature["current_window"], row)
    prior = reference[max(0, position - 20):position]
    clock = ct.cc.session_open(row["session"]).strftime("%H:%M")
    windows = [index.get((past, row["symbol"]), {}).get("feature", {}).get("current_window") for past in prior]
    middle = None
    if (len(prior) == 20 and all(window is not None and window["state"] == "ready" for window in windows)
            and all(ct.cc.session_open(past).strftime("%H:%M") == clock for past in prior)):
        values = sorted(cu._ratio(window["volume_sum"]) for window in windows)
        middle = (values[9] + values[10]) / 2
    ready = feature["current_window"]["state"] == "ready" and middle is not None and middle > 0
    volume = feature["volume_source"]
    _same(set(volume), {"saved_chunk_sha256", "current_chunk_sha256", "current_rows_sha256", "source_fetched_at", "source_offset_counts_ms"})
    for key in ("saved_chunk_sha256", "current_chunk_sha256", "current_rows_sha256"):
        if not isinstance(volume[key], str) or not re.fullmatch("[0-9a-f]{64}", volume[key]):
            raise OpeningVolumeComparisonRefusal("source_volume_vintage_invalid")
    _same(volume["saved_chunk_sha256"], original["data_vintage"])
    for name in ("source_fetched_at", "source_offset_counts_ms"):
        _same(volume[name], original[name])
    cu._instant(feature["retrieved_at"])
    activity = {"opening_prefixes": {"15": feature["current_window"]},
        "raw_chunk_metadata": {"retrieved_at": feature["retrieved_at"]}, **volume}
    expected = cy._feature(original, activity, (prior, middle, ready))
    _same(feature, expected, "source_feature_or_metadata_changed")


def _selected_summary(rows, records, reference, weeks):
    own = ct.cc.dispersion(records, reference)
    if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
        raise OpeningVolumeComparisonRefusal("undefined_source_own_sizing_not_authorized")
    reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
    if own["mde_bps"] > 33:
        reasons.append("own_label_mde_exceeds_assumed_cost_floor")
    weekly = {week: cy._counts([row for row in rows if ct._week(row["session"]) == week]) for week in weeks}
    totals = [value["selection_counts"]["selected"] for value in weekly.values()]
    return {**cy._counts(rows), "by_week": weekly,
        "by_month": {month: cy._counts([row for row in rows if row["session"][:7] == month])
            for month in sorted({day[:7] for day in reference})},
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


def _validate_source(source, checkpoint):
    if source["study_id"] != cy.SPECIFICATION_ID or source["mode"] != cy.MODE:
        raise OpeningVolumeComparisonRefusal("wrong_source_study_or_mode")
    for name, expected in (("window", cy.WINDOW), ("parameters", cy.PARAMETERS), ("assumptions", cy.ASSUMPTIONS)):
        _same(source[name], expected, "source_scope_changed")
    for name, expected in cy.FLAGS.items():
        if source.get(name) is not expected:
            raise OpeningVolumeComparisonRefusal("source_certification_or_mode_flag_changed")
    if source.get("specification_sha256", UPSTREAM_SPECIFICATION_SHA256) != UPSTREAM_SPECIFICATION_SHA256:
        raise OpeningVolumeComparisonRefusal("source_specification_changed")
    for name, expected in (("upstream_sha256", cy.UPSTREAM_SHA256),
            ("upstream_specification_sha256", cy.UPSTREAM_SPECIFICATION_SHA256),
            ("activity_sha256", cy.ACTIVITY_SHA256),
            ("activity_specification_sha256", cy.ACTIVITY_SPECIFICATION_SHA256)):
        if name in source:
            _same(source[name], expected, "source_parent_pin_changed")
    reference = source["reference_sessions"]
    if (not isinstance(reference, list) or len(reference) < 2 or reference != sorted(set(reference))
            or any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or not WINDOW["start"] <= day <= WINDOW["end"] for day in reference)):
        raise OpeningVolumeComparisonRefusal("invalid_source_reference_sessions")
    weeks = sorted({ct._week(day) for day in reference})
    _same(source["reference_weeks"], weeks)
    links = source["source_links"]
    _same(set(links), {"opening_range", "activity_readiness"})
    for name, path, digest, spec, flags in (
            ("opening_range", cy.REGISTERED_PATHS["upstream_path"], cy.UPSTREAM_SHA256, cy.UPSTREAM_SPECIFICATION_SHA256, ct.FLAGS),
            ("activity_readiness", cy.REGISTERED_PATHS["activity_path"], cy.ACTIVITY_SHA256, cy.ACTIVITY_SPECIFICATION_SHA256, cy.cx.FLAGS)):
        link = links[name]
        for key, expected in (("path", path), ("sha256", digest), ("specification_sha256", spec), ("window", WINDOW), ("flags", flags)):
            _same(link[key], expected, "source_parent_link_changed")
    _same(set(source["variants"]), {"D+1", "D+2"})
    _same(set(links["opening_range"]["by_policy"]), {"D+1", "D+2"})
    index, originals, records, choices = {}, {}, {}, []
    positions = {day: index for index, day in enumerate(reference)}
    # Validate every row's primitives before using any prior current window.
    for policy, variant in source["variants"].items():
        _same(set(variant), {"days", "by_arm"})
        _same(set(variant["by_arm"]), {"15"})
        by_day = {day: [] for day in reference}
        originals[policy] = []
        for row in variant["days"]:
            checkpoint()
            _same(set(row), {"session", "symbol", "membership_target", "membership_source", "original_finding", "price_source", "feature"})
            day, symbol = row["session"], row["symbol"]
            if day not in by_day or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol):
                raise OpeningVolumeComparisonRefusal("source_target_identity_changed")
            target, member = row["membership_target"], row["membership_source"]
            if target["symbol"] != symbol or member["session"] != day or not cu._instant(member["available_at"]) <= cu._instant(member["selected_at"]) < ct.cc.session_open(day):
                raise OpeningVolumeComparisonRefusal("source_membership_changed")
            if by_day[day]:
                _same(member, by_day[day][0]["membership_source"])
            by_day[day].append(row)
            original = _price_row(row)
            cu._finding(original, "15")
            _window(row["feature"]["current_window"], row)
            originals[policy].append(original)
            physical = {key: value for key, value in row.items() if key not in ("membership_target", "membership_source")}
            if (day, symbol) in index:
                previous = {key: value for key, value in index[day, symbol].items() if key not in ("membership_target", "membership_source")}
                _same(physical, previous, "source_cross_policy_rows_disagree")
            else:
                index[day, symbol] = row
        expected_order = []
        for day, rows in by_day.items():
            symbols = [row["symbol"] for row in rows]
            if (not 1 <= len(rows) <= 20 or len(set(symbols)) != len(rows)
                    or any(type(row["membership_target"]["rank"]) is not int for row in rows)
                    or [row["membership_target"]["rank"] for row in rows] != list(range(1, len(rows) + 1))):
                raise OpeningVolumeComparisonRefusal("source_target_population_changed")
            expected_order.extend((day, row["symbol"]) for row in rows)
        _same([(row["session"], row["symbol"]) for row in variant["days"]], expected_order)
    for policy, variant in source["variants"].items():
        groups = {group: [] for group in GROUPS}
        for row, original in zip(variant["days"], originals[policy], strict=True):
            checkpoint()
            _feature(row, original, index, reference, positions[row["session"]])
            if row["original_finding"]["status"] == "signal":
                label = row["original_finding"]["label"]
                record = {"session": row["session"], "label": None if label["status"] == "unknown" else cu._ratio(label["price_change"])}
                groups["original"].append(record)
                if row["feature"]["status"] == "selected":
                    groups["selected"].append(dict(record))
        expected = _selected_summary(variant["days"], groups["selected"], reference, weeks)
        _same(variant["by_arm"]["15"], expected, "source_selected_summary_or_own_sizing_changed")
        choices.append(expected["requires_operator_choice_before_comparison"])
        summary = links["opening_range"]["by_policy"][policy]["15"]
        own = ct.cc.dispersion(groups["original"], reference)
        if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]) or own["mde_bps"] > 33:
            raise OpeningVolumeComparisonRefusal("original_source_sizing_failure_not_authorized")
        weekly = {week: ct._counts([row for row in originals[policy] if ct._week(row["session"]) == week], "15") for week in weeks}
        reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
        expected_original = {**ct._counts(originals[policy], "15"), "by_week": weekly,
            "by_month": {month: ct._counts([row for row in originals[policy] if row["session"][:7] == month], "15") for month in sorted({day[:7] for day in reference})},
            "frequency": {"reference_sessions": len(reference), "reference_weeks": len(weeks),
                "signals_per_reference_week": Fraction(len(groups["original"]), len(weeks)),
                "known_labels_per_reference_week": Fraction(own["known_labels"], len(weeks)),
                "weekly_operating_objective": 15, "weeks_with_at_least15_signals": sum(value["signal_counts"]["signal"] >= 15 for value in weekly.values()),
                "forced_trades": False, "executable_frequency_certified": False},
            "own_label_dispersion": own, "requires_operator_choice_before_comparison": bool(reasons), "comparison_choice_reasons": reasons}
        _same(summary, expected_original, "source_original_summary_or_own_sizing_changed")
        records[policy] = groups
    if source["requires_operator_choice_before_comparison"] is not any(choices):
        raise OpeningVolumeComparisonRefusal("source_choice_flag_changed")
    # OR30 remains immutable linked provenance, never a new analysis group.
    old_choices = [summary["requires_operator_choice_before_comparison"]
        for by_arm in links["opening_range"]["by_policy"].values() for summary in by_arm.values()]
    if links["opening_range"]["requires_operator_choice_before_comparison"] is not any(old_choices):
        raise OpeningVolumeComparisonRefusal("source_original_choice_flag_changed")
    # Linked OR30 summaries are retained historical evidence, never a new arm.
    # Their raw source is intentionally unavailable here; no new waiver applies.
    for by_arm in links["opening_range"]["by_policy"].values():
        _same(set(by_arm), {"15", "30"})
        old = by_arm["30"]
        own = old["own_label_dispersion"]
        if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]) or own["mde_bps"] > 33:
            raise OpeningVolumeComparisonRefusal("other_source_sizing_failure_not_authorized")
        reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
        _same(old["comparison_choice_reasons"], reasons)
        if old["requires_operator_choice_before_comparison"] is not bool(reasons):
            raise OpeningVolumeComparisonRefusal("source_original_choice_flag_changed")
    activity = links["activity_readiness"]
    _same(activity["availability"], {"minute_model": "bucket_end_plus2minutes_assumed", "historical_available_at": None,
        "retrieved_at": "per_current_chunk_query_receipt", "fetched_at": "stored_collector_retrieval_not_historical_availability",
        "membership": "original_D+1_D+2_membership_source_preserved", "is_final": None,
        "absence_cause": "unknown_no_halt_delisting_or_listing_inference"})
    summary = activity["summary"]
    for name, expected in (("unique_target_days", len(index)),
            ("saved_rows", sum(row["price_source"]["input_rows"] for row in index.values())),
            ("current_rows", sum(row["price_source"]["input_rows"] for row in index.values())),
            ("saved_empty_current_found", 0),
            ("saved_empty_current_empty", sum(not row["price_source"]["input_rows"] for row in index.values()))):
        _same(summary[name], expected, "source_activity_summary_changed")
    _same(summary["opening_prefix_ready_counts"]["15"], sum(row["feature"]["current_window"]["state"] == "ready"
        for row in index.values()), "source_activity_summary_changed")
    return records


def build_comparison(source: Mapping, *, checkpoint: Callable[[], None] | None = None) -> dict:
    """Validate supplied CY before four own-label means and two contrasts.

    Small invented grids are supported. Actual full geometry, source bytes,
    immutable evidence, logging and the one shared budget are caller guards.
    """
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        records = _validate_source(source, checkpoint)
    except (KeyError, TypeError, ValueError, cy.OpeningVolumeRefusal,
            cu.OpeningRangeComparisonRefusal, ct.OpeningRangeRefusal, ct.cc.CalibrationRefusal) as exc:
        raise OpeningVolumeComparisonRefusal("malformed_source_calibration") from exc
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "operator_authorization": dict(AUTHORIZATION), "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "source_links": {"opening_volume": {"path": REGISTERED_PATHS["upstream_path"], "sha256": UPSTREAM_SHA256,
            "specification_sha256": UPSTREAM_SPECIFICATION_SHA256, "window": deepcopy(source["window"]),
            "flags": {key: source[key] for key in cy.FLAGS},
            "by_policy": {key: deepcopy(value["by_arm"]) for key, value in source["variants"].items()},
            "requires_operator_choice_before_comparison": source["requires_operator_choice_before_comparison"],
            "source_links": deepcopy(source["source_links"]), "execution_manifest": deepcopy(source.get("execution_manifest"))}},
        "reference_sessions": list(source["reference_sessions"]), "reference_weeks": list(source["reference_weeks"]), "variants": {}}
    for policy, group_records in records.items():
        groups, means = {}, {}
        for group in GROUPS:
            checkpoint()
            values = [row["label"] for row in group_records[group] if row["label"] is not None]
            n, total = len(values), sum(values, Fraction(0))
            mean = total / n
            summary = (source["variants"][policy]["by_arm"]["15"] if group == "selected"
                else source["source_links"]["opening_range"]["by_policy"][policy]["15"])
            own = deepcopy(summary["own_label_dispersion"])
            positive, net_positive = sum(value > 0 for value in values), sum(value > _COST for value in values)
            groups[group] = {"known_labels": n, "event_count": len(group_records[group]),
                "unknown_labels": len(group_records[group]) - n, "gross_sum": total, "gross_mean": mean,
                "net_mean": mean - _COST, "assumed_cost": _COST,
                "gross_positive_count": positive, "gross_positive_fraction": Fraction(positive, n),
                "net_positive_count": net_positive, "net_positive_fraction": Fraction(net_positive, n),
                "source_own_label_dispersion": own, "frequency": deepcopy(summary["frequency"]),
                "descriptive_normal95_interval": _interval(mean, own["design_se"])}
            means[group] = mean
        try:
            uncertainty = cw._uncertainty(group_records, result["reference_sessions"], means, checkpoint)
        except cw.OpeningImpulseComparisonRefusal as exc:
            raise OpeningVolumeComparisonRefusal("invalid_joint_uncertainty") from exc
        difference = means["selected"] - means["original"]
        result["variants"][policy] = {"by_arm": {"15": {"groups": groups, "selected_minus_original": {
            "gross_mean_difference": difference, "net_mean_difference": difference,
            "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
            "descriptive_normal95_interval": _interval(difference, uncertainty["design_se"])}}}}
    checkpoint()
    return result
