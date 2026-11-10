from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_open_late_selection as study
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2025-11-13T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2025-11-13T16:30:00+09:00")
DECISION = CLOSE - 46 * MINUTE
OPENING_LAST = OPEN + 29 * MINUTE
LATE = tuple(DECISION - offset * MINUTE for offset in range(17, 2, -1))
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(20))


def _minute(symbol, at, op=10000, close=10000, *, low=None):
    op, close = Decimal(op), Decimal(close)
    low = min(op, close) if low is None else Decimal(low)
    return Minute(symbol, at, at + 3 * MINUTE, (op, max(op, close), low, close), None,
                  volume_state="missing")


def _sessions(morning, afternoon):
    sessions = {}
    for index, symbol in enumerate(SYMBOLS):
        bars = {}
        if index in morning:
            bars[OPEN] = _minute(symbol, OPEN)
            bars[OPENING_LAST] = _minute(symbol, OPENING_LAST, close=morning[index])
        if index in afternoon:
            bars.update({at: _minute(symbol, at, low=9950 if at == LATE[7] else None) for at in LATE})
            bars[LATE[-1]] = _minute(symbol, LATE[-1], close=afternoon[index])
        sessions[symbol] = SessionInputs(symbol, OPEN, CLOSE, bars)
    return sessions


def _checked(sessions):
    produced = study.select_day(sessions)
    assert produced == study.reference_day(sessions)
    return produced


def test_target_with_valid_opening_return_is_excluded_from_own_peer_quorum():
    result = _checked(_sessions(dict.fromkeys(range(10), 10050), {0: 10020, 10: 10020}))
    assert result["decision_at"] == "2025-11-13T15:44:00+09:00"
    assert result["observed_opening_members"] == 10
    assert result["symbols"][SYMBOLS[0]] == {
        "rank_state": "select", "gated_state": "unresolved", "order_state": "ready",
        "reason": "order_ready", "limit": "10020", "stop": "9950", "late_return": "1/500",
        "opening_first_open": "10000", "opening_last_close": "10050",
        "opening_return": "1/200", "opening_peer_count": 9, "opening_peer_median": None,
        "opening_gate": "unresolved",
    }
    absent_opening = result["symbols"][SYMBOLS[10]]
    assert absent_opening["opening_return"] is None
    assert (absent_opening["opening_peer_count"], absent_opening["opening_peer_median"],
            absent_opening["gated_state"]) == (10, "1/200", "select")


def test_own_return_can_change_others_median_but_never_its_own():
    morning = {0: 12000, **dict.fromkeys(range(1, 6), 10020), **dict.fromkeys(range(6, 11), 10040)}
    rows = _checked(_sessions(morning, {0: 10020, 1: 10020}))["symbols"]
    assert (rows[SYMBOLS[0]]["opening_peer_median"], rows[SYMBOLS[0]]["gated_state"]) == ("3/1000", "decline")
    assert (rows[SYMBOLS[1]]["opening_peer_median"], rows[SYMBOLS[1]]["gated_state"]) == ("1/250", "select")
    morning[0] = 9900
    changed = _checked(_sessions(morning, {0: 10020, 1: 10020}))["symbols"]
    assert changed[SYMBOLS[0]]["opening_peer_median"] == rows[SYMBOLS[0]]["opening_peer_median"]
    assert changed[SYMBOLS[1]]["gated_state"] == "decline"


def test_strict_threshold_uses_exact_median_and_late_return_must_be_positive():
    morning = {**dict.fromkeys(range(1, 6), 10032), **dict.fromkeys(range(6, 11), 10034)}
    afternoon = {0: 10020, 11: 10000, 12: 9999}
    rows = _checked(_sessions(morning, afternoon))["symbols"]
    assert (rows[SYMBOLS[0]]["opening_peer_median"], rows[SYMBOLS[0]]["gated_state"]) == ("33/10000", "decline")
    for index in range(6, 11):
        morning[index] = "10034.00000000000000000000000000000001"
    rows = _checked(_sessions(morning, afternoon))["symbols"]
    assert rows[SYMBOLS[0]]["gated_state"] == "select"
    for index in (11, 12, 13):
        row = rows[SYMBOLS[index]]
        assert row["opening_gate"] == "select"
        assert row["rank_state"] == row["gated_state"] == ("unresolved" if index == 13 else "decline")


@pytest.mark.parametrize("changes", (
    {"available_at": OPENING_LAST + 4 * MINUTE}, {"available_at": None}, {"price_state": "conflict"},
    {"ohlc": tuple(map(Decimal, (10000, 9999, 9950, 10040)))}, {"symbol": SYMBOLS[-1]},
))
def test_invalid_opening_endpoint_and_missing_required_late_minute_remain_unknown(changes):
    sessions = _sessions(dict.fromkeys(range(10), 10050), {10: 10020, 11: 10020})
    minutes = sessions[SYMBOLS[0]].minutes
    minutes[OPENING_LAST] = replace(minutes[OPENING_LAST], **changes)
    sessions[SYMBOLS[11]].minutes.pop(LATE[7])
    result = _checked(sessions)
    assert result["observed_opening_members"] == 9
    rows = result["symbols"]
    assert rows[SYMBOLS[10]]["rank_state"] == "select"
    assert rows[SYMBOLS[10]]["gated_state"] == "unresolved"
    assert rows[SYMBOLS[11]]["reason"] == "late_window_unavailable"
    assert rows[SYMBOLS[11]]["rank_state"] == rows[SYMBOLS[11]]["gated_state"] == "unresolved"


def test_each_valid_opening_endpoint_is_retained_without_a_complete_pair():
    sessions = _sessions(dict.fromkeys(range(12), "10050.25"), {0: 10020, 1: 10020})
    sessions[SYMBOLS[0]].minutes.pop(OPEN)
    sessions[SYMBOLS[1]].minutes.pop(OPENING_LAST)
    result = _checked(sessions)
    assert result["observed_opening_members"] == 10
    first_missing, last_missing = (result["symbols"][symbol] for symbol in SYMBOLS[:2])
    assert (first_missing["opening_first_open"], first_missing["opening_last_close"]) == (None, "40201/4")
    assert (last_missing["opening_first_open"], last_missing["opening_last_close"]) == ("10000", None)
    for row in (first_missing, last_missing):
        assert row["opening_return"] is None
        assert (row["opening_peer_count"], row["gated_state"]) == (10, "select")


def test_known_declining_peer_gate_preserves_unknown_late_entry_in_both_arms():
    sessions = _sessions(dict.fromkeys(range(10), 10020), {10: 10040, 11: 10040})
    sessions[SYMBOLS[10]].minutes.pop(LATE[7])
    rows = _checked(sessions)["symbols"]
    missing = rows[SYMBOLS[10]]
    assert (missing["opening_gate"], missing["opening_peer_median"]) == ("decline", "1/500")
    assert (missing["rank_state"], missing["gated_state"], missing["order_state"]) == (
        "unresolved", "unresolved", "retain")
    assert missing["reason"] == "late_window_unavailable"
    assert (rows[SYMBOLS[11]]["rank_state"], rows[SYMBOLS[11]]["gated_state"]) == ("select", "decline")


class _ReadBudget(Mapping):
    def __init__(self, bars):
        self.bars = bars

    def __getitem__(self, at):
        assert at in (OPEN, OPENING_LAST, *LATE)
        return self.bars[at]

    def __iter__(self):
        raise AssertionError("enumerated bars outside the registered windows")

    def __len__(self):
        return len(self.bars)


def test_selector_uses_only_two_opening_endpoints_and_required_predecision_late_window():
    sessions = _sessions(dict.fromkeys(range(10), 10050), {10: 10020})
    sessions = {symbol: replace(session, minutes=_ReadBudget(session.minutes)) for symbol, session in sessions.items()}
    assert _checked(sessions)["symbols"][SYMBOLS[10]]["gated_state"] == "select"


def test_rejects_unmatched_roster_clock_or_insufficient_time_for_opening_availability():
    sessions = _sessions({}, {})
    first = SYMBOLS[0]
    for calculate in (study.select_day, study.reference_day):
        with pytest.raises(ValueError, match="exactly 20"):
            calculate({symbol: session for symbol, session in sessions.items() if symbol != first})
        with pytest.raises(ValueError, match="session identity"):
            calculate({**sessions, first: sessions[SYMBOLS[1]]})
        with pytest.raises(ValueError, match="one opening and closing"):
            calculate({**sessions, first: replace(sessions[first], closing=CLOSE - MINUTE)})
        with pytest.raises(ValueError, match="session geometry"):
            calculate({symbol: replace(session, closing=OPEN + 77 * MINUTE) for symbol, session in sessions.items()})


def test_independent_reference_detects_producer_opening_projection_error(monkeypatch):
    sessions = _sessions(dict.fromkeys(range(10), 10050), {10: 10020})
    original = study._minute

    def producer_quote(session, at):
        if (session.symbol, at) == (SYMBOLS[0], OPENING_LAST):
            return None
        return original(session, at)

    monkeypatch.setattr(study, "_minute", producer_quote)
    assert study.select_day(sessions)["symbols"][SYMBOLS[10]]["gated_state"] == "unresolved"
    assert study.reference_day(sessions)["symbols"][SYMBOLS[10]]["gated_state"] == "select"
