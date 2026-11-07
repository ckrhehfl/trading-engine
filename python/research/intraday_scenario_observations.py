"""Offline support census, with no model-input or trading-policy interface."""

from dataclasses import dataclass

from research.intraday_ed_inputs import MINUTE, SessionInputs
from research.intraday_scenario_labels import PathResult, label_horizons
from research.intraday_scenario_questions import InitialPath, opening_range_questions
from research.intraday_scenario_state import OpeningRangePriceEvidence, ScenarioScan, scan_opening_range_high


@dataclass(frozen=True)
class PathObservation:
    initial: InitialPath
    results: tuple[PathResult, ...]


@dataclass(frozen=True)
class OpeningSupportObservation:
    scan: ScenarioScan
    paths: tuple[PathObservation, ...]


def opening_support_observation(
    session: SessionInputs, evidence: OpeningRangePriceEvidence | None,
) -> OpeningSupportObservation:
    """Audit a whole historical session. This record contains subsequent outcomes.

    For an online decision, use the scan limited to that decision's cutoff and
    store its projection separately, before any outcome access.
    """
    scan = scan_opening_range_high(session, current_evidence=evidence, through=session.closing - MINUTE)
    if scan.event is None:
        return OpeningSupportObservation(scan, ())
    paths = opening_range_questions(session, scan.event)
    cutoff_at = session.closing + 2 * MINUTE
    return OpeningSupportObservation(scan, tuple(PathObservation(initial, label_horizons(
        initial.question, initial.start_close, session, cutoff_at=cutoff_at,
    ) if initial.question is not None else ()) for initial in paths))
