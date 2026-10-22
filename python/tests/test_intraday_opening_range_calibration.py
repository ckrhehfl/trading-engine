"""Invented inputs only: new full-population opening ranges and own sizing."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
import math
from statistics import stdev

import pytest

from research import intraday_opening_range_calibration as calibration


cc = calibration.cc
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


def pattern(day=DAYS[0], symbol=SYMBOLS[0], kind="shared"):
    rows = raw_rows(day, symbol)
    if kind in {"earlier", "early_only", "early_unknown"}:
        rows[19].update(high="102", close="102")
    if kind in {"shared", "earlier"}:
        rows[34].update(high="103", close="103")
    if kind == "early_unknown":
        del rows[42]
    return rows


def inputs(days=DAYS, observations=None):
    observations = observations or {}
    cb = {
        "study_id": "intraday-universe-sources-v1",
        "mode": "discovery-universe-source-and-coverage-only",
        **cc.FLAGS,
        "calendar": {"rows": [{"date": day, "fetched_at": None} for day in days], "certified": False},
        "variants": {},
    }
    chunks = {}
    for variant in ("D+1", "D+2"):
        memberships = []
        for day in days:
            targets = [{"symbol": symbol, "rank": rank, "stored_row_count": None,
                "state": "no_stored_rows_unknown_reason"} for rank, symbol in enumerate(SYMBOLS, 1)]
            memberships.append({
                "session": day, "observation_date": "2025-08-29",
                "selected_at": day + "T08:30:00+09:00",
                "available_at": "2025-09-01T08:30:00+09:00",
                "retrieved_at": {"fixture": "2026-10-20T00:00:00+00:00"},
                "source": "invented monthly source", "is_final": None,
                "data_vintage": {"fixture": "b" * 64}, "evidence_level": "assumed",
                "targets": targets,
            })
        cb["variants"][variant] = {"days": memberships, "summary": {"fixture_unknowns_preserved": True}}
    for day in days:
        for symbol in SYMBOLS:
            chunks[day, symbol] = {
                "symbol": symbol, "session": day, "observation_date": day,
                "available_at": None, "retrieved_at": "2026-10-20T01:00:00+00:00",
                "source": "collector klines", "is_final": None, "evidence_level": "assumed",
                "rows": deepcopy(observations.get((day, symbol), [])),
            }
    return cb, chunks


def finding(rows, arm="15", day=DAYS[0]):
    session = cc.aggregate_session(rows, cc.session_open(day))
    metadata = {"observation_date": day, "retrieved_at": "2026-10-20T01:00:00+00:00"}
    return calibration._session_findings(session, metadata, "a" * 64, len(rows))[arm]


def specification():
    return {"id": calibration.SPECIFICATION_ID, "mode": calibration.MODE,
        "window": calibration.WINDOW, "parameters": deepcopy(calibration.PARAMETERS),
        "resources": dict(calibration.RESOURCES), **calibration.REGISTERED_PATHS,
        "upstream_sha256": calibration.UPSTREAM_SHA256,
        "input_compressed_sha256": calibration.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": calibration.INPUT_STREAM_SHA256}


def test_exact_registration_and_all_guards_are_explicit():
    calibration.validate_spec(specification())
    assert calibration.PARAMETERS["range_minutes"] == {"15": 15, "30": 30}
    assert calibration.PARAMETERS["bootstrap_draws"] == 2000
    assert calibration.PARAMETERS["bootstrap_seed"] == 0
    assert calibration.PARAMETERS["block_sessions"] == 5
    assert calibration.RESOURCES["cpu_seconds"] == 300
    assert calibration.RESOURCES["address_space_mib"] == 512
    assert calibration.RESOURCES["wall_seconds"] == 1200


@pytest.mark.parametrize("mutate", [
    lambda spec: spec["parameters"].update(range_minutes={"15": 10, "30": 30}),
    lambda spec: spec["parameters"].update(bootstrap_draws=1999),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec.update(input_stream_sha256="a" * 64),
    lambda spec: spec.update(output_directory="/tmp/unregistered"),
    lambda spec: spec["resources"].update(cpu_seconds=600),
    lambda spec: spec.update(extra=True),
])
def test_scope_drift_refuses(mutate):
    spec = specification()
    mutate(spec)
    with pytest.raises(calibration.OpeningRangeRefusal, match="specification_scope"):
        calibration.validate_spec(spec)


def test_shorter_range_first_subsequent_bar_and_unchanged_entry_wait():
    rows = pattern(kind="earlier")
    early, control = (finding(rows, arm) for arm in calibration.ARMS)
    opening = cc.session_open(DAYS[0])
    assert early["event"]["signal_bar_end"] == opening + 20 * cc.MINUTE
    assert control["event"]["signal_bar_end"] == opening + 35 * cc.MINUTE
    for result in (early, control):
        event, label = result["event"], result["label"]
        assert event["decision_at"] == event["signal_bar_end"] + 2 * cc.MINUTE
        assert label["entry_at"] == event["decision_at"] + 3 * cc.MINUTE
        assert label["exit_at"] == label["entry_at"] + 30 * cc.MINUTE
        assert label["label_available_at"] == label["exit_at"] + 3 * cc.MINUTE


def test_initial_bar_and_equal_high_do_not_trigger():
    rows = raw_rows()
    rows[14].update(high="101", close="101")
    rows[19].update(high="101", close="101")
    rows[24].update(high="102", close="102")
    result = finding(rows)
    assert result["event"]["signal_bar_end"] == cc.session_open(DAYS[0]) + 25 * cc.MINUTE


def test_cap_uses_minute_open_not_previous_close_and_exact_arithmetic():
    rows = pattern()
    rows[40].update(open="125.00000000000000000000000001", high="126")
    rows[69].update(close="150", high="150")
    rows[70].update(open="137.5", high="138")
    with localcontext() as context:
        context.prec = 3
        result = finding(rows)["label"]
    assert result["entry_at"] == cc.session_open(DAYS[0]) + 40 * cc.MINUTE
    assert result["exit_open"] == Decimal("137.5")
    assert result["price_change"] == Fraction(Decimal("137.5")) / Fraction(Decimal(rows[40]["open"])) - 1
    assert result["price_change"] != Fraction(Decimal("150")) / Fraction(Decimal(rows[40]["open"])) - 1


@pytest.mark.parametrize("offset", [40, 55, 70])
def test_each_of31_buckets_required_even_with_endpoint_prices(offset):
    rows = pattern()
    del rows[offset]
    result = finding(rows)
    assert result["status"] == "signal"
    label = result["label"]
    assert label["status"] == "unknown"
    assert label["price_change"] is None
    assert label["unknown_minute_buckets"] == 1
    assert label["unknown_reasons"] == ["label_missing_minute_buckets"]
    assert label["required_minute_buckets"] == 31


def test_new30minute_population_never_inherits120minute_missingness():
    rows = pattern()
    del rows[100]
    result = finding(rows)
    assert result["status"] == "signal"
    assert result["label"]["status"] == "known"
    assert result["label"]["unknown_minute_buckets"] == 0


def test_known_no_signal_needs_full47_bar_prefix_and_preserves_no_rows_unknown():
    assert finding(raw_rows(count=235))["status"] == "known_no_signal"
    assert finding(raw_rows(count=234))["status"] == "unknown"
    result = finding([])
    assert result["required_bars"] == 47
    assert result["unknown_reasons"] == ["no_saved_rows_unknown_cause"]
    assert result["label"] is None and result["event"] is None


@pytest.mark.parametrize("mutation,reason", [
    (lambda rows: rows.append(dict(rows[50])), "conflict"),
    (lambda rows: rows[50].update(close="NaN"), "invalid_price"),
    (lambda rows: rows.pop(50), "missing"),
])
def test_future_defects_keep_signal_and_distinguish_label_reasons(mutation, reason):
    rows = pattern()
    mutation(rows)
    result = finding(rows)
    assert result["status"] == "signal"
    assert result["label"]["unknown_bucket_reason_counts"][reason] == 1
    assert result["label"]["unknown_reasons"] == [f"label_{reason}_minute_buckets"]


@pytest.mark.parametrize("kind,state", [
    ("shared", "shared_same_timing"),
    ("earlier", "shared_changed_timing_15_earlier"),
    ("early_only", "15_only_30_known_no_signal"),
    ("early_unknown", "15_only_30_unknown"),
    ("none", "both_known_no_signal"),
])
def test_event_membership_and_timing_categories(kind, state):
    rows = pattern(kind=kind)
    session = cc.aggregate_session(rows, cc.session_open(DAYS[0]))
    findings = calibration._session_findings(session,
        {"observation_date": DAYS[0], "retrieved_at": "2026-10-20T01:00:00+00:00"}, "a" * 64, len(rows))
    assert calibration._timing(findings) == state


def test_impossible_control_only_or_later15_signal_fails_closed():
    early = finding(pattern(kind="earlier"), "15")
    control = finding(pattern(kind="earlier"), "30")
    with pytest.raises(calibration.OpeningRangeRefusal, match="without_fifteen"):
        calibration._timing({"15": {"status": "unknown"}, "30": control})
    early["event"]["decision_at"] = control["event"]["decision_at"] + cc.MINUTE
    with pytest.raises(calibration.OpeningRangeRefusal, match="later_than_thirty"):
        calibration._timing({"15": early, "30": control})


def test_preserves_whole_target_population_metadata_unknowns_and_zero_week():
    observations = {(DAYS[0], SYMBOLS[0]): pattern()}
    cb, chunks = inputs(observations=observations)
    expected_cb, expected_chunks = deepcopy(cb), deepcopy(chunks)
    checkpoints = []
    report = calibration.build_calibration(cb, chunks, checkpoint=lambda: checkpoints.append(True))
    assert cb == expected_cb and chunks == expected_chunks
    assert report["source_target_matrix"] == cb["variants"]
    assert report["upstream_calendar"] == cb["calendar"]
    assert report["reference_sessions"] == list(DAYS)
    assert report["reference_weeks"] == ["2025-W36", "2025-W37"]
    for key, variant in report["variants"].items():
        assert len(variant["days"]) == 40
        for row in variant["days"]:
            membership = cb["variants"][key]["days"][DAYS.index(row["session"])]
            assert row["membership_target"] == next(item for item in membership["targets"] if item["symbol"] == row["symbol"])
            assert row["membership_source"] == {name: value for name, value in membership.items() if name != "targets"}
            assert set(row["arms"]) == {"15", "30"}
        for summary in variant["by_arm"].values():
            assert summary["signal_counts"] == {"signal": 1, "known_no_signal": 0, "unknown": 39}
            assert summary["by_week"]["2025-W37"]["signal_counts"]["signal"] == 0
            assert summary["frequency"]["signals_per_reference_week"] == Fraction(1, 2)
            assert summary["frequency"]["forced_trades"] is False
            assert summary["requires_operator_choice_before_comparison"] is True
            assert "undefined_own_label_sizing" in summary["comparison_choice_reasons"]
    assert len(checkpoints) > len(chunks)
    cb["variants"]["D+1"]["days"][0]["targets"][0]["rank"] = 99
    assert report["source_target_matrix"]["D+1"]["days"][0]["targets"][0]["rank"] == 1


def test_raw_retrieval_fetched_offsets_and_exact_chunk_vintage_stay_distinct():
    rows = pattern()
    rows[0]["open_time_ms"] += 12345
    cb, chunks = inputs(observations={(DAYS[0], SYMBOLS[0]): rows})
    report = calibration.build_calibration(cb, chunks)
    row = report["variants"]["D+1"]["days"][0]
    assert row["data_vintage"] == hashlib.sha256(calibration._canonical(chunks[DAYS[0], SYMBOLS[0]]) + b"\n").hexdigest()
    assert row["raw_chunk_metadata"]["available_at"] is None
    assert row["raw_chunk_metadata"]["retrieved_at"] == "2026-10-20T01:00:00+00:00"
    assert row["source_fetched_at"] == ["2026-10-20T00:00:00+00:00"]
    assert row["source_offset_counts_ms"] == {12345: 1, 0: 389}
    assert row["arms"]["15"]["event"]["retrieved_at"] == row["raw_chunk_metadata"]["retrieved_at"]


def test_own_statistics_use_each_arms_labels_all_dates_and_no_performance_summary():
    observations = {(day, SYMBOLS[0]): pattern(day, kind="earlier") for day in DAYS}
    observations[DAYS[0], SYMBOLS[0]][55].update(open="102", high="102")
    observations[DAYS[1], SYMBOLS[0]][55].update(open="98", low="98")
    cb, chunks = inputs(observations=observations)
    report = calibration.build_calibration(cb, chunks)
    by_arm = report["variants"]["D+1"]["by_arm"]
    early = by_arm["15"]["own_label_dispersion"]
    assert early["event_sd"] == pytest.approx(stdev([.02, -.02]))
    assert early["naive_se"] == pytest.approx(.02)
    assert early["design_se"] == max(early[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert early["mde_bps"] > 33
    assert by_arm["15"]["comparison_choice_reasons"] == ["own_label_mde_exceeds_assumed_cost_floor"]
    assert by_arm["30"]["own_label_dispersion"]["event_sd"] == 0
    assert by_arm["30"]["requires_operator_choice_before_comparison"] is False
    for summary in by_arm.values():
        own = summary["own_label_dispersion"]
        assert own["reference_sessions"] == 2
        assert own["known_label_sessions"] == 2
        assert not {"mean", "net_mean", "gross_mean", "p_value", "cost_exceedance"} & set(own)
    assert report["comparison_performed"] is False
    assert report["family_adopted"] is False
    assert report["full_family_certified"] is False


def test_unknown_label_is_kept_in_own_sizing_denominator_and_choice():
    observations = {(day, SYMBOLS[0]): pattern(day) for day in DAYS}
    observations[DAYS[0], SYMBOLS[0]].pop(55)
    cb, chunks = inputs(observations=observations)
    summary = calibration.build_calibration(cb, chunks)["variants"]["D+1"]["by_arm"]["15"]
    assert summary["signal_counts"]["signal"] == 2
    assert summary["label_counts"] == {"known": 1, "unknown": 1}
    assert summary["own_label_dispersion"]["events"] == 2
    assert "unknown_own_labels" in summary["comparison_choice_reasons"]


def test_zero_event_dates_contribute_to_cluster_and_whole_day_bootstrap():
    days = ("2025-09-04", "2025-09-05", "2025-09-08")
    observations = {(day, SYMBOLS[0]): pattern(day) for day in days[:2]}
    observations[days[0], SYMBOLS[0]][70].update(open="102", high="102")
    observations[days[1], SYMBOLS[0]][70].update(open="98", low="98")
    cb, chunks = inputs(days, observations)
    report = calibration.build_calibration(cb, chunks)
    own = report["variants"]["D+1"]["by_arm"]["30"]["own_label_dispersion"]
    assert own["reference_sessions"] == 3 and own["known_label_sessions"] == 2
    assert own["session_cluster_se"] == pytest.approx(math.sqrt(3 / 2 * (.02 ** 2 + (-.02) ** 2)) / 2)
    assert own == cc.dispersion([
        {"session": days[0], "label": Fraction(1, 50)},
        {"session": days[1], "label": Fraction(-1, 50)},
    ], list(days))


def test_membership_variant_union_reads_each_original_target_once():
    cb, chunks = inputs(days=(DAYS[0],))
    new_symbol = "KRX:000021"
    cb["variants"]["D+2"]["days"][0]["targets"][-1]["symbol"] = new_symbol
    chunks[DAYS[0], new_symbol] = {**deepcopy(chunks[DAYS[0], SYMBOLS[-1]]), "symbol": new_symbol}
    report = calibration.build_calibration(cb, chunks)
    assert report["input_evidence"]["unique_target_days"] == 21
    assert {row["symbol"] for row in report["variants"]["D+1"]["days"]} == set(SYMBOLS)
    assert {row["symbol"] for row in report["variants"]["D+2"]["days"]} == set(SYMBOLS[:-1]) | {new_symbol}
    assert len(report["variants"]["D+2"]["days"]) == 20


def test_undefined_date_replication_never_becomes_sizing_pass():
    observations = {(DAYS[0], symbol): pattern(symbol=symbol) for symbol in SYMBOLS[:2]}
    cb, chunks = inputs(observations=observations)
    summary = calibration.build_calibration(cb, chunks)["variants"]["D+1"]["by_arm"]["15"]
    own = summary["own_label_dispersion"]
    assert own["known_labels"] == 2 and own["known_label_sessions"] == 1
    assert own["mde_bps"] is None
    assert own["planning_constraint"] == "insufficient_known_label_sessions"
    assert summary["requires_operator_choice_before_comparison"] is True
    assert summary["comparison_choice_reasons"] == ["undefined_own_label_sizing"]


@pytest.mark.parametrize("day,count", [("2025-11-13", 390), ("2026-01-02", 330)])
def test_assumed_exceptional_session_clocks_keep_same_signal_and_cap(day, count):
    rows = raw_rows(day, count=count)
    rows[34].update(close="102", high="102")
    result = finding(rows, day=day)
    assert result["label"]["status"] == "known"
    assert result["event"]["signal_bar_end"].hour == 10
    assert result["label"]["exit_at"] == cc.session_open(day) + 70 * cc.MINUTE


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
def test_target_causality_certification_and_raw_contract_mutations_refuse(mutation):
    cb, chunks = inputs()
    mutation(cb, chunks)
    with pytest.raises((calibration.OpeningRangeRefusal, cc.CalibrationRefusal)):
        calibration.build_calibration(cb, chunks)


def test_iterator_must_have_exact_order_population_and_final_digest_check():
    cb, chunks = inputs()
    ordered = list(chunks.values())
    with pytest.raises(calibration.OpeningRangeRefusal, match="order_or_identity"):
        calibration.build_calibration(cb, reversed(ordered))
    with pytest.raises(calibration.OpeningRangeRefusal, match="missing_input"):
        calibration.build_calibration(cb, iter(ordered[:-1]))
    with pytest.raises(calibration.OpeningRangeRefusal, match="extra_input"):
        calibration.build_calibration(cb, iter([*ordered, ordered[-1]]))

    def final_check():
        yield from ordered
        raise calibration.OpeningRangeRefusal("final_digest_failed")

    with pytest.raises(calibration.OpeningRangeRefusal, match="final_digest_failed"):
        calibration.build_calibration(cb, final_check())


def test_archive_parser_uses_exact_framing_hash_and_checkpoints(tmp_path, monkeypatch):
    _, chunks = inputs(days=(DAYS[0],))
    expected = list(chunks.values())
    stream = b"".join(calibration._canonical(chunk) + b"\n" for chunk in expected)
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(stream))
    monkeypatch.setattr(calibration, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    checkpoints = []
    assert list(calibration.archive_chunks(path, lambda: checkpoints.append(True))) == expected
    assert len(checkpoints) == len(expected) + 1
    monkeypatch.setattr(calibration, "INPUT_STREAM_SHA256", "0" * 64)
    with pytest.raises(calibration.OpeningRangeRefusal, match="stream_hash"):
        list(calibration.archive_chunks(path, lambda: None))


@pytest.mark.parametrize("stream,reason", [
    (b"{}", "framing"),
    (b'{ "a": 1 }\n', "canonical_framing"),
])
def test_archive_nonoriginal_or_incomplete_framing_refuses(tmp_path, stream, reason):
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(stream))
    with pytest.raises(calibration.OpeningRangeRefusal, match=reason):
        list(calibration.archive_chunks(path, lambda: None))


@pytest.mark.parametrize("limit", ["maximum_chunk_bytes", "maximum_uncompressed_bytes", "maximum_chunks"])
def test_saved_archive_budgets_fail_closed_before_truncation(tmp_path, monkeypatch, limit):
    _, chunks = inputs(days=(DAYS[0],))
    stream = b"".join(calibration._canonical(chunk) + b"\n" for chunk in chunks.values())
    path = tmp_path / "invented.jsonl.gz"
    path.write_bytes(gzip.compress(stream))
    monkeypatch.setattr(calibration, "RESOURCES", {**calibration.RESOURCES, limit: 1})
    with pytest.raises(calibration.OpeningRangeRefusal, match="budget_or_framing"):
        list(calibration.archive_chunks(path, lambda: None))


def test_resource_checkpoints_can_stop_production_without_partial_return():
    cb, chunks = inputs()

    def stop():
        raise RuntimeError("invented_budget_stop")

    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        calibration.build_calibration(cb, chunks, checkpoint=stop)


def test_full257_reference_dates_and56_weeks_include_zero_opportunity_weeks():
    # Geometry only; all raw chunks are empty, not a projection of actual data.
    from datetime import date

    candidates = []
    current = date(2025, 9, 4)
    end = date(2026, 9, 23)
    while current <= end:
        if current.weekday() < 5:
            candidates.append(current.isoformat())
        current += timedelta(days=1)
    # Spread removals within existing weeks to keep all56 original ISO weeks.
    removals = set(candidates[10::5][:len(candidates) - 257])
    days = [day for day in candidates if day not in removals]
    assert len(days) == 257 and len({calibration._week(day) for day in days}) == 56
    cb, chunks = inputs(days=days)
    report = calibration.build_calibration(cb, iter(chunks.values()))
    assert len(report["reference_sessions"]) == 257
    assert len(report["reference_weeks"]) == 56
    for variant in report["variants"].values():
        assert len(variant["days"]) == 5140
        for summary in variant["by_arm"].values():
            assert len(summary["by_week"]) == 56
            assert summary["signal_counts"] == {"signal": 0, "known_no_signal": 0, "unknown": 5140}
            assert all(item["signal_counts"]["signal"] == 0 for item in summary["by_week"].values())
            assert summary["own_label_dispersion"]["reference_sessions"] == 257
            assert summary["own_label_dispersion"]["mde_bps"] is None
