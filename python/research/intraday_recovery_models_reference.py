"""Independent EK inference from saved trees/coefficients and target-free rows.

This verifier does not fit, call native tree prediction, or call the producer's
feature transform/convolution. Pins bind bytes; caller authentication is separate.
"""

from dataclasses import asdict
from datetime import date
from hashlib import sha256
import json
from math import exp, fsum, isclose, isfinite
import os

from research.intraday_recovery_models import ModelPrediction, ModelQuery, PreparedQueries
from research.intraday_context_tree_reference import _dense_feature, _leaf, _tree


ABS_TOL, REL_TOL = 1e-12, 1e-10
_THREADS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")


def _need(condition, reason):
    if not condition:
        raise ValueError("EK_reference_" + reason)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _digest(value):
    return sha256(_encode(value)).hexdigest()


def _numeric(value, shape):
    if shape:
        return type(value) is list and len(value) == shape[0] and all(_numeric(item, shape[1:]) for item in value)
    return type(value) in (int, float) and isfinite(value)


def _training_contract(contract):
    fields = {"schema", "mode", "fold", "cutoff_session", "static_names", "provenance", "path_classes",
        "target_kind", "target_definition", "gross_numeric_boundary", "weighting", "scaling_rule", "order_control",
        "scaling", "candidate_rows", "gross_rows", "path_rows", "date_count", "stock_dates",
        "roster_sha256", "targets_sha256", "weights_sha256", "training_input_sha256"}
    _need(type(contract) is dict and set(contract) == fields and contract["schema"] == "ek-recovery-model-v1"
          and contract["mode"] in ("chart", "chart_peer", "order_removed") and type(contract["fold"]) is int
          and 1 <= contract["fold"] <= 5, "training_fields_or_mode")
    _need(type(contract["cutoff_session"]) is str
          and date.fromisoformat(contract["cutoff_session"]).isoformat() == contract["cutoff_session"], "training_split")
    names = contract["static_names"]
    _need(type(names) is list and all(type(name) is str and name for name in names)
          and len(names) == len(set(names)), "static_names")
    pins = contract["provenance"]
    _need(type(pins) is dict and set(pins) == {"source_sha256", "runtime_sha256", "registration_sha256",
                                           "features_sha256", "labels_sha256"}, "provenance_fields")
    hashes = (*pins.values(), *(contract[name] for name in (
        "roster_sha256", "targets_sha256", "weights_sha256", "training_input_sha256")))
    _need(all(type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
              for value in hashes), "provenance_hashes")
    target = contract["target_kind"]
    _need(target in ("opening", "holding_remaining") and contract["target_definition"] == (
        "opening_executable_structural60_gross_pct_and_frozen_limit_66bp_close_first" if target == "opening"
        else "current_held_state_gross_to_original_structural60_expiry_no_path_head")
        and contract["path_classes"] == ["target_66bp_first", "structural_invalidation_first", "timeout"]
        and contract["gross_numeric_boundary"] == "common_ieee754_binary32_targets_before_any_learner"
        and contract["weighting"] == "equal_dates_then_stocks_then_supported_rows_binary32_per_target"
        and contract["scaling_rule"] == "training_only_channel_and_static_weighted_population_standardization"
        and contract["order_control"] == "deterministic_prefix59_permutation_latest_and_static_unchanged",
        "scientific_contract")
    counts = [contract[name] for name in ("candidate_rows", "gross_rows", "path_rows", "date_count", "stock_dates")]
    _need(all(type(value) is int and value >= 0 for value in counts) and max(counts[1:]) <= counts[0]
          and counts[3] <= counts[4] and (target == "opening" or counts[2] == 0), "training_support")
    width = (13 if contract["mode"] == "chart_peer" else 10) + len(names)
    scaling = contract["scaling"]
    _need(type(scaling) is dict and set(scaling) == {"mean", "scale"}
          and all(_numeric(scaling[name], (width,)) for name in scaling)
          and all(value > 0 for value in scaling["scale"]), "training_scaling")


def _names(contract):
    channels = 13 if contract["mode"] == "chart_peer" else 10
    return [f"minute_{at:02d}_channel_{channel:02d}" for at in range(60) for channel in range(channels)] + [
        f"static_{index:03d}_{name}" for index, name in enumerate(contract["static_names"])]


def _architecture(contract):
    return {"steps": 60, "channels": 13 if contract["mode"] == "chart_peer" else 10,
        "static_width": len(contract["static_names"]), "width": 12, "dilations": [1, 2, 4, 8, 16], "kernel": 3,
        "readout": "last_state_then_static", "outputs": "gross_regression_and_three_class_softmax",
        "epochs": 6, "batch_size": 64, "seed": 20261110 + contract["fold"],
        "shuffle_seed": 20261120 + contract["fold"], "learning_rate": 0.005,
        "weight_decay": 0.01, "gradient_clip": 1.0, "dtype": "float64",
        "optimizer": "adam_decoupled_weight_decay", "early_stopping": False, "parameter_search": False}


def _lgb_parameters(contract, head):
    parameters = {"boosting": "gbdt", "metric": "None", "num_leaves": 7, "max_depth": 3,
        "min_data_in_leaf": 20, "learning_rate": 0.05, "lambda_l2": 1.0,
        "num_threads": 1, "deterministic": True, "force_col_wise": True,
        "bagging_fraction": 1.0, "bagging_freq": 0, "feature_fraction": 1.0,
        "feature_fraction_bynode": 1.0, "verbosity": -1, "device_type": "cpu",
        "use_missing": False, "zero_as_missing": False, "seed": 20261110 + contract["fold"],
        "objective": "regression_l2" if head == "gross" else "multiclass"}
    if head == "path":
        parameters["num_class"] = 3
    return parameters


def _serialized_parameters(text, expected):
    _need(type(text) is str and text.startswith("tree\nversion=v4\n") and text.count("parameters:\n") == 1,
          "native_model_format")
    section = text.split("parameters:\n", 1)[1].split("\nend of parameters", 1)[0]
    values = {}
    for line in section.splitlines():
        _need(line.startswith("[") and line.endswith("]") and ": " in line, "native_parameter_format")
        name, value = line[1:-1].split(": ", 1)
        _need(name not in values, "duplicate_native_parameter")
        values[name] = value
    for name, expected_value in {**expected, "num_iterations": 40}.items():
        value = values.get(name)
        if type(expected_value) is bool:
            valid = value == str(int(expected_value))
        elif type(expected_value) is float:
            valid = value is not None and float(value) == expected_value
        else:
            valid = value == str(expected_value)
            if name == "objective" and expected_value == "regression_l2":
                valid = value == "regression"
            if name == "metric":
                valid = value == "custom"
        _need(valid, "native_training_parameter_differs:" + name)


def _compiled(model, contract, head):
    _need(type(model) is dict and set(model) == {"parameters", "iterations", "model_text", "dump", "model_sha256",
                                              "native_training"},
          "tree_artifact_fields")
    training = model["native_training"]
    _need(type(training) is dict and set(training) == {"rows", "keys_sha256", "targets_sha256", "weights_sha256"}
          and type(training["rows"]) is int and training["rows"] == contract[head + "_rows"]
          and all(type(training[name]) is str and len(training[name]) == 64
                  and all(char in "0123456789abcdef" for char in training[name])
                  for name in ("keys_sha256", "targets_sha256", "weights_sha256")), "native_training_manifest")
    expected = _lgb_parameters(contract, head)
    _need(_encode(model["parameters"]) == _encode(expected), "tree_parameters")
    _serialized_parameters(model["model_text"], expected)
    _need(model["model_sha256"] == sha256(model["model_text"].encode()).hexdigest(), "native_model_hash")
    iterations = model["iterations"]
    _need(type(iterations) is int and 1 <= iterations <= 40, "tree_iterations")
    dump, names = model["dump"], _names(contract)
    classes = 1 if head == "gross" else 3
    objective = "regression" if head == "gross" else "multiclass num_class:3"
    _need(type(dump) is dict and dump.get("name") == "tree" and dump.get("version") == "v4"
          and dump.get("objective") == objective and type(dump.get("num_class")) is int
          and dump["num_class"] == classes and type(dump.get("num_tree_per_iteration")) is int
          and dump["num_tree_per_iteration"] == classes and dump.get("average_output") is False
          and dump.get("feature_names") == names and dump.get("max_feature_idx") == len(names) - 1
          and dump.get("pandas_categorical") in (None, []), "tree_geometry_or_objective")
    trees = dump.get("tree_info")
    _need(type(trees) is list and len(trees) == classes * iterations, "tree_count")
    for tree in trees:
        pending = [(tree["tree_structure"], 0)]
        while pending:
            node, depth = pending.pop()
            _need(depth <= 3, "tree_depth")
            if "split_index" in node:
                pending.extend(((node["left_child"], depth + 1), (node["right_child"], depth + 1)))
    return tuple(_tree(tree, index, len(names), 7) for index, tree in enumerate(trees))


def validate_artifact(artifact):
    """Check every saved coefficient/tree, including branches no query visits."""
    _need(type(artifact) is dict and set(artifact) == {
        "schema", "kind", "training", "runtime", "payload", "artifact_sha256",
    } and artifact["schema"] == "ek-recovery-model-v1" and artifact["kind"] in (
        "empirical", "lightgbm", "sequence"), "artifact_fields")
    _need(artifact["artifact_sha256"] == _digest({key: value for key, value in artifact.items()
                                               if key != "artifact_sha256"}), "artifact_hash")
    contract, payload = artifact["training"], artifact["payload"]
    _training_contract(contract)
    expected_runtime = ({} if artifact["kind"] == "empirical" else {"numpy": "2.5.2"} if artifact["kind"] == "sequence"
                        else {"numpy": "2.5.2", "scipy": "1.18.1", "lightgbm": "4.6.0"})
    _need(artifact["runtime"] == expected_runtime, "runtime_identity")
    gross_support, path_support = contract["gross_rows"] > 0, contract["path_rows"] > 0
    if artifact["kind"] == "empirical":
        _need(type(payload) is dict and set(payload) == {
            "gross_pct", "probabilities", "prior_per_class", "effective_path_dates",
        } and payload["prior_per_class"] == 0.5 and type(payload["effective_path_dates"]) is int
            and 0 <= payload["effective_path_dates"] <= contract["date_count"], "empirical_fields")
        _prediction_values(payload["gross_pct"], payload["probabilities"], gross_support, path_support)
    elif artifact["kind"] == "lightgbm":
        _need(type(payload) is dict and set(payload) == {"gross", "path"}, "tree_heads")
        for head, support in (("gross", gross_support), ("path", path_support)):
            _need((payload[head] is not None) == support, "tree_support")
            if support:
                _compiled(payload[head], contract, head)
    else:
        _need(type(payload) is dict and set(payload) == {"architecture", "parameters"}
              and _encode(payload["architecture"]) == _encode(_architecture(contract)), "sequence_architecture")
        parameters = payload["parameters"]
        if not (gross_support or path_support):
            _need(parameters is None, "unsupported_sequence_parameters")
            return
        channels = 13 if contract["mode"] == "chart_peer" else 10
        shapes = {"projection": (channels, 12), **{f"kernel_{index}": (3, 12, 12) for index in range(5)},
                  "head": (12 + len(contract["static_names"]), 4), "bias": (4,)}
        _need(type(parameters) is dict and set(parameters) == set(shapes)
              and all(_numeric(parameters[name], shape) for name, shape in shapes.items()), "sequence_coefficient_geometry")


def _query_rows(queries, contract):
    _need(type(queries) is PreparedQueries and queries.contract == contract and type(queries.rows) is tuple,
          "target_free_query_contract")
    _need(queries.input_sha256 == _digest({"rows": [asdict(row) for row in queries.rows], "contract": contract}),
          "query_hash")
    keys = set()
    result = []
    for row in queries.rows:
        _need(type(row) is ModelQuery and type(row.key) is str and row.key and row.key not in keys
              and type(row.session) is str and date.fromisoformat(row.session).isoformat() == row.session
              and row.session >= contract["cutoff_session"], "query_identity_or_split")
        keys.add(row.key)
        _need(type(row.minute) is tuple and len(row.minute) == 60
              and all(type(point) is tuple and len(point) == 10
                      and all(type(value) is float and isfinite(value) for value in point) for point in row.minute)
              and type(row.static) is tuple and len(row.static) == len(contract["static_names"])
              and all(type(value) is float and isfinite(value) for value in row.static), "query_features")
        minute = list(row.minute)
        if contract["mode"] == "order_removed":
            order = sorted(range(59), key=lambda index: sha256(
                ("EK-order-v1\0" + row.key + "\0" + str(index)).encode()).digest())
            minute = [minute[index] for index in order] + minute[-1:]
        if contract["mode"] == "chart_peer":
            _need(type(row.peer) is tuple and len(row.peer) == 60
                  and all(type(point) is tuple and len(point) == 3
                          and all(type(value) is float and isfinite(value) for value in point) for point in row.peer),
                  "query_peer")
            minute = [(*point, *peer) for point, peer in zip(minute, row.peer, strict=True)]
        else:
            _need(row.peer is None, "chart_has_peer")
        channels = len(minute[0])
        mean, scale = contract["scaling"]["mean"], contract["scaling"]["scale"]
        normalized = tuple(tuple((value - center) / divisor for value, center, divisor in zip(
            point, mean[:channels], scale[:channels], strict=True)) for point in minute)
        static = tuple((value - center) / divisor for value, center, divisor in zip(
            row.static, mean[channels:], scale[channels:], strict=True))
        _need(all(isfinite(value) for point in normalized for value in point) and all(map(isfinite, static)),
              "transformed_value")
        result.append((normalized, static))
    return result


def _softmax(logits):
    maximum = max(logits)
    values = tuple(exp(value - maximum) for value in logits)
    total = fsum(values)
    return tuple(value / total for value in values)


def _tree_outputs(payload, rows, contract):
    flat = [tuple(_dense_feature(value) for point in minute for value in point) + tuple(map(_dense_feature, static))
            for minute, static in rows]
    result = [[None, None] for _ in rows]
    for head, column in (("gross", 0), ("path", 1)):
        if payload[head] is None:
            continue
        trees = _compiled(payload[head], contract, head)
        for index, row in enumerate(flat):
            if head == "gross":
                # Native accumulation is ordered tree summation, not an adjusted leaf shrinkage.
                result[index][column] = sum(_leaf(tree, row) for tree in trees)
            else:
                logits = [0.0, 0.0, 0.0]
                for number, tree in enumerate(trees):
                    logits[number % 3] += _leaf(tree, row)
                result[index][column] = _softmax(logits)
    return result


def _sequence_outputs(payload, rows, contract):
    if payload["parameters"] is None or not rows:
        return [[None, None] for _ in rows]
    _need(all(os.environ.get(name) == "1" for name in _THREADS), "single_native_thread_environment")
    import numpy as np
    _need(np.__version__ == "2.5.2", "numpy_version")
    coefficients = {name: np.asarray(value, dtype=np.float64) for name, value in payload["parameters"].items()}
    results = []
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        for start in range(0, len(rows), 64):
            batch = rows[start:start + 64]
            minute = np.asarray([point for point, _ in batch], dtype=np.float64)
            static = np.asarray([value for _, value in batch], dtype=np.float64)
            state = np.maximum(np.einsum("btc,cw->btw", minute, coefficients["projection"]), 0)
            for index, dilation in enumerate((1, 2, 4, 8, 16)):
                padded = np.pad(state, ((0, 0), (2 * dilation, 0), (0, 0)))
                offsets = 2 * dilation + np.arange(60)[:, None] - dilation * np.arange(3)
                windows = np.take(padded, offsets, axis=1)
                mapped = np.einsum("btli,lio->bto", windows, coefficients[f"kernel_{index}"], optimize=True)
                state = np.maximum(state + mapped, 0)
            joined = np.concatenate((state[:, -1], static), axis=1)
            values = np.einsum("bi,io->bo", joined, coefficients["head"]) + coefficients["bias"]
            _need(np.isfinite(values).all(), "sequence_nonfinite")
            for row in values:
                results.append([float(row[0]) if contract["gross_rows"] else None,
                                _softmax(tuple(map(float, row[1:]))) if contract["path_rows"] else None])
    return results


def _prediction_values(gross, probabilities, gross_support, path_support):
    _need((gross is not None) == gross_support and (probabilities is not None) == path_support,
          "prediction_support")
    _need(gross is None or type(gross) is float and isfinite(gross), "gross_value")
    _need(probabilities is None or type(probabilities) in (tuple, list) and len(probabilities) == 3
          and all(type(value) is float and isfinite(value) and 0 <= value <= 1 for value in probabilities)
          and isclose(fsum(probabilities), 1.0, rel_tol=0, abs_tol=1e-12), "probability_values")


def verify_predictions(artifact, queries, predictions):
    """Reconstruct every prediction and retain strict signs at zero and 33 bp."""
    validate_artifact(artifact)
    contract, payload = artifact["training"], artifact["payload"]
    rows = _query_rows(queries, contract)
    if artifact["kind"] == "empirical":
        expected = [(payload["gross_pct"], payload["probabilities"]) for _ in rows]
    elif artifact["kind"] == "lightgbm":
        expected = _tree_outputs(payload, rows, contract)
    else:
        expected = _sequence_outputs(payload, rows, contract)
    _need(type(predictions) is tuple and len(predictions) == len(queries.rows), "prediction_roster")
    for query, actual, (gross, paths) in zip(queries.rows, predictions, expected, strict=True):
        _need(type(actual) is ModelPrediction and actual.key == query.key, "prediction_identity")
        _prediction_values(actual.gross_pct, actual.probabilities, bool(contract["gross_rows"]), bool(contract["path_rows"]))
        if gross is not None:
            _need(isclose(actual.gross_pct, gross, rel_tol=REL_TOL, abs_tol=ABS_TOL)
                  and all((actual.gross_pct > threshold) == (gross > threshold) for threshold in (0.0, 0.33)),
                  "gross_replay_or_admission_sign")
        if paths is not None:
            _need(all(isclose(value, target, rel_tol=REL_TOL, abs_tol=ABS_TOL)
                      for value, target in zip(actual.probabilities, paths, strict=True)), "path_probability_replay")
    return {"verified": True, "rows": len(rows), "artifact_sha256": artifact["artifact_sha256"],
            "query_input_sha256": queries.input_sha256,
            "predictions_sha256": _digest([asdict(row) for row in predictions])}
