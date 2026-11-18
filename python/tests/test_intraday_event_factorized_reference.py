"""Invented independent EP witnesses; no native optimizer or private reads."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from itertools import accumulate
import random

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_factorized_metrics as metrics
from research import intraday_event_factorized_model as learner
from research import intraday_event_factorized_reference as reference
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_model_workflow as old_workflow
from research import intraday_event_models as common
from research import intraday_event_sequence_model as sequence
from test_intraday_event_factorized_workflow import CALENDAR, candidate, label_record, prediction
from test_intraday_event_factorized_model import query, labels, fills


def policy(candidates, forecasts, arm="direct", episodes=None):
    events = tuple({c.episode.event_id: c.episode for c in candidates}.values()) if episodes is None else episodes
    return evaluation.freeze_policy(events, candidates, forecasts, control="model", model="EP-" + arm,
        scope="common_chart_peer", prediction_binding=sha256(old_workflow.encode(forecasts)).hexdigest())


def bundle(fold=0):
    first = 97 + fold * 40
    second = candidate(first, decision=36, event="other")
    second = replace(second, episode=replace(second.episode, onset_decision_offset=36))
    candidates = [candidate(first), second, candidate(first + 1), candidate(first + 2)]
    forecasts = [prediction(c, .02 * (i + 1), .01) for i, c in enumerate(candidates)]
    events = tuple(c.episode for c in candidates)
    parts = [{"key": c.key, "session": c.episode.session, "components": {"fill_wait3": .6}} for c in candidates]
    reps = metrics.select_representatives(CALENDAR, fold, events, candidates,
        {arm: forecasts for arm in metrics.ARMS}, components_by_arm={arm: parts for arm in metrics.ARMS})
    records = [label_record(c) for c in candidates]
    for index, record in enumerate(records):
        for action in ("immediate", "wait3"):
            cash = Fraction(index - 1)
            value = cash / Fraction(record["targets"][action]["denominator"])
            record["targets"][action].update(net_cash=str(cash), value=str(value))
            record["training_labels"]["net_" + action] = str(value)
    policies = {arm: policy(candidates, forecasts, arm) for arm in metrics.ARMS}
    cash = {arm: workflow.join_policy_targets(policies[arm], records) for arm in metrics.ARMS}
    report = metrics.summarize(CALENDAR, fold, reps, records, cash)
    return candidates, forecasts, parts, reps, records, policies, report


def verify_bundle(values, fold=0):
    candidates, forecasts, parts, reps, records, policies, report = values
    events = tuple(c.episode for c in candidates)
    reference.verify_representatives(CALENDAR, fold, events, candidates,
        {arm: forecasts for arm in metrics.ARMS}, reps, components_by_arm={arm: parts for arm in metrics.ARMS})
    for arm in metrics.ARMS:
        reference.verify_policy(events, candidates, forecasts, workflow.serialize_policy(policies[arm]))
    return reference.verify_report(CALENDAR, fold, reps, records,
        {arm: workflow.serialize_policy(policies[arm]) for arm in metrics.ARMS}, report)


def test_independent_report_checks_pairs_same_cluster_exclusion_and_zero_weeks(monkeypatch):
    values = bundle()
    monkeypatch.setattr(metrics, "summarize", lambda *a, **k: pytest.fail("producer report called"))
    monkeypatch.setattr(metrics, "join_targets", lambda *a, **k: pytest.fail("producer join called"))
    monkeypatch.setattr(metrics, "select_representatives", lambda *a, **k: pytest.fail("producer selection called"))
    monkeypatch.setattr(workflow, "join_policy_targets", lambda *a, **k: pytest.fail("producer policy join called"))
    monkeypatch.setattr(evaluation, "freeze_policy", lambda *a, **k: pytest.fail("producer policy called"))
    assert verify_bundle(values)["verified"]
    report = values[-1]
    assert report["cells"][0]["arms"]["direct"]["net_immediate"]["cross_cluster"]["excluded_same_cluster_pairs"] == 1
    assert any(row["fills"] == 0 for row in report["policy"]["arms"]["direct"]["weekly_fills"])


@pytest.mark.parametrize("field", ["net_cash", "denominator", "matured_offset", "fill_matured_offset", "value"])
def test_raw_action_tampering_rejected(field):
    values = bundle()
    values[4][0]["targets"]["wait3"][field] = 100 if "offset" in field else "999"
    with pytest.raises(ValueError):
        verify_bundle(values)


def test_training_label_and_exact_policy_cash_tampering_rejected():
    values = bundle()
    values[4][0]["training_labels"]["net_immediate"] = "7"
    with pytest.raises(ValueError, match="training label"):
        verify_bundle(values)
    values = bundle()
    values[-1]["policy_rows"]["direct"][0]["net_cash"] = "1/100000000000000000000000000000000000"
    with pytest.raises(ValueError, match="policy_rows"):
        verify_bundle(values)


@pytest.mark.parametrize("location", ["phase", "paired", "calibration", "exclusion", "fill", "week"])
def test_report_selection_statistics_and_denominator_tampering_rejected(location):
    values = bundle()
    report = values[-1]
    head = report["cells"][0]["arms"]["direct"]["net_immediate"]
    if location == "phase":
        report["cells"].pop()
    elif location == "paired":
        report["cells"][0]["paired_known"] -= 1
    elif location == "calibration":
        head["calibration"]["mae"] += .01
    elif location == "exclusion":
        head["cross_cluster"]["excluded_same_cluster_pairs"] = 0
    elif location == "fill":
        head["fill"]["brier"] = 0
    else:
        report["policy"]["arms"]["direct"]["weekly_fills"].pop()
    with pytest.raises(ValueError):
        verify_bundle(values)


def test_earliest_representative_survives_absent_score_and_target():
    first, later = candidate(decision=36), candidate(decision=39)
    forecasts = [prediction(later, .1, .2)]
    reps = metrics.select_representatives(CALENDAR, 0, (first.episode,), [first, later],
        {arm: forecasts for arm in metrics.ARMS})
    assert reference.verify_representatives(CALENDAR, 0, (first.episode,), [first, later],
        {arm: forecasts for arm in metrics.ARMS}, reps)["verified"]
    selected = next(row for row in reps if row["phase"] == "waiting_pullback")
    assert selected["key"] == first.key
    selected["key"] = later.key
    with pytest.raises(ValueError, match="representatives"):
        reference.verify_representatives(CALENDAR, 0, (first.episode,), [first, later],
            {arm: forecasts for arm in metrics.ARMS}, reps)


def test_policy_tie_first_positive_one_commit_and_missing_heads():
    candidates = [candidate(), candidate(decision=36), candidate(decision=39)]
    forecasts = [prediction(candidates[0], None, 1), prediction(candidates[1], .1, .1), prediction(candidates[2], 5, 6)]
    saved = workflow.serialize_policy(policy(candidates, forecasts))
    assert reference.verify_policy((candidates[0].episode,), candidates, forecasts, saved)["verified"]
    assert saved["choices"][0]["action"] == "immediate"
    assert saved["choices"][0]["candidate"]["frame"]["decision_offset"] == 36
    saved["choices"][0]["action"] = "wait3"
    with pytest.raises(ValueError, match="policy"):
        reference.verify_policy((candidates[0].episode,), candidates, forecasts, saved)


def test_pool_retains_sixteen_cells_and_original_calendar():
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


def fit_fixture(arm="factorized"):
    """Fabricate explicit saved native metadata, without invoking a fit."""
    a = query(0, session=CALENDAR[0], episode="a", cluster="one", family="A_DOWNFAIL")
    b = replace(a, key="key:1", decision_offset=73)
    c = query(2, session=CALENDAR[0], episode="c", cluster="two", symbol="KRX:000002")
    d = query(3, session=CALENDAR[1], episode="d", cluster="three")
    rows = (a, b, c, d)
    actions = tuple(fills(row, int(i in (0, 3)), int(i in (2, 3))) for i, row in enumerate(rows))
    targets = tuple(labels(row, path=i % 3, fill=action.immediate,
        immediate=9 if i == 3 else 0, wait=6 if i == 2 else 0) for i, (row, action) in enumerate(zip(rows, actions)))
    heads, bases, weights, _, support = learner._head_plan(arm, rows,
        {label.key: label for label in targets}, {action.key: action for action in actions})
    _, diagnostics = learner._diagnostic_plan(rows, heads, weights)
    proposal = learner._proposal(bases)
    rng, digest, hits, sampled = random.Random(sequence.SEED), sha256(), {h: 0 for h in weights}, {h: set() for h in weights}
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
    diagnostics.update(completed_updates=sequence.UPDATES, proposal_sha256=sha256(sequence._json(proposal)).hexdigest(),
        sampled_indices_sha256=digest.hexdigest(), initial_state_sha256="a" * 64)
    out = query(9, session=CALENDAR[97])
    base = old_workflow.ModelData(CALENDAR, 0, "common_chart_peer", rows, targets, (out,),
                                  {"training_seal": common.support_seal(rows)})
    data = workflow.FactorizedData(base, actions)
    artifact = {"arm": arm, "parameters": sequence.PARAMETERS, "normalization": sequence._normalizer(rows, "chart_peer"),
        "heads": heads, "action_training_support": support, "training_diagnostics": diagnostics,
        "training_rows": len(rows), "eval_first_session": CALENDAR[97], "training_sessions": sorted({r.session for r in rows}),
        "training_key_hashes": sorted(sha256(r.key.encode()).hexdigest() for r in rows),
        "common_support_sha256": base.support["training_seal"],
        "fit_head_count": len(weights), "fit_attempt_head_count": len(weights), "native_fit_head_count": len(weights),
        "native_joint_fit_count": 1, "native_updates": sequence.UPDATES,
        "constant_head_count": sum(h["status"] == "constant_single_class" for h in heads.values()),
        "unsupported_head_count": sum(h["status"] == "unsupported" for h in heads.values()), "failed_head_count": 0}
    forecast = {"key": out.key, "session": out.session,
        "predictions": {"path_class": [.2, .5, .3], "fill": .25, "net_immediate": 2., "net_wait3": -1., "remaining_gross": None}}
    parts = {"key": out.key, "session": out.session,
        "components": {"fill_wait3": .5, "conditional_net_immediate": 8. if arm == "factorized" else None,
                       "conditional_net_wait3": -2. if arm == "factorized" else None}}
    result = {"artifact": artifact, "arm": arm, "predictions": [forecast], "components": [parts]}
    reseal(result)
    return result, data


def reseal(result):
    for name in ("artifact", "predictions", "components"):
        result[name + "_sha256"] = sha256(old_workflow.encode(result[name])).hexdigest()


def test_fit_independently_checks_hierarchy_conditioning_products_and_draws(monkeypatch):
    result, data = fit_fixture()
    monkeypatch.setattr(learner, "_head_plan", lambda *a: pytest.fail("producer plan called"))
    monkeypatch.setattr(learner, "predict", lambda *a: pytest.fail("producer inference called"))
    monkeypatch.setattr(sequence, "_normalizer", lambda *a: pytest.fail("producer normalizer called"))
    assert reference.verify_fit(result, data)["native_optimizer_reimplemented"] is False


@pytest.mark.parametrize("tamper", ["normalizer", "proposal", "sample", "joint", "product", "action", "roster"])
def test_fit_tampering_rejected_even_with_resealed_bytes(tamper):
    result, data = fit_fixture()
    if tamper == "normalizer":
        result["artifact"]["heads"]["net_immediate"]["target_normalization"]["mean"] = 4.5
    elif tamper == "proposal":
        result["artifact"]["training_diagnostics"]["proposal_sha256"] = "0" * 64
    elif tamper == "sample":
        result["artifact"]["training_diagnostics"]["sampled_indices_sha256"] = "0" * 64
    elif tamper == "joint":
        result["artifact"]["action_training_support"]["joint_known_rows"] -= 1
    elif tamper == "product":
        result["predictions"][0]["predictions"]["net_immediate"] = 4.
    elif tamper == "action":
        result["components"][0]["components"]["fill_wait3"] = .25
    else:
        result["predictions"].pop()
    reseal(result)
    with pytest.raises(ValueError):
        reference.verify_fit(result, data)


def test_matched_common_proposal_and_conditional_normalizers_are_distinct():
    direct, _ = fit_fixture("direct")
    factor, _ = fit_fixture("factorized")
    assert reference.verify_matched({"direct": direct, "factorized": factor})["verified"]
    factor["artifact"]["training_diagnostics"]["initial_state_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="initial_state"):
        reference.verify_matched({"direct": direct, "factorized": factor})
