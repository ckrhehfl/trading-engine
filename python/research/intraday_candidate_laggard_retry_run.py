"""Retry opening laggards once after a causally observed first cap rejection."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_retry as gate
from research import intraday_candidate_laggard_retry_report as report
from research import intraday_candidate_laggard_run as laggard
from research.intraday_candidate_laggard_stability_run import _parent_plans
from research.intraday_candidate_room_saved import _parent
from research.intraday_ed_inputs import MINUTE


RANK_UNIT = "EJ-ENTRY-OPENING-LAGGARDS"
PATH_UNIT = "EJ-ENTRY-LAGGARD-PATH"
RETRY_TRADE_OFFSETS = tuple(range(68, 124))


def _session(reader, symbol, day, offsets, invented, audit):
    session = laggard._project(reader, symbol, day, offsets, invented, audit)
    if not invented:
        return session
    sample = date.fromisoformat(day).toordinal() % 5
    minutes = dict(session.minutes)
    if offsets == gate.RETRY_GATE_OFFSETS:
        # The invented first open must match the unchanged parent receipt.
        if sample == 0:
            minutes.pop(session.opening + 64 * MINUTE, None)
    else:
        entry = session.opening + 68 * MINUTE
        if sample == 1:
            del minutes[entry]
        elif sample == 2:
            minute = minutes[entry]
            raised = minute.ohlc[0] + Decimal(1)
            minutes[entry] = replace(minute, ohlc=(raised, raised, raised, raised))
        elif sample == 3:
            start = entry + MINUTE
            minute = minutes[start]
            opened, high, low, _ = minute.ohlc
            minutes[start] = replace(minute, ohlc=(opened, high, low, low))
    return replace(session, minutes=minutes)


def _parent_slot(root, unit, release, invented):
    attempt = release["qualification" if invented else "actual"]["attempt"]
    return root / "output" / f"{unit}-{attempt}"


def _decisions(spec, scenarios, slot, invented, root, reader, ranked, pathed, audit):
    classify = gate.reference_classify if audit else gate.classify
    rank_release, path_release = spec["laggard_parent"], spec["path_parent"]
    rank_slot = _parent_slot(root, RANK_UNIT, rank_release, invented)
    path_slot = _parent_slot(root, PATH_UNIT, path_release, invented)
    decisions, seals, cache = {}, {}, {}
    for scenario in scenarios:
        ranks = _parent_plans(rank_slot / f"{scenario}-pre-entry.jsonl", ranked[scenario]["selection_seal"])
        paths = _parent_plans(path_slot / f"{scenario}-pre-entry.jsonl", pathed[scenario]["path_seal"])
        days, coordinates = [], {}
        for (day, fold, rank), (path_day, path_fold, path) in zip(ranks, paths, strict=True):
            at, ranks_by_symbol = rank["decision_at"], rank["symbols"]
            paths_by_symbol = path["symbols"]
            wire.require((day, fold, at) == (path_day, path_fold, path["decision_at"])
                         and ranks_by_symbol.keys() == paths_by_symbol.keys(), "EJ laggard retry original rosters differ")
            decision, choices = datetime.fromisoformat(at), {}
            for symbol, rank_plan in ranks_by_symbol.items():
                path_plan = paths_by_symbol[symbol]
                key, session = (day, symbol), None
                if path_plan["state"] == "weak":
                    wire.require(rank_plan["rank_state"] == "select" and rank_plan["order_state"] == "ready",
                                 "EJ laggard retry weak path lacks an original ready order")
                    if key not in cache:
                        cache[key] = _session(reader, symbol, day, gate.RETRY_GATE_OFFSETS, invented, audit)
                    session = cache[key]
                checked = classify(symbol, decision, rank_plan, path_plan, session)
                choices[symbol] = checked
                coordinates[key] = (fold, at, rank_plan, path_plan, checked)
            retry_at = (decision + execution.DELAY_MINUTES * MINUTE).isoformat()
            days.append((day, fold, {"decision_at": retry_at, "original_decision_at": at, "symbols": choices}))
        wire.require(len(coordinates) == 3140, "EJ laggard retry original coordinate extent differs")
        decisions[scenario] = coordinates
        seals[scenario] = laggard._seal(slot, scenario, days, audit)
    return decisions, seals


def _verify_initial_arm(baseline, checked, rank, decision):
    """Bind the sealed first-price and quantity evidence to the audited first attempt."""
    quantity = checked["quantity"]
    if quantity is None:
        return
    wire.require(type(quantity) is int and quantity >= 0
                 and type(baseline["requested_quantity"]) is int and baseline["requested_quantity"] == quantity,
                 "EJ laggard retry original quantity differs")
    if quantity == 0:
        wire.require(baseline["status"] == "no_fill" and baseline["fill"] is None
                     and baseline["reason"] == "insufficient_risk_or_cash_for_one_unit"
                     and all(baseline[key] is None for key in ("entry_request_at", "entry_execute_at", "entry_price", "entry_at")),
                     "EJ laggard retry original zero-quantity receipt differs")
        return
    requested, initial = decision.isoformat(), (decision + MINUTE).isoformat()
    wire.require((baseline["entry_request_at"], baseline["entry_execute_at"], baseline["entry_price"])
                 == (requested, initial, checked["original_open"]), "EJ laggard retry original entry receipt differs")
    fill = baseline["fill"]
    if checked["original_open"] is None:
        wire.require(baseline["status"] == "unresolved" and baseline["reason"] == "entry_price_unknown"
                     and baseline["unknown_at"] == initial and baseline["entry_at"] is None
                     and fill == {"outcome": "indeterminate", "quantity": 0, "cost": None},
                     "EJ laggard retry original unobserved opening receipt differs")
        return
    price, limit, stop = (Fraction(value) for value in (checked["original_open"], rank["limit"], rank["stop"]))
    if stop < price <= limit:
        wire.require(baseline["status"] in ("closed", "unresolved") and baseline["entry_at"] == initial
                     and isinstance(fill, dict) and fill["outcome"] == "modeled" and fill["quantity"] == quantity,
                     "EJ laggard retry original filled receipt differs")
    else:
        wire.require(baseline["status"] == "no_fill" and baseline["reason"] == "open_outside_frozen_entry_bounds"
                     and baseline["entry_at"] is None and fill == {"outcome": "declined", "quantity": 0, "cost": "0"},
                     "EJ laggard retry original rejection receipt differs")


def _candidate(baseline, checked, rank, decision, symbol, load_session, execute):
    _verify_initial_arm(baseline, checked, rank, decision)
    state = checked["state"]
    if state == "retain":
        return baseline
    if state == "unresolved":
        return laggard._inactive_arm("unresolved", checked["reason"])
    limit, stop = (Fraction(rank[key]) for key in ("limit", "stop"))
    wire.require(state == "retry" and baseline["status"] == "no_fill"
                 and Fraction(checked["original_open"]) > limit and checked["quantity"] > 0,
                 "EJ laggard retry lacks an original upper-limit rejection")
    result = execute(symbol, decision, limit, stop, load_session())
    wire.require(result["requested_quantity"] == baseline["requested_quantity"], "EJ laggard retry order resized")
    return result


def calculate(spec, parents, slot, invented, root, sessions, publication, audit_receipt, *, audit=False, stored=None):
    """Seal both scenarios' retry choices before opening either payoff path."""
    scenarios = tuple(item["scenario"] for item in parents["inputs"])
    wire.require(len(scenarios) == 2 and set(scenarios) == {"S1", "S2"}, "EJ laggard retry scenarios differ")
    execute = execution.reference_retry_trade if audit else execution.execute_retry_trade
    reports, outputs = {}, {}
    ranked, _ = _parent(spec["laggard_parent"], RANK_UNIT, root, invented, publication, audit_receipt, load_outputs=False)
    pathed, _ = _parent(spec["path_parent"], PATH_UNIT, root, invented, publication, audit_receipt, load_outputs=False)
    with sessions(spec, slot, invented, audit) as reader:
        decisions, seals = _decisions(spec, scenarios, slot, invented, root, reader, ranked, pathed, audit)
        original, prior = _parent(spec["path_parent"], PATH_UNIT, root, invented, publication, audit_receipt)
        cache = {}
        for scenario in scenarios:
            rows, coordinates = prior[scenario], decisions[scenario]
            wire.require(len(rows) == len(coordinates), "EJ laggard retry parent extent differs")
            persisted = stored[scenario] if audit else [None] * len(rows)
            result = []
            for row, saved in zip(rows, persisted, strict=True):
                question, = row["questions"]
                day, symbol = row["session"], row["symbol"]
                key = day, symbol
                selected = coordinates.pop(key, None)
                wire.require(selected is not None, "EJ laggard retry parent coordinate differs")
                fold, at, rank, path, checked = selected
                wire.require((row["fold"], question["decision_at"]) == (fold, at)
                             and wire.encode(question["rank_decision"]) == wire.encode(rank)
                             and wire.encode(question["path"]) == wire.encode(path), "EJ laggard retry original decision differs")

                def load_session():
                    if key not in cache:
                        cache[key] = _session(reader, symbol, day, RETRY_TRADE_OFFSETS, invented, audit)
                    return cache[key]

                baseline = question["weak"]
                candidate = _candidate(baseline, checked, rank, datetime.fromisoformat(at), symbol, load_session, execute)
                changed = {**question, "baseline": baseline, "candidate": candidate, "retry": checked}
                rebuilt = {**row, "questions": [changed]}
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(saved), "EJ laggard retry audited trade differs")
                result.append(rebuilt)

            contrast = report.summarize(result, audit=audit)
            comparison = contrast["comparison"]
            control = original[scenario]["hold60_vs_weak"]["comparison"]["groups"]["all"]["totals"]["candidate"]
            wire.require(tuple(comparison[key] for key in ("coordinates", "sessions", "weeks")) == (3140, 157, 34)
                         and comparison["groups"]["all"]["totals"]["baseline"] == control,
                         "EJ laggard retry control or evaluation geometry differs")
            reports[scenario] = {"retry_seal": seals[scenario], **contrast}
            outputs[scenario] = result
    return reports, outputs
