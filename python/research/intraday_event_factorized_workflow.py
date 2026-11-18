"""EP adapters over authenticated EL streams, with targets kept behind sealing.

This module has no market reader, execution replay or publication authority.
The closed runner admits the original shards, durably records every fit, and
seals BOTH arms' artifacts, forecasts and policies before consuming the lazy
evaluation-target iterator. All families and held queries remain in training.
"""

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256

from research import intraday_event_evaluation as evaluation
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as original
from research import intraday_event_models as models


ARMS = ("direct", "factorized")
SCOPE = "common_chart_peer"
FAMILY = "C_FIRSTPULLBACK"
FOLDS = original.FOLDS


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _exact(value):
    if value is None:
        return None
    _require(type(value) in (str, int) or isinstance(value, Fraction),
             "Exact saved target scalar required")
    return Fraction(value)


def _maturity(value):
    _require(value is None or type(value) is int and value >= 0,
             "Saved target maturity must be a nonnegative receipt clock")
    return value


def _cash_target(value, denominator, *, allow_unmatured_no_entry=False):
    """Check the original saved action, preserving unknown cash and known fill."""
    _require(type(value) is dict, "Saved action CashTarget required")
    cash, target = _exact(value["net_cash"]), _exact(value["value"])
    actual_d = _exact(value["denominator"])
    fill = value["fill"]
    _require(actual_d == denominator and (actual_d is None or actual_d >= 0),
             "Action target differs from original requested notional")
    _require(fill is None or type(fill) is int and fill in (0, 1), "Saved action fill must be binary or unknown")
    maturity, fill_maturity = _maturity(value["matured_offset"]), _maturity(value["fill_matured_offset"])
    outside_no_entry = allow_unmatured_no_entry and value["status"] == "no_entry" and maturity is None
    _require(fill is None or fill_maturity is not None or outside_no_entry, "Known fill requires its own maturity")
    _require(target is None or maturity is not None or outside_no_entry, "Known value requires cash maturity")
    _require(cash is None or maturity is not None or outside_no_entry, "Known cash requires maturity")
    if denominator is not None and denominator > 0:
        _require((target is None) == (cash is None)
                 and (target is None or target == cash / denominator), "Saved exact cash/value identity differs")
    if fill == 0:
        _require(cash in (None, Fraction()) and target in (None, Fraction()),
                 "A saved no-fill cannot have nonzero cash/value")
    return {"fill": fill if fill_maturity is not None else None,
            "value": target if maturity is not None else None,
            "net_cash": cash if maturity is not None else None,
            "denominator": denominator, "matured_offset": maturity,
            "fill_matured_offset": fill_maturity,
            "status": value["status"], "reason": value["reason"]}


@dataclass(frozen=True)
class _CausalReference:
    label_query: object


def validate_saved_record(record, query=None, *, cutoff=None, session_length=None, label_query=None):
    """Reconstruct the old TrainingLabels exactly before any float conversion.

    Action-specific F may be known while its final cash is unknown. A matured
    no-fill is an observed zero; absent or inadmissible actions stay unknown.
    """
    _require(record.get("kind") == "labels", "Original saved label wrapper required")
    day, key, target, supplied = record["session"], record["key"], record["targets"], record["training_labels"]
    models._day(day)
    if cutoff is not None:
        _require(day < cutoff, "Evaluation target reached the training boundary")
    if query is not None:
        _require((key, day, record["symbol"]) == (query.key, query.session, query.symbol),
                 "Saved target/query identity differs")
        _require((record["query_kind"] == "held") == (query.state in models.HELD_STATES),
                 "Saved task and query state differ")
    if session_length is not None:
        _require(type(session_length) is int and session_length > 0, "Original continuous session length required")
    if record["query_kind"] == "held":
        _require(target["schema"] == "EL-held-remaining-gross-v1", "Original held target schema required")
        maturity = _maturity(target["matured_offset"])
        expected = dict.fromkeys(models.HEADS)
        expected["remaining_gross"] = _exact(target["value"]) if maturity is not None else None
        fills = (None, None)
    else:
        _require(record["query_kind"] == "entry" and target["schema"] == "EL-separated-offline-targets-v1",
                 "Original entry target schema required")
        if query is not None:
            _require((target["event_id"], target["decision_offset"]) == (query.episode_id, query.decision_offset),
                     "Saved action target identity differs")
        admitted = target["entry_admissibility"] == "admissible"
        _require(target["entry_admissibility"] in ("admissible", "not_admissible", "unknown"),
                 "Unknown saved entry admissibility")
        denominator = _exact(target["original_requested_notional"])
        if label_query is not None:
            if query is not None:
                _require((label_query.event_id, label_query.family, label_query.state, label_query.decision_offset)
                         == (query.episode_id, query.family, query.state, query.decision_offset),
                         "Causal entry reference differs from its training query")
            reference = _CausalReference(label_query)
            rejection = evaluation._admission(reference)
            expected_admission = "admissible" if rejection is None else "unknown" if rejection == "reference_unknown" else "not_admissible"
            _require(target["entry_admissibility"] == expected_admission
                     and denominator == evaluation._denominator(reference)
                     and (target["event_id"], target["decision_offset"])
                     == (label_query.event_id, label_query.decision_offset),
                     "Saved target admission/denominator differs from the original causal reference")
        immediate = _cash_target(target["immediate"], denominator, allow_unmatured_no_entry=not admitted)
        wait3 = _cash_target(target["wait3"], denominator, allow_unmatured_no_entry=not admitted)
        path = target["path"]
        path_maturity = _maturity(path["matured_offset"])
        expected = {"path_class": path["value"] if path_maturity is not None else None,
                    "fill": immediate["fill"] if admitted and immediate["fill_matured_offset"] is not None else None,
                    "net_immediate": immediate["value"] if admitted and immediate["matured_offset"] is not None else None,
                    "net_wait3": wait3["value"] if admitted and wait3["matured_offset"] is not None else None,
                    "remaining_gross": None}
        eligible = admitted and denominator is not None and denominator > 0
        fills = tuple(action["fill"] if eligible and action["fill_matured_offset"] is not None else None
                      for action in (immediate, wait3))
    maturities = ([target["matured_offset"]] if record["query_kind"] == "held" else
        [target["path"]["matured_offset"], *(target[action][field]
          for action in ("immediate", "wait3") for field in ("matured_offset", "fill_matured_offset"))])
    if session_length is not None:
        _require(all(value is None or value < session_length for value in maturities),
                 "Saved target is not mature inside its original session")
    decision = query.decision_offset if query is not None else target.get("decision_offset")
    if decision is not None:
        _require(all(value is None or value >= decision for value in maturities),
                 "Saved target maturity predates its original decision")
    if supplied is None:
        _require(query is None, "Supported query lost its original TrainingLabels")
    else:
        _require(supplied["key"] == key and all(
            (_exact(supplied[head]) if head in (*models.Q_HEADS, "remaining_gross") else supplied[head]) == expected[head]
            for head in models.HEADS), "Saved TrainingLabels differ from exact original targets")
        expected_maturity = day if any(value is not None for value in expected.values()) else None
        _require(supplied["matured_session"] == expected_maturity, "Original label maturity differs")
        if cutoff is not None and expected_maturity is not None:
            _require(expected_maturity < cutoff, "Training target has not matured before evaluation")
    return fills


@dataclass(frozen=True)
class FactorizedData:
    base: original.ModelData
    action_targets: tuple


@dataclass(frozen=True)
class _TrainingIdentity:
    key: str
    session: str
    symbol: str
    episode_id: str
    decision_offset: int
    state: str
    family: str


def collect(calendar, fold, query_records, training_label_records):
    """Use the existing causal collector; intercept only its training labels."""
    from research import intraday_event_factorized_model as learner

    _require(type(fold) is int and fold in range(len(FOLDS)), "Original fold required")
    calendar = tuple(calendar)
    _require(len(calendar) == 257, "All original calendar dates required")
    cutoff = calendar[FOLDS[fold][1]]
    selected, action_targets = {}, {}

    def queries():
        for record in query_records:
            if record.get("kind") == "query" and record["session"] < cutoff and record["query"] is not None:
                raw = record["query"]
                if raw["peer"] is not None and all(value is not None for value in raw["peer"]):
                    # Do not retain a second 64x13 chart copy beside ModelData.
                    identity = _TrainingIdentity(*(raw[field] for field in
                        ("key", "session", "symbol", "episode_id", "decision_offset", "state", "family")))
                    length = record["continuous_length"]
                    causal = inputs._decode_label_query(record["label_query"]) if record["query_kind"] == "entry" else None
                    selected[identity.key] = identity, length, causal
            yield record

    def labels():
        for record in training_label_records:
            if record.get("kind") == "labels":
                _require(record["session"] < cutoff, "Evaluation target reached the training boundary")
                selected_query = selected.get(record["key"])
                if selected_query is not None:
                    query, length, causal = selected_query
                    fills = validate_saved_record(record, query, cutoff=cutoff, session_length=length, label_query=causal)
                    action_targets[query.key] = learner.ActionFills(query.key, *fills,
                        *(record["session"] if value is not None else None for value in fills))
            yield record

    base = original.collect(calendar, fold, SCOPE, queries(), labels())
    _require(set(action_targets) == {query.key for query in base.training}, "Complete action-fill training roster required")
    return FactorizedData(base, tuple(action_targets[query.key] for query in base.training))


def load_fold(shards, fold):
    """Read all original queries and only the causally identified label prefix."""
    _require(type(fold) is int and fold in range(len(FOLDS)), "Original fold required")
    shards = tuple(shards)
    calendar = inputs.calendar_from_coordinates(shards)
    _, start, end = FOLDS[fold]
    prefixes = []

    def queries():
        for slot, manifest in shards:
            first, _ = manifest["session_indices"]
            if first >= end:
                break
            rows = inputs.query_rows(slot, manifest)
            if first < start:
                prefix = []
                yield from inputs._training_prefix(rows, calendar[start], prefix)
                prefixes.append((slot, manifest, prefix))
            else:
                yield from rows

    def labels():
        for slot, manifest, prefix in prefixes:
            yield from inputs._labels_for_prefix(slot, manifest, prefix)

    return collect(calendar, fold, queries(), labels())


def _separate_predictions(rows):
    predictions, components = [], []
    for row in rows:
        _require(set(row) == {"key", "session", "query_status", "predictions", "head_status",
                             "components", "component_status"}, "EP native prediction schema differs")
        predictions.append({key: row[key] for key in ("key", "session", "query_status", "predictions", "head_status")})
        components.append({key: row[key] for key in ("key", "session", "components", "component_status")})
    return predictions, components


def _empty_predictions(data):
    predictions, components = [], []
    for query in data.base.evaluation:
        predictions.append({"key": query.key, "session": query.session,
            "query_status": "supported_features", "predictions": dict.fromkeys(models.HEADS),
            "head_status": {head: "unsupported_empty_training" if models.head_applicable(query, head)
                            else "not_applicable_to_state" for head in models.HEADS}})
        components.append({"key": query.key, "session": query.session,
            "components": dict.fromkeys(("fill_wait3", "conditional_net_immediate", "conditional_net_wait3")),
            "component_status": {head: "unsupported_empty_training" for head in (*models.HEADS, "fill_wait3")}})
    return predictions, components


def fit_unit(arm, data, *, before_fit):
    """Fit once after the caller's durable start callback; forecast every query."""
    from research import intraday_event_factorized_model as learner

    _require(arm in ARMS and type(data) is FactorizedData and callable(before_fit),
             "Registered arm, fold and durable fit-start callback required")
    base = data.base
    _, start, end = FOLDS[base.fold]
    common = {"schema": "EP-model-unit-v1", "arm": arm, "kind": "tcn", "scope": SCOPE,
              "fold": base.fold, "training_sessions": list(base.calendar[:start]),
              "evaluation_sessions": list(base.calendar[start:end]), "support": base.support}
    artifact = None
    if not base.training:
        predictions, components = _empty_predictions(data)
        status = "unsupported_empty_training"
    else:
        before_fit()
        artifact = learner.fit(arm, base.training, base.targets, data.action_targets, base.calendar[start],
                               mode="chart_peer", support_binding=base.support["training_seal"])
        predictions, components = [], []
        for first in range(0, len(base.evaluation), original.BATCH):
            forecast, parts = _separate_predictions(learner.predict(artifact, base.evaluation[first:first + original.BATCH]))
            predictions.extend(forecast)
            components.extend(parts)
        status = "completed"
    _require([row["key"] for row in predictions] == [query.key for query in base.evaluation],
             "EP forecast silently changed the original inference population")
    return {**common, "status": status, "artifact": artifact, "predictions": predictions,
            "components": components, "fit_artifacts": int(artifact is not None),
            "artifact_sha256": None if artifact is None else sha256(original.encode(artifact)).hexdigest(),
            "predictions_sha256": sha256(original.encode(predictions)).hexdigest(),
            "components_sha256": sha256(original.encode(components)).hexdigest()}


def verify_unit(result, data):
    """Replay all forecasts from saved weights without fitting or reading targets.

    Independent support/weight/composition checks belong to the separate audit.
    This check deliberately does not claim to reimplement the optimizer.
    """
    from research import intraday_event_factorized_model as learner

    _require(type(data) is FactorizedData and result["arm"] in ARMS, "EP saved unit required")
    base = data.base
    _, start, end = FOLDS[base.fold]
    _require(result["schema"] == "EP-model-unit-v1" and result["kind"] == "tcn"
             and result["scope"] == SCOPE and result["fold"] == base.fold
             and result["support"] == base.support
             and result["training_sessions"] == list(base.calendar[:start])
             and result["evaluation_sessions"] == list(base.calendar[start:end]),
             "EP saved fit population differs")
    artifact = result["artifact"]
    if not base.training:
        _require(result["status"] == "unsupported_empty_training" and artifact is None
                 and result["fit_artifacts"] == 0 and result["artifact_sha256"] is None,
                 "Empty training fabricated a model")
        predictions, components = _empty_predictions(data)
    else:
        _require(result["status"] == "completed" and result["fit_artifacts"] == 1
                 and result["artifact_sha256"] == sha256(original.encode(artifact)).hexdigest()
                 and artifact["arm"] == result["arm"] and artifact["mode"] == "chart_peer"
                 and artifact["training_rows"] == len(base.training)
                 and artifact["eval_first_session"] == base.calendar[start]
                 and artifact["training_sessions"] == sorted({query.session for query in base.training})
                 and artifact["training_key_hashes"] == sorted(sha256(query.key.encode()).hexdigest() for query in base.training)
                 and artifact["common_support_sha256"] == base.support["training_seal"],
                 "Saved EP artifact identity differs")
        predictions, components = [], []
        for first in range(0, len(base.evaluation), original.BATCH):
            forecast, parts = _separate_predictions(learner.predict(artifact, base.evaluation[first:first + original.BATCH]))
            predictions.extend(forecast)
            components.extend(parts)
    for name, expected in (("predictions", predictions), ("components", components)):
        _require(original.encode(result[name]) == original.encode(expected)
                 and result[name + "_sha256"] == sha256(original.encode(expected)).hexdigest(),
                 "Saved EP forecast or component differs from artifact replay")
    return {"verified": True, "predictions": len(predictions), "native_optimizer_reimplemented": False}


def decision_roster(shards, data):
    """Retain every original C onset and coalesced entry, including no chart."""
    _require(type(data) is FactorizedData, "EP fold data required")
    roster = inputs.decision_roster(shards, data.base)
    return inputs.DecisionRoster(tuple(e for e in roster.episodes if e.family == FAMILY),
                                 tuple(c for c in roster.candidates if c.episode.family == FAMILY))


def serialize_policy(policy):
    evaluation.verify_policy(policy)
    return evaluation._plain(policy)


def freeze_policy(data, roster, result):
    """The original first-positive capped-entry/fixed-exit commitment rule."""
    _require(type(data) is FactorizedData and result["arm"] in ARMS
             and result["fold"] == data.base.fold and result["support"] == data.base.support,
             "Matching EP fit unit required")
    _require(result["predictions_sha256"] == sha256(original.encode(result["predictions"])).hexdigest(),
             "Policy forecasts differ from the saved prediction seal")
    _require(all(e.family == FAMILY for e in roster.episodes), "Only original C policy population is registered")
    wanted = {candidate.key for candidate in roster.candidates if candidate.query is not None}
    predictions = tuple(row for row in result["predictions"] if row["key"] in wanted)
    return evaluation.freeze_policy(roster.episodes, roster.candidates, predictions,
        control="model", model="EP-" + result["arm"], scope=SCOPE,
        prediction_binding=result["predictions_sha256"])


def evaluation_records(shards, data, roster):
    """Lazy full C label wrappers; the caller must seal both arms first.

    This includes chart/peer-unsupported causal candidates. It never substitutes
    a later known label for an earlier unknown choice and never opens bars.
    """
    _require(type(data) is FactorizedData, "EP fold data required")
    wanted = {candidate.key: candidate for candidate in roster.candidates}
    _require(len(wanted) == len(roster.candidates), "Duplicate causal evaluation key")
    seen = set()
    for slot, manifest in inputs._test_shards(shards, data.base.fold):
        for row in inputs._rows(slot, manifest, "labels.jsonl", "labels"):
            key = row["key"]
            if key not in wanted:
                continue
            candidate = wanted[key]
            _require(key not in seen and row["query_kind"] == "entry"
                     and (row["session"], row["symbol"]) == (candidate.episode.session, candidate.episode.symbol),
                     "Original C evaluation target identity differs")
            _require((row["targets"]["event_id"], row["targets"]["decision_offset"])
                     == (candidate.episode.event_id, candidate.frame.decision_offset),
                     "Original C action target clock differs")
            validate_saved_record(row, candidate.query, session_length=candidate.label_query.session_length,
                                  label_query=candidate.label_query)
            seen.add(key)
            yield row
    _require(seen == set(wanted), "Original C evaluation target roster incomplete")


def join_policy_targets(policy, records):
    """Join saved exact capped/fixed outcomes; this is not filled-notional P&L."""
    evaluation.verify_policy(policy)
    _require(policy.control == "model" and policy.scope == SCOPE and policy.model in tuple("EP-" + arm for arm in ARMS),
             "Only registered EP fixed policy is supported")
    targets = {}
    for record in records:
        _require(record["key"] not in targets, "Duplicate saved evaluation target")
        targets[record["key"]] = record
    rows = []
    unsupported_reasons = {"no_causal_frame", "missing_paired_value_heads", "reference_unknown"}
    unsupported_episodes = {row[0] for row in policy.trace if row[5] in unsupported_reasons}
    for choice in policy.choices:
        episode, candidate = choice.episode, choice.candidate
        base = {"schema": "EP-policy-cash-v1", "arm": policy.model.removeprefix("EP-"),
                "policy_sha256": policy.sha256, "session": episode.session,
                "symbol": episode.symbol, "event_id": episode.event_id,
                "cluster_id": episode.cluster_id, "family": episode.family,
                "query_key": None if candidate is None else candidate.key,
                "action": choice.action, "choice_reason": choice.reason,
                "entry_policy": "next_open_capped", "management": "fixed_exit",
                "original_requested_notional": choice.initial_denominator,
                "denominator": choice.initial_denominator}
        if candidate is None:
            unsupported = choice.reason in unsupported_reasons or episode.event_id in unsupported_episodes
            rows.append({**base, "status": "unsupported" if unsupported else "no_commit",
                "reason": choice.reason, "net_cash": None if unsupported else Fraction(),
                "fill": None if unsupported else 0,
                "value": Fraction() if not unsupported and choice.initial_denominator else None,
                "matured_offset": None, "fill_matured_offset": None,
                "target_status": None, "target_reason": None})
            continue
        _require(choice.action in ("immediate", "wait3") and candidate.key in targets,
                 "Committed action is missing its original saved target")
        record = targets[candidate.key]
        validate_saved_record(record, candidate.query, session_length=candidate.label_query.session_length,
                              label_query=candidate.label_query)
        target = record["targets"]
        denominator = _exact(target["original_requested_notional"])
        _require(target["entry_admissibility"] == "admissible" and denominator == choice.initial_denominator
                 and (target["event_id"], target["decision_offset"])
                 == (episode.event_id, candidate.frame.decision_offset),
                 "Frozen policy differs from original target identity/admission/denominator")
        action = _cash_target(target[choice.action], denominator)
        rows.append({**base, **action, "target_status": action["status"], "target_reason": action["reason"]})
    return tuple(evaluation._plain(row) for row in rows)
