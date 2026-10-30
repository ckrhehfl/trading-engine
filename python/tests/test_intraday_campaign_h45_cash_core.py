"""Invented H4/H5 time-only plans preserve the existing cash/risk contract."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_campaign_account as account
from research import intraday_campaign_cash_book as cash
from research import intraday_campaign_independent as independent


DAY = date(2026, 1, 6)
MINUTE = timedelta(minutes=1)
OPENING = datetime(2026, 1, 6, 9, tzinfo=account.KST)
CLOSING = OPENING + 390 * MINUTE


def plan(strategy="H4", symbol="INVENTED", *, offset=0, closing=CLOSING):
    decision = (OPENING + (92 + offset) * MINUTE if strategy == "H4"
                else closing - 78 * MINUTE)
    entry = OPENING + ((decision - OPENING) // (5 * MINUTE) + 1) * 5 * MINUTE
    return account.CampaignCandidate(strategy, symbol, decision, entry, entry + 60 * MINUTE, None)


def day_input(*plans, closing=CLOSING, price=100, exit_price=100):
    minutes, opens = {}, {}
    for item in plans:
        for offset in range(61):
            at = item.entry_at + offset * MINUTE
            op = exit_price if at == item.cap_exit_at else price
            minutes[item.symbol, at] = (op, op, op, op)
            opens[item.symbol, at] = op
    return {"session": DAY.isoformat(), "session_open": OPENING, "session_close": closing,
            "minute_bars": minutes, "open_prices": opens}


def replay(day, plans, *, mode=None, state=None, cost=33):
    mode = mode or plans[0].strategy
    initial = state or account.new_state(mode, cost_bps=cost)
    result = account.replay_account(plans, day["minute_bars"], day["open_prices"],
        {DAY: day["session_open"]}, {DAY: day["session_close"]}, mode=mode,
        state=initial, cost_bps=cost, compress_idle_minutes=True)
    oracle_state, events, snapshots, _ = independent._reconstruct(
        day, plans, independent._plain(initial), compress_idle_minutes=True)
    independent._equal(result.state, oracle_state, "state")
    independent._equal(result.events, events, "events")
    independent._equal(result.snapshots, snapshots, "snapshots")
    return result


@pytest.mark.parametrize("mode,strategies", [
    ("A", ("A",)), ("B", ("B",)), ("A+B", ("A", "B")),
    ("H4", ("H4",)), ("H5", ("H5",))])
def test_only_explicit_single_modes_extend_existing_allocations(mode, strategies):
    book = cash.new_book(mode)
    assert book.initial_capital == book.cash == 100_000_000
    assert tuple(sleeve.strategy for sleeve in book.sleeves) == strategies
    assert all(sleeve.initial_allocation == Fraction(100_000_000, len(strategies))
               for sleeve in book.sleeves)


@pytest.mark.parametrize("mode", ["H3", "H4+H5", "A+H4", "", None])
def test_no_new_unregistered_or_joint_modes(mode):
    with pytest.raises(ValueError, match="unknown_mode"):
        cash.new_book(mode)


@pytest.mark.parametrize("strategy", ["A", "B"])
@pytest.mark.parametrize("stop", [None, 0, -1, Decimal("NaN"), Decimal("Infinity"), True, 0.5])
def test_original_ab_still_require_positive_exact_stop(strategy, stop):
    with pytest.raises(account.AccountRefusal):
        account.CampaignCandidate(strategy, "INVENTED", OPENING + 4 * MINUTE,
            OPENING + 5 * MINUTE, OPENING + 65 * MINUTE, stop)


@pytest.mark.parametrize("strategy", ["H4", "H5"])
@pytest.mark.parametrize("stop", [0, -1, Fraction(1, 10**30), 99, Decimal("NaN"), True])
def test_h45_cannot_acquire_any_structural_stop(strategy, stop):
    original = plan(strategy)
    with pytest.raises(account.AccountRefusal, match="time_only_stop_required"):
        replace(original, structural_stop=stop)


@pytest.mark.parametrize("strategy", ["H4", "H5"])
@pytest.mark.parametrize("cost", [33, 43])
def test_time_only_holds_through_a_drop_and_charges_original_cost_once(strategy, cost):
    candidate = plan(strategy)
    day = day_input(candidate, price=700_000, exit_price=710_000)
    for offset in range(2, 12):
        day["minute_bars"][candidate.symbol, candidate.entry_at + offset * MINUTE] = (
            700_000, 700_000, 490_000, 490_000)
    result = replay(day, [candidate], cost=cost)
    entries = [event for event in result.events if event.kind == "entry"]
    exits = [event for event in result.events if event.kind == "exit"]
    assert len(entries) == len(exits) == 1
    assert entries[0].quantity == exits[0].quantity == 2
    assert exits[0].at == candidate.cap_exit_at and exits[0].reason == "time_cap"
    assert not any(event.kind == "stop" or event.reason == "invalidated_entry" for event in result.events)
    assert result.state.book.cash == 100_000_000 + 20_000 - Fraction(1_400_000 * cost, 10000)


@pytest.mark.parametrize("strategy", ["H4", "H5"])
@pytest.mark.parametrize("missing", ["entry", "held", "exit"])
def test_original_missing_price_freeze_and_known_exit_rules_survive(strategy, missing):
    candidate = plan(strategy)
    day = day_input(candidate)
    at = {"entry": candidate.entry_at, "held": candidate.entry_at + MINUTE,
          "exit": candidate.cap_exit_at}[missing]
    day["open_prices"].pop((candidate.symbol, at))
    day["minute_bars"].pop((candidate.symbol, at))
    result = replay(day, [candidate])
    assert result.state.book.indeterminate
    entries = [event for event in result.events if event.kind == "entry"]
    if missing == "entry":
        assert entries[0].quantity == 0 and entries[0].reason == "entry_price_unknown"
    elif missing == "held":
        assert result.state.unknown_at == at + 3 * MINUTE
        assert result.state.book.lots == ()  # Known cap exit cannot repair history.
    else:
        assert result.state.unknown_at == at
        assert len(result.state.book.lots) == 1
        assert len([event for event in result.events if event.kind == "exit"]) == 1


@pytest.mark.parametrize("strategy", ["H4", "H5"])
def test_cap_open_does_not_read_its_future_hlc(strategy):
    candidate = plan(strategy)
    day = day_input(candidate, exit_price=120)
    day["minute_bars"][candidate.symbol, candidate.cap_exit_at] = (120, object(), None, object())
    result = replay(day, [candidate])
    assert not result.state.book.indeterminate
    assert result.state.book.cash == 100_000_000 + 400_000 - 6600


@pytest.mark.parametrize("strategy", ["H4", "H5"])
def test_unique_entry_open_survives_unavailable_later_hlc_until_its_due_mark(strategy):
    candidate = plan(strategy)
    day = day_input(candidate)
    day["minute_bars"][candidate.symbol, candidate.entry_at] = None
    result = replay(day, [candidate])
    entry = next(event for event in result.events if event.kind == "entry")
    assert entry.quantity == 20_000
    assert result.state.unknown_at == candidate.entry_at + 3 * MINUTE
    assert result.state.book.lots == ()


@pytest.mark.parametrize("strategy", ["H4", "H5"])
@pytest.mark.parametrize("amount,expected", [
    (99_500_000, ("daily",)), (98_500_000, ("daily", "weekly")),
    (97_000_000, ("daily", "weekly", "monthly")),
    (96_000_000, ("daily", "weekly", "monthly", "hard_monthly"))])
def test_all_original_entry_risk_gates_stay_inclusive(strategy, amount, expected):
    candidate = plan(strategy)
    initial = account.new_state(strategy)
    book = replace(initial.book, sleeves=(replace(initial.book.sleeves[0], cash=Fraction(amount)),))
    anchors = (account.PeriodAnchor(DAY.isoformat(), Fraction(100_000_000)),
               account.PeriodAnchor("2026-W02", Fraction(100_000_000)),
               account.PeriodAnchor("2026-01", Fraction(100_000_000)))
    initial = replace(initial, book=book, daily_anchor=anchors[0],
                      weekly_anchor=anchors[1], monthly_anchor=anchors[2])
    result = replay(day_input(candidate), [candidate], state=initial)
    entry = next(event for event in result.events if event.kind == "entry")
    assert entry.reason == "entry_blocked:" + ",".join(expected)
    assert not result.state.book.lots


def test_h4_gate_blocks_a_later_entry_but_never_forces_existing_exit():
    held, blocked = plan("H4"), plan("H4", "BLOCKED", offset=5)
    day = day_input(held, blocked)
    for offset in range(2, 12):
        day["minute_bars"][held.symbol, held.entry_at + offset * MINUTE] = (100, 100, 70, 70)
    result = replay(day, [held, blocked])
    assert [event.reason for event in result.events if event.kind == "entry"] == [
        "entered", "entry_blocked:daily"]
    assert [(event.at, event.reason) for event in result.events if event.kind == "exit"] == [
        (held.cap_exit_at, "time_cap")]


@pytest.mark.parametrize("strategy", ["H4", "H5"])
def test_duplicate_positions_and_integer_one_share_limit_are_unchanged(strategy):
    candidate = plan(strategy)
    result = replay(day_input(candidate, price=2_000_001, exit_price=2_000_001), [candidate])
    assert next(event for event in result.events if event.kind == "entry").reason == "one_share_exceeds_nav_limit"
    if strategy == "H4":
        later = plan(strategy, offset=5)
        duplicate = replay(day_input(candidate, later), [candidate, later])
        assert [event.reason for event in duplicate.events if event.kind == "entry"] == [
            "entered", "duplicate_position"]


@pytest.mark.parametrize("strategy,minutes", [("H4", -5), ("H4", 150), ("H5", 5), ("H5", -5)])
def test_replay_and_independent_oracle_refuse_changed_original_h45_clocks(strategy, minutes):
    candidate = plan(strategy)
    moved = replace(candidate, decision_at=candidate.decision_at + minutes * MINUTE,
                    entry_at=candidate.entry_at + minutes * MINUTE,
                    cap_exit_at=candidate.cap_exit_at + minutes * MINUTE)
    day = day_input(moved)
    with pytest.raises(account.AccountRefusal):
        account.replay_account([moved], day["minute_bars"], day["open_prices"],
            {DAY: OPENING}, {DAY: CLOSING}, mode=strategy)
    with pytest.raises(independent.VerificationError):
        independent._reconstruct(day, [independent._plain(moved)], independent._fresh(strategy, 33, "continuous"))


@pytest.mark.parametrize("strategy", ["H4", "H5"])
def test_raw_oracle_rejects_stop_injection_without_trusting_candidate_constructor(strategy):
    candidate = plan(strategy)
    raw = independent._plain(candidate)
    raw["structural_stop"] = Fraction(1, 10**30)
    with pytest.raises(independent.VerificationError, match="time_only_stop_required"):
        independent._reconstruct(day_input(candidate), [raw], independent._fresh(strategy, 33, "continuous"))


@pytest.mark.parametrize("strategy", ["A", "B"])
@pytest.mark.parametrize("stop", [None, Fraction(0), Fraction(-1), 0.5])
def test_raw_oracle_cannot_reinterpret_an_invalid_ab_stop_as_time_only(strategy, stop):
    candidate = account.CampaignCandidate(strategy, "INVENTED", OPENING + 4 * MINUTE,
        OPENING + 5 * MINUTE, OPENING + 65 * MINUTE, Fraction(95))
    raw = independent._plain(candidate)
    raw["structural_stop"] = stop
    with pytest.raises(independent.VerificationError, match="positive_structural_stop_required"):
        independent._reconstruct(day_input(candidate), [raw], independent._fresh(strategy, 33, "continuous"))


@pytest.mark.parametrize("offset", [0, 5, 145])
def test_h4_original_available_bar_grid_includes_first_and_last_feasible_signals(offset):
    candidate = plan("H4", offset=offset)
    assert not replay(day_input(candidate), [candidate]).state.book.indeterminate


def test_h4_cannot_shift_its_decision_inside_the_same_entry_bucket():
    candidate = plan("H4")
    changed = replace(candidate, decision_at=candidate.decision_at + MINUTE)
    day = day_input(changed)
    with pytest.raises(account.AccountRefusal, match="original_h4_clock_required"):
        account.replay_account([changed], day["minute_bars"], day["open_prices"],
            {DAY: OPENING}, {DAY: CLOSING}, mode="H4")
    with pytest.raises(independent.VerificationError, match="original_h4_clock_required"):
        independent._reconstruct(day, [independent._plain(changed)], independent._fresh("H4", 33, "continuous"))


def test_h5_fixed_clocks_remain_relative_to_a_short_session():
    closing = OPENING + 330 * MINUTE
    candidate = plan("H5", closing=closing)
    result = replay(day_input(candidate, closing=closing), [candidate])
    assert candidate.decision_at == closing - 78 * MINUTE
    assert candidate.entry_at == closing - 75 * MINUTE
    assert next(event for event in result.events if event.kind == "exit").at == closing - 15 * MINUTE


def test_core_three_finisher_does_not_accept_new_single_modes():
    with pytest.raises(independent.VerificationError, match="unregistered scenario"):
        independent.Verifier().verify_day(day_input={}, candidates=(), initial_state={}, result=None,
            compact={}, scenario={"timing_policy": "D+1", "cost_bps": 33, "mode": "H4", "risk_scope": "continuous"})


def test_ab_structural_stop_semantics_remain_distinct_from_time_only():
    candidate = account.CampaignCandidate("A", "INVENTED", OPENING + 4 * MINUTE,
        OPENING + 5 * MINUTE, OPENING + 65 * MINUTE, Fraction(95))
    day = day_input(candidate)
    day["minute_bars"][candidate.symbol, candidate.entry_at] = (100, 100, 90, 90)
    result = replay(day, [candidate], mode="A")
    assert next(event for event in result.events if event.kind == "stop").at == candidate.entry_at + 3 * MINUTE
    assert next(event for event in result.events if event.kind == "exit").at == candidate.entry_at + 4 * MINUTE
