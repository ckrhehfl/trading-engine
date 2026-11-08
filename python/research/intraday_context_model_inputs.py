"""Shared, target-separated EI numeric inputs for prospective model comparisons.

No imports activate numerical libraries and no function reads market data.
Both learned models receive the same fixed asinh features and binary32 sample
weights. The exact symbol-day Fraction weights remain recorded separately.
LightGBM 4.6.0 stores Dataset weights as float32; the common boundary prevents
an otherwise unnoticed difference from the sequence model's weighting.
https://lightgbm.readthedocs.io/en/v4.6.0/_modules/lightgbm/basic.html#Dataset.set_weight
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
import json
from math import asinh, isfinite
import struct
from typing import Literal

from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_empirical import EmpiricalTrainingRow, weighted_training_rows
from research.intraday_context_features import COMMON_NAMES, CONTEXT_NAMES, HISTORY_MINUTES, MINUTE_NAMES, NumericContextFeatures
from research.intraday_context_questions import QuestionId
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_scenario_forecast_targets import OUTCOMES


SCHEMA = "ei-common-model-inputs-draft-v1"
TRANSFORM = "asinh_each_binary64_feature_v1"
WEIGHT_BOUNDARY = "exact_fraction_then_common_ieee754_binary32_v1"
Mode = Literal["chart", "chart_context"]
_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class ModelTrainingRow:
    empirical: EmpiricalTrainingRow
    features: NumericContextFeatures


@dataclass(frozen=True)
class PreparedInput:
    question_id: QuestionId
    forecast_at: datetime
    minute: tuple[tuple[float, ...], ...]
    static: tuple[float, ...]

    @property
    def flat(self) -> tuple[float, ...]:
        return tuple(value for row in self.minute for value in row) + self.static


@dataclass(frozen=True)
class PreparedTraining:
    mode: Mode
    cutoff_at: datetime
    fold: int
    feature_names: tuple[str, ...]
    rows: tuple[PreparedInput, ...]
    outcomes: tuple[int, ...]
    outcome_mature_at: tuple[datetime, ...]
    exact_weights: tuple[Fraction, ...]
    weights: tuple[float, ...]
    max_weight_rounding_error: Fraction
    candidate_rows: int
    unavailable_by_reason: tuple[tuple[str, int], ...]
    event_count: int  # Unique symbol/session pairs, not trades or independent trials.
    date_count: int
    candidate_input_sha256: str
    roster_sha256: str
    weights_sha256: str
    outcomes_sha256: str
    input_sha256: str


@dataclass(frozen=True)
class PreparedQueries:
    mode: Mode
    cutoff_at: datetime
    fold: int
    feature_names: tuple[str, ...]
    rows: tuple[PreparedInput, ...]
    input_sha256: str


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(value) -> str:
    return sha256(encode(value)).hexdigest()


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _fraction(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def feature_names(mode: Mode) -> tuple[str, ...]:
    _need(type(mode) is str and mode in ("chart", "chart_context"), "EI_model_mode_differs")
    return tuple(f"minute_{index:02d}_{name}" for index in range(HISTORY_MINUTES) for name in MINUTE_NAMES) + (
        COMMON_NAMES + (CONTEXT_NAMES if mode == "chart_context" else ()))


def _metadata(mode: Mode, cutoff_at: datetime, fold: int) -> dict:
    _clock(cutoff_at)
    _need(type(fold) is int and 1 <= fold <= 5, "EI_model_fold_differs")
    return {"schema": SCHEMA, "mode": mode, "cutoff_at": _stamp(cutoff_at), "fold": fold,
            "feature_names": list(feature_names(mode)), "transform": TRANSFORM, "weight_boundary": WEIGHT_BOUNDARY,
            "outcome_order": [list(item) for item in OUTCOMES]}


def question_payload(identity: QuestionId, forecast_at: datetime) -> list:
    _clock(forecast_at)
    _need(type(identity) is tuple and len(identity) == 3 and identity[1] in ("approach", "breakout")
          and type(identity[0]) is tuple and len(identity[0]) == 2
          and type(identity[0][0]) is tuple and len(identity[0][0]) == 2
          and type(identity[0][0][0]) is str and bool(identity[0][0][0])
          and type(identity[0][0][1]) is ResistanceAnchor and type(identity[0][1]) is int and identity[0][1] >= 0
          and (identity[0][1] > 0 or identity[1] == "breakout")
          and type(identity[2]) is tuple and len(identity[2]) == 6, "EI_model_question_identity_differs")
    (symbol, anchor), episode = identity[0]
    start, level, band, target, stop, end = identity[2]
    for clock in (anchor.start_at, anchor.peak_end, anchor.confirmed_at, start, end):
        _clock(clock)
    values = (anchor.start_price, anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band, level, band, target, stop)
    _need(all(type(value) is Fraction and value > 0 for value in values)
          and anchor.start_at < anchor.peak_end <= anchor.confirmed_at <= start == forecast_at
          and end - start == timedelta(minutes=30) and level == anchor.price and band == anchor.band
          and anchor.advance == anchor.price - anchor.start_price
          and 0 < stop <= level - band < level + band < target, "EI_model_question_geometry_differs")
    return [symbol, [_stamp(anchor.start_at), _fraction(anchor.start_price), _stamp(anchor.peak_end),
            _stamp(anchor.confirmed_at), *map(_fraction, (anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band))],
            episode, identity[1], [_stamp(start), *map(_fraction, (level, band, target, stop)), _stamp(end)]]


def _shape(minute, static, mode):
    _need(type(minute) is tuple and len(minute) == HISTORY_MINUTES
          and all(type(row) is tuple and len(row) == len(MINUTE_NAMES) for row in minute)
          and type(static) is tuple and len(static) == len(COMMON_NAMES) + (len(CONTEXT_NAMES) if mode == "chart_context" else 0)
          and all(type(value) is float and isfinite(value) for row in (*minute, static) for value in row),
          "EI_model_numeric_shape_or_value_differs")


def _project(features: NumericContextFeatures, mode: Mode) -> PreparedInput:
    _need(type(features) is NumericContextFeatures and features.mode == mode, "EI_model_feature_mode_differs")
    question_payload(features.question_id, features.forecast_at)
    _shape(features.minute, features.static, mode)
    _need(features.feature_names == feature_names(mode), "EI_model_feature_names_differ")
    for op, high, low, close, volume, observed, scale in features.minute:
        _need(low <= min(op, close) <= max(op, close) <= high and volume >= 0
              and observed in (0.0, 1.0) and scale in (0.0, 1.0)
              and (observed == scale == 1.0 or volume == 0.0), "EI_model_volume_mask_or_ohlc_differs")
    _need(len({row[6] for row in features.minute}) == 1, "EI_model_volume_scale_mask_differs")
    _need(features.static[0] == float(features.question_id[1] == "breakout"), "EI_model_stage_feature_differs")
    if mode == "chart_context":
        for start in (len(COMMON_NAMES), len(COMMON_NAMES) + 3):
            values = features.static[start:start + 3]
            _need(all(value in (0.0, 1.0) for value in values) and sum(values) == 1.0,
                  "EI_model_context_one_hot_differs")
    return PreparedInput(features.question_id, features.forecast_at.astimezone(UTC),
                         tuple(tuple(asinh(value) for value in row) for row in features.minute),
                         tuple(asinh(value) for value in features.static))


def _row_payload(row: PreparedInput) -> list:
    return [question_payload(row.question_id, row.forecast_at), row.minute, row.static]


def _group(row: PreparedInput) -> tuple[str, str, str]:
    return row.question_id[0][0][0], row.forecast_at.astimezone(_KST).date().isoformat(), row.question_id[1]


def _rounded(weight: Fraction) -> float:
    result = struct.unpack("!f", struct.pack("!f", float(weight)))[0]
    _need(isfinite(result) and result > 0, "EI_model_weight_numeric_boundary_exceeded")
    return result


def training_contract(prepared: PreparedTraining) -> dict:
    """Small artifact binding; the input digest also covers rows and label maturity."""
    return {**_metadata(prepared.mode, prepared.cutoff_at, prepared.fold),
        "candidate_rows": prepared.candidate_rows, "supported_rows": len(prepared.rows),
        "unavailable_by_reason": [list(item) for item in prepared.unavailable_by_reason],
        "event_count": prepared.event_count, "date_count": prepared.date_count,
        "event_count_unit": "symbol_session_not_trade_or_independent_trial",
        "weighted_stage_groups": len({_group(row) for row in prepared.rows}),
        "max_weight_rounding_error": _fraction(prepared.max_weight_rounding_error),
        **{name: getattr(prepared, name) for name in ("candidate_input_sha256", "roster_sha256", "weights_sha256",
                                                    "outcomes_sha256", "input_sha256")}}


def validate_training_contract(value: dict) -> None:
    """Validate saved metadata without opening, rebuilding or fitting inputs."""
    hashes = ("candidate_input_sha256", "roster_sha256", "weights_sha256", "outcomes_sha256", "input_sha256")
    fields = {"schema", "mode", "cutoff_at", "fold", "feature_names", "transform", "weight_boundary", "outcome_order",
              "candidate_rows", "supported_rows", "unavailable_by_reason", "event_count", "date_count",
              "event_count_unit", "weighted_stage_groups", "max_weight_rounding_error", *hashes}
    _need(type(value) is dict and set(value) == fields and type(value["cutoff_at"]) is str,
          "EI_model_saved_training_contract_fields_differ")
    cutoff = datetime.fromisoformat(value["cutoff_at"])
    expected = _metadata(value["mode"], cutoff, value["fold"])
    _need(encode({key: value[key] for key in expected}) == encode(expected), "EI_model_saved_training_metadata_differs")
    _need(all(type(value[name]) is str and len(value[name]) == 64
              and all(char in "0123456789abcdef" for char in value[name]) for name in hashes),
          "EI_model_saved_training_hashes_differ")
    missing = value["unavailable_by_reason"]
    _need(type(missing) is list and all(type(item) is list and len(item) == 2 and type(item[0]) is str
          and bool(item[0].strip()) and type(item[1]) is int and item[1] > 0 for item in missing)
          and missing == sorted(missing) and len({item[0] for item in missing}) == len(missing),
          "EI_model_saved_missing_support_differs")
    _need(all(type(value[name]) is int and value[name] >= 0 for name in
              ("candidate_rows", "supported_rows", "event_count", "date_count", "weighted_stage_groups"))
          and value["candidate_rows"] == value["supported_rows"] + sum(item[1] for item in missing)
          and 0 <= value["date_count"] <= value["event_count"] <= value["supported_rows"]
          and value["event_count"] <= value["weighted_stage_groups"] <= min(2 * value["event_count"], value["supported_rows"])
          and value["event_count_unit"] == "symbol_session_not_trade_or_independent_trial"
          and (value["supported_rows"] == 0) == (value["date_count"] == value["event_count"] == 0),
          "EI_model_saved_training_support_differs")
    error = value["max_weight_rounding_error"]
    _need(type(error) is list and len(error) == 2 and all(type(item) is int for item in error)
          and error[1] > 0 and 0 <= Fraction(*error) <= Fraction(1, 2**24)
          and _fraction(Fraction(*error)) == error and (value["supported_rows"] > 0 or error == [0, 1]),
          "EI_model_saved_weight_boundary_differs")


def _training_digest(prepared: PreparedTraining) -> str:
    contract = training_contract(prepared)
    del contract["input_sha256"]
    return _sha({"contract": contract, "rows": [_row_payload(row) for row in prepared.rows],
                 "outcomes": prepared.outcomes, "mature_at": [_stamp(value) for value in prepared.outcome_mature_at],
                 "exact_weights": list(map(_fraction, prepared.exact_weights)), "weights": prepared.weights})


def prepare_training(rows: Iterable[ModelTrainingRow], *, with_context: bool, cutoff_at: datetime, fold: int) -> PreparedTraining:
    _need(type(with_context) is bool, "EI_model_explicit_context_mode_required")
    mode = "chart_context" if with_context else "chart"
    _metadata(mode, cutoff_at, fold)
    supplied = tuple(rows)
    _need(all(type(row) is ModelTrainingRow and type(row.empirical) is EmpiricalTrainingRow for row in supplied),
          "EI_model_typed_training_rows_required")
    weighted = weighted_training_rows((row.empirical for row in supplied), cutoff_at=cutoff_at)
    projected, candidate_payload = {}, []
    missing = Counter()
    for row in supplied:
        features = _project(row.features, mode)
        empirical = row.empirical
        _need((features.question_id, features.forecast_at) == (empirical.question_id, empirical.forecast_at),
              "EI_model_training_feature_identity_differs")
        if with_context:
            origin = tuple(float(empirical.origin_context == value) for value in ("A", "B", "other"))
            _need(row.features.static[len(COMMON_NAMES):len(COMMON_NAMES) + 3] == origin,
                  "EI_model_training_origin_context_differs")
        projected[features.question_id] = features
        outcome = None if empirical.outcome is None else [empirical.outcome.index, _stamp(empirical.outcome.mature_at)]
        candidate_payload.append([_row_payload(features), empirical.origin_context, outcome, empirical.unavailable_reason])
        if empirical.outcome is None:
            missing[empirical.unavailable_reason] += 1
    prepared_rows = tuple(projected[row.question_id] for row, _ in weighted)
    exact = tuple(weight for _, weight in weighted)
    weights = tuple(map(_rounded, exact))
    outcomes = tuple(row.outcome.index for row, _ in weighted)
    maturity = tuple(row.outcome.mature_at.astimezone(UTC) for row, _ in weighted)
    groups = {_group(row) for row in prepared_rows}
    roster = [question_payload(row.question_id, row.forecast_at) for row in prepared_rows]
    values = dict(mode=mode, cutoff_at=cutoff_at.astimezone(UTC), fold=fold, feature_names=feature_names(mode),
        rows=prepared_rows, outcomes=outcomes, outcome_mature_at=maturity, exact_weights=exact, weights=weights,
        max_weight_rounding_error=max((abs(Fraction.from_float(value) - expected) for value, expected in zip(weights, exact)),
                                      default=Fraction()), candidate_rows=len(supplied), unavailable_by_reason=tuple(sorted(missing.items())),
        event_count=len({group[:2] for group in groups}), date_count=len({group[1] for group in groups}),
        candidate_input_sha256=_sha(sorted(candidate_payload, key=encode)), roster_sha256=_sha(roster),
        weights_sha256=_sha([roster, list(map(_fraction, exact)), weights]), outcomes_sha256=_sha([roster, outcomes, list(map(_stamp, maturity))]))
    provisional = PreparedTraining(**values, input_sha256="")
    result = PreparedTraining(**values, input_sha256=_training_digest(provisional))
    validate_training(result)
    return result


def _validate_rows(rows: tuple[PreparedInput, ...], mode: Mode, cutoff_at: datetime, *, training: bool) -> None:
    _need(type(rows) is tuple, "EI_model_immutable_rows_required")
    seen = set()
    for row in rows:
        _need(type(row) is PreparedInput, "EI_model_prepared_row_required")
        question_payload(row.question_id, row.forecast_at)
        _shape(row.minute, row.static, mode)
        _need(row.question_id not in seen and (row.forecast_at < cutoff_at if training else row.forecast_at >= cutoff_at),
              "EI_model_duplicate_or_out_of_split_row")
        seen.add(row.question_id)


def validate_training(prepared: PreparedTraining) -> None:
    _need(type(prepared) is PreparedTraining, "EI_model_prepared_training_required")
    _metadata(prepared.mode, prepared.cutoff_at, prepared.fold)
    _need(prepared.feature_names == feature_names(prepared.mode), "EI_model_feature_order_differs")
    _validate_rows(prepared.rows, prepared.mode, prepared.cutoff_at, training=True)
    total = len(prepared.rows)
    _need(all(type(values) is tuple and len(values) == total for values in (
        prepared.outcomes, prepared.outcome_mature_at, prepared.exact_weights, prepared.weights)), "EI_model_training_lengths_differ")
    counts = Counter(_group(row) for row in prepared.rows)
    for row, outcome, mature, exact, numeric in zip(prepared.rows, prepared.outcomes, prepared.outcome_mature_at,
                                                 prepared.exact_weights, prepared.weights, strict=True):
        _clock(mature)
        _need(type(outcome) is int and 0 <= outcome < len(OUTCOMES), "EI_model_outcome_index_differs")
        earliest = (3, 8, 18)[outcome // 3] if outcome < 9 else 32
        _need(row.forecast_at + timedelta(minutes=earliest) <= mature < prepared.cutoff_at
              and type(exact) is Fraction and exact == Fraction(1, counts[_group(row)])
              and type(numeric) is float and numeric == _rounded(exact), "EI_model_outcome_maturity_or_weight_differs")
    missing = prepared.unavailable_by_reason
    _need(type(missing) is tuple and all(type(item) is tuple and len(item) == 2 and type(item[0]) is str
          and item[0].strip() and type(item[1]) is int and item[1] > 0 for item in missing)
          and tuple(sorted(set(missing))) == missing and len({item[0] for item in missing}) == len(missing)
          and type(prepared.candidate_rows) is int and prepared.candidate_rows == total + sum(value for _, value in missing)
          and type(prepared.event_count) is int and prepared.event_count == len({group[:2] for group in counts})
          and type(prepared.date_count) is int and prepared.date_count == len({group[1] for group in counts}),
          "EI_model_training_support_differs")
    expected_error = max((abs(Fraction.from_float(value) - exact) for value, exact in zip(prepared.weights, prepared.exact_weights)),
                         default=Fraction())
    _need(type(prepared.max_weight_rounding_error) is Fraction and prepared.max_weight_rounding_error == expected_error,
          "EI_model_weight_boundary_report_differs")
    roster = [question_payload(row.question_id, row.forecast_at) for row in prepared.rows]
    _need(prepared.roster_sha256 == _sha(roster)
          and prepared.weights_sha256 == _sha([roster, list(map(_fraction, prepared.exact_weights)), prepared.weights])
          and prepared.outcomes_sha256 == _sha([roster, prepared.outcomes, list(map(_stamp, prepared.outcome_mature_at))])
          and type(prepared.candidate_input_sha256) is str and len(prepared.candidate_input_sha256) == 64
          and all(char in "0123456789abcdef" for char in prepared.candidate_input_sha256)
          and prepared.input_sha256 == _training_digest(prepared), "EI_model_training_digest_differs")


def _query_digest(prepared: PreparedQueries) -> str:
    return _sha({**_metadata(prepared.mode, prepared.cutoff_at, prepared.fold),
                 "rows": [_row_payload(row) for row in prepared.rows]})


def prepare_queries(features: Iterable[NumericContextFeatures], *, with_context: bool, cutoff_at: datetime, fold: int) -> PreparedQueries:
    _need(type(with_context) is bool, "EI_model_explicit_context_mode_required")
    mode = "chart_context" if with_context else "chart"
    _metadata(mode, cutoff_at, fold)
    rows = tuple(_project(row, mode) for row in features)
    provisional = PreparedQueries(mode, cutoff_at.astimezone(UTC), fold, feature_names(mode), rows, "")
    result = PreparedQueries(mode, cutoff_at.astimezone(UTC), fold, feature_names(mode), rows, _query_digest(provisional))
    validate_queries(result)
    return result


def validate_queries(prepared: PreparedQueries) -> None:
    _need(type(prepared) is PreparedQueries, "EI_model_target_free_queries_required")
    _metadata(prepared.mode, prepared.cutoff_at, prepared.fold)
    _need(prepared.feature_names == feature_names(prepared.mode), "EI_model_query_feature_order_differs")
    _validate_rows(prepared.rows, prepared.mode, prepared.cutoff_at, training=False)
    _need(prepared.input_sha256 == _query_digest(prepared), "EI_model_query_digest_differs")
