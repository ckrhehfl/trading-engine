"""Seal fresh opening ranks before projecting executable-price outcomes."""

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import groupby

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_exit as cash_summary, intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_report as support
from research import intraday_candidate_laggard_selection as selection
from research import intraday_context_cost_census_inputs as saved
from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_ed_snapshot_verify import reconstruct_reference_session
from research.intraday_pattern_calibration import session_close, session_open


PRE_ENTRY_OFFSETS = (0, *range(45, 60))
TRADE_OFFSETS = tuple(range(63, 94))


def _rosters(root, parent, invented):
    if invented:
        return [(row["fold"], roster) for row, roster in saved.invented_rows()]
    _, _, cache = saved._directories(root, parent)
    result = []
    previous = ("", "")
    previous_day = ""
    for fold, seal in enumerate(parent["decisions"], 1):
        with saved._source(cache / f"ei-entry-decisions-fold-{fold}.jsonl", 128 * wire.MIB) as (stream, size):
            rows = saved._Rows(stream)
            first = rows.next()
            header = first["header"]
            wire.require(size == seal["size"] and first["schema"] == "ei-context-entry-decisions-draft-v1"
                         and header["scenario"] == parent["scenario"] and header["cost_bps"] == 33
                         and header["execution_policy_sha256"] == saved.POLICY_SHA
                         and header["coordinates"] == seal["coordinates"] and header["fold"]["index"] == fold,
                         "EJ laggard original roster header differs")
            for _ in range(seal["coordinates"]):
                row = rows.next()["coordinate"]["roster"]
                key = (row["session"], row["symbol"])
                wire.require(key > previous and key[0] > previous_day and saved.WINDOW_START <= key[0] <= saved.WINDOW_END,
                             "EJ laggard original roster order or dates differ")
                previous = key
                roster = {name: row[name] for name in ("session", "symbol", "frame_states")}
                result.append((fold, roster))
            rows.finish(seal)
        previous_day = previous[0]
    return result


def _invented_prices(symbol, day, offsets):
    opening, closing = session_open(day), session_close(day)
    index = int(symbol.removeprefix("KRX:")) - 100000
    weekday = date.fromisoformat(day).weekday()
    limit = Decimal(9950 + index * 10 - (160 if weekday == 0 else 0))
    stop = limit - 30
    minutes = {}
    for offset in offsets:
        if (offset == 59 and weekday == 2 and index < 12) or (offset == 64 and index == 3):
            continue
        at = opening + offset * MINUTE
        start = Decimal(10000) if offset == 0 else limit
        close, low = start, start - 30
        if offset == 63:
            start = close = limit + (1 if index == 1 else -5)
            low = start
        elif offset == 65 and index == 2:
            close = low = stop - 1
        elif offset == 69 and index == 2:
            start = close = low = stop - 10
        elif offset == 93:
            start = close = low = limit + 75
        ohlc = (start, max(start, close), low, close)
        minutes[at] = Minute(symbol, at, at + 3 * MINUTE, ohlc, None, "observed", "missing")
    return SessionInputs(symbol, opening, closing, minutes)


def _project(reader, symbol, day, offsets, invented, audit):
    if invented:
        return _invented_prices(symbol, day, offsets)
    opening = session_open(day)
    starts = tuple(opening + offset * MINUTE for offset in offsets)
    chunk = reader.load_raw_chunk("CC", symbol, day)
    decode = reconstruct_reference_session if audit else sources.reference_session_inputs
    return decode(symbol, day, chunk, None, requested_starts=starts)


def _seal(slot, scenario, days, audit):
    path = slot / f"{scenario}-pre-entry.jsonl"
    raw = b"".join(wire.encode({"session": day, "fold": fold, "plan": plan}) for day, fold, plan in days)
    wire.require(len(days) == 157 and 0 < len(raw) <= 16 * wire.MIB, "EJ laggard selection extent differs")
    expected = wire.digest(raw), len(raw)
    if not audit:
        wire.exclusive_bytes(path, raw)
    wire.require(wire.file_digest(path, 16 * wire.MIB, lambda _: None) == expected,
                 "EJ laggard pre-entry choices differ")
    return {"sha256": expected[0], "size": expected[1], "sessions": len(days)}


def _inactive_arm(status, reason):
    unknown = status == "unresolved"
    return {"status": status, "reason": reason, "fill": None, "selected_trigger": "unknown" if unknown else "retained",
            **dict.fromkeys(("gross", "cost", "net", "entry_notional"), None if unknown else "0")}


def _arm(plan, state, trade):
    if state == "unresolved":
        return _inactive_arm("unresolved", plan["reason"])
    if state == "decline":
        reason = "basket_median_negative" if plan["rank_state"] == "select" else plan["reason"]
        return _inactive_arm("no_entry", reason)
    order = plan["order_state"]
    if order == "ready":
        wire.require(trade is not None, "EJ laggard ready request has no execution result")
        return trade
    wire.require(order in ("decline", "unresolved"), "EJ laggard selection has no order decision")
    return _inactive_arm("no_entry" if order == "decline" else "unresolved", plan["reason"])


def _row(day, fold, roster, plan, trade):
    symbol = roster["symbol"]
    policy = plan["symbols"][symbol]
    question = {"status": "opportunity", "episode_origin_context": "other", "decision_at": plan["decision_at"],
                "rank_decision": policy,
                "baseline": _arm(policy, policy["rank_state"], trade),
                "candidate": _arm(policy, policy["gated_state"], trade)}
    return {"session": day, "symbol": symbol, "fold": fold, "roster": roster, "questions": [question]}


def calculate(spec, parents, slot, invented, root, sessions, *, audit=False, stored=None):
    """Use only sealed dated members and price archive, with distinct audit decoding."""
    select = selection.reference_day if audit else selection.select_day
    execute = execution.reference_trade if audit else execution.execute_trade
    rosters = {parent["scenario"]: _rosters(root, parent, invented) for parent in parents["inputs"]}
    plans, seals, reports, outputs = {}, {}, {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        pre = {}
        for scenario, rows in rosters.items():
            days = []
            for day, group in groupby(rows, key=lambda item: item[1]["session"]):
                members, folds = {}, set()
                for fold, roster in group:
                    symbol = roster["symbol"]
                    folds.add(fold)
                    key = day, symbol
                    if key not in pre:
                        pre[key] = _project(reader, symbol, day, PRE_ENTRY_OFFSETS, invented, audit)
                    members[symbol] = pre[key]
                wire.require(len(members) == 20 and len(folds) == 1, "EJ laggard original date roster differs")
                days.append((day, folds.pop(), select(members)))
            plans[scenario] = {day: plan for day, _, plan in days}
            seals[scenario] = _seal(slot, scenario, days, audit)
        del pre

        # Both decision sets are durable. Only now decode execution and exit prices.
        trade_results = {}
        for scenario, rows in rosters.items():
            result = []
            persisted = stored[scenario] if audit else [None] * len(rows)
            for (fold, roster), previous in zip(rows, persisted, strict=True):
                day, symbol = roster["session"], roster["symbol"]
                plan = plans[scenario][day]
                symbol_plan = plan["symbols"][symbol]
                trade = None
                if symbol_plan["rank_state"] == "select" and symbol_plan["order_state"] == "ready":
                    decision = datetime.fromisoformat(plan["decision_at"])
                    limit, stop = (Fraction(symbol_plan[key]) for key in ("limit", "stop"))
                    key = day, symbol, decision, limit, stop
                    if key not in trade_results:
                        session = _project(reader, symbol, day, TRADE_OFFSETS, invented, audit)
                        trade_results[key] = execute(symbol, decision, limit, stop, session)
                    trade = trade_results[key]
                rebuilt = _row(day, fold, roster, plan, trade)
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(previous), "EJ laggard audited trade differs")
                result.append(rebuilt)
            comparison = cash_summary.summarize(result)
            cash_reference.verify_summary(result, comparison)
            wire.require(tuple(comparison[key] for key in ("coordinates", "sessions", "weeks")) == (3140, 157, 34),
                         "EJ laggard original evaluation geometry differs")
            reports[scenario] = {"selection_seal": seals[scenario],
                                 "selection": support.selection_summary(plans[scenario].values(), audit=audit),
                                 "comparison": comparison}
            outputs[scenario] = result
    return reports, outputs
