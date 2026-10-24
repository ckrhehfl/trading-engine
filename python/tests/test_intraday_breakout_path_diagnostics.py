"""Invented reports/chunks only; no saved market evidence or external access."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
import hashlib
import json

import pytest

from research import intraday_breakout_path_diagnostics as diagnostics
from test_intraday_breakout_retest_comparison import DAYS, SYMBOLS, wire
from test_intraday_opening_retest_calibration import inputs, pattern, raw_rows


def _pattern(day, symbol, case, count=235):
    rows = pattern(day, symbol, contact_start=25, count=count)
    for index, row in enumerate(rows[25:55], 25):
        row.update(open="102", high="102.5" if case in (0, 1) else "102.3366",
            low="100" if index == 25 else "101", close="102")
    # Supply the unrelated original thirty-minute arm's own known labels too;
    # its first signal is after the diagnostic's cap, with a constant outcome.
    for row in rows[55:60]:
        row.update(high="103")
    rows[59].update(close="103")
    for row in rows[60:]:
        row.update(open="103", high="103", low="101", close="103")
    endpoint = ("102.3366", "102.4", "102.2", "102.4")[case]
    rows[55].update(open=endpoint, high="103", low="101", close="102")
    rows[0].update(high="100.3299" if case == 2 else "100.33")
    return rows


def _finish_parent(cb, chunks):
    di = diagnostics.di
    basic = wire(di.ct.build_calibration(cb, chunks))
    retest = wire(di.dh.dc.build_calibration(cb, chunks))
    dh = wire(di.dh.build_comparison(basic, retest))
    dh["specification_sha256"] = di.UPSTREAM_SPECIFICATION_SHA256
    result = wire(di.build_comparison(dh))
    assert result["comparison_performed"] is True
    result["specification_sha256"] = diagnostics.UPSTREAM_SPECIFICATION_SHA256
    return result, chunks


def build_synthetic_fixture(days=DAYS):
    observations = {(day, SYMBOLS[index]): _pattern(day, SYMBOLS[index], index)
        for day in days[:-1] for index in range(4)}
    observations.update({(day, SYMBOLS[4]): raw_rows(day, SYMBOLS[4], 235) for day in days})
    return _finish_parent(*inputs(days=days, observations=observations))


def build_profile_fixture(days):
    """Generate full geometry outside the measured DJ process, never market IO.

    The 5140 original invented chunks start with195 rows each; one nonsignal
    chunk loses89 rows. Eighteen D+2 replacements have empty chunks: two remove
    signaled names and sixteen replace unknown names. Thus the original union
    has5158 chunks,1002211 rows and1169/1167 fixed common basic entries.
    """
    assert len(days) == 257
    observations = {}
    for day_index, day in enumerate(days):
        signal_count = (5 if day_index < 145 else 4) if day_index < 256 else 0
        for index, symbol in enumerate(SYMBOLS):
            observations[day, symbol] = (_pattern(day, symbol, index % 4, 195)
                if index < signal_count else raw_rows(day, symbol, 195))
    observations[days[-1], SYMBOLS[-1]] = raw_rows(days[-1], SYMBOLS[-1], 106)
    cb, chunks = inputs(days=days, observations=observations)
    for index, day in enumerate(days[:18]):
        membership = cb["variants"]["D+2"]["days"][index]
        membership["targets"][0 if index < 2 else 19]["symbol"] = "KRX:000021"
        old = chunks[day, SYMBOLS[0]]
        chunks[day, "KRX:000021"] = {**{key: deepcopy(value) for key, value in old.items() if key != "rows"},
            "symbol": "KRX:000021", "rows": []}
    result, chunks = _finish_parent(cb, chunks)
    # A supplied-byte mutation control retains the original known endpoint but
    # marks one internal path bucket invalid. No row or target is removed.
    key = days[0], SYMBOLS[0]
    chunks[key]["rows"][31]["high"] = "NaN"
    _repin(result, chunks, key)
    assert len(chunks) == 5158 and sum(len(chunk["rows"]) for chunk in chunks.values()) == 1002211
    assert [sum(diagnostics._eligible(row) for row in result["variants"][policy]["days"])
        for policy in diagnostics.POLICIES] == [1169, 1167]
    return result, chunks


@lru_cache(maxsize=1)
def _base():
    return build_synthetic_fixture()


def fixture():
    return deepcopy(_base())


def _repin(parent, chunks, key):
    """Invent a changed raw vintage while preserving original known endpoints."""
    chunk = chunks[key]
    vintage = hashlib.sha256(diagnostics._canonical(chunk) + b"\n").hexdigest()
    offsets = {}
    for raw in chunk["rows"]:
        offset = str(raw["open_time_ms"] % 60000)
        offsets[offset] = offsets.get(offset, 0) + 1
    for variant in parent["variants"].values():
        for row in variant["days"]:
            if (row["session"], row["symbol"]) != key:
                continue
            row.update(data_vintage=vintage, input_rows=len(chunk["rows"]), source_offset_counts_ms=offsets)
            for finding in row["arms"].values():
                if finding["event"] is not None:
                    finding["event"]["data_vintage"] = vintage
            row["opening_volatility_feature"] = diagnostics.di._feature(row)
    return parent, chunks


def _case(report, index=0, policy="D+1", day=DAYS[0]):
    return next(row["breakout_path_diagnostic"] for row in report["variants"][policy]["days"]
        if row["session"] == day and row["symbol"] == SYMBOLS[index])


def test_exact_symbolic_registration_and_fixed_budget():
    spec = json.loads(diagnostics.SPECIFICATION_PATH.read_text())
    diagnostics.validate_spec(spec)
    assert "/home/" not in json.dumps(spec)
    assert diagnostics.RESOURCES["maximum_chunks"] == 5158
    assert diagnostics.RESOURCES["maximum_rows"] == 1002211
    assert [diagnostics.RESOURCES[key] for key in ("cpu_seconds", "address_space_mib", "wall_seconds", "nice")] == [300, 512, 1200, 10]
    assert diagnostics.RESOURCES["maximum_market_gets"] == diagnostics.RESOURCES["maximum_token_posts"] == 0


@pytest.mark.parametrize("mutate", [
    lambda spec: spec["parameters"].update(path_minutes=29),
    lambda spec: spec["parameters"]["assumed_cost"].update(numerator="34"),
    lambda spec: spec.update(output_name="another"),
    lambda spec: spec["source_roles"].update(parent="partial_DI"),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(upstream_specification_sha256="a" * 64),
    lambda spec: spec.update(extra=True),
])
def test_registration_mutations_refuse_before_inputs(mutate):
    spec = json.loads(diagnostics.SPECIFICATION_PATH.read_text())
    mutate(spec)
    with pytest.raises(diagnostics.BreakoutPathRefusal, match="specification_scope"):
        diagnostics.build_diagnostics({}, [], spec)


def test_original_population_metadata_masks_labels_features_and_all_weeks_preserved():
    parent, chunks = fixture()
    untouched = deepcopy(parent)
    result = diagnostics.build_diagnostics(parent, chunks)
    assert parent == untouched
    assert result["source_provenance"] == {key: value for key, value in parent.items() if key not in diagnostics.PROVENANCE_EXCLUDE}
    assert result["source_target_matrix"] == parent["source_target_matrix"]
    assert result["upstream_calendar"] == parent["upstream_calendar"]
    for policy, variant in result["variants"].items():
        source = parent["variants"][policy]
        assert variant["source_variant_provenance"] == {key: value for key, value in source.items() if key != "days"}
        for before, after in zip(source["days"], variant["days"], strict=True):
            assert {key: value for key, value in after.items() if key != "breakout_path_diagnostic"} == before
            assert (after["breakout_path_diagnostic"] is not None) == diagnostics._eligible(before)
        assert variant["summary"]["overall"]["total_cases"] == 12
        assert len(variant["days"]) == 80
        assert list(variant["summary"]["by_week"]) == parent["reference_weeks"]
        empty = variant["summary"]["by_week"][diagnostics.ct._week(DAYS[-1])]
        assert empty["total_cases"] == 0 and empty["categories"]["never_exceeded"]["fraction"] is None
    assert _case(result, 2)["category"] == "never_exceeded"  # DI rejects this feature; DJ still diagnoses it.
    assert result["new_performance_families"] == 0 and result["comparison_performed"] is False
    assert result["exit_policy_tested"] is False and result["underpower_exception_applied"] is False


def test_four_partition_and_cap_only_endpoint_is_orthogonal_to_holding_high():
    result = diagnostics.build_diagnostics(*fixture())
    cases = [_case(result, index) for index in range(4)]
    assert [item["category"] for item in cases] == ["exceeded_then_faded", "exceeded_and_retained", "never_exceeded", "never_exceeded"]
    assert cases[0]["endpoint_price_change"] == diagnostics.COST
    assert cases[0]["endpoint_above_cost"] is False
    assert cases[2]["mfe"] == cases[3]["mfe"] == diagnostics.COST
    assert cases[2]["mfe_exceeds_cost"] is False
    assert cases[3]["endpoint_above_cost"] is True and cases[3]["cap_only_exceeded"] is True
    assert cases[3]["giveback"] < 0  # The external cap-open can exceed the holding high.
    summary = result["variants"]["D+1"]["summary"]["overall"]
    assert sum(value["count"] for value in summary["categories"].values()) == summary["total_cases"] == 12
    assert summary["cap_only_exceeded"] == {"count": 3, "fraction": Fraction(1, 4)}
    assert summary["endpoint_above_cost"]["all"] == {"count": 6, "fraction": Fraction(1, 2)}


def test_extrema_earliest_offsets_exact_means_and_same_minute_crossing_order():
    result = diagnostics.build_diagnostics(*fixture())
    value = _case(result)
    assert value["mfe"] == Fraction(1, 204)
    assert value["mae"] == Fraction(-1, 51)
    assert value["giveback"] == Fraction(1, 204) - diagnostics.COST
    assert value["maximum_high_offset_minutes"] == value["minimum_low_offset_minutes"] == 0
    assert value["first_cost_up_offset_minutes"] == value["first_cost_down_offset_minutes"] == 0
    assert value["cost_touch_order"] == "same_minute_order_unknown"
    assert value["path_available_at"] == value["end_at"] + 2 * diagnostics.cc.MINUTE
    assert value["end_at"] == value["entry_at"] + 30 * diagnostics.cc.MINUTE
    summary = result["variants"]["D+1"]["summary"]["overall"]["metrics"]["mfe"]
    expected = [Fraction(1, 204)] * 6 + [diagnostics.COST] * 6
    assert summary == {"count": 12, "mean": sum(expected) / 12,
        "median": (Fraction(1, 204) + diagnostics.COST) / 2, "min": diagnostics.COST, "max": Fraction(1, 204)}
    assert isinstance(summary["mean"], Fraction)


@pytest.mark.parametrize("gap", ["missing", "invalid_price", "conflict"])
def test_internal_path_unknown_keeps_original_endpoint_and_partition(gap):
    parent, chunks = fixture()
    key = DAYS[0], SYMBOLS[0]
    rows = chunks[key]["rows"]
    if gap == "missing":
        rows.pop(31)
        rows.append(raw_rows(*key, count=236)[-1])
    elif gap == "invalid_price":
        rows[31]["high"] = "NaN"
    else:
        rows.append(deepcopy(rows[31]))
    _repin(parent, chunks, key)
    result = diagnostics.build_diagnostics(parent, chunks)
    value = _case(result)
    assert value["category"] == value["status"] == "path_unknown"
    assert value["path_known"] is False and value["missing_minute_buckets"] == 1
    assert value["unknown_bucket_reason_counts"][gap] == 1
    assert value["endpoint_price_change"] == diagnostics.COST and value["endpoint_above_cost"] is False
    assert all(value[name] is None for name in ("mfe", "mae", "giveback", "cap_only_exceeded", "cost_touch_order"))
    summary = result["variants"]["D+1"]["summary"]["overall"]
    assert summary["complete_paths"] == 11 and summary["incomplete_paths"] == 1
    assert summary["total_cases"] == sum(item["count"] for item in summary["categories"].values()) == 12
    assert summary["metrics"]["mfe"]["count"] == 11


@pytest.mark.parametrize("offset", [25, 55])
@pytest.mark.parametrize("kind", ["missing", "price_changed"])
def test_missing_or_changed_entry_cap_anchors_refuse(offset, kind):
    parent, chunks = fixture()
    key = DAYS[0], SYMBOLS[0]
    rows = chunks[key]["rows"]
    if kind == "missing":
        rows.pop(offset)
        rows.append(raw_rows(*key, count=236)[-1])
    else:
        rows[offset].update(open="102.1", high="103", low="100", close="102")
    _repin(parent, chunks, key)
    with pytest.raises(diagnostics.BreakoutPathRefusal, match="raw_anchor"):
        diagnostics.build_diagnostics(parent, chunks)


def test_unused_raw_scope_and_metadata_still_validated():
    parent, chunks = fixture()
    key = DAYS[-1], SYMBOLS[4]
    chunks[key]["rows"][0]["symbol"] = "KRX:999999"
    _repin(parent, chunks, key)
    with pytest.raises(diagnostics.BreakoutPathRefusal, match="registered_scope"):
        diagnostics.build_diagnostics(parent, chunks)


@pytest.mark.parametrize("name,value", [("retrieved_at", "2026-10-21T00:00:00+00:00"),
    ("observation_date", "2025-09-05"), ("is_final", True), ("available_at", "2026-10-20T00:00:00+00:00")])
def test_raw_metadata_cannot_override_original_source(name, value):
    parent, chunks = fixture()
    chunks[DAYS[0], SYMBOLS[0]][name] = value
    with pytest.raises(diagnostics.BreakoutPathRefusal):
        diagnostics.build_diagnostics(parent, chunks)


def test_unused_second_conflicting_raw_record_still_checked_for_scope():
    parent, chunks = fixture()
    key = DAYS[-1], SYMBOLS[4]
    extra = deepcopy(chunks[key]["rows"][0])
    extra["interval"] = "5m"
    chunks[key]["rows"].append(extra)
    _repin(parent, chunks, key)
    with pytest.raises(diagnostics.BreakoutPathRefusal, match="registered_scope"):
        diagnostics.build_diagnostics(parent, chunks)


def test_path_minute_orders_use_first_crossing_and_ignore_cap_high():
    parent, chunks = fixture()
    key = DAYS[0], SYMBOLS[0]
    rows = chunks[key]["rows"]
    for row in rows[25:55]:
        row.update(open="102", high="102", low="102", close="102")
    rows[27]["low"] = "101"
    rows[29]["high"] = rows[30]["high"] = "102.5"
    rows[55]["high"] = "10000"
    _repin(parent, chunks, key)
    result = diagnostics.build_diagnostics(parent, chunks)
    value = _case(result)
    assert value["first_cost_down_offset_minutes"] == 2
    assert value["first_cost_up_offset_minutes"] == value["maximum_high_offset_minutes"] == 4
    assert value["cost_touch_order"] == "down_before_up"
    assert value["maximum_high"] == Fraction("102.5")


@pytest.mark.parametrize("mutate", [
    lambda parent: parent.update(specification_sha256="a" * 64),
    lambda parent: parent.update(comparison_performed=False),
    lambda parent: parent["parameters"].update(entry_delay_minutes=1),
    lambda parent: parent["variants"]["D+1"]["days"][0].update(outcome_complete=False),
    lambda parent: parent["variants"]["D+1"]["days"][0]["opening_volatility_feature"].update(status="rejected"),
    lambda parent: parent["variants"]["D+1"]["by_arm"]["basic"].update(sizing_passed=False),
    lambda parent: parent["variants"]["D+2"]["days"][0]["arms"]["basic"]["label"].update(exit_open="102.5"),
])
def test_parent_config_mask_feature_gate_and_second_policy_anchor_mutations_refuse(mutate):
    parent, chunks = fixture()
    mutate(parent)
    with pytest.raises(diagnostics.BreakoutPathRefusal):
        diagnostics.build_diagnostics(parent, chunks)


def test_raw_chunk_framing_order_population_and_vintage_refuse():
    parent, chunks = fixture()
    ordered = [chunks[key] for key in sorted(chunks)]
    for bad in (ordered[:-1], ordered + [ordered[-1]], [ordered[1], ordered[0], *ordered[2:]]):
        with pytest.raises(diagnostics.BreakoutPathRefusal):
            diagnostics.build_diagnostics(parent, iter(bad))
    chunks[DAYS[0], SYMBOLS[0]]["rows"][31]["high"] = "103"
    with pytest.raises(diagnostics.BreakoutPathRefusal, match="vintage"):
        diagnostics.build_diagnostics(parent, chunks)


def test_no_detector_sizing_or_upstream_performance_recalculation(monkeypatch):
    parent, chunks = fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("upstream detector/sizing/performance recalculation")
    for module, names in ((diagnostics.cc, ("aggregate_session", "dispersion")),
            (diagnostics.di, ("build_comparison",)), (diagnostics.di.de, ("_summary",)),
            (diagnostics.ct, ("build_calibration", "_session_findings"))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    calls = []
    result = diagnostics.build_diagnostics(parent, iter(chunks[key] for key in sorted(chunks)), checkpoint=lambda: calls.append(1))
    assert len(calls) > len(chunks) and result["variants"]["D+1"]["summary"]["overall"]["total_cases"] == 12


def test_exact_crossing_is_independent_of_low_decimal_context():
    parent, chunks = fixture()
    with localcontext() as context:
        context.prec = 6
        result = diagnostics.build_diagnostics(parent, chunks)
    assert _case(result, 2)["mfe_exceeds_cost"] is False
    assert _case(result, 3)["cap_only_exceeded"] is True
