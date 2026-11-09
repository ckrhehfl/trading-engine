"""Replay the fixed profit exit on the audited opening-laggard entries."""

from dataclasses import replace
from decimal import Decimal

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_capacity_reference as capacity_reference
from research import intraday_candidate_capacity_report as capacity_report
from research import intraday_candidate_exit as cash_summary, intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_profit as profit
from research import intraday_candidate_laggard_report as support
from research import intraday_candidate_laggard_run as laggard
from research.intraday_candidate_room_saved import _parent
from research.intraday_ed_inputs import MINUTE


def _session(reader, row, invented, audit):
    symbol, day = row["symbol"], row["session"]
    session = laggard._project(reader, symbol, day, laggard.TRADE_OFFSETS, invented, audit)
    if invented and symbol == "100000":
        # Add a favorable close while keeping the parent's entry and exit opens.
        at = session.opening + 70 * MINUTE
        minute = session.minutes[at]
        op, high, low, close = minute.ohlc
        raised = close + Decimal(100)
        minutes = {**session.minutes, at: replace(minute, ohlc=(op, max(high, raised), low, raised))}
        return replace(session, minutes=minutes)
    return session


def calculate(spec, parents, slot, invented, root, sessions, publication, audit_receipt, *, audit=False, stored=None):
    """Keep prior control rows byte-for-byte and only reprice the new exit."""
    earlier, prior = _parent(spec["laggard_parent"], "EJ-ENTRY-OPENING-LAGGARDS", root,
                             invented, publication, audit_receipt)
    needs_session = profit.needs_reference_session if audit else profit.needs_session
    project = profit.reference_project if audit else profit.project
    capacity = capacity_reference.reconstruct_capacity if audit else capacity_report.summarize_capacity
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
                    wire.require(wire.encode(rebuilt) == wire.encode(saved), "EJ laggard profit audited trade differs")
                result.append(rebuilt)
            comparison = cash_summary.summarize(result)
            cash_reference.verify_summary(result, comparison)
            wire.require(tuple(comparison[k] for k in ("coordinates", "sessions", "weeks")) == (3140, 157, 34)
                         and comparison["groups"]["all"]["totals"]["baseline"]
                         == earlier[scenario]["comparison"]["groups"]["all"]["totals"]["baseline"],
                         "EJ laggard profit control or evaluation geometry differs")
            reports[scenario] = {"comparison": comparison, "execution": support.execution_summary(result, audit=audit),
                                 "capacity_cohort": "batch8_rank_only_immediate_closed",
                                 "path_capacity": capacity(result)}
            outputs[scenario] = result
    return reports, outputs
