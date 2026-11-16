"""Invented EM accounting, missingness and receipt-clock witnesses."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_attribution as attribution
from research.intraday_event_attribution_fixtures import case, invented_cases


def measured(index=0):
    return attribution.measure_event(*case(index))


def book(record, arm=attribution.PATH_ARM):
    return record["arms"][attribution.ARM_KEYS.index(arm)]


def test_cash_reconstructed_with_single_cost_across_partial_sales():
    roster, ledgers, market = case(8)
    record = attribution.measure_event(roster, ledgers, market)
    arm = ("confirmation", "reference_limit_one_shot", "forecast_half_then_exit")
    original = ledgers[arm]["execution"]
    assert len(original["sales"]) == 2
    quantity = original["filled_quantity"]
    assert quantity % 2 == 1
    assert original["sales"][1]["quantity"] == original["sales"][0]["quantity"] + 1
    expected_cost = quantity * Fraction(original["entry_price"]) * Fraction(33, 10_000)
    assert Fraction(book(record, arm)["cost_cash"]) == expected_cost
    assert sum(sale["quantity"] for sale in original["sales"]) == quantity
    corrupted = deepcopy(ledgers)
    corrupted[arm]["execution"]["sales"][0]["allocated_cost"] = "0"
    with pytest.raises(ValueError, match="Sale cash reconstruction"):
        attribution.measure_event(roster, corrupted, market)


def test_maturity_unknown_not_upgraded_by_closed_execution():
    roster, ledgers, market = case(6)
    assert ledgers[attribution.PATH_ARM]["execution"]["status"] == "closed"
    record = attribution.measure_event(roster, ledgers, market)
    assert book(record)["fill_state"] == "filled"
    assert book(record)["cash_state"] == "unknown"
    assert book(record)["net_cash"] is None
    assert record["path"]["final_sale_return"] is None
    assert record["path"]["category"] == "exceeded_final_unknown"


@pytest.mark.parametrize("index,fill,cash", [(3, "known_no_fill", "known_zero"),
                                            (4, "unknown", "unknown"),
                                            (9, "known_no_fill", "known_zero"),
                                            (11, "unknown", "unknown")])
def test_known_no_fill_unknown_and_no_commit_stay_separate(index, fill, cash):
    record = measured(index)
    assert book(record)["fill_state"] == fill
    assert book(record)["cash_state"] == cash
    assert book(record)["committed"] is (index != 9)
    assert record["path"]["status"] == "not_filled"


def test_complete_hindsight_extrema_and_cost_equality():
    assert measured(0)["path"]["category"] == "never_exceeded"
    path = measured(1)["path"]
    assert (path["max_open_return"], path["max_open_offset"]) == ("1/100", 20)
    assert (path["min_open_return"], path["min_open_offset"]) == ("0", 17)
    assert path["category"] == "exceeded_then_lost"
    assert measured(2)["path"]["category"] == "exceeded_retained"
    equal = measured(10)["path"]
    assert equal["category"] == "equal_only"
    assert equal["ever_cost_exceeded"] is False
    assert equal["first_cost_close"] is None


def test_close_trigger_uses_receipt_plus_one_open_not_close():
    path = measured(1)["path"]
    witness = path["first_cost_close"]
    assert witness == {"offset": 20, "receipt_offset": 21, "close": "101", "first_is_known": True,
                       "opening_offset": 22, "opening_status": "known", "opening_price": "100", "opening_return": "0"}


def test_late_received_witness_cannot_extend_structural_exit():
    path = measured(7)["path"]
    assert path["exit_offset"] == 21
    assert path["first_cost_close"]["receipt_offset"] == 25
    assert path["first_cost_close"]["opening_offset"] == 26
    assert path["first_cost_close"]["opening_status"] == "out_of_window"
    assert path["first_cost_close"]["opening_price"] is None


def test_gap_preserves_observed_lower_bound_but_not_complete_maximum():
    roster, ledgers, market = case(1)
    bars = dict(market.bars)
    del bars[18]
    path = attribution.measure_event(roster, ledgers, replace(market, bars=bars))["path"]
    assert path["status"] == "incomplete"
    assert path["missing_offsets"] == [18]
    assert path["observed_cost_exceeded"] is True
    assert path["ever_cost_exceeded"] is True
    assert path["max_open_return"] is None
    assert path["category"] == "unknown_path"
    assert path["first_cost_close"]["first_is_known"] is False
    bars[20] = replace(bars[20], open=100, close=100, low=100)
    path = attribution.measure_event(roster, ledgers, replace(market, bars=bars))["path"]
    assert path["ever_cost_exceeded"] is None
    assert path["observed_cost_exceeded"] is False


def test_trigger_opening_missing_is_explicit():
    roster, ledgers, market = case(1)
    bars = dict(market.bars)
    del bars[22]
    witness = attribution.measure_event(roster, ledgers, replace(market, bars=bars))["path"]["first_cost_close"]
    assert witness["opening_status"] == "missing"
    assert witness["opening_return"] is None
    assert witness["first_is_known"] is True  # The later opening gap cannot hide an earlier received close.


def test_entry_bar_close_is_a_post_purchase_observation():
    roster, ledgers, market = case(1)
    bars = dict(market.bars)
    bars[16] = replace(bars[16], close=101, high=101)
    witness = attribution.measure_event(roster, ledgers, replace(market, bars=bars))["path"]["first_cost_close"]
    assert (witness["offset"], witness["receipt_offset"], witness["opening_offset"]) == (16, 17, 18)
    assert witness["opening_price"] == "100"


def test_unreceived_price_is_missing_and_later_than_boundary_cannot_change_path():
    roster, ledgers, market = case(1)
    bars = dict(market.bars)
    bars[18] = replace(bars[18], available_offset=70)
    bars[47] = replace(bars[47], open=1000, high=1000)
    path = attribution.measure_event(roster, ledgers, replace(market, bars=bars))["path"]
    assert path["missing_offsets"] == [18]
    assert path["exit_offset"] == 46
    assert path["observed_openings"] == 29


def test_unresolved_execution_does_not_invent_a_fixed_exit():
    record = measured(5)
    assert book(record)["fill_state"] == "filled"
    assert record["path"]["status"] == "unknown_boundary"
    assert record["path"]["exit_offset"] is None


@pytest.mark.parametrize("mutation", ["identity", "clock", "reference", "management", "quantity", "cash"])
def test_inconsistent_original_identity_or_accounting_is_rejected(mutation):
    roster, ledgers, market = case(1)
    arm = attribution.PATH_ARM
    if mutation == "identity":
        ledgers[arm]["original_episode_id"] = "another"
    elif mutation == "clock":
        ledgers[arm]["candidate_key"] = roster["event_id"] + "@14"
    elif mutation == "reference":
        roster["confirmation_reference"] = "99"
    elif mutation == "management":
        ledgers["confirmation", "reference_limit_one_shot", "forecast_full_exit"]["execution"]["guard_cap"] = "103"
    elif mutation == "quantity":
        ledgers[arm]["outcome"]["filled_quantity"] = 1.0
    else:
        ledgers[arm]["outcome"]["net"] = "999999"
    with pytest.raises(ValueError):
        attribution.measure_event(roster, ledgers, market)


def test_summary_preserves_zero_weeks_and_separates_all_fills_from_wins():
    records = [measured(i) for i in (0, 1, 3, 4, 6, 9)]
    summary = attribution.summarize(["2024-01-02", "2024-01-15"], records)
    name = "|".join(attribution.PATH_ARM)
    totals = summary["arms"][name]
    assert (totals["fills"], totals["settled_fills"], totals["unknown_fills"], totals["unknown_cash"]) == (3, 2, 1, 2)
    assert summary["weekly"]["2024-W03"][name] == {"fills": 0, "wins": 0, "unknown_fills": 0}
    assert summary["weekly"]["2024-W01"][name] == {"fills": 3, "wins": 0, "unknown_fills": 1}
    pair = summary["comparisons"]["entry"]
    assert pair["categories"]["left_only"] == 1
    assert pair["categories"]["unknown"] == 1
    assert pair["cohort_cash"]["left_only"]["known_pairs"] == 1
    assert summary["comparisons"]["timing|next_open_capped"]["confounded"] is True
    assert summary["funnel"]["arms"][name]["selection_reasons"]["no_causal_frame"] == 1
    assert summary["concentration"]["dates"]["2024-01-15"][name]["opportunities"] == 0
    assert summary["path_observations"]["observed_cost_exceeded"] == 2
    assert summary["path_observations"]["close_witnesses"] == 2
    assert summary["path_observations"]["known_never_cost_exceeded"] == 1


def test_duplicate_episode_or_arm_and_off_calendar_record_rejected():
    record = measured()
    with pytest.raises(ValueError, match="Duplicate"):
        attribution.summarize(["2024-01-02"], [record, record])
    with pytest.raises(ValueError, match="off-calendar"):
        attribution.summarize(["2024-01-03"], [record])
    record["arms"][0] = record["arms"][1]
    with pytest.raises(ValueError, match="Duplicate or absent arm"):
        attribution.summarize(["2024-01-02"], [record])


def test_invented_geometry_has_all_modes_without_original_inputs():
    calendar, cases = invented_cases(calendar_sessions=3, names=4)
    records = [attribution.measure_event(*values) for values in cases]
    summary = attribution.summarize(calendar, records)
    assert summary["opportunities"] == 12
    assert len(summary["arms"]) == 12
    assert summary["paths"]["unknown_path"] == 1
    assert "exceeded_then_lost" in summary["paths"]
    assert "EXPIRED_NO_CONFIRMATION" in summary["funnel"]["terminal_states"]
