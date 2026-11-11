"""Independent invented EL path/wait/cash reconstruction and mutation tests."""

from dataclasses import replace
from fractions import Fraction as F
import random

import pytest

from research import intraday_event_execution as execution
from research.intraday_event_features import chart_features
from research import intraday_event_labels as producer
from research import intraday_event_labels_reference as reference
from research.intraday_event_scenarios import EventFrame, MinuteBar


def tape():
    return {i: MinuteBar(i, 120, 121, 119, 120, i + 3) for i in range(100)}


def event(**changes):
    return replace(EventFrame("C_FIRSTPULLBACK", "invented:event", "invented:series", 25, 30, 33,
        21, 31, "scheduled", "PULLBACK", False, F(3), F(125), F(110), ()), **changes)


def compare(rows=None, frame=None):
    rows = tape() if rows is None else rows
    frame = event() if frame is None else frame
    chart = chart_features(frame, rows, session_length=100)
    query = producer.prepare_query(frame, chart, session_length=100)
    assert query == reference.prepare_query(frame, chart, session_length=100)
    actual = producer.build_targets(query, rows)
    reference.verify(actual, query, rows)
    return actual, query


@pytest.mark.parametrize("high,low,value", [(121, 119, 1), (129, 119, 2), (121, 110, 0), (129, 110, None)])
def test_independent_path_classes_and_nominal_cash_are_separate(high, low, value):
    rows = tape()
    rows[34] = replace(rows[34], high=F(high), low=F(low))
    result, _ = compare(rows)
    assert result.path.value == value
    assert result.immediate.value == -F(33, 10250)
    assert result.path.status == ("unknown_intrabar" if high == 129 and low == 110 else "known")


@pytest.mark.parametrize("offset", [31, 32, 33, 34, 35, 40, 64, 67])
def test_missing_causal_wait_path_or_execution_minute_preserves_its_own_unknown(offset):
    rows = tape()
    rows.pop(offset)
    result, _ = compare(rows)
    assert result.event_id == "invented:event"
    if offset in (31, 32, 33):
        assert result.wait3.reason == "wait_required_close_unknown"
    if offset == 34:
        assert result.immediate.fill is None and result.path.value is None
    if offset == 64:
        assert result.path.value == 1 and result.immediate.value is None


@pytest.mark.parametrize("offset,arrival", [(31, 37), (34, 90), (34, 100), (35, 36), (64, 99), (67, 100)])
def test_actual_consumption_maturity_is_rebuilt_without_backdating(offset, arrival):
    rows = tape()
    rows[offset] = replace(rows[offset], available_offset=arrival)
    compare(rows)


@pytest.mark.parametrize("opening", [109, 110, 123, 124])
def test_open_only_fill_and_no_fill_boundaries_keep_known_zero(opening):
    rows = tape()
    rows[34] = MinuteBar(34, opening, max(opening, 120) + 20, min(opening, 120) - 10, 120, 37)
    result, _ = compare(rows)
    assert result.immediate.fill == int(110 < opening <= 123)
    if not result.immediate.fill:
        assert result.immediate.value == 0


def test_delayed_reference_resizing_still_uses_original_notional_denominator():
    rows = tape()
    rows[33] = MinuteBar(33, 122, 123, 121, 122, 36)
    rows[37] = MinuteBar(37, 123, 124, 122, 123, 40)
    rows[67] = MinuteBar(67, 126, 127, 125, 126, 70)
    result, _ = compare(rows)
    assert result.immediate.requested_quantity != result.wait3.requested_quantity
    assert result.wait3.reference_close == 122 and result.wait3.guard_cap == 125
    assert result.immediate.denominator == result.wait3.denominator == result.original_requested_notional


def test_received_invalidation_is_sufficient_but_future_invalidation_is_not():
    rows = tape()
    rows.pop(31)
    rows[32] = MinuteBar(32, 120, 121, 109, 110, 35)
    result, _ = compare(rows)
    assert result.wait3.value == 0 and result.wait3.reason == "wait_structure_invalidated"
    rows[32] = replace(rows[32], available_offset=37)
    result, _ = compare(rows)
    assert result.wait3.value is None and result.wait3.reason == "wait_required_close_unknown"
    previous = result
    rows[32] = replace(rows[32], low=F(100), close=F(105))
    assert compare(rows)[0] == previous


@pytest.mark.parametrize("state", ["CONFIRMED", "INVALIDATED", "UNKNOWN_GAP", "SESSION_END"])
def test_terminal_admission_and_forecast_population_remain_independent(state):
    result, _ = compare(frame=event(state=state, terminal=True, kind="terminal"))
    assert result.path.value == 1
    assert result.immediate.fill == int(state == "CONFIRMED")


def test_missing_reference_and_calendar_cutoffs_are_not_fabricated_zero_forecasts():
    query = producer.prepare_query(event(), None, session_length=100)
    actual = producer.build_targets(query, tape())
    reference.verify(actual, query, tape())
    assert actual.path.value is actual.immediate.value is actual.wait3.value is None
    for decision in (67, 69, 75):
        compare(frame=event(onset_offset=60, offset=decision - 3, decision_offset=decision))


def test_reference_survives_producer_helper_and_execution_poisoning(monkeypatch):
    rows = tape()
    result, query = compare(rows)
    def forbidden(*args, **kwargs):
        pytest.fail("Producer label/normalization/evaluator used as the independent oracle")
    for name in ("prepare_query", "build_targets", "_reference", "_path", "_mature", "_cash",
                 "_execution_target", "_waiting", "_notional"):
        monkeypatch.setattr(producer, name, forbidden)
    monkeypatch.setattr(execution, "evaluate", forbidden)
    reference.verify(result, query, rows)


@pytest.mark.parametrize("field,value", [("value", 0), ("upper", F(130)), ("lower", F(109)),
    ("matured_offset", 35), ("first_offset", 33), ("last_offset", 64), ("observed_through", 34),
    ("status", "unknown"), ("reason", "invented")])
def test_path_value_geometry_scope_and_maturity_mutations_are_detected(field, value):
    result, query = compare()
    altered = replace(result, path=replace(result.path, **{field: value}))
    with pytest.raises(ValueError, match="independent offline targets"):
        reference.verify(altered, query, tape())


@pytest.mark.parametrize("field,value", [("value", F(0)), ("net_cash", F(0)), ("fill", 0),
    ("denominator", F(1)), ("requested_quantity", 1), ("matured_offset", 33),
    ("fill_matured_offset", 33), ("reference_close", F(121)), ("guard_cap", F(124)),
    ("expiry_offset", 63), ("status", "no_fill")])
def test_each_cash_arm_and_its_common_denominator_is_independently_checked(field, value):
    result, query = compare()
    for name in ("immediate", "wait3"):
        altered = replace(result, **{name: replace(getattr(result, name), **{field: value})})
        with pytest.raises(ValueError, match="independent offline targets"):
            reference.verify(altered, query, tape())


def test_producer_path_threshold_mutation_is_detected(monkeypatch):
    rows = {i: MinuteBar(i, 120, F(1201, 10), F(1199, 10), 120, i + 3) for i in range(100)}
    rows[34] = replace(rows[34], high=F(241, 2))
    frame = event(scale=F(1, 20), structure=F(119))
    _, query = compare(rows, frame)
    monkeypatch.setattr(producer, "UPPER_BPS", 0)
    wrong = producer.build_targets(query, rows)
    assert wrong.path.value == 2
    with pytest.raises(ValueError, match="independent offline targets"):
        reference.verify(wrong, query, rows)


def test_producer_cash_denominator_mutation_is_detected(monkeypatch):
    _, query = compare()
    original = producer._notional
    monkeypatch.setattr(producer, "_notional", lambda q: original(q) / 2)
    with pytest.raises(ValueError, match="independent offline targets"):
        reference.verify(producer.build_targets(query, tape()), query, tape())


def test_future_suffix_never_changes_closed_targets_or_the_query():
    rows = tape()
    result, query = compare(rows)
    for offset in range(68, 100):
        rows[offset] = object()
    reference.verify(result, query, rows)
    assert reference.build_targets(query, {i: v for i, v in rows.items() if i <= 67}) == result


@pytest.mark.parametrize("seed", range(8))
def test_varied_exact_future_paths_unknowns_and_receipt_clocks_match(seed):
    rng, rows = random.Random(seed), tape()
    for offset in range(31, 80):
        if rng.randrange(18) == 0:
            rows.pop(offset)
            continue
        price = F(345 + rng.randrange(31), 3)
        close = price + F(rng.randrange(-3, 4), 3)
        rows[offset] = MinuteBar(offset, price, max(price, close) + F(4, 3),
            min(price, close) - F(5, 3), close, offset + rng.choice((1, 3, 3, 8)))
    compare(rows)
