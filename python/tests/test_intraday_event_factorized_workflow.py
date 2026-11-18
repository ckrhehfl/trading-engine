"""Invented EP prefix, action support, sealing and exact fixed-policy joins."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction as F

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_model as learner
from research import intraday_event_factorized_workflow as flow
from research import intraday_event_labels as labels
from research import intraday_event_model_inputs as inputs
from research import intraday_event_models as models
from research.intraday_event_scenarios import EventFrame, MinuteBar


CALENDAR = tuple((date(2025, 9, 4) + timedelta(days=i)).isoformat() for i in range(257))


def candidate(index=97, decision=33, *, state="WAITING_PULLBACK", event="e", peer=(0, 0, 1), query=True):
    episode = evaluation.Episode(CALENDAR[index], "KRX:000001", event + str(index),
        "c" + str(index), flow.FAMILY, 33)
    terminal = state in ("CONFIRMED", "INVALIDATED")
    frame = EventFrame(flow.FAMILY, episode.event_id, episode.cluster_id, 30, decision - 3,
        decision, 27, decision - 2, "at_onset" if decision == 33 else "scheduled",
        state, terminal, F(2), F(96), F(95), ())
    reference = labels.LabelQuery(episode.event_id, flow.FAMILY, state, terminal, 30,
        decision - 3, decision, decision - 3, F(100), F(2), F(95), 100)
    model_query = models.Query(episode.event_id + "@" + str(decision), CALENDAR[index], episode.symbol,
        episode.event_id, episode.cluster_id, decision, flow.FAMILY, state,
        ((0,) * 13,) * 64, (0,) * 16, peer) if query else None
    return evaluation.Candidate(episode, frame, reference, model_query)


def query_record(c):
    return {"kind": "query", "query_kind": "entry", "key": c.key,
            "session": c.episode.session, "symbol": c.episode.symbol,
            "query": evaluation._plain(c.query), "label_query": evaluation._plain(c.label_query),
            "continuous_length": c.label_query.session_length}


def tape():
    return {i: MinuteBar(i, 100, 101, 99, 100, i + 3) for i in range(100)}


def label_record(c, bars=None):
    target = labels.build_targets(c.label_query, tape() if bars is None else bars)
    admitted = target.entry_admissibility == "admissible"
    known = {"path_class": target.path.value if target.path.matured_offset is not None else None,
        "fill": target.immediate.fill if admitted and target.immediate.fill_matured_offset is not None else None,
        "net_immediate": target.immediate.value if admitted and target.immediate.matured_offset is not None else None,
        "net_wait3": target.wait3.value if admitted and target.wait3.matured_offset is not None else None,
        "remaining_gross": None}
    original = None if c.query is None else models.TrainingLabels(c.key,
        matured_session=c.episode.session if any(value is not None for value in known.values()) else None, **known)
    return {"kind": "labels", "query_kind": "entry", "key": c.key,
        "session": c.episode.session, "symbol": c.episode.symbol,
        "targets": evaluation._plain(target), "training_labels": evaluation._plain(original)}


def prediction(c, immediate=0, wait=0):
    return {"key": c.key, "session": c.episode.session, "query_status": "supported_features",
        "predictions": {"path_class": [0.2, 0.6, 0.2], "fill": 0.5,
                        "net_immediate": immediate, "net_wait3": wait, "remaining_gross": None},
        "head_status": dict.fromkeys(models.HEADS, "invented")}


def frozen(candidates, forecasts=(), *, episodes=None):
    return evaluation.freeze_policy(tuple({c.episode.event_id: c.episode for c in candidates}.values())
        if episodes is None else episodes, candidates, forecasts, control="model", model="EP-direct",
        scope=flow.SCOPE, prediction_binding="a" * 64)


def test_action_fill_keeps_known_no_fill_and_independent_wait_support():
    c = candidate(0)
    bars = tape()
    bars[34] = MinuteBar(34, 103, 104, 102, 103, 37)
    record = label_record(c, bars)
    data = flow.collect(CALENDAR, 0, [query_record(c), query_record(candidate())], [record])
    action = data.action_targets[0]
    assert (action.immediate, action.wait3) == (0, 1)
    assert action.immediate_matured_session == action.wait3_matured_session == CALENDAR[0]
    assert data.base.targets[0].net_immediate == 0
    assert data.base.targets[0].net_wait3 < 0
    assert len(data.base.evaluation) == 1


def test_known_fill_with_unknown_cash_remains_known_and_unknown_q_is_not_zero():
    c = candidate(0)
    bars = tape()
    del bars[64]
    record = label_record(c, bars)
    assert record["targets"]["immediate"]["net_cash"] is None
    data = flow.collect(CALENDAR, 0, [query_record(c)], [record])
    assert data.action_targets[0].immediate == 1
    assert data.base.targets[0].net_immediate is None


def test_unknown_fill_is_not_inferred_from_cash_and_inadmissible_zeros_do_not_train():
    c = candidate(0)
    bars = tape()
    del bars[34]
    record = label_record(c, bars)
    assert flow.validate_saved_record(record, c.query)[0] is None
    invalid = candidate(1, state="INVALIDATED")
    invalid_record = label_record(invalid)
    assert invalid_record["targets"]["immediate"]["fill"] == 0
    assert flow.validate_saved_record(invalid_record, invalid.query) == (None, None)


@pytest.mark.parametrize("tamper", ["value", "maturity", "fill_maturity", "cash", "denominator"])
def test_exact_original_training_label_and_target_consistency_is_required(tamper):
    c = candidate(0)
    record = label_record(c)
    if tamper == "value":
        record["training_labels"]["net_immediate"] = "1"
    elif tamper == "maturity":
        record["training_labels"]["matured_session"] = CALENDAR[97]
    elif tamper == "fill_maturity":
        record["targets"]["wait3"]["fill_matured_offset"] = None
    elif tamper == "cash":
        record["targets"]["immediate"]["net_cash"] = "1"
    else:
        record["targets"]["wait3"]["denominator"] = "1"
    with pytest.raises(ValueError):
        flow.collect(CALENDAR, 0, [query_record(c)], [record])


def test_zero_original_denominator_masks_action_fills_without_rewriting_old_labels():
    c = candidate(0)
    c = replace(c, frame=replace(c.frame, structure=F(9_999_995)),
        label_query=replace(c.label_query, reference_close=F(10_000_000), structure_stop=F(9_999_995)))
    bars = {i: MinuteBar(i, 10_000_000, 10_000_001, 9_999_999, 10_000_000, i + 3) for i in range(100)}
    record = label_record(c, bars)
    assert record["targets"]["original_requested_notional"] == "0"
    data = flow.collect(CALENDAR, 0, [query_record(c)], [record])
    assert data.base.targets[0].fill == 0 and data.base.targets[0].net_immediate == 0
    assert data.action_targets[0].immediate is data.action_targets[0].wait3 is None


def test_evaluation_target_is_rejected_even_for_an_unwanted_key():
    c = candidate(0)
    with pytest.raises(ValueError, match="Evaluation target"):
        flow.collect(CALENDAR, 0, [query_record(c)], [label_record(c), label_record(candidate())])


def test_all_original_dates_and_all_family_held_training_are_retained():
    c = candidate(0)
    c = replace(c, episode=replace(c.episode, family="A_DOWNFAIL"), frame=replace(c.frame, family="A_DOWNFAIL"),
        label_query=replace(c.label_query, family="A_DOWNFAIL"), query=replace(c.query, family="A_DOWNFAIL"))
    entry = query_record(c)
    target = label_record(c)
    held = replace(c.query, key="held", episode_id="held-book", state="HELD_10",
        original_episode_id=c.episode.event_id, position=(1 / 3, 2 / 3, 0))
    held_record = {"kind": "query", "query_kind": "held", "key": held.key,
        "session": held.session, "symbol": held.symbol, "query": evaluation._plain(held), "continuous_length": 100}
    held_target = {"kind": "labels", "query_kind": "held", "key": held.key,
        "session": held.session, "symbol": held.symbol,
        "targets": {"schema": "EL-held-remaining-gross-v1", "value": "1/100", "matured_offset": 64},
        "training_labels": evaluation._plain(models.TrainingLabels(held.key, None, None, None, None, held.session, F(1, 100)))}
    data = flow.collect(CALENDAR, 0, [entry, held_record], [target, held_target])
    assert {query.state for query in data.base.training} == {"WAITING_PULLBACK", "HELD_10"}
    assert all(query.family == "A_DOWNFAIL" for query in data.base.training)
    assert data.action_targets[1].key == "held" and data.action_targets[1].immediate is None
    assert data.base.calendar == CALENDAR
    assert [day for _, first, end in flow.FOLDS for day in CALENDAR[first:end]] == list(CALENDAR[97:])


def test_durable_fit_callback_precedes_each_native_fit_and_predictions_are_split(monkeypatch):
    c, e = candidate(0), candidate()
    data = flow.collect(CALENDAR, 0, [query_record(c), query_record(e)], [label_record(c)])
    events = []

    def fit(arm, queries, targets, fills, cutoff, **kwargs):
        assert events[-1] == "sealed:" + arm
        assert queries == data.base.training and fills == data.action_targets
        events.append("fit:" + arm)
        return {"arm": arm}

    def predict(artifact, queries):
        assert queries == data.base.evaluation
        return [{**prediction(e), "components": {"fill_wait3": 0.4,
            "conditional_net_immediate": None, "conditional_net_wait3": None},
            "component_status": dict.fromkeys(learner.HEADS, "invented")}]

    monkeypatch.setattr(learner, "fit", fit)
    monkeypatch.setattr(learner, "predict", predict)
    for arm in flow.ARMS:
        result = flow.fit_unit(arm, data, before_fit=lambda: events.append("sealed:" + arm))
        assert "components" not in result["predictions"][0]
        assert result["components"][0]["components"]["fill_wait3"] == 0.4
        assert result["evaluation_sessions"] == list(CALENDAR[97:137])
    assert events == ["sealed:direct", "fit:direct", "sealed:factorized", "fit:factorized"]


def test_failed_durable_fit_start_prevents_optimizer(monkeypatch):
    c = candidate(0)
    data = flow.collect(CALENDAR, 0, [query_record(c)], [label_record(c)])
    monkeypatch.setattr(learner, "fit", lambda *args, **kwargs: pytest.fail("optimizer called"))
    with pytest.raises(RuntimeError, match="disk full"):
        flow.fit_unit("direct", data, before_fit=lambda: (_ for _ in ()).throw(RuntimeError("disk full")))


def test_empty_training_preserves_inference_roster_and_is_explicitly_unsupported():
    c = candidate()
    data = flow.collect(CALENDAR, 0, [query_record(c)], [])
    result = flow.fit_unit("direct", data, before_fit=lambda: pytest.fail("no fit should start"))
    assert result["status"] == "unsupported_empty_training" and result["fit_artifacts"] == 0
    assert result["predictions"][0]["key"] == c.key
    assert flow.verify_unit(result, data)["verified"]
    result["components"][0]["components"]["fill_wait3"] = 0
    with pytest.raises(ValueError, match="component"):
        flow.verify_unit(result, data)


def test_policy_tie_and_single_wait_commit_use_the_original_causal_rule():
    first, later = candidate(), candidate(decision=36, state="CONFIRMED")
    tie = frozen((first, later), (prediction(first, 1, 1), prediction(later, 5, 9)))
    wait = frozen((first, later), (prediction(first, 1, 2), prediction(later, 5, 9)))
    assert tie.choices[0].action == "immediate" and len(tie.trace) == 1
    assert wait.choices[0].action == "wait3" and len(wait.trace) == 1
    measures = flow.join_policy_targets(wait, (label_record(first), label_record(later)))
    assert measures[0]["net_cash"] == label_record(first)["targets"]["wait3"]["net_cash"]
    assert measures[0]["entry_policy"] == "next_open_capped"


def test_saved_cash_unknown_preserves_known_fill_and_explicit_no_commit_support():
    c = candidate()
    bars = tape()
    del bars[64]
    record = label_record(c, bars)
    row = flow.join_policy_targets(frozen((c,), (prediction(c, 2, 1),)), (record,))[0]
    assert row["net_cash"] is row["value"] is None and row["fill"] == 1
    observed = flow.join_policy_targets(frozen((c,), (prediction(c, 0, -1),)), ())[0]
    assert observed["status"] == "no_commit" and observed["net_cash"] == "0"
    unsupported = flow.join_policy_targets(frozen((c,)), ())[0]
    assert unsupported["status"] == "unsupported" and unsupported["net_cash"] is unsupported["fill"] is None
    no_frame = flow.join_policy_targets(frozen((), episodes=(c.episode,)), ())[0]
    assert no_frame["reason"] == "no_causal_frame" and no_frame["net_cash"] is None


def test_evaluation_iterator_is_lazy_retains_no_chart_and_requires_all_candidates(monkeypatch):
    c = candidate(query=False)
    data = flow.collect(CALENDAR, 0, [], [])
    roster = inputs.DecisionRoster((c.episode,), (c,))
    calls = []
    monkeypatch.setattr(inputs, "_test_shards", lambda shards, fold: [(None, None)])
    monkeypatch.setattr(inputs, "_rows", lambda *args: calls.append("read") or iter((label_record(c),)))
    stream = flow.evaluation_records((), data, roster)
    assert calls == []
    assert tuple(stream)[0]["training_labels"] is None
    assert calls == ["read"]
    monkeypatch.setattr(inputs, "_rows", lambda *args: iter(()))
    with pytest.raises(ValueError, match="incomplete"):
        tuple(flow.evaluation_records((), data, roster))


def test_saved_policy_target_denominator_tampering_is_rejected():
    c = candidate()
    record = deepcopy(label_record(c))
    for action in ("immediate", "wait3"):
        target = record["targets"][action]
        target["denominator"] = str(F(target["denominator"]) * 2)
        target["value"] = str(F(target["value"]) / 2)
        record["training_labels"]["net_" + action] = target["value"]
    record["targets"]["original_requested_notional"] = record["targets"]["immediate"]["denominator"]
    with pytest.raises(ValueError, match="denominator"):
        flow.join_policy_targets(frozen((c,), (prediction(c, 1, 0),)), (record,))


def test_saved_cash_requires_exact_scalars_and_in_session_maturity():
    c = candidate(0)
    for field in ("value", "net_cash", "denominator"):
        record = label_record(c)
        record["targets"]["immediate"][field] = 0.0
        with pytest.raises(ValueError, match="Exact saved"):
            flow.validate_saved_record(record, c.query)
    record = label_record(c)
    record["targets"]["wait3"]["fill_matured_offset"] = 100
    with pytest.raises(ValueError, match="original session"):
        flow.collect(CALENDAR, 0, [query_record(c)], [record])
    record["targets"]["wait3"]["fill_matured_offset"] = 1
    with pytest.raises(ValueError, match="predates"):
        flow.collect(CALENDAR, 0, [query_record(c)], [record])


def test_unmatured_inadmissible_raw_zero_remains_unknown_at_original_session_end():
    c = candidate(decision=100, state="INVALIDATED", query=False)
    record = label_record(c)
    assert record["targets"]["immediate"]["value"] == "0"
    assert record["targets"]["immediate"]["matured_offset"] is None
    assert flow.validate_saved_record(record, session_length=100) == (None, None)


def test_last_inadmissible_query_cannot_hide_an_earlier_unsupported_decision():
    first, terminal = candidate(), candidate(decision=36, state="INVALIDATED")
    policy = frozen((first, terminal))
    assert policy.choices[0].reason == "terminal_not_admissible"
    measured = flow.join_policy_targets(policy, ())[0]
    assert measured["status"] == "unsupported" and measured["net_cash"] is None


def test_load_fold_uses_original_query_identified_prefix_before_any_labels(monkeypatch):
    train, first, second, outside = candidate(0), candidate(97), candidate(130), candidate(137)
    shards = (("one", {"session_indices": [0, 130]}), ("two", {"session_indices": [130, 257]}))
    events = []
    monkeypatch.setattr(inputs, "calendar_from_coordinates", lambda got: CALENDAR)

    def queries(slot, manifest):
        for c in ((train, first) if slot == "one" else (second, outside)):
            events.append("query:" + c.key)
            yield query_record(c)

    def prefix_labels(slot, manifest, prefix):
        assert slot == "one"
        assert prefix == [(train.key, train.episode.session, train.episode.symbol)]
        assert events == ["query:" + c.key for c in (train, first, second, outside)]
        events.append("training-label")
        yield label_record(train)

    monkeypatch.setattr(inputs, "query_rows", queries)
    monkeypatch.setattr(inputs, "_labels_for_prefix", prefix_labels)
    data = flow.load_fold(shards, 0)
    assert tuple(q.key for q in data.base.training) == (train.key,)
    assert {q.key for q in data.base.evaluation} == {first.key, second.key}
    assert events[-1] == "training-label"
