"""Fixed EH causal sequence classifier with target-free replay of numeric weights.

Only mature, pre-split labels are admitted to fitting. The caller owns source
authentication, common-support selection and the worker resource envelope.
The caller applies the existing observed-prefix contract to joint predictions.
"""
from __future__ import annotations

from array import array
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asinh, isfinite, sqrt
from typing import Literal

from research.intraday_chart_inputs import _clock, _need
from research.intraday_scenario_decision_inputs import ChartDecisionInputs, PeerDecisionInputs
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome
from research.intraday_scenario_labels import QuestionId


SCHEMA = "eh-temporal-convolution-v1"
NUMPY_VERSION = "2.5.2"
EPOCHS = 6
BATCH_SIZE = 64
SEED = 20261107
LEARNING_RATE = 0.005
WEIGHT_DECAY = 0.01
MINUTE_WIDTH, COARSE_WIDTH = 12, 6
MINUTE_DILATIONS, COARSE_DILATIONS = (1, 2, 4, 8), (1, 2)
STATIC_WIDTH = 7
_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class SequenceInputs:
    forecast_id: tuple[str, QuestionId, datetime]
    minute: tuple[tuple[float, ...], ...]
    coarse: tuple[tuple[float, ...], ...]
    static: tuple[float, ...]
    peer: tuple[tuple[float, ...], ...] | None = None


@dataclass(frozen=True)
class PackedSequenceInputs:
    forecast_id: tuple[str, QuestionId, datetime]
    minute: bytes
    coarse: bytes
    static: bytes
    peer: bytes | None


@dataclass(frozen=True)
class SequenceTrainingRow:
    inputs: SequenceInputs | PackedSequenceInputs
    outcome: TrainingOutcome


def sequence_inputs(
    chart: ChartDecisionInputs, stage: Literal["approach", "breakout"], peer: PeerDecisionInputs | None = None,
) -> SequenceInputs:
    """Freeze identical chart channels for both arms and attach only same-clock peers."""
    _need(type(chart) is ChartDecisionInputs and chart.status == "ready" and stage in ("approach", "breakout")
          and len(chart.minutes) == 20 and all(len(row) == 4 for row in chart.minutes)
          and len(chart.five_minute_bars) == 4 and all(len(row) == 4 for row in chart.five_minute_bars)
          and len(chart.state) == 6, "ready_EH_sequence_chart_required")
    symbol, question_id, decision_at = chart.forecast_id
    _clock(question_id[0])
    _clock(decision_at)
    _need(type(symbol) is str and bool(symbol) and question_id[0] <= decision_at, "invalid_EH_sequence_coordinate")

    peer_steps = None
    if peer is not None:
        _need(type(peer) is PeerDecisionInputs and (peer.symbol, peer.decision_at) == (symbol, decision_at)
              and peer.sequence is not None and len(peer.sequence) == 20
              and all(len(row) == 3 for row in peer.sequence), "matched_EH_peer_sequence_required")
        # Return channels are fractions. Use percentage points and center breadth at one half.
        peer_steps = tuple((asinh(100 * oc), asinh(100 * cumulative), float(2 * breadth - 1))
                           for oc, cumulative, breadth in peer.sequence)

    minute = tuple(tuple(asinh(value) for value in row) for row in chart.minutes)
    coarse = tuple(tuple(asinh(value) for value in row) for row in chart.five_minute_bars)
    upper_distance, lower_distance, *state = chart.state
    static = (asinh(upper_distance), asinh(lower_distance), *map(float, state), float(stage == "breakout"))
    return SequenceInputs(chart.forecast_id, minute, coarse, static, peer_steps)


def pack_training_inputs(inputs: SequenceInputs) -> PackedSequenceInputs:
    """Keep one lossless float64 buffer per channel group while accumulating a fold."""
    _need(type(inputs) is SequenceInputs, "typed_EH_training_sequence_required")
    minute = array("d", (value for row in inputs.minute for value in row)).tobytes()
    coarse = array("d", (value for row in inputs.coarse for value in row)).tobytes()
    static = array("d", inputs.static).tobytes()
    peer = None if inputs.peer is None else array("d", (value for row in inputs.peer for value in row)).tobytes()
    return PackedSequenceInputs(inputs.forecast_id, minute, coarse, static, peer)


def _numpy():
    import numpy as np

    _need(np.__version__ == NUMPY_VERSION, "pinned_EH_sequence_numpy_required")
    return np


def _tower_names(name, dilations):
    return (f"{name}_projection", *(f"{name}_kernel_{index}" for index in range(len(dilations))))


_MINUTE_NAMES = _tower_names("minute", MINUTE_DILATIONS)
_COARSE_NAMES = _tower_names("coarse", COARSE_DILATIONS)


def _shapes(with_peers):
    return {
        _MINUTE_NAMES[0]: (7 if with_peers else 4, MINUTE_WIDTH),
        **{name: (3, MINUTE_WIDTH, MINUTE_WIDTH) for name in _MINUTE_NAMES[1:]},
        _COARSE_NAMES[0]: (4, COARSE_WIDTH),
        **{name: (3, COARSE_WIDTH, COARSE_WIDTH) for name in _COARSE_NAMES[1:]},
        "head": (MINUTE_WIDTH + COARSE_WIDTH + STATIC_WIDTH, len(OUTCOMES)),
        "bias": (len(OUTCOMES),),
    }


def _initialize(np, with_peers, rng):
    parameters = {}
    for name, shape in _shapes(with_peers).items():
        if name == _MINUTE_NAMES[0]:
            chart = rng.normal(0, sqrt(2 / 4), (4, MINUTE_WIDTH))
            parameters[name] = np.vstack((chart, np.zeros((3, MINUTE_WIDTH)))) if with_peers else chart
        elif name == "bias":
            parameters[name] = np.zeros(shape)
        else:
            scale = sqrt(2 / shape[-2])
            if len(shape) == 3:
                scale *= 0.25
            parameters[name] = rng.normal(0, scale, shape)
    return parameters


def _causal_convolution(sequence, kernel, dilation):
    """kernel[lag] maps time t-lag*dilation to time t."""
    result = sequence @ kernel[0]
    for lag in range(1, len(kernel)):
        offset = lag * dilation
        if offset < sequence.shape[1]:
            result[:, offset:] += sequence[:, :-offset] @ kernel[lag]
    return result


def _outer_sum(left, right):
    return left.reshape(-1, left.shape[-1]).T @ right.reshape(-1, right.shape[-1])


def _convolution_backward(np, sequence, kernel, dilation, upstream):
    kernel_gradient = np.zeros_like(kernel)
    kernel_gradient[0] = _outer_sum(sequence, upstream)
    input_gradient = upstream @ kernel[0].T
    for lag in range(1, len(kernel)):
        offset = lag * dilation
        if offset < sequence.shape[1]:
            past = sequence[:, :-offset]
            future_gradient = upstream[:, offset:]
            kernel_gradient[lag] = _outer_sum(past, future_gradient)
            input_gradient[:, :-offset] += future_gradient @ kernel[lag].T
    return input_gradient, kernel_gradient


def _tower(np, sequence, parameters, names, dilations):
    activations = [np.maximum(sequence @ parameters[names[0]], 0)]
    for name, dilation in zip(names[1:], dilations, strict=True):
        previous = activations[-1]
        activations.append(np.maximum(previous + _causal_convolution(previous, parameters[name], dilation), 0))
    return activations


def _tower_backward(np, sequence, activations, last_gradient, parameters, names, dilations):
    gradient = np.zeros_like(activations[-1])
    gradient[:, -1] = last_gradient
    result = {}
    for layer in reversed(range(len(dilations))):
        name = names[layer + 1]
        upstream = gradient * (activations[layer + 1] > 0)
        source_gradient, result[name] = _convolution_backward(
            np, activations[layer], parameters[name], dilations[layer], upstream,
        )
        gradient = upstream + source_gradient
    result[names[0]] = _outer_sum(sequence, gradient * (activations[0] > 0))
    return result


def _forward(np, batch, parameters):
    minute, coarse, static = batch
    fine_states = _tower(np, minute, parameters, _MINUTE_NAMES, MINUTE_DILATIONS)
    coarse_states = _tower(np, coarse, parameters, _COARSE_NAMES, COARSE_DILATIONS)
    joined = np.concatenate((fine_states[-1][:, -1], coarse_states[-1][:, -1], static), axis=1)
    logits = joined @ parameters["head"] + parameters["bias"]
    scores = np.exp(logits - logits.max(axis=1, keepdims=True))
    return scores / scores.sum(axis=1, keepdims=True), joined, fine_states, coarse_states


def _gradients(np, batch, parameters, targets, sample_weights):
    scores, joined, fine_states, coarse_states = _forward(np, batch, parameters)
    scores[np.arange(len(targets)), targets] -= 1
    delta = scores * sample_weights[:, None]
    joined_gradient = delta @ parameters["head"].T
    fine_gradient = joined_gradient[:, :MINUTE_WIDTH]
    coarse_gradient = joined_gradient[:, MINUTE_WIDTH:MINUTE_WIDTH + COARSE_WIDTH]
    return {"head": joined.T @ delta, "bias": delta.sum(axis=0),
            **_tower_backward(np, batch[0], fine_states, fine_gradient, parameters, _MINUTE_NAMES, MINUTE_DILATIONS),
            **_tower_backward(np, batch[1], coarse_states, coarse_gradient, parameters, _COARSE_NAMES, COARSE_DILATIONS)}


def _batch(np, inputs, with_peers):
    size = len(inputs)
    packed = type(inputs[0]) is PackedSequenceInputs

    def field(name, shape):
        values = [getattr(row, name) for row in inputs]
        if packed:
            elements = size
            for dimension in shape:
                elements *= dimension
            raw = b"".join(values)
            _need(len(raw) == elements * 8 and all(len(value) == len(raw) // size for value in values),
                  "invalid_EH_packed_training_shape")
            return np.frombuffer(raw, dtype=np.float64).reshape((size, *shape))
        return np.asarray(values, dtype=np.float64)

    minute = field("minute", (20, 4))
    coarse = field("coarse", (4, 4))
    static = field("static", (STATIC_WIDTH,))
    _need((minute.shape, coarse.shape, static.shape) == ((size, 20, 4), (size, 4, 4), (size, STATIC_WIDTH)),
          "invalid_EH_sequence_input_shape")
    if with_peers:
        _need(all(row.peer is not None for row in inputs), "common_EH_peer_support_required")
        peer = field("peer", (20, 3))
        _need(peer.shape == (size, 20, 3), "invalid_EH_sequence_peer_shape")
        minute = np.concatenate((minute, peer), axis=2)
    _need(all(np.isfinite(value).all() for value in (minute, coarse, static)), "nonfinite_EH_sequence_input")
    return minute, coarse, static


def _training_rows(training, evaluation_starts_at):
    _need(type(training) is tuple and bool(training), "nonempty_EH_sequence_training_required")
    _clock(evaluation_starts_at)
    split_day = evaluation_starts_at.astimezone(_KST).date()
    input_type = type(training[0].inputs) if type(training[0]) is SequenceTrainingRow else None
    _need(input_type in (SequenceInputs, PackedSequenceInputs), "invalid_EH_sequence_training_outcome")
    seen = set()
    for row in training:
        _need(type(row) is SequenceTrainingRow and type(row.inputs) is input_type
              and type(row.outcome) is TrainingOutcome and type(row.outcome.index) is int
              and 0 <= row.outcome.index < len(OUTCOMES), "invalid_EH_sequence_training_outcome")
        symbol, question_id, decision_at = row.inputs.forecast_id
        _clock(question_id[0])
        _clock(decision_at)
        _clock(row.outcome.mature_at)
        _need(question_id[0] <= decision_at < row.outcome.mature_at < evaluation_starts_at
              and question_id[0].astimezone(_KST).date() < split_day and row.inputs.forecast_id not in seen,
              "EH_sequence_training_maturity_split_or_duplicate")
        seen.add(row.inputs.forecast_id)

    rows = tuple(sorted(training, key=lambda row: row.inputs.forecast_id))
    # A symbol/session is one EH event. Approach, breakout and updates share one unit of weight.
    keys = tuple((row.inputs.forecast_id[0], row.inputs.forecast_id[1][0].astimezone(_KST).date()) for row in rows)
    counts = Counter(keys)
    return rows, tuple(1.0 / counts[key] for key in keys), len(counts)


def fit_sequence(
    training: tuple[SequenceTrainingRow, ...], *, evaluation_starts_at: datetime, fold_index: int,
    with_peers: bool = False,
) -> dict:
    """Fit one fixed chronological fold and return JSON-compatible numeric coefficients."""
    _need(type(fold_index) is int and 1 <= fold_index <= 5 and type(with_peers) is bool,
          "fixed_EH_sequence_fold_and_arm_required")
    rows, weights, event_count = _training_rows(training, evaluation_starts_at)
    np = _numpy()
    parameters = _initialize(np, with_peers, np.random.default_rng(SEED + fold_index))
    shuffle = np.random.default_rng(SEED + 10 + fold_index)
    first = {name: np.zeros_like(value) for name, value in parameters.items()}
    second = {name: np.zeros_like(value) for name, value in parameters.items()}
    event_weights = np.asarray(weights, dtype=np.float64)
    # The global mean makes each mini-batch estimate the same event-weighted objective.
    weight_mean = event_count / len(rows)
    step = 0
    for _ in range(EPOCHS):
        order = shuffle.permutation(len(rows))
        for start in range(0, len(rows), BATCH_SIZE):
            indices = order[start:start + BATCH_SIZE]
            batch = _batch(np, tuple(rows[index].inputs for index in indices), with_peers)
            targets = np.asarray([rows[index].outcome.index for index in indices], dtype=np.intp)
            gradients = _gradients(np, batch, parameters, targets, event_weights[indices] / (weight_mean * len(indices)))
            norm = sqrt(sum(float(np.sum(gradient ** 2)) for gradient in gradients.values()))
            _need(isfinite(norm), "nonfinite_EH_sequence_gradient")
            step += 1
            for name, value in parameters.items():
                gradient = gradients[name] / max(1.0, norm)
                first[name] = 0.9 * first[name] + 0.1 * gradient
                second[name] = 0.999 * second[name] + 0.001 * gradient ** 2
                corrected_mean = first[name] / (1 - 0.9 ** step)
                corrected_variance = second[name] / (1 - 0.999 ** step)
                value *= 1 - LEARNING_RATE * WEIGHT_DECAY
                value -= LEARNING_RATE * corrected_mean / (np.sqrt(corrected_variance) + 1e-8)

    _need(all(np.isfinite(value).all() for value in parameters.values()), "nonfinite_EH_sequence_coefficients")
    return {"schema": SCHEMA, "numpy": NUMPY_VERSION, "with_peers": with_peers, "fold_index": fold_index,
            "evaluation_starts_at": evaluation_starts_at.isoformat(), "training_rows": len(rows),
            "training_events": event_count, "outcomes": [list(value) for value in OUTCOMES],
            "parameters": {name: value.tolist() for name, value in parameters.items()}}


def _numeric_parameter(value, shape):
    if shape:
        return type(value) is list and len(value) == shape[0] and all(
            _numeric_parameter(item, shape[1:]) for item in value
        )
    return type(value) in (float, int) and isfinite(value)


def predict_sequence(artifact: dict, queries: tuple[SequenceInputs, ...]) -> tuple[tuple[float, ...], ...]:
    """Return joint probabilities in OUTCOMES order from a frozen artifact."""
    _need(type(artifact) is dict and set(artifact) == {
        "schema", "numpy", "with_peers", "fold_index", "evaluation_starts_at", "training_rows", "training_events",
        "outcomes", "parameters",
    } and artifact["schema"] == SCHEMA and artifact["numpy"] == NUMPY_VERSION
          and type(artifact["with_peers"]) is bool and type(artifact["fold_index"]) is int
          and 1 <= artifact["fold_index"] <= 5 and type(artifact["training_rows"]) is int
          and type(artifact["training_events"]) is int and 0 < artifact["training_events"] <= artifact["training_rows"]
          and type(artifact["evaluation_starts_at"]) is str
          and artifact["outcomes"] == [list(value) for value in OUTCOMES], "invalid_EH_sequence_artifact")
    cutoff = datetime.fromisoformat(artifact["evaluation_starts_at"])
    _clock(cutoff)
    _need(type(queries) is tuple and all(type(row) is SequenceInputs for row in queries), "target_free_EH_queries_required")
    for row in queries:
        decision_at = row.forecast_id[2]
        _clock(decision_at)
        _need(decision_at >= cutoff, "EH_sequence_query_precedes_evaluation_split")

    np = _numpy()
    with_peers = artifact["with_peers"]
    shapes = _shapes(with_peers)
    values = artifact["parameters"]
    _need(type(values) is dict and set(values) == set(shapes), "invalid_EH_sequence_parameter_roster")
    _need(all(_numeric_parameter(value, shapes[name]) for name, value in values.items()),
          "invalid_EH_sequence_coefficients")
    parameters = {name: np.asarray(value, dtype=np.float64) for name, value in values.items()}
    probabilities = []
    for start in range(0, len(queries), BATCH_SIZE):
        scores, *_ = _forward(np, _batch(np, queries[start:start + BATCH_SIZE], with_peers), parameters)
        _need(np.isfinite(scores).all(), "nonfinite_EH_sequence_probabilities")
        probabilities.extend(map(tuple, scores.tolist()))
    return tuple(probabilities)
