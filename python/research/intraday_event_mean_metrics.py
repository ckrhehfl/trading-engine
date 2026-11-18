"""EQ training-mean versus stored TCN descriptive comparisons; no fitting."""

from collections import Counter
from datetime import date
from fractions import Fraction

from research import intraday_event_factorized_metrics as previous
from research import intraday_event_rank as rank


ARMS = ("mean", "direct", "factorized")
HEADS = previous.HEADS
PHASES = previous.PHASES
CLAIM = "repeated Discovery; training-only situation mean versus stored TCN forecasts; no promotion"
POLICY_CLAIM = "nominal event cash on observed saved targets; not a funded account or filled-notional return"
POOL_CLAIM = "all sixteen cells retained; no pooled best-cell AUC or promotion"


def _require(condition, message):
    if not condition:
        raise ValueError("EQ report: " + message)


def select_representatives(calendar, fold, roster, candidates, predictions_by_arm, *, components_by_arm=None):
    """Choose the original causal row once, independently of every arm's score."""
    _require(set(predictions_by_arm) == set(ARMS), "three fixed arms required")
    old = previous.select_representatives(calendar, fold, roster, candidates,
        {arm: predictions_by_arm[arm] for arm in previous.ARMS}, components_by_arm=components_by_arm)
    mean = previous._maps(predictions_by_arm["mean"])
    result = []
    for row in old:
        forecast = mean.get(row["key"], {}) if row["key"] is not None else {}
        if forecast:
            _require(forecast["session"] == row["session"], "mean prediction date differs")
        scores = {arm: {head: row["predictions"][arm][head] for head in HEADS} for arm in previous.ARMS}
        scores["mean"] = {head: previous._score(forecast.get("predictions", {}).get(head)) for head in HEADS}
        result.append({**row, "schema": "EQ-representative-v1", "predictions": scores})
    return result


def join_targets(representatives, target_records):
    return previous.join_targets(representatives, target_records)


def _paired(row):
    return all(row["outcomes"][head]["target"] is not None
        and all(row["predictions"][arm][head] is not None for arm in ARMS) for head in HEADS)


def policy_summary(days, reports):
    _require(set(reports) == set(ARMS), "three policy reports required")
    maps, outputs = {}, {}
    weeks = sorted({date.fromisoformat(day).strftime("%G-W%V") for day in days})
    _require(bool(weeks), "original calendar required")
    for arm in ARMS:
        rows = list(reports[arm])
        maps[arm] = {row["event_id"]: row for row in rows}
        _require(len(maps[arm]) == len(rows) and all(row["session"] in days and row["arm"] == arm for row in rows),
                 "policy event roster differs")
        known = [row for row in rows if row["net_cash"] is not None]
        total = sum((previous._exact(row["net_cash"]) for row in known), Fraction())
        weekly = []
        for week in weeks:
            selected = [row for row in rows if date.fromisoformat(row["session"]).strftime("%G-W%V") == week]
            weekly.append({"week": week, "fills": sum(row["fill"] == 1 for row in selected),
                           "unknown_fills": sum(row["fill"] is None for row in selected)})
        fills = sum(row["fill"] == 1 for row in rows)
        outputs[arm] = {"episodes": len(rows), "commits": sum(row["action"] in ("immediate", "wait3") for row in rows),
            "known_cash_episodes": len(known), "unknown_cash_episodes": len(rows)-len(known),
            "fills": fills, "unknown_fills": sum(row["fill"] is None for row in rows),
            "known_net_cash": str(total), "mean_known_cash_per_episode": str(total/len(known)) if known else None,
            "calendar_weeks": len(weeks), "weekly_fills": weekly, "fills_per_week": fills/len(weeks),
            "weeks_at_least_fifteen": sum(row["fills"] >= 15 for row in weekly),
            "status_counts": dict(sorted(Counter(row["status"] for row in rows).items()))}
    _require(all(maps[arm].keys() == maps["mean"].keys() for arm in ARMS), "policy populations differ")
    comparisons = {}
    for left_arm, right_arm in (("mean", "direct"), ("mean", "factorized"), ("direct", "factorized")):
        differences = []
        for key, left in maps[left_arm].items():
            right = maps[right_arm][key]
            _require(all(left[name] == right[name] for name in ("session", "symbol", "cluster_id", "family")),
                     "policy identity differs")
            if left["net_cash"] is not None and right["net_cash"] is not None:
                differences.append(previous._exact(right["net_cash"]) - previous._exact(left["net_cash"]))
        total = sum(differences, Fraction())
        comparisons[right_arm + "_minus_" + left_arm] = {
            "paired_known_episodes": len(differences),
            "paired_unknown_episodes": len(maps[left_arm])-len(differences),
            "paired_cash_difference": str(total),
            "mean_paired_cash_difference": str(total/len(differences)) if differences else None}
    return {"arms": outputs, "comparisons": comparisons, "claim": POLICY_CLAIM}


def summarize(calendar, fold, representatives, target_records, policy_reports):
    days = previous._calendar(calendar, fold)
    joined = join_targets(representatives, target_records)
    _require(all(row["fold"] == fold and row["session"] in days and set(row["predictions"]) == set(ARMS)
                 for row in joined), "off-fold or incomplete representative")
    events = {row["event_id"] for row in joined}
    identities = [(row["event_id"], row["phase"]) for row in joined]
    _require(len(identities) == len(set(identities))
        and set(identities) == {(event, phase) for event in events for phase in PHASES}, "all causal phases required")
    _require(set(policy_reports) == set(ARMS)
        and all({row["event_id"] for row in policy_reports[arm]} == events for arm in ARMS),
        "policy and representative populations differ")
    cells = []
    for phase in PHASES:
        rows = [row for row in joined if row["phase"] == phase]
        paired = [row for row in rows if _paired(row)]
        arms = {}
        for arm in ARMS:
            ranking = previous._rank_rows(paired, arm)
            weighted = rank._weights(ranking)
            arms[arm] = {}
            for head in HEADS:
                arms[arm][head] = {"own_known": sum(row["outcomes"][head]["target"] is not None
                    and row["predictions"][arm][head] is not None for row in rows),
                    "calibration": previous._calibration(paired, arm, head),
                    "cross_cluster": rank._pair_metrics(weighted, head),
                    "same_date": rank._pair_metrics(weighted, head, True),
                    "delete5_sensitivity": rank._sensitivity(days, ranking, head)}
        cells.append({"fold": fold, "phase": phase, "episodes": len(rows),
            "selected": sum(row["key"] is not None for row in rows), "paired_known": len(paired),
            "reason_counts": dict(sorted(Counter(row["reason"] for row in rows).items())), "arms": arms})
    return {"schema": "EQ-comparison-report-v1", "fold": fold, "calendar": list(days), "cells": cells,
            "policy_rows": {arm: list(policy_reports[arm]) for arm in ARMS},
            "policy": policy_summary(days, policy_reports), "claim": CLAIM}


def pool(calendar, reports):
    days, reports = previous._calendar(calendar), tuple(reports)
    _require(len(reports) == 4 and [report["fold"] for report in reports] == list(range(4)), "four ordered folds required")
    for fold, report in enumerate(reports):
        _require(report["calendar"] == list(previous._calendar(calendar, fold)), "report calendar differs")
        _require([(cell["fold"], cell["phase"]) for cell in report["cells"]] == [(fold, p) for p in PHASES],
                 "all sixteen phase cells required")
    rows = {arm: [row for report in reports for row in report["policy_rows"][arm]] for arm in ARMS}
    return {"schema": "EQ-four-fold-report-v1", "calendar_days": len(days),
            "cells": [cell for report in reports for cell in report["cells"]],
            "policy": policy_summary(days, rows), "claim": POOL_CLAIM}
