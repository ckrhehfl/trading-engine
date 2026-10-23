"""Recover only fixed DC unknown labels, then conditionally describe gross/net.

The caller supplies authenticated original chunks and bounded market responses.
This module performs no IO, authentication, HTTP, event detection or replacement.
Original signal states, labels and source vintages remain immutable provenance.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import math
import re
from statistics import NormalDist

from research import intraday_opening_retest_calibration as dc
from research import intraday_retest_gap_readiness as dd


ct, cc = dc.ct, dc.cc
ROOT = dc.ROOT
SPECIFICATION_ID = "intraday-retest-recovery-v1"
MODE = "fixed-retest-label-recovery-conditional-gross-net-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-retest-recovery-v1.json"
WINDOW = dict(dc.WINDOW)
SOURCE_REPORT_SHA256 = dd.INPUT_REPORT_SHA256
SOURCE_SPECIFICATION_SHA256 = dd.INPUT_SPEC_SHA256
INPUT_COMPRESSED_SHA256 = dc.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = dc.INPUT_STREAM_SHA256
REGISTERED_PATHS = {
    "source_report_role": "pinned_DC_calibration_report",
    "input_archive_role": "pinned_CC_canonical_raw_archive",
    "output_name": "intraday-de-retest-recovery-20261024-v1",
    "canonical_role": "existing_collector_experiment_log",
}
RESOURCES = {
    "maximum_source_bytes": 67_108_864, "maximum_compressed_bytes": 536_870_912,
    "maximum_uncompressed_bytes": 1_073_741_824, "maximum_chunk_bytes": 1_048_576,
    "maximum_chunks": 6000, "maximum_session_rows": 600,
    "maximum_recovery_coordinates": 2, "maximum_market_gets": 2,
    "maximum_rows_per_response": 120, "maximum_token_posts": 1,
    "minimum_free_bytes": 1_073_741_824, "cpu_seconds": 300,
    "address_space_mib": 512, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0,
}
PARAMETERS = {
    "source_parameters_sha256": hashlib.sha256(dc._canonical(dc.PARAMETERS)).hexdigest(),
    "recovery_scope": "union_original_known_signal_unknown_label_only_no_unknown_signal_recovery",
    "request": "one_date_addressable_KIS_page_per_coordinate_ending_original_exit_minute_second59_max120rows_fake_ticks_N_no_later_bucket",
    "required_prices": "all31_original_entry_through_exit_inclusive_valid_unique_minute_OHLC",
    "overlap": "every_existing_original_OHLC_in31bucket_window_exact_Decimal_equality_or_refuse",
    "amendment": "only_original_missing_buckets_no_overwrite_replacement_fill_or_event_redetection",
    "retention": "requested_at_KST_date_minus_one_calendar_year_Feb29_clipped_not_availability_certificate",
    "comparison_prerequisite": "all_requested_labels_recovered_and_both_policies_own_unknown0_defined_MDE_at_most33bp",
    "axis": "same_fixed_retest_signals_gross_versus_net_assumed33bp_per_event",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "uncertainty": "same_cc_dispersion_max_naive_session_cluster_circular5session_bootstrapSE_normal95percent_descriptive",
    "output_scope": "per_policy_total_and_original_reference_week_count_mean_median_win_cost_exceedance_no_subgroups_controls_or_portfolio",
    "underpower_exception_applied": False,
}
ASSUMPTIONS = {
    "source": "original_DC_full_target_population_flags_labels_metadata_and_unknown_signal_states_retained",
    "recovery": "current_KIS_retrieval_is_separate_vintage_not_historical_availability_finality_or_economic_unit_certificate",
    "overlap": "exact_existing_OHLC_agreement_is_required_consistency_evidence_not_full_price_basis_certification",
    "timing": "unchanged_DC_decision_entry_exit_and_assumed_label_availability_no_touch_or_actual_fill",
    "cost": "33bp_fixed_assumed_round_trip_per_event_net_equals_gross_minus33_over10000_no_historical_cost_certificate",
    "uncertainty": "whole_original_reference_sessions_include_zero_event_dates_maxSE_normal95percent_descriptive_not_confirmatory",
    "policies": "D+1_D+2_overlap_not_independent_replications_and_opportunities_are_not_executable_entries",
    "selection": "unknown_signals_and_repeated_search_history_remain_selection_bias_no_full_top20_population_claim",
    "claims": "no_matched_placebo_incremental_edge_portfolio_PnL_winner_strategy_pass_direction_closure_reserved_access_or_promotion",
}
POLICIES = ("D+1", "D+2")
ARM = "15_retest"
COST = Fraction(33, 10000)
PRICE_FIELDS = ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr")
_canonical = dc._canonical


class RecoveryRefusal(RuntimeError):
    """Stable reason codes exclude supplied values and private transport details."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "source_report_sha256": SOURCE_REPORT_SHA256,
        "source_specification_sha256": SOURCE_SPECIFICATION_SHA256,
        "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise RecoveryRefusal("specification_scope_mismatch")


def _same(left: object, right: object, reason: str) -> None:
    if _canonical(left) != _canonical(right):
        raise RecoveryRefusal(reason)


def _instant(value: object) -> datetime:
    try:
        return ct._instant(value).astimezone(cc.KST)
    except ct.OpeningRangeRefusal as exc:
        raise RecoveryRefusal("invalid_aware_clock") from exc


def _iso(value: datetime) -> str:
    return value.astimezone(cc.KST).isoformat()


def _ratio(value: object) -> Fraction:
    if isinstance(value, Fraction):
        return value
    try:
        if (not isinstance(value, Mapping) or set(value) != {"numerator", "denominator"}
                or any(not isinstance(value[key], str) for key in value)):
            raise ValueError
        result = Fraction(int(value["numerator"]), int(value["denominator"]))
        if str(result.numerator) != value["numerator"] or str(result.denominator) != value["denominator"]:
            raise ValueError
        return result
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise RecoveryRefusal("invalid_source_exact_ratio") from exc


def _source(report: Mapping) -> tuple[list[str], dict, dict]:
    try:
        _same(report["parameters"], dc.PARAMETERS, "source_parameters_changed")
        _same(report["assumptions"], dc.ASSUMPTIONS, "source_assumptions_changed")
        reference, rows, union = dd._source_rows(report)
        for policy in POLICIES:
            for row in rows[policy].values():
                arm = row["arms"][ARM]
                if arm["status"] not in {"signal", "known_no_signal", "unknown"}:
                    raise RecoveryRefusal("source_signal_state_changed")
                if arm["status"] == "signal":
                    dd._projection(row, dd._bucket_map(row["gaps"], row["session"]))
                    label = arm["label"]
                    decision = _instant(label["time_cap_decided_at"])
                    entry, exit_at = (_instant(label[key]) for key in ("entry_at", "exit_at"))
                    if (entry - decision != 3 * cc.MINUTE or exit_at - entry != 30 * cc.MINUTE
                            or decision != _instant(arm["event"]["decision_at"])
                            or _instant(label["entry_price_available_at"]) != entry + 3 * cc.MINUTE
                            or _instant(label["label_available_at"]) != exit_at + 3 * cc.MINUTE
                            or label["required_minute_buckets"] != 31):
                        raise RecoveryRefusal("source_label_clock_changed")
                    if label["status"] == "known":
                        _ratio(label["price_change"])
                    elif label["price_change"] is not None:
                        raise RecoveryRefusal("source_unknown_label_has_return")
                elif arm.get("label") is not None:
                    raise RecoveryRefusal("source_non_signal_has_label")
        for key, policies in union.items():
            first = rows[policies[0]][key]
            for policy in policies[1:]:
                if ({k: v for k, v in first.items() if not k.startswith("membership_")}
                        != {k: v for k, v in rows[policy][key].items() if not k.startswith("membership_")}):
                    raise RecoveryRefusal("source_shared_coordinate_changed")
        return reference, rows, union
    except (KeyError, TypeError, ValueError, dd.ReadinessRefusal) as exc:
        raise RecoveryRefusal("malformed_source_calibration") from exc


def _requests(rows: Mapping, union: Mapping) -> list[dict]:
    result = []
    for (day, symbol), policies in sorted(union.items()):
        row = rows[policies[0]][day, symbol]
        arm = row["arms"][ARM]
        if arm["status"] != "signal" or arm["label"]["status"] != "unknown":
            continue
        label = arm["label"]
        entry, exit_at = (_instant(label[key]) for key in ("entry_at", "exit_at"))
        gaps = {minute: state for minute, state in dd._bucket_map(row["gaps"], day).items()
            if entry <= minute <= exit_at}
        result.append({"session": day, "symbol": symbol, "policies": list(policies),
            "entry_at": _iso(entry), "exit_at": _iso(exit_at),
            "time_cap_decided_at": _iso(_instant(label["time_cap_decided_at"])),
            "required_minute_buckets": [_iso(entry + offset * cc.MINUTE) for offset in range(31)],
            "original_gap_intervals": dd._intervals(gaps),
            "original_missing_minute_buckets": [_iso(minute) for minute, state in sorted(gaps.items()) if state == "missing"],
            "request_parameters": {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol[4:],
                "FID_INPUT_DATE_1": day.replace("-", ""),
                "FID_INPUT_HOUR_1": exit_at.strftime("%H%M") + "59",
                "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N"}})
    if len(result) > RESOURCES["maximum_recovery_coordinates"]:
        raise RecoveryRefusal("recovery_coordinate_budget_exceeded")
    return result


def derive_requests(dc_report: Mapping) -> list[dict]:
    """Only known-signal/unknown-label coordinates; no outcome-driven selection.

    Small invented twenty-target session grids are supported. The actual caller
    also enforces two coordinates, 257 dates, 56 weeks and 525/522 fixed signals.
    """
    _, rows, union = _source(dc_report)
    return _requests(rows, union)


def _prices(row: Mapping, names: tuple[str, ...]) -> tuple[Decimal, ...]:
    try:
        values = tuple(Decimal(row[name]) for name in names if isinstance(row[name], str))
        if len(values) != 4 or any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError
        op, hi, lo, cl = values
        if not lo <= min(op, cl) <= max(op, cl) <= hi:
            raise ValueError
        return values
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise RecoveryRefusal("invalid_OHLC") from exc


def _source_session(chunk: Mapping, row: Mapping) -> cc.Session:
    if not isinstance(chunk, Mapping) or set(chunk) != {
            "symbol", "session", "observation_date", "available_at", "retrieved_at",
            "source", "is_final", "evidence_level", "rows"}:
        raise RecoveryRefusal("source_chunk_metadata_mismatch")
    if (chunk["symbol"], chunk["session"]) != (row["symbol"], row["session"]):
        raise RecoveryRefusal("source_chunk_coordinate_mismatch")
    _same({key: value for key, value in chunk.items() if key not in {"rows", "symbol", "session"}},
        row["raw_chunk_metadata"], "source_chunk_metadata_mismatch")
    raw = chunk["rows"]
    if (not isinstance(raw, list) or len(raw) != row["input_rows"]
            or len(raw) > RESOURCES["maximum_session_rows"]
            or any(not isinstance(item, Mapping) or set(item) != set(cc.COLUMNS)
                or item["symbol"] != row["symbol"] for item in raw)):
        raise RecoveryRefusal("source_chunk_rows_mismatch")
    if hashlib.sha256(_canonical(chunk) + b"\n").hexdigest() != row["data_vintage"]:
        raise RecoveryRefusal("source_chunk_vintage_mismatch")
    try:
        session = cc.aggregate_session(raw, cc.session_open(row["session"]))
        _same(session.gaps, row["gaps"], "source_chunk_gaps_mismatch")
        _same(ct._label(session, _instant(row["arms"][ARM]["event"]["decision_at"])),
            row["arms"][ARM]["label"], "source_chunk_original_label_mismatch")
    except (cc.CalibrationRefusal, ct.OpeningRangeRefusal) as exc:
        raise RecoveryRefusal("source_chunk_invalid") from exc
    return session


def _api_minutes(request: Mapping, envelope: object) -> tuple[dict, dict]:
    if envelope is None:
        raise RecoveryRefusal("api_unavailable")
    if (not isinstance(envelope, Mapping) or set(envelope) - {
            "requested_at", "retrieved_at", "status", "response", "reason"}
            or not {"requested_at", "retrieved_at", "status", "response"} <= set(envelope)):
        raise RecoveryRefusal("api_envelope_schema_mismatch")
    requested, retrieved = (_instant(envelope[key]) for key in ("requested_at", "retrieved_at"))
    if requested > retrieved or requested < _instant(request["exit_at"]) + 3 * cc.MINUTE:
        raise RecoveryRefusal("api_retrieval_clock_mismatch")
    today = requested.date()
    try:
        cutoff = today.replace(year=today.year - 1)
    except ValueError:
        cutoff = today.replace(year=today.year - 1, day=28)
    if not cutoff.isoformat() <= request["session"] <= today.isoformat():
        raise RecoveryRefusal("outside_documented_retention_horizon")
    if envelope["status"] == "unavailable" and envelope["response"] is None:
        raise RecoveryRefusal("api_unavailable")
    payload = envelope["response"]
    if (envelope["status"] != "received" or not isinstance(payload, Mapping)
            or set(payload) != {"rt_cd", "msg_cd", "output2"}
            or payload["rt_cd"] != "0" or not isinstance(payload["msg_cd"], str)):
        raise RecoveryRefusal("api_response_unsuccessful_or_schema_mismatch")
    raw = payload["output2"]
    if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_rows_per_response"]:
        raise RecoveryRefusal("api_response_row_budget_exceeded")
    minutes, duplicates, offsets = {}, 0, Counter()
    prior_dates, preopen = 0, 0
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != set(PRICE_FIELDS) | {"stck_bsop_date", "stck_cntg_hour"}:
            raise RecoveryRefusal("api_row_schema_mismatch")
        business_date = item["stck_bsop_date"]
        if (not isinstance(business_date, str) or len(business_date) != 8
                or not business_date.isascii() or not business_date.isdigit()):
            raise RecoveryRefusal("api_row_date_mismatch")
        clock = item["stck_cntg_hour"]
        if not isinstance(clock, str) or len(clock) != 6 or not clock.isascii() or not clock.isdigit():
            raise RecoveryRefusal("api_row_minute_clock_mismatch")
        try:
            parsed_day = datetime.strptime(business_date, "%Y%m%d").date()
        except ValueError as exc:
            raise RecoveryRefusal("api_row_date_mismatch") from exc
        try:
            stamp = datetime.strptime(business_date + clock, "%Y%m%d%H%M%S").replace(tzinfo=cc.KST)
        except ValueError as exc:
            raise RecoveryRefusal("api_row_minute_clock_mismatch") from exc
        minute = stamp.replace(second=0)
        offsets[str(stamp.second)] += 1
        if parsed_day.isoformat() < request["session"]:
            prior_dates += 1
            continue
        if parsed_day.isoformat() > request["session"]:
            raise RecoveryRefusal("api_row_date_mismatch")
        if minute < cc.session_open(request["session"]):
            preopen += 1
            continue
        if minute > _instant(request["exit_at"]):
            raise RecoveryRefusal("api_row_outside_requested_session_or_exit")
        prices = _prices(item, PRICE_FIELDS)
        if minute in minutes:
            if minutes[minute] != prices:
                raise RecoveryRefusal("api_duplicate_minute_conflict")
            duplicates += 1
        minutes[minute] = prices
    vintage = hashlib.sha256(_canonical(payload)).hexdigest()
    return minutes, {"response_rows": len(raw), "unique_minute_buckets": len(minutes),
        "identical_duplicate_rows": duplicates, "requested_at": envelope["requested_at"],
        "discarded_prior_date_rows": prior_dates, "discarded_preopen_rows": preopen,
        "source_offset_counts_seconds": dict(sorted(offsets.items(), key=lambda item: int(item[0]))),
        "retrieved_at": envelope["retrieved_at"], "retention_cutoff": cutoff.isoformat(),
        "response_sha256": vintage, "data_vintage": vintage,
        "observation_date": request["session"], "available_at": None,
        "source": "KIS date-addressable intraday market data", "is_final": None,
        "evidence_level": "assumed"}


def _recover(request: Mapping, chunk: Mapping, row: Mapping, envelope: object) -> dict:
    session = _source_session(chunk, row)  # Authenticated source mismatch fails the run.
    unavailable_reason = None
    if isinstance(envelope, Mapping) and envelope.get("status") == "unavailable" and "reason" in envelope:
        reason = envelope["reason"]
        unavailable_reason = reason if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason) else "invalid_unavailable_reason"
    diagnostic = {"session": request["session"], "symbol": request["symbol"],
        "policies": list(request["policies"]), "status": "unresolved", "reason": None,
        "original_label": deepcopy(row["arms"][ARM]["label"]), "recovered_label": None,
        "original_data_vintage": row["data_vintage"], "added_minute_buckets": [],
        "source_rows_overwritten": 0, "api_metadata": None,
        "response_status": envelope.get("status") if isinstance(envelope, Mapping) else "unavailable",
        "unavailable_reason": unavailable_reason}
    try:
        recovered, metadata = _api_minutes(request, envelope)
        diagnostic["api_metadata"] = metadata
        required = [_instant(value) for value in request["required_minute_buckets"]]
        if any(minute not in recovered for minute in required):
            raise RecoveryRefusal("api_required_window_incomplete")
        window_gaps = {minute: state for minute, state in dd._bucket_map(row["gaps"], request["session"]).items() if minute in required}
        if any(state != "missing" for state in window_gaps.values()):
            raise RecoveryRefusal("original_price_defect_not_missing")
        for minute in required:
            original = session.minutes[minute]
            if original is not None and original != recovered[minute]:
                raise RecoveryRefusal("source_vintage_or_price_unit_mismatch")
        missing = [minute for minute in required if session.minutes[minute] is None]
        if [_iso(minute) for minute in missing] != request["original_missing_minute_buckets"]:
            raise RecoveryRefusal("source_missing_window_changed")
        minutes = dict(session.minutes)
        minutes.update({minute: recovered[minute] for minute in missing})
        gaps = []
        missing_set = set(missing)
        for gap in session.gaps:
            minute = gap["start"]
            while minute < gap["end"]:
                if minute not in missing_set:
                    if gaps and gaps[-1]["state"] == gap["state"] and gaps[-1]["end"] == minute:
                        gaps[-1]["end"] += cc.MINUTE
                    else:
                        gaps.append({"start": minute, "end": minute + cc.MINUTE, "state": gap["state"]})
                minute += cc.MINUTE
        amended = cc.Session(session.opening, minutes, session.prefix, tuple(gaps), session.source_offsets_ms)
        label = ct._label(amended, _instant(request["time_cap_decided_at"]))
        if label["status"] != "known":
            raise RecoveryRefusal("recovered_label_still_unknown")
        diagnostic.update(status="recovered", recovered_label=label,
            added_minute_buckets=[_iso(minute) for minute in missing])
    except RecoveryRefusal as exc:
        diagnostic["reason"] = str(exc)
    return diagnostic


def _median(values: list[Fraction]) -> Fraction | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = len(ordered) // 2
    return ordered[index] if len(ordered) % 2 else (ordered[index - 1] + ordered[index]) / 2


def _summary(records: list[dict]) -> dict:
    values = [record["label"] for record in records]
    n = len(values)
    total = sum(values, Fraction(0))
    return {"event_count": n, "gross_sum": total, "net_sum": total - n * COST,
        "gross_mean": total / n if n else None, "net_mean": total / n - COST if n else None,
        "gross_median": _median(values), "net_median": _median([value - COST for value in values]),
        "gross_wins": sum(value > 0 for value in values),
        "net_wins": sum(value > COST for value in values),
        "cost_exceeding": sum(value > COST for value in values),
        "gross_win_fraction": Fraction(sum(value > 0 for value in values), n) if n else None,
        "net_win_fraction": Fraction(sum(value > COST for value in values), n) if n else None,
        "cost_exceedance_fraction": Fraction(sum(value > COST for value in values), n) if n else None,
        "assumed_cost": COST}


def build_recovery(dc_report: Mapping, source_chunks: Mapping, responses: Mapping,
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Preserve fixed signals, recover missing prices, size before any mean.

    Source/schema/pin errors refuse the run. Unavailable, incomplete, invalid or
    revised market responses yield unresolved diagnostics and no comparison.
    No underpower exception is applied to an unknown/failed new prerequisite.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    reference, rows, union = _source(dc_report)
    requests = _requests(rows, union)
    keys = {(item["session"], item["symbol"]) for item in requests}
    if not isinstance(source_chunks, Mapping) or set(source_chunks) != keys:
        raise RecoveryRefusal("source_chunk_population_mismatch")
    if not isinstance(responses, Mapping) or set(responses) != keys:
        raise RecoveryRefusal("response_population_mismatch")
    recoveries = []
    for request in requests:
        checkpoint()
        key = request["session"], request["symbol"]
        recoveries.append(_recover(request, source_chunks[key], rows[request["policies"][0]][key], responses[key]))
    repaired = {(item["session"], item["symbol"]): item for item in recoveries if item["status"] == "recovered"}
    complete = len(repaired) == len(requests)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS),
        **{key: False for key in dc.FLAGS}, "exploratory_only": True,
        "source_provenance": {key: value for key, value in dc_report.items() if key != "variants"},
        "source_target_matrix": dc_report["source_target_matrix"],
        "reference_sessions": list(reference), "reference_weeks": list(dc_report["reference_weeks"]),
        "requests": requests, "recovery": recoveries, "variants": {},
        "recovery_complete": complete, "comparison_performed": False,
        "comparison_not_run_reasons": [] if complete else ["requested_label_recovery_incomplete"],
        "underpower_exception_applied": False, "incremental_edge_claimed": False,
        "matched_control_comparison": False, "new_signals_calculated": False,
        "source_rows_overwritten": 0, "new_performance_families": 0,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
    for policy in POLICIES:
        checkpoint()
        days = [dict(row) for row in dc_report["variants"][policy]["days"]]
        records = []
        for row in days:
            arm = row["arms"][ARM]
            if arm["status"] != "signal":
                continue
            key = row["session"], row["symbol"]
            effective = deepcopy(repaired[key]["recovered_label"] if key in repaired else arm["label"])
            row["effective_label"] = effective
            records.append({"session": row["session"], "symbol": row["symbol"],
                "label": _ratio(effective["price_change"]) if effective["status"] == "known" else None,
                "original_label_status": arm["label"]["status"],
                "effective_label_status": effective["status"], "recovered": key in repaired})
        own = cc.dispersion(records, reference)
        checkpoint()
        reasons = []
        if own["unknown_labels"]:
            reasons.append("unknown_own_labels")
        if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
            reasons.append("undefined_own_label_sizing")
        elif own["mde_bps"] > 33:
            reasons.append("own_label_mde_exceeds_assumed_cost_floor")
        by_week = {week: {"signal_count": sum(ct._week(record["session"]) == week for record in records),
            "known_label_count": sum(ct._week(record["session"]) == week and record["label"] is not None for record in records)}
            for week in result["reference_weeks"]}
        result["variants"][policy] = {"days": days,
            "source_by_arm": dc_report["variants"][policy]["by_arm"], "records": records,
            "by_arm": {ARM: {"signal_counts": {state: sum(row["arms"][ARM]["status"] == state for row in days) for state in dc.STATES},
                "label_counts": {state: sum(record["effective_label_status"] == state for record in records) for state in ("known", "unknown")},
                "own_label_dispersion": own, "sizing_passed": not reasons,
                "requires_operator_choice_before_comparison": bool(reasons),
                "comparison_choice_reasons": reasons, "by_week": by_week,
                "frequency": {"reference_sessions": len(reference), "reference_weeks": len(by_week),
                    "signals_per_reference_week": Fraction(len(records), len(by_week)),
                    "known_labels_per_reference_week": Fraction(own["known_labels"], len(by_week)),
                    "weekly_operating_objective": 15, "forced_trades": False,
                    "executable_frequency_certified": False}}}, "comparison": None}
        result["comparison_not_run_reasons"].extend(policy + ":" + reason for reason in reasons)
    if not result["comparison_not_run_reasons"]:
        for variant in result["variants"].values():
            checkpoint()
            records = variant["records"]
            own = variant["by_arm"][ARM]["own_label_dispersion"]
            summary = _summary(records)
            width = NormalDist().inv_cdf(.975) * own["design_se"]
            summary.update(gross_descriptive_interval={"lower": float(summary["gross_mean"]) - width,
                "upper": float(summary["gross_mean"]) + width},
                net_descriptive_interval={"lower": float(summary["net_mean"]) - width,
                    "upper": float(summary["net_mean"]) + width},
                uncertainty=deepcopy(own),
                by_week={week: _summary([record for record in records if ct._week(record["session"]) == week])
                    for week in result["reference_weeks"]},
                label_origin={origin: {"event_count": sum(record["recovered"] is recovered for record in records),
                    "gross_sum": sum((record["label"] for record in records if record["recovered"] is recovered), Fraction(0)),
                    "net_sum": sum((record["label"] - COST for record in records if record["recovered"] is recovered), Fraction(0))}
                    for origin, recovered in (("original_known", False), ("recovered", True))})
            variant["comparison"] = summary
        result["comparison_performed"] = True
        result["new_performance_families"] = 1
    result["requires_operator_choice_before_comparison"] = bool(result["comparison_not_run_reasons"])
    checkpoint()
    return result
