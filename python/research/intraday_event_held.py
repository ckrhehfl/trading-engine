"""Pure matched-entry holding checkpoints and separately evaluated remainder targets.

Checkpoint eligibility uses only the original modeled entry and observations
received by entry+10/+20. Future expiry coverage never selects the held cohort.
These are separate counterfactual books, not concurrent holdings or live orders.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json

from research.intraday_event_execution import COST, ENTRY_POLICIES, FIRST_RISK, EntryRequest
from research.intraday_event_scenarios import EventFrame, MinuteBar


@dataclass(frozen=True)
class HeldCheckpoint:
    book_id: str
    original_event_id: str
    overlap_group_id: str
    entry_policy: str
    status: str
    reason: str
    checkpoint_offset: int
    entry_offset: int
    expiry_offset: int
    requested_quantity: int
    entry_price: Fraction | None
    unknown_offset: int | None = None
    exit_offset: int | None = None
    stop_decision_offset: int | None = None
    reference_offset: int | None = None
    frame: EventFrame | None = None
    position: tuple[Fraction, Fraction, Fraction] | None = None


@dataclass(frozen=True)
class RemainingGrossTarget:
    book_id: str
    status: str
    reason: str
    value: Fraction | None
    first_offset: int
    expiry_offset: int
    exit_offset: int | None = None
    matured_offset: int | None = None
    unknown_offset: int | None = None
    schema: str = "EL-held-remaining-gross-v1"
    semantics: str = "gross price return from next open to original expiry or receipt-triggered structural exit; no repeated cost"


@dataclass(frozen=True)
class _Progress:
    status: str
    reason: str
    unknown_offset: int | None = None
    exit_offset: int | None = None
    exit_price: Fraction | None = None
    stop_decision_offset: int | None = None


def _bar(bars, offset):
    value = bars.get(offset)
    if value is not None and (not isinstance(value, MinuteBar) or value.offset != offset):
        raise ValueError("Held minute coordinate differs")
    return value


def _validate(request, original, bars, session_length, age_minutes, entry_policy):
    if not isinstance(request, EntryRequest) or not isinstance(original, EventFrame) or not isinstance(bars, Mapping):
        raise TypeError("Original EntryRequest, EventFrame and minute mapping required")
    if type(session_length) is not int or session_length <= 0 or type(age_minutes) is not int or age_minutes not in (10, 20):
        raise ValueError("Known positive session length and entry+10/+20 checkpoint required")
    if entry_policy not in ENTRY_POLICIES:
        raise ValueError("Unknown matched entry policy")
    if (original.decision_offset != request.decision_offset or original.scale != request.scale
            or original.structure != request.structure_stop
            or not 0 <= original.onset_offset <= original.offset < original.decision_offset
            or original.level <= 0 or original.state in ("HELD_10", "HELD_20")
            or not original.event_id or not original.overlap_group_id):
        raise ValueError("Original entry frame and frozen request differ")


def _walk(request, bars, *, entry, expiry, through):
    """Advance receipt-time stops only through the caller's clock boundary.

    The ordering matches fixed execution: due sale, scheduled expiry, then
    newly received close observations; a new stop sells strictly next minute.
    """
    observed, last_availability, pending = entry, None, None
    for clock in range(entry + 1, through + 1):
        if pending is not None and pending + 1 == clock:
            quote = _bar(bars, clock)
            if quote is None:
                return _Progress("unknown", "exit_open_unknown", clock, clock, stop_decision_offset=pending)
            return _Progress("closed", "structural", exit_offset=clock, exit_price=quote.open, stop_decision_offset=pending)
        if clock == expiry:
            quote = _bar(bars, clock)
            if quote is None:
                return _Progress("unknown", "exit_open_unknown", clock, clock)
            return _Progress("closed", "time", exit_offset=clock, exit_price=quote.open)
        missing_due = clock - 3
        if missing_due >= entry:
            due = _bar(bars, missing_due)
            if due is None or due.available_offset > clock:
                return _Progress("unknown", "held_close_unknown", clock)
        while observed < clock:
            quote = _bar(bars, observed)
            if quote is None:
                if observed + 3 <= clock:
                    return _Progress("unknown", "held_close_unknown", clock)
                break
            if quote.available_offset > clock:
                if any((later := _bar(bars, offset)) is not None and later.available_offset <= clock
                       for offset in range(observed + 1, clock)):
                    return _Progress("unknown", "nonchronological_close_availability", clock)
                break
            if last_availability is not None and quote.available_offset < last_availability:
                return _Progress("unknown", "nonchronological_close_availability", clock)
            last_availability = quote.available_offset
            observed += 1
            if quote.close <= request.structure_stop:
                pending = clock
                break
    if pending is not None:
        return _Progress("stop_pending", "structural_exit_priority", exit_offset=pending + 1, stop_decision_offset=pending)
    return _Progress("free_alive", "no_structural_exit_due")


def held_checkpoint(request, original_frame, bars, session_length, *, age_minutes,
                    entry_policy="next_open_capped") -> HeldCheckpoint:
    """One causal checkpoint in a book bound to one original frame and entry arm.

    Entry OPEN is a modeled fill observation, as in the execution contract.
    Later OHLC of that minute never establishes fill. Missing required past
    closes and reversed receipt order are unknown, never flat/no-stop evidence.
    """
    _validate(request, original_frame, bars, session_length, age_minutes, entry_policy)
    entry = request.decision_offset + 1
    expiry, checkpoint = entry + request.expiry_minutes, entry + age_minutes
    cap = request.reference_close + request.scale
    quantity = int(min(FIRST_RISK // (cap * (1 + COST) - request.structure_stop),
                       min(request.capital / 50, request.capital / (1 + COST)) // cap))
    identity = (original_frame.overlap_group_id, original_frame.event_id,
                original_frame.offset, request.decision_offset, entry_policy)
    book_id = "EL-held-book:" + sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    common = dict(book_id=book_id, original_event_id=original_frame.event_id,
        overlap_group_id=original_frame.overlap_group_id, entry_policy=entry_policy,
        checkpoint_offset=checkpoint, entry_offset=entry, expiry_offset=expiry,
        requested_quantity=quantity, entry_price=None)
    if expiry >= session_length:
        return HeldCheckpoint(**common, status="no_entry", reason="expiry_not_inside_session")
    if not quantity:
        return HeldCheckpoint(**common, status="no_entry", reason="insufficient_risk_or_cash_for_one_unit")
    opening = _bar(bars, entry)
    if opening is None:
        return HeldCheckpoint(**common, status="unknown", reason="entry_open_unknown", unknown_offset=entry)
    threshold = cap if entry_policy == "next_open_capped" else request.reference_close
    if opening.open <= request.structure_stop or opening.open > threshold:
        return HeldCheckpoint(**common, status="no_fill", reason="entry_open_at_or_below_stop"
                              if opening.open <= request.structure_stop else "entry_open_above_policy_cap")
    common["entry_price"] = opening.open
    progress = _walk(request, bars, entry=entry, expiry=expiry, through=checkpoint)
    if progress.status != "free_alive":
        return HeldCheckpoint(**common, status=progress.status, reason=progress.reason,
            unknown_offset=progress.unknown_offset, exit_offset=progress.exit_offset,
            stop_decision_offset=progress.stop_decision_offset)
    known = [offset for offset in range(checkpoint)
             if (quote := _bar(bars, offset)) is not None and quote.available_offset <= checkpoint]
    if not known:
        return HeldCheckpoint(**common, status="unknown", reason="checkpoint_reference_unknown")
    latest = max(known)
    reference = bars[latest].close
    conditions = dict(original_frame.conditions)
    conditions.update(held_book_id=book_id, original_event_id=original_frame.event_id,
                      initial_decision_offset=request.decision_offset, initial_frame_state=original_frame.state,
                      modeled_entry_offset=entry, original_expiry_offset=expiry, holding_age=age_minutes)
    frame = EventFrame(original_frame.family, book_id, original_frame.overlap_group_id,
        original_frame.onset_offset, latest, checkpoint, original_frame.interval_start,
        latest + 1, "held_checkpoint", "HELD_" + str(age_minutes), False,
        original_frame.scale, original_frame.level, original_frame.structure, tuple(sorted(conditions.items())))
    position = (Fraction(age_minutes, 30), Fraction(expiry - checkpoint, 30),
                (reference - opening.open) / request.scale)
    return HeldCheckpoint(**common, status="free_alive", reason="no_structural_exit_due",
                          reference_offset=latest, frame=frame, position=position)


def remaining_gross_target(request, original_frame, bars, session_length, *, age_minutes,
                           entry_policy="next_open_capped") -> RemainingGrossTarget:
    """Future target only; never call this to choose the held checkpoint cohort."""
    held = held_checkpoint(request, original_frame, bars, session_length,
                           age_minutes=age_minutes, entry_policy=entry_policy)
    first = held.checkpoint_offset + 1
    common = dict(book_id=held.book_id, first_offset=first, expiry_offset=held.expiry_offset)
    if held.status != "free_alive":
        return RemainingGrossTarget(**common, status="not_applicable", reason=held.status + ":" + held.reason,
                                    value=None, unknown_offset=held.unknown_offset)
    opening = _bar(bars, first)
    if opening is None:
        return RemainingGrossTarget(**common, status="unknown", reason="remaining_start_open_unknown",
                                    value=None, unknown_offset=first)
    progress = _walk(request, bars, entry=held.entry_offset, expiry=held.expiry_offset, through=held.expiry_offset)
    if progress.status != "closed":
        return RemainingGrossTarget(**common, status="unknown", reason=progress.reason, value=None,
                                    exit_offset=progress.exit_offset, unknown_offset=progress.unknown_offset)
    exit_quote = _bar(bars, progress.exit_offset)
    entry_quote = _bar(bars, held.entry_offset)
    maturity = max(opening.available_offset, exit_quote.available_offset, entry_quote.available_offset,
                   progress.exit_offset, progress.stop_decision_offset or 0)
    if maturity >= session_length:
        return RemainingGrossTarget(**common, status="unknown", reason="remaining_not_mature_in_session",
                                    value=None, exit_offset=progress.exit_offset)
    value = progress.exit_price / opening.open - 1
    return RemainingGrossTarget(**common, status="known", reason=progress.reason, value=value,
                                exit_offset=progress.exit_offset, matured_offset=maturity)
