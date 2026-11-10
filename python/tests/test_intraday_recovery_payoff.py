"""Invented EK paths and exact same-entry holding transitions only."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_recovery_payoff as payoff
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPENING = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
LIMIT, STOP = Fraction(10000), Fraction(9800)


def _at(offset):
    return OPENING + offset * MINUTE


def _bar(offset, op=10000, close=None, *, low=None, high=None, lag=3):
    close = op if close is None else close
    prices = tuple(map(Decimal, (op, max(op, close) if high is None else high,
                                min(op, close) if low is None else low, close)))
    return Minute("invented", _at(offset), _at(offset + lag), prices, None)


def _session(limit=10000, stop=9800):
    minutes = {_at(i): _bar(i, limit, low=stop) for i in range(45, 60)}
    minutes.update({_at(i): _bar(i, limit) for i in range(63, 124)})
    return SessionInputs("invented", OPENING, _at(390), minutes)


def _update(offset, value):
    return {"decision_at": _at(offset).isoformat(), "available_at": _at(offset).isoformat(),
            "expected_remaining_gross_pct": value}


def _evaluate(session, limit=LIMIT, stop=STOP):
    result = payoff.evaluate(session, limit, stop)
    assert result == payoff.reference_evaluate(session, limit, stop)
    return result


def _manage(session, updates, limit=LIMIT, stop=STOP):
    baseline = _evaluate(session, limit, stop)["baseline"]
    saved = deepcopy((baseline, updates))
    result = payoff.manage(session, baseline, updates)
    payoff.verify_management(session, baseline, updates, result)
    assert (baseline, updates) == saved
    return result, baseline


@pytest.mark.parametrize(("offset", "close", "label", "reason"), [
    (63, 10066, 0, "target_first"), (63, 9800, 1, "invalidation_first"),
    (119, 10066, 0, "target_first"), (119, 9800, 1, "invalidation_first"),
])
def test_labels_use_first_causal_close_and_inclusive_boundaries(offset, close, label, reason):
    session = _session()
    session.minutes[_at(offset)] = _bar(offset, close=close)
    result = _evaluate(session)
    assert result["path"] == {"class": label, "reason": reason, "at": _at(offset + 3).isoformat()}
    if offset == 119:
        assert result["baseline"]["selected_trigger"] == "time"


def test_label_uses_frozen_limit_and_ignores_high_while_payoff_uses_actual_entry():
    session = _session()
    session.minutes[_at(63)] = _bar(63, 9900, high=11000)
    session.minutes[_at(64)] = _bar(64, 9900, close=10000, high=11000)
    session.minutes[_at(123)] = _bar(123, 10100)
    result = _evaluate(session)
    assert result["path"] == {"class": 2, "reason": "timeout", "at": _at(122).isoformat()}
    assert result["gross_pct"] == "200/99"
    assert result["baseline"]["entry_price"] == "9900"


@pytest.mark.parametrize("offset", [63, 64, 119])
@pytest.mark.parametrize("kind", ["absent", "late", "invalid"])
def test_missing_earlier_close_is_not_replaced_by_later_target(offset, kind):
    session = _session()
    if kind == "absent":
        del session.minutes[_at(offset)]
    elif kind == "late":
        session.minutes[_at(offset)] = _bar(offset, lag=4)
    else:
        session.minutes[_at(offset)] = _bar(offset, low=11000)
    if offset < 119:
        session.minutes[_at(offset + 1)] = _bar(offset + 1, close=11000)
    result = _evaluate(session)
    assert result["path"]["class"] is None
    assert result["path"]["reason"] == ("entry_unknown" if offset == 63 else "first_close_unknown")
    if offset == 119:
        assert result["baseline"]["status"] == "closed"
        assert result["gross_pct"] == "0"


@pytest.mark.parametrize("price", [9800, 10001])
def test_unfilled_entry_never_becomes_target_label_or_zero_payoff_training_row(price):
    session = _session()
    session.minutes[_at(63)] = _bar(63, price, high=11000)
    result = _evaluate(session)
    assert result["baseline"]["status"] == "no_fill"
    assert result["path"] == {"class": None, "reason": "entry_not_filled", "at": None}
    assert result["gross_pct"] is None


def test_first_target_label_survives_later_missing_close_without_inventing_executable_return():
    session = _session()
    session.minutes[_at(63)] = _bar(63, close=10066)
    del session.minutes[_at(64)]
    result = _evaluate(session)
    assert result["path"]["class"] == 0
    assert result["baseline"]["status"] == "unresolved" and result["gross_pct"] is None


def test_held_arm_preserves_baseline_exact_payoff_and_expiry():
    session = _session()
    session.minutes[_at(123)] = _bar(123, 10100)
    result, baseline = _manage(session, [])
    assert result["status"] == "closed" and result["reason"] == "time"
    assert (result["gross"], result["cost"], result["net"]) == ("20000", "6600", "13400")
    assert all(result[key] == baseline[key] for key in ("gross", "cost", "net"))
    assert result["sales"][0]["request_at"] == _at(122).isoformat()
    assert result["sales"][0]["at"] == _at(123).isoformat()


def test_two_nonpositive_forecasts_reduce_then_exit_next_open_without_reentry():
    session = _session()
    session.minutes[_at(78)] = _bar(78, 10050)
    session.minutes[_at(93)] = _bar(93, 9900)
    result, _ = _manage(session, [_update(77, "0"), _update(92, "-1/2"), _update(107, "5")])
    assert [row["quantity"] for row in result["sales"]] == [100, 100]
    assert [row["at"] for row in result["sales"]] == [_at(78).isoformat(), _at(93).isoformat()]
    assert (result["gross"], result["cost"], result["net"]) == ("-5000", "6600", "-11600")
    assert result["entry_basis"] == result["realized_basis"] == "2000000"
    assert result["remaining_quantity"] == 0


@pytest.mark.parametrize("middle", [[], [_update(92, None)], [_update(92, "1/10000")]])
def test_missing_or_positive_update_resets_consecutive_and_never_reduces_twice(middle):
    result, _ = _manage(_session(), [_update(77, "-1"), *middle, _update(107, "-1")])
    assert [row["reason"] for row in result["sales"]] == ["forecast_reduce", "time"]
    assert [row["quantity"] for row in result["sales"]] == [100, 100]


def test_positive_tiny_remaining_value_does_not_recharge_sunk_cost():
    result, _ = _manage(_session(), [_update(77, "1/10000"), _update(92, "1/10000")])
    assert len(result["sales"]) == 1 and result["sales"][0]["reason"] == "time"


@pytest.mark.parametrize("trigger", [73, 74])
def test_structural_exit_wins_before_or_at_forecast_decision(trigger):
    session = _session()
    session.minutes[_at(trigger)] = _bar(trigger, close=9800)
    session.minutes[_at(trigger + 4)] = _bar(trigger + 4, 9700)
    result, baseline = _manage(session, [_update(77, "-1")])
    assert len(result["sales"]) == 1 and result["reason"] == "structural"
    assert result["sales"][0]["at"] == _at(trigger + 4).isoformat()
    assert result["net"] == baseline["net"]


def test_time_exit_keeps_priority_over_last_close_invalidation():
    session = _session()
    session.minutes[_at(119)] = _bar(119, close=9800)
    result, _ = _manage(session, [_update(107, "-1")])
    assert result["reason"] == "time"
    assert result["sales"][-1]["at"] == _at(123).isoformat()


def test_odd_integer_size_allocates_entry_cost_once_over_both_sales():
    session = _session(10010, 9800)
    session.minutes[_at(63)] = _bar(63, 10000)
    result, _ = _manage(session, [_update(77, "-1"), _update(92, "0")], Fraction(10010))
    assert result["initial_quantity"] == 199
    assert [row["quantity"] for row in result["sales"]] == [99, 100]
    assert [row["cost"] for row in result["sales"]] == ["3267", "3300"]
    assert result["entry_cost"] == result["cost"] == "6567"


def test_single_unit_reduction_has_no_zero_quantity_sale_then_second_update_exits():
    session = _session(2000000, 1999900)
    result, _ = _manage(session, [_update(77, "0"), _update(92, "0")],
                        Fraction(2000000), Fraction(1999900))
    assert result["initial_quantity"] == 1
    assert len(result["sales"]) == 1 and result["sales"][0]["quantity"] == 1
    assert result["sales"][0]["reason"] == "forecast_exit"


def test_unknown_reduction_open_cannot_resurrect_at_later_good_price():
    session = _session()
    del session.minutes[_at(78)]
    session.minutes[_at(93)] = _bar(93, 11000)
    result, _ = _manage(session, [_update(77, "-1"), _update(92, "-1")])
    assert result["status"] == "unresolved" and result["remaining_quantity"] is None
    assert result["gross"] is result["cost"] is result["net"] is None
    assert len(result["sales"]) == 1 and result["sales"][0]["price"] is None
    assert result["sales"][0]["execute_at"] == result["unresolved_at"] == _at(78).isoformat()


def test_missing_close_after_reduction_retains_realized_cash_and_remaining_position():
    session = _session()
    session.minutes[_at(78)] = _bar(78, 10100)
    del session.minutes[_at(75)]
    result, _ = _manage(session, [_update(77, "-1"), _update(92, "-1")])
    assert result["status"] == "unresolved" and result["remaining_quantity"] == 100
    assert (result["realized_gross"], result["realized_cost"], result["realized_net"]) == (
        "10000", "3300", "6700")
    assert result["net"] is None and result["unresolved_at"] == _at(75).isoformat()


@pytest.mark.parametrize("kind", ["unknown", "declined", "zero_quantity"])
def test_management_preserves_nonfill_and_unknown_entry_without_sales(kind):
    session = _session(3000000, 2999900) if kind == "zero_quantity" else _session()
    if kind == "unknown":
        del session.minutes[_at(63)]
    elif kind == "declined":
        session.minutes[_at(63)] = _bar(63, 10001)
    bounds = (Fraction(3000000), Fraction(2999900)) if kind == "zero_quantity" else (LIMIT, STOP)
    result, _ = _manage(session, [_update(77, "-1")], *bounds)
    assert result["sales"] == []
    assert result["status"] == ("unresolved" if kind == "unknown" else "no_fill")
    assert result["net"] == (None if kind == "unknown" else "0")


@pytest.mark.parametrize("mutation", ["missing_stamp", "future", "off_schedule", "duplicate", "float", "unordered"])
def test_rejects_unstamped_future_unregistered_or_inexact_forecasts(mutation):
    updates = [_update(77, "0")]
    if mutation == "missing_stamp":
        del updates[0]["available_at"]
    elif mutation == "future":
        updates[0]["available_at"] = _at(78).isoformat()
    elif mutation == "off_schedule":
        updates = [_update(78, "0")]
    elif mutation == "duplicate":
        updates.append(_update(77, "0"))
    elif mutation == "float":
        updates[0]["expected_remaining_gross_pct"] = 0.0
    else:
        updates = [_update(92, "0"), _update(77, "0")]
    session = _session()
    baseline = _evaluate(session)["baseline"]
    with pytest.raises(ValueError):
        payoff.manage(session, baseline, updates)
    with pytest.raises(ValueError):
        payoff.verify_management(session, baseline, updates, {})


def test_rejects_tampered_original_baseline_and_wrong_session_identity():
    session = _session()
    baseline = _evaluate(session)["baseline"]
    baseline["cost"] = "0"
    with pytest.raises(ValueError, match="baseline receipt differs"):
        payoff.manage(session, baseline, [])
    with pytest.raises(ValueError):
        payoff.manage(replace(session, symbol="other"), baseline, [])


@pytest.mark.parametrize("field", ["net", "entry_cost", "remaining_quantity", "sale_quantity", "sale_clock", "sale_cost"])
def test_independent_audit_rejects_cash_quantity_and_chronology_tampering(field):
    session = _session()
    updates = [_update(77, "-1"), _update(92, "-1")]
    result, baseline = _manage(session, updates)
    if field == "sale_quantity":
        result["sales"][0]["quantity"] += 1
    elif field == "sale_clock":
        result["sales"][0]["at"] = _at(77).isoformat()
    elif field == "sale_cost":
        result["sales"][0]["cost"] = "0"
    else:
        result[field] = "0"
    with pytest.raises(ValueError, match="independent reconstruction"):
        payoff.verify_management(session, baseline, updates, result)


def test_references_do_not_call_producer_path_or_management_quote_and_accounting(monkeypatch):
    session = _session()
    updates = [_update(77, "-1"), _update(92, "-1")]
    evaluated = _evaluate(session)
    managed, baseline = _manage(session, updates)
    monkeypatch.setattr(payoff, "_minute", lambda *_: None)
    monkeypatch.setattr(payoff, "_sell", lambda *_: pytest.fail("producer accounting called"))
    monkeypatch.setattr(payoff, "_initial_result", lambda *_: pytest.fail("producer result called"))
    monkeypatch.setattr(payoff, "manage", lambda *_: pytest.fail("producer management called"))
    assert payoff.reference_evaluate(session, LIMIT, STOP) == evaluated
    payoff.verify_management(session, baseline, updates, managed)
