"""ER's two fixed early chronological EP-direct fits; no execution authority.

Only the caller admits private inputs and seals publications. In particular it
must finish sealing one inner unit before loading the next training prefix.
The original EP learner, six heads and all-family/held roster are unchanged.
"""

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256

from research import intraday_event_factorized_model as learner
from research import intraday_event_factorized_workflow as ep
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as original
from research import intraday_event_models as models


SPLITS = ((0, 49, 73), (0, 73, 97))
SCOPE = "common_chart_peer"


def _require(condition, message):
    if not condition:
        raise ValueError("ER inner OOF: " + message)


def _hash(value):
    return sha256(original.encode(value)).hexdigest()


def _calendar(calendar, inner):
    calendar = tuple(calendar)
    _require(type(inner) is int and inner in range(2), "fixed inner split required")
    _require(len(calendar) == 257 and list(calendar) == sorted(set(calendar)),
             "complete original calendar required")
    for day in calendar:
        models._day(day)
    return calendar


@dataclass(frozen=True)
class InnerData:
    calendar: tuple
    inner: int
    scope: str
    training: tuple
    targets: tuple
    evaluation: tuple
    action_targets: tuple
    support: dict


def collect(calendar, inner, query_records, training_label_records):
    """Decode only a separately bounded training-label prefix, never OOF labels."""
    calendar = _calendar(calendar, inner)
    _, stop, end = SPLITS[inner]
    cutoff = calendar[stop]
    train_days, test_days, all_days = set(calendar[:stop]), set(calendar[stop:end]), set(calendar)
    training, evaluation, seen, contexts, counts = [], [], set(), {}, Counter()
    shared_scalars = {}

    def convert(value):
        if type(value) is not str:
            return original.number(value)
        shared = shared_scalars.get(value)
        if shared is not None:
            return shared
        decoded = original.number(value)
        if len(shared_scalars) < original.MAX_SHARED_SCALARS:
            shared_scalars[value] = decoded
        return decoded

    for record in query_records:
        if record.get("kind") != "query":
            continue
        day = record["session"]
        _require(day in all_days, "query escaped the original calendar")
        if day not in train_days and day not in test_days:
            continue
        part = "training" if day in train_days else "evaluation"
        counts[part + ":original"] += 1
        _require(record["key"] not in seen, "duplicate original query")
        seen.add(record["key"])
        if record["query"] is None:
            counts[part + ":unknown_chart"] += 1
            continue
        row = original.decode_query(record["query"], convert)
        _require((row.key, row.session, row.symbol) == (record["key"], day, record["symbol"]),
                 "query wrapper identity differs")
        peer_known = row.peer is not None and all(value is not None for value in row.peer)
        counts[part + (":peer_known" if peer_known else ":peer_unknown")] += 1
        if not peer_known:
            continue
        (training if part == "training" else evaluation).append(row)
        _require(len(training) <= models.MAX_ROWS and len(evaluation) <= models.MAX_ROWS,
                 "complete population exceeds the original row bound")
        if part == "training":
            causal = inputs._decode_label_query(record["label_query"]) if record["query_kind"] == "entry" else None
            contexts[row.key] = row, record["continuous_length"], causal
    labels, actions = {}, {}
    for record in training_label_records:
        if record.get("kind") != "labels":
            continue
        _require(record["session"] in train_days, "evaluation target reached the inner fitting boundary")
        if record["key"] not in contexts:
            continue
        key = record["key"]
        _require(key not in labels and record["training_labels"] is not None,
                 "training labels missing or duplicated")
        row, length, causal = contexts[key]
        fills = ep.validate_saved_record(record, row, cutoff=cutoff, session_length=length, label_query=causal)
        labels[key] = original.decode_labels(record["training_labels"])
        actions[key] = learner.ActionFills(key, *fills,
            *(record["session"] if value is not None else None for value in fills))
    _require(set(labels) == set(contexts), "complete training label roster required")
    training = tuple(sorted(training, key=lambda row: row.key))
    evaluation = tuple(sorted(evaluation, key=lambda row: row.key))
    for rows in (training, evaluation):
        if rows:
            models._queries(rows)
    return InnerData(calendar, inner, SCOPE, training, tuple(labels[row.key] for row in training),
        evaluation, tuple(actions[row.key] for row in training),
        {"counts": dict(sorted(counts.items())), "training_rows": len(training), "evaluation_rows": len(evaluation),
         "training_seal": models.support_seal(training) if training else None,
         "evaluation_seal": models.support_seal(evaluation) if evaluation else None, "evaluation_labels_read": False})


def load_inner(shards, inner):
    """Reuse authenticated streams with a query-derived inner training prefix."""
    _require(type(inner) is int and inner in range(2), "fixed inner split required")
    shards = tuple(shards)
    calendar = inputs.calendar_from_coordinates(shards)
    _, stop, end = SPLITS[inner]
    prefixes = []

    def queries():
        for slot, manifest in shards:
            first, _ = manifest["session_indices"]
            if first >= end:
                break
            rows = inputs.query_rows(slot, manifest)
            if first < stop:
                prefix = []
                yield from inputs._training_prefix(rows, calendar[stop], prefix)
                prefixes.append((slot, manifest, prefix))
            else:
                yield from rows

    def labels():
        for slot, manifest, prefix in prefixes:
            yield from inputs._labels_for_prefix(slot, manifest, prefix)

    return collect(calendar, inner, queries(), labels())


def _validate_data(data):
    _require(type(data) is InnerData and data.scope == SCOPE, "fixed inner data required")
    calendar = _calendar(data.calendar, data.inner)
    _, stop, end = SPLITS[data.inner]
    _require(all(row.session in calendar[:stop] for row in data.training), "inner training date differs")
    _require(all(row.session in calendar[stop:end] for row in data.evaluation), "inner forecast date differs")
    _require(all(row.peer is not None and all(value is not None for value in row.peer) for row in data.evaluation),
             "inner forecast lacks original common peer support")
    _require(data.support["training_rows"] == len(data.training)
             and data.support["evaluation_rows"] == len(data.evaluation)
             and data.support["training_seal"] == (models.support_seal(data.training) if data.training else None)
             and data.support["evaluation_seal"] == (models.support_seal(data.evaluation) if data.evaluation else None)
             and data.support["evaluation_labels_read"] is False, "inner support seal differs")
    if data.training:
        rows, labels = models.validate_training(data.training, data.targets, calendar[stop],
            mode="chart_peer", support_binding=data.support["training_seal"])
        learner.validate_action_fills(rows, labels, data.action_targets, calendar[stop])
    else:
        _require(not data.targets and not data.action_targets, "empty training contains targets")
    return stop, end


def _predictions(data, artifact):
    if artifact is None:
        predictions, components = [], []
        for query in data.evaluation:
            predictions.append({"key": query.key, "session": query.session,
                "query_status": "supported_features", "predictions": dict.fromkeys(models.HEADS),
                "head_status": {head: "unsupported_empty_training" if models.head_applicable(query, head)
                                else "not_applicable_to_state" for head in models.HEADS}})
            components.append({"key": query.key, "session": query.session,
                "components": dict.fromkeys(("fill_wait3", "conditional_net_immediate", "conditional_net_wait3")),
                "component_status": dict.fromkeys(learner.HEADS, "unsupported_empty_training")})
        return predictions, components
    predictions, components = [], []
    for first in range(0, len(data.evaluation), original.BATCH):
        forecasts, parts = ep._separate_predictions(learner.predict(artifact, data.evaluation[first:first + original.BATCH]))
        predictions.extend(forecasts)
        components.extend(parts)
    return predictions, components


def fit_unit(data, *, before_fit):
    """Fit one unchanged EP-direct model after its caller's durable fit marker."""
    stop, end = _validate_data(data)
    _require(callable(before_fit), "durable fit-start callback required")
    artifact = None
    if data.training:
        before_fit()
        artifact = learner.fit("direct", data.training, data.targets, data.action_targets, data.calendar[stop],
            mode="chart_peer", support_binding=data.support["training_seal"])
    predictions, components = _predictions(data, artifact)
    _require([row["key"] for row in predictions] == [row.key for row in data.evaluation],
             "inner inference silently changed its causal population")
    return {"schema": "ER-inner-model-unit-v1", "arm": "direct", "kind": "tcn", "scope": SCOPE,
        "inner": data.inner, "training_sessions": list(data.calendar[:stop]),
        "evaluation_sessions": list(data.calendar[stop:end]), "support": data.support,
        "status": "completed" if artifact is not None else "unsupported_empty_training", "artifact": artifact,
        "predictions": predictions, "components": components, "fit_artifacts": int(artifact is not None),
        "artifact_sha256": None if artifact is None else _hash(artifact),
        "predictions_sha256": _hash(predictions), "components_sha256": _hash(components)}


def verify_unit(result, data):
    """Replay saved native inference; never fit or read an OOF target."""
    stop, end = _validate_data(data)
    expected = {"schema": "ER-inner-model-unit-v1", "arm": "direct", "kind": "tcn", "scope": SCOPE,
        "inner": data.inner, "training_sessions": list(data.calendar[:stop]),
        "evaluation_sessions": list(data.calendar[stop:end]), "support": data.support}
    _require(all(result.get(key) == value for key, value in expected.items()), "saved inner identity differs")
    artifact = result["artifact"]
    if not data.training:
        _require(result["status"] == "unsupported_empty_training" and artifact is None
                 and result["fit_artifacts"] == 0 and result["artifact_sha256"] is None,
                 "empty training fabricated a model")
    else:
        _require(result["status"] == "completed" and result["fit_artifacts"] == 1
                 and artifact is not None and result["artifact_sha256"] == _hash(artifact)
                 and artifact["arm"] == "direct" and artifact["mode"] == "chart_peer"
                 and artifact["training_rows"] == len(data.training)
                 and artifact["eval_first_session"] == data.calendar[stop]
                 and artifact["training_sessions"] == sorted({row.session for row in data.training})
                 and artifact["training_key_hashes"] == sorted(sha256(row.key.encode()).hexdigest() for row in data.training)
                 and artifact["common_support_sha256"] == data.support["training_seal"], "saved inner artifact identity differs")
    predictions, components = _predictions(data, artifact)
    for name, expected_rows in (("predictions", predictions), ("components", components)):
        _require(original.encode(result[name]) == original.encode(expected_rows)
                 and result[name + "_sha256"] == _hash(expected_rows), "saved forecast or component differs from replay")
    return {"verified": True, "predictions": len(predictions), "native_optimizer_reimplemented": False}


def invented_oof(data):
    """Target-free qualification fixture; caller must restrict it to invented runs.

    These records are explicitly synthetic consumer inputs, never claims about
    the separately qualified native inner fit or historical EP model outputs.
    """
    base = data.base
    _require(type(base.fold) is int and base.fold in range(4) and base.scope == SCOPE,
             "original invented outer fold required")
    cutoff = base.calendar[original.FOLDS[base.fold][1]]
    result = []
    for query in base.training:
        if not base.calendar[49] <= query.session < cutoff:
            continue
        values = dict.fromkeys(models.HEADS)
        status = dict.fromkeys(models.HEADS, "invented_not_estimated")
        if query.state not in models.HELD_STATES:
            for head in models.Q_HEADS:
                value = int.from_bytes(sha256((query.key + ":" + head).encode()).digest()[:4], "big") % 2001 - 1000
                values[head] = value / 100_000
                status[head] = "invented_target_free_oof"
        result.append({"key": query.key, "session": query.session, "query_status": "supported_features",
                       "predictions": values, "head_status": status})
    return tuple(result)
