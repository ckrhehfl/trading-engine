"""Invented first-attempt evidence for the fixed EJ17 retry gate."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_laggard_retry as gate
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPENING = datetime.fromisoformat("2026-01-02T10:00:00+09:00")
DECISION = OPENING + 62 * MINUTE
FIRST_AT, SECOND_AT = (OPENING + offset * MINUTE for offset in gate.RETRY_GATE_OFFSETS)
SYMBOL = "000001"
RANK_PLAN = {"rank_state": "select", "gated_state": "decline", "order_state": "ready",
             "limit": "10000", "stop": "9700"}
WEAK = {"state": "weak"}


def _bar(at, opened, closed):
    op, close = Decimal(opened), Decimal(closed)
    return Minute(SYMBOL, at, at + 3 * MINUTE, (op, max(op, close), min(op, close), close), None)


class _BoundedPrices(dict):
    def __init__(self, prices, allowed):
        super().__init__(prices)
        self.allowed = allowed
        self.accesses = []

    def get(self, at, default=None):
        assert at in self.allowed, f"read outside the fixed retry decision: {at}"
        self.accesses.append(at)
        return super().get(at, default)


def _session(*bars, allowed=(FIRST_AT, SECOND_AT)):
    prices = _BoundedPrices({bar.start_at: bar for bar in bars}, allowed)
    return SessionInputs(SYMBOL, OPENING, OPENING + 390 * MINUTE, prices)


def _both(session, rank_plan=RANK_PLAN, path_plan=WEAK):
    args = SYMBOL, DECISION, rank_plan, path_plan, session
    result = gate.classify(*args)
    assert result == gate.reference_classify(*args)
    return result


def test_above_cap_and_two_available_closes_freeze_original_quantity():
    session = _session(
        _bar(FIRST_AT, "10001.5", "9700.125"),
        _bar(SECOND_AT, 9800, "9800.5"),
        _bar(OPENING + 68 * MINUTE, 9800, 9600),
    )
    assert _both(session) == {
        "state": "retry", "reason": "retry_ready",
        "original_open": "20003/2", "first_close": "77601/8", "second_close": "19601/2",
        "quantity": 150,
    }
    assert session.minutes.accesses == [FIRST_AT, SECOND_AT] * 2


@pytest.mark.parametrize(("opened", "reason"), [
    (10000, "original_open_within_bounds"),
    (9950, "original_open_within_bounds"),
    (9700, "original_open_at_or_below_stop"),
    (9699, "original_open_at_or_below_stop"),
])
def test_original_fill_or_stop_side_miss_never_requests_second_bar(opened, reason):
    session = _session(_bar(FIRST_AT, opened, 9900), allowed=(FIRST_AT,))
    assert _both(session) == {
        "state": "retain", "reason": reason,
        "original_open": str(opened), "first_close": "9900", "second_close": None, "quantity": 150,
    }
    assert session.minutes.accesses == [FIRST_AT] * 2


@pytest.mark.parametrize(("first_close", "second_close"), [(9700, 9800), (9800, 9700), (9800, 9699)])
def test_either_confirmation_close_at_or_below_stop_retains_the_first_miss(first_close, second_close):
    outcome = _both(_session(_bar(FIRST_AT, 10001, first_close), _bar(SECOND_AT, 9800, second_close)))
    assert outcome == {
        "state": "retain", "reason": "confirmation_structure_lost",
        "original_open": "10001", "first_close": str(first_close), "second_close": str(second_close),
        "quantity": 150,
    }


def test_missing_or_invalid_first_bar_is_unresolved_without_reading_second():
    first = _bar(FIRST_AT, 10001, 9800)
    for bars in ((), (replace(first, available_at=FIRST_AT + 4 * MINUTE),),
                 (replace(first, available_at=FIRST_AT + 2 * MINUTE),),
                 (replace(first, available_at="invalid"),), (replace(first, symbol="000002"),),
                 (replace(first, price_state="missing"),)):
        session = _session(*bars, allowed=(FIRST_AT,))
        assert _both(session) == {
            "state": "unresolved", "reason": "original_open_unavailable",
            "original_open": None, "first_close": None, "second_close": None, "quantity": 150,
        }
        assert session.minutes.accesses == [FIRST_AT] * 2


def test_missing_late_or_invalid_confirmation_close_keeps_known_first_evidence():
    first = _bar(FIRST_AT, 10001, 9800)
    second = _bar(SECOND_AT, 9800, 9900)
    for bars in ((first,), (first, replace(second, available_at=SECOND_AT + 4 * MINUTE)),
                 (first, replace(second, ohlc=(Decimal(9800), Decimal(9800), Decimal(9800), Decimal(9900))))):
        assert _both(_session(*bars)) == {
            "state": "unresolved", "reason": "confirmation_close_unavailable",
            "original_open": "10001", "first_close": "9800", "second_close": None, "quantity": 150,
        }


def test_nonweak_and_zero_quantity_never_read_prices():
    session = _session(allowed=())
    for state in ("retain", "rebound", "unresolved"):
        assert _both(None, {}, {"state": state}) == {
            "state": "retain", "reason": "original_path_not_weak",
            "original_open": None, "first_close": None, "second_close": None, "quantity": None,
        }
    assert _both(session, {**RANK_PLAN, "limit": "100000000", "stop": "99000000"}) == {
        "state": "retain", "reason": "original_quantity_zero",
        "original_open": None, "first_close": None, "second_close": None, "quantity": 0,
    }
    assert session.minutes.accesses == []


@pytest.mark.parametrize("classify", (gate.classify, gate.reference_classify))
def test_rejects_inconsistent_path_order_or_session_before_reading_prices(classify):
    session = _session(allowed=())
    with pytest.raises(ValueError, match="weak_order_not_ready"):
        classify(SYMBOL, DECISION, {**RANK_PLAN, "order_state": "decline"}, WEAK, session)
    with pytest.raises(ValueError, match="path_differs"):
        classify(SYMBOL, DECISION, RANK_PLAN, {"state": "forged"}, session)
    with pytest.raises(ValueError, match="session_required"):
        classify(SYMBOL, DECISION, RANK_PLAN, WEAK, None)
    for symbol, decision in (("000002", DECISION), (SYMBOL, DECISION + MINUTE),
                             (SYMBOL, DECISION.replace(tzinfo=None))):
        with pytest.raises(ValueError):
            classify(symbol, decision, RANK_PLAN, WEAK, session)
    assert session.minutes.accesses == []


def test_reference_does_not_use_production_quote_or_sizing(monkeypatch):
    session = _session(_bar(FIRST_AT, 10001, 9800), _bar(SECOND_AT, 9800, 9900))

    def refuse(*_):
        raise AssertionError("reference used a production dependency")

    monkeypatch.setattr(gate, "_minute", refuse)
    monkeypatch.setattr(gate.execution, "requested_quantity", refuse)
    assert gate.reference_classify(SYMBOL, DECISION, RANK_PLAN, WEAK, session)["state"] == "retry"
