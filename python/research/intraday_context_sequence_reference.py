"""Independent, bounded inference replay for frozen EI sequence coefficients.

Only target-free data classes and schema metadata are shared with the input
adapter. No producer validation, convolution or prediction helper is called.
Digests detect changed content, not source authenticity; the registered caller
still owns evidence authentication. Forecast agreement is not trading profit.
"""

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from hashlib import sha256
import json
from math import isfinite

from research.intraday_context_model_inputs import PreparedInput, PreparedQueries
from research.intraday_context_features import COMMON_NAMES, CONTEXT_NAMES, MINUTE_NAMES
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal


ABS_TOL, REL_TOL, MASS_TOL = 1e-12, 1e-10, 1e-12
_BATCH = 128
_NUMPY = "2.5.2"
_OUTCOMES = [[path, horizon] for horizon in (5, 15, 30) for path in ("P1", "P2", "P3")] + [["P4", 30]]


def _need(condition, reason):
    if not condition:
        raise InputRefusal(f"EI_sequence_reference_{reason}")


def _encode(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError, OverflowError) as error:
        raise InputRefusal("EI_sequence_reference_invalid_json") from error


def _clock(value):
    _need(type(value) is datetime and value.utcoffset() is not None
          and value.second == value.microsecond == 0, "invalid_clock")
    return value.astimezone(UTC).isoformat()


def _fraction(value):
    return [value.numerator, value.denominator]


def _metadata(mode, cutoff, fold):
    _need(type(mode) is str and mode in ("chart", "chart_context"), "invalid_mode")
    _need(type(fold) is int and 1 <= fold <= 5, "invalid_fold")
    names = tuple(f"minute_{index:02d}_{name}" for index in range(30) for name in MINUTE_NAMES)
    names += COMMON_NAMES + (CONTEXT_NAMES if mode == "chart_context" else ())
    return {"schema": "ei-common-model-inputs-draft-v1", "mode": mode,
            "cutoff_at": _clock(cutoff), "fold": fold, "feature_names": list(names),
            "transform": "asinh_each_binary64_feature_v1",
            "weight_boundary": "exact_fraction_then_common_ieee754_binary32_v1", "outcome_order": _OUTCOMES}


def _coordinate(identity, at):
    _clock(at)
    _need(type(identity) is tuple and len(identity) == 3 and type(identity[1]) is str
          and identity[1] in ("approach", "breakout") and type(identity[0]) is tuple and len(identity[0]) == 2
          and type(identity[0][0]) is tuple and len(identity[0][0]) == 2
          and type(identity[0][0][0]) is str and bool(identity[0][0][0])
          and type(identity[0][0][1]) is ResistanceAnchor and type(identity[0][1]) is int
          and identity[0][1] >= 0 and (identity[0][1] > 0 or identity[1] == "breakout")
          and type(identity[2]) is tuple and len(identity[2]) == 6, "invalid_coordinate")
    (symbol, anchor), episode = identity[0]
    start, level, band, target, stop, end = identity[2]
    for value in (anchor.start_at, anchor.peak_end, anchor.confirmed_at, start, end):
        _clock(value)
    prices = (anchor.start_price, anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band, level, band, target, stop)
    _need(all(type(value) is Fraction and value > 0 for value in prices)
          and anchor.start_at < anchor.peak_end <= anchor.confirmed_at <= start == at
          and end - start == timedelta(minutes=30) and level == anchor.price and band == anchor.band
          and anchor.advance == anchor.price - anchor.start_price
          and 0 < stop <= level - band < level + band < target, "invalid_question_geometry")
    return [symbol, [_clock(anchor.start_at), _fraction(anchor.start_price), _clock(anchor.peak_end),
                    _clock(anchor.confirmed_at), *map(_fraction, (anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band))],
            episode, identity[1], [_clock(start), *map(_fraction, (level, band, target, stop)), _clock(end)]]


def _numeric(value, shape, container, scalar_types=(float, int)):
    if shape:
        return type(value) is container and len(value) == shape[0] and all(
            _numeric(item, shape[1:], container, scalar_types) for item in value)
    if type(value) not in scalar_types:
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _queries(queries):
    _need(type(queries) is PreparedQueries and type(queries.rows) is tuple, "target_free_queries_required")
    metadata = _metadata(queries.mode, queries.cutoff_at, queries.fold)
    _need(type(queries.feature_names) is tuple and queries.feature_names == tuple(metadata["feature_names"]),
          "feature_order_differs")
    # Stream the canonical JSON row array rather than materialize another full
    # input roster. Field and row order exactly match the public input contract.
    digest, seen = sha256(), set()
    digest.update(b"{")
    for index, key in enumerate(sorted((*metadata, "rows"))):
        if index:
            digest.update(b",")
        digest.update(_encode(key) + b":")
        if key != "rows":
            digest.update(_encode(metadata[key]))
            continue
        digest.update(b"[")
        for number, row in enumerate(queries.rows):
            _need(type(row) is PreparedInput, "prepared_row_required")
            coordinate = _coordinate(row.question_id, row.forecast_at)
            _need(row.forecast_at >= queries.cutoff_at and row.question_id not in seen, "before_cutoff_or_duplicate")
            seen.add(row.question_id)
            width = 17 if queries.mode == "chart_context" else 7
            _need(_numeric(row.minute, (30, 7), tuple, (float,))
                  and _numeric(row.static, (width,), tuple, (float,)), "query_shape_or_values_differ")
            if number:
                digest.update(b",")
            digest.update(_encode([coordinate, row.minute, row.static]))
        digest.update(b"]")
    digest.update(b"}")
    _need(type(queries.input_sha256) is str and queries.input_sha256 == digest.hexdigest(), "query_digest_differs")
    return metadata


def _training(value, metadata):
    hashes = {"candidate_input_sha256", "roster_sha256", "weights_sha256", "outcomes_sha256", "input_sha256"}
    counts = ("candidate_rows", "supported_rows", "event_count", "date_count", "weighted_stage_groups")
    fields = {*metadata, *hashes, *counts, "unavailable_by_reason", "event_count_unit", "max_weight_rounding_error"}
    _need(type(value) is dict and set(value) == fields, "training_fields_differ")
    _need(_encode({key: value[key] for key in metadata}) == _encode(metadata), "training_metadata_differs")
    _need(all(type(value[key]) is str and len(value[key]) == 64
              and all(char in "0123456789abcdef" for char in value[key]) for key in hashes), "training_hashes_differ")
    missing = value["unavailable_by_reason"]
    _need(type(missing) is list and all(type(item) is list and len(item) == 2 and type(item[0]) is str
          and bool(item[0].strip()) and type(item[1]) is int and item[1] > 0 for item in missing)
          and missing == sorted(missing) and len({item[0] for item in missing}) == len(missing), "training_missingness_differs")
    _need(all(type(value[key]) is int and value[key] >= 0 for key in counts)
          and value["candidate_rows"] == value["supported_rows"] + sum(item[1] for item in missing)
          and 0 < value["date_count"] <= value["event_count"] <= value["supported_rows"]
          and value["event_count"] <= value["weighted_stage_groups"] <= min(2 * value["event_count"], value["supported_rows"])
          and value["event_count_unit"] == "symbol_session_not_trade_or_independent_trial", "training_counts_differ")
    error = value["max_weight_rounding_error"]
    _need(type(error) is list and len(error) == 2 and all(type(item) is int for item in error)
          and error[1] > 0 and 0 <= Fraction(*error) <= Fraction(1, 2**24)
          and _fraction(Fraction(*error)) == error, "training_weight_boundary_differs")


def _artifact(np, artifact, metadata):
    _need(type(artifact) is dict and set(artifact) == {
        "schema", "numpy", "architecture", "optimization", "training", "outcomes", "parameters", "artifact_sha256",
    } and artifact["schema"] == "ei-context-sequence-v1" and artifact["numpy"] == _NUMPY, "artifact_schema_differs")
    _training(artifact["training"], metadata)
    width = 17 if metadata["mode"] == "chart_context" else 7
    architecture = {"minute_shape": [30, 7], "projection_width": 12, "kernel_size": 3, "dilations": [1, 2, 4, 8],
                    "activation": "relu", "residual": True, "readout": "last_state_then_static", "static_width": width,
                    "output_width": 10, "output_activation": "joint_softmax", "coarse_input": False}
    optimization = {"epochs": 6, "batch_size": 64, "learning_rate": 0.005, "weight_decay": 0.01,
                    "optimizer": "adam_decoupled_weight_decay", "beta1": 0.9, "beta2": 0.999, "epsilon": 1e-8,
                    "global_gradient_norm_clip": 1.0, "parameter_seed": 20261108 + metadata["fold"],
                    "shuffle_seed": 20261118 + metadata["fold"],
                    "weight_normalization": "shared_binary32_weights_divided_by_global_mean_and_batch_size",
                    "initialization": "shared_chart_head_then_zero_context_head_v1", "dtype": "float64",
                    "early_stopping": False, "parameter_search": False}
    _need(_encode(artifact["architecture"]) == _encode(architecture)
          and _encode(artifact["optimization"]) == _encode(optimization)
          and _encode(artifact["outcomes"]) == _encode(_OUTCOMES), "architecture_or_optimization_differs")
    shapes = {"minute_projection": (7, 12), **{f"minute_kernel_{index}": (3, 12, 12) for index in range(4)},
              "head": (12 + width, 10), "bias": (10,)}
    values = artifact["parameters"]
    _need(type(values) is dict and set(values) == set(shapes)
          and all(_numeric(values[name], shape, list) for name, shape in shapes.items()), "coefficient_shape_or_values_differ")
    body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    _need(type(artifact["artifact_sha256"]) is str
          and artifact["artifact_sha256"] == sha256(_encode(body)).hexdigest(), "artifact_digest_differs")
    return {name: np.asarray(values[name], dtype=np.float64) for name in shapes}


def _causal_states(np, minute, coefficients):
    state = np.einsum("btc,cw->btw", minute, coefficients["minute_projection"])
    _need(np.isfinite(state).all(), "nonfinite_activation")
    state = np.maximum(state, 0)
    for index, dilation in enumerate((1, 2, 4, 8)):
        padded = np.pad(state, ((0, 0), (2 * dilation, 0), (0, 0)))
        offsets = 2 * dilation + np.arange(state.shape[1])[:, None] - dilation * np.arange(3)
        windows = np.take(padded, offsets, axis=1)
        state = state + np.einsum("btli,lio->bto", windows, coefficients[f"minute_kernel_{index}"], optimize=True)
        _need(np.isfinite(state).all(), "nonfinite_activation")
        state = np.maximum(state, 0)
    return state


def _probabilities_valid(np, values):
    return (np.isfinite(values).all() and (values >= 0).all() and (values <= 1).all()
            and (np.abs(values.sum(axis=1) - 1) <= MASS_TOL).all())


def _replayed_batches(artifact, queries):
    import numpy as np

    _need(np.__version__ == _NUMPY, "pinned_numpy_required")
    metadata = _queries(queries)
    coefficients = _artifact(np, artifact, metadata)
    for start in range(0, len(queries.rows), _BATCH):
        rows = queries.rows[start:start + _BATCH]
        minute = np.array([row.minute for row in rows], dtype=np.float64)
        static = np.array([row.static for row in rows], dtype=np.float64)
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                last = _causal_states(np, minute, coefficients)[:, -1]
                state = np.column_stack((last, static))
                logits = np.einsum("bi,ij->bj", state, coefficients["head"]) + coefficients["bias"]
                _need(np.isfinite(logits).all(), "nonfinite_logits")
                shifted = logits - logits.max(axis=1, keepdims=True)
                scores = np.exp(shifted - np.logaddexp.reduce(shifted, axis=1, keepdims=True))
        except FloatingPointError as error:
            raise InputRefusal("EI_sequence_reference_nonfinite_prediction") from error
        _need(_probabilities_valid(np, scores), "invalid_probability_mass")
        yield start, rows, scores


def reference_sequence_probabilities(artifact: dict, queries: PreparedQueries) -> tuple[tuple[float, ...], ...]:
    """Replay frozen coefficients independently, preserving every query's order."""
    return tuple(tuple(row) for _, _, values in _replayed_batches(artifact, queries) for row in values.tolist())


def verify_sequence_probabilities(artifact: dict, queries: PreparedQueries, saved: tuple) -> dict:
    """Verify all saved (question_id, probabilities) pairs and their exact order."""
    import numpy as np

    _need(type(queries) is PreparedQueries and type(queries.rows) is tuple and type(saved) is tuple
          and len(saved) == len(queries.rows), "prediction_count_differs")
    maximum = 0.0
    for start, rows, expected in _replayed_batches(artifact, queries):
        entries = saved[start:start + len(rows)]
        for entry, row in zip(entries, rows, strict=True):
            _need(type(entry) is tuple and len(entry) == 2, "saved_row_differs")
            _coordinate(entry[0], row.forecast_at)
            _need(entry[0] == row.question_id, "saved_coordinate_differs")
            _need(_numeric(entry[1], (10,), tuple), "saved_values_differ")
        actual = np.array([entry[1] for entry in entries], dtype=np.float64)
        _need(_probabilities_valid(np, actual), "saved_probability_mass_differs")
        error = np.abs(actual - expected)
        _need((error <= ABS_TOL + REL_TOL * np.abs(expected)).all(), "saved_probability_differs")
        maximum = max(maximum, float(error.max()))
    return {"verified": True, "rows": len(saved), "maximum_absolute_difference": maximum,
            "artifact_sha256": artifact["artifact_sha256"], "query_input_sha256": queries.input_sha256,
            "query_ids": tuple(row.question_id for row in queries.rows)}
