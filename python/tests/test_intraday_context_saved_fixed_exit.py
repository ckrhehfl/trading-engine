"""Invented saved-ledger reuse for an alternate EI fixed-exit selector."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_context_entry_values import fit_entry_values
from research.intraday_context_saved_fixed_exit import prepare_saved_fixed_exit, project_saved_fixed_exit
from test_intraday_context_entry_compare import change_minute, data, first_pair, prepare, replay, value_model
from test_intraday_context_entry_values import POLICY, historical


def mixed_model(fold, *, gain_first):
    rows = [historical(outcome=index, stage=stage, episode=index + 1, weight=Fraction(1, 10),
                       gross=Fraction(2 if (index == 0) == gain_first else -1))
            for stage in ("approach", "breakout") for index in range(10)]
    return fit_entry_values(rows, cutoff_at=fold.first_test_opening, execution_policy_sha256=POLICY)


def first_decision(plan):
    return next(row for row in plan.coordinates[0].decisions if row.opportunity.status == "opportunity")


def alternate_forecast(stored, decision, *, outcome=0):
    forecast = next(row for row in stored.forecast.predictions if row.question_id == decision.opportunity.question_id)
    probabilities = tuple(float(index == outcome) for index in range(10))
    return replace(forecast, predictions=tuple(replace(arm, probabilities=probabilities)
                                               if arm.key == "context_lightgbm" else arm
                                               for arm in forecast.predictions))


@pytest.mark.parametrize(("outcome", "status"), (
    ("known_exit", "closed"), ("missing_open", "unresolved"),
    ("missing_exit", "unresolved"), ("target_gap", "no_fill"),
))
def test_reuses_same_request_when_alternate_forecast_switches_wait_to_enter(data, outcome, status):
    stored, fold, session = data[1:]
    plan, _ = prepare(data, model=mixed_model(fold, gain_first=True))
    original = first_decision(plan)
    decision = prepare_saved_fixed_exit(original, alternate_forecast(stored, original),
                                        forecast_sha256=stored.seal.sha256)
    if outcome == "missing_open":
        opened = original.baseline.step.episode.pending.execute_at
        session = change_minute(session, opened, ohlc=None, price_state="missing")
    elif outcome == "missing_exit":
        known, _ = replay(plan, session)
        closed = first_pair(known).baseline.holding.episode.closed_at
        session = change_minute(session, closed, ohlc=None, price_state="halt")
    elif outcome == "target_gap":
        opened = original.baseline.step.episode.pending.execute_at
        target = original.opportunity.feature.question.path.target
        price = Decimal(target.numerator) / Decimal(target.denominator)
        session = change_minute(session, opened, ohlc=(price,) * 4)

    old_result, _ = replay(plan, session)
    saved = first_pair(old_result)
    assert saved.selected.status == "no_entry"
    result = project_saved_fixed_exit(decision, saved)
    assert result.original.selected is saved.selected
    assert result.selected == result.baseline == saved.baseline
    assert result.selected.status == status
    if status == "unresolved":
        assert result.selected.payoff is result.net_delta is None
    else:
        assert result.net_delta == 0
        if status == "closed":
            assert result.selected.fill.quantity > 0
            assert result.selected.payoff.cost > 0


@pytest.mark.parametrize("baseline_known", (True, False))
def test_alternate_wait_has_known_zero_even_if_baseline_execution_is_unknown(data, baseline_known):
    stored, fold, session = data[1:]
    plan, _ = prepare(data, model=mixed_model(fold, gain_first=False))
    original = first_decision(plan)
    decision = prepare_saved_fixed_exit(original, alternate_forecast(stored, original),
                                        forecast_sha256=stored.seal.sha256)
    old_result, _ = replay(plan, session if baseline_known else None)
    saved = first_pair(old_result)
    assert saved.selected.status == ("closed" if baseline_known else "unresolved")
    result = project_saved_fixed_exit(decision, saved)
    assert result.baseline == saved.baseline
    assert result.selected.status == "no_entry" and result.selected.fill is None
    assert result.selected.payoff.net == 0 and result.selected.payoff.net_return is None
    assert result.net_delta == (-saved.baseline.payoff.net if baseline_known else None)


def test_alternate_path_without_training_payoff_is_unsupported(data):
    stored, fold, session = data[1:]
    plan, _ = prepare(data, model=value_model(fold, unavailable_class=True))
    original = first_decision(plan)
    decision = prepare_saved_fixed_exit(original, alternate_forecast(stored, original, outcome=9),
                                        forecast_sha256=stored.seal.sha256)
    old_result, _ = replay(plan, session)
    result = project_saved_fixed_exit(decision, first_pair(old_result))
    assert result.baseline.status == "closed" and result.selected.status == "unsupported"
    assert result.selected.payoff is result.net_delta is None


@pytest.mark.parametrize("corruption", ("question", "session", "symbol", "source", "sequence"))
def test_requires_the_matching_question_source_and_original_forecast(data, corruption):
    stored = data[1]
    plan, _ = prepare(data)
    original = first_decision(plan)
    forecast = alternate_forecast(stored, original)
    source = stored.seal.sha256
    if corruption == "question":
        forecast = next(row for row in stored.forecast.predictions if row.question_id != forecast.question_id)
    elif corruption == "session":
        forecast = replace(forecast, session="2000-01-01")
    elif corruption == "symbol":
        forecast = replace(forecast, symbol="different")
    elif corruption == "source":
        source = "0" * 64
    else:
        forecast = replace(forecast, predictions=tuple(replace(arm, probabilities=(1.0,) + (0.0,) * 9)
                                                       if arm.key == "context_sequence" else arm
                                                       for arm in forecast.predictions))
    with pytest.raises(ValueError):
        prepare_saved_fixed_exit(original, forecast, forecast_sha256=source)
