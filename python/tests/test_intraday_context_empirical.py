"""Invented-only EI common-roster empirical probability checks."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from fractions import Fraction

import pytest

from research.intraday_context_empirical import (
    CONTEXT_PRIOR_WEIGHT, JEFFREYS_PER_CLASS, SCHEMA, EmpiricalTrainingRow, fit_empirical, weighted_training_rows,
)
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome
from research.intraday_scenario_labels import PathQuestion


KST = timezone(timedelta(hours=9))
OPEN = datetime(2031, 2, 12, 9, tzinfo=KST)
CUTOFF = OPEN + timedelta(days=2)


def training(*, symbol="invented", day=0, episode=1, stage="approach", context="A", outcome=0,
             missing=None, offset=32):
    opening = OPEN + timedelta(days=day)
    at = opening + timedelta(minutes=offset)
    anchor = ResistanceAnchor(opening, Fraction(100), opening + timedelta(minutes=20),
                              opening + timedelta(minutes=27), Fraction(116), Fraction(4), Fraction(16), Fraction(1))
    question = PathQuestion(at, at + timedelta(minutes=30), 116, 1, 124, 112)
    key = ((symbol, anchor), episode), stage, question.question_id
    label = None if outcome is None else TrainingOutcome(outcome, at + timedelta(minutes=32))
    return EmpiricalTrainingRow(key, symbol, opening.date().isoformat(), stage, context, at, label, missing)


def prediction(model, *, stage="approach", context="A"):
    return model.predict(stage=stage, origin_context=context, forecast_at=CUTOFF)


def test_constants_and_exact_hand_calculated_single_row():
    assert SCHEMA == "ei-context-empirical-draft-v1"
    assert (JEFFREYS_PER_CLASS, CONTEXT_PRIOR_WEIGHT, len(OUTCOMES)) == (Fraction(1, 2), 20, 10)
    model = fit_empirical([training()], cutoff_at=CUTOFF)
    result = prediction(model)
    assert result.pooled == (Fraction(1, 4),) + (Fraction(1, 12),) * 9
    assert result.conditioned == (Fraction(2, 7),) + (Fraction(5, 63),) * 9
    assert sum(result.pooled) == sum(result.conditioned) == 1
    assert result.common_support.candidate_rows == result.common_support.supported_rows == 1
    assert result.common_support.weighted_count == result.common_support.symbol_days == result.common_support.dates == 1
    assert result.status == "supported" and result.reason is None and not result.context_falls_back_to_pooled
    assert all(type(value) is Fraction for value in (*result.pooled, *result.conditioned))


def test_same_symbol_day_repetitions_share_one_weight_across_contexts():
    rows = [training(outcome=0), training(episode=2, offset=42, context="B", outcome=1),
            training(symbol="second", context="B", outcome=1)]
    model = fit_empirical(rows, cutoff_at=CUTOFF)
    stage = model.stages[0]
    assert stage.counts == (Fraction(1, 2), Fraction(3, 2)) + (Fraction(),) * 8
    assert stage.support.supported_rows == 3
    assert (stage.support.symbol_days, stage.support.dates, stage.support.weighted_count) == (2, 1, 2)
    assert stage.contexts[0].support.weighted_count == Fraction(1, 2)
    assert stage.contexts[1].support.weighted_count == Fraction(3, 2)
    first, second = prediction(model), prediction(model, context="B")
    assert first.pooled == second.pooled == (Fraction(1, 7), Fraction(2, 7)) + (Fraction(1, 14),) * 8
    assert first.common_roster_sha256 == second.common_roster_sha256
    assert first.conditioned != second.conditioned
    assert sum(first.conditioned) == sum(second.conditioned) == 1


def test_another_date_adds_group_weight_but_same_date_is_not_a_new_date():
    rows = [training(), training(symbol="second"), training(day=1), training(day=1, episode=2, offset=42)]
    support = prediction(fit_empirical(rows, cutoff_at=CUTOFF)).common_support
    assert (support.candidate_rows, support.supported_rows, support.symbol_days, support.dates) == (4, 4, 3, 2)
    assert support.weighted_count == 3


def test_stage_isolation_and_same_clock_stages_are_distinct_questions():
    approach = training()
    breakout = training(stage="breakout", outcome=9)
    model = fit_empirical([approach, breakout], cutoff_at=CUTOFF)
    assert prediction(model).pooled == prediction(fit_empirical([approach], cutoff_at=CUTOFF)).pooled
    assert prediction(model, stage="breakout").pooled == (Fraction(1, 12),) * 9 + (Fraction(1, 4),)
    assert model.stages[0].supported_roster_sha256 != model.stages[1].supported_roster_sha256


def test_missing_labels_are_not_class_zero_and_stay_in_original_counts():
    observed = training(outcome=9)
    missing = training(episode=2, offset=42, outcome=None, missing="price_gap")
    another = training(episode=3, offset=52, context="B", outcome=None, missing="same_minute_order_unknown")
    model = fit_empirical([observed, missing, another], cutoff_at=CUTOFF)
    result = prediction(model)
    assert result.pooled == prediction(fit_empirical([observed], cutoff_at=CUTOFF)).pooled
    assert (result.common_support.candidate_rows, result.common_support.supported_rows) == (3, 1)
    assert result.common_support.unavailable_by_reason == (("price_gap", 1), ("same_minute_order_unknown", 1))
    assert result.common_support.weighted_count == 1
    assert model.stages[0].candidate_roster_sha256 != model.stages[0].supported_roster_sha256
    fallback = prediction(model, context="B")
    assert fallback.context_falls_back_to_pooled and fallback.conditioned == fallback.pooled
    assert fallback.context_support.candidate_rows == 1 and fallback.context_support.supported_rows == 0


@pytest.mark.parametrize("rows", [(), (training(outcome=None, missing="price_gap"),)])
def test_zero_supported_training_never_becomes_uniform_candidate(rows):
    model = fit_empirical(rows, cutoff_at=CUTOFF)
    for stage in ("approach", "breakout"):
        result = prediction(model, stage=stage)
        assert result.status == "unsupported"
        assert result.reason == "no_mature_supported_training_rows"
        assert result.pooled is result.conditioned is None
        assert result.common_support.weighted_count == 0


def test_absent_context_uses_same_stage_pooled_distribution():
    model = fit_empirical([training(), training(day=1, outcome=1)], cutoff_at=CUTOFF)
    result = prediction(model, context="other")
    assert result.context_falls_back_to_pooled
    assert result.conditioned == result.pooled
    assert result.common_support.supported_rows == 2 and result.context_support.supported_rows == 0


def test_order_does_not_change_model_and_outcome_changes_do_not_change_roster():
    rows = [training(), training(episode=2, offset=42, outcome=1)]
    first = fit_empirical(iter(rows), cutoff_at=CUTOFF)
    assert first == fit_empirical(reversed(rows), cutoff_at=CUTOFF)
    changed = fit_empirical([replace(rows[0], outcome=replace(rows[0].outcome, index=2)), rows[1]], cutoff_at=CUTOFF)
    assert first.input_sha256 != changed.input_sha256
    assert first.stages[0].supported_roster_sha256 == changed.stages[0].supported_roster_sha256
    assert first.stages[0].candidate_roster_sha256 == changed.stages[0].candidate_roster_sha256


def test_canonical_identity_does_not_depend_on_equivalent_timezone_spelling():
    row = training()
    anchor_id, episode = row.question_id[0]
    symbol, anchor = anchor_id
    utc_anchor = replace(anchor, start_at=anchor.start_at.astimezone(UTC), peak_end=anchor.peak_end.astimezone(UTC),
                         confirmed_at=anchor.confirmed_at.astimezone(UTC))
    translated = replace(row, question_id=(((symbol, utc_anchor), episode), row.stage, row.question_id[2]),
                         forecast_at=row.forecast_at.astimezone(UTC),
                         outcome=replace(row.outcome, mature_at=row.outcome.mature_at.astimezone(UTC)))
    assert fit_empirical([row], cutoff_at=CUTOFF) == fit_empirical([translated], cutoff_at=CUTOFF.astimezone(UTC))


@pytest.mark.parametrize("change", [{}, {"origin_context": "B"}, {"outcome": TrainingOutcome(1, OPEN + timedelta(minutes=64))}])
def test_duplicate_questions_and_conflicting_metadata_are_rejected(change):
    row = training()
    with pytest.raises(InputRefusal, match="duplicate_question"):
        fit_empirical([row, replace(row, **change)], cutoff_at=CUTOFF)


def test_one_episode_cannot_repeat_stage_or_change_its_origin_context():
    with pytest.raises(InputRefusal, match="duplicate_episode_stage"):
        fit_empirical([training(), training(offset=42)], cutoff_at=CUTOFF)
    with pytest.raises(InputRefusal, match="episode_origin_changed"):
        fit_empirical([training(), training(stage="breakout", context="B")], cutoff_at=CUTOFF)
    with pytest.raises(InputRefusal, match="breakout_precedes_approach"):
        fit_empirical([training(offset=42), training(stage="breakout")], cutoff_at=CUTOFF)


def test_coordinate_cannot_silently_replace_its_original_anchor():
    original = training()
    changed = training(episode=2, offset=42)
    anchor = replace(changed.question_id[0][0][1], confirmed_at=OPEN + timedelta(minutes=28))
    changed = replace(changed, question_id=(((changed.symbol, anchor), 2), changed.stage, changed.question_id[2]))
    with pytest.raises(InputRefusal, match="first_anchor_changed"):
        fit_empirical([original, changed], cutoff_at=CUTOFF)


def test_initial_breakout_episode_zero_is_supported_but_zero_approach_and_bool_are_not():
    row = training(stage="breakout", episode=0)
    assert prediction(fit_empirical([row], cutoff_at=CUTOFF), stage="breakout").status == "supported"
    for bad in (training(episode=0), training(stage="breakout", episode=False)):
        with pytest.raises(InputRefusal, match="question_identity"):
            fit_empirical([bad], cutoff_at=CUTOFF)


@pytest.mark.parametrize("change", [
    {"symbol": "wrong"}, {"session": "2031-02-11"}, {"stage": "breakout"}, {"origin_context": "unknown"},
    {"forecast_at": OPEN + timedelta(minutes=33)}, {"forecast_at": CUTOFF},
    {"forecast_at": OPEN.replace(tzinfo=None)}, {"outcome": None},
    {"outcome": None, "unavailable_reason": " "}, {"unavailable_reason": "gap"},
    {"outcome": TrainingOutcome(True, OPEN + timedelta(minutes=64))},
    {"outcome": TrainingOutcome(-1, OPEN + timedelta(minutes=64))},
    {"outcome": TrainingOutcome(10, OPEN + timedelta(minutes=64))},
    {"outcome": TrainingOutcome(0, CUTOFF)}, {"outcome": TrainingOutcome(0, CUTOFF + timedelta(minutes=1))},
    {"outcome": TrainingOutcome(0, OPEN + timedelta(minutes=32))},
    {"outcome": TrainingOutcome(9, OPEN + timedelta(minutes=63))},
])
def test_malformed_or_immature_inputs_are_rejected(change):
    with pytest.raises((InputRefusal, ValueError)):
        fit_empirical([replace(training(), **change)], cutoff_at=CUTOFF)


@pytest.mark.parametrize("index,minimum", [(0, 3), (2, 3), (3, 8), (5, 8), (6, 18), (8, 18), (9, 32)])
def test_joint_outcome_cannot_mature_before_its_first_possible_bucket(index, minimum):
    row = training(outcome=index)
    earliest = row.forecast_at + timedelta(minutes=minimum)
    fit_empirical([replace(row, outcome=TrainingOutcome(index, earliest))], cutoff_at=CUTOFF)
    with pytest.raises(InputRefusal, match="not_mature"):
        fit_empirical([replace(row, outcome=TrainingOutcome(index, earliest - timedelta(minutes=1)))], cutoff_at=CUTOFF)


@pytest.mark.parametrize("change", [
    {"stage": "unknown"}, {"origin_context": "unknown"}, {"forecast_at": CUTOFF - timedelta(minutes=1)},
])
def test_prediction_cannot_cross_stage_context_or_training_cutoff(change):
    model = fit_empirical([training()], cutoff_at=CUTOFF)
    with pytest.raises(InputRefusal):
        model.predict(**{"stage": "approach", "origin_context": "A", "forecast_at": CUTOFF, **change})


def test_model_and_nested_result_are_immutable_and_have_no_reader():
    model = fit_empirical([training()], cutoff_at=CUTOFF)
    result = prediction(model)
    for target, name, value in ((model, "input_sha256", "x"), (model.stages[0], "counts", ()),
                                 (result, "pooled", ()), (result.common_support, "supported_rows", 0)):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, value)


def test_shared_model_weight_api_matches_empirical_counts_and_validates_missing_rows():
    rows = [training(), training(episode=2, offset=42, context="B", outcome=1),
            training(symbol="second", outcome=2), training(episode=3, offset=52, outcome=None, missing="gap")]
    weighted = weighted_training_rows(rows, cutoff_at=CUTOFF)
    assert weighted == weighted_training_rows(reversed(rows), cutoff_at=CUTOFF)
    assert len(weighted) == 3 and sum(weight for _, weight in weighted) == 2
    assert {row.symbol: weight for row, weight in weighted} == {"invented": Fraction(1, 2), "second": Fraction(1)}
    counts = tuple(sum((weight for row, weight in weighted if row.outcome.index == index), Fraction())
                   for index in range(len(OUTCOMES)))
    assert counts == fit_empirical(rows, cutoff_at=CUTOFF).stages[0].counts
    assert weighted_training_rows([rows[-1]], cutoff_at=CUTOFF) == ()
    with pytest.raises(InputRefusal, match="unavailable_reason"):
        weighted_training_rows([replace(rows[-1], unavailable_reason=None)], cutoff_at=CUTOFF)
    with pytest.raises(InputRefusal, match="duplicate_question"):
        weighted_training_rows([rows[0], rows[0]], cutoff_at=CUTOFF)
