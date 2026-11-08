"""Verify EI's fresh rule attempt against an already audited parent.

The caller binds the original session and verifies the first position's
decisions, fills and ledger. This check independently reconstructs the fresh
signal, frozen request, modeled open and fixed second-position accounting.
"""

from dataclasses import replace
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from itertools import tee

from research.intraday_chart_cash import ORDER_FRACTION, Decision, Lot
from research.intraday_context_features import ContextFeatures
from research.intraday_context_features_reference import reference_features
from research.intraday_context_model_inputs import encode
from research.intraday_context_questions_reference import _same, reference_question_frames
from research.intraday_context_reentry import ContextReentryEvidence, ContextReentryIncrement
from research.intraday_context_snapshot import _feature
from research.intraday_context_support_reference import reference_frames
from research.intraday_ed_inputs import FIVE, MINUTE, InputRefusal, SessionInputs
from research.intraday_scenario_management_compare import ManagementReplay
from research.intraday_scenario_management_exit_reference import _modeled_open, _orders, _verify_holding
from research.intraday_scenario_management_report_audit import _audit_arm
from research.intraday_scenario_open_reference import _price_row
from research.intraday_scenario_policy import EntryBounds, Episode, Request, Step
from research.intraday_scenario_rollout import ReplayEvent


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_reentry_reference_" + reason)


def _clock(value: datetime) -> None:
    _need(isinstance(value, datetime) and value.utcoffset() is not None
          and value.second == value.microsecond == 0, "minute_clock_required")


def _bind_parent(entry: ContextFeatures, parent: Episode, session: SessionInputs) -> None:
    _need(type(entry) is ContextFeatures and entry.status == "ready" and type(parent) is Episode
          and type(session) is SessionInputs, "original_types_differ")
    _need(parent.symbol == session.symbol == entry.question.anchor_id[0]
          and session.opening == entry.question.anchor_id[1].start_at
          and parent.start_at == entry.forecast_at == entry.question.path.start_at
          and parent.end_at == entry.question.path.end_at < session.closing
          and type(parent.attempts) is int and parent.attempts == 1
          and type(parent.risk_budget) is Fraction and parent.risk_budget > 0
          and type(parent.cost_bps) is int and parent.cost_bps in (33, 43), "original_parent_differs")


def _evidence(entry: ContextFeatures, parent: Episode, session: SessionInputs,
              through: datetime) -> ContextReentryEvidence:
    close, expiry = parent.closed_at, parent.end_at
    _clock(close)
    _clock(through)
    _need(parent.start_at < close <= expiry and parent.last_at == close and through >= close
          and parent.entered_at is parent.stop is parent.pending is None
          and not parent.book.lots and not parent.book.indeterminate and not parent.book.inventory_uncertain,
          "confirmed_flat_close_required")
    deadline = expiry - FIVE - MINUTE
    cutoff = entry.forecast_at if close >= deadline else min(through, deadline)
    identity = (entry.question_id, sha256(encode(_feature(entry))).hexdigest(), close, expiry)
    raw_frames, question_inputs = tee(reference_frames(session, cutoff))
    questions = reference_question_frames(question_inputs, closing=session.closing)
    entry_found = False
    armed = active_reset = active_episode = None
    for frame, event in zip(raw_frames, questions, strict=True):
        if event.available_at == entry.forecast_at:
            candidates = [question for question in event.questions if question.question_id == entry.question_id]
            _need(len(candidates) == 1 and candidates[0].economic_priority
                  and _same(entry, reference_features(frame, candidates[0])), "original_entry_differs")
            entry_found = True
        if event.status in ("unknown", "invalidated"):
            _need(entry_found, "original_entry_missing")
            if event.status == "unknown":
                return ContextReentryEvidence("unsupported", "fresh_context_prefix_unknown", *identity)
            return ContextReentryEvidence("invalidated", "original_anchor_invalidated", *identity)
        if event.available_at <= close:
            continue
        for question in event.questions:
            if question.stage == "approach":
                active_episode, active_reset, armed = question.episode_id, armed, None
                continue
            if (active_reset is None or question.episode_id != active_episode
                    or question.episode_id == entry.question.episode_id
                    or question.episode_origin_at <= close or not question.economic_priority):
                continue
            _need(entry_found and _same(question.anchor_id, entry.question.anchor_id)
                  and active_reset[2] < event.available_at, "fresh_episode_differs")
            if not entry.question.path.stop < question.start_close < entry.question.path.target:
                return ContextReentryEvidence("invalidated", "fresh_entry_price_geometry_unsupported", *identity,
                                              *active_reset, question, event.available_at)
            return ContextReentryEvidence("ready", "first_fresh_post_exit_breakout", *identity,
                                          *active_reset, question, event.available_at)
        if frame.anchor is not None and armed is None and frame.observation_end - FIVE >= close:
            band_floor = frame.anchor.price - frame.anchor.band
            if all(row.prices[1] < band_floor for row in frame.prefix[-5:]):
                armed = frame.observation_end - FIVE, frame.observation_end, frame.available_at
    _need(entry_found, "original_entry_missing")
    if close >= deadline:
        return ContextReentryEvidence("expired", "insufficient_time_for_fresh_reset", *identity)
    if through >= deadline:
        return ContextReentryEvidence("expired", "no_fresh_breakout_before_original_expiry", *identity)
    return ContextReentryEvidence("pending", "fresh_reset_or_breakout_pending", *identity)


def verify_reentry_evidence(evidence: ContextReentryEvidence, *, original_entry: ContextFeatures,
                            confirmed_parent: Episode, original_session: SessionInputs, through: datetime) -> None:
    """Check the first available reset and breakout without reading an open for execution."""
    _bind_parent(original_entry, confirmed_parent, original_session)
    expected = _evidence(original_entry, confirmed_parent, original_session, through)
    _need(_same(expected, evidence), "signal_or_boundary_differs")


def _entry_fill(parent: Episode, request: Request, price: Fraction | None,
                target: Fraction, rate: Fraction, notional_budget: Fraction) -> Decision:
    book = parent.book
    if price is None:
        frozen = replace(book, inventory_uncertain=True,
                         indeterminate_reasons=(f"missing_entry_price:{book.strategy}:{parent.symbol}",))
        return Decision(frozen, "entry_price_unknown", "indeterminate")
    if price >= target:
        return Decision(book, "entry_at_or_above_target")
    if not request.bounds.stop < price <= request.bounds.max_price:
        return Decision(book, "open_outside_frozen_entry_bounds")
    quantity = request.quantity
    paid_cost = quantity * price * rate
    debit = -(quantity * price + paid_cost)
    lot = Lot(book.next_lot_id, parent.symbol, quantity, quantity, price, paid_cost)
    filled = replace(book, cash=book.cash + debit, lots=(lot,), next_lot_id=book.next_lot_id + 1)
    return Decision(filled, "entered", "modeled", quantity, debit, paid_cost, notional_budget)


def verify_reentry_increment(increment: ContextReentryIncrement, *, original_entry: ContextFeatures,
                             audited_parent: ManagementReplay, original_session: SessionInputs) -> None:
    """Verify the bounded increment using authenticated input and audited parent cash."""
    _need(type(audited_parent) is ManagementReplay and audited_parent.status in ("closed", "unresolved")
          and type(increment) is ContextReentryIncrement, "typed_increment_and_parent_required")
    parent = audited_parent.episode
    _bind_parent(original_entry, parent, original_session)
    baseline = parent.book.cash
    if audited_parent.status == "unresolved":
        expected = ContextReentryIncrement("unresolved", "first_context_holding_unresolved", baseline, parent, None)
    else:
        _clock(parent.closed_at)
        evidence = _evidence(original_entry, parent, original_session, max(parent.closed_at, parent.end_at - FIVE - MINUTE))
        _need(_same(evidence, increment.evidence), "signal_or_boundary_differs")
        if evidence.status == "ready":
            expected = _verify_fresh_execution(increment, parent, original_entry, original_session, evidence)
        else:
            unknown = evidence.status == "unsupported"
            expected = ContextReentryIncrement("unresolved" if unknown else "not_reentered", evidence.reason,
                                               baseline, parent, None if unknown else Fraction(), evidence)
    _need(_same(expected, increment), "increment_result_differs")


def _verify_fresh_execution(increment: ContextReentryIncrement, parent: Episode, entry: ContextFeatures,
                            session: SessionInputs, evidence: ContextReentryEvidence) -> ContextReentryIncrement:
    at = evidence.confirmation_at
    close = evidence.fresh_question.start_close
    path = entry.question.path
    bounds = EntryBounds(path.stop, min(close + path.band, path.target))
    rate = Fraction(parent.cost_bps, 10_000)
    baseline = parent.book.cash
    realized_loss = sum((max(Fraction(), -leg.net_pnl) for leg in parent.book.realized_exits
                         if leg.lot_id >= parent.first_lot_id), Fraction())
    risk = max(Fraction(), parent.risk_budget - realized_loss)
    budget = min(baseline * ORDER_FRACTION, baseline / (1 + rate))
    quantity = min(risk // (bounds.max_price * (1 + rate) - bounds.stop), budget // bounds.max_price)
    if quantity == 0:
        reason = "insufficient_risk_or_cash_for_one_unit"
        step = Step(replace(parent, last_at=at), "wait", reason)
        return ContextReentryIncrement("not_reentered", reason, baseline, step.episode, Fraction(),
                                       evidence, step, events=(ReplayEvent(at, "wait", reason),))

    execute = at + MINUTE
    request = Request("reenter", execute, quantity, bounds, (baseline, baseline))
    step = Step(replace(parent, last_at=at, pending=request, attempts=2), "reenter", "higher_value_of_entry")
    _need(_same(step, increment.request), "frozen_request_differs")
    price = _modeled_open(session, execute)
    fill = _entry_fill(parent, request, price, path.target, rate, budget)
    _need(_same(fill, increment.fill), "original_modeled_fill_differs")
    entered = replace(step.episode, book=fill.book, pending=None, last_at=execute, closed_at=None,
                      stop=path.stop if fill.filled else None, entered_at=execute if fill.filled else None)
    events = (ReplayEvent(at, "reenter", step.reason, execute_at=execute, quantity=quantity),
              ReplayEvent(execute, "reenter", fill.reason, quantity=fill.quantity,
                          fill_status=fill.outcome, open_price=price))
    if not fill.filled:
        unknown = price is None
        return ContextReentryIncrement("unresolved" if unknown else "not_reentered", fill.reason,
                                       baseline, entered, None if unknown else Fraction(), evidence, step, fill, events=events)

    holding = increment.holding
    _need(type(holding) is ManagementReplay and holding.cap == path.end_at
          and holding.episode.attempts == 2 and holding.episode.risk_budget == parent.risk_budget
          and bool(holding.events) and holding.reason == holding.events[-1].reason,
          "fixed_holding_boundary_differs")
    closed = _verify_holding(holding.status, _orders(holding.events), session, entered=execute, expiry=path.end_at,
                             stop=path.stop, quantity=quantity, discretionary=False)
    last = holding.events[-1]
    if closed is not None:
        terminal = replace(entered, book=holding.episode.book, last_at=closed.at, closed_at=closed.at,
                           entered_at=None, stop=None)
        _need(len(holding.events) == 2 and last.reason == "exited", "fixed_close_trace_differs")
    elif last.fill_status is not None:
        book = replace(entered.book, inventory_uncertain=True,
                       indeterminate_reasons=(f"missing_exit_price:{entered.book.strategy}:{entered.symbol}",))
        terminal = replace(entered, book=book, last_at=last.at)
        _need(len(holding.events) == 2 and last.reason == "exit_price_unknown", "unknown_exit_trace_differs")
    else:
        due = last.at - 3 * MINUTE
        row, issue = _price_row(session, due, last.at)
        _need(len(holding.events) == 1 and execute <= due and last.at < path.end_at - MINUTE
              and row is None and _same(last, ReplayEvent(last.at, "unresolved", f"holding_close_unknown:{issue}")),
              "unknown_close_trace_differs")
        for offset in range((due - execute) // MINUTE):
            earlier = execute + offset * MINUTE
            _need(_price_row(session, earlier, earlier + 3 * MINUTE)[0] is not None,
                  "unknown_close_skips_earlier_missing_minute")
        terminal = replace(entered, last_at=last.at - MINUTE)
    _need(_same(holding.episode, terminal), "fixed_holding_terminal_state_differs")
    _, verified = _audit_arm(entered, holding.episode, holding.status, holding.events, fresh=False,
                             read_open=lambda clock: _modeled_open(session, clock))
    net = None if verified is None else verified.net
    _need(net is None or holding.episode.book.cash - baseline == net, "fresh_cash_delta_differs")
    return ContextReentryIncrement(holding.status, holding.reason, baseline, holding.episode, net,
                                   evidence, step, fill, holding, events + holding.events)
