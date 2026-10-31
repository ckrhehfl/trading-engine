"""Invented-only ED clocks, causal unit labels, exact cash and sticky unknowns."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research import intraday_campaign_account as legacy_account
from research import intraday_campaign_cash_book as legacy_cash
from research.intraday_ed_inputs import H1Parent, Minute
from research.intraday_ed_replay import (
    ARMS, EDAction, EDCandidate, EDReplayRefusal, EDSession, candidate_for_parent,
    ed_unit_label, new_ed_state, replay_ed_account, replay_ed_daily_counterfactuals,
)
from research.intraday_pattern_events import Bar


MINUTE = timedelta(minutes=1)
OPENING = datetime(2025, 1, 2, 9, tzinfo=timezone(timedelta(hours=9)))
ARM = "ED-H1-RULE"


def candidate(symbol="invented", *, opening=OPENING, offset=30, parent_id=None, stop=95):
    end = opening + offset * MINUTE
    entry = end + 5 * MINUTE
    return EDCandidate(parent_id or symbol, symbol, opening, opening + 390 * MINUTE,
                       end + 2 * MINUTE, entry, entry + 60 * MINUTE, Fraction(stop))


def minute(symbol, at, op=100, *, high=None, low=None, close=None, available_at=None):
    close = op if close is None else close
    high = max(op, close) if high is None else high
    low = min(op, close) if low is None else low
    return Minute(symbol, at, available_at or at + 3 * MINUTE,
                  tuple(map(lambda value: Decimal(str(value)), (op, high, low, close))),
                  Decimal(100))


def prices(plan, *, entry=100, exit_price=110):
    result = {(plan.symbol, plan.entry_at + index * MINUTE):
              minute(plan.symbol, plan.entry_at + index * MINUTE, entry)
              for index in range(61)}
    result[plan.symbol, plan.cap_exit_at] = minute(plan.symbol, plan.cap_exit_at, exit_price)
    return result


def sessions(*plans):
    return tuple(EDSession(plan.opening, plan.closing) for plan in plans)


def accept(plan):
    return EDAction(plan.decision_at, "accept", "invented_accept", plan)


def test_idle_compression_preserves_actions_held_minutes_events_and_state():
    plan = candidate()
    later = plan.opening + 120 * MINUTE
    actions = (accept(plan), EDAction(later, "unknown", "invented_later_gap", symbol="other"))
    values = prices(plan)
    full = replay_ed_account(actions, values, sessions(plan), arm=ARM)
    compact = replay_ed_account(actions, values, sessions(plan), arm=ARM, compress_idle_minutes=True)
    assert compact.state == full.state and compact.events == full.events
    expected = {(s.at, s.phase): s for s in full.snapshots}
    assert all(s == expected[s.at, s.phase] for s in compact.snapshots)
    assert compact.snapshots[-1] == full.snapshots[-1]
    held = {plan.entry_at + i * MINUTE for i in range(60)}
    assert held <= {s.at for s in compact.snapshots}
    assert later in {s.at for s in compact.snapshots}
    assert len(compact.snapshots) < len(full.snapshots)
    with pytest.raises(EDReplayRefusal, match="boolean_idle_compression_required"):
        replay_ed_account(actions, values, sessions(plan), arm=ARM, compress_idle_minutes=1)


class RequestedOnly(Mapping):
    """Fail if replay reads an unrequested/future coordinate or enumerates prices."""

    def __init__(self, values, allowed):
        self.values, self.allowed = values, set(allowed)
        self.reads = []

    def __getitem__(self, key):
        assert key in self.allowed, f"future/unrequested price: {key}"
        self.reads.append(key)
        return self.values[key]

    def __iter__(self):
        raise AssertionError("price mapping must not be enumerated")

    def __len__(self):
        raise AssertionError("price mapping must not be sized")


def test_parent_adapter_binds_preceding_five_and_strict_next_latency_open():
    bars = tuple(Bar(OPENING + index * 5 * MINUTE, OPENING + (index + 1) * 5 * MINUTE,
                     OPENING + ((index + 1) * 5 + 2) * MINUTE,
                     Decimal(100), Decimal(101), Decimal(90 + index),
                     Decimal(102 if index == 5 else 100)) for index in range(6))
    bars = bars[:-1] + (replace(bars[-1], high=Decimal(103)),)
    parent = H1Parent("invented", OPENING, bars[-1], bars[:5])
    plan = candidate_for_parent(parent, closing=OPENING + 390 * MINUTE, parent_id="parent-v1")
    assert plan.structural_stop == 90
    assert plan.decision_at == OPENING + 32 * MINUTE
    assert plan.entry_at == OPENING + 35 * MINUTE
    assert plan.cap_exit_at == OPENING + 95 * MINUTE
    assert plan.parent_id == "parent-v1"
    with pytest.raises(EDReplayRefusal, match="upside_break"):
        candidate_for_parent(replace(parent, trigger=replace(parent.trigger, close=Decimal(100))),
                             closing=plan.closing, parent_id="bad")


def test_unit_label_exact_primary_stress_and_maturity_without_last_three_closes():
    plan = candidate()
    supplied = prices(plan)
    allowed = {(plan.symbol, plan.entry_at + offset * MINUTE) for offset in range(57)}
    allowed.add((plan.symbol, plan.cap_exit_at))
    guarded = RequestedOnly(supplied, allowed)
    with localcontext() as ctx:
        ctx.prec = 2
        label = ed_unit_label(plan, guarded)
    assert label.status == "known_traded" and label.known_filled_unit
    assert label.gross == Fraction(1, 10)
    assert label.net33 == Fraction(967, 10_000)
    assert label.net43 == Fraction(957, 10_000)
    assert label.mature_at == plan.cap_exit_at + 3 * MINUTE
    assert label.entry_reason == "scheduled_OPEN_proxy" and label.exit_reason == "time_cap"
    assert label.entry_source == label.exit_source
    assert len(label.path) == 57
    assert label.path[-1].observed_at == plan.cap_exit_at - MINUTE
    assert label.path[-1].minute_start == plan.cap_exit_at - 4 * MINUTE
    assert not label.low_touched_stop


def test_first_postentry_close_invalidation_uses_later_open_even_through_stop_gap():
    plan = candidate()
    supplied = prices(plan)
    supplied[plan.symbol, plan.entry_at] = minute(plan.symbol, plan.entry_at, 100, low=90, close=94)
    exit_at = plan.entry_at + 4 * MINUTE
    supplied[plan.symbol, exit_at] = minute(plan.symbol, exit_at, 80, high=9999, low=1, close=9999)
    guarded = RequestedOnly(supplied, {(plan.symbol, plan.entry_at), (plan.symbol, exit_at)})
    label = ed_unit_label(plan, guarded)
    assert label.reason == "delayed_structural_stop"
    assert label.exit_at == exit_at and label.exit_price == 80
    assert label.gross == Fraction(-1, 5)
    assert label.minimum_observed_low_return == Fraction(-1, 10)
    assert label.low_touched_stop
    assert len(label.path) == 1
    # Perturb every unconsumed future bar: neither exit nor completeness changes.
    suffix = dict(supplied)
    for key in tuple(suffix):
        if key not in guarded.allowed:
            suffix[key] = None
    assert ed_unit_label(plan, suffix) == label


def test_low_touch_alone_is_not_a_stop_price_fill():
    plan = candidate()
    supplied = prices(plan)
    supplied[plan.symbol, plan.entry_at] = minute(plan.symbol, plan.entry_at, 100, low=90, close=100)
    label = ed_unit_label(plan, supplied)
    assert label.reason == "time_cap" and label.exit_price == 110
    assert label.low_touched_stop and label.minimum_observed_low_return == Fraction(-1, 10)


@pytest.mark.parametrize("stop_offset,reason", [(56, "stop_at_time_cap"), (57, "time_cap")])
def test_cap_tie_and_cap_clock_observation_is_never_consulted(stop_offset, reason):
    plan = candidate()
    supplied = prices(plan)
    at = plan.entry_at + stop_offset * MINUTE
    supplied[plan.symbol, at] = minute(plan.symbol, at, 100, low=90, close=94)
    supplied[plan.symbol, plan.cap_exit_at] = minute(plan.symbol, plan.cap_exit_at, 89)
    label = ed_unit_label(plan, supplied)
    assert label.reason == reason and label.exit_at == plan.cap_exit_at
    assert label.exit_price == 89
    assert all(item.observed_at < plan.cap_exit_at for item in label.path)


@pytest.mark.parametrize("entry_price", [90, 95])
def test_known_invalid_entry_is_zero_policy_nofill_not_a_filled_training_label(entry_price):
    plan = candidate()
    only_entry = {(plan.symbol, plan.entry_at): minute(plan.symbol, plan.entry_at, entry_price)}
    label = ed_unit_label(plan, only_entry)
    assert label.status == "known_declined_entry" and not label.known_filled_unit
    assert label.net33 == label.net43 == label.gross == 0
    assert label.path == () and label.exit_at is None and label.exit_price is None
    result = replay_ed_account((accept(plan),), only_entry, sessions(plan), arm=ARM)
    assert result.state.book.cash == legacy_cash.INITIAL_CAPITAL
    assert result.state.total_entry_cost == 0 and not result.state.book.lots
    assert not result.state.book.indeterminate
    assert [event.reason for event in result.events if event.kind == "entry"] == ["invalidated_entry"]


@pytest.mark.parametrize("missing", ["entry", "path", "exit", "late_close"])
def test_missing_or_unavailable_required_coordinate_is_unknown_never_zero(missing):
    plan = candidate()
    supplied = prices(plan)
    at = {"entry": plan.entry_at, "path": plan.entry_at + MINUTE,
          "exit": plan.cap_exit_at, "late_close": plan.entry_at}[missing]
    if missing == "late_close":
        supplied[plan.symbol, at] = replace(supplied[plan.symbol, at], available_at=at + 4 * MINUTE)
    else:
        del supplied[plan.symbol, at]
    label = ed_unit_label(plan, supplied)
    assert label.status == "unknown" and not label.known_filled_unit
    assert label.gross is None and label.net33 is None and label.net43 is None
    assert label.first_unknown == (plan.symbol, at)
    assert label.mature_at is None


@pytest.mark.parametrize("bad_field,value,error", [
    ("entry_at", OPENING + 34 * MINUTE, "strict_next"),
    ("decision_at", OPENING + 31 * MINUTE, "five_minute"),
    ("cap_exit_at", OPENING + 90 * MINUTE, "sixty_minute"),
    ("closing", OPENING + 109 * MINUTE, "liquidation"),
])
def test_candidate_refuses_changed_clocks_and_shortened_cap(bad_field, value, error):
    with pytest.raises(EDReplayRefusal, match=error):
        replace(candidate(), **{bad_field: value})


def test_late_opening_is_session_relative_and_last_eligible_cap_not_shortened():
    plan = candidate(opening=OPENING + 60 * MINUTE, offset=310)
    assert plan.entry_at == plan.opening + 315 * MINUTE
    assert plan.cap_exit_at == plan.closing - 15 * MINUTE
    assert ed_unit_label(plan, prices(plan)).status == "known_traded"
    with pytest.raises(EDReplayRefusal, match="liquidation"):
        candidate(opening=plan.opening, offset=315)


@pytest.mark.parametrize("cost_bps", [33, 43])
def test_account_integer_quantity_cashflow_and_same_quantity_gross_attribution(cost_bps):
    plan = candidate(stop=600_000)
    result = replay_ed_account((accept(plan),), prices(plan, entry=700_000, exit_price=710_000),
                               sessions(plan), arm=ARM, cost_bps=cost_bps)
    entry = next(event for event in result.events if event.kind == "entry")
    exit_event = next(event for event in result.events if event.kind == "exit")
    cost = Fraction(1_400_000 * cost_bps, 10_000)
    assert entry.quantity == exit_event.quantity == 2
    assert entry.cash_delta == -1_400_000 - cost
    assert entry.assumed_cost == result.state.total_entry_cost == cost
    assert exit_event.cash_delta == 1_420_000 and exit_event.assumed_cost == 0
    assert result.state.realized_gross_pnl == 20_000
    assert result.state.book.cash == legacy_cash.INITIAL_CAPITAL + 20_000 - cost
    assert not result.state.book.lots and not result.state.book.indeterminate
    assert result.snapshots[-1].full_nav == result.state.book.cash
    assert result.snapshots[-1].same_quantity_gross_nav == legacy_cash.INITIAL_CAPITAL + 20_000


def test_simultaneous_entry_order_uses_symbol_then_parent_and_revalues_entry_cost():
    a, b = candidate("a", parent_id="z"), candidate("b", parent_id="a")
    supplied = prices(a) | prices(b)
    result = replay_ed_account((accept(b), accept(a)), supplied, sessions(a), arm=ARM)
    entries = [event for event in result.events if event.kind == "entry"]
    assert [event.symbol for event in entries] == ["a", "b"]
    assert entries[0].quantity == 20_000
    # The first entry cost reduces exact common NAV before the second sizing.
    assert entries[1].quantity == 19_998
    assert sum(event.assumed_cost for event in entries) == result.state.total_entry_cost


def test_unknown_action_and_unavailable_zero_parent_day_stay_indeterminate_on_resumption():
    plan = candidate()
    unknown = EDAction(plan.decision_at, "unknown", "earlier_parent_gap", symbol=plan.symbol)
    first = replay_ed_account((unknown, accept(plan)), prices(plan), sessions(plan), arm=ARM)
    assert first.state.first_unknown_at == unknown.at
    assert first.state.book.cash == legacy_cash.INITIAL_CAPITAL and not first.state.book.lots
    assert first.snapshots[-1].full_nav is None
    assert first.snapshots[-1].same_quantity_gross_nav is None
    assert first.snapshots[-1].observed_nav == legacy_cash.INITIAL_CAPITAL
    next_plan = candidate(opening=OPENING + timedelta(days=1), parent_id="tomorrow")
    resumed = replay_ed_account((accept(next_plan),), prices(next_plan), sessions(next_plan),
                                arm=ARM, state=first.state)
    assert resumed.state.first_unknown_at == unknown.at and not resumed.state.book.lots
    assert all(snapshot.full_nav is None for snapshot in resumed.snapshots)
    unavailable = replay_ed_account((), {}, (EDSession(OPENING, plan.closing, "unavailable", "fold_train_missing"),),
                                    arm="ED-H1-LGBM")
    assert unavailable.state.first_unknown_at == OPENING
    assert unavailable.state.book.indeterminate and unavailable.snapshots[0].full_nav is None


def test_missing_held_close_freezes_full_population_while_known_scheduled_exit_can_continue():
    plan = candidate()
    supplied = prices(plan)
    del supplied[plan.symbol, plan.entry_at + MINUTE]
    result = replay_ed_account((accept(plan),), supplied, sessions(plan), arm=ARM)
    assert result.state.first_unknown_at == plan.entry_at + 4 * MINUTE
    assert result.state.book.indeterminate and not result.state.book.lots
    assert result.snapshots[-1].full_nav is None
    assert result.snapshots[-1].observed_nav == result.state.book.cash
    assert next(event for event in result.events if event.kind == "exit").quantity > 0


def test_missing_scheduled_exit_retains_lot_cash_and_never_retries_later_open():
    plan = candidate()
    supplied = prices(plan)
    del supplied[plan.symbol, plan.cap_exit_at]
    # No minute after this cap can repair the scheduled sale or be consulted.
    allowed = set(supplied)
    allowed.add((plan.symbol, plan.cap_exit_at))
    guarded = RequestedOnly(supplied, allowed)
    result = replay_ed_account((accept(plan),), guarded, sessions(plan), arm=ARM)
    assert len(result.state.book.lots) == 1
    assert result.state.first_unknown_at == plan.cap_exit_at
    assert result.state.book.cash == legacy_cash.INITIAL_CAPITAL - 2_000_000 - 6600
    assert result.snapshots[-1].full_nav is None and result.snapshots[-1].observed_nav is None
    assert [event.reason for event in result.events if event.kind == "exit"] == ["exit_price_unknown"]
    assert max(at for _, at in guarded.reads) == plan.cap_exit_at


def test_held_price_loss_gate_blocks_later_entry_before_reading_its_open():
    first, later = candidate("first"), candidate("later", offset=35)
    supplied = prices(first, entry=100, exit_price=60)
    # A 40% fall on the 2%-of-NAV position exceeds the standing daily loss gate.
    start = first.entry_at + 2 * MINUTE
    supplied[first.symbol, start] = minute(first.symbol, start, 60)
    result = replay_ed_account((accept(later), accept(first)), supplied, sessions(first), arm=ARM)
    refused = next(event for event in result.events if event.kind == "entry" and event.symbol == "later")
    assert refused.reason.startswith("entry_blocked:daily")
    assert not any("missing_entry_price:later" == reason for reason in result.state.book.indeterminate_reasons)


def test_fresh_day_diagnostics_remain_separate_from_unknown_continuous_history():
    a, b = candidate(), candidate(opening=OPENING + timedelta(days=1), parent_id="day2")
    unknown = EDAction(a.opening, "unknown", "missing_parent_population")
    outputs = replay_ed_daily_counterfactuals((unknown, accept(b)), prices(b), sessions(a, b), arm=ARM)
    assert outputs[0].state.book.indeterminate
    assert not outputs[1].state.book.indeterminate
    assert outputs[1].state.weekly_anchor is None and outputs[1].state.monthly_anchor is None
    assert all("fresh_day_counterfactual" in output.scenario for output in outputs)


@pytest.mark.parametrize("arm", sorted(ARMS))
def test_every_native_arm_has_the_same_full_budget_and_old_ids_are_not_accepted(arm):
    state = new_ed_state(arm)
    assert state.book.cash == legacy_cash.INITIAL_CAPITAL
    assert state.book.arm == arm and not state.book.lots
    with pytest.raises(ValueError, match="unknown_mode"):
        legacy_cash.new_book(arm)
    with pytest.raises(legacy_account.AccountRefusal, match="unknown_strategy"):
        legacy_account.CampaignCandidate(arm, "invented", OPENING + 32 * MINUTE,
                                         OPENING + 35 * MINUTE, OPENING + 95 * MINUTE, Fraction(95))


@pytest.mark.parametrize("bad_arm", ["A", "B", "H4", "H5", "ED-H2-RULE", "ED-H1-TCN"])
def test_new_book_refuses_unregistered_or_old_strategy_ids(bad_arm):
    with pytest.raises(EDReplayRefusal, match="unknown_ed_arm"):
        new_ed_state(bad_arm)


def test_old_cost_clock_and_allocation_guards_are_unchanged():
    for cost in (34, 66, 86):
        with pytest.raises(legacy_account.AccountRefusal, match="registered_cost"):
            legacy_account.new_state("A", cost_bps=cost)
        with pytest.raises(legacy_account.AccountRefusal, match="registered_cost"):
            new_ed_state(ARM, cost_bps=cost)
    for constructor in (lambda: legacy_account.new_state("A", cost_bps=0),
                        lambda: new_ed_state(ARM, cost_bps=0)):
        with pytest.raises(legacy_account.AccountRefusal, match="positive_price_required"):
            constructor()
    assert legacy_cash.new_book("A+B").sleeve("A").initial_allocation == 50_000_000
    with pytest.raises(legacy_account.AccountRefusal, match="time_only_stop"):
        legacy_account.CampaignCandidate("H4", "invented", OPENING + 92 * MINUTE,
                                         OPENING + 95 * MINUTE, OPENING + 155 * MINUTE, Fraction(95))
    plan = legacy_account.CampaignCandidate("A", "invented", OPENING + 32 * MINUTE,
                                            OPENING + 34 * MINUTE, OPENING + 94 * MINUTE, Fraction(95))
    with pytest.raises(legacy_account.AccountRefusal, match="original_candidate_clock"):
        legacy_account.replay_account((plan,), {}, {}, {OPENING.date(): OPENING},
                                       {OPENING.date(): OPENING + 390 * MINUTE}, mode="A")


def test_cash_primitive_limits_and_cost_budget_are_identical_to_legacy_wrapper():
    supplied = legacy_cash._cash_entry_amounts(Fraction(100_000_000), Fraction(50_000_000),
        Fraction(1_000_000), Fraction(100), Fraction(33, 10_000))
    quantity, budget, cost, delta, reason = supplied
    assert quantity == 9967 and budget == Fraction(1_000_000 * 10_000, 10_033)
    assert Fraction(1_000_000) + delta == Fraction(1089, 100)
    assert cost == -delta - quantity * 100 and reason == "entered"


def test_duplicate_parent_coordinate_cannot_create_a_replacement_episode():
    first = candidate(parent_id="first")
    replacement = candidate(offset=35, parent_id="replacement")
    with pytest.raises(EDReplayRefusal, match="one_parent_per_symbol_session"):
        replay_ed_account((accept(first), accept(replacement)), prices(first), sessions(first), arm=ARM)


def test_resumption_rejects_a_changed_initial_budget_or_float_account_amounts():
    initial = new_ed_state(ARM)
    changed = replace(initial, book=replace(initial.book, cash=initial.book.cash + 1))
    with pytest.raises(EDReplayRefusal, match="cash_flow_conservation"):
        replay_ed_account((), {}, (), arm=ARM, state=changed)
    with pytest.raises(EDReplayRefusal, match="exact_account"):
        replay_ed_account((), {}, (), arm=ARM, state=replace(initial, cost_bps=33.0))
