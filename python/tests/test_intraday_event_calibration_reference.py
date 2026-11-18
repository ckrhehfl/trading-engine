"""Invented ER independent reconstruction and deliberate mutation checks."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_calibration as calibration
from research import intraday_event_factorized_reference as ep_reference
from research import intraday_event_factorized_workflow as ep_workflow
from research import intraday_event_mean_baseline as baseline
from research import intraday_event_calibration_metrics as metrics
from research import intraday_event_calibration_reference as reference
from research import intraday_event_model_workflow as workflow
from test_intraday_event_factorized_model import query
from test_intraday_event_factorized_reference import zero_denominator_bundle
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction
from test_intraday_event_calibration import example
from test_intraday_event_mean_baseline import data_for, roster


def cash_rows(frozen, records, arm):
    # All three policies use the original exact saved-cash join.
    if arm == "mean":
        return baseline.join_policy_targets(frozen, records)
    if arm == "direct":
        return ep_workflow.join_policy_targets(frozen, records)
    return calibration.join_policy_targets(frozen, records)


def policy(candidates, forecasts, arm, episodes=None):
    events = tuple({c.episode.event_id: c.episode for c in candidates}.values()) if episodes is None else episodes
    return evaluation.freeze_policy(events, candidates, forecasts, control="model", model={"mean": "EQ-mean", "direct": "EP-direct", "calibrated": "ER-calibrated"}[arm],
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
    cash = {arm: cash_rows(policies[arm], records, arm)
            for arm in metrics.ARMS}
    report = metrics.summarize(CALENDAR, fold, reps, records, cash)
    return candidates, forecasts, reps, records, policies, report


def verify_bundle(values, fold=0):
    candidates, forecasts, reps, records, policies, report = values
    events = tuple(c.episode for c in candidates)
    reference.verify_representatives(CALENDAR, fold, events, candidates, forecasts, reps)
    for arm in metrics.ARMS:
        checker = ep_reference if arm == "direct" else reference
        checker.verify_policy(events, candidates, forecasts[arm], ep_workflow.serialize_policy(policies[arm]))
    return reference.verify_report(CALENDAR, fold, reps, records,
        {arm: ep_workflow.serialize_policy(policies[arm]) for arm in metrics.ARMS}, report)


def test_independent_three_arm_report_never_calls_producer(monkeypatch):
    values = bundle()
    monkeypatch.setattr(metrics, "summarize", lambda *a, **k: pytest.fail("producer report invoked"))
    monkeypatch.setattr(metrics, "select_representatives", lambda *a, **k: pytest.fail("producer selection invoked"))
    monkeypatch.setattr(baseline, "join_policy_targets", lambda *a, **k: pytest.fail("producer cash invoked"))
    monkeypatch.setattr(calibration, "join_policy_targets", lambda *a, **k: pytest.fail("producer calibrated cash invoked"))
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
    with pytest.raises(ValueError, match="calibrated/mean policy"):
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
    cash = {arm: cash_rows(policies[arm], records, arm)
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


@pytest.mark.parametrize("values,scores,phases", [
    ((0, 2, 10, 16), (0, 1, 20, 23), ("onset", "onset", "pullback", "pullback")),
    ((3, 1), (1, 3), ("onset", "onset")),
    ((3, 1), (2, 2), ("onset", "onset")),
    ((2, 2), (1, 3), ("onset", "onset")),
    ((-Fraction(1, 3), Fraction(1, 3)), (1, 1), ("onset", "onset")),
])
def test_independent_raw_moments_reconstruct_shared_slope(values, scores, phases, monkeypatch):
    data, targets, phase_map, forecasts, event = example(values, scores, phases)
    artifact = calibration.estimate(data, phase_map, targets, forecasts)
    direct = (prediction(event, 1, 2),)
    projected = calibration.project(data, roster((event,)), artifact, direct)
    monkeypatch.setattr(calibration, "estimate", lambda *a, **k: pytest.fail("producer estimator invoked"))
    monkeypatch.setattr(calibration, "project", lambda *a, **k: pytest.fail("producer projection invoked"))
    assert reference.verify_calibration(data, phase_map, targets, forecasts, artifact)["verified"]
    assert reference.verify_predictions(data, roster((event,)), artifact, direct, projected)["verified"]


@pytest.mark.parametrize("mutation", ["slope", "center", "intercept", "status", "joint", "mass", "oof", "hash"])
def test_calibration_artifact_mutation_rejected(mutation):
    data, targets, phases, forecasts, _ = example()
    artifact = calibration.estimate(data, phases, targets, forecasts)
    if mutation == "slope":
        artifact["slopes"]["net_immediate"]["beta"] = "2"
    elif mutation == "center":
        artifact["cells"]["onset"]["scores"]["net_immediate"] = "3"
    elif mutation == "intercept":
        artifact["cells"]["onset"]["targets"]["net_immediate"] = "1"
    elif mutation == "status":
        artifact["slopes"]["net_immediate"]["status"] = "nonidentified_zero_variance"
    elif mutation == "joint":
        artifact["joint"]["rows"] -= 1
    elif mutation == "mass":
        artifact["cells"]["onset"]["weight_mass"] = "1/2"
    elif mutation == "oof":
        artifact["oof"]["expected_rows"] -= 1
    else:
        artifact["oof_predictions_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="calibration"):
        reference.verify_calibration(data, phases, targets, forecasts, artifact)
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "future", "schema", "nonfinite", "session"])
def test_independent_oof_roster_guards(mutation):
    data, targets, phases, forecasts, _ = example()
    artifact = calibration.estimate(data, phases, targets, forecasts)
    changed = list(deepcopy(forecasts))
    if mutation == "missing":
        changed.pop()
    elif mutation == "duplicate":
        changed.append(changed[0])
    elif mutation == "future":
        changed.append(prediction(candidate(97)))
    elif mutation == "schema":
        changed[0]["predictions"].pop("fill")
    elif mutation == "nonfinite":
        changed[0]["predictions"]["net_immediate"] = float("inf")
    else:
        changed[0]["session"] = CALENDAR[97]
    with pytest.raises(ValueError):
        reference.verify_calibration(data, phases, targets, changed, artifact)


def test_independent_full_joint_precedes_oof_conditioning_and_missing_head_removes_both():
    data, targets, phases, forecasts, _ = example((0, 2, 10), (0, 1, 2), ("onset",) * 3)
    extra = replace(data.base.training[0], key="other", episode_id="other", family="A_DOWNFAIL")
    data, targets = data_for((*data.base.training, extra), (0, 2, 10, 100))
    phases[extra.key] = None
    forecasts = (*forecasts, {**prediction(candidate(49), 4, 4), "key": extra.key})
    forecasts[1]["predictions"]["net_wait3"] = None
    artifact = calibration.estimate(data, phases, targets, forecasts)
    assert Fraction(artifact["cells"]["onset"]["weight_mass"]) == Fraction(1, 2)
    assert artifact["joint"]["rows"] == 4 and artifact["oof"]["rows"] == 2
    assert reference.verify_calibration(data, phases, targets, forecasts, artifact)["verified"]


def test_projection_rounding_cannot_create_a_positive_commit_from_exact_zero():
    # Affine relation y=(x-1)/3 gives exactly zero at x=1. Separately rounded
    # xbar=3/2, ybar=1/6 and beta=1/3 would manufacture +5E-51 here.
    data, targets, phases, forecasts, event = example((-Fraction(1, 3), Fraction(2, 3)), (0, 3))
    artifact = calibration.estimate(data, phases, targets, forecasts)
    direct = (prediction(event, 1, 1),)
    projected = calibration.project(data, roster((event,)), artifact, direct)
    assert projected[0]["predictions"]["net_immediate"] == "0"
    assert projected[0]["predictions"]["net_wait3"] == "0"
    assert calibration.freeze_policy(data, roster((event,)), projected).choices[0].action == "no_commit"
    assert reference.verify_calibration(data, phases, targets, forecasts, artifact)["verified"]
    assert reference.verify_predictions(data, roster((event,)), artifact, direct, projected)["verified"]


def test_distinct_exact_affine_mappings_preserve_an_action_tie():
    data, targets, phases, forecasts, event = example((-Fraction(1, 3), Fraction(2, 3)), (0, 3))
    changed = tuple(replace(label, net_wait3=value) for label, value in zip(data.base.targets, (0, 1)))
    data = replace(data, base=replace(data.base, targets=changed))
    for label in changed:
        targets[label.key]["net_wait3"] = str(label.net_wait3)
    artifact = calibration.estimate(data, phases, targets, forecasts)
    direct = (prediction(event, 2, 1),)
    projected = calibration.project(data, roster((event,)), artifact, direct)
    values = projected[0]["predictions"]
    assert values["net_immediate"] == values["net_wait3"] == "0." + "3" * 50
    assert calibration.freeze_policy(data, roster((event,)), projected).choices[0].action == "immediate"
    assert reference.verify_calibration(data, phases, targets, forecasts, artifact)["verified"]
    assert reference.verify_predictions(data, roster((event,)), artifact, direct, projected)["verified"]


def test_projection_preserves_auxiliary_heads_and_unknowns_and_rejects_tampering():
    data, targets, phases, forecasts, event = example()
    other = query(200, session=CALENDAR[97], family="A_DOWNFAIL")
    held = query(201, session=CALENDAR[97], state="HELD_10")
    data = replace(data, base=replace(data.base, evaluation=(event.query, other, held)))
    artifact = calibration.estimate(data, phases, targets, forecasts)
    direct = [prediction(event, None, 1), {**prediction(event), "key": other.key},
              {**prediction(event), "key": held.key}]
    projected = calibration.project(data, roster((event,)), artifact, direct)
    assert projected[0]["predictions"]["net_immediate"] is None
    assert all(projected[index]["predictions"][head] is None for index in (1, 2) for head in reference.VALUES)
    assert all(row["predictions"]["fill"] == .5 for row in projected)
    assert reference.verify_predictions(data, roster((event,)), artifact, direct, projected)["verified"]
    changed = deepcopy(projected)
    changed[1]["predictions"]["fill"] = .2
    with pytest.raises(ValueError, match="calibrated predictions"):
        reference.verify_predictions(data, roster((event,)), artifact, direct, changed)


@pytest.mark.parametrize("field", ["exact_beta", "exact_intercept"])
def test_exact_coefficient_mutation_is_rejected(field):
    data, targets, phases, forecasts, _ = example()
    artifact = calibration.estimate(data, phases, targets, forecasts)
    if field == "exact_beta":
        artifact["slopes"]["net_immediate"]["exact_beta"] = ["2", "1"]
    else:
        artifact["cells"]["onset"]["exact_intercepts"]["net_immediate"] = ["0", "1"]
    with pytest.raises(ValueError, match="calibration"):
        reference.verify_calibration(data, phases, targets, forecasts, artifact)


def test_empty_calibration_support_preserves_unknown_mapping_and_forecast():
    data, targets, phases, forecasts, event = example()
    for row in forecasts:
        row["predictions"]["net_wait3"] = None
    artifact = calibration.estimate(data, phases, targets, forecasts)
    direct = (prediction(event),)
    projected = calibration.project(data, roster((event,)), artifact, direct)
    assert artifact["oof"]["rows"] == 0
    assert all(slope["beta"] is None and slope["exact_beta"] is None for slope in artifact["slopes"].values())
    assert all(projected[0]["predictions"][head] is None for head in reference.VALUES)
    assert reference.verify_calibration(data, phases, targets, forecasts, artifact)["verified"]
    assert reference.verify_predictions(data, roster((event,)), artifact, direct, projected)["verified"]


@pytest.mark.parametrize("field", ["targets", "action_targets"])
def test_duplicate_training_labels_cannot_pass_independent_reconstruction(field):
    data, targets, phases, forecasts, _ = example()
    artifact = calibration.estimate(data, phases, targets, forecasts)
    if field == "targets":
        data = replace(data, base=replace(data.base, targets=(*data.base.targets, data.base.targets[0])))
    else:
        data = replace(data, action_targets=(*data.action_targets, data.action_targets[0]))
    with pytest.raises(ValueError, match="roster"):
        reference.verify_calibration(data, phases, targets, forecasts, artifact)
