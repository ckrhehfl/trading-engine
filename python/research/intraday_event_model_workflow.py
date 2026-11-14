"""EL chronological model units over sealed event data; no acquisition authority.

The closed caller authenticates all nine audited shards before passing their
manifest-bound readers. Evaluation labels are never opened by this module.
"""

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
import math

from research import intraday_event_models as models
from research import intraday_event_sequence_model as sequence
from research import intraday_event_tabular_fit as tabular


SCOPES = ("full_chart", "common_chart", "common_chart_peer")
KINDS = ("empirical", "lightgbm", "tcn")
FOLDS = ((0, 97, 137), (0, 137, 177), (0, 177, 217), (0, 217, 257))
BATCH = 512
MAX_SHARED_SCALARS = 65_536


def supported_in_scope(query, scope):
    if scope not in SCOPES:
        raise ValueError("Unregistered EL feature scope")
    return query is not None and (scope == "full_chart" or (
        query.peer is not None and all(value is not None for value in query.peer)))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def number(value):
    if value is None:
        return None
    if type(value) not in (str, int, float):
        raise ValueError("Fixed statistical scalar required")
    result = float(Fraction(value)) if type(value) is str else float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite statistical feature")
    return result


def decode_query(value, convert=number):
    """Exact serialized features cross to float only at the learner boundary."""
    values = dict(value)
    values["sequence"] = tuple(tuple(convert(v) for v in step) for step in value["sequence"])
    for field in ("static", "peer", "position"):
        values[field] = None if value[field] is None else tuple(convert(v) for v in value[field])
    return models.Query(**values)


def decode_labels(value):
    return models.TrainingLabels(**{**value, **{head: number(value[head])
        for head in ("net_immediate", "net_wait3", "remaining_gross")}})


@dataclass(frozen=True)
class ModelData:
    calendar: tuple
    fold: int
    scope: str
    training: tuple
    targets: tuple
    evaluation: tuple
    support: dict


def collect(calendar, fold, scope, query_records, training_label_records):
    """Receive a separately restricted training-label iterator, never test labels.

    All causal evaluation rows survive regardless of later outcome availability.
    Common scopes use exactly the same peer-known query roster, before labels.
    """
    calendar = tuple(calendar)
    if (len(calendar) != 257 or list(calendar) != sorted(set(calendar))
            or type(fold) is not int or fold not in range(4) or scope not in SCOPES):
        raise ValueError("Fixed original calendar, fold and scope required")
    for day in calendar:
        models._day(day)
    _, stop, end = FOLDS[fold]
    train_days, test_days = set(calendar[:stop]), set(calendar[stop:end])
    allowed_days = set(calendar)
    training, evaluation, keys, counts = [], [], set(), Counter()
    shared_scalars = {}

    def convert(value):
        # The sealed wire format repeats exact scalar strings across windows.
        # Share their immutable float objects without changing any value.
        if type(value) is not str:
            return number(value)
        shared = shared_scalars.get(value)
        if shared is not None:
            return shared
        decoded = number(value)
        if len(shared_scalars) < MAX_SHARED_SCALARS:
            shared_scalars[value] = decoded
        return decoded

    for record in query_records:
        if record.get("kind") != "query":
            continue
        day = record["session"]
        if day not in allowed_days:
            raise ValueError("Query escaped original calendar")
        if day not in train_days and day not in test_days:
            continue
        part = "training" if day in train_days else "evaluation"
        counts[part + ":original"] += 1
        if record["key"] in keys:
            raise ValueError("Duplicate original query")
        keys.add(record["key"])
        if record["query"] is None:
            counts[part + ":unknown_chart"] += 1
            continue
        row = decode_query(record["query"], convert)
        if row.key != record["key"] or row.session != day or row.symbol != record["symbol"]:
            raise ValueError("Query wrapper identity differs")
        peer_known = row.peer is not None and all(v is not None for v in row.peer)
        counts[part + (":peer_known" if peer_known else ":peer_unknown")] += 1
        if not supported_in_scope(row, scope):
            continue
        (training if part == "training" else evaluation).append(row)
        if len(training) > models.MAX_ROWS or len(evaluation) > models.MAX_ROWS:
            raise ValueError("Complete model population exceeds registered memory row bound")
    wanted = {row.key for row in training}
    targets = {}
    for record in training_label_records:
        if record.get("kind") != "labels":
            continue
        if record["session"] not in train_days:
            raise ValueError("Evaluation label reached the fitting boundary")
        if record["key"] not in wanted:
            continue
        if record["key"] in targets or record["training_labels"] is None:
            raise ValueError("Training labels missing or duplicated")
        label = decode_labels(record["training_labels"])
        if label.key != record["key"]:
            raise ValueError("Training label wrapper differs")
        targets[label.key] = label
    if set(targets) != wanted:
        raise ValueError("Training target roster incomplete")
    training, evaluation = tuple(sorted(training, key=lambda row: row.key)), tuple(sorted(evaluation, key=lambda row: row.key))
    for rows in (training, evaluation):
        if rows:
            models._queries(rows)
    return ModelData(calendar, fold, scope, training, tuple(targets[row.key] for row in training),
        evaluation, {"counts": dict(sorted(counts.items())),
                     "training_rows": len(training), "evaluation_rows": len(evaluation),
                     "training_seal": models.support_seal(training) if training else None,
                     "evaluation_seal": models.support_seal(evaluation) if evaluation else None,
                     "evaluation_labels_read": False})


def fit_unit(kind, data):
    if kind not in KINDS or type(data) is not ModelData:
        raise ValueError("Registered EL fit required")
    _, start, end = FOLDS[data.fold]
    base = {"schema": "EL-model-unit-v1", "kind": kind, "scope": data.scope,
            "fold": data.fold, "training_sessions": list(data.calendar[:start]),
            "evaluation_sessions": list(data.calendar[start:end]), "support": data.support}
    if not data.training:
        return {**base, "status": "unsupported_empty_training", "artifact": None,
                "predictions": [], "fit_artifacts": 0}
    api = sequence if kind == "tcn" else models
    learner = sequence if kind == "tcn" else tabular
    artifact = learner.fit(kind, data.training, data.targets, data.calendar[start],
        mode="chart_peer" if data.scope == "common_chart_peer" else "chart",
        support_binding=data.support["training_seal"])
    predictions = []
    for first in range(0, len(data.evaluation), BATCH):
        predictions.extend(api.predict(artifact, data.evaluation[first:first + BATCH]))
    return {**base, "status": "completed", "artifact": artifact, "predictions": predictions,
            "fit_artifacts": 1, "artifact_sha256": sha256(encode(artifact)).hexdigest(),
            "predictions_sha256": sha256(encode(predictions)).hexdigest()}


def verify_unit(result, data):
    """Reconcile immutable cohorts and replay every saved prediction from weights.

    The native optimizer is not independently reimplemented here. Its new logic
    has separate source/invented checks; this audit checks the actual artifact,
    input/target identity, chronology, head supports and every prediction.
    """
    if (result["support"] != data.support or result["scope"] != data.scope
            or result["fold"] != data.fold or result["kind"] not in KINDS):
        raise ValueError("Saved model unit support differs")
    _, start, end = FOLDS[data.fold]
    if result["training_sessions"] != list(data.calendar[:start]) or result["evaluation_sessions"] != list(data.calendar[start:end]):
        raise ValueError("Saved chronological split differs")
    if not data.training:
        if result["status"] != "unsupported_empty_training" or result["artifact"] is not None or result["predictions"] or result["fit_artifacts"] != 0:
            raise ValueError("Empty cohort fabricated a fit")
        return {"verified": True, "predictions": 0, "native_optimizer_reimplemented": False}
    artifact = result["artifact"]
    if (result["status"] != "completed" or result["fit_artifacts"] != 1
            or result["artifact_sha256"] != sha256(encode(artifact)).hexdigest()
            or artifact["kind"] != result["kind"] or artifact["training_rows"] != len(data.training)
            or artifact["mode"] != ("chart_peer" if data.scope == "common_chart_peer" else "chart")
            or artifact["eval_first_session"] != data.calendar[start]
            or artifact["common_support_sha256"] != models.support_seal(data.training)
            or artifact["training_sessions"] != sorted({row.session for row in data.training})
            or artifact["training_key_hashes"] != sorted(sha256(row.key.encode()).hexdigest() for row in data.training)):
        raise ValueError("Saved artifact training identity differs")
    labels = {row.key: row for row in data.targets}
    for head in models.HEADS:
        selected = [row for row in data.training if models.training_head_known(labels[row.key], head)]
        actual = artifact["heads"][head]
        if (actual["known_rows"] != len(selected) or actual["known_dates"] != len({r.session for r in selected})
                or actual["known_clusters"] != len({r.cluster_id for r in selected})):
            raise ValueError("Saved per-head actual support differs")
    api = sequence if result["kind"] == "tcn" else models
    replay = []
    for first in range(0, len(data.evaluation), BATCH):
        replay.extend(api.predict(artifact, data.evaluation[first:first + BATCH]))
    if (encode(replay) != encode(result["predictions"])
            or result["predictions_sha256"] != sha256(encode(replay)).hexdigest()):
        raise ValueError("Saved forecast differs from full artifact replay")
    return {"verified": True, "predictions": len(replay), "native_optimizer_reimplemented": False}
