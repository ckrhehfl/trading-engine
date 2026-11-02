"""Invented minute sessions only; no actual archive/source authentication."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import Minute, SessionInputs
from research.intraday_pattern_events import PatternValidationError
from research import intraday_trader_scenarios as scenarios
from research import intraday_trader_session as session


D = Decimal
M = timedelta(minutes=1)
OPEN = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
CLOSE = OPEN + 390 * M
SYMBOL = "KRX:ABC123"


def minute(at, op="100", high="110", low="90", close="100"):
    return Minute(SYMBOL, at, at + 3 * M,
                  tuple(D(value) for value in (op, high, low, close)), None,
                  volume_state="missing")


def opening():
    return [("106" if n == 3 else "108" if n == 6 else "100", "110", "90")
            for n in range(1, 7)]


def a_bars():
    return opening() + [("116", "120", "111"), ("114", "118", "112"),
                        ("115", "117", "113"), ("118", "119", "114")]


def b_bars():
    return opening() + [("88", "150", "80")] + [("91", "95", "82")] * 4 + [
        ("96", "97", "83")]


def c_bars():
    result = opening() + [("111", "112", "99")]
    result[0] = ("100", "110", "80")
    return result


def minutes(bars):
    """Five identical OHLC minutes give each hand-specified aggregate exactly."""
    return {OPEN + (index * 5 + offset) * M:
            minute(OPEN + (index * 5 + offset) * M, close, high, low, close)
            for index, (close, high, low) in enumerate(bars) for offset in range(5)}


def facts(current="100"):
    available = OPEN - timedelta(days=3)
    return (
        scenarios.ProfitFact(SYMBOL, "invented-current", "invented-content", 2025, 3,
                             D(current), "KRW", "million", "consolidated",
                             date(2025, 9, 30), available, True),
        scenarios.ProfitFact(SYMBOL, "invented-prior", "invented-content", 2024, 3,
                             D("-10"), "KRW", "million", "consolidated",
                             date(2024, 9, 30), available, True),
    )


def add_target_path(rows, signal_minutes, entry, target, exit_open):
    at = OPEN + (signal_minutes + 3) * M
    rows[at] = minute(at, entry, target, entry, target)
    rows[at + 4 * M] = replace(minute(at + 4 * M, exit_open),
                                ohlc=(D(exit_open), object(), object(), object()))


class CoordinatesOnly(Mapping):
    def __init__(self, rows, allowed=None):
        self.rows = rows
        self.allowed = set(rows) if allowed is None else allowed
        self.reads = []

    def __getitem__(self, key):
        self.reads.append(key)
        assert key in self.allowed, f"unexpected invented coordinate: {key}"
        return self.rows[key]

    def __len__(self):
        raise AssertionError("whole-source length")

    def __iter__(self):
        raise AssertionError("whole-source iteration")


def evaluate(rows, selected=("EE-C",), **kwargs):
    return session.evaluate_session(SessionInputs(SYMBOL, OPEN, CLOSE, rows),
                                    scenarios=selected, **kwargs)


def one(rows, selected=("EE-C",), **kwargs):
    return evaluate(rows, selected, **kwargs).results[0]


@pytest.mark.parametrize("family,bars,signal,stop,target,entry,exit_open,gross", [
    ("EE-A", a_bars, 50, "112", "120", "117", "121", Fraction(4, 117)),
    ("EE-B", b_bars, 60, "80", "110", "97", "109", Fraction(12, 97)),
    ("EE-C", c_bars, 35, "90", "153", "112", "154", Fraction(3, 8)),
])
def test_minutes_to_three_candidates_and_delayed_paths_are_hand_computed(
        family, bars, signal, stop, target, entry, exit_open, gross):
    rows = minutes(bars())
    add_target_path(rows, signal, entry, target, exit_open)
    source = CoordinatesOnly(rows)
    result = one(source, (family,), profit_facts=facts(), event_session_admitted=True)
    candidate = result.detection.candidate
    assert result.detection.state == "candidate" and candidate.eligible is True
    assert candidate.signal.end_at == OPEN + signal * M
    assert candidate.available_at == OPEN + (signal + 2) * M
    assert (candidate.stop, candidate.target) == (D(stop), D(target))
    outcome = result.path
    assert result.path_refusal is None and result.issues == ()
    assert outcome.status == "traded" and outcome.reason == "known_target_close_reached"
    assert outcome.entry_at == OPEN + (signal + 3) * M
    assert outcome.trigger_at == OPEN + (signal + 6) * M
    assert outcome.exit_at == OPEN + (signal + 7) * M
    assert outcome.gross == gross
    assert outcome.net33 == gross - Fraction(33, 10000)
    assert outcome.net43 == gross - Fraction(43, 10000)
    assert max(source.reads) == outcome.exit_at


def test_opening_aligned_aggregation_uses_first_open_max_high_min_low_last_close():
    rows = minutes(c_bars())
    values = [("111", "111", "110", "111"), ("111", "115", "108", "112"),
              ("112", "114", "109", "113"), ("113", "114", "110", "112"),
              ("112", "113", "111", "112")]
    for n, value in enumerate(values):
        at = OPEN + (30 + n) * M
        # Exactly the block deadline is admissible, including the first minute.
        rows[at] = replace(minute(at, *value), available_at=OPEN + 37 * M)
    result = one(CoordinatesOnly(rows), event_session_admitted=True)
    parent = result.detection.parent
    assert (parent.start_at, parent.end_at, parent.available_at) == (
        OPEN + 30 * M, OPEN + 35 * M, OPEN + 37 * M)
    assert (parent.open, parent.high, parent.low, parent.close) == tuple(
        D(value) for value in ("111", "115", "108", "112"))
    assert result.detection.candidate.stop == D("90")  # First old low80 is outside last five.
    assert result.detection.candidate.target == D("156")
    assert result.path.reason == "admission_unknown"


def test_default_six_rows_unknowns_and_opaque_original_metadata_are_retained():
    source = CoordinatesOnly({}, {OPEN})
    metadata = b'opaque caller evidence\x00not json and not an authentication claim'
    result = session.evaluate_session(SessionInputs(SYMBOL, OPEN, CLOSE, source),
                                      source_metadata=metadata)
    assert (result.symbol, result.opening, result.closing) == (SYMBOL, OPEN, CLOSE)
    assert result.source_metadata is metadata
    assert tuple(row.scenario_id for row in result.results) == scenarios.SCENARIOS
    assert source.reads == [OPEN]  # The demanded gap projection is shared.
    for row in result.results:
        assert row.detection.state == "unknown" and row.path is row.path_refusal is None
        assert row.issues == (session.ProjectionIssue(OPEN, "missing_minute"),)
    with pytest.raises(FrozenInstanceError):
        result.symbol = "changed"
    with pytest.raises(FrozenInstanceError):
        result.results[0].path_refusal = "changed"
    with pytest.raises(FrozenInstanceError):
        result.results[0].issues[0].reason = "changed"


def test_two_controls_share_only_demanded_projection_without_mapping_iteration():
    source = CoordinatesOnly(minutes(c_bars()))
    result = evaluate(source, ("EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"),
                      event_session_admitted=True)
    assert source.reads == [OPEN + n * M for n in range(35)]
    assert all(row.detection.state == "candidate" for row in result.results)
    assert all(row.path.status == "unknown" for row in result.results)


@pytest.mark.parametrize("state", ["missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"])
def test_nonobserved_price_stops_prefix_without_flat_fill_or_volume_requirement(state):
    rows = minutes(c_bars())
    at = OPEN + 32 * M
    rows[at] = replace(rows[at], ohlc=None, price_state=state, shares=None)
    source = CoordinatesOnly(rows, {OPEN + n * M for n in range(33)})
    result = one(source)
    assert result.detection.state == "unknown"
    assert result.detection.observed_through == OPEN + 30 * M
    assert result.issues == (session.ProjectionIssue(at, f"price_{state}"),)
    assert result.path is None and max(source.reads) == at


def test_missing_minute_stops_at_requested_coordinate_not_later_suffix():
    rows = minutes(c_bars())
    at = OPEN + 31 * M
    del rows[at]
    source = CoordinatesOnly(rows, {OPEN + n * M for n in range(32)})
    result = one(source)
    assert result.detection.state == "unknown"
    assert result.issues == (session.ProjectionIssue(at, "missing_minute"),)
    assert max(source.reads) == at


class NoPriceAccess(Minute):
    def __getattribute__(self, name):
        if name == "ohlc":
            raise AssertionError("late or impossible clock must not reveal prices")
        return super().__getattribute__(name)


@pytest.mark.parametrize("offset", [0, 3, 4])
def test_late_publication_is_unknown_before_poison_price_access(offset):
    rows = minutes(c_bars())
    at = OPEN + (30 + offset) * M
    rows[at] = NoPriceAccess(SYMBOL, at, OPEN + 37 * M + timedelta(microseconds=1),
                             object(), None)
    source = CoordinatesOnly(rows, {OPEN + n * M for n in range(31 + offset)})
    result = one(source)
    assert result.detection.state == "unknown"
    assert result.issues == (session.ProjectionIssue(at, "price_unavailable_by_bar_deadline"),)
    assert max(source.reads) == at


def test_impossible_early_clock_is_invalid_before_poison_price_access():
    source = CoordinatesOnly({OPEN: NoPriceAccess(SYMBOL, OPEN, OPEN, object(), None)})
    result = one(source)
    assert result.detection.state == "invalid"
    assert result.detection.reason == "minute_availability_precedes_completion"
    assert result.path is None


class NoVolumeAccess(Minute):
    def __getattribute__(self, name):
        if name in {"shares", "volume_state"}:
            raise AssertionError("price-only scenarios must not inspect volume")
        return super().__getattribute__(name)


def test_unused_volume_payload_and_state_are_never_inspected():
    rows = {at: NoVolumeAccess(row.symbol, at, row.available_at, row.ohlc, object(),
                              volume_state=object()) for at, row in minutes(c_bars()).items()}
    result = one(CoordinatesOnly(rows), event_session_admitted=True)
    assert result.detection.state == "candidate" and result.path.status == "unknown"
    assert result.issues == ()


@pytest.mark.parametrize("current,admitted,expected", [("0", True, "declined"),
                                                       ("100", None, "unknown"),
                                                       ("100", False, "declined")])
def test_c_unadmitted_candidate_never_requests_future_price(current, admitted, expected):
    source = CoordinatesOnly(minutes(c_bars()))
    result = one(source, profit_facts=facts(current), event_session_admitted=admitted)
    assert result.detection.state == "candidate"
    assert result.path.status == expected and result.path.entry_at is None
    assert result.path.gross is result.path.net33 is result.path.net43 is None
    assert max(source.reads) == OPEN + 34 * M


def test_false_profit_selected_declines_while_control_uses_same_candidate_path():
    rows = minutes(c_bars())
    add_target_path(rows, 35, "112", "153", "154")
    result = evaluate(CoordinatesOnly(rows), ("EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"),
                      profit_facts=facts("0"), event_session_admitted=True)
    left, right = result.results
    assert left.detection.candidate.signal == right.detection.candidate.signal
    assert left.detection.candidate.stop == right.detection.candidate.stop
    assert left.detection.candidate.target == right.detection.candidate.target
    assert left.path.status == "declined" and right.path.status == "traded"
    assert right.path.gross == Fraction(3, 8)


def test_finished_control_event_and_path_survive_later_invalid_primary_suffix():
    rows = minutes(b_bars())
    add_target_path(rows, 40, "97", "110", "109")
    # This exit OPEN also lies in the primary's base; keep its aggregate valid.
    at = OPEN + 47 * M
    rows[at] = minute(at, "109", "109", "109", "109")
    bad_at = OPEN + 50 * M
    rows[bad_at] = replace(rows[bad_at], symbol="KRX:BAD999")
    source = CoordinatesOnly(rows, {OPEN + n * M for n in range(51)})
    primary, control = evaluate(source, ("EE-B", "EE-B-EARLY-RECLAIM")).results
    assert primary.detection.state == "invalid"
    assert primary.issues == (session.ProjectionIssue(bad_at, "minute_identity_changed"),)
    assert control.detection.state == "candidate" and control.issues == ()
    assert control.detection.observed_through == OPEN + 40 * M
    assert control.path.status == "traded" and control.path.exit_at == OPEN + 47 * M
    assert source.reads.count(bad_at) == 1 and max(source.reads) == bad_at


def test_previous_terminal_is_explicit_per_scenario_without_control_exit_leak():
    bars = opening() + [("88", "150", "80"), ("91", "95", "82"),
                        ("93", "95", "82"), ("88", "94", "80")]
    bars += [("91", "95", "82")] * 4 + [("96", "97", "83")]
    rows = minutes(bars)
    add_target_path(rows, 40, "97", "110", "109")
    rows[OPEN + 47 * M] = minute(OPEN + 47 * M, "109", "109", "109", "109")
    add_target_path(rows, 75, "97", "110", "109")
    previous = scenarios.PreviousTerminal("EE-B", SYMBOL, OPEN, 1, OPEN + 35 * M,
                                          OPEN + 42 * M, "closed")
    primary, control = evaluate(CoordinatesOnly(rows), ("EE-B", "EE-B-EARLY-RECLAIM"),
                                previous={"EE-B": previous}).results
    assert primary.detection.attempt_number == 2
    assert primary.detection.original_parent_end_at == OPEN + 35 * M
    assert primary.detection.candidate.parent.end_at == OPEN + 50 * M
    assert primary.detection.candidate.signal.end_at == OPEN + 75 * M
    assert primary.detection.candidate.previous_terminal is previous
    assert control.detection.attempt_number == 1
    assert control.detection.candidate.previous_terminal is None
    assert control.detection.candidate.signal.end_at == OPEN + 40 * M
    assert primary.path.status == control.path.status == "traded"


def test_path_integrity_refusal_is_separate_from_detection_and_missing_path():
    rows = minutes(c_bars())
    at = OPEN + 38 * M
    rows[at] = replace(minute(at), ohlc=(D("NaN"), object(), object(), object()))
    result = one(CoordinatesOnly(rows), profit_facts=facts(), event_session_admitted=True)
    assert result.detection.state == "candidate" and result.issues == ()
    assert result.path is None and result.path_refusal == "positive_finite_decimal_price_required"
    source = CoordinatesOnly(minutes(c_bars()), {OPEN + n * M for n in (*range(35), 38)})
    unknown = one(source, profit_facts=facts(), event_session_admitted=True)
    assert unknown.path.status == "unknown" and unknown.path.reason == "missing_open_minute"
    assert unknown.path.gross is None and unknown.path_refusal is None


@pytest.mark.parametrize("changes,reason", [
    ({"symbol": "KRX:BAD999"}, "minute_identity_changed"),
    ({"start_at": OPEN - M}, "minute_identity_changed"),
    ({"start_at": OPEN.replace(tzinfo=None)}, "aware_clock_required"),
    ({"available_at": OPEN.replace(tzinfo=None)}, "aware_clock_required"),
    ({"available_at": OPEN}, "minute_availability_precedes_completion"),
    ({"price_state": "invented_invalid_state"}, "invalid_price_state"),
    ({"price_state": "missing"}, "unknown_price_has_payload"),
    ({"ohlc": None}, "observed_price_requires_ohlc"),
    ({"ohlc": (D("100"),)}, "observed_price_requires_ohlc"),
    ({"ohlc": (D("100"), D("99"), D("90"), D("100"))}, "invalid_minute_ohlc_order"),
    ({"ohlc": (D("100"), D("110"), D("101"), D("105"))}, "invalid_minute_ohlc_order"),
])
def test_malformed_demanded_identity_clocks_and_ohlc_are_invalid(changes, reason):
    source = CoordinatesOnly({OPEN: replace(minute(OPEN), **changes)})
    result = one(source)
    assert result.detection.state == "invalid" and result.detection.reason == reason
    assert result.issues == (session.ProjectionIssue(OPEN, reason),)
    assert result.path is result.path_refusal is None


@pytest.mark.parametrize("bad", [100, 100.0, D("NaN"), D("Infinity"), D("0"), D("-1"), object()])
def test_non_decimal_nonfinite_and_nonpositive_prices_refuse(bad):
    source = CoordinatesOnly({OPEN: replace(minute(OPEN), ohlc=(bad, D("110"), D("90"), D("100")))})
    result = one(source)
    assert result.detection.state == "invalid"
    assert result.detection.reason == "positive_finite_decimal_price_required"


def test_duplicate_source_identity_at_next_key_refuses_only_when_demanded():
    rows = {OPEN: minute(OPEN), OPEN + M: minute(OPEN)}
    result = one(CoordinatesOnly(rows))
    assert result.detection.state == "invalid"
    assert result.issues == (session.ProjectionIssue(OPEN + M, "minute_identity_changed"),)


def test_untyped_demanded_minute_is_invalid_without_fabricated_path():
    result = one(CoordinatesOnly({OPEN: object()}))
    assert result.detection.state == "invalid"
    assert result.issues == (session.ProjectionIssue(OPEN, "typed_minute_required"),)
    assert result.path is result.path_refusal is None


def test_complete_no_candidate_prefix_reads_only_through_registered_cutoff():
    source = CoordinatesOnly(minutes([("100", "110", "90")] * 47))
    result = evaluate(source, scenarios.SCENARIOS)
    assert all(row.detection.state == "known_no_candidate" for row in result.results)
    assert all(row.detection.reason == "decision_cutoff" for row in result.results)
    assert all(row.path is None and row.issues == () for row in result.results)
    assert source.reads == [OPEN + n * M for n in range(235)]
    assert max(source.reads) == OPEN + 234 * M


@pytest.mark.parametrize("selected", [("EE-C", "EE-C"), ("unknown",), (), ["EE-C"]])
def test_invalid_policy_selection_is_rejected_before_any_minutes(selected):
    source = CoordinatesOnly({}, set())
    with pytest.raises(PatternValidationError, match="invalid_scenario_selection"):
        evaluate(source, selected)
    assert source.reads == []


@pytest.mark.parametrize("changes", [
    {"symbol": "invented_bad_identity"}, {"minutes": []},
    {"opening": OPEN.replace(tzinfo=None)},
    {"opening": OPEN + timedelta(seconds=1)},
    {"closing": OPEN}, {"closing": CLOSE + M},
    {"closing": OPEN + timedelta(days=1)},
])
def test_session_identity_and_aware_grid_are_validated_without_scanning(changes):
    source = CoordinatesOnly({}, set())
    value = replace(SessionInputs(SYMBOL, OPEN, CLOSE, source), **changes)
    with pytest.raises(PatternValidationError):
        session.evaluate_session(value)
    assert source.reads == []


@pytest.mark.parametrize("kwargs", [{"source_metadata": "not bytes"}, {"previous": []}])
def test_invalid_metadata_or_previous_shape_refuses_before_price_access(kwargs):
    source = CoordinatesOnly({}, set())
    with pytest.raises(PatternValidationError):
        evaluate(source, **kwargs)
    assert source.reads == []


@pytest.mark.parametrize("key", ["EE-C-typo", 1, None])
def test_unregistered_previous_key_refuses_before_price_access(key):
    source = CoordinatesOnly({}, set())
    with pytest.raises(PatternValidationError, match="invalid_previous_scenario"):
        evaluate(source, previous={key: None})
    assert source.reads == []


def test_registered_previous_outside_selected_subset_is_not_interpreted():
    source = CoordinatesOnly({}, {OPEN})
    result = one(source, previous={"EE-A": object(), "EE-C-WITHOUT-PROFIT-CONTENT": object()})
    assert result.detection.state == "unknown"
    assert source.reads == [OPEN]


def test_source_exception_propagates_without_a_zero_or_unknown_substitution():
    class FailedSource(CoordinatesOnly):
        def __getitem__(self, key):
            raise RuntimeError("invented source failure")

    with pytest.raises(RuntimeError, match="invented source failure"):
        one(FailedSource({}))
