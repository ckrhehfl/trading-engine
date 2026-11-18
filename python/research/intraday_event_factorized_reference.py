"""Independent, IO-free EP witnesses; never fit or invoke EP producer math.

Original public datatypes/constants and EO's unchanged explicit-pair witness
are reused. Native inference replay is a separate workflow check; this module
does not claim to independently implement a native optimizer.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import date
from fractions import Fraction
from hashlib import sha256
from itertools import accumulate
import json
import math
import random

from research import intraday_event_rank_reference as rank_reference
from research.intraday_event_execution import CAPITAL, COST, FIRST_RISK
from research.intraday_event_models import HEADS, HELD_STATES
from research.intraday_event_sequence_model import PARAMETERS, SEED, UPDATES, BATCH_SIZE


ARMS = ("direct", "factorized")
VALUES = ("net_immediate", "net_wait3")
PHASES = ("onset", "waiting_pullback", "pullback", "confirmation")
SIX_HEADS = (*HEADS, "fill_wait3")


def _check(condition, message):
    if not condition:
        raise ValueError("EP independent: " + message)


def _plain(value):
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _encode(value, *, ascii=False):
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=ascii, allow_nan=False).encode()


def _hash(value, *, ascii=False):
    return sha256(_encode(value, ascii=ascii)).hexdigest()


def _same(actual, expected, where="record"):
    if isinstance(expected, dict):
        _check(isinstance(actual, dict) and set(actual) == set(expected), where + " fields differ")
        for key in expected:
            _same(actual[key], expected[key], where + "." + str(key))
    elif isinstance(expected, (tuple, list)):
        _check(isinstance(actual, (tuple, list)) and len(actual) == len(expected), where + " rows differ")
        for index, (left, right) in enumerate(zip(actual, expected)):
            _same(left, right, where + "." + str(index))
    elif isinstance(expected, float):
        _check(type(actual) in (int, float) and math.isfinite(actual)
               and math.isclose(actual, expected, rel_tol=2e-9, abs_tol=1e-12), where + " statistic differs")
    else:
        _check(type(actual) is type(expected) and actual == expected, where + " differs")


def _exact(value):
    _check(type(value) in (int, str, Fraction), "exact cash scalar required")
    return Fraction(value)


def _number(value):
    if value is None:
        return None
    _check(not isinstance(value, bool), "boolean score")
    result = float(Fraction(value)) if isinstance(value, str) else float(value)
    _check(math.isfinite(result), "nonfinite score")
    return result


def _days(calendar, fold=None):
    values = tuple(calendar)
    _check(len(values) == 257 and values == tuple(sorted(set(values)))
           and all(date.fromisoformat(value).isoformat() == value for value in values), "original calendar differs")
    if fold is None:
        return values[97:]
    _check(type(fold) is int and 0 <= fold < 4, "original fold differs")
    return values[97 + 40 * fold:137 + 40 * fold]


def _map(rows):
    result = {}
    for row in rows:
        _check(row["key"] not in result, "duplicate key")
        result[row["key"]] = row
    return result


def _admission(candidate):
    q = candidate.label_query
    if q.terminal and q.state != "CONFIRMED":
        return "terminal_not_admissible"
    if q.decision_offset + 31 >= q.session_length:
        return "expiry_not_inside_session"
    if q.reference_close is None:
        return "reference_unknown"
    if q.structure_stop >= q.reference_close + q.scale:
        return "stop_not_below_cap"
    return None


def _denominator(candidate):
    q = candidate.label_query
    if q.reference_close is None:
        return None
    cap = q.reference_close + q.scale
    if q.structure_stop >= cap:
        return None
    risk_quantity = FIRST_RISK // (cap * (1 + COST) - q.structure_stop)
    money_quantity = min(CAPITAL / 50, CAPITAL / (1 + COST)) // cap
    return int(min(risk_quantity, money_quantity)) * cap


def verify_representatives(calendar, fold, episodes, candidates, predictions_by_arm,
                           saved_reps, *, components_by_arm=None):
    """Choose earliest eligible causal rows independently of all forecasts."""
    days = _days(calendar, fold)
    _check(set(predictions_by_arm) == set(ARMS), "both forecast arms required")
    forecasts = {arm: _map(predictions_by_arm[arm]) for arm in ARMS}
    components = {arm: _map((components_by_arm or {}).get(arm, ())) for arm in ARMS}
    roster = {event.event_id: event for event in episodes}
    _check(len(roster) == len(episodes) and all(e.session in days and e.family == "C_FIRSTPULLBACK"
           for e in episodes), "C episode roster differs")
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
        denominator, reason = _denominator(candidate), _admission(candidate)
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
                parts = {} if chosen is None else components[arm].get(chosen.key, {})
                _check(not record or record["session"] == event.session, "forecast date differs")
                _check(not parts or parts["session"] == event.session, "component date differs")
                values = record.get("predictions", {})
                scores[arm] = {head: _number(values.get(head)) for head in (*VALUES, "fill")}
                scores[arm]["fill_wait3"] = _number(parts.get("components", {}).get("fill_wait3"))
                _check(all(scores[arm][head] is None or 0 <= scores[arm][head] <= 1
                           for head in ("fill", "fill_wait3")), "invalid fill probability")
            expected.append({"schema": "EP-representative-v1", "event_id": identity,
                "session": event.session, "symbol": event.symbol, "cluster_id": event.cluster_id,
                "fold": fold, "phase": phase, "key": None if chosen is None else chosen.key,
                "decision_offset": None if chosen is None else chosen.frame.decision_offset,
                "session_length": None if chosen is None else chosen.label_query.session_length,
                "denominator": None if denominator is None else str(denominator), "predictions": scores,
                "reason": "first_eligible_causal" if eligible else "no_eligible_causal" if choices else "no_causal_phase",
                "support": {"causal_rows": len(choices), "eligible_rows": len(eligible),
                    "reasons": dict(sorted(Counter(row[1] or "eligible" for row in choices).items()))}})
    _same(saved_reps, expected, "representatives")
    return {"verified": True, "representatives": len(expected)}


def verify_policy(episodes, candidates, predictions, serialized_policy):
    """Reconstruct first strictly positive paired commitment, trace and seals."""
    saved = serialized_policy
    _check(saved["control"] == "model" and saved["scope"] == "common_chart_peer"
           and saved["model"] in ("EP-direct", "EP-factorized"), "unregistered policy")
    _check(saved["prediction_binding"] == _hash(predictions), "prediction binding differs")
    grouped = defaultdict(list)
    roster = {e.event_id: e for e in episodes}
    _check(len(roster) == len(episodes), "duplicate policy episode")
    keys = set()
    for candidate in candidates:
        _check(roster.get(candidate.episode.event_id) == candidate.episode and candidate.key not in keys,
               "policy candidate identity differs")
        keys.add(candidate.key)
        grouped[candidate.episode.event_id].append(candidate)
    forecasts = _map(predictions)
    digest = sha256()
    ordered = sorted(episodes, key=lambda e: (e.session, e.symbol, e.family, e.event_id))
    for item in (*ordered, *sorted(candidates, key=lambda c: c.key)):
        digest.update(_encode(item) + b"\n")
    choices, trace = [], []
    for episode in ordered:
        chosen, action, reason, denominator = None, "no_commit", "no_causal_frame", None
        for candidate in sorted(grouped[episode.event_id], key=lambda c: c.frame.decision_offset):
            if denominator is None:
                denominator = _denominator(candidate)
            record = forecasts.get(candidate.key, {}) if candidate.query is not None else {}
            _check(not record or record["session"] == episode.session, "policy prediction identity differs")
            values = record.get("predictions", {})
            raw = [values.get(head) for head in VALUES]
            qi, qw = [None if v is None else Fraction(str(v)) if isinstance(v, float) else Fraction(v) for v in raw]
            unavailable = _admission(candidate)
            if unavailable:
                reason = unavailable
            elif qi is None or qw is None:
                reason = "missing_paired_value_heads"
            elif qi <= 0 and qw <= 0:
                reason = "observe_without_commitment"
            else:
                chosen = candidate
                action, reason = ("immediate" if qi >= qw else "wait3"), "first_positive_paired_value"
            trace.append([episode.event_id, candidate.key, candidate.frame.decision_offset, candidate.frame.state,
                          action if chosen else "abstain" if unavailable or qi is None or qw is None else "observe",
                          reason, None if qi is None else str(qi), None if qw is None else str(qw)])
            if chosen:
                denominator = _denominator(chosen)
                break
        choices.append({"episode": _plain(episode), "candidate": _plain(chosen), "action": action,
                        "reason": reason, "initial_denominator": None if denominator is None else str(denominator)})
    body = {"control": saved["control"], "model": saved["model"], "scope": saved["scope"],
            "prediction_binding": saved["prediction_binding"], "roster_sha256": digest.hexdigest(),
            "trace": trace, "choices": []}
    for choice in choices:
        short = dict(choice)
        candidate = choice["candidate"]
        if candidate is not None:
            key = candidate["query"]["key"] if candidate["query"] is not None else (
                candidate["frame"]["event_id"] + "@" + str(candidate["frame"]["decision_offset"]))
            short["candidate"] = {"frame": candidate["frame"], "label_query": candidate["label_query"], "key": key}
        body["choices"].append(short)
    expected = {**body, "choices": choices, "sha256": _hash(body)}
    _same(saved, expected, "policy")
    return {"verified": True, "episodes": len(choices)}


def _action(record, action, denominator, decision, length):
    target = record["targets"][action]
    _check(_exact(target["denominator"]) == denominator and denominator > 0, "action denominator differs")
    value = None if target["value"] is None else _exact(target["value"])
    cash = None if target["net_cash"] is None else _exact(target["net_cash"])
    fill, maturity, fill_maturity = target["fill"], target["matured_offset"], target["fill_matured_offset"]
    _check(fill is None or type(fill) is int and fill in (0, 1), "action fill differs")
    _check(all(clock is None or type(clock) is int and decision <= clock < length
               for clock in (maturity, fill_maturity)), "action maturity outside original session")
    _check(fill is None or fill_maturity is not None, "known fill missing maturity")
    _check((cash is None or maturity is not None) and (value is None or maturity is not None), "known cash missing maturity")
    _check(value is None or cash is not None and value == cash / denominator, "exact action cash ratio differs")
    _check(cash is None or value is not None, "known cash lost positive-denominator value")
    if fill == 0 or target["status"] in ("no_fill", "no_entry"):
        _check(cash in (None, 0) and value in (None, 0), "no-fill cash/value is nonzero")
    training = record["training_labels"]
    _check(training is not None and training["key"] == record["key"] and (
        None if training["net_" + action] is None else _exact(training["net_" + action])) == value,
        "saved training label differs from action")
    expected_day = record["session"] if any(training[head] is not None for head in HEADS) else None
    _check(training["matured_session"] == expected_day, "saved training maturity differs")
    return {"fill": fill, "value": None if value is None else str(value),
            "net_cash": None if cash is None else str(cash), "denominator": str(denominator),
            "matured_offset": maturity, "fill_matured_offset": fill_maturity,
            "status": target["status"], "reason": target["reason"]}


def _target_identity(record, *, key, session, symbol, event_id, decision, denominator):
    target = record["targets"]
    _check(record["kind"] == "labels" and record["query_kind"] == "entry"
           and (record["key"], record["session"], record["symbol"], target["event_id"], target["decision_offset"])
           == (key, session, symbol, event_id, decision)
           and target["entry_admissibility"] == "admissible"
           and _exact(target["original_requested_notional"]) == denominator, "target identity/admission differs")


def _policy_cash(policy, records):
    rows = []
    unsupported = {"no_causal_frame", "missing_paired_value_heads", "reference_unknown"}
    unknown_episodes = {row[0] for row in policy["trace"] if row[5] in unsupported}
    for choice in policy["choices"]:
        event, candidate = choice["episode"], choice["candidate"]
        key = None if candidate is None else candidate["query"]["key"]
        denominator = choice["initial_denominator"]
        row = {"schema": "EP-policy-cash-v1", "arm": policy["model"][3:], "policy_sha256": policy["sha256"],
               **{k: event[k] for k in ("session", "symbol", "event_id", "cluster_id", "family")},
               "query_key": key, "action": choice["action"], "choice_reason": choice["reason"],
               "entry_policy": "next_open_capped", "management": "fixed_exit",
               "original_requested_notional": denominator, "denominator": denominator}
        if candidate is None:
            unknown = choice["reason"] in unsupported or event["event_id"] in unknown_episodes
            row.update(status="unsupported" if unknown else "no_commit", reason=choice["reason"],
                net_cash=None if unknown else "0", fill=None if unknown else 0,
                value="0" if not unknown and denominator is not None and _exact(denominator) != 0 else None,
                matured_offset=None, fill_matured_offset=None, target_status=None, target_reason=None)
        else:
            _check(key in records and choice["action"] in ("immediate", "wait3"), "committed target missing")
            record, d = records[key], _exact(denominator)
            decision, length = candidate["frame"]["decision_offset"], candidate["label_query"]["session_length"]
            _target_identity(record, key=key, session=event["session"], symbol=event["symbol"],
                             event_id=event["event_id"], decision=decision, denominator=d)
            action = _action(record, choice["action"], d, decision, length)
            row.update(action, target_status=action["status"], target_reason=action["reason"])
        rows.append(row)
    return rows


def _weekly(days, rows_by_arm):
    weeks = sorted({date.fromisoformat(day).strftime("%G-W%V") for day in days})
    output, maps = {}, {}
    for arm in ARMS:
        rows = rows_by_arm[arm]
        maps[arm] = {row["event_id"]: row for row in rows}
        _check(len(maps[arm]) == len(rows) and all(row["session"] in days and row["arm"] == arm
               for row in rows), "policy roster/calendar differs")
        known = [_exact(row["net_cash"]) for row in rows if row["net_cash"] is not None]
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
    _check(maps["direct"].keys() == maps["factorized"].keys(), "paired policy population differs")
    differences = []
    for key, left in maps["direct"].items():
        right = maps["factorized"][key]
        _check(all(left[field] == right[field] for field in ("session", "symbol", "cluster_id", "family")),
               "paired policy identity differs")
        if left["net_cash"] is not None and right["net_cash"] is not None:
            differences.append(_exact(right["net_cash"]) - _exact(left["net_cash"]))
    total = sum(differences, Fraction())
    return {"arms": output, "paired_known_episodes": len(differences),
            "paired_unknown_episodes": len(maps["direct"]) - len(differences),
            "factorized_minus_direct_cash": str(total),
            "mean_paired_cash_difference": str(total / len(differences)) if differences else None,
            "claim": "nominal event cash on observed saved targets; not a funded account or filled-notional return"}


def _phase_weights(rows):
    counts = defaultdict(Counter)
    for row in rows:
        counts[row["session"]][row["symbol"]] += 1
    return [1 / (len(counts) * len(counts[row["session"]]) * counts[row["session"]][row["symbol"]]) for row in rows]


def _calibration(rows, arm, head):
    values = [(row["predictions"][arm][head], float(_exact(row["outcomes"][head]["value"])), weight)
              for row, weight in zip(rows, _phase_weights(rows))]
    if not values:
        return dict.fromkeys(("prediction_mean", "target_mean", "signed_error", "mae", "rmse", "zero_mae", "zero_rmse"))
    return {"prediction_mean": math.fsum(p*w for p, y, w in values),
        "target_mean": math.fsum(y*w for p, y, w in values), "signed_error": math.fsum((p-y)*w for p, y, w in values),
        "mae": math.fsum(abs(p-y)*w for p, y, w in values), "rmse": math.sqrt(math.fsum((p-y)**2*w for p, y, w in values)),
        "zero_mae": math.fsum(abs(y)*w for p, y, w in values), "zero_rmse": math.sqrt(math.fsum(y*y*w for p, y, w in values))}


def verify_report(calendar, fold, reps, target_records, policies_by_arm, saved_report):
    """Rebuild exact joins and all four phase cells using original labels."""
    days, records = _days(calendar, fold), _map(target_records)
    identities = [(row["event_id"], row["phase"]) for row in reps]
    events = {row["event_id"] for row in reps}
    _check(len(identities) == len(set(identities)) and set(identities) == {(e, p) for e in events for p in PHASES},
           "all four episode phases required")
    joined = []
    for rep in reps:
        _check(rep["fold"] == fold and rep["session"] in days, "off-fold representative")
        outcomes = {head: {"value": None, "fill": None} for head in VALUES}
        if rep["key"] is not None:
            _check(rep["key"] in records, "representative target missing")
            record, denominator = records[rep["key"]], _exact(rep["denominator"])
            _target_identity(record, key=rep["key"], session=rep["session"], symbol=rep["symbol"],
                event_id=rep["event_id"], decision=rep["decision_offset"], denominator=denominator)
            outcomes = {head: _action(record, head[4:], denominator, rep["decision_offset"], rep["session_length"])
                        for head in VALUES}
        joined.append({**rep, "outcomes": outcomes})
    policy_rows = {arm: _policy_cash(policies_by_arm[arm], records) for arm in ARMS}
    _check(all({row["event_id"] for row in policy_rows[arm]} == events for arm in ARMS), "report policy/phase roster differs")
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
                field = "fill" if head == "net_immediate" else "fill_wait3"
                fill_rows = [row for row in rows if row["outcomes"][head]["fill"] is not None
                             and all(row["predictions"][a][field] is not None for a in ARMS)]
                triples = [(row["predictions"][arm][field], row["outcomes"][head]["fill"], weight)
                           for row, weight in zip(fill_rows, _phase_weights(fill_rows))]
                fill_scores = {"rows": len(triples),
                    "brier": math.fsum(w*(p-y)**2 for p, y, w in triples) if triples else None,
                    "prediction_mean": math.fsum(w*p for p, y, w in triples) if triples else None,
                    "observed_mean": math.fsum(w*y for p, y, w in triples) if triples else None}
                groups = rank_reference._pair_groups(ranking, head)
                arms[arm][head] = {"own_known": sum(row["outcomes"][head]["value"] is not None
                    and row["predictions"][arm][head] is not None for row in rows),
                    "calibration": _calibration(paired, arm, head), "fill": fill_scores,
                    "cross_cluster": rank_reference._combine(groups),
                    "same_date": rank_reference._combine(groups, same_date=True),
                    "delete5_sensitivity": rank_reference._sensitivity(groups, days)}
        cells.append({"fold": fold, "phase": phase, "episodes": len(rows),
            "selected": sum(row["key"] is not None for row in rows), "paired_known": len(paired),
            "reason_counts": dict(sorted(Counter(row["reason"] for row in rows).items())), "arms": arms})
    expected = {"schema": "EP-comparison-report-v1", "fold": fold, "calendar": list(days), "cells": cells,
        "policy_rows": policy_rows, "policy": _weekly(days, policy_rows),
        "claim": "repeated Discovery; expected value and positive-outcome ranking are different estimands"}
    _same(saved_report, expected, "report")
    return {"verified": True, "cells": len(cells), "policy_rows": sum(map(len, policy_rows.values()))}


def verify_pool(calendar, reports, saved):
    days, reports = _days(calendar), tuple(reports)
    _check(len(reports) == 4 and [report["fold"] for report in reports] == list(range(4)), "four ordered folds required")
    for fold, report in enumerate(reports):
        _check(report["calendar"] == list(_days(calendar, fold)), "pooled fold calendar differs")
        _check([(cell["fold"], cell["phase"]) for cell in report["cells"]] == [(fold, p) for p in PHASES],
               "all sixteen fold/phase cells required")
    rows = {arm: [row for report in reports for row in report["policy_rows"][arm]] for arm in ARMS}
    expected = {"schema": "EP-four-fold-report-v1", "calendar_days": len(days),
        "cells": [cell for report in reports for cell in report["cells"]], "policy": _weekly(days, rows),
        "claim": "all sixteen cells retained; no pooled best-cell AUC or promotion"}
    _same(saved, expected, "pool")
    return {"verified": True, "cells": 16}


def _hierarchy(rows):
    dates, clusters, episodes = defaultdict(set), defaultdict(set), Counter()
    for row in rows:
        episode = row.original_episode_id or row.episode_id
        dates[row.session].add(row.cluster_id)
        clusters[row.session, row.cluster_id].add(episode)
        episodes[row.session, row.cluster_id, episode] += 1
    return [1 / (len(dates) * len(dates[row.session]) * len(clusters[row.session, row.cluster_id])
                 * episodes[row.session, row.cluster_id, row.original_episode_id or row.episode_id]) for row in rows]


def _moments(vectors, width):
    counts, means, sums = [0] * width, [0.0] * width, [0.0] * width
    for vector in vectors:
        for column, raw in enumerate(vector):
            if raw is None:
                continue
            value = _number(raw)
            counts[column] += 1
            difference = value - means[column]
            means[column] += difference / counts[column]
            sums[column] += difference * (value - means[column])
    return {"mean": means, "std": [math.sqrt(max(0, total) / max(1, count)) or 1.0
            for total, count in zip(sums, counts)], "finite_counts": counts}


def verify_fit(result, data):
    """Rebuild J, hierarchy/conditional weights, normalizers and sampled rows.

    Input action targets are the authenticated dataset adapter's output. Raw
    price-to-target accounting remains covered by the original dataset audit;
    raw evaluation cash is separately reconstructed by ``verify_report``.
    """
    base, artifact, arm = data.base, result["artifact"], result["arm"]
    _check(arm in ARMS and base.scope == "common_chart_peer", "fit arm/scope differs")
    evaluation_days = _days(base.calendar, base.fold)
    rows = tuple(sorted(base.training, key=lambda row: row.key))
    labels = {label.key: label for label in base.targets}
    fills = {fill.key: fill for fill in data.action_targets}
    _check(len(labels) == len(base.targets) == len(rows) == len(fills) == len(data.action_targets)
           and set(labels) == set(fills) == {row.key for row in rows}, "training identity differs")
    _check([row["key"] for row in result["predictions"]] == [row.key for row in base.evaluation]
           and [row["key"] for row in result["components"]] == [row.key for row in base.evaluation],
           "inference population differs")
    for field in ("predictions", "components"):
        _check(result[field + "_sha256"] == _hash(result[field]), "forecast/component seal differs")
    if not rows:
        _check(artifact is None and result["status"] == "unsupported_empty_training" and result["fit_artifacts"] == 0,
               "empty training invented artifact")
    else:
        _check(result["artifact_sha256"] == _hash(artifact), "artifact seal differs")
        _check(artifact["arm"] == arm and artifact["parameters"] == PARAMETERS, "fixed fit parameters differ")
        support_hash = _hash([(row.key, row.session, row.symbol, row.episode_id, row.decision_offset,
                              row.cluster_id, row.family, row.state, row.original_episode_id or row.episode_id)
                             for row in rows])
        _check(artifact["common_support_sha256"] == base.support["training_seal"] == support_hash,
               "common training support seal differs")
        _check(artifact["training_rows"] == len(rows)
               and artifact["eval_first_session"] == evaluation_days[0]
               and artifact["training_sessions"] == sorted({row.session for row in rows})
               and artifact["training_key_hashes"] == sorted(sha256(row.key.encode()).hexdigest() for row in rows),
               "artifact training population differs")
        for row in rows:
            label, action = labels[row.key], fills[row.key]
            _check(row.session < evaluation_days[0] and (label.matured_session is None
                   or row.session <= label.matured_session < evaluation_days[0]), "training target chronology differs")
            for name in ("immediate", "wait3"):
                fill, maturity = getattr(action, name), getattr(action, name + "_matured_session")
                _check(fill is None or type(fill) is int and fill in (0, 1) and maturity is not None,
                       "training action fill/maturity differs")
                _check(maturity is None or row.session <= maturity < evaluation_days[0], "training fill chronology differs")
                _check(row.state not in HELD_STATES or fill is None, "held action fill")
                value = getattr(label, "net_" + name)
                _check(fill != 0 or value is None or Fraction(value) == 0, "training no-fill has nonzero target")
            _check(label.fill is None or action.immediate is None or label.fill == action.immediate,
                   "immediate fill label differs")
        normalization = {
            "sequence": _moments((step for row in rows for step in row.sequence), len(rows[0].sequence[0])),
            "static": _moments((row.static for row in rows), len(rows[0].static)),
            "position": _moments((row.position or (None,) * 3 for row in rows), 3),
            "peer": _moments((row.peer for row in rows), 3),
            "policy": "training-only channel-shared moments; missing standardized=0 plus per-value missing flag"}
        _same(artifact["normalization"], normalization, "feature normalization")
        joint = [i for i, row in enumerate(rows) if row.state not in HELD_STATES
                 and all(getattr(labels[row.key], head) is not None for head in VALUES)
                 and fills[row.key].immediate is not None and fills[row.key].wait3 is not None]
        joint_set, bases, effective, expected_heads, trainable = set(joint), {}, {}, {}, []
        for head in SIX_HEADS:
            raw = [getattr(fills[row.key], "immediate" if head == "fill" else "wait3")
                   if head in ("fill", "fill_wait3") else getattr(labels[row.key], head) for row in rows]
            indices = joint if head in (*VALUES, "fill", "fill_wait3") else [i for i, value in enumerate(raw) if value is not None]
            base_weights = [0.0] * len(rows)
            for index, weight in zip(indices, _hierarchy([rows[i] for i in indices])):
                base_weights[index] = weight
            bases[head] = base_weights
            conditional = arm == "factorized" and head in VALUES
            weights = list(base_weights)
            if conditional:
                weights = [weight if getattr(fills[row.key], head[4:]) == 1 else 0.0
                           for row, weight in zip(rows, weights)]
                mass = math.fsum(weights)
                if mass:
                    weights = [weight / mass for weight in weights]
            effective[head] = weights
            known = [i for i, weight in enumerate(weights) if weight > 0]
            clusters = len({rows[i].cluster_id for i in known})
            dates = len({rows[i].session for i in known})
            values = [_number(raw[i]) for i in known]
            info = {"known_rows": len(known), "excluded_unknown_rows": len(rows) - len(known),
                "known_episodes": len({rows[i].original_episode_id or rows[i].episode_id for i in known}),
                "known_counterfactual_books": len({rows[i].episode_id for i in known if rows[i].state in HELD_STATES}),
                "known_clusters": clusters, "known_dates": dates, "low_support": clusters < 200 or dates < 30,
                "raw_known_rows": sum(value is not None for value in raw), "base_known_rows": len(indices),
                "known_zero_rows": sum(value == 0 for value in values), "conditional_on_fill": conditional,
                "dropped_outside_joint_rows": sum(value is not None and i not in joint_set for i, value in enumerate(raw))
                    if head in (*VALUES, "fill", "fill_wait3") else 0}
            if not known:
                info.update(status="unsupported", reason="no_known_conditional_fills" if conditional else "no_known_labels")
            elif min(len(known), clusters, dates) < 2:
                info.update(status="unsupported", reason="fewer_than_two_known_rows_clusters_or_dates")
            elif head in ("path_class", "fill", "fill_wait3") and len(set(values)) == 1:
                info.update(status="constant_single_class", **{"class": int(values[0])})
            else:
                trainable.append(head)
                status = artifact["heads"][head]["status"]
                _check(status in ("trained_tcn", "failed"), "native head status differs")
                info["status"] = status
                if head not in ("path_class", "fill", "fill_wait3"):
                    mean = math.fsum(_number(raw[i]) * weights[i] for i in known)
                    variance = math.fsum(weights[i] * (_number(raw[i]) - mean)**2 for i in known)
                    info["target_normalization"] = {"mean": mean, "std": math.sqrt(variance) or 1.0}
            info["fit_input_known_rows"] = len(known) if head in trainable else 0
            for name, expected in info.items():
                _same(artifact["heads"][head].get(name), expected, "head." + head + "." + name)
            expected_heads[head] = info
        support = artifact["action_training_support"]
        _check(support["joint_known_rows"] == len(joint) and support["excluded_from_joint_rows"] == len(rows) - len(joint)
               and support["joint_support_sha256"] == _hash([rows[i].key for i in joint], ascii=True), "joint cohort differs")
        for action in ("immediate", "wait3"):
            observed = support["zero_decomposition"][action]
            expected = {"joint_known_rows": len(joint),
                "no_fill_zero_rows": sum(getattr(fills[rows[i].key], action) == 0 for i in joint),
                "filled_zero_rows": sum(getattr(fills[rows[i].key], action) == 1
                    and Fraction(getattr(labels[rows[i].key], "net_" + action)) == 0 for i in joint),
                "filled_nonzero_rows": sum(getattr(fills[rows[i].key], action) == 1
                    and Fraction(getattr(labels[rows[i].key], "net_" + action)) != 0 for i in joint),
                "unknown_fill_rows": sum(getattr(fills[row.key], action) is None for row in rows if row.state not in HELD_STATES),
                "weighted_fill_mass": math.fsum(bases["fill"][i] for i in joint if getattr(fills[rows[i].key], action) == 1),
                "fill_known_cash_unknown_rows": sum(row.state not in HELD_STATES
                    and getattr(fills[row.key], action) is not None and getattr(labels[row.key], "net_" + action) is None for row in rows),
                "joint_excluded_fill_known_rows": sum(row.state not in HELD_STATES and i not in joint_set
                    and getattr(fills[row.key], action) is not None for i, row in enumerate(rows))}
            _same(observed, expected, "action support." + action)
        active_bases = [weights for weights in bases.values() if any(weights)]
        proposal = [math.fsum(weights[i] for weights in active_bases) / len(active_bases)
                    for i in range(len(rows))] if active_bases else []
        diagnostics = artifact["training_diagnostics"]
        _check(diagnostics["proposal_sha256"] == _hash(proposal, ascii=True), "common proposal differs")
        identities = [_hash([row.key, row.session, row.symbol, row.episode_id, row.cluster_id,
                            row.decision_offset, row.family, row.state], ascii=True) for row in rows]
        sampled = sorted(range(len(rows)), key=lambda i: (identities[i], rows[i].key))[:512]
        hashes = [identities[i] for i in sampled]
        _check(diagnostics["sample_identity_sha256"] == hashes and diagnostics["sample_sha256"] == _hash(hashes, ascii=True)
               and diagnostics["sampled_rows"] == len(sampled), "diagnostic sample differs")
        trained = [head for head in trainable if expected_heads[head]["status"] == "trained_tcn"]
        counts = {"fit_head_count": len(trained), "fit_attempt_head_count": len(trainable), "native_fit_head_count": len(trained),
            "native_joint_fit_count": int(bool(trained)), "native_updates": UPDATES if trained else 0,
            "constant_head_count": sum(info["status"] == "constant_single_class" for info in expected_heads.values()),
            "unsupported_head_count": sum(info["status"] == "unsupported" for info in expected_heads.values()),
            "failed_head_count": len(trainable) - len(trained)}
        for field, expected in counts.items():
            _same(artifact[field], expected, field)
        if trained:
            _check(len(trained) == len(trainable) and diagnostics["completed_updates"] == UPDATES,
                   "partial native update accounting differs")
            rng, digest = random.Random(SEED), sha256()
            cumulative = list(accumulate(proposal))
            hits, sampled_heads = Counter(), defaultdict(set)
            for _ in range(UPDATES):
                selected = rng.choices(range(len(rows)), cum_weights=cumulative, k=BATCH_SIZE)
                digest.update(_encode(selected, ascii=True) + b"\n")
                for head in trainable:
                    known = [i for i in selected if effective[head][i] > 0]
                    hits[head] += bool(known)
                    sampled_heads[head].update(known)
            _check(diagnostics["sampled_indices_sha256"] == digest.hexdigest(), "native sample sequence differs")
            for head in trainable:
                actual = artifact["heads"][head]
                _check(actual["updates_with_known_target"] == hits[head]
                       and actual["sampled_training_rows"] == len(sampled_heads[head])
                       and actual["sampled_training_clusters"] == len({rows[i].cluster_id for i in sampled_heads[head]}),
                       "head sample accounting differs")
    for query, forecast, components in zip(base.evaluation, result["predictions"], result["components"]):
        values, parts = forecast["predictions"], components["components"]
        _check(forecast["session"] == components["session"] == query.session
               and set(values) == set(HEADS)
               and set(parts) == {"fill_wait3", "conditional_net_immediate", "conditional_net_wait3"},
               "prediction component identity differs")
        for head, fill_head in (("net_immediate", "fill"), ("net_wait3", "fill_wait3")):
            probability = values["fill"] if fill_head == "fill" else parts[fill_head]
            _check(probability is None or 0 <= _number(probability) <= 1, "invalid action fill probability")
            conditional = parts["conditional_" + head]
            if arm == "factorized":
                expected = None if probability is None or conditional is None else _number(probability) * _number(conditional)
                _same(values[head], expected, "same-action product." + head)
            else:
                _check(conditional is None, "direct arm invented conditional value")
    return {"verified": True, "training_rows": len(rows), "predictions": len(base.evaluation),
            "native_optimizer_reimplemented": False}


def verify_matched(results):
    """Both arms must share inputs, feature transform, initialization and draws."""
    _check(set(results) == set(ARMS), "both matched arms required")
    left, right = (results[arm]["artifact"] for arm in ARMS)
    if left is None or right is None:
        _check(left is right is None and all(results[arm]["status"] == "unsupported_empty_training"
               and results[arm]["fit_artifacts"] == 0 for arm in ARMS), "matched empty support differs")
        return {"verified": True, "status": "unsupported_empty_training"}
    for field in ("common_support_sha256", "training_sessions", "training_key_hashes", "normalization", "parameters"):
        _same(left[field], right[field], "matched." + field)
    ldiag, rdiag = left["training_diagnostics"], right["training_diagnostics"]
    _same(ldiag["proposal_sha256"], rdiag["proposal_sha256"], "matched.proposal")
    # No trainable head means no network was initialized. Preserve that
    # explicit unsupported branch instead of inventing an initialization.
    if ldiag["initial_state_sha256"] is not None and rdiag["initial_state_sha256"] is not None:
        _same(ldiag["initial_state_sha256"], rdiag["initial_state_sha256"], "matched.initial_state")
    if ldiag["completed_updates"] == rdiag["completed_updates"] == UPDATES:
        _same(ldiag["sampled_indices_sha256"], rdiag["sampled_indices_sha256"], "matched.sampled_indices")
    return {"verified": True, "status": "matched"}
