"""Invented CT inputs only; no saved market report or private source is read."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
import json
import math
import random
from statistics import NormalDist, stdev

import pytest

from research import intraday_opening_range_comparison as comparison
from test_intraday_opening_range_calibration import SYMBOLS, inputs, pattern, raw_rows


ct = comparison.ct
DEFAULT_DAYS = ("2025-09-04", "2025-09-05", "2025-09-08", "2025-09-09",
    "2025-09-10", "2025-09-11", "2025-09-12", "2025-09-15")


def build_synthetic_upstream(days=DEFAULT_DAYS):
    """Genuine CT output with dense small-variance invented labels and unknowns.

    Accept an explicit reference-date sequence (the full 257-date profile may
    supply one) or a positive number of weekdays. Eight shared names per date
    provide more known labels than actual CT's counts at full geometry; actual
    byte identities and observed counts are never inferred from this fixture.
    """
    if isinstance(days, int):
        generated, current = [], date(2025, 9, 4)
        while len(generated) < days:
            if current.weekday() < 5:
                generated.append(current.isoformat())
            current += timedelta(days=1)
        days = generated
    days = tuple(days)
    observations = {}
    for index, day in enumerate(days):
        # A whole zero-opportunity date exercises the reference denominator.
        if len(days) > 2 and index == len(days) - 1:
            continue
        for number, symbol in enumerate(SYMBOLS[:9]):
            rows = pattern(day, symbol, "earlier")[:71]
            delta = Decimal((index + number) % 5 - 2) / Decimal(1000)
            rows[55].update(open=str(Decimal(100) + delta), high="101", low="99")
            rows[70].update(open=str(Decimal(100) - delta / 2), high="101", low="99")
            if number == 8:
                rows.pop(70)  # Fifteen known, thirty unknown; never zero fill.
            observations[day, symbol] = rows
        early = pattern(day, SYMBOLS[9], "early_only")[:235]
        early[55].update(open="100.0007", high="101")
        observations[day, SYMBOLS[9]] = early
        observations[day, SYMBOLS[10]] = raw_rows(day, SYMBOLS[10], 235)
        # Remaining original targets have genuinely empty invented archives.
    cb, chunks = inputs(days, observations)
    return json.loads(ct._canonical(ct.build_calibration(cb, chunks)))


@lru_cache(maxsize=1)
def _base():
    return build_synthetic_upstream()


def source():
    return deepcopy(_base())


def specification():
    return {"id": comparison.SPECIFICATION_ID, "mode": comparison.MODE,
        "window": deepcopy(comparison.WINDOW), "parameters": deepcopy(comparison.PARAMETERS),
        "resources": dict(comparison.RESOURCES), **comparison.REGISTERED_PATHS,
        "upstream_sha256": comparison.UPSTREAM_SHA256,
        "upstream_specification_sha256": comparison.UPSTREAM_SPECIFICATION_SHA256}


def labels(report, arm, policy="D+1"):
    return [(row["session"], comparison._ratio(row["arms"][arm]["label"]["price_change"]))
        for row in report["variants"][policy]["days"] if row["arms"][arm]["status"] == "signal"
        and row["arms"][arm]["label"]["status"] == "known"]


def signal_row(report, arm="15"):
    return next(row for row in report["variants"]["D+1"]["days"] if row["arms"][arm]["status"] == "signal")


def _refresh_own(report):
    """Recompute the unchanged CT prerequisite, for deliberate failure tests."""
    for variant in report["variants"].values():
        for arm, summary in variant["by_arm"].items():
            records = [{"session": row["session"], "label": None if row["arms"][arm]["label"]["status"] == "unknown"
                else comparison._ratio(row["arms"][arm]["label"]["price_change"])}
                for row in variant["days"] if row["arms"][arm]["status"] == "signal"]
            own = ct.cc.dispersion(records, report["reference_sessions"])
            summary["own_label_dispersion"] = own
            reasons = ["unknown_own_labels"] if own["unknown_labels"] else []
            if own["mde_bps"] is None:
                reasons.append("undefined_own_label_sizing")
            elif own["mde_bps"] > 33:
                reasons.append("own_label_mde_exceeds_assumed_cost_floor")
            summary["comparison_choice_reasons"] = reasons
            summary["requires_operator_choice_before_comparison"] = bool(reasons)


def test_fixed_specification_and_explicit_source_bound_authorization():
    comparison.validate_spec(specification())
    assert comparison.UPSTREAM_SHA256 == "b25690b7cc87278ebe1baf9f20d6f0185c7d759c93b90e7488b89b53383aa875"
    assert comparison.UPSTREAM_SPECIFICATION_SHA256 == "1578dbf61fdbbdb620218f1292da412ec99e8eff3ce1d8c67870bcce304b56bf"
    assert comparison.AUTHORIZATION["unknown_label_conditional_comparison_authorized"] is True
    assert comparison.AUTHORIZATION["underpower_exception_applied"] is False


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update(extra=True),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec.update(upstream_specification_sha256="a" * 64),
    lambda spec: spec.update(output_directory="/tmp/other"),
    lambda spec: spec["parameters"].update(bootstrap_seed=1),
    lambda spec: spec["parameters"].update(block_sessions=4),
    lambda spec: spec["parameters"].update(bootstrap_draws=1999),
    lambda spec: spec["parameters"].update(assumed_cost={"numerator": "34", "denominator": "10000"}),
    lambda spec: spec["resources"].update(cpu_seconds=600),
])
def test_specification_rejects_every_scope_change(mutate):
    spec = specification()
    mutate(spec)
    with pytest.raises(comparison.OpeningRangeComparisonRefusal, match="specification_scope"):
        comparison.validate_spec(spec)


def test_exact_observed_denominators_means_cost_and_unchanged_upstream():
    upstream = source()
    original = deepcopy(upstream)
    result = comparison.build_comparison(upstream)
    assert upstream == original
    assert result["upstream_provenance"] == {key: value for key, value in original.items() if key != "variants"}
    for policy, variant in result["variants"].items():
        assert {key: value for key, value in variant.items() if key != "comparison"} == original["variants"][policy]
        arms = variant["comparison"]["by_arm"]
        assert arms["15"]["known_labels"] > arms["30"]["known_labels"]
        for arm in comparison.ARMS:
            known = [value for _, value in labels(original, arm, policy)]
            actual = arms[arm]
            assert actual["known_labels"] == len(known)
            assert actual["gross_sum"] == sum(known, Fraction(0))
            assert actual["gross_mean"] == sum(known, Fraction(0)) / len(known)
            assert actual["net_mean"] == actual["gross_mean"] - Fraction(33, 10000)
            assert actual["assumed_cost"] == Fraction(33, 10000)
            assert actual["gross_positive_count"] == sum(value > 0 for value in known)
            assert actual["net_positive_count"] == sum(value > Fraction(33, 10000) for value in known)
            assert actual["gross_positive_fraction"] == Fraction(actual["gross_positive_count"], len(known))
            assert actual["net_positive_fraction"] == Fraction(actual["net_positive_count"], len(known))
            assert actual["own_label_dispersion"] == original["variants"][policy]["by_arm"][arm]["own_label_dispersion"]
            interval = actual["descriptive_normal95_interval"]
            assert (interval["lower"] + interval["upper"]) / 2 == pytest.approx(float(actual["gross_mean"]))
        contrast = variant["comparison"]["fifteen_minus_thirty"]
        assert contrast["gross_mean_difference"] == arms["15"]["gross_mean"] - arms["30"]["gross_mean"]
        assert contrast["net_mean_difference"] == contrast["gross_mean_difference"]
        assert contrast["fixed_cost_cancels_in_mean_difference"] is True


def test_missing_targets_and_unknown_outcomes_keep_original_counts_and_choice():
    upstream = source()
    result = comparison.build_comparison(upstream)
    assert result["requires_operator_choice_before_comparison"] is True
    assert result["requires_operator_choice"] is False
    assert result["comparison_performed"] is True and result["calibration_only"] is False
    assert result["exploratory_only"] is True and result["underpower_exception_applied"] is False
    assert result["operator_authorization"] == comparison.AUTHORIZATION
    for name in ("family_adopted", "full_family_certified", "performance_certified", "promotion_allowed",
            "actual_portfolio_pnl", "historical_costs_certified", "actual_fills_certified"):
        assert result[name] is False
    variant = result["variants"]["D+1"]
    summary = variant["by_arm"]["30"]
    assert summary["signal_counts"]["unknown"] > 0 and summary["label_counts"]["unknown"] > 0
    assert summary["comparison_choice_reasons"] == ["unknown_own_labels"]
    assert summary["requires_operator_choice_before_comparison"] is True
    assert variant["comparison"]["by_arm"]["30"]["known_labels"] == summary["label_counts"]["known"]
    assert len(variant["days"]) == 20 * len(DEFAULT_DAYS)
    assert result["reference_sessions"][-1] == DEFAULT_DAYS[-1]


def test_joint_daily_influence_and_expanded_circular_bootstrap_oracle():
    upstream = source()
    result = comparison.build_comparison(upstream)
    actual = result["variants"]["D+1"]["comparison"]["fifteen_minus_thirty"]
    uncertainty = actual["uncertainty"]
    reference = upstream["reference_sessions"]
    by_day = {day: {arm: [] for arm in comparison.ARMS} for day in reference}
    for arm in comparison.ARMS:
        for day, value in labels(upstream, arm):
            by_day[day][arm].append(value)
    means = {arm: sum((value for _, value in labels(upstream, arm)), Fraction(0)) / len(labels(upstream, arm))
        for arm in comparison.ARMS}
    influences = []
    for day in reference:
        influences.append(sum((value - means["15"] for value in by_day[day]["15"]), Fraction(0)) / len(labels(upstream, "15"))
            - sum((value - means["30"] for value in by_day[day]["30"]), Fraction(0)) / len(labels(upstream, "30")))
    cluster = math.sqrt(len(reference) / (len(reference) - 1) * sum(float(value) ** 2 for value in influences))
    assert uncertainty["session_cluster_se"] == pytest.approx(cluster, rel=1e-13)
    naive = math.sqrt(sum(stdev(float(value) for _, value in labels(upstream, arm)) ** 2 / len(labels(upstream, arm))
        for arm in comparison.ARMS))
    assert uncertainty["naive_se"] == pytest.approx(naive)
    randomizer, draws, skipped = random.Random(0), [], 0
    for _ in range(2000):
        positions = []
        remaining = len(reference)
        while remaining:
            start = randomizer.randrange(len(reference))
            length = min(5, remaining)
            positions.extend((start + offset) % len(reference) for offset in range(length))
            remaining -= length
        sampled = {arm: [value for position in positions for value in by_day[reference[position]][arm]]
            for arm in comparison.ARMS}
        if not all(sampled.values()):
            skipped += 1
            continue
        draws.append(float(sum(sampled["15"], Fraction(0)) / len(sampled["15"])
            - sum(sampled["30"], Fraction(0)) / len(sampled["30"])))
    assert uncertainty["bootstrap_se"] == pytest.approx(stdev(draws), rel=1e-12)
    assert uncertainty["bootstrap_valid_draws"] == len(draws)
    assert uncertainty["bootstrap_skipped_zero_denominator_draws"] == skipped
    assert uncertainty["design_se"] == max(uncertainty[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert uncertainty["corrected_to_naive_ratio"] == uncertainty["design_se"] / uncertainty["naive_se"]
    assert uncertainty["reference_sessions"] == len(reference)
    width = NormalDist().inv_cdf(.975) * uncertainty["design_se"]
    assert actual["descriptive_normal95_interval"] == {
        "lower": float(actual["gross_mean_difference"]) - width,
        "upper": float(actual["gross_mean_difference"]) + width}


def test_joint_bootstrap_retains_dependence_and_zero_draws():
    reference = [f"2025-09-{day:02d}" for day in range(4, 24)]
    records = {arm: [{"session": reference[0], "label": Fraction(1, 10000)},
        {"session": reference[1], "label": Fraction(-1, 10000)}] for arm in comparison.ARMS}
    uncertainty = comparison._uncertainty(records, reference, {arm: Fraction(0) for arm in comparison.ARMS}, lambda: None)
    assert uncertainty["session_cluster_se"] == uncertainty["bootstrap_se"] == 0
    assert uncertainty["naive_se"] > 0  # Unpaired reference is not joint variation.
    assert uncertainty["bootstrap_skipped_zero_denominator_draws"] > 0
    assert uncertainty["design_se"] == uncertainty["naive_se"]


def test_source_specification_execution_manifest_and_exact_serialization_preserved():
    upstream = source()
    upstream.update(specification_sha256=comparison.UPSTREAM_SPECIFICATION_SHA256,
        execution_manifest={"synthetic": True, "cpu_seconds": 1.2}, runtime={"invented": "runtime"})
    result = comparison.build_comparison(upstream)
    assert result["upstream_provenance"]["execution_manifest"] == upstream["execution_manifest"]
    assert result["upstream_provenance"]["specification_sha256"] == comparison.UPSTREAM_SPECIFICATION_SHA256
    encoded = json.loads(comparison._canonical(result))
    mean = result["variants"]["D+1"]["comparison"]["by_arm"]["15"]["gross_mean"]
    assert encoded["variants"]["D+1"]["comparison"]["by_arm"]["15"]["gross_mean"] == {
        "numerator": str(mean.numerator), "denominator": str(mean.denominator)}


@pytest.mark.parametrize("mutate", [
    lambda report: report.update(study_id="other"),
    lambda report: report.update(mode="old_comparison"),
    lambda report: report.update(calibration_only=False),
    lambda report: report.update(full_family_certified=True),
    lambda report: report.update(specification_sha256="a" * 64),
    lambda report: report["parameters"].update(label_minutes=120),
    lambda report: report["assumptions"].update(cost="verified"),
    lambda report: report["reference_sessions"].reverse(),
    lambda report: report["reference_weeks"].pop(),
    lambda report: report["variants"].pop("D+2"),
    lambda report: report["variants"]["D+1"]["days"].pop(),
    lambda report: report["variants"]["D+1"]["days"][0]["membership_target"].update(rank=20),
    lambda report: report["variants"]["D+1"]["days"][0]["membership_source"].update(available_at="2025-09-04T11:00:00+09:00"),
    lambda report: report["source_target_matrix"]["D+1"]["days"][0].update(available_at="2025-09-04T11:00:00+09:00"),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["signal_counts"].update(signal=1),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["label_counts"].update(known=0),
    lambda report: report["variants"]["D+1"]["by_arm"]["30"]["comparison_choice_reasons"].append("old_waiver"),
    lambda report: report["variants"]["D+1"]["by_arm"]["30"].update(requires_operator_choice_before_comparison=False),
    lambda report: report.update(requires_operator_choice_before_comparison=False),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"].update(design_se=0),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["frequency"].update(reference_weeks=1),
    lambda report: next(iter(report["variants"]["D+1"]["by_arm"]["15"]["by_week"].values()))["label_counts"].update(known=0),
    lambda report: next(iter(report["variants"]["D+1"]["by_arm"]["15"]["by_month"].values()))["signal_counts"].update(unknown=0),
    lambda report: report["variants"]["D+1"]["event_timing"].update(shared=0),
    lambda report: report["input_evidence"].update(stream_sha256="a" * 64),
    lambda report: report["input_evidence"].update(queried_rows=-1),
    lambda report: signal_row(report).update(input_rows=-1),
    lambda report: signal_row(report)["raw_chunk_metadata"].update(available_at="2025-09-04T08:00:00+09:00"),
])
def test_malformed_or_changed_source_refuses_before_comparison(mutate):
    report = source()
    mutate(report)
    with pytest.raises(comparison.OpeningRangeComparisonRefusal):
        comparison.build_comparison(report)


@pytest.mark.parametrize("section,key,value", [
    ("event", "decision_at", "2025-09-04T09:21:00+09:00"),
    ("event", "range_minutes", 10),
    ("event", "retrieved_at", "2025-09-04T09:00:00+09:00"),
    ("event", "data_vintage", "a" * 64),
    ("event", "parameters_sha256", "a" * 64),
    ("label", "entry_at", "2025-09-04T09:22:00+09:00"),
    ("label", "exit_at", "2025-09-04T09:54:00+09:00"),
    ("label", "label_available_at", "2025-09-04T09:55:00+09:00"),
    ("label", "actual_fill_certified", True),
    ("label", "unknown_minute_buckets", 1),
    ("label", "entry_open", "0"),
    ("label", "exit_open", "NaN"),
    ("label", "price_change", {"numerator": "1", "denominator": "0"}),
    ("label", "price_change", 0.0),
])
def test_changed_causal_clocks_and_false_known_labels_refuse(section, key, value):
    report = source()
    signal_row(report)["arms"]["15"][section][key] = value
    with pytest.raises(comparison.OpeningRangeComparisonRefusal):
        comparison.build_comparison(report)


def test_unknown_label_cannot_be_zero_filled():
    report = source()
    row = next(row for row in report["variants"]["D+1"]["days"] if row["arms"]["30"]["status"] == "signal"
        and row["arms"]["30"]["label"]["status"] == "unknown")
    row["arms"]["30"]["label"]["price_change"] = {"numerator": "0", "denominator": "1"}
    with pytest.raises(comparison.OpeningRangeComparisonRefusal, match="must_remain_unknown"):
        comparison.build_comparison(report)


def test_cost_exceedance_is_strict_and_keeps_zero_gross_distinct():
    report = source()
    outcomes = (Fraction(0), Fraction(33, 10000), Fraction(331, 100000), Fraction(-1, 100000))
    for variant in report["variants"].values():
        for arm in comparison.ARMS:
            index = 0
            for row in variant["days"]:
                finding = row["arms"][arm]
                if finding["status"] == "signal" and finding["label"]["status"] == "known":
                    value = outcomes[index % len(outcomes)]
                    label = finding["label"]
                    exit_price = comparison._ratio(label["entry_open"]) * (1 + value)
                    label["exit_open"] = str(Decimal(exit_price.numerator) / Decimal(exit_price.denominator))
                    label["price_change"] = value
                    index += 1
    _refresh_own(report)
    result = comparison.build_comparison(report)
    arm = result["variants"]["D+1"]["comparison"]["by_arm"]["30"]
    assert arm["gross_positive_fraction"] == Fraction(1, 2)
    assert arm["net_positive_fraction"] == Fraction(1, 4)


def test_zero_uncertainty_does_not_invent_a_corrected_ratio():
    reference = list(DEFAULT_DAYS)
    records = {arm: [{"session": day, "label": Fraction(0)} for day in reference] for arm in comparison.ARMS}
    uncertainty = comparison._uncertainty(records, reference, {arm: Fraction(0) for arm in comparison.ARMS}, lambda: None)
    assert uncertainty["design_se"] == 0
    assert uncertainty["corrected_to_naive_ratio"] is None


def test_genuine_new_source_own_power_failure_is_not_covered_by_unknown_choice():
    report = source()
    for variant in report["variants"].values():
        for row in variant["days"]:
            for arm in comparison.ARMS:
                finding = row["arms"][arm]
                if finding["status"] == "signal" and finding["label"]["status"] == "known":
                    label = finding["label"]
                    price = "200" if row["session"] == DEFAULT_DAYS[0] else "50"
                    label["exit_open"] = price
                    label["price_change"] = Fraction(price) / comparison._ratio(label["entry_open"]) - 1
    _refresh_own(report)
    assert report["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] > 33
    with pytest.raises(comparison.OpeningRangeComparisonRefusal, match="sizing_failure_not_authorized"):
        comparison.build_comparison(report)


def test_two_reference_dates_genuine_passing_sizing_and_callbacks():
    upstream = build_synthetic_upstream(days=2)
    checkpoints = []
    result = comparison.build_comparison(upstream, checkpoint=lambda: checkpoints.append(True))
    assert len(checkpoints) > 4000
    for variant in result["variants"].values():
        for summary in variant["by_arm"].values():
            assert summary["own_label_dispersion"]["mde_bps"] < 33


def test_checkpoint_failure_propagates_without_partial_return():
    def stop():
        raise RuntimeError("invented_budget_stop")
    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        comparison.build_comparison(source(), checkpoint=stop)


def test_joint_uncertainty_rejects_single_known_date_and_no_valid_draws(monkeypatch):
    reference = list(DEFAULT_DAYS)
    records = {arm: [{"session": reference[0], "label": Fraction(1, 10000)},
        {"session": reference[0], "label": Fraction(-1, 10000)}] for arm in comparison.ARMS}
    with pytest.raises(comparison.OpeningRangeComparisonRefusal, match="insufficient_known_labels_or_dates"):
        comparison._uncertainty(records, reference, {arm: Fraction(0) for arm in comparison.ARMS}, lambda: None)
    reference = [f"2025-09-{day:02d}" for day in range(4, 24)]
    for arm in comparison.ARMS:
        records[arm][1]["session"] = reference[1]
    monkeypatch.setattr(comparison.random.Random, "randrange", lambda self, size: 5)
    with pytest.raises(comparison.OpeningRangeComparisonRefusal, match="insufficient_valid_joint_bootstrap"):
        comparison._uncertainty(records, reference, {arm: Fraction(0) for arm in comparison.ARMS}, lambda: None)
