"""Invented ER inner-prefix, unchanged learner and target-free fixture checks."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_calibration_oof as oof
from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_model as learner
from research import intraday_event_model_inputs as inputs
from research import intraday_event_models as models
from research import intraday_event_sequence_model as sequence
from research.intraday_event_dataset_run import SHARDS
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction, query_record
from test_intraday_event_model_inputs import save


INDICES = (0, 1, 48, 49, 72, 73, 96, 97, 136, 137, 176, 177, 216, 217, 256)


def fixture_shards(tmp_path):
    """All original dates with sparse, wholly invented causal query/target rows."""
    calendar_hash = sha256(wire.encode(CALENDAR)).hexdigest()
    shards, queries, labels = [], {}, {}
    for index in INDICES:
        current = candidate(index)
        queries[index], labels[index] = query_record(current), label_record(current)
    for number, (first, end) in enumerate(SHARDS):
        slot = tmp_path / str(number)
        slot.mkdir()
        manifest = {"session_indices": [first, end], "calendar_sha256": calendar_hash, "before_sha256": "a" * 64}
        header = {"kind": "header", **manifest}
        selected = [index for index in INDICES if first <= index < end]

        def stream(name, records):
            return save(slot, name, [header, *records, {"kind": "footer", "data_rows": len(records)}])

        manifest["files"] = {
            "coordinate-support.jsonl": stream("coordinate-support.jsonl", [{"kind": "coordinate", "session": d} for d in CALENDAR[first:end]]),
            "queries.jsonl": stream("queries.jsonl", [queries[index] for index in selected]),
            "labels.jsonl": stream("labels.jsonl", [labels[index] for index in selected]),
        }
        shards.append((slot, manifest))
    return tuple(shards), queries, labels


def simple_data(inner=0, training=(0,), testing=None):
    stop, end = oof.SPLITS[inner][1:]
    testing = (stop, end - 1) if testing is None else testing
    train = [candidate(index) for index in training]
    test = [candidate(index) for index in testing]
    return oof.collect(CALENDAR, inner, [query_record(c) for c in (*train, *test)], [label_record(c) for c in train])


@pytest.mark.parametrize("inner", [0, 1])
def test_fixed_splits_preserve_original_calendar_and_reject_validation_labels(inner):
    stop, end = oof.SPLITS[inner][1:]
    selected = [candidate(index) for index in INDICES]
    data = oof.collect(CALENDAR, inner, map(query_record, selected),
                       (label_record(c) for c in selected if c.episode.session < CALENDAR[stop]))
    assert data.calendar == CALENDAR
    assert {r.session for r in data.training} == {CALENDAR[i] for i in INDICES if i < stop}
    assert {r.session for r in data.evaluation} == {CALENDAR[i] for i in INDICES if stop <= i < end}
    assert data.support["evaluation_labels_read"] is False
    with pytest.raises(ValueError, match="evaluation target"):
        oof.collect(CALENDAR, inner, map(query_record, selected), map(label_record, selected))
    for invalid in (-1, 2, True):
        with pytest.raises(ValueError, match="fixed inner"):
            oof.collect(CALENDAR, invalid, [], [])


@pytest.mark.parametrize("inner", [0, 1])
def test_training_prefix_never_decodes_first_oof_target_on_overlapping_shard(tmp_path, monkeypatch, inner):
    shards, queries, labels = fixture_shards(tmp_path)
    stop = oof.SPLITS[inner][1]
    permitted = {wire.encode(record) for index, record in labels.items() if index < stop}
    decoded, original_loads = [], inputs.json.loads

    def guarded(raw):
        if isinstance(raw, bytes) and b'"kind":"labels"' in raw:
            assert raw in permitted, "inner evaluation label was decoded"
            decoded.append(raw)
        return original_loads(raw)

    monkeypatch.setattr(inputs.json, "loads", guarded)
    data = oof.load_inner(shards, inner)
    assert len(decoded) == sum(index < stop for index in labels)
    assert len(data.training) == len(decoded)
    assert data.support["training_seal"] == models.support_seal(data.training)


def test_full_family_and_held_training_survive_while_missing_peers_stay_outside():
    a = candidate(0)
    a = replace(a, episode=replace(a.episode, family="A_DOWNFAIL"), frame=replace(a.frame, family="A_DOWNFAIL"),
                label_query=replace(a.label_query, family="A_DOWNFAIL"), query=replace(a.query, family="A_DOWNFAIL"))
    held = replace(a.query, key="held", state="HELD_10", episode_id="book", original_episode_id=a.episode.event_id,
                   position=(1 / 3, 2 / 3, 0))
    held_query = {"kind": "query", "query_kind": "held", "key": held.key, "session": held.session,
        "symbol": held.symbol, "query": evaluation._plain(held), "continuous_length": 100}
    held_label = {"kind": "labels", "query_kind": "held", "key": held.key, "session": held.session,
        "symbol": held.symbol, "targets": {"schema": "EL-held-remaining-gross-v1", "value": "1/100", "matured_offset": 64},
        "training_labels": evaluation._plain(models.TrainingLabels(held.key, None, None, None, None, held.session, Fraction(1, 100)))}
    missing = candidate(1, peer=None)
    data = oof.collect(CALENDAR, 0, [query_record(a), held_query, query_record(missing)],
                       [label_record(a), held_label, label_record(missing)])
    assert {r.family for r in data.training} == {"A_DOWNFAIL"}
    assert {r.state for r in data.training} == {"WAITING_PULLBACK", "HELD_10"}
    assert data.support["counts"]["training:peer_unknown"] == 1
    assert data.action_targets[-1].immediate is data.action_targets[-1].wait3 is None


def test_durable_start_precedes_only_direct_fit_and_all_oof_queries_are_predicted(monkeypatch):
    data = simple_data()
    events = []

    def fit(arm, rows, targets, fills, cutoff, **kwargs):
        assert events == ["start"] and arm == "direct"
        assert (rows, targets, fills, cutoff) == (data.training, data.targets, data.action_targets, CALENDAR[49])
        assert kwargs == {"mode": "chart_peer", "support_binding": data.support["training_seal"]}
        events.append("fit")
        return {"test": "invented"}

    def predict(artifact, rows):
        assert events == ["start", "fit"] and rows == data.evaluation
        return [{**prediction(candidate(CALENDAR.index(row.session))), "components": {
            "fill_wait3": 0.5, "conditional_net_immediate": None, "conditional_net_wait3": None},
            "component_status": dict.fromkeys(learner.HEADS, "invented")} for row in rows]

    monkeypatch.setattr(learner, "fit", fit)
    monkeypatch.setattr(learner, "predict", predict)
    result = oof.fit_unit(data, before_fit=lambda: events.append("start"))
    assert events == ["start", "fit"]
    assert result["training_sessions"] == list(CALENDAR[:49])
    assert result["evaluation_sessions"] == list(CALENDAR[49:73])
    assert result["fit_artifacts"] == 1 and result["predictions_sha256"] == oof._hash(result["predictions"])
    assert "components" not in result["predictions"][0]
    events.clear()
    with pytest.raises(OSError, match="durable"):
        oof.fit_unit(data, before_fit=lambda: (_ for _ in ()).throw(OSError("durable start failed")))
    assert not events


def test_empty_and_one_row_training_preserve_unknowns_without_native_runtime(monkeypatch):
    monkeypatch.setattr(sequence, "_runtime", lambda: pytest.fail("insufficient support invoked native runtime"))
    empty = simple_data(training=())
    result = oof.fit_unit(empty, before_fit=lambda: pytest.fail("empty fit started"))
    assert result["fit_artifacts"] == 0 and oof.verify_unit(result, empty)["verified"]
    data = simple_data()
    result = oof.fit_unit(data, before_fit=lambda: None)
    assert result["artifact"]["unsupported_head_count"] == 6
    assert all(row["predictions"]["net_immediate"] is None for row in result["predictions"])
    assert oof.verify_unit(result, data)["verified"]
    bad = deepcopy(result)
    bad["predictions"][0]["predictions"]["net_immediate"] = 0
    bad["predictions_sha256"] = oof._hash(bad["predictions"])
    with pytest.raises(ValueError, match="replay"):
        oof.verify_unit(bad, data)


def test_inner_boundary_maturity_and_support_tampering_fail_before_fit(monkeypatch):
    data = simple_data()
    monkeypatch.setattr(learner, "fit", lambda *a, **k: pytest.fail("invalid fit reached learner"))
    with pytest.raises(ValueError, match="forecast date"):
        oof.fit_unit(replace(data, evaluation=data.training), before_fit=lambda: None)
    labels = (replace(data.targets[0], matured_session=CALENDAR[49]),)
    with pytest.raises(ValueError, match="matured"):
        oof.fit_unit(replace(data, targets=labels), before_fit=lambda: None)
    action = (replace(data.action_targets[0], wait3_matured_session=CALENDAR[49]),)
    with pytest.raises(ValueError, match="matured"):
        oof.fit_unit(replace(data, action_targets=action), before_fit=lambda: None)
    with pytest.raises(ValueError, match="support"):
        oof.fit_unit(replace(data, support={**data.support, "training_seal": "b" * 64}), before_fit=lambda: None)


def test_invented_oof_uses_only_causal_training_keys_and_never_targets():
    rows = tuple(candidate(i).query for i in INDICES if i < 217)
    held = replace(rows[4], key="held", state="HELD_10", episode_id="book", original_episode_id=rows[4].episode_id,
                   position=(1 / 3, 2 / 3, 0))
    base = SimpleNamespace(fold=3, calendar=CALENDAR, scope=oof.SCOPE, training=(*rows, held))
    data = SimpleNamespace(base=base)
    forecasts = oof.invented_oof(data)
    assert {r["key"] for r in forecasts} == {r.key for r in base.training if r.session >= CALENDAR[49]}
    assert oof.invented_oof(data) == forecasts
    for record in forecasts:
        if record["key"] == "held":
            assert record["predictions"]["net_immediate"] is None
        else:
            assert record["head_status"]["net_immediate"] == "invented_target_free_oof"
            assert -0.01 <= record["predictions"]["net_immediate"] <= 0.01
