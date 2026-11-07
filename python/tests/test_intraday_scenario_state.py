"""Invented price, availability, gap and fresh-evidence contracts for EH."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_scenario_state import (
    PriceEvidence, previous_session_high, reentry_evidence, scan_previous_high,
)


OPENING = datetime(2030, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
PRIOR = OPENING - timedelta(days=1)
EVIDENCE = PriceEvidence("invented-previous", "invented-units", "invented-basis")
CURRENT = replace(EVIDENCE, receipt_id="invented-current")
BELOW = (94, 99, 89, 94)
APPROACH = (94, 101, 93, 100)
BREAKOUT = (100, 110, 70, 103)


def test_late_event_expires_at_last_in_session_open_before_the_close():
    current = session(BELOW, BELOW, BELOW, APPROACH, closing=25)
    result = scan(current, 22)
    assert result.status == "event"
    assert result.event.expires_at == current.closing - MINUTE


def session(*specifications, opening=OPENING, closing=120):
    rows = {}
    for index, spec in enumerate(specifications):
        if spec is not None:
            for offset in range(index * 5, index * 5 + 5):
                at = opening + offset * MINUTE
                rows[at] = Minute("invented", at, at + 3 * MINUTE, tuple(map(Decimal, spec)), None,
                                  volume_state="missing")
    return SessionInputs("invented", opening, opening + closing * MINUTE, rows)


def previous_high(current, previous=None, **overrides):
    if previous is None:
        previous = session((91, 100, 90, 95), opening=PRIOR, closing=5)
    options = {
        "expected_previous_opening": PRIOR, "previous_evidence": EVIDENCE,
        "current_evidence": CURRENT, "decision_at": OPENING,
    } | overrides
    return previous_session_high(current, previous, **options)


def scan(current, offset, level=None):
    return scan_previous_high(current, previous_high(current) if level is None else level,
                              through=OPENING + offset * MINUTE)


class NoReadAfter(dict):
    def __init__(self, rows, offset):
        super().__init__(rows)
        self.limit = OPENING + offset * MINUTE

    def get(self, at, *args):
        assert at < self.limit, f"unexpected read at {at}"
        return super().get(at, *args)


def test_previous_high_requires_full_expected_regular_session_and_same_basis():
    current = session()
    previous = session((91, 100, 90, 95), (91, 102, 90, 95), opening=PRIOR, closing=10)
    result = previous_high(current, previous)
    assert result.level.price == 102
    assert result.level.available_at == previous.closing + 2 * MINUTE
    assert result.level.previous_evidence == EVIDENCE
    assert result.level.current_evidence == CURRENT
    assert result.level.level_id.endswith(":previous_session_high")
    assert not result.issues
    registered_earlier = previous_high(current, previous, decision_at=previous.closing + 2 * MINUTE)
    assert registered_earlier == result
    unfinished = previous_high(current, previous, decision_at=previous.closing - MINUTE)
    assert unfinished.issues[0].reason == "previous_session_not_completed"
    incomplete = replace(previous, minutes={PRIOR: previous.minutes[PRIOR]})
    assert previous_high(current, incomplete).level is None
    assert len(previous_high(current, incomplete).issues) == 9
    mismatch = replace(CURRENT, price_basis="unknown-adjustment")
    unknown = previous_high(current, previous, current_evidence=mismatch)
    assert unknown.level is None
    assert scan(current, 22, unknown).status == "support_unknown"
    with pytest.raises(InputRefusal, match="previous_session_identity"):
        previous_high(current, previous, expected_previous_opening=PRIOR - timedelta(days=1))


def test_unauthenticated_previous_day_and_delayed_level_are_support_unknown():
    current = session()
    calendar_unknown = previous_session_high(
        current, None, expected_previous_opening=None, previous_evidence=None,
        current_evidence=CURRENT, decision_at=OPENING,
    )
    assert scan(current, 21, calendar_unknown).status == "not_yet_eligible"
    assert scan(current, 22, calendar_unknown).issues[0].reason == "previous_calendar_unknown"
    previous = session((91, 100, 90, 95), opening=PRIOR, closing=5)
    delayed = replace(previous, minutes={
        at: replace(row, available_at=OPENING + 23 * MINUTE) for at, row in previous.minutes.items()
    })
    unavailable = previous_high(current, delayed)
    assert unavailable.issues[0].reason == "minute_available_after_cutoff"
    after = previous_high(current, delayed, decision_at=OPENING + 30 * MINUTE)
    assert scan(current, 30, after).issues[0].reason == "level_unavailable_at_first_grid"


def test_prepared_event_freezes_preceding_band_and_then_observes_breakout():
    current = session(BELOW, BELOW, BELOW, APPROACH, BREAKOUT)
    guarded = replace(current, minutes=NoReadAfter(current.minutes, 20))
    first = scan(guarded, 22)
    assert first.status == "event"
    assert first.event.kind == "prepared"
    assert first.event.breakout is None
    assert first.event.band == Fraction(1)
    assert first.event.prepared_bounds == (98, 102)
    assert [row.available_at for row in first.event.width_context] == [
        OPENING + offset * MINUTE for offset in (7, 12, 17)
    ]
    after = scan(current, 27)
    assert after.event.breakout.close == 103
    assert after.event.breakout.available_at == OPENING + 27 * MINUTE
    assert after.event.breakout_stop == 70
    assert replace(after.event, breakout=None) == first.event


def test_strict_upper_cross_is_distinct_from_a_close_on_zone_edge():
    strict = session(BELOW, BELOW, BELOW, (94, 102, 93, 102))
    state = scan(strict, 22)
    assert state.event.kind == "breakout"
    assert state.event.breakout_stop == 93
    assert state.event.prepared_bounds is None
    equal = session(BELOW, BELOW, BELOW, (94, 101, 93, 101))
    assert scan(equal, 22).event.kind == "prepared"


@pytest.mark.parametrize(("first", "third", "reason"), [
    ((106, 107, 104, 106), (105, 106, 104, 105), "opening_gap"),
    (BELOW, (94, 105, 93, 105), "prior_close_above_at_first_eligible_grid"),
])
def test_opening_gap_and_early_above_are_left_censored(first, third, reason):
    current = session(first, BELOW, third, (105, 107, 104, 106))
    result = scan(current, 22)
    assert result.event.kind == "left_censored_gap"
    assert result.event.censor_reason == reason
    assert result.event.breakout is None
    assert result.event.prepared_bounds is None


def test_complete_below_prefix_differs_from_missing_and_zero_range():
    current = session(BELOW, BELOW, BELOW, BELOW, None, BREAKOUT)
    assert scan(current, 22).status == "known_no_event"
    unknown = scan(current, 32)
    assert unknown.status == "support_unknown"
    assert unknown.event is None
    assert all(issue.reason == "missing_minute" for issue in unknown.issues)
    zero = session(*([(94, 94, 94, 94)] * 3), APPROACH)
    assert scan(zero, 22).issues[0].reason == "zero_preceding_range"


def test_late_or_missing_crossing_cannot_erase_the_prepared_event():
    current = session(BELOW, BELOW, BELOW, APPROACH, BREAKOUT, BREAKOUT)
    rows = dict(current.minutes)
    at = OPENING + 24 * MINUTE
    rows[at] = replace(rows[at], available_at=OPENING + 28 * MINUTE)
    unknown = scan(replace(current, minutes=rows), 32)
    assert unknown.status == "support_unknown"
    assert unknown.event == scan(current, 22).event
    assert unknown.issues[0].reason == "minute_available_after_cutoff"


def reentry(current, offset, route, exit_offset=23):
    event = scan(current, 22).event
    return reentry_evidence(current, event, exit_confirmed_at=OPENING + exit_offset * MINUTE,
                            through=OPENING + offset * MINUTE, route=route)


def test_reentry_uses_full_fresh_recovery_pullback_and_later_confirmation():
    old = (104, 111, 103, 110)
    recovery = (100, 106, 100, 104)
    pullback = (104, 105, 100, 103)
    confirmation = (103, 107, 102, 106)
    current = session(BELOW, BELOW, BELOW, APPROACH, old, recovery, pullback, confirmation)
    assert reentry(current, 27, "below_level").context == ()
    pending = reentry(current, 37, "below_level")
    assert pending.status == "pending"
    assert [row.close for row in pending.context] == [104, 103]
    result = reentry(current, 42, "below_level")
    assert result.status == "ready"
    assert result.confirmation.close == 106
    assert result.confirmation.start_at == OPENING + 35 * MINUTE


def test_fractional_exit_clock_is_outside_the_modeled_minute_contract():
    current = session(BELOW, BELOW, BELOW, APPROACH, BELOW, (102, 106, 101, 104),
                      (104, 105, 103, 104), (104, 107, 103, 107))
    event = scan(current, 22).event
    with pytest.raises(InputRefusal, match="aware_minute_clock_required"):
        reentry_evidence(current, event, exit_confirmed_at=OPENING + 25 * MINUTE + timedelta(seconds=1),
                         through=OPENING + 42 * MINUTE, route="above_level")


def test_first_pullback_low_touch_invalidates_the_below_level_route():
    current = session(BELOW, BELOW, BELOW, APPROACH, BELOW, (100, 106, 100, 104),
                      (104, 105, 99, 103), (103, 107, 102, 106))
    result = reentry(current, 42, "below_level")
    assert result.status == "invalidated"
    assert result.confirmation.low == 99


def test_above_level_reentry_freezes_only_first_two_fresh_highs():
    current = session(BELOW, BELOW, BELOW, APPROACH, BELOW, (102, 106, 101, 104),
                      (104, 105, 103, 104), (104, 115, 103, 106), (106, 109, 105, 107))
    assert reentry(current, 42, "above_level").status == "pending"
    ready = reentry(current, 47, "above_level")
    assert ready.status == "ready"
    assert [row.high for row in ready.context] == [106, 105]
    assert ready.confirmation.close == 107


def test_reentry_does_not_cross_a_gap_or_original_deadline():
    current = session(BELOW, BELOW, BELOW, APPROACH, BELOW, (102, 106, 101, 104),
                      None, (104, 107, 103, 107))
    assert reentry(current, 42, "above_level").status == "support_unknown"
    assert reentry(current, 42, None).issues[0].reason == "exit_path_unknown"
    event = scan(current, 22).event
    assert event.expires_at == OPENING + 82 * MINUTE
    guarded = replace(current, minutes=NoReadAfter(current.minutes, 20))
    assert reentry(guarded, 77, "above_level").status == "expired"
