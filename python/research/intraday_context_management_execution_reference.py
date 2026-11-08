"""Verify original-minute execution and cash for a matched EI holding case.

The caller binds the confirmed first position to the independently audited
entry and original opportunity. This layer checks the sale schedule and the
recorded operations. Held-value selection and the fresh reset signal require
their own independent checks.
"""

from dataclasses import replace
from datetime import datetime
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_context_management import ContextManagementCase
from research.intraday_ed_inputs import MINUTE, SessionInputs, _require
from research.intraday_scenario_management_compare import ManagementReplay
from research.intraday_scenario_management_exit_reference import _modeled_open, _orders, _verify_holding
from research.intraday_scenario_management_prices_reference import audited_bar_reader, reconstruct_held_values
from research.intraday_scenario_management_report_audit import _audit_arm
from research.intraday_scenario_policy import Episode


def verify_management_execution(case: ContextManagementCase, *, original_initial: Episode,
                                original_session: SessionInputs) -> dict:
    """Verify a confirmed same-entry fork, preserving unresolved branch outcomes."""
    initial, session = original_initial, original_session
    _require(type(case) is ContextManagementCase and case.status == "confirmed"
             and type(initial) is Episode and case.initial == initial
             and type(session) is SessionInputs and initial.symbol == session.symbol
             and initial.attempts == 1 and len(initial.book.lots) == 1
             and initial.entered_at is not None and initial.stop is not None
             and session.opening <= initial.start_at < initial.entered_at < initial.end_at < session.closing,
             "EI_management_reference_original_initial_differs")
    lot, = initial.book.lots
    observed = {}

    def read_open(at: datetime):
        if at not in observed:
            observed[at] = _modeled_open(session, at)
        return observed[at]

    _require(read_open(initial.entered_at) == lot.entry_price and case.initial_fill is not None
             and case.initial_fill.filled and case.initial_fill.book == initial.book
             and case.initial_fill.quantity == lot.quantity,
             "EI_management_reference_initial_fill_differs")
    cap = min(initial.end_at, initial.entered_at + 30 * MINUTE)
    _require(all(item.mode in ("chart", "chart_context") for item in case.decisions),
             "EI_management_reference_assessment_mode_differs")
    audited = {}
    for mode in ("fixed", "chart", "chart_context"):
        replay = getattr(case, mode)
        _require(type(replay) is ManagementReplay and replay.cap == cap
                 and replay.episode.risk_budget == initial.risk_budget,
                 "EI_management_reference_branch_cap_or_risk_differs")
        clocks = [] if mode != "fixed" else None
        _verify_holding(replay.status, _orders(replay.events), session, entered=initial.entered_at,
                        expiry=initial.end_at, stop=initial.stop, quantity=lot.quantity,
                        discretionary=mode != "fixed", assessment_clocks=clocks)
        _require(clocks is None or tuple(clocks) == tuple(item.question.held.decision_at
                 for item in case.decisions if item.mode == mode), "EI_management_reference_assessment_clocks_differ")
        _, audited[mode] = _audit_arm(initial, replay.episode, replay.status, replay.events,
                                     fresh=False, read_open=read_open)

    increment = case.reentry
    if increment is not None:
        parent = case.chart_context
        _require(increment.parent_arm == "chart_context" and increment.baseline_cash == parent.episode.book.cash
                 and increment.episode.risk_budget == initial.risk_budget
                 and (parent.status == "closed" or (increment.status == "unresolved" and not increment.events
                                                     and increment.episode == parent.episode)),
                 "EI_management_reference_reentry_parent_differs")
        _, audited["reentry"] = _audit_arm(initial, increment.episode, increment.status,
            parent.events + increment.events, fresh=True, read_open=read_open)
        expected = None if audited["reentry"] is None else audited["reentry"].net - audited["chart_context"].net
        _require(increment.net_increment == expected, "EI_management_reference_reentry_increment_differs")
    _require(case.modeled_open_receipts == tuple(sorted(observed.items())),
             "EI_management_reference_original_open_receipts_differ")
    return {"net_by_arm": {mode: None if value is None else value.net for mode, value in audited.items()},
            "original_open_count": len(observed), "discretionary_values_recomputed": False,
            "reentry_reset_verified": False}


def verify_management_case(case: ContextManagementCase, *, original_initial: Episode, original_entry,
                           original_session: SessionInputs, original_history: tuple, fold,
                           read_prior_session, include_reentry: bool) -> dict:
    """Add held questions, source selection, action values and fresh reset checks.

    The caller independently authenticates the original entry, confirmed lot,
    complete prior roster, fold, and raw session reader. Verification uses the
    unchanged holding policy and exact cost basis of the original position.
    """
    from research.intraday_context_held_reference import verify_held_question, verify_held_selection
    from research.intraday_context_reentry_reference import verify_reentry_increment

    result = verify_management_execution(case, original_initial=original_initial, original_session=original_session)
    _require(type(original_history) is tuple and callable(read_prior_session) and type(include_reentry) is bool
             and (case.reentry is not None) is include_reentry and case.opportunity.feature == original_entry,
             "EI_management_reference_original_case_differs")
    decisions_checked = 0
    for mode in ("chart", "chart_context"):
        recorded = {item.question.held.decision_at: item for item in case.decisions if item.mode == mode}
        book = original_initial.book
        for event in getattr(case, mode).events:
            if event.fill_status == "modeled" and event.action == "reduce_half":
                # The execution verifier has already independently audited this fill.
                book = cash.reduce_half(book, original_initial.symbol, exit_price=event.open_price,
                                        requested_quantity=event.quantity).book
            decision = recorded.pop(event.at, None)
            if decision is None:
                continue
            episode = replace(original_initial, book=book, last_at=event.at - MINUTE)
            query = decision.question
            verify_held_question(query, original_entry=original_entry, original_episode=episode,
                                 original_session=original_session)
            selection = verify_held_selection(decision.assessment.selection, query=query, history=original_history,
                                              fold=fold, mode=mode)
            lot, = book.lots
            values = _verify_held_values(decision.assessment, selection, query, original_session, fold,
                                         read_prior_session)
            action, reason = "hold", "management_unsupported"
            if values is not None and values.hold is not None and values.exit is not None:
                half = values.reduce_half if lot.quantity > 1 and not lot.half_reduced else None
                if half is not None and half > values.hold and half > values.exit:
                    action, reason = "reduce_half", "higher_value_of_reduction"
                elif values.exit > values.hold:
                    action, reason = "exit", "higher_value_of_exit"
                else:
                    reason = "retain_position"
            _require((event.action, event.reason) == (action, reason), "EI_management_reference_action_differs")
            decisions_checked += 1
        _require(not recorded, "EI_management_reference_assessment_without_action")
    if include_reentry:
        verify_reentry_increment(case.reentry, original_entry=original_entry, audited_parent=case.chart_context,
                                 original_session=original_session)
    return {**result, "discretionary_values_recomputed": True, "discretionary_decisions_checked": decisions_checked,
            "reentry_reset_verified": include_reentry}


def _verify_held_values(assessment, selection, query, session, fold, read_prior_session):
    if selection.status != "ready":
        _require(assessment.projection is None and assessment.reason == selection.status,
                 "EI_management_reference_unsupported_assessment_differs")
        return None
    pins = {row.question.coordinate[:2]: row.source_sha256 for row in selection.sources}
    sources = tuple(row.question.coordinate for row in selection.sources)
    read = {}

    def original(day, symbol):
        key = day, symbol
        if key == query.coordinate[:2]:
            return session
        _require(key in pins, "EI_management_reference_unselected_prior_read")
        if key not in read:
            read[key] = read_prior_session(day, symbol, pins[key])
        return read[key]

    day, symbol, at = query.coordinate
    lot, = query.episode.book.lots
    reason, values = reconstruct_held_values(day=day, symbol=symbol, at=at, cap=query.held.planning_cap,
        cutoff=fold.first_test_opening, entered_at=query.held.entered_at, quantity=lot.quantity,
        half_reduced=lot.half_reduced, stop=query.episode.stop, downside_penalty=Fraction(1), sources=sources,
        read_session=original, read_bar=audited_bar_reader(original))
    projection = assessment.projection
    _require(projection is not None and assessment.reason == projection.reason == reason
             and (None if projection.rollout is None else projection.rollout.values) == values,
             "EI_management_reference_held_values_differ")
    return values
