"""Independent hand-counted EO arithmetic and saved-stream integrity witnesses."""

from copy import deepcopy
from fractions import Fraction
import json

import pytest

from research import intraday_event_rank as rank
from research import intraday_event_rank_fixtures as fixtures
from research import intraday_event_rank_reference as reference


def point(name, score, target, *, day="2024-01-02", symbol=None):
    return {"event_id": name, "session": day, "symbol": symbol or name,
            "predictions": {head: str(score) for head in rank.Q_HEADS},
            "heads": {head: {"target": str(target)} for head in rank.Q_HEADS}}


@pytest.mark.parametrize(("scores", "expected"), [((-1, 0, 1), 1), ((1, 0, -1), 0), ((7, 7, 7), 0.5)])
def test_hand_counted_perfect_reverse_and_tied_scores(scores, expected):
    rows = [point(str(i), score, target) for i, (score, target) in enumerate(zip(scores, (-1, 0, 1), strict=True))]
    metrics = reference.pair_metrics(rows, "net_immediate")
    assert metrics["auc"]["pairs"] == 2
    assert metrics["auc"]["pair_weight"] == pytest.approx(2 / 9)
    assert metrics["concordance"]["pairs"] == 3
    assert metrics["concordance"]["pair_weight"] == pytest.approx(1 / 3)
    for name in ("auc", "concordance"):
        assert metrics[name]["value"] == expected
    assert metrics["target_tie_pairs"] == 0


def test_target_ties_excluded_and_reported_single_class_auc_undefined():
    rows = [point("A", 1, 1), point("B", 1, 1), point("C", 1, 2)]
    metrics = reference.pair_metrics(rows, "net_wait3")
    assert metrics["auc"]["value"] is None
    assert metrics["auc"]["pairs"] == 0
    assert metrics["concordance"]["value"] == 0.5
    assert metrics["concordance"]["score_tie_pairs"] == 2
    assert metrics["target_tie_pairs"] == 1
    assert metrics["target_tie_weight"] == pytest.approx(1 / 9)


def test_fraction_ordering_does_not_round_distinct_scores_or_targets_to_ties():
    tiny = Fraction(1, 10 ** 1000)
    metrics = reference.pair_metrics([point("A", 1, -tiny), point("B", Fraction(1) + tiny, tiny)], "net_immediate")
    assert metrics["auc"]["value"] == 1
    assert metrics["auc"]["score_tie_pairs"] == 0
    assert metrics["concordance"]["value"] == 1
    assert metrics["target_tie_pairs"] == 0


def test_date_cluster_event_weights_and_same_cluster_exclusion_are_hand_counted():
    # First date: A has two events, B one; second date has C only.
    # Weights are 1/8, 1/8, 1/4, 1/2, not four equal observations.
    rows = [point("A1", 2, 1, symbol="A"), point("A2", 2, 1, symbol="A"),
            point("B", 1, -1), point("C", 0, 1, day="2024-01-03")]
    metrics = reference.pair_metrics(rows, "net_immediate")
    assert metrics["excluded_same_cluster_pairs"] == 1
    assert metrics["auc"]["pairs"] == 3
    assert metrics["auc"]["pair_weight"] == pytest.approx(3 / 16)
    assert metrics["auc"]["credit_weight"] == pytest.approx(1 / 16)
    assert metrics["auc"]["value"] == pytest.approx(1 / 3)
    assert metrics["target_tie_pairs"] == 2


def narrow_fixture():
    roster, causal, outcome_factory = fixtures.fixture()
    event_id = next(iter(roster["events"]))
    roster["events"] = {event_id: roster["events"][event_id]}
    return roster, [row for row in causal if row["event_id"] == event_id], [
        row for row in outcome_factory() if row["event_id"] == event_id]


def test_first_eligible_unknown_target_cannot_be_backfilled_from_later_known_frame():
    roster, causal, queries = narrow_fixture()
    first = causal[1]
    later = deepcopy(first)
    later.update(key=first["event_id"] + "@14", decision_offset=14)
    later["label_query"].update(decision_offset=14, event_offset=13, reference_offset=13)
    causal.append(later)
    later_query = deepcopy(queries[1])
    later_query.update(later, schema="EN-query-value-v1")
    queries.append(later_query)
    for head in rank.Q_HEADS:
        queries[1]["heads"][head].update(target=None, training_target=None, target_state="immature",
                                        matured_offset=None, scored=False, error=None)
    queries[1]["paired"]["scored"] = False
    selected = rank.select_representatives(roster, causal)
    reference.verify_selection(roster, reversed(causal), selected)
    joined = rank.join_targets(selected, queries)
    reference.verify_join(selected, queries, joined)
    waiting = next(row for row in joined if row["phase"] == "waiting_pullback")
    assert waiting["key"] == first["key"]
    assert not waiting["joint_known"]
    assert waiting["support"]["eligible_rows"] == 2
    assert waiting["heads"]["net_immediate"]["target"] is None


def test_absent_phases_and_nonpositive_predictions_stay_in_causal_denominator():
    roster, causal, queries = narrow_fixture()
    causal = causal[:1]
    for head in rank.Q_HEADS:
        causal[0]["predictions"][head] = "-1"
    selected = rank.select_representatives(roster, causal)
    reference.verify_selection(roster, causal, selected)
    assert selected[0]["key"] is not None
    assert len(selected) == 4
    assert all(row["reason"] == "no_causal_phase" for row in selected[1:])
    bad = deepcopy(selected)
    bad[0]["key"] = None
    with pytest.raises(ValueError, match="representatives"):
        reference.verify_selection(roster, causal, bad)
    with pytest.raises(ValueError, match="duplicate causal"):
        reference.verify_selection(roster, [*causal, causal[0]], selected)


def test_one_unknown_head_preserves_own_known_counts_but_excludes_both_from_ranking():
    roster, causal, queries = narrow_fixture()
    queries[0]["heads"]["net_wait3"].update(target=None, training_target=None, target_state="immature",
                                           matured_offset=None, scored=False, error=None)
    queries[0]["paired"]["scored"] = False
    selections = rank.select_representatives(roster, causal)
    joined = rank.join_targets(selections, queries)
    reference.verify_join(selections, queries, joined)
    summary = rank.summarize(roster["calendar"], joined)
    reference.verify_summary(roster["calendar"], joined, summary)
    heads = summary["cells"][0]["heads"]
    assert heads["net_immediate"]["support"]["known"] == 1
    assert heads["net_immediate"]["support"]["scored"] == 0
    assert heads["net_wait3"]["support"]["unknown"] == 1
    assert heads["net_wait3"]["support"]["zero"] == 0
    assert heads["net_wait3"]["support"]["target_states"] == {"immature": 1}


def test_saved_cash_ratio_or_stream_identity_tampering_is_rejected_independently():
    roster, causal, queries = narrow_fixture()
    selections = rank.select_representatives(roster, causal)
    joined = rank.join_targets(selections, queries)
    bad = deepcopy(queries)
    bad[0]["heads"]["net_immediate"]["net_cash"] = "1000000"
    with pytest.raises(ValueError, match="target ratio"):
        reference.verify_join(selections, bad, joined)
    bad = deepcopy(queries)
    bad[0]["cluster_id"] = "foreign"
    with pytest.raises(ValueError, match="causal.cluster_id"):
        reference.verify_join(selections, bad, joined)
    with pytest.raises(ValueError, match="missing or duplicate"):
        reference.verify_join(selections, queries[1:], joined)
    with pytest.raises(ValueError, match="duplicate query"):
        reference.verify_join(selections, [*queries, queries[0]], joined)


def sparse_rank_rows(calendar):
    rows = []
    # All positive observations lie in original block zero. Deleting that
    # block makes AUC undefined; deleting an empty block must remain valid.
    for index, (day_index, score, target) in enumerate(((0, 2, 1), (4, 0, -1), (5, 1, -1))):
        for phase in rank.PHASES:
            row = point(str(index), score, target, day=calendar[day_index])
            row.update(schema="EO-rank-row-v1", cluster_id=row["session"] + ":" + row["symbol"],
                       fold=0, phase=phase, key=str(index) + "@" + phase,
                       reason="first_eligible_causal", joint_known=True)
            for head in rank.Q_HEADS:
                row["heads"][head]["target_state"] = "known"
            rows.append(row)
    return rows


def test_original_calendar_deletions_keep_empty_dates_and_undefined_replicates():
    roster, _, _ = fixtures.fixture()
    rows = sparse_rank_rows(roster["calendar"])
    summary = rank.summarize(roster["calendar"], rows)
    reference.verify_summary(roster["calendar"], rows, summary)
    head = summary["cells"][0]["heads"]["net_immediate"]
    assert head["cross_cluster"]["auc"]["value"] == 1
    assert head["same_date"]["auc"]["value"] is None
    sensitivity = head["delete5_sensitivity"]
    assert sensitivity["blocks"] == 8
    assert sensitivity["cross_cluster"]["auc"] == {"valid": 7, "undefined": 1, "min": 1.0, "max": 1.0}
    assert sensitivity["same_date"]["auc"]["undefined"] == 8
    assert all(cell["calendar_days"] == 40 for cell in summary["cells"])
    assert summary["cells"][4]["heads"]["net_immediate"]["delete5_sensitivity"]["cross_cluster"]["auc"]["undefined"] == 8
    summary["cells"][0]["heads"]["net_immediate"]["cross_cluster"]["auc"]["value"] = 0.9
    with pytest.raises(ValueError, match="statistic differs"):
        reference.verify_summary(roster["calendar"], rows, summary)


def test_small_four_fold_producer_matches_independent_pairs_and_compact_report():
    roster, causal, outcomes = fixtures.full_fixture(names=3)
    selected = rank.select_representatives(roster, causal)
    reference.verify_selection(roster, causal, selected)
    queries = outcomes()
    joined = rank.join_targets(selected, queries)
    reference.verify_join(selected, queries, joined)
    summary = rank.summarize(roster["calendar"], joined)
    receipt = reference.verify_summary(roster["calendar"], joined, summary)
    assert receipt["cells"] == 16
    assert len(json.dumps(summary)) < 100_000
    assert len(json.dumps(receipt)) < 1_000
    assert all(cell["heads"]["net_immediate"]["cross_cluster"]["excluded_same_cluster_pairs"] == 40
               for cell in summary["cells"])


def test_full_fixture_geometry_and_future_target_separation():
    roster, causal, outcomes = fixtures.full_fixture()
    assert len(roster["events"]) == fixtures.FULL_EVENTS == 3680
    assert len(causal) == fixtures.FULL_ROWS == 14720
    assert callable(outcomes)
    assert all("heads" not in row and "targets" not in row and "training_labels" not in row for row in causal)
    queries = outcomes()
    assert len(queries) == fixtures.FULL_ROWS
    assert {Fraction(row["heads"]["net_immediate"]["target"]) for row in queries} == {
        Fraction(-2, 1000), Fraction(-1, 1000), Fraction(0), Fraction(1, 1000), Fraction(2, 1000)}
