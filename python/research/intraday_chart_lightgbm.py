"""EF fixed LightGBM fits and producer-independent scalar prediction replay."""
from __future__ import annotations

import math

from research import intraday_campaign_ml_models as numeric
from research.intraday_chart_folds import FoldSelection, Key
from research.intraday_chart_model_inputs import ADMISSION_PCT, FitBinding, LGB_ARMS, fit_binding, lightgbm_matrix
from research.intraday_chart_tensors import TABULAR_CHART_NAMES, TABULAR_PEER_NAMES


SCHEMA = "EF-fixed-lightgbm-v1"


def _training(snapshot, split: FoldSelection, binding: FitBinding):
    if not split.supported or (split.scenario, split.fold.index) != (binding.scenario, binding.fold):
        raise ValueError("supported matching EF LightGBM fold required")
    keys = tuple(row.key for row in split.training)
    names, matrix = lightgbm_matrix(snapshot, keys, binding.scenario, binding.arm)
    labels = tuple(numeric._number(row.outcomes[0].value * 100) for row in split.training)
    training = {"rows": len(keys), "key_sha256": numeric._digest(keys),
                "x_sha256": numeric._numeric_matrix_hash(matrix, len(names)), "y_sha256": numeric._vector_hash(labels),
                "cutoff": split.fold.first_test_opening.isoformat()}
    return names, matrix, labels, training


def fit_lightgbm(snapshot, split: FoldSelection, binding: FitBinding) -> dict:
    """Fit only the registered historical rows. No evaluation values enter here."""
    bound = fit_binding(binding, LGB_ARMS)
    names, matrix, labels, training = _training(snapshot, split, binding)
    np, lgb, runtime = numeric._libraries("lightgbm")
    artifact = {"schema": SCHEMA, "binding": bound, "feature_names": list(names), "training": training,
                "runtime": runtime, "parameters": numeric.lightgbm_parameter_manifest(),
                "model": numeric._fit_lightgbm_matrix(matrix, labels, names, np, lgb)}
    return {**artifact, "artifact_sha256": numeric._digest(artifact)}


def _booster(artifact: dict, binding: FitBinding):
    fields = {"schema", "binding", "feature_names", "training", "runtime", "parameters", "model", "artifact_sha256"}
    if type(artifact) is not dict or set(artifact) != fields:
        raise ValueError("EF LightGBM artifact schema differs")
    names = TABULAR_CHART_NAMES + (TABULAR_PEER_NAMES if binding.arm == "LGB_CHART_PEER" else ())
    if (artifact["schema"] != SCHEMA or artifact["binding"] != fit_binding(binding, LGB_ARMS)
            or artifact["feature_names"] != list(names)
            or artifact["artifact_sha256"] != numeric._digest({key: value for key, value in artifact.items()
                                                               if key != "artifact_sha256"})
            or numeric._canonical(artifact["parameters"]) != numeric._canonical(numeric.lightgbm_parameter_manifest())):
        raise ValueError("EF LightGBM artifact binding or checksum differs")
    np, lgb, runtime = numeric._libraries("lightgbm")
    if numeric._canonical(runtime) != numeric._canonical(artifact["runtime"]):
        raise ValueError("EF LightGBM artifact runtime differs")
    return np, numeric._reload_lightgbm_matrix(artifact["model"], lgb, names)


def predict_lightgbm(snapshot, keys: tuple[Key, ...], binding: FitBinding, artifact: dict) -> tuple[float, ...]:
    """Use only the feature file and a bound artifact."""
    np, booster = _booster(artifact, binding)
    _, rows = lightgbm_matrix(snapshot, keys, binding.scenario, binding.arm)
    return numeric._predict_lightgbm_matrix(booster, rows, np)


def verify_lightgbm(snapshot, split: FoldSelection, keys: tuple[Key, ...], binding: FitBinding,
                    artifact: dict, saved_predictions: tuple[float, ...]) -> dict:
    """Recompute the training binding and scalar tree output without fitting."""
    np, booster = _booster(artifact, binding)
    names, _, labels, training = _training(snapshot, split, binding)
    native_targets = tuple(float(value) for value in np.asarray(labels, dtype=np.float32))
    if (artifact["training"] != training
            or artifact["model"]["native_training_labels_sha256"] != numeric._vector_hash(native_targets)):
        raise ValueError("EF LightGBM training manifest differs")
    _, rows = lightgbm_matrix(snapshot, keys, binding.scenario, binding.arm)
    replay = numeric._scalar_lightgbm_replay(booster.dump_model(), rows, len(names))
    if len(replay) != len(saved_predictions) or any(
            (saved > ADMISSION_PCT) != (expected > ADMISSION_PCT)
            or not math.isclose(saved, expected, rel_tol=numeric.REL_TOL, abs_tol=numeric.ABS_TOL)
            for saved, expected in zip(saved_predictions, replay, strict=True)):
        raise ValueError("EF LightGBM scalar replay or admission differs")
    return {"verified": True, "artifact_sha256": artifact["artifact_sha256"], "rows": len(replay),
            "inputs_sha256": numeric._numeric_matrix_hash(rows, len(names)),
            "predictions_sha256": numeric._vector_hash(saved_predictions), "threshold_pct": ADMISSION_PCT}
