"""Invented checks for independent frozen EH sequence replay."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import exp

import numpy as np
import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome
from research.intraday_scenario_labels import PathQuestion
from research.intraday_scenario_sequence import SequenceInputs, SequenceTrainingRow, fit_sequence, predict_sequence
from research.intraday_scenario_sequence_reference import reference_sequence_probabilities, verify_sequence_probabilities


CUTOFF = datetime(2030, 4, 2, 0, 22, tzinfo=UTC)


def _identity(symbol, start, delay=0):
    question = PathQuestion(start, start + timedelta(minutes=30), 100, 1, 105, 95)
    return (symbol, *question.forecast_id(start + timedelta(minutes=delay)))


def _hand_example(with_peers):
    coefficients = {"minute_projection": np.zeros((7 if with_peers else 4, 12)),
                    "coarse_projection": np.zeros((4, 6)), "head": np.zeros((25, 10)), "bias": np.zeros(10)}
    coefficients.update({f"minute_kernel_{index}": np.zeros((3, 12, 12)) for index in range(4)})
    coefficients.update({f"coarse_kernel_{index}": np.zeros((3, 6, 6)) for index in range(2)})
    coefficients["minute_projection"][0, 0] = 1
    coefficients["minute_kernel_0"][1, 0, 0] = 2
    coefficients["minute_kernel_1"][1, 0, 0] = 3
    coefficients["coarse_projection"][3, 0] = 1
    coefficients["coarse_kernel_0"][2, 0, 0] = 4
    coefficients["coarse_kernel_1"][1, 0, 0] = -1
    coefficients["head"][[0, 12, 20], 0] = (0.25, 0.5, 1)
    coefficients["bias"][1] = -0.5
    peers = None
    if with_peers:
        coefficients["minute_projection"][4, 0] = 2
        peers = ((0.0, 0.0, 0.0),) * 19 + ((1.0, 0.0, 0.0),)
    minute = ((0.0, 0.0, 0.0, 0.0),) * 16 + tuple((float(value), 0.0, 0.0, 0.0) for value in range(1, 5))
    coarse = tuple((0.0, 0.0, 0.0, float(value)) for value in range(1, 5))
    row = SequenceInputs(_identity("INVENTED", CUTOFF), minute, coarse, (5, 5, 1, 0, 0, 0, 0), peers)
    artifact = {"schema": "eh-temporal-convolution-v1", "numpy": "2.5.2", "with_peers": with_peers, "fold_index": 1,
                "evaluation_starts_at": CUTOFF.isoformat(), "training_rows": 3, "training_events": 2,
                "outcomes": [list(value) for value in OUTCOMES],
                "parameters": {name: values.tolist() for name, values in coefficients.items()}}
    return artifact, row


@pytest.mark.parametrize("with_peers", [False, True])
def test_causal_residual_towers_and_softmax_agree_with_hand_arithmetic(with_peers):
    artifact, row = _hand_example(with_peers)
    fine = (4 + 2 * int(with_peers)) + 2 * 3 + 3 * (2 + 2 * 1)
    coarse = (4 + 4 * 2) - 2
    logits = (0.25 * fine + 0.5 * coarse + 1, -0.5, *([0] * 8))
    weights = tuple(exp(value) for value in logits)
    scores, = reference_sequence_probabilities(artifact, (row,))
    assert scores == pytest.approx(tuple(value / sum(weights) for value in weights), abs=1e-14, rel=1e-14)


def _random_case(index, day):
    rng = np.random.default_rng(1000 + index)
    minute = tuple(map(tuple, rng.normal(size=(20, 4)).tolist()))
    coarse = tuple(map(tuple, rng.normal(size=(4, 4)).tolist()))
    peers = tuple(map(tuple, rng.uniform(-1, 1, size=(20, 3)).tolist()))
    return SequenceInputs(_identity(f"INVENTED-{index}", day), minute, coarse, (1, 2, 1, 0, 0, 0.2, index % 2), peers)


@pytest.mark.parametrize("with_peers", [False, True])
def test_independent_replay_checks_every_trained_row_including_later_batches(with_peers):
    training_at = CUTOFF - timedelta(days=1)
    rows = tuple(SequenceTrainingRow(_random_case(index, training_at),
                                     TrainingOutcome(index % 10, training_at + timedelta(minutes=20)))
                 for index in range(48))
    artifact = fit_sequence(rows, evaluation_starts_at=CUTOFF, fold_index=1, with_peers=with_peers)
    queries = tuple(_random_case(index, CUTOFF) for index in range(133))
    scores = predict_sequence(artifact, queries)
    saved = tuple((query.forecast_id, value) for query, value in zip(queries, scores, strict=True))
    report = verify_sequence_probabilities(artifact, queries, saved)
    assert report["verified"] and report["rows"] == 133 and report["maximum_absolute_difference"] < 1e-12

    last = list(scores[-1])
    source = max(range(10), key=last.__getitem__)
    destination = (source + 1) % 10
    last[source] -= 0.01
    last[destination] += 0.01
    assert sum(last) == pytest.approx(1)
    with pytest.raises(InputRefusal, match="probability_differs"):
        verify_sequence_probabilities(artifact, queries, (*saved[:-1], (queries[-1].forecast_id, tuple(last))))


def test_verifier_rejects_wrong_clock_order_duplicates_and_wrong_count():
    artifact, first = _hand_example(False)
    second = replace(first, forecast_id=_identity("INVENTED", CUTOFF, delay=5))
    queries = (first, second)
    values = reference_sequence_probabilities(artifact, queries)
    saved = tuple((row.forecast_id, scores) for row, scores in zip(queries, values, strict=True))
    with pytest.raises(InputRefusal, match="coordinate_differs"):
        verify_sequence_probabilities(artifact, queries, saved[::-1])
    with pytest.raises(InputRefusal, match="count_differs"):
        verify_sequence_probabilities(artifact, queries, saved[:1])
    with pytest.raises(InputRefusal, match="before_split_or_duplicate"):
        reference_sequence_probabilities(artifact, (first, first))
    with pytest.raises(InputRefusal, match="before_split_or_duplicate"):
        reference_sequence_probabilities(artifact, (replace(first, forecast_id=_identity("OLD", CUTOFF - timedelta(days=1))),))
    with pytest.raises(InputRefusal, match="query_clock"):
        reference_sequence_probabilities(artifact, (replace(first, forecast_id=_identity("OFF-GRID", CUTOFF, delay=1)),))


def test_replay_rejects_corrupt_coefficients_inputs_and_saved_probability_range():
    artifact, row = _hand_example(True)
    score, = reference_sequence_probabilities(artifact, (row,))
    for invalid in ("0", True, float("nan"), 10 ** 400):
        corrupt = deepcopy(artifact)
        corrupt["parameters"]["head"][0][0] = invalid
        with pytest.raises(InputRefusal, match="coefficients"):
            reference_sequence_probabilities(corrupt, (row,))
    corrupt = deepcopy(artifact)
    corrupt["parameters"]["minute_kernel_0"].pop()
    with pytest.raises(InputRefusal, match="coefficients"):
        reference_sequence_probabilities(corrupt, (row,))
    with pytest.raises(InputRefusal, match="input_shape_or_values"):
        reference_sequence_probabilities(artifact, (replace(row, coarse=row.coarse[:-1]),))
    with pytest.raises(InputRefusal, match="peer_support_required"):
        reference_sequence_probabilities(artifact, (replace(row, peer=None),))
    with pytest.raises(InputRefusal, match="peer_support_required"):
        reference_sequence_probabilities(artifact, (replace(row, peer=((0.0, 0.0, 2.0),) * 20),))
    with pytest.raises(InputRefusal, match="saved_values"):
        verify_sequence_probabilities(artifact, (row,), ((row.forecast_id, (True, *score[1:])),))
    for invalid in ((0.0,) * 10, (1.0 + 1e-6, -1e-6, *([0.0] * 8))):
        with pytest.raises(InputRefusal, match="saved_mass"):
            verify_sequence_probabilities(artifact, (row,), ((row.forecast_id, invalid),))
