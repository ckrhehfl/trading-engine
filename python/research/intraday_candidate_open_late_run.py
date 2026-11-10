"""Compare late strength with and without earlier peer breadth."""

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import groupby

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_report as payoff_support
from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_open_late_report as support
from research import intraday_candidate_open_late_selection as selection
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_pattern_calibration import session_close, session_open


def _decision_offset(day):
    return (session_close(day) - session_open(day)) // MINUTE - selection.DECISION_LEAD_MINUTES


def _decision_offsets(day):
    decision = _decision_offset(day)
    return (0, selection.OPENING_LAST_OFFSET, *range(decision - selection.LATE_LOOKBACK_MINUTES - 2, decision - 2))


def _execution_offsets(day):
    decision = _decision_offset(day)
    return tuple(range(decision + 1, decision + 2 + execution.HOLD // MINUTE))


def _invented_prices(symbol, day, offsets):
    opening, closing = session_open(day), session_close(day)
    decision = _decision_offset(day)
    member = int(symbol.removeprefix("KRX:")) - 100000
    weekday = date.fromisoformat(day).weekday()
    limit = Decimal(10040 if member % 4 else 9980)
    stop = limit - 75
    minutes = {}
    for offset in offsets:
        if ((offset == selection.OPENING_LAST_OFFSET and weekday == 2 and member < 12)
                or (offset == decision - 8 and member == 5)
                or (offset == decision + 3 and member == 3)):
            continue
        op = close = limit
        low = stop
        if offset == 0 or offset == decision - 17:
            op = close = Decimal(10000)
        elif offset == selection.OPENING_LAST_OFFSET:
            op = close = Decimal(10066 if weekday in (0, 3) and member < 15 else 10020)
        elif offset == decision + 1:
            op = close = limit + (1 if member == 1 else -5)
        elif offset == decision + 3 and member == 2:
            close = stop - 1
        elif offset == decision + 31:
            op = close = limit + 80
        start = opening + offset * MINUTE
        prices = (op, max(op, close), min(op, close, low), close)
        minutes[start] = Minute(symbol, start, start + 3 * MINUTE, prices, None, "observed", "missing")
    return SessionInputs(symbol, opening, closing, minutes)


def _project(reader, symbol, day, offsets, invented, audit):
    if invented:
        return _invented_prices(symbol, day, offsets)
    return laggard._project(reader, symbol, day, offsets, False, audit)


def _row(day, fold, roster, plan, trade):
    entry = plan["symbols"][roster["symbol"]]
    baseline = laggard._arm(entry, entry["rank_state"], trade)
    candidate = baseline
    if entry["gated_state"] != entry["rank_state"]:
        wire.require(entry["rank_state"] == "select", "EJ open-late changes a non-selected entry")
        if entry["gated_state"] == "unresolved":
            candidate = laggard._inactive_arm("unresolved", "opening_peer_quorum_unknown")
        else:
            wire.require(entry["gated_state"] == "decline", "EJ open-late gate differs")
            candidate = laggard._inactive_arm("no_entry", "opening_peer_return_not_above_threshold")
    question = {"status": "opportunity", "episode_origin_context": "other", "decision_at": plan["decision_at"],
                "observed_opening_members": plan["observed_opening_members"], "rank_decision": entry,
                "baseline": baseline, "candidate": candidate}
    return {"session": day, "symbol": roster["symbol"], "fold": fold, "roster": roster, "questions": [question]}


def _plans(rosters, reader, slot, select, invented, audit):
    pre, plans, seals = {}, {}, {}
    for scenario, rows in rosters.items():
        days = []
        for day, group in groupby(rows, key=lambda item: item[1]["session"]):
            offsets = _decision_offsets(day)
            members, folds = {}, set()
            for fold, roster in group:
                symbol = roster["symbol"]
                folds.add(fold)
                key = day, symbol
                if key not in pre:
                    pre[key] = _project(reader, symbol, day, offsets, invented, audit)
                members[symbol] = pre[key]
            wire.require(len(members) == 20 and len(folds) == 1, "EJ open-late original date roster differs")
            days.append((day, folds.pop(), select(members)))
        plans[scenario] = {day: plan for day, _, plan in days}
        seals[scenario] = laggard._seal(slot, scenario, days, audit)
    return plans, seals


def calculate(spec, parents, slot, invented, root, sessions, *, audit=False, stored=None):
    """Reconstruct the frozen rosters and both entry plans before reading outcomes."""
    select = selection.reference_day if audit else selection.select_day
    execute = execution.reference_open_late_trade if audit else execution.execute_open_late_trade
    rosters = {parent["scenario"]: laggard._rosters(root, parent, invented) for parent in parents["inputs"]}
    reports, outputs = {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        plans, seals = _plans(rosters, reader, slot, select, invented, audit)
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
                        session = _project(reader, symbol, day, _execution_offsets(day), invented, audit)
                        trades[key] = execute(symbol, decision, limit, stop, session)
                    trade = trades[key]
                rebuilt = _row(day, fold, roster, plan, trade)
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(previous), "EJ open-late audited trade differs")
                result.append(rebuilt)
            comparison = cash_summary.summarize(result)
            cash_reference.verify_summary(result, comparison)
            wire.require(tuple(comparison[key] for key in ("coordinates", "sessions", "weeks")) == (3140, 157, 34),
                         "EJ open-late original evaluation geometry differs")
            reports[scenario] = {"selection_seal": seals[scenario],
                                 "selection": support.selection_summary(result, audit=audit),
                                 "execution": payoff_support.execution_summary(result, audit=audit),
                                 "comparison": comparison,
                                 "paired_support_scope": {
                                     "common_known": "execution.totals.common_known includes different known states",
                                     "matched_closed": "comparison.groups.all.totals.paired cash uses only trades closed in both arms",
                                     "legacy_paired_known_outcomes":
                                         "comparison.groups.all.totals.paired.known_outcomes counts identical known states",
                                 }}
            outputs[scenario] = result
    return reports, outputs
