"""Invented comparisons of causal decision fields, without any exit ledger."""

from dataclasses import asdict, replace
from fractions import Fraction

import pytest

from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_economic_fold import _plain
from research.intraday_context_entry_compare import _static_outcome
from research.intraday_context_saved_fixed_exit import prepare_saved_fixed_exit
from research.intraday_context_saved_fixed_exit_causal_reference import reference_fixed_exit_fields
from test_intraday_context_entry_compare import data, prepare, value_model
from test_intraday_context_saved_fixed_exit import alternate_forecast, first_decision, mixed_model


def _producer_fields(original, forecast, pin):
    decision = prepare_saved_fixed_exit(original, forecast, forecast_sha256=pin)
    baseline = _static_outcome(original.opportunity, original.baseline, original.baseline_unavailable, "baseline")
    value, static = decision.alternate_value, decision.static_outcome
    return {"static": None if static is None else [static.status, static.reason],
            "baseline_static": None if baseline is None else [baseline.status, baseline.reason],
            "expected_net": None if value is None or value.expected_net is None else str(value.expected_net),
            "request_sha256": None if static is not None else
                wire.digest(wire.encode(_plain(asdict(decision.alternate_step))))}


@pytest.mark.parametrize("gain_first", (True, False))
@pytest.mark.parametrize("affordable", (True, False))
def test_reconstructs_switches_and_rejects_unaffordable_requests(data, gain_first, affordable):
    _, stored, fold, _ = data
    plan, _ = prepare(data, model=mixed_model(fold, gain_first=gain_first),
                      risk_budget=Fraction(100) if affordable else Fraction(1, 1000))
    original = first_decision(plan)
    forecast = alternate_forecast(stored, original)
    pin = stored.seal.sha256
    fields = reference_fixed_exit_fields(original, forecast, forecast_sha256=pin)
    assert fields == _producer_fields(original, forecast, pin)
    if gain_first and affordable:
        assert original.selected.step.action == "wait" and fields["request_sha256"]
        assert fields["static"] is None
    else:
        reason = "insufficient_risk_or_cash_for_one_unit" if gain_first else "entry_value_not_higher"
        assert fields["request_sha256"] is None and fields["static"] == ["no_entry", reason]


def test_exact_zero_utility_precedes_quantity_constraint(data):
    _, stored, fold, _ = data
    sample, _ = prepare(data)
    question = first_decision(sample).opportunity.feature.question
    gross = question.start_close * Fraction(33, 10_000) / question.anchor_id[1].pre_peak_range
    plan, _ = prepare(data, model=value_model(fold, gross), risk_budget=Fraction(1, 1000))
    original = first_decision(plan)
    forecast = alternate_forecast(stored, original)
    fields = reference_fixed_exit_fields(original, forecast, forecast_sha256=stored.seal.sha256)
    assert fields == {"static": ["no_entry", "entry_value_not_higher"],
                      "baseline_static": ["no_entry", "insufficient_risk_or_cash_for_one_unit"],
                      "expected_net": "0", "request_sha256": None}


def test_distinguishes_unsupported_path_mass_from_zero_mass(data):
    _, stored, fold, _ = data
    plan, _ = prepare(data, model=value_model(fold, unavailable_class=True))
    original = first_decision(plan)
    pin = stored.seal.sha256
    for outcome in (0, 9):
        forecast = alternate_forecast(stored, original, outcome=outcome)
        fields = reference_fixed_exit_fields(original, forecast, forecast_sha256=pin)
        assert fields == _producer_fields(original, forecast, pin)
        if outcome == 9:
            assert fields["static"] == ["unsupported", "entry_value_unsupported: positive_mass_without_complete_payoff"]
            assert fields["expected_net"] is None
        else:
            assert fields["request_sha256"]


def test_preserves_causal_absence_and_inactive_questions(data):
    _, stored, _, _ = data
    plan, _ = prepare(data, read_visible=lambda *_: None)
    for coordinate in plan.coordinates:
        for original in coordinate.decisions:
            forecast = alternate_forecast(stored, original)
            pin = stored.seal.sha256
            fields = reference_fixed_exit_fields(original, forecast, forecast_sha256=pin)
            assert fields == _producer_fields(original, forecast, pin)
            assert fields["static"] == fields["baseline_static"]
            assert fields["expected_net"] is fields["request_sha256"] is None


@pytest.mark.parametrize("corruption", ("request_quantity", "forecast_pin", "prior_net"))
def test_rejects_changed_causal_input(data, corruption):
    _, stored, _, _ = data
    plan, _ = prepare(data)
    original = first_decision(plan)
    forecast = alternate_forecast(stored, original)
    pin = stored.seal.sha256
    if corruption == "request_quantity":
        step = original.baseline.step
        request = replace(step.episode.pending, quantity=step.episode.pending.quantity + 1)
        step = replace(step, episode=replace(step.episode, pending=request))
        original = replace(original, baseline=replace(original.baseline, step=step))
    elif corruption == "forecast_pin":
        pin = "0" * 64
    else:
        value = original.selected.entry_value
        original = replace(original, selected=replace(original.selected, entry_value=replace(value, expected_net=Fraction(-1))))
    with pytest.raises(ValueError, match="EI_saved_fixed_exit_reference_"):
        reference_fixed_exit_fields(original, forecast, forecast_sha256=pin)
