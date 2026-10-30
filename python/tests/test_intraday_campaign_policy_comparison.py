"""Invented original report shapes and prices only; no runner or market IO."""

from copy import deepcopy
from datetime import datetime
from fractions import Fraction
import weakref

import pytest

from research import intraday_campaign_policy_comparison as paired
from test_intraday_campaign_context_readiness import bind_sources, fixture as context_fixture, project


def fixture(sessions=1, *, missing=(), zero_days=()):
    """Four original A/B signals; A cells both/volume/market/neither."""
    a, b, chunks = context_fixture(sessions=sessions)
    dates = a["reference_sessions"]
    for chunk in chunks:
        day_index = dates.index(chunk["session"])
        opening = paired.cc.session_open(chunk["session"])
        template = chunk["rows"][0]
        symbol_index = int(chunk["symbol"].split(":")[1]) - 1
        signal_close = "102" if symbol_index in (0, 2) else "100" if symbol_index in (1, 3) else "101"
        chunk["rows"] = [{**template,
            "open_time_ms": int((opening + minute * paired.cc.MINUTE).timestamp()) * 1000,
            "open": "100", "high": "103" if minute < 75 else "100",
            "low": "99" if minute < 75 else "100", "close": signal_close if minute < 75 else "100"}
            for minute in range(141) if (day_index, chunk["symbol"], minute) not in missing]
    for original in (a, b):
        for variant in original["variants"].values():
            for row in variant["days"]:
                index = int(row["symbol"].split(":")[1]) - 1
                day_index = dates.index(row["session"])
                if index < 4 and day_index not in zero_days:
                    row["structural_stop"] = {"numerator": "1", "denominator": "1"}
                    row["outcome"].update(status="unknown", reason="required_path_missing", exit_at=None)
                    row["common_price_observable"] = False
                    if original["setup_id"] == "EA-A":
                        row["features"]["relative_volume"] = {
                            "value": {"numerator": "2" if index < 2 else "1", "denominator": "1"}, "reasons": []}
                        row["arms"]["volume_only"] = "selected" if index < 2 else "rejected"
                    continue
                if row["signal"]["state"] == "signal":
                    row["signal"].update(state="known_no_signal", reason="initial_condition_false",
                        signal_bar_start=None, signal_bar_end=None, setup_bar_end=None)
                    row.update(entry_at=None, cap_at=None, structural_stop=None, outcome=None, features={},
                        arms=dict.fromkeys(row["arms"], "rejected"), common_context_observable=False,
                        common_price_observable=False)
    bind_sources(a, b, chunks)
    return a, b, chunks


def build(a, b, chunks, policy_id="P06", **kwargs):
    pa, pb = project(a, b)
    return paired.build_comparison(pa, pb, iter(chunks), policy_id=policy_id,
        inputs_authenticated=True, authentication_reference="invented-paired-original-union", **kwargs)


def signals(report, timing="D+1"):
    return [row for row in report["selection_rows"][timing] if row["signal_state"] == "signal"]


def test_p06_four_matched_accounts_five_pairs_and_compact_original_evidence():
    a, b, chunks = fixture()
    before = deepcopy((a, b, chunks))
    stages = []
    report = build(a, b, chunks, checkpoint=lambda stage, payload: stages.append((stage, payload)))
    assert (a, b, chunks) == before
    assert report["independent_verification"]["verified"] is True
    assert report["day_replay_count"] == 32
    assert report["consumed_reference_sessions"] == 1
    assert report["consumed_target_chunks"] == 20
    assert report["consumed_price_rows"] == 20 * 141
    assert report["original_union_complete"] is report["idle_minutes_compressed"] is True
    assert [stage for stage, _ in stages] == ["day_complete", "sizing_complete", "summary_complete"]
    assert stages[0][1]["day_replays"] == 32
    expected_entries = {"control": 4, "volume_only": 2, "market_only": 2, "selected": 1}
    expected_pairs = {"selected_minus_control", "volume_only_minus_control", "market_only_minus_control",
                      "selected_minus_volume_only", "selected_minus_market_only"}
    for timing in paired.POLICIES:
        assert len(report["selection_rows"][timing]) == 20
        assert [row["interaction_cell"] for row in signals(report, timing)] == ["both", "volume", "market", "neither"]
        assert report["interaction_cell_counts"][timing] == dict.fromkeys(paired.CELLS, 1)
        for cost in paired.COSTS:
            for arm, entries in expected_entries.items():
                scenario = report["scenarios"][f"{timing}|{cost}|{arm}"]
                assert scenario["mode"] == "A" and scenario["arm"] == arm
                for scope in paired.SCOPES:
                    day = scenario[scope]["days"][0]
                    assert day["status"] == "identified"
                    assert day["known_entry_count"] == day["known_exit_count"] == entries
                    assert day["net_pnl"] == -day["assumed_cost"]
                    assert day["gross_pnl_same_fills"] == 0
                    assert day["coverage"]["eligible_control_candidates"] == 4
                    assert day["coverage"]["eligible_arm_candidates"] == entries
                    assert day["coverage"]["by_setup"]["EA-A"] == {"targets": 20, "signals": 4,
                        "known_no_signal": 14, "unknown_signals": 2, "clock_ineligible": 0,
                        "feature_unknown": 0, "support_observed": 4}
            contrast = report["comparisons"][f"{timing}|{cost}"]
            assert set(contrast["paired_sizing"]) == set(contrast["paired_mean_daily_net_per_initial_capital"]) == expected_pairs
            assert contrast["scope"] == "support_matched_common_identified_fresh_daily_accounts"
            assert contrast["common_sessions"] == report["reference_sessions"]
            assert contrast["paired_mean_daily_net_per_initial_capital"]["selected_minus_control"] > 0
    first = signals(report)[0]
    assert first["decision_at"] == a["variants"]["D+1"]["days"][0]["signal"]["decision_at"]
    assert first["entry_at"] == paired.cc.session_open(first["session"]) + 80 * paired.cc.MINUTE
    assert first["cap_exit_at"] == first["entry_at"] + 60 * paired.cc.MINUTE
    evidence = first["feature_evidence"]
    assert set(evidence) == {"proxy_id", "authentication_reference", "common_peer_source_indices", "required_values", "volume_value"}
    assert evidence["volume_value"] == Fraction(2)
    assert len(evidence["common_peer_source_indices"]) == 19
    assert set(evidence["required_values"]) == set(paired.selection.MARKET_A_FEATURES)
    assert all(value["available_at"] == datetime.fromisoformat(first["decision_at"])
               for value in evidence["required_values"].values())
    assert report["performance_computed"] is True
    for flag in ("strategy_pass", "promotion_allowed", "historical_brokerage_account_claim",
                 "actual_execution_authorized", "historical_timing_certified", "volume_availability_reauthenticated"):
        assert report[flag] is False


@pytest.mark.parametrize("policy_id,mode,setups,selected", [
    ("P04", "A", {"EA-A"}, 2), ("P05", "A", {"EA-A"}, 2),
    ("P07", "B", {"EA-B"}, 0), ("P08", "A+B", {"EA-A", "EA-B"}, 4),
])
def test_fixed_units_reuse_original_solo_or_joint_accounts(policy_id, mode, setups, selected):
    report = build(*fixture(), policy_id=policy_id)
    assert report["day_replay_count"] == 16
    assert len(report["scenarios"]) == 8
    assert {row["setup_id"] for row in report["selection_rows"]["D+1"]} == setups
    assert report["interaction_cell_counts"] == {}
    day = report["scenarios"]["D+1|33|selected"]["day_local_counterfactual"]["days"][0]
    assert day["known_entry_count"] == selected
    assert all(content["mode"] == mode for content in report["scenarios"].values())
    assert set(report["comparisons"]["D+1|33"]["paired_sizing"]) == {"selected_minus_control"}
    assert all(not row["auxiliary_decisions"] and row["interaction_cell"] is None
               for row in report["selection_rows"]["D+1"])
    if policy_id not in {"P04", "P06"}:
        assert all(row["feature_evidence"]["volume_value"] is None for row in signals(report))
    if policy_id == "P08":
        assert {row["selection"]["router_state"] for row in signals(report)} == {"A"}
        first30 = signals(report)[0]["feature_evidence"]["required_values"]["peer_first30_return"]
        assert first30["observation_end"] == paired.cc.session_open(report["reference_sessions"][0]) + 30 * paired.cc.MINUTE
        assert first30["available_at"] == datetime.fromisoformat(signals(report)[0]["decision_at"])
        assert day["terminal"]["sleeves"][0]["initial_allocation"] == paired.cash.INITIAL_CAPITAL / 2


@pytest.mark.parametrize("missing_kind", ["volume", "target_prefix", "peer_quorum"])
def test_p06_missing_required_input_excludes_all_four_arms_without_erasing_original(missing_kind):
    a, b, chunks = fixture()
    if missing_kind == "volume":
        for variant in a["variants"].values():
            row = variant["days"][0]
            row["features"]["relative_volume"] = {"value": None, "reasons": ["missing_current_volume"]}
            row["arms"]["volume_only"] = "unknown"
            row.update(common_context_observable=False, common_price_observable=False)
    else:
        affected = chunks[:1] if missing_kind == "target_prefix" else chunks[5:]
        for chunk in affected:
            chunk["rows"].pop(10)
        bind_sources(a, b, chunks)
    report = build(a, b, chunks)
    first = signals(report)[0]
    assert first["selection"]["state"] == "unknown"
    assert first["clock_eligible"] is True
    assert first["interaction_cell"] is None and first["auxiliary_decisions"] == {}
    assert len(report["selection_rows"]["D+1"]) == 20
    for arm in paired.UNIT_DEFINITIONS["P06"]["arms"]:
        day = report["scenarios"][f"D+1|33|{arm}"]["day_local_counterfactual"]["days"][0]
        assert day["coverage"]["unknown_feature_units"] >= 1
        assert not any(event["symbol"] == first["symbol"] for event in day["events"])


def test_nonsignals_unknowns_and_original_row_order_are_retained():
    a, b, chunks = fixture()
    for original in (a, b):
        for variant in original["variants"].values():
            variant["days"].reverse()
    report = build(a, b, chunks, policy_id="P08")
    expected_keys = {"session", "symbol", "setup_id", "original_row_index", "membership_day_index",
        "source_index", "signal_state", "signal_reason", "observed_through", "decision_at",
        "clock_eligible", "entry_at", "cap_exit_at", "selection", "auxiliary_decisions",
        "interaction_cell", "feature_evidence"}
    for timing in paired.POLICIES:
        rows = report["selection_rows"][timing]
        expected = [(original["setup_id"], index, row["symbol"]) for original in (a, b)
                    for index, row in enumerate(original["variants"][timing]["days"])]
        assert [(row["setup_id"], row["original_row_index"], row["symbol"]) for row in rows] == expected
        for row in rows:
            assert set(row) == expected_keys
            if row["signal_state"] != "signal":
                assert all(row[name] is None for name in ("selection", "clock_eligible", "entry_at",
                    "cap_exit_at", "interaction_cell", "feature_evidence"))
                assert row["auxiliary_decisions"] == {}


def test_p04_observed_volume_admits_candidate_even_when_unrelated_market_inputs_are_unknown():
    a, b, chunks = fixture()
    for chunk in chunks:
        chunk["rows"].pop(10)
    bind_sources(a, b, chunks)
    report = build(a, b, chunks, policy_id="P04")
    assert signals(report)[0]["selection"]["state"] == "selected"
    assert signals(report)[0]["feature_evidence"]["required_values"] == {}
    for arm, count in (("control", 4), ("selected", 2)):
        day = report["scenarios"][f"D+1|33|{arm}"]["day_local_counterfactual"]["days"][0]
        assert day["known_entry_count"] == count
        assert day["coverage"]["unknown_feature_units"] == 0
        assert day["coverage"]["eligible_arm_candidates"] == count


def test_no_original_detector_old_outcomes_or_future_price_admission(monkeypatch):
    a, b, chunks = fixture()
    baseline = build(a, b, chunks)

    def prohibited(*args, **kwargs):
        raise AssertionError("old detector or outcome cannot be replayed")

    monkeypatch.setattr(paired.eb.signals, "detect_bars", prohibited)
    monkeypatch.setattr(paired.eb.outcomes, "exploratory_outcome", prohibited)
    for original in (a, b):
        for variant in original["variants"].values():
            variant["statistics"] = object()
            for row in variant["days"]:
                if row["outcome"] is not None:
                    row["outcome"].update(gross=object(), primary_net=object(), entry_open=object(), exit_open=object())
    poisoned = build(a, b, chunks)
    assert poisoned == baseline
    for chunk in chunks:
        opening = paired.cc.session_open(chunk["session"])
        chunk["rows"] = [row for row in chunk["rows"]
                         if row["open_time_ms"] < int((opening + 80 * paired.cc.MINUTE).timestamp()) * 1000]
    bind_sources(a, b, chunks)
    unavailable = build(a, b, chunks)
    assert unavailable["selection_rows"] == baseline["selection_rows"]
    assert unavailable["comparisons"]["D+1|33"]["common_sessions"] == []
    assert unavailable["comparisons"]["D+1|33"]["paired_mean_daily_net_per_initial_capital"]["selected_minus_control"] is None


def test_missing_held_mark_freezes_continuous_without_restore_but_daily_recovers():
    a, b, chunks = fixture(3, missing={(0, "KRX:000001", 90)})
    report = build(a, b, chunks)
    for arm in paired.UNIT_DEFINITIONS["P06"]["arms"]:
        scenario = report["scenarios"][f"D+1|33|{arm}"]
        continuous = scenario["continuous"]["days"]
        fresh = scenario["day_local_counterfactual"]["days"]
        assert [row["status"] for row in continuous] == ["indeterminate"] * 3
        unknown = continuous[0]["terminal"]["unknown_at"]
        assert all(row["terminal"]["unknown_at"] == unknown for row in continuous)
        assert [row["status"] for row in fresh] == ["indeterminate", "identified", "identified"]
        assert fresh[1]["start_nav"] == fresh[2]["start_nav"] == paired.cash.INITIAL_CAPITAL
    result = report["comparisons"]["D+1|33"]
    assert result["excluded_sessions"] == a["reference_sessions"][:1]
    assert result["common_sessions"] == a["reference_sessions"][1:]
    assert report["consumed_target_chunks"] == 60 and report["original_union_complete"]


def test_zero_signal_dates_and_weeks_are_identified_zero_not_dropped():
    a, b, chunks = fixture(3, zero_days=(0, 1, 2))
    report = build(a, b, chunks)
    assert len(report["reference_weeks"]) == 2
    assert len(report["selection_rows"]["D+1"]) == 60
    assert report["interaction_cell_counts"]["D+1"] == dict.fromkeys(paired.CELLS, 0)
    for scenario in report["scenarios"].values():
        for scope in paired.SCOPES:
            content = scenario[scope]
            assert all(row["status"] == "identified" and row["net_pnl"] == 0 for row in content["days"])
            assert content["summary"]["complete_zero_trade_days"] == 3
            assert set(content["summary"]["by_week"]) == set(report["reference_weeks"])
            assert content["own_sizing"]["net_pnl"]["status"] == "uninformative"
            assert content["own_sizing"]["net_pnl"]["sizing_pass"] is False


def test_all_own_sizing_precedes_every_public_summary(monkeypatch):
    calls = []
    sizing, summary = paired.comparison.own_sizing, paired.comparison.summarize_days

    def size(*args, **kwargs):
        calls.append("sizing")
        return sizing(*args, **kwargs)

    def mean(*args, **kwargs):
        calls.append("summary")
        return summary(*args, **kwargs)

    monkeypatch.setattr(paired.comparison, "own_sizing", size)
    monkeypatch.setattr(paired.comparison, "summarize_days", mean)
    build(*fixture())
    # 16 scenarios * two scopes * two labels, plus 4 common comparisons *
    # (four arms * two labels + five paired net-label plans).
    assert calls.count("sizing") == 116
    assert calls == ["sizing"] * 116 + ["summary"] * 48


def test_strict_next_five_candidate_and_late_cap_do_not_use_prices():
    opening = paired.cc.session_open("2025-09-04")
    closing = paired.cc.session_close("2025-09-04")
    original = {"symbol": "KRX:000001", "decision_at": (opening + 80 * paired.cc.MINUTE).isoformat(),
                "structural_stop": Fraction(7)}
    eligible, entry, cap, candidate = paired._candidate(original, "EA-A", opening, closing)
    assert eligible and entry == opening + 85 * paired.cc.MINUTE and cap == entry + 60 * paired.cc.MINUTE
    assert candidate.structural_stop == 7 and candidate.decision_at == opening + 80 * paired.cc.MINUTE
    original["decision_at"] = (closing - 62 * paired.cc.MINUTE).isoformat()
    eligible, entry, cap, candidate = paired._candidate(original, "EA-A", opening, closing)
    assert not eligible and cap == closing and candidate is None


def test_clock_ineligible_original_signal_retains_observed_support_and_computed_clocks():
    a, b, chunks = fixture()
    for chunk in chunks:
        opening = paired.cc.session_open(chunk["session"])
        template = chunk["rows"][-1]
        chunk["rows"].extend({**template,
            "open_time_ms": int((opening + minute * paired.cc.MINUTE).timestamp()) * 1000}
            for minute in range(141, 326))
    for timing in paired.POLICIES:
        row = a["variants"][timing]["days"][0]
        opening = paired.cc.session_open(row["session"])
        end = opening + 325 * paired.cc.MINUTE
        entry = end + 5 * paired.cc.MINUTE
        cap = entry + 60 * paired.cc.MINUTE
        row["signal"].update(observed_through=end.isoformat(), signal_bar_start=(end - 5 * paired.cc.MINUTE).isoformat(),
            signal_bar_end=end.isoformat(), setup_bar_end=(end - 5 * paired.cc.MINUTE).isoformat(),
            decision_at=(end + 2 * paired.cc.MINUTE).isoformat())
        row.update(entry_at=entry.isoformat(), cap_at=cap.isoformat(),
            features={"clock": {"value": None, "reasons": ["same_day_cap_infeasible"]}},
            arms=dict.fromkeys(row["arms"], "unknown"), common_context_observable=False,
            common_price_observable=False)
        row["outcome"].update(status="unknown", reason="same_day_cap_infeasible", entry_at=entry.isoformat(), exit_at=None)
    bind_sources(a, b, chunks)
    report = build(a, b, chunks, policy_id="P05")
    first = signals(report)[0]
    assert first["clock_eligible"] is False
    assert first["cap_exit_at"] == paired.cc.session_close(first["session"])
    assert first["selection"]["state"] in {"selected", "rejected"}
    day = report["scenarios"]["D+1|33|control"]["day_local_counterfactual"]["days"][0]
    assert day["coverage"]["clock_ineligible_units"] == 1
    assert day["coverage"]["eligible_control_candidates"] == 3
    assert day["coverage"]["by_setup"]["EA-A"]["support_observed"] == 4
    assert not any(event["symbol"] == first["symbol"] for event in day["events"])


def test_p08_observed_b_branch_has_priority_on_same_original_joint_half_sleeves():
    a, b, chunks = fixture()
    for chunk in chunks:
        chunk["rows"][29]["close"] = "99"
    bind_sources(a, b, chunks)
    report = build(a, b, chunks, policy_id="P08")
    assert {row["selection"]["router_state"] for row in signals(report)} == {"B"}
    for row in signals(report):
        assert (row["selection"]["state"] == "selected") is (row["setup_id"] == "EA-B")
    day = report["scenarios"]["D+1|33|selected"]["day_local_counterfactual"]["days"][0]
    assert day["known_entry_count"] == 4
    assert {event["strategy"] for event in day["events"]} == {"B"}
    assert all(sleeve["initial_allocation"] == paired.cash.INITIAL_CAPITAL / 2
               for sleeve in day["terminal"]["sleeves"])


def test_checkpoint_failure_propagates_without_reading_next_day_or_retry():
    a, b, chunks = fixture(2)
    pa, pb = project(a, b)
    consumed = []

    def stream():
        for chunk in chunks:
            consumed.append((chunk["session"], chunk["symbol"]))
            yield chunk

    def stop(stage, payload):
        if stage == "day_complete":
            raise RuntimeError("invented budget stop")

    with pytest.raises(RuntimeError, match="invented budget stop"):
        paired.build_comparison(pa, pb, stream(), policy_id="P06", inputs_authenticated=True,
            authentication_reference="invented", checkpoint=stop)
    assert len(consumed) == 20


def test_day_price_sessions_are_released_before_consuming_next_day(monkeypatch):
    a, b, chunks = fixture(2)
    adapter = paired.account_inputs.iter_context_price_days
    previous = []

    def stream(*args, **kwargs):
        for day in adapter(*args, **kwargs):
            assert all(ref() is None for ref in previous)
            previous[:] = [weakref.ref(session) for session in day["original_sessions"].values()]
            yield day
            del day

    monkeypatch.setattr(paired.account_inputs, "iter_context_price_days", stream)
    report = build(a, b, chunks)
    assert report["consumed_reference_sessions"] == 2


@pytest.mark.parametrize("damage", ["missing", "extra", "source"])
def test_entire_original_union_and_eof_are_authenticated_before_success(damage):
    a, b, chunks = fixture()
    if damage == "missing":
        chunks.pop()
    elif damage == "extra":
        chunks.append(deepcopy(chunks[-1]))
    else:
        chunks[-1]["rows"][-1]["close"] = "101"
    with pytest.raises(paired.account_inputs.AccountInputsRefusal):
        build(a, b, chunks)


@pytest.mark.parametrize("changes", [{"policy_id": "P09"}, {"inputs_authenticated": False},
                                    {"authentication_reference": ""}, {"checkpoint": 3}])
def test_malformed_public_contract_is_refused_without_price_consumption(changes):
    a, b, _ = fixture()
    pa, pb = project(a, b)

    def stream():
        raise AssertionError("price stream consumed before contract validation")
        yield

    options = {"policy_id": "P06", "inputs_authenticated": True, "authentication_reference": "invented"}
    options.update(changes)
    with pytest.raises((paired.PolicyComparisonRefusal, paired.readiness.ContextReadinessRefusal)):
        paired.build_comparison(pa, pb, stream(), **options)
