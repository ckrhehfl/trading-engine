"""Synthetic checks for reassessing a saved entry with a different forecast."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_context_entry_values import (
    assess_entry_value, forecast_probability_boundary, reassess_saved_entry_value,
)
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES
from test_intraday_context_entry_values import fit, historical, query


def _vertex(index):
    return tuple(float(outcome == index) for outcome in range(len(OUTCOMES)))


def _forecast_query(base, key, probabilities):
    boundary = forecast_probability_boundary(probabilities)
    return replace(base, forecast_model_key=key, probabilities=boundary.probabilities, numeric_boundary=boundary)


@pytest.fixture
def model():
    weight = Fraction(1, 3)
    return fit(
        historical(outcome=0, gross=Fraction(2), gap=Fraction(1, 4), weight=weight),
        historical(outcome=1, episode=2, gross=Fraction(-1), weight=weight),
        historical(outcome=2, episode=3, filled=False, weight=weight),
    )


@pytest.mark.parametrize(("original", "alternate", "before", "after"), (
    (0, 1, "enter", "wait"),
    (1, 0, "wait", "enter"),
    (0, 2, "enter", "wait"),
))
def test_reassessment_matches_original_model_and_switches_actions(model, original, alternate, before, after):
    prior = assess_entry_value(model, _forecast_query(query(cost=Fraction(33)), "context_sequence", _vertex(original)))
    probabilities = _vertex(alternate)
    expected = assess_entry_value(model, _forecast_query(prior.query, "context_lightgbm", probabilities))

    actual = reassess_saved_entry_value(prior, probabilities)

    assert actual == expected
    assert prior.action == before and actual.action == after
    assert actual.training_support == prior.training_support
    assert actual.class_net_values == prior.class_net_values
    if alternate == 2:
        assert actual.expected_gross == actual.expected_cost == actual.expected_net == 0


def test_reassessment_renormalizes_all_mass_and_keeps_missing_payoff_unknown(model):
    base = query(cost=Fraction(33))
    prior = assess_entry_value(model, _forecast_query(base, "context_sequence", _vertex(0)))
    probabilities = (0.75,) + (0.0,) * (len(OUTCOMES) - 2) + (0.2500000001,)
    unsupported = reassess_saved_entry_value(prior, probabilities)
    expected_query = _forecast_query(base, "context_lightgbm", probabilities)

    assert unsupported == assess_entry_value(model, expected_query)
    assert unsupported.query.numeric_boundary.original_sum != 1
    assert unsupported.reason == "positive_mass_without_complete_payoff"
    assert unsupported.unsupported_probability_mass == expected_query.probabilities[-1] > 0
    assert unsupported.class_net_values[-1] is None
    assert unsupported.expected_net is None

    prior_abstention = assess_entry_value(model, _forecast_query(base, "context_sequence", probabilities))
    supported = reassess_saved_entry_value(prior_abstention, _vertex(0))
    assert supported == assess_entry_value(model, _forecast_query(base, "context_lightgbm", _vertex(0)))
    assert supported.unsupported_probability_mass == 0 and supported.action == "enter"


def test_reassessment_rechecks_transferred_notional_when_a_class_becomes_active():
    model = fit(historical(weight=Fraction(1, 2)),
                historical(outcome=1, episode=2, close=Fraction(120), gap=Fraction(-1), weight=Fraction(1, 2)))
    base = query(close=Fraction(1), cost=Fraction(33))
    prior = assess_entry_value(model, _forecast_query(base, "context_sequence", _vertex(0)))
    actual = reassess_saved_entry_value(prior, _vertex(1))

    assert actual == assess_entry_value(model, _forecast_query(base, "context_lightgbm", _vertex(1)))
    assert prior.action == "enter" and actual.action == "abstain"
    assert actual.reason == "nonpositive_transferred_entry_notional"
    assert actual.expected_cost is None and actual.unsupported_probability_mass == 0


@pytest.mark.parametrize("corruption", ("net", "class_values", "boundary_sum", "boundary_missing", "source_model"))
def test_reassessment_refuses_inconsistent_or_wrong_forecaster_prior(model, corruption):
    query_with_forecast = _forecast_query(query(cost=Fraction(33)), "context_sequence", _vertex(0))
    prior = assess_entry_value(model, query_with_forecast)
    if corruption == "net":
        prior = replace(prior, expected_net=prior.expected_net + 1)
    elif corruption == "class_values":
        prior = replace(prior, class_net_values=(None,) + prior.class_net_values[1:])
    elif corruption == "boundary_sum":
        boundary = replace(query_with_forecast.numeric_boundary, original_sum=Fraction(2))
        prior = replace(prior, query=replace(query_with_forecast, numeric_boundary=boundary))
    elif corruption == "boundary_missing":
        prior = assess_entry_value(model, replace(query_with_forecast, numeric_boundary=None))
    else:
        prior = assess_entry_value(model, replace(query_with_forecast, forecast_model_key="chart_sequence"))

    with pytest.raises(InputRefusal):
        reassess_saved_entry_value(prior, _vertex(1))
