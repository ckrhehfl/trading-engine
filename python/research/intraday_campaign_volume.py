"""Pure P04 stored-volume input projection; no runner or actual registration.

Accept an already decoded canonical EB EA-A report. The caller authenticates
the original report hash, reviewed builder/specification and access boundary.
Only stored identities, signal clocks, relative_volume and original arm states
are inspected. No old outcome, return, other feature or common-observable flag
is read. Nothing here authenticates discarded minute-level volume provenance.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import re


POLICIES = ("D+1", "D+2")
KST = timezone(timedelta(hours=9))
SIGNAL_CLOCKS = ("decision_at", "observed_through", "signal_bar_start",
                 "signal_bar_end", "setup_bar_end")
LIMITATIONS = (
    "source_only_preparation_not_registered_or_executable_policy",
    "original_report_hash_and_reviewed_builder_contract_authenticated_by_caller",
    "stored_relative_volume_has_no_minute_provenance_or_available_at",
    "volume_availability_not_reauthenticated_from_report",
    "observed_signal_and_membership_clocks_only",
    "no_old_outcome_return_other_feature_or_common_observable_flag_read",
    "no_price_recomputation_account_replay_or_performance_result",
)


class VolumeProjectionRefusal(ValueError):
    """A malformed stored contract is an error, never an unknown observation."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise VolumeProjectionRefusal(reason)


def _mapping(value: object, reason: str) -> Mapping:
    _require(isinstance(value, Mapping), reason)
    return value


def _day(value: object) -> str:
    _require(type(value) is str, "invalid_session")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise VolumeProjectionRefusal("invalid_session") from None
    _require(parsed.isoformat() == value, "noncanonical_session")
    return value


def _week(value: str) -> str:
    day = date.fromisoformat(value)
    return (day - timedelta(days=day.weekday())).isoformat()


def _clock(value: object, *, day: str | None = None) -> datetime:
    _require(type(value) is str, "invalid_clock_type")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise VolumeProjectionRefusal("invalid_clock") from None
    _require(parsed.utcoffset() is not None, "aware_clock_required")
    _require(day is None or parsed.astimezone(KST).date().isoformat() == day,
             "clock_session_changed")
    return parsed


def _symbol(value: object) -> str:
    _require(type(value) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", value) is not None,
             "invalid_symbol")
    return value


def _reasons(value: object) -> tuple[str, ...]:
    _require(type(value) is list and all(type(item) is str and bool(item.strip()) for item in value),
             "invalid_feature_reasons")
    _require(len(set(value)) == len(value), "duplicate_feature_reasons")
    return tuple(value)


def _feature(value: object) -> dict:
    feature = _mapping(value, "invalid_volume_feature")
    _require(set(feature) == {"value", "reasons"}, "stored_volume_feature_shape_changed")
    reasons = _reasons(feature["reasons"])
    encoded = feature["value"]
    if encoded is None:
        _require(bool(reasons), "unknown_volume_requires_reason")
        return {"value": None, "reasons": reasons}
    _require(not reasons, "observed_volume_has_unknown_reasons")
    encoded = _mapping(encoded, "canonical_fraction_required")
    _require(set(encoded) == {"numerator", "denominator"}, "canonical_fraction_required")
    numerator, denominator = encoded["numerator"], encoded["denominator"]
    _require(type(numerator) is str and re.fullmatch(r"0|[1-9][0-9]*", numerator) is not None
             and type(denominator) is str and re.fullmatch(r"[1-9][0-9]*", denominator) is not None,
             "invalid_nonnegative_fraction")
    try:
        exact = Fraction(int(numerator), int(denominator))
    except ValueError:
        raise VolumeProjectionRefusal("invalid_nonnegative_fraction") from None
    _require(str(exact.numerator) == numerator and str(exact.denominator) == denominator,
             "noncanonical_fraction")
    return {"value": exact, "reasons": reasons}


def _source_at(sources: list, index: object, identity: tuple[str, str]) -> int:
    _require(type(index) is int and 0 <= index < len(sources), "invalid_source_index")
    source = _mapping(sources[index], "invalid_source")
    _require((source.get("session"), source.get("symbol")) == identity,
             "source_index_identity_changed")
    return index


def _counts(rows: list[dict]) -> dict:
    signals = Counter(row["signal_state"] for row in rows)
    arms = Counter(row["volume_only"] for row in rows)
    return {"targets": len(rows), "signals": signals["signal"],
            "known_no_signal": signals["known_no_signal"], "unknown_signals": signals["unknown"],
            "common_control": sum(row["common_control"] for row in rows),
            "selected": sum(row["selected"] for row in rows),
            "volume_only_states_all_targets": {state: arms[state] for state in ("selected", "rejected", "unknown")}}


def _project_row(row: Mapping, member: Mapping, day_index: int, membership: Mapping,
                 sources: list, volume_sources: list) -> dict:
    day, symbol = membership["session"], member["symbol"]
    _require(type(row.get("membership_day_index")) is int
             and row["membership_day_index"] == day_index, "membership_day_index_changed")
    stored_member = _mapping(row.get("membership_target"), "invalid_membership_target")
    _require(type(stored_member.get("rank")) is int
             and type(stored_member.get("symbol")) is str
             and stored_member == member, "membership_target_changed")
    identity = (day, symbol)
    source_index = _source_at(sources, row.get("source_index"), identity)
    volume_index = _source_at(volume_sources, row.get("volume_source_index"), identity)
    signal = _mapping(row.get("signal"), "invalid_signal")
    _require(signal.get("hypothesis_id") == "H1-morning-trend-pullback-reclaim", "signal_hypothesis_changed")
    state, reason = signal.get("state"), signal.get("reason")
    _require(type(state) is str and state in {"signal", "known_no_signal", "unknown"}
             and type(reason) is str and bool(reason.strip()), "invalid_signal_state_or_reason")
    clocks = {}
    for field in SIGNAL_CLOCKS:
        _require(field in signal, "signal_clock_field_missing")
        clocks[field] = None if signal[field] is None else _clock(signal[field], day=day)
    decision, observed = clocks["decision_at"], clocks["observed_through"]
    if decision is not None:
        _require(_clock(membership["selected_at"], day=day) <= decision,
                 "membership_unavailable_at_decision")
    if observed is not None and decision is not None:
        _require(observed <= decision, "observed_after_decision")
    if state == "signal":
        _require(all(value is not None for value in clocks.values()), "signal_clocks_required")
        start, end = clocks["signal_bar_start"], clocks["signal_bar_end"]
        _require(clocks["setup_bar_end"] <= start < end <= observed <= decision,
                 "contradictory_signal_clocks")
        _require(end - start == timedelta(minutes=5) and observed == end
                 and decision == end + timedelta(minutes=2), "registered_signal_clock_changed")
    arms = _mapping(row.get("arms"), "invalid_arms")
    basic, arm = arms.get("basic"), arms.get("volume_only")
    _require(type(basic) is str and type(arm) is str
             and basic in {"selected", "rejected", "unknown"}
             and arm in {"selected", "rejected", "unknown"}, "invalid_arm_state")
    features = _mapping(row.get("features"), "invalid_features")
    feature, uncomputed_reasons = None, ()
    if state != "signal":
        expected = "unknown" if state == "unknown" else "rejected"
        _require(basic == arm == expected and not features, "nonsignal_feature_or_arm_conflict")
        volume_state = "not_applicable"
    elif "relative_volume" in features:
        _require(basic == "selected", "signal_basic_arm_conflict")
        feature = _feature(features["relative_volume"])
        volume_state = "unknown" if feature["value"] is None else "observed"
        expected = ("unknown" if feature["value"] is None
                    else "selected" if feature["value"] >= 2 else "rejected")
        _require(arm == expected, "volume_value_arm_conflict")
    else:
        # This is the original EA early-return branch, not a fabricated feature.
        _require(set(features) == {"clock"} and basic == arm == "unknown",
                 "signal_volume_feature_missing")
        clock = _mapping(features["clock"], "invalid_clock_feature")
        _require(set(clock) == {"value", "reasons"} and clock["value"] is None
                 and _reasons(clock["reasons"]) == ("same_day_cap_infeasible",),
                 "invalid_clock_feature")
        volume_state, uncomputed_reasons = "not_computed", ("same_day_cap_infeasible",)
    common = state == "signal" and volume_state == "observed"
    return {"session": day, "symbol": symbol, "membership_day_index": day_index,
            "membership_target": {"symbol": symbol, "rank": member["rank"]}, "source_index": source_index,
            "volume_source_index": volume_index, "signal_state": state, "signal_reason": reason,
            **{field: signal[field] for field in SIGNAL_CLOCKS},
            "volume_state": volume_state, "relative_volume": feature,
            "uncomputed_reasons": uncomputed_reasons, "volume_only": arm,
            "common_control": common, "selected": common and arm == "selected"}


def project_volume_inputs(report: Mapping, *, inputs_authenticated: bool) -> dict:
    """Project full original populations, including no-signal/unknown/zero days.

    The new common control is original A signal AND stored volume observed:
    selected plus explicitly rejected. It never requires other features or a
    future label. Source authentication is a caller assertion, not proved here.
    Returned clocks are original strings; no new feature or availability is made.
    Only the JSON-decoded canonical Fraction representation is accepted.
    """
    _require(inputs_authenticated is True, "caller_authenticated_inputs_required")
    report = _mapping(report, "invalid_report")
    _require(report.get("mode") == "fixed-ea-context-exploratory-comparison"
             and report.get("setup_id") == "EA-A", "wrong_original_report_identity")
    sessions = report.get("reference_sessions")
    _require(type(sessions) is list and bool(sessions), "invalid_reference_sessions")
    sessions = [_day(day) for day in sessions]
    _require(sessions == sorted(set(sessions)), "invalid_reference_sessions")
    weeks = sorted({_week(day) for day in sessions})
    _require(report.get("reference_weeks") == weeks, "reference_weeks_changed")
    matrix = _mapping(report.get("source_target_matrix"), "invalid_target_matrix")
    variants = _mapping(report.get("variants"), "invalid_variants")
    _require(set(matrix) == set(variants) == set(POLICIES), "timing_policies_changed")
    sources, volume_sources = report.get("sources"), report.get("volume_sources")
    _require(type(sources) is list and type(volume_sources) is list, "source_lists_required")
    result = {"mode": "ec-volume-input-sidecar-preparation", "setup_id": "EA-A",
              "scope": "original_per_policy_targets_stored_volume_only",
              "limitations": LIMITATIONS, "source_authentication": "caller_assertion",
              "volume_availability_reauthenticated": False, "performance_computed": False,
              "policy_results_not_pooled": True, "reference_sessions": list(sessions),
              "reference_weeks": weeks, "variants": {}}
    for policy in POLICIES:
        memberships = _mapping(matrix[policy], "invalid_target_policy").get("days")
        days = _mapping(variants[policy], "invalid_report_policy").get("days")
        _require(type(memberships) is list and len(memberships) == len(sessions)
                 and type(days) is list, "original_population_changed")
        original = {}
        for raw in days:
            row = _mapping(raw, "invalid_target_row")
            identity = (_day(row.get("session")), _symbol(row.get("symbol")))
            _require(identity not in original, "duplicate_target_identity")
            original[identity] = row
        projected, expected = [], set()
        for index, day in enumerate(sessions):
            membership = _mapping(memberships[index], "invalid_membership_day")
            _require(membership.get("session") == day, "membership_session_changed")
            available = _clock(membership.get("available_at"))
            selected = _clock(membership.get("selected_at"), day=day)
            _require(available <= selected, "membership_available_after_selection")
            members = membership.get("targets")
            _require(type(members) is list, "invalid_membership_targets")
            ranks = set()
            for raw_member in members:
                member = _mapping(raw_member, "invalid_membership_target")
                identity = (day, _symbol(member.get("symbol")))
                rank = member.get("rank")
                _require(type(rank) is int and rank > 0 and rank not in ranks, "invalid_membership_rank")
                ranks.add(rank)
                _require(identity not in expected, "duplicate_membership_identity")
                expected.add(identity)
                _require(identity in original, "original_target_missing")
                projected.append(_project_row(original[identity], member, index, membership,
                                              sources, volume_sources))
        _require(set(original) == expected, "extra_original_target")
        result["variants"][policy] = {
            "days": projected, "counts": _counts(projected),
            "by_session": {day: _counts([row for row in projected if row["session"] == day]) for day in sessions},
            "by_week": {week: _counts([row for row in projected if _week(row["session"]) == week]) for week in weeks}}
    return result
