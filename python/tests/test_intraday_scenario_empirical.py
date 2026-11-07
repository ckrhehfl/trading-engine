"""Invented empirical EH cases with strict date and label clocks."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal, MINUTE
from research.intraday_scenario_decision_inputs import ChartDecisionInputs
from research.intraday_scenario_empirical import EmpiricalPathModel, HistoricalDecision
from research.intraday_scenario_forecast_targets import TrainingOutcome
from research.intraday_scenario_labels import PathQuestion


START = datetime(2030, 1, 2, 0, 22, tzinfo=UTC)
CUTOFF = START + timedelta(days=40)


def chart(day, symbol, *, retested=False):
    question = PathQuestion(day, day + 30 * MINUTE, 100, 1, 105, 95)
    return ChartDecisionInputs((symbol, *question.forecast_id(day)), "ready",
                               minutes=((Fraction(0), Fraction(1), Fraction(-1), Fraction(0)),) * 20,
                               state=tuple(map(Fraction, (5, 5, 1, int(retested), int(retested), 0))))


def training(day, symbol, outcome, mature=None):
    return HistoricalDecision("approach", chart(day, symbol), TrainingOutcome(outcome, mature or day + 3 * MINUTE))


def test_empirical_counts_use_at_most_two_cases_per_day_in_a_matched_state():
    samples = tuple(training(START + timedelta(days=offset), symbol, label)
                    for offset in range(30) for symbol, label in (("A", 0), ("B", 1), ("C", 2)))
    model = EmpiricalPathModel(samples, evaluation_starts_at=CUTOFF)
    prediction = model.predict("approach", chart(CUTOFF, "A"))
    assert prediction.neighbors == 60 and prediction.dates == 30
    assert prediction.weights == (0.5, 0.5, 0, 0, 0, 0, 0, 0, 0, 0)
    assert model.predict("breakout", chart(CUTOFF, "A")).weights is None
    assert model.predict("approach", chart(CUTOFF, "A", retested=True)).weights is None


def test_immature_labels_and_earlier_or_duplicate_coordinates_cannot_support_a_prediction():
    samples = tuple(training(START + timedelta(days=offset), "A", 0) for offset in range(29))
    immature = training(START + timedelta(days=29), "A", 1, mature=CUTOFF)
    model = EmpiricalPathModel((*samples, immature), evaluation_starts_at=CUTOFF)
    prediction = model.predict("approach", chart(CUTOFF, "A"))
    assert prediction.dates == 29 and prediction.weights is None
    with pytest.raises(InputRefusal, match="prediction_precedes_evaluation"):
        model.predict("approach", chart(START, "A"))
    with pytest.raises(InputRefusal, match="coordinate_after_split_or_duplicate"):
        EmpiricalPathModel((*samples, samples[0]), evaluation_starts_at=CUTOFF)
    with pytest.raises(InputRefusal, match="coordinate_after_split_or_duplicate"):
        EmpiricalPathModel((*samples, training(CUTOFF, "A", 1)), evaluation_starts_at=CUTOFF)
