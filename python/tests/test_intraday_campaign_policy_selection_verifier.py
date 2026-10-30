"""Invented, hand-derived selection truth tables without producer expectations."""

from dataclasses import replace
from datetime import date, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType

import pytest

from research import intraday_campaign_policy_selection_verifier as oracle
from research import intraday_context_preparation as ea
from research import intraday_pattern_calibration as cc
from research.intraday_campaign_features import PreparedFeatures
from research.intraday_context_experiment import PRICE_BASIS


NAMES = {
    "P04": (),
    "P05": ("target_open_to_signal_return", "peer_open_to_signal_return", "peer_last15_return"),
    "P06": ("target_open_to_signal_return", "peer_open_to_signal_return", "peer_last15_return"),
    "P07": ("peer_first30_return", "peer_last15_return"),
    "P08": ("peer_first30_return", "peer_open_to_signal_return", "peer_last15_return"),
}


def prepared(setup="EA-A", **values):
    day = date(2025, 9, 4)
    opening = cc.session_open(day.isoformat())
    end = opening + timedelta(minutes=60)
    retrieved = opening + timedelta(days=400)
    source = ea.PriceSource(retrieved, "invented prices", None, "invented-v1", "synthetic")
    target = ea.Target("KRX:005930", day, "D+1", opening, cc.session_close(day.isoformat()),
                       PRICE_BASIS, source)
    features = {}
    defaults = {"target_open_to_signal_return": Fraction(2, 100),
                "peer_open_to_signal_return": Fraction(1, 100),
                "peer_last15_return": Fraction(0), "peer_first30_return": Fraction(-5, 1000)}
    defaults.update(values)
    for name, value in defaults.items():
        observed = opening + timedelta(minutes=30) if name == "peer_first30_return" else end
        meta = ea.Provenance(observed, observed + timedelta(minutes=2), retrieved,
                             "invented prices", None, "invented-v1", "synthetic")
        features[name] = ea.Feature(value, ("invented_missing",) if value is None else (), (meta,))
    universe = ea.Provenance(opening - timedelta(days=1), opening - timedelta(hours=1),
                             retrieved, "invented universe", True, "invented-v1", "synthetic")
    return PreparedFeatures("EC-common-observed-peer-min5", setup, target, end,
        end + timedelta(minutes=2), features, (), (), {}, (), {}, universe, "invented-auth-v1")


def selection(policy, state, router=None):
    return {"state": state, "reason": {"selected": "condition_passed", "rejected": "condition_failed",
            "unknown": "required_inputs_unknown", "not_applicable": "setup_not_applicable"}[state],
            "router_state": router, "required_features": NAMES[policy],
            "volume_required": policy in {"P04", "P06"}}


def verify(policy, row, state, *, volume=None, router=None):
    expected = selection(policy, state, router)
    receipt = oracle.verify_selection(policy, row, volume_value=volume, selection=expected)
    assert receipt["verified"] is True
    assert receipt["selection"] == expected
    assert receipt["raw_feature_reconstruction_verified"] is False
    assert receipt["volume_availability_reauthenticated"] is False
    assert receipt["strategy_pass"] is False
    assert receipt["actual_execution_authorized"] is False
    return receipt


@pytest.mark.parametrize("volume,state", [
    (None, "unknown"), (Fraction(0), "rejected"), (Fraction(199999, 100000), "rejected"),
    (Fraction(2), "selected"), (Fraction(200001, 100000), "selected"),
])
def test_p04_volume_exact_threshold_and_true_zero(volume, state):
    verify("P04", prepared(), state, volume=volume)


@pytest.mark.parametrize("target,peer,last,state", [
    (Fraction(1, 100), Fraction(1, 100), Fraction(0), "rejected"),
    (Fraction(0), Fraction(1, 100), Fraction(0), "rejected"),
    (Fraction(2, 100), Fraction(1, 100), Fraction(-1, 100000), "rejected"),
    (Fraction(2, 100), Fraction(1, 100), Fraction(0), "selected"),
    (Fraction(2, 100), Fraction(1, 100), Fraction(1, 100000), "selected"),
    (Fraction(-1, 100), Fraction(-2, 100), Fraction(0), "selected"),
    (None, Fraction(1, 100), Fraction(0), "unknown"),
    (Fraction(0), None, Fraction(-1, 100), "unknown"),
    (Fraction(0), Fraction(1, 100), None, "unknown"),
])
def test_p05_relative_strength_and_stabilization(target, peer, last, state):
    verify("P05", prepared(target_open_to_signal_return=target,
        peer_open_to_signal_return=peer, peer_last15_return=last), state)


@pytest.mark.parametrize("target,last,volume,state", [
    (Fraction(2, 100), Fraction(0), Fraction(2), "selected"),
    (Fraction(2, 100), Fraction(0), Fraction(199999, 100000), "rejected"),
    (Fraction(1, 100), Fraction(0), Fraction(2), "rejected"),
    (Fraction(0), Fraction(-1, 100), None, "unknown"),
    (None, Fraction(-1, 100), Fraction(0), "unknown"),
    (Fraction(0), None, Fraction(0), "unknown"),
])
def test_p06_missing_input_never_economically_short_circuits(target, last, volume, state):
    verify("P06", prepared(target_open_to_signal_return=target, peer_last15_return=last),
           state, volume=volume)


@pytest.mark.parametrize("first,last,state", [
    (Fraction(-501, 100000), Fraction(0), "selected"),
    (Fraction(-5, 1000), Fraction(0), "selected"),
    (Fraction(-499, 100000), Fraction(0), "rejected"),
    (Fraction(-5, 1000), Fraction(-1, 100000), "rejected"),
    (Fraction(0), Fraction(0), "rejected"),
    (None, Fraction(0), "unknown"), (Fraction(0), None, "unknown"),
])
def test_p07_inclusive_negative_half_percent_boundary(first, last, state):
    verify("P07", prepared("EA-B", peer_first30_return=first, peer_last15_return=last), state)


@pytest.mark.parametrize("setup,first,peer,last,router,state", [
    ("EA-A", Fraction(-5, 1000), Fraction(1, 100), Fraction(0), "B", "rejected"),
    ("EA-B", Fraction(-5, 1000), Fraction(1, 100), Fraction(0), "B", "selected"),
    ("EA-A", Fraction(-499, 100000), Fraction(1, 100), Fraction(0), "A", "selected"),
    ("EA-B", Fraction(-499, 100000), Fraction(1, 100), Fraction(0), "A", "rejected"),
    ("EA-A", Fraction(0), Fraction(0), Fraction(0), "cash", "rejected"),
    ("EA-B", Fraction(0), Fraction(0), Fraction(0), "cash", "rejected"),
    ("EA-A", Fraction(-1, 100), Fraction(1, 100), Fraction(-1, 100000), "cash", "rejected"),
    ("EA-B", Fraction(0), Fraction(-1, 100), Fraction(1, 100), "cash", "rejected"),
    ("EA-A", None, Fraction(1, 100), Fraction(0), None, "unknown"),
    ("EA-B", Fraction(-1, 100), None, Fraction(0), None, "unknown"),
    ("EA-A", Fraction(-1, 100), Fraction(1, 100), None, None, "unknown"),
])
def test_p08_b_precedence_both_setups_cash_and_complete_input_requirement(setup, first, peer, last, router, state):
    verify("P08", prepared(setup, peer_first30_return=first,
        peer_open_to_signal_return=peer, peer_last15_return=last), state, router=router)


class Poison:
    def __getattribute__(self, name):
        raise AssertionError(f"unrelated value read: {name}")


@pytest.mark.parametrize("policy,setup", [("P04", "EA-B"), ("P05", "EA-B"),
    ("P06", "EA-B"), ("P07", "EA-A")])
def test_not_applicable_setup_does_not_consume_features_or_volume(policy, setup):
    row = replace(prepared(setup), features={name: Poison() for name in NAMES[policy]})
    verify(policy, row, "not_applicable", volume=Poison())


@pytest.mark.parametrize("policy,setup,state,router", [("P04", "EA-A", "selected", None),
    ("P05", "EA-A", "selected", None), ("P06", "EA-A", "selected", None),
    ("P07", "EA-B", "selected", None), ("P08", "EA-B", "selected", "B")])
def test_only_designated_features_and_no_outcome_or_entry_open_are_consumed(policy, setup, state, router):
    row = prepared(setup)
    features = {name: value if name in NAMES[policy] else Poison() for name, value in row.features.items()}
    features.update(target_last15_return=Poison(), structural_stop_gap_from_signal_close=Poison(),
                    trigger_close_location=Poison(), entry_open=Poison(), outcome=Poison())
    row = replace(row, features=features, source_evidence=Poison(), windows=Poison(),
                  common_peers=Poison(), fixed_peers=Poison(), observed_peers_by_window=Poison())
    verify(policy, row, state, volume=Fraction(2), router=router)


@pytest.mark.parametrize("change", [
    lambda value: value.update(extra=True), lambda value: value.pop("state"),
    lambda value: value.update(state="rejected"), lambda value: value.update(reason="condition_failed"),
    lambda value: value.update(router_state="A"), lambda value: value.update(volume_required=0),
    lambda value: value.update(required_features=list(NAMES["P05"])),
    lambda value: value.update(required_features=tuple(reversed(NAMES["P05"]))),
    lambda value: value.update(required_features=NAMES["P05"] + ("entry_open",)),
])
def test_any_tampered_field_type_order_or_extra_field_is_refused(change):
    value = selection("P05", "selected")
    change(value)
    with pytest.raises(oracle.SelectionVerificationRefusal, match="selection"):
        oracle.verify_selection("P05", prepared(), selection=value)


def test_read_only_selection_mapping_is_supported():
    expected = MappingProxyType(selection("P08", "selected", "B"))
    assert oracle.verify_selection("P08", prepared("EA-B"), selection=expected)["verified"] is True


def test_string_subclass_keys_and_tuple_elements_are_not_exact_output_types():
    class DifferentString(str):
        pass

    expected = selection("P05", "selected")
    changed_key = {DifferentString(name): value for name, value in expected.items()}
    with pytest.raises(oracle.SelectionVerificationRefusal, match="selection_fields_changed"):
        oracle.verify_selection("P05", prepared(), selection=changed_key)
    expected["required_features"] = tuple(DifferentString(name) for name in NAMES["P05"])
    with pytest.raises(oracle.SelectionVerificationRefusal, match="selection_value_changed"):
        oracle.verify_selection("P05", prepared(), selection=expected)


@pytest.mark.parametrize("policy", ["P03", "P09", "P4", 4, None, True])
def test_unregistered_policy_is_refused(policy):
    with pytest.raises(oracle.SelectionVerificationRefusal, match="policy_id"):
        oracle.verify_selection(policy, prepared(), selection={})


@pytest.mark.parametrize("change,path", [
    (lambda row: None, "prepared"),
    (lambda row: replace(row, proxy_id="old-peer"), "proxy_id"),
    (lambda row: replace(row, setup_id="A"), "setup_id"),
    (lambda row: replace(row, authentication_reference=" "), "authentication_reference"),
    (lambda row: replace(row, authentication_reference=True), "authentication_reference"),
    (lambda row: replace(row, historical_timing_certified=True), "unsupported_certification"),
    (lambda row: replace(row, official_index=0), "unsupported_certification"),
    (lambda row: replace(row, actual_execution_authorized=True), "unsupported_certification"),
    (lambda row: replace(row, target={}), "typed_target"),
    (lambda row: replace(row, target=replace(row.target, symbol="005930")), "symbol"),
    (lambda row: replace(row, target=replace(row.target, policy=True)), "target_identity"),
    (lambda row: replace(row, target=replace(row.target, session_date=row.observed_end)), "target_identity"),
    (lambda row: replace(row, target=replace(row.target, price_basis="USD")), "basis_or_unit"),
    (lambda row: replace(row, target=replace(row.target, price_source={})), "typed_price_source"),
    (lambda row: replace(row, observed_end=row.observed_end.replace(tzinfo=None)), "aware_clock"),
    (lambda row: replace(row, decision_at=row.decision_at.astimezone(timezone.utc)), "kst_clock"),
    (lambda row: replace(row, observed_end=row.observed_end + timedelta(minutes=1)), "signal_or_decision"),
    (lambda row: replace(row, decision_at=row.decision_at + timedelta(minutes=1)), "signal_or_decision"),
    (lambda row: replace(row, features=[]), "feature_mapping"),
])
def test_prepared_identity_types_clocks_units_and_unsupported_claims_are_hard_errors(change, path):
    with pytest.raises(oracle.SelectionVerificationRefusal, match=path):
        oracle.verify_selection("P05", change(prepared()), selection=selection("P05", "unknown"))


@pytest.mark.parametrize("value", [True, 0, 0.0, Decimal("0.01"), "1/100"])
def test_required_feature_values_must_be_exact_fraction_or_null(value):
    row = prepared(peer_last15_return=value)
    with pytest.raises(oracle.SelectionVerificationRefusal, match="exact_fraction_or_null"):
        oracle.verify_selection("P05", row, selection=selection("P05", "unknown"))


@pytest.mark.parametrize("value", [True, 0, 2.0, Decimal("2"), Fraction(-1)])
def test_invalid_required_volume_is_not_missingness(value):
    with pytest.raises(oracle.SelectionVerificationRefusal, match="volume_value"):
        oracle.verify_selection("P06", prepared(target_open_to_signal_return=Fraction(0)),
                                volume_value=value, selection=selection("P06", "rejected"))


@pytest.mark.parametrize("change,reason", [
    (lambda feature, row: None, "typed_feature"),
    (lambda feature, row: replace(feature, reasons=[]), "reason_tuple"),
    (lambda feature, row: replace(feature, reasons=(True,)), "reason_tuple"),
    (lambda feature, row: replace(feature, reasons=("missing",)), "feature_state"),
    (lambda feature, row: replace(feature, value=None), "feature_state"),
    (lambda feature, row: replace(feature, provenance=[]), "provenance_tuple"),
    (lambda feature, row: replace(feature, provenance=()), "observed_feature_provenance"),
    (lambda feature, row: replace(feature, provenance=(None,)), "typed_provenance"),
    (lambda feature, row: replace(feature, provenance=(replace(feature.provenance[0],
        available_at=row.decision_at + timedelta(microseconds=1)),)), "future_or_invalid"),
    (lambda feature, row: replace(feature, provenance=(replace(feature.provenance[0],
        available_at=row.observed_end),)), "future_or_invalid"),
    (lambda feature, row: replace(feature, provenance=(replace(feature.provenance[0],
        observation_end=row.observed_end - timedelta(minutes=1)),)), "future_or_invalid"),
    (lambda feature, row: replace(feature, provenance=(replace(feature.provenance[0],
        available_at=row.decision_at.replace(tzinfo=None)),)), "aware_clock"),
    (lambda feature, row: replace(feature, provenance=(replace(feature.provenance[0],
        retrieved_at=row.observed_end - timedelta(microseconds=1)),)), "retrieval_precedes"),
    (lambda feature, row: replace(feature, provenance=(replace(feature.provenance[0],
        is_final=1),)), "invalid_source_metadata"),
    (lambda feature, row: replace(feature, provenance=(replace(feature.provenance[0],
        source=""),)), "invalid_source_metadata"),
])
def test_malformed_or_late_required_feature_is_refused_even_after_known_failed_condition(change, reason):
    row = prepared(target_open_to_signal_return=Fraction(0))
    features = dict(row.features)
    features["peer_last15_return"] = change(features["peer_last15_return"], row)
    with pytest.raises(oracle.SelectionVerificationRefusal, match=reason):
        oracle.verify_selection("P06", replace(row, features=features), volume_value=Fraction(0),
                                selection=selection("P06", "rejected"))


def test_unknown_feature_provenance_still_must_be_causal_and_missing_record_is_invalid():
    row = prepared(peer_last15_return=None)
    feature = row.features["peer_last15_return"]
    row.features["peer_last15_return"] = replace(feature, provenance=(replace(feature.provenance[0],
        available_at=row.decision_at + timedelta(minutes=1)),))
    with pytest.raises(oracle.SelectionVerificationRefusal, match="future_or_invalid"):
        oracle.verify_selection("P05", row, selection=selection("P05", "unknown"))
    del row.features["peer_last15_return"]
    with pytest.raises(oracle.SelectionVerificationRefusal, match="required_feature_record_missing"):
        oracle.verify_selection("P05", row, selection=selection("P05", "unknown"))


def test_empty_unknown_peer_provenance_remains_unknown_not_cash_or_zero():
    row = prepared("EA-B", peer_first30_return=None)
    row.features["peer_first30_return"] = ea.Feature(None, ("insufficient_common_observed_peers",), ())
    verify("P08", row, "unknown")
