"""Invented fixtures for the pure fixed DZ signal boundary."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from research import intraday_autonomous_signals as signals
from research import intraday_pattern_events as core


OPEN = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
CLOSE = OPEN + timedelta(minutes=390)
D = Decimal


def bar(n, *, op="100", hi="110", lo="90", cl="100"):
    end = OPEN + timedelta(minutes=5 * n)
    return core.Bar(end - timedelta(minutes=5), end, end + timedelta(minutes=2),
                    D(op), D(hi), D(lo), D(cl))


def run(h, bars, *, closing=CLOSE):
    return signals.detect_bars(signals.HYPOTHESES[h - 1], bars, opening=OPEN, closing=closing)


def morning_up():
    return [bar(n, op="100", hi="112", lo="98", cl="110") for n in range(1, 13)]


def h1_signal():
    return morning_up() + [
        bar(13, op="109", hi="110", lo="107", cl="109"),
        bar(14, op="108", hi="109", lo="107", cl="108"),
        bar(15, op="110", hi="111", lo="108", cl="110"),
    ]


def h2_signal():
    return [bar(n, cl="90") for n in range(1, 13)] + [
        bar(13, cl="100"), bar(14, cl="101"),
    ]


def h3_signal():
    return [bar(n) for n in range(1, 7)] + [
        bar(7, op="89", hi="90", lo="88", cl="89"),
        bar(8, op="90", hi="90", lo="89", cl="90"),
        bar(9, op="91", hi="92", lo="90", cl="91"),
    ]


def h4_signal():
    return [bar(n) for n in range(1, 7)] + [
        bar(n, op="100", hi="103", lo="93", cl="100") for n in range(7, 18)
    ] + [bar(18, op="100", hi="140", lo="80", cl="104")]


def h5_signal(count=62):
    return [bar(n, cl="105") for n in range(1, 7)] + [
        bar(n, op="111", hi="112", lo="110", cl="111") for n in range(7, count + 1)
    ]


@pytest.mark.parametrize("h,fixture,signal_n", [
    (1, h1_signal, 15), (2, h2_signal, 14), (3, h3_signal, 9),
    (4, h4_signal, 18), (5, h5_signal, 62),
])
def test_each_signal_has_exact_causal_clock(h, fixture, signal_n):
    actual = run(h, fixture())
    assert actual.state == "signal"
    assert actual.signal_bar_start == OPEN + timedelta(minutes=5 * (signal_n - 1))
    assert actual.signal_bar_end == OPEN + timedelta(minutes=5 * signal_n)
    assert actual.decision_at == OPEN + timedelta(minutes=5 * signal_n + 2)
    assert actual.observed_through == actual.signal_bar_end


@pytest.mark.parametrize("h,fixture", [
    (1, h1_signal), (2, h2_signal), (3, h3_signal), (4, h4_signal), (5, h5_signal),
])
def test_future_bar_or_generator_failure_cannot_erase_a_signal(h, fixture):
    def stream():
        yield from fixture()
        raise AssertionError("detector consumed a future observation")
    assert run(h, stream()).state == "signal"


@pytest.mark.parametrize("h", range(1, 6))
def test_missing_initial_prefix_is_unknown(h):
    actual = run(h, [bar(n) for n in range(1, 6)])
    assert actual.state == "unknown"
    assert actual.reason == "missing_prefix"
    assert actual.decision_at == OPEN + timedelta(minutes=32)
    assert actual.observed_through == OPEN + timedelta(minutes=25)


@pytest.mark.parametrize("h,fixture", [
    (1, h1_signal), (2, h2_signal), (3, h3_signal), (4, h4_signal), (5, h5_signal),
])
def test_missing_trigger_prefix_never_skips_a_scheduled_bar(h, fixture):
    bars = fixture()
    del bars[-2]
    actual = run(h, bars)
    assert actual.state == "unknown"
    assert actual.reason == "noncontiguous_prefix"
    assert actual.signal_bar_end is None


@pytest.mark.parametrize("h", range(1, 6))
def test_complete_constant_series_is_known_no_signal_not_unknown(h):
    actual = run(h, [bar(n) for n in range(1, 63)])
    assert actual.state == "known_no_signal"
    assert actual.signal_bar_end is None


@pytest.mark.parametrize("h", (1, 2))
def test_initial_close_equal_first_open_fails_strict_condition_without_future_read(h):
    assert run(h, [bar(n) for n in range(1, 13)]).reason == "initial_condition_false"


def test_h1_first_pair_can_compare_initial_last_close():
    actual = run(1, h1_signal())
    assert actual.setup_bar_end == OPEN + timedelta(minutes=70)


def test_h1_equal_close_resets_the_consecutive_fall_count():
    bars = morning_up() + [
        bar(13, op="109", hi="110", lo="107", cl="109"),
        bar(14, op="109", hi="110", lo="107", cl="109"),
        bar(15, op="108", hi="109", lo="107", cl="108"),
        bar(16, op="107", hi="108", lo="106", cl="107"),
        bar(17, op="110", hi="111", lo="109", cl="110"),
    ]
    actual = run(1, bars)
    assert actual.state == "signal"
    assert actual.setup_bar_end == OPEN + timedelta(minutes=80)
    assert actual.signal_bar_end == OPEN + timedelta(minutes=85)


def test_h1_midpoint_equality_valid_but_strictly_below_invalidates():
    bars = morning_up() + [
        bar(13, op="106", hi="107", lo="105", cl="106"),
        bar(14, op="105", hi="106", lo="105", cl="105"),
        bar(15, op="107", hi="108", lo="106", cl="107"),
    ]
    assert run(1, bars).state == "signal"
    bars[13] = bar(14, op="105", hi="106", lo="104", cl="104.999")
    actual = run(1, bars)
    assert actual.state == "known_no_signal"
    assert actual.reason == "midpoint_invalidated"
    assert actual.observed_through == OPEN + timedelta(minutes=70)


def test_h1_first_setup_three_opportunities_and_no_restart():
    bars = h1_signal()[:14] + [
        bar(15, op="108", hi="109", lo="107", cl="109"),
        bar(16, op="108", hi="109", lo="107", cl="108"),
        bar(17, op="108", hi="109", lo="107", cl="108"),
        bar(18, op="110", hi="111", lo="109", cl="110"),
    ]
    actual = run(1, bars)
    assert actual.state == "known_no_signal"
    assert actual.reason == "first_setup_timeout"
    assert actual.decision_at == OPEN + timedelta(minutes=87)
    assert actual.setup_bar_end == OPEN + timedelta(minutes=70)


def test_h1_third_opportunity_can_trigger_before_timeout():
    bars = h1_signal()[:14] + [
        bar(15, op="108", hi="109", lo="107", cl="108"),
        bar(16, op="108", hi="109", lo="107", cl="108"),
        bar(17, op="110", hi="111", lo="109", cl="110"),
    ]
    assert run(1, bars).signal_bar_end == OPEN + timedelta(minutes=85)


def test_h1_invalidation_ends_first_setup_before_later_reclaim():
    bars = h1_signal()[:14] + [
        bar(15, op="104", hi="105", lo="103", cl="104"),
        bar(16, op="110", hi="111", lo="109", cl="110"),
    ]
    actual = run(1, bars)
    assert actual.state == "known_no_signal"
    assert actual.reason == "midpoint_invalidated"
    assert actual.setup_bar_end == OPEN + timedelta(minutes=70)
    assert actual.decision_at == OPEN + timedelta(minutes=77)


def test_h1_no_pullback_by_cutoff_is_known_no_signal():
    bars = morning_up() + [
        bar(n, op="110", hi="111", lo="109", cl="110") for n in range(13, 48)
    ]
    actual = run(1, bars)
    assert actual.state == "known_no_signal"
    assert actual.reason == "decision_cutoff"
    assert actual.decision_at == OPEN + timedelta(minutes=240)


def test_h2_initial_midpoint_equality_is_not_a_decline_setup():
    bars = [bar(n, op="105", cl="100") for n in range(1, 13)]
    assert run(2, bars).state == "known_no_signal"


def test_h2_recovery_requires_strictly_above_midpoint():
    bars = h2_signal()
    assert run(2, bars[:-1]).state == "unknown"
    assert run(2, bars).signal_bar_end == OPEN + timedelta(minutes=70)


def test_h3_setup_equality_does_not_start_and_reclaim_equality_does_not_trigger():
    bars = [bar(n) for n in range(1, 7)] + [
        bar(7, op="90", hi="91", lo="89", cl="90"),
        bar(8, op="89", hi="90", lo="88", cl="89"),
        bar(9, op="90", hi="90", lo="89", cl="90"),
        bar(10, op="91", hi="92", lo="90", cl="91"),
    ]
    actual = run(3, bars)
    assert actual.setup_bar_end == OPEN + timedelta(minutes=40)
    assert actual.signal_bar_end == OPEN + timedelta(minutes=50)


def test_h3_exactly_three_scheduled_trigger_bars_no_reset_on_new_low():
    bars = h3_signal()[:7] + [
        bar(8, op="88", hi="89", lo="87", cl="88"),
        bar(9, op="87", hi="88", lo="86", cl="87"),
        bar(10, op="91", hi="92", lo="90", cl="91"),
    ]
    actual = run(3, bars)
    assert actual.state == "signal"
    assert actual.setup_bar_end == OPEN + timedelta(minutes=35)
    bars[9] = bar(10, op="88", hi="89", lo="87", cl="88")
    bars.append(bar(11, op="91", hi="92", lo="90", cl="91"))
    actual = run(3, bars)
    assert actual.reason == "first_setup_timeout"
    assert actual.observed_through == OPEN + timedelta(minutes=50)


def test_h4_equal_half_compression_and_excluded_current_extremes():
    actual = run(4, h4_signal())
    assert actual.state == "signal"
    assert actual.signal_bar_end == OPEN + timedelta(minutes=90)


def test_h4_above_half_range_and_equal_high_do_not_trigger():
    bars = h4_signal()
    bars[16] = replace(bars[16], low=D("92.999"))
    assert run(4, bars).state == "unknown"
    bars = h4_signal()
    bars[-1] = replace(bars[-1], close=D("103"))
    assert run(4, bars).state == "unknown"


def test_h4_no_trigger_before_end_plus_ninety_minutes():
    bars = [bar(n) for n in range(1, 7)] + [
        bar(n, hi="101", lo="99") for n in range(7, 17)
    ] + [bar(17, op="102", hi="103", lo="99", cl="102")]
    assert run(4, bars).state == "unknown"


def test_h4_zero_initial_range_is_proven_no_signal():
    bars = [bar(n, hi="100", lo="100") for n in range(1, 7)]
    actual = run(4, bars)
    assert actual.state == "known_no_signal"
    assert actual.reason == "initial_range_zero"


def test_h4_zero_lookback_range_is_a_valid_compression():
    bars = [bar(n) for n in range(1, 7)] + [
        bar(n, hi="100", lo="100") for n in range(7, 18)
    ] + [bar(18, cl="101")]
    assert run(4, bars).state == "signal"


@pytest.mark.parametrize("h", (2, 3, 4))
def test_ordinary_cutoff_uses_availability_not_bar_end(h):
    bars = [bar(n, cl="90" if h == 2 else "100") for n in range(1, 48)]
    if h == 3:
        bars[-1] = bar(47, op="89", hi="90", lo="88", cl="89")
    # End+240 would be available at +242 and must not influence the result.
    bars.append(bar(48, op="120", hi="121", lo="119", cl="120"))
    actual = run(h, bars)
    assert actual.state == "known_no_signal"
    assert actual.decision_at == OPEN + timedelta(minutes=240)
    assert actual.observed_through == OPEN + timedelta(minutes=235)


def test_last_causally_available_bar_before_cutoff_can_trigger():
    bars = [bar(n, cl="90") for n in range(1, 47)] + [bar(47, cl="101")]
    actual = run(2, bars)
    assert actual.state == "signal"
    assert actual.decision_at == OPEN + timedelta(minutes=237)


def test_h5_latest_available_not_next_scheduled_bar():
    bars = h5_signal()
    bars[-1] = bar(62, op="110", hi="112", lo="109", cl="110")
    bars.append(bar(63, op="120", hi="121", lo="119", cl="120"))
    actual = run(5, bars)
    assert actual.state == "known_no_signal"
    assert actual.decision_at == CLOSE - timedelta(minutes=78)
    assert actual.observed_through == OPEN + timedelta(minutes=310)


def test_h5_false_morning_still_requires_full_decision_prefix():
    bars = h5_signal()
    bars[5] = replace(bars[5], close=D("100"))
    assert run(5, bars[:-1]).state == "unknown"
    assert run(5, bars).state == "known_no_signal"


def test_h5_original_short_session_uses_its_fixed_relative_clock():
    closing = OPEN + timedelta(minutes=330)
    actual = run(5, h5_signal(50), closing=closing)
    assert actual.state == "signal"
    assert actual.decision_at == OPEN + timedelta(minutes=252)
    assert actual.signal_bar_end == OPEN + timedelta(minutes=250)


def test_h5_impossibly_short_session_is_unknown_without_consuming_bars():
    def forbidden():
        raise AssertionError("infeasible session consumed prices")
        yield
    actual = run(5, forbidden(), closing=OPEN + timedelta(minutes=105))
    assert actual.state == "unknown"
    assert actual.reason == "clock_infeasible"


@pytest.mark.parametrize("lag", (1, 3, 1000))
def test_different_source_availability_is_refused_without_assumed_retiming(lag):
    first = bar(1)
    with pytest.raises(signals.InvalidSourceClock, match="^invalid_source_clock$"):
        run(3, [replace(first, available_at=first.end_at + timedelta(minutes=lag))])


def test_invalid_prices_are_integrity_errors_not_missingness():
    with pytest.raises(core.PatternValidationError, match="positive finite Decimal"):
        run(3, [replace(bar(1), close=D("NaN"))])
    with pytest.raises(core.PatternValidationError, match="positive finite Decimal"):
        run(3, [replace(bar(1), open=100.0)])


def test_duplicate_and_backward_bars_are_integrity_errors_not_missingness():
    with pytest.raises(core.PatternValidationError, match="invalid_source_sequence"):
        run(3, [bar(1), bar(1)])
    with pytest.raises(core.PatternValidationError, match="invalid_source_sequence"):
        run(3, [bar(1), bar(2), bar(1)])


def test_midpoint_comparison_is_exact_even_under_low_decimal_precision():
    bars = [bar(n, op="101", hi="100000000000000000001", lo="99", cl="100")
            for n in range(1, 13)]
    midpoint = D("50000000000000000050")
    bars.append(bar(13, op=str(midpoint), hi=str(midpoint), lo="99", cl=str(midpoint)))
    bars.append(bar(14, op="50000000000000000051", hi="50000000000000000051",
                    lo="99", cl="50000000000000000051"))
    with localcontext() as context:
        context.prec = 3
        actual = run(2, bars)
    assert actual.state == "signal"
    assert actual.signal_bar_end == OPEN + timedelta(minutes=70)


def test_unregistered_hypothesis_and_bad_session_clock_are_refused():
    with pytest.raises(core.PatternValidationError, match="unregistered_hypothesis"):
        signals.detect_bars("H6", [], opening=OPEN, closing=CLOSE)
    with pytest.raises(core.PatternValidationError, match="invalid_session_clock"):
        run(1, [], closing=OPEN)
