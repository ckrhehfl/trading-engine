"""One separately attributed EI post-exit reset attempt, with fixed management.

This is an explicit rule increment, not a neural or learned reentry forecast.
Reset evidence never receives modeled opens. A fresh scanner question identifies
the trigger only; its thirty-minute horizon never renews the original expiry.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_entry import PreparedContextEntry, context_entry_feature_sha256, fill_context_entry
from research.intraday_context_features import ContextFeatures, project_context_features
from research.intraday_context_questions import ContextQuestion, QuestionId, _bar, scan_context_questions
from research.intraday_context_situations import context_frame, scan_context_frames
from research.intraday_ed_inputs import FIVE, MINUTE, SessionInputs
from research.intraday_scenario_management_compare import ManagementReplay, _position_replay
from research.intraday_scenario_management_exit_reference import _modeled_open
from research.intraday_scenario_policy import EntryBounds, Episode, Step, Values, apply_modeled_open, decide
from research.intraday_scenario_rollout import ReplayEvent, _decision_event, _prefer, _visible


SCHEMA = "ei-context-rule-reentry-draft-v1"


@dataclass(frozen=True)
class ContextReentryEvidence:
    status: Literal["ready", "pending", "expired", "unsupported", "invalidated"]
    reason: str
    original_question_id: QuestionId
    entry_feature_sha256: str
    closed_at: datetime
    absolute_expiry: datetime
    reset_start: datetime | None = None
    reset_end: datetime | None = None
    reset_available_at: datetime | None = None
    fresh_question: ContextQuestion | None = None
    confirmation_at: datetime | None = None


@dataclass(frozen=True)
class ContextReentryIncrement:
    status: Literal["closed", "not_reentered", "unresolved"]
    reason: str
    baseline_cash: Fraction
    episode: Episode
    net_increment: Fraction | None
    evidence: ContextReentryEvidence | None = None
    request: Step | None = None
    fill: cash.Decision | None = None
    holding: ManagementReplay | None = None
    events: tuple[ReplayEvent, ...] = ()
    parent_arm: str = "chart_context"
    policy: str = "first_fresh_post_exit_breakout_rule_only_then_fixed_holding"


def _closed(entry: ContextFeatures, episode: Episode, session: SessionInputs) -> None:
    _need(type(entry) is ContextFeatures and entry.status == "ready" and type(episode) is Episode
          and type(session) is SessionInputs, "EI_reentry_typed_entry_episode_session_required")
    _need(episode.symbol == session.symbol == entry.question.anchor_id[0]
          and session.opening == entry.question.anchor_id[1].start_at
          and episode.start_at == entry.forecast_at and episode.end_at == entry.question.path.end_at < session.closing
          and episode.attempts == 1 and episode.pending is None and episode.closed_at is not None
          and episode.entered_at is None and episode.stop is None and not episode.book.lots
          and not episode.book.indeterminate and not episode.book.inventory_uncertain
          and episode.last_at == episode.closed_at and episode.start_at < episode.closed_at <= episode.end_at,
          "EI_reentry_requires_first_confirmed_flat_close")
    _clock(episode.closed_at)
    original = context_frame(session, decision_at=entry.forecast_at)
    _need(project_context_features(original, entry.question) == entry, "EI_reentry_original_feature_differs")


def _verify_parent(prepared: PreparedContextEntry, first: ManagementReplay, session: SessionInputs) -> None:
    """Replay recorded actions from the original paid fill, not the supplied book.

    Action preferences reproduce the saved trace; they are not a second forecast.
    Every old execution price is independently read from its original source row.
    No future-open mapping enters this accounting and protective-rule check.
    """
    request = prepared.step.episode.pending
    _need(request is not None and type(first.events) is tuple and 0 < len(first.events) <= 60
          and all(type(event) is ReplayEvent for event in first.events), "EI_reentry_parent_trace_required")
    initial, fill = fill_context_entry(prepared, request.execute_at, _modeled_open(session, request.execute_at))
    _need(fill.filled, "EI_reentry_parent_initial_fill_differs")
    previous = request.execute_at
    decisions, prices = {}, {}
    for event in first.events:
        _clock(event.at)
        _need(previous < event.at <= first.episode.closed_at, "EI_reentry_parent_trace_clock_differs")
        previous = event.at
        if event.fill_status is None:
            decisions[event.at] = event
        else:
            price = _modeled_open(session, event.at)
            _need(price is not None and event.open_price == price, "EI_reentry_parent_fill_price_differs")
            prices[event.at] = price

    def recorded_action(view):
        event = decisions.get(view.at)
        _need(event is not None and event.action in ("hold", "reduce_half", "exit"),
              "EI_reentry_parent_decision_missing")
        return None if event.reason == "management_unsupported" else _prefer(view.at, event.action)

    rebuilt = _position_replay(initial, session, prices, recorded_action)
    _need(rebuilt == first, "EI_reentry_parent_cash_or_action_lineage_differs")


def context_reentry_evidence(entry: ContextFeatures, closed: Episode, session: SessionInputs,
                             *, through: datetime) -> ContextReentryEvidence:
    """Find the first new EI episode breakout armed by a wholly post-exit bar."""
    _closed(entry, closed, session)
    _clock(through)
    _need(through >= closed.closed_at, "EI_reentry_scan_precedes_confirmed_close")
    last_decision = closed.end_at - FIVE - MINUTE
    common = (entry.question_id, context_entry_feature_sha256(entry), closed.closed_at, closed.end_at)
    if closed.closed_at >= last_decision:
        return ContextReentryEvidence("expired", "insufficient_time_for_fresh_reset", *common)
    limit = min(through, last_decision)
    armed = None
    reset_by_episode = {}
    current = None

    def frames():
        nonlocal current
        for frame in scan_context_frames(session, through=limit):
            current = frame
            yield frame

    for found in scan_context_questions(frames(), closing=session.closing):
        if found.status == "unknown":
            return ContextReentryEvidence("unsupported", "fresh_context_prefix_unknown", *common)
        if found.status == "invalidated":
            return ContextReentryEvidence("invalidated", "original_anchor_invalidated", *common)
        if current.anchor is not None:
            _need(current.anchor == entry.question.anchor_id[1], "EI_reentry_frozen_anchor_changed")
        if current.available_at <= closed.closed_at:
            continue
        for question in found.questions:
            if question.stage == "approach":
                reset_by_episode[question.episode_id] = armed
                armed = None
            reset = reset_by_episode.get(question.episode_id)
            if (question.stage == "breakout" and reset is not None
                    and question.episode_id != entry.question.episode_id
                    and question.episode_origin_at > closed.closed_at and question.economic_priority):
                _need(question.anchor_id == entry.question.anchor_id and reset[0] >= closed.closed_at
                      and reset[1] - reset[0] == FIVE and reset[2] < question.path.start_at
                      and question.path.start_at + MINUTE + FIVE <= closed.end_at,
                      "EI_reentry_fresh_reset_geometry_differs")
                if not 0 < entry.question.path.stop < question.start_close < entry.question.path.target:
                    return ContextReentryEvidence("invalidated", "fresh_entry_price_geometry_unsupported", *common,
                                                   *reset, question, question.path.start_at)
                return ContextReentryEvidence("ready", "first_fresh_post_exit_breakout", *common,
                                               *reset, question, question.path.start_at)
        if current.anchor is not None and _bar(current.prefix[-5:])[1] < current.anchor.price - current.anchor.band:
            start, end = current.observation_end - FIVE, current.observation_end
            # A bar straddling the exit is not fresh reset evidence.
            if start >= closed.closed_at and armed is None:
                armed = start, end, current.available_at
    status = "expired" if through >= last_decision else "pending"
    return ContextReentryEvidence(status, "no_fresh_breakout_before_original_expiry" if status == "expired"
                                   else "fresh_reset_or_breakout_pending", *common)


def replay_context_reentry(prepared: PreparedContextEntry, first: ManagementReplay, session: SessionInputs,
                           opens: Mapping[datetime, Fraction | None]) -> ContextReentryIncrement:
    """Use only the predeclared context-held parent's close; never choose a best arm.

    The public management adapter supplies this parent. Cash change is measured
    from its confirmed close, so the first trade is not counted a second time.
    """
    _need(type(prepared) is PreparedContextEntry and prepared.step is not None and prepared.step.action == "enter"
          and type(first) is ManagementReplay and isinstance(opens, Mapping), "EI_reentry_prepared_parent_required")
    episode = first.episode
    initial = prepared.step.episode
    _need((episode.symbol, episode.start_at, episode.end_at, episode.first_lot_id, episode.risk_budget, episode.cost_bps)
          == (initial.symbol, initial.start_at, initial.end_at, initial.first_lot_id, initial.risk_budget, initial.cost_bps),
          "EI_reentry_original_risk_cost_or_expiry_differs")
    baseline = episode.book.cash
    if first.status != "closed":
        return ContextReentryIncrement("unresolved", "first_context_holding_unresolved", baseline, episode, None)
    _closed(prepared.features, episode, session)
    _verify_parent(prepared, first, session)
    last_decision = max(episode.closed_at, episode.end_at - FIVE - MINUTE)
    evidence = context_reentry_evidence(prepared.features, episode, session, through=last_decision)
    if evidence.status != "ready":
        unknown = evidence.status == "unsupported"
        return ContextReentryIncrement("unresolved" if unknown else "not_reentered", evidence.reason,
                                       baseline, episode, None if unknown else Fraction(), evidence)
    at = evidence.confirmation_at
    visible, issue = _visible(session, at - 3 * MINUTE, at)
    if issue is not None:
        return ContextReentryIncrement("unresolved", "fresh_visible_price_unknown: " + issue,
                                       baseline, episode, None, evidence)
    close = Fraction(visible.ohlc[3])
    _need(close == evidence.fresh_question.start_close, "EI_reentry_confirmation_price_differs")
    original = prepared.features.question
    bounds = EntryBounds(original.path.stop, min(close + original.path.band, original.path.target))
    # Explicit rule-only second attempt. No forecast probability is consulted.
    step = decide(episode, at, visible_minute=visible, values=Values(at, wait=Fraction(), enter=Fraction(1)),
                  bounds=bounds, reset_at=at)
    decision = _decision_event(step)
    if step.action != "reenter":
        return ContextReentryIncrement("not_reentered", step.reason, baseline, step.episode, Fraction(),
                                       evidence, step, events=(decision,))
    execute_at = step.episode.pending.execute_at
    price = opens.get(execute_at)  # First access occurs only after the fixed request exists.
    _need(price is None or type(price) is Fraction and price > 0, "EI_reentry_exact_open_required")
    if price is not None and price >= original.path.target:
        fill = cash.Decision(step.episode.book, "entry_at_or_above_target")
        after = replace(step.episode, pending=None, last_at=execute_at, stop=None, entered_at=None, closed_at=None)
    else:
        after, fill = apply_modeled_open(step.episode, execute_at, price)
    events = (decision, ReplayEvent(execute_at, "reenter", fill.reason, quantity=fill.quantity,
                                   fill_status=fill.outcome, open_price=price))
    if not fill.filled:
        unknown = fill.outcome == "indeterminate"
        return ContextReentryIncrement("unresolved" if unknown else "not_reentered", fill.reason,
            baseline, after, None if unknown else Fraction(), evidence, step, fill, events=events)
    holding = _position_replay(after, session, opens, None)
    known = holding.status == "closed" and not holding.episode.book.indeterminate
    return ContextReentryIncrement("closed" if known else "unresolved", holding.reason, baseline, holding.episode,
        holding.episode.book.cash - baseline if known else None, evidence, step, fill, holding, events + holding.events)
