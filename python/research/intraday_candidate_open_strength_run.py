"""Seal opening-strength decisions before projecting executable outcomes."""

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import groupby

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_open_strength as selection
from research import intraday_candidate_open_strength_report as report
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_pattern_calibration import session_close, session_open


TRADE_OFFSETS = tuple(range(63, 124))


def _invented_prices(symbol, day, offsets):
    opening, closing = session_open(day), session_close(day)
    member = int(symbol.removeprefix("KRX:")) - 100000
    weekday = date.fromisoformat(day).weekday()
    strength = member < 6
    pullback = weekday in (0, 3)
    limit = Decimal(10080 if strength else 10000 if pullback else 10050)
    low = limit if member == 4 else limit - 40
    minutes = {}
    for offset in offsets:
        if ((offset == 29 and weekday == 2 and member >= 6)
                or (offset == 55 and member == 5)
                or (offset == 65 and member == 3)):
            continue
        start = opening + offset * MINUTE
        opened = close = limit
        if offset == 0:
            opened = close = Decimal(10000)
        elif offset == 29:
            opened = close = Decimal(10070 if strength else 10030 if pullback else 10000)
        elif offset == 63:
            opened = close = limit + (1 if member == 1 else -5)
        elif offset == 65 and member == 2:
            close = low - 1
        elif offset == 69 and member == 2:
            opened = close = low - 10
        elif offset == 123:
            opened = close = limit + 80
        prices = opened, max(opened, close), min(opened, close, low), close
        minutes[start] = Minute(symbol, start, start + 3 * MINUTE, prices, None, "observed", "missing")
    return SessionInputs(symbol, opening, closing, minutes)


def _project(reader, symbol, day, offsets, invented, audit):
    if invented:
        return _invented_prices(symbol, day, offsets)
    return laggard._project(reader, symbol, day, offsets, False, audit)


def _plans(rosters, reader, slot, select, project, entry_offsets, invented, audit, label):
    pre, plans, seals = {}, {}, {}
    for scenario, rows in rosters.items():
        days = []
        for day, group in groupby(rows, key=lambda item: item[1]["session"]):
            members, folds = {}, set()
            for fold, roster in group:
                symbol = roster["symbol"]
                folds.add(fold)
                key = day, symbol
                if key not in pre:
                    pre[key] = project(reader, symbol, day, entry_offsets, invented, audit)
                members[symbol] = pre[key]
            wire.require(len(members) == 20 and len(folds) == 1, f"{label} original date roster differs")
            plan = select(members)
            wire.require(plan["symbols"].keys() == members.keys(), f"{label} decision roster differs")
            days.append((day, folds.pop(), plan))
        plans[scenario] = {day: plan for day, _, plan in days}
        seals[scenario] = laggard._seal(slot, scenario, days, audit)
    return plans, seals


def _row(day, fold, roster, plan, trade):
    entry = plan["symbols"][roster["symbol"]]
    baseline = laggard._arm(entry, entry["rank_state"], trade)
    candidate = baseline
    if entry["gated_state"] != entry["rank_state"]:
        wire.require(entry["rank_state"] == "select", "EJ open-strength changed a non-selected entry")
        if entry["gated_state"] == "unresolved":
            candidate = laggard._inactive_arm("unresolved", "opening_peer_quorum_unknown")
        else:
            wire.require(entry["gated_state"] == "decline", "EJ open-strength peer gate differs")
            candidate = laggard._inactive_arm("no_entry", "opening_peer_median_not_negative")
    question = {"status": "opportunity", "episode_origin_context": "other", "decision_at": plan["decision_at"],
                "observed_peer_members": plan["observed_peer_members"], "rank_decision": entry,
                "baseline": baseline, "candidate": candidate}
    return {"session": day, "symbol": roster["symbol"], "fold": fold, "roster": roster, "questions": [question]}


def calculate(spec, parents, slot, invented, root, sessions, *, audit=False, stored=None):
    """Reconstruct and seal both rosters' decisions before reading trade prices."""
    select = selection.reference_day if audit else selection.select_day
    return _replay(spec, parents, slot, invented, root, sessions, select=select, project=_project,
                   entry_offsets=selection.ENTRY_OFFSETS, build_row=_row, summarize=report.summarize,
                   label="EJ open-strength", audit=audit, stored=stored)


def _replay(spec, parents, slot, invented, root, sessions, *, select, project, entry_offsets, build_row, summarize,
            label, audit, stored, execute=None, trade_offsets=TRADE_OFFSETS):
    """Run the sealed orders using the supplied execution schedule."""
    if execute is None:
        execute = execution.reference_opening_hold60 if audit else execution.execute_opening_hold60
    wire.require(sorted(parent["scenario"] for parent in parents["inputs"]) == ["S1", "S2"],
                 f"{label} scenarios differ")
    rosters = {parent["scenario"]: laggard._rosters(root, parent, invented) for parent in parents["inputs"]}
    reports, outputs = {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        plans, seals = _plans(rosters, reader, slot, select, project, entry_offsets, invented, audit, label)
        trades = {}
        for scenario, rows in rosters.items():
            result = []
            persisted = stored[scenario] if audit else [None] * len(rows)
            for (fold, roster), previous in zip(rows, persisted, strict=True):
                day, symbol = roster["session"], roster["symbol"]
                plan = plans[scenario][day]
                entry = plan["symbols"][symbol]
                trade = None
                if entry["rank_state"] == "select" and entry["order_state"] == "ready":
                    decision = datetime.fromisoformat(plan["decision_at"])
                    limit, stop = (Fraction(entry[key]) for key in ("limit", "stop"))
                    key = day, symbol, decision, limit, stop
                    if key not in trades:
                        session = project(reader, symbol, day, trade_offsets, invented, audit)
                        trades[key] = execute(symbol, decision, limit, stop, session)
                    trade = trades[key]
                rebuilt = build_row(day, fold, roster, plan, trade)
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(previous), f"{label} audited trade differs")
                result.append(rebuilt)
            contrast = summarize(result, audit=audit)
            comparison = contrast["comparison"]
            wire.require(tuple(comparison[key] for key in ("coordinates", "sessions", "weeks")) == (3140, 157, 34),
                         f"{label} original evaluation geometry differs")
            reports[scenario] = {"selection_seal": seals[scenario], **contrast}
            outputs[scenario] = result
    return reports, outputs
