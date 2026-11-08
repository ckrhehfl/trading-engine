"""Bounded EI forecasts sealed before evaluation, with no outcome reader.

The caller authenticates the original feature/label pins and test-coordinate
stream. Hashes detect changed bytes; they do not authenticate their origin.
Only the original feature stream supplies typed questions on readback: this
file stores their digests, all frame denominators, probabilities and models,
not another copy of every minute sequence. No fitting or inference occurs.
"""

from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from itertools import zip_longest
from math import isfinite
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_chart_inputs import _need
from research.intraday_context_empirical import (
    CONTEXTS, STAGES, ContextCounts, ContextEmpiricalModel, EmpiricalSupport, StageCounts,
)
from research.intraday_context_forecast_fold import (
    LEARNED_KEYS, MODEL_KEYS, SCHEMA as FOLD_SCHEMA, ArmPrediction, FoldForecast,
    ModelArtifact, QuestionForecast, TrainingSupport, _eligible, _probabilities, _validate_fold,
)
from research.intraday_context_model_inputs import encode, prepare_queries, validate_training_contract
from research.intraday_context_snapshot import (
    FEATURE_SCHEMA, LABEL_SCHEMA, MAX_COORDINATES, ProjectedCoordinate, _coordinate, _fraction,
    _identity, _keys, _list, _read_identity, _read_time, _time, _validate_coordinate,
)
from research.intraday_scenario_forecast_targets import OUTCOMES
from research.intraday_scenario_snapshot import MAX_BYTES, MAX_LINE, SnapshotSeal, _read, _write


SCHEMA = "ei-context-forecast-seal-draft-v1"
MAX_RECORDS = 1 + len(LEARNED_KEYS) + MAX_COORDINATES


@dataclass(frozen=True)
class StoredForecast:
    seal: SnapshotSeal
    feature_seal: SnapshotSeal
    label_seal: SnapshotSeal
    forecast: FoldForecast


def _plain(value):
    if isinstance(value, datetime):
        return _time(value)
    if isinstance(value, Fraction):
        return str(value)
    if type(value) in (list, tuple):
        return [_plain(item) for item in value]
    if type(value) is dict:
        return {key: _plain(item) for key, item in value.items()}
    _need(value is None or type(value) in (bool, int, float, str), "EI_forecast_seal_non_json_metadata")
    return value


def _pin(seal: SnapshotSeal, schema: str) -> dict:
    _need(type(seal) is SnapshotSeal and seal.schema == schema and wire.valid_sha(seal.sha256)
          and type(seal.size) is int and 0 < seal.size <= MAX_BYTES
          and type(seal.records) is int and 0 <= seal.records <= MAX_COORDINATES,
          "EI_forecast_seal_source_pin_differs")
    return asdict(seal)


def _header(feature_seal: SnapshotSeal, label_seal: SnapshotSeal) -> dict:
    return {"schema": SCHEMA, "features": _pin(feature_seal, FEATURE_SCHEMA),
            "labels": _pin(label_seal, LABEL_SCHEMA), "model_keys": list(MODEL_KEYS)}


def _manifest(coordinate: ProjectedCoordinate) -> dict:
    _validate_coordinate(coordinate, coordinate.targets)
    raw = _coordinate(coordinate)
    raw["questions"] = [[item["id"], wire.digest(wire.encode(item))] for item in raw["questions"]]
    return raw


def _coordinates(values: Iterable[ProjectedCoordinate], scenario: str, cutoff: datetime,
                 allowed_dates: tuple[str, ...] | None) -> tuple[ProjectedCoordinate, ...]:
    result, previous = [], None
    for coordinate in values:
        _need(len(result) < MAX_COORDINATES and type(coordinate) is ProjectedCoordinate,
              "EI_forecast_seal_coordinate_bound_or_type")
        key = coordinate.session, coordinate.symbol
        _validate_coordinate(coordinate, coordinate.targets)
        _need((previous is None or previous < key) and any(row.scenario == scenario for row in coordinate.targets)
              and (allowed_dates is None or coordinate.session in allowed_dates)
              and (coordinate.opening is None or coordinate.opening >= cutoff),
              "EI_forecast_seal_original_test_scope_differs")
        previous = key
        result.append(coordinate)
    return tuple(result)


def _support(raw: dict) -> TrainingSupport:
    names = tuple(field.name for field in fields(TrainingSupport))
    _keys(raw, " ".join(names))
    counts = names[:6]
    _need(all(type(raw[name]) is int and raw[name] >= 0 for name in counts),
          "EI_forecast_seal_support_count_differs")
    for name, width in ((names[6], 3), (names[7], 2), (names[8], 4)):
        _list(raw[name])
        for row in raw[name]:
            _list(row, width)
            _need(type(row[-1]) is int and row[-1] > 0, "EI_forecast_seal_support_cell_differs")
        _need(raw[name] == sorted(raw[name]) and len({tuple(row[:-1]) for row in raw[name]}) == len(raw[name]),
              "EI_forecast_seal_support_order_differs")
    result = TrainingSupport(*(raw[name] for name in counts),
        *(tuple(tuple(row) for row in raw[name]) for name in names[6:]))
    _need(result.original_coordinates <= MAX_COORDINATES
          and result.original_questions >= result.eligible_feature_questions >= result.supported_joint_questions
          >= result.symbol_sessions >= result.distinct_dates
          and sum(row[-1] for row in result.supported_by_stage_context) == result.supported_joint_questions
          and sum(row[-1] for row in result.unavailable_by_reason)
              == result.original_questions - result.supported_joint_questions
          and all(stage in STAGES and context in CONTEXTS for stage, context, _ in result.supported_by_stage_context)
          and all(type(reason) is str and bool(reason) for reason, _ in result.unavailable_by_reason)
          and all(stage in STAGES and horizon in (5, 15, 30) and type(status) is str and bool(status)
                  for stage, horizon, status, _ in result.label_support_by_stage_horizon)
          and all(sum(count for _, minute, _, count in result.label_support_by_stage_horizon if minute == horizon)
                  == result.original_questions for horizon in (5, 15, 30)),
          "EI_forecast_seal_training_support_differs")
    return result


def _empirical_support(raw: dict) -> EmpiricalSupport:
    _keys(raw, "candidate_rows supported_rows unavailable_by_reason symbol_days dates weighted_count")
    _list(raw["unavailable_by_reason"])
    for row in raw["unavailable_by_reason"]:
        _list(row, 2)
        _need(type(row[0]) is str and bool(row[0]) and type(row[1]) is int and row[1] > 0,
              "EI_forecast_seal_empirical_missing_differs")
    missing = tuple(tuple(row) for row in raw["unavailable_by_reason"])
    result = EmpiricalSupport(raw["candidate_rows"], raw["supported_rows"], missing,
                              raw["symbol_days"], raw["dates"], _fraction(raw["weighted_count"]))
    _need(all(type(value) is int and value >= 0 for value in
              (result.candidate_rows, result.supported_rows, result.symbol_days, result.dates))
          and result.candidate_rows == result.supported_rows + sum(count for _, count in missing)
          and 0 <= result.dates <= result.symbol_days <= result.supported_rows
          and 0 <= result.weighted_count <= result.supported_rows
          and missing == tuple(sorted(missing)) and len({reason for reason, _ in missing}) == len(missing),
          "EI_forecast_seal_empirical_support_differs")
    return result


def _counts(raw: list) -> tuple[Fraction, ...]:
    _list(raw, len(OUTCOMES))
    values = tuple(map(_fraction, raw))
    _need(all(value >= 0 for value in values), "EI_forecast_seal_negative_empirical_count")
    return values


def _empirical(raw: dict | None, support: TrainingSupport, cutoff: datetime) -> ContextEmpiricalModel | None:
    if not support.supported_joint_questions:
        _need(raw is None, "EI_forecast_seal_empirical_without_training")
        return None
    _keys(raw, "cutoff_at stages input_sha256")
    _need(_read_time(raw["cutoff_at"]) == cutoff and wire.valid_sha(raw["input_sha256"]),
          "EI_forecast_seal_empirical_identity_differs")
    _list(raw["stages"], len(STAGES))
    stages = []
    for stage_name, stage in zip(STAGES, raw["stages"], strict=True):
        _keys(stage, "stage counts support contexts candidate_roster_sha256 supported_roster_sha256")
        _need(stage["stage"] == stage_name and all(wire.valid_sha(stage[name]) for name in
              ("candidate_roster_sha256", "supported_roster_sha256")), "EI_forecast_seal_empirical_stage_differs")
        _list(stage["contexts"], len(CONTEXTS))
        contexts = []
        for name, context in zip(CONTEXTS, stage["contexts"], strict=True):
            _keys(context, "context counts support")
            count, cell = _counts(context["counts"]), _empirical_support(context["support"])
            _need(context["context"] == name and sum(count) == cell.weighted_count,
                  "EI_forecast_seal_empirical_context_differs")
            contexts.append(ContextCounts(name, count, cell))
        count, cell = _counts(stage["counts"]), _empirical_support(stage["support"])
        _need(sum(count) == cell.weighted_count
              and tuple(sum(item.counts[i] for item in contexts) for i in range(len(OUTCOMES))) == count
              and sum(item.support.candidate_rows for item in contexts) == cell.candidate_rows
              and sum(item.support.supported_rows for item in contexts) == cell.supported_rows,
              "EI_forecast_seal_empirical_stage_counts_differ")
        stages.append(StageCounts(stage_name, count, cell, tuple(contexts),
                                 stage["candidate_roster_sha256"], stage["supported_roster_sha256"]))
    _need(sum(item.support.candidate_rows for item in stages) == support.eligible_feature_questions
          and sum(item.support.supported_rows for item in stages) == support.supported_joint_questions
          and tuple((stage.stage, context.context, context.support.supported_rows) for stage in stages
                    for context in stage.contexts if context.support.supported_rows)
              == support.supported_by_stage_context, "EI_forecast_seal_empirical_joint_support_differs")
    return ContextEmpiricalModel(cutoff, tuple(stages), raw["input_sha256"])


def _metadata(forecast: FoldForecast) -> dict:
    return {"kind": "fold", "schema": forecast.schema, "scenario": forecast.scenario, "index": forecast.index,
        "first_test_opening": _time(forecast.first_test_opening), "adapter_mode": forecast.adapter_mode,
        "timing_scenarios_are_independent": forecast.timing_scenarios_are_independent,
        "training_support": _plain(asdict(forecast.training_support)),
        "empirical": None if forecast.empirical is None else _plain(asdict(forecast.empirical)),
        "learned_fit_count": forecast.learned_fit_count, "empirical_fit_count": forecast.empirical_fit_count}


def _model(model: ModelArtifact) -> dict:
    raw = {field.name: getattr(model, field.name) for field in fields(ModelArtifact)}
    proof = raw["independent_verification"]
    if proof is not None:
        raw["independent_verification"] = {**proof, "query_ids": [_identity(row) for row in proof["query_ids"]]}
    return {"kind": "model", **raw}


def _read_model(raw: dict) -> ModelArtifact:
    names = tuple(field.name for field in fields(ModelArtifact))
    _keys(raw, "kind " + " ".join(names))
    _need(raw["kind"] == "model", "EI_forecast_seal_model_record_required")
    proof = raw["independent_verification"]
    if proof is not None:
        _need(type(proof) is dict and "query_ids" in proof, "EI_forecast_seal_proof_required")
        _list(proof["query_ids"])
        proof = {**proof, "query_ids": tuple(map(_read_identity, proof["query_ids"]))}
    return ModelArtifact(*(proof if name == "independent_verification" else raw[name] for name in names))


def _arm(raw: list) -> ArmPrediction:
    _list(raw, 4)
    key, status, probabilities, reason = raw
    if status == "predicted":
        _list(probabilities, len(OUTCOMES))
        _need(reason is None, "EI_forecast_seal_predicted_reason_differs")
        probabilities = _probabilities(tuple(probabilities))
    else:
        _need(status == "unsupported" and probabilities is None and type(reason) is str and bool(reason),
              "EI_forecast_seal_unsupported_arm_differs")
    return ArmPrediction(key, status, probabilities, reason)


def _prediction_record(coordinate: ProjectedCoordinate, predictions: tuple[QuestionForecast, ...]) -> dict:
    _need(type(predictions) is tuple and len(predictions) == len(coordinate.questions),
          "EI_forecast_seal_question_count_differs")
    rows = []
    for feature, prediction in zip(coordinate.questions, predictions, strict=True):
        _need(type(prediction) is QuestionForecast and (prediction.session, prediction.symbol, prediction.question)
              == (coordinate.session, coordinate.symbol, feature), "EI_forecast_seal_question_identity_differs")
        _need(type(prediction.predictions) is tuple and all(type(arm) is ArmPrediction for arm in prediction.predictions)
              and tuple(arm.key for arm in prediction.predictions) == MODEL_KEYS,
              "EI_forecast_seal_fixed_arms_required")
        rows.append([prediction.question_id,
                     [[arm.key, arm.status, arm.probabilities, arm.reason] for arm in prediction.predictions]])
    return {"kind": "coordinate", "original": _manifest(coordinate), "predictions": rows}


def _validate_models(forecast: FoldForecast) -> None:
    supported_stages = {stage for stage, _, _ in forecast.training_support.supported_by_stage_context}
    features = []
    for row in forecast.predictions:
        reason = _eligible(row.question)
        if reason is None and row.question.question.stage not in supported_stages:
            reason = "no_mature_supported_training_for_stage"
        _need(all(arm.status == ("predicted" if reason is None else "unsupported") and arm.reason == reason
                  for arm in row.predictions), "EI_forecast_seal_common_question_support_differs")
        if reason is None:
            features.append(row.question)
            baseline = forecast.empirical.predict(stage=row.question.question.stage,
                origin_context=row.question.question.episode_origin_context, forecast_at=row.question.forecast_at)
            _need(row.predictions[0].probabilities == tuple(map(float, baseline.pooled))
                  and row.predictions[1].probabilities == tuple(map(float, baseline.conditioned)),
                  "EI_forecast_seal_empirical_probabilities_differ")
    queries = {mode: prepare_queries((row.numeric_view(with_context=context) for row in features),
        with_context=context, cutoff_at=forecast.first_test_opening, fold=forecast.index)
        for mode, context in (("chart", False), ("chart_context", True))}
    _need(tuple(model.key for model in forecast.models) == LEARNED_KEYS, "EI_forecast_seal_fixed_models_required")
    contracts, fits = [], 0
    for model in forecast.models:
        validate_training_contract(model.training)
        mode = "chart_context" if model.key.startswith("context_") else "chart"
        query = queries[mode]
        contract = model.training
        _need(contract["mode"] == mode and contract["fold"] == forecast.index
              and contract["cutoff_at"] == _time(forecast.first_test_opening)
              and contract["supported_rows"] == forecast.training_support.supported_joint_questions
              and contract["candidate_rows"] == forecast.training_support.eligible_feature_questions
              and contract["event_count"] == forecast.training_support.symbol_sessions
              and contract["date_count"] == forecast.training_support.distinct_dates
              and model.query_input_sha256 == query.input_sha256, "EI_forecast_seal_model_training_or_query_differs")
        contracts.append(contract)
        reason = ("no_mature_supported_training_rows" if not contract["supported_rows"] else
                  "no_supported_test_questions" if not query.rows else None)
        if reason is not None:
            _need(model.status == "unsupported" and model.reason == reason and model.artifact is None
                  and model.artifact_bytes_sha256 is None and model.independent_verification is None,
                  "EI_forecast_seal_unsupported_model_differs")
        else:
            fits += 1
            _need(model.status == "fitted" and model.reason is None and type(model.artifact) is dict
                  and encode(model.artifact.get("training")) == encode(contract)
                  and model.artifact_bytes_sha256 == sha256(encode(model.artifact)).hexdigest(),
                  "EI_forecast_seal_artifact_digest_or_contract_differs")
            if forecast.adapter_mode == "fixed_native":
                if model.key.endswith("sequence"):
                    from research.intraday_context_sequence import _validate_artifact
                    _validate_artifact(model.artifact, query)
                else:
                    from research.intraday_context_lightgbm import _validate_artifact
                    _validate_artifact(model.artifact)
            proof = model.independent_verification
            if model.key.endswith("sequence"):
                required = {"verified", "rows", "artifact_sha256", "query_input_sha256", "query_ids"}
                _need(type(proof) is dict and set(proof) in (required, required | {"maximum_absolute_difference"})
                      and proof["verified"] is True and type(proof["rows"]) is int and proof["rows"] == len(query.rows)
                      and proof["artifact_sha256"] == model.artifact.get("artifact_sha256")
                      and proof["query_input_sha256"] == query.input_sha256
                      and proof["query_ids"] == tuple(row.question_id for row in query.rows),
                      "EI_forecast_seal_sequence_proof_differs")
                error = proof.get("maximum_absolute_difference")
                _need((forecast.adapter_mode == "injected_diagnostic" and error is None)
                      or type(error) in (float, int) and isfinite(error) and 0 <= error <= 1e-9,
                      "EI_forecast_seal_sequence_error_differs")
            else:
                _need(proof is None, "EI_forecast_seal_unexpected_tree_proof")
        pair = {"model": model.key, "training_input_sha256": contract["input_sha256"],
                "query_input_sha256": query.input_sha256, "artifact_bytes_sha256": model.artifact_bytes_sha256,
                "unsupported_reason": reason}
        _need(model.pair_sha256 == sha256(encode(pair)).hexdigest(), "EI_forecast_seal_model_pair_digest_differs")
    for key in ("supported_rows", "candidate_rows", "unavailable_by_reason", "event_count", "date_count",
                "weighted_stage_groups", "roster_sha256", "weights_sha256", "outcomes_sha256"):
        _need(all(contract[key] == contracts[0][key] for contract in contracts),
              "EI_forecast_seal_common_training_roster_differs")
    _need(contracts[0] == contracts[2] and contracts[1] == contracts[3],
          "EI_forecast_seal_same_mode_training_inputs_differ")
    _need(forecast.learned_fit_count == fits and forecast.empirical_fit_count == int(forecast.empirical is not None),
          "EI_forecast_seal_fit_count_differs")


def _read_checked(path: Path, seal: SnapshotSeal, feature_seal: SnapshotSeal, label_seal: SnapshotSeal, *,
                  coordinates: tuple[ProjectedCoordinate, ...], index: int, cutoff: datetime,
                  scenario: str, adapter_mode: str) -> StoredForecast:
    stream = _read(path, seal, _header(feature_seal, label_seal), maximum=MAX_RECORDS)
    first = next(stream, None)
    _need(first is not None, "EI_forecast_seal_fold_metadata_missing")
    metadata = first[0]
    _keys(metadata, "kind schema scenario index first_test_opening adapter_mode timing_scenarios_are_independent "
          "training_support empirical learned_fit_count empirical_fit_count")
    _need(metadata["kind"] == "fold" and metadata["schema"] == FOLD_SCHEMA
          and metadata["scenario"] == scenario and type(metadata["index"]) is int and metadata["index"] == index
          and _read_time(metadata["first_test_opening"]) == cutoff and metadata["adapter_mode"] == adapter_mode
          and metadata["timing_scenarios_are_independent"] is False
          and all(type(metadata[name]) is int and metadata[name] >= 0
                  for name in ("learned_fit_count", "empirical_fit_count")), "EI_forecast_seal_fold_identity_differs")
    support = _support(metadata["training_support"])
    empirical = _empirical(metadata["empirical"], support, cutoff)
    models = []
    for _ in LEARNED_KEYS:
        received = next(stream, None)
        _need(received is not None, "EI_forecast_seal_model_missing")
        models.append(_read_model(received[0]))
    predictions = []
    for coordinate, received in zip_longest(coordinates, stream):
        _need(coordinate is not None and received is not None, "EI_forecast_seal_coordinate_omitted_or_extra")
        raw, _ = received
        _keys(raw, "kind original predictions")
        _need(raw["kind"] == "coordinate" and wire.encode(raw["original"]) == wire.encode(_manifest(coordinate)),
              "EI_forecast_seal_original_feature_or_denominator_differs")
        _list(raw["predictions"], len(coordinate.questions))
        for feature, value in zip(coordinate.questions, raw["predictions"], strict=True):
            _list(value, 2)
            _list(value[1], len(MODEL_KEYS))
            arms = tuple(map(_arm, value[1]))
            row = QuestionForecast(coordinate.session, coordinate.symbol, feature, arms)
            _need(row.question_id == value[0] and tuple(arm.key for arm in arms) == MODEL_KEYS,
                  "EI_forecast_seal_prediction_identity_or_arm_order_differs")
            predictions.append(row)
    # zip_longest fully consumes the generic reader, including its footer/EOF
    # and descriptor identity checks, before any usable forecast is returned.
    forecast = FoldForecast(FOLD_SCHEMA, scenario, index, cutoff, adapter_mode, False, coordinates, support,
        empirical, tuple(models), tuple(predictions), metadata["learned_fit_count"], metadata["empirical_fit_count"])
    _validate_models(forecast)
    return StoredForecast(seal, feature_seal, label_seal, forecast)


def read_forecasts(path: Path, seal: SnapshotSeal, *, feature_seal: SnapshotSeal, label_seal: SnapshotSeal,
                   expected_coordinates: Iterable[ProjectedCoordinate], expected_fold: ChronologicalFold,
                   expected_scenario: str, expected_adapter_mode: str = "fixed_native") -> StoredForecast:
    """Return typed forecasts only after exact original binding and full EOF.

    The supplied coordinate iterator must be the caller-authenticated original
    test roster for this fold/scenario; hashes here are not source signatures.
    Its exhaustion also lets a sealed feature reader finish its own EOF check.
    """
    _validate_fold(expected_fold)
    _need(expected_scenario in ("S1", "S2")
          and expected_adapter_mode in ("fixed_native", "injected_diagnostic"), "EI_forecast_seal_expected_mode_differs")
    coordinates = _coordinates(expected_coordinates, expected_scenario, expected_fold.first_test_opening,
                               expected_fold.test_sessions)
    return _read_checked(path, seal, feature_seal, label_seal, coordinates=coordinates, index=expected_fold.index,
        cutoff=expected_fold.first_test_opening, scenario=expected_scenario, adapter_mode=expected_adapter_mode)


def seal_forecasts(path: Path, forecast: FoldForecast, feature_seal: SnapshotSeal,
                   label_seal: SnapshotSeal) -> SnapshotSeal:
    """Exclusively create, freeze and fully read back one fixed fold's forecasts."""
    _need(type(forecast) is FoldForecast and forecast.schema == FOLD_SCHEMA and forecast.scenario in ("S1", "S2")
          and type(forecast.index) is int and 1 <= forecast.index <= 5
          and forecast.adapter_mode in ("fixed_native", "injected_diagnostic")
          and type(forecast.predictions) is tuple and type(forecast.models) is tuple,
          "EI_forecast_seal_typed_fold_required")
    coordinates = _coordinates(forecast.original_test_coordinates, forecast.scenario, forecast.first_test_opening, None)
    _need(len(forecast.models) == len(LEARNED_KEYS)
          and all(type(model) is ModelArtifact for model in forecast.models)
          and len(forecast.predictions) == sum(len(row.questions) for row in coordinates),
          "EI_forecast_seal_fixed_roster_required")

    def rows():
        yield _metadata(forecast)
        yield from map(_model, forecast.models)
        offset = 0
        for coordinate in coordinates:
            stop = offset + len(coordinate.questions)
            yield _prediction_record(coordinate, forecast.predictions[offset:stop])
            offset = stop

    seal = _write(path, _header(feature_seal, label_seal), rows(), maximum=MAX_RECORDS)
    _read_checked(path, seal, feature_seal, label_seal, coordinates=coordinates, index=forecast.index,
        cutoff=forecast.first_test_opening, scenario=forecast.scenario, adapter_mode=forecast.adapter_mode)
    return seal
