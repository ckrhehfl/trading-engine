"""Invented opening scenarios with different predecision upper price space."""

from dataclasses import replace

from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_observations import opening_support_observation
from research.intraday_scenario_questions import opening_range_questions
from test_intraday_scenario_state import BELOW, BREAKOUT, OPENING, OPENING_EVIDENCE, NoReadAfter, opening_scan, session


def test_prepared_and_breakout_keep_different_questions_and_do_not_read_future_obstacle():
    current = session(BELOW, BELOW, (94, 100, 89, 99), (99, 105, 98, 100),
                      (100, 108, 99, 100), (100, 106, 99, 105), (105, 107, 103, 106))
    prepared_event = opening_scan(current, 22).event
    first, = opening_range_questions(replace(current, minutes=NoReadAfter(current.minutes, 20)), prepared_event)
    assert first.stage == "approach" and first.target_basis == "prepared_band"
    assert first.question.start_at == OPENING + 22 * MINUTE
    assert first.question.stop == 100 - 2 * prepared_event.band
    assert first.question.target == 100 + 2 * prepared_event.band

    event = opening_scan(current, 32).event
    before, after = opening_range_questions(replace(current, minutes=NoReadAfter(current.minutes, 30)), event)
    assert before == first
    assert after.stage == "breakout" and after.start_close == 105
    assert after.question.target == 108 and after.target_basis == "known_intraday_swing_high"
    assert after.question.stop == 100 - event.band
    assert after.question.start_at == OPENING + 32 * MINUTE
    assert after.question.question_id != first.question.question_id


def test_stop_distance_fallback_requires_the_whole_predecision_prefix():
    current = session(BELOW, BELOW, BELOW, BREAKOUT)
    event = opening_scan(current, 22).event
    first, = opening_range_questions(current, event)
    assert first.target_basis == "twice_stop_distance"
    assert first.question.target == 103 + 2 * (103 - 70)

    rows = dict(current.minutes)
    rows.pop(OPENING + MINUTE)
    unsupported, = opening_range_questions(replace(current, minutes=rows), event)
    assert unsupported.question is None and unsupported.issues[0].reason == "missing_minute"


def test_missing_continuation_keeps_the_initial_approach_and_its_unknown_paths():
    first = (94, 100, 89, 99)
    prepare = (99, 101, 98, 100)
    partial = opening_support_observation(session(BELOW, BELOW, first, prepare), OPENING_EVIDENCE)
    assert partial.scan.status == "support_unknown"
    assert partial.scan.event.kind == "prepared"
    observed, = partial.paths
    assert observed.initial.stage == "approach"
    assert [row.status for row in observed.results] == ["unknown"] * 3
    assert all(row.path is None for row in observed.results)

    nothing = opening_support_observation(session(*([BELOW] * 6), closing=30), OPENING_EVIDENCE)
    assert nothing.scan.status == "known_no_event" and nothing.paths == ()


def test_session_clock_excludes_long_horizons_even_if_a_short_horizon_resolves():
    opening_bars = (BELOW, BELOW, BELOW, (94, 101, 93, 100))
    touch = session(*opening_bars, (100, 104, 99, 103), (103, 104, 102, 103), closing=31)
    no_touch = session(*opening_bars, (100, 100, 99, 100), (100, 100, 99, 100), closing=31)
    with_touch = opening_support_observation(touch, OPENING_EVIDENCE).paths[0].results
    without_touch = opening_support_observation(no_touch, OPENING_EVIDENCE).paths[0].results
    assert with_touch[0].path == "P1" and without_touch[0].path == "P4"
    for results in (with_touch, without_touch):
        assert [(row.status, row.reason) for row in results[1:]] == [("unsupported", "outside_session")] * 2
