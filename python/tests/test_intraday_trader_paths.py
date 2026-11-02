"""Invented-only exact price paths: causal delay, missingness and no fills claim."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import Minute
from research import intraday_trader_paths as paths


D = Decimal
M = timedelta(minutes=1)
OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))
ENTRY = OPEN + 33 * M


def plan(**changes):
    return replace(paths.PathPlan("EE-A", "INVENTED", OPEN + 32 * M, D("90"),
                                  D("110"), OPEN, OPEN + 390 * M, "pass"), **changes)


def minute(at, *, op="100", high="105", low="95", close="101"):
    return Minute("INVENTED", at, at + 3 * M,
                  tuple(D(value) for value in (op, high, low, close)), None,
                  volume_state="missing")


def rows():
    return {ENTRY + index * M: minute(ENTRY + index * M) for index in range(61)}


def changed(source, index, **values):
    at = ENTRY + index * M
    output = dict(source)
    output[at] = minute(at, **values)
    return output


class OnlyKeys(Mapping):
    """No scanning, no lookahead and no unrelated coordinate reads."""

    def __init__(self, source, allowed):
        self.source, self.allowed = source, allowed
        self.reads = []

    def __getitem__(self, key):
        self.reads.append(key)
        assert key in self.allowed, f"unexpected source access: {key}"
        return self.source[key]

    def __len__(self):
        raise AssertionError("whole-source length")

    def __iter__(self):
        raise AssertionError("whole-source iteration")


def assert_no_returns(outcome):
    assert outcome.gross is outcome.net33 is outcome.net43 is None


def test_target_close_exits_at_delayed_open_with_hand_computed_labels():
    source = changed(rows(), 1, high="115", close="110")
    source = changed(source, 5, op="112")
    source = OnlyKeys(source, {ENTRY, ENTRY + M, ENTRY + 5 * M})
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.status == "traded"
    assert outcome.reason == "known_target_close_reached"
    assert outcome.entry_at == ENTRY
    assert outcome.trigger_minute == ENTRY + M
    assert outcome.trigger_at == ENTRY + 4 * M
    assert outcome.exit_at == ENTRY + 5 * M
    assert outcome.entry_open == D("100") and outcome.exit_open == D("112")
    assert outcome.gross == Fraction(3, 25)
    assert outcome.net33 == Fraction(1167, 10000)
    assert outcome.net43 == Fraction(1157, 10000)


def test_equal_stop_close_has_priority_over_response_and_never_gets_stop_fill():
    source = {ENTRY + index * M: minute(ENTRY + index * M, close="100") for index in range(10)}
    source = changed(source, 9, low="85", close="90")
    source[ENTRY + 13 * M] = minute(ENTRY + 13 * M, op="87")
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.reason == "structural_close_invalidation"
    assert outcome.trigger_at == ENTRY + 12 * M
    assert outcome.exit_at == ENTRY + 13 * M
    assert outcome.gross == Fraction(-13, 100)
    assert outcome.net33 == Fraction(-1333, 10000)


def test_time_cap_needs_no_irrelevant_last_four_closes_or_exit_hlc():
    source = rows()
    for index in range(56, 60):
        del source[ENTRY + index * M]
    source[ENTRY + 60 * M] = replace(minute(ENTRY + 60 * M, op="103"),
                                    available_at="archived-future-clock-not-open-clock",
                                    ohlc=(D("103"), object(), object(), object()))
    source = OnlyKeys(source, {ENTRY + index * M for index in (*range(56), 60)})
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.status == "traded" and outcome.reason == "time_cap"
    assert outcome.exit_at == ENTRY + 60 * M
    assert outcome.gross == Fraction(3, 100)
    assert outcome.net33 == Fraction(267, 10000)
    assert outcome.trigger_at is None


def test_response_requires_ten_available_closes_and_uses_next_open():
    source = {ENTRY + index * M: minute(ENTRY + index * M, close="100") for index in range(10)}
    source = changed(source, 9, close="99")
    source[ENTRY + 13 * M] = minute(ENTRY + 13 * M, op="98")
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.reason == "response_failure"
    assert outcome.trigger_minute == ENTRY + 9 * M
    assert outcome.trigger_at == ENTRY + 12 * M
    assert outcome.exit_at == ENTRY + 13 * M
    assert outcome.gross == Fraction(-1, 50)


def test_response_equal_latest_does_not_exit_and_prior_positive_close_disables_it():
    source = {ENTRY + index * M: minute(ENTRY + index * M, close="100") for index in range(61)}
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.reason == "time_cap"
    source = changed(source, 0, close="101")
    for index in range(1, 56):
        source = changed(source, index, close="99")
    assert paths.evaluate_path(plan(), source).reason == "time_cap"


def test_response_can_first_fire_after_ten_when_latest_becomes_strictly_lower():
    source = {ENTRY + index * M: minute(ENTRY + index * M, close="100") for index in range(11)}
    source = changed(source, 10, close="99")
    source[ENTRY + 14 * M] = minute(ENTRY + 14 * M, op="99")
    assert paths.evaluate_path(plan(), source).exit_at == ENTRY + 14 * M


@pytest.mark.parametrize("admission, expected", [("decline", "declined"), ("unknown", "unknown")])
def test_unadmitted_plan_never_touches_price_source(admission, expected):
    source = OnlyKeys({}, set())
    outcome = paths.evaluate_path(plan(admission=admission), source)
    assert outcome.status == expected and source.reads == []
    assert_no_returns(outcome)


@pytest.mark.parametrize("stop,target", [("100", "110"), ("90", "100"), ("101", "110"), ("90", "99")])
def test_entry_gate_strict_bounds_decline_without_held_close_access(stop, target):
    source = {ENTRY: replace(minute(ENTRY), ohlc=(D("100"), object(), object(), object()))}
    outcome = paths.evaluate_path(plan(stop=D(stop), target=D(target)), source)
    assert outcome.status == "declined" and outcome.reason == "entry_outside_frozen_levels"
    assert_no_returns(outcome)


def test_exact_primary_cost_equality_declines_and_43bp_does_not_change_gate():
    source = {ENTRY: minute(ENTRY)}
    outcome = paths.evaluate_path(plan(target=D("100.33")), source)
    assert outcome.status == "declined" and outcome.reason == "entry_cost_space_not_strict"
    source = changed(rows(), 0, high="101", close="100.34")
    source = changed(source, 4, op="100.4")
    outcome = paths.evaluate_path(plan(target=D("100.34")), source)
    assert outcome.status == "traded" and outcome.net43 < 0 < outcome.net33


def test_arithmetic_and_gate_are_exact_under_low_decimal_precision():
    source = changed(rows(), 0, high="120", close="110")
    source = changed(source, 4, op="112.12345678901234567890123456789")
    expected = Fraction(D("112.12345678901234567890123456789")) / 100 - 1
    with localcontext() as context:
        context.prec = 2
        outcome = paths.evaluate_path(plan(), source)
        assert outcome.gross == expected
        assert outcome.net33 == expected - Fraction(33, 10000)
        assert outcome.net43 == expected - Fraction(43, 10000)
        assert paths.evaluate_path(plan(target=D("100.33")), {ENTRY: minute(ENTRY)}).status == "declined"


@pytest.mark.parametrize("decision", [OPEN + 32 * M, OPEN + 32 * M + timedelta(seconds=59, microseconds=999999)])
def test_entry_is_first_whole_minute_strictly_after_decision(decision):
    outcome = paths.evaluate_path(plan(decision_at=decision), rows())
    assert outcome.entry_at == ENTRY


def test_same_day_cap_equality_allowed_but_insufficient_room_never_reads_source():
    assert paths.evaluate_path(plan(closing=ENTRY + 60 * M), rows()).status == "traded"
    source = OnlyKeys({}, set())
    outcome = paths.evaluate_path(plan(closing=ENTRY + 59 * M), source)
    assert outcome.status == "declined" and source.reads == []
    assert_no_returns(outcome)


@pytest.mark.parametrize("index", [0, 1, 55])
def test_required_missing_price_is_sticky_unknown_even_with_later_target(index):
    source = changed(rows(), 56, high="120", close="110")
    del source[ENTRY + index * M]
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.status == "unknown"
    assert_no_returns(outcome)


@pytest.mark.parametrize("state", ["missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"])
def test_nonobserved_required_price_is_unknown_never_zero(state):
    source = rows()
    source[ENTRY + M] = replace(source[ENTRY + M], price_state=state, ohlc=None)
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.status == "unknown" and outcome.reason == f"held_price_{state}"
    assert_no_returns(outcome)


def test_missing_scheduled_exit_open_is_unknown_without_consuming_later_closes():
    source = changed(rows(), 0, low="85", close="90")
    del source[ENTRY + 4 * M]
    source = OnlyKeys(source, {ENTRY, ENTRY + 4 * M})
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.status == "unknown" and outcome.reason == "missing_open_minute"
    assert outcome.exit_at == ENTRY + 4 * M
    assert_no_returns(outcome)


def test_entry_and_exit_open_only_checks_ignore_their_archived_future_hlc():
    source = changed(rows(), 0, high="120", close="110")
    source[ENTRY + 4 * M] = replace(minute(ENTRY + 4 * M), available_at=ENTRY + 100 * M,
                                   ohlc=(D("102"), D("NaN"), D("-3"), object()))
    outcome = paths.evaluate_path(plan(), OnlyKeys(source, {ENTRY, ENTRY + 4 * M}))
    assert outcome.status == "traded" and outcome.gross == Fraction(1, 50)
    declined = {ENTRY: replace(minute(ENTRY), available_at="unused",
                                ohlc=(D("100"), D("NaN"), D("-3"), object()))}
    assert paths.evaluate_path(plan(stop=D("100")), declined).status == "declined"


def test_held_open_is_not_required_but_matured_hlc_are_required():
    source = rows()
    source[ENTRY + M] = replace(source[ENTRY + M], ohlc=(object(), D("105"), D("95"), D("101")))
    assert paths.evaluate_path(plan(), source).status == "traded"


def test_simultaneous_stop_target_touch_is_only_unknown_order_diagnostic():
    source = changed(rows(), 0, high="111", low="89", close="101")
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.reason == "time_cap" and outcome.gross == 0
    assert outcome.simultaneous_touch_minutes == (ENTRY,)
    assert any("not certified executable" in text for text in outcome.assumptions)
    assert any("not account PnL" in text for text in outcome.assumptions)


@pytest.mark.parametrize("available", [ENTRY + M - timedelta(microseconds=1),
                                        ENTRY + 3 * M + timedelta(microseconds=1),
                                        ENTRY.replace(tzinfo=None)])
def test_impossible_or_future_held_close_clock_refuses(available):
    source = rows()
    source[ENTRY] = replace(source[ENTRY], available_at=available)
    with pytest.raises(paths.PathRefusal):
        paths.evaluate_path(plan(), source)


@pytest.mark.parametrize("available", [ENTRY + M, ENTRY + 3 * M])
def test_available_clock_equal_bounds_are_allowed_but_delay_stays_fixed(available):
    source = changed(rows(), 0, low="85", close="90")
    source[ENTRY] = replace(source[ENTRY], available_at=available)
    outcome = paths.evaluate_path(plan(), source)
    assert outcome.trigger_at == ENTRY + 3 * M and outcome.exit_at == ENTRY + 4 * M


@pytest.mark.parametrize("fields", [{"symbol": "OTHER"}, {"start_at": ENTRY + M},
                                    {"price_state": "invented_state"},
                                    {"price_state": "missing"}, {"ohlc": None},
                                    {"ohlc": (D("100"), D("99"), D("95"), D("101"))},
                                    {"ohlc": (D("100"), D("105"), D("95"), 101.0)},
                                    {"ohlc": (D("100"), D("NaN"), D("95"), D("101"))}])
def test_malformed_held_identity_state_shape_hlc_or_decimal_refuses(fields):
    source = rows()
    source[ENTRY] = replace(source[ENTRY], **fields)
    with pytest.raises(paths.PathRefusal):
        paths.evaluate_path(plan(), source)


@pytest.mark.parametrize("value", [100, 100.0, D("NaN"), D("Infinity"), D("0"), D("-1")])
def test_malformed_open_decimal_refuses(value):
    source = {ENTRY: replace(minute(ENTRY), ohlc=(value, object(), object(), object()))}
    with pytest.raises(paths.PathRefusal):
        paths.evaluate_path(plan(), source)


@pytest.mark.parametrize("changes", [{"decision_at": OPEN.replace(tzinfo=None)},
                                      {"opening": OPEN + timedelta(seconds=1)},
                                      {"closing": OPEN + timedelta(days=1)},
                                      {"admission": "maybe"}, {"stop": D("NaN")},
                                      {"target": 110.0}, {"symbol": ""}, {"family": ""}])
def test_invalid_plan_refuses_before_price_access(changes):
    with pytest.raises(paths.PathRefusal):
        paths.evaluate_path(plan(**changes), OnlyKeys({}, set()))


def test_plan_and_outcome_are_frozen():
    value = plan(admission="decline")
    with pytest.raises(FrozenInstanceError):
        value.stop = D("80")
    outcome = paths.evaluate_path(value, OnlyKeys({}, set()))
    with pytest.raises(FrozenInstanceError):
        outcome.status = "traded"
