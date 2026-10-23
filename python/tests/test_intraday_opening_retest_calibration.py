"""Invented inputs only: fixed first-contact retest, new labels and own sizing."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
import json

import pytest

from research import intraday_opening_retest_calibration as calibration


cc = calibration.cc
ARM = "15_retest"
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


def pattern(day=DAYS[0], symbol=SYMBOLS[0], contact_start=25, count=390):
    rows = raw_rows(day, symbol, count)
    rows[19].update(high="102", close="102")
    for row in rows[20:]:
        row.update(open="102", high="102", low="101", close="102")
    if contact_start is not None:
        rows[contact_start].update(low="100")
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
    session = cc.aggregate_session(rows, cc.session_open(day))
    return calibration._session_findings(session,
        {"observation_date": day, "retrieved_at": "2026-10-20T01:00:00+00:00"}, "a" * 64, len(rows))[ARM]


def specification():
    return {"id": calibration.SPECIFICATION_ID, "mode": calibration.MODE,
        "window": dict(calibration.WINDOW), "parameters": deepcopy(calibration.PARAMETERS),
        "resources": dict(calibration.RESOURCES), **calibration.REGISTERED_PATHS,
        "upstream_sha256": calibration.UPSTREAM_SHA256,
        "input_compressed_sha256": calibration.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": calibration.INPUT_STREAM_SHA256}


def test_committed_registration_exactly_matches_fixed_design_and_budgets():
    calibration.validate_spec(json.loads(calibration.SPECIFICATION_PATH.read_text()))
    assert calibration.ARMS == (ARM,)
    assert calibration.PARAMETERS["hypothesis_id"] == "opening-range15-first-retest-bounce-v1"
    assert calibration.PARAMETERS["range_minutes"] == 15
    assert calibration.PARAMETERS["same_bar_breakout_retest_allowed"] is False
    assert calibration.PARAMETERS["cost_floor_bps"] == "33"
    assert calibration.RESOURCES["cpu_seconds"] == 300
    assert calibration.RESOURCES["address_space_mib"] == 512
    assert calibration.RESOURCES["wall_seconds"] == 1200


@pytest.mark.parametrize("mutate", [
    lambda spec: spec["parameters"].update(range_minutes=30),
    lambda spec: spec["parameters"].update(same_bar_breakout_retest_allowed=True),
    lambda spec: spec["parameters"].update(bootstrap_draws=1999),
    lambda spec: spec["parameters"].update(cost_floor_bps="20"),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec.update(input_compressed_sha256="a" * 64),
    lambda spec: spec.update(output_directory="/tmp/unregistered"),
    lambda spec: spec["resources"].update(cpu_seconds=600),
    lambda spec: spec.update(extra=True),
])
def test_specification_mutations_refuse(mutate):
    spec = specification()
    mutate(spec)
    with pytest.raises(calibration.OpeningRetestRefusal, match="specification_scope"):
        calibration.build_calibration(*inputs(), spec)


def test_first_contact_keeps_original_breakout_and_new_entry_and_exit():
    result = finding(pattern())
    event, label = result["event"], result["label"]
    opening = cc.session_open(DAYS[0])
    assert result["status"] == "signal"
    assert event["range_high"] == Decimal("100")
    assert event["breakout_bar"]["end_at"] == opening + 20 * cc.MINUTE
    assert event["signal_bar_end"] == opening + 30 * cc.MINUTE
    assert event["decision_at"] == opening + 32 * cc.MINUTE
    assert label["entry_at"] == opening + 35 * cc.MINUTE
    assert label["exit_at"] == opening + 65 * cc.MINUTE
    assert label["label_available_at"] == opening + 68 * cc.MINUTE
    assert len(event["context"]) == 3
    assert event["available_at"] == event["decision_at"]


def test_breakout_bar_contact_is_excluded_and_later_first_contact_is_sole_trial():
    rows = pattern(contact_start=None)
    assert finding(rows)["known_no_signal_reason"] == "no_contact_before_cutoff"
    rows[20]["low"] = "100"
    result = finding(rows)
    assert result["event"]["signal_bar_end"] == cc.session_open(DAYS[0]) + 25 * cc.MINUTE
    assert result["event"]["contact_bar"]["start_at"] < result["event"]["breakout_bar"]["available_at"]


def test_first_failed_contact_invalidates_and_cannot_retry_or_replace_breakout():
    rows = pattern()
    rows[29].update(low="100", close="100")
    rows[39].update(high="104", close="104")
    rows[40].update(low="100")
    result = finding(rows)
    assert result["status"] == "known_no_signal"
    assert result["event"] is None and result["label"] is None
    assert result["known_no_signal_reason"] == "first_contact_close_at_or_below_range_high"
    assert result["path"]["breakout_bar"]["end_at"] == cc.session_open(DAYS[0]) + 20 * cc.MINUTE
    assert result["path"]["invalidation_bar"] == result["path"]["first_contact_bar"]


def test_initial_range_and_strict_breakout_do_not_use_later_higher_high():
    rows = pattern()
    rows[19].update(close="100")  # High above R alone is not B.
    rows[24].update(high="103", close="103")
    rows[25].update(low="100", high="104")
    result = finding(rows)
    assert result["event"]["range_high"] == 100
    assert result["event"]["breakout_bar"]["end_at"] == cc.session_open(DAYS[0]) + 25 * cc.MINUTE


@pytest.mark.parametrize("contact_start,expected", [(230, "signal"), (235, "known_no_signal")])
def test_confirmation_availability_not_breakout_controls_cutoff(contact_start, expected):
    result = finding(pattern(contact_start=contact_start))
    assert result["status"] == expected
    if expected == "signal":
        opening = cc.session_open(DAYS[0])
        assert result["event"]["decision_at"] == opening + 237 * cc.MINUTE
        assert result["label"]["entry_at"] == opening + 240 * cc.MINUTE
    else:
        assert result["known_no_signal_reason"] == "no_contact_before_cutoff"


@pytest.mark.parametrize("offset", [3, 17, 22, 27])
def test_gap_before_range_breakout_or_contact_is_unknown_without_skipping(offset):
    rows = pattern()
    rows.pop(offset)
    result = finding(rows)
    assert result["status"] == "unknown"
    assert result["event"] is None
    assert result["unknown_reasons"] == ["decision_prefix_missing_minute_buckets"]


@pytest.mark.parametrize("invalidated", [False, True])
@pytest.mark.parametrize("defect", ["missing", "conflict", "invalid_price"])
def test_later_gap_never_erases_known_signal_or_invalidation(invalidated, defect):
    rows = pattern()
    if invalidated:
        rows[29].update(low="100", close="100")
    if defect == "missing":
        rows.pop(100)
    elif defect == "conflict":
        rows.append(dict(rows[100]))
    else:
        rows[100]["close"] = "NaN"
    result = finding(rows)
    assert result["status"] == ("known_no_signal" if invalidated else "signal")
    assert result["unknown_reasons"] == []
    if not invalidated:
        assert result["label"]["status"] == "known"


def test_known_absence_requires_full47bars_except_prior_invalidation():
    assert finding(raw_rows(count=235))["known_no_signal_reason"] == "no_breakout_before_cutoff"
    assert finding(raw_rows(count=234))["status"] == "unknown"
    assert finding([])["unknown_reasons"] == ["no_saved_rows_unknown_cause"]
    rows = pattern(count=30)
    rows[29].update(low="100", close="100")
    assert finding(rows)["status"] == "known_no_signal"


@pytest.mark.parametrize("offset", [35, 50, 65])
def test_new_entry_cap_and_every_intervening_bucket_are_required(offset):
    rows = pattern()
    rows.pop(offset)
    result = finding(rows)
    assert result["status"] == "signal"
    assert result["label"]["status"] == "unknown"
    assert result["label"]["unknown_reasons"] == ["label_missing_minute_buckets"]
    assert result["label"]["unknown_minute_buckets"] == 1
    assert result["label"]["required_minute_buckets"] == 31
    assert result["label"]["price_change"] is None


def test_cap_open_exact_fraction_ignores_previous_close_and_decimal_context():
    rows = pattern()
    rows[35].update(open="125.00000000000000000000000001", high="126")
    rows[64].update(close="150", high="150")
    rows[65].update(open="137.5", high="138")
    with localcontext() as context:
        context.prec = 3
        label = finding(rows)["label"]
    assert label["exit_open"] == Decimal("137.5")
    assert label["price_change"] == Fraction(Decimal("137.5")) / Fraction(Decimal(rows[35]["open"])) - 1


def test_integer_prices_above_float_precision_keep_strict_comparisons():
    rows = pattern()
    base = 10 ** 40
    for row in rows:
        for name in ("open", "high", "low", "close"):
            row[name] = str(base + int(row[name]) - 100)
    result = finding(rows)
    assert result["status"] == "signal"
    assert result["event"]["range_high"] == Decimal(base)
    assert result["event"]["contact_bar"]["close"] == Decimal(base + 2)


def test_future_path_mutation_changes_only_new_label_never_signal():
    rows = pattern()
    before = finding(rows)
    rows[40].update(high="500", close="500")
    rows[100].update(low="1", open="1", close="1")
    after = finding(rows)
    assert before["event"] == after["event"]
    assert before["path"] == after["path"]
    assert before["label"] == after["label"]


def test_full_targets_original_metadata_and_zero_weeks_remain_immutable():
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


def test_known_invalidation_reasons_and_unknown_label_possibilities_count_separately():
    invalid = pattern(symbol=SYMBOLS[1])
    invalid[29].update(low="100", close="100")
    rows = pattern()
    rows.pop(50)
    cb, chunks = inputs(observations={(DAYS[0], SYMBOLS[0]): rows, (DAYS[0], SYMBOLS[1]): invalid})
    summary = calibration.build_calibration(cb, chunks)["variants"]["D+1"]["by_arm"][ARM]
    assert summary["signal_counts"] == {"signal": 1, "known_no_signal": 1, "unknown": 38}
    assert summary["label_counts"] == {"known": 0, "unknown": 1}
    assert summary["known_no_signal_reason_counts"] == {"first_contact_close_at_or_below_range_high": 1}
    assert summary["frequency"]["maximum_known_labels_including_unknown"] == 39
    assert summary["comparison_choice_reasons"] == ["unknown_own_labels", "undefined_own_label_sizing"]


def test_raw_offsets_retrieval_and_exact_chunk_hash_stay_distinct():
    rows = pattern()
    rows[0]["open_time_ms"] += 12345
    cb, chunks = inputs(observations={(DAYS[0], SYMBOLS[0]): rows})
    row = calibration.build_calibration(cb, chunks)["variants"]["D+1"]["days"][0]
    assert row["data_vintage"] == hashlib.sha256(calibration._canonical(chunks[DAYS[0], SYMBOLS[0]]) + b"\n").hexdigest()
    assert row["raw_chunk_metadata"]["available_at"] is None
    assert row["source_fetched_at"] == ["2026-10-20T00:00:00+00:00"]
    assert row["source_offset_counts_ms"] == {12345: 1, 0: 389}
    assert row["arms"][ARM]["event"]["retrieved_at"] == row["raw_chunk_metadata"]["retrieved_at"]


def test_own_dispersion_uses_new_labels_and_all_dates_without_performance_summary():
    days = ("2025-09-04", "2025-09-05", "2025-09-08")
    observations = {(day, SYMBOLS[0]): pattern(day) for day in days[:2]}
    observations[days[0], SYMBOLS[0]][65].update(open="104.04", high="105")
    observations[days[1], SYMBOLS[0]][65].update(open="99.96", low="99")
    cb, chunks = inputs(days, observations)
    report = calibration.build_calibration(cb, chunks)
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


def test_one_known_label_date_never_becomes_own_sizing_pass():
    observations = {(DAYS[0], symbol): pattern(symbol=symbol) for symbol in SYMBOLS[:2]}
    cb, chunks = inputs(observations=observations)
    summary = calibration.build_calibration(cb, chunks)["variants"]["D+1"]["by_arm"][ARM]
    assert summary["own_label_dispersion"]["known_labels"] == 2
    assert summary["own_label_dispersion"]["mde_bps"] is None
    assert summary["comparison_choice_reasons"] == ["undefined_own_label_sizing"]


@pytest.mark.parametrize("day,count", [("2025-11-13", 390), ("2026-01-02", 330)])
def test_assumed_exceptional_session_schedules_preserve_new_clocks(day, count):
    result = finding(pattern(day, count=count), day)
    assert result["event"]["signal_bar_end"].hour == 10
    assert result["label"]["exit_at"] == cc.session_open(day) + 65 * cc.MINUTE


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
def test_source_contract_mutations_fail_closed(mutation):
    cb, chunks = inputs()
    mutation(cb, chunks)
    with pytest.raises((calibration.OpeningRetestRefusal, calibration.ct.OpeningRangeRefusal, cc.CalibrationRefusal)):
        calibration.build_calibration(cb, chunks)


def test_variant_target_union_consumes_each_original_symbol_day_once():
    cb, chunks = inputs(days=(DAYS[0],))
    symbol = "KRX:000021"
    cb["variants"]["D+2"]["days"][0]["targets"][-1]["symbol"] = symbol
    chunks[DAYS[0], symbol] = {**deepcopy(chunks[DAYS[0], SYMBOLS[-1]]), "symbol": symbol}
    report = calibration.build_calibration(cb, chunks)
    assert report["input_evidence"]["unique_target_days"] == 21
    assert len(report["variants"]["D+2"]["days"]) == 20


def test_iterator_order_population_and_final_digest_must_all_pass():
    cb, chunks = inputs()
    ordered = list(chunks.values())
    for iterator, reason in [(reversed(ordered), "order_or_identity"),
            (iter(ordered[:-1]), "missing_input"), (iter([*ordered, ordered[-1]]), "extra_input")]:
        with pytest.raises(calibration.OpeningRetestRefusal, match=reason):
            calibration.build_calibration(cb, iterator)

    def failed_end():
        yield from ordered
        raise calibration.OpeningRetestRefusal("final_digest_failed")

    with pytest.raises(calibration.OpeningRetestRefusal, match="final_digest_failed"):
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
    with pytest.raises(calibration.OpeningRetestRefusal, match="stream_hash"):
        list(calibration.archive_chunks(path, lambda: None))


@pytest.mark.parametrize("stream,reason", [(b"{}", "framing"), (b'{ "a": 1 }\n', "canonical_framing")])
def test_nonoriginal_or_incomplete_archive_framing_refuses(tmp_path, stream, reason):
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(stream))
    with pytest.raises(calibration.OpeningRetestRefusal, match=reason):
        list(calibration.archive_chunks(path, lambda: None))


@pytest.mark.parametrize("limit", ["maximum_chunk_bytes", "maximum_uncompressed_bytes", "maximum_chunks"])
def test_archive_limits_fail_before_truncation(tmp_path, monkeypatch, limit):
    _, chunks = inputs(days=(DAYS[0],))
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(b"".join(calibration._canonical(chunk) + b"\n" for chunk in chunks.values())))
    monkeypatch.setattr(calibration, "RESOURCES", {**calibration.RESOURCES, limit: 1})
    with pytest.raises(calibration.OpeningRetestRefusal, match="budget_or_framing"):
        list(calibration.archive_chunks(path, lambda: None))


def test_resource_checkpoint_failure_never_returns_partial_report():
    def stop():
        raise RuntimeError("invented_budget_stop")

    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        calibration.build_calibration(*inputs(), checkpoint=stop)


def test_full257dates56weeks_and_all_months_keep_zero_opportunities():
    candidates = []
    current, end = date(2025, 9, 4), date(2026, 9, 23)
    while current <= end:
        if current.weekday() < 5:
            candidates.append(current.isoformat())
        current += timedelta(days=1)
    removals = set(candidates[10::5][:len(candidates) - 257])
    days = [day for day in candidates if day not in removals]
    cb, chunks = inputs(days)
    report = calibration.build_calibration(cb, iter(chunks.values()))
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
