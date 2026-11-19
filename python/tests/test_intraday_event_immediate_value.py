"""Invented ET score cuts, exact response, support and independent projection."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F

import pytest

from research import intraday_event_immediate_value as et
from research import intraday_event_models as models
from test_intraday_event_factorized_workflow import candidate, prediction
from test_intraday_event_mean_baseline import data_for, roster


def example(values, scores, evaluation=()):
    events = tuple(candidate(index) for index in range(49, 49 + len(values)))
    data, exact = data_for(tuple(c.query for c in events), values, evaluation_rows=tuple(c.query for c in evaluation))
    phases = dict.fromkeys((event.key for event in events), "onset")
    forecasts = tuple(prediction(c, score, None) for c, score in zip(events, scores))
    return data, phases, exact, forecasts


def decode(values):
    return [et.exact_value(value) for value in values]


def bin_means(cell):
    return decode(bucket["exact_mean"] for bucket in cell["bins"])


def test_weighted_inverse_cdf_keeps_ties_together_and_inclusive_edges():
    events = tuple(candidate(i, state="CONFIRMED") for i in range(97, 104))
    data, phases, exact, oof = example((1, 3, 5, 7, 9, 11), (1, 1, 2, 3, 4, 5), events)
    artifact = et.estimate(data, phases, exact, oof)
    assert et.verify(data, phases, exact, oof, artifact) == {"verified": True, "joint_rows": 6, "bins": 12}
    onset = artifact["cells"]["onset"]
    assert decode(onset["cutpoints"]) == [1, 3]
    assert [bucket["rows"] for bucket in onset["bins"]] == [2, 2, 2]
    assert decode(bucket["weight_mass"] for bucket in onset["bins"]) == [F(1, 3)] * 3
    assert bin_means(onset) == [2, 6, 10]
    assert artifact["oof"] == {"start_index": 49, "expected_rows": 6, "finite_immediate_rows": 6, "eligible_c_rows": 6}
    # Onset takes priority for a confirmed state at the original onset clock.
    raw = tuple(prediction(c, score, 100) for c, score in zip(events, (-3, 1, 2, 3, 4, 12, None)))
    original = deepcopy(raw)
    projected = et.project(data, roster(events), artifact, raw)
    assert raw == original
    assert decode(row["predictions"][et.IMMEDIATE] for row in projected) == [2, 2, 6, 6, 10, 10, None]
    assert all(row["predictions"][head] is None for head in models.HEADS if head != et.IMMEDIATE for row in projected)
    assert projected[-1]["head_status"][et.IMMEDIATE] == "unsupported_original_success_score"
    assert et.verify_projection(data, roster(events), artifact, raw, projected)["predictions"] == len(events)


def test_equal_cuts_and_empty_phase_stay_unknown_without_dropping_known_no_fill_zero():
    onset = (candidate(97), candidate(98), candidate(99))
    waiting = candidate(100, decision=36)
    events = (*onset, waiting)
    data, phases, exact, forecasts = example((0, 2, 4, 8), (5, 5, 5, 9), events)
    first_label, *others = data.base.targets
    first_fill, *other_fills = data.action_targets
    data = replace(data, base=replace(data.base, targets=(replace(first_label, fill=0), *others)),
                   action_targets=(replace(first_fill, immediate=0), *other_fills))
    artifact = et.estimate(data, phases, exact, forecasts)
    assert et.verify(data, phases, exact, forecasts, artifact)["verified"]
    assert decode(artifact["cells"]["onset"]["cutpoints"]) == [5, 5]
    assert bin_means(artifact["cells"]["onset"]) == [2, None, 8]
    assert artifact["cells"]["waiting_pullback"]["cutpoints"] == [None, None]
    assert bin_means(artifact["cells"]["waiting_pullback"]) == [None] * 3
    raw = tuple(prediction(c, score) for c, score in zip(events, (4, 5, 6, 5)))
    projected = et.project(data, roster(events), artifact, raw)
    assert decode(row["predictions"][et.IMMEDIATE] for row in projected) == [2, 2, 8, None]
    assert projected[-1]["head_status"][et.IMMEDIATE] == "unsupported_empty_phase"
    assert et.verify_projection(data, roster(events), artifact, raw, projected)["verified"]


def test_full_j_weights_include_pre_oof_dates_and_other_families_before_conditioning():
    pre, first, next_day, unknown, missing_score = (candidate(i) for i in (48, 49, 50, 51, 52))
    later = candidate(49, decision=36)
    other = replace(first.query, key="other", episode_id="other", family="A_DOWNFAIL")
    rows = (pre.query, first.query, later.query, other, next_day.query, unknown.query, missing_score.query)
    data, exact = data_for(rows, (10, 1, 9, 50, -1, 100, 71), fills=((1, 1),) * 5 + ((1, None), (1, 1)))
    phases = dict.fromkeys((row.key for row in rows), "onset")
    phases.update({later.key: "waiting_pullback", other.key: None})
    oof = (prediction(first, 1), prediction(later, 4), {**prediction(first, 3), "key": other.key},
           prediction(next_day, 5), prediction(unknown, -100), prediction(missing_score, None))
    artifact = et.estimate(data, phases, exact, oof)
    assert et.verify(data, phases, exact, oof, artifact)["verified"]
    assert artifact["joint"]["rows"] == 6  # One original wait3 fill is unknown.
    assert artifact["oof"] == {"start_index": 49, "expected_rows": 6, "finite_immediate_rows": 5, "eligible_c_rows": 3}
    onset = artifact["cells"]["onset"]
    assert et.exact_value(onset["weight_mass"]) == F(5, 16)
    assert decode(onset["cutpoints"]) == [5, 5]  # The unweighted lower cut would be 1.
    assert bin_means(onset) == [F(-3, 5), None, None]
    assert et.exact_value(artifact["cells"]["waiting_pullback"]["weight_mass"]) == F(1, 16)


def test_held_rows_keep_forecast_identity_and_non_c_or_held_queries_have_no_mapping():
    training, event = candidate(49), candidate()
    held = replace(training.query, key="held-training", episode_id="book-training",
                   original_episode_id=training.episode.event_id, state="HELD_10", position=(1 / 3, 2 / 3, 0))
    held_eval = replace(held, key="held-evaluation", session=event.query.session, episode_id="book-evaluation",
                        original_episode_id=event.episode.event_id, cluster_id=event.episode.cluster_id)
    other_eval = replace(event.query, key="other-evaluation", episode_id="other-evaluation", family="A_DOWNFAIL")
    data, exact = data_for((training.query, held), (1, None), fills=((1, 1), (None, None)),
                          evaluation_rows=(event.query, held_eval, other_eval))
    phases = {training.key: "onset", held.key: None}
    first = prediction(training)
    with pytest.raises(ValueError, match="complete chronological ES OOF roster"):
        et.estimate(data, phases, exact, (first,))
    oof = (first, {**prediction(training, None, None), "key": held.key})
    artifact = et.estimate(data, phases, exact, oof)
    assert et.verify(data, phases, exact, oof, artifact)["joint_rows"] == 1
    raw = (prediction(event), {**prediction(event, None), "key": held_eval.key},
           {**prediction(event, 1), "key": other_eval.key})
    projected = et.project(data, roster((event,)), artifact, raw)
    assert decode(row["predictions"][et.IMMEDIATE] for row in projected) == [1, None, None]
    assert all(row["head_status"][et.IMMEDIATE] == "not_applicable_to_mapping" for row in projected[1:])
    assert et.verify_projection(data, roster((event,)), artifact, raw, projected)["verified"]


def test_empty_bin_is_unknown_and_projection_rejects_reordered_or_tampered_records():
    events = (candidate(97), candidate(98))
    data, phases, exact, oof = example((1, -1), (2, 2), events)
    artifact = et.estimate(data, phases, exact, oof)
    raw = tuple(prediction(c, score) for c, score in zip(events, (2, 3)))
    projected = et.project(data, roster(events), artifact, raw)
    assert decode(row["predictions"][et.IMMEDIATE] for row in projected) == [0, None]
    assert projected[1]["head_status"][et.IMMEDIATE] == "unsupported_empty_bin"
    assert et.verify_projection(data, roster(events), artifact, raw, projected)["verified"]
    with pytest.raises(ValueError, match="current ES roster"):
        et.project(data, roster(events), artifact, reversed(raw))
    changed = deepcopy(projected)
    changed[1]["predictions"][et.IMMEDIATE] = ["0", "1"]
    with pytest.raises(ValueError, match="ET exact value projection"):
        et.verify_projection(data, roster(events), artifact, raw, changed)
    changed_artifact = deepcopy(artifact)
    changed_artifact["cells"]["onset"]["bins"][0]["exact_mean"] = ["1", "1"]
    with pytest.raises(ValueError, match="ET exact training mapping"):
        et.verify(data, phases, exact, oof, changed_artifact)


@pytest.mark.parametrize("sign", [-1, 0, 1])
def test_exact_value_sign_survives_original_float_cancellation(sign):
    event = candidate()
    tiny = F(1, 10**330)
    learned_values = (F(1, 3), -F(1, 3) + sign * tiny)
    assert sum(map(float, learned_values)) == 0
    data, phases, exact, oof = example(learned_values, (0, 0), (event,))
    artifact = et.estimate(data, phases, exact, oof)
    raw = (prediction(event),)
    projected = et.project(data, roster((event,)), artifact, raw)
    assert et.exact_value(projected[0]["predictions"][et.IMMEDIATE]) == sign * tiny / 2
    assert et.verify(data, phases, exact, oof, artifact)["verified"]
    assert et.verify_projection(data, roster((event,)), artifact, raw, projected)["verified"]


def test_exact_wire_supports_large_combined_denominators():
    event = candidate()
    values = (F(1, 2**4000), F(1, 3**3000), F(1, 5**2000), F(1, 7**1600))
    data, phases, exact, oof = example(values, (0,) * len(values), (event,))
    artifact = et.estimate(data, phases, exact, oof)
    projected = et.project(data, roster((event,)), artifact, (prediction(event),))
    result = et.exact_value(projected[0]["predictions"][et.IMMEDIATE])
    assert result.denominator > 10**4300
    assert result == sum(values) / len(values)
    assert et.verify(data, phases, exact, oof, artifact)["verified"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "early", "future", "session", "infinite"])
def test_oof_requires_complete_chronological_original_roster_and_finite_scores(change):
    data, phases, exact, valid = example((1, 2), (0, 1))
    oof = list(deepcopy(valid))
    if change == "missing":
        oof.pop()
    elif change == "duplicate":
        oof.append(oof[0])
    elif change in ("early", "future"):
        oof.append(prediction(candidate(48 if change == "early" else 97)))
    elif change == "session":
        oof[0]["session"] = oof[1]["session"]
    else:
        oof[0]["predictions"][et.IMMEDIATE] = float("inf")
    with pytest.raises(ValueError):
        et.estimate(data, phases, exact, oof)
