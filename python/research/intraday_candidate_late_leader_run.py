"""Replay the late leader condition on the original dated EJ population."""

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import groupby
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_exit as cash_summary, intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_report as payoff_support
from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_late_leader_report as support
from research import intraday_candidate_late_leader_selection as selection
from research import intraday_context_cost_census_inputs as saved
from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_pattern_calibration import session_close, session_open


def _ranks(spec, root, rosters, invented):
    population = {(scenario, day): {row["symbol"] for _, row in group}
                  for scenario, rows in rosters.items()
                  for day, group in groupby(rows, key=lambda item: item[1]["session"])}
    if invented:
        return {key: {symbol: rank for rank, symbol in enumerate(sorted(symbols), 1)}
                for key, symbols in population.items()}
    pin = spec["universe"]
    relative = Path(pin["relative_path"])
    wire.require(not relative.is_absolute() and ".." not in relative.parts, "EJ leader universe outside native root")
    with saved._source(root / relative, 16 * wire.MIB) as (stream, size):
        raw = stream.read(size + 1)
        wire.require(size == pin["size"] == len(raw), "EJ leader universe extent differs")
        universe = sources.load_universe(raw, pin["sha256"])
    ranks = {key: {} for key in population}
    for item in universe.targets:
        key = item.scenario, item.session
        if key in ranks:
            ranks[key][item.symbol] = item.rank
    wire.require(all(set(ranks[key]) == symbols and set(ranks[key].values()) == set(range(1, 21))
                     for key, symbols in population.items()), "EJ leader original ranked population differs")
    return ranks


def _decision_offset(day):
    return (session_close(day) - session_open(day)) // MINUTE - 76


def _invented_prices(symbol, day, offsets):
    opening, closing = session_open(day), session_close(day)
    decision = _decision_offset(day)
    index = int(symbol.removeprefix("KRX:")) - 100000
    weekday = date.fromisoformat(day).weekday()
    last = Decimal((10090 if weekday in (0, 3) else 10033) if index < 5 else
                   (10020 if index % 4 != 3 else 10050))
    stop = last - 40
    minutes = {}
    for offset in offsets:
        if (weekday == 2 and index < 2 and offset == decision - 10) or (index == 8 and offset == decision + 3):
            continue
        at = opening + offset * MINUTE
        op = Decimal(10000) if offset == decision - 17 else last
        close, low = op, min(op, stop)
        if offset == decision + 1:
            op = close = last + (1 if index == 6 else -5)
            low = min(op, stop)
        elif offset == decision + 3 and index == 9:
            close = low = stop - 1
        elif offset == decision + 31:
            op = close = low = last + 75
        minutes[at] = Minute(symbol, at, at + 3 * MINUTE, (op, max(op, close), low, close), None,
                             "observed", "missing")
    return SessionInputs(symbol, opening, closing, minutes)


def _project(reader, symbol, day, offsets, invented, audit):
    if invented:
        return _invented_prices(symbol, day, offsets)
    return laggard._project(reader, symbol, day, offsets, False, audit)


def _row(day, fold, roster, plan, trade):
    entry = plan["symbols"][roster["symbol"]]
    baseline = laggard._arm(entry, entry["rank_state"], trade)
    if entry["gated_state"] == entry["rank_state"]:
        candidate = baseline
    elif entry["gated_state"] == "unresolved":
        candidate = laggard._inactive_arm("unresolved", "leader_quorum_unknown")
    else:
        wire.require(entry["gated_state"] == "decline" and entry["rank_state"] == "select",
                     "EJ leader gated selection differs")
        candidate = laggard._inactive_arm("no_entry", "leader_return_not_above_threshold")
    question = {"status": "opportunity", "episode_origin_context": "other", "decision_at": plan["decision_at"],
                "leader_state": plan["leader_state"], "rank_decision": entry,
                "baseline": baseline, "candidate": candidate}
    return {"session": day, "symbol": roster["symbol"], "fold": fold, "roster": roster, "questions": [question]}


def calculate(spec, parents, slot, invented, root, sessions, *, audit=False, stored=None):
    """Authenticate original ranks, seal both plans and then decode execution prices."""
    select = selection.reference_day if audit else selection.select_day
    execute = execution.reference_late_trade if audit else execution.execute_late_trade
    rosters = {parent["scenario"]: laggard._rosters(root, parent, invented) for parent in parents["inputs"]}
    ranks = _ranks(spec, root, rosters, invented)
    plans, seals, reports, outputs = {}, {}, {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        pre = {}
        for scenario, rows in rosters.items():
            days = []
            for day, group in groupby(rows, key=lambda item: item[1]["session"]):
                decision = _decision_offset(day)
                offsets = tuple(range(decision - 17, decision - 2))
                members, folds = {}, set()
                for fold, roster in group:
                    symbol = roster["symbol"]
                    folds.add(fold)
                    key = day, symbol
                    if key not in pre:
                        pre[key] = _project(reader, symbol, day, offsets, invented, audit)
                    members[symbol] = pre[key]
                wire.require(len(members) == 20 and len(folds) == 1, "EJ leader original date roster differs")
                days.append((day, folds.pop(), select(members, ranks[scenario, day])))
            plans[scenario] = {day: plan for day, _, plan in days}
            seals[scenario] = laggard._seal(slot, scenario, days, audit)
        del pre

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
                        offset = _decision_offset(day)
                        session = _project(reader, symbol, day, tuple(range(offset + 1, offset + 32)), invented, audit)
                        trades[key] = execute(symbol, decision, limit, stop, session)
                    trade = trades[key]
                rebuilt = _row(day, fold, roster, plan, trade)
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(previous), "EJ leader audited trade differs")
                result.append(rebuilt)
            comparison = cash_summary.summarize(result)
            cash_reference.verify_summary(result, comparison)
            wire.require(tuple(comparison[key] for key in ("coordinates", "sessions", "weeks")) == (3140, 157, 34),
                         "EJ leader original evaluation geometry differs")
            reports[scenario] = {"selection_seal": seals[scenario],
                                 "selection": support.selection_summary(plans[scenario].values(), audit=audit),
                                 "execution": payoff_support.execution_summary(result, audit=audit),
                                 "comparison": comparison}
            outputs[scenario] = result
    return reports, outputs
