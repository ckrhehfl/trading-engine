"""Invented ES response adaptation and exact training-only controls."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F

import pytest

from research import intraday_event_factorized_model as matched
from research import intraday_event_mean_baseline as baseline
from research import intraday_event_models as models
from research import intraday_event_success as success
from test_intraday_event_factorized_workflow import CALENDAR, candidate
from test_intraday_event_mean_baseline import data_for, roster


def test_exact_response_preserves_original_joint_training_and_auxiliary_targets():
    positive, zero, no_fill, no_cash, no_fill_evidence = (candidate(index).query for index in range(5))
    held = replace(positive, key="held", episode_id="held-book", original_episode_id=positive.episode_id,
                   state="HELD_10", position=(1 / 3, 2 / 3, 0))
    rows = (positive, zero, no_fill, no_cash, no_fill_evidence, held)
    eval_row = candidate().query
    tiny = F(1, 10**330)
    data, exact = data_for(rows, (tiny, 0, 0, 5, 9, None), evaluation_rows=(eval_row,),
                          fills=((1, 1), (1, 1), (0, 0), (1, 1), (1, None), (None, None)))
    first, second, third, fourth, fifth, held_label = data.base.targets
    data = replace(data, base=replace(data.base, targets=(
        replace(first, net_immediate=float(tiny), net_wait3=float(-tiny)),
        replace(second, net_wait3=4), replace(third, fill=0), replace(fourth, net_wait3=None, path_class=2), fifth,
        replace(held_label, remaining_gross=F(2, 3), matured_session=held.session))))
    exact[positive.key]["net_wait3"] = str(-tiny)
    exact[zero.key]["net_wait3"] = "4"
    exact[no_cash.key]["net_wait3"] = None
    phases = {row.key: None if row == held else "onset" for row in rows}

    before = deepcopy(data)
    assert data.base.targets[0].net_immediate == data.base.targets[0].net_wait3 == 0.0
    adapted, artifact = success.prepare(data, phases, exact)
    assert data == before
    assert adapted.action_targets is data.action_targets
    assert replace(adapted.base, targets=data.base.targets) == data.base
    assert adapted.base.evaluation == (eval_row,)
    expected = dict(zip((row.key for row in rows), ((1, 0), (0, 1), (0, 0), (None, None), (None, None), (None, None))))
    assert [(label.net_immediate, label.net_wait3) for label in adapted.base.targets] == list(expected.values())
    assert adapted.base.targets[3].path_class == 2
    assert adapted.base.targets[-1] == data.base.targets[-1]
    assert artifact["training_success_sha256"] == baseline._hash({key: dict(zip(models.Q_HEADS, pair))
                                                                  for key, pair in expected.items()})
    assert artifact["original_q_support"] == models.training_q_support(data.base.targets)
    assert artifact["original_q_support"]["original_known_rows"] == {"net_immediate": 5, "net_wait3": 4}
    assert artifact["joint"] == {"rows": 3, "dates": 3, "clusters": 3, "episodes": 3, "weight_sum": "1"}
    assert artifact["cells"]["onset"]["rates"] == dict.fromkeys(models.Q_HEADS, "1/3")

    def plan(fold):
        ordered_rows, labels = models.validate_training(fold.base.training, fold.base.targets, CALENDAR[97],
            mode="chart_peer", support_binding=fold.base.support["training_seal"])
        fills = matched.validate_action_fills(ordered_rows, labels, fold.action_targets, CALENDAR[97])
        return matched._head_plan("direct", ordered_rows, labels, fills)

    _, original_bases, _, _, original_support = plan(data)
    _, adapted_bases, _, _, adapted_support = plan(adapted)
    assert adapted_bases == original_bases
    assert adapted_support["joint_support_sha256"] == original_support["joint_support_sha256"]


def test_full_joint_weights_precede_conditioning_and_projection_keeps_original_roster():
    first = candidate(0).query
    later = replace(first, key="later", decision_offset=36)
    other = replace(first, key="other", episode_id="other", family="A_DOWNFAIL")
    next_day = candidate(1).query
    onset, waiting, confirmation = candidate(state="CONFIRMED"), candidate(decision=36), candidate(decision=39, state="CONFIRMED")
    other_eval = replace(onset.query, key="other-eval", episode_id="other-eval", family="A_DOWNFAIL")
    eval_rows = (confirmation.query, other_eval, onset.query, waiting.query)
    data, exact = data_for((first, later, other, next_day), (1, 9, 50, -1), evaluation_rows=eval_rows)
    phases = {first.key: "onset", later.key: "waiting_pullback", other.key: None, next_day.key: "onset"}
    _, artifact = success.prepare(data, phases, exact)
    assert artifact["cells"]["onset"] == {"rows": 2, "dates": 2, "clusters": 2, "episodes": 2, "weight_mass": "5/8",
        "status": "estimated", "precision": "not_established", "rates": dict.fromkeys(models.Q_HEADS, "1/5")}
    assert artifact["cells"]["waiting_pullback"]["precision"] == "unknown_small_support"
    assert artifact["cells"]["confirmation"]["weight_mass"] == "0"
    causal = roster((onset, waiting, confirmation))
    outputs = success.project(data, causal, artifact)
    assert [row["key"] for row in outputs] == [row.key for row in eval_rows]
    assert [row["predictions"]["net_immediate"] for row in outputs] == [None, None, "1/5", "1"]
    assert all(row["predictions"]["path_class"] is None for row in outputs)
    assert [row["head_status"]["net_wait3"] for row in outputs] == ["unsupported_empty_group",
        "not_applicable_to_baseline", "estimated_training_phase_success_rate", "estimated_training_phase_success_rate"]
    artifact["cells"]["confirmation"]["rates"]["net_immediate"] = "0"
    with pytest.raises(ValueError, match="invalid exact training rate"):
        success.project(data, causal, artifact)


def test_training_identity_and_exact_targets_are_admitted_before_adaptation():
    row = candidate(0).query
    data, exact = data_for((row,), (F(1, 3),))
    with pytest.raises(ValueError, match="complete training identities"):
        success.prepare(data, {}, exact)
    with pytest.raises(ValueError, match="exact/decoded training support"):
        success.prepare(data, {row.key: "onset"}, {row.key: {"net_immediate": "1/3", "net_wait3": None}})
    with pytest.raises(ValueError, match="exact/decoded training value"):
        success.prepare(data, {row.key: "onset"}, {row.key: dict.fromkeys(models.Q_HEADS, "2/6")})
    late = replace(data, action_targets=(replace(data.action_targets[0], wait3_matured_session=CALENDAR[97]),))
    with pytest.raises(ValueError, match="matured strictly before evaluation"):
        success.prepare(late, {row.key: "onset"}, exact)


def test_empty_training_support_remains_unestimated():
    event = candidate()
    data, exact = data_for(evaluation_rows=(event.query,))
    adapted, artifact = success.prepare(data, {}, exact)
    assert data == adapted
    assert artifact["joint"]["weight_sum"] == "0"
    assert all(cell["rates"] == dict.fromkeys(models.Q_HEADS) for cell in artifact["cells"].values())
    assert success.project(data, roster((event,)), artifact)[0]["predictions"] == dict.fromkeys(models.HEADS)


def test_known_no_fill_cannot_disguise_nonzero_exact_cash_at_float_boundary():
    row = candidate(0).query
    data, exact = data_for((row,), (0.0,), fills=((1, 0),))
    exact[row.key]["net_wait3"] = str(F(1, 10**330))
    with pytest.raises(ValueError, match="known no-fill action requires exact zero"):
        success.prepare(data, {row.key: "onset"}, exact)
