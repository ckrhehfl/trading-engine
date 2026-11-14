from dataclasses import replace

import pytest

from research.intraday_event_observation import CONTRACT, Observation, OfflineJournal, propose


def packet(**changes):
    original = Observation("s", "e", "A_DOWNFAIL", "WAITING", 50, 50, 50,
                           "a" * 64, "b" * 64, "v1", True,
                           path_probabilities=(.2, .3, .5), fill_probability=.8,
                           net_immediate=.001, net_wait3=.0005)
    if changes.get("position", "flat") != "flat" and "state" not in changes:
        changes["state"] = "HELD_10"
    return replace(original, **changes)


def test_entry_wait_and_decline_are_separate():
    assert propose(packet())["action"] == "PROPOSE_ENTRY"
    assert propose(packet(net_wait3=.002))["action"] == "COMMIT_WAIT3_ENTRY"
    assert propose(packet(net_immediate=-.001, net_wait3=-.002))["action"] == "OBSERVE_NO_COMMITMENT"
    assert propose(packet(net_immediate=.001, net_wait3=.001))["action"] == "PROPOSE_ENTRY"
    assert propose(packet(net_wait3=None))["action"] == "ABSTAIN"
    assert propose(packet(state="INVALIDATED"))["action"] == "NO_ENTRY"


@pytest.mark.parametrize("changes", [{"input_supported": False}, {"input_watermark": 49},
                                     {"forecast_available_offset": 51}, {"forecast_available_offset": 49}])
def test_stale_unknown_or_future_forecast_abstains(changes):
    assert propose(packet(**changes))["action"] == "ABSTAIN"


def test_holding_uses_remaining_gross_not_new_entry_cost_target():
    value = packet(position="full", remaining_gross=.0001, net_immediate=-.02)
    assert propose(value)["action"] == "HOLD"
    assert propose(replace(value, remaining_gross=-.0001))["action"] == "PROPOSE_REDUCTION"
    assert propose(replace(value, remaining_gross=0))["action"] == "PROPOSE_REDUCTION"
    half = replace(value, position="half", state="HELD_20", remaining_gross=-.0001)
    assert propose(half)["action"] == "HOLD"
    assert propose(replace(half, previous_held_remaining_gross=.0001))["action"] == "HOLD"
    assert propose(replace(half, previous_held_remaining_gross=0))["action"] == "PROPOSE_EXIT"
    assert propose(replace(half, position="full", previous_held_remaining_gross=0))["action"] == "PROPOSE_EXIT"
    assert propose(value, management="forecast_full_exit")["action"] == "HOLD"
    assert propose(replace(value, remaining_gross=None))["action"] == "ABSTAIN"
    assert propose(replace(value, remaining_gross=-.02), management="fixed_exit")["action"] == "HOLD"


def test_passive_replay_never_claims_a_fill_or_changes_position():
    journal = OfflineJournal()
    result = journal.record(packet())
    assert result["orders_enabled"] is False and result["execution"] == "not_attempted"
    assert result["position"] == "flat"
    assert journal.record(packet()) == result
    result["position"] = "full"
    assert journal.record(packet())["position"] == "flat"
    with pytest.raises(ValueError, match="revision"):
        journal.record(packet(net_immediate=.2))
    journal.record(packet(net_immediate=.2, data_vintage="v2"))


def test_out_of_order_replay_rejected():
    journal = OfflineJournal()
    journal.record(packet())
    with pytest.raises(ValueError, match="out of decision order"):
        journal.record(packet(event_id="e2", decision_offset=49, input_watermark=49, forecast_available_offset=49))


def test_fixed_wait_commitment_cannot_be_postponed_by_another_prediction():
    journal = OfflineJournal()
    first = journal.record(packet(net_wait3=.002))
    assert first["action"] == "COMMIT_WAIT3_ENTRY"
    later = packet(decision_offset=53, input_watermark=53, forecast_available_offset=53, net_wait3=.005)
    result = journal.record(later)
    assert result["action"] == "FOLLOW_EXISTING_COMMITMENT"
    assert result["original_commitment"]["entry_decision_offset"] == 53
    assert journal.record(later) == result


def test_stale_input_preserves_but_does_not_execute_prior_wait_commitment():
    journal = OfflineJournal()
    journal.record(packet(net_wait3=.002))
    stale = journal.record(packet(decision_offset=52, input_watermark=51, forecast_available_offset=52))
    assert stale["action"] == "ABSTAIN" and stale["execution"] == "not_attempted"
    assert stale["original_commitment"]["entry_decision_offset"] == 53


@pytest.mark.parametrize("changes", [{"path_probabilities": (.2, .3, .6)},
                                     {"fill_probability": 2}, {"net_immediate": float("nan")},
                                     {"source_sha256": "x"}, {"position": "short"}])
def test_invalid_forecast_and_identity_rejected(changes):
    with pytest.raises(ValueError):
        packet(**changes)


def test_preparation_does_not_authorize_activation():
    assert CONTRACT["activation"] == "not_authorized_by_preparation"
    assert CONTRACT["orders_enabled"] is False and CONTRACT["new_period_acquisition"] is False
