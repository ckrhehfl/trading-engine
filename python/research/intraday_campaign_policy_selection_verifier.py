"""Independent, source-only oracle for the fixed EC P04--P08 selection rules.

The producer's selector, decision, and reason never supply expected values.
Only the designated causal feature records are inspected. Callers must still
authenticate the original inputs and independently reconstruct their feature
arithmetic with the existing feature verifier. A stored relative-volume scalar
does not restore its discarded minute provenance or certify its availability.
This module performs no IO, detector replay, outcome calculation, or research run.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from fractions import Fraction
import re

from research import intraday_context_preparation as ea
from research import intraday_pattern_calibration as cc
from research.intraday_campaign_features import PreparedFeatures
from research.intraday_context_experiment import PRICE_BASIS


class SelectionVerificationRefusal(ValueError):
    """Malformed causal inputs or a selection that differs from the fixed rule."""


def _need(condition: bool, path: str, reason: str) -> None:
    if not condition:
        raise SelectionVerificationRefusal(f"{path}: {reason}")


def _text(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _clock(value: object, path: str, *, session: bool = False) -> None:
    _need(type(value) is datetime and value.utcoffset() is not None,
          path, "aware_clock_required")
    _need(not session or value.utcoffset() == timedelta(hours=9),
          path, "kst_clock_required")


def _identity(prepared: object) -> None:
    _need(type(prepared) is PreparedFeatures, "prepared", "prepared_features_required")
    _need(type(prepared.proxy_id) is str
          and prepared.proxy_id == "EC-common-observed-peer-min5",
          "prepared.proxy_id", "registered_proxy_required")
    _need(type(prepared.setup_id) is str and prepared.setup_id in {"EA-A", "EA-B"},
          "prepared.setup_id", "registered_setup_required")
    _need(_text(prepared.authentication_reference), "prepared.authentication_reference",
          "authentication_reference_required")
    for name in ("historical_timing_certified", "official_index", "actual_execution_authorized"):
        _need(getattr(prepared, name) is False, f"prepared.{name}", "unsupported_certification")
    target = prepared.target
    _need(type(target) is ea.Target, "prepared.target", "typed_target_required")
    _need(type(target.symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", target.symbol) is not None,
          "prepared.target.symbol", "invalid_symbol")
    _need(type(target.session_date) is date and type(target.policy) is str
          and target.policy in {"D+1", "D+2"}, "prepared.target", "invalid_target_identity")
    for name in ("opening", "closing"):
        _clock(getattr(target, name), f"prepared.target.{name}", session=True)
    day = target.session_date.isoformat()
    _need(target.opening == cc.session_open(day) and target.closing == cc.session_close(day),
          "prepared.target", "original_session_clock_changed")
    _need(type(target.price_basis) is str and target.price_basis == PRICE_BASIS,
          "prepared.target.price_basis", "price_basis_or_unit_changed")
    source = target.price_source
    _need(type(source) is ea.PriceSource, "prepared.target.price_source", "typed_price_source_required")
    _clock(source.retrieved_at, "prepared.target.price_source.retrieved_at")
    _need(_text(source.source) and _text(source.data_vintage)
          and (source.is_final is None or type(source.is_final) is bool)
          and type(source.evidence_level) is str
          and source.evidence_level in {"synthetic", "observed", "assumed", "unknown"},
          "prepared.target.price_source", "invalid_source_metadata")
    _clock(prepared.observed_end, "prepared.observed_end", session=True)
    _clock(prepared.decision_at, "prepared.decision_at", session=True)
    _need(target.opening + timedelta(minutes=30) <= prepared.observed_end
          < prepared.decision_at < target.closing
          and (prepared.observed_end - target.opening) % timedelta(minutes=5) == timedelta(0)
          and prepared.decision_at == prepared.observed_end + timedelta(minutes=2),
          "prepared", "original_signal_or_decision_clock_changed")
    _need(source.retrieved_at >= prepared.observed_end,
          "prepared.target.price_source", "retrieval_precedes_observation")
    _need(isinstance(prepared.features, Mapping), "prepared.features", "feature_mapping_required")


def _value(prepared: PreparedFeatures, name: str) -> Fraction | None:
    path = f"prepared.features[{name!r}]"
    _need(name in prepared.features, path, "required_feature_record_missing")
    feature = prepared.features[name]
    _need(type(feature) is ea.Feature, path, "typed_feature_required")
    _need(feature.value is None or type(feature.value) is Fraction,
          f"{path}.value", "exact_fraction_or_null_required")
    _need(type(feature.reasons) is tuple and all(_text(reason) for reason in feature.reasons),
          f"{path}.reasons", "reason_tuple_required")
    _need((feature.value is None) == bool(feature.reasons), path, "feature_state_inconsistent")
    _need(type(feature.provenance) is tuple, f"{path}.provenance", "provenance_tuple_required")
    _need(feature.value is None or bool(feature.provenance),
          f"{path}.provenance", "observed_feature_provenance_required")
    expected_end = (prepared.target.opening + timedelta(minutes=30)
                    if name == "peer_first30_return" else prepared.observed_end)
    for index, provenance in enumerate(feature.provenance):
        here = f"{path}.provenance[{index}]"
        _need(type(provenance) is ea.Provenance, here, "typed_provenance_required")
        for field in ("observation_end", "available_at", "retrieved_at"):
            _clock(getattr(provenance, field), f"{here}.{field}")
        _need(provenance.observation_end == expected_end
              and expected_end + timedelta(minutes=2) <= provenance.available_at <= prepared.decision_at,
              here, "future_or_invalid_feature_clock")
        _need(provenance.retrieved_at >= expected_end, here, "retrieval_precedes_observation")
        _need(_text(provenance.source) and _text(provenance.data_vintage)
              and (provenance.is_final is None or type(provenance.is_final) is bool)
              and type(provenance.evidence_level) is str
              and provenance.evidence_level in {"synthetic", "observed", "assumed", "unknown"},
              here, "invalid_source_metadata")
    return feature.value


def _compare_selection(selection: object, expected: dict) -> None:
    _need(isinstance(selection, Mapping), "selection", "mapping_required")
    _need(all(type(name) is str for name in selection) and set(selection) == set(expected),
          "selection", "selection_fields_changed")
    for name, value in expected.items():
        actual = selection[name]
        _need(type(actual) is type(value), f"selection.{name}", "selection_type_changed")
        if type(value) is tuple:
            _need(len(actual) == len(value)
                  and all(type(item) is str and item == wanted for item, wanted in zip(actual, value)),
                  f"selection.{name}", "selection_value_changed")
        else:
            _need(actual == value, f"selection.{name}", "selection_value_changed")


def verify_selection(policy_id: str, prepared: PreparedFeatures, *,
                     volume_value: Fraction | None = None, selection: Mapping) -> dict:
    """Recompute the fixed rule independently and refuse any inexact selection.

    This verifies the selector only. The original-source feature reconstruction
    and stored-volume sidecar verification remain separate caller obligations.
    Entry OPEN, outcomes, and unrelated feature values are never inputs here.
    """
    _need(type(policy_id) is str and policy_id in {"P04", "P05", "P06", "P07", "P08"},
          "policy_id", "registered_policy_required")
    _identity(prepared)
    if policy_id == "P04":
        names, volume_required, applicable = (), True, prepared.setup_id == "EA-A"
    elif policy_id in {"P05", "P06"}:
        names = ("target_open_to_signal_return", "peer_open_to_signal_return", "peer_last15_return")
        volume_required, applicable = policy_id == "P06", prepared.setup_id == "EA-A"
    elif policy_id == "P07":
        names = ("peer_first30_return", "peer_last15_return")
        volume_required, applicable = False, prepared.setup_id == "EA-B"
    else:
        names = ("peer_first30_return", "peer_open_to_signal_return", "peer_last15_return")
        volume_required, applicable = False, True

    router = None
    if not applicable:
        state, reason = "not_applicable", "setup_not_applicable"
    else:
        values = tuple(_value(prepared, name) for name in names)
        if volume_required:
            _need(volume_value is None or type(volume_value) is Fraction,
                  "volume_value", "exact_fraction_or_null_required")
            _need(volume_value is None or volume_value >= 0,
                  "volume_value", "negative_relative_volume")
        # All designated inputs are validated before observing missingness or
        # applying any economic conjunction, including P06's volume condition.
        if any(value is None for value in values) or (volume_required and volume_value is None):
            state, reason = "unknown", "required_inputs_unknown"
        else:
            if policy_id == "P04":
                passed = volume_value >= Fraction(2)
            elif policy_id in {"P05", "P06"}:
                target, peer, last15 = values
                passed = target - peer > 0 and last15 >= 0
                if policy_id == "P06":
                    passed = passed and volume_value >= Fraction(2)
            elif policy_id == "P07":
                first30, last15 = values
                passed = first30 <= Fraction(-5, 1000) and last15 >= 0
            else:
                first30, peer, last15 = values
                if first30 <= Fraction(-5, 1000) and last15 >= 0:
                    router = "B"
                elif peer > 0 and last15 >= 0:
                    router = "A"
                else:
                    router = "cash"
                passed = (prepared.setup_id == "EA-A" and router == "A"
                          or prepared.setup_id == "EA-B" and router == "B")
            state = "selected" if passed else "rejected"
            reason = "condition_passed" if passed else "condition_failed"
    expected = {"state": state, "reason": reason, "router_state": router,
                "required_features": names, "volume_required": volume_required}
    _compare_selection(selection, expected)
    return {"mode": "source-only-ec-independent-policy-selection-verification",
            "verified": True, "policy_id": policy_id, "setup_id": prepared.setup_id,
            "selection": expected, "raw_feature_reconstruction_verified": False,
            "volume_availability_reauthenticated": False, "strategy_pass": False,
            "actual_execution_authorized": False}
