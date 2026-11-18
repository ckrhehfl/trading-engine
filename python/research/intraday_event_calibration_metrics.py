"""ER training-only calibrated versus stored direct and mean comparisons."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction

from research import intraday_event_evaluation as original
from research import intraday_event_factorized_metrics as previous
from research import intraday_event_rank as rank


ARMS = ("mean", "direct", "calibrated")
HEADS = previous.HEADS
PHASES = previous.PHASES
CLAIM = "repeated Discovery; training-only calibrated direct versus stored direct and situation mean; no promotion"
POLICY_CLAIM = "nominal event cash on observed saved targets; not a funded account or filled-notional return"
POOL_CLAIM = "all sixteen cells retained; no pooled best-cell AUC or promotion"


def _require(condition, message):
    if not condition:
        raise ValueError("ER report: " + message)


def select_representatives(calendar, fold, roster, candidates, predictions_by_arm, *, components_by_arm=None):
    days = previous._calendar(calendar, fold)
    _require(set(predictions_by_arm) == set(ARMS), "three fixed arms required")
    predictions = {arm: previous._maps(predictions_by_arm[arm]) for arm in ARMS}
    episodes = {event.event_id: event for event in roster}
    _require(len(episodes) == len(roster) and all(event.session in days and event.family == "C_FIRSTPULLBACK"
             for event in roster), "original C event roster differs")
    groups, keys = defaultdict(list), set()
    for candidate in candidates:
        event = candidate.episode
        _require(episodes.get(event.event_id) == event and candidate.key not in keys, "causal candidate differs")
        keys.add(candidate.key)
        phase = ("onset" if candidate.frame.decision_offset == event.onset_decision_offset else
                 {"WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback",
                  "CONFIRMED": "confirmation"}.get(candidate.frame.state))
        if phase is None:
            continue
        reason = original._admission(candidate)
        denominator = original._denominator(candidate)
        if reason is None and (denominator is None or denominator <= 0):
            reason = "nonpositive_or_unknown_denominator"
        if reason is None and candidate.query is None:
            reason = "unknown_chart"
        if reason is None and (candidate.query.peer is None or any(v is None for v in candidate.query.peer)):
            reason = "unknown_peer"
        groups[event.event_id, phase].append((candidate, reason, denominator))
    result = []
    for identity, event in sorted(episodes.items()):
        for phase in PHASES:
            rows = groups[identity, phase]
            eligible = [item for item in rows if item[1] is None]
            chosen = min(eligible, key=lambda item: item[0].frame.decision_offset) if eligible else None
            candidate, _, denominator = chosen if chosen else (None, None, None)
            scores = {}
            for arm in ARMS:
                record = {} if candidate is None else predictions[arm].get(candidate.key, {})
                if record:
                    _require(record["session"] == event.session, "prediction date differs")
                values = record.get("predictions", {})
                scores[arm] = {head: previous._score(values.get(head)) for head in HEADS}
            result.append({"schema": "ER-representative-v1", "event_id": identity, "session": event.session,
                "symbol": event.symbol, "cluster_id": event.cluster_id, "fold": fold, "phase": phase,
                "key": None if candidate is None else candidate.key,
                "decision_offset": None if candidate is None else candidate.frame.decision_offset,
                "session_length": None if candidate is None else candidate.label_query.session_length,
                "denominator": None if denominator is None else str(denominator), "predictions": scores,
                "reason": "first_eligible_causal" if chosen else "no_eligible_causal" if rows else "no_causal_phase",
                "support": {"causal_rows": len(rows), "eligible_rows": len(eligible),
                            "reasons": dict(sorted(Counter(reason or "eligible" for _, reason, _ in rows).items()))}})
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
    for left_arm, right_arm in (("mean", "direct"), ("mean", "calibrated"), ("direct", "calibrated")):
        differences = []
        fill_differences = []
        commit_difference = changed_actions = 0
        for key, left in maps[left_arm].items():
            right = maps[right_arm][key]
            _require(all(left[name] == right[name] for name in ("session", "symbol", "cluster_id", "family")),
                     "policy identity differs")
            if left["net_cash"] is not None and right["net_cash"] is not None:
                differences.append(previous._exact(right["net_cash"]) - previous._exact(left["net_cash"]))
            if left["fill"] is not None and right["fill"] is not None:
                fill_differences.append(right["fill"] - left["fill"])
            commit_difference += int(right["action"] in ("immediate", "wait3")) - int(left["action"] in ("immediate", "wait3"))
            changed_actions += left["action"] != right["action"]
        total = sum(differences, Fraction())
        comparisons[right_arm + "_minus_" + left_arm] = {
            "paired_known_episodes": len(differences),
            "paired_unknown_episodes": len(maps[left_arm])-len(differences),
            "paired_cash_difference": str(total),
            "mean_paired_cash_difference": str(total/len(differences)) if differences else None,
            "commit_difference": commit_difference, "changed_actions": changed_actions,
            "paired_known_fill_episodes": len(fill_differences),
            "paired_unknown_fill_episodes": len(maps[left_arm])-len(fill_differences),
            "paired_fill_difference": sum(fill_differences)}
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
    return {"schema": "ER-comparison-report-v1", "fold": fold, "calendar": list(days), "cells": cells,
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
    return {"schema": "ER-four-fold-report-v1", "calendar_days": len(days),
            "cells": [cell for report in reports for cell in report["cells"]],
            "policy": policy_summary(days, rows), "claim": POOL_CLAIM}
