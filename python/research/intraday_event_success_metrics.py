"""Measure same-session profitable-fill ranking on sealed causal candidates."""

from collections import Counter, defaultdict
from fractions import Fraction

from research import intraday_event_calibration_metrics as causal
from research import intraday_event_factorized_metrics as saved


ARMS = ("rate", "direct", "success")
HEADS = saved.HEADS
PHASES = saved.PHASES


def _require(condition, message):
    if not condition:
        raise ValueError("ES ranking: " + message)


def select_representatives(calendar, fold, roster, candidates, predictions_by_arm):
    """Use the original causal choice; restrict neither by score nor by outcomes."""
    _require(set(predictions_by_arm) == set(ARMS), "three registered arms required")
    rows = causal.select_representatives(calendar, fold, roster, candidates,
        {"mean": predictions_by_arm["rate"], "direct": predictions_by_arm["direct"],
         "calibrated": predictions_by_arm["success"]})
    mapping = dict(zip(ARMS, ("mean", "direct", "calibrated")))
    return [{**row, "schema": "ES-representative-v1", "predictions": {
        arm: row["predictions"][mapping[arm]] for arm in ARMS}} for row in rows]


def _outcome(action):
    fill, cash = action["fill"], action["net_cash"]
    _require((cash is None) == (action["target"] is None), "positive-notional cash and value support differ")
    if fill is None:
        return "unknown_fill", None
    if cash is None:
        return "unknown_cash", None
    value = Fraction(cash)
    if fill == 0:
        _require(value == 0, "no-fill cash is nonzero")
        return "not_filled", 0
    if value > 0:
        return "success", 1
    return ("loss" if value < 0 else "break_even"), 0


def _daily(day, rows, head):
    clusters = Counter(row["cluster_id"] for row in rows)
    positives = [row for row in rows if row["binary"][head] == 1]
    negatives = [row for row in rows if row["binary"][head] == 0]
    # The total number of clusters cancels in the within-day ratio. Each
    # original episode has one representative in this phase and gets equal
    # mass inside its symbol/session cluster.
    totals = dict.fromkeys(ARMS, Fraction())
    mass, pairs = Fraction(), 0
    for positive in positives:
        for negative in negatives:
            p, n = positive["cluster_id"], negative["cluster_id"]
            if p == n:
                continue
            weight = Fraction(1, clusters[p] * clusters[n])
            mass += weight
            pairs += 1
            for arm in ARMS:
                high = positive["predictions"][arm][head]
                low = negative["predictions"][arm][head]
                if high >= low:
                    totals[arm] += weight if high > low else weight / 2
    return {"session": day, "rows": len(rows), "positive": len(positives), "negative": len(negatives),
        "clusters": len(clusters), "pairs": pairs,
        "auc": {arm: str(totals[arm] / mass) if mass else None for arm in ARMS}}


def _mean(values):
    return sum(values, Fraction()) / len(values) if values else None


def _text(value):
    return None if value is None else str(value)


def _auc(days):
    return {arm: _text(_mean([Fraction(day["auc"][arm]) for day in days
                            if day["auc"][arm] is not None])) for arm in ARMS}


def _deletion(days):
    samples = {arm: [] for arm in ARMS}
    for start in range(len(days) - 4):
        reduced = _auc(days[:start] + days[start + 5:])
        for arm in ARMS:
            if reduced[arm] is not None:
                samples[arm].append(Fraction(reduced[arm]))
    return {arm: {"min": _text(min(values)) if values else None,
                  "max": _text(max(values)) if values else None, "defined": len(values)}
            for arm, values in samples.items()}


def summarize(calendar, fold, representatives, target_records):
    days = saved._calendar(calendar, fold)
    rows = saved.join_targets(representatives, target_records)
    for row in rows:
        _require(row["schema"] == "ES-representative-v1" and row["fold"] == fold and row["session"] in days
            and row["phase"] in PHASES and set(row["predictions"]) == set(ARMS)
            and all(set(scores) == set(HEADS) for scores in row["predictions"].values()),
            "off-fold or incomplete representative")
    events = {row["event_id"] for row in rows}
    identities = {(row["event_id"], row["phase"]) for row in rows}
    _require(len(rows) == len(identities) == len(events) * len(PHASES), "all phases per episode required")
    cells = []
    for phase in PHASES:
        observations = [row for row in rows if row["phase"] == phase]
        selected = [row for row in observations if row["key"] is not None]
        paired, own = defaultdict(list), {head: Counter() for head in HEADS}
        outcomes = {head: Counter() for head in HEADS}
        for row in selected:
            binary = {}
            for head in HEADS:
                category, binary[head] = _outcome(row["outcomes"][head])
                outcomes[head][category] += 1
                for arm in ARMS:
                    score = saved._score(row["predictions"][arm][head])
                    _require(score is None or arm != "rate" or 0 <= score <= 1, "invalid historical rate")
                    own[head][arm] += binary[head] is not None and score is not None
            if all(binary[head] is not None and all(row["predictions"][arm][head] is not None for arm in ARMS)
                   for head in HEADS):
                paired[row["session"]].append({**row, "binary": binary})
        actions = {}
        for head in HEADS:
            daily = [_daily(day, paired[day], head) for day in days]
            actions[head] = {"outcomes": dict(sorted(outcomes[head].items())),
                "own_known": {arm: own[head][arm] for arm in ARMS},
                "paired_positive": sum(row["positive"] for row in daily),
                "paired_negative": sum(row["negative"] for row in daily), "days": daily,
                "eligible_days": sum(row["pairs"] > 0 for row in daily), "auc": _auc(daily), "delete5": _deletion(daily)}
        cells.append({"fold": fold, "phase": phase, "episodes": len(observations), "selected": len(selected),
            "paired_known": sum(len(group) for group in paired.values()),
            "reasons": dict(sorted(Counter(row["reason"] for row in observations).items())), "actions": actions})
    return {"schema": "ES-report-v1", "fold": fold, "calendar": list(days), "cells": cells}


def pool(calendar, reports):
    days = saved._calendar(calendar)
    reports = tuple(reports)
    _require([report["fold"] for report in reports] == list(range(4)), "all four ordered folds required")
    for fold, report in enumerate(reports):
        window = list(saved._calendar(calendar, fold))
        _require(report["schema"] == "ES-report-v1" and report["calendar"] == window
            and [(cell["fold"], cell["phase"]) for cell in report["cells"]] == [(fold, phase) for phase in PHASES]
            and all([day["session"] for day in cell["actions"][head]["days"]] == window
                    for cell in report["cells"] for head in HEADS), "original fold/cell calendar differs")
    cells = [cell for report in reports for cell in report["cells"]]
    actions = {}
    for head in HEADS:
        grouped = defaultdict(list)
        for cell in cells:
            for day in cell["actions"][head]["days"]:
                if day["pairs"]:
                    grouped[day["session"]].append(day)
        actions[head] = {"eligible_days": len(grouped), "auc": {arm: _text(_mean([
            _mean([Fraction(phase["auc"][arm]) for phase in phases]) for phases in grouped.values()]))
            for arm in ARMS}}
    return {"schema": "ES-summary-v1", "calendar_days": len(days), "cells": cells, "actions": actions}
