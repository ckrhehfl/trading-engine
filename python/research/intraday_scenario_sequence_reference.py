"""Independent inference check for frozen EH sequence coefficients.

Inputs and coefficients are supplied by the sealed research runner. This check
reconstructs probabilities and verifies saved query identities without a refit.
"""
from datetime import datetime, timedelta
from fractions import Fraction
from math import isfinite

from research.intraday_chart_inputs import _need
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES
from research.intraday_scenario_sequence import SequenceInputs


ABS_TOL = 1e-12
REL_TOL = 1e-10
MASS_TOL = 1e-12
_BATCH = 128
_SCHEMA = "eh-temporal-convolution-v1"
_NUMPY_VERSION = "2.5.2"
_TOWERS = (("minute", 12, (1, 2, 4, 8)), ("coarse", 6, (1, 2)))


def _aware_minute(value):
    return (type(value) is datetime and value.utcoffset() is not None
            and value.second == value.microsecond == 0)


def _coordinate(identity):
    _need(type(identity) is tuple and len(identity) == 3, "invalid_EH_sequence_reference_coordinate")
    symbol, question, at = identity
    _need(type(symbol) is str and bool(symbol) and type(question) is tuple and len(question) == 6,
          "invalid_EH_sequence_reference_coordinate")
    start, level, band, target, stop, end = question
    _need(_aware_minute(start) and _aware_minute(end) and _aware_minute(at)
          and end - start == timedelta(minutes=30) and start <= at <= end
          and (at - start) % timedelta(minutes=5) == timedelta(0), "invalid_EH_sequence_reference_query_clock")
    _need(all(type(price) is Fraction for price in (level, band, target, stop))
          and band > 0 and 0 < stop <= level - band < level + band < target,
          "invalid_EH_sequence_reference_question")
    return at


def _numeric_block(value, shape, container):
    if shape:
        return type(value) is container and len(value) == shape[0] and all(
            _numeric_block(item, shape[1:], container) for item in value
        )
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _artifact(np, artifact):
    _need(type(artifact) is dict and set(artifact) == {
        "schema", "numpy", "with_peers", "fold_index", "evaluation_starts_at", "training_rows", "training_events",
        "outcomes", "parameters",
    }, "invalid_EH_sequence_reference_artifact")
    _need(artifact["schema"] == _SCHEMA and artifact["numpy"] == _NUMPY_VERSION
          and artifact["outcomes"] == [list(outcome) for outcome in OUTCOMES]
          and type(artifact["with_peers"]) is bool and type(artifact["fold_index"]) is int
          and 1 <= artifact["fold_index"] <= 5 and type(artifact["evaluation_starts_at"]) is str
          and type(artifact["training_rows"]) is int and type(artifact["training_events"]) is int
          and 0 < artifact["training_events"] <= artifact["training_rows"], "invalid_EH_sequence_reference_metadata")
    try:
        cutoff = datetime.fromisoformat(artifact["evaluation_starts_at"])
    except ValueError as error:
        raise InputRefusal("invalid_EH_sequence_reference_split_clock") from error
    _need(_aware_minute(cutoff), "invalid_EH_sequence_reference_split_clock")

    with_peers = artifact["with_peers"]
    shapes = {"head": (25, 10), "bias": (10,)}
    for name, width, dilations in _TOWERS:
        channels = 7 if with_peers and name == "minute" else 4
        shapes[f"{name}_projection"] = (channels, width)
        for index in range(len(dilations)):
            shapes[f"{name}_kernel_{index}"] = (3, width, width)
    values = artifact["parameters"]
    _need(type(values) is dict and set(values) == set(shapes)
          and all(_numeric_block(values[name], shape, list) for name, shape in shapes.items()),
          "invalid_EH_sequence_reference_coefficients")
    return cutoff, with_peers, {name: np.array(values[name], dtype=np.float64) for name in shapes}


def _query(row, cutoff, with_peers, seen):
    _need(type(row) is SequenceInputs, "target_free_EH_sequence_reference_input_required")
    at = _coordinate(row.forecast_id)
    _need(at >= cutoff and row.forecast_id not in seen, "EH_sequence_reference_before_split_or_duplicate")
    seen.add(row.forecast_id)
    _need(_numeric_block(row.minute, (20, 4), tuple) and _numeric_block(row.coarse, (4, 4), tuple)
          and _numeric_block(row.static, (7,), tuple), "invalid_EH_sequence_reference_input_shape_or_values")
    _, _, remaining, departed, retested, breadth, stage = row.static
    _need(0 < remaining <= 1 and departed in (0, 1) and retested in (0, 1) and retested <= departed
          and 0 <= breadth <= 1 and stage in (0, 1), "invalid_EH_sequence_reference_state")
    if with_peers:
        _need(_numeric_block(row.peer, (20, 3), tuple) and all(-1 <= step[2] <= 1 for step in row.peer),
              "common_EH_sequence_reference_peer_support_required")


def _tower(np, rows, coefficients, name, dilations):
    state = np.einsum("btc,cw->btw", rows, coefficients[f"{name}_projection"])
    _need(np.isfinite(state).all(), "nonfinite_EH_sequence_reference_activation")
    state = state.clip(min=0)
    for index, dilation in enumerate(dilations):
        pad = 2 * dilation
        history = np.pad(state, ((0, 0), (pad, 0), (0, 0)))
        offsets = pad + np.arange(state.shape[1])[:, None] - dilation * np.arange(3)
        windows = np.take(history, offsets, axis=1)
        kernel = coefficients[f"{name}_kernel_{index}"]
        state = state + np.einsum("btli,lio->bto", windows, kernel, optimize=True)
        _need(np.isfinite(state).all(), "nonfinite_EH_sequence_reference_activation")
        state = state.clip(min=0)
    return state[:, -1]


def _probability_matrix_valid(np, values):
    return (np.isfinite(values).all() and (values >= 0).all() and (values <= 1).all()
            and (np.abs(values.sum(axis=1) - 1) <= MASS_TOL).all())


def _replayed_batches(artifact, queries):
    import numpy as np

    _need(np.__version__ == _NUMPY_VERSION and type(queries) is tuple, "invalid_EH_sequence_reference_runtime_or_roster")
    cutoff, with_peers, coefficients = _artifact(np, artifact)
    seen = set()
    for start in range(0, len(queries), _BATCH):
        block = queries[start:start + _BATCH]
        for row in block:
            _query(row, cutoff, with_peers, seen)
        minute = np.array([row.minute for row in block], dtype=np.float64)
        coarse = np.array([row.coarse for row in block], dtype=np.float64)
        static = np.array([row.static for row in block], dtype=np.float64)
        if with_peers:
            minute = np.concatenate((minute, np.array([row.peer for row in block], dtype=np.float64)), axis=2)
        with np.errstate(over="ignore", invalid="ignore"):
            fine, slow = (_tower(np, rows, coefficients, name, dilations)
                          for rows, (name, _, dilations) in zip((minute, coarse), _TOWERS, strict=True))
            state = np.column_stack((fine, slow, static))
            logits = np.einsum("bi,ij->bj", state, coefficients["head"]) + coefficients["bias"]
            _need(np.isfinite(logits).all(), "nonfinite_EH_sequence_reference_logits")
            shifted = logits - logits.max(axis=1, keepdims=True)
            probabilities = np.exp(shifted - np.logaddexp.reduce(shifted, axis=1, keepdims=True))
        _need(_probability_matrix_valid(np, probabilities), "invalid_EH_sequence_reference_probabilities")
        yield start, block, probabilities


def reference_sequence_probabilities(artifact: dict, queries: tuple[SequenceInputs, ...]) -> tuple[tuple[float, ...], ...]:
    """Replay both causal towers and the joint head in query order."""
    return tuple(tuple(row) for _, _, values in _replayed_batches(artifact, queries) for row in values.tolist())


def verify_sequence_probabilities(artifact: dict, queries: tuple[SequenceInputs, ...], saved: tuple) -> dict:
    """Verify every saved (forecast_id, probability tuple) in the supplied query order."""
    import numpy as np

    _need(type(saved) is tuple and type(queries) is tuple and len(saved) == len(queries),
          "EH_sequence_reference_prediction_count_differs")
    maximum = 0.0
    for start, block, expected in _replayed_batches(artifact, queries):
        rows = saved[start:start + len(block)]
        for entry, query in zip(rows, block, strict=True):
            _need(type(entry) is tuple and len(entry) == 2, "invalid_EH_sequence_reference_saved_row")
            identity, probabilities = entry
            _coordinate(identity)
            _need(identity == query.forecast_id, "EH_sequence_reference_prediction_coordinate_differs")
            _need(_numeric_block(probabilities, (len(OUTCOMES),), tuple), "invalid_EH_sequence_reference_saved_values")
        actual = np.array([entry[1] for entry in rows], dtype=np.float64)
        _need(_probability_matrix_valid(np, actual), "invalid_EH_sequence_reference_saved_mass")
        error = np.abs(actual - expected)
        _need((error <= ABS_TOL + REL_TOL * np.abs(expected)).all(), "EH_sequence_reference_probability_differs")
        maximum = max(maximum, float(error.max()))
    return {"verified": True, "rows": len(saved), "maximum_absolute_difference": maximum}
