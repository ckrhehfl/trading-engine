"""Invented checks for the producer-independent ES witnesses."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_model_inputs as inputs
from research import intraday_event_success as producer
from research import intraday_event_success_metrics as metrics
from research import intraday_event_success_reference as reference
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction, query_record
from test_intraday_event_mean_baseline import data_for, roster


IMMEDIATE, WAIT = reference.VALUES


def test_training_witness_is_exact_and_rejects_response_and_support_tampering(monkeypatch):
    first, unknown = (candidate(index).query for index in range(2))
    evaluation = candidate(state="CONFIRMED")
    data, exact = data_for((first, unknown), (0., 1), fills=((1, 1), (1, None)), evaluation_rows=(evaluation.query,))
    tiny = Fraction(1, 10**330)
    exact[first.key] = {IMMEDIATE: str(tiny), WAIT: str(-tiny)}
    phases = {row.key: "onset" for row in data.base.training}
    adapted, artifact = producer.prepare(data, phases, exact)
    causal = roster((evaluation,))
    predictions = producer.project(data, causal, artifact)
    for function in ("prepare", "project"):
        monkeypatch.setattr(producer, function, lambda *a: pytest.fail("ES producer called"))

    assert artifact["cells"]["onset"]["rates"] == {IMMEDIATE: "1", WAIT: "0"}
    assert reference.verify_training(data, phases, exact, adapted, artifact)["joint_rows"] == 1
    assert reference.verify_projection(data, causal, artifact, predictions)["predictions"] == 1
    head = adapted.base.targets[0]
    changed = replace(adapted, base=replace(adapted.base, targets=(replace(head, net_immediate=0), adapted.base.targets[1])))
    with pytest.raises(ValueError, match="success responses"):
        reference.verify_training(data, phases, exact, changed, artifact)
    changed = replace(adapted, base=replace(adapted.base, targets=(replace(head, path_class=0), adapted.base.targets[1])))
    with pytest.raises(ValueError, match="auxiliary target"):
        reference.verify_training(data, phases, exact, changed, artifact)
    changed = deepcopy(artifact)
    changed["original_q_support"]["paired_known_rows"] = 1
    with pytest.raises(ValueError, match="original_q_support"):
        reference.verify_training(data, phases, exact, adapted, changed)
    changed = deepcopy(predictions)
    changed[0]["predictions"][IMMEDIATE] = "0"
    with pytest.raises(ValueError, match="historical success projection"):
        reference.verify_projection(data, causal, artifact, changed)


def test_bounded_saved_training_prefix_independently_checks_wait_fill(monkeypatch):
    c = candidate(0)
    raw = label_record(c)
    data = workflow.collect(CALENDAR, 0, (query_record(c),), (raw,))
    phases = {c.key: "onset"}
    exact = {c.key: {head: raw["training_labels"][head] for head in reference.VALUES}}
    onset = {"session": c.episode.session, "symbol": c.episode.symbol, "frame": {"kind": "at_onset",
        "event_id": c.episode.event_id, "overlap_group_id": c.episode.cluster_id, "family": c.episode.family,
        "decision_offset": c.episode.onset_decision_offset}}
    shards = (("train", {"session_indices": [0, 113]}), ("later", {"session_indices": [113, 130]}))

    def causal_rows(slot, *_):
        assert slot == "train"
        yield onset

    def training_labels(slot, _, cutoff):
        assert (slot, cutoff) == ("train", CALENDAR[97])
        yield raw

    monkeypatch.setattr(inputs, "_rows", causal_rows)
    monkeypatch.setattr(inputs, "training_labels", training_labels)
    assert reference.verify_training_inputs(shards, data, phases, exact)["rows"] == 1
    changed = replace(data, action_targets=(replace(data.action_targets[0], wait3=None, wait3_matured_session=None),))
    with pytest.raises(ValueError, match="original training action fills"):
        reference.verify_training_inputs(shards, changed, phases, exact)

    raw["targets"]["wait3"]["net_cash"] = "1"
    with pytest.raises(ValueError, match="exact cash ratio"):
        reference.verify_training_inputs(shards, data, phases, exact)


def _candidate(day, key, cluster):
    c = candidate(day, event=f"invented-{key}-")
    symbol = f"KRX:{cluster:06d}"
    cluster_id = f"{day}-{cluster}"
    return replace(c, episode=replace(c.episode, symbol=symbol, cluster_id=cluster_id),
                   frame=replace(c.frame, overlap_group_id=cluster_id),
                   query=replace(c.query, symbol=symbol, cluster_id=cluster_id))


def _targets(c, cash, wait_unknown):
    record = label_record(c)
    for action in ("immediate", "wait3"):
        target = record["targets"][action]
        if action == "wait3" and wait_unknown:
            target.update(net_cash=None, value=None, status="unknown")
        else:
            target.update(net_cash=str(cash), value=str(Fraction(cash) / Fraction(target["denominator"])))
        record["training_labels"]["net_" + action] = target["value"]
    return record


def test_report_rebuilds_cluster_weights_ties_empty_days_and_calendar_deletions(monkeypatch):
    specs = [
        (97, 1, 1, 0, 8, False), (97, 1, -1, 0, 99, False), (97, 2, 0, .5, 2, False),
        (97, 3, 0, 0, 10, False), (97, 3, -1, -1, 4, False), (97, 4, 1, 99, 99, True),
        (98, 1, 1, 0, 0, False), (98, 1, -1, 0, 0, False),
        (99, 1, 1, 1, 0, False), (99, 2, -1, 0, 1, False),
    ]
    candidates = tuple(_candidate(day, i, cluster) for i, (day, cluster, *_) in enumerate(specs))
    records = tuple(_targets(c, item[2], item[-1]) for c, item in zip(candidates, specs))
    forecasts = {arm: [] for arm in reference.ARMS}
    for c, (_, _, _, direct, learned, _) in zip(candidates, specs):
        for arm, score in zip(reference.ARMS, ("1/3", direct, learned)):
            forecasts[arm].append(prediction(c, score, score))
    events = tuple(c.episode for c in candidates)
    reps = metrics.select_representatives(CALENDAR, 0, events, candidates, forecasts)
    report = metrics.summarize(CALENDAR, 0, reps, records)
    monkeypatch.setattr(metrics, "select_representatives", lambda *a: pytest.fail("ES producer called"))
    monkeypatch.setattr(metrics, "summarize", lambda *a: pytest.fail("ES producer called"))
    assert reference.verify_representatives(CALENDAR, 0, events, candidates, forecasts, reps)["verified"]
    assert reference.verify_report(CALENDAR, 0, reps, iter(records), report)["verified"]

    phase = report["cells"][0]
    action = phase["actions"][IMMEDIATE]
    assert phase["paired_known"] == 9
    assert phase["actions"][WAIT]["outcomes"]["unknown_cash"] == 1
    assert action["own_known"] == dict.fromkeys(reference.ARMS, 10)
    assert action["days"][0]["pairs"] == 3 and action["days"][0]["auc"]["direct"] == "3/8"
    assert action["days"][1]["positive"] == action["days"][1]["negative"] == 1
    assert action["days"][1]["auc"] == dict.fromkeys(reference.ARMS)
    assert action["auc"] == {"rate": "1/2", "direct": "11/16", "success": "3/8"}
    assert action["delete5"]["direct"] == {"min": "3/8", "max": "11/16", "defined": 35}
    changed = deepcopy(reps)
    changed[0]["predictions"]["direct"][IMMEDIATE] += 1e-15
    with pytest.raises(ValueError, match="ranking score"):
        reference.verify_representatives(CALENDAR, 0, events, candidates, forecasts, changed)
    changed = deepcopy(report)
    changed["cells"][0]["actions"][IMMEDIATE]["days"][0]["auc"]["direct"] = "1/2"
    with pytest.raises(ValueError, match="same-day ranking report"):
        reference.verify_report(CALENDAR, 0, reps, records, changed)


def test_summary_witness_averages_eligible_phases_before_dates(monkeypatch):
    reports = [metrics.summarize(CALENDAR, fold, (), ()) for fold in range(4)]
    for phase, day, score in ((0, 0, "0"), (1, 0, "1/2"), (0, 1, "1")):
        reports[0]["cells"][phase]["actions"][IMMEDIATE]["days"][day].update(
            pairs=1, auc={"rate": "1/2", "direct": "1/2", "success": score})
    saved = metrics.pool(CALENDAR, reports)
    monkeypatch.setattr(metrics, "pool", lambda *a: pytest.fail("ES producer called"))
    assert reference.verify_pool(CALENDAR, reports, saved)["cells"] == 16
    assert saved["actions"][IMMEDIATE]["auc"]["success"] == "5/8"
    saved["actions"][IMMEDIATE]["auc"]["success"] = "1/2"
    with pytest.raises(ValueError, match="four-fold same-day ranking summary"):
        reference.verify_pool(CALENDAR, reports, saved)
