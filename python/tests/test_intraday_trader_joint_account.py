"""Invented-only EE accounting qualification; no readers or actual evidence."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from collections.abc import Mapping

import pytest

from research import intraday_trader_joint_account as p
from research import intraday_trader_joint_reference as r
from research import intraday_campaign_account as ec
from research import intraday_campaign_cash_book as cash
from research import intraday_campaign_independent as old
from research.intraday_ed_inputs import Minute
from research import intraday_trader_paths as paths
from research import intraday_ed_snapshot as codec


M = timedelta(minutes=1)
DAY = date(2026, 1, 5)
OPEN = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
CLOSE = OPEN + 240 * M
POLICIES = ("frozen-policy-one", "frozen-policy-two")


def candidate(slot="A", symbol="invented-X", signal=30, stop=90, target=120):
    return p.Candidate(slot, POLICIES[slot == "B"], symbol, OPEN + signal * M,
                       OPEN + (signal + 2) * M, Fraction(stop), Fraction(target))


def quotes(plans, *, entry=100, close=101, sale=103):
    minutes, opens = {}, {}
    for plan in plans:
        at = plan.entry_at
        while at <= plan.cap_exit_at:
            op = sale if at == plan.cap_exit_at else entry
            values = tuple(Decimal(str(x)) for x in (op, max(op, close), min(op, close), close))
            minutes[plan.symbol, at] = Minute(plan.symbol, at, at + 3 * M,
                                              values, None, volume_state="missing")
            opens[plan.symbol, at] = Decimal(str(op))
            at += M
    return minutes, opens


def run(plans, minutes=None, opens=None, **kwargs):
    if minutes is None:
        minutes, opens = quotes(plans)
    args = (plans, minutes, opens, {DAY: OPEN}, {DAY: CLOSE})
    result = p.replay(*args, policies=POLICIES, **kwargs)
    r.verify(result, *args, policies=POLICIES, **kwargs)
    return result


def put_close(minutes, plan, offset, close):
    at = plan.entry_at + offset * M
    old = minutes[plan.symbol, at]
    op = old.ohlc[0]
    value = Decimal(str(close))
    minutes[plan.symbol, at] = replace(old, ohlc=(op, max(op, value), min(op, value), value))


def exits(result):
    return [e for e in result.events if e.kind == "exit"]


def test_next_minute_cap_exact_integer_and_cost_once():
    plan = candidate()
    assert plan.entry_at == OPEN + 33 * M
    result = run([plan])
    entry, = [e for e in result.events if e.kind == "entry"]
    quantity = (cash.INITIAL_CAPITAL / 2 * cash.ORDER_FRACTION) // 100
    assert entry.quantity == quantity
    assert entry.assumed_cost == quantity * 100 * Fraction(33, 10000)
    sale, = exits(result)
    assert (sale.at, sale.reason, sale.assumed_cost) == (plan.cap_exit_at, "time_cap", 0)
    assert result.state.book.cash == cash.INITIAL_CAPITAL + quantity * 3 - entry.assumed_cost
    assert result.snapshots[-1].nav == result.state.book.cash


def test_closed_joint_codec_roundtrip_keeps_unknown_held_plan_and_target():
    plan = candidate()
    minutes, opens = quotes([plan])
    del opens[plan.symbol, plan.cap_exit_at]
    result = run([plan], minutes, opens)
    restored = codec._decode(p.AccountResult, codec.encode(result))
    assert restored == result
    assert type(restored.state) is p.State
    assert type(restored.state.holdings[0]) is p.Held
    assert restored.state.holdings[0].candidate.target == plan.target


@pytest.mark.parametrize("unknown", [False, True])
def test_explicit_flat_idle_compression_preserves_events_state_and_held_ticks(unknown):
    plan = candidate()
    minutes, opens = quotes([plan])
    if unknown:
        del opens[plan.symbol, plan.cap_exit_at]
    full = run([plan], minutes, opens)
    sparse = run([plan], minutes, opens, compress_idle_minutes=True)
    assert sparse.events == full.events and sparse.state == full.state
    assert sparse.idle_minutes_compressed and not full.idle_minutes_compressed
    assert sparse.snapshots[-1] == full.snapshots[-1]
    assert len(sparse.snapshots) < len(full.snapshots)
    held_times = {s.at for s in full.snapshots if plan.entry_at <= s.at < plan.cap_exit_at}
    assert held_times <= {s.at for s in sparse.snapshots}


@pytest.mark.parametrize("close,reason", [(89, "structural_close_invalidation"),
                                        (120, "known_target_close_reached")])
def test_exit_after_availability_plus_one_never_touch_fill(close, reason):
    plan = candidate()
    minutes, opens = quotes([plan])
    put_close(minutes, plan, 0, close)
    result = run([plan], minutes, opens)
    event, = exits(result)
    assert (event.at, event.reason) == (plan.entry_at + 4 * M, reason)
    assert event.cash_delta == event.quantity * 100


def test_response_failure_after_tenth_consecutive_observed_close():
    plan = candidate()
    minutes, opens = quotes([plan], close=99)
    event, = exits(run([plan], minutes, opens))
    assert (event.at, event.reason) == (plan.entry_at + 13 * M, "response_failure")


def test_structural_and_target_priorities_before_response():
    plan = candidate(stop=99)
    minutes, opens = quotes([plan], close=100)
    put_close(minutes, plan, 9, 99)
    event, = exits(run([plan], minutes, opens))
    assert event.reason == "structural_close_invalidation"


def test_past_above_entry_close_prevents_response_and_cap_absorbs_late_trigger():
    plan = candidate()
    minutes, opens = quotes([plan], close=100)
    put_close(minutes, plan, 0, 101)
    put_close(minutes, plan, 9, 99)
    put_close(minutes, plan, 56, 89)
    event, = exits(run([plan], minutes, opens))
    assert (event.at, event.reason) == (plan.cap_exit_at, "time_cap")


def test_intraminute_both_touches_do_not_invent_level_fill_or_exit_order():
    plan = candidate()
    minutes, opens = quotes([plan])
    row = minutes[plan.symbol, plan.entry_at]
    minutes[plan.symbol, plan.entry_at] = replace(row, ohlc=tuple(map(Decimal, (100, 125, 85, 101))))
    result = run([plan], minutes, opens)
    assert [(e.at, e.reason) for e in exits(result)] == [(plan.cap_exit_at, "time_cap")]


def test_stable_order_collision_does_not_read_duplicate_entry_quote():
    a, b = candidate("A"), candidate("B")
    result = run([b, a])
    entries = [e for e in result.events if e.kind == "entry"]
    assert [(e.strategy, e.reason) for e in entries] == [("A", "entered"), ("B", "duplicate_position")]
    assert entries[1].quantity == 0


def test_simultaneous_entry_unknown_refreshes_gate_before_second_candidate():
    first = candidate("A", "invented-A")
    second = candidate("B", "invented-B")
    minutes, opens = quotes([first, second])
    del opens[first.symbol, first.entry_at]
    result = run([second, first], minutes, opens)
    entries = [event for event in result.events if event.kind == "entry"]
    assert [(event.symbol, event.reason) for event in entries] == [
        (first.symbol, "entry_price_unknown"),
        (second.symbol, "entry_blocked:account_indeterminate"),
    ]
    assert result.state.book.cash == cash.INITIAL_CAPITAL
    assert not result.state.book.lots


def test_simultaneous_entry_cost_refreshes_daily_gate_before_second_candidate():
    held = candidate("A", "invented-held", stop=1, target=1000)
    first = candidate("A", "invented-A", signal=35)
    second = candidate("B", "invented-B", signal=35)
    minutes, opens = quotes([held, first, second])
    # At the new batch's OPEN, the just-available held CLOSE leaves the NAV
    # 1700 above the existing daily floor; the first entry's cost crosses it.
    put_close(minutes, held, 2, "50.5")
    result = run([second, first, held], minutes, opens)
    before = next(s for s in result.snapshots
                  if s.at == first.entry_at and s.phase == "before_entries")
    after = next(s for s in result.snapshots
                 if s.at == first.entry_at and s.phase == "after_entry")
    assert not before.entry_gates and after.entry_gates == ("daily",)
    entries = [event for event in result.events
               if event.kind == "entry" and event.at == first.entry_at]
    assert [(event.symbol, event.reason) for event in entries] == [
        (first.symbol, "entered"), (second.symbol, "entry_blocked:daily")]


@pytest.mark.parametrize("signal,accepted", [(235, True), (240, False)])
def test_decision_cutoff_uses_available_decision_clock_not_signal_end(signal, accepted):
    plan = candidate(signal=signal)
    args = ([plan], *quotes([plan]), {DAY: OPEN}, {DAY: OPEN + 390 * M})
    assert plan.available_at == OPEN + (signal + 2) * M
    if accepted:
        result = p.replay(*args, policies=POLICIES)
        r.verify(result, *args, policies=POLICIES)
        assert any(event.reason == "entered" for event in result.events)
    else:
        with pytest.raises(ValueError, match="clock"):
            p.replay(*args, policies=POLICIES)
        with pytest.raises(ValueError, match="clock"):
            r.reconstruct(*args, policies=POLICIES)


def test_two_distinct_names_overlap_existing_half_sleeves_and_no_fraction_shares():
    a, b = candidate("A", "invented-X"), candidate("B", "invented-Y")
    result = run([b, a])
    entries = [e for e in result.events if e.kind == "entry"]
    assert len(entries) == 2 and all(type(e.quantity) is int and e.quantity > 0 for e in entries)
    assert entries[1].quantity <= entries[0].quantity


def test_many_simultaneous_invented_names_hit_cash_ceiling_without_borrowing():
    plans = [candidate(symbol=f"invented-{i:03d}") for i in range(55)]
    result = run(plans)
    entries = [e for e in result.events if e.kind == "entry"]
    assert any(e.reason == "insufficient_cash_for_one_share" for e in entries)
    assert sum(-e.cash_delta for e in entries) <= cash.INITIAL_CAPITAL / 2
    entered = [e for e in entries if e.quantity]
    assert entered[-1].quantity < entered[0].quantity
    assert all(s.cash >= 0 for s in result.state.book.sleeves)


@pytest.mark.parametrize("mode,slot", [("A", "A"), ("B", "B")])
def test_independent_single_modes_use_existing_full_sleeve(mode, slot):
    result = run([candidate(slot)], mode=mode)
    entry, = [e for e in result.events if e.kind == "entry"]
    assert entry.quantity == (cash.INITIAL_CAPITAL * cash.ORDER_FRACTION) // 100


def test_same_exit_clock_sale_precedes_entry_and_does_not_consume_exit_row_close():
    first = candidate()
    second = candidate(symbol="invented-Y", signal=90)
    minutes, opens = quotes([first, second])
    minutes[first.symbol, first.cap_exit_at] = replace(minutes[first.symbol, first.cap_exit_at],
                                                       ohlc=(Decimal(103), object(), object(), object()))
    result = run([second, first], minutes, opens)
    at = [e for e in result.events if e.at == first.cap_exit_at]
    assert [(e.kind, e.reason) for e in at] == [("exit", "time_cap"), ("entry", "entered")]


@pytest.mark.parametrize("cost", [33, 43])
def test_entry_gate_always_primary_and_strict(cost):
    plan = candidate(target=Fraction(100_34, 100))
    result = run([plan], cost_bps=cost)
    assert any(e.reason == "entered" for e in result.events)
    exact_boundary = replace(plan, target=Fraction(100_33, 100))
    result = run([exact_boundary], cost_bps=cost)
    assert [e.reason for e in result.events] == ["entry_cost_space_not_strict"]


@pytest.mark.parametrize("missing", ["entry", "held", "exit"])
def test_unknown_not_filtered_keeps_history_sticky_no_retry(missing):
    first = candidate()
    second = candidate("B", "invented-Y", signal=95)
    minutes, opens = quotes([first, second])
    at = {"entry": first.entry_at, "held": first.entry_at, "exit": first.cap_exit_at}[missing]
    if missing == "held":
        del minutes[first.symbol, at]
    else:
        del opens[first.symbol, at]
    result = run([first, second], minutes, opens)
    assert result.state.unknown_at is not None and result.state.book.indeterminate
    assert not any(e.symbol == second.symbol and e.reason == "entered" for e in result.events)
    assert any(e.symbol == first.symbol for e in result.events)
    if missing == "exit":
        assert len(result.state.book.lots) == len(result.state.holdings) == 1
        assert len(exits(result)) == 1
    else:
        assert result.state.book.cash > 0
    if missing == "entry":
        assert result.state.book.cash == cash.INITIAL_CAPITAL
        assert not result.state.book.lots
        assert any(e.reason == "entry_price_unknown" for e in result.events)


def test_missing_first_held_close_does_not_infer_later_trigger_or_retry():
    plan = candidate()
    minutes, opens = quotes([plan])
    del minutes[plan.symbol, plan.entry_at]
    put_close(minutes, plan, 1, 89)
    del opens[plan.symbol, plan.cap_exit_at]
    result = run([plan], minutes, opens)
    assert result.state.unknown_at == plan.entry_at + 3 * M
    assert not any(e.kind == "trigger" for e in result.events)
    assert result.state.holdings[0].path_unknown
    assert len(result.state.book.lots) == 1
    assert [(e.at, e.reason) for e in exits(result)] == [(plan.cap_exit_at, "exit_price_unknown")]


def test_current_mark_remains_required_at_cap_absorbed_trigger_minute():
    plan = candidate()
    minutes, opens = quotes([plan])
    del minutes[plan.symbol, plan.cap_exit_at - 4 * M]
    result = run([plan], minutes, opens)
    assert result.state.unknown_at == plan.cap_exit_at - M
    assert exits(result)[0].reason == "time_cap"
    assert result.state.book.indeterminate and not result.state.book.lots


def test_future_held_availability_refused_and_signal_clock_not_silently_shifted():
    plan = candidate()
    minutes, opens = quotes([plan])
    row = minutes[plan.symbol, plan.entry_at]
    minutes[plan.symbol, plan.entry_at] = replace(row, available_at=row.available_at + M)
    args = ([plan], minutes, opens, {DAY: OPEN}, {DAY: CLOSE})
    with pytest.raises(ValueError, match="unavailable"):
        p.replay(*args, policies=POLICIES)
    with pytest.raises(ValueError, match="unavailable"):
        r.reconstruct(*args, policies=POLICIES)
    with pytest.raises(ValueError, match="plus_two"):
        replace(plan, available_at=plan.available_at + M)


def test_later_suffix_cannot_cancel_earlier_known_target():
    plan = candidate()
    minutes, opens = quotes([plan])
    put_close(minutes, plan, 0, 120)
    del minutes[plan.symbol, plan.entry_at + M]
    result = run([plan], minutes, opens)
    assert not result.state.book.indeterminate
    assert exits(result)[0].reason == "known_target_close_reached"


def test_risk_limit_blocks_new_entry_using_current_mark_and_existing_helper():
    first = candidate(stop=1, target=1000)
    second = candidate("B", "invented-Y", signal=35)
    minutes, opens = quotes([first, second], close=2)
    result = run([first, second], minutes, opens)
    assert any(e.symbol == second.symbol and "daily" in e.reason for e in result.events)
    assert any("daily" in snap.entry_gates for snap in result.snapshots)


def test_daily_limit_exact_inclusive_boundary_reuses_existing_risk_primitive():
    plan = candidate(stop=1, target=1000)
    minutes, opens = quotes([plan], close=101)
    quantity = (cash.INITIAL_CAPITAL / 2 * cash.ORDER_FRACTION) // 100
    charged = quantity * 100 * Fraction(33, 10000)
    boundary = (cash.INITIAL_CAPITAL * Fraction(199, 200)
                - (cash.INITIAL_CAPITAL - quantity * 100 - charged)) / quantity
    at = plan.entry_at + 3 * M
    # This hand-calculated boundary has an exact finite Decimal representation.
    old = minutes[plan.symbol, plan.entry_at]
    value = Decimal(boundary.numerator) / Decimal(boundary.denominator)
    minutes[plan.symbol, plan.entry_at] = replace(old, ohlc=(Decimal(100), Decimal(100), value, value))
    result = run([plan], minutes, opens)
    snap = next(s for s in result.snapshots if s.at == at)
    assert dict(snap.period_returns)["daily"] == Fraction(-1, 200)
    assert "daily" in snap.entry_gates


def test_simultaneous_losses_apply_all_unchanged_existing_period_limits():
    plans = [candidate(symbol=f"invented-{i}", stop=Fraction(1, 2), target=1000) for i in range(6)]
    later = candidate("B", "invented-later", signal=35)
    plans.append(later)
    result = run(plans, *quotes(plans, close=1))
    event, = [e for e in result.events if e.symbol == later.symbol]
    assert event.reason == "entry_blocked:daily,weekly,monthly,hard_monthly"


def test_unknown_exit_lot_and_continuous_history_survive_next_session():
    first = candidate()
    tomorrow = timedelta(days=1)
    later = replace(candidate("B", "invented-Y"), signal_at=OPEN + 30 * M + tomorrow,
                    available_at=OPEN + 32 * M + tomorrow)
    minutes, opens = quotes([first, later])
    del opens[first.symbol, first.cap_exit_at]
    calendar_open = {DAY: OPEN, DAY + tomorrow: OPEN + tomorrow}
    calendar_close = {DAY: CLOSE, DAY + tomorrow: CLOSE + tomorrow}
    args = ([first, later], minutes, opens, calendar_open, calendar_close)
    result = p.replay(*args, policies=POLICIES)
    r.verify(result, *args, policies=POLICIES)
    assert len(result.state.book.lots) == 1
    assert result.state.unknown_at == first.cap_exit_at
    assert len(exits(result)) == 1
    assert [e.reason for e in result.events if e.symbol == later.symbol] == ["entry_blocked:account_indeterminate"]


def test_one_share_order_limit_and_state_contract_refusals():
    plan = candidate(stop=1, target=10_000_000)
    result = run([plan], *quotes([plan], entry=2_000_001, close=2_000_001, sale=2_000_001))
    assert [e.reason for e in result.events] == ["one_share_exceeds_nav_limit"]
    with pytest.raises(ValueError, match="frozen_policy"):
        p.replay([replace(plan, policy_id="different")], *quotes([plan]), {DAY: OPEN}, {DAY: CLOSE}, policies=POLICIES)
    with pytest.raises((TypeError, ValueError)):
        p.Candidate("A", POLICIES[0], "invented-X", OPEN + 30 * M, OPEN + 32 * M, 90, 120,
                    future_status="traded")


def test_independent_reference_denies_producer_oracles_and_detects_cash_tampering(monkeypatch):
    plan = candidate()
    minutes, opens = quotes([plan])
    args = ([plan], minutes, opens, {DAY: OPEN}, {DAY: CLOSE})
    result = p.replay(*args, policies=POLICIES)
    def forbidden(*args, **kwargs):
        raise AssertionError("producer oracle called")
    for module, names in ((p, ("replay", "new_state", "_opening", "_close")),
                          (cash, ("enter", "exit", "mark", "_cash_entry_amounts")),
                          (ec, ("_risk_observation", "_value", "replay_account")),
                          (paths, ("evaluate_path",))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    assert r.verify(result, *args, policies=POLICIES)["verified"] is True
    sleeve = result.state.book.sleeves[0]
    corrupted = replace(result, state=replace(result.state, book=replace(result.state.book,
        sleeves=(replace(sleeve, cash=sleeve.cash + 1),) + result.state.book.sleeves[1:])))
    with pytest.raises(ValueError):
        r.verify(corrupted, *args, policies=POLICIES)


class GetOnly(Mapping):
    """A lazy day cache may be queried but never enumerated/materialized."""

    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, key):
        raise AssertionError("use coordinate get only")

    def __iter__(self):
        raise AssertionError("price mapping iteration forbidden")

    def __len__(self):
        raise AssertionError("price mapping materialization forbidden")

    def get(self, key, default=None):
        return self.rows.get(key, default)


@pytest.mark.parametrize("mode", ["A", "B", "A+B"])
@pytest.mark.parametrize("cost", [33, 43])
def test_whole_calendar_equals_independent_day_carry_all_account_contracts(mode, cost):
    days = (date(2026, 1, 30), date(2026, 2, 2), date(2026, 2, 3))
    plans = []
    for day in days:
        for slot in (("A", "B") if mode == "A+B" else (mode,)):
            offset = day - DAY
            base = candidate(slot, "invented-" + slot)
            plans.append(replace(base, signal_at=base.signal_at + offset,
                                 available_at=base.available_at + offset))
    minutes, opens = quotes(plans, sale=98)
    opening = {day: OPEN + (day - DAY) for day in days}
    closing = {day: CLOSE + (day - DAY) for day in days}
    args = (plans, GetOnly(minutes), GetOnly(opens), opening, closing)
    whole = p.replay(*args, policies=POLICIES, mode=mode, cost_bps=cost)
    r.verify(whole, *args, policies=POLICIES, mode=mode, cost_bps=cost)
    producer_state, reference_state = None, None
    events, snapshots = [], []
    for day in days:
        own = [plan for plan in plans if plan.signal_at.date() == day]
        # The caller exposes only today's coordinates and discards day output.
        own_minutes = GetOnly({key: row for key, row in minutes.items() if key[1].date() == day})
        own_opens = GetOnly({key: price for key, price in opens.items() if key[1].date() == day})
        day_args = (own, own_minutes, own_opens, {day: opening[day]}, {day: closing[day]})
        result = p.replay(*day_args, policies=POLICIES, mode=mode, cost_bps=cost, state=producer_state)
        receipt = r.verify(result, *day_args, policies=POLICIES, mode=mode, cost_bps=cost,
                           reference_state=reference_state)
        producer_state, reference_state = result.state, receipt["reference_state"]
        assert producer_state is not reference_state
        events.extend(result.events)
        snapshots.extend(result.snapshots)
    assert producer_state == whole.state
    assert tuple(events) == whole.events and tuple(snapshots) == whole.snapshots
    assert producer_state.book.cash < cash.INITIAL_CAPITAL
    assert producer_state.maximum_drawdown > 0
    assert producer_state.weekly_anchor.period == "2026-W06"
    assert producer_state.monthly_anchor.period == "2026-02"


def test_daily_carry_preserves_unknown_lot_response_counters_and_no_exit_retry():
    plan = candidate()
    minutes, opens = quotes([plan])
    del opens[plan.symbol, plan.cap_exit_at]
    first_args = ([plan], GetOnly(minutes), GetOnly(opens), {DAY: OPEN}, {DAY: CLOSE})
    first = p.replay(*first_args, policies=POLICIES)
    independent = r.verify(first, *first_args, policies=POLICIES)["reference_state"]
    original_reference = old._plain(independent)
    tomorrow = timedelta(days=1)
    later = replace(candidate("B", "invented-Y"), signal_at=OPEN + 30 * M + tomorrow,
                    available_at=OPEN + 32 * M + tomorrow)
    args = ([later], GetOnly({}), GetOnly({}), {DAY + tomorrow: OPEN + tomorrow},
            {DAY + tomorrow: CLOSE + tomorrow})
    second = p.replay(*args, policies=POLICIES, state=first.state)
    receipt = r.verify(second, *args, policies=POLICIES, reference_state=independent)
    assert old._plain(independent) == original_reference  # reference input is never mutated
    assert second.state.holdings == first.state.holdings
    assert second.state.holdings[0].observed_count > 10
    assert second.state.book == first.state.book
    assert second.state.unknown_at == first.state.unknown_at
    assert second.state.daily_anchor == first.state.daily_anchor
    assert not exits(second)
    assert [e.reason for e in second.events if e.symbol == later.symbol] == ["entry_blocked:account_indeterminate"]
    assert receipt["reference_state"]["unknown_at"] == independent["unknown_at"]


@pytest.mark.parametrize("change", ["policies", "cost", "mode", "time", "producer_as_reference",
                                    "producer_projection_as_reference"])
def test_carry_refuses_changed_contract_and_mixed_state_lineage(change):
    plan = candidate()
    args = ([plan], *quotes([plan]), {DAY: OPEN}, {DAY: CLOSE})
    first = p.replay(*args, policies=POLICIES)
    independent = r.verify(first, *args, policies=POLICIES)["reference_state"]
    tomorrow = timedelta(days=1)
    empty = ([], GetOnly({}), GetOnly({}), {DAY + tomorrow: OPEN + tomorrow},
             {DAY + tomorrow: CLOSE + tomorrow})
    kwargs = {"policies": POLICIES}
    if change == "policies":
        kwargs["policies"] = tuple(reversed(POLICIES))
    elif change == "cost":
        kwargs["cost_bps"] = 43
    elif change == "mode":
        kwargs["mode"] = "A"
    elif change == "time":
        empty = ([], GetOnly({}), GetOnly({}), {DAY: OPEN}, {DAY: CLOSE})
    if change in ("producer_as_reference", "producer_projection_as_reference"):
        supplied = first.state if change == "producer_as_reference" else old._plain(first.state)
        with pytest.raises(ValueError, match="reference.*state"):
            r.reconstruct(*empty, **kwargs, reference_state=supplied)
    else:
        with pytest.raises(ValueError):
            p.replay(*empty, **kwargs, state=first.state)
        with pytest.raises(ValueError):
            r.reconstruct(*empty, **kwargs, reference_state=independent)
