"""Invented EI context cases, without source files, outcomes or orders."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_context_situations import context_frame, scan_context_frames
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103))
PEAK = (103, 116, 102, 114)
PULLBACK = (*BASE, PEAK, (114, 115, 111, 112), (112, 114, 110, 111))
RESUMPTION = (111, 116, 110, 115)


def at(offset):
    return OPEN + offset * MINUTE


def session(*bars, close=390, volume=10):
    rows = {}
    for bar_index, bar in enumerate(bars):
        if bar is None:
            continue
        for offset in range(5 * bar_index, 5 * bar_index + 5):
            rows[at(offset)] = Minute("invented", at(offset), at(offset + 3),
                                      tuple(map(Decimal, bar)), Decimal(volume))
    return SessionInputs("invented", OPEN, at(close), rows)


def change(value, offset, **updates):
    rows = dict(value.minutes)
    rows[at(offset)] = replace(rows[at(offset)], **updates)
    return replace(value, minutes=rows)


def frame(value, end):
    return context_frame(value, decision_at=at(end + 2))


class OnlyRequestedKeys(Mapping):
    def __init__(self, rows, offsets):
        self.rows = rows
        self.remaining = list(map(at, offsets))

    def __getitem__(self, key):
        assert self.remaining and key == self.remaining.pop(0), f"unexpected lookup {key}"
        return self.rows[key]

    def __iter__(self):
        raise AssertionError("source mapping iteration")

    def __len__(self):
        raise AssertionError("source mapping length")


def test_first_hour_shallow_pullback_keeps_exact_anchor_metrics_and_separate_support():
    value = frame(session(*PULLBACK), 30)
    assert (value.observation_end, value.available_at) == (at(30), at(32))
    assert (value.classification, value.phase) == ("A", "first_pullback")
    assert value.anchor.price == 116
    assert value.anchor.pre_peak_range == 4
    assert value.anchor.advance == 16 and value.anchor.band == 1
    assert value.anchor.peak_end == at(20) and value.anchor.confirmed_at == at(32)
    assert value.metrics.advance_in_ranges == 4
    assert value.metrics.pullback_depth == Fraction(3, 8)
    assert value.metrics.bars_since_peak == 2 and value.metrics.retest_episodes == 0
    assert value.metrics.first_pullback_at == at(32) and value.metrics.resumption_at is None
    assert value.prefix_support.price_complete and value.prefix_support.volume_complete
    first, sixty, longer = value.windows
    assert first.price_complete and first.volume_total == 300
    assert dict(sixty.price_counts) == {"observed": 30, "outside_session": 30}
    assert dict(longer.volume_counts) == {"observed": 30, "outside_session": 90}
    assert not sixty.price_complete and longer.volume_total is None
    with pytest.raises(FrozenInstanceError):
        value.classification = "B"


def test_resumption_is_a_current_phase_then_the_first_attempt_is_completed():
    value = session(*PULLBACK, RESUMPTION, (115, 117, 114, 116))
    earlier, current, later = (frame(value, end) for end in (30, 35, 40))
    assert current.anchor == earlier.anchor == later.anchor
    assert (current.classification, current.phase) == ("A", "first_resumption")
    assert current.metrics.resumption_at == at(37) and current.metrics.retest_episodes == 1
    assert later.classification == "other" and later.reason == "first_attempt_completed"


def test_shallow_depth_and_thirty_minute_duration_boundaries_are_exact():
    held = (110, 114, 108, 109)
    value = session(*PULLBACK, *([held] * 5))
    boundary, expired = frame(value, 50), frame(value, 55)
    assert boundary.metrics.pullback_depth == Fraction(1, 2)
    assert boundary.metrics.bars_since_peak == 6 and boundary.classification == "A"
    assert expired.metrics.bars_since_peak == 7 and expired.classification == "other"
    deeper = session(*PULLBACK, *([(110, 114, Decimal("107.99"), 109)] * 4))
    assert frame(deeper, 50).metrics.pullback_depth == Fraction(801, 1600)
    assert frame(deeper, 50).classification == "other"
    flat_after = frame(session(*BASE, PEAK, (116, 116, 116, 116), (116, 116, 116, 116)), 30)
    assert flat_after.metrics.pullback_depth == 0 and flat_after.classification == "other"


def test_reference_range_uses_median_of_exact_three_individual_pre_peak_ranges():
    before = ((100, 102, 98, 100), (100, 120, 99, 102), (102, 104, 101, 103))
    value = frame(session(*before, (103, 124, 102, 122), (122, 123, 120, 121), (121, 122, 119, 120)), 30)
    # Individual ranges 4,21,3 -> median 4. Neither total range22 nor peak range22.
    assert value.anchor.pre_peak_range == 4 and value.anchor.advance == 24
    assert value.metrics.advance_in_ranges == 6


def test_peak_requires_a_completed_right_bar_and_does_not_use_later_global_high():
    before = (*BASE, (103, 105, 102, 104), (104, 106, 103, 105), (105, 120, 104, 118))
    value = session(*before, (118, 119, 116, 117), (117, 140, 116, 138), (138, 139, 130, 134))
    assert frame(value, 30).anchor is None
    first, later = frame(value, 35), frame(value, 45)
    assert first.anchor.price == 120 and first.anchor.confirmed_at == at(37)
    assert later.anchor == first.anchor and later.metrics.invalidated_at is None


def test_B_requires_extended_advance_and_two_distinct_retests_at_current_resistance():
    below, touch = (114, 114, 111, 112), (112, 116, 111, 114)
    value = session(*BASE, PEAK, below, touch, below, touch, (114, 114, 111, 112))
    first, second, after = (frame(value, end) for end in (30, 40, 45))
    assert first.metrics.retest_episodes == 1 and first.classification != "B"
    assert second.metrics.retest_episodes == 2 and second.classification == "B"
    assert second.metrics.advance_in_ranges == 4 and second.metrics.latest_overlaps_resistance
    assert after.classification != "B" and not after.metrics.latest_overlaps_resistance
    # Same retests after an advance between 2R and 3R do not meet B.
    narrow = session(*BASE, (103, 110, 102, 108), (108, 108, 106, 107),
                     (107, 110, 106, 108), (108, 108, 106, 107), (107, 110, 106, 108))
    assert frame(narrow, 40).metrics.advance_in_ranges == Fraction(5, 2)
    assert frame(narrow, 40).metrics.retest_episodes == 2
    assert frame(narrow, 40).classification != "B"


def test_adjacent_overlaps_and_elapsed_time_do_not_fabricate_retest_episodes():
    below, touch = (114, 114, 111, 112), (112, 116, 111, 114)
    value = session(*BASE, PEAK, below, *([touch] * 8))
    current = frame(value, 65)
    assert current.metrics.retest_episodes == 1
    assert current.metrics.bars_since_peak == 9 and current.classification == "other"
    no_touch = frame(session(*BASE, PEAK, *([below] * 9)), 65)
    assert no_touch.metrics.retest_episodes == 0 and no_touch.classification != "B"


def test_close_invalidation_is_retained_without_replacing_anchor_with_a_later_high():
    value = session(*PULLBACK, (111, 112, 99, 100), (100, 150, 99, 149), (149, 149, 140, 145))
    earlier, later = frame(value, 30), frame(value, 45)
    assert later.anchor == earlier.anchor
    assert later.metrics.invalidated_at == at(37)
    assert (later.classification, later.reason) == ("other", "anchor_invalidated")
    assert later.metrics.pullback_depth == Fraction(17, 16)


def test_price_prefix_is_independent_of_volume_gaps_and_observed_zero():
    value = session(*PULLBACK, volume=0)
    zero = frame(value, 30)
    assert zero.classification == "A" and zero.prefix_support.volume_total == 0
    assert zero.prefix_support.zero_volume_minutes == 30
    value = change(value, 1, shares=None, volume_state="transport_error")
    partial = frame(value, 30)
    assert partial.classification == "A" and partial.prefix_support.price_complete
    assert partial.anchor == zero.anchor and partial.metrics == zero.metrics
    assert partial.prefix_support.volume_total is None and partial.prefix_support.zero_volume_minutes == 29
    assert dict(partial.windows[0].volume_counts) == {"observed": 29, "transport_error": 1}


@pytest.mark.parametrize("state", ["missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"])
def test_price_unknown_never_becomes_other_or_zero_and_preserves_known_volume(state):
    value = change(session(*PULLBACK), 1, ohlc=None, price_state=state)
    current = frame(value, 30)
    assert current.classification == "unknown" and current.reason == "opening_price_prefix_incomplete"
    assert current.anchor is current.metrics is None
    assert current.prefix[1].price_state == state and current.prefix[1].prices is None
    assert current.prefix_support.volume_complete and current.prefix_support.volume_total == 300


def test_complete_latest120_does_not_claim_first_attempt_across_an_opening_gap():
    value = session(*([BASE[0]] * 30))
    rows = dict(value.minutes)
    del rows[at(0)]
    current = frame(replace(value, minutes=rows), 150)
    assert all(window.price_complete and window.volume_complete for window in current.windows)
    assert current.classification == "unknown" and not current.prefix_support.price_complete


def test_late_payload_is_not_inspected_and_later_support_does_not_backfill_first_identity():
    value = session(*PULLBACK, *([RESUMPTION] * 3))
    delayed = change(value, 29, available_at=at(40))
    early, later = frame(delayed, 30), frame(delayed, 40)
    assert early.prefix[-1].price_state == early.prefix[-1].volume_state == "late"
    assert early.prefix[-1].available_at is None and early.prefix[-1].prices is None
    assert later.prefix_support.price_complete and later.prefix_support.volume_complete
    assert not later.prefix_observed_on_grid
    assert later.classification == "unknown" and later.reason == "prefix_not_observed_on_grid"
    malformed = change(delayed, 29, ohlc=(Decimal(-1),) * 4, shares=object(), volume_state="invalid")
    assert frame(malformed, 30).classification == "unknown"
    with pytest.raises(InputRefusal):
        frame(malformed, 40)


def test_first_grid_accepts_a_row_available_by_E32_without_claiming_earlier_observation():
    value = change(session(*PULLBACK), 0, available_at=at(31))
    current = frame(value, 30)
    assert current.prefix_observed_on_grid and current.classification == "A"
    assert current.anchor.confirmed_at == at(32)


def test_zero_reference_range_and_no_anchor_are_supported_unclassified():
    flat = (100, 100, 100, 100)
    zero = frame(session(flat, flat, flat, (100, 110, 100, 108), (108, 109, 106, 107),
                         (107, 108, 105, 106)), 30)
    assert zero.prefix_support.price_complete and zero.classification == "unclassified"
    assert zero.reason == "zero_reference_range" and zero.anchor is None
    quiet = frame(session(*([BASE[0]] * 6)), 30)
    assert quiet.classification == "unclassified" and quiet.reason == "no_qualifying_confirmed_peak"


def test_current_frame_reads_only_completed_requested_prefix_and_ignores_changed_future():
    value = session(*PULLBACK, RESUMPTION)
    guarded = OnlyRequestedKeys(value.minutes, range(30))
    current = frame(replace(value, minutes=guarded), 30)
    assert not guarded.remaining
    poisoned = dict(value.minutes)
    poisoned.update({at(index): object() for index in range(30, 390)})
    assert current == frame(replace(value, minutes=poisoned), 30)


def test_scanner_reads_each_key_once_and_matches_individual_causal_frames_with_late_recovery():
    value = session(*PULLBACK, RESUMPTION, *([BASE[0]] * 7))
    value = change(value, 29, available_at=at(40))
    guarded = OnlyRequestedKeys(value.minutes, range(70))
    scanned = tuple(scan_context_frames(replace(value, minutes=guarded), through=at(72)))
    assert not guarded.remaining
    assert tuple(item.available_at for item in scanned) == tuple(map(at, range(32, 73, 5)))
    assert scanned == tuple(frame(value, end) for end in range(30, 71, 5))
    assert scanned == tuple(scan_context_frames(value, through=at(72)))


@pytest.mark.parametrize("end", [0, 25, 29, 31, 388, 390, 395])
def test_invalid_grid_and_outside_session_refuse_before_source_access(end):
    value = replace(session(*PULLBACK), minutes=OnlyRequestedKeys({}, []))
    with pytest.raises(InputRefusal, match="EI_requires_E30"):
        frame(value, end)


@pytest.mark.parametrize("shift", [timedelta(seconds=1), timedelta(microseconds=1)])
def test_fractional_minute_decision_is_not_rounded_into_the_grid(shift):
    value = replace(session(*PULLBACK), minutes=OnlyRequestedKeys({}, []))
    with pytest.raises(InputRefusal, match="aware_minute_clock_required"):
        context_frame(value, decision_at=at(32) + shift)


def test_short_session_and_scan_cutoff_are_explicit():
    value = session(*PULLBACK, close=32)
    assert tuple(scan_context_frames(value, through=at(40))) == ()
    assert tuple(scan_context_frames(session(*PULLBACK), through=at(31))) == ()
    with pytest.raises(InputRefusal, match="cutoff_before_opening"):
        tuple(scan_context_frames(value, through=at(-1)))


@pytest.mark.parametrize("updates", [
    {"shares": Decimal(-1)}, {"shares": None}, {"price_state": "missing"},
    {"volume_state": "missing"}, {"symbol": "changed"}, {"available_at": at(1)},
    {"ohlc": (Decimal(100), Decimal(101), Decimal(102), Decimal(100))},
])
def test_visible_malformed_inputs_fail_integrity_not_missingness(updates):
    with pytest.raises(InputRefusal):
        frame(change(session(*PULLBACK), 0, **updates), 30)
