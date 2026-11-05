"""Invented-only exact EF inputs; never read market archives or model outputs."""
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_inputs as chart
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs


OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))
D = Decimal


def session(symbol="TARGET", opening=OPEN, *, count=390, closing_minutes=390, flat=False):
    rows = {}
    for index in range(count):
        at = opening + index * MINUTE
        op = D(100 if flat else 100 + index)
        rows[at] = Minute(symbol, at, at + 3 * MINUTE, (op, op + 2, op - 1, op + 1), D(10))
    return SessionInputs(symbol, opening, opening + closing_minutes * MINUTE, rows)


def changed(value, index, **updates):
    at = value.opening + index * MINUTE
    rows = dict(value.minutes)
    rows[at] = replace(rows[at], **updates)
    return replace(value, minutes=rows)


def built(value=None, offset=60, **kwargs):
    value = session() if value is None else value
    end = value.opening + offset * MINUTE
    return chart.build_chart_inputs(value, end, end + 2 * MINUTE, **kwargs)


class PrefixOnly(Mapping):
    def __init__(self, rows, boundary):
        self.rows, self.boundary = rows, boundary

    def __getitem__(self, key):
        assert key < self.boundary, "future suffix accessed"
        return self.rows[key]

    def __iter__(self):
        raise AssertionError("whole-session iteration")

    def __len__(self):
        raise AssertionError("whole-session length")


def test_exact_latest60_channels_and_rolling_anchor():
    result = built(offset=90)
    assert result.status == "available"
    assert len(result.sequence) == 60
    first = result.sequence[0]
    assert first.start_at == OPEN + 30 * MINUTE
    assert first.channels == (Fraction(0), Fraction(2, 130), Fraction(-1, 130), Fraction(1, 130),
                              Fraction(1, 130), Fraction(3, 130), Fraction(2, 3), Fraction(0))
    assert result.sequence[-1].channels[3] == Fraction(190, 130) - 1
    assert all(type(number) is Fraction for point in result.sequence for number in point.channels)


def test_chart_records_are_immutable_and_have_no_market_fields():
    result = built()
    with pytest.raises(FrozenInstanceError):
        result.status = "unavailable"
    assert isinstance(result.sequence, tuple) and isinstance(result.coarse, tuple)
    assert not any("peer" in field.name or "market" in field.name for field in fields(result))


def test_zero_range_has_explicit_flag_and_zero_location_payload():
    value = changed(session(), 59, ohlc=(D(159),) * 4)
    assert built(value).sequence[-1].channels[4:] == (Fraction(0), Fraction(0), Fraction(0), Fraction(1))


def test_valid_zero_volume_is_distinct_from_unknown_and_not_price_admission():
    value = changed(session(), 0, shares=D(0))
    value = changed(value, 1, shares=None, volume_state="transport_error")
    result = built(value)
    assert result.status == "available"
    assert result.volume[0] == chart.VolumeObservation(OPEN, Fraction(0), "observed")
    assert result.volume[1] == chart.VolumeObservation(OPEN + MINUTE, None, "transport_error")


@pytest.mark.parametrize("updates", [
    {"shares": D(-1)}, {"shares": D("NaN")}, {"shares": None},
    {"volume_state": "missing"}, {"volume_state": "invented", "shares": None},
])
def test_malformed_optional_volume_refuses(updates):
    with pytest.raises(InputRefusal):
        built(changed(session(), 0, **updates))


def test_coarse_grid_masks_preopening_slots_without_previous_day_backfill():
    previous = session(opening=OPEN - timedelta(days=1))
    result = built(expected_previous_opening=previous.opening, previous_session=previous)
    assert len(result.coarse) == 24
    assert all(row.status == "not_yet_in_session" and row.ohlc is None for row in result.coarse[:12])
    assert result.coarse[12].start_at == OPEN
    assert result.coarse[12].ohlc == (Fraction(100), Fraction(106), Fraction(99), Fraction(105))
    assert result.previous.status == "available"


def test_missing_older_coarse_price_does_not_erase_complete_latest60():
    value = session()
    rows = dict(value.minutes)
    del rows[OPEN + MINUTE]
    result = built(replace(value, minutes=rows), offset=120)
    assert result.status == "available" and result.issues == ()
    assert result.coarse[0].status == "unavailable" and result.coarse[0].ohlc is None
    assert result.coarse[0].issues[0].reason == "missing_minute"


@pytest.mark.parametrize("state", ["missing", "halt", "no_trade", "transport_error", "unit_unknown", "conflict"])
def test_latest_price_absence_retains_unavailable_not_zero(state):
    result = built(changed(session(), 59, ohlc=None, price_state=state))
    assert result.status == "unavailable" and result.sequence is None
    assert result.issues[0].reason == "price_" + state
    assert result.coarse[-1].ohlc is None


def test_absent_minute_preserves_missing_volume_and_required_issue():
    value = session()
    rows = dict(value.minutes)
    del rows[OPEN]
    result = built(replace(value, minutes=rows))
    assert result.sequence is None
    assert result.volume[0].state == "missing" and result.volume[0].shares is None
    assert result.issues[0].reason == "missing_minute"


def test_no_current_future_suffix_read_or_iteration():
    value = session()
    value = replace(value, minutes=PrefixOnly(value.minutes, OPEN + 60 * MINUTE))
    assert built(value).status == "available"


@pytest.mark.parametrize("offset", [59, 61, 390 + 5])
def test_invalid_update_geometry_refuses(offset):
    with pytest.raises(InputRefusal):
        built(offset=offset)


@pytest.mark.parametrize("offset", [65, 305, 315])
def test_entry_grid_has_fifteen_minute_steps_and_300_cutoff(offset):
    with pytest.raises(InputRefusal, match="entry_grid"):
        built(offset=offset, entry_grid=True)


@pytest.mark.parametrize("offset", [60, 75, 300])
def test_registered_entry_grid(offset):
    assert built(offset=offset, entry_grid=True).status == "available"


@pytest.mark.parametrize("offset", [65, 305, 390])
def test_holding_updates_continue_on_five_minute_grid_through_supplied_close(offset):
    assert built(offset=offset).status == "available"


def test_decision_clock_and_future_availability_refuse():
    value = session()
    with pytest.raises(InputRefusal, match="bar_end_plus_two"):
        chart.build_chart_inputs(value, OPEN + 60 * MINUTE, OPEN + 61 * MINUTE)
    with pytest.raises(InputRefusal, match="unavailable_at_decision"):
        built(changed(value, 59, available_at=OPEN + 63 * MINUTE))
    with pytest.raises(InputRefusal, match="unavailable_at_decision"):
        built(changed(value, 0, available_at=OPEN))


@pytest.mark.parametrize("updates", [
    {"ohlc": (D(100), D(99), D(98), D(100))},
    {"ohlc": (D("NaN"),) * 4}, {"ohlc": (D(0),) * 4},
    {"symbol": "OTHER"}, {"start_at": OPEN + MINUTE},
    {"price_state": "missing"}, {"ohlc": None},
])
def test_malformed_ohlc_or_identity_refuses(updates):
    with pytest.raises(InputRefusal):
        built(changed(session(), 0, **updates))


def test_exact_previous_levels_use_complete_explicit_cash_session_only():
    previous = session(opening=OPEN - timedelta(days=1), count=375, closing_minutes=375)
    previous = replace(previous, minutes=PrefixOnly(previous.minutes, previous.closing))
    result = built(expected_previous_opening=previous.opening, previous_session=previous)
    assert (result.previous.high, result.previous.low, result.previous.close) == (
        Fraction(476), Fraction(99), Fraction(475))


def test_absent_prior_is_unknown_and_incomplete_prior_does_not_use_partial_extrema():
    expected = OPEN - timedelta(days=1)
    result = built(expected_previous_opening=expected)
    assert result.previous.status == "unavailable" and result.previous.high is None
    previous = session(opening=expected, count=389)
    result = built(expected_previous_opening=expected, previous_session=previous)
    assert result.status == "available" and result.previous.status == "unavailable"
    assert result.previous.high is result.previous.low is result.previous.close is None
    assert result.previous.issues[0].minute == expected + 389 * MINUTE


def test_previous_identity_day_and_expected_calendar_mismatch_refuse():
    expected = OPEN - timedelta(days=1)
    for prior, declared in ((session(opening=expected), None),
                            (session(opening=expected - timedelta(days=1)), expected),
                            (session("OTHER", expected), expected),
                            (session(), OPEN)):
        with pytest.raises(InputRefusal):
            built(expected_previous_opening=declared, previous_session=prior)
    with pytest.raises(InputRefusal):
        built(expected_previous_opening=OPEN)


def test_previous_price_availability_is_checked_at_current_decision():
    expected = OPEN - timedelta(days=1)
    previous = changed(session(opening=expected), 0, available_at=OPEN + 63 * MINUTE)
    with pytest.raises(InputRefusal, match="unavailable_at_decision"):
        built(expected_previous_opening=expected, previous_session=previous)


def peer_inputs(*, count=5):
    names = tuple(f"PEER{index}" for index in range(count))
    return names, {name: session(name, flat=True) for name in names}


def peer_built(names, peers, *, current=None, offset=60):
    current = session() if current is None else current
    end = current.opening + offset * MINUTE
    return chart.build_peer_context(current, end, end + 2 * MINUTE,
                                    expected_peer_ids=names, peer_sessions=peers)


def test_peers_share_whole_window_support_and_preserve_original_denominator():
    names, peers = peer_inputs(count=6)
    peers[names[-1]] = changed(peers[names[-1]], 59, ohlc=None, price_state="missing")
    result = peer_built(names, peers)
    assert result.status == "available" and result.denominator == 6
    assert result.common_peer_ids == names[:5]
    assert result.minute_oc_return == (Fraction(1, 100),) * 60
    assert result.cumulative_anchored_return == (Fraction(1, 100),) * 60
    assert result.positive_fraction == (Fraction(1),) * 60
    assert result.issues[0].symbol == names[-1]


def test_peer_cumulative_channels_use_each_peers_own_anchor_and_strict_positive():
    names, peers = peer_inputs()
    closes = (D(110), D(110), D(90), D(100), D(120))
    for name, close in zip(names, closes, strict=True):
        peers[name] = changed(peers[name], 59,
                              ohlc=(D(105), max(D(105), close), min(D(105), close), close))
    result = peer_built(names, peers)
    assert result.minute_oc_return[-1] == Fraction(1, 105)
    assert result.cumulative_anchored_return[-1] == Fraction(3, 50)
    assert result.positive_fraction[-1] == Fraction(3, 5)


def test_fewer_than_five_complete_peers_is_unavailable_with_missing_identity():
    names, peers = peer_inputs()
    del peers[names[-1]]
    result = peer_built(names, peers)
    assert result.status == "unavailable" and result.denominator == 5
    assert result.minute_oc_return is result.cumulative_anchored_return is result.positive_fraction is None
    assert result.issues[0].symbol == names[-1]
    assert result.issues[-1].reason == "fewer_than_five_complete_original_peers"


def test_peer_suffix_and_volume_are_not_consumed():
    names, peers = peer_inputs()
    for name in names:
        peer = changed(peers[name], 0, shares=None, volume_state="missing")
        peers[name] = replace(peer, minutes=PrefixOnly(peer.minutes, OPEN + 60 * MINUTE))
    assert peer_built(names, peers).status == "available"


def test_target_duplicate_and_wrong_peer_calendar_are_rejected():
    names, peers = peer_inputs()
    for invalid in (names + ("TARGET",), names + (names[0],)):
        with pytest.raises(InputRefusal, match="excluding_target"):
            peer_built(invalid, peers)
    peers[names[0]] = session(names[0], OPEN - timedelta(days=1))
    with pytest.raises(InputRefusal, match="calendar_differs"):
        peer_built(names, peers)


def test_peer_future_availability_is_an_integrity_refusal():
    names, peers = peer_inputs()
    peers[names[0]] = changed(peers[names[0]], 59, available_at=OPEN + 63 * MINUTE)
    with pytest.raises(InputRefusal, match="unavailable_at_decision"):
        peer_built(names, peers)


def test_contextual_record_preserves_exact_same_chart_object_and_requires_same_clock():
    names, peers = peer_inputs()
    own = built()
    market = peer_built(names, peers)
    contextual = chart.with_peer_context(own, market)
    assert contextual.chart is own and contextual.peer is market
    assert contextual.status == "available"
    with pytest.raises(InputRefusal, match="coordinates_differ"):
        chart.with_peer_context(built(offset=65), market)
