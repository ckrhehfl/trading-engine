"""Independent ER inner-prefix and EP-direct fit witnesses.

This module never calls the new producer's collection, fitting or prediction
functions. Existing authenticated stream/label adapters and EP's independently
implemented arithmetic helpers are reused. Native optimizer replay is not a
claim of this witness; the separate saved-inference replay covers native output.
"""

from collections import Counter, defaultdict
from fractions import Fraction
from hashlib import sha256
from itertools import accumulate
import math
import random

from research import intraday_event_factorized_reference as prior
from research import intraday_event_factorized_workflow as ep
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as original


def _check(condition, message):
    if not condition:
        raise ValueError("ER independent OOF: " + message)


def _bounds(data):
    _check(type(data.inner) is int and data.inner in (0, 1), "inner split differs")
    _check(data.scope == "common_chart_peer" and len(data.calendar) == 257
           and list(data.calendar) == sorted(set(data.calendar)), "original calendar/scope differs")
    return ((49, 73), (73, 97))[data.inner]


def _support_hash(rows):
    return prior._hash([(r.key, r.session, r.symbol, r.episode_id, r.decision_offset,
                        r.cluster_id, r.family, r.state, r.original_episode_id or r.episode_id) for r in rows])


def verify_inputs(shards, data):
    """Independently rebuild inner query selection and permitted label prefixes.

    Original saved CashTarget decoding remains the previously reviewed EP
    adapter. Future label rows are authenticated as bytes, never decoded.
    """
    stop, end = _bounds(data)
    shards = tuple(shards)
    _check(tuple(inputs.calendar_from_coordinates(shards)) == data.calendar, "authenticated calendar differs")
    cutoff = data.calendar[stop]
    training, evaluation, contexts, labels, fills, seen, counts = {}, {}, {}, {}, {}, set(), Counter()
    actual_training = {row.key: row for row in data.training}
    actual_evaluation = {row.key: row for row in data.evaluation}
    _check(len(actual_training) == len(data.training) and len(actual_evaluation) == len(data.evaluation),
           "duplicate supplied query")
    for slot, manifest in shards:
        if manifest["session_indices"][0] >= end:
            break
        prefix, boundary = [], False
        for record in inputs.query_rows(slot, manifest):
            day, key = record["session"], record["key"]
            _check(day in data.calendar, "query escaped the original calendar")
            before = day < cutoff
            _check(not before or not boundary, "training dates follow the prediction boundary")
            if before:
                prefix.append((key, day, record["symbol"]))
            else:
                boundary = True
            if day not in data.calendar[:end]:
                continue
            _check(key not in seen, "duplicate query")
            seen.add(key)
            part = "training" if before else "evaluation"
            counts[part + ":original"] += 1
            raw = record["query"]
            if raw is None:
                counts[part + ":unknown_chart"] += 1
                continue
            query = original.decode_query(raw)
            _check((query.key, query.session, query.symbol) == (key, day, record["symbol"]), "query identity differs")
            supported = query.peer is not None and all(value is not None for value in query.peer)
            counts[part + (":peer_known" if supported else ":peer_unknown")] += 1
            if not supported:
                continue
            actual = (actual_training if before else actual_evaluation).get(key)
            _check(actual == query, "training queries differ" if before else "prediction queries differ")
            # Compare a decoded row, then keep only the already loaded object:
            # a second complete 64x13 float panel would exceed the local cap.
            (training if before else evaluation)[key] = actual
            if before:
                causal = inputs._decode_label_query(record["label_query"]) if record["query_kind"] == "entry" else None
                contexts[key] = record["continuous_length"], causal
        if manifest["session_indices"][0] >= stop:
            _check(not prefix, "later shard contains training rows")
            continue
        pin = manifest["files"]["labels.jsonl"]
        _check(pin["rows"] == manifest["files"]["queries.jsonl"]["rows"]
               and len(prefix) <= pin["rows"] - 2, "query/label geometry differs")
        with inputs.verified_stream(slot, pin) as stream:
            header = inputs.line(stream)
            _check(header["kind"] == "header" and header["before_sha256"] == manifest["before_sha256"]
                   and header["calendar_sha256"] == manifest["calendar_sha256"], "label stream identity differs")
            for expected in prefix:
                record = inputs.line(stream)
                key = record["key"]
                _check(record["kind"] == "labels" and (key, record["session"], record["symbol"]) == expected,
                       "permitted label prefix differs")
                if key not in training:
                    continue
                _check(key not in labels and record["training_labels"] is not None, "training target absent or duplicated")
                length, causal = contexts[key]
                action = ep.validate_saved_record(record, training[key], cutoff=cutoff,
                    session_length=length, label_query=causal)
                labels[key] = original.decode_labels(record["training_labels"])
                fills[key] = {"key": key, "immediate": action[0], "wait3": action[1],
                    "immediate_matured_session": record["session"] if action[0] is not None else None,
                    "wait3_matured_session": record["session"] if action[1] is not None else None}
            if len(prefix) + 2 == pin["rows"]:
                footer = inputs.line(stream)
                _check(footer["kind"] == "footer" and footer["data_rows"] == len(prefix) and not stream.read(1),
                       "full training prefix footer differs")
    _check(set(labels) == set(training), "training target roster incomplete")
    train_rows = tuple(training[key] for key in sorted(training))
    eval_rows = tuple(evaluation[key] for key in sorted(evaluation))
    _check(data.training == train_rows, "training queries differ")
    _check(data.evaluation == eval_rows, "prediction queries differ")
    for actual, expected, name in ((data.targets, tuple(labels[key] for key in sorted(training)), "training labels"),
        (data.action_targets, tuple(fills[key] for key in sorted(training)), "action fills")):
        prior._same(prior._plain(actual), prior._plain(expected), name)
    expected_support = {"counts": dict(sorted(counts.items())), "training_rows": len(train_rows),
        "evaluation_rows": len(eval_rows), "training_seal": _support_hash(train_rows) if train_rows else None,
        "evaluation_seal": _support_hash(eval_rows) if eval_rows else None, "evaluation_labels_read": False}
    prior._same(data.support, expected_support, "inner support")
    return {"verified": True, "training_rows": len(train_rows), "predictions": len(eval_rows),
            "evaluation_labels_read": False, "original_cash_target_adapter_reused": True}


def verify_fit(result, data):
    """Reconstruct full-J/direct weights, normalization, diagnostics and draws."""
    stop, end = _bounds(data)
    cutoff = data.calendar[stop]
    expected = {"schema": "ER-inner-model-unit-v1", "arm": "direct", "kind": "tcn",
        "scope": "common_chart_peer", "inner": data.inner, "training_sessions": list(data.calendar[:stop]),
        "evaluation_sessions": list(data.calendar[stop:end]), "support": data.support}
    _check(all(result.get(key) == value for key, value in expected.items()), "inner model identity differs")
    rows = tuple(sorted(data.training, key=lambda row: row.key))
    labels, fills = {r.key: r for r in data.targets}, {r.key: r for r in data.action_targets}
    _check(len(rows) == len(labels) == len(data.targets) == len(fills) == len(data.action_targets)
           and {r.key for r in rows} == set(labels) == set(fills), "training identity differs")
    _check(all(r.session in data.calendar[stop:end] for r in data.evaluation), "forecast chronology differs")
    for name in ("predictions", "components"):
        _check([r["key"] for r in result[name]] == [r.key for r in data.evaluation]
               and result[name + "_sha256"] == prior._hash(result[name]), "forecast roster/seal differs")
    artifact = result["artifact"]
    if not rows:
        _check(artifact is None and result["fit_artifacts"] == 0 and result["artifact_sha256"] is None
               and result["status"] == "unsupported_empty_training", "empty training invented model")
    else:
        _check(result["status"] == "completed" and result["fit_artifacts"] == 1
               and result["artifact_sha256"] == prior._hash(artifact), "artifact publication differs")
        _check(artifact["schema"] == "EP-factorized-model-v1" and artifact["arm"] == "direct"
               and artifact["mode"] == "chart_peer" and artifact["parameters"] == prior.PARAMETERS,
               "unchanged EP-direct learner contract differs")
        _check(artifact["common_support_sha256"] == data.support["training_seal"] == _support_hash(rows)
               and artifact["training_rows"] == len(rows) and artifact["eval_first_session"] == cutoff
               and artifact["training_sessions"] == sorted({r.session for r in rows})
               and artifact["training_key_hashes"] == sorted(sha256(r.key.encode()).hexdigest() for r in rows),
               "artifact training population differs")
        for row in rows:
            label, action = labels[row.key], fills[row.key]
            _check(row.session < cutoff and (label.matured_session is None
                   or row.session <= label.matured_session < cutoff), "training target chronology differs")
            _check(row.peer is not None and all(v is not None for v in row.peer), "unsupported training peer")
            _check(all(getattr(label, h) is None for h in prior.HEADS[:4]) if row.state in prior.HELD_STATES
                   else label.remaining_gross is None, "head state applicability differs")
            for action_name in ("immediate", "wait3"):
                fill, maturity = getattr(action, action_name), getattr(action, action_name + "_matured_session")
                _check(fill is None or type(fill) is int and fill in (0, 1) and maturity is not None, "action fill differs")
                _check(maturity is None or row.session <= maturity < cutoff, "action fill maturity differs")
                _check(row.state not in prior.HELD_STATES or fill is None, "held action fill")
                value = getattr(label, "net_" + action_name)
                _check(fill != 0 or value is None or Fraction(value) == 0, "no-fill target is nonzero")
            _check(label.fill is None or action.immediate is None or label.fill == action.immediate, "immediate fill differs")
        normalization = {"sequence": prior._moments((step for r in rows for step in r.sequence), 13),
            "static": prior._moments((r.static for r in rows), 16),
            "position": prior._moments((r.position or (None,) * 3 for r in rows), 3),
            "peer": prior._moments((r.peer for r in rows), 3),
            "policy": "training-only channel-shared moments; missing standardized=0 plus per-value missing flag"}
        prior._same(artifact["normalization"], normalization, "feature normalization")
        joint = [i for i, r in enumerate(rows) if r.state not in prior.HELD_STATES
                 and all(getattr(labels[r.key], h) is not None for h in prior.VALUES)
                 and fills[r.key].immediate is not None and fills[r.key].wait3 is not None]
        joint_set, bases, trainable, statuses = set(joint), {}, [], {}
        for head in prior.SIX_HEADS:
            raw = [getattr(fills[r.key], "immediate" if head == "fill" else "wait3")
                   if head in ("fill", "fill_wait3") else getattr(labels[r.key], head) for r in rows]
            indices = joint if head in (*prior.VALUES, "fill", "fill_wait3") else [i for i, v in enumerate(raw) if v is not None]
            weights = [0.0] * len(rows)
            for i, weight in zip(indices, prior._hierarchy([rows[i] for i in indices])):
                weights[i] = weight
            bases[head] = weights
            values = [prior._number(raw[i]) for i in indices]
            clusters, dates = len({rows[i].cluster_id for i in indices}), len({rows[i].session for i in indices})
            expected = {"known_rows": len(indices), "excluded_unknown_rows": len(rows) - len(indices),
                "known_episodes": len({rows[i].original_episode_id or rows[i].episode_id for i in indices}),
                "known_counterfactual_books": len({rows[i].episode_id for i in indices if rows[i].state in prior.HELD_STATES}),
                "known_clusters": clusters, "known_dates": dates, "low_support": clusters < 200 or dates < 30,
                "raw_known_rows": sum(v is not None for v in raw), "base_known_rows": len(indices),
                "known_zero_rows": sum(v == 0 for v in values), "conditional_on_fill": False,
                "dropped_outside_joint_rows": sum(v is not None and i not in joint_set for i, v in enumerate(raw))
                    if head in (*prior.VALUES, "fill", "fill_wait3") else 0}
            if not indices:
                expected.update(status="unsupported", reason="no_known_labels")
            elif min(len(indices), clusters, dates) < 2:
                expected.update(status="unsupported", reason="fewer_than_two_known_rows_clusters_or_dates")
            elif head in ("path_class", "fill", "fill_wait3") and len(set(values)) == 1:
                expected.update(status="constant_single_class", **{"class": int(values[0])})
            else:
                trainable.append(head)
                status = artifact["heads"][head]["status"]
                _check(status in ("trained_tcn", "failed"), "native head status differs")
                expected["status"] = status
                if head in (*prior.VALUES, "remaining_gross"):
                    mean = math.fsum(prior._number(raw[i]) * weights[i] for i in indices)
                    variance = math.fsum(weights[i] * (prior._number(raw[i]) - mean) ** 2 for i in indices)
                    expected["target_normalization"] = {"mean": mean, "std": math.sqrt(variance) or 1.0}
            expected["fit_input_known_rows"] = len(indices) if head in trainable else 0
            for field, value in expected.items():
                prior._same(artifact["heads"][head].get(field), value, head + "." + field)
            statuses[head] = expected["status"]
        support = artifact["action_training_support"]
        _check(support["joint_known_rows"] == len(joint) and support["excluded_from_joint_rows"] == len(rows) - len(joint)
               and support["joint_support_sha256"] == prior._hash([rows[i].key for i in joint], ascii=True), "joint cohort differs")
        for action in ("immediate", "wait3"):
            expected = {"joint_known_rows": len(joint),
                "weighted_fill_mass": math.fsum(bases["fill"][i] for i in joint if getattr(fills[rows[i].key], action) == 1),
                "no_fill_zero_rows": sum(getattr(fills[rows[i].key], action) == 0 for i in joint),
                "filled_zero_rows": sum(getattr(fills[rows[i].key], action) == 1 and Fraction(getattr(labels[rows[i].key], "net_" + action)) == 0 for i in joint),
                "filled_nonzero_rows": sum(getattr(fills[rows[i].key], action) == 1 and Fraction(getattr(labels[rows[i].key], "net_" + action)) != 0 for i in joint),
                "unknown_fill_rows": sum(getattr(fills[r.key], action) is None for r in rows if r.state not in prior.HELD_STATES),
                "fill_known_cash_unknown_rows": sum(r.state not in prior.HELD_STATES and getattr(fills[r.key], action) is not None
                    and getattr(labels[r.key], "net_" + action) is None for r in rows),
                "joint_excluded_fill_known_rows": sum(r.state not in prior.HELD_STATES and i not in joint_set
                    and getattr(fills[r.key], action) is not None for i, r in enumerate(rows))}
            prior._same(support["zero_decomposition"][action], expected, "action support." + action)
        _verify_diagnostics(artifact, rows, bases, trainable, statuses)
    for query, forecast, component in zip(data.evaluation, result["predictions"], result["components"]):
        _check(forecast["session"] == component["session"] == query.session
               and set(forecast["predictions"]) == set(prior.HEADS)
               and set(component["components"]) == {"fill_wait3", "conditional_net_immediate", "conditional_net_wait3"},
               "forecast component identity differs")
        _check(all(component["components"]["conditional_" + head] is None for head in prior.VALUES),
               "direct forecast invented conditional payoff")
        for value in (forecast["predictions"]["fill"], component["components"]["fill_wait3"]):
            _check(value is None or 0 <= prior._number(value) <= 1, "invalid fill probability")
    return {"verified": True, "training_rows": len(rows), "predictions": len(data.evaluation),
            "native_optimizer_reimplemented": False}


def _verify_diagnostics(artifact, rows, bases, trainable, statuses):
    active = [weights for weights in bases.values() if any(weights)]
    proposal = [math.fsum(weights[i] for weights in active) / len(active) for i in range(len(rows))] if active else []
    diagnostics = artifact["training_diagnostics"]
    _check(diagnostics["proposal_sha256"] == prior._hash(proposal, ascii=True), "sampling proposal differs")
    identities = [prior._hash([r.key, r.session, r.symbol, r.episode_id, r.cluster_id,
                              r.decision_offset, r.family, r.state], ascii=True) for r in rows]
    sampled = sorted(range(len(rows)), key=lambda i: (identities[i], rows[i].key))[:512]
    hashes = [identities[i] for i in sampled]
    _check(diagnostics["sample_identity_sha256"] == hashes and diagnostics["sampled_rows"] == len(sampled)
           and diagnostics["sample_sha256"] == prior._hash(hashes, ascii=True), "diagnostic sample differs")
    trained = [head for head in trainable if statuses[head] == "trained_tcn"]
    expected = {"fit_head_count": len(trained), "fit_attempt_head_count": len(trainable),
        "native_fit_head_count": len(trained), "native_joint_fit_count": int(bool(trained)),
        "native_updates": prior.UPDATES if trained else 0,
        "constant_head_count": sum(s == "constant_single_class" for s in statuses.values()),
        "unsupported_head_count": sum(s == "unsupported" for s in statuses.values()),
        "failed_head_count": len(trainable) - len(trained)}
    for field, value in expected.items():
        prior._same(artifact[field], value, field)
    if not trained:
        return
    _check(len(trained) == len(trainable) and diagnostics["status"] == "complete"
           and diagnostics["completed_updates"] == prior.UPDATES, "partial native update accounting differs")
    rng, digest, cumulative = random.Random(prior.SEED), sha256(), list(accumulate(proposal))
    hits, sampled_heads = Counter(), defaultdict(set)
    for _ in range(prior.UPDATES):
        indices = rng.choices(range(len(rows)), cum_weights=cumulative, k=prior.BATCH_SIZE)
        digest.update(prior._encode(indices, ascii=True) + b"\n")
        for head in trainable:
            known = [i for i in indices if bases[head][i] > 0]
            hits[head] += bool(known)
            sampled_heads[head].update(known)
    _check(diagnostics["sampled_indices_sha256"] == digest.hexdigest(), "native sample sequence differs")
    for head in trainable:
        observed = artifact["heads"][head]
        _check(observed["updates_with_known_target"] == hits[head]
               and observed["sampled_training_rows"] == len(sampled_heads[head])
               and observed["sampled_training_clusters"] == len({rows[i].cluster_id for i in sampled_heads[head]}),
               "native sample support differs")
