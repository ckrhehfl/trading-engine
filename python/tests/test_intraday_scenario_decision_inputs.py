"""Invented chart-first and same-clock peer evidence for EH questions."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal, MINUTE
from research.intraday_scenario_decision_inputs import chart_decision_inputs, peer_clock_inputs, peer_decision_inputs
from research.intraday_scenario_questions import opening_range_questions
from test_intraday_scenario_state import BELOW, OPENING, NoReadAfter, opening_scan, session


def at(offset):
    return OPENING + offset * MINUTE


def test_earliest_chart_uses_only_twenty_observed_minutes_and_keeps_the_question_id():
    current = session(BELOW, BELOW, (94, 100, 89, 99), (99, 101, 98, 100), (101, 104, 100, 103))
    initial, = opening_range_questions(current, opening_scan(current, 22).event)
    guarded = replace(current, minutes=NoReadAfter(current.minutes, 20))
    first = chart_decision_inputs(guarded, initial.question, initial.start_close, decision_at=at(22))
    assert first.status == "ready" and len(first.minutes) == 20 and len(first.five_minute_bars) == 4
    assert first.minutes[-1][-1] == 0 and first.state[:2] == (2, 2)

    later = chart_decision_inputs(current, initial.question, initial.start_close, decision_at=at(27))
    assert later.forecast_id[:2] == first.forecast_id[:2]
    assert later.status == "resolved" and not later.minutes and not later.state
    with pytest.raises(InputRefusal, match="22_plus_5m_grid"):
        chart_decision_inputs(current, initial.question, initial.start_close, decision_at=at(23))

    another = replace(current, symbol="other", minutes={timestamp: replace(row, symbol="other")
                                                         for timestamp, row in current.minutes.items()})
    pair = chart_decision_inputs(another, initial.question, initial.start_close, decision_at=at(22))
    assert pair.forecast_id[1:] == first.forecast_id[1:] and pair.forecast_id[0] != first.forecast_id[0]


def test_late_chart_bar_is_unknown_even_when_the_question_is_still_pending():
    current = session(BELOW, BELOW, BELOW, (94, 101, 93, 100))
    initial, = opening_range_questions(current, opening_scan(current, 22).event)
    rows = dict(current.minutes)
    rows[at(19)] = replace(rows[at(19)], available_at=at(23))
    inputs = chart_decision_inputs(replace(current, minutes=rows), initial.question, initial.start_close,
                                   decision_at=at(22))
    assert inputs.status == "unknown" and not inputs.minutes
    assert inputs.issues[0].reason == "minute_available_after_cutoff"


def test_peers_keep_the_original_denominator_and_never_read_past_the_chart_clock():
    current = session(*([(100, 102, 100, 101)] * 5))
    expected = tuple(f"peer-{index}" for index in range(6))
    peers = {symbol: replace(current, symbol=symbol, minutes=NoReadAfter({
        timestamp: replace(row, symbol=symbol) for timestamp, row in current.minutes.items()
    }, 20)) for symbol in expected[:-1]}
    inputs = peer_decision_inputs(current, decision_at=at(22), members_known_at=at(0),
                                  expected_peers=expected, peers=peers)
    assert inputs.expected == expected and inputs.complete == expected[:-1]
    assert inputs.sequence == ((Fraction(1, 100), Fraction(1, 100), Fraction(1)),) * 20
    assert inputs.issues[0].symbol == expected[-1]
    reduced = peer_decision_inputs(current, decision_at=at(22), members_known_at=at(0),
                                   expected_peers=expected, peers={symbol: peers[symbol] for symbol in expected[:4]})
    assert reduced.sequence is None and reduced.complete == expected[:4]


def test_future_roster_cannot_access_peer_prices():
    class UnreadablePeers(dict):
        def get(self, key, default=None):
            raise AssertionError(f"Unexpected peer read {key}")

    current = session()
    inputs = peer_decision_inputs(current, decision_at=at(22), members_known_at=at(23),
                                  expected_peers=("peer",), peers=UnreadablePeers())
    assert inputs.sequence is None and inputs.issues[0].reason == "peer_roster_unavailable_at_decision"


def test_same_clock_reuses_peer_projections_and_excludes_each_targets_own_return():
    class CountReads(dict):
        count = 0

        def get(self, key, default=None):
            self.count += 1
            return super().get(key, default)

    current = session(*([(100, 110, 100, 110)] * 4))
    flat = session(*([(100, 100, 100, 100)] * 4))
    peers = CountReads({f"peer-{index}": replace(flat, symbol=f"peer-{index}", minutes={
        timestamp: replace(row, symbol=f"peer-{index}") for timestamp, row in flat.minutes.items()
    }) for index in range(5)})
    peers[current.symbol] = current
    roster = tuple(peers) + ("missing",)
    own, outsider = peer_clock_inputs(current, decision_at=at(22), members_known_at=at(0), roster=roster,
                                      target_symbols=(current.symbol, "other"), peers=peers)
    assert peers.count == len(roster)
    assert len(own.expected) == 6 and current.symbol not in own.complete
    assert own.sequence[0] == (0, 0, 0)
    assert outsider.sequence[0] == (Fraction(1, 60), Fraction(1, 60), Fraction(1, 6))
