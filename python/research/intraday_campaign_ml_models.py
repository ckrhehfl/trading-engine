"""Fixed EC numerical adapters; no market IO, split selection or evaluation labels.

Numerical libraries load only on an explicit fit/predict call in the worker.
Finite binary64 values are hashed in row order as big-endian IEEE754 bytes;
serialized ridge numbers use Python's exact ``float.hex()`` representation.
Verification tolerances are absolute 1e-12 / relative 1e-10, with NO tolerance
on the strict-positive admission sign. A failed fit/replay is never retried.
The caller authenticates source/runtime binary pins in ``binding``; this pure
adapter records that binding but cannot authenticate external source bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
import struct
import sys
from types import MappingProxyType


FEATURE_NAMES = (
    "setup_is_A", "fraction_of_session_elapsed", "target_open_to_signal_return",
    "target_last15_return", "structural_stop_gap_from_signal_close",
    "trigger_close_location", "peer_open_to_signal_return", "peer_last15_return",
    "peer_first30_return", "common_peer_count_divided_by_original_peer_count",
)
ABS_TOL = 1e-12
REL_TOL = 1e-10
THREAD_VARIABLES = (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
)
NUMERICAL_CONTRACT = MappingProxyType({
    "input_encoding": "shape-u64be/row-major-ieee754-binary64be-v1",
    "model_float_encoding": "finite-canonical-python-float.hex-v1",
    "absolute_tolerance": ABS_TOL, "relative_tolerance": REL_TOL,
    "residual_bound": "abs+rel*(abs(rhs)+sum(abs(lhs_terms)))",
    "ridge_prediction_verification": "serialized and independently rederived training-transform scalar replays",
    "strict_positive_sign_tolerance": 0,
})
RIDGE_PARAMETERS = MappingProxyType({
    "alpha": 1, "objective": "SSE+alpha*L2", "fit_intercept": True,
    "intercept_penalty": 0, "standardization_ddof": 0,
    "constant_rule": "all training float64 values exactly equal",
    "solver": "numpy.linalg.solve centered normal equations",
})
LIGHTGBM_PARAMETERS = MappingProxyType({
    "objective": "regression_l2", "num_iterations": 100,
    "learning_rate": 0.05, "max_depth": 3, "num_leaves": 7,
    "min_data_in_leaf": 20, "lambda_l2": 1, "feature_fraction": 1,
    "bagging_fraction": 1, "bagging_freq": 0, "num_threads": 1,
    "seed": 20261029, "deterministic": True, "device_type": "cpu",
    "force_col_wise": True, "verbosity": -1,
})

# Full native Config::ToString closure, fixed from the 4.6.0 sources below,
# not learned from a fitted model. Empty values denote empty vectors/strings.
# https://github.com/microsoft/LightGBM/blob/v4.6.0/include/LightGBM/config.h
# https://github.com/microsoft/LightGBM/blob/v4.6.0/src/io/config_auto.cpp
# https://github.com/microsoft/LightGBM/blob/v4.6.0/src/io/config.cpp
_NATIVE_DEFAULT_TEXT = """
boosting=gbdt
objective=regression
metric=l2
tree_learner=serial
device_type=cpu
data_sample_strategy=bagging
data=
valid=
num_iterations=100
learning_rate=0.05
num_leaves=7
num_threads=1
seed=20261029
deterministic=1
force_col_wise=1
force_row_wise=0
histogram_pool_size=-1
max_depth=3
min_data_in_leaf=20
min_sum_hessian_in_leaf=0.001
bagging_fraction=1
pos_bagging_fraction=1
neg_bagging_fraction=1
bagging_freq=0
bagging_by_query=0
feature_fraction=1
feature_fraction_bynode=1
extra_trees=0
early_stopping_round=0
early_stopping_min_delta=0
first_metric_only=0
max_delta_step=0
lambda_l1=0
lambda_l2=1
linear_lambda=0
min_gain_to_split=0
drop_rate=0.1
max_drop=50
skip_drop=0.5
xgboost_dart_mode=0
uniform_drop=0
top_rate=0.2
other_rate=0.1
min_data_per_group=100
max_cat_threshold=32
cat_l2=10
cat_smooth=10
max_cat_to_onehot=4
top_k=20
monotone_constraints=
monotone_constraints_method=basic
monotone_penalty=0
feature_contri=
forcedsplits_filename=
refit_decay_rate=0.9
cegb_tradeoff=1
cegb_penalty_split=0
cegb_penalty_feature_lazy=
cegb_penalty_feature_coupled=
path_smooth=0
interaction_constraints=
verbosity=-1
saved_feature_importance_type=0
use_quantized_grad=0
num_grad_quant_bins=4
quant_train_renew_leaf=0
stochastic_rounding=1
linear_tree=0
max_bin=255
max_bin_by_feature=
min_data_in_bin=3
bin_construct_sample_cnt=200000
is_enable_sparse=1
enable_bundle=1
use_missing=1
zero_as_missing=0
feature_pre_filter=1
pre_partition=0
two_round=0
header=0
label_column=
weight_column=
group_column=
ignore_column=
categorical_feature=
forcedbins_filename=
precise_float_parser=0
parser_config_file=
num_class=1
is_unbalance=0
scale_pos_weight=1
sigmoid=1
boost_from_average=1
reg_sqrt=0
alpha=0.9
fair_c=1
poisson_max_delta_step=0.7
tweedie_variance_power=1.5
lambdarank_truncation_level=30
lambdarank_norm=1
label_gain=
lambdarank_position_bias_regularization=0
eval_at=
multi_error_top_k=1
auc_mu_weights=
num_machines=1
local_listen_port=12400
time_out=120
machine_list_filename=
machines=
gpu_platform_id=-1
gpu_device_id=-1
gpu_use_dp=0
num_gpu=1
"""


def lightgbm_parameter_manifest() -> dict:
    """Return a fresh, predeclared invocation/default closure for registration."""
    native = dict(line.split("=", 1) for line in _NATIVE_DEFAULT_TEXT.splitlines() if line)
    state = LIGHTGBM_PARAMETERS["seed"]
    # Config::Set's six NextShort(0, INT16_MAX) calls, in their source order.
    for name in ("data_random_seed", "bagging_seed", "drop_seed",
                 "feature_fraction_seed", "objective_seed", "extra_seed"):
        state = (214013 * state + 2531011) & 0xffffffff
        native[name] = str(((state >> 16) & 0x7fff) % 32767)
    return {
        "requested": dict(LIGHTGBM_PARAMETERS), "native_effective": native,
        "python_api": {"num_boost_round": 100, "valid_sets": None,
                       "callbacks": None, "init_model": None,
                       "keep_training_booster": True, "categorical_feature": [],
                       "dataset_reference": None, "weights": None, "init_score": None,
                       "matrix_input_dtype": "float64", "native_label_storage_dtype": "float32"},
        "prediction": {"start_iteration": 0, "num_iteration": -1,
                       "raw_score": False, "pred_leaf": False, "pred_contrib": False,
                       "num_threads": 1, "pred_early_stop": False,
                       "pred_early_stop_freq": 10, "pred_early_stop_margin": 10,
                       "predict_disable_shape_check": False},
        "trusted_external_boundary": "pinned LightGBM training and native serialized prediction internals",
    }


def _canonical(value) -> bytes:
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if isinstance(item, Mapping) and all(type(key) is str for key in item):
            return {key: check(child) for key, child in item.items()}
        if type(item) in (tuple, list):
            return [check(child) for child in item]
        raise ValueError("binding/artifact must contain finite JSON values only")
    return json.dumps(check(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _copy(value):
    return json.loads(_canonical(value))


def _feature_order(names: Sequence[str]) -> None:
    if tuple(names) != FEATURE_NAMES:
        raise ValueError("fixed ten-feature order mismatch")


def _number(value) -> float:
    if isinstance(value, (str, bytes, bool)):
        raise ValueError("numeric finite float64 value required")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("numeric finite float64 value required") from exc
    if not math.isfinite(result):
        raise ValueError("nonfinite float64 value")
    return result


def _rows(X: Sequence[Sequence[float]], *, training=False) -> tuple:
    rows = tuple(tuple(_number(value) for value in row) for row in X)
    if any(len(row) != len(FEATURE_NAMES) for row in rows) or (training and not rows):
        raise ValueError("nonempty training matrix / ten columns required")
    return rows


def _vector(y: Sequence[float], count: int) -> tuple:
    values = tuple(_number(value) for value in y)
    if len(values) != count:
        raise ValueError("label/prediction row count mismatch")
    return values


def _matrix_hash(rows: tuple) -> str:
    digest = hashlib.sha256(struct.pack(">QQ", len(rows), len(FEATURE_NAMES)))
    for row in rows:
        digest.update(struct.pack(">10d", *row))
    return digest.hexdigest()


def _vector_hash(values: tuple) -> str:
    digest = hashlib.sha256(struct.pack(">Q", len(values)))
    for value in values:
        digest.update(struct.pack(">d", value))
    return digest.hexdigest()


def _libraries(learner: str):
    if learner not in ("ridge", "lightgbm"):
        raise ValueError("only fixed ridge/lightgbm learners are supported")
    if any(os.environ.get(name) != "1" for name in THREAD_VARIABLES):
        raise ValueError("single-thread worker environment required before numerical import")
    import numpy as np
    if np.__version__ != "2.5.2" or sys.version_info[:2] != (3, 12):
        raise ValueError("pinned Python3.12/NumPy2.5.2 runtime required")
    lgb = None
    versions = {"python": sys.version, "numpy": np.__version__,
                "threads": {name: os.environ[name] for name in THREAD_VARIABLES}}
    if learner == "lightgbm":
        import lightgbm as lgb
        import scipy
        if lgb.__version__ != "4.6.0" or scipy.__version__ != "1.18.1":
            raise ValueError("pinned LightGBM4.6.0/Scipy1.18.1 runtime required")
        versions.update(lightgbm=lgb.__version__, scipy=scipy.__version__)
    return np, lgb, versions


def _hex(value) -> str:
    return _number(value).hex()


def _unhex(value) -> float:
    if type(value) is not str:
        raise ValueError("canonical finite hex float required")
    try:
        result = float.fromhex(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("canonical finite hex float required") from exc
    if not math.isfinite(result) or result.hex() != value:
        raise ValueError("canonical finite hex float required")
    return result


def _native_parameters(model: str) -> dict:
    if model.count("\nparameters:\n") != 1 or model.count("\nend of parameters\n") != 1:
        raise ValueError("invalid native parameter block")
    block = model.split("\nparameters:\n", 1)[1].split("\nend of parameters\n", 1)[0]
    params = {}
    for line in block.splitlines():
        if not line.startswith("[") or not line.endswith("]") or ": " not in line:
            raise ValueError("invalid native parameter entry")
        key, value = line[1:-1].split(": ", 1)
        if key in params:
            raise ValueError("duplicate native parameter")
        params[key] = value
    if params != lightgbm_parameter_manifest()["native_effective"]:
        raise ValueError("native effective LightGBM parameter/default mismatch")
    return params


def _parameters(learner: str) -> dict:
    return dict(RIDGE_PARAMETERS) if learner == "ridge" else lightgbm_parameter_manifest()


def fit_model(learner: str, X: Sequence[Sequence[float]], y: Sequence[float], *,
              feature_names: Sequence[str], binding: Mapping) -> dict:
    """Fit once on the supplied accepted training view; no evaluation input."""
    _feature_order(feature_names)
    rows = _rows(X, training=True)
    labels = _vector(y, len(rows))
    if not isinstance(binding, Mapping) or not binding:
        raise ValueError("nonempty caller scientific/runtime binding required")
    bound = _copy(binding)
    np, lgb, runtime = _libraries(learner)
    matrix = np.array(rows, dtype=np.float64, order="C")
    target = np.array(labels, dtype=np.float64)
    if learner == "ridge":
        with np.errstate(all="raise"):
            constant = np.all(matrix == matrix[0], axis=0)
            means = np.mean(matrix, axis=0)
            means[constant] = matrix[0, constant]
            scales = np.std(matrix, axis=0, ddof=0)
            scales[constant] = 0.0
            if np.any(scales[~constant] <= 0):
                raise ValueError("nonconstant feature has unrepresentable float64 scale")
            z = (matrix[:, ~constant] - means[~constant]) / scales[~constant]
            intercept = np.mean(target)
            coefficients = np.linalg.solve(z.T @ z + np.eye(z.shape[1]),
                                           z.T @ (target - intercept))
        model = {"means": [_hex(value) for value in means],
                 "scales": [_hex(value) for value in scales],
                 "constant_mask": [bool(value) for value in constant],
                 "intercept": _hex(intercept),
                 "coefficients": [_hex(value) for value in coefficients]}
    else:
        # Native LightGBM stores labels as float32 even for float64 input y.
        # Bind both representations rather than claim exact float64 internals.
        try:
            with np.errstate(all="raise"):
                stored_labels = tuple(float(value) for value in np.array(labels, dtype=np.float32))
        except FloatingPointError as exc:
            raise ValueError("nonfinite native float32 training label") from exc
        dataset = lgb.Dataset(matrix, label=target, feature_name=list(FEATURE_NAMES),
                              categorical_feature=[], params=dict(LIGHTGBM_PARAMETERS))
        dataset.construct()
        if _vector(dataset.get_label(), len(labels)) != stored_labels:
            raise ValueError("native training label storage mismatch")
        booster = lgb.train(dict(LIGHTGBM_PARAMETERS), dataset,
                            num_boost_round=100, keep_training_booster=True)
        model_string = booster.model_to_string(num_iteration=-1)
        _native_parameters(model_string)
        _canonical(booster.dump_model())  # Reject nonfinite native model numbers.
        model = {"model_string": model_string,
                 "model_string_sha256": hashlib.sha256(model_string.encode("utf-8")).hexdigest(),
                 "current_iteration": booster.current_iteration(),
                 "num_trees": booster.num_trees(), "num_model_per_iteration": 1,
                 "native_training_labels_sha256": _vector_hash(stored_labels)}
    artifact = {"schema": "ec-fixed-ml-model-v1", "learner": learner,
                "feature_names": list(FEATURE_NAMES), "binding": bound,
                "binding_sha256": _digest(bound), "training_rows": len(rows),
                "train_features_sha256": _matrix_hash(rows),
                "train_labels_sha256": _vector_hash(labels),
                "parameters": _parameters(learner), "runtime": runtime,
                "numerical_contract": dict(NUMERICAL_CONTRACT), "model": model}
    artifact["artifact_sha256"] = _digest(artifact)
    return artifact


def _check_artifact(artifact: Mapping, feature_names: Sequence[str]):
    _feature_order(feature_names)
    required = {"schema", "learner", "feature_names", "binding", "binding_sha256",
                "training_rows", "train_features_sha256", "train_labels_sha256",
                "parameters", "runtime", "numerical_contract", "model", "artifact_sha256"}
    if not isinstance(artifact, Mapping) or set(artifact) != required:
        raise ValueError("invalid model artifact schema")
    if artifact["artifact_sha256"] != _digest({key: value for key, value in artifact.items()
                                             if key != "artifact_sha256"}):
        raise ValueError("model artifact hash mismatch")
    if (artifact["schema"] != "ec-fixed-ml-model-v1"
            or artifact["feature_names"] != list(FEATURE_NAMES)
            or _canonical(artifact["numerical_contract"]) != _canonical(NUMERICAL_CONTRACT)
            or type(artifact["training_rows"]) is not int or artifact["training_rows"] <= 0
            or artifact["binding_sha256"] != _digest(artifact["binding"])):
        raise ValueError("model scientific contract mismatch")
    np, lgb, runtime = _libraries(artifact["learner"])
    if _canonical(artifact["parameters"]) != _canonical(_parameters(artifact["learner"])):
        raise ValueError("fixed learner parameter mismatch")
    if artifact["runtime"] != runtime:
        raise ValueError("model numerical runtime mismatch")
    return np, lgb


def _ridge_values(model: Mapping):
    if set(model) != {"means", "scales", "constant_mask", "intercept", "coefficients"}:
        raise ValueError("invalid ridge serialization")
    means = tuple(map(_unhex, model["means"]))
    scales = tuple(map(_unhex, model["scales"]))
    mask = tuple(model["constant_mask"])
    coefficients = tuple(map(_unhex, model["coefficients"]))
    if (len(means) != 10 or len(scales) != 10 or len(mask) != 10
            or any(type(value) is not bool for value in mask)
            or len(coefficients) != 10 - sum(mask)
            or any(scale != 0 if dropped else scale <= 0
                   for scale, dropped in zip(scales, mask, strict=True))):
        raise ValueError("invalid ridge transform dimensions/constant mask")
    return means, scales, mask, _unhex(model["intercept"]), coefficients


def _reload_booster(model: Mapping, lgb):
    if set(model) != {"model_string", "model_string_sha256", "current_iteration",
                      "num_trees", "num_model_per_iteration", "native_training_labels_sha256"}:
        raise ValueError("invalid LightGBM serialization")
    serialized = model["model_string"]
    if (type(serialized) is not str
            or hashlib.sha256(serialized.encode("utf-8")).hexdigest() != model["model_string_sha256"]):
        raise ValueError("native model string hash mismatch")
    _native_parameters(serialized)
    booster = lgb.Booster(model_str=serialized, params={"num_threads": 1})
    _canonical(booster.dump_model())
    if (booster.feature_name() != list(FEATURE_NAMES)
            or type(model["current_iteration"]) is not int
            or not 1 <= model["current_iteration"] <= 100
            or model["current_iteration"] != booster.current_iteration()
            or type(model["num_trees"]) is not int
            or model["num_trees"] != booster.num_trees()
            or type(model["num_model_per_iteration"]) is not int
            or model["num_model_per_iteration"] != 1
            or booster.num_model_per_iteration() != 1
            or booster.num_trees() != booster.current_iteration()):
        raise ValueError("native model feature/tree/iteration mismatch")
    return booster


def predict_model(artifact: Mapping, X: Sequence[Sequence[float]], *,
                  feature_names: Sequence[str]) -> tuple[float, ...]:
    """Reload serialized parameters and predict; the interface has no y input."""
    np, lgb = _check_artifact(artifact, feature_names)
    rows = _rows(X)
    if artifact["learner"] == "ridge":
        means, scales, mask, intercept, coefficients = _ridge_values(artifact["model"])
        active = [index for index, dropped in enumerate(mask) if not dropped]
        with np.errstate(all="raise"):
            matrix = np.array(rows, dtype=np.float64).reshape((-1, 10))
            z = (matrix[:, active] - np.array(means)[active]) / np.array(scales)[active]
            predictions = tuple(intercept + z @ np.array(coefficients, dtype=np.float64))
    else:
        booster = _reload_booster(artifact["model"], lgb)
        predictions = () if not rows else tuple(booster.predict(
            np.array(rows, dtype=np.float64, order="C"), start_iteration=0,
            num_iteration=-1, raw_score=False, pred_leaf=False, pred_contrib=False,
            num_threads=1, pred_early_stop=False, pred_early_stop_freq=10,
            pred_early_stop_margin=10, predict_disable_shape_check=False))
    return _vector(predictions, len(rows))


def _close(actual: float, expected: float, what: str) -> None:
    if not math.isclose(actual, expected, abs_tol=ABS_TOL, rel_tol=REL_TOL):
        raise ValueError(f"independent ridge {what} mismatch")


def verify_model(artifact: Mapping, X_train: Sequence[Sequence[float]],
                 y_train: Sequence[float], X_test: Sequence[Sequence[float]],
                 predictions: Sequence[float], *, feature_names: Sequence[str],
                 binding: Mapping) -> dict:
    """Reconstruct ridge equations or replay pinned native model; never refit.

    The caller independently derives membership and exact raw scientific values.
    This function accepts training y only and has no evaluation-label argument.
    """
    _check_artifact(artifact, feature_names)
    train = _rows(X_train, training=True)
    labels = _vector(y_train, len(train))
    test = _rows(X_test)
    supplied = _vector(predictions, len(test))
    if (_copy(binding) != artifact["binding"] or _digest(binding) != artifact["binding_sha256"]
            or artifact["training_rows"] != len(train)
            or artifact["train_features_sha256"] != _matrix_hash(train)
            or artifact["train_labels_sha256"] != _vector_hash(labels)):
        raise ValueError("training rows/labels/order or caller binding mismatch")
    residuals = []
    if artifact["learner"] == "ridge":
        means, scales, mask, intercept, coefficients = _ridge_values(artifact["model"])
        active = []
        independent_means, independent_scales = [], []
        for index in range(10):
            values = tuple(row[index] for row in train)
            constant = all(value == values[0] for value in values)
            if constant != mask[index]:
                raise ValueError("independent ridge constant mask mismatch")
            mean = values[0] if constant else math.fsum(values) / len(values)
            scale = 0.0 if constant else math.sqrt(
                math.fsum((value - mean) ** 2 for value in values) / len(values))
            _close(means[index], mean, "training mean")
            _close(scales[index], scale, "training population scale")
            independent_means.append(mean)
            independent_scales.append(scale)
            if not constant:
                if scale <= 0:
                    raise ValueError("nonconstant feature has unrepresentable scale")
                active.append(index)
        center = math.fsum(labels) / len(labels)
        _close(intercept, center, "unpenalized intercept")
        # Separate scalar reconstruction; no NumPy solver or second fit call.
        z = tuple(tuple((row[index] - independent_means[index]) / independent_scales[index]
                        for index in active) for row in train)
        for col, coefficient in enumerate(coefficients):
            rhs = math.fsum(row[col] * (label - center)
                            for row, label in zip(z, labels, strict=True))
            terms = [coefficient]
            terms.extend(other * math.fsum(row[col] * row[j] for row in z)
                         for j, other in enumerate(coefficients))
            residual = math.fsum(terms) - rhs
            bound = ABS_TOL + REL_TOL * (abs(rhs) + math.fsum(abs(term) for term in terms))
            if not math.isfinite(residual) or abs(residual) > bound:
                raise ValueError("independent ridge normal-equation residual mismatch")
            residuals.append(_hex(residual))
        # Scalar replay of the serialized transform is separate from producer @.
        replay = tuple(_number(intercept + math.fsum(
            (row[index] - means[index]) / scales[index] * coefficient
            for index, coefficient in zip(active, coefficients, strict=True))) for row in test)
        independent_replay = tuple(_number(center + math.fsum(
            (row[index] - independent_means[index]) / independent_scales[index] * coefficient
            for index, coefficient in zip(active, coefficients, strict=True))) for row in test)
    else:
        try:
            stored_labels = tuple(struct.unpack(">f", struct.pack(">f", value))[0] for value in labels)
        except (OverflowError, struct.error) as exc:
            raise ValueError("nonfinite native float32 training label") from exc
        if artifact["model"]["native_training_labels_sha256"] != _vector_hash(stored_labels):
            raise ValueError("independent native float32 training labels mismatch")
        replay = predict_model(artifact, test, feature_names=feature_names)
        independent_replay = replay  # Native internals remain the declared trusted boundary.
    for actual, serialized, independent in zip(supplied, replay, independent_replay, strict=True):
        for expected in (serialized, independent):
            if (actual > 0) != (expected > 0):
                raise ValueError("strict-positive prediction admission sign mismatch")
            if not math.isclose(actual, expected, abs_tol=ABS_TOL, rel_tol=REL_TOL):
                raise ValueError("serialized/independent model prediction replay mismatch")
    return {"schema": "ec-fixed-ml-verification-v1", "verified": True,
            "artifact_sha256": artifact["artifact_sha256"],
            "train_features_sha256": _matrix_hash(train), "train_labels_sha256": _vector_hash(labels),
            "test_features_sha256": _matrix_hash(test), "predictions_sha256": _vector_hash(supplied),
            "replay_predictions_sha256": _vector_hash(replay),
            "independent_predictions_sha256": _vector_hash(independent_replay),
            "prediction_count": len(test), "strict_positive_signs_agree": True,
            "ridge_normal_equation_residual_hex": residuals,
            "trusted_external_boundary": ("NumPy fit solve; independent scalar normal-equation residual"
                                          if artifact["learner"] == "ridge" else
                                          lightgbm_parameter_manifest()["trusted_external_boundary"])}
