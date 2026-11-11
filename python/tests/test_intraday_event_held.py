"""Invented matched-entry books only; no stored inputs, fits or runtime backend."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_execution as execution
from research import intraday_event_held as held
from research.intraday_event_scenarios import EventFrame, MinuteBar


def bar(offset, *, opening=100, close=100, available=None, high=None, low=None):
    return MinuteBar(offset, opening, high or max(opening, close) + 1,
                     low or min(opening, close) - 1, close, offset + 3 if available is None else available)


def setup():
    request = execution.EntryRequest(30, 100, 2, 95)
    frame = EventFrame("A_DOWNFAIL", "invented-event", "invented-symbol/day", 20, 27, 30,
        18, 28, "scheduled", "WAITING", False, Fraction(2), Fraction(101), Fraction(95), ())
    return request, frame, {offset: bar(offset) for offset in range(80)}


def checkpoint(request, frame, bars, age=10, **kwargs):
    return held.held_checkpoint(request, frame, bars, 80, age_minutes=age, **kwargs)


def target(request, frame, bars, age=10, **kwargs):
    return held.remaining_gross_target(request, frame, bars, 80, age_minutes=age, **kwargs)


def test_free_checkpoint_freezes_original_structure_and_receipt_reference():
    request, frame, bars = setup()
    bars[38] = bar(38, close=102)
    result = checkpoint(request, frame, bars)
    assert result.status == "free_alive"
    assert result.reference_offset == result.frame.offset == 38
    assert result.frame.decision_offset == 41
    assert result.frame.state == "HELD_10"
    assert (result.frame.level, result.frame.scale, result.frame.structure) == (frame.level, frame.scale, frame.structure)
    assert result.frame.overlap_group_id == frame.overlap_group_id
    assert result.frame.event_id == result.book_id != frame.event_id
    assert result.position == (Fraction(1, 3), Fraction(2, 3), Fraction(1))
    assert result.requested_quantity == execution.evaluate(request, bars, 80)["filled_quantity"]
    twenty = checkpoint(request, frame, bars, 20)
    assert twenty.book_id == result.book_id
    assert twenty.position[:2] == (Fraction(2, 3), Fraction(1, 3))


def test_future_prices_and_missing_expiry_do_not_select_alive_cohort():
    request, frame, bars = setup()
    original = checkpoint(request, frame, bars)
    prefix = {offset: value for offset, value in bars.items() if offset < original.checkpoint_offset}
    assert checkpoint(request, frame, prefix) == original
    prefix[61] = bar(61, opening=300, close=300)
    assert checkpoint(request, frame, prefix) == original
    assert target(request, frame, prefix).status == "unknown"


def test_stop_receipt_at_checkpoint_is_pending_and_cannot_get_adaptive_query():
    request, frame, bars = setup()
    bars[38] = bar(38, close=95)
    result = checkpoint(request, frame, bars)
    assert result.status == "stop_pending"
    assert result.stop_decision_offset == 41 and result.exit_offset == 42
    assert result.frame is result.position is None
    assert target(request, frame, bars).status == "not_applicable"


def test_stop_due_at_checkpoint_is_closed_without_reading_later_prices():
    request, frame, bars = setup()
    bars[37] = bar(37, close=94)
    bars[41] = bar(41, opening=93, close=900)
    result = checkpoint(request, frame, bars)
    assert result.status == "closed" and result.exit_offset == 41
    assert result.stop_decision_offset == 40
    assert result.frame is None
    assert execution.evaluate(request, bars, 80)["sales"][0]["execute_offset"] == result.exit_offset


def test_missing_past_and_missing_due_sale_are_unknown():
    request, frame, bars = setup()
    del bars[35]
    result = checkpoint(request, frame, bars)
    assert result.status == "unknown" and result.reason == "held_close_unknown"
    assert result.unknown_offset == 38
    request, frame, bars = setup()
    bars[37] = bar(37, close=94)
    del bars[41]
    result = checkpoint(request, frame, bars)
    assert result.status == "unknown" and result.reason == "exit_open_unknown"


def test_unreceived_future_bars_cannot_change_the_causal_held_cohort():
    request, frame, bars = setup()
    bars[35] = bar(35, close=94, available=45)
    assert checkpoint(request, frame, bars).reason == "held_close_unknown"
    for offset in range(35, 41):
        bars[offset] = bar(offset, close=94, available=45)
    result = checkpoint(request, frame, bars)
    assert result.status == "unknown" and result.unknown_offset == 38
    received = {offset: value for offset, value in bars.items()
                if value.available_offset <= 41}
    assert checkpoint(request, frame, received) == result


@pytest.mark.parametrize("policy,opening,status", [
    ("next_open_capped", 102, "free_alive"),
    ("next_open_capped", 103, "no_fill"),
    ("reference_limit_one_shot", 101, "no_fill"),
    ("reference_limit_one_shot", 100, "free_alive"),
    ("next_open_capped", 95, "no_fill"),
])
def test_entry_open_and_quantity_match_original_execution(policy, opening, status):
    request, frame, bars = setup()
    bars[31] = bar(31, opening=opening)
    result = checkpoint(request, frame, bars, entry_policy=policy)
    execution_result = execution.evaluate(request, bars, 80, entry_policy=policy)
    assert result.status == status
    assert result.requested_quantity == execution_result["requested_quantity"]
    assert result.entry_price == (Fraction(execution_result["entry_price"]) if execution_result["entry_price"] else None)


def test_entry_missing_short_session_and_distinct_counterfactual_arms():
    request, frame, bars = setup()
    left = checkpoint(request, frame, bars)
    right = checkpoint(request, frame, bars, entry_policy="reference_limit_one_shot")
    assert left.book_id != right.book_id and left.overlap_group_id == right.overlap_group_id
    assert held.held_checkpoint(request, frame, bars, 61, age_minutes=10).status == "no_entry"
    del bars[31]
    result = checkpoint(request, frame, bars)
    assert result.status == "unknown" and result.reason == "entry_open_unknown"


def test_remaining_target_uses_next_open_basis_original_expiry_and_no_entry_fee():
    request, frame, bars = setup()
    bars[42] = bar(42, opening=104)
    bars[61] = bar(61, opening=110)
    result = target(request, frame, bars)
    assert result.status == "known"
    assert result.first_offset == 42 and result.exit_offset == result.expiry_offset == 61
    assert result.value == Fraction(110, 104) - 1
    assert result.matured_offset == 64
    assert target(*setup()).value == 0


def test_remaining_stop_uses_receipt_then_strictly_next_open():
    request, frame, bars = setup()
    bars[42] = bar(42, close=94, available=45)
    bars[43] = bar(43, available=46)
    bars[46] = bar(46, opening=91)
    result = target(request, frame, bars)
    assert result.reason == "structural" and result.exit_offset == 46
    assert result.value == Fraction(-9, 100)
    assert result.exit_offset == execution.evaluate(request, bars, 80)["sales"][0]["execute_offset"]


def test_target_missing_start_future_gap_or_expiry_is_unknown_not_zero():
    for missing, reason in ((42, "remaining_start_open_unknown"), (48, "held_close_unknown"), (61, "exit_open_unknown")):
        request, frame, bars = setup()
        del bars[missing]
        assert checkpoint(request, frame, bars).status == "free_alive"
        result = target(request, frame, bars)
        assert result.status == "unknown" and result.reason == reason and result.value is None


def test_high_low_touch_never_changes_alive_or_remaining_return():
    request, frame, bars = setup()
    first_checkpoint, first_target = checkpoint(request, frame, bars), target(request, frame, bars)
    changed = {offset: replace(value, high=Fraction(1000), low=Fraction(1)) for offset, value in bars.items()}
    assert checkpoint(request, frame, changed) == first_checkpoint
    assert target(request, frame, changed) == first_target


def test_frozen_frame_identity_and_registered_checkpoints_are_required():
    request, frame, bars = setup()
    with pytest.raises(ValueError, match="frozen request"):
        checkpoint(request, replace(frame, structure=Fraction(94)), bars)
    with pytest.raises(ValueError, match="checkpoint"):
        checkpoint(request, frame, bars, 15)


def test_target_receipt_after_session_is_unknown_without_dropping_alive_checkpoint():
    request, frame, bars = setup()
    assert held.held_checkpoint(request, frame, bars, 62, age_minutes=10).status == "free_alive"
    value = held.remaining_gross_target(request, frame, bars, 62, age_minutes=10)
    assert value.value is None and value.status == "unknown"
    assert value.reason == "remaining_not_mature_in_session"
