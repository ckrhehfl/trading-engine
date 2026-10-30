"""Original H4/H5 full-target account producer, with invented data only."""

from copy import deepcopy
from fractions import Fraction
import sys
from types import SimpleNamespace
import weakref

import pytest

from research import intraday_campaign_h45_account as producer
from test_intraday_campaign_context_readiness import bind_sources, fixture as original_fixture, project


def fixture(sessions=1, *, pattern="H4", missing=(), churn=False):
    a, b, chunks = original_fixture(sessions=sessions, churn=churn)
    dates = a["reference_sessions"]
    for chunk in chunks:
        day_index = dates.index(chunk["session"])
        opening = producer.cc.session_open(chunk["session"])
        template = chunk["rows"][0]
        rows = []
        for minute in range(390):
            if (day_index, chunk["symbol"], minute) in missing:
                continue
            op = hi = lo = cl = "100"
            if chunk["symbol"] == "KRX:000006":
                if pattern == "H4":
                    if minute < 30:
                        hi, lo = "110", "90"
                    elif minute < 85:
                        hi, lo = "102", "98"
                    elif minute < 90:
                        hi, lo, cl = "104", "99", "103"
                elif pattern == "H5":
                    if minute < 30:
                        hi, lo, cl = "102", "99", "101"
                    elif minute < 315:
                        hi, lo, cl = "104", "99", "103"
            rows.append({**template, "open_time_ms": int((opening + minute * producer.cc.MINUTE).timestamp()) * 1000,
                         "open": op, "high": hi, "low": lo, "close": cl})
        chunk["rows"] = rows
    bind_sources(a, b, chunks)
    return a, b, chunks


def build(a, b, chunks, policy_id="P11", **kwargs):
    pa, pb = project(a, b)
    return producer.build_comparison(pa, pb, iter(chunks), policy_id=policy_id,
        inputs_authenticated=True, authentication_reference="invented-h45-original-union", **kwargs)


class VerifierSpy:
    """Producer-boundary spy, not an independent verification substitute."""
    def __init__(self, policy_id, a, b, *, authentication_reference):
        self.rows = self.replays = 0
    def verify_signal_row(self, **kwargs):
        self.rows += 1
    def verify_day(self, **kwargs):
        self.replays += 1
    def finish_verify(self, report):
        return {"verified": False, "producer_spy_only": True,
                "signal_rows": self.rows, "day_replays": self.replays}


@pytest.fixture
def producer_spy(monkeypatch):
    monkeypatch.setitem(sys.modules, "research.intraday_campaign_h45_account_verifier",
                        SimpleNamespace(H45AccountVerifier=VerifierSpy))


@pytest.mark.parametrize("policy_id,pattern,mode,entry_offset", [
    ("P11", "H4", "H4", 95), ("P12", "H5", "H5", 315)])
def test_original_pattern_outside_old_ab_pool_is_detected_and_accounted(
        producer_spy, policy_id, pattern, mode, entry_offset):
    a, b, chunks = fixture(pattern=pattern)
    before = deepcopy((a, b, chunks))
    stages = []
    report = build(a, b, chunks, policy_id, checkpoint=lambda stage, payload: stages.append((stage, payload)))
    assert (a, b, chunks) == before
    assert report["day_replay_count"] == 8
    assert report["independent_verification"] == {"verified": False, "producer_spy_only": True,
                                                "signal_rows": 40, "day_replays": 8}
    assert report["consumed_target_chunks"] == 20 and report["consumed_price_rows"] == 7800
    assert report["original_union_complete"] is True
    assert [stage for stage, _ in stages] == ["day_complete", "sizing_complete", "summary_complete"]
    assert stages[1][1] == {"scenario_scopes": 8, "own_label_planning_count": 24}
    for timing in producer.POLICIES:
        rows = report["signal_rows"][timing]
        assert len(rows) == 20
        row = next(row for row in rows if row["symbol"] == "KRX:000006")
        assert a["variants"][timing]["days"][5]["signal"]["state"] == "known_no_signal"
        assert row["signal_state"] == "signal" and row["structural_stop"] is None
        assert row["entry_at"] == producer.cc.session_open(row["session"]) + entry_offset * producer.cc.MINUTE
        assert row["cap_exit_at"] == row["entry_at"] + 60 * producer.cc.MINUTE
        assert row["clock_eligible"] is True and row["clock_ineligible_reason"] is None
        assert report["signal_counts"][timing] == {"targets": 20, "signals": 1,
            "known_no_signal": 19, "unknown_signals": 0, "clock_ineligible": 0}
        for cost in producer.COSTS:
            scenario = report["scenarios"][f"{timing}|{cost}"]
            assert scenario["mode"] == mode
            for scope in producer.SCOPES:
                day = scenario[scope]["days"][0]
                assert day["status"] == "identified"
                assert day["known_entry_count"] == day["known_exit_count"] == 1
                assert day["gross_pnl_same_fills"] == 0
                assert day["net_pnl"] == -day["assumed_cost"]
                assert day["coverage"]["by_setup"][mode]["targets"] == 20
            contrast = report["comparisons"][f"{timing}|{cost}"]
            assert contrast["cash_reference_net_per_initial_capital"] == Fraction()
            assert contrast["paired_sizing"] == contrast["paired_mean_daily_net_per_initial_capital"] == {}
            assert contrast["common_sessions"] == report["reference_sessions"]


def test_all_own_plans_precede_every_profit_mean(producer_spy, monkeypatch):
    calls = []
    own, summary = producer.comparison.own_sizing, producer.comparison.summarize_days
    def plan(*args, **kwargs):
        calls.append("plan")
        return own(*args, **kwargs)
    def mean(*args, **kwargs):
        assert calls.count("plan") == 24
        calls.append("mean")
        return summary(*args, **kwargs)
    monkeypatch.setattr(producer.comparison, "own_sizing", plan)
    monkeypatch.setattr(producer.comparison, "summarize_days", mean)
    build(*fixture())
    assert calls == ["plan"] * 24 + ["mean"] * 12


def test_signal_missing_and_actual_price_missing_are_not_admission_masks(producer_spy):
    report = build(*fixture(missing=((0, "KRX:000006", 120), (0, "KRX:000007", 10))))
    rows = {row["symbol"]: row for row in report["signal_rows"]["D+1"]}
    assert rows["KRX:000006"]["signal_state"] == "signal"
    assert rows["KRX:000007"]["signal_state"] == "unknown"
    assert rows["KRX:000007"]["entry_at"] is None
    scenario = report["scenarios"]["D+1|33"]
    assert scenario["continuous"]["days"][0]["status"] == "indeterminate"
    assert scenario["day_local_counterfactual"]["days"][0]["status"] == "indeterminate"
    assert report["comparisons"]["D+1|33"]["common_sessions"] == []
    assert report["signal_counts"]["D+1"]["signals"] == 1
    assert report["signal_counts"]["D+1"]["unknown_signals"] == 1


def test_original_population_churn_and_zero_trade_dates_remain(producer_spy):
    a, b, chunks = fixture(sessions=2, churn=True)
    for chunk in chunks:
        if chunk["session"] == a["reference_sessions"][1]:
            chunk["rows"].pop(10)
    bind_sources(a, b, chunks)
    report = build(a, b, chunks)
    assert report["consumed_target_chunks"] == 42
    assert report["day_replay_count"] == 16
    for timing in producer.POLICIES:
        rows = report["signal_rows"][timing]
        assert len(rows) == 40 and report["signal_counts"][timing]["unknown_signals"] == 20
        assert rows[-1]["symbol"] == ("KRX:000020" if timing == "D+1" else "KRX:000021")
        assert len(report["scenarios"][f"{timing}|33"]["continuous"]["days"]) == 2


@pytest.mark.parametrize("bad", ["P01", "P04", "H4", None, True])
def test_unregistered_policy_is_rejected_before_price_consumption(producer_spy, bad):
    a, b, _ = fixture()
    def forbidden():
        raise AssertionError("price input accessed")
        yield
    with pytest.raises(producer.H45AccountRefusal, match="unregistered_policy"):
        build(a, b, forbidden(), bad)


def test_later_failure_is_not_partial_success(producer_spy):
    a, b, chunks = fixture(sessions=2)
    def check(stage, payload):
        if stage == "day_complete":
            raise RuntimeError("preserved checkpoint failure")
    with pytest.raises(RuntimeError, match="preserved checkpoint failure"):
        build(a, b, chunks, checkpoint=check)


def test_clock_ineligible_signal_is_retained_without_candidate():
    a, b, chunks = fixture()
    pa, pb = project(a, b)
    day = next(producer.account_inputs.iter_full_target_price_days(
        producer.readiness._account_plain(pa.account_projection),
        producer.readiness._account_plain(pb.account_projection), chunks))
    day["session_close"] = day["session_open"] + 150 * producer.cc.MINUTE
    target = a["source_target_matrix"]["D+1"]["days"][0]["targets"][5]
    row, candidate = producer._signal_row(day, target, 0, 5, producer.UNITS["P11"])
    assert row["signal_state"] == "signal" and candidate is None
    assert row["clock_eligible"] is False and row["clock_ineligible_reason"] == "same_day_cap_infeasible"


@pytest.mark.parametrize("policy_id,pattern", [("P11", "H4"), ("P12", "H5")])
def test_full_producer_uses_real_independent_verifier(policy_id, pattern):
    report = build(*fixture(pattern=pattern), policy_id=policy_id)
    receipt = report["independent_verification"]
    assert receipt["verified"] is True and receipt["day_replays"] == 8
    assert receipt["unit_work"]["own_label_planning_count"] == 24
    assert receipt["unit_work"]["scenario_scopes"] == 8
    assert receipt["unit_work"]["model_fits"] == receipt["unit_work"]["threshold_searches"] == 0


@pytest.mark.parametrize("policy_id,pattern,missing_minute", [
    ("P11", "H4", 120), ("P12", "H5", 340)])
def test_real_verifier_keeps_detected_signal_with_unknown_held_path(policy_id, pattern, missing_minute):
    report = build(*fixture(pattern=pattern, missing=((0, "KRX:000006", missing_minute),)),
                   policy_id=policy_id)
    assert report["independent_verification"]["verified"] is True
    assert report["signal_counts"]["D+1"]["signals"] == 1
    assert report["scenarios"]["D+1|33"]["continuous"]["days"][0]["status"] == "indeterminate"
    assert report["comparisons"]["D+1|33"]["common_sessions"] == []


def test_real_verifier_multiple_days_release_raw_day_references(monkeypatch):
    original = producer.account_inputs.iter_full_target_price_days
    refs = []
    class WeakDay(dict):
        pass
    def tracked(*args, **kwargs):
        for raw_day in original(*args, **kwargs):
            assert not refs or refs[-1]() is None
            wrapped = WeakDay(raw_day)
            refs.append(weakref.ref(wrapped))
            yield wrapped
            del wrapped, raw_day
    monkeypatch.setattr(producer.account_inputs, "iter_full_target_price_days", tracked)
    report = build(*fixture(sessions=2))
    assert report["independent_verification"]["verified"] is True
    assert report["day_replay_count"] == 16
    assert len(refs) == 2 and all(ref() is None for ref in refs)
