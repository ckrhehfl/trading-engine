"""Pure EE reentry connection; no reader, report, account or orders.

The separate reference session must be independently decoded by the caller.
Its authentication is outside this typed-minute mathematics prototype.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import hashlib
from typing import Literal

from research import intraday_ed_snapshot as codec
from research.intraday_ed_inputs import SessionInputs
from research import intraday_trader_paths as paths
from research import intraday_trader_scenarios as scenarios
from research import intraday_trader_session as session_core
from research import intraday_trader_reentry_reference as reference


POLICIES = reference.POLICIES


@dataclass(frozen=True)
class Reset:
    state: Literal["supported", "unsupported", "unknown", "refusal"]
    reason: str
    previous: scenarios.PreviousTerminal | None
    first_sha256: str
    impulse_high: Decimal | None
    impulse_frozen_at: datetime | None
    terminal_reason: str | None
    determined_at: datetime | None


@dataclass(frozen=True)
class FamilyAttempt:
    family: Literal["EE-A", "EE-B"]
    first: session_core.PolicyResult
    reset: Reset
    second: session_core.PolicyResult | None


@dataclass(frozen=True)
class SessionResult:
    symbol: str
    opening: datetime
    closing: datetime
    source_metadata: bytes
    families: tuple[FamilyAttempt, FamilyAttempt]


def _detection(session: SessionInputs, family: str,
               previous: scenarios.PreviousTerminal | None = None):
    """Fixed standalone candidate math without evaluating a price path."""
    if family not in ("EE-A", "EE-B"):
        raise ValueError("unsupported reentry family")
    issues = []
    detection = scenarios.detect_bars(family, session_core._bars(session, {}, issues),
        symbol=session.symbol, opening=session.opening, closing=session.closing, previous=previous)
    policy = "EE-A-2R-TARGET" if family == "EE-A" else "EE-B"
    candidate = detection.candidate
    if candidate is not None:
        if family == "EE-A":
            target = scenarios._decimal(3 * Fraction(candidate.signal.close) - 2 * Fraction(candidate.stop))
            candidate = replace(candidate, target=target)
            detection = replace(detection, trace=(*detection.trace, scenarios.Transition(
                "risk_target_frozen", candidate.signal.end_at, candidate.available_at, target)))
        candidate = replace(candidate, scenario_id=policy)
    detection = replace(detection, scenario_id=policy, candidate=candidate)
    return detection, tuple(issues)


def _attempt(session: SessionInputs, family: str,
             previous: scenarios.PreviousTerminal | None = None) -> session_core.PolicyResult:
    detection, issues = _detection(session, family, previous)
    policy, candidate = detection.scenario_id, detection.candidate
    outcome = refusal = None
    if candidate is not None:
        admission = "pass" if candidate.eligible is True else "decline" if candidate.eligible is False else "unknown"
        plan = paths.PathPlan(policy, session.symbol, candidate.available_at, candidate.stop,
                              candidate.target, session.opening, session.closing, admission)
        try:
            outcome = paths.evaluate_path(plan, session.minutes)
        except paths.PathRefusal as error:
            refusal = str(error)
    return session_core.PolicyResult(policy, detection, outcome, refusal, issues)


def _derive_reset(first: session_core.PolicyResult, family: str, session: SessionInputs) -> Reset:
    """Called only after this first result passes independent reconstruction."""
    signature = hashlib.sha256(codec.canonical(codec.encode(first))).hexdigest()
    detection, path = first.detection, first.path
    frozen = [item for item in detection.trace if item.kind == "impulse_target_frozen"]
    impulse = frozen[0].level if family == "EE-A" and frozen else None
    frozen_at = frozen[0].available_at if family == "EE-A" and frozen else None

    def finish(state, reason, previous=None, terminal_reason=None, determined_at=None):
        return Reset(state, reason, previous, signature, impulse, frozen_at, terminal_reason, determined_at)

    if first.path_refusal is not None:
        return finish("refusal", "path_refusal:" + first.path_refusal)
    if detection.state == "invalid":
        return finish("refusal", "invalid_detection:" + detection.reason)
    if detection.state == "unknown":
        return finish("unknown", "unknown_detection:" + detection.reason)
    kind = terminal = terminal_reason = None
    if detection.state == "known_no_candidate":
        cause = {"key_level_lost": "cancelled_key", "pullback_low_lost": "cancelled_new_low"}
        expected = cause.get(detection.reason) if family == "EE-A" else (
            "cancelled_new_low" if detection.reason == "parent_low_lost" else None)
        if detection.reason == "confirmation_timeout":
            expected = "expired"
        if expected is None:
            return finish("unsupported", "no_registered_terminal")
        last = detection.trace[-1]
        if last.kind != expected or last.available_at != detection.decision_at:
            raise ValueError("verified cancellation trace changed")
        kind, terminal = "cancelled", last.available_at
        terminal_reason = detection.reason
    elif path is not None:
        if path.status == "unknown":
            return finish("unknown", "unknown_path:" + path.reason)
        if path.status == "declined":
            # Cancellation of modeled eligibility, never an actual fill/flat
            # assertion. Decision-only gates are known before any OPEN lookup.
            if path.reason in ("admission_decline", "same_day_hold_cap_does_not_fit"):
                terminal = detection.decision_at
            elif path.reason in ("entry_outside_frozen_levels", "entry_cost_space_not_strict"):
                if path.entry_at is None or path.entry_open is None:
                    raise ValueError("verified entry refusal lacks observed OPEN")
                terminal = path.entry_at
            else:
                return finish("unsupported", "unregistered_decline_reason:" + path.reason)
            kind = "cancelled"
        else:
            if (path.status != "traded" or path.exit_at is None or path.exit_open is None
                    or path.gross is None or path.net33 is None or path.net43 is None):
                raise ValueError("verified path closure changed")
            kind, terminal = "closed", path.exit_at
        terminal_reason = path.reason
    else:
        raise ValueError("verified candidate has no path")
    if family == "EE-A" and impulse is None:
        return finish("unsupported", "reset_level_unavailable")
    if frozen_at is not None and frozen_at > terminal:
        raise ValueError("reset level not known by terminal")
    previous = scenarios.PreviousTerminal(family, session.symbol, session.opening, 1,
        detection.original_parent_end_at, terminal, kind, impulse)
    scenarios._validate_previous(previous, family, session.symbol, session.opening, session.closing)
    reason = "model_eligibility_cancelled" if kind == "cancelled" else "observed_closed"
    return finish("supported", reason, previous, terminal_reason, terminal)


def evaluate_verified_session(session: SessionInputs, *, reference_session: SessionInputs,
                              source_metadata: bytes = b"{}") -> tuple[SessionResult, reference.VerificationReceipt]:
    """Verify first results before deriving at most one second per family.

    No externally supplied previous/result/terminal can enter this factory.
    Whole-run source identity, original population and cash/flatness are absent.
    """
    session_core._validate_session(session)
    if (not isinstance(reference_session, SessionInputs) or reference_session is session
            or reference_session.minutes is session.minutes or type(source_metadata) is not bytes
            or (reference_session.symbol, reference_session.opening, reference_session.closing)
            != (session.symbol, session.opening, session.closing)):
        raise ValueError("separate reference session and opaque bytes required")
    families = ("EE-A", "EE-B")
    first = tuple(_attempt(session, family) for family in families)
    for family, result in zip(families, first, strict=True):
        reference.verify_first_result(result, reference_session, family)
    attempts = []
    for family, result in zip(families, first, strict=True):
        reset = _derive_reset(result, family, session)
        second = None if reset.previous is None else _attempt(session, family, reset.previous)
        attempts.append(FamilyAttempt(family, result, reset, second))
    result = SessionResult(session.symbol, session.opening, session.closing, source_metadata, tuple(attempts))
    receipt = reference.verify_session_result(result, reference_session, source_metadata=source_metadata)
    return result, receipt


def evaluate_session(session: SessionInputs, *, reference_session: SessionInputs,
                     source_metadata: bytes = b"{}") -> SessionResult:
    """The same verified connection, returning its complete typed session."""
    return evaluate_verified_session(session, reference_session=reference_session,
                                     source_metadata=source_metadata)[0]


def policy_slots(result: SessionResult):
    """Four fixed reporting slots; a null additional stays null."""
    for index, family in enumerate(result.families):
        yield POLICIES[index * 2], family.first, None
        yield POLICIES[index * 2 + 1], family.second, family.reset
