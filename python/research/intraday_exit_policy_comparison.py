"""One fixed managed exit versus its matched time-open control on CM labels.

Retain prior own-label sizing and every saved observation. New summaries are
conditional cash-unit price labels and descriptive paired uncertainty.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from statistics import NormalDist
import sys

from research import experiment_log
from research import intraday_exit_policy as cm


ci, ch, cg, horizons, cd, cc = cm.ci, cm.ch, cm.cg, cm.horizons, cm.cd, cm.cc
ROOT = cm.ROOT
SPECIFICATION_ID = "intraday-exit-policy-comparison-v1"
MODE = "fixed-three-minute-entry-time-only-vs-close-trigger-managed-exit-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-exit-policy-comparison-v1.json"
WINDOW, FAMILIES, ARMS = dict(cm.WINDOW), cm.FAMILIES, cm.ARMS
PARAMETERS = {**cm.PARAMETERS,
    "power": "retain_pinned_cm_both_own_label_dispersion_require_both_prerequisites_without_remeasurement",
    "upstream_reuse": "pinned_verified_cm_report_only_no_raw_reconstruction_or_reselection",
    "unit": "same_hypothetical_cash_budget_per_original_event_same_original_entry_open_both_arms",
    "cost": "subtract_exact_33_over_10000_from_each_event_both_arms_same_assumption",
    "comparison": "paired_managed_minus_time_only_same_entry_costs_cancel",
    "uncertainty": "paired_difference_maximum_naive_session_cluster_circular_five_session_bootstrap_2000_draws_seed_zero_descriptive_normal_95_interval",
    "bridge": "original_ch_30minute_close_summary_and_time_open_minus_old_close_reconciliation_not_management_effect",
    "output_scope": "all_four_fixed_groups_price_cost_paired_bridge_exit_reason_holding_descriptions_no_winner_or_executable_portfolio"}
UPSTREAM_SHA256 = "e03e53fb9389a461cd8d592d3335f3432df3b15b97e4d739e4535f34971ca379"
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cm-exit-policy-20261021-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cn-exit-policy-comparison-20261022-v1",
    "canonical_runs_path": cm.REGISTERED_PATHS["canonical_runs_path"]}
RESOURCES = dict(ch.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = cm.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = cm.EXPECTED_SELECTED_COMPLETE
EXPECTED_INPUT_COUNTS = cm.EXPECTED_INPUT_COUNTS
FLAGS = {**cm.FLAGS, "calibration_only": False, "comparison_performed": True, "exit_policy_compared": True}
ASSUMPTIONS = {**cm.ASSUMPTIONS,
    "measurement": "all four preregistered fixed-entry time-open and managed-open price-label groups; descriptive paired comparison only",
    "unit": "same hypothetical cash unit per original event in each arm; observed exit open divided by original entry open minus one; no equal-share or portfolio accounting",
    "power": "retain exact pinned CM own-label sizing for both arms; both must pass with zero unknowns before means; no own-power remeasurement or paired-error substitution",
    "exceptions": "no underpower exception; upstream legacy calibration choice remains separate from passing CM prerequisites",
    "cost": "subtract exact33/10000 from each event in each arm; same assumed33bp costs cancel in managed-minus-time-only; historical costs and fills remain uncertified",
    "dependence": "paired difference uncertainty includes all reference sessions including zero-event dates; maximum naive/session-cluster/circular five-session bootstrap error,2000draws seed0",
    "interval": "mean paired difference plus/minus normal0.975 quantile times design error is descriptive only; no p-value inferential decision family winner pass or promotion",
    "bridge": "oldCH30 terminal close is a descriptive bridge only; time-open minus old-close is a price-convention change separate from managed-minus-time-open",
    "exit_usage": "trigger reason counts do not guarantee gain or loss; integer entry-to-exit holding minutes describe this one fixed rule without tuning",
    "upstream": "only the pinned separately verified CM report is read; validate its saved trace contract without replaying raw first-trigger truth or recertifying original vendor truth"}
COST = Fraction(33, 10000)
_canonical, _sha = cm._canonical, cm._sha


class ComparisonRefusal(RuntimeError):
    """Stable reason codes omit private saved evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise ComparisonRefusal("specification_scope_mismatch")


def _positive_price(value: object) -> Fraction:
    if not isinstance(value, (str, Decimal)):
        raise ComparisonRefusal("upstream_price_not_exact")
    price = Decimal(value)
    if not price.is_finite() or price <= 0:
        raise ComparisonRefusal("invalid_upstream_price")
    return Fraction(price)


def _own_power_valid(own: Mapping, selected: int, sessions: int, reference: list[str]) -> bool:
    """Check retained sizing consistency, without calculating new own sizing."""
    numbers = ("event_sd", "naive_se", "session_cluster_se", "bootstrap_se", "design_se", "mde_bps")
    fields = {*numbers, "measured_labels", "reference_sessions", "known_label_sessions",
        "corrected_to_naive_se_ratio", "bootstrap_zero_label_draws", "cost_floor_bps_assumed",
        "requires_operator_choice", "underpower_exception_applied", "planning_constraint"}
    if not isinstance(own, Mapping) or set(own) != fields or any(type(own.get(name)) not in {int, float}
            or not math.isfinite(own[name]) or own[name] < 0 for name in numbers):
        return False
    expected_mde = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * own["design_se"] * 10000
    ratio = own["corrected_to_naive_se_ratio"]
    if (type(own["bootstrap_zero_label_draws"]) is not int or not 0 <= own["bootstrap_zero_label_draws"] <= 1998
            or (own["naive_se"] == 0 and ratio is not None)
            or (own["naive_se"] != 0 and (type(ratio) not in {int, float} or not math.isfinite(ratio)
                or not math.isclose(ratio, own["design_se"] / own["naive_se"], rel_tol=1e-12, abs_tol=1e-15)))):
        return False
    return (type(own.get("measured_labels")) is int and own["measured_labels"] == selected and selected >= 2
        and type(own.get("reference_sessions")) is int and own["reference_sessions"] == len(reference)
        and type(own.get("known_label_sessions")) is int and own["known_label_sessions"] == sessions
        and 2 <= sessions <= min(selected, len(reference))
        and math.isclose(own["naive_se"], own["event_sd"] / math.sqrt(selected), rel_tol=1e-12, abs_tol=1e-15)
        and own["design_se"] == max(own[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
        and math.isclose(own["mde_bps"], expected_mde, rel_tol=1e-12, abs_tol=1e-12)
        and own["mde_bps"] <= 33 and own.get("cost_floor_bps_assumed") == "33"
        and own.get("requires_operator_choice") is False and own.get("underpower_exception_applied") is False
        and own.get("planning_constraint") == "cost_floor_power_prerequisite_met")


def _validate_pair(pair: Mapping, event: Mapping, day: str) -> None:
    if set(pair) != {"common", *ARMS}:
        raise ComparisonRefusal("upstream_label_population_mismatch")
    opening = cc.session_open(day)
    signal, decision = (horizons._instant(event[name]) for name in ("signal_bar_end", "decision_at"))
    entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
    cap, entry_available = entry + 30 * cc.MINUTE, entry + 3 * cc.MINUTE
    if (not ((signal - opening) / cc.MINUTE).is_integer() or signal <= opening
            or decision != signal + 2 * cc.MINUTE or entry - decision != 3 * cc.MINUTE
            or entry + 120 * cc.MINUTE > cc.session_close(day)):
        raise ComparisonRefusal("invalid_upstream_signal_decision_or_entry_boundary")
    clocks = {"signal_bar_end": signal, "decision_at": decision, "entry_at": entry,
        "entry_available_at": entry_available, "time_cap_at": cap, "time_cap_decided_at": decision,
        "anchor_at": cap, "anchor_label_available_at": cap + 2 * cc.MINUTE}
    common = pair["common"]
    if (set(common) != {*clocks, "original_holding_minutes", "anchor_close", "anchor_price_change"}
            or any(horizons._instant(common[name]) != expected for name, expected in clocks.items())
            or type(common["original_holding_minutes"]) is not int or common["original_holding_minutes"] != 30):
        raise ComparisonRefusal("upstream_common_clock_mismatch")
    entry_prices = []
    fields = {"status", "entry_open", "exit_open", "exit_at", "price_change", "label_available_at", "unknown_reason", "model_metadata"}
    for arm in ARMS:
        outcome = pair[arm]
        if (set(outcome) != (fields if arm == "time_only" else fields | {"reason", "trigger_trace"})
                or outcome["status"] != "known" or outcome["unknown_reason"] is not None):
            raise ComparisonRefusal("upstream_unknown_or_invalid_label")
        start, end = (_positive_price(outcome[name]) for name in ("entry_open", "exit_open"))
        entry_prices.append(start)
        if horizons._ratio(outcome["price_change"]) != end / start - 1:
            raise ComparisonRefusal("upstream_exact_label_ratio_mismatch")
        exit_at, available = (horizons._instant(outcome[name]) for name in ("exit_at", "label_available_at"))
        duration = (exit_at - entry) / cc.MINUTE
        if (not duration.is_integer() or not 4 <= duration <= 30 or available != exit_at + 3 * cc.MINUTE
                or (arm == "time_only" and exit_at != cap)):
            raise ComparisonRefusal("upstream_exit_or_label_clock_mismatch")
        metadata = {"evidence_level": "assumed", "execution_price": "minute_open",
            "entry_price_available_at": common["entry_available_at"],
            "outcome_label_available_at": outcome["label_available_at"], "actual_fill_certified": False}
        if _canonical(outcome["model_metadata"]) != _canonical(metadata):
            raise ComparisonRefusal("upstream_model_metadata_mismatch")
    if entry_prices[0] != entry_prices[1]:
        raise ComparisonRefusal("upstream_shared_entry_mismatch")
    anchor = _positive_price(common["anchor_close"]) / entry_prices[0] - 1
    if horizons._ratio(common["anchor_price_change"]) != anchor:
        raise ComparisonRefusal("upstream_anchor_ratio_mismatch")
    managed = pair["managed"]
    reason, trace = managed["reason"], managed["trigger_trace"]
    if reason == "time_cap":
        if trace is not None or _canonical({name: managed[name] for name in fields}) != _canonical(pair["time_only"]):
            raise ComparisonRefusal("upstream_time_fallback_mismatch")
    elif reason in {"take_profit", "stop_loss"}:
        if not isinstance(trace, Mapping) or set(trace) != {"bar_start", "close_at", "close", "price_change", "available_at", "decision_at", "scheduled_fill_at"}:
            raise ComparisonRefusal("upstream_trigger_trace_mismatch")
        close_at = horizons._instant(trace["close_at"])
        offset = (close_at - entry) / cc.MINUTE
        if (not offset.is_integer() or not 1 <= offset <= 26
                or horizons._instant(trace["bar_start"]) != close_at - cc.MINUTE
                or horizons._instant(trace["available_at"]) != close_at + 2 * cc.MINUTE
                or horizons._instant(trace["decision_at"]) != close_at + 2 * cc.MINUTE
                or horizons._instant(trace["scheduled_fill_at"]) != close_at + 3 * cc.MINUTE
                or horizons._instant(managed["exit_at"]) != close_at + 3 * cc.MINUTE
                or not entry_available <= close_at + 2 * cc.MINUTE < close_at + 3 * cc.MINUTE < cap):
            raise ComparisonRefusal("upstream_trigger_clock_mismatch")
        change = _positive_price(trace["close"]) / entry_prices[0] - 1
        if (horizons._ratio(trace["price_change"]) != change
                or reason != ("take_profit" if change >= cm.TAKE_PROFIT else "stop_loss" if change <= cm.STOP_LOSS else None)):
            raise ComparisonRefusal("upstream_trigger_ratio_or_reason_mismatch")
    else:
        raise ComparisonRefusal("upstream_exit_reason_mismatch")
    time_change, managed_change = (horizons._ratio(pair[arm]["price_change"]) for arm in ARMS)
    if managed_change - anchor != (managed_change - time_change) + (time_change - anchor):
        raise ComparisonRefusal("upstream_bridge_identity_mismatch")


def _validate_contract(report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    expected = {"study_id": cm.SPECIFICATION_ID, "mode": cm.MODE, "parameters": cm.PARAMETERS,
        "window": WINDOW, **cm.FLAGS, "assumptions": cm.ASSUMPTIONS, "requires_operator_choice": False}
    if (any(_canonical(report.get(name)) != _canonical(value) for name, value in expected.items())
            or type(report.get("upstream_calibration_requires_operator_choice")) is not bool
            or set(report.get("variants", {})) != {"D+1", "D+2"}):
        raise ComparisonRefusal("wrong_upstream_study_or_prerequisites")
    reference = report.get("reference_sessions")
    if (not isinstance(reference, list) or not reference or reference != sorted(set(reference))
            or any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or not WINDOW["start"] <= day <= WINDOW["end"] for day in reference)):
        raise ComparisonRefusal("upstream_reference_population_mismatch")
    union, source_rows, event_labels = {day: set() for day in reference}, {}, {}
    for variant in report["variants"].values():
        if set(variant) != {"days", "families"} or set(variant["families"]) != set(FAMILIES):
            raise ComparisonRefusal("upstream_family_population_mismatch")
        grouped = {}
        for row in variant["days"]:
            day, symbol = row["session"], row["symbol"]
            if (set(row) != {"symbol", "session", "data_vintage", "raw_chunk_metadata", "families"}
                    or day not in union or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol)
                    or not re.fullmatch(r"[0-9a-f]{64}", row["data_vintage"])):
                raise ComparisonRefusal("target_outside_registered_population")
            grouped.setdefault(day, []).append(row)
        if list(grouped) != reference:
            raise ComparisonRefusal("target_matrix_lost_sessions")
        for day, rows in grouped.items():
            checkpoint()
            names = {row["symbol"] for row in rows}
            if len(rows) != 20 or len(names) != 20:
                raise ComparisonRefusal("target_population_changed")
            union[day].update(names)
            for row in rows:
                metadata = row["raw_chunk_metadata"]
                if (set(metadata) != {"observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level"}
                        or metadata["observation_date"] != day or metadata["available_at"] is not None
                        or metadata["source"] != "collector klines" or metadata["is_final"] is not None
                        or metadata["evidence_level"] != "assumed"):
                    raise ComparisonRefusal("upstream_raw_metadata_mismatch")
                horizons._instant(metadata["retrieved_at"])
                source_key, source_value = (day, row["symbol"]), _canonical([row["data_vintage"], metadata])
                if source_key in source_rows and source_rows[source_key] != source_value:
                    raise ComparisonRefusal("upstream_cross_variant_source_mismatch")
                source_rows[source_key] = source_value
                if set(row["families"]) != set(FAMILIES):
                    raise ComparisonRefusal("upstream_family_population_mismatch")
                for finding in row["families"].values():
                    if set(finding) != {*cm.ORIGINAL_FIELDS, "labels"}:
                        raise ComparisonRefusal("upstream_projection_incomplete")
                    status, state = finding["status"], finding["selection_state"]
                    reasons = finding["selection_unknown_reasons"]
                    if (status not in cd.STATES or state not in cg.SELECTION_STATES
                            or not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons)
                            or (state == "unknown") != bool(reasons) or (status != "signal") != (state == "non_signal")):
                        raise ComparisonRefusal("invalid_upstream_selection_state")
                    cohort = ("non_signal" if status != "signal" else "outside_original_complete_cohort"
                        if finding["original_pair_status"] != "complete" else "selected_complete" if state == "pass" else "not_selected")
                    if finding["label_cohort_state"] != cohort:
                        raise ComparisonRefusal("upstream_selection_cohort_mismatch")
                    peers = finding["selected_controls"]
                    peer_names = [item["symbol"] for item in peers]
                    if (peer_names != sorted(set(peer_names)) or row["symbol"] in peer_names
                            or not set(peer_names) <= names or any(set(item) != {"symbol"} for item in peers)):
                        raise ComparisonRefusal("invalid_upstream_peer_population")
                    original_reasons, unknown_peers = finding["original_pair_unknown_reasons"], finding["original_unknown_control_symbols"]
                    if (not isinstance(original_reasons, list) or any(not isinstance(item, str) for item in original_reasons)
                            or not isinstance(unknown_peers, list) or unknown_peers != sorted(set(unknown_peers))
                            or not set(unknown_peers) <= set(peer_names)):
                        raise ComparisonRefusal("invalid_upstream_original_unknowns")
                    if status != "signal":
                        if (finding["event"] is not None or finding["original_pair_status"] is not None
                                or finding["original_event_label_state"] is not None or peers
                                or finding["excluded_controls"] or original_reasons or unknown_peers):
                            raise ComparisonRefusal("upstream_nonsignal_has_event")
                    elif (finding["original_pair_status"] not in {"complete", "unknown"}
                            or (finding["original_pair_status"] == "complete") != (bool(peers) and not original_reasons)
                            or set(finding["event"]) != {"signal_bar_end", "decision_at"}):
                        raise ComparisonRefusal("invalid_upstream_event_or_cohort")
                    else:
                        original = finding["original_event_label_state"]
                        if (not isinstance(original, Mapping) or set(original) != {"status", "missing_minute_buckets", "unknown_reason"}
                                or original["status"] not in {"known", "unknown"} or type(original["missing_minute_buckets"]) is not int
                                or not 0 <= original["missing_minute_buckets"] <= 120
                                or (original["status"] == "known") != (original["missing_minute_buckets"] == 0)
                                or original["unknown_reason"] != (None if original["status"] == "known" else "incomplete_120minute_path")):
                            raise ComparisonRefusal("invalid_upstream_original_label_state")
                        expected_reasons = (["no_selected_controls"] if not peers else [])
                        if original["status"] == "unknown": expected_reasons.append("event_label_unknown")
                        if unknown_peers: expected_reasons.append("selected_control_label_unknown")
                        if original_reasons != sorted(expected_reasons):
                            raise ComparisonRefusal("upstream_original_unknown_state_mismatch")
                    labels = finding["labels"]
                    if cohort == "selected_complete":
                        _validate_pair(labels, finding["event"], day)
                        event_key = (day, row["symbol"], finding["event"]["signal_bar_end"], finding["event"]["decision_at"])
                        value = _canonical(labels)
                        if event_key in event_labels and event_labels[event_key] != value:
                            raise ComparisonRefusal("upstream_cross_family_event_label_mismatch")
                        event_labels[event_key] = value
                    elif labels is not None:
                        raise ComparisonRefusal("upstream_unselected_has_labels")
        for family in FAMILIES:
            counts = cg._counts([row["families"][family] for row in variant["days"]])
            summary = variant["families"][family]
            if (set(summary) != {*counts, "by_arm", "requires_operator_choice"}
                    or any(_canonical(summary.get(name)) != _canonical(value) for name, value in counts.items())
                    or summary["requires_operator_choice"] is not False or set(summary["by_arm"]) != set(ARMS)):
                raise ComparisonRefusal("upstream_counts_or_family_prerequisite_mismatch")
            sessions = len({row["session"] for row in variant["days"]
                if row["families"][family]["label_cohort_state"] == "selected_complete"})
            for cell in summary["by_arm"].values():
                if (set(cell) != {"selected_complete", "known_labels", "unknown_labels", "own_label_dispersion", "requires_operator_choice"}
                        or any(type(cell[name]) is not int or cell[name] != expected for name, expected in (
                            ("selected_complete", counts["selected_complete"]), ("known_labels", counts["selected_complete"]), ("unknown_labels", 0)))
                        or cell["requires_operator_choice"] is not False
                        or not _own_power_valid(cell["own_label_dispersion"], counts["selected_complete"], sessions, reference)):
                    raise ComparisonRefusal("qualified_cell_power_prerequisite_failed")
    evidence = report["input_evidence"]
    expected_evidence = {"upstream_report_sha256": cm.UPSTREAM_SHA256,
        "upstream_report_path": cm.REGISTERED_PATHS["upstream_path"], "archive_path": cm.REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": cm.INPUT_COMPRESSED_SHA256, "stream_sha256": cm.INPUT_STREAM_SHA256,
        "unique_target_days": sum(map(len, union.values()))}
    if (set(evidence) != {*expected_evidence, "queried_rows"}
            or any(_canonical(evidence.get(name)) != _canonical(value) for name, value in expected_evidence.items())
            or type(evidence["unique_target_days"]) is not int or type(evidence["queried_rows"]) is not int
            or not 0 <= evidence["queried_rows"] <= cm.RESOURCES["maximum_rows"]):
        raise ComparisonRefusal("upstream_input_evidence_mismatch")
    return reference


def _validate_upstream(report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    try:
        return _validate_contract(report, checkpoint)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as exc:
        raise ComparisonRefusal("malformed_upstream_contract") from exc


def _describe(values: list[Fraction]) -> dict:
    total, n = sum(values, Fraction()), len(values)
    return {"events": n, "sum_price_change": total, "mean_price_change": float(total / n),
        "mean_after_assumed_cost": float(total / n - COST),
        "gross_positive_events": sum(value > 0 for value in values),
        "cost_exceeding_events": sum(value > COST for value in values)}


def build_comparison(cm_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure saved-label comparison; all eight own-arm gates precede means."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    reference = _validate_upstream(cm_report, checkpoint)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "qualified_cells_power_prerequisite_met": True, "requires_operator_choice": False,
        "upstream_calibration_requires_operator_choice": cm_report["requires_operator_choice"],
        "upstream_legacy_calibration_requires_operator_choice": cm_report["upstream_calibration_requires_operator_choice"],
        "input_evidence": {"upstream_report_sha256": UPSTREAM_SHA256,
            "upstream_report_path": REGISTERED_PATHS["upstream_path"], "calibration_input_evidence": cm_report["input_evidence"]},
        "variants": {}}
    for key, variant in cm_report["variants"].items():
        families = {}
        for family in FAMILIES:
            checkpoint()
            selected = [row for row in variant["days"] if row["families"][family]["labels"] is not None]
            pairs = [row["families"][family]["labels"] for row in selected]
            time_only, managed = ([horizons._ratio(pair[arm]["price_change"]) for pair in pairs] for arm in ARMS)
            anchor = [horizons._ratio(pair["common"]["anchor_price_change"]) for pair in pairs]
            delta = [new - old for new, old in zip(managed, time_only, strict=True)]
            bridge = [new - old for new, old in zip(time_only, anchor, strict=True)]
            uncertainty = horizons._statistics([{"session": row["session"], "label": change}
                for row, change in zip(selected, delta, strict=True)], reference, checkpoint)
            mean = float(sum(delta, Fraction()) / len(delta))
            error = uncertainty["design_se"]
            if error is None:
                raise ComparisonRefusal("paired_uncertainty_unavailable")
            margin = NormalDist().inv_cdf(.975) * error
            durations = [int((horizons._instant(pair["managed"]["exit_at"])
                - horizons._instant(pair["common"]["entry_at"])) / cc.MINUTE) for pair in pairs]
            families[family] = {**variant["families"][family], "time_only": _describe(time_only), "managed": _describe(managed),
                "paired": {"events": len(delta), "sum_change": sum(delta, Fraction()), "mean_change": mean,
                    "improved_events": sum(value > 0 for value in delta), "equal_events": sum(value == 0 for value in delta),
                    "worse_events": sum(value < 0 for value in delta), "uncertainty": uncertainty,
                    "descriptive_normal_95_interval": [mean - margin, mean + margin]},
                "old_close_bridge": {"old_close": _describe(anchor), "time_open_minus_old_close": {
                    "events": len(bridge), "sum_change": sum(bridge, Fraction()), "mean_change": float(sum(bridge, Fraction()) / len(bridge))}},
                "exit_usage": {**{reason: sum(pair["managed"]["reason"] == reason for pair in pairs)
                    for reason in ("take_profit", "stop_loss", "time_cap")},
                    "holding_minutes": {"min": min(durations), "max": max(durations), "sum": sum(durations),
                        "mean": sum(durations) / len(durations)}}}
        result["variants"][key] = {"days": variant["days"], "families": families}
    checkpoint()
    return result


def run_comparison(*, specification_path: str | Path, upstream_path: str | Path,
        output_directory: str | Path, runs_path: str | Path, budget=None,
        before_completion: Callable[[], None] | None = None) -> dict:
    """Durably start before the sole CM input read; caller owns budget finish."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("exit_policy_comparison_pipeline")
    checkpoint()
    spec_path, upstream, output, log = map(Path, (specification_path, upstream_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise ComparisonRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, output, log)
    for path, key in zip((upstream, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(REGISTERED_PATHS[key]):
            raise ComparisonRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ComparisonRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise ComparisonRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(cm.__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__),
        Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    executable = Path(sys.executable).resolve(strict=True)
    for path in (upstream, output, log):
        if any(path.resolve() == source.resolve()
                or (path.exists() and source.exists() and path.samefile(source)) for source in (*sources, executable)):
            raise ComparisonRefusal("aliased_source_or_runtime_path")
    pins = {str(path): _sha(path) for path in sources}
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise ComparisonRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise ComparisonRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise ComparisonRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise ComparisonRefusal("unsafe_output_permissions")
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise ComparisonRefusal("upstream_size_or_hash_mismatch")
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        report = build_comparison(upstream_report, checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise ComparisonRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise ComparisonRefusal("registered_selected_population_changed")
        if any(report["input_evidence"]["calibration_input_evidence"][name] != value for name, value in EXPECTED_INPUT_COUNTS.items()):
            raise ComparisonRefusal("registered_raw_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False, default=lambda value: json.loads(_canonical(value)))
            for index, chunk in enumerate(encoder.iterencode(report)):
                if index % 1024 == 0: checkpoint()
                stream.write(chunk.encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        cc._sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None: before_completion()
        checkpoint()
        if any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256:
            raise ComparisonRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise ComparisonRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha, "input_evidence": report["input_evidence"],
            "requires_operator_choice": False, **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
