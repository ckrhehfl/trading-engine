"""Invented source-linked reports only; no actual data, network or runner."""

from copy import deepcopy
from datetime import timedelta
from fractions import Fraction

import pytest

from research import intraday_campaign_account_comparison as comparison
from research import intraday_campaign_account_inputs as inputs
from test_intraday_campaign_account_inputs import fixture as reports_fixture, projections


def fixture(sessions=3, *, missing=(), zero_days=(), same_symbol=False):
    a, b, chunks = reports_fixture(sessions)
    days = a["reference_sessions"]
    for chunk in chunks:
        if not chunk["rows"]:
            continue
        day_index = days.index(chunk["session"])
        opening = inputs.eb.cc.session_open(chunk["session"])
        template = chunk["rows"][0]
        chunk["rows"] = []
        for minute in range(80, 141):
            if (day_index, chunk["symbol"], minute) in missing:
                continue
            price = str(100 + day_index if minute == 140 else 100)
            chunk["rows"].append({**template,
                "open_time_ms": int((opening + timedelta(minutes=minute)).timestamp()) * 1000,
                "open": price, "high": price, "low": price, "close": price})
    for report in (a, b):
        report["sources"] = [inputs.eb._source(chunk) for chunk in chunks]
        if report["setup_id"] == "EA-A":
            report["volume_sources"] = deepcopy(report["sources"])
        symbol = "KRX:000001" if same_symbol or report["setup_id"] == "EA-A" else "KRX:000002"
        for variant in report["variants"].values():
            for row in variant["days"]:
                if row["signal"]["state"] != "signal":
                    continue
                if row["symbol"] == symbol and days.index(row["session"]) not in zero_days:
                    row["structural_stop"] = {"numerator": "1", "denominator": "1"}
                    # Deliberately misleading old outcomes must not control the
                    # entirely new causal account's admission or PnL.
                    row["outcome"].update(status="unknown", reason="required_path_missing", exit_at=None)
                    row["common_price_observable"] = False
                    continue
                row["signal"].update(state="known_no_signal", reason="initial_condition_false",
                    signal_bar_start=None, signal_bar_end=None, setup_bar_end=None)
                row.update(entry_at=None, cap_at=None, structural_stop=None, outcome=None, features={},
                    arms=dict.fromkeys(row["arms"], "rejected"), common_context_observable=False,
                    common_price_observable=False)
    return (*projections(a, b), chunks)


def test_one_stream_24_replays_per_day_and_compact_exact_account_output(monkeypatch):
    a, b, chunks = fixture()
    before = deepcopy((a, b, chunks))
    consumed, verified, calls, stages = [], [], [], []
    replay = comparison.account.replay_account

    def traced(*args, **kwargs):
        calls.append(kwargs["state"].risk_scope)
        return replay(*args, **kwargs)

    def stream():
        for chunk in chunks:
            consumed.append((chunk["session"], chunk["symbol"]))
            yield chunk

    def verify(**payload):
        verified.append((payload["day_input"]["session"], payload["scenario"]))
        assert payload["compact"]["terminal"]["cash"] == payload["result"].state.book.cash
        assert all(plan.strategy in payload["scenario"]["mode"] for plan in payload["candidates"])
        if payload["scenario"]["risk_scope"] == "day_local_counterfactual":
            assert payload["result"].scenario == "fresh_daily_counterfactual_no_continuous_weekly_monthly_claim"

    def prohibited(*args, **kwargs):
        raise AssertionError("old signal or outcome replay")

    monkeypatch.setattr(comparison.account, "replay_account", traced)
    monkeypatch.setattr(inputs.eb.signals, "detect_bars", prohibited)
    monkeypatch.setattr(inputs.eb.outcomes, "exploratory_outcome", prohibited)
    report = comparison.build(a, b, stream(), lambda stage, payload: stages.append(stage), verify)
    assert (a, b, chunks) == before
    assert len(consumed) == 60 and len(set(consumed)) == 60
    assert len(calls) == len(verified) == report["day_replay_count"] == 72
    assert calls.count("continuous") == calls.count("day_local_counterfactual") == 36
    assert stages == ["day_complete"] * 3 + ["sizing_complete", "summary_complete"]
    assert report["original_union_complete"] and report["independent_day_callback_completed"]
    assert report["input_evidence"]["A"] == a
    scenario = report["scenarios"]["D+1|33|A"]
    first, second, third = scenario["continuous"]["days"]
    fresh = scenario["day_local_counterfactual"]["days"]
    assert first["known_entry_count"] == 1
    assert first["assumed_cost"] == 6600
    assert first["net_pnl"] == -6600 and first["gross_pnl_same_fills"] == 0
    assert second["start_nav"] == first["final_nav"]
    assert fresh[1]["start_nav"] == fresh[2]["start_nav"] == comparison.cash.INITIAL_CAPITAL
    assert first["exposure"]["invested_minutes"] == 60
    assert first["exposure"]["maximum_value"] == 2_000_000
    assert first["full_universe_status"] == "indeterminate_missing_signals"
    assert first["terminal"]["lots"] == [] and first["terminal"]["marks"] == {}
    assert first["terminal"]["weekly_anchor"] is not None
    assert fresh[0]["terminal"]["weekly_anchor"] is fresh[0]["terminal"]["monthly_anchor"] is None
    assert len(first["events"]) == 2
    assert all("snapshots" not in day for scope in comparison.SCOPES for day in scenario[scope]["days"])
    assert all(row["gross_pnl_same_fills"] == row["net_pnl"] + row["assumed_cost"] for row in fresh)
    assert report["comparisons"]["D+1|33"]["common_sessions"] == a["reference_sessions"]
    assert set(report["comparisons"]["D+1|33"]["paired_sizing"]) == {"A+B_minus_A", "A+B_minus_B"}
    assert report["strategy_certification"] is False


def test_missing_day_freezes_continuous_but_daily_recovers_and_common_dates_exclude_it():
    a, b, chunks = fixture(missing={(0, "KRX:000001", 90)})
    report = comparison.build(a, b, chunks)
    scenario = report["scenarios"]["D+1|33|A"]
    continuous = scenario["continuous"]["days"]
    daily = scenario["day_local_counterfactual"]["days"]
    assert [row["status"] for row in continuous] == ["indeterminate"] * 3
    assert [row["status"] for row in daily] == ["indeterminate", "identified", "identified"]
    assert all(row["net_pnl"] is row["gross_pnl_same_fills"] is None for row in continuous)
    assert continuous[0]["terminal"]["cash"] == 99_993_400  # Conditional cap-exit bookkeeping continues.
    assert continuous[0]["terminal"]["cash_scope"] == "conditional_bookkeeping_after_first_unknown"
    assert continuous[1]["terminal"]["unknown_at"] == continuous[0]["terminal"]["unknown_at"]
    assert continuous[1]["known_entry_count"] == 0
    assert continuous[1]["exposure"]["identified_minutes"] == 0
    assert continuous[0]["exposure"]["invested_minutes"] == 13
    assert daily[1]["net_pnl"] == 13_400
    comparison_row = report["comparisons"]["D+1|33"]
    assert comparison_row["common_sessions"] == a["reference_sessions"][1:]
    assert comparison_row["excluded_sessions"] == a["reference_sessions"][:1]
    assert comparison_row["accounts"]["B"]["identified_days"] == 2
    first_week = a["reference_weeks"][0]
    weekly = comparison_row["accounts"]["B"]["by_week"][first_week]
    assert weekly["known_entry_count"] == 1 and weekly["reference_days"] == 2
    assert weekly["status"] == "incomplete" and weekly["entry_count_if_complete"] is None
    assert weekly["net_pnl_if_complete"] is None
    assert scenario["continuous"]["summary"]["mean_daily_net_pnl"] is None
    assert scenario["continuous"]["summary"]["known_total_assumed_cost"] == 6600
    plan = comparison_row["own_sizing"]["B"]["net_pnl"]
    assert plan["known_labels"] == 2 and plan["unknown_labels"] == 1


def test_zero_candidate_days_weeks_unknown_signals_and_old_targets_survive():
    a, b, chunks = fixture(zero_days={2})
    report = comparison.build(a, b, chunks)
    daily = report["scenarios"]["D+2|43|A+B"]["day_local_counterfactual"]
    zero = daily["days"][2]
    assert zero["status"] == "identified" and zero["net_pnl"] == 0
    assert zero["known_entry_count"] == zero["assumed_cost"] == 0
    assert zero["coverage"]["unknown_signal_units"] == 4
    week = daily["summary"]["by_week"][a["reference_weeks"][1]]
    assert week["entry_count_if_complete"] == 0 and week["status"] == "complete_observed_candidate_week"
    assert report["reference_weeks"] == a["reference_weeks"]
    assert report["consumed_target_chunks"] == 60


def test_clock_ineligible_plans_are_retained_but_never_passed_to_engine():
    a, b, chunks = fixture(1)
    day = a["reference_sessions"][0]
    for policy in inputs.POLICIES:
        content = a["variants"][policy]["dates"][day]
        plan = content["candidates"][0]
        plan.update(clock_eligible=False, clock_ineligible_reason="same_day_cap_infeasible",
                    entry_at=inputs.eb.cc.session_close(day) - timedelta(minutes=30))
        plan["cap_exit_at"] = plan["entry_at"] + timedelta(minutes=60)
        content["counts"]["clock_ineligible"] = 1
    report = comparison.build(a, b, chunks)
    result = report["scenarios"]["D+1|33|A"]["continuous"]["days"][0]
    assert result["coverage"]["recorded_signal_units"] == result["coverage"]["clock_ineligible_units"] == 1
    assert result["known_entry_count"] == 0 and result["net_pnl"] == 0
    assert report["input_evidence"]["A"]["variants"]["D+1"]["dates"][day]["candidates"][0] == plan


def test_joint_same_symbol_decline_and_closed_lot_concentration():
    a, b, chunks = fixture(1, same_symbol=True)
    report = comparison.build(a, b, chunks)
    joint = report["scenarios"]["D+1|33|A+B"]["day_local_counterfactual"]
    row, = joint["days"]
    assert row["same_symbol_declines"] == row["known_entry_count"] == 1
    assert row["assumed_cost"] == 3300 and row["events"][0]["quantity"] == 10000
    concentration = joint["summary"]["closed_lot_concentration"]
    assert concentration["by_symbol"]["net_pnl"] == {"KRX:000001": -3300}
    assert concentration["by_date"]["net_pnl"] == {a["reference_sessions"][0]: -3300}


def test_verifier_exception_aborts_without_reading_next_day_or_retry():
    a, b, chunks = fixture()
    consumed = []

    def stream():
        for chunk in chunks:
            consumed.append(chunk)
            yield chunk

    def verifier(**payload):
        raise RuntimeError("independent mismatch")

    with pytest.raises(RuntimeError, match="independent mismatch"):
        comparison.build(a, b, stream(), verify_day=verifier)
    assert len(consumed) == 20


def test_final_chunk_authentication_still_runs_after_frozen_accounts():
    a, b, chunks = fixture(missing={(0, "KRX:000001", 80), (0, "KRX:000002", 80)})
    chunks[-1]["rows"][0]["open"] = "999"
    with pytest.raises(inputs.AccountInputsRefusal, match="price_source_data_vintage_changed"):
        comparison.build(a, b, chunks)


def test_unknown_exit_preserves_lot_and_empty_common_population_has_no_zero_means():
    a, b, chunks = fixture(1, missing={(0, "KRX:000001", 140)})
    report = comparison.build(a, b, chunks)
    row, = report["scenarios"]["D+1|33|A"]["day_local_counterfactual"]["days"]
    assert row["terminal"]["cash"] == 97_993_400
    assert row["terminal"]["lots"][0]["quantity"] == 20_000
    assert row["terminal"]["marks"] == {"KRX:000001": None}
    assert row["closed_lots"] == [] and row["net_pnl"] is None
    common = report["comparisons"]["D+1|33"]
    assert common["common_sessions"] == []
    assert common["status"] == "no_common_identified_days"
    assert all(summary["mean_daily_net_pnl"] is None for summary in common["accounts"].values())
    assert all(value is None for value in common["paired_mean_daily_net_per_initial_capital"].values())
    assert common["accounts"]["B"]["by_week"][a["reference_weeks"][0]]["entry_count_if_complete"] is None


def test_one_held_gap_makes_other_later_exit_conditional_not_identified_concentration():
    a, b, chunks = fixture(1, missing={(0, "KRX:000001", 90)})
    report = comparison.build(a, b, chunks)
    joint = report["scenarios"]["D+1|33|A+B"]["continuous"]
    row, = joint["days"]
    assert row["terminal"]["cash"] == 99_993_400 and row["terminal"]["lots"] == []
    assert row["terminal"]["cash_scope"] == "conditional_bookkeeping_after_first_unknown"
    assert row["net_pnl"] is row["final_nav"] is None
    assert row["known_exit_count"] == row["conditional_exit_count"] == 2
    assert row["identified_exit_count"] == 0
    assert all(lot["identification"] == "conditional_after_or_at_unknown" for lot in row["closed_lots"])
    concentration = joint["summary"]["closed_lot_concentration"]
    assert concentration["identified_closed_lot_count"] == 0
    assert concentration["conditional_closed_lot_count"] == 2
    assert concentration["conditional_net_pnl"] == -6600
    assert concentration["by_date"]["net_pnl"] == concentration["by_symbol"]["net_pnl"] == {}


def test_empty_or_reordered_reference_calendar_cannot_claim_complete_consumption():
    a, b, chunks = fixture()
    a["reference_sessions"] = []
    with pytest.raises(inputs.AccountInputsRefusal, match="invalid_account_reference_calendar"):
        comparison.build(a, b, chunks)


def sizing_days(values, costs=None):
    dates = [f"2025-09-{day:02d}" for day in range(4, 4 + len(values))]
    costs = costs or [6600] * len(values)
    rows = [{"session": day, "status": "identified" if value is not None else "indeterminate",
             "net_pnl": Fraction(value) if value is not None else None,
             "gross_pnl_same_fills": Fraction(value + cost) if value is not None else None,
             "assumed_cost": Fraction(cost)} for day, value, cost in zip(dates, values, costs, strict=True)]
    return rows, dates


def test_sizing_uses_account_cost_scale_not_raw_trade_basis_points():
    rows, dates = sizing_days([0, 10000, -10000, 20000, -20000, 30000])
    plan = comparison.own_sizing(rows, dates)
    assert plan["economic_benchmark_bps"] == plan["cost_floor_bps_assumed"] == Fraction(33, 50)
    assert plan["mde_bps"] > float(plan["economic_benchmark_bps"])
    assert plan["status"] == "underpowered"
    assert plan["underpowered_descriptive_authorized"] is True
    assert plan["would_require_operator_underpower_choice"] is False
    assert plan["sizing_pass"] is plan["strategy_pass"] is False


@pytest.mark.parametrize("values,costs,reason", [
    ([None, None], None, "no_known_labels"),
    ([5, None], None, "fewer_than_two_known_days"),
    ([5, 5], None, "zero_dispersion"),
    ([5, 6], [0, 0], "zero_paid_cost_benchmark"),
])
def test_undefined_sizing_never_becomes_a_pass(values, costs, reason):
    rows, dates = sizing_days(values, costs)
    plan = comparison.own_sizing(rows, dates)
    assert plan["status"] == "uninformative" and plan["uninformative_reason"] == reason
    assert plan["sizing_pass"] is False and plan["family_adopted"] is False
    if all(value is None for value in values):
        assert plan["economic_benchmark_bps"] is None


def test_paired_sizing_uses_exact_common_dates_max_cost_and_preserves_unknown(monkeypatch):
    rows, dates = sizing_days([0, None, 12000, 24000], [3300] * 4)
    peer, _ = sizing_days([1000, 900000, None, 6000], [6600] * 4)
    original, captured = comparison.cc.dispersion, []

    def capture(records, reference):
        captured.extend(records)
        assert reference == dates
        return original(records, reference)

    monkeypatch.setattr(comparison.cc, "dispersion", capture)
    plan = comparison.own_sizing(rows, dates, peer_days=peer)
    assert [row["label"] for row in captured] == [Fraction(-1000, 100_000_000), None, None,
                                                  Fraction(18000, 100_000_000)]
    assert plan["economic_benchmark_bps"] == Fraction(33, 50)
    assert plan["known_labels"] == plan["unknown_labels"] == 2


def test_all_own_sizing_finishes_before_any_reported_mean(monkeypatch):
    a, b, chunks = fixture(1)
    own, summarize = comparison.own_sizing, comparison.summarize_days
    sequence = []

    def size(*args, **kwargs):
        sequence.append("size")
        return own(*args, **kwargs)

    def mean(*args, **kwargs):
        sequence.append("mean")
        return summarize(*args, **kwargs)

    monkeypatch.setattr(comparison, "own_sizing", size)
    monkeypatch.setattr(comparison, "summarize_days", mean)
    comparison.build(a, b, chunks)
    assert sequence[:80] == ["size"] * 80
    assert sequence[80:] == ["mean"] * 36
