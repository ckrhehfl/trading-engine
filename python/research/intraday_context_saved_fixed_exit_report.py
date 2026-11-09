"""Join already sealed EI decisions to verified fixed exits and compare three arms."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from fractions import Fraction

from research.intraday_campaign_readiness import _week
from research.intraday_campaign_wsl_transport import encode, require, valid_sha
from research.intraday_context_economic_saved import _amount, _entry


_COORDINATE = ("fold", "session", "symbol", "coordinate_status", "original_frames")
_QUESTION = ("question_sha256", "status", "stage", "decision_at", "episode_origin_context")
_MONEY = ("gross", "cost", "net", "entry_notional")
_ARMS = ("baseline", "sequence", "lightgbm")
_CONTRASTS = (("baseline", "sequence"), ("baseline", "lightgbm"), ("sequence", "lightgbm"))
_CONTEXTS = ("A", "B", "other")
_STAGES = ("approach", "breakout")
_INACTIVE = ("feature_unsupported", "question_unsupported", "overlap_suppressed")
_COST = Fraction(33, 10_000)


def _normalize(status, reason, filled, payoff):
    require(type(reason) is str, "EI fixed exit reason differs")
    values = (None,) * 4 if payoff is None else tuple(map(str, payoff))
    return {"status": status, "reason": reason, "modeled_fill": filled, **dict(zip(_MONEY, values, strict=True))}


def _expected(value):
    if value is None:
        return None
    # Probability-weighted expected payoffs can have much larger denominators
    # than the currency amounts in the original realized ledger.
    require(type(value) is str and 0 < len(value) <= 8192, "EI fixed exit exact expected value required")
    amount = Fraction(value)
    require(str(amount) == value, "EI fixed exit noncanonical expected value")
    return amount


def _static(value, opportunity):
    if value is None:
        require(opportunity == "opportunity", "EI fixed exit request on an inactive question")
        return None
    require(type(value) is list and len(value) == 2, "EI fixed exit static state shape differs")
    status, reason = value
    allowed = ("no_entry", "unsupported") if opportunity == "opportunity" else (opportunity,)
    require(status in allowed and (opportunity != "opportunity" or bool(reason)), "EI fixed exit static state differs")
    return _normalize(status, reason, False, (Fraction(),) * 4 if status == "no_entry" else None)


def join_saved_fixed_exit_row(decisions: dict, original: dict) -> dict:
    """Require the same ordered questions and retain the two original arms verbatim in meaning."""
    identity = {key: decisions[key] for key in _COORDINATE}
    require(encode(identity) == encode({key: original[key] for key in _COORDINATE})
            and original["entry_independently_verified"] is True, "EI fixed exit coordinate or original audit differs")
    selected, previous = decisions["questions"], original["questions"]
    require(type(selected) is list and type(previous) is list and len(selected) == len(previous),
            "EI fixed exit original question count differs")
    joined, seen = [], set()
    for decision, saved in zip(selected, previous, strict=True):
        question = {key: decision[key] for key in _QUESTION}
        digest, opportunity = question["question_sha256"], question["status"]
        require(encode(question) == encode({key: saved[key] for key in _QUESTION})
                and valid_sha(digest) and digest not in seen and opportunity in ("opportunity", *_INACTIVE)
                and question["stage"] in _STAGES and question["episode_origin_context"] in _CONTEXTS,
                "EI fixed exit original question identity differs")
        seen.add(digest)
        baseline_raw, sequence_raw = saved["baseline"], saved["selected"]
        base_status, base_filled, base_payoff = _entry(baseline_raw, _COST, identity["symbol"])
        seq_status, seq_filled, seq_payoff = _entry(sequence_raw, _COST, identity["symbol"])
        old_delta = None if base_payoff is None or seq_payoff is None else str(seq_payoff[2] - base_payoff[2])
        require(saved["selected_minus_baseline_net"] == old_delta, "EI fixed exit original paired delta differs")
        baseline = _normalize(base_status, baseline_raw["reason"], base_filled, base_payoff)
        sequence = _normalize(seq_status, sequence_raw["reason"], seq_filled, seq_payoff)
        baseline_static = _static(decision["baseline_static"], opportunity)
        if baseline_static is None:
            require(base_status in ("closed", "no_fill", "unresolved"), "EI fixed exit original baseline lacks terminal state")
        else:
            require(baseline == baseline_static, "EI fixed exit original baseline static outcome differs")
        if opportunity != "opportunity":
            require(sequence == baseline, "EI fixed exit original sequence on an inactive question differs")
        elif seq_status in ("closed", "no_fill", "unresolved"):
            require(sequence == baseline, "EI fixed exit original sequence terminal outcome differs from baseline")
        else:
            require(seq_status in ("no_entry", "unsupported"), "EI fixed exit original sequence active outcome differs")
        alternate = _static(decision["static"], opportunity)
        expected, request = _expected(decision["expected_net"]), decision["request_sha256"]
        if alternate is None:
            require(baseline_static is None and valid_sha(request) and expected is not None and expected > 0,
                    "EI fixed exit reuse requires a positive, identical original request")
            alternate = baseline
        else:
            require(request is None, "EI fixed exit static decision has a request")
        joined.append({**question, "baseline": baseline, "sequence": sequence, "lightgbm": alternate})
    return {**identity, "questions": joined}


def _arm_report(rows, arm):
    arms = [row[arm] for row in rows]
    closed = [tuple(_amount(row[key]) for key in _MONEY) for row in arms if row["status"] == "closed"]
    fills = sum(row["modeled_fill"] for row in arms)
    require(len(closed) <= fills and all(basis > 0 and gross - cost == net and cost == basis * _COST
                                        for gross, cost, net, basis in closed), "EI fixed exit realized basis differs")
    gross, cost, net, basis = (sum((row[i] for row in closed), Fraction()) for i in range(4))
    return {"status_counts": dict(sorted(Counter(row["status"] for row in arms).items())),
            "known_opportunities": sum(row["net"] is not None for row in arms),
            "modeled_fills": fills, "resolved_fills": len(closed), "fills_with_unknown_outcome": fills - len(closed),
            "positive_net_fills": sum(row[2] > 0 for row in closed),
            "closed_gross_pnl": str(gross) if closed else None, "closed_assumed_cost": str(cost) if closed else None,
            "closed_net_pnl": str(net) if closed else None, "closed_entry_notional": str(basis) if closed else None,
            "mean_closed_trade_net_pnl": str(net / len(closed)) if closed else None,
            "mean_closed_trade_net_bps": str(10_000 * sum((row[2] / row[3] for row in closed), Fraction())
                                            / len(closed)) if closed else None,
            "closed_fixed_notional_break_even_cost_bps": str(10_000 * gross / basis) if closed else None}


def _contrast(rows, left, right):
    paired = [(row[left], row[right]) for row in rows if row[left]["net"] is not None and row[right]["net"] is not None]
    gains = 0
    captured = 0
    delta = Fraction()
    for first, second in paired:
        first_net, second_net = _amount(first["net"]), _amount(second["net"])
        delta += second_net - first_net
        if first_net > 0:
            gains += 1
            captured += int(second_net > 0)
    return {"left": left, "right": right, "both_known_opportunities": len(paired),
            "unpaired_opportunities": len(rows) - len(paired), "paired_net_delta": str(delta) if paired else None,
            "paired_left_modeled_fills": sum(first["modeled_fill"] for first, _ in paired),
            "paired_right_modeled_fills": sum(second["modeled_fill"] for _, second in paired),
            "paired_left_positive_net_fills": gains, "paired_positive_net_fills_captured": captured}


def _cohort(rows):
    return {"original_opportunities": len(rows), "arms": {arm: _arm_report(rows, arm) for arm in _ARMS},
            "all_arms_known_opportunities": sum(all(row[arm]["net"] is not None for arm in _ARMS) for row in rows),
            "paired": [_contrast(rows, left, right) for left, right in _CONTRASTS]}


def summarize_saved_fixed_exit(rows: Iterable[dict]) -> dict:
    """Report all original sessions, including dates and weeks with no trade opportunities."""
    original_counts, coordinate_states, question_states = Counter(), Counter(), Counter()
    by_day, by_context, by_stage = defaultdict(list), defaultdict(list), defaultdict(list)
    opportunities = []
    previous = None
    for row in rows:
        day = row["session"]
        key = (row["fold"], day, row["symbol"])
        require(previous is None or (previous < key and previous[1] <= day),
                "EI fixed exit report coordinate order or dates differ")
        require(previous is None or previous[1] != day or previous[0] == key[0],
                "EI fixed exit report date overlaps folds")
        previous = key
        original_counts[day] += 1
        coordinate_states[row["coordinate_status"]] += 1
        for question in row["questions"]:
            question_states[question["status"]] += 1
            if question["status"] == "opportunity":
                opportunities.append(question)
                by_day[day].append(question)
                by_context[question["episode_origin_context"]].append(question)
                by_stage[question["stage"]].append(question)

    require(bool(original_counts), "EI fixed exit original report roster is empty")
    weeks, daily = {}, []
    for day, count in original_counts.items():
        selected = by_day[day]
        week = weeks.setdefault(_week(day), {"sessions": 0, "original_coordinates": 0, "original_opportunities": 0,
                                             "modeled_fills": dict.fromkeys(_ARMS, 0)})
        week["sessions"] += 1
        week["original_coordinates"] += count
        week["original_opportunities"] += len(selected)
        fills = {arm: sum(question[arm]["modeled_fill"] for question in selected) for arm in _ARMS}
        for arm, fill_count in fills.items():
            week["modeled_fills"][arm] += fill_count
        daily.append({"session": day, "original_coordinates": count, "original_opportunities": len(selected),
                      "modeled_fills": fills, "paired": [_contrast(selected, left, right) for left, right in _CONTRASTS]})
    return {"cost_bps": 33, "original_evaluation_sessions": len(original_counts),
            "original_evaluation_coordinates": sum(original_counts.values()),
            "coordinate_statuses": dict(sorted(coordinate_states.items())),
            "question_statuses": dict(sorted(question_states.items())), "overall": _cohort(opportunities),
            "by_episode_origin": {context: _cohort(by_context[context]) for context in _CONTEXTS},
            "by_stage": {stage: _cohort(by_stage[stage]) for stage in _STAGES},
            "weekly": [{"week": key, **value} for key, value in weeks.items()], "date_contributions": daily,
            "claim": "nominal_event_level_discovery"}
