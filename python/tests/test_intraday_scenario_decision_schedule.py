"""Keep question creation and updates on their original observable clocks."""

from dataclasses import replace

from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_decision_schedule import opening_decision_frames
from test_intraday_scenario_state import BELOW, OPENING, OPENING_EVIDENCE, NoReadAfter, session


def at(offset):
    return OPENING + offset * MINUTE


def test_frame_iterator_does_not_look_ahead_to_a_later_breakout():
    current = session(BELOW, BELOW, (94, 100, 89, 99), (99, 101, 98, 100), (100, 101, 99, 100),
                      (100, 104, 99, 103), (103, 105, 102, 104))
    source = NoReadAfter(current.minutes, 20)
    frames = opening_decision_frames(replace(current, minutes=source), OPENING_EVIDENCE)
    first = next(frames)
    assert first.initial.stage == "approach" and first.observed_at == first.event_at == at(22)
    assert first.chart.status == "ready"

    source.limit = at(25)
    update = next(frames)
    assert update.observed_at == at(27) and update.initial == first.initial and update.chart.status == "ready"
    assert update.chart.forecast_id[:2] == first.chart.forecast_id[:2]

    source.limit = at(30)
    resolved, breakout = next(frames), next(frames)
    assert resolved.chart.status == "resolved" and resolved.observed_at == breakout.observed_at == at(32)
    assert breakout.initial.stage == "breakout" and breakout.chart.status == "ready"
    assert breakout.event_at == first.event_at and breakout.event_expires_at == first.event_expires_at
    assert breakout.chart.forecast_id[1] != first.chart.forecast_id[1]


def test_opening_missingness_cannot_create_a_first_event_from_later_bars():
    current = session(BELOW, BELOW, BELOW, None, (103, 105, 102, 104))
    assert tuple(opening_decision_frames(current, OPENING_EVIDENCE)) == ()
