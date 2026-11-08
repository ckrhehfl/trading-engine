"""Invented causal EI feature views; no market input readers or fitting."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import math

import pytest

from research.intraday_context_features import (
    COMMON_NAMES, CONTEXT_NAMES, HISTORY_MINUTES, MINUTE_NAMES, SCHEMA, project_context_features,
)
from research.intraday_context_questions import scan_context_questions
from research.intraday_context_situations import scan_context_frames
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
BARS = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
        (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114))


def at(offset):
    return OPEN + offset * MINUTE


def session(bars=BARS):
    rows = {at(index): Minute("invented", at(index), at(index + 3), tuple(map(Decimal, bar)), Decimal(10))
            for bar_index, bar in enumerate(bars) for index in range(5 * bar_index, 5 * bar_index + 5)}
    return SessionInputs("invented", OPEN, at(390), rows)


def pair(value=None, end=30):
    value = value or session()
    frames = tuple(scan_context_frames(value, through=at(end + 2)))
    question_frames = tuple(scan_context_questions(frames, closing=value.closing))
    choices = [(frame, question) for frame, found in zip(frames, question_frames, strict=True)
               for question in found.questions]
    return choices[-1]


def test_exact_schema_projection_and_identical_numeric_common_views():
    frame, question = pair()
    features = project_context_features(frame, question)
    assert SCHEMA == "ei-chart-context-features-draft-v1"
    assert features.status == "ready" and not features.reasons
    assert features.forecast_at == at(32) and features.observation_end == at(30)
    assert features.window_start == OPEN
    assert features.prices_in_R[0] == (Fraction(-4), Fraction(-7, 2), Fraction(-9, 2), Fraction(-4))
    assert features.common == (0, 32, Fraction(1, 4), Fraction(5, 2), Fraction(1, 2), 30, Fraction(-1, 2))
    assert features.context[:6] == (1, 0, 0, 1, 0, 0)
    assert features.context[6] == 4 and features.context[7] == Fraction(5, 16)
    assert features.volume_scale == 10 and all(value == 10 for value in features.stored_volumes)
    chart, contextual = features.numeric_view(with_context=False), features.numeric_view(with_context=True)
    assert chart.minute == contextual.minute and contextual.static[:len(COMMON_NAMES)] == chart.static
    assert len(chart.minute) == HISTORY_MINUTES == 30
    assert {len(row) for row in chart.minute} == {len(MINUTE_NAMES)} == {7}
    assert len(chart.static) == 7 and len(contextual.static) == 17
    assert len(chart.flat) == len(chart.feature_names) == 217
    assert len(contextual.flat) == len(contextual.feature_names) == 227
    assert len(set(contextual.feature_names)) == 227
    assert contextual.static_names == COMMON_NAMES + CONTEXT_NAMES
    assert all(math.isfinite(value) for value in contextual.flat)
    assert chart.question_id == contextual.question_id == question.question_id
    with pytest.raises(FrozenInstanceError):
        features.status = "unsupported"
    with pytest.raises(FrozenInstanceError):
        chart.forecast_at = at(33)


def test_actual_zero_missing_and_zero_scale_have_distinct_masks():
    original = session()
    rows = dict(original.minutes)
    rows[at(0)] = replace(rows[at(0)], shares=Decimal(0))
    rows[at(1)] = replace(rows[at(1)], shares=None, volume_state="missing")
    frame, question = pair(replace(original, minutes=rows))
    features = project_context_features(frame, question)
    assert features.status == "ready"
    assert features.volume_states[:2] == ("observed", "missing")
    assert features.stored_volumes[:2] == (0, None)
    assert features.volume_scale == Fraction(280, 29)
    numeric = features.numeric_view(with_context=False)
    assert numeric.minute[0][4:] == (0.0, 1.0, 1.0)
    assert numeric.minute[1][4:] == (0.0, 0.0, 1.0)
    assert numeric.minute[2][4] == pytest.approx(29 / 28)

    zero_rows = {key: replace(value, shares=Decimal(0)) for key, value in original.minutes.items()}
    zero = project_context_features(*pair(replace(original, minutes=zero_rows)))
    assert zero.volume_scale == 0
    assert all(row[4:] == (0.0, 1.0, 0.0) for row in zero.numeric_view(with_context=False).minute)
    missing_rows = {key: replace(value, shares=None, volume_state="missing") for key, value in original.minutes.items()}
    missing = project_context_features(*pair(replace(original, minutes=missing_rows)))
    assert missing.volume_scale is None
    assert all(row[4:] == (0.0, 0.0, 0.0) for row in missing.numeric_view(with_context=False).minute)


def test_only_the_last_thirty_available_minutes_set_volume_scale():
    value = session((*BARS, (114, 117, 113, 117), (114, 119, 113, 118)))
    rows = dict(value.minutes)
    for index in range(10):
        rows[at(index)] = replace(rows[at(index)], shares=Decimal(100000))
    features = project_context_features(*pair(replace(value, minutes=rows), end=40))
    assert features.window_start == at(10) and features.volume_scale == 10
    assert features.context[:3] == (1, 0, 0) and features.context[3:6] == (0, 0, 1)
    assert features.question.episode_origin_at == at(32)


def test_unobserved_future_and_post_cutoff_prices_do_not_change_projection():
    original = session()
    expected = project_context_features(*pair(original))
    future = dict(original.minutes)
    future[at(30)] = Minute("invented", at(30), at(33), (Decimal(999),) * 4, Decimal(999))
    future[at(90)] = Minute("invented", at(90), at(93), None, None, "missing", "missing")
    assert project_context_features(*pair(replace(original, minutes=future))) == expected


@pytest.mark.parametrize("state", ["missing", "late", "file_error", "api_error"])
def test_price_support_is_explicit_and_cannot_be_encoded_as_zero(state):
    frame, question = pair()
    if state not in {"missing", "late"}:
        # Existing observation vocabulary must stay closed, not invent new states.
        changed = replace(frame.prefix[-1], prices=None, price_state=state)
        with pytest.raises(ValueError, match="observation_state"):
            project_context_features(replace(frame, prefix=(*frame.prefix[:-1], changed)), question)
        return
    changed = replace(frame.prefix[-1], prices=None, price_state=state,
                      available_at=None if state == "late" else frame.prefix[-1].available_at)
    if state == "late":
        changed = replace(changed, volume=None, volume_state="late")
    missing = project_context_features(replace(frame, prefix=(*frame.prefix[:-1], changed),
                                               prefix_observed_on_grid=False), question)
    assert missing.status == "unsupported" and missing.prices_in_R is None
    assert "recent_price_window_unknown" in missing.reasons
    with pytest.raises(ValueError, match="ready_features"):
        missing.numeric_view(with_context=False)


def test_old_prefix_unknown_is_not_repaired_by_a_complete_recent_window():
    frame, question = pair(session((*BARS, (114, 117, 113, 117), (114, 119, 113, 118))), end=40)
    missing = replace(frame.prefix[0], prices=None, price_state="missing")
    features = project_context_features(replace(frame, prefix=(missing, *frame.prefix[1:]),
                                                prefix_observed_on_grid=False), question)
    assert features.status == "unsupported"
    assert "context_price_prefix_unknown" in features.reasons
    assert "recent_price_window_unknown" not in features.reasons


def test_late_old_price_cannot_masquerade_as_original_on_time_context():
    frame, question = pair(session((*BARS, (114, 117, 113, 117), (114, 119, 113, 118))), end=40)
    delayed = replace(frame.prefix[0], available_at=at(34))
    forged = replace(frame, prefix=(delayed, *frame.prefix[1:]))
    with pytest.raises(ValueError, match="original_grid_metadata"):
        project_context_features(forged, question)
    unsupported = project_context_features(replace(forged, prefix_observed_on_grid=False), question)
    assert unsupported.reasons == ("context_prefix_not_observed_on_original_grid",)


def test_future_context_milestone_is_rejected():
    frame, question = pair()
    with pytest.raises(ValueError, match="context_clock"):
        project_context_features(replace(frame, metrics=replace(frame.metrics, resumption_at=at(37))), question)


@pytest.mark.parametrize("kind", ["future_availability", "early_availability", "wrong_timestamp", "unknown_payload",
                                  "late_payload", "invalid_ohlc", "negative_volume", "float_price", "extra_prefix"])
def test_malformed_or_unavailable_prefix_is_rejected(kind):
    frame, question = pair()
    row = frame.prefix[-1]
    changes = {
        "future_availability": {"available_at": at(33)},
        "early_availability": {"available_at": at(30)},
        "wrong_timestamp": {"start_at": at(28)},
        "unknown_payload": {"price_state": "missing"},
        "late_payload": {"price_state": "late", "prices": None},
        "invalid_ohlc": {"prices": tuple(map(Fraction, (112, 113, 114, 112)))},
        "negative_volume": {"volume": Fraction(-1)},
        "float_price": {"prices": (1.0,) * 4},
    }
    prefix = (*frame.prefix, row) if kind == "extra_prefix" else (*frame.prefix[:-1], replace(row, **changes[kind]))
    with pytest.raises(ValueError):
        project_context_features(replace(frame, prefix=prefix), question)


@pytest.mark.parametrize("kind", ["symbol", "anchor", "clock", "close", "origin", "current", "phase", "metrics"])
def test_question_identity_and_observed_state_must_match(kind):
    frame, question = pair()
    if kind == "symbol":
        frame = replace(frame, symbol="another")
    elif kind == "anchor":
        frame = replace(frame, anchor=replace(frame.anchor, price=Fraction(117)))
    elif kind == "clock":
        question = replace(question, path=replace(question.path, start_at=at(33)))
    elif kind == "close":
        question = replace(question, start_close=Fraction(115))
    elif kind == "origin":
        question = replace(question, episode_origin_at=at(33))
    elif kind == "current":
        question = replace(question, question_time_context="B")
    elif kind == "phase":
        question = replace(question, phase="unknown")
    else:
        frame = replace(frame, metrics=replace(frame.metrics, advance_in_ranges=Fraction(99)))
    with pytest.raises(ValueError):
        project_context_features(frame, question)


def test_feature_support_does_not_drop_shorter_horizons_for_parent_session_limit():
    frame, question = pair()
    parent_unsupported = replace(question, unsupported_reasons=("outside_session",))
    features = project_context_features(frame, parent_unsupported)
    assert features.status == "ready" and features.question.unsupported_reasons == ("outside_session",)


def test_float_boundary_requires_finite_numeric_view_and_exact_values():
    features = project_context_features(*pair())
    with pytest.raises(ValueError, match="finite_numeric"):
        replace(features, common=(Fraction(10**400), *features.common[1:])).numeric_view(with_context=False)
    with pytest.raises(ValueError, match="exact_feature"):
        replace(features, common=(float("nan"), *features.common[1:])).numeric_view(with_context=False)
    with pytest.raises(ValueError, match="typed|ready_features"):
        features.numeric_view(with_context=1)


def test_negative_window_start_means_not_yet_history_not_a_missing_session_bar():
    frame, question = pair()
    anchor = replace(frame.anchor, peak_end=at(15), confirmed_at=at(22))
    frame = replace(frame, observation_end=at(25), available_at=at(27), prefix=frame.prefix[:25], anchor=anchor)
    question = replace(question, episode_id=((frame.symbol, anchor), 0), episode_origin_at=at(27),
                       path=replace(question.path, start_at=at(27), end_at=at(57)))
    features = project_context_features(frame, question)
    assert features.status == "unsupported" and features.reasons == ("not_yet_30_in_session_minutes",)
    assert features.window_start == at(-5) and len(features.price_states) == 25
