"""Replay two fixed hold extensions on the audited opening-laggard entries."""

from dataclasses import replace
from datetime import date

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_hold as hold
from research import intraday_candidate_laggard_hold_report as report
from research import intraday_candidate_laggard_run as laggard
from research.intraday_candidate_room_saved import _parent
from research.intraday_ed_inputs import MINUTE


PARENT_UNIT = "EJ-ENTRY-OPENING-LAGGARDS"
EXTENDED_TRADE_OFFSETS = tuple(range(89, 154))


def _session(reader, row, invented, audit):
    symbol, day = row["symbol"], row["session"]
    session = laggard._project(reader, symbol, day, EXTENDED_TRADE_OFFSETS, invented, audit)
    if not invented:
        return session

    # Exercise the 30-minute boundary, a stop after the 60-minute cap,
    # an unknown first close, and expiry in the invented qualification.
    weekday = date.fromisoformat(day).weekday()
    minutes = dict(session.minutes)
    if weekday == 3:
        del minutes[session.opening + 90 * MINUTE]
    elif weekday in (0, 1):
        at = session.opening + (89 if weekday == 0 else 130) * MINUTE
        minute = minutes[at]
        opened, high, _, _ = minute.ohlc
        close = opened - 31
        minutes[at] = replace(minute, ohlc=(opened, high, close, close))
    return replace(session, minutes=minutes)


def calculate(spec, parents, slot, invented, root, sessions, publication, audit_receipt, *, audit=False, stored=None):
    """Authenticate original outcomes and independently reconstruct new exits."""
    earlier, prior = _parent(spec["laggard_parent"], PARENT_UNIT, root, invented, publication, audit_receipt)
    needs_session = hold.needs_reference_session if audit else hold.needs_session
    project = hold.reference_project if audit else hold.project
    reports, outputs = {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        cache = {}
        for parent in parents["inputs"]:
            scenario = parent["scenario"]
            rows = prior[scenario]
            result = []
            previous = stored[scenario] if audit else [None] * len(rows)
            for row, saved in zip(rows, previous, strict=True):
                session = None
                if needs_session(row):
                    key = row["session"], row["symbol"]
                    if key not in cache:
                        cache[key] = _session(reader, row, invented, audit)
                    session = cache[key]
                rebuilt = project(row, session)
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(saved), "EJ laggard hold audited trade differs")
                result.append(rebuilt)

            contrasts = report.summarize(result, audit=audit)
            original = earlier[scenario]["comparison"]["groups"]["all"]["totals"]["baseline"]
            for name in ("hold30_vs_hold60", "hold30_vs_hold90"):
                comparison = contrasts[name]["comparison"]
                wire.require(tuple(comparison[k] for k in ("coordinates", "sessions", "weeks")) == (3140, 157, 34)
                             and comparison["groups"]["all"]["totals"]["baseline"] == original,
                             "EJ laggard hold control or evaluation geometry differs")
            reports[scenario] = contrasts
            outputs[scenario] = result
    return reports, outputs
