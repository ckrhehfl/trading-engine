"""Draft EH action utilities from paired, time-rebased analogue paths.

The caller supplies exact branch weights and separately staged modeled opens.
The planning cap can shorten only the copied episode. Waiting can evaluate one
frozen close trigger five minutes later. Remaining inventory follows the
existing structural close and planning-cap exits. This kernel does no reentry.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _clock, _require, _session
from research.intraday_scenario_policy import Action, EntryBounds, Episode, Step, Values, apply_modeled_open, decide
from research.intraday_scenario_state import _observed


Candidate = Literal["wait", "enter", "hold", "reduce_half", "exit"]
OutcomeStatus = Literal["closed", "no_entry", "unresolved"]


@dataclass(frozen=True)
class AnaloguePath:
    key: str
    weight: Fraction
    session: SessionInputs
    modeled_opens: Mapping[datetime, Fraction | None]


@dataclass(frozen=True)
class ReplayEvent:
    at: datetime
    action: Action
    reason: str
    execute_at: datetime | None = None
    quantity: int | None = None
    fill_status: Literal["modeled", "declined", "indeterminate"] | None = None
    open_price: Fraction | None = None


@dataclass(frozen=True)
class PathOutcome:
    path_key: str
    weight: Fraction
    action: Candidate
    status: OutcomeStatus
    reason: str
    events: tuple[ReplayEvent, ...]
    terminal_book: cash.CashBook
    net_change: Fraction | None


@dataclass(frozen=True)
class RolloutComparison:
    values: Values | None
    outcomes: tuple[PathOutcome, ...]
    unavailable_actions: tuple[tuple[Candidate, str], ...]


def _prefer(at: datetime, action: Candidate) -> Values:
    one, zero = Fraction(1), Fraction()
    if action in ("wait", "enter"):
        return Values(at, wait=one if action == "wait" else zero, enter=one if action == "enter" else zero)
    return Values(at, hold=one if action == "hold" else zero,
                  reduce_half=one if action == "reduce_half" else None, exit=one if action == "exit" else zero)


def _decision_event(step: Step) -> ReplayEvent:
    request = step.episode.pending
    return ReplayEvent(step.episode.last_at, step.action, step.reason,
                       execute_at=request.execute_at if request is not None else None,
                       quantity=request.quantity if request is not None else None)


def _visible(session: SessionInputs, start: datetime, at: datetime) -> tuple[Minute | None, str | None]:
    if start < session.opening:
        return None, "outside_analogue_session"
    rows, issues = _observed(session, (start - session.opening) // MINUTE, 1, at)
    return (None, issues[0].reason) if issues else (rows[0], None)


def _held_step(
    episode: Episode, session: SessionInputs, at: datetime, last_decision_at: datetime,
) -> tuple[Step | None, str | None]:
    # A fill consumes its minute in the policy. At the next decision check all
    # newly due post-entry closes, including the one due on that fill minute.
    fallback = decide(episode, at)
    _require(episode.entered_at is not None, "missing_eh_rollout_entry_clock")
    first = max(episode.entered_at, last_decision_at - 2 * MINUTE)
    latest = at - 3 * MINUTE
    unknown = None
    while first <= latest:
        row, issue = _visible(session, first, at)
        first += MINUTE
        if issue is not None:
            unknown = unknown or issue
            continue
        step = decide(episode, at, visible_minute=row)
        if step.reason == "structural_close_failure":
            return step, None
    if fallback.action == "exit" or unknown is None:
        return fallback, None
    return None, f"holding_close_unknown:{unknown}"


def _replay(path: AnaloguePath, action: Candidate, initial: Step, starting_nav: Fraction | None,
            bounds: EntryBounds | None, wait_trigger: Fraction | None) -> PathOutcome:
    episode = initial.episode
    last_decision_at = episode.last_at
    at = last_decision_at
    wait_at = at + 5 * MINUTE if action == "wait" and wait_trigger is not None else None
    events = [_decision_event(initial)]

    def outcome(status: OutcomeStatus, reason: str) -> PathOutcome:
        payoff = None if status == "unresolved" or starting_nav is None else episode.book.cash - starting_nav
        if status == "closed" and starting_nav is None:
            reason = "closed_with_initial_mark_unknown"
        return PathOutcome(path.key, path.weight, action, status, reason, tuple(events), episode.book, payoff)

    if action == "hold" and starting_nav is None:
        return outcome("unresolved", "initial_held_mark_unknown")
    while at < episode.end_at:
        at += MINUTE
        request = episode.pending
        if request is not None:
            _require(request.execute_at == at, "eh_rollout_request_not_next_minute")
            price = path.modeled_opens.get(at)
            episode, fill = apply_modeled_open(episode, at, price)
            events.append(ReplayEvent(at, request.action, fill.reason, quantity=fill.quantity,
                                      fill_status=fill.outcome, open_price=price))
            if fill.outcome == "indeterminate":
                return outcome("unresolved", fill.reason)
            if not episode.book.lots:
                return outcome("closed" if request.action == "exit" else "no_entry", fill.reason)
            if not fill.filled:
                return outcome("unresolved", fill.reason)
            continue
        if episode.book.lots:
            step, issue = _held_step(episode, path.session, at, last_decision_at)
            if step is None:
                return outcome("unresolved", issue)
        elif wait_at is None or wait_at >= episode.end_at:
            return outcome("no_entry", "wait_without_entry")
        elif at < wait_at:
            continue
        else:
            step = decide(episode, at)
            if step.action != "end":
                row, issue = _visible(path.session, at - 3 * MINUTE, at)
                if issue is not None:
                    return outcome("unresolved", f"wait_confirmation_unknown:{issue}")
                if Fraction(row.ohlc[3]) <= wait_trigger:
                    events.append(ReplayEvent(at, "wait", "wait_confirmation_not_met"))
                    return outcome("no_entry", "wait_confirmation_not_met")
                step = decide(episode, at, visible_minute=row, values=_prefer(at, "enter"), bounds=bounds)
            wait_at = None
        episode = step.episode
        last_decision_at = at
        events.append(_decision_event(step))
        if step.action == "unresolved":
            return outcome("unresolved", step.reason)
        if not episode.book.lots and episode.pending is None:
            return outcome("no_entry", step.reason)
    return outcome("unresolved", "planning_cap_inventory_unresolved")


def compare_actions(
    episode: Episode, at: datetime, *, visible_minute: Minute | None, paths: tuple[AnaloguePath, ...],
    planning_minutes: int, downside_penalty: Fraction, bounds: EntryBounds | None = None,
    wait_trigger: Fraction | None = None,
) -> RolloutComparison:
    """Compare available actions on identical paths without dropping unresolved mass.

    Utility is expected cash change relative to initial marked NAV minus
    ``downside_penalty`` times expected negative change. The ledger handles
    entry cost, partial sales and inventory uncertainty for every branch.
    """
    _clock(at)
    _require(type(episode) is Episode and not episode.book.indeterminate and not episode.book.inventory_uncertain
             and episode.pending is None and (bounds is None or type(bounds) is EntryBounds)
             and len(episode.book.lots) <= 1 and (episode.book.lots or episode.attempts == 0)
             and all(lot.symbol == episode.symbol for lot in episode.book.lots), "invalid_eh_rollout_start")
    _require(type(planning_minutes) is int and 1 <= planning_minutes <= 30 and at < episode.end_at
             and type(downside_penalty) is Fraction and downside_penalty >= 0,
             "invalid_eh_rollout_horizon_or_penalty")
    _require(visible_minute is None or (isinstance(visible_minute, Minute) and visible_minute.start_at == at - 3 * MINUTE),
             "eh_rollout_requires_same_clock_visible_minute")
    _require(wait_trigger is None or (type(wait_trigger) is Fraction and wait_trigger > 0
             and type(bounds) is EntryBounds and not episode.book.lots), "invalid_eh_rollout_wait_trigger")
    _require(type(paths) is tuple and paths and all(type(path) is AnaloguePath and type(path.key) is str and path.key
             and type(path.weight) is Fraction and path.weight > 0 for path in paths)
             and len({path.key for path in paths}) == len(paths)
             and sum((path.weight for path in paths), Fraction()) == 1, "invalid_eh_rollout_branch_weights")

    cap = min(episode.end_at, at + planning_minutes * MINUTE)
    for path in paths:
        _session(path.session)
        _require(path.session.symbol == episode.symbol and path.session.opening <= at < cap < path.session.closing
                 and not (at - path.session.opening) % MINUTE and isinstance(path.modeled_opens, Mapping),
                 "invalid_eh_rollout_path_geometry")

    hypothetical = replace(episode, end_at=cap)
    actions: tuple[Candidate, ...] = ("hold", "reduce_half", "exit") if episode.book.lots else ("wait", "enter")
    initial = {action: decide(hypothetical, at, visible_minute=visible_minute, bounds=bounds,
                             values=_prefer(at, action)) for action in actions}
    close = None if visible_minute is None or visible_minute.ohlc is None else Fraction(visible_minute.ohlc[3])
    starting_nav = cash.mark(episode.book, {episode.symbol: close}).full_nav
    outcomes, unavailable, utilities = [], [], {}
    for action, step in initial.items():
        if step.action != action:
            unavailable.append((action, step.reason))
            continue
        branch = tuple(_replay(path, action, step, starting_nav, bounds, wait_trigger) for path in paths)
        outcomes.extend(branch)
        if all(row.net_change is not None for row in branch):
            utilities[action] = sum((row.weight * (row.net_change - downside_penalty * max(Fraction(), -row.net_change))
                                     for row in branch), Fraction())
    values = Values(at, **utilities) if utilities and len(utilities) + len(unavailable) == len(actions) else None
    return RolloutComparison(values, tuple(outcomes), tuple(unavailable))
