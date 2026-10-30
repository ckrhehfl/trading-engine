"""Independent, source-only EC input checks; no IO, labels, or policy replay.

Call ``verify_features(prepared, **original_prepare_features_arguments)`` and
``verify_volume_sidecar(sidecar, original_eb_a_report,
inputs_authenticated=True)``. Originals must be the caller-authenticated full
inputs, not values extracted from the output being checked. These receipts
certify reconstruction within that supplied scope only. Readiness publication
also requires verify_readiness_projection while originals are alive, then
ReadinessAccumulator.add_day/add_row/finish_day and verify_report. Its add_row
combines raw, typed, compact, and support checks in one reconstruction. Pins,
reviewed builder identity, raw-to-Session parsing, EOF, stored output hashes,
and execution gates remain caller obligations. Stored EB volume has neither
minute-level provenance nor available_at: this verifier cannot recertify it.

Only shared record types/constants and the registered session clock are reused.
No feature producer, old peer mean, volume projector, detector, or outcome
calculation supplies the independent arithmetic below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import re

from research import intraday_context_preparation as ea
from research import intraday_pattern_calibration as cc
from research.intraday_campaign_features import PreparedFeatures, WindowObservation
from research.intraday_context_experiment import PRICE_BASIS, SOURCE_FIELDS


MINUTE = timedelta(minutes=1)
PEER_WINDOWS = ("first30", "open_to_signal", "last15")
INPUT_NAMES = (
    "setup_is_A", "fraction_of_session_elapsed", "target_open_to_signal_return",
    "target_last15_return", "structural_stop_gap_from_signal_close",
    "trigger_close_location", "peer_open_to_signal_return", "peer_last15_return",
    "peer_first30_return", "common_peer_count_divided_by_original_peer_count",
)
POLICIES = ("D+1", "D+2")
SIGNAL_CLOCKS = ("decision_at", "observed_through", "signal_bar_start",
                 "signal_bar_end", "setup_bar_end")
VOLUME_LIMITATIONS = (
    "source_only_preparation_not_registered_or_executable_policy",
    "original_report_hash_and_reviewed_builder_contract_authenticated_by_caller",
    "stored_relative_volume_has_no_minute_provenance_or_available_at",
    "volume_availability_not_reauthenticated_from_report",
    "observed_signal_and_membership_clocks_only",
    "no_old_outcome_return_other_feature_or_common_observable_flag_read",
    "no_price_recomputation_account_replay_or_performance_result",
)
SUPPORT_NAMES = ("P04_volume_inputs", "P05_market_inputs", "P06_volume_and_market_inputs",
                 "P07_market_inputs", "P08_router_inputs", "P09_P10_all_ten_inputs")
READINESS_LIMITATIONS = (
    "caller_authenticates_whole_original_json_including_old_labels",
    "old_label_metadata_inspected_only_by_existing_report_integrity_validation",
    "no_outcome_price_return_or_old_common_mask_for_feature_admission",
    "new_minimum_five_common_observed_peer_proxy_not_old_ea_eb_or_official_index",
    "end_plus_two_minute_availability_assumption_not_historical_certification",
    "retrieval_is_not_historical_publication_or_finality_certification",
    "saved_krw_prices_corporate_action_comparability_uncertified",
    "stored_a_volume_availability_cannot_be_recertified_from_report",
    "feature_support_only_no_labels_models_policy_selection_or_performance",
    "all_original_targets_unknowns_zero_signal_dates_and_weeks_retained",
)


class InputVerificationRefusal(ValueError):
    """An invalid original or a mismatched output, with its structural path."""


def _need(condition: bool, path: str, reason: str) -> None:
    if not condition:
        raise InputVerificationRefusal(f"{path}: {reason}")


def _same(actual: object, expected: object, path: str) -> None:
    """Compare every field without producer equality or numeric coercion."""
    if is_dataclass(expected) and not isinstance(expected, type):
        _need(type(actual) is type(expected), path, "record_type_changed")
        for field in fields(expected):
            _same(getattr(actual, field.name), getattr(expected, field.name), f"{path}.{field.name}")
    elif isinstance(expected, Mapping):
        _need(isinstance(actual, Mapping), path, "mapping_required")
        _need(set(actual) == set(expected), path, "mapping_fields_changed")
        for key, value in expected.items():
            _same(actual[key], value, f"{path}[{key!r}]")
    elif type(expected) in (tuple, list):
        _need(type(actual) is type(expected), path, "sequence_type_changed")
        _need(len(actual) == len(expected), path, "sequence_length_changed")
        for index, value in enumerate(expected):
            _same(actual[index], value, f"{path}[{index}]")
    else:
        _need(type(actual) is type(expected) and actual == expected, path, "value_or_type_changed")


def _map(value: object, path: str) -> Mapping:
    _need(isinstance(value, Mapping), path, "mapping_required")
    return value


def _instant(value: object, path: str, *, session: bool = False) -> datetime:
    _need(type(value) is datetime and value.utcoffset() is not None, path, "aware_clock_required")
    _need(not session or value.utcoffset() == timedelta(hours=9), path, "kst_clock_required")
    return value


def _parse_clock(value: object, path: str, day: str | None = None) -> datetime:
    _need(type(value) is str, path, "clock_string_required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InputVerificationRefusal(f"{path}: invalid_clock") from None
    _instant(parsed, path)
    _need(day is None or parsed.astimezone(cc.KST).date().isoformat() == day,
          path, "clock_session_changed")
    return parsed


def _symbol(value: object, path: str) -> str:
    _need(type(value) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", value) is not None,
          path, "invalid_symbol")
    return value


def _day(value: object, path: str) -> str:
    _need(type(value) is str, path, "date_string_required")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise InputVerificationRefusal(f"{path}: invalid_date") from None
    _need(parsed.isoformat() == value, path, "noncanonical_date")
    return value


def _metadata(meta: object, end: datetime, decision: datetime, path: str) -> None:
    _need(type(meta) is ea.Provenance, path, "provenance_required")
    for name in ("observation_end", "available_at", "retrieved_at"):
        _instant(getattr(meta, name), f"{path}.{name}")
    _need(meta.observation_end == end and end <= meta.available_at <= decision,
          path, "observation_or_availability_changed")
    _need(meta.retrieved_at >= end, path, "retrieval_precedes_observation")
    _need(all(type(item) is str and bool(item.strip()) for item in (meta.source, meta.data_vintage)),
          path, "source_and_vintage_required")
    _need(meta.is_final is None or type(meta.is_final) is bool, path, "invalid_finality")
    _need(type(meta.evidence_level) is str
          and meta.evidence_level in {"synthetic", "observed", "assumed", "unknown"},
          path, "invalid_evidence_level")


def _receipt(source: object, symbol: str, target: ea.Target) -> tuple[Mapping, ea.PriceSource]:
    path = f"original.sources[{symbol!r}]"
    source = _map(source, path)
    _need(set(source) == {"session", "symbol", "data_vintage", "raw_chunk_metadata", "input_rows",
                         "row_linkage_sha256", "source_row_metadata_location"}, path, "receipt_fields_changed")
    _same(source["symbol"], symbol, f"{path}.symbol")
    _same(source["session"], target.session_date.isoformat(), f"{path}.session")
    for name in ("data_vintage", "row_linkage_sha256"):
        _need(type(source[name]) is str and re.fullmatch(r"[0-9a-f]{64}", source[name]) is not None,
              f"{path}.{name}", "invalid_digest")
    _need(type(source["input_rows"]) is int and 0 <= source["input_rows"] <= 600,
          f"{path}.input_rows", "invalid_row_count")
    _same(source["source_row_metadata_location"], "original_pinned_archive_chunk", path)
    raw = _map(source["raw_chunk_metadata"], f"{path}.raw_chunk_metadata")
    _need(set(raw) == set(SOURCE_FIELDS), path, "source_metadata_fields_changed")
    for name, expected in {"observation_date": target.session_date.isoformat(), "available_at": None,
                           "source": "collector klines", "is_final": None, "evidence_level": "assumed"}.items():
        _same(raw[name], expected, f"{path}.raw_chunk_metadata.{name}")
    retrieved = _parse_clock(raw["retrieved_at"], f"{path}.raw_chunk_metadata.retrieved_at")
    return source, ea.PriceSource(retrieved, raw["source"], raw["is_final"], source["data_vintage"], "assumed")


def _original_feature_contract(target: ea.Target, universe: ea.MonthlyUniverse,
                               sessions: Mapping, sources: Mapping, observed_end: datetime,
                               decision_at: datetime) -> tuple[tuple[str, ...], dict, dict]:
    _need(type(target) is ea.Target and type(universe) is ea.MonthlyUniverse,
          "original", "typed_target_and_universe_required")
    _symbol(target.symbol, "original.target.symbol")
    _need(type(target.session_date) is date and type(target.policy) is str
          and target.policy in POLICIES, "original.target", "invalid_date_or_policy")
    for name, value in (("opening", target.opening), ("closing", target.closing),
                        ("observed_end", observed_end), ("decision_at", decision_at)):
        _instant(value, f"original.{name}", session=True)
    day = target.session_date.isoformat()
    _need(target.opening == cc.session_open(day) and target.closing == cc.session_close(day),
          "original.target", "original_session_clock_changed")
    _need(target.opening + 30 * MINUTE <= observed_end < decision_at < target.closing
          and (observed_end - target.opening) % (5 * MINUTE) == timedelta(0)
          and decision_at - observed_end == 2 * MINUTE,
          "original.signal", "original_signal_clock_changed_or_future_first30")
    _need(type(universe.month) is date and universe.month == target.session_date.replace(day=1),
          "original.universe.month", "membership_month_changed")
    _need(type(universe.members) is tuple, "original.universe.members", "tuple_required")
    for member in universe.members:
        _symbol(member, "original.universe.members")
    _need(len(universe.members) == len(set(universe.members)) and target.symbol in universe.members,
          "original.universe.members", "duplicate_or_missing_target")
    _instant(universe.fixed_at, "original.universe.fixed_at")
    _need(universe.fixed_at <= target.opening, "original.universe.fixed_at", "membership_after_open")
    _need(type(universe.provenance) is ea.Provenance, "original.universe.provenance", "provenance_required")
    observed = _instant(universe.provenance.observation_end, "original.universe.observation_end")
    _need(observed.astimezone(cc.KST).date() < universe.month,
          "original.universe.observation_end", "formation_not_before_month")
    _metadata(universe.provenance, observed, universe.fixed_at, "original.universe.provenance")
    _need(isinstance(sessions, Mapping) and isinstance(sources, Mapping)
          and set(universe.members) <= set(sessions) and set(universe.members) <= set(sources),
          "original", "full_original_day_slots_required")
    receipts, identities = {}, {}
    for symbol in universe.members:
        receipts[symbol], identities[symbol] = _receipt(sources[symbol], symbol, target)
        session = sessions[symbol]
        if session is None:
            continue
        _need(type(session) is cc.Session and isinstance(session.minutes, Mapping)
              and type(session.gaps) is tuple, f"original.sessions[{symbol!r}]", "invalid_session_type")
        _instant(session.opening, f"original.sessions[{symbol!r}].opening", session=True)
        _same(session.opening, target.opening, f"original.sessions[{symbol!r}].opening")
        # Check suffix clock geometry, never suffix price values or prefix labels.
        for at in session.minutes:
            _instant(at, f"original.sessions[{symbol!r}].minutes.clock", session=True)
            _need(target.opening <= at < target.closing
                  and (at - target.opening) % MINUTE == timedelta(0),
                  f"original.sessions[{symbol!r}].minutes.clock", "invalid_minute_clock")
    _same(target.price_source, identities[target.symbol], "original.target.price_source")
    return tuple(sorted(set(universe.members) - {target.symbol})), receipts, identities


def _observe(symbol: str, name: str, start: datetime, end: datetime,
             session: cc.Session | None, identity: ea.PriceSource,
             decision: datetime) -> tuple[WindowObservation, tuple]:
    path = f"original.window[{symbol!r},{name!r}]"
    meta = ea.Provenance(end, end + 2 * MINUTE, identity.retrieved_at,
                         identity.source, identity.is_final, identity.data_vintage, "assumed")
    _metadata(meta, end, decision, path)
    issues, prices = [], []
    if session is None:
        issues.append((start, "session_missing"))
    else:
        at = start
        while at < end:
            row = session.minutes.get(at)
            if row is None:
                reason = "missing"
                for gap in session.gaps:
                    gap = _map(gap, f"{path}.gap")
                    _need({"start", "end", "state"} <= set(gap), path, "gap_fields_missing")
                    _instant(gap["start"], f"{path}.gap.start", session=True)
                    _instant(gap["end"], f"{path}.gap.end", session=True)
                    if gap["start"] <= at < gap["end"]:
                        reason = gap["state"]
                        _need(type(reason) is str and reason in {"missing", "conflict", "invalid_price"},
                              path, "invalid_gap_reason")
                        break
                issues.append((at, reason))
            else:
                _need(type(row) is tuple and len(row) == 4 and all(type(p) is Decimal for p in row),
                      f"{path}.minutes[{at.isoformat()}]", "exact_decimal_ohlc_required")
                if (not all(p.is_finite() and p > 0 for p in row)
                        or not row[2] <= min(row[0], row[3]) <= max(row[0], row[3]) <= row[1]):
                    issues.append((at, "invalid_price"))
                else:
                    prices.append(row)
            at += MINUTE
    exact_return = None if issues else (Fraction(prices[-1][3]) - Fraction(prices[0][0])) / Fraction(prices[0][0])
    return (WindowObservation(symbol, name, start, end, meta, exact_return,
                              tuple(sorted({reason for _, reason in issues})), tuple(issues)), tuple(prices))


def _expected_features(target: ea.Target, universe: ea.MonthlyUniverse,
                       sessions: Mapping, sources: Mapping, *, setup_id: str,
                       observed_end: datetime, decision_at: datetime, structural_stop: Fraction,
                       inputs_authenticated: bool, authentication_reference: str,
                       price_unit: str = "KRW") -> PreparedFeatures:
    """The independent reconstruction shared by typed and compact checks."""
    _need(inputs_authenticated is True, "original.inputs_authenticated", "caller_authentication_required")
    _need(type(authentication_reference) is str and bool(authentication_reference.strip()),
          "original.authentication_reference", "authentication_reference_required")
    _need(type(setup_id) is str and setup_id in ("EA-A", "EA-B"), "original.setup_id", "invalid_setup")
    _need(type(structural_stop) is Fraction and structural_stop > 0,
          "original.structural_stop", "positive_exact_stop_required")
    peers, receipts, identities = _original_feature_contract(
        target, universe, sessions, sources, observed_end, decision_at)
    _same(price_unit, "KRW", "original.price_unit")
    _same(target.price_basis, PRICE_BASIS, "original.target.price_basis")
    ranges = {"first30": (target.opening, target.opening + 30 * MINUTE),
              "open_to_signal": (target.opening, observed_end),
              "last15": (observed_end - 15 * MINUTE, observed_end)}
    observations, peer_records = [], {}
    for symbol in peers:
        peer_records[symbol] = {}
        for name in PEER_WINDOWS:
            record, _ = _observe(symbol, name, *ranges[name], sessions[symbol], identities[symbol], decision_at)
            observations.append(record)
            peer_records[symbol][name] = record
    observed = {name: tuple(symbol for symbol in peers if not peer_records[symbol][name].minute_issues)
                for name in PEER_WINDOWS}
    common = tuple(symbol for symbol in peers if all(symbol in observed[name] for name in PEER_WINDOWS))
    values = {"setup_is_A": ea.Feature(Fraction(1 if setup_id == "EA-A" else 0)),
              "fraction_of_session_elapsed": ea.Feature(Fraction(
                  (decision_at - target.opening) // timedelta(microseconds=1),
                  (target.closing - target.opening) // timedelta(microseconds=1))),
              "common_peer_count_divided_by_original_peer_count": ea.Feature(
                  Fraction(len(common), len(peers)) if peers else None,
                  () if peers else ("no_original_peers",), (universe.provenance,))}
    for name in PEER_WINDOWS:
        total = Fraction(0)
        for symbol in common:
            total += peer_records[symbol][name].value
        values[f"peer_{name}_return"] = ea.Feature(
            total / len(common) if len(common) >= 5 else None,
            () if len(common) >= 5 else ("insufficient_common_observed_peers",),
            tuple(peer_records[symbol][name].provenance for symbol in common))
    target_records, target_prices = {}, {}
    target_ranges = {"open_to_signal": ranges["open_to_signal"], "last15": ranges["last15"],
                     "trigger5": (observed_end - 5 * MINUTE, observed_end),
                     "signal_close": (observed_end - MINUTE, observed_end)}
    for name, interval in target_ranges.items():
        record, prices = _observe(target.symbol, name, *interval, sessions[target.symbol],
                                  identities[target.symbol], decision_at)
        observations.append(record)
        target_records[name], target_prices[name] = record, prices
    for name in ("open_to_signal", "last15"):
        record = target_records[name]
        values[f"target_{name}_return"] = ea.Feature(record.value, record.reasons, (record.provenance,))
    record = target_records["signal_close"]
    stop_gap = None
    if not record.minute_issues:
        close = Fraction(target_prices["signal_close"][-1][3])
        stop_gap = (close - structural_stop) / close
    values["structural_stop_gap_from_signal_close"] = ea.Feature(stop_gap, record.reasons, (record.provenance,))
    record = target_records["trigger5"]
    location, reasons = None, record.reasons
    if not record.minute_issues:
        prices = target_prices["trigger5"]
        lower, upper = min(Fraction(row[2]) for row in prices), max(Fraction(row[1]) for row in prices)
        if lower == upper:
            reasons = ("zero_trigger_range",)
        else:
            location = (Fraction(prices[-1][3]) - lower) / (upper - lower)
    values["trigger_close_location"] = ea.Feature(location, reasons, (record.provenance,))
    return PreparedFeatures("EC-common-observed-peer-min5", setup_id, target, observed_end, decision_at,
        {name: values[name] for name in INPUT_NAMES}, peers, common, observed, tuple(observations),
        receipts, universe.provenance, authentication_reference)


def _feature_receipt(expected: PreparedFeatures) -> dict:
    return {"mode": "source-only-ec-features-independent-verification", "verified": True,
            "feature_count": len(INPUT_NAMES), "window_count": len(expected.windows),
            "original_peer_count": len(expected.fixed_peers), "common_peer_count": len(expected.common_peers),
            "source_receipt_count": len(expected.source_evidence), "strategy_pass": False,
            "historical_timing_certified": False, "actual_execution_authorized": False,
            "limitations": ("caller_authenticates_full_original_inputs_and_raw_to_session_linkage",
                            "assumed_end_plus_two_availability_not_historical_certification",
                            "standalone_receipt_does_not_replace_full_report_accumulator_or_execution_gates")}


def verify_features(prepared: PreparedFeatures, **original_inputs) -> dict:
    """Reconstruct ten inputs and every diagnostic from original causal minutes."""
    expected = _expected_features(**original_inputs)
    _same(prepared, expected, "prepared")
    return _feature_receipt(expected)


def _fraction_json(value: Fraction | None) -> dict | None:
    return None if value is None else {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _compact_expected(expected: PreparedFeatures, universe: ea.MonthlyUniverse,
                      sources: list, membership_day_index: int) -> dict:
    """Serialize the independent reconstruction, including lossless gap RLE."""
    _need(type(sources) is list and type(membership_day_index) is int and membership_day_index >= 0,
          "compact.original", "source_list_and_membership_index_required")
    source_indices = {}
    for index, raw in enumerate(sources):
        raw = _map(raw, f"compact.original.sources[{index}]")
        if raw.get("session") != expected.target.session_date.isoformat():
            continue
        symbol = raw.get("symbol")
        if symbol in universe.members:
            _need(symbol not in source_indices, "compact.original.sources", "duplicate_source_identity")
            _same(raw, expected.source_evidence[symbol], f"compact.original.sources[{index}]")
            source_indices[symbol] = index
    _need(set(source_indices) == set(universe.members), "compact.original.sources", "original_member_source_missing")
    windows, window_indices = [], {}
    for index, record in enumerate(expected.windows):
        window_indices[(record.symbol, record.name)] = index
        intervals = []
        for at, reason in record.minute_issues:
            if intervals and intervals[-1]["state"] == reason and intervals[-1]["end_at"] == at.isoformat():
                intervals[-1]["end_at"] = (at + MINUTE).isoformat()
            else:
                intervals.append({"start_at": at.isoformat(), "end_at": (at + MINUTE).isoformat(), "state": reason})
        windows.append({"source_index": source_indices[record.symbol], "name": record.name,
                        "value": _fraction_json(record.value), "reasons": list(record.reasons),
                        "minute_issue_intervals": intervals})
    features = {}
    target_window_names = {
        "target_open_to_signal_return": "open_to_signal",
        "target_last15_return": "last15",
        "structural_stop_gap_from_signal_close": "signal_close",
        "trigger_close_location": "trigger5",
    }
    peer_window_names = {
        "peer_open_to_signal_return": "open_to_signal",
        "peer_last15_return": "last15",
        "peer_first30_return": "first30",
    }
    for name, feature in expected.features.items():
        is_universe = name == "common_peer_count_divided_by_original_peer_count"
        references = []
        if name in target_window_names:
            references = [window_indices[(expected.target.symbol, target_window_names[name])]]
        elif name in peer_window_names:
            references = [window_indices[(symbol, peer_window_names[name])] for symbol in expected.common_peers]
        features[name] = {"value": _fraction_json(feature.value), "reasons": list(feature.reasons),
            "provenance_window_indices": references,
            "universe_provenance": is_universe, "available_at": expected.decision_at.isoformat()}
    return {"proxy_id": expected.proxy_id, "observed_end": expected.observed_end.isoformat(),
            "decision_at": expected.decision_at.isoformat(),
            "membership_provenance_ref": {"policy": expected.target.policy, "day_index": membership_day_index},
            "source_indices": [source_indices[symbol] for symbol in universe.members],
            "fixed_peer_source_indices": [source_indices[symbol] for symbol in expected.fixed_peers],
            "common_peer_source_indices": [source_indices[symbol] for symbol in expected.common_peers],
            "observed_peer_source_indices_by_window": {
                name: [source_indices[symbol] for symbol in expected.observed_peers_by_window[name]] for name in PEER_WINDOWS},
            "window_ranges": {record.name: {"start_at": record.start_at.isoformat(), "end_at": record.end_at.isoformat()}
                              for record in expected.windows}, "windows": windows, "features": features}


def verify_compact_context(context: Mapping, prepared: PreparedFeatures, *, original_inputs: Mapping,
                           sources: list, membership_day_index: int) -> dict:
    """Check producer records AND compact publication from one raw reconstruction.

    Use this instead of a separate verify_features call: it includes that check.
    The final-report accumulator additionally binds this row's publication to
    full population/count checks before returning its completion receipt.
    """
    expected = _expected_features(**original_inputs)
    _same(prepared, expected, "prepared")
    compact = _compact_expected(expected, original_inputs["universe"], sources, membership_day_index)
    _same(context, compact, "compact_context")
    return _feature_receipt(expected)


def _reasons(value: object, path: str) -> tuple[str, ...]:
    _need(type(value) is list and all(type(item) is str and bool(item.strip()) for item in value),
          path, "reason_list_required")
    _need(len(value) == len(set(value)), path, "duplicate_reasons")
    return tuple(value)


def _stored_volume(value: object, path: str) -> dict:
    value = _map(value, path)
    _need(set(value) == {"value", "reasons"}, path, "stored_feature_fields_changed")
    reasons = _reasons(value["reasons"], f"{path}.reasons")
    if value["value"] is None:
        _need(bool(reasons), path, "unknown_without_reason")
        return {"value": None, "reasons": reasons}
    _need(not reasons, path, "observed_with_unknown_reason")
    raw = _map(value["value"], f"{path}.value")
    _need(set(raw) == {"numerator", "denominator"}, path, "fraction_fields_changed")
    for name, pattern in (("numerator", r"0|[1-9][0-9]*"), ("denominator", r"[1-9][0-9]*")):
        _need(type(raw[name]) is str and re.fullmatch(pattern, raw[name]) is not None,
              f"{path}.value.{name}", "canonical_nonnegative_fraction_required")
    try:
        result = Fraction(int(raw["numerator"]), int(raw["denominator"]))
    except ValueError:
        raise InputVerificationRefusal(f"{path}: invalid_fraction") from None
    _need((str(result.numerator), str(result.denominator)) == (raw["numerator"], raw["denominator"]),
          path, "noncanonical_fraction")
    return {"value": result, "reasons": reasons}


def _week(day: str) -> str:
    parsed = date.fromisoformat(day)
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def _row_counts(rows: list[dict]) -> dict:
    result = {"targets": 0, "signals": 0, "known_no_signal": 0, "unknown_signals": 0,
              "common_control": 0, "selected": 0,
              "volume_only_states_all_targets": dict.fromkeys(("selected", "rejected", "unknown"), 0)}
    for row in rows:
        result["targets"] += 1
        result[{"signal": "signals", "known_no_signal": "known_no_signal", "unknown": "unknown_signals"}[row["signal_state"]]] += 1
        result["common_control"] += int(row["common_control"])
        result["selected"] += int(row["selected"])
        result["volume_only_states_all_targets"][row["volume_only"]] += 1
    return result


def _volume_row(row: Mapping, member: Mapping, membership: Mapping, index: int,
                source_lists: tuple[list, list], path: str) -> dict:
    day, symbol = membership["session"], member["symbol"]
    _need(type(row.get("membership_day_index")) is int, path, "membership_index_type_changed")
    _same(row["membership_day_index"], index, f"{path}.membership_day_index")
    _same(_map(row.get("membership_target"), path), member, f"{path}.membership_target")
    indices = {}
    for field, sources in zip(("source_index", "volume_source_index"), source_lists, strict=True):
        offset = row.get(field)
        _need(type(offset) is int and 0 <= offset < len(sources), f"{path}.{field}", "invalid_source_index")
        source = _map(sources[offset], f"original.{field}[{offset}]")
        _need((source.get("session"), source.get("symbol")) == (day, symbol),
              f"{path}.{field}", "source_identity_changed")
        indices[field] = offset
    signal = _map(row.get("signal"), f"{path}.signal")
    _same(signal.get("hypothesis_id"), "H1-morning-trend-pullback-reclaim", f"{path}.signal.hypothesis_id")
    state, reason = signal.get("state"), signal.get("reason")
    _need(type(state) is str and state in {"signal", "known_no_signal", "unknown"}
          and type(reason) is str and bool(reason.strip()), path, "invalid_signal_state_or_reason")
    parsed = {}
    for field in SIGNAL_CLOCKS:
        _need(field in signal, f"{path}.signal.{field}", "clock_field_missing")
        parsed[field] = None if signal[field] is None else _parse_clock(signal[field], f"{path}.signal.{field}", day)
    decision, observed = parsed["decision_at"], parsed["observed_through"]
    if decision is not None:
        _need(_parse_clock(membership["selected_at"], f"{path}.membership.selected_at", day) <= decision,
              path, "membership_after_decision")
    if observed is not None and decision is not None:
        _need(observed <= decision, path, "future_observation")
    if state == "signal":
        _need(all(clock is not None for clock in parsed.values()), path, "signal_clocks_required")
        start, end = parsed["signal_bar_start"], parsed["signal_bar_end"]
        _need(parsed["setup_bar_end"] <= start < end == observed <= decision
              and end - start == 5 * MINUTE and decision - end == 2 * MINUTE,
              path, "original_signal_clocks_changed")
    arms = _map(row.get("arms"), f"{path}.arms")
    basic, arm = arms.get("basic"), arms.get("volume_only")
    _need(all(type(item) is str and item in {"selected", "rejected", "unknown"} for item in (basic, arm)),
          path, "invalid_arm_state")
    features = _map(row.get("features"), f"{path}.features")
    feature, uncomputed = None, ()
    if state != "signal":
        expected_arm = "unknown" if state == "unknown" else "rejected"
        _need(basic == arm == expected_arm and len(features) == 0, path, "nonsignal_arm_or_feature_changed")
        volume_state = "not_applicable"
    elif "relative_volume" in features:
        _need(basic == "selected", path, "signal_basic_arm_changed")
        feature = _stored_volume(features["relative_volume"], f"{path}.features.relative_volume")
        value = feature["value"]
        volume_state = "unknown" if value is None else "observed"
        expected_arm = "unknown" if value is None else "selected" if value >= Fraction(2) else "rejected"
        _need(arm == expected_arm, path, "volume_arm_disagrees_with_value")
    else:
        _need(set(features) == {"clock"} and basic == arm == "unknown", path, "volume_feature_missing")
        clock = _map(features["clock"], f"{path}.features.clock")
        _need(set(clock) == {"value", "reasons"} and clock["value"] is None, path, "invalid_cap_clock_feature")
        uncomputed = _reasons(clock["reasons"], f"{path}.features.clock.reasons")
        _same(uncomputed, ("same_day_cap_infeasible",), f"{path}.uncomputed_reasons")
        volume_state = "not_computed"
    common = state == "signal" and volume_state == "observed"
    return {"session": day, "symbol": symbol, "membership_day_index": index,
            "membership_target": {"symbol": symbol, "rank": member["rank"]}, **indices,
            "signal_state": state, "signal_reason": reason,
            **{name: signal[name] for name in SIGNAL_CLOCKS},
            "volume_state": volume_state, "relative_volume": feature, "uncomputed_reasons": uncomputed,
            "volume_only": arm, "common_control": common, "selected": common and arm == "selected"}


def _expected_volume_sidecar(original_report: Mapping, *, inputs_authenticated: bool) -> dict:
    """Independently reconstruct the stored-volume-only publication."""
    _need(inputs_authenticated is True, "original.inputs_authenticated", "caller_authentication_required")
    report = _map(original_report, "original_report")
    _same(report.get("mode"), "fixed-ea-context-exploratory-comparison", "original_report.mode")
    _same(report.get("setup_id"), "EA-A", "original_report.setup_id")
    sessions = report.get("reference_sessions")
    _need(type(sessions) is list and bool(sessions), "original_report.reference_sessions", "calendar_required")
    for day in sessions:
        _day(day, "original_report.reference_sessions")
    _need(sessions == sorted(set(sessions)), "original_report.reference_sessions", "calendar_order_or_duplicates")
    weeks = sorted({_week(day) for day in sessions})
    _same(report.get("reference_weeks"), weeks, "original_report.reference_weeks")
    matrix = _map(report.get("source_target_matrix"), "original_report.source_target_matrix")
    variants = _map(report.get("variants"), "original_report.variants")
    _need(set(matrix) == set(variants) == set(POLICIES), "original_report", "timing_policies_changed")
    sources = (report.get("sources"), report.get("volume_sources"))
    _need(all(type(items) is list for items in sources), "original_report.sources", "source_lists_required")
    expected = {"mode": "ec-volume-input-sidecar-preparation", "setup_id": "EA-A",
                "scope": "original_per_policy_targets_stored_volume_only", "limitations": VOLUME_LIMITATIONS,
                "source_authentication": "caller_assertion", "volume_availability_reauthenticated": False,
                "performance_computed": False, "policy_results_not_pooled": True,
                "reference_sessions": list(sessions), "reference_weeks": weeks, "variants": {}}
    all_identities = set()
    for policy in POLICIES:
        path = f"original_report.variants[{policy!r}]"
        rows = _map(variants[policy], path).get("days")
        memberships = _map(matrix[policy], f"original_report.source_target_matrix[{policy!r}]").get("days")
        _need(type(rows) is list and type(memberships) is list and len(memberships) == len(sessions),
              path, "original_population_changed")
        original = {}
        for row in rows:
            row = _map(row, path)
            identity = (_day(row.get("session"), path), _symbol(row.get("symbol"), path))
            _need(identity not in original, path, "duplicate_original_target")
            original[identity] = row
        reconstructed, population = [], set()
        for index, day in enumerate(sessions):
            membership = _map(memberships[index], f"{path}.membership[{index}]")
            _same(membership.get("session"), day, f"{path}.membership[{index}].session")
            selected = _parse_clock(membership.get("selected_at"), path, day)
            _need(_parse_clock(membership.get("available_at"), path) <= selected,
                  path, "membership_available_after_selection")
            members = membership.get("targets")
            _need(type(members) is list, path, "membership_targets_required")
            ranks = set()
            for raw in members:
                member = _map(raw, f"{path}.membership_target")
                symbol = _symbol(member.get("symbol"), path)
                rank = member.get("rank")
                _need(type(rank) is int and rank > 0 and rank not in ranks, path, "invalid_or_duplicate_rank")
                ranks.add(rank)
                identity = (day, symbol)
                _need(identity not in population and identity in original, path, "duplicate_or_missing_target")
                population.add(identity)
                reconstructed.append(_volume_row(original[identity], member, membership, index, sources,
                                                 f"{path}.days[{len(reconstructed)}]"))
        _need(set(original) == population, path, "extra_original_target")
        all_identities.update(population)
        expected["variants"][policy] = {
            "days": reconstructed, "counts": _row_counts(reconstructed),
            "by_session": {day: _row_counts([row for row in reconstructed if row["session"] == day]) for day in sessions},
            "by_week": {week: _row_counts([row for row in reconstructed if _week(row["session"]) == week]) for week in weeks}}
    for name, items in zip(("sources", "volume_sources"), sources, strict=True):
        identities = []
        for index, raw in enumerate(items):
            raw = _map(raw, f"original_report.{name}[{index}]")
            identities.append((_day(raw.get("session"), name), _symbol(raw.get("symbol"), name)))
        _need(len(identities) == len(set(identities)) and set(identities) == all_identities,
              f"original_report.{name}", "source_inventory_population_changed")
    if "input_evidence" in report:
        evidence = _map(report["input_evidence"], "original_report.input_evidence")
        _same(evidence.get("unique_target_days"), len(sources[0]), "original_report.input_evidence.unique_target_days")
        for field, items in zip(("price_rows", "volume_rows"), sources, strict=True):
            counts = [item.get("input_rows") for item in items]
            _need(all(type(n) is int and 0 <= n <= 600 for n in counts), "original_report.sources.input_rows", "invalid_row_count")
            _same(evidence.get(field), sum(counts), f"original_report.input_evidence.{field}")
    return expected


def _volume_receipt(expected: Mapping) -> dict:
    return {"mode": "source-only-ec-stored-volume-independent-verification", "verified": True,
            "targets_by_policy": {policy: expected["variants"][policy]["counts"]["targets"] for policy in POLICIES},
            "reference_session_count": len(expected["reference_sessions"]),
            "reference_week_count": len(expected["reference_weeks"]),
            "strategy_pass": False, "volume_availability_reauthenticated": False,
            "actual_execution_authorized": False, "limitations": VOLUME_LIMITATIONS}


def verify_volume_sidecar(sidecar: Mapping, original_report: Mapping, *, inputs_authenticated: bool) -> dict:
    """Check full stored-volume populations, without reading outcome/other features."""
    expected = _expected_volume_sidecar(original_report, inputs_authenticated=inputs_authenticated)
    _same(sidecar, expected, "sidecar")
    return _volume_receipt(expected)


def _frozen_tree(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _frozen_tree(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return tuple(_frozen_tree(item) for item in value)
    return value


def _json_tree(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_tree(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_json_tree(item) for item in value]
    if type(value) is Fraction:
        return _fraction_json(value)
    return value


def _json_bytes(value: object, path: str) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise InputVerificationRefusal(f"{path}: exact_json_required") from None


def _stop(value: object, path: str) -> Fraction:
    exact = _stored_volume({"value": value, "reasons": []}, path)["value"]
    _need(exact is not None and exact > 0, path, "positive_stop_required")
    return exact


def verify_readiness_projection(projected: object, original_report: Mapping, *,
                                 inputs_authenticated: bool, authentication_reference: str) -> dict:
    """Compare the immutable request to original rows while the report is alive.

    No old outcomes, common flags, arm counts, or old feature values are read.
    The existing report-integrity gate remains the caller's separate check.
    """
    _need(inputs_authenticated is True and type(authentication_reference) is str
          and bool(authentication_reference.strip()), "projection.original", "caller_authentication_required")
    names = {"setup_id", "authentication_reference", "request_metadata", "account_projection", "target_rows",
             "volume_sidecar", "volume_verification"}
    _need(is_dataclass(projected) and {field.name for field in fields(projected)} == names,
          "projection", "projection_record_fields_changed")
    setup = projected.setup_id
    _need(type(setup) is str and setup in ("EA-A", "EA-B"), "projection.setup_id", "invalid_setup")
    report = _map(original_report, "projection.original_report")
    _same(report.get("mode"), "fixed-ea-context-exploratory-comparison", "projection.original_report.mode")
    _same(report.get("setup_id"), setup, "projection.original_report.setup_id")
    reference, weeks = report.get("reference_sessions"), report.get("reference_weeks")
    _need(type(reference) is list and bool(reference), "projection.reference_sessions", "calendar_required")
    for day in reference:
        _day(day, "projection.reference_sessions")
    _need(reference == sorted(set(reference)), "projection.reference_sessions", "calendar_order_changed")
    _same(weeks, sorted({_week(day) for day in reference}), "projection.reference_weeks")
    matrix = _map(report.get("source_target_matrix"), "projection.source_target_matrix")
    variants = _map(report.get("variants"), "projection.original_variants")
    _need(set(matrix) == set(variants) == set(POLICIES), "projection", "timing_policies_changed")
    source_arrays = (report.get("sources"), report.get("volume_sources"))
    _need(all(type(array) is list for array in source_arrays), "projection.sources", "source_lists_required")
    targets, stream_variants, union = {}, {}, set()
    for policy in POLICIES:
        path = f"projection.target_rows[{policy!r}]"
        memberships = _map(matrix[policy], path).get("days")
        rows = _map(variants[policy], path).get("days")
        _need(type(memberships) is list and len(memberships) == len(reference) and type(rows) is list,
              path, "original_population_changed")
        membership_by_identity = {}
        for day_index, day in enumerate(reference):
            membership = _map(memberships[day_index], path)
            _same(membership.get("session"), day, f"{path}.membership.session")
            selected = _parse_clock(membership.get("selected_at"), path, day)
            _need(_parse_clock(membership.get("available_at"), path) <= selected, path, "membership_available_after_selection")
            members = membership.get("targets")
            _need(type(members) is list, path, "membership_targets_required")
            ranks = set()
            for member in members:
                member = _map(member, path)
                symbol, rank = _symbol(member.get("symbol"), path), member.get("rank")
                _need(type(rank) is int and rank > 0 and rank not in ranks, path, "invalid_membership_rank")
                ranks.add(rank)
                identity = (day, symbol)
                _need(identity not in membership_by_identity, path, "duplicate_membership_target")
                membership_by_identity[identity] = (day_index, member, selected)
        projected_rows, seen = [], set()
        signal_symbols = {day: [] for day in reference}
        for index, raw in enumerate(rows):
            raw = _map(raw, f"{path}[{index}]")
            identity = (_day(raw.get("session"), path), _symbol(raw.get("symbol"), path))
            _need(identity in membership_by_identity and identity not in seen, path, "duplicate_or_extra_target")
            seen.add(identity)
            day_index, member, selected = membership_by_identity[identity]
            _same(raw.get("membership_day_index"), day_index, f"{path}[{index}].membership_day_index")
            _same(raw.get("membership_target"), member, f"{path}[{index}].membership_target")
            for field, array in zip(("source_index", "volume_source_index"), source_arrays, strict=True):
                offset = raw.get(field)
                if field == "volume_source_index" and setup == "EA-B":
                    _same(offset, None, f"{path}[{index}].{field}")
                    continue
                _need(type(offset) is int and 0 <= offset < len(array), f"{path}[{index}].{field}", "invalid_source_index")
                source = _map(array[offset], path)
                _same((source.get("session"), source.get("symbol")), identity, f"{path}[{index}].{field}")
            signal = _map(raw.get("signal"), path)
            state, reason = signal.get("state"), signal.get("reason")
            _need(type(state) is str and state in {"signal", "known_no_signal", "unknown"}
                  and type(reason) is str and bool(reason.strip()), path, "invalid_signal_state_or_reason")
            clocks = {}
            for field in SIGNAL_CLOCKS:
                _need(field in signal, path, "signal_clock_missing")
                clocks[field] = None if signal[field] is None else _parse_clock(signal[field], path, identity[0])
            if clocks["decision_at"] is not None:
                _need(selected <= clocks["decision_at"], path, "membership_after_decision")
            if clocks["observed_through"] is not None and clocks["decision_at"] is not None:
                _need(clocks["observed_through"] <= clocks["decision_at"], path, "future_observation")
            stop = None
            if state == "signal":
                _need(all(value is not None for value in clocks.values()), path, "signal_clocks_required")
                start, end = clocks["signal_bar_start"], clocks["signal_bar_end"]
                _need(clocks["setup_bar_end"] <= start < end == clocks["observed_through"]
                      and end - start == 5 * MINUTE and clocks["decision_at"] - end == 2 * MINUTE,
                      path, "original_signal_clocks_changed")
                stop = _stop(raw.get("structural_stop"), f"{path}[{index}].structural_stop")
                signal_symbols[identity[0]].append(identity[1])
            projected_rows.append({"session": identity[0], "symbol": identity[1], "original_row_index": index,
                "membership_day_index": day_index, "membership_target": member,
                "source_index": raw["source_index"], "volume_source_index": raw["volume_source_index"],
                "signal_state": state, "signal_reason": reason,
                **{field: signal[field] for field in SIGNAL_CLOCKS}, "structural_stop": stop})
        _need(seen == set(membership_by_identity), path, "original_target_missing")
        union.update(seen)
        targets[policy] = projected_rows
        stream_variants[policy] = {"dates": {day: {"candidates": [{"symbol": symbol} for symbol in sorted(signal_symbols[day])]}
                                             for day in reference}}
    source_identities = [(_map(raw, "projection.sources.row").get("session"), raw.get("symbol")) for raw in source_arrays[0]]
    _need(len(source_identities) == len(set(source_identities)) and set(source_identities) == union,
          "projection.sources", "source_inventory_population_changed")
    calendar = _map(report.get("upstream_calendar"), "projection.upstream_calendar")
    calendar_rows = calendar.get("rows")
    _need(type(calendar_rows) is list, "projection.upstream_calendar", "calendar_rows_required")
    dates = [_day(_map(row, "projection.upstream_calendar.row").get("date"), "projection.upstream_calendar.date") for row in calendar_rows]
    _need(dates == sorted(set(dates)) and [day for day in dates if reference[0] <= day <= reference[-1]] == reference,
          "projection.upstream_calendar", "reference_calendar_geometry_changed")
    account = {"mode": "ec-stored-account-inputs", "setup_id": setup,
               **{key: report[key] for key in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources", "volume_sources")},
               "variants": stream_variants}
    sidecar = _expected_volume_sidecar(report, inputs_authenticated=True) if setup == "EA-A" else None
    if setup == "EA-B":
        _same(source_arrays[1], [], "projection.volume_sources")
    expected = {"setup_id": setup, "authentication_reference": authentication_reference,
        "request_metadata": {"mode": "ec-context-readiness-original-request", "setup_id": setup,
            "authentication_reference": authentication_reference,
            "target_counts_by_policy": {policy: len(targets[policy]) for policy in POLICIES}},
        "account_projection": account, "target_rows": targets, "volume_sidecar": sidecar,
        "volume_verification": _volume_receipt(sidecar) if sidecar is not None else None}
    for name, value in expected.items():
        _same(getattr(projected, name), _frozen_tree(value), f"projection.{name}")
    return {"mode": "source-only-ec-original-readiness-projection-verification", "verified": True,
            "setup_id": setup, "targets_by_policy": {policy: len(targets[policy]) for policy in POLICIES},
            "strategy_pass": False, "actual_execution_authorized": False}


def _support_expected(context: dict | None, setup: str, volume_observed: bool) -> dict:
    support = dict.fromkeys(SUPPORT_NAMES)
    if context is None:
        return support
    mask = {name: context["features"][name]["value"] is not None for name in INPUT_NAMES}
    if setup == "EA-A":
        market = mask["target_open_to_signal_return"] and mask["peer_open_to_signal_return"] and mask["peer_last15_return"]
        support["P04_volume_inputs"], support["P05_market_inputs"] = volume_observed, market
        support["P06_volume_and_market_inputs"] = volume_observed and market
    else:
        support["P07_market_inputs"] = mask["peer_first30_return"] and mask["peer_last15_return"]
    support["P08_router_inputs"] = mask["peer_first30_return"] and mask["peer_open_to_signal_return"] and mask["peer_last15_return"]
    support["P09_P10_all_ten_inputs"] = all(mask.values())
    return support


def _summarize_verified(records: list[dict]) -> dict:
    result = {"counts": dict.fromkeys(("targets", "signals", "known_no_signal", "unknown_signals"), 0),
              "feature_counts": {name: dict.fromkeys(("observed", "unknown", "not_applicable"), 0) for name in INPUT_NAMES},
              "peer_quorum": dict.fromkeys(("met", "not_met", "not_applicable"), 0),
              "common_peer_count_distribution": {}, "original_peer_count_distribution": {},
              "support_counts": {name: dict.fromkeys(("observed", "unknown", "not_applicable"), 0) for name in SUPPORT_NAMES}}
    for record in records:
        result["counts"]["targets"] += 1
        result["counts"][{"signal": "signals", "known_no_signal": "known_no_signal", "unknown": "unknown_signals"}[record["state"]]] += 1
        applicable = record["mask"] is not None
        for name in INPUT_NAMES:
            state = "not_applicable" if not applicable else "observed" if record["mask"][name] else "unknown"
            result["feature_counts"][name][state] += 1
        result["peer_quorum"]["not_applicable" if not applicable else "met" if record["common"] >= 5 else "not_met"] += 1
        if applicable:
            for field, key in (("common_peer_count_distribution", "common"), ("original_peer_count_distribution", "original")):
                count = str(record[key])
                result[field][count] = result[field].get(count, 0) + 1
        for name, value in record["support"].items():
            result["support_counts"][name]["not_applicable" if value is None else "observed" if value else "unknown"] += 1
    return result


class ReadinessAccumulator:
    """Bind raw row verification, compact hashes, complete stream, and totals.

    Construct only from projections already checked against authenticated full
    originals. Call add_day before that day's add_row calls. Each signal's
    add_row takes the ORIGINAL prepare kwargs and typed producer result; no
    separate verify_features/verify_compact_context call is needed. verify_report
    returns completion only after every original row and day has been checked.
    """

    def __init__(self, a_projected: object, b_projected: object, *, authentication_reference: str):
        _need(type(authentication_reference) is str and bool(authentication_reference.strip()),
              "accumulator.authentication_reference", "authentication_reference_required")
        self.authentication_reference = authentication_reference
        self.projected = {"EA-A": a_projected, "EA-B": b_projected}
        self.account = a_projected.account_projection
        for key in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources"):
            _same(b_projected.account_projection[key], self.account[key], f"accumulator.original.{key}")
        self.reference = list(self.account["reference_sessions"])
        self.weeks = list(self.account["reference_weeks"])
        self.sources = _json_tree(self.account["sources"])
        self.volume = {(policy, row["session"], row["symbol"]): row for policy in POLICIES
                       for row in a_projected.volume_sidecar["variants"][policy]["days"]}
        self.records = {(policy, setup): {} for policy in POLICIES for setup in ("EA-A", "EA-B")}
        self.source_by_day = {day: {} for day in self.reference}
        for source in self.sources:
            self.source_by_day[source["session"]][source["symbol"]] = source
        self.day_index, self.current_day, self.chunks, self.price_rows = 0, None, 0, 0
        self.closed = False

    def add_day(self, day: Mapping) -> None:
        _need(not self.closed and self.day_index < len(self.reference), "accumulator.day", "extra_or_closed_day")
        session = self.reference[self.day_index]
        _same(day.get("session"), session, "accumulator.day.session")
        if self.current_day is not None:
            self._day_rows_complete(self.current_day)
        expected_sources = self.source_by_day[session]
        _same(day.get("original_sources"), expected_sources, "accumulator.day.original_sources")
        _need(isinstance(day.get("original_sessions"), Mapping)
              and set(day["original_sessions"]) == set(expected_sources), "accumulator.day.original_sessions", "original_session_population_changed")
        _same(day.get("session_open"), cc.session_open(session), "accumulator.day.session_open")
        _same(day.get("session_close"), cc.session_close(session), "accumulator.day.session_close")
        rows = sum(source["input_rows"] for source in expected_sources.values())
        _same(day.get("consumed_target_chunks"), len(expected_sources), "accumulator.day.consumed_target_chunks")
        _same(day.get("consumed_price_rows"), rows, "accumulator.day.consumed_price_rows")
        _same(day.get("original_union_complete"), self.day_index == len(self.reference) - 1, "accumulator.day.original_union_complete")
        self.current_day = session
        self.current_sessions = day["original_sessions"]
        self.day_index += 1
        self.chunks += len(expected_sources)
        self.price_rows += rows

    def _day_rows_complete(self, day: str) -> None:
        for (policy, setup), seen in self.records.items():
            expected = {row["original_row_index"] for row in self.projected[setup].target_rows[policy] if row["session"] == day}
            _need({index for index, record in seen.items() if record["session"] == day} == expected,
                  f"accumulator[{policy},{setup},{day}]", "original_day_rows_missing")

    def finish_day(self) -> None:
        """Release the one-day price map before the stream constructs tomorrow."""
        _need(self.current_day is not None and not self.closed, "accumulator.day", "no_open_day")
        self._day_rows_complete(self.current_day)
        self.current_sessions = None

    def _universe(self, policy: str, day_index: int) -> ea.MonthlyUniverse:
        membership = _json_tree(self.account["source_target_matrix"][policy]["days"][day_index])
        observed_day = date.fromisoformat(_day(membership["observation_date"], "accumulator.membership.observation_date"))
        observed = datetime.combine(observed_day, datetime.min.time(), cc.KST) + timedelta(days=1) - timedelta(microseconds=1)
        retrieved = max(_parse_clock(value, "accumulator.membership.retrieved_at") for value in membership["retrieved_at"].values())
        provenance = ea.Provenance(observed, _parse_clock(membership["available_at"], "accumulator.membership.available_at"),
            retrieved, membership["source"], membership["is_final"],
            hashlib.sha256(_json_bytes(membership, "accumulator.membership")).hexdigest(), "assumed")
        return ea.MonthlyUniverse(date.fromisoformat(membership["session"]).replace(day=1),
            tuple(member["symbol"] for member in membership["targets"]),
            _parse_clock(membership["selected_at"], "accumulator.membership.selected_at"), provenance)

    def add_row(self, policy: str, setup: str, row: Mapping, *, prepared: PreparedFeatures | None = None,
                original_inputs: Mapping | None = None) -> None:
        key = (policy, setup)
        _need(not self.closed and key in self.records and self.current_day is not None,
              "accumulator.row", "unopened_or_closed_row_scope")
        index = row.get("original_row_index")
        originals = self.projected[setup].target_rows[policy]
        _need(type(index) is int and 0 <= index < len(originals) and index not in self.records[key],
              "accumulator.row.original_row_index", "invalid_or_duplicate_original_row")
        original = originals[index]
        _same(original["session"], self.current_day, "accumulator.row.session")
        expected = _json_tree(original)
        context = None
        if original["signal_state"] == "signal":
            _need(isinstance(original_inputs, Mapping) and prepared is not None,
                  "accumulator.row", "original_signal_inputs_required")
            _same(original_inputs.get("setup_id"), setup, "accumulator.row.setup_id")
            _same(original_inputs.get("authentication_reference"), self.authentication_reference, "accumulator.row.authentication_reference")
            target = original_inputs.get("target")
            source = self.sources[original["source_index"]]
            raw = source["raw_chunk_metadata"]
            target_expected = ea.Target(original["symbol"], date.fromisoformat(original["session"]), policy,
                cc.session_open(original["session"]), cc.session_close(original["session"]), PRICE_BASIS,
                ea.PriceSource(_parse_clock(raw["retrieved_at"], "accumulator.row.source.retrieved_at"),
                               raw["source"], raw["is_final"], source["data_vintage"], "assumed"))
            _same(target, target_expected, "accumulator.row.target")
            _same(original_inputs.get("structural_stop"), original["structural_stop"], "accumulator.row.structural_stop")
            _same(original_inputs.get("observed_end"), _parse_clock(original["observed_through"], "accumulator.row.observed_through"), "accumulator.row.observed_end")
            _same(original_inputs.get("decision_at"), _parse_clock(original["decision_at"], "accumulator.row.decision_at"), "accumulator.row.decision_at")
            _same(original_inputs.get("universe"), self._universe(policy, original["membership_day_index"]), "accumulator.row.universe")
            _same(original_inputs.get("sources"), self.source_by_day[self.current_day], "accumulator.row.original_sources")
            _need(original_inputs.get("sessions") is self.current_sessions,
                  "accumulator.row.original_sessions", "original_day_session_binding_changed")
            independent = _expected_features(**original_inputs)
            _same(prepared, independent, "accumulator.row.prepared")
            context = _compact_expected(independent, original_inputs["universe"], self.sources, original["membership_day_index"])
        else:
            _need(prepared is None and original_inputs is None, "accumulator.row", "nonsignal_has_feature_computation")
        volume_row = self.volume.get((policy, original["session"], original["symbol"]))
        volume_observed = volume_row is not None and volume_row["volume_state"] == "observed"
        support = _support_expected(context, setup, volume_observed)
        expected.update(context=context, input_support=support)
        _same(row, expected, "accumulator.row")
        self.records[key][index] = {"session": original["session"], "state": original["signal_state"],
            "digest": hashlib.sha256(_json_bytes(expected, "accumulator.row")).digest(), "support": support,
            "mask": None if context is None else {name: context["features"][name]["value"] is not None for name in INPUT_NAMES},
            "common": 0 if context is None else len(context["common_peer_source_indices"]),
            "original": 0 if context is None else len(context["fixed_peer_source_indices"])}

    def _training(self, policy: str) -> dict:
        daily = {day: dict.fromkeys(("EA-A", "EA-B"), 0) for day in self.reference}
        for setup in ("EA-A", "EA-B"):
            for record in self.records[(policy, setup)].values():
                daily[record["session"]][setup] += int(record["support"]["P09_P10_all_ten_inputs"] is True)

        def counts(days):
            result = {}
            for setup in ("EA-A", "EA-B", "combined"):
                values = [sum(daily[day].values()) if setup == "combined" else daily[day][setup] for day in days]
                result[setup] = {"feature_complete_candidates": sum(values),
                                 "distinct_feature_complete_sessions": sum(value > 0 for value in values)}
            return result

        initial_end = min(100, len(self.reference))
        block_lengths = [len(self.reference[initial_end:]) // 5 + int(index < len(self.reference[initial_end:]) % 5) for index in range(5)]
        blocks, end = [], initial_end
        for index, length in enumerate(block_lengths):
            test = self.reference[end:end + length]
            blocks.append({"block_index": index + 1, "test_reference_sessions": test,
                "past_training_reference_session_count": end,
                "past_training_feature_support": counts(self.reference[:end]), "test_feature_support": counts(test)})
            end += length
        return {"scope": "feature_counts_only_not_observed_labels_or_training_readiness",
            "initial_reference_session_limit": 100, "initial_reference_sessions": self.reference[:initial_end],
            "initial_feature_support": counts(self.reference[:initial_end]), "expanding_blocks": blocks,
            "by_session": {day: {"feature_complete_candidates": {**daily[day], "combined": sum(daily[day].values())},
                "past_training_feature_support": counts(self.reference[:index])} for index, day in enumerate(self.reference)},
            "by_week": {week: counts([day for day in self.reference if _week(day) == week]) for week in self.weeks}}

    def verify_report(self, report: Mapping) -> dict:
        _need(self.day_index == len(self.reference) and self.current_day is not None,
              "readiness_report", "original_stream_not_complete")
        self._day_rows_complete(self.current_day)
        _same(set(report.get("variants", {})), set(POLICIES), "readiness_report.variants")
        total_signals = 0
        for policy in POLICIES:
            variant = _map(report["variants"][policy], "readiness_report.variant")
            _need(set(variant) == {"setups", "training_feature_support"}, "readiness_report.variant", "variant_fields_changed")
            _need(set(variant["setups"]) == {"EA-A", "EA-B"}, "readiness_report.setups", "setups_changed")
            for setup in ("EA-A", "EA-B"):
                value = variant["setups"][setup]
                rows, records = value.get("days"), self.records[(policy, setup)]
                _need(type(rows) is list and len(rows) == len(self.projected[setup].target_rows[policy]) == len(records),
                      f"readiness_report[{policy},{setup}].days", "original_row_population_changed")
                for index, row in enumerate(rows):
                    _same(hashlib.sha256(_json_bytes(row, "readiness_report.row")).digest(), records[index]["digest"],
                          f"readiness_report[{policy},{setup}].days[{index}]")
                ordered = [records[index] for index in range(len(records))]
                summary = _summarize_verified(ordered)
                total_signals += summary["counts"]["signals"]
                expected = {**summary,
                    "by_session": {day: _summarize_verified([record for record in ordered if record["session"] == day]) for day in self.reference},
                    "by_week": {week: _summarize_verified([record for record in ordered if _week(record["session"]) == week]) for week in self.weeks}}
                _need(set(value) == {"days", *expected}, "readiness_report.setup", "setup_fields_changed")
                for name, expected_value in expected.items():
                    _same(value[name], expected_value, f"readiness_report[{policy},{setup}].{name}")
            _same(variant["training_feature_support"], self._training(policy), f"readiness_report[{policy}].training_feature_support")
        expected_top = {"mode": "ec-causal-context-input-readiness", "schema_version": 1,
            "proxy_id": "EC-common-observed-peer-min5", "authentication_reference": self.authentication_reference,
            "original_requests": {setup: _json_tree(value.request_metadata) for setup, value in self.projected.items()},
            **{name: _json_tree(self.account[name]) for name in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources", "volume_sources")},
            "volume_sidecar": _json_tree(self.projected["EA-A"].volume_sidecar),
            "volume_verification": _json_tree(self.projected["EA-A"].volume_verification),
            "price_basis": PRICE_BASIS, "price_unit": "KRW", "minimum_common_peers": 5,
            "input_evidence": {"consumed_reference_sessions": self.day_index, "consumed_original_target_chunks": self.chunks,
                "consumed_price_rows": self.price_rows, "original_union_complete": True, "independently_verified_signals": total_signals},
            "policy_results_not_pooled": True, "feature_support_only": True, "historical_timing_certified": False,
            "corporate_action_units_certified": False, "volume_availability_reauthenticated": False,
            "performance_computed": False, "training_labels_analyzed": False, "model_fits": 0, "policy_evaluations": 0,
            "strategy_pass": False, "promotion_allowed": False, "actual_execution_authorized": False,
            "limitations": list(READINESS_LIMITATIONS)}
        reference = expected_top["authentication_reference"]
        _need(type(reference) is str and bool(reference.strip()), "readiness_report.authentication_reference", "authentication_reference_required")
        allowed = set(expected_top) | {"variants", "independent_verification"}
        _need(set(report) in (allowed, allowed - {"independent_verification"}), "readiness_report", "report_fields_changed")
        for name, expected_value in expected_top.items():
            _same(report[name], expected_value, f"readiness_report.{name}")
        receipt = {"mode": "source-only-ec-complete-readiness-independent-verification", "verified": True,
                   "verified_target_rows": sum(len(value) for value in self.records.values()),
                   "verified_signal_contexts": total_signals, "verified_reference_sessions": self.day_index,
                   "verified_original_source_chunks": self.chunks, "strategy_pass": False,
                   "actual_execution_authorized": False, "volume_availability_reauthenticated": False}
        if "independent_verification" in report:
            _same(report["independent_verification"], receipt, "readiness_report.independent_verification")
        self.closed = True
        return receipt
