"""Invented checks for EH's shared initial management request and later fill."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, InputRefusal, SessionInputs
from research.intraday_scenario_decision_schedule import opening_decision_frames
from research.intraday_scenario_management_compare import compare_management
from research.intraday_scenario_management_entry import fill_matched_entry, prepare_matched_entry
from research.intraday_scenario_state import OpeningRangePriceEvidence, scan_opening_range_high
from test_intraday_scenario_management_compare import SYMBOL, at, row


def breakout_frame():
    minutes = {at(index): row(index) for index in range(65)}
    minutes.update({at(index): row(index, 560, high=570, low=520) for index in range(15)})
    minutes.update({at(index): row(index, 570) for index in range(15, 20)})
    session = SessionInputs(SYMBOL, at(0), at(65), minutes)
    evidence = OpeningRangePriceEvidence("invented", "invented-price-unit")
    frames = opening_decision_frames(session, evidence)
    approach = next(frames)
    breakout = next(frame for frame in frames if frame.initial.stage == "breakout")
    event = scan_opening_range_high(session, current_evidence=evidence, through=breakout.observed_at).event
    return approach, breakout, session, event


def prepare(frame, minute):
    return prepare_matched_entry(frame, cash.new_book("invented-EH-entry"), visible_minute=minute,
                                 risk_budget=Fraction(880), cost_bps=33)


def test_both_management_arms_receive_the_same_first_fill_and_fixed_quantity():
    _, frame, session, event = breakout_frame()
    planned = prepare(frame, session.minutes[at(24)])
    request = planned.episode.pending
    assert (planned.episode.start_at, planned.episode.last_at, planned.episode.end_at) == (at(22), at(27), at(64))
    assert request.execute_at == at(28)
    assert request.bounds.stop == frame.initial.question.stop == 565
    assert request.bounds.max_price == 605
    assert request.quantity == 10
    cheaper, improved = fill_matched_entry(planned, at(28), Fraction(590))
    at_limit, upper = fill_matched_entry(planned, at(28), Fraction(605))
    assert improved.filled and upper.filled
    assert cheaper.book.lots[0].quantity == at_limit.book.lots[0].quantity == request.quantity

    comparison = compare_management(cheaper, session, {at(58): Fraction(600)}, values_at=None, event=event)
    assert comparison.initial is cheaper and comparison.fixed == comparison.dynamic
    assert comparison.fixed.episode.book.realized_gross == 100
    assert comparison.fixed.episode.book.realized_net == 100 - improved.assumed_cost


@pytest.mark.parametrize(("price", "outcome", "reason"), [
    (Fraction(606), "declined", "open_outside_frozen_entry_bounds"),
    (Fraction(565), "declined", "open_outside_frozen_entry_bounds"),
    (None, "indeterminate", "entry_price_unknown"),
])
def test_modeled_decline_and_missing_open_keep_distinct_states(price, outcome, reason):
    _, frame, session, _ = breakout_frame()
    prepared = prepare(frame, session.minutes[at(24)])
    episode, fill = fill_matched_entry(prepared, at(28), price)
    assert (fill.outcome, fill.reason) == (outcome, reason)
    assert episode.book.cash == prepared.episode.book.cash
    assert not episode.book.lots and episode.entered_at is None and episode.pending is None
    assert episode.book.inventory_uncertain == (price is None)


@pytest.mark.parametrize("offset", [27, 29])
def test_open_before_or_after_requested_minute_is_rejected(offset):
    _, frame, session, _ = breakout_frame()
    prepared = prepare(frame, session.minutes[at(24)])
    with pytest.raises(InputRefusal, match="invalid_eh_execution"):
        fill_matched_entry(prepared, at(offset), Fraction(600))


def test_approach_and_later_breakout_update_cannot_prepare_entry():
    approach, frame, session, _ = breakout_frame()
    with pytest.raises(InputRefusal, match="requires_first_breakout"):
        prepare(approach, session.minutes[at(19)])
    with pytest.raises(InputRefusal, match="requires_first_breakout"):
        prepare(replace(frame, observed_at=at(32)), session.minutes[at(29)])


def test_late_event_budget_declines_preparation_without_reading_an_open():
    _, frame, session, _ = breakout_frame()
    short = replace(frame, event_expires_at=at(32))
    prepared = prepare(short, session.minutes[at(24)])
    assert (prepared.action, prepared.reason, prepared.episode.pending) == ("end", "too_little_time_for_entry", None)
    with pytest.raises(InputRefusal, match="requires_pending_first_request"):
        fill_matched_entry(prepared, at(28), None)


def test_unavailable_wrong_symbol_or_changed_close_is_rejected():
    _, frame, session, _ = breakout_frame()
    minute = session.minutes[at(24)]
    for changed in (replace(minute, available_at=at(28)), replace(minute, symbol="another"), session.minutes[at(23)]):
        with pytest.raises(InputRefusal, match="requires_same_clock_minute"):
            prepare(frame, changed)
    with pytest.raises(InputRefusal, match="price_basis_differs"):
        prepare(frame, replace(minute, ohlc=(Decimal(599),) * 4))
    with pytest.raises(InputRefusal, match="requires_ready_chart"):
        prepare(replace(frame, chart=replace(frame.chart, minutes=frame.chart.minutes[:-1])), minute)
