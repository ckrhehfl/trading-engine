"""Invented matched 30/60/90-minute exit paths."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_candidate_laggard_execution as trade
from research import intraday_candidate_laggard_hold as hold
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


SYMBOL = "invented"
OPENING = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
DECISION = OPENING + 62 * MINUTE
ENTRY = OPENING + 63 * MINUTE


def _at(offset):
    return ENTRY + offset * MINUTE


def _bar(offset, op=10_000, close=None, *, lag=3):
    at = _at(offset)
    close = op if close is None else close
    ohlc = tuple(map(Decimal, (op, max(op, close), min(op, close), close)))
    return Minute(SYMBOL, at, at + lag * MINUTE, ohlc, None)


def _session():
    minutes = {_at(offset): _bar(offset) for offset in range(91)}
    for offset, price in ((30, 10_030), (60, 10_100), (90, 10_200)):
        minutes[_at(offset)] = _bar(offset, price)
    return SessionInputs(SYMBOL, OPENING, OPENING + 390 * MINUTE, minutes)


def _row(session):
    plan = {"rank_state": "select", "order_state": "ready", "limit": "10000", "stop": "9700"}
    baseline = trade.execute_trade(SYMBOL, DECISION, Fraction(10_000), Fraction(9700), session)
    return {"symbol": SYMBOL, "session": OPENING.date().isoformat(), "fold": 1,
            "questions": [{"status": "opportunity", "decision_at": DECISION.isoformat(), "rank_decision": plan,
                           "baseline": baseline, "candidate": {"status": "no_entry"}}]}


def _arms(row, session):
    before = deepcopy(row)
    produced = hold.project(row, session)
    assert produced == hold.reference_project(row, session)
    assert row == before
    question, = produced["questions"]
    base = before["questions"][0]["baseline"]
    assert question["baseline"] == base
    assert question["rank_decision"] == before["questions"][0]["rank_decision"]
    for key in ("candidate", "hold90"):
        arm = question[key]
        assert (arm["entry_at"], arm["fill"], arm["requested_quantity"]) == (
            base["entry_at"], base["fill"], base["requested_quantity"])
    return question["candidate"], question["hold90"]


class _AllowedReads(dict):
    def get(self, at, default=None):
        assert _at(26) <= at <= _at(90)
        return super().get(at, default)


def test_time_expiries_use_archived_opens_and_preserve_exact_quantity_and_cost():
    session = _session()
    row = _row(session)
    assert hold.needs_session(row) and hold.needs_reference_session(row)
    arms = _arms(row, replace(session, minutes=_AllowedReads(session.minutes)))
    for arm, offset, price, gross, net in zip(arms, (60, 90), ("10100", "10200"),
                                              ("15000", "30000"), ("10050", "25050"), strict=True):
        assert arm["selected_trigger"] == "time"
        assert arm["exit_request_at"] == _at(offset - 1).isoformat()
        assert arm["exit_at"] == arm["exit_execute_at"] == _at(offset).isoformat()
        assert (arm["exit_price"], arm["gross"], arm["cost"], arm["net"]) == (price, gross, "4950", net)


def test_first_new_boundary_close_can_sell_at_original_expiry():
    session = _session()
    row = _row(session)
    session.minutes[_at(26)] = _bar(26, close=9700)
    for arm in _arms(row, session):
        assert (arm["selected_trigger"], arm["trigger_close"], arm["exit_price"], arm["net"]) == (
            "structural", "9700", "10030", "-450")
        assert arm["trigger_start_at"] == _at(26).isoformat()
        assert arm["exit_request_at"] == _at(29).isoformat()
        assert arm["exit_at"] == _at(30).isoformat()


def test_short_expiry_wins_tie_and_longer_arm_uses_same_open_after_structure():
    session = _session()
    row = _row(session)
    session.minutes[_at(56)] = _bar(56, close=9700)
    session.minutes[_at(60)] = _bar(60, 9600)
    first, second = _arms(row, session)
    assert first["selected_trigger"] == "time" and first["trigger_start_at"] is None
    assert second["selected_trigger"] == "structural" and second["trigger_start_at"] == _at(56).isoformat()
    for arm in (first, second):
        assert (arm["exit_at"], arm["exit_price"], arm["net"]) == (_at(60).isoformat(), "9600", "-64950")


@pytest.mark.parametrize(("offset", "expected_reason", "scheduled"), [
    (26, "first_structural_close_unknown", None),
    (30, "exit_price_unknown", 30),
])
def test_missing_earliest_close_or_requested_open_keeps_uncertain_outcome(offset, expected_reason, scheduled):
    session = _session()
    row = _row(session)
    session.minutes[_at(26)] = _bar(26, close=9700)
    session.minutes[_at(27)] = _bar(27, close=9500)
    del session.minutes[_at(offset)]
    for arm in _arms(row, session):
        assert arm["status"] == "unresolved" and arm["net"] is None
        assert arm["unknown_at"] == _at(offset).isoformat() and arm["reason"] == expected_reason
        assert arm["exit_at"] is None and arm["exit_price"] is None
        assert arm["exit_execute_at"] == (None if scheduled is None else _at(scheduled).isoformat())


@pytest.mark.parametrize(("bar", "status"), [(_bar(0, close=9700), "closed"), (_bar(0, op=10_001), "no_fill")])
def test_preexisting_structural_sale_and_nonfill_are_retained_without_new_session(bar, status):
    session = _session()
    session.minutes[_at(0)] = bar
    row = _row(session)
    base = row["questions"][0]["baseline"]
    assert base["status"] == status
    assert not hold.needs_session(row) and not hold.needs_reference_session(row)
    assert _arms(row, None) == (base, base)


def test_missing_close_after_short_arm_expiry_affects_only_long_arm():
    session = _session()
    row = _row(session)
    session.minutes[_at(56)] = _bar(56, lag=4)
    first, second = _arms(row, session)
    assert first["status"] == "closed"
    assert second["status"] == "unresolved" and second["unknown_at"] == _at(56).isoformat()


def test_rejects_bad_receipt_and_archive_ending_at_extended_expiry():
    session = _session()
    row = _row(session)
    tampered = deepcopy(row)
    tampered["questions"][0]["baseline"]["cost"] = "0"
    for project in (hold.project, hold.reference_project):
        with pytest.raises(ValueError, match="original payoff differs"):
            project(tampered, session)
        with pytest.raises(ValueError, match="archive identity or bounds differ"):
            project(row, replace(session, closing=_at(90)))


def test_audit_does_not_use_production_quote_access(monkeypatch):
    session = _session()
    row = _row(session)
    monkeypatch.setattr(hold.rule, "_minute", lambda *_: None)
    assert hold.project(row, session)["questions"][0]["candidate"]["status"] == "unresolved"
    assert hold.reference_project(row, session)["questions"][0]["candidate"]["status"] == "closed"
