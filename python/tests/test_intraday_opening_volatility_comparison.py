"""Invented saved reports only; no actual input, API, DB or remote execution."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
import json
import math

import pytest

from research import intraday_opening_volatility_comparison as comparison
from test_intraday_breakout_retest_comparison import DAYS, SYMBOLS, wire
from test_intraday_opening_retest_calibration import inputs, pattern, raw_rows


def build_synthetic_parent(days=DAYS, *, variable=False, all_rejected=False, endpoint_changes=None):
    """Three opening spans distinguish equality, below-threshold and selection."""
    observations = {}
    for day_index, day in enumerate(days[:-1]):
        for index in range(4):
            rows = pattern(day, SYMBOLS[index], contact_start=25 if index != 1 else None, count=235)
            for row in rows[40:]:
                row.update(open="103", high="103", low="101", close="103")
            rows[55].update(open="102")
            rows[65].update(open="102")
            rows[75].update(open="103")
            if endpoint_changes is not None:
                rows[55].update(open=str(Decimal(102) * (1 + Decimal(endpoint_changes[index]))))
            if variable:
                rows[55].update(open=str(Decimal(102) + Decimal(day_index + index + 1) / 1000))
                rows[65].update(open=str(Decimal(102) - Decimal(day_index + 1) / 2000))
            span = "0" if all_rejected else ("0.33" if index == 0 else "0.3299" if index == 1 else "0.5")
            rows[0].update(high=str(Decimal(100) + Decimal(span)))
            if index == 2:
                rows.pop(60)
            if index == 3:
                rows = rows[:60]
                rows.pop(40)
            observations[day, SYMBOLS[index]] = rows
        observations[day, SYMBOLS[4]] = raw_rows(day, SYMBOLS[4], 235)
        observations[day, SYMBOLS[5]] = pattern(day, SYMBOLS[5], contact_start=None, count=60)
    cb, chunks = inputs(days=days, observations=observations)
    basic = wire(comparison.ct.build_calibration(cb, chunks))
    retest = wire(comparison.dh.dc.build_calibration(cb, chunks))
    parent = wire(comparison.dh.build_comparison(basic, retest))
    parent["specification_sha256"] = comparison.UPSTREAM_SPECIFICATION_SHA256
    return parent


def build_full_geometry_parent(days):
    """Invented profiling parent: 257 sessions/56 weeks/5140 targets,1169/1167 basic.

    The caller supplies only generated calendar strings. No actual source is
    read. Two D+2-only unknown targets replace original targets by membership,
    before either original saved report is built from invented minute rows.
    """
    observations = {}
    for day_index, day in enumerate(days[:-1]):
        for index in range(5 if day_index < 145 else 4):
            rows = pattern(day, SYMBOLS[index], contact_start=25, count=235)
            for row in rows[40:]:
                row.update(open="103", high="103", low="101", close="103")
            rows[55].update(open=str(Decimal(102) + Decimal(day_index + index + 1) / 10000))
            rows[65].update(open="102")
            rows[75].update(open="103")
            rows[0].update(high=("100.2", "100.33", "100.4")[index % 3])
            observations[day, SYMBOLS[index]] = rows
        observations[day, SYMBOLS[5]] = raw_rows(day, SYMBOLS[5], 235)
    cb, chunks = inputs(days=days, observations=observations)
    for day in days[:2]:
        membership = next(item for item in cb["variants"]["D+2"]["days"] if item["session"] == day)
        membership["targets"][0]["symbol"] = "KRX:000021"
        old = chunks[day, SYMBOLS[0]]
        chunks[day, "KRX:000021"] = {**{key: deepcopy(value) for key, value in old.items() if key != "rows"},
            "symbol": "KRX:000021", "rows": []}
    basic = wire(comparison.ct.build_calibration(cb, chunks))
    retest = wire(comparison.dh.dc.build_calibration(cb, chunks))
    parent = wire(comparison.dh.build_comparison(basic, retest))
    parent["specification_sha256"] = comparison.UPSTREAM_SPECIFICATION_SHA256
    return parent


@lru_cache(maxsize=1)
def _base():
    return build_synthetic_parent()


def source():
    return deepcopy(_base())


def test_public_spec_exact_symbolic_parent_and_fixed_resources():
    specification = json.loads(comparison.SPECIFICATION_PATH.read_text())
    comparison.validate_spec(specification)
    assert "/home/" not in json.dumps(specification)
    assert specification["resources"] == comparison.dh.RESOURCES
    assert specification["resources"]["maximum_market_gets"] == 0
    assert specification["resources"]["maximum_token_posts"] == 0
    specification["parameters"]["opening_volatility_threshold"]["numerator"] = "34"
    with pytest.raises(comparison.OpeningVolatilityComparisonRefusal, match="specification_scope"):
        comparison.validate_spec(specification)


def test_exact_opening_span_equality_passes_signed_close_and_volume_do_not_select():
    report = comparison.build_comparison(source())
    for variant in report["variants"].values():
        rows = variant["days"]
        passing = next(row for row in rows if row["symbol"] == SYMBOLS[0])
        failing = next(row for row in rows if row["symbol"] == SYMBOLS[1])
        feature = passing["opening_volatility_feature"]
        assert feature["opening_volatility"] == Fraction(33, 10000)
        assert feature["status"] == "selected"
        assert passing["arms"]["basic"]["event"]["context"][-1]["close"] == "100"
        assert failing["opening_volatility_feature"]["opening_volatility"] == Fraction(3299, 1000000)
        assert failing["opening_volatility_feature"]["status"] == "rejected"
        assert feature["available_at"] == passing["arms"]["basic"]["event"]["context"][-1]["available_at"]
        assert feature["available_at"] < passing["arms"]["basic"]["event"]["decision_at"]
        assert variant["by_arm"]["basic"]["own_label_dispersion"]["events"] == 6
        assert variant["by_arm"]["volatility_selected"]["own_label_dispersion"]["events"] == 3
    assert report["comparison_performed"] is True
    assert report["new_performance_families"] == 1


def test_full_original_targets_masks_unknowns_labels_and_metadata_are_exactly_preserved():
    parent = source()
    untouched = deepcopy(parent)
    report = comparison.build_comparison(parent)
    assert parent == untouched
    assert report["source_provenance"] == {key: value for key, value in parent.items() if key not in comparison.PROVENANCE_EXCLUDE}
    assert "variants" not in report["source_provenance"]
    for policy, variant in report["variants"].items():
        original = parent["variants"][policy]
        assert len(variant["days"]) == len(original["days"]) == 80
        assert variant["source_variant_provenance"] == {key: value for key, value in original.items() if key != "days"}
        for before, after in zip(original["days"], variant["days"], strict=True):
            assert {key: value for key, value in after.items() if key != "opening_volatility_feature"} == before
            feature = after["opening_volatility_feature"]
            if before["arms"]["basic"]["status"] == "unknown":
                assert feature["status"] == "unknown" and feature["opening_volatility"] is None
            elif before["arms"]["basic"]["status"] == "known_no_signal":
                assert feature["status"] == "not_applicable" and feature["opening_volatility"] is None
            if before["symbol"] == SYMBOLS[2] and before["session"] != DAYS[-1]:
                assert feature["status"] == "selected" and after["outcome_complete"] is False
                assert after["exclusion_reasons"] == ["retest:unknown_label"]
        for name in ("full_source", "common_signal_observable", "common_outcome_complete"):
            assert variant["cohorts"][name] == original["cohorts"][name]
        assert variant["cohorts"]["volatility_selected"]["target_count"] == 3
    for flag in comparison._CLAIMS:
        assert report[flag] == comparison._CLAIMS[flag]
    assert report["underpower_exception_applied"] is False


def test_all_reference_dates_weeks_and_operating_frequency_zero_weeks_retained():
    result = comparison.build_comparison(source())
    assert result["reference_sessions"] == list(DAYS)
    for variant in result["variants"].values():
        for arm, expected in (("basic", 6), ("volatility_selected", 3)):
            frequency = variant["by_arm"][arm]["frequency"]
            assert frequency["reference_sessions"] == 4 and frequency["reference_weeks"] == 4
            assert frequency["signals_per_reference_week"] == Fraction(expected, 4)
            assert frequency["weeks_with_at_least15_signals"] == 0
            assert frequency["zero_signal_weeks"] == 1
            assert frequency["by_week"][comparison.ct._week(DAYS[-1])] == 0
            summary = variant["comparison"]["by_arm"][arm]
            assert len(summary["by_week"]) == 4
            empty = summary["by_week"][comparison.ct._week(DAYS[-1])]
            assert empty["event_count"] == 0 and empty["gross_mean"] is None
            assert empty["gross_sum"] == empty["net_sum"] == 0


def test_nonzero_exact_own_endpoint_math_and_joint_selected_minus_basic():
    report = comparison.build_comparison(build_synthetic_parent(variable=True))
    expected_selected = [Fraction(index, 102000) for index in (1, 2, 3)]
    expected_basic = [Fraction(index, 102000) for index in (1, 2, 2, 3, 3, 4)]
    for variant in report["variants"].values():
        for arm, values in (("basic", expected_basic), ("volatility_selected", expected_selected)):
            actual = variant["comparison"]["by_arm"][arm]
            assert actual["event_count"] == len(values)
            assert actual["gross_sum"] == sum(values)
            assert actual["net_sum"] == sum(values) - len(values) * comparison.COST
            assert actual["gross_mean"] == sum(values) / len(values)
            assert actual["net_mean"] == actual["gross_mean"] - comparison.COST
            assert actual["gross_median"] == (Fraction(5, 204000) if arm == "basic" else Fraction(2, 102000))
            assert actual["gross_wins"] == len(values)
            assert actual["cost_exceeding"] == actual["net_wins"] == 0
        contrast = variant["comparison"]["volatility_selected_minus_basic"]
        expected = sum(expected_selected) / 3 - sum(expected_basic) / 6
        assert contrast["gross_mean_difference"] == contrast["net_mean_difference"] == expected
        uncertainty = contrast["uncertainty"]
        assert uncertainty["known_labels_by_arm"] == {"basic": 6, "volatility_selected": 3}
        assert uncertainty["design_se"] == max(uncertainty[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
        assert uncertainty["bootstrap_draws"] == 2000 and uncertainty["bootstrap_seed"] == 0


def test_endpoint_cost_exceedance_is_strict_while_predicate_equality_selects():
    report = comparison.build_comparison(build_synthetic_parent(endpoint_changes=("0.0034", "0.0033", "0", "0")))
    for variant in report["variants"].values():
        basic = variant["comparison"]["by_arm"]["basic"]
        selected = variant["comparison"]["by_arm"]["volatility_selected"]
        assert basic["gross_wins"] == 6
        assert basic["cost_exceeding"] == basic["net_wins"] == 3
        assert basic["cost_exceedance_fraction"] == Fraction(1, 2)
        assert selected["cost_exceeding"] == selected["net_wins"] == 3
        assert selected["cost_exceedance_fraction"] == 1
        assert selected["gross_mean"] == Fraction(34, 10000)
        assert selected["net_mean"] == Fraction(1, 10000)


@pytest.mark.parametrize("mde", [None, math.inf, 33.00001])
def test_all_four_fresh_gates_before_any_new_mean_failure_completes_calibration(monkeypatch, mde):
    parent = source()
    original_dispersion = comparison.cc.dispersion
    count = 0
    def altered(records, reference):
        nonlocal count
        count += 1
        own = original_dispersion(records, reference)
        if count == 4:
            own["mde_bps"] = mde
        return own
    def forbidden_mean(*_):
        pytest.fail("new mean computed before all four fresh own gates pass")
    monkeypatch.setattr(comparison.cc, "dispersion", altered)
    monkeypatch.setattr(comparison.de, "_summary", forbidden_mean)
    result = comparison.build_comparison(parent)
    assert count == 4
    assert result["comparison_performed"] is False and result["new_performance_families"] == 0
    assert result["requires_operator_choice_before_comparison"] is True
    assert result["comparison_not_run_reasons"]
    assert all(variant["comparison"] is None for variant in result["variants"].values())
    assert len(result["variants"]["D+1"]["days"]) == 80


def test_real_empty_selected_subset_is_undefined_not_inherited_exception():
    parent = build_synthetic_parent(all_rejected=True)
    result = comparison.build_comparison(parent)
    assert result["comparison_performed"] is False
    for variant in result["variants"].values():
        assert variant["by_arm"]["volatility_selected"]["own_label_dispersion"]["events"] == 0
        assert variant["by_arm"]["volatility_selected"]["comparison_choice_reasons"] == ["undefined_own_label_sizing"]
    assert result["new_performance_families"] == 0


@pytest.mark.parametrize("mutate", [
    lambda parent: parent.update(specification_sha256="a" * 64),
    lambda parent: parent.update(comparison_performed=False),
    lambda parent: parent.update(underpower_exception_applied=True),
    lambda parent: parent["variants"]["D+1"]["days"][0].update(outcome_complete=False),
    lambda parent: parent["variants"]["D+1"]["days"][0].update(exclusion_reasons=["invented"]),
    lambda parent: parent["variants"]["D+1"]["cohorts"]["common_outcome_complete"].update(target_count=10),
    lambda parent: parent["source_target_matrix"]["D+1"]["days"][0]["targets"][0].update(rank=2),
    lambda parent: parent["variants"]["D+1"]["days"][0]["arms"]["basic"]["label"].update(exit_open="103"),
    lambda parent: parent["variants"]["D+1"]["days"][0]["arms"]["retest"]["label"].update(entry_at="2026-08-03T09:40:00+09:00"),
])
def test_parent_pin_claim_population_mask_clock_and_exact_price_tampering_refuse(mutate):
    parent = source()
    mutate(parent)
    with pytest.raises(comparison.OpeningVolatilityComparisonRefusal):
        comparison.build_comparison(parent)


@pytest.mark.parametrize("field,value", [("high", "-1"), ("low", "101"), ("open", "0"),
    ("available_at", "2026-08-03T09:30:00+09:00"), ("start_at", "2026-08-03T09:01:00+09:00")])
def test_malformed_rejected_known_context_refuses_instead_of_silent_exclusion(field, value):
    parent = source()
    row = next(item for item in parent["variants"]["D+1"]["days"] if item["symbol"] == SYMBOLS[1])
    row["arms"]["basic"]["event"]["context"][0][field] = value
    with pytest.raises(comparison.OpeningVolatilityComparisonRefusal):
        comparison.build_comparison(parent)
