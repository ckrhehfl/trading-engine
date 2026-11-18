"""Independent witnesses for ES training targets and same-day ranking."""

from collections import Counter, defaultdict
from dataclasses import replace
from fractions import Fraction
from itertools import product

from research import intraday_event_calibration_reference as er
from research import intraday_event_factorized_reference as ep
from research import intraday_event_mean_reference as eq
from research import intraday_event_model_inputs as inputs
from research.intraday_event_models import HEADS, HELD_STATES


ARMS = ("rate", "direct", "success")
PHASES, VALUES = eq.PHASES, eq.VALUES
ER_ARMS = dict(zip(er.ARMS, ARMS))


def _check(condition, message):
    if not condition:
        raise ValueError("ES independent: " + message)


def _training_actions(query, record):
    target = record["targets"]
    if query.state in HELD_STATES:
        _check(record["query_kind"] == "held" and target["schema"] == "EL-held-remaining-gross-v1",
               "original held target differs")
        return dict.fromkeys(VALUES), (None, None)
    _check(record["query_kind"] == "entry" and target["schema"] == "EL-separated-offline-targets-v1"
           and (target["event_id"], target["decision_offset"]) == (query.episode_id, query.decision_offset),
           "original action target identity differs")
    admission = target["entry_admissibility"]
    _check(admission in ("admissible", "not_admissible", "unknown"), "original entry admission differs")
    denominator = target["original_requested_notional"]
    denominator = None if denominator is None else ep._exact(denominator)
    values, fills = {}, []
    for head in VALUES:
        saved = target[head[4:]]
        fill, fill_clock, cash_clock = saved["fill"], saved["fill_matured_offset"], saved["matured_offset"]
        value = None if saved["value"] is None else ep._exact(saved["value"])
        cash = None if saved["net_cash"] is None else ep._exact(saved["net_cash"])
        action_denominator = saved["denominator"]
        _check((None if action_denominator is None else ep._exact(action_denominator)) == denominator
               and (denominator is None or denominator >= 0), "original action denominator differs")
        _check(fill is None or type(fill) is int and fill in (0, 1), "original action fill differs")
        _check(all(clock is None or type(clock) is int and clock >= query.decision_offset
                   for clock in (fill_clock, cash_clock)), "original action maturity precedes its decision")
        if admission == "admissible":
            _check(fill is None or fill_clock is not None, "known action fill missing maturity")
            _check((value is None and cash is None) or cash_clock is not None, "known action cash missing maturity")
        if denominator is not None and denominator > 0:
            _check(value == (None if cash is None else cash / denominator), "original exact cash ratio differs")
        _check(fill != 0 or cash in (None, 0) and value in (None, 0), "original no-fill cash differs")
        values[head] = str(value) if admission == "admissible" and value is not None else None
        eligible = admission == "admissible" and denominator is not None and denominator > 0
        fills.append(fill if eligible and fill_clock is not None else None)
    return values, tuple(fills)


def verify_training_inputs(shards, data, phases, exact_targets):
    """Re-read the bounded training prefix for exact values and both action fills."""
    shards = tuple(shards)
    eq.verify_training_phases(shards, data, phases)
    base = data.base
    stop = 97 + 40 * base.fold
    cutoff = ep._days(base.calendar, base.fold)[0]
    wanted = {query.key: query for query in base.training}
    fills = {row.key: row for row in data.action_targets}
    _check(len(wanted) == len(base.training) == len(fills) == len(data.action_targets)
           and set(wanted) == set(fills) == set(exact_targets), "training input roster differs")
    seen = set()
    for slot, manifest in shards:
        if manifest["session_indices"][0] >= stop:
            break
        for record in inputs.training_labels(slot, manifest, cutoff):
            _check(record["session"] < cutoff, "evaluation label reached training prefix")
            key = record["key"]
            if key not in wanted:
                continue
            query, action = wanted[key], fills[key]
            _check(key not in seen and record["kind"] == "labels"
                   and (record["session"], record["symbol"]) == (query.session, query.symbol),
                   "original training label identity differs")
            saved = record["training_labels"]
            _check(saved is not None and saved["key"] == key, "missing original training label")
            expected_maturity = query.session if any(saved[head] is not None for head in HEADS) else None
            _check(saved["matured_session"] == expected_maturity, "original training label maturity differs")
            values, known_fills = _training_actions(query, record)
            ep._same(exact_targets[key], values, "exact training values")
            ep._same({head: None if saved[head] is None else str(ep._exact(saved[head])) for head in VALUES},
                     values, "original training label values")
            ep._same((action.immediate, action.wait3), known_fills, "original training action fills")
            ep._same((action.immediate_matured_session, action.wait3_matured_session),
                     tuple(None if fill is None else query.session for fill in known_fills), "training fill sessions")
            seen.add(key)
    _check(seen == set(wanted), "incomplete original training labels")
    return {"verified": True, "rows": len(seen)}


def verify_training(data, phases, exact_targets, adapted, rate_artifact):
    """Verify the response-only substitution and independently estimate the historical rate."""
    base = data.base
    _check(base.scope == "common_chart_peer" and set(phases) == {row.key for row in base.training},
           "training phase roster/scope differs")
    _check(all(phases[row.key] is None or (phases[row.key] in PHASES
               and row.family == eq.FAMILY and row.state not in HELD_STATES) for row in base.training),
           "unknown or inapplicable training phase")
    _check(all(set(values) == set(VALUES) for values in exact_targets.values()), "exact training heads differ")
    joint, weights = eq._joint(data, exact_targets)
    fills = {row.key: row for row in data.action_targets}
    indicators = {row.key: dict.fromkeys(VALUES) for row in base.training}
    for row in joint:
        for head in VALUES:
            indicators[row.key][head] = int(getattr(fills[row.key], head[4:]) == 1
                                            and ep._exact(exact_targets[row.key][head]) > 0)
    _check(type(adapted) is type(data) and type(adapted.base) is type(base)
           and replace(adapted.base, targets=base.targets) == base and adapted.action_targets == data.action_targets,
           "adaptation changed original inputs or action fills")
    _check(len(adapted.base.targets) == len(base.targets) == len(indicators), "adapted target roster differs")
    for original, changed in zip(base.targets, adapted.base.targets):
        _check(type(changed) is type(original) and all(
            type(getattr(changed, field)) is type(getattr(original, field))
            and getattr(changed, field) == getattr(original, field)
            for field in ("key", "path_class", "fill", "remaining_gross", "matured_session")),
            "adaptation changed original auxiliary target or maturity")
        ep._same({head: getattr(changed, head) for head in VALUES}, indicators[original.key], "success responses")

    cells = {}
    for phase in PHASES:
        selected = [row for row in joint if phases[row.key] == phase]
        mass = sum((weights[row.key] for row in selected), Fraction())
        support = eq._counts(selected)
        rates = dict.fromkeys(VALUES)
        if mass:
            for head in VALUES:
                numerator = sum((weights[row.key] for row in selected if indicators[row.key][head]), Fraction())
                rates[head] = str(numerator / mass)
        cells[phase] = {**support, "weight_mass": str(mass), "rates": rates,
                        "status": "estimated" if mass else "unsupported_empty_group",
                        "precision": "unknown_small_support" if min(support[k] for k in ("rows", "dates", "clusters")) < 2
                        else "not_established"}
    expected = {"schema": "ES-phase-success-v1", "fold": base.fold, "scope": base.scope,
        "training_sessions": list(base.calendar[:97 + 40 * base.fold]),
        "evaluation_sessions": list(ep._days(base.calendar, base.fold)), "support": base.support,
        "training_targets_sha256": ep._hash(exact_targets), "training_phases_sha256": ep._hash(phases),
        "training_success_sha256": ep._hash(indicators), "weighting": "full_joint_date_cluster_episode_frame_then_condition",
        "original_q_support": _original_support(exact_targets),
        "joint": {**eq._counts(joint), "weight_sum": str(sum(weights.values(), Fraction()))}, "cells": cells}
    ep._same(rate_artifact, expected, "historical success rates")
    return {"verified": True, "joint_rows": len(joint), "cells": len(cells)}


def _original_support(exact_targets):
    availability = Counter(tuple(head for head in VALUES if row[head] is not None) for row in exact_targets.values())
    return {"original_known_rows": {head: sum(count for known, count in availability.items() if head in known)
                                    for head in VALUES},
            "paired_known_rows": availability[VALUES],
            "dropped_unpaired_rows": {head: availability[head,] for head in VALUES},
            "policy": "both Q heads use identical joint-known rows; no change to labels or inference population"}


def verify_projection(data, roster, rate_artifact, predictions):
    """Check causal phase application over the original evaluation query roster."""
    by_key = {candidate.key: candidate for candidate in roster.candidates}
    _check(len(by_key) == len(roster.candidates), "duplicate evaluation candidate")
    expected = []
    for query in data.base.evaluation:
        scores = dict.fromkeys(HEADS)
        statuses = dict.fromkeys(HEADS, "not_estimated_by_baseline")
        phase = None
        if query.family == eq.FAMILY and query.state not in HELD_STATES:
            candidate = by_key.get(query.key)
            _check(candidate is not None and candidate.query == query, "evaluation query lost causal candidate")
            phase = eq._phase(query, candidate.episode.onset_decision_offset)
        for head in VALUES:
            scores[head] = None if phase is None else rate_artifact["cells"][phase]["rates"][head]
            statuses[head] = ("not_applicable_to_baseline" if phase is None else "unsupported_empty_group"
                              if scores[head] is None else "estimated_training_phase_success_rate")
        expected.append({"key": query.key, "session": query.session, "query_status": "supported_features",
                         "predictions": scores, "head_status": statuses})
    ep._same(predictions, expected, "historical success projection")
    return {"verified": True, "predictions": len(expected)}


def verify_representatives(calendar, fold, episodes, candidates, predictions_by_arm, saved_reps):
    """Reuse the independent ER causal selector on the same representative contract."""
    _check(set(predictions_by_arm) == set(ARMS), "all three score arms required")
    forecasts = {arm: ep._map(predictions_by_arm[arm]) for arm in ARMS}
    translated = []
    for row in saved_reps:
        _check(row["schema"] == "ES-representative-v1" and set(row["predictions"]) == set(ARMS),
               "representative schema or score arms differ")
        for arm in ARMS:
            source = forecasts[arm].get(row["key"], {}) if row["key"] is not None else {}
            for head in VALUES:
                score = row["predictions"][arm][head]
                expected = ep._number(source.get("predictions", {}).get(head))
                _check(type(score) is type(expected) and score == expected, "representative ranking score differs")
        translated.append({**row, "schema": "ER-representative-v1", "predictions": {
            old: row["predictions"][arm] for old, arm in ER_ARMS.items()}})
    return er.verify_representatives(calendar, fold, episodes, candidates,
        {old: predictions_by_arm[arm] for old, arm in ER_ARMS.items()}, translated)


def _outcome(target):
    fill, cash = target["fill"], target["net_cash"]
    if fill is None:
        return "unknown_fill", None
    if cash is None:
        return "unknown_cash", None
    cash = ep._exact(cash)
    if fill == 0:
        _check(cash == 0, "known no-fill has nonzero cash")
        return "not_filled", 0
    if cash == 0:
        return "break_even", 0
    return ("success", 1) if cash > 0 else ("loss", 0)


def _join(reps, records):
    for row in reps:
        if row["key"] is None:
            yield row, None
            continue
        _check(row["key"] in records, "missing representative target")
        record = records[row["key"]]
        denominator = ep._exact(row["denominator"])
        _check(denominator > 0, "representative denominator must be positive")
        ep._target_identity(record, key=row["key"], session=row["session"], symbol=row["symbol"],
                            event_id=row["event_id"], decision=row["decision_offset"], denominator=denominator)
        yield row, {head: _outcome(ep._action(record, head[4:], denominator, row["decision_offset"],
                                             row["session_length"])) for head in VALUES}


def _daily(day, rows, head):
    # Within a date, all pairs share a factor of 1 / cluster_count**2.
    # Cancel it before computing the exact ratio between pair mass and credit.
    clusters = defaultdict(lambda: ([], []))
    for row, outcomes in rows:
        scores = tuple(row["predictions"][arm][head] for arm in ARMS)
        clusters[row["cluster_id"]][outcomes[head][1]].append(scores)
    pair_count, mass = 0, Fraction()
    credit = dict.fromkeys(ARMS, Fraction())
    for (left, left_rows), (right, right_rows) in product(clusters.items(), repeat=2):
        winners, losers = left_rows[1], right_rows[0]
        if left == right or not winners or not losers:
            continue
        weight = Fraction(1, sum(map(len, left_rows)) * sum(map(len, right_rows)))
        count = len(winners) * len(losers)
        pair_count += count
        mass += weight * count
        for index, arm in enumerate(ARMS):
            twice_credit = sum(2 if success[index] > failure[index] else success[index] == failure[index]
                               for success, failure in product(winners, losers))
            credit[arm] += weight * Fraction(twice_credit, 2)
    positive = sum(len(parts[1]) for parts in clusters.values())
    return {"session": day, "rows": len(rows), "positive": positive, "negative": len(rows) - positive,
            "clusters": len(clusters), "pairs": pair_count,
            "auc": {arm: str(credit[arm] / mass) if mass else None for arm in ARMS}}


def _mean(values):
    return sum(values, Fraction()) / len(values) if values else None


def _wire(value):
    return None if value is None else str(value)


def _day_estimates(day_rows, arm):
    return [None if row["auc"][arm] is None else ep._exact(row["auc"][arm]) for row in day_rows]


def _deletions(values):
    retained = []
    for offset in range(len(values) - 4):
        remaining = [value for value in values[:offset] + values[offset + 5:] if value is not None]
        if remaining:
            retained.append(_mean(remaining))
    return {"min": _wire(min(retained, default=None)), "max": _wire(max(retained, default=None)),
            "defined": len(retained)}


def verify_report(calendar, fold, reps, target_records, saved_report):
    """Rebuild outcome categories, matched pairs and date sensitivity independently."""
    days = ep._days(calendar, fold)
    identities = {(row["event_id"], row["phase"]) for row in reps}
    events = {row["event_id"] for row in reps}
    _check(len(identities) == len(reps) and identities == {(event, phase) for event in events for phase in PHASES},
           "all four original episode phases required")
    _check(all(row["schema"] == "ES-representative-v1" and row["fold"] == fold and row["session"] in days
               for row in reps), "off-fold or foreign representative")
    selected = [row["key"] for row in reps if row["key"] is not None]
    wanted = set(selected)
    _check(len(wanted) == len(selected), "duplicate representative target key")
    for row in reps:
        _check(set(row["predictions"]) == set(ARMS), "representative score arms differ")
        for arm, scores in row["predictions"].items():
            _check(set(scores) == set(VALUES), "representative score heads differ")
            for value in scores.values():
                _check(value is None or type(value) is float and ep._number(value) == value
                       and (arm != "rate" or 0 <= value <= 1),
                       "invalid representative ranking score")
    records, seen = {}, set()
    for record in target_records:
        key = record["key"]
        _check(key not in seen, "duplicate evaluation target")
        seen.add(key)
        if key in wanted:
            records[key] = record
    joined = list(_join(reps, records))
    cells = []
    for phase in PHASES:
        rows = [item for item in joined if item[0]["phase"] == phase]
        chosen = [(row, target) for row, target in rows if target is not None]
        matched = defaultdict(list)
        for row, target in chosen:
            if all(target[head][1] is not None for head in VALUES) and all(
                    row["predictions"][arm][head] is not None for arm in ARMS for head in VALUES):
                matched[row["session"]].append((row, target))
        paired = sum(map(len, matched.values()))
        actions = {}
        for head in VALUES:
            day_rows = [_daily(day, matched.get(day, ()), head) for day in days]
            scores = {arm: _day_estimates(day_rows, arm) for arm in ARMS}
            actions[head] = {"outcomes": dict(Counter(target[head][0] for _, target in chosen)),
                "own_known": {arm: sum(target[head][1] is not None and row["predictions"][arm][head] is not None
                                       for row, target in chosen) for arm in ARMS},
                "paired_positive": sum(row["positive"] for row in day_rows),
                "paired_negative": sum(row["negative"] for row in day_rows), "days": day_rows,
                "eligible_days": sum(row["pairs"] > 0 for row in day_rows),
                "auc": {arm: _wire(_mean([value for value in scores[arm] if value is not None])) for arm in ARMS},
                "delete5": {arm: _deletions(scores[arm]) for arm in ARMS}}
        cells.append({"fold": fold, "phase": phase, "episodes": len(rows), "selected": len(chosen), "paired_known": paired,
                      "reasons": dict(Counter(row["reason"] for row, _ in rows)), "actions": actions})
    expected = {"schema": "ES-report-v1", "fold": fold, "calendar": list(days), "cells": cells}
    ep._same(saved_report, expected, "same-day ranking report")
    return {"verified": True, "cells": len(cells)}


def verify_pool(calendar, reports, saved):
    """Average audited phases inside each date before weighting dates equally."""
    days, reports = ep._days(calendar), tuple(reports)
    _check(len(reports) == 4, "four ordered reports required")
    by_action = {head: defaultdict(list) for head in VALUES}
    cells = []
    for fold, report in enumerate(reports):
        fold_days = list(ep._days(calendar, fold))
        _check(report["schema"] == "ES-report-v1" and report["fold"] == fold and report["calendar"] == fold_days
               and [(cell["fold"], cell["phase"]) for cell in report["cells"]] == [(fold, phase) for phase in PHASES],
               "pooled calendar or cell roster differs")
        cells.extend(report["cells"])
        for cell in report["cells"]:
            for head in VALUES:
                day_rows = cell["actions"][head]["days"]
                _check([row["session"] for row in day_rows] == fold_days, "pooled action dates differ")
                for row in day_rows:
                    auc = row["auc"]
                    _check(set(auc) == set(ARMS) and all((auc[arm] is not None) == (row["pairs"] > 0) for arm in ARMS),
                           "pooled outcome score support differs")
                    if row["pairs"]:
                        by_action[head][row["session"]].append({arm: ep._exact(auc[arm]) for arm in ARMS})
    actions = {}
    for head, by_date in by_action.items():
        actions[head] = {"eligible_days": len(by_date), "auc": {arm: _wire(_mean([
            _mean([phase[arm] for phase in by_date[day]]) for day in days if day in by_date])) for arm in ARMS}}
    expected = {"schema": "ES-summary-v1", "calendar_days": len(days), "cells": cells, "actions": actions}
    ep._same(saved, expected, "four-fold same-day ranking summary")
    return {"verified": True, "cells": len(cells)}
