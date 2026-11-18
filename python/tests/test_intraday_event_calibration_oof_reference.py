"""Invented independent inner admission and fit-metadata tamper witnesses."""

from dataclasses import replace
from hashlib import sha256
from itertools import accumulate
import random

import pytest

from research import intraday_event_calibration_oof as oof
from research import intraday_event_calibration_oof_reference as reference
from research import intraday_event_factorized_model as learner
from research import intraday_event_model_inputs as inputs
from research import intraday_event_models as models
from research import intraday_event_sequence_model as sequence
from test_intraday_event_calibration_oof import fixture_shards, simple_data
from test_intraday_event_factorized_model import query, labels, fills
from test_intraday_event_factorized_workflow import CALENDAR
from research import intraday_campaign_wsl_transport as wire


def reseal(result):
    for field in ("artifact", "predictions", "components"):
        result[field + "_sha256"] = None if result[field] is None else oof._hash(result[field])


def invented_fit_metadata():
    """An invented native accounting witness, never a fitted or historical model."""
    a = query(0, session=CALENDAR[0], family="A_DOWNFAIL", episode="a", cluster="one")
    b = replace(a, key="key:1", decision_offset=73)
    c = query(2, session=CALENDAR[0], episode="c", cluster="two", symbol="KRX:000002")
    d = query(3, session=CALENDAR[1], episode="d", cluster="three")
    rows = (a, b, c, d)
    actions = tuple(fills(r, int(i in (0, 3)), int(i in (2, 3))) for i, r in enumerate(rows))
    targets = tuple(labels(r, path=i % 3, fill=action.immediate, immediate=9 if i == 3 else 0,
                           wait=6 if i == 2 else 0) for i, (r, action) in enumerate(zip(rows, actions)))
    heads, bases, weights, _, support = learner._head_plan("direct", rows,
        {r.key: r for r in targets}, {r.key: r for r in actions})
    _, diagnostics = learner._diagnostic_plan(rows, heads, weights)
    proposal = learner._proposal(bases)
    rng, digest = random.Random(sequence.SEED), sha256()
    hits, sampled = {h: 0 for h in weights}, {h: set() for h in weights}
    for _ in range(sequence.UPDATES):
        chosen = rng.choices(range(len(rows)), cum_weights=list(accumulate(proposal)), k=sequence.BATCH_SIZE)
        digest.update(sequence._json(chosen) + b"\n")
        for head in weights:
            known = [i for i in chosen if weights[head][i] > 0]
            hits[head] += bool(known)
            sampled[head].update(known)
    for head, info in heads.items():
        if head in weights:
            info.update(status="trained_tcn", updates_with_known_target=hits[head], sampled_training_rows=len(sampled[head]),
                        sampled_training_clusters=len({rows[i].cluster_id for i in sampled[head]}))
        info["fit_input_known_rows"] = info["known_rows"] if head in weights else 0
    diagnostics.update(status="complete", completed_updates=sequence.UPDATES,
        proposal_sha256=sha256(sequence._json(proposal)).hexdigest(), sampled_indices_sha256=digest.hexdigest())
    out = query(99, session=CALENDAR[49])
    data = oof.InnerData(CALENDAR, 0, oof.SCOPE, rows, targets, (out,), actions,
                        {"training_seal": models.support_seal(rows)})
    artifact = {"schema": "EP-factorized-model-v1", "arm": "direct", "mode": "chart_peer",
        "parameters": sequence.PARAMETERS, "normalization": sequence._normalizer(rows, "chart_peer"),
        "heads": heads, "action_training_support": support, "training_diagnostics": diagnostics,
        "training_rows": len(rows), "eval_first_session": CALENDAR[49], "training_sessions": list(CALENDAR[:2]),
        "training_key_hashes": sorted(sha256(r.key.encode()).hexdigest() for r in rows),
        "common_support_sha256": data.support["training_seal"], "fit_head_count": len(weights),
        "fit_attempt_head_count": len(weights), "native_fit_head_count": len(weights), "native_joint_fit_count": 1,
        "native_updates": sequence.UPDATES, "constant_head_count": sum(h["status"] == "constant_single_class" for h in heads.values()),
        "unsupported_head_count": sum(h["status"] == "unsupported" for h in heads.values()), "failed_head_count": 0}
    result = {"schema": "ER-inner-model-unit-v1", "arm": "direct", "kind": "tcn", "scope": oof.SCOPE,
        "inner": 0, "training_sessions": list(CALENDAR[:49]), "evaluation_sessions": list(CALENDAR[49:73]),
        "support": data.support, "status": "completed", "fit_artifacts": 1, "artifact": artifact,
        "predictions": [{"key": out.key, "session": out.session, "predictions": {
            "path_class": [.2, .5, .3], "fill": .25, "net_immediate": 2., "net_wait3": -1., "remaining_gross": None}}],
        "components": [{"key": out.key, "session": out.session, "components": {
            "fill_wait3": .5, "conditional_net_immediate": None, "conditional_net_wait3": None}}]}
    reseal(result)
    return result, data


@pytest.mark.parametrize("inner", [0, 1])
def test_independent_prefix_reconstruction_does_not_call_producer_or_decode_oof_targets(tmp_path, monkeypatch, inner):
    shards, _, labels_by_index = fixture_shards(tmp_path)
    data = oof.load_inner(shards, inner)
    permitted = {wire.encode(row) for index, row in labels_by_index.items() if index < (49, 73)[inner]}
    original_loads = inputs.json.loads

    def guarded(raw):
        if isinstance(raw, bytes) and b'"kind":"labels"' in raw:
            assert raw in permitted
        return original_loads(raw)

    monkeypatch.setattr(inputs.json, "loads", guarded)
    monkeypatch.setattr(oof, "collect", lambda *a: pytest.fail("producer collector called"))
    monkeypatch.setattr(oof, "load_inner", lambda *a: pytest.fail("producer reader called"))
    monkeypatch.setattr(inputs, "_labels_for_prefix", lambda *a: pytest.fail("producer prefix selector called"))
    assert reference.verify_inputs(shards, data)["evaluation_labels_read"] is False
    with pytest.raises(ValueError, match="training queries"):
        reference.verify_inputs(shards, replace(data, training=data.training[:-1]))


def test_independent_native_metadata_reconstruction_uses_no_producer_math(monkeypatch):
    result, data = invented_fit_metadata()
    monkeypatch.setattr(learner, "_head_plan", lambda *a: pytest.fail("producer head weights called"))
    monkeypatch.setattr(learner, "predict", lambda *a: pytest.fail("producer native inference called"))
    monkeypatch.setattr(sequence, "_normalizer", lambda *a: pytest.fail("producer normalizer called"))
    assert reference.verify_fit(result, data)["native_optimizer_reimplemented"] is False


@pytest.mark.parametrize("where", ["normalizer", "joint", "conditional", "proposal", "sample", "fill", "cutoff", "roster", "dates"])
def test_resealed_fit_metadata_tampering_is_rejected(where):
    result, data = invented_fit_metadata()
    artifact = result["artifact"]
    if where == "normalizer":
        artifact["heads"]["net_immediate"]["target_normalization"]["mean"] += 1
    elif where == "joint":
        artifact["action_training_support"]["joint_known_rows"] -= 1
    elif where == "conditional":
        artifact["heads"]["net_wait3"]["conditional_on_fill"] = True
    elif where == "proposal":
        artifact["training_diagnostics"]["proposal_sha256"] = "f" * 64
    elif where == "sample":
        artifact["training_diagnostics"]["sampled_indices_sha256"] = "f" * 64
    elif where == "fill":
        artifact["action_training_support"]["zero_decomposition"]["wait3"]["weighted_fill_mass"] = 1
    elif where == "cutoff":
        artifact["eval_first_session"] = CALENDAR[73]
    elif where == "roster":
        result["predictions"].pop()
    else:
        result["training_sessions"].append(CALENDAR[49])
    reseal(result)
    with pytest.raises(ValueError):
        reference.verify_fit(result, data)


def test_independent_sparse_and_empty_artifacts_do_not_create_fill_or_value_support(monkeypatch):
    monkeypatch.setattr(sequence, "_runtime", lambda: pytest.fail("native runtime"))
    for train in ((), (0,)):
        data = simple_data(training=train)
        result = oof.fit_unit(data, before_fit=lambda: None)
        assert reference.verify_fit(result, data)["verified"]
        if train:
            result["artifact"]["heads"]["net_immediate"]["known_rows"] += 1
            reseal(result)
            with pytest.raises(ValueError, match="known_rows"):
                reference.verify_fit(result, data)
