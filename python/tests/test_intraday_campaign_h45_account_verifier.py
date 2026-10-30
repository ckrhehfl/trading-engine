"""Invented raw-source, original-signal and full-account mutation checks."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_autonomous_signals as original_signals
from research import intraday_campaign_account_inputs as inputs
from research import intraday_campaign_h45_account as producer
from research import intraday_campaign_h45_account_verifier as oracle
from test_intraday_campaign_context_readiness import project
from test_intraday_campaign_h45_account import fixture


def execute(a, b, chunks, policy):
    pa, pb = project(a, b)
    return producer.build_comparison(pa, pb, iter(chunks), policy_id=policy,
        inputs_authenticated=True, authentication_reference="invented-h45-independent-union")


@pytest.fixture(scope="module", params=("P11", "P12"))
def publication(request):
    policy = request.param
    captured = []
    base = oracle.H45AccountVerifier
    class Capture(base):
        def finish_verify(self, report):
            captured.append(deepcopy(self))
            return super().finish_verify(report)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(oracle, "H45AccountVerifier", Capture)
        a, b, chunks = fixture(pattern="H4" if policy == "P11" else "H5")
        report = execute(a, b, chunks, policy)
    return report, captured[0]


def test_complete_original_account_and_independent_raw_receipt(publication):
    report, verifier = deepcopy(publication)
    receipt = verifier.finish_verify(report)
    assert receipt["verified"] is True and receipt["day_replays"] == 8
    assert receipt["unit_work"]["own_label_planning_count"] == 24
    assert receipt["unit_work"]["scenario_scopes"] == 8
    assert "raw_OHLC_and_unique_OPEN" in receipt["verified_components"]
    assert "raw_archive_to_Session_and_OPEN_parsing" not in receipt["unverified"]
    assert receipt["unit_work"]["arms"] == [report["arm"]]
    assert verifier._raw_day is verifier._detections is None
    for timing in oracle.POLICIES:
        assert report["signal_counts"][timing]["signals"] == 1
        for cost in (33, 43):
            result = report["scenarios"][f"{timing}|{cost}"]
            for scope in oracle.account_oracle.SCOPES:
                day = result[scope]["days"][0]
                assert day["known_entry_count"] == day["known_exit_count"] == 1
                assert day["net_pnl"] == -Fraction(2_000_000 * cost, 10000)
                assert day["gross_pnl_same_fills"] == 0


def test_independent_all_24_plans_precede_first_mean(publication, monkeypatch):
    report, verifier = deepcopy(publication)
    sizing, summary = oracle.account_oracle._sizing, oracle.account_oracle._summary
    calls = []
    def sized(*args, **kwargs):
        calls.append("plan")
        return sizing(*args, **kwargs)
    def summarized(*args, **kwargs):
        assert calls.count("plan") == 24
        calls.append("mean")
        return summary(*args, **kwargs)
    monkeypatch.setattr(oracle.account_oracle, "_sizing", sized)
    monkeypatch.setattr(oracle.account_oracle, "_summary", summarized)
    assert verifier.finish_verify(report)["verified"] is True
    assert calls[:24] == ["plan"] * 24
    assert calls[24:] == ["mean"] * 12


@pytest.mark.parametrize("change,match", [
    (lambda r: r["signal_rows"]["D+1"].pop(), "original_rows"),
    (lambda r: r["signal_rows"]["D+1"][5].update(signal_state="known_no_signal"), "original_rows"),
    (lambda r: r["signal_rows"]["D+1"][5].update(structural_stop=Fraction(1)), "original_rows"),
    (lambda r: r["signal_rows"]["D+1"][5].update(source_index=0), "original_rows"),
    (lambda r: r["signal_counts"]["D+1"].update(unknown_signals=0, signals=0), "signal_counts"),
    (lambda r: r["scenarios"].pop("D+2|43"), "scenario_keys"),
    (lambda r: r["scenarios"]["D+1|33"]["continuous"]["days"].clear(), "scenario.days"),
    (lambda r: r["scenarios"]["D+1|33"]["continuous"]["own_sizing"]["net_pnl"].update(known_labels=0), "own_sizing"),
    (lambda r: r["scenarios"]["D+1|33"]["continuous"]["summary"].update(mean_daily_net_pnl=Fraction()), "summary"),
    (lambda r: r["comparisons"]["D+1|33"].update(cash_reference_net_per_initial_capital=Fraction(1)), "comparisons"),
    (lambda r: r["comparisons"]["D+1|33"].update(common_sessions=[]), "comparisons"),
    (lambda r: r["comparisons"]["D+1|33"]["own_sizing"].clear(), "comparisons"),
    (lambda r: r.update(consumed_price_rows=0), "consumed_price_rows"),
    (lambda r: r["source_target_matrix"]["D+1"]["days"][0]["targets"].pop(), "source_target_matrix"),
    (lambda r: r.update(historical_brokerage_account_claim=True), "historical_brokerage_account_claim"),
    (lambda r: r.update(fabricated_performance_certification=True), "field set"),
    (lambda r: r["independent_verification"]["unit_work"].update(model_fits=1), "independent_verification"),
])
def test_finisher_rejects_population_account_summary_or_work_tamper(publication, change, match):
    report, verifier = deepcopy(publication)
    change(report)
    with pytest.raises(ValueError, match=match):
        verifier.finish_verify(report)


@pytest.mark.parametrize("change", ["row_state", "row_clock", "candidate_stop", "candidate_subset",
                                    "coverage", "initial_cash", "quantity", "cost", "source", "raw_open"])
def test_day_and_row_mutations_do_not_become_oracle_inputs(monkeypatch, change):
    base = oracle.H45AccountVerifier
    class Tamper(base):
        def verify_signal_row(self, **payload):
            if payload["compact_row"]["signal_state"] == "signal":
                if change == "row_state":
                    payload["compact_row"] = {**payload["compact_row"], "signal_state": "known_no_signal"}
                elif change == "row_clock":
                    payload["compact_row"] = {**payload["compact_row"],
                        "decision_at": payload["compact_row"]["decision_at"] + oracle.MINUTE}
                elif change == "candidate_stop":
                    payload["candidate"] = {**oracle._plain(payload["candidate"]), "structural_stop": Fraction(1)}
            if change == "source":
                payload["day_input"]["original_sources"]["KRX:000006"]["row_linkage_sha256"] = "0" * 64
            if change == "raw_open":
                chunk = payload["day_input"]["original_chunks"]["KRX:000006"]
                chunk["rows"][0]["open"] = "101"
            return super().verify_signal_row(**payload)
        def verify_day(self, **payload):
            if change == "candidate_subset":
                payload["candidates"] = ()
            elif change == "coverage":
                payload["compact"]["coverage"]["recorded_signal_units"] = 0
            elif change == "initial_cash":
                payload["initial_state"] = replace(payload["initial_state"], peak_nav=Fraction(1))
            elif change in {"quantity", "cost"}:
                result = payload["result"]
                events = list(result.events)
                at = next(i for i, event in enumerate(events) if event.kind == "entry" and event.quantity)
                events[at] = replace(events[at], **({"quantity": events[at].quantity + 1} if change == "quantity"
                                                 else {"assumed_cost": Fraction()}))
                payload["result"] = replace(result, events=tuple(events))
            return super().verify_day(**payload)
    monkeypatch.setattr(oracle, "H45AccountVerifier", Tamper)
    a, b, chunks = fixture(pattern="H4")
    with pytest.raises(ValueError):
        execute(a, b, chunks, "P11")


def test_raw_open_is_independent_of_invalid_later_hlc_and_duplicate_buckets():
    a, b, chunks = fixture(pattern="H4")
    chunk = next(row for row in chunks if row["symbol"] == "KRX:000006")
    chunk["rows"][95]["high"] = "bad"
    chunk["rows"].append({**chunk["rows"][100], "open_time_ms": chunk["rows"][100]["open_time_ms"] + 30000})
    data = oracle._raw_session(chunk, chunk["session"], chunk["symbol"])
    opening = data["opening"]
    assert data["opens"][opening + 95 * oracle.MINUTE] == Decimal(100)
    assert data["minutes"][opening + 95 * oracle.MINUTE] is None
    assert data["opens"][opening + 100 * oracle.MINUTE] is None
    assert data["minutes"][opening + 100 * oracle.MINUTE] is None
    assert data["gaps"][0]["state"] == "invalid_price"
    assert data["gaps"][1]["state"] == "conflict"
    # The original H4 trigger precedes both future invalid buckets.
    assert oracle._detect(data, "H4")["decision_at"] == opening + 92 * oracle.MINUTE


@pytest.mark.parametrize("pattern,mode,mutate", [
    ("H4", "H4", "none"), ("H4", "H4", "missing_initial"),
    ("H4", "H4", "missing_late"), ("H4", "H4", "flat"),
    ("H5", "H5", "none"), ("H5", "H5", "missing_late"),
    ("H5", "H5", "flat"), ("H5", "H5", "flat_missing_late")])
def test_independent_detector_matches_original_causal_boundaries(pattern, mode, mutate):
    a, b, chunks = fixture(pattern=pattern)
    chunk = next(row for row in chunks if row["symbol"] == "KRX:000006")
    if "flat" in mutate:
        for row in chunk["rows"]:
            row.update(open="100", high="100", low="100", close="100")
    missing = 0 if mutate == "missing_initial" else 300
    if "missing" in mutate:
        chunk["rows"].pop(missing)
    data = oracle._raw_session(chunk, chunk["session"], chunk["symbol"])
    session = inputs.eb.cc.aggregate_session(chunk["rows"], data["opening"])
    original = original_signals.detect_bars(oracle.UNITS["P11" if mode == "H4" else "P12"][2],
        session.prefix, opening=data["opening"], closing=data["closing"])
    expected = {"signal_state": original.state, "signal_reason": original.reason,
        **{field: getattr(original, field) for field in ("decision_at", "observed_through",
            "signal_bar_start", "signal_bar_end", "setup_bar_end")}}
    assert oracle._detect(data, mode) == expected
    if mutate == "flat_missing_late":
        assert expected["signal_state"] == "unknown"


@pytest.mark.parametrize("field,value", [("available_at", "2025-09-04T12:00:00+09:00"),
    ("is_final", True), ("source", "replacement"), ("evidence_level", "confirmed")])
def test_raw_source_metadata_cannot_be_recategorized(field, value):
    a, b, chunks = fixture(pattern="H4")
    chunk = deepcopy(chunks[0])
    chunk[field] = value
    with pytest.raises(ValueError, match="raw source identity"):
        oracle._raw_session(chunk, chunk["session"], chunk["symbol"])


@pytest.mark.parametrize("day,minutes,decision_offset", [
    ("2026-01-02", 330, 252), ("2025-11-13", 390, 312)])
def test_h5_original_shifted_session_clocks(day, minutes, decision_offset):
    a, b, chunks = fixture(pattern="H5")
    chunk = deepcopy(next(row for row in chunks if row["symbol"] == "KRX:000006"))
    chunk.update(session=day, observation_date=day)
    opening = oracle._opening(day)
    chunk["rows"] = [{**row, "open_time_ms": int((opening + index * oracle.MINUTE).timestamp()) * 1000}
                     for index, row in enumerate(chunk["rows"][:minutes])]
    data = oracle._raw_session(chunk, day, chunk["symbol"])
    detected = oracle._detect(data, "H5")
    assert detected["signal_state"] == "signal"
    assert detected["decision_at"] == opening + decision_offset * oracle.MINUTE
    assert detected["decision_at"] == data["closing"] - 78 * oracle.MINUTE


@pytest.mark.parametrize("trigger_end,expected", [(235, "signal"), (240, "known_no_signal")])
def test_h4_cutoff_does_not_observe_available_after_cutoff(trigger_end, expected):
    a, b, chunks = fixture(pattern="H4")
    chunk = deepcopy(next(row for row in chunks if row["symbol"] == "KRX:000006"))
    for index, row in enumerate(chunk["rows"]):
        if index >= 30:
            row.update(high="102", low="98", close="100")
        if trigger_end - 5 <= index < trigger_end:
            row.update(high="104", low="99", close="103")
    data = oracle._raw_session(chunk, chunk["session"], chunk["symbol"])
    detected = oracle._detect(data, "H4")
    assert detected["signal_state"] == expected
    assert detected["decision_at"] == data["opening"] + (237 if expected == "signal" else 240) * oracle.MINUTE


@pytest.mark.parametrize("band_high,expected", [("105", "signal"), ("106", "known_no_signal")])
def test_h4_half_initial_range_is_inclusive_and_trigger_excluded(band_high, expected):
    a, b, chunks = fixture(pattern="H4")
    chunk = deepcopy(next(row for row in chunks if row["symbol"] == "KRX:000006"))
    for index, row in enumerate(chunk["rows"]):
        if index >= 30:
            row.update(high=band_high, low="95", close="100")
        if 85 <= index < 90:
            row.update(high="108", low="99", close="107")
    data = oracle._raw_session(chunk, chunk["session"], chunk["symbol"])
    assert oracle._detect(data, "H4")["signal_state"] == expected


def test_two_days_release_raw_state_and_preserve_unknowns(monkeypatch):
    base = oracle.H45AccountVerifier
    completed = []
    class Observe(base):
        def verify_day(self, **payload):
            result = super().verify_day(**payload)
            if len(self._day_callbacks) == 8:
                assert self._raw_day is self._detections is None
                completed.append(payload["day_input"]["session"])
            return result
    monkeypatch.setattr(oracle, "H45AccountVerifier", Observe)
    a, b, chunks = fixture(sessions=2, pattern="H5", missing=((0, "KRX:000006", 300),))
    report = execute(a, b, chunks, "P12")
    assert completed == report["reference_sessions"]
    for timing in oracle.POLICIES:
        assert report["signal_counts"][timing]["unknown_signals"] == 1
        assert report["signal_counts"][timing]["signals"] == 1


def test_raw_maps_are_not_only_the_old_ab_candidate_pool(monkeypatch):
    base = oracle.H45AccountVerifier
    class DropOutsideAB(base):
        def verify_signal_row(self, **payload):
            day = payload["day_input"]
            day["minute_bars"] = {coordinate: value for coordinate, value in day["minute_bars"].items()
                                  if coordinate[0] != "KRX:000006"}
            return super().verify_signal_row(**payload)
    monkeypatch.setattr(oracle, "H45AccountVerifier", DropOutsideAB)
    a, b, chunks = fixture(pattern="H4")
    with pytest.raises(ValueError, match="raw.all_minute_bars"):
        execute(a, b, chunks, "P11")
