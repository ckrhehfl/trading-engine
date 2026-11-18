"""EP causal representatives and matched, descriptive value/policy reports.

This module fits nothing and opens no input. The caller seals representatives
before supplying the separately authenticated original evaluation labels.
"""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
import math

from research import intraday_event_evaluation as original
from research import intraday_event_rank as rank


ARMS = ("direct", "factorized")
HEADS = ("net_immediate", "net_wait3")
PHASES = rank.PHASES


def _require(condition, message):
    if not condition:
        raise ValueError("EP report: " + message)


def _exact(value):
    _require(type(value) in (str, int, Fraction), "exact cash text required")
    return Fraction(value)


def _score(value):
    if value is None:
        return None
    _require(type(value) in (int, float, str, Fraction), "finite statistical score required")
    number = float(Fraction(value)) if isinstance(value, str) else float(value)
    _require(math.isfinite(number), "nonfinite score")
    return number


def _calendar(calendar, fold=None):
    days = tuple(calendar)
    _require(len(days) == 257 and days == tuple(sorted(set(days)))
             and all(date.fromisoformat(day).isoformat() == day for day in days), "original calendar required")
    if fold is None:
        return days[97:]
    _require(type(fold) is int and fold in range(4), "original fold required")
    return days[97 + 40 * fold:137 + 40 * fold]


def _maps(rows):
    result = {}
    for row in rows:
        _require(row["key"] not in result, "duplicate forecast identity")
        result[row["key"]] = row
    return result


def select_representatives(calendar, fold, roster, candidates, predictions_by_arm, *, components_by_arm=None):
    days = _calendar(calendar, fold)
    _require(set(predictions_by_arm) == set(ARMS), "both fixed arms required")
    predictions = {arm: _maps(predictions_by_arm[arm]) for arm in ARMS}
    components = {arm: _maps((components_by_arm or {}).get(arm, ())) for arm in ARMS}
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
                part = {} if candidate is None else components[arm].get(candidate.key, {})
                if record:
                    _require(record["session"] == event.session, "prediction date differs")
                if part:
                    _require(part["session"] == event.session, "component date differs")
                values = record.get("predictions", {})
                scores[arm] = {head: _score(values.get(head)) for head in HEADS}
                scores[arm]["fill"] = _score(values.get("fill"))
                scores[arm]["fill_wait3"] = _score(part.get("components", {}).get("fill_wait3"))
                _require(all(value is None or 0 <= value <= 1 for key, value in scores[arm].items()
                             if key.startswith("fill")), "invalid fill probability")
            result.append({"schema": "EP-representative-v1", "event_id": identity, "session": event.session,
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
    wanted = {row["key"]: row for row in representatives if row["key"] is not None}
    _require(len(wanted) == sum(row["key"] is not None for row in representatives), "duplicate representative")
    measured, seen = {}, set()
    for record in target_records:
        key = record["key"]
        _require(key not in seen, "duplicate saved target")
        seen.add(key)
        if key not in wanted:
            continue
        rep, target = wanted[key], record["targets"]
        _require(record["kind"] == "labels" and record["query_kind"] == "entry"
                 and (record["session"], record["symbol"], target["event_id"], target["decision_offset"])
                 == (rep["session"], rep["symbol"], rep["event_id"], rep["decision_offset"])
                 and target["entry_admissibility"] == "admissible"
                 and _exact(target["original_requested_notional"]) == _exact(rep["denominator"]),
                 "saved target differs from causal representative")
        outcomes = {}
        for head, action in zip(HEADS, ("immediate", "wait3")):
            raw = target[action]
            _require(_exact(raw["denominator"]) == _exact(rep["denominator"]), "action denominator differs")
            value, cash, fill = raw["value"], raw["net_cash"], raw["fill"]
            maturity, fill_maturity = raw["matured_offset"], raw["fill_matured_offset"]
            _require(fill is None or type(fill) is int and fill in (0, 1), "invalid fill")
            for clock in (maturity, fill_maturity):
                _require(clock is None or type(clock) is int and rep["decision_offset"] <= clock < rep["session_length"],
                         "action maturity escaped original session")
            _require(fill is None or fill_maturity is not None, "known fill lacks maturity")
            training = record["training_labels"][head]
            _require((value is None) == (training is None), "original training target support differs")
            if value is not None:
                _require(maturity is not None and cash is not None and _exact(value) == _exact(training)
                         and _exact(value) == _exact(cash) / _exact(rep["denominator"]), "exact target cash ratio differs")
                if fill == 0 or raw["status"] in ("no_entry", "no_fill"):
                    _require(_exact(value) == 0, "no-fill value is not zero")
            outcomes[head] = {"target": None if value is None else str(_exact(value)),
                "net_cash": None if cash is None else str(_exact(cash)), "fill": fill,
                "raw_status": raw["status"], "matured_offset": maturity, "fill_matured_offset": fill_maturity}
        measured[key] = outcomes
    _require(set(measured) == set(wanted), "representative target missing")
    return [{**row, "outcomes": measured[row["key"]] if row["key"] is not None else {
        head: {"target": None, "net_cash": None, "fill": None, "raw_status": row["reason"],
               "matured_offset": None, "fill_matured_offset": None} for head in HEADS}} for row in representatives]


def _weighted(rows):
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        groups[row["session"]][row["symbol"]].append(row)
    return [(row, 1 / len(groups) / len(names) / len(events))
            for names in groups.values() for events in names.values() for row in events]


def _paired(row):
    return all(row["outcomes"][head]["target"] is not None
               and all(row["predictions"][arm][head] is not None for arm in ARMS) for head in HEADS)


def _rank_rows(rows, arm):
    return [{**row, "predictions": row["predictions"][arm],
             "heads": {head: {"target": row["outcomes"][head]["target"]} for head in HEADS}}
            for row in rows]


def _calibration(rows, arm, head):
    weights = _weighted(rows)
    pairs = [(row["predictions"][arm][head], float(_exact(row["outcomes"][head]["target"])), w) for row, w in weights]
    if not pairs:
        return dict.fromkeys(("prediction_mean", "target_mean", "signed_error", "mae", "rmse", "zero_mae", "zero_rmse"))
    return {"prediction_mean": math.fsum(p*w for p, y, w in pairs), "target_mean": math.fsum(y*w for p, y, w in pairs),
            "signed_error": math.fsum((p-y)*w for p, y, w in pairs), "mae": math.fsum(abs(p-y)*w for p, y, w in pairs),
            "rmse": math.sqrt(math.fsum((p-y)**2*w for p, y, w in pairs)),
            "zero_mae": math.fsum(abs(y)*w for p, y, w in pairs), "zero_rmse": math.sqrt(math.fsum(y*y*w for p, y, w in pairs))}


def _fill_scores(rows, arm, head):
    field = "fill" if head == "net_immediate" else "fill_wait3"
    scored = [row for row in rows if row["outcomes"][head]["fill"] is not None
              and all(row["predictions"][a][field] is not None for a in ARMS)]
    triples = [(row["predictions"][arm][field], row["outcomes"][head]["fill"], weight) for row, weight in _weighted(scored)]
    return {"rows": len(scored), "brier": math.fsum(w*(p-y)**2 for p, y, w in triples) if triples else None,
            "prediction_mean": math.fsum(w*p for p, y, w in triples) if triples else None,
            "observed_mean": math.fsum(w*y for p, y, w in triples) if triples else None}


def policy_summary(days, reports):
    _require(set(reports) == set(ARMS), "paired policy reports required")
    maps, outputs = {}, {}
    weeks = sorted({date.fromisoformat(day).strftime("%G-W%V") for day in days})
    for arm in ARMS:
        rows = list(reports[arm])
        maps[arm] = {row["event_id"]: row for row in rows}
        _require(len(maps[arm]) == len(rows) and all(row["session"] in days and row["arm"] == arm for row in rows),
                 "policy event roster differs")
        known = [row for row in rows if row["net_cash"] is not None]
        total = sum((_exact(row["net_cash"]) for row in known), Fraction())
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
    _require(maps[ARMS[0]].keys() == maps[ARMS[1]].keys(), "policy populations differ")
    paired = []
    for key, left in maps["direct"].items():
        right = maps["factorized"][key]
        _require(all(left[name] == right[name] for name in ("session", "symbol", "cluster_id", "family")), "policy identity differs")
        if left["net_cash"] is not None and right["net_cash"] is not None:
            paired.append(_exact(right["net_cash"]) - _exact(left["net_cash"]))
    total = sum(paired, Fraction())
    return {"arms": outputs, "paired_known_episodes": len(paired), "paired_unknown_episodes": len(maps["direct"])-len(paired),
            "factorized_minus_direct_cash": str(total), "mean_paired_cash_difference": str(total/len(paired)) if paired else None,
            "claim": "nominal event cash on observed saved targets; not a funded account or filled-notional return"}


def summarize(calendar, fold, representatives, target_records, policy_reports):
    days = _calendar(calendar, fold)
    joined = join_targets(representatives, target_records)
    _require(all(row["fold"] == fold and row["session"] in days for row in joined), "off-fold representative")
    cells = []
    for phase in PHASES:
        rows = [row for row in joined if row["phase"] == phase]
        paired = [row for row in rows if _paired(row)]
        arms = {}
        for arm in ARMS:
            ranking = _rank_rows(paired, arm)
            weighted = rank._weights(ranking)
            arms[arm] = {}
            for head in HEADS:
                arms[arm][head] = {"own_known": sum(row["outcomes"][head]["target"] is not None
                    and row["predictions"][arm][head] is not None for row in rows),
                    "calibration": _calibration(paired, arm, head), "fill": _fill_scores(rows, arm, head),
                    "cross_cluster": rank._pair_metrics(weighted, head),
                    "same_date": rank._pair_metrics(weighted, head, True),
                    "delete5_sensitivity": rank._sensitivity(days, ranking, head)}
        cells.append({"fold": fold, "phase": phase, "episodes": len(rows),
            "selected": sum(row["key"] is not None for row in rows), "paired_known": len(paired),
            "reason_counts": dict(sorted(Counter(row["reason"] for row in rows).items())), "arms": arms})
    return {"schema": "EP-comparison-report-v1", "fold": fold, "calendar": list(days),
            "cells": cells, "policy_rows": {arm: list(policy_reports[arm]) for arm in ARMS},
            "policy": policy_summary(days, policy_reports),
            "claim": "repeated Discovery; expected value and positive-outcome ranking are different estimands"}


def pool(calendar, reports):
    days = _calendar(calendar)
    reports = tuple(reports)
    _require(len(reports) == 4 and [report["fold"] for report in reports] == list(range(4)), "all four ordered reports required")
    _require(all(report["calendar"] == list(_calendar(calendar, i)) for i, report in enumerate(reports)), "report calendar differs")
    rows = {arm: [row for report in reports for row in report["policy_rows"][arm]] for arm in ARMS}
    return {"schema": "EP-four-fold-report-v1", "calendar_days": len(days),
            "cells": [cell for report in reports for cell in report["cells"]], "policy": policy_summary(days, rows),
            "claim": "all sixteen cells retained; no pooled best-cell AUC or promotion"}
