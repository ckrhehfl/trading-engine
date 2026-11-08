"""Invented common EI inputs; no data reader or numerical runtime activation."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from math import asinh
import struct

import pytest

from research.intraday_context_empirical import EmpiricalTrainingRow, fit_empirical
from research.intraday_context_features import NumericContextFeatures, project_context_features
from research.intraday_context_questions import scan_context_questions
from research.intraday_context_model_inputs import (
    ModelTrainingRow, prepare_queries, prepare_training, training_contract,
    validate_queries, validate_training, validate_training_contract,
)
from research.intraday_context_situations import ResistanceAnchor, scan_context_frames
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs
from research.intraday_scenario_forecast_targets import TrainingOutcome
from research.intraday_scenario_labels import PathQuestion


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
CUTOFF = OPEN + timedelta(days=2)


def sample(index=0, *, mode="chart", day=0, symbol=None, episode=1, stage="approach", outcome=0, missing=None):
    opening = OPEN + timedelta(days=day)
    symbol = symbol or f"invented{index}"
    at = opening + timedelta(minutes=32 + 5 * (episode - 1))
    anchor = ResistanceAnchor(opening, Fraction(100), opening + timedelta(minutes=20),
                              opening + timedelta(minutes=27), Fraction(116), Fraction(4), Fraction(16), Fraction(1))
    path = PathQuestion(at, at + timedelta(minutes=30), 116, 1, 124, 112)
    key = ((symbol, anchor), episode), stage, path.question_id
    close = float(index % 10) / 10
    minute = tuple((close - 0.1, close + 0.2, close - 0.2, close, 1.0, 1.0, 1.0) for _ in range(30))
    static = (float(stage == "breakout"), float((at - opening).total_seconds() / 60),
              0.25, 2.0 - close, close + 1.0, 30.0, close)
    if mode == "chart_context":
        static += (1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 4.0, 0.2, 3.0, 1.0)
    features = NumericContextFeatures(key, at, mode, minute, static)
    label = None if outcome is None else TrainingOutcome(outcome, at + timedelta(minutes=32))
    empirical = EmpiricalTrainingRow(key, symbol, opening.date().isoformat(), stage, "A", at, label, missing)
    return ModelTrainingRow(empirical, features)


@pytest.mark.parametrize("mode,width", [("chart", 217), ("chart_context", 227)])
def test_same_asinh_values_and_row_identity_are_available_to_both_learned_models(mode, width):
    raw = sample(mode=mode)
    training = prepare_training([raw], with_context=mode == "chart_context", cutoff_at=CUTOFF, fold=1)
    assert len(training.feature_names) == len(training.rows[0].flat) == width
    assert training.rows[0].minute == tuple(tuple(asinh(value) for value in row) for row in raw.features.minute)
    assert training.rows[0].static == tuple(asinh(value) for value in raw.features.static)
    assert training.rows[0].question_id == raw.empirical.question_id
    assert training.outcomes == (0,) and training.exact_weights == (Fraction(1),) and training.weights == (1.0,)
    assert training.outcome_mature_at == (raw.empirical.outcome.mature_at,)
    validate_training_contract(training_contract(training))


def test_common_binary32_weights_preserve_exact_fractions_and_report_error():
    rows = [sample(index, symbol="same", episode=index + 1) for index in range(3)]
    rows.append(sample(symbol="same", stage="breakout"))
    training = prepare_training(rows, with_context=False, cutoff_at=CUTOFF, fold=1)
    third = struct.unpack("!f", struct.pack("!f", 1 / 3))[0]
    assert sorted(training.exact_weights) == [Fraction(1, 3)] * 3 + [Fraction(1)]
    assert sorted(training.weights) == [third] * 3 + [1.0]
    assert training.max_weight_rounding_error == Fraction.from_float(third) - Fraction(1, 3)
    contract = training_contract(training)
    assert contract["event_count_unit"] == "symbol_session_not_trade_or_independent_trial"
    assert contract["weighted_stage_groups"] == 2
    assert training.event_count == 1 and training.date_count == 1
    assert training == prepare_training(reversed(rows), with_context=False, cutoff_at=CUTOFF, fold=1)


def test_none_outcomes_keep_original_support_but_never_become_training_class_zero():
    rows = [sample(), sample(1, outcome=None, missing="price_gap")]
    training = prepare_training(rows, with_context=False, cutoff_at=CUTOFF, fold=1)
    assert training.candidate_rows == 2 and len(training.rows) == 1
    assert training.unavailable_by_reason == (("price_gap", 1),)
    empty = prepare_training(rows[1:], with_context=False, cutoff_at=CUTOFF, fold=1)
    assert empty.rows == empty.outcomes == empty.weights == ()
    validate_training_contract(training_contract(empty))


def test_query_api_has_no_outcome_and_enforces_split_without_reordering():
    inputs = tuple(sample(index, day=2).features for index in (2, 1))
    queries = prepare_queries(inputs, with_context=False, cutoff_at=CUTOFF, fold=1)
    assert [row.question_id for row in queries.rows] == [row.question_id for row in inputs]
    assert not hasattr(queries, "outcomes")
    with pytest.raises(InputRefusal):
        prepare_queries([sample()], with_context=False, cutoff_at=CUTOFF, fold=1)
    with pytest.raises(InputRefusal, match="out_of_split"):
        prepare_queries([sample().features], with_context=False, cutoff_at=CUTOFF, fold=1)
    with pytest.raises(InputRefusal, match="duplicate"):
        prepare_queries(inputs * 2, with_context=False, cutoff_at=CUTOFF, fold=1)


@pytest.mark.parametrize("change", [{"mode": "chart_context"}, {"minute": ()}, {"static": ()},
    {"forecast_at": OPEN + timedelta(minutes=33)}])
def test_feature_mode_shape_and_identity_cannot_drift(change):
    row = sample()
    with pytest.raises(InputRefusal):
        prepare_training([replace(row, features=replace(row.features, **change))],
                         with_context=False, cutoff_at=CUTOFF, fold=1)


@pytest.mark.parametrize("bad", [True, 1, float("nan"), float("inf")])
def test_raw_numeric_features_reject_bools_and_non_binary64_values(bad):
    row = sample()
    features = replace(row.features, static=(bad, *row.features.static[1:]))
    with pytest.raises(InputRefusal, match="numeric_shape"):
        prepare_training([replace(row, features=features)], with_context=False, cutoff_at=CUTOFF, fold=1)


def test_feature_origin_and_volume_masks_cannot_contradict_their_metadata():
    row = sample(mode="chart_context")
    features = replace(row.features, static=(*row.features.static[:7], 0.0, 1.0, 0.0, *row.features.static[10:]))
    with pytest.raises(InputRefusal, match="origin_context"):
        prepare_training([replace(row, features=features)], with_context=True, cutoff_at=CUTOFF, fold=1)
    row = sample()
    first = (*row.features.minute[0][:5], 0.0, 1.0)
    features = replace(row.features, minute=(first, *row.features.minute[1:]))
    with pytest.raises(InputRefusal, match="volume_mask"):
        prepare_training([replace(row, features=features)], with_context=False, cutoff_at=CUTOFF, fold=1)


@pytest.mark.parametrize("field,value", [("fold", True), ("with_context", 1)])
def test_mode_and_fold_cannot_use_boolean_integer_aliases(field, value):
    options = {"fold": 1, "with_context": False, "cutoff_at": CUTOFF, field: value}
    with pytest.raises(InputRefusal):
        prepare_training([sample()], **options)


@pytest.mark.parametrize("change", [{"weights": (0.9,)}, {"exact_weights": (Fraction(1, 2),)},
    {"outcomes": (True,)}, {"outcome_mature_at": (CUTOFF,)}, {"input_sha256": "0" * 64},
    {"feature_names": ("wrong",)}, {"candidate_rows": 2}, {"max_weight_rounding_error": Fraction(1, 3)}])
def test_prepared_training_mutation_is_detected_before_native_import(change):
    training = prepare_training([sample()], with_context=False, cutoff_at=CUTOFF, fold=1)
    with pytest.raises(InputRefusal):
        validate_training(replace(training, **change))


@pytest.mark.parametrize("change", [{"weights_sha256": "x"}, {"outcome_order": []}, {"fold": True},
    {"supported_rows": 0}, {"weighted_stage_groups": 3}, {"event_count_unit": "independent_trades"},
    {"max_weight_rounding_error": [1, 3]}, {"mode": "unknown"}])
def test_saved_training_contract_rejects_changed_counts_modes_and_numeric_boundaries(change):
    value = training_contract(prepare_training([sample()], with_context=False, cutoff_at=CUTOFF, fold=1))
    with pytest.raises(InputRefusal):
        validate_training_contract({**value, **change})


def test_target_free_query_digest_and_frozen_training_detect_changes():
    queries = prepare_queries([sample(day=2).features], with_context=False, cutoff_at=CUTOFF, fold=1)
    with pytest.raises(InputRefusal, match="digest"):
        validate_queries(replace(queries, input_sha256="0" * 64))
    training = prepare_training([sample()], with_context=False, cutoff_at=CUTOFF, fold=1)
    with pytest.raises(FrozenInstanceError):
        training.weights = ()
    changed = replace(training.rows[0], static=(*training.rows[0].static[:-1], 9.0))
    with pytest.raises(InputRefusal, match="digest"):
        validate_training(replace(training, rows=(changed,)))


def test_saved_contract_refuses_equal_but_differently_typed_horizon_values():
    value = training_contract(prepare_training([sample()], with_context=False, cutoff_at=CUTOFF, fold=1))
    value["outcome_order"][0][1] = 5.0
    with pytest.raises(InputRefusal, match="metadata"):
        validate_training_contract(value)


def test_real_producer_episode_zero_flows_into_features_and_both_training_contracts():
    # Entirely invented candles; the deliberately supplied mature outcome tests
    # the input bridge, not the future-path label producer.
    bars = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
            (103, 116, 102, 114), (114, 116, 113, 115), (114, 119, 113, 118))
    minutes = {OPEN + offset * MINUTE: Minute("invented", OPEN + offset * MINUTE,
        OPEN + (offset + 3) * MINUTE, tuple(map(Decimal, bars[offset // 5])), Decimal(10)) for offset in range(30)}
    session = SessionInputs("invented", OPEN, OPEN + 390 * MINUTE, minutes)
    frame = next(scan_context_frames(session, through=OPEN + 32 * MINUTE))
    question = next(scan_context_questions((frame,), closing=session.closing)).questions[0]
    assert (question.stage, question.episode_id[1]) == ("breakout", 0)
    empirical = EmpiricalTrainingRow(question.question_id, "invented", OPEN.date().isoformat(), question.stage,
        question.episode_origin_context, question.path.start_at, TrainingOutcome(9, question.path.end_at + 2 * MINUTE))
    features = project_context_features(frame, question)
    baseline = fit_empirical([empirical], cutoff_at=CUTOFF)
    assert baseline.predict(stage="breakout", origin_context=question.episode_origin_context,
                            forecast_at=CUTOFF).status == "supported"
    for with_context in (False, True):
        prepared = prepare_training([ModelTrainingRow(empirical, features.numeric_view(with_context=with_context))],
            with_context=with_context, cutoff_at=CUTOFF, fold=1)
        assert prepared.rows[0].question_id == question.question_id and prepared.weights == (1.0,)
