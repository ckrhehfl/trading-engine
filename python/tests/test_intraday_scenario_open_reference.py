"""Invented fixtures for the independent EH opening census reconstruction."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE, Issue, Minute, SessionInputs
from research.intraday_scenario_observations import opening_support_observation
from research.intraday_scenario_open_reference import reference_opening_support
from research.intraday_scenario_state import OpeningRangePriceEvidence


OPENING = datetime(2031, 4, 7, 9, tzinfo=timezone(timedelta(hours=9)))
EVIDENCE = OpeningRangePriceEvidence("invented-receipt", "invented-unit")


def _at(offset):
    return OPENING + offset * MINUTE


def _session(*spans, length=80):
    prices = [(100, 110, 90, 100)] * length
    for begin, end, value in spans:
        prices[begin:end] = [value] * (end - begin)
    return SessionInputs("invented", OPENING, _at(length), {
        _at(offset): Minute("invented", _at(offset), _at(offset + 3), tuple(map(Decimal, price)), None,
                            volume_state="missing")
        for offset, price in enumerate(prices)
    })


def _compare(session, evidence=EVIDENCE):
    reference = reference_opening_support(session, evidence)
    assert reference == opening_support_observation(session, evidence)
    return reference


def test_distinct_causal_questions_and_mature_first_touch_paths():
    session = _session((15, 20, (108, 120, 107, 110)), (20, 25, (110, 118, 109, 113)),
                       (25, 80, (113, 115, 113, 113)), (32, 33, (113, 114, 111, 112)),
                       (35, 36, (113, 121, 112, 120)))
    result = _compare(session)
    assert result.scan.status == "event"
    event = result.scan.event
    assert event.level.price == 110
    assert event.level.available_at == _at(17)
    assert event.band == Fraction(2)
    assert event.origin.available_at == _at(22)
    assert event.breakout.available_at == _at(27)

    approach, breakout = result.paths
    assert approach.initial.question.question_id == (_at(22), 110, 2, 114, 106, _at(52))
    assert approach.initial.target_basis == "prepared_band"
    assert [(item.path, item.mature_at) for item in approach.results] == [("P1", _at(25))] * 3
    assert breakout.initial.question.question_id == (_at(27), 110, 2, 120, 108, _at(57))
    assert breakout.initial.target_basis == "known_intraday_swing_high"
    assert [(item.path, item.mature_at) for item in breakout.results] == [
        ("P4", _at(34)), ("P2", _at(38)), ("P2", _at(38)),
    ]


@pytest.mark.parametrize(("price", "status", "path", "reason"), [
    ((114, 128, 107, 114), "unknown", None, "same_minute_target_stop_order_unknown"),
    ((114, 128, 109, 114), "unknown", None, "same_minute_retest_target_order_unknown"),
    ((107, 128, 107, 114), "resolved", "P3", None),
    ((111, 128, 109, 114), "resolved", "P2", None),
    ((127, 128, 107, 114), "resolved", "P1", None),
])
def test_open_print_can_resolve_first_touch_that_ranges_alone_cannot(price, status, path, reason):
    session = _session((15, 20, (111, 118, 109, 114)), (22, 23, price))
    result = _compare(session)
    first, = result.paths
    assert first.initial.target_basis == "twice_stop_distance"
    assert first.initial.question.question_id == (_at(22), 110, 2, 126, 108, _at(52))
    assert [(item.status, item.path, item.reason, item.observed_through, item.mature_at)
            for item in first.results] == [(status, path, reason, _at(23), _at(25))] * 3


def test_late_or_missing_anchor_is_never_recovered_by_the_later_prefix():
    session = _session()
    rows = dict(session.minutes)
    rows[_at(4)] = replace(rows[_at(4)], available_at=_at(18))
    del rows[_at(14)]
    result = _compare(replace(session, minutes=rows))
    assert result.scan.status == "support_unknown" and result.scan.event is None
    assert result.scan.issues == (Issue("invented", _at(4), "minute_available_after_cutoff"),
                                  Issue("invented", _at(14), "missing_minute"))
    assert not result.paths


@pytest.mark.parametrize("late", [False, True])
def test_known_approach_survives_lost_decision_prefix_and_preserves_label_cutoff(late):
    session = _session((15, 20, (108, 112, 107, 110)), (22, 23, (110, 114, 109, 113)))
    rows = dict(session.minutes)
    if late:
        rows[_at(22)] = replace(rows[_at(22)], available_at=_at(40))
    else:
        del rows[_at(22)]
    result = _compare(replace(session, minutes=rows))
    assert result.scan.status == "support_unknown"
    assert result.scan.event.kind == "prepared" and result.scan.event.breakout is None
    assert result.scan.issues[0].reason == ("minute_available_after_cutoff" if late else "missing_minute")
    first, = result.paths
    if late:
        assert [(item.path, item.mature_at) for item in first.results] == [("P1", _at(40))] * 3
    else:
        assert [(item.status, item.reason, item.observed_through, item.mature_at) for item in first.results] == [
            ("unknown", "minute_not_observed_at_cutoff", _at(22), None),
        ] * 3


def test_session_end_excludes_long_horizons_even_after_early_resolved_touch():
    session = _session((15, 20, (108, 112, 107, 110)), (22, 23, (110, 114, 109, 113)), length=31)
    first, = _compare(session).paths
    assert first.results[0].path == "P1"
    assert [(item.status, item.reason, item.observed_through) for item in first.results[1:]] == [
        ("unsupported", "outside_session", _at(22)),
    ] * 2


def test_short_session_and_missing_provenance_have_separate_support_states():
    short = replace(_session(length=22), minutes={})
    assert _compare(short, None).scan.status == "not_yet_eligible"
    result = _compare(_session(), None)
    assert result.scan.status == "support_unknown"
    assert result.scan.issues == (Issue("invented", _at(17), "same_session_price_evidence_missing"),)


def test_expiry_prevents_later_breakout_from_rewriting_initial_approach():
    session = _session((15, 75, (108, 112, 107, 110)), (75, 80, (111, 118, 109, 114)), length=100)
    result = _compare(session)
    assert result.scan.event.origin.available_at == _at(22)
    assert result.scan.event.expires_at == _at(82)
    assert result.scan.event.breakout is None
    assert len(result.paths) == 1
