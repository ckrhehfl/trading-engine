"""Invented edge cases for the causal EO selection and exact rank producer."""

from copy import deepcopy
from fractions import Fraction

import pytest

from research import intraday_event_rank as rank
from research.intraday_event_rank_fixtures import full_fixture


def small_fixture():
    roster, causal, outcomes = full_fixture(names=1)
    ids = {row["event_id"] for row in causal[:16]}
    roster["events"] = {key: event for key, event in roster["events"].items() if key in ids}
    return roster, causal[:16], [row for row in outcomes() if row["event_id"] in ids]


def test_first_eligible_ignores_sign_future_roster_and_stream_order():
    roster, causal, _ = small_fixture()
    event = causal[0]["event_id"]
    causal[1]["predictions"] = dict.fromkeys(rank.Q_HEADS, "-1/100")
    later = deepcopy(causal[1])
    later.update(key=event + "@14", decision_offset=14)
    later["predictions"] = dict.fromkeys(rank.Q_HEADS, "9/10")
    expected = rank.select_representatives(roster, [later, *reversed(causal)])
    selection = next(row for row in expected if row["event_id"] == event and row["phase"] == "waiting_pullback")
    assert selection["key"] == causal[1]["key"]
    assert selection["predictions"]["net_immediate"] == "-1/100"
    assert selection["support"] == {"causal_rows": 2, "eligible_rows": 2, "reason_counts": {"eligible": 2}}
    for record in roster["events"].values():
        record.update(terminal_state="INVALIDATED", confirmation_key=None, confirmation_decision_offset=None)
    assert rank.select_representatives(roster, [*causal, later]) == expected
    causal[1]["predictions"]["net_immediate"] = "8"
    assert selection["predictions"]["net_immediate"] == "-1/100"


@pytest.mark.parametrize("change,reason", [
    ({"entry_admissibility": "not_admissible"}, "entry_not_admissible"),
    ({"entry_admissibility": "unknown"}, "entry_unknown"),
    ({"initial_denominator": None}, "unknown_denominator"),
    ({"initial_denominator": "0"}, "nonpositive_denominator"),
    ({"scope_supported": False}, "unsupported_scope"),
    ({"predictions": {"net_immediate": None, "net_wait3": "0"}}, "missing_paired_value_heads"),
])
def test_causal_ineligibility_retains_episode_and_all_phases(change, reason):
    roster, causal, _ = small_fixture()
    causal[0].update(change)
    rows = rank.select_representatives(roster, causal[:1])
    onset = next(row for row in rows if row["event_id"] == causal[0]["event_id"] and row["phase"] == "onset")
    assert onset["key"] is None and onset["reason"] == "no_eligible_causal"
    assert onset["support"]["reason_counts"] == {reason: 1}
    assert len(rows) == 4 * len(roster["events"])
    assert sum(row["reason"] == "no_causal_phase" for row in rows) == len(rows) - 1


@pytest.mark.parametrize("field,value", [("fold", 1), ("session", "2024-02-01"),
                                         ("phase", "confirmation"), ("cluster_id", "foreign")])
def test_causal_identity_tampering_fails(field, value):
    roster, causal, _ = small_fixture()
    causal[0][field] = value
    with pytest.raises(ValueError):
        rank.select_representatives(roster, causal)


def test_duplicate_clock_calendar_and_missing_selection_fail():
    roster, causal, outcomes = small_fixture()
    with pytest.raises(ValueError, match="Duplicate"):
        rank.select_representatives(roster, [*causal, causal[0]])
    selections = rank.select_representatives(roster, causal)
    with pytest.raises(ValueError, match="Missing selected"):
        rank.join_targets(selections, outcomes[1:])
    with pytest.raises(ValueError, match="Duplicate"):
        rank.join_targets(selections, [*outcomes, outcomes[0]])
    roster["fold_by_day"][roster["calendar"][0]] = 1
    with pytest.raises(ValueError, match="fold calendar"):
        rank.select_representatives(roster, causal)


def test_joint_known_excludes_unknown_but_keeps_true_no_fill_zero():
    roster, causal, outcomes = small_fixture()
    unknown = outcomes[0]["heads"]["net_wait3"]
    unknown.update(target=None, training_target=None, target_state="immature", net_cash=None,
                   matured_offset=None, scored=False, error=None)
    outcomes[0]["paired"]["scored"] = False
    rows = rank.join_targets(rank.select_representatives(roster, causal), outcomes)
    unknown_row = next(row for row in rows if row["key"] == outcomes[0]["key"])
    assert unknown_row["heads"]["net_wait3"]["target"] is None
    assert not unknown_row["joint_known"]
    zero = [row for row in rows if row["heads"]["net_immediate"]["target"] == "0"]
    assert zero and all(row["heads"]["net_immediate"]["target_state"] == "known" for row in zero)
    summary = rank.summarize(roster["calendar"], rows)
    assert len(summary["cells"]) == 16
    onset = summary["cells"][0]
    assert onset["heads"]["net_immediate"]["support"]["known"] == 4
    assert onset["heads"]["net_immediate"]["support"]["scored"] == 3
    assert onset["heads"]["net_wait3"]["support"]["target_states"] == {"immature": 1, "known": 3}


@pytest.mark.parametrize("change", ["projection", "missingnull", "prediction", "cash", "floatcash", "target", "unknownzero", "denominator"])
def test_exact_original_join_tampering_fails(change):
    roster, causal, outcomes = small_fixture()
    selections = rank.select_representatives(roster, causal)
    query = outcomes[0]
    head = query["heads"]["net_immediate"]
    if change == "projection":
        query["label_query"]["reference_close"] = "101"
    elif change == "missingnull":
        del query["admission_reason"]
    elif change == "prediction":
        head["prediction"] = "4"
    elif change == "cash":
        head["net_cash"] = "999"
    elif change == "floatcash":
        head["net_cash"] = float(Fraction(head["net_cash"]))
    elif change == "target":
        head["target"] = "0"
    elif change == "unknownzero":
        head.update(target_state="unknown", target="0", scored=False)
    else:
        head["denominator"] = "99961"
    with pytest.raises(ValueError):
        rank.join_targets(selections, outcomes)


def test_weighted_pairs_exclude_same_cluster_and_preserve_exact_tiny_signs():
    roster, _, _ = small_fixture()
    days = roster["calendar"]
    tiny = Fraction(1, 10 ** 500)
    # Weights: first date A has two episodes (1/8 each), B one (1/4);
    # second date C has one episode (1/2). A1/A2 are never a pair.
    episodes = [("a1", days[0], "A", -tiny, Fraction()),
                ("a2", days[0], "A", tiny, Fraction()),
                ("b", days[0], "B", tiny, Fraction()),
                ("c", days[1], "C", 2 * tiny, tiny)]
    rows = []
    for event_id, day, symbol, target, prediction in episodes:
        for phase in rank.PHASES:
            rows.append({"schema": "EO-rank-row-v1", "event_id": event_id, "session": day,
                         "symbol": symbol, "cluster_id": day + ":" + symbol, "fold": 0, "phase": phase,
                         "key": event_id + phase, "reason": "first_eligible_causal", "joint_known": True,
                         "predictions": dict.fromkeys(rank.Q_HEADS, str(prediction)),
                         "heads": {head: {"target": str(target), "target_state": "known"} for head in rank.Q_HEADS}})
    summary = rank.summarize(days, rows)
    head = summary["cells"][0]["heads"]["net_immediate"]
    cross, same = head["cross_cluster"], head["same_date"]
    assert cross["auc"]["pairs"] == 2
    assert cross["auc"]["pair_weight"] == pytest.approx(3 / 32)
    assert cross["auc"]["value"] == pytest.approx(5 / 6)
    assert cross["concordance"]["pairs"] == 4
    assert cross["concordance"]["value"] == pytest.approx(17 / 18)
    assert cross["target_tie_pairs"] == 1
    assert same["auc"]["value"] == same["concordance"]["value"] == 0.5
    assert head["support"]["positive"] == 3 and head["support"]["negative"] == 1
    sensitivity = head["delete5_sensitivity"]
    assert sensitivity["blocks"] == 8
    assert sensitivity["kind"].endswith("not_confidence_interval")
    assert sensitivity["cross_cluster"]["auc"] == {"valid": 7, "undefined": 1,
                                                    "min": pytest.approx(5 / 6), "max": pytest.approx(5 / 6)}
    empty = summary["cells"][4]["heads"]["net_immediate"]
    assert empty["cross_cluster"]["auc"]["value"] is None
    assert empty["delete5_sensitivity"]["cross_cluster"]["auc"]["undefined"] == 8


def test_equal_targets_have_no_concordance_and_equal_scores_get_half_credit():
    points = [(Fraction(), Fraction(1), 0.5), (Fraction(), Fraction(-1), 0.5)]
    statistics = rank._ordered_pairs(points, False)
    assert statistics["credit_weight"] / statistics["pair_weight"] == 0.5
    tied_targets = [(Fraction(1), Fraction(1), 0.5), (Fraction(2), Fraction(1), 0.5)]
    assert rank._ordered_pairs(tied_targets, False)["pairs"] == 0
    assert rank._target_ties(tied_targets) == (1, 0.25)


@pytest.mark.parametrize("scores,expected", [((-1, 0, 1), 1), ((1, 0, -1), 0), ((0, 0, 0), 0.5)])
def test_perfect_reversed_and_constant_order(scores, expected):
    points = [(Fraction(score), Fraction(target), 1 / 3) for score, target in zip(scores, (-1, 0, 1))]
    for binary in (True, False):
        result = rank._ordered_pairs(points, binary)
        assert result["credit_weight"] / result["pair_weight"] == pytest.approx(expected)
