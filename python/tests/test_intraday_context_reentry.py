"""Invented post-exit EI reset evidence and one separately attributed rule attempt."""

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research import intraday_context_reentry as reentry
from research.intraday_context_entry import context_entry_feature_sha256, fill_context_entry, prepare_context_entry
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_scenario_management_compare import _position_replay
from research.intraday_scenario_policy import Values
from test_intraday_context_held import POLICY, PrefixOnly, fixture


F = Fraction


class RequestedOnly(Mapping):
    def __init__(self, values):
        self.values, self.calls = values, []

    def __getitem__(self, key):
        assert key in self.values, f"unexpected future-open request: {key}"
        self.calls.append(key)
        return self.values[key]

    def __iter__(self):
        raise AssertionError("future-open mapping iteration")

    def __len__(self):
        raise AssertionError("future-open mapping length")


def prepared_for(query, session):
    return prepare_context_entry(query.entry, cash.new_book("invented-held"), mode="baseline",
        visible_minute=session.minutes[session.opening + 29 * MINUTE], risk_budget=F(100), cost_bps=33,
        causal_source_sha256=context_entry_feature_sha256(query.entry), execution_policy_sha256=POLICY, rule_enabled=True)


def reset_fixture(*, close_at=38, exit_price=F(114), fresh=True, missing=None):
    query, session, _, _ = fixture()
    rows = dict(session.minutes)
    for index in range(40, 65):
        if index < 45:
            prices = (113, 114 if fresh else 116, Decimal("112.5"), 113)
        elif index < 50:
            prices = (113, 119, 113, 118)
        else:
            prices = (118, 119, 117, 118)
        at = session.opening + index * MINUTE
        rows[at] = replace(rows[at], ohlc=tuple(map(Decimal, prices)))
    at = session.opening + close_at * MINUTE
    rows[at] = replace(rows[at], ohlc=(Decimal(exit_price.numerator) / Decimal(exit_price.denominator),) * 4)
    if missing is not None:
        del rows[session.opening + missing * MINUTE]
    session = replace(session, minutes=rows)
    prepared = prepared_for(query, session)
    initial, _ = fill_context_entry(prepared, session.opening + 33 * MINUTE, F(114))
    decision_at = session.opening + (close_at - 1) * MINUTE
    first = _position_replay(initial, session, {decision_at + MINUTE: exit_price},
        lambda view: Values(view.at, hold=F(0), exit=F(1)) if view.at == decision_at else None)
    return prepared, first, session


def test_first_fresh_episode_requires_complete_post_exit_reset_and_keeps_absolute_expiry():
    prepared, first, session = reset_fixture()
    origin = session.opening
    evidence = reentry.context_reentry_evidence(prepared.features, first.episode, session, through=origin + 56 * MINUTE)
    assert reentry.SCHEMA == "ei-context-rule-reentry-draft-v1"
    assert evidence.status == "ready" and evidence.confirmation_at == origin + 52 * MINUTE
    assert (evidence.reset_start, evidence.reset_end, evidence.reset_available_at) == tuple(origin + n * MINUTE for n in (40, 45, 47))
    assert evidence.reset_start >= first.episode.closed_at
    assert evidence.fresh_question.episode_id != prepared.features.question.episode_id
    assert evidence.fresh_question.anchor_id == prepared.features.question.anchor_id
    assert evidence.fresh_question.path.end_at > evidence.absolute_expiry == origin + 62 * MINUTE
    earlier = reentry.context_reentry_evidence(prepared.features, first.episode, session, through=origin + 52 * MINUTE)
    assert earlier == evidence


@pytest.mark.parametrize("close_at,fresh", [(43, True), (38, False), (53, True)])
def test_straddling_exit_old_episode_or_insufficient_time_cannot_reenter(close_at, fresh):
    prepared, first, session = reset_fixture(close_at=close_at, fresh=fresh)
    opens = RequestedOnly({})
    result = reentry.replay_context_reentry(prepared, first, session, opens)
    assert result.status == "not_reentered" and result.net_increment == 0
    assert result.fill is None and opens.calls == [] and result.episode.attempts == 1


def test_prefix_only_reset_does_not_read_beyond_first_confirmation():
    prepared, first, session = reset_fixture()
    prefix = replace(session, minutes=PrefixOnly(session.minutes, session.opening + 50 * MINUTE))
    result = reentry.context_reentry_evidence(prepared.features, first.episode, prefix,
                                             through=session.opening + 56 * MINUTE)
    assert result.status == "ready"


def test_rule_request_precedes_open_access_and_uses_remaining_loss_budget(monkeypatch):
    prepared, first, session = reset_fixture()
    opening = session.opening
    opens = RequestedOnly({opening + 53 * MINUTE: F(118), opening + 62 * MINUTE: F(118)})
    original = reentry.decide

    def observe(*args, **kwargs):
        assert opens.calls == []
        assert kwargs["reset_at"] == opening + 52 * MINUTE
        return original(*args, **kwargs)

    monkeypatch.setattr(reentry, "decide", observe)
    result = reentry.replay_context_reentry(prepared, first, session, opens)
    request = result.request.episode.pending
    losses = sum(max(F(0), -leg.net_pnl) for leg in first.episode.book.realized_exits)
    expected = (prepared.risk_budget - losses) // (F(119) * (1 + F(33, 10_000)) - 112)
    assert result.status == "closed" and result.policy.endswith("fixed_holding") and result.parent_arm == "chart_context"
    assert request.quantity == expected == 12
    assert (request.bounds.stop, request.bounds.max_price) == (112, 119)
    assert result.episode.end_at == first.episode.end_at == opening + 62 * MINUTE
    assert result.episode.risk_budget == prepared.risk_budget and result.episode.attempts == 2
    assert result.net_increment == -F(12 * 118 * 33, 10_000)
    assert result.net_increment == result.episode.book.cash - first.episode.book.cash
    assert opens.calls == [opening + 53 * MINUTE, opening + 62 * MINUTE]


@pytest.mark.parametrize("price,status,reason", [
    (F(120), "not_reentered", "open_outside_frozen_entry_bounds"),
    (F(112), "not_reentered", "open_outside_frozen_entry_bounds"),
    (F(124), "not_reentered", "entry_at_or_above_target"),
    (None, "unresolved", "entry_price_unknown"),
])
def test_second_open_gap_decline_and_unknown_remain_distinct(price, status, reason):
    prepared, first, session = reset_fixture()
    opens = RequestedOnly({session.opening + 53 * MINUTE: price})
    result = reentry.replay_context_reentry(prepared, first, session, opens)
    assert (result.status, result.reason) == (status, reason)
    assert result.net_increment == (None if price is None else F(0))
    assert result.episode.attempts == 2 and result.episode.pending is None
    assert result.episode.book.inventory_uncertain == (price is None)
    assert result.holding is None


def test_unknown_reset_is_unresolved_without_open_or_zero_payoff():
    prepared, first, session = reset_fixture(missing=42)
    opens = RequestedOnly({})
    result = reentry.replay_context_reentry(prepared, first, session, opens)
    assert result.status == "unresolved" and result.net_increment is None
    assert result.reason == "fresh_context_prefix_unknown" and opens.calls == []


def test_exhausted_total_loss_budget_does_not_create_another_attempt():
    prepared, first, session = reset_fixture(exit_price=F(100))
    opens = RequestedOnly({})
    result = reentry.replay_context_reentry(prepared, first, session, opens)
    assert result.status == "not_reentered" and result.reason == "insufficient_risk_or_cash_for_one_unit"
    assert result.episode.attempts == 1 and result.net_increment == 0 and opens.calls == []


def test_third_attempt_risk_change_expiry_change_or_uncertain_first_close_are_refused():
    prepared, first, session = reset_fixture()
    for episode in (replace(first.episode, risk_budget=F(101)), replace(first.episode, end_at=first.episode.end_at + MINUTE)):
        with pytest.raises(InputRefusal, match="risk_cost_or_expiry"):
            reentry.replay_context_reentry(prepared, replace(first, episode=episode), session, RequestedOnly({}))
    with pytest.raises(InputRefusal, match="first_confirmed_flat"):
        reentry.replay_context_reentry(prepared, replace(first, episode=replace(first.episode, attempts=2)),
                                       session, RequestedOnly({}))
    failed = reentry.replay_context_reentry(prepared, replace(first, status="unresolved"), session, RequestedOnly({}))
    assert failed.status == "unresolved" and failed.net_increment is None and failed.evidence is None


def test_new_causal_close_plus_band_is_capped_by_the_original_target():
    prepared, first, session = reset_fixture()
    rows = dict(session.minutes)
    for index in range(45, 65):
        at = session.opening + index * MINUTE
        prices = ((Decimal(113), Decimal("123.75"), Decimal(113), Decimal("123.5")) if index < 50
                  else (Decimal("123.5"), Decimal("123.75"), Decimal("123.25"), Decimal("123.5")))
        rows[at] = replace(rows[at], ohlc=prices)
    session = replace(session, minutes=rows)
    opens = RequestedOnly({session.opening + 53 * MINUTE: F(495, 4), session.opening + 62 * MINUTE: F(495, 4)})
    result = reentry.replay_context_reentry(prepared, first, session, opens)
    assert result.status == "closed" and result.request.episode.pending.bounds.max_price == 124
    assert result.request.episode.pending.bounds.stop == prepared.features.question.path.stop
    assert result.episode.end_at == prepared.features.question.path.end_at


@pytest.mark.parametrize("mutation", ["erase_exits", "cash", "cost", "next_lot", "quantity",
                                     "missing_decision", "missing_fill", "reverse", "price", "cap"])
def test_parent_account_and_complete_action_trace_are_rebuilt_before_remaining_risk(mutation):
    prepared, first, session = reset_fixture()
    book = first.episode.book
    if mutation == "erase_exits":
        book = replace(book, realized_exits=())
    elif mutation == "cash":
        book = replace(book, cash=book.cash + 1)
    elif mutation == "cost":
        book = replace(book, realized_exits=(replace(book.realized_exits[0], allocated_entry_cost=F()),))
    elif mutation == "next_lot":
        book = replace(book, next_lot_id=book.next_lot_id + 1)
    elif mutation == "quantity":
        first = replace(first, events=(replace(first.events[0], quantity=13), first.events[1]))
    elif mutation == "missing_decision":
        first = replace(first, events=first.events[1:])
    elif mutation == "missing_fill":
        first = replace(first, events=first.events[:-1])
    elif mutation == "reverse":
        first = replace(first, events=first.events[::-1])
    elif mutation == "price":
        first = replace(first, events=(first.events[0], replace(first.events[1], open_price=F(115))))
    else:
        first = replace(first, cap=first.cap - MINUTE)
    first = replace(first, episode=replace(first.episode, book=book))
    opens = RequestedOnly({})
    with pytest.raises(InputRefusal, match="EI_reentry_parent_"):
        reentry.replay_context_reentry(prepared, first, session, opens)
    assert opens.calls == []


@pytest.mark.parametrize("offset", [33, 38])
def test_parent_prices_are_bound_to_original_past_rows(offset):
    prepared, first, session = reset_fixture()
    rows = dict(session.minutes)
    at = session.opening + offset * MINUTE
    rows[at] = replace(rows[at], ohlc=(Decimal(113), Decimal(115), Decimal(113), Decimal(114)))
    opens = RequestedOnly({})
    with pytest.raises(InputRefusal, match="EI_reentry_parent_"):
        reentry.replay_context_reentry(prepared, first, replace(session, minutes=rows), opens)
    assert opens.calls == []


def test_parent_partial_exit_cost_and_lot_lineage_are_preserved():
    prepared, _, session = reset_fixture(close_at=43, exit_price=F(113))
    opening = session.opening
    initial, _ = fill_context_entry(prepared, opening + 33 * MINUTE, F(114))

    def actions(view):
        return Values(view.at, hold=F(), reduce_half=F(1) if view.at == opening + 37 * MINUTE else None,
                      exit=F() if view.at == opening + 37 * MINUTE else F(1))

    first = _position_replay(initial, session, {opening + 38 * MINUTE: F(114), opening + 43 * MINUTE: F(113)}, actions)
    assert len(first.episode.book.realized_exits) == 2
    result = reentry.replay_context_reentry(prepared, first, session, RequestedOnly({}))
    assert result.status == "not_reentered" and result.episode.book == first.episode.book
    legs = first.episode.book.realized_exits
    changed = replace(first, episode=replace(first.episode,
        book=replace(first.episode.book, next_lot_id=100,
                     realized_exits=(replace(legs[0], lot_id=99), legs[1]))))
    with pytest.raises(InputRefusal, match="cash_or_action_lineage"):
        reentry.replay_context_reentry(prepared, changed, session, RequestedOnly({}))
