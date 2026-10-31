"""Invented ED oracle checks; producer output is generated before sabotage."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research import intraday_campaign_account as old_account
from research import intraday_campaign_cash_book as old_cash
from research import intraday_ed_replay as producer
from research import intraday_ed_account_verify as oracle
from research.intraday_ed_inputs import Minute


MINUTE = timedelta(minutes=1)
OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))
ARM = "ED-H1-RULE"


def plan(symbol="invented", *, opening=OPEN, stop=95, offset=30):
    return producer.EDCandidate(f"{opening.date()}:{symbol}", symbol, opening, opening + 390 * MINUTE,
                               opening + (offset + 2) * MINUTE, opening + (offset + 5) * MINUTE,
                               opening + (offset + 65) * MINUTE, Fraction(stop))


def minute(symbol, at, price=100, *, close=None, available=None):
    close = price if close is None else close
    values = tuple(Decimal(str(value)) for value in (price, max(price, close), min(price, close), close))
    return Minute(symbol, at, available or at + 3 * MINUTE, values, Decimal(100))


def prices(candidate, *, price=100, exit_price=110):
    result = {(candidate.symbol, candidate.entry_at + index * MINUTE):
              minute(candidate.symbol, candidate.entry_at + index * MINUTE, price) for index in range(61)}
    result[candidate.symbol, candidate.cap_exit_at] = minute(candidate.symbol, candidate.cap_exit_at, exit_price)
    return result


def accept(candidate):
    return producer.EDAction(candidate.decision_at, "accept", "invented_accept", candidate)


def run(candidate=None, *, cost=33, supplied=None, actions=None, session=None, state=None):
    candidate = candidate or plan()
    session = session or producer.EDSession(candidate.opening, candidate.closing)
    actions = (accept(candidate),) if actions is None else tuple(actions)
    supplied = prices(candidate) if supplied is None else supplied
    return producer.replay_ed_account(actions, supplied, (session,), arm=ARM, cost_bps=cost, state=state), actions, supplied, session


@pytest.mark.parametrize("cost", [33, 43])
def test_oracle_reconstructs_entire_state_events_snapshots_and_exact_cost(cost):
    actual, actions, supplied, session = run(cost=cost)
    with localcontext() as ctx:
        ctx.prec = 2
        receipt = oracle.EDAccountVerifier(ARM, cost).verify_day(actions, supplied, session, actual)
    assert receipt["verified"] and receipt["producer_state_used_as_input"] is False
    assert receipt["snapshots"] == 392
    entry = next(event for event in actual.events if event.reason == "entered")
    assert entry.quantity == 20_000
    assert entry.assumed_cost == Fraction(2_000_000 * cost, 10000)
    assert actual.state.book.cash == Fraction(100_200_000) - entry.assumed_cost
    assert actual.snapshots[-1].same_quantity_gross_nav == Fraction(100_200_000)


def test_oracle_never_calls_producer_replay_risk_cash_or_state_helpers(monkeypatch):
    actual, actions, supplied, session = run()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("producer helper used by independent oracle")
    for module, names in ((producer, ("replay_ed_account", "new_ed_state", "_value", "_freeze", "_opening", "_completed")),
                          (old_account, ("_risk_observation", "_stop_schedule", "_registered_cost")),
                          (old_cash, ("_cash_entry_amounts", "_gross_exit_proceeds", "_marked_nav"))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    assert oracle.EDAccountVerifier(ARM).verify_day(actions, supplied, session, actual)["verified"]


def test_compressed_idle_clocks_keep_actions_exits_and_every_held_observation():
    candidate = plan()
    actions, supplied = (accept(candidate),), prices(candidate)
    session = producer.EDSession(candidate.opening, candidate.closing)
    actual = producer.replay_ed_account(actions, supplied, (session,), arm=ARM, compress_idle_minutes=True)
    receipt = oracle.EDAccountVerifier(ARM).verify_day(actions, supplied, session, actual, compress_idle_minutes=True)
    assert receipt["verified"] and receipt["snapshots"] < 100
    assert actual.snapshots[0].at == session.opening and actual.snapshots[-1].at == session.closing
    held_clocks = {snapshot.at for snapshot in actual.snapshots}
    assert all(candidate.entry_at + index * MINUTE in held_clocks for index in range(61))
    unavailable = replace(session, policy_state="unavailable", reason="no_training")
    missing = producer.replay_ed_account((), {}, (unavailable,), arm=ARM, compress_idle_minutes=True)
    assert oracle.EDAccountVerifier(ARM).verify_day((), {}, unavailable, missing, compress_idle_minutes=True)["verified"]


@pytest.mark.parametrize("field", ["cash", "cost", "quantity", "gross", "event", "snapshot", "anchor", "fractional_quantity"])
def test_changed_account_scalar_event_or_snapshot_fails_without_advancing_oracle(field):
    actual, actions, supplied, session = run()
    changed = oracle._plain(actual)
    if field == "cash":
        changed["state"]["book"]["cash"] += 1
    elif field == "cost":
        changed["state"]["total_entry_cost"] += 1
    elif field == "quantity":
        next(event for event in changed["events"] if event["reason"] == "entered")["quantity"] += 1
    elif field == "gross":
        changed["snapshots"][-1]["same_quantity_gross_nav"] += 1
    elif field == "event":
        changed["events"][-1]["reason"] = "fabricated"
    elif field == "snapshot":
        changed["snapshots"].pop()
    elif field == "anchor":
        changed["state"]["daily_anchor"]["nav"] += 1
    else:
        event = next(event for event in changed["events"] if event["reason"] == "entered")
        event["quantity"] = Fraction(event["quantity"])
    verifier = oracle.EDAccountVerifier(ARM)
    with pytest.raises(oracle.VerificationError):
        verifier.verify_day(actions, supplied, session, changed)
    assert verifier.verified_days == 0
    assert verifier.verify_day(actions, supplied, session, actual)["verified"]


def test_continuous_oracle_owns_prior_cash_anchors_and_sticky_unknown_history():
    first, actions, supplied, session = run()
    verifier = oracle.EDAccountVerifier(ARM)
    verifier.verify_day(actions, supplied, session, first)
    next_plan = plan(opening=OPEN + timedelta(days=3))
    second, a2, m2, s2 = run(next_plan, state=first.state)
    assert verifier.verify_day(a2, m2, s2, second)["verified_days"] == 2
    fresh, _, _, _ = run(next_plan)
    another = oracle.EDAccountVerifier(ARM)
    another.verify_day(actions, supplied, session, first)
    with pytest.raises(oracle.VerificationError):
        another.verify_day(a2, m2, s2, fresh)
    unavailable = replace(s2, policy_state="unavailable", reason="missing_training")
    unknown = producer.replay_ed_account((), {}, (unavailable,), arm=ARM, state=first.state)
    v3 = oracle.EDAccountVerifier(ARM)
    v3.verify_day(actions, supplied, session, first)
    receipt = v3.verify_day((), {}, unavailable, unknown)
    assert not receipt["full_population_identified"]
    third_plan = plan(opening=OPEN + timedelta(days=4))
    third, a3, m3, s3 = run(third_plan, state=unknown.state)
    assert v3.verify_day(a3, m3, s3, third)["verified"]
    assert third.state.first_unknown_at == unavailable.opening
    assert not any(event.reason == "entered" for event in third.events)


def test_fresh_day_diagnostic_resets_own_capital_without_weekly_monthly_claim():
    candidate = plan()
    session = producer.EDSession(candidate.opening, candidate.closing)
    actual = producer.replay_ed_daily_counterfactuals((accept(candidate),), prices(candidate), (session,), arm=ARM)[0]
    verifier = oracle.EDAccountVerifier(ARM, 33, "day_local_counterfactual")
    assert verifier.verify_day((accept(candidate),), prices(candidate), session, actual)["verified"]
    assert actual.state.weekly_anchor is None and actual.state.monthly_anchor is None
    next_candidate = plan(opening=OPEN + timedelta(days=3))
    next_session = producer.EDSession(next_candidate.opening, next_candidate.closing)
    second = producer.replay_ed_daily_counterfactuals((accept(next_candidate),), prices(next_candidate),
                                                    (next_session,), arm=ARM)[0]
    assert verifier.verify_day((accept(next_candidate),), prices(next_candidate), next_session, second)["verified"]


def test_conditional_domain_is_explicit_and_cannot_certify_full_original_population():
    actual, actions, supplied, session = run()
    conditional = replace(actual, scenario="observed_predecision_support_conditional/continuous_sticky_unknown")
    verifier = oracle.EDAccountVerifier(ARM, domain="observed_predecision_support_conditional")
    receipt = verifier.verify_day(actions, supplied, session, conditional)
    assert receipt["verified"] and not receipt["full_population_identified"]
    with pytest.raises(oracle.VerificationError):
        oracle.EDAccountVerifier(ARM).verify_day(actions, supplied, session, conditional)


class RequestedOnly(Mapping):
    def __init__(self, rows, allowed):
        self.rows, self.allowed = rows, set(allowed)
    def __getitem__(self, key):
        assert key in self.allowed, "unrequested future or post-exit coordinate"
        return self.rows[key]
    def __iter__(self):
        raise AssertionError("whole price map enumerated")
    def __len__(self):
        raise AssertionError("whole price map sized")


def test_close_stop_waits_for_availability_and_missing_exit_is_never_retried():
    candidate = plan()
    supplied = prices(candidate)
    supplied[candidate.symbol, candidate.entry_at] = minute(candidate.symbol, candidate.entry_at, close=94)
    exit_at = candidate.entry_at + 4 * MINUTE
    supplied[candidate.symbol, exit_at] = minute(candidate.symbol, exit_at, 80, close=9999)
    allowed = {(candidate.symbol, candidate.entry_at), (candidate.symbol, exit_at)}
    guarded = RequestedOnly(supplied, allowed)
    actual, actions, _, session = run(candidate, supplied=guarded)
    assert oracle.EDAccountVerifier(ARM).verify_day(actions, guarded, session, actual)["verified"]
    stop = next(event for event in actual.events if event.kind == "stop")
    assert stop.at == candidate.entry_at + 3 * MINUTE and stop.scheduled_exit_at == exit_at
    missing = RequestedOnly({(candidate.symbol, candidate.entry_at): supplied[candidate.symbol, candidate.entry_at]}, allowed)
    unknown, actions, _, session = run(candidate, supplied=missing)
    receipt = oracle.EDAccountVerifier(ARM).verify_day(actions, missing, session, unknown)
    assert not receipt["full_population_identified"]
    assert len(unknown.state.book.lots) == 1
    assert sum(event.kind == "exit" for event in unknown.events) == 1


def test_same_clock_exit_precedes_new_entry_and_unknown_action_freezes_both():
    first = plan("first")
    second = plan("second", offset=90)
    actions = (accept(first), accept(second))
    supplied = prices(first) | prices(second)
    session = producer.EDSession(first.opening, first.closing)
    actual = producer.replay_ed_account(actions, supplied, (session,), arm=ARM)
    assert oracle.EDAccountVerifier(ARM).verify_day(actions, supplied, session, actual)["verified"]
    same_clock = [event.kind for event in actual.events if event.at == first.cap_exit_at]
    assert same_clock == ["exit", "entry"]
    unknown = producer.EDAction(second.entry_at, "unknown", "original_target_unknown", symbol="other", parent_id="other")
    actions += (unknown,)
    changed = producer.replay_ed_account(actions, supplied, (session,), arm=ARM)
    assert oracle.EDAccountVerifier(ARM).verify_day(actions, supplied, session, changed)["verified"]
    assert any(event.reason == "entry_blocked:account_indeterminate" for event in changed.events)


def test_exact_risk_thresholds_and_post_cost_recheck_include_hard_monthly():
    candidates = tuple(plan(f"loss{index}", stop=1) for index in range(3))
    later = plan("blocked", offset=35)
    supplied = {}
    for candidate in candidates:
        supplied.update(prices(candidate, exit_price=10))
        for index in range(57):
            at = candidate.entry_at + index * MINUTE
            supplied[candidate.symbol, at] = minute(candidate.symbol, at, 100, close=10)
    supplied.update(prices(later))
    actions = tuple(accept(candidate) for candidate in (*candidates, later))
    session = producer.EDSession(OPEN, OPEN + 390 * MINUTE)
    actual = producer.replay_ed_account(actions, supplied, (session,), arm=ARM)
    assert oracle.EDAccountVerifier(ARM).verify_day(actions, supplied, session, actual)["verified"]
    snapshot = next(value for value in actual.snapshots if value.at == OPEN + 38 * MINUTE)
    assert snapshot.entry_gates == ("daily", "weekly", "monthly", "hard_monthly")
    changed = deepcopy(oracle._plain(actual))
    target = next(value for value in changed["snapshots"] if value["at"] == OPEN + 38 * MINUTE)
    target["entry_gates"].remove("hard_monthly")
    with pytest.raises(oracle.VerificationError):
        oracle.EDAccountVerifier(ARM).verify_day(actions, supplied, session, changed)
