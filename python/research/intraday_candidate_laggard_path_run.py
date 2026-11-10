"""Compare two opening paths on the audited 60-minute laggard entries."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_path as path
from research import intraday_candidate_laggard_path_report as report
from research import intraday_candidate_laggard_run as laggard
from research.intraday_candidate_laggard_stability_run import _parent_plans
from research.intraday_candidate_room_saved import _parent
from research.intraday_ed_inputs import MINUTE


RANK_UNIT = "EJ-ENTRY-OPENING-LAGGARDS"
HOLD_UNIT = "EJ-EXIT-LAGGARD-HOLD"


def _session(reader, symbol, day, invented, audit):
    session = laggard._project(reader, symbol, day, path.READY_OFFSETS, invented, audit)
    if not invented:
        return session

    # Keep the invented rank endpoints fixed and vary only the new midpoint.
    weekday = date.fromisoformat(day).weekday()
    midpoint = session.opening + 29 * MINUTE
    minutes = dict(session.minutes)
    if weekday == 1:
        del minutes[midpoint]
    elif weekday in (0, 3):
        minute = minutes[midpoint]
        opened, high, low, close = minute.ohlc
        close += Decimal(-15 if weekday == 0 else 15)
        minutes[midpoint] = replace(minute, ohlc=(opened, max(high, close), min(low, close), close))
    return replace(session, minutes=minutes)


def _arm(baseline, decision, expected):
    state = decision["state"]
    if state in ("retain", expected):
        return baseline
    if state == "unresolved":
        return laggard._inactive_arm("unresolved", decision["reason"])
    wire.require(state in ("rebound", "weak"), "EJ laggard path choice differs")
    return laggard._inactive_arm("no_entry", "opening_path_other_partition")


def _decisions(spec, scenarios, slot, invented, root, sessions, publication, audit_receipt, audit):
    classify = path.reference_classify if audit else path.classify
    release = spec["laggard_parent"]
    attempt = release["qualification" if invented else "actual"]["attempt"]
    rank_slot = root / "output" / f"{RANK_UNIT}-{attempt}"
    original, _ = _parent(release, RANK_UNIT, root, invented, publication, audit_receipt, load_outputs=False)
    decisions, seals = {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        cache = {}
        for scenario in scenarios:
            days, coordinates = [], {}
            source = _parent_plans(rank_slot / f"{scenario}-pre-entry.jsonl", original[scenario]["selection_seal"])
            for day, fold, plan in source:
                at = plan["decision_at"]
                clock = datetime.fromisoformat(at)
                paths = {}
                for symbol, entry in plan["symbols"].items():
                    key = (day, symbol)
                    session = None
                    if path.ready(entry):
                        if key not in cache:
                            cache[key] = _session(reader, symbol, day, invented, audit)
                        session = cache[key]
                    checked = classify(symbol, clock, entry, session)
                    paths[symbol] = checked
                    coordinates[key] = (fold, at, entry, checked)
                days.append((day, fold, {"decision_at": at, "symbols": paths}))
            wire.require(len(coordinates) == 3140, "EJ laggard path original coordinate extent differs")
            decisions[scenario] = coordinates
            seals[scenario] = laggard._seal(slot, scenario, days, audit)
    return decisions, seals


def calculate(spec, parents, slot, invented, root, sessions, publication, audit_receipt, *, audit=False, stored=None):
    """Seal both scenarios' decisions before opening the audited payoff ledger."""
    scenarios = tuple(item["scenario"] for item in parents["inputs"])
    wire.require(len(scenarios) == 2 and set(scenarios) == {"S1", "S2"}, "EJ laggard path scenarios differ")
    decisions, seals = _decisions(spec, scenarios, slot, invented, root, sessions, publication, audit_receipt, audit)
    earlier, prior = _parent(spec["hold_parent"], HOLD_UNIT, root, invented, publication, audit_receipt)
    reports, outputs = {}, {}
    for scenario in scenarios:
        rows = prior[scenario]
        coordinates = decisions[scenario]
        wire.require(len(rows) == len(coordinates), "EJ laggard path parent extent differs")
        persisted = stored[scenario] if audit else [None] * len(rows)
        result = []
        for row, saved in zip(rows, persisted, strict=True):
            question, = row["questions"]
            selected = coordinates.pop((row["session"], row["symbol"]), None)
            wire.require(selected is not None, "EJ laggard path parent coordinate differs")
            fold, at, entry, checked = selected
            wire.require(row["fold"] == fold and question["decision_at"] == at
                         and wire.encode(question["rank_decision"]) == wire.encode(entry),
                         "EJ laggard path original rank decision differs")
            baseline = question["candidate"]
            changed = {**question, "baseline": baseline, "candidate": _arm(baseline, checked, "rebound"),
                       "weak": _arm(baseline, checked, "weak"), "path": checked}
            rebuilt = {**row, "questions": [changed]}
            if audit:
                wire.require(wire.encode(rebuilt) == wire.encode(saved), "EJ laggard path audited trade differs")
            result.append(rebuilt)

        contrasts = report.summarize(result, audit=audit)
        control = earlier[scenario]["hold30_vs_hold60"]["comparison"]["groups"]["all"]["totals"]["candidate"]
        for name in ("hold60_vs_rebound", "hold60_vs_weak"):
            comparison = contrasts[name]["comparison"]
            wire.require(tuple(comparison[k] for k in ("coordinates", "sessions", "weeks")) == (3140, 157, 34)
                         and comparison["groups"]["all"]["totals"]["baseline"] == control,
                         "EJ laggard path control or evaluation geometry differs")
        reports[scenario] = {"path_seal": seals[scenario], **contrasts}
        outputs[scenario] = result
    return reports, outputs
