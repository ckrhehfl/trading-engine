"""Seal opening-pullback decisions before projecting executable outcomes."""

from decimal import Decimal

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_open_pullback as selection
from research import intraday_candidate_open_pullback_report as report
from research.intraday_candidate_open_strength_run import _replay
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_pattern_calibration import session_close, session_open


def _invented_prices(symbol, day, offsets):
    opening, closing = session_open(day), session_close(day)
    member = int(symbol.removeprefix("KRX:")) - 100000
    mid = Decimal({0: 10066, 9: 10065}.get(member, 10100 if member < 11 else 10040))
    limit = Decimal({0: 10033, 8: 10049, 10: 10100}.get(member, 10065 if member < 11 else 10020))
    floor = limit if member == 7 else limit - 40
    minutes = {}
    for offset in offsets:
        if (member, offset) in ((3, 65), (5, 54), (6, 55), (11, 29)):
            continue
        start = opening + offset * MINUTE
        opened = close = limit
        if offset == 0:
            opened = close = Decimal(10000)
        elif offset == 29:
            opened = close = mid
        elif offset == 54 and member not in (4, 6, 7):
            opened = close = limit - 5
        elif offset == 63:
            opened = close = limit + (1 if member == 1 else -5)
        elif offset == 65 and member == 2:
            close = floor - 1
        elif offset == 69 and member == 2:
            opened = close = floor - 10
        elif offset == 123:
            opened = close = limit + 80
        prices = opened, max(opened, close), min(opened, close, floor), close
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
        wire.require(entry["rank_state"] == "select", "EJ open-pullback changed a non-selected entry")
        if entry["gated_state"] == "unresolved":
            candidate = laggard._inactive_arm("unresolved", "opening_pullback_rebound_close_unavailable")
        else:
            wire.require(entry["gated_state"] == "decline", "EJ open-pullback rebound gate differs")
            candidate = laggard._inactive_arm("no_entry", "opening_pullback_rebound_not_positive")
    question = {"status": "opportunity", "episode_origin_context": "other", "decision_at": plan["decision_at"],
                "rank_decision": entry, "baseline": baseline, "candidate": candidate}
    return {"session": day, "symbol": roster["symbol"], "fold": fold, "roster": roster, "questions": [question]}


def calculate(spec, parents, slot, invented, root, sessions, *, audit=False, stored=None):
    """Rebuild both opening-pullback policies through the fixed opening replay."""
    select = selection.reference_day if audit else selection.select_day
    return _replay(spec, parents, slot, invented, root, sessions, select=select, project=_project,
                   entry_offsets=selection.ENTRY_OFFSETS, build_row=_row, summarize=report.summarize,
                   label="EJ open-pullback", audit=audit, stored=stored)
