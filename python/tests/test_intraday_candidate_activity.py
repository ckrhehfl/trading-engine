from copy import deepcopy
from datetime import datetime
from fractions import Fraction

import pytest

from research import intraday_candidate_activity as activity
from research import intraday_candidate_room as room
from research.intraday_context_cost_census_inputs import invented_rows
from research.intraday_ed_inputs import MINUTE


def _fixture(index=0):
    return next((row, roster) for row, roster in invented_rows()
                if row["questions"] and row["symbol"] == str(100000 + index))


def _feature(coordinate):
    return coordinate["roster"]["questions"][0]["feature"]


def _choice(coordinate):
    choices = activity.selections(coordinate)
    activity.verify_selections(coordinate, choices)
    return next(iter(choices.values()))


def test_twice_prior_mean_is_inclusive_and_invariant_to_common_volume_units():
    coordinate, _ = activity.invented_decision(*_fixture())
    feature = _feature(coordinate)
    # Uneven samples distinguish two means from a comparison of raw sums.
    feature["stored_volumes"] = ["0", "2"] * 12 + ["1"] + ["0", "1", "2", "3", "4"]
    choice = _choice(coordinate)
    assert (choice["state"], choice["activity_ratio"]) == ("select", "2")

    feature["stored_volumes"] = [str(Fraction(raw) * Fraction(3, 7)) for raw in feature["stored_volumes"]]
    assert _choice(coordinate) == choice

    feature["stored_volumes"][-1] = "17/10"
    choice = _choice(coordinate)
    assert (choice["state"], choice["activity_ratio"]) == ("decline", "299/150")


def test_zero_recent_volume_is_known_and_zero_reference_is_unresolved():
    coordinate, _ = activity.invented_decision(*_fixture())
    feature = _feature(coordinate)
    feature["stored_volumes"][-5:] = ["0"] * 5
    choice = _choice(coordinate)
    assert (choice["state"], choice["activity_ratio"]) == ("decline", "0")

    feature["stored_volumes"][:25] = ["0"] * 25
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"], choice["activity_ratio"]) == (
        "unresolved", "nonpositive_prior_activity", None)


@pytest.mark.parametrize("raw,state", [(None, "missing"), ("0", "missing"), ("2", "modeled"),
                                       ("-1", "observed"), ("2/2", "observed"), (2, "observed")])
def test_unknown_or_invalid_stored_volume_is_unresolved(raw, state):
    coordinate, _ = activity.invented_decision(*_fixture())
    feature = _feature(coordinate)
    feature["volume_states"][10], feature["stored_volumes"][10] = state, raw
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"], choice["activity_ratio"]) == (
        "unresolved", "activity_window_unavailable", None)


@pytest.mark.parametrize("change", ["future", "duplicate", "gap", "extent", "end", "start", "before_session"])
def test_requires_a_contiguous_thirty_minute_window_on_the_causal_clock(change):
    coordinate, _ = activity.invented_decision(*_fixture())
    feature = _feature(coordinate)
    clocks = feature["minute_available_at"]
    if change == "future":
        clocks[-1] = (datetime.fromisoformat(feature["forecast_at"]) + MINUTE).isoformat()
    elif change == "duplicate":
        clocks[0] = clocks[1]
    elif change == "gap":
        clocks[1] = None
    elif change == "extent":
        clocks.pop()
    elif change == "end":
        feature["observation_end"] = feature["forecast_at"]
    elif change == "start":
        feature["window_start"] = clocks[0]
    else:
        coordinate["opening"] = clocks[0]
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"], choice["activity_ratio"]) == (
        "unresolved", "activity_window_unavailable", None)


@pytest.mark.parametrize("index,state,reason", [(2, "unresolved", "invalid_pre_entry_geometry"),
                                                (3, "retain", "no_entry"), (5, "retain", "unsupported"),
                                                (18, "retain", "overlap_suppressed"),
                                                (19, "retain", "question_unsupported")])
def test_retains_original_pre_entry_states_without_activity_inputs(index, state, reason):
    coordinate, _ = activity.invented_decision(*_fixture(index))
    del _feature(coordinate)["stored_volumes"]
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"], choice["activity_ratio"]) == (state, reason, None)


def test_adapter_preserves_original_identity_and_uses_only_the_bound_pre_entry_feature():
    source, roster = _fixture()
    original, _ = room.invented_decision(source, roster)
    coordinate, adapted = activity.invented_decision(source, roster)
    question = adapted["questions"][0]
    pair = coordinate["decisions"][0]
    assert adapted["roster"] == coordinate["roster"]
    assert pair["baseline"]["features"] == _feature(coordinate)
    choices = activity.selections(coordinate)
    assert set(choices) == set(room.selections(original)) == {question["question_sha256"]}
    assert all(choices[question["question_sha256"]][key] == question[key] for key in room.METADATA)

    pair["selected"] = {"unrelated_future_payoff": "1"}
    coordinate["modeled_opens"] = [["future", "999999", None]]
    question["baseline"] = {"net": "999999"}
    assert activity.selections(coordinate) == choices

    altered = deepcopy(choices)
    next(iter(altered.values()))["activity_ratio"] = "1"
    with pytest.raises(ValueError, match="sealed selection differs"):
        activity.verify_selections(coordinate, altered)

    pair["baseline"]["features"] = deepcopy(_feature(coordinate))
    pair["baseline"]["features"]["stored_volumes"][0] = "3"
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"]) == ("unresolved", "invalid_pre_entry_geometry")
