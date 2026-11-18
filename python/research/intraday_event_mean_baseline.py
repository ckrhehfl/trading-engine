"""EQ training-only situation means and the unchanged original cash policy.

The full EP joint-known population supplies the hierarchy weights. Only then
are C phases conditioned on. These helpers fit no native model and never read
evaluation labels; the closed runner owns admission and durable sealing.
"""

from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from hashlib import sha256
import math

from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_model as matched
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as original
from research import intraday_event_models as models
from research import intraday_event_rank as rank


PHASES = rank.PHASES
HEADS = models.Q_HEADS
MODEL = "EQ-mean"
WEIGHTING = "full_joint_date_cluster_episode_frame_then_condition"
ARITHMETIC_PRECISION = 50


def _require(condition, message):
    if not condition:
        raise ValueError("EQ mean: " + message)


def _hash(value):
    return sha256(original.encode(value)).hexdigest()


def _base(data):
    _require(type(data) is workflow.FactorizedData, "original matched fold required")
    base = data.base
    _require(type(base.fold) is int and base.fold in range(4)
             and base.scope == workflow.SCOPE and len(base.calendar) == 257,
             "original fold/scope/calendar required")
    return base


def _phase(family, state, decision, onset):
    if family != workflow.FAMILY or state in models.HELD_STATES:
        return None
    _require(type(onset) is int and 0 <= onset <= decision, "original onset clock differs")
    return ("onset" if decision == onset else
            {"WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback",
             "CONFIRMED": "confirmation"}.get(state))


def training_onsets(shards, data):
    """Read original, uncoalesced onsets only for prior-session entry queries."""
    base = _base(data)
    stop = workflow.FOLDS[base.fold][1]
    cutoff = base.calendar[stop]
    wanted = {row.episode_id for row in base.training if row.state not in models.HELD_STATES}
    onsets = {}
    for slot, manifest in shards:
        if manifest["session_indices"][0] >= stop:
            break
        for row in inputs._rows(slot, manifest, "event-states.jsonl", "event_state"):
            frame = row["frame"]
            if row["session"] >= cutoff or frame["kind"] != "at_onset" or frame["event_id"] not in wanted:
                continue
            event = frame["event_id"]
            _require(event not in onsets, "duplicate original training onset")
            onsets[event] = {"session": row["session"], "symbol": row["symbol"], "event_id": event,
                "cluster_id": frame["overlap_group_id"], "family": frame["family"],
                "decision_offset": frame["decision_offset"]}
    _require(set(onsets) == wanted, "training original onset roster incomplete")
    return onsets


def training_phases(shards, data):
    """An onset coalesced into a later state still has onset phase priority."""
    base, result = _base(data), {}
    onsets = training_onsets(shards, data)
    cutoff = base.calendar[workflow.FOLDS[base.fold][1]]
    for row in base.training:
        _require(row.key not in result and row.session < cutoff, "training phase identity/chronology differs")
        if row.state in models.HELD_STATES:
            result[row.key] = None
            continue
        onset = onsets[row.episode_id]
        _require((onset["session"], onset["symbol"], onset["cluster_id"], onset["family"])
                 == (row.session, row.symbol, row.cluster_id, row.family)
                 and type(onset["decision_offset"]) is int and 0 <= onset["decision_offset"] <= row.decision_offset,
                 "training query differs from original onset")
        result[row.key] = _phase(row.family, row.state, row.decision_offset, onset["decision_offset"])
    return result


def training_targets(shards, data):
    """Preserve exact saved values before the original float learner boundary.

    The causal query prefix fixes how many label rows may be decoded. Reading
    the next evaluation label merely to discover its date is prohibited.
    """
    base = _base(data)
    stop = workflow.FOLDS[base.fold][1]
    cutoff, wanted = base.calendar[stop], {row.key: row for row in base.training}
    result = {}
    for slot, manifest in shards:
        if manifest["session_indices"][0] >= stop:
            break
        for record in inputs.training_labels(slot, manifest, cutoff):
            _require(record["session"] < cutoff, "evaluation target reached the training boundary")
            if record["key"] not in wanted:
                continue
            key, query = record["key"], wanted[record["key"]]
            _require(key not in result, "duplicate exact training target")
            workflow.validate_saved_record(record, query, cutoff=cutoff)
            result[key] = {head: (None if record["training_labels"][head] is None else
                                 str(workflow._exact(record["training_labels"][head]))) for head in HEADS}
    _require(set(result) == set(wanted), "exact training target roster incomplete")
    return result


def _weights(rows):
    """Exact form of the original head_weights hierarchy, on full J only."""
    dates, clusters, frames = defaultdict(set), defaultdict(set), Counter()
    for row in rows:
        episode = models.original_episode(row)
        dates[row.session].add(row.cluster_id)
        clusters[row.session, row.cluster_id].add(episode)
        frames[row.session, row.cluster_id, episode] += 1
    weights = {row.key: Fraction(1, len(dates) * len(dates[row.session])
        * len(clusters[row.session, row.cluster_id])
        * frames[row.session, row.cluster_id, models.original_episode(row)]) for row in rows}
    _require(all(float(weights[row.key]) == value for row, value in zip(rows, models.head_weights(rows))),
             "exact hierarchy differs from original EP float weights")
    _require(sum(weights.values(), Fraction()) == int(bool(rows)), "joint weights do not sum to one")
    return weights


def _counts(rows):
    return {"rows": len(rows), "dates": len({row.session for row in rows}),
            "clusters": len({row.cluster_id for row in rows}),
            "episodes": len({models.original_episode(row) for row in rows})}


def _mean(group, weights, values, head, mass):
    if not mass:
        return None
    # Sum before rounding: termwise Decimal rounding can turn an exact zero
    # into a tiny positive score or break an exact action tie. Convert integers
    # directly, rather than stringify the potentially large rational total.
    total = sum((weights[row.key] * Fraction(values[row.key][head])
                 for row in group), Fraction()) / mass
    with localcontext() as context:
        context.prec = ARITHMETIC_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return str(Decimal(total.numerator) / Decimal(total.denominator))


def estimate(data, phases, exact_targets):
    """Compute eight fixed conditional means, with no shrinkage or tuning."""
    base = _base(data)
    _, stop, end = workflow.FOLDS[base.fold]
    cutoff = base.calendar[stop]
    rows = tuple(sorted(base.training, key=lambda row: row.key))
    labels = {label.key: label for label in base.targets}
    keys = {row.key for row in rows}
    _require(len(keys) == len(rows) == len(labels) == len(base.targets)
             and set(labels) == set(phases) == set(exact_targets) == keys,
             "complete training identities required")
    if rows:
        models.validate_training(rows, base.targets, cutoff, mode="chart_peer",
                                 support_binding=base.support["training_seal"])
    fills = matched.validate_action_fills(rows, labels, data.action_targets, cutoff)
    for row in rows:
        _require(row.session < cutoff and phases[row.key] in (*PHASES, None), "training phase/chronology differs")
        _require(row.family == workflow.FAMILY and row.state not in models.HELD_STATES or phases[row.key] is None,
                 "non-C or held query supplied a C phase")
        exact = exact_targets[row.key]
        _require(set(exact) == set(HEADS), "exact training heads differ")
        for head in HEADS:
            supplied, decoded = exact[head], getattr(labels[row.key], head)
            _require((supplied is None) == (decoded is None), "exact/decoded training support differs")
            if supplied is not None:
                _require(type(supplied) is str and str(Fraction(supplied)) == supplied
                         and float(Fraction(supplied)) == float(decoded), "exact/decoded training value differs")
    joint = [row for row in rows if row.state not in models.HELD_STATES
             and models.training_head_known(labels[row.key], "net_immediate")
             and fills[row.key].immediate is not None and fills[row.key].wait3 is not None]
    weights, cells = _weights(joint), {}
    for phase in PHASES:
        group = [row for row in joint if row.family == workflow.FAMILY and phases[row.key] == phase]
        mass = sum((weights[row.key] for row in group), Fraction())
        means = {head: _mean(group, weights, exact_targets, head, mass) for head in HEADS}
        counts = _counts(group)
        cells[phase] = {**counts, "weight_mass": str(mass),
            "status": "estimated" if mass else "unsupported_empty_group",
            "precision": "unknown_small_support" if counts["rows"] < 2 or counts["dates"] < 2
                         or counts["clusters"] < 2 else "not_established",
            "means": means}
    return {"schema": "EQ-phase-mean-v1", "fold": base.fold, "scope": base.scope,
        "training_sessions": list(base.calendar[:stop]), "evaluation_sessions": list(base.calendar[stop:end]),
        "support": base.support, "training_targets_sha256": _hash(exact_targets),
        "training_phases_sha256": _hash(phases), "weighting": WEIGHTING,
        "arithmetic_precision": ARITHMETIC_PRECISION, "rounding": ROUND_HALF_EVEN,
        "joint": {**_counts(joint), "weight_sum": str(sum(weights.values(), Fraction()))}, "cells": cells}


def project(data, roster, artifact):
    """Project each fixed C phase mean without observing any evaluation label."""
    base = _base(data)
    _, stop, end = workflow.FOLDS[base.fold]
    _require(artifact["schema"] == "EQ-phase-mean-v1" and artifact["fold"] == base.fold
             and artifact["scope"] == base.scope and artifact["support"] == base.support
             and artifact["training_sessions"] == list(base.calendar[:stop])
             and artifact["evaluation_sessions"] == list(base.calendar[stop:end])
             and artifact["weighting"] == WEIGHTING and set(artifact["cells"]) == set(PHASES),
             "baseline artifact differs from original fold")
    _require(artifact["arithmetic_precision"] == ARITHMETIC_PRECISION and artifact["rounding"] == ROUND_HALF_EVEN,
             "baseline statistical arithmetic differs")
    candidates = {candidate.key: candidate for candidate in roster.candidates}
    _require(len(candidates) == len(roster.candidates)
             and all(event.family == workflow.FAMILY for event in roster.episodes), "C causal roster required")
    result = []
    for row in base.evaluation:
        scores, status = dict.fromkeys(models.HEADS), dict.fromkeys(models.HEADS, "not_estimated_by_baseline")
        phase = None
        if row.family == workflow.FAMILY and row.state not in models.HELD_STATES:
            candidate = candidates.get(row.key)
            _require(candidate is not None and candidate.query == row, "C evaluation query lacks original causal onset")
            phase = _phase(row.family, row.state, row.decision_offset, candidate.episode.onset_decision_offset)
        for head in HEADS:
            value = None if phase is None else artifact["cells"][phase]["means"][head]
            _require(value is None or type(value) is str and math.isfinite(float(Fraction(value))),
                     "baseline requires finite exact prediction")
            scores[head] = value
            status[head] = ("not_applicable_to_baseline" if phase is None else
                            "unsupported_empty_group" if value is None else "estimated_training_phase_mean")
        result.append({"key": row.key, "session": row.session, "query_status": "supported_features",
                       "predictions": scores, "head_status": status})
    return tuple(result)


def freeze_policy(data, roster, predictions):
    """Keep the original first-positive/tie-immediate/one-commit policy."""
    base = _base(data)
    predictions = tuple(predictions)
    _require([(row["key"], row["session"]) for row in predictions]
             == [(row.key, row.session) for row in base.evaluation], "complete prediction roster required")
    wanted = {candidate.key for candidate in roster.candidates if candidate.query is not None}
    return evaluation.freeze_policy(roster.episodes, roster.candidates,
        tuple(row for row in predictions if row["key"] in wanted), control="model", model=MODEL,
        scope=workflow.SCOPE, prediction_binding=_hash(predictions))


def join_policy_targets(policy, records):
    """Join original exact CashTargets without regenerating price execution."""
    evaluation.verify_policy(policy)
    _require(policy.control == "model" and policy.scope == workflow.SCOPE and policy.model == MODEL,
             "only the registered EQ baseline policy is supported")
    targets = {}
    for record in records:
        _require(record["key"] not in targets, "duplicate original evaluation target")
        targets[record["key"]] = record
    rows = []
    unsupported_reasons = {"no_causal_frame", "missing_paired_value_heads", "reference_unknown"}
    unsupported_episodes = {row[0] for row in policy.trace if row[5] in unsupported_reasons}
    for choice in policy.choices:
        episode, candidate = choice.episode, choice.candidate
        base = {"schema": "EQ-policy-cash-v1", "arm": "mean", "policy_sha256": policy.sha256,
            "session": episode.session, "symbol": episode.symbol, "event_id": episode.event_id,
            "cluster_id": episode.cluster_id, "family": episode.family,
            "query_key": None if candidate is None else candidate.key,
            "action": choice.action, "choice_reason": choice.reason, "entry_policy": "next_open_capped",
            "management": "fixed_exit", "original_requested_notional": choice.initial_denominator,
            "denominator": choice.initial_denominator}
        if candidate is None:
            unknown = choice.reason in unsupported_reasons or episode.event_id in unsupported_episodes
            rows.append({**base, "status": "unsupported" if unknown else "no_commit", "reason": choice.reason,
                "net_cash": None if unknown else Fraction(), "fill": None if unknown else 0,
                "value": Fraction() if not unknown and choice.initial_denominator else None,
                "matured_offset": None, "fill_matured_offset": None, "target_status": None, "target_reason": None})
            continue
        _require(choice.action in ("immediate", "wait3") and candidate.key in targets,
                 "committed action lacks its original target")
        record = targets[candidate.key]
        workflow.validate_saved_record(record, candidate.query, session_length=candidate.label_query.session_length,
                                       label_query=candidate.label_query)
        target = record["targets"]
        denominator = workflow._exact(target["original_requested_notional"])
        _require(target["entry_admissibility"] == "admissible" and denominator == choice.initial_denominator
                 and (target["event_id"], target["decision_offset"])
                 == (episode.event_id, candidate.frame.decision_offset), "frozen policy/target identity differs")
        action = workflow._cash_target(target[choice.action], denominator)
        rows.append({**base, **action, "target_status": action["status"], "target_reason": action["reason"]})
    return tuple(evaluation._plain(row) for row in rows)
