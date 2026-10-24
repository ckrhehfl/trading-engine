"""Retrospective thirty-minute paths on DI's unchanged basic population.

This pure builder consumes a caller-authenticated completed report and saved
chunks. It neither detects signals nor re-estimates an upstream comparison.
Minute extrema and touches are descriptions, never executable exit policies.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import math

from research import intraday_opening_volatility_comparison as di


ct, cu, cc = di.ct, di.cu, di.cc
ROOT = di.ROOT
SPECIFICATION_ID = "intraday-breakout-path-diagnostics-v1"
MODE = "fixed-basic-opening-breakout-thirty-minute-path-diagnostic"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-breakout-path-diagnostics-v1.json"
OUTPUT_NAME = "intraday-dj-breakout-paths-20261024-v1"
WINDOW = dict(di.WINDOW)
POLICIES = di.POLICIES
COST = Fraction(33, 10000)
UPSTREAM_SPECIFICATION_SHA256 = "d5b6313df3422735cd964b4c8c8ada72dd8b96f11fea617e2ae7ac152a396647"
SOURCE_ROLES = {"parent": "pinned_completed_full_DI_opening_volatility_report",
    "archive": "pinned_original_CC_saved_raw_minute_archive"}
RESOURCES = {**di.RESOURCES, "maximum_compressed_bytes": ct.RESOURCES["maximum_compressed_bytes"],
    "maximum_uncompressed_bytes": ct.RESOURCES["maximum_uncompressed_bytes"],
    "maximum_chunk_bytes": ct.RESOURCES["maximum_chunk_bytes"],
    "maximum_session_rows": ct.RESOURCES["maximum_session_rows"],
    "maximum_chunks": 5158, "maximum_rows": 1002211}
PARAMETERS = {
    "cohort": "unchanged_DI_inherited_DH_common_outcome_complete_basic_signals_not_volatility_subset",
    "entry_delay_minutes": 3, "path_minutes": 30,
    "path": "all_valid_minute_OHLC_entry_inclusive_entry_plus30minutes_exclusive",
    "endpoint": "original_entry_plus30minute_OPEN_label_with_exact_raw_anchor_checks",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "crossings": "strict_high_above_entry_times1pluscost_strict_low_below_entry_times1minuscost_equality_not_exceed",
    "categories": ["never_exceeded", "exceeded_then_faded", "exceeded_and_retained", "path_unknown"],
    "endpoint_flags": "orthogonal_endpoint_above_cost_and_complete_no_touch_cap_only_exceeded",
    "missingness": "incomplete_path_kept_unknown_original_known_endpoint_retained_never_zero_or_no_touch",
    "extrema": "exact_MFE_max_high_over_entry_minus1_MAE_min_low_over_entry_minus1_giveback_MFE_minus_original_endpoint",
    "timing": "earliest_extreme_and_strict_crossing_minute_offsets_same_minute_order_unknown",
    "summaries": "exact_Fraction_complete_path_descriptions_counts_fractions_all_original_reference_weeks",
    "own_sizing_gate": "not_a_new_directional_payoff_comparison_no_extremum_MDE_test_no_later_policy_waiver",
    "output_scope": "retrospective_path_diagnostic_no_optimized_exit_payoff_family_subgroups_or_pvalue",
}
ASSUMPTIONS = {
    "source": "completed_DI_parent_trusted_via_caller_pin_no_upstream_signal_or_statistic_replay",
    "population": "all_original_DI_targets_masks_labels_unknowns_and_volatility_features_retained_fixed_basic_scope",
    "availability": "path_available_at_end_plus2minutes_assumed_retrospective_label_never_selection_information",
    "metadata": "raw_observation_retrieval_unknown_finality_assumed_evidence_and_exact_vintage_preserved",
    "touch": "minute_high_low_envelopes_not_fills_no_intraminute_order_or_executable_exit_claim",
    "endpoint": "cap_minute_OPEN_is_outside_path_endpoint_above_cost_does_not_imply_path_touch",
    "power": "hindsight_extremum_description_is_not_a_directional_payoff_test_later_policies_require_own_gates",
    "claims": "no_optimized_policy_PnL_edge_qualification_direction_closure_reserved_access_or_promotion",
}
FLAGS = {**ct.FLAGS, "calibration_only": False, "diagnostic_only": True,
    "hindsight_extrema": True, "new_signals_calculated": False,
    "new_labels_calculated": False, "raw_replay_performed": True,
    "upstream_statistics_recalculated": False, "comparison_performed": False,
    "new_performance_families": 0, "exit_policy_tested": False,
    "earlier_entry_policy_tested": False, "source_rows_overwritten": 0}
PROVENANCE_EXCLUDE = di.PROVENANCE_EXCLUDE
CATEGORIES = tuple(PARAMETERS["categories"])
ORDER_CLASSES = ("up_only", "down_only", "up_before_down", "down_before_up",
    "same_minute_order_unknown", "neither")
METADATA = ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")
METRICS = ("mfe", "mae", "giveback", "endpoint_price_change")
_canonical = ct._canonical


class BreakoutPathRefusal(RuntimeError):
    """Stable refusal reasons omit supplied private values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "output_name": OUTPUT_NAME,
        "window": WINDOW, "source_roles": SOURCE_ROLES,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "parameters": PARAMETERS, "resources": RESOURCES}
    if _canonical(spec) != _canonical(expected):
        raise BreakoutPathRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise BreakoutPathRefusal(reason)


def _eligible(row: Mapping) -> bool:
    return row["outcome_complete"] and row["arms"]["basic"]["status"] == "signal"


def _source(source: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    if (source["study_id"] != di.SPECIFICATION_ID or source["mode"] != di.MODE
            or source["specification_sha256"] != UPSTREAM_SPECIFICATION_SHA256
            or source["upstream_specification_sha256"] != di.UPSTREAM_SPECIFICATION_SHA256):
        raise BreakoutPathRefusal("source_identity_or_specification_changed")
    for name, expected in (("window", di.WINDOW), ("parameters", di.PARAMETERS),
            ("assumptions", di.ASSUMPTIONS)):
        _same(source[name], expected, "source_scope_changed")
    expected_flags = {**ct.FLAGS, "calibration_only": False, "comparison_performed": True,
        "requires_operator_choice_before_comparison": False, "new_performance_families": 1, **di._CLAIMS}
    for name, expected in expected_flags.items():
        if type(source.get(name)) is not type(expected) or source[name] != expected:
            raise BreakoutPathRefusal("source_claim_or_choice_flag_changed")
    _same(source["comparison_not_run_reasons"], [], "source_comparison_not_completed")
    if source["pin_authentication"] != "caller_obligation_not_proven_by_pure_builder":
        raise BreakoutPathRefusal("source_pin_claim_changed")
    # Validate DH's original projection, counts, masks and clocks without its
    # detector, sizing, bootstrap or price-comparison routines.
    dh_projection = {**source["source_provenance"],
        "source_target_matrix": source["source_target_matrix"], "upstream_calendar": source["upstream_calendar"],
        "variants": {policy: {**variant["source_variant_provenance"],
            "days": [{key: value for key, value in row.items() if key != "opening_volatility_feature"}
                for row in variant["days"]]} for policy, variant in source["variants"].items()}}
    reference = di._source(dh_projection, checkpoint)
    _same(source["reference_sessions"], reference, "source_reference_sessions_changed")
    weeks = sorted({ct._week(day) for day in reference})
    _same(source["reference_weeks"], weeks, "source_reference_weeks_changed")
    for policy in POLICIES:
        variant = source["variants"][policy]
        rows = variant["days"]
        for row in rows:
            checkpoint()
            _same(row["opening_volatility_feature"], di._feature(row), "source_volatility_feature_changed")
        common = [row for row in rows if row["outcome_complete"]]
        selected = [row for row in common if row["opening_volatility_feature"]["status"] == "selected"]
        for name in ("full_source", "common_signal_observable", "common_outcome_complete"):
            _same(variant["cohorts"][name], variant["source_variant_provenance"]["cohorts"][name],
                "source_inherited_cohort_changed")
        _same(variant["cohorts"]["volatility_selected"], di.dh._cohort(selected, reference, weeks),
            "source_selected_cohort_changed")
        expected_counts = {"full_source": di._selection_counts(rows),
            "common_outcome_complete": di._selection_counts(common),
            "by_week": {week: {"full_source": di._selection_counts([row for row in rows if ct._week(row["session"]) == week]),
                "common_outcome_complete": di._selection_counts([row for row in common if ct._week(row["session"]) == week])}
                for week in weeks}}
        _same(variant["selection_counts"], expected_counts, "source_selection_counts_changed")
        if set(variant["by_arm"]) != set(di.ARMS) or variant["comparison"] is None:
            raise BreakoutPathRefusal("source_comparison_not_completed")
        for arm, population in (("basic", [row for row in common if _eligible(row)]), ("volatility_selected", selected)):
            own = variant["by_arm"][arm]
            mde = own["own_label_dispersion"]["mde_bps"]
            if (own["sizing_passed"] is not True or own["comparison_choice_reasons"]
                    or mde is None or not math.isfinite(mde) or not 0 <= mde <= 33):
                raise BreakoutPathRefusal("source_incomplete_own_sizing")
            _same(own["frequency"], di._frequency(population, reference, weeks), "source_frequency_changed")
    return reference


def _minutes(raw: list, day: str, symbol: str, checkpoint: Callable[[], None]) -> tuple[dict, Counter, dict]:
    """Validate every supplied row, retaining duplicate/invalid buckets unknown."""
    opening, closing = cc.session_open(day), cc.session_close(day)
    minutes, offsets, reasons = {}, Counter(), {}
    seen = set()
    for index, row in enumerate(raw):
        if index % 128 == 0:
            checkpoint()
        stamp = row.get("open_time_ms")
        if type(stamp) is not int:
            raise BreakoutPathRefusal("invalid_raw_timestamp")
        minute = datetime.fromtimestamp(stamp // 1000, cc.KST).replace(second=0, microsecond=0)
        if row.get("symbol") != symbol or row.get("interval") != "1m" or not opening <= minute < closing:
            raise BreakoutPathRefusal("raw_row_outside_registered_scope")
        offsets[str(stamp % 60000)] += 1
        if minute in seen:
            minutes[minute] = None
            reasons[minute] = "conflict"
            continue
        seen.add(minute)
        prices = None
        try:
            text = tuple(row[name] for name in ("open", "high", "low", "close"))
            if not all(isinstance(value, str) for value in text):
                raise ValueError
            values = tuple(Decimal(value) for value in text)
            op, high, low, close = values
            if not all(value.is_finite() and value > 0 for value in values) or not low <= min(op, close) <= max(op, close) <= high:
                raise ValueError
            prices = values
        except (KeyError, ValueError, InvalidOperation):
            reasons[minute] = "invalid_price"
        minutes[minute] = prices
    return minutes, offsets, reasons


def _diagnostic(row: Mapping, minutes: Mapping, reasons: Mapping | None = None) -> dict:
    label = row["arms"]["basic"]["label"]
    entry, end = (cu._instant(label[name]) for name in ("entry_at", "exit_at"))
    entry_price, endpoint = (cu._positive_price(label[name]) for name in ("entry_open", "exit_open"))
    if (minutes.get(entry) is None or minutes.get(end) is None
            or Fraction(minutes[entry][0]) != entry_price or Fraction(minutes[end][0]) != endpoint):
        raise BreakoutPathRefusal("source_entry_or_endpoint_raw_anchor_mismatch")
    terminal = cu._ratio(label["price_change"])
    if terminal != endpoint / entry_price - 1:
        raise BreakoutPathRefusal("source_endpoint_label_mismatch")
    path = [(offset, minutes.get(entry + offset * cc.MINUTE)) for offset in range(30)]
    missing = sum(prices is None for _, prices in path)
    reasons = reasons or {}
    gap_counts = Counter(reasons.get(entry + offset * cc.MINUTE, "missing") for offset, prices in path if prices is None)
    result = {"status": "path_unknown" if missing else "complete", "path_known": not missing,
        "entry_at": entry, "end_at": end, "path_available_at": end + 2 * cc.MINUTE,
        "entry_open": entry_price, "endpoint_open": endpoint, "endpoint_price_change": terminal,
        "endpoint_above_cost": terminal > COST, "missing_minute_buckets": missing,
        "unknown_bucket_reason_counts": {name: gap_counts[name] for name in ct.GAP_STATES},
        "category": "path_unknown", "cap_only_exceeded": None,
        "data_vintage": row["data_vintage"], "raw_chunk_metadata": deepcopy(row["raw_chunk_metadata"]),
        "maximum_high": None, "minimum_low": None, "maximum_high_minute": None, "minimum_low_minute": None,
        "maximum_high_offset_minutes": None, "minimum_low_offset_minutes": None,
        "mfe": None, "mae": None, "giveback": None,
        "first_cost_up_minute": None, "first_cost_down_minute": None,
        "first_cost_up_offset_minutes": None, "first_cost_down_offset_minutes": None,
        "cost_touch_order": None, "mfe_exceeds_cost": None}
    if missing:
        return result
    anchor = minutes[entry][0]
    with localcontext() as context:
        context.prec = len(anchor.as_tuple().digits) + 8
        upper, lower = anchor * Decimal("1.0033"), anchor * Decimal("0.9967")
    maximum, minimum = max(path, key=lambda item: item[1][1]), min(path, key=lambda item: item[1][2])
    up = next((offset for offset, prices in path if prices[1] > upper), None)
    down = next((offset for offset, prices in path if prices[2] < lower), None)
    order = ("neither" if up is None and down is None else "down_only" if up is None
        else "up_only" if down is None else "same_minute_order_unknown" if up == down
        else "up_before_down" if up < down else "down_before_up")
    mfe, mae = Fraction(maximum[1][1]) / entry_price - 1, Fraction(minimum[1][2]) / entry_price - 1
    result.update(category="never_exceeded" if up is None else "exceeded_then_faded" if terminal <= COST else "exceeded_and_retained",
        cap_only_exceeded=up is None and terminal > COST, maximum_high=Fraction(maximum[1][1]), minimum_low=Fraction(minimum[1][2]),
        maximum_high_minute=entry + maximum[0] * cc.MINUTE, minimum_low_minute=entry + minimum[0] * cc.MINUTE,
        maximum_high_offset_minutes=maximum[0], minimum_low_offset_minutes=minimum[0], mfe=mfe, mae=mae, giveback=mfe-terminal,
        first_cost_up_minute=None if up is None else entry + up * cc.MINUTE,
        first_cost_down_minute=None if down is None else entry + down * cc.MINUTE,
        first_cost_up_offset_minutes=up, first_cost_down_offset_minutes=down,
        cost_touch_order=order, mfe_exceeds_cost=up is not None)
    return result


def _describe(values: list) -> dict:
    known = sorted(Fraction(value) for value in values if value is not None)
    count = len(known)
    return {"count": count, "mean": sum(known, Fraction()) / count if count else None,
        "median": (known[(count-1)//2] + known[count//2]) / 2 if count else None,
        "min": known[0] if count else None, "max": known[-1] if count else None}


def _count(count: int, denominator: int) -> dict:
    return {"count": count, "fraction": Fraction(count, denominator) if denominator else None}


def _summary(diagnostics: list[dict]) -> dict:
    total = len(diagnostics)
    complete = [item for item in diagnostics if item["path_known"]]
    unknown = [item for item in diagnostics if not item["path_known"]]
    counts = Counter(item["category"] for item in diagnostics)
    orders = Counter(item["cost_touch_order"] for item in complete)
    return {"total_cases": total, "complete_paths": len(complete), "incomplete_paths": len(unknown),
        "categories": {name: _count(counts[name], total) for name in CATEGORIES},
        "endpoint_above_cost": {name: _count(sum(item["endpoint_above_cost"] for item in values), len(values))
            for name, values in (("all", diagnostics), ("complete", complete), ("path_unknown", unknown))},
        "cap_only_exceeded": _count(sum(item["cap_only_exceeded"] for item in complete), len(complete)),
        "metrics": {name: _describe([item[name] for item in complete]) for name in METRICS},
        "extreme_offsets_minutes": {name: _describe([item[name + "_offset_minutes"] for item in complete])
            for name in ("maximum_high", "minimum_low")},
        "first_crossing_offsets_minutes": {direction: _describe([item["first_cost_" + direction + "_offset_minutes"] for item in complete])
            for direction in ("up", "down")},
        "cost_touch_orders": {name: _count(orders[name], len(complete)) for name in ORDER_CLASSES}}


def build_diagnostics(di_report: Mapping, raw_chunks: Mapping | Iterable[Mapping],
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Stream original chunks; diagnose every fixed basic entry without dropping gaps.

    Smaller invented populations are supported. The authenticated private caller
    enforces actual pins, 257 sessions, 56 weeks and original 5140-target grids.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference = _source(di_report, checkpoint)
    except (KeyError, TypeError, ValueError, OverflowError, di.OpeningVolatilityComparisonRefusal,
            di.dh.BreakoutRetestComparisonRefusal, cu.OpeningRangeComparisonRefusal,
            ct.OpeningRangeRefusal, di.de.RecoveryRefusal, cc.CalibrationRefusal) as exc:
        raise BreakoutPathRefusal("malformed_or_mismatched_DI_source") from exc
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "source_provenance": {key: deepcopy(value) for key, value in di_report.items() if key not in PROVENANCE_EXCLUDE},
        "source_target_matrix": deepcopy(di_report["source_target_matrix"]),
        "upstream_calendar": deepcopy(di_report["upstream_calendar"]),
        "reference_sessions": list(reference), "reference_weeks": list(di_report["reference_weeks"]), "variants": {},
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
    by_key = {}
    for policy in POLICIES:
        variant = di_report["variants"][policy]
        days = []
        for original in variant["days"]:
            row = {**deepcopy(original), "breakout_path_diagnostic": None}
            days.append(row)
            by_key.setdefault((row["session"], row["symbol"]), []).append(row)
        result["variants"][policy] = {"days": days,
            "source_variant_provenance": {key: deepcopy(value) for key, value in variant.items() if key != "days"}}
    expected = sorted(by_key)
    if len(expected) > RESOURCES["maximum_chunks"]:
        raise BreakoutPathRefusal("input_chunk_budget_exceeded")
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise BreakoutPathRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    rows_count = 0
    for day, symbol in expected:
        checkpoint()
        try:
            chunk = next(chunks)
        except StopIteration as exc:
            raise BreakoutPathRefusal("missing_input_chunk") from exc
        if not isinstance(chunk, Mapping) or chunk.get("session") != day or chunk.get("symbol") != symbol:
            raise BreakoutPathRefusal("input_chunk_order_or_identity_mismatch")
        if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                or chunk.get("evidence_level") != "assumed"):
            raise BreakoutPathRefusal("input_chunk_metadata_mismatch")
        cu._instant(chunk.get("retrieved_at"))
        raw = chunk.get("rows")
        if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
            raise BreakoutPathRefusal("input_session_row_budget_exceeded")
        rows_count += len(raw)
        if rows_count > RESOURCES["maximum_rows"]:
            raise BreakoutPathRefusal("input_row_budget_exceeded")
        vintage = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
        metadata = {name: chunk[name] for name in METADATA}
        minutes, offsets, reasons = _minutes(raw, day, symbol, checkpoint)
        for row in by_key[day, symbol]:
            if row["data_vintage"] != vintage or row["input_rows"] != len(raw):
                raise BreakoutPathRefusal("source_raw_vintage_or_count_mismatch")
            _same(row["raw_chunk_metadata"], metadata, "source_raw_metadata_mismatch")
            _same(row["source_offset_counts_ms"], dict(offsets), "source_raw_offsets_mismatch")
            if _eligible(row):
                row["breakout_path_diagnostic"] = _diagnostic(row, minutes, reasons)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise BreakoutPathRefusal("extra_input_chunk")
    result["input_evidence"] = {"unique_target_days": len(expected), "queried_rows": rows_count}
    for variant in result["variants"].values():
        checkpoint()
        rows = [row for row in variant["days"] if row["breakout_path_diagnostic"] is not None]
        variant["summary"] = {"overall": _summary([row["breakout_path_diagnostic"] for row in rows]),
            "by_week": {week: _summary([row["breakout_path_diagnostic"] for row in rows if ct._week(row["session"]) == week])
                for week in result["reference_weeks"]}}
    checkpoint()
    return result
