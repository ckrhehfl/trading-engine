"""Two matched immediate-entry policies and exact original event cash."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from hashlib import sha256

from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_metrics as timing
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_immediate_value as mapping
from research import intraday_event_mean_baseline as baseline


ARMS = ("direct", "mapped")
PHASES = baseline.PHASES
SCHEMA = "ET-immediate-policy-v1"
CLAIM = "modeled nominal event cash; repeated Discovery"


def _require(condition, message):
    if not condition:
        raise ValueError("ET immediate: " + message)


def _score(arm, prediction):
    value = prediction["predictions"]["net_immediate"]
    if arm == "mapped":
        return mapping.exact_value(value)
    if value is None:
        return None
    timing._score(value)  # Existing finite saved native-score check.
    return Fraction(str(value))


def freeze(data, roster, predictions_by_arm):
    """Seal one causal immediate decision per original C event in each arm."""
    base = baseline._base(data)
    days = timing._calendar(base.calendar, base.fold)
    _require(set(predictions_by_arm) == set(ARMS), "both immediate arms required")
    episodes = {e.event_id: e for e in roster.episodes}
    _require(len(episodes) == len(roster.episodes) and all(e.family == workflow.FAMILY and e.session in days
              for e in roster.episodes), "original C episodes required")
    by_event, keys, clocks = defaultdict(list), set(), set()
    for c in roster.candidates:
        clock = (c.episode.cluster_id, c.frame.decision_offset)
        _require(episodes.get(c.episode.event_id) == c.episode and c.key not in keys and clock not in clocks,
                 "candidate identity or original coalesced clock differs")
        keys.add(c.key)
        clocks.add(clock)
        by_event[c.episode.event_id].append(c)
    wanted = [(q.key, q.session) for q in base.evaluation]
    queries = {q.key: q for q in base.evaluation}
    ordered = sorted(episodes.values(), key=lambda e: (e.session, e.symbol, e.event_id))
    binding = sha256()
    for row in (*ordered, *sorted(roster.candidates, key=lambda c: c.key)):
        binding.update(evaluation._json(row) + b"\n")
    output = {}
    for arm in ARMS:
        rows = tuple(predictions_by_arm[arm])
        _require([(r["key"], r["session"]) for r in rows] == wanted, "forecast roster differs")
        indexed = timing._maps(rows)
        choices, trace = [], []
        for e in ordered:
            selected, unknown = None, not by_event[e.event_id]
            for c in sorted(by_event[e.event_id], key=lambda item: item.frame.decision_offset):
                phase = baseline._phase(e.family, c.frame.state, c.frame.decision_offset, e.onset_decision_offset)
                reason = evaluation._admission(c)
                denominator, score = evaluation._denominator(c), None
                if reason is None:
                    if phase is None:
                        reason = "outside_original_phase"
                    elif denominator is None or denominator <= 0:
                        reason = "nonpositive_notional"
                    elif c.query is None or c.key not in indexed:
                        reason = "unsupported_features"
                    else:
                        _require(c.query == queries[c.key], "candidate and original feature query differ")
                        score = _score(arm, indexed[c.key])
                        reason = ("unsupported_value" if score is None else
                                  "first_positive_immediate" if score > 0 else "observe_nonpositive")
                if reason in {"reference_unknown", "unsupported_features", "unsupported_value"}:
                    unknown = True
                trace.append([e.event_id, c.key, c.frame.decision_offset, phase, reason,
                              None if score is None else (1 if score > 0 else -1 if score < 0 else 0)])
                if reason == "first_positive_immediate":
                    selected = {"query_key": c.key, "decision_offset": c.frame.decision_offset,
                        "phase": phase, "denominator": str(denominator), "action": "immediate", "reason": reason}
                    break
            choices.append({"event_id": e.event_id, "session": e.session, "symbol": e.symbol,
                "cluster_id": e.cluster_id, "family": e.family,
                **(selected if selected is not None else {"query_key": None, "decision_offset": None,
                    "phase": None, "denominator": None, "action": "no_commit",
                    "reason": "unsupported_decision" if unknown else "known_abstention"})})
        body = {"schema": SCHEMA, "fold": base.fold, "arm": arm, "roster_sha256": binding.hexdigest(),
                "predictions_sha256": baseline._hash(rows), "choices": choices, "trace": trace}
        output[arm] = {**body, "sha256": baseline._hash(body)}
    return output


def _join(roster, policies, target_records, fold):
    _require(set(policies) == set(ARMS), "two sealed policies required")
    candidates = {c.key: c for c in roster.candidates}
    targets = timing._maps(target_records)
    _require(targets.keys() == candidates.keys(), "complete original evaluation target roster required")
    episodes = {e.event_id: e for e in roster.episodes}
    results = {}
    for arm in ARMS:
        policy = policies[arm]
        _require(policy["arm"] == arm and policy["schema"] == SCHEMA and policy["fold"] == fold
            and policy["sha256"] == baseline._hash({k: v for k, v in policy.items() if k != "sha256"}),
            "original policy seal differs")
        _require(len(policy["choices"]) == len(episodes) and {r["event_id"] for r in policy["choices"]} == episodes.keys(),
                 "policy roster differs")
        output = []
        for c in policy["choices"]:
            event = episodes[c["event_id"]]
            _require(all(c[field] == getattr(event, field) for field in ("session", "symbol", "cluster_id", "family")),
                     "choice episode identity differs")
            if c["query_key"] is None:
                _require(c["action"] == "no_commit" and c["reason"] in ("unsupported_decision", "known_abstention"),
                         "invalid abstention")
                known = c["reason"] == "known_abstention"
                fill, cash, status = (0, "0", "no_commit") if known else (None, None, "unsupported")
            else:
                candidate, record = candidates[c["query_key"]], targets[c["query_key"]]
                workflow.validate_saved_record(record, candidate.query, session_length=candidate.label_query.session_length,
                                                label_query=candidate.label_query)
                target = record["targets"]
                amount = workflow._exact(target["original_requested_notional"])
                _require(c["action"] == "immediate" and candidate.episode == event
                    and (target["event_id"], target["decision_offset"], record["session"], record["symbol"])
                    == (event.event_id, c["decision_offset"], event.session, event.symbol)
                    and target["entry_admissibility"] == "admissible"
                    and amount is not None and amount > 0 and amount == Fraction(c["denominator"]),
                    "frozen immediate choice and exact target disagree")
                observed = workflow._cash_target(target["immediate"], amount)
                fill, cash, status = observed["fill"], observed["net_cash"], observed["status"]
                cash = None if cash is None else str(cash)
            output.append({**c, "fill": fill, "net_cash": cash, "status": status})
        results[arm] = output
    return results


def _week(day):
    return date.fromisoformat(day).strftime("%G-W%V")


def _arm(rows):
    known = [Fraction(r["net_cash"]) for r in rows if r["net_cash"] is not None]
    return {"episodes": len(rows), "commits": sum(r["action"] == "immediate" for r in rows),
        "fills": sum(r["fill"] == 1 for r in rows), "unknown_fills": sum(r["fill"] is None for r in rows),
        "known_cash": len(known), "unknown_cash": len(rows) - len(known),
        "known_net_cash": str(sum(known, Fraction())),
        "winning_net_cash": str(sum((cash for cash in known if cash > 0), Fraction())),
        "losing_net_cash": str(sum((cash for cash in known if cash < 0), Fraction())),
        "winning_fills": sum(r["fill"] == 1 and r["net_cash"] is not None and Fraction(r["net_cash"]) > 0 for r in rows),
        "losing_fills": sum(r["fill"] == 1 and r["net_cash"] is not None and Fraction(r["net_cash"]) < 0 for r in rows),
        "statuses": dict(sorted(Counter(r["status"] for r in rows).items()))}


def _pairs(before, after):
    left, right = ({r["event_id"]: r for r in rows} for rows in (before, after))
    _require(len(left) == len(before) == len(right) == len(after) and left.keys() == right.keys(), "event pairing differs")
    deltas, counts = defaultdict(list), Counter()
    for key, old in left.items():
        new = right[key]
        _require(all(old[k] == new[k] for k in ("session", "symbol", "family", "cluster_id")), "paired event differs")
        a, b = old["query_key"], new["query_key"]
        tag = "same" if a == b else "added" if a is None else "removed" if b is None else "retimed"
        counts[tag] += 1
        if old["net_cash"] is not None and new["net_cash"] is not None:
            deltas[tag].append(Fraction(new["net_cash"]) - Fraction(old["net_cash"]))
    transitions = {tag: {"episodes": counts[tag], "known_pairs": len(deltas[tag]),
        "known_cash_delta": str(sum(deltas[tag], Fraction())), "better": sum(d > 0 for d in deltas[tag]),
        "worse": sum(d < 0 for d in deltas[tag])} for tag in ("same", "added", "removed", "retimed")}
    known = sum(row["known_pairs"] for row in transitions.values())
    return {"known_pairs": known, "unknown_pairs": len(left) - known,
        "known_cash_delta": str(sum((Fraction(r["known_cash_delta"]) for r in transitions.values()), Fraction())),
        "transitions": transitions}


def _aggregate(days, rows):
    weeks = sorted({_week(day) for day in days})
    _require(weeks and all(r["session"] in days for arm in ARMS for r in rows[arm]), "original calendar required")
    totals = {}
    for arm in ARMS:
        total = _arm(rows[arm])
        weekly = {week: [] for week in weeks}
        symbols = defaultdict(list)
        for r in rows[arm]:
            weekly[_week(r["session"])].append(r)
            symbols[r["symbol"]].append(r)
        total.update(weeks=len(weeks), fills_per_week=str(Fraction(total["fills"], len(weeks))),
            weekly=[{"week": w, "fills": sum(r["fill"] == 1 for r in weekly[w]),
                     "unknown_fills": sum(r["fill"] is None for r in weekly[w])} for w in weeks],
            by_symbol={symbol: _arm(symbols[symbol]) for symbol in sorted(symbols)})
        total["weeks_at_least_fifteen"] = sum(w["fills"] >= 15 for w in total["weekly"])
        totals[arm] = total
    return {"arms": totals, "mapped_minus_direct": _pairs(rows["direct"], rows["mapped"]), "claim": CLAIM}


def summarize(calendar, fold, roster, policies, target_records):
    days = timing._calendar(calendar, fold)
    rows = _join(roster, policies, target_records, fold)
    return {"schema": "ET-immediate-report-v1", "fold": fold, "calendar": list(days),
            "policy_rows": rows, "policy": _aggregate(days, rows)}


def pool(calendar, reports):
    reports = tuple(reports)
    days = timing._calendar(calendar)
    _require([r["fold"] for r in reports] == list(range(4)) and all(
        r["schema"] == "ET-immediate-report-v1" and r["calendar"] == list(timing._calendar(calendar, r["fold"]))
        for r in reports), "all four original periods required")
    rows = {arm: [row for r in reports for row in r["policy_rows"][arm]] for arm in ARMS}
    return {"schema": "ET-immediate-summary-v1", "calendar_days": len(days),
        "policy": _aggregate(days, rows), "periods": [{"fold": r["fold"], "policy": r["policy"]} for r in reports]}
