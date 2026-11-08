"""Independent EI question reconstruction on invented chart prefixes."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import random

import pytest

from research import intraday_context_questions as producer
from research.intraday_context_questions_reference import reference_question_frames, verify_question_frames
from research.intraday_context_situations import scan_context_frames
from research.intraday_context_support_reference import reference_frames
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs
from research.intraday_scenario_labels import label_horizons


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103))
PEAK, BELOW, TOUCH, BREAK = (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114), (114, 119, 113, 118)


def at(minutes):
    return OPEN + minutes * MINUTE


def session(bars, *, closing=390):
    rows = {at(index): Minute("invented", at(index), at(index + 3), tuple(map(Decimal, bar)), Decimal(10))
            for bar_index, bar in enumerate(bars) if bar is not None
            for index in range(5 * bar_index, 5 * bar_index + 5)}
    return SessionInputs("invented", OPEN, at(closing), rows)


def compare(value, end):
    actual = tuple(producer.scan_context_questions(scan_context_frames(value, through=at(end + 2)), closing=value.closing))
    original = tuple(reference_frames(value, at(end + 2)))
    expected = tuple(reference_question_frames(original, closing=value.closing))
    assert actual == expected
    verify_question_frames(original, actual, closing=value.closing)
    return expected


@pytest.mark.parametrize("bars", [
    (*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, TOUCH, BELOW, TOUCH),
    (*BASE, PEAK, BELOW, BREAK),
    (*BASE, PEAK, (114, 116, 113, 115), BREAK, (118, 119, 115, 116), BREAK, BELOW, BREAK),
    (*BASE, PEAK, BELOW, TOUCH, (114, 117, 113, 117), BREAK),
    (*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, (118, 120, 118, 119)),
    (*BASE, PEAK, (114, 116, 113, 115), (115, 116, 113, 114), (114, 117, 113, 117), BREAK),
    (*BASE, (103, 105, 102, 104), (104, 120, 103, 118), (118, 118, 114, 115), (115, 120, 114, 118)),
    (*BASE, (103, 105, 102, 104), (104, 106, 103, 105), (105, 120, 104, 118), (118, 119, 115, 116), TOUCH),
    (*BASE, PEAK, (114, 115, 111, 112), (114, 117, 113, 117), BREAK),
    (*BASE, PEAK, BELOW, (112, 115, 111, 114)),
    (*BASE, PEAK, BELOW, TOUCH, (114, 140, 113, 135), (135, 139, 130, 134), BELOW, TOUCH),
    (*BASE, PEAK, BELOW, TOUCH, (114, 116, 99, 100), BELOW, BREAK),
    (*BASE, PEAK, BELOW, (112, 119, 99, 100)),
    (BASE[0],) * 8,
    ((100, 100, 100, 100),) * 3 + ((100, 110, 100, 108), (108, 109, 106, 107), (107, 108, 105, 106)),
    (*BASE, PEAK, None, TOUCH, BELOW, TOUCH),
    (*BASE, PEAK, BELOW, TOUCH, None, TOUCH),
])
def test_independent_context_and_question_traces_agree(bars):
    compare(session(bars), len(bars) * 5)


def test_repeated_episode_origins_overlap_and_same_clock_priority():
    rows = compare(session((*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, BREAK)), 45)
    questions = [item for row in rows for item in row.questions]
    assert [(q.stage, q.episode_id[1]) for q in questions] == [("approach", 1), ("approach", 2), ("breakout", 2)]
    assert questions[1].episode_origin_context == questions[2].episode_origin_context == "B"
    assert questions[1].episode_origin_at == questions[2].episode_origin_at == at(42)
    assert questions[0].question_id in questions[2].overlapping_question_ids
    together = compare(session((*BASE, PEAK, BELOW, BREAK)), 30)[0].questions
    assert [q.economic_priority for q in together] == [False, True]
    assert together[0].overlapping_question_ids == (together[1].question_id,)


def test_recovered_late_minute_does_not_repair_original_unknown_context():
    value = session((*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH))
    rows = dict(value.minutes)
    rows[at(29)] = replace(rows[at(29)], available_at=at(40))
    observed = compare(replace(value, minutes=rows), 40)
    assert [row.status for row in observed] == ["unknown"] * 3
    assert not any(row.questions for row in observed)


def test_thirty_minute_outside_session_question_keeps_shorter_horizon_labels():
    value = session((*BASE, PEAK, BELOW, TOUCH, *([TOUCH] * 5)), closing=52)
    question = compare(value, 30)[0].questions[0]
    assert question.unsupported_reasons == ("outside_session",)
    assert question.path.end_at == at(62)
    results = label_horizons(question.path, question.start_close, value, cutoff_at=at(55))
    assert [result.status for result in results] == ["resolved", "resolved", "unsupported"]
    assert results[-1].reason == "outside_session"


def test_future_suffix_never_changes_prefix_or_is_eagerly_requested():
    prefix = (*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH)
    short = compare(session(prefix), 40)
    longer = compare(session((*prefix, BREAK, (118, 130, 99, 100), BREAK)), 55)
    assert longer[:len(short)] == short
    first = next(reference_frames(session(prefix), at(42)))

    def one_then_forbidden():
        yield first
        raise AssertionError("future frame requested eagerly")

    assert next(reference_question_frames(one_then_forbidden(), closing=at(390))) == short[0]


@pytest.mark.parametrize("mutation", ["target", "priority", "priority_type", "price_type", "origin", "overlap", "episode", "clock", "duplicate", "missing"])
def test_independent_verifier_rejects_changed_producer_trace(mutation):
    value = session((*BASE, PEAK, BELOW, TOUCH, BELOW, BREAK))
    context = tuple(reference_frames(value, at(42)))
    rows = list(producer.scan_context_questions(context, closing=value.closing))
    last = rows[-1]
    question = last.questions[-1]
    if mutation == "target":
        question = replace(question, path=replace(question.path, target=question.path.target + 1))
    elif mutation == "priority":
        question = replace(question, economic_priority=not question.economic_priority)
    elif mutation == "priority_type":
        question = replace(question, economic_priority=int(question.economic_priority))
    elif mutation == "price_type":
        question = replace(question, start_close=float(question.start_close))
    elif mutation == "origin":
        question = replace(question, episode_origin_context="other")
    elif mutation == "overlap":
        question = replace(question, overlapping_question_ids=())
    elif mutation == "episode":
        question = replace(question, episode_id=(question.anchor_id, 999))
    elif mutation == "clock":
        rows[-1] = replace(last, available_at=last.available_at + MINUTE)
    elif mutation == "duplicate":
        rows.append(last)
    elif mutation == "missing":
        rows.pop()
    if mutation in {"target", "priority", "priority_type", "price_type", "origin", "overlap", "episode"}:
        rows[-1] = replace(last, questions=(*last.questions[:-1], question))
    with pytest.raises(InputRefusal, match="reference_.*differs"):
        verify_question_frames(context, rows, closing=value.closing)


def test_producer_constant_mutation_does_not_change_reference(monkeypatch):
    value = session((*BASE, PEAK, BELOW, TOUCH))
    context = tuple(reference_frames(value, at(32)))
    expected = tuple(reference_question_frames(context, closing=value.closing))
    monkeypatch.setattr(producer, "TARGET_ABOVE_LEVEL_IN_R", Fraction(3))
    changed = tuple(producer.scan_context_questions(context, closing=value.closing))
    assert tuple(reference_question_frames(context, closing=value.closing)) == expected
    with pytest.raises(InputRefusal, match="frame_or_question_differs"):
        verify_question_frames(context, changed, closing=value.closing)


@pytest.mark.parametrize("mutation", ["skip", "duplicate", "symbol", "past", "clock", "support", "invalidation"])
def test_malformed_context_frames_are_rejected(mutation):
    value = session((*BASE, PEAK, BELOW, TOUCH, (114, 116, 99, 100), BELOW, TOUCH))
    frames = list(reference_frames(value, at(47)))
    if mutation == "skip":
        del frames[1]
    elif mutation == "duplicate":
        frames.insert(1, frames[0])
    elif mutation == "symbol":
        frames[1] = replace(frames[1], symbol="changed")
    elif mutation == "past":
        prefix = list(frames[1].prefix)
        prefix[0] = replace(prefix[0], prices=tuple(map(Fraction, (100, 103, 98, 100))))
        frames[1] = replace(frames[1], prefix=tuple(prefix))
    elif mutation == "clock":
        frames[1] = replace(frames[1], available_at=at(38))
    elif mutation == "support":
        frames[0] = replace(frames[0], prefix_observed_on_grid=False)
    elif mutation == "invalidation":
        frames[2] = replace(frames[2], metrics=replace(frames[2].metrics, invalidated_at=None))
    with pytest.raises(InputRefusal):
        tuple(reference_question_frames(frames, closing=value.closing))


def test_fixed_random_invented_suffixes_preserve_independent_event_parity():
    generator = random.Random(20310212)
    choices = [BELOW, TOUCH, BREAK, (118, 120, 118, 119), (115, 117, 112, 116), (114, 116, 99, 100)]
    for _ in range(25):
        bars = (*BASE, PEAK, BELOW, TOUCH, *(generator.choice(choices) for _ in range(20)))
        compare(session(bars), len(bars) * 5)
