"""Invented-only independent EI sequence replay and fail-closed identity checks."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from hashlib import sha256
from math import exp
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from research import intraday_context_model_inputs as common
from research import intraday_context_sequence as producer
from research import intraday_context_sequence_reference as reference
from research.intraday_context_empirical import EmpiricalTrainingRow
from research.intraday_context_features import NumericContextFeatures
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import TrainingOutcome
from research.intraday_scenario_labels import PathQuestion


OPEN = datetime(2031, 3, 4, tzinfo=UTC)
CUTOFF = OPEN + timedelta(days=10)


def _row(index, contextual=False):
    opening = OPEN + timedelta(days=index // 8)
    at = opening + timedelta(minutes=32)
    anchor = ResistanceAnchor(opening, Fraction(100), opening + timedelta(minutes=20),
        opening + timedelta(minutes=27), Fraction(116), Fraction(4), Fraction(16), Fraction(1))
    question = PathQuestion(at, at + timedelta(minutes=30), 116, 1, 124, 112)
    identity = ((f"INVENTED{index % 8}", anchor), 1), "approach", question.question_id
    context = "A" if index % 2 else "B"
    minute = tuple((float((index + step) % 3), 4.0, -2.0, float(step % 2), 1.0, 1.0, 1.0) for step in range(30))
    static = (0.0, 32.0, 0.25, 2.0, 0.5, 30.0, -0.5)
    if contextual:
        one_hot = (1.0, 0.0, 0.0) if context == "A" else (0.0, 1.0, 0.0)
        static += (*one_hot, *one_hot, 4.0, 0.25, 2.0, 1.0)
    features = NumericContextFeatures(identity, at, "chart_context" if contextual else "chart", minute, static)
    empirical = EmpiricalTrainingRow(identity, identity[0][0][0], opening.date().isoformat(),
        "approach", context, at, TrainingOutcome(index % 10, at + timedelta(minutes=32)), None)
    return common.ModelTrainingRow(empirical, features)


def _queries(contextual=False, count=3):
    return common.prepare_queries((_row(index, contextual).features for index in range(80, 80 + count)),
                                  with_context=contextual, cutoff_at=CUTOFF, fold=1)


@pytest.fixture(scope="module", params=(False, True), ids=("chart", "chart_context"))
def fitted(request):
    contextual = request.param
    prepared = common.prepare_training((_row(index, contextual) for index in range(24)),
                                       with_context=contextual, cutoff_at=CUTOFF, fold=1)
    return producer.fit_sequence(prepared), _queries(contextual)


def _reseal(artifact):
    artifact["artifact_sha256"] = sha256(common.encode({key: value for key, value in artifact.items()
                                                       if key != "artifact_sha256"})).hexdigest()


def _query_reseal(queries):
    return replace(queries, input_sha256=common._query_digest(queries))


def _saved(artifact, queries):
    return tuple((row.question_id, score) for row, score in
                 zip(queries.rows, producer.predict_sequence(artifact, queries), strict=True))


def test_reference_import_is_lazy_and_does_not_import_producer():
    python_root = Path(__file__).resolve().parents[1]
    code = (f"import sys; sys.path.insert(0, {str(python_root)!r}); "
            "from research import intraday_context_sequence_reference; "
            "assert 'numpy' not in sys.modules; assert 'lightgbm' not in sys.modules; "
            "assert 'research.intraday_context_sequence' not in sys.modules; "
            "assert 'research.intraday_scenario_sequence' not in sys.modules")
    subprocess.run([sys.executable, "-I", "-S", "-c", code], check=True, capture_output=True, timeout=15)


def test_independent_replay_agrees_and_proof_binds_every_query(fitted):
    artifact, queries = fitted
    expected = producer.predict_sequence(artifact, queries)
    actual = reference.reference_sequence_probabilities(artifact, queries)
    np.testing.assert_allclose(actual, expected, atol=reference.ABS_TOL, rtol=reference.REL_TOL)
    proof = reference.verify_sequence_probabilities(artifact, queries, _saved(artifact, queries))
    assert proof["verified"] and proof["rows"] == len(queries.rows)
    assert proof["maximum_absolute_difference"] < 1e-12
    assert proof["query_ids"] == tuple(row.question_id for row in queries.rows)
    assert proof["artifact_sha256"] == artifact["artifact_sha256"]
    assert proof["query_input_sha256"] == queries.input_sha256
    assert all(sum(row) == pytest.approx(1, abs=1e-12) and all(0 <= value <= 1 for value in row) for row in actual)


def test_producer_and_common_validator_mutations_cannot_control_reference(fitted, monkeypatch):
    artifact, queries = fitted
    saved = _saved(artifact, queries)

    def fail(*args, **kwargs):
        raise AssertionError("Producer helper must not execute")

    for name in ("predict_sequence", "_forward", "_tower", "_validate_artifact", "_architecture", "_shapes"):
        monkeypatch.setattr(producer, name, fail)
    for name in ("validate_queries", "validate_training_contract", "question_payload", "_query_digest", "encode"):
        monkeypatch.setattr(common, name, fail)
    assert reference.verify_sequence_probabilities(artifact, queries, saved)["verified"]


def test_hand_computed_single_channel_residual_readout(fitted):
    artifact, queries = fitted
    artifact = deepcopy(artifact)
    for name, value in artifact["parameters"].items():
        artifact["parameters"][name] = np.zeros_like(value).tolist()
    parameters = artifact["parameters"]
    parameters["minute_projection"][0][0] = 1.0
    for index in range(4):
        parameters[f"minute_kernel_{index}"][1][0][0] = 1.0
    parameters["head"][0][0] = 0.1
    parameters["bias"][1] = 0.2
    _reseal(artifact)
    row = replace(queries.rows[0], minute=((1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),) * 30,
                  static=(0.0,) * len(queries.rows[0].static))
    queries = _query_reseal(replace(queries, rows=(row,)))
    # One lagged addition at each dilation produces 2**4 at the final step:
    # the largest summed lag is 1+2+4+8=15, inside the 30-minute window.
    denominator = exp(1.6) + exp(0.2) + 8
    expected = (exp(1.6) / denominator, exp(0.2) / denominator, *((1 / denominator,) * 8))
    np.testing.assert_allclose(reference.reference_sequence_probabilities(artifact, queries)[0], expected,
                               atol=1e-15, rtol=1e-14)


def test_scalar_receptive_field_and_no_future_suffix_dependence(fitted):
    artifact, _ = fitted
    parameters = {key: np.array(value) for key, value in artifact["parameters"].items()}
    inputs = np.random.default_rng(137).normal(size=(1, 9, 7))
    state = [[max(0.0, sum(float(inputs[0, time, channel]) * parameters["minute_projection"][channel, output]
                            for channel in range(7))) for output in range(12)] for time in range(9)]
    for index, dilation in enumerate((1, 2, 4, 8)):
        kernel = parameters[f"minute_kernel_{index}"]
        state = [[max(0.0, state[time][output] + sum(state[time - tap * dilation][channel] * kernel[tap, channel, output]
                   for tap in range(3) if time >= tap * dilation for channel in range(12)))
                  for output in range(12)] for time in range(9)]
    actual = reference._causal_states(np, inputs, parameters)
    np.testing.assert_allclose(actual[0], state, atol=1e-13, rtol=1e-12)
    altered = inputs.copy()
    altered[:, 5:] += 1000
    changed = reference._causal_states(np, altered, parameters)
    np.testing.assert_array_equal(actual[:, :5], changed[:, :5])


@pytest.mark.parametrize("mutation", ["drop", "order", "duplicate", "identity", "time", "late_probability"])
def test_every_saved_row_and_order_is_checked_across_batch_boundary(fitted, mutation, monkeypatch):
    artifact, original = fitted
    queries = _queries(original.mode == "chart_context", 130)
    saved = list(_saved(artifact, queries))
    sizes = []
    causal = reference._causal_states

    def measured(np, minute, coefficients):
        sizes.append(len(minute))
        return causal(np, minute, coefficients)

    monkeypatch.setattr(reference, "_causal_states", measured)
    proof = reference.verify_sequence_probabilities(artifact, queries, tuple(saved))
    assert proof["rows"] == 130 and sizes == [128, 2]
    if mutation == "drop":
        saved.pop()
    elif mutation == "order":
        saved[128], saved[129] = saved[129], saved[128]
    elif mutation == "duplicate":
        saved[129] = saved[128]
    elif mutation in ("identity", "time"):
        identity, probabilities = saved[129]
        if mutation == "identity":
            identity = (identity[0][0], 9), identity[1], identity[2]
        else:
            question = identity[2]
            question = question[0] + timedelta(minutes=1), *question[1:]
            identity = *identity[:2], question
        saved[129] = identity, probabilities
    else:
        identity, probabilities = saved[129]
        saved[129] = identity, tuple(reversed(probabilities))
    with pytest.raises(InputRefusal):
        reference.verify_sequence_probabilities(artifact, queries, tuple(saved))


@pytest.mark.parametrize("mutation", ["shape", "nan", "negative", "too_large", "mass", "bool", "string", "huge_int"])
def test_saved_probability_values_refuse(fitted, mutation):
    artifact, queries = fitted
    saved = list(_saved(artifact, queries))
    values = list(saved[0][1])
    if mutation == "shape":
        values.pop()
    elif mutation == "mass":
        values = [value * 0.5 for value in values]
    else:
        values[0] = {"nan": float("nan"), "negative": -0.01, "too_large": 1.01,
                     "bool": True, "string": "0", "huge_int": 10**1000}[mutation]
    saved[0] = saved[0][0], tuple(values)
    with pytest.raises(InputRefusal):
        reference.verify_sequence_probabilities(artifact, queries, tuple(saved))


@pytest.mark.parametrize("mutation", ["schema", "extra", "architecture", "optimization", "outcome_order", "mode", "fold",
                                     "feature_order", "training_count", "training_hash", "coefficient_shape", "coefficient_bool",
                                     "coefficient_nan", "coefficient_huge", "digest", "resealed_overflow"])
def test_artifact_contract_is_independently_validated(fitted, mutation):
    original, queries = fitted
    artifact = deepcopy(original)
    if mutation == "schema":
        artifact["schema"] = "unexpected"
    elif mutation == "extra":
        artifact["labels"] = []
    elif mutation == "architecture":
        artifact["architecture"]["dilations"] = [1, 2, 4, 9]
    elif mutation == "optimization":
        artifact["optimization"]["epochs"] = 7
    elif mutation == "outcome_order":
        artifact["outcomes"].reverse()
    elif mutation == "mode":
        artifact["training"]["mode"] = "chart_context" if queries.mode == "chart" else "chart"
    elif mutation == "fold":
        artifact["training"]["fold"] = 2
    elif mutation == "feature_order":
        artifact["training"]["feature_names"].reverse()
    elif mutation == "training_count":
        artifact["training"]["supported_rows"] = 0
    elif mutation == "training_hash":
        artifact["training"]["roster_sha256"] = "z" * 64
    elif mutation == "coefficient_shape":
        artifact["parameters"]["head"].pop()
    elif mutation == "resealed_overflow":
        artifact["parameters"]["minute_projection"] = [[1e308] * 12 for _ in range(7)]
    else:
        artifact["parameters"]["bias"][0] = {"coefficient_bool": True, "coefficient_nan": float("nan"),
                                             "coefficient_huge": 10**1000, "digest": 0.123}[mutation]
    if mutation not in ("digest", "coefficient_nan", "coefficient_huge"):
        _reseal(artifact)
    with pytest.raises(InputRefusal):
        reference.reference_sequence_probabilities(artifact, queries)


@pytest.mark.parametrize("mutation", ["rows_order", "feature_order", "static", "shape", "nan", "time", "duplicate", "mode", "fold"])
def test_query_identity_shape_time_and_digest_are_independent(fitted, mutation):
    artifact, queries = fitted
    row = queries.rows[0]
    if mutation == "rows_order":
        queries = replace(queries, rows=tuple(reversed(queries.rows)))
    elif mutation == "feature_order":
        queries = replace(queries, feature_names=tuple(reversed(queries.feature_names)))
    elif mutation in ("static", "shape", "nan"):
        static = row.static[:-1] if mutation == "shape" else (*row.static[:-1], float("nan") if mutation == "nan" else 4.0)
        queries = replace(queries, rows=(replace(row, static=static), *queries.rows[1:]))
    elif mutation == "time":
        queries = replace(queries, rows=(replace(row, forecast_at=row.forecast_at - timedelta(minutes=1)),))
    elif mutation == "duplicate":
        queries = replace(queries, rows=(row, row))
    elif mutation == "mode":
        queries = _queries(queries.mode == "chart")
    else:
        queries = _query_reseal(replace(queries, fold=2))
    with pytest.raises(InputRefusal):
        reference.reference_sequence_probabilities(artifact, queries)


def test_pre_cutoff_resealed_queries_and_non_target_free_inputs_refuse(fitted):
    artifact, queries = fitted
    features = _row(0, queries.mode == "chart_context").features
    early = common._project(features, queries.mode)
    queries = _query_reseal(replace(queries, rows=(early,)))
    with pytest.raises(InputRefusal, match="before_cutoff"):
        reference.reference_sequence_probabilities(artifact, queries)
    with pytest.raises(InputRefusal, match="target_free"):
        reference.reference_sequence_probabilities(artifact, (early,))


def test_empty_queries_still_validate_artifact_and_runtime(fitted, monkeypatch):
    artifact, original = fitted
    queries = _queries(original.mode == "chart_context", 0)
    assert reference.reference_sequence_probabilities(artifact, queries) == ()
    proof = reference.verify_sequence_probabilities(artifact, queries, ())
    assert proof["verified"] and proof["rows"] == 0 and proof["maximum_absolute_difference"] == 0
    changed = deepcopy(artifact)
    changed["artifact_sha256"] = "0" * 64
    with pytest.raises(InputRefusal, match="artifact_digest"):
        reference.reference_sequence_probabilities(changed, queries)
    monkeypatch.setattr(np, "__version__", "0.0-invented")
    with pytest.raises(InputRefusal, match="pinned_numpy"):
        reference.reference_sequence_probabilities(artifact, queries)
