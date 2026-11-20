"""Seal midday support-reclaim decisions before projecting executable outcomes."""

from datetime import date
from decimal import Decimal

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_mid_reclaim as selection
from research import intraday_candidate_mid_reclaim_report as report
from research.intraday_candidate_open_strength_run import _replay
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_pattern_calibration import session_close, session_open


TRADE_OFFSETS = tuple(range(123, 154))


def _invented_prices(symbol, day, offsets):
    opening, closing = session_open(day), session_close(day)
    member = int(symbol.removeprefix("KRX:")) - 100000
    missing = {(3, 125), (5, 117), (6, 117), (7, 115), (8, 59), (11, 114),
               (12, 119), (14, 123), (15, 153), (18, 118)}
    expiry_open = 10130 if date.fromisoformat(day).weekday() in (0, 3) else 10060
    minutes = {}
    for offset in offsets:
        if (member, offset) in missing:
            continue
        start = opening + offset * MINUTE
        opened = close = 10040
        if offset == 114:
            opened = close = {9: 10000, 19: 10005}.get(member, 9975)
        elif offset == 117 and member in (4, 17):
            opened = close = 10000 if member == 4 else 9995
        elif offset == 118 and member == 6:
            opened = close = 10000
        elif offset == 119:
            opened = close = 10000 if member == 10 else 10050
        elif offset == 123:
            opened = close = {0: 10050, 1: 10051, 13: 9950}.get(member, 10040)
        elif (member, offset) in ((2, 125), (16, 149)):
            close = 9950
        elif (member, offset) == (2, 129):
            opened = close = 9940
        elif offset == 153:
            opened = close = expiry_open
        low = {30: 10000, 114: 9950}.get(offset, min(opened, close) - 5)
        prices = tuple(map(Decimal, (opened, max(opened, close), low, close)))
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
        wire.require(entry["rank_state"] == "select", "EJ mid-reclaim changed a non-selected entry")
        if entry["gated_state"] == "unresolved":
            candidate = laggard._inactive_arm("unresolved", "reclaim_hold_close_unavailable")
        else:
            wire.require(entry["gated_state"] == "decline", "EJ mid-reclaim hold gate differs")
            candidate = laggard._inactive_arm("no_entry", "reclaim_hold_not_above_support")
    question = {"status": "opportunity", "episode_origin_context": "other", "decision_at": plan["decision_at"],
                "rank_decision": entry, "baseline": baseline, "candidate": candidate}
    return {"session": day, "symbol": roster["symbol"], "fold": fold, "roster": roster, "questions": [question]}


def calculate(spec, parents, slot, invented, root, sessions, *, audit=False, stored=None):
    """Replay both sealed policies with the established +122 decision and 30-minute hold."""
    select = selection.reference_day if audit else selection.select_day
    execute = execution.reference_shock_trade if audit else execution.execute_shock_trade
    return _replay(spec, parents, slot, invented, root, sessions, select=select, project=_project,
                   entry_offsets=selection.ENTRY_OFFSETS, build_row=_row, summarize=report.summarize,
                   label="EJ mid-reclaim", audit=audit, stored=stored, execute=execute, trade_offsets=TRADE_OFFSETS)
