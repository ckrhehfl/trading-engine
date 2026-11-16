"""Invented matched exits, exact cash, receipt priority and calendar retention."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_exit_followup as followup
from research.intraday_event_attribution_fixtures import case
from research.intraday_event_execution import EntryRequest, Forecast, evaluate
from research.intraday_event_scenarios import MinuteBar


def _quote(bars, offset, *, opening=100, closing=None, receipt=None):
    closing = opening if closing is None else closing
    bars[offset] = MinuteBar(offset, opening, max(opening, closing), min(opening, closing), closing,
                            offset + 1 if receipt is None else receipt)


def _case(*, length=100, mutate=None):
    roster, ledgers, market = case()
    bars = {offset: MinuteBar(offset, 100, 100, 100, 100, offset + 1) for offset in range(length)}
    if mutate is not None:
        mutate(bars)
    market = replace(market, session_length=length, bars=bars)
    for row in ledgers.values():
        clock = row["entry_decision_offset"]
        request = EntryRequest(clock, 100, 2, 98)
        consumed = evaluation._Consumed(bars)
        report = evaluate(request, consumed, length, entry_policy=row["entry_policy"], management=row["management"],
                          forecasts=(Forecast(clock + 11, clock + 11, -1), Forecast(clock + 21, clock + 21, -1)))
        row["execution"] = report
        row["outcome"] = evaluation._outcome(report, request, consumed, length, None, None)
    return roster, ledgers, market


def _book(record, index=1):
    return record["arms"][index]


def test_cost_close_uses_next_open_and_single_exact_cost_not_threshold_fill():
    def path(bars):
        _quote(bars, 20, closing=101)
        _quote(bars, 22, opening=99)
    record = followup.measure_event(*_case(mutate=path))
    book = _book(record)
    quantity = book["filled_quantity"]
    assert (book["trigger_offset"], book["trigger_receipt_offset"], book["exit_offset"]) == (20, 21, 22)
    assert book["exit_reason"] == "cost_close"
    assert Fraction(book["gross_cash"]) == -quantity
    assert Fraction(book["cost_cash"]) == quantity * Fraction(33, 100)
    assert Fraction(book["net_cash"]) == -quantity * Fraction(133, 100)
    assert book["entry_price"] == "100"
    assert book["filled_quantity"] == _book(record, 0)["filled_quantity"] == _book(record, 4)["filled_quantity"]


def test_entry_bar_close_is_eligible_but_exact_cost_equality_is_not():
    record = followup.measure_event(*_case(mutate=lambda bars: _quote(bars, 16, closing=101)))
    assert (_book(record)["trigger_offset"], _book(record)["exit_offset"]) == (16, 18)
    record = followup.measure_event(*_case(mutate=lambda bars: _quote(bars, 20, closing=Fraction(10033, 100))))
    assert _book(record)["trigger_offset"] is None
    assert (_book(record)["exit_reason"], _book(record)["exit_offset"]) == ("time", 46)


def test_structural_stop_precedes_cost_on_different_bars_received_together():
    def path(bars):
        _quote(bars, 19, closing=101, receipt=21)
        _quote(bars, 20, closing=97, receipt=21)
    record = followup.measure_event(*_case(mutate=path))
    assert (_book(record)["exit_reason"], _book(record)["exit_offset"]) == ("structural", 22)
    assert _book(record)["trigger_offset"] is None
    assert _book(record)["net_cash"] == _book(record, 0)["net_cash"]


def test_time_expiry_precedes_cost_sale_and_does_not_extend_old_boundary():
    record = followup.measure_event(*_case(mutate=lambda bars: _quote(bars, 44, closing=101)))
    assert (_book(record)["trigger_offset"], _book(record)["exit_reason"], _book(record)["exit_offset"]) == (44, "time", 46)
    record = followup.measure_event(*_case(mutate=lambda bars: _quote(bars, 45, closing=101)))
    assert _book(record)["trigger_offset"] is None
    assert _book(record)["exit_offset"] == 46


def test_structural_order_already_due_at_expiry_retains_original_reason():
    record = followup.measure_event(*_case(mutate=lambda bars: _quote(bars, 44, closing=97)))
    assert (_book(record)["exit_reason"], _book(record)["exit_offset"]) == ("structural", 46)


@pytest.mark.parametrize("length,eligible", [(79, False), (80, True)])
def test_sixty_minute_gate_is_strict_and_known_before_entry(length, eligible):
    record = followup.measure_event(*_case(length=length))
    gate = record["eligibility"]
    assert gate["eligible"] is eligible
    assert gate["planned_entry_offset"] == 16
    assert gate["excluded_original_fill"] is (not eligible)
    if eligible:
        assert (_book(record, 3)["exit_offset"], _book(record, 4)["exit_offset"]) == (46, 76)
        assert _book(record, 3)["entry_notional"] == _book(record, 4)["entry_notional"]
    else:
        assert all(_book(record, index)["filled_quantity"] == 0 for index in (3, 4))
        assert all(_book(record, index)["net_cash"] == "0" for index in (3, 4))
        assert _book(record, 0)["fill_state"] == "filled"


def test_sixty_minute_gate_does_not_filter_on_future_path_completeness():
    record = followup.measure_event(*_case(mutate=lambda bars: bars.pop(60)))
    assert record["eligibility"]["eligible"] is True
    assert _book(record, 3)["cash_state"] == "settled"
    assert (_book(record, 4)["cash_state"], _book(record, 4)["reason"]) == ("unknown", "held_close_unknown")
    assert _book(record, 4)["filled_quantity"] == _book(record, 3)["filled_quantity"]
    summary = followup.summarize(["2024-01-02"], [record])
    pair = summary["comparisons"]["fixed60_vs_fixed30_time_eligible"]
    assert pair["whole"]["unknown_pairs"] == pair["unknown_patterns"]["right_unknown_only"] == 1


def test_new_exit_can_settle_before_a_gap_without_upgrading_archived_baseline():
    def path(bars):
        _quote(bars, 20, closing=101)
        bars.pop(30)
    record = followup.measure_event(*_case(mutate=path))
    assert _book(record, 0)["cash_state"] == "unknown"
    assert _book(record)["cash_state"] == "settled"
    assert _book(record)["source_cash_state"] == "unknown"
    assert _book(record)["exit_offset"] == 22
    summary = followup.summarize(["2024-01-02"], [record])
    pair = summary["comparisons"]["cost_close_vs_fixed30"]
    assert pair["whole"]["known_pairs"] == 0
    assert pair["unknown_patterns"]["left_unknown_only"] == 1


@pytest.mark.parametrize("missing,reason", [(True, "exit_open_unknown"), (False, "outcome_not_mature_in_session")])
def test_missing_or_unreceived_sale_is_unknown(missing, reason):
    def path(bars):
        _quote(bars, 20, closing=101)
        if missing:
            bars.pop(22)
        else:
            _quote(bars, 22, receipt=100)
    book = _book(followup.measure_event(*_case(mutate=path)))
    assert book["cash_state"] == "unknown"
    assert book["reason"] == reason
    assert book["unknown_offset"] == 22
    assert book["trigger_offset"] == 20
    assert book["exit_offset"] is None and book["net_cash"] is None


@pytest.mark.parametrize("kind", ["gap", "late", "reversed"])
def test_unknown_received_prefix_cannot_be_skipped_for_profitable_close(kind):
    def path(bars):
        _quote(bars, 20, closing=101)
        if kind == "gap":
            bars.pop(18)
        elif kind == "late":
            _quote(bars, 18, receipt=22)
            _quote(bars, 19, receipt=22)
            _quote(bars, 20, closing=101, receipt=23)
        else:
            _quote(bars, 19, receipt=22)
    book = _book(followup.measure_event(*_case(mutate=path)))
    assert book["cash_state"] == "unknown"
    assert book["trigger_offset"] is None
    assert book["reason"] == ("nonchronological_close_availability" if kind == "reversed" else "held_close_unknown")


def test_unknown_original_fill_is_never_promoted_by_future_observations():
    roster, ledgers, market = case(11)
    market = replace(market, session_length=100)
    record = followup.measure_event(roster, ledgers, market)
    assert record["eligibility"]["eligible"] is True
    for book in record["arms"]:
        assert book["fill_state"] == "unknown"
        assert book["cash_state"] == "unknown"
        assert book["filled_quantity"] is None


def test_original_cash_maturity_is_retained_and_new_path_has_own_maturity():
    record = followup.measure_event(*case(6))
    assert _book(record, 0)["cash_state"] == _book(record, 2)["cash_state"] == "unknown"
    assert _book(record)["cash_state"] == "settled"
    assert _book(record)["source_cash_state"] == "unknown"


def test_no_commit_and_known_no_fill_keep_zero_cash_without_a_market():
    roster, ledgers, _ = case(9)
    record = followup.measure_event(roster, ledgers, None)
    assert record["eligibility"]["reason"] == "no_original_request"
    assert all(book["cash_state"] == "known_zero" for book in record["arms"])
    record = followup.measure_event(*case(3))
    assert all(book["fill_state"] == "known_no_fill" for book in record["arms"])


def test_actual_three_minute_receipt_assumption_and_invalid_delay_rejection():
    def path(bars):
        for offset, bar in list(bars.items()):
            bars[offset] = replace(bar, available_offset=offset + 3)
        _quote(bars, 20, closing=101, receipt=23)
    record = followup.measure_event(*_case(mutate=path), minimum_receipt_lag=3)
    assert (_book(record)["trigger_receipt_offset"], _book(record)["exit_offset"]) == (23, 24)
    with pytest.raises(ValueError, match="minimum receipt lag"):
        followup.measure_event(*_case(), minimum_receipt_lag=3)


def test_unmatched_entry_and_changed_archived_fixed_cash_are_rejected():
    roster, ledgers, market = _case()
    broken = deepcopy(ledgers)
    broken[followup.SOURCE_ARMS[1]]["execution"]["guard_cap"] = "103"
    with pytest.raises(ValueError, match="Management changed"):
        followup.measure_event(roster, broken, market)
    bars = dict(market.bars)
    _quote(bars, 46, opening=101)
    with pytest.raises(ValueError, match="fixed exit differs"):
        followup.measure_event(roster, ledgers, replace(market, bars=bars))


def test_summary_keeps_zero_weeks_all_fills_notional_and_three_matched_comparisons():
    record = followup.measure_event(*_case())
    summary = followup.summarize(["2024-01-02", "2024-01-15"], [record])
    name = followup.ARM_NAMES[0]
    totals = summary["arms"][name]
    assert summary["calendar_sessions"] == summary["calendar_weeks"] == 2
    assert totals["fills"] == totals["settled_fills"] == 1
    assert totals["wins"] == 0
    assert totals["entry_notional"] == totals["all_fill_entry_notional"] == _book(record, 0)["entry_notional"]
    assert summary["weekly"]["2024-W01"][name] == {"fills": 1, "wins": 0, "unknown_fills": 0}
    assert summary["weekly"]["2024-W03"][name] == {"fills": 0, "wins": 0, "unknown_fills": 0}
    assert summary["concentration"]["dates"]["2024-01-15"][name]["opportunities"] == 0
    assert set(summary["comparisons"]) == set(followup.COMPARISONS)
    assert summary["exit_reasons"][followup.ARM_NAMES[4]] == {"time": 1}


def test_summary_rejects_duplicate_off_calendar_and_missing_arm_records():
    record = followup.measure_event(*_case())
    with pytest.raises(ValueError, match="Duplicate"):
        followup.summarize(["2024-01-02"], [record, record])
    with pytest.raises(ValueError, match="off-calendar"):
        followup.summarize(["2024-01-03"], [record])
    record["arms"][0] = record["arms"][1]
    with pytest.raises(ValueError, match="Duplicate or absent arm"):
        followup.summarize(["2024-01-02"], [record])


def test_summary_distinguishes_cost_triggers_from_settled_exit_reasons():
    def missing_open(bars):
        _quote(bars, 20, closing=101)
        bars.pop(22)

    changes = (lambda bars: _quote(bars, 20, closing=101),
               lambda bars: _quote(bars, 44, closing=101), missing_open,
               lambda bars: _quote(bars, 20, closing=Fraction(10033, 100)))
    records = []
    for index, change in enumerate(changes):
        record = followup.measure_event(*_case(mutate=change))
        record["roster"]["event_id"] += f":trigger-test-{index}"
        records.append(record)
    summary = followup.summarize(["2024-01-02"], records)
    assert summary["cost_trigger_counts"] == {
        "triggered": 3, "settled_cost_exit": 1, "triggered_time_exit": 1, "triggered_unknown_cash": 1}
