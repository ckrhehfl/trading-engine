"""Invented sources only: exact next-close acceptance and newly timed labels."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
import json

import pytest

from research import intraday_opening_acceptance_calibration as calibration


cc = calibration.cc
ARM = "15_acceptance"
DAYS = ("2025-09-04", "2025-09-11")
SYMBOLS = [f"KRX:{number:06d}" for number in range(1, 21)]


def raw_rows(day=DAYS[0], symbol=SYMBOLS[0], count=390):
    opening = cc.session_open(day)
    return [{
        "symbol": symbol, "interval": "1m",
        "open_time_ms": int((opening + index * cc.MINUTE).timestamp()) * 1000,
        "open": "100", "high": "100", "low": "100", "close": "100",
        "fetched_at": "2026-10-20T00:00:00+00:00",
    } for index in range(count)]


def pattern(day=DAYS[0], symbol=SYMBOLS[0], breakout_start=15, count=390):
    rows = raw_rows(day, symbol, count)
    rows[breakout_start + 4].update(high="102", close="102")
    for row in rows[breakout_start + 5:]:
        row.update(open="102", high="102", low="101", close="102")
    return rows


def inputs(days=DAYS, observations=None):
    observations = observations or {}
    cb = {
        "study_id": "intraday-universe-sources-v1",
        "mode": "discovery-universe-source-and-coverage-only", **cc.FLAGS,
        "calendar": {"rows": [{"date": day, "fetched_at": None} for day in days], "certified": False},
        "variants": {},
    }
    for variant in ("D+1", "D+2"):
        cb["variants"][variant] = {"days": [{
            "session": day, "observation_date": "2025-08-29",
            "selected_at": day + "T08:30:00+09:00", "available_at": "2025-09-01T08:30:00+09:00",
            "retrieved_at": {"fixture": "2026-10-20T00:00:00+00:00"},
            "source": "invented monthly source", "is_final": None,
            "data_vintage": {"fixture": "b" * 64}, "evidence_level": "assumed",
            "targets": [{"symbol": symbol, "rank": rank, "stored_row_count": None,
                "state": "no_stored_rows_unknown_reason"} for rank, symbol in enumerate(SYMBOLS, 1)],
        } for day in days], "summary": {"fixture_unknowns_preserved": True}}
    chunks = {(day, symbol): {
        "symbol": symbol, "session": day, "observation_date": day,
        "available_at": None, "retrieved_at": "2026-10-20T01:00:00+00:00",
        "source": "collector klines", "is_final": None, "evidence_level": "assumed",
        "rows": deepcopy(observations.get((day, symbol), [])),
    } for day in days for symbol in SYMBOLS}
    return cb, chunks


def finding(rows, day=DAYS[0]):
    return calibration._session_findings(cc.aggregate_session(rows, cc.session_open(day)),
        {"observation_date": day, "retrieved_at": "2026-10-20T01:00:00+00:00"}, "a" * 64, len(rows))[ARM]


def specification():
    return {"id": calibration.SPECIFICATION_ID, "mode": calibration.MODE,
        "window": dict(calibration.WINDOW), "parameters": deepcopy(calibration.PARAMETERS),
        "resources": dict(calibration.RESOURCES), **calibration.REGISTERED_PATHS,
        "upstream_sha256": calibration.UPSTREAM_SHA256,
        "input_compressed_sha256": calibration.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": calibration.INPUT_STREAM_SHA256}


def test_registration_matches_fixed_dt_descriptor_and_unchanged_budgets():
    calibration.validate_spec(json.loads(calibration.SPECIFICATION_PATH.read_text()))
    dt = json.loads((calibration.ROOT / "configs/research/discovery/intraday-candidate-update-v1.json").read_text())["next_hypothesis"]
    for key in ("universe", "side", "range_minutes", "range_high", "breakout", "confirmation", "invalidation",
            "touch_required", "additional_filters", "availability_lag_minutes", "decision", "entry", "exit",
            "maximum_signals_per_name_session", "cost_floor_bps", "unknown_prefix", "future_labels"):
        assert calibration.PARAMETERS[key] == dt[key]
    assert calibration.PARAMETERS["hypothesis_id"] == dt["id"]
    assert calibration.ARMS == (ARM,)
    assert calibration.RESOURCES["cpu_seconds"] == 300
    assert calibration.RESOURCES["address_space_mib"] == 512
    assert calibration.RESOURCES["wall_seconds"] == 1200
    assert calibration.RESOURCES["automatic_retries"] == 0
    assert {key: calibration.PARAMETERS[key] for key in ("block_sessions", "bootstrap_draws", "bootstrap_seed")} == {
        "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0}


@pytest.mark.parametrize("mutate", [
    lambda spec: spec["parameters"].update(range_minutes=30),
    lambda spec: spec["parameters"].update(touch_required=True),
    lambda spec: spec["parameters"].update(bootstrap_draws=1999),
    lambda spec: spec["parameters"].update(cost_floor_bps="20"),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec.update(input_compressed_sha256="a" * 64),
    lambda spec: spec.update(output_directory="/tmp/unregistered"),
    lambda spec: spec["resources"].update(cpu_seconds=600),
    lambda spec: spec.update(extra=True),
])
def test_changed_design_or_input_pin_fails_closed(mutate):
    spec = specification()
    mutate(spec)
    with pytest.raises(calibration.OpeningAcceptanceRefusal, match="specification_scope"):
        calibration.build_calibration(*inputs(), spec)


def test_exact_next_close_triggers_without_a_touch_and_uses_new_labels():
    result = finding(pattern())
    event, label = result["event"], result["label"]
    opening = cc.session_open(DAYS[0])
    assert result["status"] == "signal"
    assert event["range_high"] == Decimal("100")
    assert event["breakout_bar"]["end_at"] == opening + 20 * cc.MINUTE
    assert event["confirmation_bar"]["start_at"] == event["breakout_bar"]["end_at"]
    assert event["confirmation_bar"]["low"] > event["range_high"]
    assert event["signal_bar_end"] == opening + 25 * cc.MINUTE
    assert event["decision_at"] == opening + 27 * cc.MINUTE
    assert label["entry_at"] == opening + 30 * cc.MINUTE
    assert label["exit_at"] == opening + 60 * cc.MINUTE
    assert label["label_available_at"] == opening + 63 * cc.MINUTE
    assert label["time_cap_decided_at"] == event["decision_at"]
    assert len(event["context"]) == 3 and event["available_at"] == event["decision_at"]


@pytest.mark.parametrize("close", ["100", "99"])
def test_failed_exact_w_cancels_without_retry_or_replacement_breakout(close):
    rows = pattern()
    rows[24].update(close=close, low=close)
    rows[29].update(close="104", high="104")
    result = finding(rows)
    assert result["status"] == "known_no_signal"
    assert result["event"] is None and result["label"] is None
    assert result["known_no_signal_reason"] == "next_scheduled_close_at_or_below_range_high"
    assert result["path"]["breakout_bar"]["end_at"] == cc.session_open(DAYS[0]) + 20 * cc.MINUTE
    assert result["path"]["invalidation_bar"] == result["path"]["confirmation_bar"]


def test_original_range_and_strict_first_breakout_ignore_high_only_crossing():
    rows = pattern()
    rows[19].update(close="100")
    rows[24].update(high="103", close="103")
    rows[29].update(high="105", close="104")
    result = finding(rows)
    assert result["event"]["range_high"] == 100
    assert result["event"]["breakout_bar"]["end_at"] == cc.session_open(DAYS[0]) + 25 * cc.MINUTE
    assert result["event"]["signal_bar_end"] == cc.session_open(DAYS[0]) + 30 * cc.MINUTE


@pytest.mark.parametrize("breakout_start,expected", [(225, "signal"), (230, "known_no_signal")])
def test_w_availability_controls_cutoff_even_when_b_was_in_time(breakout_start, expected):
    result = finding(pattern(breakout_start=breakout_start))
    assert result["status"] == expected
    if expected == "signal":
        opening = cc.session_open(DAYS[0])
        assert result["event"]["decision_at"] == opening + 237 * cc.MINUTE
        assert result["label"]["entry_at"] == opening + 240 * cc.MINUTE
    else:
        assert result["known_no_signal_reason"] == "confirmation_after_cutoff"


def test_w_outside_cutoff_is_not_read_or_needed_for_known_absence():
    rows = pattern(breakout_start=230, count=235)
    result = finding(rows)
    assert result["status"] == "known_no_signal"
    assert result["known_no_signal_reason"] == "confirmation_after_cutoff"
    assert result["path"]["confirmation_bar"] is None


@pytest.mark.parametrize("offset", [3, 17, 22])
@pytest.mark.parametrize("defect", ["missing", "conflict", "invalid_price"])
def test_missing_initial_b_prefix_or_exact_w_is_unknown_and_never_skipped(offset, defect):
    rows = pattern()
    if defect == "missing":
        rows.pop(offset)
    elif defect == "conflict":
        rows.append(dict(rows[offset]))
    else:
        rows[offset]["close"] = "NaN"
    result = finding(rows)
    assert result["status"] == "unknown" and result["event"] is None
    assert result["unknown_reasons"] == [f"decision_prefix_{defect}_minute_buckets"]


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("defect", ["missing", "conflict", "invalid_price"])
def test_later_gap_preserves_known_signal_or_cancellation(cancelled, defect):
    rows = pattern()
    if cancelled:
        rows[24].update(close="100", low="100")
    if defect == "missing":
        rows.pop(100)
    elif defect == "conflict":
        rows.append(dict(rows[100]))
    else:
        rows[100]["close"] = "NaN"
    result = finding(rows)
    assert result["status"] == ("known_no_signal" if cancelled else "signal")
    assert result["unknown_reasons"] == []
    if not cancelled:
        assert result["label"]["status"] == "known"


def test_no_b_requires_full47bars_but_known_w_result_needs_no_later_prefix():
    assert finding(raw_rows(count=235))["known_no_signal_reason"] == "no_breakout_before_cutoff"
    assert finding(raw_rows(count=234))["status"] == "unknown"
    assert finding([])["unknown_reasons"] == ["no_saved_rows_unknown_cause"]
    rows = pattern(count=25)
    rows[24].update(close="100", low="100")
    assert finding(rows)["status"] == "known_no_signal"
    assert finding(pattern(count=25))["status"] == "signal"


@pytest.mark.parametrize("offset", [30, 45, 60])
def test_new_entry_cap_and_all_intervening_buckets_are_required(offset):
    rows = pattern()
    rows.pop(offset)
    result = finding(rows)
    assert result["status"] == "signal"
    assert result["label"]["status"] == "unknown"
    assert result["label"]["unknown_reasons"] == ["label_missing_minute_buckets"]
    assert result["label"]["unknown_minute_buckets"] == 1
    assert result["label"]["required_minute_buckets"] == 31
    assert result["label"]["price_change"] is None


def test_cap_open_is_exact_and_does_not_reuse_b_retest_or_previous_close():
    rows = pattern()
    rows[25].update(open="80", low="80")  # Original B-entry proxy is irrelevant.
    rows[30].update(open="125.00000000000000000000000001", high="126")
    rows[35].update(open="90", low="90")  # A possible old retest-entry proxy.
    rows[59].update(close="150", high="150")
    rows[60].update(open="137.5", high="138")
    with localcontext() as context:
        context.prec = 3
        label = finding(rows)["label"]
    assert label["exit_open"] == Decimal("137.5")
    assert label["price_change"] == Fraction(Decimal("137.5")) / Fraction(Decimal(rows[30]["open"])) - 1


def test_strict_comparisons_preserve_prices_beyond_float_precision():
    rows = pattern()
    base = 10 ** 40
    for row in rows:
        for name in ("open", "high", "low", "close"):
            row[name] = str(base + int(row[name]) - 100)
    result = finding(rows)
    assert result["status"] == "signal"
    assert result["event"]["range_high"] == Decimal(base)
    assert result["event"]["confirmation_bar"]["close"] == Decimal(base + 2)


def test_future_prices_do_not_change_signal_and_only_new_cap_changes_label():
    rows = pattern()
    before = finding(rows)
    rows[60].update(open="104", high="104")
    rows[100].update(open="1", low="1", close="1")
    after = finding(rows)
    assert before["event"] == after["event"] and before["path"] == after["path"]
    assert before["label"]["price_change"] != after["label"]["price_change"]


def test_complete_targets_metadata_zero_weeks_and_loose_bounds_stay_separate():
    cb, chunks = inputs(observations={(DAYS[0], SYMBOLS[0]): pattern()})
    before = deepcopy((cb, chunks))
    checkpoints = []
    report = calibration.build_calibration(cb, chunks, specification(), lambda: checkpoints.append(True))
    assert (cb, chunks) == before
    assert report["source_target_matrix"] == cb["variants"]
    assert report["upstream_calendar"] == cb["calendar"]
    for key, variant in report["variants"].items():
        assert len(variant["days"]) == 40
        for row in variant["days"]:
            source = cb["variants"][key]["days"][DAYS.index(row["session"])]
            assert row["membership_source"] == {name: value for name, value in source.items() if name != "targets"}
            assert row["membership_target"] in source["targets"]
        summary = variant["by_arm"][ARM]
        assert summary["signal_counts"] == {"signal": 1, "known_no_signal": 0, "unknown": 39}
        assert summary["by_week"]["2025-W37"]["signal_counts"]["signal"] == 0
        frequency = summary["frequency"]
        assert frequency["signals_per_reference_week"] == Fraction(1, 2)
        assert frequency["signals_per_reference_week_upper_bound"] == 20
        assert frequency["maximum_signals_including_unknown"] == 40
        assert frequency["maximum_known_labels_including_unknown"] == 40
        assert frequency["weekly_signal_counts_min_median_max"] == {"minimum": 0, "median": Fraction(1, 2), "maximum": 1}
        assert frequency["zero_signal_weeks"] == 1
        assert frequency["weeks_with_at_least15_signals_upper_bound"] == 2
        assert frequency["forced_trades"] is False
    assert len(checkpoints) > len(chunks)
    cb["variants"]["D+1"]["days"][0]["targets"][0]["rank"] = 99
    assert report["source_target_matrix"]["D+1"]["days"][0]["targets"][0]["rank"] == 1


def test_cancellations_missing_signals_and_missing_new_labels_have_distinct_counts():
    cancelled = pattern(symbol=SYMBOLS[1])
    cancelled[24].update(low="100", close="100")
    rows = pattern()
    rows.pop(45)
    cb, chunks = inputs(observations={(DAYS[0], SYMBOLS[0]): rows, (DAYS[0], SYMBOLS[1]): cancelled})
    summary = calibration.build_calibration(cb, chunks)["variants"]["D+1"]["by_arm"][ARM]
    assert summary["signal_counts"] == {"signal": 1, "known_no_signal": 1, "unknown": 38}
    assert summary["label_counts"] == {"known": 0, "unknown": 1}
    assert summary["known_no_signal_reason_counts"] == {"next_scheduled_close_at_or_below_range_high": 1}
    assert summary["frequency"]["maximum_known_labels_including_unknown"] == 39
    assert summary["comparison_choice_reasons"] == ["unknown_own_labels", "undefined_own_label_sizing"]


def test_original_raw_offsets_retrieval_and_chunk_hash_are_preserved():
    rows = pattern()
    rows[0]["open_time_ms"] += 12345
    cb, chunks = inputs(observations={(DAYS[0], SYMBOLS[0]): rows})
    row = calibration.build_calibration(cb, chunks)["variants"]["D+1"]["days"][0]
    assert row["data_vintage"] == hashlib.sha256(calibration._canonical(chunks[DAYS[0], SYMBOLS[0]]) + b"\n").hexdigest()
    assert row["raw_chunk_metadata"]["available_at"] is None
    assert row["source_fetched_at"] == ["2026-10-20T00:00:00+00:00"]
    assert row["source_offset_counts_ms"] == {12345: 1, 0: 389}
    assert row["arms"][ARM]["event"]["retrieved_at"] == row["raw_chunk_metadata"]["retrieved_at"]


def test_own_sizing_uses_new_labels_all_reference_dates_and_reports_no_performance():
    days = ("2025-09-04", "2025-09-05", "2025-09-08")
    observations = {(day, SYMBOLS[0]): pattern(day) for day in days[:2]}
    observations[days[0], SYMBOLS[0]][60].update(open="104.04", high="105")
    observations[days[1], SYMBOLS[0]][60].update(open="99.96", low="99")
    report = calibration.build_calibration(*inputs(days, observations))
    summary = report["variants"]["D+1"]["by_arm"][ARM]
    own = summary["own_label_dispersion"]
    assert own == cc.dispersion([
        {"session": days[0], "label": Fraction(1, 50)},
        {"session": days[1], "label": Fraction(-1, 50)},
    ], list(days))
    assert own["reference_sessions"] == 3 and own["known_label_sessions"] == 2
    assert own["design_se"] == max(own[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert own["mde_bps"] > 33
    assert summary["comparison_choice_reasons"] == ["own_label_mde_exceeds_assumed_cost_floor"]
    assert not {"mean", "net_mean", "gross_mean", "p_value", "cost_exceedance", "win_count"} & set(own)
    assert report["comparison_performed"] is False and report["family_adopted"] is False


def test_one_known_date_cannot_become_own_sizing_pass():
    observations = {(DAYS[0], symbol): pattern(symbol=symbol) for symbol in SYMBOLS[:2]}
    summary = calibration.build_calibration(*inputs(observations=observations))["variants"]["D+1"]["by_arm"][ARM]
    assert summary["own_label_dispersion"]["known_labels"] == 2
    assert summary["own_label_dispersion"]["mde_bps"] is None
    assert summary["comparison_choice_reasons"] == ["undefined_own_label_sizing"]


@pytest.mark.parametrize("day,count", [("2025-11-13", 390), ("2026-01-02", 330)])
def test_exceptional_session_assumptions_preserve_new_clocks(day, count):
    result = finding(pattern(day, count=count), day)
    assert result["event"]["signal_bar_end"].hour == 10
    assert result["label"]["exit_at"] == cc.session_open(day) + 60 * cc.MINUTE


@pytest.mark.parametrize("mutation", [
    lambda cb, chunks: cb["variants"]["D+1"]["days"][0]["targets"].pop(),
    lambda cb, chunks: cb["variants"]["D+1"]["days"][0].update(available_at="2025-09-04T10:00:00+09:00"),
    lambda cb, chunks: cb.update(historical_eligibility_certified=True),
    lambda cb, chunks: cb.update(mode="old_outcome_report"),
    lambda cb, chunks: chunks[DAYS[0], SYMBOLS[0]].update(available_at="2025-09-04T09:00:00+09:00"),
    lambda cb, chunks: chunks[DAYS[0], SYMBOLS[0]].update(retrieved_at="no-clock"),
    lambda cb, chunks: chunks[DAYS[0], SYMBOLS[0]].update(is_final=True),
    lambda cb, chunks: chunks[DAYS[0], SYMBOLS[0]]["rows"].append({}),
    lambda cb, chunks: chunks.pop((DAYS[0], SYMBOLS[0])),
])
def test_source_contract_changes_fail_closed(mutation):
    cb, chunks = inputs()
    mutation(cb, chunks)
    with pytest.raises((calibration.OpeningAcceptanceRefusal, calibration.ct.OpeningRangeRefusal, cc.CalibrationRefusal)):
        calibration.build_calibration(cb, chunks)


def test_variant_union_consumes_shared_chunks_once():
    cb, chunks = inputs(days=(DAYS[0],))
    symbol = "KRX:000021"
    cb["variants"]["D+2"]["days"][0]["targets"][-1]["symbol"] = symbol
    chunks[DAYS[0], symbol] = {**deepcopy(chunks[DAYS[0], SYMBOLS[-1]]), "symbol": symbol}
    report = calibration.build_calibration(cb, chunks)
    assert report["input_evidence"]["unique_target_days"] == 21
    assert len(report["variants"]["D+2"]["days"]) == 20


def test_stream_order_count_and_final_digest_must_all_pass():
    cb, chunks = inputs()
    ordered = list(chunks.values())
    for iterator, reason in [(reversed(ordered), "order_or_identity"),
            (iter(ordered[:-1]), "missing_input"), (iter([*ordered, ordered[-1]]), "extra_input")]:
        with pytest.raises(calibration.OpeningAcceptanceRefusal, match=reason):
            calibration.build_calibration(cb, iterator)

    def failed_end():
        yield from ordered
        raise calibration.OpeningAcceptanceRefusal("final_digest_failed")

    with pytest.raises(calibration.OpeningAcceptanceRefusal, match="final_digest_failed"):
        calibration.build_calibration(cb, failed_end())


def test_archive_exact_framing_stream_hash_and_checkpoints(tmp_path, monkeypatch):
    _, chunks = inputs(days=(DAYS[0],))
    stream = b"".join(calibration._canonical(chunk) + b"\n" for chunk in chunks.values())
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(stream))
    monkeypatch.setattr(calibration, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    checkpoints = []
    assert list(calibration.archive_chunks(path, lambda: checkpoints.append(True))) == list(chunks.values())
    assert len(checkpoints) == len(chunks) + 1
    monkeypatch.setattr(calibration, "INPUT_STREAM_SHA256", "0" * 64)
    with pytest.raises(calibration.OpeningAcceptanceRefusal, match="stream_hash"):
        list(calibration.archive_chunks(path, lambda: None))


@pytest.mark.parametrize("stream,reason", [(b"{}", "framing"), (b'{ "a": 1 }\n', "canonical_framing")])
def test_nonoriginal_or_incomplete_stream_refuses(tmp_path, stream, reason):
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(stream))
    with pytest.raises(calibration.OpeningAcceptanceRefusal, match=reason):
        list(calibration.archive_chunks(path, lambda: None))


@pytest.mark.parametrize("limit", ["maximum_chunk_bytes", "maximum_uncompressed_bytes", "maximum_chunks"])
def test_archive_budgets_fail_before_truncation(tmp_path, monkeypatch, limit):
    _, chunks = inputs(days=(DAYS[0],))
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(b"".join(calibration._canonical(chunk) + b"\n" for chunk in chunks.values())))
    monkeypatch.setattr(calibration, "RESOURCES", {**calibration.RESOURCES, limit: 1})
    with pytest.raises(calibration.OpeningAcceptanceRefusal, match="budget_or_framing"):
        list(calibration.archive_chunks(path, lambda: None))


def test_budget_stop_never_returns_a_partial_report():
    def stop():
        raise RuntimeError("invented_budget_stop")

    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        calibration.build_calibration(*inputs(), checkpoint=stop)


def test_full257dates56weeks_and5140targets_keep_zero_opportunities():
    candidates = []
    current, end = date(2025, 9, 4), date(2026, 9, 23)
    while current <= end:
        if current.weekday() < 5:
            candidates.append(current.isoformat())
        current += timedelta(days=1)
    removals = set(candidates[10::5][:len(candidates) - 257])
    days = [day for day in candidates if day not in removals]
    report = calibration.build_calibration(*inputs(days))
    assert len(report["reference_sessions"]) == 257
    assert len(report["reference_weeks"]) == 56
    for variant in report["variants"].values():
        assert len(variant["days"]) == 5140
        summary = variant["by_arm"][ARM]
        assert len(summary["by_week"]) == 56 and len(summary["by_month"]) == 13
        assert summary["signal_counts"] == {"signal": 0, "known_no_signal": 0, "unknown": 5140}
        assert summary["frequency"]["zero_signal_weeks"] == 56
        assert summary["frequency"]["signals_per_reference_week"] == 0
        assert summary["own_label_dispersion"]["reference_sessions"] == 257
        assert summary["own_label_dispersion"]["mde_bps"] is None
