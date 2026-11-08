"""Invented-only independent EI report masks, denominators and arithmetic."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import fsum

import pytest

from research import intraday_context_forecast_report as producer
from research import intraday_context_forecast_report_reference as reference
from research.intraday_context_forecast_fold import (
    MODEL_KEYS, SCHEMA, ArmPrediction, FoldForecast, QuestionForecast, TrainingSupport,
)
from research.intraday_context_snapshot import QuestionLabel, project_coordinate, question_digest
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_labels import label_horizons


DAY = "2031-02-12"
NEXT_DAY = "2031-02-13"
KST = timezone(timedelta(hours=9))
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
        (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114))


def _coordinate(day, symbol, rank, kind):
    opening = datetime.combine(datetime.fromisoformat(day).date(), datetime.min.time(), KST) + timedelta(hours=9)
    target = TargetCoordinate("S1", symbol, day, rank, None if kind == "missing" else 60, "invented", b"{}")
    if kind == "missing":
        return target, project_coordinate((target,), None), None
    if kind == "flat":
        bars = ((100, 101, 99, 100),) * 12
    else:
        continuation = ((114, 119, 113, 118), (118, 120, 118, 119)) if kind in ("short", "repeated") else (
            (114, 118, 113, 117), (118, 125, 117, 124))
        repeated = ((124, 125, 123, 124), (114, 114, 110, 112), (112, 116, 111, 114),
                    (114, 119, 113, 118), (124, 125, 123, 124)) if kind == "repeated" else ()
        bars = (*BASE, *continuation, *repeated, *((124, 125, 123, 124),) * (8 if repeated else 4))
    minutes = {opening + index * MINUTE: Minute(symbol, opening + index * MINUTE, opening + (index + 3) * MINUTE,
               tuple(map(Decimal, bar)), Decimal(10)) for number, bar in enumerate(bars)
               for index in range(number * 5, number * 5 + 5)}
    session = SessionInputs(symbol, opening, opening + len(bars) * 5 * MINUTE, minutes)
    return target, project_coordinate((target,), session), session


def _fixture(specs=((DAY, "invented", "short"),)):
    targets, coordinates, predictions, labels = [], [], [], {}
    ranks = {}
    for day, symbol, kind in sorted(specs):
        ranks[day] = ranks.get(day, 0) + 1
        target, coordinate, session = _coordinate(day, symbol, ranks[day], kind)
        targets.append(target)
        coordinates.append(coordinate)
        for index, feature in enumerate(coordinate.questions):
            arms = []
            for arm_index, key in enumerate(MODEL_KEYS):
                # Distinct arm distributions expose incorrect contrast wiring.
                base = [0.03] * 10
                base[(arm_index + index) % 10] += 0.7
                arms.append(ArmPrediction(key, "predicted", tuple(base), None))
            predictions.append(QuestionForecast(day, symbol, feature, tuple(arms)))
            labels[day, symbol, question_digest(feature.question)] = QuestionLabel(
                feature.question, session.closing + 2 * MINUTE, "observed", label_horizons(
                    feature.question.path, feature.question.start_close, session, cutoff_at=session.closing + 2 * MINUTE))
    dates = tuple(sorted({row.session for row in targets}))
    opening = datetime.combine(datetime.fromisoformat(dates[0]).date(), datetime.min.time(), KST) + timedelta(hours=9)
    forecast = FoldForecast(SCHEMA, "S1", 1, opening, "injected_diagnostic", False, tuple(coordinates),
        TrainingSupport(0, 0, 0, 0, 0, 0, (), (), ()), None, (), tuple(predictions), 0, 0)
    calls = []

    def reader(*key):
        calls.append(key)
        return labels.get(key)

    return forecast, reader, labels, calls, dates, tuple(targets)


def _score(forecast, reader, dates, targets):
    return producer.score_fold(forecast, reader, allowed_dates=dates, expected_targets=targets)


def _verify(forecast, report, reader, dates, targets):
    return reference.verify_forecast_report(forecast, report, reader, allowed_dates=dates, expected_targets=targets)


def _change_group(report, change, *, stage="breakout", context="all", contrast="context_empirical_increment"):
    groups = tuple(change(group) if (group.stage, group.origin_context, group.contrast) == (stage, context, contrast)
                   else group for group in report.groups)
    return replace(report, groups=groups)


def test_complete_universe_groups_and_one_read_per_paired_question():
    forecast, reader, _, calls, dates, targets = _fixture((
        (DAY, "a", "normal"), (DAY, "b", "flat"), (DAY, "c", "missing"), (NEXT_DAY, "a", "short")))
    report = _score(forecast, reader, dates, targets)
    calls.clear()
    proof = _verify(forecast, report, reader, dates, targets)
    assert proof["verified"] and proof["groups"] == 48 and proof["original_coordinates"] == 4
    assert proof["original_questions"] == len(forecast.predictions)
    assert len(calls) == len(set(calls)) == proof["evaluation_labels_read"]
    assert proof["maximum_absolute_difference"] < 1e-12 and proof["evaluation_dates"] == dates
    assert dict(report.original_frames_by_status)["no_event"] > 0
    assert dict(report.coordinate_status)["session_missing"] == 1


def test_original_context_not_later_context_and_supported_short_children():
    original, original_reader, _, _, original_dates, original_targets = _fixture(((DAY, "invented", "normal"),))
    original_breakout = next(row for row in original.predictions if row.question.question.stage == "breakout")
    assert original_breakout.question.question.episode_origin_context == "A"
    assert original_breakout.question.question.question_time_context == "other"
    original_report = _score(original, original_reader, original_dates, original_targets)
    assert _verify(original, original_report, original_reader, original_dates, original_targets)["verified"]
    forecast, reader, _, _, dates, targets = _fixture(((DAY, "invented", "short"),))
    breakout = next(row for row in forecast.predictions if row.question.question.stage == "breakout")
    assert breakout.question.question.episode_origin_context == "A"
    uniform = tuple(replace(row, predictions=tuple(replace(arm, probabilities=(0.1,) * 10) for arm in row.predictions))
                    for row in forecast.predictions)
    forecast = replace(forecast, predictions=uniform)
    report = _score(forecast, reader, dates, targets)
    assert _verify(forecast, report, reader, dates, targets)["verified"]
    group = next(group for group in report.groups if (group.stage, group.origin_context, group.contrast)
                 == ("breakout", "A", "context_empirical_increment"))
    assert group.counts.horizon_outcomes == (1, 1, 0)
    assert group.counts.first_brier[:2] == pytest.approx((1.32, 0.88))
    assert group.counts.first_brier[2] is None
    changed = _change_group(report, lambda group: replace(group, origin_context="other"), context="A")
    with pytest.raises(InputRefusal, match="group_roster"):
        _verify(forecast, changed, reader, dates, targets)


def test_question_symbol_date_weighting_has_a_hand_computed_unequal_roster():
    forecast, reader, _, _, dates, targets = _fixture(((DAY, "a", "repeated"), (DAY, "b", "short"),
                                                     (NEXT_DAY, "a", "short")))
    rows = []
    weights = {(DAY, "a"): iter((0.0, 0.5)), (DAY, "b"): iter((1.0,)), (NEXT_DAY, "a"): iter((0.25,))}
    counts = {}
    for row in forecast.predictions:
        if row.question.question.stage == "breakout":
            key = row.session, row.symbol
            counts[key] = counts.get(key, 0) + 1
            probability = next(weights[key])
            distribution = (probability, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1 - probability)
            row = replace(row, predictions=(replace(row.predictions[0], probabilities=distribution), *row.predictions[1:]))
        rows.append(row)
    assert counts == {(DAY, "a"): 2, (DAY, "b"): 1, (NEXT_DAY, "a"): 1}
    forecast = replace(forecast, predictions=tuple(rows))
    report = _score(forecast, reader, dates, targets)
    assert _verify(forecast, report, reader, dates, targets)["verified"]
    group = next(group for group in report.groups if (group.stage, group.origin_context, group.contrast)
                 == ("breakout", "all", "context_empirical_increment"))
    # Each breakout reaches P1 within five minutes. Brier=2*(1-p)**2.
    day_one = ((2.0 + 0.5) / 2 + 0.0) / 2
    day_two = 2 * 0.75**2
    expected = (day_one + day_two) / 2
    assert group.counts.first_brier[0] == pytest.approx(expected)
    assert expected != pytest.approx(fsum((2.0, 0.5, 0.0, 1.125)) / 4)


def test_no_scoring_or_marginalization_helper_can_control_reference(monkeypatch):
    forecast, reader, _, _, dates, targets = _fixture()
    report = _score(forecast, reader, dates, targets)

    def fail(*args, **kwargs):
        raise AssertionError("producer score helper executed")

    for name in ("score_fold", "matched_forecast_scores", "_arms", "_key", "_probabilities", "_consistent_paths"):
        monkeypatch.setattr(producer, name, fail)
    monkeypatch.setattr(producer, "CONTRASTS", ())
    assert _verify(forecast, report, reader, dates, targets)["verified"]


def test_current_context_misattribution_with_unchanged_group_names_refuses():
    forecast, reader, _, _, dates, targets = _fixture(((DAY, "invented", "normal"),))
    report = _score(forecast, reader, dates, targets)
    source = {(group.stage, group.origin_context, group.contrast): group.counts for group in report.groups}
    groups = tuple(replace(group, counts=source[group.stage, "other" if group.origin_context == "A" else "A", group.contrast])
                   if group.stage == "breakout" and group.origin_context in ("A", "other") else group for group in report.groups)
    with pytest.raises(InputRefusal):
        _verify(forecast, replace(report, groups=groups), reader, dates, targets)


def test_wrong_probabilities_under_unchanged_arm_names_refuse():
    forecast, reader, _, _, dates, targets = _fixture()
    report = _score(forecast, reader, dates, targets)
    changed = []
    for row in forecast.predictions:
        arms = row.predictions
        changed.append(replace(row, predictions=(replace(arms[0], probabilities=arms[1].probabilities),
                       replace(arms[1], probabilities=arms[0].probabilities), *arms[2:])))
    with pytest.raises(InputRefusal):
        _verify(replace(forecast, predictions=tuple(changed)), report, reader, dates, targets)


def test_coherently_removed_no_event_coordinate_still_fails_external_roster():
    forecast, reader, _, calls, dates, targets = _fixture(((DAY, "a", "short"), (DAY, "b", "flat")))
    report = _score(forecast, reader, dates, targets)
    calls.clear()
    changed = replace(forecast, original_test_coordinates=forecast.original_test_coordinates[:1])
    with pytest.raises(InputRefusal, match="coordinate_roster"):
        _verify(changed, replace(report, original_coordinates=1), reader, dates, targets)
    assert not calls


@pytest.mark.parametrize("mutation", ("coordinate_count", "question_count", "frame_count", "coordinate_status", "unknown_horizon",
                                     "group_drop", "group_duplicate", "group_order", "scenario", "fold", "dates"))
def test_top_level_denominator_and_complete_strata_mutations_refuse(mutation):
    forecast, reader, _, _, dates, targets = _fixture(((DAY, "a", "normal"), (DAY, "b", "flat")))
    report = _score(forecast, reader, dates, targets)
    if mutation == "coordinate_count":
        report = replace(report, original_coordinates=1)
    elif mutation == "question_count":
        report = replace(report, original_questions=99)
    elif mutation == "frame_count":
        report = replace(report, original_frames_by_status=(("questions", 2),))
    elif mutation == "coordinate_status":
        report = replace(report, coordinate_status=(("session_missing", 2),))
    elif mutation == "unknown_horizon":
        report = replace(report, unavailable_labels_by_horizon=())
    elif mutation == "group_drop":
        report = replace(report, groups=report.groups[:-1])
    elif mutation == "group_duplicate":
        report = replace(report, groups=(*report.groups[:-1], report.groups[0]))
    elif mutation == "group_order":
        report = replace(report, groups=tuple(reversed(report.groups)))
    elif mutation == "scenario":
        report = replace(report, scenario="S2")
    elif mutation == "fold":
        report = replace(report, fold=2)
    else:
        report = replace(report, evaluation_dates=(NEXT_DAY,))
    with pytest.raises(InputRefusal):
        _verify(forecast, report, reader, dates, targets)


@pytest.mark.parametrize("field", ("candidates", "first_supported", "second_supported", "paired_forecasts", "paired_outcomes",
                                  "scored_events", "scored_dates", "horizon_pairs", "horizon_outcomes", "horizon_events",
                                  "horizon_dates", "first_brier", "second_brier", "daily_difference"))
def test_every_score_field_is_recomputed(field):
    forecast, reader, _, _, dates, targets = _fixture()
    report = _score(forecast, reader, dates, targets)

    def change(group):
        value = getattr(group.counts, field)
        if field == "daily_difference":
            value = ((value[0][0], (0.123, *value[0][1][1:])), *value[1:])
        elif type(value) is tuple:
            value = (value[0] + 0.123 if type(value[0]) is float else value[0] + 1, *value[1:])
        else:
            value += 1
        return replace(group, counts=replace(group.counts, **{field: value}))

    with pytest.raises(InputRefusal):
        _verify(forecast, _change_group(report, change), reader, dates, targets)


@pytest.mark.parametrize("mutation", ("omitted_coordinate", "duplicate_coordinate", "missing_question", "wrong_feature", "wrong_arm",
                                     "arm_order", "nan_probability", "bad_mass", "before_split", "schema", "independent_scenarios",
                                     "duplicate_dates", "extra_date"))
def test_forecast_scope_and_arm_mutations_fail_before_label_reads(mutation):
    forecast, reader, _, calls, dates, targets = _fixture()
    report = _score(forecast, reader, dates, targets)
    calls.clear()
    if mutation == "omitted_coordinate":
        forecast = replace(forecast, original_test_coordinates=(), predictions=())
    elif mutation == "duplicate_coordinate":
        forecast = replace(forecast, original_test_coordinates=forecast.original_test_coordinates * 2)
    elif mutation == "missing_question":
        forecast = replace(forecast, predictions=forecast.predictions[:-1])
    elif mutation == "wrong_feature":
        row = forecast.predictions[0]
        forecast = replace(forecast, predictions=(replace(row, question=replace(row.question, reasons=("changed",))),
                                                   *forecast.predictions[1:]))
    elif mutation in ("wrong_arm", "arm_order", "nan_probability", "bad_mass"):
        row = forecast.predictions[0]
        arms = list(row.predictions)
        if mutation == "wrong_arm":
            arms[0] = replace(arms[0], key="context_sequence")
        elif mutation == "arm_order":
            arms.reverse()
        else:
            arms[0] = replace(arms[0], probabilities=(float("nan"),) * 10 if mutation == "nan_probability" else (0.2,) * 10)
        forecast = replace(forecast, predictions=(replace(row, predictions=tuple(arms)), *forecast.predictions[1:]))
    elif mutation == "before_split":
        forecast = replace(forecast, first_test_opening=forecast.first_test_opening + timedelta(days=1))
    elif mutation == "schema":
        forecast = replace(forecast, schema="changed")
    elif mutation == "independent_scenarios":
        forecast = replace(forecast, timing_scenarios_are_independent=True)
    elif mutation == "duplicate_dates":
        dates *= 2
    else:
        dates = *dates, NEXT_DAY
    with pytest.raises(InputRefusal):
        _verify(forecast, report, reader, dates, targets)
    assert not calls


def test_all_unsupported_preserves_universe_without_reading_labels_and_missing_labels_are_not_zero():
    forecast, reader, _, _, dates, targets = _fixture()
    missing = _score(forecast, lambda *_: None, dates, targets)
    assert _verify(forecast, missing, lambda *_: None, dates, targets)["evaluation_labels_read"] == 2
    assert all(group.counts.first_brier is None for group in missing.groups)
    rows = tuple(replace(row, predictions=tuple(ArmPrediction(key, "unsupported", None, "no_training") for key in MODEL_KEYS))
                 for row in forecast.predictions)
    forecast = replace(forecast, predictions=rows)

    def forbidden(*args):
        pytest.fail("an unpaired question must not open evaluation labels")

    report = _score(forecast, forbidden, dates, targets)
    proof = _verify(forecast, report, forbidden, dates, targets)
    assert proof["original_questions"] == 2 and proof["evaluation_labels_read"] == 0


def test_partial_arm_support_has_contrast_specific_masks():
    forecast, reader, _, _, dates, targets = _fixture()
    first = forecast.predictions[0]
    first = replace(first, predictions=(*first.predictions[:-1], ArmPrediction("context_sequence", "unsupported", None, "missing")))
    forecast = replace(forecast, predictions=(first, *forecast.predictions[1:]))
    report = _score(forecast, reader, dates, targets)
    assert _verify(forecast, report, reader, dates, targets)["verified"]
    for group in report.groups:
        if group.stage == "approach" and group.contrast.startswith("context_sequence"):
            assert group.counts.candidates == group.counts.first_supported
            assert group.counts.paired_forecasts == group.counts.second_supported == 0


@pytest.mark.parametrize("mutation", ("identity", "maturity", "cutoff", "terminal_changed", "terminal_unknown", "unfinished_timeout"))
def test_evaluation_identity_maturity_and_cross_horizon_consistency_refuse(mutation):
    forecast, reader, labels, _, dates, targets = _fixture()
    report = _score(forecast, reader, dates, targets)
    key = next(key for key, label in labels.items() if label.question.stage == "breakout")
    label = labels[key]
    assert label.results[0].status == "resolved" and label.results[0].path == "P1"
    if mutation == "identity":
        labels[key] = next(value for value in labels.values() if value.question.stage == "approach")
    elif mutation == "cutoff":
        labels[key] = replace(label, cutoff_at=label.cutoff_at - MINUTE)
    else:
        results = list(label.results)
        if mutation == "maturity":
            results[0] = replace(results[0], mature_at=results[0].observed_through)
        elif mutation == "terminal_changed":
            results[1] = replace(results[1], path="P3")
        elif mutation == "terminal_unknown":
            results[1] = replace(results[1], status="unknown", path=None, mature_at=None, reason="missing_price")
        else:
            results[0] = replace(results[0], path="P4")
        labels[key] = replace(label, results=tuple(results))
    with pytest.raises(InputRefusal):
        _verify(forecast, report, reader, dates, targets)


def test_duplicate_daily_dates_and_nonfinite_score_refuse():
    forecast, reader, _, _, dates, targets = _fixture()
    report = _score(forecast, reader, dates, targets)
    for change in (
        lambda group: replace(group, counts=replace(group.counts, daily_difference=group.counts.daily_difference * 2)),
        lambda group: replace(group, counts=replace(group.counts, first_brier=(float("nan"), *group.counts.first_brier[1:]))),
    ):
        with pytest.raises(InputRefusal):
            _verify(forecast, _change_group(report, change), reader, dates, targets)
