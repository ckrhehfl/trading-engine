"""Bound EF TCN fits and independently replay their serialized coefficients."""
from __future__ import annotations

from hashlib import sha256
import math

from research.intraday_campaign_wsl_transport import digest, encode
from research.intraday_chart_folds import FoldSelection, Key
from research.intraday_chart_model_inputs import (
    ADMISSION_PCT, FitBinding, TCN_ARMS, fit_binding, sequence_buffers, training_targets,
)
from research.intraday_chart_tcn import BATCH_SIZE, HEADS, _check_cpu_runtime, build_tcn, fit_tcn, predict_tcn


SCHEMA = "EF-fixed-causal-tcn-v1"
ABS_TOL = 2e-6
REL_TOL = 2e-6


def _training(snapshot, split: FoldSelection, binding: FitBinding):
    if not split.supported or (split.scenario, split.fold.index) != (binding.scenario, binding.fold):
        raise ValueError("supported matching EF TCN fold required")
    keys = tuple(row.key for row in split.training)
    inputs = sequence_buffers(snapshot, keys, binding.scenario, binding.arm)
    values, masks = training_targets(split.training)
    truth = sha256(memoryview(values))
    truth.update(masks)
    manifest = {"rows": len(keys), "key_sha256": digest(encode(keys)), "inputs_sha256": inputs.sha256(),
                "truth_sha256": truth.hexdigest(), "cutoff": split.fold.first_test_opening.isoformat()}
    return inputs, values, masks, manifest


def fit_sequence(snapshot, split: FoldSelection, binding: FitBinding) -> dict:
    import torch

    _check_cpu_runtime(torch)
    bound = fit_binding(binding, TCN_ARMS)
    inputs, values, masks, manifest = _training(snapshot, split, binding)
    targets = torch.frombuffer(values, dtype=torch.float32).reshape(-1, HEADS)
    known = torch.frombuffer(masks, dtype=torch.uint8).reshape(-1, HEADS).bool()
    model = fit_tcn(inputs.torch_batch(torch), targets, known, fold_index=binding.fold)
    artifact = {"schema": SCHEMA, "binding": bound, "training": manifest, "torch": torch.__version__,
                "state": {key: value.tolist() for key, value in model.state_dict().items()}}
    return {**artifact, "artifact_sha256": digest(encode(artifact))}


def _reload(torch, artifact: dict, binding: FitBinding):
    _check_cpu_runtime(torch)
    fields = {"schema", "binding", "training", "torch", "state", "artifact_sha256"}
    if (type(artifact) is not dict or set(artifact) != fields or artifact["schema"] != SCHEMA
            or artifact["binding"] != fit_binding(binding, TCN_ARMS) or artifact["torch"] != torch.__version__
            or artifact["artifact_sha256"] != digest(encode({key: value for key, value in artifact.items()
                                                             if key != "artifact_sha256"}))):
        raise ValueError("EF TCN artifact schema, checksum or binding differs")
    model = build_tcn()
    template = model.state_dict()
    if type(artifact["state"]) is not dict or set(artifact["state"]) != set(template):
        raise ValueError("EF TCN coefficient roster differs")
    weights = {key: torch.tensor(value, dtype=torch.float32, device="cpu") for key, value in artifact["state"].items()}
    if any(weights[key].shape != expected.shape or not torch.isfinite(weights[key]).all().item()
           for key, expected in template.items()):
        raise ValueError("EF TCN coefficient shape or value differs")
    model.load_state_dict(weights, strict=True)
    return model.eval(), weights


def predict_sequence(snapshot, keys: tuple[Key, ...], binding: FitBinding, artifact: dict):
    import torch

    model, _ = _reload(torch, artifact, binding)
    if not keys:
        return ()
    inputs = sequence_buffers(snapshot, keys, binding.scenario, binding.arm)
    return tuple(map(tuple, predict_tcn(model, inputs.torch_batch(torch)).tolist()))


def _dot_tower(torch, inputs, weights, name, dilations):
    """Independent causal convolution using three lagged matrix products."""
    weights = {key.removeprefix(name + "."): value for key, value in weights.items() if key.startswith(name + ".")}
    state = (inputs @ weights["input_projection.weight"][:, :, 0].T + weights["input_projection.bias"]).relu()
    for index, dilation in enumerate(dilations):
        base = f"convolutions.{index}"
        kernel = weights[base + ".weight"]
        mapped = state @ kernel[:, :, 2].T + weights[base + ".bias"]
        for lag, tap in ((dilation, 1), (2 * dilation, 0)):
            if lag < state.shape[1]:
                mapped[:, lag:] += state[:, :-lag] @ kernel[:, :, tap].T
        state = (state + mapped).relu()
    return state[:, -1]


def verify_sequence(snapshot, split: FoldSelection, keys: tuple[Key, ...], binding: FitBinding,
                    artifact: dict, predictions: tuple[tuple[float, ...], ...]) -> dict:
    import torch

    _, weights = _reload(torch, artifact, binding)
    if artifact["training"] != _training(snapshot, split, binding)[-1] or len(keys) != len(predictions):
        raise ValueError("EF TCN training manifest or prediction population differs")
    inputs_hash = None
    if keys:
        inputs = sequence_buffers(snapshot, keys, binding.scenario, binding.arm)
        inputs_hash = inputs.sha256()
        batch = inputs.torch_batch(torch)
        with torch.inference_mode():
            for start in range(0, len(keys), BATCH_SIZE):
                part = slice(start, start + BATCH_SIZE)
                minute = _dot_tower(torch, batch.minute[part], weights, "minute_tower", (1, 2, 4, 8, 16))
                coarse = _dot_tower(torch, batch.coarse[part], weights, "coarse_tower", (1, 2, 4, 8))
                replay = (torch.cat((minute, coarse, batch.static[part]), dim=1) @ weights["head.weight"].T
                          + weights["head.bias"]).tolist()
                for saved, expected in zip(predictions[part], replay, strict=True):
                    if (len(saved) != HEADS or (saved[0] > ADMISSION_PCT) != (expected[0] > ADMISSION_PCT)
                            or (saved[1] <= 0) != (expected[1] <= 0)
                            or any(not math.isclose(actual, other, abs_tol=ABS_TOL, rel_tol=REL_TOL)
                                   for actual, other in zip(saved, expected, strict=True))):
                        raise ValueError("EF TCN coefficient replay or action differs")
    return {"verified": True, "artifact_sha256": artifact["artifact_sha256"], "rows": len(keys),
            "inputs_sha256": inputs_hash, "predictions_sha256": digest(encode(predictions))}
