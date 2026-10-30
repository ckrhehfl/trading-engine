"""Independent raw original-EA label and EC fixed-fold verification.

The narrow raw bucket/outcome arithmetic is source-ported from the inspected
pure functions raw_session/outcome in var/eb-context-independent.py, SHA256
1e86ecb57aeeb61eae8b2338695752deec34de4431b0e05730a3d6752021f173.
Its top level contains imports/constants/function definitions, no IO. This
public port does not import that private file, its detectors, means or verify.
It consumes caller-authenticated day chunks and proves no source-byte pin by
itself. Assumed availability never certifies historical publication/finality.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import re
from types import MappingProxyType

from research.intraday_campaign_ml_labels import (
    FoldTrainingView, MLCalendar, MLLabelProjection, OriginalLabelRow, ValidatedLabel,
)


KST = timezone(timedelta(hours=9))
MINUTE = timedelta(minutes=1)
PRICE_BASIS = "ASSUMPTION: saved vendor KRW prices; corporate-action comparability uncertified"
META = ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")
RAW_ROW = {"symbol", "interval", "open_time_ms", "open", "high", "low", "close", "fetched_at"}
INPUT_NAMES = (
    "setup_is_A", "fraction_of_session_elapsed", "target_open_to_signal_return",
    "target_last15_return", "structural_stop_gap_from_signal_close",
    "trigger_close_location", "peer_open_to_signal_return", "peer_last15_return",
    "peer_first30_return", "common_peer_count_divided_by_original_peer_count",
)


class MLLabelVerificationRefusal(ValueError):
    """An authenticated old/raw mismatch is an integrity failure, never a waiver."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MLLabelVerificationRefusal(reason)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if type(value) is Fraction:
        return {"numerator": str(value.numerator), "denominator": str(value.denominator)}
    if type(value) in (datetime, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        if not all(type(key) is str for key in value):
            return {"ordered_keyed_entries": [[_plain(key), _plain(item)] for key, item in value.items()]}
        return {key: _plain(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    return value


def _bytes(value) -> bytes:
    try:
        return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise MLLabelVerificationRefusal("noncanonical_label_assertion") from None


def _same(actual, expected, reason: str) -> None:
    _require(_bytes(actual) == _bytes(expected), reason)


def _clock(value) -> datetime:
    _require(type(value) is str, "raw_clock_required")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise MLLabelVerificationRefusal("raw_clock_required") from None
    _require(result.utcoffset() is not None, "aware_raw_clock_required")
    return result


def _opening(day: str) -> datetime:
    try:
        parsed = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise MLLabelVerificationRefusal("canonical_source_date_required") from None
    _require(parsed.isoformat() == day, "canonical_source_date_required")
    return datetime.combine(parsed, datetime.min.time(), KST).replace(
        hour=10 if day in {"2025-11-13", "2026-01-02"} else 9)


def _closing(day: str) -> datetime:
    return _opening(day).replace(hour=16 if day == "2025-11-13" else 15, minute=30)


def verify_ml_label_projection(projected: MLLabelProjection, original_report: Mapping, *,
                               inputs_authenticated: bool, authentication_reference: str) -> dict:
    """Check all originals, retaining sidecars only for original signal indices."""
    _require(inputs_authenticated is True and type(authentication_reference) is str
             and bool(authentication_reference.strip()), "caller_authentication_required")
    _require(type(projected) is MLLabelProjection and projected.setup_id in ("EA-A", "EA-B")
             and projected.authentication_reference == authentication_reference,
             "label_projection_identity_changed")
    _same(original_report.get("mode"), "fixed-ea-context-exploratory-comparison", "original_report_mode_changed")
    _same(projected.setup_id, original_report.get("setup_id"), "original_setup_changed")
    _same(projected.source_reference_sessions, original_report.get("reference_sessions"), "original_dates_changed")
    _require(set(projected.rows_by_policy) == {"D+1", "D+2"}, "label_projection_policies_changed")
    reference = original_report["reference_sessions"]
    _require(type(reference) is list and bool(reference) and reference == sorted(set(reference)),
             "original_reference_population_changed")
    for session in reference:
        _opening(session)
    _same(original_report["reference_weeks"], sorted({
        (date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday())).isoformat()
        for day in reference}), "original_reference_weeks_changed")
    calendar_dates = [row["date"] for row in original_report["upstream_calendar"]["rows"]]
    _require(calendar_dates == sorted(set(calendar_dates))
             and [day for day in calendar_dates if reference[0] <= day <= reference[-1]] == reference,
             "original_upstream_calendar_changed")
    _require(set(original_report["source_target_matrix"]) == set(original_report["variants"]) == {"D+1", "D+2"},
             "original_policies_changed")
    count = signal_count = nonsignal_count = 0
    union = set()
    for policy in ("D+1", "D+2"):
        members = original_report["source_target_matrix"][policy]["days"]
        _require(len(members) == len(reference), "original_membership_dates_changed")
        expected = {}
        for day_index, membership in enumerate(members):
            _require(membership["session"] == reference[day_index], "original_membership_dates_changed")
            _require(_clock(membership["available_at"]) <= _clock(membership["selected_at"])
                     < _opening(membership["session"]), "original_membership_clock_changed")
            targets = membership["targets"]
            _require(type(targets) is list and len(targets) == 20
                     and {target["rank"] for target in targets} == set(range(1, 21)),
                     "original_membership_targets_changed")
            for member in targets:
                identity = (membership["session"], member["symbol"])
                _require(identity not in expected, "original_membership_duplicate_target")
                expected[identity] = (day_index, member)
        originals = original_report["variants"][policy]["days"]
        rows = projected.rows_by_policy[policy]
        _require(type(originals) is list and len(originals) == len(expected), "original_target_population_changed")
        original_signals, seen = [], set()
        for index, original in enumerate(originals):
            identity = original["session"], original["symbol"]
            _require(identity in expected and identity not in seen, "original_target_population_changed")
            seen.add(identity)
            day_index, member = expected[identity]
            _same((original["membership_day_index"], original["membership_target"]), (day_index, member),
                  "original_target_membership_changed")
            for name, sources in (("source_index", original_report["sources"]),
                                  ("volume_source_index", original_report["volume_sources"])):
                offset = original[name]
                if name == "volume_source_index" and projected.setup_id == "EA-B":
                    _require(offset is None, "unexpected_original_volume_source")
                else:
                    _require(type(offset) is int and 0 <= offset < len(sources), "original_source_index_changed")
                    _same((sources[offset]["session"], sources[offset]["symbol"]), identity,
                          "original_source_coordinate_changed")
            signal = original["signal"]
            _require(signal["state"] in ("signal", "known_no_signal", "unknown"), "original_signal_state_changed")
            decision = _clock(signal["decision_at"])
            observed = None if signal["observed_through"] is None else _clock(signal["observed_through"])
            setup_end = None if signal["setup_bar_end"] is None else _clock(signal["setup_bar_end"])
            _require(decision > _opening(identity[0])
                     and (observed is None or observed + 2 * MINUTE <= decision)
                     and (setup_end is None or observed is not None and setup_end <= observed),
                     "original_signal_clock_changed")
            if signal["state"] == "signal":
                original_signals.append((index, original))
            else:
                _require(original["outcome"] is None and original["entry_at"] is None
                         and original["cap_at"] is None and original["structural_stop"] is None
                         and signal["signal_bar_start"] is None and signal["signal_bar_end"] is None,
                         "nonsignal_label_not_absent")
                nonsignal_count += 1
            count += 1
        _require(seen == set(expected), "original_target_population_changed")
        union.update(seen)
        _require(len(rows) == len(original_signals), "label_projection_signal_population_changed")
        for row, (index, original) in zip(rows, original_signals):
            _require(type(row) is OriginalLabelRow, "original_label_row_required")
            _same(row.row_key, (policy, projected.setup_id, index, original["session"], original["symbol"]),
                  "original_label_row_key_changed")
            _same(row.signal, original["signal"], "original_signal_projection_changed")
            for name in ("entry_at", "cap_at", "structural_stop", "membership_day_index", "membership_target",
                         "source_index", "volume_source_index"):
                _same(getattr(row, name), original[name], "original_label_plan_projection_changed:" + name)
            _same(row.source, original_report["sources"][original["source_index"]], "original_label_source_changed")
            _same(row.authorization_reference, original_report.get("authorization_reference"),
                  "original_label_authorization_changed")
            _same(row.stored_outcome, original["outcome"], "original_outcome_projection_changed")
            opening = _opening(row.row_key[3])
            _require(row.signal["state"] == "signal" and type(row.structural_stop) is Fraction and row.structural_stop > 0
                     and row.entry_at == opening + ((row.signal["decision_at"] - opening) // (5 * MINUTE) + 1) * 5 * MINUTE
                     and row.cap_at == row.entry_at + 60 * MINUTE, "original_label_plan_clock_changed")
            signal_count += 1
    sources = original_report["sources"]
    coordinates = [(source["session"], source["symbol"]) for source in sources]
    _require(len(coordinates) == len(set(coordinates)) and set(coordinates) == union,
             "original_source_inventory_population_changed")
    return {"original_rows_verified": count, "signal_rows_verified": signal_count,
            "nonsignal_rows_verified": nonsignal_count, "source_coordinates_verified": len(coordinates),
            "exact_original_outcomes_preserved": True,
            "raw_outcomes_verified": False, "caller_authenticates_original_bytes": True}


def _raw_session(chunk: Mapping) -> dict:
    """Independent raw unique OPEN and complete OHLC arrays; no detector bars."""
    _require(isinstance(chunk, Mapping) and set(chunk) == {*META, "symbol", "session", "rows"},
             "raw_chunk_schema_changed")
    day, symbol, rows = chunk["session"], chunk["symbol"], chunk["rows"]
    _require(type(symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None
             and chunk["observation_date"] == day and chunk["available_at"] is None
             and chunk["source"] == "collector klines" and chunk["is_final"] is None
             and chunk["evidence_level"] == "assumed", "raw_chunk_identity_changed")
    retrieved = _clock(chunk["retrieved_at"])
    _require(type(rows) is list and len(rows) <= 600, "raw_row_budget_exceeded")
    start, stop = _opening(day), _closing(day)
    duration = (stop - start) // MINUTE
    origin = (start - datetime(1970, 1, 1, tzinfo=timezone.utc)) // MINUTE
    buckets = {}
    for row in rows:
        _require(isinstance(row, Mapping) and set(row) == RAW_ROW and row["symbol"] == symbol
                 and row["interval"] == "1m" and type(row["open_time_ms"]) is int
                 and (row["fetched_at"] is None or type(row["fetched_at"]) is str), "raw_row_identity_changed")
        minute = row["open_time_ms"] // 60000 - origin
        _require(0 <= minute < duration, "raw_row_outside_session")
        buckets.setdefault(minute, []).append(row)
    minutes, opens = [], []
    for index in range(duration):
        items = buckets.get(index, ())
        prices, opened = None, None
        if len(items) == 1:
            values = [items[0][name] for name in ("open", "high", "low", "close")]
            if all(type(value) is str for value in values):
                try:
                    op, hi, lo, cl = tuple(Decimal(value) for value in values)
                    if all(value.is_finite() and value > 0 for value in (op, hi, lo, cl)) and lo <= min(op, cl) <= max(op, cl) <= hi:
                        prices = (op, hi, lo, cl)
                except (InvalidOperation, ValueError, OverflowError):
                    pass
            if type(items[0]["open"]) is str:
                try:
                    value = Decimal(items[0]["open"])
                    if value.is_finite() and value > 0:
                        opened = value
                except InvalidOperation:
                    pass
        minutes.append(prices)
        opens.append(opened)
    linkage = [[row["open_time_ms"], row["fetched_at"]] for row in rows]
    source = {"session": day, "symbol": symbol,
              "data_vintage": hashlib.sha256(_bytes(chunk) + b"\n").hexdigest(),
              "raw_chunk_metadata": {name: chunk[name] for name in META}, "input_rows": len(rows),
              "row_linkage_sha256": hashlib.sha256(_bytes(linkage)).hexdigest(),
              "source_row_metadata_location": "original_pinned_archive_chunk"}
    return {"source": source, "start": start, "stop": stop, "retrieved": retrieved,
            "minutes": minutes, "opens": opens}


def _outcome(data: Mapping, row: OriginalLabelRow) -> dict:
    """Original all-held-OHLC semantics, including the stop latency window."""
    source, start = data["source"], data["start"]
    meta = source["raw_chunk_metadata"]
    entry, cap, stop = row.entry_at, row.cap_at, row.structural_stop
    result = dict(status="unknown", reason=None, entry_at=entry, exit_at=None,
        structural_stop=stop, entry_open=None, exit_open=None, stop_observation_end=None,
        gross=None, primary_net=None, stress_net=None, primary_cost_bps=33, stress_cost_bps=43,
        evidence_level="assumed", actual_fill_certified=False, actual_execution_authorized=False,
        promotion_allowed=False, exploratory_only=True, assumed_fill=True,
        historical_timing_certified=False, inputs_authenticated_by_caller=True,
        source={"symbol": source["symbol"], "session_date": date.fromisoformat(source["session"]),
                "policy": row.row_key[0], "price_basis": PRICE_BASIS,
                "price_source": {**{key: meta[key] for key in ("source", "is_final", "evidence_level")},
                                 "retrieved_at": data["retrieved"], "data_vintage": source["data_vintage"]},
                "input_sha256": source["data_vintage"], "authorization_reference": row.authorization_reference})
    if not start < row.signal["decision_at"] < entry < cap < data["stop"]:
        result["reason"] = "same_day_cap_infeasible"
        return result
    _require(type(stop) is Fraction and stop > 0, "positive_original_stop_required")
    left, right = (entry - start) // MINUTE, (cap - start) // MINUTE
    first = data["opens"][left]
    if first is None:
        result["reason"] = "entry_open_missing"
        return result
    _require(data["retrieved"] >= entry, "entry_retrieval_precedes_observation")
    first = Fraction(first)
    result["entry_open"] = first
    if first <= stop:
        result.update(status="declined", reason="invalidated_entry")
        return result
    exit_index, reason, index = right, "time_cap", left
    while index < exit_index:
        value = data["minutes"][index]
        if value is None or data["opens"][index] is None or data["opens"][index] != value[0]:
            result["reason"] = "required_path_missing"
            return result
        end = start + (index + 1) * MINUTE
        _require(data["retrieved"] >= end, "path_retrieval_precedes_observation")
        if result["stop_observation_end"] is None and value[3] <= stop and index + 4 <= right:
            result["stop_observation_end"] = end
            exit_index = index + 4
            reason = "delayed_structural_stop" if exit_index < right else "stop_at_time_cap"
        index += 1
    result["exit_at"] = start + exit_index * MINUTE
    final = data["opens"][exit_index]
    if final is None:
        result["reason"] = "exit_open_missing"
        return result
    _require(data["retrieved"] >= result["exit_at"], "exit_retrieval_precedes_observation")
    gross = Fraction(final) / first - 1
    result.update(status="known", reason=reason, exit_open=Fraction(final), gross=gross,
                  primary_net=gross - Fraction(33, 10000), stress_net=gross - Fraction(43, 10000))
    return result


def verify_label_for_row(day: Mapping, original_row: OriginalLabelRow, stored=None, *,
                         inputs_authenticated: bool) -> ValidatedLabel:
    """Compare the original numeric outcome and optional producer with raw day."""
    _require(inputs_authenticated is True and type(original_row) is OriginalLabelRow,
             "caller_authenticated_original_row_required")
    symbol = original_row.row_key[-1]
    return _verified_label(day, original_row, stored, _raw_session(day["original_chunks"][symbol]))


def verify_labels_for_day(day: Mapping, original_rows: tuple[OriginalLabelRow, ...],
                          producer_outcomes: Mapping | None = None, *,
                          inputs_authenticated: bool) -> Mapping:
    """Parse each required raw symbol once; discard the entire cache on return.

    The cache contains only this supplied day and is constructed inside the
    independent verifier. No author-supplied pre-parsed prices are trusted.
    Returning records retain scalar outcomes/source receipts, never raw minutes.
    """
    _require(inputs_authenticated is True and type(original_rows) is tuple
             and all(type(row) is OriginalLabelRow for row in original_rows),
             "caller_authenticated_original_rows_required")
    keys = tuple(row.row_key for row in original_rows)
    _require(len(set(keys)) == len(keys), "duplicate_original_label_row_key")
    _require(producer_outcomes is None or isinstance(producer_outcomes, Mapping)
             and set(producer_outcomes) == set(keys), "producer_day_label_population_changed")
    cache, checked = {}, {}
    for row in original_rows:
        symbol = row.row_key[-1]
        if symbol not in cache:
            cache[symbol] = _raw_session(day["original_chunks"][symbol])
        stored = None if producer_outcomes is None else producer_outcomes[row.row_key]
        _require(stored is not None or row.signal["state"] != "signal" or producer_outcomes is None,
                 "producer_signal_label_missing")
        checked[row.row_key] = _verified_label(day, row, stored, cache[symbol])
    return MappingProxyType(checked)


def _verified_label(day: Mapping, row: OriginalLabelRow, stored, data: Mapping) -> ValidatedLabel:
    policy, setup, index, session, symbol = row.row_key
    _require(policy in ("D+1", "D+2") and setup in ("EA-A", "EA-B") and type(index) is int and index >= 0,
             "original_label_row_key_changed")
    _require(day["session"] == session and day["session_open"] == _opening(session)
             and day["session_close"] == _closing(session), "label_day_changed")
    chunk = day["original_chunks"][symbol]
    _require(chunk["session"] == session and chunk["symbol"] == symbol, "raw_label_coordinate_changed")
    _same(data["source"], row.source, "original_label_raw_source_mismatch")
    _same(day["original_sources"][symbol], data["source"], "projected_label_raw_source_mismatch")
    if row.signal["state"] != "signal":
        _require(row.stored_outcome is None and stored is None, "nonsignal_outcome_changed")
        return ValidatedLabel(row.row_key, "absent", None, None, None, None, None, _freeze({
            "source": data["source"], "raw_available_at": None, "availability_evidence": "assumed"}))
    _require(row.entry_at == data["start"] + ((row.signal["decision_at"] - data["start"]) // (5 * MINUTE) + 1) * 5 * MINUTE
             and row.cap_at == row.entry_at + 60 * MINUTE, "original_label_plan_clock_changed")
    expected = _outcome(data, row)
    _same(row.stored_outcome, expected, "original_stored_raw_outcome_mismatch")
    if stored is not None:
        _same(stored, expected, "producer_raw_outcome_mismatch")
    status = expected["status"]
    end = expected["exit_at"] if status == "known" else None
    available = (end + 3 * MINUTE if status == "known" else
                 row.entry_at + 3 * MINUTE if status == "declined" else None)
    evidence = {"source": data["source"], "raw_available_at": None,
                "availability_evidence": "assumed_OPEN_bucket_end_plus_two_minutes",
                "historical_publication_or_finality_certified": False,
                "caller_authenticates_original_bytes": True}
    return ValidatedLabel(row.row_key, status, expected["reason"],
        expected["primary_net"] if status == "known" else None, end, available,
        _freeze(expected), _freeze(evidence))


def verify_fold_training_view(view: FoldTrainingView, calendar: MLCalendar,
                              feature_rows: Mapping, labels: Mapping) -> dict:
    """Derive fixed date geometry, membership and guards without producer calls."""
    _require(type(view) is FoldTrainingView and type(calendar) is MLCalendar,
             "fixed_fold_records_required")
    _require(type(view.purge_reasons) is tuple and all(
        type(pair) is tuple and len(pair) == 2 and type(pair[0]) is tuple and type(pair[1]) is tuple
        for pair in view.purge_reasons), "immutable_ordered_purge_coordinates_required")
    reference = calendar.source_reference_sessions
    _require(len(reference) == 257 and reference == tuple(sorted(set(reference))), "fixed_257_source_dates_required")
    for session in reference:
        _opening(session)
    _same(calendar.initial_training_sessions, reference[:100], "initial_100_dates_changed")
    _same(calendar.evaluation_reference_sessions, reference[100:], "evaluation_157_dates_changed")
    def week(day):
        parsed = date.fromisoformat(day)
        return (parsed - timedelta(days=parsed.weekday())).isoformat()
    source_counts, counts_by_week = Counter(map(week, reference)), Counter(map(week, reference[100:]))
    weeks = tuple(sorted(counts_by_week))
    _same(calendar.source_reference_weeks, tuple(sorted(source_counts)), "source_weeks_changed")
    _same(calendar.evaluation_reference_weeks, weeks, "evaluation_weeks_changed")
    _same(calendar.evaluation_week_session_counts, dict(counts_by_week), "evaluation_week_counts_changed")
    _same(calendar.evaluation_boundary_week_flags, {key: {"first_evaluation_week": key == weeks[0],
        "last_evaluation_week": key == weeks[-1], "source_sessions_omitted_by_initial_training": source_counts[key] - counts_by_week[key],
        "partial_calendar_week": counts_by_week[key] < 5} for key in weeks}, "evaluation_boundary_week_flags_changed")
    boundaries = (100, 132, 164, 195, 226, 257)
    _require(len(calendar.folds) == 5, "five_fold_slots_required")
    for number, fold in enumerate(calendar.folds, 1):
        a, b = boundaries[number - 1:number + 1]
        _same((fold.index, fold.training_source_slice, fold.test_source_slice, fold.training_sessions,
               fold.test_sessions, fold.first_test_opening),
              (number, (0, a), (a, b), reference[:a], reference[a:b], _opening(reference[a])),
              "fixed_fold_geometry_changed")
    _require(type(view.fold_index) is int and 1 <= view.fold_index <= 5 and view.timing_policy in ("D+1", "D+2"),
             "fixed_fold_identity_changed")
    left, right = boundaries[view.fold_index - 1:view.fold_index + 1]
    train_dates, test_dates = set(reference[:left]), set(reference[left:right])
    cutoff = _opening(reference[left])
    train_keys, x, y, test_keys, test_x, purges = [], [], [], [], [], {}
    counts = {name: 0 for name in ("training_candidates", "feature_unknown", "known_label_candidates", "label_unknown",
        "purged", "observed_labeled_candidates", "distinct_original_sessions", "test_candidates", "test_feature_unknown")}
    for key, vector in feature_rows.items():
        _require(type(key) is tuple and len(key) == 5 and key[0] in ("D+1", "D+2") and key[1] in ("EA-A", "EA-B")
                 and type(key[2]) is int and key[2] >= 0 and key[3] in reference and type(key[4]) is str
                 and re.fullmatch(r"KRX:[0-9A-Z]{6}", key[4]) is not None, "invalid_model_row_key")
        if vector is not None:
            _require(type(vector) is tuple and len(vector) == 10
                     and all(value is None or type(value) is Fraction for value in vector), "exact_ten_feature_vector_required")
        if key[0] != view.timing_policy:
            continue
        observed = vector is not None and None not in vector
        if key[3] in test_dates:
            counts["test_candidates"] += 1
            if observed:
                test_keys.append(key)
                test_x.append(vector)
            else:
                counts["test_feature_unknown"] += 1
        elif key[3] in train_dates:
            counts["training_candidates"] += 1
            if not observed:
                counts["feature_unknown"] += 1
                continue
            label = labels.get(key)
            _require(type(label) is ValidatedLabel and label.row_key == key, "validated_training_label_required")
            if label.status != "known":
                _require(label.y is None, "unknown_label_has_y")
                counts["label_unknown"] += 1
                continue
            _require(type(label.y) is Fraction and type(label.label_end_at) is datetime and label.label_end_at.utcoffset() is not None
                     and type(label.available_at) is datetime and label.available_at == label.label_end_at + 3 * MINUTE,
                     "known_label_maturity_changed")
            counts["known_label_candidates"] += 1
            reasons = []
            if label.label_end_at >= cutoff:
                reasons.append("label_end_reaches_test_opening")
            if label.available_at >= cutoff:
                reasons.append("label_availability_reaches_test_opening")
            if reasons:
                counts["purged"] += 1
                purges[key] = tuple(reasons)
            else:
                train_keys.append(key)
                x.append(vector)
                y.append(label.y)
    dates = tuple(sorted({key[3] for key in train_keys}))
    counts["observed_labeled_candidates"], counts["distinct_original_sessions"] = len(train_keys), len(dates)
    reasons = []
    if len(train_keys) < 100:
        reasons.append("fewer_than_100_known_labels_after_common_features_and_purge")
    if len(dates) < 30:
        reasons.append("fewer_than_30_dates_after_common_features_and_purge")
    expected = (cutoff, INPUT_NAMES, tuple(train_keys), tuple(x), tuple(y), dates, tuple(test_keys), tuple(test_x),
                "unavailable" if reasons else "ready", ";".join(reasons) or None, counts, tuple(purges.items()))
    _same((view.cutoff, view.feature_order, view.train_row_keys, view.X, view.y, view.train_sessions,
           view.test_row_keys, view.test_X, view.status, view.reason, view.counts, view.purge_reasons),
          expected, "independent_fixed_training_view_mismatch")
    return {"fold_index": view.fold_index, "timing_policy": view.timing_policy,
            "training_membership_verified": True, "evaluation_labels_consumed": False,
            "observed_labeled_candidates": len(train_keys), "distinct_original_sessions": len(dates)}
