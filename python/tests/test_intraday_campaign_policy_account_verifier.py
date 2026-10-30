"""Invented-only independent account publication and callback tamper cases.

The comparison is the subject under test. Its helpers never provide oracle
expectations; the raw original-schema fixture and hand-derived cash facts do.
"""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_campaign_independent as account_oracle
from research import intraday_campaign_policy_account_verifier as oracle
from research import intraday_campaign_policy_comparison as subject
from test_intraday_campaign_context_readiness import project
from test_intraday_campaign_policy_comparison import fixture


def execute(a, b, chunks, policy="P06"):
    pa, pb = project(a, b)
    return subject.build_comparison(pa, pb, iter(chunks), policy_id=policy,
        inputs_authenticated=True, authentication_reference="invented-oracle-original-union")


@pytest.fixture(scope="module")
def publication():
    captured = []
    base = oracle.PolicyAccountVerifier

    class Capture(base):
        def finish_verify(self, report):
            # Preserve the completed independent callbacks immediately before
            # the real finisher, which always runs and must pass normally.
            captured.append(deepcopy(self))
            return super().finish_verify(report)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(oracle, "PolicyAccountVerifier", Capture)
        a, b, chunks = fixture()
        report = execute(a, b, chunks)
    return report, captured[0]


def test_complete_independent_p06_hand_derived_accounts(publication):
    report, verifier = deepcopy(publication)
    receipt = verifier.finish_verify(report)
    assert receipt["verified"] is True
    assert receipt["day_replays"] == 32
    assert "raw_causal_feature_reconstruction" in receipt["verified_components"]
    assert "source_byte_authentication" in receipt["unverified"]
    assert "historical_market_availability" in receipt["unverified"]
    assert receipt["statistical_float_tolerance"] == {"relative": 1e-12, "absolute": 1e-15}
    for timing in ("D+1", "D+2"):
        assert report["interaction_cell_counts"][timing] == {"neither": 1, "volume": 1, "market": 1, "both": 1}
        for cost in (33, 43):
            for arm, fills in (("control", 4), ("volume_only", 2), ("market_only", 2), ("selected", 1)):
                day = report["scenarios"][f"{timing}|{cost}|{arm}"]["day_local_counterfactual"]["days"][0]
                assert day["known_entry_count"] == fills
                assert day["gross_pnl_same_fills"] == 0
                # Each same-clock fee reduces the next exact 2%-NAV budget.
                quantities = {33: (20_000, 19_998, 19_997, 19_996),
                              43: (20_000, 19_998, 19_996, 19_994)}[cost]
                assert day["assumed_cost"] == Fraction(sum(quantities[:fills]) * cost, 100)
                assert day["net_pnl"] == -day["assumed_cost"]


@pytest.mark.parametrize("mutate,match", [
    (lambda report: report["selection_rows"]["D+1"].pop(), "verified_original_rows"),
    (lambda report: report["selection_rows"]["D+1"][0].update(source_index=1), "verified_original_rows"),
    (lambda report: report["selection_rows"]["D+1"][0]["selection"].update(state="rejected"), "verified_original_rows"),
    (lambda report: report["selection_rows"]["D+1"][0]["feature_evidence"].update(volume_value=2), "verified_original_rows"),
    (lambda report: report["selection_rows"]["D+1"][0]["feature_evidence"]["common_peer_source_indices"].pop(), "verified_original_rows"),
    (lambda report: report["reference_sessions"].append("2025-09-05"), "reference_sessions"),
    (lambda report: report["reference_weeks"].append("2025-09-08"), "reference_weeks"),
    (lambda report: report["sources"][0].update(input_rows=0), "sources"),
    (lambda report: report["source_target_matrix"]["D+1"]["days"][0]["targets"].pop(), "source_target_matrix"),
    (lambda report: report.update(consumed_price_rows=0), "consumed_price_rows"),
    (lambda report: report.update(day_replay_count=16), "day_replay_count"),
    (lambda report: report["interaction_cell_counts"]["D+1"].update(both=0), "interaction_cell_counts"),
    (lambda report: report["scenarios"].pop("D+2|43|market_only"), "scenario_keys"),
    (lambda report: report["scenarios"]["D+1|33|control"]["continuous"]["days"][0]["coverage"].update(eligible_control_candidates=3), "scenario.days"),
    (lambda report: report["scenarios"]["D+1|33|control"]["continuous"]["own_sizing"]["net_pnl"].update(known_labels=0), "scenario.own_sizing"),
    (lambda report: report["scenarios"]["D+1|33|control"]["continuous"]["summary"].update(mean_daily_net_pnl=Fraction(0)), "scenario.summary"),
    (lambda report: report["comparisons"]["D+1|33"]["paired_sizing"].pop("selected_minus_market_only"), "comparisons"),
    (lambda report: report["comparisons"]["D+1|33"].update(common_sessions=[]), "comparisons"),
    (lambda report: report.update(historical_brokerage_account_claim=True), "historical_brokerage_account_claim"),
])
def test_finisher_refuses_retained_population_plans_or_summary_tampering(publication, mutate, match):
    report, verifier = deepcopy(publication)
    mutate(report)
    with pytest.raises(ValueError, match=match):
        verifier.finish_verify(report)


@pytest.mark.parametrize("change", ["feature", "decision", "volume", "candidate", "candidate_dict", "auxiliary", "compact_source", "original"])
def test_row_checks_reconstruct_from_originals_before_replay(monkeypatch, change):
    base = oracle.PolicyAccountVerifier

    class Tamper(base):
        def verify_selection_row(self, **payload):
            if payload["original_row"]["signal_state"] == "signal":
                if change == "feature":
                    prepared = payload["prepared"]
                    values = dict(prepared.features)
                    values["target_open_to_signal_return"] = replace(values["target_open_to_signal_return"], value=Fraction(99))
                    payload["prepared"] = replace(prepared, features=values)
                elif change == "decision":
                    payload["decision"] = {**payload["decision"], "state": "rejected"}
                elif change == "volume":
                    payload["volume_value"] += 1
                elif change == "candidate":
                    candidate = payload["candidate"]
                    payload["candidate"] = replace(candidate, entry_at=candidate.entry_at + oracle.cc.MINUTE)
                elif change == "candidate_dict":
                    payload["candidate"] = account_oracle._plain(payload["candidate"])
                elif change == "auxiliary":
                    payload["auxiliary_decisions"] = {**payload["auxiliary_decisions"],
                        "P04": {**payload["auxiliary_decisions"]["P04"], "state": "rejected"}}
                elif change == "compact_source":
                    payload["compact_row"]["feature_evidence"]["common_peer_source_indices"][0] = 0
                else:
                    payload["original_row"] = {**payload["original_row"], "source_index": 1}
            return super().verify_selection_row(**payload)

    monkeypatch.setattr(oracle, "PolicyAccountVerifier", Tamper)
    a, b, chunks = fixture()
    with pytest.raises(ValueError):
        execute(a, b, chunks)


@pytest.mark.parametrize("change", ["candidate_subset", "coverage", "raw_source", "unregistered_arm"])
def test_day_checks_refuse_caller_candidates_or_coverage_as_oracle(monkeypatch, change):
    base = oracle.PolicyAccountVerifier

    class Tamper(base):
        def verify_selection_row(self, **payload):
            if change == "raw_source":
                payload["day_input"]["original_sources"]["KRX:000001"]["data_vintage"] = "0" * 64
            return super().verify_selection_row(**payload)

        def verify_day(self, **payload):
            if change == "candidate_subset":
                payload["candidates"] = payload["candidates"][:-1]
            elif change == "coverage":
                payload["compact"]["coverage"]["eligible_control_candidates"] -= 1
            elif change == "unregistered_arm":
                payload["arm"] = "unchecked"
            return super().verify_day(**payload)

    monkeypatch.setattr(oracle, "PolicyAccountVerifier", Tamper)
    a, b, chunks = fixture()
    with pytest.raises(ValueError):
        execute(a, b, chunks)


def test_source_verifier_never_uses_core_three_account_finisher(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("core-three-only finisher called")

    monkeypatch.setattr(account_oracle.Verifier, "finish_verify", forbidden)
    a, b, chunks = fixture()
    result = execute(a, b, chunks, policy="P08")
    assert result["independent_verification"]["verified"] is True
    assert len(result["selection_rows"]["D+1"]) == 40
    assert result["scenarios"]["D+1|33|control"]["mode"] == "A+B"


def test_incomplete_independent_callback_family_cannot_finish(publication):
    report, verifier = deepcopy(publication)
    verifier.oracles["market_only"]._days.pop(("D+2", 43, "A", "continuous"))
    with pytest.raises(ValueError, match="verified_scenarios"):
        verifier.finish_verify(report)


@pytest.mark.parametrize("change", ["source", "retention", "clock"])
def test_finisher_rechecks_each_independent_raw_receipt(publication, change):
    report, verifier = deepcopy(publication)
    independent = verifier.oracles["market_only"]
    session = report["reference_sessions"][0]
    if change == "source":
        independent._sources[session]["sources"][0]["input_rows"] = 0
    elif change == "retention":
        independent._sources[session]["retained_price_symbols"].pop()
    else:
        opening, closing = independent._clocks[session]
        independent._clocks[session] = (opening + oracle.cc.MINUTE, closing)
    with pytest.raises(ValueError, match="original_source_receipt|original_session_clocks"):
        verifier.finish_verify(report)


def test_all_independent_own_and_paired_plans_precede_any_summary(publication, monkeypatch):
    report, verifier = deepcopy(publication)
    sizing, summary = account_oracle._sizing, account_oracle._summary
    calls = {"sizing": 0, "summaries": 0}

    def measured_sizing(*args, **kwargs):
        assert calls["summaries"] == 0
        calls["sizing"] += 1
        return sizing(*args, **kwargs)

    def measured_summary(*args, **kwargs):
        # 32 scopes * two labels, plus four common comparisons, each with
        # four arms * two labels and five fixed paired plans.
        assert calls["sizing"] == 32 * 2 + 4 * (4 * 2 + 5)
        calls["summaries"] += 1
        return summary(*args, **kwargs)

    monkeypatch.setattr(account_oracle, "_sizing", measured_sizing)
    monkeypatch.setattr(account_oracle, "_summary", measured_summary)
    assert verifier.finish_verify(report)["verified"] is True
    assert calls["summaries"] == 32 + 4 * 4


def test_unknown_required_feature_cannot_enter_p06_single_condition_arm():
    a, b, chunks = fixture(missing=((0, "KRX:000001", 10),))
    report = execute(a, b, chunks)
    for timing in ("D+1", "D+2"):
        first = report["selection_rows"][timing][0]
        assert first["selection"]["state"] == "unknown"
        assert first["auxiliary_decisions"] == {}
        assert first["interaction_cell"] is None
        for arm in ("control", "volume_only", "market_only", "selected"):
            coverage = report["scenarios"][f"{timing}|33|{arm}"]["continuous"]["days"][0]["coverage"]
            assert coverage["unknown_feature_units"] == 1
            assert coverage["eligible_control_candidates"] == 3
