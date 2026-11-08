"""Fixed prospective EI ten-class LightGBM, with independent saved-tree replay.

The caller owns authenticated input scope, chronological folds and runtime
budgets. Native libraries load only on a fit/predict request; this component
does not install or activate an environment, read data, choose hyperparameters,
look at evaluation outcomes or authorize an actual research run.
"""

from hashlib import sha256
import json
from math import fsum, isclose, isfinite

from research.intraday_chart_inputs import _need
from research.intraday_context_model_inputs import (
    PreparedQueries, PreparedTraining, encode, training_contract, validate_queries,
    validate_training, validate_training_contract,
)
from research.intraday_context_tree_reference import replay_multiclass


SCHEMA = "ei-context-lightgbm-draft-v1"
VERSIONS = {"numpy": "2.5.2", "scipy": "1.18.1", "lightgbm": "4.6.0"}
ROUNDS = 40
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
PARAMETERS = {
    "objective": "multiclass", "num_class": 10, "boosting": "gbdt", "metric": "None",
    "num_leaves": 7, "max_depth": 3, "min_data_in_leaf": 20, "learning_rate": 0.05,
    "num_threads": 1, "deterministic": True, "force_col_wise": True,
    "bagging_fraction": 1.0, "bagging_freq": 0, "feature_fraction": 1.0,
    "feature_fraction_bynode": 1.0, "seed": 20261108, "verbosity": -1,
    "use_missing": False, "zero_as_missing": False,
}


def _libraries():
    import numpy as np
    import scipy
    import lightgbm as lgb

    _need({"numpy": np.__version__, "scipy": scipy.__version__, "lightgbm": lgb.__version__} == VERSIONS,
          "EI_pinned_lightgbm_numeric_versions_required")
    return np, lgb


def _depth_bound(dump: dict) -> None:
    for tree in dump["tree_info"]:
        pending = [(tree["tree_structure"], 0)]
        while pending:
            node, depth = pending.pop()
            _need(depth <= PARAMETERS["max_depth"], "EI_lightgbm_tree_depth_exceeded")
            if "split_index" in node:
                pending.extend(((node["left_child"], depth + 1), (node["right_child"], depth + 1)))


def _model_parameters(text: str) -> None:
    """Bind the serialized native training parameters, not just a claimed header."""
    _need(type(text) is str and text.startswith("tree\nversion=v4\n") and len(text.encode("utf-8")) < MAX_ARTIFACT_BYTES,
          "EI_lightgbm_saved_model_format_differs")
    pieces = text.split("parameters:\n")
    _need(len(pieces) == 2 and "\nend of parameters" in pieces[1], "EI_lightgbm_saved_parameters_missing")
    settings = {}
    for line in pieces[1].split("\nend of parameters", 1)[0].splitlines():
        _need(line.startswith("[") and line.endswith("]") and ": " in line,
              "EI_lightgbm_saved_parameter_format_differs")
        key, value = line[1:-1].split(": ", 1)
        _need(key not in settings, "EI_lightgbm_duplicate_saved_parameter")
        settings[key] = value
    expected = {**PARAMETERS, "num_iterations": ROUNDS}
    for key, value in expected.items():
        saved = settings.get(key)
        if type(value) is bool:
            matches = saved == ("1" if value else "0")
        elif type(value) is int:
            matches = saved == str(value)
        elif type(value) is float:
            matches = saved is not None and float(saved) == value
        else:
            matches = saved == value or key == "metric" and saved == "custom"
        _need(matches, "EI_lightgbm_serialized_training_parameter_differs")


def _validate_artifact(artifact: dict) -> None:
    fields = {"schema", "versions", "parameters", "round_limit", "effective_iterations", "training",
              "model_text", "model_sha256", "dump", "dump_sha256"}
    _need(type(artifact) is dict and set(artifact) == fields and artifact["schema"] == SCHEMA
          and encode(artifact["versions"]) == encode(VERSIONS) and encode(artifact["parameters"]) == encode(PARAMETERS)
          and type(artifact["round_limit"]) is int and artifact["round_limit"] == ROUNDS
          and type(artifact["effective_iterations"]) is int and 1 <= artifact["effective_iterations"] <= ROUNDS,
          "EI_lightgbm_artifact_contract_differs")
    validate_training_contract(artifact["training"])
    _need(artifact["training"]["supported_rows"] > 0, "EI_lightgbm_artifact_without_supported_training")
    _model_parameters(artifact["model_text"])
    _need(sha256(artifact["model_text"].encode("utf-8")).hexdigest() == artifact["model_sha256"]
          and sha256(encode(artifact["dump"])).hexdigest() == artifact["dump_sha256"],
          "EI_lightgbm_model_or_dump_digest_differs")
    replay_multiclass(artifact["dump"], (), feature_names=artifact["training"]["feature_names"],
                      expected_iterations=artifact["effective_iterations"], max_leaves=PARAMETERS["num_leaves"])
    _depth_bound(artifact["dump"])


def _predict_native(booster, matrix):
    return booster.predict(matrix, start_iteration=0, num_iteration=-1, raw_score=False,
                           num_threads=1, pred_early_stop=False)


def _verify_predictions(native, reference) -> tuple[tuple[float, ...], ...]:
    native_rows = tuple(tuple(float(value) for value in row) for row in native)
    _need(len(native_rows) == len(reference), "EI_lightgbm_native_prediction_rows_differ")
    for actual, expected in zip(native_rows, reference, strict=True):
        _need(len(actual) == 10 and all(isfinite(value) and 0 <= value <= 1 for value in actual)
              and isclose(fsum(actual), 1.0, rel_tol=0, abs_tol=1e-12)
              and all(isclose(value, target, rel_tol=1e-12, abs_tol=1e-14)
                      for value, target in zip(actual, expected, strict=True)),
              "EI_lightgbm_independent_probability_replay_differs")
    return native_rows


def _reload(artifact, lgb):
    booster = lgb.Booster(model_str=artifact["model_text"], params={"num_threads": 1})
    _need(booster.current_iteration() == artifact["effective_iterations"]
          and booster.num_trees() == 10 * artifact["effective_iterations"]
          and booster.feature_name() == artifact["training"]["feature_names"],
          "EI_lightgbm_reloaded_identity_differs")
    dump = booster.dump_model(start_iteration=0, num_iteration=-1)
    _need(encode(dump) == encode(artifact["dump"]), "EI_lightgbm_saved_text_dump_mismatch")
    return booster


def fit_lightgbm(prepared: PreparedTraining) -> dict | None:
    """Fit the fixed model once; empty supported training returns no model.

    There is no validation/test set, early-stopping callback, calibration or
    parameter search. Native no-split completion may retain fewer than forty
    iterations; the exact retained count is saved rather than fabricated.
    """
    validate_training(prepared)
    if not prepared.rows:
        return None
    np, lgb = _libraries()
    flat = tuple(row.flat for row in prepared.rows)
    matrix = np.asarray(flat, dtype=np.float64)
    weights = np.asarray(prepared.weights, dtype=np.float32)
    labels = np.asarray(prepared.outcomes, dtype=np.int32)
    dataset = lgb.Dataset(matrix, label=labels, weight=weights, feature_name=list(prepared.feature_names),
                          params=dict(PARAMETERS), free_raw_data=True)
    dataset.construct()
    stored_weights = dataset.get_field("weight")
    _need((stored_weights is None and all(value == 1.0 for value in prepared.weights))
          or (stored_weights is not None and tuple(float(value) for value in stored_weights) == prepared.weights),
          "EI_lightgbm_native_sample_weights_differ")
    _need(tuple(int(value) for value in dataset.get_field("label")) == prepared.outcomes,
          "EI_lightgbm_native_outcome_order_differs")
    trained = lgb.train(dict(PARAMETERS), dataset, num_boost_round=ROUNDS)
    iterations = trained.current_iteration()
    _need(type(iterations) is int and 1 <= iterations <= ROUNDS, "EI_lightgbm_effective_iterations_outside_bound")
    text = trained.model_to_string(start_iteration=0, num_iteration=-1)
    restored = lgb.Booster(model_str=text, params={"num_threads": 1})
    dump = restored.dump_model(start_iteration=0, num_iteration=-1)
    artifact = {"schema": SCHEMA, "versions": dict(VERSIONS), "parameters": dict(PARAMETERS), "round_limit": ROUNDS,
        "effective_iterations": iterations, "training": training_contract(prepared), "model_text": text,
        "model_sha256": sha256(text.encode("utf-8")).hexdigest(), "dump": dump, "dump_sha256": sha256(encode(dump)).hexdigest()}
    _validate_artifact(artifact)
    _reload(artifact, lgb)
    reference = replay_multiclass(dump, flat, feature_names=prepared.feature_names,
                                 expected_iterations=iterations, max_leaves=PARAMETERS["num_leaves"])
    _verify_predictions(_predict_native(trained, matrix), reference)
    _verify_predictions(_predict_native(restored, matrix), reference)
    _need(len(encode(artifact)) <= MAX_ARTIFACT_BYTES, "EI_lightgbm_artifact_size_exceeded")
    return artifact


def predict_lightgbm(artifact: dict, queries: PreparedQueries) -> tuple[tuple[float, ...], ...]:
    """Reload the retained text and independently replay every target-free query."""
    validate_queries(queries)
    _validate_artifact(artifact)
    expected = artifact["training"]
    _need(expected["mode"] == queries.mode and expected["fold"] == queries.fold
          and expected["cutoff_at"] == queries.cutoff_at.isoformat()
          and expected["feature_names"] == list(queries.feature_names), "EI_lightgbm_query_contract_differs")
    np, lgb = _libraries()
    booster = _reload(artifact, lgb)
    if not queries.rows:
        return ()
    flat = tuple(row.flat for row in queries.rows)
    reference = replay_multiclass(artifact["dump"], flat, feature_names=queries.feature_names,
        expected_iterations=artifact["effective_iterations"], max_leaves=PARAMETERS["num_leaves"])
    return _verify_predictions(_predict_native(booster, np.asarray(flat, dtype=np.float64)), reference)


def dump_lightgbm(artifact: dict) -> bytes:
    _validate_artifact(artifact)
    raw = encode(artifact)
    _need(len(raw) <= MAX_ARTIFACT_BYTES, "EI_lightgbm_artifact_size_exceeded")
    return raw


def load_lightgbm(raw: bytes, *, expected_sha256: str) -> dict:
    """Authenticate saved bytes against a caller-supplied external artifact pin."""
    _need(type(raw) is bytes and len(raw) <= MAX_ARTIFACT_BYTES
          and type(expected_sha256) is str and sha256(raw).hexdigest() == expected_sha256,
          "EI_lightgbm_saved_artifact_pin_differs")
    artifact = json.loads(raw)
    _need(encode(artifact) == raw, "EI_lightgbm_saved_artifact_not_canonical")
    _validate_artifact(artifact)
    np, lgb = _libraries()
    _reload(artifact, lgb)
    return artifact
