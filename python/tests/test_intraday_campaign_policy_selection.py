"""Hand-derived, invented selector cases; no actual reports, prices or runner."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_campaign_features as features
from research import intraday_campaign_policy_selection as selection


ea, cc = features.ea, features.cc
NAMES_A = ("target_open_to_signal_return", "peer_open_to_signal_return", "peer_last15_return")
NAMES_B = ("peer_first30_return", "peer_last15_return")
NAMES_ROUTER = ("peer_first30_return", "peer_open_to_signal_return", "peer_last15_return")


def fixture(setup="EA-A", **values):
    opening = cc.session_open("2025-09-04")
    observed = opening + 60 * cc.MINUTE
    decision = observed + 2 * cc.MINUTE
    retrieved = datetime(2026, 10, 20, tzinfo=timezone.utc)
    target = ea.Target("KRX:000001", date(2025, 9, 4), "D+1", opening,
        cc.session_close("2025-09-04"), features.eb.PRICE_BASIS,
        ea.PriceSource(retrieved, "invented collector", None, "invented-v1", "synthetic"))
    defaults = {"target_open_to_signal_return": Fraction(2, 100),
                "peer_open_to_signal_return": Fraction(1, 100),
                "peer_first30_return": Fraction(-5, 1000), "peer_last15_return": Fraction(0)}
    defaults.update(values)
    prepared_values = {}
    for name, value in defaults.items():
        end = opening + 30 * cc.MINUTE if name == "peer_first30_return" else observed
        meta = ea.Provenance(end, end + 2 * cc.MINUTE, retrieved,
            "invented collector", None, "invented-v1", "synthetic")
        prepared_values[name] = ea.Feature(value, ("invented_missing",) if value is None else (), (meta,))
    return features.PreparedFeatures(features.PROXY_ID, setup, target, observed, decision,
        prepared_values, (), (), {}, (), {},
        ea.Provenance(opening - timedelta(days=4), opening - timedelta(days=3), retrieved,
                      "invented monthly", True, "invented-month-v1", "synthetic"),
        "invented-authenticated-original-day")


def altered(prepared, name, **changes):
    updated = dict(prepared.features)
    updated[name] = replace(updated[name], **changes)
    return replace(prepared, features=updated)


@pytest.mark.parametrize("policy,names,volume_required", [
    ("P04", (), True), ("P05", NAMES_A, False), ("P06", NAMES_A, True),
    ("P07", NAMES_B, False), ("P08", NAMES_ROUTER, False),
])
def test_exact_result_contract(policy, names, volume_required):
    prepared = fixture("EA-B" if policy == "P07" else "EA-A")
    result = selection.select_policy(policy, prepared, volume_value=Fraction(2))
    assert set(result) == {"state", "reason", "router_state", "required_features", "volume_required"}
    assert result["required_features"] == names
    assert type(result["required_features"]) is tuple
    assert result["volume_required"] is volume_required
    assert result["router_state"] == ("B" if policy == "P08" else None)


@pytest.mark.parametrize("volume,state,reason", [
    (None, "unknown", "required_inputs_unknown"),
    (Fraction(0), "rejected", "condition_failed"),
    (Fraction(1999, 1000), "rejected", "condition_failed"),
    (Fraction(2), "selected", "condition_passed"),
    (Fraction(2001, 1000), "selected", "condition_passed"),
])
def test_p04_exact_original_volume_threshold_and_zero(volume, state, reason):
    result = selection.select_policy("P04", fixture(), volume_value=volume)
    assert (result["state"], result["reason"], result["router_state"]) == (state, reason, None)


@pytest.mark.parametrize("target,peer,last15,state", [
    (Fraction(1, 100), Fraction(1, 100), Fraction(0), "rejected"),
    (Fraction(10001, 1000000), Fraction(1, 100), Fraction(0), "selected"),
    (Fraction(9999, 1000000), Fraction(1, 100), Fraction(0), "rejected"),
    (Fraction(2, 100), Fraction(1, 100), Fraction(-1, 1000000), "rejected"),
    (Fraction(-1, 100), Fraction(-2, 100), Fraction(1, 100), "selected"),
])
def test_p05_relative_strength_strict_and_last15_inclusive(target, peer, last15, state):
    prepared = fixture(target_open_to_signal_return=target, peer_open_to_signal_return=peer,
                       peer_last15_return=last15)
    assert selection.select_policy("P05", prepared)["state"] == state


@pytest.mark.parametrize("volume,target,state", [
    (Fraction(2), Fraction(2, 100), "selected"),
    (Fraction(1999, 1000), Fraction(2, 100), "rejected"),
    (Fraction(0), Fraction(2, 100), "rejected"),
    (Fraction(2), Fraction(1, 100), "rejected"),
    (None, Fraction(0), "unknown"),
    (Fraction(0), None, "unknown"),
])
def test_p06_requires_observed_volume_and_market_even_if_other_condition_fails(volume, target, state):
    prepared = fixture(target_open_to_signal_return=target)
    assert selection.select_policy("P06", prepared, volume_value=volume)["state"] == state


@pytest.mark.parametrize("first30,last15,state", [
    (Fraction(-5, 1000), Fraction(0), "selected"),
    (Fraction(-5001, 1000000), Fraction(0), "selected"),
    (Fraction(-4999, 1000000), Fraction(0), "rejected"),
    (Fraction(-5, 1000), Fraction(-1, 1000000), "rejected"),
])
def test_p07_exact_shock_and_calm_boundaries(first30, last15, state):
    prepared = fixture("EA-B", peer_first30_return=first30, peer_last15_return=last15)
    assert selection.select_policy("P07", prepared)["state"] == state


@pytest.mark.parametrize("first30,peer,last15,router", [
    (Fraction(-5, 1000), Fraction(1, 100), Fraction(0), "B"),
    (Fraction(-5001, 1000000), Fraction(0), Fraction(1, 100), "B"),
    (Fraction(-4999, 1000000), Fraction(1, 100), Fraction(0), "A"),
    (Fraction(0), Fraction(1, 1000000), Fraction(0), "A"),
    (Fraction(0), Fraction(0), Fraction(0), "cash"),
    (Fraction(-5, 1000), Fraction(1, 100), Fraction(-1, 1000000), "cash"),
])
@pytest.mark.parametrize("setup", ["EA-A", "EA-B"])
def test_p08_b_priority_setup_matching_and_cash(first30, peer, last15, router, setup):
    prepared = fixture(setup, peer_first30_return=first30, peer_open_to_signal_return=peer,
                       peer_last15_return=last15)
    result = selection.select_policy("P08", prepared)
    assert result["router_state"] == router
    assert result["state"] == ("selected" if router == setup[-1] else "rejected")
    assert result["reason"] == ("condition_passed" if router == setup[-1] else "condition_failed")


@pytest.mark.parametrize("policy,setup,names", [
    ("P05", "EA-A", NAMES_A), ("P06", "EA-A", NAMES_A),
    ("P07", "EA-B", NAMES_B), ("P08", "EA-A", NAMES_ROUTER),
])
def test_each_required_missing_value_stays_unknown_before_any_economic_branch(policy, setup, names):
    for name in names:
        prepared = fixture(setup, **{name: None})
        result = selection.select_policy(policy, prepared, volume_value=Fraction(0))
        assert (result["state"], result["reason"], result["router_state"]) == (
            "unknown", "required_inputs_unknown", None)


@pytest.mark.parametrize("policy,setup", [("P04", "EA-B"), ("P05", "EA-B"),
                                           ("P06", "EA-B"), ("P07", "EA-A")])
def test_setup_inapplicable_does_not_require_policy_inputs(policy, setup):
    result = selection.select_policy(policy, replace(fixture(setup), features={}), volume_value=object())
    assert (result["state"], result["reason"], result["router_state"]) == (
        "not_applicable", "setup_not_applicable", None)


@pytest.mark.parametrize("value", [0, 2, True, 2.0, Decimal("2"), Fraction(-1)])
@pytest.mark.parametrize("policy", ["P04", "P06"])
def test_invalid_volume_type_or_negative_ratio_hard_refused(policy, value):
    with pytest.raises(selection.PolicySelectionRefusal, match="nonnegative_exact_fraction_volume_required"):
        selection.select_policy(policy, fixture(), volume_value=value)


@pytest.mark.parametrize("value", [0, True, 0.0, Decimal("0"), "0"])
def test_only_exact_fraction_or_null_feature_values_are_accepted(value):
    prepared = altered(fixture(), "peer_last15_return", value=value)
    with pytest.raises(selection.PolicySelectionRefusal, match="exact_fraction_feature_required"):
        selection.select_policy("P05", prepared)


@pytest.mark.parametrize("change,reason", [
    (lambda p: replace(p, proxy_id="old-EA-peer"), "feature_proxy_changed"),
    (lambda p: replace(p, setup_id="EA-C"), "unregistered_setup"),
    (lambda p: replace(p, authentication_reference=" "), "authentication_reference_required"),
    (lambda p: replace(p, target={}), "target_required"),
    (lambda p: replace(p, target=replace(p.target, price_basis="USD")), "price_unit_or_basis_changed"),
    (lambda p: replace(p, target=replace(p.target, price_source=replace(p.target.price_source, source=""))),
     "invalid_price_source"),
    (lambda p: replace(p, decision_at=p.decision_at.replace(tzinfo=None)), "aware_clock_required"),
    (lambda p: replace(p, decision_at=p.decision_at.astimezone(timezone.utc)), "kst_session_clock_required"),
    (lambda p: replace(p, decision_at=p.decision_at + cc.MINUTE), "original_signal_or_decision_clock_changed"),
    (lambda p: replace(p, features=[]), "feature_mapping_required"),
    (lambda p: replace(p, features={}), "required_feature_missing"),
    (lambda p: replace(p, features={**p.features, "peer_last15_return": {"value": Fraction(0)}}),
     "typed_feature_required"),
    (lambda p: altered(p, "peer_last15_return", reasons=("missing",)), "feature_value_reason_conflict"),
    (lambda p: altered(p, "peer_last15_return", value=None), "feature_value_reason_conflict"),
    (lambda p: altered(p, "peer_last15_return", reasons=[]), "invalid_feature_reasons"),
    (lambda p: altered(p, "peer_last15_return", provenance=()), "feature_provenance_required"),
])
def test_invalid_identity_clock_source_unit_or_feature_contract_refused(change, reason):
    with pytest.raises(selection.PolicySelectionRefusal, match=reason):
        selection.select_policy("P05", change(fixture()))


@pytest.mark.parametrize("field,value,reason", [
    ("available_at", "future", "unavailable_or_invalid_source_clock"),
    ("observation_end", "future", "observation_window_changed"),
    ("retrieved_at", "past", "retrieval_precedes_observation"),
    ("available_at", "naive", "aware_clock_required"),
    ("source", "", "source_and_vintage_required"),
    ("data_vintage", "", "source_and_vintage_required"),
    ("is_final", 1, "invalid_finality"),
    ("evidence_level", "certified", "invalid_evidence_level"),
])
def test_required_provenance_refused_including_future_unknown_inputs(field, value, reason):
    prepared = fixture(peer_last15_return=None)
    feature = prepared.features["peer_last15_return"]
    translated = {"future": prepared.decision_at + cc.MINUTE,
                  "past": prepared.observed_end - cc.MINUTE,
                  "naive": prepared.decision_at.replace(tzinfo=None)}
    meta = replace(feature.provenance[0], **{field: translated.get(value, value)})
    prepared = altered(prepared, "peer_last15_return", provenance=(meta,))
    with pytest.raises(selection.PolicySelectionRefusal, match=reason):
        selection.select_policy("P05", prepared)


def test_first30_provenance_must_end_at_first30_not_signal_end():
    prepared = fixture("EA-B")
    meta = prepared.features["peer_first30_return"].provenance[0]
    prepared = altered(prepared, "peer_first30_return", provenance=(replace(
        meta, observation_end=prepared.observed_end),))
    with pytest.raises(selection.PolicySelectionRefusal, match="observation_window_changed"):
        selection.select_policy("P07", prepared)


def test_unknown_without_common_peer_provenance_allowed_but_later_invalid_input_still_refused():
    prepared = altered(fixture(peer_last15_return=None), "peer_last15_return", provenance=())
    assert selection.select_policy("P05", prepared)["state"] == "unknown"
    prepared = altered(fixture(target_open_to_signal_return=None), "peer_last15_return", value=0.0)
    with pytest.raises(selection.PolicySelectionRefusal, match="exact_fraction_feature_required"):
        selection.select_policy("P05", prepared)


class DesignatedOnly(Mapping):
    """Forbid enumeration and access to every non-designated value."""

    def __init__(self, values):
        self.values_by_name = values

    def __getitem__(self, name):
        if name not in self.values_by_name:
            raise AssertionError("unrelated feature or outcome read")
        return self.values_by_name[name]

    def __iter__(self):
        raise AssertionError("unrelated feature names enumerated")

    def __len__(self):
        raise AssertionError("unrelated feature count read")


@pytest.mark.parametrize("policy,names,setup", [("P04", (), "EA-A"), ("P05", NAMES_A, "EA-A"),
    ("P06", NAMES_A, "EA-A"), ("P07", NAMES_B, "EA-B"), ("P08", NAMES_ROUTER, "EA-A")])
def test_reads_only_designated_features_and_never_outcomes_or_prepared_diagnostics(policy, names, setup):
    prepared = fixture(setup)
    poison = DesignatedOnly({})
    prepared = replace(prepared, features=DesignatedOnly({name: prepared.features[name] for name in names}),
        windows=poison, source_evidence=poison, observed_peers_by_window=poison,
        fixed_peers=poison, common_peers=poison, universe_provenance=poison)
    result = selection.select_policy(policy, prepared,
        volume_value=Fraction(2) if policy in {"P04", "P06"} else poison)
    assert result["state"] in {"selected", "rejected"}


def test_no_mutation_or_result_aliasing_and_no_unrelated_missingness_requirement():
    prepared = fixture()
    prepared.features["target_last15_return"] = object()
    original = dict(prepared.features)
    first = selection.select_policy("P05", prepared)
    first["state"] = "changed"
    second = selection.select_policy("P05", prepared)
    assert second["state"] == "selected"
    assert prepared.features == original
    assert all(prepared.features[name] is feature for name, feature in original.items())


@pytest.mark.parametrize("policy", ["P01", "P09", "P05 ", None, 5, []])
def test_unregistered_policy_hard_refused(policy):
    with pytest.raises(selection.PolicySelectionRefusal, match="unregistered_policy"):
        selection.select_policy(policy, fixture())


def test_untyped_prepared_hard_refused():
    with pytest.raises(selection.PolicySelectionRefusal, match="prepared_features_required"):
        selection.select_policy("P05", {})
