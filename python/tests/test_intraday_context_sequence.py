"""Invented-only EI sequence fitting, gradients and target-free replay."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from research import intraday_context_model_inputs as common
from research import intraday_context_sequence as model
from research import intraday_scenario_sequence as eh
from research.intraday_context_empirical import EmpiricalTrainingRow
from research.intraday_context_features import NumericContextFeatures
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome
from research.intraday_scenario_labels import PathQuestion


OPEN = datetime(2031, 2, 12, tzinfo=UTC)
CUTOFF = OPEN + timedelta(days=10)


def _row(index, *, with_context=False, outcome=True):
    opening = OPEN + timedelta(days=index // 8)
    at = opening + timedelta(minutes=32)
    sign = 1 if index % 2 == 0 else -1
    anchor = ResistanceAnchor(opening, Fraction(100), opening + timedelta(minutes=20),
        opening + timedelta(minutes=27), Fraction(116), Fraction(4), Fraction(16), Fraction(1))
    question = PathQuestion(at, at + timedelta(minutes=30), 116, 1, 124, 112)
    identity = ((f"INVENTED{index % 8}", anchor), 1), "approach", question.question_id
    minute = []
    for offset in range(30):
        value = float(sign if offset == 24 else -sign if offset == 25 else 0)
        minute.append((value, value + 1, value - 1, value, 1.0, 1.0, 1.0))
    static = (0.0, 32.0, 0.25, 2.0, 0.5, 30.0, -0.5)
    context = "A" if sign > 0 else "B"
    if with_context:
        one_hot = (1.0, 0.0, 0.0) if context == "A" else (0.0, 1.0, 0.0)
        static += (*one_hot, *one_hot, 4.0, 0.25, 2.0, 1.0)
    features = NumericContextFeatures(identity, at, "chart_context" if with_context else "chart", tuple(minute), static)
    label = TrainingOutcome(0 if sign > 0 else 4, at + timedelta(minutes=32)) if outcome else None
    empirical = EmpiricalTrainingRow(identity, identity[0][0][0], opening.date().isoformat(),
        "approach", context, at, label, None if outcome else "invented_missing_price")
    return common.ModelTrainingRow(empirical, features)


def _training(*, with_context=False, fold=1):
    return common.prepare_training((_row(index, with_context=with_context) for index in range(24)),
                                   with_context=with_context, cutoff_at=CUTOFF, fold=fold)


def _queries(*, with_context=False, fold=1):
    return common.prepare_queries((_row(index, with_context=with_context).features for index in (80, 81)),
                                  with_context=with_context, cutoff_at=CUTOFF, fold=fold)


@pytest.fixture(scope="module")
def fitted():
    return model.fit_sequence(_training())


def _reseal(artifact):
    artifact["artifact_sha256"] = sha256(common.encode({key: value for key, value in artifact.items()
                                                       if key != "artifact_sha256"})).hexdigest()


def test_import_is_lazy_and_preserves_all_eh_configuration():
    python_root = Path(__file__).resolve().parents[1]
    code = (f"import sys; sys.path.insert(0, {str(python_root)!r}); "
            "from research import intraday_context_sequence; "
            "assert 'numpy' not in sys.modules; assert 'lightgbm' not in sys.modules")
    subprocess.run([sys.executable, "-I", "-S", "-c", code], check=True, capture_output=True, timeout=15)
    assert (eh.SCHEMA, eh.SEED, eh.MINUTE_WIDTH, eh.COARSE_WIDTH) == ("eh-temporal-convolution-v1", 20261107, 12, 6)
    assert model._tower is eh._tower and model._tower_backward is eh._tower_backward


def test_fixed_single_tower_and_common_initial_weights():
    plain, context = _training(), _training(with_context=True)
    chart_batch = model._batch(np, plain.rows[:2], "chart")
    context_batch = model._batch(np, context.rows[:2], "chart_context")
    np.testing.assert_array_equal(chart_batch[0], context_batch[0])
    np.testing.assert_array_equal(chart_batch[1], context_batch[1][:, :7])
    chart_parameters = model._initialize(np, "chart", np.random.default_rng(model.SEED + 1))
    context_parameters = model._initialize(np, "chart_context", np.random.default_rng(model.SEED + 1))
    assert set(chart_parameters) == {"minute_projection", *(f"minute_kernel_{index}" for index in range(4)), "head", "bias"}
    assert chart_parameters["minute_projection"].shape == (7, 12)
    assert chart_parameters["head"].shape == (19, 10) and context_parameters["head"].shape == (29, 10)
    for name in chart_parameters:
        np.testing.assert_array_equal(chart_parameters[name],
            context_parameters[name][:19] if name == "head" else context_parameters[name])
    assert np.count_nonzero(context_parameters["head"][19:]) == 0
    expected, *_ = model._forward(np, chart_batch, chart_parameters)
    for values in (context_batch[1], np.concatenate((chart_batch[1], np.zeros((2, 10))), axis=1)):
        actual, *_ = model._forward(np, (context_batch[0], values), context_parameters)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-14)


def test_single_tower_is_causal_and_weighted_gradients_match_finite_differences():
    rng = np.random.default_rng(735)
    parameters = model._initialize(np, "chart_context", rng)
    batch = (rng.normal(size=(3, 30, 7)), rng.normal(size=(3, 17)))
    states = model._tower(np, batch[0], parameters, model._NAMES, model.DILATIONS)
    altered = batch[0].copy()
    altered[:, 15:] += 1000
    altered_states = model._tower(np, altered, parameters, model._NAMES, model.DILATIONS)
    for before, after in zip(states, altered_states, strict=True):
        np.testing.assert_array_equal(before[:, :15], after[:, :15])
    targets, weights = np.array([0, 4, 9]), np.array([0.125, 0.5, 0.375])
    gradients = model._gradients(np, batch, parameters, targets, weights)

    def loss():
        scores, *_ = model._forward(np, batch, parameters)
        return -sum(weights[index] * np.log(scores[index, outcome]) for index, outcome in enumerate(targets))

    for name, parameter in parameters.items():
        index = np.unravel_index(np.abs(gradients[name]).argmax(), parameter.shape)
        original = parameter[index]
        try:
            parameter[index] = original + 1e-6
            upper = loss()
            parameter[index] = original - 1e-6
            lower = loss()
        finally:
            parameter[index] = original
        assert gradients[name][index] == pytest.approx((upper - lower) / 2e-6, rel=1e-5, abs=1e-7)


def test_deterministic_seed_roundtrip_and_training_identity(fitted):
    reverse = common.prepare_training((_row(index) for index in reversed(range(24))),
        with_context=False, cutoff_at=CUTOFF, fold=1)
    assert model.fit_sequence(reverse) == fitted
    different_fold = model.fit_sequence(_training(fold=2))
    assert different_fold["parameters"] != fitted["parameters"]
    assert fitted["training"] == common.training_contract(_training())
    assert fitted["optimization"]["parameter_seed"] == 20261109
    assert fitted["optimization"]["shuffle_seed"] == 20261119
    assert fitted["optimization"]["epochs"] == 6 and fitted["optimization"]["batch_size"] == 64
    restored = json.loads(json.dumps(fitted, allow_nan=False))
    expected = model.predict_sequence(fitted, _queries())
    assert model.predict_sequence(restored, _queries()) == expected
    assert len(expected) == 2 and all(len(row) == len(OUTCOMES) for row in expected)
    assert all(sum(row) == pytest.approx(1, abs=1e-14) and all(0 <= value <= 1 for value in row) for row in expected)
    assert model.predict_sequence(fitted, common.prepare_queries((), with_context=False, cutoff_at=CUTOFF, fold=1)) == ()


def test_context_fit_uses_same_roster_outcomes_weights_and_target_free_queries(fitted):
    contextual = model.fit_sequence(_training(with_context=True))
    for key in ("roster_sha256", "weights_sha256", "outcomes_sha256", "event_count", "date_count"):
        assert contextual["training"][key] == fitted["training"][key]
    assert contextual["architecture"]["static_width"] == 17
    assert contextual["training"]["input_sha256"] != fitted["training"]["input_sha256"]
    scores = model.predict_sequence(contextual, _queries(with_context=True))
    assert all(sum(row) == pytest.approx(1) for row in scores)
    with pytest.raises(InputRefusal, match="target_free_queries"):
        model.predict_sequence(fitted, _training())
    with pytest.raises(InputRefusal, match="query_training_identity"):
        model.predict_sequence(fitted, _queries(with_context=True))


def test_fit_uses_common_rounded_weights_with_global_mean_normalization(monkeypatch):
    first = _row(0)
    repeated = []
    for episode in range(1, 4):
        identity = (first.empirical.question_id[0][0], episode), *first.empirical.question_id[1:]
        repeated.append(replace(first, empirical=replace(first.empirical, question_id=identity),
                                features=replace(first.features, question_id=identity)))
    prepared = common.prepare_training((*repeated, _row(1)), with_context=False, cutoff_at=CUTOFF, fold=1)
    assert prepared.exact_weights.count(Fraction(1, 3)) == 3
    assert prepared.max_weight_rounding_error > 0
    received = []
    original = model._gradients

    def gradients(np, batch, parameters, targets, weights):
        received.append(tuple(weights.tolist()))
        return original(np, batch, parameters, targets, weights)

    monkeypatch.setattr(model, "_gradients", gradients)
    model.fit_sequence(prepared)
    expected = sorted(value / sum(prepared.weights) for value in prepared.weights)
    assert len(received) == 6
    for weights in received:
        assert sorted(weights) == expected
        assert sum(weights) == pytest.approx(1, abs=1e-15)


def test_each_epoch_keeps_a_short_final_batch(monkeypatch):
    prepared = common.prepare_training((_row(index) for index in range(70)),
        with_context=False, cutoff_at=CUTOFF, fold=1)
    sizes = []
    original = model._batch

    def batch(np, rows, mode):
        sizes.append(len(rows))
        return original(np, rows, mode)

    monkeypatch.setattr(model, "_batch", batch)
    model.fit_sequence(prepared)
    assert sizes == [64, 6] * 6


def test_future_feature_and_outcome_changes_cannot_change_fit(fitted):
    training = _training()
    future = _row(80)
    changed = replace(future, empirical=replace(future.empirical, outcome=TrainingOutcome(9, CUTOFF + timedelta(days=1))),
                      features=replace(future.features, minute=tuple((0.0, 9.0, -9.0, 0.0, 50.0, 1.0, 1.0) for _ in range(30))))
    first = common.prepare_queries((future.features,), with_context=False, cutoff_at=CUTOFF, fold=1)
    second = common.prepare_queries((changed.features,), with_context=False, cutoff_at=CUTOFF, fold=1)
    assert first.input_sha256 != second.input_sha256
    model.predict_sequence(fitted, first)
    model.predict_sequence(fitted, second)
    assert model.fit_sequence(training) == fitted
    for value in (future, changed):
        with pytest.raises(InputRefusal, match="training_clock_or_day"):
            common.prepare_training((*(_row(index) for index in range(24)), value),
                                    with_context=False, cutoff_at=CUTOFF, fold=1)


@pytest.mark.parametrize("mutation", ["before_cutoff", "feature_order", "fold", "changed_values", "shape", "nonfinite"])
def test_query_time_shape_mode_and_feature_identity_are_enforced(fitted, mutation):
    queries = _queries()
    if mutation == "before_cutoff":
        queries = replace(queries, rows=(_training().rows[0],))
    elif mutation == "feature_order":
        queries = replace(queries, feature_names=tuple(reversed(queries.feature_names)))
    elif mutation == "fold":
        queries = _queries(fold=2)
    else:
        row = queries.rows[0]
        static = row.static[:-1] if mutation == "shape" else (
            *row.static[:-1], float("nan") if mutation == "nonfinite" else row.static[-1] + 1)
        queries = replace(queries, rows=(replace(row, static=static),))
    with pytest.raises(InputRefusal):
        model.predict_sequence(fitted, queries)


@pytest.mark.parametrize("mutation", ["schema", "extra", "architecture", "optimization", "training", "outcomes",
                                     "bias_bool", "bias_string", "bias_nan", "bias_huge_int", "parameter_shape", "digest"])
def test_roundtrip_artifact_tampering_is_rejected(fitted, mutation):
    artifact = deepcopy(fitted)
    if mutation in ("schema", "digest"):
        artifact["schema" if mutation == "schema" else "artifact_sha256"] = "changed"
    elif mutation == "extra":
        artifact["evaluation_labels"] = []
    elif mutation == "architecture":
        artifact["architecture"]["dilations"] = [1, 2, 4, 9]
    elif mutation == "optimization":
        artifact["optimization"]["epochs"] = 7
    elif mutation == "training":
        artifact["training"]["feature_names"].reverse()
    elif mutation == "outcomes":
        artifact["outcomes"].reverse()
    elif mutation == "parameter_shape":
        artifact["parameters"]["minute_projection"] = []
    else:
        artifact["parameters"]["bias"][0] = {"bias_bool": True, "bias_string": "0",
            "bias_nan": float("nan"), "bias_huge_int": 10**1000}[mutation]
    with pytest.raises(InputRefusal):
        model.predict_sequence(artifact, _queries())


def test_changed_finite_coefficients_need_a_new_digest_and_overflow_still_refuses(fitted):
    changed = deepcopy(fitted)
    changed["parameters"]["bias"][0] += 1
    with pytest.raises(InputRefusal, match="artifact_digest"):
        model.predict_sequence(changed, _queries())
    changed["parameters"]["minute_projection"] = [[1e308] * 12 for _ in range(7)]
    _reseal(changed)
    with pytest.raises(InputRefusal, match="nonfinite"):
        model.predict_sequence(changed, _queries())


def test_training_empty_support_changed_weights_and_unmature_labels_refuse():
    empty = common.prepare_training((), with_context=False, cutoff_at=CUTOFF, fold=1)
    with pytest.raises(InputRefusal, match="nonempty_EI_sequence"):
        model.fit_sequence(empty)
    prepared = _training()
    for changed in (replace(prepared, weights=(0.5, *prepared.weights[1:])),
                    replace(prepared, outcome_mature_at=(CUTOFF, *prepared.outcome_mature_at[1:])),
                    replace(prepared, feature_names=tuple(reversed(prepared.feature_names)))):
        with pytest.raises(InputRefusal):
            model.fit_sequence(changed)


def test_unpinned_numpy_refuses(monkeypatch):
    monkeypatch.setattr(np, "__version__", "0.0-invented")
    with pytest.raises(InputRefusal, match="pinned_EI_sequence_numpy"):
        model.fit_sequence(_training())
