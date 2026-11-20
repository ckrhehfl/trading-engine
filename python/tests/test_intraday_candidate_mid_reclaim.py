from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_mid_reclaim as study
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2025-11-13T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2025-11-13T16:30:00+09:00")
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(20))
TARGET = SYMBOLS[0]


def _bar(symbol, offset, price, *, low=None):
    at = OPEN + offset * MINUTE
    price = Decimal(price)
    low = price if low is None else Decimal(low)
    return Minute(symbol, at, at + 3 * MINUTE, (price, price, low, price), None, volume_state="missing")


def _sessions(*, break_close=99, reclaim_close=103, held_117="100.125", held_118=102):
    prices = dict.fromkeys(range(30, 60), 101)
    prices.update({114: break_close, 115: 100, 116: 100, 117: held_117, 118: held_118, 119: reclaim_close})
    lows = {59: 100, 116: "98.25"}
    bars = {OPEN + offset * MINUTE: _bar(TARGET, offset, price, low=lows.get(offset))
            for offset, price in prices.items() if price is not None}
    return {name: SessionInputs(name, OPEN, CLOSE, bars if name == TARGET else {}) for name in SYMBOLS}


def _checked(sessions):
    result = study.select_day(sessions)
    assert result == study.reference_day(sessions)
    assert result["decision_at"] == "2025-11-13T12:02:00+09:00"
    assert tuple(result["symbols"]) == SYMBOLS
    return result["symbols"][TARGET]


def test_complete_reclaim_freezes_exact_support_geometry_and_hold_evidence():
    assert _checked(_sessions()) == {
        "rank_state": "select", "gated_state": "select", "order_state": "ready", "reason": "order_ready",
        "limit": "103", "stop": "393/4", "support_low": "100", "break_close": "99", "reclaim_close": "103",
        "hold_close_117": "801/8", "hold_close_118": "102", "hold_state": "select",
    }


@pytest.mark.parametrize(("break_close", "reclaim_close", "state", "reason"), (
    (100, 103, "decline", "break_close_not_below_support"),
    (101, 103, "decline", "break_close_not_below_support"),
    (99, 100, "decline", "reclaim_close_not_above_support"),
    (99, 98, "decline", "reclaim_close_not_above_support"),
    (100, None, "unresolved", "own_prices_unavailable"),
    (None, 100, "unresolved", "own_prices_unavailable"),
))
def test_reclaim_requires_known_strict_endpoints_before_testing_hold(break_close, reclaim_close, state, reason):
    row = _checked(_sessions(break_close=break_close, reclaim_close=reclaim_close))
    assert (row["rank_state"], row["gated_state"], row["order_state"], row["reason"]) == (state, state, "retain", reason)
    assert row["limit"] is row["stop"] is row["hold_close_117"] is row["hold_close_118"] is None
    assert row["hold_state"] == "not_applicable"


@pytest.mark.parametrize("missing", (30, 42, 59))
def test_every_anchor_low_is_required(missing):
    sessions = _sessions()
    del sessions[TARGET].minutes[OPEN + missing * MINUTE]
    row = _checked(sessions)
    assert row["rank_state"] == row["gated_state"] == "unresolved"
    assert (row["support_low"], row["break_close"], row["reclaim_close"]) == (None, "99", "103")


@pytest.mark.parametrize(("held_117", "held_118", "hold", "order"), (
    (100, 102, "decline", "ready"),
    (102, 99, "decline", "ready"),
    (100, None, "decline", "unresolved"),
    (None, 100, "decline", "unresolved"),
    (None, 102, "unresolved", "unresolved"),
    (101, None, "unresolved", "unresolved"),
    (None, None, "unresolved", "unresolved"),
))
def test_known_hold_failure_takes_precedence_over_missing_other_hold_close(held_117, held_118, hold, order):
    row = _checked(_sessions(held_117=held_117, held_118=held_118))
    assert (row["rank_state"], row["gated_state"], row["hold_state"], row["order_state"]) == ("select", hold, hold, order)
    assert row["limit"] == "103"
    assert row["stop"] == ("393/4" if order == "ready" else None)
    assert row["hold_close_117"] == (None if held_117 is None else str(held_117))
    assert row["hold_close_118"] == (None if held_118 is None else str(held_118))


def test_known_hold_does_not_resolve_an_incomplete_structure_window():
    sessions = _sessions()
    del sessions[TARGET].minutes[OPEN + 115 * MINUTE]
    row = _checked(sessions)
    assert row["rank_state"] == row["gated_state"] == row["hold_state"] == "select"
    assert (row["order_state"], row["reason"], row["limit"], row["stop"]) == (
        "unresolved", "structure_prices_unavailable", "103", None,
    )


@pytest.mark.parametrize("changes", (
    {"available_at": OPEN + 123 * MINUTE},
    {"available_at": OPEN + 121 * MINUTE},
    {"available_at": None},
    {"price_state": "conflict"},
    {"ohlc": tuple(map(Decimal, (103, 102, 101, 103)))},
    {"symbol": SYMBOLS[1]},
))
def test_invalid_reclaim_quote_is_unresolved(changes):
    sessions = _sessions()
    bars = sessions[TARGET].minutes
    at = OPEN + 119 * MINUTE
    bars[at] = replace(bars[at], **changes)
    row = _checked(sessions)
    assert (row["rank_state"], row["gated_state"], row["reclaim_close"]) == ("unresolved", "unresolved", None)


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
def test_reads_only_registered_offsets_and_defers_extra_geometry_until_own_selection(selector):
    sessions = _sessions()
    assert study.ENTRY_OFFSETS == (*range(30, 60), *range(114, 120))
    for name, session in sessions.items():
        session.minutes[OPEN + 120 * MINUTE] = _bar(name, 120, 200)
        session.minutes[OPEN + 123 * MINUTE] = _bar(name, 123, 103)
        offsets = study.ENTRY_OFFSETS if name == TARGET else (*range(30, 60), 114, 119)
        sessions[name] = replace(session, minutes=_CausalPrices(session.minutes, offsets))
    selector(sessions)
    for session in sessions.values():
        assert len(session.minutes.reads) == len(session.minutes.allowed)
        assert set(session.minutes.reads) == session.minutes.allowed


@pytest.mark.parametrize("selector", (study.select_day, study.reference_day))
def test_shared_roster_session_must_contain_decision_on_same_date(selector):
    sessions = _sessions()
    with pytest.raises(ValueError, match="exactly 20 roster members"):
        selector({name: sessions[name] for name in SYMBOLS[1:]})
    with pytest.raises(ValueError, match="do not share one opening and closing"):
        selector({**sessions, TARGET: replace(sessions[TARGET], opening=OPEN + MINUTE)})
    for invalid_close in (OPEN + 122 * MINUTE, OPEN + 153 * MINUTE, CLOSE + 1440 * MINUTE):
        with pytest.raises(ValueError, match="session geometry differs"):
            selector({**sessions, TARGET: replace(sessions[TARGET], closing=invalid_close)})
