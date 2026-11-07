"""Invented EH matched and isolated reentry comparisons with exact ledger checks."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_scenario_management_compare import compare_management
from research.intraday_scenario_policy import EntryBounds, Values, apply_modeled_open, decide, start_episode
from research.intraday_scenario_state import OpeningRangePriceEvidence, scan_opening_range_high


OPEN = datetime(2031, 2, 3, 1, tzinfo=UTC)
SYMBOL = "invented-management"
BOUNDS = EntryBounds(Fraction(565), Fraction(603))


def at(offset):
    return OPEN + offset * MINUTE


def row(offset, close=600, *, opened=None, high=None, low=None):
    op = close if opened is None else opened
    high = max(op, close) if high is None else high
    low = min(op, close) if low is None else low
    return Minute(SYMBOL, at(offset), at(offset + 3), tuple(map(Decimal, (op, high, low, close))),
                  None, volume_state="missing")


def fixture(*, closing=61, opened=None, closes=None):
    opened, closes = opened or {}, closes or {}
    minutes = {at(index): row(index, closes.get(index, 600), opened=opened.get(index)) for index in range(closing)}
    minutes.update({at(index): row(index, 560, high=570, low=520) for index in range(15)})
    session = SessionInputs(SYMBOL, at(0), at(closing), minutes)
    evidence = OpeningRangePriceEvidence("invented", "invented-price-unit")
    event = scan_opening_range_high(session, current_evidence=evidence, through=at(22)).event
    assert event.breakout_stop == BOUNDS.stop
    episode = start_episode(cash.new_book("invented-EH-management"), SYMBOL, at(22), event.expires_at,
                            risk_budget=Fraction(880), cost_bps=33)
    entered = decide(episode, at(22), visible_minute=row(19), bounds=BOUNDS,
                     values=Values(at(22), wait=Fraction(), enter=Fraction(1)))
    initial, fill = apply_modeled_open(entered.episode, at(23), Fraction(600))
    assert fill.filled and fill.quantity == 11
    return initial, session, {at(index): Fraction(value) for index, value in opened.items()}, event


def fresh_fixture(*, closing=61, reentry_open=True, second_close=592):
    opened = {28: 590, 53: 600, closing - 1: 610}
    if reentry_open:
        opened[48] = 596
    closes = {index: close for start, close in ((30, 590), (35, second_close), (40, 596))
              for index in range(start, start + 5)}
    return fixture(closing=closing, opened=opened, closes=closes)


def early_exit(view):
    exit_now = view.at == at(27) and view.episode.attempts == 1
    return Values(view.at, hold=Fraction(not exit_now), exit=Fraction(exit_now))


def fresh_forecast(view):
    return Values(view.at, wait=Fraction(), enter=Fraction(1)), BOUNDS


def test_matched_arms_reuse_paid_entry_and_only_dynamic_observes_forecast_values():
    initial, session, opens, event = fixture(opened={28: 590, 33: 610, 53: 605})
    calls = []

    def values_at(view):
        calls.append((view.at, view.minute.start_at, view.episode.book.lots[0].quantity))
        assert view.episode.end_at == event.expires_at and view.fresh is None and view.planning_cap == at(53)
        if view.at == at(27):
            return Values(view.at, hold=Fraction(), reduce_half=Fraction(1), exit=Fraction())
        return Values(view.at, hold=Fraction(), exit=Fraction(1))

    result = compare_management(initial, session, opens, values_at=values_at)
    assert result.initial is initial and result.reentry is None
    assert calls == [(at(27), at(24), 11), (at(32), at(29), 6)]
    assert result.fixed.cap == result.dynamic.cap == at(53)
    assert result.fixed.episode.closed_at == at(53) and result.dynamic.episode.closed_at == at(33)
    assert result.fixed.episode.book.realized_gross == 55
    assert result.dynamic.episode.book.realized_gross == 10
    for arm in (result.fixed, result.dynamic):
        assert arm.status == "closed" and arm.episode.end_at == initial.end_at
        assert arm.episode.book.realized_net == arm.episode.book.realized_gross - Fraction(1089, 50)
    assert initial.book.lots[0].quantity == 11 and not initial.book.realized_exits


def test_structural_stop_at_plus_two_preempts_the_dynamic_forecast():
    initial, session, opens, _ = fixture(opened={28: 560}, closes={24: 565})

    def should_not_run(view):
        pytest.fail(f"forecast at structural stop {view.at}")

    result = compare_management(initial, session, opens, values_at=should_not_run)
    assert result.fixed == result.dynamic
    decision, fill = result.fixed.events
    assert (decision.at, decision.reason, decision.execute_at) == (at(27), "structural_close_failure", at(28))
    assert fill.at == at(28) and fill.open_price == 560


def test_callback_cannot_supply_a_future_timestamp():
    initial, session, opens, _ = fixture()
    with pytest.raises(InputRefusal, match="invalid_eh_decision_values"):
        compare_management(initial, session, opens,
                           values_at=lambda view: Values(view.at + MINUTE, hold=Fraction(), exit=Fraction(1)))


@pytest.mark.parametrize(("known_close", "expected_reason"), [
    (False, "holding_close_unknown:missing_minute"), (True, "exit_price_unknown"),
])
def test_missing_preclose_and_missing_requested_open_are_distinct(known_close, expected_reason):
    initial, session, opens, event = fixture(closes={24: 565})
    if not known_close:
        minutes = dict(session.minutes)
        minutes.pop(at(24))
        session = replace(session, minutes=minutes)
    result = compare_management(initial, session, opens, values_at=None, event=event, reentry_at=fresh_forecast)
    for arm in (result.fixed, result.dynamic):
        assert arm.status == "unresolved" and arm.reason == expected_reason
        assert arm.episode.book.cash == initial.book.cash and arm.episode.book.lots == initial.book.lots
        assert arm.episode.book.inventory_uncertain is known_close
        assert arm.events[-1].at == at(28 if known_close else 27)
    assert result.reentry.reason == "first_close_unresolved" and not result.reentry.events


@pytest.mark.parametrize("route", ["above_level", "below_level"])
def test_fresh_reentry_starts_from_the_same_closed_book_and_keeps_risk_and_expiry(route):
    initial, session, opens, event = fresh_fixture(second_close=585 if route == "below_level" else 592)
    if route == "below_level":
        session = replace(session, minutes={**session.minutes, at(24): row(24, low=568)})
    calls = []

    def request_reentry(view):
        assert view.episode.closed_at == at(28) and not view.episode.book.lots
        assert view.fresh.route == route and view.fresh.confirmation.start_at == at(40)
        assert all(item.start_at >= view.episode.closed_at for item in view.fresh.context)
        assert view.minute.start_at == at(44) and view.episode.end_at == view.planning_cap == event.expires_at
        calls.append(view)
        return fresh_forecast(view)

    result = compare_management(initial, session, opens, values_at=early_exit, event=event, reentry_at=request_reentry)
    reentry = result.reentry
    assert len(calls) == 1 and calls[0].at == at(47) and calls[0].episode == result.dynamic.episode
    assert result.dynamic.episode.attempts == 1 and result.dynamic.episode.book.realized_net == -Fraction(6589, 50)
    assert reentry.status == "closed" and reentry.holding.cap == event.expires_at == at(60)
    assert reentry.episode.end_at == event.expires_at and reentry.episode.closed_at == at(60)
    assert reentry.episode.attempts == 2 and reentry.episode.risk_budget == initial.risk_budget
    assert [(item.at, item.quantity, item.action) for item in reentry.events] == [(at(47), 18, "reenter"),
                                                                                (at(48), 18, "reenter")]
    assert reentry.episode.book.realized_exits[0] == result.dynamic.episode.book.realized_exits[0]
    second_net = 18 * (610 - 596) - Fraction(18 * 596 * 33, 10_000)
    assert reentry.episode.book.realized_net == result.dynamic.episode.book.realized_net + second_net


@pytest.mark.parametrize(("gap", "reason"), [(27, "exit_path_unknown:missing_minute"), (33, "fresh_evidence_unknown")])
def test_reentry_needs_supported_route_and_complete_fresh_evidence(gap, reason):
    initial, session, opens, event = fresh_fixture()
    minutes = dict(session.minutes)
    minutes.pop(at(gap))
    session = replace(session, minutes=minutes)

    def should_not_run(view):
        pytest.fail(f"reentry forecast from incomplete support {view.at}")

    result = compare_management(initial, session, opens, values_at=early_exit, event=event, reentry_at=should_not_run)
    assert result.dynamic.status == "closed" and result.dynamic.episode.closed_at == at(28)
    assert result.reentry.status == "unresolved" and result.reentry.reason == reason
    assert result.reentry.episode == result.dynamic.episode


def test_missing_reentry_open_freezes_the_last_known_flat_book():
    initial, session, opens, event = fresh_fixture(reentry_open=False)
    result = compare_management(initial, session, opens, values_at=early_exit, event=event, reentry_at=fresh_forecast)
    reentry = result.reentry
    assert reentry.status == "unresolved" and reentry.reason == "entry_price_unknown"
    assert reentry.episode.book.cash == result.dynamic.episode.book.cash and not reentry.episode.book.lots
    assert reentry.episode.book.inventory_uncertain and reentry.holding is None
    assert reentry.events[-1].at == at(48) and reentry.events[-1].open_price is None


def test_earlier_event_expiry_shortens_both_arms_and_blocks_late_fresh_request():
    initial, session, opens, event = fresh_fixture(closing=50)

    def should_not_run(view):
        pytest.fail(f"late reentry forecast {view.at}")

    result = compare_management(initial, session, opens, values_at=early_exit, event=event, reentry_at=should_not_run)
    assert result.fixed.cap == result.dynamic.cap == event.expires_at == at(49)
    assert result.fixed.episode.closed_at == at(49) and result.dynamic.episode.closed_at == at(28)
    assert result.reentry.status == "not_reentered" and result.reentry.reason == "fresh_time_expired"
    assert result.reentry.episode.end_at == event.expires_at


def test_second_confirmed_close_does_not_trigger_a_third_entry_with_time_left():
    initial, session, opens, event = fresh_fixture(closing=90)
    calls = []

    def exit_twice(view):
        exit_now = view.at in (at(27), at(52))
        return Values(view.at, hold=Fraction(not exit_now), exit=Fraction(exit_now))

    def reentry_at(view):
        calls.append(view.at)
        return fresh_forecast(view)

    result = compare_management(initial, session, opens, values_at=exit_twice, event=event, reentry_at=reentry_at)
    assert calls == [at(47)]
    assert result.reentry.episode.closed_at == at(53) and result.reentry.episode.attempts == 2
    assert result.reentry.episode.end_at == at(82)
