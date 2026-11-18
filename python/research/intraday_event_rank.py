"""Outcome-blind EO representatives and descriptive saved-value ranking.

No loaders, inference, fitting or trading policy are present. Cash and ranking
comparisons remain exact; only descriptive weights cross the float boundary.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from fractions import Fraction
from itertools import groupby
import math


PHASES = ("onset", "waiting_pullback", "pullback", "confirmation")
Q_HEADS = ("net_immediate", "net_wait3")
IDENTITY = ("event_id", "session", "symbol", "cluster_id", "fold", "phase")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _exact(value):
    _require(type(value) in (str, int, Fraction), "Exact amount required; no float cash")
    return Fraction(value)


def _score(value):
    if value is None:
        return None
    _require(type(value) in (str, int, float, Fraction), "Invalid prediction type")
    if type(value) is float:
        return Fraction(str(value)) if math.isfinite(value) else None
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("Nonfinite or invalid original prediction") from exc


def _calendar(calendar):
    calendar = tuple(calendar)
    _require(len(calendar) == 160 and tuple(sorted(set(calendar))) == calendar,
             "Original 160 ordered unique evaluation dates required")
    for day in calendar:
        _require(type(day) is str and date.fromisoformat(day).isoformat() == day,
                 "Canonical evaluation date required")
    return calendar, {day: index // 40 for index, day in enumerate(calendar)}


def _eligibility(row):
    if row["entry_admissibility"] != "admissible":
        return "entry_" + row["entry_admissibility"]
    denominator = row["initial_denominator"]
    if denominator is None:
        return "unknown_denominator"
    if _exact(denominator) <= 0:
        return "nonpositive_denominator"
    if not row["scope_supported"]:
        return "unsupported_scope"
    if any(_score(row["predictions"][head]) is None for head in Q_HEADS):
        return "missing_paired_value_heads"
    return "eligible"


def select_representatives(roster_doc, causal_rows):
    """Select the earliest admissible frame per original episode and phase.

    The roster's later confirmation/terminal fields are deliberately never read.
    All causal frames are validated, including frames outside the four phases.
    """
    _, fold_by_day = _calendar(roster_doc["calendar"])
    _require(roster_doc["fold_by_day"] == fold_by_day, "Original fold calendar differs")
    events = roster_doc["events"]
    _require(type(events) is dict, "Original event mapping required")
    for key, event in events.items():
        _require(key == event["event_id"] and event["session"] in fold_by_day
                 and all(type(event[name]) is str and event[name] for name in
                         ("event_id", "session", "symbol", "cluster_id"))
                 and type(event["onset_decision_offset"]) is int
                 and event["onset_decision_offset"] >= 0, "Invalid roster identity or onset")
    grouped, seen, clocks = defaultdict(list), set(), set()
    for row in causal_rows:
        event = events.get(row["event_id"])
        _require(event is not None, "Foreign original episode")
        clock = row["decision_offset"]
        _require(row["schema"] == "EN-causal-query-v1"
                 and all(row[name] == event[name] for name in
                         ("event_id", "session", "symbol", "cluster_id"))
                 and type(row["fold"]) is int and row["fold"] == fold_by_day[row["session"]]
                 and row["onset_decision_offset"] == event["onset_decision_offset"]
                 and type(clock) is int and clock >= event["onset_decision_offset"]
                 and row["key"] == f"{event['event_id']}@{clock}", "Causal identity or clock differs")
        _require(row["key"] not in seen and (row["event_id"], clock) not in clocks,
                 "Duplicate original causal key or clock")
        seen.add(row["key"])
        clocks.add((row["event_id"], clock))
        phase = ("onset" if clock == event["onset_decision_offset"] else
                 {"WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback",
                  "CONFIRMED": "confirmation"}.get(row["state"], "invalid_or_terminal"))
        _require(row["phase"] == phase and row["entry_admissibility"] in
                 ("admissible", "not_admissible", "unknown")
                 and type(row["scope_supported"]) is bool
                 and set(row["predictions"]) == set(Q_HEADS), "Invalid causal phase or support")
        reason = _eligibility(row)
        if phase in PHASES:
            grouped[(row["event_id"], phase)].append((row, reason))
    result = []
    for event_id, event in sorted(events.items()):
        for phase in PHASES:
            records = grouped[(event_id, phase)]
            eligible = [row for row, reason in records if reason == "eligible"]
            chosen = min(eligible, key=lambda row: row["decision_offset"]) if eligible else None
            result.append({"schema": "EO-representative-v1",
                           **{key: event[key] for key in IDENTITY[:4]},
                           "fold": fold_by_day[event["session"]], "phase": phase,
                           "key": None if chosen is None else chosen["key"],
                           "decision_offset": None if chosen is None else chosen["decision_offset"],
                           "initial_denominator": None if chosen is None else chosen["initial_denominator"],
                           "predictions": dict.fromkeys(Q_HEADS) if chosen is None else deepcopy(chosen["predictions"]),
                           "reason": "first_eligible_causal" if chosen else
                                     "no_eligible_causal" if records else "no_causal_phase",
                           "support": {"causal_rows": len(records), "eligible_rows": len(eligible),
                                       "reason_counts": dict(sorted(Counter(reason for _, reason in records).items()))},
                           "causal": deepcopy(chosen)})
    return result


def join_targets(selections, query_rows):
    """Join original EN targets after the complete representative set is sealed."""
    selections = list(selections)
    selected_by_key, coordinates = {}, set()
    for row in selections:
        coordinate = (row["event_id"], row["phase"])
        _require(row["schema"] == "EO-representative-v1" and row["phase"] in PHASES
                 and coordinate not in coordinates,
                 "Duplicate or invalid representative")
        coordinates.add(coordinate)
        if row["key"] is not None:
            causal = row["causal"]
            _require(causal is not None and _eligibility(causal) == "eligible"
                     and all(row[name] == causal[name] for name in
                             (*IDENTITY, "key", "decision_offset", "initial_denominator", "predictions"))
                     and row["key"] not in selected_by_key, "Representative differs from sealed causal row")
            selected_by_key[row["key"]] = row
        else:
            _require(row["causal"] is None and row["decision_offset"] is None
                     and row["initial_denominator"] is None
                     and row["predictions"] == dict.fromkeys(Q_HEADS), "Absent representative carries values")
    measured, seen = {}, set()
    for query in query_rows:
        key = query["key"]
        _require(key not in seen, "Duplicate original query-error key")
        seen.add(key)
        if key not in selected_by_key:
            continue
        selection = selected_by_key[key]
        causal = selection["causal"]
        _require(query["schema"] == "EN-query-value-v1"
                 and all(name in query and query[name] == value
                         for name, value in causal.items() if name != "schema"),
                 "Query-error causal projection differs from sealed selection")
        _require(set(query["heads"]) == set(Q_HEADS), "Original pair of heads required")
        for head in Q_HEADS:
            observed = query["heads"][head]
            _require(observed["prediction"] == causal["predictions"][head]
                     and observed["denominator"] == causal["initial_denominator"],
                     "Original prediction or denominator differs")
            known = observed["target_state"] == "known"
            _require((observed["target"] is not None) == known
                     and type(observed["scored"]) is bool and observed["scored"] == known,
                     "Unknown or inapplicable target cannot be a scored zero")
            if known:
                target, denominator = _exact(observed["target"]), _exact(observed["denominator"])
                _require(observed["net_cash"] is not None and denominator > 0
                         and target == _exact(observed["net_cash"]) / denominator
                         and observed["training_target"] is not None
                         and target == _exact(observed["training_target"]), "Original exact target ratio differs")
                _require(type(observed["matured_offset"]) is int
                         and causal["decision_offset"] <= observed["matured_offset"] < causal["session_length"],
                         "Known target is not mature in original session")
                if observed["raw_status"] in ("no_entry", "no_fill") or observed["fill"] == 0:
                    _require(target == 0, "Known no-fill target must be exact zero")
                _require(observed["error"] is not None
                         and _exact(observed["error"]) == _score(observed["prediction"]) - target,
                         "Original head error differs")
            else:
                _require(observed["error"] is None, "Unknown target carries a numerical error")
        joint = all(query["heads"][head]["target_state"] == "known" for head in Q_HEADS)
        _require(query["paired"]["scored"] is joint, "Original paired support differs")
        measured[key] = deepcopy(query["heads"])
    _require(set(measured) == set(selected_by_key), "Missing selected original query-error row")
    result = []
    for row in selections:
        heads = measured[row["key"]] if row["key"] is not None else {
            head: {"prediction": None, "target": None, "target_state": "no_eligible_causal"} for head in Q_HEADS}
        result.append({**deepcopy(row), "schema": "EO-rank-row-v1", "heads": heads,
                       "joint_known": all(heads[head]["target_state"] == "known" for head in Q_HEADS)})
    return result


class _Fenwick:
    """Prefix mass/counts; target groups are inserted only after being scored."""

    def __init__(self, size):
        self.mass = [0.0] * (size + 1)
        self.count = [0] * (size + 1)

    def add(self, index, weight):
        while index < len(self.mass):
            self.mass[index] += weight
            self.count[index] += 1
            index += index & -index

    def prefix(self, index):
        mass, count = [], 0
        while index:
            mass.append(self.mass[index])
            count += self.count[index]
            index -= index & -index
        return math.fsum(mass), count


def _ordered_pairs(points, binary):
    # Each point is (exact score, exact target, floating weight).
    ordered = sorted(points, key=lambda p: p[1] > 0 if binary else p[1])
    ranks = {score: index + 1 for index, score in enumerate(sorted({p[0] for p in points}))}
    tree = _Fenwick(len(ranks))
    terms, masses, tie_masses, pairs, tie_pairs = [], [], [], 0, 0
    previous_weight, previous_count = 0.0, 0
    for _, values in groupby(ordered, key=lambda p: p[1] > 0 if binary else p[1]):
        group = list(values)
        for score, _, weight in group:
            rank = ranks[score]
            less, less_count = tree.prefix(rank - 1)
            through, through_count = tree.prefix(rank)
            equal = math.fsum((through, -less))
            terms.append(weight * math.fsum((less, equal / 2)))
            masses.append(weight * previous_weight)
            tie_masses.append(weight * equal)
            pairs += previous_count
            tie_pairs += through_count - less_count
        for score, _, weight in group:
            tree.add(ranks[score], weight)
        previous_weight = math.fsum((previous_weight, *(p[2] for p in group)))
        previous_count += len(group)
    return {"pairs": pairs, "pair_weight": math.fsum(masses), "credit_weight": math.fsum(terms),
            "score_tie_pairs": tie_pairs, "score_tie_weight": math.fsum(tie_masses)}


def _target_ties(points):
    count, masses = 0, []
    for _, group in groupby(sorted(points, key=lambda p: p[1]), key=lambda p: p[1]):
        weights = [p[2] for p in group]
        count += len(weights) * (len(weights) - 1) // 2
        masses.append(math.fsum((math.fsum(weights) ** 2, -math.fsum(w * w for w in weights))) / 2)
    return count, math.fsum(masses)


def _subtract(total, exclusions):
    result = {name: (total[name] - sum(row[name] for row in exclusions) if type(total[name]) is int
                     else math.fsum((total[name], *(-row[name] for row in exclusions)))) for name in total}
    if result["pairs"] == 0:
        result["pair_weight"] = result["credit_weight"] = result["score_tie_weight"] = 0.0
    else:
        # Roundoff in subtraction cannot create out-of-range descriptive credit.
        result["credit_weight"] = min(result["pair_weight"], max(0.0, result["credit_weight"]))
        result["score_tie_weight"] = min(result["pair_weight"], max(0.0, result["score_tie_weight"]))
    result["value"] = result["credit_weight"] / result["pair_weight"] if result["pairs"] else None
    return result


def _pair_metrics(weighted, head, same_date=False):
    groups = defaultdict(list)
    for row, weight in weighted:
        groups[row["session"] if same_date else "all"].append((row, weight))
    aggregates = {name: [] for name in ("auc", "concordance")}
    tie_counts, tie_weights, excluded_pairs = [], [], 0
    for group in groups.values():
        points, clusters = [], defaultdict(list)
        for row, weight in group:
            point = (_score(row["predictions"][head]), _exact(row["heads"][head]["target"]), weight)
            points.append(point)
            clusters[(row["session"], row["symbol"])].append(point)
        excluded_pairs += sum(len(cluster) * (len(cluster) - 1) // 2 for cluster in clusters.values())
        for name, binary in (("auc", True), ("concordance", False)):
            aggregates[name].append(_subtract(_ordered_pairs(points, binary),
                                    [_ordered_pairs(cluster, binary) for cluster in clusters.values()]))
        count, mass = _target_ties(points)
        excluded = [_target_ties(cluster) for cluster in clusters.values()]
        remaining = count - sum(part[0] for part in excluded)
        tie_counts.append(remaining)
        tie_weights.append(math.fsum((mass, *(-part[1] for part in excluded))) if remaining else 0.0)
    result = {}
    for name, records in aggregates.items():
        total = {key: sum(r[key] for r in records) if key in ("pairs", "score_tie_pairs") else
                      math.fsum(r[key] for r in records)
                 for key in ("pairs", "pair_weight", "credit_weight", "score_tie_pairs", "score_tie_weight")}
        total["value"] = total["credit_weight"] / total["pair_weight"] if total["pairs"] else None
        result[name] = total
    return {**result, "target_tie_pairs": sum(tie_counts), "target_tie_weight": math.fsum(tie_weights),
            "excluded_same_cluster_pairs": excluded_pairs}


def _weights(rows):
    dates = defaultdict(lambda: defaultdict(list))
    for row in rows:
        dates[row["session"]][row["symbol"]].append(row)
    return [(row, 1 / len(dates) / len(clusters) / len(episodes))
            for clusters in dates.values() for episodes in clusters.values() for row in episodes]


def _sensitivity(days, scored, head):
    samples = {scope: {metric: [] for metric in ("auc", "concordance")}
               for scope in ("cross_cluster", "same_date")}
    for start in range(0, 40, 5):
        removed = set(days[start:start + 5])
        weighted = _weights([row for row in scored if row["session"] not in removed])
        for scope in samples:
            metrics = _pair_metrics(weighted, head, scope == "same_date")
            for metric in samples[scope]:
                samples[scope][metric].append(metrics[metric]["value"])
    result = {"kind": "delete_five_dates_sensitivity_not_confidence_interval", "blocks": 8}
    for scope, metrics in samples.items():
        result[scope] = {}
        for metric, values in metrics.items():
            known = [value for value in values if value is not None]
            result[scope][metric] = {"valid": len(known), "undefined": len(values) - len(known),
                                     "min": min(known) if known else None, "max": max(known) if known else None}
    return result


def summarize(calendar, rows):
    """Report all 16 fixed cells, never a selected macro score or significance."""
    calendar, fold_by_day = _calendar(calendar)
    rows = list(rows)
    coordinates, grouped = set(), defaultdict(list)
    events, identities = defaultdict(set), {}
    for row in rows:
        coordinate = (row["event_id"], row["phase"])
        _require(row["schema"] == "EO-rank-row-v1" and row["session"] in fold_by_day
                 and type(row["fold"]) is int and row["fold"] == fold_by_day[row["session"]]
                 and row["phase"] in PHASES and coordinate not in coordinates,
                 "Duplicate or off-calendar rank row")
        coordinates.add(coordinate)
        events[row["event_id"]].add(row["phase"])
        identity = tuple(row[name] for name in IDENTITY[:5])
        _require(identities.setdefault(row["event_id"], identity) == identity,
                 "Episode identity differs across phases")
        _require(type(row["joint_known"]) is bool and row["joint_known"] ==
                 all(row["heads"][head]["target_state"] == "known" for head in Q_HEADS),
                 "Joint-known support differs")
        for head in Q_HEADS:
            item = row["heads"][head]
            _require((item["target"] is not None) == (item["target_state"] == "known"),
                     "Unknown rank target cannot be numeric")
            if item["target"] is not None:
                _exact(item["target"])
            if row["joint_known"]:
                _require(row["key"] is not None and _score(row["predictions"][head]) is not None,
                         "Scored row lacks an eligible prediction")
        grouped[(row["fold"], row["phase"])].append(row)
    _require(all(phases == set(PHASES) for phases in events.values()), "Missing episode-phase representative")
    cells = []
    for fold in range(4):
        days = calendar[fold * 40:(fold + 1) * 40]
        for phase in PHASES:
            cell = grouped[(fold, phase)]
            scored = [row for row in cell if row["joint_known"]]
            weighted = _weights(scored)
            heads = {}
            for head in Q_HEADS:
                values = [_exact(row["heads"][head]["target"]) for row in cell
                          if row["heads"][head]["target_state"] == "known"]
                scored_values = [_exact(row["heads"][head]["target"]) for row in scored]
                heads[head] = {"support": {"known": len(values), "unknown": len(cell) - len(values),
                    "positive": sum(value > 0 for value in values), "zero": sum(value == 0 for value in values),
                    "negative": sum(value < 0 for value in values), "scored": len(scored),
                    "scored_positive": sum(value > 0 for value in scored_values),
                    "scored_zero": sum(value == 0 for value in scored_values),
                    "scored_negative": sum(value < 0 for value in scored_values),
                    "scored_dates": len({row["session"] for row in scored}),
                    "scored_clusters": len({(row["session"], row["symbol"]) for row in scored}),
                    "target_states": dict(sorted(Counter(row["heads"][head]["target_state"] for row in cell).items()))},
                    "cross_cluster": _pair_metrics(weighted, head), "same_date": _pair_metrics(weighted, head, True),
                    "delete5_sensitivity": _sensitivity(days, scored, head)}
            cells.append({"fold": fold, "phase": phase, "calendar_days": len(days),
                          "support": {"episodes": len(cell), "selected": sum(row["key"] is not None for row in cell),
                                      "joint_known": len(scored), "dates": len({row["session"] for row in cell}),
                                      "clusters": len({(row["session"], row["symbol"]) for row in cell}),
                                      "reasons": dict(sorted(Counter(row["reason"] for row in cell).items()))},
                          "heads": heads})
    return {"schema": "EO-rank-summary-v1", "calendar_days": len(calendar), "cells": cells}
