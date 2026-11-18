"""Invented EQ training-only means, causal phases and exact policy cash."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction as F
from math import isqrt
import sys
from types import SimpleNamespace

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_model as matched
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_mean_baseline as mean
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as original
from research import intraday_event_models as models
from test_intraday_event_factorized_workflow import (
    CALENDAR, candidate, label_record, prediction, query_record, tape,
)


def data_for(rows=(), values=(), *, fills=None, evaluation_rows=()):
    labels = tuple(models.TrainingLabels(row.key, None, 1 if row.state not in models.HELD_STATES else None,
        value, value, row.session if value is not None else None) for row, value in zip(rows, values))
    actions = tuple(matched.ActionFills(row.key, *(fills[index] if fills is not None else (1, 1)),
        *(row.session if v is not None else None for v in (fills[index] if fills is not None else (1, 1))))
        for index, row in enumerate(rows))
    support = {"training_seal": models.support_seal(rows) if rows else None,
               "evaluation_seal": models.support_seal(evaluation_rows) if evaluation_rows else None}
    base = original.ModelData(CALENDAR, 0, workflow.SCOPE, tuple(rows), labels, tuple(evaluation_rows), support)
    exact = {row.key: dict.fromkeys(models.Q_HEADS, None if value is None else str(F(value)))
             for row, value in zip(rows, values)}
    return workflow.FactorizedData(base, actions), exact


def roster(candidates):
    return inputs.DecisionRoster(tuple({c.episode.event_id: c.episode for c in candidates}.values()), tuple(candidates))


def policy(candidates, predictions):
    data, _ = data_for(evaluation_rows=tuple(c.query for c in candidates))
    return mean.freeze_policy(data, roster(candidates), predictions)


def test_full_joint_weights_are_conditioned_without_rebalancing_c_or_phase():
    first = candidate(0).query
    later = replace(first, key=first.key + ":later", decision_offset=36)
    other = replace(first, key="other", episode_id="other", family="A_DOWNFAIL")
    next_day = candidate(1).query
    data, exact = data_for((first, later, other, next_day), (0, 6, 100, 10))
    phases = {first.key: "onset", later.key: "waiting_pullback", other.key: None, next_day.key: "onset"}
    result = mean.estimate(data, phases, exact)
    onset = result["cells"]["onset"]
    assert result["joint"] == {"rows": 4, "dates": 2, "clusters": 2, "episodes": 3, "weight_sum": "1"}
    assert onset["weight_mass"] == "5/8" and onset["means"] == dict.fromkeys(models.Q_HEADS, "8")
    assert result["cells"]["waiting_pullback"]["means"] == dict.fromkeys(models.Q_HEADS, "6")
    assert onset["means"]["net_immediate"] != "5"  # Rebalanced C-only dates would give 5.


def test_positive_sparse_mass_has_defined_mean_but_empty_phase_has_no_fallback():
    c = candidate(0).query
    data, exact = data_for((c,), (F(1, 3),))
    cells = mean.estimate(data, {c.key: "onset"}, exact)["cells"]
    assert cells["onset"]["status"] == "estimated"
    assert cells["onset"]["precision"] == "unknown_small_support"
    assert cells["onset"]["means"]["net_immediate"] == "0." + "3" * 50
    assert cells["confirmation"]["status"] == "unsupported_empty_group"
    assert cells["confirmation"]["means"] == dict.fromkeys(models.Q_HEADS)
    empty, targets = data_for()
    assert mean.estimate(empty, {}, targets)["joint"]["weight_sum"] == "0"


def test_statistical_mean_precision_is_bounded_and_independent_of_ambient_context_or_input_order():
    first, second, third = (candidate(index).query for index in range(3))
    rows = (third, first, second)
    values = (F(10**30, 99991), -F(10**30, 99989), F(1, 99971))
    data, exact = data_for(rows, values)
    phases = dict.fromkeys((row.key for row in rows), "onset")
    before = deepcopy(exact)
    with localcontext() as context:
        context.prec = 7
        low = mean.estimate(data, phases, exact)
    reversed_data = replace(data, base=replace(data.base, training=tuple(reversed(rows))))
    with localcontext() as context:
        context.prec = 70
        high = mean.estimate(reversed_data, phases, exact)
    assert low == high and exact == before
    assert low["arithmetic_precision"] == 50 and low["rounding"] == "ROUND_HALF_EVEN"
    assert len(low["cells"]["onset"]["means"]["net_immediate"]) < 80


def test_exact_cancellation_remains_zero_and_cannot_trigger_first_positive_policy():
    rows = tuple(candidate(index).query for index in range(3))
    event = candidate()
    data, exact = data_for(rows, (-F(1, 100), -F(1, 100), F(2, 100)), evaluation_rows=(event.query,))
    phases = dict.fromkeys((row.key for row in rows), "onset")
    artifact = mean.estimate(data, phases, exact)
    assert artifact["cells"]["onset"]["means"] == dict.fromkeys(models.Q_HEADS, "0")
    forecasts = mean.project(data, roster((event,)), artifact)
    frozen = mean.freeze_policy(data, roster((event,)), forecasts)
    assert frozen.choices[0].action == "no_commit"
    assert frozen.choices[0].reason == "observe_without_commitment"


def test_exact_action_tie_survives_different_cancellations_and_chooses_immediate():
    rows = tuple(candidate(index).query for index in range(3))
    event = candidate()
    data, exact = data_for(rows, (F(1, 100),) * 3, evaluation_rows=(event.query,))
    wait_values = (-F(1, 100), F(2, 100), F(2, 100))
    data = replace(data, base=replace(data.base, targets=tuple(
        replace(label, net_wait3=value) for label, value in zip(data.base.targets, wait_values))))
    for row, value in zip(rows, wait_values):
        exact[row.key]["net_wait3"] = str(value)
    artifact = mean.estimate(data, dict.fromkeys((row.key for row in rows), "onset"), exact)
    assert artifact["cells"]["onset"]["means"] == dict.fromkeys(models.Q_HEADS, "0.01")
    frozen = mean.freeze_policy(data, roster((event,)), mean.project(data, roster((event,)), artifact))
    assert frozen.choices[0].action == "immediate"


def test_large_coprime_exact_total_bypasses_integer_string_digit_limit():
    primes = []
    value = 10001
    while len(primes) < 1100:
        if all(value % divisor for divisor in range(2, isqrt(value) + 1)):
            primes.append(value)
        value += 1
    rows = [SimpleNamespace(key=str(index)) for index in range(len(primes))]
    weights = {row.key: F(1, len(rows)) for row in rows}
    values = {row.key: {"net_immediate": f"1/{prime}"} for row, prime in zip(rows, primes)}
    total = sum((F(1, prime) for prime in primes), F()) / len(primes)
    assert total.denominator >= 10**4300
    previous_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(4300)
        with pytest.raises(ValueError, match="digit"):
            str(total.denominator)
        result = mean._mean(rows, weights, values, "net_immediate", F(1))
    finally:
        sys.set_int_max_str_digits(previous_limit)
    with localcontext() as context:
        context.prec = 80
        exact_to_decimal = Decimal(total.numerator) / Decimal(total.denominator)
        assert abs(Decimal(result) - exact_to_decimal) < Decimal("1e-53")
    assert len(result) < 80


def test_unknown_action_fill_and_held_rows_do_not_supply_joint_zero_observations():
    first, second = candidate(0).query, candidate(1).query
    held = replace(first, key="held", episode_id="heldbook", original_episode_id=first.episode_id,
                   state="HELD_10", position=(1 / 3, 2 / 3, 0))
    data, exact = data_for((first, second, held), (2, 9, None), fills=((1, 1), (1, None), (None, None)))
    labels = list(data.base.targets)
    labels[-1] = replace(labels[-1], remaining_gross=F(3), matured_session=held.session)
    data = replace(data, base=replace(data.base, targets=tuple(labels)))
    result = mean.estimate(data, {first.key: "onset", second.key: "onset", held.key: None}, exact)
    assert result["joint"]["rows"] == 1
    assert result["cells"]["onset"]["means"]["net_wait3"] == "2"


@pytest.mark.parametrize("change", ["late_label", "late_fill", "exact_value", "exact_missing", "phase_missing", "held_phase"])
def test_training_contract_tampering_is_rejected(change):
    c = candidate(0).query
    data, exact = data_for((c,), (F(1, 3),))
    phases = {c.key: "onset"}
    if change == "late_label":
        data = replace(data, base=replace(data.base, targets=(replace(data.base.targets[0], matured_session=CALENDAR[97]),)))
    elif change == "late_fill":
        data = replace(data, action_targets=(replace(data.action_targets[0], wait3_matured_session=CALENDAR[97]),))
    elif change == "exact_value":
        exact[c.key]["net_immediate"] = "1/2"
    elif change == "exact_missing":
        exact[c.key]["net_wait3"] = None
    elif change == "phase_missing":
        phases.clear()
    else:
        c = replace(c, family="A_DOWNFAIL")
        data = replace(data, base=replace(data.base, training=(c,), support={"training_seal": models.support_seal((c,))}))
    with pytest.raises(ValueError):
        mean.estimate(data, phases, exact)


def test_original_onset_decision_clock_survives_coalescing_and_future_states(monkeypatch):
    c = candidate(0, state="PULLBACK")
    data, _ = data_for((c.query,), (1,))
    manifests = (("first", {"session_indices": [0, 130]}), ("later", {"session_indices": [130, 257]}))
    onset = {"session": c.episode.session, "symbol": c.episode.symbol,
        "frame": {**evaluation._plain(c.frame), "state": "WAITING_PULLBACK", "kind": "at_onset"}}
    future = deepcopy(onset)
    future["session"] = CALENDAR[97]
    future["frame"]["decision_offset"] = 0
    calls = []

    def events(slot, manifest, filename, kind):
        calls.append((slot, filename, kind))
        assert slot == "first" and filename == "event-states.jsonl"
        return iter((onset, future))

    monkeypatch.setattr(inputs, "_rows", events)
    assert c.frame.onset_offset != c.frame.decision_offset
    assert mean.training_phases(manifests, data) == {c.key: "onset"}
    assert calls == [("first", "event-states.jsonl", "event_state")]
    onset["frame"]["decision_offset"] = 32
    assert mean.training_phases(manifests, data) == {c.key: "pullback"}
    onset["symbol"] = "KRX:000099"
    with pytest.raises(ValueError, match="original onset"):
        mean.training_phases(manifests, data)


def test_training_onsets_reject_missing_duplicate_and_later_clock(monkeypatch):
    c = candidate(0)
    data, _ = data_for((c.query,), (1,))
    shards = (("one", {"session_indices": [0, 257]}),)
    record = {"session": c.episode.session, "symbol": c.episode.symbol, "frame": evaluation._plain(c.frame)}
    for records, message in (((), "incomplete"), ((record, record), "duplicate")):
        monkeypatch.setattr(inputs, "_rows", lambda *args: iter(records))
        with pytest.raises(ValueError, match=message):
            mean.training_phases(shards, data)
    record["frame"]["decision_offset"] += 1
    monkeypatch.setattr(inputs, "_rows", lambda *args: iter((record,)))
    with pytest.raises(ValueError, match="original onset"):
        mean.training_phases(shards, data)


def test_exact_reader_decodes_only_query_fixed_training_prefix(monkeypatch):
    c, e = candidate(0), candidate()
    data = workflow.collect(CALENDAR, 0, [query_record(c), query_record(e)], [label_record(c)])
    calls = []
    shards = (("one", {"session_indices": [0, 130]}), ("later", {"session_indices": [130, 257]}))

    def queries(slot, manifest):
        assert slot == "one"
        calls.append("queries")
        yield query_record(c)
        yield query_record(e)

    def labels(slot, manifest, prefix):
        assert calls == ["queries"]
        assert prefix == [(c.key, c.episode.session, c.episode.symbol)]
        calls.append("training-prefix")
        yield label_record(c)

    monkeypatch.setattr(inputs, "query_rows", queries)
    monkeypatch.setattr(inputs, "_labels_for_prefix", labels)
    result = mean.training_targets(shards, data)
    assert set(result) == {c.key}
    assert result[c.key]["net_immediate"] == label_record(c)["training_labels"]["net_immediate"]
    assert calls == ["queries", "training-prefix"]


def test_exact_reader_rejects_late_labels_even_unwanted_and_float_cash(monkeypatch):
    c = candidate(0)
    data = workflow.collect(CALENDAR, 0, [query_record(c)], [label_record(c)])
    shards = (("one", {"session_indices": [0, 257]}),)
    monkeypatch.setattr(inputs, "training_labels", lambda *args: iter((label_record(candidate()),)))
    with pytest.raises(ValueError, match="evaluation target"):
        mean.training_targets(shards, data)
    bad = label_record(c)
    bad["targets"]["immediate"]["net_cash"] = 0.0
    monkeypatch.setattr(inputs, "training_labels", lambda *args: iter((bad,)))
    with pytest.raises(ValueError, match="Exact saved"):
        mean.training_targets(shards, data)


def test_projection_preserves_original_all_family_and_held_population_without_native_calls(monkeypatch):
    train = candidate(0).query
    onset, later = candidate(state="CONFIRMED"), candidate(decision=36)
    other = replace(onset.query, key="other", family="A_DOWNFAIL", episode_id="other")
    held = replace(onset.query, key="held", state="HELD_10", episode_id="heldbook",
                   original_episode_id=onset.query.episode_id, position=(1 / 3, 2 / 3, 0))
    rows = (onset.query, later.query, other, held)
    data, exact = data_for((train,), (F(1, 7),), evaluation_rows=rows)
    artifact = mean.estimate(data, {train.key: "onset"}, exact)
    monkeypatch.setattr(matched, "fit", lambda *args, **kwargs: pytest.fail("native fit forbidden"))
    monkeypatch.setattr(matched, "predict", lambda *args, **kwargs: pytest.fail("native inference forbidden"))
    forecasts = mean.project(data, roster((onset, later)), artifact)
    assert [row["key"] for row in forecasts] == [row.key for row in rows]
    assert float(forecasts[0]["predictions"]["net_immediate"]) == 1 / 7
    assert forecasts[1]["head_status"]["net_immediate"] == "unsupported_empty_group"
    assert all(row["predictions"]["net_immediate"] is None for row in forecasts[1:])
    assert all(row["predictions"][head] is None for row in forecasts for head in ("path_class", "fill", "remaining_gross"))
    artifact["fold"] = 1
    with pytest.raises(ValueError, match="artifact"):
        mean.project(data, roster((onset, later)), artifact)


def test_original_first_positive_tie_wait_once_and_exact_policy_cash_are_preserved():
    first, later = candidate(), candidate(decision=36, state="CONFIRMED")
    forecasts = (prediction(first, 1, 1), prediction(later, 8, 9))
    tied = policy((first, later), forecasts)
    assert tied.model == "EQ-mean" and tied.choices[0].action == "immediate" and len(tied.trace) == 1
    waited = policy((first, later), (prediction(first, 1, 2), prediction(later, 8, 9)))
    row = mean.join_policy_targets(waited, (label_record(first), label_record(later)))[0]
    assert row["schema"] == "EQ-policy-cash-v1" and row["arm"] == "mean" and row["action"] == "wait3"
    assert row["net_cash"] == label_record(first)["targets"]["wait3"]["net_cash"]


def test_unknown_earlier_policy_or_cash_cannot_become_zero():
    first, last = candidate(), candidate(decision=36, state="INVALIDATED")
    frozen = policy((first, last), (prediction(first, None, None), prediction(last, None, None)))
    row = mean.join_policy_targets(frozen, ())[0]
    assert frozen.choices[0].reason == "terminal_not_admissible"
    assert row["status"] == "unsupported" and row["net_cash"] is row["fill"] is None
    bars = tape()
    del bars[64]
    row = mean.join_policy_targets(policy((first,), (prediction(first, 1, 0),)), (label_record(first, bars),))[0]
    assert row["fill"] == 1 and row["net_cash"] is row["value"] is None
    row = mean.join_policy_targets(policy((first,), (prediction(first, 0, -1),)), ())[0]
    assert row["status"] == "no_commit" and row["net_cash"] == "0"


@pytest.mark.parametrize("action", ("immediate", "wait3"))
def test_original_zero_notional_action_contract_is_not_changed_by_mean_policy(action):
    from test_intraday_event_factorized_reference import zero_denominator_bundle

    candidates, forecasts, _, _, records, _, _ = zero_denominator_bundle(action)
    result = mean.join_policy_targets(policy(candidates, forecasts), records)[0]
    expected = records[0]["targets"][action]
    assert result["original_requested_notional"] == "0"
    for field in ("status", "reason", "net_cash", "fill", "value"):
        assert result[field] == expected[field]
