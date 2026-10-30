"""Pure fixed EC P04-P08 selection over authenticated, causal features.

This consumes no outcomes, prices or files and grants no execution permission.
Volume is the original stored ratio: its discarded minute provenance and
historical availability are not reconstructed or recertified here. The peer
proxy remains EC-common-observed-peer-min5, not an official market index.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from fractions import Fraction

from research import intraday_campaign_features as features


ea, cc = features.ea, features.cc
MARKET_A_FEATURES = (
    "target_open_to_signal_return", "peer_open_to_signal_return", "peer_last15_return",
)
MARKET_B_FEATURES = ("peer_first30_return", "peer_last15_return")
ROUTER_FEATURES = (
    "peer_first30_return", "peer_open_to_signal_return", "peer_last15_return",
)
REQUIRED_FEATURES = {
    "P04": (), "P05": MARKET_A_FEATURES, "P06": MARKET_A_FEATURES,
    "P07": MARKET_B_FEATURES, "P08": ROUTER_FEATURES,
}


class PolicySelectionRefusal(ValueError):
    """Malformed contracts are hard errors, not unavailable observations."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PolicySelectionRefusal(reason)


def _clock(value: object) -> None:
    _require(type(value) is datetime and value.utcoffset() is not None,
             "aware_clock_required")
    _require(value.utcoffset() == timedelta(hours=9), "kst_session_clock_required")


def _prepared(prepared: features.PreparedFeatures) -> None:
    _require(type(prepared) is features.PreparedFeatures, "prepared_features_required")
    _require(type(prepared.proxy_id) is str and prepared.proxy_id == features.PROXY_ID,
             "feature_proxy_changed")
    _require(type(prepared.setup_id) is str and prepared.setup_id in ea.SETUPS,
             "unregistered_setup")
    _require(type(prepared.authentication_reference) is str
             and bool(prepared.authentication_reference.strip()),
             "authentication_reference_required")
    _require(type(prepared.target) is ea.Target, "target_required")
    try:
        ea._target(prepared.target)
    except ea.PreparationRefusal as error:
        raise PolicySelectionRefusal(str(error)) from None
    target = prepared.target
    for clock in (target.opening, target.closing, prepared.observed_end, prepared.decision_at):
        _clock(clock)
    _require(target.opening == cc.session_open(target.session_date.isoformat())
             and target.closing == cc.session_close(target.session_date.isoformat()),
             "original_session_clock_changed")
    _require(target.opening < prepared.observed_end < prepared.decision_at < target.closing
             and (prepared.observed_end - target.opening) % (5 * cc.MINUTE) == timedelta(0)
             and prepared.decision_at == prepared.observed_end + 2 * cc.MINUTE
             and prepared.observed_end >= target.opening + 30 * cc.MINUTE,
             "original_signal_or_decision_clock_changed")
    _require(type(target.price_basis) is str and target.price_basis == features.eb.PRICE_BASIS,
             "price_unit_or_basis_changed")
    _require(isinstance(prepared.features, Mapping), "feature_mapping_required")


def _value(prepared: features.PreparedFeatures, name: str) -> Fraction | None:
    _require(name in prepared.features, "required_feature_missing")
    feature = prepared.features[name]
    _require(type(feature) is ea.Feature, "typed_feature_required")
    _require(feature.value is None or type(feature.value) is Fraction,
             "exact_fraction_feature_required")
    _require(type(feature.reasons) is tuple
             and all(type(reason) is str and bool(reason.strip()) for reason in feature.reasons)
             and len(set(feature.reasons)) == len(feature.reasons), "invalid_feature_reasons")
    _require(bool(feature.reasons) if feature.value is None else not feature.reasons,
             "feature_value_reason_conflict")
    _require(type(feature.provenance) is tuple
             and (feature.value is None or bool(feature.provenance)),
             "feature_provenance_required")
    expected_end = (prepared.target.opening + 30 * cc.MINUTE
                    if name == "peer_first30_return" else prepared.observed_end)
    for meta in feature.provenance:
        _require(type(meta) is ea.Provenance, "provenance_required")
        try:
            ea._metadata(meta, expected_end, prepared.decision_at)
        except ea.PreparationRefusal as error:
            raise PolicySelectionRefusal(str(error)) from None
    return feature.value


def select_policy(policy_id: str, prepared: features.PreparedFeatures, *,
                  volume_value: Fraction | None = None) -> dict:
    """Select one fixed policy without using result or unrelated-feature fields.

    All designated inputs are validated before a condition is evaluated. Missing
    observations stay unknown even when another observed condition would fail.
    Setup-inapplicable rows require no policy inputs. P08 needs all three peer
    features before routing, with the B branch taking priority over A.
    """
    _require(type(policy_id) is str and policy_id in REQUIRED_FEATURES, "unregistered_policy")
    _prepared(prepared)
    names = REQUIRED_FEATURES[policy_id]
    volume_required = policy_id in {"P04", "P06"}

    def result(state: str, reason: str, router_state: str | None = None) -> dict:
        return {"state": state, "reason": reason, "router_state": router_state,
                "required_features": names, "volume_required": volume_required}

    applicable = (prepared.setup_id == "EA-B" if policy_id == "P07"
                  else True if policy_id == "P08" else prepared.setup_id == "EA-A")
    if not applicable:
        return result("not_applicable", "setup_not_applicable")
    values = {name: _value(prepared, name) for name in names}
    if volume_required:
        _require(volume_value is None or type(volume_value) is Fraction and volume_value >= 0,
                 "nonnegative_exact_fraction_volume_required")
    if any(value is None for value in values.values()) or volume_required and volume_value is None:
        return result("unknown", "required_inputs_unknown")

    router_state = None
    if policy_id == "P04":
        passed = volume_value >= 2
    elif policy_id in {"P05", "P06"}:
        passed = (values["target_open_to_signal_return"] - values["peer_open_to_signal_return"] > 0
                  and values["peer_last15_return"] >= 0)
        if volume_required:
            passed = passed and volume_value >= 2
    elif policy_id == "P07":
        passed = (values["peer_first30_return"] <= Fraction(-5, 1000)
                  and values["peer_last15_return"] >= 0)
    else:
        if (values["peer_first30_return"] <= Fraction(-5, 1000)
                and values["peer_last15_return"] >= 0):
            router_state = "B"
        elif values["peer_open_to_signal_return"] > 0 and values["peer_last15_return"] >= 0:
            router_state = "A"
        else:
            router_state = "cash"
        passed = router_state == prepared.setup_id.removeprefix("EA-")
    return result("selected" if passed else "rejected",
                  "condition_passed" if passed else "condition_failed", router_state)
