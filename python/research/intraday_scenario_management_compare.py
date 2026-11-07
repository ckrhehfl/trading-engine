"""Draft EH matched holding policies and one separately attributed reentry."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_ed_inputs import FIVE, MINUTE, Minute, SessionInputs, _clock, _require, _session
from research.intraday_scenario_policy import EntryBounds, Episode, Values, apply_modeled_open, decide
from research.intraday_scenario_rollout import ReplayEvent, _decision_event, _held_step, _visible
from research.intraday_scenario_state import ReentryEvidence, ReentryRoute, ScenarioEvent, _observed, reentry_evidence


@dataclass(frozen=True)
class DecisionObservation:
    at: datetime
    episode: Episode
    minute: Minute
    planning_cap: datetime
    fresh: ReentryEvidence | None = None


@dataclass(frozen=True)
class ManagementReplay:
    episode: Episode
    cap: datetime
    status: Literal["closed", "unresolved"]
    reason: str
    events: tuple[ReplayEvent, ...]


@dataclass(frozen=True)
class ReentryReplay:
    episode: Episode
    status: Literal["closed", "not_reentered", "unresolved"]
    reason: str
    evidence: ReentryEvidence | None = None
    events: tuple[ReplayEvent, ...] = ()
    holding: ManagementReplay | None = None


@dataclass(frozen=True)
class ManagementComparison:
    initial: Episode
    fixed: ManagementReplay
    dynamic: ManagementReplay
    reentry: ReentryReplay | None


ManagementForecast = Callable[[DecisionObservation], Values | None]
ReentryForecast = Callable[[DecisionObservation], tuple[Values, EntryBounds] | None]
ModeledOpens = Mapping[datetime, Fraction | None]


def _fill(episode: Episode, at: datetime, opens: ModeledOpens) -> tuple[Episode, ReplayEvent]:
    request = episode.pending
    _require(request is not None and request.execute_at == at, "eh_management_requires_requested_open")
    price = opens.get(at)
    episode, outcome = apply_modeled_open(episode, at, price)
    return episode, ReplayEvent(at, request.action, outcome.reason, quantity=outcome.quantity,
                                fill_status=outcome.outcome, open_price=price)


def _position_replay(initial: Episode, session: SessionInputs, opens: ModeledOpens,
                     values_at: ManagementForecast | None) -> ManagementReplay:
    entered_at = initial.entered_at
    _require(entered_at is not None and initial.last_at == entered_at, "eh_management_requires_confirmed_entry_clock")
    cap = min(entered_at + 30 * MINUTE, initial.end_at)
    episode = replace(initial, end_at=cap)
    at = last_decision = entered_at
    events = []

    def result(status: Literal["closed", "unresolved"], reason: str) -> ManagementReplay:
        return ManagementReplay(replace(episode, end_at=initial.end_at), cap, status, reason, tuple(events))

    while at < cap:
        at += MINUTE
        if episode.pending is not None:
            episode, fill = _fill(episode, at, opens)
            events.append(fill)
            if fill.fill_status != "modeled":
                return result("unresolved", fill.reason)
            if not episode.book.lots:
                return result("closed", fill.reason)
            continue

        step, issue = _held_step(episode, session, at, last_decision)
        if step is None:
            events.append(ReplayEvent(at, "unresolved", issue))
            return result("unresolved", issue)
        due = at - 3 * MINUTE
        forecast_grid = not (at - session.opening - 2 * MINUTE) % FIVE
        if step.action == "hold" and values_at is not None and forecast_grid and due >= entered_at:
            row, issue = _visible(session, due, at)
            if issue is not None:
                reason = f"holding_close_unknown:{issue}"
                events.append(ReplayEvent(at, "unresolved", reason))
                return result("unresolved", reason)
            # A forecast callback sees only the validated current observation
            # and the current book, with no path or modeled-open mapping.
            view = DecisionObservation(at, replace(episode, end_at=initial.end_at), row, cap)
            values = values_at(view)
            _require(values is None or type(values) is Values, "invalid_eh_management_forecast")
            step = decide(episode, at, visible_minute=row, values=values)
            events.append(_decision_event(step))
        elif step.action != "hold":
            events.append(_decision_event(step))
        episode = step.episode
        last_decision = at
        if step.action == "unresolved":
            return result("unresolved", step.reason)
    return result("unresolved", "management_cap_inventory_unresolved")


def _exit_route(initial: Episode, closed: ManagementReplay, session: SessionInputs,
                event: ScenarioEvent) -> tuple[ReentryRoute | None, str]:
    entry_at, exit_at = initial.entered_at, closed.episode.closed_at
    _require(entry_at is not None and exit_at is not None and entry_at < exit_at,
             "eh_reentry_requires_confirmed_holding_interval")
    level = event.level.price
    exit_open = closed.events[-1].open_price
    _require(exit_open is not None, "eh_reentry_requires_known_exit_open")
    lowest_open = min(initial.book.lots[0].entry_price, exit_open)
    if lowest_open < level:
        return "below_level", "known_open_below_level"

    offset = (entry_at - session.opening) // MINUTE
    rows, issues = _observed(session, offset, (exit_at - entry_at) // MINUTE, exit_at + 2 * MINUTE)
    lowest = min(lowest_open, *(Fraction(row.ohlc[2]) for row in rows)) if rows else lowest_open
    if lowest < level:
        return "below_level", "known_low_below_level"
    if issues:
        return None, f"exit_path_unknown:{issues[0].reason}"
    if lowest == level:
        return None, "exit_path_touched_level"
    return "above_level", "observed_path_above_level"


def _fresh_replay(initial: Episode, dynamic: ManagementReplay, session: SessionInputs, opens: ModeledOpens,
                  event: ScenarioEvent, values_at: ManagementForecast | None,
                  reentry_at: ReentryForecast) -> ReentryReplay:
    episode = dynamic.episode
    if dynamic.status != "closed":
        return ReentryReplay(episode, "unresolved", "first_close_unresolved")
    closed_at = episode.closed_at
    _require(closed_at is not None and not episode.book.lots and episode.attempts == 1,
             "eh_reentry_requires_first_confirmed_close")
    elapsed = (closed_at - session.opening) // MINUTE
    first_fresh_bar = (elapsed + 4) // 5 * 5
    at = session.opening + (first_fresh_bar + 7) * MINUTE
    last = episode.end_at - 6 * MINUTE
    if at > last:
        return ReentryReplay(episode, "not_reentered", "fresh_time_expired")
    route, reason = _exit_route(initial, dynamic, session, event)
    if route is None:
        return ReentryReplay(episode, "unresolved", reason)

    evidence = None
    while at <= last:
        evidence = reentry_evidence(session, event, exit_confirmed_at=closed_at, route=route, through=at)
        if evidence.status != "pending":
            break
        at += FIVE
    if evidence is None or evidence.status != "ready":
        if evidence is not None and evidence.status == "support_unknown":
            return ReentryReplay(episode, "unresolved", "fresh_evidence_unknown", evidence)
        reason = "fresh_time_expired" if evidence is None or evidence.status == "pending" else evidence.status
        return ReentryReplay(episode, "not_reentered", reason, evidence)

    row, issue = _visible(session, at - 3 * MINUTE, at)
    if issue is not None:
        return ReentryReplay(episode, "unresolved", f"fresh_close_unknown:{issue}", evidence)
    fill_at = at + MINUTE
    cap = min(fill_at + 30 * MINUTE, episode.end_at)
    forecast = reentry_at(DecisionObservation(at, episode, row, cap, evidence))
    if forecast is None:
        return ReentryReplay(episode, "not_reentered", "fresh_forecast_unavailable", evidence)
    _require(type(forecast) is tuple and len(forecast) == 2 and type(forecast[0]) is Values
             and type(forecast[1]) is EntryBounds, "invalid_eh_reentry_forecast")
    values, bounds = forecast
    _require(evidence.confirmation is not None and evidence.confirmation.available_at == at,
             "eh_reentry_requires_first_confirmation_clock")
    step = decide(episode, at, visible_minute=row, values=values, bounds=bounds,
                  reset_at=evidence.confirmation.available_at)
    decision = _decision_event(step)
    if step.action != "reenter":
        return ReentryReplay(step.episode, "not_reentered", step.reason, evidence, (decision,))
    episode, fill = _fill(step.episode, fill_at, opens)
    events = decision, fill
    if fill.fill_status == "indeterminate":
        return ReentryReplay(episode, "unresolved", fill.reason, evidence, events)
    if fill.fill_status == "declined":
        return ReentryReplay(episode, "not_reentered", fill.reason, evidence, events)
    holding = _position_replay(episode, session, opens, values_at)
    return ReentryReplay(holding.episode, holding.status, holding.reason, evidence, events, holding)


def compare_management(
    initial: Episode, session: SessionInputs, opens: ModeledOpens, *, values_at: ManagementForecast | None,
    event: ScenarioEvent | None = None, reentry_at: ReentryForecast | None = None,
) -> ManagementComparison:
    """Fork the same paid initial lot. Restore the original expiry after each replay.

    Both holding arms check the same one-minute structural close and use the
    earlier of the original event expiry and 30 minutes from the fill. The
    dynamic callback runs only on the completed five-minute publication grid.
    A separate reentry contrast starts from the exact known dynamic close.
    """
    _session(session)
    _require(type(initial) is Episode and initial.symbol == session.symbol and initial.pending is None
             and initial.attempts == 1 and initial.closed_at is None and initial.stop is not None
             and not initial.book.indeterminate and not initial.book.inventory_uncertain
             and len(initial.book.lots) == 1 and isinstance(opens, Mapping)
             and (values_at is None or callable(values_at)), "eh_management_requires_known_initial_fill")
    entered_at = initial.entered_at
    _clock(entered_at)
    lot = initial.book.lots[0]
    _require(lot.symbol == initial.symbol and lot.lot_id == initial.first_lot_id
             and lot.quantity == lot.original_quantity and not lot.half_reduced and initial.last_at == entered_at
             and initial.start_at < entered_at < initial.end_at < session.closing
             and session.opening <= initial.start_at and not (entered_at - session.opening) % MINUTE,
             "invalid_eh_management_initial_geometry")
    if reentry_at is not None:
        _require(callable(reentry_at) and isinstance(event, ScenarioEvent) and event.level.symbol == initial.symbol
                 and event.level.opening == session.opening and event.level.valid_until == session.closing
                 and event.origin.available_at == initial.start_at and event.expires_at == initial.end_at,
                 "eh_reentry_requires_matching_original_event")
    dynamic = _position_replay(initial, session, opens, values_at)
    reentry = None
    if reentry_at is not None:
        reentry = _fresh_replay(initial, dynamic, session, opens, event, values_at, reentry_at)
    fixed = _position_replay(initial, session, opens, None)
    return ManagementComparison(initial, fixed, dynamic, reentry)
