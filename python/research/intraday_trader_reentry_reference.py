"""Independent EE reentry math; no producer detector/path/reset imports.

Existing independent minute math and immutable PreviousTerminal input definition
are reused. Shared closed scalar codec/raw parser are trusted boundaries, not
independently implemented parsing or authentication.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
from re import fullmatch

from research import intraday_ed_snapshot as codec
from research.intraday_ed_inputs import SessionInputs
from research import intraday_trader_reference as math
from research.intraday_trader_scenarios import PreviousTerminal


POLICIES = ("EE-A2R-FIRST", "EE-A2R-ADDITIONAL", "EE-B-BASE-FIRST", "EE-B-BASE-ADDITIONAL")
CLAIM = "typed-minute first/additional and terminal-provenance mathematics only; no raw/source/population/account/fill authentication"


@dataclass(frozen=True)
class VerificationReceipt:
    verified: bool
    scenarios: tuple[str, ...]
    first_count: int
    second_count: int
    claim: str = CLAIM


def _detection(session: SessionInputs, family: str, previous=None):
    """Independent standalone candidate math; no producer or path call."""
    math._require(family in ("EE-A", "EE-B"), "unsupported_family")
    math._validate_previous(previous, family, session)
    detection, issues = math._detect(session, family, None, None, previous)
    detection.pop("_eligible", None)
    policy = "EE-A-2R-TARGET" if family == "EE-A" else "EE-B"
    detection["scenario_id"] = policy
    candidate = detection["candidate"]
    if candidate is not None:
        candidate["scenario_id"] = policy
        if family == "EE-A":
            signal = candidate["signal"]
            candidate["target"] = math._decimal(Fraction(signal["close"]) +
                2 * (Fraction(signal["close"]) - Fraction(candidate["stop"])))
            detection["trace"] = (*detection["trace"], dict(kind="risk_target_frozen",
                bar_end_at=signal["end_at"], available_at=signal["available_at"], level=candidate["target"]))
    return detection, issues


def _attempt(session: SessionInputs, family: str, previous=None) -> dict:
    detection, issues = _detection(session, family, previous)
    policy, candidate = detection["scenario_id"], detection["candidate"]
    eligible = None
    if candidate is not None:
        info = candidate["information"]
        eligible = None if info["state"] == "unknown" else info["state"] == "pass" or not info["applied"]
    path, refusal = (None, None) if candidate is None else math._path(session, policy, candidate, eligible)
    return dict(scenario_id=policy, detection=detection, path=path, path_refusal=refusal, issues=issues)


def _reset(first: dict, family: str, session: SessionInputs) -> dict:
    detection, path = first["detection"], first["path"]
    impulse = frozen_at = None
    if family == "EE-A":
        for transition in detection["trace"]:
            if transition["kind"] == "impulse_target_frozen":
                math._require(impulse is None, "duplicate_impulse_freeze")
                impulse, frozen_at = transition["level"], transition["available_at"]
    result = dict(state="unsupported", reason="no_registered_terminal", previous=None,
        first_sha256=hashlib.sha256(codec.canonical(codec.encode(first))).hexdigest(),
        impulse_high=impulse, impulse_frozen_at=frozen_at, terminal_reason=None, determined_at=None)
    terminal = kind = terminal_reason = None
    if first["path_refusal"] is not None:
        result.update(state="refusal", reason="path_refusal:" + first["path_refusal"])
    elif detection["state"] in ("invalid", "unknown"):
        result.update(state="refusal" if detection["state"] == "invalid" else "unknown",
            reason=("invalid_detection:" if detection["state"] == "invalid" else "unknown_detection:") + detection["reason"])
    elif detection["state"] == "known_no_candidate":
        cancellation = ((family == "EE-A" and detection["reason"] in ("key_level_lost", "pullback_low_lost"))
                        or (family == "EE-B" and detection["reason"] == "parent_low_lost"))
        if cancellation or detection["reason"] == "confirmation_timeout":
            event = detection["trace"][-1]
            expected_kind = ("expired" if detection["reason"] == "confirmation_timeout" else
                             "cancelled_key" if detection["reason"] == "key_level_lost" else "cancelled_new_low")
            math._require(event["kind"] == expected_kind and event["available_at"] == detection["decision_at"],
                          "reference_cancellation_trace_differs")
            terminal, kind = detection["decision_at"], "cancelled"
            terminal_reason = detection["reason"]
    else:
        math._require(path is not None, "reference_candidate_path_missing")
        if path["status"] == "declined":
            candidate = detection["candidate"]
            decision = candidate["signal"]["available_at"]
            entry = decision.replace(second=0, microsecond=0) + timedelta(minutes=1)
            if path["reason"] == "admission_decline":
                math._require(candidate["information"]["state"] == "decline", "reference_admission_decline_differs")
                terminal = decision
            elif path["reason"] == "same_day_hold_cap_does_not_fit":
                math._require(entry + timedelta(minutes=60) > session.closing, "reference_cap_decline_differs")
                terminal = decision
            elif path["reason"] in ("entry_outside_frozen_levels", "entry_cost_space_not_strict"):
                math._require(path["entry_at"] == entry and isinstance(path["entry_open"], Decimal)
                    and path["entry_open"].is_finite() and path["entry_open"] > 0, "reference_entry_decline_unobserved")
                terminal = entry
            else:
                result["reason"] = "unregistered_decline_reason:" + path["reason"]
            if terminal is not None:
                kind, terminal_reason = "cancelled", path["reason"]
        elif path["status"] == "unknown":
            result.update(state="unknown", reason="unknown_path:" + path["reason"])
        else:
            math._require(path["status"] == "traded" and path["exit_at"] is not None
                and isinstance(path["exit_open"], Decimal) and path["exit_open"].is_finite() and path["exit_open"] > 0
                and all(type(path[name]) is Fraction for name in ("gross", "net33", "net43")),
                "reference_incomplete_closure")
            terminal, kind = path["exit_at"], "closed"
            terminal_reason = path["reason"]
    if terminal is not None:
        if family == "EE-A" and impulse is None:
            result["reason"] = "reset_level_unavailable"
        else:
            math._require(frozen_at is None or frozen_at <= terminal, "reference_future_reset_level")
            previous = PreviousTerminal(family, session.symbol, session.opening, 1,
                detection["original_parent_end_at"], terminal, kind, impulse)
            math._validate_previous(previous, family, session)
            result.update(state="supported", reason="model_eligibility_cancelled" if kind == "cancelled" else "observed_closed",
                previous=previous, terminal_reason=terminal_reason, determined_at=terminal)
    return result


def _validate(session: SessionInputs) -> None:
    math._require(isinstance(session, SessionInputs), "typed_session_required")
    math._require(type(session.symbol) is str and fullmatch(r"KRX:[0-9A-Z]{6}", session.symbol) is not None
                  and isinstance(session.minutes, Mapping), "invalid_session_identity_or_minutes")
    math._clock(session.opening)
    math._clock(session.closing)
    math._require(session.closing > session.opening and session.closing.date() == session.opening.date()
        and session.opening.utcoffset() == session.closing.utcoffset()
        and not session.opening.second and not session.opening.microsecond
        and not session.closing.second and not session.closing.microsecond
        and (session.closing - session.opening) % timedelta(minutes=5) == timedelta(), "invalid_session_clock")


def _compare(actual, expected) -> None:
    math._record_shape(actual, expected)
    if math._canonical(actual) != math._canonical(expected):
        raise math.ReferenceRefusal("reentry_mathematical_mismatch")


def _properties(actual, expected) -> None:
    candidate = actual.detection.candidate
    if candidate is not None:
        info = expected["detection"]["candidate"]["information"]
        eligible = None if info["state"] == "unknown" else info["state"] == "pass" or not info["applied"]
        if (math._canonical(candidate.eligible) != math._canonical(eligible)
                or math._canonical(candidate.available_at) != math._canonical(expected["detection"]["decision_at"])):
            raise math.ReferenceRefusal("reentry_candidate_property_mismatch")


def verify_first_result(actual, session: SessionInputs, family: str) -> None:
    try:
        _validate(session)
        math._require(family in ("EE-A", "EE-B"), "unsupported_family")
        expected = _attempt(session, family)
        _compare(actual, expected)
        _properties(actual, expected)
    except math._InputError as error:
        raise math.ReferenceRefusal(str(error)) from error


def expected_session(session: SessionInputs, *, source_metadata: bytes = b"{}") -> dict:
    _validate(session)
    math._require(type(source_metadata) is bytes, "opaque_metadata_bytes_required")
    families = []
    for family in ("EE-A", "EE-B"):
        first = _attempt(session, family)
        reset = _reset(first, family, session)
        previous = reset["previous"]
        second = None if previous is None else _attempt(session, family, previous)
        families.append(dict(family=family, first=first, reset=reset, second=second))
    return dict(symbol=session.symbol, opening=session.opening, closing=session.closing,
                source_metadata=source_metadata, families=tuple(families))


def verify_session_result(actual, session: SessionInputs, *, source_metadata: bytes = b"{}") -> VerificationReceipt:
    try:
        expected = expected_session(session, source_metadata=source_metadata)
        _compare(actual, expected)
        for observed, compared in zip(actual.families, expected["families"], strict=True):
            _properties(observed.first, compared["first"])
            if observed.second is not None:
                _properties(observed.second, compared["second"])
        return VerificationReceipt(True, POLICIES, 2,
            sum(item["second"] is not None for item in expected["families"]))
    except math._InputError as error:
        raise math.ReferenceRefusal(str(error)) from error
