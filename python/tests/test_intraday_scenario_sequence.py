"""Invented EH sequence kernels, fit and frozen replay checks."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
import json

import numpy as np
import pytest

from research import intraday_scenario_sequence as model
from research.intraday_ed_inputs import InputRefusal, MINUTE
from research.intraday_scenario_decision_inputs import ChartDecisionInputs, PeerDecisionInputs
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome, horizon_probabilities
from research.intraday_scenario_labels import PathQuestion


START = datetime(2030, 1, 2, 0, 22, tzinfo=UTC)
CUTOFF = START + timedelta(days=40)
POSITIVE = OUTCOMES.index(("P1", 5))
NEGATIVE = OUTCOMES.index(("P3", 15))


def _case(day, symbol, sign, *, update=False, peer_signal=False):
    question = PathQuestion(day, day + 30 * MINUTE, 100, 1, 105, 95)
    at = day + 5 * MINUTE if update else day
    minute = []
    for index in range(20):
        price = 0
        if not peer_signal:
            if index == 17:
                price = 3 * sign
            if index == 18:
                price = -3 * sign
        minute.append(tuple(map(Fraction, (price, price + 1, price - 1, price))))
    coarse = []
    for start in range(0, 20, 5):
        bars = minute[start:start + 5]
        coarse.append((bars[0][0], max(bar[1] for bar in bars), min(bar[2] for bar in bars), bars[-1][3]))
    chart = ChartDecisionInputs((symbol, *question.forecast_id(at)), "ready", tuple(minute), tuple(coarse),
                                tuple(map(Fraction, (5, 5, 1, 0, 0, 0))))
    peers = tuple(f"PEER{index}" for index in range(5))
    peer_path = tuple((Fraction(0), Fraction(sign, 20), Fraction(1 + sign, 2)) for _ in range(20))
    peer = PeerDecisionInputs(symbol, at, peers, peers, peer_path, ())
    return chart, peer


def _row(index, *, peer_signal=False):
    day = START + timedelta(days=index // 8)
    sign = 1 if index % 2 == 0 else -1
    chart, peer = _case(day, f"SYMBOL{index % 8}", sign, peer_signal=peer_signal)
    outcome = TrainingOutcome(POSITIVE if sign > 0 else NEGATIVE, day + 9 * MINUTE)
    return model.SequenceTrainingRow(model.sequence_inputs(chart, "approach", peer), outcome)


def test_causal_convolution_agrees_with_explicit_indexing_and_ignores_later_values():
    sequence = np.arange(2 * 7 * 3, dtype=np.float64).reshape(2, 7, 3)
    kernel = np.arange(3 * 3 * 2, dtype=np.float64).reshape(3, 3, 2)
    dilation = 2
    expected = np.zeros((2, 7, 2))
    for batch in range(2):
        for time in range(7):
            for output in range(2):
                for lag in range(3):
                    if time >= lag * dilation:
                        for channel in range(3):
                            expected[batch, time, output] += (
                                sequence[batch, time - lag * dilation, channel] * kernel[lag, channel, output]
                            )
    np.testing.assert_array_equal(model._causal_convolution(sequence, kernel, dilation), expected)
    sequence[:, 4:] *= -10
    np.testing.assert_array_equal(model._causal_convolution(sequence, kernel, dilation)[:, :4], expected[:, :4])


def test_weighted_loss_gradients_match_finite_differences_through_both_towers():
    rng = np.random.default_rng(20261107)
    parameters = model._initialize(np, True, rng)
    batch = (rng.normal(size=(3, 20, 7)), rng.normal(size=(3, 4, 4)), rng.normal(size=(3, 7)))
    targets = np.array([0, 4, 9])
    weights = np.array([0.125, 0.5, 0.375])
    gradients = model._gradients(np, batch, parameters, targets, weights)

    def loss():
        scores, *_ = model._forward(np, batch, parameters)
        return -sum(weights[index] * np.log(scores[index, outcome]) for index, outcome in enumerate(targets))

    for name, parameter in parameters.items():
        index = np.unravel_index(np.abs(gradients[name]).argmax(), parameter.shape)
        original = parameter[index]
        parameter[index] = original + 1e-6
        upper = loss()
        parameter[index] = original - 1e-6
        lower = loss()
        parameter[index] = original
        assert gradients[name][index] == pytest.approx((upper - lower) / 2e-6, rel=1e-5, abs=1e-7)


def test_matched_peer_arm_preserves_chart_values_and_initial_common_parameters():
    chart, peer = _case(CUTOFF, "TARGET", 1)
    plain = model.sequence_inputs(chart, "breakout")
    matched = model.sequence_inputs(chart, "breakout", peer)
    assert replace(matched, peer=None) == plain
    chart_batch = model._batch(np, (matched,), False)
    peer_batch = model._batch(np, (matched,), True)
    np.testing.assert_array_equal(chart_batch[0], peer_batch[0][:, :, :4])
    np.testing.assert_array_equal(chart_batch[1], peer_batch[1])
    np.testing.assert_array_equal(chart_batch[2], peer_batch[2])

    chart_parameters = model._initialize(np, False, np.random.default_rng(123))
    peer_parameters = model._initialize(np, True, np.random.default_rng(123))
    expected, *_ = model._forward(np, chart_batch, chart_parameters)
    actual, *_ = model._forward(np, peer_batch, peer_parameters)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-14)
    with pytest.raises(InputRefusal, match="matched_EH_peer_sequence_required"):
        model.sequence_inputs(chart, "breakout", replace(peer, decision_at=CUTOFF + MINUTE))
    with pytest.raises(InputRefusal, match="matched_EH_peer_sequence_required"):
        model.sequence_inputs(chart, "breakout", replace(peer, sequence=None))


def test_fit_uses_event_weights_and_refuses_unavailable_labels_or_duplicate_coordinates():
    first = _row(0)
    other = _row(1)
    updated, peer = _case(START, "SYMBOL0", 1, update=True)
    second = model.SequenceTrainingRow(model.sequence_inputs(updated, "approach", peer), first.outcome)
    rows, weights, events = model._training_rows((second, other, first), CUTOFF)
    assert rows == (first, second, other) and weights == (0.5, 0.5, 1) and events == 2
    for rejected in ((first, first), (replace(first, outcome=replace(first.outcome, mature_at=CUTOFF)),)):
        with pytest.raises(InputRefusal, match="training_maturity_split_or_duplicate"):
            model.fit_sequence(rejected, evaluation_starts_at=CUTOFF, fold_index=1)


def test_chart_fit_learns_temporal_order_and_replays_identical_numeric_coefficients():
    rows = tuple(_row(index) for index in range(192))
    model_a = model.fit_sequence(rows, evaluation_starts_at=CUTOFF, fold_index=1)
    model_b = model.fit_sequence(tuple(reversed(rows)), evaluation_starts_at=CUTOFF, fold_index=1)
    assert model_a == model_b and model_a["training_events"] == 192
    packed = tuple(replace(row, inputs=model.pack_training_inputs(row.inputs)) for row in rows)
    assert model.fit_sequence(packed, evaluation_starts_at=CUTOFF, fold_index=1) == model_a
    plus, _ = _case(CUTOFF, "TARGET", 1)
    minus, _ = _case(CUTOFF, "TARGET", -1)
    queries = (model.sequence_inputs(plus, "approach"), model.sequence_inputs(minus, "approach"))
    assert queries[0].coarse == queries[1].coarse and queries[0].minute[-1] == queries[1].minute[-1]
    restored = json.loads(json.dumps(model_a, allow_nan=False))
    scores = model.predict_sequence(restored, queries)
    assert scores == model.predict_sequence(model_a, queries)
    assert scores[0][POSITIVE] > 0.65 and scores[1][NEGATIVE] > 0.65
    assert all(sum(values) == pytest.approx(1) for values in scores)
    assert all(len(horizon_probabilities(values)) == 3 for values in scores)
    with pytest.raises(InputRefusal, match="target_free_EH_queries_required"):
        model.predict_sequence(restored, rows[:1])
    with pytest.raises(InputRefusal, match="query_precedes_evaluation_split"):
        model.predict_sequence(restored, (rows[0].inputs,))
    for malformed in ("0", True, float("nan")):
        bad = {**restored, "parameters": {**restored["parameters"], "bias": [malformed] * len(OUTCOMES)}}
        with pytest.raises(InputRefusal, match="invalid_EH_sequence_coefficients"):
            model.predict_sequence(bad, queries)


def test_peer_fit_learns_increment_on_exactly_the_same_chart_inputs_and_outcomes():
    rows = tuple(_row(index, peer_signal=True) for index in range(192))
    chart_artifact = model.fit_sequence(rows, evaluation_starts_at=CUTOFF, fold_index=1)
    peer_artifact = model.fit_sequence(rows, evaluation_starts_at=CUTOFF, fold_index=1, with_peers=True)
    plus_chart, plus_peer = _case(CUTOFF, "TARGET", 1, peer_signal=True)
    minus_chart, minus_peer = _case(CUTOFF, "TARGET", -1, peer_signal=True)
    queries = (model.sequence_inputs(plus_chart, "approach", plus_peer),
               model.sequence_inputs(minus_chart, "approach", minus_peer))
    plain = model.predict_sequence(chart_artifact, queries)
    paired = model.predict_sequence(peer_artifact, queries)
    assert plain[0] == plain[1]
    assert paired[0][POSITIVE] > 0.65 and paired[1][NEGATIVE] > 0.65
    with pytest.raises(InputRefusal, match="common_EH_peer_support_required"):
        model.predict_sequence(peer_artifact, (replace(queries[0], peer=None),))
