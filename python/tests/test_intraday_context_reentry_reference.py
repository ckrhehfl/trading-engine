"""Original-minute verification of the isolated EI fresh rule increment."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_context_reentry as producer
from research.intraday_context_reentry_reference import verify_reentry_evidence, verify_reentry_increment
from research.intraday_ed_inputs import MINUTE, InputRefusal
from test_intraday_context_held import PrefixOnly
from test_intraday_context_reentry import RequestedOnly, reset_fixture


F = Fraction


def _verify(increment, prepared, first, session):
    verify_reentry_increment(increment, original_entry=prepared.features, audited_parent=first,
                             original_session=session)


def _minute(session, offset, price):
    at = session.opening + offset * MINUTE
    rows = dict(session.minutes)
    if price is None:
        del rows[at]
    else:
        rows[at] = replace(rows[at], ohlc=(Decimal(price),) * 4)
    return replace(session, minutes=rows)


def test_first_confirmation_checks_no_suffix_or_producer_signal(monkeypatch):
    prepared, first, session = reset_fixture()
    through = session.opening + 56 * MINUTE
    evidence = producer.context_reentry_evidence(prepared.features, first.episode, session, through=through)
    assert evidence.status == "ready"
    prefix = replace(session, minutes=PrefixOnly(session.minutes, session.opening + 50 * MINUTE))

    def forbidden(*args, **kwargs):
        pytest.fail("production reentry scanner called during independent verification")

    for name in ("context_reentry_evidence", "scan_context_frames", "scan_context_questions", "_closed"):
        monkeypatch.setattr(producer, name, forbidden)
    verify_reentry_evidence(evidence, original_entry=prepared.features, confirmed_parent=first.episode,
                            original_session=prefix, through=through)
    changed = replace(evidence, reset_start=session.opening + 35 * MINUTE)
    with pytest.raises(InputRefusal, match="signal_or_boundary"):
        verify_reentry_evidence(changed, original_entry=prepared.features, confirmed_parent=first.episode,
                                original_session=prefix, through=through)


@pytest.mark.parametrize("settings,expected", [
    ({"fresh": False}, "no_fresh_breakout_before_original_expiry"),
    ({"close_at": 43}, "no_fresh_breakout_before_original_expiry"),
    ({"close_at": 58}, "insufficient_time_for_fresh_reset"),
    ({"missing": 42}, "fresh_context_prefix_unknown"),
    ({"exit_price": F(100)}, "insufficient_risk_or_cash_for_one_unit"),
])
def test_no_attempt_still_checks_every_distinct_signal_outcome(settings, expected):
    prepared, first, session = reset_fixture(**settings)
    result = producer.replay_context_reentry(prepared, first, session, RequestedOnly({}))
    assert result.reason == expected
    _verify(result, prepared, first, session)
    with pytest.raises(InputRefusal, match="increment_result"):
        _verify(replace(result, net_increment=F(9)), prepared, first, session)


def test_pending_and_anchor_invalidation_are_recomputed_at_original_clocks():
    prepared, first, session = reset_fixture()
    at = session.opening + 47 * MINUTE
    pending = producer.context_reentry_evidence(prepared.features, first.episode, session, through=at)
    assert pending.status == "pending"
    verify_reentry_evidence(pending, original_entry=prepared.features, confirmed_parent=first.episode,
                            original_session=session, through=at)
    invalid = _minute(session, 44, 99)
    result = producer.replay_context_reentry(prepared, first, invalid, RequestedOnly({}))
    assert result.reason == "original_anchor_invalidated"
    _verify(result, prepared, first, invalid)


@pytest.mark.parametrize("price,status,reason", [
    (118, "closed", "exited"),
    (120, "not_reentered", "open_outside_frozen_entry_bounds"),
    (124, "not_reentered", "entry_at_or_above_target"),
    (None, "unresolved", "entry_price_unknown"),
])
def test_increment_checks_frozen_request_open_and_cash_without_replaying_producer(price, status, reason, monkeypatch):
    prepared, first, session = reset_fixture()
    session = _minute(session, 53, price)
    opened = {session.opening + 53 * MINUTE: None if price is None else F(price),
              session.opening + 62 * MINUTE: F(118)}
    increment = producer.replay_context_reentry(prepared, first, session, RequestedOnly(opened))
    assert (increment.status, increment.reason) == (status, reason)

    def forbidden(*args, **kwargs):
        pytest.fail("production policy called during independent verification")

    for name in ("decide", "apply_modeled_open", "_position_replay", "context_reentry_evidence"):
        monkeypatch.setattr(producer, name, forbidden)
    _verify(increment, prepared, first, session)
    step = increment.request
    bad_request = replace(step.episode.pending, quantity=step.episode.pending.quantity + 1)
    changed = replace(increment, request=replace(step, episode=replace(step.episode, pending=bad_request)))
    with pytest.raises(InputRefusal, match="frozen_request"):
        _verify(changed, prepared, first, session)


def test_original_next_open_and_expiry_cannot_be_replaced_in_a_filled_increment():
    prepared, first, session = reset_fixture()
    opens = RequestedOnly({session.opening + n * MINUTE: F(118) for n in (53, 62)})
    result = producer.replay_context_reentry(prepared, first, session, opens)
    with pytest.raises(InputRefusal, match="original_modeled_fill"):
        _verify(result, prepared, first, _minute(session, 53, 119))
    later = replace(result, holding=replace(result.holding, cap=first.episode.end_at + MINUTE))
    with pytest.raises(InputRefusal, match="fixed_holding_boundary"):
        _verify(later, prepared, first, session)


@pytest.mark.parametrize("missing,reason", [(55, "holding_close_unknown:missing_minute"), (62, "exit_price_unknown")])
def test_unresolved_second_holding_retains_inventory_and_actual_clock(missing, reason):
    prepared, first, session = reset_fixture()
    session = _minute(session, missing, None)
    opens = RequestedOnly({session.opening + 53 * MINUTE: F(118), session.opening + 62 * MINUTE: None})
    result = producer.replay_context_reentry(prepared, first, session, opens)
    assert (result.status, result.reason, result.net_increment) == ("unresolved", reason, None)
    _verify(result, prepared, first, session)
    changed = replace(result.episode, last_at=result.episode.last_at + MINUTE)
    with pytest.raises(InputRefusal, match="terminal_state"):
        _verify(replace(result, episode=changed, holding=replace(result.holding, episode=changed)),
                prepared, first, session)
    if missing == 55:
        session = _minute(session, 56, None)
        event = replace(result.holding.events[0], at=result.holding.events[0].at + MINUTE)
        holding = replace(result.holding, episode=changed, events=(event,))
        with pytest.raises(InputRefusal, match="skips_earlier_missing"):
            _verify(replace(result, episode=changed, holding=holding, events=result.events[:2] + (event,)),
                    prepared, first, session)
