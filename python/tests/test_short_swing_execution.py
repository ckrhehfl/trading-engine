"""Invented multi-session prices only; no original inputs or network."""

from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_execution as original
from research import short_swing_execution as swing
from research.intraday_event_scenarios import MinuteBar


CALENDAR = ("2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25")
SYMBOL = "invented"


def tape(day, price=100):
    return swing.SessionTape(day, SYMBOL, 100,
        {i: MinuteBar(i, price, price + 2, price - 2, price, i + 3) for i in range(90)}, "same-raw-unit")


def request(level=99, family="pullback"):
    return swing.SwingRequest(CALENDAR[0], SYMBOL, original.EntryRequest(5, 100, 2, 95), level, 5, family)


def market():
    return {(day, SYMBOL): tape(day) for day in CALENDAR}


def supports():
    return tuple(swing.CarrySupport(SYMBOL, left, right, True, True, left, 0)
                 for left, right in zip(CALENDAR, CALENDAR[1:]))


def run(policy, markets=None, req=None, bridges=None):
    return swing.evaluate(req or request(), market() if markets is None else markets,
                          CALENDAR, policy=policy, carry_support=supports() if bridges is None else bridges)


def test_original_fixed30_and_same_entry_all_arms():
    markets = market()
    result = swing.compare(request(), markets, CALENDAR, carry_support=supports(), include_third_session=True)
    old = original.evaluate(request().entry, markets[CALENDAR[0], SYMBOL].bars, 90,
                            entry_policy="reference_limit_one_shot")
    assert result["fixed30"]["original_fixed30"] == old
    assert len({(r["requested_quantity"], r["filled_quantity"], r["entry_price"], r["entry_cost"])
                for r in result.values()}) == 1
    for policy, expected_day in (("session_end", CALENDAR[0]), ("next_session_end", CALENDAR[1]),
                                 ("conditional_next", CALENDAR[1]), ("third_session_end", CALENDAR[2])):
        sale = result[policy]["sales"][0]
        assert (sale["session"], sale["decision_offset"], sale["execute_offset"]) == (expected_day, 85, 86)
        assert Fraction(result[policy]["net"]) == -Fraction(result[policy]["entry_cost"])


def test_stop_is_received_close_then_next_open_and_precedes_late_decision():
    markets = market()
    bars = markets[CALENDAR[0], SYMBOL].bars
    bars[82] = MinuteBar(82, 100, 102, 94, 94, 85)
    bars[86] = MinuteBar(86, 92, 94, 90, 92, 89)
    result = run("conditional_next", markets, bridges=())
    assert result["reason"] == "structural"
    assert result["sales"][0]["price"] == "92"
    assert result["carry_decisions"] == []
    assert result["sales"][0]["decision_offset"] == 85


def test_condition_uses_last_fully_known_aligned_five_minute_close_strictly():
    markets = market()
    bars = markets[CALENDAR[0], SYMBOL].bars
    bars[79] = MinuteBar(79, 100, 101, 98, 99, 82)
    bars[84] = MinuteBar(84, 100, 110, 99, 109, 87)
    result = run("conditional_next", markets, bridges=())
    assert result["reason"] == "conditional_flat"
    assert result["carry_decisions"] == [{"session": CALENDAR[0], "decision_offset": 85,
        "block_start": 75, "block_end": 80, "close": "99", "level": "99", "carry": False}]
    assert result["sales"][0]["execute_offset"] == 86
    assert run("conditional_next", req=request(family="reversal"))["level_source"] == "second_bearish_high"


def test_gap_stop_learned_next_day_cannot_fill_at_that_days_past_open():
    markets = market()
    bars = markets[CALENDAR[1], SYMBOL].bars
    bars[0] = MinuteBar(0, 90, 92, 88, 90, 3)
    bars[4] = MinuteBar(4, 89, 92, 88, 90, 7)
    result = run("next_session_end", markets)
    sale = result["sales"][0]
    assert (sale["decision_session"], sale["decision_offset"], sale["execute_offset"], sale["price"]) == (
        CALENDAR[1], 3, 4, "89")


def test_previous_day_received_stop_waits_for_first_next_regular_open():
    markets = market()
    bars = markets[CALENDAR[0], SYMBOL].bars
    bars[87] = MinuteBar(87, 100, 102, 93, 94, 90)
    markets[CALENDAR[1], SYMBOL].bars[0] = MinuteBar(0, 90, 92, 88, 90, 3)
    result = run("next_session_end", markets)
    sale = result["sales"][0]
    assert (sale["decision_session"], sale["decision_offset"], sale["session"], sale["execute_offset"]) == (
        CALENDAR[0], 90, CALENDAR[1], 0)
    assert sale["price"] == "90"


@pytest.mark.parametrize("offset", [8, 89])
def test_held_missing_minute_liveness_including_session_tail(offset):
    markets = market()
    del markets[CALENDAR[0], SYMBOL].bars[offset]
    result = run("next_session_end", markets)
    assert result["reason"] == "held_close_unknown"
    assert result["unknown_offset"] == offset + 3
    assert result["net"] is None


def test_late_and_reversed_receipts_are_unknown():
    markets = market()
    bars = markets[CALENDAR[0], SYMBOL].bars
    bars[6] = replace(bars[6], available_offset=12)
    assert run("next_session_end", markets)["reason"] == "held_close_unknown"
    bars[6] = replace(bars[6], available_offset=9)
    bars[7] = replace(bars[7], available_offset=8)
    assert run("next_session_end", markets)["reason"] == "nonchronological_close_availability"


def test_unknown_bridge_does_not_read_next_session_prices_or_mask_day_sales():
    class Unreadable(Mapping):
        def __getitem__(self, key):
            raise AssertionError("Next day price was consulted")
        def __iter__(self):
            raise AssertionError("Next day price was iterated")
        def __len__(self):
            raise AssertionError("Next day price was sized")
    markets = market()
    markets[CALENDAR[1], SYMBOL] = replace(markets[CALENDAR[1], SYMBOL], bars=Unreadable())
    assert run("next_session_end", markets, bridges=())["reason"] == "carry_units_or_rights_unknown"
    assert run("session_end", markets, bridges=())["status"] == "closed"
    assert run("conditional_next", markets, req=request(100), bridges=())["reason"] == "conditional_flat"


@pytest.mark.parametrize("change", [{"comparison_level": None}, {"level_known_offset": None},
                                     {"comparison_level": None, "level_known_offset": None}])
def test_unknown_level_only_blocks_conditional_decision_not_other_arms_or_earlier_stop(change):
    req = replace(request(), **change)
    results = swing.compare(req, market(), CALENDAR, carry_support=supports(), include_third_session=True)
    assert all(row["status"] == "closed" for policy, row in results.items() if policy != "conditional_next")
    assert results["conditional_next"]["reason"] == "conditional_comparison_level_unknown"
    assert results["conditional_next"]["unknown_offset"] == 85
    markets = market()
    markets[CALENDAR[0], SYMBOL].bars[8] = MinuteBar(8, 100, 102, 93, 94, 11)
    stopped = run("conditional_next", markets, req, bridges=())
    assert stopped["reason"] == "structural"
    assert stopped["sales"][0]["execute_offset"] == 12


def test_missing_entry_tape_keeps_unknown_cost_and_notional():
    result = run("session_end", {})
    assert result["filled_quantity"] is result["entry_notional"] is result["entry_cost"] is None
    assert result["net"] is None


def test_zero_requested_quantity_is_known_without_any_entry_tape_lookup():
    class UnreadableSessions(dict):
        def get(self, key, default=None):
            raise AssertionError("A zero-quantity request must not consult its tape")
    req = replace(request(), entry=original.EntryRequest(5, 3_000_000, 100, 2_999_000))
    for markets in ({}, UnreadableSessions()):
        results = swing.compare(req, markets, CALENDAR, include_third_session=True)
        for result in results.values():
            assert result["status"] == "no_entry"
            assert result["reason"] == "insufficient_risk_or_cash_for_one_unit"
            assert result["requested_quantity"] == result["filled_quantity"] == 0
            assert result["entry_notional"] == result["entry_cost"] == result["net"] == "0"
            assert result["unknown_session"] is result["unknown_offset"] is None


@pytest.mark.parametrize("change,reason", [
    ({"price_units_verified": None}, "carry_units_or_rights_unknown"),
    ({"rights_unchanged": False}, "carry_units_or_rights_unknown"),
    ({"available_session": CALENDAR[1], "available_offset": 1}, "carry_units_or_rights_unknown"),
])
def test_unknown_changed_or_late_rights_never_bridge(change, reason):
    assert run("next_session_end", bridges=(replace(supports()[0], **change),))["reason"] == reason


def test_different_basis_and_absent_true_next_day_do_not_skip_to_later_data():
    markets = market()
    markets[CALENDAR[1], SYMBOL] = replace(markets[CALENDAR[1], SYMBOL], price_basis="adjusted-unit")
    assert run("next_session_end", markets)["reason"] == "carry_price_basis_unknown"
    del markets[CALENDAR[1], SYMBOL]
    result = run("next_session_end", markets)
    assert (result["reason"], result["unknown_session"]) == ("next_session_unknown", CALENDAR[1])


def test_unmatured_sale_and_missing_next_open_remain_unknown():
    markets = market()
    bars = markets[CALENDAR[1], SYMBOL].bars
    bars[86] = replace(bars[86], available_offset=90)
    assert run("next_session_end", markets)["reason"] == "outcome_not_mature_in_session"
    del bars[86]
    assert run("next_session_end", markets)["reason"] == "exit_open_unknown"


def test_original_missing_exit_observation_preserves_its_receipt_maturity_boundary():
    markets = market()
    req = replace(request(), entry=original.EntryRequest(58, 100, 2, 95))
    del markets[CALENDAR[0], SYMBOL].bars[89]
    result = run("fixed30", markets, req)
    assert result["original_fixed30"]["reason"] == "exit_open_unknown"
    assert result["reason"] == "outcome_not_mature_in_session"
    assert result["net"] is None


def test_immature_fixed30_sale_redacts_unsettled_top_level_cash_but_keeps_original_diagnostic():
    markets = market()
    bars = markets[CALENDAR[0], SYMBOL].bars
    bars[36] = replace(bars[36], available_offset=90)
    result = run("fixed30", markets)
    assert result["reason"] == "outcome_not_mature_in_session"
    assert result["gross"] is result["cost"] is result["net"] is None
    assert all(result["sales"][0][field] is None for field in ("price", "gross", "allocated_cost", "net"))
    assert result["original_fixed30"]["sales"][0]["price"] == "100"
    assert result["original_fixed30"]["net"] is not None


def test_original_entry_limit_quantity_and_clock_gate_remain_fixed():
    markets = market()
    bars = markets[CALENDAR[0], SYMBOL].bars
    bars[6] = MinuteBar(6, 101, 105, 90, 100, 9)
    result = swing.compare(request(), markets, CALENDAR)
    assert all(row["status"] == "no_fill" for row in result.values())
    late = replace(request(), entry=original.EntryRequest(60, 100, 2, 95))
    assert run("next_session_end", {}, late)["reason"] == "entry_session_unknown"
    assert run("next_session_end", markets, late)["reason"] == "expiry_not_inside_session"


def test_high_low_and_post_exit_changes_do_not_change_execution():
    markets = market()
    expected = run("session_end", markets)
    bars = markets[CALENDAR[0], SYMBOL].bars
    for i in bars:
        bars[i] = replace(bars[i], high=1000, low=1)
    del bars[87]
    assert run("session_end", markets) == expected


def test_future_level_refuses():
    with pytest.raises(ValueError, match="known"):
        replace(request(), level_known_offset=6)


@pytest.mark.parametrize("rights", [True, False])
def test_duplicate_or_conflicting_bridge_is_unknown_only_if_that_crossing_is_used(rights):
    bridges = (supports()[0], replace(supports()[0], rights_unchanged=rights))
    assert run("fixed30", bridges=bridges)["status"] == "closed"
    assert run("session_end", bridges=bridges)["status"] == "closed"
    assert run("conditional_next", req=request(100), bridges=bridges)["reason"] == "conditional_flat"
    carried = run("next_session_end", bridges=bridges)
    assert carried["reason"] == "carry_support_ambiguous"
    assert carried["unknown_session"] == CALENDAR[1]
    markets = market()
    markets[CALENDAR[0], SYMBOL].bars[8] = MinuteBar(8, 100, 102, 93, 94, 11)
    assert run("next_session_end", markets, bridges=bridges)["reason"] == "structural"


def test_ambiguous_second_crossing_does_not_invalidate_first_overnight_exit():
    bridges = (supports()[0], supports()[1], supports()[1])
    assert run("next_session_end", bridges=bridges)["status"] == "closed"
    result = run("third_session_end", bridges=bridges)
    assert result["reason"] == "carry_support_ambiguous"
    assert result["unknown_session"] == CALENDAR[2]
