"""Original-denominator and shared-clock EI opportunity regressions."""
from dataclasses import replace

import pytest

from research.intraday_context_entry_roster import build_entry_roster
from research.intraday_context_snapshot import project_coordinate
from research.intraday_ed_sources import TargetCoordinate
from test_intraday_context_questions import BASE, BELOW, BREAK, OPEN, PEAK, TOUCH, at, session


def coordinate(value, *, day=None, scenarios=("S1", "S2")):
    targets = tuple(TargetCoordinate(name, "invented", day or OPEN.date().isoformat(), 1, None, "invented", b"{}")
                    for name in scenarios)
    return project_coordinate(targets, value), targets


def roster(rows, targets, scenario="S1"):
    return build_entry_roster(iter(rows), expected_targets=targets, scenario=scenario, source_sha256="a" * 64)


def test_same_clock_breakout_priority_does_not_create_duplicate_inventory():
    row, targets = coordinate(session(*BASE, PEAK, BELOW, BREAK))
    result = roster((row,), targets)
    first, second = result.coordinates[0].questions
    assert (first.feature.question.stage, first.status) == ("breakout", "opportunity")
    assert (second.feature.question.stage, second.status) == ("approach", "overlap_suppressed")
    assert second.reserved_by == first.question_id
    assert dict(result.opportunity_counts) == {"opportunity": 1, "overlap_suppressed": 1}


def test_earliest_clock_reserves_until_absolute_expiry_including_later_episodes():
    row, targets = coordinate(session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, BELOW, TOUCH, BELOW, TOUCH))
    result = roster((row,), targets)
    questions = result.coordinates[0].questions
    assert [item.feature.forecast_at for item in questions] == list(map(at, (32, 42, 52, 62)))
    assert [item.status for item in questions] == ["opportunity", "overlap_suppressed", "overlap_suppressed", "opportunity"]
    assert questions[1].reserved_by == questions[2].reserved_by == questions[0].question_id


def test_outside_session_question_is_reported_without_an_opportunity():
    row, targets = coordinate(session(*BASE, PEAK, BELOW, TOUCH, closing=35))
    result = roster((row,), targets)
    item, = result.coordinates[0].questions
    assert item.status == "question_unsupported" and "outside_session" in item.reasons
    assert dict(result.opportunity_counts) == {"question_unsupported": 1}


def test_missing_and_no_event_coordinates_and_dates_are_not_deleted():
    first, targets = coordinate(session(*BASE, *((100, 101, 99, 100),) * 5))
    second, missing = coordinate(None, day="2031-02-13")
    result = roster((first, second), targets + missing)
    assert len(result.coordinates) == result.original_union_coordinates == 2
    assert result.original_dates == ("2031-02-12", "2031-02-13")
    assert not result.coordinates[0].questions and result.coordinates[0].original_frames == 72
    assert result.coordinates[1].coordinate_status == "session_missing"
    assert not result.coordinates[1].questions and not result.opportunity_counts


def test_other_scenario_coordinates_remain_in_original_union_but_not_selected_arm():
    first, targets = coordinate(session(*BASE, PEAK, BELOW, TOUCH), scenarios=("S1",))
    second, other = coordinate(None, day="2031-02-13", scenarios=("S2",))
    result = roster((first, second), targets + other)
    assert result.original_union_coordinates == 2 and len(result.coordinates) == 1
    assert result.original_dates == ("2031-02-12",)
    with pytest.raises(ValueError):
        roster((first, replace(second, status="observed_session")), targets + other)


@pytest.mark.parametrize("corruption", ("missing", "duplicate", "reordered", "target"))
def test_whole_original_roster_is_required(corruption):
    first, targets = coordinate(session(*BASE, PEAK, BELOW, TOUCH))
    second, missing = coordinate(None, day="2031-02-13")
    rows = (first, second)
    if corruption == "missing":
        rows = (first,)
    elif corruption == "duplicate":
        rows = (first, first, second)
    elif corruption == "reordered":
        rows = (second, first)
    else:
        rows = (replace(first, targets=tuple(replace(item, rank=2) for item in first.targets)), second)
    with pytest.raises(ValueError):
        roster(rows, targets + missing)


@pytest.mark.parametrize("scenario,pin", (("S3", "a" * 64), ("S1", "not-a-source")))
def test_external_identity_is_explicit(scenario, pin):
    row, targets = coordinate(None)
    with pytest.raises(ValueError):
        build_entry_roster(iter((row,)), expected_targets=targets, scenario=scenario, source_sha256=pin)
