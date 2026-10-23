"""Invented source reports only; no actual market inputs or remote access."""
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
import json
import math

import pytest

from research import intraday_breakout_retest_comparison as comparison
from test_intraday_opening_retest_calibration import inputs, pattern, raw_rows, SYMBOLS


DAYS = ("2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24")


def wire(value):
    return json.loads(comparison._canonical(value))


def build_synthetic_sources(days=DAYS):
    """Same invented raw source for both saved reports; sparse/zero dates stay."""
    observations = {}
    for day in days[:-1]:
        for index in range(4):
            rows = pattern(day, SYMBOLS[index], contact_start=25 if index != 1 else None, count=235)
            # A later second breakout makes CT's unused original30m calibration
            # valid too, without changing the original15m or retest decision.
            for row in rows[40:]:
                row.update(open="103", high="103", low="101", close="103")
            rows[55].update(open="102")
            rows[65].update(open="102")
            rows[75].update(open="103")
            if index == 2:
                rows.pop(60)  # basic known, retest unknown label.
            if index == 3:
                rows = rows[:60]
                rows.pop(40)  # retest known but unknown label; basic unknown label.
            observations[day, SYMBOLS[index]] = rows
        observations[day, SYMBOLS[4]] = raw_rows(day, SYMBOLS[4], 235)
    cb, chunks = inputs(days=days, observations=observations)
    basic = wire(comparison.ct.build_calibration(cb, chunks))
    retest = wire(comparison.dc.build_calibration(cb, chunks))
    return basic, retest


@lru_cache(maxsize=1)
def _base():
    return build_synthetic_sources()


def sources():
    return deepcopy(_base())


def test_public_spec_has_only_symbolic_sources_and_fixed_resources():
    spec = json.loads(comparison.SPECIFICATION_PATH.read_text())
    comparison.validate_spec(spec)
    assert "/home/" not in json.dumps(spec)
    assert spec["resources"]["maximum_market_gets"] == 0
    assert spec["resources"]["maximum_source_bytes"] == 64 * 1024 * 1024
    spec["parameters"]["assumed_cost"]["numerator"] = "34"
    with pytest.raises(comparison.BreakoutRetestComparisonRefusal, match="specification_scope"):
        comparison.validate_spec(spec)


def test_common_masks_keep_original_unknowns_and_use_distinct_own_signals():
    basic, retest = sources()
    original = (deepcopy(basic), deepcopy(retest))
    result = comparison.build_comparison(basic, retest)
    assert (basic, retest) == original
    assert result["comparison_performed"] is True
    assert result["new_performance_families"] == 1
    for policy in comparison.POLICIES:
        variant = result["variants"][policy]
        cohorts = variant["cohorts"]
        assert cohorts["full_source"]["target_count"] == 80
        assert cohorts["common_signal_observable"]["target_count"] == 15
        assert cohorts["common_outcome_complete"]["target_count"] == 9
        arms = variant["comparison"]["by_arm"]
        assert arms["basic"]["event_count"] == 6
        assert arms["retest"]["event_count"] == 3
        assert arms["basic"]["gross_mean"] == arms["retest"]["gross_mean"] == 0
        assert arms["basic"]["net_mean"] == arms["retest"]["net_mean"] == Fraction(-33, 10000)
        assert variant["source_by_arm"]["basic"] == basic["variants"][policy]["by_arm"]["15"]
        assert variant["source_by_arm"]["retest"] == retest["variants"][policy]["by_arm"]["15_retest"]
        for row in variant["days"]:
            if row["symbol"] == SYMBOLS[2] and row["session"] != DAYS[-1]:
                assert row["signal_observable"] is True and row["outcome_complete"] is False
                assert row["exclusion_reasons"] == ["retest:unknown_label"]
                assert row["arms"]["basic"]["label"]["status"] == "known"
                assert row["arms"]["retest"]["label"]["price_change"] is None
            if row["symbol"] == SYMBOLS[-1]:
                assert row["exclusion_reasons"] == ["basic:unknown_signal", "retest:unknown_signal"]
                assert row["outcome_complete"] is False


def test_full_reference_calendar_and_zero_weeks_keep_same_denominators():
    result = comparison.build_comparison(*sources())
    assert result["reference_sessions"] == list(DAYS)
    for variant in result["variants"].values():
        for cohort in variant["cohorts"].values():
            assert len(cohort["by_week"]) == 4
            for arm in comparison.ARMS:
                frequency = cohort["by_arm"][arm]["frequency"]
                assert frequency["reference_sessions"] == 4
                assert frequency["reference_weeks"] == 4
                assert frequency["signals_per_reference_week"] == Fraction(cohort["by_arm"][arm]["signal_counts"]["signal"], 4)
        for summary in variant["comparison"]["by_arm"].values():
            empty = summary["by_week"][comparison.ct._week(DAYS[-1])]
            assert empty["event_count"] == 0 and empty["gross_mean"] is None
            assert empty["gross_sum"] == empty["net_sum"] == 0
            assert sum(week["event_count"] for week in summary["by_week"].values()) == summary["event_count"]
            assert sum(week["gross_sum"] for week in summary["by_week"].values()) == summary["gross_sum"]


def test_compact_provenance_preserves_both_original_source_records():
    basic, retest = sources()
    result = comparison.build_comparison(basic, retest)
    for arm, report in (("basic", basic), ("retest", retest)):
        assert result["source_provenance"][arm] == {key: value for key, value in report.items() if key not in comparison.PROVENANCE_EXCLUDE}
        assert "variants" not in result["source_provenance"][arm]
    assert result["source_target_matrix"] == basic["source_target_matrix"] == retest["source_target_matrix"]
    for flag in ("actual_portfolio_pnl", "promotion_allowed", "full_family_certified", "performance_certified",
            "underpower_exception_applied", "actual_fills_certified", "incremental_edge_claimed", "matched_causal_effect_claimed"):
        assert result[flag] is False


def test_only_count_and_minutes_timing_diagnostics_and_joint_contrast():
    result = comparison.build_comparison(*sources())
    for variant in result["variants"].values():
        diagnostic = variant["timing_diagnostics"]
        assert diagnostic["both_signal_target_count"] == 9
        assert diagnostic["retest_minus_basic_decision_minutes_counts"] == {"10": 9}
        assert diagnostic["minimum_minutes"] == diagnostic["maximum_minutes"] == 10
        assert diagnostic["performance_calculated"] is False
        contrast = variant["comparison"]["retest_minus_basic"]
        assert contrast["gross_mean_difference"] == contrast["net_mean_difference"] == 0
        assert contrast["uncertainty"]["known_labels_by_arm"] == {"basic": 6, "retest": 3}
        assert contrast["uncertainty"]["bootstrap_draws"] == 2000
        assert contrast["uncertainty"]["reference_sessions"] == 4
        assert contrast["fixed_cost_cancels_in_mean_difference"] is True


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(data_vintage="b" * 64),
    lambda row: row["raw_chunk_metadata"].update(retrieved_at="2026-10-20T02:00:00+00:00"),
    lambda row: row["source_offset_counts_ms"].update({"1": 1}),
    lambda row: row["arms"]["15_retest"]["label"].update(entry_at="2026-08-03T09:40:00+09:00"),
    lambda row: row["arms"]["15_retest"]["label"].update(price_change={"numerator": "1", "denominator": "100"}),
    lambda row: row["arms"]["15_retest"]["event"].update(decision_at="2026-08-03T09:22:00+09:00"),
    lambda row: row["arms"]["15_retest"]["event"]["contact_bar"].update(low="101"),
])
def test_source_identity_metadata_chronology_and_original_label_tampering_refuse(mutate):
    basic, retest = sources()
    mutate(retest["variants"]["D+1"]["days"][0])
    with pytest.raises(comparison.BreakoutRetestComparisonRefusal):
        comparison.build_comparison(basic, retest)


@pytest.mark.parametrize("mde", [None, math.inf, 33.00001])
def test_all_selected_own_gates_precede_every_new_mean_and_failure_keeps_report(monkeypatch, mde):
    basic, retest = sources()
    original_source = comparison._source
    original_dispersion = comparison.cc.dispersion
    count = 0
    def validate(*args):
        answer = original_source(*args)
        def fail_selected(records, reference):
            nonlocal count
            count += 1
            own = original_dispersion(records, reference)
            if count == 4:
                own["mde_bps"] = mde
            return own
        monkeypatch.setattr(comparison.cc, "dispersion", fail_selected)
        return answer
    def forbidden_mean(*_):
        pytest.fail("mean calculated before all four own selected gates passed")
    monkeypatch.setattr(comparison, "_source", validate)
    monkeypatch.setattr(comparison.de, "_summary", forbidden_mean)
    report = comparison.build_comparison(basic, retest)
    assert count == 4 and report["comparison_performed"] is False
    assert report["new_performance_families"] == 0
    assert report["requires_operator_choice_before_comparison"] is True
    assert report["comparison_not_run_reasons"]
    assert all(variant["comparison"] is None for variant in report["variants"].values())
    assert len(report["variants"]["D+1"]["days"]) == 80
