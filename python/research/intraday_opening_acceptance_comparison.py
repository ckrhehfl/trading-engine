"""Fresh comparison of original CT15 and DU observations, without replay.

The caller authenticates completed source bytes and projects each source before
decoding the next. Only frozen states, original clocks and labels are joined.
Source statistics are opaque receipts; all four current books are freshly sized.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date
from fractions import Fraction
import hashlib
import math
import re
from statistics import median

from research import intraday_opening_acceptance_calibration as du
from research import intraday_opening_range_comparison as cu
from research import intraday_breakout_close_comparison as dn


ct, cc, dm, dl = du.ct, du.cc, dn.dm, dn.dl
ROOT, WINDOW = ct.ROOT, dict(ct.WINDOW)
POLICIES, ARMS = ("D+1", "D+2"), ("basic", "selected")
SPECIFICATION_ID = "intraday-opening-acceptance-comparison-v1"
MODE = "fixed-observed-basic-versus-next-bar-acceptance-conditional-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-acceptance-comparison-v1.json"
OUTPUT_NAME = "intraday-dx-opening-acceptance-comparison-20261026-v1"
CT_SHA256 = "b25690b7cc87278ebe1baf9f20d6f0185c7d759c93b90e7488b89b53383aa875"
CT_SPECIFICATION_SHA256 = "1578dbf61fdbbdb620218f1292da412ec99e8eff3ce1d8c67870bcce304b56bf"
DU_SHA256 = "4f928e97e3ee0be521c96115cbed28618df003027e3050823ab3f409a1933796"
DU_SPECIFICATION_SHA256 = "ee6f2d36773972d3371b7a3030b19494ad93783452cc95846f2e1431b7172e88"
UPSTREAM_SHA256 = {"CT": CT_SHA256, "DU": DU_SHA256}
UPSTREAM_SPECIFICATION_SHA256 = {"CT": CT_SPECIFICATION_SHA256, "DU": DU_SPECIFICATION_SHA256}
SOURCE_ROLES = {"CT": "pinned_completed_original_CT_calibration_report_arm15_only",
    "DU": "pinned_completed_original_DU_acceptance_calibration_report"}
COST = Fraction(33, 10000)
EXPECTED_GEOMETRY = {"reference_sessions": 257, "reference_weeks": 56,
    "targets_per_policy": 5140, "original_DU_known_labels": {"D+1": 807, "D+2": 804},
    "original_DU_unknown_labels": {"D+1": 5, "D+2": 5},
    "common_scope_counts": "computed_from_original_CT15_label_intersection_not_assumed807804"}
RESOURCES = {"maximum_source_bytes": 67108864, "minimum_free_bytes": 1073741824,
    "address_space_mib": 512, "cpu_seconds": 300, "wall_seconds": 1200,
    "nice": 10, "automatic_retries": 0, "maximum_market_gets": 0, "maximum_token_posts": 0}
PARAMETERS = {
    "cohort": "all_original_CT_and_DU_targets_joined_by_policy_session_symbol_original_vintage_and_first_B",
    "basic": "acceptance_feature_observable_accepted_plus_rejected_CT15_label_known_and_if_accepted_DU_label_known",
    "selected": "accepted_subset_of_same_common_basic_scope_with_original_newly_timed_DU_label",
    "unknowns": "preserve_all_source_feature_base_label_and_accepted_label_unknowns_and_intersection_losses_no_imputation",
    "basic_clock": "B.end_plus2minutes_decision_strict_next5minute_OPEN_then_own_entry_plus30minute_OPEN",
    "selected_clock": "W.end_plus2minutes_decision_strict_next5minute_OPEN_then_own_entry_plus30minute_OPEN",
    "effect": "combined_fixed_selection_and_five_minute_delay_policy_not_isolated_causal_filter_effect",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "sizing": "four_fresh_current_own_labels_naive_whole_session_cluster_circular5session2000drawseed0_maxSE_two_sided5percent80percent_MDE",
    "comparison_gate": "all_four_own_MDE_defined_finite_lte33bp_no_underpower_waiver",
    "weekly_operating_objective": 15,
    "frequency": "all56_reference_weeks_including_zero_opportunities_not_executable_account_trades",
    "performance": dn.PARAMETERS["performance"], "contrast": dn.PARAMETERS["contrast"],
    "uncertainty": dn.PARAMETERS["uncertainty"],
    "descriptive_interval": dn.PARAMETERS["descriptive_interval"],
    "output_scope": "one_conditional_family_four_arm_means_two_policy_contrasts_no_subgroup_returns_or_promotion",
    "expected_actual_geometry": EXPECTED_GEOMETRY,
}
ASSUMPTIONS = {
    "source": "caller_authenticated_original_CT_and_DU_only_no_detector_label_archive_DB_API_or_ancestor_statistic_replay",
    "identity": "all_dated_targets_membership_source_vintages_causal_first_B_and_original_labels_crossmatched",
    "missingness": "original_unknowns_retained_excluded_not_filled_both_common_known_outcome_conditioning_is_retrospective_selection_bias",
    "timing": "basic_enters_five_minutes_before_acceptance_combined_selection_and_delay_effect_not_causal_filter_isolation",
    "availability": "original_assumed_two_minute_observation_lag_strict_next5minute_OPEN_three_minute_wait_no_new_historical_publication_fact",
    "planning": dn.ASSUMPTIONS["planning"], "uncertainty": dn.ASSUMPTIONS["uncertainty"],
    "measurement": dn.ASSUMPTIONS["measurement"], "policies": dn.ASSUMPTIONS["policies"],
    "claims": dn.ASSUMPTIONS["claims"],
}
FLAGS = {**ct.FLAGS, "calibration_only": False, "comparison_performed": False,
    "new_performance_families": 0, "new_signals_calculated": False,
    "new_labels_calculated": False, "new_selection_calculated": False,
    "upstream_statistics_recalculated": False, "raw_replay_performed": False,
    "subgroup_return_means_calculated": False}
STATES = ("basic_unknown", "basic_no_signal", "feature_unknown", "accepted", "rejected")
_canonical, _digest = dl._canonical, dl._digest
_performance, _uncertainty, _interval = dn._performance, dn._uncertainty, dn._interval
_META_EXCLUDED = {"session", "symbol", "arms", "event_timing_state", "membership_target", "membership_source"}


class OpeningAcceptanceComparisonRefusal(RuntimeError):
    """Stable refusal codes exclude private source values."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise OpeningAcceptanceComparisonRefusal(reason)


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


def _label(label: Mapping, decision: object, day: str) -> Fraction | None:
    decision = dl._instant(decision)
    entry, cap = (dl._instant(label[key]) for key in ("entry_at", "exit_at"))
    opening = cc.session_open(day)
    _require(entry == decision + 3 * cc.MINUTE and (entry - opening) % cc.FIVE == cc.MINUTE * 0
        and cap == entry + 30 * cc.MINUTE and cap < cc.session_close(day)
        and dl._instant(label["time_cap_decided_at"]) == decision
        and dl._instant(label["entry_price_available_at"]) == entry + 3 * cc.MINUTE
        and dl._instant(label["label_available_at"]) == cap + 3 * cc.MINUTE
        and label["required_minute_buckets"] == 31 and label["evidence_level"] == "assumed"
        and label["actual_fill_certified"] is False, "source_label_clock_or_rule_changed")
    unknown, gaps = label["unknown_minute_buckets"], label["unknown_bucket_reason_counts"]
    _require(type(unknown) is int and 0 <= unknown <= 31 and set(gaps) == set(ct.GAP_STATES)
        and all(type(value) is int and 0 <= value <= 31 for value in gaps.values())
        and sum(gaps.values()) == unknown, "source_label_unknown_counts_changed")
    _same(label["unknown_reasons"], [f"label_{state}_minute_buckets" for state in ct.GAP_STATES if gaps[state]],
        "source_label_unknown_reasons_changed")
    for key in ("entry_open", "exit_open"):
        if label[key] is not None:
            dl._price(label[key])
    if label["status"] == "unknown":
        _require(unknown > 0 and label["price_change"] is None, "source_unknown_label_changed")
        return None
    _require(label["status"] == "known" and unknown == 0, "source_known_label_changed")
    value = dl._ratio(label["price_change"])
    _require(value == dl._price(label["exit_open"]) / dl._price(label["entry_open"]) - 1,
        "source_label_exact_price_ratio_changed")
    return value


def _project(source: Mapping, role: str, checkpoint: Callable[[], None]) -> dict:
    model, arm = (ct, "15") if role == "CT" else (du, "15_acceptance")
    _require(source["study_id"] == model.SPECIFICATION_ID and source["mode"] == model.MODE
        and source.get("specification_sha256") == UPSTREAM_SPECIFICATION_SHA256[role],
        "source_identity_or_specification_changed")
    for key, expected in (("window", WINDOW), ("parameters", model.PARAMETERS), ("assumptions", model.ASSUMPTIONS)):
        _same(source[key], expected, "source_scope_changed")
    for key, value in model.FLAGS.items():
        _require(type(source.get(key)) is type(value) and source[key] == value, "source_claim_changed")
    reference = source["reference_sessions"]
    _require(isinstance(reference, list) and len(reference) >= 2 and reference == sorted(set(reference))
        and all(isinstance(day, str) and date.fromisoformat(day).isoformat() == day
            and WINDOW["start"] <= day <= WINDOW["end"] for day in reference), "source_calendar_changed")
    weeks = sorted({ct._week(day) for day in reference})
    _same(source["reference_weeks"], weeks, "source_reference_weeks_changed")
    _same([row["date"] for row in source["upstream_calendar"]["rows"]
        if WINDOW["start"] <= row["date"] <= WINDOW["end"]], reference, "source_calendar_changed")
    _require(set(source["variants"]) == set(source["source_target_matrix"]) == set(POLICIES),
        "source_policy_population_changed")
    evidence = source["input_evidence"]
    for key, expected in (("upstream_report_sha256", ct.UPSTREAM_SHA256),
            ("compressed_sha256", ct.INPUT_COMPRESSED_SHA256), ("stream_sha256", ct.INPUT_STREAM_SHA256),
            ("pin_authentication", "caller_obligation_not_proven_by_pure_builder")):
        _require(evidence[key] == expected, "source_original_archive_lineage_changed")
    variants = {}
    for policy in POLICIES:
        checkpoint()
        original = source["variants"][policy]
        _require("comparison" not in original, "source_is_not_original_calibration")
        memberships = source["source_target_matrix"][policy]["days"]
        _same([member["session"] for member in memberships], reference, "source_target_calendar_changed")
        rows, expected = original["days"], []
        for membership in memberships:
            day, targets = membership["session"], membership["targets"]
            _require(dl._instant(membership["available_at"]) <= dl._instant(membership["selected_at"])
                < cc.session_open(day) and isinstance(targets, list) and 1 <= len(targets) <= 20
                and len({member["symbol"] for member in targets}) == len(targets)
                and all(type(member["rank"]) is int for member in targets)
                and {member["rank"] for member in targets} == set(range(1, len(targets) + 1)),
                "source_dated_membership_changed")
            provenance = {key: value for key, value in membership.items() if key != "targets"}
            expected.extend((day, member, provenance) for member in targets)
        _require(len(rows) == len(expected), "source_target_population_changed")
        targets, counts = [], Counter()
        for row, (day, member, provenance) in zip(rows, expected, strict=True):
            checkpoint()
            _require(row["session"] == day and row["symbol"] == member["symbol"]
                and re.fullmatch(r"KRX:[0-9A-Z]{6}", row["symbol"]) is not None,
                "source_target_identity_changed")
            _same(row["membership_target"], member, "source_membership_target_changed")
            _same(row["membership_source"], provenance, "source_membership_source_changed")
            _require(isinstance(row["data_vintage"], str) and dl._SHA.fullmatch(row["data_vintage"]) is not None,
                "source_vintage_changed")
            metadata = row["raw_chunk_metadata"]
            _require(metadata["observation_date"] == day and metadata["available_at"] is None
                and metadata["is_final"] is None and metadata["evidence_level"] == "assumed"
                and metadata["source"] == "collector klines", "source_metadata_changed")
            dl._instant(metadata["retrieved_at"])
            _require(type(row["input_rows"]) is int and 0 <= row["input_rows"] <= 600,
                "source_input_row_count_changed")
            finding = row["arms"][arm]
            status = finding["status"]
            _require(status in ct.STATES and finding["required_bars"] == 47
                and type(finding["valid_prefix_bars"]) is int and 0 <= finding["valid_prefix_bars"] <= 47
                and row["input_rows"] >= finding["valid_prefix_bars"] * 5,
                "source_signal_state_changed")
            # CT's reviewed validator inspects arm15 only. No CT30 calculation,
            # detector, source calibration or old sizing pipeline is invoked.
            if role == "CT":
                cu._finding(row, "15")
            else:
                _acceptance_finding(finding, row)
            counts[status] += 1
            if finding["label"] is not None:
                counts["label_" + finding["label"]["status"]] += 1
            targets.append({"session": day, "symbol": row["symbol"], "data_vintage": row["data_vintage"],
                "membership_sha256": _digest({"target": member, "source": provenance}, checkpoint),
                "source_row_sha256": _digest(row, checkpoint),
                "source_metadata_sha256": _digest({key: value for key, value in row.items()
                    if key not in _META_EXCLUDED}, checkpoint),
                "status": status, "event": deepcopy(finding["event"]), "label": deepcopy(finding["label"]),
                "path": deepcopy(finding.get("path")), "unknown_reasons": list(finding["unknown_reasons"]),
                "known_no_signal_reason": finding.get("known_no_signal_reason")})
        summary = original["by_arm"][arm]
        _same(summary["target_symbol_sessions"], len(rows), "source_saved_counts_changed")
        _same(summary["signal_counts"], {state: counts[state] for state in ct.STATES}, "source_saved_counts_changed")
        _same(summary["label_counts"], {state: counts["label_" + state] for state in ("known", "unknown")},
            "source_saved_counts_changed")
        variants[policy] = {"targets": targets, "source_calibration_receipts": deepcopy(summary)}
    return {"source_identity": {"role": role, "report_sha256": UPSTREAM_SHA256[role],
        "specification_sha256": UPSTREAM_SPECIFICATION_SHA256[role],
        "header_sha256": _digest({key: value for key, value in source.items()
            if key not in {"variants", "source_target_matrix", "upstream_calendar"}}, checkpoint),
        "matrix_sha256": _digest(source["source_target_matrix"], checkpoint),
        "calendar_sha256": _digest(source["upstream_calendar"], checkpoint)},
        "reference_sessions": list(reference), "reference_weeks": weeks,
        "source_target_matrix": deepcopy(source["source_target_matrix"]),
        "upstream_calendar": deepcopy(source["upstream_calendar"]), "variants": variants}


def _acceptance_finding(finding: Mapping, row: Mapping) -> None:
    status, event, label, path = (finding[key] for key in ("status", "event", "label", "path"))
    gaps, prefix = finding["decision_prefix_unknown_bucket_counts"], finding["valid_prefix_bars"]
    _require(set(gaps) == set(ct.GAP_STATES)
        and all(type(value) is int and 0 <= value <= 235 for value in gaps.values()),
        "acceptance_prefix_gaps_changed")
    if status != "signal":
        _require(event is None and label is None, "acceptance_nonsignal_has_label")
        if status == "unknown":
            expected = (["no_saved_rows_unknown_cause"] if not row["input_rows"] else
                [f"decision_prefix_{state}_minute_buckets" for state in ct.GAP_STATES if gaps[state]])
            _require(bool(expected) and finding["unknown_reasons"] == expected and prefix < 47
                and finding["known_no_signal_reason"] is None,
                "acceptance_unknown_reason_changed")
        else:
            _require(not finding["unknown_reasons"] and finding["known_no_signal_reason"] in {
                "next_scheduled_close_at_or_below_range_high", "confirmation_after_cutoff", "no_breakout_before_cutoff"},
                "acceptance_no_signal_reason_changed")
        return
    decision, end = (dl._instant(event[key]) for key in ("decision_at", "signal_bar_end"))
    _require(event["family"] == "opening_range15_two_close_acceptance" and event["range_minutes"] == 15
        and decision == end + 2 * cc.MINUTE and dl._instant(event["available_at"]) == decision
        and decision <= cc.session_open(row["session"]) + 240 * cc.MINUTE
        and event["data_vintage"] == row["data_vintage"] and event["observation_date"] == row["session"]
        and event["retrieved_at"] == row["raw_chunk_metadata"]["retrieved_at"]
        and event["evidence_level"] == "assumed" and event["is_final"] is None
        and event["source"] == "collector klines; assumed completed five-minute bars"
        and event["parameters_sha256"] == hashlib.sha256(_canonical(du.PARAMETERS)).hexdigest()
        and prefix >= (end - cc.session_open(row["session"])) // cc.FIVE
        and not finding["unknown_reasons"] and finding["known_no_signal_reason"] is None,
        "acceptance_event_clock_or_provenance_changed")
    _same(event["breakout_bar"], path["breakout_bar"], "acceptance_first_B_changed")
    _same(event["confirmation_bar"], path["confirmation_bar"], "acceptance_W_changed")
    _require(path["invalidation_bar"] is None and path["invalidation_reason"] is None,
        "accepted_event_has_invalidation")
    _label(label, decision, row["session"])


def _project_checked(source: Mapping, role: str, checkpoint: Callable[[], None] | None) -> dict:
    try:
        return _project(source, role, checkpoint or (lambda: None))
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError,
            cu.OpeningRangeComparisonRefusal, dl.BreakoutLossConcentrationRefusal) as exc:
        raise OpeningAcceptanceComparisonRefusal("malformed_or_mismatched_original_source") from exc


def project_ct(source: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Project original CT arm15; unused arm30 remains opaque provenance."""
    return _project_checked(source, "CT", checkpoint)


def project_du(source: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Project frozen DU states and original acceptance labels, without replay."""
    return _project_checked(source, "DU", checkpoint)


def _bar(bar: Mapping, opening: object) -> None:
    start, end, available = (dl._instant(bar[key]) for key in ("start_at", "end_at", "available_at"))
    _require(start >= opening and (start - opening) % cc.FIVE == cc.MINUTE * 0
        and end == start + cc.FIVE and available == end + 2 * cc.MINUTE, "original_bar_clock_changed")
    low, high = dl._price(bar["low"]), dl._price(bar["high"])
    _require(low <= min(dl._price(bar["open"]), dl._price(bar["close"]))
        <= max(dl._price(bar["open"]), dl._price(bar["close"])) <= high, "original_bar_OHLC_changed")


def _state(basic: Mapping, selected: Mapping) -> str:
    if basic["status"] == "unknown":
        _require(selected["status"] == "unknown" and selected["path"]["breakout_bar"] is None,
            "unknown_basic_has_acceptance_decision")
        return "basic_unknown"
    if basic["status"] == "known_no_signal":
        _require(selected["status"] == "known_no_signal"
            and selected["known_no_signal_reason"] == "no_breakout_before_cutoff"
            and selected["path"]["breakout_bar"] is None, "no_basic_has_acceptance_decision")
        return "basic_no_signal"
    event, path = basic["event"], selected["path"]
    opening = cc.session_open(basic["session"])
    breakout = path["breakout_bar"]
    _require(isinstance(breakout, Mapping), "original_first_B_missing")
    _bar(breakout, opening)
    for left, right in ((breakout["start_at"], event["signal_bar_start"]),
            (breakout["end_at"], event["signal_bar_end"]), (breakout["available_at"], event["decision_at"])):
        _require(dl._instant(left) == dl._instant(right), "original_first_B_clock_changed")
    level = max(dl._price(bar["high"]) for bar in event["context"])
    _require(dl._price(path["range_high"]) == level and dl._price(breakout["close"]) > level,
        "original_first_B_range_or_price_changed")
    if selected["status"] == "unknown":
        _require(path["confirmation_bar"] is None and path["invalidation_bar"] is None,
            "unknown_acceptance_has_known_W")
        return "feature_unknown"
    confirmation = path["confirmation_bar"]
    if selected["status"] == "known_no_signal" and selected["known_no_signal_reason"] == "confirmation_after_cutoff":
        _require(confirmation is None and dl._instant(breakout["end_at"]) + cc.FIVE + 2 * cc.MINUTE
            > opening + 240 * cc.MINUTE, "acceptance_cutoff_changed")
        return "rejected"
    _require(isinstance(confirmation, Mapping), "observed_acceptance_W_missing")
    _bar(confirmation, opening)
    _require(dl._instant(confirmation["start_at"]) == dl._instant(breakout["end_at"]),
        "acceptance_not_exact_next_scheduled_W")
    if selected["status"] == "known_no_signal":
        _require(selected["known_no_signal_reason"] == "next_scheduled_close_at_or_below_range_high"
            and dl._price(confirmation["close"]) <= level, "frozen_rejection_changed")
        _same(path["invalidation_bar"], confirmation, "frozen_invalidation_changed")
        return "rejected"
    accepted = selected["event"]
    _same(accepted["context"], event["context"], "original_range_context_changed")
    _require(dl._price(accepted["range_high"]) == level and dl._price(confirmation["close"]) > level
        and dl._instant(accepted["signal_bar_start"]) == dl._instant(confirmation["start_at"])
        and dl._instant(accepted["signal_bar_end"]) == dl._instant(confirmation["end_at"])
        and dl._instant(accepted["decision_at"]) == dl._instant(event["decision_at"]) + cc.FIVE
        and dl._instant(selected["label"]["entry_at"]) == dl._instant(basic["label"]["entry_at"]) + cc.FIVE,
        "combined_acceptance_five_minute_delay_changed")
    return "accepted"


def _cell() -> dict:
    return {"targets": 0, "state_counts": dict.fromkeys(STATES, 0),
        "CT_signal_counts": dict.fromkeys(ct.STATES, 0), "DU_signal_counts": dict.fromkeys(ct.STATES, 0),
        "CT_label_counts": {"known": 0, "unknown": 0}, "DU_label_counts": {"known": 0, "unknown": 0},
        "feature_observable_basic_signals": 0, "base_label_unknown_on_feature_scope": 0,
        "accepted_label_unknown_on_feature_scope": 0, "accepted_known_lost_to_base_label_unknown": 0,
        "excluded_basic_signal_targets": 0, "common_basic_events": 0, "common_selected_events": 0}


def _frequency(records: list[dict], weeks: list[str], denominator: int) -> tuple[dict, dict]:
    weekly = dict.fromkeys(weeks, 0)
    for row in records:
        weekly[ct._week(row["session"])] += 1
    counts = list(weekly.values())
    return {"reference_weeks": len(weeks), "events": len(records), "known_outcomes": len(records),
        "distinct_dates": len({row["session"] for row in records}),
        "mean_per_all_reference_weeks": Fraction(len(records), len(weeks)),
        "known_outcomes_per_all_reference_weeks": Fraction(len(records), len(weeks)),
        "weekly_min": min(counts), "weekly_median": median(counts), "weekly_max": max(counts),
        "weeks_at_least15": sum(count >= 15 for count in counts), "zero_event_weeks": counts.count(0),
        "retained_opportunity_fraction": Fraction(len(records), denominator) if denominator else None,
        "opportunities_are_fills_or_account_entries": False}, weekly


def _choice(own: Mapping) -> list[str]:
    mde = own["mde_bps"]
    if isinstance(mde, bool) or not isinstance(mde, (int, float)) or not math.isfinite(mde):
        return ["own_label_mde_undefined_or_nonfinite"]
    return ["own_label_mde_exceeds_assumed_cost_floor"] if mde < 0 or mde > 33 else []


def _variant(ct_projection: Mapping, du_projection: Mapping, policy: str,
        checkpoint: Callable[[], None]) -> tuple[dict, dict]:
    first, second = (projection["variants"][policy] for projection in (ct_projection, du_projection))
    reference, weeks = ct_projection["reference_sessions"], ct_projection["reference_weeks"]
    _require(len(first["targets"]) == len(second["targets"]), "original_target_population_disagrees")
    flow, states, records = _cell(), [], {arm: [] for arm in ARMS}
    by_session, by_week = {day: _cell() for day in reference}, {week: _cell() for week in weeks}
    unknown_reasons = Counter()
    for basic, selected in zip(first["targets"], second["targets"], strict=True):
        checkpoint()
        for key in ("session", "symbol", "data_vintage", "membership_sha256", "source_metadata_sha256"):
            _same(basic[key], selected[key], "original_target_membership_or_source_vintage_disagrees")
        state = _state(basic, selected)
        base_status = basic["label"]["status"] if basic["label"] else "not_applicable"
        chosen_status = selected["label"]["status"] if selected["label"] else "not_applicable"
        observable = state in {"accepted", "rejected"}
        exclusions = (["acceptance_feature_unknown"] if state == "feature_unknown" else [])
        if observable and base_status == "unknown":
            exclusions.append("original_basic_label_unknown")
        if state == "accepted" and chosen_status == "unknown":
            exclusions.append("original_acceptance_label_unknown")
        common = observable and not exclusions
        row = {"session": basic["session"], "symbol": basic["symbol"], "data_vintage": basic["data_vintage"],
            "CT_source_row_sha256": basic["source_row_sha256"], "DU_source_row_sha256": selected["source_row_sha256"],
            "membership_sha256": basic["membership_sha256"], "source_metadata_sha256": basic["source_metadata_sha256"],
            "basic_status": basic["status"], "acceptance_status": selected["status"],
            "basic_label_status": base_status, "selected_label_status": chosen_status,
            "state": state, "feature_observable": observable, "in_common_scope": common,
            "exclusion_reasons": exclusions, "CT_unknown_reasons": basic["unknown_reasons"],
            "DU_unknown_reasons": selected["unknown_reasons"]}
        states.append(row)
        for cell in (flow, by_session[row["session"]], by_week[ct._week(row["session"])]):
            cell["targets"] += 1
            cell["state_counts"][state] += 1
            cell["CT_signal_counts"][basic["status"]] += 1
            cell["DU_signal_counts"][selected["status"]] += 1
            if base_status != "not_applicable":
                cell["CT_label_counts"][base_status] += 1
            if chosen_status != "not_applicable":
                cell["DU_label_counts"][chosen_status] += 1
            cell["feature_observable_basic_signals"] += observable
            cell["base_label_unknown_on_feature_scope"] += observable and base_status == "unknown"
            cell["accepted_label_unknown_on_feature_scope"] += state == "accepted" and chosen_status == "unknown"
            cell["accepted_known_lost_to_base_label_unknown"] += state == "accepted" and chosen_status == "known" and base_status == "unknown"
            cell["excluded_basic_signal_targets"] += basic["status"] == "signal" and not common
            cell["common_basic_events"] += common
            cell["common_selected_events"] += common and state == "accepted"
        unknown_reasons.update(exclusions)
        if common:
            records["basic"].append({"session": row["session"], "symbol": row["symbol"],
                "label": dl._ratio(basic["label"]["price_change"])})
            if state == "accepted":
                records["selected"].append({"session": row["session"], "symbol": row["symbol"],
                    "label": dl._ratio(selected["label"]["price_change"])})
    arms = {}
    for arm in ARMS:
        own = dm._dispersion(records[arm], reference, checkpoint)
        reasons = _choice(own)
        frequency, weekly = _frequency(records[arm], weeks, len(records["basic"]))
        arms[arm] = {"events": len(records[arm]), "own_label_dispersion": own,
            "frequency": frequency, "by_week": weekly, "sizing_passed": not reasons,
            "comparison_choice_reasons": reasons}
    reasons = [arm + ":" + reason for arm in ARMS for reason in arms[arm]["comparison_choice_reasons"]]
    return {"cohort_flow": flow, "source_counts": {role: {
            "signal_counts": flow[role + "_signal_counts"], "label_counts": flow[role + "_label_counts"]}
            for role in ("CT", "DU")},
        "unknown_reason_counts": dict(sorted(unknown_reasons.items())), "target_states": states,
        "common_basic_coordinates": [{"session": row["session"], "symbol": row["symbol"]} for row in records["basic"]],
        "selected_coordinates": [{"session": row["session"], "symbol": row["symbol"]} for row in records["selected"]],
        "by_session": by_session, "by_week": by_week, "by_arm": arms,
        "source_calibration_receipts": {"CT": deepcopy(first["source_calibration_receipts"]),
            "DU": deepcopy(second["source_calibration_receipts"])},
        "comparison_choice_reasons": reasons, "sizing_passed": not reasons,
        "requires_operator_choice_before_comparison": bool(reasons), "comparison": None,
        "reconciliation": {"all_original_targets_accounted": True, "original_unknowns_preserved": True,
            "selected_is_subset_of_same_common_basic_scope": True, "weekly_counts_match_arm_events": True,
            "intersection_losses_reported": True}}, records


def build_comparison(ct_projection: Mapping, du_projection: Mapping, *, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Finish four fresh own-label plans before activating one descriptive family."""
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        for role, projection in (("CT", ct_projection), ("DU", du_projection)):
            identity = projection["source_identity"]
            _require(identity["role"] == role and identity["report_sha256"] == UPSTREAM_SHA256[role]
                and identity["specification_sha256"] == UPSTREAM_SPECIFICATION_SHA256[role],
                "projection_source_identity_changed")
        for key in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar"):
            _same(ct_projection[key], du_projection[key], "original_calendars_or_targets_disagree")
        variants, records = {}, {}
        for policy in POLICIES:
            variants[policy], records[policy] = _variant(ct_projection, du_projection, policy, checkpoint)
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError,
            dl.BreakoutLossConcentrationRefusal) as exc:
        raise OpeningAcceptanceComparisonRefusal("malformed_or_mismatched_original_projections") from exc
    reasons = [policy + ":" + reason for policy in POLICIES for reason in variants[policy]["comparison_choice_reasons"]]
    if not reasons:
        for policy in POLICIES:
            means = {}
            for arm in ARMS:
                checkpoint()
                performance = _performance(records[policy][arm])
                variants[policy]["by_arm"][arm].update(performance)
                means[arm] = performance["gross_mean"]
            uncertainty = _uncertainty(records[policy], ct_projection["reference_sessions"], means, checkpoint)
            difference = means["selected"] - means["basic"]
            variants[policy]["comparison"] = {"selected_minus_basic": {
                "gross_mean_difference": difference, "net_mean_difference": difference,
                "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
                "descriptive_normal95_interval": _interval(difference, uncertainty["design_se"])}}
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "source_identity": {"CT": deepcopy(ct_projection["source_identity"]), "DU": deepcopy(du_projection["source_identity"])},
        "reference_sessions": list(ct_projection["reference_sessions"]), "reference_weeks": list(ct_projection["reference_weeks"]),
        "variants": variants, "comparison_performed": not reasons, "new_performance_families": int(not reasons),
        "calibration_only": bool(reasons),
        "requires_operator_choice": bool(reasons), "requires_operator_choice_before_comparison": bool(reasons),
        "comparison_not_run_reasons": reasons,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
