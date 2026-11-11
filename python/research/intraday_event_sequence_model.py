"""Fresh EL CPU-only causal sequence learner, with no NumPy dependency.

The caller owns immutable source/runtime admission, resource limits and trial
logging. This pure numerical component cannot authorize an actual run. Importing
it does not import torch. Native operations use one CPU thread, preserve the
caller's RNG/settings, and never deserialize executable/pickled model payloads.
"""

from contextlib import contextmanager
from hashlib import sha256
from itertools import accumulate
import json
import math
import random

from research.intraday_event_features import CHANNELS, STATIC, STEPS
from research.intraday_event_models import (
    FAMILIES, HEADS, HELD_STATES, PEER_NAMES, POSITION_NAMES, Q_HEADS, STATES, TASK_SEMANTICS,
    head_applicable, head_weights, training_head_known, training_q_support,
    validate_prediction_queries, validate_training,
)


WIDTH = 16
DILATIONS = (1, 2, 4, 8, 16, 32)
SEED = 20261111
UPDATES = 200
BATCH_SIZE = 64
LEARNING_RATE = 0.001
MAX_PARAMETERS = 20_000
MAX_MODEL_BYTES = 1024 * 1024
TORCH_VERSION = "2.7.0+cpu"
DIAGNOSTICS = {"max_training_rows": 512, "selection": "ascending SHA256 of target-free training identity",
               "loss_block_updates": 50, "extra_optimizer_updates": 0,
               "sample_weighting": "recompute date->cluster->episode->frame weights on sampled head-known rows",
               "regression_loss_space": "squared error in fixed training-target-normalized space",
               "categorical_loss_space": "path cross entropy; fill binary cross entropy with logits",
               "block_head_mean": "mean importance-corrected minibatch loss over updates with known targets"}
PARAMETERS = {"width": WIDTH, "kernel": 3, "dilations": list(DILATIONS),
              "activation": "relu_residual", "embedding": "last_time",
              "optimizer": "Adam", "learning_rate": LEARNING_RATE,
              "updates": UPDATES, "batch_size": BATCH_SIZE, "seed": SEED,
              "device": "cpu", "threads": 1, "deterministic": True,
              "dtype": "float32", "gradient_clip_norm": 1.0}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _number(value):
    if value is None:
        return None
    # Query already admits exact Fractions/Decimals and canonical rational text.
    from fractions import Fraction
    result = float(Fraction(value)) if isinstance(value, str) else float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite statistical input")
    return result


def _feature_contract():
    return {"steps": STEPS, "channels": list(CHANNELS), "static": list(STATIC),
            "families": list(FAMILIES), "states": list(STATES), "peer": list(PEER_NAMES),
            "position": list(POSITION_NAMES), "task_semantics": json.loads(_json(TASK_SEMANTICS))}


class _Moments:
    def __init__(self, width):
        self.mean = [0.0] * width
        self.m2 = [0.0] * width
        self.count = [0] * width

    def add(self, values):
        for index, raw in enumerate(values):
            value = _number(raw)
            if value is None:
                continue
            self.count[index] += 1
            delta = value - self.mean[index]
            self.mean[index] += delta / self.count[index]
            self.m2[index] += delta * (value - self.mean[index])

    def result(self):
        std = [math.sqrt(max(0.0, value) / max(count, 1)) or 1.0
               for value, count in zip(self.m2, self.count)]
        if not all(math.isfinite(v) for v in (*self.mean, *self.m2, *std)):
            raise ValueError("Training normalization overflow")
        return {"mean": self.mean, "std": std, "finite_counts": self.count}


def _normalizer(rows, mode):
    sequence, static, peer = _Moments(len(CHANNELS)), _Moments(len(STATIC)), _Moments(len(PEER_NAMES))
    position = _Moments(len(POSITION_NAMES))
    for row in rows:
        for step in row.sequence:
            sequence.add(step)
        static.add(row.static)
        position.add(row.position or (None,) * len(POSITION_NAMES))
        if mode == "chart_peer":
            peer.add(row.peer)
    return {"sequence": sequence.result(), "static": static.result(), "position": position.result(),
            "peer": peer.result() if mode == "chart_peer" else None,
            "policy": "training-only channel-shared moments; missing standardized=0 plus per-value missing flag"}


def _validate_normalizer(normalization, mode):
    for key, width in (("sequence", len(CHANNELS)), ("static", len(STATIC)),
                       ("position", len(POSITION_NAMES)),
                       ("peer", len(PEER_NAMES))):
        stats = normalization.get(key)
        if key == "peer" and mode == "chart":
            if stats is not None:
                raise ValueError("Chart-only normalization contains peer data")
            continue
        if not isinstance(stats, dict) or any(len(stats.get(field, ())) != width
                                              for field in ("mean", "std", "finite_counts")):
            raise ValueError("Malformed normalization shape")
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in stats["mean"])
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in stats["std"])
                or any(type(v) is not int or v < 0 for v in stats["finite_counts"])):
            raise ValueError("Malformed normalization values")


def _standardize(values, stats):
    transformed, missing = [], []
    for index, raw in enumerate(values):
        value = _number(raw)
        transformed.append(0.0 if value is None else (value - stats["mean"][index]) / stats["std"][index])
        missing.append(float(value is None))
    result = transformed + missing
    if not all(math.isfinite(v) for v in result):
        raise ValueError("Transformed feature overflow")
    return result


def _batch(rows, normalization, mode, torch):
    sequences, static = [], []
    for row in rows:
        sequences.append([_standardize(step, normalization["sequence"]) for step in row.sequence])
        context = _standardize(row.static, normalization["static"])
        context.extend(_standardize(row.position or (None,) * len(POSITION_NAMES), normalization["position"]))
        if mode == "chart_peer":
            context.extend(_standardize(row.peer, normalization["peer"]))
        context.extend(float(row.family == name) for name in FAMILIES)
        context.extend(float(row.state == name) for name in STATES)
        static.append(context)
    sequence_tensor = torch.tensor(sequences, dtype=torch.float32, device="cpu").transpose(1, 2)
    static_tensor = torch.tensor(static, dtype=torch.float32, device="cpu")
    if not torch.isfinite(sequence_tensor).all() or not torch.isfinite(static_tensor).all():
        raise ValueError("Float32 feature overflow")
    return sequence_tensor, static_tensor


@contextmanager
def _runtime():
    import torch
    if torch.__version__ != TORCH_VERSION or torch.version.cuda is not None:
        raise ValueError("Unregistered CPU torch runtime")
    threads = torch.get_num_threads()
    deterministic = torch.are_deterministic_algorithms_enabled()
    warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    try:
        torch.set_num_threads(1)
        torch.use_deterministic_algorithms(True)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(SEED)
            yield torch
    finally:
        torch.use_deterministic_algorithms(deterministic, warn_only=warn_only)
        torch.set_num_threads(threads)


def _network(torch, mode):
    """Factory avoids importing native code at module import time."""
    nn, functional = torch.nn, torch.nn.functional

    class CausalEncoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.input = nn.Conv1d(2 * len(CHANNELS), WIDTH, 1)
            self.layers = nn.ModuleList(nn.Conv1d(WIDTH, WIDTH, 3, dilation=d) for d in DILATIONS)

        def forward(self, sequence):
            value = torch.relu(self.input(sequence))
            for dilation, layer in zip(DILATIONS, self.layers):
                value = torch.relu(value + layer(functional.pad(value, (2 * dilation, 0))))
            return value

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = CausalEncoder()
            context = 2 * (len(STATIC) + len(POSITION_NAMES)) + len(FAMILIES) + len(STATES)
            if mode == "chart_peer":
                context += 2 * len(PEER_NAMES)
            self.context = nn.Linear(WIDTH + context, WIDTH)
            self.heads = nn.ModuleDict({head: nn.Linear(WIDTH, 3 if head == "path_class" else 1)
                                       for head in HEADS})

        def forward(self, sequence, static):
            last = self.encoder(sequence)[:, :, -1]
            value = torch.relu(self.context(torch.cat((last, static), dim=1)))
            return {head: layer(value) for head, layer in self.heads.items()}

    model = Model().to(device="cpu", dtype=torch.float32)
    if sum(parameter.numel() for parameter in model.parameters()) > MAX_PARAMETERS:
        raise ValueError("TCN parameter budget exceeded")
    return model


def _head_plan(rows, labels):
    heads, weights = {}, {}
    for head in HEADS:
        indices = [i for i, row in enumerate(rows) if training_head_known(labels[row.key], head)]
        known = [rows[i] for i in indices]
        targets = [_number(getattr(labels[row.key], head)) for row in known]
        dates, clusters = len({r.session for r in known}), len({r.cluster_id for r in known})
        result = {"known_rows": len(known), "excluded_unknown_rows": len(rows) - len(known),
                  "known_episodes": len({r.original_episode_id or r.episode_id for r in known}),
                  "known_counterfactual_books": len({r.episode_id for r in known if r.state in HELD_STATES}),
                  "known_clusters": clusters, "known_dates": dates,
                  "known_zero_rows": sum(value == 0 for value in targets),
                  "low_support": clusters < 200 or dates < 30,
                  "precision_warning": "200 clusters/30 dates are warnings, not power or permission gates"}
        result["raw_known_rows"] = sum(getattr(labels[row.key], head) is not None for row in rows)
        result["dropped_unpaired_q_rows"] = result["raw_known_rows"] - len(known) if head in Q_HEADS else 0
        if not known:
            result.update(status="unsupported", reason="no_known_labels")
        elif len(known) < 2 or clusters < 2 or dates < 2:
            result.update(status="unsupported", reason="fewer_than_two_known_rows_clusters_or_dates")
        elif head in ("path_class", "fill") and len(set(targets)) == 1:
            result.update(status="constant_single_class", **{"class": int(targets[0])})
        else:
            result["status"] = "pending"
            known_weights = head_weights(known)
            weights[head] = [0.0] * len(rows)
            for index, weight in zip(indices, known_weights):
                weights[head][index] = weight
            if head not in ("path_class", "fill"):
                center = math.fsum(v * w for v, w in zip(targets, known_weights))
                variance = math.fsum(w * (v - center) ** 2 for v, w in zip(targets, known_weights))
                scale = math.sqrt(variance) or 1.0
                if not math.isfinite(center) or not math.isfinite(scale):
                    raise ValueError("Training target normalization overflow")
                result["target_normalization"] = {"mean": center, "std": scale}
        heads[head] = result
    return heads, weights


def _state(model):
    result = {name: {"shape": list(value.shape), "values": value.detach().reshape(-1).tolist()}
              for name, value in model.state_dict().items()}
    if any(not math.isfinite(number) for value in result.values() for number in value["values"]):
        raise FloatingPointError("Nonfinite fitted parameter")
    if len(_json(result)) > MAX_MODEL_BYTES:
        raise ValueError("TCN artifact byte budget exceeded")
    return result


def _load_state(model, state, torch):
    if not isinstance(state, dict) or set(state) != set(model.state_dict()):
        raise ValueError("TCN parameter identities differ")
    parameters, count = {}, 0
    for name, expected in model.state_dict().items():
        value = state[name]
        if not isinstance(value, dict) or value.get("shape") != list(expected.shape):
            raise ValueError("TCN parameter shape differs")
        values = value.get("values")
        if not isinstance(values, list) or len(values) != expected.numel():
            raise ValueError("TCN parameter value count differs")
        count += len(values)
        if count > MAX_PARAMETERS or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("TCN parameter values exceed bounds")
        tensor = torch.tensor(values, dtype=torch.float32, device="cpu").reshape(expected.shape)
        if not torch.isfinite(tensor).all():
            raise ValueError("TCN parameter float32 overflow")
        parameters[name] = tensor
    model.load_state_dict(parameters, strict=True)


def _diagnostic_plan(rows, labels, heads):
    identities = [sha256(_json([row.key, row.session, row.symbol, row.episode_id, row.cluster_id,
                                row.decision_offset, row.family, row.state])).hexdigest() for row in rows]
    indices = sorted(range(len(rows)), key=lambda i: (identities[i], rows[i].key))[:DIAGNOSTICS["max_training_rows"]]
    per_head = {}
    for head in HEADS:
        known = [rows[i] for i in indices if training_head_known(labels[rows[i].key], head)]
        status = "ready" if heads[head]["status"] == "pending" and known else (
            "no_known_sampled_rows" if heads[head]["status"] == "pending" else "not_trained_head")
        per_head[head] = {"status": status, "model_head_status_at_start": heads[head]["status"],
                          "sampled_known_rows": len(known), "sampled_known_clusters": len({r.cluster_id for r in known}),
                          "sampled_known_dates": len({r.session for r in known}),
                          "initial_loss": None, "final_loss": None}
    hashes = [identities[i] for i in indices]
    return indices, {"contract": json.loads(_json(DIAGNOSTICS)), "status": "not_trained",
                     "sampled_rows": len(indices), "sample_identity_sha256": hashes,
                     "sample_sha256": sha256(_json(hashes)).hexdigest(), "heads": per_head,
                     "completed_updates": 0, "loss_blocks": []}


def _loss_vector(head, output, selected_rows, labels, heads, torch):
    values = []
    for row in selected_rows:
        target = _number(getattr(labels[row.key], head)) if training_head_known(labels[row.key], head) else None
        value = 0.0 if target is None else target
        if target is not None and head not in ("path_class", "fill"):
            stats = heads[head]["target_normalization"]
            value = (value - stats["mean"]) / stats["std"]
        values.append(value)
    if head == "path_class":
        target = torch.tensor(values, dtype=torch.long, device="cpu")
        return torch.nn.functional.cross_entropy(output, target, reduction="none")
    target = torch.tensor(values, dtype=torch.float32, device="cpu")
    output = output[:, 0]
    return (torch.nn.functional.binary_cross_entropy_with_logits(output, target, reduction="none")
            if head == "fill" else (output - target).square())


def _diagnostic_losses(model, rows, labels, normalization, heads, mode, indices, diagnostics, phase, torch):
    selected = [rows[i] for i in indices]
    weights, totals = {}, {}
    for head, info in diagnostics["heads"].items():
        if info["status"] != "ready":
            continue
        known_positions = [i for i, row in enumerate(selected) if training_head_known(labels[row.key], head)]
        weights[head] = dict(zip(known_positions, head_weights([selected[i] for i in known_positions])))
        totals[head] = []
    was_training = model.training
    model.eval()
    with torch.no_grad():
        for start in range(0, len(selected), BATCH_SIZE):
            batch = selected[start:start + BATCH_SIZE]
            sequence, static = _batch(batch, normalization, mode, torch)
            outputs = model(sequence, static)
            for head in weights:
                correction = [weights[head].get(start + i, 0.0) for i in range(len(batch))]
                if not any(correction):
                    continue
                losses = _loss_vector(head, outputs[head], batch, labels, heads, torch)
                value = (losses * torch.tensor(correction, dtype=torch.float32, device="cpu")).sum()
                if not torch.isfinite(value):
                    raise FloatingPointError("Nonfinite fixed-sample training diagnostic loss")
                totals[head].append(float(value))
    model.train(was_training)
    for head, values in totals.items():
        diagnostics["heads"][head][phase + "_loss"] = math.fsum(values)
        if phase == "final":
            diagnostics["heads"][head]["status"] = "recorded"


def _train(model, rows, labels, normalization, heads, weights, mode, torch, diagnostic_indices, diagnostics):
    active = tuple(weights)
    proposal = [math.fsum(weights[head][i] for head in active) / len(active) for i in range(len(rows))]
    cumulative = list(accumulate(proposal))
    rng = random.Random(SEED)
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    hit_updates = {head: 0 for head in active}
    sampled = {head: set() for head in active}
    block_joint, block_heads = [], {head: [] for head in active}
    diagnostics["status"] = "training"
    _diagnostic_losses(model, rows, labels, normalization, heads, mode, diagnostic_indices, diagnostics, "initial", torch)
    model.train()
    for update in range(UPDATES):
        indices = rng.choices(range(len(rows)), cum_weights=cumulative, k=BATCH_SIZE)
        selected = [rows[i] for i in indices]
        sequence, static = _batch(selected, normalization, mode, torch)
        outputs = model(sequence, static)
        losses = []
        for head in active:
            correction = [weights[head][i] / proposal[i] for i in indices]
            if any(correction):
                hit_updates[head] += 1
                sampled[head].update(i for i in indices if weights[head][i] > 0)
            loss = _loss_vector(head, outputs[head], selected, labels, heads, torch)
            corrected = (loss * torch.tensor(correction, dtype=torch.float32, device="cpu")).mean()
            losses.append(corrected)
            if any(correction):
                block_heads[head].append(float(corrected.detach()))
        loss = torch.stack(losses).mean()
        if not torch.isfinite(loss):
            raise FloatingPointError("Nonfinite TCN training loss")
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
        optimizer.step()
        diagnostics["completed_updates"] = update + 1
        block_joint.append(float(loss.detach()))
        if (update + 1) % DIAGNOSTICS["loss_block_updates"] == 0:
            diagnostics["loss_blocks"].append({"first_update": update + 2 - len(block_joint),
                "last_update": update + 1, "updates": len(block_joint),
                "mean_joint_loss": math.fsum(block_joint) / len(block_joint),
                "heads": {head: {"updates_with_known_target": len(values),
                                  "mean_loss": math.fsum(values) / len(values) if values else None}
                          for head, values in block_heads.items()}})
            block_joint, block_heads = [], {head: [] for head in active}
    if any(count == 0 for count in hit_updates.values()):
        raise FloatingPointError("A native head received no known-target update")
    _diagnostic_losses(model, rows, labels, normalization, heads, mode, diagnostic_indices, diagnostics, "final", torch)
    diagnostics["status"] = "complete"
    return {head: {"updates_with_known_target": hit_updates[head], "sampled_training_rows": len(sampled[head]),
                   "sampled_training_clusters": len({rows[i].cluster_id for i in sampled[head]})} for head in active}


def fit(kind, training_queries, labels, eval_first_session, *, mode="chart", support_binding=None):
    if kind != "tcn":
        raise ValueError("Unregistered sequence learner")
    rows, by_key = validate_training(training_queries, labels, eval_first_session,
                                    mode=mode, support_binding=support_binding)
    normalization = _normalizer(rows, mode)
    heads, weights = _head_plan(rows, by_key)
    diagnostic_indices, diagnostics = _diagnostic_plan(rows, by_key, heads)
    state, versions, native_updates = None, {}, 0
    if weights:
        with _runtime() as torch:
            versions = {"torch": torch.__version__, "device": "cpu", "dtype": "float32"}
            model = _network(torch, mode)
            try:
                hits = _train(model, rows, by_key, normalization, heads, weights, mode, torch, diagnostic_indices, diagnostics)
                state = _state(model)
            except (RuntimeError, FloatingPointError) as error:
                diagnostics.update(status="failed", error_type=type(error).__name__, error=str(error)[:500])
                for head in weights:
                    heads[head].update(status="failed", reason="native_fit_failed",
                                       error_type=type(error).__name__, error=str(error)[:500])
            else:
                native_updates = UPDATES
                for head in weights:
                    heads[head].update(status="trained_tcn", **hits[head])
    for head, info in heads.items():
        info["fit_input_known_rows"] = info["known_rows"] if head in weights else 0
    feature_contract = _feature_contract()
    artifact = {"schema": "EL-sequence-model-v1", "kind": kind, "mode": mode,
                "feature_contract": feature_contract, "parameters": json.loads(_json(PARAMETERS)),
                "source_contract_sha256": sha256(_json([feature_contract, PARAMETERS, DIAGNOSTICS])).hexdigest(),
                "normalization": normalization, "heads": heads, "state": state,
                "training_diagnostics": diagnostics, "q_training_support": training_q_support(by_key.values()),
                "backend_versions": versions, "eval_first_session": eval_first_session,
                "training_sessions": sorted({row.session for row in rows}),
                "training_key_hashes": sorted(sha256(row.key.encode()).hexdigest() for row in rows),
                "common_support_sha256": support_binding, "training_rows": len(rows),
                "weighting": "head-known-only date->cluster->episode->frame; mixture sampling with importance correction",
                "fit_head_count": sum(h["status"] == "trained_tcn" for h in heads.values()),
                "fit_attempt_head_count": len(weights), "native_fit_head_count": sum(h["status"] == "trained_tcn" for h in heads.values()),
                "native_joint_fit_count": int(native_updates > 0), "native_updates": native_updates,
                "constant_head_count": sum(h["status"] == "constant_single_class" for h in heads.values()),
                "unsupported_head_count": sum(h["status"] == "unsupported" for h in heads.values()),
                "failed_head_count": sum(h["status"] == "failed" for h in heads.values()),
                "claim": "exploratory predictions; no economic accounting, probability calibration or profitability claim"}
    _json(artifact)
    return artifact


def _validate_artifact(artifact):
    if (artifact.get("schema") != "EL-sequence-model-v1" or artifact.get("kind") != "tcn"
            or artifact.get("mode") not in ("chart", "chart_peer")):
        raise ValueError("Unregistered TCN artifact")
    expected = _feature_contract()
    if (artifact.get("feature_contract") != expected or artifact.get("parameters") != PARAMETERS
            or artifact.get("source_contract_sha256") != sha256(_json([expected, PARAMETERS, DIAGNOSTICS])).hexdigest()
            or set(artifact.get("heads", {})) != set(HEADS)):
        raise ValueError("TCN feature/source/head contract differs")
    _validate_normalizer(artifact["normalization"], artifact["mode"])
    for head, info in artifact["heads"].items():
        status = info.get("status")
        if status not in ("trained_tcn", "constant_single_class", "unsupported", "failed"):
            raise ValueError("Unknown head status; never substitute zero")
        if status == "constant_single_class":
            if head not in ("path_class", "fill") or type(info.get("class")) is not int or info["class"] not in range(3 if head == "path_class" else 2):
                raise ValueError("Malformed constant categorical head")
        if status == "trained_tcn" and head not in ("path_class", "fill"):
            stats = info.get("target_normalization", {})
            if (type(stats.get("mean")) not in (int, float) or type(stats.get("std")) not in (int, float)
                    or not math.isfinite(stats["mean"]) or not math.isfinite(stats["std"]) or stats["std"] <= 0):
                raise ValueError("Malformed target normalization")
    if len(_json(artifact.get("state"))) > MAX_MODEL_BYTES:
        raise ValueError("TCN artifact byte budget exceeded")


def predict(artifact, queries):
    _validate_artifact(artifact)
    rows = validate_prediction_queries(artifact, queries)
    supported = [index for index, row in enumerate(rows)
                 if artifact["mode"] == "chart" or row.peer is not None and all(v is not None for v in row.peer)]
    native = [head for head in HEADS if artifact["heads"][head]["status"] == "trained_tcn"
              and any(head_applicable(rows[index], head) for index in supported)]
    values = {head: [None] * len(rows) for head in HEADS}
    for head in HEADS:
        info = artifact["heads"][head]
        if info["status"] == "constant_single_class":
            for index in supported:
                if not head_applicable(rows[index], head):
                    continue
                values[head][index] = ([float(i == info["class"]) for i in range(3)]
                                       if head == "path_class" else float(info["class"]))
    if native and supported:
        with _runtime() as torch:
            if artifact["backend_versions"] != {"torch": torch.__version__, "device": "cpu", "dtype": "float32"}:
                raise ValueError("TCN numerical runtime changed")
            model = _network(torch, artifact["mode"])
            _load_state(model, artifact["state"], torch)
            model.eval()
            with torch.no_grad():
                for start in range(0, len(supported), BATCH_SIZE):
                    indices = supported[start:start + BATCH_SIZE]
                    sequence, static = _batch([rows[i] for i in indices], artifact["normalization"], artifact["mode"], torch)
                    outputs = model(sequence, static)
                    for head in native:
                        if not any(head_applicable(rows[index], head) for index in indices):
                            continue
                        output = outputs[head]
                        if head == "path_class":
                            decoded = torch.softmax(output, dim=1).tolist()
                        elif head == "fill":
                            decoded = torch.sigmoid(output[:, 0]).tolist()
                        else:
                            stats = artifact["heads"][head]["target_normalization"]
                            decoded = (output[:, 0] * stats["std"] + stats["mean"]).tolist()
                        for index, value in zip(indices, decoded):
                            if not head_applicable(rows[index], head):
                                continue
                            numbers = value if isinstance(value, list) else [value]
                            if not all(math.isfinite(v) for v in numbers):
                                raise ValueError("Nonfinite TCN prediction")
                            if head == "path_class" and (any(v < 0 or v > 1 for v in value) or abs(sum(value) - 1) > 1e-6):
                                raise ValueError("Malformed path probabilities")
                            if head == "fill" and not 0 <= value <= 1:
                                raise ValueError("Malformed fill probability")
                            values[head][index] = value
    support_set = set(supported)
    results = []
    for index, row in enumerate(rows):
        applicable = {head: head_applicable(row, head) for head in HEADS}
        results.append({"key": row.key, "session": row.session,
                        "query_status": "supported_features" if index in support_set else "unsupported_peer_context",
                        "predictions": {head: values[head][index] if applicable[head] else None for head in HEADS},
                        "head_status": {head: "unsupported_peer_context" if index not in support_set
                                        else "not_applicable_to_state" if not applicable[head]
                                        else artifact["heads"][head]["status"] for head in HEADS}})
    return tuple(results)
