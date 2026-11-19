"""Independent ET reconstruction over admitted original targets and ES forecasts."""

from bisect import bisect_left
from collections import defaultdict
from fractions import Fraction
from itertools import accumulate

from research import intraday_event_calibration_reference as er
from research import intraday_event_factorized_reference as ep
from research.intraday_event_models import HEADS, HELD_STATES


IMMEDIATE = "net_immediate"


def _check(condition, message):
    if not condition:
        raise ValueError("ET independent: " + message)


def _wire(value):
    if value is None:
        return None
    return [hex(value.numerator).replace("0x", ""), hex(value.denominator)[2:]]


def _fraction(wire):
    _check(type(wire) is list and len(wire) == 2 and all(type(part) is str for part in wire),
           "exact ET rational required")
    value = Fraction(int(wire[0], 16), int(wire[1], 16))
    _check(_wire(value) == wire, "noncanonical ET rational")
    return value


def _raw_score(row, session):
    _check(row.keys() == {"key", "session", "query_status", "predictions", "head_status"}
           and row["session"] == session and row["query_status"] == "supported_features",
           "original ES row or chronology differs")
    _check(set(row["predictions"]) == set(row["head_status"]) == set(HEADS), "ES forecast heads differ")
    value = row["predictions"][IMMEDIATE]
    _check(value is None or type(value) in (int, float, str), "original ES scalar differs")
    if value is None:
        return None
    ep._number(value)
    return Fraction(str(value))


def _phase_witness(rows, weights, scores, targets):
    by_score = defaultdict(Fraction)
    for row in rows:
        by_score[scores[row.key]] += weights[row.key]
    values = sorted(by_score)
    cumulative = list(accumulate(by_score[score] for score in values))
    mass = cumulative[-1] if cumulative else Fraction()
    low, high = (values[bisect_left(cumulative, share * mass)] for share in
                 (Fraction(1, 3), Fraction(2, 3))) if values else (None, None)
    groups = ((), (), ()) if low is None else (
        [row for row in rows if scores[row.key] <= low],
        [row for row in rows if low < scores[row.key] <= high],
        [row for row in rows if high < scores[row.key]],
    )
    bins = []
    for group in groups:
        counts = er._counts(group)
        total_weight = sum((weights[row.key] for row in group), Fraction())
        mean = None if not total_weight else sum(
            (weights[row.key] / total_weight) * ep._exact(targets[row.key][IMMEDIATE]) for row in group)
        sparse = counts["rows"] < 2 or counts["dates"] < 2 or counts["clusters"] < 2
        bins.append({**counts, "weight_mass": _wire(total_weight), "exact_mean": _wire(mean),
                     "status": "estimated" if total_weight else "unsupported_empty_bin",
                     "precision": "unknown_small_support" if sparse else "not_established"})
    return {**er._counts(rows), "weight_mass": _wire(mass), "cutpoints": list(map(_wire, (low, high))), "bins": bins}


def verify(data, phases, exact_targets, oof_predictions, artifact):
    """Rebuild the table with grouped score masses and independent prior J weights."""
    base = data.base
    evaluation_days = ep._days(base.calendar, base.fold)
    stop = 97 + 40 * base.fold
    _check(base.scope == "common_chart_peer", "original common scope required")
    queries = {row.key: row for row in base.training}
    _check(queries.keys() == phases.keys(), "training phases lost original identities")
    _check(all(phase is None or (phase in er.PHASES and queries[key].family == er.FAMILY
                               and queries[key].state not in HELD_STATES)
               for key, phase in phases.items()), "training phase outside original C entry scope")
    joint, weights = er._joint(data, exact_targets)
    forecasts = tuple(oof_predictions)
    prior_days = set(base.calendar[49:stop])
    wanted = {query.key: query for query in queries.values() if query.session in prior_days}
    scores = {}
    for forecast in forecasts:
        key = forecast["key"]
        _check(key in wanted and key not in scores, "duplicate or out-of-scope ES OOF forecast")
        scores[key] = _raw_score(forecast, wanted[key].session)
    _check(scores.keys() == wanted.keys(), "incomplete chronological ES OOF roster")
    groups = defaultdict(list)
    for row in joint:
        phase = phases[row.key]
        if phase is not None and scores.get(row.key) is not None:
            groups[phase].append(row)
    cells = {phase: _phase_witness(groups[phase], weights, scores, exact_targets) for phase in er.PHASES}
    expected = {"schema": "ET-immediate-value-v1", "fold": base.fold, "scope": base.scope,
        "support": base.support, "training_sessions": list(base.calendar[:stop]),
        "evaluation_sessions": list(evaluation_days), "training_targets_sha256": ep._hash(exact_targets),
        "training_phases_sha256": ep._hash(phases), "oof_predictions_sha256": ep._hash(forecasts),
        "weighting": "full_joint_date_cluster_episode_frame_then_condition",
        "cut_rule": "weighted_inverse_ecdf_inclusive_thirds",
        "joint": {**er._counts(joint), "weight_sum": _wire(sum(weights.values(), Fraction()))},
        "oof": {"start_index": 49, "expected_rows": len(wanted),
                "finite_immediate_rows": sum(score is not None for score in scores.values()),
                "eligible_c_rows": sum(len(group) for group in groups.values())}, "cells": cells}
    ep._same(artifact, expected, "ET exact training mapping")
    return {"verified": True, "joint_rows": len(joint), "bins": 3 * len(cells)}


def verify_projection(data, roster, artifact, current_success_predictions, projected):
    """Independently apply the already verified table to causal current ES rows."""
    candidates = {candidate.key: candidate for candidate in roster.candidates}
    _check(len(candidates) == len(roster.candidates) and all(event.family == er.FAMILY for event in roster.episodes),
           "causal roster differs")
    queries = data.base.evaluation
    forecasts = tuple(current_success_predictions)
    _check(tuple(row["key"] for row in forecasts) == tuple(query.key for query in queries),
           "current ES roster differs")
    cutpoints = {phase: tuple(_fraction(cut) if cut is not None else None
                             for cut in cell["cutpoints"]) for phase, cell in artifact["cells"].items()}
    expected = []
    for query, row in zip(queries, forecasts):
        score = _raw_score(row, query.session)
        phase = None
        if query.family == er.FAMILY and query.state not in HELD_STATES:
            candidate = candidates.get(query.key)
            _check(candidate is not None and candidate.query == query, "original causal query differs")
            phase = er._phase(query, candidate.episode.onset_decision_offset)
        value = None
        if phase is None:
            status = "not_applicable_to_mapping"
        elif score is None:
            status = "unsupported_original_success_score"
        else:
            low, high = cutpoints[phase]
            if low is None:
                status = "unsupported_empty_phase"
            else:
                index = int(score > low) + int(score > high)
                value = artifact["cells"][phase]["bins"][index]["exact_mean"]
                status = "unsupported_empty_bin" if value is None else "estimated_training_score_bin"
        scores, states = dict.fromkeys(HEADS), dict.fromkeys(HEADS, "not_estimated_by_mapping")
        scores[IMMEDIATE], states[IMMEDIATE] = value, status
        expected.append({"key": query.key, "session": query.session, "query_status": "supported_features",
                         "predictions": scores, "head_status": states})
    ep._same(projected, expected, "ET exact value projection")
    return {"verified": True, "predictions": len(expected)}
