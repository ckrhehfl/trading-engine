"""EJ pre-entry room on sealed EI decisions and the matched saved exits."""

from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
import json


MINUTE = timedelta(minutes=1)
KST = timezone(9 * timedelta(hours=1))
ROOM_BPS = 66
AMOUNTS = ("gross", "cost", "net", "entry_notional")
METADATA = ("status", "stage", "decision_at", "episode_origin_context")
SUPPRESSED = ("feature_unsupported", "question_unsupported", "overlap_suppressed")
TRIGGERS = ("selected_trigger", "entry_at", "request_at", "exit_at", "entry_price", "exit_price",
            "quantity", "trigger_start_at", "trigger_close", "issue")


def _require(condition, reason):
    if not condition:
        raise ValueError(f"EJ room: {reason}")


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            + "\n").encode()


def _clock(value):
    try:
        at = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo is not None and at.second == at.microsecond == 0 else None


def _amount(value, *, canonical=True):
    if type(value) is str:
        try:
            parsed = Fraction(value)
        except (ValueError, ZeroDivisionError):
            return None
        if (not canonical or str(parsed) == value) and parsed > 0:
            return parsed
    return None


def _identity(feature, symbol, day):
    """Recover the original snapshot identity from EI's asdict decision layout."""
    try:
        question = feature["question"]
        (name, anchor), episode = question["episode_id"]
        anchor_fields = [anchor[key] for key in ("start_at", "start_price", "peak_end", "confirmed_at",
                                               "price", "pre_peak_range", "advance", "band")]
        path = question["path"]
        path_fields = [path[key] for key in ("start_at", "end_at", "level", "band", "target", "stop")]
        stage, origin = question["stage"], question["episode_origin_context"]
        clock = feature["forecast_at"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("EJ room: original question identity missing") from exc
    at = _clock(clock)
    _require(name == symbol and type(episode) is int and 0 <= episode <= 72
             and stage in ("approach", "breakout") and origin in ("A", "B", "other")
             and at is not None and at.astimezone(UTC).isoformat() == clock
             and at.astimezone(KST).date() == day and path["start_at"] == clock,
             "original question identity or decision clock differs")
    digest = sha256(_encode([symbol, anchor_fields, episode, stage, path_fields])).hexdigest()
    return digest, {"stage": stage, "decision_at": clock, "episode_origin_context": origin}, at


def _pending(pair, opportunity, at, coordinate):
    """Read and validate only the sealed baseline preparation used by the gate."""
    status = opportunity["status"]
    baseline = pair.get("baseline")
    unavailable = pair.get("baseline_unavailable")
    if status in SUPPRESSED:
        _require(baseline is None, "suppressed question has a baseline preparation")
        return "retain", status, None, None
    if baseline is None and type(unavailable) is str and unavailable:
        return "retain", "unsupported", None, None
    invalid = ("unresolved", "invalid_pre_entry_geometry", None, None)
    if (not isinstance(baseline, dict) or baseline.get("mode") != "baseline"
            or baseline.get("features") != opportunity["feature"]
            or baseline.get("rule_enabled") is not True or baseline.get("cost_bps") != 33
            or unavailable is not None):
        return invalid
    step = baseline.get("step")
    if baseline.get("status") == "abstain" and step is None:
        return "retain", "unsupported", None, None
    if baseline.get("status") != "prepared" or not isinstance(step, dict):
        return invalid

    try:
        episode = step["episode"]
        request = episode["pending"]
        feature = opportunity["feature"]
        question = feature["question"]
        path = question["path"]
        expiry = _clock(path["end_at"])
        opening, closing = (_clock(coordinate.get(key)) for key in ("opening", "closing"))
        visible = baseline["visible_minute"]
        symbol = coordinate["roster"]["symbol"]
        close = _amount(question.get("start_close"))
        target, band, stop = (_amount(path.get(key)) for key in ("target", "band", "stop"))
        if (opening is None or closing is None or expiry is None
                or not opening <= at < expiry <= closing or expiry - at != 30 * MINUTE
                or episode["symbol"] != symbol or _clock(episode["start_at"]) != at
                or _clock(episode["last_at"]) != at or _clock(episode["end_at"]) != expiry
                or episode["cost_bps"] != 33 or episode["entered_at"] is not None
                or episode["closed_at"] is not None or feature["status"] != "ready"
                or question["economic_priority"] is not True or question["unsupported_reasons"] != []
                or visible["symbol"] != symbol or _clock(visible["available_at"]) != at
                or _clock(visible["start_at"]) != at - 3 * MINUTE or visible["price_state"] != "observed"
                or len(visible["ohlc"]) != 4 or close is None
                or _amount(visible["ohlc"][3], canonical=False) != close
                or target is None or band is None or stop is None or not stop < close < target):
            return invalid
        if step["action"] in ("wait", "end", "unresolved") and request is None and episode["attempts"] == 0:
            return "retain", "no_entry", None, None
        cap = _amount(request["bounds"]["max_price"])
        if (step["action"] == request["action"] == "enter" and episode["attempts"] == 1
                and _clock(request["execute_at"]) == at + MINUTE
                and type(request["quantity"]) is int and request["quantity"] > 0
                and cap == min(close + band, target) and _amount(request["bounds"]["stop"]) == stop):
            return "priced", "", target, cap
    except (KeyError, TypeError):
        pass
    return invalid


def _inputs(coordinate):
    roster = coordinate["roster"]
    symbol, day = roster["symbol"], date.fromisoformat(roster["session"])
    _require(type(symbol) is str and bool(symbol) and day.isoformat() == roster["session"],
             "original coordinate identity differs")
    seen = set()
    for opportunity, pair in zip(roster["questions"], coordinate["decisions"], strict=True):
        _require(pair["opportunity"] == opportunity and opportunity["status"] in ("opportunity", *SUPPRESSED),
                 "original opportunity and prepared pair differ")
        identity, metadata, at = _identity(opportunity["feature"], symbol, day)
        _require(identity not in seen, "duplicate original question")
        seen.add(identity)
        yield identity, {**metadata, "status": opportunity["status"]}, _pending(pair, opportunity, at, coordinate)


def selections(coordinate):
    """Select from one admitted sealed EI coordinate before joining outcomes."""
    result = {}
    for identity, metadata, (state, reason, target, cap) in _inputs(coordinate):
        bps = None
        if state == "priced":
            room = 10_000 * (target / cap - 1)
            bps = str(room)
            state, reason = ("select", "at_least_66bp") if room >= ROOM_BPS else ("decline", "below_66bp")
        result[identity] = {**metadata, "state": state, "reason": reason, "room_bps": bps}
    return result


def verify_selections(coordinate, produced):
    """Check identity, support and the threshold with independent cross-products."""
    expected = {}
    for identity, metadata, (status, reason, target, maximum) in _inputs(coordinate):
        bps = None
        if maximum is not None:
            surplus = 10_000 * (target - maximum)
            status, reason = (("decline", "below_66bp") if surplus < 66 * maximum
                              else ("select", "at_least_66bp"))
            bps = str(surplus / maximum)
        expected[identity] = {"state": status, "reason": reason, "room_bps": bps, **metadata}
    _require(_encode(produced) == _encode(expected), "sealed selection differs")


def _bound_questions(row, choices):
    roster = row["roster"]
    _require(row["entry_independently_verified"] is True and type(row["fold"]) is int and row["fold"] in range(1, 6)
             and all(row[key] == roster[key] for key in ("session", "symbol", "coordinate_status", "original_frames")),
             "saved exit coordinate differs")
    questions = row["questions"]
    keys = {q["question_sha256"] for q in questions}
    _require(len(keys) == len(questions) and keys == set(choices), "original and selected question identities differ")
    for question in questions:
        choice = choices[question["question_sha256"]]
        _require("room_selection" not in question and all(question[key] == choice[key] for key in METADATA),
                 "original and selected question metadata differs")
        base, exit_arm = question["baseline"], question["candidate"]
        state = choice["state"]
        if state == "retain":
            _require(base["status"] == exit_arm["status"] == choice["reason"],
                     "retained outcome differs from the sealed non-entry")
        else:
            _require(question["status"] == "opportunity" and state in ("select", "decline", "unresolved")
                     and (state == "unresolved" or base["status"] in ("closed", "no_fill", "unresolved")),
                     "pending selection differs from saved entry")
        yield question, choice, base, exit_arm


def project(row, choices, *, choice_field="room_selection"):
    """Gate both exit arms on the same roster without changing reservations."""
    questions = []
    for question, choice, base, exit_arm in _bound_questions(row, choices):
        state = choice["state"]
        if state in ("decline", "unresolved"):
            rejected = state == "decline"
            status = "no_entry" if rejected else "unresolved"
            amounts = dict.fromkeys(AMOUNTS, "0" if rejected else None)
            base = {"status": status, **amounts, "reason": choice["reason"], "fill": None, "holding": None}
            exit_arm = {"status": status, **amounts, **dict.fromkeys(TRIGGERS),
                        "selected_trigger": "retained" if rejected else "unknown",
                        "issue": None if rejected else choice["reason"]}
        identity = {key: question[key] for key in ("question_sha256", *METADATA)}
        questions.append({**identity, "baseline": base, "candidate": exit_arm, choice_field: choice})
    return {**row, "questions": questions}


def verify_projection(row, choices, produced, *, choice_field="room_selection"):
    """Check original payoff preservation, zero or unknown outcomes, and row identity."""
    _require(set(row) == set(produced) and all(_encode(row[key]) == _encode(produced[key])
             for key in row.keys() - {"questions"}), "projected coordinate differs")
    for (original, choice, base, exit_arm), saved in zip(_bound_questions(row, choices), produced["questions"], strict=True):
        keys = ("question_sha256", *METADATA)
        _require(set(saved) == {*keys, "baseline", "candidate", choice_field}
                 and _encode(saved[choice_field]) == _encode(choice)
                 and all(_encode(saved[key]) == _encode(original[key])
                         for key in keys), "projected question differs")
        state = choice["state"]
        if state in ("select", "retain"):
            _require(_encode(saved["baseline"]) == _encode(base) and _encode(saved["candidate"]) == _encode(exit_arm),
                     "retained payoff differs")
            continue
        expected_status, amount, trigger, issue = (("no_entry", "0", "retained", None) if state == "decline"
                                                   else ("unresolved", None, "unknown", choice["reason"]))
        left, right = saved["baseline"], saved["candidate"]
        _require(set(left) == {"status", "reason", "fill", "holding", *AMOUNTS}
                 and set(right) == {"status", *AMOUNTS, *TRIGGERS}
                 and left["status"] == right["status"] == expected_status
                 and all(left[key] == right[key] == amount for key in AMOUNTS)
                 and left["fill"] is left["holding"] is None and left["reason"] == choice["reason"]
                 and right["selected_trigger"] == trigger and right["issue"] == issue
                 and all(right[key] is None for key in TRIGGERS if key not in ("selected_trigger", "issue")),
                 "gated payoff differs")


def invented_decision(row, roster):
    """Build an invented sealed decision and assign its identities to an invented exit row."""
    symbol, day = roster["symbol"], roster["session"]
    opening = datetime.fromisoformat(day + "T00:00:00+00:00")
    closing = opening + 390 * MINUTE
    questions, opportunities, decisions = [], [], []
    for index, saved in enumerate(row["questions"]):
        at = datetime.fromisoformat(saved["decision_at"]).astimezone(UTC)
        start, end = at.isoformat(), (at + 30 * MINUTE).isoformat()
        variant = (int(symbol) - 100000) % 7
        target = 10065 if variant == 1 else 10066
        level = target - 80
        stop = level - 40
        anchor = {"start_at": (at - 15 * MINUTE).isoformat(), "start_price": str(level - 100),
                  "peak_end": (at - 10 * MINUTE).isoformat(), "confirmed_at": (at - 5 * MINUTE).isoformat(),
                  "price": str(level), "pre_peak_range": "40", "advance": "100", "band": "10"}
        path = {"start_at": start, "end_at": end, "level": str(level), "band": "10",
                "target": str(target), "stop": str(stop)}
        context = saved["episode_origin_context"]
        question = {"episode_id": [[symbol, anchor], index], "stage": "breakout", "path": path,
                    "start_close": "9990", "episode_origin_context": context, "episode_origin_at": start,
                    "question_time_context": context, "phase": "invented", "unsupported_reasons": [],
                    "economic_priority": True, "overlapping_question_ids": []}
        feature = {"question": question, "forecast_at": start, "status": "ready"}
        opportunity = {"feature": feature, "status": saved["status"], "reasons": [], "reserved_by": None}
        identity, metadata, _ = _identity(feature, symbol, opening.date())
        questions.append({**saved, **metadata, "question_sha256": identity})
        opportunities.append(opportunity)

        baseline, unavailable = None, None
        if saved["status"] == "opportunity":
            state = saved["baseline"]["status"]
            if state == "unsupported":
                unavailable = "causal_close_missing"
            else:
                pending = None
                if state != "no_entry":
                    bounds = {"stop": str(stop), "max_price": "10000"}
                    if variant == 2:
                        del bounds["max_price"]
                    pending = {"action": "enter", "execute_at": (at + MINUTE).isoformat(), "quantity": 1,
                               "bounds": bounds}
                episode = {"symbol": symbol, "start_at": start, "last_at": start, "end_at": end,
                           "attempts": int(pending is not None), "entered_at": None, "closed_at": None,
                           "cost_bps": 33, "pending": pending}
                visible = {"symbol": symbol, "start_at": (at - 3 * MINUTE).isoformat(), "available_at": start,
                           "price_state": "observed", "ohlc": ["9990"] * 4}
                baseline = {"mode": "baseline", "features": feature, "status": "prepared", "cost_bps": 33,
                            "rule_enabled": True, "visible_minute": visible,
                            "step": {"action": "enter" if pending is not None else "wait", "episode": episode}}
        decisions.append({"opportunity": opportunity, "baseline": baseline, "baseline_unavailable": unavailable})
    invented_roster = {**roster, "questions": opportunities}
    coordinate = {"roster": invented_roster, "opening": opening.isoformat(),
                  "closing": closing.isoformat(), "decisions": decisions}
    return coordinate, {**row, "roster": invented_roster, "questions": questions}
