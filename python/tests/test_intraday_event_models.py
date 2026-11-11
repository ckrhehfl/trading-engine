"""Tiny invented classical fits only; no stored market inputs or output reads."""

from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
import json

import pytest

from research import intraday_event_models as models


def query(index, *, session=None, family="A_DOWNFAIL", state="WAITING", value=1, peer=None,
          episode=None, cluster=None, offset=70, position=None):
    day = session or (date(2026, 1, 1) + timedelta(days=index)).isoformat()
    return models.Query(f"key{index}-{day}-{offset}", day, "KRX:000001", episode or f"event{index}-{day}",
        cluster or f"cluster{index}-{day}", offset, family, state,
        tuple((value,) * 13 for _ in range(64)), (value,) * 16, peer, position,
        episode or f"event{index}-{day}" if state in models.HELD_STATES else None)


def label(row, *, path=0, fill=0, immediate=0, wait=None, maturity=None, remaining=None):
    return models.TrainingLabels(row.key, path, fill, immediate, wait, maturity or row.session, remaining)


def train(kind, rows, labels, **kwargs):
    return models.fit(kind, rows, labels, "2026-06-01", support_binding=models.support_seal(rows), **kwargs)


def test_training_only_normalization_and_target_free_inference():
    rows = (query(0, value=None), query(1, value=2), query(2, value=4))
    artifact = train("empirical", rows, tuple(label(row) for row in rows))
    saved = json.dumps(artifact, sort_keys=True)
    assert artifact["normalization"]["mean"][0] == 3
    assert artifact["normalization"]["std"][0] == 1
    assert artifact["normalization"]["finite_counts"][0] == 2
    models.predict(artifact, (query(50, session="2026-06-01", value=1000000),))
    assert json.dumps(artifact, sort_keys=True) == saved
    with pytest.raises(ValueError, match="target-free"):
        models.predict(artifact, (label(rows[0]),))


def test_known_zero_is_used_only_in_the_joint_known_q_cohort():
    rows = (query(0), query(1))
    artifact = train("empirical", rows, (label(rows[0], wait=0), label(rows[1], immediate=None)))
    assert artifact["heads"]["net_immediate"]["known_rows"] == 1
    assert artifact["heads"]["net_immediate"]["known_zero_rows"] == 1
    assert artifact["heads"]["net_wait3"]["known_zero_rows"] == 1
    predictions = models.predict(artifact, (query(2, session="2026-06-01"),))[0]["predictions"]
    assert predictions["net_immediate"] == 0
    assert predictions["net_wait3"] == 0


def test_maturity_overlap_and_original_s1_s2_duplicates_are_rejected():
    rows = (query(0), query(1))
    with pytest.raises(ValueError, match="matured"):
        train("empirical", rows, (label(rows[0], maturity="2026-06-01"), label(rows[1])))
    duplicate = replace(rows[0], key="S2-copy")
    with pytest.raises(ValueError, match="Duplicate"):
        models.support_seal((rows[0], duplicate))
    artifact = train("empirical", rows, tuple(label(row) for row in rows))
    with pytest.raises(ValueError, match="overlaps"):
        models.predict(artifact, rows)
    with pytest.raises(ValueError, match="overlaps"):
        models.predict(artifact, (replace(rows[0], session="2026-06-01"),))


def test_weights_balance_dates_clusters_episodes_and_frames():
    a = query(0, episode="a", cluster="one", offset=70)
    a2 = replace(a, key="a2", decision_offset=73)
    b = query(1, session=a.session, episode="b", cluster="one", family="B_ORL30")
    c = replace(query(2, session=a.session, episode="c", cluster="two"), symbol="KRX:000002")
    d = query(3, session="2026-01-02")
    weights = models.head_weights((a, a2, b, c, d))
    assert weights == [1/16, 1/16, 1/8, 1/4, 1/2]
    assert sum(weights) == 1


def test_empirical_family_and_state_shrinkage_are_explicit():
    rows = (query(0), query(1), query(2, family="C_FIRSTPULLBACK", state="PULLBACK"),
            query(3, family="C_FIRSTPULLBACK", state="CONFIRMED"))
    labels = (label(rows[0], immediate=-10, wait=0), label(rows[1], immediate=-10, wait=0),
              label(rows[2], immediate=10, wait=0), label(rows[3], immediate=20, wait=0))
    artifact = train("empirical", rows, labels)
    queries = (query(90, session="2026-06-01", cluster="shared-query"),
               query(91, session="2026-06-01", cluster="shared-query", family="C_FIRSTPULLBACK", state="CONFIRMED"))
    predictions = models.predict(artifact, queries)
    assert predictions[1]["predictions"]["net_immediate"] > predictions[0]["predictions"]["net_immediate"]
    assert artifact["heads"]["net_immediate"]["prior_strength"] == 20
    assert artifact["fit_head_count"] == 4
    assert artifact["native_fit_head_count"] == 0


def test_paired_roster_seal_and_missing_peer_prediction():
    rows = (query(0, peer=(1, 2, 1)), query(1, peer=(2, 3, 0)))
    labels = tuple(label(row) for row in rows)
    with pytest.raises(ValueError, match="seal"):
        models.fit("empirical", rows, labels, "2026-06-01")
    chart = train("empirical", rows, labels)
    peer = train("empirical", rows, labels, mode="chart_peer")
    assert chart["common_support_sha256"] == peer["common_support_sha256"]
    value = models.predict(peer, (query(5, session="2026-06-01"),))[0]
    assert value["query_status"] == "unsupported_peer_context"
    assert all(v is None for v in value["predictions"].values())
    bad = (replace(rows[0], peer=None), rows[1])
    with pytest.raises(ValueError, match="unsupported peer"):
        train("empirical", bad, labels, mode="chart_peer")


def test_low_support_is_warning_and_two_date_lgb_fits_reload_identically():
    rows = (query(0, value=0), query(1, value=1))
    labels = (label(rows[0], path=0, fill=0, immediate=-1, wait=0),
              label(rows[1], path=1, fill=1, immediate=1, wait=2))
    artifact = train("lightgbm", rows, labels)
    assert artifact["native_fit_head_count"] == artifact["fit_head_count"] == 4
    assert all(head["low_support"] for head in artifact["heads"].values())
    assert all(artifact["heads"][head]["known_clusters"] == artifact["heads"][head]["known_dates"] == 2 for head in models.HEADS[:4])
    assert all(artifact["heads"][head]["rounds_requested"] == 100 for head in models.HEADS[:4])
    query_rows = (query(3, session="2026-06-01", value=10),)
    assert models.predict(artifact, query_rows) == models.predict(json.loads(json.dumps(artifact)), query_rows)
    again = train("lightgbm", rows, labels)
    assert artifact == again


def test_one_class_is_explicit_constant_not_a_native_fit():
    rows = (query(0), query(1))
    artifact = train("lightgbm", rows, tuple(label(row, path=2, fill=0, immediate=None) for row in rows))
    assert artifact["constant_head_count"] == 2
    assert artifact["native_fit_head_count"] == 0
    assert artifact["unsupported_head_count"] == 3
    value = models.predict(artifact, (query(3, session="2026-06-01"),))[0]["predictions"]
    assert value["path_class"] == [0.0, 0.0, 1.0]
    assert value["fill"] == 0.0


def test_one_cluster_or_one_date_is_explicit_computationally_unsupported():
    a = query(0)
    b = replace(query(1, session=a.session), symbol="KRX:000002")
    artifact = train("lightgbm", (a, b), (label(a), label(b, path=1, fill=1)))
    assert artifact["native_fit_head_count"] == 0
    assert artifact["heads"]["path_class"]["known_rows"] == 2
    assert artifact["heads"]["path_class"]["known_dates"] == 1
    assert artifact["heads"]["path_class"]["status"] == "unsupported"


def test_head_known_counts_recompute_clusters_without_overlapping_family_inflation():
    a = query(0, cluster="one")
    b = query(1, session=a.session, cluster="one", family="B_ORL30")
    c = query(2)
    artifact = train("empirical", (a, b, c), (label(a), label(b), label(c, fill=None)))
    assert artifact["heads"]["fill"]["known_episodes"] == 2
    assert artifact["heads"]["fill"]["known_clusters"] == 1
    assert artifact["heads"]["path_class"]["known_clusters"] == 2


def test_same_symbol_day_cannot_inflate_cluster_count():
    a = query(0)
    b = query(1, session=a.session)
    with pytest.raises(ValueError, match="independent clusters"):
        models.support_seal((a, b))


def test_nonfinite_and_unknown_artifact_status_never_become_zero():
    with pytest.raises(ValueError, match="Nonfinite"):
        query(0, value=float("nan"))
    rows = (query(0), query(1))
    artifact = train("empirical", rows, tuple(label(row) for row in rows))
    artifact["heads"]["fill"]["status"] = "unknown_status"
    with pytest.raises(ValueError, match="Unknown head"):
        models.predict(artifact, (query(3, session="2026-06-01"),))


def test_native_fit_failure_is_reported_without_zero_prediction(monkeypatch):
    import lightgbm as lgb

    def fail(*args, **kwargs):
        raise lgb.basic.LightGBMError("invented native failure")

    monkeypatch.setattr(lgb, "train", fail)
    rows = (query(0), query(1))
    labels = (label(rows[0], path=0, fill=0, immediate=-1, wait=0),
              label(rows[1], path=1, fill=1, immediate=1, wait=2))
    artifact = train("lightgbm", rows, labels)
    assert artifact["fit_attempt_head_count"] == artifact["failed_head_count"] == 4
    assert artifact["fit_head_count"] == artifact["native_fit_head_count"] == 0
    values = models.predict(artifact, (query(3, session="2026-06-01"),))[0]
    assert {values["head_status"][head] for head in models.HEADS[:4]} == {"failed"}
    assert values["head_status"]["remaining_gross"] == "not_applicable_to_state"
    assert all(value is None for value in values["predictions"].values())


def test_missing_peer_query_never_reaches_native_prediction(monkeypatch):
    import lightgbm as lgb

    rows = (query(0, peer=(1, 2, 0)), query(1, peer=(2, 1, 1)))
    artifact = train("lightgbm", rows, (label(rows[0], immediate=-1, wait=0), label(rows[1], immediate=1, wait=0)), mode="chart_peer")

    def fail(*args, **kwargs):
        raise AssertionError("Unsupported peer query reached native prediction")

    monkeypatch.setattr(lgb, "Booster", fail)
    result = models.predict(artifact, (query(3, session="2026-06-01"),))[0]
    assert all(value is None for value in result["predictions"].values())


def test_holding_position_and_label_tasks_are_separate():
    with pytest.raises(ValueError, match="position features"):
        query(0, state="HELD_10")
    with pytest.raises(ValueError, match="Position clock"):
        query(0, state="HELD_10", position=(Fraction(2, 3), Fraction(1, 3), 1))
    with pytest.raises(ValueError, match="reserved for held"):
        query(0, position=(Fraction(1, 3), Fraction(2, 3), 1))
    held = query(0, state="HELD_10", position=(Fraction(1, 3), Fraction(2, 3), 1))
    with pytest.raises(ValueError, match="entry-head labels"):
        train("empirical", (held,), (label(held, remaining=1),))
    entry = query(1)
    with pytest.raises(ValueError, match="held-alive"):
        train("empirical", (entry,), (label(entry, remaining=1),))


def test_holding_gross_head_conditions_on_remaining_clock_and_masks_entry_heads():
    entry = query(0)
    ten = query(1, state="HELD_10", position=(Fraction(1, 3), Fraction(2, 3), -1))
    twenty = query(2, state="HELD_20", position=(Fraction(2, 3), Fraction(1, 3), 1))
    rows = (entry, ten, twenty)
    targets = (label(entry, wait=0),
               label(ten, path=None, fill=None, immediate=None, remaining=Fraction(-1, 100)),
               label(twenty, path=None, fill=None, immediate=None, remaining=Fraction(1, 100)))
    artifact = train("empirical", rows, targets)
    assert artifact["heads"]["remaining_gross"]["known_rows"] == 2
    assert artifact["heads"]["net_immediate"]["known_rows"] == 1
    assert artifact["normalization"]["finite_counts"][-3:] == [2, 2, 2]
    assert artifact["task_semantics"] == models.TASK_SEMANTICS
    future = tuple(replace(row, key="eval-" + row.key, session="2026-06-01", cluster_id="eval-cluster") for row in rows)
    predictions = {item["key"]: item for item in models.predict(artifact, future)}
    entry_value = predictions["eval-" + entry.key]
    assert entry_value["predictions"]["remaining_gross"] is None
    assert entry_value["head_status"]["remaining_gross"] == "not_applicable_to_state"
    assert predictions["eval-" + ten.key]["predictions"]["remaining_gross"] < predictions["eval-" + twenty.key]["predictions"]["remaining_gross"]
    for row in (ten, twenty):
        value = predictions["eval-" + row.key]
        assert all(value["predictions"][head] is None for head in models.HEADS[:4])
        assert all(value["head_status"][head] == "not_applicable_to_state" for head in models.HEADS[:4])
    assert models.predict(artifact, future) == models.predict(json.loads(json.dumps(artifact)), future)


def test_q_heads_share_joint_known_rows_and_report_original_asymmetric_support():
    rows = tuple(query(i) for i in range(4))
    targets = (label(rows[0], immediate=0, wait=2), label(rows[1], immediate=2, wait=4),
               label(rows[2], immediate=999, wait=None), label(rows[3], immediate=None, wait=-999))
    original = tuple(targets)
    artifact = train("empirical", rows, targets)
    support = artifact["q_training_support"]
    assert support["original_known_rows"] == {"net_immediate": 3, "net_wait3": 3}
    assert support["paired_known_rows"] == 2
    assert support["dropped_unpaired_rows"] == {"net_immediate": 1, "net_wait3": 1}
    for head in models.Q_HEADS:
        assert artifact["heads"][head]["known_rows"] == artifact["heads"][head]["fit_input_known_rows"] == 2
        assert artifact["heads"][head]["raw_known_rows"] == 3
        assert artifact["heads"][head]["dropped_unpaired_q_rows"] == 1
    assert artifact["heads"]["net_immediate"]["pooled"] == [1.0]
    assert artifact["heads"]["net_wait3"]["pooled"] == [3.0]
    assert artifact["heads"]["path_class"]["known_rows"] == 4
    assert tuple(targets) == original
    result = models.predict(artifact, (query(9, session="2026-06-01"),))[0]
    assert all(result["predictions"][head] is not None for head in models.Q_HEADS)


def test_one_sided_q_observations_remain_labels_but_cannot_train_either_action():
    rows = (query(0), query(1))
    targets = (label(rows[0], immediate=0, wait=None), label(rows[1], immediate=None, wait=1))
    artifact = train("empirical", rows, targets)
    assert all(artifact["heads"][head]["known_rows"] == 0 for head in models.Q_HEADS)
    assert artifact["q_training_support"]["paired_known_rows"] == 0
    assert targets[0].net_immediate == 0 and targets[1].net_wait3 == 1
    result = models.predict(artifact, (query(2, session="2026-06-01"),))[0]
    assert all(result["predictions"][head] is None for head in models.Q_HEADS)


def test_many_counterfactual_books_do_not_overweight_the_original_episode():
    rows = [replace(query(0, state="HELD_10", episode=f"book-{i}", offset=80 + i,
                          position=(Fraction(1, 3), Fraction(2, 3), 0)),
                    key=f"book-{i}", original_episode_id="original-A") for i in range(5)]
    rows.append(replace(rows[0], key="book-B", episode_id="book-B", original_episode_id="original-B"))
    weights = models.head_weights(rows)
    assert sum(weights[:5]) == pytest.approx(0.5)
    assert weights[5] == pytest.approx(0.5)
    stats = models._known_stats(rows, len(rows))
    assert stats["known_episodes"] == 2
    assert stats["known_counterfactual_books"] == 6
    with pytest.raises(ValueError, match="original causal episode"):
        replace(rows[0], original_episode_id=None)
    assert models.support_seal(rows) != models.support_seal([*rows[:5], replace(rows[5], original_episode_id="original-A")])
