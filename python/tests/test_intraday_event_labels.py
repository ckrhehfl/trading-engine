"""Manual invented path/cash targets; no market input or fitted learner."""

from dataclasses import FrozenInstanceError, fields, replace
from fractions import Fraction as F

import pytest

from research.intraday_event_features import chart_features
from research import intraday_event_labels as labels
from research.intraday_event_scenarios import EventFrame, MinuteBar


def tape(price=100, length=100):
    return {i: MinuteBar(i, price, price + 1, price - 1, price, i + 3) for i in range(length)}


def frame(**changes):
    base = EventFrame("A_DOWNFAIL", "invented:a", "invented:session", 25, 30, 33, 22, 31,
                      "scheduled", "WAITING", False, F(2), F(96), F(95), ())
    return replace(base, **changes)


def quote(offset, *, price=100, close=None, high=None, low=None, available=None):
    close = price if close is None else close
    return MinuteBar(offset, price, max(price, close) + 1 if high is None else high,
                     min(price, close) - 1 if low is None else low, close,
                     offset + 3 if available is None else available)


def query(bars=None, event=None, length=100):
    bars = tape(length=length) if bars is None else bars
    event = frame() if event is None else event
    chart = chart_features(event, bars, session_length=length)
    return labels.prepare_query(event, chart, session_length=length)


def result(bars=None, event=None, length=100):
    bars = tape(length=length) if bars is None else bars
    return labels.build_targets(query(bars, event, length), bars)


def quantity(reference, scale, stop):
    cap = F(reference) + scale
    risk_per_unit = cap - stop + cap * F(33, 10000)
    return min(int(F(50000) / risk_per_unit), int(F(2000000) / cap))


def test_separate_path_neutral_and_exact_cash_heads_have_distinct_maturities():
    row = result()
    assert row.path.value == labels.PATH_CLASSES["neutral"] == 1
    assert (row.path.first_offset, row.path.last_offset, row.path.matured_offset) == (34, 63, 66)
    assert row.path.upper == 106 and row.path.lower == 95
    assert row.immediate.value == row.wait3.value == -F(11, 3400)
    assert row.immediate.fill == row.wait3.fill == 1
    assert row.immediate.expiry_offset == 64 and row.wait3.expiry_offset == 67
    assert row.immediate.matured_offset == 67 and row.wait3.matured_offset == 70
    assert row.immediate.fill_matured_offset == 37 and row.wait3.fill_matured_offset == 40
    assert row.original_requested_notional == quantity(100, 2, 95) * 102
    assert row.immediate.denominator == row.wait3.denominator == row.original_requested_notional


@pytest.mark.parametrize("high,low,expected", [(106, 99, 2), (101, 95, 0)])
def test_first_barrier_touch_is_inclusive_and_independent_of_execution(high, low, expected):
    bars = tape()
    bars[34] = quote(34, high=high, low=low)
    row = result(bars)
    assert row.path.value == expected and row.path.observed_through == 34
    assert row.path.matured_offset == 37
    assert row.immediate.value == -F(11, 3400)  # A high/low touch never manufactures an exit.


def test_same_minute_both_barriers_preserves_intrabar_unknown_not_loss_or_zero():
    bars = tape()
    bars[34] = quote(34, high=106, low=95)
    row = result(bars)
    assert row.path.value is None and row.path.status == "unknown_intrabar"
    assert row.path.reason == "both_barriers_in_one_minute"
    assert row.immediate.status == "known" and row.immediate.fill == 1


def test_path_uses_maximum_of_three_scales_and_separate_66bp_barrier():
    bars = tape(price=1000)
    event = frame(scale=F(1), structure=F(990), level=F(995))
    bars[34] = quote(34, price=1000, high=F(5033, 5), low=999)
    row = result(bars, event)
    assert row.path.upper == F(5033, 5) and row.path.value == 2
    assert row.path.upper - 1000 == F(33, 5)


def test_missing_before_first_touch_is_unknown_and_missing_after_does_not_erase_path():
    bars = tape()
    bars.pop(34)
    bars[35] = quote(35, high=110)
    row = result(bars)
    assert row.path.value is None and row.path.reason == "missing_before_first_touch"
    assert row.path.matured_offset == 37
    assert row.immediate.value is None and row.immediate.fill is None
    assert row.immediate.matured_offset == 37
    bars = tape()
    bars[34] = quote(34, high=110)
    bars.pop(35)
    row = result(bars)
    assert row.path.value == 2 and row.path.observed_through == 34
    assert row.immediate.value is None and row.immediate.fill == 1
    assert row.immediate.reason == "held_close_unknown"


def test_late_path_matures_on_actual_consumed_clock_without_backdating():
    bars = tape()
    bars[34] = quote(34, available=90)
    bars[35] = quote(35, high=110)
    row = result(bars)
    assert row.path.value == 2 and row.path.matured_offset == 90
    assert row.immediate.reason == "nonchronological_close_availability"
    assert row.immediate.value is None
    bars[34] = quote(34, high=110, available=100)
    row = result(bars)
    assert row.path.value is None and row.path.reason == "path_not_mature_in_session"
    assert row.immediate.value is None and row.immediate.reason == "cash_not_mature_in_session"
    assert row.immediate.fill is None and row.immediate.fill_matured_offset is None


@pytest.mark.parametrize("price,reason", [(103, "entry_open_above_policy_cap"), (95, "entry_open_at_or_below_stop")])
def test_known_no_fill_is_zero_cash_label_and_zero_fill_not_missing(price, reason):
    bars = tape()
    bars[34] = quote(34, price=price)
    row = result(bars)
    assert row.immediate.value == 0 and row.immediate.net_cash == 0
    assert row.immediate.fill == 0 and row.immediate.status == "no_fill"
    assert row.immediate.reason == reason


def test_wait_uses_new_receipt_reference_with_same_original_denominator_scale_and_structure():
    bars = tape()
    bars[33] = quote(33, price=104)
    bars[34] = quote(34, price=101, close=103)
    bars[37] = quote(37, price=104)
    bars[64] = quote(64, price=110)
    bars[67] = quote(67, price=110)
    row = result(bars)
    first_q, delayed_q = quantity(100, 2, 95), quantity(104, 2, 95)
    assert row.original_requested_notional == first_q * 102
    assert row.wait3.requested_quantity == delayed_q and row.wait3.reference_close == 104
    assert row.wait3.guard_cap == 106 and row.wait3.denominator == first_q * 102
    assert row.immediate.net_cash == first_q * (F(9) - F(101) * F(33, 10000))
    assert row.wait3.net_cash == delayed_q * (F(6) - F(104) * F(33, 10000))
    assert row.wait3.value == row.wait3.net_cash / (first_q * 102)
    assert row.wait3.value != row.wait3.net_cash / (delayed_q * 106)


def test_wait_invalidation_uses_received_close_not_minute_low():
    bars = tape()
    bars[31] = quote(31, low=90)
    row = result(bars)
    assert row.wait3.status == "known" and row.wait3.fill == 1
    bars[31] = quote(31, close=95, low=90)
    row = result(bars)
    assert row.wait3.reason == "wait_structure_invalidated"
    assert row.wait3.value == 0 and row.wait3.fill == 0 and row.wait3.matured_offset == 36


def test_wait_does_not_use_late_invalidating_close_or_treat_missing_as_safe():
    bars = tape()
    bars[31] = quote(31, close=94, available=37)
    row = result(bars)
    assert row.wait3.value is None and row.wait3.fill is None
    assert row.wait3.reason == "wait_required_close_unknown"
    bars.pop(31)
    assert result(bars).wait3 == row.wait3
    bars[32] = quote(32, close=95)
    assert result(bars).wait3.value == 0  # A received invalidation is independently sufficient.


def test_early_received_new_bar_can_change_wait_reference_but_unreceived_future_cannot():
    bars = tape()
    bars[35] = quote(35, price=101, available=36)
    row = result(bars)
    assert row.wait3.reference_close == 101
    bars[35] = quote(35, price=102, available=37)
    assert result(bars).wait3.reference_close == 100


@pytest.mark.parametrize("state", ["INVALIDATED", "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP", "SESSION_END"])
def test_terminal_nonentry_states_keep_independent_path_labels(state):
    bars = tape()
    bars[34] = quote(34, high=106)
    row = result(bars, frame(state=state, terminal=True, kind="terminal"))
    assert row.entry_admissibility == "not_admissible" and row.path.value == 2
    assert row.immediate.value == row.wait3.value == 0
    assert row.immediate.fill == row.wait3.fill == 0
    confirmed = result(bars, frame(state="CONFIRMED", terminal=True, kind="terminal"))
    assert confirmed.entry_admissibility == "admissible" and confirmed.immediate.fill == 1


def test_session_cutoff_preserves_known_noentry_and_path_before_fixed_exit_boundary():
    bars = tape()
    event = frame(onset_offset=61, offset=66, decision_offset=69)
    bars[70] = quote(70, high=106)
    row = result(bars, event)
    assert row.path.value == 2 and row.path.last_offset == 99
    assert row.immediate.value == row.wait3.value == 0
    assert row.entry_reason == "expiry_not_inside_session"
    event = frame(onset_offset=60, offset=64, decision_offset=67)
    row = result(bars, event)
    assert row.immediate.fill == 1
    assert row.wait3.value == 0 and row.wait3.reason == "delayed_expiry_not_inside_session"


def test_missing_chart_is_not_a_negative_scene_or_zero_cash():
    q = labels.prepare_query(frame(), None, session_length=100)
    row = labels.build_targets(q, tape())
    assert row.entry_admissibility == "unknown" and row.path.value is None
    assert row.immediate.value is row.wait3.value is None
    assert row.immediate.fill is row.wait3.fill is None
    assert row.event_id == q.event_id


def test_zero_original_denominator_is_explicit_for_a_later_filled_wait():
    bars = tape(price=2100000)
    event = frame(scale=F(1000), structure=F(1990000), level=F(2000000))
    bars[33] = quote(33, price=1999000)
    bars[37] = quote(37, price=1999000)
    bars[67] = quote(67, price=2000000)
    row = result(bars, event)
    assert row.original_requested_notional == 0 and row.immediate.value == 0
    assert row.immediate.fill == 0
    assert row.wait3.fill == 1 and row.wait3.net_cash == F(1000) - F(1999000) * F(33, 10000)
    assert row.wait3.value is None and row.wait3.status == "undefined"
    assert row.wait3.reason == "zero_original_requested_notional"


def test_query_is_immutable_target_free_and_unchanged_when_later_outcomes_change():
    bars = tape()
    q = query(bars)
    initial = labels.build_targets(q, bars)
    bars[34] = quote(34, high=106)
    changed = labels.build_targets(q, bars)
    assert initial.path.value != changed.path.value
    assert query(bars) == q
    assert not {"path", "fill", "net_immediate", "net_wait3", "value"}.intersection(f.name for f in fields(q))
    with pytest.raises(FrozenInstanceError):
        q.reference_close = F(999)
    with pytest.raises(TypeError, match="LabelQuery"):
        labels.build_targets(initial, bars)


def test_post_horizon_suffix_and_predecision_future_prices_do_not_rewrite_sealed_targets():
    bars = tape()
    q = query(bars)
    expected = labels.build_targets(q, bars)
    for index in range(68, 100):
        bars[index] = {"unread_future": True}
    assert labels.build_targets(q, bars) == expected
    assert labels.build_targets(q, {i: b for i, b in bars.items() if i <= 67}) == expected


def test_chart_binding_and_raw_reference_conflicts_refuse():
    bars, event = tape(), frame()
    chart = chart_features(event, bars, session_length=100)
    for changes in ({"event_id": "other"}, {"decision_offset": 34}, {"reference_offset": 34},
                    {"reference_close": "100.0"}, {"state": "CONFIRMED"}):
        with pytest.raises(ValueError, match="binding|canonical"):
            labels.prepare_query(event, {**chart, **changes}, session_length=100)
    q = labels.prepare_query(event, chart, session_length=100)
    bars[30] = quote(30, price=101)
    with pytest.raises(ValueError, match="sealed reference"):
        labels.build_targets(q, bars)
    bars = tape()
    bars[32] = quote(32, available=33)
    with pytest.raises(ValueError, match="sealed reference"):
        labels.build_targets(q, bars)


def test_invalid_direct_query_state_and_price_geometry_refuse():
    q = query()
    for changes in ({"terminal": False, "state": "INVALIDATED"}, {"reference_offset": None},
                    {"reference_close": F(0)}, {"decision_offset": True}, {"scale": F(0)}):
        with pytest.raises(ValueError, match="query"):
            replace(q, **changes)
