"""ET training-only expected immediate value from chronological ES scores."""

from bisect import bisect_left
from fractions import Fraction

from research import intraday_event_calibration as er
from research import intraday_event_factorized_model as matched
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_mean_baseline as baseline
from research import intraday_event_models as models
from research.intraday_event_immediate_value_reference import verify as verify, verify_projection as verify_projection


SCHEMA = "ET-immediate-value-v1"
PHASES = baseline.PHASES
IMMEDIATE = "net_immediate"
CUT_RULE = "weighted_inverse_ecdf_inclusive_thirds"
FORECAST_FIELDS = {"key", "session", "query_status", "predictions", "head_status"}


def _require(condition, message):
    if not condition:
        raise ValueError("ET immediate value: " + message)


def exact_value(packed):
    """Decode an exact ET forecast or unknown without rounding its sign."""
    return None if packed is None else er._unpacked(packed)


def _training_joint(base, action_targets, phases, exact_targets, cutoff):
    rows, labels = (), {}
    if base.training:
        rows, labels = models.validate_training(base.training, base.targets, cutoff, mode="chart_peer",
                                                support_binding=base.support["training_seal"])
    _require(len(base.targets) == len(labels) and set(labels) == set(phases) == set(exact_targets),
             "complete original training identities required")
    fills = matched.validate_action_fills(rows, labels, action_targets, cutoff)
    joint, immediate = [], {}
    for row in rows:
        key = row.key
        entry = row.state not in models.HELD_STATES
        phase = phases[key]
        _require(phase in (*PHASES, None) and (phase is None or entry and row.family == workflow.FAMILY),
                 "training phase differs from original C entry scope")
        original, exact, fill = labels[key], exact_targets[key], fills[key]
        _require(set(exact) == set(models.Q_HEADS), "exact original target heads differ")
        for head in models.Q_HEADS:
            supplied, decoded = exact[head], getattr(original, head)
            _require((supplied is None) == (decoded is None), "exact/decoded target support differs")
            if supplied is not None:
                _require(type(supplied) is str, "canonical original target required")
                value = Fraction(supplied)
                _require(str(value) == supplied and float(value) == float(decoded),
                         "exact/decoded training value differs")
                _require(getattr(fill, head[4:]) != 0 or value == 0, "known no-fill cash must be zero")
                if head == IMMEDIATE:
                    immediate[key] = value
        if (entry and models.training_head_known(original, IMMEDIATE)
                and fill.immediate is not None and fill.wait3 is not None):
            joint.append(row)
    return joint, immediate


def _success_score(row, session):
    _require(set(row) == FORECAST_FIELDS and row["session"] == session
             and row["query_status"] == "supported_features"
             and set(row["predictions"]) == set(row["head_status"]) == set(models.HEADS),
             "original ES forecast identity/schema differs")
    return er._score(row["predictions"][IMMEDIATE])


def _oof_scores(base, stop, forecasts):
    days = set(base.calendar[er.START_INDEX:stop])
    expected = {row.key: row.session for row in base.training if row.session in days}
    indexed = {row["key"]: row for row in forecasts}
    _require(len(indexed) == len(forecasts) and indexed.keys() == expected.keys(),
             "complete chronological ES OOF roster required")
    return {key: _success_score(row, expected[key]) for key, row in indexed.items()}


def _cuts(group, weights, scores, mass):
    cuts, cumulative = [], Fraction()
    for row in sorted(group, key=lambda row: scores[row.key]):
        cumulative += weights[row.key]
        while len(cuts) < 2 and 3 * cumulative >= (len(cuts) + 1) * mass:
            cuts.append(scores[row.key])
        if len(cuts) == 2:
            break
    return cuts


def _cell(group, weights, scores, immediate):
    mass = sum((weights[row.key] for row in group), Fraction())
    cuts = _cuts(group, weights, scores, mass)
    groups = [[], [], []]
    for row in group:
        groups[bisect_left(cuts, scores[row.key])].append(row)
    bins = []
    for bucket in groups:
        weight = sum((weights[row.key] for row in bucket), Fraction())
        mean = (sum((weights[row.key] * immediate[row.key] for row in bucket), Fraction()) / weight
                if weight else None)
        counts = baseline._counts(bucket)
        bins.append({**counts, "weight_mass": er._packed(weight),
            "exact_mean": None if mean is None else er._packed(mean),
            "status": "estimated" if weight else "unsupported_empty_bin",
            "precision": "unknown_small_support" if min(counts[k] for k in ("rows", "dates", "clusters")) < 2
                         else "not_established"})
    return {**baseline._counts(group), "weight_mass": er._packed(mass),
            "cutpoints": [er._packed(cut) for cut in cuts] if cuts else [None, None], "bins": bins}


def estimate(data, phases, exact_targets, oof_predictions):
    """Freeze three bins per C phase using full original J weights before conditioning."""
    base = baseline._base(data)
    _, stop, end = workflow.FOLDS[base.fold]
    forecasts = tuple(oof_predictions)
    joint, immediate = _training_joint(base, data.action_targets, phases, exact_targets, base.calendar[stop])
    weights = baseline._weights(joint)
    scores = _oof_scores(base, stop, forecasts)
    selected = [row for row in joint if phases[row.key] is not None and scores.get(row.key) is not None]
    cells = {phase: _cell([row for row in selected if phases[row.key] == phase], weights, scores, immediate)
             for phase in PHASES}
    return {"schema": SCHEMA, "fold": base.fold, "scope": base.scope, "support": base.support,
        "training_sessions": list(base.calendar[:stop]), "evaluation_sessions": list(base.calendar[stop:end]),
        "training_targets_sha256": baseline._hash(exact_targets), "training_phases_sha256": baseline._hash(phases),
        "oof_predictions_sha256": baseline._hash(forecasts), "weighting": baseline.WEIGHTING, "cut_rule": CUT_RULE,
        "joint": {**baseline._counts(joint), "weight_sum": er._packed(sum(weights.values(), Fraction()))},
        "oof": {"start_index": er.START_INDEX, "expected_rows": len(scores),
                "finite_immediate_rows": sum(value is not None for value in scores.values()),
                "eligible_c_rows": len(selected)}, "cells": cells}


def _projection_cells(base, artifact):
    _, stop, end = workflow.FOLDS[base.fold]
    _require(artifact["schema"] == SCHEMA and artifact["fold"] == base.fold
             and artifact["scope"] == base.scope and artifact["support"] == base.support
             and artifact["training_sessions"] == list(base.calendar[:stop])
             and artifact["evaluation_sessions"] == list(base.calendar[stop:end])
             and artifact["weighting"] == baseline.WEIGHTING and artifact["cut_rule"] == CUT_RULE
             and set(artifact["cells"]) == set(PHASES), "mapping artifact differs from original fold")
    result = {}
    for phase, cell in artifact["cells"].items():
        mass = er._unpacked(cell["weight_mass"])
        cuts = tuple(exact_value(value) for value in cell["cutpoints"])
        _require(0 <= mass <= 1 and len(cuts) == 2 and len(cell["bins"]) == 3
                 and (cuts == (None, None) if not mass else None not in cuts and cuts[0] <= cuts[1]),
                 "invalid phase mass or cutpoints")
        means, total = [], Fraction()
        for bucket in cell["bins"]:
            weight, mean = er._unpacked(bucket["weight_mass"]), exact_value(bucket["exact_mean"])
            _require(0 <= weight <= mass and (weight == 0) == (mean is None), "invalid bin support/mean")
            means.append(None if mean is None else er._packed(mean))
            total += weight
        _require(total == mass, "bin weights differ from phase mass")
        result[phase] = (cuts, means)
    return result


def project(data, roster, artifact, current_success_predictions):
    """Project exact expected net value at each original causal C entry query."""
    base = baseline._base(data)
    forecasts = tuple(current_success_predictions)
    _require([row["key"] for row in forecasts] == [row.key for row in base.evaluation],
             "complete ordered current ES roster required")
    cells = _projection_cells(base, artifact)
    candidates = {candidate.key: candidate for candidate in roster.candidates}
    _require(len(candidates) == len(roster.candidates)
             and all(event.family == workflow.FAMILY for event in roster.episodes), "C causal roster required")
    result = []
    for query, raw in zip(base.evaluation, forecasts):
        score = _success_score(raw, query.session)
        phase = None
        if query.family == workflow.FAMILY and query.state not in models.HELD_STATES:
            candidate = candidates.get(query.key)
            _require(candidate is not None and candidate.query == query, "C query lacks original causal onset")
            phase = baseline._phase(query.family, query.state, query.decision_offset,
                                    candidate.episode.onset_decision_offset)
        scores = dict.fromkeys(models.HEADS)
        statuses = dict.fromkeys(models.HEADS, "not_estimated_by_mapping")
        if phase is None:
            status = "not_applicable_to_mapping"
        elif score is None:
            status = "unsupported_original_success_score"
        elif cells[phase][0] == (None, None):
            status = "unsupported_empty_phase"
        else:
            cuts, means = cells[phase]
            mean = means[bisect_left(cuts, score)]
            scores[IMMEDIATE] = None if mean is None else mean.copy()
            status = "unsupported_empty_bin" if mean is None else "estimated_training_score_bin"
        statuses[IMMEDIATE] = status
        result.append({"key": query.key, "session": query.session, "query_status": "supported_features",
                       "predictions": scores, "head_status": statuses})
    return tuple(result)
