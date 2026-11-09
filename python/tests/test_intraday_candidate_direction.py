from copy import deepcopy
from datetime import datetime
from fractions import Fraction

import pytest

from research import intraday_candidate_activity as activity
from research import intraday_candidate_direction as direction
from research.intraday_context_cost_census_inputs import invented_rows
from research.intraday_ed_inputs import MINUTE


def _coordinate(index=0):
    row, roster = next((row, roster) for row, roster in invented_rows()
                       if row["questions"] and row["symbol"] == str(100000 + index))
    return direction.invented_decision(row, roster)[0]


def _feature(coordinate):
    return coordinate["roster"]["questions"][0]["feature"]


def _choice(coordinate):
    choices = direction.selections(coordinate)
    direction.verify_selections(coordinate, choices)
    assert direction.control_choices(choices) == activity.selections(coordinate)
    return next(iter(choices.values()))


@pytest.mark.parametrize("index,state,reason,delta", [
    (0, "select", "rising_close_in_R", "1/4"),
    (13, "decline", "flat_or_falling_close_in_R", "0"),
    (6, "decline", "flat_or_falling_close_in_R", "-1/4"),
    (7, "unresolved", "direction_window_unavailable", None),
])
def test_only_a_strictly_rising_close_selects(index, state, reason, delta):
    coordinate = _coordinate(index)
    choice = _choice(coordinate)
    assert choice["activity_state"] == "select"
    assert choice["activity_ratio"] == "2"
    assert (choice["state"], choice["reason"], choice["direction_delta_in_R"]) == (state, reason, delta)


def test_compares_close_at_exact_offset_without_float_rounding():
    coordinate = _coordinate()
    prices = _feature(coordinate)["prices_in_R"]
    latest = Fraction(prices[-1][3])
    tiny = Fraction(1, 10**24)
    # Valid OHLC bars where the open and the adjacent minute point the other way.
    prices[-6] = [str(latest + 1), str(latest + 2), str(latest - 1), str(latest - tiny)]
    prices[-5] = [str(latest + 1)] * 4
    prices[-7] = [str(latest + 1)] * 4
    assert _choice(coordinate)["direction_delta_in_R"] == str(tiny)
    assert _choice(coordinate)["state"] == "select"

    prices[-6][3] = str(latest + tiny)
    assert _choice(coordinate)["direction_delta_in_R"] == str(-tiny)
    assert _choice(coordinate)["state"] == "decline"


@pytest.mark.parametrize("raw", [None, "0.1", "-2/4", "-0", "1/0", 0])
def test_missing_and_noncanonical_close_is_unresolved(raw):
    coordinate = _coordinate()
    _feature(coordinate)["prices_in_R"][-6][3] = raw
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"], choice["direction_delta_in_R"]) == (
        "unresolved", "direction_window_unavailable", None)
    assert choice["activity_state"] == "select"


@pytest.mark.parametrize("corruption", ["states", "extent", "ohlc", "detached_latest"])
def test_requires_the_ready_price_window_and_original_latest_close(corruption):
    coordinate = _coordinate()
    feature = _feature(coordinate)
    if corruption == "states":
        feature["price_states"][-6] = "late"
    elif corruption == "extent":
        feature["prices_in_R"].pop(0)
    elif corruption == "ohlc":
        feature["prices_in_R"][-6][1] = "-1"
    else:
        feature["prices_in_R"][-1] = ["0"] * 4
    assert _choice(coordinate)["state"] == "unresolved"


@pytest.mark.parametrize("index", [-1, -6])
def test_requires_both_closes_to_have_their_original_causal_clock(index):
    coordinate = _coordinate()
    feature = _feature(coordinate)
    clocks = feature["minute_available_at"]
    clocks[index] = (datetime.fromisoformat(clocks[-1]) + MINUTE).isoformat()
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"], choice["activity_state"], choice["direction_delta_in_R"]) == (
        "unresolved", "activity_window_unavailable", "unresolved", None)


@pytest.mark.parametrize("index,state,reason", [
    (1, "decline", "below_twice_prior_mean"),
    (2, "unresolved", "invalid_pre_entry_geometry"),
    (3, "retain", "no_entry"),
    (4, "unresolved", "activity_window_unavailable"),
    (5, "retain", "unsupported"),
    (18, "retain", "overlap_suppressed"),
    (19, "retain", "question_unsupported"),
])
def test_nonselecting_activity_states_need_no_price_data(index, state, reason):
    coordinate = _coordinate(index)
    del _feature(coordinate)["prices_in_R"]
    choice = _choice(coordinate)
    assert (choice["state"], choice["reason"], choice["direction_delta_in_R"]) == (state, reason, None)
    assert (choice["activity_state"], choice["activity_reason"]) == (state, reason)


@pytest.mark.parametrize("key,invalid", [
    ("state", "select"),
    ("reason", "rising_close_in_R"),
    ("direction_delta_in_R", "1/4"),
    ("activity_state", "decline"),
    ("activity_reason", "below_twice_prior_mean"),
    ("activity_ratio", "1"),
    ("decision_at", "unrelated"),
])
def test_audit_rejects_changed_direction_control_or_identity_fields(key, invalid):
    coordinate = _coordinate(13)
    corrupted = direction.selections(coordinate)
    next(iter(corrupted.values()))[key] = invalid
    with pytest.raises(ValueError, match="sealed selection differs"):
        direction.verify_selections(coordinate, corrupted)


def test_verification_binds_choices_by_identity_even_when_the_map_is_reordered():
    coordinate = _coordinate()
    other = deepcopy(coordinate["decisions"][0])
    feature = other["opportunity"]["feature"]
    feature["question"]["episode_id"][1] = 1
    feature["prices_in_R"][-6] = feature["prices_in_R"][-1][:]
    coordinate["decisions"].append(other)
    coordinate["roster"]["questions"].append(other["opportunity"])
    choices = direction.selections(coordinate)
    assert [choice["state"] for choice in choices.values()] == ["select", "decline"]
    direction.verify_selections(coordinate, dict(reversed(choices.items())))


def test_direction_does_not_read_selected_preparation_or_outcomes():
    coordinate = _coordinate()
    expected = direction.selections(coordinate)
    coordinate["decisions"][0]["selected"] = {"unrelated_future_payoff": "1000"}
    coordinate["modeled_opens"] = [["future", "999999", None]]
    assert direction.selections(coordinate) == expected
