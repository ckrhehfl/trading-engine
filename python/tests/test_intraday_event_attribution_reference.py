"""Invented independent EM reconstruction and corruption checks."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_event_attribution import measure_event, summarize
from research.intraday_event_attribution_fixtures import case
from research.intraday_event_attribution_reference import verify_event, verify_source_support, verify_summary
from research.intraday_event_attribution_support import summarize_source_support


PATH_ARM = ("confirmation", "reference_limit_one_shot", "fixed_exit")


@pytest.mark.parametrize("regime", range(12))
def test_reference_reconstructs_each_invented_regime(regime):
    roster, ledgers, market = case(regime)
    saved = measure_event(roster, ledgers, market)
    assert verify_event(roster, ledgers, market, saved) == saved


def test_reference_keeps_zero_dates_weeks_and_selection_cohorts():
    records = []
    for regime in (0, 1, 3, 4, 6, 9):
        roster, ledgers, market = case(regime)
        saved = measure_event(roster, ledgers, market)
        records.append(verify_event(roster, ledgers, market, saved))
    calendar = ("2024-01-02", "2024-01-09", "2024-01-16")
    report = summarize(calendar, records)
    assert verify_summary(calendar, records, report) == report
    assert report["weekly"]["2024-W02"]["|".join(PATH_ARM)] == dict(fills=0, wins=0, unknown_fills=0)
    assert report["concentration"]["dates"]["2024-01-16"]["|".join(PATH_ARM)]["opportunities"] == 0
    entry = report["comparisons"]["entry"]
    assert entry["categories"]["left_only"] == 1
    assert entry["cohort_cash"]["left_only"]["known_pairs"] == 1
    assert entry["common_settled"]["known_pairs"] < entry["whole"]["known_pairs"]
    assert entry["whole"]["unknown_pairs"] > 0


def test_reference_preserves_outcome_maturity_despite_closed_execution():
    roster, ledgers, market = case(6)
    assert ledgers[PATH_ARM]["execution"]["net"] is not None
    saved = measure_event(roster, ledgers, market)
    reconstructed = verify_event(roster, ledgers, market, saved)
    selected = next(row for row in reconstructed["arms"] if tuple(row[field] for field in
                    ("control", "entry_policy", "management")) == PATH_ARM)
    assert selected["fill_state"] == "filled"
    assert selected["cash_state"] == "unknown"
    assert selected["net_cash"] is None
    assert reconstructed["path"]["final_sale_return"] is None
    tampered = deepcopy(saved)
    tampered["arms"][0]["net_cash"] = "999"
    with pytest.raises(ValueError, match="event reconstruction"):
        verify_event(roster, ledgers, market, tampered)


def test_independent_minutes_reject_self_consistent_false_sale_price():
    roster, ledgers, market = case(0)
    arm = ("confirmation", "reference_limit_one_shot", "forecast_full_exit")
    row = ledgers[arm]
    report, outcome = row["execution"], row["outcome"]
    leg = report["sales"][0]
    leg["price"] = str(Fraction(leg["price"]) + 1)
    delta = leg["quantity"]
    for field in ("gross", "net"):
        leg[field] = str(Fraction(leg[field]) + delta)
        report[field] = str(Fraction(report[field]) + delta)
        outcome[field] = str(Fraction(outcome[field]) + delta)
    report["cash"] = str(Fraction(report["cash"]) + delta)
    saved = measure_event(roster, ledgers, market)
    with pytest.raises(ValueError, match="independent minute opening"):
        verify_event(roster, ledgers, market, saved)


def test_reference_rejects_repriced_same_quantity_entry():
    roster, ledgers, market = case(0)
    saved = measure_event(roster, ledgers, market)
    bars = dict(market.bars)
    bars[16] = replace(bars[16], open=Fraction(99), low=Fraction(99))
    changed = replace(market, bars=bars)
    with pytest.raises(ValueError, match="modeled purchase"):
        verify_event(roster, ledgers, changed, saved)


def test_partial_sales_allocate_the_initial_cost_once():
    roster, ledgers, market = case(0)
    arm = ("confirmation", "reference_limit_one_shot", "forecast_half_then_exit")
    execution = ledgers[arm]["execution"]
    assert len(execution["sales"]) == 2
    saved = measure_event(roster, ledgers, market)
    verify_event(roster, ledgers, market, saved)
    assert sum(Fraction(leg["allocated_cost"]) for leg in execution["sales"]) == Fraction(execution["entry_notional"]) * Fraction(33, 10_000)
    execution["sales"][1]["allocated_cost"] = execution["entry_cost"]
    with pytest.raises(ValueError, match="sale cash"):
        verify_event(roster, ledgers, market, saved)


def test_reference_rejects_earlier_structural_exit_without_received_breach():
    roster, ledgers, market = case(0)
    row = ledgers[PATH_ARM]
    row["execution"]["sales"][0].update(execute_offset=45, decision_offset=44, reason="structural")
    row["execution"]["reason"] = row["outcome"]["reason"] = "structural"
    saved = measure_event(roster, ledgers, market)
    with pytest.raises(ValueError, match="structural/time boundary"):
        verify_event(roster, ledgers, market, saved)


@pytest.mark.parametrize("field,value", [("max_open_return", "999"), ("max_open_offset", 69),
                                         ("ever_cost_exceeded", False), ("category", "never_exceeded")])
def test_reference_rejects_hindsight_path_tampering(field, value):
    roster, ledgers, market = case(1)
    saved = measure_event(roster, ledgers, market)
    saved["path"][field] = value
    with pytest.raises(ValueError, match="event reconstruction"):
        verify_event(roster, ledgers, market, saved)


def test_reference_never_uses_threshold_close_as_sale_price():
    roster, ledgers, market = case(1)
    saved = measure_event(roster, ledgers, market)
    verify_event(roster, ledgers, market, saved)
    witness = saved["path"]["first_cost_close"]
    assert witness["offset"] == 20 and witness["receipt_offset"] == 21
    assert witness["close"] == "101" and witness["opening_offset"] == 22
    assert witness["opening_price"] == "100" and witness["opening_return"] == "0"
    saved["path"]["first_cost_close"]["opening_price"] = "101"
    with pytest.raises(ValueError, match="event reconstruction"):
        verify_event(roster, ledgers, market, saved)


def test_reference_distinguishes_equality_and_keeps_original_exit_boundary():
    roster, ledgers, market = case(10)
    saved = measure_event(roster, ledgers, market)
    assert saved["path"]["category"] == "equal_only"
    assert saved["path"]["ever_cost_exceeded"] is False
    assert saved["path"]["first_cost_close"] is None
    bars = dict(market.bars)
    bars[47] = replace(bars[47], open=Fraction(200), high=Fraction(200), close=Fraction(200))
    assert verify_event(roster, ledgers, replace(market, bars=bars), saved) == saved


def test_entry_minute_received_close_can_be_first_causal_witness():
    roster, ledgers, market = case(0)
    bars = dict(market.bars)
    bars[16] = replace(bars[16], high=Fraction(101), close=Fraction(101))
    market = replace(market, bars=bars)
    saved = measure_event(roster, ledgers, market)
    assert verify_event(roster, ledgers, market, saved) == saved
    witness = saved["path"]["first_cost_close"]
    assert witness["offset"] == 16 and witness["opening_offset"] == 18
    assert saved["path"]["max_open_return"] == "0"


def test_gap_after_witness_keeps_observed_positive_and_known_first_receipt():
    roster, ledgers, market = case(1)
    bars = dict(market.bars)
    del bars[45]  # The original fixed sale executes at 46, before close 45 is demanded.
    market = replace(market, bars=bars)
    saved = measure_event(roster, ledgers, market)
    assert verify_event(roster, ledgers, market, saved) == saved
    assert saved["path"]["status"] == "incomplete"
    assert saved["path"]["observed_cost_exceeded"] is True
    assert saved["path"]["max_open_return"] is None
    assert saved["path"]["first_cost_close"]["first_is_known"] is True


def test_summary_tampering_cannot_drop_zero_weeks_or_unknowns():
    roster, ledgers, market = case(6)
    saved = measure_event(roster, ledgers, market)
    calendar = ("2024-01-02", "2024-01-09")
    report = summarize(calendar, [saved])
    del report["weekly"]["2024-W02"]
    with pytest.raises(ValueError, match="summary reconstruction"):
        verify_summary(calendar, [saved], report)
    report = summarize(calendar, [saved])
    report["arms"]["|".join(PATH_ARM)]["unknown_cash"] = 0
    with pytest.raises(ValueError, match="summary reconstruction"):
        verify_summary(calendar, [saved], report)


def test_reference_rejects_duplicate_episodes_and_missing_arms():
    roster, ledgers, market = case(0)
    saved = measure_event(roster, ledgers, market)
    report = summarize(("2024-01-02",), [saved])
    with pytest.raises(ValueError, match="duplicate"):
        verify_summary(("2024-01-02",), [saved, saved], report)
    ledgers.pop(PATH_ARM)
    with pytest.raises(ValueError, match="twelve"):
        verify_event(roster, ledgers, market, saved)


def _source_fixture():
    coordinates, support = [], []
    for index, length in enumerate((70, 50, 70, 70)):
        prices = ["missing" if index == 2 else "invalid" if index == 0 and offset == 7 else "observed"
                  for offset in range(length)]
        identity = dict(session="2024-01-02", symbol=f"SOURCE{index}")
        coordinates.append({**identity, "continuous_length": length,
            "continuous_price_observed": prices.count("observed"),
            "minute_states": [{"offset": offset, "price": value, "volume": "missing"}
                              for offset, value in enumerate(prices + ["observed"] * 10)]})
        status = "unknown_price" if index == 2 else "no_event"
        support.append({**identity, "family": "C_FIRSTPULLBACK", "scheduled_minutes": length,
                        "counts": {status: length},
                        "runs": [{"start": 0, "end": length, "status": status, "event_id": None}]})
    roster = {"event": dict(session="2024-01-02", symbol="SOURCE0")}
    return coordinates, support, roster


def test_source_reference_preserves_missing_prices_and_all_coordinates():
    coordinates, support, roster = _source_fixture()
    saved = summarize_source_support(coordinates, support, roster)
    assert verify_source_support(coordinates, support, roster, saved) == saved
    assert saved["coordinates"] == 4 and saved["coordinates_without_events"] == 3
    assert saved["continuous_minutes"] == 260
    assert saved["price_absent_coordinates"] == 1
    assert saved["price_partial_coordinates"] == 1
    assert saved["price_complete_coordinates"] == 2
    assert saved["first60_price_complete_coordinates"] == 1
    assert saved["continuous_price_states"] == dict(invalid=1, missing=70, observed=189)


def test_source_reference_reconstructs_rle_instead_of_trusting_saved_counts():
    coordinates, support, roster = _source_fixture()
    saved = summarize_source_support(coordinates, support, roster)
    support[0]["runs"] = [dict(start=0, end=20, status="unknown_price", event_id=None),
                          dict(start=20, end=70, status="no_event", event_id=None)]
    with pytest.raises(ValueError, match="expanded runs"):
        verify_source_support(coordinates, support, roster, saved)


def test_source_reference_rejects_incomplete_run_partition_and_coordinate_loss():
    coordinates, support, roster = _source_fixture()
    saved = summarize_source_support(coordinates, support, roster)
    support[0]["runs"][0]["start"] = 1
    with pytest.raises(ValueError, match="partition"):
        verify_source_support(coordinates, support, roster, saved)
    coordinates, support, roster = _source_fixture()
    with pytest.raises(ValueError, match="coordinate denominator"):
        verify_source_support(coordinates, support[:-1], roster, saved)
