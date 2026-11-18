"""ES training responses and a training-only phase success rate.

The existing EP direct learner receives two binary responses through its value
slots. Its squared-error forecasts are ranking scores. The historical control
uses exact rates computed on the same joint training cohort.
"""

from dataclasses import replace
from fractions import Fraction

from research import intraday_event_factorized_model as matched
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_mean_baseline as baseline
from research import intraday_event_models as models


SCHEMA = "ES-phase-success-v1"
PHASES = baseline.PHASES
HEADS = models.Q_HEADS
WEIGHTING = baseline.WEIGHTING


def _require(condition, message):
    if not condition:
        raise ValueError("ES success: " + message)


def prepare(data, phases, exact_targets):
    """Replace only the two learner responses and seal exact training rates."""
    base = baseline._base(data)
    _, stop, end = workflow.FOLDS[base.fold]
    cutoff = base.calendar[stop]
    rows, labels = (), {}
    if base.training:
        rows, labels = models.validate_training(base.training, base.targets, cutoff, mode="chart_peer",
                                                support_binding=base.support["training_seal"])
    _require(len(base.targets) == len(labels) and set(labels) == set(phases) == set(exact_targets),
             "complete training identities required")
    fills = matched.validate_action_fills(rows, labels, data.action_targets, cutoff)
    success, joint = {}, []
    for row in rows:
        key = row.key
        phase, exact, label, fill = phases[key], exact_targets[key], labels[key], fills[key]
        entry = row.state not in models.HELD_STATES
        _require(phase in (*PHASES, None) and (phase is None or entry and row.family == workflow.FAMILY),
                 "training phase differs from original entry scope")
        _require(set(exact) == set(HEADS), "exact training heads differ")
        values = {}
        for head in HEADS:
            supplied, decoded = exact[head], getattr(label, head)
            _require((supplied is None) == (decoded is None), "exact/decoded training support differs")
            if supplied is None:
                values[head] = None
            else:
                _require(type(supplied) is str, "canonical exact training value required")
                value = Fraction(supplied)
                _require(str(value) == supplied and float(value) == float(decoded),
                         "exact/decoded training value differs")
                _require(getattr(fill, head.removeprefix("net_")) != 0 or value == 0,
                         "known no-fill action requires exact zero cash when observed")
                values[head] = value
        success[key] = dict.fromkeys(HEADS)
        if entry and all(value is not None for value in values.values()) and None not in (fill.immediate, fill.wait3):
            joint.append(row)
            # Known EP action fills here require D0 > 0. The exact saved net/D0
            # ratios retain the original net cash sign even after float underflow.
            success[key] = {head: int(getattr(fill, head.removeprefix("net_")) == 1 and values[head] > 0)
                            for head in HEADS}

    weights = baseline._weights(joint)
    cells = {}
    for phase in PHASES:
        group = [row for row in joint if phases[row.key] == phase]
        mass = sum((weights[row.key] for row in group), Fraction())
        rates = {head: str(sum((weights[row.key] for row in group if success[row.key][head]), Fraction()) / mass)
                 if mass else None for head in HEADS}
        counts = baseline._counts(group)
        cells[phase] = {**counts, "weight_mass": str(mass), "rates": rates,
            "status": "estimated" if mass else "unsupported_empty_group",
            "precision": "unknown_small_support" if min(counts[name] for name in ("rows", "dates", "clusters")) < 2
                         else "not_established"}
    artifact = {"schema": SCHEMA, "fold": base.fold, "scope": base.scope, "support": base.support,
        "training_sessions": list(base.calendar[:stop]), "evaluation_sessions": list(base.calendar[stop:end]),
        "training_targets_sha256": baseline._hash(exact_targets), "training_phases_sha256": baseline._hash(phases),
        "training_success_sha256": baseline._hash(success), "weighting": WEIGHTING,
        "original_q_support": models.training_q_support(base.targets),
        "joint": {**baseline._counts(joint), "weight_sum": str(sum(weights.values(), Fraction()))}, "cells": cells}
    targets = tuple(replace(label, **success[label.key]) for label in base.targets)
    return replace(data, base=replace(base, targets=targets)), artifact


def project(data, roster, artifact):
    """Apply the frozen training control in the original evaluation row order."""
    base = baseline._base(data)
    _, stop, end = workflow.FOLDS[base.fold]
    _require(artifact["schema"] == SCHEMA and artifact["fold"] == base.fold
             and artifact["scope"] == base.scope and artifact["support"] == base.support
             and artifact["training_sessions"] == list(base.calendar[:stop])
             and artifact["evaluation_sessions"] == list(base.calendar[stop:end])
             and artifact["weighting"] == WEIGHTING and set(artifact["cells"]) == set(PHASES),
             "rate artifact differs from original fold")
    for cell in artifact["cells"].values():
        mass = Fraction(cell["weight_mass"])
        _require(0 <= mass <= 1 and set(cell["rates"]) == set(HEADS), "invalid phase support")
        for value in cell["rates"].values():
            _require(value is None if not mass else type(value) is str and 0 <= Fraction(value) <= 1
                     and str(Fraction(value)) == value, "invalid exact training rate")

    candidates = {candidate.key: candidate for candidate in roster.candidates}
    _require(len(candidates) == len(roster.candidates)
             and all(event.family == workflow.FAMILY for event in roster.episodes), "C causal roster required")
    result = []
    for row in base.evaluation:
        scores = dict.fromkeys(models.HEADS)
        status = dict.fromkeys(models.HEADS, "not_estimated_by_baseline")
        phase = None
        if row.family == workflow.FAMILY and row.state not in models.HELD_STATES:
            candidate = candidates.get(row.key)
            _require(candidate is not None and candidate.query == row, "C evaluation query lacks original causal onset")
            phase = baseline._phase(row.family, row.state, row.decision_offset, candidate.episode.onset_decision_offset)
        for head in HEADS:
            value = None if phase is None else artifact["cells"][phase]["rates"][head]
            scores[head] = value
            status[head] = ("not_applicable_to_baseline" if phase is None else
                            "unsupported_empty_group" if value is None else "estimated_training_phase_success_rate")
        result.append({"key": row.key, "session": row.session, "query_status": "supported_features",
                       "predictions": scores, "head_status": status})
    return tuple(result)
