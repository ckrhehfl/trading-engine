"""Invented EH observations for positions after first entry and reentry."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_decision_inputs import chart_decision_inputs
from research.intraday_scenario_held_inputs import held_decision_inputs
from research.intraday_scenario_management_entry import fill_matched_entry
from research.intraday_scenario_policy import EntryBounds, Values, apply_modeled_open, decide
from test_intraday_scenario_management_compare import SYMBOL, at, row
from test_intraday_scenario_management_entry import breakout_frame, prepare


def _entered():
    _, frame, session, event = breakout_frame()
    planned = prepare(frame, session.minutes[at(24)])
    episode, fill = fill_matched_entry(planned, at(28), Fraction(590))
    assert fill.filled
    return episode, frame, session, event


def _observe(session, episode, *, decision_at=at(37), planning_cap=at(58)):
    return held_decision_inputs(session, episode, level=Fraction(570), band=Fraction(5),
                                decision_at=decision_at, planning_cap=planning_cap)


def test_held_projection_continues_after_initial_question_resolves_without_seeing_suffix():
    episode, frame, session, _ = _entered()
    prefix = session.minutes | {at(32): row(32, 620, high=700),
                                at(34): row(34, 610, opened=605, high=615, low=603)}

    class PrefixProbe(Mapping):
        def __getitem__(self, clock):
            assert clock <= at(34)
            return prefix[clock]

        def __iter__(self):
            raise AssertionError("held projection must read an exact window")

        def __len__(self):
            return len(prefix)

    path = frame.initial
    resolved = chart_decision_inputs(replace(session, minutes=prefix), path.question, path.start_close,
                                     decision_at=at(37))
    assert resolved.status == "resolved"
    observed = _observe(replace(session, minutes=PrefixProbe()), episode)
    assert (observed.symbol, observed.entered_at, observed.decision_at, observed.planning_cap) == (
        SYMBOL, at(28), at(37), at(58),
    )
    assert (observed.attempt, observed.status, observed.minutes_held, observed.minutes_remaining) == (1, "ready", 9, 21)
    assert (observed.entry_in_bands, observed.stop_in_bands, observed.remaining_fraction, observed.half_reduced) == (
        4, -1, 1, False,
    )
    assert len(observed.minutes) == 20 and len(observed.five_minute_bars) == 4
    assert observed.minutes[-1] == (7, 9, Fraction(33, 5), 8)
    assert observed.five_minute_bars[-1] == (6, 26, 6, 8)
    suffix = {at(35): row(35, 900), at(38): row(38, 100)}
    assert _observe(replace(session, minutes=prefix | suffix), episode) == observed


@pytest.mark.parametrize("late", [False, True])
def test_due_missing_or_late_minute_is_explicit_unknown(late):
    episode, _, session, _ = _entered()
    prices = dict(session.minutes)
    if late:
        prices[at(34)] = replace(prices[at(34)], available_at=at(38))
    else:
        del prices[at(34)]
    observed = _observe(replace(session, minutes=prices), episode)
    assert observed.status == "unknown" and not observed.minutes and not observed.five_minute_bars
    issue, = observed.issues
    assert issue.minute == at(34)
    assert issue.reason == ("minute_available_after_cutoff" if late else "missing_minute")


def test_reduced_then_fresh_position_uses_actual_lot_and_unchanged_event_cap():
    episode, _, session, event = _entered()
    reduce = decide(episode, at(37), visible_minute=session.minutes[at(34)],
                    values=Values(at(37), hold=Fraction(), reduce_half=Fraction(1), exit=Fraction()))
    reduced, fill = apply_modeled_open(reduce.episode, at(38), Fraction(600))
    assert fill.filled
    observed = _observe(session, reduced, decision_at=at(42))
    assert (observed.attempt, observed.entry_in_bands, observed.remaining_fraction, observed.half_reduced) == (
        1, 4, Fraction(1, 2), True,
    )
    leave = decide(reduced, at(42), visible_minute=session.minutes[at(39)],
                   values=Values(at(42), hold=Fraction(), exit=Fraction(1)))
    flat, fill = apply_modeled_open(leave.episode, at(43), Fraction(600))
    assert fill.filled
    # The extractor consumes the resulting held lot. The policy caller owns
    # validation of the fresh setup that supplied this invented reset clock.
    fresh = decide(flat, at(47), visible_minute=session.minutes[at(44)], reset_at=at(47),
                   values=Values(at(47), wait=Fraction(), enter=Fraction(1)),
                   bounds=EntryBounds(Fraction(560), Fraction(605)))
    reentered, fill = apply_modeled_open(fresh.episode, at(48), Fraction(595))
    assert fill.filled
    observed = _observe(session, reentered, decision_at=at(52), planning_cap=event.expires_at)
    assert (observed.attempt, observed.entered_at, observed.entry_in_bands, observed.stop_in_bands) == (2, at(48), 5, -2)
    assert (observed.remaining_fraction, observed.half_reduced, observed.minutes_remaining) == (1, False, 12)


def test_wrong_clock_or_cap_and_pending_position_are_refused():
    episode, _, session, _ = _entered()
    with pytest.raises(InputRefusal, match="aware_minute_clock_required"):
        _observe(session, episode, decision_at=at(37) + timedelta(seconds=1))
    for changes in (dict(planning_cap=at(57)), dict(decision_at=at(27))):
        with pytest.raises(InputRefusal, match="clock_or_cap_differs"):
            _observe(session, episode, **changes)
    leave = decide(episode, at(32), visible_minute=session.minutes[at(29)],
                   values=Values(at(32), hold=Fraction(), exit=Fraction(1)))
    with pytest.raises(InputRefusal, match="requires_confirmed_position"):
        _observe(session, leave.episode)
