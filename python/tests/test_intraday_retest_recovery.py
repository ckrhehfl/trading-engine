"""Invented-only fixed-label recovery, exact overlap, own-sizing and no-fill gates."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
import json

import pytest

from research import intraday_retest_recovery as recovery
from test_intraday_opening_retest_calibration import inputs, pattern, SYMBOLS


DAYS = ("2026-08-03", "2026-08-10", "2026-08-17")


def wire(value):
    return json.loads(recovery._canonical(value))


def example(*, cap_prices=("102", "102"), defect="missing"):
    observations, complete = {}, {}
    for day, cap in zip(DAYS, cap_prices):
        for index in (0, 1):
            raw = pattern(day, SYMBOLS[index])
            raw[65].update(open=cap, high=str(max(Decimal("102"), Decimal(cap))), low=str(min(Decimal("101"), Decimal(cap))))
            observations[day, SYMBOLS[index]] = deepcopy(raw)
            if index == 0:
                complete[day, SYMBOLS[index]] = deepcopy(raw)
                if defect == "missing":
                    observations[day, SYMBOLS[index]].pop(50)
                elif defect == "conflict":
                    observations[day, SYMBOLS[index]].append(deepcopy(raw[50]))
                else:
                    observations[day, SYMBOLS[index]][50]["close"] = "NaN"
    cb, chunks = inputs(days=DAYS, observations=observations)
    source = wire(recovery.dc.build_calibration(cb, chunks))
    plan = recovery.derive_requests(source)
    selected = {(item["session"], item["symbol"]): chunks[item["session"], item["symbol"]] for item in plan}
    responses = {}
    for item in plan:
        key = item["session"], item["symbol"]
        output = []
        for row in complete[key][35:66]:
            clock = recovery.datetime.fromtimestamp(row["open_time_ms"] / 1000, recovery.cc.KST)
            output.append({"stck_bsop_date": item["session"].replace("-", ""),
                "stck_cntg_hour": clock.strftime("%H%M%S"),
                **{field: row[source_field] for field, source_field in zip(recovery.PRICE_FIELDS, ("open", "high", "low", "close"))}})
        responses[key] = {"requested_at": "2026-10-24T09:00:00+09:00",
            "retrieved_at": "2026-10-24T09:00:01+09:00", "status": "received",
            "response": {"rt_cd": "0", "msg_cd": "MCA00000", "output2": list(reversed(output))}}
    return source, selected, responses


@pytest.fixture(scope="module")
def baseline():
    return example()


def test_committed_specification_and_privacy_roles():
    spec = json.loads(recovery.SPECIFICATION_PATH.read_text())
    recovery.validate_spec(spec)
    assert spec["resources"]["maximum_market_gets"] == 2
    assert spec["resources"]["maximum_rows_per_response"] == 120
    assert spec["resources"]["automatic_retries"] == 0
    assert "output_directory" not in spec and "canonical_runs_path" not in spec
    assert "/home/" not in json.dumps(spec)


@pytest.mark.parametrize("field,value", [
    ("cpu_seconds", 301), ("maximum_market_gets", 3),
    ("maximum_rows_per_response", 121), ("automatic_retries", 1),
])
def test_specification_resource_mutations_refuse(field, value):
    spec = json.loads(recovery.SPECIFICATION_PATH.read_text())
    spec["resources"][field] = value
    with pytest.raises(recovery.RecoveryRefusal, match="specification_scope_mismatch"):
        recovery.validate_spec(spec)


def test_requests_union_only_known_signals_with_unknown_labels(baseline):
    source, chunks, _ = baseline
    plan = recovery.derive_requests(source)
    assert [(item["session"], item["symbol"]) for item in plan] == sorted(chunks)
    assert len(plan) == 2
    for request in plan:
        assert request["policies"] == ["D+1", "D+2"]
        assert len(request["required_minute_buckets"]) == 31
        assert len(request["original_missing_minute_buckets"]) == 1
        assert request["request_parameters"]["FID_INPUT_HOUR_1"] == "100559"
        assert request["request_parameters"]["FID_FAKE_TICK_INCU_YN"] == "N"
        assert all(item["state"] == "missing" for item in request["original_gap_intervals"])


def test_recovery_preserves_inputs_all_targets_original_signals_and_source_unknowns(baseline):
    source, chunks, responses = deepcopy(baseline)
    before = [recovery._canonical(value) for value in (source, chunks_to_list(chunks), chunks_to_list(responses))]
    report = recovery.build_recovery(source, chunks, responses)
    assert report["comparison_performed"] is True
    assert report["recovery_complete"] is True
    assert report["source_rows_overwritten"] == 0
    for policy in recovery.POLICIES:
        variant = report["variants"][policy]
        assert len(variant["days"]) == 60
        assert len(variant["records"]) == 4
        for old, new in zip(source["variants"][policy]["days"], variant["days"], strict=True):
            assert old == {key: value for key, value in new.items() if key != "effective_label"}
        own = variant["by_arm"][recovery.ARM]["own_label_dispersion"]
        assert own["known_labels"] == 4 and own["unknown_labels"] == 0
        assert own["reference_sessions"] == 3
        assert variant["by_arm"][recovery.ARM]["signal_counts"]["unknown"] == 56
    assert before == [recovery._canonical(value) for value in (source, chunks_to_list(chunks), chunks_to_list(responses))]


def chunks_to_list(mapping):
    return [{"key": list(key), "value": value} for key, value in sorted(mapping.items())]


def test_exact_gross_net_cost_medians_rates_zero_event_week_and_attribution(baseline):
    report = recovery.build_recovery(*baseline)
    for variant in report["variants"].values():
        comparison = variant["comparison"]
        assert comparison["gross_mean"] == Fraction(0)
        assert comparison["net_mean"] == Fraction(-33, 10000)
        assert comparison["gross_median"] == Fraction(0)
        assert comparison["net_median"] == Fraction(-33, 10000)
        assert comparison["cost_exceeding"] == 0
        assert comparison["gross_win_fraction"] == Fraction(0)
        assert comparison["net_win_fraction"] == Fraction(0)
        assert len(comparison["by_week"]) == 3
        empty = comparison["by_week"][recovery.ct._week(DAYS[2])]
        assert empty["event_count"] == 0 and empty["gross_mean"] is None
        assert comparison["label_origin"]["recovered"]["event_count"] == 2
        assert comparison["label_origin"]["original_known"]["event_count"] == 2
        assert sum(item["gross_sum"] for item in comparison["label_origin"].values()) == comparison["gross_sum"]
        assert sum(item["net_sum"] for item in comparison["label_origin"].values()) == comparison["net_sum"]
    assert report["matched_control_comparison"] is False
    assert report["incremental_edge_claimed"] is False
    assert report["actual_portfolio_pnl"] is False
    assert report["underpower_exception_applied"] is False


@pytest.mark.parametrize("which", ["source", "response"])
def test_coordinate_population_must_equal_request_union(baseline, which):
    source, chunks, responses = deepcopy(baseline)
    target = chunks if which == "source" else responses
    target.pop(next(iter(target)))
    with pytest.raises(recovery.RecoveryRefusal, match="population_mismatch"):
        recovery.build_recovery(source, chunks, responses)


@pytest.mark.parametrize("mutate", [
    lambda chunk: chunk["rows"][0].update(open="99"),
    lambda chunk: chunk.update(retrieved_at="2026-10-21T00:00:00+00:00"),
    lambda chunk: chunk.update(symbol=SYMBOLS[1]),
])
def test_source_chunk_tampering_is_fatal_not_market_missingness(baseline, mutate):
    source, chunks, responses = deepcopy(baseline)
    mutate(chunks[next(iter(chunks))])
    with pytest.raises(recovery.RecoveryRefusal, match="source_chunk"):
        recovery.build_recovery(source, chunks, responses)


@pytest.mark.parametrize("defect,reason", [
    ("date", "api_row_date_mismatch"),
    ("clock", "api_row_minute_clock_mismatch"),
    ("nonfinite", "invalid_OHLC"),
    ("negative", "invalid_OHLC"),
    ("zero", "invalid_OHLC"),
    ("float", "invalid_OHLC"),
    ("bounds", "invalid_OHLC"),
    ("missing", "api_required_window_incomplete"),
    ("entry_missing", "api_required_window_incomplete"),
    ("exit_missing", "api_required_window_incomplete"),
    ("unit_revision", "source_vintage_or_price_unit_mismatch"),
    ("duplicate_conflict", "api_duplicate_minute_conflict"),
    ("overbudget", "api_response_row_budget_exceeded"),
    ("failure", "api_response_unsuccessful_or_schema_mismatch"),
    ("unexpected_field", "api_row_schema_mismatch"),
    ("after_exit", "api_row_outside_requested_session_or_exit"),
])
def test_market_price_defects_preserve_unknowns_and_skip_every_mean(baseline, monkeypatch, defect, reason):
    source, chunks, responses = deepcopy(baseline)
    envelope = responses[next(iter(responses))]
    raw = envelope["response"]["output2"]
    row = raw[0]
    if defect == "date":
        row["stck_bsop_date"] = "20260804"
    elif defect == "clock":
        row["stck_cntg_hour"] = "096000"
    elif defect in {"nonfinite", "negative", "zero", "float"}:
        row["stck_oprc"] = {"nonfinite": "NaN", "negative": "-1", "zero": "0", "float": 102.0}[defect]
    elif defect == "bounds":
        row["stck_hgpr"] = "99"
    elif defect == "missing":
        raw.pop(15)
    elif defect == "entry_missing":
        raw.pop()
    elif defect == "exit_missing":
        raw.pop(0)
    elif defect == "unit_revision":
        raw[1].update(stck_oprc="101")
    elif defect == "duplicate_conflict":
        duplicate = dict(raw[1], stck_oprc="101")
        raw.append(duplicate)
    elif defect == "overbudget":
        raw.extend([deepcopy(raw[1]) for _ in range(121)])
    elif defect == "failure":
        envelope["response"]["rt_cd"] = "1"
    elif defect == "unexpected_field":
        row["fake"] = "Y"
    elif defect == "after_exit":
        row["stck_cntg_hour"] = "100600"
    monkeypatch.setattr(recovery, "_summary", lambda *_: pytest.fail("mean before prerequisites"))
    report = recovery.build_recovery(source, chunks, responses)
    assert report["recovery"][0]["reason"] == reason
    assert report["recovery"][0]["original_label"]["status"] == "unknown"
    assert report["recovery"][0]["recovered_label"] is None
    assert report["comparison_performed"] is False
    assert all(variant["comparison"] is None for variant in report["variants"].values())
    assert report["requires_operator_choice_before_comparison"] is True


@pytest.mark.parametrize("mode", ["none", "unavailable", "outside_horizon", "reversed_clock"])
def test_unavailable_retention_and_timing_failures_are_diagnostics(baseline, mode):
    source, chunks, responses = deepcopy(baseline)
    key = next(iter(responses))
    if mode == "none":
        responses[key] = None
    elif mode == "unavailable":
        responses[key].update(status="unavailable", response=None, reason="endpoint_unavailable")
    elif mode == "outside_horizon":
        responses[key].update(requested_at="2028-10-24T00:00:00+09:00", retrieved_at="2028-10-24T00:00:01+09:00")
    else:
        responses[key]["retrieved_at"] = "2026-10-24T08:59:59+09:00"
    report = recovery.build_recovery(source, chunks, responses)
    assert report["comparison_performed"] is False
    if mode == "unavailable":
        assert report["recovery"][0]["unavailable_reason"] == "endpoint_unavailable"
    if mode == "outside_horizon":
        assert report["recovery"][0]["reason"] == "outside_documented_retention_horizon"


def test_identical_duplicate_offsets_coalesce_with_counts(baseline):
    source, chunks, responses = deepcopy(baseline)
    for response in responses.values():
        rows = response["response"]["output2"]
        duplicate = dict(rows[1])
        duplicate["stck_cntg_hour"] = duplicate["stck_cntg_hour"][:-2] + "47"
        rows.append(duplicate)
    report = recovery.build_recovery(source, chunks, responses)
    assert report["comparison_performed"] is True
    for item in report["recovery"]:
        metadata = item["api_metadata"]
        assert metadata["response_rows"] == 32
        assert metadata["unique_minute_buckets"] == 31
        assert metadata["identical_duplicate_rows"] == 1
        assert metadata["source_offset_counts_seconds"] == {"0": 31, "47": 1}


def test_prior_dates_and_preopen_rows_are_counted_and_never_fill_required_prices(baseline):
    source, chunks, responses = deepcopy(baseline)
    for response in responses.values():
        rows = response["response"]["output2"]
        rows.append(dict(rows[0], stck_bsop_date="20260801"))
        rows.append(dict(rows[0], stck_cntg_hour="085959"))
    report = recovery.build_recovery(source, chunks, responses)
    assert report["comparison_performed"] is True
    for item in report["recovery"]:
        assert item["api_metadata"]["discarded_prior_date_rows"] == 1
        assert item["api_metadata"]["discarded_preopen_rows"] == 1
        assert item["api_metadata"]["unique_minute_buckets"] == 31


@pytest.mark.parametrize("defect", ["conflict", "invalid_price"])
def test_existing_conflict_or_invalid_price_can_never_be_overwritten(defect):
    report = recovery.build_recovery(*example(defect=defect))
    assert report["comparison_performed"] is False
    assert report["source_rows_overwritten"] == 0
    assert all(item["reason"] == "original_price_defect_not_missing" for item in report["recovery"])
    assert all(item["added_minute_buckets"] == [] for item in report["recovery"])


def test_price_precision_remains_exact_under_small_decimal_context():
    example_inputs = example(cap_prices=("102.0000000000000000001",) * 2)
    with localcontext() as context:
        context.prec = 6
        report = recovery.build_recovery(*example_inputs)
    expected = Fraction(1, 102 * 10**19)
    assert report["comparison_performed"] is True
    for variant in report["variants"].values():
        assert variant["comparison"]["gross_mean"] == expected
        assert variant["comparison"]["net_mean"] == expected - Fraction(33, 10000)


def test_unavailable_reason_is_restricted_to_stable_codes(baseline):
    source, chunks, responses = deepcopy(baseline)
    responses[next(iter(responses))].update(status="unavailable", response=None,
        reason="arbitrary private response details")
    report = recovery.build_recovery(source, chunks, responses)
    assert report["recovery"][0]["unavailable_reason"] == "invalid_unavailable_reason"
    assert "arbitrary private" not in recovery._canonical(report).decode()


def test_new_sizing_failure_never_inherits_a_waiver(monkeypatch):
    source, chunks, responses = example(cap_prices=("90", "120"))
    monkeypatch.setattr(recovery, "_summary", lambda *_: pytest.fail("failed own sizing emitted a mean"))
    report = recovery.build_recovery(source, chunks, responses)
    assert report["recovery_complete"] is True
    assert report["comparison_performed"] is False
    assert report["underpower_exception_applied"] is False
    for variant in report["variants"].values():
        assert variant["by_arm"][recovery.ARM]["own_label_dispersion"]["mde_bps"] > 33
        assert variant["comparison"] is None


def test_unknown_own_sizing_never_calls_summary(baseline, monkeypatch):
    source, chunks, responses = deepcopy(baseline)
    original = recovery.cc.dispersion
    def undefined(records, reference):
        result = original(records, reference)
        result["mde_bps"] = None
        return result
    monkeypatch.setattr(recovery.cc, "dispersion", undefined)
    monkeypatch.setattr(recovery, "_summary", lambda *_: pytest.fail("undefined own sizing emitted a mean"))
    report = recovery.build_recovery(source, chunks, responses)
    assert report["comparison_performed"] is False
    assert "D+1:undefined_own_label_sizing" in report["comparison_not_run_reasons"]


def test_exact_mean_and_session_uncertainty_use_same_own_sizing(baseline):
    report = recovery.build_recovery(*baseline)
    for variant in report["variants"].values():
        assert variant["comparison"]["uncertainty"] == variant["by_arm"][recovery.ARM]["own_label_dispersion"]
        expected = recovery.cc.dispersion(variant["records"], list(DAYS))
        assert variant["comparison"]["uncertainty"] == expected


def test_stable_order_determinism_and_checkpoints(baseline):
    ticks = []
    first = recovery.build_recovery(*baseline, checkpoint=lambda: ticks.append(True))
    second = recovery.build_recovery(*baseline)
    assert recovery._canonical(first) == recovery._canonical(second)
    assert len(ticks) >= 8
