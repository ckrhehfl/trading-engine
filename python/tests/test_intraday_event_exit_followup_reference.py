"""Invented independent exit-clock, maturity and paired-cash checks."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_event_attribution_fixtures import case
from research.intraday_event_execution import EntryRequest, Forecast, evaluate
from research.intraday_event_exit_followup import ARM_NAMES, SOURCE_ARMS, measure_event, summarize
from research.intraday_event_exit_followup_reference import verify_event, verify_summary
from research.intraday_event_scenarios import MinuteBar


def _case(index=0, *, length=100, changes=None, missing=(), lag=1):
    roster, ledgers, market = case(index)
    bars = {offset: MinuteBar(offset, 100, 100, 100, 100, offset + lag) for offset in range(70, length)}
    bars.update({offset: replace(bar, available_offset=offset + lag) for offset, bar in market.bars.items()
                 if offset < length})
    for offset, fields in (changes or {}).items():
        bars[offset] = replace(bars[offset], **fields)
    for offset in missing:
        bars.pop(offset, None)
    market = replace(market, session_length=length, bars=bars)
    for arm in SOURCE_ARMS:
        row = ledgers[arm]
        if row["action"] == "no_commit":
            continue
        request = EntryRequest(15, 100, 2, 97 if index % 12 == 8 else 98)
        report = evaluate(request, bars, length, entry_policy=arm[1], management=arm[2],
                          forecasts=(Forecast(26, 26, -1), Forecast(36, 36, -1)))
        row["execution"] = report
        row["outcome"].update({field: report[field] for field in
                               ("status", "reason", "net", "gross", "filled_quantity", "entry_notional")})
        if index % 12 == 6:
            row["outcome"].update(status="unknown", reason="outcome_not_mature_in_session", net=None, gross=None)
        if index % 12 == 11:
            row["outcome"].update(status="unknown", reason="outcome_not_mature_in_session", net=None, gross=None,
                                  filled_quantity=None, entry_notional=None)
    return roster, ledgers, market


def _verified(values, *, lag=1):
    saved = measure_event(*values, minimum_receipt_lag=lag)
    return verify_event(*values, saved, minimum_receipt_lag=lag)


@pytest.mark.parametrize("regime", range(12))
@pytest.mark.parametrize("length", (70, 100))
def test_all_invented_archival_regimes_have_exact_independent_reconstruction(regime, length):
    _verified(_case(regime, length=length))


def test_trigger_uses_received_close_then_next_open_and_charges_cost_once():
    values = _case(changes={20: dict(high=102, close=102), 22: dict(open=101, high=101)})
    saved = _verified(values)
    book = saved["arms"][1]
    assert (book["trigger_offset"], book["trigger_receipt_offset"], book["exit_offset"]) == (20, 21, 22)
    assert book["exit_reason"] == "cost_close"
    quantity = book["filled_quantity"]
    assert Fraction(book["gross_cash"]) == quantity
    assert Fraction(book["cost_cash"]) == quantity * 100 * Fraction(33, 10_000)
    assert Fraction(book["net_cash"]) == quantity * (1 - 100 * Fraction(33, 10_000))
    changed = deepcopy(saved)
    changed["arms"][1]["gross_cash"] = str(quantity * 2)
    with pytest.raises(ValueError, match="event reconstruction"):
        verify_event(*values, changed)


def test_exact_equality_is_not_a_trigger_and_entry_bar_close_can_trigger():
    equal = _verified(_case(10))["arms"][1]
    assert equal["trigger_offset"] is None and equal["exit_offset"] == 46
    early = _verified(_case(changes={16: dict(high=101, close=101)}))["arms"][1]
    assert (early["trigger_offset"], early["trigger_receipt_offset"], early["exit_offset"]) == (16, 17, 18)


def test_same_receipt_structural_failure_overrides_cost_crossing():
    saved = _verified(_case(changes={20: dict(high=101, close=101, available_offset=23),
                                    21: dict(low=97, close=97, available_offset=23)}))
    book = saved["arms"][1]
    assert book["exit_reason"] == "structural" and book["exit_offset"] == 24
    assert book["trigger_offset"] is None


def test_time_expiry_has_priority_over_an_already_scheduled_cost_exit():
    book = _verified(_case(changes={44: dict(high=101, close=101)}))["arms"][1]
    assert book["trigger_receipt_offset"] == 45
    assert book["exit_offset"] == 46 and book["exit_reason"] == "time"


def test_known_early_exit_does_not_promote_unknown_original_cash_or_pair():
    saved = _verified(_case(changes={20: dict(high=101, close=101)}, missing=(25,)))
    old, new, _, _, longer = saved["arms"]
    assert old["cash_state"] == "unknown" and old["net_cash"] is None
    assert new["cash_state"] == "settled" and new["exit_offset"] == 22
    assert longer["cash_state"] == "unknown"
    assert saved["eligibility"]["eligible"] is True
    report = summarize(["2024-01-02", "2024-01-09"], [saved])
    verify_summary(["2024-01-02", "2024-01-09"], [saved], report)
    pair = report["comparisons"]["cost_close_vs_fixed30"]
    assert pair["whole"]["known_pairs"] == 0 and pair["whole"]["unknown_pairs"] == 1
    assert pair["unknown_patterns"]["left_unknown_only"] == 1
    assert pair["common_settled"]["known_pairs"] == 0


@pytest.mark.parametrize("offset,fields,missing,reason,clock", [
    (20, dict(high=101, close=101), (22,), "exit_open_unknown", 22),
    (20, dict(available_offset=24), (), "nonchronological_close_availability", 22),
    (20, dict(available_offset=24), (21, 22), "held_close_unknown", 23),
])
def test_gap_and_receipt_failures_remain_unknown(offset, fields, missing, reason, clock):
    book = _verified(_case(changes={offset: fields}, missing=missing))["arms"][1]
    assert book["cash_state"] == "unknown" and book["net_cash"] is None
    assert book["reason"] == reason and book["unknown_offset"] == clock


def test_sale_receipt_at_session_end_is_unknown_even_with_visible_open():
    book = _verified(_case(changes={20: dict(high=101, close=101),
                                   22: dict(available_offset=100)}))["arms"][1]
    assert book["reason"] == "outcome_not_mature_in_session"
    assert book["cash_state"] == "unknown" and book["exit_offset"] is None


def test_unknown_original_fill_stays_unknown_despite_a_complete_new_path():
    saved = _verified(_case(11))
    assert all(saved["arms"][position]["fill_state"] == "unknown" for position in (0, 1, 3, 4))
    assert all(saved["arms"][position]["net_cash"] is None for position in (0, 1, 3, 4))


@pytest.mark.parametrize("length,eligible", [(79, False), (80, True)])
def test_sixty_minute_subset_uses_strict_predetermined_calendar_gate(length, eligible):
    saved = _verified(_case(length=length))
    assert saved["eligibility"]["planned_entry_offset"] == 16
    assert saved["eligibility"]["eligible"] is eligible
    assert saved["eligibility"]["excluded_original_fill"] is not eligible
    assert saved["arms"][3]["fill_state"] == ("filled" if eligible else "known_no_fill")
    assert saved["arms"][4]["exit_offset"] == (76 if eligible else None)


def test_three_minute_source_receipt_does_not_demand_future_gap_before_sale():
    saved = _verified(_case(changes={20: dict(high=101, close=101)}, missing=(22,), lag=3), lag=3)
    assert saved["arms"][0]["cash_state"] == "unknown"
    new = saved["arms"][1]
    assert (new["trigger_offset"], new["trigger_receipt_offset"], new["exit_offset"]) == (20, 23, 24)
    assert new["cash_state"] == "settled"


def test_summary_reconstructs_all_fills_costs_exclusions_and_zero_weeks():
    records = [_verified(_case(index, length=70 if index == 0 else 100)) for index in (0, 1, 3, 4, 6, 9, 11)]
    calendar = ["2024-01-02", "2024-01-09", "2024-01-16"]
    report = summarize(calendar, records)
    assert verify_summary(calendar, records, report) == report
    assert report["weekly"]["2024-W03"][ARM_NAMES[0]] == dict(fills=0, wins=0, unknown_fills=0)
    assert report["concentration"]["dates"][calendar[-1]][ARM_NAMES[0]]["opportunities"] == 0
    assert report["eligibility"]["excluded_original_fills"] == 1
    totals = report["arms"][ARM_NAMES[0]]
    assert totals["fills"] == 3 and totals["settled_fills"] == 2
    assert Fraction(totals["all_fill_entry_notional"]) > Fraction(totals["entry_notional"])
    changed = deepcopy(report)
    changed["comparisons"]["cost_close_vs_fixed30"]["whole"]["delta_net_cash"] = "1000000"
    with pytest.raises(ValueError, match="summary reconstruction"):
        verify_summary(calendar, records, changed)


@pytest.mark.parametrize("mutation", ("exit", "quantity", "gate", "cost", "trigger"))
def test_saved_followup_tampering_is_rejected(mutation):
    values = _case(1)
    saved = _verified(values)
    if mutation == "gate":
        saved["eligibility"]["eligible"] = False
    else:
        field, value = {"exit": ("exit_offset", 21), "quantity": ("filled_quantity", 1),
                        "cost": ("cost_cash", "0"), "trigger": ("trigger_receipt_offset", 20)}[mutation]
        saved["arms"][1][field] = value
    with pytest.raises(ValueError, match="event reconstruction"):
        verify_event(*values, saved)


def test_reference_reprices_independently_even_if_archival_sale_cash_is_self_consistent():
    roster, ledgers, market = _case()
    saved = _verified((roster, ledgers, market))
    bars = dict(market.bars)
    bars[27] = replace(bars[27], open=101, high=101)
    with pytest.raises(ValueError, match="independent minute opening"):
        verify_event(roster, ledgers, replace(market, bars=bars), saved)


def test_reference_rejects_missing_arms_and_duplicate_original_episodes():
    saved = _verified(_case())
    report = summarize(["2024-01-02"], [saved])
    with pytest.raises(ValueError, match="duplicate or off-calendar"):
        verify_summary(["2024-01-02"], [saved, saved], report)
    saved["arms"][0] = saved["arms"][1]
    with pytest.raises(ValueError, match="duplicate or absent arm"):
        verify_summary(["2024-01-02"], [saved], report)
