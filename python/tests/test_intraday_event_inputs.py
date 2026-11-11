from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import Minute, SessionInputs
from research.intraday_event_inputs import adapt_session, case_roster, prefix_card
from research.intraday_event_scenarios import EventFrame


def session():
    opening = datetime(2025, 1, 2, tzinfo=timezone.utc)
    minute = Minute("KRX:000001", opening, opening + timedelta(minutes=3),
                    tuple(map(Decimal, (100, 102, 99, 101))), Decimal(0))
    return SessionInputs(minute.symbol, opening, opening + timedelta(minutes=60), {opening: minute})


def frame(event="e1", kind="at_onset", state="WAITING"):
    return EventFrame("A_DOWNFAIL", event, "2025-01-02:KRX:000001", 0, 0, 3,
                      0, 0, kind, state, False, Fraction(1), Fraction(100), Fraction(99), ())


def test_calendar_and_zero_remain_explicit():
    value = adapt_session(session())
    assert value.session_length == 60
    assert value.bars[0].close == 101 and value.volumes[0].value == 0
    assert len(value.states) == 60 and value.states[1]["price"] == "missing"
    assert 1 not in value.bars and 1 not in value.volumes


def test_receipt_fraction_is_rounded_up_not_backdated():
    source = session()
    item = source.minutes[source.opening]
    source = replace(source, minutes={source.opening: replace(item, available_at=item.available_at + timedelta(seconds=1))})
    value = adapt_session(source)
    assert value.bars[0].available_offset == 4
    assert not prefix_card(frame(), value)["observations"][0]["price_known"]


@pytest.mark.parametrize("change", [
    {"symbol": "KRX:000002"}, {"available_at": datetime(2025, 1, 2, tzinfo=timezone.utc)},
    {"price_state": "missing"}, {"volume_state": "halt"}, {"ohlc": None}, {"shares": None},
    {"shares": 0.1}, {"price_state": "assumed_flat"},
])
def test_impossible_or_inconsistent_observation_rejected(change):
    source = session()
    item = replace(source.minutes[source.opening], **change)
    with pytest.raises(ValueError):
        adapt_session(replace(source, minutes={source.opening: item}))


def test_case_selection_ignores_later_terminal_states_and_order():
    onsets = [frame(f"e{i}") for i in range(10)]
    selected = case_roster(onsets)
    terminals = [replace(row, kind="terminal", state="CONFIRMED", terminal=True, offset=10, decision_offset=13)
                 for row in onsets]
    assert case_roster(list(reversed(onsets)) + terminals) == selected
    assert len(selected["examples"]) == 3 and selected["candidate_episodes"] == 10
    assert case_roster(onsets + [onsets[0]]) == selected


def test_case_roster_conflicting_onset_rejected():
    with pytest.raises(ValueError, match="Conflicting"):
        case_roster([frame(), replace(frame(), onset_offset=1)])


def test_prefix_has_neither_future_value_nor_missing_as_zero():
    source = session()
    later = source.opening + timedelta(minutes=2)
    item = Minute(source.symbol, later, later + timedelta(minutes=3),
                  tuple(map(Decimal, (500, 502, 499, 501))), Decimal(100))
    value = adapt_session(replace(source, minutes={**source.minutes, later: item}))
    card = prefix_card(frame(), value)
    assert card["observations"][0]["ohlc"] == ["100", "102", "99", "101"]
    assert card["observations"][0]["reported_volume"] == "0"
    assert card["observations"][1]["ohlc"] is None
    assert card["observations"][2]["ohlc"] is None
