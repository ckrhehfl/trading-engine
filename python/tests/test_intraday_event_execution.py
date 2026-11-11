"""Invented EL event execution and independent exact ledger checks only."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_execution as execution
from research import intraday_event_execution_reference as reference
from research.intraday_event_scenarios import MinuteBar


def bars(price=100, length=100):
    return {i: MinuteBar(i, price, price + 2, price - 2, price, i + 3) for i in range(length)}


def request():
    return execution.EntryRequest(5, 100, 2, 95)


def compare(tape, req=None, **kwargs):
    req = req or request()
    expected = reference.evaluate(req, tape, 100, **kwargs)
    actual = execution.evaluate(req, tape, 100, **kwargs)
    assert actual == expected
    return actual


def test_fixed_exit_and_exact_initial_sizing():
    result = compare(bars())
    q = min(Fraction(50000) // (Fraction(102) * Fraction(10033, 10000) - 95), 2000000 // 102)
    assert result["requested_quantity"] == result["filled_quantity"] == q
    assert result["sales"][0]["execute_offset"] == 36
    assert result["gross"] == "0"
    assert Fraction(result["cost"]) == q * 100 * Fraction(33, 10000)
    assert Fraction(result["cash"]) == 100000000 + Fraction(result["net"])


def test_stop_uses_available_close_then_strictly_next_open():
    tape = bars()
    tape[8] = MinuteBar(8, 100, 102, 93, 94, 11)
    tape[12] = MinuteBar(12, 92, 94, 90, 92, 15)
    result = compare(tape)
    sale = result["sales"][0]
    assert (sale["decision_offset"], sale["execute_offset"], sale["price"], sale["reason"]) == (11, 12, "92", "structural")
    assert result["entry_offset"] == 6


def test_entry_only_uses_next_open_and_policies_share_reserved_size():
    tape = bars()
    tape[6] = MinuteBar(6, 101, 110, 90, 101, 9)
    capped = compare(tape)
    limited = compare(tape, entry_policy="reference_limit_one_shot")
    assert capped["filled_quantity"] > 0
    assert limited["status"] == "no_fill"
    assert capped["requested_quantity"] == limited["requested_quantity"]
    assert limited["net"] == "0"
    assert limited["counts"]["no_fill"] == 1


def test_price_gap_over_guard_never_fills_on_later_low():
    tape = bars()
    tape[6] = MinuteBar(6, 103, 105, 90, 101, 9)
    result = compare(tape)
    assert result["status"] == "no_fill"
    assert result["sales"] == []


@pytest.mark.parametrize("missing,reason", [(6, "entry_open_unknown"), (8, "held_close_unknown"), (36, "exit_open_unknown")])
def test_missing_price_preserves_unknown(missing, reason):
    tape = bars()
    del tape[missing]
    result = compare(tape)
    assert result["status"] == "unresolved"
    assert result["reason"] == reason
    assert result["net"] is result["cash"] is None
    assert result["counts"]["unknown"] == 1


def test_pre_entry_session_refusal_does_not_read_prices():
    result = execution.evaluate(execution.EntryRequest(70, 100, 2, 95), {}, 100)
    assert result == reference.evaluate(execution.EntryRequest(70, 100, 2, 95), {}, 100)
    assert result["status"] == "no_entry"
    assert result["counts"]["no_entry"] == 1


def test_forecast_full_exit_at_next_minute_and_late_forecast_is_unknown():
    predictions = (execution.Forecast(16, 16, 0), execution.Forecast(26, 26, 1))
    result = compare(bars(), management="forecast_full_exit", forecasts=predictions)
    assert result["sales"][0]["execute_offset"] == 17
    late = (replace(predictions[0], available_offset=17), predictions[1])
    result = compare(bars(), management="forecast_full_exit", forecasts=late)
    assert result["sales"][0]["reason"] == "time"
    stale = (replace(predictions[0], available_offset=15), predictions[1])
    result = compare(bars(), management="forecast_full_exit", forecasts=stale)
    assert result["sales"][0]["reason"] == "time"


def test_odd_share_half_then_full_conserves_quantity_and_charges_once():
    req = execution.EntryRequest(5, 600000, 1, 599999)
    predictions = (execution.Forecast(16, 16, -1), execution.Forecast(26, 26, 0))
    result = compare(bars(price=600000), req, management="forecast_half_then_exit", forecasts=predictions)
    assert result["filled_quantity"] == 3
    assert [leg["quantity"] for leg in result["sales"]] == [1, 2]
    assert [leg["execute_offset"] for leg in result["sales"]] == [17, 27]
    assert result["remaining_quantity"] == 0
    assert sum(Fraction(leg["allocated_cost"]) for leg in result["sales"]) == Fraction(result["entry_cost"])
    assert Fraction(result["cash"]) == 100000000 - Fraction(result["entry_cost"])


@pytest.mark.parametrize("second", [execution.Forecast(26, 26, 1), execution.Forecast(26, 27, -1), execution.Forecast(26, 26, None)])
def test_positive_unknown_and_late_reset_half_streak(second):
    predictions = (execution.Forecast(16, 16, -1), second)
    result = compare(bars(), management="forecast_half_then_exit", forecasts=predictions)
    assert [leg["reason"] for leg in result["sales"]] == ["forecast_half", "time"]


def test_structural_stop_precedes_forecast_at_same_decision():
    tape = bars()
    tape[13] = MinuteBar(13, 100, 102, 93, 94, 16)
    result = compare(tape, management="forecast_half_then_exit", forecasts=(execution.Forecast(16, 16, -1),))
    assert result["sales"][0]["reason"] == "structural"
    assert result["sales"][0]["quantity"] == result["filled_quantity"]


def test_matched_management_has_identical_initial_fill_and_size():
    predictions = (execution.Forecast(16, 16, -1), execution.Forecast(26, 26, -1))
    result = execution.compare_management(request(), bars(), 100, forecasts=predictions)
    assert result == reference.compare_management(request(), bars(), 100, forecasts=predictions)
    assert len({(row["requested_quantity"], row["entry_price"], row["entry_notional"], row["entry_cost"]) for row in result.values()}) == 1


def test_high_low_and_post_exit_prices_do_not_change_the_execution():
    tape = bars()
    baseline = compare(tape)
    for i in range(100):
        tape[i] = replace(tape[i], high=1000, low=1)
    assert compare(tape) == baseline
    del tape[37]
    assert compare(tape) == baseline


def test_reversed_availability_is_unresolved():
    tape = bars()
    tape[6] = replace(tape[6], available_offset=12)
    result = compare(tape)
    assert result["reason"] == "held_close_unknown"
    assert result["unknown_offset"] == 9
    assert result["net"] is None


def test_unreceived_late_close_never_looks_like_a_known_held_prefix():
    tape = bars()
    tape[6] = MinuteBar(6, 100, 102, 93, 94, 12)
    for offset in (7, 8, 9):
        tape[offset] = replace(tape[offset], available_offset=12)
    result = compare(tape)
    assert result["reason"] == "held_close_unknown"
    assert result["unknown_offset"] == 9
    assert result["sales"] == []
    # The modeled opening fill is separately known; only later unseen closes
    # disappear. Removing the entry itself would change the fill evidence.
    assert compare({offset: value for offset, value in tape.items()
                    if offset == 6 or value.available_offset <= 9}) == result


def test_a_missing_minute_cannot_hide_behind_a_delayed_older_bar():
    tape = bars()
    for offset in range(6, 36):
        tape[offset] = replace(tape[offset], available_offset=50)
    del tape[7]
    result = compare(tape)
    assert result["reason"] == "held_close_unknown"
    assert result["unknown_offset"] == 9


def test_one_share_half_attempt_waits_for_second_nonpositive_forecast():
    req = execution.EntryRequest(5, 1500000, 1, 1499999)
    predictions = (execution.Forecast(16, 16, -1), execution.Forecast(26, 26, 0))
    result = compare(bars(price=1500000), req, management="forecast_half_then_exit", forecasts=predictions)
    assert result["filled_quantity"] == 1
    assert [(sale["quantity"], sale["execute_offset"]) for sale in result["sales"]] == [(1, 27)]


def test_unknown_after_partial_sale_preserves_known_proceeds_and_paid_fee():
    tape = bars()
    del tape[20]
    predictions = (execution.Forecast(16, 16, -1), execution.Forecast(26, 26, -1))
    result = compare(tape, management="forecast_half_then_exit", forecasts=predictions)
    assert result["status"] == "unresolved"
    assert len(result["sales"]) == 1
    assert Fraction(result["entry_cost"]) > Fraction(result["allocated_cost"]) > 0
    assert Fraction(result["last_known_cash"]) > 0
    assert result["net"] is None


def test_invalid_requests_and_forecast_schedule_refuse():
    with pytest.raises(ValueError, match="stop"):
        execution.EntryRequest(5, 100, 2, 102)
    with pytest.raises(TypeError, match="Exact"):
        execution.EntryRequest(5, 100.0, 2, 95)
    with pytest.raises(ValueError, match="capital"):
        execution.EntryRequest(5, 100, 2, 95, capital=200000000)
    with pytest.raises(ValueError, match="forecasts"):
        execution.evaluate(request(), bars(), 100, forecasts=(execution.Forecast(15, 15, -1),))
