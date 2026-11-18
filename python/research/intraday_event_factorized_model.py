"""Pure EP matched direct/factorized six-head CPU TCN.

Only the two value losses and their final composition differ between arms.
The caller owns actual-run admission; this module reads no files or labels at
inference. Unknown action support never supplies a no-fill observation.
"""

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from itertools import accumulate
import json
import math
import random

from research import intraday_event_models as common
from research import intraday_event_sequence_model as sequence


ARMS = ("direct", "factorized")
HEADS = (*common.HEADS, "fill_wait3")
ACTION_HEADS = ("fill", "fill_wait3", *common.Q_HEADS)
CATEGORICAL = ("path_class", "fill", "fill_wait3")
PARAMETERS = sequence.PARAMETERS
DIAGNOSTICS = {
    "max_training_rows": 512, "loss_block_updates": 50, "extra_optimizer_updates": 0,
    "selection": "ascending SHA256 of target-free training identity",
    "sample_weighting": "restrict full-training loss weights to sampled rows and renormalize",
    "proposal": "equal mixture of nonempty unmasked base head cohorts, before fit-status or fill masks",
    "joint_loss_denominator": len(HEADS),
}
TASK_SEMANTICS = {
    "joint_entry_support": "applicable entry rows with both action values and both action fills known",
    "action_weights": "same J date->cluster->episode->frame base weights for all four action heads",
    "direct": "both action net values on J",
    "factorized": "P(action fill|X) times E(action net|X,action fill=1)",
    "conditional_weights": "original J weights times actual fill, divided by their sum; never rebalance filled rows",
    "auxiliary": "original path classification and held remaining-gross targets and cohorts",
    "loss_slots": "all six slots retain equal scaling; unsupported and constant heads have zero loss",
}


@dataclass(frozen=True)
class ActionFills:
    """Separate action-specific modeled fills and their own maturity evidence."""

    key: str
    immediate: int | None
    wait3: int | None
    immediate_matured_session: str | None
    wait3_matured_session: str | None

    def __post_init__(self):
        common._name(self.key)
        for action in ("immediate", "wait3"):
            value, maturity = getattr(self, action), getattr(self, action + "_matured_session")
            if value is not None and (type(value) is not int or value not in (0, 1)):
                raise ValueError("Action fill must be binary or unknown")
            if value is not None and maturity is None:
                raise ValueError("Known action fill requires action-specific maturity")
            if maturity is not None:
                common._day(maturity)


def validate_action_fills(rows, labels, action_fills, eval_first_session):
    fills = tuple(action_fills)
    if len(fills) != len(rows) or any(type(value) is not ActionFills for value in fills):
        raise ValueError("Separate complete ActionFills roster required")
    by_key = {value.key: value for value in fills}
    if len(by_key) != len(fills) or set(by_key) != {row.key for row in rows}:
        raise ValueError("Action fill key roster differs")
    for row in rows:
        fill, target = by_key[row.key], labels[row.key]
        for action in ("immediate", "wait3"):
            value, maturity = getattr(fill, action), getattr(fill, action + "_matured_session")
            if row.state in common.HELD_STATES and value is not None:
                raise ValueError("Held rows cannot supply action fills")
            if maturity is not None and not row.session <= maturity < eval_first_session:
                raise ValueError("Action fill has not matured strictly before evaluation")
            net = getattr(target, "net_" + action)
            if value == 0 and net is not None and Fraction(net) != 0:
                raise ValueError("Known no-fill action cannot have nonzero net target")
        if target.fill is not None and fill.immediate is not None and target.fill != fill.immediate:
            raise ValueError("Immediate action fill disagrees with original fill label")
    return by_key


def _applicable(row, head):
    return common.head_applicable(row, "fill" if head == "fill_wait3" else head)


def _feature_contract():
    return {**sequence._feature_contract(), "ep_task_semantics": TASK_SEMANTICS,
            "ep_heads": list(HEADS)}


def _network(torch, mode):
    model = sequence._network(torch, mode)
    model.heads["fill_wait3"] = torch.nn.Linear(sequence.WIDTH, 1).to(device="cpu", dtype=torch.float32)
    if sum(parameter.numel() for parameter in model.parameters()) > sequence.MAX_PARAMETERS:
        raise ValueError("EP parameter budget exceeded")
    return model


def _head_plan(arm, rows, labels, fills):
    """Keep proposal/base weights independent of conditional targets and status."""
    if arm not in ARMS:
        raise ValueError("Unregistered EP arm")
    joint = [i for i, row in enumerate(rows) if row.state not in common.HELD_STATES
             and common.training_head_known(labels[row.key], "net_immediate")
             and fills[row.key].immediate is not None and fills[row.key].wait3 is not None]
    joint_set = set(joint)
    joint_weights = dict(zip(joint, common.head_weights([rows[i] for i in joint])))
    heads, bases, weights, targets = {}, {}, {}, {}
    for head in HEADS:
        raw = [getattr(fills[row.key], "immediate" if head == "fill" else "wait3")
               if head in ("fill", "fill_wait3") else getattr(labels[row.key], head) for row in rows]
        base_indices = joint if head in ACTION_HEADS else [i for i, value in enumerate(raw) if value is not None]
        base = [0.0] * len(rows)
        known_base = joint_weights if head in ACTION_HEADS else dict(zip(
            base_indices, common.head_weights([rows[i] for i in base_indices])))
        for index, value in known_base.items():
            base[index] = value
        bases[head] = base
        effective = list(base)
        if arm == "factorized" and head in common.Q_HEADS:
            action = head.removeprefix("net_")
            effective = [w if getattr(fills[row.key], action) == 1 else 0.0 for row, w in zip(rows, base)]
            total = math.fsum(effective)
            effective = [w / total for w in effective] if total else effective
        indices = [i for i, weight in enumerate(effective) if weight > 0]
        known = [rows[i] for i in indices]
        values = [sequence._number(raw[i]) for i in indices]
        info = common._known_stats(known, len(rows))
        info.update(raw_known_rows=sum(value is not None for value in raw),
                    base_known_rows=len(base_indices), known_zero_rows=sum(value == 0 for value in values),
                    conditional_on_fill=arm == "factorized" and head in common.Q_HEADS,
                    dropped_outside_joint_rows=sum(value is not None and i not in joint_set
                                                  for i, value in enumerate(raw)) if head in ACTION_HEADS else 0)
        if not known:
            info.update(status="unsupported", reason="no_known_conditional_fills" if info["conditional_on_fill"] else "no_known_labels")
        elif len(known) < 2 or info["known_clusters"] < 2 or info["known_dates"] < 2:
            info.update(status="unsupported", reason="fewer_than_two_known_rows_clusters_or_dates")
        elif head in CATEGORICAL and len(set(values)) == 1:
            info.update(status="constant_single_class", **{"class": int(values[0])})
        else:
            info["status"] = "pending"
            weights[head] = effective
            if head not in CATEGORICAL:
                center = math.fsum(sequence._number(raw[i]) * effective[i] for i in indices)
                variance = math.fsum(effective[i] * (sequence._number(raw[i]) - center) ** 2 for i in indices)
                scale = math.sqrt(variance) or 1.0
                if not math.isfinite(center) or not math.isfinite(scale):
                    raise ValueError("EP target normalization overflow")
                info["target_normalization"] = {"mean": center, "std": scale}
        # Out-of-cohort target bytes must not enter a numerical loss, even with
        # zero weight: an overflowing intermediate times zero is still NaN.
        heads[head] = info
        targets[head] = [value if effective[i] > 0 else None for i, value in enumerate(raw)]
    decomposition = {}
    for action in ("immediate", "wait3"):
        decomposition[action] = {
            "joint_known_rows": len(joint),
            "weighted_fill_mass": math.fsum(joint_weights[i] for i in joint
                                            if getattr(fills[rows[i].key], action) == 1),
            "no_fill_zero_rows": sum(getattr(fills[rows[i].key], action) == 0 for i in joint),
            "filled_zero_rows": sum(getattr(fills[rows[i].key], action) == 1
                                    and Fraction(getattr(labels[rows[i].key], "net_" + action)) == 0 for i in joint),
            "filled_nonzero_rows": sum(getattr(fills[rows[i].key], action) == 1
                                       and Fraction(getattr(labels[rows[i].key], "net_" + action)) != 0 for i in joint),
            "unknown_fill_rows": sum(getattr(fills[row.key], action) is None
                                     for row in rows if row.state not in common.HELD_STATES),
            "fill_known_cash_unknown_rows": sum(getattr(fills[row.key], action) is not None
                                                and getattr(labels[row.key], "net_" + action) is None
                                                for row in rows if row.state not in common.HELD_STATES),
            "joint_excluded_fill_known_rows": sum(getattr(fills[row.key], action) is not None
                                                  and i not in joint_set for i, row in enumerate(rows)
                                                  if row.state not in common.HELD_STATES),
        }
    support = {"joint_known_rows": len(joint), "excluded_from_joint_rows": len(rows) - len(joint),
               "joint_support_sha256": sha256(sequence._json([rows[i].key for i in joint])).hexdigest(),
               "zero_decomposition": decomposition}
    return heads, bases, weights, targets, support


def _proposal(bases):
    active = [values for values in bases.values() if any(values)]
    return [math.fsum(values[i] for values in active) / len(active) for i in range(len(active[0]))] if active else []


def _diagnostic_plan(rows, heads, weights):
    identities = [sha256(sequence._json([row.key, row.session, row.symbol, row.episode_id,
                                        row.cluster_id, row.decision_offset, row.family, row.state])).hexdigest()
                  for row in rows]
    indices = sorted(range(len(rows)), key=lambda i: (identities[i], rows[i].key))[:DIAGNOSTICS["max_training_rows"]]
    per_head = {}
    for head in HEADS:
        known = [rows[i] for i in indices if head in weights and weights[head][i] > 0]
        per_head[head] = {"status": "ready" if known else "not_trained_head" if head not in weights else "no_known_sampled_rows",
                          "sampled_known_rows": len(known), "sampled_known_clusters": len({r.cluster_id for r in known}),
                          "sampled_known_dates": len({r.session for r in known}),
                          "initial_loss": None, "final_loss": None}
    hashes = [identities[i] for i in indices]
    return indices, {"contract": json.loads(sequence._json(DIAGNOSTICS)), "status": "not_trained",
                     "sampled_rows": len(indices), "sample_identity_sha256": hashes,
                     "sample_sha256": sha256(sequence._json(hashes)).hexdigest(),
                     "heads": per_head, "completed_updates": 0, "loss_blocks": []}


def _loss_vector(head, output, indices, targets, heads, torch):
    values = []
    for index in indices:
        target = sequence._number(targets[head][index])
        value = 0.0 if target is None else target
        if target is not None and head not in CATEGORICAL:
            stats = heads[head]["target_normalization"]
            value = (value - stats["mean"]) / stats["std"]
        values.append(value)
    if head == "path_class":
        return torch.nn.functional.cross_entropy(output, torch.tensor(values, dtype=torch.long), reduction="none")
    target = torch.tensor(values, dtype=torch.float32, device="cpu")
    return (torch.nn.functional.binary_cross_entropy_with_logits(output[:, 0], target, reduction="none")
            if head in CATEGORICAL else (output[:, 0] - target).square())


def _diagnostic_losses(model, rows, normalization, heads, weights, targets, mode, indices, diagnostics, phase, torch):
    totals = {head: [] for head in weights if diagnostics["heads"][head]["status"] in ("ready", "recorded")}
    masses = {head: math.fsum(weights[head][i] for i in indices) for head in totals}
    model.eval()
    with torch.no_grad():
        for start in range(0, len(indices), sequence.BATCH_SIZE):
            selected = indices[start:start + sequence.BATCH_SIZE]
            inputs = sequence._batch([rows[i] for i in selected], normalization, mode, torch)
            outputs = model(*inputs)
            for head in totals:
                correction = [weights[head][i] / masses[head] for i in selected]
                loss = _loss_vector(head, outputs[head], selected, targets, heads, torch)
                value = (loss * torch.tensor(correction, dtype=torch.float32)).sum()
                if not torch.isfinite(value):
                    raise FloatingPointError("Nonfinite EP diagnostic loss")
                totals[head].append(float(value))
    for head, values in totals.items():
        diagnostics["heads"][head][phase + "_loss"] = math.fsum(values)
        if phase == "final":
            diagnostics["heads"][head]["status"] = "recorded"


def _train(model, rows, normalization, heads, bases, weights, targets, mode, indices, diagnostics, torch):
    proposal = _proposal(bases)
    cumulative = list(accumulate(proposal))
    rng, sample_digest = random.Random(sequence.SEED), sha256()
    optimizer = torch.optim.Adam(model.parameters(), lr=sequence.LEARNING_RATE)
    hits, sampled = {head: 0 for head in weights}, {head: set() for head in weights}
    block_joint, block_heads = [], {head: [] for head in weights}
    diagnostics["status"] = "training"
    _diagnostic_losses(model, rows, normalization, heads, weights, targets, mode, indices, diagnostics, "initial", torch)
    model.train()
    for update in range(sequence.UPDATES):
        selected = rng.choices(range(len(rows)), cum_weights=cumulative, k=sequence.BATCH_SIZE)
        sample_digest.update(sequence._json(selected) + b"\n")
        outputs = model(*sequence._batch([rows[i] for i in selected], normalization, mode, torch))
        losses = []
        for head in weights:
            correction = [weights[head][i] / proposal[i] for i in selected]
            if any(correction):
                hits[head] += 1
                sampled[head].update(i for i in selected if weights[head][i] > 0)
            loss = _loss_vector(head, outputs[head], selected, targets, heads, torch)
            corrected = (loss * torch.tensor(correction, dtype=torch.float32)).mean()
            losses.append(corrected)
            if any(correction):
                block_heads[head].append(float(corrected.detach()))
        loss = torch.stack(losses).sum() / len(HEADS)
        if not torch.isfinite(loss):
            raise FloatingPointError("Nonfinite EP training loss")
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
            block_joint, block_heads = [], {head: [] for head in weights}
    if any(count == 0 for count in hits.values()):
        raise FloatingPointError("EP native head received no known-target update")
    _diagnostic_losses(model, rows, normalization, heads, weights, targets, mode, indices, diagnostics, "final", torch)
    diagnostics.update(status="complete", sampled_indices_sha256=sample_digest.hexdigest())
    return {head: {"updates_with_known_target": hits[head], "sampled_training_rows": len(sampled[head]),
                   "sampled_training_clusters": len({rows[i].cluster_id for i in sampled[head]})} for head in weights}


def fit(arm, training_queries, labels, action_fills, eval_first_session, *, mode="chart_peer", support_binding=None):
    if arm not in ARMS:
        raise ValueError("Unregistered EP arm")
    rows, by_key = common.validate_training(training_queries, labels, eval_first_session,
                                           mode=mode, support_binding=support_binding)
    fills = validate_action_fills(rows, by_key, action_fills, eval_first_session)
    normalization = sequence._normalizer(rows, mode)
    heads, bases, weights, targets, support = _head_plan(arm, rows, by_key, fills)
    indices, diagnostics = _diagnostic_plan(rows, heads, weights)
    diagnostics.update(proposal_sha256=sha256(sequence._json(_proposal(bases))).hexdigest(),
                       sampled_indices_sha256=None, initial_state_sha256=None)
    state, versions, updates = None, {}, 0
    if weights:
        with sequence._runtime() as torch:
            versions = {"torch": torch.__version__, "device": "cpu", "dtype": "float32"}
            model = _network(torch, mode)
            diagnostics["initial_state_sha256"] = sha256(sequence._json(sequence._state(model))).hexdigest()
            try:
                hits = _train(model, rows, normalization, heads, bases, weights, targets,
                              mode, indices, diagnostics, torch)
                state = sequence._state(model)
            except (RuntimeError, FloatingPointError) as error:
                diagnostics.update(status="failed", error_type=type(error).__name__, error=str(error)[:500])
                for head in weights:
                    heads[head].update(status="failed", reason="native_fit_failed")
            else:
                updates = sequence.UPDATES
                for head in weights:
                    heads[head].update(status="trained_tcn", **hits[head])
    for head, info in heads.items():
        info["fit_input_known_rows"] = info["known_rows"] if head in weights else 0
    feature_contract = _feature_contract()
    artifact = {
        "schema": "EP-factorized-model-v1", "kind": "tcn", "arm": arm, "mode": mode,
        "feature_contract": json.loads(sequence._json(feature_contract)),
        "parameters": json.loads(sequence._json(PARAMETERS)),
        "source_contract_sha256": sha256(sequence._json([feature_contract, PARAMETERS, DIAGNOSTICS])).hexdigest(),
        "normalization": normalization, "heads": heads, "state": state,
        "training_diagnostics": diagnostics, "action_training_support": support,
        "q_training_support": common.training_q_support(by_key.values()),
        "backend_versions": versions, "eval_first_session": eval_first_session,
        "training_sessions": sorted({row.session for row in rows}),
        "training_key_hashes": sorted(sha256(row.key.encode()).hexdigest() for row in rows),
        "common_support_sha256": support_binding, "training_rows": len(rows),
        "weighting": TASK_SEMANTICS["conditional_weights"],
        "fit_head_count": sum(info["status"] == "trained_tcn" for info in heads.values()),
        "fit_attempt_head_count": len(weights),
        "native_fit_head_count": sum(info["status"] == "trained_tcn" for info in heads.values()),
        "native_joint_fit_count": int(updates > 0), "native_updates": updates,
        "constant_head_count": sum(info["status"] == "constant_single_class" for info in heads.values()),
        "unsupported_head_count": sum(info["status"] == "unsupported" for info in heads.values()),
        "failed_head_count": sum(info["status"] == "failed" for info in heads.values()),
        "claim": "exploratory matched targets; no calibration, accounting or profitability claim",
    }
    sequence._json(artifact)
    return artifact


def _validate_artifact(artifact):
    if (not isinstance(artifact, dict) or artifact.get("schema") != "EP-factorized-model-v1"
            or artifact.get("kind") != "tcn" or artifact.get("arm") not in ARMS
            or artifact.get("mode") not in ("chart", "chart_peer")):
        raise ValueError("Unregistered EP artifact")
    expected = _feature_contract()
    if (artifact.get("feature_contract") != expected or artifact.get("parameters") != PARAMETERS
            or artifact.get("source_contract_sha256") != sha256(sequence._json([expected, PARAMETERS, DIAGNOSTICS])).hexdigest()
            or set(artifact.get("heads", {})) != set(HEADS)):
        raise ValueError("EP feature/source/head contract differs")
    sequence._validate_normalizer(artifact["normalization"], artifact["mode"])
    for head, info in artifact["heads"].items():
        status = info.get("status")
        if info.get("conditional_on_fill") is not (artifact["arm"] == "factorized" and head in common.Q_HEADS):
            raise ValueError("EP arm/conditional target contract differs")
        if status not in ("trained_tcn", "constant_single_class", "unsupported", "failed"):
            raise ValueError("Unknown EP head status; never substitute zero")
        if status == "constant_single_class" and (head not in CATEGORICAL
                or type(info.get("class")) is not int or info["class"] not in range(3 if head == "path_class" else 2)):
            raise ValueError("Malformed EP constant categorical head")
        if status == "trained_tcn" and head not in CATEGORICAL:
            stats = info.get("target_normalization", {})
            if (type(stats.get("mean")) not in (int, float) or type(stats.get("std")) not in (int, float)
                    or not math.isfinite(stats["mean"]) or not math.isfinite(stats["std"]) or stats["std"] <= 0):
                raise ValueError("Malformed EP target normalization")
    if len(sequence._json(artifact.get("state"))) > sequence.MAX_MODEL_BYTES:
        raise ValueError("EP artifact byte budget exceeded")


def predict(artifact, queries):
    """Return legacy five-head policy predictions plus separate EP components."""
    _validate_artifact(artifact)
    rows = common.validate_prediction_queries(artifact, queries)
    supported = [i for i, row in enumerate(rows) if artifact["mode"] == "chart"
                 or row.peer is not None and all(value is not None for value in row.peer)]
    values = {head: [None] * len(rows) for head in HEADS}
    for head in HEADS:
        info = artifact["heads"][head]
        if info["status"] == "constant_single_class":
            for i in supported:
                if _applicable(rows[i], head):
                    values[head][i] = [float(c == info["class"]) for c in range(3)] if head == "path_class" else float(info["class"])
    native = [head for head in HEADS if artifact["heads"][head]["status"] == "trained_tcn"
              and any(_applicable(rows[i], head) for i in supported)]
    if native:
        with sequence._runtime() as torch:
            if artifact["backend_versions"] != {"torch": torch.__version__, "device": "cpu", "dtype": "float32"}:
                raise ValueError("EP numerical runtime changed")
            model = _network(torch, artifact["mode"])
            sequence._load_state(model, artifact["state"], torch)
            model.eval()
            with torch.no_grad():
                for start in range(0, len(supported), sequence.BATCH_SIZE):
                    indices = supported[start:start + sequence.BATCH_SIZE]
                    outputs = model(*sequence._batch([rows[i] for i in indices], artifact["normalization"], artifact["mode"], torch))
                    for head in native:
                        output = outputs[head]
                        if head == "path_class":
                            decoded = torch.softmax(output, dim=1).tolist()
                        elif head in CATEGORICAL:
                            decoded = torch.sigmoid(output[:, 0]).tolist()
                        else:
                            stats = artifact["heads"][head]["target_normalization"]
                            decoded = (output[:, 0] * stats["std"] + stats["mean"]).tolist()
                        for index, value in zip(indices, decoded):
                            if not _applicable(rows[index], head):
                                continue
                            if not all(math.isfinite(v) for v in (value if isinstance(value, list) else [value])):
                                raise ValueError("Nonfinite EP prediction")
                            if head == "path_class" and (any(v < 0 or v > 1 for v in value) or abs(sum(value) - 1) > 1e-6):
                                raise ValueError("Malformed EP path probabilities")
                            if head in ("fill", "fill_wait3") and not 0 <= value <= 1:
                                raise ValueError("Malformed EP fill probability")
                            values[head][index] = value
    result, support_set = [], set(supported)
    for i, row in enumerate(rows):
        predictions = {head: values[head][i] for head in common.HEADS}
        components = {"fill_wait3": values["fill_wait3"][i],
                      "conditional_net_immediate": None, "conditional_net_wait3": None}
        statuses = {head: "unsupported_peer_context" if i not in support_set
                    else "not_applicable_to_state" if not _applicable(row, head)
                    else artifact["heads"][head]["status"] for head in HEADS}
        if artifact["arm"] == "factorized":
            for head, fill_head in (("net_immediate", "fill"), ("net_wait3", "fill_wait3")):
                conditional, probability = values[head][i], values[fill_head][i]
                components["conditional_" + head] = conditional
                predictions[head] = probability * conditional if probability is not None and conditional is not None else None
                if predictions[head] is not None and not math.isfinite(predictions[head]):
                    raise ValueError("Nonfinite EP composed prediction")
                if predictions[head] is None and _applicable(row, head) and i in support_set:
                    statuses[head] = "unsupported_factor_component"
        result.append({"key": row.key, "session": row.session,
                       "query_status": "supported_features" if i in support_set else "unsupported_peer_context",
                       "predictions": predictions,
                       "head_status": {head: statuses[head] for head in common.HEADS},
                       "components": components, "component_status": statuses})
    return tuple(result)
