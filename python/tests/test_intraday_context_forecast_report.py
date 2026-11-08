"""Invented EI context attribution, paired masks and complete score rosters."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research import intraday_context_forecast_report as report
from research.intraday_context_forecast_fold import (
    MODEL_KEYS, SCHEMA, ArmPrediction, FoldForecast, QuestionForecast, TrainingSupport,
)
from research.intraday_context_snapshot import QuestionLabel, project_coordinate, question_digest
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_labels import label_horizons


DAY = "2031-02-12"
OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
        (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114))
TARGET = TargetCoordinate("S1", "invented", DAY, 1, 60, "observed", b"{}")


def score(forecast, reader, *, allowed_dates):
    return report.score_fold(forecast, reader, allowed_dates=allowed_dates, expected_targets=(TARGET,))


def fixture(short_case=False):
    continuation = ((114, 119, 113, 118), (118, 120, 118, 119)) if short_case else (
        (114, 118, 113, 117), (118, 125, 117, 124))
    bars = (*BASE, *continuation, *((124, 125, 123, 124),) * 4)
    minutes = {OPEN + i * MINUTE: Minute("invented", OPEN + i * MINUTE, OPEN + (i + 3) * MINUTE,
               tuple(map(Decimal, bar)), Decimal(10)) for number, bar in enumerate(bars)
               for i in range(number * 5, number * 5 + 5)}
    session = SessionInputs("invented", OPEN, OPEN + 60 * MINUTE, minutes)
    coordinate = project_coordinate((TARGET,), session)
    predictions = tuple(QuestionForecast(DAY, "invented", row,
        tuple(ArmPrediction(key, "predicted", (0.1,) * 10, None) for key in MODEL_KEYS))
        for row in coordinate.questions)
    support = TrainingSupport(0, 0, 0, 0, 0, 0, (), (), ())
    forecast = FoldForecast(SCHEMA, "S1", 1, OPEN, "injected_diagnostic", False,
                            (coordinate,), support, None, (), predictions, 0, 0)
    labels = {question_digest(row.question): QuestionLabel(row.question, session.closing + 2 * MINUTE,
        "observed", label_horizons(row.question.path, row.question.start_close, session,
                                     cutoff_at=session.closing + 2 * MINUTE)) for row in coordinate.questions}
    calls = []

    def reader(day, symbol, key):
        assert (day, symbol) == (DAY, "invented")
        calls.append(key)
        return labels[key]

    return forecast, labels, reader, calls


def test_same_predictions_zero_increment_and_read_each_label_once():
    forecast, _, reader, calls = fixture()
    result = score(forecast, reader, allowed_dates=(DAY,))
    assert result.original_coordinates == 1 and result.original_questions == len(forecast.predictions) == 2
    assert len(result.groups) == 48 and len(calls) == len(set(calls)) == 2
    assert dict(result.original_frames_by_status)["no_event"] > 0
    assert all(group.counts.first_brier == group.counts.second_brier for group in result.groups)
    merged = report.combine_fold_reports((result,))
    assert all(all(value in (None, 0.0) for value in row["second_minus_first"]) for row in merged)
    assert all(row["horizon_dates"] in ((0, 0, 0), (1, 1, 0), (1, 1, 1)) for row in merged)


def test_origin_context_does_not_change_with_later_question_context():
    forecast, _, reader, _ = fixture()
    breakout = next(row for row in forecast.predictions if row.question.question.stage == "breakout")
    assert breakout.question.question.episode_origin_context == "A"
    assert breakout.question.question.question_time_context == "other"
    result = score(forecast, reader, allowed_dates=(DAY,))
    groups = {(row.stage, row.origin_context, row.contrast): row.counts for row in result.groups}
    assert groups["breakout", "A", report.CONTRASTS[0][0]].candidates == 1
    assert groups["breakout", "other", report.CONTRASTS[0][0]].candidates == 0


@pytest.mark.parametrize("mutation", ("omitted", "duplicate", "changed_feature", "wrong_day", "arm_order", "mass"))
def test_bad_complete_prediction_roster_fails_before_label_access(mutation):
    forecast, _, reader, calls = fixture()
    rows = list(forecast.predictions)
    if mutation == "omitted":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = rows[0]
    elif mutation == "changed_feature":
        rows[-1] = replace(rows[-1], question=replace(rows[-1].question, reasons=("different",)))
    elif mutation == "wrong_day":
        rows[-1] = replace(rows[-1], session="2031-02-13")
    elif mutation == "arm_order":
        rows[-1] = replace(rows[-1], predictions=tuple(reversed(rows[-1].predictions)))
    else:
        arms = rows[-1].predictions
        rows[-1] = replace(rows[-1], predictions=(*arms[:-1], replace(arms[-1], probabilities=(0.2,) * 10)))
    with pytest.raises(ValueError):
        score(replace(forecast, predictions=tuple(rows)), reader, allowed_dates=(DAY,))
    assert not calls


def test_missing_outcomes_stay_unknown_without_becoming_zero_score():
    forecast, _, _, _ = fixture()
    result = score(forecast, lambda *_: None, allowed_dates=(DAY,))
    assert result.unavailable_labels_by_horizon == ((5, "label_missing", 2), (15, "label_missing", 2),
                                                   (30, "label_missing", 2))
    groups = [row.counts for row in result.groups if row.counts.candidates]
    assert all(row.paired_forecasts and row.paired_outcomes == 0 and row.first_brier is None for row in groups)


def test_one_unsupported_arm_uses_own_pair_mask_and_keeps_denominator():
    forecast, _, reader, _ = fixture()
    rows = tuple(replace(row, predictions=(*row.predictions[:-1],
        ArmPrediction(MODEL_KEYS[-1], "unsupported", None, "invented_missing_model"))) for row in forecast.predictions)
    result = score(replace(forecast, predictions=rows), reader, allowed_dates=(DAY,))
    for group in result.groups:
        if group.contrast in ("context_sequence_increment", "context_sequence_vs_tree"):
            assert group.counts.second_supported == group.counts.paired_forecasts == 0
            assert group.counts.candidates == group.counts.first_supported


def test_wrong_question_label_rejected():
    forecast, labels, _, _ = fixture()
    wrong = next(iter(labels.values()))
    with pytest.raises(ValueError, match="identity"):
        score(forecast, lambda *_: wrong, allowed_dates=(DAY,))


def test_original_coordinate_is_preserved_with_all_forecasts_unsupported():
    forecast, _, _, _ = fixture()
    rows = tuple(replace(row, predictions=tuple(ArmPrediction(key, "unsupported", None, "no_training")
                                                for key in MODEL_KEYS)) for row in forecast.predictions)
    result = score(replace(forecast, predictions=rows), lambda *_: pytest.fail("no paired forecast"),
                               allowed_dates=(DAY,))
    assert result.original_coordinates == 1 and result.original_questions == 2
    assert all(group.counts.first_supported == 0 for group in result.groups)


def test_overlapping_fold_dates_and_mixed_scenarios_are_not_independent():
    forecast, _, reader, _ = fixture()
    first = score(forecast, reader, allowed_dates=(DAY,))
    with pytest.raises(ValueError, match="overlap"):
        report.combine_fold_reports((first, replace(first, fold=2)))
    with pytest.raises(ValueError, match="order"):
        report.combine_fold_reports((first, replace(first, scenario="S2", fold=2)))


def test_short_child_horizon_is_scored_when_parent_is_unsupported():
    forecast, _, reader, _ = fixture(short_case=True)
    result = score(forecast, reader, allowed_dates=(DAY,))
    row = next(group.counts for group in result.groups if group.stage == "breakout"
               and group.origin_context == "all" and group.contrast == report.CONTRASTS[0][0])
    assert row.horizon_outcomes == (1, 1, 0)
    # Joint uniform mass projects to (.1,.1,.1,.7) at five minutes and
    # (.2,.2,.2,.4) at fifteen; the observed path is direct expansion P1.
    assert row.first_brier[:2] == pytest.approx((1.32, 0.88))
    assert row.first_brier[2] is None


def test_coherent_removed_coordinate_is_rejected_by_external_original_roster():
    forecast, _, reader, calls = fixture()
    with pytest.raises(ValueError, match="roster"):
        score(replace(forecast, original_test_coordinates=(), predictions=()), reader, allowed_dates=(DAY,))
    assert not calls


def test_shifted_training_cutoff_fails_before_any_evaluation_label():
    forecast, _, reader, calls = fixture()
    with pytest.raises(ValueError, match="split_clock"):
        score(replace(forecast, first_test_opening=OPEN + timedelta(days=1)), reader, allowed_dates=(DAY,))
    assert not calls


def test_all_unsupported_fold_dates_still_cannot_overlap():
    forecast, _, _, _ = fixture()
    rows = tuple(replace(row, predictions=tuple(ArmPrediction(key, "unsupported", None, "no_training")
                                                for key in MODEL_KEYS)) for row in forecast.predictions)
    first = score(replace(forecast, predictions=rows), lambda *_: pytest.fail("not paired"), allowed_dates=(DAY,))
    with pytest.raises(ValueError, match="declared_evaluation_dates_overlap"):
        report.combine_fold_reports((first, replace(first, fold=2)))


def test_terminal_path_cannot_change_between_supported_horizons():
    forecast, labels, reader, _ = fixture(short_case=True)
    key = next(row.question_id for row in forecast.predictions if row.question.question.stage == "breakout")
    label = labels[key]
    assert label.results[0].path == label.results[1].path == "P1"
    labels[key] = replace(label, results=(label.results[0], replace(label.results[1], path="P3"), label.results[2]))
    with pytest.raises(ValueError, match="terminal_path_changed"):
        score(forecast, reader, allowed_dates=(DAY,))


def test_timeout_path_requires_observed_horizon_end():
    forecast, labels, reader, _ = fixture(short_case=True)
    key = next(row.question_id for row in forecast.predictions if row.question.question.stage == "breakout")
    label = labels[key]
    labels[key] = replace(label, results=(replace(label.results[0], path="P4"), *label.results[1:]))
    with pytest.raises(ValueError, match="timeout_path_incomplete"):
        score(forecast, reader, allowed_dates=(DAY,))
