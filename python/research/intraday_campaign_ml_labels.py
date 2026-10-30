"""Bounded original-EA label sidecars and fixed EC chronological fit views.

No files, market reads, detectors, numerical libraries or fits are used here.
The caller authenticates original report/archive bytes and their full geometry.
Old outcomes are retained only for supervised training and integrity checks;
evaluation features and account admission have no label-completeness input.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
import re
from types import MappingProxyType

from research import intraday_campaign_readiness as readiness
from research import intraday_context_outcomes as outcomes
from research import intraday_context_experiment as eb


POLICIES = ("D+1", "D+2")
SETUPS = ("EA-A", "EA-B")
FEATURE_ORDER = (
    "setup_is_A", "fraction_of_session_elapsed", "target_open_to_signal_return",
    "target_last15_return", "structural_stop_gap_from_signal_close",
    "trigger_close_location", "peer_open_to_signal_return", "peer_last15_return",
    "peer_first30_return", "common_peer_count_divided_by_original_peer_count",
)
FOLD_SLICES = ((0, 100, 100, 132), (0, 132, 132, 164),
               (0, 164, 164, 195), (0, 195, 195, 226), (0, 226, 226, 257))
NUMERIC_FIELDS = ("structural_stop", "entry_open", "exit_open", "gross", "primary_net", "stress_net")
CLOCK_FIELDS = ("entry_at", "exit_at", "stop_observation_end")
FLAGS = {"actual_fill_certified": False, "actual_execution_authorized": False,
         "promotion_allowed": False, "exploratory_only": True, "assumed_fill": True,
         "historical_timing_certified": False, "inputs_authenticated_by_caller": True}
OUTCOME_FIELDS = frozenset(("status", "reason", *NUMERIC_FIELDS, *CLOCK_FIELDS,
                            "primary_cost_bps", "stress_cost_bps", "evidence_level", "source", *FLAGS))
RowKey = tuple[str, str, int, str, str]


class MLLabelsRefusal(ValueError):
    """Integrity or scientific-contract failures never become missing labels."""


@dataclass(frozen=True)
class OriginalLabelRow:
    row_key: RowKey
    signal: Mapping
    entry_at: datetime | None
    cap_at: datetime | None
    structural_stop: Fraction | None
    membership_day_index: int
    membership_target: Mapping
    source_index: int
    volume_source_index: int | None
    source: Mapping
    authorization_reference: str
    stored_outcome: Mapping | None


@dataclass(frozen=True)
class MLLabelProjection:
    setup_id: str
    authentication_reference: str
    source_reference_sessions: tuple[str, ...]
    rows_by_policy: Mapping


@dataclass(frozen=True)
class ValidatedLabel:
    row_key: RowKey
    status: str
    reason: str | None
    y: Fraction | None
    label_end_at: datetime | None
    available_at: datetime | None
    outcome: Mapping | None
    source_evidence: Mapping


@dataclass(frozen=True)
class ChronologicalFold:
    index: int
    training_source_slice: tuple[int, int]
    test_source_slice: tuple[int, int]
    training_sessions: tuple[str, ...]
    test_sessions: tuple[str, ...]
    first_test_opening: datetime


@dataclass(frozen=True)
class MLCalendar:
    source_reference_sessions: tuple[str, ...]
    initial_training_sessions: tuple[str, ...]
    evaluation_reference_sessions: tuple[str, ...]
    source_reference_weeks: tuple[str, ...]
    evaluation_reference_weeks: tuple[str, ...]
    evaluation_week_session_counts: Mapping
    evaluation_boundary_week_flags: Mapping
    folds: tuple[ChronologicalFold, ...]


@dataclass(frozen=True)
class FoldTrainingView:
    fold_index: int
    timing_policy: str
    cutoff: datetime
    feature_order: tuple[str, ...]
    train_row_keys: tuple[RowKey, ...]
    X: tuple[tuple[Fraction, ...], ...]
    y: tuple[Fraction, ...]
    train_sessions: tuple[str, ...]
    test_row_keys: tuple[RowKey, ...]
    test_X: tuple[tuple[Fraction, ...], ...]
    status: str
    reason: str | None
    counts: Mapping
    purge_reasons: tuple[tuple[RowKey, tuple[str, ...]], ...]


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise MLLabelsRefusal(reason)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _fraction(value: object, *, optional: bool = False) -> Fraction | None:
    if value is None and optional:
        return None
    _need(isinstance(value, Mapping) and set(value) == {"numerator", "denominator"},
          "canonical_signed_fraction_required")
    numerator, denominator = value["numerator"], value["denominator"]
    _need(type(numerator) is str and re.fullmatch(r"0|-?[1-9][0-9]*", numerator) is not None
          and type(denominator) is str and re.fullmatch(r"[1-9][0-9]*", denominator) is not None,
          "canonical_signed_fraction_required")
    try:
        result = Fraction(int(numerator), int(denominator))
    except (ValueError, ZeroDivisionError):
        raise MLLabelsRefusal("canonical_signed_fraction_required") from None
    _need(str(result.numerator) == numerator and str(result.denominator) == denominator,
          "noncanonical_fraction")
    return result


def _clock(value: object, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    _need(type(value) is str, "canonical_aware_clock_required")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise MLLabelsRefusal("canonical_aware_clock_required") from None
    _need(result.utcoffset() is not None and result.isoformat() == value,
          "canonical_aware_clock_required")
    return result


def _decode_outcome(value: object) -> Mapping | None:
    if value is None:
        return None
    _need(isinstance(value, Mapping) and set(value) == OUTCOME_FIELDS, "outcome_schema_changed")
    result = dict(value)
    _need(type(result["status"]) is str and result["status"] in readiness.OUTCOME_REASONS
          and type(result["reason"]) is str
          and result["reason"] in readiness.OUTCOME_REASONS[result["status"]], "outcome_state_changed")
    for name in NUMERIC_FIELDS:
        result[name] = _fraction(result[name], optional=name != "structural_stop")
    for name in CLOCK_FIELDS:
        result[name] = _clock(result[name], optional=name != "entry_at")
    _need(result["structural_stop"] > 0 and type(result["primary_cost_bps"]) is int
          and result["primary_cost_bps"] == 33 and type(result["stress_cost_bps"]) is int
          and result["stress_cost_bps"] == 43 and result["evidence_level"] == "assumed",
          "outcome_cost_stop_or_evidence_changed")
    _need(all(type(result[name]) is bool and result[name] is expected for name, expected in FLAGS.items()),
          "outcome_evidence_flags_changed")
    source = result["source"]
    _need(isinstance(source, Mapping) and set(source) == {"symbol", "session_date", "policy", "price_basis",
          "price_source", "input_sha256", "authorization_reference"}, "outcome_source_schema_changed")
    source = dict(source)
    try:
        source["session_date"] = date.fromisoformat(source["session_date"])
    except (TypeError, ValueError):
        raise MLLabelsRefusal("outcome_source_date_invalid") from None
    price = source["price_source"]
    _need(isinstance(price, Mapping) and set(price) == {"retrieved_at", "source", "is_final", "data_vintage",
          "evidence_level"}, "outcome_price_source_schema_changed")
    source["price_source"] = {**price, "retrieved_at": _clock(price["retrieved_at"])}
    result["source"] = source
    return _freeze(result)


def project_ml_labels(report: Mapping, setup_id: str, *, inputs_authenticated: bool,
                      authentication_reference: str) -> MLLabelProjection:
    """Retain exact outcomes for original signals, preserving original indices.

    Full original targets/nonsignals stay in ReadinessInputs, not duplicated in
    this supervised sidecar. The full schema and independent complete original
    population/nonsignal absence check both run while the report is alive.
    """
    _need(inputs_authenticated is True and type(authentication_reference) is str
          and bool(authentication_reference.strip()), "caller_authentication_required")
    readiness.project_report(report, setup_id)
    authorization = report.get("authorization_reference")
    _need(type(authorization) is str and bool(authorization.strip()), "original_authorization_required")
    rows, frozen_sources = {}, {}
    for policy in POLICIES:
        projected = []
        for index, original in enumerate(report["variants"][policy]["days"]):
            if original["signal"]["state"] != "signal":
                continue
            signal = dict(original["signal"])
            for name in ("decision_at", "observed_through", "signal_bar_start", "signal_bar_end", "setup_bar_end"):
                signal[name] = _clock(signal[name], optional=name != "decision_at")
            stop = _fraction(original.get("structural_stop"))
            _need(stop > 0, "positive_original_stop_required")
            key = (policy, setup_id, index, original["session"], original["symbol"])
            source_index = original["source_index"]
            if source_index not in frozen_sources:
                frozen_sources[source_index] = _freeze(report["sources"][source_index])
            projected.append(OriginalLabelRow(key, _freeze(signal),
                _clock(original["entry_at"], optional=True), _clock(original["cap_at"], optional=True), stop,
                original["membership_day_index"], _freeze(original["membership_target"]),
                source_index, original["volume_source_index"], frozen_sources[source_index], authorization,
                _decode_outcome(original["outcome"])))
        rows[policy] = tuple(projected)
    result = MLLabelProjection(setup_id, authentication_reference,
                               tuple(report["reference_sessions"]), _freeze(rows))
    from research.intraday_campaign_ml_label_verifier import verify_ml_label_projection
    verify_ml_label_projection(result, report, inputs_authenticated=True,
                               authentication_reference=authentication_reference)
    return result


def produce_label_for_row(day: Mapping, original_row: OriginalLabelRow, *, inputs_authenticated: bool) -> dict | None:
    """Reuse the original public arithmetic from stored plans, never detectors."""
    _need(inputs_authenticated is True and type(original_row) is OriginalLabelRow, "caller_authentication_required")
    row = original_row
    if row.signal["state"] != "signal":
        return None
    policy, setup, _, session, symbol = row.row_key
    _need(day["session"] == session, "label_day_changed")
    parsed = day.get("original_sessions", {}).get(symbol)
    if parsed is None:
        parsed, _ = eb._read_price(day["original_chunks"][symbol], session, symbol)
    source = day["original_sources"][symbol]
    target = eb.ea.Target(symbol, date.fromisoformat(session), policy, day["session_open"],
                          day["session_close"], eb.PRICE_BASIS, eb._price_source(source))
    signal = eb.signals.Detection(**row.signal)
    candidate = eb.ea.PreparedCandidate(setup, target, signal, "prepared", MappingProxyType({}),
        MappingProxyType({}), (), row.structural_stop, row.entry_at, row.cap_at, False,
        eb._provenance(source, signal.observed_through))
    return outcomes.exploratory_outcome(candidate, parsed.minutes,
        source=outcomes.OutcomeSource(symbol, target.session_date, policy, eb.PRICE_BASIS,
            target.price_source, source["data_vintage"], row.authorization_reference),
        inputs_authenticated=True, opening_prices=eb._opening_prices(day["original_chunks"][symbol]["rows"]))


def fixed_calendar(source_reference_sessions: Sequence[str]) -> MLCalendar:
    """Separate the authenticated 257 source dates from 157 account dates."""
    reference = tuple(source_reference_sessions)
    _need(len(reference) == 257 and reference == tuple(sorted(set(reference))), "fixed_257_source_dates_required")
    for day in reference:
        try:
            _need(type(day) is str and date.fromisoformat(day).isoformat() == day, "canonical_source_date_required")
        except (TypeError, ValueError):
            raise MLLabelsRefusal("canonical_source_date_required") from None
    evaluation = reference[100:]
    week_counts = Counter(readiness._week(day) for day in evaluation)
    source_counts = Counter(readiness._week(day) for day in reference)
    weeks = tuple(sorted(week_counts))
    flags = {week: {"first_evaluation_week": week == weeks[0], "last_evaluation_week": week == weeks[-1],
                    "source_sessions_omitted_by_initial_training": source_counts[week] - week_counts[week],
                    "partial_calendar_week": (week_counts[week] < 5)} for week in weeks}
    folds = tuple(ChronologicalFold(index, (a, b), (c, d), reference[a:b], reference[c:d],
                  readiness._opening(reference[c])) for index, (a, b, c, d) in enumerate(FOLD_SLICES, 1))
    return MLCalendar(reference, reference[:100], evaluation, tuple(sorted(source_counts)), weeks,
                      _freeze(dict(week_counts)), _freeze(flags), folds)


def _row_key(key: object, source: set[str]) -> None:
    _need(type(key) is tuple and len(key) == 5 and key[0] in POLICIES and key[1] in SETUPS
          and type(key[2]) is int and key[2] >= 0 and key[3] in source and type(key[4]) is str
          and re.fullmatch(r"KRX:[0-9A-Z]{6}", key[4]) is not None, "invalid_model_row_key")


def prepare_fold(calendar: MLCalendar, fold_index: int, timing_policy: str,
                 feature_rows: Mapping, labels: Mapping) -> FoldTrainingView:
    """Build exact past-only X/y; evaluation rows are read without their labels.

    Feature mapping iteration preserves the original immutable projection order.
    None, or any missing component of ten, means a feature-unknown row. It never
    becomes an imputed zero. An unavailable slot retains its test population.
    """
    _need(type(calendar) is MLCalendar and type(fold_index) is int and 1 <= fold_index <= 5
          and timing_policy in POLICIES and isinstance(feature_rows, Mapping) and isinstance(labels, Mapping),
          "invalid_fixed_fold_inputs")
    _need(calendar == fixed_calendar(calendar.source_reference_sessions), "fixed_calendar_changed")
    fold = calendar.folds[fold_index - 1]
    training, testing = set(fold.training_sessions), set(fold.test_sessions)
    source = set(calendar.source_reference_sessions)
    train_keys, train_x, train_y, test_keys, test_x = [], [], [], [], []
    counts = Counter({name: 0 for name in ("training_candidates", "feature_unknown", "known_label_candidates",
                                         "label_unknown", "purged", "observed_labeled_candidates",
                                         "distinct_original_sessions", "test_candidates", "test_feature_unknown")})
    purge_reasons = {}
    for key, values in feature_rows.items():
        _row_key(key, source)
        if values is not None:
            _need(type(values) is tuple and len(values) == len(FEATURE_ORDER)
                  and all(item is None or type(item) is Fraction for item in values), "exact_ten_feature_vector_required")
        if key[0] != timing_policy or key[3] not in training | testing:
            continue
        observed = values is not None and all(item is not None for item in values)
        if key[3] in testing:
            counts["test_candidates"] += 1
            if observed:
                test_keys.append(key)
                test_x.append(values)
            else:
                counts["test_feature_unknown"] += 1
            continue  # Deliberately never look up an evaluation label.
        counts["training_candidates"] += 1
        if not observed:
            counts["feature_unknown"] += 1
            continue
        label = labels.get(key)
        _need(type(label) is ValidatedLabel and label.row_key == key, "validated_training_label_required")
        if label.status != "known":
            _need(label.y is None, "unknown_label_has_y")
            counts["label_unknown"] += 1
            continue
        _need(type(label.y) is Fraction and type(label.label_end_at) is datetime
              and type(label.available_at) is datetime and label.label_end_at.utcoffset() is not None
              and label.available_at == label.label_end_at + timedelta(minutes=3), "known_label_maturity_changed")
        counts["known_label_candidates"] += 1
        reasons = tuple(name for name, at in (("label_end_reaches_test_opening", label.label_end_at),
                                             ("label_availability_reaches_test_opening", label.available_at))
                        if at >= fold.first_test_opening)
        if reasons:
            counts["purged"] += 1
            purge_reasons[key] = reasons
            continue
        train_keys.append(key)
        train_x.append(values)
        train_y.append(label.y)
    dates = tuple(sorted({key[3] for key in train_keys}))
    counts["observed_labeled_candidates"] = len(train_keys)
    counts["distinct_original_sessions"] = len(dates)
    failures = []
    if len(train_keys) < 100:
        failures.append("fewer_than_100_known_labels_after_common_features_and_purge")
    if len(dates) < 30:
        failures.append("fewer_than_30_dates_after_common_features_and_purge")
    return FoldTrainingView(fold_index, timing_policy, fold.first_test_opening, FEATURE_ORDER,
        tuple(train_keys), tuple(train_x), tuple(train_y), dates, tuple(test_keys), tuple(test_x),
        "unavailable" if failures else "ready", ";".join(failures) or None,
        _freeze(dict(counts)), tuple(purge_reasons.items()))


def build_fold_views(source_reference_sessions: Sequence[str], feature_rows: Mapping,
                     labels: Mapping, timing_policy: str) -> tuple[FoldTrainingView, ...]:
    calendar = fixed_calendar(source_reference_sessions)
    return tuple(prepare_fold(calendar, index, timing_policy, feature_rows, labels) for index in range(1, 6))
