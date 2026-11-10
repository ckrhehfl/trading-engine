"""Fixed EK numerical learners; no files, market readers or execution authority.

Queries cannot contain targets. The caller owns causal features, original scope,
label meaning, source authentication and the closed CPU/address-space budget.
All training transformations and target-specific date/stock weights are sealed.
"""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from fractions import Fraction
from hashlib import sha256
import json
from math import fsum, isfinite, sqrt
import os
import struct


SCHEMA = "ek-recovery-model-v1"
NUMPY_VERSION = "2.5.2"
LGB_VERSIONS = {"numpy": NUMPY_VERSION, "scipy": "1.18.1", "lightgbm": "4.6.0"}
MODES = ("chart", "chart_peer", "order_removed")
KINDS = ("empirical", "lightgbm", "sequence")
PATH_CLASSES = ("target_66bp_first", "structural_invalidation_first", "timeout")
THREAD_VARIABLES = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")
STEPS, CHART_CHANNELS, PEER_CHANNELS = 60, 10, 3
WIDTH, EPOCHS, BATCH_SIZE, SEED = 12, 6, 64, 20261110
DILATIONS = (1, 2, 4, 8, 16)
LEARNING_RATE, WEIGHT_DECAY = 0.005, 0.01
LGB_ROUNDS = 40
LGB_PARAMETERS = {
    "boosting": "gbdt", "metric": "None", "num_leaves": 7, "max_depth": 3,
    "min_data_in_leaf": 20, "learning_rate": 0.05, "lambda_l2": 1.0,
    "num_threads": 1, "deterministic": True, "force_col_wise": True,
    "bagging_fraction": 1.0, "bagging_freq": 0, "feature_fraction": 1.0,
    "feature_fraction_bynode": 1.0, "verbosity": -1, "device_type": "cpu",
    "use_missing": False, "zero_as_missing": False,
}
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_NAMES = ("projection", *(f"kernel_{index}" for index in range(len(DILATIONS))))


@dataclass(frozen=True)
class ModelRow:
    key: str
    session: str
    minute: tuple[tuple[float, ...], ...]
    static: tuple[float, ...]
    target_gross_pct: float | None
    path_class: int | None
    mature_session: str
    peer: tuple[tuple[float, ...], ...] | None = None


@dataclass(frozen=True)
class ModelQuery:
    key: str
    session: str
    minute: tuple[tuple[float, ...], ...]
    static: tuple[float, ...]
    peer: tuple[tuple[float, ...], ...] | None = None


@dataclass(frozen=True)
class PreparedTraining:
    rows: tuple[ModelRow, ...]
    stocks: tuple[str, ...]
    gross_weights: tuple[float, ...]
    path_weights: tuple[float, ...]
    contract: dict
    input_sha256: str


@dataclass(frozen=True)
class PreparedQueries:
    rows: tuple[ModelQuery, ...]
    contract: dict
    input_sha256: str


@dataclass(frozen=True)
class ModelPrediction:
    key: str
    gross_pct: float | None
    probabilities: tuple[float, float, float] | None


def _need(condition, reason):
    if not condition:
        raise ValueError("EK_model_" + reason)


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _hash(value) -> str:
    return sha256(encode(value)).hexdigest()


def _day(value):
    _need(type(value) is str and date.fromisoformat(value).isoformat() == value, "canonical_session_required")


def _pin(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _vector(values, width):
    return (type(values) is tuple and len(values) == width
            and all(type(value) is float and isfinite(value) for value in values))


def _row(row, mode, static_width, *, training):
    _need(type(row) is (ModelRow if training else ModelQuery), "typed_target_separated_row_required")
    _need(type(row.key) is str and bool(row.key.strip()), "row_key_required")
    _day(row.session)
    _need(type(row.minute) is tuple and len(row.minute) == STEPS
          and all(_vector(values, CHART_CHANNELS) for values in row.minute)
          and _vector(row.static, static_width), "finite_feature_geometry_required")
    if mode == "chart_peer":
        _need(type(row.peer) is tuple and len(row.peer) == STEPS
              and all(_vector(values, PEER_CHANNELS) for values in row.peer), "complete_peer_required")
    else:
        _need(row.peer is None, "chart_row_contains_peer")
    if training:
        _day(row.mature_session)
        _need(row.mature_session >= row.session, "maturity_before_observation")
        _need(row.target_gross_pct is None or type(row.target_gross_pct) is float
              and isfinite(row.target_gross_pct), "finite_gross_target_required")
        _need(row.path_class is None or type(row.path_class) is int and 0 <= row.path_class < 3,
              "path_class_required")


def _minute(row, mode):
    values = row.minute
    if mode == "order_removed":
        order = sorted(range(STEPS - 1), key=lambda index: sha256(
            f"EK-order-v1\0{row.key}\0{index}".encode("utf-8")).digest())
        values = tuple(values[index] for index in order) + values[-1:]
    if mode == "chart_peer":
        values = tuple(left + right for left, right in zip(values, row.peer, strict=True))
    return values


def _weights(rows, stocks, supported):
    counts = Counter((row.session, stock) for row, stock, known in zip(rows, stocks, supported, strict=True) if known)
    dates = Counter(day for day, _ in counts)
    exact = tuple(Fraction(1, len(dates) * dates[row.session] * counts[row.session, stock]) if known else Fraction()
                  for row, stock, known in zip(rows, stocks, supported, strict=True))
    rounded = tuple(float(struct.unpack("f", struct.pack("f", float(value)))[0]) for value in exact)
    return exact, rounded


def _gross(row):
    if row.target_gross_pct is None:
        return None
    try:
        value = float(struct.unpack("f", struct.pack("f", row.target_gross_pct))[0])
    except (OverflowError, struct.error) as error:
        raise ValueError("EK_model_gross_target_outside_binary32") from error
    _need(isfinite(value), "gross_target_outside_binary32")
    return value


def _scaling(rows, stocks, mode, static_width):
    _, weights = _weights(rows, stocks, (True,) * len(rows))
    channels = CHART_CHANNELS + (PEER_CHANNELS if mode == "chart_peer" else 0)
    if not rows:
        return {"mean": [0.0] * (channels + static_width), "scale": [1.0] * (channels + static_width)}
    sequences = tuple(_minute(row, mode) for row in rows)
    total = fsum(weights)
    means, scales = [], []
    for column in range(channels + static_width):
        def values():
            for row, sequence, weight in zip(rows, sequences, weights, strict=True):
                if column < channels:
                    for minute in sequence:
                        yield minute[column], weight / STEPS
                else:
                    yield row.static[column - channels], weight
        mean = fsum(value * weight for value, weight in values()) / total
        variance = fsum(weight * (value - mean) ** 2 for value, weight in values()) / total
        _need(isfinite(mean) and isfinite(variance), "scaling_overflow")
        means.append(mean)
        scales.append(sqrt(variance) if variance > 0 else 1.0)
    return {"mean": means, "scale": scales}


def _metadata(*, mode, fold, cutoff_session, static_names, provenance, target_kind):
    _need(mode in MODES and type(fold) is int and 1 <= fold <= 5, "fixed_mode_and_fold_required")
    _need(target_kind in ("opening", "holding_remaining"), "fixed_target_kind_required")
    _day(cutoff_session)
    _need(type(static_names) is tuple and all(type(name) is str and name for name in static_names)
          and len(set(static_names)) == len(static_names), "unique_static_names_required")
    _need(type(provenance) is dict and set(provenance) == {
        "source_sha256", "runtime_sha256", "registration_sha256", "features_sha256", "labels_sha256",
    } and all(_pin(value) for value in provenance.values()), "complete_provenance_required")
    return {"schema": SCHEMA, "mode": mode, "fold": fold, "cutoff_session": cutoff_session,
            "static_names": list(static_names), "provenance": dict(provenance), "path_classes": list(PATH_CLASSES),
            "target_kind": target_kind,
            "target_definition": ("opening_executable_structural60_gross_pct_and_frozen_limit_66bp_close_first"
                if target_kind == "opening" else "current_held_state_gross_to_original_structural60_expiry_no_path_head"),
            "gross_numeric_boundary": "common_ieee754_binary32_targets_before_any_learner",
            "weighting": "equal_dates_then_stocks_then_supported_rows_binary32_per_target",
            "scaling_rule": "training_only_channel_and_static_weighted_population_standardization",
            "order_control": "deterministic_prefix59_permutation_latest_and_static_unchanged"}


def prepare_training(rows, *, stock_by_key, fold, cutoff_session, mode, static_names, provenance,
                     target_kind="opening") -> PreparedTraining:
    """Seal past-only rows; unknown targets retain their rows and separate masks."""
    metadata = _metadata(mode=mode, fold=fold, cutoff_session=cutoff_session,
                         static_names=static_names, provenance=provenance, target_kind=target_kind)
    rows = tuple(rows)
    _need(type(stock_by_key) is dict and set(stock_by_key) == {row.key for row in rows}
          and all(type(stock) is str and stock for stock in stock_by_key.values()), "exact_stock_roster_required")
    _need(len({row.key for row in rows}) == len(rows), "duplicate_training_key")
    for row in rows:
        _row(row, mode, len(static_names), training=True)
        _need(row.session < cutoff_session and row.mature_session < cutoff_session, "training_not_mature_before_split")
        _need(target_kind == "opening" or row.path_class is None, "holding_target_cannot_reuse_initial_path")
    stocks = tuple(stock_by_key[row.key] for row in rows)
    gross_exact, gross = _weights(rows, stocks, tuple(row.target_gross_pct is not None for row in rows))
    path_exact, path = _weights(rows, stocks, tuple(row.path_class is not None for row in rows))
    contract = {**metadata, "scaling": _scaling(rows, stocks, mode, len(static_names)),
        "candidate_rows": len(rows), "gross_rows": sum(value > 0 for value in gross),
        "path_rows": sum(value > 0 for value in path), "date_count": len({row.session for row in rows}),
        "stock_dates": len(set(zip((row.session for row in rows), stocks, strict=True))),
        "roster_sha256": _hash([(row.key, row.session, stock) for row, stock in zip(rows, stocks, strict=True)]),
        "targets_sha256": _hash([(row.target_gross_pct, _gross(row), row.path_class, row.mature_session) for row in rows]),
        "weights_sha256": _hash([[str(value) for value in gross_exact], [str(value) for value in path_exact], gross, path])}
    body = {"rows": [asdict(row) for row in rows], "stocks": stocks,
            "gross_weights": gross, "path_weights": path, "contract": contract}
    return PreparedTraining(rows, stocks, gross, path, contract, _hash(body))


def validate_training(prepared):
    _need(type(prepared) is PreparedTraining, "prepared_training_required")
    c = prepared.contract
    rebuilt = prepare_training(prepared.rows, stock_by_key=dict(zip((row.key for row in prepared.rows), prepared.stocks,
        strict=True)), fold=c["fold"], cutoff_session=c["cutoff_session"], mode=c["mode"],
        static_names=tuple(c["static_names"]), provenance=c["provenance"], target_kind=c["target_kind"])
    _need(rebuilt == prepared, "training_contract_or_payload_changed")


def _contract(contract):
    _need(type(contract) is dict, "training_contract_required")
    metadata = _metadata(mode=contract["mode"], fold=contract["fold"], cutoff_session=contract["cutoff_session"],
        static_names=tuple(contract["static_names"]), provenance=contract["provenance"], target_kind=contract["target_kind"])
    extra = {"scaling", "candidate_rows", "gross_rows", "path_rows", "date_count", "stock_dates",
             "roster_sha256", "targets_sha256", "weights_sha256", "training_input_sha256"}
    _need(set(contract) == set(metadata) | extra and all(contract[key] == value for key, value in metadata.items()),
          "training_metadata_changed")
    _need(all(_pin(contract[name]) for name in ("roster_sha256", "targets_sha256", "weights_sha256", "training_input_sha256")),
          "training_hash_required")
    counts = [contract[name] for name in ("candidate_rows", "gross_rows", "path_rows", "date_count", "stock_dates")]
    _need(all(type(value) is int and value >= 0 for value in counts)
          and max(counts[1:]) <= counts[0] and counts[3] <= counts[4], "training_counts_differ")
    _need(contract["target_kind"] == "opening" or contract["path_rows"] == 0,
          "holding_contract_contains_initial_path")
    width = CHART_CHANNELS + (PEER_CHANNELS if contract["mode"] == "chart_peer" else 0) + len(contract["static_names"])
    scaling = contract["scaling"]
    _need(type(scaling) is dict and set(scaling) == {"mean", "scale"}
          and all(type(scaling[name]) is list and len(scaling[name]) == width
                  and all(type(value) is float and isfinite(value) for value in scaling[name]) for name in scaling)
          and all(value > 0 for value in scaling["scale"]), "scaling_contract_differs")


def training_contract(prepared):
    validate_training(prepared)
    return {**json.loads(encode(prepared.contract)), "training_input_sha256": prepared.input_sha256}


def prepare_queries(rows, contract: dict) -> PreparedQueries:
    """Bind target-free queries to an already sealed training transformation."""
    _contract(contract)
    rows = tuple(rows)
    _need(len({row.key for row in rows}) == len(rows), "duplicate_query_key")
    for row in rows:
        _row(row, contract["mode"], len(contract["static_names"]), training=False)
        _need(row.session >= contract["cutoff_session"], "query_before_split")
    copied = json.loads(encode(contract))
    return PreparedQueries(rows, copied, _hash({"rows": [asdict(row) for row in rows], "contract": copied}))


def validate_queries(queries):
    _need(type(queries) is PreparedQueries and prepare_queries(queries.rows, queries.contract) == queries,
          "query_contract_or_payload_changed")


def _values(row, contract):
    minute = _minute(row, contract["mode"])
    channels = len(minute[0])
    means, scales = contract["scaling"]["mean"], contract["scaling"]["scale"]
    normalized = tuple(tuple((value - means[index]) / scales[index] for index, value in enumerate(values))
                       for values in minute)
    static = tuple((value - means[channels + index]) / scales[channels + index]
                   for index, value in enumerate(row.static))
    _need(all(isfinite(value) for values in normalized for value in values) and all(map(isfinite, static)),
          "transformed_features_not_finite")
    return normalized, static


def _numpy():
    _need(all(os.environ.get(name) == "1" for name in THREAD_VARIABLES), "single_native_thread_environment_required")
    import numpy as np
    _need(np.__version__ == NUMPY_VERSION, "pinned_numpy_required")
    return np


def _libraries():
    np = _numpy()
    import scipy
    import lightgbm as lgb
    _need({"numpy": np.__version__, "scipy": scipy.__version__, "lightgbm": lgb.__version__} == LGB_VERSIONS,
          "pinned_lightgbm_runtime_required")
    return np, lgb


def _feature_names(contract):
    channels = CHART_CHANNELS + (PEER_CHANNELS if contract["mode"] == "chart_peer" else 0)
    return [f"minute_{at:02d}_channel_{channel:02d}" for at in range(STEPS) for channel in range(channels)] + [
        f"static_{index:03d}_{name}" for index, name in enumerate(contract["static_names"])]


def _flat(row, contract):
    minute, static = _values(row, contract)
    return tuple(value for point in minute for value in point) + static


def _lgb_parameters(fold, head):
    result = {**LGB_PARAMETERS, "seed": SEED + fold,
              "objective": "regression_l2" if head == "gross" else "multiclass"}
    if head == "path":
        result["num_class"] = 3
    return result


def _fit_lightgbm(prepared, contract):
    np, lgb = _libraries()
    models = {}
    for head, weights in (("gross", prepared.gross_weights), ("path", prepared.path_weights)):
        selected = [(row, weight) for row, weight in zip(prepared.rows, weights, strict=True) if weight > 0]
        if not selected:
            models[head] = None
            continue
        rows, selected_weights = zip(*selected, strict=True)
        parameters = _lgb_parameters(contract["fold"], head)
        target = [_gross(row) if head == "gross" else row.path_class for row in rows]
        data = lgb.Dataset(np.asarray([_flat(row, contract) for row in rows], dtype=np.float64),
            label=target, weight=np.asarray(selected_weights, dtype=np.float32),
            feature_name=_feature_names(contract), params=parameters)
        data.construct()
        stored_weights = data.get_field("weight")
        _need(stored_weights is not None and tuple(map(float, stored_weights)) == selected_weights
              or stored_weights is None and all(value == 1.0 for value in selected_weights), "native_weights_differ")
        stored_targets = tuple(map(float, data.get_field("label")))
        _need(stored_targets == tuple(map(float, target)), "native_targets_differ")
        booster = lgb.train(parameters, data, num_boost_round=LGB_ROUNDS)
        text = booster.model_to_string()
        restored = lgb.Booster(model_str=text, params={"num_threads": 1})
        models[head] = {"parameters": parameters, "iterations": restored.current_iteration(), "model_text": text,
            "dump": restored.dump_model(), "model_sha256": sha256(text.encode()).hexdigest(),
            "native_training": {"rows": len(rows), "keys_sha256": _hash([row.key for row in rows]),
                "targets_sha256": _hash(stored_targets), "weights_sha256": _hash(selected_weights)}}
    return models


def _sequence_manifest(contract):
    return {"steps": STEPS, "channels": CHART_CHANNELS + (PEER_CHANNELS if contract["mode"] == "chart_peer" else 0),
        "static_width": len(contract["static_names"]), "width": WIDTH, "dilations": list(DILATIONS), "kernel": 3,
        "readout": "last_state_then_static", "outputs": "gross_regression_and_three_class_softmax",
        "epochs": EPOCHS, "batch_size": BATCH_SIZE, "seed": SEED + contract["fold"],
        "shuffle_seed": SEED + 10 + contract["fold"], "learning_rate": LEARNING_RATE,
        "weight_decay": WEIGHT_DECAY, "gradient_clip": 1.0, "dtype": "float64",
        "optimizer": "adam_decoupled_weight_decay", "early_stopping": False, "parameter_search": False}


def _initialize(np, contract):
    rng = np.random.default_rng(SEED + contract["fold"])
    projection = rng.normal(0, sqrt(2 / CHART_CHANNELS), (CHART_CHANNELS, WIDTH))
    if contract["mode"] == "chart_peer":
        projection = np.vstack((projection, np.zeros((PEER_CHANNELS, WIDTH))))
    parameters = {"projection": projection}
    for name in _NAMES[1:]:
        parameters[name] = rng.normal(0, sqrt(2 / WIDTH) * 0.25, (3, WIDTH, WIDTH))
    parameters["head"] = rng.normal(0, sqrt(2 / (WIDTH + len(contract["static_names"]))),
                                    (WIDTH + len(contract["static_names"]), 4))
    parameters["bias"] = np.zeros(4)
    return parameters


def _batch(np, rows, contract):
    values = [_values(row, contract) for row in rows]
    return (np.asarray([minute for minute, _ in values], dtype=np.float64),
            np.asarray([static for _, static in values], dtype=np.float64))


def _forward(np, batch, parameters):
    from research.intraday_scenario_sequence import _tower
    states = _tower(np, batch[0], parameters, _NAMES, DILATIONS)
    joined = np.concatenate((states[-1][:, -1], batch[1]), axis=1)
    output = joined @ parameters["head"] + parameters["bias"]
    _need(np.isfinite(output).all(), "nonfinite_sequence_output")
    logits = output[:, 1:]
    weights = np.exp(logits - logits.max(axis=1, keepdims=True))
    return output[:, 0], weights / weights.sum(axis=1, keepdims=True), joined, states


def _gradients(np, batch, parameters, gross, paths, gross_weights, path_weights):
    from research.intraday_scenario_sequence import _tower_backward
    estimates, probabilities, joined, states = _forward(np, batch, parameters)
    probabilities[np.arange(len(paths)), paths] -= 1
    delta = np.column_stack((2 * (estimates - gross) * gross_weights, probabilities * path_weights[:, None]))
    return {"head": joined.T @ delta, "bias": delta.sum(axis=0),
            **_tower_backward(np, batch[0], states, (delta @ parameters["head"].T)[:, :WIDTH],
                              parameters, _NAMES, DILATIONS)}


def _fit_sequence(prepared, contract):
    np = _numpy()
    parameters = _initialize(np, contract)
    first = {name: np.zeros_like(value) for name, value in parameters.items()}
    second = {name: np.zeros_like(value) for name, value in parameters.items()}
    shuffle = np.random.default_rng(SEED + 10 + contract["fold"])
    size, step = len(prepared.rows), 0
    totals = fsum(prepared.gross_weights), fsum(prepared.path_weights)
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        for _ in range(EPOCHS):
            order = shuffle.permutation(size)
            for start in range(0, size, BATCH_SIZE):
                indices = order[start:start + BATCH_SIZE]
                rows = tuple(prepared.rows[index] for index in indices)
                target = np.asarray([0.0 if row.target_gross_pct is None else _gross(row) for row in rows])
                paths = np.asarray([0 if row.path_class is None else row.path_class for row in rows])
                weights = [np.asarray([values[index] for index in indices]) * size / (total * len(indices))
                           if total else np.zeros(len(indices)) for values, total in zip(
                               (prepared.gross_weights, prepared.path_weights), totals, strict=True)]
                gradients = _gradients(np, _batch(np, rows, contract), parameters, target, paths, *weights)
                norm = sqrt(fsum(float(np.sum(value ** 2)) for value in gradients.values()))
                _need(isfinite(norm), "nonfinite_sequence_gradient")
                step += 1
                for name, value in parameters.items():
                    gradient = gradients[name] / max(1.0, norm)
                    first[name] = 0.9 * first[name] + 0.1 * gradient
                    second[name] = 0.999 * second[name] + 0.001 * gradient ** 2
                    value *= 1 - LEARNING_RATE * WEIGHT_DECAY
                    value -= LEARNING_RATE * (first[name] / (1 - 0.9 ** step)) / (
                        np.sqrt(second[name] / (1 - 0.999 ** step)) + 1e-8)
    _need(all(np.isfinite(value).all() for value in parameters.values()), "nonfinite_sequence_parameters")
    return {name: value.tolist() for name, value in parameters.items()}


def fit_model(kind: str, prepared: PreparedTraining) -> dict:
    """Fit one fixed learner; unsupported heads remain explicit in every prediction."""
    _need(kind in KINDS, "fixed_learner_required")
    contract = training_contract(prepared)
    support = contract["gross_rows"] > 0 or contract["path_rows"] > 0
    if kind == "empirical":
        gross_total, path_total = fsum(prepared.gross_weights), fsum(prepared.path_weights)
        gross = (fsum(_gross(row) * weight for row, weight in zip(prepared.rows, prepared.gross_weights,
                    strict=True) if weight) / gross_total if gross_total else None)
        # Equal-date weights sum to one; multiply by supported dates for the prior's effective mass.
        dates = len({row.session for row in prepared.rows if row.path_class is not None})
        counts = [fsum(weight for row, weight in zip(prepared.rows, prepared.path_weights, strict=True)
                       if row.path_class == label) * dates / path_total if path_total else 0.0 for label in range(3)]
        payload = {"gross_pct": gross, "probabilities": [(value + 0.5) / (dates + 1.5) for value in counts]
                   if path_total else None, "prior_per_class": 0.5, "effective_path_dates": dates}
        runtime = {}
    elif kind == "lightgbm":
        payload = _fit_lightgbm(prepared, contract) if support else {"gross": None, "path": None}
        runtime = dict(LGB_VERSIONS)
    else:
        payload = {"architecture": _sequence_manifest(contract),
                   "parameters": _fit_sequence(prepared, contract) if support else None}
        runtime = {"numpy": NUMPY_VERSION}
    body = {"schema": SCHEMA, "kind": kind, "training": contract, "runtime": runtime, "payload": payload}
    artifact = {**body, "artifact_sha256": _hash(body)}
    _need(len(encode(artifact)) <= MAX_ARTIFACT_BYTES, "artifact_size_exceeded")
    _artifact(artifact)
    return artifact


def _artifact(artifact):
    _need(type(artifact) is dict and set(artifact) == {
        "schema", "kind", "training", "runtime", "payload", "artifact_sha256",
    } and artifact["schema"] == SCHEMA and artifact["kind"] in KINDS, "artifact_schema_differs")
    _contract(artifact["training"])
    _need(artifact["artifact_sha256"] == _hash({key: value for key, value in artifact.items() if key != "artifact_sha256"})
          and len(encode(artifact)) <= MAX_ARTIFACT_BYTES, "artifact_digest_or_size_differs")
    expected = {} if artifact["kind"] == "empirical" else (LGB_VERSIONS if artifact["kind"] == "lightgbm"
                                                           else {"numpy": NUMPY_VERSION})
    _need(artifact["runtime"] == expected, "artifact_runtime_differs")
    from research.intraday_recovery_models_reference import validate_artifact
    validate_artifact(artifact)


def predict_model(artifact: dict, queries: PreparedQueries) -> tuple[ModelPrediction, ...]:
    """Predict without targets and verify numerical output through separate replay."""
    _artifact(artifact)
    validate_queries(queries)
    _need(artifact["training"] == queries.contract, "query_training_identity_differs")
    contract, payload = queries.contract, artifact["payload"]
    gross, probabilities = [None] * len(queries.rows), [None] * len(queries.rows)
    if artifact["kind"] == "empirical":
        gross = [payload["gross_pct"]] * len(queries.rows)
        probabilities = [None if payload["probabilities"] is None else tuple(payload["probabilities"])] * len(queries.rows)
    elif artifact["kind"] == "lightgbm" and queries.rows and (contract["gross_rows"] or contract["path_rows"]):
        np, lgb = _libraries()
        matrix = np.asarray([_flat(row, contract) for row in queries.rows], dtype=np.float64)
        for head in ("gross", "path"):
            model = payload[head]
            if model is None:
                continue
            _need(model["parameters"] == _lgb_parameters(contract["fold"], head)
                  and sha256(model["model_text"].encode()).hexdigest() == model["model_sha256"], "lightgbm_model_changed")
            booster = lgb.Booster(model_str=model["model_text"], params={"num_threads": 1})
            _need(booster.current_iteration() == model["iterations"] and 1 <= model["iterations"] <= LGB_ROUNDS
                  and encode(booster.dump_model()) == encode(model["dump"]), "lightgbm_reload_differs")
            values = booster.predict(matrix, num_threads=1, start_iteration=0, num_iteration=-1, pred_early_stop=False)
            if head == "gross":
                gross = tuple(map(float, values))
            else:
                probabilities = tuple(tuple(map(float, row)) for row in values)
    elif artifact["kind"] == "sequence" and queries.rows and payload["parameters"] is not None:
        np = _numpy()
        _need(payload["architecture"] == _sequence_manifest(contract), "sequence_architecture_differs")
        parameters = {name: np.asarray(value, dtype=np.float64) for name, value in payload["parameters"].items()}
        for start in range(0, len(queries.rows), BATCH_SIZE):
            estimates, paths, _, _ = _forward(np, _batch(np, queries.rows[start:start + BATCH_SIZE], contract), parameters)
            for index, (estimate, path) in enumerate(zip(estimates, paths, strict=True), start):
                gross[index] = float(estimate) if contract["gross_rows"] else None
                probabilities[index] = tuple(map(float, path)) if contract["path_rows"] else None
    result = tuple(ModelPrediction(row.key, estimate, probability) for row, estimate, probability in zip(
        queries.rows, gross, probabilities, strict=True))
    from research.intraday_recovery_models_reference import verify_predictions
    verify_predictions(artifact, queries, result)
    return result


def dump_model(artifact: dict) -> bytes:
    _artifact(artifact)
    return encode(artifact)


def load_model(raw: bytes, *, expected_sha256: str) -> dict:
    _need(type(raw) is bytes and len(raw) <= MAX_ARTIFACT_BYTES and _pin(expected_sha256)
          and sha256(raw).hexdigest() == expected_sha256, "saved_model_external_pin_differs")
    artifact = json.loads(raw)
    _need(encode(artifact) == raw, "saved_model_not_canonical")
    _artifact(artifact)
    return artifact
