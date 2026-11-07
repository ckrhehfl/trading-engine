"""Freeze the one initial breakout request shared by both EH management arms."""

from datetime import datetime
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _clock, _require, window
from research.intraday_scenario_decision_inputs import WINDOW_MINUTES, ChartDecisionInputs
from research.intraday_scenario_decision_schedule import DecisionFrame
from research.intraday_scenario_policy import EntryBounds, Episode, Step, Values, apply_modeled_open, decide, start_episode


def prepare_matched_entry(
    frame: DecisionFrame, book: cash.CashBook, *, visible_minute: Minute, risk_budget: Fraction, cost_bps: int,
) -> Step:
    """Prepare one rule-based request from its original observed breakout."""
    _require(type(frame) is DecisionFrame and type(book) is cash.CashBook, "eh_matched_entry_requires_typed_inputs")
    at = frame.observed_at
    question, chart = frame.initial.question, frame.chart
    for clock in (at, frame.event_at, frame.event_expires_at):
        _clock(clock)
    _require(frame.initial.stage == "breakout" and question is not None and question.start_at == at
             and frame.event_at <= at < frame.event_expires_at, "eh_matched_entry_requires_first_breakout")
    _require(type(chart) is ChartDecisionInputs and chart.status == "ready"
             and chart.forecast_id == (frame.symbol, *question.forecast_id(at))
             and len(chart.minutes) == WINDOW_MINUTES, "eh_matched_entry_requires_ready_chart")

    minute_at = at - 3 * MINUTE
    _require(type(visible_minute) is Minute and visible_minute.symbol == frame.symbol
             and visible_minute.start_at == minute_at and visible_minute.available_at == at,
             "eh_matched_entry_requires_same_clock_minute")
    observed = SessionInputs(frame.symbol, minute_at, minute_at + MINUTE, {minute_at: visible_minute})
    _require(not window(observed, 0, 1, at).issues, "eh_matched_entry_price_unsupported")
    close = Fraction(visible_minute.ohlc[3])
    _require(question.band > 0 and close == frame.initial.start_close and close > question.level + question.band
             and chart.minutes[-1] == tuple((Fraction(price) - question.level) / question.band
                                           for price in visible_minute.ohlc), "eh_matched_entry_price_basis_differs")

    bounds = EntryBounds(question.stop, close + question.band)
    episode = start_episode(book, frame.symbol, frame.event_at, frame.event_expires_at,
                            risk_budget=risk_budget, cost_bps=cost_bps)
    return decide(episode, at, visible_minute=visible_minute, bounds=bounds,
                  values=Values(at, wait=Fraction(), enter=Fraction(1)))


def fill_matched_entry(prepared: Step, at: datetime, open_price: Fraction | None) -> tuple[Episode, cash.Decision]:
    """Apply only the prepared first request to its exact next-minute open."""
    _require(type(prepared) is Step and prepared.action == "enter", "eh_matched_entry_requires_pending_first_request")
    episode = prepared.episode
    request = episode.pending
    _require(request is not None and request.action == "enter" and episode.attempts == 1
             and not episode.book.lots and episode.entered_at is None and episode.closed_at is None
             and request.execute_at == episode.last_at + MINUTE, "eh_matched_entry_requires_pending_first_request")
    return apply_modeled_open(episode, at, open_price)
