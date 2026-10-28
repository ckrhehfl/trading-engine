"""Invented-only tests of causal EC account clocks and exact cash paths."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from random import Random
from unittest.mock import patch

import pytest

from research import intraday_campaign_account as account
from research import intraday_campaign_cash_book as cash


DAY = date(2025, 1, 6)
MINUTE = timedelta(minutes=1)


def at(hour=9, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=account.KST)


def candidate(symbol="x", strategy="A", entry=None, decision=None, stop=1):
    entry = entry or at(9, 5)
    return account.CampaignCandidate(strategy, symbol, decision or entry - MINUTE,
                                     entry, entry + 60 * MINUTE, Fraction(stop))


def panel(*plans, price=100, exit_price=None):
    minutes, opens = {}, {}
    for plan in plans:
        t = plan.entry_at
        while t <= plan.cap_exit_at:
            op = price if t < plan.cap_exit_at or exit_price is None else exit_price
            opens[plan.symbol, t] = op
            minutes[plan.symbol, t] = (op, op, op, op)
            t += MINUTE
    return minutes, opens


def run(plans=(), minutes=None, opens=None, *, days=(DAY,), mode="A", state=None,
        cost_bps=33, compress_idle_minutes=False):
    return account.replay_account(plans, minutes or {}, opens or {},
                                  {day: at(day=day) for day in days},
                                  {day: at(15, 30, day) for day in days},
                                  mode=mode, state=state, cost_bps=cost_bps,
                                  compress_idle_minutes=compress_idle_minutes)


def events(result, kind="entry"):
    return [item for item in result.events if item.kind == kind]


@pytest.mark.parametrize("cost", [33, 43])
def test_exact_integer_account_arithmetic_and_cost_once(cost):
    plan = candidate()
    minutes, opens = panel(plan, price=700_000, exit_price=710_000)
    with localcontext() as ctx:
        ctx.prec = 3
        result = run([plan], minutes, opens, cost_bps=cost)
    entry, = events(result)
    sale, = events(result, "exit")
    assert entry.quantity == sale.quantity == 2
    assert entry.assumed_cost == Fraction(1_400_000 * cost, 10_000)
    assert sale.assumed_cost == 0
    assert result.state.book.cash == 100_000_000 + 20_000 - entry.assumed_cost
    assert result.state.book.lots == result.state.holdings == ()
    assert result.snapshots[0].nav == 100_000_000
    assert result.snapshots[-1].nav == result.state.book.cash
    assert result.state.maximum_drawdown == entry.assumed_cost / 100_000_000


def test_joint_sleeves_have_fixed_half_budget():
    a, b = candidate("a"), candidate("b", "B")
    minutes, opens = panel(a, b)
    result = run([b, a], minutes, opens, mode="A+B")
    assert [(item.symbol, item.quantity) for item in events(result)] == [("a", 10000), ("b", 10000)]
    assert all(s.cash == 50_000_000 - 3300 for s in result.state.book.sleeves)


def test_three_minute_close_lag_and_four_minute_stop_exit():
    plan = candidate(stop=95)
    minutes, opens = panel(plan)
    minutes["x", plan.entry_at] = (100, 100, 90, 90)
    # This would make old EA's full outcome unknown. The causal account exits
    # before this close is available, so it cannot retroactively freeze it.
    minutes["x", plan.entry_at + MINUTE] = None
    stop, = events(result := run([plan], minutes, opens), "stop")
    sale, = events(result, "exit")
    assert stop.at == plan.entry_at + 3 * MINUTE
    assert stop.scheduled_exit_at == sale.at == plan.entry_at + 4 * MINUTE
    marks = {row.at: row for row in result.snapshots if row.phase == "before_entries"}
    initial_mark = 100_000_000 - 6600
    assert marks[plan.entry_at + MINUTE].nav == initial_mark
    assert marks[plan.entry_at + 2 * MINUTE].nav == initial_mark
    assert marks[plan.entry_at + 3 * MINUTE].nav == initial_mark - 200_000
    assert not result.state.book.indeterminate


def test_exit_uses_open_only_without_its_future_high_low_close():
    plan = candidate(stop=95)
    minutes, opens = panel(plan)
    minutes["x", plan.entry_at] = (100, 100, 90, 90)
    exit_at = plan.entry_at + 4 * MINUTE
    minutes["x", exit_at] = (120, "not_yet_known", None, object())
    opens["x", exit_at] = 120
    result = run([plan], minutes, opens)
    assert events(result, "exit")[0].cash_delta == 20_000 * 120
    assert not result.state.book.indeterminate


def test_missing_entry_freezes_account_without_fabricating_fill_and_persists_next_day():
    first = candidate()
    next_day = DAY + timedelta(days=1)
    later = candidate("later", entry=at(9, 5, next_day))
    result = run([first])
    assert result.state.book.indeterminate
    assert result.state.unknown_at == first.entry_at
    assert result.state.book.cash == 100_000_000 and not result.state.book.lots
    assert events(result)[0].reason == "entry_price_unknown"
    assert result.snapshots[-1].at == first.entry_at
    assert result.snapshots[-1].phase == "before_entries"
    minutes, opens = panel(later)
    resumed = run([later], minutes, opens, days=(next_day,), state=result.state)
    assert events(resumed)[0].reason == "entry_blocked:account_indeterminate"
    assert resumed.snapshots == ()
    assert resumed.state.unknown_at == first.entry_at


@pytest.mark.parametrize("missing", ["row", "open", "mismatch"])
def test_missing_held_observation_freezes_exact_first_required_minute(missing):
    plan = candidate()
    minutes, opens = panel(plan)
    key = ("x", plan.entry_at + 7 * MINUTE)
    if missing == "row":
        minutes[key] = None
    elif missing == "open":
        opens[key] = None
    else:
        opens[key] = 101
    result = run([plan], minutes, opens)
    assert result.state.unknown_at == plan.entry_at + 10 * MINUTE
    assert result.snapshots[-1].at == plan.entry_at + 9 * MINUTE
    assert result.state.book.indeterminate
    assert result.state.book.cash == 100_000_000 - 6600
    assert result.state.book.lots == ()  # Known scheduled sale still executes.
    assert result.state.maximum_drawdown == Fraction(6600, 100_000_000)


def test_missing_exit_preserves_whole_unresolved_lot_without_retry():
    plan = candidate()
    minutes, opens = panel(plan)
    opens["x", plan.cap_exit_at] = None
    result = run([plan], minutes, opens)
    sale, = events(result, "exit")
    assert sale.reason == "exit_price_unknown" and sale.cash_delta == 0
    assert result.state.unknown_at == plan.cap_exit_at
    assert result.state.book.cash == 97_993_400
    assert result.state.book.lots[0].quantity == 20_000
    assert result.state.holdings[0].exit_at == plan.cap_exit_at
    assert result.snapshots[-1].at == plan.cap_exit_at - MINUTE


def test_failed_exit_does_not_extend_price_reads_past_original_horizon():
    class Traced(dict):
        def __init__(self, values):
            super().__init__(values)
            self.reads = []

        def get(self, key, default=None):
            self.reads.append(key)
            return super().get(key, default)

    plan = candidate()
    minutes, opens = panel(plan)
    opens["x", plan.cap_exit_at] = None
    traced = Traced(minutes)
    result = run([plan], traced, opens)
    assert result.state.book.indeterminate
    assert max(key[1] for key in traced.reads) < plan.cap_exit_at


def test_stop_available_immediately_before_cap_keeps_cap_open():
    plan = candidate(stop=95)
    minutes, opens = panel(plan)
    minutes["x", plan.cap_exit_at - 4 * MINUTE] = (100, 100, 90, 90)
    result = run([plan], minutes, opens)
    stop, = events(result, "stop")
    sale, = events(result, "exit")
    assert stop.at == plan.cap_exit_at - MINUTE
    assert stop.scheduled_exit_at == sale.at == plan.cap_exit_at
    assert sale.reason == "stop_at_time_cap"


def test_same_minute_exit_precedes_entry_and_permits_symbol_reuse():
    first = candidate()
    second = candidate(entry=first.cap_exit_at)
    minutes, opens = panel(first, second)
    result = run([second, first], minutes, opens)
    simultaneous = [event for event in result.events if event.at == second.entry_at]
    assert [event.kind for event in simultaneous] == ["exit", "entry"]
    assert simultaneous[1].reason == "entered"
    assert simultaneous[1].quantity == 19_998  # Prior cost lowers current NAV.


def test_overlap_keeps_first_causal_decision_then_symbol_then_strategy_order():
    a = candidate("same", "A", decision=at(9, 4))
    b = candidate("same", "B", decision=at(9, 3))
    c = candidate("aaa", "A")
    d = candidate("zzz", "B")
    minutes, opens = panel(a, b, c, d)
    result = run([d, a, c, b], minutes, opens, mode="A+B")
    assert [(e.strategy, e.symbol, e.reason) for e in events(result)] == [
        ("B", "same", "entered"), ("A", "aaa", "entered"),
        ("A", "same", "duplicate_position"), ("B", "zzz", "entered")]
    tie_a, tie_b = candidate("tie", "A"), candidate("tie", "B")
    minutes, opens = panel(tie_a)
    tied = run([tie_b, tie_a], minutes, opens, mode="A+B")
    assert events(tied)[0].strategy == "A"
    assert events(tied)[1].reason == "duplicate_position"


def test_invalidated_entry_does_not_consult_future_prices_or_charge_cost():
    plan = candidate(stop=100)
    minutes = {("x", plan.entry_at): (100, object(), object(), object())}
    result = run([plan], minutes, {("x", plan.entry_at): 100})
    assert events(result)[0].reason == "invalidated_entry"
    assert result.state.book.cash == 100_000_000
    assert not result.state.book.indeterminate


def test_no_label_status_or_future_exit_can_control_entry_admission():
    assert {field.name for field in fields(account.CampaignCandidate)} == {
        "strategy", "symbol", "decision_at", "entry_at", "cap_exit_at", "structural_stop"}
    plan = candidate()
    with pytest.raises(TypeError):
        account.CampaignCandidate(**{field.name: getattr(plan, field.name)
                                     for field in fields(plan)}, status="known")
    minutes, opens = panel(plan)
    opens["x", plan.cap_exit_at] = None
    result = run([plan], minutes, opens)
    assert events(result)[0].reason == "entered"
    assert events(result)[0].quantity == 20_000


@pytest.mark.parametrize("row", [(100, 99, 90, 95), (100, 110, 101, 105),
                                 (100, Decimal("NaN"), 90, 95), (100, 100.0, 90, 95),
                                 (100, 100, 0, 95)])
def test_invalid_held_ohlc_is_refused(row):
    plan = candidate()
    minutes, opens = panel(plan)
    minutes["x", plan.entry_at] = row
    with pytest.raises(account.AccountRefusal):
        run([plan], minutes, opens)


def test_unused_future_and_other_symbol_prices_are_never_validated():
    plan = candidate(stop=100)
    minutes = {("x", plan.entry_at + MINUTE): (object(),) * 4,
               ("unused", plan.entry_at): (object(),) * 4}
    result = run([plan], minutes, {("x", plan.entry_at): 100})
    assert not result.state.book.indeterminate


def test_gate_uses_lagged_mark_and_recovers_without_new_latch():
    held = candidate()
    blocked = candidate("blocked", entry=at(9, 10))
    recovered = candidate("recovered", entry=at(9, 15))
    minutes, opens = panel(held, recovered)
    for offset in range(2, 7):
        minutes["x", held.entry_at + offset * MINUTE] = (100, 100, 70, 70)
    result = run([recovered, blocked, held], minutes, opens)
    assert [event.reason for event in events(result)] == [
        "entered", "entry_blocked:daily", "entered"]
    assert not result.state.book.indeterminate  # No blocked candidate quote exists.
    blocked_at = next(row for row in result.snapshots
                      if row.at == blocked.entry_at and row.phase == "before_entries")
    assert blocked_at.entry_gates == ("daily",)
    assert next(row for row in result.snapshots
                if row.at == recovered.entry_at).entry_gates == ()


def test_gate_rechecks_cost_after_each_same_timestamp_entry():
    held = candidate()
    first = candidate("a", entry=at(9, 10))
    second = candidate("b", entry=at(9, 10))
    minutes, opens = panel(held, first)
    minutes["x", at(9, 7)] = (100, 100, Fraction(151, 2), Fraction(151, 2))
    result = run([second, first, held], minutes, opens)
    assert [event.reason for event in events(result)] == [
        "entered", "entered", "entry_blocked:daily"]
    before = next(row for row in result.snapshots
                  if row.at == at(9, 10) and row.phase == "before_entries")
    after = next(row for row in result.snapshots
                 if row.at == at(9, 10) and row.phase == "after_entry")
    assert before.nav == 99_503_400 and not before.entry_gates
    assert after.nav == 99_496_833 and after.entry_gates == ("daily",)


@pytest.mark.parametrize("amount,expected", [
    (99_500_000, ("daily",)), (98_500_000, ("daily", "weekly")),
    (97_000_000, ("daily", "weekly", "monthly")),
    (96_000_000, ("daily", "weekly", "monthly", "hard_monthly"))])
def test_inclusive_current_period_thresholds(amount, expected):
    # A synthetic known prior cash balance isolates exact gate boundaries.
    state = account.new_state("A")
    book = replace(state.book, sleeves=(replace(state.book.sleeves[0], cash=Fraction(amount)),))
    keys = (DAY.isoformat(), "2025-W02", "2025-01")
    anchors = [account.PeriodAnchor(key, Fraction(100_000_000)) for key in keys]
    state = replace(state, book=book, daily_anchor=anchors[0], weekly_anchor=anchors[1],
                    monthly_anchor=anchors[2])
    result = run(state=state)
    assert result.snapshots[0].entry_gates == expected


def test_hard_gate_uses_month_anchor_and_not_peak_drawdown():
    state = replace(account.new_state("A"), peak_nav=Fraction(120_000_000))
    result = run(state=state)
    assert result.snapshots[0].drawdown == Fraction(1, 6)
    assert result.snapshots[0].entry_gates == ()


def test_zero_candidate_sessions_preserve_continuous_week_month_and_reset_periods():
    first = candidate()
    minutes, opens = panel(first, exit_price=70)
    initial = run([first], minutes, opens)
    next_day = DAY + timedelta(days=1)
    day_two = run(days=(next_day,), state=initial.state)
    assert day_two.state.daily_anchor.nav == initial.state.book.cash
    assert day_two.state.weekly_anchor == initial.state.weekly_anchor
    assert day_two.state.monthly_anchor == initial.state.monthly_anchor
    assert dict(day_two.snapshots[0].period_returns)["daily"] == 0
    assert dict(day_two.snapshots[0].period_returns)["weekly"] < 0
    next_week = run(days=(date(2025, 1, 13),), state=day_two.state)
    assert next_week.state.weekly_anchor.nav == initial.state.book.cash
    assert next_week.state.monthly_anchor == initial.state.monthly_anchor
    next_month = run(days=(date(2025, 2, 3),), state=next_week.state)
    assert next_month.state.monthly_anchor.nav == initial.state.book.cash
    assert next_month.state.book.cash == initial.state.book.cash


def test_weekly_gate_survives_new_day_but_resets_on_new_week():
    plan = candidate()
    minutes, opens = panel(plan, exit_price=10)
    first = run([plan], minutes, opens)
    tomorrow = DAY + timedelta(days=1)
    blocked = candidate("blocked", entry=at(9, 5, tomorrow))
    second = run([blocked], days=(tomorrow,), state=first.state)
    assert events(second)[0].reason == "entry_blocked:weekly"
    assert not second.state.book.indeterminate
    next_week = date(2025, 1, 13)
    admitted = candidate("admitted", entry=at(9, 5, next_week))
    minutes, opens = panel(admitted)
    third = run([admitted], minutes, opens, days=(next_week,), state=second.state)
    assert events(third)[0].reason == "entered"


def test_bulk_and_daily_stream_replay_are_identical():
    tomorrow = DAY + timedelta(days=1)
    one, two = candidate(), candidate("two", entry=at(9, 5, tomorrow))
    minutes, opens = panel(one, two, exit_price=105)
    bulk = run([two, one], minutes, opens, days=(DAY, tomorrow))
    first = run([one], minutes, opens)
    second = run([two], minutes, opens, days=(tomorrow,), state=first.state)
    assert bulk.state == second.state
    assert bulk.events == first.events + second.events
    assert bulk.snapshots == first.snapshots + second.snapshots


def test_fresh_daily_counterfactual_is_separate_and_preserves_empty_and_unknown_days():
    next_day = DAY + timedelta(days=1)
    days = (DAY, next_day)
    result = account.replay_daily_counterfactuals(
        [candidate()], {}, {}, {day: at(day=day) for day in days},
        {day: at(15, 30, day) for day in days}, mode="A")
    assert tuple(result) == days
    assert result[DAY].state.book.indeterminate
    assert not result[next_day].state.book.indeterminate
    assert result[next_day].state.book.cash == 100_000_000
    assert all(item.state.risk_scope == "day_local_counterfactual" for item in result.values())
    assert all("no_continuous_weekly_monthly_claim" in item.scenario for item in result.values())
    assert all(tuple(dict(row.period_returns)) == ("daily",)
               for item in result.values() for row in item.snapshots)
    assert all(item.state.weekly_anchor is item.state.monthly_anchor is None
               for item in result.values())


@pytest.mark.parametrize("change", ["naive", "seconds", "late_entry", "cap", "closing"])
def test_invalid_candidate_or_session_clocks_refused(change):
    plan = candidate()
    with pytest.raises(account.AccountRefusal):
        if change == "naive":
            replace(plan, decision_at=plan.decision_at.replace(tzinfo=None))
        elif change == "seconds":
            replace(plan, decision_at=plan.decision_at + timedelta(seconds=1))
        elif change == "late_entry":
            run([candidate(entry=at(9, 10), decision=at(9, 4))])
        elif change == "cap":
            replace(plan, cap_exit_at=plan.cap_exit_at + MINUTE)
        else:
            account.replay_account([plan], {}, {}, {DAY: at()},
                                   {DAY: plan.cap_exit_at}, mode="A")


def test_immutable_candidate_and_contract_cannot_change_between_days():
    plan = candidate()
    with pytest.raises(FrozenInstanceError):
        plan.symbol = "changed"
    initial = run()
    with pytest.raises(account.AccountRefusal, match="contract_changed"):
        run(days=(DAY + timedelta(days=1),), state=initial.state, cost_bps=43)
    with pytest.raises(account.AccountRefusal, match="contract_changed"):
        run(days=(DAY + timedelta(days=1),), state=initial.state, mode="B")
    with pytest.raises(account.AccountRefusal, match="nonchronological"):
        run(state=initial.state)


def test_rejection_for_one_share_above_ceiling_is_explicit():
    plan = candidate()
    minutes, opens = panel(plan, price=2_000_001)
    result = run([plan], minutes, opens)
    assert events(result)[0].reason == "one_share_exceeds_nav_limit"
    assert result.state.book.cash == 100_000_000
    assert not result.state.book.indeterminate


def test_insufficient_cash_rejection_preserves_other_sleeve_cash():
    # Known synthetic depleted sleeve without borrowing from B.
    state = account.new_state("A+B")
    sleeves = (replace(state.book.sleeves[0], cash=Fraction(10)), state.book.sleeves[1])
    state = replace(state, book=replace(state.book, sleeves=sleeves))
    plan = candidate()
    minutes, opens = panel(plan)
    result = run([plan], minutes, opens, mode="A+B", state=state)
    assert events(result)[0].reason == "insufficient_cash_for_one_share"
    assert result.state.book.sleeve("B").cash == 50_000_000


def test_duplicate_identical_plan_is_contract_refusal():
    plan = candidate()
    with pytest.raises(account.AccountRefusal, match="duplicate_candidate_plan"):
        run([plan, plan])


def test_resumption_refuses_lot_without_original_clock_plan():
    initial = account.new_state("A")
    book = cash.enter(initial.book, "A", "orphan", entry_price=100,
                      observed_prices={}, cost_bps=33).book
    with pytest.raises(account.AccountRefusal, match="holding_plan_mismatch"):
        run(state=replace(initial, book=book))


def assert_idle_compression_equivalent(plans=(), minutes=None, opens=None, *,
                                      mode="A", cost_bps=33, state=None, day=DAY):
    from research.intraday_campaign_account_comparison import compact_day

    initial = state or account.new_state(mode, cost_bps=cost_bps)
    kwargs = dict(days=(day,), mode=mode, cost_bps=cost_bps, state=initial)
    dense = run(plans, minutes, opens, **kwargs)
    compressed = run(plans, minutes, opens, compress_idle_minutes=True, **kwargs)
    assert dense.state == compressed.state
    assert dense.events == compressed.events
    essential_clocks = {at(day=day), at(15, 30, day)} | {e.at for e in dense.events}
    expected = tuple(row for row in dense.snapshots
                     if row.at in essential_clocks or row.nav > row.cash)
    assert compressed.snapshots == expected
    day_input = {"session": day.isoformat(), "session_open": at(day=day),
                 "session_close": at(15, 30, day)}
    coverage = {"unknown_signal_units": 1, "recorded_signal_units": len(plans)}
    dense_day = compact_day(day_input, initial, dense, coverage)
    compressed_day = compact_day(day_input, initial, compressed, coverage)
    # This includes exact P&L/cost, time-integrated exposure, closed lots,
    # prefix drawdown, final NAV and the last identified valuation clock.
    assert dense_day == compressed_day
    return dense, compressed, compressed_day


def test_idle_compression_default_remains_dense_and_empty_day_keeps_boundaries():
    dense, compressed, daily = assert_idle_compression_equivalent()
    assert len(dense.snapshots) == 391
    assert [row.at for row in compressed.snapshots] == [at(), at(15, 30)]
    assert daily["net_pnl"] == daily["assumed_cost"] == 0
    assert daily["exposure"]["identified_minutes"] == 390
    assert daily["exposure"]["invested_minutes"] == 0


@pytest.mark.parametrize("flag", [0, 1, None, "true", (), []])
def test_idle_compression_requires_actual_bool(flag):
    with pytest.raises(account.AccountRefusal, match="boolean_idle_compression_required"):
        run(compress_idle_minutes=flag)


@pytest.mark.parametrize("case", ["known", "stop", "missing_entry", "missing_held",
                                 "missing_exit", "mismatched_open", "invalidated",
                                 "cost_gate", "same_time_exit_entry", "duplicate"])
@pytest.mark.parametrize("scope", ["continuous", "day_local_counterfactual"])
def test_idle_compression_preserves_branch_semantics_and_daily_metrics(case, scope):
    first = candidate(stop=95 if case == "stop" else 1)
    second = candidate("later", entry=at(12, 5))
    plans = [first, second]
    minutes, opens = panel(*plans)
    if case == "stop":
        minutes["x", first.entry_at] = (100, 100, 90, 90)
    elif case == "missing_entry":
        opens["x", first.entry_at] = None
    elif case == "missing_held":
        minutes["x", first.entry_at + 4 * MINUTE] = None
    elif case == "missing_exit":
        opens["x", first.cap_exit_at] = None
    elif case == "mismatched_open":
        opens["x", first.entry_at + 4 * MINUTE] = 101
    elif case == "invalidated":
        plans[0] = replace(first, structural_stop=Fraction(100))
    elif case == "cost_gate":
        plans = [first, candidate("a", entry=at(9, 10)), candidate("b", entry=at(9, 10))]
        minutes, opens = panel(*plans[:2])
        minutes["x", at(9, 7)] = (100, 100, Fraction(151, 2), Fraction(151, 2))
    elif case == "same_time_exit_entry":
        plans[1] = candidate(entry=first.cap_exit_at)
        minutes, opens = panel(*plans)
    elif case == "duplicate":
        plans.append(candidate(entry=at(9, 10)))
    dense, compressed, daily = assert_idle_compression_equivalent(
        plans, minutes, opens, state=account.new_state("A", risk_scope=scope))
    assert len(compressed.snapshots) < len(dense.snapshots)
    if case == "cost_gate":
        assert daily["risk_rejections"] == 1
    elif case == "duplicate":
        assert daily["same_symbol_declines"] == 1
    elif case.startswith("missing") or case == "mismatched_open":
        assert daily["status"] == "indeterminate"


def test_idle_compression_keeps_every_unresolved_holding_minute(monkeypatch):
    plan = candidate()
    minutes, opens = panel(plan)
    opens["x", plan.cap_exit_at] = None
    called = []
    original = account._value

    def traced_value(state, clock, prices, phase):
        if phase == "before_entries":
            called.append(clock)
        return original(state, clock, prices, phase)

    monkeypatch.setattr(account, "_value", traced_value)
    result = run([plan], minutes, opens, compress_idle_minutes=True)
    assert result.state.holdings and result.state.book.lots
    assert called == [at()] + [plan.entry_at + offset * MINUTE
                             for offset in range((at(15, 30) - plan.entry_at) // MINUTE + 1)]


@pytest.mark.parametrize("seed", range(8))
def test_idle_compression_deterministic_staggered_random_paths(seed):
    rng = Random(seed)
    mode = ("A", "B", "A+B")[seed % 3]
    strategies = ("A", "B") if mode == "A+B" else (mode,)
    plans = []
    for index in range(16):
        entry = at() + rng.randrange(1, 51) * 5 * MINUTE
        plans.append(candidate(f"invented-{index % 5}", rng.choice(strategies), entry,
                               entry - rng.randrange(1, 5) * MINUTE,
                               rng.choice((1, 90, 99, 100))))
    # Exact duplicate plans are contract errors; preserve overlapping symbols.
    plans = list(dict.fromkeys(plans))
    rng.shuffle(plans)
    minutes, opens = {}, {}
    for symbol in sorted({plan.symbol for plan in plans}):
        for offset in range(391):
            clock = at() + offset * MINUTE
            close = rng.choice((70, 90, 99, 100, 101, 125))
            minutes[symbol, clock] = (100, max(100, close), min(100, close), close)
            opens[symbol, clock] = 100
    first = min(plans, key=lambda item: (item.entry_at, item.decision_at,
                                         item.symbol, item.strategy))
    if seed % 4 == 1:
        opens[first.symbol, first.entry_at] = None
    elif seed % 4 == 2:
        minutes[first.symbol, first.entry_at + 7 * MINUTE] = None
    elif seed % 4 == 3:
        opens[first.symbol, first.cap_exit_at] = None
    assert_idle_compression_equivalent(plans, minutes, opens, mode=mode,
                                      cost_bps=33 if seed % 2 == 0 else 43)


def test_idle_compression_carries_week_month_unknown_state_across_empty_days():
    days = (DAY, DAY + timedelta(days=1), date(2025, 1, 13), date(2025, 2, 3))
    dense_state = compressed_state = account.new_state("A")
    for index, day in enumerate(days):
        plans = [candidate(entry=at(9, 5, day))] if index in (0, 3) else []
        minutes, opens = panel(*plans, exit_price=10)
        if index == 3:
            opens["x", plans[0].entry_at] = None
        dense, compressed, _ = assert_idle_compression_equivalent(
            plans, minutes, opens, state=dense_state, day=day)
        separately_streamed = run(plans, minutes, opens, state=compressed_state,
                                  days=(day,), compress_idle_minutes=True)
        assert separately_streamed == compressed
        dense_state, compressed_state = dense.state, separately_streamed.state
    assert dense_state.unknown_at == at(9, 5, days[-1])


def baseline_value_before_state_copy_optimization(state, clock, prices, phase):
    """Frozen pre-optimization oracle; keep its separate copies/period formatting.

    Copied from account.py SHA256
    fcedc0892d43af77862f403479795cf7d701d7c94120fb939cb674e54614f53f.
    This deliberately duplicates the old value path to catch semantic drift.
    """
    valuation = cash.mark(state.book, prices)
    state = replace(state, book=valuation.book)
    if valuation.full_nav is None:
        return replace(state, unknown_at=state.unknown_at or clock), None, ("account_indeterminate",)
    nav = valuation.full_nav
    day = clock.astimezone(account.KST).date()
    iso = day.isocalendar()
    keys = day.isoformat(), f"{iso.year}-W{iso.week:02d}", day.strftime("%Y-%m")
    updates = {}
    names = ("daily_anchor", "weekly_anchor", "monthly_anchor") \
        if state.risk_scope == "continuous" else ("daily_anchor",)
    for name, key in zip(names, keys):
        previous = getattr(state, name)
        if previous is None or previous.period != key:
            updates[name] = account.PeriodAnchor(key, nav)
    state = replace(state, **updates)
    returns = tuple((name.removesuffix("_anchor"), nav / getattr(state, name).nav - 1)
                    for name in names)
    current = dict(returns)
    limits = (("daily", Fraction(-1, 200)),)
    if state.risk_scope == "continuous":
        limits += (("weekly", Fraction(-3, 200)), ("monthly", Fraction(-3, 100)),
                   ("hard_monthly", Fraction(-1, 25)))
    gates = tuple(name for name, limit in limits
                  if current["monthly" if name == "hard_monthly" else name] <= limit)
    peak = max(state.peak_nav, nav)
    drawdown = 1 - nav / peak
    state = replace(state, peak_nav=peak,
                    maximum_drawdown=max(state.maximum_drawdown, drawdown))
    snapshot = account.NAVSnapshot(clock, phase, nav, state.book.cash, valuation.sleeve_navs,
                                   returns, gates, drawdown)
    return state, snapshot, gates


def valuation_replay_fixture(mode="A+B", case="known"):
    """Small deterministic held-price workload, also usable for CPU measurement."""
    strategy = "B" if mode == "B" else "A"
    other = "B" if mode == "A+B" else strategy
    plans = [candidate("held", strategy, stop=1),
             candidate("duplicate", other, entry=at(9, 15), stop=90),
             candidate("duplicate", strategy, entry=at(9, 20), stop=90),
             candidate("late", strategy, entry=at(11, 5), stop=95),
             candidate("last", other, entry=at(13, 10), stop=1)]
    minutes, opens = panel(*plans)
    for offset in range(60):
        clock = plans[0].entry_at + offset * MINUTE
        close = Fraction(96 + offset % 9)
        minutes["held", clock] = (100, max(100, close), min(100, close), close)
    minutes["late", at(11, 7)] = (100, 100, 90, 90)
    if case == "missing_entry":
        opens["held", plans[0].entry_at] = None
    elif case == "missing_held":
        minutes["held", at(9, 8)] = None
    elif case == "missing_exit":
        opens["held", plans[0].cap_exit_at] = None
    elif case == "risk":
        minutes["held", at(9, 12)] = (100, 100, 40, 40)
    return plans, minutes, opens


@pytest.mark.parametrize("mode", ["A", "B", "A+B"])
@pytest.mark.parametrize("cost", [33, 43])
@pytest.mark.parametrize("scope", ["continuous", "day_local_counterfactual"])
@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("case", ["known", "risk", "missing_entry", "missing_held", "missing_exit"])
def test_optimized_valuation_matches_frozen_full_replay_and_price_reads(
        mode, cost, scope, compressed, case):
    class Traced(dict):
        def __init__(self, values):
            super().__init__(values)
            self.reads = []

        def get(self, key, default=None):
            self.reads.append(key)
            return super().get(key, default)

    plans, minutes, opens = valuation_replay_fixture(mode, case)
    original_minutes, original_opens = Traced(minutes), Traced(opens)
    changed_minutes, changed_opens = Traced(minutes), Traced(opens)
    initial = account.new_state(mode, cost_bps=cost, risk_scope=scope)
    kwargs = dict(mode=mode, cost_bps=cost, state=initial, compress_idle_minutes=compressed)
    with patch.object(account, "_value", baseline_value_before_state_copy_optimization):
        baseline = run(plans, original_minutes, original_opens, **kwargs)
    changed = run(plans, changed_minutes, changed_opens, **kwargs)
    assert changed == baseline  # Full state, every event and every snapshot.
    assert changed_minutes.reads == original_minutes.reads
    assert changed_opens.reads == original_opens.reads


@pytest.mark.parametrize("scope", ["continuous", "day_local_counterfactual"])
def test_optimized_valuation_matches_baseline_across_period_gates_and_unknown(scope):
    state = account.new_state("A", risk_scope=scope)
    # ISO-year, week and month boundaries, exact thresholds, recovery and peaks.
    days = (date(2024, 12, 30), date(2025, 1, 1), date(2025, 1, 6), date(2025, 2, 3))
    for day in days:
        anchor = state.book.cash
        for index, ratio in enumerate((Fraction(1), Fraction(199, 200), Fraction(197, 200),
                                       Fraction(97, 100), Fraction(24, 25), Fraction(1),
                                       Fraction(101, 100))):
            book = replace(state.book, sleeves=(replace(state.book.sleeves[0], cash=anchor * ratio),))
            state = replace(state, book=book)
            clock = at(9, index, day)
            expected = baseline_value_before_state_copy_optimization(state, clock, {}, "before_entries")
            observed = account._value(state, clock, {}, "before_entries")
            assert observed == expected
            state = observed[0]
    state = replace(state, book=cash.freeze(state.book, "invented_unknown"))
    for clock in (at(10, day=days[-1]), at(9, day=date(2025, 3, 3))):
        expected = baseline_value_before_state_copy_optimization(state, clock, {}, "before_entries")
        observed = account._value(state, clock, {}, "before_entries")
        assert observed == expected
        state = observed[0]
    assert state.unknown_at == at(10, day=days[-1])


@pytest.mark.parametrize("clock,expected", [
    (datetime(2024, 12, 29, 15, tzinfo=timezone.utc), ("2024-12-30", "2025-W01", "2024-12")),
    (datetime(2024, 12, 31, 15, tzinfo=timezone.utc), ("2025-01-01", "2025-W01", "2025-01")),
    (datetime(2025, 2, 2, 15, tzinfo=timezone.utc), ("2025-02-03", "2025-W06", "2025-02")),
])
def test_period_key_cache_keeps_kst_and_iso_week_boundaries(clock, expected):
    assert account._periods(clock) == expected
    assert account._periods(clock.astimezone(account.KST)) == expected
