"""Invented EI event identities and question geometry; no market inputs."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_context_questions import (
    AVAILABILITY_LAG_MINUTES, BAND_IN_R, BAR_MINUTES, ECONOMIC_STAGE_PRIORITY,
    FIRST_ENDPOINT_MINUTES, QUESTION_HORIZON_MINUTES, SCHEME_ID, STOP_BELOW_LEVEL_IN_R,
    TARGET_ABOVE_LEVEL_IN_R, scan_context_questions,
)
from research.intraday_context_situations import scan_context_frames
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103))
PEAK = (103, 116, 102, 114)
BELOW = (114, 114, 111, 112)
TOUCH = (112, 116, 111, 114)
BREAK = (114, 119, 113, 118)


def at(offset):
    return OPEN + offset * MINUTE


def session(*bars, closing=390):
    rows = {}
    for bar_index, bar in enumerate(bars):
        if bar is not None:
            for offset in range(bar_index * 5, (bar_index + 1) * 5):
                rows[at(offset)] = Minute("invented", at(offset), at(offset + 3),
                                          tuple(map(Decimal, bar)), Decimal(10))
    return SessionInputs("invented", OPEN, at(closing), rows)


def frames(value, end):
    return tuple(scan_context_frames(value, through=at(end + 2)))


def scan(value, end):
    return tuple(scan_context_questions(frames(value, end), closing=value.closing))


def questions(rows):
    return tuple(question for row in rows for question in row.questions)


def test_scheme_constants_are_explicit_pre_outcome_draft_values():
    assert SCHEME_ID == "EI-common-approach-breakout-draft-v1"
    assert (FIRST_ENDPOINT_MINUTES, BAR_MINUTES, AVAILABILITY_LAG_MINUTES) == (30, 5, 2)
    assert (BAND_IN_R, STOP_BELOW_LEVEL_IN_R, TARGET_ABOVE_LEVEL_IN_R) == (Fraction(1, 4), 1, 2)
    assert QUESTION_HORIZON_MINUTES == 30
    assert ECONOMIC_STAGE_PRIORITY == ("breakout", "approach")


def test_repeated_approach_retains_B_instead_of_only_first_event_and_keeps_overlap():
    value = session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, TOUCH, BELOW, TOUCH)
    rows = scan(value, 55)
    found = questions(rows)
    assert [row.available_at for row in rows] == list(map(at, (32, 37, 42, 47, 52, 57)))
    assert [row.status for row in rows] == ["questions", "no_event", "questions", "no_event", "no_event", "questions"]
    assert [(item.stage, item.question_time_context, item.episode_id[1]) for item in found] == [
        ("approach", "A", 1), ("approach", "B", 2), ("approach", "B", 3),
    ]
    assert found[0].question_id in found[1].overlapping_question_ids
    assert found[0].question_id in found[2].overlapping_question_ids
    assert len({item.anchor_id for item in found}) == 1
    assert [item.episode_origin_at for item in found] == list(map(at, (32, 42, 57)))
    assert found[0].episode_origin_context == found[0].question_time_context == "A"
    assert found[0].path.end_at == at(62)
    assert found[0].path.start_at == at(32)
    assert (found[0].path.level, found[0].path.band, found[0].path.stop, found[0].path.target) == (116, 1, 112, 124)
    assert found[0].start_close == 114 and not found[0].unsupported_reasons
    with pytest.raises(FrozenInstanceError):
        found[0].question_time_context = "B"
    with pytest.raises(FrozenInstanceError):
        found[0].episode_origin_context = "B"


def test_simultaneous_approach_and_breakout_have_two_typed_questions_one_priority():
    first, second = questions(scan(session(*BASE, PEAK, BELOW, BREAK), 30))
    assert (first.stage, second.stage) == ("approach", "breakout")
    assert first.episode_id == second.episode_id
    assert first.question_id != second.question_id
    assert first.path == second.path and first.path.question_id == second.path.question_id
    assert (first.economic_priority, second.economic_priority) == (False, True)
    assert first.overlapping_question_ids == (second.question_id,)
    assert second.overlapping_question_ids == (first.question_id,)
    assert first.start_close == second.start_close == 118
    assert first.episode_origin_at == second.episode_origin_at == at(32)
    assert first.episode_origin_context == second.episode_origin_context == first.question_time_context


def test_changed_absolute_question_is_a_new_identity_within_same_episode_and_stage():
    question = questions(scan(session(*BASE, PEAK, BELOW, TOUCH), 30))[0]
    later_expiry = replace(question, path=replace(question.path, end_at=question.path.end_at + MINUTE))
    different_target = replace(question, path=replace(question.path, target=question.path.target + 1))
    assert later_expiry.episode_id == different_target.episode_id == question.episode_id
    assert len({question.question_id, later_expiry.question_id, different_target.question_id}) == 3


def test_initial_episode_breaks_without_armed_approach_then_new_episode_can_break_again():
    touching_right = (114, 116, 113, 115)
    retreat = (118, 119, 115, 116)
    value = session(*BASE, PEAK, touching_right, BREAK, retreat, BREAK, BELOW, BREAK)
    rows = scan(value, 50)
    found = questions(rows)
    assert [(item.stage, item.episode_id[1]) for item in found] == [
        ("breakout", 0), ("approach", 1), ("breakout", 1),
    ]
    assert rows[2].status == "no_event"  # A second crossing within episode zero is suppressed.
    assert found[0].question_id in found[1].overlapping_question_ids


def test_same_episode_retains_A_origin_when_later_breakout_context_is_other():
    resumption_at_upper = (114, 117, 113, 117)
    value = session(*BASE, PEAK, BELOW, TOUCH, resumption_at_upper, BREAK)
    context = frames(value, 40)
    assert (context[1].classification, context[1].phase) == ("A", "first_resumption")
    approach, breakout = questions(scan(value, 40))
    assert approach.episode_id == breakout.episode_id and approach.episode_id[1] == 1
    assert approach.question_time_context == "A" and breakout.question_time_context == "other"
    assert approach.episode_origin_context == breakout.episode_origin_context == "A"
    assert approach.episode_origin_at == breakout.episode_origin_at == at(32)
    assert breakout.path.start_at == at(42)
    assert questions(scan(value, 30)) == (approach,)


def test_same_episode_retains_B_origin_when_gap_breakout_no_longer_overlaps_band():
    gap_break = (118, 120, 118, 119)
    value = session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, gap_break)
    first, approach, breakout = questions(scan(value, 45))
    assert first.episode_id[1] == 1 and approach.episode_id == breakout.episode_id
    assert approach.episode_id[1] == 2 and approach.question_time_context == "B"
    assert breakout.question_time_context == "other" and breakout.stage == "breakout"
    assert approach.episode_origin_context == breakout.episode_origin_context == "B"
    assert approach.episode_origin_at == breakout.episode_origin_at == at(42)
    assert breakout.path.start_at == at(47)
    assert first.episode_origin_context == first.question_time_context == "A"
    assert questions(scan(value, 40)) == (first, approach)


def test_episode_zero_origin_is_first_known_anchor_clock_even_without_initial_question():
    touching_right, held = (114, 116, 113, 115), (115, 116, 113, 114)
    value = session(*BASE, PEAK, touching_right, held, (114, 117, 113, 117), BREAK)
    rows = scan(value, 40)
    assert [row.status for row in rows] == ["no_event", "no_event", "questions"]
    question, = questions(rows)
    assert question.episode_id[1] == 0 and question.stage == "breakout"
    assert question.episode_origin_at == at(32) and question.path.start_at == at(42)
    assert question.episode_origin_context == "A" and question.question_time_context == "other"


def test_confirming_current_bar_can_arm_only_a_later_approach():
    value = session(*BASE, (103, 105, 102, 104), (104, 120, 103, 118),
                    (118, 118, 114, 115), (115, 120, 114, 118))
    rows = scan(value, 35)
    assert rows[0].status == "no_event" and rows[0].episode_id[1] == 0
    assert [(item.stage, item.episode_id[1]) for item in rows[1].questions] == [("approach", 1)]


def test_later_anchor_starts_episode_zero_after_known_no_anchor_frames():
    value = session(*BASE, (103, 105, 102, 104), (104, 106, 103, 105), (105, 120, 104, 118),
                    (118, 119, 115, 116), (116, 120, 115, 118))
    rows = scan(value, 40)
    assert [row.status for row in rows] == ["no_anchor", "no_event", "questions"]
    assert rows[0].episode_id is None and rows[1].episode_id[1] == 0
    assert [(item.stage, item.episode_id[1]) for item in rows[2].questions] == [("approach", 1)]
    assert rows[2].questions[0].anchor_id[1].confirmed_at == at(37)


def test_band_contact_boundaries_are_inclusive_but_arm_and_break_are_strict():
    # High exactly lower=115 is touching, not wholly below. Close exactly upper=117 is not a breakout.
    boundary_touch = (114, 117, 113, 117)
    value = session(*BASE, PEAK, (114, 115, 111, 112), boundary_touch, BREAK)
    rows = scan(value, 35)
    assert rows[0].status == "no_event"
    assert [(item.stage, item.episode_id[1]) for item in rows[1].questions] == [("breakout", 0)]
    lower_contact = session(*BASE, PEAK, BELOW, (112, 115, 111, 114))
    assert questions(scan(lower_contact, 30))[0].stage == "approach"


def test_new_higher_peak_does_not_replace_original_anchor_or_rewrite_questions():
    value = session(*BASE, PEAK, BELOW, TOUCH, (114, 140, 113, 135),
                    (135, 139, 130, 134), BELOW, TOUCH)
    found = questions(scan(value, 50))
    assert [item.path.level for item in found] == [116, 116, 116]
    assert [(item.stage, item.episode_id[1]) for item in found] == [("approach", 1), ("breakout", 1), ("approach", 2)]
    assert found[-1].question_time_context == "B" and found[1].unsupported_reasons == ("unsupported_price_geometry",)
    assert len({item.anchor_id for item in found}) == 1


def test_invalidation_is_permanent_and_does_not_discard_prior_question():
    value = session(*BASE, PEAK, BELOW, TOUCH, (114, 116, 99, 100), BELOW, BREAK)
    rows = scan(value, 45)
    assert [row.status for row in rows] == ["questions", "invalidated", "invalidated", "invalidated"]
    assert len(questions(rows)) == 1
    assert rows[-1].episode_id == rows[0].episode_id
    initially_invalid = scan(session(*BASE, PEAK, BELOW, (112, 119, 99, 100)), 30)
    assert initially_invalid[0].status == "invalidated" and not questions(initially_invalid)


def test_no_anchor_and_zero_reference_range_are_explicit_no_anchor_frames():
    quiet = scan(session(*([BASE[0]] * 8)), 40)
    assert len(quiet) == 3 and all(row.status == "no_anchor" and row.episode_id is None for row in quiet)
    flat = (100, 100, 100, 100)
    zero = scan(session(flat, flat, flat, (100, 110, 100, 108),
                        (108, 109, 106, 107), (107, 108, 105, 106)), 30)
    assert zero[0].status == "no_anchor" and zero[0].reason == "zero_reference_range"


def test_unknown_history_never_repairs_first_attempt_after_late_recovery():
    value = session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH)
    source = dict(value.minutes)
    source[at(29)] = replace(source[at(29)], available_at=at(40))
    value = replace(value, minutes=source)
    context = frames(value, 40)
    assert not context[0].prefix_support.price_complete and context[-1].prefix_support.price_complete
    rows = tuple(scan_context_questions(context, closing=value.closing))
    assert all(row.status == "unknown" and not row.questions for row in rows)
    missing = scan(session(*BASE, PEAK, None, TOUCH, BELOW, TOUCH), 40)
    assert all(row.status == "unknown" for row in missing)


def test_later_missing_bar_keeps_earlier_question_and_then_reports_unknown():
    rows = scan(session(*BASE, PEAK, BELOW, TOUCH, None, TOUCH), 40)
    assert [row.status for row in rows] == ["questions", "unknown", "unknown"]
    assert len(questions(rows)) == 1


def test_late_prefix_cannot_be_replaced_with_a_payload_claiming_old_availability():
    value = session(*BASE, PEAK, BELOW, TOUCH, BELOW)
    source = dict(value.minutes)
    source[at(29)] = replace(source[at(29)], available_at=at(40))
    unknown = frames(replace(value, minutes=source), 30)[0]
    repaired_past = frames(value, 35)[1]
    with pytest.raises(InputRefusal, match="observed_past_prefix_changed"):
        tuple(scan_context_questions((unknown, repaired_past), closing=value.closing))


def test_unsupported_horizon_remains_thirty_minutes_and_geometry_can_be_unsupported_too():
    value = session(*BASE, PEAK, BELOW, TOUCH, closing=45)
    question = questions(scan(value, 30))[0]
    assert question.unsupported_reasons == ("outside_session",)
    assert question.path.end_at == at(62) > value.closing
    wide = session(*BASE, PEAK, BELOW, (114, 130, 113, 125), closing=45)
    assert all(item.unsupported_reasons == ("unsupported_price_geometry", "outside_session")
               for item in questions(scan(wide, 30)))
    exact = session(*BASE, PEAK, BELOW, TOUCH, closing=62)
    assert not questions(scan(exact, 30))[0].unsupported_reasons


@pytest.mark.parametrize("close", [112, 124])
def test_start_close_must_be_strictly_between_stop_and_target(close):
    bar = (114, max(116, close), min(111, close), close)
    found = questions(scan(session(*BASE, PEAK, BELOW, bar), 30))
    assert found and all(item.unsupported_reasons == ("unsupported_price_geometry",) for item in found)


@pytest.mark.parametrize("multiple", [Fraction(29), Fraction(30)])
def test_nonpositive_stop_guard_retains_question_under_mutated_science(monkeypatch, multiple):
    # The present eligible anchor makes R < level/2. Exercise the defensive
    # stop guard by mutating only this local draft constant, not any risk limit.
    monkeypatch.setattr("research.intraday_context_questions.STOP_BELOW_LEVEL_IN_R", multiple)
    found = questions(scan(session(*BASE, PEAK, BELOW, TOUCH), 30))
    assert len(found) == 1 and found[0].path.stop <= 0
    assert found[0].unsupported_reasons == ("unsupported_price_geometry",)


def test_current_five_minute_ohlc_and_start_close_use_all_five_exact_observations():
    value = session(*BASE, PEAK, BELOW, BELOW)
    source = dict(value.minutes)
    source[at(27)] = replace(source[at(27)], ohlc=tuple(map(Decimal, (112, 116, 111, 113))))
    source[at(29)] = replace(source[at(29)], ohlc=tuple(map(Decimal, (112, 114, 111, "113.25"))))
    found = questions(scan(replace(value, minutes=source), 30))
    assert len(found) == 1 and found[0].stage == "approach"
    assert found[0].start_close == Fraction(453, 4)


def test_future_append_and_changed_future_leave_every_prefix_result_unchanged():
    before = session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH)
    extended = session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, BREAK, BELOW, BREAK)
    assert scan(before, 40) == scan(extended, 55)[:3]
    changed = session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH, (114, 140, 99, 100), BREAK, BREAK)
    assert scan(before, 40) == scan(changed, 55)[:3]
    source = iter(frames(extended, 55))
    first = next(source)

    def only_first():
        yield first
        raise AssertionError("future frame requested before consumer advanced")

    assert next(scan_context_questions(only_first(), closing=extended.closing)) == scan(before, 30)[0]


@pytest.mark.parametrize("change", [
    lambda row: replace(row, available_at=row.available_at + MINUTE),
    lambda row: replace(row, observation_end=row.observation_end + MINUTE),
    lambda row: replace(row, prefix=row.prefix[:-1]),
    lambda row: replace(row, prefix_observed_on_grid=False),
    lambda row: replace(row, prefix_support=replace(row.prefix_support, price_counts=(("missing", 30),))),
    lambda row: replace(row, classification="unknown"),
])
def test_malformed_grid_and_support_metadata_are_integrity_errors(change):
    value = session(*BASE, PEAK, BELOW, TOUCH)
    with pytest.raises(InputRefusal):
        tuple(scan_context_questions((change(frames(value, 30)[0]),), closing=value.closing))


@pytest.mark.parametrize("offset, updates", [
    (0, {"available_at": at(0)}),
    (29, {"available_at": at(33)}),
    (29, {"price_state": "missing"}),
    (29, {"prices": (100.0, 102.0, 99.0, 100.0)}),
    (29, {"prices": (Fraction(100), Fraction(98), Fraction(99), Fraction(100))}),
])
def test_malformed_visible_prices_are_integrity_errors(offset, updates):
    value = session(*BASE, PEAK, BELOW, TOUCH)
    row = frames(value, 30)[0]
    prefix = list(row.prefix)
    prefix[offset] = replace(prefix[offset], **updates)
    with pytest.raises(InputRefusal):
        tuple(scan_context_questions((replace(row, prefix=tuple(prefix)),), closing=value.closing))


def test_skipped_reordered_mixed_session_or_changed_observed_history_is_rejected():
    value = session(*BASE, PEAK, BELOW, TOUCH, BELOW, TOUCH)
    first, second, third = frames(value, 40)
    mutated_prefix = (replace(second.prefix[0], prices=(Fraction(99), Fraction(102), Fraction(98), Fraction(100))),
                      *second.prefix[1:])
    cases = ((second,), (first, third), (first, first), (first, replace(second, symbol="different")),
             (first, replace(second, prefix=mutated_prefix)))
    for case in cases:
        with pytest.raises(InputRefusal):
            tuple(scan_context_questions(case, closing=value.closing))


def test_replacing_fixed_anchor_or_clearing_invalidation_fails_integrity():
    value = session(*BASE, PEAK, BELOW, TOUCH, BELOW)
    first, second = frames(value, 35)
    changed = replace(second.anchor, price=Fraction(120), advance=Fraction(20))
    with pytest.raises(InputRefusal, match="fixed_anchor_changed"):
        tuple(scan_context_questions((first, replace(second, anchor=changed)), closing=value.closing))
    invalid = session(*BASE, PEAK, BELOW, (112, 116, 99, 100), BELOW)
    first, second = frames(invalid, 35)
    with pytest.raises(InputRefusal, match="invalidation_repaired"):
        tuple(scan_context_questions((first, replace(second, metrics=replace(second.metrics, invalidated_at=None))),
                                     closing=invalid.closing))


def test_empty_frame_stream_remains_empty():
    assert tuple(scan_context_questions((), closing=at(390))) == ()
