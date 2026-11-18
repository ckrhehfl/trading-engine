"""Invented EQ independent reconstruction and deliberate mutation checks."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_reference as ep_reference
from research import intraday_event_factorized_workflow as ep_workflow
from research import intraday_event_mean_baseline as baseline
from research import intraday_event_mean_metrics as metrics
from research import intraday_event_mean_reference as reference
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_workflow as workflow
from research import intraday_event_models as common
from test_intraday_event_factorized_model import query, labels, fills
from test_intraday_event_factorized_reference import zero_denominator_bundle
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction


def training_bundle():
    a = query(0, session=CALENDAR[0], episode="a", cluster="one", state="PULLBACK")
    other = query(1, session=CALENDAR[0], episode="b", cluster="two", family="A_DOWNFAIL", symbol="KRX:000002")
    b = query(2, session=CALENDAR[1], episode="c", cluster="three", state="PULLBACK")
    repeated = replace(b, key="key:3", decision_offset=73)
    sparse = query(4, session=CALENDAR[1], episode="d", cluster="four", state="CONFIRMED", symbol="KRX:000002")
    unknown = query(5, session=CALENDAR[2], state="PULLBACK")
    rows = (a, other, b, repeated, sparse, unknown)
    immediate = (1, 100, 3, 7, 11, 3)
    wait = (2, 200, 4, 8, 12, None)
    targets = tuple(labels(row, fill=1, immediate=x, wait=y) for row, x, y in zip(rows, immediate, wait))
    out = candidate()
    base = workflow.ModelData(CALENDAR, 0, "common_chart_peer", rows, targets, (out.query,),
                              {"training_seal": common.support_seal(rows)})
    data = ep_workflow.FactorizedData(base, tuple(fills(row, 1, 1) for row in rows))
    phases = {a.key: "pullback", other.key: None, b.key: "pullback", repeated.key: "pullback",
              sparse.key: "confirmation", unknown.key: "pullback"}
    exact = {row.key: {"net_immediate": str(x), "net_wait3": None if y is None else str(y)}
             for row, x, y in zip(rows, immediate, wait)}
    return data, phases, exact


def test_independent_exact_conditioning_does_not_rebalance_subset(monkeypatch):
    data, phases, exact = training_bundle()
    artifact = baseline.estimate(data, phases, exact)
    # C pullback weights 1/4, 1/8, 1/8 => (1/4 + 3/8 + 7/8)/(1/2) = 3.
    assert Fraction(artifact["cells"]["pullback"]["means"]["net_immediate"]) == 3
    assert Fraction(artifact["cells"]["confirmation"]["means"]["net_immediate"]) == 11
    assert artifact["cells"]["confirmation"]["precision"] == "unknown_small_support"
    assert artifact["cells"]["onset"]["means"]["net_immediate"] is None
    monkeypatch.setattr(baseline, "estimate", lambda *a, **k: pytest.fail("producer mean invoked"))
    assert reference.verify_baseline(data, phases, exact, artifact)["verified"]


@pytest.mark.parametrize("tamper", ["mean", "mass", "joint", "empty", "precision", "target_hash", "phase_hash"])
def test_baseline_mutations_are_rejected(tamper):
    data, phases, exact = training_bundle()
    artifact = baseline.estimate(data, phases, exact)
    if tamper == "mean":
        artifact["cells"]["pullback"]["means"]["net_immediate"] = "11/3"
    elif tamper == "mass":
        artifact["cells"]["pullback"]["weight_mass"] = "1"
    elif tamper == "joint":
        artifact["joint"]["rows"] -= 1
    elif tamper == "empty":
        artifact["cells"]["onset"]["means"]["net_immediate"] = "0"
    elif tamper == "precision":
        artifact["cells"]["confirmation"]["precision"] = "not_established"
    else:
        artifact["training_targets_sha256" if tamper == "target_hash" else "training_phases_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        reference.verify_baseline(data, phases, exact, artifact)


def test_baseline_rejects_unmatured_or_unpaired_training_changes():
    data, phases, exact = training_bundle()
    artifact = baseline.estimate(data, phases, exact)
    changed = replace(data, action_targets=(replace(data.action_targets[0], immediate_matured_session=CALENDAR[97]),
                                           *data.action_targets[1:]))
    with pytest.raises(ValueError, match="mature"):
        reference.verify_baseline(changed, phases, exact, artifact)
    exact[data.base.training[-1].key]["net_wait3"] = "0"
    with pytest.raises(ValueError, match="decoded training"):
        reference.verify_baseline(data, phases, exact, artifact)


def test_projection_checks_exact_decimal_and_auxiliary_unknowns(monkeypatch):
    data, phases, exact = training_bundle()
    c = candidate(state="PULLBACK", decision=36)
    non_c = query(10, session=CALENDAR[97], family="A_DOWNFAIL")
    data = replace(data, base=replace(data.base, evaluation=(c.query, non_c)))
    roster = inputs.DecisionRoster((c.episode,), (c,))
    artifact = baseline.estimate(data, phases, exact)
    predictions = baseline.project(data, roster, artifact)
    monkeypatch.setattr(baseline, "project", lambda *a, **k: pytest.fail("producer projection invoked"))
    assert reference.verify_predictions(data, roster, artifact, predictions)["verified"]
    assert all(value is None for value in predictions[1]["predictions"].values())
    changed = deepcopy(predictions)
    changed[0]["predictions"]["net_immediate"] = "3.00000000000000000000000000000000000000000000000001"
    with pytest.raises(ValueError, match="baseline predictions"):
        reference.verify_predictions(data, roster, artifact, changed)


def test_empty_joint_keeps_all_cells_unknown():
    data, phases, exact = training_bundle()
    data = replace(data, action_targets=tuple(fills(row, None, None) for row in data.base.training))
    artifact = baseline.estimate(data, phases, exact)
    assert reference.verify_baseline(data, phases, exact, artifact)["verified"]
    assert artifact["joint"]["rows"] == 0
    assert all(cell["status"] == "unsupported_empty_group" for cell in artifact["cells"].values())


def test_training_phase_reconstruction_uses_original_onset_not_coalesced_kind(monkeypatch):
    c = candidate(0, decision=36)
    base = workflow.ModelData(CALENDAR, 0, "common_chart_peer", (c.query,), (), (), {})
    data = ep_workflow.FactorizedData(base, ())
    onset = {"kind": "event_state", "session": c.episode.session, "symbol": c.episode.symbol,
             "frame": {"event_id": c.episode.event_id, "overlap_group_id": c.episode.cluster_id,
                       "family": c.episode.family, "decision_offset": 36, "kind": "at_onset"}}
    shards = [("invented", {"session_indices": [0, 120]})]
    monkeypatch.setattr(inputs, "_rows", lambda *args: iter((onset,)))
    assert reference.verify_training_phases(shards, data, {c.key: "onset"})["verified"]
    with pytest.raises(ValueError, match="training phases"):
        reference.verify_training_phases(shards, data, {c.key: "waiting_pullback"})
    onset["frame"]["decision_offset"] = 37
    with pytest.raises(ValueError, match="original onset"):
        reference.verify_training_phases(shards, data, {c.key: "onset"})


def test_training_exact_targets_are_checked_against_original_prefix(monkeypatch):
    c = candidate(0)
    raw = label_record(c)
    base = workflow.ModelData(CALENDAR, 0, "common_chart_peer", (c.query,), (), (), {})
    data = ep_workflow.FactorizedData(base, ())
    onset = {"kind": "event_state", "session": c.episode.session, "symbol": c.episode.symbol,
             "frame": {"event_id": c.episode.event_id, "overlap_group_id": c.episode.cluster_id,
                       "family": c.episode.family, "decision_offset": 33, "kind": "at_onset"}}
    shards = [("invented", {"session_indices": [0, 120]})]
    monkeypatch.setattr(inputs, "_rows", lambda *args: iter((onset,)))

    def training_labels(slot, manifest, cutoff):
        assert cutoff == CALENDAR[97]
        yield raw

    monkeypatch.setattr(inputs, "training_labels", training_labels)
    exact = {c.key: {head: raw["training_labels"][head] for head in reference.VALUES}}
    assert reference.verify_training_inputs(shards, data, {c.key: "onset"}, exact)["verified"]
    exact[c.key]["net_wait3"] = "100"
    with pytest.raises(ValueError, match="exact training targets"):
        reference.verify_training_inputs(shards, data, {c.key: "onset"}, exact)


def policy(candidates, forecasts, arm, episodes=None):
    events = tuple({c.episode.event_id: c.episode for c in candidates}.values()) if episodes is None else episodes
    return evaluation.freeze_policy(events, candidates, forecasts, control="model", model=("EQ-" if arm == "mean" else "EP-") + arm,
        scope="common_chart_peer", prediction_binding=sha256(workflow.encode(forecasts)).hexdigest())


def bundle(fold=0):
    first = 97 + fold * 40
    candidates = [candidate(first), candidate(first + 1), candidate(first + 2)]
    forecasts = {arm: [prediction(c, .01 if arm == "mean" else .02 * (i + 1), .01)
                       for i, c in enumerate(candidates)] for arm in metrics.ARMS}
    events = tuple(c.episode for c in candidates)
    reps = metrics.select_representatives(CALENDAR, fold, events, candidates, forecasts)
    records = [label_record(c) for c in candidates]
    for index, record in enumerate(records):
        for action in ("immediate", "wait3"):
            cash = Fraction(index - 1)
            value = cash / Fraction(record["targets"][action]["denominator"])
            record["targets"][action].update(net_cash=str(cash), value=str(value))
            record["training_labels"]["net_" + action] = str(value)
    policies = {arm: policy(candidates, forecasts[arm], arm) for arm in metrics.ARMS}
    cash = {arm: (baseline if arm == "mean" else ep_workflow).join_policy_targets(policies[arm], records)
            for arm in metrics.ARMS}
    report = metrics.summarize(CALENDAR, fold, reps, records, cash)
    return candidates, forecasts, reps, records, policies, report


def verify_bundle(values, fold=0):
    candidates, forecasts, reps, records, policies, report = values
    events = tuple(c.episode for c in candidates)
    reference.verify_representatives(CALENDAR, fold, events, candidates, forecasts, reps)
    for arm in metrics.ARMS:
        checker = reference if arm == "mean" else ep_reference
        checker.verify_policy(events, candidates, forecasts[arm], ep_workflow.serialize_policy(policies[arm]))
    return reference.verify_report(CALENDAR, fold, reps, records,
        {arm: ep_workflow.serialize_policy(policies[arm]) for arm in metrics.ARMS}, report)


def test_independent_three_arm_report_never_calls_producer(monkeypatch):
    values = bundle()
    monkeypatch.setattr(metrics, "summarize", lambda *a, **k: pytest.fail("producer report invoked"))
    monkeypatch.setattr(metrics, "select_representatives", lambda *a, **k: pytest.fail("producer selection invoked"))
    monkeypatch.setattr(baseline, "join_policy_targets", lambda *a, **k: pytest.fail("producer cash invoked"))
    monkeypatch.setattr(evaluation, "freeze_policy", lambda *a, **k: pytest.fail("producer policy invoked"))
    assert verify_bundle(values)["verified"]
    head = values[-1]["cells"][0]["arms"]["mean"]["net_immediate"]
    assert head["cross_cluster"]["auc"]["value"] == .5
    assert any(row["fills"] == 0 for row in values[-1]["policy"]["arms"]["mean"]["weekly_fills"])


@pytest.mark.parametrize("tamper", ["phase", "paired", "calibration", "ranking", "week", "comparison", "cash"])
def test_report_and_exact_cash_mutations_rejected(tamper):
    values = bundle()
    report = values[-1]
    head = report["cells"][0]["arms"]["mean"]["net_immediate"]
    if tamper == "phase":
        report["cells"].pop()
    elif tamper == "paired":
        report["cells"][0]["paired_known"] -= 1
    elif tamper == "calibration":
        head["calibration"]["mae"] += .01
    elif tamper == "ranking":
        head["cross_cluster"]["auc"]["value"] = 1
    elif tamper == "week":
        report["policy"]["arms"]["mean"]["weekly_fills"].pop()
    elif tamper == "comparison":
        report["policy"]["comparisons"]["direct_minus_mean"]["paired_cash_difference"] = "1"
    else:
        report["policy_rows"]["mean"][0]["net_cash"] = "1/100000000000000000000000000000000000"
    with pytest.raises(ValueError):
        verify_bundle(values)


def test_mean_policy_first_positive_tie_and_earlier_unknown_preserved():
    candidates = [candidate(), candidate(decision=36), candidate(decision=39)]
    forecasts = [prediction(candidates[0], None, 1), prediction(candidates[1], .1, .1), prediction(candidates[2], 5, 6)]
    saved = ep_workflow.serialize_policy(policy(candidates, forecasts, "mean"))
    assert reference.verify_policy((candidates[0].episode,), candidates, forecasts, saved)["verified"]
    saved["choices"][0]["action"] = "wait3"
    with pytest.raises(ValueError, match="mean policy"):
        reference.verify_policy((candidates[0].episode,), candidates, forecasts, saved)
    forecasts[1]["predictions"].update(net_immediate=-1, net_wait3=-1)
    forecasts[2]["predictions"].update(net_immediate=-1, net_wait3=-1)
    frozen = policy(candidates, forecasts, "mean")
    records = [label_record(c) for c in candidates]
    actual = baseline.join_policy_targets(frozen, records)
    expected = reference._policy_cash(ep_workflow.serialize_policy(frozen), {row["key"]: row for row in records})
    assert actual == tuple(expected)
    assert actual[0]["net_cash"] is None


@pytest.mark.parametrize("action", ["immediate", "wait3"])
def test_mean_zero_original_denominator_preserves_cash_without_fabricated_value(action):
    candidates, old_forecasts, _, _, records, _, _ = zero_denominator_bundle(action)
    forecasts = {arm: deepcopy(old_forecasts) for arm in metrics.ARMS}
    events = tuple(c.episode for c in candidates)
    reps = metrics.select_representatives(CALENDAR, 0, events, candidates, forecasts)
    policies = {arm: policy(candidates, forecasts[arm], arm) for arm in metrics.ARMS}
    cash = {arm: (baseline if arm == "mean" else ep_workflow).join_policy_targets(policies[arm], records)
            for arm in metrics.ARMS}
    report = metrics.summarize(CALENDAR, 0, reps, records, cash)
    assert verify_bundle((candidates, forecasts, reps, records, policies, report))["verified"]
    assert cash["mean"][0]["value"] == ("0" if action == "immediate" else None)


def test_four_fold_pool_keeps_all_cells_and_zero_weeks():
    reports = []
    for fold in range(4):
        values = bundle(fold)
        assert verify_bundle(values, fold)["verified"]
        reports.append(values[-1])
    saved = metrics.pool(CALENDAR, reports)
    assert reference.verify_pool(CALENDAR, reports, saved)["cells"] == 16
    saved["cells"].pop()
    with pytest.raises(ValueError, match="pool"):
        reference.verify_pool(CALENDAR, reports, saved)
