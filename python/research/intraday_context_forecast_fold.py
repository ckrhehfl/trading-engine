"""Fixed EI chronological probability comparison; evaluation labels are absent.

The caller supplies an authenticated sealed feature stream and an original
dated roster. Only labels of pre-split training questions may be requested.
Diagnostic adapters are explicit in the output and do not authorize an actual
run. Event probabilities are neither fills nor portfolio performance.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from itertools import zip_longest
from math import fsum, isfinite

from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_empirical import ContextEmpiricalModel, EmpiricalTrainingRow, fit_empirical
from research.intraday_context_features import ContextFeatures
from research.intraday_context_model_inputs import (
    ModelTrainingRow, PreparedQueries, PreparedTraining, encode, prepare_queries, prepare_training, training_contract,
)
from research.intraday_context_snapshot import (
    LabeledCoordinate, ProjectedCoordinate, QuestionLabel, _roster, _validate_coordinate, _validate_labels,
    question_digest,
)
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome, training_outcome


SCHEMA = "ei-context-forecast-fold-draft-v1"
MODEL_KEYS = ("pooled_empirical", "context_empirical", "chart_lightgbm", "context_lightgbm",
              "chart_sequence", "context_sequence")
LEARNED_KEYS = MODEL_KEYS[2:]
_KST = timezone(timedelta(hours=9))
TrainingLabels = Callable[[str, str, str], QuestionLabel | None]


@dataclass(frozen=True)
class ForecastAdapters:
    """Explicit invented-test injection; defaults always use fixed native models."""
    fit_lightgbm: Callable[[PreparedTraining], dict | None]
    predict_lightgbm: Callable[[dict, PreparedQueries], tuple[tuple[float, ...], ...]]
    fit_sequence: Callable[[PreparedTraining], dict]
    predict_sequence: Callable[[dict, PreparedQueries], tuple[tuple[float, ...], ...]]
    verify_sequence: Callable[[dict, PreparedQueries, tuple], dict]


@dataclass(frozen=True)
class ArmPrediction:
    key: str
    status: str
    probabilities: tuple[float, ...] | None
    reason: str | None


@dataclass(frozen=True)
class QuestionForecast:
    session: str
    symbol: str
    question: ContextFeatures
    predictions: tuple[ArmPrediction, ...]

    @property
    def question_id(self) -> str:
        return question_digest(self.question.question)


@dataclass(frozen=True)
class ModelArtifact:
    key: str
    status: str
    reason: str | None
    artifact: dict | None
    training: dict
    query_input_sha256: str
    independent_verification: dict | None
    artifact_bytes_sha256: str | None
    pair_sha256: str


@dataclass(frozen=True)
class TrainingSupport:
    original_coordinates: int
    original_questions: int
    eligible_feature_questions: int
    supported_joint_questions: int
    symbol_sessions: int
    distinct_dates: int
    supported_by_stage_context: tuple[tuple[str, str, int], ...]
    unavailable_by_reason: tuple[tuple[str, int], ...]
    label_support_by_stage_horizon: tuple[tuple[str, int, str, int], ...]


@dataclass(frozen=True)
class FoldForecast:
    schema: str
    scenario: str
    index: int
    first_test_opening: datetime
    adapter_mode: str
    timing_scenarios_are_independent: bool
    original_test_coordinates: tuple[ProjectedCoordinate, ...]
    training_support: TrainingSupport
    empirical: ContextEmpiricalModel | None
    models: tuple[ModelArtifact, ...]
    predictions: tuple[QuestionForecast, ...]
    learned_fit_count: int
    empirical_fit_count: int


def _default_adapters() -> ForecastAdapters:
    from research.intraday_context_lightgbm import fit_lightgbm, predict_lightgbm
    from research.intraday_context_sequence import fit_sequence, predict_sequence
    from research.intraday_context_sequence_reference import verify_sequence_probabilities

    return ForecastAdapters(fit_lightgbm, predict_lightgbm, fit_sequence, predict_sequence,
                            verify_sequence_probabilities)


def _validate_fold(fold: ChronologicalFold) -> None:
    _need(type(fold) is ChronologicalFold and type(fold.index) is int and 1 <= fold.index <= 5,
          "EI_forecast_fixed_fold_required")
    _clock(fold.first_test_opening)
    for sessions, source_slice in ((fold.training_sessions, fold.training_source_slice),
                                   (fold.test_sessions, fold.test_source_slice)):
        _need(type(sessions) is tuple and bool(sessions) and tuple(sorted(set(sessions))) == sessions
              and all(type(day) is str and date.fromisoformat(day).isoformat() == day for day in sessions)
              and type(source_slice) is tuple and len(source_slice) == 2
              and all(type(index) is int and index >= 0 for index in source_slice)
              and source_slice[1] - source_slice[0] == len(sessions), "EI_forecast_fold_dates_or_slice_differ")
    _need(fold.training_sessions[-1] < fold.test_sessions[0]
          and fold.training_source_slice[1] <= fold.test_source_slice[0]
          and fold.first_test_opening.astimezone(_KST).date().isoformat() == fold.test_sessions[0],
          "EI_forecast_fold_clock_differs")


def _eligible(feature: ContextFeatures) -> str | None:
    if feature.status != "ready":
        return "feature_unsupported:" + (feature.reasons[0] if feature.reasons else "unspecified")
    question = feature.question
    path = question.path
    if "unsupported_price_geometry" in question.unsupported_reasons or not (
            0 < path.stop <= path.level - path.band < path.level + path.band < path.target
            and path.stop < question.start_close < path.target):
        return "unsupported_price_geometry"
    # A 30-minute outside-session label does not exclude supported 5/15-minute
    # queries. Evaluation support is determined later, separately by horizon.
    return None


def _training_label(coordinate: ProjectedCoordinate, feature: ContextFeatures, cutoff: datetime,
                    reader: TrainingLabels) -> tuple[TrainingOutcome | None, str | None, tuple[tuple[int, str], ...]]:
    _need(feature.forecast_at < cutoff, "EI_forecast_training_question_after_split")
    label = reader(coordinate.session, coordinate.symbol, question_digest(feature.question))
    if label is None:
        return None, "training_label_missing", tuple((horizon, "label_missing") for horizon in (5, 15, 30))
    _need(type(label) is QuestionLabel and label.question == feature.question,
          "EI_forecast_training_label_identity_differs")
    selected = replace(coordinate, questions=(feature,))
    _validate_labels(LabeledCoordinate(coordinate.session, coordinate.symbol, label.status, (label,)), selected)
    _need(label.cutoff_at <= cutoff, "EI_forecast_training_label_cutoff_after_split")
    if not label.results:
        return None, "training_session_missing", tuple((horizon, "session_missing") for horizon in (5, 15, 30))
    support = tuple((horizon, result.status) for horizon, result in zip((5, 15, 30), label.results, strict=True))
    first_unavailable = next(((horizon, result) for horizon, result in zip((5, 15, 30), label.results, strict=True)
                              if result.status != "resolved"), None)
    outcome = training_outcome(label.results, before=cutoff)
    if first_unavailable is not None:
        horizon, result = first_unavailable
        return None, f"joint_horizon_{horizon}_{result.status}:{result.reason}", support
    if outcome is None:
        return None, "outcome_not_mature_before_split", support
    _need(outcome.mature_at < cutoff, "EI_forecast_training_outcome_after_split")
    return outcome, None, support


def _probabilities(values: tuple) -> tuple[float, ...]:
    _need(type(values) is tuple and len(values) == len(OUTCOMES)
          and all(type(value) in (float, int) and isfinite(value) and 0 <= value <= 1 for value in values)
          and abs(fsum(values) - 1) <= 1e-9, "EI_forecast_probability_mass_differs")
    return tuple(map(float, values))


def _common(chart: PreparedTraining, context: PreparedTraining) -> None:
    _need(chart.mode == "chart" and context.mode == "chart_context"
          and tuple((row.question_id, row.forecast_at) for row in chart.rows)
          == tuple((row.question_id, row.forecast_at) for row in context.rows)
          and all(getattr(chart, name) == getattr(context, name) for name in (
              "outcomes", "outcome_mature_at", "exact_weights", "weights", "candidate_rows", "unavailable_by_reason",
              "event_count", "date_count", "roster_sha256", "weights_sha256", "outcomes_sha256")),
          "EI_forecast_models_do_not_share_training_roster_and_weights")


def _pair_digest(key: str, prepared: PreparedTraining, query: PreparedQueries, artifact_sha: str | None,
                 reason: str | None) -> str:
    return sha256(encode({"model": key, "training_input_sha256": prepared.input_sha256,
        "query_input_sha256": query.input_sha256, "artifact_bytes_sha256": artifact_sha,
        "unsupported_reason": reason})).hexdigest()


def forecast_fold(coordinates: Iterable[ProjectedCoordinate], fold: ChronologicalFold, scenario: str,
                  read_training_label: TrainingLabels, *, expected_targets: tuple[TargetCoordinate, ...],
                  adapters: ForecastAdapters | None = None) -> FoldForecast:
    """Fit six fixed arms on one mature training roster without evaluation labels.

    The complete original stream is consumed before any training-label request.
    Source dates outside the fold remain validated but cannot train/query a model.
    A stage without training support is explicitly unsupported in all six arms;
    a model never acquires extra test cases merely because another arm abstains.
    """
    _validate_fold(fold)
    _need(scenario in ("S1", "S2") and callable(read_training_label), "EI_forecast_scenario_or_reader_differs")
    _need(adapters is None or type(adapters) is ForecastAdapters, "EI_forecast_typed_adapters_required")
    groups = _roster(expected_targets)
    days = {day for day, _ in groups}
    past, test = set(fold.training_sessions), set(fold.test_sessions)
    _need(past | test <= days, "EI_forecast_fold_outside_original_dates")
    training_coordinates, test_coordinates = [], []
    for expected, coordinate in zip_longest(groups.items(), coordinates):
        _need(expected is not None and coordinate is not None, "EI_forecast_original_stream_omitted_or_extra")
        key, targets = expected
        _validate_coordinate(coordinate, targets)
        _need((coordinate.session, coordinate.symbol) == key, "EI_forecast_original_stream_order_differs")
        if scenario not in {row.scenario for row in coordinate.targets}:
            continue
        if coordinate.session in past:
            _need(coordinate.closing is None or coordinate.closing < fold.first_test_opening,
                  "EI_forecast_training_session_overlaps_test")
            training_coordinates.append(coordinate)
        elif coordinate.session in test:
            _need(coordinate.opening is None or coordinate.opening >= fold.first_test_opening,
                  "EI_forecast_test_session_before_split")
            test_coordinates.append(coordinate)

    empirical_rows, chart_rows, context_rows = [], [], []
    exclusions, horizon_support, stage_context = Counter(), Counter(), Counter()
    original_questions = sum(len(row.questions) for row in training_coordinates)
    for coordinate in training_coordinates:
        for feature in coordinate.questions:
            reason = _eligible(feature)
            if reason is not None:
                exclusions[reason] += 1
                for horizon in (5, 15, 30):
                    horizon_support[(feature.question.stage, horizon, "not_requested_feature_unsupported")] += 1
                continue
            outcome, reason, support = _training_label(coordinate, feature, fold.first_test_opening, read_training_label)
            for horizon, status in support:
                horizon_support[(feature.question.stage, horizon, status)] += 1
            question = feature.question
            empirical = EmpiricalTrainingRow(question.question_id, coordinate.symbol, coordinate.session,
                question.stage, question.episode_origin_context, feature.forecast_at, outcome, reason)
            empirical_rows.append(empirical)
            chart_rows.append(ModelTrainingRow(empirical, feature.numeric_view(with_context=False)))
            context_rows.append(ModelTrainingRow(empirical, feature.numeric_view(with_context=True)))
            if reason:
                exclusions[reason] += 1
            else:
                stage_context[(question.stage, question.episode_origin_context)] += 1
    chart = prepare_training(chart_rows, with_context=False, cutoff_at=fold.first_test_opening, fold=fold.index)
    context = prepare_training(context_rows, with_context=True, cutoff_at=fold.first_test_opening, fold=fold.index)
    _common(chart, context)
    supported_stages = {row.stage for row in empirical_rows if row.outcome is not None}
    queries, query_reasons = [], {}
    all_queries = [(coordinate, feature) for coordinate in test_coordinates for feature in coordinate.questions]
    for coordinate, feature in all_queries:
        reason = _eligible(feature)
        if reason is None and feature.question.stage not in supported_stages:
            reason = "no_mature_supported_training_for_stage"
        query_reasons[feature.question_id] = reason
        if reason is None:
            queries.append(feature)
    prepared_queries = {
        mode: prepare_queries((row.numeric_view(with_context=with_context) for row in queries),
            with_context=with_context, cutoff_at=fold.first_test_opening, fold=fold.index)
        for mode, with_context in (("chart", False), ("chart_context", True))}
    _need(tuple(row.question_id for row in prepared_queries["chart"].rows)
          == tuple(row.question_id for row in prepared_queries["chart_context"].rows),
          "EI_forecast_common_query_roster_differs")
    baseline = fit_empirical(empirical_rows, cutoff_at=fold.first_test_opening) if chart.rows else None
    native = adapters is None
    adapters = adapters or _default_adapters()
    _need(all(callable(value) for value in (adapters.fit_lightgbm, adapters.predict_lightgbm,
          adapters.fit_sequence, adapters.predict_sequence, adapters.verify_sequence)), "EI_forecast_adapters_not_callable")
    models, learned_predictions = [], {}
    fits = 0
    for key in LEARNED_KEYS:
        mode = "chart_context" if key.startswith("context_") else "chart"
        prepared = context if mode == "chart_context" else chart
        query = prepared_queries[mode]
        reason = ("no_mature_supported_training_rows" if not prepared.rows else
                  "no_supported_test_questions" if not query.rows else None)
        if reason is not None:
            models.append(ModelArtifact(key, "unsupported", reason, None, training_contract(prepared),
                                        query.input_sha256, None, None, _pair_digest(key, prepared, query, None, reason)))
            learned_predictions[key] = {}
            continue
        sequence = key.endswith("sequence")
        artifact = (adapters.fit_sequence if sequence else adapters.fit_lightgbm)(prepared)
        fits += 1
        _need(type(artifact) is dict and encode(artifact.get("training")) == encode(training_contract(prepared)),
              "EI_forecast_fitted_artifact_training_differs")
        artifact_sha = sha256(encode(artifact)).hexdigest()
        probabilities = (adapters.predict_sequence if sequence else adapters.predict_lightgbm)(artifact, query)
        _need(type(probabilities) is tuple and len(probabilities) == len(query.rows),
              "EI_forecast_prediction_roster_length_differs")
        probabilities = tuple(_probabilities(row) for row in probabilities)
        proof = None
        if sequence:
            pairs = tuple((row.question_id, values) for row, values in zip(query.rows, probabilities, strict=True))
            proof = adapters.verify_sequence(artifact, query, pairs)
            _need(type(proof) is dict and proof.get("verified") is True and type(proof.get("rows")) is int
                  and proof["rows"] == len(query.rows) and proof.get("query_input_sha256") == query.input_sha256
                  and proof.get("artifact_sha256") == artifact.get("artifact_sha256")
                  and proof.get("query_ids") == tuple(row.question_id for row in query.rows),
                  "EI_forecast_sequence_independent_verification_differs")
        _need(sha256(encode(artifact)).hexdigest() == artifact_sha, "EI_forecast_model_changed_during_inference")
        models.append(ModelArtifact(key, "fitted", None, artifact, training_contract(prepared), query.input_sha256, proof,
                                    artifact_sha, _pair_digest(key, prepared, query, artifact_sha, None)))
        learned_predictions[key] = {row.question_id: value for row, value in zip(query.rows, probabilities, strict=True)}
    forecasts = []
    for coordinate, feature in all_queries:
        reason = query_reasons[feature.question_id]
        if reason is not None:
            predictions = tuple(ArmPrediction(key, "unsupported", None, reason) for key in MODEL_KEYS)
        else:
            predicted = baseline.predict(stage=feature.question.stage,
                origin_context=feature.question.episode_origin_context, forecast_at=feature.forecast_at)
            _need(predicted.status == "supported", "EI_forecast_empirical_common_support_differs")
            values = (_probabilities(tuple(map(float, predicted.pooled))),
                      _probabilities(tuple(map(float, predicted.conditioned))),
                      *(learned_predictions[key][feature.question_id] for key in LEARNED_KEYS))
            predictions = tuple(ArmPrediction(key, "predicted", value, None)
                                for key, value in zip(MODEL_KEYS, values, strict=True))
        forecasts.append(QuestionForecast(coordinate.session, coordinate.symbol, feature, predictions))
    support = TrainingSupport(len(training_coordinates), original_questions, len(empirical_rows), len(chart.rows),
        chart.event_count, chart.date_count, tuple((*key, count) for key, count in sorted(stage_context.items())),
        tuple(sorted(exclusions.items())), tuple((*key, count) for key, count in sorted(horizon_support.items())))
    return FoldForecast(SCHEMA, scenario, fold.index, fold.first_test_opening,
        "fixed_native" if native else "injected_diagnostic", False, tuple(test_coordinates), support, baseline,
        tuple(models), tuple(forecasts), fits, int(baseline is not None))
