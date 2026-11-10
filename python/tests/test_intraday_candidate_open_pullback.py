from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_open_pullback as study
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2025-11-13T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2025-11-13T16:30:00+09:00")
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(20))


def _bar(symbol, offset, price, low=None):
    at = OPEN + offset * MINUTE
    price = Decimal(price)
    return Minute(symbol, at, at + 3 * MINUTE, (price, price, price if low is None else Decimal(low), price),
                  None, volume_state="missing")


def _sessions(endpoints=(10000, 10066, 10033), *, rebound=10032, lows=None):
    symbol = SYMBOLS[0]
    lows = {} if lows is None else lows
    prices = dict.fromkeys(range(45, 59), endpoints[2])
    prices.update(zip((0, 29, 54, 59), (*endpoints[:2], rebound, endpoints[2]), strict=True))
    bars = {OPEN + offset * MINUTE: _bar(symbol, offset, price, lows.get(offset))
            for offset, price in prices.items() if price is not None}
    return {name: SessionInputs(name, OPEN, CLOSE, bars if name == symbol else {}) for name in SYMBOLS}


def _checked(sessions):
    result = study.select_day(sessions)
    assert result == study.reference_day(sessions)
    assert result["decision_at"] == "2025-11-13T11:02:00+09:00"
    assert tuple(result["symbols"]) == SYMBOLS
    return result["symbols"][SYMBOLS[0]]


def test_exact_strength_and_half_gain_are_included():
    assert _checked(_sessions(lows={45: 10010, 59: 10015})) == {
        "rank_state": "select", "gated_state": "select", "order_state": "ready", "reason": "order_ready",
        "limit": "10033", "stop": "10010", "first_open": "10000", "mid_close": "10066", "last_close": "10033",
        "rebound_reference_close": "10032", "rebound_state": "select",
    }


@pytest.mark.parametrize(("endpoints", "reason", "state"), (
    ((10000, "10065.99999999999999999999999999999", 10033), "first_half_below_threshold", "decline"),
    ((10000, 10066, 10066), "last_close_not_below_mid", "decline"),
    ((10000, 10066, 10067), "last_close_not_below_mid", "decline"),
    ((10000, 10066, "10032.99999999999999999999999999999"), "pullback_below_half_gain", "decline"),
    ((10000, None, 10033), "own_prices_unavailable", "unresolved"),
    ((None, 10066, 10033), "own_prices_unavailable", "unresolved"),
    ((10000, 10066, None), "own_prices_unavailable", "unresolved"),
))
def test_own_condition_boundaries_retain_order_and_do_not_evaluate_rebound(endpoints, reason, state):
    row = _checked(_sessions(endpoints))
    assert (row["rank_state"], row["gated_state"], row["reason"]) == (state, state, reason)
    assert (row["order_state"], row["limit"], row["stop"]) == ("retain", None, None)
    assert (row["rebound_state"], row["rebound_reference_close"]) == ("not_applicable", None)


@pytest.mark.parametrize("rebound", (10033, 10034))
def test_equal_or_declining_rebound_leaves_baseline_order_ready(rebound):
    row = _checked(_sessions(rebound=rebound, lows={59: 10020}))
    assert (row["rank_state"], row["gated_state"], row["rebound_state"]) == ("select", "decline", "decline")
    assert (row["order_state"], row["stop"], row["rebound_reference_close"]) == ("ready", "10020", str(rebound))


def test_missing_rebound_and_missing_other_geometry_are_distinct():
    row = _checked(_sessions(rebound=None))
    assert (row["rank_state"], row["gated_state"], row["rebound_state"]) == ("select", "unresolved", "unresolved")
    assert (row["order_state"], row["limit"], row["stop"]) == ("unresolved", "10033", None)
    assert row["rebound_reference_close"] is None

    sessions = _sessions(rebound=10034)
    sessions[SYMBOLS[0]].minutes.pop(OPEN + 45 * MINUTE)
    row = _checked(sessions)
    assert (row["rank_state"], row["gated_state"], row["rebound_state"]) == ("select", "decline", "decline")
    assert (row["order_state"], row["limit"], row["stop"]) == ("unresolved", "10033", None)
    assert row["reason"] == "structure_prices_unavailable"


def test_touching_stop_is_known_no_order():
    row = _checked(_sessions(rebound=10033))
    assert (row["limit"], row["stop"], row["order_state"], row["reason"]) == (
        "10033", "10033", "decline", "limit_at_stop",
    )


@pytest.mark.parametrize("changes", (
    {"available_at": OPEN + 63 * MINUTE},
    {"available_at": OPEN + 56 * MINUTE},
    {"available_at": None},
    {"price_state": "conflict"},
    {"ohlc": tuple(map(Decimal, (0, 0, 0, 0)))},
    {"ohlc": tuple(map(Decimal, (10032, 10031, 10029, 10032)))},
    {"symbol": SYMBOLS[-1]},
))
def test_invalid_rebound_bar_is_unknown_for_both_gate_and_geometry(changes):
    sessions = _sessions()
    bars = sessions[SYMBOLS[0]].minutes
    rebound_at = OPEN + 54 * MINUTE
    bars[rebound_at] = replace(bars[rebound_at], **changes)
    row = _checked(sessions)
    assert (row["rank_state"], row["gated_state"], row["order_state"]) == ("select", "unresolved", "unresolved")
    assert (row["rebound_state"], row["rebound_reference_close"]) == ("unresolved", None)


class _CausalPrices(Mapping):
    def __init__(self, values, offsets):
        self.values = values
        self.allowed = {OPEN + offset * MINUTE for offset in offsets}
        self.reads = []

    def __getitem__(self, at):
        assert at in self.allowed
        self.reads.append(at)
        return self.values[at]

    def __iter__(self):
        raise AssertionError("price path must not be traversed")

    def __len__(self):
        return len(self.values)


@pytest.mark.parametrize("selector", (study.select_day, study.reference_day))
def test_only_eligible_symbols_read_rebound_and_geometry_and_none_read_execution(selector):
    sessions = _sessions()
    rejected, unknown = SYMBOLS[1:3]
    for name, endpoints in ((rejected, (10000, 10065, 10033)), (unknown, (10000, None, 10033))):
        session = sessions[name]
        session.minutes.update({OPEN + offset * MINUTE: _bar(name, offset, price)
                                for offset, price in zip((0, 29, 59), endpoints, strict=True) if price is not None})
    for name, session in sessions.items():
        session.minutes[OPEN + 63 * MINUTE] = _bar(name, 63, 10031)
        offsets = study.ENTRY_OFFSETS if name == SYMBOLS[0] else (0, 29, 59)
        sessions[name] = replace(session, minutes=_CausalPrices(session.minutes, offsets))
    selector(sessions)
    for session in sessions.values():
        assert set(session.minutes.reads) == session.minutes.allowed
        assert len(session.minutes.reads) == len(session.minutes.allowed)


def test_roster_membership_and_shared_opening_are_required():
    sessions = _sessions()
    for selector in (study.select_day, study.reference_day):
        with pytest.raises(ValueError, match="exactly 20 roster members"):
            selector({symbol: sessions[symbol] for symbol in SYMBOLS[1:]})
        shifted = {**sessions, SYMBOLS[0]: replace(sessions[SYMBOLS[0]], opening=OPEN + MINUTE)}
        with pytest.raises(ValueError, match="do not share one opening"):
            selector(shifted)
