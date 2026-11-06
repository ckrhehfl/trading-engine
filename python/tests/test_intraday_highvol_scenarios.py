"""Invented price paths for the causal EG scenario kernel."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_highvol_events import classify_window, measure_window
from research.intraday_highvol_scenarios import detect_scenario


OPEN = datetime(2026, 7, 6, 9, tzinfo=timezone(timedelta(hours=9)))
SYMBOL = "KRX:000001"
UP = ((100, 102, 99, 101), (101, 103, 100, 102), (102, 106, 101, 105))
DOWN = ((105, 106, 103, 104), (104, 105, 102, 103), (103, 104, 98, 100))
PULLBACK = (105, 106, 104, "104.5")
RESUME = ("104.5", 108, 104, 107)
RECLAIM = (100, 103, 98, "102.5")
WAIT = ("104.5", 106, 104, 105)


def market(bars, *, close=60):
    minutes = {}
    for index, values in enumerate(bars):
        ohlc = tuple(Decimal(price) for price in values)
        for minute in range(index * 5, index * 5 + 5):
            at = OPEN + minute * MINUTE
            minutes[at] = Minute(SYMBOL, at, at + 3 * MINUTE, ohlc, Decimal(1))
    return SessionInputs(SYMBOL, OPEN, OPEN + close * MINUTE, minutes)


def registered_high(session):
    metric = measure_window(session, 15, OPEN + 17 * MINUTE)
    prior = {}
    for days in range(20, 0, -1):
        shift = timedelta(days=days)
        prior[OPEN - shift] = replace(
            metric, opening=OPEN - shift, decision_at=metric.decision_at - shift,
            price_available_at=metric.price_available_at - shift,
            volume_available_at=metric.volume_available_at - shift, energy=Fraction(0),
            signed_move=Fraction(0), direction_efficiency=None, zero_path=True,
            relative_range=Fraction(0),
        )
    return classify_window(metric, tuple(prior), prior)


class BoundedMinutes(Mapping):
    """Raise on a suffix read or an attempt to scan the minute mapping."""

    def __init__(self, minutes, before):
        self.minutes = minutes
        self.before = before
        self.reads = []

    def __getitem__(self, at):
        assert at < OPEN + self.before * MINUTE
        self.reads.append(at)
        return self.minutes[at]

    def __iter__(self):
        raise AssertionError("minute map iteration is not causal")

    def __len__(self):
        return len(self.minutes)


def detect(session):
    return detect_scenario(session, registered_high(session), left_censored=True)


def test_up_uses_first_pullback_and_stops_reading_at_confirmation():
    session = market(UP + (PULLBACK, RESUME, RECLAIM))
    high = registered_high(session)
    minutes = BoundedMinutes(session.minutes, before=25)
    result = detect_scenario(replace(session, minutes=minutes), high, left_censored=True)

    assert (result.scenario, result.state, result.reason) == ("A", "confirmed", "resumption")
    assert result.left_censored and result.high_offset == 15
    assert result.key_level == Fraction(103)
    assert result.pullback == result.observed_bars[3]
    assert result.pullback.end_at == OPEN + 20 * MINUTE
    assert result.decision_at == result.confirmation.decision_at == OPEN + 27 * MINUTE
    assert result.confirmation.intended_entry_at == OPEN + 28 * MINUTE
    assert (result.confirmation.price, result.confirmation.stop, result.confirmation.target) == (
        Fraction(107), Fraction(104), Fraction(113),
    )
    assert minutes.reads == [OPEN + i * MINUTE for i in range(25)]


def test_down_reclaim_accepts_a_low_equal_to_stop_and_keeps_exact_fraction():
    result = detect(market(DOWN + (RECLAIM,)))

    assert (result.scenario, result.state, result.reason) == ("B", "confirmed", "reclaim")
    assert result.pullback is None and result.key_level == Fraction(102)
    assert result.confirmation.decision_at == OPEN + 22 * MINUTE
    assert result.confirmation.intended_entry_at == OPEN + 23 * MINUTE
    assert (result.confirmation.price, result.confirmation.stop, result.confirmation.target) == (
        Fraction(205, 2), Fraction(98), Fraction(223, 2),
    )


@pytest.mark.parametrize(
    ("bars", "scenario", "reason"),
    [
        (UP + ((105, 106, 103, 104), RESUME), "A", "pullback_failed_to_hold_key"),
        (UP + (PULLBACK, ("104.5", 108, 103, 107)), "A", "new_low_below_structural_stop"),
        (DOWN + ((100, 103, 97, 103),), "B", "new_low_below_structural_stop"),
    ],
)
def test_structure_failure_precedes_same_bar_confirmation(bars, scenario, reason):
    result = detect(market(bars))
    assert (result.scenario, result.state, result.reason) == (scenario, "invalidated", reason)
    assert result.confirmation is None


@pytest.mark.parametrize("late", [False, True])
def test_gap_or_late_bar_ends_attempt_without_reading_later_prices(late):
    session = market(UP + (PULLBACK, RESUME, RECLAIM))
    high = registered_high(session)
    at = OPEN + 20 * MINUTE
    if late:
        session.minutes[at] = replace(session.minutes[at], available_at=OPEN + 28 * MINUTE)
    else:
        del session.minutes[at]
    minutes = BoundedMinutes(session.minutes, before=21)
    result = detect_scenario(replace(session, minutes=minutes), high, left_censored=True)

    assert (result.scenario, result.state) == ("A", "unsupported")
    assert result.issue.minute == at
    assert result.issue.reason == ("price_unavailable_by_bar_deadline" if late else "missing_minute")
    assert result.decision_at == OPEN + 27 * MINUTE
    assert len(result.observed_bars) == 4
    assert result.confirmation is None


def test_inclusive_deadline_and_no_search_after_expiry():
    on_time = detect(market(UP + (PULLBACK,) + (WAIT,) * 4 + (RESUME,)))
    session = market(UP + (PULLBACK,) + (WAIT,) * 5 + (RESUME,))
    late = detect_scenario(replace(session, minutes=BoundedMinutes(session.minutes, before=45)),
                           registered_high(session), left_censored=True)

    assert on_time.state == "confirmed"
    assert late.state == "expired"
    assert on_time.decision_at == late.decision_at == OPEN + 47 * MINUTE
    assert late.confirmation is None


def test_close_truncates_future_confirmation_but_retains_observed_signal():
    truncated = detect(market(UP + (PULLBACK,), close=20))
    confirmed = detect(market(DOWN + (RECLAIM,), close=20))

    assert truncated.state == "outside_clock"
    assert truncated.decision_at == OPEN + 22 * MINUTE
    assert confirmed.state == "confirmed"
    assert confirmed.confirmation.intended_entry_at == OPEN + 23 * MINUTE


def test_non_directional_high_consumes_only_initial_context():
    session = market(UP[:2] + ((102, 106, 101, 103), RESUME))
    result = detect_scenario(replace(session, minutes=BoundedMinutes(session.minutes, before=15)),
                             registered_high(session), left_censored=True)
    assert (result.scenario, result.state) == (None, "no_setup")
    assert len(result.observed_bars) == 3


def test_refuses_mismatched_high_context_and_impossible_first_grid_onset():
    session = market(UP + (PULLBACK, RESUME))
    high = registered_high(session)
    with pytest.raises(InputRefusal, match="first_grid_high_must_be_left_censored"):
        detect_scenario(session, high, left_censored=False)
    changed = replace(high, current=replace(high.current, signed_move=Fraction(1, 20) - Fraction(1, 100)))
    with pytest.raises(InputRefusal, match="scenario_high_signed_move_changed"):
        detect_scenario(session, changed, left_censored=True)
