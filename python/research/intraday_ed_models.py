"""ED-H1 chronological views and one fixed LightGBM numerical adapter.

No readers, transport, account, fit ledger or evaluation labels are consumed by
inference. The caller authenticates raw inputs, common support, label replay and
source/runtime pins, and authorizes each fit once. Native training and the
pinned native model parser are trusted boundaries; independent fold/raw checks
belong to the caller. Scalar tree traversal separately checks native prediction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from fractions import Fraction
import math
import re
import struct
from types import MappingProxyType

from research import intraday_campaign_ml_models as numeric
from research.intraday_campaign_ml_labels import ChronologicalFold, MLCalendar, fixed_calendar
from research.intraday_ed_inputs import EVENT_TYPE_NAMES, FEATURE_NAMES


WITHOUT_FILING_FEATURE_NAMES = FEATURE_NAMES[:191]
SCENARIOS = ("S1", "S2")
LABEL_STATUSES = ("known_traded", "known_declined_entry", "unknown")


class EDModelRefusal(ValueError):
    """A violated contract is not silently converted to missingness."""


@dataclass(frozen=True)
class EDTrainingExample:
    parent_id: str
    symbol: str
    scenario: str
    session: str
    decision_at: datetime
    features: tuple[float, ...] | None
    common_support: bool
    event_types: tuple[str, ...]
    label_status: str
    primary_label: Fraction | None
    label_end_at: datetime | None
    mature_at: datetime | None
    reason: str | None = None


@dataclass(frozen=True)
class EDInferenceRow:
    parent_id: str
    symbol: str
    session: str
    decision_at: datetime
    features: tuple[float, ...] | None
    event_types: tuple[str, ...]


@dataclass(frozen=True)
class EDTrainingRow:
    parent_id: str
    symbol: str
    session: str
    decision_at: datetime
    features: tuple[float, ...]
    event_types: tuple[str, ...]
    y: Fraction
    label_status: str
    label_end_at: datetime
    mature_at: datetime


@dataclass(frozen=True)
class EDFoldView:
    source_reference_sessions: tuple[str, ...]
    fold: ChronologicalFold
    scenario: str
    feature_names: tuple[str, ...]
    training: tuple[EDTrainingRow, ...]
    testing: tuple[EDInferenceRow, ...]
    status: str
    reason: str | None
    counts: Mapping[str, int]
    exclusions: tuple[tuple[str, tuple[str, ...]], ...]
    training_group_counts: Mapping[str, int]

    @property
    def X(self) -> tuple[tuple[float, ...], ...]:
        return tuple(row.features for row in self.training)

    @property
    def y(self) -> tuple[Fraction, ...]:
        return tuple(row.y for row in self.training)

    @property
    def train_ids(self) -> tuple[str, ...]:
        return tuple(row.parent_id for row in self.training)

    @property
    def test_ids(self) -> tuple[str, ...]:
        return tuple(row.parent_id for row in self.testing)

    @property
    def test_X(self) -> tuple[tuple[float, ...] | None, ...]:
        return tuple(row.features for row in self.testing)

    @property
    def test_sessions(self) -> tuple[str, ...]:
        return self.fold.test_sessions


@dataclass(frozen=True)
class EDModelBinding:
    source_sha256: str
    runtime_sha256: str
    snapshot_sha256: str
    registration_sha256: str
    fold_index: int
    scenario: str
    context_mode: str = "full"


@dataclass(frozen=True)
class EDModelArtifact:
    payload: Mapping


@dataclass(frozen=True)
class EDFitResult:
    status: str
    reason: str | None
    artifact: EDModelArtifact | None
    unavailable_sessions: tuple[str, ...]


@dataclass(frozen=True)
class EDPrediction:
    parent_id: str
    status: str
    reason: str | None
    prediction_hex: str | None
    accepted: bool | None
    unseen_types: tuple[str, ...]
    extrapolation: bool


@dataclass(frozen=True)
class EDPredictionBatch:
    fold_index: int
    scenario: str
    status: str
    reason: str | None
    evaluation_sessions: tuple[str, ...]
    unavailable_sessions: tuple[str, ...]
    rows: tuple[EDPrediction, ...]


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise EDModelRefusal(reason)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (tuple, list):
        return tuple(_freeze(item) for item in value)
    return value


def _names(without_filing: bool) -> tuple[str, ...]:
    _need(type(without_filing) is bool, "context_removal_must_be_boolean")
    _need(len(FEATURE_NAMES) == 206 and len(WITHOUT_FILING_FEATURE_NAMES) == 191,
          "ED_feature_contract_changed")
    return WITHOUT_FILING_FEATURE_NAMES if without_filing else FEATURE_NAMES


def _types(values: tuple[str, ...], *, observed: bool) -> None:
    _need(type(values) is tuple and all(value in EVENT_TYPE_NAMES for value in values)
          and len(set(values)) == len(values)
          and values == tuple(value for value in EVENT_TYPE_NAMES if value in values)
          and ("NO_EVENT" not in values or values == ("NO_EVENT",))
          and (not observed or bool(values)), "invalid_fixed_event_types")


def _features(values: tuple[float, ...] | None, event_types: tuple[str, ...]):
    if values is None:
        _types(event_types, observed=False)
        return None
    _need(type(values) is tuple and len(values) == 206, "ED_exact_206_feature_vector_required")
    row = tuple(numeric._number(value) for value in values)
    _types(event_types, observed=True)
    expected = tuple(float(name in event_types) for name in EVENT_TYPE_NAMES)
    _need(row[191:195] == expected and all(value in (0.0, 1.0) for value in row[195:])
          and sum(row[195:]) == 1
          and (row[195] == 1) == (event_types == ("NO_EVENT",)),
          "ED_fixed_filing_encoding_mismatch")
    return row


def _identity(row: EDTrainingExample, source: set[str]) -> None:
    _need(type(row) is EDTrainingExample and type(row.parent_id) is str and bool(row.parent_id)
          and type(row.symbol) is str and bool(row.symbol) and row.scenario in SCENARIOS
          and row.session in source and type(row.common_support) is bool,
          "invalid_ED_example_identity")
    _need(type(row.decision_at) is datetime and row.decision_at.utcoffset() is not None
          and row.decision_at.date().isoformat() == row.session,
          "ED_aware_same_session_decision_required")


def _group_counts(rows: tuple[EDTrainingRow, ...]) -> Mapping[str, int]:
    counts = {}
    for kind in EVENT_TYPE_NAMES:
        selected = tuple(row for row in rows if kind in row.event_types)
        counts[kind] = len(selected)
        counts[kind + "__sessions"] = len({row.session for row in selected})
        counts[kind + "__known_traded"] = sum(row.label_status == "known_traded" for row in selected)
    return MappingProxyType(counts)


def _minimum(rows: tuple[EDTrainingRow, ...]) -> tuple[str, ...]:
    traded = tuple(row for row in rows if row.label_status == "known_traded")
    reasons = []
    if len(traded) < 100:
        reasons.append("fewer_than_100_known_traded_labels_after_support_and_purge")
    if len({row.session for row in traded}) < 30:
        reasons.append("fewer_than_30_known_traded_training_sessions_after_support_and_purge")
    return tuple(reasons)


def prepare_ed_fold(calendar: MLCalendar, fold_index: int, scenario: str,
                    examples: Sequence[EDTrainingExample], *, without_filing: bool = False) -> EDFoldView:
    """Construct past-only rows; evaluation labels are never inspected.

    Fixed rolling features already use each row's causal raw history. There is
    no fitted scaling, feature selection or evaluation-dependent preprocessing.
    Both context modes require the identical observed full206 support first.
    """
    _need(type(calendar) is MLCalendar and calendar == fixed_calendar(calendar.source_reference_sessions)
          and type(fold_index) is int and 1 <= fold_index <= 5 and scenario in SCENARIOS,
          "fixed_ED_calendar_and_scenario_required")
    names = _names(without_filing)
    fold = calendar.folds[fold_index - 1]
    training_dates, testing_dates = set(fold.training_sessions), set(fold.test_sessions)
    source = set(calendar.source_reference_sessions)
    selected, seen, seen_coordinates = [], set(), set()
    for row in examples:
        _identity(row, source)
        key = (row.scenario, row.parent_id)
        _need(key not in seen, "duplicate_ED_parent_identity")
        seen.add(key)
        coordinate = (row.scenario, row.symbol, row.session)
        _need(coordinate not in seen_coordinates, "one_ED_parent_per_stock_session_required")
        seen_coordinates.add(coordinate)
        if row.scenario == scenario and row.session in training_dates | testing_dates:
            selected.append(row)
    selected.sort(key=lambda row: (row.session, row.symbol, row.decision_at, row.parent_id))
    training, testing, exclusions = [], [], []
    counts = Counter({key: 0 for key in ("training_candidates", "training_input_unknown", "label_unknown",
        "purged", "known_traded", "known_declined_entry", "known_traded_sessions", "test_candidates",
        "test_input_unknown")})
    for row in selected:
        values = _features(row.features, row.event_types)
        if not row.common_support:
            values = None
        projected = None if values is None else values[:len(names)]
        if row.session in testing_dates:
            counts["test_candidates"] += 1
            counts["test_input_unknown"] += projected is None
            testing.append(EDInferenceRow(row.parent_id, row.symbol, row.session, row.decision_at,
                                         projected, row.event_types))
            if projected is None:
                exclusions.append((row.parent_id, ("test_common_input_unknown",)))
            continue  # Do not read any label/status/maturity field from a test row.
        counts["training_candidates"] += 1
        if projected is None:
            counts["training_input_unknown"] += 1
            exclusions.append((row.parent_id, ("training_common_input_unknown",)))
            continue
        _need(row.label_status in LABEL_STATUSES, "invalid_ED_training_label_status")
        if row.label_status == "unknown":
            _need(row.primary_label is None, "unknown_ED_label_has_value")
            counts["label_unknown"] += 1
            exclusions.append((row.parent_id, ("training_label_unknown", row.reason or "unspecified")))
            continue
        _need(type(row.primary_label) is Fraction and type(row.label_end_at) is datetime
              and type(row.mature_at) is datetime and row.label_end_at.utcoffset() is not None
              and row.mature_at.utcoffset() is not None
              and row.decision_at <= row.label_end_at <= row.mature_at,
              "known_ED_label_and_maturity_required")
        _need(row.label_status != "known_declined_entry" or row.primary_label == 0,
              "declined_entry_is_policy_cash_zero_not_a_fill")
        reasons = tuple(reason for reason, at in (
            ("label_end_reaches_test_opening", row.label_end_at),
            ("label_maturity_reaches_test_opening", row.mature_at)) if at >= fold.first_test_opening)
        if reasons:
            counts["purged"] += 1
            exclusions.append((row.parent_id, reasons))
            continue
        training.append(EDTrainingRow(row.parent_id, row.symbol, row.session, row.decision_at,
            projected, row.event_types, row.primary_label, row.label_status, row.label_end_at, row.mature_at))
        counts[row.label_status] += 1
    train = tuple(training)
    counts["known_traded_sessions"] = len({row.session for row in train if row.label_status == "known_traded"})
    failures = _minimum(train)
    return EDFoldView(calendar.source_reference_sessions, fold, scenario, names, train, tuple(testing),
        "unavailable" if failures else "ready", ";".join(failures) or None,
        MappingProxyType(dict(counts)), tuple(exclusions), _group_counts(train))


def _check_view(view: EDFoldView) -> None:
    _need(type(view) is EDFoldView and type(view.fold) is ChronologicalFold
          and view.scenario in SCENARIOS
          and view.feature_names in (FEATURE_NAMES, WITHOUT_FILING_FEATURE_NAMES), "invalid_ED_fold_view")
    _need(type(view.fold.index) is int and view.fold.index in range(1, 6)
          and view.fold == fixed_calendar(view.source_reference_sessions).folds[view.fold.index - 1],
          "ED_fixed_fold_geometry_required")
    _need(type(view.training) is tuple and type(view.testing) is tuple
          and all(type(row) is EDTrainingRow for row in view.training)
          and all(type(row) is EDInferenceRow for row in view.testing), "typed_ED_fold_rows_required")
    train_ids, test_ids = view.train_ids, view.test_ids
    _need(len(set(train_ids)) == len(train_ids) and len(set(test_ids)) == len(test_ids)
          and not set(train_ids) & set(test_ids), "ED_fold_parent_identity_overlap")
    for row in (*view.training, *view.testing):
        dates = view.fold.training_sessions if type(row) is EDTrainingRow else view.fold.test_sessions
        _need(row.session in dates and row.decision_at.date().isoformat() == row.session
              and row.decision_at.utcoffset() is not None, "ED_fold_row_chronology_mismatch")
        if row.features is not None:
            _need(len(row.features) == len(view.feature_names), "ED_fold_feature_dimensions_changed")
            tuple(numeric._number(value) for value in row.features)
        _types(row.event_types, observed=row.features is not None)
        if view.feature_names == FEATURE_NAMES:
            _features(row.features, row.event_types)
        if type(row) is EDTrainingRow:
            _need(type(row.y) is Fraction and row.label_status in LABEL_STATUSES[:2]
                  and row.features is not None and row.decision_at <= row.label_end_at <= row.mature_at
                  and row.label_end_at < view.fold.first_test_opening
                  and row.mature_at < view.fold.first_test_opening
                  and (row.label_status != "known_declined_entry" or row.y == 0),
                  "ED_training_purge_or_label_changed")
    reasons = _minimum(view.training)
    _need(view.status == ("unavailable" if reasons else "ready")
          and view.reason == (";".join(reasons) or None)
          and dict(view.training_group_counts) == dict(_group_counts(view.training)),
          "ED_fold_training_minimum_or_groups_changed")


def _binding(binding: EDModelBinding, view: EDFoldView) -> dict:
    _need(type(binding) is EDModelBinding and binding.fold_index == view.fold.index
          and binding.scenario == view.scenario
          and binding.context_mode == ("full" if view.feature_names == FEATURE_NAMES else "without_filing")
          and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
                  for value in (binding.source_sha256, binding.runtime_sha256,
                                binding.snapshot_sha256, binding.registration_sha256)),
          "immutable_ED_scientific_runtime_binding_required")
    return asdict(binding)


def _training_contract(view: EDFoldView) -> dict:
    return {"source_reference_sessions": view.source_reference_sessions,
        "fold": asdict(view.fold) | {"first_test_opening": view.fold.first_test_opening.isoformat()},
        "scenario": view.scenario, "feature_names": list(view.feature_names),
        "rows": [{"parent_id": row.parent_id, "symbol": row.symbol, "session": row.session,
            "decision_at": row.decision_at.isoformat(), "label_status": row.label_status,
            "label_end_at": row.label_end_at.isoformat(), "mature_at": row.mature_at.isoformat(),
            "exact_y": [str(row.y.numerator), str(row.y.denominator)], "event_types": list(row.event_types)}
            for row in view.training], "training_group_counts": dict(view.training_group_counts),
        "preprocessing": "unscaled_fixed_schema_no_fitted_transform", "threshold": "strictly>0"}


def fit_ed_lightgbm(view: EDFoldView, binding: EDModelBinding) -> EDFitResult:
    """Call exactly one fixed native fit, or return explicit fold unavailability."""
    _check_view(view)
    bound = _binding(binding, view)
    if view.status == "unavailable":
        return EDFitResult("unavailable", view.reason, None, view.test_sessions)
    np, lgb, runtime = numeric._libraries("lightgbm")
    rows, labels = view.X, tuple(numeric._number(value) for value in view.y)
    model = numeric._fit_lightgbm_matrix(rows, labels, view.feature_names, np, lgb)
    payload = {"schema": "ed-h1-fixed-lightgbm-v1", "learner": "lightgbm",
        "feature_names": list(view.feature_names), "binding": bound, "binding_sha256": numeric._digest(bound),
        "training_contract": _training_contract(view), "training_rows": len(rows),
        "train_features_sha256": numeric._numeric_matrix_hash(rows, len(view.feature_names)),
        "train_labels_sha256": numeric._vector_hash(labels), "parameters": numeric.lightgbm_parameter_manifest(),
        "runtime": runtime, "numerical_contract": dict(numeric.NUMERICAL_CONTRACT), "model": model}
    payload["artifact_sha256"] = numeric._digest(payload)
    return EDFitResult("ready", None, EDModelArtifact(_freeze(payload)), ())


def _check_artifact(result: EDFitResult, view: EDFoldView):
    _check_view(view)
    _need(view.status == "ready" and type(result) is EDFitResult
          and result.status == "ready" and result.reason is None
          and result.unavailable_sessions == () and type(result.artifact) is EDModelArtifact,
          "fitted_ED_model_required")
    payload = result.artifact.payload
    required = {"schema", "learner", "feature_names", "binding", "binding_sha256", "training_contract",
        "training_rows", "train_features_sha256", "train_labels_sha256", "parameters", "runtime",
        "numerical_contract", "model", "artifact_sha256"}
    _need(isinstance(payload, Mapping) and set(payload) == required
          and payload["artifact_sha256"] == numeric._digest({key: value for key, value in payload.items()
                                                            if key != "artifact_sha256"}),
          "ED_model_artifact_hash_or_schema_mismatch")
    names = view.feature_names
    _need(payload["schema"] == "ed-h1-fixed-lightgbm-v1" and payload["learner"] == "lightgbm"
          and tuple(payload["feature_names"]) == names
          and payload["binding_sha256"] == numeric._digest(payload["binding"])
          and numeric._canonical(payload["training_contract"]) == numeric._canonical(_training_contract(view))
          and type(payload["training_rows"]) is int and payload["training_rows"] == len(view.training)
          and payload["train_features_sha256"] == numeric._numeric_matrix_hash(view.X, len(names))
          and payload["train_labels_sha256"] == numeric._vector_hash(tuple(map(numeric._number, view.y)))
          and numeric._canonical(payload["parameters"]) == numeric._canonical(numeric.lightgbm_parameter_manifest())
          and numeric._canonical(payload["numerical_contract"]) == numeric._canonical(numeric.NUMERICAL_CONTRACT),
          "ED_model_scientific_or_training_binding_mismatch")
    _binding(EDModelBinding(**payload["binding"]), view)
    np, lgb, runtime = numeric._libraries("lightgbm")
    _need(numeric._canonical(payload["runtime"]) == numeric._canonical(runtime), "ED_model_runtime_mismatch")
    # Convert immutable tuple containers back to native JSON lists for parser helpers.
    model = numeric._copy(payload["model"])
    booster = numeric._reload_lightgbm_matrix(model, lgb, names)
    try:
        stored = tuple(struct.unpack(">f", struct.pack(">f", numeric._number(value)))[0] for value in view.y)
    except (OverflowError, struct.error) as exc:
        raise EDModelRefusal("nonfinite_native_float32_training_label") from exc
    _need(model["native_training_labels_sha256"] == numeric._vector_hash(stored),
          "ED_native_float32_training_labels_mismatch")
    return payload, np, booster


def predict_ed_lightgbm(result: EDFitResult, view: EDFoldView) -> EDPredictionBatch:
    """Predict without test labels; fixed-vocabulary unseen types remain computable."""
    _check_view(view)
    _need(type(result) is EDFitResult, "typed_ED_fit_result_required")
    if result.status == "unavailable":
        _need(result.artifact is None and view.status == "unavailable" and result.reason == view.reason
              and result.unavailable_sessions == view.test_sessions, "invalid_ED_unavailable_result")
        rows = tuple(EDPrediction(row.parent_id, "unavailable", result.reason, None, None, (), False)
                     for row in view.testing)
        return EDPredictionBatch(view.fold.index, view.scenario, "unavailable", result.reason,
                                 view.test_sessions, view.test_sessions, rows)
    _, np, booster = _check_artifact(result, view)
    observed = tuple(row.features for row in view.testing if row.features is not None)
    values = iter(numeric._predict_lightgbm_matrix(booster, observed, np))
    rows = []
    for row in view.testing:
        unseen = tuple(kind for kind in row.event_types if view.training_group_counts[kind] == 0)
        if row.features is None:
            rows.append(EDPrediction(row.parent_id, "unavailable", "common_input_unknown", None, None, unseen, bool(unseen)))
        else:
            value = next(values)
            rows.append(EDPrediction(row.parent_id, "known", None, value.hex(), value > 0, unseen, bool(unseen)))
    return EDPredictionBatch(view.fold.index, view.scenario, "ready", None, view.test_sessions, (), tuple(rows))


def verify_ed_model(result: EDFitResult, view: EDFoldView, predictions: EDPredictionBatch,
                    *, binding: EDModelBinding) -> dict:
    """Bind independent caller rows and replay native plus scalar trees; never fit."""
    _check_view(view)
    _need(type(result) is EDFitResult and type(predictions) is EDPredictionBatch,
          "typed_ED_fit_result_and_prediction_batch_required")
    expected_binding = _binding(binding, view)
    if result.status == "unavailable":
        _need(predictions == predict_ed_lightgbm(result, view), "ED_unavailable_prediction_mismatch")
        return {"schema": "ed-h1-model-verification-v1", "verified": True, "status": "unavailable",
                "unavailable_sessions": view.test_sessions, "model_fitted": False}
    payload, np, booster = _check_artifact(result, view)
    _need(numeric._canonical(payload["binding"]) == numeric._canonical(expected_binding),
          "ED_external_scientific_runtime_binding_mismatch")
    rows = tuple(row.features for row in view.testing if row.features is not None)
    native = numeric._predict_lightgbm_matrix(booster, rows, np)
    scalar = numeric._scalar_lightgbm_replay(booster.dump_model(), rows, len(view.feature_names))
    _need(predictions.fold_index == view.fold.index and predictions.scenario == view.scenario
          and predictions.status == "ready" and predictions.reason is None
          and predictions.evaluation_sessions == view.test_sessions and predictions.unavailable_sessions == ()
          and tuple(row.parent_id for row in predictions.rows) == view.test_ids,
          "ED_prediction_population_mismatch")
    supplied, index = [], 0
    for row, prediction in zip(view.testing, predictions.rows, strict=True):
        unseen = tuple(kind for kind in row.event_types if view.training_group_counts[kind] == 0)
        _need(prediction.unseen_types == unseen and prediction.extrapolation == bool(unseen),
              "ED_unseen_fixed_type_flag_mismatch")
        if row.features is None:
            _need(prediction.status == "unavailable" and prediction.reason == "common_input_unknown"
                  and prediction.prediction_hex is None and prediction.accepted is None,
                  "ED_missing_input_became_prediction")
            continue
        value = numeric._unhex(prediction.prediction_hex)
        _need(prediction.status == "known" and prediction.reason is None
              and type(prediction.accepted) is bool and prediction.accepted == (value > 0),
              "ED_strict_positive_admission_mismatch")
        for replay in (native[index], scalar[index]):
            _need((value > 0) == (replay > 0), "ED_strict_positive_replay_sign_mismatch")
            _need(math.isclose(value, replay, abs_tol=numeric.ABS_TOL, rel_tol=numeric.REL_TOL),
                  "ED_serialized_scalar_prediction_replay_mismatch")
        supplied.append(value)
        index += 1
    return {"schema": "ed-h1-model-verification-v1", "verified": True, "status": "ready",
        "artifact_sha256": payload["artifact_sha256"], "prediction_count": len(supplied),
        "train_features_sha256": payload["train_features_sha256"],
        "train_labels_sha256": payload["train_labels_sha256"],
        "test_features_sha256": numeric._numeric_matrix_hash(rows, len(view.feature_names)),
        "predictions_sha256": numeric._vector_hash(tuple(supplied)),
        "native_replay_predictions_sha256": numeric._vector_hash(native),
        "scalar_replay_predictions_sha256": numeric._vector_hash(scalar),
        "strict_positive_signs_agree": True,
        "trusted_external_boundary": "pinned LightGBM training and native serialized dump parser; scalar branch/leaf prediction replay",
        "caller_obligation": "independent raw preprocessing, common support, exact unit labels and fold membership; source/runtime pin authentication"}
