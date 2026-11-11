"""Invented OHLC only: event semantics, missingness and causal prefix checks."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from research.intraday_event_scenarios import CONTRACT, MinuteBar, scan_scenarios


def candle(offset, close="100", *, low=None, high=None, available=None):
    """Name OHLC explicitly to avoid sharing production's geometry assumptions."""
    close = Decimal(close)
    return MinuteBar(offset=offset, open=close,
                     high=Decimal(high) if high is not None else close + Decimal("0.5"),
                     low=Decimal(low) if low is not None else close - Decimal("0.5"),
                     close=close, available_offset=offset + 3 if available is None else available)


def flat(length):
    return [candle(offset) for offset in range(length)]


def project(bars, end=None, length=90):
    return scan_scenarios(bars, end_offset=len(bars) if end is None else end,
                          session_length=length, series_id="synthetic:2026-01-05")


def family(result, name):
    return [frame for frame in result.frames if frame.family == name]


def a_path(length=27):
    bars = flat(length)
    for offset, close, low, high in [
        (21, "99", "98.8", "100"), (22, "98", "97.8", "99"),
        (23, "96.5", "96.3", "98"), (24, "96.6", "96.35", "97"),
        (25, "96.7", "96.3", "97.1"), (26, "97.4", "96.4", "97.6"),
    ]:
        if offset < length:
            bars[offset] = candle(offset, close, low=low, high=high)
    return bars


def b_path(length=36):
    bars = flat(length)
    bars[33] = candle(33, "99", low="98.8", high="100")
    bars[34] = candle(34, "99.1", low="98.7", high="99.4")
    bars[35] = candle(35, "99.8", low="99", high="100")
    return bars


def c_path(length=30):
    bars = flat(length)
    for offset, close in enumerate(["100.5", "101", "101.5", "102", "103.5"], 21):
        bars[offset] = candle(offset, close)
    bars[26] = candle(26, "104", low="103.8", high="104.2")
    bars[27] = candle(27, "103.6", low="103.5", high="104.1")
    bars[28] = candle(28, "103.4", low="103", high="103.9")
    bars[29] = candle(29, "104.3", low="103.6", high="104.4")
    return bars


def test_positive_a_freezes_pre_impulse_scale_and_retest_level():
    frames = family(project(a_path()), "A_DOWNFAIL")
    assert [(f.offset, f.kind, f.state) for f in frames] == [
        (23, "at_onset", "WAITING"), (26, "terminal", "CONFIRMED")]
    assert frames[0].scale == 1
    assert frames[0].level == frames[-1].level == Fraction(963, 10)
    assert frames[-1].decision_offset == 29
    assert dict(frames[-1].conditions)["prior_retest"] is True


def test_scale_uses_twenty_pre_impulse_ranges_and_exact_even_median():
    bars = a_path()
    for offset in range(11, 21):
        bars[offset] = candle(offset, low="99", high="101")
    # TR 1..10 = 1, TR 11..20 = 2: median = 3/2. The impulse is excluded.
    bars[23] = candle(23, "95.5", low="95.3", high="98")
    onset = family(project(bars), "A_DOWNFAIL")[0]
    assert onset.offset == 23
    assert onset.scale == Fraction(3, 2)


def test_positive_b_known_level_does_not_follow_later_lows():
    frames = family(project(b_path()), "B_ORL30")
    assert [(f.offset, f.state) for f in frames] == [(33, "WAITING"), (35, "CONFIRMED")]
    assert dict(frames[0].conditions)["level_known_at"] == 32
    assert frames[0].level == frames[-1].level == Fraction(199, 2)
    assert frames[-1].structure == Fraction(987, 10)
    assert dict(frames[0].conditions)["opening_range_bps"] == 100
    assert dict(frames[0].conditions)["opening_range_meets_66bps"] is True


def test_positive_c_only_first_pullback_and_frozen_peak():
    frames = family(project(c_path()), "C_FIRSTPULLBACK")
    assert [(f.offset, f.kind, f.state) for f in frames] == [
        (25, "at_onset", "WAITING_PULLBACK"), (27, "first_pullback", "PULLBACK"),
        (28, "scheduled", "PULLBACK"), (29, "terminal", "CONFIRMED")]
    assert frames[0].scale == 1
    assert frames[1].level == frames[-1].level == Fraction(521, 5)
    assert frames[-1].structure == 103


@pytest.mark.parametrize("name,maker,offset,new_low", [
    ("A_DOWNFAIL", a_path, 26, "95.9"),
    ("C_FIRSTPULLBACK", c_path, 29, "101.9"),
])
def test_same_bar_invalidation_overrides_confirmation(name, maker, offset, new_low):
    bars = maker()
    bars[offset] = replace(bars[offset], low=Decimal(new_low))
    terminal = family(project(bars), name)[-1]
    assert terminal.state == "INVALIDATED"
    assert dict(terminal.conditions)["reclaimed_prior_highs"] is True


def test_a_equality_at_low_extension_is_permitted():
    bars = a_path()
    bars[26] = replace(bars[26], low=Decimal("96.05"))
    assert family(project(bars), "A_DOWNFAIL")[-1].state == "CONFIRMED"


def test_b_breach_strict_reclaim_inclusive():
    bars = b_path()
    bars[33] = candle(33, "99.25")
    assert not family(project(bars[:34]), "B_ORL30")
    bars[33] = candle(33, "99", low="98.8", high="100")
    bars[35] = candle(35, "99.75")
    assert family(project(bars), "B_ORL30")[-1].state == "CONFIRMED"


def test_b_expiry_does_not_restart_without_a_new_cross():
    bars = b_path(65)
    for offset in range(34, 65):
        bars[offset] = candle(offset, "99", low="98.8", high="99.2")
    frames = family(project(bars), "B_ORL30")
    assert frames[-1].offset == 53
    assert frames[-1].state == "EXPIRED_NO_CONFIRMATION"
    assert sum(frame.kind == "at_onset" for frame in frames) == 1


def test_b_breach_at_level_availability_is_not_preknown():
    bars = b_path()
    bars[32] = candle(32, "99", low="98.8", high="100")
    # No new cross at 33: a breach before the level was pre-known is not adopted.
    assert not family(project(bars), "B_ORL30")


def test_a_expiry_has_scheduled_frames_without_relabelling_onset():
    bars = a_path(36)
    for offset in range(24, 36):
        bars[offset] = candle(offset, "96.6", low="96.3", high="97.1")
    frames = family(project(bars), "A_DOWNFAIL")
    assert [f.offset for f in frames] == [23, 26, 29, 32, 35]
    assert frames[-1].state == "EXPIRED_NO_CONFIRMATION"
    assert not frames[0].terminal


def test_c_no_pullback_and_no_resumption_are_distinct_expiries():
    bars = c_path(38)
    for offset in range(26, 36):
        close = Decimal("103.5") + Decimal("0.6") * (offset - 25)
        bars[offset] = candle(offset, str(close), high=str(close + Decimal("0.2")))
    assert family(project(bars[:36]), "C_FIRSTPULLBACK")[-1].state == "EXPIRED_NO_PULLBACK"
    bars = c_path(38)
    for offset in range(28, 38):
        bars[offset] = candle(offset, "103.4", low="103", high="103.9")
    terminal = family(project(bars), "C_FIRSTPULLBACK")[-1]
    assert terminal.offset == 37
    assert terminal.state == "EXPIRED_NO_CONFIRMATION"


def test_missing_active_minute_is_unknown_not_expiry_or_skipped_time():
    bars = [item for item in a_path() if item.offset != 25]
    result = project(bars, end=27)
    frames = family(result, "A_DOWNFAIL")
    assert frames[-1].offset == 25 and frames[-1].state == "UNKNOWN_GAP"
    assert len(result.support) == 3 * 27
    assert len([row for row in result.support if row.offset == 25 and row.status == "MISSING_BAR"]) == 3


def test_zero_scale_warmup_and_absent_history_are_preserved():
    bars = [candle(offset, low="100", high="100") for offset in range(40)]
    result = project(bars)
    assert not result.frames
    assert {row.status for row in result.support if row.family == "A_DOWNFAIL"} == {"WARMUP", "ZERO_SCALE"}
    bars = [item for item in a_path() if item.offset != 1]
    result = project(bars, end=27)
    row = next(row for row in result.support if row.family == "A_DOWNFAIL" and row.offset == 23)
    assert row.status == "MISSING_SCALE_HISTORY"
    assert not family(result, "A_DOWNFAIL")


def test_incomplete_opening_interval_never_produces_b_level():
    bars = [item for item in b_path() if item.offset != 0]
    result = project(bars, end=36)
    assert not family(result, "B_ORL30")
    assert any(row.status == "MISSING_OPENING_RANGE" for row in result.support)


def test_late_input_delays_decisions_and_b_requires_preknown_physical_level():
    bars = a_path()
    bars[20] = replace(bars[20], available_offset=50)
    frames = family(project(bars), "A_DOWNFAIL")
    assert [frame.decision_offset for frame in frames] == [50, 50]
    bars = b_path(43)
    bars[29] = replace(bars[29], available_offset=40)
    bars[40] = candle(40)
    bars[41] = candle(41, "99", low="98.8", high="100")
    bars[42] = candle(42, "99.8")
    frames = family(project(bars), "B_ORL30")
    assert frames[0].offset == 41
    assert dict(frames[0].conditions)["level_known_at"] == 40


@pytest.mark.parametrize("maker", [a_path, b_path, c_path])
def test_every_future_prefix_preserves_already_emitted_frames_and_support(maker):
    bars = maker()
    complete = project(bars)
    for end in range(len(bars) + 1):
        prefix = project([item for item in bars if item.offset < end], end=end)
        assert prefix.frames == tuple(frame for frame in complete.frames if frame.offset < end)
        assert prefix.support == tuple(row for row in complete.support if row.offset < end)
        assert prefix.broad_observations == tuple(row for row in complete.broad_observations if row.offset < end)


def test_prefix_invariance_with_late_history_and_missing_active_minute():
    bars = a_path()
    bars[20] = replace(bars[20], available_offset=50)
    bars = [item for item in bars if item.offset != 25]
    complete = project(bars, end=27)
    for end in range(28):
        prefix = project([item for item in bars if item.offset < end], end=end)
        assert prefix.frames == tuple(frame for frame in complete.frames if frame.offset < end)
        assert prefix.support == tuple(row for row in complete.support if row.offset < end)
        assert prefix.broad_observations == tuple(row for row in complete.broad_observations if row.offset < end)


def narrow_candle(offset, close="100", half_width="0.005"):
    value, half = Decimal(close), Decimal(half_width)
    return candle(offset, close, low=str(value - half), high=str(value + half))


@pytest.mark.parametrize("name,length,closes", [
    ("A_DOWNFAIL", 24, ["99.99", "99.98", "99.96"]),
    ("C_FIRSTPULLBACK", 26, ["100.01", "100.02", "100.03", "100.04", "100.05"]),
])
def test_small_three_scale_moves_remain_broad_denominators_only(name, length, closes):
    bars = [narrow_candle(offset) for offset in range(length)]
    for offset, close in enumerate(closes, 21):
        bars[offset] = narrow_candle(offset, close)
    result = project(bars)
    assert not family(result, name)
    broad = [row for row in result.broad_observations if row.family == name]
    assert broad and all(not row.meets_minimum_past_move for row in broad)
    status = next(row for row in result.support if row.family == name and row.offset == length - 1)
    assert status.status == "below_past_move_floor"
    assert dict(status.conditions)["broad_episode_id"] == broad[-1].broad_episode_id


@pytest.mark.parametrize("name,terminal,endpoint", [
    ("A_DOWNFAIL", 23, "99.34"), ("C_FIRSTPULLBACK", 25, "100.66"),
])
def test_exact_66bp_past_move_boundary_is_inclusive(name, terminal, endpoint):
    bars = [narrow_candle(offset, half_width="0.05") for offset in range(terminal + 1)]
    bars[terminal] = narrow_candle(terminal, endpoint, half_width="0.05")
    result = project(bars)
    onset = family(result, name)[0]
    assert dict(onset.conditions)["past_move_bps"] == 66
    assert dict(onset.conditions)["minimum_past_move_bps"] == 66
    direction = Decimal("0.001") if name == "A_DOWNFAIL" else Decimal("-0.001")
    bars[terminal] = narrow_candle(terminal, str(Decimal(endpoint) + direction), half_width="0.05")
    assert not family(project(bars), name)


def test_broad_observations_and_episode_groups_are_separate_counts():
    bars = [narrow_candle(offset) for offset in range(32)]
    values = {21: "99.99", 22: "99.98", 23: "99.96", 24: "99.95", 25: "99.94"}
    for offset in range(21, 32):
        bars[offset] = narrow_candle(offset, values.get(offset, "99.94"))
    bars[31] = narrow_candle(31, "99.90")
    result = project(bars)
    broad = [row for row in result.broad_observations if row.family == "A_DOWNFAIL"]
    assert [row.offset for row in broad] == [23, 24, 25, 31]
    assert len({row.broad_episode_id for row in broad}) == 2
    assert broad[0].broad_episode_id == broad[2].broad_episode_id
    assert not family(result, "A_DOWNFAIL")


def test_b_records_small_opening_range_without_filtering_the_event():
    bars = [narrow_candle(offset) for offset in range(35)]
    bars[33] = narrow_candle(33, "99.99")
    frames = family(project(bars), "B_ORL30")
    assert frames[-1].state == "CONFIRMED"
    assert dict(frames[0].conditions)["opening_range_bps"] == 1
    assert dict(frames[0].conditions)["opening_range_meets_66bps"] is False


def test_reset_requires_five_scheduled_minutes_and_a_new_impulse():
    bars = a_path(41)
    for offset in range(24, 38):
        bars[offset] = candle(offset, "96.6", low="96.3", high="97.1")
    bars[38] = candle(38, "95.5")
    bars[39] = candle(39, "94")
    bars[40] = candle(40, "92")
    result = project(bars)
    frames = family(result, "A_DOWNFAIL")
    assert [(frame.offset, frame.kind) for frame in frames if frame.kind == "at_onset"] == [(23, "at_onset"), (40, "at_onset")]
    assert len({frame.event_id for frame in frames}) == 2
    assert len({frame.overlap_group_id for frame in frames}) == 1
    assert all(row.status == "RESET" for row in result.support
               if row.family == "A_DOWNFAIL" and 36 <= row.offset < 40)


def test_prefix_end_is_not_session_end_but_explicit_calendar_end_is():
    bars = a_path(25)
    assert not family(project(bars), "A_DOWNFAIL")[-1].terminal
    ended = family(project(bars, length=25), "A_DOWNFAIL")[-1]
    assert ended.state == "SESSION_END" and ended.offset == 24


def test_new_onset_at_calendar_end_retains_both_onset_and_terminal():
    frames = family(project(a_path(24), length=24), "A_DOWNFAIL")
    assert [(frame.offset, frame.kind, frame.state) for frame in frames] == [
        (23, "at_onset", "WAITING"), (23, "terminal", "SESSION_END")]


def test_missing_trailing_prefix_minutes_are_not_silently_dropped():
    result = project(a_path(24), end=26)
    terminal = family(result, "A_DOWNFAIL")[-1]
    assert terminal.offset == 24 and terminal.state == "UNKNOWN_GAP"
    assert len(result.support) == 3 * 26


def test_exact_json_and_rejected_ambiguous_input_contracts():
    frame = family(project(a_path()), "A_DOWNFAIL")[0]
    decoded = json.loads(json.dumps(frame.as_dict()))
    assert decoded["level"] == "963/10"
    assert json.loads(json.dumps(CONTRACT))["status"] == "proposed_design_values_not_validated_rules"
    with pytest.raises(TypeError):
        MinuteBar(0, 1.0, 2, 1, 1, 3)
    with pytest.raises(ValueError):
        MinuteBar(0, 1, 2, 1, 1, 0)
    with pytest.raises(ValueError):
        project([candle(0), candle(0)])
    with pytest.raises(ValueError):
        project([candle(1), candle(0)])
    with pytest.raises(ValueError):
        project([candle(0), candle(1)], end=1)
