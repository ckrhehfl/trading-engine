"""Invented EH action paths and independent exact cash expectations."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_scenario_policy import EntryBounds, Values, apply_modeled_open, decide, start_episode
from research.intraday_scenario_rollout import AnaloguePath, compare_actions


OPEN = datetime(2031, 2, 3, 1, tzinfo=UTC)
SYMBOL = "invented-rollout"
BOUNDS = EntryBounds(Fraction(565), Fraction(603))


def at(offset):
    return OPEN + offset * MINUTE


def row(offset, close=600, *, opened=None, delay=0):
    op = close if opened is None else opened
    ohlc = tuple(map(Decimal, (op, max(op, close), min(op, close), close)))
    return Minute(SYMBOL, at(offset), at(offset + 3 + delay), ohlc,
                  None, volume_state="missing")


def episode():
    return start_episode(cash.new_book("invented-EH-rollout"), SYMBOL, at(0), at(40),
                         risk_budget=Fraction(880), cost_bps=33)


def analogue(key, weight, opens, closes=None):
    closes = closes or {}
    minutes = {at(index): row(index, closes.get(index, 600), opened=opens.get(index)) for index in range(-3, 41)}
    return AnaloguePath(key, weight, SessionInputs(SYMBOL, at(-3), at(41), minutes),
                        {at(index): Fraction(value) for index, value in opens.items()})


def held():
    step = decide(episode(), at(0), visible_minute=row(-3), bounds=BOUNDS,
                  values=Values(at(0), enter=Fraction(1), wait=Fraction()))
    return apply_modeled_open(step.episode, at(1), Fraction(600))


def test_wait_uses_one_frozen_confirmation_and_utility_uses_both_branches():
    up = analogue("up", Fraction(1, 2), {1: 600, 6: 601, 11: 610}, {2: 601})
    down = analogue("down", Fraction(1, 2), {1: 600, 5: 560}, {1: 565, 2: 595})
    initial = episode()
    result = compare_actions(initial, at(0), visible_minute=row(-3), paths=(up, down),
                             planning_minutes=11, downside_penalty=Fraction(1, 2),
                             bounds=BOUNDS, wait_trigger=Fraction(600))
    assert not result.unavailable_actions
    branches = {(item.action, item.path_key): item for item in result.outcomes}
    assert branches["enter", "up"].net_change == 110 - Fraction(1089, 50)
    loss = -440 - Fraction(1089, 50)
    assert branches["enter", "down"].net_change == loss
    assert branches["wait", "down"].status == "no_entry" and branches["wait", "down"].net_change == 0
    assert branches["wait", "down"].reason == "wait_confirmation_not_met"
    waited = branches["wait", "up"]
    assert waited.net_change == 99 - Fraction(601 * 11 * 33, 10_000)
    assert [(event.at, event.execute_at, event.quantity) for event in waited.events if event.action == "enter"
            and event.fill_status is None] == [(at(5), at(6), 11)]
    assert result.values.wait == waited.net_change / 2
    assert result.values.enter == (branches["enter", "up"].net_change + Fraction(3, 2) * loss) / 2
    assert branches["enter", "up"].events[0] == branches["enter", "down"].events[0]
    down_stop = next(event for event in branches["enter", "down"].events if event.reason == "structural_close_failure")
    assert (down_stop.at, down_stop.execute_at) == (at(4), at(5))
    assert initial.end_at == at(40) and initial.book.cash == cash.INITIAL_CAPITAL


def test_hold_reduce_and_exit_start_with_one_lot_and_preserve_paid_cost():
    initial, purchase = held()
    # Offset 3's close becomes visible at 6, when the half sale is being
    # applied. The next legal decision must still consider that close.
    path = analogue("stop", Fraction(1), {6: 590, 7: 580, 8: 570}, {3: 565})
    result = compare_actions(initial, at(5), visible_minute=row(2, 590), paths=(path,),
                             planning_minutes=10, downside_penalty=Fraction())
    assert result.values == Values(at(5), hold=Fraction(-110), reduce_half=Fraction(-120), exit=Fraction())
    outcomes = {item.action: item for item in result.outcomes}
    reduced = outcomes["reduce_half"]
    assert [(event.at, event.quantity) for event in reduced.events if event.fill_status == "modeled"] == [
        (at(6), 5), (at(8), 6),
    ]
    assert next(event.at for event in reduced.events if event.reason == "structural_close_failure") == at(7)
    for item in outcomes.values():
        assert item.status == "closed" and not item.terminal_book.lots
        assert sum(exit.allocated_entry_cost for exit in item.terminal_book.realized_exits) == purchase.assumed_cost
    assert initial.book.lots[0].quantity == 11 and not initial.book.realized_exits


def test_missing_execution_keeps_its_weight_and_never_reads_another_open():
    class OpenProbe(dict):
        def get(self, key, default=None):
            assert key == at(1)
            reads.append(key)
            return super().get(key, default)

    reads = []
    unknown = replace(analogue("missing", Fraction(1, 2), {}), modeled_opens=OpenProbe())
    known = analogue("known", Fraction(1, 2), {1: 604})
    result = compare_actions(episode(), at(0), visible_minute=row(-3), paths=(unknown, known),
                             planning_minutes=10, downside_penalty=Fraction(), bounds=BOUNDS)
    assert result.values is None and reads == [at(1)]
    missed, declined = (item for item in result.outcomes if item.action == "enter")
    assert missed.status == "unresolved" and missed.reason == "entry_price_unknown"
    assert missed.weight == Fraction(1, 2) and missed.net_change is None
    assert missed.terminal_book.inventory_uncertain and missed.terminal_book.cash == cash.INITIAL_CAPITAL
    assert declined.status == "no_entry" and declined.net_change == 0


def test_missing_wait_confirmation_does_not_become_no_entry_or_trigger_a_later_read():
    class MinutesProbe(dict):
        def get(self, key, default=None):
            reads.append(key)
            return super().get(key, default)

    reads = []
    path = analogue("missing-confirmation", Fraction(1), {1: 604})
    path = replace(path, session=replace(path.session, minutes=MinutesProbe({at(7): row(7, 602)})))
    result = compare_actions(episode(), at(0), visible_minute=row(-3), paths=(path,),
                             planning_minutes=11, downside_penalty=Fraction(),
                             bounds=BOUNDS, wait_trigger=Fraction(600))
    wait, enter = result.outcomes
    assert reads == [at(2)] and result.values is None
    assert (wait.status, wait.reason, wait.net_change) == ("unresolved", "wait_confirmation_unknown:missing_minute", None)
    assert enter.status == "no_entry" and enter.reason == "open_outside_frozen_entry_bounds"


def test_late_stop_close_cannot_change_earlier_decision_or_be_replaced():
    initial, _ = held()
    path = analogue("late", Fraction(1), {6: 590})
    minutes = dict(path.session.minutes)
    minutes[at(3)] = row(3, 560, delay=1)
    path = replace(path, session=replace(path.session, minutes=minutes))
    result = compare_actions(initial, at(5), visible_minute=row(2, 590), paths=(path,),
                             planning_minutes=10, downside_penalty=Fraction())
    hold = next(item for item in result.outcomes if item.action == "hold")
    assert hold.events[0].action == "hold" and hold.status == "unresolved"
    assert hold.reason == "holding_close_unknown:minute_available_after_cutoff"
    assert hold.terminal_book == initial.book and result.values is None


def test_forced_exit_excludes_other_actions_and_uses_the_next_open():
    initial, _ = held()
    path = analogue("stop", Fraction(1), {6: 562})
    result = compare_actions(initial, at(5), visible_minute=row(2, 565), paths=(path,),
                             planning_minutes=1, downside_penalty=Fraction())
    assert result.unavailable_actions == (("hold", "structural_close_failure"),
                                          ("reduce_half", "structural_close_failure"))
    assert result.values == Values(at(5), exit=Fraction(-33))
    assert result.outcomes[0].events[0].execute_at == at(6)
    with pytest.raises(InputRefusal, match="horizon_or_penalty"):
        compare_actions(initial, at(5), visible_minute=row(2), paths=(path,),
                        planning_minutes=31, downside_penalty=Fraction())
