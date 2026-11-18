"""ER: one chronological, phase-centered calibration of saved net values.

The closed caller authenticates OOF block provenance. This pure estimator also
requires the complete prior prediction roster, and never sees evaluation cash.
"""

from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import math

from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_model as matched
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_mean_baseline as baseline
from research import intraday_event_models as models


PHASES = baseline.PHASES
HEADS = models.Q_HEADS
MODEL = "ER-calibrated"
START_INDEX = 49
PRECISION = 50


def _require(condition, message):
    if not condition:
        raise ValueError("ER calibration: " + message)


def _decimal(value):
    value = Fraction(value)
    with localcontext() as context:
        context.prec = PRECISION
        context.rounding = ROUND_HALF_EVEN
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def _score(value):
    if value is None:
        return None
    _require(type(value) in (str, int, float) and math.isfinite(float(Fraction(str(value)))),
             "finite saved value required")
    return Fraction(str(value))


def _packed(value):
    """Hex bypasses decimal integer-string limits without rounding a coefficient."""
    return [format(value.numerator, "x"), format(value.denominator, "x")]


def _unpacked(value):
    _require(type(value) is list and len(value) == 2 and all(type(part) is str for part in value),
             "exact coefficient pair required")
    numerator, denominator = (int(part, 16) for part in value)
    _require(denominator > 0, "positive exact denominator required")
    result = Fraction(numerator, denominator)
    _require(_packed(result) == value, "canonical exact coefficient required")
    return result


def _oof(data, forecasts):
    base = baseline._base(data)
    stop = workflow.FOLDS[base.fold][1]
    expected = {row.key: row for row in base.training if base.calendar[START_INDEX] <= row.session < base.calendar[stop]}
    forecasts = tuple(forecasts)
    indexed = {row["key"]: row for row in forecasts}
    _require(len(indexed) == len(forecasts) and indexed.keys() == expected.keys(),
             "complete chronological OOF training roster required")
    values = {}
    for key, row in indexed.items():
        _require(row["session"] == expected[key].session
                 and row["query_status"] == "supported_features"
                 and set(row["predictions"]) == set(models.HEADS)
                 and set(row["head_status"]) == set(models.HEADS), "OOF identity/schema differs")
        values[key] = {head: _score(row["predictions"][head]) for head in HEADS}
    return forecasts, values, expected


def estimate(data, phases, exact_targets, oof_predictions):
    """Fit two shared nonnegative slopes and four phase intercepts per action."""
    base = baseline._base(data)
    # Reuse original training validation and the full-J definition, not its means
    # as calibration intercepts. OOF centers describe a different support.
    full_mean = baseline.estimate(data, phases, exact_targets)
    rows = tuple(sorted(base.training, key=lambda row: row.key))
    labels = {label.key: label for label in base.targets}
    cutoff = base.calendar[workflow.FOLDS[base.fold][1]]
    fills = matched.validate_action_fills(rows, labels, data.action_targets, cutoff)
    joint = [row for row in rows if row.state not in models.HELD_STATES
             and models.training_head_known(labels[row.key], "net_immediate")
             and fills[row.key].immediate is not None and fills[row.key].wait3 is not None]
    weights = baseline._weights(joint)
    forecasts, scores, expected = _oof(data, oof_predictions)
    selected = [row for row in joint if row.family == workflow.FAMILY and phases[row.key] is not None
                and row.key in scores and all(scores[row.key][head] is not None for head in HEADS)]
    cells, centers = {}, {}
    for phase in PHASES:
        group = [row for row in selected if phases[row.key] == phase]
        mass = sum((weights[row.key] for row in group), Fraction())
        xs, ys = {}, {}
        for head in HEADS:
            xs[head] = sum((weights[row.key] * scores[row.key][head] for row in group), Fraction()) / mass if mass else None
            ys[head] = sum((weights[row.key] * Fraction(exact_targets[row.key][head]) for row in group), Fraction()) / mass if mass else None
        centers[phase] = xs, ys
        counts = baseline._counts(group)
        cells[phase] = {**counts, "weight_mass": str(mass),
            "status": "estimated" if mass else "unsupported_empty_group",
            "precision": "unknown_small_support" if any(counts[k] < 2 for k in ("rows", "dates", "clusters")) else "not_established",
            "scores": {head: None if xs[head] is None else _decimal(xs[head]) for head in HEADS},
            "targets": {head: None if ys[head] is None else _decimal(ys[head]) for head in HEADS}}
    slopes, exact_betas = {}, {}
    for head in HEADS:
        numerator, denominator = Fraction(), Fraction()
        for row in selected:
            xs, ys = centers[phases[row.key]]
            x = scores[row.key][head] - xs[head]
            y = Fraction(exact_targets[row.key][head]) - ys[head]
            numerator += weights[row.key] * x * y
            denominator += weights[row.key] * x * x
        if not selected:
            beta, status = None, "unsupported_empty_support"
        elif denominator == 0:
            # Predeclared minimum-slope constant solution; not an identified
            # response slope or evidence for a genuinely zero relationship.
            beta, status = Fraction(), "nonidentified_zero_variance"
        elif numerator <= 0:
            beta, status = Fraction(), "constrained_zero"
        else:
            beta, status = numerator / denominator, "fitted_nonnegative"
        exact_betas[head] = beta
        slopes[head] = {"numerator": _decimal(numerator), "denominator": _decimal(denominator),
                        "beta": None if beta is None else _decimal(beta), "status": status,
                        "exact_beta": None if beta is None else _packed(beta)}
    for phase in PHASES:
        xs, ys = centers[phase]
        cells[phase]["exact_intercepts"] = {head: None if xs[head] is None or exact_betas[head] is None
            else _packed(ys[head] - exact_betas[head] * xs[head]) for head in HEADS}
    return {"schema": "ER-phase-calibration-v1", "fold": base.fold, "scope": base.scope,
        "training_sessions": full_mean["training_sessions"], "evaluation_sessions": full_mean["evaluation_sessions"],
        "support": base.support, "training_targets_sha256": baseline._hash(exact_targets),
        "training_phases_sha256": baseline._hash(phases),
        "oof_predictions_sha256": baseline._hash(forecasts), "weighting": baseline.WEIGHTING,
        "arithmetic_precision": PRECISION, "rounding": ROUND_HALF_EVEN, "joint": full_mean["joint"],
        "oof": {**baseline._counts(selected), "start_index": START_INDEX,
                "expected_rows": len(expected), "paired_finite_rows": sum(
                    all(value[head] is not None for head in HEADS) for value in scores.values())},
        "cells": cells, "slopes": slopes}


def _validate(data, artifact):
    base = baseline._base(data)
    _, stop, end = workflow.FOLDS[base.fold]
    _require(artifact["schema"] == "ER-phase-calibration-v1" and artifact["fold"] == base.fold
             and artifact["scope"] == base.scope and artifact["support"] == base.support
             and artifact["training_sessions"] == list(base.calendar[:stop])
             and artifact["evaluation_sessions"] == list(base.calendar[stop:end])
             and artifact["weighting"] == baseline.WEIGHTING
             and artifact["arithmetic_precision"] == PRECISION and artifact["rounding"] == ROUND_HALF_EVEN
             and artifact["oof"]["start_index"] == START_INDEX
             and set(artifact["cells"]) == set(PHASES) and set(artifact["slopes"]) == set(HEADS),
             "calibration artifact contract differs")
    for head, slope in artifact["slopes"].items():
        beta = _score(slope["beta"])
        numerator, denominator = _score(slope["numerator"]), _score(slope["denominator"])
        _require(numerator is not None and denominator is not None and denominator >= 0,
                 "invalid sufficient statistics")
        status = slope["status"]
        _require((status == "unsupported_empty_support" and beta is None and numerator == denominator == 0)
                 or (status == "nonidentified_zero_variance" and beta == numerator == denominator == 0)
                 or (status == "constrained_zero" and beta == 0 and numerator <= 0 and denominator > 0)
                 or (status == "fitted_nonnegative" and beta is not None and beta > 0 and numerator > 0 and denominator > 0),
                 "slope support/status differs")
        _require((slope["exact_beta"] is None and beta is None) or
                 (slope["exact_beta"] is not None and beta is not None
                  and _decimal(_unpacked(slope["exact_beta"])) == slope["beta"]),
                 "exact/rounded slope differs")
    for cell in artifact["cells"].values():
        _require(set(cell["scores"]) == set(cell["targets"]) == set(cell["exact_intercepts"]) == set(HEADS),
                 "phase head roster differs")
        for head in HEADS:
            x, y = _score(cell["scores"][head]), _score(cell["targets"][head])
            _require((cell["status"] == "estimated" and x is not None and y is not None and Fraction(cell["weight_mass"]) > 0)
                     or (cell["status"] == "unsupported_empty_group" and x is None and y is None and Fraction(cell["weight_mass"]) == 0),
                     "phase support/status differs")
            if cell["status"] == "estimated":
                _unpacked(cell["exact_intercepts"][head])
            else:
                _require(cell["exact_intercepts"][head] is None, "empty phase invented exact intercept")


def project(data, roster, artifact, direct_predictions):
    """Apply the stored mapping before any evaluation outcome is consumed."""
    _validate(data, artifact)
    base = data.base
    direct_predictions = tuple(direct_predictions)
    _require([(row["key"], row["session"]) for row in direct_predictions]
             == [(row.key, row.session) for row in base.evaluation], "complete direct prediction roster required")
    candidates = {candidate.key: candidate for candidate in roster.candidates}
    _require(len(candidates) == len(roster.candidates)
             and all(event.family == workflow.FAMILY for event in roster.episodes), "original C roster required")
    result = []
    for query, direct in zip(base.evaluation, direct_predictions):
        _require(set(direct["predictions"]) == set(direct["head_status"]) == set(models.HEADS)
                 and direct["query_status"] == "supported_features", "original direct forecast schema differs")
        phase = None
        if query.family == workflow.FAMILY and query.state not in models.HELD_STATES:
            candidate = candidates.get(query.key)
            _require(candidate is not None and candidate.query == query, "C query lacks original onset")
            phase = baseline._phase(query.family, query.state, query.decision_offset, candidate.episode.onset_decision_offset)
        values, status = dict(direct["predictions"]), dict(direct["head_status"])
        for head in HEADS:
            raw = _score(values[head])
            cell = artifact["cells"].get(phase)
            beta = artifact["slopes"][head]["exact_beta"]
            if phase is None:
                value, why = None, "not_applicable_to_calibration"
            elif raw is None:
                value, why = None, "unsupported_original_prediction"
            elif cell["scores"][head] is None or beta is None:
                value, why = None, "unsupported_training_calibration"
            else:
                value = _decimal(_unpacked(cell["exact_intercepts"][head]) + _unpacked(beta) * raw)
                why = "estimated_training_only_calibration"
            values[head], status[head] = value, why
        result.append({**direct, "predictions": values, "head_status": status})
    return tuple(result)


def freeze_policy(data, roster, predictions):
    predictions = tuple(predictions)
    _require([(row["key"], row["session"]) for row in predictions]
             == [(row.key, row.session) for row in data.base.evaluation], "complete calibrated policy roster required")
    wanted = {candidate.key for candidate in roster.candidates if candidate.query is not None}
    return evaluation.freeze_policy(roster.episodes, roster.candidates,
        tuple(row for row in predictions if row["key"] in wanted), control="model", model=MODEL,
        scope=workflow.SCOPE, prediction_binding=baseline._hash(predictions))


def join_policy_targets(policy, records):
    """Join the unchanged exact saved actions to the calibrated commitments."""
    evaluation.verify_policy(policy)
    _require(policy.control == "model" and policy.scope == workflow.SCOPE and policy.model == MODEL,
             "registered calibrated policy required")
    targets = {}
    for record in records:
        _require(record["key"] not in targets, "duplicate original evaluation target")
        targets[record["key"]] = record
    rows = []
    unsupported = {"no_causal_frame", "missing_paired_value_heads", "reference_unknown"}
    unknown_episodes = {row[0] for row in policy.trace if row[5] in unsupported}
    for choice in policy.choices:
        episode, candidate = choice.episode, choice.candidate
        base = {"schema": "ER-policy-cash-v1", "arm": "calibrated", "policy_sha256": policy.sha256,
            "session": episode.session, "symbol": episode.symbol, "event_id": episode.event_id,
            "cluster_id": episode.cluster_id, "family": episode.family,
            "query_key": None if candidate is None else candidate.key,
            "action": choice.action, "choice_reason": choice.reason, "entry_policy": "next_open_capped",
            "management": "fixed_exit", "original_requested_notional": choice.initial_denominator,
            "denominator": choice.initial_denominator}
        if candidate is None:
            unknown = choice.reason in unsupported or episode.event_id in unknown_episodes
            rows.append({**base, "status": "unsupported" if unknown else "no_commit", "reason": choice.reason,
                "net_cash": None if unknown else Fraction(), "fill": None if unknown else 0,
                "value": Fraction() if not unknown and choice.initial_denominator else None,
                "matured_offset": None, "fill_matured_offset": None, "target_status": None, "target_reason": None})
            continue
        _require(choice.action in ("immediate", "wait3") and candidate.key in targets,
                 "committed action lacks original target")
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
