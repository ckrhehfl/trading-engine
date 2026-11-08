"""Invented training/payoff bindings and deliberate coherent-model mutations."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
import subprocess
import sys

import pytest

from research import intraday_context_entry_values as producer
from research import intraday_context_entry_values_reference as reference
from research.intraday_context_empirical import EmpiricalTrainingRow
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import TrainingOutcome


F = Fraction
OPEN = datetime(2032, 4, 8, 9, tzinfo=timezone(timedelta(hours=9)))
CUTOFF = OPEN + timedelta(days=3)
POLICY, SOURCE = "1" * 64, "2" * 64


def identity(day=0, stage="approach", episode=1, offset=32, symbol="invented", risk=F(4)):
    opening = OPEN + timedelta(days=day)
    anchor = ResistanceAnchor(opening, F(100), opening + timedelta(minutes=20),
                              opening + timedelta(minutes=27), F(116), risk, F(16), risk / 4)
    at = opening + timedelta(minutes=offset)
    return (((symbol, anchor), episode), stage,
            (at, F(116), risk / 4, 116 + 2 * risk, 116 - risk, at + timedelta(minutes=30)))


def row(*, day=0, stage="approach", episode=1, offset=32, symbol="invented", outcome=0,
        origin="A", fill=True, gross=F(1), gap=F(0), weight=F(1), close=F(116)):
    key = identity(day, stage, episode, offset, symbol)
    at = key[2][0]
    empirical = EmpiricalTrainingRow(key, symbol, at.date().isoformat(), stage, origin, at,
        None if outcome is None else TrainingOutcome(outcome, at + timedelta(minutes=32)),
        "missing_label" if outcome is None else None)
    if fill is None:
        gross = gap = maturity = None
    elif fill is False:
        gross = gap = F(0)
        maturity = at + timedelta(minutes=3)
    else:
        maturity = at + timedelta(minutes=35)
    return producer.HistoricalEntryPayoff(empirical, close, F(4), fill, maturity, gross, gap,
        None if outcome is None else weight, SOURCE, "missing_price" if fill is None else None,
        "gap_declined" if fill is False else None)


def probabilities(*pairs):
    values = [F(0)] * 10
    for index, value in pairs or ((0, 1),):
        values[index] = F(value)
    return tuple(values)


def query(*, stage="approach", close=F(116), risk=F(4), cost=F(0), mass=None):
    key = identity(day=3, stage=stage, risk=risk)
    return producer.EntryValueQuery(key, key[2][0], close, risk, cost, mass or probabilities(),
        "context_sequence", "3" * 64, "4" * 64, POLICY)


def fitted(rows):
    return producer.fit_entry_values(rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY)


def verify(rows, requests=(), *, model=None, results=None, **kwargs):
    model = fitted(rows) if model is None else model
    results = tuple(producer.assess_entry_value(model, request) for request in requests) if results is None else results
    return reference.verify_entry_values(model, rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY,
        queries=requests, results=results, **kwargs)


def rehash(model):
    # Deliberately coherent forged artifacts test more than checksum checking.
    digest = producer._model_digest(model.cutoff_at, model.execution_policy_sha256,
                                    model.input_sha256, model.source_sha256s, model.stages)
    return replace(model, model_sha256=digest)


def replace_path(model, path):
    stage = replace(model.stages[0], paths=(path,) + model.stages[0].paths[1:])
    return rehash(replace(model, stages=(stage, model.stages[1])))


def test_reference_exact_formula_no_fill_and_external_query_order():
    rows = (row(gross=F(2), gap=F(1, 4), weight=F(1, 2)),
            row(episode=2, offset=42, fill=False, weight=F(1, 2)))
    requests = (query(cost=F(100)), query(cost=F(0)))
    model = fitted(rows)
    results = tuple(producer.assess_entry_value(model, request) for request in requests)
    assert (results[0].expected_gross, results[0].expected_cost, results[0].expected_net) == (
        F(4), F(117, 200), F(683, 200))
    proof = verify(rows, requests, model=model, results=results)
    assert proof["verified"] and proof["actions"] == {"enter": 2}
    assert proof["original_rows"] == proof["complete_payoff_rows"] == 2
    assert proof["known_no_fill_rows"] == 1 and proof["maximum_absolute_difference"] == 0
    assert not proof["source_authenticated"] and not proof["market_payoffs_reconstructed"]
    with pytest.raises(InputRefusal):
        verify(rows, requests, model=model, results=results[::-1])


def test_producer_and_common_validators_are_not_called(monkeypatch):
    rows, requests = (row(),), (query(cost=F(33)),)
    model = fitted(rows)
    results = (producer.assess_entry_value(model, requests[0]),)

    def forbidden(*args, **kwargs):
        pytest.fail("independent verifier called producer/common validation")

    for name in ("fit_entry_values", "assess_entry_value", "forecast_probability_boundary", "_model_digest",
                 "_payoff", "_geometry", "_moments", "_digest", "_clock", "_need", "_probabilities",
                 "question_payload", "weighted_training_rows"):
        monkeypatch.setattr(producer, name, forbidden)
    from research import intraday_context_empirical as empirical
    from research import intraday_context_model_inputs as common
    monkeypatch.setattr(empirical, "weighted_training_rows", forbidden)
    monkeypatch.setattr(empirical, "_validate", forbidden)
    monkeypatch.setattr(common, "question_payload", forbidden)
    assert verify(rows, requests, model=model, results=results)["verified"]


def test_all_ten_classes_both_stages_and_origins_use_same_original_weights():
    rows = tuple(row(stage=stage, episode=index + 1, offset=32 + index * 5, outcome=index,
                     origin=("A", "B", "other")[index % 3], gross=F(index - 4, 5), weight=F(1, 10))
                 for stage in ("approach", "breakout") for index in range(10))
    requests = tuple(query(stage=stage, cost=F(33), mass=(F(1, 10),) * 10)
                     for stage in ("approach", "breakout"))
    proof = verify(rows, requests)
    assert proof["label_supported_rows"] == proof["complete_payoff_rows"] == 20
    assert proof["queries"] == 2
    assert verify(tuple(reversed(rows)), requests) == proof


def test_original_denominators_preserve_missing_label_and_missing_payoff():
    rows = (row(weight=F(1, 2)), row(episode=2, offset=42, fill=None, weight=F(1, 2)),
            row(episode=3, offset=52, outcome=None, fill=None), row(day=1, gross=F(3)))
    model = fitted(rows)
    path = model.stages[0].paths[0]
    assert path.support.eligible_weight == 2
    assert path.support.complete_weight == F(3, 2)
    assert path.mean_filled_gross_in_R == F(7, 3)
    proof = verify(rows, (query(),), model=model)
    assert (proof["original_rows"], proof["label_supported_rows"], proof["unknown_payoff_rows"]) == (4, 3, 2)
    forged = fitted((replace(rows[0], weight=F(1)), rows[3]))
    with pytest.raises(InputRefusal):
        verify(rows, model=forged)


@pytest.mark.parametrize("rows,entry_query,action,reason", [
    ((), query(), "abstain", "positive_mass_without_complete_payoff"),
    ((row(fill=False),), query(cost=F(100)), "wait", None),
    ((row(fill=None),), query(), "abstain", "positive_mass_without_complete_payoff"),
    ((row(gross=F(0)),), query(cost=F(33)), "wait", None),
    ((row(gross=F(1, 10)),), query(cost=F(1000, 29)), "wait", None),
    ((row(gross=F(1, 10)),), query(cost=F(100)), "wait", None),
    ((row(outcome=9, gross=F(-1, 2)),), query(mass=probabilities((9, 1))), "wait", None),
    ((row(close=F(120), gap=F(-1)),), query(close=F(1), cost=F(33)), "abstain",
     "nonpositive_transferred_entry_notional"),
    ((row(stage="breakout"),), query(), "abstain", "positive_mass_without_complete_payoff"),
])
def test_empty_zero_missing_timeout_and_invalid_transfer_are_distinct(rows, entry_query, action, reason):
    model = fitted(rows)
    result = producer.assess_entry_value(model, entry_query)
    assert (result.action, result.reason) == (action, reason)
    assert verify(rows, (entry_query,), model=model, results=(result,))["actions"] == {action: 1}


def test_unsupported_mass_is_not_renormalized_or_priced_as_zero():
    rows = (row(),)
    request = query(mass=probabilities((0, F(99, 100)), (1, F(1, 100))))
    model = fitted(rows)
    result = producer.assess_entry_value(model, request)
    assert result.unsupported_probability_mass == F(1, 100)
    assert result.class_net_values[:2] == (F(4), None)
    assert verify(rows, (request,), model=model, results=(result,))["actions"] == {"abstain": 1}
    forged = replace(result, unsupported_probability_mass=F(0), expected_gross=F(4), expected_cost=F(0),
                     expected_net=F(4), status="supported", action="enter", reason=None)
    with pytest.raises(InputRefusal):
        verify(rows, (request,), model=model, results=(forged,))


@pytest.mark.parametrize("change", [
    {"mean_fill": F(0)}, {"mean_filled_gross_in_R": F(2)}, {"mean_filled_gap_in_R": F(1, 4)},
    {"mean_fill": 1}, {"outcome_index": True}, {"outcome_index": 1},
])
def test_coherently_rehashed_moments_still_require_original_rows(change):
    rows = (row(),)
    model = fitted(rows)
    changed = replace_path(model, replace(model.stages[0].paths[0], **change))
    if change == {"mean_fill": 1} and type(change["mean_fill"]) is int:
        assert changed.model_sha256 == model.model_sha256  # Wire integers can conceal a type substitution.
    with pytest.raises(InputRefusal):
        verify(rows, model=changed)


@pytest.mark.parametrize("change", [
    {"eligible_rows": 2}, {"complete_rows": 0}, {"known_no_fill_rows": 1},
    {"eligible_weight": F(2)}, {"complete_weight": F(0)}, {"remaining_unknown_rows": 1},
    {"remaining_unknown_weight": F(1)}, {"unknown_by_reason": (("fabricated", 1, F(1)),)},
    {"eligible_rows": True},
])
def test_coherently_rehashed_support_counts_and_types_are_recomputed(change):
    rows = (row(),)
    model = fitted(rows)
    path = model.stages[0].paths[0]
    changed = replace_path(model, replace(path, support=replace(path.support, **change)))
    with pytest.raises(InputRefusal):
        verify(rows, model=changed)


@pytest.mark.parametrize("change", [
    {"candidate_rows": 2}, {"label_unavailable_by_reason": (("fake", 1),)},
    {"payoff_unavailable_by_reason": (("fake", 1),)}, {"stage": "breakout"},
])
def test_coherent_stage_metadata_cannot_relabel_original_roster(change):
    rows = (row(),)
    model = fitted(rows)
    changed = rehash(replace(model, stages=(replace(model.stages[0], **change), model.stages[1])))
    with pytest.raises(InputRefusal):
        verify(rows, model=changed)


def test_external_cutoff_policy_sources_and_input_content_bind_model():
    rows = (row(),)
    model = fitted(rows)
    alternatives = (
        producer.fit_entry_values(rows, cutoff_at=CUTOFF + timedelta(days=1), execution_policy_sha256=POLICY),
        producer.fit_entry_values(rows, cutoff_at=CUTOFF, execution_policy_sha256="9" * 64),
        fitted((replace(rows[0], source_sha256="9" * 64),)),
        fitted((replace(rows[0], gross_in_R=F(2)),)),
        rehash(replace(model, source_sha256s=("9" * 64,))),
        rehash(replace(model, input_sha256="9" * 64)),
    )
    for forged in alternatives:
        with pytest.raises(InputRefusal):
            verify(rows, model=forged)


@pytest.mark.parametrize("change", [
    {"weight": F(1, 2)}, {"weight": 1}, {"filled": 1}, {"gross_in_R": 1},
    {"gap_in_R": F(-1)}, {"gap_in_R": F(2)}, {"gross_in_R": F(-100)},
    {"decision_close": 116}, {"risk": F(3)}, {"source_sha256": "bad"},
    {"actual_mature_at": CUTOFF}, {"actual_mature_at": OPEN}, {"actual_mature_at": OPEN.replace(tzinfo=None)},
    {"actual_mature_at": OPEN.replace(second=1)}, {"unsupported_reason": "hidden"},
    {"no_fill_reason": "hidden"},
])
def test_original_payoff_validation_is_independent(change):
    original = row()
    with pytest.raises(InputRefusal):
        verify((replace(original, **change),), model=fitted((original,)))


@pytest.mark.parametrize("changed", [
    replace(row(fill=False), gross_in_R=F(1)), replace(row(fill=False), no_fill_reason=None),
    replace(row(fill=None), gross_in_R=F(0)), replace(row(fill=None), actual_mature_at=OPEN),
    replace(row(fill=None), unsupported_reason=""), replace(row(outcome=None, fill=None), weight=F(0)),
])
def test_no_fill_unknown_and_unlabeled_payloads_cannot_smuggle_values(changed):
    with pytest.raises(InputRefusal):
        verify((changed,), model=fitted((row(),)))


@pytest.mark.parametrize("change", [
    {"symbol": "substitution"}, {"session": "2032-04-09"}, {"session": "not-date"},
    {"stage": "breakout"}, {"origin_context": "future"}, {"unavailable_reason": "extra"},
    {"forecast_at": OPEN + timedelta(minutes=33)},
    {"outcome": TrainingOutcome(0, CUTOFF)}, {"outcome": TrainingOutcome(True, OPEN + timedelta(minutes=64))},
    {"outcome": TrainingOutcome(9, OPEN + timedelta(minutes=35))},
])
def test_original_question_identity_and_label_maturity_are_checked(change):
    original = row()
    changed = replace(original, question=replace(original.question, **change))
    with pytest.raises(InputRefusal):
        verify((changed,), model=fitted((original,)))


def test_duplicate_episode_origin_anchor_and_stage_order_are_rejected():
    original = row()
    model = fitted((original,))
    changed_anchor = replace(original.question.question_id[0][0][1], start_price=F(99), advance=F(17))
    other = row(episode=2, offset=42, weight=F(1, 2))
    key = other.question.question_id
    changed_key = (((key[0][0][0], changed_anchor), key[0][1]), key[1], key[2])
    invalid = (
        (original, original),
        (original, row(offset=42)),
        (original, row(stage="breakout", origin="B")),
        (row(weight=F(1, 2)), replace(other, question=replace(other.question, question_id=changed_key))),
        (row(offset=42), row(stage="breakout", offset=32)),
    )
    for rows in invalid:
        with pytest.raises(InputRefusal):
            verify(rows, model=model)


@pytest.mark.parametrize("change", [
    {"cost_bps": 0}, {"cost_bps": F(-1)}, {"risk": F(3)}, {"decision_close": F(0)},
    {"execution_policy_sha256": "9" * 64}, {"forecast_source_sha256": "bad"},
    {"causal_source_sha256": "bad"}, {"forecast_model_key": "evaluation_winner"},
    {"probabilities": (F(1),) * 10}, {"probabilities": (1,) + (F(0),) * 9},
    {"probabilities": (F(-1), F(2)) + (F(0),) * 8},
])
def test_external_queries_are_checked_even_with_a_genuine_model(change):
    rows, request = (row(),), query()
    model = fitted(rows)
    result = producer.assess_entry_value(model, request)
    with pytest.raises(InputRefusal):
        verify(rows, (replace(request, **change),), model=model, results=(result,))


@pytest.mark.parametrize("change", [
    {"expected_gross": F(0)}, {"expected_cost": F(0)}, {"expected_net": F(0)},
    {"expected_gross": 4}, {"class_net_values": (F(4),) * 10}, {"unsupported_probability_mass": F(1)},
    {"status": "abstain"}, {"action": "wait"}, {"reason": "fake"},
    {"model_sha256": "9" * 64}, {"training_input_sha256": "9" * 64},
    {"transfer_assumption": "no_assumptions"},
])
def test_saved_value_and_claim_mutations_fail(change):
    rows, request = (row(),), query(cost=F(33))
    model = fitted(rows)
    result = producer.assess_entry_value(model, request)
    with pytest.raises(InputRefusal):
        verify(rows, (request,), model=model, results=(replace(result, **change),))


def test_float_boundary_normalizes_all_mass_once_and_retains_correction():
    boundary = producer.forecast_probability_boundary((0.1,) * 10)
    request = replace(query(mass=boundary.probabilities), numeric_boundary=boundary)
    rows, model = (row(),), fitted((row(),))
    result = producer.assess_entry_value(model, request)
    assert result.unsupported_probability_mass == F(9, 10)
    assert verify(rows, (request,), model=model, results=(result,))["verified"]
    for changed in (replace(boundary, original_sum=F(1)), replace(boundary, maximum_absolute_correction=F(0)),
                    replace(boundary, probabilities=probabilities()),
                    replace(boundary, original=(float("nan"),) * 10),
                    replace(boundary, original=(True,) + (0.0,) * 9)):
        with pytest.raises(InputRefusal):
            verify(rows, (replace(request, numeric_boundary=changed),), model=model, results=(result,))


def test_complete_exact_mass_boundary_supports_small_roundoff_and_integer_input_vertices():
    for values in ((0.5, 0.5000000001) + (0.0,) * 8, (1,) + (0,) * 9):
        boundary = producer.forecast_probability_boundary(values)
        request = replace(query(mass=boundary.probabilities), numeric_boundary=boundary)
        assert verify((row(),), (request,))["verified"]


def test_roster_shape_and_backdated_query_fail():
    rows, request = (row(),), query()
    model = fitted(rows)
    result = producer.assess_entry_value(model, request)
    for queries, results in (((request,), ()), ((), (result,)), ([request], (result,)), ((request,), [result])):
        with pytest.raises(InputRefusal):
            reference.verify_entry_values(model, rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY,
                                           queries=queries, results=results)
    key = identity(day=2)
    with pytest.raises(InputRefusal):
        verify(rows, (replace(request, question_id=key, forecast_at=key[2][0]),), model=model, results=(result,))


def test_import_does_not_load_numeric_libraries_or_run_market_io():
    root = Path(__file__).resolve().parents[1]
    code = (f"import sys; sys.path.insert(0, {str(root)!r}); "
            "import research.intraday_context_entry_values_reference; "
            "assert not any(name in sys.modules for name in ('numpy','torch','lightgbm'))")
    subprocess.run([sys.executable, "-I", "-S", "-c", code], check=True, capture_output=True, timeout=15)
