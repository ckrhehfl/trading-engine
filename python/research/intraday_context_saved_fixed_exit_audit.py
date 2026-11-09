"""Independently verify an EI fixed-exit projection from three sealed streams.

The decision seal must already be authenticated by the caller. This recount
does not reconstruct a forecast or attest how that seal was produced.
"""

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, timedelta
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_economic_fold import DecisionSeal
from research.intraday_context_economic_saved import _entry
from research.intraday_context_economic_unit import ECONOMIC_SCHEMA, MAXIMUM_LEDGER_BYTES
from research.intraday_scenario_snapshot_unit import BundleSeal, _sealed_input


_DECISIONS = "ei-saved-fixed-exit-decisions.jsonl"
_OUTPUT = "ei-saved-fixed-exit-comparison.jsonl"
_COST = Fraction(33, 10_000)
_COORDINATE = ("fold", "session", "symbol", "coordinate_status", "original_frames")
_QUESTION = ("question_sha256", "status", "stage", "decision_at", "episode_origin_context")
_MONEY = ("gross", "cost", "net", "entry_notional")
_ARMS = ("baseline", "sequence", "lightgbm")
_CONTRASTS = ((0, 1), (0, 2), (1, 2))
_CONTEXTS = ("A", "B", "other")
_STAGES = ("approach", "breakout")
_INACTIVE = ("feature_unsupported", "question_unsupported", "overlap_suppressed")
_ZERO_PAYOFF = (Fraction(),) * 4


def _require(condition, message):
    wire.require(condition, "EI saved fixed-exit audit " + message)


def _equal(actual, expected, message):
    _require(wire.encode(actual) == wire.encode(expected), message)


@contextmanager
def _read_records(path, pin, line_maximum, checkpoint):
    _require(wire.valid_sha(pin.sha256) and type(pin.size) is int and 0 < pin.size <= MAXIMUM_LEDGER_BYTES,
             "input pin differs")
    digest = sha256()
    with _sealed_input(path) as (source, size):
        _require(size == pin.size, "input size differs")

        def read():
            checkpoint("ei_saved_fixed_exit_audit_record")
            raw = source.readline(line_maximum + 1)
            _require(0 < len(raw) <= line_maximum and raw.endswith(b"\n"), "record missing or exceeds bound")
            digest.update(raw)
            return bridge.canonical(raw)

        yield read
        _require(not source.read(1) and digest.hexdigest() == pin.sha256, "input hash or EOF differs")


def _arm_wire(arm):
    status, reason, filled, payoff = arm
    amounts = (None,) * 4 if payoff is None else tuple(map(str, payoff))
    return {"status": status, "reason": reason, "modeled_fill": filled, **dict(zip(_MONEY, amounts, strict=True))}


def _static(raw, kind):
    if raw is None:
        _require(kind == "opportunity", "inactive question requested an entry")
        return None
    _require(type(raw) is list and len(raw) == 2 and type(raw[1]) is str, "static outcome differs")
    status, reason = raw
    _require((kind == "opportunity" and status in ("no_entry", "unsupported") and bool(reason))
             or (kind in _INACTIVE and status == kind), "static question status differs")
    return status, reason, False, _ZERO_PAYOFF if status == "no_entry" else None


def _question(decision, old, new, symbol):
    _require(set(decision) == set(_QUESTION) | {"static", "baseline_static", "expected_net", "request_sha256"}
             and set(old) == set(_QUESTION) | {"baseline", "selected", "selected_minus_baseline_net", "same_fill_management"},
             "question row schema differs")
    keys = {key: old[key] for key in _QUESTION}
    _equal({key: decision[key] for key in _QUESTION}, keys, "decision question identity differs")
    kind = keys["status"]
    _require(wire.valid_sha(keys["question_sha256"]) and kind in ("opportunity", *_INACTIVE)
             and keys["stage"] in _STAGES and keys["episode_origin_context"] in _CONTEXTS, "question scope differs")
    prior_arms = []
    for name in ("baseline", "selected"):
        original = old[name]
        status, filled, payoff = _entry(original, _COST, symbol)
        _require(type(original["reason"]) is str, "original reason differs")
        prior_arms.append((status, original["reason"], filled, payoff))
    baseline, sequence = prior_arms
    paired = None if baseline[3] is None or sequence[3] is None else str(sequence[3][2] - baseline[3][2])
    _equal(old["selected_minus_baseline_net"], paired, "original paired delta differs")
    base_static = _static(decision["baseline_static"], kind)
    terminal = ("closed", "no_fill", "unresolved")
    _require((base_static is None and baseline[0] in terminal) or baseline == base_static,
             "baseline decision and outcome differ")
    if kind != "opportunity" or sequence[0] in terminal:
        _require(sequence == baseline, "original shared fixed exit differs")
    else:
        _require(sequence[0] in ("no_entry", "unsupported"), "original selected status differs")

    alternate = _static(decision["static"], kind)
    value = decision["expected_net"]
    if value is not None:
        _require(type(value) is str and 0 < len(value) <= 8192 and str(Fraction(value)) == value,
                 "expected value is not an exact fraction")
    request = decision["request_sha256"]
    if alternate is None:
        _require(base_static is None and wire.valid_sha(request) and value is not None and Fraction(value) > 0,
                 "fixed exit reuse differs from sealed decision")
        alternate = baseline
    else:
        _require(request is None, "static outcome has a pending request")
    arms = baseline, sequence, alternate
    _equal(new, {**keys, **{name: _arm_wire(arm) for name, arm in zip(_ARMS, arms, strict=True)}},
           "projected question row differs")
    return keys, arms


def _arm_stats(arms):
    status_counts = Counter(arm[0] for arm in arms)
    fills = sum(arm[2] for arm in arms)
    closed = [arm[3] for arm in arms if arm[0] == "closed"]
    totals = [sum((amounts[index] for amounts in closed), Fraction()) for index in range(4)]
    gross, _, net, basis = totals
    closed_count = len(closed)
    result = {"status_counts": dict(status_counts), "known_opportunities": sum(arm[3] is not None for arm in arms),
              "modeled_fills": fills, "resolved_fills": closed_count, "fills_with_unknown_outcome": fills - closed_count,
              "positive_net_fills": sum(amounts[2] > 0 for amounts in closed),
              **{"closed_" + key: str(total) if closed else None for key, total in zip(
                  ("gross_pnl", "assumed_cost", "net_pnl", "entry_notional"), totals, strict=True)}}
    result["mean_closed_trade_net_pnl"] = str(net / closed_count) if closed else None
    result["mean_closed_trade_net_bps"] = str(sum(10_000 * amounts[2] / amounts[3] for amounts in closed)
                                              / closed_count) if closed else None
    result["closed_fixed_notional_break_even_cost_bps"] = str(10_000 * gross / basis) if closed else None
    return result


def _pairs(rows):
    pairs = []
    for left, right in _CONTRASTS:
        stats = Counter()
        delta = Fraction()
        for arms in rows:
            a, b = arms[left], arms[right]
            if a[3] is None or b[3] is None:
                continue
            a_net, b_net = a[3][2], b[3][2]
            delta += b_net - a_net
            stats.update(paired=1, left_fills=int(a[2]), right_fills=int(b[2]), left_wins=int(a_net > 0),
                         captured=int(a_net > 0 and b_net > 0))
        pairs.append({"left": _ARMS[left], "right": _ARMS[right], "both_known_opportunities": stats["paired"],
            "unpaired_opportunities": len(rows) - stats["paired"], "paired_net_delta": str(delta) if stats["paired"] else None,
            "paired_left_modeled_fills": stats["left_fills"], "paired_right_modeled_fills": stats["right_fills"],
            "paired_left_positive_net_fills": stats["left_wins"], "paired_positive_net_fills_captured": stats["captured"]})
    return pairs


def _cohort(rows):
    return {"original_opportunities": len(rows),
            "arms": {name: _arm_stats([row[index] for row in rows]) for index, name in enumerate(_ARMS)},
            "all_arms_known_opportunities": sum(all(arm[3] is not None for arm in row) for row in rows),
            "paired": _pairs(rows)}


def _audit_report(days, contexts, stages, coordinate_states, question_states):
    all_rows, daily = [], []
    weeks = defaultdict(lambda: {"sessions": 0, "original_coordinates": 0, "original_opportunities": 0,
                                 "modeled_fills": dict.fromkeys(_ARMS, 0)})
    for session, (coordinates, rows) in days.items():
        all_rows.extend(rows)
        day = date.fromisoformat(session)
        bucket = weeks[(day - timedelta(days=day.weekday())).isoformat()]
        fills = {name: sum(row[index][2] for row in rows) for index, name in enumerate(_ARMS)}
        bucket["sessions"] += 1
        bucket["original_coordinates"] += coordinates
        bucket["original_opportunities"] += len(rows)
        for name, count in fills.items():
            bucket["modeled_fills"][name] += count
        daily.append({"session": session, "original_coordinates": coordinates, "original_opportunities": len(rows),
                      "modeled_fills": fills, "paired": _pairs(rows)})
    return {"cost_bps": 33, "original_evaluation_sessions": len(days),
            "original_evaluation_coordinates": sum(count for count, _ in days.values()),
            "coordinate_statuses": dict(coordinate_states), "question_statuses": dict(question_states),
            "overall": _cohort(all_rows), "by_episode_origin": {key: _cohort(contexts[key]) for key in _CONTEXTS},
            "by_stage": {key: _cohort(stages[key]) for key in _STAGES},
            "weekly": [{"week": week, **bucket} for week, bucket in weeks.items()], "date_contributions": daily,
            "claim": "nominal_event_level_discovery"}


def audit_saved_fixed_exit(directory: Path, outcome_path: Path, *, outcome_pin: BundleSeal, decisions: DecisionSeal,
                           output: BundleSeal, binding: dict, expected_coordinates: tuple[tuple[int, str, str], ...],
                           checkpoint) -> dict:
    """Verify full pinned predecessor, sealed decision, projection and report bytes."""
    _require(type(outcome_pin) is type(output) is BundleSeal and type(decisions) is DecisionSeal
             and type(expected_coordinates) is tuple and bool(expected_coordinates)
             and len(expected_coordinates) == decisions.coordinates
             and expected_coordinates == tuple(sorted(set(expected_coordinates))), "coordinate roster differs")
    _require(type(binding) is dict and set(binding) == {"schema", "scenario", "snapshot", "forecast",
             "original_decisions", "original_ledger", "cost_bps", "replacement"}, "external decision binding shape differs")
    _equal([binding["schema"], binding["scenario"], binding["cost_bps"], binding["replacement"], binding["original_ledger"]],
           ["EI-saved-fixed-exit-decisions-v1", "S2", 33, "context_lightgbm", asdict(outcome_pin)], "decision binding differs")
    header = {"schema": ECONOMIC_SCHEMA, "scenario": "S2", "cost_bps": 33, "snapshot": binding["snapshot"],
              "forecast": binding["forecast"], "entry_decision_seals": binding["original_decisions"]}
    days, contexts, stages = {}, defaultdict(list), defaultdict(list)
    coordinate_states, question_states = Counter(), Counter()
    questions = 0
    with (_read_records(directory / _DECISIONS, decisions, wire.MIB, checkpoint) as read_decision,
          _read_records(outcome_path, outcome_pin, 4 * wire.MIB, checkpoint) as read_old,
          _read_records(directory / _OUTPUT, output, 4 * wire.MIB, checkpoint) as read_new):
        _equal(read_decision(), binding, "decision header differs")
        original_header = read_old()
        _equal({key: original_header[key] for key in header}, header, "original ledger header differs")
        _equal(read_new(), {"schema": "EI-saved-fixed-exit-comparison-v1", "decision_seal": asdict(decisions),
                           "predecessor": binding}, "comparison header differs")
        previous = None
        for fold, session, symbol in expected_coordinates:
            _require(previous is None or (previous[0] == fold and previous[1] <= session) or previous[1] < session,
                     "session crosses folds")
            previous = fold, session
            decision, old, new = read_decision(), read_old(), read_new()
            _equal([old[key] for key in _COORDINATE[:3]], [fold, session, symbol], "original coordinate differs")
            identity = {key: old[key] for key in _COORDINATE}
            _equal({key: decision[key] for key in _COORDINATE}, identity, "decision coordinate differs")
            _equal({key: new[key] for key in _COORDINATE}, identity, "comparison coordinate differs")
            coordinate_fields = set(_COORDINATE) | {"questions"}
            _require(old["entry_independently_verified"] is True and set(decision) == set(new) == coordinate_fields
                     and set(old) == coordinate_fields | {"entry_independently_verified", "modeled_opens"},
                     "predecessor audit or comparison coordinate schema differs")
            lists = decision["questions"], old["questions"], new["questions"]
            _require(all(type(rows) is list and len(rows) == len(lists[0]) for rows in lists), "question roster differs")
            count, rows = days.get(session, (0, []))
            days[session] = count + 1, rows
            coordinate_states[identity["coordinate_status"]] += 1
            seen = set()
            for causal, before, after in zip(*lists, strict=True):
                key, arms = _question(causal, before, after, symbol)
                digest = key["question_sha256"]
                _require(digest not in seen, "duplicate question")
                seen.add(digest)
                questions += 1
                question_states[key["status"]] += 1
                if key["status"] == "opportunity":
                    rows.append(arms)
                    contexts[key["episode_origin_context"]].append(arms)
                    stages[key["stage"]].append(arms)
        _equal(read_old(), {"records": len(expected_coordinates)}, "original ledger trailer differs")
        report = _audit_report(days, contexts, stages, coordinate_states, question_states)
        _equal(read_new(), {"records": len(expected_coordinates), "report": report}, "independent report differs")
    return {"verified": True, "full_eof_verified": True, "coordinates": len(expected_coordinates), "questions": questions,
            "decision_sha256": decisions.sha256, "output_sha256": output.sha256, "report": report}
