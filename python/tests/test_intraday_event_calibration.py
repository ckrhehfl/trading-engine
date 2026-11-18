"""Invented ER calibration algebra, chronology and unchanged policy accounting."""

from copy import deepcopy
from dataclasses import replace
from decimal import localcontext
from fractions import Fraction as F

import pytest

from research import intraday_event_calibration as calibration
from research import intraday_event_models as models
from test_intraday_event_factorized_workflow import candidate, prediction, label_record
from test_intraday_event_mean_baseline import data_for, roster


def example(values=(-1, 1), scores=(1, 3), phases=("onset", "onset")):
    candidates = tuple(candidate(49+i) for i in range(len(values)))
    event = candidate()
    data, targets = data_for(tuple(c.query for c in candidates), values, evaluation_rows=(event.query,))
    phase_map = {c.key: phase for c, phase in zip(candidates, phases)}
    forecasts = tuple(prediction(c, x, x) for c, x in zip(candidates, scores))
    return data, targets, phase_map, forecasts, event


def test_oof_affine_offset_corrects_overestimate_without_changing_policy_contract():
    data, targets, phases, forecasts, event = example()
    artifact = calibration.estimate(data, phases, targets, forecasts)
    assert artifact["slopes"]["net_immediate"]["beta"] == "1"
    assert artifact["cells"]["onset"]["scores"]["net_immediate"] == "2"
    assert artifact["cells"]["onset"]["targets"]["net_immediate"] == "0"
    projected = calibration.project(data, roster((event,)), artifact, (prediction(event, 1, 1),))
    assert projected[0]["predictions"]["net_immediate"] == "-1"
    assert projected[0]["predictions"]["fill"] == 0.5
    frozen = calibration.freeze_policy(data, roster((event,)), projected)
    assert frozen.model == "ER-calibrated" and frozen.choices[0].action == "no_commit"
    assert calibration.join_policy_targets(frozen, ())[0]["net_cash"] == "0"


def test_slope_is_shared_after_phase_centering_not_raw_cross_phase_regression():
    data, targets, phases, forecasts, _ = example((0, 2, 10, 16), (0, 1, 20, 23),
        ("onset", "onset", "pullback", "pullback"))
    artifact = calibration.estimate(data, phases, targets, forecasts)
    assert artifact["slopes"]["net_immediate"]["beta"] == "2"
    assert artifact["cells"]["pullback"]["targets"]["net_immediate"] == "13"


@pytest.mark.parametrize("scores,values,status", [
    ((1, 3), (3, 1), "constrained_zero"),
    ((2, 2), (3, 1), "nonidentified_zero_variance"),
    ((1, 3), (2, 2), "constrained_zero"),
])
def test_zero_slope_reasons_are_distinct_and_never_score_inversion(scores, values, status):
    data, targets, phases, forecasts, event = example(values, scores)
    artifact = calibration.estimate(data, phases, targets, forecasts)
    assert artifact["slopes"]["net_immediate"]["status"] == status
    assert artifact["slopes"]["net_immediate"]["beta"] == "0"
    projected = calibration.project(data, roster((event,)), artifact, (prediction(event, 100, -100),))
    assert projected[0]["predictions"]["net_immediate"] == "2"
    frozen = calibration.freeze_policy(data, roster((event,)), projected)
    assert frozen.choices[0].action == "immediate"
    cash = calibration.join_policy_targets(frozen, (label_record(event),))[0]
    assert cash["arm"] == "calibrated" and cash["schema"] == "ER-policy-cash-v1"
    assert F(cash["net_cash"]) < 0  # Same existing cost, not the invented positive forecast.


def test_one_missing_oof_head_excludes_both_training_heads_but_not_its_causal_row():
    data, targets, phases, forecasts, _ = example()
    forecasts = deepcopy(forecasts)
    forecasts[0]["predictions"]["net_wait3"] = None
    artifact = calibration.estimate(data, phases, targets, forecasts)
    assert artifact["oof"]["expected_rows"] == 2 and artifact["oof"]["rows"] == 1
    assert artifact["cells"]["onset"]["targets"]["net_immediate"] == "1"
    assert artifact["cells"]["confirmation"]["scores"]["net_immediate"] is None


def test_empty_support_never_falls_back_and_original_unknown_never_becomes_zero():
    data, targets, phases, forecasts, event = example()
    forecasts = deepcopy(forecasts)
    for row in forecasts:
        row["predictions"]["net_wait3"] = None
    artifact = calibration.estimate(data, phases, targets, forecasts)
    assert artifact["slopes"]["net_immediate"]["beta"] is None
    projected = calibration.project(data, roster((event,)), artifact, (prediction(event, 1, None),))
    assert projected[0]["predictions"]["net_immediate"] is None
    assert projected[0]["predictions"]["net_wait3"] is None
    cash = calibration.join_policy_targets(calibration.freeze_policy(data, roster((event,)), projected), ())
    assert cash[0]["net_cash"] is None


@pytest.mark.parametrize("change", ["missing", "duplicate", "future", "early", "nan", "bad_session"])
def test_oof_roster_and_finite_value_guards(change):
    data, targets, phases, forecasts, _ = example()
    forecasts = list(deepcopy(forecasts))
    if change == "missing":
        forecasts.pop()
    elif change == "duplicate":
        forecasts.append(deepcopy(forecasts[0]))
    elif change in ("future", "early"):
        forecasts.append(prediction(candidate(97 if change == "future" else 48), 1, 1))
    elif change == "nan":
        forecasts[0]["predictions"]["net_immediate"] = float("nan")
    else:
        forecasts[0]["session"] = forecasts[1]["session"]
    with pytest.raises(ValueError):
        calibration.estimate(data, phases, targets, forecasts)


def test_full_joint_weights_precede_oof_conditioning():
    data, targets, phases, forecasts, _ = example((0, 2, 10), (0, 1, 2), ("onset",) * 3)
    original = data.base.training[0]
    extra = replace(original, key="other", episode_id="other", family="A_DOWNFAIL")
    data, targets = data_for((*data.base.training, extra), (0, 2, 10, 100))
    phases[extra.key] = None
    forecasts = (*forecasts, {**prediction(candidate(49), 4, 4), "key": extra.key})
    artifact = calibration.estimate(data, phases, targets, forecasts)
    assert F(artifact["cells"]["onset"]["weight_mass"]) == F(5, 6)
    assert artifact["joint"]["rows"] == 4 and artifact["oof"]["rows"] == 3


def test_exact_zero_and_identical_action_tie_survive_ambient_decimal_context():
    data, targets, phases, forecasts, event = example((-F(1, 3), F(1, 3)), (1, 1))
    with localcontext() as ctx:
        ctx.prec = 5
        low = calibration.estimate(data, phases, targets, forecasts)
    with localcontext() as ctx:
        ctx.prec = 80
        high = calibration.estimate(data, phases, targets, forecasts)
    assert low == high
    projected = calibration.project(data, roster((event,)), low, (prediction(event, 1, 1),))
    assert projected[0]["predictions"]["net_immediate"] == "0"
    assert calibration.freeze_policy(data, roster((event,)), projected).choices[0].action == "no_commit"


def test_projection_refuses_changed_contract_and_preserves_input_objects():
    data, targets, phases, forecasts, event = example()
    artifact = calibration.estimate(data, phases, targets, forecasts)
    original = deepcopy(artifact)
    predictions = (prediction(event, 1, 1),)
    original_predictions = deepcopy(predictions)
    calibration.project(data, roster((event,)), artifact, predictions)
    assert artifact == original and predictions == original_predictions
    artifact["slopes"]["net_immediate"]["beta"] = "-1"
    with pytest.raises(ValueError):
        calibration.project(data, roster((event,)), artifact, predictions)
