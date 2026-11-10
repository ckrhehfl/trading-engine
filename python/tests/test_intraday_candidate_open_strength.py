from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_open_strength as study
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2025-11-13T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2025-11-13T16:30:00+09:00")
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(20))


def _bar(symbol, offset, value, *, low=None):
    at = OPEN + offset * MINUTE
    price = Decimal(value)
    return Minute(symbol, at, at + 3 * MINUTE,
                  (price, price, price if low is None else Decimal(low), price), None, volume_state="missing")


def _sessions(endpoints, *, stops=None):
    stops = {} if stops is None else stops
    sessions = {}
    for index, symbol in enumerate(SYMBOLS):
        prices = endpoints.get(index, (None, None, None))
        bars = {OPEN + offset * MINUTE: _bar(symbol, offset, price)
                for offset, price in zip((0, 29, 59), prices, strict=True) if price is not None}
        if index in stops:
            for offset in range(45, 59):
                bars[OPEN + offset * MINUTE] = _bar(symbol, offset, prices[-1],
                                                    low=stops[index] if offset == 51 else None)
        sessions[symbol] = SessionInputs(symbol, OPEN, CLOSE, bars)
    return sessions


def _checked(sessions):
    result = study.select_day(sessions)
    assert result == study.reference_day(sessions)
    assert result["decision_at"] == "2025-11-13T11:02:00+09:00"
    assert set(result["symbols"]) == set(SYMBOLS)
    return result


def test_exact_strength_and_flat_late_half_are_included():
    endpoints = {0: (10000, 10066, 10066), 1: (10000, 10060, "10065.99999999999999999999999999999"),
                 2: (10000, 10070, 10069), **dict.fromkeys(range(3, 11), (None, 10000, 9950))}
    rows = _checked(_sessions(endpoints, stops={0: 10055}))["symbols"]
    assert rows[SYMBOLS[0]] == {
        "rank_state": "select", "gated_state": "select", "order_state": "ready", "reason": "order_ready",
        "limit": "10066", "stop": "10055", "first_open": "10000", "mid_close": "10066",
        "last_close": "10066", "own_return": "33/5000", "peer_count": 10,
        "peer_median": "-1/200", "peer_state": "select",
    }
    assert rows[SYMBOLS[1]]["reason"] == "first_hour_below_threshold"
    assert rows[SYMBOLS[2]]["reason"] == "late_half_did_not_hold"


def test_peers_exclude_target_and_need_eight_complete_pairs():
    endpoints = {0: (10000, 10000, 10066),
                 **dict.fromkeys(range(1, 5), (None, 10000, 9900)),
                 **dict.fromkeys(range(5, 9), (None, 10000, 10050))}
    result = _checked(_sessions(endpoints, stops={0: 10000}))
    assert result["observed_peer_members"] == 9
    row = result["symbols"][SYMBOLS[0]]
    assert (row["peer_count"], row["peer_median"], row["gated_state"]) == (8, "-1/400", "select")
    assert result["symbols"][SYMBOLS[8]]["rank_state"] == "unresolved"
    endpoints.pop(8)
    row = _checked(_sessions(endpoints, stops={0: 10000}))["symbols"][SYMBOLS[0]]
    assert (row["peer_count"], row["peer_median"], row["peer_state"], row["gated_state"]) == (7, None, "unresolved", "unresolved")
    assert (row["rank_state"], row["order_state"]) == ("select", "ready")


def test_peer_median_zero_is_known_no_trade_even_if_structure_is_missing():
    endpoints = {0: (10000, 10000, 10066),
                 **dict.fromkeys(range(1, 5), (None, 10000, 9900)),
                 **dict.fromkeys(range(5, 9), (None, 10000, 10100))}
    row = _checked(_sessions(endpoints))["symbols"][SYMBOLS[0]]
    assert (row["rank_state"], row["order_state"], row["reason"]) == ("select", "unresolved", "structure_prices_unavailable")
    assert (row["limit"], row["stop"]) == ("10066", None)
    assert (row["peer_median"], row["peer_state"], row["gated_state"]) == ("0", "decline", "decline")


def test_missing_midpoint_preserves_first_to_last_return_but_is_unknown():
    endpoints = {0: (10000, None, 10066), **dict.fromkeys(range(1, 9), (None, 10000, 9900))}
    result = _checked(_sessions(endpoints, stops={0: 10055}))
    assert result["observed_peer_members"] == 8
    row = result["symbols"][SYMBOLS[0]]
    assert (row["first_open"], row["mid_close"], row["last_close"], row["own_return"]) == ("10000", None, "10066", "33/5000")
    assert (row["rank_state"], row["gated_state"], row["order_state"]) == ("unresolved", "unresolved", "retain")
    assert row["peer_state"] == "select"


def test_structure_must_use_all_fifteen_lows_and_touching_limit_is_no_order():
    sessions = _sessions({0: (10000, 10066, 10066)}, stops={0: 10066})
    row = _checked(sessions)["symbols"][SYMBOLS[0]]
    assert (row["limit"], row["stop"], row["order_state"], row["reason"]) == ("10066", "10066", "decline", "limit_at_stop")
    sessions[SYMBOLS[0]].minutes.pop(OPEN + 45 * MINUTE)
    row = _checked(sessions)["symbols"][SYMBOLS[0]]
    assert (row["stop"], row["order_state"]) == (None, "unresolved")


@pytest.mark.parametrize("changes", (
    {"available_at": OPEN + 63 * MINUTE}, {"available_at": None}, {"price_state": "conflict"},
    {"ohlc": tuple(map(Decimal, (0, 0, 0, 0)))}, {"ohlc": tuple(map(Decimal, (10000, 9999, 9950, 10066)))},
    {"symbol": SYMBOLS[-1]},
))
def test_invalid_last_bar_is_neither_known_zero_nor_peer_evidence(changes):
    sessions = _sessions({0: (10000, 10000, 10066), 1: (None, 10000, 9900)}, stops={0: 10055})
    bars = sessions[SYMBOLS[0]].minutes
    last_at = OPEN + 59 * MINUTE
    bars[last_at] = replace(bars[last_at], **changes)
    result = _checked(sessions)
    assert result["observed_peer_members"] == 1
    row = result["symbols"][SYMBOLS[0]]
    assert (row["last_close"], row["own_return"], row["rank_state"]) == (None, None, "unresolved")
    assert result["symbols"][SYMBOLS[1]]["peer_count"] == 0


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
def test_only_fixed_causal_bars_are_read(selector):
    sessions = _sessions({0: (10000, 10000, 10066), 1: (10000, 10000, 10001)}, stops={0: 10055})
    for index, (symbol, session) in enumerate(sessions.items()):
        offsets = study.ENTRY_OFFSETS if index == 0 else (0, 29, 59)
        sessions[symbol] = replace(session, minutes=_CausalPrices(session.minutes, offsets))
    selector(sessions)
    for session in sessions.values():
        assert set(session.minutes.reads) == session.minutes.allowed


def test_roster_membership_and_shared_opening_are_required():
    sessions = _sessions({})
    for selector in (study.select_day, study.reference_day):
        with pytest.raises(ValueError, match="exactly 20 roster members"):
            selector({symbol: sessions[symbol] for symbol in SYMBOLS[1:]})
        shifted = {**sessions, SYMBOLS[0]: replace(sessions[SYMBOLS[0]], opening=OPEN + MINUTE)}
        with pytest.raises(ValueError, match="do not share one opening"):
            selector(shifted)
