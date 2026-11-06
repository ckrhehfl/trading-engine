"""Invented-only EG chart and peer decision-clock checks."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_highvol_inputs import build_chart_inputs, build_peer_inputs


OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))
D = Decimal


def at(offset):
    return OPEN + offset * MINUTE


def session(symbol="TARGET", *, rising=True):
    rows = {}
    for i in range(65):
        op = D(100 + i if rising else 200 - i)
        close = op + (1 if rising else -1)
        rows[at(i)] = Minute(symbol, at(i), at(i + 3), (op, op + 2, op - 2, close), D(10))
    return SessionInputs(symbol, OPEN, at(390), rows)


def changed(value, offset, **updates):
    rows = dict(value.minutes)
    rows[at(offset)] = replace(rows[at(offset)], **updates)
    return replace(value, minutes=rows)


def chart(value, offset):
    return build_chart_inputs(value, at(offset), at(offset + 2))


class ReadOnlyKeys(Mapping):
    def __init__(self, values, keys):
        self.values = values
        self.remaining = list(keys)

    def __getitem__(self, key):
        assert self.remaining and self.remaining.pop(0) == key, f"unexpected lookup {key}"
        return self.values[key]

    def __iter__(self):
        raise AssertionError("whole mapping iteration")

    def __len__(self):
        raise AssertionError("whole mapping length")


def test_opening_latest15_only_reads_completed_prefix():
    value = session()
    bounded = ReadOnlyKeys(value.minutes, map(at, range(15)))
    result = chart(replace(value, minutes=bounded), 15)
    assert not bounded.remaining
    assert result.status == "available"
    assert len(result.sequence) == 60
    assert result.price_states[:45] == ("preopening",) * 45
    assert result.sequence[:45] == ((0.0,) * 11 + (1.0,),) * 45
    assert result.sequence[45] == pytest.approx((0, 2, -2, 1, 1, 4, 0.75, 0, 0.69314718056, 1, 1, 0))


def test_optional_price_gap_and_volume_zero_keep_separate_masks():
    value = changed(session(), 0, ohlc=None, price_state="halt", shares=D(0))
    value = changed(value, 1, shares=None, volume_state="transport_error")
    result = chart(value, 20)
    assert result.status == "available"
    assert result.price_states[39:42] == ("preopening", "price_halt", "observed")
    assert result.volume_states[39:42] == ("preopening", "observed", "transport_error")
    assert result.sequence[39][8:] == (0, 0, 0, 1)
    assert result.sequence[40][8:] == (0, 1, 0, 0)
    assert result.sequence[41][8:] == (0, 0, 1, 0)


def test_late_required_payload_is_unavailable_then_observed_at_later_cutoff():
    late = changed(session(), 14, available_at=at(18))
    result = chart(late, 15)
    assert result.status == "unavailable" and result.sequence is None
    assert result.price_states[-1] == "minute_available_after_cutoff"
    assert result.volume_states[-1] == "minute_available_after_cutoff"
    assert result.price_issues[-1].minute == at(14)
    assert chart(late, 20).status == "available"

    hidden_invalid = changed(late, 14, ohlc=(D(-1),) * 4)
    assert chart(hidden_invalid, 15).status == "unavailable"
    with pytest.raises(InputRefusal, match="invalid_exact_price"):
        chart(hidden_invalid, 20)


def test_rolling_anchor_renormalizes_same_minute_when_window_moves():
    value = session()
    assert chart(value, 60).sequence[5][0] == 5.0
    assert chart(value, 65).sequence[0][0] == 0.0


def test_peer_known_counts_use_original_denominator_and_optional_gaps():
    value = session()
    a = changed(session("A"), 0, ohlc=None, price_state="missing")
    b = session("B", rising=False)
    incomplete = changed(session("C"), 19, available_at=at(23))
    supplied = ReadOnlyKeys({"A": a, "B": b, "C": incomplete, "TARGET": value}, ("A", "B", "C", "D"))
    result = build_peer_inputs(value, at(20), at(22), expected_peer_ids=("D", "C", "B", "A"),
                               peer_sessions=supplied, minimum_known=2)
    assert not supplied.remaining
    assert result.status == "available"
    assert result.expected_peer_ids == ("A", "B", "C", "D")
    assert result.common_peer_ids == ("A", "B")
    assert result.denominator == 4
    assert result.known_counts[39:42] == (0, 1, 2)
    assert result.sequence[39:41] == ((0, 0, 0, 0, 0, 1), (0, 0, 0, 0.25, 0, 0))
    oc = 50 * (1 / 101 - 1 / 199)
    cumulative = 50 * (1 / 101 - 2 / 200)
    assert result.sequence[41] == pytest.approx((oc, cumulative, 0.5, 0.5, 1, 0))


def test_peer_unknown_roster_and_below_minimum_have_distinct_support():
    value = session()
    unknown = build_peer_inputs(value, at(15), at(17), expected_peer_ids=None,
                                peer_sessions=ReadOnlyKeys({}, ()), minimum_known=2)
    known = build_peer_inputs(value, at(15), at(17), expected_peer_ids=("A", "B"),
                              peer_sessions={"A": session("A")}, minimum_known=2)
    assert unknown.status == known.status == "unavailable"
    assert unknown.sequence is known.sequence is None
    assert unknown.denominator is unknown.known_counts is None
    assert known.denominator == 2 and known.known_counts[-15:] == (1,) * 15


def test_invalid_decision_and_same_target_peer_roster_refuse():
    value = session()
    with pytest.raises(InputRefusal, match="invalid_eg_input_bar_end"):
        chart(value, 16)
    with pytest.raises(InputRefusal, match="invalid_eg_input_decision_at"):
        build_chart_inputs(value, at(15), at(18))
    with pytest.raises(InputRefusal, match="excluding_target"):
        build_peer_inputs(value, at(15), at(17), expected_peer_ids=(value.symbol,),
                          peer_sessions={}, minimum_known=1)
