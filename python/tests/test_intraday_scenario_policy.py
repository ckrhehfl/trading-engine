"""Invented sequential decisions, independent cash checks and clock boundaries."""

from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute
from research.intraday_scenario_policy import EntryBounds, Values, apply_modeled_open, decide, start_episode


SYMBOL = "invented"
BEGIN = datetime(2025, 1, 2, 1, 0, tzinfo=UTC)


def clock(offset):
    return BEGIN + offset * MINUTE


def visible(offset, price, *, delay=0):
    ohlc = (Decimal(price),) * 4
    return Minute(SYMBOL, clock(offset - 3), clock(offset + delay), ohlc, None, volume_state="missing")


def new(*, end=60, risk=880):
    return start_episode(cash.new_book("invented-EH"), SYMBOL, clock(0), clock(end),
                         risk_budget=Fraction(risk), cost_bps=33)


def entry(episode, offset=0, *, reset=None):
    at = clock(offset)
    return decide(episode, at, visible_minute=visible(offset, 600),
                  bounds=EntryBounds(Fraction(565), Fraction(603)),
                  values=Values(at, wait=Fraction(1), enter=Fraction(2)), reset_at=reset)


def test_fixed_quantity_partial_exit_reentry_and_cost_conservation():
    request = entry(new())
    assert request.action == "enter" and request.episode.pending.quantity == 11
    held, purchase = apply_modeled_open(request.episode, clock(1), Fraction(600))
    assert purchase.quantity == 11 and purchase.assumed_cost == Fraction(1089, 50)
    reduced = decide(held, clock(5), visible_minute=visible(5, 590),
                     values=Values(clock(5), hold=Fraction(-2), reduce_half=Fraction(1), exit=Fraction()))
    assert reduced.action == "reduce_half" and reduced.episode.pending.quantity == 5
    held, _ = apply_modeled_open(reduced.episode, clock(6), Fraction(590))
    assert held.book.lots[0].quantity == 6
    same = decide(held, clock(10), visible_minute=visible(10, 590),
                  values=Values(clock(10), hold=Fraction(1), reduce_half=Fraction(2), exit=Fraction()))
    assert same.action == "hold"
    forced = decide(same.episode, clock(11), visible_minute=visible(11, 565),
                    values=Values(clock(11), hold=Fraction(500), exit=Fraction()))
    assert forced.reason == "structural_close_failure"
    flat, _ = apply_modeled_open(forced.episode, clock(12), Fraction(560))
    assert not flat.book.lots and flat.closed_at == clock(12)
    assert flat.book.realized_gross == -290
    assert flat.book.realized_net == -290 - Fraction(1089, 50)
    assert flat.book.cash == cash.INITIAL_CAPITAL + flat.book.realized_net
    no_reset = entry(flat, 15)
    assert no_reset.reason == "fresh_reset_required"
    again = entry(no_reset.episode, 25, reset=clock(25))
    assert again.action == "reenter"
    # Remaining risk 880 - 311.78. The frozen maximum costs 39.9899 per unit.
    assert again.episode.pending.quantity == 14
    held, _ = apply_modeled_open(again.episode, clock(26), Fraction(600))
    final = decide(held, clock(59))
    assert final.action == "exit" and final.reason == "fixed_episode_end"
    flat, _ = apply_modeled_open(final.episode, clock(60), Fraction(610))
    assert flat.attempts == 2 and not flat.book.lots
    assert flat.book.cash == cash.INITIAL_CAPITAL + flat.book.realized_net
    assert decide(flat, clock(61)).action == "end"


def test_unconfirmed_partial_fill_freezes_inventory_and_cash():
    initial = entry(new())
    held, _ = apply_modeled_open(initial.episode, clock(1), Fraction(600))
    action = decide(held, clock(5), visible_minute=visible(5, 599),
                    values=Values(clock(5), hold=Fraction(-1), exit=Fraction(), reduce_half=Fraction(1)))
    frozen, outcome = apply_modeled_open(action.episode, clock(6), None)
    assert outcome.outcome == "indeterminate"
    assert frozen.book.lots == held.book.lots and frozen.book.cash == held.book.cash
    assert decide(frozen, clock(10), visible_minute=visible(10, 700)).action == "unresolved"


def test_decision_does_not_depend_on_future_fill_and_never_retries_it():
    action = entry(new())
    refused, outcome = apply_modeled_open(action.episode, clock(1), Fraction(604))
    assert outcome.reason == "open_outside_frozen_entry_bounds"
    assert entry(refused, 2).action == "end"
    bought, outcome = apply_modeled_open(action.episode, clock(1), Fraction(590))
    assert outcome.quantity == action.episode.pending.quantity
    with pytest.raises(InputRefusal, match="prior_request"):
        decide(bought, clock(1))
    with pytest.raises(InputRefusal, match="invalid_eh_execution"):
        apply_modeled_open(bought, clock(1), Fraction(590))


def test_future_observation_and_stale_score_rejected_and_cap_does_not_need_prediction():
    with pytest.raises(InputRefusal, match="unavailable"):
        decide(new(), clock(0), visible_minute=visible(0, 599, delay=1))
    with pytest.raises(InputRefusal, match="decision_values"):
        decide(new(), clock(0), values=Values(clock(1), enter=Fraction(1)))
    assert decide(new(), clock(0)).action == "wait"
    action = entry(new(end=10))
    held, _ = apply_modeled_open(action.episode, clock(1), Fraction(600))
    exit_step = decide(held, clock(9))
    frozen, result = apply_modeled_open(exit_step.episode, clock(10), None)
    assert result.outcome == "indeterminate" and frozen.book.lots == held.book.lots


def test_delayed_preentry_close_does_not_count_as_a_postentry_stop_failure():
    action = entry(new())
    held, _ = apply_modeled_open(action.episode, clock(1), Fraction(600))
    answer = decide(held, clock(2), visible_minute=visible(2, 550))
    assert answer.action == "hold" and answer.reason == "management_unsupported"


def test_early_source_timestamp_cannot_bypass_the_registered_availability_floor():
    early = Minute(SYMBOL, clock(0), clock(1), (Decimal(600),) * 4,
                   None, volume_state="missing")
    with pytest.raises(InputRefusal, match="scheduled_availability"):
        decide(new(), clock(1), visible_minute=early,
               bounds=EntryBounds(Fraction(565), Fraction(603)),
               values=Values(clock(1), wait=Fraction(), enter=Fraction(2)))
    step = decide(new(), clock(3), visible_minute=early,
                  bounds=EntryBounds(Fraction(565), Fraction(603)),
                  values=Values(clock(3), wait=Fraction(), enter=Fraction(2)))
    assert step.action == "enter" and step.episode.pending.execute_at == clock(4)
