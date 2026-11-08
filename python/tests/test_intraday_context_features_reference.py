"""Independent EI feature arithmetic and causal-mask tests on invented inputs."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_context_features as producer
from research.intraday_context_features_reference import reference_features, reference_numeric_view, verify_features
from research.intraday_context_questions_reference import reference_question_frames
from research.intraday_context_support_reference import reference_frames
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
BARS = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
        (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114))
BELOW, TOUCH, BREAK = BARS[-2], BARS[-1], (114, 119, 113, 118)


def at(minutes):
    return OPEN + minutes * MINUTE


def session(bars=BARS, closing=390):
    rows = {at(index): Minute("invented", at(index), at(index + 3), tuple(map(Decimal, bar)), Decimal(10))
            for number, bar in enumerate(bars) for index in range(5 * number, 5 * number + 5)}
    return SessionInputs("invented", OPEN, at(closing), rows)


def pairs(value=None, end=30):
    value = value or session()
    contexts = tuple(reference_frames(value, at(end + 2)))
    questions = reference_question_frames(contexts, closing=value.closing)
    return tuple((frame, question) for frame, row in zip(contexts, questions, strict=True) for question in row.questions)


def compare(frame, question):
    produced = producer.project_context_features(frame, question)
    expected = reference_features(frame, question)
    assert produced == expected
    if expected.status == "ready":
        chart = produced.numeric_view(with_context=False)
        contextual = produced.numeric_view(with_context=True)
        verify_features(frame, question, produced, chart=chart, chart_context=contextual)
        assert reference_numeric_view(expected, with_context=False) == chart
        assert reference_numeric_view(expected, with_context=True) == contextual
    else:
        verify_features(frame, question, produced)
        with pytest.raises(InputRefusal, match="ready_feature_shape"):
            reference_numeric_view(expected, with_context=False)
    return expected


@pytest.mark.parametrize("bars", [BARS, (*BARS, BELOW, TOUCH, BREAK),
                                 (*BARS, (114, 117, 113, 117), BREAK),
                                 (*BARS[:-1], BREAK), (*BARS, (114, 140, 113, 135), BELOW, TOUCH)])
def test_independent_features_and_numeric_views_match_each_event(bars):
    for pair in pairs(session(bars), end=len(bars) * 5):
        compare(*pair)


def test_exact_first_projection_has_known_coordinates_without_producer_conversion():
    frame, question = pairs()[0]
    expected = reference_features(frame, question)
    assert expected.prices_in_R[0] == (Fraction(-4), Fraction(-7, 2), Fraction(-9, 2), Fraction(-4))
    assert expected.common == tuple(map(Fraction, (0, 32, Fraction(1, 4), Fraction(5, 2), Fraction(1, 2), 30, Fraction(-1, 2))))
    assert expected.context[:6] == (1, 0, 0, 1, 0, 0)
    assert expected.context[6:] == (4, Fraction(5, 16), 2, 1)
    numeric = reference_numeric_view(expected, with_context=True)
    assert numeric.minute[0] == (-4.0, -3.5, -4.5, -4.0, 1.0, 1.0, 1.0)
    assert len(numeric.static) == 17 and len(numeric.minute) == 30
    assert all(type(value) is float for row in numeric.minute for value in row)


@pytest.mark.parametrize("mode", ["mixed", "zeros", "missing", "unit_unknown", "fractional"])
def test_independent_volume_mean_masks_distinguish_zero_and_missing(mode):
    original = session()
    rows = dict(original.minutes)
    if mode == "mixed":
        rows[at(0)] = replace(rows[at(0)], shares=Decimal(0))
        rows[at(1)] = replace(rows[at(1)], shares=None, volume_state="missing")
    elif mode in {"missing", "unit_unknown"}:
        rows = {key: replace(row, shares=None, volume_state=mode) for key, row in rows.items()}
    elif mode == "zeros":
        rows = {key: replace(row, shares=Decimal(0)) for key, row in rows.items()}
    else:
        rows[at(0)] = replace(rows[at(0)], shares=Decimal("0.125"))
    result = compare(*pairs(replace(original, minutes=rows))[0])
    numeric = reference_numeric_view(result, with_context=False)
    if mode == "mixed":
        assert result.volume_scale == Fraction(280, 29)
        assert numeric.minute[0][4:] == (0.0, 1.0, 1.0)
        assert numeric.minute[1][4:] == (0.0, 0.0, 1.0)
    elif mode == "zeros":
        assert result.volume_scale == 0 and all(row[4:] == (0.0, 1.0, 0.0) for row in numeric.minute)
    elif mode in {"missing", "unit_unknown"}:
        assert result.volume_scale is None and all(row[4:] == (0.0, 0.0, 0.0) for row in numeric.minute)
    else:
        assert result.volume_scale == Fraction(2321, 240)


def test_only_recent_thirty_minutes_affect_scale_and_episode_origin_is_preserved():
    original = session((*BARS, (114, 117, 113, 117), BREAK))
    rows = {key: replace(row, shares=Decimal("999999999999999999999999999999999999999")) if key < at(10) else row
            for key, row in original.minutes.items()}
    result = compare(*pairs(replace(original, minutes=rows), end=40)[-1])
    assert result.volume_scale == 10 and result.window_start == at(10)
    assert result.context[:3] == (1, 0, 0) and result.context[3:6] == (0, 0, 1)


def test_future_suffix_changes_do_not_change_present_projection():
    before = reference_features(*pairs()[0])
    after = session((*BARS, (114, 180, 1, 175), BREAK))
    assert reference_features(*pairs(after, end=40)[0]) == before
    rows = dict(after.minutes)
    rows[at(30)] = replace(rows[at(30)], shares=Decimal("999999999999999999999"))
    assert reference_features(*pairs(replace(after, minutes=rows), end=40)[0]) == before


@pytest.mark.parametrize("where,state", [(0, "missing"), (29, "missing"), (29, "late")])
def test_unknown_price_remains_unsupported_without_numeric_fill(where, state):
    frame, question = pairs()[0]
    prefix = list(frame.prefix)
    row = prefix[where]
    prefix[where] = replace(row, prices=None, price_state=state,
        available_at=None if state == "late" else row.available_at,
        volume=None if state == "late" else row.volume, volume_state="late" if state == "late" else row.volume_state)
    result = compare(replace(frame, prefix=tuple(prefix), prefix_observed_on_grid=False), question)
    assert result.prices_in_R is None and "context_price_prefix_unknown" in result.reasons


def test_late_original_observation_cannot_be_repaired_by_current_visibility():
    frame, question = pairs(session((*BARS, (114, 117, 113, 117), BREAK)), end=40)[-1]
    delayed = replace(frame.prefix[0], available_at=at(34))
    frame = replace(frame, prefix=(delayed, *frame.prefix[1:]))
    with pytest.raises(InputRefusal, match="original_deadline_metadata"):
        reference_features(frame, question)
    result = compare(replace(frame, prefix_observed_on_grid=False), question)
    assert result.reasons == ("context_prefix_not_observed_on_original_grid",)


def test_outside_session_parent_does_not_remove_present_features():
    frame, question = pairs(session(closing=45))[0]
    assert question.unsupported_reasons == ("outside_session",)
    result = compare(frame, question)
    assert result.status == "ready" and result.common[5] == 30


def test_preopening_padding_is_not_reported_as_missing_prices():
    frame, question = pairs()[0]
    anchor = replace(frame.anchor, peak_end=at(15), confirmed_at=at(22))
    frame = replace(frame, observation_end=at(25), available_at=at(27), prefix=frame.prefix[:25], anchor=anchor)
    question = replace(question, episode_id=((frame.symbol, anchor), 0), episode_origin_at=at(27),
                       path=replace(question.path, start_at=at(27), end_at=at(57)))
    result = compare(frame, question)
    assert result.reasons == ("not_yet_30_in_session_minutes",)
    assert result.window_start == at(-5) and len(result.price_states) == 25


def test_old_missing_prefix_keeps_complete_recent_window_separate():
    frame, question = pairs(session((*BARS, (114, 117, 113, 117), BREAK)), end=40)[-1]
    unknown = replace(frame.prefix[0], price_state="missing", prices=None)
    result = compare(replace(frame, prefix=(unknown, *frame.prefix[1:]), prefix_observed_on_grid=False), question)
    assert result.reasons == ("context_price_prefix_unknown", "context_prefix_not_observed_on_original_grid")
    assert result.price_states == ("observed",) * 30 and result.prices_in_R is None


@pytest.mark.parametrize("field,value", [("volume_scale", Fraction(99)), ("forecast_at", at(33)),
    ("status", "unsupported"), ("reasons", ("invented",)), ("price_states", ("missing",) * 30)])
def test_saved_projection_field_mutation_is_detected(field, value):
    frame, question = pairs()[0]
    feature = producer.project_context_features(frame, question)
    with pytest.raises(InputRefusal, match="exact_projection_differs"):
        verify_features(frame, question, replace(feature, **{field: value}))


@pytest.mark.parametrize("mutate", [
    lambda feature: replace(feature, common=(0, *feature.common[1:])),
    lambda feature: replace(feature, common=(False, *feature.common[1:])),
    lambda feature: replace(feature, common=(0.0, *feature.common[1:])),
    lambda feature: replace(feature, stored_volumes=(10.0, *feature.stored_volumes[1:])),
    lambda feature: replace(feature, question=replace(feature.question, economic_priority=1)),
    lambda feature: replace(feature, context=(False, *feature.context[1:])),
])
def test_saved_exact_projection_requires_types_not_only_equal_values(mutate):
    frame, question = pairs()[0]
    feature = producer.project_context_features(frame, question)
    with pytest.raises(InputRefusal, match="exact_projection_differs"):
        verify_features(frame, question, mutate(feature))


@pytest.mark.parametrize("mutation", ["int", "mask", "mode", "clock", "shape", "nan"])
def test_saved_numeric_views_are_independently_checked(mutation):
    frame, question = pairs()[0]
    feature = producer.project_context_features(frame, question)
    numeric = feature.numeric_view(with_context=False)
    if mutation in {"int", "mask", "nan"}:
        row = list(numeric.minute[0])
        row[0 if mutation != "mask" else 5] = {"int": -4, "mask": 0.0, "nan": float("nan")}[mutation]
        numeric = replace(numeric, minute=(tuple(row), *numeric.minute[1:]))
    elif mutation == "mode":
        numeric = replace(numeric, mode="chart_context")
    elif mutation == "clock":
        numeric = replace(numeric, forecast_at=at(33))
    else:
        numeric = replace(numeric, minute=numeric.minute[:-1])
    with pytest.raises(InputRefusal, match="numeric_projection_differs"):
        verify_features(frame, question, feature, chart=numeric)


def test_reference_never_calls_producer_projection_conversion_or_numeric_method(monkeypatch):
    frame, question = pairs()[0]
    baseline = reference_features(frame, question)

    def forbidden(*args, **kwargs):
        raise AssertionError("producer calculation called")

    monkeypatch.setattr(producer, "project_context_features", forbidden)
    monkeypatch.setattr(producer, "_finite", forbidden)
    monkeypatch.setattr(producer.ContextFeatures, "numeric_view", forbidden)
    assert reference_features(frame, question) == baseline
    assert reference_numeric_view(baseline, with_context=False).minute[0][0] == -4.0


def test_context_metric_mutation_cannot_be_accepted_as_a_reconstructed_fact():
    frame, question = pairs()[0]
    changed = replace(frame, metrics=replace(frame.metrics, pullback_depth=Fraction(99)))
    with pytest.raises(InputRefusal, match="independent_context_state"):
        reference_features(changed, question)


@pytest.mark.parametrize("kind", ["future", "early", "negative_volume", "float_volume", "unknown_payload", "anchor", "origin"])
def test_bad_input_identity_or_visibility_is_rejected(kind):
    frame, question = pairs()[0]
    if kind == "anchor":
        frame = replace(frame, anchor=replace(frame.anchor, pre_peak_range=Fraction(5)))
    elif kind == "origin":
        question = replace(question, episode_origin_at=at(33))
    else:
        row = frame.prefix[-1]
        updates = {"future": {"available_at": at(33)}, "early": {"available_at": at(30)},
                   "negative_volume": {"volume": Fraction(-1)}, "float_volume": {"volume": 10.0},
                   "unknown_payload": {"volume_state": "missing"}}
        frame = replace(frame, prefix=(*frame.prefix[:-1], replace(row, **updates[kind])))
    with pytest.raises(InputRefusal):
        reference_features(frame, question)


def test_numeric_reference_rejects_wrong_scale_and_nonfinite_values():
    feature = reference_features(*pairs()[0])
    with pytest.raises(InputRefusal, match="volume_scale"):
        reference_numeric_view(replace(feature, volume_scale=Fraction(20)), with_context=False)
    with pytest.raises(InputRefusal, match="nonfinite_numeric"):
        reference_numeric_view(replace(feature, common=(Fraction(10**400), *feature.common[1:])), with_context=False)
    with pytest.raises(InputRefusal, match="ready_feature_shape"):
        reference_numeric_view(feature, with_context=1)
