"""Independent event-calendar audit of matched holding queries and targets.

Only immutable public records and frozen risk constants are shared. No producer
function, execution evaluator, feature builder, loader or model is called.
"""

from collections.abc import Mapping
from fractions import Fraction
from hashlib import sha256
import json

from research.intraday_event_execution import COST, ENTRY_POLICIES, FIRST_RISK, EntryRequest
from research.intraday_event_held import HeldCheckpoint, RemainingGrossTarget
from research.intraday_event_scenarios import EventFrame, MinuteBar


def _read(bars, offset):
    item = bars.get(offset)
    if item is not None and (type(item) is not MinuteBar or item.offset != offset):
        raise ValueError("Reference bar coordinate differs")
    return item


def _receipt_calendar(request, bars, entry, expiry, cutoff):
    """Reconstruct ordered observations at discrete receipt/deadline events.

    This jumps between evidence times, unlike the producer's minute clock loop.
    A missing-minute deadline precedes newly received stops at the same time;
    a scheduled liquidation precedes both. Future opens are never consulted
    when the liquidation falls after the requested cutoff.
    """
    observations = {offset: _read(bars, offset) for offset in range(entry, cutoff)}
    missing = {offset + 3 for offset, item in observations.items()
               if item is None or item.available_offset > offset + 3}
    events = {item.available_offset for item in observations.values() if item is not None}
    events.update(missing)
    if expiry <= cutoff:
        events.add(expiry)
    pending_offset = entry
    previous_receipt = None

    def liquidate(execute, reason, decision=None):
        if execute > cutoff:
            return {"status": "stop_pending", "reason": "structural_exit_priority",
                    "exit_offset": execute, "stop_decision_offset": decision}
        quote = _read(bars, execute)
        if quote is None:
            return {"status": "unknown", "reason": "exit_open_unknown", "unknown_offset": execute,
                    "exit_offset": execute, "stop_decision_offset": decision}
        return {"status": "closed", "reason": reason, "exit_offset": execute,
                "exit_price": quote.open, "stop_decision_offset": decision}

    for clock in sorted(at for at in events if entry < at <= cutoff):
        if clock == expiry:
            return liquidate(expiry, "time")
        if clock in missing:
            return {"status": "unknown", "reason": "held_close_unknown", "unknown_offset": clock}
        available = {offset for offset, item in observations.items()
                     if offset < clock and item is not None and item.available_offset <= clock}
        while pending_offset < clock:
            next_item = observations.get(pending_offset)
            if next_item is None:
                break
            if pending_offset not in available:
                if any(offset > pending_offset for offset in available):
                    return {"status": "unknown", "reason": "nonchronological_close_availability", "unknown_offset": clock}
                break
            if previous_receipt is not None and next_item.available_offset < previous_receipt:
                return {"status": "unknown", "reason": "nonchronological_close_availability", "unknown_offset": clock}
            previous_receipt = next_item.available_offset
            pending_offset += 1
            if next_item.close <= request.structure_stop:
                return liquidate(clock + 1, "structural", clock)
    return {"status": "free_alive", "reason": "no_structural_exit_due"}


def held_checkpoint(request, original_frame, bars, session_length, *, age_minutes,
                    entry_policy="next_open_capped") -> HeldCheckpoint:
    if type(request) is not EntryRequest or type(original_frame) is not EventFrame or not isinstance(bars, Mapping):
        raise TypeError("Reference requires immutable entry/frame records and minute mapping")
    if type(session_length) is not int or session_length < 1 or type(age_minutes) is not int or age_minutes not in (10, 20):
        raise ValueError("Reference calendar/checkpoint differs")
    if entry_policy not in ENTRY_POLICIES:
        raise ValueError("Reference entry policy differs")
    frame = original_frame
    if (frame.decision_offset != request.decision_offset or frame.scale != request.scale
            or frame.structure != request.structure_stop or frame.level <= 0
            or not 0 <= frame.onset_offset <= frame.offset < frame.decision_offset
            or frame.state in ("HELD_10", "HELD_20") or not frame.event_id or not frame.overlap_group_id):
        raise ValueError("Reference original frame/request binding differs")
    entry, expiry = request.decision_offset + 1, request.decision_offset + 31
    clock = entry + age_minutes
    cap = request.reference_close + request.scale
    per_share_risk = cap + COST * cap - request.structure_stop
    risk_quantity = FIRST_RISK // per_share_risk
    nominal_quantity = min(request.capital * Fraction(1, 50), request.capital / (1 + COST)) // cap
    quantity = int(min(risk_quantity, nominal_quantity))
    coordinate = [frame.overlap_group_id, frame.event_id, frame.offset, request.decision_offset, entry_policy]
    book = "EL-held-book:" + sha256(json.dumps(coordinate, separators=(",", ":")).encode()).hexdigest()
    fields = dict(book_id=book, original_event_id=frame.event_id, overlap_group_id=frame.overlap_group_id,
                  entry_policy=entry_policy, checkpoint_offset=clock, entry_offset=entry, expiry_offset=expiry,
                  requested_quantity=quantity, entry_price=None)
    if expiry >= session_length or quantity == 0:
        reason = "expiry_not_inside_session" if expiry >= session_length else "insufficient_risk_or_cash_for_one_unit"
        return HeldCheckpoint(**fields, status="no_entry", reason=reason)
    entry_bar = _read(bars, entry)
    if entry_bar is None:
        return HeldCheckpoint(**fields, status="unknown", reason="entry_open_unknown", unknown_offset=entry)
    limit = request.reference_close if entry_policy == "reference_limit_one_shot" else cap
    if not request.structure_stop < entry_bar.open <= limit:
        reason = "entry_open_at_or_below_stop" if entry_bar.open <= request.structure_stop else "entry_open_above_policy_cap"
        return HeldCheckpoint(**fields, status="no_fill", reason=reason)
    fields["entry_price"] = entry_bar.open
    state = _receipt_calendar(request, bars, entry, expiry, clock)
    if state["status"] != "free_alive":
        return HeldCheckpoint(**fields, **{key: value for key, value in state.items() if key != "exit_price"})
    last = None
    for offset in range(clock - 1, -1, -1):
        candidate = _read(bars, offset)
        if candidate is not None and candidate.available_offset <= clock:
            last = candidate
            break
    if last is None:
        return HeldCheckpoint(**fields, status="unknown", reason="checkpoint_reference_unknown")
    context = dict(frame.conditions)
    context.update(held_book_id=book, original_event_id=frame.event_id,
                   initial_decision_offset=request.decision_offset, initial_frame_state=frame.state,
                   modeled_entry_offset=entry, original_expiry_offset=expiry, holding_age=age_minutes)
    projection = EventFrame(frame.family, book, frame.overlap_group_id, frame.onset_offset,
                           last.offset, clock, frame.interval_start, last.offset + 1,
                           "held_checkpoint", f"HELD_{age_minutes}", False,
                           frame.scale, frame.level, frame.structure, tuple(sorted(context.items())))
    position = (Fraction(age_minutes, 30), Fraction(30 - age_minutes, 30),
                (last.close - entry_bar.open) / frame.scale)
    return HeldCheckpoint(**fields, **state, reference_offset=last.offset, frame=projection, position=position)


def remaining_gross_target(request, original_frame, bars, session_length, *, age_minutes,
                           entry_policy="next_open_capped") -> RemainingGrossTarget:
    held = held_checkpoint(request, original_frame, bars, session_length,
                           age_minutes=age_minutes, entry_policy=entry_policy)
    first = held.checkpoint_offset + 1
    fields = dict(book_id=held.book_id, first_offset=first, expiry_offset=held.expiry_offset)
    if held.status != "free_alive":
        return RemainingGrossTarget(**fields, status="not_applicable", reason=held.status + ":" + held.reason,
                                    value=None, unknown_offset=held.unknown_offset)
    start = _read(bars, first)
    if start is None:
        return RemainingGrossTarget(**fields, status="unknown", reason="remaining_start_open_unknown",
                                    value=None, unknown_offset=first)
    outcome = _receipt_calendar(request, bars, held.entry_offset, held.expiry_offset, held.expiry_offset)
    if outcome["status"] != "closed":
        return RemainingGrossTarget(**fields, status="unknown", reason=outcome["reason"], value=None,
                                    exit_offset=outcome.get("exit_offset"), unknown_offset=outcome.get("unknown_offset"))
    exit_at = outcome["exit_offset"]
    finish, entry_bar = _read(bars, exit_at), _read(bars, held.entry_offset)
    receipt = max(start.available_offset, finish.available_offset, entry_bar.available_offset,
                  exit_at, outcome.get("stop_decision_offset") or 0)
    if receipt >= session_length:
        return RemainingGrossTarget(**fields, status="unknown", reason="remaining_not_mature_in_session",
                                    value=None, exit_offset=exit_at)
    gross_price_change = finish.open - start.open
    return RemainingGrossTarget(**fields, status="known", reason=outcome["reason"], value=gross_price_change / start.open,
                                exit_offset=exit_at, matured_offset=receipt)
