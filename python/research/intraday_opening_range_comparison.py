"""One source-bound descriptive comparison of observed CT opening-range labels.

The supplied calibration remains intact, including unknown targets/outcomes and
its original choice flags. The caller authenticates saved bytes, registration,
runtime, logging and resources; this module performs no IO or actual execution.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date
from fractions import Fraction
import hashlib
import math
import random
import re
from statistics import NormalDist, stdev

from research import intraday_opening_range_calibration as ct


ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-opening-range-comparison-v1"
MODE = "observed-label-conditional-opening-range-descriptive-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-range-comparison-v1.json"
WINDOW = dict(ct.WINDOW)
ARMS = ct.ARMS
UPSTREAM_SHA256 = "b25690b7cc87278ebe1baf9f20d6f0185c7d759c93b90e7488b89b53383aa875"
UPSTREAM_SPECIFICATION_SHA256 = "1578dbf61fdbbdb620218f1292da412ec99e8eff3ce1d8c67870bcce304b56bf"
PARAMETERS = {
    "axis": "initial_opening_range15versus30minutes_only",
    "cohort": "each_arm_independently_CT_signal_and_known_label_no_common_complete_mask",
    "source_parameters": deepcopy(ct.PARAMETERS),
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "contrasts": "15minus30_conditional_per_entry_means_separately_D+1_D+2",
    "uncertainty": "max_unpaired_naive_joint_session_cluster_joint_circular_block_bootstrap",
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "descriptive_interval": "normal95percent_not_confirmatory",
    "missingness_choice": "one_source_bound_observed_label_comparison_preserve_unknowns",
    "underpower_exception_applied": False,
    "output_scope": "four_conditional_means_two_policy_contrasts_no_subgroup_means_or_winner",
}
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-ct-opening-range-20261023-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cu-opening-range-comparison-20261023-v1",
    "canonical_runs_path": "/home/minjun4897/trading-engine/runs/experiments.jsonl",
}
RESOURCES = {
    "maximum_upstream_bytes": 67_108_864, "minimum_free_bytes": 1_073_741_824,
    "address_space_mib": 512, "cpu_seconds": 300, "wall_seconds": 1200,
    "nice": 10, "automatic_retries": 0,
}
FLAGS = {**ct.FLAGS, "calibration_only": False, "comparison_performed": True,
    "requires_operator_choice": False}
AUTHORIZATION = {
    "operator_choice": "option1_one_limited_observed_label_descriptive_comparison",
    "upstream_report_sha256": UPSTREAM_SHA256,
    "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
    "unknown_label_conditional_comparison_authorized": True,
    "underpower_exception_applied": False,
    "scope": "this_CT_report_this15versus30comparison_only_no_other_family_or_source_failure",
}
ASSUMPTIONS = {
    "population": "each_arm_own_observed_labels_unequal_selected_samples_not_a_matched_causal_timing_effect",
    "missingness": "all_original_targets_signals_unknowns_retained_no_zero_fill_or_name_replacement_selection_bias_remains",
    "cost": "fixed_assumed33bp_per_entry_not_historical_cost_or_actual_fill_certificate",
    "uncertainty": "joint_whole_reference_calendar_including_zero_dates_maxSE_descriptive_normal95percent_not_a_significance_test",
    "policies": "D+1_and_D+2_overlap_not_independent_replications_or_holding_periods",
    "claims": "conditional_price_responses_not_portfolio_PnL_no_winner_edge_family_certification_direction_closure_reserved_access_or_promotion",
    "source": "upstream_provenance_retains_CT_assumptions_flags_own_sizing_and_execution_manifest_without_new_raw_replay",
}
_canonical = ct._canonical
_COST = Fraction(33, 10000)


class OpeningRangeComparisonRefusal(RuntimeError):
    """Stable reason codes exclude supplied source values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise OpeningRangeComparisonRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise OpeningRangeComparisonRefusal(reason)


def _integer(value: object, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or maximum is not None and value > maximum:
        raise OpeningRangeComparisonRefusal("invalid_source_quantity")
    return value


def _ratio(value: object) -> Fraction:
    try:
        if isinstance(value, Fraction):
            return value
        if isinstance(value, Mapping):
            if set(value) != {"numerator", "denominator"}:
                raise ValueError
            numerator, denominator = value["numerator"], value["denominator"]
            if not isinstance(numerator, str) or not isinstance(denominator, str):
                raise ValueError
            ratio = Fraction(int(numerator), int(denominator))
            if str(ratio.numerator) != numerator or str(ratio.denominator) != denominator:
                raise ValueError
            return ratio
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError
        return Fraction(value)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise OpeningRangeComparisonRefusal("invalid_source_exact_ratio") from exc


def _positive_price(value: object) -> Fraction:
    price = _ratio(value)
    if price <= 0:
        raise OpeningRangeComparisonRefusal("invalid_source_positive_price")
    return price


def _instant(value: object):
    try:
        return ct._instant(value)
    except ct.OpeningRangeRefusal as exc:
        raise OpeningRangeComparisonRefusal("invalid_source_clock") from exc


def _finding(row: Mapping, arm: str) -> Fraction | None:
    finding = row["arms"][arm]
    status = finding["status"]
    if status not in ct.STATES or finding["required_bars"] != 47:
        raise OpeningRangeComparisonRefusal("invalid_source_signal_state")
    prefix = _integer(finding["valid_prefix_bars"], 47)
    if row["input_rows"] < prefix * 5:
        raise OpeningRangeComparisonRefusal("source_prefix_exceeds_observed_rows")
    gaps = finding["decision_prefix_unknown_bucket_counts"]
    if set(gaps) != set(ct.GAP_STATES):
        raise OpeningRangeComparisonRefusal("invalid_source_prefix_gaps")
    for count in gaps.values():
        _integer(count, 235)
    if status != "signal":
        if finding["event"] is not None or finding["label"] is not None:
            raise OpeningRangeComparisonRefusal("source_nonsignal_has_outcome")
        reasons = (["no_saved_rows_unknown_cause"] if row["input_rows"] == 0 else
            [f"decision_prefix_{state}_minute_buckets" for state in ct.GAP_STATES if gaps[state]])
        if status == "known_no_signal":
            if prefix != 47 or any(gaps.values()):
                raise OpeningRangeComparisonRefusal("source_absence_without_complete_prefix")
            reasons = []
        elif prefix == 47 or not reasons:
            raise OpeningRangeComparisonRefusal("source_unknown_without_missing_prefix")
        _same(finding["unknown_reasons"], reasons, "source_signal_unknown_reasons_mismatch")
        return None
    if finding["unknown_reasons"]:
        raise OpeningRangeComparisonRefusal("source_signal_unknown_reasons_mismatch")
    event, label = finding["event"], finding["label"]
    opening = ct.cc.session_open(row["session"])
    start, end, decision = (_instant(event[name]) for name in
        ("signal_bar_start", "signal_bar_end", "decision_at"))
    if (event["family"] != "opening_range_breakout" or event["range_minutes"] != int(arm)
            or start < opening + int(arm) * ct.cc.MINUTE or end - start != ct.cc.FIVE
            or (end - opening) % ct.cc.FIVE or decision != end + 2 * ct.cc.MINUTE
            or decision > opening + 240 * ct.cc.MINUTE
            or prefix < (end - opening) // ct.cc.FIVE
            or _instant(event["available_at"]) != decision):
        raise OpeningRangeComparisonRefusal("source_signal_clock_or_rule_changed")
    if (event["observation_date"] != row["session"] or event["is_final"] is not None
            or event["evidence_level"] != "assumed" or event["data_vintage"] != row["data_vintage"]
            or event["retrieved_at"] != row["raw_chunk_metadata"]["retrieved_at"]
            or event["source"] != "collector klines; assumed completed five-minute bars"
            or event["parameters_sha256"] != hashlib.sha256(_canonical(ct.PARAMETERS)).hexdigest()):
        raise OpeningRangeComparisonRefusal("source_event_provenance_changed")
    if len(event["context"]) != int(arm) // 5 or event["drop_bar"] is not None or event["trough_bar"] is not None:
        raise OpeningRangeComparisonRefusal("source_context_changed")
    for index, bar in enumerate(event["context"]):
        expected_start = opening + index * ct.cc.FIVE
        if (_instant(bar["start_at"]) != expected_start
                or _instant(bar["end_at"]) != expected_start + ct.cc.FIVE
                or _instant(bar["available_at"]) != expected_start + ct.cc.FIVE + 2 * ct.cc.MINUTE):
            raise OpeningRangeComparisonRefusal("source_context_changed")
        low, high = _positive_price(bar["low"]), _positive_price(bar["high"])
        if not low <= min(_positive_price(bar["open"]), _positive_price(bar["close"])) <= max(
                _positive_price(bar["open"]), _positive_price(bar["close"])) <= high:
            raise OpeningRangeComparisonRefusal("source_context_changed")
    entry, exit_at = (_instant(label[name]) for name in ("entry_at", "exit_at"))
    if (entry != decision + 3 * ct.cc.MINUTE or (entry - opening) % ct.cc.FIVE
            or exit_at != entry + 30 * ct.cc.MINUTE or exit_at >= ct.cc.session_close(row["session"])
            or _instant(label["time_cap_decided_at"]) != decision
            or _instant(label["entry_price_available_at"]) != entry + 3 * ct.cc.MINUTE
            or _instant(label["label_available_at"]) != exit_at + 3 * ct.cc.MINUTE
            or label["required_minute_buckets"] != 31
            or label["evidence_level"] != "assumed" or label["actual_fill_certified"] is not False):
        raise OpeningRangeComparisonRefusal("source_label_clock_or_rule_changed")
    unknown = _integer(label["unknown_minute_buckets"], 31)
    label_gaps = label["unknown_bucket_reason_counts"]
    if set(label_gaps) != set(ct.GAP_STATES) or sum(_integer(value, 31) for value in label_gaps.values()) != unknown:
        raise OpeningRangeComparisonRefusal("source_label_unknown_counts_changed")
    _same(label["unknown_reasons"], [f"label_{state}_minute_buckets" for state in ct.GAP_STATES
        if label_gaps[state]], "source_label_unknown_reasons_changed")
    for name in ("entry_open", "exit_open"):
        if label[name] is not None:
            _positive_price(label[name])
    if label["status"] == "unknown":
        if not unknown or label["price_change"] is not None:
            raise OpeningRangeComparisonRefusal("source_unknown_outcome_must_remain_unknown")
        return None
    if label["status"] != "known" or unknown:
        raise OpeningRangeComparisonRefusal("source_known_label_has_missing_buckets")
    value = _ratio(label["price_change"])
    if value != _positive_price(label["exit_open"]) / _positive_price(label["entry_open"]) - 1:
        raise OpeningRangeComparisonRefusal("source_label_price_ratio_changed")
    return value


def _validate_source(source: Mapping, checkpoint: Callable[[], None]) -> dict:
    if source["study_id"] != ct.SPECIFICATION_ID or source["mode"] != ct.MODE:
        raise OpeningRangeComparisonRefusal("wrong_source_study_or_mode")
    for name, expected in (("window", ct.WINDOW), ("parameters", ct.PARAMETERS),
            ("assumptions", ct.ASSUMPTIONS)):
        _same(source[name], expected, "source_scope_changed")
    for name, expected in ct.FLAGS.items():
        if source.get(name) is not expected:
            raise OpeningRangeComparisonRefusal("source_certification_or_mode_flag_changed")
    if "specification_sha256" in source and source["specification_sha256"] != UPSTREAM_SPECIFICATION_SHA256:
        raise OpeningRangeComparisonRefusal("source_specification_changed")
    reference = source["reference_sessions"]
    if (not isinstance(reference, list) or len(reference) < 2 or reference != sorted(set(reference))
            or any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or not WINDOW["start"] <= day <= WINDOW["end"] for day in reference)):
        raise OpeningRangeComparisonRefusal("invalid_source_reference_sessions")
    weeks = sorted({ct._week(day) for day in reference})
    _same(source["reference_weeks"], weeks, "source_reference_weeks_changed")
    _same([row["date"] for row in source["upstream_calendar"]["rows"]
        if WINDOW["start"] <= row["date"] <= WINDOW["end"]], reference, "source_calendar_changed")
    variants, matrix = source["variants"], source["source_target_matrix"]
    if set(variants) != {"D+1", "D+2"} or set(matrix) != set(variants):
        raise OpeningRangeComparisonRefusal("source_policy_variants_changed")
    records, unique_rows, choices = {}, {}, []
    for policy, variant in variants.items():
        checkpoint()
        if "comparison" in variant:
            raise OpeningRangeComparisonRefusal("source_already_contains_comparison")
        memberships = matrix[policy]["days"]
        _same([row["session"] for row in memberships], reference, "source_target_dates_changed")
        expected_rows = []
        for membership in memberships:
            day = membership["session"]
            if not _instant(membership["available_at"]) <= _instant(membership["selected_at"]) < ct.cc.session_open(day):
                raise OpeningRangeComparisonRefusal("noncausal_source_membership")
            targets = membership["targets"]
            symbols = [target["symbol"] for target in targets]
            if (not 1 <= len(targets) <= 20 or len(set(symbols)) != len(symbols)
                    or any(type(target["rank"]) is not int for target in targets)
                    or {target["rank"] for target in targets} != set(range(1, len(targets) + 1))
                    or any(not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) for symbol in symbols)):
                raise OpeningRangeComparisonRefusal("source_target_population_changed")
            expected_rows.extend((day, target, membership) for target in targets)
        if len(variant["days"]) != len(expected_rows) or set(variant["by_arm"]) != set(ARMS):
            raise OpeningRangeComparisonRefusal("source_target_population_changed")
        arm_records = {arm: [] for arm in ARMS}
        for row, (day, target, membership) in zip(variant["days"], expected_rows, strict=True):
            checkpoint()
            if row["session"] != day or row["symbol"] != target["symbol"] or set(row["arms"]) != set(ARMS):
                raise OpeningRangeComparisonRefusal("source_target_identity_changed")
            _same(row["membership_target"], target, "source_membership_target_changed")
            _same(row["membership_source"], {key: value for key, value in membership.items() if key != "targets"},
                "source_membership_metadata_changed")
            _integer(row["input_rows"], 600)
            if not re.fullmatch(r"[0-9a-f]{64}", row["data_vintage"]):
                raise OpeningRangeComparisonRefusal("invalid_source_vintage")
            metadata = row["raw_chunk_metadata"]
            _instant(metadata["retrieved_at"])
            if (metadata["observation_date"] != day or metadata["available_at"] is not None
                    or metadata["is_final"] is not None or metadata["evidence_level"] != "assumed"
                    or metadata["source"] != "collector klines"):
                raise OpeningRangeComparisonRefusal("source_raw_metadata_changed")
            offsets = row["source_offset_counts_ms"]
            if (any(not re.fullmatch(r"0|[1-9][0-9]{0,4}", str(offset)) or int(offset) >= 60000 for offset in offsets)
                    or sum(_integer(count, 600) for count in offsets.values()) != row["input_rows"]):
                raise OpeningRangeComparisonRefusal("source_raw_offset_counts_changed")
            for arm in ARMS:
                value = _finding(row, arm)
                if row["arms"][arm]["status"] == "signal":
                    arm_records[arm].append({"session": day, "label": value})
            _same(row["event_timing_state"], ct._timing(row["arms"]), "source_event_timing_changed")
            key = day, row["symbol"]
            physical_row = {name: value for name, value in row.items() if name not in ("membership_target", "membership_source")}
            if key in unique_rows:
                _same(physical_row, unique_rows[key], "source_cross_policy_observations_disagree")
            else:
                unique_rows[key] = physical_row
        for arm in ARMS:
            checkpoint()
            summary = variant["by_arm"][arm]
            for name, expected in ct._counts(variant["days"], arm).items():
                _same(summary[name], expected, "source_counts_changed")
            own = ct.cc.dispersion(arm_records[arm], reference)
            _same(summary["own_label_dispersion"], own, "source_own_sizing_changed")
            if own["mde_bps"] is None or own["mde_bps"] > 33:
                raise OpeningRangeComparisonRefusal("new_source_own_sizing_failure_not_authorized")
            reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
            _same(summary["comparison_choice_reasons"], reasons, "source_choice_reasons_changed")
            if summary["requires_operator_choice_before_comparison"] is not bool(reasons):
                raise OpeningRangeComparisonRefusal("source_choice_flag_changed")
            choices.append(bool(reasons))
            expected_week = {week: ct._counts([row for row in variant["days"] if ct._week(row["session"]) == week], arm)
                for week in weeks}
            _same(summary["by_week"], expected_week, "source_week_counts_changed")
            _same(summary["by_month"], {month: ct._counts([row for row in variant["days"]
                if row["session"][:7] == month], arm) for month in sorted({day[:7] for day in reference})},
                "source_month_counts_changed")
            _same(summary["frequency"], {"reference_sessions": len(reference), "reference_weeks": len(weeks),
                "signals_per_reference_week": Fraction(len(arm_records[arm]), len(weeks)),
                "known_labels_per_reference_week": Fraction(own["known_labels"], len(weeks)),
                "weekly_operating_objective": 15,
                "weeks_with_at_least15_signals": sum(item["signal_counts"]["signal"] >= 15 for item in expected_week.values()),
                "forced_trades": False, "executable_frequency_certified": False}, "source_frequency_changed")
        _same(variant["event_timing"], ct._timing_counts(variant["days"]), "source_event_timing_counts_changed")
        _same(variant["event_timing_by_week"], {week: ct._timing_counts([row for row in variant["days"]
            if ct._week(row["session"]) == week]) for week in weeks}, "source_week_timing_changed")
        records[policy] = arm_records
    if source["requires_operator_choice_before_comparison"] is not any(choices):
        raise OpeningRangeComparisonRefusal("source_choice_flag_changed")
    evidence = source["input_evidence"]
    _same(evidence, {"upstream_report_sha256": ct.UPSTREAM_SHA256,
        "upstream_report_path": ct.REGISTERED_PATHS["upstream_path"],
        "archive_path": ct.REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": ct.INPUT_COMPRESSED_SHA256, "stream_sha256": ct.INPUT_STREAM_SHA256,
        "unique_target_days": len(unique_rows), "queried_rows": sum(row["input_rows"] for row in unique_rows.values()),
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}, "source_input_lineage_changed")
    return records


def _interval(mean: Fraction, se: float) -> dict:
    width = NormalDist().inv_cdf(.975) * se
    return {"lower": float(mean) - width, "upper": float(mean) + width}


def _uncertainty(records: Mapping, reference: list[str], means: Mapping,
        checkpoint: Callable[[], None]) -> dict:
    known = {arm: [row for row in records[arm] if row["label"] is not None] for arm in ARMS}
    counts = {arm: len(known[arm]) for arm in ARMS}
    daily = {day: {arm: [Fraction(0), 0] for arm in ARMS} for day in reference}
    for arm in ARMS:
        for row in known[arm]:
            daily[row["session"]][arm][0] += row["label"]
            daily[row["session"]][arm][1] += 1
    known_dates = {arm: sum(daily[day][arm][1] > 0 for day in reference) for arm in ARMS}
    if any(counts[arm] < 2 or known_dates[arm] < 2 for arm in ARMS):
        raise OpeningRangeComparisonRefusal("insufficient_known_labels_or_dates")
    groups = len(reference)
    naive = math.sqrt(math.fsum(stdev(float(row["label"]) for row in known[arm]) ** 2 / counts[arm] for arm in ARMS))
    influences = []
    for day in reference:
        influences.append((daily[day]["15"][0] - daily[day]["15"][1] * means["15"]) / counts["15"]
            - (daily[day]["30"][0] - daily[day]["30"][1] * means["30"]) / counts["30"])
    cluster = math.sqrt(groups / (groups - 1) * math.fsum(float(value) ** 2 for value in influences))
    calendar = [daily[day] for day in reference]
    blocks = {size: [{arm: (math.fsum(float(calendar[(index + offset) % groups][arm][0]) for offset in range(size)),
        sum(calendar[(index + offset) % groups][arm][1] for offset in range(size))) for arm in ARMS}
        for index in range(groups)] for size in {5, groups % 5} if size}
    randomizer, draws = random.Random(0), []
    for _ in range(2000):
        checkpoint()
        sampled = [blocks[5][randomizer.randrange(groups)] for _ in range(groups // 5)]
        if groups % 5:
            sampled.append(blocks[groups % 5][randomizer.randrange(groups)])
        n = {arm: sum(block[arm][1] for block in sampled) for arm in ARMS}
        if all(n.values()):
            draws.append(math.fsum(block["15"][0] for block in sampled) / n["15"]
                - math.fsum(block["30"][0] for block in sampled) / n["30"])
    if len(draws) < 2:
        raise OpeningRangeComparisonRefusal("insufficient_valid_joint_bootstrap_draws")
    bootstrap = stdev(draws)
    design = max(naive, cluster, bootstrap)
    if any(not math.isfinite(value) or value < 0 for value in (naive, cluster, bootstrap, design)):
        raise OpeningRangeComparisonRefusal("invalid_joint_uncertainty")
    return {"known_labels_by_arm": counts, "known_label_sessions_by_arm": known_dates,
        "reference_sessions": groups, "naive_se": naive, "session_cluster_se": cluster,
        "bootstrap_se": bootstrap, "design_se": design,
        "corrected_to_naive_ratio": design / naive if naive else None,
        "bootstrap_valid_draws": len(draws), "bootstrap_skipped_zero_denominator_draws": 2000 - len(draws),
        "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
        "naive_reference": "unpaired_independent_arm_formula_only_not_an_independence_claim"}


def build_comparison(ct_report: Mapping, *, checkpoint: Callable[[], None] | None = None) -> dict:
    """Compare only supplied known labels after all four original sizing gates.

    Small invented source grids are supported. The actual caller separately
    enforces the source byte pins and its 257 dates/56 weeks/5140 target counts.
    """
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        records = _validate_source(ct_report, checkpoint)
    except (KeyError, TypeError, ValueError, ct.OpeningRangeRefusal, ct.cc.CalibrationRefusal) as exc:
        raise OpeningRangeComparisonRefusal("malformed_source_calibration") from exc
    result = deepcopy(ct_report)
    result["upstream_provenance"] = {key: deepcopy(value) for key, value in ct_report.items() if key != "variants"}
    result.update(study_id=SPECIFICATION_ID, mode=MODE, parameters=deepcopy(PARAMETERS),
        assumptions=dict(ASSUMPTIONS), operator_authorization=dict(AUTHORIZATION),
        upstream_sha256=UPSTREAM_SHA256, upstream_specification_sha256=UPSTREAM_SPECIFICATION_SHA256,
        **FLAGS)
    for policy, variant in result["variants"].items():
        checkpoint()
        by_arm, means = {}, {}
        for arm in ARMS:
            values = [row["label"] for row in records[policy][arm] if row["label"] is not None]
            n, total = len(values), sum(values, Fraction(0))
            mean = total / n
            own = deepcopy(variant["by_arm"][arm]["own_label_dispersion"])
            positive, net_positive = sum(value > 0 for value in values), sum(value > _COST for value in values)
            by_arm[arm] = {"known_labels": n, "gross_sum": total, "gross_mean": mean,
                "net_mean": mean - _COST, "assumed_cost": _COST,
                "gross_positive_count": positive, "gross_positive_fraction": Fraction(positive, n),
                "net_positive_count": net_positive, "net_positive_fraction": Fraction(net_positive, n),
                "own_label_dispersion": own,
                "descriptive_normal95_interval": _interval(mean, own["design_se"])}
            means[arm] = mean
        uncertainty = _uncertainty(records[policy], result["reference_sessions"], means, checkpoint)
        difference = means["15"] - means["30"]
        variant["comparison"] = {"by_arm": by_arm, "fifteen_minus_thirty": {
            "gross_mean_difference": difference, "net_mean_difference": difference,
            "fixed_cost_cancels_in_mean_difference": True, "uncertainty": uncertainty,
            "descriptive_normal95_interval": _interval(difference, uncertainty["design_se"])}}
    checkpoint()
    return result
