"""Invented prices only: independent arithmetic versus the producer path."""

from copy import deepcopy
from collections import UserDict
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from math import isclose
from types import MappingProxyType
from collections.abc import Mapping

import pytest

from research import intraday_campaign_account as account
from research import intraday_campaign_account_comparison as comparison
from research.intraday_campaign_independent import VerificationError, Verifier, _equal, _plain, _sizing, _summary


MINUTE = timedelta(minutes=1)
KST = timezone(timedelta(hours=9))


def fixture(day="2026-01-30", *, strategy="A", symbol="INVENTED-A", price=100,
            stop=90, entry_offset=5):
    opening = datetime.fromisoformat(day).replace(hour=9, tzinfo=KST)
    entry = opening + entry_offset * MINUTE
    plan = account.CampaignCandidate(strategy, symbol, entry - MINUTE, entry,
                                     entry + 60 * MINUTE, Fraction(stop))
    minutes = {(symbol, opening + offset * MINUTE): (price, price, price, price)
               for offset in range(81)}
    data = {"session": day, "session_open": opening, "session_close": opening + 80 * MINUTE,
            "minute_bars": minutes, "open_prices": {key: bar[0] for key, bar in minutes.items()},
            "sources": [], "retained_price_symbols": [symbol], "consumed_target_chunks": 0,
            "consumed_price_rows": 0, "original_union_complete": True}
    return data, plan


def run(verifier, day, plans, *, mode="A", cost=33, scope="continuous", state=None,
        transform=None, policy="D+1", compress_idle_minutes=False):
    initial = state or account.new_state(mode, cost_bps=cost, risk_scope=scope)
    result = account.replay_account(plans, day["minute_bars"], day["open_prices"],
                                    {day["session_open"].date(): day["session_open"]},
                                    {day["session_open"].date(): day["session_close"]},
                                    mode=mode, cost_bps=cost, state=initial,
                                    compress_idle_minutes=compress_idle_minutes)
    if scope == "day_local_counterfactual":
        result = replace(result, scenario="fresh_daily_counterfactual_no_continuous_weekly_monthly_claim")
    coverage = {"by_setup": {}, "unknown_signal_units": 0, "clock_ineligible_units": 0,
                "recorded_signal_units": len(plans)}
    compact = comparison.compact_day(day, initial, result, coverage)
    payload = {"day_input": day, "candidates": plans, "initial_state": initial,
               "result": result, "compact": compact,
               "scenario": {"timing_policy": policy, "cost_bps": cost, "mode": mode, "risk_scope": scope,
                            "compress_idle_minutes": compress_idle_minutes}}
    if transform:
        transform(payload)
    receipt = verifier.verify_day(**payload)
    return result, compact, receipt


@pytest.mark.parametrize("mode", ["A", "B", "A+B"])
@pytest.mark.parametrize("cost", [33, 43])
def test_exact_complete_day(mode, cost):
    day, plan = fixture(strategy="B" if mode == "B" else "A")
    result, compact, receipt = run(Verifier(), day, [plan], mode=mode, cost=cost)
    assert compact["known_entry_count"] == compact["known_exit_count"] == 1
    assert compact["net_pnl"] == -compact["assumed_cost"]
    assert receipt["status"] == "verified"
    assert result.state.book.cash == 100_000_000 - compact["assumed_cost"]


@pytest.mark.parametrize("missing", ["entry", "held", "exit"])
def test_missing_prices_remain_unknown_across_days(missing):
    day, plan = fixture()
    at = {"entry": plan.entry_at, "held": plan.entry_at + MINUTE,
          "exit": plan.cap_exit_at}[missing]
    day["minute_bars"].pop((plan.symbol, at))
    day["open_prices"].pop((plan.symbol, at))
    verifier = Verifier()
    result, compact, _ = run(verifier, day, [plan])
    assert compact["status"] == "indeterminate"
    tomorrow, plan2 = fixture("2026-02-02")
    result2, compact2, _ = run(verifier, tomorrow, [plan2], state=result.state)
    assert compact2["status"] == "indeterminate"
    assert result2.state.unknown_at == result.state.unknown_at
    assert compact2["known_entry_count"] == 0


def test_stop_is_available_close_then_next_open():
    day, plan = fixture()
    coordinate = (plan.symbol, plan.entry_at + MINUTE)
    day["minute_bars"][coordinate] = (100, 100, 89, 89)
    result, compact, _ = run(Verifier(), day, [plan])
    stop = next(event for event in result.events if event.kind == "stop")
    assert stop.at == plan.entry_at + 4 * MINUTE
    assert compact["closed_lots"][0]["exit_at"] == stop.at + MINUTE


def test_duplicate_missing_open_affects_only_later_held_mark():
    day, plan = fixture()
    second = replace(plan, strategy="B", decision_at=plan.decision_at + 5 * MINUTE,
                     entry_at=plan.entry_at + 5 * MINUTE, cap_exit_at=plan.cap_exit_at + 5 * MINUTE)
    day["open_prices"][(plan.symbol, second.entry_at)] = None
    _, compact, _ = run(Verifier(), day, [plan, second], mode="A+B")
    assert compact["terminal"]["unknown_at"] == second.entry_at + 3 * MINUTE
    assert compact["same_symbol_declines"] == 1


def test_same_clock_duplicate_only_reads_entry_open_once_per_path():
    class CountedPrices(dict):
        def __init__(self, values, entry):
            super().__init__(values)
            self.entry = entry
            self.reads = 0

        def get(self, key, default=None):
            if key == self.entry:
                self.reads += 1
            return super().get(key, default)

    day, plan = fixture()
    prices = CountedPrices(day["open_prices"], (plan.symbol, plan.entry_at))
    day["open_prices"] = prices
    _, compact, _ = run(Verifier(), day, [plan, replace(plan, strategy="B")], mode="A+B")
    assert compact["same_symbol_declines"] == 1
    # Per implementation: one entry read and one held-minute read three minutes later.
    assert prices.reads == 4


@pytest.mark.parametrize("boundary", ["entry", "exit"])
@pytest.mark.parametrize("missing", ["missing_key", "conflict", "invalid_later_hlc"])
def test_open_boundary_uses_exact_map_but_not_future_hlc(boundary, missing):
    day, plan = fixture()
    at = plan.entry_at if boundary == "entry" else plan.cap_exit_at
    key = (plan.symbol, at)
    if missing == "missing_key":
        day["open_prices"].pop(key)
    elif missing == "conflict":
        day["open_prices"][key] = Fraction(101)
    else:
        day["minute_bars"][key] = None
    _, compact, _ = run(Verifier(), day, [plan])
    if missing == "invalid_later_hlc":
        event = next(event for event in compact["events"] if event["kind"] == boundary)
        assert event["quantity"] > 0
        if boundary == "exit":
            assert compact["status"] == "identified"
        else:
            assert compact["terminal"]["unknown_at"] == plan.entry_at + 3 * MINUTE
    else:
        assert compact["terminal"]["unknown_at"] == at


def test_current_daily_gate_recovers_without_a_latch():
    day, _ = fixture()
    plans = []
    for index in range(20):
        extra, plan = fixture(symbol=f"INVENTED-{index:02d}", stop=1)
        plans.append(plan)
        day["minute_bars"].update(extra["minute_bars"])
        day["open_prices"].update(extra["open_prices"])
        for offset in range(7, 12):
            day["minute_bars"][(plan.symbol, day["session_open"] + offset * MINUTE)] = (100, 100, 98, 98)
    for name, offset in (("DECLINED", 10), ("RECOVERED", 15)):
        extra, plan = fixture(symbol=name, stop=1, entry_offset=offset)
        plans.append(plan)
        day["minute_bars"].update(extra["minute_bars"])
        day["open_prices"].update(extra["open_prices"])
    _, compact, _ = run(Verifier(), day, plans)
    entries = {event["symbol"]: event for event in compact["events"] if event["kind"] == "entry"}
    assert entries["DECLINED"]["reason"] == "entry_blocked:daily"
    assert entries["RECOVERED"]["quantity"] > 0


def test_inclusive_daily_gate_and_after_entry_cost_gate():
    for margin, expected_first in ((Fraction(), "entry_blocked:daily"), (Fraction(1), "entered")):
        day, plan = fixture(stop=1)
        # Solve the mark that puts the independently funded account at threshold + margin.
        price = (Fraction(99_500_000) + margin - Fraction(97_993_400)) / 20_000
        day["minute_bars"][(plan.symbol, plan.entry_at + 2 * MINUTE)] = (100, 100, price, price)
        plans = [plan]
        for name in ("LATE-X", "LATE-Y"):
            extra, late = fixture(symbol=name, stop=1, entry_offset=10)
            plans.append(late)
            day["minute_bars"].update(extra["minute_bars"])
            day["open_prices"].update(extra["open_prices"])
        _, compact, _ = run(Verifier(), day, plans)
        events = {event["symbol"]: event for event in compact["events"] if event["kind"] == "entry"}
        assert events["LATE-X"]["reason"] == expected_first
        assert events["LATE-Y"]["reason"] == "entry_blocked:daily"


def test_missing_exit_is_never_retried_on_a_later_known_open():
    day, plan = fixture()
    day["open_prices"].pop((plan.symbol, plan.cap_exit_at))
    result, compact, _ = run(Verifier(), day, [plan])
    exits = [event for event in result.events if event.kind == "exit"]
    assert len(exits) == 1 and exits[0].reason == "exit_price_unknown"
    assert len(compact["terminal"]["lots"]) == 1
    assert compact["terminal"]["cash_scope"] == "conditional_bookkeeping_after_first_unknown"


def test_all_period_gates_use_current_returns_and_reset_at_new_periods():
    day, _ = fixture()
    day["session_close"] = day["session_open"] + 140 * MINUTE
    plans = []
    for index in range(3):
        extra, plan = fixture(symbol=f"GAP-{index}", stop=1)
        plans.append(plan)
        day["minute_bars"].update(extra["minute_bars"])
        day["open_prices"].update(extra["open_prices"])
        day["minute_bars"][(plan.symbol, plan.cap_exit_at)] = (1, 1, 1, 1)
        day["open_prices"][(plan.symbol, plan.cap_exit_at)] = 1
    extra, late = fixture(symbol="BLOCKED", stop=1, entry_offset=70)
    day["minute_bars"].update(extra["minute_bars"])
    day["open_prices"].update(extra["open_prices"])
    verifier = Verifier()
    result, compact, _ = run(verifier, day, [*plans, late])
    entry = next(event for event in compact["events"] if event["symbol"] == "BLOCKED")
    assert entry["reason"] == "entry_blocked:daily,weekly,monthly,hard_monthly"
    next_day, _ = fixture("2026-02-02")
    next_result, next_compact, _ = run(verifier, next_day, [], state=result.state)
    assert next_result.snapshots[0].entry_gates == ()
    assert next_compact["prefix_maximum_drawdown"] == compact["prefix_maximum_drawdown"] > 0


def test_stop_at_original_cap_is_not_a_second_exit():
    day, plan = fixture()
    day["minute_bars"][(plan.symbol, plan.cap_exit_at - 4 * MINUTE)] = (100, 100, 89, 89)
    result, compact, _ = run(Verifier(), day, [plan])
    exits = [event for event in result.events if event.kind == "exit"]
    assert len(exits) == 1 and exits[0].reason == "stop_at_time_cap"
    assert compact["known_exit_count"] == 1


def test_cash_constraint_and_integer_share_rejections_are_independent():
    day, _ = fixture()
    plans = []
    for index in range(101):
        extra, plan = fixture(symbol=f"CASH-{index:03d}", price=1_000_000, stop=1)
        plans.append(plan)
        day["minute_bars"].update(extra["minute_bars"])
        day["open_prices"].update(extra["open_prices"])
    _, compact, _ = run(Verifier(), day, plans)
    assert compact["cash_rejections"] > 0
    assert compact["terminal"]["cash"] > 0
    expensive, plan = fixture(price=3_000_000, stop=1)
    _, rejected, _ = run(Verifier(), expensive, [plan])
    assert rejected["size_rejections"] == 1


def test_month_boundary_anchors_are_derived_from_own_history():
    verifier = Verifier()
    day, plan = fixture()
    result, first, _ = run(verifier, day, [plan])
    next_day, next_plan = fixture("2026-02-02")
    _, second, _ = run(verifier, next_day, [next_plan], state=result.state)
    assert second["terminal"]["monthly_anchor"]["nav"] == first["final_nav"]
    assert second["terminal"]["weekly_anchor"]["nav"] == first["final_nav"]


def test_daily_scope_has_no_invented_week_or_month_anchor():
    verifier = Verifier()
    for session in ("2026-01-30", "2026-02-02"):
        day, plan = fixture(session)
        _, compact, _ = run(verifier, day, [plan], scope="day_local_counterfactual")
        assert compact["start_nav"] == 100_000_000
        assert compact["terminal"]["weekly_anchor"] is None
        assert compact["terminal"]["monthly_anchor"] is None


@pytest.mark.parametrize("target", ["cash", "event", "snapshot", "pnl", "exposure", "closed_lot", "initial"])
def test_output_tampering_fails(target):
    day, plan = fixture()

    def mutate(payload):
        result = payload["result"]
        if target == "cash":
            sleeve = result.state.book.sleeves[0]
            book = replace(result.state.book, sleeves=(replace(sleeve, cash=sleeve.cash + 1),))
            payload["result"] = replace(result, state=replace(result.state, book=book))
        elif target == "event":
            payload["result"] = replace(result, events=(replace(result.events[0], quantity=1), *result.events[1:]))
        elif target == "snapshot":
            payload["result"] = replace(result, snapshots=(replace(result.snapshots[0], nav=Fraction(1)), *result.snapshots[1:]))
        elif target == "pnl":
            payload["compact"]["net_pnl"] += 1
        elif target == "exposure":
            payload["compact"]["exposure"]["invested_minutes"] += 1
        elif target == "closed_lot":
            payload["compact"]["closed_lots"][0]["net_pnl"] += 1
        else:
            initial = payload["initial_state"]
            payload["initial_state"] = replace(initial, peak_nav=Fraction(1))

    with pytest.raises(VerificationError):
        run(Verifier(), day, [plan], transform=mutate)


def test_finish_does_not_claim_full_verification_from_one_day_callback():
    day, plan = fixture()
    verifier = Verifier()
    run(verifier, day, [plan])
    with pytest.raises(VerificationError, match="incomplete scenario"):
        verifier.finish_verify({"reference_sessions": [day["session"]]})


def test_zero_candidate_day_is_identified_zero_and_callback_cannot_repeat():
    day, _ = fixture()
    verifier = Verifier()
    _, compact, _ = run(verifier, day, [])
    assert compact["net_pnl"] == compact["known_entry_count"] == 0
    with pytest.raises(VerificationError, match="duplicate callback"):
        run(verifier, day, [])


@pytest.mark.parametrize("shape", ["varying", "constant", "no_cost", "missing", "empty", "paired"])
def test_independent_sizing_matches_registered_estimator(shape):
    reference = [f"2026-01-{day:02d}" for day in range(5, 17)]
    rows = [{"session": day, "status": "identified", "net_pnl": Fraction((index % 4) * 12789),
             "assumed_cost": Fraction(1234 + index)} for index, day in enumerate(reference)]
    if shape == "constant":
        rows = [dict(row, net_pnl=Fraction()) for row in rows]
    elif shape == "no_cost":
        rows = [dict(row, assumed_cost=Fraction()) for row in rows]
    elif shape == "missing":
        rows = rows[::4]
    elif shape == "empty":
        rows = []
    peer = [dict(row, net_pnl=row["net_pnl"] / 2, assumed_cost=2 * row["assumed_cost"]) for row in rows] if shape == "paired" else None
    actual = comparison.own_sizing(rows, reference, peer_days=peer)
    _equal(actual, _sizing(rows, reference, peer=peer), "own_sizing")


def report_fixture(*, compress_idle_minutes=True):
    """A complete 24-scenario output, generated solely from invented input."""
    verifier = Verifier()
    reference = ["2026-01-30", "2026-02-02", "2026-02-03"]
    weeks = ["2026-01-26", "2026-02-02"]
    projections = {}
    scenarios = {}
    sources = [{"session": session, "symbol": f"INVENTED-{strategy}", "input_rows": 81,
                "data_vintage": "invented-only", "row_linkage_sha256": "0" * 64}
               for session in reference for strategy in ("A", "B")]
    for strategy in ("A", "B"):
        dates = {}
        for index, session in enumerate(reference):
            _, plan = fixture(session, strategy=strategy, symbol=f"INVENTED-{strategy}", price=100 + index)
            dates[session] = {"targets": [{"symbol": plan.symbol, "signal_state": "signal"}],
                              "candidates": [dict(asdict(plan), clock_eligible=True, clock_ineligible_reason=None)],
                              "counts": {"targets": 1, "signals": 1, "known_no_signal": 0,
                                         "unknown_signals": 0, "clock_ineligible": 0}}
        projections[strategy] = {"setup_id": f"EA-{strategy}", "reference_sessions": reference,
                                 "reference_weeks": weeks, "sources": deepcopy(sources),
                                 "variants": {policy: {"dates": deepcopy(dates)} for policy in ("D+1", "D+2")}}
    for index, session in enumerate(reference):
        day_a, plan_a = fixture(session, price=100 + index)
        day_b, plan_b = fixture(session, strategy="B", symbol="INVENTED-B", price=100 + index)
        day_a["minute_bars"].update(day_b["minute_bars"])
        day_a["open_prices"].update(day_b["open_prices"])
        day_a["retained_price_symbols"] = ["INVENTED-A", "INVENTED-B"]
        day_a["sources"] = [source for source in sources if source["session"] == session]
        day_a["consumed_target_chunks"] = 2
        day_a["consumed_price_rows"] = 162
        day_a["original_union_complete"] = index == len(reference) - 1
        for policy in ("D+1", "D+2"):
            for cost in (33, 43):
                for mode in ("A", "B", "A+B"):
                    key = f"{policy}|{cost}|{mode}"
                    scenario = scenarios.setdefault(key, {"timing_policy": policy, "cost_bps": cost, "mode": mode,
                                                          "continuous": {"days": []}, "day_local_counterfactual": {"days": []}})
                    plans = [plan for plan in (plan_a, plan_b) if mode == "A+B" or plan.strategy == mode]
                    for scope in ("continuous", "day_local_counterfactual"):
                        def coverage(payload):
                            counts = {f"EA-{s}": projections[s]["variants"][policy]["dates"][session]["counts"]
                                      for s in ("A", "B") if mode == "A+B" or s == mode}
                            payload["compact"]["coverage"]["by_setup"] = counts

                        result, compact, _ = run(verifier, day_a, plans, mode=mode, cost=cost,
                                                 scope=scope, policy=policy,
                                                 state=scenario[scope].pop("_state", None), transform=coverage,
                                                 compress_idle_minutes=compress_idle_minutes)
                        scenario[scope]["days"].append(compact)
                        if scope == "continuous" and index < len(reference) - 1:
                            scenario[scope]["_state"] = result.state
    report = {"mode": comparison.MODE, "reference_sessions": reference, "reference_weeks": weeks,
              "initial_capital": Fraction(100_000_000), "input_evidence": projections,
              "consumed_target_chunks": 6, "consumed_price_rows": 486,
              "original_union_complete": True, "day_replay_count": 24 * len(reference),
              "idle_minutes_compressed": True,
              "scenarios": scenarios, "independent_day_callback_completed": True,
              "account_path_scope": "recorded_detectable_candidates_conditioned_on_archive_availability",
              "continuous_scope": "identified_prefix_freezes_at_first_unknown_without_next_day_reset",
              "daily_scope": "fresh_initial_capital_day_local_gate_only_no_continuous_account_claim",
              "gross_scope": "net_plus_assumed_cost_on_same_fills_no_zero_cost_rerun",
              "performance_computed": True, "strategy_pass": False, "promotion_allowed": False,
              "strategy_certification": False, "historical_brokerage_account_claim": False}
    comparison._finish_statistics(report, None)
    return verifier, report


def test_independent_aggregate_preserves_calendar_and_conditional_lots():
    day, plan = fixture()
    day["minute_bars"].pop((plan.symbol, plan.entry_at + MINUTE))
    _, compact, _ = run(Verifier(), day, [plan])
    reference = [day["session"], "2026-02-02"]
    weeks = [compact["week"], "2026-02-02"]
    _equal(comparison.summarize_days([compact], weeks, reference), _summary([compact], weeks, reference), "summary")


def test_full_finish_reconstructs_all_aggregates_and_rejects_tampering():
    verifier, report = report_fixture()
    receipt = verifier.finish_verify(report)
    assert receipt["status"] == "verified"
    assert receipt["verified"] is True
    assert receipt["day_replays"] == 72
    changes = [
        lambda r: r["scenarios"]["D+1|33|A"]["continuous"]["summary"].update(known_entry_count=999),
        lambda r: r["scenarios"]["D+1|33|A"]["continuous"]["own_sizing"]["net_pnl"].update(design_se=999.0),
        lambda r: r["comparisons"]["D+1|33"].update(common_sessions=[]),
        lambda r: r["comparisons"]["D+1|33"]["paired_sizing"]["A+B_minus_A"].update(economic_benchmark_bps=Fraction(999)),
        lambda r: r["comparisons"]["D+1|33"]["paired_mean_daily_net_per_initial_capital"].update({"A+B_minus_A": Fraction(999)}),
        lambda r: r.update(consumed_price_rows=999),
        lambda r: r["input_evidence"]["A"]["variants"]["D+1"]["dates"]["2026-01-30"]["candidates"].clear(),
    ]
    for change in changes:
        changed = deepcopy(report)
        change(changed)
        with pytest.raises(VerificationError):
            verifier.finish_verify(changed)


@pytest.mark.parametrize("condition", ["complete", "no_candidates", "invalidated", "entry_unknown",
                                       "held_unknown", "exit_unknown", "duplicate", "stop", "cap_stop"])
def test_compression_preserves_dense_financial_path_and_required_price_reads(condition):
    class ObservedPrices(dict):
        def __init__(self, values):
            super().__init__(values)
            self.reads = []

        def get(self, key, default=None):
            self.reads.append(key)
            return super().get(key, default)

    day, plan = fixture()
    plans = [plan]
    if condition == "no_candidates":
        plans = []
    elif condition == "invalidated":
        plans = [replace(plan, structural_stop=Fraction(100))]
    elif condition == "entry_unknown":
        day["open_prices"].pop((plan.symbol, plan.entry_at))
    elif condition == "held_unknown":
        day["minute_bars"].pop((plan.symbol, plan.entry_at + MINUTE))
    elif condition == "exit_unknown":
        day["open_prices"].pop((plan.symbol, plan.cap_exit_at))
    elif condition == "duplicate":
        plans.append(replace(plan, strategy="B"))
    elif condition in ("stop", "cap_stop"):
        clock = plan.entry_at + MINUTE if condition == "stop" else plan.cap_exit_at - 4 * MINUTE
        day["minute_bars"][(plan.symbol, clock)] = (100, 100, 89, 89)
    outcomes = []
    for compressed in (False, True):
        inputs = deepcopy(day)
        inputs["open_prices"] = ObservedPrices(inputs["open_prices"])
        inputs["minute_bars"] = ObservedPrices(inputs["minute_bars"])
        result, compact, _ = run(Verifier(), inputs, plans, mode="A+B", compress_idle_minutes=compressed)
        outcomes.append((result, compact, inputs))
    dense, sparse = outcomes
    assert dense[0].state == sparse[0].state
    assert dense[0].events == sparse[0].events
    assert dense[1] == sparse[1]
    assert dense[2]["open_prices"].reads == sparse[2]["open_prices"].reads
    assert dense[2]["minute_bars"].reads == sparse[2]["minute_bars"].reads
    assert sparse[0].snapshots[0].at == day["session_open"]
    assert len(sparse[0].snapshots) < len(dense[0].snapshots)
    if condition == "no_candidates":
        assert [row.at for row in sparse[0].snapshots] == [day["session_open"], day["session_close"]]
    elif condition == "complete":
        clocks = {row.at for row in sparse[0].snapshots}
        assert clocks == {day["session_open"], day["session_close"],
                          *(plan.entry_at + offset * MINUTE for offset in range(61))}


def test_compression_keeps_future_entry_attempt_after_sticky_empty_unknown():
    day, plan = fixture()
    day["open_prices"].pop((plan.symbol, plan.entry_at))
    extra, next_plan = fixture(symbol="LATER", entry_offset=15)
    day["minute_bars"].update(extra["minute_bars"])
    day["open_prices"].update(extra["open_prices"])
    dense, dense_day, _ = run(Verifier(), day, [plan, next_plan])
    sparse, sparse_day, _ = run(Verifier(), day, [plan, next_plan], compress_idle_minutes=True)
    assert dense.state == sparse.state
    assert dense.events == sparse.events
    assert dense_day == sparse_day
    assert sparse.events[-1].at == next_plan.entry_at
    assert sparse.events[-1].reason == "entry_blocked:account_indeterminate"
    assert sparse.state.unknown_at == plan.entry_at
    assert sparse.state.last_at == day["session_close"]


def test_compression_preserves_period_reset_and_prefix_risk():
    dense_verifier, sparse_verifier = Verifier(), Verifier()
    dense_state = sparse_state = None
    for session in ("2026-01-30", "2026-02-02"):
        day, plan = fixture(session)
        dense, dense_day, _ = run(dense_verifier, day, [plan], state=dense_state)
        sparse, sparse_day, _ = run(sparse_verifier, day, [plan], state=sparse_state, compress_idle_minutes=True)
        assert dense.state == sparse.state
        assert dense_day == sparse_day
        dense_state, sparse_state = dense.state, sparse.state


@pytest.mark.parametrize("flag", [0, 1, "true", None])
def test_verifier_refuses_non_boolean_compression_metadata(flag):
    day, plan = fixture()
    with pytest.raises(VerificationError, match="must be boolean"):
        run(Verifier(), day, [plan], transform=lambda payload: payload["scenario"].update(compress_idle_minutes=flag))


def test_legacy_day_callback_without_compression_flag_uses_dense_grid():
    day, plan = fixture()
    result, _, receipt = run(Verifier(), day, [plan],
                              transform=lambda payload: payload["scenario"].pop("compress_idle_minutes"))
    assert receipt["status"] == "verified"
    assert len(result.snapshots) == 82  # 81 minute clocks plus the filled entry.


def test_finish_refuses_false_compression_claim_and_dense_callback_history():
    verifier, report = report_fixture()
    report["idle_minutes_compressed"] = False
    with pytest.raises(VerificationError, match="idle_minutes_compressed"):
        verifier.finish_verify(report)
    verifier, report = report_fixture(compress_idle_minutes=False)
    with pytest.raises(VerificationError, match="compression_callbacks"):
        verifier.finish_verify(report)


# Frozen pre-optimization oracle: preserve eager normalization and its exact
# comparison/error rules independently of the optimized production comparator.
def _legacy_plain(value):
    if is_dataclass(value):
        return {field.name: _legacy_plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _legacy_plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_legacy_plain(item) for item in value]
    return value


def _legacy_compare(actual, expected, path):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise VerificationError(f"{path}: field set differs")
        for key in expected:
            _legacy_compare(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise VerificationError(f"{path}: sequence length differs")
        for index, (left, right) in enumerate(zip(actual, expected)):
            _legacy_compare(left, right, f"{path}[{index}]")
    elif isinstance(expected, float):
        if not isinstance(actual, float) or not isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15):
            raise VerificationError(f"{path}: reconstructed statistical value differs")
    elif isinstance(expected, Fraction) and not isinstance(actual, Fraction):
        raise VerificationError(f"{path}: exact Fraction output required")
    elif actual != expected or (isinstance(actual, bool) != isinstance(expected, bool)):
        raise VerificationError(f"{path}: reconstructed value differs")


def _legacy_equal(actual, expected, path):
    _legacy_compare(_legacy_plain(actual), _legacy_plain(expected), path)


def _comparison_outcome(compare, actual, expected):
    try:
        compare(actual, expected, "root")
    except VerificationError as error:
        return str(error)
    return "equal"


@dataclass(frozen=True, eq=False)
class EqualityForbiddenRecord:
    name: str
    payload: object

    def __eq__(self, other):
        raise AssertionError("opaque record equality must never be invoked")


def _nested_comparison_cases():
    record = EqualityForbiddenRecord("invented", {
        "rows": (EqualityForbiddenRecord("A", Fraction(7, 3)),
                 EqualityForbiddenRecord("B", {"count": 1, "ready": True, "cost": Fraction(0),
                                                "se": 0.125, "at": datetime(2026, 1, 30, tzinfo=KST)})),
        "none": None, "tags": {"A", "B"}})
    plain = _legacy_plain(record)
    cases = [(record, plain), (plain, record), (record, deepcopy(record)),
             (MappingProxyType(plain), UserDict(plain)),
             (UserDict(plain), MappingProxyType(plain)),
             ((record, plain), [plain, record]),
             (record, None), (None, record), ([], record), (record, []),
             ({}, plain), (dict(plain, extra="metadata"), record),
             (record, dict(plain, extra="metadata")),
             ((1, 2), [1]), ("bad", [1]), ([1], "bad")]
    mutations = [
        lambda row: row["payload"]["rows"].pop(),
        lambda row: row["payload"]["rows"][0].update(payload=7 / 3),
        lambda row: row["payload"]["rows"][1]["payload"].update(cost=0),
        lambda row: row["payload"]["rows"][1]["payload"].update(count=True),
        lambda row: row["payload"]["rows"][1]["payload"].update(ready=1),
        lambda row: row["payload"]["rows"][1]["payload"].update(se=0.125 + 1e-14),
        lambda row: row["payload"]["rows"][1]["payload"].update(se=0.125 + 1e-6),
        lambda row: row["payload"]["rows"][1]["payload"].update(se=float("nan")),
        lambda row: row["payload"]["rows"][1]["payload"].pop("at"),
        lambda row: row["payload"]["rows"][1]["payload"].update(source_metadata={"extra": 1}),
    ]
    for mutate in mutations:
        changed = deepcopy(plain)
        mutate(changed)
        cases.append((changed, record))
    scalars = [None, True, False, 0, 1, Fraction(0), Fraction(1), Fraction(2, 3),
               0.0, 1.0, float("nan"), float("inf"), "1", "", timedelta(minutes=1)]
    cases.extend((actual, expected) for actual in scalars for expected in scalars)
    return cases


@pytest.mark.parametrize("case", _nested_comparison_cases(), ids=lambda case: None)
def test_lazy_comparison_preserves_eager_oracle_decisions_and_error_paths(case):
    actual, expected = case
    assert _comparison_outcome(_equal, actual, expected) == _comparison_outcome(_legacy_equal, actual, expected)


def test_normalization_capture_still_matches_eager_oracle():
    for actual, expected in _nested_comparison_cases():
        # NaN is intentionally unequal under both comparators; compare outcomes.
        for value in (actual, expected):
            assert _comparison_outcome(_legacy_equal, _plain(value), _legacy_plain(value)) in (
                "equal", "root: reconstructed statistical value differs",
                "root.payload.rows[1].payload.se: reconstructed statistical value differs")


def benchmark_comparator(iterations=200):
    """Manual invented-only microbenchmark; no timing assertion in the suite."""
    import cProfile
    import pstats
    from time import process_time

    day, _ = fixture()
    plans = []
    for index in range(20):
        extra, plan = fixture(symbol=f"BENCHMARK-{index:02d}", stop=1)
        plans.append(plan)
        day["minute_bars"].update(extra["minute_bars"])
        day["open_prices"].update(extra["open_prices"])
    result, compact, _ = run(Verifier(), day, plans)
    actual = {"result": result, "compact": compact}
    expected = _legacy_plain(actual)
    measurements = {}
    for name, compare in (("legacy", _legacy_equal), ("lazy", _equal)):
        started = process_time()
        for _ in range(iterations):
            compare(actual, expected, "replay")
        elapsed = process_time() - started
        profile = cProfile.Profile()
        profile.enable()
        for _ in range(iterations):
            compare(actual, expected, "replay")
        profile.disable()
        stats = pstats.Stats(profile)
        normalization_calls = sum(row[1] for key, row in stats.stats.items() if key[2] in ("_plain", "_legacy_plain"))
        measurements[name] = {"cpu_seconds_unprofiled": elapsed, "profile_total_calls": stats.total_calls,
                              "normalization_calls": normalization_calls}
    return {"iterations": iterations, "invented_symbols": len(plans), "snapshots": len(result.snapshots),
            "events": len(result.events), "comparison": measurements}
