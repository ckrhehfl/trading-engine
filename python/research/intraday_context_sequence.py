"""Fixed prospective EI causal sequence classifier on shared model inputs.

Only pure EH convolution kernels are reused. Fitting and target-free replay
perform no I/O; callers retain responsibility for the registered resource
envelope and source/evidence authentication. The artifact digest detects
accidental changes, not a malicious replacement of an artifact and its digest.
"""

from __future__ import annotations

from datetime import UTC
from hashlib import sha256
from math import fsum, isfinite, sqrt

from research.intraday_chart_inputs import _need
from research import intraday_context_model_inputs as common
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES
from research.intraday_scenario_sequence import _tower, _tower_backward


SCHEMA = "ei-context-sequence-v1"
NUMPY_VERSION = "2.5.2"
EPOCHS, BATCH_SIZE, SEED = 6, 64, 20261108
LEARNING_RATE, WEIGHT_DECAY = 0.005, 0.01
WIDTH, CHANNELS, STEPS = 12, 7, 30
DILATIONS = (1, 2, 4, 8)
_NAMES = ("minute_projection", *(f"minute_kernel_{index}" for index in range(len(DILATIONS))))


def _numpy():
    import numpy as np

    _need(np.__version__ == NUMPY_VERSION, "pinned_EI_sequence_numpy_required")
    return np


def _static_width(mode: common.Mode) -> int:
    _need(type(mode) is str and mode in ("chart", "chart_context"), "EI_sequence_mode_differs")
    return 17 if mode == "chart_context" else 7


def _architecture(mode: common.Mode) -> dict:
    return {"minute_shape": [STEPS, CHANNELS], "projection_width": WIDTH,
            "kernel_size": 3, "dilations": list(DILATIONS), "activation": "relu",
            "residual": True, "readout": "last_state_then_static", "static_width": _static_width(mode),
            "output_width": len(OUTCOMES), "output_activation": "joint_softmax", "coarse_input": False}


def _optimization(fold: int) -> dict:
    return {"epochs": EPOCHS, "batch_size": BATCH_SIZE, "learning_rate": LEARNING_RATE,
            "weight_decay": WEIGHT_DECAY, "optimizer": "adam_decoupled_weight_decay",
            "beta1": 0.9, "beta2": 0.999, "epsilon": 1e-8, "global_gradient_norm_clip": 1.0,
            "parameter_seed": SEED + fold, "shuffle_seed": SEED + 10 + fold,
            "weight_normalization": "shared_binary32_weights_divided_by_global_mean_and_batch_size",
            "initialization": "shared_chart_head_then_zero_context_head_v1",
            "dtype": "float64", "early_stopping": False, "parameter_search": False}


def _shapes(mode: common.Mode) -> dict[str, tuple[int, ...]]:
    return {_NAMES[0]: (CHANNELS, WIDTH),
            **{name: (3, WIDTH, WIDTH) for name in _NAMES[1:]},
            "head": (WIDTH + _static_width(mode), len(OUTCOMES)), "bias": (len(OUTCOMES),)}


def _initialize(np, mode: common.Mode, rng):
    parameters = {}
    for name, shape in _shapes(mode).items():
        if name == "bias":
            parameters[name] = np.zeros(shape, dtype=np.float64)
        elif name == "head":
            # Preserve both common coefficients and RNG consumption across arms.
            head = rng.normal(0, sqrt(2 / (WIDTH + 7)), (WIDTH + 7, len(OUTCOMES)))
            parameters[name] = (np.vstack((head, np.zeros((10, len(OUTCOMES)))))
                                if mode == "chart_context" else head)
        else:
            scale = sqrt(2 / shape[-2]) * (0.25 if len(shape) == 3 else 1)
            parameters[name] = rng.normal(0, scale, shape)
    return parameters


def _batch(np, rows: tuple[common.PreparedInput, ...], mode: common.Mode):
    minute = np.asarray([row.minute for row in rows], dtype=np.float64)
    static = np.asarray([row.static for row in rows], dtype=np.float64)
    _need(minute.shape == (len(rows), STEPS, CHANNELS)
          and static.shape == (len(rows), _static_width(mode))
          and np.isfinite(minute).all() and np.isfinite(static).all(), "EI_sequence_batch_shape_or_values_differ")
    return minute, static


def _forward(np, batch, parameters):
    minute, static = batch
    states = _tower(np, minute, parameters, _NAMES, DILATIONS)
    joined = np.concatenate((states[-1][:, -1], static), axis=1)
    logits = joined @ parameters["head"] + parameters["bias"]
    _need(np.isfinite(logits).all(), "nonfinite_EI_sequence_activation")
    scores = np.exp(logits - logits.max(axis=1, keepdims=True))
    probabilities = scores / scores.sum(axis=1, keepdims=True)
    _need(np.isfinite(probabilities).all(), "nonfinite_EI_sequence_probabilities")
    return probabilities, joined, states


def _gradients(np, batch, parameters, targets, sample_weights):
    scores, joined, states = _forward(np, batch, parameters)
    scores[np.arange(len(targets)), targets] -= 1
    delta = scores * sample_weights[:, None]
    last_gradient = (delta @ parameters["head"].T)[:, :WIDTH]
    return {"head": joined.T @ delta, "bias": delta.sum(axis=0),
            **_tower_backward(np, batch[0], states, last_gradient, parameters, _NAMES, DILATIONS)}


def _digest(body: dict) -> str:
    return sha256(common.encode(body)).hexdigest()


def fit_sequence(prepared: common.PreparedTraining) -> dict:
    """Fit exactly six epochs from the common mature, pre-cutoff training seal."""
    common.validate_training(prepared)
    _need(bool(prepared.rows), "nonempty_EI_sequence_training_required")
    np = _numpy()
    parameters = _initialize(np, prepared.mode, np.random.default_rng(SEED + prepared.fold))
    shuffle = np.random.default_rng(SEED + 10 + prepared.fold)
    first = {name: np.zeros_like(value) for name, value in parameters.items()}
    second = {name: np.zeros_like(value) for name, value in parameters.items()}
    weights = np.asarray(prepared.weights, dtype=np.float64)
    weight_mean = fsum(prepared.weights) / len(prepared.rows)
    step = 0
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            for _ in range(EPOCHS):
                order = shuffle.permutation(len(prepared.rows))
                for start in range(0, len(prepared.rows), BATCH_SIZE):
                    indices = order[start:start + BATCH_SIZE]
                    batch = _batch(np, tuple(prepared.rows[index] for index in indices), prepared.mode)
                    targets = np.asarray([prepared.outcomes[index] for index in indices], dtype=np.intp)
                    gradients = _gradients(np, batch, parameters, targets, weights[indices] / (weight_mean * len(indices)))
                    norm = sqrt(sum(float(np.sum(gradient ** 2)) for gradient in gradients.values()))
                    _need(isfinite(norm), "nonfinite_EI_sequence_gradient")
                    step += 1
                    for name, value in parameters.items():
                        gradient = gradients[name] / max(1.0, norm)
                        first[name] = 0.9 * first[name] + 0.1 * gradient
                        second[name] = 0.999 * second[name] + 0.001 * gradient ** 2
                        corrected_mean = first[name] / (1 - 0.9 ** step)
                        corrected_variance = second[name] / (1 - 0.999 ** step)
                        value *= 1 - LEARNING_RATE * WEIGHT_DECAY
                        value -= LEARNING_RATE * corrected_mean / (np.sqrt(corrected_variance) + 1e-8)
    except FloatingPointError as error:
        raise InputRefusal("nonfinite_EI_sequence_fit") from error
    _need(all(np.isfinite(value).all() for value in parameters.values()), "nonfinite_EI_sequence_coefficients")
    body = {"schema": SCHEMA, "numpy": NUMPY_VERSION, "architecture": _architecture(prepared.mode),
            "optimization": _optimization(prepared.fold), "training": common.training_contract(prepared),
            "outcomes": [list(value) for value in OUTCOMES],
            "parameters": {name: value.tolist() for name, value in parameters.items()}}
    return {**body, "artifact_sha256": _digest(body)}


def _numeric_parameter(value, shape: tuple[int, ...]) -> bool:
    if shape:
        return type(value) is list and len(value) == shape[0] and all(
            _numeric_parameter(item, shape[1:]) for item in value)
    if type(value) not in (float, int):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _same_json(value, expected) -> bool:
    try:
        return common.encode(value) == common.encode(expected)
    except (TypeError, ValueError, OverflowError):
        return False


def _validate_artifact(artifact: dict, prepared: common.PreparedQueries) -> None:
    _need(type(artifact) is dict and set(artifact) == {
        "schema", "numpy", "architecture", "optimization", "training", "outcomes", "parameters", "artifact_sha256",
    } and artifact["schema"] == SCHEMA and artifact["numpy"] == NUMPY_VERSION, "EI_sequence_artifact_schema_differs")
    common.validate_training_contract(artifact["training"])
    training = artifact["training"]
    _need(training["supported_rows"] > 0 and training["mode"] == prepared.mode
          and training["cutoff_at"] == prepared.cutoff_at.astimezone(UTC).isoformat() and training["fold"] == prepared.fold
          and training["feature_names"] == list(prepared.feature_names), "EI_sequence_query_training_identity_differs")
    _need(_same_json(artifact["architecture"], _architecture(prepared.mode))
          and _same_json(artifact["optimization"], _optimization(prepared.fold))
          and _same_json(artifact["outcomes"], [list(value) for value in OUTCOMES]),
          "EI_sequence_architecture_or_optimization_differs")
    values = artifact["parameters"]
    shapes = _shapes(prepared.mode)
    _need(type(values) is dict and set(values) == set(shapes)
          and all(_numeric_parameter(values[name], shape) for name, shape in shapes.items()),
          "EI_sequence_parameter_shape_or_values_differ")
    body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    _need(type(artifact["artifact_sha256"]) is str and artifact["artifact_sha256"] == _digest(body),
          "EI_sequence_artifact_digest_differs")


def predict_sequence(artifact: dict, prepared: common.PreparedQueries) -> tuple[tuple[float, ...], ...]:
    """Replay one frozen model on target-free rows, preserving query row order."""
    common.validate_queries(prepared)
    _validate_artifact(artifact, prepared)
    np = _numpy()
    parameters = {name: np.asarray(value, dtype=np.float64) for name, value in artifact["parameters"].items()}
    probabilities = []
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            for start in range(0, len(prepared.rows), BATCH_SIZE):
                batch = _batch(np, prepared.rows[start:start + BATCH_SIZE], prepared.mode)
                scores, *_ = _forward(np, batch, parameters)
                probabilities.extend(map(tuple, scores.tolist()))
    except FloatingPointError as error:
        raise InputRefusal("nonfinite_EI_sequence_prediction") from error
    return tuple(probabilities)
