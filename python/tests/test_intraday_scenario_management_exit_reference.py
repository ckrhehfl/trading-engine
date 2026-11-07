"""Invented management traces checked against independent minute observations."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_management_compare import compare_management
from research.intraday_scenario_management_day import _RequestedOpens
from research.intraday_scenario_management_execution import _orders
from research.intraday_scenario_management_exit_reference import verify_management_exits
from research.intraday_scenario_policy import EntryBounds, Values
from test_intraday_scenario_management_compare import at, early_exit, fixture, fresh_fixture, row
from test_intraday_scenario_management_day import DAY


def _produce(initial, session, event, *, values=None, fresh=False):
    def fresh_rule(view):
        confirmation = view.fresh.confirmation
        stop = min(Fraction(confirmation.low), event.level.price - event.band)
        limit = Fraction(view.minute.ohlc[3]) + event.band
        return Values(view.at, wait=Fraction(), enter=Fraction(1)), EntryBounds(stop, limit)

    comparison = compare_management(initial, session, _RequestedOpens(session), values_at=values, event=event,
                                    reentry_at=fresh_rule if fresh else None)
    fixed, dynamic = comparison.fixed, comparison.dynamic
    arms = {"fixed": [fixed.status, _orders(fixed.events)], "dynamic": [dynamic.status, _orders(dynamic.events)]}
    if fresh:
        reentry = comparison.reentry
        holding = () if reentry.holding is None else reentry.holding.events
        arms["fresh_increment"] = [reentry.status, _orders(reentry.events + holding)]
    lot = initial.book.lots[0]
    return {
        "day": DAY, "symbol": session.symbol, "status": "confirmed", "event_expiry": event.expires_at.isoformat(),
        "initial": {"stop": str(initial.stop), "trade": [
            "enter", at(22).isoformat(), initial.entered_at.isoformat(), lot.quantity,
            "modeled", lot.quantity, str(lot.entry_price), "higher_value_of_entry",
        ]},
        "arms": arms,
    }


def _verify(trace, session, event):
    verify_management_exits(trace, session, level=event.level.price, band=event.band,
                            risk_budget=Fraction(880), cost_bps=33,
                            fresh_reentry="fresh_increment" in trace["arms"])


@pytest.mark.parametrize(("close", "omit", "terminal", "reason"), [
    (None, None, "closed", "fixed_episode_end"),
    (24, None, "closed", "structural_close_failure"),
    (None, 24, "unresolved", None),
    (24, 28, "unresolved", "structural_close_failure"),
    (None, 49, "closed", "fixed_episode_end"),
    (49, None, "closed", "structural_close_failure"),
])
def test_structural_stop_and_cap_keep_preclose_and_open_gaps_distinct(close, omit, terminal, reason):
    initial, session, _, event = fixture(closes={} if close is None else {close: 565})
    if omit is not None:
        session = replace(session, minutes={key: value for key, value in session.minutes.items() if key != at(omit)})
    trace = _produce(initial, session, event)
    status, orders = trace["arms"]["fixed"]
    assert status == terminal
    assert (orders[0][7] if orders else None) == reason
    _verify(trace, session, event)


def test_late_fixed_exit_cannot_hide_an_earlier_visible_structural_failure():
    initial, session, _, event = fixture(closes={24: 565})
    trace = _produce(initial, session, event)
    trade = trace["arms"]["fixed"][1][0]
    trade[1:3] = [at(52).isoformat(), at(53).isoformat()]
    trade[7] = "fixed_episode_end"
    with pytest.raises(InputRefusal, match="request_differs"):
        _verify(trace, session, event)


def test_after_a_reduction_the_catch_up_checks_all_newly_due_closes():
    initial, session, _, event = fixture(closes={26: 565})
    session = replace(session, minutes={key: value for key, value in session.minutes.items() if key != at(25)})

    def reduce(view):
        return Values(view.at, hold=Fraction(), reduce_half=Fraction(1), exit=Fraction())

    trace = _produce(initial, session, event, values=reduce)
    assert trace["arms"]["fixed"] == ["unresolved", []]
    sales = trace["arms"]["dynamic"][1]
    assert [order[2] for order in sales] == [at(28).isoformat(), at(30).isoformat()]
    assert sales[1][7] == "structural_close_failure"
    _verify(trace, session, event)
    sales[1][7] = "higher_value_of_exit"
    with pytest.raises(InputRefusal, match="request_differs"):
        _verify(trace, session, event)


@pytest.mark.parametrize("route", ("above", "below"))
def test_fresh_reentry_requires_the_first_confirmed_post_exit_path_and_frozen_quantity(route):
    initial, session, _, event = fresh_fixture(second_close=585 if route == "below" else 592)
    if route == "below":
        session = replace(session, minutes=session.minutes | {at(24): row(24, low=568)})
    trace = _produce(initial, session, event, values=early_exit, fresh=True)
    terminal, trades = trace["arms"]["fresh_increment"]
    assert terminal == "closed" and trades[0][1:4] == [at(47).isoformat(), at(48).isoformat(), 19]
    _verify(trace, session, event)

    trades[0][3] += 1
    with pytest.raises(InputRefusal, match="request_differs"):
        _verify(trace, session, event)
    trades[0][3] -= 1
    trace["arms"]["fresh_increment"] = ["not_reentered", []]
    with pytest.raises(InputRefusal, match="fresh_request_missing"):
        _verify(trace, session, event)


def test_realized_gains_cannot_restore_event_risk_but_do_restore_cash():
    closes = {i: close for offset, close in ((35, 590), (40, 592), (45, 596))
              for i in range(offset, offset + 5)}
    initial, session, _, event = fixture(closing=90, opened={28: 580, 33: 620, 53: 596}, closes=closes)

    def reduce_then_close(view):
        if view.episode.attempts != 1:
            return None
        if view.at == at(27):
            return Values(view.at, hold=Fraction(), reduce_half=Fraction(1), exit=Fraction())
        return Values(view.at, hold=Fraction(), exit=Fraction(1))

    trace = _produce(initial, session, event, values=reduce_then_close, fresh=True)
    status, trades = trace["arms"]["fresh_increment"]
    assert status == "closed" and trades[0][1:4] == [at(52).isoformat(), at(53).isoformat(), 20]
    _verify(trace, session, event)
    trades[0][3] = 23
    with pytest.raises(InputRefusal, match="request_differs"):
        _verify(trace, session, event)


@pytest.mark.parametrize(("omit", "terminal", "count"), [
    (28, "unresolved", 0),
    (27, "unresolved", 0),
    (33, "unresolved", 0),
    (48, "unresolved", 1),
])
def test_unknown_first_exit_reset_path_or_reentry_open_cannot_be_relabelled(omit, terminal, count):
    initial, session, _, event = fresh_fixture()
    session = replace(session, minutes={key: value for key, value in session.minutes.items() if key != at(omit)})
    trace = _produce(initial, session, event, values=early_exit, fresh=True)
    fresh = trace["arms"]["fresh_increment"]
    assert (fresh[0], len(fresh[1])) == (terminal, count)
    _verify(trace, session, event)
    fresh[0] = "not_reentered"
    with pytest.raises(InputRefusal, match="fresh_"):
        _verify(trace, session, event)


def test_fresh_decline_and_expiry_cannot_become_an_extra_holding():
    initial, session, _, event = fresh_fixture()
    session = replace(session, minutes=session.minutes | {at(48): row(48, opened=605)})
    trace = _produce(initial, session, event, values=early_exit, fresh=True)
    fresh = trace["arms"]["fresh_increment"]
    assert fresh[0] == "not_reentered" and fresh[1][0][4] == "declined"
    _verify(trace, session, event)

    initial, session, _, event = fresh_fixture(closing=50)
    trace = _produce(initial, session, event, values=early_exit, fresh=True)
    assert trace["arms"]["fresh_increment"] == ["not_reentered", []]
    _verify(trace, session, event)
