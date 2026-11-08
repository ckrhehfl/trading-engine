"""Outcome-blind EI take/skip opportunities with a shared reservation clock.

This is an event comparison roster, not a concurrent cash account. A chosen
question reserves its symbol through its original expiry in both arms, even
when the learned arm later skips it or exits early. Unknown observations and
suppressed questions stay in the ledger. The caller authenticates the snapshot.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_features import ContextFeatures
from research.intraday_context_snapshot import ProjectedCoordinate, _roster, _validate_coordinate, question_digest
from research.intraday_ed_sources import TargetCoordinate


SCHEMA = "ei-context-entry-roster-draft-v1"


@dataclass(frozen=True)
class Opportunity:
    feature: ContextFeatures
    status: str
    reasons: tuple[str, ...]
    reserved_by: str | None

    @property
    def question_id(self) -> str:
        return question_digest(self.feature.question)


@dataclass(frozen=True)
class CoordinateOpportunities:
    session: str
    symbol: str
    coordinate_status: str
    original_frames: int
    frame_states: tuple[tuple[str, int], ...]
    questions: tuple[Opportunity, ...]


@dataclass(frozen=True)
class EntryRoster:
    scenario: str
    source_sha256: str
    original_dates: tuple[str, ...]
    coordinates: tuple[CoordinateOpportunities, ...]
    original_union_coordinates: int
    opportunity_counts: tuple[tuple[str, int], ...]


def _opportunities(coordinate: ProjectedCoordinate) -> tuple[Opportunity, ...]:
    reserved_until: datetime | None = None
    reserved_by = None
    rows = []
    ordered = sorted(coordinate.questions, key=lambda feature: (
        feature.forecast_at, feature.question.stage != "breakout", question_digest(feature.question)))
    for feature in ordered:
        question = feature.question
        if feature.status != "ready":
            rows.append(Opportunity(feature, "feature_unsupported", feature.reasons, None))
        elif question.unsupported_reasons:
            rows.append(Opportunity(feature, "question_unsupported", question.unsupported_reasons, None))
        elif reserved_until is not None and feature.forecast_at < reserved_until:
            rows.append(Opportunity(feature, "overlap_suppressed", ("earlier_shared_reservation",), reserved_by))
        else:
            reserved_until, reserved_by = question.path.end_at, question_digest(question)
            rows.append(Opportunity(feature, "opportunity", (), None))
    return tuple(rows)


def build_entry_roster(coordinates: Iterable[ProjectedCoordinate], *, expected_targets: tuple[TargetCoordinate, ...],
                      scenario: str, source_sha256: str) -> EntryRoster:
    """Exhaust the complete original feature roster without accepting outcomes.

    Same-clock breakout priority is fixed. A non-ready question cannot reserve
    a future slot, and the expiry may not be changed using an eventual fill or
    return. All original scenario coordinates, including no-event dates, remain.
    """
    wire.require(scenario in ("S1", "S2") and wire.valid_sha(source_sha256), "EI entry roster identity differs")
    groups = _roster(expected_targets)
    expected = iter(sorted(groups.items()))
    sentinel = object()
    result, counts = [], Counter()
    for coordinate in coordinates:
        item = next(expected, sentinel)
        wire.require(item is not sentinel and type(coordinate) is ProjectedCoordinate,
                     "EI entry original coordinate count differs")
        key, targets = item
        wire.require((coordinate.session, coordinate.symbol) == key, "EI entry original coordinate order differs")
        _validate_coordinate(coordinate, targets)
        if not any(target.scenario == scenario for target in targets):
            continue
        opportunities = _opportunities(coordinate)
        counts.update(row.status for row in opportunities)
        result.append(CoordinateOpportunities(coordinate.session, coordinate.symbol, coordinate.status,
            len(coordinate.frames), tuple(sorted(Counter(row.status for row in coordinate.frames).items())), opportunities))
    wire.require(next(expected, sentinel) is sentinel, "EI entry original coordinate missing")
    dates = tuple(sorted({target.session for target in expected_targets if target.scenario == scenario}))
    return EntryRoster(scenario, source_sha256, dates, tuple(result), len(groups), tuple(sorted(counts.items())))
