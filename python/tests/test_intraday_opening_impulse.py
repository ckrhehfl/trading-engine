"""Invented complete CT reports only; no saved report or actual prices are read."""

from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
import json

import pytest

from research import intraday_opening_impulse as calibration
from test_intraday_opening_range_calibration import SYMBOLS, inputs, pattern, raw_rows
from test_intraday_opening_range_comparison import DEFAULT_DAYS, build_synthetic_upstream


ct = calibration.ct


def build_synthetic_source(*, days=DEFAULT_DAYS, reject_unknown_labels=False, sparse_volatile_selection=False):
    """Build genuine CT context/labels from invented raw rows with mixed states."""
    observations = {}
    fifteen = ("100.33", "100.32", "99.67", "101", "100", "100.33", "101", "100.33", "100.33")
    thirty = ("100.32", "100.33", "100.33", "99", "100", "100.33", "101", "100.33", "100.33")
    for index, day in enumerate(days[:-1]):
        for number, symbol in enumerate(SYMBOLS[:9]):
            rows = pattern(day, symbol, "earlier")[:71]
            for minute, prices in ((14, fifteen), (29, thirty)):
                price = prices[number]
                if number == 8 and reject_unknown_labels:
                    price = "100"
                if minute == 14 and sparse_volatile_selection and number != 0:
                    price = "100"
                rows[minute].update(close=price, high=str(max(Decimal(100), Decimal(price))),
                    low=str(min(Decimal(100), Decimal(price))))
            delta = Decimal((index + number) % 5 - 2) / Decimal(1000)
            rows[55].update(open=str(Decimal(100) + delta), high="101", low="99")
            if sparse_volatile_selection and number == 0:
                rows[55].update(open="101" if index % 2 else "99")
            rows[70].update(open=str(Decimal(100) - delta / 2), high="101", low="99")
            if number == 8:
                rows.pop(70)
            observations[day, symbol] = rows
        early = pattern(day, SYMBOLS[9], "early_only")[:235]
        early[14].update(close="100" if sparse_volatile_selection else "100.33", high="100.33")
        early[55].update(open="100.0007", high="101")
        observations[day, SYMBOLS[9]] = early
        observations[day, SYMBOLS[10]] = raw_rows(day, SYMBOLS[10], 235)
    cb, chunks = inputs(days, observations)
    return json.loads(ct._canonical(ct.build_calibration(cb, chunks)))


@lru_cache(maxsize=2)
def _base(reject_unknown_labels=False):
    return build_synthetic_source(reject_unknown_labels=reject_unknown_labels)


def source(reject_unknown_labels=False):
    return deepcopy(_base(reject_unknown_labels))


def specification():
    return {"id": calibration.SPECIFICATION_ID, "mode": calibration.MODE,
        "window": dict(calibration.WINDOW), "parameters": deepcopy(calibration.PARAMETERS),
        "resources": dict(calibration.RESOURCES), **calibration.REGISTERED_PATHS,
        "upstream_sha256": calibration.UPSTREAM_SHA256,
        "upstream_specification_sha256": calibration.UPSTREAM_SPECIFICATION_SHA256}


def test_exact_fixed_registration_and_resource_limits():
    calibration.validate_spec(specification())
    assert calibration.SPECIFICATION_ID == "intraday-opening-impulse-v1"
    assert calibration.MODE == "opening-impulse-own-label-frequency-calibration"
    assert calibration.UPSTREAM_SHA256 == "b25690b7cc87278ebe1baf9f20d6f0185c7d759c93b90e7488b89b53383aa875"
    assert calibration.UPSTREAM_SPECIFICATION_SHA256 == "1578dbf61fdbbdb620218f1292da412ec99e8eff3ce1d8c67870bcce304b56bf"
    assert calibration.PARAMETERS["opening_impulse_threshold"] == {"numerator": "33", "denominator": "10000"}
    assert calibration.RESOURCES == calibration.cu.RESOURCES
    assert calibration.REGISTERED_PATHS["output_directory"].endswith("intraday-cv-opening-impulse-20261023-v1")


def test_repository_specification_matches_every_fixed_constant():
    spec = json.loads(calibration.SPECIFICATION_PATH.read_text(encoding="utf-8"))
    calibration.validate_spec(spec)
    assert spec == specification()


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update(extra=True),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec.update(upstream_specification_sha256="a" * 64),
    lambda spec: spec.update(output_directory="/tmp/unregistered"),
    lambda spec: spec["parameters"].update(opening_impulse_threshold={"numerator": "34", "denominator": "10000"}),
    lambda spec: spec["parameters"].update(bootstrap_seed=1),
    lambda spec: spec["parameters"].update(block_sessions=4),
    lambda spec: spec["parameters"].update(bootstrap_draws=1999),
    lambda spec: spec["resources"].update(cpu_seconds=600),
])
def test_specification_refuses_scope_changes(mutate):
    spec = specification()
    mutate(spec)
    with pytest.raises(calibration.OpeningImpulseRefusal, match="specification_scope"):
        calibration.validate_spec(spec)


def test_signed_impulse_exact_equality_both_arms_and_no_later_retry():
    upstream = source()
    result = calibration.build_calibration(upstream)
    first = result["variants"]["D+1"]["days"][:4]
    assert [(row["arms"]["15"]["status"], row["arms"]["30"]["status"]) for row in first] == [
        ("selected", "rejected"), ("rejected", "selected"),
        ("rejected", "selected"), ("selected", "rejected")]
    assert first[0]["arms"]["15"]["opening_impulse"] == Fraction(33, 10000)
    assert first[2]["arms"]["15"]["opening_impulse"] == Fraction(-33, 10000)
    for policy, variant in result["variants"].items():
        for original, row in zip(upstream["variants"][policy]["days"], variant["days"], strict=True):
            for arm in calibration.ARMS:
                feature, finding = row["arms"][arm], original["arms"][arm]
                assert feature["label"] == finding["label"]
                assert feature["original_signal_status"] == finding["status"]
                if finding["status"] == "signal":
                    context = finding["event"]["context"]
                    assert feature["opening_impulse"] == Fraction(context[-1]["close"]) / Fraction(context[0]["open"]) - 1
                    assert feature["available_at"] == context[-1]["available_at"]
                    assert calibration.cu._instant(feature["available_at"]) <= calibration.cu._instant(finding["event"]["decision_at"])
                    for name in ("observation_date", "retrieved_at", "source", "is_final", "data_vintage", "evidence_level"):
                        assert feature[name] == finding["event"][name]
    # The first fifteen-minute signal is rejected even though a later CT
    # thirty-minute breakout exists; its original entry/exit are untouched.
    rejected = first[1]["arms"]["15"]
    assert rejected["label"]["entry_at"] == DEFAULT_DAYS[0] + "T09:25:00+09:00"


def test_fraction_boundary_does_not_depend_on_decimal_precision():
    upstream = source()
    for variant in upstream["variants"].values():
        bar = variant["days"][0]["arms"]["15"]["event"]["context"][-1]
        bar.update(close="100.329999999999999999999999999999", high="100.33")
    with localcontext() as context:
        context.prec = 3
        result = calibration.build_calibration(upstream)
    feature = result["variants"]["D+1"]["days"][0]["arms"]["15"]
    assert feature["status"] == "rejected"
    assert feature["opening_impulse"] < Fraction(33, 10000)


def test_entire_upstream_is_preserved_without_mutation_or_shared_objects():
    upstream = source()
    upstream.update(specification_sha256=calibration.UPSTREAM_SPECIFICATION_SHA256,
        execution_manifest={"invented": {"cpu_seconds": 1.2}}, runtime={"invented": "runtime"})
    original = deepcopy(upstream)
    result = calibration.build_calibration(upstream)
    assert upstream == original
    assert result["upstream_provenance"] == original
    result["upstream_provenance"]["execution_manifest"]["invented"]["cpu_seconds"] = 9
    result["variants"]["D+1"]["days"][0]["arms"]["15"]["label"]["entry_open"] = "999"
    assert upstream == original


def test_full_targets_zero_dates_week_month_frequency_and_selected_own_sizing():
    upstream = source()
    result = calibration.build_calibration(upstream)
    for policy, variant in result["variants"].items():
        assert len(variant["days"]) == len(DEFAULT_DAYS) * 20
        assert result["reference_sessions"] == list(DEFAULT_DAYS)
        for arm, summary in variant["by_arm"].items():
            features = [row["arms"][arm] for row in variant["days"]]
            selected = [item for item in features if item["status"] == "selected"]
            assert summary["selection_counts"] == {state: sum(item["status"] == state for item in features)
                for state in ("selected", "rejected", "unknown", "not_applicable")}
            assert sum(summary["selection_counts"].values()) == len(DEFAULT_DAYS) * 20
            assert summary["label_counts"] == {state: sum(item["label"]["status"] == state for item in selected)
                for state in ("known", "unknown")}
            records = [{"session": row["session"], "label": None if row["arms"][arm]["label"]["status"] == "unknown"
                else calibration.cu._ratio(row["arms"][arm]["label"]["price_change"])}
                for row in variant["days"] if row["arms"][arm]["status"] == "selected"]
            own = ct.cc.dispersion(records, list(DEFAULT_DAYS))
            assert summary["own_label_dispersion"] == own
            assert own["reference_sessions"] == len(DEFAULT_DAYS)
            assert own["design_se"] == max(own[key] for key in ("naive_se", "session_cluster_se", "bootstrap_se"))
            frequency = summary["frequency"]
            assert frequency["selected_per_reference_week"] == Fraction(len(selected), len(result["reference_weeks"]))
            assert frequency["known_labels_per_reference_week"] == Fraction(own["known_labels"], len(result["reference_weeks"]))
            assert set(summary["by_week"]) == set(result["reference_weeks"])
            assert set(summary["by_month"]) == {"2025-09"}
            for weekly in summary["by_week"].values():
                assert sum(weekly["label_counts"].values()) == weekly["selection_counts"]["selected"]
            assert sum(item["selection_counts"]["selected"] for item in summary["by_week"].values()) == len(selected)
        zero = [row for row in variant["days"] if row["session"] == DEFAULT_DAYS[-1]]
        assert all(row["arms"][arm]["status"] == "unknown" for row in zero for arm in calibration.ARMS)


def test_original_signal_unknowns_remain_separate_from_selected_label_choice():
    upstream = source(reject_unknown_labels=True)
    assert upstream["requires_operator_choice_before_comparison"] is True
    result = calibration.build_calibration(upstream)
    assert result["requires_operator_choice_before_comparison"] is False
    assert result["upstream_provenance"]["requires_operator_choice_before_comparison"] is True
    for variant in result["variants"].values():
        for summary in variant["by_arm"].values():
            assert summary["selection_counts"]["unknown"] > 0
            assert summary["label_counts"]["unknown"] == 0
            assert summary["comparison_choice_reasons"] == []
            assert summary["requires_operator_choice_before_comparison"] is False
        rejected = variant["days"][8]["arms"]["30"]
        assert rejected["status"] == "rejected" and rejected["label"]["status"] == "unknown"


def test_selected_unknown_label_requires_new_choice_without_an_inherited_waiver():
    result = calibration.build_calibration(source())
    assert result["requires_operator_choice_before_comparison"] is True
    assert result["comparison_performed"] is False and result["calibration_only"] is True
    assert result["underpower_exception_applied"] is False
    for variant in result["variants"].values():
        assert variant["by_arm"]["30"]["comparison_choice_reasons"] == ["unknown_own_labels"]
        assert variant["by_arm"]["15"]["comparison_choice_reasons"] == []
        unknown = variant["days"][8]["arms"]["30"]
        assert unknown["status"] == "selected" and unknown["label"]["price_change"] is None


def test_no_selected_events_retains_all_targets_and_undefined_own_choice():
    # Reuse CU's genuine CT fixture: all range open/close impulses are zero.
    result = calibration.build_calibration(build_synthetic_upstream())
    for variant in result["variants"].values():
        for summary in variant["by_arm"].values():
            assert summary["selection_counts"]["selected"] == 0
            assert summary["label_counts"] == {"known": 0, "unknown": 0}
            assert summary["own_label_dispersion"]["mde_bps"] is None
            assert summary["comparison_choice_reasons"] == ["undefined_own_label_sizing"]
            assert summary["frequency"]["selected_per_reference_week"] == 0
    assert result["requires_operator_choice_before_comparison"] is True


def test_new_subset_can_fail_own_sizing_when_original_CT_sizing_passes():
    upstream = build_synthetic_source(reject_unknown_labels=True, sparse_volatile_selection=True)
    assert upstream["variants"]["D+1"]["by_arm"]["15"]["own_label_dispersion"]["mde_bps"] < 33
    result = calibration.build_calibration(upstream)
    for variant in result["variants"].values():
        summary = variant["by_arm"]["15"]
        assert summary["label_counts"]["unknown"] == 0
        assert summary["own_label_dispersion"]["mde_bps"] > 33
        assert summary["comparison_choice_reasons"] == ["own_label_mde_exceeds_assumed_cost_floor"]
        assert summary["requires_operator_choice_before_comparison"] is True
    assert result["requires_operator_choice_before_comparison"] is True
    assert result["underpower_exception_applied"] is False


@pytest.mark.parametrize("mutate", [
    lambda report: report.update(study_id="wrong"),
    lambda report: report.update(full_family_certified=True),
    lambda report: report["reference_weeks"].pop(),
    lambda report: report["variants"]["D+1"]["days"].pop(),
    lambda report: report["variants"]["D+1"]["by_arm"]["15"]["label_counts"].update(known=0),
    lambda report: report["variants"]["D+1"]["days"][1]["arms"]["15"]["event"]["context"].pop(),
    lambda report: report["variants"]["D+1"]["days"][1]["arms"]["15"]["event"]["context"][0].update(open="0"),
    lambda report: report["variants"]["D+1"]["days"][1]["arms"]["15"]["event"]["context"][0].update(close=100.0),
    lambda report: report["variants"]["D+1"]["days"][1]["arms"]["15"]["event"]["context"][0].update(low="101"),
    lambda report: report["variants"]["D+1"]["days"][1]["arms"]["15"]["event"]["context"][0].update(available_at="2025-09-04T11:00:00+09:00"),
    lambda report: report["variants"]["D+1"]["days"][1]["arms"]["15"]["label"].update(entry_at="2025-09-04T09:26:00+09:00"),
    lambda report: report["variants"]["D+1"]["days"][1]["arms"]["15"]["label"].update(price_change=Fraction(0)),
    lambda report: report["variants"]["D+1"]["days"][8]["arms"]["30"]["label"].update(price_change=Fraction(0)),
])
def test_complete_source_validation_includes_rejected_signals(mutate):
    report = source()
    mutate(report)
    with pytest.raises(calibration.OpeningImpulseRefusal, match="malformed_source_calibration"):
        calibration.build_calibration(report)


def test_no_performance_statistics_or_comparison_authorization_are_created():
    result = calibration.build_calibration(source())
    forbidden = {"gross_mean", "net_mean", "gross_sum", "win_rate", "gross_positive_count",
        "net_positive_count", "comparison", "contrasts", "operator_authorization"}
    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check({key: value for key, value in result.items() if key != "upstream_provenance"})
    for name, value in calibration.FLAGS.items():
        assert result[name] is value


def test_checkpoint_failure_propagates_without_returning_a_partial_report():
    calls = []
    calibration.build_calibration(source(), checkpoint=lambda: calls.append(True))
    assert len(calls) > 600
    def stop():
        raise RuntimeError("invented_budget_stop")
    with pytest.raises(RuntimeError, match="invented_budget_stop"):
        calibration.build_calibration(source(), checkpoint=stop)
