"""Producer-independent EO reconstruction using explicit unordered pairs.

Only authenticated saved records are accepted. No producer calculations,
model, execution engine or original input loaders are imported. Target and
score ordering remains exact; floating arithmetic is confined to statistical
weights. Date-pair totals allow whole-date deletion without repeating every
event-pair comparison: renormalization multiplies every surviving pair by the
same constant and therefore cancels in each reported sensitivity ratio.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from fractions import Fraction
from itertools import combinations, combinations_with_replacement, product
import math


_PHASES = ("onset", "waiting_pullback", "pullback", "confirmation")
_HEADS = ("net_immediate", "net_wait3")


def _check(condition, message):
    if not condition:
        raise ValueError("EO independent audit: " + message)


def _number(value):
    _check(type(value) in (str, int, Fraction), "exact saved numeric text required")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("EO independent audit: invalid saved numeric text") from error


def _prediction(value):
    if value is None:
        return None
    if type(value) is float:
        return Fraction(str(value)) if math.isfinite(value) else None
    return _number(value)


def _same(actual, expected, where="record"):
    if isinstance(expected, dict):
        _check(isinstance(actual, dict) and actual.keys() == expected.keys(), where + " fields differ")
        for key in expected:
            _same(actual[key], expected[key], where + "." + key)
    elif isinstance(expected, (tuple, list)):
        _check(isinstance(actual, (tuple, list)) and len(actual) == len(expected), where + " length differs")
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            _same(left, right, f"{where}[{index}]")
    elif type(expected) is float:
        _check(type(actual) in (int, float) and math.isfinite(actual)
               and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10), where + " statistic differs")
    else:
        _check(type(actual) is type(expected) and actual == expected, where + " differs")


def _calendar(calendar):
    days = tuple(calendar)
    _check(len(days) == 160 and tuple(sorted(set(days))) == days, "original 160-session calendar required")
    _check(all(date.fromisoformat(day).isoformat() == day for day in days), "canonical dates required")
    return days


def _identity(row):
    return tuple(row[key] for key in ("event_id", "session", "symbol", "cluster_id", "fold", "phase"))


def _admission(row):
    if row["entry_admissibility"] != "admissible":
        return "entry_" + row["entry_admissibility"]
    if row["initial_denominator"] is None:
        return "unknown_denominator"
    if _number(row["initial_denominator"]) <= 0:
        return "nonpositive_denominator"
    if not row["scope_supported"]:
        return "unsupported_scope"
    if any(_prediction(row["predictions"][head]) is None for head in _HEADS):
        return "missing_paired_value_heads"
    return "eligible"


def verify_selection(roster_doc, causal_rows, saved_selection):
    """Reconstruct all episode/phase choices before inspecting any target."""
    calendar = _calendar(roster_doc["calendar"])
    folds = {day: index // 40 for index, day in enumerate(calendar)}
    _same(roster_doc["fold_by_day"], folds, "fold calendar")
    events = roster_doc["events"]
    _check(isinstance(events, dict), "event mapping required")
    for event_id, event in events.items():
        _check(event_id == event["event_id"] and event["session"] in folds
               and type(event["onset_decision_offset"]) is int and event["onset_decision_offset"] >= 0
               and all(type(event[key]) is str and event[key] for key in
                       ("event_id", "session", "symbol", "cluster_id")), "invalid original event")
    grouped, seen = defaultdict(list), set()
    for row in causal_rows:
        event = events.get(row["event_id"])
        _check(event is not None, "foreign event")
        clock = row["decision_offset"]
        _check(type(clock) is int and clock >= event["onset_decision_offset"]
               and row["onset_decision_offset"] == event["onset_decision_offset"]
               and row["key"] == event["event_id"] + "@" + str(clock)
               and row["schema"] == "EN-causal-query-v1"
               and type(row["fold"]) is int and row["fold"] == folds[event["session"]]
               and all(row[key] == event[key] for key in ("event_id", "session", "symbol", "cluster_id")),
               "causal identity differs")
        _check(row["key"] not in seen, "duplicate causal key")
        seen.add(row["key"])
        expected_phase = ("onset" if clock == event["onset_decision_offset"] else
                          {"WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback",
                           "CONFIRMED": "confirmation"}.get(row["state"], "invalid_or_terminal"))
        _check(row["phase"] == expected_phase and type(row["scope_supported"]) is bool
               and row["entry_admissibility"] in ("admissible", "not_admissible", "unknown")
               and set(row["predictions"]) == set(_HEADS), "causal phase or support differs")
        reason = _admission(row)
        if expected_phase in _PHASES:
            grouped[event["event_id"], expected_phase].append((clock, reason, row))
    expected = []
    for event_id in sorted(events):
        event = events[event_id]
        for phase in _PHASES:
            records = sorted(grouped[event_id, phase], key=lambda value: value[0])
            chosen = next((row for _, reason, row in records if reason == "eligible"), None)
            expected.append({"schema": "EO-representative-v1",
                **{key: event[key] for key in ("event_id", "session", "symbol", "cluster_id")},
                "fold": folds[event["session"]], "phase": phase,
                "key": None if chosen is None else chosen["key"],
                "decision_offset": None if chosen is None else chosen["decision_offset"],
                "initial_denominator": None if chosen is None else chosen["initial_denominator"],
                "predictions": dict.fromkeys(_HEADS) if chosen is None else deepcopy(chosen["predictions"]),
                "reason": ("first_eligible_causal" if chosen is not None else
                           "no_eligible_causal" if records else "no_causal_phase"),
                "support": {"causal_rows": len(records), "eligible_rows": sum(reason == "eligible" for _, reason, _ in records),
                            "reason_counts": dict(sorted(Counter(reason for _, reason, _ in records).items()))},
                "causal": deepcopy(chosen)})
    _same(saved_selection, expected, "representatives")
    return {"schema": "EO-independent-selection-verification-v1", "representatives": len(expected),
            "causal_rows": len(seen)}


def verify_join(selections, query_rows, saved_join):
    """Authenticate selected original EN ratios and retain target unknowns."""
    selections, queries = list(selections), {}
    for query in query_rows:
        _check(query["key"] not in queries, "duplicate query-error key")
        queries[query["key"]] = query
    expected, coordinates, selected_keys = [], set(), set()
    for selection in selections:
        coordinate = (selection["event_id"], selection["phase"])
        _check(coordinate not in coordinates and selection["schema"] == "EO-representative-v1",
               "duplicate representative")
        coordinates.add(coordinate)
        key, causal = selection["key"], selection["causal"]
        if key is None:
            _check(causal is None and selection["decision_offset"] is None
                   and selection["initial_denominator"] is None
                   and selection["predictions"] == dict.fromkeys(_HEADS), "absent representative has values")
            heads = {head: {"prediction": None, "target": None, "target_state": "no_eligible_causal"} for head in _HEADS}
        else:
            _check(key not in selected_keys and key in queries and causal is not None
                   and _admission(causal) == "eligible", "missing or duplicate selected query")
            selected_keys.add(key)
            _check(_identity(selection) == _identity(causal) and all(selection[field] == causal[field] for field in
                   ("key", "decision_offset", "initial_denominator", "predictions")), "selected causal projection differs")
            query = queries[key]
            _check(query["schema"] == "EN-query-value-v1" and set(query["heads"]) == set(_HEADS), "invalid measured query")
            for field, value in causal.items():
                if field != "schema":
                    _check(field in query, "query causal field absent: " + field)
                    _same(query[field], value, "query causal." + field)
            heads = deepcopy(query["heads"])
            for head, observed in heads.items():
                _same(observed["prediction"], causal["predictions"][head], "saved prediction")
                _same(observed["denominator"], causal["initial_denominator"], "saved denominator")
                known = observed["target_state"] == "known"
                _check((observed["target"] is not None) == known and type(observed["scored"]) is bool
                       and observed["scored"] == known, "unknown target became a scored value")
                if not known:
                    _check(observed["error"] is None, "unknown target has error")
                    continue
                target, denominator = _number(observed["target"]), _number(observed["denominator"])
                _check(denominator > 0 and observed["net_cash"] is not None
                       and target * denominator == _number(observed["net_cash"])
                       and observed["training_target"] is not None
                       and target == _number(observed["training_target"]), "exact original target ratio differs")
                maturity = observed["matured_offset"]
                _check(type(maturity) is int and causal["decision_offset"] <= maturity < causal["session_length"],
                       "original target maturity differs")
                if observed["fill"] == 0 or observed["raw_status"] in ("no_fill", "no_entry"):
                    _check(target == 0, "no-fill target is not zero")
                _check(observed["error"] is not None
                       and _number(observed["error"]) == _prediction(observed["prediction"]) - target,
                       "saved prediction error differs")
            _check(query["paired"]["scored"] is all(heads[head]["target_state"] == "known" for head in _HEADS),
                   "original paired support differs")
        expected.append({**deepcopy(selection), "schema": "EO-rank-row-v1", "heads": heads,
                         "joint_known": all(heads[head]["target_state"] == "known" for head in _HEADS)})
    _same(saved_join, expected, "joined representatives")
    return {"schema": "EO-independent-join-verification-v1", "representatives": len(expected),
            "selected_queries": len(selected_keys), "original_queries": len(queries)}


def _empty_metric():
    return {"value": None, "pairs": 0, "pair_weight": 0.0, "credit_weight": 0.0,
            "score_tie_pairs": 0, "score_tie_weight": 0.0}


def _pair_groups(rows, head):
    """Compare each pair once, independently of any sorted-rank algorithm."""
    if not rows:
        return []
    clusters = defaultdict(Counter)
    for row in rows:
        clusters[row["session"]][row["symbol"]] += 1
    # Integer ranks preserve exact rational ordering without Fraction work
    # inside the quadratic loop. Their values are never statistical scores.
    scores = {_prediction(row["predictions"][head]) for row in rows}
    targets = {_number(row["heads"][head]["target"]) for row in rows}
    score_rank = {value: index for index, value in enumerate(sorted(scores))}
    target_rank = {value: index for index, value in enumerate(sorted(targets))}
    by_date = defaultdict(list)
    for row in rows:
        day, symbol = row["session"], row["symbol"]
        target = _number(row["heads"][head]["target"])
        weight = 1.0 / (len(clusters) * len(clusters[day]) * clusters[day][symbol])
        by_date[day].append((symbol, score_rank[_prediction(row["predictions"][head])],
                             target_rank[target], target > 0, weight))
    groups = []
    for left_day, right_day in combinations_with_replacement(sorted(by_date), 2):
        same_day = left_day == right_day
        iterator = combinations(by_date[left_day], 2) if same_day else product(by_date[left_day], by_date[right_day])
        masses = {name: {"mass": [], "credit": [], "score_tie": []} for name in ("auc", "concordance")}
        target_ties, excluded = [], 0
        for left, right in iterator:
            if same_day and left[0] == right[0]:
                excluded += 1
                continue
            mass = left[4] * right[4]
            score_order = (left[1] > right[1]) - (left[1] < right[1])
            target_order = (left[2] > right[2]) - (left[2] < right[2])
            if target_order == 0:
                target_ties.append(mass)
            for name, comparison in (("auc", int(left[3]) - int(right[3])), ("concordance", target_order)):
                if comparison == 0:
                    continue
                data = masses[name]
                data["mass"].append(mass)
                if score_order == 0:
                    data["credit"].append(mass * 0.5)
                    data["score_tie"].append(mass)
                elif score_order * comparison > 0:
                    data["credit"].append(mass)
        metrics = {}
        for name, data in masses.items():
            metrics[name] = {"pairs": len(data["mass"]), "pair_weight": math.fsum(data["mass"]),
                             "credit_weight": math.fsum(data["credit"]),
                             "score_tie_pairs": len(data["score_tie"]),
                             "score_tie_weight": math.fsum(data["score_tie"])}
        metrics.update(target_tie_pairs=len(target_ties), target_tie_weight=math.fsum(target_ties),
                       excluded_same_cluster_pairs=excluded)
        groups.append((left_day, right_day, metrics))
    return groups


def _combine(groups, *, same_date=False, omitted=()):
    retained = [metric for left, right, metric in groups
                if left not in omitted and right not in omitted and (not same_date or left == right)]
    result = {}
    for name in ("auc", "concordance"):
        metric = _empty_metric()
        for key in ("pairs", "score_tie_pairs"):
            metric[key] = sum(group[name][key] for group in retained)
        for key in ("pair_weight", "credit_weight", "score_tie_weight"):
            metric[key] = math.fsum(group[name][key] for group in retained)
        metric["value"] = (metric["credit_weight"] / metric["pair_weight"] if metric["pair_weight"] else None)
        result[name] = metric
    result["target_tie_pairs"] = sum(group["target_tie_pairs"] for group in retained)
    result["target_tie_weight"] = math.fsum(group["target_tie_weight"] for group in retained)
    result["excluded_same_cluster_pairs"] = sum(group["excluded_same_cluster_pairs"] for group in retained)
    return result


def pair_metrics(rows, head):
    """Independent explicit-pair witness for invented arithmetic tests."""
    _check(head in _HEADS, "unknown head")
    return _combine(_pair_groups(list(rows), head))


def _sensitivity(groups, fold_calendar):
    result = {"kind": "delete_five_dates_sensitivity_not_confidence_interval", "blocks": 8}
    for name, same_date in (("cross_cluster", False), ("same_date", True)):
        values = {metric: [] for metric in ("auc", "concordance")}
        for start in range(0, 40, 5):
            result_after_delete = _combine(groups, same_date=same_date, omitted=frozenset(fold_calendar[start:start + 5]))
            for metric in values:
                values[metric].append(result_after_delete[metric]["value"])
        result[name] = {}
        for metric, observed in values.items():
            defined = [value for value in observed if value is not None]
            result[name][metric] = {"valid": len(defined), "undefined": 8 - len(defined),
                                    "min": min(defined) if defined else None,
                                    "max": max(defined) if defined else None}
    return result


def _summary(calendar, rows):
    days, rows = _calendar(calendar), list(rows)
    seen, by_cell, event_phases, event_identities = set(), defaultdict(list), defaultdict(set), {}
    for row in rows:
        identity = row["event_id"], row["phase"]
        _check(row["schema"] == "EO-rank-row-v1" and identity not in seen and row["phase"] in _PHASES
               and row["session"] in days and type(row["fold"]) is int
               and row["fold"] == days.index(row["session"]) // 40, "duplicate or off-calendar representative")
        seen.add(identity)
        event_phases[row["event_id"]].add(row["phase"])
        event_identity = tuple(row[field] for field in ("session", "symbol", "cluster_id", "fold"))
        _check(event_identities.setdefault(row["event_id"], event_identity) == event_identity,
               "original event identity differs between phases")
        _check(type(row["joint_known"]) is bool
               and row["joint_known"] == (row["key"] is not None and all(
                   row["heads"][head]["target_state"] == "known" and row["heads"][head]["target"] is not None
                   for head in _HEADS)), "joint known support differs")
        for head in _HEADS:
            _check((row["heads"][head]["target"] is not None) == (row["heads"][head]["target_state"] == "known"),
                   "unknown target is numeric")
        by_cell[row["fold"], row["phase"]].append(row)
    _check(all(phases == set(_PHASES) for phases in event_phases.values()), "missing episode-phase representative")
    cells = []
    for fold in range(4):
        for phase in _PHASES:
            cell_rows = by_cell[fold, phase]
            scored = [row for row in cell_rows if row["joint_known"]]
            heads = {}
            for head in _HEADS:
                known = [row for row in cell_rows if row["heads"][head]["target_state"] == "known"]
                groups = _pair_groups(scored, head)
                heads[head] = {"support": {"known": len(known), "unknown": len(cell_rows) - len(known),
                    "positive": sum(_number(row["heads"][head]["target"]) > 0 for row in known),
                    "zero": sum(_number(row["heads"][head]["target"]) == 0 for row in known),
                    "negative": sum(_number(row["heads"][head]["target"]) < 0 for row in known),
                    "scored_positive": sum(_number(row["heads"][head]["target"]) > 0 for row in scored),
                    "scored_zero": sum(_number(row["heads"][head]["target"]) == 0 for row in scored),
                    "scored_negative": sum(_number(row["heads"][head]["target"]) < 0 for row in scored),
                    "scored": len(scored), "scored_dates": len({row["session"] for row in scored}),
                    "scored_clusters": len({(row["session"], row["symbol"]) for row in scored}),
                    "target_states": dict(sorted(Counter(row["heads"][head]["target_state"] for row in cell_rows).items()))},
                    "cross_cluster": _combine(groups), "same_date": _combine(groups, same_date=True),
                    "delete5_sensitivity": _sensitivity(groups, days[fold * 40:(fold + 1) * 40])}
            cells.append({"fold": fold, "phase": phase, "calendar_days": 40,
                          "support": {"episodes": len(cell_rows), "selected": sum(row["key"] is not None for row in cell_rows),
                                      "joint_known": len(scored), "dates": len({row["session"] for row in cell_rows}),
                                      "clusters": len({(row["session"], row["symbol"]) for row in cell_rows}),
                                      "reasons": dict(sorted(Counter(row["reason"] for row in cell_rows).items()))},
                          "heads": heads})
    return {"schema": "EO-rank-summary-v1", "calendar_days": len(days), "cells": cells}


def verify_summary(calendar, joined_rows, saved_summary):
    """Verify every fixed cell, pair mass and original-calendar deletion."""
    _same(saved_summary, _summary(calendar, joined_rows), "summary")
    return {"schema": "EO-independent-summary-verification-v1", "cells": 16,
            "heads_per_cell": 2, "date_blocks_per_cell": 8,
            "algorithm": "explicit_unordered_event_pairs_by_date_pair"}
