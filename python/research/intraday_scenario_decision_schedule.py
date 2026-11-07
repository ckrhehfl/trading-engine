"""Advance EH feature rows on observable clocks, without a census or label file."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from research.intraday_chart_inputs import _need, _session
from research.intraday_ed_inputs import MINUTE, SessionInputs
from research.intraday_scenario_decision_inputs import ChartDecisionInputs, chart_decision_inputs
from research.intraday_scenario_questions import InitialPath, opening_range_questions
from research.intraday_scenario_state import OpeningRangePriceEvidence, opening_range_scan_steps


@dataclass(frozen=True)
class DecisionFrame:
    symbol: str
    observed_at: datetime
    event_at: datetime
    event_expires_at: datetime
    initial: InitialPath
    chart: ChartDecisionInputs | None


def opening_decision_frames(
    session: SessionInputs, evidence: OpeningRangePriceEvidence | None,
) -> Iterator[DecisionFrame]:
    """First approach and breakout share one event and keep their own questions."""
    _session(session)
    event = None
    initial_paths: tuple[InitialPath, ...] = ()
    closed_stages = set()
    scanning = True
    at = session.opening + 22 * MINUTE
    scans = opening_range_scan_steps(session, current_evidence=evidence, through=session.closing)
    while at < session.closing:
        if event is not None and at >= event.expires_at:
            break
        if scanning:
            scan_at, scan = next(scans)
            _need(scan_at == at, "eh_opening_scan_grid_differs")
            if scan.status == "support_unknown" and scan.event is None:
                break
            if scan.event != event:
                event = scan.event
                if event is not None:
                    initial_paths = opening_range_questions(session, event)
            if scan.status == "support_unknown" or (event is not None and event.breakout is not None):
                scanning = False
        if event is not None:
            for path in initial_paths:
                if path.stage in closed_stages:
                    continue
                chart = None
                if path.question is not None:
                    chart = chart_decision_inputs(session, path.question, path.start_close, decision_at=at)
                if chart is None or chart.status in ("resolved", "unsupported"):
                    closed_stages.add(path.stage)
                yield DecisionFrame(session.symbol, at, event.origin.available_at, event.expires_at, path, chart)
        if not scanning and all(path.stage in closed_stages for path in initial_paths):
            break
        at += 5 * MINUTE
