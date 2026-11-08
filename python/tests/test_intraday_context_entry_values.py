"""Invented exact payoffs only; no market readers, native receipts or ledgers."""

from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest

from research.intraday_context_empirical import EmpiricalTrainingRow
from research.intraday_context_entry_values import (
    SCHEMA, EntryValueQuery, HistoricalEntryPayoff, assess_entry_value,
    fit_entry_values, forecast_probability_boundary,
)
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome
from research.intraday_scenario_labels import PathQuestion


F = Fraction
OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
CUTOFF = OPEN + timedelta(days=2)
POLICY = "a" * 64
SOURCE = "b" * 64
FORECAST = "c" * 64
CAUSAL = "d" * 64


def identity(*, day=0, episode=1, offset=32, stage="approach", symbol="invented", risk=F(4)):
    opening = OPEN + timedelta(days=day)
    at = opening + timedelta(minutes=offset)
    anchor = ResistanceAnchor(opening, F(100), opening + timedelta(minutes=20),
                              opening + timedelta(minutes=27), F(116), risk, F(16), risk / 4)
    path = PathQuestion(at, at + timedelta(minutes=30), 116, risk / 4, 116 + 2 * risk, 116 - risk)
    return (((symbol, anchor), episode), stage, path.question_id), at


def historical(*, outcome=0, filled=True, gross=F(1), gap=F(0), weight=F(1),
               day=0, episode=1, offset=32, stage="approach", symbol="invented", close=F(116)):
    key, at = identity(day=day, episode=episode, offset=offset, stage=stage, symbol=symbol)
    label = None if outcome is None else TrainingOutcome(outcome, at + timedelta(minutes=32))
    question = EmpiricalTrainingRow(key, symbol, at.date().isoformat(), stage, "A", at, label,
                                    "unobserved_label" if outcome is None else None)
    if filled is None:
        maturity, gross, gap, unknown, no_fill = None, None, None, "unknown_fill", None
    elif filled is False:
        maturity, gross, gap, unknown, no_fill = at + timedelta(minutes=3), F(0), F(0), None, "gap_declined"
    else:
        maturity, unknown, no_fill = at + timedelta(minutes=35), None, None
    return HistoricalEntryPayoff(question, close, F(4), filled, maturity, gross, gap,
                                 None if outcome is None else weight, SOURCE, unknown, no_fill)


def mass(*pairs):
    values = [F(0)] * len(OUTCOMES)
    for index, value in pairs or ((0, F(1)),):
        values[index] = F(value)
    return tuple(values)


def query(*, probabilities=None, close=F(116), risk=F(4), cost=F(0), stage="approach", day=2):
    key, at = identity(day=day, stage=stage, risk=risk)
    return EntryValueQuery(key, at, close, risk, cost, probabilities or mass(), "context_sequence",
                           FORECAST, CAUSAL, POLICY)


def fit(*rows):
    return fit_entry_values(rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY)


def test_exact_fill_gap_cost_formula_and_known_no_fill_weight():
    model = fit(historical(gross=F(2), gap=F(1, 4), weight=F(1, 2)),
                historical(episode=2, offset=42, filled=False, weight=F(1, 2)))
    result = assess_entry_value(model, query(cost=F(100)))
    path = result.training_support.paths[0]
    assert SCHEMA == "ei-context-entry-values-draft-v1"
    assert (path.mean_fill, path.mean_filled_gross_in_R, path.mean_filled_gap_in_R) == (F(1, 2), F(1), F(1, 8))
    assert (result.expected_gross, result.expected_cost, result.expected_net) == (F(4), F(117, 200), F(683, 200))
    assert path.support.eligible_weight == path.support.complete_weight == path.support.complete_fraction == 1
    assert path.support.known_no_fill_rows == 1
    assert (result.status, result.action, result.unsupported_probability_mass) == ("supported", "enter", 0)


def test_high_hit_probability_can_have_negative_value_with_asymmetric_payoffs():
    model = fit(historical(gross=F(1, 2), weight=F(1, 2)),
                historical(episode=2, offset=42, outcome=1, gross=F(-3), weight=F(1, 2)))
    result = assess_entry_value(model, query(probabilities=mass((0, F(7, 10)), (1, F(3, 10)))))
    assert result.expected_net == F(-11, 5)
    assert result.action == "wait"


def test_positive_gross_can_be_negative_after_cost_and_zero_tie_waits():
    model = fit(historical(gross=F(1, 10)))
    assert assess_entry_value(model, query()).action == "enter"
    costly = assess_entry_value(model, query(cost=F(100)))
    assert costly.expected_gross == F(2, 5)
    assert costly.expected_net == F(-19, 25) and costly.action == "wait"
    tied = assess_entry_value(model, query(cost=F(1000, 29)))
    assert tied.expected_net == 0 and tied.action == "wait"


def test_current_price_and_risk_scale_separate_gross_from_entry_notional_cost():
    model = fit(historical(gross=F(1), gap=F(1, 4)))
    a = assess_entry_value(model, query(cost=F(100)))
    b = assess_entry_value(model, query(close=F(232), cost=F(100)))
    c = assess_entry_value(model, query(risk=F(8), cost=F(100)))
    assert a.expected_gross == b.expected_gross == 4
    assert b.expected_cost - a.expected_cost == F(116, 100)
    assert c.expected_gross == 8 and c.expected_cost == F(118, 100)


def test_label_class_does_not_substitute_fixed_profit_or_timeout_zero():
    model = fit(historical(outcome=9, gross=F(-1, 2)))
    result = assess_entry_value(model, query(probabilities=mass((9, 1))))
    assert result.expected_net == -2 and result.action == "wait"
    assert OUTCOMES[9] == ("P4", 30)


def test_same_symbol_day_questions_share_weight_before_path_conditioning():
    model = fit(historical(gross=F(1), weight=F(1, 2)),
                historical(episode=2, offset=42, gross=F(3), weight=F(1, 2)),
                historical(day=1, gross=F(5)))
    path = model.stages[0].paths[0]
    assert path.mean_filled_gross_in_R == F(7, 2)
    assert (path.support.eligible_rows, path.support.eligible_weight) == (3, 2)


def test_missing_payoff_keeps_original_weight_and_complete_case_limitation():
    model = fit(historical(weight=F(1, 2)),
                historical(episode=2, offset=42, filled=None, weight=F(1, 2)))
    result = assess_entry_value(model, query())
    support = result.training_support.paths[0].support
    assert result.expected_net == 4
    assert (support.eligible_rows, support.complete_rows, support.remaining_unknown_rows) == (2, 1, 1)
    assert (support.eligible_weight, support.complete_weight, support.remaining_unknown_weight) == (1, F(1, 2), F(1, 2))
    assert support.complete_fraction == F(1, 2)
    assert support.unknown_by_reason == (("unknown_fill", 1, F(1, 2)),)
    assert "complete_case" in result.transfer_assumption


@pytest.mark.parametrize("unknown_row", [False, True])
def test_positive_mass_without_complete_payoff_abstains_without_mass_redistribution(unknown_row):
    rows = [historical(weight=F(1, 2) if unknown_row else F(1))]
    if unknown_row:
        rows.append(historical(episode=2, offset=42, outcome=1, filled=None, weight=F(1, 2)))
    model = fit(*rows)
    result = assess_entry_value(model, query(probabilities=mass((0, F(99, 100)), (1, F(1, 100)))))
    assert (result.status, result.action, result.reason) == ("abstain", "abstain", "positive_mass_without_complete_payoff")
    assert result.unsupported_probability_mass == F(1, 100)
    assert result.expected_net is result.expected_cost is result.expected_gross is None
    assert result.class_net_values[:2] == (F(4), None)


def test_known_no_fill_is_complete_zero_unlike_unknown_fill():
    zero = assess_entry_value(fit(historical(filled=False)), query(cost=F(100)))
    unknown = assess_entry_value(fit(historical(filled=None)), query())
    assert (zero.action, zero.expected_gross, zero.expected_cost, zero.expected_net) == ("wait", 0, 0, 0)
    assert zero.training_support.paths[0].support.complete_fraction == 1
    assert unknown.action == "abstain" and unknown.training_support.paths[0].support.complete_fraction == 0


def test_zero_gross_filled_outcome_still_pays_cost():
    result = assess_entry_value(fit(historical(gross=F(0))), query(cost=F(100)))
    assert result.expected_net == F(-29, 25)


def test_missing_label_retains_reason_without_inventing_a_path_or_weight():
    model = fit(historical(), historical(episode=2, offset=42, outcome=None, filled=None))
    stage = model.stages[0]
    assert stage.candidate_rows == 2 and stage.label_unavailable_by_reason == (("unobserved_label", 1),)
    assert stage.payoff_unavailable_by_reason == (("unknown_fill", 1),)
    assert stage.paths[0].support.eligible_weight == 1
    assert sum(path.support.eligible_rows for path in stage.paths) == 1
    assert assess_entry_value(model, query()).expected_net == 4


def test_empty_training_and_missing_stage_abstain():
    assert assess_entry_value(fit(), query()).unsupported_probability_mass == 1
    model = fit(historical(stage="breakout"))
    assert assess_entry_value(model, query()).action == "abstain"
    assert assess_entry_value(model, query(stage="breakout")).expected_net == 4


@pytest.mark.parametrize("maturity", [CUTOFF, CUTOFF + timedelta(minutes=1), OPEN])
def test_actual_payoff_maturity_must_be_after_question_and_strictly_before_cutoff(maturity):
    row = historical()
    assert row.question.outcome.mature_at < CUTOFF
    with pytest.raises(InputRefusal, match="actual_payoff_not_mature"):
        fit(replace(row, actual_mature_at=maturity))


def test_mature_payoff_does_not_bypass_label_cutoff_and_no_fill_can_mature_first():
    row = historical(filled=False)
    assert row.actual_mature_at < row.question.outcome.mature_at
    assert fit(row).stages[0].paths[0].mean_fill == 0
    future_label = replace(row.question, outcome=TrainingOutcome(0, CUTOFF))
    with pytest.raises(InputRefusal, match="outcome_not_mature"):
        fit(replace(row, question=future_label))


@pytest.mark.parametrize("mutation", [
    {"weight": F(1, 2)}, {"weight": 1}, {"filled": 1}, {"source_sha256": "bad"},
    {"risk": F(3)}, {"decision_close": 116}, {"gross_in_R": 1.0},
    {"gap_in_R": F(2)}, {"gap_in_R": F(-1)}, {"gross_in_R": F(-30)},
    {"no_fill_reason": "not_a_no_fill"}, {"unsupported_reason": "not_unknown"},
])
def test_historical_exact_identity_geometry_source_and_state_mutations_refused(mutation):
    with pytest.raises(InputRefusal):
        fit(replace(historical(), **mutation))


def test_no_fill_payload_and_unknown_payload_cannot_smuggle_payoff():
    for changed in (replace(historical(filled=False), gross_in_R=F(1)),
                    replace(historical(filled=False), no_fill_reason=None),
                    replace(historical(filled=None), gap_in_R=F(0)),
                    replace(historical(filled=None), actual_mature_at=OPEN),
                    replace(historical(filled=None), unsupported_reason="")):
        with pytest.raises(InputRefusal):
            fit(changed)


def test_missing_payoff_cannot_reweight_complete_rows():
    with pytest.raises(InputRefusal, match="weight_differs"):
        fit(historical(weight=F(1)), historical(episode=2, offset=42, filled=None, weight=F(1, 2)))


def test_duplicate_question_and_changed_question_clock_are_refused():
    row = historical()
    with pytest.raises(InputRefusal, match="duplicate"):
        fit(row, row)
    with pytest.raises(InputRefusal):
        fit(replace(row, question=replace(row.question, forecast_at=row.question.forecast_at + timedelta(minutes=1))))


def test_model_and_result_bind_exact_inputs_policy_sources_and_query():
    one, two = historical(weight=F(1, 2)), historical(episode=2, offset=42, weight=F(1, 2))
    model = fit(one, two)
    assert fit(two, one) == model
    assert fit(replace(one, gross_in_R=F(2)), two).input_sha256 != model.input_sha256
    assert fit(replace(one, source_sha256=FORECAST), two).input_sha256 != model.input_sha256
    request = query()
    result = assess_entry_value(model, request)
    assert result.query is request and result.training_input_sha256 == model.input_sha256
    assert result.model_sha256 == model.model_sha256 and model.source_sha256s == (SOURCE,)
    with pytest.raises(InputRefusal, match="model_digest"):
        assess_entry_value(replace(model, cutoff_at=model.cutoff_at - timedelta(minutes=1)), request)


@pytest.mark.parametrize("mutation", [
    {"execution_policy_sha256": FORECAST}, {"forecast_source_sha256": ""},
    {"causal_source_sha256": ""}, {"forecast_model_key": "best_evaluated"},
    {"risk": F(3)}, {"cost_bps": F(-1)}, {"cost_bps": 0},
    {"probabilities": (F(1),) * 10}, {"probabilities": (1,) + (F(0),) * 9},
    {"probabilities": (F(-1), F(2)) + (F(0),) * 8},
])
def test_query_contract_mutations_refused(mutation):
    with pytest.raises(InputRefusal):
        assess_entry_value(fit(historical()), replace(query(), **mutation))


def test_query_cannot_be_backdated_and_has_no_future_fill_input():
    with pytest.raises(InputRefusal, match="clock_policy"):
        assess_entry_value(fit(historical()), query(day=1))
    names = {field.name for field in fields(EntryValueQuery)}
    assert not names.intersection({"entry_price", "exit_price", "next_open", "filled", "outcome"})
    request = query()
    with pytest.raises(TypeError):
        replace(request, next_open=F(117))


def test_nonpositive_transferred_entry_notional_abstains_instead_of_negative_cost():
    model = fit(historical(close=F(120), gap=F(-1)))  # Historical modeled entry is 116.
    result = assess_entry_value(model, query(close=F(1), cost=F(100)))
    assert result.action == "abstain" and result.reason == "nonpositive_transferred_entry_notional"
    assert result.expected_cost is result.expected_net is None


def test_float_boundary_records_entire_mass_and_exact_correction():
    boundary = forecast_probability_boundary((0.1,) * 10)
    assert boundary.probabilities == (F(1, 10),) * 10
    assert boundary.original_sum == 10 * F.from_float(0.1) and boundary.original_sum != 1
    assert boundary.maximum_absolute_correction == abs(F.from_float(0.1) - F(1, 10))
    request = replace(query(probabilities=boundary.probabilities), numeric_boundary=boundary)
    result = assess_entry_value(fit(historical()), request)
    assert result.unsupported_probability_mass == F(9, 10)
    assert result.query.numeric_boundary == boundary
    assert result.action == "abstain"  # Complete class zero is never scaled to probability one.


@pytest.mark.parametrize("values", [
    [0.1] * 10, (0.1,) * 9, (float("nan"),) + (0.0,) * 9,
    (float("inf"),) + (0.0,) * 9, (-0.1, 1.1) + (0.0,) * 8,
    (True,) + (0.0,) * 9, (F(1),) + (0.0,) * 9, (0.0,) * 10,
    (0.5, 0.500001) + (0.0,) * 8,
])
def test_float_boundary_reuses_forecast_numeric_type_and_mass_contract(values):
    with pytest.raises(InputRefusal, match="forecast_probability"):
        forecast_probability_boundary(values)


def test_float_boundary_accepts_shared_tolerance_and_integer_forecast_vertices():
    boundary = forecast_probability_boundary((0.5, 0.5000000001) + (0.0,) * 8)
    assert sum(boundary.probabilities) == 1 and boundary.original_sum != 1
    assert boundary.maximum_absolute_correction > 0
    assert forecast_probability_boundary((1,) + (0,) * 9).probabilities == mass()


def test_numeric_boundary_tampering_and_detaching_values_are_refused():
    boundary = forecast_probability_boundary((1.0,) + (0.0,) * 9)
    model = fit(historical())
    for bad in (replace(boundary, original_sum=F(2)),
                replace(boundary, maximum_absolute_correction=F(1)),
                replace(boundary, probabilities=mass((1, 1)))):
        with pytest.raises(InputRefusal, match="boundary_differs"):
            assess_entry_value(model, replace(query(), numeric_boundary=bad))
    with pytest.raises(InputRefusal, match="boundary_differs"):
        assess_entry_value(model, replace(query(probabilities=mass((1, 1))), numeric_boundary=boundary))
