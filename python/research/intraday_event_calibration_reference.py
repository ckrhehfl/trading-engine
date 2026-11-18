"""Independent ER witnesses over authenticated training and stored outcomes.

The producer's calibration, projection, policy and report functions are never invoked.
Only the established bounded stream readers and independent EP/EO witnesses are
shared. There is no native model fitting or inference in this module.
"""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from hashlib import sha256
import math

from research import intraday_event_factorized_reference as prior
from research import intraday_event_model_inputs as inputs
from research import intraday_event_rank_reference as rank_reference
from research.intraday_event_models import HEADS, HELD_STATES


ARMS = ("mean", "direct", "calibrated")
VALUES = ("net_immediate", "net_wait3")
PHASES = ("onset", "waiting_pullback", "pullback", "confirmation")
FAMILY = "C_FIRSTPULLBACK"
COMPARISONS = (("direct", "mean"), ("calibrated", "mean"), ("calibrated", "direct"))


def _check(condition, message):
    if not condition:
        raise ValueError("ER independent: " + message)


def _phase(query, onset):
    if query.family != FAMILY or query.state in HELD_STATES:
        return None
    _check(type(onset) is int and 0 <= onset <= query.decision_offset, "original onset clock differs")
    return "onset" if query.decision_offset == onset else {
        "WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback", "CONFIRMED": "confirmation"
    }.get(query.state)


def _training_phases(shards, data):
    base = data.base
    cutoff = prior._days(base.calendar, base.fold)[0]
    onsets = {}
    for slot, manifest in shards:
        if manifest["session_indices"][0] >= 97 + 40 * base.fold:
            break
        for row in inputs._rows(slot, manifest, "event-states.jsonl", "event_state"):
            frame = row["frame"]
            if row["session"] >= cutoff or frame["kind"] != "at_onset":
                continue
            identity = frame["event_id"]
            _check(identity not in onsets, "duplicate original training onset")
            onsets[identity] = (row["session"], row["symbol"], frame["overlap_group_id"],
                                frame["family"], frame["decision_offset"])
    phases = {}
    for query in base.training:
        _check(query.session < cutoff and query.key not in phases, "training query boundary or identity differs")
        if query.state in HELD_STATES:
            phases[query.key] = None
            continue
        onset = onsets.get(query.episode_id)
        _check(onset is not None and onset[:4] == (query.session, query.symbol, query.cluster_id, query.family)
               and type(onset[4]) is int and 0 <= onset[4] <= query.decision_offset, "training original onset differs")
        phases[query.key] = _phase(query, onset[4])
    return phases


def verify_training_phases(shards, data, phases):
    expected = _training_phases(shards, data)
    prior._same(phases, expected, "training phases")
    return {"verified": True, "rows": len(expected)}


def verify_training_inputs(shards, data, phases, exact_targets):
    """Re-read only causal training prefixes; never decode evaluation labels."""
    verify_training_phases(shards, data, phases)
    base = data.base
    cutoff = prior._days(base.calendar, base.fold)[0]
    wanted = {query.key: query for query in base.training}
    expected = {}
    for slot, manifest in shards:
        if manifest["session_indices"][0] >= 97 + 40 * base.fold:
            break
        for record in inputs.training_labels(slot, manifest, cutoff):
            _check(record["session"] < cutoff, "evaluation target reached training prefix")
            key = record["key"]
            if key not in wanted:
                continue
            query = wanted[key]
            _check(key not in expected and (record["session"], record["symbol"]) == (query.session, query.symbol)
                   and record["session"] < cutoff, "training exact-target identity differs")
            labels = record["training_labels"]
            _check(labels is not None and labels["key"] == key and
                   (labels["matured_session"] is None or labels["matured_session"] < cutoff),
                   "training label maturity differs")
            expected[key] = {head: None if labels[head] is None else str(prior._exact(labels[head])) for head in VALUES}
    _check(set(expected) == set(wanted), "training exact-target roster differs")
    prior._same(exact_targets, expected, "exact training targets")
    return {"verified": True, "rows": len(expected)}


def _counts(rows):
    return {"rows": len(rows), "dates": len({row.session for row in rows}),
            "clusters": len({row.cluster_id for row in rows}),
            "episodes": len({row.original_episode_id or row.episode_id for row in rows})}


def _joint(data, exact_targets):
    base = data.base
    cutoff = prior._days(base.calendar, base.fold)[0]
    labels = {label.key: label for label in base.targets}
    fills = {row.key: row for row in data.action_targets}
    keys = {row.key for row in base.training}
    _check(len(keys) == len(base.training) == len(base.targets) == len(data.action_targets)
           and set(labels) == set(fills) == set(exact_targets) == keys,
           "complete original training roster required")
    rows = []
    for query in sorted(base.training, key=lambda row: row.key):
        label, fill = labels[query.key], fills[query.key]
        _check(set(exact_targets[query.key]) == set(VALUES), "exact target heads differ")
        _check(query.session < cutoff and (label.matured_session is None or query.session <= label.matured_session < cutoff),
               "training maturity boundary differs")
        for head in VALUES:
            value = exact_targets[query.key][head]
            observed = getattr(label, head)
            _check((value is None) == (observed is None) and
                   (value is None or prior._number(value) == prior._number(observed)), "decoded training value differs")
            _check(value is None or type(value) is str and str(prior._exact(value)) == value,
                   "exact canonical training scalar required")
        for action in ("immediate", "wait3"):
            value, maturity = getattr(fill, action), getattr(fill, action + "_matured_session")
            _check(value is None or type(value) is int and value in (0, 1), "invalid training action fill")
            _check(value is None or maturity is not None and query.session <= maturity < cutoff,
                   "training action fill not mature")
            _check(value != 0 or exact_targets[query.key]["net_" + action] is None
                   or prior._exact(exact_targets[query.key]["net_" + action]) == 0, "no-fill value differs")
            _check(query.state not in HELD_STATES or value is None, "held row supplied entry action fill")
        _check(label.fill is None or fill.immediate is None or label.fill == fill.immediate,
               "immediate action fill differs from original label")
        if (query.state not in HELD_STATES and all(exact_targets[query.key][head] is not None for head in VALUES)
                and fill.immediate is not None and fill.wait3 is not None):
            rows.append(query)
    dates, clusters, episodes = defaultdict(set), defaultdict(set), Counter()
    for row in rows:
        episode = row.original_episode_id or row.episode_id
        dates[row.session].add(row.cluster_id)
        clusters[row.session, row.cluster_id].add(episode)
        episodes[row.session, row.cluster_id, episode] += 1
    weights = {row.key: Fraction(1, len(dates) * len(dates[row.session]) *
               len(clusters[row.session, row.cluster_id]) *
               episodes[row.session, row.cluster_id, row.original_episode_id or row.episode_id]) for row in rows}
    return rows, weights


def _decimal(value):
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def _score_exact(value):
    if value is None:
        return None
    _check(type(value) in (int, float, str), "saved finite decimal score required")
    parsed = Decimal(str(value))
    _check(parsed.is_finite() and math.isfinite(float(parsed)), "nonfinite saved score")
    return Fraction(parsed)


def _coefficient(value):
    return None if value is None else [format(value.numerator, "x"), format(value.denominator, "x")]


def _read_coefficient(value):
    _check(type(value) is list and len(value) == 2 and all(type(item) is str for item in value),
           "exact mapping coefficient required")
    numerator, denominator = (int(item, 16) for item in value)
    _check(denominator > 0, "positive coefficient denominator required")
    exact = Fraction(numerator, denominator)
    _check(_coefficient(exact) == value, "canonical exact coefficient required")
    return exact


def verify_calibration(data, phases, exact_targets, oof_predictions, artifact):
    """Reconstruct phase moments via raw moments, without producer fitting code.

    Full-J weights precede the C/OOF intersection. Centered covariance and
    variance use the algebraically equivalent raw-moment identity, independently
    of the producer's row-centered arithmetic. Only final statistics are rounded.
    """
    base = data.base
    stop = 97 + 40 * base.fold
    _check(base.scope == "common_chart_peer" and set(phases) == {row.key for row in base.training},
           "training phase roster/scope differs")
    _check(all(phase is None or phase in PHASES for phase in phases.values()), "unknown phase")
    _check(all(phases[row.key] is None or row.family == FAMILY and row.state not in HELD_STATES
               for row in base.training), "non-C or held phase supplied")
    rows, weights = _joint(data, exact_targets)
    expected_queries = {row.key: row for row in base.training if base.calendar[49] <= row.session < base.calendar[stop]}
    forecasts = prior._map(oof_predictions)
    _check(forecasts.keys() == expected_queries.keys(), "complete chronological OOF query roster required")
    scores = {}
    for key, record in forecasts.items():
        _check(record["session"] == expected_queries[key].session, "OOF session differs")
        _check(record["query_status"] == "supported_features"
               and set(record["predictions"]) == set(record["head_status"]) == set(HEADS), "OOF schema differs")
        scores[key] = {head: _score_exact(record["predictions"][head]) for head in VALUES}
    supported = [row for row in rows if row.family == FAMILY and phases[row.key] is not None
                 and row.key in scores and all(scores[row.key][head] is not None for head in VALUES)]
    totals = {head: [Fraction(), Fraction()] for head in VALUES}
    cells, centers = {}, {}
    for phase in PHASES:
        selected = [row for row in supported if phases[row.key] == phase]
        mass = sum((weights[row.key] for row in selected), Fraction())
        means_x, means_y = dict.fromkeys(VALUES), dict.fromkeys(VALUES)
        centers[phase] = dict.fromkeys(VALUES)
        for head in VALUES:
            if not mass:
                continue
            sx = sum((weights[row.key] * scores[row.key][head] for row in selected), Fraction())
            sy = sum((weights[row.key] * prior._exact(exact_targets[row.key][head]) for row in selected), Fraction())
            sxx = sum((weights[row.key] * scores[row.key][head] ** 2 for row in selected), Fraction())
            sxy = sum((weights[row.key] * scores[row.key][head] * prior._exact(exact_targets[row.key][head])
                       for row in selected), Fraction())
            totals[head][0] += sxy - sx * sy / mass
            totals[head][1] += sxx - sx * sx / mass
            means_x[head], means_y[head] = _decimal(sx / mass), _decimal(sy / mass)
            centers[phase][head] = sx / mass, sy / mass
        counts = _counts(selected)
        cells[phase] = {**counts, "weight_mass": str(mass),
            "status": "estimated" if mass else "unsupported_empty_group",
            "precision": "unknown_small_support" if any(counts[key] < 2 for key in ("rows", "dates", "clusters"))
                         else "not_established", "scores": means_x, "targets": means_y}
    slopes = {}
    for head, (numerator, denominator) in totals.items():
        _check(denominator >= 0, "negative reconstructed variance")
        beta = max(Fraction(), numerator / denominator) if denominator else Fraction()
        status = ("unsupported_empty_support" if not supported else "nonidentified_zero_variance" if not denominator
                  else "constrained_zero" if numerator <= 0 else "fitted_nonnegative")
        slopes[head] = {"numerator": _decimal(numerator), "denominator": _decimal(denominator),
                        "beta": _decimal(beta) if supported else None, "status": status,
                        "exact_beta": _coefficient(beta) if supported else None}
        for phase in PHASES:
            center = centers[phase][head]
            cells[phase].setdefault("exact_intercepts", {})[head] = (
                None if center is None else _coefficient(center[1] - beta * center[0]))
    expected = {"schema": "ER-phase-calibration-v1", "fold": base.fold, "scope": base.scope,
        "training_sessions": list(base.calendar[:stop]), "evaluation_sessions": list(prior._days(base.calendar, base.fold)),
        "support": base.support, "training_targets_sha256": prior._hash(exact_targets),
        "training_phases_sha256": prior._hash(phases), "oof_predictions_sha256": prior._hash(oof_predictions),
        "weighting": "full_joint_date_cluster_episode_frame_then_condition",
        "arithmetic_precision": 50, "rounding": ROUND_HALF_EVEN,
        "joint": {**_counts(rows), "weight_sum": str(sum(weights.values(), Fraction()))},
        "oof": {**_counts(supported), "start_index": 49, "expected_rows": len(expected_queries),
                "paired_finite_rows": sum(all(values[head] is not None for head in VALUES) for values in scores.values())},
        "cells": cells, "slopes": slopes}
    prior._same(artifact, expected, "calibration")
    return {"verified": True, "joint_rows": len(rows), "oof_rows": len(supported), "cells": len(cells)}


def verify_predictions(data, roster, artifact, direct_predictions, calibrated_predictions):
    """Rebuild the frozen affine mapping, retaining all original auxiliary heads."""
    base = data.base
    _check(artifact["fold"] == base.fold and artifact["scope"] == base.scope
           and artifact["support"] == base.support
           and artifact["training_sessions"] == list(base.calendar[:97 + 40 * base.fold])
           and artifact["evaluation_sessions"] == list(prior._days(base.calendar, base.fold)),
           "projection training/evaluation boundary differs")
    _check([(row["key"], row["session"]) for row in direct_predictions]
           == [(row.key, row.session) for row in base.evaluation], "complete original direct forecasts required")
    candidates = {row.key: row for row in roster.candidates}
    _check(len(candidates) == len(roster.candidates) and all(event.family == FAMILY for event in roster.episodes),
           "original C candidate roster differs")
    expected = []
    for query, source in zip(base.evaluation, direct_predictions):
        _check(set(source["predictions"]) == set(source["head_status"]) == set(HEADS)
               and source["query_status"] == "supported_features", "direct forecast schema differs")
        phase = None
        if query.family == FAMILY and query.state not in HELD_STATES:
            candidate = candidates.get(query.key)
            _check(candidate is not None and candidate.query == query, "original C query candidate missing")
            phase = _phase(query, candidate.episode.onset_decision_offset)
        values, statuses = dict(source["predictions"]), dict(source["head_status"])
        for head in VALUES:
            score = _score_exact(source["predictions"][head])
            if phase is None:
                value, status = None, "not_applicable_to_calibration"
            elif score is None:
                value, status = None, "unsupported_original_prediction"
            elif artifact["cells"][phase]["scores"][head] is None or artifact["slopes"][head]["beta"] is None:
                value, status = None, "unsupported_training_calibration"
            else:
                cell = artifact["cells"][phase]
                beta = _read_coefficient(artifact["slopes"][head]["exact_beta"])
                _check(beta >= 0, "negative mapping slope")
                intercept = _read_coefficient(cell["exact_intercepts"][head])
                value, status = _decimal(intercept + beta * score), "estimated_training_only_calibration"
            values[head], statuses[head] = value, status
        expected.append({**source, "predictions": values, "head_status": statuses})
    prior._same(calibrated_predictions, expected, "calibrated predictions")
    return {"verified": True, "predictions": len(expected)}


verify_projection = verify_predictions


def verify_representatives(calendar, fold, episodes, candidates, predictions_by_arm, saved_reps, *, components_by_arm=None):
    """Rebuild target-free earliest representatives without score-based choice."""
    days = prior._days(calendar, fold)
    _check(set(predictions_by_arm) == set(ARMS), "all three forecast arms required")
    forecasts = {arm: prior._map(predictions_by_arm[arm]) for arm in ARMS}
    roster = {event.event_id: event for event in episodes}
    _check(len(roster) == len(episodes) and all(e.session in days and e.family == FAMILY for e in episodes),
           "C episode roster differs")
    grouped, seen = defaultdict(list), set()
    for candidate in candidates:
        event, query = candidate.episode, candidate.query
        _check(roster.get(event.event_id) == event and candidate.key not in seen, "candidate identity differs")
        seen.add(candidate.key)
        phase = "onset" if candidate.frame.decision_offset == event.onset_decision_offset else {
            "WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback", "CONFIRMED": "confirmation"
        }.get(candidate.frame.state)
        if phase is None:
            continue
        denominator, reason = prior._denominator(candidate), prior._admission(candidate)
        if reason is None:
            if denominator is None or denominator <= 0:
                reason = "nonpositive_or_unknown_denominator"
            elif query is None:
                reason = "unknown_chart"
            elif query.peer is None or None in query.peer:
                reason = "unknown_peer"
        grouped[event.event_id, phase].append((candidate, reason, denominator))
    expected = []
    for identity, event in sorted(roster.items()):
        for phase in PHASES:
            choices = grouped[identity, phase]
            eligible = sorted((row for row in choices if row[1] is None), key=lambda row: row[0].frame.decision_offset)
            chosen, denominator = (eligible[0][0], eligible[0][2]) if eligible else (None, None)
            scores = {}
            for arm in ARMS:
                record = {} if chosen is None else forecasts[arm].get(chosen.key, {})
                _check(not record or record["session"] == event.session, "forecast date differs")
                scores[arm] = {head: prior._number(record.get("predictions", {}).get(head)) for head in VALUES}
            expected.append({"schema": "ER-representative-v1", "event_id": identity, "session": event.session,
                "symbol": event.symbol, "cluster_id": event.cluster_id, "fold": fold, "phase": phase,
                "key": None if chosen is None else chosen.key,
                "decision_offset": None if chosen is None else chosen.frame.decision_offset,
                "session_length": None if chosen is None else chosen.label_query.session_length,
                "denominator": None if denominator is None else str(denominator), "predictions": scores,
                "reason": "first_eligible_causal" if eligible else "no_eligible_causal" if choices else "no_causal_phase",
                "support": {"causal_rows": len(choices), "eligible_rows": len(eligible),
                    "reasons": dict(sorted(Counter(row[1] or "eligible" for row in choices).items()))}})
    prior._same(saved_reps, expected, "representatives")
    return {"verified": True, "representatives": len(expected)}


def verify_policy(episodes, candidates, predictions, serialized_policy):
    """Reconstruct the unchanged first-positive policy and unknown earlier frames."""
    saved = serialized_policy
    _check(saved["control"] == "model" and saved["scope"] == "common_chart_peer" and saved["model"] in ("EQ-mean", "ER-calibrated"),
           "unregistered calibrated/mean policy")
    _check(saved["prediction_binding"] == prior._hash(predictions), "prediction binding differs")
    grouped, keys = defaultdict(list), set()
    roster = {event.event_id: event for event in episodes}
    _check(len(roster) == len(episodes), "duplicate policy episode")
    for candidate in candidates:
        _check(roster.get(candidate.episode.event_id) == candidate.episode and candidate.key not in keys,
               "policy candidate identity differs")
        keys.add(candidate.key)
        grouped[candidate.episode.event_id].append(candidate)
    forecasts, digest = prior._map(predictions), sha256()
    ordered = sorted(episodes, key=lambda event: (event.session, event.symbol, event.family, event.event_id))
    for item in (*ordered, *sorted(candidates, key=lambda candidate: candidate.key)):
        digest.update(prior._encode(item) + b"\n")
    choices, trace = [], []
    for episode in ordered:
        chosen, action, reason, denominator = None, "no_commit", "no_causal_frame", None
        for candidate in sorted(grouped[episode.event_id], key=lambda item: item.frame.decision_offset):
            if denominator is None:
                denominator = prior._denominator(candidate)
            record = forecasts.get(candidate.key, {}) if candidate.query is not None else {}
            _check(not record or record["session"] == episode.session, "policy forecast date differs")
            values = record.get("predictions", {})
            qi, qw = [None if values.get(head) is None else Fraction(str(values[head])) for head in VALUES]
            unavailable = prior._admission(candidate)
            if unavailable:
                reason = unavailable
            elif qi is None or qw is None:
                reason = "missing_paired_value_heads"
            elif qi <= 0 and qw <= 0:
                reason = "observe_without_commitment"
            else:
                chosen, action, reason = candidate, "immediate" if qi >= qw else "wait3", "first_positive_paired_value"
            trace.append([episode.event_id, candidate.key, candidate.frame.decision_offset, candidate.frame.state,
                action if chosen else "abstain" if unavailable or qi is None or qw is None else "observe",
                reason, None if qi is None else str(qi), None if qw is None else str(qw)])
            if chosen:
                denominator = prior._denominator(chosen)
                break
        choices.append({"episode": prior._plain(episode), "candidate": prior._plain(chosen), "action": action,
                        "reason": reason, "initial_denominator": None if denominator is None else str(denominator)})
    body = {"control": saved["control"], "model": saved["model"], "scope": saved["scope"],
        "prediction_binding": saved["prediction_binding"], "roster_sha256": digest.hexdigest(), "trace": trace, "choices": []}
    for choice in choices:
        short, candidate = dict(choice), choice["candidate"]
        if candidate is not None:
            key = candidate["query"]["key"] if candidate["query"] is not None else (
                candidate["frame"]["event_id"] + "@" + str(candidate["frame"]["decision_offset"]))
            short["candidate"] = {"frame": candidate["frame"], "label_query": candidate["label_query"], "key": key}
        body["choices"].append(short)
    prior._same(saved, {**body, "choices": choices, "sha256": prior._hash(body)}, "calibrated/mean policy")
    return {"verified": True, "episodes": len(choices)}


def _policy_cash(policy, records):
    rows = prior._policy_cash(policy, records)
    if policy["model"] == "EQ-mean":
        for row in rows:
            row["schema"] = "EQ-policy-cash-v1"
    if policy["model"] == "ER-calibrated":
        for row in rows:
            row["schema"] = "ER-policy-cash-v1"
    return rows


def _weekly(days, rows_by_arm):
    weeks = sorted({date.fromisoformat(day).strftime("%G-W%V") for day in days})
    output, maps = {}, {}
    for arm in ARMS:
        rows = rows_by_arm[arm]
        maps[arm] = {row["event_id"]: row for row in rows}
        _check(len(maps[arm]) == len(rows) and all(row["session"] in days and row["arm"] == arm for row in rows),
               "policy roster/calendar differs")
        known = [prior._exact(row["net_cash"]) for row in rows if row["net_cash"] is not None]
        total = sum(known, Fraction())
        weekly = [{"week": week, "fills": sum(row["fill"] == 1 for row in rows
                    if date.fromisoformat(row["session"]).strftime("%G-W%V") == week),
                   "unknown_fills": sum(row["fill"] is None for row in rows
                    if date.fromisoformat(row["session"]).strftime("%G-W%V") == week)} for week in weeks]
        fills = sum(row["fills"] for row in weekly)
        output[arm] = {"episodes": len(rows), "commits": sum(row["action"] in ("immediate", "wait3") for row in rows),
            "known_cash_episodes": len(known), "unknown_cash_episodes": len(rows) - len(known),
            "fills": fills, "unknown_fills": sum(row["unknown_fills"] for row in weekly),
            "known_net_cash": str(total), "mean_known_cash_per_episode": str(total / len(known)) if known else None,
            "calendar_weeks": len(weeks), "weekly_fills": weekly, "fills_per_week": fills / len(weeks),
            "weeks_at_least_fifteen": sum(row["fills"] >= 15 for row in weekly),
            "status_counts": dict(sorted(Counter(row["status"] for row in rows).items()))}
    comparisons = {}
    for left_arm, right_arm in COMPARISONS:
        left, right = maps[left_arm], maps[right_arm]
        _check(left.keys() == right.keys(), "paired policy population differs")
        differences = []
        for key, a in left.items():
            b = right[key]
            _check(all(a[field] == b[field] for field in ("session", "symbol", "cluster_id", "family")),
                   "paired policy identity differs")
            if a["net_cash"] is not None and b["net_cash"] is not None:
                differences.append(prior._exact(a["net_cash"]) - prior._exact(b["net_cash"]))
        total = sum(differences, Fraction())
        paired_fills = [(left[key]["fill"], right[key]["fill"]) for key in left
                        if left[key]["fill"] is not None and right[key]["fill"] is not None]
        comparisons[left_arm + "_minus_" + right_arm] = {"paired_known_episodes": len(differences),
            "paired_unknown_episodes": len(left) - len(differences), "paired_cash_difference": str(total),
            "mean_paired_cash_difference": str(total / len(differences)) if differences else None,
            "commit_difference": sum(row["action"] in ("immediate", "wait3") for row in left.values())
                                 - sum(row["action"] in ("immediate", "wait3") for row in right.values()),
            "changed_actions": sum(left[key]["action"] != right[key]["action"] for key in left),
            "paired_known_fill_episodes": len(paired_fills),
            "paired_unknown_fill_episodes": len(left) - len(paired_fills),
            "paired_fill_difference": sum(a - b for a, b in paired_fills)}
    return {"arms": output, "comparisons": comparisons,
            "claim": "nominal event cash on observed saved targets; not a funded account or filled-notional return"}


def verify_report(calendar, fold, reps, target_records, policies_by_arm, saved_report):
    days, records = prior._days(calendar, fold), prior._map(target_records)
    identities = [(row["event_id"], row["phase"]) for row in reps]
    events = {row["event_id"] for row in reps}
    _check(len(identities) == len(set(identities)) and set(identities) == {(event, phase) for event in events for phase in PHASES},
           "all four episode phases required")
    joined = []
    for rep in reps:
        _check(rep["fold"] == fold and rep["session"] in days, "off-fold representative")
        outcomes = {head: {"value": None, "fill": None} for head in VALUES}
        if rep["key"] is not None:
            _check(rep["key"] in records, "representative target missing")
            record, denominator = records[rep["key"]], prior._exact(rep["denominator"])
            prior._target_identity(record, key=rep["key"], session=rep["session"], symbol=rep["symbol"],
                event_id=rep["event_id"], decision=rep["decision_offset"], denominator=denominator)
            outcomes = {head: prior._action(record, head[4:], denominator, rep["decision_offset"], rep["session_length"])
                        for head in VALUES}
        joined.append({**rep, "outcomes": outcomes})
    _check(set(policies_by_arm) == set(ARMS), "all three policies required")
    policy_rows = {arm: _policy_cash(policies_by_arm[arm], records) for arm in ARMS}
    _check(all({row["event_id"] for row in policy_rows[arm]} == events for arm in ARMS), "policy/phase population differs")
    cells = []
    for phase in PHASES:
        rows = [row for row in joined if row["phase"] == phase]
        paired = [row for row in rows if all(row["outcomes"][head]["value"] is not None
                    and all(row["predictions"][arm][head] is not None for arm in ARMS) for head in VALUES)]
        arms = {}
        for arm in ARMS:
            arms[arm] = {}
            ranking = [{**row, "predictions": row["predictions"][arm],
                "heads": {head: {"target": row["outcomes"][head]["value"]} for head in VALUES}} for row in paired]
            for head in VALUES:
                groups = rank_reference._pair_groups(ranking, head)
                arms[arm][head] = {"own_known": sum(row["outcomes"][head]["value"] is not None
                    and row["predictions"][arm][head] is not None for row in rows),
                    "calibration": prior._calibration(paired, arm, head),
                    "cross_cluster": rank_reference._combine(groups),
                    "same_date": rank_reference._combine(groups, same_date=True),
                    "delete5_sensitivity": rank_reference._sensitivity(groups, days)}
        cells.append({"fold": fold, "phase": phase, "episodes": len(rows),
            "selected": sum(row["key"] is not None for row in rows), "paired_known": len(paired),
            "reason_counts": dict(sorted(Counter(row["reason"] for row in rows).items())), "arms": arms})
    expected = {"schema": "ER-comparison-report-v1", "fold": fold, "calendar": list(days), "cells": cells,
        "policy_rows": policy_rows, "policy": _weekly(days, policy_rows),
        "claim": "repeated Discovery; training-only calibrated direct versus stored direct and situation mean; no promotion"}
    prior._same(saved_report, expected, "report")
    return {"verified": True, "cells": len(cells), "policy_rows": sum(map(len, policy_rows.values()))}


def verify_pool(calendar, reports, saved):
    days, reports = prior._days(calendar), tuple(reports)
    _check(len(reports) == 4 and [report["fold"] for report in reports] == list(range(4)), "four ordered folds required")
    for fold, report in enumerate(reports):
        _check(report["calendar"] == list(prior._days(calendar, fold)), "pooled fold calendar differs")
        _check([(cell["fold"], cell["phase"]) for cell in report["cells"]] == [(fold, phase) for phase in PHASES],
               "all sixteen fold/phase cells required")
    rows = {arm: [row for report in reports for row in report["policy_rows"][arm]] for arm in ARMS}
    expected = {"schema": "ER-four-fold-report-v1", "calendar_days": len(days),
        "cells": [cell for report in reports for cell in report["cells"]], "policy": _weekly(days, rows),
        "claim": "all sixteen cells retained; no pooled best-cell AUC or promotion"}
    prior._same(saved, expected, "pool")
    return {"verified": True, "cells": 16}
