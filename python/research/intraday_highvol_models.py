"""EG model numerics over caller-authenticated past-only rows.

The registered runner owns release, data scope, maturity and fit-count gates.
Prediction and independent coefficient replay consume no evaluation outcomes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from math import exp, fsum, isclose, isfinite
from os import environ
from statistics import pstdev
from struct import pack

from research import intraday_campaign_ml_models as numeric
from research.intraday_chart_tcn import BATCH_SIZE, EPOCHS, SEED, build_tcn
from research.intraday_chart_tcn_artifact import ABS_TOL, REL_TOL, _dot_tower
from research.intraday_highvol_inputs import CHART_CHANNELS, MINUTE_SLOTS, PEER_CHANNELS, REQUIRED_MINUTES


SCHEMA = "eg-fixed-model-v1"
ARMS = ("LGB_CHART", "TCN_CHART", "TCN_CHART_PEER")
TASKS = ("gross30_pct", "target_first", "hold_advantage_pct")
STATES = ("holding", "remaining_over_30", "close_vs_stop_pct", "close_vs_target_pct")
DILATIONS = (1, 2, 4, 8, 16)
SUMMARY_WINDOWS = (5, 15, 30, 60)
SUMMARY_METRICS = ("return_pct", "span_pct", "body_mean_pct", "body_pstd_pct",
                   "price_fraction", "preopening_fraction", "volume_fraction", "volume_log_mean")
LGB_NAMES = (tuple(f"minute{window}__{name}" for window in SUMMARY_WINDOWS for name in SUMMARY_METRICS)
             + STATES + tuple(f"task__{task}" for task in TASKS))
TASK_TAGS = tuple(tuple(float(i == j) for j in range(len(TASKS))) for i in range(len(TASKS)))
OPTIMIZER = {"lr": 0.001, "weight_decay": 0.01, "beta1": 0.9, "beta2": 0.999, "eps": 1e-8,
             "foreach": False, "fused": False, "capturable": False, "amsgrad": False, "maximize": False}


@dataclass(frozen=True)
class ModelInput:
    minute: tuple[tuple[float, ...], ...]
    state: tuple[float, float, float, float]


@dataclass(frozen=True)
class TrainingRow:
    input: ModelInput
    gross30: Fraction | None = None
    target_first: bool | None = None
    hold_advantage: Fraction | None = None


@dataclass(frozen=True)
class ModelPrediction:
    gross30_pct: float | None
    target_first_probability: float | None
    hold_advantage_pct: float | None
    target_first_raw: float | None
    target_first_clipped: bool


def decision_state(*, holding: bool, remaining_minutes: int, close: Fraction,
                   stop: Fraction, target: Fraction) -> tuple[float, float, float, float]:
    """Use completed decision close and already-known structure levels."""
    if (type(holding) is not bool or type(remaining_minutes) is not int
            or remaining_minutes not in (range(5, 30, 5) if holding else (30,))
            or any(type(value) is not Fraction or value <= 0 for value in (close, stop, target))
            or stop >= target):
        raise ValueError("invalid EG causal decision state")
    return (float(holding), remaining_minutes / 30, numeric._number(100 * (close / stop - 1)),
            numeric._number(100 * (close / target - 1)))


def _channels(arm: str) -> tuple[str, ...]:
    if arm not in ARMS:
        raise ValueError("unregistered EG model arm")
    return CHART_CHANNELS + ((*PEER_CHANNELS[:-1], "peer_preopening") if arm == "TCN_CHART_PEER" else ())


def _eligible(row: ModelInput) -> tuple[int, ...]:
    return (2,) if row.state[0] else (0, 1)


def _validate_input(row: ModelInput, width: int) -> None:
    if (type(row) is not ModelInput or len(row.state) != len(STATES)
            or len(row.minute) != MINUTE_SLOTS):
        raise ValueError("invalid EG model geometry")
    holding, remaining, stop, target = row.state
    if (any(type(x) is not float or not isfinite(x) for x in row.state)
            or holding not in (0, 1)
            or remaining not in (tuple(step / 30 for step in range(5, 30, 5)) if holding else (1,))
            or not -100 < target < stop):
        raise ValueError("invalid EG model state")
    for offset, point in enumerate(row.minute):
        if len(point) != width or any(type(x) is not float or not isfinite(x) for x in point):
            raise ValueError("invalid EG minute values")
        volume, observed, preopening = point[9:12]
        recent = offset >= MINUTE_SLOTS - REQUIRED_MINUTES
        if (any(mask not in (0, 1) for mask in (volume, observed, preopening))
                or (preopening and (observed or volume)) or (recent and not observed)
                or (not observed and any(point[:8])) or (not volume and point[8])):
            raise ValueError("invalid EG minute masks")
        if width > len(CHART_CHANNELS):
            _, _, positive, coverage, supported, peer_preopening = point[len(CHART_CHANNELS):]
            if (not 0 <= positive <= 1 or not 0 <= coverage <= 1
                    or supported not in (0, 1) or peer_preopening != preopening
                    or (preopening and (supported or coverage)) or (recent and not supported)
                    or (not supported and any(point[len(CHART_CHANNELS):len(CHART_CHANNELS) + 3]))):
                raise ValueError("invalid EG peer masks")


def _label_values(row: TrainingRow) -> tuple[float | None, ...]:
    if (row.gross30 is not None and type(row.gross30) is not Fraction
            or row.target_first is not None and type(row.target_first) is not bool
            or row.hold_advantage is not None and type(row.hold_advantage) is not Fraction):
        raise ValueError("EG labels require exact returns and adjudicated boolean outcomes")
    exact = (row.gross30, row.target_first, row.hold_advantage)
    if any(value is not None and index not in _eligible(row.input) for index, value in enumerate(exact)):
        raise ValueError("EG label task disagrees with decision state")
    return (None if row.gross30 is None else numeric._number(row.gross30 * 100),
            None if row.target_first is None else float(row.target_first),
            None if row.hold_advantage is None else numeric._number(row.hold_advantage * 100))


def _features_digest(inputs: Sequence[ModelInput], width: int) -> str:
    count = MINUTE_SLOTS * width + len(STATES)
    hashed = sha256(pack(">QQ", len(inputs), count))
    for row in inputs:
        flattened = tuple(value for point in row.minute for value in point)
        hashed.update(pack(f">{count}d", *flattened, *row.state))
    return hashed.hexdigest()


def _training(arm: str, rows: Sequence[TrainingRow], fold_index: int):
    width = len(_channels(arm))
    if type(fold_index) is not int or fold_index not in range(1, 6) or not rows:
        raise ValueError("fixed EG fold and nonempty training required")
    for row in rows:
        if type(row) is not TrainingRow:
            raise ValueError("invalid EG training row")
        _validate_input(row.input, width)
    inputs = tuple(row.input for row in rows)
    labels = tuple(map(_label_values, rows))
    class_counts = [sum(row[1] == value for row in labels) for value in (0.0, 1.0)]
    target_support = "both_classes" if all(class_counts) else "one_class" if any(class_counts) else "no_labels"
    if target_support != "both_classes":
        labels = tuple((gross, None, holding) for gross, _, holding in labels)
    counts = [sum(row[index] is not None for row in labels) for index in range(len(TASKS))]
    if not any(counts):
        raise ValueError("EG training has no supported task")
    # Retain exact values so two distinct fractions rounded alike still differ.
    def rational(value):
        return None if value is None else [value.numerator, value.denominator]

    truth = [(rational(row.gross30), row.target_first, rational(row.hold_advantage)) for row in rows]
    manifest = {"fold_index": fold_index, "rows": len(rows), "task_rows": counts,
                "target_first_false_true_rows": class_counts, "target_first_support": target_support,
                "inputs_sha256": _features_digest(inputs, width), "labels_sha256": numeric._digest(truth)}
    return inputs, labels, manifest


def _summaries(row: ModelInput) -> tuple[float, ...]:
    features = []
    for count in SUMMARY_WINDOWS:
        window = row.minute[-count:]
        observed = [point for point in window if point[10]]
        volumes = [point[8] for point in window if point[9]]
        first = observed[0][0]  # latest15 support guarantees an observed minute
        scale = 1 + first / 100
        if scale <= 0:
            raise ValueError("EG summary anchor is out of range")
        bodies = [point[4] for point in observed]
        features.extend(((observed[-1][3] - first) / scale,
                         (max(point[1] for point in observed) - min(point[2] for point in observed)) / scale,
                         fsum(bodies) / len(bodies), pstdev(bodies), len(observed) / count,
                         sum(point[11] for point in window) / count, len(volumes) / count,
                         fsum(volumes) / len(volumes) if volumes else 0.0))
    return tuple(map(numeric._number, (*features, *row.state)))


def _lgb_training(inputs, labels):
    rows, targets = [], []
    for item, values in zip(inputs, labels, strict=True):
        summary = _summaries(item)
        for index, value in enumerate(values):
            if value is not None:
                rows.append(summary + TASK_TAGS[index])
                targets.append(value)
    return tuple(rows), tuple(targets)


def _lgb_queries(inputs, counts):
    rows, keys = [], []
    for offset, item in enumerate(inputs):
        summary = _summaries(item)
        for task in _eligible(item):
            if counts[task]:
                rows.append(summary + TASK_TAGS[task])
                keys.append((offset, task))
    return tuple(rows), tuple(keys)


def _torch_runtime(torch) -> dict:
    if (torch.__version__ != "2.7.0+cu128" or torch.get_num_threads() != 4
            or torch.get_num_interop_threads() != 1 or not torch.are_deterministic_algorithms_enabled()
            or torch.is_deterministic_algorithms_warn_only_enabled() or torch.is_autocast_enabled("cpu")
            or torch.get_default_dtype() != torch.float32 or torch.get_float32_matmul_precision() != "highest"
            or environ.get("CUDA_VISIBLE_DEVICES") != ""):
        raise ValueError("pinned deterministic EG CPU Torch runtime required")
    return {"torch": torch.__version__, "device": "cpu", "dtype": "float32", "threads": 4,
            "interop_threads": 1, "deterministic": True, "matmul_precision": "highest", "cuda_visible_devices": "",
            "mkldnn_enabled": torch.backends.mkldnn.enabled, "mkldnn_deterministic": torch.backends.mkldnn.deterministic}


def _network(torch, width: int):
    class MinuteTCN(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.minute_tower = build_tcn().minute_tower
            self.minute_tower.input_projection = torch.nn.Conv1d(width, 16, 1, device="cpu", dtype=torch.float32)
            self.head = torch.nn.Linear(16 + len(STATES), len(TASKS), device="cpu", dtype=torch.float32)

        def forward(self, minute, state):
            return self.head(torch.cat((self.minute_tower(minute)[:, :, -1], state), dim=1))

    with torch.device("cpu"):
        return MinuteTCN()


def _tensors(torch, inputs):
    minute = torch.tensor([row.minute for row in inputs], dtype=torch.float32, device="cpu")
    state = torch.tensor([row.state for row in inputs], dtype=torch.float32, device="cpu")
    if not torch.isfinite(minute).all().item() or not torch.isfinite(state).all().item():
        raise ValueError("EG input is not finite after float32 conversion")
    return minute, state


def _tcn_fit(torch, inputs, labels, width, seed):
    from torch.nn import functional as F
    from torch.optim.adamw import adamw

    minute, state = _tensors(torch, inputs)
    target = torch.tensor([[0 if v is None else v for v in row] for row in labels],
                          dtype=torch.float32, device="cpu")
    known = torch.tensor([[v is not None for v in row] for row in labels], dtype=torch.bool, device="cpu")
    if not torch.isfinite(target).all().item():
        raise ValueError("EG labels are not finite after float32 conversion")
    # Explicit empty device list avoids touching accelerator RNG state.
    with torch.random.fork_rng(devices=[]):
        torch.random.default_generator.manual_seed(seed)
        order = torch.Generator(device="cpu").manual_seed(seed)
        model = _network(torch, width)
        parameters = list(model.parameters())
        first_moments = [torch.zeros_like(value) for value in parameters]
        second_moments = [torch.zeros_like(value) for value in parameters]
        steps = [torch.zeros((), dtype=torch.float32, device="cpu") for _ in parameters]
        for _ in range(EPOCHS):
            for indices in torch.randperm(len(inputs), generator=order, device="cpu").split(BATCH_SIZE):
                mask = known[indices]
                if not mask.any().item():
                    continue
                model.zero_grad(set_to_none=True)
                outputs = model(minute[indices], state[indices])
                truth = target[indices]
                losses = []
                for head in range(len(TASKS)):
                    active = mask[:, head]
                    if active.any().item():
                        loss = F.binary_cross_entropy_with_logits if head == 1 else F.mse_loss
                        losses.append(loss(outputs[active, head], truth[active, head]))
                loss = torch.stack(losses).mean()
                if not torch.isfinite(loss).item():
                    raise ValueError("nonfinite EG training loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(parameters, 1.0, error_if_nonfinite=True, foreach=False)
                # Functional AdamW avoids the optimizer object's CUDA graph health check.
                with torch.no_grad():
                    adamw(parameters, [value.grad for value in parameters], first_moments,
                          second_moments, [], steps, **OPTIMIZER)
    if any(not torch.isfinite(value).all().item() for value in model.state_dict().values()):
        raise ValueError("nonfinite EG coefficients")
    return {name: value.tolist() for name, value in model.state_dict().items()}


def _contract(arm: str, binding: Mapping) -> dict:
    channels = _channels(arm)
    if not isinstance(binding, Mapping) or not binding:
        raise ValueError("EG source/runtime/scientific binding required")
    parameters = (numeric.lightgbm_parameter_manifest() if arm == "LGB_CHART" else
                  {"epochs": EPOCHS, "batch_size": BATCH_SIZE, "seed_base": SEED,
                   "dilations": DILATIONS, "optimizer": {"method": "functional AdamW", **OPTIMIZER},
                   "gradient_clip_norm": 1.0,
                   "loss": "mean of available task means, return MSE and target-first BCE with logits"})
    return numeric._copy({"schema": SCHEMA, "arm": arm, "binding": binding, "minute_slots": MINUTE_SLOTS,
                          "channels": channels, "state": STATES, "tasks": TASKS, "parameters": parameters,
                          "target_first_raw_scale": "regression_score" if arm == "LGB_CHART" else "logit",
                          "lgb_features": LGB_NAMES if arm == "LGB_CHART" else None})


def fit(arm: str, training: Sequence[TrainingRow], *, binding: Mapping, fold_index: int) -> dict:
    """One fit on authenticated eligible training rows, without evaluation input."""
    contract = _contract(arm, binding)
    inputs, labels, manifest = _training(arm, training, fold_index)
    if arm == "LGB_CHART":
        np, lgb, runtime = numeric._libraries("lightgbm")
        rows, targets = _lgb_training(inputs, labels)
        manifest["native_features_sha256"] = numeric._numeric_matrix_hash(rows, len(LGB_NAMES))
        model = numeric._fit_lightgbm_matrix(rows, targets, LGB_NAMES, np, lgb)
    else:
        import torch

        runtime = _torch_runtime(torch)
        model = _tcn_fit(torch, inputs, labels, len(_channels(arm)), SEED + fold_index)
    artifact = {**contract, "training": manifest, "runtime": runtime, "model": model}
    return {**artifact, "artifact_sha256": numeric._digest(artifact)}


def _check(arm, artifact, binding) -> tuple[int, ...]:
    contract = _contract(arm, binding)
    if (type(artifact) is not dict
            or set(artifact) != set(contract) | {"training", "runtime", "model", "artifact_sha256"}
            or any(artifact[key] != value for key, value in contract.items())
            or artifact["artifact_sha256"] != numeric._digest({key: value for key, value in artifact.items()
                                                               if key != "artifact_sha256"})):
        raise ValueError("EG model artifact contract or checksum differs")
    manifest = artifact["training"]
    counts = manifest["task_rows"]
    if (type(manifest["rows"]) is not int or manifest["rows"] <= 0
            or type(counts) is not list or len(counts) != len(TASKS)
            or any(type(n) is not int or not 0 <= n <= manifest["rows"] for n in counts) or not any(counts)):
        raise ValueError("invalid EG task support")
    return tuple(counts)


def _tcn_raw(torch, inputs, artifact, width, replay):
    with torch.random.fork_rng(devices=[]):
        model = _network(torch, width)
    template = model.state_dict()
    if type(artifact["model"]) is not dict or set(artifact["model"]) != set(template):
        raise ValueError("EG coefficient roster differs")
    weights = {key: torch.tensor(value, dtype=torch.float32, device="cpu")
               for key, value in artifact["model"].items()}
    if any(weights[key].shape != value.shape or not torch.isfinite(weights[key]).all().item()
           for key, value in template.items()):
        raise ValueError("EG coefficient shape or value differs")
    if not replay:
        model.load_state_dict(weights, strict=True)
        model.eval()
    with torch.inference_mode():
        for start in range(0, len(inputs), BATCH_SIZE):
            minute, state = _tensors(torch, inputs[start:start + BATCH_SIZE])
            if replay:
                hidden = _dot_tower(torch, minute, weights, "minute_tower", DILATIONS)
                values = torch.cat((hidden, state), dim=1) @ weights["head.weight"].T + weights["head.bias"]
            else:
                values = model(minute, state)
            if not torch.isfinite(values).all().item():
                raise ValueError("nonfinite EG prediction")
            yield from values.tolist()


def _prediction(values, *, probability_from_logit: bool) -> ModelPrediction:
    gross, raw, holding = values
    if raw is None:
        probability, clipped = None, False
    elif probability_from_logit:
        tail = exp(-abs(raw))
        probability, clipped = (1 if raw >= 0 else tail) / (1 + tail), False
    else:
        probability, clipped = max(0.0, min(1.0, raw)), not 0 <= raw <= 1
    return ModelPrediction(gross, probability, holding, raw, clipped)


def _predict(arm, inputs, artifact, counts, *, replay, training_targets=None):
    width = len(_channels(arm))
    for row in inputs:
        _validate_input(row, width)
    if arm == "LGB_CHART":
        np, lgb, runtime = numeric._libraries("lightgbm")
        if artifact["runtime"] != runtime:
            raise ValueError("EG LightGBM runtime differs")
        booster = numeric._reload_lightgbm_matrix(artifact["model"], lgb, LGB_NAMES)
        if training_targets is not None:
            native_labels = tuple(float(value) for value in np.asarray(training_targets, dtype=np.float32))
            if artifact["model"]["native_training_labels_sha256"] != numeric._vector_hash(native_labels):
                raise ValueError("EG native training labels differ")
        rows, keys = _lgb_queries(inputs, counts)
        scores = (numeric._scalar_lightgbm_replay(booster.dump_model(), rows, len(LGB_NAMES)) if replay
                  else numeric._predict_lightgbm_matrix(booster, rows, np))
        values = [[None] * len(TASKS) for _ in inputs]
        for (index, task), score in zip(keys, scores, strict=True):
            values[index][task] = score
    else:
        import torch

        if artifact["runtime"] != _torch_runtime(torch):
            raise ValueError("EG Torch runtime differs")
        values = ([v if counts[i] and i in _eligible(row) else None for i, v in enumerate(scores)]
                  for row, scores in zip(inputs, _tcn_raw(torch, inputs, artifact, width, replay), strict=True))
    return tuple(_prediction(row, probability_from_logit=arm != "LGB_CHART") for row in values)


def predict(arm: str, inputs: Sequence[ModelInput], artifact: dict, *,
            binding: Mapping) -> tuple[ModelPrediction, ...]:
    return _predict(arm, inputs, artifact, _check(arm, artifact, binding), replay=False)


def _compare(arm, predictions, rebuilt) -> dict:
    absolute, relative = (numeric.ABS_TOL, numeric.REL_TOL) if arm == "LGB_CHART" else (ABS_TOL, REL_TOL)
    if len(predictions) != len(rebuilt):
        raise ValueError("EG replay prediction population differs")
    ambiguity = {"gross33": [], "gross43": [], "hold_zero": []}
    for index, (saved, expected) in enumerate(zip(predictions, rebuilt, strict=True)):
        if (type(saved) is not ModelPrediction or type(saved.target_first_clipped) is not bool
                or saved.target_first_clipped != expected.target_first_clipped):
            raise ValueError("EG replay prediction or clipping differs")
        for field in ("gross30_pct", "target_first_probability", "hold_advantage_pct", "target_first_raw"):
            left, right = getattr(saved, field), getattr(expected, field)
            if (left is None) != (right is None) or left is not None and (
                    type(left) is not float or not isclose(left, right, abs_tol=absolute, rel_tol=relative)):
                raise ValueError("EG independent replay score differs")
        for name, field, threshold in (("gross33", "gross30_pct", 0.33), ("gross43", "gross30_pct", 0.43),
                                       ("hold_zero", "hold_advantage_pct", 0.0)):
            left, right = getattr(saved, field), getattr(expected, field)
            if left is not None and ((left > threshold) != (right > threshold)
                    or any(isclose(value, threshold, abs_tol=absolute, rel_tol=relative) for value in (left, right))):
                ambiguity[name].append(index)
    return {"verified": True, "rows": len(rebuilt), "threshold_ambiguous_rows": ambiguity,
            "target_first_clipped_rows": sum(row.target_first_clipped for row in rebuilt)}


def verify(arm: str, training: Sequence[TrainingRow], inputs: Sequence[ModelInput], artifact: dict,
           predictions: Sequence[ModelPrediction], *, binding: Mapping, fold_index: int) -> dict:
    """Reconstruct training identity and replay numerics without a refit."""
    counts = _check(arm, artifact, binding)
    train, labels, manifest = _training(arm, training, fold_index)
    targets = None
    if arm == "LGB_CHART":
        rows, targets = _lgb_training(train, labels)
        manifest["native_features_sha256"] = numeric._numeric_matrix_hash(rows, len(LGB_NAMES))
    if artifact["training"] != manifest:
        raise ValueError("EG training manifest differs")
    rebuilt = _predict(arm, inputs, artifact, counts, replay=True, training_targets=targets)
    return {**_compare(arm, predictions, rebuilt), "artifact_sha256": artifact["artifact_sha256"],
            "inputs_sha256": _features_digest(inputs, len(_channels(arm)))}
