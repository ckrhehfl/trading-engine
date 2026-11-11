"""Independent held calendar audits on invented prefixes and event permutations."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_held as producer
from research import intraday_event_held_reference as reference
from research.intraday_event_execution import EntryRequest
from research.intraday_event_scenarios import EventFrame, MinuteBar


def invented():
    request = EntryRequest(30, 100, 2, 95)
    frame = EventFrame("C_FIRSTPULLBACK", "event", "symbol/session", 20, 27, 30, 18, 28,
                       "scheduled", "PULLBACK", False, Fraction(2), Fraction(101), Fraction(95), ())
    bars = {offset: MinuteBar(offset, 100, 105, 90, 100, offset + 3) for offset in range(80)}
    return request, frame, bars


def both_functions(request, frame, bars, session_length, age, policy):
    for name in ("held_checkpoint", "remaining_gross_target"):
        arguments = dict(age_minutes=age, entry_policy=policy)
        expected = getattr(producer, name)(request, frame, bars, session_length, **arguments)
        observed = getattr(reference, name)(request, frame, bars, session_length, **arguments)
        assert observed == expected


@pytest.mark.parametrize("age", [10, 20])
@pytest.mark.parametrize("policy", ["next_open_capped", "reference_limit_one_shot"])
def test_reference_matches_full_record_across_missing_and_stop_coordinates(age, policy):
    for variant in ("plain", "missing", "stop", "late", "entry_price"):
        offsets = range(31, 62) if variant in ("missing", "stop", "late") else (31,)
        for offset in offsets:
            request, frame, bars = invented()
            if variant == "missing":
                del bars[offset]
            elif variant == "stop":
                bars[offset] = replace(bars[offset], close=Fraction(94))
            elif variant == "late":
                bars[offset] = replace(bars[offset], available_offset=offset + 7)
            elif variant == "entry_price":
                bars[31] = replace(bars[31], open=Fraction(101))
            both_functions(request, frame, bars, 80, age, policy)


def test_missing_receipt_deadline_precedes_a_future_delayed_stop():
    request, frame, bars = invented()
    bars[31] = replace(bars[31], close=Fraction(94), available_offset=36)
    bars[32] = replace(bars[32], available_offset=40)
    bars[33] = replace(bars[33], available_offset=36)
    both_functions(request, frame, bars, 80, 10, "next_open_capped")
    value = reference.held_checkpoint(request, frame, bars, 80, age_minutes=10)
    assert value.status == "unknown" and value.unknown_offset == 34


def test_missing_deadline_precedes_same_clock_received_stop():
    request, frame, bars = invented()
    bars[31] = replace(bars[31], close=Fraction(94), available_offset=36)
    bars[32] = replace(bars[32], available_offset=36)
    del bars[33]
    both_functions(request, frame, bars, 80, 10, "next_open_capped")
    value = reference.held_checkpoint(request, frame, bars, 80, age_minutes=10)
    assert value.status == "unknown" and value.reason == "held_close_unknown"


def test_structural_due_at_expiry_beats_time_and_maturity_is_receipt_time():
    request, frame, bars = invented()
    bars[57] = replace(bars[57], close=Fraction(94))
    both_functions(request, frame, bars, 80, 20, "next_open_capped")
    value = reference.remaining_gross_target(request, frame, bars, 80, age_minutes=20)
    assert value.reason == "structural" and value.exit_offset == 61 and value.matured_offset == 64
    both_functions(request, frame, bars, 62, 20, "next_open_capped")


def test_no_producer_functions_or_full_future_support_are_needed(monkeypatch):
    request, frame, bars = invented()
    expected = producer.held_checkpoint(request, frame, bars, 80, age_minutes=10)
    expected_target = producer.remaining_gross_target(request, frame, bars, 80, age_minutes=10)

    def forbidden(*args, **kwargs):
        raise AssertionError("Reference called a producer calculation")

    monkeypatch.setattr(producer, "held_checkpoint", forbidden)
    monkeypatch.setattr(producer, "remaining_gross_target", forbidden)
    prefix = {offset: value for offset, value in bars.items() if offset < 41}
    assert reference.held_checkpoint(request, frame, prefix, 80, age_minutes=10) == expected
    assert reference.remaining_gross_target(request, frame, bars, 80, age_minutes=10) == expected_target


def test_quantity_denominator_boundaries_and_initial_unknown_are_reconstructed():
    request, frame, bars = invented()
    both_functions(request, frame, bars, 61, 10, "next_open_capped")
    for entry_price in (95, 100, 102, 103):
        changed = dict(bars)
        changed[31] = replace(changed[31], open=Fraction(entry_price))
        both_functions(request, frame, changed, 80, 10, "next_open_capped")
    expensive = EntryRequest(30, 3_000_000, 2, 2_999_995)
    expensive_frame = replace(frame, structure=expensive.structure_stop)
    both_functions(expensive, expensive_frame, bars, 80, 10, "next_open_capped")


def test_tampered_frame_binding_and_unknown_policy_are_rejected_independently():
    request, frame, bars = invented()
    with pytest.raises(ValueError, match="binding"):
        reference.held_checkpoint(request, replace(frame, scale=Fraction(3)), bars, 80, age_minutes=10)
    with pytest.raises(ValueError, match="policy"):
        reference.held_checkpoint(request, frame, bars, 80, age_minutes=10, entry_policy="future_low_touch")
