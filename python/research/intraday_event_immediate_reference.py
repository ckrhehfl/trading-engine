"""Independent ET witnesses for decision-time entries and exact summary cash."""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from hashlib import sha256
from itertools import chain

from research import intraday_event_factorized_reference as original


ARMS = ("direct", "mapped")


def _check(condition, message):
    if not condition:
        raise ValueError("ET independent: " + message)


def _phase(event, frame):
    _check(event.onset_decision_offset <= frame.decision_offset, "decision precedes original onset")
    if frame.decision_offset == event.onset_decision_offset:
        return "onset"
    return {"WAITING_PULLBACK": "waiting_pullback", "PULLBACK": "pullback", "CONFIRMED": "confirmation"}.get(frame.state)


def _value(arm, row):
    raw = row["predictions"]["net_immediate"]
    if raw is None:
        return None
    if arm == "direct":
        original._number(raw)
        return Fraction(str(raw))
    _check(type(raw) is list and len(raw) == 2 and all(type(part) is str for part in raw), "exact coefficient shape differs")
    result = Fraction(int(raw[0], 16), int(raw[1], 16))
    _check(raw == [format(result.numerator, "x"), format(result.denominator, "x")], "exact coefficient differs")
    return result


def verify_policies(data, roster, predictions_by_arm, policies):
    """Reconstruct eligible decisions and take the first supported positive row."""
    base = data.base
    dates = original._days(base.calendar, base.fold)
    _check(set(policies) == set(predictions_by_arm) == set(ARMS), "both immediate policies required")
    ordered = sorted(roster.episodes, key=lambda e: (e.session, e.symbol, e.event_id))
    episodes = {row.event_id: row for row in ordered}
    _check(len(episodes) == len(ordered) and all(e.session in dates and e.family == "C_FIRSTPULLBACK" for e in ordered),
           "original event roster differs")
    candidates = sorted(roster.candidates, key=lambda c: c.key)
    _check(len({c.key for c in candidates}) == len({(c.episode.cluster_id, c.frame.decision_offset) for c in candidates})
           == len(candidates), "candidate clock or key differs")
    sealed = sha256()
    for value in chain(ordered, candidates):
        sealed.update(original._encode(value) + b"\n")
    groups = defaultdict(list)
    for c in candidates:
        _check(episodes.get(c.episode.event_id) == c.episode, "candidate event differs")
        groups[c.episode.event_id].append(c)
    eval_rows = {row.key: row for row in base.evaluation}
    for arm in ARMS:
        forecasts = list(predictions_by_arm[arm])
        _check([(row["key"], row["session"]) for row in forecasts] == [(q.key, q.session) for q in base.evaluation],
               "original evaluation predictions differ")
        predictions = original._map(forecasts)
        choices, traces = [], []
        for e in ordered:
            order = sorted(groups[e.event_id], key=lambda c: c.frame.decision_offset)
            frames = []
            for c in order:
                phase, denominator = _phase(e, c.frame), original._denominator(c)
                rejection = original._admission(c)
                available = c.query is not None and c.key in eval_rows
                if available:
                    _check(c.query == eval_rows[c.key], "original candidate query differs")
                if rejection:
                    reason, comparable = rejection, False
                elif phase is None:
                    reason, comparable = "outside_original_phase", False
                elif not available:
                    reason, comparable = "unsupported_features", False
                elif not denominator or denominator < 0:
                    reason, comparable = "nonpositive_notional", False
                else:
                    reason, comparable = None, True
                score = _value(arm, predictions[c.key]) if comparable else None
                sign = None if score is None else int(score > 0) - int(score < 0)
                status = reason or ({None: "unsupported_value", 1: "first_positive_immediate"}.get(sign, "observe_nonpositive"))
                frames.append((c, phase, denominator, status, sign))
            first = next((index for index, row in enumerate(frames) if row[3] == "first_positive_immediate"), None)
            end = first + 1 if first is not None else len(frames)
            traces.extend([e.event_id, c.key, c.frame.decision_offset, phase, status, sign]
                          for c, phase, _, status, sign in frames[:end])
            row = {name: getattr(e, name) for name in ("event_id", "session", "symbol", "cluster_id", "family")}
            if first is not None:
                c, phase, denom, status, _ = frames[first]
                row.update(query_key=c.key, decision_offset=c.frame.decision_offset, phase=phase,
                           denominator=str(denom), action="immediate", reason=status)
            else:
                missing = not frames or any(value[3] in {"reference_unknown", "unsupported_features", "unsupported_value"}
                                            for value in frames)
                row.update(query_key=None, decision_offset=None, phase=None, denominator=None, action="no_commit",
                           reason="unsupported_decision" if missing else "known_abstention")
            choices.append(row)
        expected = {"schema": "ET-immediate-policy-v1", "fold": base.fold, "arm": arm,
            "roster_sha256": sealed.hexdigest(), "predictions_sha256": original._hash(forecasts),
            "choices": choices, "trace": traces}
        original._same(policies[arm], {**expected, "sha256": original._hash(expected)}, "immediate policy")


def _observed(roster, policies, target_records):
    lookup = {c.key: c for c in roster.candidates}
    records = original._map(target_records)
    _check(set(records) == set(lookup), "C evaluation target identities differ")
    rows = {arm: [] for arm in ARMS}
    for arm, ledger in rows.items():
        _check(policies[arm]["arm"] == arm, "mismatched immediate policy")
        for choice in policies[arm]["choices"]:
            key = choice["query_key"]
            if key is None:
                known = choice["reason"] == "known_abstention"
                status, fill, cash = ("no_commit", 0, "0") if known else ("unsupported", None, None)
            else:
                _check(choice["action"] == "immediate" and key in lookup, "fixed immediate decision required")
                c = lookup[key]
                d0 = Fraction(choice["denominator"])
                _check(d0 > 0 and d0 == original._denominator(c), "original denominator differs")
                record = records[key]
                original._target_identity(record, key=key, session=choice["session"], symbol=choice["symbol"],
                    event_id=choice["event_id"], decision=choice["decision_offset"], denominator=d0)
                slot = original._action(record, "immediate", d0, choice["decision_offset"], c.label_query.session_length)
                status, fill, cash = (slot[field] for field in ("status", "fill", "net_cash"))
            ledger.append({**choice, "fill": fill, "net_cash": cash, "status": status})
    return rows


def _partition(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def _counts(rows):
    cash = [Fraction(r["net_cash"]) for r in rows if r["net_cash"] is not None]
    filled_cash = [Fraction(r["net_cash"]) for r in rows if r["fill"] == 1 and r["net_cash"] is not None]
    fills = Counter(r["fill"] for r in rows)
    return dict(episodes=len(rows), commits=sum(r["query_key"] is not None for r in rows), fills=fills[1],
        unknown_fills=fills[None], known_cash=len(cash), unknown_cash=len(rows)-len(cash),
        known_net_cash=str(sum(cash, Fraction())), winning_fills=sum(v > 0 for v in filled_cash),
        losing_fills=sum(v < 0 for v in filled_cash), statuses=dict(Counter(r["status"] for r in rows)))


def _week(session):
    iso = date.fromisoformat(session).isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _comparison(days, ledgers):
    _check(set(ledgers) == set(ARMS) and all(r["session"] in days for arm in ARMS for r in ledgers[arm]), "comparison calendar differs")
    weeks = sorted({_week(day) for day in days})
    result = {}
    for arm in ARMS:
        rows = ledgers[arm]
        by_symbol = _partition(rows, lambda r: r["symbol"])
        by_week = _partition(rows, lambda r: _week(r["session"]))
        counts = _counts(rows)
        weekly = [{"week": week, "fills": _counts(by_week[week])["fills"],
                   "unknown_fills": _counts(by_week[week])["unknown_fills"]} for week in weeks]
        result[arm] = {**counts, "weeks": len(weeks), "weekly": weekly,
            "fills_per_week": str(Fraction(counts["fills"], len(weeks))),
            "weeks_at_least_fifteen": len([row for row in weekly if row["fills"] >= 15]),
            "by_symbol": {symbol: _counts(claims) for symbol, claims in by_symbol.items()}}
    direct, mapped = (sorted(ledgers[arm], key=lambda r: r["event_id"]) for arm in ARMS)
    _check(len(direct) == len(mapped) == len({r["event_id"] for r in direct}), "paired episode count differs")
    paired = {kind: [] for kind in ("same", "added", "removed", "retimed")}
    for left, right in zip(direct, mapped):
        _check(all(left[field] == right[field] for field in ("event_id", "session", "symbol", "cluster_id", "family")),
               "matched original episode differs")
        keys = left["query_key"], right["query_key"]
        if keys[0] == keys[1]:
            kind = "same"
        elif keys[0] is None:
            kind = "added"
        elif keys[1] is None:
            kind = "removed"
        else:
            kind = "retimed"
        paired[kind].append(None if left["net_cash"] is None or right["net_cash"] is None else
                            Fraction(right["net_cash"]) - Fraction(left["net_cash"]))
    transitions = {}
    all_known = []
    for kind, changes in paired.items():
        exact = [change for change in changes if change is not None]
        all_known.extend(exact)
        transitions[kind] = dict(episodes=len(changes), known_pairs=len(exact),
            known_cash_delta=str(sum(exact, Fraction())), better=sum(v > 0 for v in exact), worse=sum(v < 0 for v in exact))
    return {"arms": result, "mapped_minus_direct": {"known_pairs": len(all_known),
        "unknown_pairs": len(mapped) - len(all_known), "known_cash_delta": str(sum(all_known, Fraction())),
        "transitions": transitions}, "claim": "modeled nominal event cash; repeated Discovery"}


def verify_report(calendar, fold, roster, policies, target_records, saved_report):
    days = original._days(calendar, fold)
    rows = _observed(roster, policies, target_records)
    original._same(saved_report, {"schema": "ET-immediate-report-v1", "fold": fold, "calendar": list(days),
        "policy_rows": rows, "policy": _comparison(days, rows)}, "immediate report")


def verify_pool(calendar, reports, saved):
    days, reports = original._days(calendar), tuple(reports)
    _check(len(reports) == 4, "four original folds required")
    for fold, report in enumerate(reports):
        dates = original._days(calendar, fold)
        _check(report["fold"] == fold and report["schema"] == "ET-immediate-report-v1" and report["calendar"] == list(dates),
               "original fold order/calendar differs")
        original._same(report["policy"], _comparison(dates, report["policy_rows"]), "fold aggregate")
    all_rows = {arm: list(chain.from_iterable(report["policy_rows"][arm] for report in reports)) for arm in ARMS}
    expected = {"schema": "ET-immediate-summary-v1", "calendar_days": len(days), "policy": _comparison(days, all_rows),
        "periods": [{"fold": r["fold"], "policy": r["policy"]} for r in reports]}
    original._same(saved, expected, "immediate pooled summary")
