"""Invented ET inner-prefix and exact ES response regressions."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_calibration_oof as er
from research import intraday_event_calibration_oof_reference as er_reference
from research import intraday_event_evaluation as evaluation
from research import intraday_event_immediate_oof as oof
from research import intraday_event_model_inputs as inputs
from research import intraday_event_models as models
from research import intraday_event_sequence_model as sequence
from test_intraday_event_calibration_oof import INDICES, fixture_shards
from test_intraday_event_factorized_workflow import CALENDAR, candidate
from test_intraday_event_model_inputs import save


def _replace_streams(shards, queries, labels):
    for slot, manifest in shards:
        first, end = manifest["session_indices"]
        header = {"kind": "header", **{key: value for key, value in manifest.items() if key != "files"}}
        for name, records in (("queries.jsonl", queries), ("labels.jsonl", labels)):
            rows = [record for record in records if CALENDAR[first] <= record["session"] <= CALENDAR[end - 1]]
            manifest["files"][name] = save(slot, name, [header, *rows,
                {"kind": "footer", "data_rows": len(rows)}])


def _set_action(record, action, *, fill, cash):
    target = record["targets"][action]
    value = None if cash is None else str(cash / Fraction(target["denominator"]))
    target.update(fill=fill, net_cash=None if cash is None else str(cash), value=value)
    if fill is None:
        target["fill_matured_offset"] = None
    if cash is None:
        target["matured_offset"] = None
    record["training_labels"]["net_" + action] = value
    if action == "immediate":
        record["training_labels"]["fill"] = fill


@pytest.mark.parametrize("inner", [0, 1])
def test_load_fit_and_audit_keep_each_forecast_label_unparsed(tmp_path, monkeypatch, inner):
    shards, _, labels = fixture_shards(tmp_path)
    _, stop, end = oof.SPLITS[inner]
    permitted = {wire.encode(record) for index, record in labels.items() if index < stop}
    loads = inputs.json.loads

    def guard(raw):
        if isinstance(raw, bytes) and b'"kind":"labels"' in raw:
            assert raw in permitted
        return loads(raw)

    monkeypatch.setattr(inputs.json, "loads", guard)
    data = oof.load_inner(iter(shards), inner)
    assert {row.session for row in data.training} == {CALENDAR[index] for index in INDICES if index < stop}
    assert {row.session for row in data.evaluation} == {CALENDAR[index] for index in INDICES if stop <= index < end}
    verified = oof.verify_inputs(iter(shards), data)
    assert verified["verified"] and verified["evaluation_labels_read"] is False
    bad_calendar = (*data.calendar[:stop], data.calendar[end], *data.calendar[stop + 1:])
    with pytest.raises(ValueError, match="authenticated calendar"):
        oof.verify_inputs(shards, replace(data, calendar=bad_calendar))

    # A one-row invented roster exercises the inherited fit and reconstruction
    # contracts without running the native optimiser.
    training = data.training[:1]
    data = replace(data, training=training, targets=data.targets[:1], action_targets=data.action_targets[:1],
        support={**data.support, "training_rows": 1, "training_seal": models.support_seal(training)})
    monkeypatch.setattr(sequence, "_runtime", lambda: pytest.fail("one-row fit used native backend"))
    starts = []
    result = oof.fit_unit(data, before_fit=lambda: starts.append(True))
    assert starts == [True]
    assert oof.verify_unit(result, data)["verified"]
    assert er_reference.verify_fit(result, data)["verified"]


def test_exact_profit_no_fill_zero_and_original_joint_support(tmp_path):
    shards, queries, labels = fixture_shards(tmp_path)
    _set_action(labels[0], "immediate", fill=1, cash=Fraction(1, 10**400))
    _set_action(labels[0], "wait3", fill=1, cash=Fraction())
    _set_action(labels[1], "immediate", fill=0, cash=Fraction())
    _set_action(labels[1], "wait3", fill=1, cash=Fraction(-1, 100))
    _set_action(labels[48], "wait3", fill=1, cash=None)
    _set_action(labels[49], "immediate", fill=None, cash=Fraction(1, 100))
    held = replace(candidate(1).query, key="held", state="HELD_10", episode_id="book",
                   original_episode_id="e1", position=(1 / 3, 2 / 3, 0))
    held_query = {"kind": "query", "query_kind": "held", "key": held.key, "session": held.session,
        "symbol": held.symbol, "query": evaluation._plain(held), "continuous_length": 100}
    held_label = {"kind": "labels", "query_kind": "held", "key": held.key, "session": held.session,
        "symbol": held.symbol, "targets": {"schema": "EL-held-remaining-gross-v1", "value": "1/100", "matured_offset": 64},
        "training_labels": evaluation._plain(models.TrainingLabels(held.key, None, None, None, None,
                                                                  held.session, Fraction(1, 100)))}
    _replace_streams(shards, sorted([*queries.values(), held_query], key=lambda row: row["session"]),
                     sorted([*labels.values(), held_label], key=lambda row: row["session"]))

    original = er.load_inner(shards, 1)
    data = oof.load_inner(shards, 1)
    verified = oof.verify_inputs(shards, data)
    assert verified["joint_rows"] == 3
    assert replace(data, targets=original.targets) == original
    assert original.targets[0].net_immediate == 0.0
    values = {row.key: (row.net_immediate, row.net_wait3) for row in data.targets}
    assert values[candidate(0).key] == (1, 0)
    assert values[candidate(1).key] == (0, 0)
    assert values[candidate(48).key] == values[candidate(49).key] == values[held.key] == (None, None)
    assert next(row for row in data.targets if row.key == held.key).remaining_gross == 0.01

    label = data.targets[0]
    for altered in (replace(label, net_immediate=0), replace(label, net_immediate=1.0),
                    replace(label, path_class=0 if label.path_class != 0 else 1)):
        with pytest.raises(ValueError, match="binary response or original auxiliary"):
            oof.verify_inputs(shards, replace(data, targets=(altered, *data.targets[1:])))
    actions = (replace(data.action_targets[0], wait3=None), *data.action_targets[1:])
    with pytest.raises(ValueError, match="action fills"):
        oof.verify_inputs(shards, replace(data, action_targets=actions))
