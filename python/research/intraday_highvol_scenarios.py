"""Causal EG scenario attempts from a caller-authenticated first observed high."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_ed_inputs import MINUTE, SessionInputs, _require
from research.intraday_highvol_events import HighVolDecision, _metric
from research.intraday_pattern_events import Bar, PatternValidationError
from research.intraday_trader_session import FIVE, LAG, ProjectionIssue, _project_block, _validate_session


CONFIRMATION_WINDOW = 6 * FIVE
REWARD_TO_RISK = 2
Scenario = Literal["A", "B"]
AttemptState = Literal["confirmed", "no_setup", "unsupported", "outside_clock", "expired", "invalidated"]


@dataclass(frozen=True)
class ScenarioConfirmation:
    decision_at: datetime
    intended_entry_at: datetime
    price: Fraction
    stop: Fraction
    target: Fraction


@dataclass(frozen=True)
class ScenarioAttempt:
    symbol: str
    opening: datetime
    high_offset: int
    left_censored: bool
    scenario: Scenario | None
    state: AttemptState
    reason: str
    decision_at: datetime
    observed_bars: tuple[Bar, ...]
    key_level: Fraction | None
    pullback: Bar | None
    stop: Fraction | None
    confirmation: ScenarioConfirmation | None
    issue: ProjectionIssue | None


def detect_scenario(
    session: SessionInputs, first_high: HighVolDecision, *, left_censored: bool,
) -> ScenarioAttempt:
    """Inspect one registered cluster start and its first scenario attempt.

    The caller authenticates the high decision and selects one start per
    observed cluster. Only the onset flag known at that start enters here.
    The initial context and each later bar use their own completion-plus-two
    availability deadline. A missing or late required bar ends the attempt.
    A confirmation carries the intended entry clock for later evaluation,
    including when that clock falls outside the session.
    """
    _validate_session(session)
    _require(type(first_high) is HighVolDecision and first_high.status == "high"
             and type(left_censored) is bool, "registered_first_high_required")
    metric = first_high.current
    _metric(metric)
    _require(metric.symbol == session.symbol and metric.opening == session.opening
             and metric.price_status == "available"
             and type(first_high.threshold) is Fraction and metric.energy > first_high.threshold >= 0,
             "scenario_high_identity_or_support_changed")
    _require(metric.offset != 15 or left_censored, "first_grid_high_must_be_left_censored")

    high_end = session.opening + metric.offset * MINUTE
    decision_at = metric.decision_at
    observed: list[Bar] = []
    scenario: Scenario | None = None
    key = stop = None
    pullback: Bar | None = None
    issue: ProjectionIssue | None = None

    def finish(state: AttemptState, reason: str, confirmation: ScenarioConfirmation | None = None) -> ScenarioAttempt:
        return ScenarioAttempt(session.symbol, session.opening, metric.offset, left_censored,
                               scenario, state, reason, decision_at, tuple(observed),
                               key, pullback, stop, confirmation, issue)

    def read_bar(end: datetime) -> Bar | None:
        nonlocal issue
        projection = _project_block(session, end - FIVE)
        issue = projection.issue
        if projection.invalid:
            raise PatternValidationError(issue.reason)
        if projection.bar is not None:
            observed.append(projection.bar)
        return projection.bar

    if high_end > session.closing:
        return finish("outside_clock", "initial_context_outside_session")
    for end in (high_end - 2 * FIVE, high_end - FIVE, high_end):
        if read_bar(end) is None:
            return finish("unsupported", "initial_context_unavailable")
    first, second, impulse = observed
    signed_move = Fraction(impulse.close) / Fraction(first.open) - 1
    _require(signed_move == metric.signed_move, "scenario_high_signed_move_changed")
    prior_high = max(first.high, second.high)
    prior_low = min(first.low, second.low)
    if signed_move > 0 and impulse.close > prior_high:
        scenario, key = "A", Fraction(prior_high)
    elif signed_move < 0 and impulse.close < prior_low:
        scenario, key, stop = "B", Fraction(prior_low), Fraction(impulse.low)
    else:
        return finish("no_setup", "no_directional_expansion_or_breakdown")

    previous_close = impulse.close
    end = high_end + FIVE
    while end <= high_end + CONFIRMATION_WINDOW:
        if end > session.closing:
            return finish("outside_clock", "confirmation_window_truncated_by_close")
        decision_at = end + LAG
        bar = read_bar(end)
        if bar is None:
            return finish("unsupported", "confirmation_bar_unavailable")
        if scenario == "A" and pullback is None:
            if bar.close < previous_close:
                if bar.low <= key:
                    return finish("invalidated", "pullback_failed_to_hold_key")
                pullback, stop = bar, Fraction(bar.low)
            previous_close = bar.close
        else:
            if bar.low < stop:
                return finish("invalidated", "new_low_below_structural_stop")
            trigger = pullback.high if scenario == "A" else key
            if bar.close > trigger:
                price = Fraction(bar.close)
                confirmation = ScenarioConfirmation(
                    decision_at, decision_at + MINUTE, price, stop,
                    price + REWARD_TO_RISK * (price - stop),
                )
                return finish("confirmed", "resumption" if scenario == "A" else "reclaim", confirmation)
        end += FIVE
    return finish("expired", "confirmation_deadline_elapsed")
