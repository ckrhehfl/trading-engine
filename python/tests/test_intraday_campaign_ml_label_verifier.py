"""Invented raw-day label parity/integrity checks; no actual report/archive IO."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest

from research import intraday_campaign_ml_labels as labels
from research import intraday_campaign_ml_label_verifier as verifier


MINUTE = timedelta(minutes=1)


def invented_day(case="cap", *, setup="EA-A", symbol="KRX:000001", minutes=81):
    opening = datetime(2025, 9, 4, 9, tzinfo=timezone(timedelta(hours=9)))
    entry = opening + 20 * MINUTE
    chunk = {"session": "2025-09-04", "symbol": symbol, "observation_date": "2025-09-04",
             "available_at": None, "retrieved_at": "2026-10-20T00:00:00+00:00", "source": "collector klines",
             "is_final": None, "evidence_level": "assumed", "rows": []}
    for offset in range(minutes):
        at = opening + offset * MINUTE
        prices = ("100", "101", "99", "100")
        if case in {"stop", "latency_gap"} and at == entry:
            prices = ("100", "101", "98", "99")
        if case == "stop_at_cap" and at == entry + 56 * MINUTE:
            prices = ("100", "101", "98", "99")
        if case == "too_late_stop" and at == entry + 57 * MINUTE:
            prices = ("100", "101", "98", "99")
        if case == "declined" and at == entry:
            prices = ("98", "99", "97", "98")
        if case == "exit_open_only" and at == entry + 60 * MINUTE:
            prices = ("111.123456789012345678901", "NaN", "0", "future")
        if case == "held_invalid" and at == entry + MINUTE:
            prices = ("100", "NaN", "99", "100")
        if (case == "entry_missing" and at == entry or case == "exit_missing" and at == entry + 60 * MINUTE
                or case == "latency_gap" and at == entry + 3 * MINUTE):
            continue
        chunk["rows"].append({"symbol": symbol, "interval": "1m", "open_time_ms": int(at.timestamp()) * 1000 + 123,
            **dict(zip(("open", "high", "low", "close"), prices)), "fetched_at": "2026-10-20T00:00:00+00:00"})
    if case == "duplicate_entry":
        duplicate = deepcopy(chunk["rows"][20])
        duplicate["open_time_ms"] += 456
        chunk["rows"].append(duplicate)
    source = labels.eb._source(chunk)
    session, _ = labels.eb._read_price(chunk, chunk["session"], symbol)
    day = {"session": chunk["session"], "session_open": opening, "session_close": opening.replace(hour=15, minute=30),
           "original_chunks": {symbol: chunk}, "original_sources": {symbol: source}, "original_sessions": {symbol: session}}
    signal = {"hypothesis_id": "H1-morning-trend-pullback-reclaim" if setup == "EA-A" else "H3-failed-downside-range-break",
              "state": "signal", "reason": "reclaim" if setup == "EA-A" else "downside_reclaimed",
              "decision_at": opening + 17 * MINUTE, "observed_through": opening + 15 * MINUTE,
              "signal_bar_start": opening + 10 * MINUTE, "signal_bar_end": opening + 15 * MINUTE,
              "setup_bar_end": opening + 10 * MINUTE}
    row = labels.OriginalLabelRow(("D+1", setup, 0, chunk["session"], symbol), labels._freeze(signal),
        entry, entry + 60 * MINUTE, Fraction(199, 2), 0, labels._freeze({"symbol": symbol, "rank": 1}),
        0, 0 if setup == "EA-A" else None, labels._freeze(source), "invented-original-EA-authorization", None)
    outcome = labels.produce_label_for_row(day, row, inputs_authenticated=True)
    row = replace(row, stored_outcome=labels._decode_outcome(verifier._plain(outcome)))
    return day, row, outcome


@pytest.mark.parametrize("setup", ["EA-A", "EA-B"])
@pytest.mark.parametrize("case,status,reason", [
    ("cap", "known", "time_cap"), ("stop", "known", "delayed_structural_stop"),
    ("stop_at_cap", "known", "stop_at_time_cap"), ("too_late_stop", "known", "time_cap"),
    ("declined", "declined", "invalidated_entry"), ("entry_missing", "unknown", "entry_open_missing"),
    ("exit_missing", "unknown", "exit_open_missing"), ("latency_gap", "unknown", "required_path_missing"),
    ("held_invalid", "unknown", "required_path_missing"), ("exit_open_only", "known", "time_cap"),
    ("duplicate_entry", "unknown", "entry_open_missing"),
])
def test_original_producer_and_independent_raw_parity(setup, case, status, reason):
    day, row, outcome = invented_day(case, setup=setup)
    checked = verifier.verify_label_for_row(day, row, outcome, inputs_authenticated=True)
    assert (checked.status, checked.reason) == (status, reason)
    assert checked.source_evidence["raw_available_at"] is None
    assert checked.source_evidence["historical_publication_or_finality_certified"] is False
    if status == "known":
        assert checked.y == outcome["exit_open"] / outcome["entry_open"] - 1 - Fraction(33, 10000)
        assert checked.available_at == outcome["exit_at"] + 3 * MINUTE
        assert checked.label_end_at == outcome["exit_at"]
    else:
        assert checked.y is None and checked.label_end_at is None
        assert checked.available_at == (row.entry_at + 3 * MINUTE if status == "declined" else None)
    if case == "stop":
        assert outcome["exit_at"] == row.entry_at + 4 * MINUTE
        assert outcome["stop_observation_end"] == row.entry_at + MINUTE
    if case == "latency_gap":
        assert outcome["stop_observation_end"] == row.entry_at + MINUTE
        assert outcome["exit_at"] is None
    if case == "too_late_stop":
        assert outcome["stop_observation_end"] is None


@pytest.mark.parametrize("field", ["entry_open", "exit_open", "gross", "primary_net", "stress_net", "structural_stop"])
def test_original_stored_numeric_tamper_is_integrity_failure(field):
    day, row, _ = invented_day()
    changed = dict(row.stored_outcome)
    changed[field] += Fraction(1, 1000000)
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="original_stored_raw_outcome_mismatch"):
        verifier.verify_label_for_row(day, replace(row, stored_outcome=labels._freeze(changed)), inputs_authenticated=True)


@pytest.mark.parametrize("field,value", [("actual_fill_certified", True), ("primary_cost_bps", 34),
    ("historical_timing_certified", True), ("evidence_level", "observed"), ("reason", "delayed_structural_stop")])
def test_old_state_clocks_alone_cannot_validate_outcome_evidence(field, value):
    day, row, _ = invented_day()
    changed = {**row.stored_outcome, field: value}
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="outcome_mismatch"):
        verifier.verify_label_for_row(day, replace(row, stored_outcome=labels._freeze(changed)), inputs_authenticated=True)


def test_original_latency_ohlc_requirement_is_stricter_than_exit_open():
    day, row, _ = invented_day("latency_gap")
    checked = verifier.verify_label_for_row(day, row, inputs_authenticated=True)
    assert checked.status == "unknown" and checked.reason == "required_path_missing"
    day, row, _ = invented_day("exit_open_only")
    assert verifier.verify_label_for_row(day, row, inputs_authenticated=True).status == "known"


def test_producer_mismatch_and_raw_source_mutation_fail_closed():
    day, row, producer = invented_day()
    producer["primary_net"] = Fraction(0)
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="producer_raw_outcome_mismatch"):
        verifier.verify_label_for_row(day, row, producer, inputs_authenticated=True)
    day["original_chunks"][row.row_key[-1]]["rows"][0]["fetched_at"] = None
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="raw_source_mismatch"):
        verifier.verify_label_for_row(day, row, inputs_authenticated=True)


def test_missing_caller_authentication_is_not_assumed():
    day, row, _ = invented_day()
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="caller"):
        verifier.verify_label_for_row(day, row, inputs_authenticated=False)


def test_day_batch_parses_each_symbol_once_and_matches_row_api(monkeypatch):
    day, row, outcome = invented_day()
    other = replace(row, row_key=("D+2", "EA-B", 3, row.row_key[3], row.row_key[4]),
                    signal=labels._freeze({**row.signal, "hypothesis_id": "H3-failed-downside-range-break",
                                            "reason": "downside_reclaimed"}))
    second = labels.produce_label_for_row(day, other, inputs_authenticated=True)
    other = replace(other, stored_outcome=labels._decode_outcome(verifier._plain(second)))
    expected = {item.row_key: verifier.verify_label_for_row(day, item, value, inputs_authenticated=True)
                for item, value in ((row, outcome), (other, second))}
    calls = []
    raw_session = verifier._raw_session
    def counted(chunk):
        calls.append(chunk["symbol"])
        return raw_session(chunk)
    monkeypatch.setattr(verifier, "_raw_session", counted)
    actual = verifier.verify_labels_for_day(day, (row, other), {row.row_key: outcome, other.row_key: second},
                                             inputs_authenticated=True)
    assert actual == expected
    assert calls == [row.row_key[-1]]
    with pytest.raises(TypeError):
        actual[row.row_key] = None
    day["original_chunks"][row.row_key[-1]]["rows"][0]["fetched_at"] = None
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="raw_source_mismatch"):
        verifier.verify_labels_for_day(day, (row, other), inputs_authenticated=True)


def test_batch_does_not_hide_missing_producer_outcome_or_duplicate_key():
    day, row, _ = invented_day()
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="producer_signal_label_missing"):
        verifier.verify_labels_for_day(day, (row,), {row.row_key: None}, inputs_authenticated=True)
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="duplicate_original_label_row_key"):
        verifier.verify_labels_for_day(day, (row, row), inputs_authenticated=True)


def valid_report():
    # Read the old invented metadata template, replacing no frozen file. Its
    # deliberately invalid old outcome placeholders are never used as labels.
    from test_intraday_campaign_readiness import reports
    report, _ = reports(sessions=1)
    report["authorization_reference"] = "invented-original-EA-authorization"
    report["sources"] = []
    days = {}
    for index in range(20):
        symbol = f"KRX:{index + 1:06d}"
        day, _, _ = invented_day(symbol=symbol, minutes=141)
        days[symbol] = day
        report["sources"].append(day["original_sources"][symbol])
    report["volume_sources"] = deepcopy(report["sources"])
    for policy in ("D+1", "D+2"):
        for index, raw in enumerate(report["variants"][policy]["days"]):
            signal = {key: datetime.fromisoformat(value) if value is not None and key.endswith(("_at", "_through", "_start", "_end"))
                      else value for key, value in raw["signal"].items()}
            raw["structural_stop"] = {"numerator": "199", "denominator": "2"} if signal["state"] == "signal" else None
            row = labels.OriginalLabelRow((policy, "EA-A", index, raw["session"], raw["symbol"]), labels._freeze(signal),
                datetime.fromisoformat(raw["entry_at"]) if raw["entry_at"] else None,
                datetime.fromisoformat(raw["cap_at"]) if raw["cap_at"] else None,
                Fraction(199, 2) if signal["state"] == "signal" else None, 0, labels._freeze(raw["membership_target"]),
                index, index, labels._freeze(report["sources"][index]), report["authorization_reference"], None)
            outcome = labels.produce_label_for_row(days[raw["symbol"]], row, inputs_authenticated=True)
            raw["outcome"] = verifier._plain(outcome)
            raw["common_price_observable"] = raw["common_context_observable"] and outcome is not None and outcome["status"] == "known"
    return report


def test_report_projection_preserves_exact_outcomes_original_order_and_is_deep_immutable():
    report = valid_report()
    report["variants"]["D+1"]["days"].reverse()
    projected = labels.project_ml_labels(report, "EA-A", inputs_authenticated=True, authentication_reference="invented-current-unit-auth")
    first = projected.rows_by_policy["D+1"][0]
    assert first.row_key == ("D+1", "EA-A", 0, "2025-09-04", "KRX:000020")
    assert first.stored_outcome["status"] == "known"
    assert first.stored_outcome["entry_open"] == 100
    assert first.stored_outcome["primary_net"] == Fraction(-33, 10000)
    assert first.authorization_reference == "invented-original-EA-authorization"
    report["variants"]["D+1"]["days"][0]["outcome"]["entry_open"] = None
    assert first.stored_outcome["entry_open"] == 100
    with pytest.raises(TypeError):
        first.stored_outcome["gross"] = Fraction(0)


def test_signal_only_sidecars_keep_original_indices_and_full_readiness_population():
    from research import intraday_campaign_context_readiness as readiness
    report = valid_report()
    for variant in report["variants"].values():
        for row in variant["days"]:
            relative = row["features"].get("relative_volume")
            if relative is not None and relative["value"] is not None:
                relative["value"] = {"numerator": "2" if row["arms"]["volume_only"] == "selected" else "0", "denominator": "1"}
    original = deepcopy(report)
    complete = readiness.project_readiness_inputs(report, "EA-A", inputs_authenticated=True,
                                                  authentication_reference="invented-current-unit-auth")
    projected = labels.project_ml_labels(report, "EA-A", inputs_authenticated=True,
                                         authentication_reference="invented-current-unit-auth")
    expected_indices = (0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19)
    for timing in ("D+1", "D+2"):
        rows = projected.rows_by_policy[timing]
        assert tuple(row.row_key[2] for row in rows) == expected_indices
        assert all(row.signal["state"] == "signal" and row.stored_outcome is not None for row in rows)
        assert len(complete.target_rows[timing]) == 20
        for index in (5, 6, 13, 14):
            raw = original["variants"][timing]["days"][index]
            assert complete.target_rows[timing][index]["signal_state"] == raw["signal"]["state"]
            assert complete.target_rows[timing][index]["original_row_index"] == index
            assert complete.target_rows[timing][index]["decision_at"] == raw["signal"]["decision_at"]
            assert complete.target_rows[timing][index]["source_index"] == raw["source_index"]
    assert len(complete.account_projection["sources"]) == 20
    assert readiness._account_plain(complete.account_projection["source_target_matrix"]) == original["source_target_matrix"]
    assert report == original
    receipt = verifier.verify_ml_label_projection(projected, report, inputs_authenticated=True,
                                                  authentication_reference="invented-current-unit-auth")
    assert receipt["original_rows_verified"] == 40
    assert receipt["signal_rows_verified"] == 32
    assert receipt["nonsignal_rows_verified"] == 8
    assert receipt["source_coordinates_verified"] == 20
    assert projected.rows_by_policy["D+1"][0].source is projected.rows_by_policy["D+2"][0].source


@pytest.mark.parametrize("fault", ["outcome", "population", "source", "clock", "renumbered_signal"])
def test_independent_projection_checks_omitted_nonsignals_and_original_signal_indices(fault):
    report = valid_report()
    projected = labels.project_ml_labels(report, "EA-A", inputs_authenticated=True, authentication_reference="invented")
    nonsignal = report["variants"]["D+1"]["days"][5]
    if fault == "outcome":
        nonsignal["outcome"] = {"status": "known"}
    elif fault == "population":
        report["variants"]["D+1"]["days"].pop(5)
    elif fault == "source":
        nonsignal["source_index"] = 0
    elif fault == "clock":
        nonsignal["signal"]["observed_through"] = "2025-09-04T12:00:00+09:00"
    else:
        rows = list(projected.rows_by_policy["D+1"])
        assert rows[5].row_key[2] == 7
        rows[5] = replace(rows[5], row_key=(*rows[5].row_key[:2], 5, *rows[5].row_key[3:]))
        projected = replace(projected, rows_by_policy=labels._freeze({**projected.rows_by_policy, "D+1": rows}))
    with pytest.raises(verifier.MLLabelVerificationRefusal):
        verifier.verify_ml_label_projection(projected, report, inputs_authenticated=True,
                                            authentication_reference="invented")


def test_zero_original_signals_need_no_supervised_sidecars_but_all_targets_are_checked():
    report = valid_report()
    for variant in report["variants"].values():
        for row in variant["days"]:
            row.update(entry_at=None, cap_at=None, structural_stop=None, outcome=None,
                       features={}, common_context_observable=False, common_price_observable=False,
                       arms=dict.fromkeys(row["arms"], "rejected"))
            row["signal"].update(state="known_no_signal", reason="initial_condition_false",
                                  signal_bar_start=None, signal_bar_end=None)
    projected = labels.project_ml_labels(report, "EA-A", inputs_authenticated=True, authentication_reference="invented")
    assert projected.rows_by_policy == {"D+1": (), "D+2": ()}
    receipt = verifier.verify_ml_label_projection(projected, report, inputs_authenticated=True,
                                                  authentication_reference="invented")
    assert receipt["original_rows_verified"] == receipt["nonsignal_rows_verified"] == 40
    assert receipt["signal_rows_verified"] == 0 and receipt["source_coordinates_verified"] == 20


def test_old_invented_placeholders_are_rejected_not_rewritten_or_dropped():
    from test_intraday_campaign_context_readiness import fixture
    report, _, _ = fixture(1)
    report["authorization_reference"] = "invented-original-EA-authorization"
    before = deepcopy(report)
    with pytest.raises(labels.MLLabelsRefusal, match="outcome_schema"):
        labels.project_ml_labels(report, "EA-A", inputs_authenticated=True, authentication_reference="invented")
    assert report == before


@pytest.mark.parametrize("field", ["primary_net", "gross", "entry_open"])
def test_projection_rejects_noncanonical_signed_numeric_outcome(field):
    report = valid_report()
    report["variants"]["D+1"]["days"][0]["outcome"][field] = {"numerator": "-2", "denominator": "4"}
    with pytest.raises(labels.MLLabelsRefusal, match="noncanonical_fraction"):
        labels.project_ml_labels(report, "EA-A", inputs_authenticated=True, authentication_reference="invented")
